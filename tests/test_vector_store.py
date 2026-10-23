"""T-055: VectorStore (cosseno em Python sobre a collection `memories`).

Roda sem servidor, com uma collection falsa; os testes `integration` usam o MongoDB local.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from brain.config import RetrievalSettings, Settings, settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.mongo import MongoConnection, MongoVectorStore
from brain.memory.vector import (
    Memory,
    SearchHit,
    VectorStore,
    VectorStoreError,
    cosine_scores,
    memory_from_doc,
    memory_to_doc,
    new_memory_id,
    rank,
)
from tests.test_mongo_store import DOWN

MODEL = "modelo-a"


def mem(text: str, vec: list[float], **kw: Any) -> Memory:
    return Memory(
        text=text, type=kw.pop("type", "fact"), embedding=vec, embedding_model=MODEL, **kw
    )


def _get(doc: dict[str, Any], key: str) -> Any:
    for part in key.split("."):
        doc = doc.get(part) if isinstance(doc, dict) else None  # type: ignore[assignment]
    return doc


class FakeCollection:
    """The slice of the pymongo Collection API the store uses (equality filters, `$set`)."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.indexes: list[Any] = []

    def create_index(self, keys: Any) -> None:
        self.indexes.append(keys)

    def insert_one(self, doc: dict[str, Any]) -> None:
        if doc["_id"] in self.docs:
            raise ValueError("duplicate key")
        self.docs[doc["_id"]] = dict(doc)

    def find_one(self, flt: dict[str, Any]) -> dict[str, Any] | None:
        return next(iter(self.find(flt)), None)

    def find(self, flt: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        flt = flt or {}
        return [dict(d) for d in self.docs.values() if all(_get(d, k) == v for k, v in flt.items())]

    def count_documents(self, flt: dict[str, Any]) -> int:
        return len(self.find(flt))

    def replace_one(self, flt: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> Any:
        found = flt["_id"] in self.docs
        if found or upsert:
            self.docs[flt["_id"]] = dict(doc)
        return SimpleNamespace(matched_count=int(found))

    def update_one(self, flt: dict[str, Any], update: dict[str, Any]) -> Any:
        doc = self.docs.get(flt["_id"])
        if doc is not None:
            doc.update(update["$set"])
        return SimpleNamespace(matched_count=int(doc is not None))

    def delete_one(self, flt: dict[str, Any]) -> Any:
        return SimpleNamespace(deleted_count=int(self.docs.pop(flt["_id"], None) is not None))


class FakeConnection(MongoConnection):
    def __init__(self) -> None:
        super().__init__()
        self.collections: dict[str, FakeCollection] = {}

    @property
    def fake(self) -> FakeCollection:
        return self.collection("memories")  # type: ignore[no-any-return]

    def collection(self, name: str) -> Any:
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture(autouse=True)
def _no_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests use tiny hand-made vectors: start from "no cut" (the real default is 0.80)."""
    monkeypatch.setattr(settings.memory, "retrieval", RetrievalSettings(threshold=0.0))


@pytest.fixture
def store() -> MongoVectorStore:
    return MongoVectorStore(FakeConnection(), embedding_model=MODEL)


def fake(store: MongoVectorStore) -> FakeCollection:
    return store.connection.collection("memories")  # type: ignore[no-any-return]


# --- cosine -------------------------------------------------------------------------------


def test_cosine_basics() -> None:
    scores = cosine_scores([1, 0], [[1, 0], [0, 1], [-1, 0], [2, 0], [1, 1]])
    assert scores == pytest.approx([1, 0, -1, 1, math.sqrt(0.5)])


def test_cosine_ignores_vector_length_and_zero_rows() -> None:
    assert cosine_scores([3, 4], [[0.6, 0.8], [0, 0]]) == pytest.approx([1.0, -1.0])
    assert len(cosine_scores([1, 0], [])) == 0


def test_rank_orders_limits_and_applies_threshold() -> None:
    a, b, c = mem("a", [1, 0]), mem("b", [1, 1]), mem("c", [0, 1])
    hits = rank([1, 0], [(c, c.embedding), (b, b.embedding), (a, a.embedding)], 5, 0.0)
    assert [h.memory.text for h in hits] == ["a", "b", "c"]
    assert [h.memory.text for h in rank([1, 0], [(a, a.embedding), (b, b.embedding)], 1, 0.0)] == [
        "a"
    ]
    assert [h.memory.text for h in rank([1, 0], [(a, a.embedding), (b, b.embedding)], 5, 0.9)] == [
        "a"
    ]


def test_rank_skips_vectors_of_another_size() -> None:
    a, odd = mem("a", [1, 0]), mem("odd", [1, 0, 0])
    assert [
        h.memory.text for h in rank([1, 0], [(odd, odd.embedding), (a, a.embedding)], 5, -1)
    ] == ["a"]


# --- documents ----------------------------------------------------------------------------


def test_document_matches_the_adr_and_round_trips() -> None:
    m = mem("O projeto X usa Laravel 11.", [0.5, 0.5], type="project", session_id="s1")
    doc = memory_to_doc(m)
    assert doc["_id"] == m.id and doc["status"] == "active" and doc["superseded_by"] is None
    assert doc["source"] == {"kind": "extracted", "session_id": "s1", "turn": None}
    assert doc["embedding_model"] == MODEL
    assert memory_from_doc(doc) == m


def test_ids_are_sortable_and_unique() -> None:
    ids = [new_memory_id() for _ in range(50)]
    assert len(set(ids)) == 50 and all(i.startswith("mem_") for i in ids)


def test_damaged_documents_are_refused() -> None:
    with pytest.raises(VectorStoreError):
        memory_from_doc({"_id": "x", "text": "oi"})


@pytest.mark.parametrize(
    "bad",
    [
        {"text": "  "},
        {"type": "outro"},
        {"status": "apagada"},
        {"source_kind": "magica"},
        {"embedding": []},
        {"embedding": [0.0, 0.0]},
        {"embedding": [1.0, float("nan")]},
        {"embedding_model": ""},
    ],
)
def test_add_validates(store: MongoVectorStore, bad: dict[str, Any]) -> None:
    m = mem("ok", [1.0, 0.0])
    for key, value in bad.items():
        setattr(m, key, value)
    with pytest.raises(VectorStoreError):
        store.add(m)
    assert fake(store).docs == {}


# --- the store ----------------------------------------------------------------------------


def test_is_a_vector_store(store: MongoVectorStore) -> None:
    assert isinstance(store, VectorStore)


def test_add_get_and_indexes(store: MongoVectorStore) -> None:
    m = mem("Prefere ZSH.", [1.0, 0.0], type="preference")
    assert store.add(m) == m.id
    assert store.get(m.id) == m
    assert store.get("mem_nao_existe") is None
    assert [("status", 1), ("type", 1)] in fake(store).indexes
    assert [("created_at", -1)] in fake(store).indexes


def test_search_finds_by_meaning_order(store: MongoVectorStore) -> None:
    for t, v in [("zsh", [1, 0, 0]), ("laravel", [0, 1, 0]), ("sp", [0, 0, 1])]:
        store.add(mem(t, [float(x) for x in v]))
    hits = store.search([0.1, 1.0, 0.0], top_k=2)
    assert [h.memory.text for h in hits] == ["laravel", "zsh"]
    assert hits[0].score > hits[1].score
    assert isinstance(hits[0], SearchHit)


def test_search_only_sees_active_memories(store: MongoVectorStore) -> None:
    old, new = mem("usa Laravel 11", [1.0, 0.0]), mem("usa Laravel 12", [1.0, 0.1])
    store.add(old)
    store.add(new)
    assert store.update(old.id, status="superseded", superseded_by=new.id)
    assert [h.memory.text for h in store.search([1.0, 0.0])] == ["usa Laravel 12"]
    assert store.get(old.id).superseded_by == new.id  # type: ignore[union-attr]


def test_filters_cannot_reveal_superseded_ones(store: MongoVectorStore) -> None:
    m = mem("velha", [1.0, 0.0])
    store.add(m)
    store.update(m.id, status="superseded")
    assert store.search([1.0, 0.0], filters={"type": "fact"}) == []


def test_search_ignores_other_models_and_other_dimensions(store: MongoVectorStore) -> None:
    other = mem("outro modelo", [1.0, 0.0])
    other.embedding_model = "modelo-b"
    store.add(other)
    store.add(mem("dimensão errada", [1.0, 0.0, 0.0]))
    store.add(mem("certa", [1.0, 0.0]))
    assert [h.memory.text for h in store.search([1.0, 0.0])] == ["certa"]


def test_search_filters(store: MongoVectorStore) -> None:
    store.add(mem("gosta de zsh", [1.0, 0.0], type="preference"))
    store.add(mem("usa laravel", [1.0, 0.1], type="project", session_id="s9"))
    assert [h.memory.text for h in store.search([1, 0], filters={"type": "project"})] == [
        "usa laravel"
    ]
    assert len(store.search([1, 0], filters={"source.session_id": "s9"})) == 1
    for bad in (
        {"embedding": [1]},
        {"$where": "1"},
        {"type": {"$ne": "x"}},
        {"status": "superseded"},
    ):
        with pytest.raises(VectorStoreError):
            store.search([1, 0], filters=bad)


def test_search_uses_configured_defaults(
    store: MongoVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(4):
        store.add(mem(f"m{i}", [1.0, i / 10]))
    monkeypatch.setattr(settings.memory, "retrieval", RetrievalSettings(top_k=2, threshold=0.0))
    assert len(store.search([1.0, 0.0])) == 2
    assert len(store.search([1.0, 0.0], top_k=3)) == 3
    monkeypatch.setattr(settings.memory, "retrieval", RetrievalSettings(top_k=9, threshold=0.99))
    assert [h.memory.text for h in store.search([1.0, 0.0])] == ["m0", "m1"]
    assert store.search([1.0, 0.0], threshold=1.01) == []


def test_search_validates_query(store: MongoVectorStore) -> None:
    for vec in ([], [0.0, 0.0], [float("inf"), 1.0]):
        with pytest.raises(VectorStoreError):
            store.search(vec)
    with pytest.raises(VectorStoreError):
        store.search([1.0], top_k=0)


def test_search_skips_damaged_documents(store: MongoVectorStore) -> None:
    store.add(mem("boa", [1.0, 0.0]))
    fake(store).docs["ruim"] = {"_id": "ruim", "status": "active", "embedding_model": MODEL}
    assert [h.memory.text for h in store.search([1.0, 0.0])] == ["boa"]


def test_update_refreshes_updated_at_and_rejects_unknown_fields(store: MongoVectorStore) -> None:
    m = mem("antes", [1.0, 0.0])
    m.updated_at = m.created_at = datetime.now(UTC) - timedelta(days=1)
    store.add(m)
    assert store.update(m.id, text="depois")
    got = store.get(m.id)
    assert got is not None and got.text == "depois"
    assert got.updated_at > m.updated_at and got.created_at == m.created_at
    assert not store.update("mem_nao_existe", text="x")
    for bad in ({"created_at": 1}, {"_id": "x"}, {"text": " "}, {"type": "zzz"}, {"status": "zzz"}):
        with pytest.raises(VectorStoreError):
            store.update(m.id, **bad)


def test_update_vector_needs_its_model(store: MongoVectorStore) -> None:
    m = mem("v", [1.0, 0.0])
    store.add(m)
    with pytest.raises(VectorStoreError):
        store.update(m.id, embedding=[0.0, 1.0])
    with pytest.raises(VectorStoreError):
        store.update(m.id, embedding=[0.0, 0.0], embedding_model="b")
    assert store.update(m.id, embedding=[0.0, 1.0], embedding_model=MODEL)
    assert store.search([0.0, 1.0])[0].score == pytest.approx(1.0)


def test_delete_really_removes(store: MongoVectorStore) -> None:
    m = mem("esquece", [1.0, 0.0])
    store.add(m)
    assert store.delete(m.id) is True
    assert store.delete(m.id) is False
    assert store.get(m.id) is None and fake(store).docs == {}


def test_model_defaults_to_the_configured_one() -> None:
    assert MongoVectorStore(FakeConnection()).embedding_model == settings.memory.embeddings.model


def test_retrieval_config(monkeypatch: pytest.MonkeyPatch) -> None:
    assert RetrievalSettings().top_k == 5 and RetrievalSettings().threshold == 0.80
    monkeypatch.setenv("JUNIPER_MEMORY__RETRIEVAL__TOP_K", "3")
    monkeypatch.setenv("JUNIPER_MEMORY__RETRIEVAL__THRESHOLD", "0.8")
    cfg = Settings(_env_file=None).memory.retrieval  # type: ignore[call-arg]
    assert (cfg.top_k, cfg.threshold) == (3, 0.8)
    monkeypatch.setenv("JUNIPER_MEMORY__RETRIEVAL__THRESHOLD", "2")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_database_down_is_a_friendly_error() -> None:
    down = MongoVectorStore(MongoConnection(DOWN), embedding_model=MODEL)
    with pytest.raises(MemoryUnavailableError, match="MongoDB"):
        down.add(mem("x", [1.0, 0.0]))
    with pytest.raises(MemoryUnavailableError):
        down.search([1.0, 0.0])
    down.close()


# --- integration: needs `docker compose up -d` --------------------------------------------


@pytest.fixture
def live() -> Iterator[MongoVectorStore]:
    name = f"juniper_test_{uuid.uuid4().hex[:8]}"
    connection = MongoConnection()
    connection.config = connection.config.model_copy(update={"database": name})
    vs = MongoVectorStore(connection, embedding_model=MODEL)
    try:
        vs.check()
    except MemoryUnavailableError as exc:
        pytest.skip(str(exc))
    yield vs
    connection.client.drop_database(name)
    vs.close()


@pytest.mark.integration
def test_real_mongo_round_trip(live: MongoVectorStore) -> None:
    a, b = mem("zsh", [1.0, 0.0], type="preference"), mem("laravel", [0.0, 1.0], type="project")
    live.add(a)
    live.add(b)
    assert live.get(a.id) == a
    hits = live.search([0.1, 1.0], top_k=1)
    assert [h.memory.id for h in hits] == [b.id]
    assert live.update(b.id, status="superseded")
    assert [h.memory.id for h in live.search([0.1, 1.0])] == [a.id]
    assert live.delete(a.id) and live.search([0.1, 1.0]) == []


def test_search_latency_with_five_thousand_memories(store: MongoVectorStore) -> None:
    """Documents the cost of the Python cosine at the volume the ADR plans for (T-055).

    Measured here without the network hop to MongoDB (the collection is in memory): about
    0.8 s for 5000 memories of 384 numbers. The bound is loose on purpose, so slow CI runners
    do not make it flaky; it only catches an accidental blow-up (e.g. quadratic work).
    """
    import random
    import time

    rng = random.Random(0)
    docs = fake(store).docs
    for i in range(5000):
        m = mem(f"memória {i}", [rng.gauss(0, 1) for _ in range(384)])
        docs[m.id] = memory_to_doc(m)
    query = [rng.gauss(0, 1) for _ in range(384)]
    started = time.perf_counter()
    hits = store.search(query, top_k=5)
    elapsed = time.perf_counter() - started
    assert len(hits) == 5 and hits[0].score >= hits[-1].score
    assert elapsed < 5.0, f"busca em 5000 memórias levou {elapsed:.2f}s"
    print(f"search 5000x384: {elapsed * 1000:.0f} ms")
