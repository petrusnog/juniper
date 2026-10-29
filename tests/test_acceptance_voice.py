"""Acceptance of the voice milestone (T-153, ADR-023): "toca Djavan" by voice, end to end.

The server, the chat handler, the voice session, the VAD, the dispatcher, the music tools and the
executor protocol are the real ones. The fakes are at the edges only: the LLM script, the Groq STT
(returns the words the test says were spoken), the Google TTS and the YouTube API. The audio is a
synthetic tone: a tone is speech, quiet noise is silence.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from brain.clients import protocol as p
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message, StreamChunk
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

    async def phone(self, **flags: Any) -> Any:
        """A phone: an executor of open_url that also opens the voice conversation."""
        client, _ = await self.executor("celular", PHONE_TOOL)
        await client.send(
            type="voice.start",
            id="v-1",
            format={"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
            local_stt=True,
            local_tts=True,
            **{"route": "headset", **flags},  # tests talk right after she stops (speaker: ~1 s)
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


async def test_a_stop_command_after_the_question_kills_the_pending_action(
    tmp_path: Path, youtube: Youtube
) -> None:
    """Review finding: "chega" after "Posso criar...?" must not leave the question armed, or a
    later "sim" (about anything) would execute it."""
    provider = FakeProvider(
        [
            use("music_playlist_create", "k1", title="Foco", video_ids=[OCEANO]),
            say("Posso criar a playlist «Foco» com 1 música?"),
            say("Não tenho nenhuma ação esperando por você."),
        ]
    )
    async with VoiceSetup(tmp_path, provider, "cria a playlist Foco", "chega", "sim") as s:
        register_music(s)
        phone = await s.phone()
        await s.speak(phone)
        await until_spoken(phone)
        await s.speak(phone)  # "chega"
        await phone.until(p.VoiceState)
        await s.speak(phone)  # "sim"
        second = await until_spoken(phone)
        assert "nenhuma ação" in said(second)
    assert youtube.created == []


# --- the wake word flow (T-165): greeting, one request, the comment, and back to standby ------


async def test_the_wake_flow_greets_plays_comments_and_ends_the_session(
    tmp_path: Path, youtube: Youtube
) -> None:
    from brain.clients.voice_session import GREETINGS

    link = music_base.track_url(OCEANO)
    provider = FakeProvider(
        [
            use("play_music", "k1", query="Djavan Oceano"),
            say("Oceano, do Djavan. Um clássico para a estrada."),
        ]
    )
    async with VoiceSetup(tmp_path, provider, "toca Oceano do Djavan") as s:
        register_music(s)
        s.registry.register(music_tools.PlayMusic)
        phone = await s.phone(wake=True)
        greeting = await collect_greeting(phone)
        assert s.synth.spoken[0] in GREETINGS  # she greets first, as before
        await s.speak(phone)
        seen = await until_spoken(phone, answer_open_url=link)
        end = (await phone.until(p.VoiceEnd))[-1]
    assert greeting and isinstance(end, p.VoiceEnd) and end.reason == "playback"
    assert "clássico" in said(seen) and s.synth.spoken[-1].startswith("Oceano")
    assert youtube.searches == [("Djavan Oceano", 5, "track")]


async def collect_greeting(client: Any) -> list[p.ServerMessage]:
    """Everything up to the end of her greeting (the first complete audio)."""
    seen: list[p.ServerMessage] = []
    while True:
        message = await client.recv()
        seen.append(message)
        if isinstance(message, p.VoiceAudio) and message.last:
            return seen


async def test_an_ordinary_request_keeps_the_conversation_open(
    tmp_path: Path, youtube: Youtube
) -> None:
    provider = FakeProvider([say("São três e meia da tarde, passando rápido.")])
    async with VoiceSetup(tmp_path, provider, "que horas são") as s:
        register_music(s)
        phone = await s.phone(wake=True)
        await collect_greeting(phone)
        await s.speak(phone)
        await until_spoken(phone)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.8):
                while True:
                    message = await phone.recv()
                    assert not isinstance(message, p.VoiceEnd)  # no music: still open


# --- what he says is never lost (T-166) -----------------------------------------------------


class StuckThenAnswers(LLMProvider):
    """The first call starts answering and never ends (he talks over it); the next ones answer."""

    name: ClassVar[str] = "stuck"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.first_started = asyncio.Event()

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(delta="Deixa eu ver ")
            self.first_started.set()
            await asyncio.Event().wait()  # until the turn is cancelled
        yield StreamChunk(delta=self.reply, finish_reason="stop")

    async def health_check(self) -> bool:
        return True


async def test_speaking_over_her_keeps_what_he_said_in_the_conversation_and_the_transcript(
    tmp_path: Path, youtube: Youtube
) -> None:
    provider = StuckThenAnswers("Anotado, Petrus.")
    sunk: list[Any] = []
    async with VoiceSetup(tmp_path, provider, "primeira parte do pedido", "e a segunda parte") as s:
        s.handler._turn_sink = lambda session_id, turn: sunk.append((session_id, turn))
        phone = await s.phone()
        await s.speak(phone)
        await asyncio.wait_for(provider.first_started.wait(), 3)
        await s.speak(phone)  # he talks over the turn in progress
        await until_spoken(phone)
        conversation = s.handler._live[phone.welcome.session.id]
        history = conversation.agent.session.history
    users = [m.content or "" for m in history if m.role == "user"]
    assert len(users) == 2
    assert "primeira parte do pedido" in users[0] and "e a segunda parte" in users[1]
    assert [m.content for m in history if m.role == "assistant"] == ["Anotado, Petrus."]
    # the transcript got both: the cut one without a reply, the next one with it
    assert [(t.user, t.assistant) for _, t in sunk] == [
        ("primeira parte do pedido", ""),
        ("e a segunda parte", "Anotado, Petrus."),
    ]
