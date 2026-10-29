"""The voice session on the server (T-152, ADR-023): a real server, handler and WebSocket client;
the edges (LLM, Google TTS, Groq STT) are fakes. Audio is synthetic: a tone is speech."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from brain.clients import protocol as p
from brain.clients.chat import ChatHandler
from brain.clients.server import BrainServer
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message, StreamChunk
from brain.memory.session import FileSessionStore
from brain.tools.open_url import OpenUrl
from brain.voice.budget import VoiceBudget
from brain.voice.errors import VoiceError
from brain.voice.services import VoiceServices
from brain.voice.speech import SpeechService
from brain.voice.stt import SpeechToText, Transcriber
from brain.voice.tts import Synthesizer
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import TOKEN, Client, connect_client, texts

RATE = 16_000
LONG = "Claro, Petrus, já resolvi isso para você agora mesmo."


def tone(ms: int, amplitude: int = 6000) -> bytes:
    t = np.arange(RATE * ms // 1000) / RATE
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype("<i2").tobytes()


def quiet(ms: int) -> bytes:
    rng = np.random.default_rng(3)
    return rng.integers(-40, 40, RATE * ms // 1000).astype("<i2").tobytes()


class FakeStt(Transcriber):
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.error: VoiceError | None = None
        self.heard: list[int] = []

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        self.heard.append(len(pcm))
        if self.error is not None:
            raise self.error
        return self.texts.pop(0) if self.texts else "ok então"


class FakeSynth(Synthesizer):
    def __init__(self) -> None:
        self.spoken: list[str] = []

    @property
    def sample_rate(self) -> int:
        return 24_000

    async def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        return b"\x05\x00" * 4800  # 200 ms: two chunks of 100 ms


class Gated(LLMProvider):
    """The first answer starts and never ends (until cancelled); the next ones are quick."""

    name: ClassVar[str] = "gated-voice"

    def __init__(self, *later: str) -> None:
        self.later = list(later)
        self.calls = 0

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(delta=f"{LONG} ")
            await asyncio.Event().wait()
        yield StreamChunk(delta=self.later.pop(0), finish_reason="stop")

    async def health_check(self) -> bool:
        return True


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        provider: LLMProvider,
        *,
        stt: FakeStt | None = None,
        synth: FakeSynth | None = None,
        cloud_stt: bool = True,
        limit: int = 10_000,
        idle: float = 60.0,
        wake_idle: float = 15.0,
        with_voice: bool = True,
    ) -> None:
        self.stt = stt or FakeStt()
        self.synth = synth or FakeSynth()
        budget = VoiceBudget(
            tmp_path / "usage.json", limit, clock=lambda: datetime(2026, 3, 10, tzinfo=UTC)
        )
        self.services = VoiceServices(
            speech=SpeechService(self.synth, budget),
            stt=SpeechToText(self.stt if cloud_stt else None, cooldown_seconds=3600),
            max_spoken_chars=800,
            idle_timeout=idle,
            wake_idle_timeout=wake_idle,
        )
        self.handler = ChatHandler(
            FileSessionStore(tmp_path / "sessions"),
            provider,
            voice=self.services if with_voice else None,
        )
        self.server = BrainServer("127.0.0.1", 0, TOKEN, self.handler)

    async def __aenter__(self) -> Rig:
        await self.server.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.server.stop()

    async def open(self, **flags: Any) -> tuple[Client, p.VoiceReady]:
        # Headphones by default: speaking over her cuts her off. The speaker tests say so.
        flags.setdefault("route", "headset")
        client = await connect_client(self.server, "celular")
        ready = await start(client, **flags)
        return client, ready


async def start(client: Client, **flags: Any) -> p.VoiceReady:
    await client.send(
        type="voice.start",
        id="v-1",
        format={"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1},
        **flags,
    )
    ready = (await client.until(p.VoiceReady))[-1]
    assert isinstance(ready, p.VoiceReady)
    return ready


async def stream_audio(client: Client, pcm: bytes, seq: int = 0) -> int:
    step = RATE * 2 // 10
    for i in range(0, len(pcm), step):
        data = base64.b64encode(pcm[i : i + step]).decode()
        await client.send(type="voice.audio", seq=seq, data=data)
        seq += 1
    return seq


async def say_phrase(client: Client, ms: int = 800) -> None:
    """Silence (to settle the noise floor), a phrase, and the pause that ends it."""
    seq = await stream_audio(client, quiet(400))
    seq = await stream_audio(client, tone(ms), seq)
    await stream_audio(client, quiet(900), seq)


def of(messages: list[p.ServerMessage], kind: type[Any]) -> list[Any]:
    return [m for m in messages if isinstance(m, kind)]


async def wait_for(condition: Any, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def collect(client: Client, *kinds: type[Any]) -> list[p.ServerMessage]:
    """Everything that arrives until a message of every one of ``kinds`` has been seen (a
    ``VoiceAudio`` counts only when it is the ``last`` one)."""
    seen: list[p.ServerMessage] = []
    pending = set(kinds)
    while pending:
        message = await client.recv()
        seen.append(message)
        for kind in list(pending):
            if isinstance(message, kind) and (kind is not p.VoiceAudio or message.last):
                pending.discard(kind)
    return seen


# --- opening -------------------------------------------------------------------------------


async def test_start_reports_the_formats_and_where_the_voices_come_from(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([])) as rig:
        client, ready = await rig.open()
    assert ready.reply_to == "v-1"
    assert (ready.input.sample_rate, ready.output.sample_rate) == (16_000, 24_000)
    assert (ready.stt, ready.tts) == ("cloud", "cloud")


async def test_voice_off_is_a_typed_error_and_the_chat_still_works(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([say("oi")]), with_voice=False) as rig:
        client = await connect_client(rig.server, "celular")
        await client.send(
            type="voice.start",
            id="v-1",
            format={"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1},
        )
        error = (await client.until(p.Error))[-1]
        assert isinstance(error, p.Error) and error.code == "voice_unavailable"
        assert texts(await client.chat("oi")) == "oi"


async def test_a_second_session_on_the_same_connection_is_busy(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([])) as rig:
        client, _ = await rig.open()
        await client.send(
            type="voice.start",
            id="v-2",
            format={"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1},
        )
        error = (await client.until(p.Error))[-1]
        assert isinstance(error, p.Error) and error.code == "voice_busy"


async def test_without_ears_it_is_unavailable_unless_the_device_transcribes(
    tmp_path: Path,
) -> None:
    async with Rig(tmp_path, FakeProvider([]), cloud_stt=False) as rig:
        client = await connect_client(rig.server, "celular")
        await client.send(
            type="voice.start",
            id="v-1",
            format={"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1},
        )
        error = (await client.until(p.Error))[-1]
        assert isinstance(error, p.Error) and error.code == "voice_unavailable"
        ready = await start(client, local_stt=True)
        assert ready.stt == "local"


async def test_audio_and_end_before_start_are_ignored_but_text_gets_an_answer(
    tmp_path: Path,
) -> None:
    async with Rig(tmp_path, FakeProvider([say("oi")])) as rig:
        client = await connect_client(rig.server, "celular")
        await client.send(type="voice.audio", seq=0, data="")
        await client.send(type="voice.end", reason="client")
        await client.send(type="voice.text", id="t-1", text="oi")
        error = (await client.until(p.Error))[-1]
        assert isinstance(error, p.Error) and error.code == "voice_unavailable"
        assert texts(await client.chat("oi")) == "oi"  # the connection is fine


# --- a spoken turn -------------------------------------------------------------------------


async def test_a_phrase_becomes_a_turn_and_her_answer_comes_back_as_audio(tmp_path: Path) -> None:
    provider = FakeProvider([say(LONG)])
    async with Rig(tmp_path, provider, stt=FakeStt("que horas são")) as rig:
        client, _ = await rig.open()
        await say_phrase(client)
        messages = await collect(client, p.VoiceTranscript, p.ChatEnd, p.VoiceAudio)
    assert of(messages, p.VoiceTranscript)[0].text == "que horas são"
    assert texts(messages) == LONG  # the caption is the usual chat stream
    audio = of(messages, p.VoiceAudio)
    turn_ids = {m.turn_id for m in audio}
    assert len(turn_ids) == 1 and None not in turn_ids
    assert [m.seq for m in audio] == list(range(len(audio)))
    assert audio[-1].last is True and audio[-1].data == ""
    assert sum(len(m.pcm()) for m in audio) == 9600  # one sentence, 200 ms at 24 kHz
    assert rig.synth.spoken == [LONG]
    asked = next(m.content for m in provider.seen[0] if m.role == "user")
    assert asked is not None and "que horas são" in asked


async def test_the_states_walk_listening_thinking_speaking_listening(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([say(LONG)]), stt=FakeStt("oi Juniper")) as rig:
        client, _ = await rig.open()
        assert (await client.recv()).state == "listening"  # type: ignore[union-attr]
        await say_phrase(client)
        seen: list[p.ServerMessage] = []
        while True:
            seen.append(await client.recv())
            states = [m.state for m in seen if isinstance(m, p.VoiceState)]
            if states[-2:] == ["speaking", "listening"]:
                break
    assert states[0] == "thinking" and states[-2:] == ["speaking", "listening"]


async def test_the_device_voice_takes_over_when_the_cloud_voice_is_gone(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([say(LONG)]), stt=FakeStt("oi"), limit=0) as rig:
        client, ready = await rig.open(local_tts=True)
        assert ready.tts == "local"
        await say_phrase(client)
        says = []
        while not says or says[-1].last is not True:
            message = await client.recv()
            if isinstance(message, p.VoiceSay):
                says.append(message)
    assert [s.text for s in says][0] == LONG and says[-1].text == ""
    assert not rig.synth.spoken


async def test_budget_running_out_mid_conversation_is_announced_once(tmp_path: Path) -> None:
    provider = FakeProvider([say(LONG)])
    async with Rig(tmp_path, provider, stt=FakeStt("oi"), limit=len(LONG) - 1) as rig:
        client, ready = await rig.open(local_tts=True)
        await say_phrase(client)
        fallbacks = []
        while True:
            message = await client.recv()
            if isinstance(message, p.VoiceFallback):
                fallbacks.append(message)
            if isinstance(message, p.VoiceSay) and message.last:
                break
    assert [(f.component, f.mode, f.reason) for f in fallbacks] == [("tts", "local", "budget")]


async def test_voice_text_from_the_device_is_a_turn_without_cloud_stt(tmp_path: Path) -> None:
    provider = FakeProvider([say(LONG)])
    async with Rig(tmp_path, provider, cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True)
        await client.send(type="voice.text", id="t-1", text="toca Djavan")
        end = await client.until(p.ChatEnd)
        assert texts(end) == LONG
    asked = next(m.content for m in provider.seen[0] if m.role == "user")
    assert asked is not None and "toca Djavan" in asked


async def test_an_llm_error_is_spoken_not_only_shown(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([]), cloud_stt=False) as rig:  # no script: it fails
        client, _ = await rig.open(local_stt=True)
        await client.send(type="voice.text", id="t-1", text="oi")
        messages = await client.until(p.Error)
        while not [m for m in messages if isinstance(m, p.VoiceAudio) and m.last]:
            messages.append(await client.recv())
    assert rig.synth.spoken  # the friendly message went through the voice


async def test_hallucinated_silence_never_becomes_a_turn(tmp_path: Path) -> None:
    provider = FakeProvider([])
    async with Rig(tmp_path, provider, stt=FakeStt("Legendas pela comunidade Amara.org")) as rig:
        client, _ = await rig.open()
        await say_phrase(client)
        await wait_for(lambda: rig.stt.heard)
        await asyncio.sleep(0.2)
    assert provider.seen == []


# --- commands, barge-in, idle --------------------------------------------------------------


async def test_the_spoken_end_command_ends_without_asking_the_llm(tmp_path: Path) -> None:
    provider = FakeProvider([])
    async with Rig(tmp_path, provider, stt=FakeStt("encerrar conversa")) as rig:
        client, _ = await rig.open()
        await say_phrase(client)
        end = (await client.until(p.VoiceEnd))[-1]
        assert isinstance(end, p.VoiceEnd) and end.reason == "command"
        await wait_for(lambda: not rig.handler._voices)
        # the connection and the chat survive
        provider.turns.append(say("oi"))
        assert texts(await client.chat("oi")) == "oi"
    assert len(provider.seen) == 1


async def test_the_end_command_from_the_device_stt_works_too(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([]), cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True)
        await client.send(type="voice.text", id="t-1", text="Tchau, Juniper!")
        end = (await client.until(p.VoiceEnd))[-1]
        assert isinstance(end, p.VoiceEnd) and end.reason == "command"


async def test_a_sentence_that_only_contains_the_words_is_not_the_command(tmp_path: Path) -> None:
    provider = FakeProvider([say(LONG)])
    async with Rig(tmp_path, provider, cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True)
        await client.send(type="voice.text", id="t-1", text="pode parar de tocar essa música")
        assert texts(await client.until(p.ChatEnd)) == LONG
    assert len(provider.seen) == 1


async def test_speaking_over_her_cuts_the_turn_and_the_new_phrase_gets_an_answer(
    tmp_path: Path,
) -> None:
    provider = Gated("Agora sim, resolvido para você.")
    async with Rig(tmp_path, provider, stt=FakeStt("primeira", "espera, outra coisa")) as rig:
        client, _ = await rig.open()
        await say_phrase(client)
        first = await client.until(p.VoiceAudio)  # she started to talk
        turn_id = first[-1].turn_id  # type: ignore[union-attr]
        # he speaks over her
        seq = await stream_audio(client, tone(900, 7000), 1000)
        await stream_audio(client, quiet(900), seq)
        messages = await client.until(p.ChatEnd)
        assert messages[-1].finish == "cancelled"  # type: ignore[union-attr]
        cut = [m for m in messages if isinstance(m, p.VoiceState) and m.turn_id == turn_id]
        assert cut and cut[-1].state == "listening"  # "drop that turn's audio"
        rest = await client.until(p.ChatEnd)
        assert "Agora sim" in texts(rest)
    assert rig.stt.texts == []  # both utterances were transcribed


async def test_the_stop_command_silences_her_and_keeps_the_session(tmp_path: Path) -> None:
    provider = Gated()
    async with Rig(tmp_path, provider, stt=FakeStt("primeira", "silêncio")) as rig:
        client, _ = await rig.open()
        await say_phrase(client)
        await client.until(p.VoiceAudio)
        seq = await stream_audio(client, tone(900, 7000), 1000)
        await stream_audio(client, quiet(900), seq)
        await client.until(p.ChatEnd)
        await wait_for(lambda: rig.stt.texts == [])
        await asyncio.sleep(0.2)
        assert rig.handler._voices  # still open
        assert provider.calls == 1  # "silêncio" never reached the LLM


async def test_the_client_can_interrupt_with_a_message(tmp_path: Path) -> None:
    provider = Gated()
    async with Rig(tmp_path, provider, cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True)
        await client.send(type="voice.text", id="t-1", text="fala bastante")
        await client.until(p.VoiceAudio)
        await client.send(type="voice.interrupt")
        end = await client.until(p.ChatEnd)
        assert end[-1].finish == "cancelled"  # type: ignore[union-attr]


async def test_long_silence_ends_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients import voice_session

    original = voice_session.VoiceSession.__init__

    def fast(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["tick"] = 0.05
        original(self, *args, **kwargs)

    monkeypatch.setattr(voice_session.VoiceSession, "__init__", fast)
    async with Rig(tmp_path, FakeProvider([]), idle=0.3) as rig:
        client, _ = await rig.open()
        end = (await client.until(p.VoiceEnd))[-1]
        assert isinstance(end, p.VoiceEnd) and end.reason == "silence"
        await wait_for(lambda: not rig.handler._voices)


async def test_the_client_ending_frees_the_connection_for_a_new_session(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([])) as rig:
        client, _ = await rig.open()
        await client.send(type="voice.end", reason="client")
        await wait_for(lambda: not rig.handler._voices)
        assert (await start(client)).stt == "cloud"


async def test_a_dropped_connection_ends_its_session(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([])) as rig:
        client, _ = await rig.open()
        assert rig.handler._voices
        await client.ws.close()
        await wait_for(lambda: not rig.handler._voices)


# --- the cloud STT failing -----------------------------------------------------------------


async def test_when_the_cloud_stt_fails_she_says_so_and_hands_over_to_the_device(
    tmp_path: Path,
) -> None:
    stt = FakeStt()
    stt.error = VoiceError("quota", "cota", retry_after=600)
    provider = FakeProvider([])
    async with Rig(tmp_path, provider, stt=stt) as rig:
        client, _ = await rig.open(local_stt=True)
        await say_phrase(client)
        messages = await client.until(p.VoiceFallback)
        fallback = messages[-1]
        assert isinstance(fallback, p.VoiceFallback)
        assert (fallback.component, fallback.mode, fallback.reason) == ("stt", "local", "quota")
        while not [m for m in messages if isinstance(m, p.VoiceAudio) and m.last]:
            messages.append(await client.recv())
    assert rig.synth.spoken == ["Não consegui te ouvir agora. Pode repetir?"]
    assert provider.seen == []  # the lost utterance never became a turn


# --- tokens and secrets --------------------------------------------------------------------


async def test_logs_never_carry_what_was_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients import voice_session

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(voice_session.event_log, "log", lambda event, **kw: seen.append(kw))
    async with Rig(tmp_path, FakeProvider([say(LONG)]), stt=FakeStt("meu segredo íntimo")) as rig:
        client, _ = await rig.open()
        await say_phrase(client)
        await client.until(p.ChatEnd)
        await client.send(type="voice.end", reason="client")
        await wait_for(lambda: not rig.handler._voices)
    assert seen and "segredo" not in str(seen) and "Petrus" not in str(seen)


# --- findings of the independent review (T-153) --------------------------------------------


async def test_stop_and_end_commands_drop_a_pending_confirmation(tmp_path: Path) -> None:
    """ "chega" after a question is a no: a later "sim" must not execute the old action."""
    cleared: list[str] = []
    async with Rig(tmp_path, FakeProvider([say(LONG), say(LONG)]), cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True)
        conversation = next(iter(rig.handler._live.values()))
        original = conversation.agent.clear_pending
        conversation.agent.clear_pending = lambda: (cleared.append("x"), original())[1]  # type: ignore[method-assign]
        await client.send(type="voice.text", id="t-1", text="oi")
        await collect(client, p.ChatEnd, p.VoiceAudio)
        before = len(cleared)
        await client.send(type="voice.text", id="t-2", text="chega")
        await asyncio.sleep(0.2)
        assert len(cleared) == before + 1
        await client.send(type="voice.text", id="t-3", text="tchau Juniper")
        await client.until(p.VoiceEnd)
        assert len(cleared) == before + 2


async def test_ending_by_voice_during_a_turn_cancels_it(tmp_path: Path) -> None:
    async with Rig(tmp_path, Gated(), cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True)
        await client.send(type="voice.text", id="t-1", text="fala bastante")
        await client.until(p.ChatStarted)
        await client.send(type="voice.text", id="t-2", text="tchau Juniper")
        messages = await collect(client, p.VoiceEnd, p.ChatEnd)
        assert of(messages, p.ChatEnd)[0].finish == "cancelled"


async def test_ending_while_a_turn_is_being_opened_does_not_leave_the_chat_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients import voice_session

    gate = asyncio.Event()

    async def stuck(self: Any, turn_id: str) -> None:
        gate.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(voice_session.VoiceSession, "turn_started", stuck)
    async with Rig(tmp_path, FakeProvider([say("oi")]), stt=FakeStt("oi")) as rig:
        client, _ = await rig.open()
        await say_phrase(client)
        await asyncio.wait_for(gate.wait(), 3)
        await client.send(type="voice.end", reason="client")
        await wait_for(lambda: not rig.handler._voices)
        conversation = next(iter(rig.handler._live.values()))
        assert not conversation.busy
        assert texts(await client.chat("e agora?")) == "oi"


async def test_a_broken_connection_still_cleans_the_session_up(tmp_path: Path) -> None:
    from brain.clients.voice_session import VoiceSession

    rig = Rig(tmp_path, FakeProvider([]))
    ended: list[bool] = []

    async def dead(message: Any) -> bool:
        return False

    async def nothing() -> None:
        return None

    session = VoiceSession(
        rig.services,
        send=dead,
        submit=lambda text: nothing(),
        cancel_turn=nothing,
        turn_active=lambda: False,
        ended=lambda: ended.append(True),
        clear_pending=lambda: None,
    )
    await session.start("v-1")
    worker, watchdog = session._worker, session._watchdog
    await session.end("server", notify=False)
    await asyncio.sleep(0)
    assert worker is not None and watchdog is not None
    assert worker.done() and watchdog.done() and ended == [True]


async def test_without_a_device_voice_the_text_is_not_sent_as_voice_say(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([say(LONG)]), cloud_stt=False, limit=0) as rig:
        client, ready = await rig.open(local_stt=True)  # no local_tts
        assert ready.tts == "local"
        await client.send(type="voice.text", id="t-1", text="oi")
        messages = await collect(client, p.ChatEnd)
        await asyncio.sleep(0.3)
        while True:
            try:
                async with asyncio.timeout(0.2):
                    messages.append(await client.recv())
            except TimeoutError:
                break
    assert not of(messages, p.VoiceSay)
    assert texts(messages) == LONG  # the caption is still there


async def test_odd_sized_audio_at_another_rate_is_not_an_error(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([say("oi")])) as rig:
        client = await connect_client(rig.server, "celular")
        await client.send(
            type="voice.start",
            id="v-1",
            format={"encoding": "pcm_s16le", "sample_rate": 44_100, "channels": 1},
        )
        await client.until(p.VoiceReady)
        odd = base64.b64encode(b"\x01\x02\x03").decode()
        for seq in range(3):
            await client.send(type="voice.audio", seq=seq, data=odd)
        await client.send(type="voice.audio", seq=3, data=base64.b64encode(b"\0\0" * 441).decode())
        assert rig.handler._voices  # still open, and no `error` came back
        messages = await client.chat("oi")  # the chat still works: nothing was broken
        assert texts(messages) == "oi" and not of(messages, p.Error)


async def test_the_model_is_told_the_turn_came_by_voice(tmp_path: Path) -> None:
    from brain.clients.executors import ExecutorHub

    provider = FakeProvider([say(LONG)])
    async with Rig(tmp_path, provider, cloud_stt=False) as rig:
        # The phone: a mobile client that registered open_url (so the app can open links).
        client = await connect_client(rig.server, "celular", "mobile")
        await client.send(
            type="register",
            tools=[
                {
                    "name": "open_url",
                    "description": "Abre um link no celular.",
                    "parameters": OpenUrl.parameters,
                    "tier": "write",
                }
            ],
        )
        await client.until(p.Registered)
        await start(client, local_stt=True)
        await client.send(type="voice.text", id="t-1", text="toca Djavan")
        await client.until(p.ChatEnd)
        assert isinstance(rig.handler.executors, ExecutorHub)
    note = next(
        m.content or ""
        for m in provider.seen[0]
        if m.role == "system" and "Canal:" in (m.content or "")
    )
    assert "aplicativo do celular (voz)" in note


# --- speakerphone: half-duplex (ignore the microphone while she speaks) ----------------------


async def test_on_the_speaker_her_own_echo_never_cuts_her_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients import voice_session

    monkeypatch.setattr(voice_session, "ECHO_TAIL_SECONDS", 0.2)
    provider = Gated("Resposta nova depois do eco.")
    async with Rig(tmp_path, provider, stt=FakeStt("primeira", "de novo")) as rig:
        client, _ = await rig.open(route="speaker")
        await say_phrase(client)
        await client.until(p.VoiceAudio)  # she is talking
        # her voice coming back into the microphone: loud and long, like speech
        seq = await stream_audio(client, tone(900, 7000), 1000)
        await stream_audio(client, quiet(900), seq)
        await asyncio.sleep(0.2)
        assert provider.calls == 1  # no barge-in: the turn was not cut
        assert rig.stt.texts == ["de novo"]  # and the echo was never transcribed


async def test_on_the_speaker_he_can_still_cut_her_with_the_button(tmp_path: Path) -> None:
    provider = Gated()
    async with Rig(tmp_path, provider, cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True, route="speaker")
        await client.send(type="voice.text", id="t-1", text="fala bastante")
        await client.until(p.VoiceAudio)
        await client.send(type="voice.interrupt")
        end = await client.until(p.ChatEnd)
        assert end[-1].finish == "cancelled"  # type: ignore[union-attr]


async def test_on_the_speaker_he_is_heard_again_once_she_has_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients import voice_session

    monkeypatch.setattr(voice_session, "ECHO_TAIL_SECONDS", 0.2)
    provider = FakeProvider([say(LONG), say("Segunda resposta tranquila, sem pressa.")])
    async with Rig(tmp_path, provider, stt=FakeStt("primeira", "segunda")) as rig:
        client, _ = await rig.open(route="speaker")
        await say_phrase(client)
        await collect(client, p.ChatEnd, p.VoiceAudio)
        await asyncio.sleep(0.8)  # her audio (0.2 s) and the tail are over
        await say_phrase(client)
        messages = await collect(client, p.ChatEnd)
        assert "Segunda resposta" in texts(messages)


async def test_a_route_change_in_the_middle_switches_the_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = Gated("Depois do fone.")
    async with Rig(tmp_path, provider, stt=FakeStt("primeira", "por cima")) as rig:
        client, _ = await rig.open(route="speaker")
        await say_phrase(client)
        await client.until(p.VoiceAudio)
        await client.send(type="voice.route", route="headset")  # headphones plugged in
        seq = await stream_audio(client, tone(900, 7000), 1000)
        await stream_audio(client, quiet(900), seq)
        end = await client.until(p.ChatEnd)  # with headphones, barge-in works again
        assert end[-1].finish == "cancelled"  # type: ignore[union-attr]


# --- wake word sessions and the always-closed microphone while she speaks ---------------------


async def test_a_wake_session_greets_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.clients import voice_session

    monkeypatch.setattr(voice_session, "GREETINGS", ("Oi! Pode falar.",))
    monkeypatch.setattr(voice_session.random, "choice", lambda options: options[0])
    async with Rig(tmp_path, FakeProvider([]), cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True, wake=True, route="speaker")
        messages = await collect(client, p.VoiceAudio)
    assert rig.synth.spoken == ["Oi! Pode falar."]
    states = [m.state for m in of(messages, p.VoiceState)]
    assert "speaking" in states  # she talks first; on a speaker the microphone is closed then


async def test_a_session_that_was_not_woken_does_not_greet(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([]), cloud_stt=False) as rig:
        client, _ = await rig.open(local_stt=True)
        await asyncio.sleep(0.3)
    assert rig.synth.spoken == []


async def test_a_wake_session_with_no_request_closes_soon_and_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients import voice_session

    original = voice_session.VoiceSession.__init__

    def fast(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["tick"] = 0.05
        original(self, *args, **kwargs)

    monkeypatch.setattr(voice_session.VoiceSession, "__init__", fast)
    async with Rig(tmp_path, FakeProvider([]), idle=60.0, wake_idle=0.4) as rig:
        client, _ = await rig.open(wake=True)
        end = (await client.until(p.VoiceEnd))[-1]
        assert isinstance(end, p.VoiceEnd) and end.reason == "silence"
        await wait_for(lambda: not rig.handler._voices)


async def test_by_default_the_microphone_is_closed_while_she_speaks_on_any_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No route reported (an old app): the safe behaviour is the half-duplex one."""
    from brain.clients import voice_session

    monkeypatch.setattr(voice_session, "ECHO_TAIL_SECONDS", 0.2)
    provider = Gated("não deveria ser usada")
    async with Rig(tmp_path, provider, stt=FakeStt("primeira", "eco")) as rig:
        client = await connect_client(rig.server, "celular")
        await client.send(
            type="voice.start",
            id="v-1",
            format={"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1},
        )  # no "route" at all
        await client.until(p.VoiceReady)
        await say_phrase(client)
        await client.until(p.VoiceAudio)
        seq = await stream_audio(client, tone(900, 7000), 1000)
        await stream_audio(client, quiet(900), seq)
        await asyncio.sleep(0.2)
        assert provider.calls == 1 and rig.stt.texts == ["eco"]  # the echo was never heard
