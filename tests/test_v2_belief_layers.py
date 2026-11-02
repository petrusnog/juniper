"""Crenças vivas: sobre o usuário, sobre si e sobre o mundo; fontes, erro de previsão e histórico
(T-177, ADR-030)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from brain.config import MemoryV2Settings, V2BeliefSettings, V2OpinionSettings, V2PersonaSettings
from brain.eventlog import bind_session
from brain.memory.v2 import confidence as conf
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.context import V2Context
from brain.memory.v2.models import Belief, BeliefPoint, Evidence, V2DataError
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
WORLD = "A psicodelia surgiu nos anos 60 como resposta à contenção da época."
SELF = "Tenho andado mais irritada nesta semana."


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def book(surprise: float = 1.0) -> tuple[BeliefBook, V2Data, Clock]:
    data = V2Data.over(InMemoryDocStore())
    clock = Clock()
    return BeliefBook(data, FakeEmbedder(), V2BeliefSettings(surprise=surprise), clock), data, clock


def source(domain: str, clock: Clock, kind: str = "source") -> Evidence:
    return Evidence(kind, domain=domain, at=clock())


# --- the layers ----------------------------------------------------------------------------------


def test_a_belief_can_be_about_herself_or_the_world_and_nothing_else() -> None:
    assert Belief(SELF, about="self").to_doc()["about"] == "self"
    assert Belief(WORLD, about="world").to_doc()["about"] == "world"
    with pytest.raises(V2DataError):
        Belief("x", about="outro").to_doc()


def test_evidence_remembers_the_domain_and_the_reference_of_her_own_history() -> None:
    ev = Evidence("source", domain="a.org", ref="note:1")
    again = Evidence.from_doc(ev.to_doc())
    assert again.domain == "a.org" and again.ref == "note:1"
    old = Evidence("observation").to_doc()
    old.pop("domain"), old.pop("ref")
    assert Evidence.from_doc(old).domain is None  # documents from before still load


def test_a_world_belief_from_one_source_stays_a_candidate_and_never_goes_past_maybe() -> None:
    b, data, clock = book()
    belief, created = b.create(WORLD, "história", source("a.org", clock), about="world")
    assert created and belief.status == "candidate" and belief not in b.held()
    assert conf.band(0.9, 8.0, sources=1, about="world") == conf.WORD_TALVEZ
    assert conf.band(0.9, 8.0, sources=1, about="user") == conf.WORD_ACHO


def test_two_domains_form_the_world_belief_and_one_site_only_counts_once() -> None:
    b, data, clock = book()
    belief, _ = b.create(WORLD, "história", source("a.org", clock), about="world")
    b.add_source(belief.id, "a.org")  # the same site again: it adds nothing
    assert data.beliefs.get(belief.id).status == "candidate"  # type: ignore[union-attr]
    clock.now += timedelta(days=1)
    b.add_source(belief.id, "b.org")
    formed = data.beliefs.get(belief.id)
    assert formed is not None and formed.status == "active"
    assert {e.domain for e in formed.evidence_for} == {"a.org", "b.org"}
    assert b.words(formed) in (conf.WORD_ACHO, conf.WORD_TALVEZ)


def test_a_source_that_says_the_opposite_counts_against() -> None:
    b, data, clock = book(surprise=0.0)
    belief, _ = b.create(WORLD, "história", source("a.org", clock), about="world")
    b.add_source(belief.id, "b.org", supports=False)
    got = data.beliefs.get(belief.id)
    assert got is not None and got.beta == pytest.approx(1.0 + 1.5)


def test_her_own_history_is_evidence_once_per_reference() -> None:
    b, data, clock = book(surprise=0.0)
    belief, _ = b.create(
        SELF, "humor", Evidence("self_observation", ref="affect:1", at=clock()), about="self"
    )
    b.add_self_observation(belief.id, "affect:1")  # same piece of history
    b.add_self_observation(belief.id, "persona:v7")
    got = data.beliefs.get(belief.id)
    assert got is not None and got.alpha == pytest.approx(1.0 + 2.0)
    assert conf.distinct_sources(got.evidence_for) == 2  # two different pieces of history


def test_the_three_layers_do_not_merge_into_each_other() -> None:
    b, data, clock = book()
    user, _ = b.create(WORLD, "x", Evidence("observation", "s", 1, at=clock()), about="user")
    world, created = b.create(WORLD, "x", source("a.org", clock), about="world")
    assert created and world.id != user.id  # same words, another layer


# --- prediction error ----------------------------------------------------------------------------


def test_a_contradiction_of_a_firm_belief_weighs_more_than_one_of_a_weak_belief() -> None:
    b, data, clock = book(surprise=1.0)
    firm, _ = b.create("Firme.", "t", Evidence("explicit", "s", 1, at=clock()))
    weak, _ = b.create(
        "Fraca sobre coisas diferentes.", "t", Evidence("observation", "s", 2, at=clock())
    )
    for belief in (firm, weak):
        b.add_evidence(belief.id, Evidence("contradiction", "s2", 9, at=clock()))
    firm_after, weak_after = data.beliefs.get(firm.id), data.beliefs.get(weak.id)
    assert firm_after is not None and weak_after is not None
    assert firm_after.beta - 1.0 > weak_after.beta - 1.0  # the surprise


def test_what_the_user_says_keeps_its_fixed_weight_and_surprise_can_be_switched_off() -> None:
    b, data, clock = book(surprise=1.0)
    belief, _ = b.create("Algo.", "t", Evidence("observation", "s", 1, at=clock()))
    b.add_evidence(belief.id, Evidence("denial", "s", 2, at=clock()))
    assert data.beliefs.get(belief.id).beta == pytest.approx(1.0 + 4.0)  # type: ignore[union-attr]
    plain, pdata, pclock = book(surprise=0.0)
    other, _ = plain.create("Outra.", "t", Evidence("observation", "s", 1, at=pclock()))
    plain.add_evidence(other.id, Evidence("contradiction", "s", 2, at=pclock()))
    assert pdata.beliefs.get(other.id).beta == pytest.approx(2.0)  # type: ignore[union-attr]


def test_the_first_evidence_has_no_expectation_to_be_surprised_by() -> None:
    b, data, clock = book(surprise=2.0)
    belief, _ = b.create("Algo.", "t", Evidence("observation", "s", 1, at=clock()))
    assert belief.alpha == pytest.approx(2.0)


# --- the history ---------------------------------------------------------------------------------


def test_every_change_of_confidence_adds_a_point_to_the_curve() -> None:
    b, data, clock = book(surprise=0.0)
    belief, _ = b.create(WORLD, "história", source("a.org", clock), about="world")
    clock.now += timedelta(days=1)
    b.add_source(belief.id, "b.org")
    clock.now += timedelta(days=1)
    b.add_source(belief.id, "c.org")
    points = b.history(belief.id)
    assert [p.cause for p in points] == ["created", "evidence", "evidence"]
    assert [p.confidence for p in points] == sorted(p.confidence for p in points)
    assert points[0].status == "candidate" and points[-1].status == "active"


def test_a_repeated_evidence_adds_no_point_and_the_decay_does() -> None:
    b, data, clock = book(surprise=0.0)
    belief, _ = b.create("Algo.", "t", Evidence("explicit", "s", 1, at=clock()))
    b.add_evidence(belief.id, Evidence("explicit", "s", 1, at=clock()))  # already counted
    assert len(b.history(belief.id)) == 1
    clock.now += timedelta(days=120)
    b.review_all()
    assert [p.cause for p in b.history(belief.id)][-1] == "decay"


def test_a_point_round_trips() -> None:
    point = BeliefPoint("bel_1", 0.7, 3.0, "active", "source", at=NOW)
    assert BeliefPoint.from_doc(point.to_doc()) == point


# --- in the conversation -------------------------------------------------------------------------


async def test_the_general_view_brings_the_firmest_of_each_layer_with_its_tag() -> None:
    b, data, clock = book(surprise=0.0)
    firm = [
        ("Acho que ele se cobra demais.", "user", Evidence("explicit", "s", 1, at=clock())),
        (SELF, "self", Evidence("explicit", "s", 2, at=clock())),
        (WORLD, "world", Evidence("explicit", "s", 3, at=clock())),
    ]
    for text, about, ev in firm:
        belief, _ = b.create(text, "t", ev, about=about)
        belief.alpha, belief.beta, belief.status = 14.0, 1.0, "active"
        for day in range(3):  # three distinct sources
            belief.evidence_for.append(
                Evidence("explicit", f"x{day}", day, at=NOW + timedelta(days=day))
            )
            belief.evidence_for.append(Evidence("source", domain=f"d{day}.org", at=NOW))
        data.beliefs.save(belief)
    bind_session("sessao-camadas")
    config = MemoryV2Settings(
        enabled=True,
        persona=V2PersonaSettings(always_describe=False),
        opinions=V2OpinionSettings(always_on=1),
    )
    ctx = V2Context(data, FakeEmbedder(), config=config, clock=clock)
    block = dict(await ctx.preview("zzz xyzzy"))["opinioes"]
    assert "(visão geral) tenho bastante certeza" in block or "(visão geral)" in block
    assert "(sobre você mesma)" in block and "(sobre o mundo)" in block
    assert "diga com clareza se concorda ou discorda" in block
