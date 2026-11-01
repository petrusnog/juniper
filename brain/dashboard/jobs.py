"""Background tasks the owner starts from the dashboard (ADR-028): the dream and the reindex.

One at a time (a second request is a typed ``409``). The task runs on the server's own loop; the
result is the same text ``juniper memory dream`` prints, or a typed error. The last ``HISTORY``
runs stay in memory: a restart forgets the list, not the effect. A task never raises into the
server: whatever goes wrong becomes the run's ``error``.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from brain.eventlog import event_log
from brain.memory.errors import MemoryUnavailableError

HISTORY: Final = 10
MAX_RESULT_CHARS: Final = 4000
Runner = Callable[[dict[str, Any]], Awaitable[str]]


class JobBusyError(Exception):
    """Another task is running; ``str(exc)`` says which."""


class JobUnknownError(Exception):
    pass


@dataclass
class Job:
    id: str
    name: str
    params: dict[str, Any]
    state: str = "running"  # running | done | failed
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    result: str = ""
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "params": self.params,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class JobRunner:
    def __init__(
        self,
        runners: dict[str, Runner],
        *,
        busy: Callable[[str], str | None] | None = None,
    ) -> None:
        self._runners = runners
        self._busy = busy  # an outside reason not to start (the nightly dream is running)
        self._history: deque[Job] = deque(maxlen=HISTORY)
        self._active: Job | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def names(self) -> list[str]:
        return sorted(self._runners)

    @property
    def active(self) -> Job | None:
        return self._active

    def snapshot(self) -> dict[str, Any]:
        return {
            "available": self.names,
            "active": self._active.to_json() if self._active else None,
            "history": [j.to_json() for j in reversed(self._history)],
        }

    def start(self, name: str, params: dict[str, Any] | None = None) -> Job:
        """Begin ``name`` in the background. Raises ``JobUnknown`` or ``JobBusy``."""
        runner = self._runners.get(name)
        if runner is None:
            raise JobUnknownError(name)
        if self._active is not None:
            raise JobBusyError(f"Já tem uma tarefa rodando: {self._active.name}.")
        reason = self._busy(name) if self._busy else None
        if reason:
            raise JobBusyError(reason)
        job = Job(secrets.token_hex(4), name, dict(params or {}))
        self._active = job
        task = asyncio.get_running_loop().create_task(self._run(job, runner))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        event_log.log("dashboard", action="job", op="start", name=name, job=job.id)
        return job

    async def _run(self, job: Job, runner: Runner) -> None:
        try:
            job.result = (await runner(job.params))[:MAX_RESULT_CHARS]
            job.state = "done"
        except asyncio.CancelledError:
            job.state, job.error = "failed", "interrompida: o servidor está saindo"
            raise
        except MemoryUnavailableError as exc:
            job.state, job.error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001  (a task must never reach the server)
            job.state, job.error = "failed", f"erro interno ({type(exc).__name__})"
            event_log.log("error", where="dashboard.job", name=job.name, type=type(exc).__name__)
        finally:
            job.finished_at = datetime.now(UTC)
            self._history.append(job)
            self._active = None
            event_log.log("dashboard", action="job", op="end", name=job.name, state=job.state)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def default_runners() -> dict[str, Runner]:
    """The two tasks: the same routines the ``juniper`` commands call."""

    async def dream(params: dict[str, Any]) -> str:
        from brain.memory.v2.service import run_night

        report = await run_night(dry_run=bool(params.get("dry_run")))
        return report.text()

    async def reindex(params: dict[str, Any]) -> str:
        from brain.memory.reindex import reindex_all

        result, v2 = await asyncio.to_thread(reindex_all, bool(params.get("force")))
        text = (
            f"{result.reindexed} memória(s) reindexada(s), {result.skipped} já em dia "
            f"(de {result.total})."
        )
        if v2 is not None:
            text += f" Memória V2: {v2.reindexed} item(ns) reindexado(s), {v2.skipped} em dia."
        return text

    return {"dream": dream, "reindex": reindex}
