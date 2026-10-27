"""The spoken commands recognised without the LLM (T-152, ADR-023 section 8)."""

from __future__ import annotations

import pytest

from brain.voice.commands import is_end_command, is_stop_command


@pytest.mark.parametrize(
    "text",
    [
        "Encerrar conversa",
        "encerrar a conversa.",
        "Pode parar!",
        "Tchau, Juniper.",
        "tchau Juniper",
        "ok, juniper, encerra a conversa por favor",
        "Até logo",
        "ate mais",
        "Sair do modo de voz",
        "fim da conversa",
    ],
)
def test_end_commands(text: str) -> None:
    assert is_end_command(text) and not is_stop_command(text)


@pytest.mark.parametrize(
    "text",
    [
        "pode parar de tocar essa música",
        "toca uma música e depois encerra a conversa",
        "o que significa tchau em japonês",
        "encerrar a conversa com o Marcos",
        "",
        "   ",
        "pare a música do Djavan",
        "parar",
    ],
)
def test_sentences_that_merely_contain_the_words_are_not_commands(text: str) -> None:
    assert not is_end_command(text)
    assert not is_stop_command(text)


@pytest.mark.parametrize(
    "text", ["Para!", "chega", "Silêncio.", "para de falar", "Juniper, fica quieta"]
)
def test_stop_commands(text: str) -> None:
    assert is_stop_command(text) and not is_end_command(text)
