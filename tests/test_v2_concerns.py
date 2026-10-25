"""Preocupações e valores: a régua dos sentimentos (T-108)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients.cli import app
from brain.config import DEFAULT_CONCERNS, ConcernDefault, MemoryV2Settings, V2ConcernSettings
from brain.memory.v2.concerns import ConcernBook, ConcernError
from brain.memory.v2.guards import check_concern_change
from brain.memory.v2.models import Concern, V2DataError
from brain.memory.v2.store import InMemoryDocStore, V2Data

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def make(config: V2ConcernSettings | None = None) -> tuple[ConcernBook, V2Data, Clock]:
    data = V2Data.over(InMemoryDocStore())
    clock = Clock()
    book = ConcernBook(data, config or V2ConcernSettings(), clock)
    book.ensure_defaults()
    return book, data, clock


# --- the starting list --------------------------------------------------------------------------


def test_the_defaults_are_loaded_the_first_time_and_cover_what_the_petrus_defined() -> None:
    book, data, _ = make()
    ids = {c.id for c in book.all()}
    assert ids == {
        "bem-estar-do-petrus",
        "honestidade",
        "curiosidade",
        "capricho",
        "autonomia-do-petrus",
    }
    assert all(0.0 <= c.weight <= 1.0 and c.weight == c.baseline_weight for c in book.all())
    assert data.concerns.count() == 5


def test_loading_the_defaults_again_keeps_what_was_edited() -> None:
    book, _, _ = make()
    book.set_weight("curiosidade", 0.2)
    book.ensure_defaults()
    concern = book.get("curiosidade")
    assert concern is not None and concern.weight == 0.2 and concern.baseline_weight == 0.7


def test_the_starting_list_is_configurable() -> None:
    config = V2ConcernSettings(defaults=[ConcernDefault(id="musica", name="A música", weight=0.5)])
    book, _, _ = make(config)
    assert [c.id for c in book.all()] == ["musica"]


@pytest.mark.parametrize("weight", [-0.1, 1.01, 5])
def test_a_default_weight_outside_zero_to_one_is_refused(weight: float) -> None:
    with pytest.raises(ValueError):
        ConcernDefault(id="x", name="X", weight=weight)


@pytest.mark.parametrize("weight", [-0.01, 1.2, float("nan")])
def test_a_stored_weight_outside_zero_to_one_is_refused(weight: float) -> None:
    with pytest.raises(V2DataError):
        Concern("x", "X", weight, 0.5).to_doc()


# --- the user edits freely ----------------------------------------------------------------------


def test_the_petrus_edits_freely_with_no_monthly_ceiling_and_it_is_recorded() -> None:
    book, _, clock = make()
    book.set_weight("honestidade", 0.1, "hoje não")
    book.set_weight("honestidade", 1.0)
    clock.advance(minutes=1)
    book.set_weight("honestidade", 0.3)

    concern = book.get("honestidade")
    assert concern is not None and concern.weight == 0.3
    assert [c.source for c in concern.history] == ["petrus"] * 3
    assert concern.history[0].reason == "hoje não" and concern.history[0].delta == pytest.approx(
        -0.8
    )


@pytest.mark.parametrize("weight", [-0.1, 1.5, "muito", None])
def test_an_invalid_edit_is_refused_and_changes_nothing(weight: Any) -> None:
    book, _, _ = make()
    with pytest.raises(ConcernError):
        book.set_weight("honestidade", weight)
    concern = book.get("honestidade")
    assert concern is not None and concern.weight == 0.9 and concern.history == []


def test_editing_an_unknown_concern_is_refused() -> None:
    book, _, _ = make()
    with pytest.raises(ConcernError, match="Não conheço"):
        book.set_weight("inexistente", 0.5)


# --- the night moves them very slowly -----------------------------------------------------------


def test_without_high_impact_nothing_changes() -> None:
    book, _, _ = make()
    outcome = book.propose_night_change("curiosidade", 0.05, 0.5, "S1:t4", "x")
    concern = book.get("curiosidade")
    assert outcome.rejection is not None and outcome.rejection.code == "sem_impacto"
    assert concern is not None and concern.weight == 0.7 and concern.history == []


def test_a_change_must_cite_the_event_that_caused_it() -> None:
    book, _, _ = make()
    for event in (None, "", "   "):
        outcome = book.propose_night_change("curiosidade", 0.05, 0.95, event)
        assert outcome.rejection is not None and outcome.rejection.code == "sem_evento"


def test_a_high_impact_change_is_applied_and_records_the_event() -> None:
    book, _, _ = make()
    outcome = book.propose_night_change("curiosidade", 0.03, 0.9, "S2:t7", "ele pediu mais estudo")
    concern = book.get("curiosidade")
    assert outcome.ok and outcome.applied == pytest.approx(0.03) and not outcome.capped
    assert concern is not None and concern.weight == pytest.approx(0.73)
    (change,) = concern.history
    assert change.event_ref == "S2:t7" and change.source == "night"
    assert change.reason == "ele pediu mais estudo"


def test_a_bigger_change_is_cut_at_the_monthly_ceiling() -> None:
    book, _, _ = make()
    outcome = book.propose_night_change("curiosidade", 0.4, 1.0, "S1:t1")
    concern = book.get("curiosidade")
    assert outcome.applied == pytest.approx(0.05) and outcome.capped
    assert concern is not None and concern.weight == pytest.approx(0.75)


def test_the_ceiling_is_per_item_per_month_in_either_direction() -> None:
    book, _, clock = make()
    book.propose_night_change("curiosidade", 0.03, 1.0, "S1:t1")
    clock.advance(days=3)
    second = book.propose_night_change("curiosidade", -0.04, 1.0, "S1:t2")  # only 0.02 is left
    assert second.applied == pytest.approx(-0.02) and second.capped
    clock.advance(days=3)
    third = book.propose_night_change("curiosidade", 0.05, 1.0, "S1:t3")
    assert third.rejection is not None and third.rejection.code == "teto_mensal"
    other = book.propose_night_change("honestidade", 0.05, 1.0, "S1:t3")
    assert other.ok  # another item has its own ceiling
    clock.advance(days=30)
    assert book.propose_night_change("curiosidade", 0.05, 1.0, "S9:t1").ok  # a new month


def test_the_petrus_edits_do_not_use_up_the_nights_ceiling() -> None:
    book, _, _ = make()
    book.set_weight("curiosidade", 0.2)
    assert book.propose_night_change("curiosidade", 0.05, 1.0, "S1:t1").applied == pytest.approx(
        0.05
    )


def test_a_change_never_leaves_the_zero_to_one_range() -> None:
    book, _, _ = make(V2ConcernSettings(max_monthly_change=0.5))
    book.set_weight("honestidade", 0.98)
    outcome = book.propose_night_change("honestidade", 0.4, 1.0, "S1:t1")
    concern = book.get("honestidade")
    assert outcome.applied == pytest.approx(0.02)
    assert concern is not None and concern.weight == pytest.approx(1.0)


def test_the_night_cannot_touch_an_unknown_concern() -> None:
    book, _, _ = make()
    outcome = book.propose_night_change("inventada", 0.05, 1.0, "S1:t1")
    assert outcome.rejection is not None and outcome.rejection.code == "preocupacao_desconhecida"


@pytest.mark.parametrize(("delta", "impact"), [("x", 0.9), (0.05, "alto"), (None, None)])
def test_garbage_numbers_from_the_model_are_refused_not_raised(delta: Any, impact: Any) -> None:
    concern = Concern("x", "X", 0.5, 0.5)
    rejection = check_concern_change(concern, delta, impact, "S1:t1", min_impact=0.8)
    assert rejection is not None and rejection.code == "numero"


# --- no probability ----------------------------------------------------------------------------


def test_nothing_resembling_a_probability_is_stored_in_a_concern() -> None:
    book, data, _ = make()
    book.propose_night_change("curiosidade", 0.03, 0.9, "S1:t1")
    for doc in (c.to_doc() for c in data.concerns.list()):
        keys = set(doc) | {k for ch in doc["history"] for k in ch}
        assert not keys & {"confidence", "probability", "alpha", "beta", "n", "prob"}
    assert {d.id for d in DEFAULT_CONCERNS} == {c.id for c in book.all()}
    assert MemoryV2Settings().concerns.max_monthly_change == 0.05


# --- the command -------------------------------------------------------------------------------


@pytest.fixture
def v2(monkeypatch: pytest.MonkeyPatch) -> V2Data:
    data = V2Data.over(InMemoryDocStore())
    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", lambda: None)
    monkeypatch.setattr("brain.clients.memory_v2_cli.open_v2_data", lambda: data)
    from brain.clients.cli import console

    monkeypatch.setattr(console, "width", 300, raising=False)
    return data


def run(*args: str) -> Any:
    return CliRunner().invoke(app, ["memory", "concerns", *args])


def test_the_command_lists_the_concerns_with_weights_and_loads_the_defaults(v2: V2Data) -> None:
    result = run()
    assert result.exit_code == 0
    assert "bem-estar-do-petrus" in result.output and "0.90" in result.output
    assert "uma escolha e não uma estimativa" in result.output
    assert v2.concerns.count() == 5


def test_set_changes_the_weight_and_show_prints_the_history(v2: V2Data) -> None:
    changed = run("set", "curiosidade", "0.4", "--reason", "quero foco")
    shown = run("show", "curiosidade")
    assert changed.exit_code == 0 and "agora pesa 0.40" in changed.output
    assert "peso 0.40" in shown.output and "-0.30" in shown.output
    assert "você" in shown.output and "quero foco" in shown.output


def test_show_marks_the_night_and_its_event(v2: V2Data) -> None:
    run()
    ConcernBook(v2).propose_night_change("curiosidade", 0.03, 0.9, "S2:t7", "estudo")
    out = run("show", "curiosidade").output
    assert "algoritmo de sonho" in out and "evento S2:t7" in out


def test_set_refuses_a_bad_weight_and_an_unknown_concern(v2: V2Data) -> None:
    bad = run("set", "curiosidade", "1.5")
    unknown = run("show", "nada")
    assert bad.exit_code == 1 and "entre 0 e 1" in bad.output
    assert unknown.exit_code == 1 and "Não conheço" in unknown.output
    concern = v2.concerns.get("curiosidade")
    assert concern is not None and concern.weight == 0.7
