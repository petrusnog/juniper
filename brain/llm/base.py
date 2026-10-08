"""`LLMProvider` interface that Ollama and Groq implement.

The agent talks only to this abstraction, never to a concrete provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from brain.llm.types import LLMResponse, Message, StreamChunk


class LLMProvider(ABC):
    """Async chat provider with optional tool calling and streaming.

    Implementations must raise the typed errors from ``brain.llm.errors``.
    ``tools`` uses the OpenAI function-calling schema (ADR-002).
    """

    name: ClassVar[str]

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Return a complete response."""

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Yield response chunks (implement as an ``async def`` generator)."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is reachable and usable."""
