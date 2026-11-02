"""Aceite da Memória V3 (ADR-029, 030 e 031), de ponta a ponta com modelos roteirizados.

Uma conversa irritante muda o humor; o humor muda a ordem de tom do prompt (e só ela); a noite
estuda pela web, forma uma crença sobre o mundo com dois sites, move a personalidade, forma uma
crença sobre ela mesma e conta tudo no diário; o dashboard mostra o caminho. Nada aqui chama um
modelo ou a rede de verdade.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from brain.config import MemoryV2Settings
from brain.core.session import Turn
from brain.dashboard.routes import build_api
from brain.dashboard.sources import Sources
from brain.eventlog import bind_session
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.context import V2Context
from brain.memory.v2.live import LiveReactor
from brain.memory.v2.models import Evidence, Interest, TranscriptTurn, Watermark
from brain.memory.v2.night import NightRunner, default_phases
from brain.memory.v2.persona import PersonaBook
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.tools.base import Tier
from brain.tools.knowledge import LearnTopic
from brain.web.fetch import Fetcher
from brain.web.search import MonthlyQuota, SearchResult, SearchRouter
from tests.test_extraction import FakeEmbedder

DAY = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
NIGHT = DAY + timedelta(hours=15)
TEXT = "Texto da página sobre jazz, com bastante conteúdo útil para a nota. " * 10


class Script(LLMProvider):
    """One model that plays the live judge, the web extraction, the study and the personality."""

    name: ClassVar[str] = "script"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        system = messages[0].content or ""
        self.calls.append(system[:40])
        if system.startswith("Você é a Juniper, sentindo em tempo real"):
            out: Any = {
                "reaction": {
                    "kind": "irritação", "concern": "bem-estar-do-petrus", "effect": -0.9,
                    "novelty": 1.0, "about": "self", "grievance": False,
                }
            }  # fmt: skip
        elif system.startswith("Você é a Juniper, aprendendo agora"):
            out = {"notes": [{"text": "O jazz nasceu em Nova Orleans.", "volatility": "estavel"}]}
        elif system.startswith("Você é a Juniper, estudando"):
            out = {
                "appeal": 0.8,
                "reasons": ["li que o improviso é rico"],
                "summary": "li sobre jazz.",
                "notes": [],
                "questions": ["quem foram os pioneiros?"],
            }
        elif system.startswith("Você é a Juniper, relendo como você foi"):
            label = "S1:t0"
            out = {"conversas": [{"conversa": "S1", "estados": [
                {"traco": "assertividade", "valor": 0.95, "evidencia": [label], "reacao": 0},
                {"traco": "entusiasmo", "valor": 0.1, "evidencia": [label], "reacao": -1},
            ]}], "marcou": ["Fiquei seca com ele."]}  # fmt: skip
        elif system.startswith("Você é a Juniper, notando"):
            out = {"interests": []}
        else:
            out = {}
        return LLMResponse(Message("assistant", json.dumps(out)), model="m", provider="s")

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


class Engine:
    name = "fake"

    def usable(self) -> bool:
        return True

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        return [SearchResult(d, f"https://{d}/jazz", "trecho", TEXT) for d in ("a.org", "b.org")]


@pytest.fixture
async def world(tmp_path: Path) -> Any:
    class W:
        data = V2Data.over(InMemoryDocStore())
        llm = Script()
        web = (SearchRouter([(Engine(), 0)], MonthlyQuota(tmp_path / "q.json")), Fetcher())
        clock = [DAY]

        def now(self) -> datetime:
            return self.clock[0]

    return W()


async def test_a_provocation_changes_the_mood_the_mood_changes_the_voice_and_only_the_voice(
    world: Any,
) -> None:
    bind_session("sessao-v3")
    config = MemoryV2Settings(enabled=True)
    live = LiveReactor(world.data, world.llm, config=config, clock=world.now)
    context = V2Context(world.data, FakeEmbedder(), config=config, clock=world.now)

    before = dict(await context.preview("bom dia, tudo bem?"))
    assert "voz" not in before  # her usual self: no order of tone

    for index in range(2):
        assert await live.react("sessao-v3", Turn("você é uma inútil", "Poxa.", index=index))

    after = dict(await context.preview("bom dia, tudo bem?"))
    assert "hostil" in after["estado"] or "pra baixo" in after["estado"]
    assert "voz" in after and "secas" in after["voz"] and "sem emoji" in after["voz"]
    # the order is for the tone, and says so: the actions are not hers to bend
    assert "nunca a escolha de ferramentas" in after["voz"]
    assert "sinal" not in after  # nothing is held back from her: no gag, no welcome mode


async def test_the_night_studies_the_web_forms_world_and_self_beliefs_and_tells_how_she_changed(
    world: Any,
) -> None:
    data = world.data
    PersonaBook(data, clock=lambda: DAY - timedelta(days=1)).ensure()
    interest = Interest(
        "jazz", stance="like", intensity=0.8, origin=Evidence("observation", "s", 0, at=DAY)
    )
    interest.created_at = interest.updated_at = DAY - timedelta(days=2)
    data.interests.save(interest)
    data.transcripts.save(TranscriptTurn("sessA", 0, "me conta de jazz", "Conto sim.", at=DAY))
    data.watermarks.save(Watermark("sessA", 0, 1, DAY))
    book = AffectBook(data, clock=lambda: NIGHT)
    for hours in (1, 20, 30):  # the same feeling on more than one day
        book.record(
            "irritação", -0.6, 0.7, ["turn:sessA:0"], occurred_at=NIGHT - timedelta(hours=hours)
        )

    runner = NightRunner(
        data,
        config=MemoryV2Settings(enabled=True),
        phases=default_phases(),
        cheap=world.llm,
        reflect=world.llm,
        embedder=FakeEmbedder(),
        web=world.web,
        clock=lambda: NIGHT,
    )
    report = await runner.run()

    # NREM: the web taught, two sites agreed, so it became a belief about the world
    (note,) = data.notes.list({"kind": "afirmacao"})
    assert (
        note.source == "web"
        and note.uncertainty == "baixa"
        and set(note.domains) == {"a.org", "b.org"}
    )
    beliefs = {b.about: b for b in data.beliefs.list()}
    assert beliefs["world"].statement == "O jazz nasceu em Nova Orleans."
    assert {e.domain for e in beliefs["world"].evidence_for} == {"a.org", "b.org"}
    # REM: the states became traits and her own history became self-knowledge
    state = data.persona.current()
    assert state is not None and state.traits["assertividade"].value > 0.7
    assert state.traits["entusiasmo"].value < 0.6 and state.recent == ["Fiquei seca com ele."]
    assert any("irritação" in b.statement for b in data.beliefs.list({"about": "self"}))
    # and the diary says it
    assert report.journal is not None and "Como mudei:" in report.journal
    assert report.run is not None and report.run.status == "done"
    assert [r.name for r in report.results][-3:] == ["refine", "rem", "prune"]


async def test_the_dashboard_shows_the_road_she_took(world: Any) -> None:
    await test_the_night_studies_the_web_forms_world_and_self_beliefs_and_tells_how_she_changed(
        world
    )
    api = build_api(Sources(data=world.data, transcripts=True))

    async def get(path: str, **params: Any) -> Any:
        status, body = await api.handle(path, {k: [str(v)] for k, v in params.items()})
        assert status == 200, (path, body)
        return body

    persona = await get("/persona")
    assert len(persona["history"]) >= 2 and "baselines" in persona["history"][-1]
    why = await get("/evolution/why", version=persona["state"]["version"])
    assert why["recent"] == ["Fiquei seca com ele."]
    layers = {layer: await get("/beliefs", about=layer) for layer in ("self", "world")}
    assert layers["world"]["items"] and layers["self"]["items"]
    detail = await get(f"/beliefs/{layers['world']['items'][0]['id']}")
    assert detail["history"] and detail["domains"] == ["a.org", "b.org"]
    assert (await get("/voice"))["active"]


def test_fala_livre_acao_protegida_the_tools_keep_their_tiers() -> None:
    """ADR-029: free speech, protected action. Learning writes silently (like `remember`), reading
    the web asks nothing; nothing here widens what a tool may do."""
    assert LearnTopic.tier is Tier.WRITE and LearnTopic.confirm is False
    from brain.tools.web import WebFetch, WebSearch

    assert WebSearch.tier is Tier.READ and WebFetch.tier is Tier.READ


async def test_the_web_only_ever_reaches_her_notes(world: Any) -> None:
    """ADR-017 principle 5, revised: a page can teach a note, never a feeling about the user."""
    await test_the_night_studies_the_web_forms_world_and_self_beliefs_and_tells_how_she_changed(
        world
    )
    assert all(b.about in ("world", "self") for b in world.data.beliefs.list())
    assert not any("a.org" in a.kind for a in world.data.affects.list())


def test_httpx_is_the_only_network_door_of_the_web_module() -> None:
    import brain.web.fetch as fetch
    import brain.web.search as search

    assert fetch.httpx is httpx and search.httpx is httpx
