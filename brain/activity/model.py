"""The activity record (T-219): one line of what Juniper did, in a form people can search.

A record carries both audiences: ``summary`` is the friendly sentence (rendered in the server's
language when written) and ``raw`` is the technical event exactly as the ``event_log`` wrote it.
``msg_key`` + ``msg_params`` are what the sentence is made of, so it can be rendered again in any
language (``brain/i18n``). Times are kept twice: ``ts`` (UTC, what the database indexes and sorts)
and ``ts_local`` (ISO text with the offset, UTC-3 for the configured ``agent.timezone``).
"""

from __future__ import annotations

import os
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

import brain
from brain.config import settings
from brain.eventlog import redact
from brain.i18n import render
from brain.memory.secretfilter import mask_secrets

ID_CHARS: Final = 16
OUTCOMES: Final = ("ok", "failed", "pending", "denied", "partial")
SEVERITIES: Final = ("info", "warn", "error")
ACTORS: Final = ("user", "juniper", "scheduler", "system")
# Longest value of a message parameter: a pasted page must not become a sentence.
PARAM_CHARS: Final = 160
HAYSTACK_RAW_CHARS: Final = 1500
# Fields the builder owns: a fact of the same name must never overwrite them.
RESERVED: Final = frozenset(
    {
        "_id",
        "ts",
        "ts_local",
        "session",
        "turn",
        "run",
        "actor",
        "channel",
        "voice",
        "category",
        "action",
        "outcome",
        "severity",
        "msg_key",
        "msg_params",
        "locale",
        "summary",
        "event",
        "version",
        "commit",
        "raw",
        "haystack",
    }
)


@dataclass
class Entry:
    """What the catalog says about one event: how to classify it and what to tell."""

    category: str
    action: str
    key: str
    params: dict[str, Any] = field(default_factory=dict)
    outcome: str = "ok"
    severity: str = "info"
    actor: str = "juniper"
    # Searchable facts of the record (provider, model, tokens, duration_ms, tool, tier, ...).
    facts: dict[str, Any] = field(default_factory=dict)


def fold(text: object) -> str:
    """Lower case without accents, so «evidencias» finds «Evidências»."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _mask(value: Any) -> Any:
    return mask_secrets(value) if isinstance(value, str) else value


def clean_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Parameters safe to store: secrets masked, long text cut, one line each."""
    out: dict[str, Any] = {}
    for name, value in params.items():
        masked = redact(value, str(name), limit=PARAM_CHARS)
        if isinstance(masked, str):
            masked = mask_secrets(" ".join(masked.split()))
        out[str(name)] = masked
    return out


def local_text(moment: datetime) -> str:
    """``2026-11-13T14:32:08.412-03:00``: the instant in the agent's time zone, offset included."""
    zone = ZoneInfo(settings.agent.timezone)
    return moment.astimezone(zone).isoformat(timespec="milliseconds")


def _haystack(
    summary: str,
    entry: Entry,
    params: Mapping[str, Any],
    facts: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> str:
    parts = [summary, entry.category, entry.action, *map(str, facts.values())]
    parts += [str(v) for v in params.values()]
    technical = str({k: v for k, v in raw.items() if k not in ("ts", "run")})
    parts.append(mask_secrets(technical[:HAYSTACK_RAW_CHARS]))
    return fold(mask_secrets(" ".join(parts)))


def build(
    entry: Entry,
    raw: Mapping[str, Any],
    *,
    event: str,
    now: datetime | None = None,
    session: str = "",
    turn: str = "",
    run: str = "",
    channel: str | None = None,
    voice: bool = False,
    locale: str | None = None,
) -> dict[str, Any]:
    """The document that goes to the store (``raw`` is already masked by the event log)."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    params = clean_params(entry.params)
    language = locale or settings.activity.locale
    summary = mask_secrets(render(entry.key, params, language))
    facts = {
        k: _mask(redact(v, k, limit=PARAM_CHARS))
        for k, v in entry.facts.items()
        if v is not None and k not in RESERVED
    }
    record: dict[str, Any] = {
        "_id": uuid.uuid4().hex[:ID_CHARS],
        "ts": moment,
        "ts_local": local_text(moment),
        "session": session or None,
        "turn": turn or None,
        "run": run or None,
        "actor": entry.actor,
        "channel": channel,
        "voice": voice,
        "category": entry.category,
        "action": entry.action,
        "outcome": entry.outcome,
        "severity": entry.severity,
        "msg_key": entry.key,
        "msg_params": params,
        "locale": language,
        "summary": summary,
        "event": event,
        "version": brain.__version__,
        "commit": os.environ.get("GIT_COMMIT") or None,
        "raw": dict(raw),
        **facts,
    }
    record["haystack"] = _haystack(summary, entry, params, facts, raw)
    return record


def public(record: Mapping[str, Any], locale: str | None = None) -> dict[str, Any]:
    """A record as the API shows it: no ``haystack``, the sentence rendered in ``locale``."""
    out = {k: v for k, v in record.items() if k != "haystack"}
    out["id"] = str(out.pop("_id", ""))
    if locale:
        out["summary"] = render(out.get("msg_key") or "", out.get("msg_params") or {}, locale)
        out["locale"] = locale
    ts = out.get("ts")
    if isinstance(ts, datetime):
        out["ts"] = ts.astimezone(UTC).isoformat(timespec="milliseconds")
    return out
