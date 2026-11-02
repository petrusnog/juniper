"""A semana simulada (ADR-030, seção 12): a personalidade muda de forma mensurável e explicável.

Sete noites de conversas, com o roteiro dos estados que ela expressou. Um roteiro marcado tem de
mover os aspectos visados, na direção esperada e dentro dos tetos; um roteiro neutro não pode mover
nada além do ruído; e cada passo tem de apontar para uma conversa real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from brain.config import MemoryV2Settings, V2PersonaSettings
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.v2.evolve import PersonaPhase
from brain.memory.v2.models import TranscriptTurn
from brain.memory.v2.night import Budget, PhaseContext, SessionMaterial
from brain.memory.v2.persona import PersonaBook
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder

START = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
NOISE = 0.005


class Script(LLMProvider):
    """Rates every conversation of the night with a fixed script of states."""

    name: ClassVar[str] = "script"

    def __init__(self, states: dict[str, float]) -> None:
        self.states = states

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        rated = [
            {"traco": trait, "valor": value, "evidencia": ["S1:t0"], "reacao": 0}
            for trait, value in self.states.items()
        ]
        reply = {"conversas": [{"conversa": "S1", "estados": rated}] if rated else [], "marcou": []}
        return LLMResponse(Message("assistant", json.dumps(reply)), model="m", provider="s")

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


async def week(states: dict[str, float]) -> tuple[PersonaBook, dict[str, float]]:
    data = V2Data.over(InMemoryDocStore())
    config = V2PersonaSettings()
    PersonaBook(data, config, lambda: START).ensure()
    before = {n: t.value for n, t in data.persona.current().traits.items()}  # type: ignore[union-attr]
    llm = Script(states)
    for day in range(1, 8):
        now = START + timedelta(days=day)
        turns = [TranscriptTurn(f"s{day}", 0, "conversa do dia", "resposta", at=now)]
        ctx = PhaseContext(
            data=data,
            config=MemoryV2Settings(enabled=True, persona=config),
            material=[SessionMaterial(f"s{day}", turns)],
            budget=Budget(10**9, 10**6),
            cheap=llm,
            reflect=llm,
            embedder=FakeEmbedder(),
            clock=lambda now=now: now,
        )
        await PersonaPhase().run(ctx)
    return PersonaBook(data, config, lambda: START + timedelta(days=8)), before


async def test_a_week_of_jokes_and_warmth_raises_those_aspects_and_nothing_else() -> None:
    book, before = await week({"entusiasmo": 1.0, "compaixão": 1.0, "ironia": 0.0})
    now = book.current().traits  # type: ignore[union-attr]
    assert now["entusiasmo"].value - before["entusiasmo"] >= 0.05
    assert now["compaixão"].value - before["compaixão"] >= 0.01
    assert before["ironia"] - now["ironia"].value >= 0.05
    for untouched in ("ordem", "abertura", "diligência", "polidez"):
        assert abs(now[untouched].value - before[untouched]) <= NOISE  # nothing else moved
    for trait in now.values():  # within every ceiling
        assert (
            abs(trait.value - trait.baseline) <= 0.25 + 1e-9
            and trait.min <= trait.value <= trait.max
        )
    assert book.current().version >= 5  # type: ignore[union-attr]


async def test_a_neutral_week_does_not_move_anything_beyond_the_noise() -> None:
    book, before = await week({})
    now = book.current().traits  # type: ignore[union-attr]
    assert all(abs(now[n].value - v) <= NOISE for n, v in before.items())


async def test_a_week_that_stays_where_she_already_is_changes_nothing() -> None:
    base = {t.name: t.baseline for t in V2PersonaSettings().traits}
    book, before = await week({"entusiasmo": base["entusiasmo"], "ironia": base["ironia"]})
    now = book.current().traits  # type: ignore[union-attr]
    assert all(abs(now[n].value - v) <= NOISE for n, v in before.items())


async def test_every_step_points_to_a_real_conversation() -> None:
    book, _ = await week({"entusiasmo": 1.0})
    data = book.data
    for version in data.persona.history.list():
        for event in version.state.changed_by:
            assert event.startswith("turn:s") and event.endswith(":0")
