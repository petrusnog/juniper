"""Daily quota counter for the YouTube Data API (ADR-024, section 4).

Google gives each project 10,000 units a day (a search costs 100, a write 50) and resets the
count at midnight Pacific time. The brain counts what it spends in a small JSON file, so the
count survives a restart, and refuses *before* calling the API once the configured limit is
reached, instead of earning a 403. The file is written atomically; a corrupt or missing one
just starts the day at zero.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from brain.music.base import MusicError

PACIFIC = ZoneInfo("America/Los_Angeles")

COST_SEARCH = 100
COST_READ = 1
COST_WRITE = 50


def _now() -> datetime:
    return datetime.now(UTC)


class QuotaTracker:
    """Counts API units per Pacific day and refuses to go past ``limit``."""

    def __init__(self, path: Path, limit: int, *, clock: Callable[[], datetime] = _now) -> None:
        self._path = path
        self._limit = limit
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    def _day(self) -> str:
        return self._clock().astimezone(PACIFIC).date().isoformat()

    def _used(self) -> int:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            used = data.get("used")
            if data.get("day") == self._day() and isinstance(used, int):
                return max(0, used)
        except (OSError, ValueError, AttributeError):
            pass
        return 0

    def _store(self, used: int) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        tmp.write_text(json.dumps({"day": self._day(), "used": used}), encoding="utf-8")
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)
        os.replace(tmp, self._path)

    def used(self) -> int:
        with self._lock:
            return self._used()

    def remaining(self) -> int:
        with self._lock:
            return max(0, self._limit - self._used())

    def resets_in(self) -> timedelta:
        """Time until the next Pacific midnight."""
        local = self._clock().astimezone(PACIFIC)
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight - local

    def reserve(self, units: int) -> None:
        """Count ``units`` for a call about to be made, or raise ``quota_exhausted``."""
        with self._lock:
            used = self._used()
            if used + units > self._limit:
                raise MusicError("quota_exhausted", self.exhausted_message())
            self._store(used + units)

    def mark_exhausted(self) -> None:
        """The API itself said the quota is gone: stop calling until the day rolls over."""
        with self._lock:
            self._store(max(self._used(), self._limit))

    def exhausted_message(self) -> str:
        hours, rest = divmod(int(self.resets_in().total_seconds()), 3600)
        return (
            "A cota diária da busca de músicas acabou. "
            f"Ela volta em cerca de {hours}h{rest // 60:02d} (meia-noite no horário do Pacífico)."
        )
