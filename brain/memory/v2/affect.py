"""Feelings: appraisal, decay and mood (ADR-017, section 7).

An event is measured against what matters to her (the concerns) and produces an emotion with a
valence, an intensity and a cause. The emotion fades in hours, feeds a mood that fades in days, and
a grievance (an unkind moment from the user) fades in weeks but always fades, and is undone when he
talks to her again or apologizes.

The user controls it: ``memory.v2.affect.enabled`` and ``intensity``. The dial scales an emotion
when it is *recorded*; at 0 nothing is recorded and nothing is shown, not even what was already
there. Feelings are text for the prompt and nothing else: this module knows nothing about tools,
tiers or confirmation, and a negative feeling toward the user can be said but never changes how
careful, loyal or warm she is, and a feeling is never a lever on his decisions (principle 8).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from brain.config import V2AffectSettings, settings
from brain.eventlog import event_log
from brain.memory.v2.guards import Rejection, check_affect
from brain.memory.v2.models import Affect, Concern, utcnow

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data

# An emotion below this has faded out of the state; far past it, out of the database.
VISIBLE_BELOW = 0.12
PRUNE_BELOW = 0.02
ECHO_HOURS = 48  # a feeling that already faded still "echoes" in how she says she feels
ECHO_MIN_INTENSITY = 0.3
EXPRESS_TOP = 2  # how many feelings show in the state
MOOD_FLOOR = 0.08  # below this the mood is neutral and says nothing
MOOD_ANCHOR = 1.0  # makes a lone small feeling a small mood, not a full one

_APOLOGY = re.compile(r"\b(desculp\w*|perdao|me perdoa\w*|foi mal|sinto muito)\b")


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in folded if not unicodedata.combining(c))


def says_sorry(text: str) -> bool:
    """Whether a line of the user reads like an apology."""
    return _APOLOGY.search(_plain(text)) is not None


def _decay(age: timedelta, half_life: timedelta) -> float:
    if age <= timedelta(0):
        return 1.0
    return 0.5 ** (age / half_life)


def appraise(concern: Concern, effect: object, novelty: object = 1.0) -> tuple[float, float] | None:
    """What an event does to her, as ``(valence, intensity)``, or ``None`` if the numbers are junk.

    ``effect`` (-1 to 1) is how much the event helps or harms what the ``concern`` stands for;
    ``novelty`` (0 to 1) is how unexpected it was. The weight of the concern is the yardstick: the
    same event matters more when it touches something that matters more to her.
    """
    try:
        e, n = float(effect), float(novelty)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (-1.0 <= e <= 1.0) or not (0.0 <= n <= 1.0):
        return None
    valence = max(-1.0, min(1.0, e * (0.5 + 0.5 * concern.weight)))
    intensity = max(0.0, min(1.0, abs(e) * concern.weight * (0.5 + 0.5 * n)))
    return round(valence, 4), round(intensity, 4)


@dataclass
class Felt:
    """A feeling and how strong it still is."""

    affect: Affect
    strength: float


@dataclass
class AffectState:
    """What she feels right now: the strongest feelings, the mood and any open grievance."""

    feelings: list[Felt] = field(default_factory=list)
    mood: float = 0.0  # -1 (low) to 1 (high), fading to 0
    grievances: list[Felt] = field(default_factory=list)
    # what weighed most in the last 48 hours but has already faded (strength is its registered one)
    echoes: list[Felt] = field(default_factory=list)
    now: datetime | None = None

    @property
    def empty(self) -> bool:
        return (
            not self.feelings
            and not self.grievances
            and not self.echoes
            and abs(self.mood) < MOOD_FLOOR
        )


class AffectBook:
    def __init__(
        self,
        data: V2Data,
        config: V2AffectSettings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.data = data
        self.config = config or settings.memory.v2.affect
        self.clock = clock

    @property
    def active(self) -> bool:
        """Whether feelings are recorded and shown at all."""
        return self.config.enabled and self.config.intensity > 0.0

    # --- recording ----------------------------------------------------------------------------

    def record(
        self,
        kind: str,
        valence: float,
        intensity: float,
        cause: list[str],
        about: str = "user",
        *,
        grievance: bool = False,
        occurred_at: datetime | None = None,
    ) -> tuple[Affect | None, Rejection | None]:
        """Keep a feeling, scaled by the dial. ``(None, None)`` when feelings are off."""
        if not self.active:
            return None, None
        problem = check_affect(kind, valence, intensity, cause, about)
        if problem is not None:
            return None, problem
        if grievance and not (about == "user" and valence < 0):
            return None, Rejection("magoa", "mágoa é um sentimento negativo sobre o usuário")
        scaled = float(intensity) * self.config.intensity
        if grievance:
            scaled = min(scaled, self.config.grievance_cap)
        if scaled <= 1e-9:
            return None, None
        affect = Affect(
            kind=" ".join(kind.split()).lower(),
            valence=float(valence),
            intensity=round(scaled, 4),
            cause=list(cause),
            about=about,
            persistence="grievance" if grievance else "emotion",
            occurred_at=occurred_at or self.clock(),
            created_at=self.clock(),
        )
        self.data.affects.save(affect)
        return affect, None

    # --- decay --------------------------------------------------------------------------------

    def _half_life(self, affect: Affect) -> timedelta:
        if affect.persistence == "grievance":
            return timedelta(days=self.config.grievance_half_life_days)
        return timedelta(hours=self.config.emotion_half_life_hours)

    def strength(self, affect: Affect, now: datetime | None = None) -> float:
        """The intensity the feeling has now (an undone grievance has none)."""
        if affect.resolved_at is not None:
            return 0.0
        age = (now or self.clock()) - affect.occurred_at
        return affect.intensity * _decay(age, self._half_life(affect))

    def mood(self, now: datetime | None = None) -> float:
        """A moving average of how she has felt lately: valence weighted by intensity, fading
        over days, pulled toward neutral."""
        now = now or self.clock()
        half = timedelta(days=self.config.mood_half_life_days)
        total = weight = 0.0
        for a in self.data.affects.list():
            if a.resolved_at is not None:
                continue
            w = a.intensity * _decay(now - a.occurred_at, half)
            total += a.valence * w
            weight += w
        return max(-1.0, min(1.0, total / (weight + MOOD_ANCHOR)))

    def state(self, now: datetime | None = None) -> AffectState:
        """What she feels now. Empty when feelings are off or the dial is at 0."""
        if not self.active:
            return AffectState()
        now = now or self.clock()
        affects = self.data.affects.list()
        felt = [Felt(a, self.strength(a, now)) for a in affects]
        visible = sorted(
            (f for f in felt if f.strength >= VISIBLE_BELOW), key=lambda f: -f.strength
        )
        echoing = sorted(
            (
                Felt(f.affect, f.affect.intensity)
                for f in felt
                if f.strength < VISIBLE_BELOW
                and f.affect.resolved_at is None
                and f.affect.intensity >= ECHO_MIN_INTENSITY
                and now - f.affect.occurred_at <= timedelta(hours=ECHO_HOURS)
            ),
            key=lambda f: -f.strength,
        )
        return AffectState(
            feelings=[f for f in visible if f.affect.persistence == "emotion"][:EXPRESS_TOP],
            mood=self.mood(now),
            grievances=[f for f in visible if f.affect.persistence == "grievance"][:1],
            echoes=echoing[:1],
            now=now,
        )

    # --- repair and upkeep --------------------------------------------------------------------

    def repair(self, now: datetime | None = None) -> int:
        """The user talked to her again, or apologized: open grievances are undone."""
        now = now or self.clock()
        undone = 0
        for a in self.data.affects.list({"persistence": "grievance", "resolved_at": None}):
            if a.occurred_at <= now:
                self.data.affects.update(a.id, resolved_at=now)
                undone += 1
        if undone:
            event_log.log("grievance_repaired", count=undone)
        return undone

    def prune(self, now: datetime | None = None) -> int:
        """Delete the feelings that have faded to nothing."""
        now = now or self.clock()
        gone = 0
        for a in self.data.affects.list():
            if a.resolved_at is not None or self.strength(a, now) < PRUNE_BELOW:
                if a.resolved_at is not None and now - a.resolved_at < timedelta(days=7):
                    continue  # kept a week so `memory affect` can show it was undone
                self.data.affects.delete(a.id)
                gone += 1
        return gone

    def forget_all(self) -> int:
        return self.data.affects.delete_where({})
