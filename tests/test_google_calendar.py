"""Tests for brain.tools.google_calendar: tools and the thin Calendar client (no network)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httplib2
import pytest
from googleapiclient.errors import HttpError

from brain.config import settings
from brain.core import clock
from brain.tools import google_calendar as gc
from brain.tools.base import Locality, Tier
from brain.tools.google_calendar import (
    CalendarClient,
    CalendarError,
    CalendarEvent,
    CreateEvent,
    DeleteEvent,
    ListEvents,
    UpdateEvent,
)

SP = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 10, 11, 17, 30, tzinfo=SP)  # a Sunday
JUNIPER = "juniper@group.calendar.google.com"
MAIN = "me@gmail.com"


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SP)


def ev(
    event_id: str,
    title: str,
    start: datetime,
    end: datetime,
    cal: str = JUNIPER,
    all_day: bool = False,
) -> CalendarEvent:
    return CalendarEvent(event_id, title, start, end, cal, all_day)


class FakeClient(CalendarClient):
    """Real calendar-selection logic, fake Google: keeps events per calendar and records writes."""

    def __init__(
        self,
        events: list[CalendarEvent] | None = None,
        manage: list[str] | None = None,
        default: str | None = None,
    ) -> None:
        super().__init__(None, JUNIPER, [MAIN], manage or [], default)
        self.events = events or []
        self.inserted: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.deleted_from: list[str | None] = []
        self.delete_error: CalendarError | None = None

    async def list_events(self, cal: str, start: datetime, end: datetime) -> list[CalendarEvent]:
        return [e for e in self.events if e.calendar_id == cal and e.start < end and start < e.end]

    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent:
        for e in self.events:
            if e.id == event_id and e.calendar_id == calendar_id:
                return e
        raise CalendarError("não encontrado (id incorreto ou sem acesso)")

    async def insert_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        description: str,
        reminder_minutes: int,
        calendar_id: str | None = None,
    ) -> CalendarEvent:
        target = calendar_id or self.default_calendar_id
        self.inserted.append(
            {
                "title": title,
                "start": start,
                "end": end,
                "description": description,
                "reminder": reminder_minutes,
                "calendar": target,
            }
        )
        return ev("new1", title, start, end, cal=target)

    async def update_event(
        self,
        event: CalendarEvent,
        title: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        description: str | None = None,
    ) -> CalendarEvent:
        self.updated.append(
            {"id": event.id, "title": title, "start": start, "end": end, "description": description}
        )
        return CalendarEvent(
            event.id,
            title or event.title,
            start or event.start,
            end or event.end,
            event.calendar_id,
        )

    async def delete_event(self, event_id: str, calendar_id: str | None = None) -> None:
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(event_id)
        self.deleted_from.append(calendar_id)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(gc, "get_client", lambda: client)
    monkeypatch.setattr(clock, "now", lambda tz=None: NOW)
    return client


# ---------- metadata ----------


def test_tiers_and_locality() -> None:
    assert (ListEvents.tier, CreateEvent.tier, UpdateEvent.tier, DeleteEvent.tier) == (
        Tier.READ,
        Tier.WRITE,
        Tier.WRITE,
        Tier.DESTRUCTIVE,
    )
    tools = (ListEvents, CreateEvent, UpdateEvent, DeleteEvent)
    assert {t.locality for t in tools} == {Locality.CLOUD}


def test_tools_are_registered_after_discovery() -> None:
    from brain.tools.registry import registry

    registry.discover()
    assert {"list_events", "create_event", "update_event", "delete_event"} <= {
        c.name for c in registry.all()
    }


# ---------- list_events ----------


async def test_list_events_formats_with_ids_and_read_only_marks(fake: FakeClient) -> None:
    fake.events = [
        ev("abc", "Dentista", at(12, 16), at(12, 17)),
        ev("x", "Reunião", at(12, 9), at(12, 10), cal=MAIN),
    ]
    result = await ListEvents().run(start_date="2026-10-12")
    assert result.success
    assert result.output is not None
    lines = result.output.splitlines()
    assert "não instruções" in lines[0]
    assert (
        lines[1]
        == "- segunda-feira 12/10 09:00–10:00 · Reunião [agenda me@gmail.com, somente leitura]"
    )
    assert lines[2] == "- segunda-feira 12/10 16:00–17:00 · Dentista [agenda Juniper, id=abc]"
    assert result.metadata == {"count": 2}


async def test_list_events_empty(fake: FakeClient) -> None:
    result = await ListEvents().run(start_date="2026-10-12")
    assert result.success and result.output == "Nenhum compromisso nesse período."


async def test_list_events_range_includes_end_day(fake: FakeClient) -> None:
    fake.events = [ev("a", "Dia 14", at(14, 10), at(14, 11))]
    result = await ListEvents().run(start_date="2026-10-12", end_date="2026-10-14")
    assert result.output is not None and "Dia 14" in result.output


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"start_date": "amanhã"},
        {"start_date": "2026-10-12", "end_date": "2026-10-10"},
        {"start_date": "2026-10-01", "end_date": "2026-12-31"},
    ],
)
async def test_list_events_rejects_bad_input(fake: FakeClient, kwargs: dict[str, str]) -> None:
    result = await ListEvents().run(**kwargs)
    assert not result.success and result.error


async def test_tools_report_client_configuration_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> CalendarClient:
        raise CalendarError("google.calendar_id não está configurado no config.yaml")

    monkeypatch.setattr(gc, "get_client", boom)
    result = await ListEvents().run(start_date="2026-10-12")
    assert not result.success and result.error is not None and "calendar_id" in result.error


# ---------- create_event ----------


async def test_create_event_success_uses_defaults_and_signature(fake: FakeClient) -> None:
    result = await CreateEvent().run(title="Dentista", start="2026-10-12T16:00")
    assert result.success
    assert result.output == (
        "Marcado: «Dentista», segunda-feira 12/10 16:00–17:00 (agenda Juniper, id=new1)."
    )
    sent = fake.inserted[0]
    assert sent["start"] == at(12, 16) and sent["end"] == at(12, 17)  # default 60 min
    assert sent["description"] == "Agendado pela Juniper"
    assert sent["reminder"] == settings.google.reminder_minutes
    assert result.metadata == {"event_id": "new1"}


async def test_create_event_naive_time_means_configured_timezone(fake: FakeClient) -> None:
    await CreateEvent().run(title="X", start="2026-10-12T16:00")
    assert fake.inserted[0]["start"].utcoffset() == timedelta(hours=-3)


async def test_create_event_converts_explicit_offset_to_local(fake: FakeClient) -> None:
    await CreateEvent().run(title="X", start="2026-10-12T19:00:00+00:00")
    assert fake.inserted[0]["start"] == at(12, 16)


async def test_create_event_custom_duration_end_and_notes(fake: FakeClient) -> None:
    await CreateEvent().run(title="A", start="2026-10-12T10:00", duration_minutes=30)
    await CreateEvent().run(
        title="B", start="2026-10-12T12:00", end="2026-10-12T13:30", description="levar exames"
    )
    assert fake.inserted[0]["end"] == at(12, 10, 30)
    assert fake.inserted[1]["end"] == at(12, 13, 30)
    assert fake.inserted[1]["description"] == "levar exames\n\nAgendado pela Juniper"


@pytest.mark.parametrize("raw", ["30", " 30 ", 30.0, 30])
async def test_create_event_accepts_numeric_duration_sent_as_text(
    fake: FakeClient, raw: object
) -> None:
    result = await CreateEvent().run(title="X", start="2026-10-12T10:00", duration_minutes=raw)
    assert result.success
    assert fake.inserted[0]["end"] == at(12, 10, 30)


async def test_create_event_rejects_past(fake: FakeClient) -> None:
    result = await CreateEvent().run(title="X", start="2026-10-11T09:00")
    assert not result.success
    assert result.error is not None and "já passou" in result.error and "domingo" in result.error
    assert fake.inserted == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "", "start": "2026-10-12T16:00"},
        {"title": "X", "start": "2026-10-12"},
        {"title": "X", "start": "terça às 16"},
        {"title": "X", "start": "2026-10-12T16:00", "end": "2026-10-12T15:00"},
        {"title": "X", "start": "2026-10-12T16:00", "duration_minutes": 0},
        {"title": "X", "start": "2026-10-12T16:00", "duration_minutes": "muito"},
        {"title": "X", "start": "2026-10-12T16:00", "duration_minutes": True},
        {"title": "X", "start": "2026-10-12T16:00", "duration_minutes": -5},
    ],
)
async def test_create_event_rejects_bad_input(fake: FakeClient, kwargs: dict[str, Any]) -> None:
    result = await CreateEvent().run(**kwargs)
    assert not result.success and result.error
    assert fake.inserted == []


async def test_create_event_conflict_in_juniper_calendar_is_deletable(fake: FakeClient) -> None:
    fake.events = [ev("abc", "Fisioterapia", at(12, 16, 30), at(12, 17, 30))]
    result = await CreateEvent().run(title="Dentista", start="2026-10-12T16:00")
    assert not result.success
    assert fake.inserted == []
    assert result.error is not None
    assert "NÃO foi criado" in result.error and "«Fisioterapia»" in result.error
    assert "id=abc" in result.error
    assert result.metadata == {
        "conflicts": [{"id": "abc", "title": "Fisioterapia", "deletable": True}]
    }


async def test_create_event_conflict_in_other_calendar_is_not_deletable(fake: FakeClient) -> None:
    fake.events = [ev("m1", "Reunião", at(12, 15), at(12, 16, 30), cal=MAIN)]
    result = await CreateEvent().run(title="Dentista", start="2026-10-12T16:00")
    assert not result.success and fake.inserted == []
    assert result.error is not None and "somente leitura" in result.error
    assert result.metadata is not None and result.metadata["conflicts"][0]["deletable"] is False


async def test_create_event_adjacent_events_do_not_conflict(fake: FakeClient) -> None:
    fake.events = [
        ev("a", "Antes", at(12, 15), at(12, 16)),
        ev("b", "Depois", at(12, 17), at(12, 18)),
    ]
    result = await CreateEvent().run(title="Dentista", start="2026-10-12T16:00")
    assert result.success


async def test_create_event_all_day_events_do_not_block(fake: FakeClient) -> None:
    fake.events = [ev("h", "Feriado", at(12, 0), at(13, 0), cal=MAIN, all_day=True)]
    assert (await CreateEvent().run(title="X", start="2026-10-12T16:00")).success


async def test_create_event_truncates_long_title(fake: FakeClient) -> None:
    await CreateEvent().run(title="x" * 500, start="2026-10-12T16:00")
    assert len(fake.inserted[0]["title"]) == gc.MAX_TITLE


# ---------- delete_event ----------


async def test_delete_event_success(fake: FakeClient) -> None:
    fake.events = [ev("abc", "Dentista", at(12, 16), at(12, 17))]
    result = await DeleteEvent().run(event_id="abc")
    assert result.success and fake.deleted == ["abc"] and fake.deleted_from == [JUNIPER]
    assert result.output is not None and "«Dentista»" in result.output


async def test_delete_event_requires_id(fake: FakeClient) -> None:
    result = await DeleteEvent().run()
    assert not result.success and fake.deleted == []


async def test_delete_event_unknown_id_is_reported(fake: FakeClient) -> None:
    result = await DeleteEvent().run(event_id="nope")
    assert not result.success and fake.deleted == []
    assert result.error is not None and "não encontrei esse evento" in result.error


async def test_delete_event_api_failure_is_reported(fake: FakeClient) -> None:
    fake.events = [ev("abc", "Dentista", at(12, 16), at(12, 17))]
    fake.delete_error = CalendarError("Google recusou o acesso (HTTP 403)")
    result = await DeleteEvent().run(event_id="abc")
    assert not result.success and result.error is not None and "recusou" in result.error


async def test_delete_never_touches_read_only_calendars(fake: FakeClient) -> None:
    fake.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    result = await DeleteEvent().run(event_id="m1")
    assert not result.success and fake.deleted == []


# ---------- calendars handed over (ADR-010) ----------


@pytest.fixture
def managed(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    """MAIN is handed over: writable, and the default for new events."""
    client = FakeClient(manage=[MAIN], default=MAIN)
    monkeypatch.setattr(gc, "get_client", lambda: client)
    monkeypatch.setattr(clock, "now", lambda tz=None: NOW)
    return client


def test_client_calendar_selection() -> None:
    client = FakeClient(manage=[MAIN], default=MAIN)
    assert client.writable_ids == [JUNIPER, MAIN] and client.all_calendar_ids == [JUNIPER, MAIN]
    assert client.resolve_calendar(None) == MAIN
    assert client.resolve_calendar("padrão") == MAIN
    assert client.resolve_calendar("Juniper") == JUNIPER
    assert client.resolve_calendar("principal") == MAIN
    assert client.resolve_calendar("ME@gmail.com") == MAIN
    with pytest.raises(CalendarError, match="desconhecida ou somente leitura"):
        client.resolve_calendar("outra@gmail.com")


def test_without_manage_calendars_everything_stays_on_juniper() -> None:
    client = FakeClient()
    assert client.writable_ids == [JUNIPER] and client.default_calendar_id == JUNIPER
    with pytest.raises(CalendarError, match="posso usar: Juniper"):
        client.resolve_calendar("principal")  # nothing handed over: there is no such alias
    with pytest.raises(CalendarError):
        client.resolve_calendar(MAIN)  # read-only calendars are never writable


def test_default_calendar_must_be_writable() -> None:
    with pytest.raises(CalendarError, match="default_calendar_id"):
        FakeClient(default=MAIN)  # MAIN is only readable here


async def test_create_goes_to_the_default_or_the_requested_calendar(managed: FakeClient) -> None:
    await CreateEvent().run(title="Dentista", start="2026-10-12T16:00")
    await CreateEvent().run(title="Interno", start="2026-10-13T16:00", calendar="juniper")
    assert [i["calendar"] for i in managed.inserted] == [MAIN, JUNIPER]


async def test_create_rejects_a_read_only_calendar(fake: FakeClient) -> None:
    result = await CreateEvent().run(title="X", start="2026-10-12T16:00", calendar=MAIN)
    assert not result.success and fake.inserted == []
    assert result.error is not None and "somente leitura" in result.error


async def test_list_shows_ids_for_handed_over_calendars(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    result = await ListEvents().run(start_date="2026-10-12")
    assert result.output is not None and "[agenda me@gmail.com, id=m1]" in result.output


async def test_conflicts_in_handed_over_calendars_are_deletable(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 15), at(12, 16, 30), cal=MAIN)]
    result = await CreateEvent().run(title="Dentista", start="2026-10-12T16:00")
    assert not result.success and result.metadata is not None
    assert result.metadata["conflicts"] == [{"id": "m1", "title": "Reunião", "deletable": True}]
    assert result.error is not None and "id=m1" in result.error


async def test_delete_in_a_handed_over_calendar(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    result = await DeleteEvent().run(event_id="m1")
    assert result.success and managed.deleted == ["m1"] and managed.deleted_from == [MAIN]


async def test_describe_explains_the_call_in_words(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    assert await DeleteEvent().describe(event_id="m1") == (
        "apagar «Reunião», segunda-feira 12/10 09:00–10:00, da agenda me@gmail.com"
    )
    assert await DeleteEvent().describe(event_id="nope") is None
    created = await CreateEvent().describe(title="Dentista", start="2026-10-12T16:00")
    assert created == "marcar «Dentista», segunda-feira 12/10 16:00–17:00, na agenda me@gmail.com"
    assert await CreateEvent().describe(title="", start="2026-10-12T16:00") is None


# ---------- update_event ----------


async def test_update_title_only_keeps_the_time(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    result = await UpdateEvent().run(event_id="m1", title="Reunião de produto")
    assert result.success
    assert managed.updated == [
        {"id": "m1", "title": "Reunião de produto", "start": None, "end": None, "description": None}
    ]


async def test_update_start_keeps_the_duration(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10, 30), cal=MAIN)]
    result = await UpdateEvent().run(event_id="m1", start="2026-10-13T14:00")
    assert result.success
    assert managed.updated[0]["start"] == at(13, 14) and managed.updated[0]["end"] == at(13, 15, 30)


async def test_update_explicit_end(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    await UpdateEvent().run(event_id="m1", start="2026-10-12T18:00", end="2026-10-12T19:00")
    assert managed.updated[0]["end"] == at(12, 19)


async def test_update_conflict_ignores_the_event_itself(managed: FakeClient) -> None:
    managed.events = [
        ev("m1", "Reunião", at(12, 18), at(12, 19), cal=MAIN),
        ev("m2", "Dentista", at(12, 19), at(12, 20), cal=MAIN),
    ]
    # Moving m1 by 30 min overlaps only with itself and m2's start: m2 conflicts, m1 does not.
    result = await UpdateEvent().run(event_id="m1", start="2026-10-12T18:30")
    assert not result.success and managed.updated == []
    assert (
        result.error is not None
        and "«Dentista»" in result.error
        and "«Reunião»" not in result.error
    )
    # A pure shift that stays clear of everything else works.
    managed.events[1] = ev("m2", "Dentista", at(12, 21), at(12, 22), cal=MAIN)
    assert (await UpdateEvent().run(event_id="m1", start="2026-10-12T18:30")).success


async def test_update_description_gets_the_signature(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    await UpdateEvent().run(event_id="m1", description="sala 3")
    assert managed.updated[0]["description"] == f"sala 3\n\n{gc.SIGNATURE}"


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({}, "event_id"),
        ({"event_id": "m1"}, "diga o que mudar"),
        ({"event_id": "nope", "title": "x"}, "não encontrei esse evento"),
        ({"event_id": "m1", "start": "2026-10-01T10:00"}, "já passou"),
        ({"event_id": "m1", "start": "2026-10-12T18:00", "end": "2026-10-12T17:00"}, "fim"),
        ({"event_id": "m1", "title": "   "}, "vazio"),
        ({"event_id": "m1", "start": "amanhã"}, "ISO 8601"),
    ],
)
async def test_update_rejects_bad_input(
    managed: FakeClient, kwargs: dict[str, Any], fragment: str
) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    result = await UpdateEvent().run(**kwargs)
    assert not result.success and managed.updated == []
    assert result.error is not None and fragment in result.error


async def test_update_never_touches_read_only_calendars(fake: FakeClient) -> None:
    fake.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    result = await UpdateEvent().run(event_id="m1", title="x")
    assert not result.success and fake.updated == []


async def test_update_precheck_and_describe(managed: FakeClient) -> None:
    managed.events = [
        ev("m1", "Reunião", at(12, 18), at(12, 19), cal=MAIN),
        ev("m2", "Dentista", at(12, 19), at(12, 20), cal=MAIN),
    ]
    blocked = await UpdateEvent().precheck(event_id="m1", start="2026-10-12T18:30")
    assert blocked is not None and not blocked.success and managed.updated == []
    assert await UpdateEvent().precheck(event_id="m1", title="Novo") is None
    text = await UpdateEvent().describe(event_id="m1", title="Novo", start="2026-10-13T10:00")
    assert text == (
        "alterar «Reunião» (segunda-feira 12/10 18:00–19:00) para «Novo», "
        "passando a terça-feira 13/10 10:00–11:00, na agenda me@gmail.com"
    )


# ---------- CalendarClient against a fake Google service ----------


class FakeRequest:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error

    def execute(self) -> Any:
        if self.error:
            raise self.error
        return self.result


class FakeEvents:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_result: dict[str, Any] = {"items": []}
        self.insert_result: dict[str, Any] = {"id": "g1"}
        self.get_result: dict[str, Any] = {}
        self.error: Exception | None = None

    def list(self, **kw: Any) -> FakeRequest:
        self.calls.append(("list", kw))
        return FakeRequest(self.list_result, self.error)

    def insert(self, **kw: Any) -> FakeRequest:
        self.calls.append(("insert", kw))
        return FakeRequest(self.insert_result, self.error)

    def delete(self, **kw: Any) -> FakeRequest:
        self.calls.append(("delete", kw))
        return FakeRequest(None, self.error)

    def get(self, **kw: Any) -> FakeRequest:
        self.calls.append(("get", kw))
        return FakeRequest(self.get_result, self.error)

    def patch(self, **kw: Any) -> FakeRequest:
        self.calls.append(("patch", kw))
        return FakeRequest({"id": kw["eventId"]}, self.error)


class FakeService:
    def __init__(self) -> None:
        self._events = FakeEvents()

    def events(self) -> FakeEvents:
        return self._events


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


@pytest.fixture
def service() -> FakeService:
    return FakeService()


@pytest.fixture
def client(service: FakeService) -> CalendarClient:
    return CalendarClient(service, JUNIPER, [MAIN, JUNIPER])


def test_client_dedupes_calendars_keeping_write_calendar_first(client: CalendarClient) -> None:
    assert client.all_calendar_ids == [JUNIPER, MAIN]


async def test_client_list_parses_timed_all_day_cancelled_and_untitled(
    client: CalendarClient, service: FakeService
) -> None:
    service.events().list_result = {
        "items": [
            {
                "id": "1",
                "summary": "Dentista",
                "start": {"dateTime": "2026-10-12T19:00:00Z"},
                "end": {"dateTime": "2026-10-12T20:00:00Z"},
            },
            {"id": "2", "start": {"date": "2026-10-13"}, "end": {"date": "2026-10-14"}},
            {
                "id": "3",
                "status": "cancelled",
                "summary": "Cancelado",
                "start": {"dateTime": "2026-10-12T10:00:00-03:00"},
                "end": {"dateTime": "2026-10-12T11:00:00-03:00"},
            },
        ]
    }
    events = await client.list_events(MAIN, at(12, 0), at(14, 0))
    assert [e.id for e in events] == ["1", "2"]
    assert events[0].start == at(12, 16) and not events[0].all_day  # UTC converted to São Paulo
    assert events[1].title == "(sem título)" and events[1].all_day
    kind, kw = service.events().calls[0]
    assert kind == "list" and kw["calendarId"] == MAIN
    assert kw["singleEvents"] is True and kw["orderBy"] == "startTime"


async def test_client_insert_sends_timezone_reminder_and_writes_to_juniper(
    client: CalendarClient, service: FakeService
) -> None:
    event = await client.insert_event("Dentista", at(12, 16), at(12, 17), "desc", 20)
    kind, kw = service.events().calls[0]
    assert kind == "insert" and kw["calendarId"] == JUNIPER
    body = kw["body"]
    assert body["summary"] == "Dentista" and body["description"] == "desc"
    assert body["start"] == {
        "dateTime": "2026-10-12T16:00:00-03:00",
        "timeZone": "America/Sao_Paulo",
    }
    assert body["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 20}],
    }
    assert event.id == "g1" and event.calendar_id == JUNIPER


async def test_client_delete_defaults_to_the_juniper_calendar(
    client: CalendarClient, service: FakeService
) -> None:
    await client.delete_event("abc")
    assert service.events().calls == [("delete", {"calendarId": JUNIPER, "eventId": "abc"})]


async def test_client_delete_in_a_given_calendar(
    client: CalendarClient, service: FakeService
) -> None:
    await client.delete_event("abc", MAIN)
    assert service.events().calls == [("delete", {"calendarId": MAIN, "eventId": "abc"})]


async def test_client_insert_into_a_given_calendar(
    service: FakeService,
) -> None:
    client = CalendarClient(service, JUNIPER, [], [MAIN], MAIN)
    event = await client.insert_event("X", at(12, 16), at(12, 17), "d", 10)
    assert service.events().calls[0][1]["calendarId"] == MAIN and event.calendar_id == MAIN
    await client.insert_event("Y", at(12, 16), at(12, 17), "d", 10, JUNIPER)
    assert service.events().calls[1][1]["calendarId"] == JUNIPER


async def test_client_get_and_find_event(service: FakeService) -> None:
    client = CalendarClient(service, JUNIPER, [], [MAIN])
    service.events().get_result = {
        "id": "e1",
        "summary": "Reunião",
        "start": {"dateTime": "2026-10-12T09:00:00-03:00"},
        "end": {"dateTime": "2026-10-12T10:00:00-03:00"},
    }
    found = await client.find_event("e1")
    assert found is not None and found.title == "Reunião" and found.calendar_id == JUNIPER
    kind, kw = service.events().calls[0]
    assert kind == "get" and kw == {"calendarId": JUNIPER, "eventId": "e1"}

    service.events().error = http_error(404)
    assert await client.find_event("e1") is None  # not found anywhere: no exception


async def test_client_get_treats_cancelled_events_as_missing(service: FakeService) -> None:
    client = CalendarClient(service, JUNIPER, [], [])
    service.events().get_result = {
        "id": "e1",
        "status": "cancelled",
        "start": {"dateTime": "2026-10-12T09:00:00-03:00"},
        "end": {"dateTime": "2026-10-12T10:00:00-03:00"},
    }
    with pytest.raises(CalendarError, match="já foi apagado"):
        await client.get_event(JUNIPER, "e1")


async def test_client_update_patches_only_the_changed_fields(
    service: FakeService,
) -> None:
    client = CalendarClient(service, JUNIPER, [], [MAIN])
    event = ev("e1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)
    updated = await client.update_event(event, title="Novo")
    kind, kw = service.events().calls[0]
    assert kind == "patch" and kw["calendarId"] == MAIN and kw["eventId"] == "e1"
    assert kw["body"] == {"summary": "Novo"}  # time untouched
    assert updated.title == "Novo" and updated.start == event.start

    await client.update_event(event, start=at(13, 14), end=at(13, 15))
    body = service.events().calls[1][1]["body"]
    assert body["start"] == {
        "dateTime": "2026-10-13T14:00:00-03:00",
        "timeZone": "America/Sao_Paulo",
    }
    assert body["end"]["dateTime"] == "2026-10-13T15:00:00-03:00"


async def test_client_403_explains_the_permission(
    client: CalendarClient, service: FakeService
) -> None:
    service.events().error = http_error(403)
    with pytest.raises(CalendarError, match="fazer alterações"):
        await client.delete_event("abc", MAIN)


@pytest.mark.parametrize(
    ("status", "fragment"),
    [
        (404, "não encontrado"),
        (410, "não encontrado"),
        (403, "recusou"),
        (401, "recusou"),
        (500, "HTTP 500"),
    ],
)
async def test_client_maps_http_errors(
    client: CalendarClient, service: FakeService, status: int, fragment: str
) -> None:
    service.events().error = http_error(status)
    with pytest.raises(CalendarError, match=fragment):
        await client.delete_event("abc")


async def test_client_maps_connection_errors(client: CalendarClient, service: FakeService) -> None:
    service.events().error = ConnectionError("down")
    with pytest.raises(CalendarError, match="sem conexão"):
        await client.list_events(JUNIPER, at(12, 0), at(13, 0))


# ---------- get_client configuration errors ----------


@pytest.fixture
def no_cached_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gc, "_client", None)


def test_get_client_requires_calendar_id(
    no_cached_client: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.google, "calendar_id", None)
    with pytest.raises(CalendarError, match="calendar_id"):
        gc.get_client()


def test_get_client_requires_key_file(
    no_cached_client: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings.google, "calendar_id", JUNIPER)
    monkeypatch.setattr(settings.google, "credentials_file", tmp_path / "missing.json")
    with pytest.raises(CalendarError, match="não encontrada"):
        gc.get_client()


def test_get_client_rejects_garbage_key_without_leaking_content(
    no_cached_client: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "key.json"
    key.write_text('{"private_key": "SECRET-DO-NOT-LEAK"}')
    monkeypatch.setattr(settings.google, "calendar_id", JUNIPER)
    monkeypatch.setattr(settings.google, "credentials_file", key)
    with pytest.raises(CalendarError) as info:
        gc.get_client()
    assert "SECRET-DO-NOT-LEAK" not in str(info.value)
    assert "inválida" in str(info.value)


# ---------- precheck (runs before the [y/N]) and prompt budget ----------


async def test_precheck_reports_conflict_without_creating(fake: FakeClient) -> None:
    fake.events = [ev("abc", "Fisioterapia", at(12, 16, 30), at(12, 17, 30))]
    result = await CreateEvent().precheck(title="Dentista", start="2026-10-12T16:00")
    assert result is not None and not result.success
    assert result.error is not None and "«Fisioterapia»" in result.error
    assert fake.inserted == []


async def test_precheck_none_when_slot_is_free_and_does_not_create(fake: FakeClient) -> None:
    assert await CreateEvent().precheck(title="Dentista", start="2026-10-12T16:00") is None
    assert fake.inserted == []


async def test_precheck_reports_invalid_input(fake: FakeClient) -> None:
    result = await CreateEvent().precheck(title="X", start="2026-10-11T09:00")
    assert result is not None and result.error is not None and "já passou" in result.error


async def test_run_rechecks_conflicts_after_a_clean_precheck(fake: FakeClient) -> None:
    tool = CreateEvent()
    assert await tool.precheck(title="Dentista", start="2026-10-12T16:00") is None
    fake.events = [ev("late", "Chegou depois", at(12, 16), at(12, 17))]  # calendar changed
    result = await tool.run(title="Dentista", start="2026-10-12T16:00")
    assert not result.success and fake.inserted == []


async def test_other_tools_have_no_precheck() -> None:
    assert await ListEvents().precheck(start_date="2026-10-12") is None
    assert await DeleteEvent().precheck(event_id="x") is None  # the confirmation shows describe()


def test_tool_schemas_stay_small() -> None:
    """The schemas travel in every LLM call; keep them lean (Groq's free tier is ~8k tokens/min)."""
    import json

    from brain.tools.registry import registry

    registry.discover()
    calendar = [
        c.to_schema()
        for c in registry.all()
        if c.name in {"list_events", "create_event", "update_event", "delete_event"}
    ]
    assert len(json.dumps(calendar, ensure_ascii=False)) < 2700  # ≈ 700 tokens


# ---------- subjects (approval recognised in the conversation) ----------


async def test_subjects_name_what_each_tool_acts_on(managed: FakeClient) -> None:
    managed.events = [ev("m1", "Reunião", at(12, 9), at(12, 10), cal=MAIN)]
    assert await DeleteEvent().subjects(event_id="m1") == ["Reunião"]
    assert await UpdateEvent().subjects(event_id="m1", title="Novo") == ["Reunião"]
    assert await CreateEvent().subjects(title="  Dentista ", start="2026-10-12T16:00") == [
        "Dentista"
    ]
    assert await DeleteEvent().subjects(event_id="nope") is None  # unknown id: cannot be vouched
    assert await CreateEvent().subjects(title="", start="x") is None
    assert await ListEvents().subjects(start_date="2026-10-12") is None
