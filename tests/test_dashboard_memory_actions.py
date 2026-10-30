"""Ações na memória pelo dashboard (ADR-028): as mesmas camadas dos comandos `juniper memory`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from brain.config import MemoryV2Settings
from brain.dashboard.sources import Sources
from brain.eventlog import event_log
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.concerns import ConcernBook
from brain.memory.v2.models import Evidence, Interest
from brain.memory.v2.persona import PersonaBook
from tests.test_dashboard_api import NOW, World, add_study
from tests.test_extraction import FakeEmbedder
from tests.test_issues import Env


class MemEnv(Env):
    def __init__(self, tmp_path: Path, world: World) -> None:
        self.world = world
        super().__init__(tmp_path, Sources(memory=world.memory, data=world.data))


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[MemEnv]:
    e = MemEnv(tmp_path, World())
    assert await e.server.start()
    yield e
    await e.server.stop()


async def login(env: MemEnv) -> tuple[dict[str, str], str]:
    return await env.session(env.owner.bearer)


async def test_forgetting_a_fact_needs_its_id_and_takes_the_old_versions_too(env: MemEnv) -> None:
    old = env.world.memory_item("Petrus mora em Recife.")
    new = env.world.memory_item("Petrus mora em Olinda.")
    env.world.store.update(old.id, status="superseded", superseded_by=new.id)
    cookie, csrf = await login(env)

    status, body = await env.post(f"/api/memory/facts/{new.id}/forget", cookie, csrf)
    assert status == 400 and "Confirme" in body["error"]
    assert env.world.store.get(new.id) is not None  # nothing happened yet

    status, body = await env.post(
        f"/api/memory/facts/{new.id}/forget?confirm={new.id}", cookie, csrf
    )
    assert status == 200 and body["removed"] == 2
    assert env.world.store.get(new.id) is None and env.world.store.get(old.id) is None
    assert (await env.post("/api/memory/facts/mem_nada/forget?confirm=x", cookie, csrf))[0] == 404


async def test_beliefs_interests_and_notes_are_forgotten_with_confirmation(env: MemEnv) -> None:
    data = env.world.data
    belief, _ = BeliefBook(data, FakeEmbedder(), MemoryV2Settings().beliefs, lambda: NOW).create(
        "Petrus gosta de jazz", "gostos", Evidence("observation", "s", 1)
    )
    interest = Interest("jazz", origin=Evidence("observation", "s", 1))
    data.interests.save(interest)
    cookie, csrf = await login(env)

    for kind, item_id in (("beliefs", belief.id), ("interests", interest.id)):
        assert (
            await env.post(f"/api/memory/{kind}/{item_id}/forget?confirm=errado", cookie, csrf)
        )[0] == 400
        status, _ = await env.post(
            f"/api/memory/{kind}/{item_id}/forget?confirm={item_id}", cookie, csrf
        )
        assert status == 200
    assert data.beliefs.get(belief.id) is None and data.interests.get(interest.id) is None
    assert (
        await env.post(f"/api/memory/beliefs/{belief.id}/forget?confirm={belief.id}", cookie, csrf)
    )[0] == 404


async def test_a_single_study_note_is_forgotten(env: MemEnv) -> None:
    interest = add_study(env.world)
    notes = env.world.data.notes.list({"interest_id": interest.id})
    target = notes[0]
    cookie, csrf = await login(env)
    path = f"/api/memory/notes/{target.id}/forget"
    assert (await env.post(path, cookie, csrf))[0] == 400
    assert (await env.post(f"{path}?confirm={target.id}", cookie, csrf))[0] == 200
    remaining = env.world.data.notes.list({"interest_id": interest.id})
    assert target.id not in {n.id for n in remaining} and len(remaining) == len(notes) - 1


async def test_a_concern_weight_is_set_validated_and_recorded(env: MemEnv) -> None:
    book = ConcernBook(env.world.data, MemoryV2Settings().concerns)
    first = book.ensure_defaults()[0]
    cookie, csrf = await login(env)
    status, body = await env.post(
        f"/api/memory/concerns/{first.id}/weight?value=0.25&reason=ajuste", cookie, csrf
    )
    assert status == 200 and body["weight"] == 0.25
    assert book.get(first.id).weight == 0.25  # type: ignore[union-attr]
    assert book.get(first.id).history[-1].reason == "ajuste"  # type: ignore[union-attr]
    for bad in ("2", "x", ""):
        status, body = await env.post(
            f"/api/memory/concerns/{first.id}/weight?value={bad}", cookie, csrf
        )
        assert status == 400
    assert (await env.post("/api/memory/concerns/nada/weight?value=0.5", cookie, csrf))[0] == 400


async def test_reverting_the_persona_makes_a_new_version(env: MemEnv) -> None:
    book = PersonaBook(env.world.data, MemoryV2Settings().persona)
    book.ensure()
    cookie, csrf = await login(env)
    status, body = await env.post("/api/memory/persona/revert?to=1", cookie, csrf)
    assert status == 400  # already on version 1: a typed answer, not a crash
    assert (await env.post("/api/memory/persona/revert?to=abc", cookie, csrf))[0] == 400


async def test_clearing_the_feelings_needs_the_word_sentimentos(env: MemEnv) -> None:
    ConcernBook(env.world.data, MemoryV2Settings().concerns).ensure_defaults()
    AffectBook(env.world.data, MemoryV2Settings().affect).record("alegria", 0.8, 0.8, ["turn:s:1"])
    cookie, csrf = await login(env)
    assert (await env.post("/api/memory/affects/clear", cookie, csrf))[0] == 400
    status, body = await env.post("/api/memory/affects/clear?confirm=sentimentos", cookie, csrf)
    assert status == 200 and body["removed"] >= 1


async def test_the_layers_being_off_is_a_typed_503(tmp_path: Path) -> None:
    env = Env(tmp_path, Sources())
    assert await env.server.start()
    try:
        cookie, csrf = await env.session(env.owner.bearer)
        assert (await env.post("/api/memory/beliefs/x/forget?confirm=x", cookie, csrf))[0] == 503
        assert (await env.post("/api/memory/facts/x/forget?confirm=x", cookie, csrf))[0] == 503
    finally:
        await env.server.stop()


async def test_the_audit_never_carries_what_was_forgotten(env: MemEnv) -> None:
    fact = env.world.memory_item("Segredo-muito-pessoal-do-Petrus.")
    cookie, csrf = await login(env)
    await env.post(f"/api/memory/facts/{fact.id}/forget?confirm={fact.id}", cookie, csrf)
    text = "".join(f.read_text() for f in event_log.directory.glob("*"))
    assert '"op": "memory_forget"' in text and fact.id in text
    assert "Segredo-muito-pessoal" not in text
