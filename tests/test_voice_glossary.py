"""The vocabulary hint and the confidence of the transcription (T-205, ADR-034 section 4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from brain.voice.glossary import LABEL, Glossary
from brain.voice.stt import (
    GroqTranscriber,
    SpeechToText,
    Transcriber,
    Transcription,
    is_prompt_echo,
)

# --- the glossary ----------------------------------------------------------------------------


async def test_the_prompt_lists_the_terms_after_a_label() -> None:
    g = Glossary(["Media Works", "Petrus"])
    assert await g.prompt() == f"{LABEL}: Media Works, Petrus."


async def test_no_terms_means_no_prompt() -> None:
    assert await Glossary().prompt() == ""


async def test_repeats_are_dropped_ignoring_case_and_accents() -> None:
    g = Glossary(["Media Works", "media works", "Média  Works", "Fortaleza"])
    assert await g.terms() == ["Media Works", "Fortaleza"]


async def test_configured_terms_come_first_and_the_budget_cuts_the_rest() -> None:
    g = Glossary(["Alfa"], char_budget=len(LABEL) + 3 + 6 + 8)
    g.add_source(lambda: ["Beta", "Gamma", "Delta"])
    assert await g.terms() == ["Alfa", "Beta"]  # 6 + 6 + 8 left: "Gamma" does not fit
    assert len(await g.prompt()) <= len(LABEL) + 3 + 6 + 8 + 2


async def test_a_term_that_does_not_fit_does_not_block_a_shorter_one() -> None:
    g = Glossary(["Um nome muito comprido para o espaço", "Ana"], char_budget=len(LABEL) + 3 + 5)
    assert await g.terms() == ["Ana"]


async def test_sources_add_names_and_blank_or_huge_terms_are_ignored() -> None:
    g = Glossary()
    g.add_source(lambda: ["Petrus", "  ", "x" * 200, "Ana  Clara"])
    assert await g.terms() == ["Petrus", "Ana Clara"]


async def test_a_source_is_asked_once_per_ttl_not_once_per_phrase() -> None:
    now = [0.0]
    calls: list[int] = []
    g = Glossary(ttl=60.0, clock=lambda: now[0])
    g.add_source(lambda: calls.append(1) or ["Petrus"])
    for _ in range(3):
        await g.prompt()
    assert len(calls) == 1
    now[0] = 61.0
    await g.prompt()
    assert len(calls) == 2


async def test_a_failing_source_costs_only_its_own_terms() -> None:
    def broken() -> list[str]:
        raise RuntimeError("mongo caiu")

    g = Glossary(["Media Works"])
    g.add_source(broken)
    g.add_source(lambda: ["Petrus"])
    assert await g.terms() == ["Media Works", "Petrus"]


async def test_a_slow_source_never_holds_a_phrase_for_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import brain.voice.glossary as module

    monkeypatch.setattr(module, "FIRST_LOAD_SECONDS", 0.05)
    release = asyncio.Event()

    async def hang(_fn: object) -> list[str]:
        await release.wait()
        return []

    monkeypatch.setattr(module.asyncio, "to_thread", hang)
    g = Glossary(["Media Works"])
    g.add_source(lambda: ["Lento"])
    async with asyncio.timeout(1.0):
        assert await g.terms() == ["Media Works"]  # the configured ones still go


# --- the echo guard --------------------------------------------------------------------------


def test_the_hint_written_back_is_an_echo() -> None:
    prompt = f"{LABEL}: Media Works, Petrus, Fortaleza."
    assert is_prompt_echo("Vocabulário: Media Works, Petrus.", prompt)
    assert is_prompt_echo("Media Works Petrus Fortaleza Petrus", prompt)  # only hint words


def test_a_real_phrase_is_not_an_echo_even_with_a_term_in_it() -> None:
    prompt = f"{LABEL}: Media Works, Petrus, Fortaleza."
    assert not is_prompt_echo("abra o projeto Media Works", prompt)
    assert not is_prompt_echo("Vocabulário de inglês", prompt)  # starts like the label only
    assert not is_prompt_echo("Petrus", prompt)  # short: could be a real answer
    assert not is_prompt_echo("qualquer coisa", "")  # no hint, nothing to echo


# --- the Groq request and confidence ---------------------------------------------------------


def groq(
    body: dict, seen: list[httpx.Request] | None = None, *, contextual: bool = True
) -> GroqTranscriber:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=body)

    return GroqTranscriber(
        api_key="gsk_testtesttest", contextual=contextual, transport=httpx.MockTransport(handler)
    )


def form(request: httpx.Request) -> dict[str, str]:
    """The text fields of the multipart body, by name."""
    fields: dict[str, str] = {}
    for part in request.content.split(b"--")[1:]:
        head, _, value = part.partition(b"\r\n\r\n")
        if b'name="' in head and b"filename" not in head:
            name = head.split(b'name="')[1].split(b'"')[0].decode()
            fields[name] = value.rstrip(b"\r\n").decode()
    return fields


SEG = {"start": 0.0, "end": 2.0, "avg_logprob": -0.2, "no_speech_prob": 0.01}


async def test_the_request_asks_for_segments_and_carries_the_prompt() -> None:
    seen: list[httpx.Request] = []
    stt = groq({"text": "oi", "segments": [SEG]}, seen)
    await stt.transcribe_detailed(b"\x00\x00" * 160, 16_000, f"{LABEL}: Petrus.")
    fields = form(seen[0])
    assert fields["response_format"] == "verbose_json"
    assert fields["prompt"] == f"{LABEL}: Petrus."
    assert fields["language"] == "pt"


async def test_with_contextual_off_the_request_is_the_plain_one_even_if_a_hint_is_given() -> None:
    seen: list[httpx.Request] = []
    stt = groq({"text": "oi"}, seen, contextual=False)
    got = await stt.transcribe_detailed(b"\x00\x00" * 160, 16_000, f"{LABEL}: Petrus.")
    fields = form(seen[0])
    assert fields["response_format"] == "json" and "prompt" not in fields
    assert got.text == "oi" and got.confidence is None


def test_contextual_is_off_by_default() -> None:
    from brain.config import settings

    assert settings.voice.stt.contextual is False
    assert GroqTranscriber(api_key="gsk_x")._contextual is False


async def test_without_a_hint_no_prompt_field_is_sent() -> None:
    seen: list[httpx.Request] = []
    await groq({"text": "oi"}, seen).transcribe(b"\x00\x00" * 160, 16_000)
    assert "prompt" not in form(seen[0])


async def test_confidence_is_the_probability_of_the_words_weighted_by_length() -> None:
    body = {
        "text": "abra o projeto",
        "segments": [
            {"start": 0, "end": 3, "avg_logprob": -0.1, "no_speech_prob": 0.0},
            {"start": 3, "end": 4, "avg_logprob": -0.9, "no_speech_prob": 0.0},
        ],
    }
    got = await groq(body).transcribe_detailed(b"\x00\x00" * 160, 16_000)
    expected = pytest.approx(__import__("math").exp((3 * -0.1 + 1 * -0.9) / 4))
    assert got.text == "abra o projeto" and got.confidence == expected and not got.silent


async def test_unsure_words_over_noise_are_flagged_silent() -> None:
    noise = {"start": 0, "end": 2, "avg_logprob": -1.4, "no_speech_prob": 0.9}
    got = await groq({"text": "Obrigado.", "segments": [noise]}).transcribe_detailed(
        b"\x00\x00" * 160, 16_000
    )
    assert got.silent and got.confidence is None


async def test_one_noisy_tail_does_not_make_clear_speech_silent() -> None:
    body = {
        "text": "que horas são",
        "segments": [SEG, {"start": 2, "end": 3, "avg_logprob": -1.4, "no_speech_prob": 0.9}],
    }
    got = await groq(body).transcribe_detailed(b"\x00\x00" * 160, 16_000)
    assert not got.silent and got.confidence == pytest.approx(__import__("math").exp(-0.2))


@pytest.mark.parametrize(
    "segments",
    [None, [], "x", [1], [{"start": 0, "end": 1}], [{"avg_logprob": True}], {"a": 1}],
)
async def test_missing_or_odd_segments_mean_unknown_confidence_not_an_error(
    segments: object,
) -> None:
    body: dict = {"text": "oi"} if segments is None else {"text": "oi", "segments": segments}
    got = await groq(body).transcribe_detailed(b"\x00\x00" * 160, 16_000)
    assert got.text == "oi" and got.confidence is None and not got.silent


async def test_an_engine_that_knows_nothing_of_confidence_keeps_working() -> None:
    class Plain(Transcriber):
        async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
            return "oi"

    assert await Plain().transcribe_detailed(b"", 16_000, "dica") == Transcription("oi")


# --- through SpeechToText --------------------------------------------------------------------


class Scripted(Transcriber):
    def __init__(self, *answers: Transcription) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        raise NotImplementedError

    async def transcribe_detailed(
        self, pcm: bytes, sample_rate: int, prompt: str = ""
    ) -> Transcription:
        self.prompts.append(prompt)
        return self.answers.pop(0)


async def test_listen_passes_the_prompt_and_returns_the_confidence() -> None:
    engine = Scripted(Transcription("abra o projeto Media Works", confidence=0.83))
    heard = await SpeechToText(engine).listen(b"\x00\x00", 16_000, "Vocabulário: Media Works.")
    assert engine.prompts == ["Vocabulário: Media Works."]
    assert heard.text == "abra o projeto Media Works" and heard.confidence == 0.83


async def test_silent_text_and_echoes_are_dropped_like_hallucinations() -> None:
    prompt = f"{LABEL}: Media Works, Petrus."
    engine = Scripted(
        Transcription("Obrigado pela atenção", silent=True),
        Transcription("Vocabulário: Media Works, Petrus.", confidence=0.4),
    )
    stt = SpeechToText(engine)
    assert (await stt.listen(b"\x00\x00", 16_000, prompt)).text is None
    assert (await stt.listen(b"\x00\x00", 16_000, prompt)).text is None


async def test_the_log_carries_sizes_and_confidence_never_the_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from brain.voice import stt as module

    seen: list[dict] = []
    monkeypatch.setattr(module.event_log, "log", lambda event, **kw: seen.append(kw))
    engine = Scripted(Transcription("meu segredo íntimo", confidence=0.8312))
    await SpeechToText(engine).listen(b"\x00\x00", 16_000, "Vocabulário: Petrus.")
    assert seen == [
        {"action": "stt", "mode": "cloud", "chars": 18, "prompt_chars": 20, "confidence": 0.83}
    ]
    assert "segredo" not in json.dumps(seen)


# --- in a voice session ----------------------------------------------------------------------


async def test_a_voice_session_hands_the_vocabulary_to_the_transcriber(tmp_path: Path) -> None:
    from brain.clients import protocol as p
    from tests.test_agent import FakeProvider, say
    from tests.test_voice_session import LONG, Rig, collect, say_phrase

    class Listening(Transcriber):
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
            raise NotImplementedError

        async def transcribe_detailed(
            self, pcm: bytes, sample_rate: int, prompt: str = ""
        ) -> Transcription:
            self.prompts.append(prompt)
            return Transcription("abra o projeto Media Works", confidence=0.9)

    def broken() -> list[str]:
        raise RuntimeError("mongo caiu")

    engine = Listening()
    async with Rig(tmp_path, FakeProvider([say(LONG)])) as rig:
        rig.services.stt = SpeechToText(engine)
        rig.services.contextual = True
        rig.services.glossary = Glossary(["Media Works"])
        rig.services.glossary.add_source(broken)  # a dead source must not stop the phrase
        client, _ = await rig.open()
        await say_phrase(client)
        messages = await collect(client, p.VoiceTranscript, p.ChatEnd)
    assert engine.prompts == [f"{LABEL}: Media Works."]
    assert [m.text for m in messages if isinstance(m, p.VoiceTranscript)] == [
        "abra o projeto Media Works"
    ]


async def test_with_contextual_off_a_session_sends_no_hint_but_still_corrects(
    tmp_path: Path,
) -> None:
    from brain.clients import protocol as p
    from tests.test_agent import FakeProvider, say
    from tests.test_voice_session import LONG, Rig, collect, say_phrase

    class Listening(Transcriber):
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
            raise NotImplementedError

        async def transcribe_detailed(
            self, pcm: bytes, sample_rate: int, prompt: str = ""
        ) -> Transcription:
            self.prompts.append(prompt)
            return Transcription("abra o projeto mídia works")

    engine = Listening()
    async with Rig(tmp_path, FakeProvider([say(LONG)])) as rig:
        rig.services.stt = SpeechToText(engine)
        rig.services.glossary = Glossary(["Media Works"])  # contextual stays off (the default)
        client, _ = await rig.open()
        await say_phrase(client)
        messages = await collect(client, p.VoiceTranscript, p.ChatEnd)
    assert engine.prompts == [""]  # nothing extra asked of the provider
    transcript = next(m for m in messages if isinstance(m, p.VoiceTranscript))
    assert transcript.text == "abra o projeto Media Works"  # the glossary still fixes the sound
