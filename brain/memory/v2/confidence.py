"""Confidence from evidence, never from what an LLM says (ADR-017, section 5.1).

A belief starts from a weak prior (alpha = beta = 1: "no idea"). Every piece of evidence adds a
weight to the side it supports; the weight depends on the *source* (a hunch of hers counts 1, the
user saying it counts 3, the user confirming her hypothesis counts 4). Confidence is the mean of
the resulting Beta distribution, ``alpha / (alpha + beta)``, and *firmness* is how much evidence
piled up on top of the prior. Without reinforcement the counts shrink back toward the prior, so a
stale belief returns to uncertainty, not to error.

Everything here is a pure function over plain numbers, so it is easy to test and to calibrate.
The starting values are a first guess; the settings in ``memory.v2.beliefs`` can change them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from brain.config import V2BeliefSettings
from brain.memory.v2.models import Belief, Evidence

# What each kind of evidence weighs, and which side of the belief it supports.
EVIDENCE_WEIGHT: dict[str, float] = {
    "observation": 1.0,  # she inferred it from a turn
    "contradiction": 1.0,  # a turn that points the other way
    "explicit": 3.0,  # the user said it
    "confirmation": 4.0,  # the user confirmed her hypothesis when asked
    "denial": 4.0,  # the user denied it
}
SUPPORTS = frozenset({"observation", "explicit", "confirmation"})
EXPLICIT_KINDS = frozenset({"explicit", "confirmation"})

# Words for the user and for the prompt (the number stays underneath).
TALVEZ_BELOW = 0.45
CERTEZA_FROM = 0.70
CERTEZA_MIN_N = 6.0
CERTEZA_MIN_SOURCES = 3
WORD_TALVEZ = "talvez"
WORD_ACHO = "acho que"
WORD_CERTEZA = "tenho bastante certeza"

# A belief retires when it is clearly wrong, or when it has faded to nothing.
RETIRE_BELOW = 0.25
RETIRE_MIN_N = 4.0
FADED_BELOW = 0.25

# Topic calibration: how skeptical a new belief starts on a topic she tends to get wrong.
SKEPTIC_SCALE = 4.0
MAX_EVIDENCE_REFS = 50


def weight_of(kind: str) -> float:
    try:
        return EVIDENCE_WEIGHT[kind]
    except KeyError:
        raise ValueError(f"tipo de evidência desconhecido: {kind!r}") from None


def confidence(alpha: float, beta: float) -> float:
    """The mean of Beta(alpha, beta)."""
    return alpha / (alpha + beta)


def firmness(alpha: float, beta: float, prior_alpha: float = 1.0, prior_beta: float = 1.0) -> float:
    """Evidence accumulated beyond the prior."""
    return alpha + beta - prior_alpha - prior_beta


def decay(
    alpha: float,
    beta: float,
    days: float,
    half_life_days: float,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
) -> tuple[float, float]:
    """Shrink the evidence toward the prior: after ``half_life_days`` half of it is gone."""
    if days <= 0:
        return alpha, beta
    keep = 0.5 ** (days / half_life_days)
    return (
        prior_alpha + (alpha - prior_alpha) * keep,
        prior_beta + (beta - prior_beta) * keep,
    )


def band(conf: float, n: float, sources: int | None = None) -> str:
    """The words for this confidence. With little evidence it never goes past "acho que".

    "Tenho bastante certeza" also needs evidence from at least ``CERTEZA_MIN_SOURCES`` distinct
    sources (session and day): a lot of weight from one conversation is still one conversation.
    ``sources=None`` skips that check (callers that only have the numbers).
    """
    if conf < TALVEZ_BELOW:
        return WORD_TALVEZ
    if conf < CERTEZA_FROM or n < CERTEZA_MIN_N:
        return WORD_ACHO
    if sources is not None and sources < CERTEZA_MIN_SOURCES:
        return WORD_ACHO
    return WORD_CERTEZA


def apply_evidence(belief: Belief, evidence: Evidence) -> bool:
    """Add ``evidence`` to ``belief`` (counts, references, user confirmation). Returns False, and
    changes nothing, when the same reference was already counted (so a night can be re-run)."""
    side_for = evidence.kind in SUPPORTS
    refs = belief.evidence_for if side_for else belief.evidence_against
    if _already_counted(belief, evidence):
        return False
    evidence.weight = weight_of(evidence.kind)
    refs.append(evidence)
    del refs[:-MAX_EVIDENCE_REFS]  # the counts keep the weight; only the references are capped
    if side_for:
        belief.alpha += evidence.weight
    else:
        belief.beta += evidence.weight
    if evidence.kind == "confirmation":
        belief.user_confirmed = "confirmed"
    elif evidence.kind == "denial":
        belief.user_confirmed = "denied"
    return True


def _already_counted(belief: Belief, evidence: Evidence) -> bool:
    if evidence.session_id is None or evidence.turn is None:
        return False  # no stable reference: cannot tell, so it counts
    key = (evidence.kind, evidence.session_id, evidence.turn, evidence.fact_id)
    return any(
        (e.kind, e.session_id, e.turn, e.fact_id) == key
        for e in (*belief.evidence_for, *belief.evidence_against)
    )


def is_formed(belief: Belief, config: V2BeliefSettings) -> bool:
    """Whether a hypothesis has earned the right to be called a belief.

    An explicit statement or a confirmation from the user is enough. Otherwise it takes at least
    ``min_evidence`` of supporting evidence coming from different sessions or different days: one
    observation, or several in the same breath, is a hunch, not a belief.
    """
    if any(e.kind in EXPLICIT_KINDS for e in belief.evidence_for):
        return True
    supporting = belief.alpha - belief.prior_alpha
    if supporting < config.min_evidence:
        return False
    sources = {(e.session_id or "", e.at.date()) for e in belief.evidence_for}
    return len(sources) >= 2


def next_status(belief: Belief, config: V2BeliefSettings) -> str:
    """The status this belief should have now (retirement is final)."""
    if belief.status == "retired":
        return "retired"
    conf = confidence(belief.alpha, belief.beta)
    if belief.user_confirmed == "denied":
        return "retired"
    if conf < RETIRE_BELOW and belief.n >= RETIRE_MIN_N:
        return "retired"  # the evidence says she was wrong
    if belief.user_confirmed != "confirmed" and belief.n < FADED_BELOW:
        return "retired"  # faded to nothing without being reinforced
    if belief.status in ("active", "revised"):
        return belief.status
    return "active" if is_formed(belief, config) else "candidate"


def review(belief: Belief, now: datetime, config: V2BeliefSettings) -> Belief:
    """Apply the time decay since the last review, then re-evaluate the status."""
    days = (now - belief.last_reviewed_at).total_seconds() / 86400
    belief.alpha, belief.beta = decay(
        belief.alpha,
        belief.beta,
        days,
        config.half_life_days,
        belief.prior_alpha,
        belief.prior_beta,
    )
    belief.last_reviewed_at = now
    belief.status = next_status(belief, config)
    return belief


def hit_rate(hits: float, misses: float) -> float:
    """How often her opinions on a topic held up, smoothed (no data = 0.5)."""
    return (hits + 1.0) / (hits + misses + 2.0)


def prior_for(hits: float, misses: float) -> tuple[float, float]:
    """The starting prior for a new belief on a topic with this track record.

    Only skepticism is added: a topic she tends to get wrong starts with more weight on the
    "wrong" side. A good record does not make a new belief start more sure of itself.
    """
    doubt = max(0.0, 0.5 - hit_rate(hits, misses))
    return 1.0, 1.0 + doubt * SKEPTIC_SCALE


def distinct_sources(evidence: Sequence[Evidence]) -> int:
    return len({(e.session_id or "", e.at.date()) for e in evidence})
