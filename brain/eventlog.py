"""Structured event log: one JSON object per line, one file per day (T-031).

Events: ``llm_request``, ``llm_response``, ``tool_call``, ``failover``, ``error``,
``memory_recall``, ``timing`` (where a turn or the memory work spent its time),
``embedding``, ``embedding_model_load``, ``connection`` and ``server`` (the WebSocket server).
Secrets are redacted before anything touches the disk. Logging never breaks the app:
an unwritable log directory is silently skipped.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from brain.config import settings

REDACTED = "***"
# Longest string value kept verbatim; the rest is cut so one huge tool output cannot flood the log.
MAX_VALUE_CHARS = 2000

_SECRET_KEY = re.compile(
    r"(api[_-]?key|(?<!first_)token(?!s)|secret|password|passwd|authorization|private[_-]?key)",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"(gsk_[A-Za-z0-9]{10,}"  # Groq keys
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$))"
)


def redact(value: Any, _key: str = "") -> Any:
    """Return a JSON-safe copy of ``value`` with secrets masked and long strings cut."""
    if _key and _SECRET_KEY.search(_key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact(v) for v in value]
    if isinstance(value, str):
        text = _mask_known_key(_SECRET_VALUE.sub(REDACTED, value))
        if len(text) > MAX_VALUE_CHARS:
            text = f"{text[:MAX_VALUE_CHARS]}…[+{len(text) - MAX_VALUE_CHARS} chars]"
        return text
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact(str(value))


def _mask_known_key(text: str) -> str:
    """Mask the configured provider keys even if they do not match a known pattern."""
    for key in (settings.groq_api_key, settings.deepinfra_api_key):
        secret = key.get_secret_value() if key else ""
        if len(secret) >= 8:
            text = text.replace(secret, REDACTED)
    return text


def _narrate(event: str, record: dict[str, Any]) -> None:
    """Passa o evento ao diário de mensagens (message.log), se houver uma interação em curso."""
    from brain.trace import message_trace  # import tardio: o diário também usa o redact daqui

    if message_trace.active:
        fields = {k: v for k, v in record.items() if k not in ("ts", "run", "event")}
        message_trace.on_event(event, fields)


class EventLog:
    """Appends events to ``<directory>/YYYY-MM-DD.jsonl`` (directory resolved lazily)."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self.enabled = True
        # Groups the events of one process run (one CLI invocation or REPL session).
        self.run_id = uuid.uuid4().hex[:8]

    @property
    def directory(self) -> Path:
        if self._directory is not None:
            return self._directory
        if settings.paths is None:
            raise RuntimeError("settings.paths não calculado")
        return settings.paths.logs

    @directory.setter
    def directory(self, value: Path | None) -> None:
        self._directory = value

    def path_for(self, moment: datetime) -> Path:
        return self.directory / f"{moment:%Y-%m-%d}.jsonl"

    def log(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        try:
            now = datetime.now(ZoneInfo(settings.agent.timezone))
            record = {
                "ts": now.isoformat(timespec="milliseconds"),
                "run": self.run_id,
                "event": event,
            }
            record.update(redact(fields))
            _narrate(event, record)
            line = json.dumps(record, ensure_ascii=False, default=str)
            self.directory.mkdir(parents=True, exist_ok=True)
            # 0600: the log holds what the user said and what the tools returned.
            fd = os.open(self.path_for(now), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:  # noqa: BLE001  (logging must never take the app down)
            return


event_log = EventLog()
