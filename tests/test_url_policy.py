"""Tests for brain.tools.url_policy: the URL policy of ``open_url`` (ADR-014, section 6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from brain.tools.url_policy import (
    MAX_URL_CHARS,
    check_url,
    is_trusted,
    normalise_domain,
)

CASES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "url_policy_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES["urls"], ids=lambda c: repr(c["url"])[:60])
def test_shared_url_cases(case: dict[str, Any]) -> None:
    result = check_url(case["url"])
    assert result.ok is case["allowed"]
    if case["allowed"]:
        assert result.url == case["normalized"]
        assert result.reason is None
    else:
        assert result.url is None
        assert result.reason


@pytest.mark.parametrize("case", CASES["trust"], ids=lambda c: f"{c['host']}-{c['domains']}")
def test_shared_trust_cases(case: dict[str, Any]) -> None:
    assert is_trusted(case["host"], case["domains"]) is case["trusted"]


@pytest.mark.parametrize("value", [None, 5, 1.5, [], {}, b"https://example.com"])
def test_non_string_is_denied_without_raising(value: object) -> None:
    result = check_url(value)
    assert not result.ok
    assert result.reason == "o endereço está vazio"


def test_scheme_is_checked_case_insensitively() -> None:
    assert check_url("HTTP://example.com").ok
    assert not check_url("JavaScript:alert(1)").ok


def test_host_is_lowercased_and_trailing_dot_removed() -> None:
    result = check_url("https://Music.YouTube.COM./x")
    assert result.url == "https://music.youtube.com/x"
    assert result.host == "music.youtube.com"


def test_idn_host_is_compared_in_punycode() -> None:
    result = check_url("https://bücher.example/")
    assert result.host == "xn--bcher-kva.example"


def test_length_limit_is_exact() -> None:
    prefix = "https://example.com/"
    assert check_url(prefix + "a" * (MAX_URL_CHARS - len(prefix))).ok
    assert not check_url(prefix + "a" * (MAX_URL_CHARS - len(prefix) + 1)).ok


def test_port_is_kept_in_the_normalised_url() -> None:
    assert check_url("http://example.com:8080/").url == "http://example.com:8080/"


def test_a_private_address_is_refused_even_with_a_port_and_a_path() -> None:
    result = check_url("http://100.64.0.1:8766/api/state")
    assert not result.ok
    assert result.reason == "endereços locais ou de rede privada não são permitidos"


def test_denied_reasons_are_distinct_for_the_main_rules() -> None:
    reasons = {
        check_url(url).reason
        for url in (
            "",
            "ftp://example.com/",
            "https://u:p@example.com/",
            "http://localhost/",
            "https://example.com:abc/",
            "https://a b.example/",
        )
    }
    assert len(reasons) == 6


class TestTrustedDomains:
    def test_exact_domain_and_subdomain_match(self) -> None:
        assert is_trusted("music.youtube.com", ["music.youtube.com"])
        assert is_trusted("music.youtube.com", ["youtube.com"])

    def test_parent_of_a_trusted_subdomain_is_not_trusted(self) -> None:
        assert not is_trusted("youtube.com", ["music.youtube.com"])

    def test_lookalike_suffix_is_not_trusted(self) -> None:
        assert not is_trusted("evilyoutube.com", ["youtube.com"])
        assert not is_trusted("youtube.com.evil.example", ["youtube.com"])

    def test_config_entries_are_normalised(self) -> None:
        assert is_trusted("music.youtube.com", ["  Music.YouTube.com. "])

    def test_entries_that_are_not_plain_domains_are_ignored(self) -> None:
        for entry in ("https://music.youtube.com/", "music.youtube.com:443", "a@b.example", ""):
            assert normalise_domain(entry) is None
            assert not is_trusted("music.youtube.com", [entry])

    def test_ip_addresses_are_never_trusted(self) -> None:
        assert not is_trusted("8.8.8.8", ["8.8.8.8"])
        assert normalise_domain("8.8.8.8") is None


class TestParserDifferentials:
    """What a browser reads differently from ``urlsplit`` must never reach the trust check."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.com\\.music.youtube.com/",
            "https://127.0.0.1\\.music.youtube.com/",
            "https://example.com/a\\b",
        ],
    )
    def test_backslash_anywhere_is_refused(self, url: str) -> None:
        assert not check_url(url).ok

    @pytest.mark.parametrize("url", ["https://%31%32%37.0.0.1/", "https://localhost%2e/"])
    def test_percent_encoding_in_the_host_is_refused(self, url: str) -> None:
        result = check_url(url)
        assert not result.ok and result.reason == "o nome do site é inválido"

    @pytest.mark.parametrize(
        "host", ["a_b.example", "-a.example", "a-.example", "a..example", "exa*mple.com", "a" * 64]
    )
    def test_only_plain_dns_names_pass(self, host: str) -> None:
        assert not check_url(f"https://{host}/").ok

    def test_a_trusted_suffix_cannot_smuggle_another_host(self) -> None:
        assert not is_trusted("evil.com\\.music.youtube.com", ["music.youtube.com"])
        assert not is_trusted("a_b.music.youtube.com", ["music.youtube.com"])

    def test_a_numeric_looking_name_that_is_not_an_address_is_refused(self) -> None:
        assert not check_url("https://999.999.999.999/").ok


class TestIpv6CarryingIpv4:
    @pytest.mark.parametrize(
        "host",
        [
            "::127.0.0.1",
            "::ffff:127.0.0.1",
            "::ffff:0:127.0.0.1",
            "64:ff9b::7f00:1",
            "::ffff:a00:1",
        ],
    )
    def test_private_ipv4_inside_ipv6_is_refused(self, host: str) -> None:
        assert not check_url(f"http://[{host}]/").ok

    def test_a_public_ipv4_inside_nat64_still_passes(self) -> None:
        assert check_url("https://[64:ff9b::808:808]/").ok


class TestTrustedDomainEntries:
    @pytest.mark.parametrize(
        "entry", ["com", "localhost", "*.youtube.com", "a_b.example", "a b.com"]
    )
    def test_entries_that_would_trust_too_much_or_nothing_are_refused(self, entry: str) -> None:
        assert normalise_domain(entry) is None

    def test_a_bare_top_level_domain_trusts_nothing(self) -> None:
        assert not is_trusted("evil.com", ["com"])


def test_url_policy_does_not_depend_on_the_configuration() -> None:
    """``brain.config`` imports this module: importing it back would be a cycle."""
    import ast

    source = (Path(__file__).parents[1] / "brain" / "tools" / "url_policy.py").read_text()
    modules = {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {m for m in modules if m.startswith("brain")}
