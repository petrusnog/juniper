"""Wire log: every HTTP request and response the brain makes to a provider or integration.

Two thin wrappers feed the event log (``http_request``, ``http_response``, ``http_error``):

- ``LoggingTransport`` wraps an ``httpx`` transport (the LLM providers).
- ``LoggedHttp`` is an ``httplib2.Http`` (the Google API client).

Bodies are parsed and passed through ``redact()`` like every other event; streamed responses are
recorded as they flow, up to a cap, without delaying a single chunk. Logging never breaks a call:
any failure inside the logging code is swallowed.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
import zlib
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import parse_qsl

import httplib2
import httpx

from brain.config import settings
from brain.eventlog import event_log


def _enabled() -> bool:
    return settings.logging.wire and event_log.enabled


def _limit() -> int:
    return settings.logging.wire_max_chars


def _decode(raw: bytes, content_encoding: str) -> bytes | str:
    """Undo gzip/deflate on what was captured (possibly a truncated prefix)."""
    encoding = content_encoding.strip().lower()
    if encoding in ("", "identity"):
        return raw
    if encoding in ("gzip", "x-gzip"):
        wbits = 16 + zlib.MAX_WBITS
    elif encoding == "deflate":
        wbits = zlib.MAX_WBITS
    else:
        return f"<{len(raw)} bytes, content-encoding {encoding}>"
    try:
        return zlib.decompressobj(wbits).decompress(raw)
    except zlib.error:
        return f"<{len(raw)} bytes, undecodable {encoding}>"


def body_view(raw: bytes | str | None, content_type: str = "", content_encoding: str = "") -> Any:
    """A loggable version of a body: parsed JSON/form when possible, text otherwise."""
    if raw is None or raw == b"" or raw == "":
        return None
    if isinstance(raw, bytes):
        decoded = _decode(raw, content_encoding)
        if isinstance(decoded, str):
            return decoded
        text = decoded.decode("utf-8", errors="replace")
    else:
        text = raw
    kind = content_type.lower()
    stripped = text.lstrip()
    if "json" in kind or stripped[:1] in ("{", "["):
        try:
            return json.loads(text)
        except ValueError:
            return text  # a cut stream: keep what we have
    if "x-www-form-urlencoded" in kind:
        return dict(parse_qsl(text, keep_blank_values=True))
    return text


def _headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _log_request(
    req_id: str, service: str, method: str, url: str, headers: dict[str, str], body: Any
) -> None:
    event_log.log(
        "http_request",
        limit=_limit(),
        req=req_id,
        service=service,
        method=method,
        url=url,
        headers=headers,
        body=body,
    )


def _log_response(
    req_id: str,
    service: str,
    status: int,
    headers: dict[str, str],
    body: Any,
    *,
    ttfb_ms: int,
    total_ms: int,
    body_bytes: int,
) -> None:
    event_log.log(
        "http_response",
        limit=_limit(),
        req=req_id,
        service=service,
        status=status,
        ttfb_ms=ttfb_ms,
        total_ms=total_ms,
        body_bytes=body_bytes,
        headers=headers,
        body=body,
    )


def _log_error(req_id: str, service: str, started: float, exc: BaseException) -> None:
    event_log.log(
        "http_error",
        req=req_id,
        service=service,
        total_ms=_ms(started),
        type=type(exc).__name__,
        message=str(exc),
    )


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


# --- httpx (LLM providers) ----------------------------------------------------------------


class _TeeStream(httpx.AsyncByteStream):
    """Passes the response bytes through untouched while keeping a capped copy for the log."""

    def __init__(self, inner: Any, on_done: Any, cap: int) -> None:
        self._inner = inner
        self._on_done = on_done
        self._cap = cap
        self._chunks: list[bytes] = []
        self._size = 0
        self.total = 0
        self._done = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._inner:
                self.total += len(chunk)
                if self._size < self._cap:
                    self._chunks.append(chunk[: self._cap - self._size])
                    self._size += len(self._chunks[-1])
                yield chunk
        finally:
            self._finish()

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()
        finally:
            self._finish()

    def captured(self) -> bytes:
        return b"".join(self._chunks)

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._on_done(self)
        except Exception:  # noqa: BLE001  (logging must never break the call)
            return


class LoggingTransport(httpx.AsyncBaseTransport):
    """Wraps an httpx transport and logs each exchange under ``service`` (e.g. ``deepinfra``)."""

    def __init__(self, service: str, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._service = service
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not _enabled():
            return await self._inner.handle_async_request(request)
        req_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        with contextlib.suppress(Exception):
            headers = _headers(request.headers)
            try:
                content = request.content
            except httpx.RequestNotRead:
                content = None
            _log_request(
                req_id,
                self._service,
                request.method,
                str(request.url),
                headers,
                body_view(content, headers.get("content-type", "")),
            )
        try:
            response = await self._inner.handle_async_request(request)
        except BaseException as exc:
            _log_error(req_id, self._service, started, exc)
            raise
        ttfb = _ms(started)

        def done(tee: _TeeStream) -> None:
            resp_headers = _headers(response.headers)
            _log_response(
                req_id,
                self._service,
                response.status_code,
                resp_headers,
                body_view(
                    tee.captured(),
                    resp_headers.get("content-type", ""),
                    resp_headers.get("content-encoding", ""),
                ),
                ttfb_ms=ttfb,
                total_ms=_ms(started),
                body_bytes=tee.total,
            )

        response.stream = _TeeStream(response.stream, done, _limit() * 4)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def logged_transport(
    service: str, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncBaseTransport:
    """The transport a provider should hand to its ``httpx.AsyncClient``."""
    return LoggingTransport(service, transport)


# --- httplib2 (Google API client) -----------------------------------------------------------


class LoggedHttp(httplib2.Http):
    """An ``httplib2.Http`` that logs each exchange under ``service`` (e.g. ``google_calendar``)."""

    def __init__(self, service: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._log_service = service

    def request(
        self, uri: str, method: str = "GET", body: Any = None, headers: Any = None, **kw: Any
    ) -> tuple[httplib2.Response, bytes]:
        if not _enabled():
            return super().request(uri, method, body=body, headers=headers, **kw)
        req_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        sent = _headers(headers)
        with contextlib.suppress(Exception):
            _log_request(
                req_id,
                self._log_service,
                method,
                uri,
                sent,
                body_view(body, sent.get("content-type", "")),
            )
        try:
            response, content = super().request(uri, method, body=body, headers=headers, **kw)
        except BaseException as exc:
            _log_error(req_id, self._log_service, started, exc)
            raise
        with contextlib.suppress(Exception):
            got = _headers(response)
            total = _ms(started)
            _log_response(
                req_id,
                self._log_service,
                int(response.status),
                got,
                body_view(content, got.get("content-type", "")),
                ttfb_ms=total,
                total_ms=total,
                body_bytes=len(content or b""),
            )
        return response, content
