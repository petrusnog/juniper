"""Comandos de memória pelo servidor (ADR-012, seção 7; T-077).

Cada `memory.request` vira a mesma chamada que o `juniper memory ...` faz hoje, só que na
memória do servidor: existe uma memória só. A confirmação do `forget` fica no cliente, antes
de mandar o pedido. `memory reindex` é administrativo e local ao servidor: não passa por aqui.

Falhas esperadas viram `error` tipado (a conexão continua aberta); nada aqui levanta para o
servidor. Os textos devolvidos passam pelo filtro de segredos da memória, e o log guarda só
a operação, nunca o que foi perguntado ou devolvido.
"""

from __future__ import annotations

import asyncio
from typing import Any

from brain.clients import protocol as p
from brain.eventlog import event_log
from brain.memory.errors import MemoryUnavailableError
from brain.memory.longterm import LongTermMemory
from brain.memory.profile import ProfileError
from brain.memory.secretfilter import mask_secrets
from brain.memory.vector import Memory, SearchHit, VectorStoreError

DISABLED_MESSAGE = (
    "A memória de longo prazo não está disponível neste servidor "
    "(precisa de memory.session.store: mongo)."
)


def _memory_row(m: Memory) -> dict[str, Any]:
    return {
        "id": m.id,
        "text": mask_secrets(m.text),
        "type": m.type,
        "status": m.status,
        "status_reflexao": m.status_reflexao,
        "created_at": m.created_at.isoformat(),
    }


def _hit_row(hit: SearchHit) -> dict[str, Any]:
    return {
        "id": hit.memory.id,
        "text": mask_secrets(hit.memory.text),
        "type": hit.memory.type,
        "score": round(hit.score, 4),
    }


def _profile_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        **row,
        "text": mask_secrets(str(row["text"])),
        "rendered": mask_secrets(row["rendered"]),
    }


class MemoryNotFoundError(Exception):
    """No memory with that id."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MemoryService:
    """Executa `memory.request` na memória de longo prazo do servidor."""

    def __init__(self, memory: LongTermMemory | None) -> None:
        self._memory = memory

    async def handle(self, request: Any) -> p.MemoryResult | p.Error:
        """O `memory.result` do pedido, ou o `error` que o descreve. Nunca levanta."""
        op = str(request.op)
        try:
            if self._memory is None:
                raise MemoryUnavailableError(DISABLED_MESSAGE)
            data = await self._run(self._memory, request)
        except MemoryUnavailableError as exc:
            return self._failed(op, request.id, p.Error.from_exception(exc, str(exc), request.id))
        except MemoryNotFoundError as exc:
            return self._failed(
                op, request.id, p.Error.from_code("not_found", exc.message, request.id)
            )
        except (ProfileError, VectorStoreError, ValueError) as exc:
            return self._failed(
                op, request.id, p.Error.from_code("bad_request", str(exc), request.id)
            )
        except Exception as exc:
            event_log.log("error", where="memory.request", op=op, type=type(exc).__name__)
            return self._failed(
                op,
                request.id,
                p.Error.from_code(
                    "internal", "Não consegui falar com a memória agora.", request.id
                ),
            )
        result = p.MemoryResult(reply_to=request.id, ok=True, data=data)
        try:
            p.serialize_message(
                result
            )  # cabe em um quadro (1 MiB)? Senão o cliente ficaria sem resposta
        except p.InvalidMessageError:
            hint = "Exporte sem vetores." if op == "export" else "Filtre por tipo."
            return self._failed(
                op,
                request.id,
                p.Error.from_code("bad_request", f"Resultado grande demais. {hint}", request.id),
            )
        event_log.log("memory_request", op=op, ok=True)
        return result

    @staticmethod
    def _failed(op: str, request_id: str, error: p.Error) -> p.Error:
        event_log.log("memory_request", op=op, ok=False, code=error.code)
        return error

    async def _run(self, memory: LongTermMemory, request: Any) -> dict[str, Any]:
        args = request.args
        match request.op:
            case "list":
                rows = await asyncio.to_thread(
                    memory.store.list_memories, args.type, bool(args.all)
                )
                return {"memories": [_memory_row(m) for m in rows]}
            case "search":
                hits = await memory.recall(args.query.strip(), args.limit, args.threshold)
                return {"results": [_hit_row(h) for h in hits]}
            case "forget":
                found = await asyncio.to_thread(memory.store.get, args.id.strip())
                if found is None:
                    raise MemoryNotFoundError(f"Não achei a memória {args.id}.")
                removed = await memory.forget([found])
                return {"deleted": [m.id for m in removed]}
            case "export":
                exported = await asyncio.to_thread(memory.export, bool(args.vectors))
                profile = await asyncio.to_thread(memory.export_profile)
                for row in exported:
                    row["text"] = mask_secrets(str(row["text"]))
                return {"profile": _profile_row(profile), "memories": exported}
            case "profile.get":
                if memory.profile_enabled():
                    profile = await asyncio.to_thread(memory.export_profile)
                else:
                    profile = None
                return {"profile": _profile_row(profile)}
            case "profile.set":
                fields = {
                    key: value
                    for key in ("name", "birth_date", "nationality", "gender")
                    if (value := getattr(args, key)) is not None
                }
                await memory.set_profile(args.text, fields)
                return {"profile": _profile_row(await asyncio.to_thread(memory.export_profile))}
            case "profile.forget":
                return {"deleted": await memory.forget_profile()}
        raise ValueError(f"operação desconhecida: {request.op}")
