"""O sonho estuda pela web: as notas vêm das fontes, o modelo só julga o gosto (T-183, ADR-031)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx

from brain.config import MemoryV2Settings, V2InterestSettings, V2KnowledgeSettings, settings
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.v2.models import Evidence, Interest, TranscriptTurn
from brain.memory.v2.night import Budget, NightRunner, PhaseContext, SessionMaterial
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.memory.v2.study import READING_WEB, InterestSeedPhase, StudyPhase
from brain.web.fetch import Fetcher
from brain.web.search import MonthlyQuota, SearchResult, SearchRouter
from tests.test_extraction import FakeEmbedder

NOW = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
LONG = "Texto da página sobre o assunto, com bastante conteúdo útil. " * 12


class Dreamer(LLMProvider):
    """Plays both the extraction of a web page and the reflective study."""

    name: ClassVar[str] = "dreamer"

    def __init__(self, notes: list[str] | None = None) -> None:
        self.notes = notes or ["Jazz nasceu em Nova Orleans."]
        self.study: list[str] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        system = messages[0].content or ""
        if system.startswith("Você é a Juniper, aprendendo agora"):
            out: Any = {"notes": [{"text": n, "volatility": "estavel"} for n in self.notes]}
        elif system.startswith("Você é a Juniper, estudando"):
            self.study.append(system)
            out = {
                "appeal": 0.7,
                "reasons": ["li que o improviso é rico"],
                "summary": "li sobre jazz.",
                "notes": [{"text": "uma nota do modelo", "uncertainty": "baixa"}],
                "questions": ["quem foram os pioneiros?"],
            }
        else:
            raise AssertionError(system[:60])
        return LLMResponse(Message("assistant", json.dumps(out)), model="m", provider="d")

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


class Engine:
    name = "fake"

    def __init__(self, domains: list[str], fail: bool = False) -> None:
        self.domains = domains
        self.fail = fail
        self.queries: list[str] = []

    def usable(self) -> bool:
        return True

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        self.queries.append(query)
        if self.fail:
            raise httpx.ConnectError("fora do ar")
        return [SearchResult(d, f"https://{d}/p", "trecho", LONG) for d in self.domains]


def web_of(engine: Engine, tmp_path: Path) -> tuple[SearchRouter, Fetcher]:
    return SearchRouter([(engine, 0)], MonthlyQuota(tmp_path / "q.json")), Fetcher()


def ctx(
    data: V2Data,
    llm: Dreamer,
    web: Any,
    *,
    strict: bool = True,
    now: datetime = NOW,
) -> PhaseContext:
    return PhaseContext(
        data=data,
        config=MemoryV2Settings(
            enabled=True,
            interests=V2InterestSettings(),
            knowledge=V2KnowledgeSettings(strict=strict),
        ),
        material=[SessionMaterial("s", [TranscriptTurn("s", 4, "jazz", at=now)])],
        budget=Budget(10**9, 10**6),
        cheap=llm,
        reflect=llm,
        embedder=FakeEmbedder(),
        web=web,
        clock=lambda: now,
    )


def with_interest(data: V2Data) -> Interest:
    interest = Interest("jazz", origin=Evidence("observation", "s", 4, at=NOW - timedelta(days=3)))
    interest.created_at = interest.updated_at = NOW - timedelta(days=3)
    data.interests.save(interest)
    return interest


async def test_the_step_reads_the_web_and_the_notes_come_from_the_sources(tmp_path: Path) -> None:
    data = V2Data.over(InMemoryDocStore())
    interest = with_interest(data)
    llm = Dreamer()
    engine = Engine(["a.org", "b.org"])
    result = await StudyPhase().run(ctx(data, llm, web_of(engine, tmp_path)))

    notes = data.notes.list({"kind": "afirmacao"})
    assert [n.text for n in notes] == ["Jazz nasceu em Nova Orleans."]  # none from the model
    assert notes[0].source == "web" and set(notes[0].domains) == {"a.org", "b.org"}
    assert notes[0].uncertainty == "baixa"  # two domains agree
    assert data.interests.get(interest.id).level == 1  # type: ignore[union-attr]
    assert [q.text for q in data.notes.list({"kind": "pergunta"})] == ["quem foram os pioneiros?"]
    assert READING_WEB in llm.study[0] and "Jazz nasceu em Nova Orleans." in llm.study[0]
    assert (
        result.notes[0].startswith("leria na web sobre jazz")
        and "1 nota(s) nova(s)" in result.summary
    )


async def test_the_open_questions_of_the_last_step_guide_the_search(tmp_path: Path) -> None:
    data = V2Data.over(InMemoryDocStore())
    with_interest(data)
    engine = Engine(["a.org"])
    web = web_of(engine, tmp_path)
    await StudyPhase().run(ctx(data, Dreamer(), web))
    later = NOW + timedelta(days=2)
    await StudyPhase().run(ctx(data, Dreamer(["Outra nota."]), web, now=later))
    assert engine.queries[0] == "jazz" and "quem foram os pioneiros?" in engine.queries[1]


async def test_a_strict_memory_waits_when_the_web_fails_and_tries_again_tomorrow(
    tmp_path: Path,
) -> None:
    data = V2Data.over(InMemoryDocStore())
    interest = with_interest(data)
    llm = Dreamer()
    result = await StudyPhase().run(ctx(data, llm, web_of(Engine(["a.org"], fail=True), tmp_path)))
    assert data.interests.get(interest.id).level == 0  # type: ignore[union-attr]
    assert data.notes.count() == 0 and llm.study == []  # the model did not study alone
    assert "sem_web×1" in result.summary


async def test_a_lenient_memory_falls_back_to_the_model_when_the_web_fails(tmp_path: Path) -> None:
    data = V2Data.over(InMemoryDocStore())
    interest = with_interest(data)
    llm = Dreamer()
    web = web_of(Engine(["a.org"], fail=True), tmp_path)
    await StudyPhase().run(ctx(data, llm, web, strict=False))
    assert data.interests.get(interest.id).level == 1  # type: ignore[union-attr]
    assert [n.source for n in data.notes.list({"kind": "afirmacao"})] == ["modelo"]


async def test_the_nights_search_ceiling_is_respected(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(settings.web.dream, "max_searches", 1)
    data = V2Data.over(InMemoryDocStore())
    for name in ("jazz", "rock"):
        interest = Interest(
            name, origin=Evidence("observation", "s", 4, at=NOW - timedelta(days=3))
        )
        interest.created_at = interest.updated_at = NOW - timedelta(days=3)
        data.interests.save(interest)
    engine = Engine(["a.org"])
    result = await StudyPhase().run(ctx(data, Dreamer(), web_of(engine, tmp_path)))
    assert len(engine.queries) == 1 and "sem_busca×1" in result.summary
    assert sorted(i.level for i in data.interests.list()) == [0, 1]


async def test_without_a_web_the_study_is_the_old_reflection_on_what_the_model_knows() -> None:
    data = V2Data.over(InMemoryDocStore())
    interest = with_interest(data)
    llm = Dreamer()
    await StudyPhase().run(ctx(data, llm, None))
    assert [n.source for n in data.notes.list({"kind": "afirmacao"})] == ["modelo"]
    assert data.interests.get(interest.id).level == 1  # type: ignore[union-attr]
    assert READING_WEB not in llm.study[0]


async def test_the_dream_charges_the_web_reading_to_its_budget(tmp_path: Path) -> None:
    data = V2Data.over(InMemoryDocStore())
    with_interest(data)
    c = ctx(data, Dreamer(), web_of(Engine(["a.org", "b.org"]), tmp_path))
    await StudyPhase().run(c)
    assert c.budget.used_tokens >= 1600  # two extractions, 800 each at least


async def test_technical_topics_are_studied_now(tmp_path: Path) -> None:
    data = V2Data.over(InMemoryDocStore())
    llm = Dreamer()
    llm_seed = _seed_llm(["Docker"])
    result = await InterestSeedPhase().run(ctx(data, llm_seed, None))
    assert [i.topic for i in data.interests.list()] == [
        "Docker"
    ] and "tecnico" not in result.summary
    assert llm.study == []


def _seed_llm(topics: list[str]) -> Dreamer:
    class Seeder(Dreamer):
        async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
            out = {"interests": [{"topic": t, "evidence": ["S1:t4"]} for t in topics]}
            return LLMResponse(Message("assistant", json.dumps(out)), model="m", provider="d")

    return Seeder()


async def test_the_night_runner_hands_the_web_to_the_phases(tmp_path: Path) -> None:
    data = V2Data.over(InMemoryDocStore())
    with_interest(data)
    llm = Dreamer()
    runner = NightRunner(
        data,
        config=MemoryV2Settings(enabled=True),
        phases=[StudyPhase()],
        cheap=llm,
        reflect=llm,
        embedder=FakeEmbedder(),
        web=web_of(Engine(["a.org"]), tmp_path),
        clock=lambda: NOW,
    )
    await runner.run()
    assert [n.source for n in data.notes.list({"kind": "afirmacao"})] == ["web"]
