"""Keeps secrets out of the long-term memory (ADR-011, section 6).

``find_secret`` says why a text looks like a secret (API key, token, password, card number,
private key...), or ``None``. ``mask_secrets`` blanks the same patterns inside a larger text,
so a conversation can be sent to the extraction LLM without them.

This is a heuristic and errs on the side of refusing: a harmless memory may be turned down,
but a secret should not get in. It is not a guarantee: a password pasted into the chat is still
in the session file (ADR-011, consequences).
"""

from __future__ import annotations

import re

MASK = "[segredo removido]"

# (reason, pattern). The patterns are specific enough to avoid common words.
_TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("chave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "chave de API",
        re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|proj|ant|or)?[-_]?[A-Za-z0-9]{16,}"),
    ),
    (
        "chave de API",
        re.compile(
            r"\b(?:gsk|ghp|gho|ghs|ghu|github_pat|glpat|xox[abprs]|hf|AIza)[_-]?[A-Za-z0-9_-]{16,}"
        ),
    ),
    ("chave da AWS", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}")),
    ("token de acesso", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I)),
    ("credencial em URL", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    # A long unbroken run mixing letters and digits is almost always a token or a hash.
    (
        "sequência que parece token",
        re.compile(
            r"\b(?=[A-Za-z0-9_+/=-]*[A-Za-z])(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}\b"
        ),
    ),
]

# "senha: xyz", "password is xyz", "token=xyz": a secret word followed by a value.
_LABEL = re.compile(
    r"\b(?:senha|password|passwd|pwd|pass|token|secret|segredo|api[ _-]?key"
    r"|chave(?: de api| secreta| privada)?|credencial(?:is)?|pin|cvv|cvc)\b"
    r"(?:\s+\w+){0,3}(?:\s*[=:]\s*|\s+(?:é|eh|is|foi|será)\s+)[\"'`]?(?!\[segredo)(\S{3,})",
    re.I,
)
_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){13,19}(?![\d-])")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0


def _card_numbers(text: str) -> list[re.Match[str]]:
    found = []
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append(m)
    return found


def find_secret(text: str) -> str | None:
    """Why ``text`` looks like it holds a secret (in Portuguese), or ``None`` if it does not."""
    for reason, pattern in _TOKEN_PATTERNS:
        if pattern.search(text):
            return reason
    if _LABEL.search(text):
        return "senha ou credencial"
    if _card_numbers(text):
        return "número de cartão"
    return None


def mask_secrets(text: str) -> str:
    """``text`` with everything ``find_secret`` would flag replaced by a marker."""
    for _, pattern in _TOKEN_PATTERNS:
        text = pattern.sub(MASK, text)
    text = _LABEL.sub(lambda m: m.group().replace(m.group(1), MASK), text)
    for m in reversed(_card_numbers(text)):
        text = text[: m.start()] + MASK + text[m.end() :]
    return text
