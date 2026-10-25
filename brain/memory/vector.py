"""Long-term semantic memory: the ``VectorStore`` interface and its building blocks.

ADR-011, T-055. A memory is one short statement ("O projeto X usa Laravel 11.") with its
embedding. ``VectorStore`` is how the rest of the code adds, searches, updates and deletes
memories; the first implementation (``MongoVectorStore``, in ``brain/memory/mongo.py``) loads
the vectors from MongoDB and ranks them by cosine similarity in Python, which is plenty for a
personal volume. Moving to Atlas Vector Search later only swaps the implementation.

This module has no database or driver import.
"""

from __future__ import annotations

import math
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

import numpy as np

MEMORIES_COLLECTION = "memories"

MemoryType = Literal["preference", "project", "fact"]
MemoryStatus = Literal["active", "superseded"]
SourceKind = Literal["extracted", "explicit"]

MEMORY_TYPES: tuple[str, ...] = ("preference", "project", "fact")
MEMORY_STATUSES: tuple[str, ...] = ("active", "superseded")
SOURCE_KINDS: tuple[str, ...] = ("extracted", "explicit")
# Where a memory stands in the dream's refinement (ADR-018): as it was saved, or with the layer of
# remembering added (context, what she felt, how she was, links). Old documents have no field and
# count as "bruta".
REFLECTION_STATUSES: tuple[str, ...] = ("bruta", "refinada")

# What `update` may change. The id and the creation date never change.
_UPDATABLE = frozenset(
    {
        "text",
        "type",
        "status",
        "superseded_by",
        "embedding",
        "embedding_model",
        "source",
        "status_reflexao",
    }
)


class VectorStoreError(Exception):
    """A memory or a query is invalid (a programming error, not an outage)."""


def new_memory_id() -> str:
    """``mem_`` + UTC time + random suffix: sortable by creation time, unique enough."""
    return f"mem_{datetime.now(UTC):%Y%m%d%H%M%S}{secrets.token_hex(3)}"


@dataclass
class Memory:
    """One long-term memory (the ``memories`` document of ADR-011, section 3)."""

    text: str
    type: str
    embedding: list[float]
    embedding_model: str
    id: str = field(default_factory=new_memory_id)
    status: str = "active"
    superseded_by: str | None = None
    source_kind: str = "extracted"
    session_id: str | None = None
    # Provenance (ADR-017): the last turn of the conversation that was being analyzed (0-based).
    turn: int | None = None
    # The dream's refinement (ADR-018). The fact (``text``) never changes; the refined memory adds
    # the layer of remembering. All empty for a "bruta" memory.
    status_reflexao: str = "bruta"
    contexto: str = ""  # how and why it came up
    sentimento: list[dict[str, Any]] = field(
        default_factory=list
    )  # real feelings: kind, valence, intensity
    como_eu_era: dict[str, Any] = field(default_factory=dict)  # personality version and how she was
    ligacoes: dict[str, list[str]] = field(default_factory=dict)  # memorias, crencas, interesses
    relevancia: float | None = None  # 0..1: how much it matters (weighs a little when recalling)
    refinada_em: datetime | None = None
    refinada_de: str | None = None  # the id of the raw memory it came from
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def validate(self) -> None:
        """Raise ``VectorStoreError`` unless the memory is fit to be stored and searched."""
        if not isinstance(self.text, str) or not self.text.strip():
            raise VectorStoreError("A memória precisa ter texto.")
        if self.type not in MEMORY_TYPES:
            raise VectorStoreError(f"Tipo de memória inválido: {self.type!r} ({MEMORY_TYPES}).")
        if self.status not in MEMORY_STATUSES:
            raise VectorStoreError(f"Status inválido: {self.status!r} ({MEMORY_STATUSES}).")
        if self.source_kind not in SOURCE_KINDS:
            raise VectorStoreError(f"Origem inválida: {self.source_kind!r} ({SOURCE_KINDS}).")
        if self.status_reflexao not in REFLECTION_STATUSES:
            raise VectorStoreError(
                f"Status de reflexão inválido: {self.status_reflexao!r} ({REFLECTION_STATUSES})."
            )
        if self.relevancia is not None and not 0.0 <= float(self.relevancia) <= 1.0:
            raise VectorStoreError("A relevância vai de 0 a 1.")
        if not self.embedding_model:
            raise VectorStoreError("A memória precisa dizer qual modelo gerou o vetor.")
        check_vector(self.embedding)


@dataclass(frozen=True)
class SearchHit:
    memory: Memory
    score: float  # cosine similarity, -1 to 1


@runtime_checkable
class VectorStore(Protocol):
    """Where long-term memories live and how they are found."""

    def add(self, memory: Memory) -> str:
        """Store a new memory and return its id."""
        ...

    def get(self, memory_id: str) -> Memory | None: ...

    def count(self) -> int:
        """How many ``active`` memories there are (cheap: lets callers skip a pointless search)."""
        ...

    def list_memories(
        self, memory_type: str | None = None, include_superseded: bool = False
    ) -> list[Memory]:
        """Memories newest first; ``active`` only unless ``include_superseded``."""
        ...

    def search(
        self,
        vector: Sequence[float],
        top_k: int | None = None,
        threshold: float | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> list[SearchHit]:
        """The ``active`` memories closest to ``vector``, best first.

        Only memories made by the store's embedding model are compared. ``filters`` are
        equality conditions on stored fields (e.g. ``{"type": "project"}``); they can never
        make a ``superseded`` memory visible.
        """
        ...

    def update(self, memory_id: str, **fields: Any) -> bool:
        """Change fields of a memory (``updated_at`` is refreshed). False if it does not exist."""
        ...

    def delete(self, memory_id: str) -> bool:
        """Remove a memory for good. False if it does not exist."""
        ...


def check_vector(vector: Sequence[float]) -> None:
    """Raise ``VectorStoreError`` unless ``vector`` is a usable embedding."""
    if len(vector) == 0:
        raise VectorStoreError("O vetor está vazio.")
    try:
        array = np.asarray(vector, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise VectorStoreError("O vetor precisa ser uma lista de números.") from exc
    if array.ndim != 1 or not np.all(np.isfinite(array)):
        raise VectorStoreError("O vetor precisa ser uma lista de números finitos.")
    if not np.any(array):
        raise VectorStoreError("O vetor é todo zero, não dá para compará-lo.")


def cosine_scores(
    query: Sequence[float], matrix: Sequence[Sequence[float]]
) -> np.ndarray[Any, Any]:
    """Cosine similarity between ``query`` and every row of ``matrix`` (rows need equal length).

    Vectors are normalized here, so it does not matter whether the model already did it.
    """
    q = np.asarray(query, dtype=np.float64)
    m = np.asarray(matrix, dtype=np.float64)
    if m.size == 0:
        return np.zeros(0)
    norms = np.linalg.norm(m, axis=1) * np.linalg.norm(q)
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = (m @ q) / norms
    return np.where(norms > 0, scores, -1.0)


def rank(
    query: Sequence[float],
    candidates: Sequence[tuple[Memory, Sequence[float]]],
    top_k: int,
    threshold: float,
) -> list[SearchHit]:
    """Best ``top_k`` candidates with score >= ``threshold``. Wrong-sized vectors are skipped."""
    size = len(query)
    usable = [(m, v) for m, v in candidates if len(v) == size]
    if not usable:
        return []
    scores = cosine_scores(query, [v for _, v in usable])
    order = sorted(range(len(usable)), key=lambda i: (-scores[i], usable[i][0].id))
    return [
        SearchHit(usable[i][0], float(scores[i]))
        for i in order
        if math.isfinite(scores[i]) and scores[i] >= threshold
    ][:top_k]


def memory_to_doc(memory: Memory) -> dict[str, Any]:
    """The MongoDB document of ADR-011 (``_id`` is the memory id)."""
    return {
        "_id": memory.id,
        "text": memory.text,
        "type": memory.type,
        "status": memory.status,
        "superseded_by": memory.superseded_by,
        "embedding": [float(x) for x in memory.embedding],
        "embedding_model": memory.embedding_model,
        "source": {
            "kind": memory.source_kind,
            "session_id": memory.session_id,
            "turn": memory.turn,
        },
        "status_reflexao": memory.status_reflexao,
        "lembranca": _remembering_to_doc(memory),
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


def _remembering_to_doc(memory: Memory) -> dict[str, Any] | None:
    """The layer of remembering of a refined memory (``None`` for a raw one)."""
    if memory.status_reflexao != "refinada":
        return None
    return {
        "contexto": memory.contexto,
        "sentimento": [dict(item) for item in memory.sentimento],
        "como_eu_era": dict(memory.como_eu_era),
        "ligacoes": {k: list(v) for k, v in memory.ligacoes.items()},
        "relevancia": memory.relevancia,
        "refinada_em": memory.refinada_em,
        "refinada_de": memory.refinada_de,
    }


def memory_from_doc(doc: Mapping[str, Any]) -> Memory:
    """Inverse of ``memory_to_doc``. Raises ``VectorStoreError`` for a damaged document."""
    try:
        source = doc.get("source") or {}
        memory = Memory(
            id=str(doc["_id"]),
            text=doc["text"],
            type=doc["type"],
            status=doc.get("status", "active"),
            superseded_by=doc.get("superseded_by"),
            embedding=[float(x) for x in doc["embedding"]],
            embedding_model=doc["embedding_model"],
            source_kind=source.get("kind", "extracted"),
            session_id=source.get("session_id"),
            turn=source.get("turn"),
            status_reflexao=doc.get("status_reflexao") or "bruta",
            **_remembering_from_doc(doc.get("lembranca")),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )
        memory.validate()
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise VectorStoreError(f"memória corrompida ({doc.get('_id')}): {exc}") from exc
    return memory


def _remembering_from_doc(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    if not doc:
        return {}
    relevance = doc.get("relevancia")
    return {
        "contexto": str(doc.get("contexto") or ""),
        "sentimento": [dict(item) for item in doc.get("sentimento") or []],
        "como_eu_era": dict(doc.get("como_eu_era") or {}),
        "ligacoes": {k: [str(i) for i in v] for k, v in (doc.get("ligacoes") or {}).items()},
        "relevancia": None if relevance is None else float(relevance),
        "refinada_em": doc.get("refinada_em"),
        "refinada_de": doc.get("refinada_de"),
    }


def check_update_fields(fields: Mapping[str, Any]) -> None:
    """Raise ``VectorStoreError`` for fields ``update`` may not change, or invalid values."""
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise VectorStoreError(f"Campos que não podem ser alterados: {sorted(unknown)}.")
    if "text" in fields and not (isinstance(fields["text"], str) and fields["text"].strip()):
        raise VectorStoreError("A memória precisa ter texto.")
    if "type" in fields and fields["type"] not in MEMORY_TYPES:
        raise VectorStoreError(f"Tipo de memória inválido: {fields['type']!r}.")
    if "status" in fields and fields["status"] not in MEMORY_STATUSES:
        raise VectorStoreError(f"Status inválido: {fields['status']!r}.")
    if "status_reflexao" in fields and fields["status_reflexao"] not in REFLECTION_STATUSES:
        raise VectorStoreError(f"Status de reflexão inválido: {fields['status_reflexao']!r}.")
    if "embedding" in fields:
        check_vector(fields["embedding"])
        if not fields.get("embedding_model"):
            raise VectorStoreError("Ao trocar o vetor, informe também o embedding_model.")
