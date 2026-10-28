"""Executores conectados: registro, catálogo dinâmico e roteamento de tools (ADR-007, ADR-014;
T-078).

Um executor é um cliente que, depois do `welcome`, manda `register` com as tools que o
dispositivo sabe cumprir. O cérebro as oferece ao LLM como qualquer outra tool; quando o LLM as
chama, o `Dispatcher` entrega a chamada ao `ExecutorHub.invoke`, que manda um `invoke` ao
dispositivo certo e espera o `result`.

O cérebro não escala tier: manda ao dispositivo o tier que ele mesmo declarou, e recusa o
registro de uma tool que declara um tier mais permissivo do que a política que o cérebro já
tem para ela. Um executor também não pode tomar o lugar de uma tool do próprio servidor.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Final

from brain.clients import protocol as p
from brain.clients.server import Connection
from brain.core.dispatcher import validate_arguments
from brain.eventlog import event_log
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import ToolRegistry
from brain.tools.registry import registry as default_registry

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS: Final = 10_000

# Quem originou o turno em andamento: o executor dessa conexão tem preferência (ADR-014, seção 4).
current_origin: contextvars.ContextVar[Connection | None] = contextvars.ContextVar(
    "current_origin", default=None
)

_STRICTNESS: Final = {Tier.READ: 0, Tier.WRITE: 1, Tier.DESTRUCTIVE: 2, Tier.FORBIDDEN: 3}


def _unavailable(tool_name: str) -> ToolResult:
    return ToolResult(
        success=False,
        error="O dispositivo que faz isso não está conectado agora, então não consegui executar.",
        metadata={"code": "executor_unavailable", "tool": tool_name},
    )


def _failure(message: str, code: str, **extra: Any) -> ToolResult:
    return ToolResult(success=False, error=message, metadata={"code": code, **extra})


def _label(executor: ExecutorSession) -> str:
    return executor.conn.device_name or executor.conn.device_id


@dataclass
class RemoteTool:
    """Uma tool anunciada por um executor, já validada."""

    spec: p.ToolSpec
    tier: Tier
    cls: type[Tool]


@dataclass
class ExecutorSession:
    """O catálogo de uma conexão."""

    conn: Connection
    tools: dict[str, RemoteTool] = field(default_factory=dict)


def _remote_class(spec: p.ToolSpec, tier: Tier) -> type[Tool]:
    """A `Tool` que representa a tool remota para o LLM e para o `Dispatcher`.

    Nunca roda aqui: o `Dispatcher` entrega as de locality EXECUTOR ao gateway.
    """

    async def run(self: Tool, **kwargs: Any) -> ToolResult:
        raise RuntimeError("tool de executor: deve passar pelo gateway")

    return type(
        f"Remote_{spec.name}",
        (Tool,),
        {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
            "tier": tier,
            "locality": Locality.EXECUTOR,
            "run": run,
        },
    )


class ExecutorHub:
    """Quem está conectado como executor e o que cada um sabe fazer."""

    def __init__(
        self,
        base: ToolRegistry | None = None,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        unknown_tool_floor: Tier = Tier.WRITE,
    ) -> None:
        self._base = base or default_registry
        # Tier mínimo de uma tool que o servidor não conhece (só o dispositivo a descreve): ela
        # nunca roda sem confirmação, mesmo que o dispositivo a declare `read` (ADR-014).
        self._floor = unknown_tool_floor
        self._timeout_ms = timeout_ms
        self._executors: dict[str, ExecutorSession] = {}  # por id da conexão
        self._pending: dict[str, tuple[asyncio.Future[p.Result], ExecutorSession]] = {}

    # --- registro ---

    @property
    def executors(self) -> list[ExecutorSession]:
        return list(self._executors.values())

    def register(self, conn: Connection, specs: list[p.ToolSpec]) -> list[p.ToolSpec]:
        """Troca o catálogo da conexão; devolve as tools aceitas (as outras vão para o log)."""
        accepted: dict[str, RemoteTool] = {}
        for spec in specs:
            reason = self._refusal(spec, accepted)
            if reason is None:
                tier = Tier(spec.tier)
                effective = tier
                if (
                    self._base.get(spec.name) is None
                    and _STRICTNESS[tier] < _STRICTNESS[self._floor]
                ):
                    effective = self._floor  # o `invoke` ainda leva o tier declarado
                    event_log.log(
                        "executor",
                        action="tier_raised",
                        tool=spec.name,
                        declared=tier.value,
                        effective=effective.value,
                    )
                accepted[spec.name] = RemoteTool(spec, tier, _remote_class(spec, effective))
            else:
                event_log.log(
                    "executor", action="tool_refused", tool=spec.name, reason=reason, device=conn.id
                )
        if accepted:
            self._executors[conn.id] = ExecutorSession(conn, accepted)
        else:
            self._executors.pop(conn.id, None)
        event_log.log(
            "executor",
            action="register",
            device=conn.device_name or conn.device_id,
            tools=sorted(accepted),
        )
        return [t.spec for t in accepted.values()]

    def _refusal(self, spec: p.ToolSpec, accepted: dict[str, RemoteTool]) -> str | None:
        """Por que a tool não é aceita, ou `None`."""
        try:
            tier = Tier(spec.tier)
        except ValueError:
            return "tier desconhecido"
        if tier is Tier.FORBIDDEN:
            return "tier proibido"
        if spec.name in accepted:
            return "nome repetido"
        try:
            _remote_class(spec, tier)  # a validação da `Tool` (nome, schema) roda aqui
        except ValueError as exc:
            return str(exc)
        own = self._base.get(spec.name)
        if own is not None:
            if own.locality is not Locality.EXECUTOR:
                return "colide com uma tool do servidor"
            if _STRICTNESS[tier] > _STRICTNESS[own.tier]:
                return "tier mais permissivo que a política do servidor"
        return None

    def unregister(self, conn: Connection) -> None:
        """A conexão acabou: o catálogo some e as chamadas em andamento falham."""
        session = self._executors.pop(conn.id, None)
        if session is None:
            return
        for call_id, (future, owner) in list(self._pending.items()):
            if owner is session and not future.done():
                future.set_exception(ConnectionError(call_id))
        event_log.log("executor", action="unregister", device=_label(session))

    # --- visão para o Agent e o Dispatcher ---

    def remote_class(self, name: str) -> type[Tool] | None:
        """A tool de executor com esse nome (a do primeiro dispositivo que a oferece)."""
        for session in self._executors.values():
            remote = session.tools.get(name)
            if remote is not None:
                return remote.cls
        return None

    def remote_classes(self) -> list[type[Tool]]:
        seen: dict[str, type[Tool]] = {}
        for session in self._executors.values():
            for name, remote in session.tools.items():
                seen.setdefault(name, remote.cls)
        return list(seen.values())

    def offers(self, conn: Connection, tool_name: str) -> bool:
        """Whether this very connection is an executor that offers ``tool_name``."""
        session = self._executors.get(conn.id)
        return session is not None and tool_name in session.tools

    def _offering(self, tool_name: str) -> list[ExecutorSession]:
        return [s for s in self._executors.values() if tool_name in s.tools]

    # --- invoke / result ---

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Manda a chamada ao dispositivo escolhido e espera o resultado. Nunca levanta."""
        candidates = self._offering(tool_name)
        if not candidates:
            return _unavailable(tool_name)
        origin = current_origin.get()
        target = next((c for c in candidates if c.conn is origin), None)
        if target is None:
            if len(candidates) > 1:
                names = ", ".join(sorted(_label(c) for c in candidates))
                return _failure(
                    f"Mais de um dispositivo faz isso ({names}). Pergunte à pessoa qual usar.",
                    "ambiguous_executor",
                )
            target = candidates[0]
        remote = target.tools[tool_name]
        problem = validate_arguments(remote.spec.parameters, arguments)
        if problem is not None:  # nunca manda ao dispositivo o que ele mesmo recusaria
            return _failure(f"argumentos inválidos: {problem}", "invalid_args")
        return await self._call(target, remote, arguments)

    async def _call(
        self, target: ExecutorSession, remote: RemoteTool, arguments: dict[str, Any]
    ) -> ToolResult:
        call_id = f"k-{secrets.token_hex(6)}"
        future: asyncio.Future[p.Result] = asyncio.get_running_loop().create_future()
        self._pending[call_id] = (future, target)
        name = remote.spec.name
        started = time.monotonic()
        outcome = "ok"
        try:
            sent = await target.conn.send(
                p.Invoke(
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    timeout_ms=self._timeout_ms,
                    tier=remote.tier.value,
                )
            )
            if not sent:
                outcome = "executor_unavailable"
                return _unavailable(name)
            try:
                result = await asyncio.wait_for(future, self._timeout_ms / 1000)
            except TimeoutError:
                outcome = "timeout"
                return _failure(
                    f"O dispositivo {_label(target)} não respondeu a tempo.", "timeout", tool=name
                )
            except ConnectionError:
                outcome = "executor_disconnected"
                return _failure(
                    f"O dispositivo {_label(target)} desconectou durante a ação.",
                    "executor_disconnected",
                    tool=name,
                )
            outcome = "ok" if result.success else "failed"
            return ToolResult(
                success=result.success,
                output=result.output,
                error=result.error or (None if result.success else "o dispositivo recusou"),
                metadata=result.metadata,
            )
        finally:
            self._pending.pop(call_id, None)
            event_log.log(
                "executor",
                action="invoke",
                tool=name,
                tier=remote.tier.value,
                device=_label(target),
                outcome=outcome,
                duration_ms=round((time.monotonic() - started) * 1000),
            )

    def handle_result(self, conn: Connection, result: p.Result) -> None:
        """O `result` de um `invoke`. Só vale se vier da conexão que recebeu a chamada."""
        entry = self._pending.get(result.call_id)
        if entry is None:  # chegou depois do timeout (ou nunca foi pedido): ignora e registra
            event_log.log("executor", action="late_result", call_id=result.call_id)
            return
        future, owner = entry
        if owner.conn is not conn:
            event_log.log("executor", action="foreign_result", call_id=result.call_id)
            return
        if not future.done():
            future.set_result(result)


class ExecutorToolRegistry(ToolRegistry):
    """O catálogo que uma conversa enxerga: as tools do servidor mais as dos executores.

    As do servidor vencem em caso de nome repetido (um executor não toma o lugar delas).
    """

    def __init__(self, base: ToolRegistry, hub: ExecutorHub) -> None:
        super().__init__()
        self._base = base
        self._hub = hub

    def register(self, cls: type[Tool]) -> type[Tool]:
        raise TypeError("registre a tool no ToolRegistry do servidor, não nesta visão")

    def get(self, name: str) -> type[Tool] | None:
        return self._base.get(name) or self._hub.remote_class(name)

    def all(self) -> list[type[Tool]]:
        own = self._base.all()
        names = {t.name for t in own}
        return own + [t for t in self._hub.remote_classes() if t.name not in names]

    def schemas(self) -> list[dict[str, Any]]:
        return [cls.to_schema() for cls in self.all() if cls.available()]

    def discover(self, package: str = "brain.tools") -> None:
        self._base.discover(package)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def __len__(self) -> int:
        return len(self.all())
