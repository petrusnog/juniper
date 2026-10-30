"""Dashboard: login com client_id/client_secret e gestão de usuários (ADR-027)."""

from __future__ import annotations

import asyncio
import http.client
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import mongomock
import pytest

from brain.auth import AuthService
from brain.auth.credentials import load_pepper
from brain.auth.store import UserStore
from brain.dashboard.server import DashboardServer
from brain.memory.mongo import MongoConnection

LEGACY = "token-legado-do-dashboard"


class Env:
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
        self.owner = self.auth.create_user("Petrus", "owner")
        self.server = DashboardServer("127.0.0.1", 0, LEGACY, auth=self.auth)

    async def call(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        def run() -> tuple[int, dict[str, str], Any]:
            conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=3)
            try:
                conn.request(method, path, headers=headers or {})
                response = conn.getresponse()
                raw = response.read()
                h = {k.lower(): v for k, v in response.getheaders()}
                try:
                    return response.status, h, json.loads(raw)
                except ValueError:
                    return response.status, h, raw
            finally:
                conn.close()

        return await asyncio.to_thread(run)

    async def login(self, bearer: str) -> tuple[dict[str, str], str]:
        """The cookie header and the CSRF token of a fresh session."""
        status, headers, _ = await self.call(
            "/api/login", headers={"Authorization": f"Bearer {bearer}"}
        )
        assert status == 200
        cookie = {"Cookie": headers["set-cookie"].split(";")[0]}
        _, _, me = await self.call("/api/me", headers=cookie)
        return cookie, me["csrf"]

    async def post(
        self, path: str, cookie: dict[str, str], csrf: str | None, **extra: str
    ) -> tuple[int, Any]:
        headers = {**cookie, **extra}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        status, _, body = await self.call(path, method="POST", headers=headers)
        return status, body


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    e = Env(tmp_path)
    assert await e.server.start()
    yield e
    await e.server.stop()


async def test_login_needs_the_client_id_and_the_secret(env: Env) -> None:
    secret_only = env.owner.secret
    id_only = env.owner.credential.client_id
    for bearer in (secret_only, id_only, f"{id_only}.jsk_errado"):
        status, _, _ = await env.call("/api/login", headers={"Authorization": f"Bearer {bearer}"})
        assert status == 401
    cookie, _ = await env.login(env.owner.bearer)
    status, _, me = await env.call("/api/me", headers=cookie)
    assert status == 200 and me["user"]["name"] == "Petrus" and me["user"]["role"] == "owner"


async def test_the_owner_registers_a_user_and_sees_the_secret_once(env: Env) -> None:
    cookie, csrf = await env.login(env.owner.bearer)
    status, body = await env.post("/api/users?name=Ana&role=tester&label=celular", cookie, csrf)
    assert status == 200, body
    assert body["client_secret"].startswith("jsk_") and "não aparece de novo" in body["warning"]
    client_id = body["credential"]["client_id"]
    assert (
        await env.call(
            "/api/login", headers={"Authorization": f"Bearer {client_id}.{body['client_secret']}"}
        )
    )[0] == 200

    status, _, listing = await env.call("/api/users", headers=cookie)
    text = json.dumps(listing)
    assert status == 200 and "Ana" in text and client_id in text
    assert body["client_secret"] not in text and "secret_hash" not in text


async def test_writes_need_the_csrf_token_and_our_origin(env: Env) -> None:
    cookie, csrf = await env.login(env.owner.bearer)
    assert (await env.post("/api/users?name=Ana&role=tester", cookie, None))[0] == 403
    assert (await env.post("/api/users?name=Ana&role=tester", cookie, "errado"))[0] == 403
    status, _ = await env.post(
        "/api/users?name=Ana&role=tester", cookie, csrf, Origin="http://evil.example"
    )
    assert status == 403
    # a bearer header alone (no session cookie) cannot write either
    status, _, _ = await env.call(
        "/api/users?name=Ana&role=tester",
        method="POST",
        headers={"Authorization": f"Bearer {env.owner.bearer}"},
    )
    assert status == 403
    assert (await env.post("/api/users?name=Ana&role=tester", cookie, csrf))[0] == 200


async def test_a_tester_cannot_administer(env: Env) -> None:
    tester = env.auth.create_user("Ana", "tester")
    cookie, csrf = await env.login(tester.bearer)
    assert (await env.call("/api/users", headers=cookie))[0] == 403
    assert (await env.post("/api/users?name=Bia&role=tester", cookie, csrf))[0] == 403


async def test_destructive_actions_are_confirmed_by_the_server(env: Env) -> None:
    tester = env.auth.create_user("Ana", "tester")
    cookie, csrf = await env.login(env.owner.bearer)
    path = f"/api/users/{tester.user.user_id}/disable"
    status, body = await env.post(path, cookie, csrf)
    assert status == 400 and "Confirme" in body["error"]
    assert (await env.post(path + "?confirm=errado", cookie, csrf))[0] == 400
    assert (await env.post(path + "?confirm=Ana", cookie, csrf))[0] == 200
    assert not env.auth.authenticate(tester.bearer, "x").ok


async def test_rotating_or_revoking_ends_the_sessions_of_that_credential(env: Env) -> None:
    extra = env.auth.add_credential(env.owner.user.user_id, "tablet")
    owner_cookie, csrf = await env.login(env.owner.bearer)
    tablet_cookie, _ = await env.login(extra.bearer)
    assert (await env.call("/api/session", headers=tablet_cookie))[0] == 200

    client_id = extra.credential.client_id
    status, body = await env.post(
        f"/api/credentials/{client_id}/rotate?confirm={client_id}", owner_cookie, csrf
    )
    assert status == 200 and body["client_secret"] != extra.secret
    env.auth._cache.clear()
    assert (await env.call("/api/session", headers=tablet_cookie))[0] == 401
    assert not env.auth.authenticate(extra.bearer, "y").ok

    new_bearer = f"{client_id}.{body['client_secret']}"
    cookie2, _ = await env.login(new_bearer)
    status, _ = await env.post(
        f"/api/credentials/{client_id}/revoke?confirm={client_id}", owner_cookie, csrf
    )
    assert status == 200
    env.auth._cache.clear()
    assert (await env.call("/api/session", headers=cookie2))[0] == 401


async def test_the_last_owner_credential_cannot_be_revoked(env: Env) -> None:
    cookie, csrf = await env.login(env.owner.bearer)
    client_id = env.owner.credential.client_id
    status, body = await env.post(
        f"/api/credentials/{client_id}/revoke?confirm={client_id}", cookie, csrf
    )
    assert status == 400 and "última" in body["error"]


async def test_other_posts_and_methods_are_still_refused(env: Env) -> None:
    cookie, csrf = await env.login(env.owner.bearer)
    status, headers, _ = await env.call(
        "/api/session", method="POST", headers={**cookie, "X-CSRF-Token": csrf}
    )
    assert status == 405 and headers["allow"] == "GET"
    status, _, _ = await env.call("/api/users", method="DELETE", headers=cookie)
    assert status == 405


async def test_the_audit_log_records_who_did_what_without_secrets(env: Env) -> None:
    from brain.eventlog import event_log

    cookie, csrf = await env.login(env.owner.bearer)
    _, body = await env.post("/api/users?name=Ana&role=tester", cookie, csrf)
    text = "".join(f.read_text() for f in event_log.directory.glob("*"))
    assert '"op": "user_created"' in text and env.owner.user.user_id in text
    assert body["client_secret"] not in text and env.owner.secret not in text
