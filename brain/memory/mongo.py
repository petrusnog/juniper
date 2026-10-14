"""MongoDB access for the memory layer (ADR-011, T-053).

This is the only module that imports the MongoDB driver. ``MongoConnection`` owns the client
and turns driver failures into ``MemoryUnavailableError`` (a friendly message, no stacktrace);
``MongoSessionStore`` implements ``SessionStore`` on top of it, one document per session in
the ``sessions`` collection. The rest of the code only sees the interface.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import (
    ConnectionFailure,
    OperationFailure,
    PyMongoError,
)

from brain.config import MongoSettings, settings
from brain.core.session import Session
from brain.memory.errors import MemoryUnavailableError
from brain.memory.session import (
    SessionInfo,
    SessionStoreError,
    check_session_id,
    session_from_dict,
    session_info,
    session_to_dict,
)

SESSIONS_COLLECTION = "sessions"
_AUTH_FAILED = 18
_CREDENTIALS = re.compile(r"//[^/@]*@")


def _where(uri: str) -> str:
    """The URI without any ``user:password@``, safe to show or log."""
    return _CREDENTIALS.sub("//", uri)


class MongoConnection:
    """One lazily created client for the configured MongoDB."""

    def __init__(self, config: MongoSettings | None = None) -> None:
        self.config = config if config is not None else settings.memory.mongo
        self._client: MongoClient[dict[str, Any]] | None = None

    @property
    def client(self) -> MongoClient[dict[str, Any]]:
        if self._client is None:
            password = self.config.password
            self._client = MongoClient(
                self.config.uri,
                username=self.config.username,
                password=password.get_secret_value() if password is not None else None,
                serverSelectionTimeoutMS=self.config.timeout_ms,
                tz_aware=True,
            )
        return self._client

    def collection(self, name: str) -> Collection[dict[str, Any]]:
        return self.client[self.config.database][name]

    @contextmanager
    def guard(self) -> Iterator[None]:
        """Run driver calls, reporting any failure as ``MemoryUnavailableError``."""
        try:
            yield
        except OperationFailure as exc:
            if exc.code == _AUTH_FAILED:
                raise MemoryUnavailableError(
                    "O MongoDB recusou o usuário ou a senha. Confira "
                    "JUNIPER_MEMORY__MONGO__USERNAME e JUNIPER_MEMORY__MONGO__PASSWORD no .env "
                    "(devem ser os mesmos usados para criar o banco com `docker compose up -d`)."
                ) from exc
            raise MemoryUnavailableError(f"O MongoDB recusou a operação: {exc}") from exc
        except ConnectionFailure as exc:
            raise MemoryUnavailableError(
                f"Não consegui conectar ao MongoDB em {_where(self.config.uri)}. "
                "Suba o banco com `docker compose up -d`, ou guarde as conversas em arquivos "
                "com `memory.session.store: file` no config.yaml."
            ) from exc
        except PyMongoError as exc:
            raise MemoryUnavailableError(f"Erro ao falar com o MongoDB: {exc}") from exc

    def check(self) -> None:
        """Startup healthcheck: ``MemoryUnavailableError`` unless the server answers."""
        with self.guard():
            self.client.admin.command("ping")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class MongoSessionStore:
    """Conversations in MongoDB, with the same behavior as ``FileSessionStore``.

    A document is the serialized session (``_id`` is the session id) plus what listings
    need without reading the whole history (``turns``, ``preview``) and ``saved_at``, when
    it was last written. ``latest`` goes by ``saved_at``, as the file store goes by mtime, so
    resuming and saving an old conversation makes it the latest again.
    """

    def __init__(self, connection: MongoConnection | None = None) -> None:
        self.connection = connection if connection is not None else MongoConnection()
        self._indexed = False

    def check(self) -> None:
        self.connection.check()

    def close(self) -> None:
        """Release the connection (stops the driver's background threads)."""
        self.connection.close()

    def _sessions(self) -> Collection[dict[str, Any]]:
        sessions = self.connection.collection(SESSIONS_COLLECTION)
        if not self._indexed:
            sessions.create_index([("saved_at", -1)])
            sessions.create_index([("updated_at", -1), ("saved_at", -1)])
            self._indexed = True
        return sessions

    def save(self, session: Session) -> None:
        if not session.history:
            return
        session_id = check_session_id(session.id)
        info = session_info(session)
        document = session_to_dict(session)
        del document["id"]
        document.update(
            turns=info.turns,
            preview=info.preview,
            saved_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        )
        with self.connection.guard():
            self._sessions().replace_one({"_id": session_id}, document, upsert=True)

    def load(self, session_id: str) -> Session | None:
        check_session_id(session_id)
        with self.connection.guard():
            document = self._sessions().find_one({"_id": session_id})
        return None if document is None else self._parse(document)

    def latest(self) -> Session | None:
        with self.connection.guard():
            documents = list(self._sessions().find().sort("saved_at", -1))
        for document in documents:
            try:
                return self._parse(document)
            except SessionStoreError:
                continue  # a damaged document must not hide the good ones
        return None

    def list_sessions(self) -> list[SessionInfo]:
        with self.connection.guard():
            documents = list(
                self._sessions()
                .find({}, projection={"history": 0})
                .sort([("updated_at", -1), ("saved_at", -1)])
            )
        infos: list[SessionInfo] = []
        for document in documents:
            try:
                infos.append(
                    SessionInfo(
                        id=str(document["_id"]),
                        created_at=str(document["created_at"]),
                        updated_at=str(document["updated_at"]),
                        turns=int(document["turns"]),
                        preview=str(document["preview"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return infos

    @staticmethod
    def _parse(document: dict[str, Any]) -> Session:
        try:
            return session_from_dict({**document, "id": document["_id"]})
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SessionStoreError(f"sessão corrompida ({document.get('_id')}): {exc}") from exc
