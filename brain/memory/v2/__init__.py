"""Memória V2 (ADR-017): the layers above the facts. Opt-in: ``memory.v2.enabled``.

Everything here lives in MongoDB (the same database as the v1 memory); only the persona core is a
file. ``v2_unavailable_reason`` says why the V2 cannot run (or ``None`` when it can) and
``require_v2`` turns that into the typed ``MemoryUnavailableError``, so a bad configuration is a
clear message, never a crash of the server.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from brain.config import settings
from brain.memory.errors import MemoryUnavailableError

if TYPE_CHECKING:
    from brain.core.session import Turn
    from brain.llm.base import LLMProvider
    from brain.memory.embeddings import EmbeddingProvider
    from brain.memory.v2.context import V2Context
    from brain.memory.v2.live import LiveReactor
    from brain.memory.v2.store import V2Data
    from brain.memory.v2.transcripts import TranscriptWriter

NEEDS_MONGO = (
    "A Memória V2 guarda tudo no MongoDB. Use memory.session.store: mongo no config.yaml "
    "(e suba o banco com `docker compose up -d`) ou desligue memory.v2.enabled."
)


def v2_unavailable_reason() -> str | None:
    """Why the V2 cannot run with the current settings, or ``None`` if it can (or is off)."""
    if settings.memory.v2.enabled and settings.memory.session.store != "mongo":
        return NEEDS_MONGO
    return None


def v2_enabled() -> bool:
    """True when the V2 is on *and* can run."""
    return settings.memory.v2.enabled and v2_unavailable_reason() is None


def require_v2() -> None:
    """Raise ``MemoryUnavailableError`` (a message fit for the user) unless the V2 can run."""
    if not settings.memory.v2.enabled:
        raise MemoryUnavailableError(
            "A Memória V2 está desligada. Ligue com memory.v2.enabled: true no config.yaml."
        )
    reason = v2_unavailable_reason()
    if reason is not None:
        raise MemoryUnavailableError(reason)


class V2Runtime:
    """What a chat needs from the V2: the transcript sink, the opinions context and the database.

    The caller closes it when the chat ends.
    """

    def __init__(
        self,
        data: V2Data,
        context: V2Context,
        writer: TranscriptWriter,
        reactor: LiveReactor | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.data = data
        self.context = context
        self._writer = writer
        self._reactor = reactor
        self._loop = loop
        self._reactions: set[Future[Any]] = set()

    def sink(self, session_id: str, turn: Turn) -> None:
        """The ``turn_sink``: tells the context what she asked, keeps the transcript and, in the
        background, lets her react to what he wrote (the reply never waits for it)."""
        self.context.after_turn(session_id, turn)
        self._writer.write(session_id, turn)
        self._react_in_background(session_id, turn)

    def _react_in_background(self, session_id: str, turn: Turn) -> None:
        loop = self._loop
        if self._reactor is None or loop is None or loop.is_closed() or not self._reactor.active:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._reactor.react(session_id, turn), loop)
        except RuntimeError:  # the loop is gone: nothing to react in
            return
        self._reactions.add(future)
        future.add_done_callback(self._reactions.discard)

    def close(self) -> None:
        self.data.close()


def open_v2_runtime(
    embedder: EmbeddingProvider | None = None, judge: LLMProvider | None = None
) -> V2Runtime | None:
    """The V2 for a chat, or ``None`` when it is off or cannot run (the chat goes on without it).

    ``embedder`` is the v1 memory's, to share the loaded model; ``judge`` reads long answers to a
    verification question.
    """
    if not v2_enabled():
        return None
    from brain.memory.embeddings import open_embedding_provider
    from brain.memory.v2.context import V2Context
    from brain.memory.v2.live import LiveReactor
    from brain.memory.v2.transcripts import TranscriptWriter

    data = open_v2_data()
    try:
        context = V2Context(data, embedder or open_embedding_provider(), judge=judge)
    except Exception:
        data.close()
        raise
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:  # opened outside a running loop: no live reactions
        loop = None
    return V2Runtime(data, context, TranscriptWriter(data), LiveReactor(data, judge), loop)


def open_v2_data() -> V2Data:
    """The V2 collections on the configured MongoDB (``MemoryUnavailableError`` if not allowed)."""
    require_v2()
    from brain.memory.v2.store import MongoDocStore, V2Data

    try:
        docs = MongoDocStore()
    except ImportError as exc:  # the driver is only needed here
        raise MemoryUnavailableError(
            "Falta o driver do MongoDB (pymongo). Rode `pip install -e .` na pasta da Juniper."
        ) from exc
    return V2Data.over(docs)
