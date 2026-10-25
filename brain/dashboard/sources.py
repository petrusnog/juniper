"""What the dashboard may read: the long-term memory and the V2 data of the running server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data


@dataclass
class Sources:
    """Everything is optional: a layer that is off simply answers ``enabled: false``."""

    memory: Any | None = None  # the v1 LongTermMemory (store, embedder, profile)
    data: V2Data | None = None  # the V2 collections
    transcripts: bool = True  # dashboard.transcripts

    @property
    def embedder(self) -> Any | None:
        return getattr(self.memory, "embedder", None)
