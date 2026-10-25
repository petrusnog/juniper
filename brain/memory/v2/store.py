"""Where the V2 layers live (ADR-017, section 12): documents in MongoDB, or in memory.

``DocStore`` is a small document-store interface (put, get, find, update, delete, optimistic
versioned put). ``MongoDocStore`` implements it on the memory database and ``InMemoryDocStore``
implements it with plain dicts, for ``--dry-run`` and for tests; both pass the same contract
tests. On top of it, ``Repo`` gives typed access to one collection and ``V2Data`` bundles the
collections of every layer.

The MongoDB of the compose is a single node without multi-document transactions, so nothing here
relies on one: callers are idempotent and ``put_if_version`` gives the one place that needs
concurrency control (the personality) an optimistic check.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from brain.memory.v2.models import (
    AFFECTS,
    BELIEFS,
    CALIBRATION,
    CONCERNS,
    DREAM_RUNS,
    DREAM_WATERMARKS,
    INTERESTS,
    JOURNAL,
    PERSONA_HISTORY,
    PERSONA_ID,
    PERSONA_STATE,
    STUDY_NOTES,
    TRANSCRIPT_TURNS,
    Affect,
    Belief,
    Calibration,
    Concern,
    DreamRun,
    Interest,
    JournalEntry,
    PersonaState,
    PersonaVersion,
    StudyNote,
    TranscriptTurn,
    V2DataError,
    Watermark,
    utcnow,
)

Query = Mapping[str, Any]
Sort = Sequence[tuple[str, int]]  # (field, 1 ascending / -1 descending)

T = TypeVar("T")

# Indexes per collection (only MongoDB needs them).
INDEXES: dict[str, list[list[tuple[str, int]]]] = {
    BELIEFS: [[("status", 1), ("about", 1)], [("last_reviewed_at", -1)], [("topic", 1)]],
    INTERESTS: [[("topic", 1)], [("intensity", -1)]],
    STUDY_NOTES: [[("interest_id", 1), ("created_at", 1)]],
    AFFECTS: [[("occurred_at", -1)], [("persistence", 1), ("resolved_at", 1)]],
    PERSONA_HISTORY: [[("version", -1)]],
    JOURNAL: [[("day", -1)], [("at", -1)]],
    DREAM_RUNS: [[("started_at", -1)]],
    TRANSCRIPT_TURNS: [[("session_id", 1), ("index", 1)], [("at", -1)]],
}


@runtime_checkable
class DocStore(Protocol):
    """Documents grouped in collections; ``_id`` is the key of a document."""

    def put(self, collection: str, doc: Mapping[str, Any]) -> None:
        """Insert or replace the document with this ``_id``."""
        ...

    def put_if_version(
        self, collection: str, doc: Mapping[str, Any], expected_version: int | None
    ) -> bool:
        """Replace only if the stored ``version`` is ``expected_version`` (``None`` = must not
        exist yet). False when somebody else wrote first."""
        ...

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None: ...

    def find(
        self, collection: str, query: Query | None = None, sort: Sort | None = None, limit: int = 0
    ) -> list[dict[str, Any]]:
        """Documents matching ``query`` (equality plus ``$in $ne $lt $lte $gt $gte``)."""
        ...

    def update(self, collection: str, doc_id: str, changes: Mapping[str, Any]) -> bool:
        """``$set`` the fields. False if the document does not exist."""
        ...

    def delete(self, collection: str, doc_id: str) -> bool: ...

    def delete_many(self, collection: str, query: Query) -> int: ...

    def count(self, collection: str, query: Query | None = None) -> int: ...


# --- in memory --------------------------------------------------------------------------------


def _get_path(doc: Mapping[str, Any], path: str) -> Any:
    value: Any = doc
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _matches(doc: Mapping[str, Any], query: Query) -> bool:
    for path, condition in query.items():
        value = _get_path(doc, path)
        if isinstance(condition, Mapping) and any(k.startswith("$") for k in condition):
            for op, operand in condition.items():
                if op == "$in":
                    ok = value in operand
                elif op == "$ne":
                    ok = value != operand
                elif value is None:
                    ok = False  # ordering operators never match a missing value
                elif op == "$lt":
                    ok = value < operand
                elif op == "$lte":
                    ok = value <= operand
                elif op == "$gt":
                    ok = value > operand
                elif op == "$gte":
                    ok = value >= operand
                else:
                    raise V2DataError(f"operador não suportado: {op}")
                if not ok:
                    return False
        elif value != condition:
            return False
    return True


def _sorted(docs: list[dict[str, Any]], sort: Sort | None) -> list[dict[str, Any]]:
    for field_name, direction in reversed(list(sort or [])):
        docs.sort(
            key=lambda d, f=field_name: (_get_path(d, f) is None, _get_path(d, f)),  # type: ignore[misc]
            reverse=direction < 0,
        )
    return docs


class InMemoryDocStore:
    """``DocStore`` over dicts. Thread-safe; documents are copied in and out."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def _col(self, collection: str) -> dict[str, dict[str, Any]]:
        return self._data.setdefault(collection, {})

    def put(self, collection: str, doc: Mapping[str, Any]) -> None:
        with self._lock:
            self._col(collection)[str(doc["_id"])] = copy.deepcopy(dict(doc))

    def put_if_version(
        self, collection: str, doc: Mapping[str, Any], expected_version: int | None
    ) -> bool:
        with self._lock:
            current = self._col(collection).get(str(doc["_id"]))
            if expected_version is None:
                if current is not None:
                    return False
            elif current is None or current.get("version") != expected_version:
                return False
            self._col(collection)[str(doc["_id"])] = copy.deepcopy(dict(doc))
            return True

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        with self._lock:
            doc = self._col(collection).get(doc_id)
            return None if doc is None else copy.deepcopy(doc)

    def find(
        self, collection: str, query: Query | None = None, sort: Sort | None = None, limit: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            found = [
                copy.deepcopy(d) for d in self._col(collection).values() if _matches(d, query or {})
            ]
        found = _sorted(found, sort)
        return found[:limit] if limit > 0 else found

    def update(self, collection: str, doc_id: str, changes: Mapping[str, Any]) -> bool:
        with self._lock:
            doc = self._col(collection).get(doc_id)
            if doc is None:
                return False
            doc.update(copy.deepcopy(dict(changes)))
            return True

    def delete(self, collection: str, doc_id: str) -> bool:
        with self._lock:
            return self._col(collection).pop(doc_id, None) is not None

    def delete_many(self, collection: str, query: Query) -> int:
        with self._lock:
            doomed = [k for k, d in self._col(collection).items() if _matches(d, query)]
            for key in doomed:
                del self._col(collection)[key]
            return len(doomed)

    def count(self, collection: str, query: Query | None = None) -> int:
        with self._lock:
            return sum(1 for d in self._col(collection).values() if _matches(d, query or {}))


# --- MongoDB ----------------------------------------------------------------------------------


class MongoDocStore:
    """``DocStore`` on the memory database. Driver failures become ``MemoryUnavailableError``."""

    def __init__(self, connection: Any | None = None) -> None:
        from brain.memory.mongo import MongoConnection  # the only place that needs the driver

        self.connection = connection if connection is not None else MongoConnection()
        self._indexed: set[str] = set()

    def _col(self, collection: str) -> Any:
        col = self.connection.collection(collection)
        if collection not in self._indexed:
            for spec in INDEXES.get(collection, []):
                col.create_index(spec)
            self._indexed.add(collection)
        return col

    def put(self, collection: str, doc: Mapping[str, Any]) -> None:
        with self.connection.guard():
            self._col(collection).replace_one({"_id": doc["_id"]}, dict(doc), upsert=True)

    def put_if_version(
        self, collection: str, doc: Mapping[str, Any], expected_version: int | None
    ) -> bool:
        from pymongo.errors import DuplicateKeyError

        with self.connection.guard():
            col = self._col(collection)
            if expected_version is None:
                try:
                    col.insert_one(dict(doc))
                except DuplicateKeyError:
                    return False
                return True
            result = col.replace_one({"_id": doc["_id"], "version": expected_version}, dict(doc))
            return bool(result.matched_count)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        with self.connection.guard():
            doc = self._col(collection).find_one({"_id": doc_id})
        return None if doc is None else dict(doc)

    def find(
        self, collection: str, query: Query | None = None, sort: Sort | None = None, limit: int = 0
    ) -> list[dict[str, Any]]:
        with self.connection.guard():
            cursor = self._col(collection).find(dict(query or {}))
            if sort:
                cursor = cursor.sort(list(sort))
            if limit > 0:
                cursor = cursor.limit(limit)
            return [dict(d) for d in cursor]

    def update(self, collection: str, doc_id: str, changes: Mapping[str, Any]) -> bool:
        with self.connection.guard():
            result = self._col(collection).update_one({"_id": doc_id}, {"$set": dict(changes)})
        return bool(result.matched_count)

    def delete(self, collection: str, doc_id: str) -> bool:
        with self.connection.guard():
            result = self._col(collection).delete_one({"_id": doc_id})
        return bool(result.deleted_count)

    def delete_many(self, collection: str, query: Query) -> int:
        with self.connection.guard():
            result = self._col(collection).delete_many(dict(query))
        return int(result.deleted_count)

    def count(self, collection: str, query: Query | None = None) -> int:
        with self.connection.guard():
            return int(self._col(collection).count_documents(dict(query or {})))

    def close(self) -> None:
        self.connection.close()


# --- typed repositories -----------------------------------------------------------------------


class Repo(Generic[T]):
    """Typed access to one collection. Damaged documents are skipped when listing."""

    def __init__(
        self,
        docs: DocStore,
        collection: str,
        to_doc: Callable[[T], dict[str, Any]],
        from_doc: Callable[[Mapping[str, Any]], T],
    ) -> None:
        self.docs = docs
        self.collection = collection
        self._to_doc = to_doc
        self._from_doc = from_doc

    def save(self, item: T) -> None:
        """Insert or replace (the document is validated by ``to_doc``)."""
        self.docs.put(self.collection, self._to_doc(item))

    def get(self, item_id: str) -> T | None:
        doc = self.docs.get(self.collection, item_id)
        if doc is None:
            return None
        try:
            return self._from_doc(doc)
        except V2DataError:
            return None

    def list(self, query: Query | None = None, sort: Sort | None = None, limit: int = 0) -> list[T]:
        items: list[T] = []
        for doc in self.docs.find(self.collection, query, sort, limit):
            try:
                items.append(self._from_doc(doc))
            except V2DataError:
                continue  # a damaged document must not hide the good ones
        return items

    def update(self, item_id: str, **changes: Any) -> bool:
        if "updated_at" not in changes:
            changes["updated_at"] = utcnow()
        return self.docs.update(self.collection, item_id, changes)

    def delete(self, item_id: str) -> bool:
        return self.docs.delete(self.collection, item_id)

    def delete_where(self, query: Query) -> int:
        return self.docs.delete_many(self.collection, query)

    def count(self, query: Query | None = None) -> int:
        return self.docs.count(self.collection, query)


class PersonaRepo:
    """The personality: one current document, optimistic versioning, and its history."""

    def __init__(self, docs: DocStore) -> None:
        self.docs = docs
        self.history: Repo[PersonaVersion] = Repo(
            docs, PERSONA_HISTORY, PersonaVersion.to_doc, PersonaVersion.from_doc
        )

    def current(self) -> PersonaState | None:
        doc = self.docs.get(PERSONA_STATE, PERSONA_ID)
        if doc is None:
            return None
        try:
            return PersonaState.from_doc(doc)
        except V2DataError:
            return None

    def save(self, state: PersonaState, expected_version: int | None, reason: str = "") -> bool:
        """Write ``state`` if the stored version is still ``expected_version`` (``None``: first
        write). The version being replaced is kept in the history. False on a conflict."""
        previous = self.current() if expected_version is not None else None
        ok = self.docs.put_if_version(PERSONA_STATE, state.to_doc(), expected_version)
        if ok and previous is not None:
            self.history.save(PersonaVersion(previous, reason))
        return ok


@dataclass
class V2Data:
    """Every V2 collection, typed. Build it with ``open_v2_data`` or over any ``DocStore``."""

    docs: DocStore
    beliefs: Repo[Belief]
    interests: Repo[Interest]
    notes: Repo[StudyNote]
    affects: Repo[Affect]
    concerns: Repo[Concern]
    journal: Repo[JournalEntry]
    runs: Repo[DreamRun]
    watermarks: Repo[Watermark]
    transcripts: Repo[TranscriptTurn]
    calibration: Repo[Calibration]
    persona: PersonaRepo

    @classmethod
    def over(cls, docs: DocStore) -> V2Data:
        return cls(
            docs=docs,
            beliefs=Repo(docs, BELIEFS, Belief.to_doc, Belief.from_doc),
            interests=Repo(docs, INTERESTS, Interest.to_doc, Interest.from_doc),
            notes=Repo(docs, STUDY_NOTES, StudyNote.to_doc, StudyNote.from_doc),
            affects=Repo(docs, AFFECTS, Affect.to_doc, Affect.from_doc),
            concerns=Repo(docs, CONCERNS, Concern.to_doc, Concern.from_doc),
            journal=Repo(docs, JOURNAL, JournalEntry.to_doc, JournalEntry.from_doc),
            runs=Repo(docs, DREAM_RUNS, DreamRun.to_doc, DreamRun.from_doc),
            watermarks=Repo(docs, DREAM_WATERMARKS, Watermark.to_doc, Watermark.from_doc),
            transcripts=Repo(
                docs, TRANSCRIPT_TURNS, TranscriptTurn.to_doc, TranscriptTurn.from_doc
            ),
            calibration=Repo(docs, CALIBRATION, Calibration.to_doc, Calibration.from_doc),
            persona=PersonaRepo(docs),
        )

    def close(self) -> None:
        close = getattr(self.docs, "close", None)
        if callable(close):
            close()
