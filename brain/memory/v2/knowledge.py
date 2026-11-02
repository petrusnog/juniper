"""What she noted while studying: short claims with their uncertainty, and open questions (ADR-019).

The notes are *second-hand* knowledge: what the model knows (``source="modelo"``), what Petrus
taught her in a document (``source="documento"``) or what she read on the web (``source="web"``,
ADR-031, with the URL, the date and how fast it goes stale). They are auditable and deletable,
they do not repeat, and each topic keeps a limited number of them: when it is full, the oldest and
least reliable go first (consolidation is code, not a model's call).

For web notes the uncertainty is code too: one source is ``média``; the same claim read on a
second domain is corroborated and becomes ``baixa``; claims that contradict each other are both
``alta``. A note past its validity stops counting as knowledge until she reads it again.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from brain.config import V2InterestSettings, V2KnowledgeSettings, settings
from brain.memory.v2.models import UNCERTAINTIES, Interest, StudyNote, utcnow

if TYPE_CHECKING:
    from brain.memory.embeddings import EmbeddingProvider
    from brain.memory.v2.store import V2Data

SAME_NOTE_SIMILARITY = 0.93
# Marks for the prompt: how sure she is of a claim.
UNCERTAINTY_MARKS = {"baixa": "", "média": " (não tenho certeza total)", "alta": " (incerto)"}


def _key(text: str) -> str:
    return " ".join(text.casefold().strip(" .!?…").split())


class NoteBook:
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

    def of(self, interest_id: str) -> list[StudyNote]:
        """The notes of a topic, oldest first."""
        return self.data.notes.list({"interest_id": interest_id}, [("created_at", 1)])

    def claims(self, interest_id: str) -> list[StudyNote]:
        return [n for n in self.of(interest_id) if n.kind == "afirmacao"]

    def questions(self, interest_id: str) -> list[StudyNote]:
        return [n for n in self.of(interest_id) if n.kind == "pergunta"]

    # --- writing ------------------------------------------------------------------------------

    def add_claim(
        self,
        interest: Interest,
        text: str,
        uncertainty: str,
        level: int,
        source: str = "modelo",
        *,
        url: str = "",
        domain: str = "",
        volatility: str = "estavel",
    ) -> StudyNote | None:
        """Keep a claim unless she already has it. Returns ``None`` for a duplicate; a web claim
        she already had from *another* domain corroborates the old note instead."""
        uncertainty = uncertainty if uncertainty in UNCERTAINTIES else "alta"
        clean = " ".join(text.split())
        existing = self.claims(interest.id)
        vector, model = self._embed(clean)
        twin = next((n for n in existing if _key(n.text) == _key(clean)), None)
        if twin is None and vector:
            twin = next(
                (
                    n
                    for n in existing
                    if n.embedding_model == model
                    and n.embedding
                    and _cosine(n.embedding, vector) >= SAME_NOTE_SIMILARITY
                ),
                None,
            )
        if twin is not None:
            if source == "web" and domain:
                self._corroborate(twin, domain)
            return None
        web = source == "web"
        note = StudyNote(
            interest_id=interest.id,
            text=clean,
            kind="afirmacao",
            uncertainty=uncertainty,
            source=source,
            level=level,
            embedding=vector,
            embedding_model=model,
            created_at=self.clock(),
            updated_at=self.clock(),
            url=url if web else "",
            domains=[domain] if web and domain else [],
            fetched_at=self.clock() if web else None,
            volatility=volatility if web else "estavel",
        )
        self.data.notes.save(note)
        return note

    def _corroborate(self, note: StudyNote, domain: str) -> None:
        """The same claim from another site: one more domain, firmer once two agree; and a
        fresh reading (the validity starts again)."""
        domains = list(dict.fromkeys([*note.domains, domain]))
        fields: dict[str, object] = {"fetched_at": self.clock(), "updated_at": self.clock()}
        if domains != note.domains:
            fields["domains"] = domains
            if len(domains) >= 2 and note.uncertainty != "alta":
                fields["uncertainty"] = "baixa"
        if note.source == "web":
            self.data.notes.update(note.id, **fields)

    def contradict(self, first: StudyNote, second: StudyNote) -> None:
        """Two claims that contradict each other: both stay, both become uncertain."""
        for note in (first, second):
            self.data.notes.update(note.id, uncertainty="alta", updated_at=self.clock())

    def set_questions(self, interest: Interest, questions: list[str], level: int) -> int:
        """The open questions for the next step replace the old ones (answered or dropped)."""
        self.data.notes.delete_where({"interest_id": interest.id, "kind": "pergunta"})
        kept = 0
        for question in questions[: self.config.max_open_questions]:
            self.data.notes.save(
                StudyNote(
                    interest_id=interest.id,
                    text=" ".join(question.split()),
                    kind="pergunta",
                    uncertainty="alta",
                    level=level,
                    created_at=self.clock(),
                    updated_at=self.clock(),
                )
            )
            kept += 1
        return kept

    def consolidate(self, interest: Interest) -> int:
        """Over the limit, drop the most uncertain notes, oldest first. Returns how many went."""
        claims = self.claims(interest.id)
        extra = len(claims) - self.config.max_notes_per_topic
        if extra <= 0:
            return 0
        order = {name: rank for rank, name in enumerate(UNCERTAINTIES)}  # baixa=0 (most reliable)
        doomed = sorted(claims, key=lambda n: (-order[n.uncertainty], n.created_at, n.id))[:extra]
        for note in doomed:
            self.data.notes.delete(note.id)
        return len(doomed)

    def forget(self, interest_id: str) -> int:
        return self.data.notes.delete_where({"interest_id": interest_id})

    def forget_one(self, note_id: str) -> bool:
        return self.data.notes.delete(note_id)

    # --- helpers ------------------------------------------------------------------------------

    def _embed(self, text: str) -> tuple[list[float], str]:
        if self.embedder is None:
            return [], ""
        vector = [float(x) for x in self.embedder.embed(text, "passage")]
        return vector, self.embedder.model_id

    def render(self, interest_id: str) -> tuple[str, str]:
        """The notes as lines for a prompt: (claims with their uncertainty, open questions)."""
        lines = [f"- {n.text}{UNCERTAINTY_MARKS[n.uncertainty]}" for n in self.claims(interest_id)]
        asks = [f"- {n.text}" for n in self.questions(interest_id)]
        return "\n".join(lines) or "(nenhuma ainda)", "\n".join(asks) or "(nenhuma)"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def validity(note: StudyNote, config: V2KnowledgeSettings | None = None) -> timedelta | None:
    """How long a web note stays valid (``None``: it never expires: not a web note)."""
    if note.source != "web" or note.fetched_at is None:
        return None
    config = config or settings.memory.v2.knowledge
    if note.volatility == "noticia":
        return timedelta(hours=config.news_hours)
    if note.volatility == "atual":
        return timedelta(days=config.current_days)
    return timedelta(days=config.stable_days)


def expired(note: StudyNote, now: datetime, config: V2KnowledgeSettings | None = None) -> bool:
    """Whether a web note is past its validity (it no longer counts as knowledge)."""
    span = validity(note, config)
    return span is not None and note.fetched_at is not None and now - note.fetched_at > span
