"""Contract every ``SessionStore`` must honor (T-051 file store, T-053 MongoDB store).

The same tests run against both. The MongoDB ones are ``integration``: they need the local
server (``docker compose up -d``) and are left out of the default CI run.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from brain.config import settings
from brain.core.session import Session, load_system_prompt
from brain.memory.errors import MemoryUnavailableError
from brain.memory.session import FileSessionStore, SessionStore, SessionStoreError
from tests.test_session_memory import turn, users

KINDS = [
    pytest.param("file", id="file"),
    pytest.param("mongo", id="mongo", marks=pytest.mark.integration),
]


@pytest.fixture
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SessionStore]:
    if request.param == "file":
        yield FileSessionStore(tmp_path / "store")
        return

    from brain.memory.mongo import MongoConnection, MongoSessionStore

    # A database of its own, dropped at the end: tests never touch real conversations.
    config = settings.memory.mongo.model_copy(
        update={"database": f"juniper_test_{uuid.uuid4().hex}"}
    )
    connection = MongoConnection(config)
    try:
        connection.check()
    except MemoryUnavailableError as exc:
        connection.close()
        pytest.skip(f"MongoDB local indisponível: {exc}")
    try:
        yield MongoSessionStore(connection)
    finally:
        connection.client.drop_database(config.database)
        connection.close()


pytestmark = pytest.mark.parametrize("store", KINDS, indirect=True)


def pause() -> None:
    """Saves close together must still be ordered (file mtime, document timestamp)."""
    time.sleep(0.02)


def test_save_and_load_roundtrip(store: SessionStore) -> None:
    s = Session("p")
    turn(s, "marca dentista", reply="marcado", tools=True)
    store.save(s)
    loaded = store.load(s.id)
    assert loaded is not None
    assert loaded.history == s.history
    assert (loaded.id, loaded.created_at, loaded.updated_at) == (s.id, s.created_at, s.updated_at)


def test_unicode_survives(store: SessionStore) -> None:
    s = Session("p")
    turn(s, "ação, coração e “aspas” 🌿")
    store.save(s)
    loaded = store.load(s.id)
    assert loaded is not None and users(loaded) == ["ação, coração e “aspas” 🌿"]


def test_empty_session_is_not_saved(store: SessionStore) -> None:
    store.save(Session("p"))
    assert store.latest() is None and store.list_sessions() == []


def test_system_prompt_is_not_stored_and_resume_uses_the_current_persona(
    store: SessionStore,
) -> None:
    s = Session("persona antiga e secreta")
    turn(s, "oi")
    store.save(s)
    loaded = store.load(s.id)
    assert loaded is not None and loaded.system_prompt == load_system_prompt()


def test_saving_again_replaces_the_session(store: SessionStore) -> None:
    s = Session("p")
    turn(s, "um")
    store.save(s)
    turn(s, "dois")
    store.save(s)
    loaded = store.load(s.id)
    assert loaded is not None and users(loaded) == ["um", "dois"]
    assert [i.id for i in store.list_sessions()] == [s.id]


def test_load_of_unknown_id_is_none(store: SessionStore) -> None:
    assert store.load("nao-existe") is None


def test_unsafe_ids_are_refused(store: SessionStore) -> None:
    with pytest.raises(SessionStoreError):
        store.load("../segredo")
    with pytest.raises(SessionStoreError):
        store.load("$where")
    evil = Session("p", session_id="../fora")
    turn(evil, "oi")
    with pytest.raises(SessionStoreError):
        store.save(evil)


def test_latest_is_the_most_recently_saved(store: SessionStore) -> None:
    old, new = Session("p"), Session("p")
    turn(old, "antiga")
    turn(new, "nova")
    store.save(old)
    pause()
    store.save(new)
    latest = store.latest()
    assert latest is not None and latest.id == new.id
    # resuming and saving "old" again makes it the latest
    pause()
    store.save(old)
    latest = store.latest()
    assert latest is not None and latest.id == old.id


def test_latest_and_list_without_any_session(store: SessionStore) -> None:
    assert store.latest() is None and store.list_sessions() == []


def test_list_sessions_summarizes_newest_first(store: SessionStore) -> None:
    first, second = Session("p"), Session("p")
    first.updated_at = "2026-10-12T10:00:00+00:00"
    second.updated_at = "2026-10-13T10:00:00+00:00"
    turn(first, "a" * 200)
    turn(first, "outra")
    turn(second, "só uma")
    store.save(first)
    store.save(second)
    infos = store.list_sessions()
    assert [i.id for i in infos] == [second.id, first.id]
    assert infos[0].turns == 1 and infos[1].turns == 2
    assert infos[1].preview == "a" * 60
    assert infos[1].created_at == first.created_at and infos[1].updated_at == first.updated_at
