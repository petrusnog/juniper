"""Reminder lifecycle, isolation, delivery receipts and integration boundaries (no network)."""

from __future__ import annotations

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
from brain.reminders.delivery import send_morning, voice_briefing
from brain.reminders.store import ReminderError, ReminderStore
from brain.tools import reminders as tools

NOW = datetime(2030, 5, 10, 12, tzinfo=UTC)  # 09:00 in São Paulo
DUE = "2030-05-10T16:00:00-03:00"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda *args: NOW)
    monkeypatch.setattr(settings.agent, "timezone", "America/Sao_Paulo")
    monkeypatch.setattr(settings, "reminders", ReminderSettings(telegram_chat_ids={"alice": 123}))
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
    return store.create(owner, **({"title": "Dentista", "due_at": DUE} | changes))


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


def test_date_only_end_of_day_and_default_notify_day(store):
    doc = add(store, due_at="2030-05-11")
    assert doc["due_at"] == datetime(2030, 5, 12, 2, 59, 59, tzinfo=UTC)
    assert doc["notify_on"] == "2030-05-11"


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"due_at": "bad"},
        {"due_at": "2020-01-01"},
        {"notify_on": "2030-05-11"},
        {"notify_on": "invalid"},
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
    assert await send_morning(store, send) == 1
    send.assert_awaited_once()
    assert send.await_args.args[0] == 123
    assert "Dentista" in send.await_args.args[1]
    found = store.get("alice", doc["id"])
    assert found["telegram_sent_at"] and not found["voice_reminded_at"]
    assert await send_morning(store, send) == 0
    assert send.await_count == 1


async def test_failed_send_is_not_marked_and_can_retry(store):
    doc = add(store)
    failed = AsyncMock(side_effect=OSError("offline"))
    assert await send_morning(store, failed) == 0
    assert not store.get("alice", doc["id"])["telegram_sent_at"]
    assert await send_morning(store, AsyncMock()) == 1


async def test_no_early_delivery_or_broadcast(store, monkeypatch):
    add(store, owner="bob")
    send = AsyncMock()
    assert await send_morning(store, send) == 0
    add(store)
    monkeypatch.setattr(settings.reminders, "morning_at", "10:00")
    assert await send_morning(store, send) == 0
    send.assert_not_awaited()


async def test_expired_and_future_notice_not_delivered(store, monkeypatch):
    doc = add(store)
    add(store, due_at="2030-05-12")
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(hours=8))
    assert store.get("alice", doc["id"])["statuses"] == ["Vencido"]
    assert not store.eligible("telegram")
    send = AsyncMock()
    assert await send_morning(store, send) == 0
    send.assert_not_awaited()


async def test_voice_once_across_sessions_and_independent_of_telegram(store):
    doc = add(store)
    await send_morning(store, AsyncMock())
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
    await send_morning(store, AsyncMock())
    changed = store.update("alice", doc["id"], title="Novo nome")
    assert changed["telegram_sent_at"]
    changed = store.update("alice", doc["id"], due_at="2030-05-11")
    assert changed["notify_on"] == "2030-05-11"
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
    assert "Lembrete matinal salvo" in result.output
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


def test_rescheduling_keeps_advance_days(store):
    doc = add(store, due_at="2030-05-15", notify_on="2030-05-13")
    changed = store.update("alice", doc["id"], due_at="2030-05-20")
    assert changed["notify_on"] == "2030-05-18"
    explicit = store.update("alice", doc["id"], due_at="2030-05-22", notify_on="2030-05-21")
    assert explicit["notify_on"] == "2030-05-21"


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


async def test_job_retries_failed_delivery_without_repeating_success(store, monkeypatch):
    from brain.reminders import delivery

    add(store)
    send = AsyncMock(side_effect=[OSError("offline"), None])
    original = delivery.send_morning

    async def attempt(store):
        return await original(store, send)

    sleep = AsyncMock()
    monkeypatch.setattr(delivery, "send_morning", attempt)
    monkeypatch.setattr(delivery.asyncio, "sleep", sleep)
    await delivery.morning_job()
    assert send.await_count == 2
    assert [call.args[0] for call in sleep.await_args_list] == [60, 300]
    assert store.list("alice")[0]["telegram_sent_at"]


async def test_late_catchup_uses_evening_greeting(store, monkeypatch):
    add(store, due_at="2030-05-11", notify_on="2030-05-10")
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(hours=14))
    send = AsyncMock()
    await send_morning(store, send)
    assert send.await_args.args[1].startswith("Boa noite!")


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
        await send_morning(store, send)
    assert send.await_count == 1
    monkeypatch.setattr(store, "finish", original)
    monkeypatch.setattr(clock, "now", lambda *args: NOW + timedelta(minutes=11))
    await send_morning(store, send)
    assert send.await_count == 2
    assert store.get("alice", doc["id"])["telegram_sent_at"]


def test_legacy_ids_remain_editable_and_new_ids_survive_dashboard_redaction(store):
    from brain.dashboard.serialize import sanitize

    fresh = add(store)
    assert sanitize(fresh)["id"] == fresh["id"]
    raw = store.collection().find_one({"_id": fresh["id"]})
    old_id = "a1" * 16
    store.collection().insert_one({**raw, "_id": old_id})
    exposed = store.get("alice", old_id)["id"]
    assert exposed == "rem_" + old_id and sanitize({"id": exposed})["id"] == exposed
    assert store.update("alice", exposed, title="Migrado")["title"] == "Migrado"
    store.delete("alice", exposed)
    assert store.collection().find_one({"_id": old_id}) is None
