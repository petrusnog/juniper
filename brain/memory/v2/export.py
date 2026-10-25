"""The V2 layers as plain data, for ``juniper memory export`` (ADR-017, principle 7).

Everything she concluded about the user is exportable: beliefs (with their evidence references),
interests, concerns, feelings, the personality (current and history), the calibration of her own
hunches and the night diary. Vectors are left out unless asked for. The raw durable transcript
and the nightly run bookkeeping are not layers and stay out.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data


def _plain(doc: dict[str, Any], vectors: bool) -> dict[str, Any]:
    row = json.loads(json.dumps(doc, ensure_ascii=False, default=str))
    row["id"] = row.pop("_id", row.get("id"))
    if not vectors:
        row.pop("embedding", None)
    return row


def export_v2(data: V2Data, include_vectors: bool = False) -> dict[str, Any]:
    """Every V2 layer as JSON-safe data."""
    persona = data.persona.current()
    return {
        "beliefs": [_plain(b.to_doc(), include_vectors) for b in data.beliefs.list()],
        "interests": [_plain(i.to_doc(), include_vectors) for i in data.interests.list()],
        "study_notes": [
            _plain(n.to_doc(), include_vectors) for n in data.notes.list(sort=[("created_at", 1)])
        ],
        "concerns": [_plain(c.to_doc(), include_vectors) for c in data.concerns.list()],
        "affects": [_plain(a.to_doc(), include_vectors) for a in data.affects.list()],
        "persona": {
            "current": _plain(persona.to_doc(), include_vectors) if persona else None,
            "history": [_plain(v.to_doc(), include_vectors) for v in data.persona.history.list()],
        },
        "calibration": [_plain(c.to_doc(), include_vectors) for c in data.calibration.list()],
        "journal": [
            _plain(j.to_doc(), include_vectors) for j in data.journal.list(sort=[("at", 1)])
        ],
    }
