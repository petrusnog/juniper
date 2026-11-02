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


async def send_morning(store: ReminderStore, send: Send = telegram_send) -> int:
    if not settings.reminders.enabled:
        return 0
    stamp = now().astimezone(ZoneInfo(settings.agent.timezone))
    if stamp.strftime("%H:%M") < settings.reminders.morning_at:
        return 0  # startup catch-up must never send before the configured morning
    sent = 0
    for doc in await asyncio.to_thread(store.eligible, "telegram"):
        chat = settings.reminders.telegram_chat_ids.get(doc["owner_id"])
        if not chat:
            event_log.log("reminder", action="telegram_unconfigured", reminder_id=doc["_id"])
            continue
        token = await asyncio.to_thread(store.claim, doc, "telegram")
        if token is None:
            continue
        success = False
        try:
            await send(chat, "Bom dia! Seu lembrete: " + line(doc))
            success = True
            sent += 1
        except Exception as exc:
            # No message, title, chat id or bot token in the technical log.
            event_log.log(
                "reminder",
                action="telegram_failed",
                reminder_id=doc["_id"],
                reason=type(exc).__name__,
            )
        finally:
            await asyncio.to_thread(store.finish, doc, "telegram", token, success=success)
        if success:
            event_log.log("reminder", action="telegram_sent", reminder_id=doc["_id"])
    return sent


async def morning_job() -> None:
    from brain.tools.reminders import store

    await send_morning(store())


async def voice_briefing(
    store: ReminderStore, owner: str
) -> tuple[str, Callable[[bool], Awaitable[None]]]:
    """Claim reminders across devices. The voice session acknowledges after sending speech."""
    claimed = []
    for doc in await asyncio.to_thread(store.eligible, "voice", owner):
        token = await asyncio.to_thread(store.claim, doc, "voice")
        if token:
            claimed.append((doc, token))
    hour = now().astimezone(ZoneInfo(settings.agent.timezone)).hour
    greeting = "Bom dia!" if hour < 12 else "Boa tarde!" if hour < 18 else "Boa noite!"
    text = greeting + (
        " Seus lembretes: " + " ".join(line(d) for d, _ in claimed) if claimed else " Pode falar."
    )

    async def finish(success: bool) -> None:
        for doc, token in claimed:
            await asyncio.to_thread(store.finish, doc, "voice", token, success=success)
            if success:
                event_log.log("reminder", action="voice_reminded", reminder_id=doc["_id"])

    return text, finish
