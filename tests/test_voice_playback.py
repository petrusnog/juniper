"""The device says when its speaker is busy: the echo gate follows the sound, not a guess
(T-208, ADR-034 section 5)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from brain.clients import protocol as p
from brain.clients import voice_session as vs
from brain.clients.voice_session import ECHO_TAIL_SECONDS, VoiceSession
from tests.test_agent import FakeProvider
from tests.test_voice_session import Rig, wait_for


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def opened(tmp_path: Path, clock: Clock) -> VoiceSession:
    rig = Rig(tmp_path, FakeProvider([]))

    async def send(_m: p.ServerMessage) -> bool:
        return True

    async def nothing() -> None:
        return None

    session = VoiceSession(
        rig.services,
        send=send,
        submit=lambda _t: nothing(),
        cancel_turn=nothing,
        turn_active=lambda: False,
        ended=lambda: None,
        clear_pending=lambda: None,
        route="speaker",
        clock=clock,
        idle_timeout=1000,
    )
    await session.start("v-1")
    return session


def sent(state: str) -> p.VoicePlayback:
    return p.VoicePlayback(id="p", state=state)  # type: ignore[arg-type]


async def test_without_reports_the_estimate_decides_like_before(tmp_path: Path) -> None:
    clock = Clock()
    session = await opened(tmp_path, clock)
    try:
        session._extend_play(5.0)  # five seconds of sound were sent
        clock.now += 5.0 + ECHO_TAIL_SECONDS - 0.1
        assert session._echo_gate()
        clock.now += 0.2
        assert not session._echo_gate()
    finally:
        await session.end("client", notify=False)


async def test_finished_closes_the_tail_from_the_real_end_of_the_sound(tmp_path: Path) -> None:
    clock = Clock()
    session = await opened(tmp_path, clock)
    try:
        session._extend_play(5.0)
        session.playback(sent("started"))
        clock.now += 2.0  # the sound actually lasted 2 s, not the 5 s estimated
        assert session._echo_gate()
        session.playback(sent("finished"))
        clock.now += ECHO_TAIL_SECONDS - 0.1
        assert session._echo_gate()  # still inside the tail of the real end
        clock.now += 0.2
        assert not session._echo_gate()  # heard again 1 s after the real end, not 4 s
    finally:
        await session.end("client", notify=False)


async def test_while_the_device_says_it_plays_the_microphone_stays_closed(tmp_path: Path) -> None:
    clock = Clock()
    session = await opened(tmp_path, clock)
    try:
        session._extend_play(1.0)  # estimated 1 s, but the device is slower
        session.playback(sent("started"))
        clock.now += 1.0 + ECHO_TAIL_SECONDS + 0.5  # past the estimate and its tail
        assert session._echo_gate()  # still closed: the device is still playing
    finally:
        await session.end("client", notify=False)


async def test_a_lost_finished_cannot_close_the_microphone_for_good(tmp_path: Path) -> None:
    clock = Clock()
    session = await opened(tmp_path, clock)
    try:
        session._extend_play(1.0)
        session.playback(sent("started"))  # and "finished" never comes
        clock.now += 1.0 + vs.DEVICE_GRACE_SECONDS + 0.1
        assert not session._echo_gate()
    finally:
        await session.end("client", notify=False)


async def test_a_reply_with_a_gap_between_sentences_stays_closed(tmp_path: Path) -> None:
    clock = Clock()
    session = await opened(tmp_path, clock)
    try:
        session._extend_play(1.0)
        session.playback(sent("started"))
        clock.now += 1.0
        session.playback(sent("finished"))  # sentence 1 played, sentence 2 still being made
        session._speech = vs._Speech("t-1", True, None)
        session._speech.spoke = True
        clock.now += 3.0
        assert session._echo_gate()  # she is mid-reply: the gate is about her, not the speaker
    finally:
        session._speech = None
        await session.end("client", notify=False)


async def test_playback_after_the_session_closed_is_ignored(tmp_path: Path) -> None:
    session = await opened(tmp_path, Clock())
    await session.end("client", notify=False)
    session.playback(sent("started"))
    assert not session._device_playing


async def test_the_session_waits_for_the_device_before_closing_after_music(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vs, "END_PLAYBACK_PAD_SECONDS", 0.0)
    session = await opened(tmp_path, vs.time.monotonic)  # type: ignore[arg-type]
    ended: list[float] = []
    real_end = session.end

    async def spy(reason: p.VoiceEndReason, *, notify: bool = True) -> None:
        ended.append(asyncio.get_running_loop().time())
        await real_end(reason, notify=notify)

    session.end = spy  # type: ignore[method-assign]
    session._extend_play(0.05)
    session.playback(sent("started"))
    began = asyncio.get_running_loop().time()
    task = asyncio.create_task(session._end_after_playback())
    await asyncio.sleep(0.3)
    assert not ended  # the estimate was over long ago, but the device still plays
    session.playback(sent("finished"))
    await asyncio.wait_for(task, 2.0)
    assert ended and ended[0] - began >= 0.3


# --- the message -----------------------------------------------------------------------------


def test_the_message_has_exactly_two_states() -> None:
    assert p.VoicePlayback(id="a", state="started").state == "started"
    assert p.VoicePlayback(id="a", state="finished").type == "voice.playback"
    with pytest.raises(ValidationError):
        p.VoicePlayback(id="a", state="paused")  # type: ignore[arg-type]


async def test_the_server_accepts_it_from_a_client_and_keeps_the_session(tmp_path: Path) -> None:
    async with Rig(tmp_path, FakeProvider([])) as rig:
        client, _ = await rig.open()
        await client.send(type="voice.playback", state="started")
        await client.send(type="voice.playback", state="finished")
        await client.send(type="voice.playback", state="nonsense")  # typed error, not a crash
        error = (await client.until(p.Error))[-1]
        assert isinstance(error, p.Error)
        await client.send(type="voice.end", reason="client")
        await wait_for(lambda: not rig.handler._voices)  # the session was alive until it ended
