"""Verifica as dependências da Juniper dentro do venv (usado por scripts/install.sh).

Roda com o Python do venv e imprime, uma por linha, o nome de cada dependência que está
AUSENTE, QUEBRADA (instalada mas não importa) ou FORA DA VERSÃO mínima do pyproject.toml.
Saída vazia = ambiente saudável. O próprio pacote ("juniper") é verificado também.

Uso: python scripts/check_env.py [--dev]
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Distribuição -> módulo importável, quando o nome difere.
IMPORT_NAMES = {
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
    "google-api-python-client": "googleapiclient",
    "google-auth": "google.auth",
    "python-telegram-bot": "telegram",
}
# Ferramentas de linha de comando: validadas executando `python -m <módulo> --version`.
CLI_MODULES = {"ruff": "ruff"}


def parse_requirement(spec: str) -> tuple[str, tuple[int, ...] | None]:
    """``"httpx>=0.27"`` -> ``("httpx", (0, 27))``. Só o mínimo (``>=``) é verificado."""
    name = re.split(r"[<>=!~\[;\s]", spec, maxsplit=1)[0].lower()
    match = re.search(r">=\s*([0-9][0-9.]*)", spec)
    minimum = tuple(int(p) for p in match.group(1).split(".") if p) if match else None
    return name, minimum


def version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split("."):
        digits = re.match(r"\d+", piece)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def requirements(include_dev: bool) -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    specs = list(project.get("dependencies", []))
    if include_dev:
        specs += project.get("optional-dependencies", {}).get("dev", [])
    return specs


def problem_with(spec: str) -> bool:
    """True if the requirement is missing, broken or older than the minimum version."""
    name, minimum = parse_requirement(spec)
    try:
        installed = md.version(name)
    except md.PackageNotFoundError:
        return True
    if minimum is not None and version_tuple(installed) < minimum:
        return True
    module = IMPORT_NAMES.get(name, name.replace("-", "_"))
    if name in CLI_MODULES:
        result = subprocess.run(
            [sys.executable, "-m", CLI_MODULES[name], "--version"],
            capture_output=True,
            check=False,
        )
        return result.returncode != 0
    try:
        importlib.import_module(module)
    except Exception:  # noqa: BLE001  (any import failure means a broken install)
        return True
    return False


def juniper_problem() -> bool:
    """The project itself must be importable from THIS checkout (editable install)."""
    try:
        brain = importlib.import_module("brain")
        importlib.import_module("brain.clients.cli")
    except Exception:  # noqa: BLE001
        return True
    return not str(Path(brain.__file__ or "").resolve()).startswith(str(ROOT))


def main() -> int:
    broken = [parse_requirement(s)[0] for s in requirements("--dev" in sys.argv) if problem_with(s)]
    if juniper_problem():
        broken.append("juniper")
    print("\n".join(broken))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
