"""Authenticated reminder CRUD; identity comes from the dashboard session, never query args."""

from __future__ import annotations

import asyncio
from typing import Any

from brain.auth import Principal
from brain.dashboard.api import Query, first
from brain.dashboard.serialize import paged
from brain.dashboard.sources import Sources
from brain.eventlog import event_log
from brain.memory.errors import MemoryUnavailableError
from brain.reminders.store import ReminderError

Result = tuple[int, dict[str, Any], None]


# The dashboard's writes ride in the query string (the HTTP server reads no body), so the notes
# are capped well below the store's 2000 characters to keep the request line within its limit.
MAX_NOTES = 500


class ReminderApi:
    serves_get = True

    def __init__(self, sources: Sources) -> None:
        self.sources = sources

    @staticmethod
    def handles(path: str) -> bool:
        return path == "/api/reminders" or path.startswith("/api/reminders/")

    async def handle(self, method: str, path: str, query: Query, who: Principal) -> Result:
        if not who.is_owner:
            return 403, {"error": "só o dono administra lembretes no dashboard"}, None
        if self.sources.reminders is None:
            return 503, {"error": "Os lembretes precisam do MongoDB."}, None
        try:
            return await asyncio.to_thread(self._dispatch, method, path, query, who)
        except ReminderError as exc:
            return 400, {"error": str(exc)}, None
        except MemoryUnavailableError as exc:
            return 503, {"error": str(exc)}, None

    def _dispatch(self, method: str, path: str, query: Query, who: Principal) -> Result:
        store = self.sources.reminders
        assert store is not None
        parts = path.removeprefix("/api/reminders").strip("/").split("/")
        fields = {
            k: (first(query, k) or "").strip()
            for k in ("title", "due_at", "notify_on", "notes")
            if k in query
        }
        if len(fields.get("notes", "")) > MAX_NOTES:
            raise ReminderError(
                f"Pelo dashboard as observações vão até {MAX_NOTES} caracteres; "
                "para um texto maior use o chat."
            )
        if method == "GET":
            if parts == [""]:
                docs = store.list(who.user_id)
                state = first(query, "status")
                text = (first(query, "q") or "").casefold()
                docs = [
                    d
                    for d in docs
                    if (not state or state in d["statuses"])
                    and (not text or text in (d["title"] + " " + d["notes"]).casefold())
                ]
                return 200, paged(docs, query), None
            if len(parts) == 1:
                return 200, store.get(who.user_id, parts[0]), None
        elif method == "POST":
            if parts == [""]:
                doc = store.create(
                    who.user_id,
                    title=fields.get("title"),
                    due_at=fields.get("due_at"),
                    notify_on=fields.get("notify_on"),
                    notes=fields.get("notes", ""),
                )
                action = "created"
            elif len(parts) == 2 and parts[1] == "update":
                doc = store.update(who.user_id, parts[0], **fields)
                action = "updated"
            elif len(parts) == 2 and parts[1] == "delete":
                if first(query, "confirm") != parts[0]:
                    raise ReminderError("Confirme a exclusão com o id completo do lembrete.")
                store.delete(who.user_id, parts[0])
                doc, action = {"id": parts[0]}, "deleted"
            else:
                return 404, {"error": "não existe"}, None
            event_log.log("reminder", action=action, reminder_id=doc["id"], actor=who.user_id)
            return 200, {"ok": True, "reminder": doc}, None
        return 405, {"error": "método não permitido"}, None
