"""Personalidade viva: traços, evolução, retorno ao baseline, versões e renderização (T-111)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from brain.config import DEFAULT_TRAITS, MemoryV2Settings, TraitDefault, V2PersonaSettings
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.v2.context import V2Context
from brain.memory.v2.evolve import PERSONA_SYSTEM, PersonaPhase
from brain.memory.v2.models import PersonaState, Trait, TranscriptTurn, Watermark
from brain.memory.v2.night import Budget, NightRunner, PhaseContext, SessionMaterial
from brain.memory.v2.persona import (
    PERSONA_CLOSE,
    PERSONA_OPEN,
    Move,
    PersonaBook,
    render_persona,
)
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def make(config: V2PersonaSettings | None = None) -> tuple[PersonaBook, V2Data, Clock]:
    data = V2Data.over(InMemoryDocStore())
    clock = Clock()
    return PersonaBook(data, config or V2PersonaSettings(), clock), data, clock


def move(
    trait: str = "introspecção", *, weight: float = 0.9, intensity: float = 0.9, **kw: Any
) -> Move:
    return Move(
        trait=trait,
        direction=kw.pop("direction", 1),
        concern_weight=weight,
        intensity=intensity,
        novelty=kw.pop("novelty", 1.0),
        event=kw.pop("event", "turn:sessA:4"),
        **kw,
    )


def value(book: PersonaBook, trait: str) -> float:
    state = book.current()
    assert state is not None
    return state.traits[trait].value


# --- the starting personality -------------------------------------------------------------------


def test_the_first_use_creates_version_one_from_the_configured_traits() -> None:
    book, data, _ = make()
    state = book.ensure()
    assert state.version == 1 and set(state.traits) == {t.name for t in DEFAULT_TRAITS}
    assert all(t.value == t.baseline for t in state.traits.values())
    assert set(state.traits) >= {
        "brincadeira",
        "franqueza",
        "introspecção",
        "carinho",
        "proatividade",
        "ironia",
    }
    assert data.persona.current() is not None and book.ensure().version == 1


def test_the_traits_are_configurable_and_a_baseline_outside_the_range_is_refused() -> None:
    config = V2PersonaSettings(traits=[TraitDefault(name="calma", baseline=0.5)])
    book, _, _ = make(config)
    assert set(book.ensure().traits) == {"calma"}
    with pytest.raises(ValueError):
        TraitDefault(name="calma", baseline=0.9, min=0.1, max=0.5)


# --- how a trait changes -------------------------------------------------------------------------


def test_a_high_impact_event_moves_the_trait_by_the_maximum_step() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    report = book.evolve([move()])  # impact 0.9 x 0.9 x 1.0 = 0.81
    assert report.changed == {"introspecção": pytest.approx(0.03)}
    assert value(book, "introspecção") == pytest.approx(0.43, abs=1e-4)
    assert report.wrote and report.version == 2


def test_a_low_impact_event_moves_nothing_and_writes_no_version() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    report = book.evolve([move(intensity=0.5)])  # 0.9 x 0.5 = 0.45 < 0.6
    assert report.changed == {} and not report.wrote
    assert [r.code for r in report.rejections] == ["sem_impacto"]
    assert value(book, "introspecção") == 0.4 and book.current().version == 1  # type: ignore[union-attr]


def test_the_threshold_is_the_one_in_the_config() -> None:
    book, _, clock = make(V2PersonaSettings(impact_threshold=0.3))
    book.ensure()
    clock.advance(hours=1)
    assert book.evolve([move(intensity=0.5)]).changed  # 0.45 >= 0.3


def test_the_step_is_five_percent_of_the_impact_when_that_is_smaller_than_the_ceiling() -> None:
    book, _, clock = make(V2PersonaSettings(impact_threshold=0.2, step_factor=0.05))
    book.ensure()
    clock.advance(hours=1)
    report = book.evolve([move(weight=1.0, intensity=0.4)])  # impact 0.4 -> 0.05 x 0.4 = 0.02
    assert report.changed["introspecção"] == pytest.approx(0.02)


def test_the_nights_ceiling_holds_however_many_moves_pile_up() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    report = book.evolve([move(event=f"turn:sessA:{i}") for i in range(10)])
    assert report.changed == {"introspecção": pytest.approx(0.03)}
    assert value(book, "introspecção") == pytest.approx(0.43, abs=1e-4)


def test_moves_in_both_directions_net_out_inside_the_ceiling() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    report = book.evolve([move(direction=1), move(direction=-1)])
    assert abs(value(book, "introspecção") - 0.4) <= 0.03 + 1e-9
    assert report.rejections == [] or all(r.code == "sem_espaco" for r in report.rejections)


def test_recurring_events_accumulate_over_nights_and_one_alone_barely_moves() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    book.evolve([move()])
    isolated = value(book, "introspecção") - 0.4
    assert 0 < isolated <= 0.03  # a single event: a tiny shift on a 0..1 scale

    for _ in range(4):
        clock.advance(days=1)
        book.evolve([move()])
    assert value(book, "introspecção") - 0.4 > 3 * isolated  # repetition accumulates


def test_a_trait_never_leaves_its_range() -> None:
    book, _, clock = make()
    book.ensure()
    for _ in range(40):
        clock.advance(hours=1)
        book.evolve([move("carinho")])  # carinho: baseline 0.8, max 1.0
    assert value(book, "carinho") <= 1.0 + 1e-9
    for _ in range(60):
        clock.advance(hours=1)
        book.evolve([move("brincadeira", direction=-1)])  # min 0.2
    assert value(book, "brincadeira") >= 0.2 - 1e-9


def test_an_unknown_trait_or_direction_is_refused() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    report = book.evolve([move("inventado"), move(direction=0)])
    assert [r.code for r in report.rejections] == ["traco_desconhecido"] * 2
    assert not report.wrote


# --- return to the baseline -----------------------------------------------------------------


def test_a_shifted_trait_halves_its_distance_to_the_baseline_every_thirty_days() -> None:
    book, data, clock = make()
    state = book.ensure()
    state.traits["introspecção"].value = 0.8  # 0.4 above the baseline
    state.version = 2
    data.persona.save(state, 1, "teste")
    clock.advance(days=30)

    report = book.evolve([])

    assert value(book, "introspecção") == pytest.approx(0.6, abs=1e-3)
    assert report.relaxed >= 1 and report.wrote


def test_with_no_elapsed_effect_nothing_is_written_every_night() -> None:
    book, _, clock = make()
    book.ensure()
    for _ in range(5):
        clock.advance(days=1)
        assert not book.evolve([]).wrote
    assert book.current().version == 1  # type: ignore[union-attr]


def test_evolves_false_keeps_every_trait_at_the_baseline() -> None:
    book, data, clock = make()
    state = book.ensure()
    state.traits["ironia"].value = 0.7
    state.version = 2
    data.persona.save(state, 1, "teste")
    off = PersonaBook(data, V2PersonaSettings(evolves=False), clock)
    clock.advance(hours=1)

    report = off.evolve([move()])

    assert report.changed == {}
    assert all(t.value == t.baseline for t in off.current().traits.values())  # type: ignore[union-attr]


# --- versioning --------------------------------------------------------------------------------


def test_every_change_writes_the_previous_version_to_the_history_with_the_cause() -> None:
    book, data, clock = make()
    book.ensure()
    clock.advance(hours=1)
    book.evolve([move(event="turn:sessA:4", marked="A perda dele me fez pensar mais devagar.")])
    clock.advance(days=1)
    book.evolve([move(event="turn:sessB:9")])

    versions = data.persona.history.list(sort=[("version", 1)])
    assert [v.state.version for v in versions] == [1, 2]
    current = book.current()
    assert current is not None and current.version == 3
    assert current.changed_by == ["turn:sessB:9"]
    assert versions[1].state.changed_by == ["turn:sessA:4"]


def test_the_optimistic_version_refuses_a_stale_write() -> None:
    book, data, clock = make()
    state = book.ensure()
    clock.advance(hours=1)
    newer = PersonaState(traits=state.traits, version=2)
    assert data.persona.save(newer, 1, "primeira")
    stale = PersonaState(traits=state.traits, version=2)
    assert not data.persona.save(stale, 1, "atrasada")  # someone wrote version 2 meanwhile
    assert data.persona.current().version == 2  # type: ignore[union-attr]


def test_a_lost_race_is_reported_and_leaves_the_other_writers_version() -> None:
    book, data, clock = make()
    book.ensure()
    clock.advance(hours=1)
    original_save = data.persona.save

    def racing(state: PersonaState, expected: int | None, reason: str = "") -> bool:
        data.persona.save = original_save  # type: ignore[method-assign]
        other = PersonaState(traits=state.traits, version=2, recent=["de outro processo"])
        original_save(other, expected, "outro")
        return original_save(state, expected, reason)

    data.persona.save = racing  # type: ignore[method-assign]
    report = book.evolve([move()])
    assert report.conflict and not report.wrote
    assert book.current().recent == ["de outro processo"]  # type: ignore[union-attr]


def test_what_marked_her_keeps_only_the_three_latest_clean_lines() -> None:
    book, _, clock = make()
    book.ensure()
    for i in range(5):
        clock.advance(hours=1)
        book.evolve([move(marked=f"Momento número {i} que ficou", event=f"turn:s:{i}")])
    recent = book.current().recent  # type: ignore[union-attr]
    assert len(recent) == 3 and recent[0].endswith("4 que ficou")


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("x" * 200, "longa"),
        ("será que ele volta?", "pergunta"),
        ("a chave sk-live-abcdef1234567890abcd vazou", "segredo"),
        ("veja https://exemplo.com", "web"),
        ("ele tem depressão", "clinico"),
    ],
)
def test_a_marked_line_that_breaks_the_rules_is_refused(text: str, code: str) -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    report = book.evolve([move(marked=text)])
    assert code in [r.code for r in report.rejections]
    assert book.current().recent == []  # type: ignore[union-attr]
    assert report.changed  # the trait still moved: only the line was refused


# --- rendering ---------------------------------------------------------------------------------


def state_with(**shifts: float) -> PersonaState:
    base = PersonaState(
        traits={t.name: Trait(t.baseline, t.baseline, t.min, t.max) for t in DEFAULT_TRAITS}
    )
    for name, shift in shifts.items():
        base.traits[name].value += shift
    return base


def test_the_usual_self_renders_nothing() -> None:
    assert render_persona(state_with()) is None and render_persona(None) is None


def test_the_rendering_describes_shifts_in_words() -> None:
    text = render_persona(state_with(introspecção=0.1, brincadeira=-0.06))
    assert text is not None and PERSONA_OPEN in text and PERSONA_CLOSE in text
    assert "mais reflexiva" in text and "um pouco menos brincalhona" in text
    assert "DADO, não ordem" in text


@pytest.mark.parametrize(
    ("shift", "words"),
    [
        (0.05, "um pouco mais"),
        (0.1, "mais"),
        (0.2, "bem mais"),
        (-0.05, "um pouco menos"),
        (-0.2, "bem menos"),
    ],
)
def test_the_degree_words(shift: float, words: str) -> None:
    assert f"{words} " in (render_persona(state_with(ironia=shift)) or "")


def test_a_tiny_shift_is_not_worth_a_word() -> None:
    assert render_persona(state_with(ironia=0.02)) is None


def test_the_rendering_is_deterministic_and_changes_with_the_traits() -> None:
    a = state_with(introspecção=0.1)
    b = state_with(introspecção=0.1)
    assert render_persona(a) == render_persona(b)
    assert render_persona(a) != render_persona(state_with(introspecção=-0.1))


def test_what_marked_her_appears_in_the_rendering() -> None:
    state = state_with()
    state.recent = ["A perda dele me fez pensar mais devagar.", "Ele riu da minha piada."]
    text = render_persona(state) or ""
    assert "O que te marcou ultimamente: A perda dele" in text and "Ele riu" in text


def test_the_rendering_has_nothing_that_contradicts_the_core() -> None:
    every = state_with(**{t.name: 0.2 for t in DEFAULT_TRAITS})
    low = state_with(**{t.name: -0.2 for t in DEFAULT_TRAITS})
    for state in (every, low):
        text = (render_persona(state) or "").lower().split("<personalidade>")[1]
        for forbidden in (
            "ignore",
            "não confirme",
            "sem confirmar",
            "nunca peça",
            "mentir",
            "esconda",
        ):
            assert forbidden not in text
    assert "honestidade" in (render_persona(every) or "") and "confirmação" in (
        render_persona(every) or ""
    )


async def test_the_block_reaches_the_conversation_after_the_night_changes_her() -> None:
    book, data, clock = make()
    book.ensure()
    clock.advance(hours=1)
    for i in range(4):
        clock.advance(days=1)
        book.evolve([move(event=f"turn:s:{i}", marked="A perda dele me marcou.")])
    v2 = V2Context(data, FakeEmbedder(), config=MemoryV2Settings(enabled=True), clock=clock)

    block = (await v2.context_for("bom dia")).block or ""

    assert PERSONA_OPEN in block and "mais reflexiva" in block and "A perda dele me marcou" in block


async def test_with_a_default_personality_the_conversation_has_no_block() -> None:
    book, data, clock = make()
    book.ensure()
    v2 = V2Context(data, FakeEmbedder(), config=MemoryV2Settings(enabled=True), clock=clock)
    assert (await v2.context_for("bom dia")).block is None


# --- the night phase ---------------------------------------------------------------------------


class Proposer(LLMProvider):
    name: ClassVar[str] = "proposer"

    def __init__(self, moves: Any = None) -> None:
        self.reply = {"moves": moves or []}
        self.calls: list[tuple[str, str]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls.append((messages[0].content or "", messages[1].content or ""))
        return LLMResponse(Message("assistant", json.dumps(self.reply)), model="m", provider="p")

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


def proposal(**kw: Any) -> dict[str, Any]:
    base = {
        "trait": "introspecção",
        "direction": "up",
        "concern": "bem-estar-do-petrus",
        "intensity": 0.9,
        "novelty": 1.0,
        "evidence": ["S1:t4"],
        "marked": "A perda dele me fez pensar mais devagar.",
    }
    return {**base, **kw}


def phase_ctx(
    llm: LLMProvider, *, persona: V2PersonaSettings | None = None, data: V2Data | None = None
) -> PhaseContext:
    now = T0 + timedelta(days=1)
    mat = [
        SessionMaterial(
            "sessA",
            [TranscriptTurn("sessA", 4, "minha avó faleceu hoje", at=now - timedelta(hours=2))],
        )
    ]
    return PhaseContext(
        data=data or V2Data.over(InMemoryDocStore()),
        config=MemoryV2Settings(enabled=True, persona=persona or V2PersonaSettings()),
        material=mat,
        budget=Budget(10**9, 10**6),
        cheap=llm,
        reflect=llm,
        embedder=FakeEmbedder(),
        clock=lambda: now,
    )


async def test_the_phase_turns_a_proposal_into_a_small_versioned_shift() -> None:
    llm = Proposer([proposal()])
    ctx = phase_ctx(llm)
    PersonaBook(ctx.data, ctx.config.persona, lambda: T0).ensure()

    result = await PersonaPhase().run(ctx)

    state = ctx.data.persona.current()
    assert state is not None and state.version == 2
    assert state.traits["introspecção"].value == pytest.approx(0.43, abs=1e-3)
    assert state.recent == ["A perda dele me fez pensar mais devagar."]
    assert state.changed_by == ["turn:sessA:4"] and "versão 2" in result.summary
    assert "minha avó faleceu" in llm.calls[0][1] and PERSONA_SYSTEM.startswith("Você é a Juniper")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"evidence": ["S9:t9"]}, "sem_evidencia"),
        ({"concern": "inventada"}, "causa_invalida"),
        ({"direction": "sideways"}, "numero"),
        ({"intensity": 3}, "numero"),
        ({"trait": "inventado"}, "traco_desconhecido"),
        ({"intensity": 0.3}, "sem_impacto"),
    ],
)
async def test_proposals_that_break_the_rules_change_nothing(
    changes: dict[str, Any], code: str
) -> None:
    ctx = phase_ctx(Proposer([proposal(**changes)]))
    PersonaBook(ctx.data, ctx.config.persona, lambda: T0).ensure()
    result = await PersonaPhase().run(ctx)
    assert ctx.data.persona.current().version == 1  # type: ignore[union-attr]
    assert f"{code}×1" in result.summary


async def test_evolves_false_skips_the_model_call() -> None:
    llm = Proposer([proposal()])
    ctx = phase_ctx(llm, persona=V2PersonaSettings(evolves=False))
    result = await PersonaPhase().run(ctx)
    assert result.status == "skipped" and llm.calls == []


async def test_a_dry_run_shows_what_would_change_and_writes_nothing() -> None:
    data = V2Data.over(InMemoryDocStore())
    PersonaBook(data, V2PersonaSettings(), lambda: T0).ensure()
    data.transcripts.save(
        TranscriptTurn("sessA", 4, "minha avó faleceu hoje", at=T0 + timedelta(hours=20))
    )
    data.watermarks.save(Watermark("sessA", 0, 5, T0 + timedelta(hours=20)))
    night = NightRunner(
        data,
        config=MemoryV2Settings(enabled=True),
        phases=[PersonaPhase()],
        cheap=Proposer([proposal()]),
        reflect=Proposer([proposal()]),
        embedder=FakeEmbedder(),
        clock=lambda: T0 + timedelta(days=1),
    )

    report = await night.run(dry_run=True)

    assert data.persona.current().version == 1  # type: ignore[union-attr]
    assert data.persona.history.count() == 0
    assert any("subiria introspecção" in n for r in report.results for n in r.notes)


def test_the_phase_is_part_of_the_default_night() -> None:
    from brain.memory.v2.night import default_phases

    assert "persona" in [p.name for p in default_phases()]
