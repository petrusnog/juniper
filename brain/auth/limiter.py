"""Limite de tentativas de login erradas, por chave (cliente ou client_id)."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

MAX_FAILURES = 5
WINDOW_SECONDS = 300
MAX_TRACKED = 500


class LoginLimiter:
    """At most ``MAX_FAILURES`` wrong logins per key in ``WINDOW_SECONDS``."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: OrderedDict[str, list[float]] = OrderedDict()

    def _recent(self, key: str) -> list[float]:
        cutoff = self._clock() - WINDOW_SECONDS
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= MAX_FAILURES

    def failed(self, key: str) -> None:
        recent = self._recent(key)
        recent.append(self._clock())
        self._failures[key] = recent
        self._failures.move_to_end(key)
        while len(self._failures) > MAX_TRACKED:
            self._failures.popitem(last=False)

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)
