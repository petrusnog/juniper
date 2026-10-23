"""Her own interests: where they come from, how much they matter, where she stands (ADR-017, 5.6).

A topic can be seeded by the user, even against him ("odeio os Beatles"). His opinion is a
fact about him; it never enters her taste. Her stance comes from studying the topic with the general
knowledge of the advanced model (no web, no tools) and weighing what she read against her own
concerns and traits. It moves slowly, with the same kind of evidence count as a belief, so it does
not flip from one night to the next. Exposure raises the intensity, no contact lets it fade, and
ambivalence ("mixed") is allowed. Her taste is second-hand: she read about it, she did not live it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from brain.config import V2InterestSettings, settings
from brain.eventlog import event_log
from brain.memory.v2.models import Evidence, Interest, utcnow

if TYPE_CHECKING:
    from brain.memory.embeddings import EmbeddingProvider
    from brain.memory.v2.store import V2Data

# What a study can add to the "like" or "dislike" side, at most (an appeal is -1..1).
MAX_STUDY_WEIGHT = 1.0
APPEAL_FLOOR = 0.15  # a study this lukewarm adds nothing either way
LIKE_FROM, DISLIKE_UNTIL = 0.65, 0.35  # to take a stance
LIKE_KEEP, DISLIKE_KEEP = 0.58, 0.42  # to keep it (hysteresis: no flipping on noise)
MIN_EVIDENCE = 1.0  # accumulated beyond the prior before she takes a stance at all
EXPOSURE_GAIN = 0.15
SAME_TOPIC_SIMILARITY = 0.88
MAX_REASONS = 3


@dataclass
class StudyOutcome:
    stance: str
    changed: bool
    weight: float


def _key(topic: str) -> str:
    return " ".join(topic.casefold().split())


def stance_of(alpha: float, beta: float, current: str) -> str:
    """The stance for these counts, with some inertia: taking one takes more than keeping it."""
    if alpha + beta - 2.0 < MIN_EVIDENCE:
        return "undecided"
    ratio = alpha / (alpha + beta)
    if ratio >= LIKE_FROM or (current == "like" and ratio >= LIKE_KEEP):
        return "like"
    if ratio <= DISLIKE_UNTIL or (current == "dislike" and ratio <= DISLIKE_KEEP):
        return "dislike"
    return "mixed"


class InterestBook:
    def __init__(
        self,
        data: V2Data,
        embedder: EmbeddingProvider | None = None,
        config: V2InterestSettings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.data = data
        self.embedder = embedder
        self.config = config or settings.memory.v2.interests
        self.clock = clock

    # --- reading ------------------------------------------------------------------------------

    def all(self) -> list[Interest]:
        return sorted(self.data.interests.list(), key=lambda i: (-i.intensity, i.topic))

    def find(self, topic: str) -> Interest | None:
        """The interest on ``topic``: the same words, or the same meaning."""
        wanted = _key(topic)
        interests = self.data.interests.list()
        for interest in interests:
            if _key(interest.topic) == wanted:
                return interest
        if self.embedder is None:
            return None
        from brain.memory.v2.semantic import semantic_search

        vector = self.embedder.embed(topic, "passage")
        hits = semantic_search(
            interests,
            vector,
            model=self.embedder.model_id,
            top_k=1,
            threshold=SAME_TOPIC_SIMILARITY,
        )
        return hits[0].item if hits else None

    # --- seeding and exposure -----------------------------------------------------------------

    def seed(self, topic: str, origin: Evidence) -> tuple[Interest, bool]:
        """Note a topic that came up. A new one starts curious; a known one is exposed again."""
        interest = self.find(topic)
        if interest is not None:
            self.expose(interest)
            return interest, False
        interest = Interest(
            topic=" ".join(topic.split()),
            intensity=0.3,
            origin=origin,
            exposures=1,
            created_at=self.clock(),
            updated_at=self.clock(),
        )
        if self.embedder is not None:
            interest.embedding = [float(x) for x in self.embedder.embed(topic, "passage")]
            interest.embedding_model = self.embedder.model_id
        self.data.interests.save(interest)
        return interest, True

    def expose(self, interest: Interest) -> None:
        """The topic came up again: it matters more (once a day at most)."""
        now = self.clock()
        if now - interest.updated_at < timedelta(days=1) and interest.exposures > 0:
            return
        interest.exposures += 1
        interest.intensity = round(
            interest.intensity + EXPOSURE_GAIN * (1.0 - interest.intensity), 4
        )
        interest.updated_at = now
        self.data.interests.save(interest)

    def fade(self) -> int:
        """No contact, less intensity: halve it every ``half_life_days`` since the last touch."""
        now = self.clock()
        faded = 0
        for interest in self.data.interests.list():
            touched = max(
                interest.updated_at,
                interest.last_studied_at or interest.updated_at,
                interest.faded_at or interest.updated_at,
            )
            days = (now - touched).total_seconds() / 86400
            if days < 1.0:
                continue
            kept = round(interest.intensity * 0.5 ** (days / self.config.half_life_days), 4)
            interest.faded_at = now  # the gap is accounted for; the next fade counts from here
            if kept < interest.intensity - 1e-4:
                interest.intensity = kept
                faded += 1
            self.data.interests.save(interest)
        return faded

    # --- studying -----------------------------------------------------------------------------

    def due(self, limit: int | None = None) -> list[Interest]:
        """Topics worth studying tonight: never studied first, then the strongest, after the gap."""
        now = self.clock()
        gap = timedelta(days=self.config.study_gap_days)
        ready = [
            i
            for i in self.data.interests.list()
            if i.last_studied_at is None or now - i.last_studied_at >= gap
        ]
        ready.sort(key=lambda i: (i.last_studied_at is not None, -i.intensity, i.topic))
        return ready[: self.config.max_per_night if limit is None else limit]

    def apply_study(self, interest: Interest, appeal: float, reasons: list[str]) -> StudyOutcome:
        """Fold one study into the stance. ``appeal`` (-1..1) is how much someone with her concerns
        and traits would like what she read; the weight and the stance are the code's."""
        weight = min(MAX_STUDY_WEIGHT, abs(appeal))
        if abs(appeal) >= APPEAL_FLOOR:
            if appeal > 0:
                interest.alpha += weight
            else:
                interest.beta += weight
        before = interest.stance
        interest.stance = stance_of(interest.alpha, interest.beta, before)
        if reasons:
            merged = [*reasons, *interest.reasons]
            interest.reasons = list(dict.fromkeys(merged))[:MAX_REASONS]
        interest.experience = "reading"
        interest.last_studied_at = self.clock()
        interest.exposures += 1
        interest.intensity = round(
            interest.intensity + EXPOSURE_GAIN * (1.0 - interest.intensity), 4
        )
        interest.updated_at = self.clock()
        self.data.interests.save(interest)
        event_log.log("interest_studied", stance=interest.stance, changed=before != interest.stance)
        return StudyOutcome(interest.stance, before != interest.stance, weight)

    def forget(self, interest_id: str) -> bool:
        return self.data.interests.delete(interest_id)


STANCE_WORDS = {
    "like": "gosto",
    "dislike": "não gosto",
    "mixed": "tenho sentimentos misturados sobre",
    "undecided": "ainda não decidi o que acho de",
}
