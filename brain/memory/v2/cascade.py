"""Forgetting a fact reaches the beliefs that leaned on it (ADR-017, principle 7).

A belief cites the lines it came from. When the user asks her to forget a fact, the evidence that
came from the same line (or that points at the fact itself) goes too, with the weight it had
added. A belief left with no supporting evidence at all is retired, unless the user himself
confirmed it. The opposite never happens: deleting a belief does not delete facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from brain.eventlog import event_log
from brain.memory.v2.confidence import SUPPORTS
from brain.memory.v2.models import Evidence, utcnow

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data


@dataclass
class CascadeReport:
    trimmed: list[str] = field(default_factory=list)  # beliefs that lost some evidence
    retired: list[str] = field(default_factory=list)  # beliefs left with nothing to stand on

    def __bool__(self) -> bool:
        return bool(self.trimmed or self.retired)


def _from_fact(evidence: Evidence, facts: list[Any]) -> bool:
    for fact in facts:
        if evidence.fact_id is not None and evidence.fact_id == fact.id:
            return True
        turn = getattr(fact, "turn", None)
        if (
            turn is not None
            and evidence.turn == turn
            and evidence.session_id is not None
            and evidence.session_id == getattr(fact, "session_id", None)
        ):
            return True
    return False


def forget_fact_evidence(data: V2Data, facts: list[Any]) -> CascadeReport:
    """Drop, from every belief, the evidence that came from ``facts`` (already deleted)."""
    report = CascadeReport()
    if not facts:
        return report
    for belief in data.beliefs.list():
        alpha, beta = belief.alpha, belief.beta
        keep_for = [e for e in belief.evidence_for if not _from_fact(e, facts)]
        keep_against = [e for e in belief.evidence_against if not _from_fact(e, facts)]
        removed = [
            e for e in (*belief.evidence_for, *belief.evidence_against) if _from_fact(e, facts)
        ]
        if not removed:
            continue
        for e in removed:
            if e.kind in SUPPORTS:
                alpha = max(belief.prior_alpha, alpha - e.weight)
            else:
                beta = max(belief.prior_beta, beta - e.weight)
        changes: dict[str, Any] = {
            "evidence_for": [e.to_doc() for e in keep_for],
            "evidence_against": [e.to_doc() for e in keep_against],
            "alpha": alpha,
            "beta": beta,
            "updated_at": utcnow(),
        }
        orphaned = not keep_for and belief.user_confirmed != "confirmed"
        if orphaned and belief.status != "retired":
            changes["status"] = "retired"
            report.retired.append(belief.id)
        else:
            report.trimmed.append(belief.id)
        data.beliefs.update(belief.id, **changes)
    if report:
        event_log.log("belief_cascade", trimmed=len(report.trimmed), retired=len(report.retired))
    return report
