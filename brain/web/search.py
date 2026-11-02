"""Web search with failover: ``SearchProvider`` and its three providers (ADR-031, section 2).

The order comes from ``web.search.providers`` (by default Tavily, then SearXNG, then DuckDuckGo).
The router tries them in turn: a provider that is off, has no key or URL, or spent its monthly
quota is skipped; one that fails (network, HTTP error, unexpected answer) is logged as a failover
and the next one runs. Only when every provider fails does the caller get a ``SearchError``.

What leaves the VPS is the query alone, with secrets masked and its length capped. Results are
data, never instructions: whoever shows them to the LLM wraps them as untrusted content.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import parse_qs, urlsplit

import httpx

from brain.config import WebSearchProviderSettings, WebSearchSettings, settings
from brain.eventlog import event_log
from brain.httplog import logged_transport
from brain.memory.secretfilter import mask_secrets

MAX_QUERY_CHARS: Final = 300
MAX_TITLE_CHARS: Final = 200
MAX_SNIPPET_CHARS: Final = 600
MAX_CONTENT_CHARS: Final = 4000
TAVILY_URL: Final = "https://api.tavily.com/search"
DUCKDUCKGO_URL: Final = "https://html.duckduckgo.com/html/"
USER_AGENT: Final = "Mozilla/5.0 (X11; Linux x86_64) Juniper/1.0"


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str | None = None  # extracted text, when the provider already gives it (Tavily)

    @property
    def domain(self) -> str:
        return (urlsplit(self.url).hostname or "").removeprefix("www.")


class SearchError(Exception):
    """A search that could not be done. ``message`` is safe to show (no URL, no key)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SearchProvider(Protocol):
    name: str

    def usable(self) -> bool: ...

    async def search(self, query: str, limit: int) -> list[SearchResult]: ...


def clean_query(query: str) -> str:
    """The query that may leave the VPS: one line, secrets masked, length capped."""
    return " ".join(mask_secrets(query or "").split())[:MAX_QUERY_CHARS]


def _clip(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _result(title: Any, url: Any, snippet: Any, content: Any = None) -> SearchResult | None:
    link = str(url or "").strip()
    if urlsplit(link).scheme not in ("http", "https") or not urlsplit(link).hostname:
        return None
    body = _clip(content, MAX_CONTENT_CHARS) if content else None
    title_text = _clip(title, MAX_TITLE_CHARS)
    return SearchResult(title_text, link, _clip(snippet, MAX_SNIPPET_CHARS), body)


class _HttpProvider:
    """Shared HTTP plumbing: one ``AsyncClient`` per provider, logged, with a timeout."""

    name = "http"

    def __init__(
        self, *, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=logged_transport(f"web.{self.name}", self._transport),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()


class TavilyProvider(_HttpProvider):
    """Tavily, built for agents: returns the extracted text with each result."""

    name = "tavily"

    def __init__(self, key: str | None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._key = key

    def usable(self) -> bool:
        return bool(self._key)

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        response = await self.client().post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {self._key}"},
            json={"query": query, "max_results": limit, "search_depth": "basic"},
        )
        response.raise_for_status()
        items = response.json().get("results")
        if not isinstance(items, list):
            raise ValueError("tavily: no results list")
        found = (
            _result(i.get("title"), i.get("url"), i.get("content"), i.get("content"))
            for i in items
            if isinstance(i, dict)
        )
        return [r for r in found if r is not None][:limit]


class SearxngProvider(_HttpProvider):
    """A SearXNG of our own (the compose service), with the JSON format turned on."""

    name = "searxng"

    def __init__(self, url: str | None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._url = (url or "").rstrip("/")

    def usable(self) -> bool:
        return bool(self._url)

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        response = await self.client().get(
            f"{self._url}/search", params={"q": query, "format": "json"}
        )
        response.raise_for_status()
        items = response.json().get("results")
        if not isinstance(items, list):
            raise ValueError("searxng: no results list")
        found = (
            _result(i.get("title"), i.get("url"), i.get("content"))
            for i in items
            if isinstance(i, dict)
        )
        return [r for r in found if r is not None][:limit]


class _DuckDuckGoPage(HTMLParser):
    """Reads the results of DuckDuckGo's HTML page (no JavaScript, no key)."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if "result__a" in classes:
            self.results.append({"url": values.get("href") or "", "title": "", "snippet": ""})
            self._field = "title"
        elif "result__snippet" in classes and self.results:
            self._field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._field = None

    def handle_data(self, data: str) -> None:
        if self._field and self.results:
            self.results[-1][self._field] += data


def _duckduckgo_target(href: str) -> str:
    """DuckDuckGo wraps each link in a redirect (``//duckduckgo.com/l/?uddg=<url>``)."""
    parts = urlsplit(href if "://" in href else f"https:{href}")
    if parts.path.startswith("/l/"):
        return parse_qs(parts.query).get("uddg", [""])[0]
    return href


class DuckDuckGoProvider(_HttpProvider):
    """DuckDuckGo's HTML page, read without a library. Fragile by nature: the last resort."""

    name = "duckduckgo"

    def usable(self) -> bool:
        return True

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        response = await self.client().post(DUCKDUCKGO_URL, data={"q": query})
        response.raise_for_status()
        page = _DuckDuckGoPage()
        page.feed(response.text)
        found = (
            _result(r["title"], _duckduckgo_target(r["url"]), r["snippet"]) for r in page.results
        )
        results = [r for r in found if r is not None and r.domain != "duckduckgo.com"]
        if not results and "result__a" not in response.text:
            raise ValueError("duckduckgo: unexpected page (blocked?)")
        return results[:limit]


class MonthlyQuota:
    """Searches per provider per UTC month, in a small JSON file (survives a restart)."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._path = path
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    def _month(self) -> str:
        return self._clock().strftime("%Y-%m")

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("month") != self._month():
            return {}
        used = data.get("used")
        if not isinstance(used, dict):
            return {}
        return {str(k): int(v) for k, v in used.items() if isinstance(v, int) and v >= 0}

    def used(self, provider: str) -> int:
        with self._lock:
            return self._load().get(provider, 0)

    def spend(self, provider: str) -> None:
        with self._lock:
            used = self._load()
            used[provider] = used.get(provider, 0) + 1
            with contextlib.suppress(OSError):  # a lost count never breaks a search
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_name(f"{self._path.name}.tmp")
                tmp.write_text(json.dumps({"month": self._month(), "used": used}), "utf-8")
                tmp.chmod(0o600)
                os.replace(tmp, self._path)


@dataclass
class _Slot:
    provider: SearchProvider
    monthly_limit: int


class SearchRouter:
    """Tries the providers in order; skips the unusable and the spent, falls over on failure."""

    def __init__(
        self,
        providers: list[tuple[SearchProvider, int]],
        quota: MonthlyQuota,
        *,
        max_results: int = 5,
    ) -> None:
        self._slots = [_Slot(p, limit) for p, limit in providers]
        self._quota = quota
        self.max_results = max_results

    @property
    def names(self) -> list[str]:
        return [s.provider.name for s in self._slots]

    def usable(self) -> bool:
        return any(self._ready(s) for s in self._slots)

    def _ready(self, slot: _Slot) -> bool:
        if not slot.provider.usable():
            return False
        limit = slot.monthly_limit
        return limit <= 0 or self._quota.used(slot.provider.name) < limit

    async def search(self, query: str, limit: int | None = None) -> tuple[str, list[SearchResult]]:
        """``(provider that answered, results)``. Raises ``SearchError`` when none could."""
        text = clean_query(query)
        if not text:
            raise SearchError("empty_query", "A busca precisa de um texto.")
        count = max(1, min(limit or self.max_results, self.max_results))
        tried = 0
        for slot in self._slots:
            if not self._ready(slot):
                continue
            tried += 1
            name = slot.provider.name
            started = time.monotonic()
            try:
                results = await slot.provider.search(text, count)
            except Exception as exc:  # noqa: BLE001  (any failure moves on to the next provider)
                event_log.log("web_search_failover", provider=name, reason=type(exc).__name__)
                continue
            if slot.monthly_limit > 0:
                self._quota.spend(name)
            event_log.log(
                "web_search",
                provider=name,
                results=len(results),
                ms=round((time.monotonic() - started) * 1000),
            )
            return name, results
        if not tried:
            raise SearchError("no_provider", "Nenhum provedor de busca está disponível agora.")
        raise SearchError("unavailable", "A busca na web falhou em todos os provedores.")

    async def aclose(self) -> None:
        for slot in self._slots:
            aclose = getattr(slot.provider, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()


def _build_provider(
    item: WebSearchProviderSettings,
    timeout: float,
    tavily_key: str | None,
    searxng_url: str | None = None,
) -> SearchProvider:
    if item.name == "tavily":
        return TavilyProvider(tavily_key, timeout=timeout)
    if item.name == "searxng":
        return SearxngProvider(item.url or searxng_url, timeout=timeout)
    return DuckDuckGoProvider(timeout=timeout)


def build_router(config: WebSearchSettings | None = None) -> SearchRouter:
    """The router from the settings, in the configured order (disabled providers left out)."""
    config = config or settings.web.search
    key = settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None
    providers = [
        (
            _build_provider(item, config.timeout_seconds, key, config.searxng_url or None),
            item.monthly_limit,
        )
        for item in config.providers
        if item.enabled
    ]
    return SearchRouter(providers, MonthlyQuota(config.quota_file), max_results=config.max_results)
