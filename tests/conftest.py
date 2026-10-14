"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.config import settings
from brain.eventlog import event_log


@pytest.fixture(autouse=True)
def _isolated_event_log(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Keep tests from writing into the real ``~/.juniper/logs``."""
    previous = event_log.directory
    event_log.directory = tmp_path / "logs"
    event_log.enabled = True
    yield
    event_log.directory = previous


@pytest.fixture(autouse=True)
def _isolated_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from saving conversations into the real ``~/.juniper/sessions`` or MongoDB.

    The developer's own config.yaml may select MongoDB; tests always start from files.
    """
    monkeypatch.setattr(settings.memory.session, "dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.memory.session, "store", "file")
