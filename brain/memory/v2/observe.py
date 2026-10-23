"""Phase 1 of the night: observation (ADR-017, sections 4 and 9).

Re-reads what the day said, with the **same pipeline as the v1 memory** (extract, filter secrets,
compare with what is stored, add / refresh / supersede), in two ways the per-turn extraction cannot:

- **Long messages are sliced, not cut.** The v1 extractor keeps the first 2000 characters of each
  message, so a pasted text or a long outburst lost its tail. Here a long message becomes slices
  (cut at the end of a sentence when possible, with a little overlap), each one extracted.
- **Windows over the day.** Facts that only show across several turns are easier to see with the
  whole conversation in view: the new turns go through the extractor in windows, with the last
  turns of the previous window as context.

Nothing new is written by this phase itself: facts go through ``MemoryExtractor``, with the
provenance (session and turn) of the turn they came from. On a dry run the extractor works over a
snapshot of the memories, so the real ones are not touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from brain.core.session import Turn
from brain.memory.background import is_trivial
from brain.memory.extraction import ExtractionResult, MemoryExtractor
from brain.memory.v2.night import PhaseContext, PhaseResult, SessionMaterial, estimate_tokens
from brain.memory.v2.overlay import SnapshotVectorStore

# Where the v1 extractor cuts a message (brain.memory.extraction._MAX_MESSAGE_CHARS).
LONG_MESSAGE_CHARS = 2000
SLICE_CHARS = 1800
SLICE_OVERLAP = 150
# The window of turns the extractor sees, and how many of the previous ones come along as context.
WINDOW_OVERLAP = 2
# A rough price of one extraction call besides the text (the prompt and the answer).
CALL_OVERHEAD_TOKENS = 1100
MAX_NOTES = 12

_SENTENCE_END = re.compile(r"[.!?…]\s|\n")


def chunk_text(text: str, size: int = SLICE_CHARS, overlap: int = SLICE_OVERLAP) -> list[str]:
    """Split ``text`` into slices of at most ``size`` characters.

    Nothing is lost: the slices cover the whole text, each one starting ``overlap`` characters
    before the previous one ended (so a sentence cut in two is whole in one of them). A slice
    ends at a sentence boundary when there is one in its last quarter, else at a space.
    """
    if size <= overlap:
        raise ValueError("size precisa ser maior que overlap")
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    slices: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            end = _boundary(text, start, end, size)
        slices.append(text[start:end].strip())
        if end >= len(text):
            break
        start = _word_start(text, max(end - overlap, start + 1))
    return [s for s in slices if s]


def _boundary(text: str, start: int, end: int, size: int) -> int:
    window_from = start + (size * 3) // 4
    cut = -1
    for match in _SENTENCE_END.finditer(text, window_from, end):
        cut = match.end()
    if cut != -1:
        return cut
    space = text.rfind(" ", window_from, end)
    return space + 1 if space != -1 else end


def _word_start(text: str, position: int) -> int:
    """Move ``position`` forward to the start of a word (never into the middle of one)."""
    if position <= 0 or position >= len(text):
        return position
    if text[position - 1].isspace():
        return position
    space = text.find(" ", position)
    return space + 1 if space != -1 else position


@dataclass
class _Tally:
    slices: int = 0
    windows: int = 0
    long_messages: int = 0
    added: int = 0
    duplicates: int = 0
    superseded: int = 0
    rejected: int = 0
    skipped_trivial: int = 0
    errors: list[str] = field(default_factory=list)

    def add(self, result: ExtractionResult) -> None:
        self.added += len(result.added)
        self.duplicates += len(result.duplicates)
        self.superseded += len(result.superseded)
        self.rejected += len(result.rejected)
        if result.error:
            self.errors.append(result.error)


class ObservationPhase:
    """Reads the day's turns again, slicing long messages, with the v1 pipeline."""

    name = "observe"
    consumes_material = True

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        memory = ctx.memory
        base = getattr(memory, "extractor", None)
        if memory is None or base is None:
            return PhaseResult(self.name, "skipped", "a memória de longo prazo está desligada")
        store = None
        extractor: MemoryExtractor = base
        if ctx.dry_run:  # the real pipeline over a copy: the real memories stay untouched
            store = SnapshotVectorStore.of(base.store, base.embedder.model_id)
            extractor = MemoryExtractor(base.llm, base.embedder, store, base.config, profiles=None)

        tally = _Tally()
        for item in ctx.material:
            await self._long_messages(ctx, extractor, item, tally)
            await self._windows(ctx, extractor, item, tally)

        summary = (
            f"{tally.long_messages} mensagem(ns) longa(s) em {tally.slices} fatia(s), "
            f"{tally.windows} janela(s); {tally.added} fato(s) novo(s), "
            f"{tally.superseded} atualizado(s), {tally.duplicates} já conhecido(s), "
            f"{tally.rejected} recusado(s)"
        )
        notes = (
            [f"guardaria: {m.text}" for m in store.added()[:MAX_NOTES]] if store is not None else []
        )
        status = "error" if tally.errors and not (tally.added or tally.duplicates) else "done"
        return PhaseResult(
            self.name,
            status,
            summary,
            changed=tally.added + tally.superseded,
            error=tally.errors[0] if status == "error" else None,
            notes=notes,
        )

    async def _long_messages(
        self, ctx: PhaseContext, extractor: MemoryExtractor, item: SessionMaterial, tally: _Tally
    ) -> None:
        for turn in item.turns:
            if len(turn.user) <= LONG_MESSAGE_CHARS:
                continue
            tally.long_messages += 1
            for piece in chunk_text(turn.user):
                tally.slices += 1
                await self._extract(
                    ctx, extractor, [Turn(piece, "", index=turn.index)], item.session_id, 0, tally
                )

    async def _windows(
        self, ctx: PhaseContext, extractor: MemoryExtractor, item: SessionMaterial, tally: _Tally
    ) -> None:
        size = max(extractor.config.max_turns, WINDOW_OVERLAP + 1)
        turns = [
            # her reply is left out when tools ran: it may quote what a tool returned
            Turn(t.user, "" if t.used_tools else t.assistant, index=t.index)
            for t in item.turns
        ]
        start = 0
        while start < len(turns):
            context = WINDOW_OVERLAP if start > 0 else 0
            window = turns[max(0, start - context) : start + size - context]
            fresh = window[context:]
            if all(is_trivial(t.user) for t in fresh):
                tally.skipped_trivial += 1
            else:
                tally.windows += 1
                await self._extract(ctx, extractor, window, item.session_id, context, tally)
            start += size - context

    async def _extract(
        self,
        ctx: PhaseContext,
        extractor: MemoryExtractor,
        turns: list[Turn],
        session_id: str,
        context: int,
        tally: _Tally,
    ) -> None:
        ctx.budget.check()
        text_tokens = sum(estimate_tokens(t.user) + estimate_tokens(t.assistant) for t in turns)
        result = await extractor.extract(turns, session_id, context)
        ctx.budget.charge(text_tokens + CALL_OVERHEAD_TOKENS)
        tally.add(result)
