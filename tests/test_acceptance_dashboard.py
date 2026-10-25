"""Aceite do dashboard (ADR-020): a noite inteira da Juniper vista pelo navegador, de ponta a ponta.

Roda o sonho do marco (refinamento, estudo, afetos, personalidade), sobe o servidor HTTP do
dashboard sobre esses dados e percorre login, cada rota da API e a pesquisa. Também garante que
nada do que o dashboard faz escreve no banco.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from brain.core.session import Turn
from brain.dashboard.routes import build_api
from brain.dashboard.server import DashboardServer
from brain.dashboard.sources import Sources
from brain.memory.v2.live import LiveReactor
from tests.test_acceptance_memory_v2 import FACT, World, morning  # noqa: F401
from tests.test_dashboard_server import BEARER, TOKEN, get
from tests.test_v2_live_affect import PROVOKE, Judge

COLLECTIONS = (
    "beliefs",
    "interests",
    "study_notes",
    "affects",
    "concerns",
    "journal",
    "dream_runs",
    "dream_watermarks",
    "transcript_turns",
    "persona_state",
    "persona_history",
)


def snapshot(world: World) -> dict[str, Any]:
    state = {name: sorted(map(str, world.data.docs.find(name))) for name in COLLECTIONS}
    state["memories"] = sorted(
        (m.id, m.status, m.status_reflexao, m.text) for m in world.store.list_memories(None, True)
    )
    return state


async def fetch(server: DashboardServer, path: str) -> Any:
    status, headers, body = await get(server, path, headers=BEARER)
    assert status == 200, (path, status, body[:200])
    assert headers["content-type"].startswith("application/json"), path
    return json.loads(body)


@pytest.fixture
async def dashboard(morning: World):  # type: ignore[no-untyped-def]  # noqa: F811
    server = DashboardServer(
        "127.0.0.1",
        0,
        TOKEN,
        build_api(Sources(memory=morning.memory, data=morning.data, transcripts=True)),
    )
    assert await server.start()
    yield server
    await server.stop()


async def test_after_a_night_every_page_has_its_data(
    dashboard: DashboardServer,
    morning: World,  # noqa: F811
) -> None:
    overview = await fetch(dashboard, "/api/overview")
    assert overview["v1"]["refinada"] == 1 and overview["v2"]["interests"] == 1
    assert overview["lastDream"]["status"] == "done" and overview["persona"]["version"] == 2
    # the dashboard reads with the real clock: the night's feeling has long faded, but it is kept
    assert overview["v2"]["affects"] == 1 and overview["affect"]["active"] is True

    memories = await fetch(dashboard, "/api/memories?status=all")
    refined = next(m for m in memories["items"] if m["status_reflexao"] == "refinada")
    raw = next(m for m in memories["items"] if m["status"] == "superseded")
    detail = await fetch(dashboard, f"/api/memories/{refined['id']}")
    assert detail["text"] == FACT and detail["from"]["id"] == raw["id"]
    assert detail["lembranca"]["como_eu_era"]["versao"] == 2

    interests = await fetch(dashboard, "/api/interests")
    topic = interests["items"][0]
    topic_detail = await fetch(dashboard, f"/api/interests/{topic['id']}")
    texts = {n["text"]: n for n in topic_detail["notes_list"]}
    assert texts["os Beatles se formaram em Liverpool"]["uncertainty"] == "baixa"
    assert texts["a banda gravou em Abbey Road"]["uncertainty"] == "média"
    assert topic_detail["level"] == 1 and "como foi o fim da banda?" in texts

    beliefs = await fetch(dashboard, "/api/beliefs")
    assert beliefs["items"][0]["statement"].startswith("Acho que o Petrus se cobra demais")
    dreams = await fetch(dashboard, "/api/dreams")
    assert [p["name"] for p in dreams["items"][0]["phases"]][-1] == "refine"
    assert (await fetch(dashboard, "/api/journal?days=3650"))["total"] >= 1
    assert (await fetch(dashboard, "/api/persona"))["state"]["version"] == 2
    assert (await fetch(dashboard, "/api/core"))["core"].startswith("Você é a Juniper")


async def test_the_affect_trace_reaches_the_line_the_night_read(
    dashboard: DashboardServer,
    morning: World,  # noqa: F811
) -> None:
    (sadness,) = (await fetch(dashboard, "/api/affects"))["items"]
    detail = await fetch(dashboard, f"/api/affects/{sadness['id']}")
    turn = next(t for t in detail["trace"] if t["kind"] == "turn")
    assert "avó faleceu" in turn["user"]
    assert any(t["kind"] == "concern" and t["name"] for t in detail["trace"])


async def test_a_live_reaction_shows_up_with_its_origin(
    dashboard: DashboardServer,
    morning: World,  # noqa: F811
) -> None:
    live = LiveReactor(morning.data, Judge(PROVOKE), config=morning.config)
    await live.react("20261023-100000-cccccc", Turn("você falhou de novo", index=0))
    items = (await fetch(dashboard, "/api/affects"))["items"]
    assert {a["origin"] for a in items} == {"sonho", "conversa"}
    assert max(a["intensity"] for a in items if a["origin"] == "conversa") <= 0.3


async def test_the_simulator_and_the_search_work_on_the_night_data(
    dashboard: DashboardServer,
    morning: World,  # noqa: F811
) -> None:
    prompt = await fetch(dashboard, "/api/prompt?message=bom+dia")
    assert "personalidade" in [s["name"] for s in prompt["sections"]]
    found = await fetch(dashboard, "/api/search?q=Beatles")
    kinds = {g["type"] for g in found["groups"] if g["items"]}
    assert {"temas", "notas", "conversas"} <= kinds
    assert found["semantic"] is True


async def test_conversations_show_what_he_wrote_without_a_reply_after_tools(
    dashboard: DashboardServer,
    morning: World,  # noqa: F811
) -> None:
    sessions = await fetch(dashboard, "/api/sessions")
    assert sessions["total"] == 2
    turns = await fetch(dashboard, f"/api/sessions/{sessions['items'][0]['session_id']}")
    assert turns["items"] and all("user" in t for t in turns["items"])


async def test_nothing_the_dashboard_does_writes_to_the_database(
    dashboard: DashboardServer,
    morning: World,  # noqa: F811
) -> None:
    before = snapshot(morning)
    topic = (await fetch(dashboard, "/api/interests"))["items"][0]["id"]
    belief = (await fetch(dashboard, "/api/beliefs"))["items"][0]["id"]
    memory = (await fetch(dashboard, "/api/memories?status=all"))["items"][0]["id"]
    affect = (await fetch(dashboard, "/api/affects"))["items"][0]["id"]
    session = (await fetch(dashboard, "/api/sessions"))["items"][0]["session_id"]
    paths = [
        "/api/overview", "/api/memories?status=all", f"/api/memories/{memory}", "/api/beliefs",
        f"/api/beliefs/{belief}", "/api/affects", f"/api/affects/{affect}", "/api/mood",
        "/api/persona", "/api/concerns", "/api/core", "/api/interests", f"/api/interests/{topic}",
        "/api/notes", "/api/dreams", "/api/journal", "/api/sessions", f"/api/sessions/{session}",
        "/api/prompt?message=oi+tudo+bem", "/api/search?q=Beatles", "/api/search?q=cobrei",
    ]  # fmt: skip
    for path in paths:
        await fetch(dashboard, path)
    assert snapshot(morning) == before


async def test_the_whole_thing_needs_the_token(dashboard: DashboardServer) -> None:
    for path in ("/api/overview", "/api/memories", "/api/search?q=x", "/api/prompt?message=x"):
        assert (await get(dashboard, path))[0] == 401
    assert (await get(dashboard, "/"))[0] == 302
