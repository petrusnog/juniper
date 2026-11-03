"""Bounded HTTP/1 transport for the dashboard, including small JSON POST bodies.

One request per connection, no chunked transfer or keep-alive. Request contents and
headers are never logged. Routes retain the existing Host, session and CSRF checks.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http.client import HTTPException, parse_headers
from typing import Any

from websockets.datastructures import Headers
from websockets.http11 import Request, Response

MAX_BODY = 16_384
MAX_HEADERS = 32_768
MAX_LINE = 8192
TIMEOUT = 10


@dataclass
class BodyRequest(Request):
    body: bytes = b""


@dataclass
class Peer:
    remote_address: Any


async def read_request(reader: asyncio.StreamReader) -> BodyRequest:
    raw = await reader.readuntil(b"\r\n\r\n")
    if len(raw) > MAX_HEADERS:
        raise ValueError("headers too large")
    lines = raw.split(b"\r\n")
    if any(len(line) > MAX_LINE for line in lines):
        raise ValueError("line too large")
    method, path, version = lines[0].decode("ascii").split(" ")
    if version not in ("HTTP/1.0", "HTTP/1.1"):
        raise ValueError("unsupported version")
    parsed = parse_headers(io.BytesIO(raw[len(lines[0]) + 2 :]))
    if parsed.defects:
        raise ValueError("invalid headers")
    headers = Headers(parsed.raw_items())
    if any(
        len(headers.get_all(name)) > 1
        for name in ("Host", "Authorization", "Cookie", "Origin", "X-CSRF-Token", "Content-Type")
    ):
        raise ValueError("ambiguous security headers")
    if "Transfer-Encoding" in headers or len(headers.get_all("Content-Length")) > 1:
        raise ValueError("ambiguous body framing")
    raw_length = headers.get("Content-Length", "0")
    if not raw_length.isascii() or not raw_length.isdigit():
        raise ValueError("invalid content length")
    length = int(raw_length)
    if not 0 <= length <= MAX_BODY:
        raise ValueError("body too large")
    body = await reader.readexactly(length)
    return BodyRequest(path, headers, method=method, protocol=version, body=body)


async def respond(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    route: Callable[[Peer, Request], Awaitable[Response]],
) -> None:
    try:
        async with asyncio.timeout(TIMEOUT):
            try:
                request = await read_request(reader)
                response = await route(Peer(writer.get_extra_info("peername")), request)
            except (
                ValueError,
                UnicodeError,
                HTTPException,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
            ):
                response = Response(
                    400, "Bad Request", Headers({"Content-Length": "0", "Connection": "close"}), b""
                )
            response.headers["Connection"] = "close"
            writer.write(response.serialize())
            await writer.drain()
    except (TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
