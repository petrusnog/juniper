"""DeepInfra provider (primary, cloud), via its OpenAI-compatible HTTP API.

Docs: https://docs.deepinfra.com/chat/overview. ``POST {base}/chat/completions`` with a
Bearer key; streaming is SSE (``data: {...}`` lines ending in ``data: [DONE]``), tool-call
``arguments`` are a JSON *string*, and usage rides on the last chunk. No SDK: ``httpx`` is
already a dependency and the surface is small.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

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

BASE_URL = "https://api.deepinfra.com/v1/openai"


class DeepInfraProvider(LLMProvider):
    """Talks to DeepInfra. Defaults come from ``settings.llm.deepinfra``.

    The API key is resolved lazily: constructing the provider never fails, so the router can
    instantiate every provider in ``llm.priority``. A missing key surfaces as ``LLMAuthError``
    on the first call (and ``health_check`` returns False).
    """

    name: ClassVar[str] = "deepinfra"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        *,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        cfg = settings.llm.deepinfra
        self._model = model or cfg.model
        if api_key is None and settings.deepinfra_api_key is not None:
            api_key = settings.deepinfra_api_key.get_secret_value()
        self._api_key = api_key or None
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout or cfg.timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- API -------------------------------------------------------------------------

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = self._payload(messages, tools, temperature, max_tokens, stream=False)
        try:
            resp = await self._client.post(
                "/chat/completions", json=payload, headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("DeepInfra: timeout em /chat/completions") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"DeepInfra indisponível: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise self._status_error(resp)

        data = self._json(resp)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("DeepInfra: resposta sem 'choices'")
        choice = choices[0]
        raw = choice.get("message")
        if not isinstance(raw, dict):
            raise LLMError("DeepInfra: resposta sem campo 'message'")
        message = Message(
            role="assistant",
            content=raw.get("content") or None,
            tool_calls=tuple(_parse_tool_call(tc) for tc in raw.get("tool_calls") or []),
        )
        return LLMResponse(
            message=message,
            model=str(data.get("model") or self._model),
            provider=self.name,
            usage=_parse_usage(data.get("usage")),
            finish_reason=choice.get("finish_reason"),
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._payload(messages, tools, temperature, max_tokens, stream=True)
        fragments: dict[int, _ToolCallFragment] = {}
        finish_reason: str | None = None
        usage: TokenUsage | None = None

        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=payload, headers=self._headers()
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise self._status_error(resp)
                async for line in resp.aiter_lines():
                    event = _sse_data(line)
                    if event is None:
                        continue
                    if event == "[DONE]":
                        break
                    data = self._decode(event)
                    if data.get("error"):
                        raise LLMError(f"DeepInfra: erro no meio do stream: {self._excerpt(data)}")
                    usage = _parse_usage(data.get("usage")) or usage
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or {}
                        for call in delta.get("tool_calls") or []:
                            index = _fragment_index(call, fragments)
                            fragments.setdefault(index, _ToolCallFragment()).add(call)
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                        if text := delta.get("content"):
                            yield StreamChunk(delta=text)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("DeepInfra: timeout em /chat/completions (stream)") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"DeepInfra indisponível: {type(exc).__name__}") from exc

        if finish_reason is None:
            raise LLMUnavailableError("DeepInfra: stream interrompido antes do fim")
        yield StreamChunk(
            tool_calls=tuple(frag.build() for _, frag in sorted(fragments.items())),
            usage=usage,
            finish_reason=finish_reason,
        )

    async def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            resp = await self._client.get("/models", headers=self._headers())
        except httpx.HTTPError:
            return False
        return resp.is_success

    # --- Helpers ---------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise LLMAuthError(
                "DeepInfra: chave de API ausente. Defina JUNIPER_DEEPINFRA_API_KEY no .env"
            )
        return {"Authorization": f"Bearer {self._api_key}"}

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _status_error(self, resp: httpx.Response) -> LLMError:
        """Typed error for an HTTP failure. Built by us, so the key cannot leak through it."""
        status = resp.status_code
        if status in (401, 402, 403):  # bad/missing key, or no credit left: unusable provider
            return LLMAuthError(f"DeepInfra: credenciais recusadas (HTTP {status})")
        if status == 429:
            return LLMRateLimitError("DeepInfra: limite de requisições (HTTP 429)")
        if status >= 500:
            return LLMUnavailableError(f"DeepInfra: HTTP {status}")
        return LLMError(f"DeepInfra: HTTP {status}: {self._excerpt(self._try_json(resp))}")

    def _excerpt(self, body: Any) -> str:
        """Short, key-free description of an error body (shapes vary: error/detail/message)."""
        text = ""
        if isinstance(body, dict):
            for key in ("error", "detail", "message"):
                value = body.get(key)
                if isinstance(value, dict):
                    value = value.get("message") or value.get("error") or json.dumps(value)
                if value:
                    text = str(value)
                    break
        return self._redact(text or "sem detalhes")[:200]

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    @staticmethod
    def _try_json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return None

    def _json(self, resp: httpx.Response) -> dict[str, Any]:
        data = self._try_json(resp)
        if not isinstance(data, dict):
            raise LLMError("DeepInfra: resposta não é um objeto JSON")
        return data

    @staticmethod
    def _decode(event: str) -> dict[str, Any]:
        try:
            data = json.loads(event)
        except ValueError as exc:
            raise LLMError("DeepInfra: chunk do stream não é JSON válido") from exc
        if not isinstance(data, dict):
            raise LLMError("DeepInfra: chunk do stream inesperado")
        return data


def _sse_data(line: str) -> str | None:
    """Payload of an SSE ``data:`` line; None for blanks, comments and other fields."""
    if not line.startswith("data:"):
        return None
    return line[5:].strip() or None


def _fragment_index(call: dict[str, Any], known: dict[int, Any]) -> int:
    """Slot of a streamed tool-call piece: its ``index``, or a guess if the API omits it."""
    index = call.get("index")
    if isinstance(index, int):
        return index
    # No index: a piece that carries an id starts a new call, otherwise it continues the last.
    if call.get("id") or not known:
        return len(known)
    return max(known)


def _build_tool_call(call_id: str | None, name: str | None, arguments: str | None) -> ToolCall:
    if not name:
        raise LLMError("DeepInfra: tool_call sem nome")
    # Arguments come as a JSON *string*; the model may emit it empty.
    try:
        args = json.loads(arguments or "{}")
    except ValueError as exc:
        raise LLMError(f"DeepInfra: argumentos de tool_call não são JSON válido ({name})") from exc
    if not isinstance(args, dict):
        raise LLMError(f"DeepInfra: argumentos de tool_call inválidos ({name})")
    return ToolCall(id=call_id or f"call_{uuid.uuid4().hex[:12]}", name=name, arguments=args)


def _parse_tool_call(raw: dict[str, Any]) -> ToolCall:
    fn = raw.get("function") or {}
    return _build_tool_call(raw.get("id"), fn.get("name"), fn.get("arguments"))


class _ToolCallFragment:
    """Accumulates one tool call that the API sends in pieces."""

    def __init__(self) -> None:
        self.id: str | None = None
        self.name: str | None = None
        self.arguments = ""

    def add(self, delta: dict[str, Any]) -> None:
        self.id = delta.get("id") or self.id
        fn = delta.get("function") or {}
        self.name = fn.get("name") or self.name
        self.arguments += fn.get("arguments") or ""

    def build(self) -> ToolCall:
        return _build_tool_call(self.id, self.name, self.arguments)


def _parse_usage(usage: Any) -> TokenUsage | None:
    if not isinstance(usage, dict):
        return None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if total is None and isinstance(prompt, int) and isinstance(completion, int):
        total = prompt + completion
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    return TokenUsage(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=total, cached_tokens=cached
    )
