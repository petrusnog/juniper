"""What a voice session needs from the brain, built once for the whole server (ADR-023).

The cloud voice, its monthly budget and the cloud transcriber are shared by every connection (the
budget and the cool-downs are about the *accounts*, not about who is talking). Building them
never calls a provider; a missing credential just means that side falls back to the device.
"""

from __future__ import annotations

from dataclasses import dataclass

from brain.config import settings
from brain.voice.budget import VoiceBudget
from brain.voice.speech import SpeechService
from brain.voice.stt import GroqTranscriber, SpeechToText, Transcriber
from brain.voice.tts import GoogleSynthesizer, Synthesizer


@dataclass
class VoiceServices:
    speech: SpeechService
    stt: SpeechToText
    max_spoken_chars: int
    idle_timeout: float
    _closers: tuple[Synthesizer | Transcriber, ...] = ()

    async def aclose(self) -> None:
        for provider in self._closers:
            await provider.aclose()


def open_voice_services() -> VoiceServices:
    cfg = settings.voice
    synthesizer: Synthesizer | None = None
    if cfg.tts.monthly_chars_limit > 0 and cfg.tts.credentials_file.is_file():
        synthesizer = GoogleSynthesizer()
    transcriber: Transcriber | None = None
    if settings.groq_api_key is not None:
        transcriber = GroqTranscriber()
    budget = VoiceBudget(cfg.usage_file, cfg.tts.monthly_chars_limit)
    return VoiceServices(
        speech=SpeechService(synthesizer, budget),
        stt=SpeechToText(transcriber),
        max_spoken_chars=cfg.max_spoken_chars,
        idle_timeout=float(cfg.idle_timeout_seconds),
        _closers=tuple(p for p in (synthesizer, transcriber) if p is not None),
    )
