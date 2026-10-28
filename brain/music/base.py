"""The `MusicProvider` interface, the `Track` value and the typed music errors (ADR-024).

Everything the tools know about a music service goes through `MusicProvider`, so the YouTube
Data API can be swapped (for the unofficial `ytmusicapi`, say) without touching a tool. Every
expected failure is a `MusicError` with a stable ``code``; the tools turn it into a
``ToolResult(success=False)`` and never let the raw exception text (which may hold a key) reach
the LLM or the log.
"""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import parse_qs, urlsplit

MUSIC_URL: Final = "https://music.youtube.com/watch"
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
MAX_TRACKS_PER_CALL: Final = 25  # each added track costs 50 quota units
MAX_TITLE_CHARS: Final = 150
_YOUTUBE_HOSTS = frozenset({"music.youtube.com", "www.youtube.com", "youtube.com", "m.youtube.com"})

TrackKind = Literal["official", "other"]
SearchMode = Literal["track", "radio"]

ErrorCode = Literal[
    "quota_exhausted",
    "storage_error",
    "auth_required",
    "auth_expired",
    "not_configured",
    "not_supported",
    "api_error",
    "network_error",
    "invalid_request",
]


class MusicError(Exception):
    """An expected music failure. ``message`` is safe to show (PT-BR, no URL, no key).

    When a playlist call fails *after* it had already done part of the work, ``playlist_id`` and
    ``added`` say what exists, so the tool can tell the LLM and it can finish with
    ``music_playlist_add`` instead of creating a duplicate.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        playlist_id: str | None = None,
        added: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.playlist_id = playlist_id
        self.added = added


@dataclass(frozen=True)
class Track:
    """One search result: enough to choose it and to open it on the phone."""

    video_id: str
    title: str
    artist: str
    url: str
    kind: TrackKind = "other"


@dataclass(frozen=True)
class Playlist:
    playlist_id: str
    title: str
    track_count: int | None = None

    @property
    def url(self) -> str:
        return playlist_url(self.playlist_id)


def is_video_id(value: object) -> bool:
    """Whether ``value`` has the shape of a YouTube video id (11 URL-safe characters)."""
    return isinstance(value, str) and _VIDEO_ID.fullmatch(value) is not None


def is_playlist_id(value: object) -> bool:
    return isinstance(value, str) and _PLAYLIST_ID.fullmatch(value) is not None


def extract_video_id(value: object) -> str | None:
    """A video id from a bare id or from a YouTube / YouTube Music link, or ``None``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if is_video_id(text):
        return text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"}:
        return None
    if host == "youtu.be":
        candidate = parts.path.strip("/")
    elif host in _YOUTUBE_HOSTS:
        candidate = (parse_qs(parts.query).get("v") or [""])[0]
    else:
        return None
    return candidate if is_video_id(candidate) else None


def clean_video_ids(values: object) -> list[str] | None:
    """Video ids from ids or links, order kept, repeats dropped; ``None`` if any one is bad."""
    if not isinstance(values, list):
        return None
    ids: list[str] = []
    for value in values:
        video_id = extract_video_id(value)
        if video_id is None:
            return None
        if video_id not in ids:
            ids.append(video_id)
    return ids


def clean_text(value: str) -> str:
    """``value`` without control, format and direction characters (``\x1b``, U+202E, zero-width...),
    with the whitespace collapsed: nothing that could scramble what the user is asked to approve."""
    kept = "".join(
        " " if c.isspace() else c
        for c in value
        if not unicodedata.category(c).startswith("C") or c.isspace()
    )
    return " ".join(kept.split())


def valid_title(value: object) -> str | None:
    """A playlist title YouTube accepts, or ``None`` (empty, too long, or with ``<`` / ``>``)."""
    if not isinstance(value, str):
        return None
    title = clean_text(value)
    if not title or len(title) > MAX_TITLE_CHARS or "<" in title or ">" in title:
        return None
    return title


def playlist_url(playlist_id: str) -> str:
    return f"https://music.youtube.com/playlist?list={playlist_id}"


def track_url(video_id: str) -> str:
    return f"{MUSIC_URL}?v={video_id}"


def radio_url(video_id: str) -> str:
    """The YouTube Music mix ("radio") link seeded by a track."""
    return f"{MUSIC_URL}?v={video_id}&list=RDAMVM{video_id}"


class MusicProvider(ABC):
    """A music service. Only ``search`` is required; the playlist calls default to "unsupported"."""

    @abstractmethod
    async def search(
        self, query: str, *, limit: int = 5, mode: SearchMode = "track"
    ) -> list[Track]:
        """Tracks matching ``query``, best first, at most ``limit``."""

    async def list_playlists(self) -> list[Playlist]:
        raise MusicError("not_supported", "Este serviço de música não lista playlists.")

    async def create_playlist(self, title: str, video_ids: list[str]) -> Playlist:
        raise MusicError("not_supported", "Este serviço de música não cria playlists.")

    async def add_to_playlist(self, playlist_id: str, video_ids: list[str]) -> int:
        raise MusicError("not_supported", "Este serviço de música não altera playlists.")
