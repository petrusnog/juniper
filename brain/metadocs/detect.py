"""Is this message a question about the Juniper herself? Keyword rules, no LLM (ADR-025).

Cheap on purpose: it only decides whether to *offer* excerpts of the documentation for this
turn. A miss costs nothing (the ``docs_search`` tool is still there); a false positive is
caught by the second gate in ``brain.metadocs.context`` (the best section must score high
enough), and even then it only adds a few hundred tokens of data.
"""

from __future__ import annotations

import re

from brain.metadocs.index import fold

# Words that, in a question, can only be about the Juniper's own workings.
_STRONG = re.compile(
    r"\b(juniper|arquitetura|dispatcher|router|roteador|adrs?|websocket|protocolo|failover|"
    r"embeddings?|executor(es)?|tiers?|cerebro|brain|documentacao|memoria v2|mongodb|"
    r"dashboard|consent|registry|algoritmo de sonho|passada noturna|persona)\b"
)

# Addressing her ("você", "sua memória", "te").
_SELF = re.compile(r"\b(voce|vc|tu|te|teu|tua|seu|sua|seus|suas|contigo|si mesma)\b")

# What she is made of or what she does, in plain words.
_TOPIC = re.compile(
    r"\b(funciona\w*|arquitetura|memoria\w*|lembra\w*|esquec\w*|ferramenta\w*|tools?|llms?|"
    r"modelos?|provedor\w*|confirma\w*|permiss\w*|seguranca|sonh\w*|crenca\w*|opini\w*|"
    r"sentiment\w*|emoc\w*|personalidade|protocolo|servidor|deploy|docker|banco de dados|"
    r"painel|telegram|aplicativo|app|voz|configura\w*|codigo|constru\w*|feita|programad\w*|"
    r"implementad\w*|camadas?|clientes?|privacidade|dados|integra\w*|capaz|recursos|"
    r"funcionalidade\w*|limites|aprende\w*|pensa|decide|guarda\w*|armazena\w*|estuda\w*|"
    r"interesses?|token\w*|senha\w*|chaves?|pergunt\w*)\b"
)

_QUESTION = re.compile(
    r"\?|^\s*(como|o que|oque|qual|quais|por que|porque|pq|onde|quando|quem|quanto\w*|"
    r"explica\w*|me explica|me conta|me fala|conta|descreve|fala sobre|o que e|e verdade)\b"
)


def about_self(text: str) -> bool:
    """True when ``text`` reads like a question about the Juniper's own functioning."""
    folded = fold(text)
    if not _QUESTION.search(folded):
        return False
    if _STRONG.search(folded):
        return True
    return bool(_SELF.search(folded) and _TOPIC.search(folded))
