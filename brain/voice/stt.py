"""Listening: the `Transcriber` interface, Groq's Whisper and the cloud/device fallback (ADR-023).

``GroqTranscriber`` sends each utterance as a 16 kHz mono WAV to ``/audio/transcriptions``
(``whisper-large-v3-turbo``, ``language=pt``) with the Groq key the brain already has. Whisper
invents text on silence ("Legendas pela comunidade Amara.org"), so `is_hallucination` drops
those before they become a turn.

``SpeechToText`` is what the voice session uses: it tries the cloud and, when the free limit is
reached (429), leaves it alone for the ``retry-after`` (or ``voice.stt.cooldown_seconds``) and
tells the client to listen by itself (``voice.fallback``); when the time passes it tries again
and, if it works, reports that the cloud is back. Only sizes are logged, never the words.
"""

from __future__ import annotations

import io
import math
import re
import time
import unicodedata
import wave
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

import httpx

from brain.config import settings
from brain.eventlog import event_log
from brain.httplog import logged_transport
from brain.voice.errors import VoiceError
from brain.voice.speech import Notice

ENDPOINT: Final = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_AUDIO_BYTES: Final = 24 * 1024 * 1024  # Groq's free tier takes up to 25 MB
ERROR_RETRY_SECONDS: Final = 30.0

# What Whisper "hears" in silence or noise (normalised: lowercase, no accents or punctuation).
_HALLUCINATIONS: Final = frozenset(
    {
        "legendas pela comunidade amara org",
        "legendado pela comunidade amara org",
        "legendas pela comunidade",
        "legenda adriana zanotto",
        "legendas em portugues",
        "obrigado por assistir",
        "obrigada por assistir",
        "obrigado",
        "obrigada",
        "tchau",
        "ate a proxima",
        "ate o proximo video",
        "inscreva se no canal",
        "se inscreva no canal",
        "thanks for watching",
        "thank you for watching",
        "thank you",
        "you",
        "tv globo",
        "musica",
        "continua",
        "fim",
    }
)


def normalise(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def is_hallucination(text: str) -> bool:
    """True for empty text, a known Whisper phrase on silence, or the same word over and over."""
    plain = normalise(text)
    if not plain or plain in _HALLUCINATIONS:
        return True
    words = plain.split()
    return len(words) >= 4 and len(set(words)) == 1


def is_prompt_echo(text: str, prompt: str) -> bool:
    """True when the engine wrote the vocabulary hint back instead of what was said.

    Whisper does that on silence and noise when it is given a prompt. The hint starts with a
    fixed label, and an echo repeats its opening, or is only words that came from the hint.
    """
    if not prompt:
        return False
    heard = normalise(text).split()
    if not heard:
        return False
    hint = normalise(prompt).split()
    opening = hint[:3]  # the label and the first terms: what an echo repeats from the top
    return heard[: len(opening)] == opening or (len(heard) >= 4 and set(heard) <= set(hint))


def to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


# Whisper's own rule of thumb for "this segment is not speech": it thinks there is no speech AND
# it is unsure of what it wrote. Both together, so a quiet but clear word is never dropped.
SILENT_NO_SPEECH: Final = 0.6
SILENT_LOGPROB: Final = -1.0


@dataclass(frozen=True)
class Transcription:
    """What a transcriber heard, with how sure it was (ADR-034, section 4).

    ``confidence`` is 0..1 (the geometric-mean probability of the words, ``exp(avg_logprob)``),
    None when the engine does not say. ``silent`` is true when every segment looks like noise the
    engine wrote words for: the voice session drops it like a known hallucination.
    """

    text: str
    confidence: float | None = None
    silent: bool = False


class Transcriber(ABC):
    @abstractmethod
    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """The text of 16-bit mono PCM. Raises `VoiceError`."""

    async def transcribe_detailed(
        self, pcm: bytes, sample_rate: int, prompt: str = ""
    ) -> Transcription:
        """Like `transcribe`, with a vocabulary ``prompt`` and the engine's confidence.

        The default wraps `transcribe` (no prompt, no confidence), so an engine that knows
        nothing of either keeps working unchanged.
        """
        return Transcription(await self.transcribe(pcm, sample_rate))

    async def aclose(self) -> None:
        return None


class GroqTranscriber(Transcriber):
    """Groq's Whisper. Built lazily: constructing it never fails or calls anything."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        language: str | None = None,
        timeout: float | None = None,
        contextual: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        cfg = settings.voice.stt
        # off: the request is exactly the plain one (`json`, no hint); on: `verbose_json` for the
        # confidence and the vocabulary hint (ADR-034)
        self._contextual = cfg.contextual if contextual is None else contextual
        key = api_key
        if key is None and settings.groq_api_key is not None:
            key = settings.groq_api_key.get_secret_value()
        self._key = key
        self._model = model or cfg.model
        self._language = language or cfg.language
        self._client = httpx.AsyncClient(
            timeout=timeout or cfg.timeout,
            transport=logged_transport("groq_stt", transport, redact_bodies=True),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        return (await self.transcribe_detailed(pcm, sample_rate)).text

    async def transcribe_detailed(
        self, pcm: bytes, sample_rate: int, prompt: str = ""
    ) -> Transcription:
        if not self._key:
            raise VoiceError("not_configured", "A chave da Groq não está configurada.")
        wav = to_wav(pcm, sample_rate)
        if len(wav) > MAX_AUDIO_BYTES:
            raise VoiceError("invalid_audio", "A fala ficou longa demais para transcrever.")
        try:
            response = await self._client.post(
                ENDPOINT,
                headers={"authorization": f"Bearer {self._key}"},
                data={
                    "model": self._model,
                    "language": self._language,
                    "response_format": "verbose_json" if self._contextual else "json",
                    "temperature": "0",
                    **({"prompt": prompt} if prompt and self._contextual else {}),
                },
                files={"file": ("fala.wav", wav, "audio/wav")},
            )
        except httpx.TimeoutException as exc:
            raise VoiceError("network_error", "A Groq não respondeu a tempo.") from exc
        except httpx.HTTPError as exc:
            raise VoiceError("network_error", "Não consegui falar com a Groq.") from exc
        self._check(response)
        try:
            body = response.json()
            text = body["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise VoiceError("api_error", "A Groq devolveu uma resposta ilegível.") from exc
        confidence, silent = _confidence(body.get("segments") if isinstance(body, dict) else None)
        return Transcription(
            text.strip() if isinstance(text, str) else "", confidence=confidence, silent=silent
        )

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        if response.status_code == 429:
            raise VoiceError(
                "quota",
                "A Groq recusou por limite de uso da transcrição.",
                retry_after=_retry_after(response),
            )
        if response.status_code in (401, 403):
            raise VoiceError(
                "not_configured", "A Groq recusou a chave (confira JUNIPER_GROQ_API_KEY)."
            )
        if response.status_code in (400, 413, 422):
            raise VoiceError("invalid_audio", "A Groq não aceitou o áudio.")
        raise VoiceError("api_error", f"A Groq respondeu com erro ({response.status_code}).")


def _confidence(segments: object) -> tuple[float | None, bool]:
    """The confidence of the speech and whether it is all noise, from Whisper's segments.

    Segments weigh by their length. Anything malformed means "unknown", never an error: the text
    is what matters and a missing field must not cost the user a turn.
    """
    if not isinstance(segments, list) or not segments:
        return None, False
    weighted = total = 0.0
    silent = 0
    for segment in segments:
        if not isinstance(segment, dict):
            return None, False
        logprob = segment.get("avg_logprob")
        no_speech = segment.get("no_speech_prob")
        if not isinstance(logprob, (int, float)) or isinstance(logprob, bool):
            return None, False
        if (
            isinstance(no_speech, (int, float))
            and no_speech > SILENT_NO_SPEECH
            and logprob < SILENT_LOGPROB
        ):
            silent += 1
            continue
        start, end = segment.get("start"), segment.get("end")
        length = (
            end - start
            if isinstance(start, (int, float)) and isinstance(end, (int, float))
            else 1.0
        )
        weight = max(length, 0.05)
        weighted += weight * logprob
        total += weight
    if silent == len(segments):
        return None, True
    return min(1.0, max(0.0, math.exp(weighted / total))), False


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after", "")
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if 0 < seconds <= 86_400 else None


@dataclass
class Heard:
    """What an utterance became: ``text`` (None when dropped or when the device should listen)."""

    text: str | None = None
    notices: list[Notice] = field(default_factory=list)
    local: bool = False  # True when the cloud could not be used: the client transcribes
    confidence: float | None = None  # 0..1, when the engine says (ADR-034)


class SpeechToText:
    """The cloud transcriber with a cool-down and a report of each change of mode."""

    def __init__(
        self,
        transcriber: Transcriber | None,
        *,
        cooldown_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stt = transcriber
        self._cooldown = (
            cooldown_seconds
            if cooldown_seconds is not None
            else settings.voice.stt.cooldown_seconds
        )
        self._clock = clock
        self._retry_at = 0.0
        self._local = transcriber is None

    @property
    def cloud(self) -> bool:
        """True while the cloud transcriber is the one in use."""
        return not self._local

    def waiting(self) -> bool:
        return self._clock() < self._retry_at

    def recovered(self) -> Notice | None:
        """The notice that the cloud is worth trying again, once the cool-down has passed.

        Once the client listens by itself it stops sending audio, so the brain is the one that
        has to say when to start again; if the cloud fails again the next ``listen`` reverts it.
        """
        if self._stt is None or not self._local or self.waiting():
            return None
        self._local = False
        return Notice("cloud", "recovered")

    async def listen(self, pcm: bytes, sample_rate: int, prompt: str = "") -> Heard:
        """Transcribe one utterance. Never raises: a failure is ``local=True`` plus one notice.

        ``prompt`` is the vocabulary hint for the engine (the glossary of ADR-034).
        """
        if self._stt is None or self.waiting():
            return Heard(local=True)
        try:
            heard = await self._stt.transcribe_detailed(pcm, sample_rate, prompt)
            text = heard.text
        except VoiceError as exc:
            pause = exc.retry_after or (
                self._cooldown if exc.code in ("quota", "not_configured") else ERROR_RETRY_SECONDS
            )
            return self._failed(exc.code, pause, "quota" if exc.code == "quota" else "error")
        except Exception as exc:  # noqa: BLE001 - a bug in the provider must not end the conversation
            return self._failed(type(exc).__name__, ERROR_RETRY_SECONDS, "error")
        notices: list[Notice] = []
        if self._local:
            self._local = False
            notices.append(Notice("cloud", "recovered"))
        if heard.silent or is_hallucination(text) or is_prompt_echo(text, prompt):
            event_log.log("voice", action="stt", dropped=True, chars=len(text), silent=heard.silent)
            return Heard(notices=notices)
        fields: dict[str, float | int] = {"chars": len(text), "prompt_chars": len(prompt)}
        if heard.confidence is not None:
            fields["confidence"] = round(heard.confidence, 2)
        event_log.log("voice", action="stt", mode="cloud", **fields)
        return Heard(text=text, notices=notices, confidence=heard.confidence)

    def _failed(self, code: str, pause: float, reason: str) -> Heard:
        self._retry_at = self._clock() + pause
        event_log.log("voice", action="stt_failed", code=code, pause=int(pause))
        notices = []
        if not self._local:
            self._local = True
            notices.append(Notice("local", "quota" if reason == "quota" else "error"))
        return Heard(notices=notices, local=True)
