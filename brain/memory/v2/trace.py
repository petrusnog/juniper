"""Where a feeling came from: its cause references, resolved to what they point at.

A feeling cites ``turn:<session>:<n>`` (the user's line), ``concern:<id>`` (what it touched) and
sometimes ``belief:<id>``. This turns those references into readable data for the dashboard. It
only reads; a line or a concern that is gone is reported as such, never as an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain.memory.v2.models import Affect
    from brain.memory.v2.store import V2Data


def trace_affect(data: V2Data, affect: Affect, *, transcripts: bool = True) -> list[dict[str, Any]]:
    """One entry per cause: ``{"kind": "turn"|"concern"|"belief"|"other", ...}``."""
    return [_trace(data, ref, transcripts) for ref in affect.cause]


def _trace(data: V2Data, ref: str, transcripts: bool) -> dict[str, Any]:
    kind, _, rest = ref.partition(":")
    if kind == "turn":
        session, _, index = rest.rpartition(":")
        entry: dict[str, Any] = {"kind": "turn", "ref": ref, "session_id": session}
        try:
            number = int(index)
        except ValueError:
            return {"kind": "other", "ref": ref}
        entry["index"] = number
        if not transcripts:
            return {**entry, "hidden": True}
        found = data.transcripts.list({"session_id": session, "index": number})
        if not found:
            return {**entry, "missing": True}
        turn = found[0]
        return {
            **entry,
            "user": turn.user,
            # her reply may carry tool output: it stays out
            "assistant": None if turn.used_tools else turn.assistant,
            "at": turn.at,
        }
    if kind == "concern":
        concern = data.concerns.get(rest)
        if concern is None:
            return {"kind": "concern", "ref": ref, "id": rest, "missing": True}
        return {
            "kind": "concern",
            "ref": ref,
            "id": concern.id,
            "name": concern.name,
            "weight": concern.weight,
        }
    if kind == "belief":
        belief = data.beliefs.get(rest)
        if belief is None:
            return {"kind": "belief", "ref": ref, "id": rest, "missing": True}
        return {"kind": "belief", "ref": ref, "id": belief.id, "statement": belief.statement}
    return {"kind": "other", "ref": ref}
