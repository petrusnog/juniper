"""One chokepoint for what leaves the dashboard: JSON-safe, secrets masked, no vectors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from brain.memory.secretfilter import mask_secrets

# Our own ids (``mem_<14 digits><6 hex>``, session ids). The secret filter reads a long run of
# digits as a card number and would mangle an id now and then, so ids are kept out of its sight.
_ID = re.compile(r"\b(?:[a-z]{2,6}_\d{14}[0-9a-f]{6}|\d{8}-\d{6}-[0-9a-f]{6})\b")
HIDDEN_KEYS = frozenset({"embedding", "embedding_model", "vector"})
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def sanitize(value: Any) -> Any:
    """``value`` as plain JSON data: dates as ISO text, secrets masked, vectors removed."""
    if isinstance(value, str):
        return _mask(value)
    if isinstance(value, Mapping):
        return {str(k): sanitize(v) for k, v in value.items() if k not in HIDDEN_KEYS}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _mask(text: str) -> str:
    """``mask_secrets`` over the text between our ids, which stay as they are."""
    out: list[str] = []
    last = 0
    for found in _ID.finditer(text):
        out.append(mask_secrets(text[last : found.start()]))
        out.append(found.group())
        last = found.end()
    out.append(mask_secrets(text[last:]))
    return "".join(out)


def number(query: Mapping[str, list[str]], name: str, default: int, low: int, high: int) -> int:
    values = query.get(name)
    try:
        parsed = int(values[0]) if values else default
    except ValueError:
        parsed = default
    return max(low, min(high, parsed))


def paged(items: list[Any], query: Mapping[str, list[str]]) -> dict[str, Any]:
    """``{"total", "offset", "limit", "items"}`` for ``?offset=&limit=``."""
    limit = number(query, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = number(query, "offset", 0, 0, 10**9)
    return {
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "items": items[offset : offset + limit],
    }


def contains(text: str, needle: str | None) -> bool:
    return not needle or needle.casefold() in text.casefold()
