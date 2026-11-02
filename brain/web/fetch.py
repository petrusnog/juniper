"""Fetch a web page in the brain, safely, and reduce it to text (ADR-031, section 3).

Unlike ``open_url`` (ADR-014), here the *server* downloads the page, from inside the private
network. So the policy is stricter:

- only ``http`` and ``https``, on ports 80 and 443, without credentials in the URL;
- the brain resolves the name itself and refuses loopback, private, link-local, shared (Tailscale),
  multicast, reserved and cloud metadata addresses; the connection is then **pinned** to the
  checked address (the ``Host`` header and the TLS SNI keep the name, so the certificate is still
  checked against it), so a second DNS answer cannot point it somewhere else (DNS rebinding);
- redirects are followed by hand, at most ``max_redirects``, and each hop is checked again;
- a time limit, a size limit (the body stops being read past ``max_bytes``) and only HTML or text.

Every expected failure is a ``FetchError`` with a message safe to show.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from brain.config import WebFetchSettings, settings
from brain.eventlog import event_log
from brain.web.search import USER_AGENT

ALLOWED_PORTS: Final = frozenset({80, 443})
TEXT_TYPES: Final = ("text/html", "application/xhtml+xml", "text/plain")
REDIRECTS: Final = frozenset({301, 302, 303, 307, 308})
# Tags whose text is never content.
SKIP_TAGS: Final = frozenset(
    {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "template", "iframe"}
)
BLOCK_TAGS: Final = frozenset(
    {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}
)
_METADATA = ipaddress.ip_address("169.254.169.254")

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """A page that could not (or must not) be fetched. ``message`` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Page:
    url: str  # the final URL, after redirects
    title: str
    text: str
    truncated: bool

    @property
    def domain(self) -> str:
        return (urlsplit(self.url).hostname or "").removeprefix("www.")


def public_address(address: str) -> bool:
    """Whether ``address`` is a public internet address (never private, Tailscale or metadata)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and ip != _METADATA


def check_url(url: str) -> tuple[str, str, int]:
    """``(scheme, host, port)`` of a URL the policy accepts, or ``FetchError``."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise FetchError("bad_url", "Esse endereço não é válido.") from exc
    if parts.scheme not in ("http", "https"):
        raise FetchError("bad_scheme", "Só consigo abrir páginas http ou https.")
    if parts.username or parts.password:
        raise FetchError("credentials", "Não abro endereços com usuário ou senha no link.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchError("bad_url", "Esse endereço não tem um site.")
    port = port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise FetchError("bad_port", "Só abro páginas nas portas padrão (80 e 443).")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".ts.net")):
        raise FetchError("private", "Não abro endereços da rede interna.")
    return parts.scheme, host, port


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def safe_address(host: str, port: int, resolver: Resolver) -> str:
    """The address to connect to: every answer of the name must be public, else ``FetchError``."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = await resolver(host, port)
        except OSError as exc:
            raise FetchError("dns", "Não achei esse site (o nome não resolve).") from exc
    if not addresses:
        raise FetchError("dns", "Não achei esse site (o nome não resolve).")
    if not all(public_address(a) for a in addresses):
        raise FetchError("private", "Não abro endereços da rede interna.")
    return addresses[0]


def _pinned(url: str, address: str) -> str:
    """``url`` with the host swapped for the checked address (IPv6 in brackets)."""
    parts = urlsplit(url)
    host = f"[{address}]" if ":" in address else address
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


class _Text(HTMLParser):
    """The readable text of a page and its title; scripts, menus and forms are left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def page_text(body: str, content_type: str) -> tuple[str, str]:
    """``(title, text)`` of a body: HTML is reduced to its readable text; plain text is kept."""
    if "html" not in content_type:
        return "", "\n".join(" ".join(line.split()) for line in body.splitlines() if line.strip())
    parser = _Text()
    parser.feed(body)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return " ".join(parser.title.split()), "\n".join(line for line in lines if line)


class Fetcher:
    """Downloads one page at a time under the policy above."""

    def __init__(
        self,
        config: WebFetchSettings | None = None,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or settings.web.fetch
        self._resolver = resolver
        self._transport = transport

    async def fetch(self, url: str) -> Page:
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                page, size = await self._follow(url)
        except TimeoutError as exc:
            raise FetchError("timeout", "A página demorou demais para responder.") from exc
        except httpx.HTTPError as exc:
            raise FetchError("network", "Não consegui baixar a página.") from exc
        event_log.log(
            "web_fetch",
            domain=page.domain,
            bytes=size,
            truncated=page.truncated,
            ms=round((time.monotonic() - started) * 1000),
        )
        return page

    async def _follow(self, url: str) -> tuple[Page, int]:
        # trust_env=False: a proxy would resolve the name again and undo the pinning.
        async with httpx.AsyncClient(
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
            timeout=self.config.timeout_seconds,
        ) as client:
            current = url.strip()
            for _ in range(self.config.max_redirects + 1):
                scheme, host, port = check_url(current)
                address = await safe_address(host, port, self._resolver)
                request = client.build_request(
                    "GET",
                    _pinned(current, address),
                    headers={
                        "Host": host if port in (80, 443) else f"{host}:{port}",
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html, text/plain;q=0.9",
                    },
                    extensions={"sni_hostname": host} if scheme == "https" else {},
                )
                response = await client.send(request, stream=True)
                try:
                    if response.status_code in REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("bad_redirect", "A página redirecionou para o nada.")
                        current = urljoin(current, location)
                        continue
                    return await self._read(current, response)
                finally:
                    await response.aclose()
        raise FetchError("redirects", "A página redirecionou vezes demais.")

    async def _read(self, url: str, response: httpx.Response) -> tuple[Page, int]:
        if response.status_code != 200:
            raise FetchError("status", f"A página respondeu com o código {response.status_code}.")
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in TEXT_TYPES:
            raise FetchError("not_text", "Essa página não é texto (só leio HTML e texto).")
        body = bytearray()
        truncated = False
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) >= self.config.max_bytes:
                truncated = True
                del body[self.config.max_bytes :]
                break
        text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
        title, content = page_text(text, content_type)
        if len(content) > self.config.max_chars:
            content, truncated = content[: self.config.max_chars], True
        return Page(url, title, content, truncated), len(body)
