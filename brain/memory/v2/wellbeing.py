"""Whether the user seems to be going through something hard, from what he just wrote.

A plain, conservative heuristic (ADR-017, principle 9: comfort comes before self-expression). It
only ever *holds things back* (no verification question, no mood talk, no interests); a false
positive costs a skipped remark, a false negative is covered by the persona core, which tells her
to welcome first whatever the blocks say.
"""

from __future__ import annotations

import re
import unicodedata

_SIGNS = re.compile(
    r"\b("
    r"estou mal|to mal|tô mal|me sinto mal|estou pessimo|to pessimo|"
    r"triste|tristeza|deprimid|depressao|ansios|ansiedade|angustia|desesper|"
    r"nao aguento|nao consigo mais|sem forcas|sem esperanca|sem vontade de nada|"
    r"chorei|chorando|vontade de chorar|estou arrasad|me sinto sozinh|"
    r"esgotad|exaust|burnout|estou sofrendo|sofrendo muito|"
    r"perdi (meu|minha|o|a) (pai|mae|avo|avo|irmao|irma|amigo|amiga|emprego)|faleceu|morreu"
    r")"
)


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in folded if not unicodedata.combining(c))


def seems_down(text: str | None) -> bool:
    """True when ``text`` reads like the user is hurting, tired of everything or grieving."""
    return bool(text) and _SIGNS.search(_plain(text or "")) is not None
