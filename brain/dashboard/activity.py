"""The audit panel's data (T-222/T-224): filters, list rows, detail and the spreadsheet.

Pure functions over the stored records. Dates typed by the person are read in the agent's time
zone (UTC-3 for ``America/Sao_Paulo``): «13/11» means 00:00 to 23:59 of Brasília, not of UTC. The
friendly sentence is rendered in the language asked (``?lang=``), from the record's key and
parameters, so the same history reads in any language that has a catalog.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from brain import i18n
from brain.activity.model import public
from brain.config import settings
from brain.dashboard.api import ApiError, Query, first
from brain.dashboard.xlsx import Cell, Sheet
from brain.memory.activity import Filters

PERIODS: Final = ("1h", "today", "yesterday", "7d", "30d")
MULTI: Final = (
    "category",
    "action",
    "outcome",
    "model",
    "provider",
    "channel",
    "severity",
    "actor",
)
LOCAL_LABEL: Final = "UTC-3"
RAW_CELL_CHARS: Final = 4000  # the technical log in a spreadsheet cell
OUTCOME_LABELS: Final = {
    "ok": "ok",
    "failed": "falhou",
    "pending": "pendente",
    "denied": "negado",
    "partial": "parcial",
}


def zone() -> ZoneInfo:
    return ZoneInfo(settings.agent.timezone)


def offset_label(moment: datetime | None = None) -> str:
    """``UTC-3`` for the configured time zone (at ``moment``, now by default)."""
    local = (moment or datetime.now(UTC)).astimezone(zone())
    delta = local.utcoffset() or timedelta()
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


def _parse_local(text: str, *, end: bool = False) -> datetime:
    """``2026-11-13`` or ``2026-11-13T14:30`` read in the agent's time zone, as UTC."""
    raw = text.strip()
    try:
        date_only = "T" not in raw and " " not in raw
        moment = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError as exc:
        raise ApiError(
            400, f"data inválida: {text!r} (use AAAA-MM-DD ou AAAA-MM-DDTHH:MM)"
        ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone())
    if date_only and end:
        moment += timedelta(days=1)  # the whole last day is included
    return moment.astimezone(UTC)


def period_bounds(name: str, now: datetime | None = None) -> tuple[datetime, datetime | None]:
    """``(since, until)`` in UTC of a shortcut: 1h, today, yesterday, 7d, 30d (local days)."""
    current = (now or datetime.now(UTC)).astimezone(zone())
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    if name == "1h":
        return (current - timedelta(hours=1)).astimezone(UTC), None
    if name == "today":
        return midnight.astimezone(UTC), None
    if name == "yesterday":
        return (midnight - timedelta(days=1)).astimezone(UTC), midnight.astimezone(UTC)
    days = {"7d": 7, "30d": 30}.get(name)
    if days is None:
        raise ApiError(400, f"period deve ser um de: {', '.join(PERIODS)}")
    return (midnight - timedelta(days=days - 1)).astimezone(UTC), None


def _values(query: Query, name: str) -> list[str]:
    """``?outcome=failed,pending&outcome=denied`` -> ``["failed", "pending", "denied"]``."""
    out: list[str] = []
    for chunk in query.get(name) or []:
        out += [part.strip() for part in chunk.split(",") if part.strip()]
    return out


def filters_from(query: Query) -> Filters:
    """The search filters of a request. Unknown shortcuts and bad dates are a 400."""
    filters = Filters(
        q=(first(query, "q") or "").strip(),
        session=(first(query, "session") or "").strip(),
        turn=(first(query, "turn") or "").strip(),
        tool=(first(query, "tool") or "").strip(),
    )
    for name in MULTI:
        setattr(filters, name, _values(query, name))
    if period := first(query, "period"):
        filters.since, filters.until = period_bounds(period)
    if start := first(query, "from"):
        filters.since = _parse_local(start)
    if end := first(query, "to"):
        filters.until = _parse_local(end, end=True)
    if filters.since and filters.until and filters.until <= filters.since:
        raise ApiError(400, "o fim do período precisa ser depois do início")
    return filters


_LOCALE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")


def language(query: Query) -> str:
    """The language the sentences are shown in: the one asked when it is a well-formed locale
    name (no catalog is not an error: English comes out), else the configured one."""
    wanted = i18n.normalize_locale(first(query, "lang") or settings.activity.locale)
    return wanted if _LOCALE.fullmatch(wanted) else i18n.normalize_locale(settings.activity.locale)


def item(record: Mapping[str, Any], locale: str) -> dict[str, Any]:
    """A row of the timeline."""
    out = public(record, locale)
    out.pop("raw", None)
    out["clock"] = clock(record.get("ts"))
    return out


def clock(moment: Any) -> str:
    """``13/11/2026 14:32:08 (UTC-3)`` for a stored instant."""
    if not isinstance(moment, datetime):
        return str(moment or "")
    stamp = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return f"{stamp.astimezone(zone()):%d/%m/%Y %H:%M:%S} ({offset_label(stamp)})"


def detail(
    record: Mapping[str, Any], locale: str, steps: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """One record whole: every field, the technical event and the other steps of its message."""
    out = public(record, locale)
    out["clock"] = clock(record.get("ts"))
    out["steps"] = [item(step, locale) for step in steps]
    out["languages"] = i18n.available()
    return out


def workbook(records: Sequence[Mapping[str, Any]], locale: str) -> list[Sheet]:
    """The export: one row per record, with the friendly text and the technical one side by side."""
    rows: list[list[Cell]] = []
    for record in records:
        shown = public(record, locale)
        raw = record.get("raw") or {}
        rows.append(
            [
                shown["id"],
                clock(record.get("ts")),
                str(record.get("ts_local") or ""),
                shown.get("summary") or "",
                record.get("category") or "",
                record.get("action") or "",
                OUTCOME_LABELS.get(str(record.get("outcome")), str(record.get("outcome") or "")),
                record.get("actor") or "",
                record.get("channel") or "",
                "sim" if record.get("voice") else "não",
                record.get("provider") or "",
                record.get("model") or "",
                record.get("tool") or "",
                record.get("duration_ms") if record.get("duration_ms") is not None else "",
                record.get("tokens") if record.get("tokens") is not None else "",
                record.get("session") or "",
                record.get("turn") or "",
                record.get("severity") or "",
                str(raw)[:RAW_CELL_CHARS],
            ]
        )
    header = [
        "Registro",
        f"Data e hora ({LOCAL_LABEL})",
        "Data e hora (ISO)",
        "O que a Juniper fez",
        "Categoria",
        "Ação",
        "Desfecho",
        "Quem",
        "Canal",
        "Voz",
        "Provedor",
        "Modelo",
        "Ferramenta",
        "Duração (ms)",
        "Tokens",
        "Sessão",
        "Mensagem (turno)",
        "Severidade",
        "Log técnico",
    ]
    return [
        Sheet(
            "Atividade",
            header,
            rows,
            widths=[18, 24, 28, 80, 12, 24, 10, 10, 10, 6, 12, 28, 18, 12, 9, 26, 14, 10, 90],
            wrap=frozenset({3, 18}),
        )
    ]
