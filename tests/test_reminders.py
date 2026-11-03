"""Reminder lifecycle, isolation, delivery receipts and integration boundaries (no network)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import mongomock
import pytest

from brain.auth import Principal
from brain.auth.context import current_user
from brain.config import ReminderSettings, settings
from brain.core import clock
from brain.dashboard.reminders import ReminderApi
from brain.dashboard.sources import Sources
from brain.memory.errors import MemoryUnavailableError
from brain.memory.mongo import MongoConnection
from brain.reminders.delivery import send_due, voice_briefing
from brain.reminders.store import RETRY_AFTER, ReminderError, ReminderStore
from brain.tools import reminders as tools

NOW = datetime(2030, 5, 10, 12, tzinfo=UTC)  # 09:00 in São Paulo
DUE = "2030-05-10T16:00:00-03:00"
NOTICE = "2030-05-10T09:00:00-03:00"  # exactly NOW: the notice time has come


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda *args: NOW)
    monkeypatch.setattr(settings.agent, "timezone", "America/Sao_Paulo")
    monkeypatch.setattr(settings, "reminders", ReminderSettings())
    monkeypatch.setattr(settings.telegram, "allowed_user_ids", [123])
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)
    result = ReminderStore(connection)
    tools.bind(result)
    token = current_user.set("alice")
    yield result
    current_user.reset(token)
    tools.bind(None)
    connection.close()


def add(store, owner="alice", **changes):
    return store.create(
        owner, **({"title": "Dentista", "due_at": DUE, "notify_at": NOTICE} | changes)
    )


def test_crud_is_owner_scoped_and_dates_are_utc(store):
    doc = add(store)
    assert doc["due_at"] == datetime(2030, 5, 10, 19, tzinfo=UTC)
    assert doc["statuses"] == ["Pendente"]
    assert not store.list("bob")
    for operation in (store.get, store.delete):
        with pytest.raises(ReminderError):
            operation("bob", doc["id"])
    with pytest.raises(ReminderError):
        store.update("bob", doc["id"], title="Roubar")
    assert store.update("alice", doc["id"], title="Consulta")["title"] == "Consulta"
    store.delete("alice", doc["id"])
    assert not store.list("alice")


def test_default_notice_time(store):
    # An all-day deadline ends at 23:59:59 and warns at 09:00 that day.
    day = store.create("alice", title="Aluguel", due_at="2030-05-11")
    assert day["due_at"] == datetime(2030, 5, 12, 2, 59, 59, tzinfo=UTC)
    assert day["notify_at"] == datetime(2030, 5, 11, 12, tzinfo=UTC)
    # A timed deadline warns at that moment; a bare notice date means 09:00.
    timed = store.create("alice", title="Ligar", due_at=DUE)
    assert timed["notify_at"] == timed["due_at"]
    early = store.create(
        "alice", title="Ligar", due_at="2030-05-12T16:00:00-03:00", notify_at="2030-05-11"
    )
    assert early["notify_at"] == datetime(2030, 5, 11, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"due_at": "bad"},
        {"due_at": "2020-01-01"},
        {"notify_at": "2030-05-10T17:00:00-03:00"},  # after the deadline
        {"notify_at": "invalid"},
    ],
)
def test_invalid_reminders_do_not_write(store, changes):
    with pytest.raises(ReminderError):
        add(store, **changes)
    assert not store.list("alice")


def test_linked_calendar_retry_does_not_duplicate(store):
    first = add(store, event_id="event-1", calendar_id="cal")
    again = add(store, event_id="event-1", calendar_id="cal")
    assert first["id"] == again["id"]
    # Same event in another owner's account is independent.
    assert add(store, owner="bob", event_id="event-1", calendar_id="cal")["id"] != first["id"]


def test_leases_prevent_duplicate_workers_and_conflicting_edits(store):
    doc = add(store)
    token = store.claim(doc, "telegram")
    assert token and store.claim(doc, "telegram") is None
    with pytest.raises(ReminderError, match="em envio"):
        store.update("alice", doc["id"], title="Outra")
    with pytest.raises(ReminderError, match="em envio"):
        store.delete("alice", doc["id"])
    store.finish(doc, "telegram", token, success=False)
    assert store.claim(doc, "telegram")


async def test_success_marks_only_telegram_and_is_repeatable(store):
    doc = add(store)
    send = AsyncMock()
    assert await send_due(store, send) == 1
    send.assert_awaited_once()
    assert send.await_args.args[0] == 123  # an allowed Telegram user (a private chat)
    assert "Dentista" in send.await_args.args[1]
    found = store.get("alice", doc["id"])
    assert found["telegram_sent_at"] and not found["voice_reminded_at"]
    assert await send_due(store, send) == 0
    assert send.await_count == 1


async def test_failed_send_is_not_marked_and_retries_after_a_pause(store, monkeypatch):
    doc = add(store)
    failed = AsyncMock(side_effect=OSError("offline"))
    assert await send_due(store, failed) == 0
    assert not store.get("alice", doc["id"])["telegram_sent_at"]
    # The next minute's tick does not hammer Telegram: it waits for RETRY_AFTER.
    again = AsyncMock()
    assert await send_due(store, again) == 0 and not again.await_count
    monkeypatch.setattr(clock, "now", lambda *args: NOW + RETRY_AFTER + timedelta(seconds=1))
    assert await send_due(store, again) == 1


async def test_nothing_goes_out_before_its_time_or_without_recipients(store, monkeypatch):
    send = AsyncMock()
    add(store, notify_at="2030-05-10T10:00:00-03:00")  # one hour from now
    assert await send_due(store, send) == 0
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(hours=1))
    assert await send_due(store, send) == 1  # the minute it comes due, it goes
    monkeypatch.setattr(settings.telegram, "allowed_user_ids", [])
    add(store, title="Sem destino")
    assert await send_due(store, send) == 0 and send.await_count == 1


async def test_sent_to_every_allowed_user_and_a_retry_skips_who_already_got_it(store, monkeypatch):
    monkeypatch.setattr(settings.telegram, "allowed_user_ids", [123, 456])
    doc = add(store)
    calls: list[int] = []

    async def flaky(chat: int, text: str) -> None:
        calls.append(chat)
        if chat == 456 and calls.count(456) == 1:
            raise OSError("offline")

    assert await send_due(store, flaky) == 0
    monkeypatch.setattr(clock, "now", lambda *args: NOW + RETRY_AFTER + timedelta(seconds=1))
    assert await send_due(store, flaky) == 1
    assert calls == [123, 456, 456]  # 123 was not messaged twice
    assert store.get("alice", doc["id"])["telegram_sent_at"]


async def test_late_notice_still_goes_within_the_grace_window_only(store, monkeypatch):
    add(store)  # due at 16:00 (19:00 UTC), notice at 09:00
    send = AsyncMock()
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(hours=8))  # 4 h past due
    assert store.eligible("telegram")
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(hours=20))  # 13 h past due
    assert not store.eligible("telegram")
    assert await send_due(store, send) == 0 and not send.await_count


async def test_voice_once_across_sessions_and_independent_of_telegram(store):
    doc = add(store)
    await send_due(store, AsyncMock())
    text, finish = await voice_briefing(store, "alice")
    assert "Bom dia" in text and "Dentista" in text
    other_text, other_finish = await voice_briefing(store, "alice")
    assert "Dentista" not in other_text
    await other_finish(True)
    await finish(True)
    # A new store/session sees the durable receipt.
    text, finish = await voice_briefing(ReminderStore(store.connection), "alice")
    assert "Dentista" not in text
    await finish(True)
    assert store.get("alice", doc["id"])["statuses"] == [
        "Enviado pelo Telegram",
        "Relembrado no chat de voz",
    ]


async def test_voice_failure_releases_reminders_and_does_not_expose_other_owner(store):
    doc = add(store)
    text, finish = await voice_briefing(store, "bob")
    assert "Dentista" not in text
    await finish(True)
    text, finish = await voice_briefing(store, "alice")
    await finish(False)
    assert not store.get("alice", doc["id"])["voice_reminded_at"]
    text, finish = await voice_briefing(store, "alice")
    assert "Dentista" in text
    await finish(True)


async def test_edits_preserve_receipts_until_rescheduled(store):
    doc = add(store)
    await send_due(store, AsyncMock())
    changed = store.update("alice", doc["id"], title="Novo nome")
    assert changed["telegram_sent_at"]
    changed = store.update("alice", doc["id"], due_at="2030-05-11")
    # The 7 h lead (09:00 notice, 16:00 deadline) is kept: 23:59:59 - 7 h.
    assert changed["notify_at"] == datetime(2030, 5, 11, 19, 59, 59, tzinfo=UTC)
    assert not changed["telegram_sent_at"] and not changed["voice_reminded_at"]


async def test_tools_crud_and_no_double_confirmation(store):
    assert tools.CreateReminder.confirm is False
    assert tools.UpdateReminder.confirm is False
    assert tools.DeleteReminder.confirm is True
    created = await tools.CreateReminder().run(title="Pagar conta", due_at=DUE)
    assert created.success
    rid = created.metadata["reminder_id"]
    listed = await tools.ListReminders().run()
    assert rid in listed.output
    assert (await tools.UpdateReminder().run(reminder_id=rid, notes="Até às 16h")).success
    assert (await tools.DeleteReminder().run(reminder_id=rid)).success
    assert not (await tools.DeleteReminder().run(reminder_id=rid)).success


async def test_dashboard_crud_filters_permissions_and_confirmation(store):
    api = ReminderApi(Sources(reminders=store))
    alice = Principal("alice", "Alice", "owner", "c1", "browser", False)
    bob = Principal("bob", "Bob", "tester", "c2", "browser", False)
    status, body, _ = await api.handle(
        "POST",
        "/api/reminders",
        {"title": ["Pagar aluguel"], "due_at": [DUE], "owner_id": ["bob"]},
        alice,
    )
    assert status == 200
    rid = body["reminder"]["id"]
    status, body, _ = await api.handle("GET", "/api/reminders", {"q": ["aluguel"]}, alice)
    assert status == 200 and body["total"] == 1
    assert body["items"][0]["owner_id"] == "alice"
    assert (await api.handle("GET", "/api/reminders", {}, bob))[0] == 403
    assert (
        await api.handle("POST", f"/api/reminders/{rid}/update", {"title": ["Aluguel"]}, alice)
    )[0] == 200
    assert (await api.handle("POST", f"/api/reminders/{rid}/delete", {}, alice))[0] == 400
    assert (await api.handle("POST", f"/api/reminders/{rid}/delete", {"confirm": [rid]}, alice))[
        0
    ] == 200


async def test_calendar_combined_request_and_sync(store, monkeypatch):
    from brain.tools import google_calendar as gc
    from tests.test_google_calendar import FakeClient

    client = FakeClient()
    monkeypatch.setattr(gc, "get_client", lambda: client)
    result = await gc.CreateEvent().run(title="Dentista", start=DUE, reminder=True)
    assert result.success and len(client.inserted) == 1
    doc = store.get("alice", result.metadata["reminder_id"])
    assert doc["event_id"] == "new1"
    assert "Aviso (lembrete) salvo" in result.output
    from brain.tools.google_calendar import CalendarEvent

    event = CalendarEvent(
        "new1",
        "Consulta",
        datetime.fromisoformat(DUE),
        datetime.fromisoformat(DUE) + timedelta(hours=1),
        doc["calendar_id"],
    )
    assert "atualizado" in await gc._sync_reminder(event, renamed=True)
    assert store.get("alice", doc["id"])["title"] == "Consulta"
    assert "excluído" in await gc._sync_reminder(event, deleted=True)
    assert not store.list("alice")


async def test_calendar_offers_only_if_not_explicitly_declined(store, monkeypatch):
    from brain.tools import google_calendar as gc
    from tests.test_google_calendar import FakeClient

    monkeypatch.setattr(gc, "get_client", lambda: FakeClient())
    result = await gc.CreateEvent().run(title="Dentista", start=DUE)
    assert result.success and result.metadata["offer_reminder"]
    result = await gc.CreateEvent().run(title="Dentista", start=DUE, reminder=False)
    assert result.success and "offer_reminder" not in result.metadata
    assert not store.list("alice")


async def test_calendar_partial_failure_does_not_report_event_failure(store, monkeypatch):
    from brain.tools import google_calendar as gc
    from tests.test_google_calendar import FakeClient

    client = FakeClient()
    monkeypatch.setattr(gc, "get_client", lambda: client)

    def fail(*args, **kwargs):
        raise MemoryUnavailableError("offline")

    monkeypatch.setattr(store, "create", fail)
    result = await gc.CreateEvent().run(title="Dentista", start=DUE, reminder=True)
    assert result.success and len(client.inserted) == 1
    assert result.metadata["event_id"] == "new1"
    assert result.metadata["reminder_error"] == "offline"


async def test_voice_opening_reads_once_and_commits_only_after_speech(store, tmp_path):
    from tests.test_agent import FakeProvider
    from tests.test_voice_session import Rig, start, wait_for

    doc = add(store, owner="owner")
    from brain.auth import AuthService
    from tests.test_voice_session import TOKEN

    rig = Rig(tmp_path, FakeProvider([]))
    rig.server._auth = AuthService.legacy_only(TOKEN)
    async with rig:
        client, _ = await rig.open()
        await wait_for(lambda: bool(store.get("owner", doc["id"])["voice_reminded_at"]))
        assert any("Bom dia" in text and "Dentista" in text for text in rig.synth.spoken)
        await client.send(type="voice.end")
        await wait_for(lambda: not rig.handler._voices)
        before = len(rig.synth.spoken)
        await start(client)
        import asyncio

        await asyncio.sleep(0.05)
        assert len(rig.synth.spoken) == before


async def test_voice_connection_failure_does_not_mark_as_reminded(store, tmp_path):
    import asyncio

    from brain.clients.voice_session import VoiceSession
    from tests.test_agent import FakeProvider
    from tests.test_voice_session import Rig, wait_for

    doc = add(store)
    rig = Rig(tmp_path, FakeProvider([]))
    session = VoiceSession(
        rig.services,
        send=AsyncMock(return_value=False),
        submit=AsyncMock(),
        cancel_turn=AsyncMock(),
        turn_active=lambda: False,
        ended=lambda: None,
        clear_pending=lambda: None,
        briefing=lambda: voice_briefing(store, "alice"),
    )
    try:
        await session.start("v-1")
        await wait_for(lambda: session._speech is None)
        assert not store.get("alice", doc["id"])["voice_reminded_at"]
        # Failed transmission releases the lease immediately.
        assert store.claim(doc, "voice")
    finally:
        await session.end("server")
        await asyncio.sleep(0)


async def test_dashboard_http_requires_csrf_and_scopes_to_logged_in_user(store):
    import json
    from urllib.parse import urlencode

    from brain.dashboard.actions import Actions
    from brain.dashboard.server import DashboardServer
    from tests.test_dashboard_server import TOKEN, get, login

    dash = DashboardServer("127.0.0.1", 0, TOKEN, actions=Actions(Sources(reminders=store)))
    assert await dash.start()
    try:
        cookie = await login(dash)
        headers = {"Cookie": cookie}
        status, _, body = await get(dash, "/api/me", headers=headers)
        me = json.loads(body)
        query = urlencode({"title": "Conta de luz", "due_at": DUE})
        path = "/api/reminders?" + query
        status, _, _ = await get(dash, path, method="POST", headers=headers)
        assert status == 403
        headers["X-CSRF-Token"] = me["csrf"]
        status, _, body = await get(dash, path, method="POST", headers=headers)
        assert status == 200
        status, _, body = await get(dash, "/api/reminders", headers=headers)
        assert status == 200 and json.loads(body)["total"] == 1
        assert store.list(me["user"]["id"])[0]["title"] == "Conta de luz"
    finally:
        await dash.stop()


def test_expired_history_can_be_edited_without_reopening_delivery(store, monkeypatch):
    doc = add(store)
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(days=1))
    changed = store.update("alice", doc["id"], notes="Já resolvido")
    assert changed["statuses"] == ["Vencido"]
    assert changed["notes"] == "Já resolvido"


def test_crashed_worker_lease_expires_and_stale_receipt_cannot_win(store, monkeypatch):
    doc = add(store)
    old = store.claim(doc, "voice")
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(minutes=11))
    new = store.claim(doc, "voice")
    assert new and new != old
    store.finish(doc, "voice", old, success=True)
    assert not store.get("alice", doc["id"])["voice_reminded_at"]
    store.finish(doc, "voice", new, success=True)
    assert store.get("alice", doc["id"])["voice_reminded_at"]


async def test_calendar_and_reminder_use_one_dispatcher_confirmation(store, monkeypatch):
    from brain.core.dispatcher import Dispatcher
    from brain.llm.types import ToolCall
    from brain.tools import google_calendar as gc
    from brain.tools.registry import ToolRegistry
    from tests.test_google_calendar import FakeClient

    client = FakeClient()
    monkeypatch.setattr(gc, "get_client", lambda: client)
    registry = ToolRegistry()
    registry.register(gc.CreateEvent)
    registry.register(tools.CreateReminder)
    confirm = AsyncMock(return_value=True)
    dispatcher = Dispatcher(confirm=confirm, tool_registry=registry)
    result = await dispatcher.execute(
        ToolCall("1", "create_event", {"title": "Dentista", "start": DUE, "reminder": True})
    )
    assert result.success and len(store.list("alice")) == 1
    confirm.assert_awaited_once()
    assert "lembrete" in confirm.await_args.args[3]
    # A standalone explicit reminder needs no second confirmation.
    result = await dispatcher.execute(
        ToolCall("2", "create_reminder", {"title": "Pagar conta", "due_at": DUE})
    )
    assert result.success and len(store.list("alice")) == 2
    assert confirm.await_count == 1


async def test_declining_combined_calendar_action_writes_neither(store, monkeypatch):
    from brain.core.dispatcher import Dispatcher
    from brain.llm.types import ToolCall
    from brain.tools import google_calendar as gc
    from brain.tools.registry import ToolRegistry
    from tests.test_google_calendar import FakeClient

    client = FakeClient()
    monkeypatch.setattr(gc, "get_client", lambda: client)
    registry = ToolRegistry()
    registry.register(gc.CreateEvent)
    dispatcher = Dispatcher(confirm=AsyncMock(return_value=False), tool_registry=registry)
    result = await dispatcher.execute(
        ToolCall("1", "create_event", {"title": "Dentista", "start": DUE, "reminder": True})
    )
    assert not result.success and not client.inserted and not store.list("alice")


@pytest.mark.parametrize("wake", [False, True])
async def test_empty_briefing_preserves_original_voice_behavior(store, tmp_path, wake):
    from brain.clients.voice_session import VoiceSession
    from tests.test_agent import FakeProvider
    from tests.test_voice_session import Rig, wait_for

    rig = Rig(tmp_path, FakeProvider([]))
    session = VoiceSession(
        rig.services,
        send=AsyncMock(return_value=True),
        submit=AsyncMock(),
        cancel_turn=AsyncMock(),
        turn_active=lambda: False,
        ended=lambda: None,
        clear_pending=lambda: None,
        wake=wake,
        greeting=lambda: "Saudação original.",
        briefing=lambda: voice_briefing(store, "alice"),
    )
    try:
        await session.start("empty")
        if wake:
            await wait_for(lambda: session._speech is None)
            assert rig.synth.spoken == ["Saudação original."]
        else:
            assert not rig.synth.spoken and session._speech is None
    finally:
        await session.end("server")


async def test_briefing_enqueue_failure_releases_claims(store, tmp_path, monkeypatch):
    from brain.clients.voice_session import VoiceSession
    from tests.test_agent import FakeProvider
    from tests.test_voice_session import Rig

    doc = add(store)
    rig = Rig(tmp_path, FakeProvider([]))
    session = VoiceSession(
        rig.services,
        send=AsyncMock(return_value=True),
        submit=AsyncMock(),
        cancel_turn=AsyncMock(),
        turn_active=lambda: False,
        ended=lambda: None,
        clear_pending=lambda: None,
        briefing=lambda: voice_briefing(store, "alice"),
    )
    original = session._say_standalone

    async def fail_once(text, **kwargs):
        if kwargs.get("delivered") is not None:
            raise RuntimeError("cannot enqueue")
        await original(text, **kwargs)

    monkeypatch.setattr(session, "_say_standalone", fail_once)
    try:
        await session.start("fail")
        # The reminder is immediately editable: no abandoned ten-minute lease.
        assert store.update("alice", doc["id"], notes="retry")["notes"] == "retry"
    finally:
        await session.end("server")


async def test_unidentified_local_tools_report_error_instead_of_creating_orphan(store):
    token = current_user.set(None)
    try:
        result = await tools.CreateReminder().run(title="Conta", due_at=DUE)
        assert not result.success and "CLI local" in result.error
        assert not (await tools.ListReminders().run()).success
        assert not store.list("local")
    finally:
        current_user.reset(token)


async def test_calendar_link_is_validated_before_writing(store, monkeypatch):
    from brain.tools import google_calendar as gc
    from tests.test_google_calendar import FakeClient, ev

    client = FakeClient()
    monkeypatch.setattr(gc, "get_client", lambda: client)
    result = await tools.CreateReminder().run(
        title="Conta", due_at=DUE, event_id="invented", calendar_id=client.write_calendar_id
    )
    assert not result.success and not store.list("alice")
    client.events.append(
        ev(
            "real",
            "Conta",
            datetime.fromisoformat(DUE),
            datetime.fromisoformat(DUE) + timedelta(hours=1),
        )
    )
    result = await tools.CreateReminder().run(
        title="Conta", due_at=DUE, event_id="real", calendar_id=client.write_calendar_id
    )
    assert result.success and store.list("alice")[0]["event_id"] == "real"


def test_rescheduling_keeps_the_lead_time(store):
    doc = add(store, due_at="2030-05-15T16:00:00-03:00", notify_at="2030-05-14T16:00:00-03:00")
    changed = store.update("alice", doc["id"], due_at="2030-05-20T18:00:00-03:00")
    assert changed["notify_at"] == datetime(2030, 5, 19, 21, tzinfo=UTC)  # still a day before
    new = "2030-05-21T09:30:00-03:00"
    explicit = store.update("alice", doc["id"], due_at="2030-05-22T10:00:00-03:00", notify_at=new)
    assert explicit["notify_at"] == datetime(2030, 5, 21, 12, 30, tzinfo=UTC)


async def test_delete_event_confirmation_mentions_linked_reminder(store, monkeypatch):
    from brain.tools import google_calendar as gc
    from tests.test_google_calendar import FakeClient, ev

    event = ev(
        "event1",
        "Dentista",
        datetime.fromisoformat(DUE),
        datetime.fromisoformat(DUE) + timedelta(hours=1),
    )
    client = FakeClient([event])
    monkeypatch.setattr(gc, "get_client", lambda: client)
    add(store, event_id=event.id, calendar_id=event.calendar_id)
    text = await gc.DeleteEvent().describe(event_id=event.id)
    assert "excluir o lembrete vinculado" in text
    assert "Dentista" in text


async def test_the_minute_tick_sends_what_is_due_and_prunes_once_a_day(store, monkeypatch):
    from brain.reminders import delivery

    add(store)
    sent: list[str] = []

    async def fake_send(chat: int, text: str) -> None:
        sent.append(text)

    original = delivery.send_due

    async def tick(reminders):
        return await original(reminders, fake_send)

    monkeypatch.setattr(delivery, "send_due", tick)
    pruned = []
    monkeypatch.setattr(store, "prune", lambda: pruned.append(1) or 0)
    monkeypatch.setitem(delivery._state, "pruned_on", None)
    await delivery.reminder_job()
    await delivery.reminder_job()
    assert len(sent) == 1 and len(pruned) == 1


async def test_an_outage_is_logged_once_not_every_minute(store, monkeypatch):
    from brain.reminders import delivery

    lines: list[str] = []
    monkeypatch.setattr(
        delivery.event_log, "log", lambda kind, **kw: lines.append(kw.get("action", ""))
    )
    monkeypatch.setitem(delivery._state, "failing", False)
    monkeypatch.setitem(delivery._state, "pruned_on", None)

    def down(*args, **kwargs):
        raise MemoryUnavailableError("offline")

    monkeypatch.setattr(store, "eligible", down)
    for _ in range(3):
        await delivery.reminder_job()
    assert lines.count("job_failed") == 1


def test_retention_prunes_expired_history_and_list_is_not_truncated(store, monkeypatch):
    doc = add(store)
    # Historical imports can exceed one page; active data must not disappear behind a 500 cap.
    raw = store.collection().find_one({"_id": doc["id"]})
    store.collection().insert_many([{**raw, "_id": f"import-{i}"} for i in range(501)])
    assert len(store.list("alice")) == 502
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(days=91))
    assert store.prune() == 502
    assert not store.list("alice")


async def test_successful_send_with_failed_receipt_is_documented_at_least_once(store, monkeypatch):
    doc = add(store)
    original = store.finish
    monkeypatch.setattr(
        store,
        "finish",
        lambda *a, **k: (_ for _ in ()).throw(MemoryUnavailableError("database offline")),
    )
    send = AsyncMock()
    with pytest.raises(MemoryUnavailableError):
        await send_due(store, send)
    assert send.await_count == 1
    monkeypatch.setattr(store, "finish", original)
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(minutes=11))
    await send_due(store, send)
    assert send.await_count == 2
    assert store.get("alice", doc["id"])["telegram_sent_at"]


def test_ids_are_prefixed_and_survive_dashboard_redaction(store):
    from brain.dashboard.serialize import sanitize

    fresh = add(store)
    assert fresh["id"].startswith("rem_") and len(fresh["id"]) == 36
    assert sanitize(fresh)["id"] == fresh["id"]


async def test_dashboard_notes_are_capped_because_they_ride_in_the_url(store):
    from brain.dashboard.reminders import MAX_NOTES

    api = ReminderApi(Sources(reminders=store))
    alice = Principal("alice", "Alice", "owner", "c1", "browser", False)
    ok = {"title": ["A"], "due_at": [DUE], "notes": ["x" * MAX_NOTES]}
    assert (await api.handle("POST", "/api/reminders", ok, alice))[0] == 200
    too_long = {**ok, "notes": ["x" * (MAX_NOTES + 1)]}
    status, body, _ = await api.handle("POST", "/api/reminders", too_long, alice)
    assert status == 400 and str(MAX_NOTES) in body["error"]
    # The tools (chat) keep the store's larger limit.
    assert store.create("alice", title="B", due_at=DUE, notes="x" * 2000)["notes"]


async def test_without_an_owner_calendar_hooks_stay_silent(store, monkeypatch):
    from types import SimpleNamespace

    from brain.tools import google_calendar as gc

    event = SimpleNamespace()  # never touched: the hook returns before reading it
    token = current_user.set(None)  # the local CLI: no logged-in owner
    try:
        assert not tools.usable()
        assert await gc._sync_reminder(event, deleted=True) == ""
    finally:
        current_user.reset(token)


async def test_failed_briefing_is_silent_unless_wake(store, tmp_path):
    from brain.clients.voice_session import VoiceSession
    from tests.test_agent import FakeProvider
    from tests.test_voice_session import Rig, wait_for

    async def broken():
        raise MemoryUnavailableError("mongo fora")

    for wake in (False, True):
        rig = Rig(tmp_path, FakeProvider([]))
        session = VoiceSession(
            rig.services,
            send=AsyncMock(return_value=True),
            submit=AsyncMock(),
            cancel_turn=AsyncMock(),
            turn_active=lambda: False,
            ended=lambda: None,
            clear_pending=lambda: None,
            wake=wake,
            briefing=broken,
        )
        try:
            await session.start("broken")
            if wake:
                await wait_for(lambda s=session: s._speech is None)
                assert rig.synth.spoken and "Não consegui" in rig.synth.spoken[0]
            else:
                assert not rig.synth.spoken
        finally:
            await session.end("server")


def test_records_from_the_first_release_are_migrated_to_notify_at(store):
    doc = add(store)
    raw = store.collection().find_one({"_id": doc["id"]})
    legacy = {k: v for k, v in raw.items() if k not in ("notify_at", "_id")}
    store.collection().insert_one({**legacy, "_id": "rem_legacy", "notify_on": "2030-05-10"})
    store.collection().delete_one({"_id": doc["id"]})
    fresh = ReminderStore(store.connection)  # the migration runs when a store first opens
    migrated = fresh.get("alice", "rem_legacy")
    assert migrated["notify_at"] == datetime(2030, 5, 10, 12, tzinfo=UTC)  # 09:00 that day
    assert "notify_on" not in migrated


def test_scheduler_ticks_at_the_top_of_every_minute():
    from brain.scheduler import seconds_to_next_minute

    assert seconds_to_next_minute(datetime(2030, 1, 1, 9, 0, 0)) == 60
    assert seconds_to_next_minute(datetime(2030, 1, 1, 9, 0, 45)) == 15
    assert seconds_to_next_minute(datetime(2030, 1, 1, 9, 0, 59, 999_999)) == 1  # never a busy loop


async def test_run_every_minute_keeps_going_after_a_failing_run(monkeypatch):
    from brain import scheduler

    runs: list[int] = []

    async def job() -> None:
        runs.append(1)
        raise RuntimeError("boom")  # logged by the scheduler, never stops it

    async def stop_after_three(_seconds: float) -> None:
        if len(runs) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", stop_after_three)
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_every_minute("reminders", job)
    assert len(runs) == 3


def test_the_model_is_told_event_and_reminder_are_different_things():
    from pathlib import Path

    from brain.tools.google_calendar import CreateEvent

    create = tools.CreateReminder.description
    assert "LEMBRETE ISOLADO" in create and "SEM criar evento" in create
    assert "create_event" in create  # points the other way for a real appointment
    event = CreateEvent.description
    assert "EVENTO NA AGENDA" in event and "create_reminder" in event
    for name in ("persona.core.md", "persona.core.compact.md"):
        text = (Path(__file__).parent.parent / "prompts" / name).read_text(encoding="utf-8")
        assert "create_event" in text and "create_reminder" in text
        assert "reminder_at" in text


async def test_the_server_runs_the_reminder_tick_every_minute(store, monkeypatch):
    import os
    import signal

    from brain.clients import server as server_module
    from brain.clients.server import BrainServer, run_server
    from tests.test_server import TOKEN, RecordingHandler, wait_until

    scheduled: list[str] = []

    async def fake_every_minute(name, job):
        scheduled.append(name)
        await asyncio.Event().wait()

    monkeypatch.setattr(server_module, "run_every_minute", fake_every_minute)
    server = BrainServer("127.0.0.1", 0, TOKEN, RecordingHandler())
    task = asyncio.create_task(run_server(server))
    await wait_until(lambda: server.port != 0 and scheduled)
    os.kill(os.getpid(), signal.SIGTERM)
    async with asyncio.timeout(2):
        await task
    assert scheduled == ["reminders"]


# --- round trips and the event loop: what makes the assistant feel slow ----------------------


class _Counting:
    """Wraps a Mongo collection and counts every driver call."""

    def __init__(self, inner, calls):
        self._inner, self._calls = inner, calls

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def counted(*args, **kwargs):
            self._calls.append(name)
            return attr(*args, **kwargs)

        return counted


def counted(store):
    """Make ``store`` report its driver calls (index creation excluded: it runs once)."""
    store.collection()  # indexes and migration happen here, once
    calls: list[str] = []
    real = store.connection.collection
    store.connection.collection = lambda name: _Counting(real(name), calls)
    return calls


async def test_creating_an_event_with_a_reminder_scans_the_calendar_no_more_than_without(
    store, monkeypatch
):
    """Each scan lists every calendar one after the other: a needless one is seconds of wait."""
    from brain.tools import google_calendar as gc
    from tests.test_google_calendar import FakeClient

    scans = 0
    original = gc._conflicts

    async def scan(*args, **kwargs):
        nonlocal scans
        scans += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(gc, "get_client", lambda: FakeClient())
    monkeypatch.setattr(gc, "_conflicts", scan)

    async def as_the_dispatcher_does(**extra):
        nonlocal scans
        scans = 0
        tool, args = gc.CreateEvent(), {"title": "Dentista", "start": "2030-05-11T16:00", **extra}
        await tool.precheck(**args)
        await tool.describe(**args)
        assert (await tool.run(**args)).success
        return scans

    plain = await as_the_dispatcher_does()
    with_reminder = await as_the_dispatcher_does(reminder=True)
    assert with_reminder == plain


async def test_creating_a_reminder_is_one_write_and_no_calendar_call(store, monkeypatch):
    from brain.tools import google_calendar as gc

    monkeypatch.setattr(gc, "get_client", lambda: (_ for _ in ()).throw(AssertionError("Google")))
    calls = counted(store)
    result = await tools.CreateReminder().run(title="Ligar", due_at=DUE, notify_at=NOTICE)
    assert result.success
    assert calls == ["insert_one"]


async def test_an_idle_tick_is_a_single_query_whatever_the_history(store, monkeypatch):
    from brain.reminders import delivery

    for i in range(40):  # plenty of history: none of it may cost a call of its own
        add(store, title=f"Antigo {i}", notify_at=NOTICE, due_at="2030-05-10T09:30:00-03:00")
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(days=2))
    today = delivery.now().astimezone(delivery.ZoneInfo(settings.agent.timezone)).date()
    monkeypatch.setitem(delivery._state, "pruned_on", today)
    calls = counted(store)
    await delivery.reminder_job()
    assert calls == ["find"]


async def test_opening_the_voice_with_nothing_due_is_a_single_query(store):
    calls = counted(store)
    text, _ = await voice_briefing(store, "alice")
    assert text == "" and calls == ["find"]


async def test_a_slow_database_never_blocks_the_event_loop(store, monkeypatch):
    """A tick waiting on MongoDB must not freeze the chat: the loop keeps beating meanwhile."""
    import time

    from brain.reminders import delivery

    monkeypatch.setitem(delivery._state, "pruned_on", None)
    original = store.eligible

    def slow(*args, **kwargs):
        time.sleep(0.4)  # a database that takes its time, in the worker thread
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "eligible", slow)
    monkeypatch.setattr(store, "prune", lambda: time.sleep(0.4) or 0)
    worst, running = 0.0, True

    async def heartbeat():
        nonlocal worst
        last = time.perf_counter()
        while running:
            await asyncio.sleep(0.01)
            now_ = time.perf_counter()
            worst, last = max(worst, now_ - last), now_

    beat = asyncio.create_task(heartbeat())
    await delivery.reminder_job()
    running = False
    await beat
    assert worst < 0.2  # the 0.8 s of waiting happened off the loop
