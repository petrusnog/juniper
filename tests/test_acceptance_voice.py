"""Acceptance of the voice milestone (T-153, ADR-023): "toca Djavan" by voice, end to end.

The server, the chat handler, the voice session, the VAD, the dispatcher, the music tools and the
executor protocol are the real ones. The fakes are at the edges only: the LLM script, the Groq STT
(returns the words the test says were spoken), the Google TTS and the YouTube API. The audio is a
synthetic tone: a tone is speech, quiet noise is silence.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from brain.clients import protocol as p
from brain.memory.v2.store import InMemoryDocStore
from brain.music import base as music_base
from brain.music.taste import TasteBook
from brain.tools import music as music_tools
from brain.voice.budget import VoiceBudget
from brain.voice.services import VoiceServices
from brain.voice.speech import SpeechService
from brain.voice.stt import SpeechToText
from tests.test_acceptance_music import (
    OCEANO,
    PHONE_TOOL,
    PL,
    SINA,
    Youtube,
    register_music,
)
from tests.test_agent import FakeProvider, say
from tests.test_executors import Setup, use
from tests.test_voice_session import FakeStt, FakeSynth, quiet, stream_audio, tone


@pytest.fixture
def youtube() -> Iterator[Youtube]:
    fake = Youtube()
    music_tools.bind(fake)
    music_tools.bind_taste(TasteBook(InMemoryDocStore()))
    yield fake
    music_tools.bind(None)
    music_tools.bind_taste(None)


class VoiceSetup(Setup):
    def __init__(self, tmp_path: Path, provider: Any, *spoken: str) -> None:
        super().__init__(tmp_path, provider)
        self.stt = FakeStt(*spoken)
        self.synth = FakeSynth()
        budget = VoiceBudget(
            tmp_path / "usage.json", 100_000, clock=lambda: datetime(2026, 3, 10, tzinfo=UTC)
        )
        self.handler._voice = VoiceServices(
            speech=SpeechService(self.synth, budget),
            stt=SpeechToText(self.stt),
            max_spoken_chars=800,
            idle_timeout=60.0,
        )
        self.seq = 0

    async def phone(self) -> Any:
        """A phone: an executor of open_url that also opens the voice conversation."""
        client, _ = await self.executor("celular", PHONE_TOOL)
        await client.send(
            type="voice.start",
            id="v-1",
            format={"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
            local_stt=True,
            local_tts=True,
        )
        ready = (await client.until(p.VoiceReady))[-1]
        assert isinstance(ready, p.VoiceReady) and (ready.stt, ready.tts) == ("cloud", "cloud")
        return client

    async def speak(self, client: Any) -> None:
        """He says something (the words were already scripted in the fake STT)."""
        self.seq = await stream_audio(client, quiet(400), self.seq)
        self.seq = await stream_audio(client, tone(800), self.seq)
        self.seq = await stream_audio(client, quiet(900), self.seq)


async def until_spoken(client: Any, *, answer_open_url: str | None = None) -> list[p.ServerMessage]:
    """Everything until the end of the turn and of her speech; answers the phone's open_url."""
    seen: list[p.ServerMessage] = []
    ended = spoken = False
    while not (ended and spoken):
        message = await client.recv()
        seen.append(message)
        if isinstance(message, p.Invoke):
            assert answer_open_url is not None, f"unexpected invoke {message.tool_name}"
            assert message.tool_name == "open_url"
            assert message.arguments == {"url": answer_open_url}
            await client.send(type="result", call_id=message.call_id, success=True, output="Abri.")
        ended = ended or isinstance(message, p.ChatEnd)
        spoken = spoken or (isinstance(message, p.VoiceAudio) and bool(message.last))
    return seen


def said(messages: list[p.ServerMessage]) -> str:
    return "".join(m.text for m in messages if isinstance(m, p.ChatToken))


# --- play a song by voice ------------------------------------------------------------------


async def test_play_a_song_by_voice_the_phone_opens_the_link_and_she_answers_aloud(
    tmp_path: Path, youtube: Youtube
) -> None:
    link = music_base.track_url(OCEANO)
    reply = "Tocando Oceano, do Djavan, agora mesmo."
    provider = FakeProvider(
        [
            use("music_search", "k1", query="Djavan Oceano"),
            use("open_url", "k2", url=link),
            say(reply),
        ]
    )
    async with VoiceSetup(tmp_path, provider, "toca Oceano do Djavan") as s:
        register_music(s)
        phone = await s.phone()
        await s.speak(phone)
        seen = await until_spoken(phone, answer_open_url=link)
    transcript = next(m for m in seen if isinstance(m, p.VoiceTranscript))
    assert transcript.text == "toca Oceano do Djavan"
    assert said(seen) == reply  # the caption
    assert s.synth.spoken == [reply]  # and her voice
    assert youtube.searches == [("Djavan Oceano", 5, "track")]
    assert not [m for m in seen if isinstance(m, p.Error)]


# --- the playlist: one question, one answer ------------------------------------------------


async def test_a_playlist_by_voice_asks_once_and_the_spoken_yes_creates_it(
    tmp_path: Path, youtube: Youtube
) -> None:
    link = f"https://music.youtube.com/playlist?list={PL}"
    question = "Posso criar a playlist «Foco» com 2 músicas?"
    provider = FakeProvider(
        [
            use("music_search", "k1", query="Djavan"),
            use("music_playlist_create", "k2", title="Foco", video_ids=[OCEANO, SINA]),
            say(question),
            use("open_url", "k3", url=link),
            say("Criei e abri a playlist Foco, pode ouvir."),
        ]
    )
    async with VoiceSetup(tmp_path, provider, "monta uma playlist Foco do Djavan", "sim") as s:
        register_music(s)
        phone = await s.phone()
        await s.speak(phone)
        first = await until_spoken(phone)
        assert question in said(first) and s.synth.spoken == [question]
        assert youtube.created == []  # nothing happens before the spoken yes

        await s.speak(phone)
        second = await until_spoken(phone, answer_open_url=link)
        assert "Criei e abri a playlist Foco" in said(second)
    assert youtube.created == [("Foco", [OCEANO, SINA])]
    assert s.synth.spoken.count(question) == 1  # asked once, never repeated


async def test_a_spoken_no_creates_nothing(tmp_path: Path, youtube: Youtube) -> None:
    provider = FakeProvider(
        [
            use("music_playlist_create", "k1", title="Foco", video_ids=[OCEANO]),
            say("Posso criar a playlist «Foco» com 1 música?"),
            say("Tudo bem, não criei nenhuma playlist."),
        ]
    )
    async with VoiceSetup(tmp_path, provider, "cria a playlist Foco", "não") as s:
        register_music(s)
        phone = await s.phone()
        await s.speak(phone)
        await until_spoken(phone)
        await s.speak(phone)
        second = await until_spoken(phone)
        assert "não criei" in said(second)
    assert youtube.created == []


# --- ending ----------------------------------------------------------------------------------


async def test_the_spoken_end_command_closes_the_voice_but_not_the_connection(
    tmp_path: Path, youtube: Youtube
) -> None:
    provider = FakeProvider([say("Pronto, anotado para você.")])
    async with VoiceSetup(tmp_path, provider, "encerrar a conversa") as s:
        register_music(s)
        phone = await s.phone()
        await s.speak(phone)
        end = (await phone.until(p.VoiceEnd))[-1]
        assert isinstance(end, p.VoiceEnd) and end.reason == "command"
        assert provider.seen == []  # the LLM was never asked
        reply = await phone.chat("oi")  # the connection and the chat are still there
        assert said(reply) == "Pronto, anotado para você."
