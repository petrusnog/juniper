"""Cliente remoto (T-086): conexão, stream, erros, cancelamento e reconexão com backoff."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

import pytest

from brain.clients import protocol as p
from brain.clients import remote
from brain.clients.chat import ChatHandler
from brain.clients.remote import (
    ConnectionLostError,
    FatalConnectionError,
    RemoteClient,
    ServerError,
    ServerUnavailableError,
)
from brain.clients.server import BrainServer
from brain.memory.session import FileSessionStore
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import GatedProvider, wait_until

TOKEN = "token-do-cliente"


class Env:
    """Um servidor de verdade (porta efêmera) e clientes apontados para ele."""

    def __init__(self, tmp_path: Path, provider: Any, **server_kwargs: Any) -> None:
        self.store = FileSessionStore(tmp_path / "sessions")
        self.handler = ChatHandler(self.store, provider)
        self.kwargs = server_kwargs
        self.server = BrainServer("127.0.0.1", 0, TOKEN, self.handler, **server_kwargs)
        self.clients: list[RemoteClient] = []

    async def __aenter__(self) -> Env:
        await self.server.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        for client in self.clients:
            await client.close()
        await self.server.stop()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.server.port}/v1/ws"

    def client(self, token: str = TOKEN, device: str = "dev", **kwargs: Any) -> RemoteClient:
        client = RemoteClient(self.url, token, device_id=device, device_name="pc", **kwargs)
        self.clients.append(client)
        return client

    async def restart(self) -> None:
        """Derruba o servidor e sobe outro na mesma porta, com o mesmo estado."""
        port = self.server.port
        await self.server.stop(p.CLOSE_SERVICE_RESTART, "reiniciando")
        self.server = BrainServer("127.0.0.1", port, TOKEN, self.handler, **self.kwargs)
        await self.server.start()


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "BACKOFF_SECONDS", (0.05, 0.1))


# --- backoff (ADR-012, seção 10) ----------------------------------------------------------


def test_backoff_grows_1_2_4_8_16_then_stays_at_30_with_20_percent_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote, "BACKOFF_SECONDS", (1.0, 2.0, 4.0, 8.0, 16.0, 30.0))  # a do ADR
    rng = random.Random(1)
    for attempt, base in enumerate([1, 2, 4, 8, 16, 30, 30, 30]):
        for _ in range(50):
            delay = remote.backoff_delay(attempt, rng)
            assert base * 0.8 <= delay <= base * 1.2


def test_a_deploy_restart_reconnects_within_two_seconds() -> None:
    rng = random.Random(2)
    assert all(0 <= remote.restart_delay(rng) <= 2 for _ in range(100))


# --- conexão e chat -----------------------------------------------------------------------


async def test_chat_streams_tokens_in_order(tmp_path: Path) -> None:
    async with Env(tmp_path, FakeProvider([say("Amanhã ", "tem dentista.")])) as env:
        client = env.client()
        welcome = await client.start()
        assert welcome.session.resumed is False and client.connected
        pieces: list[str] = []
        result = await client.chat("o que tenho amanhã?", pieces.append)
    assert pieces == ["Amanhã ", "tem dentista."]
    assert result.finish == "ok" and result.error is None and result.turn_id


async def test_llm_error_comes_back_typed_and_the_client_keeps_working(tmp_path: Path) -> None:
    async with Env(tmp_path, FakeProvider([])) as env:  # sem turnos: o provider falha
        client = env.client()
        await client.start()
        result = await client.chat("oi", lambda _t: None)
        assert result.finish == "error"
        assert result.error is not None and result.error.code == "llm_unavailable"
        assert client.connected


async def test_wrong_token_is_fatal_and_not_retried(tmp_path: Path) -> None:
    async with Env(tmp_path, FakeProvider([])) as env:
        client = env.client(token="errado")
        with pytest.raises(FatalConnectionError, match="token"):
            await client.start()


async def test_server_down_raises_unavailable_after_the_wait(tmp_path: Path) -> None:
    client = RemoteClient("ws://127.0.0.1:1/v1/ws", TOKEN, device_id="x")
    with pytest.raises(ServerUnavailableError, match="indisponível"):
        await client.start(wait=0.3)


async def test_request_error_is_a_server_error(tmp_path: Path) -> None:
    async with Env(tmp_path, FakeProvider([])) as env:
        client = env.client()
        await client.start()
        with pytest.raises(ServerError) as info:
            await client.request({"type": "session.resume", "session_id": "nao-existe"})
        assert info.value.code == "session_not_found"


async def test_cancel_stops_the_turn(tmp_path: Path) -> None:
    provider = GatedProvider()
    async with Env(tmp_path, provider) as env:
        client = env.client()
        await client.start()
        task = asyncio.create_task(client.chat("conta uma história", lambda _t: None))
        await provider.started.wait()
        await client.cancel()
        result = await asyncio.wait_for(task, 3)
    assert result.finish == "cancelled"


# --- heartbeat ----------------------------------------------------------------------------


async def test_client_answers_pings_so_the_connection_lives(tmp_path: Path) -> None:
    async with Env(
        tmp_path, FakeProvider([]), heartbeat_seconds=0.05, idle_timeout_seconds=0.2
    ) as env:
        client = env.client()
        await client.start()
        await asyncio.sleep(0.6)  # três vezes o idle_timeout do servidor
        assert client.connected and not client.reconnected.is_set()


async def test_client_drops_a_connection_that_goes_silent_and_reconnects(tmp_path: Path) -> None:
    # O servidor não manda ping (heartbeat de 60 s): o cliente é quem desiste do silêncio.
    async with Env(tmp_path, FakeProvider([]), heartbeat_seconds=60) as env:
        client = env.client(idle_timeout=0.2)
        await client.start()
        async with asyncio.timeout(3):
            await client.reconnected.wait()
        assert client.connected


# --- reconexão ----------------------------------------------------------------------------


async def test_client_reconnects_after_a_restart_and_resumes_the_conversation(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([say("Prazer!"), say("Você é o Petrus.")])
    async with Env(tmp_path, provider) as env:
        statuses: list[str] = []
        client = env.client(on_status=statuses.append)
        await client.start()
        session_id = client.session_id
        await client.chat("meu nome é Petrus", lambda _t: None)

        await env.restart()
        async with asyncio.timeout(5):
            await client.reconnected.wait()
        assert client.session_id == session_id
        assert client.welcome is not None and client.welcome.session.resumed is True
        pieces: list[str] = []
        await client.chat("quem sou eu?", pieces.append)
    assert "".join(pieces) == "Você é o Petrus."
    assert any("indisponível" in s for s in statuses) and "Reconectei." in statuses
    assert any("meu nome é Petrus" in (m.content or "") for m in provider.seen[-1])


async def test_connection_dropped_mid_turn_raises_and_the_reply_is_saved_on_the_server(
    tmp_path: Path,
) -> None:
    provider = GatedProvider("resposta completa")
    async with Env(tmp_path, provider) as env:
        client = env.client()
        await client.start()
        task = asyncio.create_task(client.chat("pergunta", lambda _t: None))
        await provider.started.wait()
        await env.server.connections[0].close(1011, "queda")
        with pytest.raises(ConnectionLostError):
            await asyncio.wait_for(task, 3)

        provider.release.set()  # o turno termina no servidor, sem cliente
        await client.wait_ready(5)
        await wait_until(lambda: not env.handler._tasks)
        for _ in range(100):
            try:
                history = await client.history()
                break
            except ServerError:
                await asyncio.sleep(0.05)
    assert [(h.role, h.text) for h in history][-1] == ("assistant", "parcial resposta completa")


async def test_a_request_refused_before_the_turn_returns_instead_of_hanging(
    tmp_path: Path,
) -> None:
    """Outra conexão assumiu a conversa: o servidor responde `error` e NÃO manda `chat.end`."""
    async with Env(tmp_path, FakeProvider([say("não deveria")])) as env:
        first = env.client(device="a")
        welcome = await first.start()
        taker = env.client(device="b")
        await taker.start(p.Resume(session_id=welcome.session.id))  # assume a conversa
        await wait_until(lambda: first.detached)
        async with asyncio.timeout(3):  # antes da correção, isto esperava para sempre
            result = await first.chat("alguém aí?", lambda _t: None)
        assert result.finish == "error" and result.turn_id is None
        assert result.error is not None and result.error.code == "bad_request"
        assert first.connected  # a conexão continua boa


async def test_a_takeover_in_the_middle_of_a_turn_ends_it_instead_of_hanging(
    tmp_path: Path,
) -> None:
    provider = GatedProvider("pronto")
    async with Env(tmp_path, provider) as env:
        client = env.client()
        await client.start()
        running = asyncio.create_task(client.chat("primeira", lambda _t: None))
        await asyncio.wait_for(provider.started.wait(), 3)
        taker = env.client(device="outra")  # `juniper --resume` em outra janela
        await taker.start(p.Resume(session_id=client.session_id))
        async with asyncio.timeout(3):  # antes da correção, esperava para sempre
            result = await running
        assert result.finish == "error" and result.error is not None
        assert result.error.message == remote.DETACHED_MESSAGE
        provider.release.set()  # o turno termina no servidor, sem ninguém esperando por ele
        assert client.detached and client.connected


async def test_after_session_new_a_reconnection_never_goes_back_to_the_old_conversation(
    tmp_path: Path,
) -> None:
    """`/novo` no bot: uma queda depois dele não pode voltar à conversa antiga."""
    async with Env(tmp_path, FakeProvider([say("Velha."), say("Nova.")])) as env:
        client = env.client()
        old = (await client.start()).session.id
        await client.chat("conversa velha", lambda _t: None)
        reply = await client.request({"type": "session.new"})
        assert isinstance(reply, p.SessionState)
        assert client.session_id == reply.session_id != old  # o cliente sabe qual é a conversa
        await env.restart()  # o servidor cai e volta: o cliente reconecta sozinho
        await asyncio.wait_for(client.reconnected.wait(), 5)
        assert client.welcome is not None
        assert client.welcome.session.id != old  # (uma conversa vazia não é salva: abre outra)
        assert client.welcome.session.turns == 0


async def test_after_session_new_and_a_message_a_reconnection_resumes_the_new_one(
    tmp_path: Path,
) -> None:
    async with Env(tmp_path, FakeProvider([say("Velha."), say("Nova.")])) as env:
        client = env.client()
        old = (await client.start()).session.id
        await client.chat("conversa velha", lambda _t: None)
        reply = await client.request({"type": "session.new"})
        assert isinstance(reply, p.SessionState)
        await client.chat("conversa nova", lambda _t: None)  # agora ela fica salva
        await env.restart()
        await asyncio.wait_for(client.reconnected.wait(), 5)
        assert client.welcome is not None
        assert client.welcome.session.id == reply.session_id != old
        assert client.welcome.session.resumed is True and client.welcome.session.turns == 1
