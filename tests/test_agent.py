"""Tests for Agent loop, Dispatcher and Session using a scripted fake provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest

from brain.core.agent import LIMIT_MESSAGE, Agent
from brain.core.dispatcher import Dispatcher
from brain.core.session import Session
from brain.llm.base import LLMProvider
from brain.llm.errors import LLMUnavailableError
from brain.llm.types import LLMResponse, Message, StreamChunk, ToolCall
from brain.tools.base import Tier, Tool, ToolResult
from brain.tools.registry import ToolRegistry


class FakeProvider(LLMProvider):
    """Replays scripted turns; each turn is (text pieces, tool calls)."""

    name: ClassVar[str] = "fake"

    def __init__(self, turns: list[tuple[tuple[str, ...], tuple[ToolCall, ...]]]) -> None:
        self.turns = list(turns)
        self.seen: list[list[Message]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        self.seen.append(list(messages))
        if not self.turns:
            raise LLMUnavailableError("sem mais turnos")
        texts, calls = self.turns.pop(0)
        for piece in texts:
            yield StreamChunk(delta=piece)
        yield StreamChunk(tool_calls=calls, finish_reason="stop")

    async def health_check(self) -> bool:
        return True


def say(*pieces: str) -> tuple[tuple[str, ...], tuple[ToolCall, ...]]:
    return pieces, ()


def call(name: str, id: str = "c1", **args: Any) -> tuple[tuple[str, ...], tuple[ToolCall, ...]]:
    return (), (ToolCall(id, name, args),)


def make_registry() -> tuple[ToolRegistry, list[str]]:
    reg = ToolRegistry()
    ran: list[str] = []

    class Echo(Tool):
        name = "echo"
        description = "Repete o texto."
        parameters = {
            "type": "object",
            "properties": {"text": {"type": "string"}, "n": {"type": "integer"}},
            "required": ["text"],
        }
        tier = Tier.READ

        async def run(self, **kwargs: Any) -> ToolResult:
            ran.append("echo")
            return ToolResult(success=True, output=f"eco: {kwargs['text']}")

    class Write(Tool):
        name = "write_thing"
        description = "Altera algo."
        parameters = {"type": "object", "properties": {}}
        tier = Tier.WRITE

        async def run(self, **kwargs: Any) -> ToolResult:
            ran.append("write_thing")
            return ToolResult(success=True, output="feito")

    class Boom(Tool):
        name = "boom"
        description = "Quebra."
        parameters = {"type": "object", "properties": {}}
        tier = Tier.READ

        async def run(self, **kwargs: Any) -> ToolResult:
            raise RuntimeError("segredo-no-erro")

    class Nuke(Tool):
        name = "nuke"
        description = "Proibida."
        parameters = {"type": "object", "properties": {}}
        tier = Tier.FORBIDDEN

        async def run(self, **kwargs: Any) -> ToolResult:
            ran.append("nuke")
            return ToolResult(success=True)

    for cls in (Echo, Write, Boom, Nuke):
        reg.register(cls)
    return reg, ran


def make_agent(
    turns: list[tuple[tuple[str, ...], tuple[ToolCall, ...]]],
    confirm: Any = None,
    max_iterations: int = 5,
    timeout: float = 1,
) -> tuple[Agent, FakeProvider, list[str]]:
    reg, ran = make_registry()
    provider = FakeProvider(turns)
    dispatcher = Dispatcher(confirm, tool_registry=reg, confirmation_timeout=timeout)
    agent = Agent(
        provider, dispatcher, Session("persona"), tool_registry=reg, max_iterations=max_iterations
    )
    return agent, provider, ran


async def yes(name: str, args: dict[str, Any], tier: Tier) -> bool:
    return True


async def no(name: str, args: dict[str, Any], tier: Tier) -> bool:
    return False


def tool_texts(agent: Agent) -> list[str]:
    return [m.content or "" for m in agent.session.history if m.role == "tool"]


async def test_direct_answer_streams_tokens() -> None:
    agent, provider, _ = make_agent([say("Oi, ", "tudo bem?")])
    tokens: list[str] = []
    out = await agent.run("oi", on_token=tokens.append)
    assert out == "Oi, tudo bem?"
    assert tokens == ["Oi, ", "tudo bem?"]
    first = provider.seen[0]
    assert first[0] == Message("system", "persona")
    assert first[1].role == "user" and first[1].content and first[1].content.endswith("] oi")


async def test_async_token_callback() -> None:
    agent, _, _ = make_agent([say("a", "b")])
    tokens: list[str] = []

    async def on_token(t: str) -> None:
        tokens.append(t)

    await agent.run("oi", on_token=on_token)
    assert tokens == ["a", "b"]


async def test_single_tool_call() -> None:
    agent, _, ran = make_agent([call("echo", text="x"), say("pronto")])
    assert await agent.run("faz") == "pronto"
    assert ran == ["echo"]
    assert tool_texts(agent) == ["eco: x"]


async def test_chained_tool_calls() -> None:
    agent, _, ran = make_agent(
        [call("echo", "c1", text="a"), call("echo", "c2", text="b"), say("fim")]
    )
    assert await agent.run("faz") == "fim"
    assert ran == ["echo", "echo"]
    assert tool_texts(agent) == ["eco: a", "eco: b"]


async def test_iteration_limit() -> None:
    agent, _, ran = make_agent([call("echo", text="x")] * 5, max_iterations=3)
    tokens: list[str] = []
    out = await agent.run("loop", on_token=tokens.append)
    assert out == LIMIT_MESSAGE
    assert len(ran) == 3
    assert tokens[-1] == LIMIT_MESSAGE
    assert agent.session.history[-1] == Message("assistant", LIMIT_MESSAGE)


async def test_confirmation_granted_runs_tool() -> None:
    agent, _, ran = make_agent([call("write_thing"), say("ok")], confirm=yes)
    await agent.run("altera")
    assert ran == ["write_thing"]


async def test_confirmation_denied() -> None:
    agent, _, ran = make_agent([call("write_thing"), say("beleza")], confirm=no)
    assert await agent.run("altera") == "beleza"
    assert ran == []
    assert "negou" in tool_texts(agent)[0]


async def test_no_confirm_callback_denies_write() -> None:
    agent, _, ran = make_agent([call("write_thing"), say("ok")])
    await agent.run("altera")
    assert ran == []


async def test_confirmation_timeout_denies() -> None:
    async def slow(name: str, args: dict[str, Any], tier: Tier) -> bool:
        await asyncio.sleep(5)
        return True

    agent, _, ran = make_agent([call("write_thing"), say("ok")], confirm=slow, timeout=0.05)
    await agent.run("altera")
    assert ran == []
    assert "negou" in tool_texts(agent)[0]


async def test_read_tier_skips_confirmation() -> None:
    asked: list[str] = []

    async def spy(name: str, args: dict[str, Any], tier: Tier) -> bool:
        asked.append(name)
        return True

    agent, _, ran = make_agent([call("echo", text="x"), say("ok")], confirm=spy)
    await agent.run("faz")
    assert asked == [] and ran == ["echo"]


async def test_forbidden_never_runs_nor_asks() -> None:
    asked: list[str] = []

    async def spy(name: str, args: dict[str, Any], tier: Tier) -> bool:
        asked.append(name)
        return True

    agent, _, ran = make_agent([call("nuke"), say("não rola")], confirm=spy)
    assert await agent.run("faz") == "não rola"
    assert ran == [] and asked == []
    assert "proibida" in tool_texts(agent)[0]


async def test_unknown_tool_and_bad_arguments_are_reported_to_llm() -> None:
    agent, _, ran = make_agent(
        [
            call("inexistente", "a"),
            call("echo", "b"),  # missing required 'text'
            call("echo", "c", text=1),  # wrong type
            call("echo", "d", text="x", n=True),  # bool is not an integer
            call("echo", "e", text="x", extra=1),  # unknown argument
            say("ok"),
        ],
        max_iterations=6,
    )
    await agent.run("faz")
    assert ran == []
    texts = tool_texts(agent)
    assert "desconhecida" in texts[0]
    assert all(t.startswith("ERRO: argumentos inválidos") for t in texts[1:])


async def test_tool_exception_does_not_leak_message() -> None:
    agent, _, _ = make_agent([call("boom"), say("ok")])
    await agent.run("faz")
    text = tool_texts(agent)[0]
    assert "RuntimeError" in text and "segredo" not in text


async def test_llm_error_rolls_back_session() -> None:
    agent, _, _ = make_agent([call("echo", text="x")])  # second LLM call has no script → fails
    with pytest.raises(LLMUnavailableError):
        await agent.run("faz")
    assert agent.session.history == []


def test_session_compact_drops_old_tool_exchanges() -> None:
    s = Session("p", max_turns=2)
    for i in range(3):
        s.add(Message("user", f"u{i}"))
        s.add(Message("assistant", None, tool_calls=(ToolCall(f"c{i}", "echo", {}),)))
        s.add(Message("tool", "r", tool_call_id=f"c{i}"))
        s.add(Message("assistant", f"a{i}"))
    s.compact()
    assert [m.content for m in s.history if m.role == "user"] == ["u1", "u2"]
    assert [m.role for m in s.history].count("tool") == 2  # u0 lost its tool exchange
