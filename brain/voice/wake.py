"""The second check of the wake word, on the brain (ADR-034, section 2).

The detector on the device hears "Juniper" in a few milliseconds and with no network, which is
what makes it cheap to leave on; it also gets it wrong now and then (a TV, a similar name). Before
a session opened that way costs anything, the brain checks the *transcription* of the first phrase:
if the name is not there, the detector was wrong, the session closes in silence (``unverified``)
and the false activation is counted.

When the name is there it is taken off the front, so the model gets "que horas são?" and not
"Juniper, que horas são?". The comparison is by sound, because the speech-to-text writes the name
the way it hears it ("Júnior", "Junipe").
"""

from __future__ import annotations

import re
from typing import Final

from brain.voice.correction import similarity
from brain.voice.stt import normalise

WAKE_WORD: Final = "Juniper"
# What Whisper writes for the name in pt-BR, besides what sounds close enough.
ALIASES: Final = frozenset({"juniper", "junipe", "junior", "djuniper", "zuniper", "juniperr"})
THRESHOLD: Final = 0.75
LEADING_WORDS: Final = 2  # the name leads the phrase, at most after "oi" or "ei"
_WORD: Final = re.compile(r"\w+", re.UNICODE)
_EDGE: Final = " \t\n,.;:!?-—–…"


def is_wake_word(token: str) -> bool:
    plain = normalise(token)
    return plain in ALIASES or similarity(plain, WAKE_WORD) >= THRESHOLD


def strip_wake_word(text: str) -> tuple[bool, str]:
    """``(heard, rest)``: whether the name is in ``text`` and the phrase without it.

    The name is removed only when it leads the phrase (at the start, or right after one word such
    as "oi"); said later ("abra o projeto, Juniper") it confirms the activation and the phrase is
    left as it is.
    """
    tokens = list(_WORD.finditer(text))
    for index, token in enumerate(tokens):
        if is_wake_word(token.group()):
            if index < LEADING_WORDS:
                return True, text[token.end() :].lstrip(_EDGE).rstrip()
            return True, text
    return False, text
