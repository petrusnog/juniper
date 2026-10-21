"""Speech style rules that must hold whatever the model writes.

The persona asks the LLM not to use dashes (the em dash and its siblings read as written text, not
as speech), but a prompt is a request, not a guarantee. ``DashFilter`` is the guarantee: it
rewrites the dash while the answer streams, so the client, the saved conversation and the next
LLM call (which would imitate its own history) never see one.
"""

from __future__ import annotations

import re

# En dash, em dash and horizontal bar. The plain hyphen is left alone: it is part of words
# ("guarda-chuva") and of arithmetic ("2 - 1").
_DASHES = "–—―"
_RUN = re.compile(rf"[ \t]*(?:[{_DASHES}]+[ \t]*)+")
# What can still grow into a dash run once the next chunk arrives: held back, never emitted early.
_TAIL = re.compile(rf"(?:[ \t]*(?:[{_DASHES}]+[ \t]*)+|[ \t]+)$")


class DashFilter:
    """Streaming rewrite of dashes into speech: ``oi — tudo`` becomes ``oi, tudo``.

    Feed it the chunks in order and ``flush()`` at the end; the result is the same however the text
    was split into chunks. Trailing spaces are held back until the next chunk shows whether a dash
    follows, so a space may travel with the following chunk.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._last = ""  # last character emitted; "" at the start of the text

    def feed(self, text: str) -> str:
        data = self._pending + text
        tail = _TAIL.search(data)
        if tail:
            self._pending, body = tail.group(0), data[: tail.start()]
        else:
            self._pending, body = "", data
        out = _RUN.sub(lambda m: self._speak(body, m), body)
        if out:
            self._last = out[-1]
        return out

    def flush(self) -> str:
        """The held-back tail: spaces are kept; a dash that ends the text is dropped."""
        tail, self._pending = self._pending, ""
        out = "" if re.search(rf"[{_DASHES}]", tail) else tail
        if out:
            self._last = out[-1]
        return out

    def _speak(self, body: str, run: re.Match[str]) -> str:
        before = body[run.start() - 1] if run.start() > 0 else self._last
        after = body[run.end()] if run.end() < len(body) else ""
        if before in ("", "\n"):
            return ""  # a dash opening a line (a bullet): nothing to say
        if before.isdigit() and after.isdigit():
            return " a "  # 1997–2003 -> 1997 a 2003
        if before in ",;:.!?…":
            return " "  # already punctuated: "oi, — tudo" -> "oi, tudo"
        return ", "


def speak(text: str) -> str:
    """``DashFilter`` over a whole text at once."""
    filt = DashFilter()
    return filt.feed(text) + filt.flush()
