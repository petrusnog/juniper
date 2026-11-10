"""What a voice session needs from the brain, built once for the whole server (ADR-023).

The cloud voice, its monthly budget and the cloud transcriber are shared by every connection (the
budget and the cool-downs are about the *accounts*, not about who is talking). Building them
never calls a provider; a missing credential just means that side falls back to the device.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from brain.config import settings
from brain.eventlog import event_log
from brain.voice.arbiter import WakeArbiter
from brain.voice.budget import VoiceBudget
from brain.voice.errors import VoiceError
from brain.voice.glossary import Glossary
from brain.voice.speaker import SpeakerIdentifier, open_identifier
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
    arbiter: WakeArbiter = field(default_factory=WakeArbiter)  # which device answers a wake word
    correct_terms: bool = True  # swap sound-alikes of glossary terms (ADR-034)
    repeat_below: float = 0.3  # ask to repeat under this confidence; 0 never asks
    debug_transcripts: bool = False  # log the heard → corrected pair (personal data)
    speaker: SpeakerIdentifier | None = None  # who is speaking; None: not available
    speaker_mode: Literal["off", "shadow", "enforce"] = "off"
    speaker_identify_first: bool = False  # transcribe only voices that were recognised first
    # how a user id becomes the name she says; sync, run off the loop
    name_of: Callable[[str], str | None] | None = None
    name_ttl: float = 60.0  # a disabled user stops being answered within this long
    clock: Callable[[], float] = time.monotonic
    _names: dict[str, tuple[float, str | None]] = field(default_factory=dict)

    async def speaker_name(self, user_id: str) -> str | None:
        """The name of an *active* user (cached briefly); None when unknown or disabled."""
        held = self._names.get(user_id)
        if held is None or self.clock() - held[0] >= self.name_ttl:
            lookup = self.name_of
            name = None if lookup is None else await asyncio.to_thread(_safely, lookup, user_id)
            held = self._names[user_id] = (self.clock(), name)
        return held[1]

    async def speaker_allowed(self, user_id: str) -> bool:
        """May this registered voice be answered? Not if the person was disabled or removed."""
        if self.name_of is None:
            return True  # nothing to ask (no user directory configured)
        return await self.speaker_name(user_id) is not None

    _closers: tuple[Synthesizer | Transcriber, ...] = ()

    async def aclose(self) -> None:
        for provider in self._closers:
            await provider.aclose()


def _safely(lookup: Callable[[str], str | None], user_id: str) -> str | None:
    try:
        return lookup(user_id)
    except Exception:  # noqa: BLE001 - a name is a courtesy: no name, no problem
        return None


def _user_name(user_id: str) -> str | None:
    from brain.auth.store import UserStore

    user = UserStore().get_user(user_id)
    return user.name if user is not None and user.active else None


def open_voice_services() -> VoiceServices:
    cfg = settings.voice
    synthesizer: Synthesizer | None = None
    if cfg.tts.monthly_chars_limit > 0 and cfg.tts.credentials_file.is_file():
        synthesizer = GoogleSynthesizer()
    transcriber: Transcriber | None = None
    if settings.groq_api_key is not None:
        transcriber = GroqTranscriber()
    budget = VoiceBudget(cfg.usage_file, cfg.tts.monthly_chars_limit)
    speaker: SpeakerIdentifier | None = None
    if cfg.speaker.mode != "off":
        try:
            speaker = open_identifier(cfg.speaker)
        except VoiceError as exc:
            # not fatal: `enforce` then ignores every voice (she answers nobody she cannot
            # recognise) and says so in the log, which is the safe side of a broken setup
            event_log.log("error", where="voice.speaker", type=exc.code, mode=cfg.speaker.mode)
    return VoiceServices(
        speech=SpeechService(synthesizer, budget),
        stt=SpeechToText(transcriber),
        max_spoken_chars=cfg.max_spoken_chars,
        idle_timeout=float(cfg.idle_timeout_seconds),
        wake_idle_timeout=float(cfg.wake_idle_seconds),
        reply_budget_chars=cfg.reply_budget,
        glossary=Glossary(cfg.stt.glossary, char_budget=cfg.stt.glossary_chars),
        arbiter=WakeArbiter(cfg.wake_arbitration_ms / 1000),
        correct_terms=cfg.stt.correct_terms,
        repeat_below=cfg.stt.repeat_below_confidence,
        debug_transcripts=cfg.stt.debug_transcripts,
        speaker=speaker,
        speaker_mode=cfg.speaker.mode,
        speaker_identify_first=cfg.speaker.identify_first,
        name_of=_user_name,
        _closers=tuple(p for p in (synthesizer, transcriber) if p is not None),
    )
