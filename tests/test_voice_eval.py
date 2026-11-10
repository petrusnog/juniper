"""The evaluation harness of the STT (T-203): the metrics, the set on disk and the run.

Everything here is automated and offline: the transcriber is a fake and the recordings are
generated tones. What these tests cannot say is how the real Whisper does on real voices; that is
the manual run described in docs/pages/voz-avaliacao.md.
"""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path

import pytest

from brain.voice import evaluation as ev
from brain.voice.errors import VoiceError
from brain.voice.stt import Transcriber

# --- the metrics -----------------------------------------------------------------------------


def test_edit_distance_counts_insertions_deletions_and_substitutions() -> None:
    assert ev.edit_distance(list("gato"), list("pato")) == 1
    assert ev.edit_distance(list("gato"), list("gatos")) == 1
    assert ev.edit_distance(list("gato"), list("ato")) == 1
    assert ev.edit_distance([], list("abc")) == 3
    assert ev.edit_distance(list("abc"), []) == 3
    assert ev.edit_distance(list("kitten"), list("sitting")) == 3


def test_the_wer_of_the_example_in_the_feature_request() -> None:
    score = ev.word_errors(
        "Juniper, abra o projeto Media Works.", "Juniper, abra o projeto mídia works."
    )
    assert (score.errors, score.total) == (1, 6)
    assert score.rate == pytest.approx(1 / 6)


def test_comparison_ignores_case_accents_and_punctuation() -> None:
    assert ev.word_errors("Até amanhã, Petrus!", "ate AMANHA petrus").errors == 0
    assert ev.char_errors("Até amanhã!", "ate amanha").errors == 0


def test_hearing_nothing_costs_every_word() -> None:
    score = ev.word_errors("que horas são", "")
    assert (score.errors, score.total) == (3, 3)


def test_the_cer_counts_characters() -> None:
    score = ev.char_errors("gato", "pato")
    assert (score.errors, score.total) == (1, 4)


def test_a_rate_over_nothing_said_is_undefined_not_zero() -> None:
    assert ev.Score().rate is None
    assert ev.Score(2, 4).rate == 0.5
    assert (ev.Score(1, 2) + ev.Score(2, 6)) == ev.Score(3, 8)


def test_term_hits_count_each_time_a_term_was_said() -> None:
    terms = ["Media Works", "Juniper"]
    said = "Juniper, abra o projeto Media Works"
    assert ev.term_hits(said, "juniper abra o projeto media works", terms) == ev.Score(0, 2)
    assert ev.term_hits(said, "juniper abra o projeto mídia works", terms) == ev.Score(1, 2)
    assert ev.term_hits("Juniper Juniper", "juniper", ["Juniper"]) == ev.Score(1, 2)
    # a term inside another word is not a hit: "juniper" is not in "juniperos"
    assert ev.term_hits("juniper", "juniperos", ["Juniper"]) == ev.Score(1, 1)


def test_term_hits_ignore_terms_that_were_not_said_and_blank_lines() -> None:
    assert ev.term_hits("que horas são", "que horas são", ["Media Works", "  "]) == ev.Score(0, 0)


def test_percentile_is_the_nearest_rank() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    assert ev.percentile(values, 50) == 50.0
    assert ev.percentile(values, 95) == 100.0
    assert ev.percentile([7.0], 95) == 7.0
    assert ev.percentile([], 50) is None


# --- the set on disk -------------------------------------------------------------------------


def write_wav(path: Path, *, rate: int = 16_000, channels: int = 1, ms: int = 100) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x10\x00" * (rate * ms // 1000) * channels)


def make_set(folder: Path, phrases: dict[str, str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in phrases.items():
        write_wav(folder / f"{name}.wav")
        (folder / f"{name}.txt").write_text(text + "\n", encoding="utf-8")


def test_load_cases_pairs_each_recording_with_what_was_said(tmp_path: Path) -> None:
    make_set(tmp_path, {"b-longa": "segunda frase", "a-curta": "primeira frase"})
    cases = ev.load_cases(tmp_path)
    assert [(c.name, c.reference) for c in cases] == [
        ("a-curta", "primeira frase"),
        ("b-longa", "segunda frase"),
    ]


def test_a_recording_without_its_text_is_an_error_that_names_it(tmp_path: Path) -> None:
    make_set(tmp_path, {"ok": "frase"})
    write_wav(tmp_path / "sem-texto.wav")
    write_wav(tmp_path / "vazio.wav")
    (tmp_path / "vazio.txt").write_text("  \n", encoding="utf-8")
    with pytest.raises(ev.EvalError, match=r"sem-texto\.wav.*vazio\.wav"):
        ev.load_cases(tmp_path)


def test_a_missing_or_empty_folder_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ev.EvalError, match="não existe"):
        ev.load_cases(tmp_path / "nada")
    with pytest.raises(ev.EvalError, match="nenhum .wav"):
        ev.load_cases(tmp_path)


def test_terms_skip_comments_and_blank_lines(tmp_path: Path) -> None:
    assert ev.load_terms(tmp_path) == []
    (tmp_path / "termos.txt").write_text("# nomes\nMedia Works\n\n  Fortaleza \n", encoding="utf-8")
    assert ev.load_terms(tmp_path) == ["Media Works", "Fortaleza"]


def test_audio_is_resampled_to_what_the_voice_session_sends(tmp_path: Path) -> None:
    write_wav(tmp_path / "x.wav", rate=8_000, ms=500)
    pcm = ev.read_audio(tmp_path / "x.wav")
    assert len(pcm) == 16_000 * 500 // 1000 * 2


def test_stereo_or_unreadable_audio_is_an_error_not_a_crash(tmp_path: Path) -> None:
    write_wav(tmp_path / "estereo.wav", channels=2)
    (tmp_path / "lixo.wav").write_bytes(b"not a wav")
    with pytest.raises(ev.EvalError, match=r"estereo\.wav"):
        ev.read_audio(tmp_path / "estereo.wav")
    with pytest.raises(ev.EvalError, match=r"lixo\.wav"):
        ev.read_audio(tmp_path / "lixo.wav")


# --- the run ---------------------------------------------------------------------------------


class Scripted(Transcriber):
    """Answers the phrases in order; an item that is a VoiceError is raised instead."""

    def __init__(self, *answers: str | VoiceError) -> None:
        self.answers = list(answers)
        self.rates: list[int] = []

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        self.rates.append(sample_rate)
        answer = self.answers.pop(0)
        if isinstance(answer, VoiceError):
            raise answer
        return answer


def cases_of(*pairs: tuple[str, str]) -> list[ev.Case]:
    return [ev.Case(name, Path(f"{name}.wav"), said) for name, said in pairs]


def fake_clock(*instants: float):  # type: ignore[no-untyped-def]
    ticks = iter(instants)
    return lambda: next(ticks)


async def test_the_run_scores_the_whole_set_not_the_average_of_phrases() -> None:
    stt = Scripted("abra o projeto mídia works", "que horas são")
    report = await ev.evaluate(
        stt,
        cases_of(("a", "abra o projeto Media Works"), ("b", "que horas são")),
        terms=["Media Works"],
        clock=fake_clock(0.0, 0.2, 1.0, 1.5),
        audio=lambda _path: b"\x00\x00" * 8,
    )
    assert (report.words.errors, report.words.total) == (1, 8)  # 1 error in 5 + 3 words
    assert report.term_misses == ev.Score(1, 1)
    assert [round(r.ms) for r in report.results] == [200, 500]
    assert report.latency()["p50_ms"] == pytest.approx(200)
    assert report.latency()["mean_ms"] == pytest.approx(350)
    assert stt.rates == [16_000, 16_000]


async def test_a_typed_failure_is_a_failed_phrase_and_the_run_goes_on() -> None:
    stt = Scripted(VoiceError("quota", "limite"), "bom dia")
    report = await ev.evaluate(
        stt,
        cases_of(("a", "boa tarde pessoal"), ("b", "bom dia")),
        audio=lambda _path: b"\x00\x00",
    )
    first, second = report.results
    assert first.failed and first.error == "quota" and first.heard == ""
    assert first.words == ev.Score()  # a failed call says nothing about the words
    assert not second.failed and second.words == ev.Score(0, 2)
    assert report.words == ev.Score(0, 2)  # so it stays out of the totals, not scored as wrong
    assert report.failures == 1
    assert "ATENÇÃO: 1 frase(s) falharam" in ev.render(report)
    assert report.latency()["mean_ms"] is not None  # failures do not pollute the latency


async def test_a_hallucination_counts_as_heard_nothing_like_in_the_voice_session() -> None:
    stt = Scripted("Legendas pela comunidade Amara.org")
    report = await ev.evaluate(
        stt, cases_of(("a", "que horas são")), audio=lambda _path: b"\x00\x00"
    )
    result = report.results[0]
    assert result.dropped and result.heard == "" and result.words == ev.Score(3, 3)
    assert report.dropped == 1 and report.failures == 0


async def test_the_pause_is_between_calls_not_before_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await ev.evaluate(
        Scripted("a", "b", "c"),
        cases_of(("1", "a"), ("2", "b"), ("3", "c")),
        pause=3.2,
        audio=lambda _path: b"\x00\x00",
    )
    assert slept == [3.2, 3.2]


async def test_end_to_end_over_files_and_the_report(tmp_path: Path) -> None:
    make_set(tmp_path, {"01-hora": "Juniper, que horas são?", "02-projeto": "Abra o Media Works"})
    (tmp_path / "termos.txt").write_text("Media Works\n", encoding="utf-8")
    report = await ev.evaluate(
        Scripted("juniper que horas são", "abra o mídia works"),
        ev.load_cases(tmp_path),
        terms=ev.load_terms(tmp_path),
    )
    text = ev.render(report)
    assert "02-projeto" in text and "disse:   Abra o Media Works" in text
    assert "ouviu:   abra o mídia works" in text
    assert "01-hora" not in text  # the phrase that came out right is not listed
    assert "WER: 12.5% (1 erros em 8 palavras)" in text
    data = report.to_dict()
    assert json.loads(json.dumps(data))["phrases"] == 2
    assert data["term_miss_rate"] == 1.0 and data["failures"] == 0


async def test_an_empty_report_has_no_rates_instead_of_dividing_by_zero() -> None:
    report = await ev.evaluate(Scripted(), [])
    assert report.words.rate is None and report.latency()["p95_ms"] is None
    assert "WER: n/d" in ev.render(report)
    assert json.dumps(report.to_dict())
