"""What the dashboard shows about the running server: logs, costs and health (ADR-028).

Read only. Every function takes what it needs and returns plain data; the routes call them and the
dashboard's single chokepoint (``sanitize``) masks secrets on the way out. The log rows reuse the
renderers of ``juniper logs`` so the screen and the terminal say the same thing.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from brain import __version__
from brain import logview as lv
from brain.config import settings
from brain.dashboard.sources import Sources
from brain.logfiles import daily_files, iter_records

MAX_LOG_ROWS = 500
SUMMARY_WIDTH = 220
_LEVEL = {"red": "error", "cyan": "http", "green": "info", "": ""}


def _today() -> date:
    return date.today()


def log_rows(
    directory: Path,
    *,
    days: int = 1,
    events: list[str] | None = None,
    errors_only: bool = False,
    session: str | None = None,
    text: str | None = None,
    limit: int = 200,
    today: date | None = None,
) -> dict[str, Any]:
    """The last ``limit`` matching events of the last ``days`` days, newest first."""
    wanted = (text or "").casefold().strip()

    def keep(record: dict[str, Any]) -> bool:
        if not lv.matches(record, session=session, events=events or (), errors_only=errors_only):
            return False
        return not wanted or wanted in lv.summary(record).casefold()

    window = lv.days_back(today or _today(), max(1, days))
    chosen = lv.select(directory, window, keep, max(1, min(limit, MAX_LOG_ROWS)))
    rows = [
        {
            "ts": r.get("ts"),
            "clock": lv.clock(r),
            "event": r.get("event"),
            "session": r.get("session") or "",
            "level": _LEVEL.get(lv.style(r), ""),
            "summary": lv._short(lv.summary(r), SUMMARY_WIDTH),
        }
        for r in reversed(chosen)
    ]
    return {
        "days": days,
        "available": [d.isoformat() for d in lv.available_days(directory)][-14:],
        "items": rows,
    }


def failovers(directory: Path, *, days: int = 7, today: date | None = None) -> dict[str, Any]:
    """How often each provider failed over, and the log errors, per day: a cheap health signal."""
    window = lv.days_back(today or _today(), days)
    by_provider: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for record in iter_records(directory, window):
        name = str(record.get("event", ""))
        if name == "failover":
            by_provider[str(record.get("provider", "?"))] += 1
        if lv.matches(record, errors_only=True):
            errors[str(record.get("ts", ""))[:10]] += 1
    return {
        "days": days,
        "failovers": dict(by_provider),
        "errors_per_day": dict(sorted(errors.items())),
    }


def costs(sources: Sources, directory: Path) -> dict[str, Any]:
    """Voice against its monthly cap, music against its daily quota, the dream's tokens."""
    out: dict[str, Any] = {}

    from brain.voice.budget import VoiceBudget

    cfg = settings.voice
    limit = cfg.tts.monthly_chars_limit
    voice: dict[str, Any] = {"enabled": cfg.enabled, "limit": limit, "used": None}
    try:
        used = VoiceBudget(cfg.usage_file, limit).used()
        voice.update(used=used, remaining=max(0, limit - used) if limit else None)
    except OSError:
        pass
    out["voice"] = voice

    from brain.music.quota import QuotaTracker

    music: dict[str, Any] = {"limit": settings.music.daily_quota_limit, "used": None}
    try:
        tracker = QuotaTracker(settings.music.quota_file, settings.music.daily_quota_limit)
        music.update(used=tracker.used(), remaining=tracker.remaining())
    except OSError:
        pass
    out["music"] = music

    dreams: dict[str, Any] = {"budget": settings.memory.v2.night.max_tokens, "runs": []}
    if sources.data is not None:
        runs = sources.data.runs.list(sort=[("started_at", -1)])[:14]
        dreams["runs"] = [
            {"started_at": r.started_at, "status": r.status, "tokens": r.tokens_used} for r in runs
        ]
        dreams["total"] = sum(r.tokens_used for r in runs)
    out["dreams"] = dreams
    out["providers"] = failovers(directory)
    return out


def health(sources: Sources, started: float, tools: list[dict[str, str]]) -> dict[str, Any]:
    """The server's pulse: what `/health` says, the version, the layers that are on, the tools."""
    base: dict[str, Any] = sources.health() if sources.health else {"status": "ok"}
    return {
        **base,
        "version": __version__,
        "mode": settings.deployment.mode,
        "uptime_seconds": max(0, round(time.monotonic() - started)),
        "layers": {
            "memory_v1": sources.memory is not None,
            "memory_v2": sources.data is not None,
            "leak_audit": sources.audit is not None,
            "issues": sources.issues is not None,
            "voice": settings.voice.enabled,
            "transcripts": sources.transcripts,
        },
        "auth": {
            "legacy_token": settings.deployment.auth.legacy_token,
            "allow_testers": settings.deployment.auth.allow_testers,
            "telegram_login": settings.telegram.require_login,
        },
        "tools": tools,
        "log_days": len(daily_files(Path(str(sources.log_dir)))) if sources.log_dir else 0,
    }


def tool_rows() -> list[dict[str, str]]:
    from brain.tools.registry import registry

    return [
        {"name": t.name, "tier": str(t.tier), "locality": str(t.locality), "offered": t.available()}  # type: ignore[dict-item]
        for t in sorted(registry.all(), key=lambda t: t.name)
    ]


def split(value: str | None) -> list[str]:
    return [part for part in (value or "").split(",") if part.strip()]


def flag(query: Mapping[str, list[str]], name: str) -> bool:
    values = query.get(name)
    return bool(values and values[0].strip().lower() in ("1", "true", "sim", "on"))


__all__ = ["costs", "failovers", "flag", "health", "log_rows", "split", "tool_rows"]
