"""``juniper voices`` (T-213): consent, the vectors never shown, deletion at once."""

from __future__ import annotations

import wave
from datetime import UTC, datetime
from pathlib import Path

import mongomock
import pytest
from typer.testing import CliRunner

from brain.auth.credentials import User
from brain.auth.store import UserStore
from brain.clients import cli, voices_cli
from brain.memory.mongo import MongoConnection
from brain.voice.speaker import SpeakerIdentifier, VoiceKey, VoiceprintStore
from tests.fake_speaker import ANA, BIA, CAIO, RATE, ToneEmbedder, voice_pcm

runner = CliRunner()


def write_wav(path: Path, pcm: bytes, *, rate: int = RATE, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return path


@pytest.fixture(autouse=True)
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SpeakerIdentifier:
    conn = MongoConnection()
    conn._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    users = UserStore(conn)
    for uid, name in (("u_ana", "Ana"), ("u_bia", "Bia")):
        users.add_user(User(user_id=uid, name=name, role="tester", created_at=datetime.now(UTC)))
    identifier = SpeakerIdentifier(
        ToneEmbedder(), VoiceprintStore(VoiceKey.load_or_create(tmp_path / "k"), conn)
    )
    monkeypatch.setattr(voices_cli, "_identifier", lambda: identifier)
    monkeypatch.setattr(voices_cli, "_users", lambda: users)
    return identifier


def run(*args: str, input: str | None = None) -> tuple[int, str]:
    result = runner.invoke(cli.app, ["voices", *args], input=input, terminal_width=200)
    return result.exit_code, result.output


def samples(tmp_path: Path, tones: tuple[float, ...], n: int = 3) -> list[str]:
    out: list[str] = []
    for i in range(n):
        path = write_wav(tmp_path / f"{tones[0]:.0f}-{i}.wav", voice_pcm(tones, 2.0, seed=i))
        out += ["--wav", str(path)]
    return out


def stored(rig: SpeakerIdentifier) -> int:
    return len(rig.store.info())


def test_adding_with_the_consent_flag_registers_the_voice(
    tmp_path: Path, rig: SpeakerIdentifier
) -> None:
    code, out = run("add", "Ana", *samples(tmp_path, ANA), "--consent", "--label", "rouca")
    assert code == 0 and "Voz de Ana cadastrada" in out and "«rouca»" in out
    assert stored(rig) == 1


def test_without_the_flag_it_asks_and_a_no_registers_nothing(
    tmp_path: Path, rig: SpeakerIdentifier
) -> None:
    code, out = run("add", "Ana", *samples(tmp_path, ANA), input="n\n")
    assert code == 1 and "vetor de voz" in out and "concorda?" in out
    assert "cadastrada" not in out
    assert stored(rig) == 0
    code, out = run("add", "Ana", *samples(tmp_path, ANA), input="y\n")
    assert code == 0 and stored(rig) == 1


def test_an_unknown_user_is_pointed_to_users_add(tmp_path: Path, rig: SpeakerIdentifier) -> None:
    code, out = run("add", "Fulano", *samples(tmp_path, ANA), "--consent")
    assert code == 1 and "juniper users add" in out and stored(rig) == 0


def test_bad_samples_are_explained_not_stored(tmp_path: Path, rig: SpeakerIdentifier) -> None:
    code, out = run("add", "Ana", *samples(tmp_path, ANA, 2), "--consent")
    assert code == 1 and "ao menos 3 amostras" in out
    stereo = write_wav(tmp_path / "estereo.wav", b"\x00\x00" * RATE, channels=2)
    code, out = run("add", "Ana", "--wav", str(stereo), "--consent")
    assert code == 1 and "estereo.wav" in out
    mixed = [
        write_wav(tmp_path / f"m{i}.wav", voice_pcm(t, 2.0, seed=i))
        for i, t in enumerate((ANA, BIA, CAIO))
    ]
    code, out = run("add", "Ana", *[a for m in mixed for a in ("--wav", str(m))], "--consent")
    assert code == 1 and "mesma pessoa" in out
    assert stored(rig) == 0


def test_the_list_shows_who_agreed_and_never_the_vector(
    tmp_path: Path, rig: SpeakerIdentifier
) -> None:
    assert "Nenhuma voz cadastrada" in run("list")[1]
    run("add", "Ana", *samples(tmp_path, ANA), "--consent")
    code, out = run("list")
    assert code == 0 and "Ana" in out and "normal" in out and "fake:tone" in out
    assert "vector" not in out.lower() and "sealed" not in out.lower()
    assert "aceitou" in out


def test_removing_a_voice_is_immediate_and_a_missing_one_is_an_error(
    tmp_path: Path, rig: SpeakerIdentifier
) -> None:
    run("add", "Ana", *samples(tmp_path, ANA), "--consent")
    voiceprint_id = rig.store.info()[0]["_id"]
    code, out = run("remove", voiceprint_id)
    assert code == 0 and "apagada" in out and stored(rig) == 0
    code, out = run("remove", voiceprint_id)
    assert code == 1 and "Não achei" in out


def test_forget_erases_every_voice_of_a_person_and_only_theirs(
    tmp_path: Path, rig: SpeakerIdentifier
) -> None:
    run("add", "Ana", *samples(tmp_path, ANA), "--consent", "--label", "a")
    run("add", "Ana", *samples(tmp_path, ANA), "--consent", "--label", "b")
    run("add", "Bia", *samples(tmp_path, BIA), "--consent")
    code, out = run("forget", "Ana", input="n\n")
    assert "Nada apagado" in out and stored(rig) == 3
    code, out = run("forget", "Ana", "--yes")
    assert code == 0 and "2 voz(es) de Ana" in out
    assert [d["user_id"] for d in rig.store.info()] == ["u_bia"]


def test_test_says_who_a_recording_sounds_like(tmp_path: Path, rig: SpeakerIdentifier) -> None:
    clip = write_wav(tmp_path / "clip.wav", voice_pcm(ANA, 1.5, seed=7))
    assert "Ninguém tem voz cadastrada" in run("test", str(clip))[1]
    run("add", "Ana", *samples(tmp_path, ANA), "--consent")
    run("add", "Bia", *samples(tmp_path, BIA), "--consent")
    code, out = run("test", str(clip))
    assert code == 0 and "Soa como: Ana" in out and "semelhança" in out
    stranger = write_wav(tmp_path / "stranger.wav", voice_pcm(CAIO, 1.5))
    assert "ninguém conhecido" in run("test", str(stranger))[1]


def test_without_a_configured_model_it_says_what_to_configure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()  # the real opener: no model file in the default settings
    code, out = run("list")
    assert code == 1 and "voice.speaker.model_file" in out
