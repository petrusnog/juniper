"""Fixing what the STT heard wrong, without ever rewriting what was said (ADR-034, section 4).

"abra o projeto mídia works" -> "abra o projeto Media Works", because a project with that name is
in the glossary and the two sound alike. That is the *whole* power of this module, on purpose:

- It only ever puts a **glossary term** in place of a stretch of the heard words. It adds no other
  word, removes none, and never touches digits (so no number, date or amount can change).
- It is deterministic and local: no LLM, no network, a few microseconds. An LLM asked to "clean up"
  a transcript can invent a command; this cannot, because the only text it can insert is one the
  user wrote down (or a registered name) beforehand.
- Two terms that fit a stretch about equally well mean *nobody knows*: nothing is replaced.
- A close sound-alike (``strong``) is fixed whatever the engine's confidence, because Whisper is
  often confident about a wrong spelling; a looser one (``weak``) only when the confidence is low.

When the engine is very unsure and no term explains why, ``should_ask`` says to ask the person to
repeat instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from brain.voice.stt import normalise

_WORD: Final = re.compile(r"\w+", re.UNICODE)
# Words the corrector never touches and a learned term may never contain: the answers to a
# confirmation (ADR-010), the spoken commands, and verbs that act. A term made of them could turn
# "liste os eventos" into "apague os eventos", or break a "sim" (T-218).
_PROTECTED_TEXT: Final = """
sim nao ok claro pode certo confirmo confirma confirmar cancela cancele cancelar
nunca jamais pare parar para encerrar encerra encerre termine terminar tchau
apague apagar apaga exclua excluir exclui delete deletar remova remover remove
envie enviar manda mande desative desativar desliga desligue ative ativar
execute executar rode rodar instale instalar pague pagar transfira transferir
compre comprar senha
"""
PROTECTED_WORDS: Final = frozenset(_PROTECTED_TEXT.split())
MIN_FUZZY_CHARS: Final = 5  # shorter terms only match when they sound exactly the same
AMBIGUITY_GAP: Final = 0.04  # two terms this close for the same words: do not choose


@dataclass(frozen=True)
class Fix:
    heard: str
    term: str
    similarity: float


@dataclass(frozen=True)
class Corrected:
    text: str
    original: str
    fixes: tuple[Fix, ...] = ()

    @property
    def changed(self) -> bool:
        return self.text != self.original


def sound_key(text: str) -> str:
    """The text as it sounds, roughly, in pt-BR: no accents, case, spaces or doubled letters.

    Both sides of a comparison go through the same key, so it only has to make spellings that
    sound alike collapse together ("ph"/"f", "k"/"c", "y"/"i", a silent "h", "ss"/"s").
    """
    plain = normalise(text).replace(" ", "")
    plain = plain.replace("ph", "f").replace("k", "c").replace("y", "i").replace("h", "")
    return re.sub(r"(.)\1+", r"\1", plain)


def similarity(a: str, b: str) -> float:
    """1.0 for the same sound, lower as more letters differ (0..1)."""
    ka, kb = sound_key(a), sound_key(b)
    if not ka or not kb:
        return 0.0
    return 1.0 - _distance(ka, kb) / max(len(ka), len(kb))


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class _Match:
    start: int  # token index
    end: int  # token index, exclusive
    term: str
    score: float


def correct(
    text: str,
    terms: list[str],
    confidence: float | None,
    *,
    strong: float = 0.88,
    weak: float = 0.8,
    low_confidence: float = 0.8,
) -> Corrected:
    """``text`` with the stretches that sound like a glossary term replaced by that term.

    ``strong`` is the similarity that is enough on its own; ``weak`` also needs the engine's
    ``confidence`` to be below ``low_confidence`` (unknown confidence counts as high: no guess).
    """
    tokens = list(_WORD.finditer(text))
    if not tokens or not terms:
        return Corrected(text, text)
    chosen: list[_Match] = []
    taken = [False] * len(tokens)
    candidates: list[_Match] = []
    for term in terms:
        match = _best_match(tokens, term, terms, strong, weak, low_confidence, confidence)
        if match is not None:
            candidates.append(match)
    for match in sorted(candidates, key=lambda m: (-m.score, m.start)):
        if any(taken[match.start : match.end]):
            continue  # overlaps a better match
        for i in range(match.start, match.end):
            taken[i] = True
        chosen.append(match)
    if not chosen:
        return Corrected(text, text)
    pieces: list[str] = []
    fixes: list[Fix] = []
    cursor = 0
    for match in sorted(chosen, key=lambda m: m.start):
        first, last = tokens[match.start], tokens[match.end - 1]
        heard = text[first.start() : last.end()]
        pieces.append(text[cursor : first.start()])
        pieces.append(match.term)
        cursor = last.end()
        if heard != match.term:
            fixes.append(Fix(heard, match.term, round(match.score, 2)))
    pieces.append(text[cursor:])
    fixed = "".join(pieces)
    return Corrected(fixed, text, tuple(fixes)) if fixes else Corrected(text, text)


def _best_match(
    tokens: list[re.Match[str]],
    term: str,
    all_terms: list[str],
    strong: float,
    weak: float,
    low_confidence: float,
    confidence: float | None,
) -> _Match | None:
    size = len(term.split())
    key = sound_key(term)
    if not key:
        return None
    fuzzy = len(key) >= MIN_FUZZY_CHARS and not any(c.isdigit() for c in term)
    # a term with digits could put a number nobody said into the phrase: same sound or nothing
    need_weak = confidence is not None and confidence < low_confidence
    best: _Match | None = None
    for width in {max(1, size - 1), size, size + 1}:  # "mediaworks", "media works", "me dia works"
        for start in range(0, len(tokens) - width + 1):
            words = tokens[start : start + width]
            if any(any(c.isdigit() for c in w.group()) for w in words):
                continue  # a number is never "corrected"
            if any(normalise(w.group()) in PROTECTED_WORDS for w in words):
                continue  # nor a confirmation or a command
            heard = " ".join(w.group() for w in words)
            score = similarity(heard, term)
            if score < 1.0 and not fuzzy:
                continue
            if score < (weak if need_weak else strong):
                continue
            if best is None or score > best.score:
                best = _Match(start, start + width, term, score)
    if best is None:
        return None
    if best.score < 1.0:  # an exact sound is not a guess; anything less must beat the others
        heard = " ".join(t.group() for t in tokens[best.start : best.end])
        for other in all_terms:  # another term fits these words about as well: do not choose
            if other != term and similarity(heard, other) >= best.score - AMBIGUITY_GAP:
                return None
    return best


def should_ask(text: str, confidence: float | None, floor: float) -> bool:
    """True when the engine was so unsure that guessing would be worse than asking again.

    A single word is let through: "sim", "não" and the like are short, often low-scored and
    perfectly fine. ``floor`` 0 turns the question off.
    """
    if floor <= 0 or confidence is None:
        return False
    return confidence < floor and len(text.split()) > 1
