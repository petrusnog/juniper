"""Tests for the conversational yes/no parser (the system, not the LLM, decides)."""

from __future__ import annotations

import pytest

from brain.core.consent import interpret_reply


@pytest.mark.parametrize(
    "reply",
    [
        "Sim",
        "sim.",
        "SIM!",
        "Sim, pode.",
        "pode",
        "Pode sim",
        "pode apagar",
        "Pode apagar todos",
        "claro",
        "Claro que sim",
        "isso",
        "Isso mesmo",
        "confirmo",
        "Confirmo, pode prosseguir com a exclusão de todos",
        "manda ver",
        "pode ir",
        "ok",
        "beleza",
        "com certeza",
        "tá",
        "uhum",
        "s",
        "yes",
        "apaga",
        "  sim  ",
    ],
)
def test_clear_yes(reply: str) -> None:
    assert interpret_reply(reply) == "yes"


@pytest.mark.parametrize(
    "reply",
    ["não", "Não.", "nao", "n", "melhor não", "não, deixa", "Nem pensar", "negativo", "esquece"],
)
def test_clear_no(reply: str) -> None:
    assert interpret_reply(reply) == "no"


@pytest.mark.parametrize(
    "reply",
    [
        "sim, mas só o primeiro",  # partial: not a yes to the whole
        "sim mas espera",
        "pode apagar só os dois primeiros",
        "apenas o da tarde",
        "quais são mesmo?",
        "sim?",  # a question is not a confirmation
        "espera, deixa eu ver",
        "pode deixar",  # ambiguous in pt-BR: never executes
        "talvez",
        "depois",
        "outro dia",
        "me mostra de novo",
        "o dentista é amanhã",
        "",
        "   ",
        "...",
        "sim pode apagar tudo isso aí que eu confirmo sem problema nenhum",  # too long
    ],
)
def test_anything_doubtful_is_other(reply: str) -> None:
    assert interpret_reply(reply) == "other"


def test_negation_wins_over_an_affirmative_start() -> None:
    assert interpret_reply("sim não") == "no"
    assert interpret_reply("pode não") == "no"
    assert interpret_reply("não pode apagar") == "no"


def test_accents_and_case_are_ignored() -> None:
    assert interpret_reply("TÁ") == "yes"
    assert interpret_reply("NÃO") == "no"
    assert interpret_reply("Está bem?") == "other"


def test_words_that_merely_contain_a_yes_token_are_not_a_yes() -> None:
    for reply in ("simples", "claramente", "okupa", "podcast", "sinto muito", "nada"):
        assert interpret_reply(reply) == "other"
