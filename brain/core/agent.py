"""Agent loop: decide → call tools → reassess, bounded by ``agent.max_iterations``.

Transport-agnostic (never imports ``brain.clients``). The client supplies the optional
``on_token`` streaming callback and, through the ``Dispatcher``, the confirmation callback.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from brain.config import settings
from brain.core.consent import interpret_reply
from brain.core.dispatcher import Dispatcher, PendingBatch, tool_message
from brain.core.session import Session, Turn
from brain.core.style import DashFilter
from brain.eventlog import bind_session, event_log
from brain.llm.base import LLMProvider
from brain.llm.types import Message, TokenUsage, ToolCall
from brain.memory.retrieval import RecalledContext
from brain.memory.session import SessionStore
from brain.timing import Stages
from brain.tools.registry import ToolRegistry
from brain.tools.registry import registry as default_registry
from brain.trace import message_trace, quote

logger = logging.getLogger(__name__)

TokenCallback = Callable[[str], Awaitable[None] | None]


class ContextProvider(Protocol):
    """Long-term memory as the agent sees it (``brain.memory.longterm.LongTermMemory``)."""

    async def context_for(self, message: str, previous: str | None = None) -> RecalledContext: ...


NOTHING_PENDING_HINT = (
    '[Sistema: se esse "sim" confirma uma ação que você acabou de propor em texto, '
    "chame a ferramenta agora, citando os mesmos itens.]"
)

LIMIT_MESSAGE = (
    "Dei voltas demais e parei antes de terminar. Reformula o pedido ou divide em passos menores?"
)


class Agent:
    """Runs one user turn at a time over a ``Session``."""

    def __init__(
        self,
        provider: LLMProvider,
        dispatcher: Dispatcher,
        session: Session | None = None,
        tool_registry: ToolRegistry | None = None,
        max_iterations: int | None = None,
        store: SessionStore | None = None,
        memory: ContextProvider | None = None,
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self.session = session or Session()
        self._registry = tool_registry or default_registry
        self._max_iterations = max_iterations or settings.agent.max_iterations
        self._store = store
        self._memory = memory
        self._memory_message: Message | None = None
        self._profile_message: Message | None = None
        self._executed_until: int | None = None
        self._turns: list[Turn] = []
        # Where the time of the last turn went (T-062): total ms and ms per stage.
        self.last_timing: tuple[int, dict[str, int]] | None = None
        self._stages = Stages()
        self._recall_timings: dict[str, int] = {}
        self._llm_calls = 0

    def clear_pending(self) -> None:
        """Forget actions waiting for a "yes". Called when the conversation changes hands or is
        resumed: after that, nothing runs without a new, explicit confirmation (ADR-012)."""
        self._dispatcher.pending = None

    def drain_turns(self) -> list[Turn]:
        """The exchanges finished since the last call (for long-term memory extraction)."""
        turns, self._turns = self._turns, []
        return turns

    async def run(self, user_input: str, on_token: TokenCallback | None = None) -> str:
        """Answer ``user_input``, running tools as needed; return the final text.

        If the LLM fails, the session is rolled back to before this turn and the error
        propagates, so a retry does not see half a turn.
        """
        bind_session(self.session.id)
        event_log.log("turn_start", input_chars=len(user_input))
        checkpoint = self.session.checkpoint()
        self._executed_until = None
        self._stages = Stages()
        self._recall_timings = {}
        self._llm_calls = 0
        try:
            reply = await self._loop(user_input, on_token)
        except BaseException as exc:
            # Actions the user approved already ran: keep that record even if the LLM then fails.
            keep = self._executed_until if self._executed_until is not None else checkpoint
            self.session.rollback(keep)
            event_log.log("error", where="agent.run", type=type(exc).__name__, message=str(exc))
            with self._stages.stage("persist"):
                self._persist()
            self._log_timing()
            raise
        self._turns.append(Turn(user_input, reply))
        with self._stages.stage("persist"):
            self._persist()
        message_trace.step(
            "Agente",
            f"A resposta final ficou pronta ({len(reply)} caracteres) e a conversa foi salva"
            + (
                " no armazenamento de sessões."
                if self._store is not None
                else " (sem armazenamento)."
            ),
        )
        self._log_timing()
        return reply

    def _log_timing(self) -> None:
        """Record where this turn's time went (the ``timing`` event; the CLI may show it too)."""
        stages = self._stages.rounded()
        total = self._stages.total_ms()
        self.last_timing = (total, stages)
        event_log.log(
            "timing",
            scope="turn",
            total_ms=total,
            stages_ms=stages,
            llm_calls=self._llm_calls,
            recall_ms=self._recall_timings,
        )

    def _persist(self) -> None:
        """Save the conversation; a disk problem is logged, never allowed to end the chat."""
        if self._store is None:
            return
        try:
            self._store.save(self.session)
        except Exception as exc:
            event_log.log("error", where="session.save", type=type(exc).__name__, message=str(exc))

    async def _loop(self, user_input: str, on_token: TokenCallback | None) -> str:
        session = self.session
        previous = _last_user_text(session)
        session.add_user(user_input)
        message_trace.step(
            "Agente",
            f"Recebi a mensagem {quote(user_input)}. Acrescentei ao histórico da sessão "
            f"{session.id}, carimbada com a data e a hora (o histórico tem {len(session.history)} "
            f"{'mensagem' if len(session.history) == 1 else 'mensagens'} agora), e vou consultar "
            "a memória de longo prazo antes de falar com o modelo.",
        )
        with self._stages.stage("recall"):
            self._profile_message, self._memory_message = await self._recall(user_input, previous)
        pending, self._dispatcher.pending = self._dispatcher.pending, None
        said_yes = interpret_reply(user_input) == "yes"
        consent: str | None = None
        if pending is not None and said_yes:
            message_trace.step(
                "Agente",
                "Havia ação(ões) esperando a sua confirmação e você respondeu sim: o sistema "
                "(não o modelo) interpretou o sim e vai executar o que foi aprovado.",
            )
            await self._execute_approved(pending)
        elif pending is None and said_yes:
            # The user answered "sim" to something the assistant just said in plain words. If
            # the actions it now requests name exactly what that question named, they run.
            consent = _last_assistant_words(session)
            if consent is not None and "?" in consent:
                session.history[-1] = Message(
                    "user", f"{session.history[-1].content} {NOTHING_PENDING_HINT}"
                )
            else:
                consent = None
        session.compact()
        schemas = self._registry.schemas() or None

        for _ in range(self._max_iterations):
            message = await self._complete(schemas, on_token)
            session.add(message)
            if not message.tool_calls:
                return await self._ensure_question(message, on_token)
            # One assistant message = one batch, approved (or not) as a whole.
            # The "sim" only vouches for the FIRST reaction to it, never for what the LLM decides
            # after reading new tool output (which could contain text planted by a third party).
            with self._stages.stage("tools"):
                results = await self._dispatcher.dispatch_many(message.tool_calls, consent)
            consent = None
            for result in results:
                session.add(result)

        session.add(Message("assistant", LIMIT_MESSAGE))
        if on_token is not None:
            await _emit(on_token, LIMIT_MESSAGE)
        return LIMIT_MESSAGE

    async def _recall(
        self, user_input: str, previous: str | None
    ) -> tuple[Message | None, Message | None]:
        """The profile and the relevant memories as system messages. Never breaks the turn."""
        if self._memory is None:
            return None, None
        try:
            context = await self._memory.context_for(user_input, previous)
        except Exception as exc:
            # The chat goes on without memory; and without retrying it on every message.
            event_log.log("error", where="memory.recall", type=type(exc).__name__, message=str(exc))
            self._memory = None
            return None, None
        self._recall_timings = context.timings
        event_log.log("memory_recall", **context.audit())
        profile = Message("system", context.profile) if context.profile else None
        memories = Message("system", context.block) if context.block else None
        return profile, memories

    async def _ensure_question(self, message: Message, on_token: TokenCallback | None) -> str:
        """If actions are parked but the assistant did not ask, the system asks for it."""
        text = message.content or ""
        pending = self._dispatcher.pending
        if pending is None or "?" in text:
            return text
        question = pending.question()
        self.session.add(Message("assistant", question))
        if on_token is not None:
            await _emit(on_token, f"\n{question}")
        return f"{text}\n{question}".strip()

    async def _execute_approved(self, pending: PendingBatch) -> None:
        """The user said yes to what was proposed: run it and report it as a tool turn."""
        with self._stages.stage("tools"):
            outcomes = await self._dispatcher.execute_pending(pending)
        calls = tuple(ToolCall(f"{call.id}_ok", call.name, call.arguments) for call, _ in outcomes)
        self.session.add(Message("assistant", None, tool_calls=calls))
        for new_call, (_, result) in zip(calls, outcomes, strict=True):
            self.session.add(tool_message(new_call, result))
        self._executed_until = self.session.checkpoint()

    async def _complete(
        self, schemas: list[dict[str, object]] | None, on_token: TokenCallback | None
    ) -> Message:
        text: list[str] = []
        calls: list[ToolCall] = []
        usage: TokenUsage | None = None
        finish_reason: str | None = None
        messages = _with_context(
            self.session.messages(), self._profile_message, self._memory_message
        )
        event_log.log("llm_request", messages=len(messages), tools=len(schemas or []))
        started = time.monotonic()
        first_token = True
        self._llm_calls += 1
        dashes = DashFilter()  # the persona forbids dashes; this makes it hold (core/style.py)
        async for chunk in self._provider.stream(messages, tools=schemas):
            if first_token and (chunk.delta or chunk.tool_calls):
                first_token = False
                self._stages.add("llm_first_token", (time.monotonic() - started) * 1000)
            if chunk.delta:
                spoken = dashes.feed(chunk.delta)
                if spoken:
                    text.append(spoken)
                    if on_token is not None:
                        await _emit(on_token, spoken)
            calls.extend(chunk.tool_calls)
            usage = chunk.usage or usage
            finish_reason = chunk.finish_reason or finish_reason
        if rest := dashes.flush():
            text.append(rest)
            if on_token is not None:
                await _emit(on_token, rest)
        self._stages.add("llm", (time.monotonic() - started) * 1000)
        event_log.log(
            "llm_response",
            provider=getattr(self._provider, "last_provider", None) or self._provider.name,
            latency_ms=round((time.monotonic() - started) * 1000),
            finish_reason=finish_reason,
            usage=None if usage is None else dataclasses.asdict(usage),
            tool_calls=[c.name for c in calls],
            chars=sum(len(t) for t in text),
        )
        return Message("assistant", "".join(text) or None, tool_calls=tuple(calls))


async def _emit(callback: TokenCallback, token: str) -> None:
    result = callback(token)
    if result is not None:
        await result


def _last_user_text(session: Session) -> str | None:
    """The user's previous message without the date prefix, for short follow-up questions."""
    for message in reversed(session.history):
        if message.role == "user" and message.content:
            return re.sub(r"^\[[^\]]*\]\s*", "", message.content)
    return None


def _with_context(
    messages: list[Message], profile: Message | None, memory: Message | None
) -> list[Message]:
    """``messages`` with the user's profile and the recalled memories added for this turn.

    Neither is saved in the history. The profile (who the user is) changes rarely, so it goes
    right after the persona, in the stable part of the prompt that providers cache; the memories
    (relevant to this message) go just before the current user message.
    """
    out = list(messages)
    if memory is not None:
        for index in range(len(out) - 1, 0, -1):
            if out[index].role == "user":
                out.insert(index, memory)
                break
    if profile is not None and out and out[0].role == "system":
        out.insert(1, profile)
    return out


def _last_assistant_words(session: Session) -> str | None:
    """What the assistant said since the previous user message (the user's "sim" answers it)."""
    said: list[str] = []
    for message in reversed(session.history[:-1]):  # [-1] is the "sim" itself
        if message.role == "user":
            break
        if message.role == "assistant" and message.content:
            said.append(message.content)
    return "\n".join(reversed(said)) if said else None
