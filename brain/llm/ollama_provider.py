"""Ollama provider (local fallback), via the HTTP ``/api/chat`` endpoint."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import httpx

from brain.config import settings
from brain.llm.base import LLMProvider
from brain.llm.errors import LLMError, LLMTimeoutError, LLMUnavailableError
from brain.llm.types import LLMResponse, Message, StreamChunk, TokenUsage, ToolCall


class OllamaProvider(LLMProvider):
    """Talks to a local Ollama server. Defaults come from ``settings.llm.ollama``."""

    name: ClassVar[str] = "ollama"

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        cfg = settings.llm.ollama
        self._model = model or cfg.model
        self._client = httpx.AsyncClient(
            base_url=(url or cfg.url).rstrip("/"),
            timeout=timeout or cfg.timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = self._payload(messages, tools, temperature, max_tokens, stream=False)

        data = await self._post("/api/chat", payload)
        raw = data.get("message")
        if not isinstance(raw, dict):
            raise LLMError("Ollama: resposta sem campo 'message'")

        message = Message(
            role="assistant",
            content=raw.get("content") or None,
            tool_calls=tuple(_parse_tool_call(tc) for tc in raw.get("tool_calls") or []),
        )
        return LLMResponse(
            message=message,
            model=data.get("model", self._model),
            provider=self.name,
            usage=_parse_usage(data),
            finish_reason=data.get("done_reason"),
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
        tool_calls: list[ToolCall] = []
        done: dict[str, Any] | None = None

        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise _status_error(resp)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = _parse_json_line(line)
                    if data.get("error"):
                        raise LLMError(f"Ollama: {str(data['error'])[:200]}")
                    raw = data.get("message") or {}
                    tool_calls.extend(_parse_tool_call(tc) for tc in raw.get("tool_calls") or [])
                    if delta := raw.get("content") or "":
                        yield StreamChunk(delta=delta)
                    if data.get("done"):
                        done = data
                        break
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Ollama: timeout em /api/chat (stream)") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama indisponível: {exc!r}") from exc

        if done is None:
            raise LLMUnavailableError("Ollama: stream interrompido antes do fim")
        yield StreamChunk(
            tool_calls=tuple(tool_calls),
            usage=_parse_usage(done),
            finish_reason=done.get("done_reason"),
        )

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/api/tags")
        except httpx.HTTPError:
            return False
        return resp.is_success

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_to_ollama(m) for m in messages],
            "stream": stream,
            "options": options,
        }
        if tools:
            payload["tools"] = tools
        return payload

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"Ollama: timeout em {path}") from exc
        except httpx.HTTPError as exc:  # connection refused, reset, etc.
            raise LLMUnavailableError(f"Ollama indisponível: {exc!r}") from exc

        if resp.status_code >= 400:
            raise _status_error(resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError("Ollama: resposta não é JSON válido") from exc
        if not isinstance(data, dict):
            raise LLMError("Ollama: formato de resposta inesperado")
        return data


def _status_error(resp: httpx.Response) -> LLMError:
    if resp.status_code >= 500:
        return LLMUnavailableError(f"Ollama: HTTP {resp.status_code}")
    return LLMError(f"Ollama: HTTP {resp.status_code}: {resp.text[:200]}")


def _parse_json_line(line: str) -> dict[str, Any]:
    try:
        data = json.loads(line)
    except ValueError as exc:
        raise LLMError("Ollama: linha do stream não é JSON válido") from exc
    if not isinstance(data, dict):
        raise LLMError("Ollama: formato de linha inesperado no stream")
    return data


def _to_ollama(msg: Message) -> dict[str, Any]:
    """Ollama expects tool-call arguments as a dict (not a JSON string)."""
    out: dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in msg.tool_calls
        ]
    if msg.role == "tool" and msg.name:
        out["tool_name"] = msg.name
    return out


def _parse_tool_call(raw: dict[str, Any]) -> ToolCall:
    fn = raw.get("function") or {}
    name = fn.get("name")
    if not name:
        raise LLMError("Ollama: tool_call sem nome")
    args = fn.get("arguments") or {}
    if not isinstance(args, dict):
        raise LLMError("Ollama: argumentos de tool_call inválidos")
    return ToolCall(id=raw.get("id") or f"call_{uuid.uuid4().hex[:12]}", name=name, arguments=args)


def _parse_usage(data: dict[str, Any]) -> TokenUsage | None:
    prompt, completion = data.get("prompt_eval_count"), data.get("eval_count")
    if prompt is None and completion is None:
        return None
    total = (prompt or 0) + (completion or 0)
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
