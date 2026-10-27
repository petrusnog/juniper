"""The wire log: every HTTP exchange with a provider or integration lands in the event log."""

from __future__ import annotations

import gzip
import json
from typing import Any

import httplib2
import httpx
import pytest

from brain.config import settings
from brain.eventlog import bind_session, event_log
from brain.httplog import LoggedHttp, LoggingTransport, body_view


def _events(name: str | None = None) -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for path in sorted(event_log.directory.glob("*.jsonl"))
        for line in path.read_text().splitlines()
    ]
    return [r for r in records if name is None or r["event"] == name]


def _raw() -> str:
    return "".join(p.read_text() for p in event_log.directory.glob("*.jsonl"))


class _Wire(httpx.AsyncBaseTransport):
    """Like MockTransport, but the response is a real stream (as a network transport returns)."""

    def __init__(self, handler: Any, extra_headers: dict[str, str] | None = None) -> None:
        self._handler = handler
        self._extra = extra_headers or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        canned = self._handler(request)
        return httpx.Response(
            canned.status_code,
            headers={**canned.headers, **self._extra},
            stream=httpx.ByteStream(canned.content),
        )


def _client(handler: Any, extra_headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    transport = LoggingTransport("fake", _Wire(handler, extra_headers))
    return httpx.AsyncClient(transport=transport, base_url="https://llm.test")


async def test_request_and_response_are_logged_with_bodies_and_timing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "oi"}}]})

    bind_session("sess-1")
    async with _client(handler) as client:
        response = await client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "olá"}]},
            headers={"Authorization": "Bearer abcdefgh12345678"},
        )
        await response.aread()

    (request,) = _events("http_request")
    (reply,) = _events("http_response")
    assert request["service"] == "fake" and request["method"] == "POST"
    assert request["url"] == "https://llm.test/chat"
    assert request["body"] == {"messages": [{"role": "user", "content": "olá"}]}
    assert request["session"] == "sess-1"
    assert request["req"] == reply["req"]
    assert reply["status"] == 200
    assert reply["body"]["choices"][0]["message"]["content"] == "oi"
    assert {"ttfb_ms", "total_ms", "body_bytes"} <= reply.keys()


async def test_secrets_never_reach_the_file() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "ya29.SECRETVALUE"})

    async with _client(handler) as client:
        await client.post(
            "/t",
            content=b"grant_type=jwt&assertion=eyJhbGciOiJSUzI1NiJ9.payload",
            headers={
                "Authorization": "Bearer abcdefgh12345678",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    raw = _raw()
    for secret in ("abcdefgh12345678", "ya29.SECRETVALUE", "eyJhbGciOiJSUzI1NiJ9"):
        assert secret not in raw


async def test_a_streamed_response_is_passed_through_and_logged_whole() -> None:
    sse = b'data: {"a": 1}\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    async with _client(handler) as client, client.stream("POST", "/chat", json={}) as response:
        received = b"".join([chunk async for chunk in response.aiter_bytes()])
    assert received == sse
    (reply,) = _events("http_response")
    assert "[DONE]" in reply["body"] and reply["body_bytes"] == len(sse)


async def test_gzip_bodies_are_logged_decoded() -> None:
    payload = gzip.compress(json.dumps({"ok": True}).encode())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    async with _client(handler, {"content-encoding": "gzip"}) as client:
        response = await client.get("/x")
        assert response.json() == {"ok": True}
    assert _events("http_response")[0]["body"] == {"ok": True}


async def test_a_failed_connection_is_logged_and_still_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/x")
    (error,) = _events("http_error")
    assert error["type"] == "ConnectError" and error["service"] == "fake"


async def test_long_bodies_are_cut_at_the_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.logging, "wire_max_chars", 500)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "x" * 5000})

    async with _client(handler) as client:
        await client.get("/x")
    body = _events("http_response")[0]["body"]  # a cut JSON stays text, with the cut counted
    assert isinstance(body, str) and "chars]" in body and len(body) < 700


async def test_wire_logging_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.logging, "wire", False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        assert (await client.get("/x")).status_code == 200
    assert not event_log.directory.exists() or not list(event_log.directory.glob("*.jsonl"))


def test_body_view_handles_form_json_and_garbage() -> None:
    assert body_view(b'{"a": 1}', "application/json") == {"a": 1}
    assert body_view(b"a=1&b=2", "application/x-www-form-urlencoded") == {"a": "1", "b": "2"}
    assert body_view(b'{"cut": ', "application/json") == '{"cut": '
    assert body_view(b"", "") is None


def test_google_calls_go_through_the_logged_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(self: Any, uri: str, method: str = "GET", **kw: Any) -> Any:
        return httplib2.Response(
            {"status": "200", "content-type": "application/json"}
        ), b'{"items": []}'

    monkeypatch.setattr(httplib2.Http, "request", fake_request)
    http = LoggedHttp("google_calendar")
    response, content = http.request(
        "https://www.googleapis.com/calendar/v3/calendars/x/events",
        "GET",
        headers={"authorization": "Bearer abcdefgh12345678"},
    )
    assert response.status == 200 and content == b'{"items": []}'
    assert _events("http_request")[0]["service"] == "google_calendar"
    assert _events("http_response")[0]["body"] == {"items": []}
    assert "abcdefgh12345678" not in _raw()


async def test_redacted_bodies_keep_the_exchange_but_not_what_was_said() -> None:
    """Voice providers (T-153 review): the log shows sizes and status, never the words."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "transcrição muito privada"})

    transport = LoggingTransport("groq_stt", _Wire(handler), redact_bodies=True)
    async with httpx.AsyncClient(transport=transport, base_url="https://stt.test") as client:
        response = await client.post("/t", json={"input": {"text": "texto secreto do Petrus"}})
        await response.aread()

    (request,) = _events("http_request")
    (reply,) = _events("http_response")
    assert request["service"] == "groq_stt" and reply["status"] == 200
    assert reply["body_bytes"] > 0
    raw = _raw()
    assert "secreto" not in raw and "privada" not in raw
