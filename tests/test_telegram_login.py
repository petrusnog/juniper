"""Telegram com login por client_id/client_secret (ADR-027 §6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import mongomock
import pytest

from brain.auth import AuthService, NewCredential
from brain.auth.credentials import load_pepper
from brain.auth.store import UserStore
from brain.clients import telegram as tg
from brain.memory.mongo import MongoConnection
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import Scenario
from tests.test_telegram import CHAT, OWNER, FakeMessenger, factory_for, msg


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Env:
    def __init__(self, tmp_path: Path, scenario: Scenario) -> None:
        connection = MongoConnection()
        connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
        self.auth = AuthService(
            UserStore(connection), load_pepper(tmp_path / "pepper"), allow_testers=True
        )
        self.owner: NewCredential = self.auth.create_user("Petrus", "owner")
        scenario.server._auth = self.auth
        self.scenario = scenario
        self.messenger = FakeMessenger()
        self.clock = Clock()
        self.bridge = tg.TelegramBridge(
            [OWNER],
            self.messenger,
            factory_for(scenario),
            require_login=True,
            clock=self.clock,
        )

    @property
    def login_text(self) -> str:
        return f"/login {self.owner.credential.client_id} {self.owner.secret}"


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    async with Scenario(FakeProvider([say("Oi!"), say("De novo!")]), tmp_path) as scenario:
        e = Env(tmp_path, scenario)
        yield e
        await e.bridge.close()


async def test_without_a_session_the_bot_asks_for_login_and_never_reaches_the_brain(
    env: Env,
) -> None:
    await env.bridge.handle(msg("oi"))
    assert env.messenger.texts == [tg.LOGIN_REQUIRED]
    assert env.scenario.server.connections == []


async def test_login_deletes_the_message_and_opens_the_session(env: Env) -> None:
    await env.bridge.handle(msg(env.login_text, message_id=77))
    assert (CHAT, 77) in env.messenger.deleted  # the message with the secret is gone
    assert env.messenger.texts[-1].startswith("Entrei, Petrus")
    await env.bridge.handle(msg("oi"))
    assert env.messenger.texts[-1] == "Oi!"
    assert env.owner.secret not in "".join(env.messenger.texts)


async def test_wrong_credentials_are_refused_and_the_message_is_still_deleted(env: Env) -> None:
    await env.bridge.handle(
        msg(f"/login {env.owner.credential.client_id} jsk_errado", message_id=5)
    )
    assert env.messenger.texts == [tg.LOGIN_REFUSED]
    assert (CHAT, 5) in env.messenger.deleted
    await env.bridge.handle(msg("oi"))
    assert env.messenger.texts[-1] == tg.LOGIN_REQUIRED
    await env.bridge.handle(msg("/login so-um-argumento"))
    assert env.messenger.texts[-1] == tg.LOGIN_USAGE


async def test_the_session_expires_after_idle_time_and_sair_ends_it(env: Env) -> None:
    await env.bridge.handle(msg(env.login_text))
    env.clock.now += 12 * 3600 + 1
    await env.bridge.handle(msg("oi"))
    assert env.messenger.texts[-1] == tg.LOGIN_REQUIRED

    await env.bridge.handle(msg(env.login_text))
    await env.bridge.handle(msg("/sair"))
    assert env.messenger.texts[-1] == tg.LOGGED_OUT
    await env.bridge.handle(msg("oi"))
    assert env.messenger.texts[-1] == tg.LOGIN_REQUIRED


async def test_a_revoked_credential_ends_the_session(env: Env) -> None:
    extra = env.auth.add_credential(env.owner.user.user_id, "telegram")
    await env.bridge.handle(msg(f"/login {extra.credential.client_id} {extra.secret}"))
    env.auth.revoke(extra.credential.client_id)
    env.auth._cache.clear()
    env.scenario.server._heartbeat = 0.05
    import asyncio

    await asyncio.sleep(0.3)
    env.bridge._users[OWNER].client = None  # the dropped connection
    await env.bridge.handle(msg("oi"))
    assert env.messenger.texts[-1] == tg.LOGIN_ENDED


async def test_the_login_secret_never_reaches_the_log(env: Env) -> None:
    from brain.eventlog import event_log

    await env.bridge.handle(msg(env.login_text))
    text = "".join(f.read_text() for f in event_log.directory.glob("*"))
    assert env.owner.secret not in text


async def test_without_require_login_nothing_changes(tmp_path: Path) -> None:
    async with Scenario(FakeProvider([say("Oi!")]), tmp_path) as s:
        messenger = FakeMessenger()
        bridge = tg.TelegramBridge([OWNER], messenger, factory_for(s))
        await bridge.handle(msg("oi"))
        await bridge.handle(msg("/login a b"))
        await bridge.close()
    assert messenger.texts[0] == "Oi!" and messenger.texts[1] == tg.UNKNOWN_COMMAND
