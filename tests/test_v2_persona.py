"""Personalidade viva: Big Five, estados, média móvel, maturação, versões e renderização
(T-111; algoritmo da V3 em T-176, ADR-030)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from brain.config import (
    DEFAULT_TRAITS,
    MemoryV2Settings,
    TraitDefault,
    V2KnowledgeSettings,
    V2PersonaSettings,
)
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.v2.context import V2Context
from brain.memory.v2.evolve import PERSONA_SYSTEM, PersonaPhase
from brain.memory.v2.models import PersonaState, Trait, TranscriptTurn, Watermark
from brain.memory.v2.night import Budget, NightRunner, PhaseContext, SessionMaterial
from brain.memory.v2.persona import (
    PERSONA_CLOSE,
    PERSONA_OPEN,
    PersonaBook,
    Sample,
    render_persona,
)
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
ASPECTS = {t.name for t in DEFAULT_TRAITS}


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


def sample(trait: str = "entusiasmo", value: float = 1.0, weight: float = 1.0, **kw: Any) -> Sample:
    return Sample(trait, value, weight, kw.pop("event", "turn:sessA:4"))


def value(book: PersonaBook, trait: str) -> float:
    state = book.current()
    assert state is not None
    return state.traits[trait].value


# --- the starting personality -------------------------------------------------------------------


def test_the_first_use_creates_version_one_with_the_big_five_aspects_and_irony() -> None:
    book, data, _ = make()
    state = book.ensure()
    assert state.version == 1 and set(state.traits) == ASPECTS and len(ASPECTS) == 11
    assert all(t.value == t.baseline for t in state.traits.values())
    assert {"volatilidade", "retraimento", "compaixão", "polidez", "diligência", "ordem"} <= ASPECTS
    assert {"entusiasmo", "assertividade", "abertura", "intelecto", "ironia"} <= ASPECTS
    assert data.persona.current() is not None and book.ensure().version == 1


def test_the_traits_are_configurable_and_a_baseline_outside_the_range_is_refused() -> None:
    config = V2PersonaSettings(traits=[TraitDefault(name="calma", baseline=0.5)])
    book, _, _ = make(config)
    assert set(book.ensure().traits) == {"calma"}
    with pytest.raises(ValueError):
        TraitDefault(name="calma", baseline=0.9, min=0.1, max=0.5)


# --- the migration from the V2 traits -----------------------------------------------------------


def v2_state() -> PersonaState:
    def t(value: float, base: float) -> Trait:
        return Trait(value, base, 0.1, 0.95)

    return PersonaState(
        traits={
            "entusiasmo": t(0.7, 0.6),
            "assertividade": t(0.75, 0.7),
            "intelecto": t(0.5, 0.4),
            "compaixão": t(0.9, 0.8),
            "diligência": t(0.7, 0.7),
            "ironia": t(0.55, 0.5),
        },
        recent=["Um momento que marcou."],
        version=4,
    )


def test_a_v2_personality_is_migrated_once_as_a_new_version_keeping_what_she_was() -> None:
    book, data, _ = make()
    data.persona.save(v2_state(), None, "v2")
    state = book.ensure()
    assert state.version == 5 and set(state.traits) == ASPECTS
    assert state.traits["entusiasmo"].value == pytest.approx(0.7)  # was brincadeira
    assert state.traits["entusiasmo"].baseline == pytest.approx(0.6)
    assert state.traits["compaixão"].value == pytest.approx(0.9)  # was carinho
    assert state.traits["assertividade"].value == pytest.approx(0.75)  # was franqueza
    assert state.traits["intelecto"].baseline == pytest.approx(0.4)  # was introspecção
    assert state.traits["ironia"].value == pytest.approx(0.55)  # same name
    assert state.traits["volatilidade"].value == state.traits["volatilidade"].baseline  # new
    assert state.recent == ["Um momento que marcou."]
    assert state.changed_by == ["migracao:big-five"]
    assert book.ensure().version == 5  # only once
    history = data.persona.history.list(sort=[("version", 1)])
    assert history[-1].state.version == 4 and "entusiasmo" in history[-1].state.traits


# --- how a trait changes -------------------------------------------------------------------------


def test_a_state_moves_the_trait_a_share_of_the_way_toward_it() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    book.evolve([sample("entusiasmo", 0.8)])  # from 0.6 toward 0.8: alpha 0.1 -> +0.02
    assert value(book, "entusiasmo") == pytest.approx(0.62, abs=1e-6)


def test_the_weighted_mean_of_several_states_is_the_target() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    book.evolve([sample(value=1.0, weight=3.0), sample(value=0.0, weight=1.0)])  # mean 0.75
    assert value(book, "entusiasmo") == pytest.approx(0.6 + 0.1 * 0.15, abs=1e-6)


def test_the_step_never_passes_the_nightly_ceiling() -> None:
    book, _, clock = make(V2PersonaSettings(alpha=1.0, max_step=0.05))
    book.ensure()
    clock.advance(hours=1)
    book.evolve([sample(value=1.0)])
    assert value(book, "entusiasmo") == pytest.approx(0.65, abs=1e-6)


def test_the_same_state_every_night_keeps_her_walking_but_inside_the_band() -> None:
    book, _, clock = make(V2PersonaSettings(alpha=0.5, max_step=0.05, maturation=0.0))
    book.ensure()
    for _ in range(30):
        clock.advance(days=1)
        book.evolve([sample("ironia", 1.0)])
    state = book.current()
    assert state is not None
    t = state.traits["ironia"]
    assert t.value == pytest.approx(t.baseline + 0.25, abs=1e-6)  # the band is +-0.25


def test_a_trait_never_leaves_its_range() -> None:
    config = V2PersonaSettings(
        alpha=1.0,
        max_step=0.2,
        max_band=1.0,
        traits=[TraitDefault(name="calma", baseline=0.9, max=0.95)],
    )
    book, _, clock = make(config)
    book.ensure()
    for _ in range(5):
        clock.advance(days=1)
        book.evolve([sample("calma", 1.0)])
    assert value(book, "calma") == pytest.approx(0.95)


def test_an_unknown_trait_or_an_invalid_state_is_refused() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(hours=1)
    report = book.evolve([sample("inventado"), sample(value=1.5), sample(weight=0.0)])
    assert [r.code for r in report.rejections] == ["traco_desconhecido"] * 3
    assert not report.wrote


def test_a_neutral_night_writes_nothing() -> None:
    book, _, clock = make()
    book.ensure()
    clock.advance(days=1)
    assert not book.evolve([sample("entusiasmo", 0.6)]).wrote  # exactly where she is


# --- maturation ----------------------------------------------------------------------------------


def test_the_baseline_walks_toward_the_trait_so_a_long_phase_becomes_who_she_is() -> None:
    book, _, clock = make(V2PersonaSettings(alpha=0.3, max_step=0.05, maturation=0.05))
    book.ensure()
    for _ in range(40):
        clock.advance(days=1)
        book.evolve([sample("entusiasmo", 0.2)])
    state = book.current()
    assert state is not None
    t = state.traits["entusiasmo"]
    assert t.baseline < 0.5 and t.value < t.baseline + 0.01  # the usual self moved down


def test_with_no_states_the_trait_stays_and_the_baseline_catches_up_slowly() -> None:
    book, data, clock = make(V2PersonaSettings(maturation=0.1))
    state = book.ensure()
    state.traits["ironia"].value = 0.7  # she is away from her usual self
    data.persona.save(PersonaState(traits=state.traits, version=2, updated_at=T0), 1, "teste")
    clock.advance(days=1)
    book.evolve([])
    t = book.current().traits["ironia"]  # type: ignore[union-attr]
    assert t.value == pytest.approx(0.7) and t.baseline == pytest.approx(0.52, abs=1e-6)


def test_evolves_false_keeps_every_trait_at_the_baseline() -> None:
    book, data, clock = make(V2PersonaSettings(evolves=False))
    state = book.ensure()
    state.traits["ironia"].value = 0.8
    data.persona.save(PersonaState(traits=state.traits, version=2), 1, "teste")
    clock.advance(days=1)
    report = book.evolve([sample("ironia", 1.0)])
    assert report.wrote
    assert value(book, "ironia") == pytest.approx(0.5)


# --- versioning --------------------------------------------------------------------------------


def test_every_change_writes_the_previous_version_to_the_history_with_the_cause() -> None:
    book, data, clock = make()
    book.ensure()
    clock.advance(hours=1)
    book.evolve([sample(event="turn:sessA:4")], ["A perda dele me fez pensar mais devagar."])
    clock.advance(days=1)
    book.evolve([sample(event="turn:sessB:9")])

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
    report = book.evolve([sample()])
    assert report.conflict and not report.wrote
    assert book.current().recent == ["de outro processo"]  # type: ignore[union-attr]


def test_what_marked_her_keeps_only_the_three_latest_clean_lines() -> None:
    book, _, clock = make()
    book.ensure()
    for i in range(5):
        clock.advance(hours=1)
        book.evolve([sample(event=f"turn:s:{i}")], [f"Momento número {i} que ficou"])
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
    report = book.evolve([sample()], [text])
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


def test_the_usual_self_still_says_how_she_is_today_but_no_drift() -> None:
    text = render_persona(state_with()) or ""
    assert "Seu jeito hoje:" in text and "Comparada ao seu jeito de sempre" not in text
    assert render_persona(None) is None


def test_with_always_describe_off_the_usual_self_renders_nothing() -> None:
    assert render_persona(state_with(), always=False) is None
    drifted = render_persona(state_with(intelecto=0.1), always=False) or ""
    assert "Seu jeito hoje" not in drifted and "mais curiosa" in drifted


def test_the_rendering_describes_shifts_in_words() -> None:
    text = render_persona(state_with(intelecto=0.1, entusiasmo=-0.06))
    assert text is not None and PERSONA_OPEN in text and PERSONA_CLOSE in text
    assert "mais curiosa" in text and "um pouco menos entusiasmada" in text
    assert "deixe que ele guie o tom" in text


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


def test_a_tiny_shift_is_not_worth_a_drift_word() -> None:
    assert "Comparada ao seu jeito de sempre" not in (render_persona(state_with(ironia=0.02)) or "")
    assert "Comparada ao seu jeito de sempre" in (render_persona(state_with(ironia=0.03)) or "")


@pytest.mark.parametrize(
    ("value", "grade"),
    [
        (0.9, "muito"),
        (0.7, "bastante"),
        (0.5, "moderadamente"),
        (0.3, "pouco"),
        (0.1, "quase nada"),
    ],
)
def test_every_trait_is_graded_in_words_from_its_value(value: float, grade: str) -> None:
    state = state_with()
    state.traits["ironia"] = Trait(value, 0.5, 0.0, 1.0)
    text = render_persona(state) or ""
    assert f"{grade} irônica" in text


def test_the_traits_are_listed_from_the_strongest_to_the_weakest() -> None:
    text = render_persona(state_with(compaixão=0.15)) or ""  # carinho 0.95: muito
    today = next(line for line in text.splitlines() if line.startswith("Seu jeito hoje"))
    assert today.index("compassiva") < today.index("assertiva") < today.index("curiosa")


def test_the_rendering_is_deterministic_and_changes_with_the_traits() -> None:
    a = state_with(intelecto=0.1)
    b = state_with(intelecto=0.1)
    assert render_persona(a) == render_persona(b)
    assert render_persona(a) != render_persona(state_with(intelecto=-0.1))


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
        book.evolve([sample("intelecto", 1.0, event=f"turn:s:{i}")], ["A perda dele me marcou."])
    v2 = V2Context(data, FakeEmbedder(), config=MemoryV2Settings(enabled=True), clock=clock)

    block = (await v2.context_for("bom dia")).block or ""

    assert PERSONA_OPEN in block and "mais curiosa" in block and "A perda dele me marcou" in block


async def test_with_a_default_personality_the_conversation_describes_her_but_adds_no_drift() -> (
    None
):
    book, data, clock = make()
    book.ensure()
    v2 = V2Context(data, FakeEmbedder(), config=MemoryV2Settings(enabled=True), clock=clock)
    block = (await v2.context_for("bom dia")).block or ""
    assert PERSONA_OPEN in block and "Seu jeito hoje:" in block
    assert "Comparada ao seu jeito de sempre" not in block


async def test_before_the_first_dream_she_already_describes_her_starting_self_without_writing() -> (
    None
):
    data = V2Data.over(InMemoryDocStore())
    v2 = V2Context(data, FakeEmbedder(), config=MemoryV2Settings(enabled=True), clock=Clock())
    block = (await v2.context_for("bom dia")).block or ""
    assert "Seu jeito hoje: muito compassiva" in block
    assert data.persona.current() is None  # reading never creates the personality


async def test_with_always_describe_off_a_default_personality_adds_no_block() -> None:
    book, data, clock = make()
    book.ensure()
    off = MemoryV2Settings(
        enabled=True,
        persona=V2PersonaSettings(always_describe=False),
        knowledge=V2KnowledgeSettings(strict=False),
    )
    v2 = V2Context(data, FakeEmbedder(), config=off, clock=clock)
    assert (await v2.context_for("bom dia")).block is None


# --- the night phase ---------------------------------------------------------------------------


class Proposer(LLMProvider):
    name: ClassVar[str] = "proposer"

    def __init__(self, conversations: Any = None, marked: Any = None) -> None:
        self.reply = {"conversas": conversations or [], "marcou": marked or []}
        self.calls: list[tuple[str, str]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls.append((messages[0].content or "", messages[1].content or ""))
        return LLMResponse(Message("assistant", json.dumps(self.reply)), model="m", provider="p")

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


def rating(**kw: Any) -> dict[str, Any]:
    base = {"traco": "intelecto", "valor": 0.9, "evidencia": ["S1:t4"], "reacao": 0}
    return {**base, **kw}


def day(*states: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"conversa": "S1", "estados": list(states)}]


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


async def test_the_phase_turns_a_rated_state_into_a_small_versioned_shift() -> None:
    llm = Proposer(day(rating()), ["A perda dele me fez pensar mais devagar."])
    ctx = phase_ctx(llm)
    PersonaBook(ctx.data, ctx.config.persona, lambda: T0).ensure()

    result = await PersonaPhase().run(ctx)

    state = ctx.data.persona.current()
    assert state is not None and state.version == 2
    assert state.traits["intelecto"].value == pytest.approx(0.63, abs=1e-3)  # 0.6 + 0.1 * 0.3
    assert state.recent == ["A perda dele me fez pensar mais devagar."]
    assert state.changed_by == ["turn:sessA:4"] and "versão 2" in result.summary
    assert "minha avó faleceu" in llm.calls[0][1] and PERSONA_SYSTEM.startswith("Você é a Juniper")


async def test_how_the_user_reacted_pushes_the_expressed_state() -> None:
    liked = Proposer(day(rating(traco="ironia", valor=0.5, reacao=1)))
    ctx = phase_ctx(liked)
    PersonaBook(ctx.data, ctx.config.persona, lambda: T0).ensure()
    await PersonaPhase().run(ctx)
    assert ctx.data.persona.current().traits["ironia"].value > 0.5  # type: ignore[union-attr]
    complained = Proposer(day(rating(traco="ironia", valor=0.5, reacao=-1)))
    ctx = phase_ctx(complained)
    PersonaBook(ctx.data, ctx.config.persona, lambda: T0).ensure()
    await PersonaPhase().run(ctx)
    assert ctx.data.persona.current().traits["ironia"].value < 0.5  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"evidencia": ["S9:t9"]}, "sem_evidencia"),
        ({"evidencia": ["S2:t4"]}, "sem_evidencia"),  # a real turn, but of another conversation
        ({"evidencia": []}, "sem_evidencia"),
        ({"traco": "inventado"}, "traco_desconhecido"),
        ({"valor": 3}, "numero"),
        ({"valor": "alto"}, "numero"),
    ],
)
async def test_ratings_that_break_the_rules_change_nothing(
    changes: dict[str, Any], code: str
) -> None:
    ctx = phase_ctx(Proposer(day(rating(**changes))))
    PersonaBook(ctx.data, ctx.config.persona, lambda: T0).ensure()
    result = await PersonaPhase().run(ctx)
    assert ctx.data.persona.current().version == 1  # type: ignore[union-attr]
    assert f"{code}×1" in result.summary


async def test_evolves_false_skips_the_model_call() -> None:
    llm = Proposer(day(rating()))
    ctx = phase_ctx(llm, persona=V2PersonaSettings(evolves=False))
    result = await PersonaPhase().run(ctx)
    assert result.status == "skipped" and llm.calls == []


async def test_the_days_mood_is_one_more_sample() -> None:
    """A day of irritation leaves a mark on the personality even without a rating."""
    from brain.memory.v2.affect import AffectBook

    ctx = phase_ctx(Proposer())
    PersonaBook(ctx.data, ctx.config.persona, lambda: T0).ensure()
    now = T0 + timedelta(days=1)
    book = AffectBook(ctx.data, ctx.config.affect, lambda: now)
    for hours in range(0, 24, 3):
        book.record("irritação", -0.8, 0.9, ["turn:s:1"], occurred_at=now - timedelta(hours=hours))
    await PersonaPhase().run(ctx)
    state = ctx.data.persona.current()
    assert state is not None
    assert state.traits["assertividade"].value > 0.7  # irritation is dominant
    assert state.traits["entusiasmo"].value < 0.6 or state.traits["compaixão"].value < 0.8


async def test_a_dry_run_shows_what_would_change_and_writes_nothing() -> None:
    data = V2Data.over(InMemoryDocStore())
    PersonaBook(data, V2PersonaSettings(), lambda: T0).ensure()
    data.transcripts.save(
        TranscriptTurn("sessA", 4, "minha avó faleceu hoje", at=T0 + timedelta(hours=20))
    )
    data.watermarks.save(Watermark("sessA", 0, 5, T0 + timedelta(hours=20)))
    llm = Proposer(day(rating()))
    night = NightRunner(
        data,
        config=MemoryV2Settings(enabled=True),
        phases=[PersonaPhase()],
        cheap=llm,
        reflect=llm,
        embedder=FakeEmbedder(),
        clock=lambda: T0 + timedelta(days=1),
    )

    report = await night.run(dry_run=True)

    assert data.persona.current().version == 1  # type: ignore[union-attr]
    assert data.persona.history.count() == 0
    assert any("subiria intelecto" in n for r in report.results for n in r.notes)


def test_the_phase_is_part_of_the_default_night() -> None:
    from brain.memory.v2.night import default_phases

    assert "persona" in [p.name for p in default_phases()]


def test_what_marked_her_never_gets_a_double_period() -> None:
    state = state_with()
    state.recent = ["A perda dele me fez pensar mais devagar.", "Ele riu da piada!", "E agora?"]
    text = render_persona(state) or ""
    assert "devagar; Ele riu da piada; E agora." in text
    assert ".." not in text and "!." not in text and "?." not in text
