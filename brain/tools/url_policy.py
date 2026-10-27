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

# A hostname label: letters, digits and inner hyphens only (after IDNA, so always ASCII). Anything
# else in a host ("\\", "%", "_", "*") is read differently by a browser than by ``urlsplit``: a
# "\\" ends the host for a browser, and "%31" is decoded into a digit. Refuse it instead.
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
MAX_HOST_CHARS = 253

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


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_ZERO_PREFIX = bytes(12)  # ::/96, the deprecated IPv4-compatible form
_SIIT_PREFIX = bytes(8) + b"\xff\xff\x00\x00"  # ::ffff:0:0:0/96


def _embedded_v4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address a mapped, compatible, SIIT or NAT64 IPv6 address carries, if any."""
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    raw = addr.packed
    if raw[:12] in (_ZERO_PREFIX, _SIIT_PREFIX) or addr in _NAT64:
        return ipaddress.IPv4Address(raw[12:])
    return None


def _blocked_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = _embedded_v4(addr)
        if embedded is not None and _blocked_address(embedded):
            return True
    elif addr in _TAILSCALE_V4:
        return True
    return not addr.is_global or addr.is_multicast


def _valid_name(host: str) -> bool:
    """Whether ``host`` (ASCII, lowercase) is a plain DNS name: LDH labels, no empty label."""
    return 0 < len(host) <= MAX_HOST_CHARS and all(_LABEL.match(label) for label in host.split("."))


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
    if "\\" in text:  # a browser reads "\\" as "/", urlsplit does not: never trust the parse
        return _deny("o endereço está mal formado")

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
    elif _NUMERIC_HOST.match(ascii_host) or not _valid_name(ascii_host):
        return _deny("o nome do site é inválido")

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
    if not value or _address(value) is not None:
        return None
    try:
        ascii_value = value.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    # At least two labels: a bare "com" would trust every site of a top-level domain.
    if "." not in ascii_value or not _valid_name(ascii_value) or _NUMERIC_HOST.match(ascii_value):
        return None
    return ascii_value


def is_trusted(host: str, trusted_domains: Iterable[str]) -> bool:
    """Whether ``host`` (already checked) is, or is a subdomain of, a trusted domain.

    Trust never covers IP addresses, and it never lifts the URL policy: call ``check_url`` first.
    """
    if _address(host) is not None or not _valid_name(host):
        return False
    for domain in trusted_domains:
        wanted = normalise_domain(domain)
        if wanted is not None and (host == wanted or host.endswith(f".{wanted}")):
            return True
    return False
