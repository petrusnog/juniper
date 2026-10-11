"""Tests for brain.llm.router.LLMRouter failover."""

from __future__ import annotations

import logging
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
from brain.llm.router import LLMRouter
from brain.llm.types import LLMResponse, Message, StreamChunk

MSGS = [Message("user", "oi")]


class ScriptedProvider(LLMProvider):
    """Fake provider: optionally fails up front, or after emitting some chunks."""

    def __init__(
        self,
        name: str,
        *,
        error: LLMError | None = None,
        chunks: tuple[str, ...] = ("o", "i"),
        fail_after_chunks: bool = False,
        healthy: bool = True,
    ) -> None:
        self.name = name  # type: ignore[misc]
        self.error = error
        self.chunks = chunks
        self.fail_after_chunks = fail_after_chunks
        self.healthy = healthy
        self.calls = 0

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.error:
            raise self.error
        text = "".join(self.chunks)
        return LLMResponse(Message("assistant", text), model="m", provider=self.name)

    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        if self.error and not self.fail_after_chunks:
            raise self.error
        for c in self.chunks:
            yield StreamChunk(delta=c)
        if self.error:
            raise self.error
        yield StreamChunk(finish_reason="stop")

    async def health_check(self) -> bool:
        return self.healthy


async def collect(router: LLMRouter) -> str:
    return "".join([c.delta async for c in router.stream(MSGS)])


def test_requires_providers() -> None:
    with pytest.raises(ValueError):
        LLMRouter([])


def test_builds_providers_from_priority() -> None:
    router = LLMRouter()
    assert [p.name for p in router._providers] == ["groq", "ollama"]


async def test_chat_first_ok_skips_second() -> None:
    a, b = ScriptedProvider("a"), ScriptedProvider("b")
    resp = await LLMRouter([a, b]).chat(MSGS)
    assert resp.provider == "a"
    assert (a.calls, b.calls) == (1, 0)


@pytest.mark.parametrize(
    "error",
    [LLMUnavailableError("x"), LLMTimeoutError("x"), LLMRateLimitError("x"), LLMAuthError("x")],
)
async def test_chat_fails_over(error: LLMError, caplog: pytest.LogCaptureFixture) -> None:
    a, b = ScriptedProvider("a", error=error), ScriptedProvider("b")
    router = LLMRouter([a, b])
    with caplog.at_level(logging.WARNING):
        resp = await router.chat(MSGS)
    assert resp.provider == "b"
    assert "Failover: a falhou" in caplog.text
    assert router.last_provider == "b"


async def test_chat_all_fail_aggregates_causes() -> None:
    a = ScriptedProvider("a", error=LLMRateLimitError("limite"))
    b = ScriptedProvider("b", error=LLMUnavailableError("fora"))
    with pytest.raises(LLMError) as info:
        await LLMRouter([a, b]).chat(MSGS)
    text = str(info.value)
    assert "a: LLMRateLimitError: limite" in text
    assert "b: LLMUnavailableError: fora" in text
    assert isinstance(info.value.__cause__, LLMUnavailableError)


async def test_chat_non_failover_error_propagates() -> None:
    a = ScriptedProvider("a", error=LLMError("requisição inválida"))
    b = ScriptedProvider("b")
    with pytest.raises(LLMError, match="inválida"):
        await LLMRouter([a, b]).chat(MSGS)
    assert b.calls == 0


async def test_stream_first_ok() -> None:
    a, b = ScriptedProvider("a"), ScriptedProvider("b")
    assert await collect(LLMRouter([a, b])) == "oi"
    assert b.calls == 0


async def test_stream_fails_over_before_first_chunk() -> None:
    a = ScriptedProvider("a", error=LLMUnavailableError("fora"))
    b = ScriptedProvider("b", chunks=("o", "k"))
    router = LLMRouter([a, b])
    assert await collect(router) == "ok"
    assert router.last_provider == "b"


async def test_stream_mid_stream_failure_does_not_duplicate() -> None:
    a = ScriptedProvider("a", error=LLMTimeoutError("t"), fail_after_chunks=True)
    b = ScriptedProvider("b")
    router = LLMRouter([a, b])
    received: list[str] = []
    with pytest.raises(LLMTimeoutError):
        async for chunk in router.stream(MSGS):
            received.append(chunk.delta)
    assert received == ["o", "i"]
    assert b.calls == 0


async def test_stream_all_fail() -> None:
    a = ScriptedProvider("a", error=LLMAuthError("k"))
    b = ScriptedProvider("b", error=LLMUnavailableError("fora"))
    with pytest.raises(LLMError, match="Todos os providers falharam"):
        await collect(LLMRouter([a, b]))


async def test_health_check_any_healthy() -> None:
    down, up = ScriptedProvider("a", healthy=False), ScriptedProvider("b")
    assert await LLMRouter([down, up]).health_check() is True
    assert await LLMRouter([down]).health_check() is False
