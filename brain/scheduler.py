"""A tiny daily scheduler: run a job once a day at HH:MM (agent timezone), like a cron entry.

Used by the server for the log housekeeping (``brain.logfiles.run_rotation``). There is no cron
daemon inside the container, and the job is idempotent, so the server also runs it once at start
to catch up after a day it was down.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from brain.config import settings
from brain.eventlog import event_log


def seconds_until(hhmm: str, now: datetime) -> float:
    """Seconds from ``now`` to the next ``HH:MM`` (today if still ahead, otherwise tomorrow)."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_daily(name: str, hhmm: str, job: Callable[[], Any], *, catch_up: bool = True) -> None:
    """Run ``job`` (in a thread) at start if ``catch_up``, then every day at ``hhmm``, forever."""
    if catch_up:
        await _run(name, job)
    while True:
        now = datetime.now(ZoneInfo(settings.agent.timezone))
        await asyncio.sleep(seconds_until(hhmm, now))
        await _run(name, job)


async def _run(name: str, job: Callable[[], Any]) -> None:
    try:
        await asyncio.to_thread(job)
    except Exception as exc:  # noqa: BLE001  (a failed housekeeping run must not stop the server)
        event_log.log("error", where=f"scheduler.{name}", type=type(exc).__name__, message=str(exc))
