"""Tarefas em segundo plano no dashboard (ADR-028): sonho e reindex, uma de cada vez."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from brain.dashboard.actions import Actions
from brain.dashboard.jobs import JobBusyError, JobRunner, JobUnknownError, default_runners
from brain.dashboard.routes import build_api
from brain.dashboard.sources import Sources
from brain.memory.errors import MemoryUnavailableError
from brain.memory.reindex import ReindexResult
from tests.test_issues import Env


class Gate:
    """A task that waits until the test lets it finish."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.params: dict[str, Any] = {}

    async def __call__(self, params: dict[str, Any]) -> str:
        self.params = params
        self.started.set()
        await self.release.wait()
        return "feito"


async def finish(runner: JobRunner) -> None:
    for _ in range(100):
        if runner.active is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("a tarefa não terminou")


async def test_one_task_at_a_time_with_its_result_in_the_history() -> None:
    gate = Gate()
    runner = JobRunner({"dream": gate})
    job = runner.start("dream", {"dry_run": True})
    await gate.started.wait()
    assert gate.params == {"dry_run": True} and runner.active is job
    with pytest.raises(JobBusyError, match="dream"):
        runner.start("dream")
    gate.release.set()
    await finish(runner)
    (done,) = runner.snapshot()["history"]
    assert done["state"] == "done" and done["result"] == "feito" and done["finished_at"]
    runner.start("dream")  # free again
    await finish(runner)


async def test_an_unknown_task_and_an_outside_reason_to_wait() -> None:
    runner = JobRunner({"dream": Gate()}, busy=lambda name: "a noite está rodando")
    with pytest.raises(JobUnknownError):
        runner.start("nada")
    with pytest.raises(JobBusyError, match="noite"):
        runner.start("dream")
    assert runner.active is None


async def test_failures_become_the_runs_error_not_an_exception() -> None:
    async def down(params: dict[str, Any]) -> str:
        raise MemoryUnavailableError("banco fora do ar")

    async def boom(params: dict[str, Any]) -> str:
        raise RuntimeError("segredo-interno gsk_abcdefghijklmnop1234")

    runner = JobRunner({"down": down, "boom": boom})
    runner.start("down")
    await finish(runner)
    runner.start("boom")
    await finish(runner)
    first, second = reversed(runner.snapshot()["history"])
    assert first["state"] == "failed" and first["error"] == "banco fora do ar"
    assert second["state"] == "failed" and "RuntimeError" in second["error"]
    assert "segredo-interno" not in second["error"]  # only the type leaves


async def test_shutdown_cuts_a_running_task() -> None:
    gate = Gate()
    runner = JobRunner({"dream": gate})
    runner.start("dream")
    await gate.started.wait()
    await runner.shutdown()
    assert runner.active is None and runner.snapshot()["history"][0]["state"] == "failed"


async def test_the_default_runners_call_the_same_routines_as_the_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brain.memory import reindex
    from brain.memory.v2 import service

    seen: dict[str, Any] = {}

    class Report:
        def text(self) -> str:
            return "Sonhei."

    async def fake_night(dry_run: bool = False, **_: Any) -> Report:
        seen["dry"] = dry_run
        return Report()

    def fake_reindex(force: bool = False, announce: Any = None) -> Any:
        seen["force"] = force
        return ReindexResult(4, 3, 1), ReindexResult(2, 2, 0)

    monkeypatch.setattr(service, "run_night", fake_night)
    monkeypatch.setattr(reindex, "reindex_all", fake_reindex)
    runners = default_runners()
    assert await runners["dream"]({"dry_run": True}) == "Sonhei." and seen["dry"] is True
    text = await runners["reindex"]({"force": True})
    assert (
        seen["force"] is True and "3 memória(s) reindexada(s)" in text and "Memória V2: 2" in text
    )


# --- through the dashboard ---------------------------------------------------------------------


class JobEnv(Env):
    def __init__(self, tmp_path: Path, runner: JobRunner | None) -> None:
        sources = Sources(jobs=runner)
        super().__init__(tmp_path, sources)
        self.server._actions = Actions(sources, runner)  # type: ignore[union-attr]
        self.server.api = build_api(sources)


@pytest.fixture
async def gate() -> Gate:
    return Gate()


@pytest.fixture
async def env(tmp_path: Path, gate: Gate) -> AsyncIterator[JobEnv]:
    e = JobEnv(tmp_path, JobRunner({"dream": gate, "reindex": Gate()}))
    assert await e.server.start()
    yield e
    gate.release.set()
    await e.server.stop()


async def test_the_owner_starts_a_task_and_a_second_one_gets_409(env: JobEnv, gate: Gate) -> None:
    cookie, csrf = await env.session(env.owner.bearer)
    status, body = await env.post("/api/jobs/dream?dry=1", cookie, csrf)
    assert status == 200 and body["name"] == "dream"
    await gate.started.wait()
    assert gate.params["dry_run"] is True

    status, body = await env.post("/api/jobs/reindex", cookie, csrf)
    assert status == 409 and "dream" in body["error"]
    _, snapshot = await env.call("/api/jobs", headers=cookie)
    assert snapshot["active"]["name"] == "dream" and snapshot["available"] == ["dream", "reindex"]

    gate.release.set()
    for _ in range(100):
        _, snapshot = await env.call("/api/jobs", headers=cookie)
        if snapshot["active"] is None:
            break
        await asyncio.sleep(0.02)
    assert snapshot["history"][0]["state"] == "done"


async def test_unknown_tasks_and_a_tester_are_refused(env: JobEnv) -> None:
    cookie, csrf = await env.session(env.owner.bearer)
    assert (await env.post("/api/jobs/nada", cookie, csrf))[0] == 404
    env.auth.allow_testers = True
    tester = env.auth.create_user("Ana", "tester")
    tcookie, tcsrf = await env.session(tester.bearer)
    assert (await env.post("/api/jobs/dream", tcookie, tcsrf))[0] == 403


async def test_without_a_runner_the_routes_are_a_typed_503(tmp_path: Path) -> None:
    env = JobEnv(tmp_path, None)
    assert await env.server.start()
    try:
        cookie, csrf = await env.session(env.owner.bearer)
        assert (await env.post("/api/jobs/dream", cookie, csrf))[0] == 503
        assert (await env.call("/api/jobs", headers=cookie))[0] == 503
    finally:
        await env.server.stop()
