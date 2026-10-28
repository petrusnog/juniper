"""Speaking: the `Synthesizer` interface and the Google Cloud Text-to-Speech one (ADR-023).

``GoogleSynthesizer`` calls ``text:synthesize`` (Chirp 3 HD, voice ``pt-BR-Chirp3-HD-Leda``) once
per sentence and returns raw PCM 16-bit mono at ``sample_rate``. Auth is a service account in a
mounted file (never in the image or the log): the access token is renewed in a thread, and only
its bearer header travels (the wire log masks ``authorization`` by name).

Every expected failure is a typed `VoiceError` with a fixed PT-BR message: no key, URL, token or
raw provider answer is ever put in one.
"""

from __future__ import annotations

import asyncio
import base64
import io
import threading
import wave
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import httpx
import numpy as np

from brain.config import settings
from brain.httplog import logged_transport
from brain.voice.errors import VoiceError

ENDPOINT: Final = "https://texttospeech.googleapis.com/v1/text:synthesize"
SCOPE: Final = "https://www.googleapis.com/auth/cloud-platform"
MAX_REQUEST_CHARS: Final = 450  # Google accepts up to 5000 bytes; short requests stay fast
_QUOTA_REASONS = {"RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED", "rateLimitExceeded", "quotaExceeded"}


class Synthesizer(ABC):
    """Text in, PCM out. ``sample_rate`` is the rate of the audio ``synthesize`` returns."""

    @property
    @abstractmethod
    def sample_rate(self) -> int: ...

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Raw PCM 16-bit little-endian mono for ``text``. Raises `VoiceError`."""

    async def aclose(self) -> None:
        return None


def split_for_synthesis(text: str, limit: int = MAX_REQUEST_CHARS) -> list[str]:
    """``text`` in pieces of at most ``limit`` characters, cut at spaces (a sentence with no
    punctuation at all must still fit a request)."""
    words, pieces, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > limit:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
        while len(current) > limit:  # a single "word" longer than the limit (a pasted token)
            pieces.append(current[:limit])
            current = current[limit:]
    if current:
        pieces.append(current)
    return pieces


def resample(pcm: bytes, source: int, target: int) -> bytes:
    """PCM 16-bit mono from ``source`` Hz to ``target`` Hz (linear interpolation)."""
    if source == target or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    length = max(1, round(len(samples) * target / source))
    positions = np.linspace(0, len(samples) - 1, num=length)
    out = np.interp(positions, np.arange(len(samples)), samples)
    return np.clip(np.rint(out), -32768, 32767).astype("<i2").tobytes()


def wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    """The PCM frames and the sample rate of a mono 16-bit WAV (the header is dropped)."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                raise VoiceError(
                    "invalid_audio", "O áudio que o Google devolveu não é PCM 16 bits."
                )
            return handle.readframes(handle.getnframes()), handle.getframerate()
    except (wave.Error, EOFError) as exc:
        raise VoiceError("invalid_audio", "O áudio que o Google devolveu está ilegível.") from exc


class ServiceAccountToken:
    """A bearer token for the service account in ``path``, renewed when it is about to expire."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._credentials: Any | None = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._credentials is None:
            try:
                from google.oauth2 import service_account

                self._credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                    str(self._path), scopes=[SCOPE]
                )
            except Exception as exc:  # noqa: BLE001 - OSError, ValueError, AttributeError...
                raise VoiceError(
                    "not_configured",
                    "Falta a credencial do Google para a voz (voice.tts.credentials_file) "
                    "ou ela é inválida.",
                ) from exc
        return self._credentials

    def token(self) -> str:
        """Blocking (a refresh is a network call): call it through ``asyncio.to_thread``."""
        with self._lock:
            credentials = self._load()
            if not credentials.valid:
                try:
                    import google_auth_httplib2
                    import httplib2

                    credentials.refresh(google_auth_httplib2.Request(httplib2.Http(timeout=10)))
                except Exception as exc:  # noqa: BLE001 - google raises many types; none is shown
                    raise VoiceError(
                        "api_error", "Não consegui renovar o acesso ao Google para a voz."
                    ) from exc
            return str(credentials.token)


class GoogleSynthesizer(Synthesizer):
    """Google Cloud Text-to-Speech. Built lazily: constructing it never fails or calls anything."""

    def __init__(
        self,
        *,
        credentials_file: Path | None = None,
        voice: str | None = None,
        language: str | None = None,
        speaking_rate: float | None = None,
        sample_rate: int | None = None,
        timeout: float | None = None,
        token_source: Callable[[], str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        cfg = settings.voice.tts
        self._voice = voice or cfg.voice
        self._language = language or cfg.language
        self._rate = speaking_rate if speaking_rate is not None else cfg.speaking_rate
        self._sample_rate = sample_rate or cfg.sample_rate
        self._token = (
            token_source or ServiceAccountToken(credentials_file or cfg.credentials_file).token
        )
        self._client = httpx.AsyncClient(
            timeout=timeout or cfg.timeout,
            transport=logged_transport("google_tts", transport, redact_bodies=True),
        )

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def aclose(self) -> None:
        await self._client.aclose()

    async def synthesize(self, text: str) -> bytes:
        pieces = split_for_synthesis(" ".join(text.split()))
        if not pieces:
            return b""
        audio = bytearray()
        for piece in pieces:
            audio += await self._one(piece)
        return bytes(audio)

    async def _one(self, text: str) -> bytes:
        token = await asyncio.to_thread(self._token)
        body = {
            "input": {"text": text},
            "voice": {"languageCode": self._language, "name": self._voice},
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": self._sample_rate,
                "speakingRate": self._rate,
            },
        }
        try:
            response = await self._client.post(
                ENDPOINT, json=body, headers={"authorization": f"Bearer {token}"}
            )
        except httpx.TimeoutException as exc:
            raise VoiceError("network_error", "O Google não respondeu a tempo para a voz.") from exc
        except httpx.HTTPError as exc:
            raise VoiceError(
                "network_error", "Não consegui falar com o Google para a voz."
            ) from exc
        self._check(response)
        try:
            wav_bytes = base64.b64decode(response.json()["audioContent"], validate=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise VoiceError("api_error", "O Google devolveu uma resposta ilegível.") from exc
        pcm, rate = wav_to_pcm(wav_bytes)
        return resample(pcm, rate, self._sample_rate)

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            status = str(response.json().get("error", {}).get("status", ""))
        except (ValueError, AttributeError):
            status = ""
        if response.status_code == 429 or status in _QUOTA_REASONS:
            raise VoiceError("quota", "O Google recusou por limite de uso da voz.")
        if response.status_code in (401, 403):
            raise VoiceError(
                "not_configured",
                "O Google recusou a credencial da voz (confira a conta de serviço).",
            )
        if response.status_code == 400:
            raise VoiceError("api_error", "O Google não aceitou o texto ou a voz configurada.")
        raise VoiceError("api_error", f"O Google respondeu com erro ({response.status_code}).")
