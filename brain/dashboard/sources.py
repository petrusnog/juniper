"""What the dashboard may read: the long-term memory and the V2 data of the running server."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data


@dataclass
class Sources:
    """Everything is optional: a layer that is off simply answers ``enabled: false``."""

    memory: Any | None = None  # the v1 LongTermMemory (store, embedder, profile)
    data: V2Data | None = None  # the V2 collections
    transcripts: bool = True  # dashboard.transcripts
    reports: Any | None = None  # the user's leak reports (``LeakReportStore``), MongoDB only
    audit: Any | None = None  # the automatic leak audit records (``LeakAuditStore``)
    health: Callable[[], dict[str, Any]] | None = None  # the server's own ``/health`` answer
    log_dir: Any | None = None  # where the technical log lives (``event_log.directory``)
    started: float = field(default_factory=time.monotonic)
    jobs: Any | None = None  # the background tasks (``JobRunner``), ADR-028
    issues: Any | None = None  # the general issue log (``IssueStore``), MongoDB only (ADR-028)

    @property
    def embedder(self) -> Any | None:
        return getattr(self.memory, "embedder", None)
