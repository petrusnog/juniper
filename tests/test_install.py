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


def test_api_key_detection(tmp_path: Path) -> None:
    env = tmp_path / ".env"

    def present(var: str) -> str:
        probe = f'JUNIPER_ROOT="{tmp_path}"; api_key_present {var} && echo yes || echo no'
        return sh(probe).stdout.strip()

    env.write_text("JUNIPER_GROQ_API_KEY=\nJUNIPER_DEEPINFRA_API_KEY=\n")
    assert present("JUNIPER_GROQ_API_KEY") == "no" and present("JUNIPER_DEEPINFRA_API_KEY") == "no"
    env.write_text("JUNIPER_GROQ_API_KEY=gsk_abc123\nJUNIPER_DEEPINFRA_API_KEY=\n")
    assert present("JUNIPER_GROQ_API_KEY") == "yes"
    assert present("JUNIPER_DEEPINFRA_API_KEY") == "no"  # one key does not stand in for the other
    env.unlink()
    assert present("JUNIPER_GROQ_API_KEY") == "no"


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


# --- memory: file or MongoDB (T-053) ----------------------------------------------------

import re  # noqa: E402
import stat  # noqa: E402

import yaml  # noqa: E402


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


set_memory_store = load_script("set_memory_store")
EXAMPLE = (ROOT / "config.yaml.example").read_text(encoding="utf-8")


def store_of(text: str) -> str:
    return str(yaml.safe_load(text)["memory"]["session"]["store"])


@pytest.mark.parametrize("value", ["file", "mongo"])
def test_set_store_edits_the_example_and_keeps_everything_else(value: str) -> None:
    result = set_memory_store.set_store(EXAMPLE, value)
    assert store_of(result) == value
    before, after = yaml.safe_load(EXAMPLE), yaml.safe_load(result)
    before["memory"]["session"]["store"] = after["memory"]["session"]["store"] = None
    assert before == after  # nothing else changed
    assert "# Memória da conversa (ADR-011)" in result  # comments survive
    assert set_memory_store.set_store(result, value) == result  # idempotent


def test_set_store_keeps_the_inline_comment() -> None:
    result = set_memory_store.set_store("memory:\n  session:\n    store: file   # onde\n", "mongo")
    assert result == "memory:\n  session:\n    store: mongo   # onde\n"


def test_set_store_appends_a_memory_block_when_there_is_none() -> None:
    result = set_memory_store.set_store("agent:\n  language: pt-BR\n", "mongo")
    assert store_of(result) == "mongo" and yaml.safe_load(result)["agent"]["language"] == "pt-BR"


def test_set_store_adds_session_and_store_when_missing() -> None:
    only_memory = set_memory_store.set_store("memory:\n  mongo:\n    database: x\n", "mongo")
    parsed = yaml.safe_load(only_memory)
    assert parsed["memory"]["session"]["store"] == "mongo"
    assert parsed["memory"]["mongo"]["database"] == "x"

    no_store = set_memory_store.set_store(
        "memory:\n  session:\n    max_turns: 8\nllm:\n  x: 1\n", "mongo"
    )
    parsed = yaml.safe_load(no_store)
    assert parsed["memory"]["session"] == {"max_turns": 8, "store": "mongo"} and parsed["llm"] == {
        "x": 1
    }


def test_set_store_does_not_touch_a_store_key_of_another_section() -> None:
    text = "other:\n  store: file\nmemory:\n  session:\n    max_turns: 8\n"
    parsed = yaml.safe_load(set_memory_store.set_store(text, "mongo"))
    assert parsed["other"]["store"] == "file" and parsed["memory"]["session"]["store"] == "mongo"


def test_set_store_refuses_unknown_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        set_memory_store.set_store(EXAMPLE, "postgres")
    config = tmp_path / "config.yaml"
    config.write_text(EXAMPLE)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "set_memory_store.py"), str(config), "postgres"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and config.read_text() == EXAMPLE


def test_memory_option_is_validated_and_documented() -> None:
    bad = subprocess.run(
        ["bash", str(INSTALL_SH), "--memory=postgres"], capture_output=True, text=True, check=False
    )
    assert bad.returncode == 2 and "file" in bad.stderr and "mongo" in bad.stderr
    assert sh("parse_flags --memory=mongo; echo $MEMORY_ARG").stdout.strip() == "mongo"
    helped = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"], capture_output=True, text=True, check=False
    )
    assert "--memory" in helped.stdout


def test_passwords_are_random_and_easy_to_paste() -> None:
    first, second = (sh("generate_password").stdout for _ in range(2))
    assert re.fullmatch(r"[0-9a-f]{24}", first) and first != second


def test_env_variables_are_replaced_or_added(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\nB=\n")
    sh(f'JUNIPER_ROOT="{tmp_path}"; set_env_var B novo; set_env_var C "x y"; set_env_var B outro')
    assert (tmp_path / ".env").read_text() == "A=1\nB=outro\nC=x y\n"
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    read = sh(f'JUNIPER_ROOT="{tmp_path}"; env_value A; env_value NADA; env_value C')
    assert read.stdout.split("\n")[:2] == ["1", "x y"]


class Sandbox:
    """A throwaway Juniper folder plus a fake ``docker`` that records what it was asked."""

    def __init__(self, tmp_path: Path, *, docker: bool = True) -> None:
        self.root = tmp_path / "juniper"
        (self.root / "scripts").mkdir(parents=True)
        shutil.copy(ROOT / "scripts" / "set_memory_store.py", self.root / "scripts")
        shutil.copy(ROOT / "config.yaml.example", self.root / "config.yaml")
        shutil.copy(ROOT / ".env.example", self.root / ".env")
        self.venv = tmp_path / "venv"
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "bin" / "python").symlink_to(sys.executable)
        self.docker_log = tmp_path / "docker.log"
        self.docker_log.write_text("")
        self.bin = tmp_path / "fakebin"
        self.bin.mkdir()
        if docker:
            fake = self.bin / "docker"
            fake.write_text(
                "#!/bin/sh\n"
                'echo "$@" >> "$FAKE_DOCKER_LOG"\n'
                'case "$1" in\n'
                '  info) [ "${FAKE_INFO:-1}" = 1 ]; exit $? ;;\n'
                '  volume) [ "${FAKE_VOLUME:-0}" = 1 ]; exit $? ;;\n'
                "  compose)\n"
                '    if [ "$2" = version ]; then [ "${FAKE_COMPOSE:-1}" = 1 ]; exit $?; fi\n'
                '    [ "${FAKE_UP:-1}" = 1 ]; exit $? ;;\n'
                "esac\nexit 0\n"
            )
            fake.chmod(0o755)

    def run(self, script: str, **fake: str) -> subprocess.CompletedProcess[str]:
        """Run ``script`` with the installer's functions, pointed at this sandbox."""
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(self.docker_log),
            **{f"FAKE_{k.upper()}": v for k, v in fake.items()},
        }
        setup = (
            f'JUNIPER_ROOT="{self.root}"; VENV_DIR="{self.venv}"; LOG_DIR="{self.root}"; '
            f'LOG_FILE="{self.root}/install.log"; sleep() {{ :; }}; '
            "verify_mongo() { return 0; }; "
        )
        return sh(setup + script, env=env)

    def env(self) -> str:
        return (self.root / ".env").read_text()

    def config(self) -> str:
        return (self.root / "config.yaml").read_text()

    def docker_calls(self) -> list[str]:
        return self.docker_log.read_text().splitlines()


def test_docker_problems_are_explained(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    assert box.run("docker_problem", info="1").stdout.strip() == ""
    assert "não está rodando" in box.run("docker_problem", info="0").stdout
    assert "docker compose" in box.run("docker_problem", compose="0").stdout
    bare = Sandbox(tmp_path / "bare", docker=False)
    minimal = tmp_path / "minimal"
    minimal.mkdir()
    for tool in ("bash", "mktemp", "rm", "dirname", "uname"):
        found = shutil.which(tool)
        assert found
        (minimal / tool).symlink_to(found)
    result = sh("docker_problem", env={"PATH": str(minimal)})
    assert "não está instalado" in result.stdout
    assert bare.run("true").returncode == 0


def test_choosing_mongo_sets_everything_up(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    result = box.run("MEMORY_ARG=mongo; ensure_memory_store")
    assert result.returncode == 0, result.stderr
    assert store_of(box.config()) == "mongo"
    assert "JUNIPER_MEMORY__MONGO__USERNAME=juniper" in box.env()
    assert re.search(r"^JUNIPER_MEMORY__MONGO__PASSWORD=[0-9a-f]{24}$", box.env(), re.MULTILINE)
    assert any(" up -d --wait" in call for call in box.docker_calls())
    assert "Docker" in result.stdout  # it said Docker was needed, and that it found it


def test_the_database_credentials_are_kept_on_a_second_run(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    box.run("MEMORY_ARG=mongo; ensure_memory_store")
    first = box.env()
    box.run("ensure_memory_store")  # no option: the config already says mongo
    assert box.env() == first and store_of(box.config()) == "mongo"
    assert sum(" up -d --wait" in c for c in box.docker_calls()) == 2  # makes sure it is running


def test_without_docker_it_falls_back_to_files_and_says_why(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    before = box.env()
    result = box.run("MEMORY_ARG=mongo; ensure_memory_store", info="0")
    assert result.returncode == 0
    assert store_of(box.config()) == "file" and box.env() == before  # no credentials invented
    assert "não está rodando" in result.stdout and "juniper install --memory=mongo" in result.stdout
    assert not any(" up " in call for call in box.docker_calls())


def test_a_failed_start_falls_back_to_files(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    result = box.run("MEMORY_ARG=mongo; ensure_memory_store", up="0")
    assert result.returncode == 0 and store_of(box.config()) == "file"
    assert "Não consegui subir o MongoDB" in result.stdout


def test_an_old_database_without_credentials_is_never_overwritten(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    result = box.run("MEMORY_ARG=mongo; ensure_memory_store", volume="1")
    assert result.returncode == 0 and store_of(box.config()) == "file"
    assert not re.search(r"MONGO__PASSWORD=\S", box.env())  # still empty
    assert "docker compose down -v" in result.stdout
    assert not any(" up " in call for call in box.docker_calls())


def test_a_mongo_setup_is_not_downgraded_when_docker_is_off(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    box.run("MEMORY_ARG=mongo; ensure_memory_store")
    result = box.run("ensure_memory_store", info="0")
    assert result.returncode == 0 and store_of(box.config()) == "mongo"
    assert "Mantive o MongoDB" in result.stdout


def test_without_a_terminal_it_asks_nothing_and_never_touches_docker(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    result = box.run("CONFIG_FRESH=true; ensure_memory_store")
    assert result.returncode == 0 and store_of(box.config()) == "file"
    assert box.docker_calls() == []


def test_an_earlier_choice_of_files_is_respected_without_asking(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    result = box.run("CONFIG_FRESH=false; ensure_memory_store")
    assert "arquivos" in result.stdout and box.docker_calls() == []


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("2\n", "mongo"),
        ("mongo\n", "mongo"),
        ("MongoDB\n", "mongo"),
        ("1\n", "file"),
        ("\n", "file"),
    ],
)
def test_the_question_defaults_to_files(tmp_path: Path, answer: str, expected: str) -> None:
    box = Sandbox(tmp_path)
    full_env = {**os.environ, "JUNIPER_INSTALL_LIB": "1"}
    result = subprocess.run(
        [BASH, "-c", f'source "{INSTALL_SH}"; ask_memory_choice; echo "=$MEMORY_ARG"'],
        input=answer,
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )
    assert result.stdout.strip().endswith(f"={expected}")
    assert "precisa do Docker" in result.stdout  # the requirement is stated up front
    assert box.root.exists()


def _ask_in_a_pty(typed: bytes, *, icrnl: bool) -> str:
    """Run ``ask_memory_choice`` on a real terminal and type ``typed`` (Enter is a CR)."""
    import pty
    import select
    import termios
    import time

    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    if not icrnl:  # what a terminal left in this state by an interrupted program looks like
        attrs[0] &= ~termios.ICRNL
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    proc = subprocess.Popen(
        [BASH, "-c", f'source "{INSTALL_SH}"; ask_memory_choice; echo "=$MEMORY_ARG="'],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={**os.environ, "JUNIPER_INSTALL_LIB": "1"},
        close_fds=True,
    )
    os.close(slave)
    output, deadline, sent = b"", time.monotonic() + 10, False
    while time.monotonic() < deadline and proc.poll() is None:
        if select.select([master], [], [], 0.2)[0]:
            try:
                output += os.read(master, 4096)
            except OSError:
                break
        if not sent and output.rstrip().endswith(b"VOC\xc3\x8a:"):
            os.write(master, typed)  # typed after the prompt, as a person does
            sent = True
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    os.close(master)
    return output.decode(errors="replace")


@pytest.mark.parametrize("icrnl", [True, False])
def test_enter_sent_as_cr_still_answers_the_question(icrnl: bool) -> None:
    """Regression: with ICRNL off, Enter showed "^M" and the installer never moved on."""
    out = _ask_in_a_pty(b"2\r", icrnl=icrnl)
    assert "=mongo=" in out, out
