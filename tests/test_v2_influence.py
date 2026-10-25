"""Mais influência da V2 na conversa: crenças firmes, eco dos sentimentos e humor fraco (T-122)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from brain.config import MemoryV2Settings, V2AffectSettings, V2OpinionSettings, V2PersonaSettings
from brain.eventlog import bind_session
from brain.memory.v2.affect import ECHO_HOURS, AffectBook
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.context import STATE_OPEN, V2Context, mood_words, state_lines
from brain.memory.v2.models import Evidence
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder
from tests.test_v2_context import QUIET, quiet

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
CAUSE = ["turn:s:1", "concern:bem-estar-do-petrus"]
UNRELATED = "qual a previsão do tempo para amanhã"


@pytest.fixture(autouse=True)
def _session() -> None:
    bind_session("20261022-120000-dddddd")


def firm(data: V2Data, statement: str, *, sources: int = 3, alpha: float = 12.0) -> str:
    book = BeliefBook(data, FakeEmbedder(), MemoryV2Settings().beliefs, lambda: NOW)
    belief, _ = book.create(statement, "geral", Evidence("explicit", "old", 1, at=NOW))
    spread = [
        Evidence("observation", f"s{i}", i, at=NOW - timedelta(days=i)) for i in range(sources)
    ]
    data.beliefs.update(
        belief.id,
        status="active",
        alpha=alpha,
        beta=1.0,
        evidence_for=[e.to_doc() for e in spread],
    )
    return belief.id


def context(data: V2Data, **settings: Any) -> V2Context:
    config = quiet(**settings)
    return V2Context(data, FakeEmbedder(), config=config, clock=lambda: NOW)


# --- the firmest beliefs are always present --------------------------------------------------


async def test_the_firmest_beliefs_come_in_every_conversation_as_a_general_view() -> None:
    data = V2Data.over(InMemoryDocStore())
    for text in ("Acho que o Petrus planeja tudo em planilhas.", "Acho que o Petrus acorda cedo."):
        firm(data, text)
    block = (
        await context(data, opinions=V2OpinionSettings(always_on=2)).context_for(UNRELATED)
    ).block
    assert block is not None and block.count("(visão geral)") == 2
    assert "tenho bastante certeza" in block and "<opinioes>" in block


async def test_always_on_is_a_ceiling_and_picks_the_surest() -> None:
    data = V2Data.over(InMemoryDocStore())
    firm(data, "Acho que o Petrus planeja tudo em planilhas.", alpha=20.0)
    firm(data, "Acho que o Petrus acorda cedo.", alpha=15.0)
    firm(data, "Acho que o Petrus prefere café coado.", alpha=11.0)
    block = (
        await context(data, opinions=V2OpinionSettings(always_on=2)).context_for(UNRELATED)
    ).block or ""
    assert "planilhas" in block and "acorda cedo" in block and "café coado" not in block


async def test_a_belief_touching_the_message_is_not_repeated_as_general_view() -> None:
    data = V2Data.over(InMemoryDocStore())
    firm(data, "Acho que o Petrus planeja tudo em planilhas.")
    ctx = V2Context(
        data,
        FakeEmbedder(),
        config=quiet(opinions=V2OpinionSettings(always_on=2)),
        clock=lambda: NOW,
        relevance=0.1,
    )
    block = (await ctx.context_for("o Petrus planeja tudo em planilhas")).block or ""
    assert block.count("planilhas") == 1 and "(visão geral)" not in block


async def test_beliefs_that_are_only_acho_que_never_become_general_view() -> None:
    data = V2Data.over(InMemoryDocStore())
    firm(data, "Acho que o Petrus planeja tudo em planilhas.", sources=1)  # one source: acho que
    block = (
        await context(data, opinions=V2OpinionSettings(always_on=2)).context_for(UNRELATED)
    ).block
    assert block is None


async def test_always_on_zero_switches_it_off() -> None:
    data = V2Data.over(InMemoryDocStore())
    firm(data, "Acho que o Petrus planeja tudo em planilhas.")
    assert (
        await context(data, opinions=V2OpinionSettings(always_on=0)).context_for(UNRELATED)
    ).block is None


async def test_never_in_the_welcome_mode() -> None:
    data = V2Data.over(InMemoryDocStore())
    firm(data, "Acho que o Petrus planeja tudo em planilhas.")
    ctx = context(data, opinions=V2OpinionSettings(always_on=2))
    block = (await ctx.context_for("minha avó faleceu hoje")).block or ""
    assert "Acolha primeiro" in block and "(visão geral)" not in block


async def test_the_general_view_works_while_the_embedding_model_loads() -> None:
    class Loading:
        ready = False

        async def context_for(self, message: str, previous: str | None = None) -> Any:
            from brain.memory.retrieval import RecalledContext

            return RecalledContext()

    data = V2Data.over(InMemoryDocStore())
    firm(data, "Acho que o Petrus planeja tudo em planilhas.")
    ctx = V2Context(
        data,
        FakeEmbedder(),
        Loading(),
        config=quiet(opinions=V2OpinionSettings(always_on=1)),
        clock=lambda: NOW,
    )
    assert "(visão geral)" in ((await ctx.context_for(UNRELATED)).block or "")


async def test_the_general_view_respects_the_token_budget() -> None:
    data = V2Data.over(InMemoryDocStore())
    firm(data, "Acho que o Petrus planeja tudo em planilhas.")
    tight = quiet(opinions=V2OpinionSettings(always_on=2)).model_copy(update={"max_tokens": 20})
    ctx = V2Context(data, FakeEmbedder(), config=tight, clock=lambda: NOW)
    assert (await ctx.context_for(UNRELATED)).block is None


def test_the_defaults_make_the_v2_speak_more() -> None:
    v2 = MemoryV2Settings()
    assert v2.opinions.always_on == 2 and v2.persona.always_describe is True
    assert (v2.persona.max_step, v2.persona.step_factor) == (0.06, 0.1)
    assert QUIET["opinions"].always_on == 0  # the test helper silences them on purpose


# --- the echo of feelings ---------------------------------------------------------------------


def feel(data: V2Data, hours_ago: float, intensity: float = 0.6, kind: str = "tristeza") -> Any:
    book = AffectBook(data, V2AffectSettings(), lambda: NOW)
    affect, problem = book.record(
        kind, -0.8, intensity, CAUSE, occurred_at=NOW - timedelta(hours=hours_ago)
    )
    assert problem is None and affect is not None
    return book


def test_a_faded_feeling_still_echoes_for_forty_eight_hours() -> None:
    data = V2Data.over(InMemoryDocStore())
    book = feel(data, hours_ago=30)  # strength 0.6 x 0.5^5 ~ 0.02: gone from the state
    state = book.state(NOW)
    assert not state.feelings and [e.affect.kind for e in state.echoes] == ["tristeza"]
    assert any("tristeza (ontem)" in line for line in state_lines(state))


@pytest.mark.parametrize(("hours", "when"), [(14, "ontem"), (20, "ontem"), (40, "anteontem")])
def test_the_echo_says_when(hours: int, when: str) -> None:
    book = feel(V2Data.over(InMemoryDocStore()), hours_ago=hours)
    assert f"({when})" in " ".join(state_lines(book.state(NOW)))


def test_the_echo_is_gone_after_forty_eight_hours() -> None:
    book = feel(V2Data.over(InMemoryDocStore()), hours_ago=ECHO_HOURS + 1)
    state = book.state(NOW)
    assert not state.echoes and not any("te tocou" in line for line in state_lines(state))


def test_a_light_feeling_does_not_echo() -> None:
    book = feel(V2Data.over(InMemoryDocStore()), hours_ago=30, intensity=0.2)
    assert not book.state(NOW).echoes


def test_a_feeling_that_is_still_alive_shows_as_before_and_not_as_an_echo() -> None:
    book = feel(V2Data.over(InMemoryDocStore()), hours_ago=1)
    state = book.state(NOW)
    assert state.feelings and not state.echoes
    assert any("O que mais pesou hoje: tristeza" in line for line in state_lines(state))


def test_an_undone_grievance_leaves_no_echo() -> None:
    data = V2Data.over(InMemoryDocStore())
    book = AffectBook(data, V2AffectSettings(), lambda: NOW)
    grievance, _ = book.record(
        "mágoa", -0.6, 0.6, CAUSE, grievance=True, occurred_at=NOW - timedelta(hours=30)
    )
    assert grievance is not None
    data.affects.update(grievance.id, resolved_at=NOW)
    assert not book.state(NOW).echoes


async def test_the_echo_reaches_the_state_block_of_the_conversation() -> None:
    data = V2Data.over(InMemoryDocStore())
    feel(data, hours_ago=30)
    block = (await context(data).context_for("bom dia")).block or ""
    assert STATE_OPEN in block and "Há pouco o que mais te tocou foi tristeza (ontem)." in block


async def test_the_echo_respects_the_dial() -> None:
    data = V2Data.over(InMemoryDocStore())
    feel(data, hours_ago=30)
    off = quiet(affect=V2AffectSettings(intensity=0.0))
    ctx = V2Context(data, FakeEmbedder(), config=off, clock=lambda: NOW)
    assert (await ctx.context_for("bom dia")).block is None


# --- a weak mood is said too ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mood", "words"),
    [
        (-0.6, "bem pra baixo"),
        (-0.3, "meio pra baixo"),
        (-0.1, "um pouco pra baixo"),
        (-0.05, None),
        (0.0, None),
        (0.1, "um pouco animado"),
        (0.3, "de bom humor"),
        (0.7, "ótimo"),
    ],
)
def test_the_mood_words_include_the_weak_moods(mood: float, words: str | None) -> None:
    assert mood_words(mood) == words


def test_the_persona_options_are_part_of_the_documented_settings() -> None:
    assert V2PersonaSettings().always_describe is True
