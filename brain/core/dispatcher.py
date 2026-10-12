"""Tool call dispatcher: registry lookup, argument validation, tier check, confirmation.

The core never prompts the user. The client injects an async ``confirm`` callback
(``confirm(tool_name, args, tier) -> bool``); the dispatcher bounds it with
``tools.security.confirmation_timeout``. Everything that goes wrong is returned to the
LLM as ``ToolResult(success=False)``; ``dispatch`` never raises. Sprint 2+ will route to
remote executors by ``Tool.locality`` (ADR-007); for now everything runs locally.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from brain.config import settings
from brain.eventlog import event_log
from brain.llm.types import Message, ToolCall
from brain.tools.base import Tier, Tool, ToolResult
from brain.tools.registry import ToolRegistry
from brain.tools.registry import registry as default_registry

logger = logging.getLogger(__name__)

ConfirmFn = Callable[[str, dict[str, Any], Tier], Awaitable[bool]]

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_arguments(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Check ``args`` against the top-level JSON Schema of a tool; return an error or None."""
    properties: dict[str, Any] = schema.get("properties", {})
    missing = [k for k in schema.get("required", []) if k not in args]
    if missing:
        return f"argumentos obrigatórios ausentes: {', '.join(missing)}"
    unknown = [k for k in args if k not in properties]
    if unknown:
        return f"argumentos desconhecidos: {', '.join(unknown)}"
    for key, value in args.items():
        expected = _JSON_TYPES.get(properties[key].get("type", ""))
        if expected is None:
            continue
        is_bool = isinstance(value, bool)
        # bool is an int subclass in Python, but not a JSON integer/number.
        if not isinstance(value, expected) or (is_bool and bool not in expected):
            return f"argumento '{key}' deve ser {properties[key]['type']}"
    return None


class Dispatcher:
    """Runs the tool calls requested by the LLM."""

    def __init__(
        self,
        confirm: ConfirmFn | None = None,
        tool_registry: ToolRegistry | None = None,
        confirmation_timeout: float | None = None,
    ) -> None:
        self._confirm = confirm
        self._registry = tool_registry or default_registry
        self._timeout = (
            confirmation_timeout
            if confirmation_timeout is not None
            else settings.tools.security.confirmation_timeout
        )

    async def dispatch(self, call: ToolCall) -> Message:
        """Execute ``call`` and wrap the outcome as a ``role="tool"`` message."""
        result = await self.execute(call)
        text = (result.output or "ok") if result.success else f"ERRO: {result.error}"
        return Message("tool", text, tool_call_id=call.id, name=call.name)

    async def execute(self, call: ToolCall) -> ToolResult:
        started = time.monotonic()
        tool_cls = self._registry.get(call.name)
        tier = tool_cls.tier if tool_cls is not None else None
        result, decision = await self._decide_and_run(call, tool_cls)
        event_log.log(
            "tool_call",
            name=call.name,
            tier=tier,
            args=call.arguments,
            decision=decision,
            success=result.success,
            output=result.output if result.success else None,
            error=result.error,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return result

    async def _decide_and_run(
        self, call: ToolCall, tool_cls: type[Tool] | None
    ) -> tuple[ToolResult, str]:
        """Run the call; returns the result and the decision taken (for the log)."""
        if tool_cls is None:
            return ToolResult(
                success=False, error=f"ferramenta desconhecida: {call.name}"
            ), "unknown"
        if tool_cls.tier is Tier.FORBIDDEN:
            error = "ação proibida: esta ferramenta nunca executa"
            return ToolResult(success=False, error=error), "forbidden"
        problem = validate_arguments(tool_cls.parameters, call.arguments)
        if problem is not None:
            return ToolResult(success=False, error=f"argumentos inválidos: {problem}"), "invalid"

        try:
            tool: Tool = tool_cls()
            # Fail early: never ask the user to approve something bound to fail (ADR-006).
            early = await tool.precheck(**call.arguments)
            if early is not None:
                return early, "precheck_failed"
            if tool_cls.tier is not Tier.READ and not await self._confirmed(call, tool_cls.tier):
                return ToolResult(success=False, error="o usuário negou a confirmação"), "denied"
            decision = "auto" if tool_cls.tier is Tier.READ else "confirmed"
            return await tool.run(**call.arguments), decision
        except Exception as exc:  # a tool bug must not kill the agent; type only, never the message
            logger.exception("Tool %s falhou", call.name)
            event_log.log(
                "error", where=f"tool.{call.name}", type=type(exc).__name__, message=str(exc)
            )
            error = f"falha interna da ferramenta ({type(exc).__name__})"
            return ToolResult(success=False, error=error), "crashed"

    async def _confirmed(self, call: ToolCall, tier: Tier) -> bool:
        if self._confirm is None:  # no way to ask: deny rather than act unattended
            return False
        try:
            return await asyncio.wait_for(
                self._confirm(call.name, call.arguments, tier), timeout=self._timeout
            )
        except TimeoutError:
            logger.warning("Confirmação de %s expirou após %ss", call.name, self._timeout)
            return False
