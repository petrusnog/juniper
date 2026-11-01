"""Cliente WebSocket do brain (ADR-012; T-086): a parte do CLI remoto que fala o protocolo.

Conecta com o token, faz o `hello`, responde ao heartbeat, entrega o stream do chat, faz pedidos
com resposta (sessões, memória, tools) e **reconecta sozinho** com backoff exponencial quando a
conexão cai, retomando a conversa. Nada aqui sabe de terminal: quem imprime é o CLI.

Estado que fica aqui: só o id da conversa aberta (para retomar). Histórico e confirmações vivem
no servidor (ADR-010, ADR-012).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from brain import __version__
from brain.clients import protocol as p
from brain.config import settings
from brain.eventlog import event_log
from brain.trace import message_trace, quote

logger = logging.getLogger(__name__)

BACKOFF_SECONDS: Final = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
JITTER: Final = 0.2
STABLE_AFTER_SECONDS: Final = 30.0
IDLE_TIMEOUT_SECONDS: Final = 90.0
REQUEST_TIMEOUT_SECONDS: Final = 60.0

UNAVAILABLE_MESSAGE = "Juniper indisponível no momento."
DETACHED_MESSAGE = "Outra janela assumiu esta conversa; o resto da resposta vai aparecer lá."


# ---------------------------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------------------------


class RemoteError(Exception):
    """Base dos erros do cliente remoto. `str(exc)` já é texto para o usuário."""


class ServerUnavailableError(RemoteError):
    """Sem conexão com o servidor (e, depois de tentar, sem conseguir uma)."""


class FatalConnectionError(RemoteError):
    """Falha que repetir não resolve: token recusado (401/4401) ou versão incompatível (4426)."""


class ConnectionLostError(RemoteError):
    """A conexão caiu no meio de um pedido; o cliente já está reconectando."""


class ServerError(RemoteError):
    """O servidor respondeu um `error` ao pedido."""

    def __init__(self, error: p.Error) -> None:
        super().__init__(error.message)
        self.error = error

    @property
    def code(self) -> str:
        return self.error.code


@dataclass
class ChatResult:
    turn_id: str | None
    finish: p.FinishReason
    error: p.Error | None = None
    timing: dict[str, Any] | None = None


def backoff_delay(attempt: int, rng: random.Random | None = None) -> float:
    """Espera antes da tentativa `attempt` (0, 1, 2...): 1, 2, 4, 8, 16 e depois 30 s, ±20%."""
    base = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
    roll = (rng or random).uniform(-JITTER, JITTER)
    return base * (1 + roll)


def restart_delay(rng: random.Random | None = None) -> float:
    """Depois de um fechamento 1012 (deploy): reconecta quase na hora (0 a 2 s)."""
    return (rng or random).uniform(0.0, 2.0)


# ---------------------------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------------------------


class RemoteClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        device_id: str,
        device_name: str | None = None,
        kind: str = "cli",
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        sleep: Callable[[float], Any] = asyncio.sleep,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._device_id = device_id
        self._device_name = device_name
        self._kind = kind
        self._idle_timeout = idle_timeout
        self._sleep = sleep
        self._on_status = on_status
        self._ws: ClientConnection | None = None
        self._ready = asyncio.Event()
        self._closing = False
        self._fatal: FatalConnectionError | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[p.ServerMessage]] = {}
        self._turn: asyncio.Queue[p.ServerMessage | None] | None = None
        self._turn_id: str | None = None
        self._cancel_requested = False
        self._first: asyncio.Future[p.Welcome] | None = None
        self.welcome: p.Welcome | None = None
        self.session_id: str | None = None
        self._resume: p.Resume | None = None
        self.detached = False
        self.reconnected = asyncio.Event()

    # --- vida da conexão ---

    @property
    def connected(self) -> bool:
        return self._ready.is_set()

    async def start(self, resume: p.Resume | None = None, wait: float = 15.0) -> p.Welcome:
        """Conecta (tentando de novo por até `wait` segundos) e devolve o `welcome`.

        Levanta `ServerUnavailableError` se não deu, `FatalConnectionError` se o servidor recusou.
        Depois disso o cliente reconecta sozinho, sem prazo.
        """
        self._resume = resume
        self._first = asyncio.get_running_loop().create_future()
        self._supervisor = asyncio.get_running_loop().create_task(self._supervise())
        try:
            return await asyncio.wait_for(asyncio.shield(self._first), wait)
        except TimeoutError:
            await self.close()
            raise ServerUnavailableError(UNAVAILABLE_MESSAGE) from None
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        self._closing = True
        self._ready.clear()
        if self._supervisor is not None:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._fail_pending(ConnectionLostError(UNAVAILABLE_MESSAGE))

    def _status(self, text: str) -> None:
        if self._on_status is not None:
            self._on_status(text)

    async def _supervise(self) -> None:
        """Mantém a conexão: conecta, lê até cair, e reconecta com backoff."""
        attempt = 0
        while not self._closing:
            started = time.monotonic()
            close_code: int | None = None
            try:
                close_code = await self._session()
            except FatalConnectionError as exc:
                self._fatal = exc
                if self._first is not None and not self._first.done():
                    self._first.set_exception(exc)
                self._fail_pending(exc)
                return
            except (OSError, WebSocketException, TimeoutError) as exc:
                event_log.log("remote", action="connect_failed", type=type(exc).__name__)
            finally:
                self._ready.clear()
                self._ws = None
                self._fail_pending(ConnectionLostError(UNAVAILABLE_MESSAGE))
            if self._closing:
                return
            if time.monotonic() - started >= STABLE_AFTER_SECONDS:
                attempt = 0
            delay = (
                restart_delay() if close_code == p.CLOSE_SERVICE_RESTART else backoff_delay(attempt)
            )
            attempt += 1
            self._status(UNAVAILABLE_MESSAGE)
            await self._sleep(delay)

    async def _session(self) -> int | None:
        """Uma conexão do começo ao fim. Devolve o código com que ela fechou, se houve."""
        try:
            ws = await connect(
                self._url,
                additional_headers={"Authorization": f"Bearer {self._token}"},
                max_size=p.MAX_MESSAGE_BYTES,
                ping_interval=None,
                open_timeout=10,
            )
        except InvalidStatus as exc:
            if exc.response.status_code in (401, 403):
                raise FatalConnectionError(
                    "O servidor recusou a credencial. Rode `juniper login` de novo "
                    "(ou confira o token antigo)."
                ) from exc
            raise
        self._ws = ws
        try:
            await ws.send(p.serialize_message(self._hello()))
            welcome = await self._read_welcome(ws)
            self.welcome = welcome
            self.session_id = welcome.session.id
            self._resume = p.Resume(session_id=welcome.session.id)  # a próxima retoma esta
            self._ready.set()
            if self._first is not None and not self._first.done():
                self._first.set_result(welcome)
            else:
                self.reconnected.set()
                self._status("Reconectei.")
            await self._read_loop(ws)
        except ConnectionClosed:
            pass
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
        code = ws.close_code
        if code in (p.CLOSE_UNAUTHORIZED, p.CLOSE_UNSUPPORTED_VERSION):
            raise FatalConnectionError(
                "Versão do protocolo incompatível com o servidor."
                if code == p.CLOSE_UNSUPPORTED_VERSION
                else "O servidor recusou a credencial."
            )
        return code

    def _hello(self) -> p.Hello:
        return p.Hello(
            protocol_version=p.PROTOCOL_VERSION,
            client=p.ClientInfo(
                kind=self._kind,
                version=__version__,
                device_id=self._device_id,
                device_name=self._device_name,
            ),
            resume=self._resume,
            trace=True if settings.trace.enabled else None,
        )

    async def _read_welcome(self, ws: ClientConnection) -> p.Welcome:
        async with asyncio.timeout(10):
            while True:
                frame = await ws.recv()
                message = self._parse(frame)
                if isinstance(message, p.Welcome):
                    return message
                if isinstance(message, p.Error):
                    if message.code == "unsupported_version":
                        raise FatalConnectionError(
                            "Versão do protocolo incompatível com o servidor."
                        )
                    raise RemoteError(message.message)

    @staticmethod
    def _parse(frame: str | bytes) -> p.ServerMessage:
        return p.parse_server_message(frame)

    async def _read_loop(self, ws: ClientConnection) -> None:
        while True:
            try:
                async with asyncio.timeout(self._idle_timeout):
                    frame = await ws.recv()
            except TimeoutError:  # 90 s sem nenhum sinal: a conexão morreu (ADR-012, seção 4)
                await ws.close(p.CLOSE_HEARTBEAT_TIMEOUT, "sem sinal de vida")
                return
            try:
                message = self._parse(frame)
            except p.ProtocolError as exc:
                logger.warning("Mensagem do servidor ignorada: %s", exc.message)
                continue
            await self._dispatch(ws, message)

    async def _dispatch(self, ws: ClientConnection, message: p.ServerMessage) -> None:
        if isinstance(message, p.Ping):
            await ws.send(p.serialize_message(p.Pong()))
        elif isinstance(message, p.Trace):  # um passo do servidor, para o message.log
            if self._turn is not None:  # na fila do turno, para sair na ordem em que chegou
                self._turn.put_nowait(message)
            else:
                self._write_trace(message)
        elif isinstance(message, p.SessionDetached):
            self.detached = True
            if self._turn is not None:  # acorda o turno em espera: o fim dele não virá para cá
                self._turn.put_nowait(message)
        elif isinstance(message, p.ChatStarted | p.ChatToken | p.ChatEnd):
            if self._turn is not None:
                self._turn.put_nowait(message)
        elif isinstance(message, p.Error) and message.reply_to in self._pending:
            self._settle(message.reply_to, message)
        elif isinstance(message, p.Error):
            if self._turn is not None:  # erro do turno (reply_to é o id do chat.send)
                self._turn.put_nowait(message)
        else:
            reply_to = getattr(message, "reply_to", None)
            if isinstance(reply_to, str):
                self._settle(reply_to, message)

    @staticmethod
    def _write_trace(message: p.Trace) -> None:
        message_trace.file_line(message.layer, message.text, datetime.fromisoformat(message.at))

    def _settle(self, request_id: str, message: p.ServerMessage) -> None:
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(message)

    def _fail_pending(self, exc: RemoteError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
        if self._turn is not None:
            self._turn.put_nowait(None)  # acorda o turno em andamento: a conexão caiu

    # --- envio ---

    async def _send(self, message: p.ClientMessage) -> None:
        ws = self._ws
        if ws is None or not self._ready.is_set():
            raise ConnectionLostError(UNAVAILABLE_MESSAGE)
        try:
            await ws.send(p.serialize_message(message))
        except ConnectionClosed as exc:
            raise ConnectionLostError(UNAVAILABLE_MESSAGE) from exc

    async def wait_ready(self, timeout: float = 5.0) -> None:
        """Espera a conexão (útil depois de uma queda). `ServerUnavailableError` se não vier."""
        if self._fatal is not None:
            raise self._fatal
        try:
            async with asyncio.timeout(timeout):
                await self._ready.wait()
        except TimeoutError:
            raise ServerUnavailableError(UNAVAILABLE_MESSAGE) from None

    async def request(
        self, frame: dict[str, Any], timeout: float = REQUEST_TIMEOUT_SECONDS
    ) -> p.ServerMessage:
        """Manda um pedido (com `id` novo) e devolve a resposta. `ServerError` se for um `error`."""
        await self.wait_ready()
        request_id = f"r-{secrets.token_hex(4)}"
        message = p.parse_client_message({**frame, "id": request_id})
        future: asyncio.Future[p.ServerMessage] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(message)
            async with asyncio.timeout(timeout):
                reply = await future
        except TimeoutError:
            raise ServerUnavailableError("O servidor demorou demais para responder.") from None
        finally:
            self._pending.pop(request_id, None)
        if isinstance(reply, p.Error):
            raise ServerError(reply)
        if isinstance(reply, p.SessionState):
            # `session.new`/`session.resume`: é essa a conversa a retomar numa reconexão. Senão uma
            # queda depois de `/novo` voltaria à conversa antiga (e roubaria a de outra janela).
            self.session_id = reply.session_id
            self._resume = p.Resume(session_id=reply.session_id)
        return reply

    # --- chat ---

    async def chat(self, text: str, on_token: Callable[[str], None]) -> ChatResult:
        """Manda `text` e entrega os tokens em ordem. `ConnectionLostError` se a conexão cair."""
        await self.wait_ready()
        request_id = f"c-{secrets.token_hex(4)}"
        self._turn = asyncio.Queue()
        self._turn_id = None
        self._cancel_requested = False
        error: p.Error | None = None
        started = time.monotonic()
        pieces = chars = 0
        try:
            message_trace.step(
                "Cliente remoto · WebSocket",
                f"Montei a mensagem chat.send (id {request_id}, {len(text)} caracteres) e a enviei "
                f"pela conexão com {self._url}, na conversa {self.session_id}.",
            )
            await self._send(p.ChatSend(id=request_id, text=text))
            while True:
                message = await self._turn.get()
                if message is None:
                    message_trace.step(
                        "Cliente remoto · WebSocket",
                        "A conexão caiu no meio da resposta; vou reconectar e retomar a conversa.",
                    )
                    raise ConnectionLostError(UNAVAILABLE_MESSAGE)
                if isinstance(message, p.ChatStarted):
                    self._turn_id = message.turn_id
                    message_trace.step(
                        "Cliente remoto · WebSocket",
                        f"Recebi chat.started: o servidor abriu o turno {message.turn_id}.",
                    )
                    if self._cancel_requested:
                        await self._send(p.ChatCancel(turn_id=message.turn_id))
                elif isinstance(message, p.Trace):
                    self._write_trace(message)
                elif isinstance(message, p.ChatToken):
                    if pieces == 0:
                        message_trace.step(
                            "Cliente remoto · WebSocket",
                            "Chegou o primeiro pedaço da resposta (chat.token), "
                            f"{round((time.monotonic() - started) * 1000)} ms depois do envio. "
                            "Entrego cada pedaço ao terminal assim que chega.",
                        )
                    pieces += 1
                    chars += len(message.text)
                    on_token(message.text)
                elif isinstance(message, p.SessionDetached):
                    # Outra conexão assumiu a conversa no meio do turno: o resto da resposta vai
                    # para ela (e fica salvo). Não há `chat.end` a esperar por aqui.
                    return ChatResult(
                        self._turn_id,
                        "error",
                        p.Error.from_code("bad_request", DETACHED_MESSAGE, request_id),
                    )
                elif isinstance(message, p.Error):
                    error = message
                    message_trace.step(
                        "Cliente remoto · WebSocket",
                        f"Recebi um erro tipado do servidor: {message.code} "
                        f"({quote(message.message, 200)}).",
                    )
                    if self._turn_id is None and message.reply_to == request_id:
                        # Recusado antes de abrir o turno (conversa assumida por outra conexão,
                        # `turn_in_progress`...): o servidor não manda `chat.end`. Sem isto o
                        # cliente esperaria para sempre.
                        return ChatResult(None, "error", error)
                elif isinstance(message, p.ChatEnd):
                    message_trace.step(
                        "Cliente remoto · WebSocket",
                        f"Recebi chat.end (finish «{message.finish}»): ao todo {pieces} pedaço(s) "
                        f"e {chars} caracteres, {round((time.monotonic() - started) * 1000)} ms "
                        "depois do envio.",
                    )
                    return ChatResult(message.turn_id, message.finish, error, message.timing)
        finally:
            self._turn = None
            self._turn_id = None

    async def cancel(self) -> None:
        """Pede ao servidor para parar o turno em andamento (Ctrl+C)."""
        self._cancel_requested = True
        turn_id = self._turn_id
        if turn_id is not None:
            with contextlib.suppress(ConnectionLostError):
                await self._send(p.ChatCancel(turn_id=turn_id))

    # --- atalhos ---

    async def history(self) -> list[p.HistoryItem]:
        """O histórico visível da conversa atual (retoma a si mesma para o servidor mandá-lo)."""
        reply = await self.request({"type": "session.resume", "session_id": self.session_id})
        assert isinstance(reply, p.SessionState)
        return reply.history or []
