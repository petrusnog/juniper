"""Comandos de memória pelo servidor (T-077): mesmos dados do `juniper memory ...` local."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from brain.clients import protocol as p
from brain.clients.chat import ChatHandler
from brain.clients.server import BrainServer
from brain.config import RetrievalSettings, settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoProfileStore, MongoVectorStore
from brain.memory.session import FileSessionStore
from brain.memory.vector import Memory
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import Client, connect_client
from tests.test_extraction import MODEL, FakeEmbedder
from tests.test_vector_store import FakeConnection

TOKEN = "token-de-teste"


@pytest.fixture(autouse=True)
def _retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.memory, "retrieval", RetrievalSettings(top_k=5, threshold=0.4))


@pytest.fixture
def store() -> MongoVectorStore:
    return MongoVectorStore(FakeConnection(), embedding_model=MODEL)


@pytest.fixture
def memory(store: MongoVectorStore) -> LongTermMemory:
    return LongTermMemory(store, FakeEmbedder(), None, MongoProfileStore(store.connection))


def seed(store: MongoVectorStore, text: str, kind: str = "project") -> Memory:
    m = Memory(text=text, type=kind, embedding=FakeEmbedder().embed(text), embedding_model=MODEL)
    store.add(m)
    return m


class Harness:
    def __init__(self, tmp_path: Path, memory: LongTermMemory | None) -> None:
        handler = ChatHandler(
            FileSessionStore(tmp_path / "sessions"),
            FakeProvider([say("oi")]),
            commands_memory=memory,
        )
        self.server = BrainServer("127.0.0.1", 0, TOKEN, handler)

    async def __aenter__(self) -> Client:
        await self.server.start()
        return await connect_client(self.server)

    async def __aexit__(self, *exc: object) -> None:
        await self.server.stop()


async def ask(client: Client, op: str, args: dict[str, Any] | None = None, id: str = "m-1") -> Any:
    await client.send(type="memory.request", id=id, op=op, args=args or {})
    reply = await client.recv()
    assert getattr(reply, "reply_to", None) == id
    return reply


async def test_list_returns_what_the_local_command_shows(
    tmp_path: Path, memory: LongTermMemory, store: MongoVectorStore
) -> None:
    a = seed(store, "O projeto X usa Laravel 11.")
    b = seed(store, "Prefiro café sem açúcar.", "preference")
    async with Harness(tmp_path, memory) as client:
        reply = await ask(client, "list")
        only_pref = await ask(client, "list", {"type": "preference"}, "m-2")
    assert isinstance(reply, p.MemoryResult) and reply.ok and reply.data is not None
    rows = reply.data["memories"]
    expected = store.list_memories(None, False)
    assert [r["id"] for r in rows] == [m.id for m in expected]
    assert {r["id"] for r in rows} == {a.id, b.id}
    first = rows[0]
    assert set(first) == {"id", "text", "type", "status", "created_at"}
    assert first["created_at"] == expected[0].created_at.isoformat()
    assert [r["id"] for r in only_pref.data["memories"]] == [b.id]


async def test_list_with_an_invalid_type_is_bad_request(
    tmp_path: Path, memory: LongTermMemory
) -> None:
    async with Harness(tmp_path, memory) as client:
        reply = await ask(client, "list", {"type": "inexistente"})
    assert isinstance(reply, p.Error) and reply.code == "bad_request"


async def test_search_returns_scored_hits_like_the_local_command(
    tmp_path: Path, memory: LongTermMemory, store: MongoVectorStore
) -> None:
    target = seed(store, "O projeto X usa Laravel 11.")
    seed(store, "Gosto de caminhar de manhã.", "preference")
    async with Harness(tmp_path, memory) as client:
        reply = await ask(client, "search", {"query": "framework do projeto X Laravel", "limit": 3})
    expected = await memory.recall("framework do projeto X Laravel", 3, None)
    assert isinstance(reply, p.MemoryResult) and reply.data is not None
    results = reply.data["results"]
    assert [r["id"] for r in results] == [h.memory.id for h in expected]
    assert results[0]["id"] == target.id
    assert results[0]["score"] == round(expected[0].score, 4)
    assert set(results[0]) == {"id", "text", "type", "score"}


async def test_forget_removes_the_memory_and_unknown_id_is_a_typed_error(
    tmp_path: Path, memory: LongTermMemory, store: MongoVectorStore
) -> None:
    doomed = seed(store, "Moro em Recife.", "fact")
    async with Harness(tmp_path, memory) as client:
        missing = await ask(client, "forget", {"id": "mem_inexistente"})
        gone = await ask(client, "forget", {"id": doomed.id}, "m-2")
        assert (await ask(client, "list", None, "m-3")).data == {"memories": []}
    assert isinstance(missing, p.Error)
    assert (missing.code, missing.retryable, missing.reply_to) == ("not_found", False, "m-1")
    assert isinstance(gone, p.MemoryResult) and gone.data == {"deleted": [doomed.id]}
    assert store.get(doomed.id) is None


async def test_export_has_profile_and_memories_without_vectors_by_default(
    tmp_path: Path, memory: LongTermMemory, store: MongoVectorStore
) -> None:
    seed(store, "O projeto X usa Laravel 11.")
    await memory.set_profile("O usuário se chama Petrus.", {"name": "Petrus"})
    async with Harness(tmp_path, memory) as client:
        plain = await ask(client, "export")
        vectors = await ask(client, "export", {"vectors": True}, "m-2")
    assert plain.data["memories"] == [
        {k: v for k, v in row.items() if k != "embedding"} for row in memory.export(False)
    ]
    assert plain.data["profile"]["fields"] == {"name": "Petrus"}
    assert "embedding" not in plain.data["memories"][0]
    assert "embedding" in vectors.data["memories"][0]
    json.dumps(plain.data)  # serializável


async def test_profile_get_set_and_forget(tmp_path: Path, memory: LongTermMemory) -> None:
    async with Harness(tmp_path, memory) as client:
        empty = await ask(client, "profile.get")
        assert empty.data == {"profile": None}
        saved = await ask(
            client,
            "profile.set",
            {"text": "O usuário se chama Petrus Rennan.", "name": "Petrus Rennan", "gender": "M"},
            "m-2",
        )
        shown = await ask(client, "profile.get", None, "m-3")
        forgotten = await ask(client, "profile.forget", None, "m-4")
        again = await ask(client, "profile.forget", None, "m-5")
    assert saved.data["profile"]["fields"] == {"name": "Petrus Rennan", "gender": "M"}
    assert shown.data["profile"] == saved.data["profile"]
    assert shown.data["profile"]["rendered"]
    assert forgotten.data == {"deleted": True} and again.data == {"deleted": False}


async def test_invalid_profile_is_a_typed_error(tmp_path: Path, memory: LongTermMemory) -> None:
    async with Harness(tmp_path, memory) as client:
        reply = await ask(client, "profile.set", {"text": "x" * 5000})
        bad_date = await ask(
            client, "profile.set", {"text": "O usuário.", "birth_date": "ontem"}, "m-2"
        )
    assert isinstance(reply, p.Error) and reply.code == "bad_request"
    assert isinstance(bad_date, p.Error) and bad_date.code == "bad_request"


class BrokenStore(MongoVectorStore):
    def list_memories(self, *args: Any, **kwargs: Any) -> list[Memory]:
        raise MemoryUnavailableError("O MongoDB não respondeu.")


async def test_mongodb_down_is_a_typed_error_and_the_connection_stays_open(
    tmp_path: Path,
) -> None:
    broken = LongTermMemory(
        BrokenStore(FakeConnection(), embedding_model=MODEL), FakeEmbedder(), None
    )
    async with Harness(tmp_path, broken) as client:
        reply = await ask(client, "list")
        assert isinstance(reply, p.Error)
        assert (reply.code, reply.retryable) == ("memory_unavailable", True)
        assert "MongoDB" in reply.message
        await client.send(type="chat.send", id="c-1", text="oi")  # a conexão segue viva
        assert isinstance(await client.recv(), p.ChatStarted)


async def test_memory_off_on_the_server_is_a_typed_error(tmp_path: Path) -> None:
    async with Harness(tmp_path, None) as client:
        reply = await ask(client, "search", {"query": "qualquer coisa"})
    assert isinstance(reply, p.Error) and reply.code == "memory_unavailable"


async def test_secrets_are_masked_in_responses_and_never_logged(
    tmp_path: Path, memory: LongTermMemory, store: MongoVectorStore
) -> None:
    secret = "gsk_abcdefghijklmnopqrstuvwxyz0123456789"
    leaked = seed(store, f"A chave do serviço é {secret} e o projeto Y usa Django.")
    async with Harness(tmp_path, memory) as client:
        listed = await ask(client, "list")
        found = await ask(client, "search", {"query": "projeto Y Django", "threshold": 0.0}, "m-2")
        exported = await ask(client, "export", None, "m-3")
    for reply in (listed, found, exported):
        assert secret not in json.dumps(reply.data)
    assert leaked.id in json.dumps(listed.data)
    logs = "".join(f.read_text() for f in (tmp_path / "logs").glob("*.jsonl"))
    assert "memory_request" in logs
    assert secret not in logs and "projeto Y" not in logs and "Django" not in logs


async def test_a_slow_memory_request_does_not_block_the_connection(
    tmp_path: Path, memory: LongTermMemory, store: MongoVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = asyncio.Event()
    original = memory.recall

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await gate.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(memory, "recall", slow)
    seed(store, "O projeto X usa Laravel 11.")
    async with Harness(tmp_path, memory) as client:
        await client.send(type="memory.request", id="m-1", op="search", args={"query": "Laravel"})
        await client.send(type="session.list", id="s-1")
        assert isinstance(await client.recv(), p.SessionListed)  # respondeu antes da busca
        gate.set()
        assert isinstance(await client.recv(), p.MemoryResult)
