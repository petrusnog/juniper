"""Executores: registro, catálogo por conexão e roteamento de tools (T-078).

O executor de teste é um cliente WebSocket que manda `register` e responde ao `invoke`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from brain.clients import protocol as p
from brain.clients.chat import ChatHandler
from brain.clients.executors import ExecutorHub
from brain.clients.server import BrainServer
from brain.core.dispatcher import Dispatcher
from brain.llm.types import ToolCall
from brain.memory.session import FileSessionStore
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import ToolRegistry
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import Client, connect_client, texts, wait_until

TOKEN = "token-de-teste"

OPEN_URL = {
    "name": "open_thing",
    "description": "Abre uma coisa no dispositivo.",
    "parameters": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
    "tier": "read",
}


def use(name: str, id: str = "k1", **args: Any) -> tuple[tuple[str, ...], tuple[ToolCall, ...]]:
    return (), (ToolCall(id, name, args),)


def server_registry() -> ToolRegistry:
    """O ToolRegistry do servidor: uma tool de nuvem e uma de executor (como `open_url`)."""
    registry = ToolRegistry()

    class CloudThing(Tool):
        name = "cloud_thing"
        description = "Roda no servidor."
        parameters = {"type": "object", "properties": {}}
        tier = Tier.READ
        locality = Locality.CLOUD

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True, output="rodou na nuvem")

    class StaticOpen(Tool):
        name = "static_open"
        description = "Abre algo no dispositivo."
        parameters = {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        }
        tier = Tier.WRITE
        locality = Locality.EXECUTOR

        async def run(self, **kwargs: Any) -> ToolResult:
            raise AssertionError("não pode rodar no servidor")

    registry.register(CloudThing)
    registry.register(StaticOpen)
    return registry


class Setup:
    def __init__(
        self,
        tmp_path: Path,
        provider: Any,
        *,
        timeout_ms: int = 5000,
    ) -> None:
        self.registry = server_registry()
        self.hub = ExecutorHub(self.registry, timeout_ms=timeout_ms)
        self.handler = ChatHandler(
            FileSessionStore(tmp_path / "sessions"),
            provider,
            tool_registry=self.registry,
            executors=self.hub,
        )
        self.server = BrainServer("127.0.0.1", 0, TOKEN, self.handler)

    async def __aenter__(self) -> Setup:
        await self.server.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.server.stop()

    async def executor(self, device: str, *tools: dict[str, Any]) -> tuple[Client, p.Registered]:
        client = await connect_client(self.server, device)
        await client.send(type="register", tools=list(tools))
        registered = (await client.until(p.Registered))[-1]
        assert isinstance(registered, p.Registered)
        return client, registered


def tool_result_seen_by_llm(provider: FakeProvider) -> str:
    return next(m.content or "" for m in reversed(provider.seen[-1]) if m.role == "tool")


# --- registro e catálogo ------------------------------------------------------------------


async def test_executor_registers_a_tool_and_the_agent_can_call_it(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://exemplo.com"), say("Abri a página.")])
    async with Setup(tmp_path, provider) as s:
        pc, registered = await s.executor("pc", OPEN_URL)
        assert [t.name for t in registered.tools] == ["open_thing"]
        assert "open_thing" in {x["function"]["name"] for x in s.handler._registry.schemas()}

        await pc.send(type="chat.send", id="c-1", text="abre o exemplo")
        invoke = (await pc.until(p.Invoke))[-1]
        assert isinstance(invoke, p.Invoke)
        assert invoke.tool_name == "open_thing"
        assert invoke.arguments == {"url": "https://exemplo.com"}
        assert invoke.tier == "read" and invoke.timeout_ms == 5000 and invoke.call_id
        await pc.send(type="result", call_id=invoke.call_id, success=True, output="Abri.")
        end = await pc.until(p.ChatEnd)
        assert texts(end) == "Abri a página."
    assert tool_result_seen_by_llm(provider) == "Abri."


async def test_tool_leaves_the_catalog_when_the_executor_disconnects(tmp_path: Path) -> None:
    async with Setup(tmp_path, FakeProvider([])) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        names = lambda: {x["function"]["name"] for x in s.handler._registry.schemas()}  # noqa: E731
        assert "open_thing" in names()
        await pc.ws.close()
        await wait_until(lambda: not s.hub.executors)
        assert "open_thing" not in names()
        assert s.handler._registry.get("open_thing") is None


async def test_registering_again_replaces_the_catalog(tmp_path: Path) -> None:
    other = {**OPEN_URL, "name": "other_thing"}
    async with Setup(tmp_path, FakeProvider([])) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        await pc.send(type="register", tools=[other])
        await pc.until(p.Registered)
        assert s.handler._registry.get("open_thing") is None
        assert s.handler._registry.get("other_thing") is not None


@pytest.mark.parametrize(
    ("tool", "reason"),
    [
        ({**OPEN_URL, "name": "cloud_thing"}, "tool do servidor"),
        ({**OPEN_URL, "tier": "forbidden"}, "tier proibido"),
        ({**OPEN_URL, "tier": "root"}, "tier desconhecido"),
        ({**OPEN_URL, "name": "Nome Ruim"}, "nome inválido"),
        ({**OPEN_URL, "parameters": {"type": "string"}}, "schema inválido"),
        ({**OPEN_URL, "name": "static_open", "tier": "destructive"}, "escalaria o tier"),
    ],
)
async def test_unsafe_registrations_are_refused(
    tmp_path: Path, tool: dict[str, Any], reason: str
) -> None:
    async with Setup(tmp_path, FakeProvider([])) as s:
        _, registered = await s.executor("pc", tool)
        assert registered.tools == [], reason
        assert s.hub.executors == []
        # a tool do servidor continua sendo a do servidor
        assert s.handler._registry.get("cloud_thing") is s.registry.get("cloud_thing")


async def test_executor_may_serve_a_server_tool_at_the_same_or_lower_tier(
    tmp_path: Path,
) -> None:
    async with Setup(tmp_path, FakeProvider([])) as s:
        _, registered = await s.executor("pc", {**OPEN_URL, "name": "static_open", "tier": "write"})
        assert [t.name for t in registered.tools] == ["static_open"]
        # a política do servidor (WRITE, com confirmação) é a que vale
        assert s.handler._registry.get("static_open") is s.registry.get("static_open")


# --- falhas -------------------------------------------------------------------------------


async def test_timeout_returns_an_error_result_without_blocking_the_turn(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://x.com"), say("Não consegui abrir.")])
    async with Setup(tmp_path, provider, timeout_ms=150) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        await pc.send(type="chat.send", id="c-1", text="abre")
        await pc.until(p.Invoke)  # o dispositivo recebe e não responde
        end = await pc.until(p.ChatEnd)
        assert texts(end) == "Não consegui abrir."
        assert isinstance(end[-1], p.ChatEnd) and end[-1].finish == "ok"
    assert "não respondeu a tempo" in tool_result_seen_by_llm(provider)


async def test_late_result_after_timeout_is_ignored(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://x.com"), say("Ok."), say("Segue.")])
    async with Setup(tmp_path, provider, timeout_ms=100) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        await pc.send(type="chat.send", id="c-1", text="abre")
        invoke = (await pc.until(p.Invoke))[-1]
        assert isinstance(invoke, p.Invoke)
        await pc.until(p.ChatEnd)
        await pc.send(type="result", call_id=invoke.call_id, success=True, output="tarde")
        assert texts(await pc.chat("e agora?", id="c-2")) == "Segue."


async def test_executor_dropping_mid_call_gives_the_llm_a_clear_error(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://x.com"), say("O PC caiu.")])
    async with Setup(tmp_path, provider) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        chat = await connect_client(s.server, "phone")  # quem conversa é outro cliente
        await chat.send(type="chat.send", id="c-1", text="abre no pc")
        await pc.until(p.Invoke)
        await pc.ws.close()
        end = await chat.until(p.ChatEnd)
        assert texts(end) == "O PC caiu."
    assert "desconectou durante a ação" in tool_result_seen_by_llm(provider)


async def test_arguments_off_schema_never_reach_the_executor(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url=123), say("Não deu.")])
    async with Setup(tmp_path, provider) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        await pc.send(type="chat.send", id="c-1", text="abre")
        got = await pc.until(p.ChatEnd)
        assert not any(isinstance(m, p.Invoke) for m in got)
    assert "argumentos inválidos" in tool_result_seen_by_llm(provider)


async def test_hub_validates_against_the_declared_schema_too(tmp_path: Path) -> None:
    async with Setup(tmp_path, FakeProvider([])) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        result = await s.hub.invoke("open_thing", {"url": 123})
        assert not result.success and result.metadata == {"code": "invalid_args"}
        with pytest.raises(asyncio.TimeoutError):  # nada foi enviado ao dispositivo
            async with asyncio.timeout(0.2):
                await pc.recv()


async def test_no_executor_connected_says_the_device_is_unavailable(tmp_path: Path) -> None:
    provider = FakeProvider(
        [use("static_open", url="https://x.com"), say("Seu PC está offline."), say("Ok")]
    )
    async with Setup(tmp_path, provider) as s:
        chat = await connect_client(s.server)
        got = await chat.chat("abre o site")
        assert texts(got) != ""
        # a tool é WRITE: a conversa pergunta primeiro; o "sim" executa
        got = await chat.chat("sim", id="c-2")
        assert isinstance(got[-1], p.ChatEnd) and got[-1].finish == "ok"
    seen = tool_result_seen_by_llm(provider)
    assert "não está conectado" in seen and "Traceback" not in seen


async def test_server_tools_still_run_on_the_server(tmp_path: Path) -> None:
    provider = FakeProvider([use("cloud_thing"), say("Feito.")])
    async with Setup(tmp_path, provider) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        got = await pc.chat("roda")
        assert not any(isinstance(m, p.Invoke) for m in got)
    assert tool_result_seen_by_llm(provider) == "rodou na nuvem"


# --- vários executores --------------------------------------------------------------------


async def test_the_executor_of_the_originating_connection_is_preferred(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://x.com"), say("Aberto no notebook.")])
    async with Setup(tmp_path, provider) as s:
        desktop, _ = await s.executor("desktop", OPEN_URL)
        laptop, _ = await s.executor("laptop", OPEN_URL)
        await laptop.send(type="chat.send", id="c-1", text="abre")
        invoke = (await laptop.until(p.Invoke))[-1]
        assert isinstance(invoke, p.Invoke)
        await laptop.send(type="result", call_id=invoke.call_id, success=True, output="ok")
        await laptop.until(p.ChatEnd)
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.2):
                await desktop.recv()  # o outro não recebeu nada


async def test_a_single_other_executor_is_used_when_the_origin_has_none(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://x.com"), say("Pronto.")])
    async with Setup(tmp_path, provider) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        chat = await connect_client(s.server, "phone")
        await chat.send(type="chat.send", id="c-1", text="abre no pc")
        invoke = (await pc.until(p.Invoke))[-1]
        assert isinstance(invoke, p.Invoke)
        await pc.send(type="result", call_id=invoke.call_id, success=True, output="ok")
        assert texts(await chat.until(p.ChatEnd)) == "Pronto."


async def test_ambiguity_is_reported_with_the_device_names(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://x.com"), say("Qual deles?")])
    async with Setup(tmp_path, provider) as s:
        await s.executor("pc-casa", OPEN_URL)
        await s.executor("pc-trabalho", OPEN_URL)
        chat = await connect_client(s.server, "phone")
        await chat.chat("abre")
    seen = tool_result_seen_by_llm(provider)
    assert "pc-casa" in seen and "pc-trabalho" in seen


async def test_result_from_a_connection_that_was_not_asked_is_ignored(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://x.com"), say("Pronto.")])
    async with Setup(tmp_path, provider, timeout_ms=400) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        intruder = await connect_client(s.server, "intruso")
        await pc.send(type="chat.send", id="c-1", text="abre")
        invoke = (await pc.until(p.Invoke))[-1]
        assert isinstance(invoke, p.Invoke)
        await intruder.send(type="result", call_id=invoke.call_id, success=True, output="falso")
        await pc.until(p.ChatEnd)  # sem resposta do dono: dá timeout
    assert "falso" not in tool_result_seen_by_llm(provider)


async def test_cancelling_a_turn_waiting_on_a_device_cleans_up(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_thing", url="https://x.com")])
    async with Setup(tmp_path, provider) as s:
        pc, _ = await s.executor("pc", OPEN_URL)
        await pc.send(type="chat.send", id="c-1", text="abre")
        started = (await pc.until(p.ChatStarted))[-1]
        assert isinstance(started, p.ChatStarted)
        await pc.until(p.Invoke)
        await pc.send(type="chat.cancel", turn_id=started.turn_id)
        end = (await pc.until(p.ChatEnd))[-1]
        assert isinstance(end, p.ChatEnd) and end.finish == "cancelled"
        assert s.hub._pending == {}


def test_dispatcher_without_gateway_still_runs_executor_tools_locally() -> None:
    """No modo local (CLI), tools de locality EXECUTOR continuam rodando no próprio processo."""
    ran: list[str] = []
    registry = ToolRegistry()

    class Local(Tool):
        name = "local_thing"
        description = "Roda aqui."
        parameters = {"type": "object", "properties": {}}
        tier = Tier.READ
        locality = Locality.EXECUTOR

        async def run(self, **kwargs: Any) -> ToolResult:
            ran.append("local")
            return ToolResult(success=True, output="ok")

    registry.register(Local)
    result = asyncio.run(Dispatcher(tool_registry=registry).execute(ToolCall("1", "local_thing")))
    assert result.success and ran == ["local"]
