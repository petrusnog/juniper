"""The typed voice errors (ADR-023). Every expected failure is a `VoiceError` with a stable code;
the message is fixed PT-BR and never carries a key, a URL or a raw provider answer.
"""

from __future__ import annotations

from typing import Literal

VoiceErrorCode = Literal[
    "not_configured",
    "network_error",
    "api_error",
    "quota",  # the provider says the quota or rate limit is gone
    "invalid_audio",
]


class VoiceError(Exception):
    """An expected voice failure. ``retry_after`` (seconds) is set when the provider gave one."""

    def __init__(
        self, code: VoiceErrorCode, message: str, *, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.code: VoiceErrorCode = code
        self.message = message
        self.retry_after = retry_after
