"""URL policy for ``open_url`` (ADR-014, section 6): what the brain may ask a device to open.

The device applies the same rules again (the client revalidates everything), and the cases in
``tests/fixtures/url_policy_cases.json`` keep the two implementations aligned. The policy never
resolves DNS: a public name that points at a private address passes (accepted risk, ADR-014).

Not a tool: ``open_url.py`` uses it, and importing it registers nothing.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

MAX_URL_CHARS = 2048
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Shared Address Space (RFC 6598), where Tailscale hands out its IPv4 addresses. `is_global` is
# already false for it; naming it keeps the intent visible.
_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")

# Forms a browser still reads as an IPv4 address ("2130706433", "0x7f.1", "0177.0.0.1").
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$", re.IGNORECASE)

_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = "/%:@!$&'()*+,;=-._~?"


@dataclass(frozen=True)
class UrlCheck:
    """Outcome of ``check_url``: the normalised URL and host when allowed, else a reason."""

    ok: bool
    url: str | None = None
    host: str | None = None
    reason: str | None = None  # PT-BR, safe to show to the user and the LLM


def _deny(reason: str) -> UrlCheck:
    return UrlCheck(ok=False, reason=reason)


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The IP address ``host`` stands for, or ``None`` if it is a name."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _NUMERIC_HOST.match(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _blocked_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if isinstance(addr, ipaddress.IPv4Address) and addr in _TAILSCALE_V4:
        return True
    return not addr.is_global or addr.is_multicast


def _is_local_name(host: str) -> bool:
    return host == "localhost" or host.endswith(".localhost")


def check_url(url: object) -> UrlCheck:
    """Apply the ADR-014 URL policy. Never raises."""
    if not isinstance(url, str) or not url.strip():
        return _deny("o endereço está vazio")
    text = url.strip()
    if len(text) > MAX_URL_CHARS:
        return _deny(f"o endereço passa de {MAX_URL_CHARS} caracteres")
    if any(unicodedata.category(c).startswith("C") or c.isspace() for c in text):
        return _deny("o endereço tem espaços ou caracteres de controle")

    try:
        parts = urlsplit(text)
        port = parts.port
        host = parts.hostname
    except ValueError:
        return _deny("o endereço está mal formado")

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return _deny("só endereços http e https são permitidos")
    if not parts.netloc or not host:
        return _deny("o endereço não tem um site")
    if "@" in parts.netloc:
        return _deny("endereços com usuário e senha embutidos não são permitidos")

    host = host.rstrip(".")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return _deny("o nome do site é inválido")
    if not ascii_host:
        return _deny("o endereço não tem um site")

    addr = _address(ascii_host)
    if addr is not None:
        if _blocked_address(addr):
            return _deny("endereços locais ou de rede privada não são permitidos")
    elif _is_local_name(ascii_host):
        return _deny("endereços locais ou de rede privada não são permitidos")

    netloc = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    if port is not None:
        netloc = f"{netloc}:{port}"
    normalised = urlunsplit(
        (
            parts.scheme.lower(),
            netloc,
            quote(parts.path, safe=_PATH_SAFE),
            quote(parts.query, safe=_QUERY_SAFE),
            quote(parts.fragment, safe=_QUERY_SAFE),
        )
    )
    return UrlCheck(ok=True, url=normalised, host=ascii_host)


def normalise_domain(domain: str) -> str | None:
    """A configured trusted domain in its ASCII form, or ``None`` if it is not a plain domain."""
    value = domain.strip().lower().rstrip(".")
    if not value or "/" in value or ":" in value or "@" in value or _address(value) is not None:
        return None
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def is_trusted(host: str, trusted_domains: Iterable[str]) -> bool:
    """Whether ``host`` (already checked) is, or is a subdomain of, a trusted domain.

    Trust never covers IP addresses, and it never lifts the URL policy: call ``check_url`` first.
    """
    if _address(host) is not None:
        return False
    for domain in trusted_domains:
        wanted = normalise_domain(domain)
        if wanted is not None and (host == wanted or host.endswith(f".{wanted}")):
            return True
    return False
