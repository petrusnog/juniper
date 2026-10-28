"""The monthly budget of spoken characters (ADR-023, section 3).

The Google TTS is paid by the character, so the brain counts what it sends in a small JSON file
(per calendar month, in the agent's time zone; it works without MongoDB and survives a restart)
and, once ``voice.tts.monthly_chars_limit`` is reached, the voice falls back to the device's own.
Writing the file never breaks a conversation: if the disk refuses, the count lives in memory.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from brain.config import settings


def _now() -> datetime:
    return datetime.now(UTC)


class VoiceBudget:
    """Counts the characters sent to the cloud TTS this month and refuses past ``limit``."""

    def __init__(
        self,
        path: Path,
        limit: int,
        *,
        clock: Callable[[], datetime] = _now,
        zone: ZoneInfo | None = None,
    ) -> None:
        self._path = path
        self._limit = limit
        self._clock = clock
        self._zone = zone or ZoneInfo(settings.agent.timezone)
        self._lock = threading.Lock()
        self._month = ""
        self._used = 0

    @property
    def limit(self) -> int:
        return self._limit

    def _current_month(self) -> str:
        return self._clock().astimezone(self._zone).strftime("%Y-%m")

    def _sync(self) -> None:
        """Load the month's count (from the file the first time and when the month changes)."""
        month = self._current_month()
        if month == self._month:
            return
        self._month, self._used = month, 0
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            used = data.get("tts_chars")
            if data.get("month") == month and isinstance(used, int):
                self._used = max(0, used)
        except (OSError, ValueError, AttributeError):
            pass  # no file, or a corrupt one: the month starts at zero

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self._path.with_name(f"{self._path.name}.tmp")
            tmp.write_text(
                json.dumps({"month": self._month, "tts_chars": self._used}), encoding="utf-8"
            )
            with contextlib.suppress(OSError):
                tmp.chmod(0o600)
            os.replace(tmp, self._path)
        except OSError:
            pass  # keep going on the in-memory count: a full disk must not silence the Juniper

    def used(self) -> int:
        with self._lock:
            self._sync()
            return self._used

    def remaining(self) -> int:
        with self._lock:
            self._sync()
            return max(0, self._limit - self._used)

    def exhausted(self) -> bool:
        """True when the cloud voice cannot be used any more this month (or is switched off)."""
        return self.remaining() <= 0

    def reserve(self, chars: int) -> bool:
        """Count ``chars`` for a call about to be made; ``False`` (nothing counted) if it would
        go past the limit."""
        with self._lock:
            self._sync()
            if self._used + chars > self._limit:
                return False
            self._used += chars
            self._save()
            return True

    def exhaust(self) -> None:
        """Call the month's budget spent (what is left is less than a sentence): no more cloud
        voice until the month turns, instead of flipping between voices sentence by sentence."""
        with self._lock:
            self._sync()
            self._used = max(self._used, self._limit)
            self._save()

    def release(self, chars: int) -> None:
        """Give back what was reserved for a call that failed (the provider bills only success)."""
        with self._lock:
            self._sync()
            self._used = max(0, self._used - chars)
            self._save()
