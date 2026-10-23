"""Conversation state: message history and the system prompt.

The system prompt is static (persona only) so providers can reuse their prompt cache;
the current date/time (ADR-008) is prefixed to each user message instead.

Short-term memory (ADR-011): the history is capped by turns *and* by an estimated token
budget, whole turns at a time, oldest first. Saving and resuming a conversation lives in
``brain.memory.session``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from brain.config import settings
from brain.core import clock
from brain.core.persona import CORE_PATH, load_core
from brain.llm.types import Message

PERSONA_PATH = CORE_PATH  # the fixed core (brain/core/persona.py); living blocks come after it

# Rough size of a token in characters (Portuguese + code). Good enough to budget a prompt
# without depending on a tokenizer.
CHARS_PER_TOKEN = 4
# Per-message framing (role, separators) on top of its text.
MESSAGE_OVERHEAD_TOKENS = 4


@dataclass(frozen=True)
class Turn:
    """One finished exchange: what the user typed and the final text Juniper answered.

    Unlike ``Session.history`` this is never compacted, so long-term memory extraction sees
    the whole conversation, without tool output or the date prefix of the user turns.
    """

    user: str
    assistant: str = ""
    # 0-based position of this turn in the whole conversation (``Session.turns_total``), the
    # provenance that facts and beliefs cite. ``None`` when the caller does not track it.
    index: int | None = None
    # True when tools ran during the turn: her reply may then carry what the tools returned
    # (an event title, a commit message), which must never feed the subjective layers.
    used_tools: bool = False


def load_system_prompt(path: Path = PERSONA_PATH) -> str:
    return load_core(path)


def estimate_tokens(message: Message) -> int:
    """Estimated tokens of one message: its text plus any tool calls it carries."""
    chars = len(message.content or "")
    for call in message.tool_calls:
        chars += len(call.name) + len(json.dumps(call.arguments, ensure_ascii=False))
    return chars // CHARS_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS


def new_session_id() -> str:
    """Sortable by creation time: ``20261013-221500-a1b2c3``."""
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Session:
    """Holds the history of one conversation (everything except the system message)."""

    def __init__(
        self,
        system_prompt: str | None = None,
        max_turns: int | None = None,
        max_tokens: int | None = None,
        session_id: str | None = None,
    ) -> None:
        config = settings.memory.session
        self.system_prompt = system_prompt if system_prompt is not None else load_system_prompt()
        self.max_turns = max_turns if max_turns is not None else config.max_turns
        self.max_tokens = max_tokens if max_tokens is not None else config.max_tokens
        self.id = session_id or new_session_id()
        self.created_at = self.updated_at = _timestamp()
        self.history: list[Message] = []
        # Turns finished in this conversation since it began. Unlike ``history`` it is never
        # compacted, so a turn number keeps meaning the same thing across resumes.
        self.turns_total = 0

    def messages(self) -> list[Message]:
        """System prompt followed by the history, ready for the LLM."""
        return [Message("system", self.system_prompt), *self.history]

    def add(self, message: Message) -> None:
        self.history.append(message)
        self.updated_at = _timestamp()

    def add_user(self, text: str) -> None:
        """Append a user turn stamped with the current date and time."""
        self.add(Message("user", f"[{clock.describe()}] {text}"))

    def checkpoint(self) -> int:
        return len(self.history)

    def rollback(self, checkpoint: int) -> None:
        """Drop everything added after ``checkpoint`` (e.g. a turn that failed)."""
        del self.history[checkpoint:]
        self.updated_at = _timestamp()

    def tokens(self) -> int:
        """Estimated tokens of what the LLM receives (system prompt and history)."""
        return (
            len(self.system_prompt) // CHARS_PER_TOKEN
            + MESSAGE_OVERHEAD_TOKENS
            + sum(estimate_tokens(m) for m in self.history)
        )

    def _user_indexes(self) -> list[int]:
        return [i for i, m in enumerate(self.history) if m.role == "user"]

    def compact(self) -> None:
        """Cap the turns, drop old tool exchanges and fit the token budget.

        The previous turn keeps its tool messages (so "apaga esse evento" still has the
        id); anything older keeps only what the user and the assistant said. If the history
        is still over ``max_tokens``, the oldest turns go, whole, until it fits. The turn
        in progress is never dropped, even if it alone is over budget.
        """
        users = self._user_indexes()
        if len(users) > self.max_turns:
            del self.history[: users[-self.max_turns]]
            users = self._user_indexes()
        if len(users) >= 2:
            keep_from = users[-2]
            older = [m for m in self.history[:keep_from] if m.role != "tool" and not m.tool_calls]
            self.history[:keep_from] = older
        self._fit_budget()

    def _fit_budget(self) -> None:
        while self.tokens() > self.max_tokens:
            users = self._user_indexes()
            if len(users) < 2:
                return
            del self.history[: users[1]]
