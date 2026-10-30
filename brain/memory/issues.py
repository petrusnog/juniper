"""The issue log (ADR-028): occurrences of any kind of bug, registered by the user or by voice.

The leak reports (``/vazou``) and the leak audit keep their own collections; an *issue* is the
general case: a title, what happened, a severity and a state the owner moves along in the
dashboard. The tool ``report_issue`` only creates; changing a state or deleting is the owner's.
Free text is masked before it is stored: a secret is never kept (ADR-011).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from pymongo import DESCENDING

from brain.memory.mongo import MongoConnection
from brain.memory.secretfilter import mask_secrets

ISSUES_COLLECTION: Final = "issues"
TYPES: Final = ("bug", "vazamento", "comportamento", "ideia", "outro")
SEVERITIES: Final = ("baixa", "media", "alta")
STATES: Final = ("aberta", "investigando", "resolvida", "descartada")
ORIGINS: Final = ("fala", "dashboard")
MAX_TITLE: Final = 120
MAX_TEXT: Final = 4000
MAX_NOTE: Final = 1000
MAX_HISTORY: Final = 50
_HEX = re.compile(r"[0-9a-f]{4,32}")


class IssueError(Exception):
    """A request the store will not take; ``str(exc)`` is fit to show the user."""


def _clean(text: object, limit: int) -> str:
    return mask_secrets(" ".join(str(text or "").split()))[:limit]


def _now() -> datetime:
    return datetime.now(UTC)


def new_issue(
    *,
    title: str,
    description: str = "",
    type: str = "bug",  # noqa: A002  (the stored field is called ``type``)
    severity: str = "media",
    origin: str = "dashboard",
    session: str | None = None,
    channel: str | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    """A validated document. Raises ``IssueError`` for a bad value."""
    title = _clean(title, MAX_TITLE)
    if not title:
        raise IssueError("A ocorrência precisa de um título.")
    if type not in TYPES:
        raise IssueError(f"Tipo inválido: use {', '.join(TYPES)}.")
    if severity not in SEVERITIES:
        raise IssueError(f"Gravidade inválida: use {', '.join(SEVERITIES)}.")
    if origin not in ORIGINS:
        raise IssueError(f"Origem inválida: use {', '.join(ORIGINS)}.")
    now = _now()
    return {
        "_id": uuid.uuid4().hex,
        "at": now,
        "updated_at": now,
        "type": type,
        "title": title,
        "description": _clean(description, MAX_TEXT),
        "severity": severity,
        "state": "aberta",
        "origin": origin,
        "session": session,
        "channel": channel,
        "owner_id": owner_id,
        "history": [{"at": now, "by": owner_id or "?", "change": f"criada ({origin})"}],
    }


class IssueStore:
    """Borrows the connection (from the session store): closing it is the owner's job."""

    def __init__(self, connection: MongoConnection | None = None) -> None:
        self.connection = connection if connection is not None else MongoConnection()
        self._indexed = False

    def _collection(self) -> Any:
        collection = self.connection.collection(ISSUES_COLLECTION)
        if not self._indexed:
            collection.create_index([("at", DESCENDING)])
            collection.create_index([("state", 1), ("at", DESCENDING)])
            self._indexed = True
        return collection

    def add(self, issue: dict[str, Any]) -> str:
        with self.connection.guard():
            self._collection().insert_one(issue)
        return str(issue["_id"])

    def recent(
        self,
        *,
        limit: int = 100,
        state: str | None = None,
        type: str | None = None,  # noqa: A002
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if state:
            query["state"] = state
        if type:
            query["type"] = type
        with self.connection.guard():
            return list(self._collection().find(query).sort("at", DESCENDING).limit(limit))

    def get(self, issue_id: str) -> dict[str, Any] | None:
        """One issue by its id or by a unique prefix of it."""
        if not _HEX.fullmatch(issue_id):
            return None
        with self.connection.guard():
            collection = self._collection()
            found: dict[str, Any] | None = collection.find_one({"_id": issue_id})
            if found is not None:
                return found
            matches = list(collection.find({"_id": {"$regex": f"^{issue_id}"}}).limit(2))
        return matches[0] if len(matches) == 1 else None

    def _require(self, issue_id: str) -> dict[str, Any]:
        found = self.get(issue_id)
        if found is None:
            raise IssueError("Não achei essa ocorrência.")
        return found

    def _change(self, issue_id: str, fields: dict[str, Any], by: str, what: str) -> dict[str, Any]:
        found = self._require(issue_id)
        now = _now()
        entry = {"at": now, "by": by or "?", "change": what}
        history = [*found.get("history", []), entry][-MAX_HISTORY:]
        with self.connection.guard():
            self._collection().update_one(
                {"_id": found["_id"]},
                {"$set": {**fields, "updated_at": now, "history": history}},
            )
        return self._require(str(found["_id"]))

    def set_state(self, issue_id: str, state: str, by: str) -> dict[str, Any]:
        if state not in STATES:
            raise IssueError(f"Estado inválido: use {', '.join(STATES)}.")
        return self._change(issue_id, {"state": state}, by, f"estado: {state}")

    def set_severity(self, issue_id: str, severity: str, by: str) -> dict[str, Any]:
        if severity not in SEVERITIES:
            raise IssueError(f"Gravidade inválida: use {', '.join(SEVERITIES)}.")
        return self._change(issue_id, {"severity": severity}, by, f"gravidade: {severity}")

    def add_note(self, issue_id: str, text: str, by: str) -> dict[str, Any]:
        note = _clean(text, MAX_NOTE)
        if not note:
            raise IssueError("A nota está vazia.")
        return self._change(issue_id, {}, by, f"nota: {note}")

    def delete(self, issue_id: str) -> bool:
        found = self.get(issue_id)
        if found is None:
            return False
        with self.connection.guard():
            return bool(self._collection().delete_one({"_id": found["_id"]}).deleted_count)
