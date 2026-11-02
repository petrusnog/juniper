"""The living personality: numeric traits that change with what she expresses (ADR-017 section 8;
the V3 algorithm is ADR-030, section 5).

The traits are the Big Five in 10 aspects (DeYoung) plus irony. Following the Whole Trait Theory
(a trait is the distribution of the states a person expresses) and TESSERA (repeated states in
repeated situations consolidate into traits), every night:

1. each conversation of the day gives a **state** per aspect (how enthusiastic, assertive,
   compassionate... she was there), proposed by the model with the turns as evidence; the user's
   reaction to that expression reinforces or weakens it; the day's mood is one more sample;
2. each trait moves ``alpha`` of the way toward the weighted mean of its samples, at most
   ``max_step`` a night and never more than ``max_band`` away from its baseline;
3. the baseline itself matures toward the trait (``maturation`` a day): a long phase becomes who
   she is. There is no return to a fixed baseline: a trait only goes back if the states do.

Every change writes the previous version to the history, with the events behind it, so it can be
reverted. The core never changes; this is only what comes after it. A personality stored with the
V2 traits is migrated once, as a new version (the V2 stays in the history).

The text that reaches the prompt comes from a fixed template over the traits and the few lines of
"what marked me lately": deterministic, no LLM writes it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from brain.config import V2PersonaSettings, settings
from brain.eventlog import event_log
from brain.memory.v2.guards import STEP_EPSILON, Rejection, check_marked
from brain.memory.v2.models import PersonaState, PersonaVersion, Trait, utcnow

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data

MAX_RECENT = 3
MIGRATION_REASON = "migração para o Big Five (ADR-030)"
MAX_CHANGED_BY = 12
# A shift smaller than this is not worth a word in the prompt.
NOTICEABLE = 0.03
RELAX_EPSILON = 1e-3

# Each trait as an adjective, used with "mais" or "menos" (the V2 names stay for old versions).
WORDS: dict[str, str] = {
    "volatilidade": "volátil",
    "retraimento": "retraída",
    "compaixão": "compassiva",
    "polidez": "polida",
    "diligência": "diligente",
    "ordem": "organizada",
    "entusiasmo": "entusiasmada",
    "assertividade": "assertiva",
    "abertura": "aberta",
    "intelecto": "curiosa",
    "ironia": "irônica",
    "brincadeira": "brincalhona",
    "franqueza": "direta",
    "introspecção": "reflexiva",
    "carinho": "carinhosa",
    "proatividade": "proativa",
}
# The V2 traits and the aspect each one became (ADR-030, section 4).
V2_TO_ASPECT = {
    "carinho": "compaixão",
    "brincadeira": "entusiasmo",
    "franqueza": "assertividade",
    "proatividade": "diligência",
    "introspecção": "intelecto",
    "ironia": "ironia",
}
PERSONA_OPEN = "<personalidade>"
PERSONA_CLOSE = "</personalidade>"
PERSONA_HEADER = (
    "Como você anda, comparada ao seu jeito de sempre. É o seu jeito: deixe que ele guie o tom. O "
    "resto do que você é (honestidade, cuidado com as ferramentas, confirmação) continua igual."
)


@dataclass
class Sample:
    """One state she expressed (a conversation, or the day's mood) for one trait."""

    trait: str
    value: float  # 0 to 1: how much of the trait she showed
    weight: float = 1.0
    event: str = ""  # "turn:<session>:<turn>", or "" for the mood


@dataclass
class EvolveReport:
    changed: dict[str, float] = field(default_factory=dict)  # trait -> net delta
    matured: int = 0  # baselines that moved toward their trait
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
        """The stored personality, created from the configured traits the first time, and
        migrated once when it still has traits the configuration no longer has (the V2 ones)."""
        state = self.current()
        if state is None:
            state = default_state(self.config, self.clock())
            if not self.data.persona.save(state, None, "versão inicial"):
                state = self.current() or state  # another process wrote it first
            return state
        configured = {t.name for t in self.config.traits}
        if set(state.traits) != configured:
            migrated = self._migrated(state)
            if self.data.persona.save(migrated, state.version, MIGRATION_REASON):
                event_log.log("persona_migrated", version=migrated.version)
                return migrated
            state = self.current() or state
        return state

    def _migrated(self, state: PersonaState) -> PersonaState:
        """The configured traits, carrying over what the old ones already were: a V2 trait
        becomes its aspect (value and baseline); a known name keeps its numbers; the rest start
        at the configured baseline. The polite aspect mirrors what frankness had drifted."""
        old = state.traits
        traits: dict[str, Trait] = {}
        for item in self.config.traits:
            source = old.get(item.name) or next(
                (
                    old[v2]
                    for v2, aspect in V2_TO_ASPECT.items()
                    if aspect == item.name and v2 in old
                ),
                None,
            )
            if source is not None:
                value = min(item.max, max(item.min, source.value))
                base = min(item.max, max(item.min, source.baseline))
                traits[item.name] = Trait(value, base, item.min, item.max)
            else:
                traits[item.name] = Trait(item.baseline, item.baseline, item.min, item.max)
        frank, polite = old.get("franqueza"), traits.get("polidez")
        if frank is not None and polite is not None and "polidez" not in old:
            shifted = polite.baseline - (frank.value - frank.baseline)
            polite.value = round(min(polite.max, max(polite.min, shifted)), 6)
        return PersonaState(
            traits=traits,
            recent=list(state.recent),
            version=state.version + 1,
            changed_by=["migracao:big-five"],
            updated_at=self.clock(),
        )

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

    def evolve(
        self,
        samples: list[Sample],
        marked: list[str] | None = None,
        reason: str = "algoritmo de sonho",
    ) -> EvolveReport:
        """Move every trait toward the weighted mean of its samples, mature the baselines, and
        write one new version (nothing when nothing changed). ``evolves: false`` keeps every
        trait at its baseline (it is reset, with a version, if it had drifted)."""
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

        by_trait: dict[str, list[Sample]] = {}
        events: list[str] = []
        for sample in samples:
            if sample.trait not in traits or not 0.0 <= sample.value <= 1.0 or sample.weight <= 0:
                report.rejections.append(
                    Rejection("traco_desconhecido", "traço ou estado inválido")
                )
                continue
            by_trait.setdefault(sample.trait, []).append(sample)
            if sample.event:
                events.append(sample.event)
        for name, group in by_trait.items():
            delta = self._toward(traits[name], group)
            if abs(delta) > STEP_EPSILON:
                report.changed[name] = round(delta, 6)
        report.matured = self._mature(traits, state.updated_at, now)
        kept: list[str] = []
        for line in marked or []:
            problem = check_marked(line)
            if problem is not None:
                report.rejections.append(problem)
            elif len(kept) < MAX_RECENT:
                kept.append(" ".join(line.split()))
        report.marked = kept
        self._write(state, traits, events, kept, reason=reason, report=report, expected=expected)
        return report

    def _toward(self, trait: Trait, group: list[Sample]) -> float:
        """Move ``trait`` toward the weighted mean of its states; returns the delta."""
        total = sum(s.weight for s in group)
        target = sum(s.value * s.weight for s in group) / total
        step = self.config.alpha * (target - trait.value)
        step = max(-self.config.max_step, min(self.config.max_step, step))
        low = max(trait.min, trait.baseline - self.config.max_band)
        high = min(trait.max, trait.baseline + self.config.max_band)
        new = round(max(low, min(high, trait.value + step)), 6)
        delta = new - trait.value
        trait.value = new
        return delta

    def _mature(self, traits: dict[str, Trait], since: datetime, now: datetime) -> int:
        """Each baseline walks ``maturation`` a day toward its trait (a phase becomes a self)."""
        days = min(7.0, max(0.0, (now - since).total_seconds() / 86400))
        if days <= 0 or self.config.maturation <= 0:
            return 0
        share = 1 - (1 - self.config.maturation) ** days
        moved = 0
        for t in traits.values():
            target = round(t.baseline + share * (t.value - t.baseline), 6)
            if abs(target - t.baseline) >= RELAX_EPSILON / 10:
                t.baseline = min(t.max, max(t.min, target))
                moved += 1
        return moved

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
            all(
                abs(traits[n].value - t.value) < 1e-9
                and abs(traits[n].baseline - t.baseline) < 1e-9
                for n, t in state.traits.items()
            )
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
