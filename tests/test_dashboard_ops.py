"""Logs, custos e saúde no dashboard (ADR-028): leituras, sem escrita."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from brain.config import settings
from brain.dashboard import ops
from brain.dashboard.api import Api
from brain.dashboard.routes import build_api
from brain.dashboard.sources import Sources

DAY = date(2026, 10, 30)


def write_log(directory: Path, records: list[dict[str, Any]], day: date = DAY) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


RECORDS = [
    {"ts": "2026-10-30T10:00:00.100-03:00", "event": "turn_start", "session": "s1"},
    {
        "ts": "2026-10-30T10:00:01.000-03:00",
        "event": "failover",
        "provider": "deepinfra",
        "type": "LLMRateLimitError",
        "message": "limite",
        "session": "s1",
    },
    {
        "ts": "2026-10-30T10:00:02.000-03:00",
        "event": "tool_call",
        "name": "forget",
        "success": False,
        "session": "s2",
    },
    {
        "ts": "2026-10-30T10:00:03.000-03:00",
        "event": "note",
        "text": "chave gsk_abcdefghijklmnop1234",
    },
]


def test_log_rows_are_newest_first_filterable_and_typed(tmp_path: Path) -> None:
    write_log(tmp_path, RECORDS)
    everything = ops.log_rows(tmp_path, today=DAY)
    assert [r["event"] for r in everything["items"]] == [
        "note",
        "tool_call",
        "failover",
        "turn_start",
    ]
    assert everything["items"][1]["level"] == "error" and everything["items"][3]["level"] == "info"

    errors = ops.log_rows(tmp_path, errors_only=True, today=DAY)
    assert {r["event"] for r in errors["items"]} == {"failover", "tool_call"}
    assert [r["session"] for r in ops.log_rows(tmp_path, session="s2", today=DAY)["items"]] == [
        "s2"
    ]
    assert len(ops.log_rows(tmp_path, limit=2, today=DAY)["items"]) == 2
    assert ops.log_rows(tmp_path, text="LIMITE", today=DAY)["items"][0]["event"] == "failover"
    assert ops.log_rows(tmp_path, events=["tool"], today=DAY)["items"][0]["event"] == "tool_call"


def test_the_window_is_the_last_days_and_a_missing_directory_is_empty(tmp_path: Path) -> None:
    write_log(tmp_path, RECORDS, date(2026, 10, 25))
    assert ops.log_rows(tmp_path, days=1, today=DAY)["items"] == []
    assert len(ops.log_rows(tmp_path, days=7, today=DAY)["items"]) == 4
    assert ops.log_rows(tmp_path / "nada", today=DAY)["items"] == []


def test_failovers_and_errors_are_counted(tmp_path: Path) -> None:
    write_log(tmp_path, RECORDS)
    summary = ops.failovers(tmp_path, days=7, today=DAY)
    assert summary["failovers"] == {"deepinfra": 1}
    assert summary["errors_per_day"] == {"2026-10-30": 2}


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Api:
    write_log(tmp_path / "logs", [{**r, "ts": r["ts"]} for r in RECORDS], date.today())
    monkeypatch.setattr(settings.voice.tts, "monthly_chars_limit", 1000)
    monkeypatch.setattr(settings.voice, "usage_file", tmp_path / "voice.json")
    monkeypatch.setattr(settings.music, "quota_file", tmp_path / "quota.json")
    sources = Sources(health=lambda: {"status": "degraded", "memory": "warming_up"})
    sources.log_dir = tmp_path / "logs"
    return build_api(sources)


async def test_the_routes_answer_and_mask_secrets(api: Api) -> None:
    status, logs = await api.handle("/logs", {})
    assert status == 200
    text = json.dumps(logs)
    assert "gsk_abcdefghijklmnop1234" not in text

    status, costs = await api.handle("/costs", {})
    assert status == 200 and costs["voice"]["limit"] == 1000 and costs["voice"]["used"] == 0
    assert costs["music"]["used"] == 0 and costs["dreams"]["runs"] == []

    status, health = await api.handle("/health", {})
    assert status == 200 and health["status"] == "degraded" and health["memory"] == "warming_up"
    assert health["layers"]["memory_v2"] is False and "version" in health
    assert any(t["name"] == "report_issue" for t in health["tools"]) or health["tools"] == []


async def test_the_filters_reach_the_log(api: Api) -> None:
    _, errors = await api.handle("/logs", {"errors": ["1"], "limit": ["1"]})
    assert len(errors["items"]) == 1 and errors["items"][0]["level"] == "error"
    status, _ = await asyncio.wait_for(api.handle("/logs", {"days": ["99"]}), 3)
    assert status == 200  # an out-of-range window is clamped, not an error
