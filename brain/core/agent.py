"""Agent loop: decide → call tools → reassess, bounded by ``agent.max_iterations``.

Transport-agnostic (never imports ``brain.clients``). The client supplies the optional
``on_token`` streaming callback and, through the ``Dispatcher``, the confirmation callback.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from brain.config import settings
from brain.core.dispatcher import Dispatcher
from brain.core.session import Session
from brain.llm.base import LLMProvider
from brain.llm.types import Message, ToolCall
from brain.tools.registry import ToolRegistry
from brain.tools.registry import registry as default_registry

logger = logging.getLogger(__name__)

TokenCallback = Callable[[str], Awaitable[None] | None]

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
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self.session = session or Session()
        self._registry = tool_registry or default_registry
        self._max_iterations = max_iterations or settings.agent.max_iterations

    async def run(self, user_input: str, on_token: TokenCallback | None = None) -> str:
        """Answer ``user_input``, running tools as needed; return the final text.

        If the LLM fails, the session is rolled back to before this turn and the error
        propagates, so a retry does not see half a turn.
        """
        checkpoint = self.session.checkpoint()
        try:
            return await self._loop(user_input, on_token)
        except BaseException:
            self.session.rollback(checkpoint)
            raise

    async def _loop(self, user_input: str, on_token: TokenCallback | None) -> str:
        session = self.session
        session.add_user(user_input)
        session.compact()
        schemas = self._registry.schemas() or None

        for _ in range(self._max_iterations):
            message = await self._complete(schemas, on_token)
            session.add(message)
            if not message.tool_calls:
                return message.content or ""
            for call in message.tool_calls:
                session.add(await self._dispatcher.dispatch(call))

        session.add(Message("assistant", LIMIT_MESSAGE))
        if on_token is not None:
            await _emit(on_token, LIMIT_MESSAGE)
        return LIMIT_MESSAGE

    async def _complete(
        self, schemas: list[dict[str, object]] | None, on_token: TokenCallback | None
    ) -> Message:
        text: list[str] = []
        calls: list[ToolCall] = []
        async for chunk in self._provider.stream(self.session.messages(), tools=schemas):
            if chunk.delta:
                text.append(chunk.delta)
                if on_token is not None:
                    await _emit(on_token, chunk.delta)
            calls.extend(chunk.tool_calls)
        return Message("assistant", "".join(text) or None, tool_calls=tuple(calls))


async def _emit(callback: TokenCallback, token: str) -> None:
    result = callback(token)
    if result is not None:
        await result
