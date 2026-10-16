"""O CLI no modo `server` (T-086): conversa e comandos pelo brain remoto.

Com `deployment.mode: server`, o `juniper` não monta o `Agent` no próprio processo: abre um
WebSocket com o servidor (`brain.clients.remote`) e só entrega texto e mostra texto. A conversa,
as memórias e a confirmação ("Posso apagar?" / "sim") ficam no servidor.

Este módulo é carregado só nesse modo (o `cli` o importa na hora de usar) e usa o `Printer` e o
`LineReader` do `cli`, para a aparência ser a mesma de hoje.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import signal
import socket
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from brain.clients import cli, voice
from brain.clients import protocol as p
from brain.clients.remote import (
    UNAVAILABLE_MESSAGE,
    ConnectionLostError,
    FatalConnectionError,
    RemoteClient,
    RemoteError,
    ServerError,
    ServerUnavailableError,
)
from brain.config import settings
from brain.core.resume import NO_PREVIOUS_NOTE
from brain.memory.errors import MemoryUnavailableError
from brain.memory.profile import Profile, ProfileError
from brain.memory.vector import Memory, SearchHit
from brain.timing import summarize_turn
from brain.trace import message_trace, quote

T = TypeVar("T")

CONNECT_WAIT_SECONDS = 15.0
RECOVER_WAIT_SECONDS = 60.0
# A primeira busca baixa o modelo de embeddings (~120 MB) no servidor.
MEMORY_TIMEOUT_SECONDS = 300.0


def remote_mode() -> bool:
    return settings.deployment.mode == "server"


# ---------------------------------------------------------------------------------------------
# Conexão: endereço, token e identidade
# ---------------------------------------------------------------------------------------------


def server_url() -> str:
    host = settings.deployment.server.host
    if host in ("", "0.0.0.0", "::"):  # endereço de escuta do servidor, não de destino
        host = "127.0.0.1"
    return f"ws://{host}:{settings.deployment.server.port}/v1/ws"


def read_token() -> str:
    path = settings.deployment.auth.token_file.expanduser()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        raise FatalConnectionError(
            f"Não achei o token do servidor em {path}. Copie o token que o servidor criou "
            "(deployment.auth.token_file) para esse arquivo."
        )
    return token


def installation_id() -> str:
    """Estável por instalação: guardado em `~/.juniper/device_id`."""
    home = settings.paths.home if settings.paths is not None else Path.home() / ".juniper"
    path = home / "device_id"
    try:
        found = path.read_text(encoding="utf-8").strip()
        if found:
            return found
    except OSError:
        pass
    value = secrets.token_hex(6)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
    except OSError:
        pass  # sem onde gravar: vale o desta execução
    return value


def make_client(on_status: Callable[[str], None] | None = None) -> RemoteClient:
    # Cada processo do CLI é uma conexão própria: com o mesmo device_id, a segunda substituiria
    # a primeira (ADR-012, seção 3), e `juniper memory list` derrubaria o chat aberto.
    device_id = f"{installation_id()}-cli-{secrets.token_hex(2)}"
    return RemoteClient(
        server_url(),
        read_token(),
        device_id=device_id,
        device_name=socket.gethostname(),
        on_status=on_status,
    )


# ---------------------------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------------------------


def _status_printer(printer: cli.Printer) -> Callable[[str], None]:
    """Mostra "indisponível" uma vez por queda (não a cada tentativa) e "Reconectei."."""
    down = False

    def status(text: str) -> None:
        nonlocal down
        if text == UNAVAILABLE_MESSAGE:
            if down:
                return
            down = True
            printer.say(f"{text} Vou tentando reconectar.", "yellow")
        else:
            down = False
            printer.say(text)

    return status


async def chat(
    message: str | None, resume: bool, printer: cli.Printer, reader: cli.LineReader
) -> int:
    """`juniper` / `juniper <mensagem>` / `--resume`, falando com o brain do servidor."""
    try:
        client = make_client(_status_printer(printer))
        welcome = await client.start(
            p.Resume(latest=True) if resume else None, CONNECT_WAIT_SECONDS
        )
    except FatalConnectionError as exc:
        printer.say(str(exc), "red")
        return 1
    except ServerUnavailableError as exc:
        printer.say(str(exc), "red")
        return 1
    try:
        if resume:
            await _announce_resume(client, welcome, printer)
        if message is None:
            printer.say("Oi! Pode falar, tô te ouvindo. (/sair para encerrar)")
            cli.console.print()
        return await converse(client, printer, reader, message, welcome.session.turns)
    finally:
        await client.close()


async def _announce_resume(client: RemoteClient, welcome: p.Welcome, printer: cli.Printer) -> None:
    if not welcome.session.resumed:
        printer.say(NO_PREVIOUS_NOTE)
        return
    try:
        reply = await client.request({"type": "session.resume", "session_id": welcome.session.id})
    except RemoteError:
        return
    if isinstance(reply, p.SessionState) and reply.note:
        printer.say(reply.note)


async def converse(
    client: RemoteClient,
    printer: cli.Printer,
    reader: cli.LineReader,
    message: str | None,
    known_turns: int = 0,
) -> int:
    """Um turno por mensagem. Sem `message`, é o REPL. `known_turns`: mensagens suas já salvas."""
    pending = message
    while True:
        if pending is None:
            try:
                text = (await reader.read(voice.user_prompt())).strip()
            except EOFError:
                printer.out.print()
                printer.say("Até logo!")
                return 0
            if text in cli._EXIT:
                printer.say("Até logo!")
                return 0
            if not text:
                continue
        else:
            text = pending
        code = await _turn(client, printer, text, known_turns)
        known_turns += 1
        if message is not None:
            return code
        printer.out.print()
        pending = None


async def _turn(client: RemoteClient, printer: cli.Printer, text: str, known_turns: int) -> int:
    """Manda `text` e mostra a resposta em stream. Ctrl+C cancela o turno, não fecha o chat."""
    message_trace.begin(f"conversa {client.session_id} · modo server ({client._url})")
    message_trace.step(
        "Terminal (CLI)",
        f"Você digitou {quote(text)}. No modo server o terminal só repassa o texto ao cliente "
        "remoto: quem pensa, lembra e responde é o servidor.",
    )
    printer.begin_reply()
    loop = asyncio.get_running_loop()
    interrupted = False
    shown: list[str] = []

    def show(piece: str) -> None:
        shown.append(piece)
        printer.token(piece)

    def on_sigint() -> None:
        nonlocal interrupted
        interrupted = True
        loop.create_task(client.cancel())

    with contextlib.suppress(NotImplementedError, ValueError, RuntimeError):
        loop.add_signal_handler(signal.SIGINT, on_sigint)
    try:
        result = await client.chat(text, show)
    except ServerUnavailableError as exc:
        printer.end_line()
        printer.say(f"{exc} Tenta de novo daqui a pouco.", "red")
        return 1
    except FatalConnectionError as exc:
        printer.end_line()
        printer.say(str(exc), "red")
        return 1
    except ConnectionLostError:
        printer.end_line()
        printer.say("A conexão caiu no meio da resposta. Reconectando...", "yellow")
        await _recover(client, printer, known_turns)
        return 1
    finally:
        with contextlib.suppress(NotImplementedError, ValueError, RuntimeError):
            loop.remove_signal_handler(signal.SIGINT)
    printer.end_line()
    message_trace.step(
        "Terminal (CLI)",
        f"Mostrei a resposta no terminal, pedaço a pedaço: {quote(''.join(shown), 1500)}"
        if shown
        else "Não havia texto de resposta para mostrar.",
    )
    if result.error is not None:
        printer.say(result.error.message, "red")
    elif result.finish == "cancelled" or interrupted:
        printer.say("Parei por aqui.")
    elif result.finish == "error":
        printer.say("Não consegui terminar essa resposta.", "red")
    if result.timing:  # o servidor só manda se agent.show_timing estiver ligado
        total = int(result.timing.get("total_ms", 0))
        printer.timing(summarize_turn(total, dict(result.timing.get("stages_ms", {})), {}))
    return 0 if result.finish == "ok" else 1


async def _recover(client: RemoteClient, printer: cli.Printer, turns_before: int) -> None:
    """Depois de uma queda no meio do turno: espera reconectar e mostra a resposta que ficou salva.

    O turno termina no servidor mesmo sem o cliente (ADR-012, seção 11), então a resposta
    completa está no histórico da conversa.
    """
    try:
        await client.wait_ready(RECOVER_WAIT_SECONDS)
    except ServerUnavailableError:
        printer.say(
            f"{UNAVAILABLE_MESSAGE} Quando voltar, use --resume para ver a resposta.", "red"
        )
        return
    for _ in range(60):  # o turno pode ainda estar terminando no servidor
        try:
            history = await client.history()
            break
        except ServerError as exc:
            if exc.code != "turn_in_progress":
                return
            await asyncio.sleep(1)
        except RemoteError:
            return
    else:
        printer.say("Ainda estou respondendo no servidor. Daqui a pouco use --resume.", "yellow")
        return
    users = sum(1 for item in history if item.role == "user")
    if history and history[-1].role == "assistant" and users > turns_before:
        printer.say("A resposta ficou pronta no servidor:")
        printer.begin_reply()
        printer.token(history[-1].text)
        printer.end_line()


# ---------------------------------------------------------------------------------------------
# Comandos de memória: o mesmo contrato do `LongTermMemory`, falado com o servidor
# ---------------------------------------------------------------------------------------------


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _error(exc: RemoteError, op: str) -> Exception:
    """O erro do servidor, na forma que os comandos de memória já sabem tratar."""
    if isinstance(exc, ServerError) and exc.code == "bad_request":
        return ProfileError(str(exc)) if op.startswith("profile") else ValueError(str(exc))
    return MemoryUnavailableError(str(exc))


class _RemoteStore:
    """A parte síncrona do `store` que os comandos usam (`list_memories`, `get`)."""

    def __init__(self, memory: RemoteMemory) -> None:
        self._memory = memory

    def list_memories(
        self, memory_type: str | None = None, include_superseded: bool = False
    ) -> list[Memory]:
        args: dict[str, Any] = {"all": include_superseded}
        if memory_type is not None:
            args["type"] = memory_type
        data = self._memory.call({"type": "memory.request", "op": "list", "args": args})
        return [_memory(row) for row in data["memories"]]

    def get(self, memory_id: str) -> Memory | None:
        return next((m for m in self.list_memories(None, True) if m.id == memory_id), None)


def _memory(row: dict[str, Any]) -> Memory:
    created = (
        _moment(row["created_at"]) if row.get("created_at") else datetime.fromtimestamp(0, UTC)
    )
    return Memory(
        text=row["text"],
        type=row["type"],
        embedding=[],
        embedding_model="",
        id=row["id"],
        status=row.get("status", "active"),
        created_at=created,
        updated_at=created,
    )


def _profile(row: dict[str, Any] | None) -> Profile | None:
    if row is None:
        return None
    return Profile(
        text=row["text"],
        fields=dict(row.get("fields") or {}),
        updated_at=_moment(row["updated_at"]),
    )


class RemoteMemory:
    """Faz de conta de `LongTermMemory` para os comandos `juniper memory ...` no modo server.

    Cada chamada vira um `memory.request`. Roda o cliente num laço de eventos próprio (numa
    thread), porque os comandos são síncronos e misturam `asyncio.run` com chamadas diretas.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._client: RemoteClient | None = None
        self._export: dict[str, Any] | None = None
        self.store = _RemoteStore(self)

    def _run(self, work: Awaitable[T]) -> T:
        future = asyncio.run_coroutine_threadsafe(_wrap(work), self._loop)
        return future.result()

    async def _connected(self) -> RemoteClient:
        if self._client is None:
            client = make_client()
            await client.start(None, CONNECT_WAIT_SECONDS)
            self._client = client
        return self._client

    def call(self, frame: dict[str, Any]) -> dict[str, Any]:
        async def go() -> dict[str, Any]:
            client = await self._connected()
            reply = await client.request(frame, MEMORY_TIMEOUT_SECONDS)
            assert isinstance(reply, p.MemoryResult)
            return reply.data or {}

        try:
            return self._run(go())
        except RemoteError as exc:
            raise _error(exc, str(frame.get("op", ""))) from None

    async def _acall(self, frame: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.call, frame)

    # --- a interface do LongTermMemory que os comandos usam ---

    async def recall(
        self, query: str, top_k: int | None = None, threshold: float | None = None
    ) -> list[SearchHit]:
        args: dict[str, Any] = {"query": query}
        if top_k is not None:
            args["limit"] = top_k
        if threshold is not None:
            args["threshold"] = threshold
        data = await self._acall({"type": "memory.request", "op": "search", "args": args})
        return [SearchHit(_memory(r), r["score"]) for r in data["results"]]

    async def forget(self, memories: list[Memory]) -> list[Memory]:
        removed: list[Memory] = []
        for memory in memories:
            data = await self._acall(
                {"type": "memory.request", "op": "forget", "args": {"id": memory.id}}
            )
            removed += [Memory(memory.text, memory.type, [], "", id=i) for i in data["deleted"]]
        return removed

    def export(self, include_vectors: bool = False) -> list[dict[str, Any]]:
        self._export = self.call(
            {"type": "memory.request", "op": "export", "args": {"vectors": include_vectors}}
        )
        return list(self._export["memories"])

    def export_profile(self) -> dict[str, Any] | None:
        if self._export is None:
            self.export(False)
        assert self._export is not None
        profile = self._export.get("profile")
        return dict(profile) if profile else None

    async def get_profile(self) -> Profile | None:
        data = await self._acall({"type": "memory.request", "op": "profile.get", "args": {}})
        return _profile(data.get("profile"))

    async def set_profile(self, text: str, fields: dict[str, Any] | None = None) -> Profile:
        args = {"text": text, **(fields or {})}
        data = await self._acall({"type": "memory.request", "op": "profile.set", "args": args})
        profile = _profile(data.get("profile"))
        assert profile is not None
        return profile

    async def forget_profile(self) -> bool:
        data = await self._acall({"type": "memory.request", "op": "profile.forget", "args": {}})
        return bool(data.get("deleted"))

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._run(self._client.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


async def _wrap(work: Awaitable[T]) -> T:
    return await work


# ---------------------------------------------------------------------------------------------
# Tools do servidor
# ---------------------------------------------------------------------------------------------


async def list_tools() -> list[p.ToolInfo]:
    client = make_client()
    try:
        await client.start(None, CONNECT_WAIT_SECONDS)
        reply = await client.request({"type": "tools.request"})
    finally:
        await client.close()
    assert isinstance(reply, p.ToolsListed)
    return reply.tools
