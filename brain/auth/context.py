"""Who is speaking, for code that runs deep inside a turn (ADR-027 §9).

The chat handler sets these when a message arrives; every task it spawns for the turn inherits
them. A tool that creates a record uses them to stamp its ``owner_id``, without the LLM ever
seeing or choosing either.
"""

from __future__ import annotations

from contextvars import ContextVar

current_user: ContextVar[str | None] = ContextVar("juniper_current_user", default=None)
current_client: ContextVar[str | None] = ContextVar("juniper_current_client", default=None)
