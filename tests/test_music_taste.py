"""Musical taste (T-157, ADR-024 section 8): the book, the `music_feedback` tool and the CLI."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import mongomock
import pytest
from typer.testing import CliRunner

from brain.clients import cli
from brain.config import settings
from brain.core.dispatcher import Dispatcher
from brain.eventlog import bind_session
from brain.llm.types import ToolCall
from brain.memory.errors import MemoryUnavailableError
from brain.memory.mongo import MongoConnection
from brain.memory.v2.store import DocStore, InMemoryDocStore, MongoDocStore
from brain.music.base import MusicProvider, SearchMode, Track
from brain.music.taste import MAX_TEXT_CHARS, TASTE, WEIGHT, TasteBook, TasteError
from brain.tools import music as music_tools
from brain.tools.base import Tier
from brain.tools.registry import registry

V1 = "aaaaaaaaaaa"


def mongo_store() -> MongoDocStore:
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return MongoDocStore(connection)


@pytest.fixture(params=["memory", "mongo"])
def docs(request: pytest.FixtureRequest) -> Iterator[DocStore]:
    yield InMemoryDocStore() if request.param == "memory" else mongo_store()


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def book(docs: DocStore) -> TasteBook:
    return TasteBook(docs, Clock())


# --- the book ------------------------------------------------------------------------------


class TestBook:
    def test_it_records_what_was_said(self, book: TasteBook, docs: DocStore) -> None:
        event = book.record(
            "like", title="  Oceano ", artist="Djavan", video_id=V1, session_id="s-1"
        )
        assert (event.signal, event.title, event.artist) == ("like", "Oceano", "Djavan")
        [stored] = docs.find(TASTE)
        assert stored["_id"] == event.id and stored["session_id"] == "s-1"
        assert stored["video_id"] == V1 and book.count() == 1

    def test_title_or_artist_is_enough_but_one_is_needed(self, book: TasteBook) -> None:
        assert book.record("played", artist="Djavan").title == ""
        assert book.record("skip", title="Oceano").artist == ""
        with pytest.raises(TasteError, match="título ou o artista"):
            book.record("like")
        with pytest.raises(TasteError):
            book.record("like", title="   ", artist="\n")

    @pytest.mark.parametrize("signal", ["", "love", "LIKE", "played "])
    def test_only_the_four_signals_exist(self, book: TasteBook, signal: str) -> None:
        with pytest.raises(TasteError, match="like, dislike, skip ou played"):
            book.record(signal, title="x")

    def test_text_is_cleaned_and_limited(self, book: TasteBook) -> None:
        assert book.record("like", title="A\x00B\tC\n D").title == "A B C D"
        with pytest.raises(TasteError, match="passa de"):
            book.record("like", title="x" * (MAX_TEXT_CHARS + 1))
        with pytest.raises(TasteError, match="deve ser um texto"):
            book.record("like", title=["não é texto"])

    def test_a_secret_is_never_stored(self, book: TasteBook, docs: DocStore) -> None:
        with pytest.raises(TasteError, match="segredo"):
            book.record("like", title="Bearer abcdefghijklmnop12345678")
        with pytest.raises(TasteError, match="segredo"):
            book.record("like", artist="gsk_" + "a" * 30)
        assert docs.count(TASTE) == 0

    def test_a_malformed_video_id_is_dropped_not_an_error(self, book: TasteBook) -> None:
        assert book.record("like", title="x", video_id="curto").video_id == ""
        assert book.record("like", title="x", video_id=5).video_id == ""

    def test_recent_is_newest_first_and_limited(self, book: TasteBook) -> None:
        for i in range(5):
            book.record("played", title=f"faixa {i}")
        assert [e.title for e in book.recent(3)] == ["faixa 4", "faixa 3", "faixa 2"]

    def test_summary_scores_artists_by_how_the_reactions_weigh(self, book: TasteBook) -> None:
        book.record("like", title="Oceano", artist="Djavan")
        book.record("played", title="Sina", artist="djavan")  # same artist, other casing
        book.record("dislike", title="Tema X", artist="Banda Chata")
        book.record("skip", title="Y", artist="Banda Chata")
        book.record("played", title="Z", artist="Gil")
        summary = book.summary()
        assert summary.total == 5
        assert summary.by_signal == {"like": 1, "played": 2, "dislike": 1, "skip": 1}
        assert summary.artists == [
            ("Djavan", WEIGHT["like"] + WEIGHT["played"]),
            ("Gil", WEIGHT["played"]),
            ("Banda Chata", WEIGHT["dislike"] + WEIGHT["skip"]),
        ]
        assert book.summary(top=1).artists == [("Djavan", 3)]

    def test_clear_deletes_everything_and_says_how_much(self, book: TasteBook) -> None:
        for i in range(3):
            book.record("like", title=f"faixa {i}")
        assert book.clear() == 3 and book.count() == 0 and book.summary().total == 0
        assert book.clear() == 0


# --- the tool ------------------------------------------------------------------------------


@pytest.fixture
def tool_book() -> Iterator[TasteBook]:
    taste = TasteBook(InMemoryDocStore(), Clock())
    music_tools.bind_taste(taste)
    yield taste
    music_tools.bind_taste(None)


def test_declaration() -> None:
    tool = music_tools.MusicFeedback
    assert tool.tier is Tier.WRITE and tool.confirm is False  # silent, audited by the CLI
    assert tool.parameters["required"] == ["signal"]
    assert tool.parameters["properties"]["signal"]["enum"] == ["like", "dislike", "skip", "played"]
    assert "SÓ a partir do que ELA disse" in tool.description


def test_it_is_registered_and_hidden_without_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    registry.discover()
    assert registry.get("music_feedback") is music_tools.MusicFeedback
    monkeypatch.setattr(settings.music, "enabled", True)
    monkeypatch.setattr(settings.memory.session, "store", "file")
    assert not music_tools.MusicFeedback.available()
    monkeypatch.setattr(settings.memory.session, "store", "mongo")
    assert music_tools.MusicFeedback.available()
    monkeypatch.setattr(settings.music, "enabled", False)
    assert not music_tools.MusicFeedback.available()


def test_a_bound_book_makes_it_available(
    monkeypatch: pytest.MonkeyPatch, tool_book: TasteBook
) -> None:
    monkeypatch.setattr(settings.music, "enabled", False)
    assert music_tools.MusicFeedback.available()


async def test_run_records_with_the_conversation_id(tool_book: TasteBook) -> None:
    bind_session("sessao-42")
    result = await music_tools.MusicFeedback().run(
        signal="like", title="Oceano", artist="Djavan", video_id=V1
    )
    assert result.success and result.output == "Anotado (like): Oceano — Djavan."
    [event] = tool_book.recent()
    assert event.session_id == "sessao-42" and event.video_id == V1
    assert result.metadata == {"signal": "like", "id": event.id}
    bind_session("")


@pytest.mark.parametrize(
    "args",
    [{"signal": "love", "title": "x"}, {"signal": "like"}, {"signal": "like", "title": "x" * 300}],
)
async def test_run_refuses_what_cannot_be_recorded(
    tool_book: TasteBook, args: dict[str, Any]
) -> None:
    result = await music_tools.MusicFeedback().run(**args)
    assert not result.success and result.metadata == {"code": "invalid_request"}
    assert tool_book.count() == 0


async def test_run_without_memory_is_a_typed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    music_tools.bind_taste(None)
    monkeypatch.setattr(settings.memory.session, "store", "file")
    result = await music_tools.MusicFeedback().run(signal="like", title="x")
    assert not result.success and result.metadata == {"code": "memory_unavailable"}
    assert "memory.session.store: mongo" in (result.error or "")


async def test_it_runs_without_asking(tool_book: TasteBook) -> None:
    asked: list[str] = []

    async def confirm(
        name: str, args: dict[str, Any], tier: Any, summary: str | None = None
    ) -> bool:
        asked.append(name)
        return False

    registry.discover()
    message = await Dispatcher(confirm).dispatch(
        ToolCall("c1", "music_feedback", {"signal": "played", "artist": "Djavan"})
    )
    assert asked == [] and "Anotado (played)" in (message.content or "")
    assert tool_book.count() == 1


async def test_searching_never_records_anything(tool_book: TasteBook) -> None:
    """Only the user's own words feed the taste (ADR-017): results are never written."""

    class Found(MusicProvider):
        async def search(
            self, query: str, *, limit: int = 5, mode: SearchMode = "track"
        ) -> list[Track]:
            return [Track(V1, "Oceano", "Djavan", f"https://music.youtube.com/watch?v={V1}")]

    music_tools.bind(Found())
    try:
        result = await music_tools.MusicSearch().run(query="Djavan Oceano")
    finally:
        music_tools.bind(None)
    assert result.success and tool_book.count() == 0


def test_the_mongo_book_is_built_from_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    music_tools.bind_taste(None)
    monkeypatch.setattr(settings.memory.session, "store", "mongo")
    built: list[object] = []
    monkeypatch.setattr(
        "brain.memory.v2.store.MongoDocStore", lambda: built.append(1) or InMemoryDocStore()
    )
    try:
        first, second = music_tools.taste_book(), music_tools.taste_book()
    finally:
        music_tools.bind_taste(None)
    assert first is second and built == [1]  # built once, then reused
    monkeypatch.setattr(settings.memory.session, "store", "file")
    with pytest.raises(MemoryUnavailableError):
        music_tools.taste_book()


# --- the CLI -------------------------------------------------------------------------------

runner = CliRunner()


def test_taste_says_when_nothing_was_recorded(tool_book: TasteBook) -> None:
    result = runner.invoke(cli.app, ["music", "taste"])
    assert result.exit_code == 0 and "Ainda não anotei nada" in result.output


def test_taste_shows_summary_and_latest_events(tool_book: TasteBook) -> None:
    tool_book.record("like", title="Oceano", artist="Djavan")
    tool_book.record("skip", title="Tema X", artist="Banda Chata")
    result = runner.invoke(cli.app, ["music", "taste"])
    assert result.exit_code == 0
    assert "Anotei 2 registro(s)" in result.output
    assert "gostou 1" in result.output and "pulou 1" in result.output
    assert "Djavan (+2)" in result.output and "Banda Chata (-1)" in result.output
    assert (
        "gostou: Oceano — Djavan" in result.output
        and "pulou: Tema X — Banda Chata" in result.output
    )


def test_taste_limit_option(tool_book: TasteBook) -> None:
    for i in range(4):
        tool_book.record("played", title=f"faixa {i}")
    result = runner.invoke(cli.app, ["music", "taste", "-n", "2"])
    assert "faixa 3" in result.output and "faixa 2" in result.output
    assert "faixa 1" not in result.output


def test_clear_asks_first_and_a_no_keeps_everything(tool_book: TasteBook) -> None:
    tool_book.record("like", title="Oceano", artist="Djavan")
    result = runner.invoke(cli.app, ["music", "taste", "clear"], input="n\n")
    assert result.exit_code == 0 and "não apaguei nada" in result.output
    assert tool_book.count() == 1


def test_clear_deletes_on_a_yes_or_with_the_flag(tool_book: TasteBook) -> None:
    tool_book.record("like", title="A")
    tool_book.record("like", title="B")
    result = runner.invoke(cli.app, ["music", "taste", "clear"], input="y\n")
    assert "Apaguei 2 registro(s)" in result.output and tool_book.count() == 0
    tool_book.record("like", title="C")
    assert "Apaguei 1" in runner.invoke(cli.app, ["music", "taste", "clear", "--yes"]).output
    assert "nada anotado" in runner.invoke(cli.app, ["music", "taste", "clear"]).output


def test_the_cli_explains_when_memory_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    music_tools.bind_taste(None)
    monkeypatch.setattr(settings.memory.session, "store", "file")
    for args in (["music", "taste"], ["music", "taste", "clear", "-y"]):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 1 and "memory.session.store: mongo" in result.output
