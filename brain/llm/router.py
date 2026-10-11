"""Provider router with failover, following ``settings.llm.priority`` (default Groq → Ollama)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar

from brain.config import settings
from brain.llm.base import LLMProvider
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
        for provider in self._providers:
            try:
                response = await provider.chat(
                    messages, tools=tools, temperature=temperature, max_tokens=max_tokens
                )
                self.last_provider = provider.name
                return response
            except _FAILOVER_ERRORS as exc:
                self._record_failure(provider, exc, failures)
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
        for provider in self._providers:
            started = False
            try:
                async for chunk in provider.stream(
                    messages, tools=tools, temperature=temperature, max_tokens=max_tokens
                ):
                    started = True
                    self.last_provider = provider.name
                    yield chunk
                return
            except _FAILOVER_ERRORS as exc:
                if started:
                    raise
                self._record_failure(provider, exc, failures)
        raise self._all_failed(failures)

    async def health_check(self) -> bool:
        results = await asyncio.gather(*(p.health_check() for p in self._providers))
        return any(results)

    @staticmethod
    def _record_failure(
        provider: LLMProvider, exc: LLMError, failures: list[tuple[str, LLMError]]
    ) -> None:
        failures.append((provider.name, exc))
        logger.warning("Failover: %s falhou (%s: %s)", provider.name, type(exc).__name__, exc)

    @staticmethod
    def _all_failed(failures: list[tuple[str, LLMError]]) -> LLMError:
        causes = "; ".join(f"{name}: {type(exc).__name__}: {exc}" for name, exc in failures)
        error = LLMError(f"Todos os providers falharam ({causes})")
        error.__cause__ = failures[-1][1]
        return error
