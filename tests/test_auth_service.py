"""Usuários e credenciais (ADR-027): geração, hash, autenticação, cadastro e espelho do dono."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import mongomock
import pytest

from brain.auth import AuthError, AuthService, format_bearer, parse_bearer
from brain.auth.credentials import hash_secret, load_pepper
from brain.auth.store import CREDENTIALS_COLLECTION, UserStore
from brain.memory.errors import MemoryUnavailableError
from brain.memory.mongo import MongoConnection


def _store() -> UserStore:
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return UserStore(connection)


@pytest.fixture
def service(tmp_path: Path) -> AuthService:
    return AuthService(
        _store(),
        load_pepper(tmp_path / "pepper"),
        mirror_file=tmp_path / "owner.json",
        legacy_token="legacy-static-token",
        allow_testers=True,
    )


def test_bearer_roundtrip() -> None:
    assert parse_bearer(format_bearer("jc_abc", "jsk_xyz")) == ("jc_abc", "jsk_xyz")
    for bad in (None, "", "token-sem-ponto", "xx_abc.secret", "jc_abc."):
        assert parse_bearer(bad) is None


def test_pepper_is_created_once_with_mode_600(tmp_path: Path) -> None:
    path = tmp_path / "auth" / "pepper"
    first = load_pepper(path)
    assert load_pepper(path) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_secret_is_shown_once_and_only_a_hash_is_stored(service: AuthService) -> None:
    new = service.create_user("Petrus", "owner")
    assert new.secret.startswith("jsk_")
    stored = service.store.get_credential(new.credential.client_id)
    assert stored is not None
    assert stored.secret_hash == hash_secret(service._pepper, new.secret)
    dump = json.dumps(
        list(service.store.connection.collection(CREDENTIALS_COLLECTION).find({})), default=str
    )
    assert new.secret not in dump


def test_login_with_the_credential(service: AuthService) -> None:
    new = service.create_user("Petrus", "owner")
    result = service.authenticate(new.bearer, "1.1.1.1")
    assert result.ok and result.principal is not None
    assert result.principal.is_owner and result.principal.name == "Petrus"
    assert service.store.get_credential(new.credential.client_id).last_used_at is not None  # type: ignore[union-attr]


def test_wrong_secret_unknown_id_and_garbage_are_all_refused(service: AuthService) -> None:
    new = service.create_user("Petrus", "owner")
    wrong = format_bearer(new.credential.client_id, "jsk_errado")
    unknown = format_bearer("jc_naoexiste", new.secret)
    for bearer in (wrong, unknown, "lixo", None):
        result = service.authenticate(bearer, "2.2.2.2")
        assert not result.ok and result.reason == "invalid"


def test_five_failures_lock_the_client_even_with_the_right_secret(service: AuthService) -> None:
    new = service.create_user("Petrus", "owner")
    for _ in range(5):
        service.authenticate("jc_x.jsk_y", "3.3.3.3")
    assert service.authenticate(new.bearer, "3.3.3.3").reason == "locked"
    assert service.authenticate(new.bearer, "4.4.4.4").ok


def test_rotate_invalidates_the_old_secret(service: AuthService) -> None:
    old = service.create_user("Petrus", "owner")
    new = service.rotate(old.credential.client_id)
    assert new.credential.client_id == old.credential.client_id and new.secret != old.secret
    assert not service.authenticate(old.bearer, "5.5.5.5").ok
    assert service.authenticate(new.bearer, "5.5.5.5").ok


def test_revoke_and_disable(service: AuthService) -> None:
    service.create_user("Petrus", "owner")
    tester = service.create_user("Ana", "tester")
    service.set_active(tester.user.user_id, False)
    assert not service.authenticate(tester.bearer, "6.6.6.6").ok
    service.set_active(tester.user.user_id, True)
    assert service.authenticate(tester.bearer, "6.6.6.6").ok
    service.revoke(tester.credential.client_id)
    assert not service.authenticate(tester.bearer, "6.6.6.6").ok
    with pytest.raises(AuthError):
        service.rotate(tester.credential.client_id)


def test_owner_cannot_be_locked_out(service: AuthService) -> None:
    owner = service.create_user("Petrus", "owner")
    with pytest.raises(AuthError, match="última credencial"):
        service.revoke(owner.credential.client_id)
    with pytest.raises(AuthError, match="dono"):
        service.set_active(owner.user.user_id, False)
    second = service.add_credential(owner.user.user_id, "celular")
    service.revoke(owner.credential.client_id)
    assert service.authenticate(second.bearer, "7.7.7.7").ok


def test_registration_rules(service: AuthService, tmp_path: Path) -> None:
    service.create_user("Petrus", "owner")
    with pytest.raises(AuthError, match="Já existe um dono"):
        service.create_user("Outro", "owner")
    with pytest.raises(AuthError, match="Já existe"):
        service.create_user("petrus", "tester")
    with pytest.raises(AuthError, match="Papel"):
        service.create_user("Zé", "admin")
    with pytest.raises(AuthError, match="nome"):
        service.create_user("<script>", "tester")
    closed = AuthService(service.store, service._pepper, allow_testers=False)
    with pytest.raises(AuthError, match="allow_testers"):
        closed.create_user("Ana", "tester")


def test_legacy_token_only_while_enabled(service: AuthService) -> None:
    assert service.authenticate("legacy-static-token", "8.8.8.8").ok
    off = AuthService(service.store, service._pepper, legacy_token=None)
    assert not off.authenticate("legacy-static-token", "8.8.8.8").ok


class _DownStore(UserStore):
    def __init__(self) -> None:
        pass

    def get_credential(self, client_id):  # type: ignore[no-untyped-def]
        raise MemoryUnavailableError("fora do ar")

    def get_user(self, user_id):  # type: ignore[no-untyped-def]
        raise MemoryUnavailableError("fora do ar")

    def list_users(self):  # type: ignore[no-untyped-def]
        raise MemoryUnavailableError("fora do ar")


def test_owner_mirror_lets_only_the_owner_in_when_the_database_is_down(
    service: AuthService, tmp_path: Path
) -> None:
    owner = service.create_user("Petrus", "owner")
    tester = service.create_user("Ana", "tester")
    mirror = tmp_path / "owner.json"
    assert stat.S_IMODE(mirror.stat().st_mode) == 0o600
    assert tester.secret not in mirror.read_text() and owner.secret not in mirror.read_text()

    down = AuthService(_DownStore(), service._pepper, mirror_file=mirror)
    assert down.authenticate(owner.bearer, "9.9.9.9").ok
    assert not down.authenticate(tester.bearer, "9.9.9.9").ok


def test_principal_for_follows_revocation(service: AuthService) -> None:
    owner = service.create_user("Petrus", "owner")
    extra = service.add_credential(owner.user.user_id, "dashboard")
    assert service.principal_for(extra.credential.client_id) is not None
    service.revoke(extra.credential.client_id)
    assert service.principal_for(extra.credential.client_id) is None


def test_secrets_never_reach_the_log(service: AuthService, tmp_path: Path) -> None:
    from brain.eventlog import event_log

    new = service.create_user("Petrus", "owner")
    service.authenticate(new.bearer, "1.2.3.4")
    service.authenticate("jc_x.jsk_segredo-errado-longo-mesmo", "1.2.3.4")
    text = "".join(f.read_text() for f in event_log.directory.glob("*"))
    assert new.secret not in text and "segredo-errado" not in text
