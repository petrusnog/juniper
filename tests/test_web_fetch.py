"""web_fetch no servidor, com a política contra SSRF (T-181, ADR-031)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from brain.config import WebFetchSettings
from brain.tools import web as web_tools
from brain.tools.base import Locality, Tier
from brain.tools.web import UNTRUSTED_OPEN, WebFetch
from brain.web.fetch import Fetcher, FetchError, check_url, page_text, public_address

PUBLIC = "93.184.216.34"
HTML = """<html><head><title>Psicodelia</title><style>x{}</style></head><body>
<nav>menu menu</nav><h1>Psicodelia</h1><p>Movimento dos anos 60.</p>
<script>alert(1)</script><p>Ligado ao rock.</p><footer>rodapé</footer></body></html>"""


def resolver(table: dict[str, list[str]]) -> Any:
    calls: list[str] = []

    async def resolve(host: str, port: int) -> list[str]:
        calls.append(host)
        if host not in table:
            raise OSError("nxdomain")
        return table[host]

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


def fetcher(handler: Any, table: dict[str, list[str]] | None = None, **cfg: Any) -> Fetcher:
    return Fetcher(
        WebFetchSettings(**cfg),
        resolver=resolver(table or {"exemplo.org": [PUBLIC], "outro.org": [PUBLIC]}),
        transport=httpx.MockTransport(handler),
    )


def html(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=HTML, headers={"content-type": "text/html; charset=utf-8"})


# --- the policy ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "100.101.102.103",  # Tailscale
        "169.254.169.254",  # cloud metadata
        "::1",
        "fd00::1",
        "fe80::1",
        "0.0.0.0",
        "224.0.0.1",
        "::ffff:127.0.0.1",
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert not public_address(address)


def test_a_public_address_is_accepted() -> None:
    assert public_address(PUBLIC) and public_address("2606:4700:4700::1111")


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://exemplo.org/x", "bad_scheme"),
        ("file:///etc/passwd", "bad_scheme"),
        ("javascript:alert(1)", "bad_scheme"),
        ("https://user:senha@exemplo.org/", "credentials"),
        ("https://exemplo.org:8443/", "bad_port"),
        ("http://localhost/", "private"),
        ("http://juniper-brain.internal/", "private"),
        ("http://vps.tail1234.ts.net/", "private"),
        ("https:///sem-host", "bad_url"),
        ("http://[::1", "bad_url"),
    ],
)
def test_bad_urls_are_refused_before_any_connection(url: str, code: str) -> None:
    with pytest.raises(FetchError) as caught:
        check_url(url)
    assert caught.value.code == code


async def test_a_name_that_resolves_to_a_private_address_is_refused() -> None:
    called = []
    f = fetcher(lambda r: called.append(r) or html(r), {"exemplo.org": ["10.0.0.7"]})
    with pytest.raises(FetchError) as caught:
        await f.fetch("https://exemplo.org/")
    assert caught.value.code == "private" and called == []


async def test_one_private_answer_among_public_ones_is_enough_to_refuse() -> None:
    f = fetcher(html, {"exemplo.org": [PUBLIC, "127.0.0.1"]})
    with pytest.raises(FetchError):
        await f.fetch("https://exemplo.org/")


async def test_an_ip_literal_is_checked_without_dns() -> None:
    with pytest.raises(FetchError) as caught:
        await fetcher(html).fetch("http://169.254.169.254/latest/meta-data")
    assert caught.value.code == "private"


async def test_a_name_that_does_not_resolve_is_a_typed_failure() -> None:
    with pytest.raises(FetchError) as caught:
        await fetcher(html, {}).fetch("https://nao-existe.org/")
    assert caught.value.code == "dns"


async def test_the_connection_is_pinned_to_the_checked_address_with_host_and_sni() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return html(request)

    await fetcher(handler).fetch("https://exemplo.org/a?b=1#frag")
    (request,) = seen
    assert request.url.host == PUBLIC and request.url.path == "/a" and request.url.query == b"b=1"
    assert request.headers["host"] == "exemplo.org"
    assert request.extensions["sni_hostname"] == "exemplo.org"


# --- redirects -----------------------------------------------------------------------------------


async def test_each_redirect_is_checked_again() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://localhost/admin"})

    with pytest.raises(FetchError) as caught:
        await fetcher(handler).fetch("https://exemplo.org/")
    assert caught.value.code == "private"


async def test_a_redirect_to_a_name_that_resolves_privately_is_refused() -> None:
    table = {"exemplo.org": [PUBLIC], "rebind.org": ["192.168.0.10"]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "https://rebind.org/"})

    with pytest.raises(FetchError) as caught:
        await fetcher(handler, table).fetch("https://exemplo.org/")
    assert caught.value.code == "private"


async def test_a_good_redirect_is_followed_and_the_final_url_is_kept() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "exemplo.org":
            return httpx.Response(302, headers={"location": "https://outro.org/final"})
        return html(request)

    page = await fetcher(handler).fetch("https://exemplo.org/")
    assert page.url == "https://outro.org/final" and page.domain == "outro.org"


async def test_too_many_redirects_stop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/de-novo"})

    with pytest.raises(FetchError) as caught:
        await fetcher(handler, max_redirects=2).fetch("https://exemplo.org/")
    assert caught.value.code == "redirects"


# --- the body ------------------------------------------------------------------------------------


async def test_only_html_and_text_are_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    with pytest.raises(FetchError) as caught:
        await fetcher(handler).fetch("https://exemplo.org/a.pdf")
    assert caught.value.code == "not_text"


async def test_an_error_status_is_a_typed_failure() -> None:
    with pytest.raises(FetchError) as caught:
        await fetcher(lambda r: httpx.Response(404)).fetch("https://exemplo.org/")
    assert caught.value.code == "status" and "404" in caught.value.message


async def test_the_body_stops_at_the_size_limit() -> None:
    big = "<p>" + "a" * 50_000 + "</p>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big, headers={"content-type": "text/html"})

    page = await fetcher(handler, max_bytes=10_000).fetch("https://exemplo.org/")
    assert page.truncated and len(page.text) <= 10_000


async def test_the_text_is_cut_at_max_chars() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 3000, headers={"content-type": "text/plain"})

    page = await fetcher(handler, max_chars=500).fetch("https://exemplo.org/")
    assert page.truncated and len(page.text) == 500


async def test_a_network_failure_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado")

    with pytest.raises(FetchError) as caught:
        await fetcher(handler).fetch("https://exemplo.org/")
    assert caught.value.code == "network"


def test_html_becomes_readable_text_without_scripts_menus_or_footers() -> None:
    title, text = page_text(HTML, "text/html")
    assert title == "Psicodelia"
    assert "Movimento dos anos 60." in text and "Ligado ao rock." in text
    assert "alert" not in text and "menu" not in text and "rodapé" not in text and "x{}" not in text


# --- the tool ------------------------------------------------------------------------------------


def test_web_fetch_only_reads_and_runs_in_the_brain() -> None:
    assert WebFetch.tier is Tier.READ and WebFetch.locality is Locality.CLOUD


async def test_the_tool_wraps_the_page_as_untrusted_and_failures_are_typed() -> None:
    web_tools.bind(None, fetcher(html))
    try:
        ok = await WebFetch().run(url="https://exemplo.org/")
        bad = await WebFetch().run(url="http://127.0.0.1/")
    finally:
        web_tools.bind(None)
    assert ok.success and UNTRUSTED_OPEN in (ok.output or "") and "anos 60" in (ok.output or "")
    assert ok.metadata == {"domain": "exemplo.org", "truncated": False, "learned": False}
    assert not bad.success and bad.metadata == {"code": "private"}
