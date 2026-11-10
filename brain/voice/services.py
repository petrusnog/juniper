"""What a voice session needs from the brain, built once for the whole server (ADR-023).

The cloud voice, its monthly budget and the cloud transcriber are shared by every connection (the
budget and the cool-downs are about the *accounts*, not about who is talking). Building them
never calls a provider; a missing credential just means that side falls back to the device.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from brain.config import settings
from brain.voice.budget import VoiceBudget
from brain.voice.glossary import Glossary
from brain.voice.speech import SpeechService
from brain.voice.stt import GroqTranscriber, SpeechToText, Transcriber
from brain.voice.tts import GoogleSynthesizer, Synthesizer


@dataclass
class VoiceServices:
    speech: SpeechService
    stt: SpeechToText
    max_spoken_chars: int
    idle_timeout: float
    wake_idle_timeout: float = 15.0
    reply_budget_chars: int = 600  # what the model is asked to stay within (ADR-034)
    glossary: Glossary = field(default_factory=Glossary)  # the vocabulary hint for the STT
    correct_terms: bool = True  # swap sound-alikes of glossary terms (ADR-034)
    repeat_below: float = 0.3  # ask to repeat under this confidence; 0 never asks
    debug_transcripts: bool = False  # log the heard → corrected pair (personal data)
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
        wake_idle_timeout=float(cfg.wake_idle_seconds),
        reply_budget_chars=cfg.reply_budget,
        glossary=Glossary(cfg.stt.glossary, char_budget=cfg.stt.glossary_chars),
        correct_terms=cfg.stt.correct_terms,
        repeat_below=cfg.stt.repeat_below_confidence,
        debug_transcripts=cfg.stt.debug_transcripts,
        _closers=tuple(p for p in (synthesizer, transcriber) if p is not None),
    )
