"""The Petrus's musical taste (ADR-024, section 8): what he asks for and how he reacts.

The official YouTube API gives no play history, so taste comes from his own words: "toca Djavan"
(``played``) and his reactions ("gostei dessa", "pula essa", "não curto isso"). Following the rule
of ADR-017, **only what he said** is recorded: nothing here is ever written from a search result, a
track title the API returned or any other tool output. The only writer is the ``music_feedback``
tool, which the LLM calls for his words.

The v1 only *records*: ``juniper music taste`` shows it, ``juniper music taste clear`` deletes all
of it. That the dream reads these events and forms beliefs about his taste is a later step that
changes ADR-017.

Storage is the memory's ``DocStore`` (MongoDB on the VPS, a dict in tests), collection
``music_taste``.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from brain.config import settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.secretfilter import find_secret
from brain.memory.v2.store import DocStore
from brain.music.base import clean_text

TASTE: Final = "music_taste"
MAX_TEXT_CHARS: Final = 200
MAX_QUOTE_CHARS: Final = 160
MAX_PER_SESSION: Final = 30  # a loop of the LLM must not fill the collection
MAX_TOTAL: Final = 5000
DEDUPE_SECONDS: Final = 60  # the same reaction twice within a minute is one reaction

Signal = Literal["like", "dislike", "skip", "played"]
SIGNALS: Final[tuple[str, ...]] = ("like", "dislike", "skip", "played")
# How much each reaction says about liking an artist.
WEIGHT: Final[dict[str, int]] = {"like": 2, "played": 1, "skip": -1, "dislike": -2}

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class TasteError(ValueError):
    """A feedback that cannot be recorded; the message is safe to show (PT-BR)."""


@dataclass(frozen=True)
class TasteEvent:
    id: str
    at: datetime
    signal: str
    title: str
    artist: str
    video_id: str
    session_id: str
    quote: str = ""

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> TasteEvent:
        return cls(
            id=str(doc["_id"]),
            at=doc["at"],
            signal=str(doc["signal"]),
            title=str(doc.get("title", "")),
            artist=str(doc.get("artist", "")),
            video_id=str(doc.get("video_id", "")),
            session_id=str(doc.get("session_id", "")),
            quote=str(doc.get("quote", "")),
        )


@dataclass(frozen=True)
class TasteSummary:
    total: int
    by_signal: dict[str, int]
    artists: list[tuple[str, int]]  # (artist, score), best first


def _text(value: object, label: str, limit: int = MAX_TEXT_CHARS) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TasteError(f"{label} deve ser um texto.")
    text = clean_text(value)
    if len(text) > limit:
        raise TasteError(f"{label} passa de {limit} caracteres.")
    if find_secret(text) is not None:
        raise TasteError(f"{label} parece conter um segredo; não guardo isso.")
    return text


class TasteBook:
    """Records and reads the taste events."""

    def __init__(
        self, docs: DocStore, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._docs = docs
        self._clock = clock

    def record(
        self,
        signal: str,
        *,
        title: object = None,
        artist: object = None,
        video_id: object = None,
        quote: object = None,
        session_id: str = "",
    ) -> TasteEvent:
        if signal not in SIGNALS:
            raise TasteError("O sinal deve ser like, dislike, skip ou played.")
        name, who = _text(title, "O título"), _text(artist, "O artista")
        words = _text(quote, "A citação", MAX_QUOTE_CHARS)
        if not name and not who:
            raise TasteError("Diga ao menos o título ou o artista da música.")
        repeated = self._repeat(signal, name, who)
        if repeated is not None:
            return repeated
        if self._docs.count(TASTE) >= MAX_TOTAL:
            raise TasteError(
                "O gosto musical anotado chegou ao limite; rode `juniper music taste clear`."
            )
        if session_id and self._docs.count(TASTE, {"session_id": session_id}) >= MAX_PER_SESSION:
            raise TasteError("Já anotei bastante do seu gosto nesta conversa; continuo na próxima.")
        vid = video_id.strip() if isinstance(video_id, str) else ""
        if vid and not _VIDEO_ID.match(vid):
            vid = ""  # a malformed id is dropped, not an error: the words are what matters
        event = TasteEvent(
            id=uuid.uuid4().hex,
            at=self._clock(),
            signal=signal,
            title=name,
            artist=who,
            video_id=vid,
            session_id=session_id,
            quote=words,
        )
        self._docs.put(
            TASTE,
            {
                "_id": event.id,
                "at": event.at,
                "signal": event.signal,
                "title": event.title,
                "artist": event.artist,
                "video_id": event.video_id,
                "session_id": event.session_id,
                "quote": event.quote,
            },
        )
        return event

    def _repeat(self, signal: str, title: str, artist: str) -> TasteEvent | None:
        """The same reaction recorded within the last minute, if any (it is not recorded twice)."""
        recent = self._docs.find(
            TASTE,
            {"signal": signal, "title": title, "artist": artist},
            sort=[("at", -1)],
            limit=1,
        )
        if recent and (self._clock() - recent[0]["at"]).total_seconds() < DEDUPE_SECONDS:
            return TasteEvent.from_doc(recent[0])
        return None

    def recent(self, limit: int = 20) -> list[TasteEvent]:
        docs = self._docs.find(TASTE, sort=[("at", -1)], limit=limit)
        return [TasteEvent.from_doc(d) for d in docs]

    def count(self) -> int:
        return self._docs.count(TASTE)

    def summary(self, top: int = 5) -> TasteSummary:
        by_signal: dict[str, int] = defaultdict(int)
        scores: dict[str, int] = defaultdict(int)
        names: dict[str, str] = {}
        docs = self._docs.find(TASTE)
        for doc in docs:
            by_signal[str(doc["signal"])] += 1
            artist = str(doc.get("artist", "")).strip()
            if artist:
                key = artist.casefold()
                names.setdefault(key, artist)
                scores[key] += WEIGHT.get(str(doc["signal"]), 0)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return TasteSummary(
            total=len(docs),
            by_signal=dict(by_signal),
            artists=[(names[key], score) for key, score in ranked[:top]],
        )

    def clear(self) -> int:
        """Delete everything recorded; returns how many events were removed."""
        return self._docs.delete_many(TASTE, {})


# --- the book on the memory's MongoDB (shared by the tool and the CLI) -----------------------

_book: TasteBook | None = None


def bind(book: TasteBook | None) -> None:
    """Point the tool and the CLI at a book (``None`` goes back to the MongoDB one)."""
    global _book
    _book = book


def book() -> TasteBook:
    """The bound book, or one on the memory's MongoDB (built once). Raises if memory is off."""
    global _book
    if _book is None:
        if settings.memory.session.store != "mongo":
            raise MemoryUnavailableError(
                "O gosto musical precisa de `memory.session.store: mongo` (com o MongoDB no ar)."
            )
        from brain.memory.v2.store import MongoDocStore

        try:
            _book = TasteBook(MongoDocStore())
        except ImportError as exc:
            raise MemoryUnavailableError(
                "Falta o driver do MongoDB (pymongo). Rode `pip install -e .`."
            ) from exc
    return _book


def enabled() -> bool:
    if _book is not None:
        return True
    return settings.music.enabled and settings.memory.session.store == "mongo"
