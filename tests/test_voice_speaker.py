"""Voiceprints: protected at rest, enrolled with consent, compared with care (T-213, ADR-034)."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

import mongomock
import numpy as np
import pytest

from brain.memory.mongo import MongoConnection
from brain.voice.errors import VoiceError
from brain.voice.speaker import (
    EnrollError,
    SherpaEmbedder,
    SpeakerIdentifier,
    VoiceKey,
    Voiceprint,
    VoiceprintStore,
    enroll,
    open_identifier,
)
from tests.fake_speaker import ANA, ANA_TWIN, BIA, CAIO, RATE, ToneEmbedder, voice_pcm


def connection() -> MongoConnection:
    conn = MongoConnection()
    conn._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return conn


@pytest.fixture
def key(tmp_path: Path) -> VoiceKey:
    return VoiceKey.load_or_create(tmp_path / "speaker.key")


@pytest.fixture
def identifier(key: VoiceKey) -> SpeakerIdentifier:
    return SpeakerIdentifier(
        ToneEmbedder(), VoiceprintStore(key, connection()), threshold=0.6, margin=0.05
    )


def clips(tones: tuple[float, ...], n: int = 3) -> list[tuple[bytes, int]]:
    return [(voice_pcm(tones, 2.0, seed=i), RATE) for i in range(n)]


def sign_up(
    identifier: SpeakerIdentifier, user: str, tones: tuple[float, ...], label: str = "normal"
) -> Voiceprint:
    return enroll(
        identifier,
        user_id=user,
        label=label,
        samples=clips(tones),
        consent=True,
        consent_by="admin",
    )


# --- the key ---------------------------------------------------------------------------------


def test_the_key_file_is_created_private_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "speaker.key"
    first = VoiceKey.load_or_create(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and len(path.read_bytes()) == 32
    sealed = first.seal(b"vector", b"bound")
    assert VoiceKey.load_or_create(path).open(sealed, b"bound") == b"vector"  # same key again


def test_sealed_data_is_bound_to_its_record_and_to_the_key(tmp_path: Path, key: VoiceKey) -> None:
    sealed = key.seal(b"vector", b"vp_1|ana")
    assert b"vector" not in sealed
    assert key.seal(b"vector", b"vp_1|ana") != sealed  # a fresh nonce every time
    with pytest.raises(Exception, match=r".*"):
        key.open(sealed, b"vp_1|bia")  # pasted onto another person
    other = VoiceKey(bytes(32))
    with pytest.raises(Exception, match=r".*"):
        other.open(sealed, b"vp_1|ana")


def test_a_key_of_the_wrong_size_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        VoiceKey(b"short")


# --- the store -------------------------------------------------------------------------------


def stored(identifier: SpeakerIdentifier) -> list[dict]:
    return list(identifier.store.connection.collection("voiceprints").find({}))


def test_the_vector_is_encrypted_in_the_database(identifier: SpeakerIdentifier) -> None:
    vp = sign_up(identifier, "ana", ANA)
    (doc,) = stored(identifier)
    assert vp.vector.astype("<f4").tobytes() not in bytes(doc["vector_sealed"])
    assert "vector" not in doc and doc["consent_by"] == "admin" and doc["dim"] == 64
    assert not any(isinstance(v, np.ndarray) for v in doc.values())


def test_a_stored_voiceprint_comes_back_identical(identifier: SpeakerIdentifier) -> None:
    vp = sign_up(identifier, "ana", ANA)
    (loaded,) = identifier.store.load("fake:tone")
    assert np.allclose(loaded.vector, vp.vector) and loaded.user_id == "ana"
    # MongoDB keeps milliseconds, not microseconds
    kept = vp.consent_at.replace(microsecond=vp.consent_at.microsecond // 1000 * 1000)
    assert loaded.consent_at == kept


def test_listing_never_includes_the_vector(identifier: SpeakerIdentifier) -> None:
    sign_up(identifier, "ana", ANA)
    (info,) = identifier.store.info()
    assert "vector_sealed" not in info and info["user_id"] == "ana"


def test_a_record_moved_to_another_person_stops_working(identifier: SpeakerIdentifier) -> None:
    sign_up(identifier, "ana", ANA)
    identifier.store.connection.collection("voiceprints").update_many(
        {}, {"$set": {"user_id": "bia"}}
    )
    assert identifier.store.load("fake:tone") == []  # the vector was sealed for "ana"


def test_voiceprints_of_another_model_are_ignored_not_mixed(identifier: SpeakerIdentifier) -> None:
    sign_up(identifier, "ana", ANA)
    assert identifier.store.load("sherpa:another-model.onnx") == []


def test_removing_one_or_all_of_a_person_deletes_at_once(identifier: SpeakerIdentifier) -> None:
    first = sign_up(identifier, "ana", ANA, "normal")
    sign_up(identifier, "ana", ANA, "rouca")
    sign_up(identifier, "bia", BIA)
    assert identifier.store.remove(first.voiceprint_id) is True
    assert identifier.store.remove(first.voiceprint_id) is False
    assert identifier.store.remove_user("ana") == 1
    assert [d["user_id"] for d in stored(identifier)] == ["bia"]


def test_a_wrong_key_makes_voiceprints_unreadable_not_an_error(
    identifier: SpeakerIdentifier,
) -> None:
    sign_up(identifier, "ana", ANA)
    other = VoiceprintStore(VoiceKey(bytes(range(32))), identifier.store.connection)
    assert other.load("fake:tone") == []


# --- identifying -----------------------------------------------------------------------------


def test_known_voices_are_recognised_with_their_score(identifier: SpeakerIdentifier) -> None:
    sign_up(identifier, "ana", ANA)
    sign_up(identifier, "bia", BIA)
    heard = identifier.identify(voice_pcm(ANA, 1.5, seed=9), RATE)
    assert heard.known and heard.user_id == "ana" and heard.label == "normal"
    assert heard.score > 0.9 and heard.margin is not None and heard.margin > 0.3
    assert identifier.identify(voice_pcm(BIA, 1.5, seed=9), RATE).user_id == "bia"


def test_a_stranger_is_nobody_and_is_never_taken_for_the_main_user(
    identifier: SpeakerIdentifier,
) -> None:
    sign_up(identifier, "ana", ANA)
    sign_up(identifier, "bia", BIA)
    heard = identifier.identify(voice_pcm(CAIO, 1.5), RATE)
    assert not heard.known and heard.user_id is None and heard.label is None
    assert heard.score < 0.6 and heard.enrolled


def test_two_voices_too_alike_to_tell_apart_are_nobody_rather_than_a_guess(
    identifier: SpeakerIdentifier,
) -> None:
    sign_up(identifier, "ana", ANA)
    sign_up(identifier, "gemea", ANA_TWIN)
    heard = identifier.identify(voice_pcm(ANA, 1.5, seed=4), RATE)
    assert heard.margin is not None
    if heard.margin < identifier.margin:
        assert not heard.known and heard.user_id is None
    else:
        assert heard.known  # they happen to differ enough: then the lead is what decides


def test_the_margin_is_what_separates_close_candidates(identifier: SpeakerIdentifier) -> None:
    sign_up(identifier, "ana", ANA)
    sign_up(identifier, "gemea", ANA_TWIN)
    clip = voice_pcm(ANA, 1.5, seed=4)
    strict = identifier.identify(clip, RATE)
    identifier.margin = 0.0
    loose = identifier.identify(clip, RATE)
    assert loose.known and loose.user_id == "ana"
    assert strict.margin == loose.margin


def test_the_threshold_decides(identifier: SpeakerIdentifier) -> None:
    sign_up(identifier, "ana", ANA)
    clip = voice_pcm(ANA, 1.5, seed=2)
    assert identifier.identify(clip, RATE).known
    identifier.threshold = 1.01
    assert not identifier.identify(clip, RATE).known


def test_nobody_enrolled_is_reported_as_such(identifier: SpeakerIdentifier) -> None:
    heard = identifier.identify(voice_pcm(ANA, 1.5), RATE)
    assert not heard.known and heard.enrolled is False and heard.score == 0.0


def test_an_enrolment_is_seen_at_once_and_a_removal_after_invalidation(
    identifier: SpeakerIdentifier,
) -> None:
    clip = voice_pcm(ANA, 1.5)
    assert identifier.identify(clip, RATE).enrolled is False  # primes the cache
    vp = sign_up(identifier, "ana", ANA)
    assert identifier.identify(clip, RATE).known
    identifier.store.remove(vp.voiceprint_id)
    identifier.invalidate()
    assert identifier.identify(clip, RATE).enrolled is False


def test_a_cached_voiceprint_list_expires(key: VoiceKey) -> None:
    now = [0.0]
    ident = SpeakerIdentifier(
        ToneEmbedder(), VoiceprintStore(key, connection()), ttl=30, clock=lambda: now[0]
    )
    clip = voice_pcm(ANA, 1.5)
    assert not ident.identify(clip, RATE).enrolled
    sign_up(ident, "ana", ANA)  # invalidates
    assert ident.identify(clip, RATE).known
    ident.store.remove_user("ana")  # behind the cache's back (another process)
    assert ident.identify(clip, RATE).known  # still cached...
    now[0] = 31.0
    assert not ident.identify(clip, RATE).enrolled  # ...until the ttl


def test_a_failing_model_is_a_typed_error(identifier: SpeakerIdentifier) -> None:
    assert isinstance(identifier.embedder, ToneEmbedder)
    identifier.embedder.fail = VoiceError("speaker_unavailable", "modelo ausente")
    with pytest.raises(VoiceError) as caught:
        identifier.identify(voice_pcm(ANA, 1.5), RATE)
    assert caught.value.code == "speaker_unavailable"


async def test_the_async_wrapper_gives_the_same_answer(identifier: SpeakerIdentifier) -> None:
    sign_up(identifier, "ana", ANA)
    heard = await identifier.identify_async(voice_pcm(ANA, 1.5), RATE)
    assert heard.known and heard.user_id == "ana"


# --- enrolling -------------------------------------------------------------------------------


def refused(identifier: SpeakerIdentifier, **kw: object) -> EnrollError:
    base: dict = {
        "user_id": "ana",
        "label": "x",
        "samples": clips(ANA),
        "consent": True,
        "consent_by": "admin",
    }
    base.update(kw)
    with pytest.raises(EnrollError) as caught:
        enroll(identifier, **base)
    return caught.value


def test_no_consent_no_voiceprint(identifier: SpeakerIdentifier) -> None:
    assert refused(identifier, consent=False).code == "consent"
    assert stored(identifier) == []


def test_the_enrolment_records_who_agreed_and_when(identifier: SpeakerIdentifier) -> None:
    moment = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    vp = enroll(
        identifier,
        user_id="ana",
        label="  ",
        samples=clips(ANA),
        consent=True,
        consent_by="petrus",
        now=lambda: moment,
    )
    assert (vp.consent_by, vp.consent_at, vp.label, vp.samples) == ("petrus", moment, "voz", 3)
    assert vp.voiceprint_id.startswith("vp_")
    assert "vector" not in repr(vp)  # a voiceprint is never printed


def test_too_few_short_quiet_or_clipped_samples_are_refused(identifier: SpeakerIdentifier) -> None:
    assert refused(identifier, samples=clips(ANA, 2)).code == "few_samples"
    short = [(voice_pcm(ANA, 0.5), RATE)] * 3
    assert refused(identifier, samples=short).code == "too_short"
    silent = [(bytes(RATE * 2 * 2), RATE)] * 3
    assert refused(identifier, samples=silent).code == "too_quiet"
    loud = [(np.full(RATE * 2, 32767, dtype="<i2").tobytes(), RATE)] * 3
    assert refused(identifier, samples=loud).code == "clipped"
    assert stored(identifier) == []


def test_samples_of_different_people_are_refused(identifier: SpeakerIdentifier) -> None:
    mixed = [
        (voice_pcm(ANA, 2.0, seed=1), RATE),
        (voice_pcm(BIA, 2.0, seed=2), RATE),
        (voice_pcm(CAIO, 2.0, seed=3), RATE),
    ]
    assert refused(identifier, samples=mixed).code == "inconsistent"
    assert stored(identifier) == []


def test_a_model_error_while_enrolling_is_an_enrol_error(identifier: SpeakerIdentifier) -> None:
    assert isinstance(identifier.embedder, ToneEmbedder)
    identifier.embedder.fail = VoiceError("speaker_unavailable", "modelo ausente")
    assert refused(identifier).code == "speaker_unavailable"


def test_the_audio_is_not_kept(identifier: SpeakerIdentifier) -> None:
    sign_up(identifier, "ana", ANA)
    (doc,) = stored(identifier)
    assert not any(isinstance(v, bytes) and len(v) > 1000 for v in doc.values())
    assert set(doc) == {
        "_id",
        "user_id",
        "label",
        "vector_sealed",
        "dim",
        "model_id",
        "samples",
        "consent_by",
        "consent_at",
        "created_at",
    }


# --- the real extractor (optional dependency) ------------------------------------------------


def test_the_real_extractor_says_so_when_it_cannot_run(tmp_path: Path) -> None:
    embedder = SherpaEmbedder(tmp_path / "nao-existe.onnx")
    assert embedder.model_id == "sherpa:nao-existe.onnx"
    with pytest.raises(VoiceError) as short:
        embedder.embed(voice_pcm(ANA, 0.3), RATE)
    assert short.value.code == "invalid_audio"  # too short: refused before any model is loaded
    with pytest.raises(VoiceError) as missing:
        embedder.embed(voice_pcm(ANA, 2.0), RATE)
    assert missing.value.code == "speaker_unavailable"  # no library or no model file


def test_without_a_configured_model_there_is_no_identifier() -> None:
    from brain.config import VoiceSpeakerSettings

    with pytest.raises(VoiceError, match="model_file"):
        open_identifier(VoiceSpeakerSettings())
