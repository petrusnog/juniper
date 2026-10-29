"""Evidence of what leaked into her speech, for the investigation (T-165).

``LeakGuard`` (``core/leak.py``) cuts the leaks it knows; this module looks for the ones it does
not, and records both. It never changes what is said: it only reads the model's raw text (before
the guard) and the delivered text (after it), and tells what in them looks like machinery.

The rules are deliberately broad (false positives are fine here: a person reads the records),
and one of them knows no format at all: ``prompt_echo`` finds a run of words copied from a system
block sent in that same call (the persona, the profile, the memories, the channel note, the docs),
which is how a leak in a shape nobody listed yet shows up.

Pure: no I/O. The agent builds the record; whoever stores it is injected (``leak_sink``).
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import brain

# The excerpt of a hit kept in the record: enough to recognize it, never the whole answer.
EXCERPT_CHARS = 160
MAX_HITS = 20
MAX_ECHOES = 3

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Tool-call markup the provider failed to parse (DeepSeek's DSML, XML-style calls).
    (
        "tool_markup",
        re.compile(r"DSML|</?(?:function|parameters?|tool_calls?|invoke)\b", re.IGNORECASE),
    ),
    # Chat-template tokens (<|im_start|>, the fullwidth bars of DeepSeek).
    ("special_token", re.compile(r"<\|[^|\n]{1,40}\|>|<｜|｜>")),
    # Any XML-like tag: the context blocks are tags (<conhecimento>, <memorias>...).
    ("tag", re.compile(r"</?[A-Za-z_][\w-]{0,30}(?:\s[^<>\n]{0,80})?/?>")),
    # An answer that opens with a bracket is closing (or opening) a system note.
    ("bracket_start", re.compile(r"\A\s*[\[\]]")),
    # A line that speaks as a role of the chat.
    (
        "role_prefix",
        re.compile(
            r"^[^\w\n]*(?:mensagem d[eo] sistema|sistema|system|assistant|assistente|user"
            r"|usu[aá]ri[oa]|tool|ferramenta)\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # An instruction meant for the model, never for the person.
    (
        "instruction",
        re.compile(
            r"\bresponda (?:como|usando|com base)\b|\bvoc[eê] [ée] a juniper\b"
            r"|\bn[aã]o mencione\b|\bno contexto e nas mem[oó]rias\b",
            re.IGNORECASE,
        ),
    ),
)

_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class Hit:
    rule: str
    where: str  # "delivered" (the person saw it) or "raw" (only the model wrote it)
    at: int  # character offset in that text
    excerpt: str


@dataclass(frozen=True)
class Block:
    """A system message of the call: what it is and how big, never its content."""

    kind: str  # persona, profile, memories, docs, channel, system
    chars: int


def _excerpt(text: str, start: int, end: int) -> str:
    begin = max(0, start - 30)
    return text[begin : min(len(text), max(end, start + 1) + 30)][:EXCERPT_CHARS]


def _rule_hits(text: str, where: str) -> list[Hit]:
    hits: list[Hit] = []
    for name, pattern in RULES:
        for match in pattern.finditer(text):
            hits.append(Hit(name, where, match.start(), _excerpt(text, match.start(), match.end())))
            if len(hits) >= MAX_HITS:
                return hits
    return hits


def _shingles(sources: Sequence[str], size: int) -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    for source in sources:
        words = _WORD.findall(source.lower())
        found.update(tuple(words[i : i + size]) for i in range(len(words) - size + 1))
    return found


def _echo_hits(text: str, where: str, shingles: set[tuple[str, ...]], size: int) -> list[Hit]:
    """Runs of ``size`` words of ``text`` that also appear in a system block."""
    if not shingles:
        return []
    matches = list(_WORD.finditer(text))
    words = [m.group().lower() for m in matches]
    hits: list[Hit] = []
    i = 0
    while i <= len(words) - size and len(hits) < MAX_ECHOES:
        if tuple(words[i : i + size]) not in shingles:
            i += 1
            continue
        end = i + size
        while end < len(words) and tuple(words[end - size + 1 : end + 1]) in shingles:
            end += 1  # the whole copied run, not just its first window
        start, stop = matches[i].start(), matches[end - 1].end()
        hits.append(Hit("prompt_echo", where, start, text[start:stop][:EXCERPT_CHARS]))
        i = end
    return hits


def detect(
    raw: str, delivered: str, prompt_sources: Sequence[str], *, echo_words: int
) -> list[Hit]:
    """What looks like machinery in the delivered text and, if it differs, in the raw one.

    A hit seen in both is reported once, as ``delivered``: that is the one the person saw.
    """
    shingles = _shingles(prompt_sources, echo_words)
    hits = _rule_hits(delivered, "delivered") + _echo_hits(
        delivered, "delivered", shingles, echo_words
    )
    if raw != delivered:
        seen = {(h.rule, h.excerpt.strip()) for h in hits}
        for hit in _rule_hits(raw, "raw") + _echo_hits(raw, "raw", shingles, echo_words):
            if (hit.rule, hit.excerpt.strip()) not in seen:
                hits.append(hit)
    return hits[:MAX_HITS]


def build_record(
    *,
    hits: Sequence[Hit],
    guard_triggered: bool,
    raw: str,
    delivered: str,
    session_id: str,
    channel: str | None,
    voice: bool,
    provider: str | None,
    model: str | None,
    finish_reason: str | None,
    tool_calls: Sequence[str],
    blocks: Sequence[Block],
    user_message: str | None,
    run: str | None = None,
) -> dict[str, Any]:
    """The document stored for one suspicious LLM call (see ``docs/pages/vazamentos.md``)."""
    return {
        "_id": uuid.uuid4().hex,
        "at": datetime.now(UTC),
        "session": session_id,
        "run": run,
        "channel": channel,
        "voice": voice,
        "provider": provider,
        "model": model,
        "finish_reason": finish_reason,
        "tool_calls": list(tool_calls),
        "guard_triggered": guard_triggered,
        "leaked": any(h.where == "delivered" for h in hits),
        "rules": sorted({h.rule for h in hits}),
        "hits": [asdict(h) for h in hits],
        "raw": raw,
        "delivered": delivered,
        "blocks": [asdict(b) for b in blocks],
        "user_message": user_message,
        "version": brain.__version__,
        "commit": os.environ.get("GIT_COMMIT") or None,
    }
