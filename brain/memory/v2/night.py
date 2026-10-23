"""The nightly pass, "o inconsciente" (ADR-017, section 9): the skeleton.

Once a day the server reads what happened since the last pass and updates the layers. This
module is the frame; the content comes from phases (observe, reflect, affect, persona, interests),
each one a small object with a ``run(ctx)``.

- **Collection:** sessions with something new (``turns_total > turns_read``), left alone while
  still active, read from the durable transcript (the saved session is compacted).
- **Idempotent and resumable:** there are no multi-document transactions, so a re-run must do the
  right thing. Phases cite evidence by reference, and the watermark of a session only advances
  after the phases that consume the material finished, so a failed night is simply read again.
- **Budget:** tokens and minutes per night; when it runs out the phase stops and the rest waits.
- **The code decides:** phases may ask an LLM to *propose*; they validate and apply with code.
- **Dry run:** the real phases run over an overlay store, so nothing reaches the database; the
  overlay then says what *would* have been written.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from brain.config import MemoryV2Settings, settings
from brain.eventlog import event_log
from brain.llm.base import LLMProvider
from brain.llm.errors import LLMError
from brain.llm.types import Message
from brain.memory.errors import MemoryUnavailableError
from brain.memory.v2.guards import NightMaterial, build_night_material
from brain.memory.v2.models import DreamRun, JournalEntry, TranscriptTurn, utcnow
from brain.memory.v2.overlay import OverlayDocStore
from brain.memory.v2.store import V2Data

CHARS_PER_TOKEN = 4
# A session touched in the last minutes is still a conversation, not yesterday's: wait for it.
ACTIVE_MINUTES = 30
# Share of the night's token budget that the material read may take; the rest is for answers.
MATERIAL_SHARE = 0.4

_THINK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def strip_reasoning(text: str) -> str:
    """The answer without the "thinking" a reasoning model may put in front of it."""
    return _THINK.sub("", text).strip()


class BudgetExceededError(Exception):
    """The night ran out of tokens or minutes."""


class Budget:
    def __init__(
        self, max_tokens: int, max_seconds: float, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_tokens = max_tokens
        self.max_seconds = max_seconds
        self.used_tokens = 0
        self._monotonic = monotonic
        self._started = monotonic()

    @property
    def elapsed(self) -> float:
        return self._monotonic() - self._started

    @property
    def exhausted(self) -> bool:
        return self.used_tokens >= self.max_tokens or self.elapsed >= self.max_seconds

    def check(self) -> None:
        if self.exhausted:
            raise BudgetExceededError("o orçamento da noite acabou")

    def charge(self, tokens: int) -> None:
        self.used_tokens += max(0, tokens)


# --- the material -----------------------------------------------------------------------------


@dataclass
class SessionMaterial:
    """What one session adds to tonight: the turns after the watermark."""

    session_id: str
    turns: list[TranscriptTurn]

    @property
    def first_index(self) -> int:
        return self.turns[0].index

    @property
    def last_index(self) -> int:
        return self.turns[-1].index

    def material(self, max_chars_per_message: int | None = None) -> NightMaterial:
        return build_night_material(self.turns, max_chars_per_message=max_chars_per_message)


def collect_material(
    data: V2Data, now: datetime, *, max_tokens: int, active_minutes: int = ACTIVE_MINUTES
) -> list[SessionMaterial]:
    """Sessions with unread turns, oldest first, up to ``max_tokens`` of text.

    A very long session may be cut in the middle: the watermark then advances only as far as the
    last turn included, and the rest is read tomorrow.
    """
    quiet_before = now - timedelta(minutes=active_minutes)
    marks = [
        m
        for m in data.watermarks.list()
        if m.turns_total > m.turns_read
        and (m.last_turn_at is None or m.last_turn_at <= quiet_before)
    ]
    marks.sort(key=lambda m: (m.last_turn_at or now, m.session_id))
    batch: list[SessionMaterial] = []
    used = 0
    for mark in marks:
        turns = data.transcripts.list(
            {"session_id": mark.session_id, "index": {"$gte": mark.turns_read}}, [("index", 1)]
        )
        kept: list[TranscriptTurn] = []
        for turn in turns:
            cost = (len(turn.user) + len(turn.assistant)) // CHARS_PER_TOKEN + 1
            if used + cost > max_tokens and (kept or batch):
                break
            kept.append(turn)
            used += cost
        if kept:
            batch.append(SessionMaterial(mark.session_id, kept))
        if used >= max_tokens:
            break
    return batch


# --- phases -----------------------------------------------------------------------------------


@dataclass
class PhaseResult:
    name: str
    status: str = "done"  # done | skipped | error | budget
    summary: str = ""
    changed: int = 0
    tokens: int = 0
    error: str | None = None
    notes: list[str] = field(default_factory=list)  # what the phase proposed (shown on --dry-run)

    def to_doc(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "changed": self.changed,
            "tokens": self.tokens,
            "error": self.error,
        }


@dataclass
class PhaseContext:
    """What a phase gets: the data (the real one, or a dry-run overlay), the LLMs, the budget."""

    data: V2Data
    config: MemoryV2Settings
    material: list[SessionMaterial]
    budget: Budget
    cheap: LLMProvider | None = None
    reflect: LLMProvider | None = None
    embedder: Any | None = None
    memory: Any | None = None  # the v1 LongTermMemory, for the observation pipeline
    dry_run: bool = False
    clock: Callable[[], datetime] = utcnow
    state: dict[str, Any] = field(default_factory=dict)  # what one phase hands the next

    async def ask(
        self,
        which: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> str:
        """One LLM call (``"cheap"`` for the chat model, ``"reflect"`` for the advanced one).

        Charges the budget, strips a reasoning model's "thinking", and raises
        ``BudgetExceededError`` before the call when the night is out of budget.
        """
        llm = self.reflect if which == "reflect" and self.reflect is not None else self.cheap
        if llm is None:
            raise MemoryUnavailableError("a passada noturna não tem LLM configurado")
        self.budget.check()
        response = await llm.chat(
            [Message("system", system), Message("user", user)],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = strip_reasoning(response.message.content or "")
        usage = response.usage
        if usage is not None and usage.total_tokens:
            self.budget.charge(usage.total_tokens)
        else:
            self.budget.charge(
                estimate_tokens(system) + estimate_tokens(user) + estimate_tokens(text)
            )
        return text


@runtime_checkable
class Phase(Protocol):
    name: str
    # True for phases that read the sessions' material: the watermark waits for them.
    consumes_material: bool

    async def run(self, ctx: PhaseContext) -> PhaseResult: ...


def default_phases() -> list[Phase]:
    """The phases of a real night, in order (each task of the V2 adds its own)."""
    from brain.memory.v2.observe import ObservationPhase

    return [ObservationPhase()]


# --- the run ----------------------------------------------------------------------------------


@dataclass
class DreamReport:
    run: DreamRun | None
    results: list[PhaseResult] = field(default_factory=list)
    changes: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # --dry-run only
    sessions: int = 0
    turns: int = 0
    journal: str | None = None
    disabled: bool = False

    @property
    def status(self) -> str:
        return "disabled" if self.run is None else self.run.status

    def text(self) -> str:
        """The report as lines of Portuguese for the CLI."""
        if self.disabled:
            return "A Memória V2 está desligada (memory.v2.enabled)."
        lines = [f"Li {self.sessions} conversa(s), {self.turns} turno(s) novo(s)."]
        for r in self.results:
            tail = f": {r.error}" if r.error else (f": {r.summary}" if r.summary else "")
            lines.append(f"- {r.name} [{r.status}]{tail}")
            lines.extend(f"    {note}" for note in r.notes)
        if self.run is not None and self.run.dry_run:
            lines.append("Nada foi gravado (--dry-run). O que seria gravado:")
            if not self.changes:
                lines.append("    nada")
            for collection, change in sorted(self.changes.items()):
                parts = [f"{len(ids)} {kind}" for kind, ids in change.items() if ids]
                lines.append(f"    {collection}: {', '.join(parts)}")
        return "\n".join(lines)


class NightRunner:
    """Runs the phases over the sessions with something new."""

    def __init__(
        self,
        data: V2Data,
        *,
        config: MemoryV2Settings | None = None,
        phases: Sequence[Phase] | None = None,
        cheap: LLMProvider | None = None,
        reflect: LLMProvider | None = None,
        embedder: Any | None = None,
        memory: Any | None = None,
        clock: Callable[[], datetime] = utcnow,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.data = data
        self.config = config if config is not None else settings.memory.v2
        self.phases = list(phases) if phases is not None else default_phases()
        self.cheap = cheap
        self.reflect = reflect
        self.embedder = embedder
        self.memory = memory
        self.clock = clock
        self._monotonic = monotonic

    async def run(self, *, dry_run: bool = False) -> DreamReport:
        if not self.config.enabled:
            return DreamReport(run=None, disabled=True)
        night = self.config.night
        budget = Budget(night.max_tokens, night.max_minutes * 60, self._monotonic)
        overlay = OverlayDocStore(self.data.docs) if dry_run else None
        data = V2Data.over(overlay) if overlay is not None else self.data
        run = DreamRun(started_at=self.clock(), dry_run=dry_run)
        if not dry_run:
            data.runs.save(run)

        material = collect_material(
            self.data, self.clock(), max_tokens=int(night.max_tokens * MATERIAL_SHARE)
        )
        ctx = PhaseContext(
            data=data,
            config=self.config,
            material=material,
            budget=budget,
            cheap=self.cheap,
            reflect=self.reflect,
            embedder=self.embedder,
            memory=self.memory,
            dry_run=dry_run,
            clock=self.clock,
        )
        results = [await self._run_phase(phase, ctx) for phase in self.phases]

        run.phases = [r.to_doc() for r in results]
        run.tokens_used = budget.used_tokens
        run.sessions_read = len(material)
        run.finished_at = self.clock()
        failed = [r for r in results if r.status in ("error", "budget")]
        run.status = "partial" if failed else "done"
        run.error = "; ".join(f"{r.name}: {r.error or r.status}" for r in failed) or None

        report = DreamReport(
            run=run,
            results=results,
            sessions=len(material),
            turns=sum(len(m.turns) for m in material),
        )
        if overlay is not None:
            report.changes = overlay.changes()
        else:
            self._advance_watermarks(material, results)
            report.journal = self._write_journal(report)
            data.runs.save(run)
        self._log(report)
        return report

    async def _run_phase(self, phase: Phase, ctx: PhaseContext) -> PhaseResult:
        before = ctx.budget.used_tokens
        try:
            result = await phase.run(ctx)
        except BudgetExceededError:
            result = PhaseResult(phase.name, "budget", error="o orçamento da noite acabou")
        except (LLMError, MemoryUnavailableError) as exc:
            result = PhaseResult(phase.name, "error", error=str(exc))
        except Exception as exc:  # noqa: BLE001  (one phase must not take the night down)
            event_log.log(
                "error", where=f"night.{phase.name}", type=type(exc).__name__, message=str(exc)
            )
            result = PhaseResult(phase.name, "error", error=f"{type(exc).__name__}: {exc}")
        result.tokens = ctx.budget.used_tokens - before
        return result

    def _advance_watermarks(
        self, material: list[SessionMaterial], results: list[PhaseResult]
    ) -> None:
        """A session counts as read only if every phase that consumes the material finished."""
        consumers = {p.name for p in self.phases if p.consumes_material}
        if any(r.status in ("error", "budget") and r.name in consumers for r in results):
            return
        now = self.clock()
        for item in material:
            mark = self.data.watermarks.get(item.session_id)
            if mark is None:
                continue
            mark.turns_read = max(mark.turns_read, item.last_index + 1)
            mark.updated_at = now
            self.data.watermarks.save(mark)

    def _write_journal(self, report: DreamReport) -> str:
        lines = [f"Li {report.sessions} conversa(s), {report.turns} turno(s) novo(s)."]
        lines += [f"{r.name}: {r.summary or r.status}" for r in report.results]
        text = "\n".join(lines)
        assert report.run is not None
        self.data.journal.save(
            JournalEntry(
                "dream",
                text,
                day=self.clock().date().isoformat(),
                data={"run": report.run.id, "status": report.run.status},
                at=self.clock(),
            )
        )
        return text

    def _log(self, report: DreamReport) -> None:
        run = report.run
        assert run is not None
        event_log.log(
            "dream",
            run=run.id,
            dry_run=run.dry_run,
            status=run.status,
            sessions=report.sessions,
            turns=report.turns,
            tokens=run.tokens_used,
            phases={r.name: r.status for r in report.results},
        )
