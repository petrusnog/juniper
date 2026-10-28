"""Speech detection by energy, in the brain (ADR-023, section 6).

The app only streams audio; where an utterance starts and ends is decided here, by a plain
energy detector with an adaptive noise floor (no new dependency). Input is PCM 16-bit mono at
``SAMPLE_RATE``, in chunks of any size; output is a list of `VadEvent`:

- ``speech_start``: energy above the floor for ``speech_start_ms``;
- ``barge_in``: the same while the Juniper is talking (``set_playing(True)``), but with a higher
  threshold and ``barge_in_ms`` of continuous speech, so her own voice leaking into the mic
  does not cut her off; it replaces ``speech_start`` and the utterance goes on being captured;
- ``utterance``: ``silence_ms`` of silence (or the maximum length) after speech, with the audio,
  including the 300 ms before the start (pre-roll);
- ``too_short``: speech shorter than ``min_utterance_ms``: a click or a cough, dropped.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np

from brain.config import VoiceVADSettings, settings

SAMPLE_RATE: Final = 16_000
FRAME_MS: Final = 20
FRAME_SAMPLES: Final = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES: Final = FRAME_SAMPLES * 2
PREROLL_MS: Final = 300
MIN_RMS: Final = 250.0  # absolute floor of the threshold (of 32768): digital silence is not speech
SPEECH_FACTOR: Final = 3.0  # speech is this many times above the noise floor
PLAYING_FACTOR: Final = 6.0  # ... and this many while the Juniper is talking
PLAYING_MIN_RMS: Final = 900.0

EventKind = Literal["speech_start", "barge_in", "utterance", "too_short"]


@dataclass(frozen=True)
class VadEvent:
    kind: EventKind
    pcm: bytes = b""


class Vad:
    def __init__(self, config: VoiceVADSettings | None = None) -> None:
        cfg = config or settings.voice.vad
        self._start_frames = max(1, cfg.speech_start_ms // FRAME_MS)
        self._barge_frames = max(1, cfg.barge_in_ms // FRAME_MS)
        self._end_frames = max(1, cfg.silence_ms // FRAME_MS)
        self._min_frames = max(1, cfg.min_utterance_ms // FRAME_MS)
        self._max_frames = cfg.max_utterance_seconds * 1000 // FRAME_MS
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_MS // FRAME_MS)
        self._pending = b""
        self._floor = MIN_RMS / 2
        self._playing = False
        self._reset()

    def _reset(self) -> None:
        self._in_speech = False
        self._loud_run: list[bytes] = []
        self._silent_run = 0
        self._frames: list[bytes] = []
        self._voiced = 0

    def set_playing(self, playing: bool) -> None:
        """Tell the detector the Juniper is (not) talking: it changes how easily speech starts."""
        if playing != self._playing and not self._in_speech:
            self._loud_run = []  # the bar for "speech" changed: start counting again
        self._playing = playing

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        """Forget the utterance in progress (a turn was cancelled, a connection changed)."""
        self._preroll.clear()
        self._pending = b""
        self._reset()

    def _threshold(self) -> float:
        if self._playing:
            return max(PLAYING_MIN_RMS, self._floor * PLAYING_FACTOR)
        return max(MIN_RMS, self._floor * SPEECH_FACTOR)

    def _adapt(self, rms: float) -> None:
        """Follow the background noise: down fast, up slowly (speech must not raise it)."""
        rate = 0.2 if rms < self._floor else 0.01
        self._floor += (rms - self._floor) * rate

    def feed(self, pcm: bytes) -> list[VadEvent]:
        events: list[VadEvent] = []
        data = self._pending + pcm
        usable = len(data) - len(data) % FRAME_BYTES
        self._pending = data[usable:]
        for i in range(0, usable, FRAME_BYTES):
            frame = data[i : i + FRAME_BYTES]
            events.extend(self._frame(frame))
        return events

    def _frame(self, frame: bytes) -> list[VadEvent]:
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float64)
        rms = float(np.sqrt(np.mean(samples * samples)))
        loud = rms >= self._threshold()
        if not self._in_speech:
            return self._idle_frame(frame, rms, loud)
        return self._speech_frame(frame, loud)

    def _idle_frame(self, frame: bytes, rms: float, loud: bool) -> list[VadEvent]:
        if not loud:
            self._adapt(rms)
            self._loud_run = []
            self._preroll.append(frame)
            return []
        self._loud_run.append(frame)
        needed = self._barge_frames if self._playing else self._start_frames
        if len(self._loud_run) < needed:
            return []
        self._in_speech = True
        self._frames = [*self._preroll, *self._loud_run]
        self._voiced = len(self._loud_run)
        self._preroll.clear()
        self._loud_run = []
        self._silent_run = 0
        return [VadEvent("barge_in" if self._playing else "speech_start")]

    def _speech_frame(self, frame: bytes, loud: bool) -> list[VadEvent]:
        self._frames.append(frame)
        if loud:
            self._voiced += 1
            self._silent_run = 0
        else:
            self._silent_run += 1
        if self._silent_run < self._end_frames and len(self._frames) < self._max_frames:
            return []
        event = (
            VadEvent("utterance", b"".join(self._frames))
            if self._voiced >= self._min_frames
            else VadEvent("too_short")
        )
        self._preroll.clear()
        self._reset()
        return [event]
