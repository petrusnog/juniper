"""Understanding a spoken/typed "yes" or "no" to a pending request (pt-BR).

When a tool needs approval, the agent asks in plain words ("Posso apagar esses cinco
agendamentos? É irreversível.") and waits. The user's next message is interpreted HERE, by
the system, not by the LLM: only a clear, unqualified yes executes the parked action. The
parser is deliberately conservative; anything doubtful is "other", which cancels the pending
action and hands the message to the LLM as a normal turn (it can ask again if needed).

It works on text, so it serves a keyboard and a speech-to-text transcript alike.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

Verdict = Literal["yes", "no", "other"]

# A confirmation is short. A long answer is a new instruction, not a "yes".
MAX_WORDS = 8

_YES_START = re.compile(
    r"^(sim|s|claro|certo|ok|okay|beleza|blz|confirmo|confirmado|confirma|pode|podes|vai|manda|"
    r"mande|faz|faca|isso|exato|positivo|yes|y|uhum|aham|com certeza|combinado|fechado|tudo bem|"
    r"tudo certo|apague|apaga|exclua|exclui|delete|marca|marque|aprovado|autorizo|ta|tá)\b"
)
_NO = re.compile(
    r"\b(nao|n|nem|nunca|negativo|melhor nao|desisto|para|pare|esquece|esqueca|deixa pra la|"
    r"cancela a acao)\b"
)
# Words that turn a "yes" into a conditional/partial answer or a question.
_QUALIFIERS = re.compile(
    r"\b(mas|so|apenas|exceto|menos|porem|antes|espera|espere|calma|aguarda|aguarde|quando|talvez|"
    r"depois|outro|outra|outros|outras|qual|quais|quantos|quantas|quem|onde|por que|porque|"
    r"deixa|deixe|deixar|primeiro|primeira|ultimo|ultima|dois|duas|tres)\b"
)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def interpret_reply(text: str) -> Verdict:
    """Classify a reply to "can I do this?" as a clear yes, a clear no, or anything else."""
    words = _normalize(text)
    if not words or len(words.split()) > MAX_WORDS or "?" in text:
        return "other"
    if _NO.search(words):  # any negation: nothing runs (and it is clearly a "no", not a doubt)
        return "no"
    if _QUALIFIERS.search(words):
        return "other"
    if _YES_START.match(words):
        return "yes"
    return "other"


def mentions_all(text: str, subjects: list[str]) -> bool:
    """True if every subject (e.g. an event title) appears in ``text``, ignoring case/accents."""
    haystack = _normalize(text)
    wanted = [_normalize(s) for s in subjects]
    return bool(wanted) and all(w and w in haystack for w in wanted)
