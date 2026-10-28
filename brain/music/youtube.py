"""`YouTubeProvider`: music search on the official YouTube Data API v3 (ADR-024).

`GET /youtube/v3/search` with the music category and an API key (no user login needed: playing a
track only needs its video id). The key travels in the ``x-goog-api-key`` header, never in the URL,
so it cannot leak through a logged URL or an error message. Results are ranked with a small,
deterministic heuristic (official "Artist - Topic" audio first, covers/live/karaoke down unless the
user asked for them); the final choice is the LLM's, which sees the list.

Every expected failure is a typed `MusicError`; nothing here puts the key or the request URL in a
message.
"""

from __future__ import annotations

import html
import re
from typing import Any, Final

import httpx

from brain.config import settings
from brain.httplog import logged_transport
from brain.music.base import (
    MAX_TRACKS_PER_CALL,
    MusicError,
    MusicProvider,
    Playlist,
    SearchMode,
    Track,
    clean_video_ids,
    is_playlist_id,
    radio_url,
    track_url,
    valid_title,
)
from brain.music.oauth import LOGIN_HINT, TokenProvider
from brain.music.quota import COST_READ, COST_SEARCH, COST_WRITE, QuotaTracker

BASE_URL: Final = "https://www.googleapis.com/youtube/v3"
MUSIC_CATEGORY: Final = "10"
MAX_QUERY_CHARS: Final = 120
MAX_RESULTS: Final = 5
_FETCH: Final = (
    10  # fetched per search (same cost as fewer), so ranking has something to choose from
)

_TOPIC_SUFFIX = re.compile(r"\s*-\s*Topic\s*$", re.IGNORECASE)
# Versions the user rarely means when they ask for a song; kept if the query mentions them.
_UNWANTED = (
    "live",
    "ao vivo",
    "cover",
    "karaoke",
    "karaokê",
    "reaction",
    "remix",
    "slowed",
    "nightcore",
    "full album",
    "playlist",
    "1 hour",
)
_QUOTA_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
}
# Why one track could not be added; the others are still added (that is not a fatal failure).
_SKIPPABLE = {"videoNotFound", "videoAlreadyInPlaylist", "playlistItemsNotAccessible"}
DESCRIPTION: Final = "Criada pela Juniper."


def clean_title(text: str) -> str:
    """The API returns HTML-escaped titles (``&#39;``, ``&amp;``); show them as text."""
    return " ".join(html.unescape(text).split())


def _penalty(title: str, query: str) -> int:
    """How many unwanted markers the title has that the query did not ask for."""
    low_title, low_query = title.lower(), query.lower()
    return sum(1 for word in _UNWANTED if word in low_title and word not in low_query)


def rank(items: list[dict[str, Any]], query: str, limit: int, mode: SearchMode) -> list[Track]:
    """Turn raw ``search.list`` items into at most ``limit`` ranked, de-duplicated tracks."""
    scored: list[tuple[int, int, int, Track]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not isinstance(video_id, str) or video_id in seen or not snippet.get("title"):
            continue
        seen.add(video_id)
        channel = clean_title(str(snippet.get("channelTitle") or ""))
        title = clean_title(str(snippet["title"]))
        official = bool(_TOPIC_SUFFIX.search(channel))
        track = Track(
            video_id=video_id,
            title=title,
            artist=_TOPIC_SUFFIX.sub("", channel) or "?",
            url=radio_url(video_id) if mode == "radio" else track_url(video_id),
            kind="official" if official else "other",
        )
        scored.append((_penalty(title, query), 0 if official else 1, index, track))
    scored.sort(key=lambda row: row[:3])
    return [row[3] for row in scored[:limit]]


def _reason(response: httpx.Response) -> str:
    try:
        errors = response.json().get("error", {}).get("errors") or []
        return str(errors[0].get("reason", "")) if errors else ""
    except (ValueError, AttributeError, IndexError):
        return ""


class YouTubeProvider(MusicProvider):
    """Searches YouTube for music. The key is read lazily, so building it never fails."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        quota: QuotaTracker | None = None,
        region: str | None = None,
        tokens: TokenProvider | None = None,
        timeout: float = 10.0,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        cfg = settings.music
        if api_key is None and settings.youtube_api_key is not None:
            api_key = settings.youtube_api_key.get_secret_value()
        self._api_key = api_key or None
        self._region = region or cfg.region
        self._tokens = tokens
        self._quota = quota or QuotaTracker(cfg.quota_file, cfg.daily_quota_limit)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=logged_transport("youtube", transport),
        )

    @property
    def quota(self) -> QuotaTracker:
        return self._quota

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self, query: str, *, limit: int = 5, mode: SearchMode = "track"
    ) -> list[Track]:
        text = " ".join(str(query).split())
        if not text:
            raise MusicError("invalid_request", "Diga o que procurar (música, artista ou álbum).")
        if len(text) > MAX_QUERY_CHARS:
            raise MusicError("invalid_request", f"A busca passa de {MAX_QUERY_CHARS} caracteres.")
        if self._api_key is None:
            raise MusicError(
                "not_configured",
                "A busca de músicas não está configurada: falta a chave da YouTube Data API.",
            )
        limit = max(1, min(int(limit), MAX_RESULTS))

        self._quota.reserve(COST_SEARCH)
        try:
            response = await self._client.get(
                "/search",
                params={
                    "part": "snippet",
                    "type": "video",
                    "videoCategoryId": MUSIC_CATEGORY,
                    "maxResults": _FETCH,
                    "regionCode": self._region,
                    "q": text,
                },
                headers={"x-goog-api-key": self._api_key},
            )
        except httpx.TimeoutException as exc:
            raise MusicError("network_error", "O YouTube não respondeu a tempo.") from exc
        except httpx.HTTPError as exc:
            raise MusicError("network_error", "Não consegui falar com o YouTube agora.") from exc
        self._check(response)
        try:
            items = response.json().get("items", [])
        except ValueError as exc:
            raise MusicError("api_error", "O YouTube devolveu uma resposta ilegível.") from exc
        return rank(items if isinstance(items, list) else [], text, limit, mode)

    # --- playlists (need the user's login) ------------------------------------------------

    async def _auth_headers(self) -> dict[str, str]:
        if self._tokens is None:
            raise MusicError(
                "not_configured",
                "As playlists precisam do login no YouTube (cliente OAuth não configurado).",
            )
        return {"authorization": f"Bearer {await self._tokens.access_token()}"}

    async def _call(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method, path, params=params, json=body, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise MusicError("network_error", "O YouTube não respondeu a tempo.") from exc
        except httpx.HTTPError as exc:
            raise MusicError("network_error", "Não consegui falar com o YouTube agora.") from exc

    async def list_playlists(self) -> list[Playlist]:
        self._quota.reserve(COST_READ)
        headers = await self._auth_headers()
        response = await self._call(
            "GET",
            "/playlists",
            headers,
            params={"part": "snippet,contentDetails", "mine": "true", "maxResults": 25},
        )
        self._check(response, authed=True)
        try:
            items = response.json().get("items", [])
            return [
                Playlist(
                    playlist_id=str(i["id"]),
                    title=clean_title(i["snippet"]["title"]),
                    track_count=i.get("contentDetails", {}).get("itemCount"),
                )
                for i in items
            ]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise MusicError("api_error", "O YouTube devolveu uma resposta ilegível.") from exc

    async def create_playlist(self, title: str, video_ids: list[str]) -> Playlist:
        name = valid_title(title)
        ids = clean_video_ids(video_ids)
        if name is None:
            raise MusicError(
                "invalid_request", "O título da playlist está vazio, longo demais ou tem < ou >."
            )
        if ids is None or len(ids) > MAX_TRACKS_PER_CALL:
            raise MusicError(
                "invalid_request",
                f"Use de 0 a {MAX_TRACKS_PER_CALL} músicas (ids ou links do YouTube) por playlist.",
            )
        # The whole cost up front: never leave a half-made playlist because the quota ran out.
        self._quota.reserve(COST_WRITE * (1 + len(ids)))
        headers = await self._auth_headers()
        response = await self._call(
            "POST",
            "/playlists",
            headers,
            params={"part": "snippet,status"},
            body={
                "snippet": {"title": name, "description": DESCRIPTION},
                "status": {"privacyStatus": "private"},
            },
        )
        self._check(response, authed=True)
        try:
            playlist_id = str(response.json()["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise MusicError("api_error", "O YouTube devolveu uma resposta ilegível.") from exc
        try:
            added = await self._add(playlist_id, ids, headers)
        except MusicError as exc:
            raise MusicError(
                exc.code,
                f"A playlist «{name}» foi criada, mas não terminei de adicionar as músicas: "
                f"{exc.message}",
            ) from exc
        return Playlist(playlist_id, name, added)

    async def add_to_playlist(self, playlist_id: str, video_ids: list[str]) -> int:
        ids = clean_video_ids(video_ids)
        if not is_playlist_id(playlist_id):
            raise MusicError("invalid_request", "O identificador da playlist é inválido.")
        if not ids or len(ids) > MAX_TRACKS_PER_CALL:
            raise MusicError(
                "invalid_request",
                f"Use de 1 a {MAX_TRACKS_PER_CALL} músicas (ids ou links do YouTube) por vez.",
            )
        self._quota.reserve(COST_WRITE * len(ids))
        return await self._add(playlist_id, ids, await self._auth_headers())

    async def _add(self, playlist_id: str, ids: list[str], headers: dict[str, str]) -> int:
        """Add the tracks one by one, in order. A track that cannot be added is skipped."""
        added = 0
        for video_id in ids:
            response = await self._call(
                "POST",
                "/playlistItems",
                headers,
                params={"part": "snippet"},
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            )
            if response.status_code < 400:
                added += 1
                continue
            if _reason(response) in _SKIPPABLE and response.status_code in (403, 404, 409):
                continue
            self._check(response, authed=True)
        return added

    def _check(self, response: httpx.Response, *, authed: bool = False) -> None:
        if response.status_code < 400:
            return
        reason = _reason(response)
        if response.status_code == 403 and reason in _QUOTA_REASONS:
            self._quota.mark_exhausted()
            raise MusicError("quota_exhausted", self._quota.exhausted_message())
        if response.status_code in (400, 403) and reason in {"keyInvalid", "ipRefererBlocked"}:
            raise MusicError(
                "not_configured",
                "A chave da YouTube Data API foi recusada; confira a configuração.",
            )
        if authed and response.status_code == 401:
            raise MusicError(
                "auth_expired",
                f"O acesso à sua conta do YouTube não vale mais: {LOGIN_HINT} de novo.",
            )
        if authed and reason in {"insufficientPermissions", "forbidden", "authError"}:
            raise MusicError(
                "auth_expired",
                f"Faltam permissões na sua conta do YouTube: {LOGIN_HINT} de novo.",
            )
        if reason in {"youtubeSignupRequired", "channelNotFound"}:
            raise MusicError("api_error", "A conta do Google ainda não tem um canal do YouTube.")
        if reason == "playlistContainsMaximumNumberOfVideos":
            raise MusicError("api_error", "Essa playlist já está cheia.")
        if response.status_code == 404 and reason == "playlistNotFound":
            raise MusicError("invalid_request", "Não encontrei essa playlist na sua conta.")
        if response.status_code in (401, 403):
            raise MusicError("auth_required", "O YouTube recusou a chamada (sem permissão).")
        if response.status_code == 400:
            raise MusicError("invalid_request", "O YouTube não entendeu a busca.")
        raise MusicError("api_error", f"O YouTube respondeu com erro ({response.status_code}).")
