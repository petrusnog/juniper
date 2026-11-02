"""O sonho V2: crenças do mundo, crenças sobre si, REM e poda (T-178, ADR-030)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from brain.config import MemoryV2Settings
from brain.memory.extraction import MemoryExtractor
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoVectorStore
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.models import Interest, PersonaState, StudyNote, Trait
from brain.memory.v2.night import (
    CHANGE_PHASES,
    Budget,
    NightRunner,
    PhaseContext,
    default_phases,
)
from brain.memory.v2.persona import PersonaBook
from brain.memory.v2.sleep import (
    EmotionRestPhase,
    PrunePhase,
    SelfBeliefPhase,
    WorldBeliefPhase,
)
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.memory.vector import Memory
from tests.test_extraction import MODEL, FakeEmbedder
from tests.test_vector_store import FakeConnection

NOW = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
CLAIM = "A psicodelia surgiu nos anos 60."


def ctx_over(data: V2Data, memory: Any = None, **kw: Any) -> PhaseContext:
    return PhaseContext(
        data=data,
        config=MemoryV2Settings(enabled=True),
        material=[],
        budget=Budget(10**9, 10**6),
        embedder=FakeEmbedder(),
        memory=memory,
        clock=lambda: kw.pop("now", NOW),
    )


def web_note(
    data: V2Data, domains: list[str], uncertainty: str = "baixa", text: str = CLAIM, **kw: Any
) -> StudyNote:
    interest = Interest("psicodelia")
    data.interests.save(interest)
    note = StudyNote(
        interest.id,
        text,
        uncertainty=uncertainty,
        source="web",
        url=f"https://{domains[0]}/x",
        domains=domains,
        fetched_at=kw.pop("fetched_at", NOW),
        **kw,
    )
    data.notes.save(note)
    return note


# --- the order of the night ----------------------------------------------------------------------


def test_the_night_goes_nrem_then_rem_then_prune() -> None:
    names = [p.name for p in default_phases()]
    assert names == [
        "observe", "reflect", "affect", "interests", "study", "world", "persona", "self",
        "refine", "rem", "prune",
    ]  # fmt: skip
    assert set(CHANGE_PHASES) <= set(names)


# --- the world -----------------------------------------------------------------------------------


async def test_a_note_two_domains_agree_on_becomes_a_world_belief_once() -> None:
    data = V2Data.over(InMemoryDocStore())
    web_note(data, ["a.org", "b.org"])
    web_note(data, ["c.org"], "média", text="Algo que só uma fonte disse.")
    first = await WorldBeliefPhase().run(ctx_over(data))
    again = await WorldBeliefPhase().run(ctx_over(data))
    (belief,) = data.beliefs.list()
    assert belief.about == "world" and belief.statement == CLAIM
    assert {e.domain for e in belief.evidence_for} == {"a.org", "b.org"}
    assert belief.status == "active" and "1 crença(s) nova(s) sobre o mundo" in first.summary
    assert "0 crença(s) nova(s)" in again.summary and len(data.beliefs.list()) == 1
    assert any("sobre o mundo" in n for n in first.notes)


async def test_an_expired_or_uncertain_note_teaches_no_belief() -> None:
    data = V2Data.over(InMemoryDocStore())
    web_note(data, ["a.org", "b.org"], fetched_at=NOW - timedelta(days=400))
    web_note(data, ["a.org", "b.org"], "alta", text="Duas versões em conflito.")
    await WorldBeliefPhase().run(ctx_over(data))
    assert data.beliefs.list() == []


# --- herself -------------------------------------------------------------------------------------


async def test_a_feeling_that_kept_coming_back_becomes_a_belief_about_herself() -> None:
    data = V2Data.over(InMemoryDocStore())
    book = AffectBook(data, clock=lambda: NOW)
    for hours in (2, 20, 30, 40):
        book.record("irritação", -0.6, 0.7, ["turn:s:1"], occurred_at=NOW - timedelta(hours=hours))
    result = await SelfBeliefPhase().run(ctx_over(data))
    (belief,) = data.beliefs.list()
    assert (
        belief.about == "self"
        and belief.statement == "Tenho andado com irritação nos últimos dias."
    )
    assert all(
        e.kind == "self_observation" and e.ref.startswith("affect:") for e in belief.evidence_for
    )
    assert "1 crença(s) nova(s) sobre mim" in result.summary
    again = await SelfBeliefPhase().run(ctx_over(data))
    assert len(data.beliefs.list()) == 1 and "0 evidência(s) somada(s)" in again.summary


async def test_one_bad_hour_is_not_a_pattern_and_a_manual_mood_is_not_a_feeling() -> None:
    data = V2Data.over(InMemoryDocStore())
    book = AffectBook(data, clock=lambda: NOW)
    book.record("irritação", -0.6, 0.9, ["turn:s:1"], occurred_at=NOW - timedelta(hours=1))
    book.force("hostil", "forte")
    await SelfBeliefPhase().run(ctx_over(data))
    assert data.beliefs.list() == []


async def test_a_trait_that_moved_becomes_a_belief_and_the_opposite_one_is_challenged() -> None:
    data = V2Data.over(InMemoryDocStore())
    persona = PersonaBook(data, clock=lambda: NOW)
    state = persona.ensure()
    state.traits["intelecto"].value = state.traits["intelecto"].baseline + 0.15
    data.persona.save(PersonaState(traits=state.traits, version=2), 1, "teste")
    await SelfBeliefPhase().run(ctx_over(data))
    (more,) = data.beliefs.list()
    assert more.statement == "Tenho andado mais curiosa." and more.about == "self"

    later = persona.current()
    assert later is not None
    later.traits["intelecto"].value = later.traits["intelecto"].baseline - 0.15
    data.persona.save(PersonaState(traits=later.traits, version=3), 2, "teste")
    result = await SelfBeliefPhase().run(ctx_over(data))
    beliefs = {b.statement: b for b in data.beliefs.list()}
    assert set(beliefs) == {"Tenho andado mais curiosa.", "Tenho andado menos curiosa."}
    assert beliefs["Tenho andado mais curiosa."].evidence_against  # contested by the new state
    assert "1 contestada(s)" in result.summary


async def test_an_interest_she_likes_becomes_self_knowledge() -> None:
    data = V2Data.over(InMemoryDocStore())
    data.interests.save(Interest("synthwave", stance="like", intensity=0.8))
    data.interests.save(Interest("jardinagem", stance="undecided", intensity=0.9))
    await SelfBeliefPhase().run(ctx_over(data))
    assert [b.statement for b in data.beliefs.list()] == ["Gosto de synthwave."]


# --- REM and the pruning -------------------------------------------------------------------------


def memory_with(feelings: list[dict[str, Any]]) -> tuple[LongTermMemory, Memory]:
    store = MongoVectorStore(FakeConnection(), embedding_model=MODEL)
    extractor = MemoryExtractor(None, FakeEmbedder(), store, None)  # type: ignore[arg-type]
    memory = LongTermMemory(store, FakeEmbedder(), extractor, None)  # type: ignore[arg-type]
    item = Memory(
        text="Petrus perdeu a avó.",
        type="fact",
        embedding=FakeEmbedder().embed("avó"),
        embedding_model=MODEL,
        status_reflexao="refinada",
        sentimento=feelings,
    )
    store.add(item)
    return memory, item


async def test_the_charge_of_a_memory_cools_but_the_fact_and_the_floor_stay() -> None:
    memory, item = memory_with(
        [
            {"kind": "tristeza", "valence": -0.8, "intensity": 0.8},
            {"kind": "carinho", "valence": 0.5, "intensity": 0.16},
        ]
    )
    data = V2Data.over(InMemoryDocStore())
    store = memory.extractor.store
    for _ in range(3):
        result = await EmotionRestPhase().run(ctx_over(data, memory))
    kept = store.get(item.id)
    assert kept is not None and kept.text == "Petrus perdeu a avó."  # the fact never changes
    sad, soft = kept.sentimento
    assert sad["intensity"] == pytest.approx(0.8 * 0.9**3, abs=1e-3) and sad["valence"] == -0.8
    assert soft["intensity"] == pytest.approx(0.15)  # never below the floor
    assert "lembrança(s)" in result.summary


async def test_a_dry_run_cools_nothing() -> None:
    memory, item = memory_with([{"kind": "tristeza", "valence": -0.8, "intensity": 0.8}])
    ctx = ctx_over(V2Data.over(InMemoryDocStore()), memory)
    ctx.dry_run = True
    result = await EmotionRestPhase().run(ctx)
    assert result.changed == 1
    assert memory.extractor.store.get(item.id).sentimento[0]["intensity"] == 0.8  # type: ignore[union-attr]


async def test_without_refined_memory_the_rem_is_skipped() -> None:
    result = await EmotionRestPhase().run(ctx_over(V2Data.over(InMemoryDocStore())))
    assert result.status == "skipped"


async def test_the_pruning_takes_faded_feelings_and_web_notes_long_past_their_validity() -> None:
    data = V2Data.over(InMemoryDocStore())
    book = AffectBook(data, clock=lambda: NOW)
    book.record("tédio", -0.2, 0.05, ["turn:s:1"], occurred_at=NOW - timedelta(days=30))
    fresh = web_note(data, ["a.org"], "média", text="Nota nova.", volatility="atual")
    old = web_note(
        data, ["a.org"], "média", text="Nota velha.", volatility="atual",
        fetched_at=NOW - timedelta(days=30),
    )  # fmt: skip
    result = await PrunePhase().run(ctx_over(data))
    assert data.affects.count() == 0
    assert data.notes.get(fresh.id) is not None and data.notes.get(old.id) is None
    assert "1 nota(s) da web vencida(s)" in result.summary


# --- the diary -----------------------------------------------------------------------------------


async def test_the_diary_tells_how_she_changed() -> None:
    data = V2Data.over(InMemoryDocStore())
    web_note(data, ["a.org", "b.org"])
    runner = NightRunner(
        data,
        config=MemoryV2Settings(enabled=True),
        phases=[WorldBeliefPhase()],
        embedder=FakeEmbedder(),
        clock=lambda: NOW,
    )
    report = await runner.run()
    assert report.journal is not None
    assert "Como mudei:" in report.journal and "acharia (sobre o mundo)" in report.journal


def test_trait_values_exist_for_the_self_phase() -> None:
    assert Trait(0.5, 0.5, 0, 1).value == 0.5
