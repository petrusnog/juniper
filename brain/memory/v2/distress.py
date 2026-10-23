"""Whether the user seems to be going through something hard, from what he writes.

Plain, deterministic rules (ADR-017, principle 9: comfort comes before self-expression): grief,
loss, exhaustion, sadness, frustration, the need to vent. They only ever *hold things back* (no
verification question, no mood or interests, a nudge to welcome first); a false positive costs a
skipped remark, a false negative is covered by the persona core, which tells her to welcome first
whatever the blocks say.
"""

from __future__ import annotations

import re
import unicodedata

_SIGNS = re.compile(
    r"\b("
    # how he is
    r"estou mal|to mal|me sinto mal|estou pessimo|to pessimo|nao to bem|nao estou bem|"
    r"triste|tristeza|deprimid|depressao|ansios|ansiedade|angustia|desesper|"
    r"nao aguento|nao consigo mais|sem forcas|sem esperanca|sem vontade de nada|"
    r"chorei|chorando|vontade de chorar|estou arrasad|to arrasad|me sinto sozinh|"
    r"esgotad|exaust|burnout|estou sofrendo|sofrendo muito|"
    # tired and frustrated
    r"cansad[oa] demais|cansad[oa] de tudo|estou frustrad|to frustrad|que raiva|"
    r"cheio de tudo|de saco cheio|nada da certo|nada dá certo|"
    # venting
    r"preciso desabafar|quero desabafar|vou desabafar|"
    # grief and loss
    r"faleceu|morreu|velorio|enterro|luto|falecimento|"
    r"perdi (meu|minha|o|a) (pai|mae|avo|irmao|irma|amigo|amiga|emprego|filho|filha|cachorro|gato)|"
    r"fui demitid[oa]|me demitiram|terminamos|me deixou"
    r")"
)
# What the same words look like with the accents stripped (the text is folded before the match).
_SIGNS = re.compile(_SIGNS.pattern.replace("dá", "da"))


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in folded if not unicodedata.combining(c))


def seems_down(text: str | None) -> bool:
    """True when ``text`` reads like the user is hurting, grieving, exhausted or venting."""
    return bool(text) and _SIGNS.search(_plain(text or "")) is not None
