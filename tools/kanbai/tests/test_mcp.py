"""The MCP server over real HTTP (uvicorn on loopback) with a real MCP client."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from kanbai.board import Board
from kanbai.domain import Actor, Project, Role
from kanbai.mcp_server import build_app
from kanbai.modelmap import ModelMap
from kanbai.store import MemoryStore


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def served():  # type: ignore[no-untyped-def]
    board = Board(MemoryStore(), ModelMap.load())
    board.create_project(Project("juniper", "Juniper", "/repo"))
    tokens = {}
    for name, role in (("Aiko", Role.DELEGATOR), ("Mika", Role.EXECUTOR), ("Rei", Role.QA)):
        agent = board.register_agent(name, role).data
        tokens[role] = board.issue_token(agent.id).data  # type: ignore[attr-defined]
    port = _port()
    server = uvicorn.Server(uvicorn.Config(build_app(board), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield board, tokens, f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


async def call(url: str, token: str, tool: str, **args):  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with client, streamable_http_client(url, http_client=client) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            return res.content[0].text  # type: ignore[union-attr]


def test_no_token_or_wrong_token_is_401(served) -> None:  # type: ignore[no-untyped-def]
    _, _, url = served
    for headers in ({}, {"Authorization": "Bearer nope"}):
        r = httpx.post(url, headers=headers, json={})
        assert r.status_code == 401


def test_tools_are_listed_and_full_flow_over_http(served) -> None:  # type: ignore[no-untyped-def]
    board, tok, url = served

    async def flow() -> None:
        created = json.loads(await call(url, tok[Role.DELEGATOR], "create_task", project_id="juniper",
                                        key="T256", title="T", acceptance_criteria="criterios"))
        again = json.loads(await call(url, tok[Role.DELEGATOR], "create_task", project_id="juniper",
                                      key="T256", title="T"))
        assert created["id"] == again["id"]  # idempotent over the wire too
        kai = json.loads(await call(url, tok[Role.DELEGATOR], "create_kai", task_id=created["id"],
                                    title="Faz X", description="d", points=3))
        assert kai["column"] == "a_fazer"
        # role comes from the token: the executor cannot create KAIs, typed error not exception
        denied = await call(url, tok[Role.EXECUTOR], "create_kai", task_id=created["id"], title="x", description="d", points=1)
        assert denied.startswith("Erro:")
        started = json.loads(await call(url, tok[Role.EXECUTOR], "start_kai", kai_id=kai["id"]))
        assert started["column"] == "fazendo" and "sonnet" in started["model"]
        await call(url, tok[Role.EXECUTOR], "submit", kai_id=kai["id"], report="meu raciocínio secreto")
        packet = await call(url, tok[Role.QA], "review_packet", kai_id=kai["id"])
        assert "criterios" in packet and "secreto" not in packet
        assert (await call(url, tok[Role.EXECUTOR], "review_packet", kai_id=kai["id"])).startswith("Erro:")
        done = json.loads(await call(url, tok[Role.QA], "review", kai_id=kai["id"], approved=True, verdict="ok"))
        assert done["column"] == "revisao_humana"
        who = json.loads(await call(url, tok[Role.QA], "whoami"))
        assert who["role"] == "qa" and "token_hash" not in who

    asyncio.run(flow())
