"""Picks the ``SessionStore`` the settings ask for (``memory.session.store``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from brain.config import settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.session import FileSessionStore, SessionStore

if TYPE_CHECKING:
    from brain.memory.issues import IssueStore
    from brain.memory.leak_audit import LeakAuditStore, LeakReportStore


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


def open_leak_audit(store: SessionStore) -> LeakAuditStore | None:
    """Where the leak audit records go (T-165): MongoDB, on the session store's connection.

    None when the audit is off or the conversations are in files (then only the ``leak_audit``
    event, without text, reaches the log).
    """
    connection = getattr(store, "connection", None)
    if not settings.leak_audit.enabled or connection is None:
        return None
    from brain.memory.leak_audit import LeakAuditStore

    return LeakAuditStore(connection)


def open_issue_store(store: SessionStore) -> IssueStore | None:
    """Where the registered issues go (ADR-028): MongoDB, on the session store's connection.

    None when the conversations are in files: ``report_issue`` is then not offered at all.
    """
    connection = getattr(store, "connection", None)
    if connection is None:
        return None
    from brain.memory.issues import IssueStore

    return IssueStore(connection)


def open_leak_reports(store: SessionStore) -> LeakReportStore | None:
    """Where the ``/vazou`` reports go (T-167): MongoDB, on the session store's connection.

    None when the conversations are in files: the command then says it has nowhere to save.
    """
    connection = getattr(store, "connection", None)
    if connection is None:
        return None
    from brain.memory.leak_audit import LeakReportStore

    return LeakReportStore(connection)
