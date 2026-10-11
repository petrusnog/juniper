"""Tests for brain.core.clock."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from brain.core import clock


def test_now_is_aware_and_uses_configured_timezone() -> None:
    moment = clock.now()
    assert moment.tzinfo is not None
    assert getattr(moment.tzinfo, "key", None) == "America/Sao_Paulo"


def test_now_accepts_explicit_timezone() -> None:
    assert getattr(clock.now("UTC").tzinfo, "key", None) == "UTC"


def test_describe_formats_weekday_date_time_and_zone() -> None:
    moment = datetime(2026, 10, 11, 17, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert clock.describe(moment) == "domingo, 11/10/2026, 17:30 (America/Sao_Paulo)"


@pytest.mark.parametrize(
    ("day", "weekday"),
    [
        (5, "segunda-feira"),
        (6, "terça-feira"),
        (7, "quarta-feira"),
        (8, "quinta-feira"),
        (9, "sexta-feira"),
        (10, "sábado"),
        (11, "domingo"),
    ],
)
def test_describe_weekday_names(day: int, weekday: str) -> None:
    moment = datetime(2026, 10, day, 9, 5, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert clock.describe(moment).startswith(f"{weekday}, {day:02d}/10/2026, 09:05")


def test_describe_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="fuso"):
        clock.describe(datetime(2026, 10, 11, 17, 30))


def test_describe_defaults_to_now() -> None:
    assert "(America/Sao_Paulo)" in clock.describe()
