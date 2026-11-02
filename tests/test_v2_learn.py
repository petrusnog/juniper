"""A memória fala, a web ensina: notas da web, learn_topic e o modo estrito (T-182, ADR-031)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from brain.config import MemoryV2Settings, V2KnowledgeSettings
from brain.eventlog import bind_session
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.v2.context import (
    KNOWLEDGE_OPEN,
    NO_NOTES,
    STRICT_KNOWLEDGE_HEADER,
    V2Context,
)
from brain.memory.v2.knowledge import NoteBook, expired
from brain.memory.v2.learn import Learner, LearnError
from brain.memory.v2.models import Interest, StudyNote, V2DataError
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.tools import knowledge as knowledge_tools
from brain.tools.base import Tier
from brain.tools.knowledge import LearnTopic
from brain.web.fetch import FetchError, Page
from brain.web.search import MonthlyQuota, SearchResult, SearchRouter
from tests.test_extraction import FakeEmbedder

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
LONG = "Texto extraído da página sobre o assunto. " * 20


class Teacher(LLMProvider):
    """Answers each source with ``fn(user_text)`` (a dict becomes JSON)."""

    name: ClassVar[str] = "teacher"

    def __init__(self, fn: Callable[[str], Any] | None = None, error: Exception | None = None):
        self.fn = fn or (lambda user: {"notes": [{"text": "HTTP/3 usa QUIC."}]})
        self.error = error
        self.users: list[str] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        assert (messages[0].content or "").startswith("Você é a Juniper, aprendendo agora")
        user = messages[1].content or ""
        self.users.append(user)
        if self.error is not None:
            raise self.error
        out = self.fn(user)
        return LLMResponse(Message("assistant", json.dumps(out)), model="m", provider="t")

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


class Engine:
    """A search provider with fixed results."""

    name = "fake"

    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    def usable(self) -> bool:
        return True

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        self.queries.append(query)
        return self.results[:limit]


class Pages:
    def __init__(self, text: str = LONG, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.urls: list[str] = []

    async def fetch(self, url: str) -> Page:
        self.urls.append(url)
        if self.fail:
            raise FetchError("network", "falhou")
        return Page(url, "T", self.text, False)


def hit(domain: str, content: str | None = LONG) -> SearchResult:
    return SearchResult(f"sobre {domain}", f"https://{domain}/pagina", "trecho", content)


def learner(
    tmp_path: Path,
    results: list[SearchResult],
    llm: Teacher | None = None,
    pages: Pages | None = None,
    data: V2Data | None = None,
    clock: Callable[[], datetime] = lambda: NOW,
) -> tuple[Learner, V2Data, Engine, Teacher, Pages]:
    data = data or V2Data.over(InMemoryDocStore())
    engine = Engine(results)
    router = SearchRouter([(engine, 0)], MonthlyQuota(tmp_path / "q.json"))
    llm = llm or Teacher()
    pages = pages or Pages()
    config = MemoryV2Settings(enabled=True)
    made = Learner(
        data,
        llm,
        router,
        pages,
        embedder=FakeEmbedder(),
        config=config,
        clock=clock,  # type: ignore[arg-type]
    )
    return made, data, engine, llm, pages


# --- the notes -----------------------------------------------------------------------------------


def test_a_web_note_needs_its_url_and_keeps_its_metadata() -> None:
    with pytest.raises(V2DataError):
        StudyNote("i", "x", source="web").to_doc()
    note = StudyNote(
        "i",
        "x",
        source="web",
        url="https://a.org",
        domains=["a.org"],
        fetched_at=NOW,
        volatility="atual",
    )
    again = StudyNote.from_doc(note.to_doc())
    assert again.url == "https://a.org" and again.domains == ["a.org"]
    assert again.fetched_at == NOW and again.volatility == "atual"


def test_old_notes_without_the_new_fields_still_load() -> None:
    doc = StudyNote("i", "x").to_doc()
    for key in ("url", "domains", "fetched_at", "volatility"):
        doc.pop(key)
    note = StudyNote.from_doc(doc)
    assert note.source == "modelo" and note.volatility == "estavel" and note.fetched_at is None


@pytest.mark.parametrize(
    ("volatility", "age", "gone"),
    [
        ("noticia", timedelta(hours=11), False),
        ("noticia", timedelta(hours=13), True),
        ("atual", timedelta(days=6), False),
        ("atual", timedelta(days=8), True),
        ("estavel", timedelta(days=179), False),
        ("estavel", timedelta(days=181), True),
    ],
)
def test_a_web_note_expires_by_its_volatility(volatility: str, age: timedelta, gone: bool) -> None:
    note = StudyNote(
        "i", "x", source="web", url="https://a.org", fetched_at=NOW, volatility=volatility
    )
    assert expired(note, NOW + age) is gone


def test_notes_that_are_not_from_the_web_never_expire() -> None:
    assert not expired(StudyNote("i", "x", source="documento"), NOW + timedelta(days=9999))


def test_the_same_claim_from_a_second_site_is_corroborated_and_firmer() -> None:
    data = V2Data.over(InMemoryDocStore())
    interest = Interest("http")
    data.interests.save(interest)
    book = NoteBook(data, FakeEmbedder(), clock=lambda: NOW)
    first = book.add_claim(
        interest, "HTTP/3 usa QUIC.", "média", 1, "web", url="https://a.org/x", domain="a.org"
    )
    assert first is not None and first.uncertainty == "média"
    assert (
        book.add_claim(
            interest, "HTTP/3 usa QUIC.", "média", 1, "web", url="https://b.org/y", domain="b.org"
        )
        is None
    )
    (note,) = book.claims(interest.id)
    assert note.domains == ["a.org", "b.org"] and note.uncertainty == "baixa"


def test_the_same_site_twice_does_not_corroborate() -> None:
    data = V2Data.over(InMemoryDocStore())
    interest = Interest("http")
    data.interests.save(interest)
    book = NoteBook(data, FakeEmbedder(), clock=lambda: NOW)
    for _ in range(2):
        book.add_claim(
            interest, "HTTP/3 usa QUIC.", "média", 1, "web", url="https://a.org", domain="a.org"
        )
    (note,) = book.claims(interest.id)
    assert note.domains == ["a.org"] and note.uncertainty == "média"


# --- learning ------------------------------------------------------------------------------------


async def test_learning_writes_web_notes_with_their_source_and_returns_them(tmp_path: Path) -> None:
    made, data, engine, llm, pages = learner(tmp_path, [hit("a.org")])

    report = await made.learn("protocolo HTTP/3", ["o que muda?"])

    assert engine.queries == ["protocolo HTTP/3 o que muda?"] and pages.urls == []  # Tavily text
    (note,) = data.notes.list({"kind": "afirmacao"})
    assert note.source == "web" and note.url == "https://a.org/pagina"
    assert note.domains == ["a.org"] and note.fetched_at == NOW and note.uncertainty == "média"
    assert report.added == 1 and report.sources == ["a.org"] and report.provider == "fake"
    assert "HTTP/3 usa QUIC." in report.render() and "uma fonte só" in report.render()
    assert data.interests.list()[0].topic == "protocolo HTTP/3"  # it joins her study tree


async def test_a_thin_extract_makes_her_read_the_page(tmp_path: Path) -> None:
    made, _, _, llm, pages = learner(tmp_path, [hit("a.org", content="curto")])
    await made.learn("http")
    assert pages.urls == ["https://a.org/pagina"] and LONG.strip()[:30] in llm.users[0]


async def test_a_page_that_cannot_be_read_is_skipped(tmp_path: Path) -> None:
    made, _, _, llm, _ = learner(
        tmp_path, [hit("a.org", content=None), hit("b.org")], pages=Pages(fail=True)
    )
    report = await made.learn("http")
    assert report.sources == ["b.org"] and len(llm.users) == 1


async def test_one_source_per_domain_up_to_the_limit(tmp_path: Path) -> None:
    results = [hit("a.org"), hit("a.org"), hit("b.org"), hit("c.org"), hit("d.org")]
    made, _, _, llm, _ = learner(tmp_path, results)
    report = await made.learn("http")
    assert report.sources == ["a.org", "b.org", "c.org"] and len(llm.users) == 3


async def test_two_sites_saying_the_same_make_the_note_firm(tmp_path: Path) -> None:
    made, data, *_ = learner(tmp_path, [hit("a.org"), hit("b.org")])
    report = await made.learn("http")
    (note,) = data.notes.list({"kind": "afirmacao"})
    assert note.uncertainty == "baixa" and note.domains == ["a.org", "b.org"]
    assert report.corroborated == 1 and "(uma fonte só)" not in report.render()


async def test_a_contradiction_makes_both_claims_uncertain(tmp_path: Path) -> None:
    answers = iter(
        [
            {"notes": [{"text": "A versão atual é a 3."}]},
            {"notes": [{"text": "A versão atual é a 4.", "volatility": "atual", "contradiz": 1}]},
        ]
    )
    made, data, *_ = learner(
        tmp_path, [hit("a.org"), hit("b.org")], llm=Teacher(lambda u: next(answers))
    )
    report = await made.learn("software x")
    notes = data.notes.list({"kind": "afirmacao"})
    assert {n.uncertainty for n in notes} == {"alta"} and report.contradictions == 1
    assert {n.volatility for n in notes} == {"estavel", "atual"}


async def test_junk_and_personal_claims_are_turned_down(tmp_path: Path) -> None:
    llm = Teacher(
        lambda u: {
            "notes": [
                "ok, uma afirmação",
                {"text": "a chave é sk-abcdefghijklmnopqrstuvwx"},
                {"sem": "texto"},
                42,
            ]
        }
    )
    made, data, *_ = learner(tmp_path, [hit("a.org")], llm=llm)
    report = await made.learn("http")
    texts = [n.text for n in data.notes.list({"kind": "afirmacao"})]
    assert texts == ["ok, uma afirmação"] and report.rejected == 1


async def test_a_model_failure_loses_one_source_and_never_raises(tmp_path: Path) -> None:
    made, data, *_ = learner(tmp_path, [hit("a.org")], llm=Teacher(error=RuntimeError("caiu")))
    report = await made.learn("http")
    assert report.added == 0 and data.notes.count() == 0
    assert "não consegui aprender" in report.render()


async def test_a_personal_or_empty_topic_is_refused(tmp_path: Path) -> None:
    made, *_ = learner(tmp_path, [hit("a.org")])
    with pytest.raises(LearnError):
        await made.learn("   ")


async def test_secrets_in_a_page_never_reach_the_model(tmp_path: Path) -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    made, _, _, llm, _ = learner(tmp_path, [hit("a.org", content=LONG + f" chave {secret}")])
    await made.learn("http")
    assert secret not in llm.users[0]


async def test_the_page_text_is_cut(tmp_path: Path) -> None:
    made, _, _, llm, _ = learner(tmp_path, [hit("a.org", content="x" * 50_000)])
    await made.learn("http")
    assert llm.users[0].count("x") <= 6000 + 50


# --- the tool ------------------------------------------------------------------------------------


def test_learn_topic_writes_silently_like_remember() -> None:
    assert LearnTopic.tier is Tier.WRITE and LearnTopic.confirm is False


async def test_the_tool_is_hidden_without_a_learner_and_answers_with_the_notes(
    tmp_path: Path,
) -> None:
    knowledge_tools.bind(None)
    assert not LearnTopic.available()
    made, *_ = learner(tmp_path, [hit("a.org")])
    knowledge_tools.bind(made)
    try:
        assert LearnTopic.available()
        result = await LearnTopic().run(topic="http", questions=["o que é?"])
        refused = await LearnTopic().run(topic="")
    finally:
        knowledge_tools.bind(None)
    assert result.success and "HTTP/3 usa QUIC." in (result.output or "")
    assert result.metadata and result.metadata["added"] == 1
    assert not refused.success and refused.metadata == {"code": "topic"}


# --- strict knowledge in the conversation --------------------------------------------------------


def context(
    data: V2Data, strict: bool = True, clock: Callable[[], datetime] = lambda: NOW
) -> V2Context:
    bind_session("sessao-learn")
    config = MemoryV2Settings(enabled=True, knowledge=V2KnowledgeSettings(strict=strict))
    return V2Context(data, FakeEmbedder(), config=config, clock=clock)


async def test_strict_mode_brings_the_rule_even_without_notes() -> None:
    data = V2Data.over(InMemoryDocStore())
    sections = dict(await context(data).preview("qual a capital da Mongólia?"))
    block = sections["conhecimento"]
    assert STRICT_KNOWLEDGE_HEADER in block and NO_NOTES in block and "learn_topic" in block


async def test_without_strict_mode_and_without_notes_there_is_no_block() -> None:
    data = V2Data.over(InMemoryDocStore())
    sections = dict(await context(data, strict=False).preview("qual a capital da Mongólia?"))
    assert "conhecimento" not in sections


async def test_web_notes_show_where_she_read_them_and_expired_ones_do_not_count(
    tmp_path: Path,
) -> None:
    made, data, *_ = learner(tmp_path, [hit("a.org")])
    await made.learn("protocolo HTTP/3 QUIC")
    fresh = dict(await context(data).preview("HTTP/3 usa QUIC"))["conhecimento"]
    assert KNOWLEDGE_OPEN in fresh and "HTTP/3 usa QUIC." in fresh and "(li em a.org)" in fresh
    later = dict(
        await context(data, clock=lambda: NOW + timedelta(days=400)).preview("HTTP/3 usa QUIC")
    )["conhecimento"]
    assert "HTTP/3 usa QUIC." not in later and NO_NOTES in later


# --- everything she reads on the web becomes study notes -----------------------------------------


@pytest.fixture
def bound(tmp_path: Path):
    """The web tools over a real ``Learner`` (in memory), restored afterwards."""
    from brain.tools import web

    made, data, engine, llm, pages = learner(tmp_path, [hit("a.com"), hit("b.org")])
    router = SearchRouter([(engine, 0)], MonthlyQuota(tmp_path / "q2.json"))
    knowledge_tools.bind(made)
    web.bind(router, pages)  # type: ignore[arg-type]
    yield data, web
    knowledge_tools.bind(None)
    web.bind(None)


async def test_web_search_files_what_it_read_under_the_topic_she_names(bound) -> None:
    data, web = bound
    out = await web.WebSearch().run(
        query="notícia recente sobre o Ibovespa hoje", topic="bolsa de valores"
    )
    assert out.success and out.metadata["learned"] is True
    assert [i.topic for i in data.interests.list()] == ["bolsa de valores"]
    notes = data.notes.list({"source": "web"})
    assert notes and all(n.url and n.domains for n in notes)


async def test_a_topic_she_already_studies_is_fed_not_duplicated(bound) -> None:
    data, web = bound
    await web.WebSearch().run(query="Donald Trump", topic="Donald Trump")
    first = len(data.notes.list({"source": "web"}))
    await web.WebSearch().run(query="Trump discurso hoje", topic="Donald Trump")
    assert [i.topic for i in data.interests.list()] == ["Donald Trump"]
    assert first >= 1


async def test_web_fetch_files_the_page_under_its_topic_too(bound) -> None:
    data, web = bound
    out = await web.WebFetch().run(url="https://a.com/x", topic="HTTP/3")
    assert out.success and out.metadata["learned"] is True
    assert [i.topic for i in data.interests.list()] == ["HTTP/3"]
    assert data.notes.list({"source": "web"})


async def test_without_a_topic_the_query_or_the_page_title_names_it(bound) -> None:
    data, web = bound
    await web.WebSearch().run(query="protocolo QUIC")
    await web.WebFetch().run(url="https://a.com/y")
    assert {i.topic for i in data.interests.list()} == {"protocolo QUIC", "T"}
