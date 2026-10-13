"""Current date and time in the configured timezone (ADR-008).

The LLM has no idea what day it is; the agent injects ``describe()`` in the prompt on
every request so "tomorrow at 4pm" resolves to a real date. The system clock is the source.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from brain.config import settings

_WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


def now(tz: str | None = None) -> datetime:
    """Timezone-aware current time (defaults to ``settings.agent.timezone``)."""
    return datetime.now(ZoneInfo(tz or settings.agent.timezone))


def weekday_name(moment: datetime) -> str:
    """Portuguese weekday name, independent of the system locale."""
    return _WEEKDAYS[moment.weekday()]


def describe(moment: datetime | None = None) -> str:
    """Human-readable moment for the prompt: ``domingo, 11/10/2026, 17:30 (America/Sao_Paulo)``."""
    moment = moment or now()
    if moment.tzinfo is None:
        raise ValueError("describe() precisa de um datetime com fuso horário")
    zone = getattr(moment.tzinfo, "key", None) or moment.tzname() or "UTC"
    return f"{weekday_name(moment)}, {moment:%d/%m/%Y, %H:%M} ({zone})"
