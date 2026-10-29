"""The leak audit records in MongoDB: ``leak_audit`` (T-165, expiring by TTL) and ``leak_reports``
(T-167, the user's own "isso vazou" reports).

The agent builds each audit record (``brain/core/leak_audit.py``) and hands it to
``LeakAuditStore.add`` (its ``leak_sink``). They hold the model's raw text and the user's message,
so they expire on their own after ``leak_audit.retention_days`` (a TTL index on ``at``). A report
(``brain/core/leak_report.py``) is something the person chose to keep, with the last ten exchanges
of the conversation: it does not expire; ``LeakReportStore.delete`` removes one.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import DESCENDING
from pymongo.errors import OperationFailure

from brain.config import settings
from brain.memory.mongo import MongoConnection

LEAK_AUDIT_COLLECTION = "leak_audit"
LEAK_REPORTS_COLLECTION = "leak_reports"
_INDEX_OPTIONS_CONFLICT = (85, 86)  # IndexOptionsConflict, IndexKeySpecsConflict
# Only the counters: the summary never needs the texts.
_SUMMARY_FIELDS = dict.fromkeys(
    ("rules", "leaked", "quarantined", "guard_triggered", "provider", "model", "channel"), 1
)
_HEX = re.compile(r"[0-9a-f]{4,32}")


class LeakAuditStore:
    """The records. The connection is borrowed (from the session store): closing it is the
    owner's job."""

    def __init__(
        self, connection: MongoConnection | None = None, retention_days: int | None = None
    ) -> None:
        self.connection = connection if connection is not None else MongoConnection()
        self._retention = (
            retention_days if retention_days is not None else settings.leak_audit.retention_days
        )
        self._indexed = False

    def _collection(self) -> Any:
        collection = self.connection.collection(LEAK_AUDIT_COLLECTION)
        if not self._indexed:
            self._ensure_ttl(collection)
            collection.create_index([("session", 1), ("at", DESCENDING)])
            self._indexed = True
        return collection

    def _ensure_ttl(self, collection: Any) -> None:
        """The TTL index on ``at``; a changed ``retention_days`` is applied in place."""
        seconds = self._retention * 86400
        try:
            collection.create_index("at", expireAfterSeconds=seconds)
        except OperationFailure as exc:
            if exc.code not in _INDEX_OPTIONS_CONFLICT:
                raise
            collection.database.command(
                "collMod",
                LEAK_AUDIT_COLLECTION,
                index={"keyPattern": {"at": 1}, "expireAfterSeconds": seconds},
            )

    def add(self, record: dict[str, Any]) -> None:
        """The agent's ``leak_sink``. Errors propagate: the agent logs them and moves on."""
        with self.connection.guard():
            self._collection().insert_one(record)

    def recent(
        self, *, days: int = 7, leaked_only: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        """The latest records, newest first."""
        query: dict[str, Any] = {"at": {"$gte": datetime.now(UTC) - timedelta(days=days)}}
        if leaked_only:
            query["leaked"] = True
        with self.connection.guard():
            cursor = self._collection().find(query).sort("at", DESCENDING).limit(limit)
            return list(cursor)

    def get(self, record_id: str) -> dict[str, Any] | None:
        """One record by its id or by a unique prefix of it."""
        if not _HEX.fullmatch(record_id):
            return None
        with self.connection.guard():
            collection = self._collection()
            found = collection.find_one({"_id": record_id})
            if found is not None:
                return found
            matches = list(collection.find({"_id": {"$regex": f"^{record_id}"}}).limit(2))
        return matches[0] if len(matches) == 1 else None

    def for_session(self, session_id: str, *, limit: int = 3, hours: int = 24) -> list[str]:
        """The ids of the latest records of one conversation (a report points at them)."""
        since = datetime.now(UTC) - timedelta(hours=hours)
        with self.connection.guard():
            cursor = (
                self._collection()
                .find({"session": session_id, "at": {"$gte": since}}, {"_id": 1})
                .sort("at", DESCENDING)
                .limit(limit)
            )
            return [str(doc["_id"]) for doc in cursor]

    def summary(self, *, days: int = 7) -> dict[str, Any]:
        """Counts by rule, model and channel over the period (``juniper vazamentos --resumo``)."""
        since = datetime.now(UTC) - timedelta(days=days)
        with self.connection.guard():
            records = list(
                self._collection().find({"at": {"$gte": since}}, _SUMMARY_FIELDS).limit(10_000)
            )
        rules: Counter[str] = Counter()
        for record in records:
            rules.update(record.get("rules") or [])
        return {
            "total": len(records),
            "leaked": sum(1 for r in records if r.get("leaked")),
            "quarantined": sum(1 for r in records if r.get("quarantined")),
            "blocked": sum(1 for r in records if r.get("guard_triggered")),
            "rules": rules.most_common(),
            "models": Counter(
                f"{r.get('provider')}/{r.get('model')}" for r in records
            ).most_common(),
            "channels": Counter(str(r.get("channel")) for r in records).most_common(),
        }


class LeakReportStore:
    """The user's reports (``/vazou``). Same borrowed connection as ``LeakAuditStore``."""

    def __init__(self, connection: MongoConnection | None = None) -> None:
        self.connection = connection if connection is not None else MongoConnection()
        self._indexed = False

    def _collection(self) -> Any:
        collection = self.connection.collection(LEAK_REPORTS_COLLECTION)
        if not self._indexed:
            collection.create_index([("at", DESCENDING)])
            collection.create_index([("session", 1), ("at", DESCENDING)])
            self._indexed = True
        return collection

    def add(self, report: dict[str, Any]) -> None:
        """Errors propagate as ``MemoryUnavailableError``: the handler tells the user."""
        with self.connection.guard():
            self._collection().insert_one(report)

    def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """The latest reports, newest first."""
        with self.connection.guard():
            return list(self._collection().find().sort("at", DESCENDING).limit(limit))

    def get(self, report_id: str) -> dict[str, Any] | None:
        """One report by its id or by a unique prefix of it."""
        if not _HEX.fullmatch(report_id):
            return None
        with self.connection.guard():
            collection = self._collection()
            found = collection.find_one({"_id": report_id})
            if found is not None:
                return found
            matches = list(collection.find({"_id": {"$regex": f"^{report_id}"}}).limit(2))
        return matches[0] if len(matches) == 1 else None

    def delete(self, report_id: str) -> bool:
        """Remove one report (the full id or a unique prefix). ``False`` if there is none."""
        found = self.get(report_id)
        if found is None:
            return False
        with self.connection.guard():
            return bool(self._collection().delete_one({"_id": found["_id"]}).deleted_count)
