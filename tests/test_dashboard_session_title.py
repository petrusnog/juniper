"""Dashboard: título das conversas e renomear (T-229)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from brain.core.session import Session
from brain.dashboard.sources import Sources
from brain.llm.types import Message
from brain.memory.session import FileSessionStore
from brain.memory.v2.models import TranscriptTurn, Watermark
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_issues import Env

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


class TitleEnv(Env):
    def __init__(self, tmp_path: Path) -> None:
        self.sessions = FileSessionStore(tmp_path / "sessions")
        self.data = V2Data.over(InMemoryDocStore())
        self.renamed: list[tuple[str, str]] = []
        self.live = True
        super().__init__(
            tmp_path,
            Sources(data=self.data, sessions=self.sessions, rename=self.rename),
        )

    async def rename(self, session_id: str, title: str) -> bool:
        if not self.live:
            return self.sessions.set_title(session_id, title, "user")
        self.renamed.append((session_id, title))
        return self.sessions.set_title(session_id, title, "user")

    def conversation(self, title: str, source: str, user_text: str = "pergunta") -> str:
        session = Session("persona")
        session.add_user(user_text)
        session.add(Message("assistant", "resposta"))
        session.title, session.title_source = title, source
        self.sessions.save(session)
        self.data.transcripts.save(TranscriptTurn(session.id, 0, user_text, "resposta", at=NOW))
        self.data.watermarks.save(Watermark(session.id, 0, 1, NOW, updated_at=NOW))
        return session.id


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[TitleEnv]:
    e = TitleEnv(tmp_path)
    assert await e.server.start()
    yield e
    await e.server.stop()


async def test_the_list_and_the_conversation_carry_the_title(env: TitleEnv) -> None:
    named = env.conversation("Receita de bolo", "auto")
    old = env.conversation("", "", "como configuro o wifi?")
    cookie, _ = await env.session(env.owner.bearer)
    status, body = await env.call("/api/sessions", headers=cookie)
    assert status == 200
    rows = {r["session_id"]: r for r in body["items"]}
    assert (rows[named]["title"], rows[named]["title_source"]) == ("Receita de bolo", "auto")
    assert rows[old]["title"] == "" and rows[old]["preview"] == "como configuro o wifi?"
    _, detail = await env.call(f"/api/sessions/{named}", headers=cookie)
    assert detail["title"] == "Receita de bolo" and detail["title_source"] == "auto"


async def test_the_owner_renames_with_csrf_and_the_title_is_the_users(env: TitleEnv) -> None:
    sid = env.conversation("Do modelo", "auto")
    cookie, csrf = await env.session(env.owner.bearer)
    status, body = await env.post(
        f"/api/sessions/{sid}/rename?title=%20Meu%20*bolo*%20", cookie, csrf
    )
    assert status == 200 and body["title"] == "Meu bolo"
    saved = env.sessions.load(sid)
    assert saved is not None and (saved.title, saved.title_source) == ("Meu bolo", "user")
    assert env.renamed == [(sid, "Meu bolo")]  # through the server's path: open windows hear of it


async def test_rename_checks_who_asks_and_what(env: TitleEnv) -> None:
    sid = env.conversation("A", "auto")
    cookie, csrf = await env.session(env.owner.bearer)
    assert (await env.post(f"/api/sessions/{sid}/rename?title=", cookie, csrf))[0] == 400
    assert (await env.post(f"/api/sessions/{sid}/rename?title=%3Cb%3E%3C/b%3E", cookie, csrf))[
        0
    ] == 400
    missing = await env.post("/api/sessions/20200101-000000-aaaaaa/rename?title=x", cookie, csrf)
    assert missing[0] == 404
    # no anti-CSRF token: refused before it gets here
    assert (await env.post(f"/api/sessions/{sid}/rename?title=x", cookie, ""))[0] in (400, 403)
    env.auth.allow_testers = True
    tester = env.auth.create_user("Visita", "tester")
    t_cookie, t_csrf = await env.session(tester.bearer)
    assert (await env.post(f"/api/sessions/{sid}/rename?title=x", t_cookie, t_csrf))[0] == 403
    assert env.sessions.load(sid).title == "A"  # type: ignore[union-attr]
    # a plain GET of the conversation is still the read API, not the rename
    assert (await env.call(f"/api/sessions/{sid}", headers=cookie))[0] == 200


async def test_the_audit_detail_shows_the_conversation_title(env: TitleEnv) -> None:
    from brain.dashboard.routes import build_api
    from tests.test_dashboard_activity import add, make_store

    sid = env.conversation("Receita de bolo", "auto")
    old = env.conversation("", "", "como configuro o wifi?")
    store = make_store()
    titled = add(store, "turn_start", {"input_chars": 3}, 0, session=sid)
    plain = add(store, "turn_start", {"input_chars": 3}, 1, session=old)
    api = build_api(Sources(activity=store, sessions=env.sessions))
    assert (await api.handle(f"/activity/{titled}", {}))[1]["session_title"] == "Receita de bolo"
    assert (await api.handle(f"/activity/{plain}", {}))[1]["session_title"] == (
        "como configuro o wifi?"  # an older conversation: its first message
    )
