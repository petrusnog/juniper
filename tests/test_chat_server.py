"""Chat pelo servidor: streaming, erros tipados, confirmação, sessões, queda e cancelamento
(T-076). Usa um `FakeProvider` e um cliente WebSocket de teste; nada de rede externa."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from websockets.asyncio.client import ClientConnection, connect

from brain.clients import protocol as p
from brain.clients.chat import ChatHandler
from brain.clients.server import BrainServer
from brain.core.session import Turn
from brain.llm.base import LLMProvider
from brain.llm.errors import LLMAuthError, LLMError, LLMRateLimitError, LLMUnavailableError
from brain.llm.types import LLMResponse, Message, StreamChunk
from brain.memory.errors import MemoryUnavailableError
from brain.memory.session import FileSessionStore
from brain.tools.base import Locality
from brain.tools.registry import ToolRegistry
from tests.test_agent import FakeProvider, batch_registry, say, several

TOKEN = "token-de-teste"


def server_side(registry: ToolRegistry) -> ToolRegistry:
    """`Tool.locality` defaults to EXECUTOR; on a server those go to devices, so mark them local."""
    for cls in registry.all():
        cls.locality = Locality.CLOUD
    return registry


def hello(device_id: str = "dev-1", **resume: Any) -> str:
    frame: dict[str, Any] = {
        "type": "hello",
        "protocol_version": p.PROTOCOL_VERSION,
        "client": {"kind": "cli", "version": "0.1.0", "device_id": device_id},
    }
    if resume:
        frame["resume"] = resume
    return json.dumps(frame)


class Client:
    """Cliente de teste: guarda o que chega e entrega por tipo."""

    def __init__(self, ws: ClientConnection, welcome: p.Welcome) -> None:
        self.ws = ws
        self.welcome = welcome

    async def send(self, **frame: Any) -> None:
        await self.ws.send(json.dumps(frame))

    async def recv(self) -> p.ServerMessage:
        async with asyncio.timeout(3):
            return p.parse_server_message(await self.ws.recv())

    async def until(self, kind: type[Any]) -> list[p.ServerMessage]:
        """Tudo o que chega até a primeira mensagem de `kind` (inclusive)."""
        seen: list[p.ServerMessage] = []
        while True:
            message = await self.recv()
            seen.append(message)
            if isinstance(message, kind):
                return seen

    async def chat(self, text: str, id: str = "c-1") -> list[p.ServerMessage]:
        await self.send(type="chat.send", id=id, text=text)
        return await self.until(p.ChatEnd)


async def connect_client(server: BrainServer, device_id: str = "dev-1", **resume: Any) -> Client:
    ws = await connect(
        f"ws://127.0.0.1:{server.port}/v1/ws",
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
    )
    await ws.send(hello(device_id, **resume))
    welcome = p.parse_server_message(await ws.recv())
    assert isinstance(welcome, p.Welcome)
    return Client(ws, welcome)


class Scenario:
    def __init__(self, provider: LLMProvider, tmp_path: Path, **kwargs: Any) -> None:
        self.store = FileSessionStore(tmp_path / "sessions")
        self.handler = ChatHandler(self.store, provider, **kwargs)
        self.server = BrainServer("127.0.0.1", 0, TOKEN, self.handler)

    async def __aenter__(self) -> Scenario:
        await self.server.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.server.stop()


def texts(messages: list[p.ServerMessage]) -> str:
    return "".join(m.text for m in messages if isinstance(m, p.ChatToken))


class FailingProvider(FakeProvider):
    def __init__(self, error: Exception, then: list[Any] | None = None) -> None:
        super().__init__(then or [])
        self.error = error
        self.failures = 1

    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        if self.failures:
            self.failures -= 1
            raise self.error
        async for chunk in super().stream(messages, **kwargs):
            yield chunk


class GatedProvider(LLMProvider):
    """Começa a responder e só termina quando o teste libera."""

    name: ClassVar[str] = "gated"

    def __init__(self, reply: str = "resposta completa") -> None:
        self.reply = reply
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="parcial ")
        self.started.set()
        await self.release.wait()
        yield StreamChunk(delta=self.reply, finish_reason="stop")

    async def health_check(self) -> bool:
        return True


async def wait_until(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


# --- streaming ----------------------------------------------------------------------------


async def test_message_returns_ordered_tokens_then_end(tmp_path: Path) -> None:
    async with Scenario(FakeProvider([say("Amanhã você ", "tem dentista.")]), tmp_path) as s:
        client = await connect_client(s.server)
        got = await client.chat("o que tenho amanhã?", id="c-7f3a")
    started, *tokens, end = got
    assert isinstance(started, p.ChatStarted)
    assert started.reply_to == "c-7f3a" and started.session_id == client.welcome.session.id
    assert [t.text for t in tokens if isinstance(t, p.ChatToken)] == [
        "Amanhã você ",
        "tem dentista.",
    ]
    assert all(isinstance(t, p.ChatToken) and t.turn_id == started.turn_id for t in tokens)
    assert isinstance(end, p.ChatEnd) and end.finish == "ok" and end.turn_id == started.turn_id


async def test_conversation_is_saved_on_the_server(tmp_path: Path) -> None:
    async with Scenario(FakeProvider([say("Oi!")]), tmp_path) as s:
        client = await connect_client(s.server)
        await client.chat("olá")
        saved = s.store.load(client.welcome.session.id)
    assert saved is not None
    assert [m.role for m in saved.history] == ["user", "assistant"]


async def test_second_message_while_turn_runs_gets_turn_in_progress(tmp_path: Path) -> None:
    provider = GatedProvider()
    async with Scenario(provider, tmp_path) as s:
        client = await connect_client(s.server)
        await client.send(type="chat.send", id="c-1", text="primeira")
        await provider.started.wait()
        await client.send(type="chat.send", id="c-2", text="segunda")
        errors = await client.until(p.Error)
        error = errors[-1]
        assert isinstance(error, p.Error)
        assert (error.code, error.reply_to, error.retryable) == ("turn_in_progress", "c-2", True)
        provider.release.set()
        end = (await client.until(p.ChatEnd))[-1]
        assert isinstance(end, p.ChatEnd) and end.finish == "ok"


# --- erros do LLM -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LLMAuthError("groq: LLMAuthError: 401"), "llm_auth"),
        (LLMRateLimitError("groq: LLMRateLimitError: 429"), "llm_rate_limit"),
        (LLMUnavailableError("fora do ar"), "llm_unavailable"),
        (LLMError("outra coisa"), "llm_error"),
    ],
)
async def test_llm_errors_reach_the_client_typed_and_the_connection_survives(
    tmp_path: Path, error: Exception, code: str
) -> None:
    async with Scenario(FailingProvider(error, [say("Voltei!")]), tmp_path) as s:
        client = await connect_client(s.server)
        got = await client.chat("oi", id="c-1")
        err = next(m for m in got if isinstance(m, p.Error))
        assert (err.code, err.reply_to) == (code, "c-1")
        assert "Não consegui falar com nenhum LLM" in err.message
        assert isinstance(got[-1], p.ChatEnd) and got[-1].finish == "error"
        # a conexão continua aberta e a conversa não ficou pela metade
        again = await client.chat("de novo", id="c-2")
        assert texts(again) == "Voltei!"


async def test_unexpected_failure_hides_details_from_the_client(tmp_path: Path) -> None:
    async with Scenario(FailingProvider(RuntimeError("segredo-interno")), tmp_path) as s:
        client = await connect_client(s.server)
        got = await client.chat("oi")
    err = next(m for m in got if isinstance(m, p.Error))
    assert err.code == "internal" and "segredo-interno" not in err.message


async def test_turn_timeout_ends_with_error(tmp_path: Path) -> None:
    provider = GatedProvider()
    async with Scenario(provider, tmp_path, turn_timeout=0.1) as s:
        client = await connect_client(s.server)
        got = await client.chat("oi")
    err = next(m for m in got if isinstance(m, p.Error))
    assert err.code == "llm_timeout"
    assert isinstance(got[-1], p.ChatEnd) and got[-1].finish == "error"


# --- confirmação (ADR-010) ----------------------------------------------------------------


async def test_confirmation_is_the_next_message(tmp_path: Path) -> None:
    registry, ran = batch_registry()
    server_side(registry)
    provider = FakeProvider(
        [
            several(("wipe", {"id": "1"})),
            say("Posso apagar o item 1? É irreversível."),
            say("Apagado!"),
        ]
    )
    async with Scenario(provider, tmp_path, tool_registry=registry) as s:
        client = await connect_client(s.server)
        first = await client.chat("apaga o item 1")
        assert "Posso apagar" in texts(first) and ran == []
        second = await client.chat("Sim.", id="c-2")
    assert texts(second) == "Apagado!" and ran == ["wipe:1"]


async def test_pending_confirmation_does_not_survive_a_reconnection(tmp_path: Path) -> None:
    registry, ran = batch_registry()
    server_side(registry)
    provider = FakeProvider(
        [several(("wipe", {"id": "1"})), say("Posso apagar o item 1?"), say("Não executei nada.")]
    )
    async with Scenario(provider, tmp_path, tool_registry=registry) as s:
        client = await connect_client(s.server)
        await client.chat("apaga o item 1")
        await client.ws.close()
        await wait_until(lambda: not s.server.connections)
        again = await connect_client(s.server, latest=True)
        assert again.welcome.session.resumed is True
        await again.chat("Sim.", id="c-2")
    assert ran == []  # sem um novo "sim" depois de retomar, nada executa


# --- sessões ------------------------------------------------------------------------------


async def test_hello_resume_latest_continues_the_previous_conversation(tmp_path: Path) -> None:
    provider = FakeProvider([say("Prazer, Petrus."), say("Você é o Petrus.")])
    async with Scenario(provider, tmp_path) as s:
        first = await connect_client(s.server)
        await first.chat("meu nome é Petrus")
        await first.ws.close()
        await wait_until(lambda: not s.server.connections)

        second = await connect_client(s.server, latest=True)
        assert second.welcome.session.resumed is True
        assert second.welcome.session.id == first.welcome.session.id
        assert second.welcome.session.turns == 1
        assert texts(await second.chat("quem sou eu?")) == "Você é o Petrus."
    last_request = provider.seen[-1]
    assert any("meu nome é Petrus" in (m.content or "") for m in last_request)


async def test_resume_latest_without_history_starts_a_new_conversation(tmp_path: Path) -> None:
    async with Scenario(FakeProvider([]), tmp_path) as s:
        client = await connect_client(s.server, latest=True)
    assert client.welcome.session.resumed is False and client.welcome.session.turns == 0


async def test_session_new_resume_and_list(tmp_path: Path) -> None:
    provider = FakeProvider([say("Resposta A."), say("Resposta B.")])
    async with Scenario(provider, tmp_path) as s:
        client = await connect_client(s.server)
        first_id = client.welcome.session.id
        await client.chat("assunto A")

        await client.send(type="session.new", id="s-1")
        state = (await client.until(p.SessionState))[-1]
        assert isinstance(state, p.SessionState)
        assert state.reply_to == "s-1" and state.session_id != first_id and state.turns == 0
        second_id = state.session_id
        await client.chat("assunto B", id="c-2")

        await client.send(type="session.list", id="s-2", limit=10)
        listed = (await client.until(p.SessionListed))[-1]
        assert isinstance(listed, p.SessionListed)
        assert [x.id for x in listed.sessions] == [second_id, first_id]
        assert listed.sessions[0].preview.endswith("assunto B")

        await client.send(type="session.resume", id="s-3", session_id=first_id)
        back = (await client.until(p.SessionState))[-1]
        assert isinstance(back, p.SessionState) and back.session_id == first_id
        assert back.history is not None
        assert [(h.role, h.text) for h in back.history] == [
            ("user", "assunto A"),
            ("assistant", "Resposta A."),
        ]
        assert "Retomando a conversa" in back.note


async def test_session_resume_unknown_id_is_session_not_found(tmp_path: Path) -> None:
    async with Scenario(FakeProvider([]), tmp_path) as s:
        client = await connect_client(s.server)
        for bad in ("nao-existe", "../../etc/passwd"):
            await client.send(type="session.resume", id="s-1", session_id=bad)
            err = (await client.until(p.Error))[-1]
            assert isinstance(err, p.Error) and err.code == "session_not_found"


async def test_resuming_a_conversation_held_by_another_connection_takes_it(
    tmp_path: Path,
) -> None:
    async with Scenario(FakeProvider([say("Ok.")]), tmp_path) as s:
        old = await connect_client(s.server, "dev-old")
        await old.chat("oi")
        session_id = old.welcome.session.id
        new = await connect_client(s.server, "dev-new", session_id=session_id)
        assert new.welcome.session.resumed is True
        detached = await old.recv()
        assert isinstance(detached, p.SessionDetached) and detached.session_id == session_id
        # a antiga já não manda nesta conversa
        await old.send(type="chat.send", id="c-9", text="ainda aqui?")
        err = (await old.until(p.Error))[-1]
        assert isinstance(err, p.Error) and err.code == "bad_request"


# --- cancelamento e queda do cliente ------------------------------------------------------


async def test_cancel_stops_generation_and_drops_the_partial_turn(tmp_path: Path) -> None:
    provider = GatedProvider()
    async with Scenario(provider, tmp_path) as s:
        client = await connect_client(s.server)
        await client.send(type="chat.send", id="c-1", text="conta uma história")
        started = (await client.until(p.ChatStarted))[-1]
        assert isinstance(started, p.ChatStarted)
        await provider.started.wait()
        await client.send(type="chat.cancel", turn_id=started.turn_id)
        got = await client.until(p.ChatEnd)
        end = got[-1]
        assert isinstance(end, p.ChatEnd) and end.finish == "cancelled"
        assert not any(isinstance(m, p.Error) for m in got)
        # o texto parcial não virou histórico
        conversation = s.handler._live[client.welcome.session.id]
        assert conversation.agent.session.history == []
        assert not conversation.busy
        # e dá para conversar de novo
        provider.release.set()
        again = await client.chat("e agora?", id="c-2")
        assert isinstance(again[-1], p.ChatEnd) and again[-1].finish == "ok"


async def test_cancel_of_an_unknown_turn_is_harmless(tmp_path: Path) -> None:
    async with Scenario(FakeProvider([say("Oi.")]), tmp_path) as s:
        client = await connect_client(s.server)
        await client.send(type="chat.cancel", turn_id="t-nao-existe")
        assert texts(await client.chat("oi")) == "Oi."


async def test_disconnect_mid_turn_finishes_and_saves_the_conversation(tmp_path: Path) -> None:
    provider = GatedProvider("resposta que ninguém leu")
    async with Scenario(provider, tmp_path) as s:
        client = await connect_client(s.server)
        session_id = client.welcome.session.id
        await client.send(type="chat.send", id="c-1", text="pergunta")
        await provider.started.wait()
        await client.ws.close()
        await wait_until(lambda: not s.server.connections)
        provider.release.set()
        await wait_until(lambda: not s.handler._live)  # o turno acabou e a conversa foi liberada
        saved = s.store.load(session_id)
        assert saved is not None
        assert [m.role for m in saved.history] == ["user", "assistant"]
        assert saved.history[-1].content == "parcial resposta que ninguém leu"

        # ao retomar, o histórico traz a resposta completa
        back = await connect_client(s.server, "dev-2", session_id=session_id)
        await back.send(type="session.resume", id="s-1", session_id=session_id)
        state = (await back.until(p.SessionState))[-1]
        assert isinstance(state, p.SessionState) and state.history is not None
        assert state.history[-1].text == "parcial resposta que ninguém leu"


class FakeMemory:
    """Memória de longo prazo de mentira: só registra o que a extração recebeu."""

    def __init__(self) -> None:
        self.extracted: list[tuple[list[Turn], str | None]] = []
        self.fail = False

    async def context_for(self, message: str, previous: str | None = None) -> Any:
        from brain.memory.retrieval import RecalledContext

        return RecalledContext()

    async def extract(
        self, turns: list[Turn], session_id: str | None = None, context: int = 0
    ) -> Any:
        if self.fail:
            raise MemoryUnavailableError("mongo fora")
        self.extracted.append((list(turns), session_id))
        from brain.memory.extraction import ExtractionResult

        return ExtractionResult()


@pytest.mark.parametrize("background", [True, False])
async def test_memory_extraction_finishes_even_if_the_client_leaves(
    tmp_path: Path, background: bool
) -> None:
    memory = FakeMemory()
    provider = FakeProvider([say("Anotado: você gosta de café.")])
    async with Scenario(
        provider,
        tmp_path,
        memory=memory,
        background_extraction=background,
    ) as s:
        client = await connect_client(s.server)
        session_id = client.welcome.session.id
        await client.chat("eu gosto de café com leite")
        await client.ws.close()
        await wait_until(lambda: len(memory.extracted) == 1)
    turns, extracted_session = memory.extracted[0]
    assert extracted_session == session_id
    assert [(t.user, t.assistant) for t in turns] == [
        ("eu gosto de café com leite", "Anotado: você gosta de café.")
    ]


async def test_memory_failure_after_the_client_left_does_not_break_the_server(
    tmp_path: Path,
) -> None:
    memory = FakeMemory()
    memory.fail = True
    async with Scenario(
        FakeProvider([say("Ok."), say("Oi de novo.")]),
        tmp_path,
        memory=memory,
    ) as s:
        client = await connect_client(s.server)
        await client.chat("guarde isto")
        await client.ws.close()
        await wait_until(lambda: not s.handler._live and not s.handler._tasks)
        other = await connect_client(s.server, "dev-2")
        assert texts(await other.chat("oi")) == "Oi de novo."


async def test_server_stop_concludes_pending_extraction(tmp_path: Path) -> None:
    memory = FakeMemory()
    s = Scenario(FakeProvider([say("Certo.")]), tmp_path, memory=memory)
    async with s:
        client = await connect_client(s.server)
        await client.chat("lembra que moro em Recife")
    assert len(memory.extracted) == 1


# --- montagem do servidor -----------------------------------------------------------------


async def test_open_chat_handler_builds_and_releases_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients.chat import open_chat_handler

    monkeypatch.setenv("JUNIPER_GROQ_API_KEY", "")
    async with open_chat_handler() as handler:
        assert isinstance(handler, ChatHandler)
        server = BrainServer("127.0.0.1", 0, TOKEN, handler)
        await server.start()
        client = await connect_client(server)
        assert client.welcome.session.resumed is False
        await server.stop()


def test_serve_refuses_to_start_in_local_mode() -> None:
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.config import settings

    assert settings.deployment.mode == "local"
    result = CliRunner().invoke(app, ["serve"])
    assert result.exit_code == 1
    assert "deployment.mode: server" in result.output


# --- aquecimento do modelo de embeddings --------------------------------------------------


class SlowEmbedder:
    """Um modelo que demora a carregar: `dimension` só volta quando o teste libera."""

    def __init__(self) -> None:
        self.gate = threading.Event()

    @property
    def dimension(self) -> int:
        self.gate.wait(5)
        return 384


class WarmableMemory(FakeMemory):
    def __init__(self) -> None:
        super().__init__()
        self.embedder = SlowEmbedder()
        self.recalls: list[str] = []

    def needs_model_download(self) -> bool:
        return True

    async def context_for(self, message: str, previous: str | None = None) -> Any:
        from brain.memory.retrieval import RecalledContext

        self.recalls.append(message)
        return RecalledContext()


async def test_chat_does_not_wait_for_the_embedding_model_download(tmp_path: Path) -> None:
    memory = WarmableMemory()
    provider = FakeProvider([say("Já respondo."), say("Agora com memória.")])
    async with Scenario(provider, tmp_path, memory=memory) as s:  # type: ignore[arg-type]
        warm_up = asyncio.create_task(s.handler.warm_up())
        client = await connect_client(s.server)
        # o modelo ainda está "baixando": a conversa responde na hora, sem consultar a memória
        assert texts(await client.chat("você se lembra de mim?")) == "Já respondo."
        assert memory.recalls == [] and not s.handler.memory_ready

        memory.embedder.gate.set()  # o download terminou
        await asyncio.wait_for(warm_up, 3)
        assert s.handler.memory_ready
        assert texts(await client.chat("e agora?", id="c-2")) == "Agora com memória."
    assert len(memory.recalls) == 1


async def test_failed_warm_up_retries_and_never_takes_the_server_down(tmp_path: Path) -> None:
    memory = WarmableMemory()
    attempts = 0

    class Flaky(SlowEmbedder):
        @property
        def dimension(self) -> int:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("sem rede")
            return 384

    memory.embedder = Flaky()
    async with Scenario(FakeProvider([]), tmp_path, memory=memory) as s:  # type: ignore[arg-type]
        await asyncio.wait_for(s.handler.warm_up(retry_seconds=0.01), 3)
        assert attempts == 2 and s.handler.memory_ready
