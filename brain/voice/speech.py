"""`SpeechService`: speak each sentence with the cloud voice or fall back to the device's (ADR-023).

Per sentence it decides:

- the budget is gone (or the cloud is switched off): the text goes to the device (``voice.say``);
- the cloud failed (network, quota, credential): the same, and the cloud is left alone for
  ``retry_seconds`` (longer after a quota error) before trying again;
- otherwise: the audio from the synthesizer.

Each change of mode is reported **once** as a notice (``voice.fallback``), and again when the
cloud is back. A failure is never an exception for the conversation: the sentence is always said,
one way or the other. Only the *size* of the text is logged, never the text.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Literal

from brain.eventlog import event_log
from brain.voice.budget import VoiceBudget
from brain.voice.errors import VoiceError
from brain.voice.tts import Synthesizer

RETRY_SECONDS: Final = 30.0
QUOTA_RETRY_SECONDS: Final = 600.0

Reason = Literal["quota", "budget", "error", "recovered"]
Mode = Literal["cloud", "local"]


@dataclass(frozen=True)
class Notice:
    """A change of mode to tell the client about (``voice.fallback``)."""

    mode: Mode
    reason: Reason


@dataclass
class Spoken:
    """One sentence, said: ``audio`` (PCM from the cloud) or ``say`` (text for the device)."""

    audio: bytes | None = None
    say: str | None = None
    notices: list[Notice] = field(default_factory=list)


class SpeechService:
    def __init__(
        self,
        synthesizer: Synthesizer | None,
        budget: VoiceBudget,
        *,
        retry_seconds: float = RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._synth = synthesizer
        self._budget = budget
        self._retry = retry_seconds
        self._clock = clock
        self._retry_at = 0.0
        self._mode: Mode = (
            "cloud" if synthesizer is not None and not budget.exhausted() else "local"
        )

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def sample_rate(self) -> int | None:
        return self._synth.sample_rate if self._synth is not None else None

    async def speak(self, text: str) -> Spoken:
        if self._synth is None:
            return self._local(text, None, "error")
        if self._budget.exhausted():
            return self._local(text, "budget", "budget")
        if not self._budget.reserve(len(text)):
            self._budget.exhaust()  # less than a sentence is left: stop for the month
            return self._local(text, "budget", "budget")
        if self._clock() < self._retry_at:
            self._budget.release(len(text))
            return self._local(text, None, "error")
        try:
            audio = await self._synth.synthesize(text)
        except VoiceError as exc:
            self._budget.release(len(text))
            pause = QUOTA_RETRY_SECONDS if exc.code == "quota" else self._retry
            self._retry_at = self._clock() + pause
            event_log.log("voice", action="tts_failed", code=exc.code, chars=len(text))
            return self._local(text, "quota" if exc.code == "quota" else "error", exc.code)
        except Exception as exc:  # noqa: BLE001 - a bug in the provider must not silence her
            self._budget.release(len(text))
            self._retry_at = self._clock() + self._retry
            event_log.log("voice", action="tts_failed", code=type(exc).__name__, chars=len(text))
            return self._local(text, "error", type(exc).__name__)
        event_log.log("voice", action="tts", mode="cloud", chars=len(text))
        notices: list[Notice] = []
        if self._mode == "local":
            self._mode = "cloud"
            notices.append(Notice("cloud", "recovered"))
        return Spoken(audio=audio, notices=notices)

    def _local(self, text: str, reason: Reason | None, why: str) -> Spoken:
        notices: list[Notice] = []
        if self._mode == "cloud" and reason is not None:
            self._mode = "local"
            notices.append(Notice("local", reason))
        event_log.log("voice", action="tts", mode="local", chars=len(text), why=why)
        return Spoken(say=text, notices=notices)
