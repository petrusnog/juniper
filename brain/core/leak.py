"""Keeps the machinery out of what she says.

The model sometimes writes what was only meant for it: the DSML markup of a tool call the provider
failed to parse, a context block copied from the prompt (``<conhecimento>``, the memory headers,
the channel note), or its own planning ("Vou responder de forma curta...") glued to the start of
the answer. A prompt asking it not to is a request, not a guarantee. ``LeakGuard`` is the
guarantee: it filters the answer while it streams, so the client, the saved conversation and the
next LLM call (which would imitate its own history) never see it.

Two rules, both deliberately narrow so natural speech is never touched:

* a marker (``MARKERS``) cuts the answer there: nothing from it onwards is ever emitted;
* planning sentences at the very start are dropped, but only when one of the first sentences is
  unmistakably planning (``_PLANNING``); the sentences around it that narrate the user in the third
  person (``_NARRATION``) go with it.
"""

from __future__ import annotations

import re

# Text that has no place in an answer. Matched literally, anywhere.
MARKERS: tuple[str, ...] = (
    "DSML",
    "<｜",
    "</｜",
    "<conhecimento",
    "</conhecimento",
    "<opinioes",
    "</opinioes",
    "<estado",
    "</estado",
    "<interesse",
    "</interesse",
    "<personalidade",
    "</personalidade",
    "<perfil_atual",
    "<memorias",
    "<data_da_conversa",
    "<conversa>",
    "<resposta>",
    "[Canal:",
    "[Pausa de",
    "[O usuário",
    # The start of each context header (brain/memory/retrieval.py, brain/memory/v2/context.py).
    "Notas suas, de estudo",
    "Fatos que você já conhece sobre a pessoa",
    "Quem é a pessoa com quem você fala",
    "O que você estudou sobre o tema da conversa",
    "O que você acha do Petrus: hipóteses",
    "Um tema da conversa que também é seu",
    "Como você está, em poucas palavras",
)

# A sentence that is the model talking to itself, never to the person.
_PLANNING = re.compile(
    r"^\W*(?:(?:o|a) usu[aá]ri[oa] (?:se chama|perguntou|disse|est[aá]|quer|pediu|acabou"
    r"|mencionou|cumprimentou|falou)\b"
    r"|(?:vou|devo|preciso|posso) responder (?:de forma|de maneira|em tom)\b"
    r"|responder (?:de forma|de maneira|em tom)\b)",
    re.IGNORECASE,
)
# Third-person narration of the person, only dropped next to a planning sentence.
_NARRATION = re.compile(
    r"^\W*(?:(?:ele|ela)\s+(?:est[aá]|quer|perguntou|disse|pediu|mencionou|falou|cumprimentou"
    r"|parece|ficou|acabou)\b|(?:manter|mostrar|ecoar|perguntar|responder|usar)\b)",
    re.IGNORECASE,
)
# What makes a start worth holding back: narration of the person in the third person, or planning.
# Speech addressed to the person ("você", "tu", a name) is released once its first sentence ends.
_SUSPECT = re.compile(r"\b(?:ele|ela|dele|dela|usu[aá]ri[oa])\b", re.IGNORECASE)
# A turn she invents for the person: the date stamp the system puts before each of their messages
# ("[quarta-feira, 28/10/2026, 18:41 ...]"), or a reply that opens by addressing herself.
_TURN_STAMP = re.compile(r"\[[^\]\[\n]{0,20}, \d{1,2}/\d{1,2}/\d{4}")
_ADDRESSES_HERSELF = re.compile(r"^\s*(?:juniper|j[uú]nior)\s*[,!:]", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.!?…]*[.!?…]+\s*")

# How much of the start is held back to look for planning before it is released. The planning of
# the logs was two to three sentences; holding more would only delay the first word.
HEAD_SENTENCES = 3
HEAD_CHARS = 280
EARLY_CHARS = 60  # an unfinished first sentence this long, with nothing suspect, is released

_STAMP_LOOKAHEAD = 40
_LONGEST_MARKER = max(len(m) for m in MARKERS)


class LeakGuard:
    """Streaming filter: ``feed`` the chunks in order and ``flush()`` at the end.

    ``triggered`` tells whether anything was cut, for the event log.
    """

    def __init__(self) -> None:
        self._hold = ""  # a tail that may still grow into a marker
        self._cut = False
        self._head = ""
        self._in_head = True
        self._dropping = False  # planning found: the narration after it goes too
        self.triggered = False

    def feed(self, text: str) -> str:
        return self._start(self._scan(text, final=False), final=False)

    def flush(self) -> str:
        return self._start(self._scan("", final=True), final=True)

    def _scan(self, text: str, *, final: bool) -> str:
        """Stage one: cut at the first marker; hold back a tail that might become one."""
        if self._cut:
            return ""
        data = self._hold + text
        hits = [i for m in MARKERS if (i := data.find(m)) >= 0]
        if stamp := _TURN_STAMP.search(data):
            hits.append(stamp.start())
        if hits:
            self._cut = self.triggered = True
            self._hold = ""
            return data[: min(hits)].rstrip()
        if final:
            self._hold = ""
            return data
        keep = 0
        for size in range(min(_LONGEST_MARKER - 1, len(data)), 0, -1):
            if any(m.startswith(data[-size:]) for m in MARKERS):
                keep = size
                break
        bracket = data.rfind("[")
        if bracket >= 0 and len(data) - bracket < _STAMP_LOOKAHEAD and "]" not in data[bracket:]:
            keep = max(keep, len(data) - bracket)  # may still become a turn stamp
        self._hold = data[len(data) - keep :] if keep else ""
        return data[: len(data) - keep]

    def _start(self, text: str, *, final: bool) -> str:
        """Stage two: look for planning at the start of the answer, then step aside."""
        if not self._in_head:
            return text
        self._head += text
        if _ADDRESSES_HERSELF.match(self._head):
            # She never opens by calling herself: this is the model writing the person's turn, and
            # the answer after it is to a message nobody sent. Nothing of it is worth keeping.
            self._cut = self.triggered = True
            self._head, self._in_head = "", False
            return ""
        sentences = _SENTENCE.findall(self._head)
        rest = self._head[sum(len(s) for s in sentences) :]
        if final and rest.strip():
            sentences.append(rest)
            rest = ""
        if self._dropping:
            while sentences and _NARRATION.match(sentences[0]):
                sentences.pop(0)
            if sentences or final:
                return self._release(sentences, rest)
            self._head = rest  # all narration so far: keep looking at what comes next
            return ""
        plans = [i for i, s in enumerate(sentences[:HEAD_SENTENCES]) if _PLANNING.match(s)]
        if plans:
            self.triggered = self._dropping = True
            del sentences[: plans[-1] + 1]
            self._head = "".join(sentences) + rest
            return self._start("", final=final)
        if final or len(sentences) >= HEAD_SENTENCES or len(self._head) >= HEAD_CHARS:
            return self._release(sentences, rest)
        first = sentences[0] if sentences else self._head
        if not _suspect(first) and (sentences or len(self._head) >= EARLY_CHARS):
            return self._release(sentences, rest)
        return ""

    def _release(self, sentences: list[str], rest: str) -> str:
        out = "".join(sentences) + rest
        self._head = ""
        self._in_head = False
        return out.lstrip() if self.triggered else out


def _suspect(text: str) -> bool:
    return bool(_SUSPECT.search(text) or _PLANNING.match(text))


def clean(text: str) -> str:
    """``LeakGuard`` over a whole text at once."""
    guard = LeakGuard()
    return guard.feed(text) + guard.flush()
