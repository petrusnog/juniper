"""Tests for brain.llm.ollama_provider (no network: httpx.MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from brain.llm.errors import LLMError, LLMTimeoutError, LLMUnavailableError
from brain.llm.ollama_provider import OllamaProvider
from brain.llm.types import Message, ToolCall

Handler = Callable[[httpx.Request], httpx.Response]


def make(handler: Handler) -> OllamaProvider:
    return OllamaProvider(
        url="http://ollama.test", model="llama3.1", transport=httpx.MockTransport(handler)
    )


async def test_chat_text() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "llama3.1",
                "message": {"role": "assistant", "content": "olá!"},
                "done_reason": "stop",
                "prompt_eval_count": 10,
                "eval_count": 5,
            },
        )

    resp = await make(handler).chat([Message("user", "oi")], temperature=0.2, max_tokens=50)

    assert resp.message.content == "olá!"
    assert resp.provider == "ollama"
    assert resp.finish_reason == "stop"
    assert resp.usage is not None and resp.usage.total_tokens == 15
    assert seen["url"] == "http://ollama.test/api/chat"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.2, "num_predict": 50}
    assert "tools" not in body


async def test_chat_tool_call_generates_id() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert "tools" in json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "open_app", "arguments": {"target": "firefox"}}}
                    ],
                }
            },
        )

    schema = {"type": "function", "function": {"name": "open_app", "parameters": {}}}
    resp = await make(handler).chat([Message("user", "abre")], tools=[schema])

    assert resp.message.content is None
    (call,) = resp.message.tool_calls
    assert call.name == "open_app"
    assert call.arguments == {"target": "firefox"}
    assert call.id.startswith("call_")
    assert resp.usage is None


async def test_request_serializes_tool_history_for_ollama() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["messages"] = json.loads(req.content)["messages"]
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"}})

    tc = ToolCall(id="c1", name="open_app", arguments={"target": "firefox"})
    history = [
        Message("user", "abre"),
        Message("assistant", None, tool_calls=(tc,)),
        Message("tool", "ok", tool_call_id="c1", name="open_app"),
    ]
    await make(handler).chat(history)

    msgs = seen["messages"]
    assert isinstance(msgs, list)
    assert msgs[1]["tool_calls"][0]["function"]["arguments"] == {"target": "firefox"}
    assert msgs[1]["content"] == ""
    assert msgs[2]["tool_name"] == "open_app"


async def test_connect_error_is_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailableError):
        await make(handler).chat([Message("user", "oi")])


async def test_timeout_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMTimeoutError):
        await make(handler).chat([Message("user", "oi")])


async def test_5xx_is_unavailable() -> None:
    with pytest.raises(LLMUnavailableError):
        await make(lambda r: httpx.Response(503)).chat([Message("user", "oi")])


async def test_4xx_is_generic_llm_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    with pytest.raises(LLMError, match="404") as exc:
        await make(handler).chat([Message("user", "oi")])
    assert not isinstance(exc.value, LLMUnavailableError)


async def test_invalid_json_and_missing_message() -> None:
    with pytest.raises(LLMError):
        await make(lambda r: httpx.Response(200, text="not json")).chat([Message("user", "x")])
    with pytest.raises(LLMError):
        await make(lambda r: httpx.Response(200, json={})).chat([Message("user", "x")])


async def test_health_check() -> None:
    assert await make(lambda r: httpx.Response(200, json={"models": []})).health_check() is True
    assert await make(lambda r: httpx.Response(500)).health_check() is False

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await make(boom).health_check() is False


def test_stream_not_implemented_yet() -> None:
    p = OllamaProvider(url="http://x", model="m")
    with pytest.raises(NotImplementedError):
        p.stream([Message("user", "oi")])
