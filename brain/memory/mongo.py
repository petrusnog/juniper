"""MongoDB access for the memory layer (ADR-011, T-053).

This is the only module that imports the MongoDB driver. ``MongoConnection`` owns the client
and turns driver failures into ``MemoryUnavailableError`` (a friendly message, no stacktrace);
``MongoSessionStore`` implements ``SessionStore`` on top of it, one document per session in
the ``sessions`` collection. The rest of the code only sees the interface.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
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
from brain.memory.vector import (
    MEMORIES_COLLECTION,
    Memory,
    SearchHit,
    VectorStoreError,
    check_update_fields,
    check_vector,
    memory_from_doc,
    memory_to_doc,
    rank,
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


# Stored fields a search may filter on. Anything else (and any operator) is refused, so a
# filter can neither reach another memory's vector nor smuggle in a MongoDB operator.
_FILTERABLE = frozenset({"type", "source.kind", "source.session_id"})


class MongoVectorStore:
    """Long-term memories in the ``memories`` collection (ADR-011, T-055).

    Search loads the ``active`` vectors made by ``embedding_model`` and ranks them by cosine
    similarity in Python (numpy). At personal volume (hundreds to a few thousand memories)
    that answers in milliseconds and needs no Atlas. Vectors from another model are ignored,
    since they are not comparable; ``juniper memory reindex`` brings them up to date.
    """

    def __init__(
        self,
        connection: MongoConnection | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self.connection = connection if connection is not None else MongoConnection()
        self.embedding_model = (
            embedding_model if embedding_model is not None else settings.memory.embeddings.model
        )
        self._indexed = False

    def check(self) -> None:
        self.connection.check()

    def close(self) -> None:
        self.connection.close()

    def _memories(self) -> Collection[dict[str, Any]]:
        memories = self.connection.collection(MEMORIES_COLLECTION)
        if not self._indexed:
            memories.create_index([("status", 1), ("type", 1)])
            memories.create_index([("created_at", -1)])
            self._indexed = True
        return memories

    def add(self, memory: Memory) -> str:
        memory.validate()
        with self.connection.guard():
            self._memories().insert_one(memory_to_doc(memory))
        return memory.id

    def get(self, memory_id: str) -> Memory | None:
        with self.connection.guard():
            doc = self._memories().find_one({"_id": memory_id})
        return None if doc is None else memory_from_doc(doc)

    def count(self) -> int:
        with self.connection.guard():
            return int(self._memories().count_documents({"status": "active"}))

    def search(
        self,
        vector: Sequence[float],
        top_k: int | None = None,
        threshold: float | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> list[SearchHit]:
        check_vector(vector)
        retrieval = settings.memory.retrieval
        top_k = retrieval.top_k if top_k is None else top_k
        threshold = retrieval.threshold if threshold is None else threshold
        if top_k < 1:
            raise VectorStoreError("top_k precisa ser pelo menos 1.")
        query: dict[str, Any] = {}
        for key, value in (filters or {}).items():
            if key not in _FILTERABLE or isinstance(value, (dict, list)):
                raise VectorStoreError(
                    f"Filtro não permitido: {key!r} "
                    f"(use {sorted(_FILTERABLE)}, com um valor simples)."
                )
            query[key] = value
        query.update(status="active", embedding_model=self.embedding_model)
        with self.connection.guard():
            docs = list(self._memories().find(query))
        candidates: list[tuple[Memory, list[float]]] = []
        for doc in docs:
            try:
                memory = memory_from_doc(doc)
            except VectorStoreError:
                continue  # a damaged document must not hide the good ones
            candidates.append((memory, memory.embedding))
        return rank(vector, candidates, top_k, threshold)

    def update(self, memory_id: str, **fields: Any) -> bool:
        check_update_fields(fields)
        changes = dict(fields)
        if "embedding" in changes:
            changes["embedding"] = [float(x) for x in changes["embedding"]]
        changes["updated_at"] = datetime.now(UTC)
        with self.connection.guard():
            result = self._memories().update_one({"_id": memory_id}, {"$set": changes})
        return bool(result.matched_count)

    def delete(self, memory_id: str) -> bool:
        with self.connection.guard():
            result = self._memories().delete_one({"_id": memory_id})
        return bool(result.deleted_count)
