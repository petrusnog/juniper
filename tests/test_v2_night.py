"""O algoritmo de sonho (T-103): coleta, orçamento, idempotência, dry-run, diário e agendamento."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from brain.clients import server as server_module
from brain.clients.server import BrainServer, run_server
from brain.config import MemoryV2Settings, V2NightSettings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.session import Turn
from brain.eventlog import event_log
from brain.llm.base import LLMProvider
from brain.llm.errors import LLMUnavailableError
from brain.llm.types import LLMResponse, Message, TokenUsage
from brain.memory.errors import MemoryUnavailableError
from brain.memory.v2.models import (
    Belief,
    DreamRun,
    JournalEntry,
    TranscriptTurn,
    Watermark,
)
from brain.memory.v2.night import (
    Budget,
    BudgetExceededError,
    NightRunner,
    PhaseContext,
    PhaseResult,
    collect_material,
    estimate_tokens,
    strip_reasoning,
)
from brain.memory.v2.overlay import OverlayDocStore
from brain.memory.v2.service import run_night, run_night_job
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.memory.v2.transcripts import TranscriptWriter
from brain.scheduler import run_daily
from tests.test_agent import FakeProvider, say

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
CFG = MemoryV2Settings(enabled=True)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Mono:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def data() -> V2Data:
    return V2Data.over(InMemoryDocStore())


@pytest.fixture
def clock() -> Clock:
    return Clock()


def add_session(
    data: V2Data,
    session: str,
    count: int,
    *,
    last_at: datetime,
    read: int = 0,
    text: str = "mensagem",
) -> None:
    """A session with ``count`` turns in the transcript, ``read`` of them already read."""
    for i in range(count):
        data.transcripts.save(
            TranscriptTurn(session, i, f"{text} {i}", f"resposta {i}", at=last_at)
        )
    data.watermarks.save(Watermark(session, read, count, last_at))


class Phase:
    """A test phase: records its calls and can write, fail, or run out of budget."""

    def __init__(
        self,
        name: str,
        *,
        consumes: bool = True,
        raises: BaseException | None = None,
        write: Callable[[PhaseContext], None] | None = None,
        spend: int = 0,
        notes: list[str] | None = None,
    ) -> None:
        self.name = name
        self.consumes_material = consumes
        self.raises = raises
        self.write = write
        self.spend = spend
        self.notes = notes or []
        self.seen: list[PhaseContext] = []

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        self.seen.append(ctx)
        if self.raises is not None:
            raise self.raises
        ctx.budget.charge(self.spend)
        if self.write is not None:
            self.write(ctx)
        return PhaseResult(self.name, summary=f"{self.name} ok", changed=1, notes=self.notes)


def runner(
    data: V2Data, phases: list[Phase], clock: Clock, mono: Mono | None = None, **kw: Any
) -> NightRunner:
    return NightRunner(
        data,
        config=kw.pop("config", CFG),
        phases=phases,
        clock=clock,
        monotonic=mono or Mono(),
        **kw,
    )


# --- small pieces -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ('<think>vou pensar</think>{"a": 1}', '{"a": 1}'),
        ('<thinking>hmm\nmais</thinking>\n\n{"a": 1}', '{"a": 1}'),
        ("<THINK>x</THINK>ok", "ok"),
        ('{"a": 1}', '{"a": 1}'),
        ("  texto  ", "texto"),
    ],
)
def test_the_thinking_of_a_reasoning_model_is_stripped(raw: str, clean: str) -> None:
    assert strip_reasoning(raw) == clean


def test_token_estimates_grow_with_the_text() -> None:
    assert estimate_tokens("") == 1 and estimate_tokens("a" * 400) == 101


def test_the_budget_runs_out_by_tokens_or_by_time() -> None:
    mono = Mono()
    budget = Budget(max_tokens=100, max_seconds=60, monotonic=mono)
    budget.check()
    budget.charge(99)
    budget.check()
    budget.charge(1)
    assert budget.exhausted
    with pytest.raises(BudgetExceededError):
        budget.check()

    slow = Budget(max_tokens=10**9, max_seconds=60, monotonic=mono)
    mono.t = 61
    assert slow.exhausted and slow.elapsed == 61


# --- the durable transcript -------------------------------------------------------------------


def test_the_transcript_writer_stores_the_turn_and_the_session_watermark(
    data: V2Data, clock: Clock
) -> None:
    writer = TranscriptWriter(data, clock)

    writer.write("s1", Turn("oi", "olá", index=0))
    writer.write("s1", Turn("e aí", "tudo bem", index=1, used_tools=True))
    writer.write("s1", Turn("e aí", "tudo bem", index=1, used_tools=True))  # written twice

    turns = data.transcripts.list({"session_id": "s1"}, [("index", 1)])
    assert [(t.index, t.user, t.used_tools) for t in turns] == [(0, "oi", False), (1, "e aí", True)]
    mark = data.watermarks.get("s1")
    assert mark is not None and (mark.turns_total, mark.turns_read, mark.last_turn_at) == (2, 0, T0)


def test_a_turn_without_an_index_is_not_written(data: V2Data) -> None:
    TranscriptWriter(data).write("s1", Turn("oi", "olá"))
    assert data.transcripts.count() == 0 and data.watermarks.count() == 0


async def test_the_agent_hands_each_turn_to_the_sink() -> None:
    sunk: list[tuple[str, Turn]] = []
    agent = Agent(
        FakeProvider([say("Oi!"), say("Anotado.")]),
        Dispatcher(conversational=True),
        turn_sink=lambda session_id, turn: sunk.append((session_id, turn)),
    )

    await agent.run("oi")
    await agent.run("uso ZSH")

    assert [(s, t.user, t.index) for s, t in sunk] == [
        (agent.session.id, "oi", 0),
        (agent.session.id, "uso ZSH", 1),
    ]


async def test_a_failing_sink_never_ends_the_chat() -> None:
    def boom(session_id: str, turn: Turn) -> None:
        raise RuntimeError("banco fora")

    agent = Agent(FakeProvider([say("Oi!")]), Dispatcher(conversational=True), turn_sink=boom)

    assert await agent.run("oi") == "Oi!"
    logged = [
        json.loads(line)
        for path in event_log.directory.glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert any(e["event"] == "error" and e.get("where") == "turn_sink" for e in logged)


# --- collecting what is new -------------------------------------------------------------------


def test_only_quiet_sessions_with_unread_turns_are_collected_oldest_first(data: V2Data) -> None:
    add_session(data, "novo", 3, last_at=T0 - timedelta(hours=2))
    add_session(data, "velho", 2, last_at=T0 - timedelta(days=1))
    add_session(data, "ativo", 4, last_at=T0 - timedelta(minutes=5))  # still a conversation
    add_session(data, "lido", 3, last_at=T0 - timedelta(days=2), read=3)  # nothing new

    batch = collect_material(data, T0, max_tokens=10_000, active_minutes=30)

    assert [m.session_id for m in batch] == ["velho", "novo"]


def test_by_default_every_unread_message_enters_even_one_from_a_second_ago(data: V2Data) -> None:
    add_session(data, "agora", 2, last_at=T0 - timedelta(seconds=1))
    add_session(data, "agorinha", 3, last_at=T0 - timedelta(minutes=5))
    add_session(data, "lido", 3, last_at=T0 - timedelta(days=2), read=3)

    batch = collect_material(data, T0, max_tokens=10_000)

    assert [m.session_id for m in batch] == ["agorinha", "agora"]  # oldest first


def test_a_conversation_read_halfway_continues_where_it_stopped_without_repeating(
    data: V2Data,
) -> None:
    add_session(data, "s1", 6, last_at=T0 - timedelta(seconds=2), read=2)
    (first,) = collect_material(data, T0, max_tokens=10_000)
    assert [t.index for t in first.turns] == [2, 3, 4, 5]

    mark = data.watermarks.get("s1")
    assert mark is not None
    mark.turns_read = first.last_index + 1  # what the dream does after reading
    data.watermarks.save(mark)
    for i in (6, 7):
        data.transcripts.save(TranscriptTurn("s1", i, f"mensagem {i}", "ok", at=T0))
    mark.turns_total = 8
    data.watermarks.save(mark)

    (second,) = collect_material(data, T0, max_tokens=10_000)
    assert [t.index for t in second.turns] == [6, 7]


async def test_the_runner_uses_quiet_minutes_from_the_configuration(data: V2Data) -> None:
    add_session(data, "ativo", 2, last_at=T0 - timedelta(minutes=5))
    for minutes, expected in ((0, 1), (30, 0)):
        config = MemoryV2Settings(enabled=True, night=V2NightSettings(quiet_minutes=minutes))
        runner = NightRunner(data, config=config, phases=[], clock=lambda: T0)
        report = await runner.run(dry_run=True)
        assert report.sessions == expected, minutes


def test_the_default_budget_is_300k_tokens_and_45_minutes() -> None:
    night = V2NightSettings()
    assert (night.max_tokens, night.max_minutes, night.quiet_minutes) == (300_000, 45, 0)


def test_only_the_turns_after_the_watermark_are_read(data: V2Data) -> None:
    add_session(data, "s1", 6, last_at=T0 - timedelta(hours=3), read=4)

    (item,) = collect_material(data, T0, max_tokens=10_000)

    assert [t.index for t in item.turns] == [4, 5]
    assert (item.first_index, item.last_index) == (4, 5)
    assert "[t4] Usuário: mensagem 4" in item.material().text


def test_a_long_session_is_cut_at_the_token_cap_and_the_rest_waits(data: V2Data) -> None:
    add_session(
        data, "s1", 10, last_at=T0 - timedelta(hours=3), text="x" * 400
    )  # ~200 tokens a turn

    (item,) = collect_material(data, T0, max_tokens=500)

    assert 1 <= len(item.turns) < 10 and item.turns[0].index == 0


def test_a_single_turn_bigger_than_the_cap_is_still_read(data: V2Data) -> None:
    add_session(data, "s1", 1, last_at=T0 - timedelta(hours=3), text="y" * 4000)
    (item,) = collect_material(data, T0, max_tokens=10)
    assert len(item.turns) == 1


def test_a_session_with_no_last_turn_time_counts_as_quiet(data: V2Data) -> None:
    data.transcripts.save(TranscriptTurn("s1", 0, "oi", at=T0))
    data.watermarks.save(Watermark("s1", 0, 1, None))
    assert len(collect_material(data, T0, max_tokens=1000)) == 1


# --- the run ----------------------------------------------------------------------------------


async def test_a_disabled_v2_does_nothing_and_touches_nothing(data: V2Data, clock: Clock) -> None:
    phase = Phase("p")
    add_session(data, "s1", 2, last_at=T0 - timedelta(hours=3))

    report = await runner(data, [phase], clock, config=MemoryV2Settings(enabled=False)).run()

    assert report.disabled and report.status == "disabled" and phase.seen == []
    assert data.runs.count() == 0 and data.journal.count() == 0
    assert data.watermarks.get("s1").turns_read == 0  # type: ignore[union-attr]
    assert "desligada" in report.text()


async def test_the_phases_run_in_order_and_the_run_is_recorded(data: V2Data, clock: Clock) -> None:
    order: list[str] = []
    first = Phase("observe", write=lambda c: order.append("observe"), spend=100)
    second = Phase("reflect", write=lambda c: order.append("reflect"), spend=50)
    add_session(data, "s1", 3, last_at=T0 - timedelta(hours=3))

    report = await runner(data, [first, second], clock).run()

    assert order == ["observe", "reflect"] and report.status == "done"
    assert (report.sessions, report.turns) == (1, 3)
    (run,) = data.runs.list()
    assert run.status == "done" and run.finished_at == T0 and run.dry_run is False
    assert (run.tokens_used, run.sessions_read) == (150, 1)
    assert [p["name"] for p in run.phases] == ["observe", "reflect"] and run.phases[0][
        "tokens"
    ] == 100


async def test_the_night_writes_a_journal_entry_and_a_log_event(data: V2Data, clock: Clock) -> None:
    add_session(data, "s1", 2, last_at=T0 - timedelta(hours=3))

    report = await runner(data, [Phase("observe")], clock).run()

    (entry,) = data.journal.list()
    assert (
        entry.kind == "dream" and entry.day == "2026-10-22" and "observe: observe ok" in entry.text
    )
    assert report.journal == entry.text
    events = [
        json.loads(line)
        for path in event_log.directory.glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    (dream,) = [e for e in events if e["event"] == "dream"]
    assert (
        dream["status"] == "done"
        and dream["sessions"] == 1
        and dream["phases"] == {"observe": "done"}
    )


async def test_the_watermark_advances_only_after_a_clean_night_and_a_rerun_reads_nothing(
    data: V2Data, clock: Clock
) -> None:
    add_session(data, "s1", 4, last_at=T0 - timedelta(hours=3))
    phase = Phase("observe")

    first = await runner(data, [phase], clock).run()
    second = await runner(data, [phase], clock).run()

    assert data.watermarks.get("s1").turns_read == 4  # type: ignore[union-attr]
    assert first.turns == 4 and second.turns == 0 and second.sessions == 0


async def test_a_failing_phase_that_consumes_the_material_keeps_the_watermark_back(
    data: V2Data, clock: Clock
) -> None:
    add_session(data, "s1", 4, last_at=T0 - timedelta(hours=3))

    report = await runner(data, [Phase("observe", raises=ValueError("quebrou"))], clock).run()

    assert report.status == "partial" and report.results[0].status == "error"
    assert "ValueError: quebrou" in (report.results[0].error or "")
    assert data.watermarks.get("s1").turns_read == 0  # type: ignore[union-attr]
    retry = await runner(data, [Phase("observe")], clock).run()  # tomorrow it is read again
    assert retry.turns == 4 and data.watermarks.get("s1").turns_read == 4  # type: ignore[union-attr]


async def test_a_failing_phase_that_does_not_consume_material_does_not_block_it(
    data: V2Data, clock: Clock
) -> None:
    add_session(data, "s1", 2, last_at=T0 - timedelta(hours=3))
    phases = [Phase("observe"), Phase("study", consumes=False, raises=RuntimeError("sem tema"))]

    report = await runner(data, phases, clock).run()

    assert report.status == "partial" and report.results[1].status == "error"
    assert data.watermarks.get("s1").turns_read == 2  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "error", [LLMUnavailableError("fora do ar"), MemoryUnavailableError("sem banco")]
)
async def test_expected_failures_become_a_typed_result_and_later_phases_still_run(
    data: V2Data, clock: Clock, error: Exception
) -> None:
    later = Phase("later", consumes=False)

    report = await runner(data, [Phase("a", raises=error), later], clock).run()

    assert report.results[0].status == "error" and str(error) in (report.results[0].error or "")
    assert len(later.seen) == 1 and report.status == "partial"
    assert data.runs.list()[0].status == "partial" and "a:" in (data.runs.list()[0].error or "")


async def test_a_phase_that_runs_out_of_budget_stops_and_the_rest_waits(
    data: V2Data, clock: Clock
) -> None:
    add_session(data, "s1", 2, last_at=T0 - timedelta(hours=3))
    tiny = MemoryV2Settings(enabled=True, night=V2NightSettings(max_tokens=1000, max_minutes=5))

    async def overspend(ctx: PhaseContext) -> PhaseResult:
        ctx.budget.charge(2000)
        ctx.budget.check()
        return PhaseResult("reflect")

    phase = Phase("reflect")
    phase.run = overspend  # type: ignore[method-assign]

    report = await runner(data, [phase], clock, config=tiny).run()

    assert report.results[0].status == "budget" and report.status == "partial"
    assert data.watermarks.get("s1").turns_read == 0  # type: ignore[union-attr]


async def test_the_time_budget_also_stops_the_night(data: V2Data, clock: Clock) -> None:
    mono = Mono()

    async def slow(ctx: PhaseContext) -> PhaseResult:
        mono.t = 10_000
        ctx.budget.check()
        return PhaseResult("x")

    phase = Phase("x")
    phase.run = slow  # type: ignore[method-assign]
    report = await runner(data, [phase], clock, mono).run()
    assert report.results[0].status == "budget"


async def test_material_is_capped_by_a_share_of_the_night_budget(
    data: V2Data, clock: Clock
) -> None:
    add_session(data, "s1", 40, last_at=T0 - timedelta(hours=3), text="z" * 400)
    small = MemoryV2Settings(enabled=True, night=V2NightSettings(max_tokens=2000, max_minutes=5))
    phase = Phase("observe")

    await runner(data, [phase], clock, config=small).run()

    (ctx,) = phase.seen
    assert sum(len(m.turns) for m in ctx.material) < 40
    mark = data.watermarks.get("s1")
    assert mark is not None and 0 < mark.turns_read < 40  # it advanced only as far as it read


# --- dry run ----------------------------------------------------------------------------------


def snapshot(docs: InMemoryDocStore) -> dict[str, dict[str, Any]]:
    # an empty collection is only what a read leaves behind, not a write
    return {c: dict(sorted(d.items())) for c, d in sorted(docs._data.items()) if d}


async def test_a_dry_run_runs_the_real_phases_but_writes_nothing(clock: Clock) -> None:
    docs = InMemoryDocStore()
    data = V2Data.over(docs)
    add_session(data, "s1", 3, last_at=T0 - timedelta(hours=3))
    before = snapshot(docs)

    def write_belief(ctx: PhaseContext) -> None:
        ctx.data.beliefs.save(Belief("Acho que o Petrus se cobra demais."))
        ctx.data.journal.save(JournalEntry("note", "uma nota"))

    phase = Phase("reflect", write=write_belief, notes=["proporia 1 crença"])

    report = await runner(data, [phase], clock).run(dry_run=True)

    assert snapshot(docs) == before  # not even the watermark or the run record
    assert data.runs.count() == 0 and data.beliefs.count() == 0
    assert report.run is not None and report.run.dry_run is True and report.journal is None
    assert report.changes["beliefs"]["added"] and report.changes["journal"]["added"]
    text = report.text()
    assert (
        "Nada foi gravado (--dry-run)" in text
        and "proporia 1 crença" in text
        and "beliefs: 1 added" in text
    )


async def test_a_dry_run_phase_sees_its_own_writes(clock: Clock) -> None:
    data = V2Data.over(InMemoryDocStore())
    seen: list[int] = []

    def write_then_read(ctx: PhaseContext) -> None:
        ctx.data.beliefs.save(Belief("Acho que o Petrus se cobra demais."))
        seen.append(ctx.data.beliefs.count())

    await runner(data, [Phase("p", write=write_then_read)], clock).run(dry_run=True)
    assert seen == [1] and data.beliefs.count() == 0


def test_the_overlay_reads_the_base_and_writes_only_to_itself() -> None:
    base = InMemoryDocStore()
    base.put("c", {"_id": "a", "v": 1, "n": 1})
    base.put("c", {"_id": "b", "v": 1, "n": 2})
    over = OverlayDocStore(base)

    over.put("c", {"_id": "new", "v": 9, "n": 3})
    assert over.update("c", "a", {"v": 2}) is True
    assert over.delete("c", "b") is True and over.delete("c", "nope") is False

    assert [d["_id"] for d in over.find("c", None, [("n", 1)])] == ["a", "new"]
    assert over.get("c", "a")["v"] == 2 and over.get("c", "b") is None  # type: ignore[index]
    assert over.count("c", {"v": 9}) == 1
    assert (
        base.get("c", "a")["v"] == 1
        and base.get("c", "b") is not None
        and base.get("c", "new") is None
    )  # type: ignore[index]
    assert over.changes() == {"c": {"added": ["new"], "updated": ["a"], "deleted": ["b"]}}
    assert over.delete_many("c", {"v": 9}) == 1 and over.get("c", "new") is None


def test_the_overlay_checks_versions_against_what_it_sees() -> None:
    base = InMemoryDocStore()
    base.put("p", {"_id": "current", "version": 1})
    over = OverlayDocStore(base)
    assert over.put_if_version("p", {"_id": "current", "version": 2}, 1) is True
    assert over.put_if_version("p", {"_id": "current", "version": 3}, 1) is False
    assert over.put_if_version("p", {"_id": "other", "version": 1}, None) is True
    assert base.get("p", "current") == {"_id": "current", "version": 1}


def test_unchanged_documents_are_not_reported_as_updated() -> None:
    base = InMemoryDocStore()
    base.put("c", {"_id": "a", "v": 1})
    over = OverlayDocStore(base)
    over.put("c", {"_id": "a", "v": 1})
    assert over.changes() == {}


# --- asking the LLM ---------------------------------------------------------------------------


class Say(LLMProvider):
    name = "say"  # type: ignore[assignment]

    def __init__(self, text: str, usage: TokenUsage | None = None) -> None:
        self.text, self.usage, self.calls = text, usage, 0

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(Message("assistant", self.text), "m", "say", self.usage)

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


def context(**kw: Any) -> PhaseContext:
    return PhaseContext(
        data=V2Data.over(InMemoryDocStore()),
        config=CFG,
        material=[],
        budget=Budget(10_000, 600),
        **kw,
    )


async def test_ask_strips_thinking_and_charges_the_reported_tokens() -> None:
    ctx = context(cheap=Say('<think>hmm</think>{"ok": true}', TokenUsage(10, 5, 15)))
    assert await ctx.ask("cheap", "sistema", "usuário") == '{"ok": true}'
    assert ctx.budget.used_tokens == 15


async def test_ask_estimates_the_cost_when_the_provider_reports_none() -> None:
    ctx = context(cheap=Say("a" * 400))
    await ctx.ask("cheap", "s" * 400, "u" * 400)
    assert ctx.budget.used_tokens == 101 * 3


async def test_the_reflection_model_is_used_when_there_is_one_and_the_chat_model_otherwise() -> (
    None
):
    cheap, reflect = Say("barato"), Say("avançado")
    assert await context(cheap=cheap, reflect=reflect).ask("reflect", "s", "u") == "avançado"
    assert (
        await context(cheap=cheap).ask("reflect", "s", "u") == "barato"
    )  # falls back to the chat's
    assert await context(cheap=cheap, reflect=reflect).ask("cheap", "s", "u") == "barato"


async def test_ask_refuses_to_call_when_the_budget_is_gone_and_when_there_is_no_llm() -> None:
    llm = Say("x")
    ctx = context(cheap=llm)
    ctx.budget.charge(10_000)
    with pytest.raises(BudgetExceededError):
        await ctx.ask("cheap", "s", "u")
    assert llm.calls == 0
    with pytest.raises(MemoryUnavailableError):
        await context().ask("cheap", "s", "u")


# --- the service, the job and the schedule ----------------------------------------------------


async def test_the_service_reports_that_a_disabled_v2_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brain.config import settings

    monkeypatch.setattr(settings.memory.v2, "enabled", False)
    report = await run_night()
    assert report.disabled and "desligada" in report.text()


async def test_enabling_the_v2_without_mongo_is_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brain.config import settings

    monkeypatch.setattr(settings.memory.v2, "enabled", True)
    monkeypatch.setattr(settings.memory.session, "store", "file")
    with pytest.raises(MemoryUnavailableError, match="MongoDB"):
        await run_night()


async def test_the_scheduled_job_logs_a_failure_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing(*a: Any, **k: Any) -> Any:
        raise MemoryUnavailableError("sem banco")

    monkeypatch.setattr("brain.memory.v2.service.run_night", failing)
    await run_night_job()  # does not raise
    logged = [
        json.loads(line)
        for path in event_log.directory.glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert any(e["event"] == "error" and e.get("where") == "night.run" for e in logged)


async def test_run_daily_awaits_a_coroutine_job_and_survives_its_failure() -> None:
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)
        raise RuntimeError("falhou")

    task = asyncio.create_task(run_daily("v2_night", "03:00", job))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [1]
    logged = [
        json.loads(line)
        for path in event_log.directory.glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert any(e.get("where") == "scheduler.v2_night" for e in logged)


async def test_the_server_schedules_the_night_at_the_configured_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brain.config import settings
    from tests.test_server import TOKEN, RecordingHandler, wait_until

    scheduled: list[tuple[str, str, bool]] = []

    async def fake_run_daily(
        name: str, hhmm: str, job: Callable[..., Awaitable[Any]], *, catch_up: bool = True
    ) -> None:
        scheduled.append((name, hhmm, catch_up))
        await asyncio.Event().wait()

    monkeypatch.setattr(server_module, "run_daily", fake_run_daily)
    monkeypatch.setattr(server_module, "v2_enabled", lambda: True)
    monkeypatch.setattr(settings.memory.v2.night, "at", "04:15")
    server = BrainServer("127.0.0.1", 0, TOKEN, RecordingHandler())
    task = asyncio.create_task(run_server(server))
    await wait_until(lambda: server.port != 0 and len(scheduled) == 2)

    os.kill(os.getpid(), signal.SIGTERM)
    async with asyncio.timeout(2):
        await task

    assert ("v2_night", "04:15", False) in scheduled and scheduled[0][0] == "log_rotation"


async def test_the_server_does_not_schedule_the_night_when_the_v2_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_server import TOKEN, RecordingHandler, wait_until

    scheduled: list[str] = []

    async def fake_run_daily(
        name: str, hhmm: str, job: Callable[..., Awaitable[Any]], *, catch_up: bool = True
    ) -> None:
        scheduled.append(name)
        await asyncio.Event().wait()

    monkeypatch.setattr(server_module, "run_daily", fake_run_daily)
    monkeypatch.setattr(server_module, "v2_enabled", lambda: False)
    server = BrainServer("127.0.0.1", 0, TOKEN, RecordingHandler())
    task = asyncio.create_task(run_server(server))
    await wait_until(lambda: server.port != 0 and scheduled)

    os.kill(os.getpid(), signal.SIGTERM)
    async with asyncio.timeout(2):
        await task

    assert scheduled == ["log_rotation"]


def test_a_stale_dream_run_record_round_trips() -> None:
    run = DreamRun(status="partial", error="x")
    assert DreamRun.from_doc(run.to_doc()).status == "partial"


# --- the command ------------------------------------------------------------------------------


def test_the_dream_command_runs_the_night_and_prints_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.memory.v2.night import DreamReport

    calls: list[bool] = []

    async def fake(dry_run: bool = False, **kw: Any) -> DreamReport:
        calls.append(dry_run)
        run = DreamRun(status="done", dry_run=dry_run)
        return DreamReport(run, [PhaseResult("observe", summary="3 fatos")], sessions=2, turns=9)

    monkeypatch.setattr("brain.memory.v2.service.run_night", fake)
    runner_ = CliRunner()

    plain = runner_.invoke(app, ["memory", "dream"])
    dry = runner_.invoke(app, ["memory", "dream", "--dry-run"])

    assert plain.exit_code == 0 and "Li 2 conversa(s), 9 turno(s) novo(s)." in plain.output
    assert "observe [done]: 3 fatos" in plain.output
    assert dry.exit_code == 0 and "Nada foi gravado (--dry-run)" in dry.output
    assert calls == [False, True]


def test_the_dream_command_says_why_it_cannot_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from brain.clients.cli import app

    async def refuse(**kw: Any) -> Any:
        raise MemoryUnavailableError("A Memória V2 guarda tudo no MongoDB.")

    monkeypatch.setattr("brain.memory.v2.service.run_night", refuse)
    result = CliRunner().invoke(app, ["memory", "dream"])
    assert result.exit_code == 1 and "MongoDB" in result.output
