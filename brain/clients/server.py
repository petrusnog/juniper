"""Servidor WebSocket do brain (ADR-012, ADR-007; T-075).

Autentica o cliente (Bearer), valida o handshake (`hello`/`welcome`), mantém o heartbeat e
cuida do ciclo de vida da conexão. O que acontece *dentro* de uma conexão (chat, memória,
executor) é de um `ConnectionHandler`: este módulo só entrega as mensagens e limpa tudo
quando a conexão acaba.

Regra para quem implementa o handler: `handle` precisa **voltar logo**. Enquanto ele roda, o
servidor não lê o próximo quadro (e um `pong` ou um `chat.cancel` ficariam esperando). Trabalho
longo vai em `Connection.spawn`, que acompanha a tarefa e a cancela quando a conexão cai.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import signal
import time
from collections.abc import Callable, Coroutine
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from brain import __version__
from brain.clients import protocol as p
from brain.config import settings
from brain.core.session import new_session_id
from brain.eventlog import event_log
from brain.logfiles import run_rotation
from brain.memory.v2 import v2_enabled
from brain.memory.v2.service import run_night_job
from brain.scheduler import run_daily

logger = logging.getLogger(__name__)

WS_PATH: Final = "/v1/ws"
HEALTH_PATH: Final = "/health"
HEARTBEAT_SECONDS: Final = 30.0
IDLE_TIMEOUT_SECONDS: Final = 90.0
HELLO_TIMEOUT_SECONDS: Final = 10.0
INTERNAL_MESSAGE: Final = "Algo deu errado aqui dentro. Tenta de novo daqui a pouco?"


# ---------------------------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------------------------


def ensure_token(path: Path) -> str:
    """O token do servidor: lê o do arquivo, ou gera um novo (modo 600) no primeiro boot.

    O valor nunca vai para log.
    """
    path = path.expanduser()
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return existing
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    # O arquivo nasce 600 (sem janela em que outro usuário o leria). Se ele existia vazio,
    # é reescrito mantendo o modo.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    os.chmod(path, 0o600)
    return token


def _bearer(headers: Headers) -> str | None:
    value = headers.get("Authorization")
    if value is None:
        return None
    scheme, _, credentials = value.partition(" ")
    return credentials.strip() if scheme.lower() == "bearer" and credentials.strip() else None


# ---------------------------------------------------------------------------------------------
# Conexão e handler
# ---------------------------------------------------------------------------------------------


class Connection:
    """Uma conexão autenticada e com handshake feito."""

    def __init__(self, websocket: ServerConnection, hello: p.Hello) -> None:
        self.websocket = websocket
        self.id = secrets.token_hex(4)
        self.kind = hello.client.kind
        self.device_id = hello.client.device_id
        self.device_name = hello.client.device_name
        self.client_version = hello.client.version
        self.last_seen = time.monotonic()
        self.session_id: str | None = None
        self.state: Any = None  # do handler
        self._tasks: set[asyncio.Task[None]] = set()
        # message.log: o cliente pediu os passos do servidor e o servidor permite
        self.wants_trace = bool(hello.trace) and settings.trace.enabled
        self.frame_bytes = 0  # tamanho do último quadro recebido
        self._trace_queue: asyncio.Queue[p.Trace] = asyncio.Queue()
        self._trace_pump: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()

    def trace(self, layer: str, text: str, moment: datetime) -> None:
        """Um passo do servidor para o `message.log` do cliente (em ordem; nunca bloqueia)."""
        if not self.wants_trace:
            return
        self._trace_queue.put_nowait(p.Trace(layer=layer, text=text, at=moment.isoformat()))
        if self._trace_pump is None:  # nenhuma mensagem vai levar o passo: manda sozinho
            self._trace_pump = self.spawn(self._pump_trace())

    async def _pump_trace(self) -> None:
        try:
            async with self._send_lock:
                await self._drain_trace()
        finally:
            self._trace_pump = None  # mesmo se falhar, o próximo passo cria outro pump

    async def send(self, message: p.ClientMessage | p.ServerMessage) -> bool:
        """Manda uma mensagem. `False` se a conexão já caiu (não é erro para quem manda).

        Os passos do `message.log` que já foram anotados saem antes dela, no mesmo lock: assim o
        cliente os recebe na ordem em que aconteceram, intercalados com a resposta.
        """
        async with self._send_lock:
            if not await self._drain_trace():
                return False
            return await self._send_now(message)

    async def _send_now(self, message: p.ClientMessage | p.ServerMessage) -> bool:
        try:
            try:
                text = p.serialize_message(message)
            except p.InvalidMessageError:  # grande demais para um quadro: nunca deixar sem resposta
                event_log.log("error", where="server.send", type=type(message).__name__)
                text = p.serialize_message(
                    p.Error.from_code(
                        "internal",
                        "A resposta ficou grande demais para enviar.",
                        getattr(message, "reply_to", None),
                    )
                )
            await self.websocket.send(text)
        except ConnectionClosed:
            return False
        return True

    async def _drain_trace(self) -> bool:
        while not self._trace_queue.empty():
            if not await self._send_now(self._trace_queue.get_nowait()):
                return False
        return True

    async def close(self, code: int, reason: str = "") -> None:
        with contextlib.suppress(Exception):
            await self.websocket.close(code, reason)

    def spawn(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Roda `work` em segundo plano, preso à conexão: é cancelado quando ela acaba."""
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def cancel_tasks(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class ConnectionHandler(Protocol):
    """O que o servidor delega sobre cada conexão."""

    async def open(self, conn: Connection, hello: p.Hello) -> p.WelcomeSession:
        """Depois do `hello`: liga a conexão a uma conversa e devolve o que vai no `welcome`.

        Levantar `p.ProtocolError` recusa a conexão com o erro correspondente.
        """

    async def handle(self, conn: Connection, message: p.ClientMessage) -> None:
        """Uma mensagem do cliente (já validada, fora `pong`). Precisa voltar logo."""

    async def close(self, conn: Connection) -> None:
        """A conexão acabou (qualquer motivo): solta o que ela segurava."""

    async def shutdown(self) -> None:
        """O servidor está saindo e todas as conexões já fecharam: conclui o que ficou."""


class BaseHandler:
    """Handler mínimo: uma conversa nova por conexão e nenhuma mensagem tratada ainda."""

    async def open(self, conn: Connection, hello: p.Hello) -> p.WelcomeSession:
        conn.session_id = new_session_id()
        return p.WelcomeSession(id=conn.session_id, resumed=False, turns=0)

    async def handle(self, conn: Connection, message: p.ClientMessage) -> None:
        reply_to = getattr(message, "id", None)
        await conn.send(
            p.Error.from_code("bad_request", "Essa mensagem ainda não é suportada.", reply_to)
        )

    async def close(self, conn: Connection) -> None:
        return None

    async def shutdown(self) -> None:
        return None


HealthFn = Callable[[], dict[str, Any]]


def _default_health() -> dict[str, Any]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------------------------
# Servidor
# ---------------------------------------------------------------------------------------------


class BrainServer:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        handler: ConnectionHandler | None = None,
        *,
        health: HealthFn | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        hello_timeout_seconds: float = HELLO_TIMEOUT_SECONDS,
    ) -> None:
        self.host = host
        self.port = port
        self._token = token
        self._handler: ConnectionHandler = handler or BaseHandler()
        self._health = health or _default_health
        self._heartbeat = heartbeat_seconds
        self._idle_timeout = idle_timeout_seconds
        self._hello_timeout = hello_timeout_seconds
        self._server: Server | None = None
        self._by_device: dict[str, Connection] = {}

    @property
    def connections(self) -> list[Connection]:
        return list(self._by_device.values())

    async def start(self) -> None:
        self._server = await serve(
            self._serve_connection,
            self.host,
            self.port,
            process_request=self._process_request,
            max_size=p.MAX_MESSAGE_BYTES,
            ping_interval=None,  # o heartbeat é o da aplicação (ADR-012, seção 4)
        )
        for socket in self._server.sockets:
            self.port = socket.getsockname()[1]
            break
        event_log.log("server", action="start", host=self.host, port=self.port)

    async def stop(self, code: int = p.CLOSE_GOING_AWAY, reason: str = "servidor saindo") -> None:
        """Avisa os clientes (fechamento limpo) e espera as conexões acabarem."""
        server, self._server = self._server, None
        if server is None:
            return
        server.close(close_connections=True, code=code, reason=reason)
        await server.wait_closed()
        await self._handler.shutdown()
        event_log.log("server", action="stop", code=code)

    # --- HTTP: autenticação antes do upgrade e /health ---

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        path = urlsplit(request.path).path
        if path == HEALTH_PATH:
            body = json.dumps({**self._health(), "version": __version__}).encode()
            headers = Headers([("Content-Type", "application/json"), ("Cache-Control", "no-store")])
            return Response(HTTPStatus.OK.value, "OK", headers, body)
        if path != WS_PATH:
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        given = _bearer(request.headers)
        if given is None or not hmac.compare_digest(given.encode(), self._token.encode()):
            event_log.log("connection", action="rejected", reason="unauthorized")
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        return None

    # --- ciclo de vida da conexão ---

    async def _serve_connection(self, websocket: ServerConnection) -> None:
        conn: Connection | None = None
        try:
            conn = await self._handshake(websocket)
            if conn is None:
                return
            await self._run(conn)
        except ConnectionClosed:
            pass
        except Exception as exc:  # um bug não pode derrubar o servidor
            logger.exception("Conexão falhou")
            event_log.log("error", where="server.connection", type=type(exc).__name__)
            with contextlib.suppress(Exception):
                await websocket.close(1011, "erro interno")
        finally:
            if conn is not None:
                await self._release(conn)

    async def _refuse(self, websocket: ServerConnection, error: p.ProtocolError, code: int) -> None:
        with contextlib.suppress(ConnectionClosed):
            await websocket.send(p.serialize_message(p.Error.from_exception(error, error.message)))
        await websocket.close(code, error.code)
        event_log.log("connection", action="refused", reason=error.code)

    async def _handshake(self, websocket: ServerConnection) -> Connection | None:
        try:
            frame = await asyncio.wait_for(websocket.recv(), self._hello_timeout)
        except TimeoutError:
            await websocket.close(p.CLOSE_PROTOCOL_VIOLATION, "hello esperado")
            return None
        try:
            if isinstance(frame, bytes):
                raise p.InvalidMessageError("Quadros binários não são aceitos.")
            hello = p.parse_client_message(frame)
            if not isinstance(hello, p.Hello):
                raise p.InvalidMessageError("A primeira mensagem deve ser 'hello'.")
        except p.UnsupportedVersionError as exc:
            await self._refuse(websocket, exc, p.CLOSE_UNSUPPORTED_VERSION)
            return None
        except p.ProtocolError as exc:
            await self._refuse(websocket, exc, p.CLOSE_PROTOCOL_VIOLATION)
            return None

        conn = Connection(websocket, hello)
        try:
            session = await self._handler.open(conn, hello)
        except p.ProtocolError as exc:
            await self._refuse(websocket, exc, p.CLOSE_PROTOCOL_VIOLATION)
            return None

        previous = self._by_device.get(conn.device_id)
        self._by_device[conn.device_id] = conn
        if previous is not None:  # a segunda conexão do mesmo dispositivo substitui a primeira
            await previous.close(p.CLOSE_REPLACED, "substituída por outra conexão")
        await conn.send(
            p.Welcome(
                protocol_version=p.PROTOCOL_VERSION,
                server_version=__version__,
                heartbeat_seconds=max(1, round(self._heartbeat)),
                session=session,
            )
        )
        event_log.log(
            "connection",
            action="open",
            connection=conn.id,
            kind=conn.kind,
            device_id=conn.device_id,
            session_id=conn.session_id,
        )
        return conn

    async def _run(self, conn: Connection) -> None:
        conn.spawn(self._watchdog(conn))
        async for frame in conn.websocket:
            conn.last_seen = time.monotonic()  # qualquer quadro conta como sinal de vida
            if isinstance(frame, bytes):
                await conn.send(
                    p.Error.from_code("bad_request", "Quadros binários não são aceitos.")
                )
                await conn.close(p.CLOSE_PROTOCOL_VIOLATION, "binário")
                return
            try:
                message = p.parse_client_message(frame)
            except p.ProtocolError as exc:
                reply_to = _id_of(frame)
                await conn.send(p.Error.from_exception(exc, exc.message, reply_to))
                continue  # a conexão continua aberta depois de qualquer erro de pedido
            if isinstance(message, p.Pong):
                continue
            if isinstance(message, p.Hello):
                await conn.send(p.Error.from_code("bad_request", "O 'hello' já foi enviado."))
                continue
            conn.frame_bytes = len(frame.encode("utf-8")) if isinstance(frame, str) else len(frame)
            try:
                await self._handler.handle(conn, message)
            except Exception as exc:  # um bug em um pedido não derruba a conexão (ADR-012, seção 9)
                logger.exception("Pedido falhou")
                event_log.log("error", where="server.handle", type=type(exc).__name__)
                await conn.send(p.Error.from_code("internal", INTERNAL_MESSAGE, _id_of(frame)))

    async def _watchdog(self, conn: Connection) -> None:
        """Manda `ping` a cada intervalo; sem nenhum sinal por `idle_timeout`, derruba."""
        while True:
            await asyncio.sleep(self._heartbeat)
            if time.monotonic() - conn.last_seen >= self._idle_timeout:
                event_log.log("connection", action="timeout", connection=conn.id)
                await conn.close(p.CLOSE_HEARTBEAT_TIMEOUT, "sem sinal de vida")
                return
            if not await conn.send(p.Ping()):
                return

    async def _release(self, conn: Connection) -> None:
        if self._by_device.get(conn.device_id) is conn:
            del self._by_device[conn.device_id]
        await conn.cancel_tasks()
        try:
            await self._handler.close(conn)
        except Exception as exc:
            event_log.log("error", where="handler.close", type=type(exc).__name__)
        event_log.log("connection", action="close", connection=conn.id)


def _id_of(frame: str | bytes) -> str | None:
    """O `id` do pedido malformado, para o erro poder apontar para ele."""
    try:
        data = json.loads(frame)
    except ValueError:
        return None
    value = data.get("id") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


# ---------------------------------------------------------------------------------------------
# Processo (`juniper serve`)
# ---------------------------------------------------------------------------------------------


async def run_server(server: BrainServer, dashboard: Any | None = None) -> None:
    """Sobe o servidor e roda até SIGTERM (deploy: fecha com 1012) ou SIGINT (1001).

    O dashboard (ADR-020), se houver, sobe junto e isolado: se não subir, o servidor segue."""
    loop = asyncio.get_running_loop()
    stop: asyncio.Future[int] = loop.create_future()

    def request_stop(code: int) -> None:
        if not stop.done():
            stop.set_result(code)

    loop.add_signal_handler(signal.SIGTERM, request_stop, p.CLOSE_SERVICE_RESTART)
    loop.add_signal_handler(signal.SIGINT, request_stop, p.CLOSE_GOING_AWAY)
    await server.start()
    if dashboard is not None:
        await dashboard.start()  # logs and returns False if the port is taken
    # Daily housekeeping, cron-style: gzip yesterday's log and delete the expired ones.
    cron = loop.create_task(run_daily("log_rotation", settings.logging.rotate_at, run_rotation))
    # The Memória V2 nightly pass (ADR-017), only when it is on and can run.
    night = None
    if v2_enabled():
        night = loop.create_task(
            run_daily("v2_night", settings.memory.v2.night.at, run_night_job, catch_up=False)
        )
    try:
        code = await stop
    finally:
        for job in (cron, night):
            if job is not None:
                job.cancel()
        for job in (cron, night):
            if job is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await job
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)
    if dashboard is not None:
        with contextlib.suppress(Exception):
            await dashboard.stop()
    await server.stop(code, "servidor reiniciando" if code == p.CLOSE_SERVICE_RESTART else "saindo")
