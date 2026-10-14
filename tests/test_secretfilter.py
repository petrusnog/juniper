"""T-056: o filtro de segredos recusa o que parece segredo e deixa passar fatos comuns."""

from __future__ import annotations

import pytest

from brain.memory.secretfilter import MASK, find_secret, mask_secrets

SECRETS = [
    "minha senha é hunter2",
    "password: abc12345",
    "o token = ghp_abcdefghijklmnop1234",
    "use a chave gsk_abcdefghijklmnop1234",
    "sk-proj-abcdefghijklmnopqrstuv",
    "AKIAIOSFODNN7EXAMPLE",
    "cartão 4111 1111 1111 1111",
    "4111-1111-1111-1111",
    "postgres://usuario:segredo@host:5432/db",
    "Authorization: Bearer abcdefghijklmnop",
    "-----BEGIN RSA PRIVATE KEY-----",
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcd1234",
    "o cvv é 123",
]

HARMLESS = [
    "Prefere ZSH.",
    "O projeto X usa Laravel 11.",
    "Mora em São Paulo desde 2019.",
    "Odeia tabulação com espaço.",
    "O servidor roda na porta 8765.",
    "Usa o token de acesso do GitHub para fazer deploy.",
    "Guarda a chave de API do Groq no arquivo .env.",
    "Tem o telefone 4111 antigo",
    "Nasceu em 12/03/1990.",
]


@pytest.mark.parametrize("text", SECRETS)
def test_secrets_are_found(text: str) -> None:
    assert find_secret(text) is not None


@pytest.mark.parametrize("text", HARMLESS)
def test_ordinary_facts_pass(text: str) -> None:
    assert find_secret(text) is None


def test_card_numbers_need_a_valid_checksum() -> None:
    assert find_secret("pedido 1234 5678 9012 3456") is None
    assert find_secret("cartão 4111 1111 1111 1111") == "número de cartão"


@pytest.mark.parametrize("text", SECRETS)
def test_masking_removes_what_is_found(text: str) -> None:
    masked = mask_secrets(f"antes {text} depois")
    assert MASK in masked
    assert find_secret(masked) is None


def test_masking_keeps_the_rest_and_ordinary_text() -> None:
    assert mask_secrets("Prefere ZSH.") == "Prefere ZSH."
    out = mask_secrets("minha senha é hunter2 e eu uso ZSH")
    assert "hunter2" not in out and "uso ZSH" in out
