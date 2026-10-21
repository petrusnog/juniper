"""Read and render the event log for ``juniper logs``: filters, one-line summaries, follow mode."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from brain.logfiles import daily_files, iter_records

# Events that mean something went wrong (for ``--errors``).
_PROBLEMS = {"error", "failover", "http_error"}


def matches(
    record: dict[str, Any],
    *,
    session: str | None = None,
    run: str | None = None,
    events: Iterable[str] = (),
    errors_only: bool = False,
) -> bool:
    """Whether ``record`` passes the filters. ``events`` match by name or prefix (``http``)."""
    if session and session not in str(record.get("session", "")):
        return False
    if run and record.get("run") != run:
        return False
    name = str(record.get("event", ""))
    wanted = [e for e in events if e]
    if wanted and not any(name == e or name.startswith(e) for e in wanted):
        return False
    if errors_only:
        status = record.get("status")
        failed_http = name == "http_response" and isinstance(status, int) and status >= 400
        failed_tool = name == "tool_call" and record.get("success") is False
        if name not in _PROBLEMS and not failed_http and not failed_tool:
            return False
    return True


def days_back(today: date, days: int) -> set[date]:
    return {today - timedelta(days=i) for i in range(days)}


def select(
    directory: Path,
    days: set[date] | None,
    predicate: Callable[[dict[str, Any]], bool],
    limit: int,
) -> list[dict[str, Any]]:
    """The last ``limit`` matching records (all of them if ``limit`` is 0), oldest first."""
    chosen = [r for r in iter_records(directory, days) if predicate(r)]
    return chosen[-limit:] if limit > 0 else chosen


# --- rendering -------------------------------------------------------------------------------


def _short(value: Any, width: int = 90) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _pairs(record: dict[str, Any], skip: set[str], width: int = 70) -> str:
    return "  ".join(f"{k}={_short(v, width)}" for k, v in record.items() if k not in skip)


_BASE = {"ts", "run", "event", "session"}


def summary(record: dict[str, Any]) -> str:
    """One technical line for the event (without timestamp and session)."""
    event = str(record.get("event", "?"))
    if event == "http_request":
        size = len(json.dumps(record.get("body"), ensure_ascii=False)) if record.get("body") else 0
        return (
            f"→ {record.get('method')} {record.get('url')}  [{record.get('service')}] "
            f"req={record.get('req')}  body={size}B"
        )
    if event == "http_response":
        return (
            f"← {record.get('status')} [{record.get('service')}] req={record.get('req')}  "
            f"{record.get('total_ms')}ms (1º byte {record.get('ttfb_ms')}ms)  "
            f"{record.get('body_bytes')}B"
        )
    if event == "http_error":
        return (
            f"✗ [{record.get('service')}] req={record.get('req')}  {record.get('total_ms')}ms  "
            f"{record.get('type')}: {_short(record.get('message'))}"
        )
    return _pairs(record, _BASE)


def style(record: dict[str, Any]) -> str:
    """A rich style name for the event (red for problems, dim for transport detail)."""
    event = str(record.get("event", ""))
    status = record.get("status")
    if event in _PROBLEMS or (isinstance(status, int) and status >= 400):
        return "red"
    if event == "tool_call" and record.get("success") is False:
        return "red"
    if event.startswith("http_"):
        return "cyan"
    if event in ("turn_start", "server", "connection", "log_rotation"):
        return "green"
    return ""


def clock(record: dict[str, Any]) -> str:
    """``HH:MM:SS.mmm`` out of the ISO timestamp."""
    ts = str(record.get("ts", ""))
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M:%S.%f")[:-3]
    except ValueError:
        return ts[:12]


def details(record: dict[str, Any]) -> str:
    """The full record (headers, bodies, ...) as indented JSON, for ``--verbose``."""
    extra = {k: v for k, v in record.items() if k not in _BASE}
    return json.dumps(extra, ensure_ascii=False, indent=2, default=str)


# --- follow ----------------------------------------------------------------------------------


def follow(
    directory: Path,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    today: Callable[[], date],
    poll_seconds: float = 0.5,
    stop: Callable[[], bool] = lambda: False,
) -> Iterator[dict[str, Any]]:
    """Yield new matching records of today's file as they are appended (``tail -f``)."""
    current: date | None = None
    handle = None
    try:
        while not stop():
            day = today()
            if day != current:
                if handle:
                    handle.close()
                path = directory / f"{day:%Y-%m-%d}.jsonl"
                handle = (
                    path.open("r", encoding="utf-8", errors="replace") if path.exists() else None
                )
                if handle:
                    handle.seek(0, 2)  # start at the end: only what happens from now on
                current = day
            if handle is None:
                path = directory / f"{day:%Y-%m-%d}.jsonl"
                if path.exists():
                    handle = path.open("r", encoding="utf-8", errors="replace")
            line = handle.readline() if handle else ""
            if not line:
                time.sleep(poll_seconds)
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and predicate(record):
                yield record
    finally:
        if handle:
            handle.close()


def available_days(directory: Path) -> list[date]:
    return sorted({day for day, _ in daily_files(directory)})
