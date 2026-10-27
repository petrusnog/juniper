"""Text prepared for speech (ADR-023, section 2): what is worth saying aloud.

The reply is text meant for a screen. Said aloud, markdown marks, code blocks, emojis and links
are noise (or worse: a spoken URL is unintelligible). ``speech_text`` cleans one piece of text;
``SentenceChunker`` joins the streamed ``chat.token`` pieces into sentences so the first one can be
synthesised while the model is still writing the next, and enforces ``max_chars`` (the rest stays
on the screen, and the Juniper says so once).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

MIN_SENTENCE_CHARS: Final = 24
CODE_PLACEHOLDER: Final = "Deixei o trecho de código na tela."
LINK_PLACEHOLDER: Final = "O link está na tela."
TRUNCATED_NOTICE: Final = "O resto está na tela."

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://|www\.)[^)\s]*\)")
_BOLD_ITALIC = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_LEADING_MARK = re.compile(r"^\s*(?:#{1,6}\s+|>\s+|[-*+•]\s+|\d+[.)]\s+)", re.MULTILINE)
_RULE = re.compile(r"^[ \t]*[-=_*|:+ \t]{3,}[ \t]*$", re.MULTILINE)  # "---", table rules
_SPACES = re.compile(r"[ \t\u00a0]+")
_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?…])")
_BLANKS = re.compile(r"\s*\n\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")
# What can be spoken ends a sentence; markers dropped above must not leave a dangling mark.
_TRAILING = re.compile(r"[\s:;,\-–—]+$")


def _is_emoji_like(char: str) -> bool:
    category = unicodedata.category(char)
    return category in {"So", "Sk", "Cs", "Co"} or char in {"\u200d", "\ufe0f", "\u20e3"}


def strip_emoji(text: str) -> str:
    return "".join(c for c in text if not _is_emoji_like(c))


def speech_text(text: str, *, say_link: bool = False) -> str:
    """One piece of text cleaned for speech.

    URLs are dropped, or replaced by "O link está na tela." once when ``say_link`` is true; code
    blocks become one sentence saying so; markdown marks, bullets and emojis disappear.
    """
    cleaned = _FENCE.sub(f"\n{CODE_PLACEHOLDER}\n", text)
    cleaned = cleaned.replace("```", " ")
    cleaned = _MD_LINK.sub(lambda m: f"{m.group(1)} \u0000", cleaned)  # keep the label
    cleaned = _URL.sub("\u0000", cleaned)
    marks = cleaned.count("\u0000")
    cleaned = cleaned.replace("\u0000", "")
    if marks and say_link:
        cleaned = f"{cleaned} {LINK_PLACEHOLDER}"
    cleaned = cleaned.replace("`", "")
    cleaned = _BOLD_ITALIC.sub(r"\2", cleaned)
    cleaned = _RULE.sub("", cleaned)
    cleaned = _LEADING_MARK.sub("", cleaned)
    cleaned = cleaned.replace("|", " ")
    cleaned = strip_emoji(cleaned)
    cleaned = _BLANKS.sub(". ", cleaned.strip())
    cleaned = _SPACES.sub(" ", cleaned)
    cleaned = _BEFORE_PUNCT.sub(r"\1", cleaned)
    cleaned = re.sub(r"([.!?…:;,])\s*(?:\.\s*)+", r"\1 ", cleaned)  # "a. . b" left by dropped lines
    cleaned = cleaned.strip(" .")
    if cleaned and not re.search(r"[.!?…]$", cleaned):
        cleaned += "."
    return cleaned


class SentenceChunker:
    """Turns streamed pieces of a reply into speakable sentences, in order.

    ``feed`` returns the sentences that are complete; ``flush`` returns what is left. Sentences
    shorter than ``min_chars`` are joined to the next one so the intonation does not break. Only
    ``max_chars`` of speech are produced per reply: after that nothing more is spoken and
    ``truncated`` is true (``flush`` then adds the "the rest is on the screen" sentence).
    """

    def __init__(self, *, max_chars: int = 800, min_chars: int = MIN_SENTENCE_CHARS) -> None:
        self._max = max_chars
        self._min = min_chars
        self._buffer = ""
        self._pending = ""  # a short sentence waiting for the next one
        self._spoken = 0
        self._link_said = False
        self._code_said = False
        self.truncated = False

    @property
    def spoken_chars(self) -> int:
        return self._spoken

    def feed(self, piece: str) -> list[str]:
        self._buffer += piece
        return self._drain(final=False)

    def flush(self) -> list[str]:
        out = self._drain(final=True)
        if self.truncated:
            out.append(TRUNCATED_NOTICE)
        return out

    # --- internals ---

    def _drain(self, *, final: bool) -> list[str]:
        out: list[str] = []
        text = _FENCE.sub(self._code_sentence, self._buffer)  # complete code blocks, said once
        held = ""
        if "```" in text:  # an opener still unclosed: hold it back (and never read it at the end)
            text, opener, rest = text.partition("```")
            held = "" if final else opener + rest
        pieces = _SENTENCE_END.split(text)
        if final:
            self._buffer = ""
        else:
            tail = pieces.pop() if pieces else ""
            self._buffer = tail + held
        for raw in pieces:
            self._emit(raw, out)
        if final and self._pending:
            self._take(self._pending, out)
            self._pending = ""
        return out

    def _code_sentence(self, _match: re.Match[str]) -> str:
        """A whole code block becomes one sentence, the first time; later blocks are silent."""
        if self._code_said:
            return "\n"
        self._code_said = True
        return f"\n{CODE_PLACEHOLDER}\n"

    def _emit(self, raw: str, out: list[str]) -> None:
        if self.truncated or not raw.strip():
            return
        has_link = bool(_URL.search(raw) or _MD_LINK.search(raw))
        spoken = speech_text(raw, say_link=has_link and not self._link_said)
        self._link_said = self._link_said or has_link
        if not spoken:
            return
        spoken = f"{self._pending} {spoken}".strip() if self._pending else spoken
        self._pending = ""
        if len(spoken) < self._min:
            self._pending = spoken
            return
        self._take(spoken, out)

    def _take(self, sentence: str, out: list[str]) -> None:
        room = self._max - self._spoken
        if room <= 0:
            self.truncated = True
            return
        if len(sentence) > room:
            cut = sentence[:room]
            end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
            sentence = cut[: end + 1] if end > 0 else ""
            self.truncated = True
            if not sentence:
                return
        self._spoken += len(sentence)
        out.append(sentence)
