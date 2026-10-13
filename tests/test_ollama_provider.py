"""Tests for brain.llm.ollama_provider (no network: httpx.MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from brain.llm.errors import LLMError, LLMTimeoutError, LLMUnavailableError
from brain.llm.ollama_provider import OllamaProvider
from brain.llm.types import Message, StreamChunk, ToolCall

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


# --- streaming (T-014) ---------------------------------------------------------------


def ndjson(*objs: dict[str, object] | str) -> bytes:
    return b"".join((o if isinstance(o, str) else json.dumps(o)).encode() + b"\n" for o in objs)


def tok(text: str) -> dict[str, object]:
    return {"message": {"role": "assistant", "content": text}, "done": False}


DONE = {
    "message": {"role": "assistant", "content": ""},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 10,
    "eval_count": 5,
}


async def collect(provider: OllamaProvider, **kw: object) -> list[StreamChunk]:
    return [c async for c in provider.stream([Message("user", "oi")], **kw)]  # type: ignore[arg-type]


async def test_stream_text_deltas_and_final_chunk() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=ndjson(tok("ol"), tok("á"), tok("!"), DONE))

    chunks = await collect(make(handler), temperature=0.1, max_tokens=9)

    assert [c.delta for c in chunks] == ["ol", "á", "!", ""]
    last = chunks[-1]
    assert last.finish_reason == "stop"
    assert last.usage is not None and last.usage.total_tokens == 15
    assert all(c.finish_reason is None and c.usage is None for c in chunks[:-1])
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["stream"] is True
    assert body["options"] == {"temperature": 0.1, "num_predict": 9}


async def test_stream_matches_chat() -> None:
    """Acceptance: concatenated deltas == chat() content, same usage and finish_reason."""
    parts = ["Olá, ", "tudo ", "bem?"]

    def handler(req: httpx.Request) -> httpx.Response:
        if json.loads(req.content)["stream"]:
            return httpx.Response(200, content=ndjson(*(tok(p) for p in parts), DONE))
        return httpx.Response(
            200,
            json={
                "model": "llama3.1",
                "message": {"role": "assistant", "content": "".join(parts)},
                "done_reason": "stop",
                "prompt_eval_count": 10,
                "eval_count": 5,
            },
        )

    provider = make(handler)
    chunks = await collect(provider)
    resp = await provider.chat([Message("user", "oi")])

    assert "".join(c.delta for c in chunks) == resp.message.content
    assert chunks[-1].usage == resp.usage
    assert chunks[-1].finish_reason == resp.finish_reason


async def test_stream_tool_call_is_emitted_complete_in_final_chunk() -> None:
    call = {"function": {"name": "open_app", "arguments": {"target": "firefox"}}}
    lines = ndjson(
        {"message": {"role": "assistant", "content": "", "tool_calls": [call]}, "done": False},
        DONE,
    )
    chunks = await collect(make(lambda r: httpx.Response(200, content=lines)))

    assert [c.delta for c in chunks] == [""]  # no empty delta chunks before the final one
    (tc,) = chunks[-1].tool_calls
    assert (tc.name, tc.arguments) == ("open_app", {"target": "firefox"})
    assert tc.id.startswith("call_")


async def test_stream_ignores_blank_lines_and_missing_usage() -> None:
    body = b"\n" + ndjson(tok("a")) + b"  \n" + ndjson({"done": True})
    chunks = await collect(make(lambda r: httpx.Response(200, content=body)))
    assert [c.delta for c in chunks] == ["a", ""]
    assert chunks[-1].usage is None and chunks[-1].finish_reason is None


async def test_stream_http_errors() -> None:
    with pytest.raises(LLMUnavailableError):
        await collect(make(lambda r: httpx.Response(503)))
    with pytest.raises(LLMError, match="404") as exc:
        await collect(make(lambda r: httpx.Response(404, text="model not found")))
    assert not isinstance(exc.value, LLMUnavailableError)


async def test_stream_connect_error_and_timeout() -> None:
    def refused(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMUnavailableError):
        await collect(make(refused))
    with pytest.raises(LLMTimeoutError):
        await collect(make(slow))


class _BrokenStream(httpx.AsyncByteStream):
    """Yields one NDJSON line and then fails like a dropped connection."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield ndjson(tok("parcial"))
        raise self._error


async def test_stream_failure_after_first_chunk_is_typed() -> None:
    provider = make(lambda r: httpx.Response(200, stream=_BrokenStream(httpx.ReadError("reset"))))
    received: list[str] = []
    with pytest.raises(LLMUnavailableError):
        async for c in provider.stream([Message("user", "oi")]):
            received.append(c.delta)
    assert received == ["parcial"]  # the caller already got the partial text

    provider = make(lambda r: httpx.Response(200, stream=_BrokenStream(httpx.ReadTimeout("t"))))
    with pytest.raises(LLMTimeoutError):
        await collect(provider)


async def test_stream_error_line_truncation_and_garbage() -> None:
    with pytest.raises(LLMError, match="out of memory"):
        await collect(
            make(lambda r: httpx.Response(200, content=ndjson({"error": "out of memory"})))
        )
    with pytest.raises(LLMUnavailableError, match="interrompido"):
        await collect(make(lambda r: httpx.Response(200, content=ndjson(tok("a")))))
    with pytest.raises(LLMError, match="JSON"):
        await collect(make(lambda r: httpx.Response(200, content=b"not json\n")))
    with pytest.raises(LLMError, match="formato"):
        await collect(make(lambda r: httpx.Response(200, content=b"[1]\n")))
