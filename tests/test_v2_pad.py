"""Emoção no espaço PAD e humor ALMA (T-174, ADR-030)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from brain.config import V2AffectSettings
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.context import state_lines
from brain.memory.v2.models import Affect, PersonaState, Trait, V2DataError
from brain.memory.v2.pad import (
    PAD,
    Temperament,
    emotion_pad,
    octant,
    octant_words,
    temperament,
)
from brain.memory.v2.store import InMemoryDocStore, V2Data

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CAUSE = ["turn:s:1", "concern:bem-estar-do-petrus"]


def book(temper: Temperament | None = None) -> AffectBook:
    data = V2Data.over(InMemoryDocStore())
    return AffectBook(data, V2AffectSettings(), lambda: NOW, temper=temper)


# --- the emotion as a point ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "valence", "where"),
    [
        ("irritação", -0.7, "hostil"),
        ("raiva", -0.9, "hostil"),
        ("medo", -0.6, "ansiosa"),
        ("preocupação", -0.5, "ansiosa"),
        ("tristeza", -0.7, "entediada"),
        ("tédio", -0.3, "entediada"),
        ("alegria", 0.8, "exuberante"),
        ("empolgação", 0.7, "exuberante"),
        ("alívio", 0.5, "relaxada"),
        ("gratidão", 0.6, "dependente"),
        ("desprezo", -0.6, "hostil"),
    ],
)
def test_each_feeling_lands_in_its_octant(kind: str, valence: float, where: str) -> None:
    assert octant(emotion_pad(kind, valence)) == where


def test_pleasure_is_the_valence_and_unknown_labels_are_neutral_on_the_rest() -> None:
    point = emotion_pad("um troço indescritível", -0.4)
    assert point == PAD(-0.4, 0.0, 0.0)
    assert emotion_pad("Irritada!", -0.5).a > 0  # folded, any form of the word


def test_octant_words_carry_a_degree_and_stay_quiet_near_zero() -> None:
    assert octant_words(PAD(0.05, 0.02, 0.0)) is None
    assert octant_words(PAD(-0.2, 0.15, -0.1)) == "um pouco ansiosa"
    assert octant_words(PAD(-0.5, 0.5, 0.4)) == "bem hostil"


# --- the mood ------------------------------------------------------------------------------------


def test_with_no_feelings_she_rests_where_her_temperament_says() -> None:
    b = book(Temperament(0.5, PAD(0.2, 0.1, 0.0)))
    assert b.shift(NOW) == PAD(0.0, 0.0, 0.0) and b.mood(NOW) == 0.0
    assert b.mood_point(NOW) == PAD(0.2, 0.1, 0.0)


def test_an_emotion_pushes_the_mood_toward_its_point_and_it_goes_back() -> None:
    b = book()
    b.record("irritação", -0.8, 0.8, CAUSE, occurred_at=NOW)
    now = b.shift(NOW)
    assert now.p < -0.4 and now.a > 0.2 and now.d > 0.0 and octant(now) == "hostil"
    later = b.shift(NOW + timedelta(hours=12))
    assert later.size == pytest.approx(now.size / 2, rel=0.15)  # one half-life (12 h)
    assert b.shift(NOW + timedelta(days=5)).size < 0.01


def test_a_volatile_temperament_is_moved_more_and_for_longer() -> None:
    calm, volatile = book(Temperament(0.0)), book(Temperament(1.0))
    for b in (calm, volatile):
        b.record("tristeza", -0.6, 0.5, CAUSE, occurred_at=NOW)
    assert abs(volatile.mood(NOW)) > abs(calm.mood(NOW)) * 2
    later = NOW + timedelta(hours=10)
    ratio_calm = calm.mood(later) / calm.mood(NOW)
    ratio_volatile = volatile.mood(later) / volatile.mood(NOW)
    assert ratio_volatile > ratio_calm


def test_many_pushes_saturate_inside_the_range() -> None:
    b = book(Temperament(1.0))
    for _ in range(20):
        b.record("alegria", 1.0, 1.0, CAUSE, occurred_at=NOW)
    point = b.mood_point(NOW)
    assert all(-1.0 <= x <= 1.0 for x in point.as_list()) and point.p > 0.9


def test_an_explicit_pad_beats_the_label() -> None:
    b = book()
    affect = Affect("qualquer", -0.1, 1.0, CAUSE, pad=[0.5, -0.5, 0.5], occurred_at=NOW)
    b.data.affects.save(affect)
    assert octant(b.shift(NOW)) == "relaxada"


def test_a_bad_pad_is_refused_and_old_affects_load_without_one() -> None:
    with pytest.raises(V2DataError):
        Affect("x", 0.1, 0.5, CAUSE, pad=[2.0, 0.0, 0.0]).to_doc()
    doc = Affect("x", 0.1, 0.5, CAUSE).to_doc()
    doc.pop("pad")
    assert Affect.from_doc(doc).pad == []


def test_the_temperament_comes_from_the_personality() -> None:
    lively = PersonaState(
        traits={
            "entusiasmo": Trait(0.9, 0.9, 0, 1),
            "retraimento": Trait(0.1, 0.1, 0, 1),
            "assertividade": Trait(0.8, 0.8, 0, 1),
            "volatilidade": Trait(0.8, 0.8, 0, 1),
        }
    )
    t = temperament(lively)
    assert t.volatility == 0.8 and t.rest.p > 0.3 and t.rest.a > 0.1 and t.rest.d > 0.1
    v2 = PersonaState(traits={"brincadeira": Trait(0.2, 0.6, 0, 1)})  # before the migration
    assert temperament(v2).rest.a < 0
    assert temperament(None) == Temperament(0.5, temperament(None).rest)


def test_the_state_says_the_octant_of_the_mood() -> None:
    b = book()
    b.record("medo", -0.7, 0.9, CAUSE, occurred_at=NOW)
    first = state_lines(b.state(NOW))[0]
    assert "pra baixo" in first and "ansiosa" in first
