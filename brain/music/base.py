"""The `MusicProvider` interface, the `Track` value and the typed music errors (ADR-024).

Everything the tools know about a music service goes through `MusicProvider`, so the YouTube
Data API can be swapped (for the unofficial `ytmusicapi`, say) without touching a tool. Every
expected failure is a `MusicError` with a stable ``code``; the tools turn it into a
``ToolResult(success=False)`` and never let the raw exception text (which may hold a key) reach
the LLM or the log.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final, Literal

MUSIC_URL: Final = "https://music.youtube.com/watch"
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

TrackKind = Literal["official", "other"]
SearchMode = Literal["track", "radio"]

ErrorCode = Literal[
    "quota_exhausted",
    "auth_required",
    "auth_expired",
    "not_configured",
    "not_supported",
    "api_error",
    "network_error",
    "invalid_request",
]


class MusicError(Exception):
    """An expected music failure. ``message`` is safe to show (PT-BR, no URL, no key)."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message


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


def is_video_id(value: object) -> bool:
    """Whether ``value`` has the shape of a YouTube video id (11 URL-safe characters)."""
    return isinstance(value, str) and _VIDEO_ID.match(value) is not None


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
