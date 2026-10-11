"""Checks how far Juniper works today and writes the report for the HTML dashboard.

Usage:
    python scripts/healthcheck.py              # unit tests + live checks (Ollama/Groq)
    python scripts/healthcheck.py --no-live    # offline: no model calls
    python scripts/healthcheck.py --full       # also runs mypy (slower)

Output: a table in the terminal and docs/html/assets/status.js (read by docs/html/status.html).
Exit code is 1 when any check fails ("skip" never counts as failure).
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import re
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "html" / "assets" / "status.js"
console = Console()

OK, FAIL, SKIP = "ok", "fail", "skip"


@dataclass
class Check:
    group: str
    name: str
    status: str
    detail: str = ""
    ms: int = 0


# --- helpers ----------------------------------------------------------


def _timed(fn: Callable[[], tuple[str, str]]) -> tuple[str, str, int]:
    t0 = time.perf_counter()
    try:
        status, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a check must never crash the report
        status, detail = FAIL, f"{type(exc).__name__}: {exc}"
    return status, detail, int((time.perf_counter() - t0) * 1000)


async def _timed_async(fn: Callable[[], Awaitable[tuple[str, str]]]) -> tuple[str, str, int]:
    t0 = time.perf_counter()
    try:
        status, detail = await fn()
    except Exception as exc:  # noqa: BLE001
        status, detail = FAIL, f"{type(exc).__name__}: {exc}"
    return status, detail, int((time.perf_counter() - t0) * 1000)


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False
    )


def _pytest(path: str) -> tuple[str, str]:
    res = _run(
        [sys.executable, "-m", "pytest", path, "-q", "--no-header", "-p", "no:cacheprovider"]
    )
    summary = (res.stdout.strip().splitlines() or ["(sem saída)"])[-1]
    return (OK if res.returncode == 0 else FAIL), summary


# --- offline checks ---------------------------------------------------


def check_config() -> tuple[str, str]:
    from brain.config import settings

    assert settings.paths is not None
    cfg = settings.paths.config
    if not cfg.exists():
        return FAIL, f"config.yaml não encontrado em {cfg} (rode ./scripts/dev.sh)"
    return OK, f"{cfg} · prioridade: {' → '.join(settings.llm.priority)}"


def check_persona() -> tuple[str, str]:
    p = ROOT / "prompts" / "juniper.md"
    if not p.exists():
        return FAIL, "prompts/juniper.md não existe"
    return OK, f"{len(p.read_text(encoding='utf-8'))} caracteres"


def check_contract() -> tuple[str, str]:
    from brain.llm.base import LLMProvider
    from brain.llm.errors import LLMError, LLMUnavailableError

    try:
        LLMProvider()  # type: ignore[abstract]
    except TypeError:
        pass
    else:
        return FAIL, "LLMProvider deveria ser abstrata"
    assert issubclass(LLMUnavailableError, LLMError)
    return OK, "contrato abstrato e hierarquia de erros ok"


def check_groq_key() -> tuple[str, str]:
    from brain.config import settings

    if settings.groq_api_key is None:
        return SKIP, "JUNIPER_GROQ_API_KEY ausente no .env (Groq não será testada ao vivo)"
    return OK, "chave presente (valor não exibido)"


def check_mypy() -> tuple[str, str]:
    res = _run([sys.executable, "-m", "mypy", "brain/"])
    summary = (res.stdout.strip().splitlines() or ["(sem saída)"])[-1]
    return (OK if res.returncode == 0 else FAIL), summary


# --- live checks ------------------------------------------------------


async def _live_provider(provider_name: str) -> list[Check]:
    from brain.llm.errors import LLMError
    from brain.llm.groq_provider import GroqProvider
    from brain.llm.ollama_provider import OllamaProvider
    from brain.llm.types import Message

    group = f"Ao vivo · {provider_name}"
    provider = OllamaProvider() if provider_name == "ollama" else GroqProvider()
    checks: list[Check] = []
    prompt = [Message("user", "Responda apenas com a palavra: ok")]

    async def health() -> tuple[str, str]:
        return (OK, "respondeu") if await provider.health_check() else (FAIL, "não respondeu")

    async def chat() -> tuple[str, str]:
        r = await provider.chat(prompt, max_tokens=16, temperature=0)
        tokens = r.usage.total_tokens if r.usage else "?"
        return OK, f"{(r.message.content or '').strip()[:60]!r} · {tokens} tokens · {r.model}"

    async def stream() -> tuple[str, str]:
        parts, last = [], None
        async for c in provider.stream(prompt, max_tokens=16, temperature=0):
            parts.append(c.delta)
            last = c
        if last is None or last.finish_reason is None:
            return FAIL, "stream terminou sem finish_reason"
        return OK, f"{len(parts)} chunks · {''.join(parts).strip()[:40]!r}"

    try:
        for name, fn in (("health_check", health), ("chat()", chat), ("stream()", stream)):
            status, detail, ms = await _timed_async(fn)
            checks.append(Check(group, name, status, detail, ms))
            if name == "health_check" and status != OK:
                for rest in ("chat()", "stream()"):
                    checks.append(Check(group, rest, SKIP, "provider indisponível"))
                break
    except LLMError as exc:  # pragma: no cover - defensive
        checks.append(Check(group, "provider", FAIL, str(exc)))
    finally:
        await provider.aclose()
    return checks


def ollama_processor() -> Check | None:
    try:
        res = _run(["ollama", "ps"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = res.stdout.strip().splitlines()
    if res.returncode != 0 or len(lines) < 2:
        return None
    m = re.search(r"(\d+%\s*(?:CPU|GPU)(?:/\d+%\s*(?:CPU|GPU))?)", lines[1])
    return Check("Ao vivo · ollama", "processador", OK, m.group(1) if m else lines[1])


# --- roadmap (detected from the code) ---------------------------------

# (task, label, file, extra predicate on source or None)
ROADMAP: list[tuple[str, str, str]] = [
    ("T-011", "Contrato e tipos LLM", "brain/llm/base.py"),
    ("T-012", "OllamaProvider", "brain/llm/ollama_provider.py"),
    ("T-013", "GroqProvider", "brain/llm/groq_provider.py"),
    ("T-015", "Router com failover", "brain/llm/router.py"),
    ("T-020", "Base de tools", "brain/tools/base.py"),
    ("T-021", "Registry de tools", "brain/tools/registry.py"),
    ("T-022", "Tools de sistema", "brain/tools/system.py"),
    ("T-024", "Tools de web", "brain/tools/web.py"),
    ("T-026", "Bridge de comandos legados", "brain/tools/juniper_cmds.py"),
    ("T-030", "Agent loop", "brain/core/agent.py"),
    ("T-040", "CLI de chat", "brain/clients/cli.py"),
]


def is_implemented(rel: str) -> bool:
    path = ROOT / rel
    if not path.exists():
        return False
    src = path.read_text(encoding="utf-8")
    if "TODO(T-" in src or "NotImplementedError" in src:
        return False
    tree = ast.parse(src)
    defs = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return any(isinstance(n, defs) for n in tree.body)


def roadmap() -> list[dict[str, object]]:
    return [
        {"task": t, "name": n, "file": f, "implemented": is_implemented(f)} for t, n, f in ROADMAP
    ]


# --- main -------------------------------------------------------------


async def collect(live: bool, full: bool) -> list[Check]:
    checks: list[Check] = []

    def add(group: str, name: str, fn: Callable[[], tuple[str, str]]) -> None:
        s, d, ms = _timed(fn)
        checks.append(Check(group, name, s, d, ms))

    console.print("[dim]Rodando verificações offline…[/dim]")
    add("Fundação", "config.yaml carrega", check_config)
    add("Fundação", "persona (prompts/juniper.md)", check_persona)
    add("Fundação", "testes de config", lambda: _pytest("tests/test_config.py"))
    add("T-011 · Contrato", "contrato abstrato", check_contract)
    add("T-011 · Contrato", "testes do contrato", lambda: _pytest("tests/test_llm_base.py"))
    add("T-012 · Ollama", "testes (servidor falso)",
        lambda: _pytest("tests/test_ollama_provider.py"))
    add("T-013 · Groq", "testes (SDK falso)", lambda: _pytest("tests/test_groq_provider.py"))
    add("T-013 · Groq", "chave configurada", check_groq_key)
    if full:
        console.print("[dim]Rodando mypy…[/dim]")
        add("Qualidade", "mypy --strict", check_mypy)

    if live:
        console.print("[dim]Chamando os modelos de verdade (pode demorar na CPU)…[/dim]")
        checks += await _live_provider("ollama")
        if (proc := ollama_processor()) is not None:
            checks.append(proc)
        key_ok = next(c for c in checks if c.name == "chave configurada").status == OK
        if key_ok:
            checks += await _live_provider("groq")
        else:
            checks.append(Check("Ao vivo · groq", "chat/stream", SKIP, "sem chave da Groq"))
    return checks


def render(checks: list[Check]) -> None:
    table = Table(show_lines=False)
    for col in ("", "Grupo", "Verificação", "Detalhe", "ms"):
        table.add_column(col, overflow="fold")
    icon = {OK: "[green]✔[/green]", FAIL: "[red]✘[/red]", SKIP: "[yellow]–[/yellow]"}
    for c in checks:
        table.add_row(icon[c.status], c.group, c.name, c.detail, str(c.ms))
    console.print(table)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--no-live", action="store_true", help="não chama Ollama/Groq")
    ap.add_argument("--full", action="store_true", help="inclui mypy")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    checks = asyncio.run(collect(live=not args.no_live, full=args.full))
    rm = roadmap()
    render(checks)

    counts = {s: sum(c.status == s for c in checks) for s in (OK, FAIL, SKIP)}
    done = sum(bool(r["implemented"]) for r in rm)
    console.print(
        f"\n[bold]{counts[OK]} ok · {counts[FAIL]} falhas · {counts[SKIP]} puladas[/bold]"
        f"  |  roadmap: {done}/{len(rm)} módulos implementados"
    )
    for r in rm:
        mark = "[green]✔[/green]" if r["implemented"] else "[dim]·[/dim]"
        console.print(f"  {mark} {r['task']} {r['name']}")

    report = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "live": not args.no_live,
        "counts": counts,
        "checks": [asdict(c) for c in checks],
        "roadmap": rm,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "/* gerado por scripts/healthcheck.py — não edite */\n"
        f"window.JUNIPER_STATUS = {json.dumps(report, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8",
    )
    console.print(f"\n[dim]Relatório: {OUT.relative_to(ROOT)} → abra docs/html/status.html[/dim]")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
