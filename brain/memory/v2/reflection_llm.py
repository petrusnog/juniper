"""The LLMs of the nightly pass: the chat model for the cheap work, a stronger one to reflect.

``llm.reflection`` (provider and model) picks the stronger one; without it the pass reflects with
the chat model. A reasoning model may put its "thinking" in front of the answer: ``PhaseContext``
strips it before anyone parses the JSON (``night.strip_reasoning``).
"""

from __future__ import annotations

from brain.config import settings
from brain.llm.base import LLMProvider
from brain.llm.router import PROVIDERS, LLMRouter


def build_reflection_llm() -> LLMProvider | None:
    """A router over the configured reflection model, or ``None`` to use the chat's."""
    cfg = settings.llm.reflection
    if cfg.provider is None and cfg.model is None:
        return None
    name = cfg.provider or settings.llm.priority[0]
    return LLMRouter([PROVIDERS[name](model=cfg.model)])  # type: ignore[call-arg]
