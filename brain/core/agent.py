"""Agent loop: decide → call tools → reassess, bounded by ``agent.max_iterations``.

Transport-agnostic (never imports ``brain.clients``). The client supplies the optional
``on_token`` streaming callback and, through the ``Dispatcher``, the confirmation callback.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from brain.config import settings
from brain.core.consent import interpret_reply
from brain.core.dispatcher import Dispatcher, PendingBatch, tool_message
from brain.core.leak import LeakGuard
from brain.core.leak_audit import Block, build_record, detect
from brain.core.session import Session, Turn
from brain.core.style import DashFilter
from brain.eventlog import bind_session, event_log
from brain.llm.base import LLMProvider
from brain.llm.types import Message, TokenUsage, ToolCall
from brain.memory.retrieval import RecalledContext
from brain.memory.session import SessionStore
from brain.timing import Stages
from brain.tools.channel import current_channel
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

EMPTY_REPLY_MESSAGE = "Me perdi aqui e não consegui responder direito. Pode repetir?"
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
        turn_sink: Callable[[str, Turn], Any] | None = None,
        self_docs: Callable[[str], str | None] | None = None,
        leak_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._provider = provider
        # Stores the evidence of a suspicious answer (T-165, core/leak_audit.py); None = log only.
        self._leak_sink = leak_sink
        self._dispatcher = dispatcher
        self.session = session or Session()
        self._registry = tool_registry or default_registry
        self._max_iterations = max_iterations or settings.agent.max_iterations
        self._store = store
        self._memory = memory
        # Called with (session id, finished turn) after each turn: the durable transcript (V2).
        self._turn_sink = turn_sink
        self._memory_message: Message | None = None
        # Excerpts of her own documentation when the message is about her (ADR-025).
        self._self_docs = self_docs
        self._docs_message: Message | None = None
        self._profile_message: Message | None = None
        # Which channel the turn comes from (set by the server before each turn; ADR-022).
        self.channel_note: str | None = None
        self._executed_until: int | None = None
        self._used_tools = False
        self._leaked = False  # a reply of this turn showed machinery (T-165)
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
        self._used_tools = False
        self._leaked = False
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
        self.session.turns_total += 1
        index = self.session.turns_total - 1
        if self._leaked:
            # What she said is kept out of memory (and of the night): the evidence is in the audit.
            event_log.log("leak_quarantined", turn=index)
        self._turns.append(
            Turn(
                user_input,
                "" if self._leaked else reply,
                index=index,
                used_tools=self._used_tools,
                leaked=self._leaked,
            )
        )
        with self._stages.stage("persist"):
            self._persist()
            await self._sink(self._turns[-1])
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

    async def _sink(self, turn: Turn) -> None:
        """Hand the turn to the sink; a problem is logged, never allowed to end the chat."""
        if self._turn_sink is None:
            return
        try:
            await asyncio.to_thread(self._turn_sink, self.session.id, turn)
        except Exception as exc:  # noqa: BLE001
            event_log.log("error", where="turn_sink", type=type(exc).__name__, message=str(exc))

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
        with self._stages.stage("docs"):
            self._docs_message = await self._docs(user_input)
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
        if scrubbed := session.scrub():
            event_log.log("history_scrubbed", messages=scrubbed)
        session.compact()
        schemas = self._registry.schemas() or None

        for _ in range(self._max_iterations):
            message = await self._complete(schemas, on_token)
            if not message.content and not message.tool_calls:
                # Nothing to say (an empty reply, or all of it was machinery cut by the guard):
                # one more try, then a plain sentence. An empty turn is never saved.
                event_log.log("llm_empty_reply")
                message = await self._complete(schemas, on_token)
            if not message.content and not message.tool_calls:
                message = Message("assistant", EMPTY_REPLY_MESSAGE)
                if on_token is not None:
                    await _emit(on_token, EMPTY_REPLY_MESSAGE)
            session.add(message)
            if not message.tool_calls:
                return await self._ensure_question(message, on_token)
            self._used_tools = True
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

    async def _docs(self, user_input: str) -> Message | None:
        """Documentation excerpts for this turn, if it is a question about her. Never breaks it.

        Runs in a thread: the first search builds the index (file reads), which must not block
        the event loop of the server."""
        if self._self_docs is None:
            return None
        try:
            block = await asyncio.to_thread(self._self_docs, user_input)
        except Exception as exc:  # noqa: BLE001  (the turn goes on without them)
            event_log.log("error", where="metadocs.agent", type=type(exc).__name__)
            return None
        if block:
            message_trace.step(
                "Agente",
                "A mensagem é uma pergunta sobre mim: juntei trechos da minha documentação, "
                "achados por busca local (sem LLM), para o modelo responder com base neles.",
            )
        return Message("system", block) if block else None

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
        self._used_tools = True
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
        raw: list[str] = []  # what the model wrote, before the guard: evidence for the audit
        calls: list[ToolCall] = []
        usage: TokenUsage | None = None
        finish_reason: str | None = None
        messages = _with_context(
            self.session.messages(),
            self._profile_message,
            self._memory_message,
            self.channel_note,
            self._docs_message,
        )
        event_log.log("llm_request", messages=len(messages), tools=len(schemas or []))
        started = time.monotonic()
        first_token = True
        self._llm_calls += 1
        guard = LeakGuard()  # keeps markup and prompt text out of her speech (core/leak.py)
        dashes = DashFilter()  # the persona forbids dashes; this makes it hold (core/style.py)
        async for chunk in self._provider.stream(messages, tools=schemas):
            if first_token and (chunk.delta or chunk.tool_calls):
                first_token = False
                self._stages.add("llm_first_token", (time.monotonic() - started) * 1000)
            if chunk.delta:
                raw.append(chunk.delta)
                spoken = dashes.feed(guard.feed(chunk.delta))
                if spoken:
                    text.append(spoken)
                    if on_token is not None:
                        await _emit(on_token, spoken)
            calls.extend(chunk.tool_calls)
            usage = chunk.usage or usage
            finish_reason = chunk.finish_reason or finish_reason
        if rest := dashes.feed(guard.flush()) + dashes.flush():
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
            leak_blocked=guard.triggered,
        )
        await self._audit(
            messages, "".join(raw), "".join(text), guard.triggered, finish_reason, calls
        )
        return Message("assistant", "".join(text) or None, tool_calls=tuple(calls))

    async def _audit(
        self,
        messages: list[Message],
        raw: str,
        delivered: str,
        guard_triggered: bool,
        finish_reason: str | None,
        calls: list[ToolCall],
    ) -> None:
        """Record a call whose text looks like machinery (T-165) and, when the person saw it, mark
        the turn as leaked (kept out of memory). Never changes the answer nor breaks the turn."""
        if not (raw or delivered):
            return
        try:
            hits = detect(
                raw,
                delivered,
                [m.content or "" for m in messages if m.role == "system"],
                echo_words=settings.leak_audit.echo_words,
            )
            if any(h.where == "delivered" for h in hits):
                self._leaked = True
            if not settings.leak_audit.enabled or (not hits and not guard_triggered):
                return
            blocks = self._blocks(messages)
            channel = current_channel.get()
            record = build_record(
                hits=hits,
                guard_triggered=guard_triggered,
                raw=raw,
                delivered=delivered,
                session_id=self.session.id,
                channel=channel.kind if channel is not None else None,
                voice=channel.voice if channel is not None else False,
                provider=getattr(self._provider, "last_provider", None) or self._provider.name,
                model=self._provider.model,
                finish_reason=finish_reason,
                tool_calls=[c.name for c in calls],
                blocks=blocks,
                user_message=_last_user_message(self.session),
                run=event_log.run_id,
            )
            event_log.log(
                "leak_audit",
                id=record["_id"],
                leaked=record["leaked"],
                guard=guard_triggered,
                rules=record["rules"],
                provider=record["provider"],
                model=record["model"],
                channel=record["channel"],
            )
            if self._leak_sink is not None:
                await asyncio.to_thread(self._leak_sink, record)
        except Exception as exc:  # noqa: BLE001  (the audit must never cost her the answer)
            event_log.log("error", where="leak_audit", type=type(exc).__name__, message=str(exc))

    def _blocks(self, messages: list[Message]) -> list[Block]:
        """The system messages of a call, named after what they are."""
        named = {
            id(m): kind
            for kind, m in (
                ("profile", self._profile_message),
                ("memories", self._memory_message),
                ("docs", self._docs_message),
            )
            if m is not None
        }
        blocks: list[Block] = []
        for index, message in enumerate(messages):
            if message.role != "system":
                continue
            kind = named.get(id(message))
            if kind is None:
                if index == 0:
                    kind = "persona"
                elif self.channel_note is not None and message.content == self.channel_note:
                    kind = "channel"
                else:
                    kind = "system"
            blocks.append(Block(kind, len(message.content or "")))
        return blocks


async def _emit(callback: TokenCallback, token: str) -> None:
    result = callback(token)
    if result is not None:
        await result


def _last_user_message(session: Session) -> str | None:
    """The user's latest message as the model read it (date stamp included)."""
    for message in reversed(session.history):
        if message.role == "user" and message.content:
            return message.content
    return None


def _last_user_text(session: Session) -> str | None:
    """The user's previous message without the date prefix, for short follow-up questions."""
    for message in reversed(session.history):
        if message.role == "user" and message.content:
            return re.sub(r"^\[[^\]]*\]\s*", "", message.content)
    return None


def _with_context(
    messages: list[Message],
    profile: Message | None,
    memory: Message | None,
    channel: str | None = None,
    docs: Message | None = None,
) -> list[Message]:
    """``messages`` with the user's profile, the recalled memories and (for a question about
    herself) documentation excerpts added for this turn.

    Neither is saved in the history. The profile (who the user is) changes rarely, so it goes
    right after the persona, in the stable part of the prompt that providers cache; the memories
    (relevant to this message) go just before the current user message.
    """
    out = list(messages)
    if channel:  # the very last thing the model reads before the answer: it is about this turn
        out.append(Message("system", channel))
    for block in (memory, docs):  # both about this message: memories, then docs, then it
        if block is None:
            continue
        for index in range(len(out) - 1, 0, -1):
            if out[index].role == "user":
                out.insert(index, block)
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
