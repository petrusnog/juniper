"""Servidor WebSocket: autenticação, handshake, heartbeat e ciclo de vida (T-075)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from brain import __version__
from brain.clients import protocol as p
from brain.clients.server import BaseHandler, BrainServer, Connection, ensure_token, run_server

TOKEN = "segredo-de-teste-123"


def hello(device_id: str = "dev-1", version: object = p.PROTOCOL_VERSION) -> str:
    return json.dumps(
        {
            "type": "hello",
            "protocol_version": version,
            "client": {"kind": "cli", "version": "0.1.0", "device_id": device_id},
        }
    )


class RecordingHandler(BaseHandler):
    def __init__(self) -> None:
        self.opened: list[Connection] = []
        self.closed: list[Connection] = []
        self.messages: list[p.ClientMessage] = []

    async def open(self, conn: Connection, hello: p.Hello) -> p.WelcomeSession:
        self.opened.append(conn)
        return await super().open(conn, hello)

    async def handle(self, conn: Connection, message: p.ClientMessage) -> None:
        self.messages.append(message)

    async def close(self, conn: Connection) -> None:
        self.closed.append(conn)


async def start(
    handler: RecordingHandler | None = None, **kwargs: Any
) -> tuple[BrainServer, RecordingHandler]:
    handler = handler or RecordingHandler()
    server = BrainServer("127.0.0.1", 0, TOKEN, handler, **kwargs)
    await server.start()
    return server, handler


@pytest.fixture
async def running() -> AsyncIterator[tuple[BrainServer, RecordingHandler]]:
    server, handler = await start()
    yield server, handler
    await server.stop()


def url(server: BrainServer, path: str = "/v1/ws") -> str:
    return f"ws://127.0.0.1:{server.port}{path}"


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def open_client(server: BrainServer, device_id: str = "dev-1") -> ClientConnection:
    ws = await connect(url(server), additional_headers=auth())
    await ws.send(hello(device_id))
    welcome = json.loads(await ws.recv())
    assert welcome["type"] == "welcome"
    return ws


async def wait_until(condition: Any, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


# --- autenticação -------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, auth("errado"), {"Authorization": TOKEN}])
async def test_connection_without_valid_token_is_rejected(
    running: tuple[BrainServer, RecordingHandler], headers: dict[str, str]
) -> None:
    server, handler = running
    with pytest.raises(InvalidStatus) as info:
        await connect(url(server), additional_headers=headers)
    assert info.value.response.status_code == 401
    assert handler.opened == []


async def test_connection_with_valid_token_gets_welcome(
    running: tuple[BrainServer, RecordingHandler],
) -> None:
    server, handler = running
    async with connect(url(server), additional_headers=auth()) as ws:
        await ws.send(hello())
        welcome = p.parse_message(await ws.recv(), source="server")
    assert isinstance(welcome, p.Welcome)
    assert welcome.protocol_version == p.PROTOCOL_VERSION
    assert welcome.server_version == __version__
    assert welcome.session.resumed is False
    assert handler.opened[0].device_id == "dev-1"


async def test_token_never_reaches_the_event_log(
    running: tuple[BrainServer, RecordingHandler], tmp_path: Path
) -> None:
    server, _ = running
    with pytest.raises(InvalidStatus):
        await connect(url(server), additional_headers=auth("outro-segredo-xyz"))
    await open_client(server)
    logs = "".join(f.read_text() for f in (tmp_path / "logs").glob("*.jsonl"))
    assert "connection" in logs
    assert TOKEN not in logs
    assert "outro-segredo-xyz" not in logs


def test_token_file_is_created_with_mode_600_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "token"
    first = ensure_token(path)
    assert len(first) >= 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ensure_token(path) == first


def test_token_file_with_loose_mode_is_tightened(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text("meu-token\n")
    os.chmod(path, 0o644)
    assert ensure_token(path) == "meu-token"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_empty_token_file_is_regenerated(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text("\n")
    assert ensure_token(path)
    assert path.read_text().strip()


# --- handshake ----------------------------------------------------------------------------


async def test_incompatible_version_gets_typed_error_and_4426(
    running: tuple[BrainServer, RecordingHandler],
) -> None:
    server, handler = running
    async with connect(url(server), additional_headers=auth()) as ws:
        await ws.send(hello(version=99))
        error = p.parse_message(await ws.recv(), source="server")
        assert isinstance(error, p.Error)
        assert error.code == "unsupported_version"
        with pytest.raises(ConnectionClosed) as info:
            await ws.recv()
    assert info.value.rcvd is not None and info.value.rcvd.code == p.CLOSE_UNSUPPORTED_VERSION
    assert handler.opened == []


@pytest.mark.parametrize(
    "frame", ["não é json", json.dumps({"type": "chat.send", "id": "c", "text": "oi"})]
)
async def test_first_message_must_be_a_valid_hello(
    running: tuple[BrainServer, RecordingHandler], frame: str
) -> None:
    server, _ = running
    async with connect(url(server), additional_headers=auth()) as ws:
        await ws.send(frame)
        error = p.parse_message(await ws.recv(), source="server")
        assert isinstance(error, p.Error) and error.code == "bad_request"
        with pytest.raises(ConnectionClosed) as info:
            await ws.recv()
    assert info.value.rcvd is not None and info.value.rcvd.code == p.CLOSE_PROTOCOL_VIOLATION


async def test_silent_client_is_closed_after_the_hello_timeout() -> None:
    server, _ = await start(hello_timeout_seconds=0.1)
    try:
        async with connect(url(server), additional_headers=auth()) as ws:
            with pytest.raises(ConnectionClosed) as info:
                await ws.recv()
        assert info.value.rcvd is not None and info.value.rcvd.code == p.CLOSE_PROTOCOL_VIOLATION
    finally:
        await server.stop()


async def test_malformed_request_gets_error_and_connection_stays_open(
    running: tuple[BrainServer, RecordingHandler],
) -> None:
    server, handler = running
    ws = await open_client(server)
    await ws.send(json.dumps({"type": "chat.send", "id": "c-9"}))  # falta text
    error = p.parse_message(await ws.recv(), source="server")
    assert isinstance(error, p.Error)
    assert (error.code, error.reply_to) == ("bad_request", "c-9")
    await ws.send(json.dumps({"type": "chat.cancel", "turn_id": "t"}))
    await wait_until(lambda: len(handler.messages) == 1)
    await ws.close()


async def test_binary_frames_are_refused(running: tuple[BrainServer, RecordingHandler]) -> None:
    server, _ = running
    ws = await open_client(server)
    await ws.send(b"\x00\x01")
    error = p.parse_message(await ws.recv(), source="server")
    assert isinstance(error, p.Error) and error.code == "bad_request"
    with pytest.raises(ConnectionClosed):
        await ws.recv()


async def test_same_device_replaces_the_previous_connection(
    running: tuple[BrainServer, RecordingHandler],
) -> None:
    server, _ = running
    first = await open_client(server, "dev-1")
    second = await open_client(server, "dev-1")
    with pytest.raises(ConnectionClosed) as info:
        await first.recv()
    assert info.value.rcvd is not None and info.value.rcvd.code == p.CLOSE_REPLACED
    assert len(server.connections) == 1
    await second.close()


# --- heartbeat ----------------------------------------------------------------------------


async def test_server_sends_ping_and_pong_keeps_the_connection_alive() -> None:
    server, handler = await start(heartbeat_seconds=0.05, idle_timeout_seconds=0.2)
    try:
        ws = await open_client(server)
        for _ in range(8):  # bem mais que o idle_timeout: só o pong mantém viva
            message = p.parse_message(await ws.recv(), source="server")
            assert isinstance(message, p.Ping)
            await ws.send(json.dumps({"type": "pong"}))
        assert len(server.connections) == 1
        assert handler.closed == []
        await ws.close()
    finally:
        await server.stop()


async def test_silent_connection_is_closed_and_cleaned_up() -> None:
    server, handler = await start(heartbeat_seconds=0.05, idle_timeout_seconds=0.15)
    try:
        ws = await open_client(server)  # nunca responde ao ping
        with pytest.raises(ConnectionClosed) as info:
            while True:
                await ws.recv()
        assert info.value.rcvd is not None and info.value.rcvd.code == p.CLOSE_HEARTBEAT_TIMEOUT
        await wait_until(lambda: len(handler.closed) == 1)
        assert server.connections == []
    finally:
        await server.stop()


# --- ciclo de vida ------------------------------------------------------------------------


async def test_spawned_tasks_are_cancelled_when_the_connection_ends() -> None:
    cancelled = asyncio.Event()

    class Spawner(RecordingHandler):
        async def handle(self, conn: Connection, message: p.ClientMessage) -> None:
            async def work() -> None:
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            conn.spawn(work())

    server, _ = await start(Spawner())
    try:
        ws = await open_client(server)
        await ws.send(json.dumps({"type": "chat.cancel", "turn_id": "t"}))
        await asyncio.sleep(0.05)
        await ws.close()
        async with asyncio.timeout(2):
            await cancelled.wait()
    finally:
        await server.stop()


async def test_stop_closes_clients_cleanly_with_the_given_code() -> None:
    server, handler = await start()
    ws = await open_client(server)
    await server.stop(p.CLOSE_SERVICE_RESTART, "reiniciando")
    with pytest.raises(ConnectionClosed) as info:
        await ws.recv()
    assert info.value.rcvd is not None and info.value.rcvd.code == p.CLOSE_SERVICE_RESTART
    await wait_until(lambda: len(handler.closed) == 1)
    assert server.connections == []


async def test_sigterm_shuts_the_server_down_notifying_clients() -> None:
    server = BrainServer("127.0.0.1", 0, TOKEN, RecordingHandler())
    task = asyncio.create_task(run_server(server))
    await wait_until(lambda: server.port != 0)
    ws = await open_client(server)
    os.kill(os.getpid(), signal.SIGTERM)
    with pytest.raises(ConnectionClosed) as info:
        await ws.recv()
    assert info.value.rcvd is not None and info.value.rcvd.code == p.CLOSE_SERVICE_RESTART
    async with asyncio.timeout(2):
        await task


# --- HTTP ---------------------------------------------------------------------------------


def _get(address: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(address, timeout=2) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


async def test_health_endpoint_needs_no_auth(running: tuple[BrainServer, RecordingHandler]) -> None:
    server, _ = running
    status, body = await asyncio.to_thread(_get, url(server, "/health").replace("ws://", "http://"))
    assert status == 200
    assert json.loads(body) == {"status": "ok", "version": __version__}


async def test_unknown_path_is_404(running: tuple[BrainServer, RecordingHandler]) -> None:
    server, _ = running
    status, _ = await asyncio.to_thread(_get, url(server, "/outro").replace("ws://", "http://"))
    assert status == 404


async def test_health_stops_answering_after_stop() -> None:
    server, _ = await start()
    address = url(server, "/health").replace("ws://", "http://")
    assert (await asyncio.to_thread(_get, address))[0] == 200
    await server.stop()
    with pytest.raises(OSError):
        await asyncio.to_thread(_get, address)


# --- falhas de um pedido não derrubam a conexão ---------------------------------------------


async def test_handler_bug_gets_internal_error_and_connection_survives() -> None:
    class Buggy(RecordingHandler):
        async def handle(self, conn: Connection, message: p.ClientMessage) -> None:
            if isinstance(message, p.ChatSend):
                raise RuntimeError("bug no tratador")
            await super().handle(conn, message)

    server, handler = await start(Buggy())
    try:
        ws = await open_client(server)
        await ws.send(json.dumps({"type": "chat.send", "id": "c-1", "text": "oi"}))
        error = p.parse_message(await ws.recv(), source="server")
        assert isinstance(error, p.Error)
        assert (error.code, error.reply_to) == ("internal", "c-1")
        assert "bug no tratador" not in error.message  # o detalhe não vai ao cliente
        await ws.send(json.dumps({"type": "chat.cancel", "turn_id": "t"}))
        await wait_until(lambda: len(handler.messages) == 1)  # a conexão continua atendendo
        await ws.close()
    finally:
        await server.stop()


async def test_oversized_reply_becomes_an_error_instead_of_silence() -> None:
    class Big(RecordingHandler):
        async def handle(self, conn: Connection, message: p.ClientMessage) -> None:
            await conn.send(
                p.MemoryResult(reply_to="m-1", ok=True, data={"x": "a" * p.MAX_MESSAGE_BYTES})
            )

    server, _ = await start(Big())
    try:
        ws = await open_client(server)
        await ws.send(json.dumps({"type": "chat.cancel", "turn_id": "t"}))
        error = p.parse_message(await ws.recv(), source="server")
        assert isinstance(error, p.Error) and error.code == "internal"
        assert error.reply_to == "m-1"
        await ws.close()
    finally:
        await server.stop()


async def test_trace_pump_recovers_after_a_failed_drain() -> None:
    from datetime import UTC, datetime

    server, _ = await start(RecordingHandler())
    try:
        ws = await connect(url(server), additional_headers=auth())
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "protocol_version": 1,
                    "client": {"kind": "cli", "version": "t", "device_id": "dev-t"},
                    "trace": True,
                }
            )
        )
        await ws.recv()
        await wait_until(lambda: bool(server.connections))
        conn = server.connections[0]
        conn.wants_trace = True
        calls = {"n": 0}
        real = conn._drain_trace

        async def flaky() -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("falha ao enviar o passo")
            return await real()

        conn._drain_trace = flaky  # type: ignore[method-assign]
        conn.trace("Teste", "primeiro", datetime.now(UTC))
        await wait_until(lambda: conn._trace_pump is None)  # o pump morreu, mas liberou o lugar
        conn.trace("Teste", "segundo", datetime.now(UTC))
        message = p.parse_message(await asyncio.wait_for(ws.recv(), 2), source="server")
        assert isinstance(message, p.Trace)
        await ws.close()
    finally:
        await server.stop()
