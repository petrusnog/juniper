"""Tests for Agent loop, Dispatcher and Session using a scripted fake provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest

from brain.core.agent import EMPTY_REPLY_MESSAGE, LIMIT_MESSAGE, Agent
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


async def yes(name: str, args: dict[str, Any], tier: Tier, summary: str | None = None) -> bool:
    return True


async def no(name: str, args: dict[str, Any], tier: Tier, summary: str | None = None) -> bool:
    return False


def tool_texts(agent: Agent) -> list[str]:
    return [m.content or "" for m in agent.session.history if m.role == "tool"]


async def test_direct_answer_streams_tokens() -> None:
    agent, provider, _ = make_agent([say("Oi, ", "tudo bem?")])
    tokens: list[str] = []
    out = await agent.run("oi", on_token=tokens.append)
    assert out == "Oi, tudo bem?"
    # The start of an answer is held back to look for leaked planning (core/leak.py), so a short
    # answer may arrive in one piece.
    assert "".join(tokens) == "Oi, tudo bem?"
    first = provider.seen[0]
    assert first[0] == Message("system", "persona")
    assert first[1].role == "user" and first[1].content and first[1].content.endswith("] oi")


async def test_async_token_callback() -> None:
    agent, _, _ = make_agent([say("a", "b")])
    tokens: list[str] = []

    async def on_token(t: str) -> None:
        tokens.append(t)

    await agent.run("oi", on_token=on_token)
    assert "".join(tokens) == "ab"


async def test_long_answer_still_streams() -> None:
    sentence = "Isso é uma frase comum de conversa. "
    agent, _, _ = make_agent([say(*[sentence] * 12)])
    tokens: list[str] = []
    await agent.run("oi", on_token=tokens.append)
    assert "".join(tokens).strip() == (sentence * 12).strip()
    assert len(tokens) > 1  # not everything waited for the end


async def test_leaked_markup_never_reaches_the_user_or_the_history() -> None:
    agent, _, _ = make_agent([say("</｜DSML｜tool_calls>"), say("Pronto, Petrus.")])
    tokens: list[str] = []
    out = await agent.run("oi", on_token=tokens.append)
    assert out == "Pronto, Petrus."  # the cut reply was empty: one retry
    assert "DSML" not in "".join(tokens)
    assert all("DSML" not in (m.content or "") for m in agent.session.history)


async def test_two_empty_replies_become_a_sentence_never_a_null() -> None:
    agent, _, _ = make_agent([say(""), say("")])
    out = await agent.run("oi")
    assert out == EMPTY_REPLY_MESSAGE
    assert agent.session.history[-1].content == EMPTY_REPLY_MESSAGE


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
    async def slow(name: str, args: dict[str, Any], tier: Tier, summary: str | None = None) -> bool:
        await asyncio.sleep(5)
        return True

    agent, _, ran = make_agent([call("write_thing"), say("ok")], confirm=slow, timeout=0.05)
    await agent.run("altera")
    assert ran == []
    assert "negou" in tool_texts(agent)[0]


async def test_read_tier_skips_confirmation() -> None:
    asked: list[str] = []

    async def spy(name: str, args: dict[str, Any], tier: Tier, summary: str | None = None) -> bool:
        asked.append(name)
        return True

    agent, _, ran = make_agent([call("echo", text="x"), say("ok")], confirm=spy)
    await agent.run("faz")
    assert asked == [] and ran == ["echo"]


async def test_forbidden_never_runs_nor_asks() -> None:
    asked: list[str] = []

    async def spy(name: str, args: dict[str, Any], tier: Tier, summary: str | None = None) -> bool:
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


# --- describe(): the confirmation says what will happen ---------------------------------


def make_described_registry(describe: Any) -> ToolRegistry:
    reg = ToolRegistry()

    class Wipe(Tool):
        name = "wipe"
        description = "Apaga."
        parameters = {"type": "object", "properties": {"id": {"type": "string"}}}
        tier = Tier.DESTRUCTIVE

        async def describe(self, **kwargs: Any) -> str | None:
            return await describe(**kwargs)

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True, output="apagado")

    reg.register(Wipe)
    return reg


async def test_confirmation_receives_the_tool_summary() -> None:
    seen: list[str | None] = []

    async def confirm(
        name: str, args: dict[str, Any], tier: Tier, summary: str | None = None
    ) -> bool:
        seen.append(summary)
        return True

    async def describe(**kwargs: Any) -> str | None:
        return f"apagar o item {kwargs['id']}"

    dispatcher = Dispatcher(confirm, tool_registry=make_described_registry(describe))
    message = await dispatcher.dispatch(ToolCall("c1", "wipe", {"id": "42"}))
    assert seen == ["apagar o item 42"] and message.content == "apagado"


async def test_a_broken_describe_does_not_block_the_confirmation() -> None:
    seen: list[str | None] = []

    async def confirm(
        name: str, args: dict[str, Any], tier: Tier, summary: str | None = None
    ) -> bool:
        seen.append(summary)
        return True

    async def describe(**kwargs: Any) -> str | None:
        raise RuntimeError("falha na API")

    dispatcher = Dispatcher(confirm, tool_registry=make_described_registry(describe))
    message = await dispatcher.dispatch(ToolCall("c1", "wipe", {"id": "42"}))
    assert seen == [None] and message.content == "apagado"


async def test_tools_without_describe_confirm_with_no_summary() -> None:
    seen: list[str | None] = []

    async def confirm(
        name: str, args: dict[str, Any], tier: Tier, summary: str | None = None
    ) -> bool:
        seen.append(summary)
        return True

    agent, _, _ = make_agent([call("write_thing"), say("ok")], confirm=confirm)
    await agent.run("altera")
    assert seen == [None]


# --- requires_confirmation(): confirmation that depends on the call ---------------------


def make_gated_registry(hook: Any, level: Tier = Tier.WRITE) -> tuple[ToolRegistry, list[str]]:
    reg = ToolRegistry()
    ran: list[str] = []

    class Gated(Tool):
        name = "gated"
        description = "Faz algo, e só pergunta às vezes."
        parameters = {"type": "object", "properties": {"id": {"type": "string"}}}
        tier = level

        async def requires_confirmation(self, **kwargs: Any) -> bool:
            return await hook(**kwargs)

        async def run(self, **kwargs: Any) -> ToolResult:
            ran.append(kwargs.get("id", ""))
            return ToolResult(success=True, output="feito")

    reg.register(Gated)
    return reg, ran


def counting_confirm(asked: list[str]) -> Any:
    async def confirm(
        name: str, args: dict[str, Any], tier: Tier, summary: str | None = None
    ) -> bool:
        asked.append(args.get("id", ""))
        return True

    return confirm


async def test_a_write_tool_can_skip_the_question_for_a_safe_call() -> None:
    async def hook(**kwargs: Any) -> bool:
        return kwargs["id"] != "safe"

    reg, ran = make_gated_registry(hook)
    asked: list[str] = []
    dispatcher = Dispatcher(counting_confirm(asked), tool_registry=reg)
    safe = await dispatcher.dispatch(ToolCall("c1", "gated", {"id": "safe"}))
    risky = await dispatcher.dispatch(ToolCall("c2", "gated", {"id": "risky"}))
    assert safe.content == risky.content == "feito"
    assert ran == ["safe", "risky"]
    assert asked == ["risky"]  # only the call that needed it was put to the user


async def test_a_safe_call_runs_even_without_a_way_to_ask() -> None:
    async def hook(**kwargs: Any) -> bool:
        return False

    reg, ran = make_gated_registry(hook)
    message = await Dispatcher(None, tool_registry=reg).dispatch(
        ToolCall("c1", "gated", {"id": "x"})
    )
    assert message.content == "feito" and ran == ["x"]


async def test_a_destructive_tool_always_asks_whatever_the_hook_says() -> None:
    async def hook(**kwargs: Any) -> bool:
        return False

    reg, ran = make_gated_registry(hook, level=Tier.DESTRUCTIVE)
    asked: list[str] = []
    dispatcher = Dispatcher(counting_confirm(asked), tool_registry=reg)
    await dispatcher.dispatch(ToolCall("c1", "gated", {"id": "x"}))
    assert asked == ["x"] and ran == ["x"]
    denied = await Dispatcher(None, tool_registry=reg).dispatch(
        ToolCall("c2", "gated", {"id": "y"})
    )
    assert "negou" in (denied.content or "") and ran == ["x"]


async def test_a_broken_hook_asks_instead_of_skipping_the_question() -> None:
    async def hook(**kwargs: Any) -> bool:
        raise RuntimeError("falha na política")

    reg, ran = make_gated_registry(hook)
    asked: list[str] = []
    message = await Dispatcher(counting_confirm(asked), tool_registry=reg).dispatch(
        ToolCall("c1", "gated", {"id": "x"})
    )
    assert asked == ["x"] and message.content == "feito" and ran == ["x"]
    unattended = await Dispatcher(None, tool_registry=reg).dispatch(
        ToolCall("c2", "gated", {"id": "y"})
    )
    assert ran == ["x"] and "negou" in (unattended.content or "")


async def test_the_default_hook_follows_the_class_confirm_flag() -> None:
    reg, ran = make_registry()
    asked: list[str] = []
    dispatcher = Dispatcher(counting_confirm(asked), tool_registry=reg)
    await dispatcher.dispatch(ToolCall("c1", "write_thing", {}))
    assert ran == ["write_thing"] and asked == [""]

    class Quiet(Tool):
        name = "quiet"
        description = "Silenciosa."
        parameters = {"type": "object", "properties": {}}
        confirm = False

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True, output="ok")

    write = reg.get("write_thing")
    assert write is not None
    assert await Quiet().requires_confirmation() is False
    assert await write().requires_confirmation() is True


# --- canonical_arguments(): what is asked about is what runs -----------------------------


def make_canonical_registry(hook: Any) -> tuple[ToolRegistry, list[dict[str, Any]]]:
    reg = ToolRegistry()
    seen: list[dict[str, Any]] = []

    class Canon(Tool):
        name = "canon"
        description = "Normaliza o argumento."
        parameters = {"type": "object", "properties": {"id": {"type": "string"}}}

        async def canonical_arguments(self, **kwargs: Any) -> dict[str, Any]:
            return await hook(**kwargs)

        async def run(self, **kwargs: Any) -> ToolResult:
            seen.append(kwargs)
            return ToolResult(success=True, output="feito")

    reg.register(Canon)
    return reg, seen


async def test_the_question_and_the_run_use_the_canonical_arguments() -> None:
    async def hook(**kwargs: Any) -> dict[str, Any]:
        return {"id": kwargs["id"].strip().lower()}

    reg, seen = make_canonical_registry(hook)
    asked: list[str] = []
    await Dispatcher(counting_confirm(asked), tool_registry=reg).dispatch(
        ToolCall("c1", "canon", {"id": "  ABC "})
    )
    assert asked == ["abc"] and seen == [{"id": "abc"}]


async def test_a_broken_canonical_hook_keeps_the_original_arguments() -> None:
    async def hook(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("falha")

    reg, seen = make_canonical_registry(hook)
    asked: list[str] = []
    await Dispatcher(counting_confirm(asked), tool_registry=reg).dispatch(
        ToolCall("c1", "canon", {"id": "X"})
    )
    assert asked == ["X"] and seen == [{"id": "X"}]


async def test_the_default_canonical_arguments_are_the_same_values() -> None:
    reg, ran = make_registry()
    write = reg.get("write_thing")
    assert write is not None
    assert await write().canonical_arguments(a=1) == {"a": 1}


# --- one approval for the whole batch ----------------------------------------------------


def several(*calls: tuple[str, dict[str, Any]]) -> tuple[tuple[str, ...], tuple[ToolCall, ...]]:
    """One assistant message that requests several tool calls at once."""
    return (), tuple(ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(calls))


class Recorder:
    """Confirm callback that records every question and answers with a fixed value."""

    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.asked: list[tuple[str, dict[str, Any], Tier, str | None]] = []

    async def __call__(
        self, name: str, args: dict[str, Any], tier: Tier, summary: str | None = None
    ) -> bool:
        self.asked.append((name, args, tier, summary))
        return self.answer


def batch_registry() -> tuple[ToolRegistry, list[str]]:
    reg = ToolRegistry()
    ran: list[str] = []

    class Wipe(Tool):
        name = "wipe"
        description = "Apaga."
        parameters = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        }
        tier = Tier.DESTRUCTIVE

        async def precheck(self, **kwargs: Any) -> ToolResult | None:
            if kwargs["id"] == "ghost":
                return ToolResult(success=False, error="não existe")
            return None

        async def describe(self, **kwargs: Any) -> str | None:
            return f"apagar {kwargs['id']}"

        async def subjects(self, **kwargs: Any) -> list[str] | None:
            return [f"item {kwargs['id']}"]

        async def run(self, **kwargs: Any) -> ToolResult:
            ran.append(f"wipe:{kwargs['id']}")
            return ToolResult(success=True, output=f"apagado {kwargs['id']}")

    class Rename(Tool):
        name = "rename"
        description = "Renomeia."
        parameters = {"type": "object", "properties": {"id": {"type": "string"}}}
        tier = Tier.WRITE

        async def run(self, **kwargs: Any) -> ToolResult:
            ran.append("rename")
            return ToolResult(success=True, output="renomeado")

    class Peek(Tool):
        name = "peek"
        description = "Lê."
        parameters = {"type": "object", "properties": {}}
        tier = Tier.READ

        async def run(self, **kwargs: Any) -> ToolResult:
            ran.append("peek")
            return ToolResult(success=True, output="visto")

    for cls in (Wipe, Rename, Peek):
        reg.register(cls)
    return reg, ran


async def test_batch_asks_once_for_all_pending_actions() -> None:
    reg, ran = batch_registry()
    confirm = Recorder()
    calls = [ToolCall(f"c{i}", "wipe", {"id": str(i)}) for i in range(5)]
    messages = await Dispatcher(confirm, tool_registry=reg).dispatch_many(calls)

    assert len(confirm.asked) == 1  # one question for five deletions
    name, _, tier, summary = confirm.asked[0]
    assert tier is Tier.DESTRUCTIVE and name == "varias_acoes"
    assert summary is not None and summary.startswith("fazer estas 5 ações:")
    assert all(f"• apagar {i}" in summary for i in range(5))
    assert ran == [f"wipe:{i}" for i in range(5)]
    assert [m.tool_call_id for m in messages] == [f"c{i}" for i in range(5)]  # order kept
    assert [m.content for m in messages] == [f"apagado {i}" for i in range(5)]


async def test_batch_denial_denies_everything() -> None:
    reg, ran = batch_registry()
    confirm = Recorder(answer=False)
    calls = [ToolCall("a", "wipe", {"id": "1"}), ToolCall("b", "rename", {})]
    messages = await Dispatcher(confirm, tool_registry=reg).dispatch_many(calls)
    assert len(confirm.asked) == 1 and ran == []
    assert all("negou" in (m.content or "") for m in messages)


async def test_batch_mixes_reads_and_writes_asking_only_for_the_writes() -> None:
    reg, ran = batch_registry()
    confirm = Recorder()
    calls = [ToolCall("a", "peek", {}), ToolCall("b", "rename", {}), ToolCall("c", "peek", {})]
    messages = await Dispatcher(confirm, tool_registry=reg).dispatch_many(calls)
    assert len(confirm.asked) == 1 and confirm.asked[0][2] is Tier.WRITE
    assert confirm.asked[0][0] == "rename"  # a single pending action keeps its own name
    assert ran == ["peek", "rename", "peek"]
    assert [m.content for m in messages] == ["visto", "renomeado", "visto"]


async def test_batch_only_reads_never_asks() -> None:
    reg, ran = batch_registry()
    confirm = Recorder()
    await Dispatcher(confirm, tool_registry=reg).dispatch_many(
        [ToolCall("a", "peek", {}), ToolCall("b", "peek", {})]
    )
    assert confirm.asked == [] and ran == ["peek", "peek"]


async def test_batch_failed_prechecks_are_not_part_of_the_question() -> None:
    reg, ran = batch_registry()
    confirm = Recorder()
    calls = [
        ToolCall("a", "wipe", {"id": "1"}),
        ToolCall("b", "wipe", {"id": "ghost"}),  # precheck fails: nothing to approve
        ToolCall("c", "nope", {}),  # unknown tool
        ToolCall("d", "wipe", {"id": "2"}),
    ]
    messages = await Dispatcher(confirm, tool_registry=reg).dispatch_many(calls)
    assert len(confirm.asked) == 1
    summary = confirm.asked[0][3]
    assert summary is not None and "• apagar 1" in summary and "• apagar 2" in summary
    assert "ghost" not in summary
    assert ran == ["wipe:1", "wipe:2"]
    assert messages[1].content == "ERRO: não existe" and "desconhecida" in (
        messages[2].content or ""
    )


async def test_batch_with_mixed_tiers_escalates_to_destructive() -> None:
    reg, _ = batch_registry()
    confirm = Recorder()
    calls = [ToolCall("a", "rename", {}), ToolCall("b", "wipe", {"id": "1"})]
    await Dispatcher(confirm, tool_registry=reg).dispatch_many(calls)
    assert confirm.asked[0][2] is Tier.DESTRUCTIVE


async def test_batch_without_a_confirm_callback_denies() -> None:
    reg, ran = batch_registry()
    messages = await Dispatcher(None, tool_registry=reg).dispatch_many(
        [ToolCall("a", "wipe", {"id": "1"}), ToolCall("b", "wipe", {"id": "2"})]
    )
    assert ran == [] and all("negou" in (m.content or "") for m in messages)


async def test_batch_confirmation_times_out_as_denied() -> None:
    reg, ran = batch_registry()

    async def slow(name: str, args: dict[str, Any], tier: Tier, summary: str | None = None) -> bool:
        await asyncio.sleep(5)
        return True

    messages = await Dispatcher(slow, tool_registry=reg, confirmation_timeout=0.05).dispatch_many(
        [ToolCall("a", "wipe", {"id": "1"}), ToolCall("b", "wipe", {"id": "2"})]
    )
    assert ran == [] and all("negou" in (m.content or "") for m in messages)


async def test_batch_one_crash_does_not_stop_the_others() -> None:
    reg, ran = make_registry()  # has "boom" (READ, raises) and "echo"
    messages = await Dispatcher(yes, tool_registry=reg).dispatch_many(
        [ToolCall("a", "boom", {}), ToolCall("b", "echo", {"text": "oi"})]
    )
    assert "falha interna" in (messages[0].content or "") and messages[1].content == "eco: oi"
    assert ran == ["echo"]


async def test_agent_runs_a_parallel_batch_with_one_question() -> None:
    reg, ran = batch_registry()
    confirm = Recorder()
    provider = FakeProvider(
        [
            several(("wipe", {"id": "1"}), ("wipe", {"id": "2"}), ("wipe", {"id": "3"})),
            say("Apaguei os 3."),
        ]
    )
    agent = Agent(
        provider,
        Dispatcher(confirm, tool_registry=reg),
        Session("persona"),
        tool_registry=reg,
    )
    assert await agent.run("cancela tudo") == "Apaguei os 3."
    assert len(confirm.asked) == 1 and ran == ["wipe:1", "wipe:2", "wipe:3"]
    tool_messages = [m for m in agent.session.history if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["c0", "c1", "c2"]


async def test_each_call_of_a_batch_is_logged_with_its_decision() -> None:
    from brain.eventlog import event_log
    from tests.test_eventlog import read_events

    reg, _ = batch_registry()
    await Dispatcher(Recorder(), tool_registry=reg).dispatch_many(
        [ToolCall("a", "wipe", {"id": "1"}), ToolCall("b", "wipe", {"id": "ghost"})]
    )
    events = [e for e in read_events(event_log.directory) if e["event"] == "tool_call"]
    assert [e["decision"] for e in events] == ["confirmed", "precheck_failed"]


# --- conversational approval: ask in words, execute on the user's "sim" -------------------


def conv_agent(
    turns: list[tuple[tuple[str, ...], tuple[ToolCall, ...]]],
) -> tuple[Agent, FakeProvider, list[str], Dispatcher]:
    reg, ran = batch_registry()
    provider = FakeProvider(turns)
    dispatcher = Dispatcher(tool_registry=reg, conversational=True)
    agent = Agent(provider, dispatcher, Session("persona"), tool_registry=reg)
    return agent, provider, ran, dispatcher


def wipes(n: int) -> tuple[tuple[str, ...], tuple[ToolCall, ...]]:
    return several(*[("wipe", {"id": str(i)}) for i in range(n)])


def tool_texts_of(agent: Agent) -> list[str]:
    return [m.content or "" for m in agent.session.history if m.role == "tool"]


def assert_history_is_well_formed(agent: Agent) -> None:
    """Every tool call has exactly one answering tool message (providers reject otherwise)."""
    asked = [tc.id for m in agent.session.history for tc in m.tool_calls]
    answered = [m.tool_call_id for m in agent.session.history if m.role == "tool"]
    assert sorted(asked) == sorted(a for a in answered if a is not None)


async def test_conversational_flow_asks_in_words_then_executes_on_yes() -> None:
    agent, provider, ran, _ = conv_agent(
        [
            wipes(5),
            say("Posso apagar esses cinco agendamentos? É irreversível."),
            say("Apagado!"),
        ]
    )
    # Turn 1: the LLM proposes; NOTHING runs; it asks the question in plain words.
    first = await agent.run("cancela os meus compromissos de amanhã")
    assert first == "Posso apagar esses cinco agendamentos? É irreversível."
    assert ran == []
    notices = tool_texts_of(agent)
    assert len(notices) == 5 and all(n.startswith("PENDENTE, nada foi executado") for n in notices)
    assert "Diga que é irreversível" in notices[-1] and "as 5 ações" in notices[-1]

    # Turn 2: the user says yes; the system executes and the LLM reports.
    second = await agent.run("Sim.")
    assert second == "Apagado!"
    assert ran == [f"wipe:{i}" for i in range(5)]
    last_request = provider.seen[-1]  # what the LLM saw before answering "Apagado!"
    results = [m.content for m in last_request if m.role == "tool"][-5:]
    assert results == [f"apagado {i}" for i in range(5)]
    assert_history_is_well_formed(agent)


async def test_a_clear_no_cancels_and_nothing_runs() -> None:
    agent, _, ran, dispatcher = conv_agent([wipes(2), say("Posso apagar?"), say("Beleza, deixei.")])
    await agent.run("apaga")
    assert dispatcher.pending is not None
    assert await agent.run("Não") == "Beleza, deixei."
    assert ran == [] and dispatcher.pending is None
    assert_history_is_well_formed(agent)


@pytest.mark.parametrize(
    "reply", ["sim, mas só o primeiro", "quais são mesmo?", "o dentista é amanhã"]
)
async def test_a_qualified_or_unrelated_reply_never_executes(reply: str) -> None:
    agent, _, ran, dispatcher = conv_agent([wipes(2), say("Posso apagar?"), say("Entendi.")])
    await agent.run("apaga")
    await agent.run(reply)
    assert ran == [] and dispatcher.pending is None  # cleared: a stale yes can never fire later


async def test_a_later_yes_does_not_resurrect_an_old_proposal() -> None:
    agent, _, ran, _ = conv_agent(
        [wipes(1), say("Posso?"), say("Ok, outra coisa."), say("Sim o quê?")]
    )
    await agent.run("apaga")
    await agent.run("na verdade me mostra a agenda")  # not an answer: proposal is dropped
    await agent.run("sim")
    assert ran == []


async def test_reads_run_immediately_only_writes_wait() -> None:
    agent, _, ran, _ = conv_agent(
        [
            several(("peek", {}), ("rename", {}), ("peek", {})),
            say("Posso renomear?"),
        ]
    )
    await agent.run("renomeia")
    assert ran == ["peek", "peek"]  # the read-only calls ran; the rename is parked
    assert any("PENDENTE" in t for t in tool_texts_of(agent))


async def test_the_proposal_is_not_logged_as_confirmed() -> None:
    from brain.eventlog import event_log
    from tests.test_eventlog import read_events

    agent, _, _, _ = conv_agent([wipes(1), say("Posso?"), say("Feito.")])
    await agent.run("apaga")
    await agent.run("sim")
    decisions = [
        e["decision"] for e in read_events(event_log.directory) if e["event"] == "tool_call"
    ]
    assert decisions == ["pending", "confirmed"]


async def test_failed_prechecks_are_not_parked() -> None:
    agent, _, ran, dispatcher = conv_agent(
        [several(("wipe", {"id": "ghost"})), say("Esse não existe.")]
    )
    await agent.run("apaga o fantasma")
    assert dispatcher.pending is None and ran == []
    assert tool_texts_of(agent) == ["ERRO: não existe"]


async def test_execution_survives_a_failing_llm_after_approval() -> None:
    agent, _, ran, _ = conv_agent([wipes(1), say("Posso?")])  # no script left for the reply
    await agent.run("apaga")
    with pytest.raises(LLMUnavailableError):
        await agent.run("sim")
    assert ran == ["wipe:0"]  # it did run...
    texts = tool_texts_of(agent)
    assert any(t == "apagado 0" for t in texts)  # ...and the record of it is kept
    assert_history_is_well_formed(agent)


async def test_new_proposals_in_the_same_turn_are_merged() -> None:
    agent, _, ran, dispatcher = conv_agent(
        [wipes(1), several(("wipe", {"id": "9"})), say("Posso apagar os dois?"), say("Feito.")]
    )
    await agent.run("apaga")
    assert dispatcher.pending is not None and len(dispatcher.pending.plans) == 2
    await agent.run("sim")
    assert ran == ["wipe:0", "wipe:9"]


async def test_callback_mode_is_unchanged_for_non_conversational_clients() -> None:
    reg, ran = batch_registry()
    confirm = Recorder()
    dispatcher = Dispatcher(confirm, tool_registry=reg)  # conversational=False (default)
    await dispatcher.dispatch_many([ToolCall("a", "wipe", {"id": "1"})])
    assert len(confirm.asked) == 1 and ran == ["wipe:1"] and dispatcher.pending is None


async def test_the_system_asks_when_the_llm_forgets_to_ask() -> None:
    """Parked actions with a reply that is not a question: the system adds the question."""
    agent, _, ran, _ = conv_agent([wipes(3), say("Certo, vou cuidar disso.")])
    streamed: list[str] = []
    reply = await agent.run("apaga", on_token=streamed.append)
    assert reply.endswith("Posso fazer essas 3 ações? É irreversível.")
    assert "Posso fazer essas 3 ações?" in "".join(streamed)
    assert agent.session.history[-1].content == "Posso fazer essas 3 ações? É irreversível."
    assert ran == []


async def test_a_single_parked_action_is_asked_in_words() -> None:
    agent, _, _, _ = conv_agent([wipes(1), say("Hmm.")])
    reply = await agent.run("apaga")
    assert reply.endswith("Posso apagar 0? É irreversível.")


async def test_no_extra_question_when_the_llm_already_asked() -> None:
    agent, _, _, _ = conv_agent([wipes(2), say("Posso apagar os dois?")])
    assert await agent.run("apaga") == "Posso apagar os dois?"


async def test_a_yes_with_nothing_pending_gets_a_recovery_hint() -> None:
    """The assistant asked WITHOUT calling the tool; the "sim" must not silently do nothing."""
    agent, provider, ran, _ = conv_agent([say("Posso apagar o dentista?"), say("Vou propor.")])
    await agent.run("apaga o dentista")
    await agent.run("sim")
    assert ran == []  # a yes never executes anything that was not parked
    sent = provider.seen[-1][-1]
    assert sent.role == "user" and 'se esse "sim" confirma' in (sent.content or "")


async def test_no_hint_when_the_assistant_did_not_ask_anything() -> None:
    agent, provider, _, _ = conv_agent([say("Oi."), say("Beleza.")])
    await agent.run("oi")
    await agent.run("sim")
    assert 'se esse "sim" confirma' not in (provider.seen[-1][-1].content or "")


# --- approval recognised in the conversation: "sim" to a question that named the action -----


async def test_a_yes_to_a_question_that_named_the_items_runs_them_at_once() -> None:
    """The dialogue the user wants: JUNIPER asks, VOCÊ says sim, JUNIPER reports. No re-asking."""
    agent, provider, ran, dispatcher = conv_agent(
        [
            say("Achei item 1 e item 2. Posso apagar os dois? É irreversível."),
            wipes_ids("1", "2"),
            say("Apagado!"),
        ]
    )
    await agent.run("cancela tudo de amanhã")
    assert ran == []
    assert await agent.run("Sim.") == "Apagado!"
    assert ran == ["wipe:1", "wipe:2"] and dispatcher.pending is None
    assert not any("PENDENTE" in t for t in tool_texts_of(agent))
    assert_history_is_well_formed(agent)


def wipes_ids(*ids: str) -> tuple[tuple[str, ...], tuple[ToolCall, ...]]:
    return several(*[("wipe", {"id": i}) for i in ids])


async def test_a_vague_question_does_not_vouch_for_the_action() -> None:
    agent, _, ran, dispatcher = conv_agent(
        [say("Posso apagar esses dois? É irreversível."), wipes_ids("1", "2"), say("Posso mesmo?")]
    )
    await agent.run("cancela")
    await agent.run("sim")
    assert ran == [] and dispatcher.pending is not None  # titles were never named: ask properly


async def test_only_the_items_the_question_named_run_the_rest_is_asked() -> None:
    agent, _, ran, dispatcher = conv_agent(
        [say("Posso apagar o item 1?"), wipes_ids("1", "2"), say("E o item 2, posso?")]
    )
    await agent.run("cancela")
    await agent.run("sim")
    assert ran == ["wipe:1"]  # named: ran. "item 2" was never mentioned: parked
    assert dispatcher.pending is not None and len(dispatcher.pending.plans) == 1


async def test_a_yes_vouches_only_for_the_first_reaction_not_for_later_reads() -> None:
    """After new tool output (which a third party could have planted), ask again."""
    agent, _, ran, dispatcher = conv_agent(
        [
            say("Posso apagar o item 1?"),
            several(("peek", {})),  # the LLM reads something new first...
            wipes_ids("1"),  # ...and only then decides to delete
            say("Posso apagar o item 1, então?"),
        ]
    )
    await agent.run("cancela")
    await agent.run("sim")
    assert ran == ["peek"] and dispatcher.pending is not None


async def test_the_consent_belongs_to_one_turn_only() -> None:
    agent, _, ran, dispatcher = conv_agent(
        [say("Posso apagar o item 1?"), say("Ok, não apago."), wipes_ids("1"), say("Posso?")]
    )
    await agent.run("cancela")
    await agent.run("na verdade deixa quieto")  # not a yes
    await agent.run("apaga o item 1")  # a request, not an answer
    assert ran == [] and dispatcher.pending is not None


async def test_a_no_or_a_qualified_yes_gives_no_consent() -> None:
    for reply in ("não", "sim, mas só o item 2"):
        agent, _, ran, dispatcher = conv_agent(
            [say("Posso apagar o item 1?"), wipes_ids("1"), say("Certo, posso?")]
        )
        await agent.run("cancela")
        await agent.run(reply)
        assert ran == [] and dispatcher.pending is not None


async def test_a_question_that_is_not_the_last_thing_said_gives_no_consent() -> None:
    agent, _, ran, _ = conv_agent(
        [say("Posso apagar o item 1?"), say("Ok."), say("Beleza."), wipes_ids("1"), say("Posso?")]
    )
    await agent.run("cancela")
    await agent.run("espera")  # a new turn: the question is no longer what "sim" answers
    await agent.run("sim")
    assert ran == []


async def test_context_approval_is_logged_as_such() -> None:
    from brain.eventlog import event_log
    from tests.test_eventlog import read_events

    agent, _, _, _ = conv_agent([say("Posso apagar o item 1?"), wipes_ids("1"), say("Apagado!")])
    await agent.run("cancela")
    await agent.run("sim")
    decisions = [
        e["decision"] for e in read_events(event_log.directory) if e["event"] == "tool_call"
    ]
    assert decisions == ["confirmed_in_context"]


async def test_tools_without_subjects_are_never_approved_by_context() -> None:
    agent, _, ran, dispatcher = conv_agent(
        [say("Posso renomear?"), several(("rename", {})), say("Posso mesmo?")]
    )
    await agent.run("renomeia")
    await agent.run("sim")
    assert ran == [] and dispatcher.pending is not None


async def test_dashes_the_model_writes_never_reach_the_client_or_the_history() -> None:
    agent, _, _ = make_agent([(("Petrus ", "— tem dentista ", "amanhã —", " das 10h"), ())])
    shown: list[str] = []

    reply = await agent.run("e amanhã?", on_token=shown.append)

    assert reply == "Petrus, tem dentista amanhã, das 10h"
    assert "".join(shown) == reply
    assert agent.session.history[-1].content == reply
    assert not any(d in reply for d in "–—―")
