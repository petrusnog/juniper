"""The dashboard's HTTP server (ADR-020).

It reuses ``websockets`` (already a dependency of the brain) as an HTTP server: every request is
answered in ``process_request``, so a WebSocket handler is never reached. It is read-only, so only
``GET`` is answered (anything else is a 405) and the login sends the token in a header.

Every response carries security headers; everything but ``/health``, ``/login``, the static
assets and the login call needs the session cookie (or the token as a bearer header); the ``Host``
must look like a private address, so a DNS-rebinding page cannot talk to it. A problem in here is
logged and swallowed: it never reaches the chat server.
"""

from __future__ import annotations

import contextlib
import hmac
import ipaddress
import json
import logging
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from brain.dashboard.api import Api, default_api
from brain.dashboard.auth import COOKIE_NAME, LoginLimiter, SessionSigner, read_cookie
from brain.eventlog import event_log

logger = logging.getLogger(__name__)

STATIC_DIR: Final = Path(__file__).parent / "static"
MIME: Final = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".woff2": "font/woff2",
}
SECURITY_HEADERS: Final = (
    (
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
OPEN_PATHS: Final = {"/health", "/login", "/api/login"}


def host_allowed(header: str | None, extra: list[str]) -> bool:
    """Whether a ``Host`` header names this machine by an address or a private name."""
    if not header:
        return False
    host = header.strip().lower()
    if host.startswith("["):  # [::1]:8766
        host = host[1 : host.find("]")] if "]" in host else host
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return host == "localhost" or host.endswith(".ts.net") or host in {h.lower() for h in extra}


class DashboardServer:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        api: Api | None = None,
        *,
        session_hours: int = 12,
        allowed_hosts: list[str] | None = None,
        static_dir: Path = STATIC_DIR,
    ) -> None:
        self.host = host
        self.port = port
        self._token = token
        self.api = api or default_api()
        self._signer = SessionSigner(token, session_hours)
        self._limiter = LoginLimiter()
        self._allowed = list(allowed_hosts or [])
        self._static = static_dir.resolve()
        self._server: Server | None = None

    # --- life cycle ---------------------------------------------------------------------------

    async def start(self) -> bool:
        """Open the port. ``False`` (logged) when it cannot: the chat server goes on without it."""
        try:
            self._server = await serve(
                self._no_websocket,
                self.host,
                self.port,
                process_request=self._process_request,
                max_size=2**16,
                ping_interval=None,
            )
        except OSError as exc:
            event_log.log("error", where="dashboard.start", type=type(exc).__name__, port=self.port)
            logger.warning("Dashboard não subiu em %s:%s: %s", self.host, self.port, exc)
            return False
        for socket in self._server.sockets:
            self.port = socket.getsockname()[1]
            break
        event_log.log("dashboard", action="start", host=self.host, port=self.port)
        return True

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close(close_connections=True)
        with contextlib.suppress(Exception):
            await server.wait_closed()
        event_log.log("dashboard", action="stop")

    async def _no_websocket(self, websocket: ServerConnection) -> None:  # pragma: no cover
        await websocket.close(1008, "sem websocket")

    # --- requests -----------------------------------------------------------------------------

    async def _process_request(self, connection: ServerConnection, request: Request) -> Response:
        try:
            response = await self._route(connection, request)
        except Exception as exc:  # noqa: BLE001  (a bug here must not become a crash)
            logger.exception("Dashboard falhou")
            event_log.log("error", where="dashboard.request", type=type(exc).__name__)
            response = self._json(500, {"error": "erro interno"})
        return self._secure(response)

    async def _route(self, connection: ServerConnection, request: Request) -> Response:
        parts = urlsplit(request.path)
        path = parts.path
        if request.method != "GET":
            response = self._text(HTTPStatus.METHOD_NOT_ALLOWED, "Somente GET\n")
            response.headers["Allow"] = "GET"
            return response
        if not host_allowed(request.headers.get("Host"), self._allowed):
            return self._text(HTTPStatus.MISDIRECTED_REQUEST, "Host não aceito\n")
        if path == "/health":
            return self._json(200, {"ok": True, "service": "dashboard"})
        if path == "/api/login":
            return self._login(connection, request)
        if path == "/api/logout":
            return self._json(200, {"ok": True}, extra=[("Set-Cookie", self._cookie("", 0))])
        if path == "/login":
            return self._file("login.html")
        if path.startswith("/assets/"):
            return self._file(path.lstrip("/"))
        if not self._authorized(request):
            if path.startswith("/api/"):
                return self._json(401, {"error": "faça login"})
            return self._redirect("/login")
        if path.startswith("/api/"):
            status, body = await self.api.handle(
                path.removeprefix("/api"), parse_qs(parts.query, keep_blank_values=False)
            )
            return self._json(status, body)
        if path in ("/", "/index.html"):
            return self._file("index.html")
        return self._text(HTTPStatus.NOT_FOUND, "Não achei\n")

    # --- login --------------------------------------------------------------------------------

    def _authorized(self, request: Request) -> bool:
        if self._signer.valid(read_cookie(request.headers.get("Cookie"))):
            return True
        return self._bearer_ok(request)

    def _bearer_ok(self, request: Request) -> bool:
        value = request.headers.get("Authorization") or ""
        scheme, _, given = value.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(
            given.strip().encode(), self._token.encode()
        )

    def _login(self, connection: ServerConnection, request: Request) -> Response:
        client = _client(connection)
        if self._limiter.blocked(client):
            event_log.log("dashboard", action="login_blocked")
            return self._json(429, {"error": "muitas tentativas; espere alguns minutos"})
        if not self._bearer_ok(request):
            self._limiter.failed(client)
            event_log.log("dashboard", action="login_failed")
            return self._json(401, {"error": "token incorreto"})
        self._limiter.succeeded(client)
        event_log.log("dashboard", action="login")
        secure = request.headers.get("X-Forwarded-Proto") == "https"
        cookie = self._cookie(self._signer.issue(), self._signer.max_age, secure)
        return self._json(200, {"ok": True}, extra=[("Set-Cookie", cookie)])

    @staticmethod
    def _cookie(value: str, max_age: int, secure: bool = False) -> str:
        flags = "; HttpOnly; SameSite=Strict; Path=/" + ("; Secure" if secure else "")
        return f"{COOKIE_NAME}={value}; Max-Age={max_age}{flags}"

    # --- responses ----------------------------------------------------------------------------

    def _file(self, relative: str) -> Response:
        target = (self._static / relative).resolve()
        if not target.is_relative_to(self._static) or not target.is_file():
            return self._text(HTTPStatus.NOT_FOUND, "Não achei\n")
        kind = MIME.get(target.suffix.lower())
        if kind is None:
            return self._text(HTTPStatus.NOT_FOUND, "Não achei\n")
        headers = Headers([("Content-Type", kind), ("Cache-Control", "no-cache")])
        return Response(200, "OK", headers, target.read_bytes())

    @staticmethod
    def _json(status: int, body: Any, extra: list[tuple[str, str]] | None = None) -> Response:
        payload = json.dumps(body, ensure_ascii=False, default=str).encode()
        headers = Headers(
            [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")]
        )
        for key, value in extra or []:
            headers[key] = value
        return Response(status, HTTPStatus(status).phrase, headers, payload)

    @staticmethod
    def _text(status: HTTPStatus, body: str) -> Response:
        headers = Headers([("Content-Type", "text/plain; charset=utf-8")])
        return Response(status.value, status.phrase, headers, body.encode())

    @staticmethod
    def _redirect(location: str) -> Response:
        return Response(302, "Found", Headers([("Location", location)]), b"")

    @staticmethod
    def _secure(response: Response) -> Response:
        for key, value in SECURITY_HEADERS:
            response.headers[key] = value
        return response


def _client(connection: ServerConnection) -> str:
    address = connection.remote_address
    return str(address[0]) if isinstance(address, tuple) and address else "?"
