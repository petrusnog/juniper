"""The manual leak report (T-167): "isso vazou", said by the user in the moment.

The automatic audit (``core/leak_audit.py``) only flags what its rules know. The report is the
person saying "this one leaked": the last ``REPORT_EXCHANGES`` exchanges of the conversation, as he
saw them, plus the session, the channel and a note. Pure: no I/O. The chat handler stores it.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Final

import brain
from brain.memory.secretfilter import mask_secrets

COMMAND: Final = "/vazou"
REPORT_EXCHANGES: Final = 10
MAX_NOTE_CHARS: Final = 500
MAX_TEXT_CHARS: Final = 8000  # per message: a report is evidence, not a copy of the whole chat

_COMMAND = re.compile(r"^/vazou(?:@\w+)?(?:\s+(?P<note>.*))?$", re.IGNORECASE | re.DOTALL)
_DATE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_SYSTEM_HINT = re.compile(r"\s*\[Sistema:[^\]]*\]\s*$")  # the agent appends it to a bare "sim"


@dataclass(frozen=True)
class Exchange:
    """What the user said and what she answered, as it was shown to him."""

    user: str
    assistant: str
    at: str | None = None  # ISO instant the turn ended; None for those read from a saved session
    interrupted: bool = False  # he cut her off: ``assistant`` is only what she had said so far


def leak_command(text: str) -> str | None:
    """The note of a ``/vazou [nota]`` message ("" without one), or ``None`` if it is not one."""
    match = _COMMAND.match(text.strip())
    if match is None:
        return None
    return " ".join((match.group("note") or "").split())[:MAX_NOTE_CHARS]


def exchanges_from_history(history: Sequence[Any]) -> list[Exchange]:
    """The visible exchanges of a saved conversation: user message, then what she said (tool
    messages and the stamps the system adds are left out)."""
    found: list[Exchange] = []
    user: str | None = None
    replies: list[str] = []

    def close() -> None:
        if user is not None:
            found.append(Exchange(user, "\n".join(replies)))

    for message in history:
        if message.role == "user" and message.content:
            close()
            text = _SYSTEM_HINT.sub("", _DATE_PREFIX.sub("", message.content, count=1))
            user, replies = text, []
        elif message.role == "assistant" and message.content and not message.tool_calls:
            replies.append(message.content)
    close()
    return found


def _clip(text: str) -> str:
    text = mask_secrets(text)
    return text if len(text) <= MAX_TEXT_CHARS else f"{text[:MAX_TEXT_CHARS]}…"


def build_report(
    *,
    exchanges: Sequence[Exchange],
    session_id: str,
    channel: str | None,
    voice: bool,
    note: str,
    audit_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """The document stored for one report (``docs/pages/vazamentos.md``). Secrets are masked."""
    kept = list(exchanges)[-REPORT_EXCHANGES:]
    return {
        "_id": uuid.uuid4().hex,
        "at": datetime.now(UTC),
        "session": session_id,
        "channel": channel,
        "voice": voice,
        "note": mask_secrets(note)[:MAX_NOTE_CHARS],
        "exchanges": [
            {**asdict(e), "user": _clip(e.user), "assistant": _clip(e.assistant)} for e in kept
        ],
        "audit": list(audit_ids),
        "version": brain.__version__,
        "commit": os.environ.get("GIT_COMMIT") or None,
    }
