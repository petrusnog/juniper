"""Tool call dispatcher: registry lookup, argument validation, tier check, confirmation.

The core never prompts the user. Approval works in one of two ways. In *conversational* mode
(the default for chat, voice-friendly) the dispatcher parks the actions that need approval in
``pending`` and tells the LLM to ask the user in plain words; the agent executes them only when
the user's next message is a clear "yes" (``brain.core.consent``). Alternatively the client
injects an async ``confirm`` callback
(``confirm(tool_name, args, tier) -> bool``); the dispatcher bounds it with
``tools.security.confirmation_timeout``. Everything that goes wrong is returned to the
LLM as ``ToolResult(success=False)``; ``dispatch`` never raises. Sprint 2+ will route to
remote executors by ``Tool.locality`` (ADR-007) when a ``gateway`` is given; without one
everything runs locally.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from brain.config import settings
from brain.core.consent import mentions_all
from brain.eventlog import event_log
from brain.llm.types import Message, ToolCall
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import ToolRegistry
from brain.tools.registry import registry as default_registry

logger = logging.getLogger(__name__)

# confirm(tool_name, args, tier, summary) -> approved. ``summary`` is the tool's own description
# of what will happen (None when it has none).
ConfirmFn = Callable[[str, dict[str, Any], Tier, str | None], Awaitable[bool]]

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


def tool_message(call: ToolCall, result: ToolResult) -> Message:
    """The ``role="tool"`` message that reports ``result`` back to the LLM."""
    text = (result.output or "ok") if result.success else f"ERRO: {result.error}"
    return Message("tool", text, tool_call_id=call.id, name=call.name)


class RemoteGateway(Protocol):
    """Where ``Locality.EXECUTOR`` tools really run, when the brain is a server (ADR-007).

    ``invoke`` never raises: an executor that is offline, slow or gone comes back as a failed
    ``ToolResult`` with a message the LLM can pass on to the user.
    """

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult: ...


@dataclass
class _Plan:
    """One tool call after validation, before anything runs."""

    call: ToolCall
    tool_cls: type[Tool] | None
    tool: Tool | None = None
    early: ToolResult | None = None  # outcome already known (unknown tool, bad args, precheck...)
    decision: str = ""
    summary: str | None = None  # what the user is asked to approve (None: no confirmation needed)
    needs_confirm: bool = False
    in_context: bool = False  # approved by a "sim" to a question that named it
    started: float = field(default_factory=time.monotonic)


@dataclass
class PendingBatch:
    """Actions parked until the user says yes in the conversation."""

    plans: list[_Plan] = field(default_factory=list)

    @property
    def calls(self) -> list[ToolCall]:
        return [p.call for p in self.plans]

    @property
    def irreversible(self) -> bool:
        return any(p.tool_cls and p.tool_cls.tier is Tier.DESTRUCTIVE for p in self.plans)

    def question(self) -> str:
        """The question to ask in plain words, if the LLM did not ask it itself."""
        if len(self.plans) == 1:
            first = self.plans[0]
            text = f"Posso {first.summary or first.call.name}?"
        else:
            text = f"Posso fazer essas {len(self.plans)} ações?"
        return f"{text} É irreversível." if self.irreversible else text


class Dispatcher:
    """Runs the tool calls requested by the LLM.

    Calls that arrive together (one assistant message) are approved together, with a single
    question, instead of once per call (ADR-006).
    """

    def __init__(
        self,
        confirm: ConfirmFn | None = None,
        tool_registry: ToolRegistry | None = None,
        confirmation_timeout: float | None = None,
        *,
        conversational: bool = False,
        gateway: RemoteGateway | None = None,
    ) -> None:
        self._confirm = confirm
        # Set on the server: EXECUTOR tools go to the connected devices instead of running here.
        self._gateway = gateway
        self._conversational = conversational
        # Actions waiting for the user's "yes" (conversational mode). The agent consumes it.
        self.pending: PendingBatch | None = None
        self._registry = tool_registry or default_registry
        self._timeout = (
            confirmation_timeout
            if confirmation_timeout is not None
            else settings.tools.security.confirmation_timeout
        )

    async def dispatch(self, call: ToolCall) -> Message:
        """Execute one ``call`` and wrap the outcome as a ``role="tool"`` message."""
        return (await self.dispatch_many([call]))[0]

    async def execute(self, call: ToolCall) -> ToolResult:
        return (await self._run_all([call]))[0]

    async def dispatch_many(
        self, calls: Sequence[ToolCall], consent: str | None = None
    ) -> list[Message]:
        """Execute the calls of one assistant message; one message per call, in order.

        ``consent`` is what the assistant said to the user just before they answered a clear
        "yes" (conversational mode): actions whose subjects it names are already approved.
        """
        results = await self._run_all(calls, consent)
        return [tool_message(call, result) for call, result in zip(calls, results, strict=True)]

    async def _run_all(
        self, calls: Sequence[ToolCall], consent: str | None = None
    ) -> list[ToolResult]:
        plans = [await self._prepare(call) for call in calls]
        if self._conversational and consent:
            for plan in plans:
                if plan.needs_confirm and await _covered(plan, consent):
                    plan.needs_confirm = False  # the user already said yes to exactly this
                    plan.in_context = True
        pending = [p for p in plans if p.needs_confirm]
        if self._conversational and pending:
            return await self._park(plans, pending)
        approved = await self._confirmed(pending) if pending else True

        results: list[ToolResult] = []
        for plan in plans:
            if plan.early is not None:
                result, decision = plan.early, plan.decision
            elif plan.needs_confirm and not approved:
                result = ToolResult(success=False, error="o usuário negou a confirmação")
                decision = "denied"
            else:
                result, decision = await self._run(plan)
            self._log(plan, result, decision)
            results.append(result)
        return results

    async def _park(self, plans: list[_Plan], pending: list[_Plan]) -> list[ToolResult]:
        """Conversational approval: run what needs none, park the rest and tell the LLM to ask."""
        batch = self.pending or PendingBatch()
        batch.plans.extend(pending)
        self.pending = batch
        notice = _pending_notice(batch)
        results: list[ToolResult] = []
        for plan in plans:
            if plan.early is not None:
                result, decision = plan.early, plan.decision
            elif plan.needs_confirm:
                text = f"PENDENTE, nada foi executado: {plan.summary or plan.call.name}."
                if plan is pending[-1]:
                    text += f" {notice}"
                result, decision = ToolResult(success=True, output=text), "pending"
            else:
                result, decision = await self._run(plan)
            self._log(plan, result, decision)
            results.append(result)
        return results

    async def execute_pending(self, batch: PendingBatch) -> list[tuple[ToolCall, ToolResult]]:
        """Run a batch the user has just approved (the agent calls this after a clear "yes")."""
        outcomes: list[tuple[ToolCall, ToolResult]] = []
        for plan in batch.plans:
            result, decision = await self._run(plan)
            self._log(plan, result, "confirmed" if decision != "crashed" else decision)
            outcomes.append((plan.call, result))
        return outcomes

    async def _prepare(self, call: ToolCall) -> _Plan:
        """Everything that can be decided before running: lookup, tier, arguments, precheck."""
        tool_cls = self._registry.get(call.name)
        plan = _Plan(call, tool_cls)

        def settle(error: str, decision: str) -> _Plan:
            plan.early, plan.decision = ToolResult(success=False, error=error), decision
            return plan

        if tool_cls is None:
            return settle(f"ferramenta desconhecida: {call.name}", "unknown")
        if tool_cls.tier is Tier.FORBIDDEN:
            return settle("ação proibida: esta ferramenta nunca executa", "forbidden")
        problem = validate_arguments(tool_cls.parameters, call.arguments)
        if problem is not None:
            return settle(f"argumentos inválidos: {problem}", "invalid")

        try:
            plan.tool = tool_cls()
            # Fail early: never ask the user to approve something bound to fail (ADR-006).
            early = await plan.tool.precheck(**call.arguments)
            if early is not None:
                plan.early, plan.decision = early, "precheck_failed"
                return plan
            if tool_cls.tier is not Tier.READ and await _asks(plan.tool, tool_cls, call):
                plan.needs_confirm = True
                plan.summary = await _summary(plan.tool, call)
        except Exception as exc:  # a tool bug must not kill the agent
            plan.early, plan.decision = self._crashed(call, exc), "crashed"
        return plan

    async def _run(self, plan: _Plan) -> tuple[ToolResult, str]:
        assert plan.tool is not None and plan.tool_cls is not None  # early plans never get here
        decision = (
            "confirmed_in_context"
            if plan.in_context
            else "confirmed"
            if plan.needs_confirm
            else "auto"
        )
        try:
            if self._gateway is not None and plan.tool_cls.locality is Locality.EXECUTOR:
                return await self._gateway.invoke(plan.call.name, plan.call.arguments), decision
            return await plan.tool.run(**plan.call.arguments), decision
        except Exception as exc:  # type only, never the message: it may hold secrets
            return self._crashed(plan.call, exc), "crashed"

    @staticmethod
    def _crashed(call: ToolCall, exc: Exception) -> ToolResult:
        logger.exception("Tool %s falhou", call.name)
        event_log.log("error", where=f"tool.{call.name}", type=type(exc).__name__, message=str(exc))
        return ToolResult(
            success=False, error=f"falha interna da ferramenta ({type(exc).__name__})"
        )

    @staticmethod
    def _log(plan: _Plan, result: ToolResult, decision: str) -> None:
        event_log.log(
            "tool_call",
            name=plan.call.name,
            tier=plan.tool_cls.tier if plan.tool_cls is not None else None,
            args=plan.call.arguments,
            decision=decision,
            success=result.success,
            output=result.output if result.success else None,
            error=result.error,
            duration_ms=round((time.monotonic() - plan.started) * 1000),
        )

    async def _confirmed(self, pending: list[_Plan]) -> bool:
        """One approval for every pending action (they were requested together)."""
        if self._confirm is None:  # no way to ask: deny rather than act unattended
            return False
        first = pending[0]
        tier = (
            Tier.DESTRUCTIVE
            if any(p.tool_cls and p.tool_cls.tier is Tier.DESTRUCTIVE for p in pending)
            else Tier.WRITE
        )
        if len(pending) == 1:
            name, args, summary = first.call.name, first.call.arguments, first.summary
        else:
            name = "varias_acoes"
            args = {"acoes": [{"tool": p.call.name, **p.call.arguments} for p in pending]}
            lines = [p.summary or f"executar {p.call.name}" for p in pending]
            summary = f"fazer estas {len(pending)} ações:\n" + "\n".join(f"• {t}" for t in lines)
        try:
            return await asyncio.wait_for(
                self._confirm(name, args, tier, summary), timeout=self._timeout
            )
        except TimeoutError:
            logger.warning("Confirmação de %s expirou após %ss", name, self._timeout)
            return False


async def _covered(plan: _Plan, consent: str) -> bool:
    """Does the user's "yes" cover this action? Only if the question named all its subjects."""
    if plan.tool is None:
        return False
    try:
        subjects = await plan.tool.subjects(**plan.call.arguments)
    except Exception:  # noqa: BLE001
        return False
    return subjects is not None and mentions_all(consent, subjects)


async def _asks(tool: Tool, tool_cls: type[Tool], call: ToolCall) -> bool:
    """Whether this call must be confirmed. DESTRUCTIVE always asks; a broken hook asks too."""
    if tool_cls.tier is Tier.DESTRUCTIVE:
        return True
    try:
        return await tool.requires_confirmation(**call.arguments)
    except Exception:  # noqa: BLE001 - fail closed: a tool bug must not skip the question
        return True


async def _summary(tool: Tool, call: ToolCall) -> str | None:
    """The tool's description of the call, or None (a broken ``describe`` must not block it)."""
    try:
        return await tool.describe(**call.arguments)
    except Exception:  # noqa: BLE001
        return None


def _pending_notice(batch: PendingBatch) -> str:
    """Instruction to the LLM: ask the user in words; the system executes on a clear "yes"."""
    n = len(batch.plans)
    what = "a ação" if n == 1 else f"as {n} ações"
    warning = " Diga que é irreversível." if batch.irreversible else ""
    return (
        f"Agora pergunte à pessoa, numa frase curta e natural, se pode fazer {what} "
        f"(diga quantas são e, se forem poucas, o que são; sem ids).{warning} "
        "Não chame as ferramentas de novo e não diga que já fez: assim que ela responder sim, "
        "a execução é automática."
    )
