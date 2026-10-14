"""MongoDB layer (T-053): friendly errors, configuration, store selection, Mongo specifics.

Everything here runs without a server, except the ``integration`` tests at the end.
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pymongo.errors import OperationFailure
from typer.testing import CliRunner

from brain.clients import cli
from brain.config import MongoSettings, SessionMemorySettings, Settings, settings
from brain.core.session import Session
from brain.llm.router import LLMRouter
from brain.memory.errors import MemoryUnavailableError
from brain.memory.mongo import MongoConnection, MongoSessionStore, _where
from brain.memory.session import FileSessionStore, SessionStoreError
from brain.memory.stores import close_session_store, open_session_store
from tests.test_agent import FakeProvider, say
from tests.test_session_memory import turn, users

runner = CliRunner()

# Nothing listens on port 1: the connection is refused at once, with no server needed.
DOWN = MongoSettings(uri="mongodb://localhost:1", timeout_ms=200)


# --- configuration ------------------------------------------------------------------------


def test_files_are_the_default_store() -> None:
    assert SessionMemorySettings().store == "file"


def test_unknown_store_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUNIPER_MEMORY__SESSION__STORE", "postgres")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_mongo_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUNIPER_MEMORY__MONGO__USERNAME", "juniper")
    monkeypatch.setenv("JUNIPER_MEMORY__MONGO__PASSWORD", "s3nha:com/@estranha")
    mongo = Settings(_env_file=None).memory.mongo  # type: ignore[call-arg]
    assert mongo.username == "juniper"
    assert mongo.password is not None
    assert mongo.password.get_secret_value() == "s3nha:com/@estranha"


def test_the_password_never_shows_up_in_a_repr() -> None:
    mongo = MongoSettings(username="juniper", password="segredo-de-verdade")  # type: ignore[arg-type]
    assert "segredo-de-verdade" not in repr(mongo) and "segredo-de-verdade" not in str(mongo)


def test_mongo_limits_are_validated() -> None:
    with pytest.raises(ValidationError):
        MongoSettings(timeout_ms=1)
    with pytest.raises(ValidationError):
        MongoSettings(database="")


# --- friendly errors ----------------------------------------------------------------------


def test_credentials_are_hidden_from_messages() -> None:
    assert _where("mongodb://juniper:segredo@db:27017") == "mongodb://db:27017"
    assert _where("mongodb://localhost:27017") == "mongodb://localhost:27017"


def test_a_server_that_is_down_gives_a_friendly_message() -> None:
    connection = MongoConnection(DOWN)
    try:
        with pytest.raises(MemoryUnavailableError) as raised:
            connection.check()
    finally:
        connection.close()
    message = str(raised.value)
    assert "docker compose up -d" in message and "localhost:1" in message
    assert "Traceback" not in message and "ServerSelection" not in message


def test_a_rejected_login_says_so() -> None:
    connection = MongoConnection(DOWN)
    with pytest.raises(MemoryUnavailableError, match="usuário ou a senha"), connection.guard():
        raise OperationFailure("Authentication failed.", code=18)


def test_other_driver_failures_are_reported_too() -> None:
    connection = MongoConnection(DOWN)
    with pytest.raises(MemoryUnavailableError, match="recusou a operação"), connection.guard():
        raise OperationFailure("not authorized", code=13)


def test_a_store_without_a_server_fails_with_the_friendly_error() -> None:
    store = MongoSessionStore(MongoConnection(DOWN))
    s = Session("p")
    turn(s, "oi")
    try:
        with pytest.raises(MemoryUnavailableError):
            store.save(s)
        with pytest.raises(MemoryUnavailableError):
            store.latest()
    finally:
        store.connection.close()


def test_an_empty_session_does_not_touch_the_server() -> None:
    MongoSessionStore(MongoConnection(DOWN)).save(Session("p"))  # no error: nothing to save


# --- choosing the store -------------------------------------------------------------------


def test_the_file_store_is_chosen_by_default() -> None:
    assert isinstance(open_session_store(), FileSessionStore)


def test_mongo_is_checked_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.memory.session, "store", "mongo")
    monkeypatch.setattr(settings.memory, "mongo", DOWN)
    with pytest.raises(MemoryUnavailableError, match="docker compose up -d"):
        open_session_store()


def test_a_missing_driver_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.memory.session, "store", "mongo")
    monkeypatch.setitem(sys.modules, "brain.memory.mongo", None)  # makes the import fail
    with pytest.raises(MemoryUnavailableError, match="pip install"):
        open_session_store()


def test_the_file_store_never_needs_the_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "brain.memory.mongo", None)
    assert isinstance(open_session_store(), FileSessionStore)


# --- CLI ----------------------------------------------------------------------------------


def test_chat_with_mongo_down_prints_a_message_and_not_a_stacktrace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.memory.session, "store", "mongo")
    monkeypatch.setattr(settings.memory, "mongo", DOWN)
    monkeypatch.setattr(cli.registry, "discover", lambda *a, **k: None)
    provider = FakeProvider([say("não deveria chegar aqui")])
    monkeypatch.setattr(cli, "build_router", lambda name: LLMRouter([provider]))

    result = runner.invoke(cli.app, ["oi"])

    assert result.exit_code == 1
    assert "Não consegui conectar ao MongoDB" in result.output
    assert "docker compose up -d" in result.output
    assert "Traceback" not in result.output
    assert provider.seen == []  # nothing was sent to the LLM


# --- integration: needs `docker compose up -d` --------------------------------------------


@pytest.fixture
def mongo() -> Iterator[MongoSessionStore]:
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


def raw(store: MongoSessionStore) -> Any:
    return store.connection.collection("sessions")


@pytest.mark.integration
def test_the_session_id_is_the_document_id(mongo: MongoSessionStore) -> None:
    s = Session("p")
    turn(s, "oi")
    mongo.save(s)
    document = raw(mongo).find_one({"_id": s.id})
    assert document is not None
    assert document["turns"] == 1 and document["preview"] == "oi"
    assert "system_prompt" not in document and "id" not in document


@pytest.mark.integration
def test_indexes_are_created(mongo: MongoSessionStore) -> None:
    s = Session("p")
    turn(s, "oi")
    mongo.save(s)
    keys = [
        tuple(name for name, _ in info["key"]) for info in raw(mongo).index_information().values()
    ]
    assert ("saved_at",) in keys and ("updated_at", "saved_at") in keys


@pytest.mark.integration
def test_a_corrupt_document_raises_a_clear_error_on_load(mongo: MongoSessionStore) -> None:
    raw(mongo).insert_one({"_id": "quebrada", "version": 1})
    with pytest.raises(SessionStoreError, match="corrompida"):
        mongo.load("quebrada")


@pytest.mark.integration
def test_an_unsupported_version_is_refused(mongo: MongoSessionStore) -> None:
    raw(mongo).insert_one({"_id": "futura", "version": 99})
    with pytest.raises(SessionStoreError):
        mongo.load("futura")


@pytest.mark.integration
def test_latest_and_list_skip_damaged_documents(mongo: MongoSessionStore) -> None:
    good = Session("p")
    turn(good, "boa")
    mongo.save(good)
    raw(mongo).insert_one({"_id": "estragada", "version": 1, "saved_at": "9999"})  # newest
    latest = mongo.latest()
    assert latest is not None and latest.id == good.id
    assert [i.id for i in mongo.list_sessions()] == [good.id]


@pytest.mark.integration
def test_a_resumed_conversation_keeps_growing_in_one_document(mongo: MongoSessionStore) -> None:
    s = Session("p")
    turn(s, "um")
    mongo.save(s)
    resumed = mongo.latest()
    assert resumed is not None
    turn(resumed, "dois")
    mongo.save(resumed)
    assert raw(mongo).count_documents({}) == 1
    final = mongo.load(s.id)
    assert final is not None and users(final) == ["um", "dois"]


# --- shutdown -----------------------------------------------------------------------------


def driver_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if "pymongo" in t.name.lower()]


def test_closing_the_store_stops_the_driver_threads() -> None:
    """Left running, they are joined by an exit hook, and Ctrl+C there prints a traceback."""
    store = MongoSessionStore(MongoConnection(DOWN))
    with pytest.raises(MemoryUnavailableError):
        store.check()  # starts the driver's background monitors
    assert driver_threads()
    store.close()
    deadline = time.time() + 3
    while driver_threads() and time.time() < deadline:
        time.sleep(0.05)
    assert driver_threads() == []


def test_a_failed_startup_check_releases_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.memory.session, "store", "mongo")
    monkeypatch.setattr(settings.memory, "mongo", DOWN)
    with pytest.raises(MemoryUnavailableError):
        open_session_store()
    deadline = time.time() + 3
    while driver_threads() and time.time() < deadline:
        time.sleep(0.05)
    assert driver_threads() == []


def test_the_chat_closes_its_store_when_it_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    closed: list[bool] = []

    class ClosingStore(FileSessionStore):
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(cli, "open_session_store", lambda: ClosingStore(tmp_path / "s"))
    monkeypatch.setattr(cli.registry, "discover", lambda *a, **k: None)
    monkeypatch.setattr(cli, "build_router", lambda name: LLMRouter([FakeProvider([say("Oi!")])]))
    assert runner.invoke(cli.app, ["oi"]).exit_code == 0
    assert closed == [True]


def test_stores_without_resources_can_be_closed_anyway() -> None:
    close_session_store(FileSessionStore())  # no close(): nothing to do, no error
