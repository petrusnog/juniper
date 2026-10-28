"""The monthly TTS budget, the cloud/device fallback and ``juniper voice status`` (T-150)."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from brain.clients import cli
from brain.config import settings
from brain.voice.budget import VoiceBudget
from brain.voice.errors import VoiceError
from brain.voice.speech import Notice, SpeechService
from brain.voice.tts import Synthesizer

SP = ZoneInfo("America/Sao_Paulo")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def budget(tmp_path: Path, limit: int = 100, now: datetime | None = None) -> VoiceBudget:
    start = now or datetime(2026, 3, 10, 12, tzinfo=UTC)
    return VoiceBudget(tmp_path / "usage.json", limit, clock=Clock(start), zone=SP)


# --- the budget ----------------------------------------------------------------------------


def test_reserve_counts_until_the_exact_limit(tmp_path: Path) -> None:
    b = budget(tmp_path, 10)
    assert b.reserve(6) and b.remaining() == 4
    assert b.reserve(4) and b.remaining() == 0 and b.exhausted()
    assert not b.reserve(1)
    assert b.used() == 10


def test_a_reserve_that_does_not_fit_counts_nothing(tmp_path: Path) -> None:
    b = budget(tmp_path, 10)
    assert b.reserve(8)
    assert not b.reserve(3)
    assert b.used() == 8


def test_limit_zero_means_the_cloud_voice_is_off(tmp_path: Path) -> None:
    b = budget(tmp_path, 0)
    assert b.exhausted() and not b.reserve(1)


def test_release_gives_back_and_never_goes_below_zero(tmp_path: Path) -> None:
    b = budget(tmp_path, 10)
    b.reserve(5)
    b.release(3)
    assert b.used() == 2
    b.release(50)
    assert b.used() == 0


def test_exhaust_spends_what_is_left(tmp_path: Path) -> None:
    b = budget(tmp_path, 10)
    b.reserve(3)
    b.exhaust()
    assert b.exhausted() and b.used() == 10


def test_the_count_survives_a_restart(tmp_path: Path) -> None:
    budget(tmp_path, 100).reserve(42)
    assert budget(tmp_path, 100).used() == 42


def test_the_month_turns_in_the_agent_time_zone(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 3, 31, 23, 30, tzinfo=SP))  # March 31st in São Paulo
    b = VoiceBudget(tmp_path / "usage.json", 10, clock=clock, zone=SP)
    b.reserve(10)
    assert b.exhausted()
    clock.now = datetime(2026, 4, 1, 0, 30, tzinfo=SP)
    assert b.used() == 0 and not b.exhausted()


def test_utc_midnight_does_not_turn_the_month_in_sao_paulo(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 4, 1, 1, 0, tzinfo=UTC))  # still March 31st, 22:00, in São Paulo
    b = VoiceBudget(tmp_path / "usage.json", 10, clock=clock, zone=SP)
    b.reserve(10)
    clock.now = datetime(2026, 4, 1, 2, 0, tzinfo=UTC)
    assert b.exhausted()


def test_a_file_from_another_month_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    path.write_text(json.dumps({"month": "2026-02", "tts_chars": 99}))
    assert (
        VoiceBudget(path, 100, clock=Clock(datetime(2026, 3, 10, tzinfo=UTC)), zone=SP).used() == 0
    )


@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"month": "2026-03", "tts_chars": "x"}', ""]
)
def test_a_corrupt_file_starts_the_month_at_zero(tmp_path: Path, content: str) -> None:
    (tmp_path / "usage.json").write_text(content)
    assert budget(tmp_path).used() == 0


def test_a_negative_count_in_the_file_is_not_a_credit(tmp_path: Path) -> None:
    (tmp_path / "usage.json").write_text(json.dumps({"month": "2026-03", "tts_chars": -500}))
    assert budget(tmp_path, 10).remaining() == 10


def test_the_file_is_private(tmp_path: Path) -> None:
    b = budget(tmp_path)
    b.reserve(1)
    mode = stat.S_IMODE((tmp_path / "usage.json").stat().st_mode)
    assert mode == 0o600


def test_a_disk_that_refuses_keeps_counting_in_memory(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    b = VoiceBudget(
        blocker / "usage.json", 10, clock=Clock(datetime(2026, 3, 10, tzinfo=UTC)), zone=SP
    )
    assert b.reserve(7)
    assert b.used() == 7 and not b.reserve(5)


# --- the speech service --------------------------------------------------------------------


class FakeSynth(Synthesizer):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: Exception | None = None

    @property
    def sample_rate(self) -> int:
        return 24000

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return b"\x01\x00" * 4


class Mono:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def service(
    tmp_path: Path, limit: int = 1000
) -> tuple[SpeechService, FakeSynth, VoiceBudget, Mono]:
    synth, b, mono = FakeSynth(), budget(tmp_path, limit), Mono()
    return SpeechService(synth, b, retry_seconds=30, clock=mono), synth, b, mono


async def test_a_sentence_becomes_audio_and_is_counted(tmp_path: Path) -> None:
    s, synth, b, _ = service(tmp_path)
    spoken = await s.speak("Olá, Petrus.")
    assert spoken.audio and spoken.say is None and spoken.notices == []
    assert synth.calls == ["Olá, Petrus."] and b.used() == len("Olá, Petrus.")
    assert s.mode == "cloud" and s.sample_rate == 24000


async def test_an_exhausted_budget_goes_to_the_device_with_one_notice(tmp_path: Path) -> None:
    s, synth, b, _ = service(tmp_path, 20)
    assert (await s.speak("x" * 15)).audio
    first = await s.speak("y" * 10)  # does not fit: 5 left is less than a sentence
    assert first.say == "y" * 10 and first.audio is None
    assert first.notices == [Notice("local", "budget")]
    second = await s.speak("z" * 3)
    assert second.say == "z" * 3 and second.notices == []  # told once, not per sentence
    assert len(synth.calls) == 1 and b.exhausted()


async def test_the_next_month_brings_the_cloud_back_with_a_notice(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 3, 10, 12, tzinfo=UTC))
    b = VoiceBudget(tmp_path / "usage.json", 10, clock=clock, zone=SP)
    s = SpeechService(FakeSynth(), b, retry_seconds=30, clock=Mono())
    await s.speak("x" * 8)
    await s.speak("y" * 8)
    assert s.mode == "local"
    clock.now += timedelta(days=30)
    again = await s.speak("w" * 5)
    assert again.audio and again.notices == [Notice("cloud", "recovered")]


async def test_without_a_synthesizer_everything_is_local_without_notices(tmp_path: Path) -> None:
    s = SpeechService(None, budget(tmp_path), clock=Mono())
    spoken = await s.speak("oi")
    assert spoken.say == "oi" and spoken.notices == [] and s.sample_rate is None


async def test_a_failure_goes_local_waits_and_then_retries(tmp_path: Path) -> None:
    s, synth, b, mono = service(tmp_path)
    synth.error = VoiceError("network_error", "sem rede")
    first = await s.speak("um dois")
    assert first.say == "um dois" and first.notices == [Notice("local", "error")]
    assert b.used() == 0  # the failed call is refunded
    calls = len(synth.calls)
    during = await s.speak("três")
    assert during.say == "três" and during.notices == [] and len(synth.calls) == calls
    mono.now += 31
    synth.error = None
    back = await s.speak("quatro")
    assert back.audio and back.notices == [Notice("cloud", "recovered")]


async def test_a_quota_error_waits_longer_and_says_why(tmp_path: Path) -> None:
    s, synth, _, mono = service(tmp_path)
    synth.error = VoiceError("quota", "cota")
    assert (await s.speak("a b")).notices == [Notice("local", "quota")]
    mono.now += 31
    assert (await s.speak("c d")).say == "c d"
    assert len(synth.calls) == 1  # still cooling down after 31 s
    mono.now += 600
    synth.error = None
    assert (await s.speak("e f")).audio


async def test_a_bug_in_the_provider_does_not_break_the_conversation(tmp_path: Path) -> None:
    s, synth, b, _ = service(tmp_path)
    synth.error = RuntimeError("boom")
    spoken = await s.speak("segredo da conversa")
    assert spoken.say == "segredo da conversa" and b.used() == 0


async def test_logs_carry_sizes_never_the_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.voice import speech

    seen: list[dict[str, object]] = []
    monkeypatch.setattr(speech.event_log, "log", lambda kind, **kw: seen.append(kw))
    s, synth, _, _ = service(tmp_path)
    await s.speak("texto muito privado")
    synth.error = VoiceError("api_error", "falhou")
    await s.speak("outro texto privado")
    assert seen and "privado" not in json.dumps(seen)


# --- juniper voice status ------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def voice_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings.voice, "usage_file", tmp_path / "usage.json")
    monkeypatch.setattr(settings.voice.tts, "credentials_file", tmp_path / "sa.json")
    monkeypatch.setattr(settings, "groq_api_key", None)
    return tmp_path


def test_voice_status_tells_what_is_missing(voice_cfg: Path) -> None:
    result = runner.invoke(cli.app, ["voice", "status"])
    assert result.exit_code == 0
    assert "Credencial do Google: FALTA" in result.output
    assert "Chave da Groq (fala para texto): FALTA" in result.output


def test_voice_status_shows_usage_and_never_a_secret(
    voice_cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (voice_cfg / "sa.json").write_text('{"private_key": "PRIVATE-KEY-VALUE"}')
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk-secret-value"))
    VoiceBudget(settings.voice.usage_file, settings.voice.tts.monthly_chars_limit).reserve(123)
    result = runner.invoke(cli.app, ["voice", "status"])
    assert "Credencial do Google: encontrada" in result.output
    assert "configurada" in result.output and "123 de" in result.output
    assert "gsk-secret-value" not in result.output and "PRIVATE-KEY-VALUE" not in result.output
