"""Runs the nightly pass for real: opens the database and the LLMs, runs, and closes everything.

Used by the server (the daily schedule) and by ``juniper memory dream``. It never raises for an
expected problem: a Memória V2 that is off or cannot run gives a report that says so.
"""

from __future__ import annotations

from brain.eventlog import event_log
from brain.memory.errors import MemoryUnavailableError
from brain.memory.v2 import require_v2
from brain.memory.v2.night import DreamReport, NightRunner, Phase


async def run_night(dry_run: bool = False, *, phases: list[Phase] | None = None) -> DreamReport:
    """One night. Raises ``MemoryUnavailableError`` (a message fit for the user) when the V2 is on
    but cannot run (no MongoDB); returns a ``disabled`` report when it is simply off."""
    from brain.config import settings

    if not settings.memory.v2.enabled:
        return DreamReport(run=None, disabled=True)
    require_v2()

    from brain.llm.router import LLMRouter
    from brain.memory.embeddings import open_embedding_provider
    from brain.memory.longterm import open_long_term_memory
    from brain.memory.v2 import open_v2_data
    from brain.memory.v2.reflection_llm import build_reflection_llm

    data = open_v2_data()
    cheap = LLMRouter()
    reflect = build_reflection_llm()
    memory = None
    try:
        memory = open_long_term_memory(cheap)
        embedder = memory.embedder if memory is not None else open_embedding_provider()
        runner = NightRunner(
            data, phases=phases, cheap=cheap, reflect=reflect, embedder=embedder, memory=memory
        )
        return await runner.run(dry_run=dry_run)
    finally:
        if memory is not None:
            memory.close()
        data.close()
        await cheap.aclose()
        if reflect is not None:
            await reflect.aclose()


async def run_night_job() -> None:
    """The scheduled job: a failure is logged, never raised (the server keeps running)."""
    try:
        await run_night()
    except MemoryUnavailableError as exc:
        event_log.log("error", where="night.run", type="MemoryUnavailableError", message=str(exc))


async def run_teach(text: str, topic: str):  # type: ignore[no-untyped-def]
    """``juniper study add``: teach her from a text. Raises ``MemoryUnavailableError`` when the
    V2 cannot run and ``TeachError`` for a text or topic she will not take."""
    from brain.config import settings
    from brain.llm.router import LLMRouter
    from brain.memory.embeddings import open_embedding_provider
    from brain.memory.v2 import open_v2_data
    from brain.memory.v2.night import Budget, PhaseContext
    from brain.memory.v2.reflection_llm import build_reflection_llm
    from brain.memory.v2.teach import check_text, teach

    check_text(text)  # refuse a big text or a secret before opening anything
    require_v2()
    night = settings.memory.v2.night
    data = open_v2_data()
    cheap = LLMRouter()
    reflect = build_reflection_llm()
    try:
        ctx = PhaseContext(
            data=data,
            config=settings.memory.v2,
            material=[],
            budget=Budget(night.max_tokens, night.max_minutes * 60),
            cheap=cheap,
            reflect=reflect,
            embedder=open_embedding_provider(),
        )
        return await teach(ctx, text, topic)
    finally:
        data.close()
        await cheap.aclose()
        if reflect is not None:
            await reflect.aclose()
