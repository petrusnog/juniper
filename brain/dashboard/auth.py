"""Login do dashboard: o token do servidor vira um cookie assinado, com prazo.

O token (ADR-007) nunca vai na URL: a tela de login o manda num cabeçalho `Authorization`. O
cookie é `expira.assinatura`, com a assinatura HMAC derivada do token, então trocar o token no
servidor derruba todas as sessões. Falhas de login são limitadas por cliente.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict
from collections.abc import Callable

COOKIE_NAME = "juniper_session"
MAX_FAILURES = 5
WINDOW_SECONDS = 300
MAX_TRACKED_CLIENTS = 500


class SessionSigner:
    def __init__(self, token: str, hours: int = 12, clock: Callable[[], float] = time.time) -> None:
        self._key = hashlib.sha256(b"juniper-dashboard:" + token.encode()).digest()
        self.max_age = hours * 3600
        self._clock = clock

    def _sign(self, expires: int) -> str:
        return hmac.new(self._key, str(expires).encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        expires = int(self._clock()) + self.max_age
        return f"{expires}.{self._sign(expires)}"

    def valid(self, cookie: str | None) -> bool:
        if not cookie:
            return False
        expires_text, _, signature = cookie.partition(".")
        if not expires_text.isdigit():
            return False
        expires = int(expires_text)
        if expires < self._clock():
            return False
        return hmac.compare_digest(signature.encode(), self._sign(expires).encode())


class LoginLimiter:
    """At most ``MAX_FAILURES`` wrong tokens per client in ``WINDOW_SECONDS``."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: OrderedDict[str, list[float]] = OrderedDict()

    def _recent(self, client: str) -> list[float]:
        cutoff = self._clock() - WINDOW_SECONDS
        recent = [t for t in self._failures.get(client, []) if t > cutoff]
        if recent:
            self._failures[client] = recent
        else:
            self._failures.pop(client, None)
        return recent

    def blocked(self, client: str) -> bool:
        return len(self._recent(client)) >= MAX_FAILURES

    def failed(self, client: str) -> None:
        recent = self._recent(client)
        recent.append(self._clock())
        self._failures[client] = recent
        self._failures.move_to_end(client)
        while len(self._failures) > MAX_TRACKED_CLIENTS:
            self._failures.popitem(last=False)

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)


def read_cookie(header: str | None, name: str = COOKIE_NAME) -> str | None:
    """The value of cookie ``name`` in a ``Cookie`` header."""
    if not header:
        return None
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None
