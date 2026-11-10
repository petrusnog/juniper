"""A speaker embedder for tests: a "voice" is a set of tones and its vector is the spectrum.

Two clips with the same tones (plus a little noise) point the same way; different tones do not;
tones a few hertz apart are "similar voices". No model file, no network.
"""

from __future__ import annotations

import numpy as np

from brain.voice.errors import VoiceError
from brain.voice.speaker import MIN_EMBED_SECONDS, SpeakerEmbedder, seconds_of

RATE = 16_000
ANA = (200.0, 410.0, 630.0)
BIA = (320.0, 700.0, 1100.0)
CAIO = (900.0, 1500.0, 2300.0)
ANA_TWIN = (205.0, 415.0, 640.0)  # almost her


def voice_pcm(
    tones: tuple[float, ...], seconds: float = 2.0, *, noise: float = 0.03, seed: int = 0
) -> bytes:
    rng = np.random.default_rng(seed)
    t = np.arange(int(RATE * seconds)) / RATE
    wave = sum(np.sin(2 * np.pi * f * t) for f in tones) / len(tones)
    wave = wave * 9000 + rng.normal(0, noise * 9000, t.shape)
    return np.clip(wave, -32767, 32766).astype("<i2").tobytes()


class ToneEmbedder(SpeakerEmbedder):
    model_id = "fake:tone"

    def __init__(self) -> None:
        self.calls = 0
        self.fail: VoiceError | None = None

    def embed(self, pcm: bytes, sample_rate: int) -> np.ndarray:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        if seconds_of(pcm, sample_rate) < MIN_EMBED_SECONDS:
            raise VoiceError("invalid_audio", "curta")
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        freqs = np.fft.rfftfreq(len(x), 1 / sample_rate)
        edges = np.geomspace(100, 4000, 65)
        bands = np.array(
            [
                spectrum[(freqs >= lo) & (freqs < hi)].sum()
                for lo, hi in zip(edges, edges[1:], strict=False)
            ]
        )
        vector = bands.astype(np.float32)
        return vector / np.linalg.norm(vector)
