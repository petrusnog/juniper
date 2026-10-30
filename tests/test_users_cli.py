"""``juniper users`` (ADR-027): o segredo aparece uma vez e só uma."""

from __future__ import annotations

import re
from pathlib import Path

import mongomock
import pytest
from typer.testing import CliRunner

from brain.auth import AuthService
from brain.auth.credentials import load_pepper
from brain.auth.store import UserStore
from brain.clients import cli
from brain.config import settings
from brain.memory.mongo import MongoConnection

runner = CliRunner()


@pytest.fixture(autouse=True)
def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AuthService:
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    service = AuthService(
        UserStore(connection),
        load_pepper(tmp_path / "pepper"),
        mirror_file=tmp_path / "owner.json",
        allow_testers=True,
    )
    monkeypatch.setattr(
        AuthService, "from_settings", classmethod(lambda cls, legacy_token=None: service)
    )
    monkeypatch.setattr(settings.deployment.auth, "allow_testers", True)
    return service


def run(*args: str) -> str:
    result = runner.invoke(cli.app, ["users", *args], terminal_width=200)
    return result.output


def test_add_prints_the_secret_once(_service: AuthService) -> None:
    out = run("add", "Petrus", "--role", "owner")
    secret = re.search(r"jsk_[\w-]+", out)
    client_id = re.search(r"jc_[0-9a-f]+", out)
    assert secret and client_id and "não aparece de novo" in out
    assert _service.authenticate(f"{client_id.group()}.{secret.group()}", "cli").ok
    for later in (run("list"), run("credentials", "list")):
        assert secret.group() not in later
    assert client_id.group() in run("credentials", "list")


def test_rotate_prints_a_new_secret_and_kills_the_old(_service: AuthService) -> None:
    out = run("add", "Petrus", "--role", "owner")
    found_id, found_secret = re.search(r"jc_[0-9a-f]+", out), re.search(r"jsk_[\w-]+", out)
    assert found_id and found_secret
    client_id = found_id.group()
    old = f"{client_id}.{found_secret.group()}"
    new_out = run("credentials", "rotate", client_id)
    new_secret = re.search(r"jsk_[\w-]+", new_out)
    assert new_secret and new_secret.group() not in old
    assert not _service.authenticate(old, "cli").ok
    assert _service.authenticate(f"{client_id}.{new_secret.group()}", "cli").ok


def test_errors_exit_nonzero_with_a_message() -> None:
    run("add", "Petrus", "--role", "owner")
    result = runner.invoke(cli.app, ["users", "add", "Petrus2", "--role", "owner"])
    assert result.exit_code == 1 and "Já existe um dono" in result.output
    result = runner.invoke(cli.app, ["users", "disable", "ninguém"])
    assert result.exit_code == 1 and "Não achei" in result.output


def test_tester_flow_and_disable() -> None:
    run("add", "Petrus", "--role", "owner")
    run("add", "Ana")
    assert "Ana" in run("list")
    assert "desativado" in run("disable", "Ana")
    assert "disabled" in run("list")
    assert "reativado" in run("enable", "Ana")


def test_login_saves_the_credential_with_mode_600_and_the_remote_cli_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    from brain.clients.remote_cli import read_token

    path = tmp_path / "home" / "credential"
    monkeypatch.setattr(settings.deployment.auth, "credential_file", path)
    monkeypatch.setattr(settings.deployment.auth, "token_file", tmp_path / "token")
    result = runner.invoke(
        cli.app, ["login", "--client-id", "jc_abc", "--client-secret", "jsk_xyz"]
    )
    assert result.exit_code == 0 and "jsk_xyz" not in result.output
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_token() == "jc_abc.jsk_xyz"

    assert runner.invoke(cli.app, ["logout"]).exit_code == 0
    assert not path.exists()
    with pytest.raises(Exception, match="juniper login"):
        read_token()


def test_the_legacy_token_file_still_works_without_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients.remote_cli import read_token

    (tmp_path / "token").write_text("token-antigo\n")
    monkeypatch.setattr(settings.deployment.auth, "credential_file", tmp_path / "nada")
    monkeypatch.setattr(settings.deployment.auth, "token_file", tmp_path / "token")
    assert read_token() == "token-antigo"


def test_login_refuses_something_that_is_not_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.deployment.auth, "credential_file", tmp_path / "c")
    result = runner.invoke(cli.app, ["login", "--client-id", "xx", "--client-secret", "yy"])
    assert result.exit_code == 1 and not (tmp_path / "c").exists()
