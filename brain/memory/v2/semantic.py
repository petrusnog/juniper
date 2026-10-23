"""Search by meaning over V2 items that carry an embedding (beliefs, interests).

Same idea as the v1 ``VectorStore``: at personal volume (tens to a few thousand items) cosine in
numpy is plenty. Items made by another embedding model are ignored, since their vectors are not
comparable; ``juniper memory reindex`` brings them up to date.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from brain.memory.vector import cosine_scores


class Embedded(Protocol):
    id: str
    embedding: list[float]
    embedding_model: str


E = TypeVar("E", bound=Embedded)


@dataclass(frozen=True)
class Hit(Generic[E]):
    item: E
    score: float  # cosine similarity, -1 to 1


def semantic_search(
    items: Sequence[E],
    vector: Sequence[float],
    *,
    model: str,
    top_k: int = 5,
    threshold: float = 0.0,
) -> list[Hit[E]]:
    """The ``top_k`` items closest to ``vector`` (made by ``model``) with score >= ``threshold``."""
    usable = [i for i in items if i.embedding_model == model and len(i.embedding) == len(vector)]
    if not usable or top_k < 1:
        return []
    scores = cosine_scores(vector, [i.embedding for i in usable])
    order = sorted(range(len(usable)), key=lambda k: (-scores[k], usable[k].id))
    hits: list[Hit[Any]] = [
        Hit(usable[k], float(scores[k]))
        for k in order
        if math.isfinite(scores[k]) and scores[k] >= threshold
    ]
    return hits[:top_k]
