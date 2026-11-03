"""HTTP reminder payloads: multibyte text, privacy and unchanged authorization boundaries."""

from __future__ import annotations

import asyncio
import http.client
import json
import logging

import pytest

from brain.dashboard.actions import Actions
from brain.dashboard.server import DashboardServer
from brain.dashboard.sources import Sources
from tests.test_dashboard_server import TOKEN, get, login
from tests.test_reminders import DUE
from tests.test_reminders import store as store


@pytest.fixture
async def dashboard(store):
    server = DashboardServer("127.0.0.1", 0, TOKEN, actions=Actions(Sources(reminders=store)))
    assert await server.start()
    try:
        cookie = await login(server)
        _, _, body = await get(server, "/api/me", headers={"Cookie": cookie})
        yield (
            server,
            {
                "Cookie": cookie,
                "X-CSRF-Token": json.loads(body)["csrf"],
                "Content-Type": "application/json",
            },
        )
    finally:
        await server.stop()


async def post(server, path, headers, data):
    body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()

    def request():
        client = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        try:
            client.request("POST", path, body=body, headers=headers)
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    return await asyncio.to_thread(request)


async def test_full_length_multibyte_notes_roundtrip_without_url_or_logs(dashboard, caplog):
    server, headers = dashboard
    notes = "📝漢" * 1000
    with caplog.at_level(logging.DEBUG):
        status, body = await post(
            server,
            "/api/reminders",
            headers,
            {"title": "中文" * 100, "due_at": DUE, "notes": notes},
        )
    assert status == 200
    saved = json.loads(body)["reminder"]
    assert saved["notes"] == notes and len(saved["title"]) == 200
    assert "📝漢" not in caplog.text
    assert "中文" not in caplog.text
    assert all("/api/reminders?" not in rec.message for rec in caplog.records)
    status, body = await post(
        server, f"/api/reminders/{saved['id']}/update", headers, {"notes": ""}
    )
    assert status == 200 and json.loads(body)["reminder"]["notes"] == ""


@pytest.mark.parametrize("change", ["no-login", "no-csrf", "bad-origin"])
async def test_json_writes_still_require_login_csrf_and_origin(dashboard, change):
    server, headers = dashboard
    headers = dict(headers)
    if change == "no-login":
        headers.pop("Cookie")
    elif change == "no-csrf":
        headers.pop("X-CSRF-Token")
    else:
        headers["Origin"] = "https://evil.example"
    status, _ = await post(server, "/api/reminders", headers, {"title": "Private", "due_at": DUE})
    assert status == (401 if change == "no-login" else 403)


@pytest.mark.parametrize(
    "path,data,content_type,status",
    [
        ("/api/reminders?title=ambiguous", {}, "application/json", 400),
        ("/api/reminders", b"{invalid", "application/json", 400),
        ("/api/reminders", {"title": ["bad type"]}, "application/json", 400),
        ("/api/reminders", {}, "text/plain", 415),
        ("/api/reminders", b"x" * 16_385, "application/json", 400),
    ],
)
async def test_malformed_oversized_or_ambiguous_payloads_are_rejected(
    dashboard, path, data, content_type, status
):
    server, headers = dashboard
    result, _ = await post(server, path, {**headers, "Content-Type": content_type}, data)
    assert result == status


@pytest.mark.parametrize(
    "framing",
    [
        "Transfer-Encoding: chunked\r\n",
        "Content-Length: 0\r\nContent-Length: 0\r\n",
        "Content-Length: -1\r\n",
    ],
)
async def test_ambiguous_http_framing_is_not_accepted(dashboard, framing):
    server, _ = dashboard
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        writer.write((f"POST /api/reminders HTTP/1.1\r\nHost: 127.0.0.1\r\n{framing}\r\n").encode())
        await writer.drain()
        assert (await reader.readline()).startswith(b"HTTP/1.1 400")
    finally:
        writer.close()
        await writer.wait_closed()
