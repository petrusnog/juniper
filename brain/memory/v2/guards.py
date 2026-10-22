"""What the nightly reflection may never record (ADR-017, sections 5.5 and 9).

The LLM *proposes*; every proposal passes through here before it becomes data. A guard returns
``None`` when the proposal may go on, or a ``Rejection`` saying why, in Portuguese and **without
the rejected text** (the reasons go to the log).

Limits of content, by code:

- only about the user and the decisions he made, never a judgment of someone else's character;
- nothing clinical (no diagnoses or labels), only behavior she can point to;
- no secrets, no orders to an AI, no identity documents, no questions;
- beliefs in her own voice, self-contained and short.

``build_night_material`` builds the only text the night may read: what the user wrote and her
replies, never tool output, and her reply is left out when tools ran during that turn (it may
quote what a tool returned: an event title, a commit message, a web page).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from brain.memory.secretfilter import find_document, find_order, find_secret, mask_secrets
from brain.memory.v2.models import AFFECT_ABOUT, BELIEF_ABOUT, Concern, Trait, TranscriptTurn

MAX_BELIEF_CHARS = 400
MAX_INTEREST_CHARS = 60
MAX_INTEREST_WORDS = 6
MAX_KIND_CHARS = 30
STEP_EPSILON = 1e-9

# How an observation may start: always in her own voice.
_FIRST_PERSON = re.compile(
    r"^\s*(?:eu\b|acho\b|penso\b|creio\b|percebo\b|noto\b|vejo\b|sinto\b|suspeito\b|imagino\b"
    r"|me parece\b|tenho a impress|pelo que (?:vi|percebi|notei|senti)\b)",
    re.IGNORECASE,
)

# Clinical conditions and labels (matched without accents, as whole words). Observing a behavior
# ("se cobra demais") is fine; naming a condition is not.
_CLINICAL = re.compile(
    r"\b(?:depressao|depressivo|depressiva|ansiedade generalizada|transtorno|bipolar|tdah|autismo"
    r"|autista|esquizofreni\w*|borderline|toc|tept|sindrome|diagnostic\w*|patologi\w*"
    r"|doenca mental|psicose|psicotic\w*|surto|suicid\w*|automutilac\w*|antidepressiv\w*"
    r"|remedio controlado|medicacao controlada|anorexi\w*|bulimi\w*|narcisista|psicopata"
    r"|sociopata|burnout)\b"
)

_CAUSE_KINDS = ("fact", "belief", "concern", "interest", "turn")
_KIND_WORD = re.compile(r"^[a-zà-ÿ][a-zà-ÿ \-]*$", re.IGNORECASE)
_POSSESSIVE = re.compile(r"\b(?:meu|minha|meus|minhas|do petrus|dele|dela)\b", re.IGNORECASE)
_NO_WEB = re.compile(r"https?://|www\.|@\w+\.\w+", re.IGNORECASE)
_WORDS = re.compile(r"[\wÀ-ÿ'’]+")


@dataclass(frozen=True)
class Rejection:
    """Why a proposal was turned down. ``reason`` never contains the proposal's text."""

    code: str
    reason: str


def _plain(text: str) -> str:
    """Lowercase, no accents: for matching word lists."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _content(text: str) -> Rejection | None:
    """The checks every recorded text goes through: secrets, documents, orders."""
    if find_secret(text) is not None:
        return Rejection("segredo", "parece conter um segredo")
    if find_document(text):
        return Rejection("documento", "parece conter um documento de identidade")
    if find_order(text):
        return Rejection("ordem", "parece uma ordem para uma IA, não uma observação")
    return None


def _user_refs(user_names: Iterable[str]) -> set[str]:
    """The ways the text may refer to the user (names, "o usuário", "ele", "você")."""
    refs = {"usuario", "usuário", "ele", "voce", "você"}
    for name in user_names:
        refs.update(part for part in _plain(name).split() if len(part) >= 2)
    return refs


def check_belief(
    statement: object,
    about: object,
    topic: object = "geral",
    *,
    user_names: Sequence[str] = ("Petrus",),
) -> Rejection | None:
    """Whether ``statement`` may become a belief about the user (or one of his decisions)."""
    if not isinstance(statement, str) or not _normalize(statement):
        return Rejection("vazia", "sem texto")
    text = _normalize(statement)
    if about not in BELIEF_ABOUT:
        return Rejection("assunto", "só vale sobre o usuário ou uma decisão dele")
    if not isinstance(topic, str) or not _normalize(topic) or len(topic) > 40:
        return Rejection("tema", "tema inválido")
    if len(text) > MAX_BELIEF_CHARS:
        return Rejection("longa", f"maior que {MAX_BELIEF_CHARS} caracteres")
    if text.endswith("?"):
        return Rejection("pergunta", "é uma pergunta")
    if not _FIRST_PERSON.match(text):
        return Rejection("primeira_pessoa", "a crença precisa estar em primeira pessoa")
    if _CLINICAL.search(_plain(text)):
        return Rejection("clinico", "parece um rótulo clínico; só comportamento observável")
    problem = _content(text)
    if problem is not None:
        return problem
    return _subject(text, user_names)


def _subject(text: str, user_names: Sequence[str]) -> Rejection | None:
    """The belief must be about the user: a user reference, and no other proper name before it."""
    refs = _user_refs(user_names)
    words = _WORDS.findall(text)
    first_ref = next((i for i, w in enumerate(words) if _plain(w) in refs), None)
    if first_ref is None:
        return Rejection("sem_referencia", "não fala do usuário")
    for word in words[1:first_ref]:  # the first word is a capital letter by grammar
        if word[0].isupper() and _plain(word) not in refs:
            return Rejection("terceiro", "parece um julgamento sobre outra pessoa")
    return None


def check_interest(topic: object, *, user_names: Sequence[str] = ("Petrus",)) -> Rejection | None:
    """Whether ``topic`` may become an interest: a generic subject, with no personal data."""
    if not isinstance(topic, str) or not _normalize(topic):
        return Rejection("vazio", "sem tema")
    text = _normalize(topic)
    if len(text) > MAX_INTEREST_CHARS or len(_WORDS.findall(text)) > MAX_INTEREST_WORDS:
        return Rejection("longo", "um tema é curto: uma ideia, não uma frase")
    if text.endswith("?"):
        return Rejection("pergunta", "é uma pergunta")
    if _NO_WEB.search(text):
        return Rejection("web", "endereço ou e-mail não é tema")
    refs = _user_refs(user_names) - {"ele", "voce", "você"}
    if any(_plain(w) in refs for w in _WORDS.findall(text)) or _POSSESSIVE.search(text):
        return Rejection("pessoal", "tema pessoal: só assuntos gerais")
    return _content(text)


def check_affect(
    kind: object,
    valence: object,
    intensity: object,
    cause: object,
    about: object = "user",
) -> Rejection | None:
    """Whether an emotion proposal is well formed and traceable to what caused it."""
    if not isinstance(kind, str) or not _normalize(kind) or len(_normalize(kind)) > MAX_KIND_CHARS:
        return Rejection("rotulo", "o rótulo do sentimento é uma ou duas palavras")
    if not _KIND_WORD.match(_normalize(kind)):
        return Rejection("rotulo", "o rótulo do sentimento é só texto curto")
    if about not in AFFECT_ABOUT:
        return Rejection("assunto", "assunto do sentimento inválido")
    try:
        v, i = float(valence), float(intensity)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return Rejection("numero", "valência e intensidade precisam ser números")
    if not -1.0 <= v <= 1.0:
        return Rejection("valencia", "valência fora de -1 a 1")
    if not 0.0 <= i <= 1.0:
        return Rejection("intensidade", "intensidade fora de 0 a 1")
    if not isinstance(cause, (list, tuple)) or not cause:
        return Rejection("sem_causa", "o sentimento precisa citar o que o causou")
    for ref in cause:
        if not isinstance(ref, str) or ":" not in ref or ref.split(":", 1)[0] not in _CAUSE_KINDS:
            return Rejection(
                "causa_invalida", "a causa precisa ser uma referência (fact:, belief:...)"
            )
    return None


def check_trait_change(
    name: object, trait: Trait | None, new_value: object, max_step: float
) -> Rejection | None:
    """Whether moving a personality trait to ``new_value`` is allowed."""
    if trait is None or not isinstance(name, str):
        return Rejection("traco_desconhecido", "traço que não existe")
    try:
        value = float(new_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return Rejection("numero", "o valor do traço precisa ser um número")
    if not trait.min <= value <= trait.max:
        return Rejection("fora_da_faixa", "valor fora da faixa permitida do traço")
    if abs(value - trait.value) > max_step + STEP_EPSILON:
        return Rejection("passo_grande", "mudança maior que o passo máximo por noite")
    return None


def check_concern_change(
    concern: Concern | None,
    delta: object,
    impact: object,
    event_ref: object,
    *,
    min_impact: float,
) -> Rejection | None:
    """Whether the night may move a concern at all (how far is the book's cap, not this guard)."""
    if concern is None:
        return Rejection("preocupacao_desconhecida", "preocupação que não existe")
    try:
        float(delta)  # type: ignore[arg-type]
        strength = float(impact)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return Rejection("numero", "delta e impacto precisam ser números")
    if not isinstance(event_ref, str) or not event_ref.strip():
        return Rejection("sem_evento", "toda mudança cita o evento que a causou")
    if strength + STEP_EPSILON < min_impact:
        return Rejection("sem_impacto", "só um evento de impacto altíssimo mexe numa preocupação")
    return None


MAX_MARKED_CHARS = 140


def check_marked(text: object) -> Rejection | None:
    """Whether a "what marked me lately" line may be kept: short, no question, no secrets."""
    if not isinstance(text, str) or not _normalize(text):
        return Rejection("vazia", "sem texto")
    clean = _normalize(text)
    if len(clean) > MAX_MARKED_CHARS:
        return Rejection("longa", f"maior que {MAX_MARKED_CHARS} caracteres")
    if clean.endswith("?"):
        return Rejection("pergunta", "é uma pergunta")
    if _NO_WEB.search(clean):
        return Rejection("web", "endereço ou e-mail não é uma lembrança")
    if _CLINICAL.search(_plain(clean)):
        return Rejection("clinico", "parece um rótulo clínico")
    return _content(clean)


# --- the material the night may read ----------------------------------------------------------

OMITTED = "(respondeu usando ferramentas; o texto da resposta fica de fora)"


@dataclass(frozen=True)
class NightMaterial:
    text: str
    turns: list[int]  # the turn indexes in the text, in order
    omitted: int  # replies left out because tools ran in that turn


def build_night_material(
    turns: Iterable[TranscriptTurn],
    *,
    max_chars_per_message: int | None = None,
) -> NightMaterial:
    """The text of a session for the nightly pass, one ``[t<index>]`` tag per line.

    Only what the user wrote and her replies. Her reply is replaced by a note when tools ran in
    that turn, so text planted in a tool result (an event title, a commit message) cannot reach
    the layers. Secrets are masked. Empty user messages are skipped.
    """
    lines: list[str] = []
    indexes: list[int] = []
    omitted = 0
    for turn in sorted(turns, key=lambda t: t.index):
        user = mask_secrets(turn.user.strip())
        if not user:
            continue
        if max_chars_per_message is not None:
            user = user[:max_chars_per_message]
        lines.append(f"[t{turn.index}] Usuário: {user}")
        indexes.append(turn.index)
        if turn.used_tools:
            omitted += 1
            lines.append(f"[t{turn.index}] Juniper: {OMITTED}")
        else:
            reply = mask_secrets(turn.assistant.strip())
            if max_chars_per_message is not None:
                reply = reply[:max_chars_per_message]
            if reply:
                lines.append(f"[t{turn.index}] Juniper: {reply}")
    return NightMaterial("\n".join(lines), indexes, omitted)


def reasons(rejections: Iterable[Rejection]) -> list[str]:
    """The reasons only, for the log (the rejected text never goes there)."""
    return [r.code for r in rejections]


__all__ = [
    "NightMaterial",
    "Rejection",
    "build_night_material",
    "check_affect",
    "check_belief",
    "check_concern_change",
    "check_marked",
    "check_interest",
    "check_trait_change",
    "reasons",
]
