"""The persona: a fixed core plus optional living blocks (ADR-017, sections 2 and 3).

``prompts/persona.core.md`` is the part of the persona that never changes at runtime: the rules
for tools and confirmation, security, form, honesty about what she is, and the limits of her
feelings. It lives in git; nothing in the memory layers writes it. What changes with her
experience (opinions, mood, personality traits) arrives as *blocks* that come after the core, so
the start of the system prompt stays identical between calls and providers can cache it.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
CORE_PATH = PROMPTS_DIR / "persona.core.md"
CORE_COMPACT_PATH = PROMPTS_DIR / "persona.core.compact.md"
FALLBACK_CORE = "Você é a Juniper."


def load_core(path: Path = CORE_PATH) -> str:
    """The fixed persona text (a one-line fallback if the file is missing)."""
    return path.read_text(encoding="utf-8") if path.exists() else FALLBACK_CORE


def build_system_prompt(blocks: Iterable[str] = (), core: str | None = None) -> str:
    """The core, then each non-empty living block, separated by a blank line.

    The core always comes first and unchanged, whatever the blocks say.
    """
    text = core if core is not None else load_core()
    extra = [b.strip() for b in blocks if b and b.strip()]
    return "\n\n".join([text.rstrip(), *extra]) if extra else text
