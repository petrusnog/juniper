"""Saving and resuming conversations (short-term memory, ADR-011).

``SessionStore`` is the interface the agent depends on. ``FileSessionStore`` keeps one JSON
file per session under ``memory.session.dir``; ``MongoSessionStore`` (``brain.memory.mongo``,
T-053) is the MongoDB implementation, chosen by ``memory.session.store``.

Only the history is stored. The system prompt is not: a resumed conversation gets the
current persona. Pending confirmations are not stored either, so after resuming nothing runs
without a new "sim" (ADR-006).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from brain.config import settings
from brain.core.session import Session
from brain.core.title import SOURCES, clean_title
from brain.llm.types import Message, ToolCall

FORMAT_VERSION = 1
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ROLES = {"system", "user", "assistant", "tool"}
PREVIEW_CHARS = 60
# The "[quinta-feira, 15/10/2026, 23:18 (...)] " stamp every user turn carries (core.session).
_DATE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")


class SessionStoreError(Exception):
    """A session could not be read (corrupt file, unsupported format, invalid id)."""


def check_session_id(session_id: str) -> str:
    """``session_id`` itself, or ``SessionStoreError`` if it is not safe to use as a key."""
    if not _ID_PATTERN.match(session_id):
        raise SessionStoreError(f"id de sessão inválido: {session_id!r}")
    return session_id


@dataclass(frozen=True)
class SessionInfo:
    """Summary of a saved session, for listings."""

    id: str
    created_at: str
    updated_at: str
    turns: int
    preview: str
    title: str = ""  # T-225: what the lists show; "" for a conversation that has none (older ones)
    title_source: str = ""


class SessionStore(Protocol):
    """Where conversations are kept between runs."""

    def save(self, session: Session) -> None:
        """Persist ``session`` (a no-op while it has no messages)."""

    def load(self, session_id: str) -> Session | None:
        """The session with that id, or ``None`` if it does not exist."""

    def latest(self) -> Session | None:
        """The most recently updated session, or ``None`` if there is none."""

    def list_sessions(self) -> list[SessionInfo]:
        """Saved sessions, most recently updated first."""

    def set_title(self, session_id: str, title: str, source: str) -> bool:
        """Name a saved session without touching when it was last used. ``False`` when there is
        no such session, or when ``source`` is not ``"user"`` and the person already renamed it
        (a title the person chose is never overwritten)."""


def message_to_dict(message: Message) -> dict[str, Any]:
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["tool_calls"] = [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls
        ]
    if message.tool_call_id is not None:
        data["tool_call_id"] = message.tool_call_id
    if message.name is not None:
        data["name"] = message.name
    return data


def message_from_dict(data: dict[str, Any]) -> Message:
    role = data["role"]
    if role not in _ROLES:
        raise ValueError(f"papel desconhecido: {role!r}")
    calls = tuple(
        ToolCall(str(c["id"]), str(c["name"]), dict(c.get("arguments") or {}))
        for c in data.get("tool_calls") or ()
    )
    return Message(
        role,
        data.get("content"),
        tool_calls=calls,
        tool_call_id=data.get("tool_call_id"),
        name=data.get("name"),
    )


def session_to_dict(session: Session) -> dict[str, Any]:
    return {
        "version": FORMAT_VERSION,
        "id": session.id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "history": [message_to_dict(m) for m in session.history],
        "turns_total": session.turns_total,
        "title": session.title,
        "title_source": session.title_source,
    }


def session_from_dict(data: dict[str, Any]) -> Session:
    if data.get("version") != FORMAT_VERSION:
        raise ValueError(f"formato não suportado: {data.get('version')!r}")
    session = Session(session_id=str(data["id"]))
    session.created_at = str(data["created_at"])
    session.updated_at = str(data["updated_at"])
    session.history = [message_from_dict(m) for m in data["history"]]
    # Older saves have no counter: the user messages still in the history are the best guess.
    session.turns_total = int(
        data.get("turns_total", sum(1 for m in session.history if m.role == "user"))
    )
    session.title, session.title_source = read_title(data)
    return session


def read_title(data: dict[str, Any]) -> tuple[str, str]:
    """The stored title and who made it; a save from before T-225 has neither."""
    title = data.get("title")
    source = data.get("title_source")
    if not isinstance(title, str) or source not in SOURCES:
        return "", ""
    return title, str(source)


def keep_users_title(session: Session, stored: dict[str, Any] | None) -> None:
    """A title the person chose must survive a save made from a stale copy of the session
    (the model finishes naming it, or another window renames it, while a turn is being saved):
    when the stored one is the person's and this copy's is not, this copy takes the stored one."""
    if not stored or session.title_source == "user":
        return
    title, source = read_title(stored)
    if source == "user" and title:
        session.title, session.title_source = title, source


def session_info(session: Session) -> SessionInfo:
    users = [m for m in session.history if m.role == "user"]
    first = _DATE_PREFIX.sub("", (users[0].content or ""), count=1) if users else ""
    return SessionInfo(
        id=session.id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        turns=len(users),
        preview=first[:PREVIEW_CHARS],
        title=session.title,
        title_source=session.title_source,
    )


class FileSessionStore:
    """One JSON file per session, private to the user (directory 700, files 600)."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory if directory is not None else settings.memory.session.dir

    def _path(self, session_id: str) -> Path:
        return self.directory / f"{check_session_id(session_id)}.json"

    def save(self, session: Session) -> None:
        if not session.history:
            return
        path = self._path(session.id)
        keep_users_title(session, self._stored_title(path))
        created = not self.directory.exists()
        self.directory.mkdir(parents=True, exist_ok=True)
        if created:
            os.chmod(self.directory, 0o700)
        # Atomic: a crash mid-write leaves the previous version, never a half file.
        # mkstemp creates the file with mode 600.
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(session_to_dict(session), handle, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def load(self, session_id: str) -> Session | None:
        return self._read(self._path(session_id))

    @staticmethod
    def _stored_title(path: Path) -> dict[str, Any] | None:
        """Only the title fields of the saved file (None when there is none or it is damaged)."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def set_title(self, session_id: str, title: str, source: str) -> bool:
        path = self._path(session_id)
        clean = clean_title(title)
        if source not in SOURCES or not clean:
            return False
        try:
            stat = path.stat()
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        if source != "user" and read_title(data)[1] == "user":
            return False
        data["title"], data["title_source"] = clean, source
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            os.utime(tmp, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # renaming is not using it
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return True

    def _read(self, path: Path) -> Session | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            return session_from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SessionStoreError(f"sessão corrompida ({path.name}): {exc}") from exc

    def _files_newest_first(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        files = [p for p in self.directory.glob("*.json") if _ID_PATTERN.match(p.stem)]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def latest(self) -> Session | None:
        for path in self._files_newest_first():
            try:
                session = self._read(path)
            except SessionStoreError:
                continue  # a damaged file must not hide the good ones
            if session is not None:
                return session
        return None

    def list_sessions(self) -> list[SessionInfo]:
        infos: list[SessionInfo] = []
        for path in self._files_newest_first():
            try:
                session = self._read(path)
            except SessionStoreError:
                continue
            if session is not None:
                infos.append(session_info(session))
        return sorted(infos, key=lambda i: i.updated_at, reverse=True)
