"""Daily delivery and voice introductions; channel receipts are committed after delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from zoneinfo import ZoneInfo

from brain.config import settings
from brain.eventlog import event_log
from brain.reminders.store import ReminderStore, now

Send = Callable[[int, str], Awaitable[None]]


def line(doc: dict[str, Any]) -> str:
    due = doc["due_at"].astimezone(ZoneInfo(settings.agent.timezone))
    return f"{doc['title']}, dia {due:%d/%m}, às {due:%H:%M}."


def greeting() -> str:
    hour = now().astimezone(ZoneInfo(settings.agent.timezone)).hour
    return "Bom dia!" if hour < 12 else "Boa tarde!" if hour < 18 else "Boa noite!"


async def telegram_send(chat_id: int, text: str) -> None:
    from telegram import Bot

    token = settings.telegram_bot_token
    if token is None:
        raise ValueError("Configure JUNIPER_TELEGRAM_BOT_TOKEN no processo brain.")
    async with Bot(token.get_secret_value()) as bot:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=None,
            connect_timeout=10,
            read_timeout=20,
            write_timeout=20,
        )


def recipients() -> list[int]:
    """Who gets the Telegram notice: the same people allowed to talk to the bot (private chats)."""
    return list(settings.telegram.allowed_user_ids)


async def send_due(store: ReminderStore, send: Send = telegram_send) -> int:
    """One pass: send every reminder whose notice time has come. Returns how many were completed.

    A reminder goes to each allowed user. Recipients that already got it are skipped on a retry,
    so a partial failure does not repeat the message to them.
    """
    if not settings.reminders.enabled:
        return 0
    chats = recipients()
    sent = 0
    for doc in await asyncio.to_thread(store.eligible, "telegram"):
        if not chats:
            event_log.log("reminder", action="telegram_unconfigured", reminder_id=doc["_id"])
            continue
        token = await asyncio.to_thread(store.claim, doc, "telegram")
        if token is None:
            continue
        done: list[int] = []
        failed = False
        try:
            for chat in chats:
                if chat in doc.get("telegram_sent_to", []):
                    continue
                try:
                    await send(chat, "Lembrete: " + line(doc))
                    done.append(chat)
                except Exception as exc:
                    failed = True
                    # No message, title, chat id or bot token in the technical log.
                    event_log.log(
                        "reminder",
                        action="telegram_failed",
                        reminder_id=doc["_id"],
                        reason=type(exc).__name__,
                    )
        finally:
            await asyncio.to_thread(
                store.finish, doc, "telegram", token, success=not failed, delivered=done
            )
        if not failed:
            sent += 1
            event_log.log("reminder", action="telegram_sent", reminder_id=doc["_id"])
    return sent


_state: dict[str, Any] = {"failing": False, "pruned_on": None}


async def reminder_job() -> None:
    """The every-minute tick: clean old history once a day, then send what is due."""
    from brain.tools.reminders import store

    try:
        reminders = store()
        today = now().astimezone(ZoneInfo(settings.agent.timezone)).date()
        if _state["pruned_on"] != today:
            await asyncio.to_thread(reminders.prune)
            _state["pruned_on"] = today
        await send_due(reminders)
        if _state["failing"]:
            event_log.log("reminder", action="job_recovered")
        _state["failing"] = False
    except Exception as exc:
        if not _state["failing"]:  # one line per outage, not one per minute
            event_log.log("reminder", action="job_failed", reason=type(exc).__name__)
        _state["failing"] = True


async def voice_briefing(
    store: ReminderStore, owner: str
) -> tuple[str, Callable[[bool], Awaitable[None]]]:
    """Claim reminders across devices. The voice session acknowledges after sending speech."""
    claimed = []
    for doc in await asyncio.to_thread(store.eligible, "voice", owner):
        token = await asyncio.to_thread(store.claim, doc, "voice")
        if token:
            claimed.append((doc, token))
    text = (
        greeting() + " Seus lembretes: " + " ".join(line(d) for d, _ in claimed) if claimed else ""
    )

    async def finish(success: bool) -> None:
        for doc, token in claimed:
            await asyncio.to_thread(store.finish, doc, "voice", token, success=success)
            if success:
                event_log.log("reminder", action="voice_reminded", reminder_id=doc["_id"])

    return text, finish
