"""Extract memories in the background, right after each reply (ADR-011, T-063).

Extracting only when the chat ends meant a wait on the way out, and lost everything if the
terminal was closed with Ctrl+C. Here each finished turn is handed to ``BackgroundExtractor.submit``
(which returns at once) and a worker extracts it while the user is reading and typing. What it
learns is available from the very next message. Nothing is printed during the chat; ``finish``
waits for what is left when the chat ends and returns one summary.

One extraction runs at a time. Turns that arrive meanwhile are batched into the next run, and the
last turn already seen goes along as context, so "e ela tem 3 anos" still knows who "ela" is.
Pure pleasantries ("ok", "valeu", "sim") are not worth an LLM call and are skipped.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from brain.core.session import Turn
from brain.eventlog import event_log
from brain.memory.extraction import ExtractionResult

# Words that, alone, never carry anything worth remembering.
_FILLER = frozenset(
    {
        "oi",
        "ola",
        "eae",
        "e",
        "ai",
        "opa",
        "bom",
        "boa",
        "dia",
        "tarde",
        "noite",
        "tchau",
        "ate",
        "logo",
        "sim",
        "nao",
        "ok",
        "okay",
        "certo",
        "beleza",
        "blz",
        "valeu",
        "obrigado",
        "obrigada",
        "brigado",
        "vlw",
        "tmj",
        "pode",
        "mesmo",
        "claro",
        "entendi",
        "legal",
        "show",
        "top",
        "uhum",
        "aham",
        "hum",
        "hmm",
        "tudo",
        "bem",
        "como",
        "vai",
        "voce",
        "ce",
        "td",
        "tb",
        "tambem",
        "kkk",
        "kkkk",
        "rs",
        "haha",
        "a",
        "o",
        "de",
        "que",
    }
)


def is_trivial(text: str) -> bool:
    """True for a message made only of greetings, thanks and acknowledgements."""
    flat = unicodedata.normalize("NFKD", text.casefold())
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", flat)
    return not words or (len(words) <= 5 and all(w in _FILLER for w in words))


@dataclass
class BackgroundSummary:
    runs: int = 0
    added: int = 0
    superseded: int = 0
    duplicates: int = 0
    profile_updated: bool = False
    error: str | None = None
    results: list[ExtractionResult] = field(default_factory=list)

    @property
    def saved_something(self) -> bool:
        return bool(self.added or self.superseded or self.profile_updated)


class BackgroundExtractor:
    """Hands finished turns to a worker task that extracts memories without blocking the chat."""

    def __init__(
        self,
        memory: object,
        session_id: Callable[[], str | None],
    ) -> None:
        self._memory = memory
        self._session_id = session_id
        self._pending: list[Turn] = []
        self._context: Turn | None = None  # the last turn already looked at
        self._task: asyncio.Task[None] | None = None
        self._results: list[ExtractionResult] = []

    def submit(self, turn: Turn) -> None:
        """Queue ``turn`` for extraction and return immediately."""
        if is_trivial(turn.user):
            self._context = turn  # not worth a call, but useful as context for the next one
            return
        self._pending.append(turn)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())

    async def _work(self) -> None:
        while self._pending:
            batch, self._pending = self._pending, []
            turns = ([self._context] if self._context else []) + batch
            try:
                result = await self._memory.extract(  # type: ignore[attr-defined]
                    turns, self._session_id(), 1 if self._context else 0
                )
            except Exception as exc:  # never lets a memory problem reach the chat
                event_log.log(
                    "error", where="memory.background", type=type(exc).__name__, message=str(exc)
                )
                result = ExtractionResult(error=str(exc))
            self._results.append(result)
            self._context = batch[-1]

    @property
    def busy(self) -> bool:
        return bool(self._pending) or (self._task is not None and not self._task.done())

    async def finish(self) -> BackgroundSummary:
        """Wait for what is still being extracted, then summarize everything that was kept."""
        while self.busy:
            if self._task is not None:
                await asyncio.shield(self._task)
        summary = BackgroundSummary(runs=len(self._results), results=list(self._results))
        for result in self._results:
            summary.added += len(result.added)
            summary.superseded += len(result.superseded)
            summary.duplicates += len(result.duplicates)
            summary.profile_updated = summary.profile_updated or result.profile is not None
            summary.error = summary.error or result.error
        return summary
