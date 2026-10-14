"""Long-term memory as the clients see it: one object, or ``None`` when it is off.

It is on when ``memory.extraction.enabled`` is true *and* the conversations live in MongoDB
(``memory.session.store: mongo``), since that is where the memories are kept. Later tasks hang
retrieval (T-057) and the ``remember``/``recall``/``forget`` tools (T-058) on this object.
"""

from __future__ import annotations

import asyncio
from typing import Any

from brain.config import settings
from brain.core.session import Turn
from brain.llm.base import LLMProvider
from brain.memory.embeddings import EmbeddingProvider, open_embedding_provider
from brain.memory.errors import MemoryUnavailableError
from brain.memory.extraction import ExtractionResult, MemoryExtractor
from brain.memory.profile import Profile, ProfileStore, build_profile
from brain.memory.profile import export as export_profile
from brain.memory.retrieval import RecalledContext, recall_context
from brain.memory.vector import Memory, SearchHit, VectorStore


class LongTermMemory:
    def __init__(
        self,
        store: VectorStore,
        embedder: EmbeddingProvider,
        extractor: MemoryExtractor | None,
        profiles: ProfileStore | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.extractor = extractor
        self.profiles = profiles

    async def extract(
        self, turns: list[Turn], session_id: str | None = None, context: int = 0
    ) -> ExtractionResult:
        if self.extractor is None:
            raise MemoryUnavailableError("A extração de memórias não está ligada neste contexto.")
        return await self.extractor.extract(turns, session_id, context)

    async def remember(
        self, text: str, kind: str, session_id: str | None = None
    ) -> ExtractionResult:
        """Store a fact the user asked for (same checks, dedup and update as an extracted one)."""
        if self.extractor is None:
            raise MemoryUnavailableError("Guardar memórias não está ligado neste contexto.")
        return await self.extractor.remember(text, kind, session_id)

    async def recall(
        self, query: str, top_k: int | None = None, threshold: float | None = None
    ) -> list[SearchHit]:
        """Active memories closest in meaning to ``query``, best first."""
        vector = await asyncio.to_thread(self.embedder.embed, query, "query")
        return await asyncio.to_thread(self.store.search, vector, top_k, threshold)

    async def find_to_forget(self, memory_id: str | None, query: str | None) -> list[Memory]:
        """What ``forget`` would remove: the memory with that id, or the closest to ``query``."""
        if memory_id:
            memory = await asyncio.to_thread(self.store.get, memory_id.strip())
            return [] if memory is None else [memory]
        if query and query.strip():
            return [h.memory for h in await self.recall(query.strip())]
        return []

    async def forget(self, memories: list[Memory]) -> list[Memory]:
        """Delete ``memories`` for good, and the older versions they replaced (ADR-011, section 4:
        ``superseded`` is there to audit updates, not to keep what the user asked to forget).
        Returns everything that was removed."""
        everything = await asyncio.to_thread(self.store.list_memories, None, True)
        doomed = {m.id for m in memories}
        grew = True
        while grew:
            grew = False
            for m in everything:
                if m.superseded_by in doomed and m.id not in doomed:
                    doomed.add(m.id)
                    grew = True
        removed = [m for m in everything if m.id in doomed]
        known = {m.id for m in removed}
        removed += [m for m in memories if m.id not in known]  # asked for, though not listed
        for m in removed:
            await asyncio.to_thread(self.store.delete, m.id)
        return removed

    def export(self, include_vectors: bool = False) -> list[dict[str, Any]]:
        """Every memory as plain data. The vectors are left out unless asked for."""
        rows = []
        for m in self.store.list_memories(None, True):
            row: dict[str, Any] = {
                "id": m.id,
                "text": m.text,
                "type": m.type,
                "status": m.status,
                "superseded_by": m.superseded_by,
                "source": {"kind": m.source_kind, "session_id": m.session_id},
                "embedding_model": m.embedding_model,
                "created_at": m.created_at.isoformat(),
                "updated_at": m.updated_at.isoformat(),
            }
            if include_vectors:
                row["embedding"] = m.embedding
            rows.append(row)
        return rows

    async def context_for(self, message: str, previous: str | None = None) -> RecalledContext:
        """The memories relevant to ``message``, ready to inject into the prompt (T-057)."""
        return await recall_context(self.store, self.embedder, message, previous, self.profiles)

    # --- profile (T-061) ---

    def profile_enabled(self) -> bool:
        return self.profiles is not None and settings.memory.profile.enabled

    async def get_profile(self) -> Profile | None:
        if not self.profile_enabled():
            return None
        assert self.profiles is not None
        return await asyncio.to_thread(self.profiles.get)

    async def set_profile(self, text: str, fields: dict[str, Any] | None = None) -> Profile:
        """Replace the profile paragraph (fields are merged). ``ProfileError`` if not acceptable."""
        if self.profiles is None or not self.profile_enabled():
            raise MemoryUnavailableError("O perfil está desligado (memory.profile.enabled).")
        existing = await asyncio.to_thread(self.profiles.get)
        profile = build_profile(text, fields, existing, settings.memory.profile.max_chars)
        await asyncio.to_thread(self.profiles.set, profile)
        return profile

    async def forget_profile(self) -> bool:
        if self.profiles is None:
            return False
        return await asyncio.to_thread(self.profiles.clear)

    def export_profile(self) -> dict[str, Any] | None:
        return export_profile(self.profiles.get() if self.profiles is not None else None)

    def needs_model_download(self) -> bool:
        """True if there are memories to search and the embedding model is not on disk yet."""
        downloaded = getattr(self.embedder, "is_downloaded", None)
        return callable(downloaded) and not downloaded() and self.store.count() > 0

    def close(self) -> None:
        close = getattr(self.store, "close", None)
        if callable(close):
            close()


def long_term_enabled() -> bool:
    return settings.memory.extraction.enabled and settings.memory.session.store == "mongo"


def open_long_term_memory(llm: LLMProvider) -> LongTermMemory | None:
    """The configured long-term memory, or ``None`` when it is off.

    Opening it does not touch the network or load the embedding model: both happen on first use.
    """
    if not long_term_enabled():
        return None
    try:
        from brain.memory.mongo import MongoProfileStore, MongoVectorStore
    except ImportError as exc:
        raise MemoryUnavailableError(
            "Falta o driver do MongoDB (pymongo). Rode `pip install -e .` na pasta da Juniper."
        ) from exc
    store = MongoVectorStore()
    embedder = open_embedding_provider()
    profiles = MongoProfileStore(store.connection)
    return LongTermMemory(
        store, embedder, MemoryExtractor(llm, embedder, store, profiles=profiles), profiles
    )


def open_memory_for_commands() -> LongTermMemory:
    """Memory for the ``juniper memory`` commands: needs MongoDB, but no LLM and no chat.

    Raises ``MemoryUnavailableError`` (message fit for the user) when MongoDB cannot be used.
    """
    try:
        from brain.memory.mongo import MongoProfileStore, MongoVectorStore
    except ImportError as exc:
        raise MemoryUnavailableError(
            "Falta o driver do MongoDB (pymongo). Rode `pip install -e .` na pasta da Juniper."
        ) from exc
    store = MongoVectorStore()
    try:
        store.check()
    except MemoryUnavailableError:
        store.close()
        raise
    return LongTermMemory(
        store, open_embedding_provider(), None, MongoProfileStore(store.connection)
    )
