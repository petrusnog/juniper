"""The activity records in MongoDB (``activity``, Observabilidade V2).

One document per action Juniper took (``brain/activity``). They expire by TTL on ``ts`` after
``activity.retention_days``. Search is a plain query over indexed fields plus a case- and
accent-insensitive substring match on ``haystack`` (the friendly text and the technical event,
folded), which is cheap enough for the few hundred thousand documents a retention window holds.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from pymongo import DESCENDING
from pymongo.errors import OperationFailure

from brain.activity.model import fold
from brain.config import settings
from brain.memory.mongo import MongoConnection

ACTIVITY_COLLECTION: Final = "activity"
_INDEX_OPTIONS_CONFLICT = (85, 86)
MAX_PAGE: Final = 200
MAX_OFFSET: Final = 100_000  # a deeper skip scans the collection; narrow the filter instead
MAX_EXPORT: Final = 5000
MAX_SCAN: Final = 20000  # documents read to draw the charts and the rankings
MAX_TERM_CHARS: Final = 200
FACET_FIELDS: Final = ("category", "action", "outcome", "model", "provider", "channel", "severity")
_ID = re.compile(r"[0-9a-f]{4,32}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_TERMS: Final = 5  # words of a search; each one is a regex over the whole period


@dataclass
class Filters:
    """Everything the search accepts; empty means "any"."""

    since: datetime | None = None  # inclusive, UTC
    until: datetime | None = None  # exclusive, UTC
    q: str = ""
    category: list[str] = field(default_factory=list)
    action: list[str] = field(default_factory=list)
    outcome: list[str] = field(default_factory=list)
    model: list[str] = field(default_factory=list)
    provider: list[str] = field(default_factory=list)
    channel: list[str] = field(default_factory=list)
    severity: list[str] = field(default_factory=list)
    actor: list[str] = field(default_factory=list)
    session: str = ""
    turn: str = ""
    tool: str = ""

    def query(self) -> dict[str, Any]:
        """The MongoDB filter. Values are matched exactly (``$in``), the term as a substring."""
        query: dict[str, Any] = {}
        if self.since or self.until:
            window: dict[str, Any] = {}
            if self.since:
                window["$gte"] = self.since
            if self.until:
                window["$lt"] = self.until
            query["ts"] = window
        for name in (
            "category",
            "action",
            "outcome",
            "model",
            "provider",
            "channel",
            "severity",
            "actor",
        ):
            values = [v for v in getattr(self, name) if v]
            if values:
                query[name] = {"$in": values}
        for name in ("session", "turn", "tool"):
            if value := getattr(self, name):
                query[name] = value
        words = _CONTROL.sub(" ", self.q)[:MAX_TERM_CHARS].split()
        terms = [fold(t) for t in words[:MAX_TERMS]]
        if terms:
            query["$and"] = [{"haystack": {"$regex": re.escape(t)}} for t in terms]
        return query


class ActivityStore:
    """The records. The connection is borrowed (from the session store), like the leak audit."""

    def __init__(
        self, connection: MongoConnection | None = None, retention_days: int | None = None
    ) -> None:
        self.connection = connection if connection is not None else MongoConnection()
        self._retention = (
            retention_days if retention_days is not None else settings.activity.retention_days
        )
        self._indexed = False

    def _collection(self) -> Any:
        collection = self.connection.collection(ACTIVITY_COLLECTION)
        if not self._indexed:
            self._ensure_ttl(collection)
            collection.create_index([("session", 1), ("ts", DESCENDING)])
            collection.create_index([("turn", 1), ("ts", 1)])
            collection.create_index([("category", 1), ("action", 1), ("ts", DESCENDING)])
            collection.create_index([("model", 1), ("ts", DESCENDING)])
            collection.create_index([("outcome", 1), ("ts", DESCENDING)])
            self._indexed = True
        return collection

    def _ensure_ttl(self, collection: Any) -> None:
        seconds = self._retention * 86400
        try:
            collection.create_index("ts", expireAfterSeconds=seconds)
        except OperationFailure as exc:
            if exc.code not in _INDEX_OPTIONS_CONFLICT:
                raise
            collection.database.command(
                "collMod",
                ACTIVITY_COLLECTION,
                index={"keyPattern": {"ts": 1}, "expireAfterSeconds": seconds},
            )

    # --- writing -------------------------------------------------------------------------------

    def add(self, record: dict[str, Any]) -> None:
        """The recorder's sink. Errors propagate: the recorder counts them and moves on."""
        with self.connection.guard():
            self._collection().insert_one(record)

    # --- reading -------------------------------------------------------------------------------

    def search(
        self, filters: Filters, *, limit: int = 50, offset: int = 0, oldest_first: bool = False
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of the matching records (newest first) and how many match in all."""
        size = max(1, min(int(limit), MAX_PAGE))
        query = filters.query()
        direction = 1 if oldest_first else DESCENDING
        with self.connection.guard():
            collection = self._collection()
            total = collection.count_documents(query)
            cursor = (
                collection.find(query)
                .sort([("ts", direction), ("seq", direction), ("_id", direction)])  # stable ties
                .skip(min(max(0, offset), MAX_OFFSET))
                .limit(size)
            )
            return list(cursor), int(total)

    def get(self, record_id: str) -> dict[str, Any] | None:
        """One record by its id or by a unique prefix of it."""
        if not _ID.fullmatch(record_id):
            return None
        with self.connection.guard():
            collection = self._collection()
            found: dict[str, Any] | None = collection.find_one({"_id": record_id})
            if found is not None:
                return found
            matches = list(collection.find({"_id": {"$regex": f"^{record_id}"}}).limit(2))
        return matches[0] if len(matches) == 1 else None

    def turn(self, turn_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Every step of one message, in the order they happened."""
        if not turn_id:
            return []
        with self.connection.guard():
            cursor = (
                self._collection()
                .find({"turn": turn_id})
                .sort([("ts", 1), ("seq", 1)])
                .limit(limit)
            )
            return list(cursor)

    def facets(self, filters: Filters) -> dict[str, list[tuple[str, int]]]:
        """How many matching records each value of the filter fields has (for the chips)."""
        query = filters.query()
        out: dict[str, list[tuple[str, int]]] = {}
        with self.connection.guard():
            collection = self._collection()
            for name in FACET_FIELDS:
                rows = collection.aggregate(
                    [
                        {"$match": {**query, name: {"$ne": None}}},
                        {"$group": {"_id": f"${name}", "n": {"$sum": 1}}},
                        {"$sort": {"n": -1}},
                        {"$limit": 50},
                    ]
                )
                out[name] = [(str(row["_id"]), int(row["n"])) for row in rows if row["_id"]]
        return out

    def scan(self, filters: Filters, fields: dict[str, int]) -> list[dict[str, Any]]:
        """The matching records, projected to ``fields`` (capped at ``MAX_SCAN``)."""
        with self.connection.guard():
            cursor = (
                self._collection().find(filters.query(), fields).sort("ts", DESCENDING)
            ).limit(MAX_SCAN)
            return list(cursor)

    def summary(self, filters: Filters) -> dict[str, Any]:
        """Counters, activity over time and rankings of the filtered period."""
        rows = self.scan(
            filters,
            dict.fromkeys(
                (
                    "ts",
                    "outcome",
                    "severity",
                    "category",
                    "action",
                    "model",
                    "provider",
                    "duration_ms",
                    "tokens",
                ),
                1,
            ),
        )
        return summarize(rows, filters)

    def count_since(self, since: datetime) -> int:
        with self.connection.guard():
            return int(self._collection().count_documents({"ts": {"$gte": since}}))


MAX_BUCKETS: Final = 400


def _continuous(
    buckets: Counter[str], failures: Counter[str], by_hour: bool
) -> list[dict[str, Any]]:
    """Every hour (or day) from the first to the last record, empty ones as zero, so the chart's
    axis is honest. A span too long to draw that way keeps only the buckets that have records."""
    if not buckets:
        return []
    pattern = "%Y-%m-%dT%H:00" if by_hour else "%Y-%m-%d"
    first, last = (datetime.strptime(x, pattern) for x in (min(buckets), max(buckets)))
    step = timedelta(hours=1) if by_hour else timedelta(days=1)
    if (last - first) / step >= MAX_BUCKETS:
        labels = sorted(buckets)
    else:
        labels = []
        moment = first
        while moment <= last:
            labels.append(moment.strftime(pattern))
            moment += step
    return [
        {"at": label, "count": buckets.get(label, 0), "failed": failures.get(label, 0)}
        for label in labels
    ]


def summarize(rows: list[dict[str, Any]], filters: Filters) -> dict[str, Any]:
    """Pure aggregation over projected records, so it is testable without a database."""
    outcomes = Counter(str(r.get("outcome")) for r in rows)
    zone = ZoneInfo(settings.agent.timezone)
    times = [r["ts"].astimezone(UTC) for r in rows if isinstance(r.get("ts"), datetime)]
    span = (max(times) - min(times)) if times else timedelta()
    if filters.since and filters.until:
        span = filters.until - filters.since
    by_hour = span <= timedelta(days=2)
    buckets: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    for r in rows:
        ts = r.get("ts")
        if not isinstance(ts, datetime):
            continue
        local = (
            ts.replace(tzinfo=UTC).astimezone(zone) if ts.tzinfo is None else ts.astimezone(zone)
        )
        label = local.strftime("%Y-%m-%dT%H:00" if by_hour else "%Y-%m-%d")
        buckets[label] += 1
        if r.get("outcome") == "failed":
            failures[label] += 1
    timeline = _continuous(buckets, failures, by_hour)
    models: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.get("category") != "llm" or r.get("action") != "llm.call":
            continue
        name = f"{r.get('provider') or '?'}/{r.get('model') or '?'}"
        entry = models.setdefault(name, {"calls": 0, "ms": 0, "tokens": 0})
        entry["calls"] += 1
        entry["ms"] += float(r.get("duration_ms") or 0)
        entry["tokens"] += float(r.get("tokens") or 0)
    ranking = [
        {
            "model": name,
            "calls": int(v["calls"]),
            "avg_ms": round(v["ms"] / v["calls"]) if v["calls"] else 0,
            "tokens": int(v["tokens"]),
        }
        for name, v in sorted(models.items(), key=lambda kv: -kv[1]["calls"])
    ]
    return {
        "total": len(rows),
        "capped": len(rows) >= MAX_SCAN,
        "failed": outcomes.get("failed", 0),
        "pending": outcomes.get("pending", 0),
        "denied": outcomes.get("denied", 0),
        "failovers": sum(1 for r in rows if r.get("action") == "llm.failover"),
        "by": "hour" if by_hour else "day",
        "timeline": timeline,
        "models": ranking,
        "actions": Counter(str(r.get("action")) for r in rows).most_common(15),
    }
