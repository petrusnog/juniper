"""Re-embed stored memories after the embedding model changes (ADR-011, T-054).

Vectors from different models are not comparable, so a memory whose ``embedding_model`` differs
from the configured one is invisible to a correct search until it is re-embedded. This module
finds those memories and rewrites their vectors in place. It talks to the ``memories``
collection through the small slice of the driver API used below, so it needs no driver import.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from brain.memory.embeddings import EmbeddingProvider
from brain.memory.vector import MEMORIES_COLLECTION

__all__ = ["MEMORIES_COLLECTION", "ReindexResult", "reindex_memories"]


@dataclass(frozen=True)
class ReindexResult:
    total: int  # memories examined
    reindexed: int  # vectors rewritten
    skipped: int  # already on the current model


def reindex_memories(
    collection: Any,
    provider: EmbeddingProvider,
    *,
    force: bool = False,
    batch_size: int = 16,
    progress: Callable[[int, int], None] | None = None,
) -> ReindexResult:
    """Re-embed every memory not made by ``provider.model_id`` (all of them with ``force``).

    Superseded memories are included: they keep their audit trail consistent with the model.
    Memories with no text are left alone. Safe to re-run: a memory is only marked with the new
    model after its new vector is saved, so an interrupted run resumes where it stopped.
    """
    docs = list(collection.find({}, {"text": 1, "embedding_model": 1}))
    todo = [d for d in docs if force or d.get("embedding_model") != provider.model_id]
    todo = [d for d in todo if isinstance(d.get("text"), str) and d["text"].strip()]
    done = 0
    for start in range(0, len(todo), batch_size):
        chunk = todo[start : start + batch_size]
        vectors = provider.embed_batch([d["text"] for d in chunk], "passage")
        for doc, vector in zip(chunk, vectors, strict=True):
            collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "embedding": vector,
                        "embedding_model": provider.model_id,
                        "updated_at": datetime.now(UTC),
                    }
                },
            )
        done += len(chunk)
        if progress is not None:
            progress(done, len(todo))
    return ReindexResult(total=len(docs), reindexed=done, skipped=len(docs) - len(todo))
