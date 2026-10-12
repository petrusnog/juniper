"""Tests for the installer: scripts/check_env.py and the helpers of scripts/install.sh."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from brain.clients import cli

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "scripts" / "install.sh"
BASH = shutil.which("bash") or "bash"


def load_check_env() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_env", ROOT / "scripts" / "check_env.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_env = load_check_env()


def sh(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run bash code with the installer's functions loaded (without running ``main``)."""
    full_env = {**os.environ, "JUNIPER_INSTALL_LIB": "1", **(env or {})}
    return subprocess.run(
        [BASH, "-c", f'source "{INSTALL_SH}"; {script}'],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


# --- check_env.py -------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("httpx>=0.27", ("httpx", (0, 27))),
        ("pydantic-settings>=2.4", ("pydantic-settings", (2, 4))),
        ("typer", ("typer", None)),
        ("foo[extra]>=1.2.3; python_version>'3.8'", ("foo", (1, 2, 3))),
        ("Mixed_Case>=1", ("mixed_case", (1,))),
    ],
)
def test_parse_requirement(spec: str, expected: tuple[str, tuple[int, ...] | None]) -> None:
    assert check_env.parse_requirement(spec) == expected


def test_version_tuple_ignores_suffixes() -> None:
    assert check_env.version_tuple("2.8.1") == (2, 8, 1)
    assert check_env.version_tuple("1.0rc1") == (1, 0)
    assert check_env.version_tuple("0.27.0.post1") == (0, 27, 0)


def test_problem_detection() -> None:
    assert check_env.problem_with("pytest>=1") is False
    assert check_env.problem_with("pacote-que-nao-existe-xyz>=1") is True  # missing
    assert check_env.problem_with("pytest>=9999") is True  # too old


def test_broken_import_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installed metadata but a module that no longer imports (half-deleted package)."""
    monkeypatch.setitem(check_env.IMPORT_NAMES, "pytest", "modulo_que_nao_importa_xyz")
    assert check_env.problem_with("pytest>=1") is True


def test_requirements_follow_pyproject() -> None:
    runtime = check_env.requirements(include_dev=False)
    with_dev = check_env.requirements(include_dev=True)
    assert any(r.startswith("typer") for r in runtime)
    assert not any(r.startswith("pytest") for r in runtime)
    assert any(r.startswith("pytest") for r in with_dev)


def test_current_environment_is_healthy() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_env.py"), "--dev"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""


# --- install.sh helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("Linux x86_64 gnu", "x86_64-unknown-linux-gnu"),
        ("Linux aarch64 musl", "aarch64-unknown-linux-musl"),
        ("Linux amd64 gnu", "x86_64-unknown-linux-gnu"),
        ("Darwin arm64 gnu", "aarch64-apple-darwin"),
        ("Darwin x86_64 gnu", "x86_64-apple-darwin"),
    ],
)
def test_uv_triple(args: str, expected: str) -> None:
    result = sh(f"uv_triple {args}")
    assert result.returncode == 0 and result.stdout.strip() == expected


def test_uv_triple_rejects_unsupported_systems() -> None:
    assert sh("uv_triple FreeBSD amd64 gnu").returncode != 0
    assert sh("uv_triple Linux riscv64 gnu").returncode != 0


def test_unknown_option_is_rejected() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--nao-existe"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2 and "desconhecida" in result.stderr


def test_help_prints_usage() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0 and "--dev" in result.stdout and "--check" in result.stdout


def test_rc_path_is_added_once(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("# meu rc\n")
    for _ in range(3):
        assert sh(f'rc_add_path "{rc}"').returncode == 0
    text = rc.read_text()
    assert text.count("Juniper - PATH") == 1 and ".local/bin" in text
    assert (tmp_path / ".bashrc.juniper.bak").read_text() == "# meu rc\n"  # backup of the original


def test_rc_path_respects_existing_configuration(tmp_path: Path) -> None:
    rc = tmp_path / ".zshrc"
    rc.write_text('export PATH="$HOME/.local/bin:$PATH"\n')
    sh(f'rc_add_path "{rc}"')
    assert "Juniper - PATH" not in rc.read_text()


def test_rc_hook_migrates_old_line_and_is_idempotent(tmp_path: Path) -> None:
    rc = tmp_path / ".zshrc"
    rc.write_text("alias x=y\n[ -f ~/.juniper/juniper.sh ] && source ~/.juniper/juniper.sh\n")
    sh(f'rc_add_hook "{rc}"')
    sh(f'rc_add_hook "{rc}"')
    text = rc.read_text()
    assert "alias x=y" in text  # unrelated lines survive
    assert "~/.juniper/juniper.sh" not in text  # old path is gone
    assert text.count("# Juniper - shell") == 1 and text.count("source ") == 1


def test_missing_rc_files_are_skipped(tmp_path: Path) -> None:
    assert (
        sh(f'rc_add_path "{tmp_path}/nao-existe"; rc_add_hook "{tmp_path}/nao-existe"').returncode
        == 0
    )
    assert not (tmp_path / "nao-existe").exists()


def test_groq_key_detection(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    probe = f'JUNIPER_ROOT="{tmp_path}"; groq_key_present && echo yes || echo no'
    env.write_text("JUNIPER_GROQ_API_KEY=\n")
    assert sh(probe).stdout.strip() == "no"
    env.write_text("JUNIPER_GROQ_API_KEY=gsk_abc123\n")
    assert sh(probe).stdout.strip() == "yes"
    env.unlink()
    assert sh(probe).stdout.strip() == "no"


def test_no_system_python_is_detected(tmp_path: Path) -> None:
    """Without any python on PATH the installer must fall back to the isolated one."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("mktemp", "rm", "dirname", "uname"):
        found = shutil.which(tool)
        assert found
        (bindir / tool).symlink_to(found)
    result = sh("find_system_python", env={"PATH": str(bindir)})
    assert result.returncode == 0 and result.stdout.strip() == ""


def test_system_python_is_found_when_it_can_build_venvs() -> None:
    result = sh("find_system_python")
    assert result.returncode == 0
    assert result.stdout.strip() != ""  # the dev machine running the tests has a usable Python


# --- `juniper install` (Python CLI) ---------------------------------------------------


def test_cli_install_hands_over_to_the_script(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli.os, "execvp", lambda f, a: calls.append((f, a)))
    result = CliRunner().invoke(cli.app, ["install", "--dev", "--clean"])
    assert result.exit_code == 0
    assert calls == [("bash", ["bash", str(cli.INSTALL_SCRIPT), "--dev", "--clean"])]
