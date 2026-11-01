"""The dashboard's actions on issues, memory and background jobs (ADR-028).

Every route is a ``POST`` of the owner (the server checks the session, the anti-CSRF token and the
origin before calling in here). The parameters ride in the query; none is a secret. The actions
call the same services as the ``juniper`` commands: nothing is reimplemented for the browser.

Two classes of action: *registering* (create or move an issue, set a concern's weight, revert the
persona to a new version) runs at once; *destructive* ones (forget, delete) need ``confirm=<id>``,
which is checked here, not only on the screen. Every action is audited without any text of the
memory or of the issue.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from brain.auth import Principal
from brain.dashboard.api import ApiError
from brain.dashboard.jobs import JobBusyError, JobUnknownError
from brain.dashboard.sources import Sources
from brain.eventlog import event_log
from brain.memory.errors import MemoryUnavailableError
from brain.memory.issues import IssueError, new_issue
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.concerns import ConcernBook, ConcernError
from brain.memory.v2.interests import InterestBook
from brain.memory.v2.knowledge import NoteBook
from brain.memory.v2.persona import PersonaBook, PersonaError

Query = Mapping[str, list[str]]
Result = tuple[int, dict[str, Any], dict[str, Any] | None]
PREFIXES = ("/api/issues", "/api/memory/", "/api/jobs/")


def _one(query: Query, name: str, default: str = "") -> str:
    values = query.get(name)
    return values[0].strip() if values else default


def _ok(**fields: Any) -> Result:
    return 200, {"ok": True, **fields}, None


class Actions:
    serves_get = False

    def __init__(self, sources: Sources, jobs: Any | None = None) -> None:
        self._sources = sources
        self._jobs = jobs
        self._memory_routes: dict[str, Callable[[str, Query, Principal], Result]] = {}

    @staticmethod
    def handles(path: str) -> bool:
        return path == "/api/issues" or path.startswith(PREFIXES)

    async def handle(self, method: str, path: str, query: Query, who: Principal) -> Result:
        if not who.is_owner:
            return 403, {"error": "só o dono executa ações"}, None
        if method != "POST":
            return 405, {"error": "método não permitido"}, None
        parts = path.removeprefix("/api/").split("/")
        try:
            return await self._dispatch(parts, query, who)
        except ApiError as exc:
            return exc.status, {"error": exc.message}, None
        except (IssueError, ConcernError, PersonaError) as exc:
            return 400, {"error": str(exc)}, None
        except MemoryUnavailableError as exc:
            return 503, {"error": str(exc)}, None

    async def _dispatch(self, parts: list[str], query: Query, who: Principal) -> Result:
        if parts[0] == "issues":
            return await asyncio.to_thread(self._issue, parts[1:], query, who)
        if parts[0] == "memory":
            return await self._memory(parts[1:], query, who)
        if parts[0] == "jobs":
            return await self._job(parts[1:], query, who)
        return 404, {"error": "não existe"}, None

    # --- shared ----------------------------------------------------------------------------------

    @staticmethod
    def audit(who: Principal, op: str, target: str = "") -> None:
        event_log.log("dashboard", action="admin", op=op, actor=who.user_id, target=target)

    @staticmethod
    def confirm(query: Query, expected: str) -> None:
        if _one(query, "confirm") != expected:
            raise ApiError(400, f"Confirme digitando «{expected}».")

    # --- issues ----------------------------------------------------------------------------------

    def _issue(self, parts: list[str], query: Query, who: Principal) -> Result:
        store = self._sources.issues
        if store is None:
            raise ApiError(503, "as ocorrências precisam do MongoDB (memory.session.store: mongo)")
        if not parts:
            issue = new_issue(
                title=_one(query, "title"),
                description=_one(query, "description"),
                type=_one(query, "type", "bug"),
                severity=_one(query, "severity", "media"),
                origin="dashboard",
                owner_id=who.user_id,
            )
            issue_id = store.add(issue)
            self.audit(who, "issue_created", issue_id[:16])
            return _ok(id=issue_id[:16])
        found = store.get(parts[0].lower())
        if found is None or len(parts) != 2:
            raise ApiError(404, "ocorrência não encontrada")
        issue_id, action = str(found["_id"]), parts[1]
        if action == "state":
            store.set_state(issue_id, _one(query, "value"), who.user_id)
        elif action == "severity":
            store.set_severity(issue_id, _one(query, "value"), who.user_id)
        elif action == "note":
            store.add_note(issue_id, _one(query, "text"), who.user_id)
        elif action == "delete":
            self.confirm(query, issue_id[:16])
            store.delete(issue_id)
        else:
            raise ApiError(404, "não existe")
        self.audit(who, f"issue_{action}", issue_id[:16])
        return _ok(id=issue_id[:16])

    # --- memory (ADR-028): the same books the `juniper memory` commands use --------------------

    async def _memory(self, parts: list[str], query: Query, who: Principal) -> Result:
        if parts[:1] == ["facts"] and len(parts) == 3 and parts[2] == "forget":
            return await self._forget_fact(parts[1], query, who)
        return await asyncio.to_thread(self._v2, parts, query, who)

    async def _forget_fact(self, memory_id: str, query: Query, who: Principal) -> Result:
        memory = self._sources.memory
        if memory is None or getattr(memory, "store", None) is None:
            raise ApiError(503, "a memória de longo prazo está desligada")
        targets = await memory.find_to_forget(memory_id, None)
        if not targets:
            raise ApiError(404, "memória não encontrada")
        self.confirm(query, targets[0].id)
        removed = await memory.forget(targets)  # also the older versions it replaced
        self.audit(who, "memory_forget", targets[0].id)
        return _ok(removed=len(removed))

    def _v2(self, parts: list[str], query: Query, who: Principal) -> Result:
        data = self._sources.data
        if data is None:
            raise ApiError(503, "a Memória V2 está desligada (memory.v2.enabled)")
        kind = parts[0] if parts else ""
        if kind == "affects" and parts[1:] == ["clear"]:
            self.confirm(query, "sentimentos")
            removed = AffectBook(data).forget_all()
            self.audit(who, "affects_clear", "all")
            return _ok(removed=removed)
        if kind == "persona" and parts[1:] == ["revert"]:
            raw = _one(query, "to")
            if raw and not raw.isdigit():
                raise ApiError(400, "to deve ser o número de uma versão")
            state = PersonaBook(data).revert(int(raw) if raw else None)
            self.audit(who, "persona_revert", str(state.version))
            return _ok(version=state.version)
        if len(parts) != 3:
            raise ApiError(404, "não existe")
        item_id, action = parts[1], parts[2]
        if kind == "concerns" and action == "weight":
            book = ConcernBook(data)
            book.ensure_defaults()
            concern = book.set_weight(item_id, _one(query, "value"), _one(query, "reason"))
            self.audit(who, "concern_weight", item_id)
            return _ok(id=concern.id, weight=concern.weight)
        if action != "forget":
            raise ApiError(404, "não existe")
        forgetters: dict[str, Callable[[str], Any]] = {
            "beliefs": BeliefBook(data).forget,
            "interests": InterestBook(data).forget,
            "notes": NoteBook(data).forget_one,
        }
        forget = forgetters.get(kind)
        if forget is None:
            raise ApiError(404, "não existe")
        self.confirm(query, item_id)
        if not forget(item_id):
            raise ApiError(404, "não encontrado")
        self.audit(who, f"{kind}_forget", item_id)
        return _ok(id=item_id)

    # --- jobs (ADR-028) ------------------------------------------------------------------------

    async def _job(self, parts: list[str], query: Query, who: Principal) -> Result:
        if self._jobs is None:
            raise ApiError(503, "as tarefas não estão disponíveis neste servidor")
        if len(parts) != 1:
            raise ApiError(404, "não existe")
        params = {
            "dry_run": _one(query, "dry") == "1",
            "force": _one(query, "force") == "1",
        }
        try:
            job = self._jobs.start(parts[0], params)
        except JobUnknownError:
            raise ApiError(404, "tarefa desconhecida") from None
        except JobBusyError as exc:
            raise ApiError(409, str(exc)) from None
        self.audit(who, f"job_{parts[0]}", job.id)
        return _ok(job=job.id, name=job.name)
