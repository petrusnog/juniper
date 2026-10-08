"""Tests for brain.llm.groq_provider (no network: fake SDK client)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import groq
import httpx
import pytest

from brain.config import settings
from brain.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from brain.llm.groq_provider import GroqProvider
from brain.llm.types import Message, ToolCall

SECRET = "gsk_super_secret_key"
REQ = httpx.Request("POST", "https://api.groq.test/chat/completions")


def status_error(cls: type[groq.APIStatusError], code: int, body: str = "boom") -> Exception:
    return cls(body, response=httpx.Response(code, request=REQ), body=None)


def completion(
    content: str | None = "olá!",
    tool_calls: list[Any] | None = None,
    usage: Any = "default",
    finish_reason: str = "stop",
) -> SimpleNamespace:
    if usage == "default":
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        model="llama-3.3-70b-versatile",
        choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)],
        usage=usage,
    )


def raw_tool_call(
    arguments: str | None, name: str | None = "open_app", id_: str | None = "c1"
) -> Any:
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=arguments))


class FakeClient:
    """Mimics the slice of AsyncGroq used by the provider."""

    def __init__(
        self, result: Any = None, error: Exception | None = None, stream: FakeStream | None = None
    ) -> None:
        self.stream_obj = stream
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self._result, self._error = result, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(list=self._list)

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        if kwargs.get("stream"):
            return self.stream_obj
        return self._result

    async def _list(self) -> Any:
        if self._error:
            raise self._error
        return self._result

    async def close(self) -> None:
        self.closed = True


def make(client: FakeClient, api_key: str | None = SECRET) -> GroqProvider:
    return GroqProvider(api_key=api_key, model="llama-3.3-70b-versatile", client=client)


async def test_chat_text() -> None:
    client = FakeClient(completion())
    resp = await make(client).chat([Message("user", "oi")], temperature=0.2, max_tokens=50)

    assert resp.message.content == "olá!"
    assert resp.provider == "groq"
    assert resp.finish_reason == "stop"
    assert resp.usage is not None and resp.usage.total_tokens == 15
    (call,) = client.calls
    assert call["model"] == "llama-3.3-70b-versatile"
    assert call["messages"] == [{"role": "user", "content": "oi"}]
    assert call["temperature"] == 0.2
    assert call["max_tokens"] == 50
    assert "tools" not in call


async def test_optional_params_omitted() -> None:
    client = FakeClient(completion())
    await make(client).chat([Message("user", "oi")])
    assert "max_tokens" not in client.calls[0]


async def test_chat_tool_call_parses_json_string() -> None:
    tc = raw_tool_call(json.dumps({"target": "firefox"}))
    client = FakeClient(completion(content="", tool_calls=[tc], finish_reason="tool_calls"))
    schema = {"type": "function", "function": {"name": "open_app", "parameters": {}}}

    resp = await make(client).chat([Message("user", "abre")], tools=[schema])

    assert client.calls[0]["tools"] == [schema]
    assert resp.message.content is None
    (call,) = resp.message.tool_calls
    assert (call.id, call.name, call.arguments) == ("c1", "open_app", {"target": "firefox"})
    assert resp.finish_reason == "tool_calls"


async def test_tool_call_empty_arguments_and_missing_id() -> None:
    client = FakeClient(completion(tool_calls=[raw_tool_call("", id_=None)]))
    resp = await make(client).chat([Message("user", "x")])
    (call,) = resp.message.tool_calls
    assert call.arguments == {}
    assert call.id.startswith("call_")


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '"str"'])
async def test_tool_call_invalid_arguments(bad: str) -> None:
    client = FakeClient(completion(tool_calls=[raw_tool_call(bad)]))
    with pytest.raises(LLMError, match="argumentos"):
        await make(client).chat([Message("user", "x")])


async def test_tool_call_without_name() -> None:
    client = FakeClient(completion(tool_calls=[raw_tool_call("{}", name=None)]))
    with pytest.raises(LLMError, match="sem nome"):
        await make(client).chat([Message("user", "x")])


async def test_request_serializes_tool_history_as_openai() -> None:
    client = FakeClient(completion())
    tc = ToolCall(id="c1", name="open_app", arguments={"target": "firefox"})
    history = [
        Message("user", "abre"),
        Message("assistant", None, tool_calls=(tc,)),
        Message("tool", "ok", tool_call_id="c1", name="open_app"),
    ]
    await make(client).chat(history)

    msgs = client.calls[0]["messages"]
    assert msgs[1]["tool_calls"][0]["function"]["arguments"] == '{"target": "firefox"}'
    assert msgs[2]["tool_call_id"] == "c1"


async def test_usage_none_and_empty_choices() -> None:
    resp = await make(FakeClient(completion(usage=None))).chat([Message("user", "x")])
    assert resp.usage is None
    with pytest.raises(LLMError, match="choices"):
        await make(FakeClient(SimpleNamespace(choices=[]))).chat([Message("user", "x")])


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(groq.AuthenticationError, 401), LLMAuthError),
        (status_error(groq.PermissionDeniedError, 403), LLMAuthError),
        (status_error(groq.RateLimitError, 429), LLMRateLimitError),
        (status_error(groq.InternalServerError, 503), LLMUnavailableError),
        (groq.APITimeoutError(request=REQ), LLMTimeoutError),
        (groq.APIConnectionError(request=REQ), LLMUnavailableError),
    ],
)
async def test_error_mapping(error: Exception, expected: type[LLMError]) -> None:
    with pytest.raises(expected):
        await make(FakeClient(error=error)).chat([Message("user", "x")])


async def test_timeout_is_not_reported_as_unavailable() -> None:
    with pytest.raises(LLMTimeoutError) as exc:
        await make(FakeClient(error=groq.APITimeoutError(request=REQ))).chat([Message("user", "x")])
    assert not isinstance(exc.value, LLMUnavailableError)


async def test_other_4xx_is_generic_llm_error() -> None:
    err = status_error(groq.BadRequestError, 400, "model not found")
    with pytest.raises(LLMError, match="400") as exc:
        await make(FakeClient(error=err)).chat([Message("user", "x")])
    assert type(exc.value) is LLMError


async def test_api_key_never_leaks_in_exceptions() -> None:
    err = status_error(groq.BadRequestError, 400, f"bad request for key {SECRET}")
    with pytest.raises(LLMError) as exc:
        await make(FakeClient(error=err)).chat([Message("user", "x")])
    assert SECRET not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


async def test_missing_key_raises_auth_error_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", None)
    provider = GroqProvider()  # construction must not fail
    with pytest.raises(LLMAuthError, match="JUNIPER_GROQ_API_KEY"):
        await provider.chat([Message("user", "x")])
    assert await provider.health_check() is False


def test_key_and_defaults_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "groq_api_key", SecretStr("from-settings"))
    provider = GroqProvider()
    assert provider._api_key == "from-settings"
    assert provider._model == settings.llm.groq.model
    assert provider._timeout == settings.llm.groq.timeout
    assert "from-settings" not in repr(provider.__dict__.get("_model"))


def test_real_client_is_built_without_sdk_retries() -> None:
    client = GroqProvider(api_key=SECRET, timeout=7)._get_client()
    assert isinstance(client, groq.AsyncGroq)
    assert client.max_retries == 0


async def test_health_check() -> None:
    assert await make(FakeClient(SimpleNamespace(data=[]))).health_check() is True
    assert (
        await make(FakeClient(error=groq.APIConnectionError(request=REQ))).health_check() is False
    )
    assert (
        await make(FakeClient(error=status_error(groq.AuthenticationError, 401))).health_check()
        is False
    )


async def test_aclose() -> None:
    client = FakeClient()
    await make(client).aclose()
    assert client.closed
    await GroqProvider(api_key=None).aclose()  # no client built: no-op


# --- streaming (T-014) ---------------------------------------------------------------


def tc_delta(
    index: int, id_: str | None = None, name: str | None = None, args: str | None = None
) -> Any:
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=id_, function=fn)


def sdk_chunk(
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = None,
    xg_usage: Any = None,
    xg_error: Any = None,
) -> Any:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=None,
        x_groq=SimpleNamespace(usage=xg_usage, error=xg_error),
    )


USAGE = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)


class FakeStream:
    """Async-iterable, async-context-managed stand-in for the SDK's AsyncStream."""

    def __init__(self, chunks: list[Any], error: Exception | None = None) -> None:
        self._chunks, self._error = chunks, error
        self.closed = False

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True

    def __aiter__(self) -> FakeStream:
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            if self._error:
                raise self._error from None
            raise StopAsyncIteration from None


def stream_client(
    chunks: list[Any], error: Exception | None = None, create_error: Exception | None = None
) -> FakeClient:
    return FakeClient(error=create_error, stream=FakeStream(chunks, error))


async def collect(client: FakeClient, **kw: Any) -> list[Any]:
    return [c async for c in make(client).stream([Message("user", "oi")], **kw)]


async def test_stream_text_deltas_and_final_chunk() -> None:
    client = stream_client(
        [
            sdk_chunk(content="ol"),
            sdk_chunk(content="á"),
            sdk_chunk(content="!"),
            sdk_chunk(finish_reason="stop", xg_usage=USAGE),
        ]
    )
    chunks = await collect(client, temperature=0.1, max_tokens=9)

    assert [c.delta for c in chunks] == ["ol", "á", "!", ""]
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None and chunks[-1].usage.total_tokens == 15
    assert all(c.finish_reason is None and c.usage is None for c in chunks[:-1])
    (call,) = client.calls
    assert call["stream"] is True
    assert call["temperature"] == 0.1 and call["max_tokens"] == 9
    assert "stream_options" not in call  # not supported by the SDK
    assert client.stream_obj.closed


async def test_stream_matches_chat() -> None:
    """Acceptance: concatenated deltas == chat() content, same usage and finish_reason."""
    parts = ["Olá, ", "tudo ", "bem?"]
    client = stream_client(
        [*(sdk_chunk(content=p) for p in parts), sdk_chunk(finish_reason="stop", xg_usage=USAGE)]
    )
    client._result = completion(content="".join(parts))

    provider = make(client)
    chunks = [c async for c in provider.stream([Message("user", "oi")])]
    resp = await provider.chat([Message("user", "oi")])

    assert "".join(c.delta for c in chunks) == resp.message.content
    assert chunks[-1].usage == resp.usage
    assert chunks[-1].finish_reason == resp.finish_reason


async def test_stream_accumulates_fragmented_tool_calls() -> None:
    client = stream_client(
        [
            sdk_chunk(tool_calls=[tc_delta(0, "c1", "open_app", "")]),
            sdk_chunk(tool_calls=[tc_delta(0, None, None, '{"target"')]),
            sdk_chunk(tool_calls=[tc_delta(1, "c2", "get_time", "{}")]),
            sdk_chunk(tool_calls=[tc_delta(0, None, None, ': "firefox"}')]),
            sdk_chunk(finish_reason="tool_calls", xg_usage=USAGE),
        ]
    )
    chunks = await collect(client)

    assert [c.delta for c in chunks] == [""]  # tool calls only in the final chunk
    first, second = chunks[-1].tool_calls
    assert (first.id, first.name, first.arguments) == ("c1", "open_app", {"target": "firefox"})
    assert (second.id, second.name, second.arguments) == ("c2", "get_time", {})
    assert chunks[-1].finish_reason == "tool_calls"


async def test_stream_usage_can_come_from_top_level_and_be_missing() -> None:
    top = sdk_chunk(finish_reason="stop")
    top.usage = USAGE
    assert (await collect(stream_client([top])))[-1].usage is not None
    assert (await collect(stream_client([sdk_chunk(finish_reason="stop")])))[-1].usage is None


async def test_stream_skips_chunks_without_choices() -> None:
    empty = SimpleNamespace(choices=[], usage=None, x_groq=None)
    chunks = await collect(
        stream_client([empty, sdk_chunk(content="a"), sdk_chunk(finish_reason="stop")])
    )
    assert [c.delta for c in chunks] == ["a", ""]


async def test_stream_invalid_tool_arguments_and_missing_name() -> None:
    bad_json = [
        sdk_chunk(tool_calls=[tc_delta(0, "c1", "t", "{oops")]),
        sdk_chunk(finish_reason="stop"),
    ]
    with pytest.raises(LLMError, match="argumentos"):
        await collect(stream_client(bad_json))
    no_name = [
        sdk_chunk(tool_calls=[tc_delta(0, "c1", None, "{}")]),
        sdk_chunk(finish_reason="stop"),
    ]
    with pytest.raises(LLMError, match="sem nome"):
        await collect(stream_client(no_name))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(groq.AuthenticationError, 401), LLMAuthError),
        (status_error(groq.RateLimitError, 429), LLMRateLimitError),
        (status_error(groq.InternalServerError, 503), LLMUnavailableError),
        (groq.APITimeoutError(request=REQ), LLMTimeoutError),
        (groq.APIConnectionError(request=REQ), LLMUnavailableError),
    ],
)
async def test_stream_error_when_opening(error: Exception, expected: type[LLMError]) -> None:
    with pytest.raises(expected):
        await collect(stream_client([], create_error=error))


async def test_stream_failure_after_first_chunk_is_typed() -> None:
    client = stream_client(
        [sdk_chunk(content="parcial")], error=groq.APIConnectionError(request=REQ)
    )
    received: list[str] = []
    with pytest.raises(LLMUnavailableError):
        async for c in make(client).stream([Message("user", "oi")]):
            received.append(c.delta)
    assert received == ["parcial"]
    assert client.stream_obj.closed


async def test_stream_truncated_and_error_in_payload() -> None:
    with pytest.raises(LLMUnavailableError, match="interrompido"):
        await collect(stream_client([sdk_chunk(content="a")]))
    with pytest.raises(LLMError, match="meio do stream"):
        await collect(stream_client([sdk_chunk(content="a", xg_error={"message": "x"})]))


async def test_stream_missing_key_is_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", None)
    with pytest.raises(LLMAuthError):
        async for _ in GroqProvider().stream([Message("user", "x")]):
            pass


async def test_stream_error_does_not_leak_key() -> None:
    err = status_error(groq.BadRequestError, 400, f"bad key {SECRET}")
    with pytest.raises(LLMError) as exc:
        await collect(stream_client([], create_error=err))
    assert SECRET not in str(exc.value)
