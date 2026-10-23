"""Data of the Memória V2 layers (ADR-017): beliefs, interests, affects, concerns, persona.

Plain dataclasses with ``to_doc`` / ``from_doc``. A damaged document raises ``V2DataError`` from
``from_doc`` so listings can skip it without hiding the good ones. No database import here.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

# Collections (ADR-017, section 12). ``memories`` is the v1 collection (layer 1, the facts).
BELIEFS = "beliefs"
INTERESTS = "interests"
AFFECTS = "affects"
CONCERNS = "concerns"
PERSONA_STATE = "persona_state"
PERSONA_HISTORY = "persona_history"
JOURNAL = "journal"
DREAM_RUNS = "dream_runs"
DREAM_WATERMARKS = "dream_watermarks"
TRANSCRIPT_TURNS = "transcript_turns"
CALIBRATION = "calibration"
PERSONA_ID = "current"

BELIEF_ABOUT = ("user", "decision")
# ``candidate`` is a hypothesis still short of the minimum evidence (not a belief yet).
BELIEF_STATUSES = ("candidate", "active", "revised", "retired")
BELIEF_ORIGINS = ("night", "conversation")
USER_CONFIRMATIONS = ("confirmed", "denied")
EVIDENCE_KINDS = ("observation", "explicit", "confirmation", "denial", "contradiction")
STANCES = ("undecided", "like", "dislike", "mixed")
AFFECT_ABOUT = ("user", "self", "situation")
AFFECT_PERSISTENCE = ("emotion", "grievance")
JOURNAL_KINDS = ("dream", "study", "persona", "note")
RUN_STATUSES = ("running", "done", "partial", "failed")


class V2DataError(ValueError):
    """A V2 document or value is not valid (a damaged document, or a programming error)."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """``<prefix>_`` + UTC time + random suffix: sortable by creation time, unique enough."""
    return f"{prefix}_{utcnow():%Y%m%d%H%M%S}{secrets.token_hex(3)}"


def as_dt(value: Any) -> datetime:
    """A timezone-aware datetime from a stored value (naive means UTC)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise V2DataError(f"data inválida: {value!r}") from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise V2DataError(f"data inválida: {value!r}")


def _opt_dt(value: Any) -> datetime | None:
    return None if value is None else as_dt(value)


def _unit(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise V2DataError(f"{name} precisa ser um número") from exc
    if not 0.0 <= number <= 1.0:
        raise V2DataError(f"{name} fora de 0..1: {number}")
    return number


def _choice(value: Any, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise V2DataError(f"{name} inválido: {value!r} (use {allowed})")
    return str(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V2DataError(f"{name} precisa ter texto")
    return value


def _vector(doc: Mapping[str, Any]) -> list[float]:
    raw = doc.get("embedding") or []
    try:
        return [float(x) for x in raw]
    except (TypeError, ValueError) as exc:
        raise V2DataError("vetor inválido") from exc


# --- evidence ---------------------------------------------------------------------------------


@dataclass
class Evidence:
    """A reference to what supports or contradicts something (never the text itself)."""

    kind: str
    session_id: str | None = None
    turn: int | None = None
    fact_id: str | None = None
    weight: float = 1.0
    at: datetime = field(default_factory=utcnow)

    def to_doc(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "session_id": self.session_id,
            "turn": self.turn,
            "fact_id": self.fact_id,
            "weight": self.weight,
            "at": self.at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> Evidence:
        try:
            turn = doc.get("turn")
            return cls(
                kind=_choice(doc.get("kind"), EVIDENCE_KINDS, "evidência"),
                session_id=doc.get("session_id"),
                turn=None if turn is None else int(turn),
                fact_id=doc.get("fact_id"),
                weight=float(doc.get("weight", 1.0)),
                at=as_dt(doc["at"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise V2DataError(f"evidência inválida: {exc}") from exc


def _evidence_list(raw: Any) -> list[Evidence]:
    return [Evidence.from_doc(e) for e in (raw or [])]


# --- Umwelt: beliefs and interests ------------------------------------------------------------


@dataclass
class Belief:
    """A hypothesis in her own voice, with confidence computed from evidence (T-101)."""

    statement: str
    about: str = "user"
    topic: str = "geral"
    id: str = field(default_factory=lambda: new_id("bel"))
    evidence_for: list[Evidence] = field(default_factory=list)
    evidence_against: list[Evidence] = field(default_factory=list)
    alpha: float = 1.0
    beta: float = 1.0
    # The weak prior it started from (more skeptical on topics she tends to get wrong), so the
    # accumulated evidence is what came on top of it.
    prior_alpha: float = 1.0
    prior_beta: float = 1.0
    status: str = "candidate"
    origin: str = "night"
    user_confirmed: str | None = None
    embedding: list[float] = field(default_factory=list)
    embedding_model: str = ""
    created_at: datetime = field(default_factory=utcnow)
    last_reviewed_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    @property
    def n(self) -> float:
        """Accumulated evidence: how much has been seen beyond the weak prior."""
        return self.alpha + self.beta - self.prior_alpha - self.prior_beta

    def validate(self) -> None:
        _text(self.statement, "statement")
        _choice(self.about, BELIEF_ABOUT, "about")
        _choice(self.status, BELIEF_STATUSES, "status")
        _choice(self.origin, BELIEF_ORIGINS, "origin")
        if self.user_confirmed is not None:
            _choice(self.user_confirmed, USER_CONFIRMATIONS, "user_confirmed")
        if min(self.alpha, self.beta, self.prior_alpha, self.prior_beta) <= 0:
            raise V2DataError("alpha, beta e os a priori precisam ser positivos")

    def to_doc(self) -> dict[str, Any]:
        self.validate()
        return {
            "_id": self.id,
            "statement": self.statement,
            "about": self.about,
            "topic": self.topic,
            "evidence_for": [e.to_doc() for e in self.evidence_for],
            "evidence_against": [e.to_doc() for e in self.evidence_against],
            "alpha": self.alpha,
            "beta": self.beta,
            "prior_alpha": self.prior_alpha,
            "prior_beta": self.prior_beta,
            "n": self.n,
            "status": self.status,
            "origin": self.origin,
            "user_confirmed": self.user_confirmed,
            "embedding": [float(x) for x in self.embedding],
            "embedding_model": self.embedding_model,
            "created_at": self.created_at,
            "last_reviewed_at": self.last_reviewed_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> Belief:
        try:
            belief = cls(
                id=str(doc["_id"]),
                statement=doc["statement"],
                about=doc.get("about", "user"),
                topic=doc.get("topic", "geral"),
                evidence_for=_evidence_list(doc.get("evidence_for")),
                evidence_against=_evidence_list(doc.get("evidence_against")),
                alpha=float(doc.get("alpha", 1.0)),
                beta=float(doc.get("beta", 1.0)),
                prior_alpha=float(doc.get("prior_alpha", 1.0)),
                prior_beta=float(doc.get("prior_beta", 1.0)),
                status=doc.get("status", "candidate"),
                origin=doc.get("origin", "night"),
                user_confirmed=doc.get("user_confirmed"),
                embedding=_vector(doc),
                embedding_model=doc.get("embedding_model", ""),
                created_at=as_dt(doc["created_at"]),
                last_reviewed_at=as_dt(doc.get("last_reviewed_at", doc["created_at"])),
                updated_at=as_dt(doc.get("updated_at", doc["created_at"])),
            )
            belief.validate()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise V2DataError(f"crença corrompida ({doc.get('_id')}): {exc}") from exc
        return belief


@dataclass
class Interest:
    """A topic that interests her, and where she stands on it (T-113)."""

    topic: str
    id: str = field(default_factory=lambda: new_id("int"))
    intensity: float = 0.3
    stance: str = "undecided"
    alpha: float = 1.0
    beta: float = 1.0
    reasons: list[str] = field(default_factory=list)
    origin: Evidence | None = None
    experience: str = "reading"  # second-hand taste: she read about it, she did not live it
    exposures: int = 0
    last_studied_at: datetime | None = None
    embedding: list[float] = field(default_factory=list)
    embedding_model: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def validate(self) -> None:
        _text(self.topic, "topic")
        _unit(self.intensity, "intensity")
        _choice(self.stance, STANCES, "stance")
        if self.alpha <= 0 or self.beta <= 0:
            raise V2DataError("alpha e beta precisam ser positivos")

    def to_doc(self) -> dict[str, Any]:
        self.validate()
        return {
            "_id": self.id,
            "topic": self.topic,
            "intensity": self.intensity,
            "stance": self.stance,
            "alpha": self.alpha,
            "beta": self.beta,
            "reasons": list(self.reasons),
            "origin": None if self.origin is None else self.origin.to_doc(),
            "experience": self.experience,
            "exposures": self.exposures,
            "last_studied_at": self.last_studied_at,
            "embedding": [float(x) for x in self.embedding],
            "embedding_model": self.embedding_model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> Interest:
        try:
            origin = doc.get("origin")
            interest = cls(
                id=str(doc["_id"]),
                topic=doc["topic"],
                intensity=float(doc.get("intensity", 0.3)),
                stance=doc.get("stance", "undecided"),
                alpha=float(doc.get("alpha", 1.0)),
                beta=float(doc.get("beta", 1.0)),
                reasons=[str(r) for r in doc.get("reasons") or []],
                origin=None if origin is None else Evidence.from_doc(origin),
                experience=doc.get("experience", "reading"),
                exposures=int(doc.get("exposures", 0)),
                last_studied_at=_opt_dt(doc.get("last_studied_at")),
                embedding=_vector(doc),
                embedding_model=doc.get("embedding_model", ""),
                created_at=as_dt(doc["created_at"]),
                updated_at=as_dt(doc.get("updated_at", doc["created_at"])),
            )
            interest.validate()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise V2DataError(f"interesse corrompido ({doc.get('_id')}): {exc}") from exc
        return interest


# --- affect -----------------------------------------------------------------------------------


@dataclass
class Affect:
    """One emotion she had about something that happened (T-109)."""

    kind: str
    valence: float
    intensity: float
    cause: list[str] = field(default_factory=list)  # "fact:<id>", "belief:<id>", "concern:<id>"
    about: str = "user"
    persistence: str = "emotion"
    id: str = field(default_factory=lambda: new_id("aff"))
    occurred_at: datetime = field(default_factory=utcnow)
    resolved_at: datetime | None = None  # a grievance undone by an apology or a talk
    created_at: datetime = field(default_factory=utcnow)

    def validate(self) -> None:
        _text(self.kind, "kind")
        if not -1.0 <= float(self.valence) <= 1.0:
            raise V2DataError(f"valence fora de -1..1: {self.valence}")
        _unit(self.intensity, "intensity")
        _choice(self.about, AFFECT_ABOUT, "about")
        _choice(self.persistence, AFFECT_PERSISTENCE, "persistence")

    def to_doc(self) -> dict[str, Any]:
        self.validate()
        return {
            "_id": self.id,
            "kind": self.kind,
            "valence": float(self.valence),
            "intensity": float(self.intensity),
            "cause": list(self.cause),
            "about": self.about,
            "persistence": self.persistence,
            "occurred_at": self.occurred_at,
            "resolved_at": self.resolved_at,
            "created_at": self.created_at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> Affect:
        try:
            affect = cls(
                id=str(doc["_id"]),
                kind=doc["kind"],
                valence=float(doc["valence"]),
                intensity=float(doc["intensity"]),
                cause=[str(c) for c in doc.get("cause") or []],
                about=doc.get("about", "user"),
                persistence=doc.get("persistence", "emotion"),
                occurred_at=as_dt(doc["occurred_at"]),
                resolved_at=_opt_dt(doc.get("resolved_at")),
                created_at=as_dt(doc.get("created_at", doc["occurred_at"])),
            )
            affect.validate()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise V2DataError(f"sentimento corrompido ({doc.get('_id')}): {exc}") from exc
        return affect


# --- concerns ---------------------------------------------------------------------------------


@dataclass
class ConcernChange:
    at: datetime
    delta: float
    reason: str
    event_ref: str | None = None

    def to_doc(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "delta": self.delta,
            "reason": self.reason,
            "event_ref": self.event_ref,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> ConcernChange:
        try:
            return cls(
                as_dt(doc["at"]), float(doc["delta"]), str(doc["reason"]), doc.get("event_ref")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise V2DataError(f"mudança de preocupação inválida: {exc}") from exc


@dataclass
class Concern:
    """Something that matters to her (T-108). A value, not a probability."""

    id: str  # a stable slug, e.g. "bem-estar-do-petrus"
    name: str
    weight: float
    baseline_weight: float
    description: str = ""
    history: list[ConcernChange] = field(default_factory=list)
    updated_at: datetime = field(default_factory=utcnow)

    def validate(self) -> None:
        _text(self.id, "id")
        _text(self.name, "name")
        _unit(self.weight, "weight")
        _unit(self.baseline_weight, "baseline_weight")

    def to_doc(self) -> dict[str, Any]:
        self.validate()
        return {
            "_id": self.id,
            "name": self.name,
            "description": self.description,
            "weight": self.weight,
            "baseline_weight": self.baseline_weight,
            "history": [c.to_doc() for c in self.history],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> Concern:
        try:
            concern = cls(
                id=str(doc["_id"]),
                name=doc["name"],
                weight=float(doc["weight"]),
                baseline_weight=float(doc.get("baseline_weight", doc["weight"])),
                description=doc.get("description", ""),
                history=[ConcernChange.from_doc(c) for c in doc.get("history") or []],
                updated_at=as_dt(doc.get("updated_at", utcnow())),
            )
            concern.validate()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise V2DataError(f"preocupação corrompida ({doc.get('_id')}): {exc}") from exc
        return concern


# --- persona ----------------------------------------------------------------------------------


@dataclass
class Trait:
    value: float
    baseline: float
    min: float = 0.0
    max: float = 1.0

    def validate(self, name: str) -> None:
        if not (0.0 <= self.min <= self.max <= 1.0):
            raise V2DataError(f"faixa inválida do traço {name}")
        if not self.min <= self.value <= self.max:
            raise V2DataError(f"traço {name} fora da faixa: {self.value}")
        if not self.min <= self.baseline <= self.max:
            raise V2DataError(f"baseline do traço {name} fora da faixa: {self.baseline}")

    def to_doc(self) -> dict[str, float]:
        return {"value": self.value, "baseline": self.baseline, "min": self.min, "max": self.max}

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> Trait:
        try:
            return cls(
                float(doc["value"]), float(doc["baseline"]), float(doc["min"]), float(doc["max"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise V2DataError(f"traço inválido: {exc}") from exc


@dataclass
class PersonaState:
    """The living personality: numeric traits and a few lines of what marked her (T-111)."""

    traits: dict[str, Trait] = field(default_factory=dict)
    recent: list[str] = field(default_factory=list)
    version: int = 1
    changed_by: list[str] = field(default_factory=list)  # ids of the events behind this version
    updated_at: datetime = field(default_factory=utcnow)

    def validate(self) -> None:
        if self.version < 1:
            raise V2DataError("version precisa ser >= 1")
        for name, trait in self.traits.items():
            trait.validate(name)
        if len(self.recent) > 3:
            raise V2DataError("recent tem no máximo 3 frases")

    def to_doc(self, doc_id: str = PERSONA_ID) -> dict[str, Any]:
        self.validate()
        return {
            "_id": doc_id,
            "traits": {name: t.to_doc() for name, t in self.traits.items()},
            "recent": list(self.recent),
            "version": self.version,
            "changed_by": list(self.changed_by),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> PersonaState:
        try:
            state = cls(
                traits={str(k): Trait.from_doc(v) for k, v in (doc.get("traits") or {}).items()},
                recent=[str(r) for r in doc.get("recent") or []],
                version=int(doc.get("version", 1)),
                changed_by=[str(c) for c in doc.get("changed_by") or []],
                updated_at=as_dt(doc.get("updated_at", utcnow())),
            )
            state.validate()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise V2DataError(f"personalidade corrompida: {exc}") from exc
        return state


@dataclass
class PersonaVersion:
    """A past version of the personality, kept so it can be reverted (T-112)."""

    state: PersonaState
    reason: str = ""
    at: datetime = field(default_factory=utcnow)

    @property
    def id(self) -> str:
        return f"v{self.state.version:06d}"

    def to_doc(self) -> dict[str, Any]:
        doc = self.state.to_doc(self.id)
        doc.update(reason=self.reason, at=self.at)
        return doc

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> PersonaVersion:
        try:
            return cls(PersonaState.from_doc(doc), str(doc.get("reason", "")), as_dt(doc["at"]))
        except (KeyError, TypeError) as exc:
            raise V2DataError(f"versão da personalidade inválida: {exc}") from exc


# --- transcripts ------------------------------------------------------------------------------


@dataclass
class TranscriptTurn:
    """One finished exchange, kept whole (ADR-017, section 9, phase 1).

    The saved session is *compacted* (only the last turns survive), so the nightly pass could not
    see what fell out of the window. This is the full text of what the user wrote and what she
    answered, never tool output. ``_id`` is ``<session id>:<turn index>``, so writing a turn twice
    is harmless.
    """

    session_id: str
    index: int
    user: str
    assistant: str = ""
    used_tools: bool = (
        False  # her reply may carry tool output: it must not feed the subjective layers
    )
    at: datetime = field(default_factory=utcnow)

    @property
    def id(self) -> str:
        return f"{self.session_id}:{self.index:06d}"

    def to_doc(self) -> dict[str, Any]:
        _text(self.session_id, "session_id")
        if self.index < 0:
            raise V2DataError("index não pode ser negativo")
        return {
            "_id": self.id,
            "session_id": self.session_id,
            "index": self.index,
            "user": self.user,
            "assistant": self.assistant,
            "used_tools": self.used_tools,
            "at": self.at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> TranscriptTurn:
        try:
            return cls(
                session_id=_text(doc["session_id"], "session_id"),
                index=int(doc["index"]),
                user=str(doc.get("user", "")),
                assistant=str(doc.get("assistant", "")),
                used_tools=bool(doc.get("used_tools", False)),
                at=as_dt(doc["at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise V2DataError(f"turno da transcrição corrompido ({doc.get('_id')}): {exc}") from exc


# --- metacognition ----------------------------------------------------------------------------


@dataclass
class Calibration:
    """How right her opinions have turned out on one topic (``_id`` is the topic)."""

    topic: str
    hits: float = 0.0
    misses: float = 0.0
    updated_at: datetime = field(default_factory=utcnow)

    def to_doc(self) -> dict[str, Any]:
        _text(self.topic, "topic")
        if self.hits < 0 or self.misses < 0:
            raise V2DataError("hits e misses não podem ser negativos")
        return {
            "_id": self.topic,
            "hits": self.hits,
            "misses": self.misses,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> Calibration:
        try:
            return cls(
                str(doc["_id"]),
                float(doc.get("hits", 0.0)),
                float(doc.get("misses", 0.0)),
                as_dt(doc.get("updated_at", utcnow())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise V2DataError(f"calibração corrompida: {exc}") from exc


# --- the night --------------------------------------------------------------------------------


@dataclass
class JournalEntry:
    """What the night changed and what she studied, in a few lines (T-103)."""

    kind: str
    text: str
    day: str = field(default_factory=lambda: date.today().isoformat())
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("jrn"))
    at: datetime = field(default_factory=utcnow)

    def validate(self) -> None:
        _choice(self.kind, JOURNAL_KINDS, "kind")
        _text(self.text, "text")

    def to_doc(self) -> dict[str, Any]:
        self.validate()
        return {
            "_id": self.id,
            "kind": self.kind,
            "text": self.text,
            "day": self.day,
            "data": self.data,
            "at": self.at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> JournalEntry:
        try:
            entry = cls(
                id=str(doc["_id"]),
                kind=doc["kind"],
                text=doc["text"],
                day=str(doc["day"]),
                data=dict(doc.get("data") or {}),
                at=as_dt(doc["at"]),
            )
            entry.validate()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise V2DataError(f"entrada do diário corrompida ({doc.get('_id')}): {exc}") from exc
        return entry


@dataclass
class DreamRun:
    """One execution of the nightly pass."""

    id: str = field(default_factory=lambda: new_id("run"))
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    dry_run: bool = False
    status: str = "running"
    phases: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    sessions_read: int = 0
    error: str | None = None

    def validate(self) -> None:
        _choice(self.status, RUN_STATUSES, "status")

    def to_doc(self) -> dict[str, Any]:
        self.validate()
        return {
            "_id": self.id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "status": self.status,
            "phases": self.phases,
            "tokens_used": self.tokens_used,
            "sessions_read": self.sessions_read,
            "error": self.error,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> DreamRun:
        try:
            run = cls(
                id=str(doc["_id"]),
                started_at=as_dt(doc["started_at"]),
                finished_at=_opt_dt(doc.get("finished_at")),
                dry_run=bool(doc.get("dry_run", False)),
                status=doc.get("status", "running"),
                phases=list(doc.get("phases") or []),
                tokens_used=int(doc.get("tokens_used", 0)),
                sessions_read=int(doc.get("sessions_read", 0)),
                error=doc.get("error"),
            )
            run.validate()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise V2DataError(f"execução da passada corrompida ({doc.get('_id')}): {exc}") from exc
        return run


@dataclass
class Watermark:
    """Up to which turn of a session the night has read (``_id`` is the session id)."""

    session_id: str
    turns_read: int = 0
    updated_at: datetime = field(default_factory=utcnow)

    def to_doc(self) -> dict[str, Any]:
        if self.turns_read < 0:
            raise V2DataError("turns_read não pode ser negativo")
        return {
            "_id": self.session_id,
            "turns_read": self.turns_read,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> Watermark:
        try:
            return cls(
                str(doc["_id"]),
                int(doc.get("turns_read", 0)),
                as_dt(doc.get("updated_at", utcnow())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise V2DataError(f"marca d'água corrompida: {exc}") from exc
