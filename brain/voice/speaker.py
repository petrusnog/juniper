"""Who is speaking: voiceprints, their protection and the comparison (T-213, ADR-034 section 3).

A *voiceprint* is a vector of a few hundred numbers that a model computes from a person's voice;
two clips of the same voice give vectors that point the same way (cosine near 1), two people give
vectors that do not. This module keeps the voiceprints of the people who agreed to it and says,
for an utterance, whose voice it is, or that it is nobody known.

What this is **not**: authentication. Whether a voice is known decides if she *answers* and what
she calls the person. It never lifts a tier, a permission or a confirmation (ADR-006, ADR-010); a
recording of a known voice proves nothing the device's own credential did not already prove.

A voiceprint is biometric data, so:

- it is **encrypted at rest** (AES-256-GCM, the key in a file of the server, mode 600, outside the
  database), bound to its own record so a stolen vector cannot be pasted onto another person;
- it is only created with a recorded **consent** (who agreed, when) and from audio that is thrown
  away right after the vector is computed;
- it is **deleted at once** on request, per voiceprint or for a whole person;
- it never reaches a log, a client or a provider: only the similarity score is ever logged.

The model that turns audio into a vector sits behind `SpeakerEmbedder`; vectors of different
models are not comparable, so each voiceprint remembers which model made it and is ignored (never
mixed) when the configured model changes.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np

from brain.memory.mongo import MongoConnection
from brain.voice.errors import VoiceError

COLLECTION: Final = "voiceprints"
KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
MIN_EMBED_SECONDS: Final = 0.8  # shorter than this the vector says little about who spoke
MIN_SAMPLE_SECONDS: Final = 1.5  # an enrolment sample
MAX_SAMPLE_SECONDS: Final = 30.0
MIN_SAMPLES: Final = 3
MIN_RMS: Final = 300.0  # of 32768: quieter than this is a recording of the room
CLIP_LIMIT: Final = 0.02  # more than 2% of the samples at full scale: clipped
CONSISTENCY: Final = 0.5  # any two samples of one enrolment must resemble each other this much


class EnrollError(Exception):
    """An enrolment that cannot be accepted; ``code`` is stable, the message is for the person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- turning audio into a vector -------------------------------------------------------------


class SpeakerEmbedder(ABC):
    model_id: str  # vectors are only comparable within one model

    @abstractmethod
    def embed(self, pcm: bytes, sample_rate: int) -> np.ndarray:
        """The unit-length voiceprint vector of 16-bit mono PCM. Raises `VoiceError`."""


def seconds_of(pcm: bytes, sample_rate: int) -> float:
    return len(pcm) / 2 / sample_rate


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm == 0.0 or not np.isfinite(norm):
        raise VoiceError("speaker_unavailable", "O modelo de voz devolveu um vetor inválido.")
    return (vector / norm).astype(np.float32)


class SherpaEmbedder(SpeakerEmbedder):
    """The speaker embedding models of sherpa-onnx (WeSpeaker, 3D-Speaker...), on the CPU.

    ``sherpa-onnx`` is an optional dependency and the model a file the operator provides
    (``voice.speaker.model_file``); neither is needed until someone uses voice identification,
    and a missing one is a typed `VoiceError`, never a crash at start-up.
    """

    def __init__(self, model_file: Path, threads: int = 1) -> None:
        self._model_file = model_file
        self._threads = threads
        self._extractor: Any = None
        self._lock = threading.Lock()
        self.model_id = f"sherpa:{model_file.name}"

    def _load(self) -> Any:
        with self._lock:
            if self._extractor is not None:
                return self._extractor
            try:
                import sherpa_onnx  # type: ignore[import-not-found,unused-ignore]
            except ImportError as exc:
                raise VoiceError(
                    "speaker_unavailable",
                    "Falta a biblioteca de reconhecimento de voz (pip install sherpa-onnx).",
                ) from exc
            if not self._model_file.is_file():
                raise VoiceError(
                    "speaker_unavailable",
                    "O modelo de voz não foi encontrado (voice.speaker.model_file).",
                )
            try:
                config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(self._model_file), num_threads=self._threads, provider="cpu"
                )
                self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
            except Exception as exc:  # noqa: BLE001 - a broken model file is a typed error
                raise VoiceError(
                    "speaker_unavailable", "Não consegui carregar o modelo de voz."
                ) from exc
            return self._extractor

    def embed(self, pcm: bytes, sample_rate: int) -> np.ndarray:
        if seconds_of(pcm, sample_rate) < MIN_EMBED_SECONDS:
            raise VoiceError("invalid_audio", "A fala foi curta demais para reconhecer a voz.")
        extractor = self._load()
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        try:
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
            stream.input_finished()
            if not extractor.is_ready(stream):
                raise VoiceError("invalid_audio", "A fala foi curta demais para reconhecer a voz.")
            return _unit(np.asarray(extractor.compute(stream), dtype=np.float32))
        except VoiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VoiceError("speaker_unavailable", "O modelo de voz falhou nesta fala.") from exc


# --- protecting the vectors ------------------------------------------------------------------


class VoiceKey:
    """The AES-256-GCM key of the voiceprints: a file of the server, never the database.

    Losing it makes every stored voiceprint unreadable (the people enrol again); keep it with the
    other server secrets and back it up apart from the database.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError("a chave de voz precisa de 32 bytes")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._aead = AESGCM(key)

    @classmethod
    def load_or_create(cls, path: Path) -> VoiceKey:
        if path.is_file():
            return cls(path.read_bytes())
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(KEY_BYTES)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never world-readable
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        return cls(key)

    def seal(self, plain: bytes, bound_to: bytes) -> bytes:
        nonce = secrets.token_bytes(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, plain, bound_to)

    def open(self, sealed: bytes, bound_to: bytes) -> bytes:
        return self._aead.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], bound_to)


# --- the stored voiceprints ------------------------------------------------------------------


@dataclass(frozen=True)
class Voiceprint:
    voiceprint_id: str
    user_id: str
    label: str
    vector: np.ndarray = field(repr=False, compare=False)  # never printed
    model_id: str
    samples: int
    consent_by: str
    consent_at: datetime
    created_at: datetime


def _bound(voiceprint_id: str, user_id: str) -> bytes:
    return f"{voiceprint_id}|{user_id}".encode()


class VoiceprintStore:
    """The voiceprints in MongoDB, vector encrypted; behind the project's connection ``guard``."""

    def __init__(self, key: VoiceKey, connection: MongoConnection | None = None) -> None:
        self._key = key
        self.connection = connection if connection is not None else MongoConnection()

    def _collection(self) -> Any:
        return self.connection.collection(COLLECTION)

    def add(self, voiceprint: Voiceprint) -> None:
        sealed = self._key.seal(
            voiceprint.vector.astype("<f4").tobytes(),
            _bound(voiceprint.voiceprint_id, voiceprint.user_id),
        )
        with self.connection.guard():
            self._collection().insert_one(
                {
                    "_id": voiceprint.voiceprint_id,
                    "user_id": voiceprint.user_id,
                    "label": voiceprint.label,
                    "vector_sealed": sealed,
                    "dim": int(voiceprint.vector.shape[0]),
                    "model_id": voiceprint.model_id,
                    "samples": voiceprint.samples,
                    "consent_by": voiceprint.consent_by,
                    "consent_at": voiceprint.consent_at,
                    "created_at": voiceprint.created_at,
                }
            )

    def _documents(self, user_id: str | None) -> list[dict[str, Any]]:
        query = {} if user_id is None else {"user_id": user_id}
        with self.connection.guard():
            return list(self._collection().find(query).sort("created_at", 1))

    def info(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """The voiceprints without their vectors (what a listing may show)."""
        return [
            {k: v for k, v in doc.items() if k != "vector_sealed"}
            for doc in self._documents(user_id)
        ]

    def load(self, model_id: str) -> list[Voiceprint]:
        """The usable voiceprints: made by ``model_id`` and readable with the current key.

        One that cannot be read (another key, a damaged record) is skipped, not an error: it
        simply never matches anyone.
        """
        found: list[Voiceprint] = []
        for doc in self._documents(None):
            if doc.get("model_id") != model_id:
                continue
            try:
                raw = self._key.open(
                    bytes(doc["vector_sealed"]), _bound(str(doc["_id"]), str(doc["user_id"]))
                )
            except Exception:  # noqa: BLE001 - wrong key or tampered record
                continue
            found.append(
                Voiceprint(
                    voiceprint_id=str(doc["_id"]),
                    user_id=str(doc["user_id"]),
                    label=str(doc.get("label", "")),
                    vector=np.frombuffer(raw, dtype="<f4").astype(np.float32),
                    model_id=model_id,
                    samples=int(doc.get("samples", 0)),
                    consent_by=str(doc.get("consent_by", "")),
                    consent_at=doc["consent_at"],
                    created_at=doc["created_at"],
                )
            )
        return found

    def remove(self, voiceprint_id: str) -> bool:
        with self.connection.guard():
            result = self._collection().delete_one({"_id": voiceprint_id})
        return bool(result.deleted_count)

    def remove_user(self, user_id: str) -> int:
        with self.connection.guard():
            result = self._collection().delete_many({"user_id": user_id})
        return int(result.deleted_count)


# --- who is it -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """The answer for one utterance. ``score`` is the best cosine similarity found (0 to 1)."""

    known: bool
    user_id: str | None = None
    label: str | None = None
    score: float = 0.0
    margin: float | None = None  # lead over the best *other person* (None when only one is known)
    enrolled: bool = True  # False: nobody has a voiceprint, so nothing could match


class SpeakerIdentifier:
    def __init__(
        self,
        embedder: SpeakerEmbedder,
        store: VoiceprintStore,
        *,
        threshold: float = 0.6,
        margin: float = 0.05,
        ttl: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.threshold = threshold
        self.margin = margin
        self._ttl = ttl
        self._clock = clock
        self._cache: list[Voiceprint] = []
        self._loaded_at: float | None = None

    def invalidate(self) -> None:
        """Forget the cached voiceprints (after an enrolment or a removal)."""
        self._loaded_at = None

    def voiceprints(self) -> list[Voiceprint]:
        if self._loaded_at is None or self._clock() - self._loaded_at >= self._ttl:
            self._cache = self.store.load(self.embedder.model_id)
            self._loaded_at = self._clock()
        return self._cache

    def compare(self, vector: np.ndarray) -> Identity:
        prints = self.voiceprints()
        if not prints:
            return Identity(known=False, enrolled=False)
        best_by_person: dict[str, tuple[float, Voiceprint]] = {}
        for vp in prints:
            if vp.vector.shape != vector.shape:
                continue
            score = float(np.dot(vp.vector, vector))
            held = best_by_person.get(vp.user_id)
            if held is None or score > held[0]:
                best_by_person[vp.user_id] = (score, vp)
        if not best_by_person:
            return Identity(known=False, enrolled=True)
        ranked = sorted(best_by_person.values(), key=lambda item: item[0], reverse=True)
        score, vp = ranked[0]
        lead = score - ranked[1][0] if len(ranked) > 1 else None
        known = score >= self.threshold and (lead is None or lead >= self.margin)
        return Identity(
            known=known,
            user_id=vp.user_id if known else None,
            label=vp.label if known else None,
            score=max(0.0, min(1.0, score)),
            margin=lead,
        )

    def identify(self, pcm: bytes, sample_rate: int) -> Identity:
        """Whose voice is this utterance? Blocking (the model runs); use `identify_async`."""
        return self.compare(self.embedder.embed(pcm, sample_rate))

    async def identify_async(self, pcm: bytes, sample_rate: int) -> Identity:
        return await asyncio.to_thread(self.identify, pcm, sample_rate)


# --- enrolling -------------------------------------------------------------------------------


def check_sample(pcm: bytes, sample_rate: int) -> None:
    """Rejects a sample that would make a bad voiceprint. Raises `EnrollError`."""
    seconds = seconds_of(pcm, sample_rate)
    if seconds < MIN_SAMPLE_SECONDS:
        raise EnrollError("too_short", f"Cada amostra precisa de ao menos {MIN_SAMPLE_SECONDS} s.")
    if seconds > MAX_SAMPLE_SECONDS:
        raise EnrollError(
            "too_long", f"Cada amostra pode ter no máximo {MAX_SAMPLE_SECONDS:.0f} s."
        )
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64)
    if float(np.sqrt(np.mean(samples**2))) < MIN_RMS:
        raise EnrollError("too_quiet", "A amostra está baixa demais (quase só silêncio).")
    if float(np.mean(np.abs(samples) >= 32767)) > CLIP_LIMIT:
        raise EnrollError("clipped", "A amostra estourou o volume; grave mais longe do microfone.")


def enroll(
    identifier: SpeakerIdentifier,
    *,
    user_id: str,
    label: str,
    samples: Sequence[tuple[bytes, int]],
    consent: bool,
    consent_by: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Voiceprint:
    """Creates a voiceprint from several clips of one person speaking. Raises `EnrollError`.

    The audio is used for the vectors and not kept; the stored voiceprint is their normalised
    mean. Clips that do not resemble each other (two people, a TV) are refused, because a mixed
    voiceprint would match nobody well and anybody a little.
    """
    if not consent:
        raise EnrollError("consent", "Sem o consentimento da pessoa não há cadastro de voz.")
    if len(samples) < MIN_SAMPLES:
        raise EnrollError("few_samples", f"Preciso de ao menos {MIN_SAMPLES} amostras.")
    vectors: list[np.ndarray] = []
    for pcm, rate in samples:
        check_sample(pcm, rate)
        try:
            vectors.append(identifier.embedder.embed(pcm, rate))
        except VoiceError as exc:
            raise EnrollError(exc.code, str(exc)) from exc
    mean = np.mean(np.stack(vectors), axis=0)
    try:
        center = _unit(mean)
    except VoiceError as exc:
        raise EnrollError("inconsistent", "As amostras não parecem a mesma voz.") from exc
    # the WORST pair, not the average to the mean: three unrelated voices still "agree" 0.58
    # with their own mean, but two of them never resemble each other
    worst = min(float(np.dot(a, b)) for i, a in enumerate(vectors) for b in vectors[i + 1 :])
    if worst < CONSISTENCY:
        raise EnrollError("inconsistent", "As amostras não parecem ser da mesma pessoa.")
    moment = now()
    voiceprint = Voiceprint(
        voiceprint_id=f"vp_{secrets.token_hex(6)}",
        user_id=user_id,
        label=label.strip() or "voz",
        vector=center,
        model_id=identifier.embedder.model_id,
        samples=len(vectors),
        consent_by=consent_by,
        consent_at=moment,
        created_at=moment,
    )
    identifier.store.add(voiceprint)
    identifier.invalidate()
    return voiceprint


def open_identifier(
    config: Any | None = None, connection: MongoConnection | None = None
) -> SpeakerIdentifier:
    """The identifier from ``voice.speaker`` (reads the key, creating it on first use).

    Raises `VoiceError` ("speaker_unavailable") when no model file is configured.
    """
    from brain.config import settings

    cfg = config or settings.voice.speaker
    if cfg.model_file is None:
        raise VoiceError(
            "speaker_unavailable", "Falta configurar voice.speaker.model_file (o modelo de voz)."
        )
    return SpeakerIdentifier(
        SherpaEmbedder(cfg.model_file, cfg.threads),
        VoiceprintStore(VoiceKey.load_or_create(cfg.key_file), connection),
        threshold=cfg.threshold,
        margin=cfg.margin,
    )
