"""Spoken commands the brain recognises by itself, without the LLM (ADR-023, section 8).

Ending the conversation or cutting her off must never depend on a model's mood, so these are an
exact match on the whole utterance (after dropping accents, punctuation and a few filler words):
"pode parar de tocar a música" is a request for the LLM, not an end command.
"""

from __future__ import annotations

from typing import Final

from brain.voice.stt import normalise

_FILLER: Final = frozenset({"juniper", "por", "favor", "agora", "entao", "ok", "bom", "ei", "oi"})

_END: Final = frozenset(
    {
        "encerrar conversa",
        "encerrar a conversa",
        "encerra a conversa",
        "encerra conversa",
        "terminar conversa",
        "terminar a conversa",
        "termina a conversa",
        "finalizar conversa",
        "finalizar a conversa",
        "finaliza a conversa",
        "fim da conversa",
        "pode parar",
        "pode encerrar",
        "pode terminar",
        "pode encerrar a conversa",
        "tchau",
        "ate logo",
        "ate mais",
        "ate amanha",
        "encerrar modo de voz",
        "sair do modo de voz",
        "desligar a voz",
    }
)

_STOP: Final = frozenset(
    {"para", "pare", "para de falar", "pare de falar", "chega", "silencio", "fica quieta", "calma"}
)


_LEAK: Final = frozenset(
    {
        "vazou",
        "vazamento",
        "isso vazou",
        "deu vazamento",
        "teve vazamento",
        "houve vazamento",
        "isso e um vazamento",
        "marcar vazamento",
        "marca vazamento",
        "marcar o vazamento",
        "marca o vazamento",
        "registrar vazamento",
        "registra vazamento",
        "registrar o vazamento",
        "registra o vazamento",
        "reportar vazamento",
        "reporta vazamento",
        "reportar o vazamento",
        "reporta o vazamento",
        "anota o vazamento",
        "anotar o vazamento",
    }
)


def _core(text: str) -> str:
    return " ".join(w for w in normalise(text).split() if w not in _FILLER)


def is_end_command(text: str) -> bool:
    """ "Encerrar conversa", "tchau Juniper"...: the voice session ends."""
    return _core(text) in _END


def is_stop_command(text: str) -> bool:
    """ "Para", "chega", "silêncio"...: cut what she is saying, keep the session."""
    return _core(text) in _STOP


def is_leak_command(text: str) -> bool:
    """ "Isso vazou", "marcar vazamento"...: report that her last answer leaked (T-167)."""
    return _core(text) in _LEAK
