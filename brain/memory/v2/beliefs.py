"""The book of beliefs: hypotheses about the user with confidence computed from evidence.

``BeliefBook`` is the one place that creates, reinforces, confirms, denies, reviews and retires
beliefs, over a ``V2Data``. The LLM may *propose* (the nightly reflection, T-105); every number
and every status change comes from ``confidence.py``. Nothing here calls an LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from brain.config import V2BeliefSettings, settings
from brain.memory.embeddings import EmbeddingProvider
from brain.memory.v2 import confidence as conf
from brain.memory.v2.models import Belief, Calibration, Evidence, utcnow
from brain.memory.v2.semantic import Hit, semantic_search
from brain.memory.v2.store import V2Data

# A new hypothesis this close to a live belief of the same subject is evidence for that belief.
DUPLICATE_SIMILARITY = 0.9
LIVE = ("candidate", "active", "revised")
HELD = ("active", "revised")


@dataclass
class ReviewReport:
    """What a review (the nightly decay) did."""

    reviewed: int = 0
    formed: list[str] = field(default_factory=list)  # candidates that became beliefs
    retired: list[str] = field(default_factory=list)  # wrong or faded


class BeliefBook:
    def __init__(
        self,
        data: V2Data,
        embedder: EmbeddingProvider | None = None,
        config: V2BeliefSettings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.data = data
        self.embedder = embedder
        self.config = config if config is not None else settings.memory.v2.beliefs
        self.clock = clock

    # --- creating and reinforcing -------------------------------------------------------------

    def create(
        self,
        statement: str,
        topic: str,
        evidence: Evidence,
        *,
        about: str = "user",
        origin: str = "night",
    ) -> tuple[Belief, bool]:
        """A new hypothesis backed by ``evidence`` (``(belief, created)``).

        If a live belief about the same subject already says nearly the same, the evidence goes to
        that one instead and ``created`` is False. A new belief starts from a prior that is more
        skeptical on topics she has been wrong about.
        """
        vector: list[float] = []
        model = ""
        if self.embedder is not None:
            vector = self.embedder.embed(statement, "passage")
            model = self.embedder.model_id
            twin = self._twin(vector, model, about)
            if twin is not None:
                self.add_evidence(twin.id, evidence)
                return self.data.beliefs.get(twin.id) or twin, False
        calibration = self.calibration(topic)
        prior_alpha, prior_beta = conf.prior_for(calibration.hits, calibration.misses)
        now = self.clock()
        belief = Belief(
            statement=statement,
            about=about,
            topic=topic,
            alpha=prior_alpha,
            beta=prior_beta,
            prior_alpha=prior_alpha,
            prior_beta=prior_beta,
            origin=origin,
            embedding=vector,
            embedding_model=model,
            created_at=now,
            last_reviewed_at=now,
            updated_at=now,
        )
        conf.apply_evidence(belief, evidence)
        belief.status = conf.next_status(belief, self.config)
        self.data.beliefs.save(belief)
        return belief, True

    def add_evidence(self, belief_id: str, evidence: Evidence) -> Belief | None:
        """Count ``evidence`` for or against a belief (a repeated reference counts once)."""
        belief = self.data.beliefs.get(belief_id)
        if belief is None:
            return None
        before = belief.status
        if conf.apply_evidence(belief, evidence):
            self._settle(belief, before)
        return belief

    def confirm(
        self, belief_id: str, session_id: str | None = None, turn: int | None = None
    ) -> Belief | None:
        """The user confirmed the hypothesis when she asked."""
        belief = self.add_evidence(
            belief_id, Evidence("confirmation", session_id, turn, at=self.clock())
        )
        if belief is not None:
            self._outcome(belief.topic, hit=True)
        return belief

    def deny(
        self, belief_id: str, session_id: str | None = None, turn: int | None = None
    ) -> Belief | None:
        """The user denied it: retired, and a miss on this topic."""
        return self.add_evidence(belief_id, Evidence("denial", session_id, turn, at=self.clock()))

    # --- the nightly review -------------------------------------------------------------------

    def review_all(self) -> ReviewReport:
        """Decay every live belief toward uncertainty, form the ones that earned it, retire the
        wrong and the faded."""
        report = ReviewReport()
        now = self.clock()
        for belief in self.data.beliefs.list({"status": {"$in": list(LIVE)}}):
            before = belief.status
            conf.review(belief, now, self.config)
            report.reviewed += 1
            if before == "candidate" and belief.status == "active":
                report.formed.append(belief.id)
            if belief.status == "retired":
                report.retired.append(belief.id)
            self._persist(belief)
        return report

    # --- reading ------------------------------------------------------------------------------

    def get(self, belief_id: str) -> Belief | None:
        return self.data.beliefs.get(belief_id)

    def held(self) -> list[Belief]:
        """The beliefs she holds (active or revised), newest first."""
        return self.data.beliefs.list({"status": {"$in": list(HELD)}}, [("created_at", -1)])

    def candidates(self) -> list[Belief]:
        return self.data.beliefs.list({"status": "candidate"}, [("created_at", -1)])

    def search(
        self, vector: list[float], top_k: int = 3, threshold: float = 0.5
    ) -> list[Hit[Belief]]:
        """Held beliefs closest in meaning to ``vector`` (needs an embedder for the model id)."""
        if self.embedder is None:
            return []
        return semantic_search(
            self.held(), vector, model=self.embedder.model_id, top_k=top_k, threshold=threshold
        )

    def words(self, belief: Belief) -> str:
        """ "talvez", "acho que" or "tenho bastante certeza" for this belief."""
        return conf.band(conf.confidence(belief.alpha, belief.beta), belief.n)

    def confidence_of(self, belief: Belief) -> float:
        return conf.confidence(belief.alpha, belief.beta)

    def forget(self, belief_id: str) -> bool:
        return self.data.beliefs.delete(belief_id)

    # --- metacognition ------------------------------------------------------------------------

    def calibration(self, topic: str) -> Calibration:
        return self.data.calibration.get(topic) or Calibration(topic)

    def _outcome(self, topic: str, hit: bool) -> None:
        record = self.calibration(topic)
        if hit:
            record.hits += 1.0
        else:
            record.misses += 1.0
        record.updated_at = self.clock()
        self.data.calibration.save(record)

    # --- internals ----------------------------------------------------------------------------

    def _twin(self, vector: list[float], model: str, about: str) -> Belief | None:
        live = [
            b for b in self.data.beliefs.list({"status": {"$in": list(LIVE)}}) if b.about == about
        ]
        hits = semantic_search(live, vector, model=model, top_k=1, threshold=DUPLICATE_SIMILARITY)
        return hits[0].item if hits else None

    def _settle(self, belief: Belief, before: str) -> None:
        """New status after new evidence; a belief she HELD that turns out wrong is a miss."""
        belief.status = conf.next_status(belief, self.config)
        if belief.status == "retired" and before in HELD:
            wrong = belief.user_confirmed == "denied" or (
                conf.confidence(belief.alpha, belief.beta) < conf.RETIRE_BELOW
            )
            if wrong:
                self._outcome(belief.topic, hit=False)
        self._persist(belief)

    def _persist(self, belief: Belief) -> None:
        belief.updated_at = self.clock()
        self.data.beliefs.save(belief)
