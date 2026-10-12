"""Tests for brain.llm.deepinfra_provider with a mocked HTTP transport (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from brain.llm.deepinfra_provider import BASE_URL, DeepInfraProvider
from brain.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from brain.llm.types import Message

KEY = "di-test-key-0123456789"
MSGS = [Message("system", "persona"), Message("user", "oi")]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "Lista eventos.",
            "parameters": {"type": "object", "properties": {"d": {"type": "string"}}},
        },
    }
]


def provider(
    handler: Callable[[httpx.Request], httpx.Response], api_key: str | None = KEY
) -> DeepInfraProvider:
    return DeepInfraProvider(
        api_key=api_key, model="test/model", transport=httpx.MockTransport(handler)
    )


def completion(content: str | None = "Olá!", tool_calls: list[Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "model": "test/model",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def sse(*events: dict[str, Any] | str) -> httpx.Response:
    body = "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def delta(**fields: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


FINISH = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}


# --- chat ---------------------------------------------------------------------------


async def test_chat_returns_message_usage_and_sends_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=completion())

    resp = await provider(handler).chat(MSGS, max_tokens=50, temperature=0.2)
    assert resp.message.content == "Olá!" and resp.provider == "deepinfra"
    assert resp.finish_reason == "stop" and resp.model == "test/model"
    assert resp.usage is not None and resp.usage.total_tokens == 15

    request = seen[0]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "test/model" and body["stream"] is False
    assert body["max_tokens"] == 50 and body["temperature"] == 0.2
    assert body["messages"][0] == {"role": "system", "content": "persona"}
    assert "tools" not in body


async def test_chat_sends_tools_with_auto_choice() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    await provider(handler).chat(MSGS, tools=TOOLS)
    assert bodies[0]["tools"] == TOOLS and bodies[0]["tool_choice"] == "auto"


async def test_chat_parses_tool_calls_with_json_string_arguments() -> None:
    calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "list_events", "arguments": '{"d": "2026-10-12"}'},
        },
        {"id": "c2", "type": "function", "function": {"name": "list_events", "arguments": ""}},
    ]
    p = provider(lambda r: httpx.Response(200, json=completion(None, calls)))
    resp = await p.chat(MSGS, tools=TOOLS)
    assert resp.message.content is None
    assert [(c.id, c.name, c.arguments) for c in resp.message.tool_calls] == [
        ("c1", "list_events", {"d": "2026-10-12"}),
        ("c2", "list_events", {}),
    ]


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
async def test_chat_rejects_invalid_tool_arguments(arguments: str) -> None:
    call = {"id": "c", "function": {"name": "x", "arguments": arguments}}
    p = provider(lambda r: httpx.Response(200, json=completion(None, [call])))
    with pytest.raises(LLMError, match="argumentos de tool_call"):
        await p.chat(MSGS)


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{"x": 1}]}, ["lista"]])
async def test_chat_rejects_malformed_responses(payload: Any) -> None:
    p = provider(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(LLMError):
        await p.chat(MSGS)


async def test_chat_rejects_non_json_body() -> None:
    p = provider(lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(LLMError, match="JSON"):
        await p.chat(MSGS)


# --- stream ---------------------------------------------------------------------------


async def collect(p: DeepInfraProvider, **kwargs: Any) -> list[Any]:
    return [chunk async for chunk in p.stream(MSGS, **kwargs)]


async def test_stream_emits_deltas_then_final_chunk_with_usage() -> None:
    last = {**FINISH, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    p = provider(lambda r: sse(delta(content="Ol"), delta(content="á!"), last, "[DONE]"))
    chunks = await collect(p)
    assert [c.delta for c in chunks] == ["Ol", "á!", ""]
    final = chunks[-1]
    assert final.finish_reason == "stop" and final.tool_calls == ()
    assert final.usage is not None
    assert (final.usage.prompt_tokens, final.usage.completion_tokens) == (10, 5)
    assert final.usage.total_tokens == 15  # derived when the API omits it


async def test_stream_equals_chat_content() -> None:
    p = provider(lambda r: sse(delta(content="a"), delta(content="b"), FINISH, "[DONE]"))
    assert "".join(c.delta for c in await collect(p)) == "ab"


async def test_stream_usage_in_a_separate_trailing_chunk() -> None:
    trailing = {
        "choices": [],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    p = provider(lambda r: sse(delta(content="x"), FINISH, trailing, "[DONE]"))
    final = (await collect(p))[-1]
    assert final.usage is not None and final.usage.total_tokens == 5


async def test_stream_accumulates_fragmented_tool_calls() -> None:
    def frag(index: int, **fn: str) -> dict[str, Any]:
        entry: dict[str, Any] = {"index": index, "function": fn}
        if "name" in fn:
            entry["id"] = f"call_{index}"
        return delta(tool_calls=[entry])

    p = provider(
        lambda r: sse(
            frag(0, name="list_events", arguments=""),
            frag(0, arguments='{"d": '),
            frag(0, arguments='"2026-10-12"}'),
            frag(1, name="list_events", arguments="{}"),
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            "[DONE]",
        )
    )
    final = (await collect(p, tools=TOOLS))[-1]
    assert final.finish_reason == "tool_calls"
    assert [(c.id, c.name, c.arguments) for c in final.tool_calls] == [
        ("call_0", "list_events", {"d": "2026-10-12"}),
        ("call_1", "list_events", {}),
    ]


async def test_stream_tool_call_without_index_still_assembles() -> None:
    p = provider(
        lambda r: sse(
            delta(
                tool_calls=[{"id": "c", "function": {"name": "list_events", "arguments": '{"d"'}}]
            ),
            delta(tool_calls=[{"function": {"arguments": ': "x"}'}}]),
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            "[DONE]",
        )
    )
    final = (await collect(p))[-1]
    assert len(final.tool_calls) == 1 and final.tool_calls[0].arguments == {"d": "x"}


async def test_stream_sends_stream_flag_and_ignores_sse_noise() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        raw = (
            ": keep-alive\n\nevent: ping\n\n" + "data: " + json.dumps(delta(content="ok")) + "\n\n"
        )
        raw += "data: " + json.dumps(FINISH) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=raw.encode())

    chunks = await collect(provider(handler))
    assert bodies[0]["stream"] is True and chunks[0].delta == "ok"


async def test_stream_error_event_midway() -> None:
    p = provider(lambda r: sse(delta(content="a"), {"error": {"message": "boom"}}))
    with pytest.raises(LLMError, match="erro no meio do stream: boom"):
        await collect(p)


async def test_stream_truncated_before_finish_is_unavailable() -> None:
    p = provider(lambda r: sse(delta(content="a"), "[DONE]"))
    with pytest.raises(LLMUnavailableError, match="interrompido"):
        await collect(p)


async def test_stream_invalid_json_chunk() -> None:
    with pytest.raises(LLMError, match="JSON válido"):
        await collect(provider(lambda r: sse("{not json")))


async def test_stream_http_error_before_first_chunk() -> None:
    p = provider(lambda r: httpx.Response(429, json={"error": {"message": "busy"}}))
    with pytest.raises(LLMRateLimitError):
        await collect(p)


# --- errors ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, LLMAuthError),
        (402, LLMAuthError),  # out of credit: unusable, fail over
        (403, LLMAuthError),
        (429, LLMRateLimitError),
        (500, LLMUnavailableError),
        (503, LLMUnavailableError),
        (400, LLMError),
        (422, LLMError),
    ],
)
async def test_http_status_mapping(status: int, expected: type[LLMError]) -> None:
    p = provider(lambda r: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(expected) as info:
        await p.chat(MSGS)
    assert type(info.value) is expected or (expected is LLMError and type(info.value) is LLMError)


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"message": f"bad key {KEY}"}},
        {"detail": {"error": f"bad key {KEY}"}},
        {"error": f"bad key {KEY}"},
    ],
)
async def test_error_body_shapes_never_leak_the_key(body: dict[str, Any]) -> None:
    p = provider(lambda r: httpx.Response(400, json=body))
    with pytest.raises(LLMError) as info:
        await p.chat(MSGS)
    assert KEY not in str(info.value) and "bad key" in str(info.value)


async def test_auth_and_rate_limit_messages_do_not_contain_the_key() -> None:
    for status in (401, 429):
        p = provider(lambda r, s=status: httpx.Response(s, text=f"key {KEY}"))
        with pytest.raises(LLMError) as info:
            await p.chat(MSGS)
        assert KEY not in str(info.value)


async def test_timeout_and_connection_errors() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusada", request=request)

    with pytest.raises(LLMTimeoutError):
        await provider(timeout).chat(MSGS)
    with pytest.raises(LLMUnavailableError):
        await provider(refused).chat(MSGS)
    with pytest.raises(LLMTimeoutError):
        await collect(provider(timeout))
    with pytest.raises(LLMUnavailableError):
        await collect(provider(refused))


async def test_missing_key_raises_auth_error_without_calling_the_api() -> None:
    called: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(True)
        return httpx.Response(200, json=completion())

    p = provider(handler, api_key=None)
    p._api_key = None  # in case a real key is configured in the environment
    with pytest.raises(LLMAuthError, match="JUNIPER_DEEPINFRA_API_KEY"):
        await p.chat(MSGS)
    with pytest.raises(LLMAuthError):
        await collect(p)
    assert called == []


# --- health ---------------------------------------------------------------------------


async def test_health_check() -> None:
    assert await provider(lambda r: httpx.Response(200, json={"data": []})).health_check() is True
    assert await provider(lambda r: httpx.Response(401)).health_check() is False

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=request)

    assert await provider(down).health_check() is False


async def test_health_check_without_key_does_not_call_the_api() -> None:
    p = provider(lambda r: pytest.fail("não deveria chamar a API"), api_key=None)
    p._api_key = None
    assert await p.health_check() is False


def test_provider_is_registered_and_not_compact() -> None:
    from brain.llm.router import PROVIDERS

    assert PROVIDERS["deepinfra"] is DeepInfraProvider
    assert DeepInfraProvider.compact_prompt is False
