"""Ocorrências genéricas (ADR-028): o store, a tool report_issue e o caminho do dashboard."""

from __future__ import annotations

import asyncio
import http.client
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import mongomock
import pytest

from brain.auth import AuthService
from brain.auth.context import current_client, current_user
from brain.auth.credentials import load_pepper
from brain.auth.store import UserStore
from brain.core.dispatcher import Dispatcher
from brain.dashboard.actions import Actions
from brain.dashboard.routes import build_api
from brain.dashboard.server import DashboardServer
from brain.dashboard.sources import Sources
from brain.eventlog import bind_session
from brain.llm.types import ToolCall
from brain.memory.issues import IssueError, IssueStore, new_issue
from brain.memory.mongo import MongoConnection
from brain.tools import issues as issue_tools
from brain.tools.base import Tier
from brain.tools.issues import ReportIssue
from brain.tools.registry import ToolRegistry


def _connection() -> MongoConnection:
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return connection


@pytest.fixture
def store() -> IssueStore:
    return IssueStore(_connection())


# --- the store -----------------------------------------------------------------------------------


def test_a_new_issue_is_validated_and_masked() -> None:
    issue = new_issue(title="  Errou   a data ", description="token gsk_abcdefghijklmnop1234 aqui")
    assert issue["title"] == "Errou a data" and issue["state"] == "aberta"
    assert "gsk_abcdefghijklmnop1234" not in issue["description"]
    with pytest.raises(IssueError, match="título"):
        new_issue(title="   ")
    for bad in ({"type": "x"}, {"severity": "x"}, {"origin": "x"}):
        with pytest.raises(IssueError):
            new_issue(title="ok", **bad)


def test_the_store_moves_an_issue_along_and_keeps_the_history(store: IssueStore) -> None:
    issue_id = store.add(new_issue(title="Bug", owner_id="u1"))
    found = store.set_state(issue_id[:8], "investigando", "u1")  # a unique prefix is enough
    assert found["state"] == "investigando"
    store.set_severity(issue_id, "alta", "u1")
    store.add_note(issue_id, "reproduzi no celular", "u1")
    history = [h["change"] for h in store.get(issue_id)["history"]]  # type: ignore[index]
    assert history == [
        "criada (dashboard)",
        "estado: investigando",
        "gravidade: alta",
        "nota: reproduzi no celular",
    ]
    with pytest.raises(IssueError):
        store.set_state(issue_id, "lixo", "u1")
    with pytest.raises(IssueError, match="Não achei"):
        store.set_state("ffffffff", "resolvida", "u1")
    assert store.recent(state="investigando")[0]["_id"] == issue_id
    assert store.delete(issue_id) and store.get(issue_id) is None


# --- the tool ------------------------------------------------------------------------------------


@pytest.fixture
def bound(store: IssueStore) -> Iterator[IssueStore]:
    issue_tools.bind(store)
    bind_session("sess-1")
    current_user.set("user-1")
    current_client.set("telegram")
    yield store
    issue_tools.bind(None)


def test_the_tool_is_a_write_that_asks_and_is_hidden_without_a_store() -> None:
    assert ReportIssue.tier is Tier.WRITE and ReportIssue.confirm is True
    assert not ReportIssue.available()


async def test_the_tool_stamps_the_session_the_channel_and_the_user(bound: IssueStore) -> None:
    result = await ReportIssue().run(title="Errou a data", description="disse terça", type="bug")
    assert result.success and "Registrei" in (result.output or "")
    saved = bound.recent()[0]
    assert saved["origin"] == "fala" and saved["session"] == "sess-1"
    assert saved["channel"] == "telegram" and saved["owner_id"] == "user-1"


async def test_the_model_cannot_choose_the_user_or_the_state(bound: IssueStore) -> None:
    await ReportIssue().run(title="x", owner_id="outro", state="resolvida", origin="dashboard")  # type: ignore[call-arg]
    saved = bound.recent()[0]
    assert (
        saved["owner_id"] == "user-1" and saved["state"] == "aberta" and saved["origin"] == "fala"
    )


async def test_the_tool_validates_before_asking_and_describes_what_it_will_store(
    bound: IssueStore,
) -> None:
    tool = ReportIssue()
    refused = await tool.precheck(title="")
    assert refused is not None and not refused.success
    assert (await tool.precheck(title="ok", type="nada")) is not None
    assert await tool.precheck(title="ok") is None
    summary = await tool.describe(title="Errou a data", severity="alta")
    assert summary == "registrar a ocorrência «Errou a data» (tipo bug, gravidade alta)"


async def test_the_dispatcher_asks_the_user_before_registering(bound: IssueStore) -> None:
    registry = ToolRegistry()
    registry.register(ReportIssue)
    asked: list[str | None] = []

    async def deny(name: str, args: dict[str, Any], tier: Tier, summary: str | None = None) -> bool:
        asked.append(summary)
        return False

    dispatcher = Dispatcher(deny, tool_registry=registry)
    message = await dispatcher.dispatch(
        ToolCall(id="1", name="report_issue", arguments={"title": "Falha"})
    )
    assert asked and "Falha" in (asked[0] or "")
    assert bound.recent() == []  # denied: nothing was written
    assert "negou" in message.content


# --- the dashboard -------------------------------------------------------------------------------


class Env:
    def __init__(self, tmp_path: Path, sources: Sources | None = None) -> None:
        connection = _connection()
        self.store = IssueStore(connection)
        self.auth = AuthService(UserStore(connection), load_pepper(tmp_path / "pepper"))
        self.owner = self.auth.create_user("Petrus", "owner")
        sources = sources or Sources(issues=self.store)
        self.server = DashboardServer(
            "127.0.0.1", 0, "t", build_api(sources), auth=self.auth, actions=Actions(sources)
        )

    async def call(
        self, path: str, *, method: str = "GET", headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        def run() -> tuple[int, Any]:
            conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=3)
            try:
                conn.request(method, path, headers=headers or {})
                response = conn.getresponse()
                return response.status, json.loads(response.read() or b"null")
            finally:
                conn.close()

        return await asyncio.to_thread(run)

    async def session(self, bearer: str) -> tuple[dict[str, str], str]:
        def login() -> dict[str, str]:
            conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=3)
            conn.request("GET", "/api/login", headers={"Authorization": f"Bearer {bearer}"})
            response = conn.getresponse()
            response.read()
            conn.close()
            return {"Cookie": response.getheader("Set-Cookie", "").split(";")[0]}

        cookie = await asyncio.to_thread(login)
        _, me = await self.call("/api/me", headers=cookie)
        return cookie, me["csrf"]

    async def post(self, path: str, cookie: dict[str, str], csrf: str) -> tuple[int, Any]:
        return await self.call(path, method="POST", headers={**cookie, "X-CSRF-Token": csrf})


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    e = Env(tmp_path)
    assert await e.server.start()
    yield e
    await e.server.stop()


async def test_the_owner_registers_moves_and_deletes_an_issue(env: Env) -> None:
    cookie, csrf = await env.session(env.owner.bearer)
    status, created = await env.post(
        "/api/issues?title=Tela+trava&description=ao+abrir&type=bug&severity=alta", cookie, csrf
    )
    assert status == 200
    issue_id = created["id"]

    _, listing = await env.call("/api/occurrences?kind=registro", headers=cookie)
    row = listing["items"][0]
    assert row["kind"] == "registro" and row["title"] == "Tela trava" and row["state"] == "aberta"
    assert row["severity"] == "alta" and row["origin"] == "dashboard"

    assert (await env.post(f"/api/issues/{issue_id}/state?value=resolvida", cookie, csrf))[0] == 200
    assert (await env.post(f"/api/issues/{issue_id}/note?text=corrigido", cookie, csrf))[0] == 200
    _, detail = await env.call(f"/api/occurrences/{issue_id}", headers=cookie)
    assert detail["state"] == "resolvida"
    assert [m["text"] for m in detail["messages_list"]][-1] == "nota: corrigido"

    status, body = await env.post(f"/api/issues/{issue_id}/delete", cookie, csrf)
    assert status == 400 and "Confirme" in body["error"]  # destructive: the server asks
    assert (await env.post(f"/api/issues/{issue_id}/delete?confirm={issue_id}", cookie, csrf))[
        0
    ] == 200
    assert env.store.recent() == []


async def test_validation_errors_are_typed_answers(env: Env) -> None:
    cookie, csrf = await env.session(env.owner.bearer)
    status, body = await env.post("/api/issues?title=&type=bug", cookie, csrf)
    assert status == 400 and "título" in body["error"]
    status, body = await env.post("/api/issues/ffffffff/state?value=aberta", cookie, csrf)
    assert status == 404


async def test_a_tester_cannot_act_and_a_missing_csrf_is_refused(env: Env) -> None:
    env.auth.allow_testers = True
    tester = env.auth.create_user("Ana", "tester")
    cookie, csrf = await env.session(tester.bearer)
    assert (await env.post("/api/issues?title=x", cookie, csrf))[0] == 403
    owner_cookie, _ = await env.session(env.owner.bearer)
    status, _ = await env.call("/api/issues?title=x", method="POST", headers=owner_cookie)
    assert status == 403
    assert env.store.recent() == []


async def test_a_spoken_issue_carries_the_user_the_client_and_the_conversation(
    tmp_path: Path,
) -> None:
    """End to end: a credential logs in, the model calls the tool, the user says «sim»."""
    from websockets.asyncio.client import connect

    from brain.clients import protocol as p
    from tests.test_agent import FakeProvider, say, several
    from tests.test_chat_server import Client, Scenario, hello

    store = IssueStore(_connection())
    issue_tools.bind(store)
    try:
        registry = ToolRegistry()
        registry.register(ReportIssue)
        provider = FakeProvider(
            [
                several(("report_issue", {"title": "Errou a data", "type": "bug"})),
                say("Posso registrar «Errou a data»?"),
                say("Registrado."),
            ]
        )
        async with Scenario(provider, tmp_path, tool_registry=registry) as s:
            auth = AuthService(UserStore(_connection()), load_pepper(tmp_path / "pepper"))
            owner = auth.create_user("Petrus", "owner")
            s.server._auth = auth
            ws = await connect(
                f"ws://127.0.0.1:{s.server.port}/v1/ws",
                additional_headers={"Authorization": f"Bearer {owner.bearer}"},
            )
            await ws.send(hello("dev-1", "cli"))
            welcome = p.parse_server_message(await ws.recv())
            assert isinstance(welcome, p.Welcome) and welcome.user is not None
            client = Client(ws, welcome)
            await client.chat("anota isso como bug")
            assert store.recent() == []  # asked first: nothing written yet
            await client.chat("Sim.", id="c-2")
            await ws.close()
        (saved,) = store.recent()
        assert saved["title"] == "Errou a data" and saved["origin"] == "fala"
        assert saved["owner_id"] == owner.user.user_id and saved["channel"] == "cli"
        assert saved["session"]
    finally:
        issue_tools.bind(None)
