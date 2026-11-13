"""Conversations as the text clients show them (T-226): the Telegram bot and the terminal.

``/conversas`` lists the latest ones by title, ``/renomear <título>`` renames the open one and
``/retomar <número|id>`` goes back to another. The apps and the dashboard have screens for this;
these are the same three actions for the clients that only have a line of text. Pure functions:
the callers (``telegram.py``, ``remote_cli.py``, ``cli.py``) do the talking.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from brain.clients import protocol as p
from brain.core.title import clean_title

LIST_LIMIT: Final = 10
NO_TITLE: Final = "(sem título)"
NO_CONVERSATIONS: Final = "Ainda não há conversas guardadas."
RENAME_USAGE: Final = "Use /renomear <novo título> (até 80 caracteres)."
RESUME_USAGE: Final = "Use /retomar <número da lista ou id>. Veja a lista com /conversas."
NOT_FOUND: Final = "Não achei essa conversa. Veja a lista com /conversas."
AMBIGUOUS: Final = "Esse começo de id serve para mais de uma conversa. Use o número da lista."
HELP_LINES: Final = (
    "/conversas: lista as conversas pelo título\n"
    "/renomear <título>: renomeia a conversa atual\n"
    "/retomar <número|id>: volta a uma conversa da lista\n"
)

_COMMANDS: Final = {
    "conversas": "list",
    "renomear": "rename",
    "titulo": "rename",
    "título": "rename",
    "retomar": "resume",
}
_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


@dataclass(frozen=True)
class Entry:
    """One line of the list, whatever the client (a protocol summary or a store's info)."""

    id: str
    title: str
    preview: str
    updated_at: str
    turns: int

    @property
    def label(self) -> str:
        return self.title or self.preview or NO_TITLE


def parse(text: str) -> tuple[str, str] | None:
    """``("list"|"rename"|"resume", argument)`` for one of the three commands, else None.

    ``/renomear@MeuBot Bolo`` (how Telegram names a bot in a group) counts as ``/renomear Bolo``.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped.partition(" ")
    action = _COMMANDS.get(head[1:].split("@")[0].lower())
    return (action, rest.strip()) if action else None


def ago(moment: str, now: datetime | None = None) -> str:
    """``há 5 min`` / ``há 3 h`` / ``há 2 dias`` for an ISO timestamp ("" when unreadable)."""
    try:
        then = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    seconds = max(0.0, ((now or datetime.now(UTC)) - then).total_seconds())
    if seconds < 90:
        return "agora há pouco"
    if seconds < 3600:
        return f"há {round(seconds / 60)} min"
    if seconds < 172800:
        return f"há {round(seconds / 3600)} h"
    return f"há {round(seconds / 86400)} dias"


def format_list(
    entries: Sequence[Entry], current: str | None = None, now: datetime | None = None
) -> str:
    """The numbered list: ``1. Receita de bolo · há 2 h · 6 mensagens`` (the open one marked)."""
    if not entries:
        return NO_CONVERSATIONS
    lines = ["Conversas (use /retomar <número>):"]
    for number, entry in enumerate(entries[:LIST_LIMIT], start=1):
        mark = " ← esta" if entry.id == current else ""
        noun = "mensagem" if entry.turns == 1 else "mensagens"
        lines.append(
            f"{number}. {entry.label} · {ago(entry.updated_at, now)} · {entry.turns} {noun}{mark}"
        )
    return "\n".join(lines)


def resolve(argument: str, entries: Sequence[Entry]) -> tuple[Entry | None, str]:
    """The conversation a ``/retomar`` argument names: a number of the list or a start of an id.

    Returns ``(entry, "")`` or ``(None, message)`` saying what went wrong.
    """
    arg = argument.strip()
    if not arg:
        return None, RESUME_USAGE
    shown = list(entries[:LIST_LIMIT])
    if arg.isdigit() and len(arg) <= 3:  # a longer run of digits is the date that starts an id
        number = int(arg)
        if 1 <= number <= len(shown):
            return shown[number - 1], ""
        return None, NOT_FOUND
    if not _ID.match(arg):
        return None, NOT_FOUND
    matches = [e for e in entries if e.id.startswith(arg)]
    if len(matches) == 1:
        return matches[0], ""
    return None, AMBIGUOUS if matches else NOT_FOUND


def renamed_text(title: str) -> str:
    return f"Pronto: esta conversa agora se chama «{title}»."


def new_title(argument: str) -> str:
    """The title to send for ``/renomear <argument>`` ("" when there is nothing usable)."""
    return clean_title(argument)


Request = Callable[[dict[str, Any]], Awaitable[p.ServerMessage]]
"""``RemoteClient.request``: sends a frame, returns the reply (``ServerError`` on an ``error``)."""


@dataclass(frozen=True)
class Outcome:
    """What a command did: the text to show and, after ``/retomar``, the state to adopt."""

    text: str
    resumed: p.SessionState | None = None


def entries_of(listed: p.ServerMessage) -> list[Entry]:
    """The conversations of a ``session.listed`` reply."""
    if not isinstance(listed, p.SessionListed):
        return []
    return [Entry(s.id, s.title, s.preview, s.updated_at, s.turns) for s in listed.sessions]


async def run(request: Request, action: str, argument: str, current: str | None = None) -> Outcome:
    """Do ``/conversas``, ``/renomear`` or ``/retomar`` through the protocol (remote clients)."""
    if action == "rename":
        title = new_title(argument)
        if not title:
            return Outcome(RENAME_USAGE)
        reply = await request({"type": "session.rename", "title": title})
        return Outcome(renamed_text(reply.title if isinstance(reply, p.SessionRenamed) else title))
    entries = entries_of(await request({"type": "session.list", "limit": 50}))
    if action == "list":
        return Outcome(format_list(entries, current))
    chosen, problem = resolve(argument, entries)
    if chosen is None:
        return Outcome(problem)
    state = await request({"type": "session.resume", "session_id": chosen.id})
    if isinstance(state, p.SessionState):
        return Outcome(f"Voltei para «{state.title or chosen.label}». Pode continuar.", state)
    return Outcome(NOT_FOUND)
