"""Speech detection (VAD), the Groq transcriber and the cloud/device fallback (T-151)."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from brain.config import VoiceVADSettings
from brain.voice.errors import VoiceError
from brain.voice.speech import Notice
from brain.voice.stt import (
    ENDPOINT,
    GroqTranscriber,
    SpeechToText,
    Transcriber,
    is_hallucination,
    to_wav,
)
from brain.voice.vad import FRAME_MS, SAMPLE_RATE, Vad

# --- the VAD -------------------------------------------------------------------------------


def tone(ms: int, amplitude: int = 6000) -> bytes:
    t = np.arange(SAMPLE_RATE * ms // 1000) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype("<i2").tobytes()


def quiet(ms: int, amplitude: int = 40) -> bytes:
    rng = np.random.default_rng(1)
    return rng.integers(-amplitude, amplitude, SAMPLE_RATE * ms // 1000).astype("<i2").tobytes()


def kinds(events: list) -> list[str]:
    return [e.kind for e in events]


def vad(**kw: int) -> Vad:
    return Vad(VoiceVADSettings(**kw))


def test_silence_never_starts_anything() -> None:
    v = vad()
    assert v.feed(quiet(3000)) == []
    assert not v.in_speech


def test_a_phrase_starts_and_ends_with_the_audio_and_the_preroll() -> None:
    v = vad()
    events = v.feed(quiet(500))
    events += v.feed(tone(1000))
    assert kinds(events) == ["speech_start"] and v.in_speech
    events = v.feed(quiet(900))
    assert kinds(events) == ["utterance"] and not v.in_speech
    audio = events[0].pcm
    # 300 ms of pre-roll + 1000 ms of speech + the 700 ms of silence that closed it
    assert abs(len(audio) / 2 / SAMPLE_RATE * 1000 - 2000) <= 2 * FRAME_MS * 2


def test_chunks_of_any_size_give_the_same_result() -> None:
    signal = quiet(400) + tone(800) + quiet(1000)
    whole = vad().feed(signal)
    pieces: list = []
    v = vad()
    for i in range(0, len(signal), 333):  # odd sizes split frames and even samples
        pieces += v.feed(signal[i : i + 333])
    assert kinds(whole) == kinds(pieces) == ["speech_start", "utterance"]


def test_a_click_is_too_short() -> None:
    v = vad()
    v.feed(quiet(500))
    events = v.feed(tone(140)) + v.feed(quiet(1000))
    assert "utterance" not in kinds(events)


def test_a_short_word_after_the_minimum_is_kept() -> None:
    v = vad()
    v.feed(quiet(500))
    events = v.feed(tone(400)) + v.feed(quiet(900))
    assert kinds(events) == ["speech_start", "utterance"]


def test_a_pause_shorter_than_the_silence_does_not_cut_the_phrase() -> None:
    v = vad()
    v.feed(quiet(500))
    events = v.feed(tone(500)) + v.feed(quiet(400)) + v.feed(tone(500)) + v.feed(quiet(900))
    assert kinds(events) == ["speech_start", "utterance"]


def test_a_very_long_speech_is_cut_at_the_maximum() -> None:
    v = vad(max_utterance_seconds=3)
    v.feed(quiet(300))
    events = v.feed(tone(5000))
    # cut at 3 s; what is still being said starts the next utterance
    assert kinds(events) == ["speech_start", "utterance", "speech_start"]
    assert len(events[1].pcm) / 2 / SAMPLE_RATE <= 3.1


def test_the_floor_follows_a_noisy_room() -> None:
    fresh = vad()
    fresh.feed(quiet(300))
    assert kinds(fresh.feed(quiet(500, amplitude=1000))) == ["speech_start"]  # a new, louder noise

    v = vad()
    v.feed(quiet(8000, amplitude=380))  # a room that was already noisy: the floor follows it
    assert v.feed(quiet(500, amplitude=1000)) == []
    assert kinds(v.feed(tone(500, 9000))) == ["speech_start"]  # but real speech still stands out


def test_while_she_talks_a_blip_does_not_interrupt_but_sustained_speech_does() -> None:
    v = vad()
    v.feed(quiet(500))
    v.set_playing(True)
    assert v.feed(tone(100, 3000)) == [] and not v.in_speech
    v.feed(quiet(300))
    events = v.feed(tone(500, 6000))
    assert kinds(events) == ["barge_in"]
    v.set_playing(False)
    assert kinds(v.feed(quiet(900))) == ["utterance"]


def test_her_own_voice_leaking_at_low_level_does_not_interrupt() -> None:
    v = vad()
    v.feed(quiet(500))
    v.set_playing(True)
    assert v.feed(tone(2000, 600)) == []


def test_reset_forgets_the_utterance_in_progress() -> None:
    v = vad()
    v.feed(quiet(500))
    v.feed(tone(500))
    assert v.in_speech
    v.reset()
    assert not v.in_speech and v.feed(quiet(1000)) == []


# --- hallucinations ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Legendas pela comunidade Amara.org",
        "Obrigado.",
        "Obrigada por assistir!",
        "Thanks for watching",
        "ok ok ok ok ok",
        "...",
    ],
)
def test_known_hallucinations_are_dropped(text: str) -> None:
    assert is_hallucination(text)


@pytest.mark.parametrize(
    "text", ["Toca Djavan", "Obrigado, Juniper, pode tocar uma música?", "sim", "não", "ok ok"]
)
def test_real_speech_is_kept(text: str) -> None:
    assert not is_hallucination(text)


# --- the Groq transcriber ------------------------------------------------------------------


def groq(handler, key: str | None = "gsk-test-key") -> GroqTranscriber:  # type: ignore[no-untyped-def]
    return GroqTranscriber(api_key=key, transport=httpx.MockTransport(handler))


async def test_the_audio_goes_as_wav_with_model_and_language() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": " toca Djavan "})

    text = await groq(handler).transcribe(tone(500), SAMPLE_RATE)
    assert text == "toca Djavan"
    assert seen["url"] == ENDPOINT and seen["auth"] == "Bearer gsk-test-key"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b"whisper-large-v3-turbo" in body and b'name="language"' in body and b"RIFF" in body


async def test_no_key_is_not_configured_and_nothing_is_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    with pytest.raises(VoiceError) as info:
        await groq(handler, key=None).transcribe(tone(500), SAMPLE_RATE)
    assert info.value.code == "not_configured"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (429, "quota"),
        (401, "not_configured"),
        (403, "not_configured"),
        (400, "invalid_audio"),
        (413, "invalid_audio"),
        (500, "api_error"),
    ],
)
async def test_http_errors_are_typed(status: int, code: str) -> None:
    transcriber = groq(
        lambda r: httpx.Response(status, json={"error": {"message": "gsk-test-key"}})
    )
    with pytest.raises(VoiceError) as info:
        await transcriber.transcribe(tone(500), SAMPLE_RATE)
    assert info.value.code == code
    assert "gsk-test-key" not in str(info.value)  # nothing of the provider's answer leaks


async def test_retry_after_is_kept_when_sane() -> None:
    transcriber = groq(lambda r: httpx.Response(429, headers={"retry-after": "42"}))
    with pytest.raises(VoiceError) as info:
        await transcriber.transcribe(tone(500), SAMPLE_RATE)
    assert info.value.retry_after == 42


@pytest.mark.parametrize("header", ["abc", "-5", "0", "999999"])
async def test_a_silly_retry_after_is_ignored(header: str) -> None:
    transcriber = groq(lambda r: httpx.Response(429, headers={"retry-after": header}))
    with pytest.raises(VoiceError) as info:
        await transcriber.transcribe(tone(500), SAMPLE_RATE)
    assert info.value.retry_after is None


async def test_network_failures_are_typed() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    for handler in (boom, slow):
        with pytest.raises(VoiceError) as info:
            await groq(handler).transcribe(tone(500), SAMPLE_RATE)
        assert info.value.code == "network_error"


@pytest.mark.parametrize("payload", [{"nope": 1}, [], {"text": None}])
async def test_an_unreadable_answer_is_typed_or_empty(payload: object) -> None:
    transcriber = groq(lambda r: httpx.Response(200, json=payload))
    try:
        assert await transcriber.transcribe(tone(500), SAMPLE_RATE) == ""
    except VoiceError as exc:
        assert exc.code == "api_error"


async def test_audio_too_large_is_refused_before_sending() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    with pytest.raises(VoiceError) as info:
        await groq(handler).transcribe(b"\0" * (25 * 1024 * 1024), SAMPLE_RATE)
    assert info.value.code == "invalid_audio"


def test_the_wav_is_16_bit_mono_at_the_given_rate() -> None:
    import io
    import wave

    with wave.open(io.BytesIO(to_wav(tone(100), SAMPLE_RATE))) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, SAMPLE_RATE)


# --- the fallback --------------------------------------------------------------------------


class FakeStt(Transcriber):
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None
        self.text = "toca Djavan"

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.text


class Mono:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def stt() -> tuple[SpeechToText, FakeStt, Mono]:
    fake, mono = FakeStt(), Mono()
    return SpeechToText(fake, cooldown_seconds=3600, clock=mono), fake, mono


async def test_a_good_utterance_is_text() -> None:
    s, _, _ = stt()
    heard = await s.listen(b"\0\0", SAMPLE_RATE)
    assert heard.text == "toca Djavan" and heard.notices == [] and not heard.local and s.cloud


async def test_a_hallucination_is_dropped_without_a_turn() -> None:
    s, fake, _ = stt()
    fake.text = "Legendas pela comunidade Amara.org"
    heard = await s.listen(b"\0\0", SAMPLE_RATE)
    assert heard.text is None and not heard.local


async def test_the_limit_hands_over_to_the_device_once_and_waits() -> None:
    s, fake, mono = stt()
    fake.error = VoiceError("quota", "cota", retry_after=120)
    first = await s.listen(b"\0\0", SAMPLE_RATE)
    assert first.local and first.text is None and first.notices == [Notice("local", "quota")]
    again = await s.listen(b"\0\0", SAMPLE_RATE)
    assert again.local and again.notices == [] and fake.calls == 1  # no call, no second notice
    assert s.waiting() and not s.cloud
    assert s.recovered() is None
    mono.now += 121
    assert not s.waiting()


async def test_without_retry_after_the_configured_cooldown_is_used() -> None:
    s, fake, mono = stt()
    fake.error = VoiceError("quota", "cota")
    await s.listen(b"\0\0", SAMPLE_RATE)
    mono.now += 3599
    assert s.waiting() and s.recovered() is None
    mono.now += 2
    assert s.recovered() == Notice("cloud", "recovered")
    assert s.recovered() is None  # told once


async def test_after_a_recovery_notice_a_new_failure_goes_local_again() -> None:
    s, fake, mono = stt()
    fake.error = VoiceError("quota", "cota", retry_after=10)
    await s.listen(b"\0\0", SAMPLE_RATE)
    mono.now += 11
    assert s.recovered() is not None
    again = await s.listen(b"\0\0", SAMPLE_RATE)
    assert again.local and again.notices == [Notice("local", "quota")]


async def test_if_the_client_keeps_sending_audio_success_reports_the_return() -> None:
    s, fake, mono = stt()
    fake.error = VoiceError("network_error", "rede")
    await s.listen(b"\0\0", SAMPLE_RATE)
    mono.now += 31
    fake.error = None
    heard = await s.listen(b"\0\0", SAMPLE_RATE)
    assert heard.text == "toca Djavan" and heard.notices == [Notice("cloud", "recovered")]


async def test_a_bug_in_the_provider_is_not_an_exception() -> None:
    s, fake, _ = stt()
    fake.error = RuntimeError("boom")
    heard = await s.listen(b"\0\0", SAMPLE_RATE)
    assert heard.local and heard.notices == [Notice("local", "error")]


async def test_without_a_transcriber_the_device_always_listens() -> None:
    s = SpeechToText(None, clock=Mono())
    heard = await s.listen(b"\0\0", SAMPLE_RATE)
    assert heard.local and heard.notices == [] and s.recovered() is None


async def test_logs_carry_sizes_never_the_words(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.voice import stt as stt_module

    seen: list[dict[str, object]] = []
    monkeypatch.setattr(stt_module.event_log, "log", lambda kind, **kw: seen.append(kw))
    s, fake, _ = stt()
    fake.text = "meu segredo particular"
    await s.listen(b"\0\0", SAMPLE_RATE)
    fake.error = VoiceError("quota", "x")
    await s.listen(b"\0\0", SAMPLE_RATE)
    assert seen and "segredo" not in json.dumps(seen)


def test_repeating_the_same_playing_state_does_not_restart_the_count() -> None:
    """The session tells the detector the state on every chunk of audio."""
    v = vad()
    v.feed(quiet(500))
    events = []
    for _ in range(10):
        v.set_playing(False)
        events += v.feed(tone(20))
    assert kinds(events) == ["speech_start"]
