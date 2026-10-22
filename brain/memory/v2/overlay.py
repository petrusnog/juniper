"""A ``DocStore`` that reads the real data and writes only to a throwaway overlay.

``--dry-run`` of the nightly pass runs the real phases, with their real logic, over this store:
every write lands in the overlay, so nothing can reach the database by accident, and the overlay
says afterwards what *would* have been written.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from brain.memory.v2.store import DocStore, Query, Sort, _matches, _sorted


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
