"""O bloco <voz> e o juniper mood set (T-175, ADR-030)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients.cli import app
from brain.config import MemoryV2Settings, V2KnowledgeSettings, V2PersonaSettings
from brain.eventlog import bind_session
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.context import V2Context
from brain.memory.v2.models import PersonaState, Trait
from brain.memory.v2.pad import OCTANTS, octant
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.memory.v2.tone import VOICE_HEADER, VOICE_OPEN, render_voice, voice_lines
from tests.test_extraction import FakeEmbedder

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CAUSE = ["turn:s:1", "concern:bem-estar-do-petrus"]


def fresh() -> tuple[V2Data, AffectBook]:
    data = V2Data.over(InMemoryDocStore())
    return data, AffectBook(data, clock=lambda: NOW)


# --- the voice -----------------------------------------------------------------------------------


def test_her_usual_self_needs_no_voice_block() -> None:
    _, book = fresh()
    assert voice_lines(book.state()) == [] and render_voice(book.state()) is None


@pytest.mark.parametrize("where", sorted(OCTANTS.values()))
def test_every_octant_has_concrete_orders_of_tone(where: str) -> None:
    _, book = fresh()
    book.force(where, "forte")
    text = render_voice(book.state()) or ""
    assert VOICE_OPEN in text and VOICE_HEADER in text and where in text
    assert len(voice_lines(book.state())) >= 2


def test_irritated_and_happy_sound_opposite() -> None:
    _, angry = fresh()
    angry.force("hostil", "forte")
    _, happy = fresh()
    happy.force("exuberante", "forte")
    a, h = render_voice(angry.state()) or "", render_voice(happy.state()) or ""
    assert "secas" in a and "sem emoji" in a and "sem brincadeira" in a
    assert "energia" in h and "exclamação" in h and "emoji também" in h


def test_the_voice_never_touches_the_actions_and_never_orders_offence() -> None:
    _, book = fresh()
    book.force("hostil", "forte")
    text = render_voice(book.state()) or ""
    assert "nunca a escolha de ferramentas" in text and "confirmações" in text
    for word in ("humilh", "ofend", "xing", "insult"):
        assert word not in text.lower()


def test_a_strong_feeling_is_said_and_a_grudge_with_him_can_be_told() -> None:
    _, book = fresh()
    book.record("irritação", -0.8, 0.9, CAUSE, occurred_at=NOW)
    text = render_voice(book.state()) or ""
    assert "irritação com ele" in text and "pode dizer isso" in text


def test_traits_shade_the_voice() -> None:
    _, book = fresh()
    book.force("hostil", "forte")
    dry = PersonaState(traits={"ironia": Trait(0.9, 0.5, 0, 1)})
    plain = PersonaState(traits={"ironia": Trait(0.1, 0.5, 0, 1)})
    assert "ironia seca" in (render_voice(book.state(), dry) or "")
    assert "ironia seca" not in (render_voice(book.state(), plain) or "")


def test_same_state_same_text() -> None:
    _, book = fresh()
    book.record("medo", -0.6, 0.7, CAUSE, occurred_at=NOW)
    assert render_voice(book.state()) == render_voice(book.state())


# --- the forced mood -----------------------------------------------------------------------------


def test_a_forced_mood_holds_then_ends_by_itself() -> None:
    data, book = fresh()
    book.force("ansiosa", "medio", hours=2)
    assert octant(book.shift(NOW + timedelta(hours=1))) == "ansiosa"
    assert book.shift(NOW + timedelta(hours=1)).size == pytest.approx(book.shift(NOW).size)
    assert book.forced(NOW + timedelta(hours=3)) is None
    assert book.shift(NOW + timedelta(hours=3)).size < 0.01


def test_a_forced_mood_wins_over_the_feelings_and_is_not_a_feeling() -> None:
    _, book = fresh()
    book.record("alegria", 0.9, 0.9, CAUSE, occurred_at=NOW)
    book.force("entediada", "forte")
    state = book.state()
    assert octant(state.shift) == "entediada"
    assert [f.affect.kind for f in state.feelings] == ["alegria"]


def test_clearing_ends_it_and_a_new_one_replaces_the_old() -> None:
    _, book = fresh()
    book.force("hostil")
    book.force("relaxada")
    assert octant(book.shift()) == "relaxada"
    assert book.release() == 1 and book.forced() is None


def test_bad_moods_and_durations_are_refused() -> None:
    _, book = fresh()
    for args in (("brava",), ("hostil", "enorme"), ("hostil", "forte", 0)):
        with pytest.raises(ValueError):
            book.force(*args)  # type: ignore[arg-type]


# --- in the prompt -------------------------------------------------------------------------------


async def test_the_voice_block_goes_into_the_prompt_after_the_state() -> None:
    data, book = fresh()
    book.force("hostil", "forte")
    bind_session("sessao-voz")
    config = MemoryV2Settings(
        enabled=True,
        persona=V2PersonaSettings(always_describe=False),
        knowledge=V2KnowledgeSettings(strict=False),
    )
    ctx = V2Context(data, FakeEmbedder(), config=config, clock=lambda: NOW)
    names = [n for n, _ in await ctx.preview("oi")]
    assert names.index("estado") < names.index("voz")


def test_the_mood_probe_a_and_b_give_different_orders() -> None:
    """The A/B of ADR-030, section 7, at the prompt level: a good and a bad mood (+-0.6) must
    produce different orders of tone. The answer-level probe runs against a real model."""
    _, good = fresh()
    good.force("exuberante", "forte")
    _, bad = fresh()
    bad.force("hostil", "forte")
    assert good.mood() == pytest.approx(0.6) and bad.mood() == pytest.approx(-0.6)
    diff = set(voice_lines(good.state())) ^ set(voice_lines(bad.state()))
    assert len(diff) >= 5  # only the dominance line (both are dominant) is shared


# --- the command ---------------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> V2Data:
    data = V2Data.over(InMemoryDocStore())
    data.close = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", lambda: None)
    monkeypatch.setattr("brain.clients.memory_v2_cli.open_v2_data", lambda: data)
    return data


def run(*args: str) -> Any:
    return CliRunner().invoke(app, ["mood", *args])


def test_mood_set_show_and_clear(cli: V2Data) -> None:
    out = run("set", "hostil", "--grau", "forte", "--por", "1")
    assert out.exit_code == 0 and "hostil" in out.output
    assert [a.origin for a in cli.affects.list()] == ["manual"]
    assert "forçou o meu humor" in cli.journal.list()[0].text
    shown = run("show").output
    assert "bem hostil" in shown and "secas" in shown
    assert "Voltei" in run("clear").output and AffectBook(cli).forced() is None


def test_mood_set_refuses_an_unknown_mood(cli: V2Data) -> None:
    out = run("set", "brava")
    assert out.exit_code == 1 and "Não conheço" in out.output and cli.affects.count() == 0
