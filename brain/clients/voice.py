"""How Juniper talks in the terminal: one prefix for her, one for the user.

    🌿 JUNIPER: <what Juniper says>      AI replies and fixed-text commands alike
    👤 VOCÊ: <what the user types>       (later: the user's name, once memory exists)

Two kinds of speech share the same prefix:
  * generated (``juniper chat``): streamed from the LLM after ``start_reply``;
  * fixed (version, tools, install, errors, confirmations...): ``say`` with a canned text.
The shell side (``shell/core/voice.sh``) uses the same strings; a test keeps them in sync.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

JUNIPER_TAG = "🌿 JUNIPER:"
USER_TAG = "👤 VOCÊ:"

# Continuation lines of a multi-line speech are indented under the first one.
_INDENT = "   "


def user_tag() -> str:
    """Prefix for what the user types. Single point of change for the name (memory, Sprint 3)."""
    return USER_TAG


def user_prompt() -> str:
    """Prompt string for ``input()``: the user's tag plus a space."""
    return f"{user_tag()} "


def tag_text() -> Text:
    return Text.assemble((JUNIPER_TAG, "bold green"), " ")


def say(out: Console, text: str, style: str | None = None) -> None:
    """Juniper says a fixed ``text`` (plain, never parsed as markup)."""
    first, *rest = text.split("\n")
    out.print(Text.assemble(tag_text(), (first, style or "")), highlight=False)
    for line in rest:
        out.print(Text(f"{_INDENT}{line}", style=style or ""), highlight=False)


def start_reply(out: Console) -> None:
    """Open a generated reply: the tag, no newline; tokens are streamed right after."""
    out.print(tag_text(), end="", highlight=False)
