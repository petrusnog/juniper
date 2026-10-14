"""Picks the ``SessionStore`` the settings ask for (``memory.session.store``)."""

from __future__ import annotations

from brain.config import settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.session import FileSessionStore, SessionStore


def open_session_store() -> SessionStore:
    """The configured store. For MongoDB this is also the startup healthcheck.

    Raises ``MemoryUnavailableError`` (with a message fit for the user) when MongoDB is
    selected but cannot be used.
    """
    if settings.memory.session.store == "file":
        return FileSessionStore()
    try:
        from brain.memory.mongo import MongoSessionStore
    except ImportError as exc:  # the driver is only needed when MongoDB is chosen
        raise MemoryUnavailableError(
            "Falta o driver do MongoDB (pymongo). Rode `pip install -e .` na pasta da Juniper."
        ) from exc
    store = MongoSessionStore()
    try:
        store.check()
    except MemoryUnavailableError:
        store.close()
        raise
    return store


def close_session_store(store: SessionStore) -> None:
    """Release what the store holds, if it holds anything (MongoDB does, files do not).

    Without this the driver's monitor threads outlive the chat and are joined by an exit
    hook, where a second Ctrl+C prints a traceback.
    """
    close = getattr(store, "close", None)
    if callable(close):
        close()
