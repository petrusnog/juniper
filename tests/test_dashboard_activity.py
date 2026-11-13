"""Dashboard: painel de Auditoria, filtros em UTC-3 e exportação (T-222, T-223, T-224)."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import mongomock
import openpyxl
import pytest

from brain.activity import catalog, model
from brain.dashboard import activity as panel
from brain.dashboard.api import Api, ApiError
from brain.dashboard.routes import build_api
from brain.dashboard.sources import Sources
from brain.memory.activity import ActivityStore
from brain.memory.mongo import MongoConnection

SECRET = "gsk_abcdefghijklmnopqrstuvwxyz"
BASE = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)  # 14:00 in UTC-3


def make_store() -> ActivityStore:
    conn = MongoConnection()
    conn._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return ActivityStore(conn)


def add(
    store: ActivityStore, event: str, fields: dict[str, Any], minutes: int, **extra: Any
) -> str:
    built = catalog.translate(event, fields)
    assert built is not None
    built.facts.update({k: v for k, v in extra.items() if k in ("provider", "model")})
    record = model.build(
        built,
        fields,
        event=event,
        now=BASE + timedelta(minutes=minutes),
        session=extra.get("session", "s1"),
        turn=extra.get("turn", ""),
        channel=extra.get("channel", "app"),
    )
    store.add(record)
    return str(record["_id"])


@pytest.fixture
def api() -> Api:
    store = make_store()
    add(
        store,
        "tool_call",
        {"name": "music_search", "args": {"query": "Evidências"}, "success": True},
        0,
        provider="groq",
        model="llama",
        turn="t-1",
    )
    add(
        store,
        "tool_call",
        {"name": "open_url", "args": {"url": "https://exemplo.com"}, "success": True},
        5,
        provider="groq",
        model="llama",
        turn="t-1",
    )
    add(
        store,
        "tool_call",
        {
            "name": "web_fetch",
            "args": {"url": "https://x.dev"},
            "success": False,
            "error": f"falhou com {SECRET}",
        },
        600,
        provider="deepinfra",
        model="qwen",
    )
    return build_api(Sources(activity=store))


async def ask(api: Api, path: str, **query: Any) -> tuple[int, Any]:
    return await api.handle(path, {k: [str(v)] for k, v in query.items()})


# --- filters ------------------------------------------------------------------------------------


def test_dates_are_read_in_the_agent_time_zone_not_in_utc() -> None:
    since = panel._parse_local("2026-11-13")
    assert since == datetime(2026, 11, 13, 3, 0, tzinfo=UTC)  # midnight of Brasília
    until = panel._parse_local("2026-11-13", end=True)
    assert until == datetime(2026, 11, 14, 3, 0, tzinfo=UTC)  # the whole last day is included
    assert panel._parse_local("2026-11-13T14:30") == datetime(2026, 11, 13, 17, 30, tzinfo=UTC)
    assert panel.offset_label(BASE) == "UTC-3"


def test_period_shortcuts_follow_local_days() -> None:
    now = datetime(2026, 11, 13, 2, 0, tzinfo=UTC)  # still 12 Nov 23:00 in Brasília
    today, none = panel.period_bounds("today", now)
    assert today == datetime(2026, 11, 12, 3, 0, tzinfo=UTC) and none is None
    since, until = panel.period_bounds("yesterday", now)
    assert (until - since) == timedelta(days=1)
    assert panel.period_bounds("1h", now)[0] == now - timedelta(hours=1)
    with pytest.raises(ApiError):
        panel.period_bounds("sempre", now)


def test_bad_filters_are_a_400_not_a_crash() -> None:
    with pytest.raises(ApiError) as bad:
        panel.filters_from({"from": ["ontem de manhã"]})
    assert bad.value.status == 400
    with pytest.raises(ApiError):
        panel.filters_from({"from": ["2026-11-13"], "to": ["2026-11-12"]})


def test_multi_values_accept_commas_and_repeats() -> None:
    filters = panel.filters_from({"outcome": ["failed,pending", "denied"], "q": [" dólar "]})
    assert filters.outcome == ["failed", "pending", "denied"] and filters.q == "dólar"


def test_the_clock_text_always_carries_the_offset() -> None:
    assert panel.clock(BASE) == "13/11/2026 14:00:00 (UTC-3)"


# --- routes -------------------------------------------------------------------------------------


async def test_list_returns_friendly_text_clock_facets_and_total(api: Api) -> None:
    status, body = await ask(api, "/activity", period="")
    assert status == 200 and body["total"] == 3 and body["utc"] == "UTC-3"
    newest = body["items"][0]
    assert newest["clock"].endswith("(UTC-3)") and newest["ts_local"].endswith("-03:00")
    assert "raw" not in newest  # the technical event is in the detail
    assert dict(body["facets"]["outcome"]) == {"ok": 2, "failed": 1}


async def test_list_filters_by_term_model_outcome_and_date(api: Api) -> None:
    assert (await ask(api, "/activity", period="", q="evidencias"))[1]["total"] == 1
    assert (await ask(api, "/activity", period="", model="qwen"))[1]["total"] == 1
    assert (await ask(api, "/activity", period="", outcome="failed"))[1]["total"] == 1
    day = (await ask(api, "/activity", **{"from": "2026-11-13T14:00", "to": "2026-11-13T14:10"}))[1]
    assert day["total"] == 2  # 14:00 and 14:05 (UTC-3); the 00:00+600 min one is out


async def test_turn_filter_gathers_the_steps_of_one_message(api: Api) -> None:
    assert (await ask(api, "/activity", period="", turn="t-1"))[1]["total"] == 2


async def test_language_switches_the_sentence_without_touching_the_record(api: Api) -> None:
    pt = (await ask(api, "/activity", period="", q="evidencias"))[1]["items"][0]
    en = (await ask(api, "/activity", period="", q="evidencias", lang="en"))[1]["items"][0]
    assert pt["summary"] == "Pesquisei «Evidências» no YouTube Music"
    assert en["summary"] == "Searched Evidências on YouTube Music"
    assert pt["msg_key"] == en["msg_key"]


async def test_detail_has_the_raw_event_the_steps_and_masks_secrets(api: Api) -> None:
    items = (await ask(api, "/activity", period=""))[1]["items"]
    failed = next(i for i in items if i["outcome"] == "failed")
    status, body = await ask(api, f"/activity/{failed['id']}")
    assert status == 200 and body["raw"]["name"] == "web_fetch"
    assert SECRET not in str(body)
    ok = next(i for i in items if i["turn"] == "t-1")
    _, with_steps = await ask(api, f"/activity/{ok['id']}")
    assert len(with_steps["steps"]) == 2
    assert (await ask(api, "/activity/ffffffffffffffff"))[0] == 404


async def test_summary_counts_and_buckets_in_utc_minus_3(api: Api) -> None:
    status, body = await ask(api, "/activity/summary", period="")
    assert status == 200 and (body["total"], body["failed"]) == (3, 1)
    assert body["timeline"][0]["at"] == "2026-11-13T14:00"


async def test_without_mongodb_the_panel_says_why() -> None:
    status, body = await build_api(Sources()).handle("/activity", {})
    assert status == 503 and "MongoDB" in body["error"]


async def test_export_is_a_spreadsheet_with_friendly_and_technical_columns(api: Api) -> None:
    status, payload = await api.handle_file("/activity/export.xlsx", {"period": [""]})
    assert status == 200
    name, mime, data = payload
    assert (
        name.startswith("atividade-juniper-") and name.endswith(".xlsx") and "spreadsheet" in mime
    )
    sheet = openpyxl.load_workbook(io.BytesIO(data)).active
    header = [c.value for c in sheet[1]]
    assert header[:4] == [
        "Registro",
        "Data e hora (UTC-3)",
        "Data e hora (ISO)",
        "O que a Juniper fez",
    ]
    assert "Log técnico" in header
    rows = [[c.value for c in r] for r in sheet.iter_rows(min_row=2)]
    assert len(rows) == 3 and rows[0][1].endswith("(UTC-3)")  # oldest first
    assert rows[0][3] == "Pesquisei «Evidências» no YouTube Music"
    assert SECRET not in str(rows)
    assert not any(
        str(c.value).startswith("=") for r in sheet.iter_rows() for c in r
    )  # no formulas


async def test_export_filters_like_the_screen(api: Api) -> None:
    _, (_, _, data) = await api.handle_file(
        "/activity/export.xlsx", {"period": [""], "outcome": ["failed"]}
    )
    sheet = openpyxl.load_workbook(io.BytesIO(data)).active
    assert sheet.max_row == 2


def test_from_wins_over_a_period_and_a_timed_end_is_exclusive() -> None:
    filters = panel.filters_from({"period": ["today"], "from": ["2026-11-01"]})
    assert filters.since == datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    timed = panel.filters_from({"from": ["2026-11-13T10:00"], "to": ["2026-11-13T14:30"]})
    assert timed.until == datetime(2026, 11, 13, 17, 30, tzinfo=UTC)  # 14:30 itself is out


async def test_the_summary_tells_how_the_recorder_is_doing(api: Api) -> None:
    body = (await ask(api, "/activity/summary", period=""))[1]
    assert body["recorder"] == {"dropped": 0, "failed": 0} or set(body["recorder"]) == {
        "dropped",
        "failed",
    }
