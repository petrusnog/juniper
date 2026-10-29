"""Evidence of what leaked into her speech, for the investigation (T-165).

``LeakGuard`` (``core/leak.py``) cuts the leaks it knows; this module looks for the ones it does
not, and records both. It never changes what is said: it only reads the model's raw text (before
the guard) and the delivered text (after it), and tells what in them looks like machinery.

Two decisions, two precisions:

* the **audit** uses every rule, broad on purpose: a false positive only adds a record a person
  reads (``<div>`` in a code answer, "Sistema: Android 14", a fact quoted from her memories);
* the **quarantine** (the turn kept out of long-term memory and of the nightly pass) uses only
  the rules in ``QUARANTINE_RULES``, which natural speech does not trigger: tool-call markup,
  chat-template tokens, the tags of her own context blocks, a line speaking as the system, an
  instruction meant for the model, and words copied from the persona or the channel note.

``prompt_echo`` knows no format: a run of words copied from a system block sent in that same call
is how a leak in a shape nobody listed yet shows up. Copying the persona or the channel note is a
leak; quoting the profile, the memories or the docs is often what she was asked to do, so that is
``context_echo``, audit only.

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
from brain.core.leak import MARKERS
from brain.memory.secretfilter import mask_secrets

# The excerpt of a hit kept in the record: enough to recognize it, never the whole answer.
EXCERPT_CHARS = 160
MAX_HITS = 20
MAX_ECHOES = 3

# System blocks whose words must never be said back; the others are data she may quote.
INSTRUCTION_BLOCKS = frozenset({"persona", "channel", "system"})

QUARANTINE_RULES = frozenset(
    {"tool_markup", "special_token", "context_tag", "system_role", "instruction", "prompt_echo"}
)

# The tags of her context blocks (brain/memory/v2/context.py), also listed in the guard.
_KNOWN_TAGS = frozenset(
    re.sub(r"[</>]", "", m).lower() for m in MARKERS if re.match(r"</?[a-z_]", m)
)

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Tool-call markup the provider failed to parse (DeepSeek's DSML, XML-style calls).
    (
        "tool_markup",
        re.compile(r"DSML|</?(?:function|parameters?|tool_calls?|invoke)\b", re.IGNORECASE),
    ),
    # Chat-template tokens (<|im_start|>, the fullwidth bars of DeepSeek).
    ("special_token", re.compile(r"<\|[^|\n]{1,40}\|>|<｜|｜>")),
    # Any XML-like tag; ``context_tag`` when it is one of her context blocks (see ``_tag_rule``).
    ("tag", re.compile(r"</?([A-Za-z_][\w-]{0,30})(?:\s[^<>\n]{0,80})?/?>")),
    # An answer that opens with a bracket is closing (or opening) a system note.
    ("bracket_start", re.compile(r"\A\s*[\[\]]")),
    # A line that speaks as the system or as the model.
    (
        "system_role",
        re.compile(
            r"^[^\w\n]*(?:mensagem d[eo] sistema|system|assistant)\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # A line opening with a word that can be a role, or just a label ("Sistema: Android 14").
    (
        "role_prefix",
        re.compile(
            r"^[^\w\n]*(?:sistema|assistente|user|usu[aá]ri[oa]|tool|ferramenta)\s*:",
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
_TAG_NAME = re.compile(r"</?([A-Za-z_][\w-]{0,30})")
_DATA_BLOCK = re.compile(r"<([A-Za-z_][\w-]{0,30})>.*?</\1>", re.DOTALL)


@dataclass(frozen=True)
class Hit:
    rule: str
    where: str  # "delivered" (the person saw it) or "raw" (only the model wrote it)
    at: int  # character offset in that text
    excerpt: str
    matched: str = ""  # the matched text itself (what tells two hits apart)


@dataclass(frozen=True)
class Block:
    """A system message of the call: what it is and how big, never its content."""

    kind: str  # persona, profile, memories, docs, channel, system
    chars: int


def _excerpt(text: str, start: int, end: int) -> str:
    begin = max(0, start - 30)
    return text[begin : min(len(text), max(end, start + 1) + 30)][:EXCERPT_CHARS]


def _context_tags(sources: Sequence[tuple[str, str]]) -> frozenset[str]:
    """The tag names her context uses: the known ones and any tag in this call's system blocks."""
    found = {m.group(1).lower() for _, text in sources for m in _TAG_NAME.finditer(text)}
    return _KNOWN_TAGS | found


def _rule_hits(text: str, where: str, context_tags: frozenset[str]) -> list[Hit]:
    hits: list[Hit] = []
    for name, pattern in RULES:
        for match in pattern.finditer(text):
            rule = name
            if name == "tag" and match.group(1).lower() in context_tags:
                rule = "context_tag"
            excerpt = _excerpt(text, match.start(), match.end())
            hits.append(Hit(rule, where, match.start(), excerpt, match.group()))
            if len(hits) >= MAX_HITS:
                return hits
    return hits


def _shingles(sources: Sequence[str], size: int) -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    for source in sources:
        words = _WORD.findall(source.lower())
        found.update(tuple(words[i : i + size]) for i in range(len(words) - size + 1))
    return found


def _echo_hits(
    text: str, where: str, rule: str, shingles: set[tuple[str, ...]], size: int
) -> list[Hit]:
    """Runs of ``size`` words of ``text`` that also appear in one of the blocks."""
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
        run = text[start:stop]
        hits.append(Hit(rule, where, start, run[:EXCERPT_CHARS], run.lower()))
        i = end
    return hits


def _all_hits(
    text: str,
    where: str,
    context_tags: frozenset[str],
    echoes: Sequence[tuple[str, set[tuple[str, ...]]]],
    size: int,
) -> list[Hit]:
    hits = _rule_hits(text, where, context_tags)
    for rule, shingles in echoes:
        hits += _echo_hits(text, where, rule, shingles, size)
    return hits


def detect(
    raw: str, delivered: str, sources: Sequence[tuple[str, str]], *, echo_words: int
) -> list[Hit]:
    """What looks like machinery in the delivered text and, if it differs, in the raw one.

    ``sources`` are the system blocks of the call as ``(kind, text)``. A hit seen in both texts
    is reported once, as ``delivered``: that is the one the person saw. The texts are read with
    secrets masked, as ``build_record`` stores them: offsets and excerpts match the record, and
    an excerpt never carries a piece of a token.
    """
    raw, delivered = mask_secrets(raw), mask_secrets(delivered)
    context_tags = _context_tags(sources)
    rules, data = [], []
    for kind, text in sources:
        if kind not in INSTRUCTION_BLOCKS:
            data.append(text)
            continue
        # The persona carries data blocks too (<opinioes>, <estado>...): quoting those is data.
        data.extend(m.group(0) for m in _DATA_BLOCK.finditer(text))
        rules.append(_DATA_BLOCK.sub(" ", text))
    echoes = [
        ("prompt_echo", _shingles(rules, echo_words)),
        ("context_echo", _shingles(data, echo_words)),
    ]
    hits = _all_hits(delivered, "delivered", context_tags, echoes, echo_words)
    if raw != delivered:
        seen = {(h.rule, h.matched) for h in hits}
        for hit in _all_hits(raw, "raw", context_tags, echoes, echo_words):
            if (hit.rule, hit.matched) not in seen:
                hits.append(hit)
    return hits[:MAX_HITS]


def quarantined(hits: Sequence[Hit]) -> bool:
    """The person saw a leak that natural speech does not produce: keep the turn out of memory."""
    return any(h.where == "delivered" and h.rule in QUARANTINE_RULES for h in hits)


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
    """The document stored for one suspicious LLM call (see ``docs/pages/vazamentos.md``).

    The texts go through ``mask_secrets``: the evidence does not need a pasted token.
    """
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
        "quarantined": quarantined(hits),
        "rules": sorted({h.rule for h in hits}),
        "hits": [
            {"rule": h.rule, "where": h.where, "at": h.at, "excerpt": h.excerpt} for h in hits
        ],
        "raw": mask_secrets(raw),
        "delivered": mask_secrets(delivered),
        "blocks": [asdict(b) for b in blocks],
        "user_message": mask_secrets(user_message) if user_message is not None else None,
        "version": brain.__version__,
        "commit": os.environ.get("GIT_COMMIT") or None,
    }
