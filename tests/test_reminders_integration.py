"""Real MongoDB checks: partial indexes, idempotent upsert and competing workers."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from pymongo.errors import DuplicateKeyError

from brain.config import settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.mongo import MongoConnection
from brain.reminders.store import ReminderStore

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_store():
    config = settings.memory.mongo.model_copy(
        update={"database": f"juniper_reminders_test_{uuid.uuid4().hex}", "timeout_ms": 500}
    )
    connection = MongoConnection(config)
    try:
        connection.check()
    except MemoryUnavailableError as exc:
        connection.close()
        pytest.skip(f"MongoDB real indisponível: {exc}")
    try:
        yield ReminderStore(connection)
    finally:
        connection.client.drop_database(config.database)
        connection.close()


def create(store, owner="integration", **kwargs):
    return store.create(
        owner,
        title="Real reminder",
        due_at=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        **kwargs,
    )


def test_partial_unique_index_allows_unlinked_and_rejects_duplicate_link(real_store):
    create(real_store)
    create(real_store)
    linked = create(real_store, event_id="unique-event", calendar_id="calendar")
    raw = real_store.collection().find_one({"_id": linked["id"]})
    with pytest.raises(DuplicateKeyError):
        real_store.collection().insert_one({**raw, "_id": uuid.uuid4().hex})


def test_set_on_insert_is_idempotent_with_filter_fields(real_store):
    first = create(real_store, event_id="retry-event", calendar_id="calendar")
    again = create(real_store, event_id="retry-event", calendar_id="calendar")
    assert first["id"] == again["id"]


def test_competing_workers_have_a_single_lease_winner(real_store):
    doc = create(real_store, owner="competition")
    barrier = Barrier(8)

    def claim(_):
        barrier.wait(timeout=5)
        return real_store.claim(doc, "telegram")

    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(claim, range(8)))
    assert sum(t is not None for t in tokens) == 1
