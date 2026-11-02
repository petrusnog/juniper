"""Emotion and mood in the PAD space (ADR-030, sections 2 and 3).

Every emotion is a point on three axes from -1 to 1: **pleasure** (P), **arousal** (A) and
**dominance** (D) (Mehrabian). The model only names the feeling (``kind``) and says how much the
event helped or harmed a concern (the valence); this module turns that into a point: the
pleasure is the valence, arousal and dominance come from a fixed table of labels (OCC emotions
placed in PAD, after Gebhard's ALMA). An unknown label keeps its pleasure and sits neutral on the
other two axes.

The mood (ALMA) is where those emotions pushed her: each one pushes with ``intensity x
reactivity``, fading with the mood's half-life, and with nothing new she drifts back to her rest
point, which comes from her personality. In words, the mood is an octant of the space
(exuberante, relaxada, dócil, dependente, entediada, desdenhosa, ansiosa, hostil), with a degree.

Pure functions over numbers, so they are easy to test and to calibrate. The values are a starting
point **[a calibrar]**.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brain.memory.v2.models import PersonaState


@dataclass(frozen=True)
class PAD:
    p: float = 0.0
    a: float = 0.0
    d: float = 0.0

    def __add__(self, other: PAD) -> PAD:
        return PAD(self.p + other.p, self.a + other.a, self.d + other.d)

    def __sub__(self, other: PAD) -> PAD:
        return PAD(self.p - other.p, self.a - other.a, self.d - other.d)

    def scaled(self, factor: float) -> PAD:
        return PAD(self.p * factor, self.a * factor, self.d * factor)

    def clamped(self) -> PAD:
        return PAD(*(max(-1.0, min(1.0, x)) for x in (self.p, self.a, self.d)))

    def soft(self) -> PAD:
        """Squashed into -1..1 without a hard edge (many pushes saturate, never overflow)."""
        return PAD(math.tanh(self.p), math.tanh(self.a), math.tanh(self.d))

    @property
    def size(self) -> float:
        return math.sqrt(self.p**2 + self.a**2 + self.d**2)

    def rounded(self, digits: int = 3) -> PAD:
        return PAD(round(self.p, digits), round(self.a, digits), round(self.d, digits))

    def as_list(self) -> list[float]:
        return [self.p, self.a, self.d]


# (arousal, dominance) of each label; the pleasure is the valence the appraisal gave.
# Keys are stems of the folded label, so "irritada", "irritação" and "irritado" all match.
EMOTION_AD: dict[str, tuple[float, float]] = {
    "alegr": (0.2, 0.1),
    "feliz": (0.2, 0.1),
    "content": (0.0, 0.2),
    "satisf": (-0.2, 0.4),
    "orgulh": (0.3, 0.3),
    "gratid": (0.2, -0.3),
    "grata": (0.2, -0.3),
    "admir": (0.3, -0.2),
    "carinh": (0.1, 0.2),
    "afeto": (0.1, 0.2),
    "amor": (0.1, 0.2),
    "ternur": (0.0, 0.1),
    "esperan": (0.2, -0.1),
    "alivi": (-0.3, 0.4),
    "calm": (-0.5, 0.2),
    "seren": (-0.5, 0.2),
    "paz": (-0.5, 0.2),
    "empolg": (0.6, 0.3),
    "entusiasm": (0.6, 0.3),
    "animad": (0.5, 0.2),
    "euforia": (0.7, 0.3),
    "divert": (0.4, 0.2),
    "curios": (0.5, 0.0),
    "interesse": (0.4, 0.1),
    "fascin": (0.5, -0.1),
    "surpres": (0.6, -0.1),
    "espant": (0.6, -0.2),
    "tristez": (-0.2, -0.5),
    "triste": (-0.2, -0.5),
    "melancol": (-0.3, -0.4),
    "saudade": (-0.3, -0.2),
    "solid": (-0.3, -0.4),
    "decep": (0.1, -0.4),
    "desapont": (0.1, -0.4),
    "magoa": (-0.1, 0.1),
    "magoad": (-0.1, 0.1),
    "ressent": (0.2, 0.3),
    "irrit": (0.4, 0.2),
    "raiva": (0.6, 0.3),
    "furia": (0.8, 0.4),
    "indign": (0.5, 0.3),
    "frustr": (0.4, -0.2),
    "impacien": (0.5, 0.1),
    "desprez": (0.2, 0.5),
    "desdem": (-0.1, 0.5),
    "nojo": (0.3, 0.3),
    "medo": (0.6, -0.4),
    "susto": (0.7, -0.3),
    "preocup": (0.4, -0.3),
    "ansied": (0.6, -0.4),
    "ansios": (0.6, -0.4),
    "aflic": (0.5, -0.4),
    "nervos": (0.6, -0.3),
    "tens": (0.5, -0.2),
    "inseg": (0.3, -0.5),
    "vergonh": (0.1, -0.6),
    "culpa": (0.1, -0.6),
    "remors": (0.1, -0.6),
    "pena": (-0.2, -0.5),
    "compaix": (-0.1, -0.2),
    "tedio": (-0.6, -0.3),
    "entedi": (-0.6, -0.3),
    "cansa": (-0.6, -0.3),
    "apat": (-0.6, -0.4),
    "confus": (0.3, -0.4),
    "ciume": (0.4, -0.1),
    "inveja": (0.3, -0.2),
}

# The eight octants, in the words of the prompt (she is a woman: feminine forms).
OCTANTS: dict[tuple[int, int, int], str] = {
    (1, 1, 1): "exuberante",
    (1, -1, 1): "relaxada",
    (1, -1, -1): "dócil",
    (1, 1, -1): "dependente",
    (-1, -1, -1): "entediada",
    (-1, -1, 1): "desdenhosa",
    (-1, 1, -1): "ansiosa",
    (-1, 1, 1): "hostil",
}
# A mood closer than this to the rest point says nothing.
QUIET_SHIFT = 0.12


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(c for c in plain if not unicodedata.combining(c))


def emotion_pad(kind: str, valence: float) -> PAD:
    """The PAD point of a feeling: pleasure = valence; arousal and dominance from its label."""
    label = fold(kind)
    arousal, dominance = next((ad for stem, ad in EMOTION_AD.items() if stem in label), (0.0, 0.0))
    return PAD(float(valence), arousal, dominance).clamped()


def octant(point: PAD) -> str:
    """The octant word of a point (the sign of each axis; zero counts as positive)."""
    key = tuple(1 if x >= 0 else -1 for x in (point.p, point.a, point.d))
    return OCTANTS[key]  # type: ignore[index]


def octant_words(point: PAD, quiet: float = QUIET_SHIFT) -> str | None:
    """The octant with its degree ("um pouco ansiosa", "bem exuberante"), or ``None`` near zero."""
    size = point.size
    if size < quiet:
        return None
    degree = "um pouco " if size < 0.35 else "bem " if size >= 0.7 else ""
    return f"{degree}{octant(point)}"


@dataclass(frozen=True)
class Temperament:
    """What her personality does to her feelings: how much they move her and where she rests."""

    volatility: float = 0.5  # 0 (steady) to 1 (volatile)
    rest: PAD = PAD(0.1, 0.0, 0.0)

    @property
    def reactivity(self) -> float:
        """How hard an emotion pushes the mood (0.5 to 1.5)."""
        return 0.5 + self.volatility

    @property
    def lingering(self) -> float:
        """How much longer the mood takes to go back (0.5 to 1.5 times the half-life)."""
        return 0.5 + self.volatility


def _trait(state: PersonaState | None, *names: str, default: float = 0.5) -> float:
    if state is None:
        return default
    for name in names:
        trait = state.traits.get(name)
        if trait is not None:
            return float(trait.value)
    return default


def temperament(state: PersonaState | None) -> Temperament:
    """Her temperament from the personality (the Big Five aspects, or the V2 traits before the
    migration). Without a personality, the default."""
    enthusiasm = _trait(state, "entusiasmo", "brincadeira", default=0.6)
    withdrawal = _trait(state, "retraimento", default=0.3)
    assertiveness = _trait(state, "assertividade", "franqueza", default=0.6)
    volatility = _trait(state, "volatilidade", default=0.5)
    rest = PAD(
        0.1 + 0.4 * (enthusiasm - 0.5) - 0.4 * (withdrawal - 0.5),
        0.4 * (enthusiasm - 0.5),
        0.4 * (assertiveness - 0.5),
    ).clamped()
    return Temperament(max(0.0, min(1.0, volatility)), rest.rounded(4))
