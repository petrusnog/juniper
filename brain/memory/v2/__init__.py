"""Memória V2 (ADR-017): the layers above the facts. Opt-in: ``memory.v2.enabled``.

Everything here lives in MongoDB (the same database as the v1 memory); only the persona core is a
file. ``v2_unavailable_reason`` says why the V2 cannot run (or ``None`` when it can) and
``require_v2`` turns that into the typed ``MemoryUnavailableError``, so a bad configuration is a
clear message, never a crash of the server.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from brain.config import settings
from brain.memory.errors import MemoryUnavailableError

if TYPE_CHECKING:
    from brain.core.session import Turn
    from brain.memory.v2.store import V2Data

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


def open_transcript_sink() -> tuple[Callable[[str, Turn], None], V2Data] | None:
    """The ``turn_sink`` that keeps the durable transcript, and the data it writes to.

    ``None`` when the V2 is off or cannot run. The caller closes the data when it is done.
    """
    if not v2_enabled():
        return None
    from brain.memory.v2.transcripts import TranscriptWriter

    data = open_v2_data()
    return TranscriptWriter(data).write, data


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
