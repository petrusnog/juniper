"""One search box over everything the dashboard shows (ADR-020).

Each kind of thing is searched two ways: by words (every word of the query appears) and by
meaning (embeddings, when the model answers). A match by words always ranks above one by meaning.
If the embedding model is missing or fails, the search falls back to words and says so.
Everything here only reads.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from brain.dashboard.sources import Sources
from brain.memory.v2.semantic import semantic_search

SEMANTIC_MIN = 0.4  # below this a "meaning" match is noise
SEARCH_TURNS = 3000  # how far back the search reads the conversations
SEARCH_TYPES = {
    "memorias": "Memórias",
    "crencas": "Crenças",
    "temas": "Temas de estudo",
    "notas": "Notas de estudo",
    "afetos": "Sentimentos",
    "conversas": "Conversas",
}

Found = dict[str, tuple[float, bool, dict[str, Any]]]  # id -> (score, matched by words, item)


def hit(item_id: str, text: str, kind: str, link: str, at: datetime) -> dict[str, Any]:
    clean = " ".join(text.split())
    return {
        "id": item_id,
        "text": clean if len(clean) <= 240 else clean[:239].rstrip() + "…",
        "kind": kind,
        "link": f"#{link}",
        "at": at,
    }


def run_search(sources: Sources, text: str, limit: int, wanted: set[str]) -> dict[str, Any]:
    vector, warning = _embed(sources, text)
    words = [w for w in text.casefold().split() if w]

    def by_words(*fields: str) -> bool:
        haystack = " ".join(fields).casefold()
        return all(w in haystack for w in words)

    groups: list[dict[str, Any]] = []

    def add(kind: str, found: Found) -> None:
        if kind not in wanted:
            return
        ordered = sorted(found.values(), key=lambda t: (not t[1], -t[0]))[:limit]
        items = [
            {**item, "score": round(score, 3), "match": "texto" if words_hit else "significado"}
            for score, words_hit, item in ordered
        ]
        groups.append({"type": kind, "label": SEARCH_TYPES[kind], "items": items})

    memory, data = sources.memory, sources.data
    if memory is not None and getattr(memory, "store", None) is not None:
        add("memorias", _memories(memory, vector, limit, by_words))
    if data is not None:
        model = getattr(sources.embedder, "model_id", "")
        studied = [
            ("crencas", data.beliefs.list(), lambda b: b.statement, _belief_hit),
            ("temas", data.interests.list(), lambda i: i.topic, _interest_hit),
            ("notas", data.notes.list(), lambda n: n.text, _note_hit),
        ]
        for kind, items, text_of, make in studied:
            add(kind, _embedded(items, text_of, make, vector, model, limit, by_words))
        add("afetos", _affects(data, by_words))
        if sources.transcripts:
            add("conversas", _turns(data, by_words))
    return {
        "query": text,
        "semantic": vector is not None,
        "warning": warning,
        "total": sum(len(g["items"]) for g in groups),
        "groups": groups,
    }


def _embed(sources: Sources, text: str) -> tuple[list[float] | None, str | None]:
    embedder = sources.embedder
    if embedder is None:
        return None, "Sem modelo de embeddings: só busca por palavras."
    try:
        return [float(x) for x in embedder.embed(text, "query")], None
    except Exception:  # noqa: BLE001  (the model may still be loading, or have failed)
        return None, "O modelo de embeddings não respondeu: só busca por palavras."


def _memories(
    memory: Any, vector: list[float] | None, limit: int, by_words: Callable[..., bool]
) -> Found:
    def make(m: Any) -> dict[str, Any]:
        return hit(m.id, m.text, f"memória {m.status_reflexao}", f"/memorias/{m.id}", m.created_at)

    found: Found = {}
    for m in memory.store.list_memories(None, False):
        if by_words(m.text, m.contexto):
            found[m.id] = (1.0, True, make(m))
    if vector is not None:
        for result in memory.store.search(vector, top_k=limit, threshold=SEMANTIC_MIN):
            found.setdefault(result.memory.id, (result.score, False, make(result.memory)))
    return found


def _embedded(
    items: Iterable[Any],
    text_of: Callable[[Any], str],
    make: Callable[[Any], dict[str, Any]],
    vector: list[float] | None,
    model: str,
    limit: int,
    by_words: Callable[..., bool],
) -> Found:
    items = list(items)
    found: Found = {}
    for item in items:
        if by_words(text_of(item)):
            found[item.id] = (1.0, True, make(item))
    if vector is not None:
        for result in semantic_search(
            items, vector, model=model, top_k=limit, threshold=SEMANTIC_MIN
        ):
            found.setdefault(result.item.id, (result.score, False, make(result.item)))
    return found


def _belief_hit(b: Any) -> dict[str, Any]:
    return hit(b.id, b.statement, f"crença ({b.status})", "/central", b.updated_at)


def _interest_hit(i: Any) -> dict[str, Any]:
    return hit(i.id, i.topic, f"tema, passo {i.level}", f"/estudos/{i.id}", i.updated_at)


def _note_hit(n: Any) -> dict[str, Any]:
    kind = f"nota ({n.uncertainty}, {n.source})"
    return hit(n.id, n.text, kind, f"/estudos/{n.interest_id}", n.created_at)


def _affects(data: Any, by_words: Callable[..., bool]) -> Found:
    return {
        a.id: (
            1.0,
            True,
            hit(a.id, a.kind, f"sentimento ({a.origin})", f"/afetos/{a.id}", a.occurred_at),
        )
        for a in data.affects.list()
        if by_words(a.kind)
    }


def _turns(data: Any, by_words: Callable[..., bool]) -> Found:
    found: Found = {}
    for t in data.transcripts.list(sort=[("at", -1)], limit=SEARCH_TURNS):
        if by_words(t.user):
            kind = f"conversa, turno {t.index}"
            found[t.id] = (1.0, True, hit(t.id, t.user, kind, f"/conversas/{t.session_id}", t.at))
    return found
