"""The voice session of one connection (ADR-023): the microphone in, her voice out.

It wraps the chat, it does not replace it. Each utterance becomes text (the cloud STT, or the
device's through ``voice.text``) and then a normal chat turn; the tokens of that turn are also cut
into sentences, each one spoken by the cloud voice or, if that is unavailable, handed to the
device (``voice.say``). The agent, the confirmation (ADR-010) and the memory see plain text: the
voice is a layer around them.

What lives here and nowhere else:

- speech detection and **barge-in** (speaking over her cuts her off and the turn is cancelled);
- the **spoken commands** that end the session or silence her, matched without the LLM;
- the **idle timeout** (``voice.idle_timeout_seconds`` without anyone talking ends the session);
- the notices of ``voice.fallback`` and the sentence "pode repetir" when the cloud STT fails.

Every failure is a typed message or a sentence she says; nothing here raises into the connection.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final, Literal

from brain.clients import protocol as p
from brain.eventlog import event_log
from brain.voice.commands import is_end_command, is_stop_command
from brain.voice.services import VoiceServices
from brain.voice.speech import Notice
from brain.voice.text import SentenceChunker
from brain.voice.tts import resample
from brain.voice.vad import SAMPLE_RATE as VAD_RATE
from brain.voice.vad import Vad

AUDIO_CHUNK_MS: Final = 100
SAY_SECONDS_PER_CHAR: Final = 0.07  # how long the device takes to say a character (an estimate)
CANT_HEAR_MESSAGE = "Não consegui te ouvir agora. Pode repetir?"
MAX_UTTERANCES_QUEUED: Final = 4
SETTLE_SECONDS: Final = 0.4  # a pause in the reply this long lets a finished sentence be spoken

Send = Callable[[p.ServerMessage], Awaitable[bool]]


@dataclass
class _Speech:
    """What she is saying now: one turn (``chat``) or one standalone sentence."""

    turn_id: str
    chat: bool
    chunker: SentenceChunker | None
    queue: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    spoke: bool = False


class VoiceSession:
    def __init__(
        self,
        services: VoiceServices,
        *,
        send: Send,
        submit: Callable[[str], Awaitable[None]],
        cancel_turn: Callable[[], Awaitable[None]],
        turn_active: Callable[[], bool],
        ended: Callable[[], None],
        input_rate: int = VAD_RATE,
        local_stt: bool = False,
        local_tts: bool = False,
        vad: Vad | None = None,
        clock: Callable[[], float] = time.monotonic,
        idle_timeout: float | None = None,
        tick: float = 1.0,
    ) -> None:
        self._services = services
        self._send = send
        self._submit = submit
        self._cancel_turn = cancel_turn
        self._turn_active = turn_active
        self._ended = ended
        self._input_rate = input_rate
        self.local_stt = local_stt
        self.local_tts = local_tts
        self._vad = vad or Vad()
        self._clock = clock
        self._idle = idle_timeout if idle_timeout is not None else services.idle_timeout
        self._tick = tick
        self._last_activity = clock()
        self._play_until = 0.0
        self._speech: _Speech | None = None
        self._utterances: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_UTTERANCES_QUEUED)
        self._worker: asyncio.Task[None] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._open = False
        self._out_rate = services.speech.sample_rate or 24_000
        self._settle: asyncio.TimerHandle | None = None

    @property
    def open(self) -> bool:
        return self._open

    # --- opening and closing ---

    async def start(self, reply_to: str) -> None:
        self._open = True
        self._worker = asyncio.get_running_loop().create_task(self._listen_loop())
        self._watchdog = asyncio.get_running_loop().create_task(self._watch())
        stt_cloud = self._services.stt.cloud
        tts_cloud = self._services.speech.mode == "cloud"
        await self._send(
            p.VoiceReady(
                reply_to=reply_to,
                input=p.VoiceFormat(sample_rate=self._input_rate),
                output=p.VoiceFormat(sample_rate=self._out_rate),
                stt="cloud" if stt_cloud else "local",
                tts="cloud" if tts_cloud else "local",
            )
        )
        await self._state("listening")
        event_log.log("voice", action="start", stt=stt_cloud, tts=tts_cloud)

    async def end(self, reason: p.VoiceEndReason, *, notify: bool = True) -> None:
        """Close the session (idempotent). ``notify`` tells the client why."""
        if not self._open:
            return
        self._open = False
        current = asyncio.current_task()
        for task in (self._watchdog, self._worker):
            if task is not None and task is not current:
                task.cancel()
        self._drop_speech()
        if reason == "client":
            await self._cancel_turn()
        if notify:
            await self._send(p.VoiceEnd(reason=reason))
        event_log.log("voice", action="end", reason=reason)
        self._ended()

    # --- what the client sends ---

    async def audio(self, message: p.VoiceAudio) -> None:
        if not self._open:
            return
        pcm = message.pcm()
        if self._input_rate != VAD_RATE:
            pcm = resample(pcm, self._input_rate, VAD_RATE)
        self._refresh_playing()
        for event in self._vad.feed(pcm):
            if event.kind in ("speech_start", "barge_in"):
                self._touch()
            if event.kind == "barge_in":
                await self._cut()
                await self._state("listening")
            elif event.kind == "utterance":
                self._touch()
                try:
                    self._utterances.put_nowait(event.pcm)
                except asyncio.QueueFull:
                    event_log.log("voice", action="utterance_dropped")

    async def text(self, message: p.VoiceText) -> None:
        """An utterance the device already transcribed (local STT)."""
        if not self._open:
            return
        self._touch()
        await self._heard(message.text.strip())

    async def interrupt(self) -> None:
        """ "Shut up" from the client (the button, or its own detection)."""
        if self._open:
            await self._cut()
            await self._state("listening")

    # --- what the chat handler reports for a turn started by voice ---

    async def turn_started(self, turn_id: str) -> None:
        self._touch()
        self._drop_speech()
        speech = _Speech(turn_id, True, SentenceChunker(max_chars=self._services.max_spoken_chars))
        self._begin(speech)
        self._vad.set_playing(True)
        await self._state("thinking", turn_id)

    def token(self, turn_id: str, piece: str) -> None:
        speech = self._speech
        if speech is None or speech.turn_id != turn_id or speech.chunker is None:
            return
        for sentence in speech.chunker.feed(piece):
            speech.queue.put_nowait(sentence)
        self._disarm_settle()
        self._settle = asyncio.get_running_loop().call_later(SETTLE_SECONDS, self._settled, speech)

    def _disarm_settle(self) -> None:
        if self._settle is not None:
            self._settle.cancel()
            self._settle = None

    def _settled(self, speech: _Speech) -> None:
        self._settle = None
        if self._speech is speech and speech.chunker is not None:
            for sentence in speech.chunker.settle():
                speech.queue.put_nowait(sentence)

    def turn_finished(self, turn_id: str) -> None:
        speech = self._speech
        if speech is None or speech.turn_id != turn_id or speech.chunker is None:
            return
        self._disarm_settle()
        for sentence in speech.chunker.flush():
            speech.queue.put_nowait(sentence)
        speech.queue.put_nowait(None)

    # --- internals ---

    def _touch(self) -> None:
        self._last_activity = self._clock()

    def _refresh_playing(self) -> None:
        playing = (
            self._turn_active() or self._speech is not None or self._clock() < self._play_until
        )
        self._vad.set_playing(playing)

    async def _state(self, state: p.VoiceStateName, turn_id: str | None = None) -> None:
        await self._guarded_send(p.VoiceState(state=state, turn_id=turn_id))

    async def _guarded_send(self, message: p.ServerMessage) -> bool:
        if not await self._send(message):
            # the connection is gone: the handler will close us, nothing else to say
            self._open = False
            return False
        return True

    def _begin(self, speech: _Speech) -> None:
        self._speech = speech
        speech.task = asyncio.get_running_loop().create_task(self._speak_loop(speech))

    def _drop_speech(self) -> str | None:
        """Stop what she is saying and forget it; returns the cut turn's id, if any."""
        self._disarm_settle()
        speech, self._speech = self._speech, None
        if speech is None:
            return None
        if speech.task is not None and speech.task is not asyncio.current_task():
            speech.task.cancel()
        self._play_until = 0.0
        return speech.turn_id

    async def _cut(self) -> None:
        """Cancel the turn and the speech in progress (barge-in, stop command, new utterance)."""
        cut = self._drop_speech()
        self._vad.set_playing(False)
        if cut is not None:
            # a listening state *with* a turn id means "that turn was cut: drop its audio"; it goes
            # first so the device goes quiet at once, before the turn finishes cancelling
            await self._state("listening", cut)
        if self._turn_active():
            await self._cancel_turn()

    async def _speak_loop(self, speech: _Speech) -> None:
        seq = 0
        last_was_audio = True
        try:
            while True:
                sentence = await speech.queue.get()
                if sentence is None:
                    break
                spoken = await self._services.speech.speak(sentence)
                for notice in spoken.notices:
                    await self._fallback("tts", notice)
                if not speech.spoke:
                    speech.spoke = True
                    await self._state("speaking", speech.turn_id)
                if spoken.audio is not None:
                    last_was_audio = True
                    seq = await self._send_audio(speech, spoken.audio, seq)
                else:
                    last_was_audio = False
                    text = spoken.say or ""
                    self._extend_play(len(text) * SAY_SECONDS_PER_CHAR)
                    await self._guarded_send(p.VoiceSay(turn_id=speech.turn_id, text=text))
            if speech.spoke:
                if last_was_audio:
                    await self._guarded_send(
                        p.VoiceAudio(seq=seq, data="", turn_id=speech.turn_id, last=True)
                    )
                else:
                    await self._guarded_send(p.VoiceSay(turn_id=speech.turn_id, text="", last=True))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bug in speaking must not drop the connection
            event_log.log("error", where="voice.speak", type=type(exc).__name__)
        if self._speech is speech:
            self._speech = None
            await self._state("listening")

    async def _send_audio(self, speech: _Speech, pcm: bytes, seq: int) -> int:
        step = self._out_rate * 2 * AUDIO_CHUNK_MS // 1000 // 2 * 2
        self._extend_play(len(pcm) / 2 / self._out_rate)
        for i in range(0, len(pcm), step):
            data = base64.b64encode(pcm[i : i + step]).decode("ascii")
            if not await self._guarded_send(
                p.VoiceAudio(seq=seq, data=data, turn_id=speech.turn_id)
            ):
                break
            seq += 1
        return seq

    def _extend_play(self, seconds: float) -> None:
        """Keep "she is talking" true for as long as the device will take to play what was sent."""
        now = self._clock()
        self._play_until = max(self._play_until, now) + seconds
        self._touch()

    async def _fallback(self, component: Literal["stt", "tts"], notice: Notice) -> None:
        await self._guarded_send(
            p.VoiceFallback(component=component, mode=notice.mode, reason=notice.reason)
        )

    async def _listen_loop(self) -> None:
        """Transcribe the utterances one at a time (the audio keeps being analysed meanwhile)."""
        while True:
            pcm = await self._utterances.get()
            try:
                await self._transcribe(pcm)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never end the session for one utterance
                event_log.log("error", where="voice.listen", type=type(exc).__name__)
            if not self._open:  # an end command ended the session from inside this very task
                return

    async def _transcribe(self, pcm: bytes) -> None:
        await self._state("thinking")
        heard = await self._services.stt.listen(pcm, VAD_RATE)
        for notice in heard.notices:
            await self._fallback("stt", notice)
        if heard.text is None:
            if heard.local:
                await self._say_standalone(CANT_HEAR_MESSAGE)
            elif self._speech is None and not self._turn_active():
                await self._state("listening")
            return
        await self._guarded_send(p.VoiceTranscript(text=heard.text))
        await self._heard(heard.text)

    async def _heard(self, text: str) -> None:
        """One utterance of text, from either STT: a command, or a turn of chat."""
        if not text:
            return
        if is_end_command(text):
            await self.end("command")
            return
        if is_stop_command(text):
            if self._speech is not None or self._turn_active():
                await self._cut()
            await self._state("listening")
            return
        if self._speech is not None or self._turn_active():
            await self._cut()  # what he says now replaces what she was saying
        await self._submit(text)

    async def _say_standalone(self, text: str) -> None:
        if self._speech is not None or self._turn_active():
            return
        speech = _Speech(f"v-{secrets.token_hex(4)}", False, None)
        self._begin(speech)
        speech.queue.put_nowait(text)
        speech.queue.put_nowait(None)

    async def _watch(self) -> None:
        """Idle timeout, and the notice that the cloud STT is worth trying again."""
        while True:
            await asyncio.sleep(self._tick)
            notice = self._services.stt.recovered()
            if notice is not None:
                await self._fallback("stt", notice)
            busy = (
                self._turn_active() or self._speech is not None or self._clock() < self._play_until
            )
            if busy:
                self._touch()
            elif self._clock() - self._last_activity >= self._idle:
                await self.end("silence")
                return
