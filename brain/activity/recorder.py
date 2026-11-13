"""Turns every ``event_log`` event into an activity record and hands it to the store (T-219/T-221).

``EventLog.log`` calls ``recorder.on_event`` once the legacy line is on disk, so the technical
JSONL is exactly what it always was and never waits for the recorder. The recorder translates
(``catalog``), builds the document (``model``) and queues it; a daemon thread drains the queue
into the store, because the store's driver blocks and the events are logged from the event loop.
A full queue drops the new record (and counts it): auditing never slows down or breaks a
conversation.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import queue
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from brain.activity import catalog, model

logger = logging.getLogger(__name__)

QUEUE_SIZE: Final = 5000
_turn: contextvars.ContextVar[str] = contextvars.ContextVar("activity_turn", default="")
Sink = Callable[[dict[str, Any]], None]


def current_turn() -> str:
    """The id of the message being answered by this task ("" outside a turn)."""
    return _turn.get()


def _served() -> tuple[str | None, str | None]:
    from brain.llm.router import served_in_task

    return served_in_task() or (None, None)


def _channel() -> tuple[str | None, bool]:
    from brain.tools.channel import current_channel

    channel = current_channel.get()
    return (channel.kind, channel.voice) if channel is not None else (None, False)


class Recorder:
    def __init__(self) -> None:
        self._sink: Sink | None = None
        self._queue: queue.Queue[dict[str, Any] | None] | None = None
        self._thread: threading.Thread | None = None
        self.dropped = 0
        self.failed = 0
        self._stopping = threading.Event()
        self._warned: set[str] = set()

    @property
    def active(self) -> bool:
        return self._sink is not None

    def start(self, sink: Sink, *, threaded: bool = True) -> None:
        """Begin recording into ``sink``. ``threaded=False`` writes inline (tests)."""
        self.stop()
        self._sink = sink
        self._stopping = threading.Event()
        self._warned = set()
        self.dropped = self.failed = 0
        if not threaded:
            return
        self._queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._thread = threading.Thread(
            target=self._drain,
            args=(self._queue, sink, self._stopping),
            name="activity-writer",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Stop recording; what is queued is written first (up to ``timeout`` seconds)."""
        worker, pending = self._thread, self._queue
        self._sink = None
        self._thread = self._queue = None
        if worker is not None and pending is not None:
            with contextlib.suppress(queue.Full):  # a full queue and a dead database: do not wait
                pending.put_nowait(None)
            self._stopping.set()
            worker.join(timeout)

    def flush(self, timeout: float = 3.0) -> None:
        """Wait until everything queued so far is written (tests, shutdown)."""
        pending = self._queue
        if pending is None:
            return
        done = threading.Event()
        pending.put({"__flush__": done})
        done.wait(timeout)

    def _warn(self, kind: str, message: str) -> None:
        """One warning per kind and run: a database that is down must not flood the log."""
        if kind not in self._warned:
            self._warned.add(kind)
            logger.warning(message)

    def _drain(
        self,
        pending: queue.Queue[dict[str, Any] | None],
        sink: Sink,
        stopping: threading.Event,
    ) -> None:
        while not stopping.is_set():
            try:
                item = pending.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                return
            if "__flush__" in item:
                item["__flush__"].set()
                continue
            try:
                sink(item)
            except Exception as exc:  # noqa: BLE001  (auditing must never take the app down)
                self.failed += 1
                self._warn(
                    "failed", f"registro de atividade: a gravação falhou ({type(exc).__name__})"
                )

    def on_event(self, event: str, record: Mapping[str, Any]) -> None:
        """Called by the event log with the (masked) record it just wrote."""
        sink = self._sink
        if sink is None:
            return
        if event == "timing":  # the last event of a turn: what follows belongs to no message
            _turn.set("")
        try:
            self._record(sink, event, record)
        except Exception as exc:  # noqa: BLE001
            self.failed += 1
            self._warn(
                "failed", f"registro de atividade: um evento foi ignorado ({type(exc).__name__})"
            )

    def _record(self, sink: Sink, event: str, record: Mapping[str, Any]) -> None:
        fields = {k: v for k, v in record.items() if k not in ("ts", "run", "event", "session")}
        entry = catalog.translate(event, fields)
        if entry is None:
            return
        if event == "turn_start":
            _turn.set(f"t-{uuid.uuid4().hex[:8]}")
        provider, served_model = _served()
        entry.facts.setdefault("provider", provider)
        entry.facts.setdefault("model", served_model)
        kind, spoken = _channel()
        document = model.build(
            entry,
            record,
            event=event,
            now=datetime.now(UTC),
            session=str(record.get("session") or ""),
            turn=_turn.get(),
            run=str(record.get("run") or ""),
            channel=kind,
            voice=spoken,
        )
        if self._queue is None:
            sink(document)
            return
        try:
            self._queue.put_nowait(document)
        except queue.Full:
            self.dropped += 1
            self._warn("dropped", "registro de atividade: a fila encheu, registros descartados")


recorder = Recorder()
