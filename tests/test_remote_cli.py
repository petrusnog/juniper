"""O `juniper` no modo `server` (T-086): chat, --resume, memória, tools e queda de conexão.

O servidor roda numa thread própria (o CLI usa `asyncio.run`, que não convive com o laço do
teste). Nada de rede externa, LLM ou MongoDB reais.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients import cli, remote, remote_cli
from brain.clients.chat import ChatHandler
from brain.clients.server import BrainServer
from brain.config import ExtractionSettings, RetrievalSettings, settings
from brain.llm.types import ToolCall
from brain.memory.extraction import MemoryExtractor
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoProfileStore, MongoVectorStore
from brain.memory.session import FileSessionStore
from brain.tools import memory as memory_tools
from brain.tools.registry import registry
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import GatedProvider
from tests.test_extraction import MODEL, FakeEmbedder, ScriptedLLM
from tests.test_vector_store import FakeConnection

TOKEN = "token-do-cli"
runner = CliRunner(env={"COLUMNS": "250"})  # tabelas sem quebra de linha


class LiveServer:
    """BrainServer + ChatHandler vivendo numa thread, para o CLI (síncrono) falar com ele."""

    def __init__(self, handler: ChatHandler, **kwargs: Any) -> None:
        self.handler = handler
        self.loop = asyncio.new_event_loop()
        self.server = BrainServer("127.0.0.1", 0, TOKEN, handler, **kwargs)
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self) -> LiveServer:
        self._thread.start()
        self.run(self.server.start())
        return self

    def __exit__(self, *exc: object) -> None:
        self.run(self.server.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=3)

    def run(self, work: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(work, self.loop).result(timeout=10)

    @property
    def port(self) -> int:
        return self.server.port


@pytest.fixture(autouse=True)
def server_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """O CLI aponta para o servidor de teste; token e id do dispositivo ficam no tmp."""
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n")
    monkeypatch.setattr(settings.deployment, "mode", "server")
    monkeypatch.setattr(settings.deployment.server, "host", "127.0.0.1")
    monkeypatch.setattr(settings.deployment.auth, "token_file", token)
    monkeypatch.setattr(settings.paths, "home", tmp_path)  # type: ignore[union-attr]
    monkeypatch.setattr(remote, "BACKOFF_SECONDS", (0.05, 0.1))
    monkeypatch.setattr(remote_cli, "CONNECT_WAIT_SECONDS", 1.0)
    return token


def serve(tmp_path: Path, provider: Any, **handler_kwargs: Any) -> LiveServer:
    store = FileSessionStore(tmp_path / "sessions")
    return LiveServer(ChatHandler(store, provider, **handler_kwargs))


def jun(*args: str, input: str | None = None) -> Any:
    return runner.invoke(cli.app, list(args), input=input)


# --- chat ---------------------------------------------------------------------------------


def test_message_gets_a_streamed_reply_from_the_remote_brain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with serve(tmp_path, FakeProvider([say("Amanhã ", "tem dentista.")])) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun("o que tenho amanhã?")
    assert result.exit_code == 0, result.output
    assert "JUNIPER:" in result.output and "Amanhã tem dentista." in result.output


def test_interactive_chat_over_the_remote_brain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider([say("Oi!"), say("Tudo bem.")])
    with serve(tmp_path, provider) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun(input="olá\ncomo vai?\n/sair\n")
    assert result.exit_code == 0, result.output
    assert "Oi!" in result.output and "Tudo bem." in result.output
    assert "/sair para encerrar" in result.output and "Até logo!" in result.output
    # o histórico vive no servidor: a segunda mensagem chegou ao LLM com a primeira
    assert any("olá" in (m.content or "") for m in provider.seen[-1])


def test_resume_continues_the_previous_conversation_through_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider([say("Prazer, Petrus."), say("Você é o Petrus.")])
    with serve(tmp_path, provider) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        assert jun("meu nome é Petrus").exit_code == 0
        result = jun("--resume", "quem sou eu?")
    assert result.exit_code == 0, result.output
    assert "Retomando a conversa" in result.output and "1 mensagem suas" in result.output
    assert "Você é o Petrus." in result.output
    assert any("meu nome é Petrus" in (m.content or "") for m in provider.seen[-1])


def test_resume_without_history_says_so_and_starts_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with serve(tmp_path, FakeProvider([say("Oi.")])) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun("--resume", "oi")
    assert "Não achei nenhuma conversa anterior" in result.output and "Oi." in result.output


def test_confirmation_happens_in_the_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_agent import batch_registry, several
    from tests.test_chat_server import server_side

    reg, ran = batch_registry()
    server_side(reg)
    provider = FakeProvider(
        [
            several(("wipe", {"id": "1"})),
            say("Posso apagar o item 1? É irreversível."),
            say("Apagado!"),
        ]
    )
    with serve(tmp_path, provider, tool_registry=reg) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun(input="apaga o item 1\nsim\n/sair\n")
    assert "Posso apagar o item 1?" in result.output and "Apagado!" in result.output
    assert ran == ["wipe:1"]


def test_llm_failure_is_shown_as_a_friendly_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with serve(tmp_path, FakeProvider([])) as live:  # sem turnos: o LLM "cai"
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun("oi")
    assert result.exit_code == 1
    assert "Não consegui falar com nenhum LLM" in result.output
    assert "Traceback" not in result.output


# --- servidor fora do ar / token ----------------------------------------------------------


def test_server_unavailable_is_a_friendly_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.deployment.server, "port", 1)  # nada escuta aqui
    monkeypatch.setattr(remote_cli, "CONNECT_WAIT_SECONDS", 0.3)
    result = jun("oi")
    assert result.exit_code == 1
    assert "Juniper indisponível no momento" in result.output
    assert "Traceback" not in result.output


def test_missing_token_file_explains_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.deployment.auth, "token_file", tmp_path / "nao-existe")
    result = jun("oi")
    assert result.exit_code == 1 and "token do servidor" in result.output


def test_wrong_token_is_reported_without_retrying_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server_mode: Path
) -> None:
    server_mode.write_text("token-errado\n")
    with serve(tmp_path, FakeProvider([])) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun("oi")
    assert result.exit_code == 1 and "recusou o token" in result.output


# --- queda no meio do turno ---------------------------------------------------------------


def test_connection_dropped_mid_turn_reconnects_and_shows_the_saved_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = GatedProvider("resposta completa")
    results: list[Any] = []
    with serve(tmp_path, provider) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        worker = threading.Thread(target=lambda: results.append(jun("pergunta")), daemon=True)
        worker.start()
        try:
            live.run(_wait(provider.started))
        except Exception:
            worker.join(timeout=1)
            raise AssertionError(results[0].output if results else "sem saída")
        live.run(_close_all(live.server))  # a conexão cai com o turno em andamento
        live.loop.call_soon_threadsafe(provider.release.set)  # o servidor termina sozinho
        worker.join(timeout=20)
    (result,) = results
    assert "A conexão caiu no meio da resposta" in result.output
    assert "A resposta ficou pronta no servidor" in result.output
    assert "resposta completa" in result.output
    assert "Traceback" not in result.output


async def _wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), 5)


async def _close_all(server: BrainServer) -> None:
    for conn in server.connections:
        await conn.close(1011, "queda")


# --- memória pelo servidor ----------------------------------------------------------------


@pytest.fixture
def memory() -> Iterator[LongTermMemory]:
    store = MongoVectorStore(FakeConnection(), embedding_model=MODEL)
    extractor = MemoryExtractor(
        ScriptedLLM(judge={"action": "new", "target": None}),
        FakeEmbedder(),
        store,
        ExtractionSettings(neighbor_threshold=0.3),
    )
    lt = LongTermMemory(store, FakeEmbedder(), extractor, MongoProfileStore(store.connection))
    memory_tools.bind(lt)
    registry.discover()
    yield lt
    memory_tools.bind(None)


@pytest.fixture(autouse=True)
def _retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.memory, "retrieval", RetrievalSettings(top_k=5, threshold=0.4))


def test_something_said_in_one_client_is_recalled_from_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: LongTermMemory
) -> None:
    from brain.tools.memory import Remember

    assert Remember.confirm is False
    provider = FakeProvider(
        [
            (
                (),
                (ToolCall("m1", "remember", {"text": "O Petrus mora em Recife.", "type": "fact"}),),
            ),
            say("Guardei."),
        ]
    )
    with serve(tmp_path, provider, memory=memory) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        assert jun("lembra que moro em Recife").exit_code == 0  # cliente 1: conversa
        listed = jun("memory", "list")  # cliente 2: outra conexão, outro processo
        found = jun("memory", "search", "onde o Petrus mora Recife")
    assert listed.exit_code == 0, listed.output
    assert "O Petrus mora em Recife." in listed.output and "1 memória(s)" in listed.output
    assert "O Petrus mora em Recife." in found.output


def test_memory_commands_use_the_server_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: LongTermMemory
) -> None:
    from tests.test_memory_server import seed

    kept = seed(memory.store, "O projeto X usa Laravel 11.")  # type: ignore[arg-type]
    with serve(tmp_path, FakeProvider([]), memory=memory) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        listed = jun("memory", "list")
        exported = jun("memory", "export")
        profile_set = jun(
            "memory", "profile", "set", "O usuário se chama Petrus.", "--name", "Petrus"
        )
        profile = jun("memory", "profile")
        missing = jun("memory", "forget", "mem_nao_existe", "--yes")
        forgotten = jun("memory", "forget", kept.id, "--yes")
        gone = jun("memory", "list")
        profile_gone = jun("memory", "profile", "forget", "--yes")
    assert kept.id in listed.output and "Laravel 11" in listed.output
    assert kept.id in exported.output and '"profile"' in exported.output
    assert "Perfil guardado" in profile_set.output
    assert "Petrus" in profile.output
    assert missing.exit_code == 1 and "Não achei a memória" in missing.output
    assert "Esqueci." in forgotten.output
    assert "Ainda não guardei nenhuma memória" in gone.output
    assert "Esqueci o seu perfil." in profile_gone.output


def test_forget_confirmation_stays_on_the_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: LongTermMemory
) -> None:
    from tests.test_memory_server import seed

    kept = seed(memory.store, "Moro em Recife.", "fact")  # type: ignore[arg-type]
    with serve(tmp_path, FakeProvider([]), memory=memory) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        declined = jun("memory", "forget", kept.id, input="n\n")
        assert "não apaguei nada" in declined.output
        assert memory.store.get(kept.id) is not None
        accepted = jun("memory", "forget", kept.id, input="y\n")
    assert "Pode apagar?" in declined.output and "Esqueci." in accepted.output
    assert memory.store.get(kept.id) is None


def test_memory_unavailable_on_the_server_is_a_friendly_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with serve(tmp_path, FakeProvider([]), memory=None) as live:  # servidor sem memória
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun("memory", "list")
    assert result.exit_code == 1 and "não está disponível neste servidor" in result.output


# --- tools e comandos locais --------------------------------------------------------------


def test_tools_lists_the_servers_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry.discover()
    with serve(tmp_path, FakeProvider([])) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun("tools")
    assert result.exit_code == 0, result.output
    assert "create_event" in result.output and "cloud" in result.output


def test_local_commands_work_without_a_server() -> None:
    result = jun("version")
    assert result.exit_code == 0 and "versão" in result.output


def test_local_mode_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.deployment, "mode", "local")
    assert not remote_cli.remote_mode()
    result = jun("tools")
    assert result.exit_code == 0 and "create_event" in result.output


# --- token em claro --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "trusted"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("192.168.0.10", True),
        ("100.101.102.103", True),  # faixa do Tailscale
        ("juniper-brain", True),  # MagicDNS curto
        ("juniper-brain.tail1234.ts.net", True),
        ("8.8.8.8", False),  # internet
        ("brain.example.com", False),
        ("100.63.255.255", False),  # logo abaixo da faixa do Tailscale
    ],
)
def test_plaintext_warning_only_for_hosts_outside_private_networks(
    host: str, trusted: bool
) -> None:
    assert remote_cli.is_trusted_host(host) is trusted


def test_warning_text_is_shown_for_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.deployment.server, "host", "8.8.8.8")
    assert "sem cifra" in (remote_cli.plaintext_warning() or "")
    monkeypatch.setattr(settings.deployment.server, "host", "0.0.0.0")
    assert remote_cli.plaintext_warning() is None
