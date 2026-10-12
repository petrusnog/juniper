"""Provider router with failover, following ``settings.llm.priority`` (default Groq → Ollama)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, ClassVar

from brain.config import settings
from brain.eventlog import event_log
from brain.llm.base import LLMProvider
from brain.llm.compact import compact_messages, compact_tools
from brain.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from brain.llm.groq_provider import GroqProvider
from brain.llm.ollama_provider import OllamaProvider
from brain.llm.types import LLMResponse, Message, StreamChunk

logger = logging.getLogger(__name__)

PROVIDERS: dict[str, type[LLMProvider]] = {"groq": GroqProvider, "ollama": OllamaProvider}

# Errors that make a provider unusable for this call; the next one gets a chance.
# Any other LLMError (bad request, malformed response) would repeat elsewhere: it propagates.
_FAILOVER_ERRORS = (LLMUnavailableError, LLMTimeoutError, LLMRateLimitError, LLMAuthError)


class LLMRouter(LLMProvider):
    """Same interface as a provider; tries each one in priority order.

    ``stream`` only fails over before the first chunk is emitted, so a failure
    mid-stream is raised instead of replaying (and duplicating) output.
    """

    name: ClassVar[str] = "router"

    def __init__(self, providers: Sequence[LLMProvider] | None = None) -> None:
        # ``providers`` is injectable for tests; otherwise built from ``llm.priority``.
        if providers is None:
            providers = [PROVIDERS[n]() for n in settings.llm.priority]
        if not providers:
            raise ValueError("LLMRouter precisa de ao menos um provider")
        self._providers = list(providers)
        # Name of the provider that served the latest call (None before the first success).
        self.last_provider: str | None = None
        # Optional hook for clients: ``(failed_provider, error, next_provider_or_None)``. Lets a UI
        # tell the user that a slower fallback is taking over instead of going silent.
        self.on_failover: Callable[[str, LLMError, str | None], None] | None = None

    @property
    def providers(self) -> tuple[LLMProvider, ...]:
        return tuple(self._providers)

    async def aclose(self) -> None:
        for provider in self._providers:
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        failures: list[tuple[str, LLMError]] = []
        for index, provider in enumerate(self._providers):
            try:
                send_messages, send_tools = self._prepare(provider, messages, tools)
                response = await provider.chat(
                    send_messages, tools=send_tools, temperature=temperature, max_tokens=max_tokens
                )
                self.last_provider = provider.name
                return response
            except _FAILOVER_ERRORS as exc:
                self._record_failure(index, exc, failures)
        raise self._all_failed(failures)

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        failures: list[tuple[str, LLMError]] = []
        for index, provider in enumerate(self._providers):
            started = False
            try:
                send_messages, send_tools = self._prepare(provider, messages, tools)
                async for chunk in provider.stream(
                    send_messages, tools=send_tools, temperature=temperature, max_tokens=max_tokens
                ):
                    started = True
                    self.last_provider = provider.name
                    yield chunk
                return
            except _FAILOVER_ERRORS as exc:
                if started:
                    raise
                self._record_failure(index, exc, failures)
        raise self._all_failed(failures)

    async def health_check(self) -> bool:
        results = await asyncio.gather(*(p.health_check() for p in self._providers))
        return any(results)

    @staticmethod
    def _prepare(
        provider: LLMProvider, messages: list[Message], tools: list[dict[str, Any]] | None
    ) -> tuple[list[Message], list[dict[str, Any]] | None]:
        """What this provider should receive: compact for slow local ones, as-is otherwise."""
        if provider.compact_prompt:
            return compact_messages(messages), compact_tools(tools)
        return messages, tools

    def _record_failure(
        self, index: int, exc: LLMError, failures: list[tuple[str, LLMError]]
    ) -> None:
        provider = self._providers[index]
        failures.append((provider.name, exc))
        event_log.log("failover", provider=provider.name, type=type(exc).__name__, message=str(exc))
        logger.warning("Failover: %s falhou (%s: %s)", provider.name, type(exc).__name__, exc)
        if self.on_failover is not None:
            following = (
                self._providers[index + 1].name if index + 1 < len(self._providers) else None
            )
            self.on_failover(provider.name, exc, following)

    @staticmethod
    def _all_failed(failures: list[tuple[str, LLMError]]) -> LLMError:
        causes = "; ".join(f"{name}: {type(exc).__name__}: {exc}" for name, exc in failures)
        error = LLMError(f"Todos os providers falharam ({causes})")
        error.__cause__ = failures[-1][1]
        return error
