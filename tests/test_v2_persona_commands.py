"""Comandos persona show|history|revert e a métrica de deriva (T-112)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients.cli import app
from brain.config import MemoryV2Settings, V2PersonaSettings
from brain.memory.v2.evolve import PersonaPhase
from brain.memory.v2.models import TranscriptTurn
from brain.memory.v2.night import Budget, PhaseContext, SessionMaterial
from brain.memory.v2.persona import PersonaBook, PersonaError, drift
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder
from tests.test_v2_persona import Clock, Proposer, move, proposal

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


def shifted_book(nights: int = 3) -> tuple[PersonaBook, V2Data, Clock]:
    data = V2Data.over(InMemoryDocStore())
    clock = Clock()
    book = PersonaBook(data, V2PersonaSettings(), clock)
    book.ensure()
    for i in range(nights):
        clock.advance(days=1)
        book.evolve([move(event=f"turn:s:{i}", marked=f"Marca {i} que ficou")])
    return book, data, clock


# --- drift -------------------------------------------------------------------------------------


def test_the_usual_self_has_no_drift() -> None:
    book, _, _ = shifted_book(0)
    assert drift(book.current()) == 0.0 and drift(None) == 0.0
    assert book.drift_warning() is None


def test_moving_the_traits_away_passes_the_limit_and_coming_back_removes_the_warning() -> None:
    config = V2PersonaSettings(drift_warning=0.02)
    data = V2Data.over(InMemoryDocStore())
    clock = Clock()
    book = PersonaBook(data, config, clock)
    book.ensure()
    for i in range(6):
        clock.advance(days=1)
        for trait in (
            "introspecção",
            "ironia",
            "carinho",
            "proatividade",
            "franqueza",
            "brincadeira",
        ):
            book.evolve([move(trait, event=f"turn:s:{i}")])
    assert book.drift_warning() is not None and book.drift_warning() >= 0.02

    clock.advance(days=400)  # a long time later the phase has passed
    book.evolve([])
    assert book.drift_warning() is None


def test_the_drift_is_the_mean_distance_to_the_baselines() -> None:
    book, _, _ = shifted_book(1)
    state = book.current()
    assert state is not None
    total = sum(abs(t.value - t.baseline) for t in state.traits.values())
    assert drift(state) == pytest.approx(total / len(state.traits))


async def test_the_night_writes_one_diary_warning_a_day_when_she_drifts_too_far() -> None:
    data = V2Data.over(InMemoryDocStore())
    config = V2PersonaSettings(drift_warning=0.001)
    book = PersonaBook(data, config, lambda: T0)
    state = book.ensure()
    state.traits["ironia"].value += 0.2
    state.version = 2
    data.persona.save(state, 1, "teste")
    now = T0 + timedelta(hours=1)
    mat = [SessionMaterial("sessA", [TranscriptTurn("sessA", 0, "oi", at=now)])]

    def ctx() -> PhaseContext:
        return PhaseContext(
            data=data,
            config=MemoryV2Settings(enabled=True, persona=config),
            material=mat,
            budget=Budget(10**9, 10**6),
            cheap=Proposer([]),
            reflect=Proposer([]),
            embedder=FakeEmbedder(),
            clock=lambda: now,
        )

    first = await PersonaPhase().run(ctx())
    await PersonaPhase().run(ctx())  # the same day again

    warnings = data.journal.list({"kind": "persona"})
    assert len(warnings) == 1 and warnings[0].data["warning"] == "drift"
    assert "persona revert" in warnings[0].text and "deriva" in first.summary


# --- revert ------------------------------------------------------------------------------------


def test_revert_goes_back_to_the_previous_version_as_a_new_version() -> None:
    book, data, _ = shifted_book(3)  # versions 1..4, now at 4
    before = book.current()
    assert before is not None and before.version == 4

    new = book.revert()

    assert new.version == 5 and new.changed_by == ["revert:v3"]
    third = next(v for v in book.history() if v.state.version == 3)
    assert {n: t.value for n, t in new.traits.items()} == {
        n: t.value for n, t in third.state.traits.items()
    }
    assert [v.state.version for v in book.history()] == [1, 2, 3, 4]  # nothing was erased
    assert data.persona.history.count() == 4


def test_revert_to_a_chosen_version() -> None:
    book, _, _ = shifted_book(3)
    new = book.revert(to=1)
    assert all(t.value == t.baseline for t in new.traits.values()) and new.recent == []


def test_revert_to_a_missing_version_or_the_current_one_is_refused() -> None:
    book, _, _ = shifted_book(2)
    with pytest.raises(PersonaError, match="Não existe a versão 99"):
        book.revert(to=99)
    with pytest.raises(PersonaError, match="Já estou na versão 3"):
        book.revert(to=3)
    with pytest.raises(PersonaError):
        shifted_book(0)[0].revert()  # nothing before version 1


# --- the commands ------------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> tuple[V2Data, Any]:
    book, data, _ = shifted_book(3)
    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", lambda: None)
    monkeypatch.setattr("brain.clients.memory_v2_cli.open_v2_data", lambda: data)
    from brain.clients.cli import console

    monkeypatch.setattr(console, "width", 300, raising=False)

    def run(*args: str, input: str | None = None) -> Any:
        return CliRunner().invoke(app, ["persona", *args], input=input)

    return data, run


def test_show_prints_traits_recent_version_and_drift(cli: Any) -> None:
    data, run = cli
    result = run("show")
    assert result.exit_code == 0
    assert "introspecção" in result.output and "versão 4" in result.output
    assert "deriva" in result.output and "Marca 2 que ficou" in result.output
    assert "mais reflexiva" in result.output


def test_show_warns_when_the_drift_is_past_the_limit(
    cli: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.config import settings

    data, run = cli
    monkeypatch.setattr(settings.memory.v2, "persona", V2PersonaSettings(drift_warning=0.001))
    assert "Atenção: a deriva" in run("show").output


def test_history_lists_the_versions_with_the_reason_and_the_events(cli: Any) -> None:
    data, run = cli
    result = run("history")
    assert result.exit_code == 0
    assert "4 (atual)" in result.output and "turn:s:2" in result.output
    assert "algoritmo de sonho" in result.output


def test_revert_asks_for_confirmation_then_creates_a_new_version(cli: Any) -> None:
    data, run = cli
    refused = run("revert", input="n\n")
    assert "não mudei nada" in refused.output and data.persona.current().version == 4

    accepted = run("revert", input="y\n")
    assert accepted.exit_code == 0 and "Agora estou na versão 5" in accepted.output
    assert data.persona.current().version == 5


def test_revert_with_yes_to_a_version(cli: Any) -> None:
    data, run = cli
    assert run("revert", "--to", "2", "--yes").exit_code == 0
    assert data.persona.current().changed_by == ["revert:v2"]


def test_revert_to_a_missing_version_fails_clearly(cli: Any) -> None:
    data, run = cli
    result = run("revert", "--to", "99", "--yes")
    assert result.exit_code == 1 and "Não existe a versão 99" in result.output
    assert data.persona.current().version == 4


def test_without_the_v2_the_persona_commands_say_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.memory.errors import MemoryUnavailableError

    def off() -> None:
        raise MemoryUnavailableError("A Memória V2 está desligada.")

    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", off)
    for args in (["show"], ["history"], ["revert", "--yes"]):
        result = CliRunner().invoke(app, ["persona", *args])
        assert result.exit_code == 1 and "desligada" in result.output


def test_a_proposal_helper_still_works() -> None:
    assert proposal()["trait"] == "introspecção"
