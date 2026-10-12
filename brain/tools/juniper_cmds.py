"""Bridge to the legacy shell commands (`search`, `deploy`) so the agent can run them.

The commands live in ``shell/`` (zsh) and act on the git repository of the current
directory. They are invoked as ``zsh -c 'source juniper.sh; _juniper_dispatch "$@"' ...``:
arguments travel as separate argv entries, never through a shell string, and stdin is
closed so the scripts' interactive prompts get EOF (and refuse/abort) instead of hanging.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool

SHELL_DIR = Path(__file__).resolve().parents[2] / "shell"
ENTRY = SHELL_DIR / "juniper.sh"

SEARCH_TIMEOUT = 30.0
DEPLOY_TIMEOUT = 300.0
MAX_OUTPUT_CHARS = 20_000
MAX_TERMS = 10
MAX_TERM_CHARS = 200

_DISPATCH = 'source "$1"; shift; _juniper_dispatch "$@"'
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DIGITS = re.compile(r"^[0-9]{1,10}$")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def legacy_argv(args: list[str]) -> list[str]:
    """Command line that runs ``juniper <args>`` from the legacy shell layer."""
    return ["zsh", "-c", _DISPATCH, "zsh", str(ENTRY), *args]


async def run_legacy(args: list[str], timeout: float, cwd: Path | None = None) -> tuple[int, str]:
    """Run a legacy command without a terminal; return ``(exit_code, cleaned_output)``."""
    if shutil.which("zsh") is None:
        return 127, "zsh não está instalado"
    # JUNIPER_SHELL_DIR is set by juniper.sh itself; start from a clean value.
    env = {**os.environ, "JUNIPER_SHELL_DIR": str(SHELL_DIR), "GIT_TERMINAL_PROMPT": "0"}
    proc = await asyncio.create_subprocess_exec(
        *legacy_argv(args),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=env,
    )
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"tempo esgotado após {timeout:.0f}s"
    text = strip_ansi(raw.decode("utf-8", "replace")).strip()
    if len(text) > MAX_OUTPUT_CHARS:
        text = f"{text[:MAX_OUTPUT_CHARS]}\n…[saída cortada]"
    return proc.returncode or 0, text


async def _git(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    raw, _ = await proc.communicate()
    return proc.returncode or 0, raw.decode("utf-8", "replace").strip()


def _clean_terms(value: object) -> list[str] | str:
    """Validated search terms, or an error message."""
    if not isinstance(value, list) or not value:
        return "informe ao menos um termo em 'terms'"
    if len(value) > MAX_TERMS:
        return f"no máximo {MAX_TERMS} termos"
    terms: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return "cada termo deve ser um texto não vazio"
        if len(item) > MAX_TERM_CHARS:
            return f"termo maior que {MAX_TERM_CHARS} caracteres"
        terms.append(item.strip())
    return terms


def _clean_ids(value: object, label: str) -> list[str] | str:
    if not isinstance(value, list):
        return f"'{label}' deve ser uma lista de números"
    ids: list[str] = []
    for item in value:
        text = str(item).strip()
        if not _DIGITS.match(text) or isinstance(item, bool):
            return f"{label}: '{item}' não é um número de task válido"
        ids.append(text)
    return ids


@register_tool
class GitSearch(Tool):
    name = "git_search"
    description = (
        "Busca commits no repositório git do diretório atual cujas mensagens contenham qualquer "
        "um dos termos (comando 'search' da Juniper). Use quando pedirem commits de uma "
        "feature/task, ex.: 'busque os commits da feature 1339' -> terms=['1339']. "
        "Devolve data, hash, mensagem e autor."
    )
    parameters = {
        "type": "object",
        "properties": {
            "terms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Termos da busca (número da task, palavra...); basta casar com um.",
            }
        },
        "required": ["terms"],
    }
    tier = Tier.READ
    locality = Locality.EXECUTOR

    async def run(self, **kwargs: Any) -> ToolResult:
        terms = _clean_terms(kwargs.get("terms"))
        if isinstance(terms, str):
            return ToolResult(success=False, error=terms)
        code, _ = await _git("rev-parse", "--is-inside-work-tree")
        if code != 0:
            return ToolResult(success=False, error="o diretório atual não é um repositório git")
        code, text = await run_legacy(["search", *terms], SEARCH_TIMEOUT)
        if code == 0:
            return ToolResult(success=True, output=text, metadata={"terms": terms})
        if code == 1 and "Não encontrei" in text:  # "no match" is an answer, not a failure
            return ToolResult(
                success=True, output=f"Nenhum commit encontrado para: {', '.join(terms)}"
            )
        return ToolResult(success=False, error=text or f"falha (código {code})")


@register_tool
class GitDeploy(Tool):
    name = "git_deploy"
    description = (
        "Faz o deploy de uma feature (comando 'deploy' da Juniper): acha os commits "
        "feature(<id>)/hotfix(<id>), faz cherry-pick nas branches feature/<id>-develop e "
        "feature/<id>-stage e dá push. Commits já presentes são ignorados. Só use quando a pessoa "
        "pedir deploy explicitamente. Para só consultar commits, use git_search."
    )
    parameters = {
        "type": "object",
        "properties": {
            "feature_id": {"type": "string", "description": "Número da task/feature, só dígitos."},
            "extra_tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Outras tasks cujos commits vão junto, nas branches de feature_id.",
            },
            "target": {
                "type": "string",
                "enum": ["both", "develop", "stage"],
                "description": "Branch de destino. Padrão: both.",
            },
        },
        "required": ["feature_id"],
    }
    tier = Tier.DESTRUCTIVE  # pushes to the remote: cannot be undone from here
    locality = Locality.EXECUTOR

    @staticmethod
    def _plan(kwargs: dict[str, Any]) -> tuple[list[str], list[str]] | str:
        """Validated ``(deploy argv, all task ids)``, or an error message."""
        feature_id = str(kwargs.get("feature_id", "")).strip()
        if not _DIGITS.match(feature_id) or isinstance(kwargs.get("feature_id"), bool):
            return "feature_id deve conter só dígitos"
        extra = _clean_ids(kwargs.get("extra_tasks", []), "extra_tasks")
        if isinstance(extra, str):
            return extra
        target = kwargs.get("target", "both")
        if target not in {"both", "develop", "stage"}:
            return "target deve ser both, develop ou stage"
        argv = ["deploy", feature_id]
        if extra:
            argv += ["--tasks", *extra]
        if target != "both":
            argv.append(f"--{target}")
        return argv, [feature_id, *extra]

    async def subjects(self, **kwargs: Any) -> list[str] | None:
        plan = self._plan(kwargs)
        return None if isinstance(plan, str) else plan[1]

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        plan = self._plan(kwargs)
        if isinstance(plan, str):
            return ToolResult(success=False, error=plan)
        _, ids = plan
        code, _ = await _git("rev-parse", "--is-inside-work-tree")
        if code != 0:
            return ToolResult(success=False, error="o diretório atual não é um repositório git")
        _, dirty = await _git("status", "--porcelain", "--untracked-files=no")
        if dirty:
            return ToolResult(
                success=False,
                error="há alterações não commitadas; commite ou guarde (stash) antes do deploy",
            )
        patterns = [f"--grep=feature({i})" for i in ids] + [f"--grep=hotfix({i})" for i in ids]
        _, found = await _git("log", "--fixed-strings", "--oneline", "-n", "1", *patterns)
        if not found:
            return ToolResult(
                success=False,
                error=f"nenhum commit feature/hotfix encontrado para: {', '.join(ids)}",
            )
        return None

    async def run(self, **kwargs: Any) -> ToolResult:
        plan = self._plan(kwargs)
        if isinstance(plan, str):
            return ToolResult(success=False, error=plan)
        argv, ids = plan
        code, text = await run_legacy(argv, DEPLOY_TIMEOUT)
        if code == 0:
            return ToolResult(success=True, output=text, metadata={"tasks": ids})
        return ToolResult(success=False, error=text or f"falha (código {code})")
