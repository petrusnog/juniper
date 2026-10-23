"""Writes every finished turn to the durable transcript (ADR-017, section 9, phase 1).

The saved session is compacted (only the last turns survive), so the nightly pass reads this
instead. ``TranscriptWriter`` is the ``turn_sink`` of the ``Agent``: after each turn it stores the
text the user wrote and her final reply (never tool output) and updates the session's watermark
(``turns_total`` and ``last_turn_at``), which is how the night finds sessions with something new
without scanning every turn.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from brain.core.session import Turn
from brain.memory.v2.models import TranscriptTurn, Watermark, utcnow
from brain.memory.v2.store import V2Data


class TranscriptWriter:
    def __init__(self, data: V2Data, clock: Callable[[], datetime] = utcnow) -> None:
        self.data = data
        self.clock = clock

    def write(self, session_id: str, turn: Turn) -> None:
        """Store ``turn`` (a turn without an index is skipped). Writing it twice is harmless."""
        if turn.index is None:
            return
        now = self.clock()
        self.data.transcripts.save(
            TranscriptTurn(
                session_id=session_id,
                index=turn.index,
                user=turn.user,
                assistant=turn.assistant,
                used_tools=turn.used_tools,
                at=now,
            )
        )
        mark = self.data.watermarks.get(session_id) or Watermark(session_id)
        mark.turns_total = max(mark.turns_total, turn.index + 1)
        mark.last_turn_at = now
        mark.updated_at = now
        self.data.watermarks.save(mark)
