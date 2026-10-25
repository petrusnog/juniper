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
from brain.memory.v2.models import PersonaState, PersonaVersion, Trait, utcnow

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data

MAX_RECENT = 3
MAX_CHANGED_BY = 12
# A shift smaller than this is not worth a word in the prompt.
NOTICEABLE = 0.03
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


class PersonaError(ValueError):
    """A persona operation that cannot be done (unknown version, lost race). Fit for the user."""


def drift(state: PersonaState | None) -> float:
    """How far she is from her usual self: the mean distance of the traits to their baselines."""
    if state is None or not state.traits:
        return 0.0
    return sum(abs(t.value - t.baseline) for t in state.traits.values()) / len(state.traits)


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

    def history(self) -> list[PersonaVersion]:
        """Past versions, oldest first (the current one is in ``current``)."""
        return sorted(self.data.persona.history.list(), key=lambda v: v.state.version)

    def drift_warning(self, state: PersonaState | None = None) -> float | None:
        """The drift, if it is past the configured limit; otherwise ``None``."""
        value = drift(state if state is not None else self.current())
        return value if value >= self.config.drift_warning else None

    def revert(self, to: int | None = None) -> PersonaState:
        """Go back to an earlier version as a *new* version (the history is never erased).

        ``to`` defaults to the version just before the current one. Raises ``PersonaError``.
        """
        state = self.ensure()
        target_number = state.version - 1 if to is None else to
        if target_number == state.version:
            raise PersonaError(f"Já estou na versão {target_number}.")
        target = next((v for v in self.history() if v.state.version == target_number), None)
        if target is None:
            known = ", ".join(str(v.state.version) for v in self.history()) or "nenhuma"
            raise PersonaError(f"Não existe a versão {target_number}. Versões guardadas: {known}.")
        new = PersonaState(
            traits={n: Trait(**vars(t)) for n, t in target.state.traits.items()},
            recent=list(target.state.recent),
            version=state.version + 1,
            changed_by=[f"revert:v{target_number}"],
            updated_at=self.clock(),
        )
        if not self.data.persona.save(new, state.version, f"revert para a versão {target_number}"):
            raise PersonaError("A personalidade mudou enquanto eu voltava. Tente de novo.")
        event_log.log("persona_reverted", to=target_number, version=new.version)
        return new

    # --- the night ----------------------------------------------------------------------------

    def evolve(self, moves: list[Move], reason: str = "algoritmo de sonho") -> EvolveReport:
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


def _grade(value: float) -> str:
    """How much of a trait, in words, from its absolute value (0 to 1)."""
    if value >= 0.8:
        return "muito"
    if value >= 0.6:
        return "bastante"
    if value >= 0.4:
        return "moderadamente"
    if value >= 0.25:
        return "pouco"
    return "quase nada"


def persona_lines(state: PersonaState | None, always: bool = True) -> list[str]:
    """The sentences that say how she is today, how that differs from her usual self, and what
    marked her lately. Always says how she is today (even with no drift); empty without a state."""
    if state is None:
        return []
    lines: list[str] = []
    ordered = sorted(state.traits.items(), key=lambda kv: (-kv[1].value, kv[0]))
    today = ", ".join(f"{_grade(t.value)} {WORDS.get(name, name)}" for name, t in ordered)
    if today and always:
        lines.append(f"Seu jeito hoje: {today}.")
    parts: list[str] = []
    for name in sorted(state.traits):
        trait = state.traits[name]
        word = WORDS.get(name)
        shift = trait.value - trait.baseline
        if word is None or abs(shift) < NOTICEABLE:
            continue
        parts.append(f"{_degree(shift)} {word}")
    if parts:
        lines.append("Comparada ao seu jeito de sempre, você anda " + ", ".join(parts) + ".")
    if state.recent:
        marked = "; ".join(r.strip().rstrip(" .!?…") for r in state.recent[:MAX_RECENT])
        lines.append(f"O que te marcou ultimamente: {marked}.")
    return lines


def how_she_was(state: PersonaState | None) -> str:
    """A short phrase for how she was in a given version, for a refined memory ("como eu era")."""
    if state is None:
        return ""
    parts = []
    for name in sorted(state.traits):
        trait = state.traits[name]
        word = WORDS.get(name)
        shift = trait.value - trait.baseline
        if word is not None and abs(shift) >= NOTICEABLE:
            parts.append(f"{_degree(shift)} {word}")
    return ", ".join(parts) if parts else "no seu jeito de sempre"


def render_persona(state: PersonaState | None, always: bool = True) -> str | None:
    """The ``<personalidade>`` block for ``state``, from a fixed template (``None`` without a
    personality yet). Same state, same text."""
    lines = persona_lines(state, always)
    if not lines:
        return None
    return f"{PERSONA_HEADER}\n{PERSONA_OPEN}\n" + "\n".join(lines) + f"\n{PERSONA_CLOSE}"
