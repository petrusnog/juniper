"""Speaking (T-149, ADR-023): text prepared for speech, sentences from a stream, Google TTS."""

from __future__ import annotations

import base64
import io
import json
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from brain.config import VoiceSettings, settings
from brain.voice.errors import VoiceError
from brain.voice.text import (
    CODE_PLACEHOLDER,
    LINK_PLACEHOLDER,
    TRUNCATED_NOTICE,
    SentenceChunker,
    speech_text,
)
from brain.voice.tts import (
    MAX_REQUEST_CHARS,
    GoogleSynthesizer,
    ServiceAccountToken,
    resample,
    split_for_synthesis,
    wav_to_pcm,
)

TOKEN = "ya29.token-de-acesso-da-voz"


# --- speech_text ---------------------------------------------------------------------------


class TestSpeechText:
    def test_markdown_marks_disappear_but_the_words_stay(self) -> None:
        assert speech_text("**Oi**, tudo _bem_? Veja `isto` e ~~aquilo~~.") == (
            "Oi, tudo bem? Veja isto e aquilo."
        )

    def test_headings_bullets_and_numbered_lists_become_sentences(self) -> None:
        text = "# Título\n- item um\n* item dois\n1. primeiro\n2) segundo\n\nFim."
        assert speech_text(text) == "Título. item um. item dois. primeiro. segundo. Fim."

    def test_urls_are_never_read(self) -> None:
        text = "Veja https://music.youtube.com/watch?v=abc123 e www.exemplo.com/x agora."
        assert speech_text(text) == "Veja e agora."
        assert "http" not in speech_text(text) and "www" not in speech_text(text)

    def test_a_link_can_be_announced_once_instead(self) -> None:
        spoken = speech_text("Está em https://exemplo.com/a", say_link=True)
        assert spoken == f"Está em. {LINK_PLACEHOLDER}" or LINK_PLACEHOLDER in spoken
        assert "exemplo" not in spoken

    def test_a_markdown_link_keeps_its_label(self) -> None:
        assert speech_text("Abra [a página](https://exemplo.com/a).") == "Abra a página."

    def test_code_blocks_are_replaced_by_one_sentence(self) -> None:
        spoken = speech_text("Aqui:\n```python\nprint(1)\nprint(2)\n```\nPronto.")
        assert CODE_PLACEHOLDER in spoken and "print" not in spoken and spoken.endswith("Pronto.")

    def test_emojis_and_invisible_characters_are_dropped(self) -> None:
        assert speech_text("oi 😀 você 👨‍👩‍👧 tudo  bem") == "oi você tudo bem."

    def test_tables_lose_their_rules_and_pipes(self) -> None:
        assert speech_text("| a | b |\n|---|---|\n| 1 | 2 |") == "a b. 1 2."

    def test_horizontal_rules_are_dropped(self) -> None:
        assert speech_text("Texto\n\n---\n\nOutro") == "Texto. Outro."

    def test_a_dash_in_a_sentence_survives(self) -> None:
        assert speech_text("Valor 3 - 2 = 1 ok") == "Valor 3 - 2 = 1 ok."

    @pytest.mark.parametrize("text", ["", "   ", "😀😀", "https://exemplo.com", "---", "```\n```"])
    def test_nothing_speakable_is_empty_or_a_placeholder(self, text: str) -> None:
        spoken = speech_text(text)
        assert spoken in ("", CODE_PLACEHOLDER)

    def test_it_always_ends_with_a_full_stop_when_there_is_text(self) -> None:
        assert speech_text("Tudo certo") == "Tudo certo."
        assert speech_text("Tudo certo?") == "Tudo certo?"


# --- SentenceChunker -----------------------------------------------------------------------


def stream(chunker: SentenceChunker, pieces: list[str]) -> list[str]:
    out: list[str] = []
    for piece in pieces:
        out += chunker.feed(piece)
    return out + chunker.flush()


class TestSentenceChunker:
    def test_sentences_come_out_in_order_as_they_complete(self) -> None:
        chunker = SentenceChunker()
        assert chunker.feed("Achei três músicas parecidas. Vou tocar a ") == [
            "Achei três músicas parecidas."
        ]
        assert chunker.feed("primeira agora, ok? Um instante") == [
            "Vou tocar a primeira agora, ok?"
        ]
        assert chunker.flush() == ["Um instante."]

    def test_the_split_does_not_depend_on_where_the_tokens_break(self) -> None:
        text = "Achei três músicas parecidas. Vou tocar a primeira agora, ok? Um instante."
        expected = stream(SentenceChunker(), [text])
        for size in (1, 2, 3, 5, 7, 11):
            pieces = [text[i : i + size] for i in range(0, len(text), size)]
            assert stream(SentenceChunker(), pieces) == expected, size

    def test_a_decimal_number_does_not_split_a_sentence(self) -> None:
        out = stream(SentenceChunker(), ["O valor é 3.5 reais por mês, mais ou menos isso."])
        assert out == ["O valor é 3.5 reais por mês, mais ou menos isso."]

    def test_short_sentences_are_joined_to_the_next_one(self) -> None:
        out = stream(SentenceChunker(min_chars=10), ["Oi. Tudo bem por aqui? Que bom!"])
        assert out == ["Oi. Tudo bem por aqui?", "Que bom!"]  # "Oi." rode along; "Que bom!" is last
        # With the default minimum (24) two short ones travel together:
        assert stream(SentenceChunker(), ["Oi. Tudo bem por aqui? Que bom!"]) == [
            "Oi. Tudo bem por aqui? Que bom!"
        ]

    def test_a_short_last_sentence_is_still_spoken(self) -> None:
        assert stream(SentenceChunker(), ["Pronto."]) == ["Pronto."]

    def test_newlines_end_a_sentence(self) -> None:
        out = stream(SentenceChunker(min_chars=1), ["Primeira linha\nSegunda linha\n"])
        assert out == ["Primeira linha.", "Segunda linha."]

    def test_a_code_block_is_held_until_it_closes_and_spoken_as_one_sentence(self) -> None:
        pieces = ["Veja:\n```py", "thon\nprint(1)\n", "```\nPronto, é isso aí."]
        out = stream(SentenceChunker(min_chars=1), pieces)
        assert out.count(CODE_PLACEHOLDER) == 1 and not any("print" in s for s in out)
        assert out[-1] == "Pronto, é isso aí."

    def test_an_unclosed_code_block_at_the_end_is_not_read(self) -> None:
        out = stream(SentenceChunker(min_chars=1), ["Veja:\n```python\nprint(1)\nainda aberto"])
        assert not any("print" in s or "aberto" in s for s in out)

    def test_urls_are_announced_once_per_reply(self) -> None:
        pieces = ["Veja https://a.example/x agora mesmo. ", "E também https://b.example/y depois."]
        out = stream(SentenceChunker(min_chars=1), pieces)
        joined = " ".join(out)
        assert joined.count(LINK_PLACEHOLDER) == 1 and "example" not in joined

    def test_the_spoken_total_is_capped_and_the_rest_is_announced(self) -> None:
        sentences = [f"Esta é a frase número {i}, com um tamanho razoável." for i in range(20)]
        chunker = SentenceChunker(max_chars=200)
        out = stream(chunker, [" ".join(sentences)])
        assert chunker.truncated and out[-1] == TRUNCATED_NOTICE
        spoken = "".join(out[:-1])
        assert 0 < len(spoken) <= 200 and chunker.spoken_chars == len(spoken)

    def test_a_reply_inside_the_cap_is_not_marked_truncated(self) -> None:
        chunker = SentenceChunker(max_chars=800)
        out = stream(chunker, ["Uma resposta curta mas completa."])
        assert not chunker.truncated and TRUNCATED_NOTICE not in out

    def test_nothing_speakable_gives_no_sentences(self) -> None:
        assert stream(SentenceChunker(), ["😀", " ", "😀😀"]) == []

    def test_a_reply_that_is_only_a_link_says_the_link_is_on_the_screen(self) -> None:
        assert stream(SentenceChunker(), ["https://exemplo.com/a"]) == [LINK_PLACEHOLDER]

    def test_several_code_blocks_are_announced_once(self) -> None:
        text = "Um:\n```a\n1\n```\nDois:\n```b\n2\n```\nPronto, terminei de explicar tudo."
        out = stream(SentenceChunker(min_chars=1), [text])
        assert out.count(CODE_PLACEHOLDER) == 1 and not any(c in " ".join(out) for c in "12")


# --- helpers -------------------------------------------------------------------------------


def make_wav(samples: np.ndarray, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


def tone(count: int = 2400, value: int = 1000) -> np.ndarray:
    return np.full(count, value, dtype="<i2")


class TestHelpers:
    def test_split_keeps_short_text_whole_and_cuts_long_text_at_spaces(self) -> None:
        assert split_for_synthesis("uma frase curta") == ["uma frase curta"]
        pieces = split_for_synthesis("palavra " * 200, limit=100)
        assert all(len(p) <= 100 for p in pieces) and " ".join(pieces).split() == ["palavra"] * 200

    def test_split_handles_a_token_longer_than_the_limit(self) -> None:
        pieces = split_for_synthesis("x" * 1000, limit=300)
        assert [len(p) for p in pieces] == [300, 300, 300, 100]

    def test_split_of_nothing_is_nothing(self) -> None:
        assert split_for_synthesis("") == [] and split_for_synthesis("   ") == []

    def test_resample_changes_the_length_in_proportion_and_keeps_a_constant(self) -> None:
        pcm = tone(2400).tobytes()
        down = resample(pcm, 24000, 16000)
        assert len(down) // 2 == 1600
        assert set(np.frombuffer(down, dtype="<i2").tolist()) == {1000}
        up = resample(pcm, 16000, 24000)
        assert len(up) // 2 == 3600

    def test_resample_is_the_identity_at_the_same_rate_and_safe_on_nothing(self) -> None:
        pcm = tone(10).tobytes()
        assert resample(pcm, 24000, 24000) == pcm and resample(b"", 24000, 16000) == b""

    def test_resample_never_overflows(self) -> None:
        loud = np.array([32767, -32768] * 100, dtype="<i2").tobytes()
        out = np.frombuffer(resample(loud, 16000, 24000), dtype="<i2")
        assert out.max() <= 32767 and out.min() >= -32768

    def test_wav_round_trip_drops_the_header(self) -> None:
        pcm, rate = wav_to_pcm(make_wav(tone(100, 7), rate=22050))
        assert rate == 22050 and pcm == tone(100, 7).tobytes()

    @pytest.mark.parametrize("wav", [b"", b"nada de wav", make_wav(tone(10), channels=2)])
    def test_a_bad_wav_is_a_typed_error(self, wav: bytes) -> None:
        with pytest.raises(VoiceError) as exc:
            wav_to_pcm(wav)
        assert exc.value.code == "invalid_audio"


# --- the Google synthesizer ----------------------------------------------------------------


def audio_response(samples: np.ndarray, rate: int = 24000) -> httpx.Response:
    content = base64.b64encode(make_wav(samples, rate)).decode()
    return httpx.Response(200, json={"audioContent": content})


def make(handler: Any, **kwargs: Any) -> GoogleSynthesizer:
    return GoogleSynthesizer(
        token_source=lambda: TOKEN, transport=httpx.MockTransport(handler), **kwargs
    )


async def test_it_sends_the_voice_the_rate_and_a_bearer_token_and_returns_pcm() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return audio_response(tone(2400))

    pcm = await make(handler).synthesize("Oi, Petrus.")
    [request] = seen
    body = json.loads(request.content)
    assert str(request.url) == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert body["input"] == {"text": "Oi, Petrus."}
    assert body["voice"] == {"languageCode": "pt-BR", "name": "pt-BR-Chirp3-HD-Leda"}
    assert body["audioConfig"] == {
        "audioEncoding": "LINEAR16",
        "sampleRateHertz": 24000,
        "speakingRate": 0.95,
    }
    assert pcm == tone(2400).tobytes()


async def test_the_defaults_come_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.voice.tts, "voice", "pt-BR-Chirp3-HD-Aoede")
    monkeypatch.setattr(settings.voice.tts, "speaking_rate", 1.1)
    monkeypatch.setattr(settings.voice.tts, "sample_rate", 16000)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return audio_response(tone(1600), rate=16000)

    synth = make(handler)
    assert synth.sample_rate == 16000
    await synth.synthesize("oi")
    assert seen[0]["voice"]["name"] == "pt-BR-Chirp3-HD-Aoede"
    assert seen[0]["audioConfig"]["speakingRate"] == 1.1


async def test_a_long_text_goes_in_several_requests_in_order() -> None:
    texts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts.append(json.loads(request.content)["input"]["text"])
        return audio_response(tone(10, len(texts)))

    pcm = await make(handler).synthesize("palavra " * 300)
    assert len(texts) > 1 and all(len(t) <= MAX_REQUEST_CHARS for t in texts)
    assert np.frombuffer(pcm, dtype="<i2").tolist() == [
        i for i in range(1, len(texts) + 1) for _ in range(10)
    ]


async def test_nothing_to_say_calls_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar o Google")

    assert await make(handler).synthesize("   ") == b""


async def test_audio_at_another_rate_is_resampled_to_the_configured_one() -> None:
    synth = make(lambda r: audio_response(tone(1600), rate=16000), sample_rate=24000)
    pcm = await synth.synthesize("oi")
    assert len(pcm) // 2 == 2400


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}), "quota"),
        (httpx.Response(403, json={"error": {"status": "RESOURCE_EXHAUSTED"}}), "quota"),
        (httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}}), "not_configured"),
        (httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}}), "not_configured"),
        (httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}}), "api_error"),
        (httpx.Response(500, text="boom"), "api_error"),
        (httpx.Response(200, text="não é json"), "api_error"),
        (httpx.Response(200, json={}), "api_error"),
        (httpx.Response(200, json={"audioContent": "isto não é base64!"}), "api_error"),
        (
            httpx.Response(200, json={"audioContent": base64.b64encode(b"nao-wav").decode()}),
            "invalid_audio",
        ),
    ],
)
async def test_failures_are_typed_and_never_leak_the_token(
    response: httpx.Response, code: str
) -> None:
    with pytest.raises(VoiceError) as exc:
        await make(lambda r: response).synthesize("Oi.")
    assert exc.value.code == code
    assert TOKEN not in exc.value.message and "googleapis" not in exc.value.message


@pytest.mark.parametrize("failure", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
async def test_network_failures_are_typed(failure: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(VoiceError) as exc:
        await make(handler).synthesize("Oi.")
    assert exc.value.code == "network_error"


async def test_a_failing_token_source_comes_through_typed() -> None:
    def broken() -> str:
        raise VoiceError("not_configured", "Falta a credencial do Google para a voz.")

    synth = GoogleSynthesizer(
        token_source=broken, transport=httpx.MockTransport(lambda r: audio_response(tone()))
    )
    with pytest.raises(VoiceError) as exc:
        await synth.synthesize("Oi.")
    assert exc.value.code == "not_configured"


# --- the service account -------------------------------------------------------------------


def write_service_account(path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "juniper-voz",
                "private_key_id": "abc",
                "private_key": pem,
                "client_email": "juniper-tts@juniper-voz.iam.gserviceaccount.com",
                "client_id": "1",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )


class TestServiceAccountToken:
    @pytest.mark.parametrize("content", [None, "", "{", "[]", '{"type":"service_account"}'])
    def test_a_missing_or_invalid_file_is_not_configured(
        self, tmp_path: Path, content: str | None
    ) -> None:
        path = tmp_path / "gcp-voice.json"
        if content is not None:
            path.write_text(content)
        with pytest.raises(VoiceError) as exc:
            ServiceAccountToken(path).token()
        assert exc.value.code == "not_configured"
        assert str(path) not in exc.value.message  # the path of a secret is not shown either

    def test_a_valid_file_renews_the_token_only_when_it_is_not_valid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from google.oauth2 import service_account

        path = tmp_path / "gcp-voice.json"
        write_service_account(path)
        refreshes: list[int] = []

        def fake_refresh(self: Any, request: Any) -> None:
            refreshes.append(1)
            self.token = f"tok-{len(refreshes)}"
            self.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

        monkeypatch.setattr(service_account.Credentials, "refresh", fake_refresh)
        source = ServiceAccountToken(path)
        assert source.token() == "tok-1"
        assert source.token() == "tok-1"  # still valid: no second refresh
        assert refreshes == [1]

    def test_a_failing_refresh_is_typed_and_hides_the_cause(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from google.oauth2 import service_account

        path = tmp_path / "gcp-voice.json"
        write_service_account(path)

        def boom(self: Any, request: Any) -> None:
            raise RuntimeError("invalid_grant: Invalid JWT Signature for juniper-tts@x")

        monkeypatch.setattr(service_account.Credentials, "refresh", boom)
        with pytest.raises(VoiceError) as exc:
            ServiceAccountToken(path).token()
        assert exc.value.code == "api_error" and "juniper-tts" not in exc.value.message


# --- configuration -------------------------------------------------------------------------


def test_voice_is_off_by_default_with_the_documented_defaults() -> None:
    cfg = VoiceSettings()
    assert cfg.enabled is False and cfg.max_spoken_chars == 800 and cfg.idle_timeout_seconds == 60
    assert cfg.tts.voice == "pt-BR-Chirp3-HD-Leda" and cfg.tts.language == "pt-BR"
    assert cfg.tts.speaking_rate == 0.95 and cfg.tts.sample_rate == 24000
    assert cfg.tts.monthly_chars_limit == 1_250_000
    assert cfg.tts.credentials_file == Path.home() / ".juniper" / "certs" / "gcp-voice.json"
    assert cfg.stt.model == "whisper-large-v3-turbo" and cfg.stt.language == "pt"
    assert (cfg.vad.speech_start_ms, cfg.vad.silence_ms, cfg.vad.barge_in_ms) == (120, 700, 300)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_spoken_chars": 10},
        {"idle_timeout_seconds": 1},
        {"tts": {"speaking_rate": 5}},
        {"tts": {"sample_rate": 100}},
        {"tts": {"monthly_chars_limit": -1}},
        {"tts": {"provider": "outro"}},
        {"stt": {"cooldown_seconds": 1}},
        {"vad": {"silence_ms": 5}},
    ],
)
def test_voice_settings_reject_bad_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        VoiceSettings(**kwargs)


def test_the_credentials_path_expands_home() -> None:
    cfg = VoiceSettings(tts={"credentials_file": "~/x/voz.json"})  # type: ignore[arg-type]
    assert cfg.tts.credentials_file == Path.home() / "x" / "voz.json"
