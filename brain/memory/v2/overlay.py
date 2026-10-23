"""A ``DocStore`` that reads the real data and writes only to a throwaway overlay.

``--dry-run`` of the nightly pass runs the real phases, with their real logic, over this store:
every write lands in the overlay, so nothing can reach the database by accident, and the overlay
says afterwards what *would* have been written.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from brain.memory.v2.store import DocStore, Query, Sort, _matches, _sorted
from brain.memory.vector import (
    Memory,
    SearchHit,
    VectorStoreError,
    check_update_fields,
    check_vector,
    rank,
)


class OverlayDocStore:
    def __init__(self, base: DocStore) -> None:
        self.base = base
        # collection -> id -> document (None marks a deletion)
        self._over: dict[str, dict[str, dict[str, Any] | None]] = {}

    def _layer(self, collection: str) -> dict[str, dict[str, Any] | None]:
        return self._over.setdefault(collection, {})

    def _current(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        layer = self._layer(collection)
        if doc_id in layer:
            doc = layer[doc_id]
            return None if doc is None else copy.deepcopy(doc)
        return self.base.get(collection, doc_id)

    def _merged(self, collection: str) -> list[dict[str, Any]]:
        merged = {str(d["_id"]): d for d in self.base.find(collection)}
        for doc_id, doc in self._layer(collection).items():
            if doc is None:
                merged.pop(doc_id, None)
            else:
                merged[doc_id] = copy.deepcopy(doc)
        return list(merged.values())

    def put(self, collection: str, doc: Mapping[str, Any]) -> None:
        self._layer(collection)[str(doc["_id"])] = copy.deepcopy(dict(doc))

    def put_if_version(
        self, collection: str, doc: Mapping[str, Any], expected_version: int | None
    ) -> bool:
        current = self._current(collection, str(doc["_id"]))
        if expected_version is None:
            if current is not None:
                return False
        elif current is None or current.get("version") != expected_version:
            return False
        self.put(collection, doc)
        return True

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self._current(collection, doc_id)

    def find(
        self, collection: str, query: Query | None = None, sort: Sort | None = None, limit: int = 0
    ) -> list[dict[str, Any]]:
        found = [d for d in self._merged(collection) if _matches(d, query or {})]
        found = _sorted(found, sort)
        return found[:limit] if limit > 0 else found

    def update(self, collection: str, doc_id: str, changes: Mapping[str, Any]) -> bool:
        current = self._current(collection, doc_id)
        if current is None:
            return False
        current.update(copy.deepcopy(dict(changes)))
        self._layer(collection)[doc_id] = current
        return True

    def delete(self, collection: str, doc_id: str) -> bool:
        if self._current(collection, doc_id) is None:
            return False
        self._layer(collection)[doc_id] = None
        return True

    def delete_many(self, collection: str, query: Query) -> int:
        doomed = [str(d["_id"]) for d in self.find(collection, query)]
        for doc_id in doomed:
            self._layer(collection)[doc_id] = None
        return len(doomed)

    def count(self, collection: str, query: Query | None = None) -> int:
        return len(self.find(collection, query))

    def changes(self) -> dict[str, dict[str, list[str]]]:
        """Against the base, per collection: the ids added, updated and deleted."""
        report: dict[str, dict[str, list[str]]] = {}
        for collection, layer in self._over.items():
            added: list[str] = []
            updated: list[str] = []
            deleted: list[str] = []
            for doc_id, doc in layer.items():
                existed = self.base.get(collection, doc_id) is not None
                if doc is None:
                    if existed:
                        deleted.append(doc_id)
                elif existed:
                    if doc != self.base.get(collection, doc_id):
                        updated.append(doc_id)
                else:
                    added.append(doc_id)
            if added or updated or deleted:
                report[collection] = {
                    "added": sorted(added),
                    "updated": sorted(updated),
                    "deleted": sorted(deleted),
                }
        return report


class SnapshotVectorStore:
    """A ``VectorStore`` over a *copy* of the memories: writes never reach the real one.

    The v1 extraction pipeline writes facts through a ``VectorStore``. In a ``--dry-run`` the
    observation phase hands it this store instead, seeded from the real memories, so the pipeline
    runs with its real logic (search, judge, supersede) and the real database stays untouched.
    """

    def __init__(self, memories: Iterable[Memory], embedding_model: str) -> None:
        self.embedding_model = embedding_model
        self._items: dict[str, Memory] = {m.id: _copy(m) for m in memories}
        self._original = set(self._items)

    @classmethod
    def of(cls, store: Any, embedding_model: str) -> SnapshotVectorStore:
        return cls(store.list_memories(None, True), embedding_model)

    def added(self) -> list[Memory]:
        """The memories created since the snapshot, oldest first."""
        fresh = [m for i, m in self._items.items() if i not in self._original]
        return sorted(fresh, key=lambda m: (m.created_at, m.id))

    def add(self, memory: Memory) -> str:
        memory.validate()
        self._items[memory.id] = _copy(memory)
        return memory.id

    def get(self, memory_id: str) -> Memory | None:
        item = self._items.get(memory_id)
        return None if item is None else _copy(item)

    def count(self) -> int:
        return sum(1 for m in self._items.values() if m.status == "active")

    def list_memories(
        self, memory_type: str | None = None, include_superseded: bool = False
    ) -> list[Memory]:
        found = [
            _copy(m)
            for m in self._items.values()
            if (include_superseded or m.status == "active")
            and (memory_type is None or m.type == memory_type)
        ]
        return sorted(found, key=lambda m: (m.created_at, m.id), reverse=True)

    def search(
        self,
        vector: Sequence[float],
        top_k: int | None = None,
        threshold: float | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> list[SearchHit]:
        check_vector(vector)
        if top_k is not None and top_k < 1:
            raise VectorStoreError("top_k precisa ser pelo menos 1.")
        candidates = [
            (m, m.embedding)
            for m in self._items.values()
            if m.status == "active"
            and m.embedding_model == self.embedding_model
            and all(_attr(m, k) == v for k, v in (filters or {}).items())
        ]
        return rank(vector, candidates, top_k or 5, -1.0 if threshold is None else threshold)

    def update(self, memory_id: str, **fields: Any) -> bool:
        check_update_fields(fields)
        item = self._items.get(memory_id)
        if item is None:
            return False
        for key, value in fields.items():
            if key == "source":
                continue
            setattr(item, key, list(value) if key == "embedding" else value)
        item.updated_at = datetime.now(UTC)
        return True

    def delete(self, memory_id: str) -> bool:
        return self._items.pop(memory_id, None) is not None


def _copy(memory: Memory) -> Memory:
    return Memory(
        text=memory.text,
        type=memory.type,
        embedding=list(memory.embedding),
        embedding_model=memory.embedding_model,
        id=memory.id,
        status=memory.status,
        superseded_by=memory.superseded_by,
        source_kind=memory.source_kind,
        session_id=memory.session_id,
        turn=memory.turn,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def _attr(memory: Memory, path: str) -> Any:
    return {
        "type": memory.type,
        "source.kind": memory.source_kind,
        "source.session_id": memory.session_id,
    }.get(path)
