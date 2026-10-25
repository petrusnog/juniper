"""Concerns and values: what matters to her, the yardstick of her feelings (ADR-017, section 6).

A short, stable list; each item has a weight from 0 to 1. They are narrative and value, not
probability: nothing here carries a confidence. They start from a default list the user can edit
in the configuration, are loaded into MongoDB the first time and move *very* slowly after that:

- the user changes a weight freely (``juniper memory concerns set``), and it is recorded;
- the night may move one only for an event of very high impact, by at most
  ``max_monthly_change`` (0.05) per item per rolling month, and always citing the event.

The values *of the user* ("what matters to him") are not here: they are facts he said or beliefs
he confirmed, never her estimate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from brain.config import MemoryV2Settings, V2ConcernSettings, settings
from brain.eventlog import event_log
from brain.memory.v2.guards import STEP_EPSILON, Rejection, check_concern_change
from brain.memory.v2.models import Concern, ConcernChange, V2DataError, utcnow

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data

MONTH = timedelta(days=30)
SOURCE_PETRUS = "petrus"
SOURCE_NIGHT = "night"


class ConcernError(ValueError):
    """A change the user asked for that cannot be made (unknown concern, weight outside 0..1)."""


@dataclass
class NightChange:
    """The outcome of a proposal from the night."""

    applied: float = 0.0  # how far it really moved (after the monthly ceiling)
    rejection: Rejection | None = None
    capped: bool = False

    @property
    def ok(self) -> bool:
        return self.rejection is None and self.applied != 0.0


class ConcernBook:
    def __init__(
        self,
        data: V2Data,
        config: V2ConcernSettings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.data = data
        self.config = config or settings.memory.v2.concerns
        self.clock = clock

    # --- reading ------------------------------------------------------------------------------

    def ensure_defaults(self) -> list[Concern]:
        """Load the starting list on first use. Existing concerns (and the user's edits) stay."""
        for item in self.config.defaults:
            if self.data.concerns.get(item.id) is None:
                self.data.concerns.save(
                    Concern(
                        id=item.id,
                        name=item.name,
                        weight=item.weight,
                        baseline_weight=item.weight,
                        description=item.description,
                        updated_at=self.clock(),
                    )
                )
        return self.all()

    def all(self) -> list[Concern]:
        return sorted(self.data.concerns.list(), key=lambda c: (-c.weight, c.id))

    def get(self, concern_id: str) -> Concern | None:
        return self.data.concerns.get(concern_id)

    # --- the user's edit ----------------------------------------------------------------------

    def set_weight(self, concern_id: str, weight: object, reason: str = "") -> Concern:
        """The user sets a weight: free, validated, recorded. Raises ``ConcernError``."""
        concern = self.get(concern_id)
        if concern is None:
            raise ConcernError(f"Não conheço a preocupação {concern_id!r}.")
        try:
            value = float(weight)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ConcernError("O peso precisa ser um número entre 0 e 1.") from None
        if not 0.0 <= value <= 1.0:
            raise ConcernError("O peso precisa estar entre 0 e 1.")
        self._record(
            concern,
            value - concern.weight,
            reason.strip() or "edição do Petrus",
            None,
            SOURCE_PETRUS,
            value,
        )
        return concern

    # --- the night's proposal -----------------------------------------------------------------

    def propose_night_change(
        self, concern_id: str, delta: object, impact: object, event_ref: object, reason: str = ""
    ) -> NightChange:
        """The night asks to move a weight by ``delta``. Allowed only for a very high impact event
        that it cites, and cut to what is left of this item's monthly ceiling."""
        concern = self.get(concern_id)
        problem = check_concern_change(
            concern, delta, impact, event_ref, min_impact=self.config.min_impact
        )
        if problem is not None or concern is None:
            return NightChange(rejection=problem)
        wanted = float(delta)  # type: ignore[arg-type]
        left = max(0.0, self.config.max_monthly_change - self._spent(concern))
        allowed = max(-left, min(left, wanted))
        allowed = max(-concern.weight, min(1.0 - concern.weight, allowed))  # stays inside 0..1
        if abs(allowed) <= STEP_EPSILON:
            return NightChange(rejection=Rejection("teto_mensal", "teto do mês já usado"))
        self._record(
            concern,
            allowed,
            reason.strip() or "mudança do algoritmo de sonho",
            str(event_ref).strip(),
            SOURCE_NIGHT,
            round(concern.weight + allowed, 6),
        )
        event_log.log(
            "concern_change", concern=concern.id, delta=round(allowed, 4), capped=allowed != wanted
        )
        return NightChange(applied=allowed, capped=abs(allowed - wanted) > STEP_EPSILON)

    def _spent(self, concern: Concern) -> float:
        """How far the night already moved this item in the last 30 days (either direction)."""
        since = self.clock() - MONTH
        return sum(
            abs(c.delta) for c in concern.history if c.source == SOURCE_NIGHT and c.at >= since
        )

    def _record(
        self,
        concern: Concern,
        delta: float,
        reason: str,
        event_ref: str | None,
        source: str,
        new_weight: float,
    ) -> None:
        now = self.clock()
        concern.history.append(ConcernChange(now, delta, reason, event_ref, source))
        concern.weight = max(0.0, min(1.0, new_weight))
        concern.updated_at = now
        try:
            self.data.concerns.save(concern)
        except V2DataError as exc:  # pragma: no cover - the weight was validated above
            raise ConcernError(str(exc)) from exc


def default_book(data: V2Data, config: MemoryV2Settings | None = None) -> ConcernBook:
    return ConcernBook(data, (config or settings.memory.v2).concerns)
