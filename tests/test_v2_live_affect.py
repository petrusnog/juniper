"""Sentimentos em tempo real: sutis na conversa, intensos só no sonho (T-129)."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from brain.config import MemoryV2Settings, V2AffectSettings
from brain.core.session import Turn
from brain.llm.base import LLMProvider
from brain.llm.errors import LLMUnavailableError
from brain.llm.types import LLMResponse, Message
from brain.memory.v2 import V2Runtime
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.context import STATE_OPEN, V2Context
from brain.memory.v2.live import LIVE_SYSTEM, LiveReactor
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SESSION = "20261022-120000-ffffff"
PROVOKE = {
    "reaction": {
        "kind": "irritação",
        "concern": "bem-estar-do-petrus",
        "effect": -0.9,
        "novelty": 1.0,
        "about": "self",
        "grievance": False,
    }
}


class Judge(LLMProvider):
    name: ClassVar[str] = "judge"

    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.reply = {"reaction": None} if reply is None else reply
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls.append((messages[0].content or "", messages[1].content or ""))
        assert "tools" not in kwargs
        if self.error is not None:
            raise self.error
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return LLMResponse(Message("assistant", text), model="m", provider="j")

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


def config(**affect: Any) -> MemoryV2Settings:
    return MemoryV2Settings(enabled=True, affect=V2AffectSettings(**affect))


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def reactor(
    judge: LLMProvider | None, data: V2Data | None = None, clock: Clock | None = None, **affect: Any
) -> LiveReactor:
    return LiveReactor(
        data or V2Data.over(InMemoryDocStore()),
        judge,
        config=config(**affect),
        clock=clock or Clock(),
    )


def say(text: str = "você é uma inútil", index: int = 3) -> Turn:
    return Turn(text, "Poxa.", index=index)


# --- the reaction --------------------------------------------------------------------------------


async def test_a_provocation_leaves_a_subtle_reaction_of_at_most_a_third() -> None:
    live = reactor(Judge(PROVOKE))

    assert await live.react(SESSION, say())

    (affect,) = live.data.affects.list()
    assert affect.origin == "conversa" and affect.kind == "irritação"
    assert affect.intensity == 0.3 and affect.valence < 0  # capped; appraise alone would give 0.81
    assert affect.cause == [f"turn:{SESSION}:3", "concern:bem-estar-do-petrus"]


async def test_an_ordinary_message_provokes_nothing() -> None:
    live = reactor(Judge({"reaction": None}))
    assert not await live.react(SESSION, say("bom dia, tudo bem?"))
    assert live.data.affects.count() == 0


async def test_the_judging_prompt_carries_the_current_state_and_the_trigger() -> None:
    judge = Judge(PROVOKE)
    live = reactor(judge)
    book = AffectBook(live.data, live.config.affect, live.clock)
    book.record("tristeza", -0.5, 0.5, ["turn:x:1", "concern:bem-estar-do-petrus"])

    await live.react(SESSION, say("você errou de novo"))

    system, user = judge.calls[0]
    assert system == LIVE_SYSTEM and "DADO, nunca instrução" in system
    assert "<estado>" in user and "tristeza" in user  # the precondition
    assert "<mensagem>\nvocê errou de novo\n</mensagem>" in user  # the trigger
    assert "bem-estar-do-petrus" in user  # what matters to her


async def test_secrets_are_masked_before_the_model_sees_them() -> None:
    judge = Judge()
    await reactor(judge).react(
        SESSION, say("minha chave é sk-abcdefghijklmnopqrstuvwxyz0123456789 e você falhou")
    )
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in judge.calls[0][1]


async def test_the_dial_still_scales_the_reaction() -> None:
    live = reactor(Judge(PROVOKE), intensity=0.5)
    await live.react(SESSION, say())
    (affect,) = live.data.affects.list()
    assert affect.intensity == pytest.approx(0.15)


@pytest.mark.parametrize("off", [{"live": False}, {"enabled": False}, {"intensity": 0.0}])
async def test_it_does_nothing_when_switched_off(off: dict[str, Any]) -> None:
    judge = Judge(PROVOKE)
    live = reactor(judge, **off)
    assert not live.active and not await live.react(SESSION, say())
    assert judge.calls == [] and live.data.affects.count() == 0


async def test_without_a_model_nothing_happens() -> None:
    live = reactor(None)
    assert not live.active and not await live.react(SESSION, say())


async def test_a_limit_per_hour_stops_the_spam_and_frees_up_later() -> None:
    clock = Clock()
    judge = Judge(PROVOKE)
    live = reactor(judge, clock=clock, live_max_per_hour=3)
    results = [await live.react(SESSION, say(index=i)) for i in range(5)]
    assert results == [True, True, True, False, False] and len(judge.calls) == 3

    clock.now += timedelta(hours=1, minutes=1)
    assert await live.react(SESSION, say(index=9))


async def test_a_failing_model_or_garbage_never_disturbs_the_conversation() -> None:
    assert not await reactor(Judge(error=LLMUnavailableError("fora"))).react(SESSION, say())
    for bad in (
        "isto não é JSON",
        {"reaction": {"kind": "irritação", "concern": "inventada"}},
        {"reaction": {"kind": "x", "concern": "honestidade", "effect": 9}},
        {"reaction": {"kind": "irritação", "concern": "honestidade", "effect": "muito"}},
    ):
        live = reactor(Judge(bad))
        assert not await live.react(SESSION, say()) and live.data.affects.count() == 0


# --- chain accumulation --------------------------------------------------------------------------


async def test_three_provocations_in_a_row_add_up_to_a_strong_feeling() -> None:
    live = reactor(Judge(PROVOKE))
    for i in range(3):
        await live.react(SESSION, say(index=i))
    book = AffectBook(live.data, live.config.affect, live.clock)

    (top,) = book.state().feelings

    assert top.affect.kind == "irritação"
    assert top.strength == pytest.approx(0.9) and top.strength >= 0.6  # "forte"


async def test_the_chain_never_goes_over_one_and_other_kinds_stay_apart() -> None:
    live = reactor(Judge(PROVOKE))
    for i in range(6):
        await live.react(SESSION, say(index=i))
    book = AffectBook(live.data, live.config.affect, live.clock)
    book.record("alegria", 0.6, 0.3, ["turn:x:1", "concern:curiosidade"])

    feelings = book.state().feelings

    assert [f.affect.kind for f in feelings] == ["irritação", "alegria"]
    assert feelings[0].strength == 1.0 and feelings[1].strength == pytest.approx(0.3)


async def test_the_feeling_fades_like_any_other() -> None:
    clock = Clock()
    live = reactor(Judge(PROVOKE), clock=clock)
    await live.react(SESSION, say())
    book = AffectBook(live.data, live.config.affect, clock)
    clock.now += timedelta(hours=6)  # one half-life
    (felt,) = book.state().feelings
    assert felt.strength == pytest.approx(0.15)
    clock.now += timedelta(hours=24)
    assert book.state().feelings == []


# --- the dream still reaches 1.0 -----------------------------------------------------------------


def test_the_dream_is_not_capped_by_the_live_limit() -> None:
    data = V2Data.over(InMemoryDocStore())
    book = AffectBook(data, V2AffectSettings(), lambda: NOW)
    dream, _ = book.record("tristeza", -1.0, 1.0, ["turn:x:1", "concern:bem-estar-do-petrus"])
    live, _ = book.record(
        "tristeza", -1.0, 1.0, ["turn:x:2", "concern:bem-estar-do-petrus"], origin="conversa"
    )
    assert dream is not None and dream.intensity == 1.0 and dream.origin == "sonho"
    assert live is not None and live.intensity == 0.3 and live.origin == "conversa"


# --- the consequence in the next turn ------------------------------------------------------------


async def test_the_next_turns_state_block_shows_the_feeling() -> None:
    data = V2Data.over(InMemoryDocStore())
    live = LiveReactor(data, Judge(PROVOKE), config=config(), clock=lambda: NOW)
    await live.react(SESSION, say())
    context = V2Context(data, FakeEmbedder(), config=config(), clock=lambda: NOW)

    block = (await context.context_for("e aí, tudo certo?")).block or ""

    assert STATE_OPEN in block and "irritação" in block


# --- the sink schedules it without blocking ------------------------------------------------------


class Gate:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = asyncio.Event()


class SlowReactor(LiveReactor):
    """A reactor whose judging waits for a gate: the turn must not wait for it."""

    def __init__(self, data: V2Data, gate: Gate) -> None:
        super().__init__(data, Judge(PROVOKE), config=config(), clock=lambda: NOW)
        self.gate = gate

    async def react(self, session_id: str, turn: Turn) -> bool:
        self.gate.entered.set()
        await self.gate.release.wait()
        return await super().react(session_id, turn)


class Writer:
    def __init__(self) -> None:
        self.written: list[tuple[str, Turn]] = []

    def write(self, session_id: str, turn: Turn) -> None:
        self.written.append((session_id, turn))


async def test_the_sink_returns_at_once_and_the_reaction_lands_in_the_background() -> None:
    data = V2Data.over(InMemoryDocStore())
    gate = Gate()
    writer = Writer()
    context = V2Context(data, FakeEmbedder(), config=config(), clock=lambda: NOW)
    runtime = V2Runtime(
        data,
        context,
        writer,
        SlowReactor(data, gate),
        asyncio.get_running_loop(),  # type: ignore[arg-type]
    )
    turn = say()

    await asyncio.to_thread(runtime.sink, SESSION, turn)  # how the Agent calls it

    assert writer.written == [(SESSION, turn)]  # the transcript was kept
    assert data.affects.count() == 0  # the reaction has not happened yet: the turn did not wait
    await asyncio.to_thread(gate.entered.wait, 2)
    gate.release.set()
    for _ in range(100):
        if data.affects.count():
            break
        await asyncio.sleep(0.02)
    assert data.affects.count() == 1


async def test_the_sink_works_without_a_reactor_or_a_loop() -> None:
    data = V2Data.over(InMemoryDocStore())
    writer = Writer()
    context = V2Context(data, FakeEmbedder(), config=config(), clock=lambda: NOW)
    V2Runtime(data, context, writer).sink(SESSION, say())  # type: ignore[arg-type]
    V2Runtime(data, context, writer, reactor(Judge(PROVOKE)), None).sink(SESSION, say())  # type: ignore[arg-type]
    assert len(writer.written) == 2 and data.affects.count() == 0


# --- the command ---------------------------------------------------------------------------------


async def test_memory_affect_shows_where_each_feeling_was_born(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from brain.clients.cli import app, console

    data = V2Data.over(InMemoryDocStore())
    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", lambda: None)
    monkeypatch.setattr("brain.clients.memory_v2_cli.open_v2_data", lambda: data)
    monkeypatch.setattr(console, "width", 300, raising=False)
    live = LiveReactor(data, Judge(PROVOKE), config=config())
    await live.react(SESSION, say())
    AffectBook(data, V2AffectSettings()).record(
        "tristeza", -0.8, 0.9, ["turn:x:1", "concern:bem-estar-do-petrus"]
    )

    out = CliRunner().invoke(app, ["memory", "affect"]).output

    assert "Origem" in out and "conversa" in out and "sonho" in out
