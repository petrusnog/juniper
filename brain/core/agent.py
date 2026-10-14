"""Agent loop: decide → call tools → reassess, bounded by ``agent.max_iterations``.

Transport-agnostic (never imports ``brain.clients``). The client supplies the optional
``on_token`` streaming callback and, through the ``Dispatcher``, the confirmation callback.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Awaitable, Callable

from brain.config import settings
from brain.core.consent import interpret_reply
from brain.core.dispatcher import Dispatcher, PendingBatch, tool_message
from brain.core.session import Session
from brain.eventlog import event_log
from brain.llm.base import LLMProvider
from brain.llm.types import Message, TokenUsage, ToolCall
from brain.memory.session import SessionStore
from brain.tools.registry import ToolRegistry
from brain.tools.registry import registry as default_registry

logger = logging.getLogger(__name__)

TokenCallback = Callable[[str], Awaitable[None] | None]

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
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self.session = session or Session()
        self._registry = tool_registry or default_registry
        self._max_iterations = max_iterations or settings.agent.max_iterations
        self._store = store
        self._executed_until: int | None = None

    async def run(self, user_input: str, on_token: TokenCallback | None = None) -> str:
        """Answer ``user_input``, running tools as needed; return the final text.

        If the LLM fails, the session is rolled back to before this turn and the error
        propagates, so a retry does not see half a turn.
        """
        checkpoint = self.session.checkpoint()
        self._executed_until = None
        try:
            reply = await self._loop(user_input, on_token)
        except BaseException as exc:
            # Actions the user approved already ran: keep that record even if the LLM then fails.
            keep = self._executed_until if self._executed_until is not None else checkpoint
            self.session.rollback(keep)
            event_log.log("error", where="agent.run", type=type(exc).__name__, message=str(exc))
            self._persist()
            raise
        self._persist()
        return reply

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
        session.add_user(user_input)
        pending, self._dispatcher.pending = self._dispatcher.pending, None
        said_yes = interpret_reply(user_input) == "yes"
        consent: str | None = None
        if pending is not None and said_yes:
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
            results = await self._dispatcher.dispatch_many(message.tool_calls, consent)
            consent = None
            for result in results:
                session.add(result)

        session.add(Message("assistant", LIMIT_MESSAGE))
        if on_token is not None:
            await _emit(on_token, LIMIT_MESSAGE)
        return LIMIT_MESSAGE

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
        messages = self.session.messages()
        event_log.log("llm_request", messages=len(messages), tools=len(schemas or []))
        started = time.monotonic()
        async for chunk in self._provider.stream(messages, tools=schemas):
            if chunk.delta:
                text.append(chunk.delta)
                if on_token is not None:
                    await _emit(on_token, chunk.delta)
            calls.extend(chunk.tool_calls)
            usage = chunk.usage or usage
            finish_reason = chunk.finish_reason or finish_reason
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


def _last_assistant_words(session: Session) -> str | None:
    """What the assistant said since the previous user message (the user's "sim" answers it)."""
    said: list[str] = []
    for message in reversed(session.history[:-1]):  # [-1] is the "sim" itself
        if message.role == "user":
            break
        if message.role == "assistant" and message.content:
            said.append(message.content)
    return "\n".join(reversed(said)) if said else None
