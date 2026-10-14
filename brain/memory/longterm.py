"""Long-term memory as the clients see it: one object, or ``None`` when it is off.

It is on when ``memory.extraction.enabled`` is true *and* the conversations live in MongoDB
(``memory.session.store: mongo``), since that is where the memories are kept. Later tasks hang
retrieval (T-057) and the ``remember``/``recall``/``forget`` tools (T-058) on this object.
"""

from __future__ import annotations

from brain.config import settings
from brain.core.session import Turn
from brain.llm.base import LLMProvider
from brain.memory.embeddings import EmbeddingProvider, open_embedding_provider
from brain.memory.errors import MemoryUnavailableError
from brain.memory.extraction import ExtractionResult, MemoryExtractor
from brain.memory.vector import VectorStore


class LongTermMemory:
    def __init__(
        self, store: VectorStore, embedder: EmbeddingProvider, extractor: MemoryExtractor
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.extractor = extractor

    async def extract(self, turns: list[Turn], session_id: str | None = None) -> ExtractionResult:
        return await self.extractor.extract(turns, session_id)

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
        from brain.memory.mongo import MongoVectorStore
    except ImportError as exc:
        raise MemoryUnavailableError(
            "Falta o driver do MongoDB (pymongo). Rode `pip install -e .` na pasta da Juniper."
        ) from exc
    store = MongoVectorStore()
    embedder = open_embedding_provider()
    return LongTermMemory(store, embedder, MemoryExtractor(llm, embedder, store))
