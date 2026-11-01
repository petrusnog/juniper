"""Login do dashboard: a credencial (ADR-027) vira um cookie assinado, com prazo.

A credencial (``client_id`` + ``client_secret``) nunca vai na URL: a tela de login a manda num
cabeçalho ``Authorization``. O cookie é ``expira.client_id.impressão.assinatura``; a assinatura é
HMAC derivada do *pepper* do servidor, e a «impressão» é o começo do hash do segredo: rotacionar
o segredo ou revogar a credencial derruba a sessão. As falhas de login são limitadas pelo
serviço de identidade.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass

from brain.auth.limiter import MAX_FAILURES, WINDOW_SECONDS, LoginLimiter

__all__ = [
    "COOKIE_NAME",
    "MAX_FAILURES",
    "WINDOW_SECONDS",
    "LoginLimiter",
    "Session",
    "SessionSigner",
    "read_cookie",
]

COOKIE_NAME = "juniper_session"
LEGACY_CLIENT_ID = "legacy-token"


@dataclass(frozen=True)
class Session:
    client_id: str
    fingerprint: str


class SessionSigner:
    def __init__(
        self,
        secret: str | bytes,
        hours: int = 12,
        clock: Callable[[], float] = time.time,
    ) -> None:
        raw = secret.encode() if isinstance(secret, str) else secret
        self._key = hashlib.sha256(b"juniper-dashboard:" + raw).digest()
        self.max_age = hours * 3600
        self._clock = clock

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, client_id: str = LEGACY_CLIENT_ID, fingerprint: str = "") -> str:
        expires = int(self._clock()) + self.max_age
        payload = f"{expires}.{client_id}.{fingerprint}"
        return f"{payload}.{self._sign(payload)}"

    def parse(self, cookie: str | None) -> Session | None:
        """The session in a valid, unexpired cookie; ``None`` for anything else."""
        if not cookie:
            return None
        payload, _, signature = cookie.rpartition(".")
        parts = payload.split(".", 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1]:
            return None
        if int(parts[0]) < self._clock():
            return None
        if not hmac.compare_digest(signature.encode(), self._sign(payload).encode()):
            return None
        return Session(parts[1], parts[2])

    def valid(self, cookie: str | None) -> bool:
        return self.parse(cookie) is not None

    def csrf(self, cookie: str) -> str:
        """The anti-CSRF token of one session: a page of another origin cannot read it."""
        return self._sign("csrf:" + cookie)


def read_cookie(header: str | None, name: str = COOKIE_NAME) -> str | None:
    """The value of cookie ``name`` in a ``Cookie`` header."""
    if not header:
        return None
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None
