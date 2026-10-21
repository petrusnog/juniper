"""Daily log files: compress the old ones, delete the expired ones, and read them back.

The event log writes ``YYYY-MM-DD.jsonl`` (one file per day, shared by every session of that day).
``rotate()`` is the daily housekeeping job: files from days before today are gzipped, and files
older than ``retention_days`` are deleted. It is idempotent, so running it twice (or after a day
the machine was off) does the right thing.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from brain.config import settings
from brain.eventlog import event_log

_DAILY = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")


@dataclass
class RotationReport:
    compressed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    kept: int = 0


def today_local() -> date:
    return datetime.now(ZoneInfo(settings.agent.timezone)).date()


def daily_files(directory: Path) -> list[tuple[date, Path]]:
    """The dated log files in ``directory``, oldest first (both ``.jsonl`` and ``.jsonl.gz``)."""
    found: list[tuple[date, Path]] = []
    if not directory.is_dir():
        return found
    for path in directory.iterdir():
        match = _DAILY.match(path.name)
        if match and path.is_file():
            try:
                found.append((date.fromisoformat(match.group(1)), path))
            except ValueError:
                continue  # 2026-13-45.jsonl: not ours
    return sorted(found, key=lambda item: (item[0], item[1].name))


def rotate(
    directory: Path, today: date, retention_days: int, *, dry_run: bool = False
) -> RotationReport:
    """Delete files older than ``retention_days`` days; gzip every other file before today.

    With ``dry_run`` nothing is touched; the report lists what would happen.
    """
    report = RotationReport()
    for day, path in daily_files(directory):
        age = (today - day).days
        try:
            if age > retention_days:
                if not dry_run:
                    path.unlink()
                report.deleted.append(path.name)
            elif age >= 1 and not path.name.endswith(".gz"):
                report.compressed.append(path.name + ".gz" if dry_run else _compress(path).name)
            else:
                report.kept += 1
        except OSError as exc:
            report.errors.append(f"{path.name}: {exc}")
    return report


def _compress(source: Path) -> Path:
    """gzip ``source`` next to itself (atomically), then remove it. Appends if a .gz exists."""
    target = source.with_name(source.name + ".gz")
    partial = source.with_name(source.name + ".gz.tmp")
    partial.unlink(missing_ok=True)  # leftover of a run that died half-way
    try:
        if target.exists():  # a previous run (or a late write) left a member: keep it, add ours
            shutil.copyfile(target, partial)
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as raw, source.open("rb") as src:
            with gzip.GzipFile(fileobj=raw, mode="wb") as out:
                shutil.copyfileobj(src, out)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    source.unlink()
    return target


def run_rotation() -> RotationReport:
    """The daily job: rotate the configured log directory and record what happened."""
    cfg = settings.logging
    report = rotate(event_log.directory, today_local(), cfg.retention_days)
    event_log.log(
        "log_rotation",
        compressed=report.compressed,
        deleted=report.deleted,
        kept=report.kept,
        errors=report.errors,
        retention_days=cfg.retention_days,
    )
    return report


# --- reading ---------------------------------------------------------------------------------


def iter_records(directory: Path, days: set[date] | None = None) -> Iterator[dict[str, Any]]:
    """Yield the events of the dated files (oldest first), skipping lines that are not JSON."""
    for day, path in daily_files(directory):
        if days is not None and day not in days:
            continue
        opener = gzip.open if path.name.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    with contextlib.suppress(ValueError):
                        record = json.loads(line)
                        if isinstance(record, dict):
                            yield record
        except (OSError, EOFError):
            continue  # unreadable or half-written .gz: show the rest instead of failing
