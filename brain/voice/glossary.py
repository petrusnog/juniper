"""The vocabulary the transcriber is told to expect (ADR-034, section 4).

Whisper writes "mídia works" for "Media Works" because nothing tells it that a project of that name
exists. A short list of the names and terms that matter, handed to it as the ``prompt`` of each
transcription, moves it towards the right spelling for free (contextual biasing).

The list is small on purpose: the engine takes about 224 tokens of prompt, and a long one invites
it to write the prompt back on silence (`stt.is_prompt_echo` drops that). Terms come from the
configuration first (they win when the budget is short) and then from *sources*: callables that
return more names, such as the people registered on the brain. Sources run off the event loop,
at most once per ``ttl`` seconds, and a source that fails costs only its own terms.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from typing import Final

from brain.eventlog import event_log
from brain.voice.stt import normalise

LABEL: Final = "Vocabulário"
MAX_TERM_CHARS: Final = 60
FIRST_LOAD_SECONDS: Final = 1.0  # the very first utterance waits for the sources this long

Source = Callable[[], Iterable[str]]


class Glossary:
    def __init__(
        self,
        terms: Iterable[str] = (),
        *,
        char_budget: int = 500,
        ttl: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fixed = list(_clean(terms))
        self._sources: list[Source] = []
        self._budget = char_budget
        self._ttl = ttl
        self._clock = clock
        self._loaded_at: float | None = None
        self._dynamic: list[str] = []
        self._lock = asyncio.Lock()

    def add_source(self, source: Source) -> None:
        """Another place to ask for names (called off the event loop; may raise)."""
        self._sources.append(source)
        self._loaded_at = None  # ask it on the next prompt

    async def terms(self) -> list[str]:
        """The terms that fit the budget: configured ones first, no repeats (case, accents)."""
        await self._refresh()
        chosen: list[str] = []
        seen: set[str] = set()
        used = len(LABEL) + 3  # "<label>: " and the final "."
        for term in (*self._fixed, *self._dynamic):
            key = normalise(term)
            if not key or key in seen:
                continue
            cost = len(term) + 2
            if used + cost > self._budget:
                continue  # a shorter one later may still fit
            seen.add(key)
            chosen.append(term)
            used += cost
        return chosen

    async def prompt(self) -> str:
        """The hint to send with an utterance; empty when there is nothing to say."""
        terms = await self.terms()
        return f"{LABEL}: {', '.join(terms)}." if terms else ""

    async def _refresh(self) -> None:
        if not self._sources:
            return
        now = self._clock()
        if self._loaded_at is not None and now - self._loaded_at < self._ttl:
            return
        async with self._lock:
            if self._loaded_at is not None and self._clock() - self._loaded_at < self._ttl:
                return
            try:
                async with asyncio.timeout(FIRST_LOAD_SECONDS):
                    self._dynamic = await self._collect()
            except TimeoutError:
                event_log.log("error", where="voice.glossary", type="TimeoutError")
            self._loaded_at = self._clock()  # even on a timeout: do not ask again on every word

    async def _collect(self) -> list[str]:
        found: list[str] = []
        for source in self._sources:
            try:
                found.extend(await asyncio.to_thread(lambda s=source: list(_clean(s()))))
            except Exception as exc:  # noqa: BLE001 - a broken source must not silence the others
                event_log.log("error", where="voice.glossary", type=type(exc).__name__)
        return found


def _clean(terms: Iterable[str]) -> Iterable[str]:
    for term in terms:
        text = " ".join(str(term).split())
        if text and len(text) <= MAX_TERM_CHARS:
            yield text
