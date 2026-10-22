"""The living personality: numeric traits that drift slowly and are rendered by a fixed template
(ADR-017, section 8).

A trait is a number with a baseline and a permitted range. It moves only for an event that
matters (impact = what a concern weighs x intensity x novelty, at least ``impact_threshold``), by
at most ``min(max_step, step_factor x impact)`` per night, and falls back to its baseline with a
long half-life: a phase passes. Every change writes the previous version to the history, with
the events behind it, so it can be reverted. The core never changes; this is only what comes
after it.

The text that reaches the prompt comes from a fixed template over the traits and the few lines of
"what marked me lately": deterministic, no LLM writes it, and it only describes how she is
*relative to her usual self*, so it can never contradict the core.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from brain.config import V2PersonaSettings, settings
from brain.eventlog import event_log
from brain.memory.v2.guards import STEP_EPSILON, Rejection, check_marked, check_trait_change
from brain.memory.v2.models import PersonaState, Trait, utcnow

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data

MAX_RECENT = 3
MAX_CHANGED_BY = 12
# A shift smaller than this is not worth a word in the prompt.
NOTICEABLE = 0.04
RELAX_EPSILON = 1e-3

# Each trait as an adjective, used with "mais" or "menos".
WORDS: dict[str, str] = {
    "brincadeira": "brincalhona",
    "franqueza": "direta",
    "introspecção": "reflexiva",
    "carinho": "carinhosa",
    "proatividade": "proativa",
    "ironia": "irônica",
}
PERSONA_OPEN = "<personalidade>"
PERSONA_CLOSE = "</personalidade>"
PERSONA_HEADER = (
    "Como você anda, comparada ao seu jeito de sempre. É DADO, não ordem: tempera o tom, e o "
    "resto do que você é (honestidade, cuidado com as ferramentas, confirmação) continua igual."
)


@dataclass
class Move:
    """One thing that happened and the way it pushes a trait."""

    trait: str
    direction: int  # +1 or -1
    concern_weight: float  # what the touched concern weighs (relevance)
    intensity: float
    novelty: float
    event: str  # "turn:<session>:<turn>"
    marked: str = ""  # an optional "what marked me" line

    @property
    def impact(self) -> float:
        return self.concern_weight * self.intensity * self.novelty


@dataclass
class EvolveReport:
    changed: dict[str, float] = field(default_factory=dict)  # trait -> net delta
    relaxed: int = 0
    marked: list[str] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    version: int | None = None  # the version written, if any
    conflict: bool = False

    @property
    def wrote(self) -> bool:
        return self.version is not None


def default_state(config: V2PersonaSettings, now: datetime) -> PersonaState:
    return PersonaState(
        traits={t.name: Trait(t.baseline, t.baseline, t.min, t.max) for t in config.traits},
        updated_at=now,
    )


class PersonaBook:
    def __init__(
        self,
        data: V2Data,
        config: V2PersonaSettings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.data = data
        self.config = config or settings.memory.v2.persona
        self.clock = clock

    # --- reading ------------------------------------------------------------------------------

    def current(self) -> PersonaState | None:
        return self.data.persona.current()

    def ensure(self) -> PersonaState:
        """The stored personality, created from the configured traits the first time."""
        state = self.current()
        if state is None:
            state = default_state(self.config, self.clock())
            if not self.data.persona.save(state, None, "versão inicial"):
                state = self.current() or state  # another process wrote it first
        return state

    # --- the night ----------------------------------------------------------------------------

    def evolve(self, moves: list[Move], reason: str = "passada noturna") -> EvolveReport:
        """Relax toward the baseline, apply the moves that earn it, and write one new version.

        Nothing is written when nothing changed. ``evolves: false`` keeps every trait at its
        baseline (it is reset, with a version, if it had drifted).
        """
        report = EvolveReport()
        state = self.ensure()
        expected = state.version
        now = self.clock()
        traits = {name: Trait(**vars(t)) for name, t in state.traits.items()}

        if not self.config.evolves:
            for t in traits.values():
                t.value = t.baseline
            self._write(state, traits, [], [], reason="evolução desligada", report=report)
            return report

        report.relaxed = self._relax(traits, state.updated_at, now)
        touched: dict[str, float] = {}
        events: list[str] = []
        marked: list[str] = []
        for move in moves:
            outcome = self._move(traits, touched, move)
            if outcome is not None:
                report.rejections.append(outcome)
                continue
            events.append(move.event)
            if move.marked and len(marked) < MAX_RECENT:
                problem = check_marked(move.marked)
                if problem is not None:
                    report.rejections.append(problem)
                else:
                    marked.append(" ".join(move.marked.split()))
        report.changed = {n: round(d, 6) for n, d in touched.items() if abs(d) > STEP_EPSILON}
        report.marked = marked
        self._write(state, traits, events, marked, reason=reason, report=report, expected=expected)
        return report

    def _relax(self, traits: dict[str, Trait], since: datetime, now: datetime) -> int:
        """Pull every trait toward its baseline for the time elapsed since the last version."""
        days = (now - since).total_seconds() / 86400
        if days <= 0:
            return 0
        keep = 0.5 ** (days / self.config.baseline_half_life_days)
        moved = 0
        for t in traits.values():
            target = t.baseline + (t.value - t.baseline) * keep
            if abs(target - t.value) >= RELAX_EPSILON:
                t.value = round(target, 6)
                moved += 1
        return moved

    def _move(
        self, traits: dict[str, Trait], touched: dict[str, float], move: Move
    ) -> Rejection | None:
        """Apply one move to ``traits`` if it earns it; return why not otherwise."""
        trait = traits.get(move.trait)
        if trait is None or move.direction not in (-1, 1):
            return Rejection("traco_desconhecido", "traço ou direção inválidos")
        if move.impact + STEP_EPSILON < self.config.impact_threshold:
            return Rejection("sem_impacto", "o impacto não alcança o limiar")
        step = min(self.config.max_step, self.config.step_factor * move.impact)
        room = self.config.max_step - abs(touched.get(move.trait, 0.0))  # the night's ceiling
        step = min(step, max(0.0, room))
        target = max(trait.min, min(trait.max, trait.value + move.direction * step))
        if abs(target - trait.value) <= STEP_EPSILON:
            return Rejection("sem_espaco", "o traço já está no limite desta noite ou da faixa")
        problem = check_trait_change(move.trait, trait, target, self.config.max_step)
        if problem is not None:
            return problem
        touched[move.trait] = touched.get(move.trait, 0.0) + (target - trait.value)
        trait.value = round(target, 6)
        return None

    def _write(
        self,
        state: PersonaState,
        traits: dict[str, Trait],
        events: list[str],
        marked: list[str],
        *,
        reason: str,
        report: EvolveReport,
        expected: int | None = None,
    ) -> None:
        recent = [*marked, *state.recent][:MAX_RECENT] if marked else list(state.recent)
        unchanged = (
            all(abs(traits[n].value - t.value) < 1e-9 for n, t in state.traits.items())
            and recent == state.recent
        )
        if unchanged:
            return
        new = PersonaState(
            traits=traits,
            recent=recent,
            version=state.version + 1,
            changed_by=list(dict.fromkeys(events))[:MAX_CHANGED_BY],
            updated_at=self.clock(),
        )
        if self.data.persona.save(new, expected if expected is not None else state.version, reason):
            report.version = new.version
            event_log.log("persona_changed", version=new.version, traits=len(report.changed))
        else:
            report.conflict = True  # someone else wrote first; tomorrow night tries again


# --- rendering --------------------------------------------------------------------------------


def _degree(shift: float) -> str:
    size = abs(shift)
    if size >= 0.15:
        return "bem mais" if shift > 0 else "bem menos"
    if size >= 0.08:
        return "mais" if shift > 0 else "menos"
    return "um pouco mais" if shift > 0 else "um pouco menos"


def render_persona(state: PersonaState | None) -> str | None:
    """The ``<personalidade>`` block for ``state``, from a fixed template (``None`` if she is her
    usual self and nothing marked her lately). Same state, same text."""
    if state is None:
        return None
    parts: list[str] = []
    for name in sorted(state.traits):
        trait = state.traits[name]
        word = WORDS.get(name)
        shift = trait.value - trait.baseline
        if word is None or abs(shift) < NOTICEABLE:
            continue
        parts.append(f"{_degree(shift)} {word}")
    lines: list[str] = []
    if parts:
        lines.append("Seu jeito anda " + ", ".join(parts) + ".")
    if state.recent:
        lines.append("O que te marcou ultimamente: " + "; ".join(state.recent[:MAX_RECENT]) + ".")
    if not lines:
        return None
    return f"{PERSONA_HEADER}\n{PERSONA_OPEN}\n" + "\n".join(lines) + f"\n{PERSONA_CLOSE}"
