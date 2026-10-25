"""Dashboard: servidor, login, cabeçalhos e isolamento (T-130, ADR-020)."""

from __future__ import annotations

import asyncio
import http.client
import os
import re
import signal
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from brain.clients.server import BaseHandler, BrainServer, run_server
from brain.dashboard.api import Api, ApiError, default_api
from brain.dashboard.auth import (
    MAX_FAILURES,
    LoginLimiter,
    SessionSigner,
    read_cookie,
)
from brain.dashboard.server import STATIC_DIR, DashboardServer, host_allowed

TOKEN = "t0ken-de-teste-bem-longo-1234567890"


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def request(
    server: DashboardServer,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
    try:
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


async def get(server: DashboardServer, path: str, **kw: Any) -> tuple[int, dict[str, str], bytes]:
    return await asyncio.to_thread(request, server, path, **kw)


BEARER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def server() -> AsyncIterator[DashboardServer]:
    dash = DashboardServer("127.0.0.1", 0, TOKEN)
    assert await dash.start()
    yield dash
    await dash.stop()


async def login(server: DashboardServer) -> str:
    status, headers, _ = await get(server, "/api/login", headers=BEARER)
    assert status == 200
    return headers["set-cookie"].split(";")[0]


# --- who gets in ---------------------------------------------------------------------------------


async def test_health_and_the_login_page_are_open(server: DashboardServer) -> None:
    status, _, body = await get(server, "/health")
    assert status == 200 and b'"dashboard"' in body
    status, headers, page = await get(server, "/login")
    assert status == 200 and headers["content-type"].startswith("text/html")
    assert b"Token" in page


async def test_everything_else_needs_a_login(server: DashboardServer) -> None:
    status, headers, _ = await get(server, "/")
    assert status == 302 and headers["location"] == "/login"
    status, _, body = await get(server, "/api/session")
    assert status == 401 and b"login" in body
    status, _, _ = await get(server, "/api/session", headers={"Authorization": "Bearer errado"})
    assert status == 401


async def test_the_token_makes_a_cookie_that_opens_the_app(server: DashboardServer) -> None:
    status, headers, _ = await get(server, "/api/login", headers=BEARER)
    cookie = headers["set-cookie"]

    assert status == 200
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # plain HTTP on the tailnet

    session = {"Cookie": cookie.split(";")[0]}
    assert (await get(server, "/api/session", headers=session))[0] == 200
    status, headers, page = await get(server, "/", headers=session)
    assert status == 200 and b"<title>Juniper" in page


async def test_behind_https_the_cookie_is_secure(server: DashboardServer) -> None:
    _, headers, _ = await get(
        server, "/api/login", headers={**BEARER, "X-Forwarded-Proto": "https"}
    )
    assert "; Secure" in headers["set-cookie"]


async def test_the_bearer_token_alone_also_opens_the_api(server: DashboardServer) -> None:
    assert (await get(server, "/api/session", headers=BEARER))[0] == 200


async def test_logout_clears_the_cookie(server: DashboardServer) -> None:
    status, headers, _ = await get(server, "/api/logout")
    assert status == 200 and "Max-Age=0" in headers["set-cookie"]


async def test_the_token_is_never_accepted_in_the_url(server: DashboardServer) -> None:
    status, _, _ = await get(server, f"/api/session?token={TOKEN}")
    assert status == 401
    status, _, _ = await get(server, f"/api/login?token={TOKEN}")
    assert status == 401


async def test_wrong_tokens_are_limited_even_for_the_right_one_afterwards(
    server: DashboardServer,
) -> None:
    for _ in range(MAX_FAILURES):
        status, _, _ = await get(server, "/api/login", headers={"Authorization": "Bearer x"})
        assert status == 401
    status, _, body = await get(server, "/api/login", headers=BEARER)
    assert status == 429 and b"tentativas" in body


# --- the session cookie --------------------------------------------------------------------------


def test_the_cookie_expires_and_cannot_be_forged_or_reused_with_another_token() -> None:
    clock = Clock()
    signer = SessionSigner(TOKEN, hours=1, clock=clock)
    cookie = signer.issue()

    assert signer.valid(cookie)
    clock.now += 3601
    assert not signer.valid(cookie)  # expired

    clock.now = 1_000_000.0
    expires, _, signature = cookie.partition(".")
    for forged in (
        f"{int(expires) + 99999}.{signature}",  # longer life, same signature
        f"{expires}.{'0' * 64}",
        "lixo",
        "",
        None,
        f".{signature}",
    ):
        assert not signer.valid(forged)
    assert not SessionSigner("outro-token", hours=1, clock=clock).valid(cookie)


def test_reading_a_cookie_from_the_header() -> None:
    assert read_cookie("a=1; juniper_session=xyz; b=2") == "xyz"
    assert read_cookie("a=1") is None and read_cookie(None) is None


def test_the_limiter_forgets_after_the_window_and_after_a_success() -> None:
    clock = Clock(0.0)
    limiter = LoginLimiter(clock=clock)
    for _ in range(MAX_FAILURES):
        limiter.failed("1.2.3.4")
    assert limiter.blocked("1.2.3.4") and not limiter.blocked("5.6.7.8")
    clock.now += 301
    assert not limiter.blocked("1.2.3.4")
    limiter.failed("1.2.3.4")
    limiter.succeeded("1.2.3.4")
    assert not limiter._failures


# --- headers and host ----------------------------------------------------------------------------


async def test_every_response_has_the_security_headers(server: DashboardServer) -> None:
    for path in ("/health", "/login", "/", "/api/session", "/nada", "/assets/app.css"):
        _, headers, _ = await get(server, path)
        csp = headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY" and headers["referrer-policy"] == "no-referrer"
        assert "access-control-allow-origin" not in headers  # no CORS


async def test_the_api_is_never_cached(server: DashboardServer) -> None:
    _, headers, _ = await get(server, "/api/session", headers=BEARER)
    assert headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("127.0.0.1:8766", True),
        ("100.101.102.103:8766", True),
        ("[::1]:8766", True),
        ("localhost", True),
        ("juniper.tail1234.ts.net", True),
        ("evil.example.com", False),
        ("evil.example.com:8766", False),
        ("100.1.1.1.evil.com", False),
        ("", False),
        (None, False),
    ],
)
def test_the_host_must_look_private(host: str | None, ok: bool) -> None:
    assert host_allowed(host, []) is ok


def test_extra_hosts_can_be_allowed_by_configuration() -> None:
    assert host_allowed("juniper.casa", ["juniper.casa"])
    assert not host_allowed("juniper.casa", [])


async def test_a_dns_rebinding_host_is_refused(server: DashboardServer) -> None:
    status, _, _ = await get(server, "/health", headers={"Host": "evil.example.com"})
    assert status == 421
    status, _, _ = await get(server, "/api/session", headers={**BEARER, "Host": "evil.example.com"})
    assert status == 421


# --- files ---------------------------------------------------------------------------------------


async def test_static_files_are_served_and_cannot_escape_their_folder(
    server: DashboardServer,
) -> None:
    status, headers, _ = await get(server, "/assets/app.css")
    assert status == 200 and headers["content-type"].startswith("text/css")
    for path in (
        "/assets/../server.py",
        "/assets/%2e%2e/server.py",
        "/assets/../../config.py",
        "/assets/nao-existe.js",
        "/assets/",
    ):
        assert (await get(server, path))[0] == 404


async def test_a_file_of_an_unknown_type_is_not_served(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "segredo.py").write_text("print(1)")
    dash = DashboardServer("127.0.0.1", 0, TOKEN, static_dir=tmp_path)
    assert await dash.start()
    try:
        assert (await get(dash, "/assets/segredo.py"))[0] == 404
    finally:
        await dash.stop()


def test_the_pages_load_nothing_from_outside() -> None:
    external = re.compile(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//|url\(\s*["']?https?:""")
    for path in STATIC_DIR.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".css", ".js"}:
            assert not external.search(path.read_text()), path
            if path.suffix == ".html":
                text = path.read_text()
                assert "<script>" not in text and " onclick=" not in text  # the CSP allows none


# --- the api -------------------------------------------------------------------------------------


async def test_a_route_answers_with_json_and_an_unknown_one_is_404(
    server: DashboardServer,
) -> None:
    status, headers, body = await get(server, "/api/session", headers=BEARER)
    assert status == 200 and b'"ok": true' in body
    assert headers["content-type"].startswith("application/json")
    assert (await get(server, "/api/inexistente", headers=BEARER))[0] == 404


async def test_a_typed_error_and_a_crash_in_a_route_never_reach_the_connection() -> None:
    api = default_api()

    async def refuse(query: Any) -> Any:
        raise ApiError(400, "pedido ruim")

    async def crash(query: Any) -> Any:
        raise RuntimeError("bug")

    api.route("/refuse", refuse)
    api.route("/crash", crash)
    dash = DashboardServer("127.0.0.1", 0, TOKEN, api)
    assert await dash.start()
    try:
        status, _, body = await get(dash, "/api/refuse", headers=BEARER)
        assert status == 400 and b"pedido ruim" in body
        status, _, body = await get(dash, "/api/crash", headers=BEARER)
        assert status == 500 and b"erro interno" in body and b"bug" not in body
        assert (await get(dash, "/api/session", headers=BEARER))[0] == 200  # still alive
    finally:
        await dash.stop()


async def test_the_query_string_reaches_the_route() -> None:
    api = Api()
    seen: list[Any] = []

    async def echo(query: Any) -> Any:
        seen.append(dict(query))
        return {"n": len(query)}

    api.route("/echo", echo)
    dash = DashboardServer("127.0.0.1", 0, TOKEN, api)
    assert await dash.start()
    try:
        await get(dash, "/api/echo?tipo=fact&tipo=project&q=jazz", headers=BEARER)
    finally:
        await dash.stop()
    assert seen == [{"tipo": ["fact", "project"], "q": ["jazz"]}]


async def test_only_get_is_answered(server: DashboardServer) -> None:
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, headers, _ = await get(server, "/api/session", headers=BEARER, method=method)
        assert status == 405 and headers["allow"] == "GET"
    assert (await get(server, "/health"))[0] == 200


# --- isolation from the chat server --------------------------------------------------------------


async def test_a_busy_port_is_a_warning_not_a_crash() -> None:
    first = DashboardServer("127.0.0.1", 0, TOKEN)
    assert await first.start()
    try:
        second = DashboardServer("127.0.0.1", first.port, TOKEN)
        assert await second.start() is False
    finally:
        await first.stop()


async def test_the_chat_server_runs_even_when_the_dashboard_cannot_start() -> None:
    class Broken:
        started = False
        stopped = False

        async def start(self) -> bool:
            self.started = True
            return False

        async def stop(self) -> None:
            self.stopped = True

    broken = Broken()
    brain = BrainServer("127.0.0.1", 0, TOKEN, BaseHandler())
    task = asyncio.create_task(run_server(brain, broken))
    for _ in range(100):
        if brain.port != 0:
            break
        await asyncio.sleep(0.02)
    assert brain.port != 0 and broken.started  # the chat side is up
    os.kill(os.getpid(), signal.SIGTERM)
    async with asyncio.timeout(3):
        await task
    assert broken.stopped


async def test_run_server_starts_and_stops_a_real_dashboard() -> None:
    brain = BrainServer("127.0.0.1", 0, TOKEN, BaseHandler())
    dash = DashboardServer("127.0.0.1", 0, TOKEN)
    task = asyncio.create_task(run_server(brain, dash))
    for _ in range(100):
        if brain.port != 0 and dash.port != 0:
            break
        await asyncio.sleep(0.02)
    assert (await get(dash, "/health"))[0] == 200
    os.kill(os.getpid(), signal.SIGTERM)
    async with asyncio.timeout(3):
        await task
    with pytest.raises(OSError):
        await asyncio.to_thread(request, dash, "/health")
