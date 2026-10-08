"""Tests for brain.llm base types, errors and interface."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from brain.llm.base import LLMProvider
from brain.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from brain.llm.types import LLMResponse, Message, StreamChunk, TokenUsage, ToolCall


class FakeProvider(LLMProvider):
    name = "fake"

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return LLMResponse(Message("assistant", "oi"), model="fake-1", provider=self.name)

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="o")
        yield StreamChunk(delta="i", finish_reason="stop", usage=TokenUsage(total_tokens=2))

    async def health_check(self) -> bool:
        return True


def test_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


@pytest.mark.parametrize(
    "exc", [LLMTimeoutError, LLMAuthError, LLMRateLimitError, LLMUnavailableError]
)
def test_error_hierarchy(exc: type[LLMError]) -> None:
    assert issubclass(exc, LLMError)
    assert issubclass(LLMError, Exception)


async def test_fake_provider_chat() -> None:
    resp = await FakeProvider().chat([Message("user", "olá")])
    assert resp.message.content == "oi"
    assert resp.usage is None


async def test_fake_provider_stream() -> None:
    chunks = [c async for c in FakeProvider().stream([Message("user", "olá")])]
    assert "".join(c.delta for c in chunks) == "oi"
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.prompt_tokens is None


async def test_fake_provider_health() -> None:
    assert await FakeProvider().health_check() is True


def test_message_to_openai_plain() -> None:
    assert Message("user", "oi").to_openai() == {"role": "user", "content": "oi"}


def test_message_to_openai_with_tool_calls() -> None:
    tc = ToolCall(id="c1", name="open_app", arguments={"target": "firefox"})
    data = Message("assistant", None, tool_calls=(tc,)).to_openai()
    call = data["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "open_app"
    assert json.loads(call["function"]["arguments"]) == {"target": "firefox"}


def test_tool_result_message_to_openai() -> None:
    data = Message("tool", "ok", tool_call_id="c1", name="open_app").to_openai()
    assert data == {"role": "tool", "content": "ok", "tool_call_id": "c1", "name": "open_app"}
