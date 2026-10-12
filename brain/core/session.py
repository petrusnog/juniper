"""Conversation state: message history and the system prompt.

The system prompt is static (persona only) so providers can reuse their prompt cache;
the current date/time (ADR-008) is prefixed to each user message instead. Persistent
memory arrives in T-050 (Sprint 3).
"""

from __future__ import annotations

from pathlib import Path

from brain.core import clock
from brain.llm.types import Message

PERSONA_PATH = Path(__file__).resolve().parents[2] / "prompts" / "juniper.md"
_FALLBACK_PERSONA = "Você é a Juniper."

# User turns kept in the history; older ones are dropped to save tokens.
MAX_TURNS = 8


def load_system_prompt(path: Path = PERSONA_PATH) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else _FALLBACK_PERSONA


class Session:
    """Holds the history of one conversation (everything except the system message)."""

    def __init__(self, system_prompt: str | None = None, max_turns: int = MAX_TURNS) -> None:
        self.system_prompt = system_prompt if system_prompt is not None else load_system_prompt()
        self.max_turns = max_turns
        self.history: list[Message] = []

    def messages(self) -> list[Message]:
        """System prompt followed by the history, ready for the LLM."""
        return [Message("system", self.system_prompt), *self.history]

    def add(self, message: Message) -> None:
        self.history.append(message)

    def add_user(self, text: str) -> None:
        """Append a user turn stamped with the current date and time."""
        self.history.append(Message("user", f"[{clock.describe()}] {text}"))

    def checkpoint(self) -> int:
        return len(self.history)

    def rollback(self, checkpoint: int) -> None:
        """Drop everything added after ``checkpoint`` (e.g. a turn that failed)."""
        del self.history[checkpoint:]

    def compact(self) -> None:
        """Cap the turns and drop old tool exchanges.

        The previous turn keeps its tool messages (so "apaga esse evento" still has the
        id); anything older keeps only what the user and the assistant said.
        """
        users = [i for i, m in enumerate(self.history) if m.role == "user"]
        if len(users) > self.max_turns:
            del self.history[: users[-self.max_turns]]
            users = [i for i, m in enumerate(self.history) if m.role == "user"]
        if len(users) >= 2:
            keep_from = users[-2]
            older = [m for m in self.history[:keep_from] if m.role != "tool" and not m.tool_calls]
            self.history[:keep_from] = older
