"""The dashboard's HTTP server (ADR-020).

It reuses ``websockets`` (already a dependency of the brain) as an HTTP server: every request is
answered in ``process_request``, so a WebSocket handler is never reached. It is read-only, so only
``GET`` is answered (anything else is a 405), except the user-management routes of
``admin.py`` (ADR-027), which take ``POST`` from a logged-in owner with an anti-CSRF token.
The login sends the credential (``client_id.client_secret``) in a header.

Every response carries security headers; everything but ``/health``, ``/login``, the static
assets and the login call needs the session cookie (or the token as a bearer header); the ``Host``
must look like a private address, so a DNS-rebinding page cannot talk to it. A problem in here is
logged and swallowed: it never reaches the chat server.
"""

from __future__ import annotations

import asyncio
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

from brain.auth import AuthService, Principal
from brain.dashboard.actions import Actions
from brain.dashboard.admin import AdminApi
from brain.dashboard.api import Api, default_api
from brain.dashboard.auth import COOKIE_NAME, SessionSigner, read_cookie
from brain.dashboard.serialize import sanitize
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
        auth: AuthService | None = None,
        actions: Actions | None = None,
        session_hours: int = 12,
        allowed_hosts: list[str] | None = None,
        static_dir: Path = STATIC_DIR,
    ) -> None:
        self.host = host
        self.port = port
        self.api = api or default_api()
        # Sem o serviço de identidade, vale só o token estático (ADR-007), como antes do ADR-027.
        self._auth = auth or AuthService.legacy_only(token)
        self._admin = AdminApi(self._auth)
        self._actions = actions
        self._signer = SessionSigner(self._auth.pepper, session_hours)
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
        writable = request.method == "POST" and self._writer(path) is not None
        if request.method != "GET" and not writable:
            response = self._text(HTTPStatus.METHOD_NOT_ALLOWED, "Somente GET\n")
            response.headers["Allow"] = "GET"
            return response
        if not host_allowed(request.headers.get("Host"), self._allowed):
            return self._text(HTTPStatus.MISDIRECTED_REQUEST, "Host não aceito\n")
        if path == "/health":
            return self._json(200, {"ok": True, "service": "dashboard"})
        if path == "/api/login":
            return await self._login(connection, request)
        if path == "/api/logout":
            return self._json(200, {"ok": True}, extra=[("Set-Cookie", self._cookie("", 0))])
        if path == "/login":
            return self._file("login.html")
        if path.startswith("/assets/"):
            return self._file(path.lstrip("/"))
        who = await self._authorized(connection, request)
        if who is None:
            if path.startswith("/api/"):
                return self._json(401, {"error": "faça login"})
            return self._redirect("/login")
        query = parse_qs(parts.query)
        if path == "/api/me":
            return self._me(request, who)
        writer = self._writer(path)
        if writer is not None and (request.method == "POST" or writer.serves_get):
            return await self._administer(writer, request, path, query, who)
        if path.startswith("/api/"):
            api_path = path.removeprefix("/api")
            if self.api.is_file(api_path):
                return await self._download(api_path, query)
            status, body = await self.api.handle(api_path, query)
            return self._json(status, body)
        if path in ("/", "/index.html"):
            return self._file("index.html")
        return self._text(HTTPStatus.NOT_FOUND, "Não achei\n")

    # --- login --------------------------------------------------------------------------------

    async def _authorized(self, connection: ServerConnection, request: Request) -> Principal | None:
        """Who is on the other end: the cookie's session, or a credential in the bearer header."""
        cookie = read_cookie(request.headers.get("Cookie"))
        session = self._signer.parse(cookie)
        if session is not None:
            who = await asyncio.to_thread(self._auth.principal_for, session.client_id)
            if who is not None and who.fingerprint == session.fingerprint:
                return who
            return None
        bearer = _bearer_value(request)
        if bearer is None:
            return None
        result = await asyncio.to_thread(self._auth.authenticate, bearer, _client(connection))
        return result.principal

    async def _login(self, connection: ServerConnection, request: Request) -> Response:
        result = await asyncio.to_thread(
            self._auth.authenticate, _bearer_value(request), _client(connection)
        )
        if result.reason == "locked":
            event_log.log("dashboard", action="login_blocked")
            return self._json(429, {"error": "muitas tentativas; espere alguns minutos"})
        if result.principal is None:
            event_log.log("dashboard", action="login_failed")
            return self._json(401, {"error": "credenciais incorretas"})
        who = result.principal
        event_log.log("dashboard", action="login", user_id=who.user_id)
        secure = request.headers.get("X-Forwarded-Proto") == "https"
        value = self._signer.issue(who.client_id, who.fingerprint)
        cookie = self._cookie(value, self._signer.max_age, secure)
        return self._json(200, {"ok": True, "user": who.public()}, extra=[("Set-Cookie", cookie)])

    def _me(self, request: Request, who: Principal) -> Response:
        cookie = read_cookie(request.headers.get("Cookie"))
        csrf = self._signer.csrf(cookie) if cookie else None
        # The anti-CSRF token skips the mask: it is a long hex run, which the filter would erase.
        body = {**sanitize({"user": who.public(), "device": who.label}), "csrf": csrf}
        return self._json(200, body)

    def _writer(self, path: str) -> Any | None:
        """The module that answers writes (and its own reads) at ``path``, or ``None``."""
        if self._admin.handles(path):
            return self._admin
        if self._actions is not None and self._actions.handles(path):
            return self._actions
        return None

    async def _administer(
        self,
        writer: Any,
        request: Request,
        path: str,
        query: dict[str, list[str]],
        who: Principal,
    ) -> Response:
        if request.method == "POST":
            refusal = self._write_refused(request)
            if refusal is not None:
                return refusal
        status, body, reveal = await writer.handle(request.method, path, query, who)
        safe = sanitize(body)
        if reveal:  # the one-time client_secret skips the mask that would erase it
            safe = {**safe, **reveal}
        return self._json(status, safe)

    def _write_refused(self, request: Request) -> Response | None:
        """CSRF: a session cookie, its anti-CSRF header, and an Origin (if sent) that is ours."""
        cookie = read_cookie(request.headers.get("Cookie"))
        given = request.headers.get("X-CSRF-Token") or ""
        if not cookie or not hmac.compare_digest(
            given.encode(), self._signer.csrf(cookie).encode()
        ):
            return self._json(403, {"error": "token anti-CSRF ausente ou inválido"})
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != (request.headers.get("Host") or ""):
            return self._json(403, {"error": "origem não aceita"})
        return None

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

    async def _download(self, path: str, query: dict[str, list[str]]) -> Response:
        status, body = await self.api.handle_file(path, query)
        if isinstance(body, dict):
            return self._json(status, body)
        name, kind, content = body
        headers = Headers(
            [
                ("Content-Type", kind),
                ("Content-Disposition", f'attachment; filename="{name}"'),
                ("Cache-Control", "no-store"),
            ]
        )
        return Response(200, "OK", headers, content)

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


def _bearer_value(request: Request) -> str | None:
    value = request.headers.get("Authorization") or ""
    scheme, _, given = value.partition(" ")
    return given.strip() or None if scheme.lower() == "bearer" else None


def _client(connection: ServerConnection) -> str:
    address = connection.remote_address
    return str(address[0]) if isinstance(address, tuple) and address else "?"
