"""Fixing what the STT heard with glossary terms, and only with them (T-206, ADR-034 section 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from brain.clients import protocol as p
from brain.voice.correction import correct, should_ask, sound_key
from brain.voice.glossary import Glossary
from brain.voice.stt import SpeechToText, Transcriber, Transcription
from tests.test_agent import FakeProvider, say
from tests.test_voice_session import LONG, Rig, collect, say_phrase

TERMS = ["Media Works", "Petrus", "Fortaleza"]

# --- the corrector ---------------------------------------------------------------------------


def test_the_example_of_the_feature_request() -> None:
    fixed = correct("Juniper, abra o projeto mídia works.", TERMS, 0.9)
    assert fixed.text == "Juniper, abra o projeto Media Works."
    assert fixed.changed and fixed.original == "Juniper, abra o projeto mídia works."
    assert [(f.heard, f.term) for f in fixed.fixes] == [("mídia works", "Media Works")]


def test_words_run_together_or_split_apart_still_find_the_term() -> None:
    assert correct("abra o mediaworks", TERMS, 0.9).text == "abra o Media Works"
    assert correct("abra o me dia works agora", TERMS, 0.9).text == "abra o Media Works agora"


def test_the_right_words_in_the_wrong_case_get_the_term_spelling() -> None:
    assert correct("abra o media works", TERMS, 0.9).text == "abra o Media Works"


def test_an_exact_match_changes_nothing_and_reports_no_fix() -> None:
    fixed = correct("abra o projeto Media Works", TERMS, 0.9)
    assert not fixed.changed and fixed.fixes == ()


def test_text_with_nothing_like_a_term_is_left_alone() -> None:
    for text in ["que horas são", "toca Djavan", "", "   "]:
        assert not correct(text, TERMS, 0.1).changed


def test_without_terms_there_is_nothing_to_do() -> None:
    assert not correct("mídia works", [], 0.1).changed


def test_numbers_are_never_touched() -> None:
    assert not correct("marca dentista dia 14 às 15", ["Dia 14", "Dentista 15"], 0.1).changed
    # a term that carries a number cannot be matched by a stretch that has none
    assert not correct("marca dentista", ["Dentista 15"], 0.1).changed
    assert correct("abra o projeto 2024 mídia works", TERMS, 0.9).text == (
        "abra o projeto 2024 Media Works"
    )


def test_a_short_term_only_matches_the_same_sound() -> None:
    assert not correct("o ano passado", ["Ana"], 0.1).changed  # "ano" is a word, not "Ana"
    assert correct("a ana chegou", ["Ana"], 0.1).text == "a Ana chegou"


def test_a_looser_match_is_only_taken_when_the_engine_was_unsure() -> None:
    # "contato" sounds a bit like "Contabo", but it is also a perfectly good word
    assert not correct("mande o contato", ["Contabo"], 0.95).changed
    assert not correct("mande o contato", ["Contabo"], None).changed  # unknown counts as sure
    assert correct("mande o contato", ["Contabo"], 0.4).text == "mande o Contabo"


def test_two_terms_that_fit_equally_mean_no_choice_is_made() -> None:
    assert not correct("abra o projeto midia works", ["Media Works", "Mídia Wurks"], 0.9).changed


def test_an_exact_term_wins_over_a_similar_one() -> None:
    assert not correct("fala com Marta Silva", ["Marta Silva", "Marca Silva"], 0.9).changed
    assert correct("fala com marta silva", ["Marta Silva", "Marca Silva"], 0.9).text == (
        "fala com Marta Silva"
    )


def test_two_fixes_in_one_phrase_and_overlaps_go_to_the_better_one() -> None:
    fixed = correct("o petrus abriu o mídia works em fortalesa", TERMS, 0.9)
    assert fixed.text == "o Petrus abriu o Media Works em Fortaleza"
    assert len(fixed.fixes) == 3


def test_the_only_new_text_is_a_term() -> None:
    """Whatever it does, every inserted piece is a glossary term; the rest is what was heard."""
    phrases = [
        "abra o projeto mídia works agora",
        "o petrus disse que a fortalesa está quente",
        "manda um zap pra ana sobre o mediaworks",
        "toca uma música do djavan",
    ]
    for phrase in phrases:
        fixed = correct(phrase, TERMS + ["Ana"], 0.5)
        rebuilt = fixed.text
        for fix in fixed.fixes:
            assert fix.term in TERMS + ["Ana"]
            rebuilt = rebuilt.replace(fix.term, fix.heard, 1)
        assert rebuilt == phrase


@pytest.mark.parametrize(
    ("a", "b"),
    [("ph", "f"), ("kamera", "camera"), ("Yuri", "iuri"), ("Hugo", "ugo"), ("Pessoa", "pesoa")],
)
def test_spellings_that_sound_alike_share_a_key(a: str, b: str) -> None:
    assert sound_key(a) == sound_key(b)


# --- asking to repeat ------------------------------------------------------------------------


def test_very_low_confidence_asks_but_never_for_one_word_or_unknown_confidence() -> None:
    assert should_ask("abra o negócio lá", 0.1, 0.3)
    assert not should_ask("abra o negócio lá", 0.5, 0.3)
    assert not should_ask("sim", 0.05, 0.3)  # "sim" and "não" are short and often low-scored
    assert not should_ask("abra o negócio lá", None, 0.3)
    assert not should_ask("abra o negócio lá", 0.01, 0.0)  # 0 turns the question off


# --- in a voice session ----------------------------------------------------------------------


class Engine(Transcriber):
    def __init__(self, text: str, confidence: float | None) -> None:
        self.answer = Transcription(text, confidence=confidence)

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        raise NotImplementedError

    async def transcribe_detailed(
        self, pcm: bytes, sample_rate: int, prompt: str = ""
    ) -> Transcription:
        return self.answer


async def phrase(
    tmp_path: Path,
    text: str,
    confidence: float | None,
    *,
    provider: FakeProvider | None = None,
    configure: Any = None,
) -> tuple[Rig, FakeProvider, list[p.ServerMessage]]:
    provider = provider or FakeProvider([say(LONG)])
    async with Rig(tmp_path, provider) as rig:
        rig.services.stt = SpeechToText(Engine(text, confidence))
        rig.services.glossary = Glossary(["Media Works"])
        if configure is not None:
            configure(rig.services)
        client, _ = await rig.open()
        await say_phrase(client)
        kinds: tuple[type[Any], ...] = (p.VoiceTranscript, p.ChatEnd)
        if text and confidence is not None and confidence < 0.3 and "negócio" in text:
            kinds = (p.VoiceAudio,)  # asked to repeat: no transcript, no turn, just her voice
        messages = await collect(client, *kinds)
    return rig, provider, messages


async def test_the_turn_gets_the_corrected_text_and_the_client_both(tmp_path: Path) -> None:
    rig, provider, messages = await phrase(tmp_path, "abra o projeto mídia works", 0.9)
    transcript = next(m for m in messages if isinstance(m, p.VoiceTranscript))
    assert transcript.text == "abra o projeto Media Works"
    assert transcript.original == "abra o projeto mídia works"
    asked = next(m.content for m in provider.seen[0] if m.role == "user")
    assert asked is not None and "Media Works" in asked and "mídia" not in asked


async def test_nothing_corrected_means_no_original_in_the_message(tmp_path: Path) -> None:
    _, _, messages = await phrase(tmp_path, "que horas são", 0.9)
    transcript = next(m for m in messages if isinstance(m, p.VoiceTranscript))
    assert transcript.text == "que horas são" and transcript.original is None


async def test_correction_can_be_turned_off(tmp_path: Path) -> None:
    def off(services: Any) -> None:
        services.correct_terms = False

    _, provider, messages = await phrase(tmp_path, "abra o projeto mídia works", 0.9, configure=off)
    transcript = next(m for m in messages if isinstance(m, p.VoiceTranscript))
    assert transcript.text == "abra o projeto mídia works" and transcript.original is None


async def test_unsure_and_unexplained_she_asks_instead_of_guessing(tmp_path: Path) -> None:
    rig, provider, messages = await phrase(tmp_path, "abra o negócio lá", 0.1)
    assert provider.seen == []  # no turn was opened
    assert not [m for m in messages if isinstance(m, p.VoiceTranscript)]
    assert rig.synth.spoken == ["Não tenho certeza se entendi direito. Pode repetir?"]


async def test_unsure_but_explained_by_a_term_the_turn_goes_on(tmp_path: Path) -> None:
    _, provider, messages = await phrase(tmp_path, "abra o projeto mídia works", 0.2)
    assert provider.seen  # corrected, so it was not a guess
    transcript = next(m for m in messages if isinstance(m, p.VoiceTranscript))
    assert transcript.text == "abra o projeto Media Works"


async def test_the_log_counts_corrections_and_only_keeps_the_words_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.clients import voice_session

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(voice_session.event_log, "log", lambda event, **kw: seen.append(kw))
    await phrase(tmp_path / "a", "abra o projeto mídia works", 0.9)
    fix = next(kw for kw in seen if kw.get("action") == "correction")
    assert fix["fixes"] == 1 and "mídia" not in str(seen) and "original" not in fix

    seen.clear()

    def debug(services: Any) -> None:
        services.debug_transcripts = True

    await phrase(tmp_path / "b", "abra o projeto mídia works", 0.9, configure=debug)
    fix = next(kw for kw in seen if kw.get("action") == "correction")
    assert fix["original"] == "abra o projeto mídia works"
    assert fix["corrected"] == "abra o projeto Media Works"


def test_a_confirmation_or_a_command_is_never_rewritten_by_a_term() -> None:
    """A term that sounds like "sim" or "não" must not break an answer to a question (ADR-010)."""
    assert not correct("sim", ["Sym", "Shim"], 0.9).changed
    assert not correct("não", ["Naoh"], 0.9).changed
    assert not correct("pare", ["Pari"], 0.9).changed
    assert not correct("apague tudo", ["Apague Tudo Já"], 0.2).changed


def test_a_phrase_without_protected_words_is_still_corrected_next_to_them() -> None:
    assert correct("sim abra o projeto mídia works", ["Media Works"], 0.9).text == (
        "sim abra o projeto Media Works"
    )
