"""The second check of the wake word, on the brain (T-210, ADR-034 section 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.clients import protocol as p
from brain.voice.wake import is_wake_word, strip_wake_word
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import connect_client
from tests.test_voice_arbitration import ask_to_open, outcome
from tests.test_voice_session import LONG, FakeStt, Rig, collect, of, say_phrase, wait_for

# --- the name ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word",
    ["Juniper", "juniper", "JUNIPER", "Júnior", "Junipe", "Djuniper", "Junipher", "Juniperr"],
)
def test_the_name_as_the_transcriber_writes_it(word: str) -> None:
    assert is_wake_word(word)


@pytest.mark.parametrize("word", ["que", "horas", "Janaína", "jupiter", "uniforme", "Juliana", ""])
def test_other_words_are_not_the_name(word: str) -> None:
    assert not is_wake_word(word)


def test_the_name_leading_the_phrase_is_taken_off() -> None:
    assert strip_wake_word("Juniper, qual a previsão do tempo para amanhã?") == (
        True,
        "qual a previsão do tempo para amanhã?",
    )
    assert strip_wake_word("juniper que horas são") == (True, "que horas são")


def test_the_name_after_a_greeting_word_is_taken_off_too() -> None:
    assert strip_wake_word("Oi, Juniper! Abre o projeto Media Works.") == (
        True,
        "Abre o projeto Media Works.",
    )


def test_the_name_said_later_confirms_but_leaves_the_phrase_alone() -> None:
    phrase = "abra o projeto Media Works, Juniper"
    assert strip_wake_word(phrase) == (True, phrase)


def test_only_the_name_leaves_nothing_to_ask() -> None:
    assert strip_wake_word("Juniper") == (True, "")
    assert strip_wake_word("Juniper?") == (True, "")


def test_a_phrase_without_the_name_is_not_confirmed() -> None:
    phrase = "que horas são agora"
    assert strip_wake_word(phrase) == (False, phrase)
    assert strip_wake_word("") == (False, "")


def test_the_phrase_keeps_its_own_words_and_case() -> None:
    assert strip_wake_word("Juniper, Toca DJAVAN")[1] == "Toca DJAVAN"


# --- a session opened by the spoken word -----------------------------------------------------


def quick(tmp_path: Path, **kw: object) -> Rig:
    return Rig(tmp_path, FakeProvider(kw.pop("replies", [])), **kw)  # type: ignore[arg-type]


async def test_the_command_that_came_with_the_word_is_answered_without_a_greeting(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([say(LONG)])
    async with Rig(tmp_path, provider, stt=FakeStt("Juniper, que horas são?")) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(phone, wake=True, wake_score=0.9)
        assert await outcome(phone) == "ready"
        await say_phrase(phone)
        messages = await collect(phone, p.VoiceTranscript, p.ChatEnd, p.VoiceAudio)
    asked = next(m.content for m in provider.seen[0] if m.role == "user")
    assert asked is not None and "que horas são?" in asked and "Juniper" not in asked
    assert of(messages, p.VoiceTranscript)[0].text == "Juniper, que horas são?"  # the caption
    assert rig.synth.spoken == [LONG]  # only the answer: no "Oi! Pode falar." before it


async def test_a_touch_or_launcher_session_still_greets_first(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([])) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(phone, wake=True)  # opened by the launcher: no score, no spoken word
        assert await outcome(phone) == "ready"
        await collect(phone, p.VoiceAudio)
    assert len(rig.synth.spoken) == 1  # the greeting


async def test_when_the_detector_was_wrong_the_session_closes_in_silence(tmp_path: Path) -> None:
    provider = FakeProvider([say(LONG)])
    async with Rig(tmp_path, provider, stt=FakeStt("a novela começa às oito")) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(phone, wake=True, wake_score=0.55)
        assert await outcome(phone) == "ready"
        await say_phrase(phone)
        end = (await phone.until(p.VoiceEnd))[-1]
        assert isinstance(end, p.VoiceEnd) and end.reason == "unverified"
        await wait_for(lambda: not rig.handler._voices)
    assert provider.seen == []  # nothing reached the model
    assert rig.synth.spoken == []  # and she said nothing


async def test_only_the_name_waits_for_the_request_and_later_phrases_need_no_name(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([say(LONG), say(LONG)])
    async with Rig(tmp_path, provider, stt=FakeStt("Juniper", "e na sexta?")) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(phone, wake=True, wake_score=0.9)
        assert await outcome(phone) == "ready"
        await say_phrase(phone)  # just "Juniper"
        await phone.until(p.VoiceTranscript)
        assert provider.seen == [] and rig.handler._voices  # no turn yet; still listening
        await say_phrase(phone)  # the request, with no name needed anymore
        await collect(phone, p.ChatEnd, p.VoiceAudio)
    asked = next(m.content for m in provider.seen[0] if m.role == "user")
    assert asked is not None and "e na sexta?" in asked


async def test_the_name_is_always_in_the_vocabulary_hint(tmp_path: Path) -> None:
    from brain.config import settings
    from brain.voice.services import open_voice_services

    services = open_voice_services()
    try:
        assert (await services.glossary.terms())[0] == "Juniper"
        assert settings.voice.stt.glossary == [] or "Juniper" not in settings.voice.stt.glossary[1:]
    finally:
        await services.aclose()
