"""Several devices of one person hear one wake word: one answers; a follow-up needs no new wake
word (T-212, ADR-034 section 5). A real server; the devices are WebSocket clients."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from brain.clients import protocol as p
from brain.voice.arbiter import WakeArbiter
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import Client, connect_client
from tests.test_voice_session import (
    LONG,
    RATE,
    FakeStt,
    Rig,
    collect,
    of,
    say_phrase,
    wait_for,
)


async def ask_to_open(client: Client, **flags: Any) -> None:
    await client.send(
        type="voice.start",
        id="v-1",
        format={"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1},
        route="headset",
        **flags,
    )


async def outcome(client: Client) -> str:
    """ "ready" when the session opened, or the reason it was refused."""
    while True:
        message = await client.recv()
        if isinstance(message, p.VoiceReady):
            return "ready"
        if isinstance(message, p.VoiceEnd):
            return message.reason
        if isinstance(message, p.Error):
            return f"error:{message.code}"


def quick_rig(tmp_path: Path, **kw: Any) -> Rig:
    rig = Rig(tmp_path, FakeProvider(kw.pop("replies", [])), **kw)
    rig.services.arbiter = WakeArbiter(0.15)
    return rig


async def test_the_device_that_heard_better_answers_and_the_other_goes_back_to_waiting(
    tmp_path: Path,
) -> None:
    async with quick_rig(tmp_path) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        laptop = await connect_client(rig.server, "notebook", "desktop")
        await ask_to_open(laptop, wake=True, wake_score=0.55)
        await ask_to_open(phone, wake=True, wake_score=0.93)
        got = await asyncio.gather(outcome(phone), outcome(laptop))
        assert got == ["ready", "elsewhere"]
        await asyncio.sleep(0.3)
        assert len(rig.handler._voices) == 1  # one session for two devices
    assert rig.synth.spoken == []  # opened by the word: nobody greets, the command is coming


async def test_a_wake_word_on_another_device_does_not_take_an_open_session_over(
    tmp_path: Path,
) -> None:
    async with quick_rig(tmp_path) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        laptop = await connect_client(rig.server, "notebook", "desktop")
        await ask_to_open(phone, wake=True, wake_score=0.5)
        assert await outcome(phone) == "ready"
        await ask_to_open(laptop, wake=True, wake_score=0.99)  # louder, but too late
        assert await outcome(laptop) == "elsewhere"
        assert len(rig.handler._voices) == 1


async def test_a_touch_always_wins_and_closes_the_wake_session_of_another_device(
    tmp_path: Path,
) -> None:
    async with quick_rig(tmp_path) as rig:
        laptop = await connect_client(rig.server, "notebook", "desktop")
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(laptop, wake=True, wake_score=0.9)
        assert await outcome(laptop) == "ready"
        await ask_to_open(phone)  # tapped, no wake word
        assert await outcome(phone) == "ready"
        end = (await laptop.until(p.VoiceEnd))[-1]
        assert isinstance(end, p.VoiceEnd) and end.reason == "elsewhere"
        await wait_for(lambda: len(rig.handler._voices) == 1)


async def test_once_the_session_ends_the_next_wake_word_opens_again(tmp_path: Path) -> None:
    async with quick_rig(tmp_path) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(phone, wake=True, wake_score=0.5)
        assert await outcome(phone) == "ready"
        await phone.send(type="voice.end", reason="client")
        await wait_for(lambda: not rig.handler._voices)
        await ask_to_open(phone, wake=True, wake_score=0.5)
        assert await outcome(phone) == "ready"


async def test_a_second_start_while_the_first_is_deciding_is_busy_not_a_second_vote(
    tmp_path: Path,
) -> None:
    async with quick_rig(tmp_path) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(phone, wake=True, wake_score=0.5)
        await ask_to_open(phone, wake=True, wake_score=0.9)
        results = {await outcome(phone), await outcome(phone)}
        assert results == {"ready", "error:voice_busy"}


def test_the_score_is_a_probability() -> None:
    fmt = p.VoiceFormat(sample_rate=16_000)
    assert p.VoiceStart(id="a", format=fmt, wake=True, wake_score=0.5).wake_score == 0.5
    assert p.VoiceStart(id="a", format=fmt).wake_score is None
    for bad in (-0.1, 1.5):
        with pytest.raises(ValidationError):
            p.VoiceStart(id="a", format=fmt, wake=True, wake_score=bad)


# --- following up without the wake word ------------------------------------------------------


async def test_after_her_answer_a_follow_up_needs_no_new_wake_word(tmp_path: Path) -> None:
    replies = [say("Amanhã teremos sol, com máxima de 29 graus."), say("Na sexta pode chover.")]
    async with quick_rig(
        tmp_path, replies=replies, stt=FakeStt("Juniper, previsão de amanhã", "e na sexta?")
    ) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(phone, wake=True, wake_score=0.9)
        assert await outcome(phone) == "ready"
        await say_phrase(phone)
        first = await collect(phone, p.VoiceTranscript, p.ChatEnd, p.VoiceAudio)
        await say_phrase(phone)  # the follow-up: same session, no voice.start
        second = await collect(phone, p.VoiceTranscript, p.ChatEnd, p.VoiceAudio)
        assert len(rig.handler._voices) == 1  # still the one session
    assert of(first, p.VoiceTranscript)[0].text == "Juniper, previsão de amanhã"
    assert of(second, p.VoiceTranscript)[0].text == "e na sexta?"


async def test_the_follow_up_window_is_the_configured_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients import voice_session

    original = voice_session.VoiceSession.__init__

    def fast(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["tick"] = 0.05
        original(self, *args, **kwargs)

    monkeypatch.setattr(voice_session.VoiceSession, "__init__", fast)
    async with quick_rig(
        tmp_path, replies=[say(LONG)], stt=FakeStt("Juniper, que horas são"), wake_idle=0.6
    ) as rig:
        phone = await connect_client(rig.server, "celular", "mobile")
        await ask_to_open(phone, wake=True, wake_score=0.9)
        assert await outcome(phone) == "ready"
        await say_phrase(phone)
        await collect(phone, p.ChatEnd, p.VoiceAudio)
        end = (await phone.until(p.VoiceEnd))[-1]  # nobody follows up: back to waiting
        assert isinstance(end, p.VoiceEnd) and end.reason == "silence"
