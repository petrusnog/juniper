"""WebSocket com usuários e credenciais (ADR-027): login, welcome.user, revogação ao vivo."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import mongomock
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from brain.auth import AuthService, NewCredential
from brain.auth.credentials import load_pepper
from brain.auth.store import UserStore
from brain.clients import protocol as p
from brain.clients.server import BaseHandler, BrainServer
from brain.memory.mongo import MongoConnection
from tests.test_server import hello, url

LEGACY = "token-estatico-legado"


class World:
    def __init__(self, tmp_path: Path) -> None:
        connection = MongoConnection()
        connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
        self.auth = AuthService(
            UserStore(connection),
            load_pepper(tmp_path / "pepper"),
            mirror_file=tmp_path / "owner.json",
            legacy_token=LEGACY,
            allow_testers=True,
        )
        self.owner: NewCredential = self.auth.create_user("Petrus", "owner")
        self.server = BrainServer(
            "127.0.0.1", 0, LEGACY, BaseHandler(), auth=self.auth, heartbeat_seconds=0.05
        )


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    w = World(tmp_path)
    await w.server.start()
    yield w
    await w.server.stop()


async def welcome(w: World, bearer: str) -> dict[str, object]:
    ws = await connect(url(w.server), additional_headers={"Authorization": f"Bearer {bearer}"})
    try:
        await ws.send(hello())
        return json.loads(await ws.recv())  # type: ignore[no-any-return]
    finally:
        await ws.close()


async def test_the_credential_opens_the_door_and_the_welcome_names_the_user(world: World) -> None:
    message = await welcome(world, world.owner.bearer)
    assert message["type"] == "welcome"
    assert message["user"] == {
        "id": world.owner.user.user_id,
        "name": "Petrus",
        "role": "owner",
    }
    p.parse_server_message(json.dumps(message))  # the schema accepts the new field


async def test_wrong_secret_unknown_id_and_no_header_are_a_plain_401(world: World) -> None:
    bad = [
        f"{world.owner.credential.client_id}.jsk_errado",
        f"jc_naoexiste.{world.owner.secret}",
        "lixo",
    ]
    for bearer in bad:
        with pytest.raises(InvalidStatus) as err:
            await connect(
                url(world.server), additional_headers={"Authorization": f"Bearer {bearer}"}
            )
        assert err.value.response.status_code == 401


async def test_the_legacy_token_works_until_it_is_switched_off(world: World) -> None:
    assert (await welcome(world, LEGACY))["type"] == "welcome"
    world.auth._legacy = None
    with pytest.raises(InvalidStatus):
        await connect(url(world.server), additional_headers={"Authorization": f"Bearer {LEGACY}"})


async def test_a_disabled_user_cannot_connect(world: World) -> None:
    tester = world.auth.create_user("Ana", "tester")
    assert (await welcome(world, tester.bearer))["type"] == "welcome"
    world.auth.set_active(tester.user.user_id, False)
    with pytest.raises(InvalidStatus):
        await connect(
            url(world.server), additional_headers={"Authorization": f"Bearer {tester.bearer}"}
        )


async def test_revoking_a_credential_cuts_the_live_connection(world: World) -> None:
    tester = world.auth.create_user("Ana", "tester")
    ws = await connect(
        url(world.server), additional_headers={"Authorization": f"Bearer {tester.bearer}"}
    )
    await ws.send(hello("tablet"))
    assert json.loads(await ws.recv())["type"] == "welcome"
    world.auth.revoke(tester.credential.client_id)
    world.auth._cache.clear()
    with pytest.raises(ConnectionClosed) as closed:
        async with asyncio.timeout(3):
            while True:
                await ws.recv()
    assert closed.value.rcvd is not None and closed.value.rcvd.code == p.CLOSE_UNAUTHORIZED
