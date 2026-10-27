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
