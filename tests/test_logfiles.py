"""Daily log housekeeping (compress, delete), reading back, scheduling and `juniper logs`."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from brain import logview
from brain.clients.cli import app
from brain.eventlog import bind_session, event_log
from brain.logfiles import daily_files, iter_records, rotate, run_rotation
from brain.scheduler import run_daily, seconds_until

TODAY = date(2026, 10, 21)


def _write(directory: Path, day: str, *events: dict[str, object], gz: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(e) + "\n" for e in events)
    path = directory / (f"{day}.jsonl.gz" if gz else f"{day}.jsonl")
    if gz:
        path.write_bytes(gzip.compress(text.encode()))
    else:
        path.write_text(text)
    return path


def test_rotation_compresses_yesterday_keeps_today_and_deletes_the_expired(tmp_path: Path) -> None:
    for day in ("2026-10-21", "2026-10-20", "2026-10-19", "2026-10-18", "2026-10-17", "2026-10-10"):
        _write(tmp_path, day, {"event": "x", "day": day})

    report = rotate(tmp_path, TODAY, retention_days=3)

    names = sorted(p.name for _, p in daily_files(tmp_path))
    assert names == [
        "2026-10-18.jsonl.gz",  # 3 days old: still kept (only older than 3 days goes)
        "2026-10-19.jsonl.gz",
        "2026-10-20.jsonl.gz",
        "2026-10-21.jsonl",  # today is never touched
    ]
    assert sorted(report.deleted) == ["2026-10-10.jsonl", "2026-10-17.jsonl"]
    assert len(report.compressed) == 3 and not report.errors
    with gzip.open(tmp_path / "2026-10-20.jsonl.gz", "rt") as handle:
        assert json.loads(handle.readline())["day"] == "2026-10-20"


def test_rotation_is_idempotent_and_ignores_foreign_files(tmp_path: Path) -> None:
    _write(tmp_path, "2026-10-20", {"event": "x"})
    (tmp_path / "message.log").write_text("diário")
    (tmp_path / "notes.jsonl").write_text("{}")
    (tmp_path / "2026-13-45.jsonl").write_text("{}")

    rotate(tmp_path, TODAY, 3)
    again = rotate(tmp_path, TODAY, 3)

    assert again.compressed == [] and again.deleted == []
    assert (tmp_path / "message.log").exists() and (tmp_path / "notes.jsonl").exists()
    assert (tmp_path / "2026-13-45.jsonl").exists()


def test_a_late_write_after_compression_is_merged_not_lost(tmp_path: Path) -> None:
    _write(tmp_path, "2026-10-20", {"event": "first"}, gz=True)
    _write(tmp_path, "2026-10-20", {"event": "late"})

    rotate(tmp_path, TODAY, 3)

    events = [r["event"] for r in iter_records(tmp_path)]
    assert events == ["first", "late"]
    assert not list(tmp_path.glob("*.tmp")) and not (tmp_path / "2026-10-20.jsonl").exists()


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "2026-10-20", {"event": "x"})
    _write(tmp_path, "2026-10-01", {"event": "old"})

    report = rotate(tmp_path, TODAY, 3, dry_run=True)

    assert report.deleted == ["2026-10-01.jsonl"] and report.compressed == ["2026-10-20.jsonl.gz"]
    assert sorted(p.name for _, p in daily_files(tmp_path)) == [
        "2026-10-01.jsonl",
        "2026-10-20.jsonl",
    ]


def test_iter_records_reads_plain_and_gzip_and_skips_garbage(tmp_path: Path) -> None:
    _write(tmp_path, "2026-10-19", {"event": "a"}, gz=True)
    path = _write(tmp_path, "2026-10-21", {"event": "b"})
    path.write_text(path.read_text() + "not json\n[1,2]\n")

    assert [r["event"] for r in iter_records(tmp_path)] == ["a", "b"]
    assert [r["event"] for r in iter_records(tmp_path, {date(2026, 10, 21)})] == ["b"]


def test_run_rotation_records_what_it_did() -> None:
    _write(event_log.directory, "2000-01-01", {"event": "ancient"})

    report = run_rotation()

    assert report.deleted == ["2000-01-01.jsonl"]
    logged = [r for r in iter_records(event_log.directory) if r["event"] == "log_rotation"]
    assert logged[-1]["deleted"] == ["2000-01-01.jsonl"]


def test_seconds_until_picks_today_or_tomorrow() -> None:
    before = datetime(2026, 10, 21, 0, 0, 0)
    after = datetime(2026, 10, 21, 0, 30, 0)
    assert seconds_until("00:10", before) == 600
    assert seconds_until("00:10", after) == 24 * 3600 - 20 * 60


async def test_run_daily_catches_up_at_start_and_survives_a_failing_job() -> None:
    import asyncio

    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        raise RuntimeError("disk gone")

    task = asyncio.create_task(run_daily("log_rotation", "00:10", job))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [1]
    errors = [r for r in iter_records(event_log.directory) if r["event"] == "error"]
    assert errors and errors[0]["where"] == "scheduler.log_rotation"


def test_rotate_at_must_be_hh_mm() -> None:
    from brain.config import LoggingSettings

    assert LoggingSettings(rotate_at="3:5").rotate_at == "03:05"
    with pytest.raises(ValueError):
        LoggingSettings(rotate_at="25:00")
    with pytest.raises(ValueError):
        LoggingSettings(rotate_at="meia-noite")


# --- the event log keeps every session in the one daily file --------------------------------


def test_sessions_share_the_daily_file_and_are_tagged() -> None:
    bind_session("sess-A")
    event_log.log("turn_start", input_chars=3)
    bind_session("sess-B")
    event_log.log("turn_start", input_chars=5)

    files = list(event_log.directory.glob("*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [r["session"] for r in records] == ["sess-A", "sess-B"]


# --- juniper logs ---------------------------------------------------------------------------

NOW = datetime.now().astimezone()


def _record(event: str, **fields: object) -> dict[str, object]:
    return {"ts": NOW.isoformat(timespec="milliseconds"), "run": "r1", "event": event, **fields}


def test_matches_filters_by_session_event_prefix_and_failures() -> None:
    ok = _record("http_response", session="abc-123", status=200)
    bad = _record("http_response", session="abc-123", status=503)
    other = _record("tool_call", session="zzz", success=False)
    assert logview.matches(ok, session="abc")
    assert not logview.matches(other, session="abc")
    assert logview.matches(ok, events=["http"]) and not logview.matches(ok, events=["tool"])
    assert logview.matches(bad, errors_only=True) and not logview.matches(ok, errors_only=True)
    assert logview.matches(other, errors_only=True)


def test_summary_describes_requests_and_responses_in_one_line() -> None:
    request = _record(
        "http_request",
        req="q1",
        service="deepinfra",
        method="POST",
        url="https://x/y",
        body={"a": 1},
    )
    reply = _record(
        "http_response",
        req="q1",
        service="deepinfra",
        status=200,
        total_ms=812,
        ttfb_ms=400,
        body_bytes=99,
    )
    assert "POST https://x/y" in logview.summary(request) and "[deepinfra]" in logview.summary(
        request
    )
    assert "200" in logview.summary(reply) and "812ms" in logview.summary(reply)
    assert logview.style(_record("http_response", status=500)) == "red"


def test_cli_logs_shows_filters_and_rotates() -> None:
    directory = event_log.directory
    today = datetime.now().astimezone().date().isoformat()
    _write(
        directory,
        today,
        _record("turn_start", session="s1", input_chars=4),
        _record(
            "http_request",
            session="s1",
            req="q",
            service="groq",
            method="POST",
            url="https://api/x",
            body={"k": "v"},
        ),
        _record(
            "http_response",
            session="s2",
            req="r",
            service="groq",
            status=429,
            total_ms=5,
            ttfb_ms=5,
            body_bytes=2,
        ),
    )
    _write(directory, "2000-01-01", _record("turn_start"))
    runner = CliRunner()

    shown = runner.invoke(app, ["logs", "--date", today])
    assert shown.exit_code == 0, shown.output
    assert "POST https://api/x" in shown.output and "429" in shown.output

    only = runner.invoke(app, ["logs", "--date", today, "--session", "s1", "--event", "http"])
    assert "POST https://api/x" in only.output and "429" not in only.output

    failures = runner.invoke(app, ["logs", "--date", today, "--errors", "--json"])
    assert json.loads(failures.output.strip().splitlines()[-1])["status"] == 429

    verbose = runner.invoke(app, ["logs", "--date", today, "-e", "http_request", "-v"])
    assert '"k": "v"' in verbose.output

    assert runner.invoke(app, ["logs", "--date", "ontem"]).exit_code == 2

    dry = runner.invoke(app, ["logs", "rotate", "--dry-run"])
    assert "2000-01-01.jsonl" in dry.output and (directory / "2000-01-01.jsonl").exists()
    real = runner.invoke(app, ["logs", "rotate"])
    assert real.exit_code == 0 and not (directory / "2000-01-01.jsonl").exists()
    assert "KB" in runner.invoke(app, ["logs", "ls"]).output
