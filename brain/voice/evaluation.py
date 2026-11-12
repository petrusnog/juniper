"""Measuring the speech-to-text on a recorded set (T-203, ADR-034 section 9).

Before changing how the voice listens, measure how it listens today; after each change, measure
again. A *set* is a folder with one ``name.wav`` (mono, 16-bit) and one ``name.txt`` per phrase
(the exact words that were said), plus an optional ``termos.txt`` with the proper nouns and
technical terms that matter (one per line, ``#`` starts a comment).

The numbers:

- **WER** (word error rate) and **CER** (character error rate): edits needed to turn what was
  heard into what was said, over the length of what was said. They are computed on the whole set
  (total edits over total length), so a long phrase weighs more than a short one.
- **Term recall**: of the times a listed term was said, how many came out right. This is what the
  glossary of T-205 is meant to move.
- **Latency** of the transcription call (mean, median, 95th percentile).
- **Failures**: calls that raised a typed error (a rate limit, the network). They say nothing
  about how well the words were heard, so they are left *out* of WER, CER and the terms and
  reported apart; a run with failures is not a valid baseline. Phrases dropped as a Whisper
  hallucination do count (as heard nothing): that is what the voice session does with them.

Text is compared normalised (lowercase, no accents, no punctuation), like the hallucination filter
does: this measures *which words* were heard, not accents or commas. Nothing here touches the
network by itself; the ``Transcriber`` it is given decides that, so the tests use a fake one.
The result contains the spoken text of the set: keep the report and the recordings out of git.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from brain.voice.errors import VoiceError
from brain.voice.stt import Transcriber, is_hallucination, normalise
from brain.voice.tts import resample, wav_to_pcm

SAMPLE_RATE: Final = 16_000  # what the voice session sends to the transcriber
MAX_WAV_BYTES: Final = 24 * 1024 * 1024  # the Groq free tier takes up to 25 MB per call
TERMS_FILE: Final = "termos.txt"


class EvalError(Exception):
    """The set cannot be evaluated (a missing reference, an unreadable recording)."""


# --- the metrics -----------------------------------------------------------------------------


def edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    """Levenshtein distance between two sequences (insertions, deletions, substitutions)."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i]
        for j, item_b in enumerate(b, start=1):
            cost = 0 if item_a == item_b else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class Score:
    """``errors`` out of ``total`` units said; the rate is undefined when nothing was said."""

    errors: int = 0
    total: int = 0

    @property
    def rate(self) -> float | None:
        return self.errors / self.total if self.total else None

    def __add__(self, other: Score) -> Score:
        return Score(self.errors + other.errors, self.total + other.total)


def word_errors(reference: str, heard: str) -> Score:
    said = normalise(reference).split()
    return Score(edit_distance(said, normalise(heard).split()), len(said))


def char_errors(reference: str, heard: str) -> Score:
    said = normalise(reference)
    return Score(edit_distance(said, normalise(heard)), len(said))


def term_hits(reference: str, heard: str, terms: Sequence[str]) -> Score:
    """For each listed term: the times it was said and the times it was heard right.

    ``errors`` counts the missed ones, so ``rate`` is the miss rate (0.0 is perfect).
    """
    said = f" {normalise(reference)} "
    got = f" {normalise(heard)} "
    missed = total = 0
    for term in terms:
        needle = f" {normalise(term)} "
        if needle.strip() == "":
            continue
        wanted = _occurrences(said, needle)
        total += wanted
        missed += wanted - min(wanted, _occurrences(got, needle))
    return Score(missed, total)


def _occurrences(text: str, needle: str) -> int:
    count = start = 0
    while (found := text.find(needle, start)) != -1:
        count += 1
        start = found + len(needle) - 1  # the space between two words can be shared
    return count


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (``q`` in 0..100); None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))  # ceil
    return ordered[int(rank) - 1]


# --- the set ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    name: str
    audio: Path
    reference: str


def load_terms(folder: Path) -> list[str]:
    path = folder / TERMS_FILE
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def load_cases(folder: Path) -> list[Case]:
    """Every ``*.wav`` of the folder with the text said in it. Raises `EvalError`."""
    if not folder.is_dir():
        raise EvalError(f"A pasta {folder} não existe.")
    audios = sorted(a for a in folder.glob("*.wav") if a.is_file() and not a.is_symlink())
    if not audios:
        raise EvalError(f"Não há nenhum .wav em {folder}.")
    cases: list[Case] = []
    missing: list[str] = []
    for audio in audios:
        reference_file = audio.with_suffix(".txt")
        reference = (
            reference_file.read_text(encoding="utf-8").strip() if (reference_file.is_file()) else ""
        )
        if not reference:
            missing.append(audio.name)
            continue
        cases.append(Case(audio.stem, audio, reference))
    if missing:
        raise EvalError(
            "Faltam as frases de referência (.txt, não vazio) de: " + ", ".join(missing)
        )
    return cases


def read_audio(path: Path) -> bytes:
    """The recording as 16 kHz mono 16-bit PCM, the format the voice session transcribes."""
    try:
        if path.stat().st_size > MAX_WAV_BYTES:
            raise EvalError(f"{path.name} passa de 24 MB: grave frases mais curtas.")
        pcm, rate = wav_to_pcm(path.read_bytes())
    except (OSError, VoiceError) as exc:
        raise EvalError(f"Não consegui ler {path.name}: precisa ser WAV mono de 16 bits.") from exc
    return resample(pcm, rate, SAMPLE_RATE)


# --- the run ---------------------------------------------------------------------------------


@dataclass
class CaseResult:
    name: str
    reference: str
    heard: str
    ms: float
    error: str | None = None  # the typed error code, when the call failed
    dropped: bool = False  # the filter took it for a Whisper hallucination (it would be dropped)
    words: Score = field(default_factory=Score)
    chars: Score = field(default_factory=Score)
    terms: Score = field(default_factory=Score)

    @property
    def failed(self) -> bool:
        return self.error is not None


@dataclass
class Report:
    results: list[CaseResult]
    terms: list[str]

    def _sum(self, attr: str) -> Score:
        total = Score()
        for result in self.results:
            total = total + getattr(result, attr)
        return total

    @property
    def words(self) -> Score:
        return self._sum("words")

    @property
    def chars(self) -> Score:
        return self._sum("chars")

    @property
    def term_misses(self) -> Score:
        return self._sum("terms")

    @property
    def failures(self) -> int:
        return sum(1 for r in self.results if r.failed)

    @property
    def dropped(self) -> int:
        return sum(1 for r in self.results if r.dropped)

    def latency(self) -> dict[str, float | None]:
        times = [r.ms for r in self.results if not r.failed]
        return {
            "mean_ms": sum(times) / len(times) if times else None,
            "p50_ms": percentile(times, 50),
            "p95_ms": percentile(times, 95),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "phrases": len(self.results),
            "wer": self.words.rate,
            "word_errors": self.words.errors,
            "words": self.words.total,
            "cer": self.chars.rate,
            "term_miss_rate": self.term_misses.rate,
            "terms_said": self.term_misses.total,
            "failures": self.failures,
            "dropped": self.dropped,
            "latency": self.latency(),
            "results": [
                {
                    "name": r.name,
                    "reference": r.reference,
                    "heard": r.heard,
                    "ms": round(r.ms, 1),
                    "error": r.error,
                    "dropped": r.dropped,
                    "word_errors": r.words.errors,
                    "words": r.words.total,
                }
                for r in self.results
            ],
        }


async def evaluate(
    transcriber: Transcriber,
    cases: Sequence[Case],
    *,
    terms: Sequence[str] = (),
    pause: float = 0.0,
    clock: Callable[[], float] = time.perf_counter,
    audio: Callable[[Path], bytes] = read_audio,
) -> Report:
    """Transcribe every case, one at a time, and score it.

    ``pause`` seconds between calls keep a free-tier rate limit happy. A typed `VoiceError` is a
    failed phrase (it scores as everything missed), never an exception: one bad call must not lose
    the rest of a long run, and it is scored as nothing at all (not as everything wrong). A reply
    the hallucination filter would drop counts as heard nothing, which is what the voice session
    does with it.
    """
    results: list[CaseResult] = []
    for index, case in enumerate(cases):
        if index and pause:
            await asyncio.sleep(pause)
        pcm = audio(case.audio)
        started = clock()
        error: str | None = None
        text = ""
        try:
            text = await transcriber.transcribe(pcm, SAMPLE_RATE)
        except VoiceError as exc:
            error = exc.code
        elapsed_ms = (clock() - started) * 1000
        dropped = error is None and is_hallucination(text)
        heard = "" if dropped or error else text
        results.append(
            CaseResult(
                name=case.name,
                reference=case.reference,
                heard=heard,
                ms=elapsed_ms,
                error=error,
                dropped=dropped,
                words=Score() if error else word_errors(case.reference, heard),
                chars=Score() if error else char_errors(case.reference, heard),
                terms=Score() if error else term_hits(case.reference, heard, terms),
            )
        )
    return Report(results, list(terms))


# --- the text report -------------------------------------------------------------------------


def _pct(score: Score) -> str:
    return "n/d" if score.rate is None else f"{score.rate * 100:.1f}%"


def _ms(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.0f} ms"


def render(report: Report) -> str:
    """The report as plain text: the worst phrases first, then the totals."""
    lines = ["Frases com erro (as piores primeiro):"]
    wrong = sorted(
        (r for r in report.results if r.words.errors or r.failed),
        key=lambda r: r.words.rate or 0.0,
        reverse=True,
    )
    if not wrong:
        lines.append("  nenhuma")
    for r in wrong:
        if r.failed:
            lines.append(f"  {r.name}: falhou ({r.error})")
            continue
        note = " [descartada como alucinação]" if r.dropped else ""
        lines.append(f"  {r.name}: {_pct(r.words)}{note}")
        lines.append(f"    disse:   {r.reference}")
        lines.append(f"    ouviu:   {r.heard}")
    latency = report.latency()
    lines += [
        "",
        f"Frases: {len(report.results)}  ·  falhas: {report.failures}  ·  "
        f"descartadas: {report.dropped}",
        *(
            [
                f"ATENÇÃO: {report.failures} frase(s) falharam e ficaram fora do WER, do CER e "
                "dos termos; refaça a rodada antes de usar estes números."
            ]
            if report.failures
            else []
        ),
        f"WER: {_pct(report.words)} ({report.words.errors} erros em {report.words.total} palavras)",
        f"CER: {_pct(report.chars)}",
        f"Termos próprios errados: {_pct(report.term_misses)} "
        f"({report.term_misses.errors} de {report.term_misses.total} ditos)",
        f"Latência da transcrição: média {_ms(latency['mean_ms'])} · "
        f"mediana {_ms(latency['p50_ms'])} · p95 {_ms(latency['p95_ms'])}",
    ]
    return "\n".join(lines)
