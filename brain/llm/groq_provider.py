"""Groq provider (primary, cloud), via the official ``groq`` SDK."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import groq

from brain.config import settings
from brain.llm.base import LLMProvider
from brain.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from brain.llm.types import LLMResponse, Message, StreamChunk, TokenUsage, ToolCall


class GroqProvider(LLMProvider):
    """Talks to Groq's OpenAI-compatible API. Defaults come from ``settings``.

    The API key is resolved lazily: constructing the provider never fails, so the
    router can instantiate every provider in ``llm.priority``. A missing key surfaces
    as ``LLMAuthError`` on the first call (and ``health_check`` returns False).
    """

    name: ClassVar[str] = "groq"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        cfg = settings.llm.groq
        self._model = model or cfg.model
        self._timeout = timeout or cfg.timeout
        if api_key is None and settings.groq_api_key is not None:
            api_key = settings.groq_api_key.get_secret_value()
        self._api_key = api_key or None
        # ``client`` is injectable for tests (any object exposing the SDK surface).
        self._client: Any | None = client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise LLMAuthError(
                    "Groq: chave de API ausente. Defina JUNIPER_GROQ_API_KEY no .env"
                )
            # max_retries=0: retrying/failover is the router's job (T-015), not the SDK's.
            self._client = groq.AsyncGroq(
                api_key=self._api_key, timeout=self._timeout, max_retries=0
            )
        return self._client

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools

        try:
            completion = await client.chat.completions.create(**kwargs)
        except groq.GroqError as exc:
            raise self._map_error(exc) from None

        choices = getattr(completion, "choices", None)
        if not choices:
            raise LLMError("Groq: resposta sem 'choices'")
        choice = choices[0]
        raw = choice.message
        message = Message(
            role="assistant",
            content=raw.content or None,
            tool_calls=tuple(_parse_tool_call(tc) for tc in raw.tool_calls or []),
        )
        return LLMResponse(
            message=message,
            model=getattr(completion, "model", None) or self._model,
            provider=self.name,
            usage=_parse_usage(getattr(completion, "usage", None)),
            finish_reason=choice.finish_reason,
        )

    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError("Streaming será implementado na T-014")

    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            await client.models.list()
        except (LLMAuthError, groq.GroqError):
            return False
        return True

    def _map_error(self, exc: groq.GroqError) -> LLMError:
        """Translate SDK exceptions into typed errors.

        Messages are built by us (status code + a redacted excerpt), never from
        ``str(exc)`` verbatim, so the API key cannot leak through an exception.
        """
        # APITimeoutError subclasses APIConnectionError: check it first.
        if isinstance(exc, groq.APITimeoutError):
            return LLMTimeoutError("Groq: timeout")
        if isinstance(exc, groq.APIConnectionError):
            return LLMUnavailableError("Groq indisponível: falha de conexão")
        if isinstance(exc, groq.AuthenticationError | groq.PermissionDeniedError):
            return LLMAuthError(f"Groq: credenciais recusadas (HTTP {exc.status_code})")
        if isinstance(exc, groq.RateLimitError):
            return LLMRateLimitError("Groq: limite de requisições (HTTP 429)")
        if isinstance(exc, groq.InternalServerError):
            return LLMUnavailableError(f"Groq: HTTP {exc.status_code}")
        if isinstance(exc, groq.APIStatusError):
            detail = self._redact(str(exc.message))[:200]
            return LLMError(f"Groq: HTTP {exc.status_code}: {detail}")
        return LLMError(f"Groq: {type(exc).__name__}")

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text


def _parse_tool_call(raw: Any) -> ToolCall:
    fn = raw.function
    if not getattr(fn, "name", None):
        raise LLMError("Groq: tool_call sem nome")
    # Groq returns arguments as a JSON *string*; the model may emit it empty.
    text = fn.arguments or "{}"
    try:
        args = json.loads(text)
    except ValueError as exc:
        raise LLMError(f"Groq: argumentos de tool_call não são JSON válido ({fn.name})") from exc
    if not isinstance(args, dict):
        raise LLMError(f"Groq: argumentos de tool_call inválidos ({fn.name})")
    return ToolCall(id=raw.id or f"call_{uuid.uuid4().hex[:12]}", name=fn.name, arguments=args)


def _parse_usage(usage: Any) -> TokenUsage | None:
    if usage is None:
        return None
    return TokenUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )
