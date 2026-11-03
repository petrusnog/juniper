"""MongoDB reminders: owner-scoped CRUD, independent channel receipts and atomic leases."""

from __future__ import annotations

import builtins
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pymongo import ReturnDocument

from brain.config import settings
from brain.core import clock
from brain.memory.mongo import MongoConnection
from brain.memory.secretfilter import mask_secrets


class ReminderError(ValueError):
    """A safe, user-facing validation error."""


def now() -> datetime:
    return clock.now().astimezone(UTC)


def parse_due(value: object, *, require_future: bool = True) -> datetime:
    try:
        text = str(value or "")
        parsed = (
            datetime.combine(date.fromisoformat(text), time(23, 59, 59))
            if len(text) == 10
            else datetime.fromisoformat(text)
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(settings.agent.timezone))
        parsed = parsed.astimezone(UTC)
    except (ValueError, TypeError):
        raise ReminderError("Informe due_at como AAAA-MM-DD ou data e hora ISO 8601.") from None
    if require_future and parsed <= now():
        raise ReminderError("O prazo do lembrete precisa estar no futuro.")
    return parsed


def fields(
    title: object,
    due_at: object,
    notify_on: object = None,
    notes: object = "",
    *,
    require_future: bool = True,
) -> dict[str, Any]:
    title = mask_secrets(str(title or "").strip())
    if not title or len(title) > 200:
        raise ReminderError("O título precisa ter entre 1 e 200 caracteres.")
    due = parse_due(due_at, require_future=require_future)
    last_day = due.astimezone(ZoneInfo(settings.agent.timezone)).date()
    try:
        notify = date.fromisoformat(str(notify_on)) if notify_on else last_day
    except ValueError:
        raise ReminderError("notify_on deve ser AAAA-MM-DD.") from None
    if notify > last_day:
        raise ReminderError("O dia do aviso não pode ser posterior ao prazo.")
    return {
        "title": title,
        "notes": mask_secrets(str(notes or ""))[:2000],
        "due_at": due,
        "notify_on": notify.isoformat(),
    }


def public(doc: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
    statuses = []
    if doc.get("telegram_sent_at"):
        statuses.append("Enviado pelo Telegram")
    if doc.get("voice_reminded_at"):
        statuses.append("Relembrado no chat de voz")
    if doc["due_at"] <= (at or now()):
        statuses.append("Vencido")
    return {k: v for k, v in doc.items() if not k.endswith("_lease")} | {
        "id": doc["_id"],
        "statuses": statuses or ["Pendente"],
    }


class ReminderStore:
    def __init__(self, connection: MongoConnection) -> None:
        self.connection = connection
        self._indexed = False

    def collection(self) -> Any:
        c = self.connection.collection("reminders")
        if not self._indexed:
            c.create_index([("owner_id", 1), ("due_at", 1)])
            c.create_index([("notify_on", 1), ("due_at", 1)])
            c.create_index(
                [("owner_id", 1), ("calendar_id", 1), ("event_id", 1)],
                unique=True,
                partialFilterExpression={"event_id": {"$type": "string"}},
            )
            self._indexed = True
        return c

    def create(
        self,
        owner: str,
        *,
        title: object,
        due_at: object,
        notify_on: object = None,
        notes: object = "",
        event_id: str | None = None,
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        if not owner:
            raise ReminderError("Não consegui identificar o dono do lembrete.")
        data = fields(title, due_at, notify_on, notes)
        stamp = now()
        doc = {
            "_id": "rem_" + uuid.uuid4().hex,
            "owner_id": owner,
            **data,
            "created_at": stamp,
            "updated_at": stamp,
            "revision": 1,
            "telegram_sent_at": None,
            "voice_reminded_at": None,
        }
        if event_id:
            doc.update(event_id=event_id, calendar_id=calendar_id)
        with self.connection.guard():
            if event_id:
                # Idempotent for retries of a successful Google Calendar creation.
                result = self.collection().find_one_and_update(
                    {"owner_id": owner, "calendar_id": calendar_id, "event_id": event_id},
                    {"$setOnInsert": doc},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
                return public(result)
            self.collection().insert_one(doc)
        return public(doc)

    def list(self, owner: str, *, include_expired: bool = True) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"owner_id": owner}
        if not include_expired:
            query["due_at"] = {"$gt": now()}
        with self.connection.guard():
            return [public(d) for d in self.collection().find(query).sort("due_at", 1)]

    def get(self, owner: str, reminder_id: str) -> dict[str, Any]:
        with self.connection.guard():
            doc = self.collection().find_one({"_id": reminder_id, "owner_id": owner})
        if doc is None:
            raise ReminderError("Lembrete não encontrado. Use list_reminders para obter o id.")
        return public(doc)

    def _idle(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "_id": doc["_id"],
            "owner_id": doc["owner_id"],
            "revision": doc["revision"],
            "$and": [
                {"$or": [{f"{c}_lease": {"$exists": False}}, {f"{c}_lease.until": {"$lte": now()}}]}
                for c in ("telegram", "voice")
            ],
        }

    def update(self, owner: str, reminder_id: str, **changes: Any) -> dict[str, Any]:
        doc = self.get(owner, reminder_id)
        allowed = {"title", "due_at", "notify_on", "notes"}
        if not changes or set(changes) - allowed:
            raise ReminderError("Altere title, due_at, notify_on ou notes.")
        values = {k: doc.get(k) for k in allowed} | changes
        if "due_at" in changes and "notify_on" not in changes:
            zone = ZoneInfo(settings.agent.timezone)
            advance = doc["due_at"].astimezone(zone).date() - date.fromisoformat(doc["notify_on"])
            values["notify_on"] = (
                parse_due(changes["due_at"]).astimezone(zone).date() - advance
            ).isoformat()
        updated = fields(**values, require_future=bool({"due_at", "notify_on"} & changes.keys()))
        if "due_at" in changes or "notify_on" in changes:
            updated.update(telegram_sent_at=None, voice_reminded_at=None)
        with self.connection.guard():
            result = self.collection().update_one(
                self._idle(doc), {"$set": {**updated, "updated_at": now()}, "$inc": {"revision": 1}}
            )
        if not result.modified_count:
            raise ReminderError("Lembrete em envio ou alterado em outra janela; tente novamente.")
        return self.get(owner, reminder_id)

    def delete(self, owner: str, reminder_id: str) -> None:
        doc = self.get(owner, reminder_id)
        with self.connection.guard():
            if not self.collection().delete_one(self._idle(doc)).deleted_count:
                raise ReminderError(
                    "Lembrete em envio ou alterado em outra janela; tente novamente."
                )

    def eligible(
        self, channel: str, owner: str | None = None, at: datetime | None = None
    ) -> builtins.list[dict[str, Any]]:
        stamp = at or now()
        day = stamp.astimezone(ZoneInfo(settings.agent.timezone)).date().isoformat()
        query: dict[str, Any] = {
            "notify_on": {"$lte": day},
            "due_at": {"$gt": stamp},
            f"{channel}_sent_at" if channel == "telegram" else "voice_reminded_at": None,
        }
        if owner is not None:
            query["owner_id"] = owner
        with self.connection.guard():
            return list(self.collection().find(query).sort("due_at", 1))

    def prune(self) -> int:
        """Keep expired reminders for the configured history window."""
        with self.connection.guard():
            return int(
                self.collection()
                .delete_many(
                    {"due_at": {"$lt": now() - timedelta(days=settings.reminders.retention_days)}}
                )
                .deleted_count
            )

    def claim(self, doc: dict[str, Any], channel: str) -> str | None:
        lease = f"{channel}_lease"
        receipt = "telegram_sent_at" if channel == "telegram" else "voice_reminded_at"
        token = uuid.uuid4().hex
        with self.connection.guard():
            result = self.collection().update_one(
                {
                    "_id": doc["_id"],
                    "revision": doc["revision"],
                    receipt: None,
                    "due_at": {"$gt": now()},
                    "$or": [{lease: {"$exists": False}}, {f"{lease}.until": {"$lte": now()}}],
                },
                {"$set": {lease: {"token": token, "until": now() + timedelta(minutes=10)}}},
            )
        return token if result.modified_count else None

    def finish(self, doc: dict[str, Any], channel: str, token: str, *, success: bool) -> None:
        receipt = "telegram_sent_at" if channel == "telegram" else "voice_reminded_at"
        change: dict[str, Any] = {"$unset": {f"{channel}_lease": ""}}
        if success:
            change["$set"] = {receipt: now(), "updated_at": now()}
        with self.connection.guard():
            self.collection().update_one(
                {"_id": doc["_id"], "revision": doc["revision"], f"{channel}_lease.token": token},
                change,
            )

    def linked(self, owner: str, calendar_id: str, event_id: str) -> dict[str, Any] | None:
        with self.connection.guard():
            found: dict[str, Any] | None = self.collection().find_one(
                {"owner_id": owner, "calendar_id": calendar_id, "event_id": event_id}
            )
            return found
