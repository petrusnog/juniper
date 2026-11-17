"""Hybrid search (vector + Portuguese full-text, fused by RRF) and the read operations.

Everything the agents see goes through ``mask_secrets`` (the Juniper's own filter, loaded from
``brain/memory/secretfilter.py`` by path so this tool does not need the brain's dependencies).
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from knowledge.embed import embed_one
from knowledge.parse import fold

RRF_K = 60
CANDIDATES = 30
MAX_PER_DOC = 2

_SPEC = importlib.util.spec_from_file_location(
    "_secretfilter", Path(__file__).resolve().parents[3] / "brain" / "memory" / "secretfilter.py"
)
assert _SPEC and _SPEC.loader
_secretfilter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_secretfilter)
mask_secrets = _secretfilter.mask_secrets

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class Hit:
    note_id: int
    source: str
    anchor: str
    title: str
    heading: str
    kind: str
    tokens: int
    score: float
    body: str

    @property
    def ref(self) -> str:
        return f"{self.source}#{self.anchor}"


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: each list contributes 1/(k+rank) to the ids it contains."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def fts_query(text: str) -> str:
    """An OR tsquery from the free-text question ('' when nothing usable)."""
    words = [w for w in _WORD.findall(text.lower()) if len(w) > 2]
    return " | ".join(dict.fromkeys(words))


def excerpt(body: str, query: str, budget_tokens: int) -> str:
    """The paragraphs of ``body`` that best match ``query`` within the budget, in document order."""
    budget = max(80, budget_tokens * 4)
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    if sum(len(p) for p in paragraphs) <= budget:
        return mask_secrets("\n\n".join(paragraphs))
    terms = {fold(w) for w in _WORD.findall(query) if len(w) > 2}
    scores = [sum(1 for w in _WORD.findall(fold(p)) if w in terms) for p in paragraphs]
    order = sorted(range(len(paragraphs)), key=lambda i: (-scores[i], i))
    chosen: set[int] = set()
    used = 0
    for i in order:
        size = len(paragraphs[i]) + 2
        if used + size > budget:
            if not chosen:  # even the best paragraph is too long: cut it
                chosen.add(i)
                paragraphs[i] = paragraphs[i][:budget].rsplit(" ", 1)[0] + " […]"
            continue
        chosen.add(i)
        used += size
    return mask_secrets("\n\n".join(paragraphs[i] for i in sorted(chosen)))


def _filters(kind: str | None, tag: str | None, group: str | None) -> tuple[str, list[object]]:
    # The opening block of an ADR is metadata (status, date, "Relaciona-se com"): it cites other
    # ADRs by number and would outrank the sections that actually answer. Still readable by ref.
    clauses: list[str] = ["NOT (n.level = 0 AND d.kind = 'adr')"]
    params: list[object] = []
    if kind:
        clauses.append("d.kind = %s")
        params.append(kind)
    if tag:
        clauses.append("%s = ANY(d.tags)")
        params.append(tag.lower())
    if group:
        clauses.append("d.grp = %s")
        params.append(group)
    return " AND " + " AND ".join(clauses), params


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    limit: int = 5,
    kind: str | None = None,
    tag: str | None = None,
    group: str | None = None,
) -> list[Hit]:
    where, params = _filters(kind, tag, group)
    vec = embed_one(query)
    vector_ids = [
        r[0]
        for r in conn.execute(
            f"SELECT n.id FROM notes n JOIN documents d ON d.id = n.document_id"
            f" WHERE n.embedding IS NOT NULL{where}"
            f" ORDER BY n.embedding <=> %s::vector LIMIT {CANDIDATES}",
            [*params, vec],
        )
    ]
    text_ids: list[int] = []
    tsq = fts_query(query)
    if tsq:
        text_ids = [
            r[0]
            for r in conn.execute(
                f"SELECT n.id FROM notes n JOIN documents d ON d.id = n.document_id,"
                f" to_tsquery('portuguese', %s) q WHERE n.fts @@ q{where}"
                f" ORDER BY ts_rank_cd(n.fts, q) DESC LIMIT {CANDIDATES}",
                [tsq, *params],
            )
        ]
    fused = rrf([vector_ids, text_ids])
    if not fused:
        return []
    ranked = sorted(fused, key=lambda i: -fused[i])
    rows = {
        r[0]: r
        for r in conn.execute(
            "SELECT n.id, d.source, n.anchor, d.title, n.heading, d.kind, n.tokens_est, n.body"
            " FROM notes n JOIN documents d ON d.id = n.document_id WHERE n.id = ANY(%s)",
            (ranked,),
        )
    }
    hits: list[Hit] = []
    per_doc: dict[str, int] = {}
    for note_id in ranked:
        r = rows[note_id]
        if per_doc.get(r[1], 0) >= MAX_PER_DOC:
            continue
        per_doc[r[1]] = per_doc.get(r[1], 0) + 1
        hits.append(Hit(r[0], r[1], r[2], r[3], r[4], r[5], r[6], fused[note_id], r[7]))
        if len(hits) >= limit:
            break
    return hits


def format_hits(hits: list[Hit], query: str, max_tokens: int) -> str:
    """The text an agent reads: one block per note, trimmed to the total token budget."""
    if not hits:
        return "Nenhuma nota encontrada. Reformule com outras palavras ou use list_documents."
    per_note = max(60, max_tokens // len(hits))
    blocks = []
    for i, h in enumerate(hits, 1):
        title = f"{h.title} › {h.heading}" if h.heading else h.title
        body = excerpt(h.body, query, per_note)
        cut = " (trecho; use get_note para a nota inteira)" if len(body) < len(h.body) else ""
        blocks.append(f"[{i}] {title}\nref: {h.ref} · ~{h.tokens} tokens{cut}\n{body}")
    return "\n\n".join(blocks)


def get_note(conn: psycopg.Connection, ref: str, *, max_tokens: int = 4000) -> str:
    """A whole note (``docs/pages/x.md#anchor``) or, with no anchor, the outline of the document."""
    source, _, anchor = ref.partition("#")
    doc = conn.execute(
        "SELECT id, title, description FROM documents WHERE source = %s", (source,)
    ).fetchone()
    if not doc:
        return f"Documento '{source}' não encontrado."
    if not anchor:
        rows = conn.execute(
            "SELECT anchor, heading, tokens_est FROM notes WHERE document_id = %s ORDER BY position",
            (doc[0],),
        ).fetchall()
        lines = [f"{doc[1]} — {doc[2]}", ""]
        lines += [f"- {a} · {h or '(abertura)'} · ~{t} tokens" for a, h, t in rows]
        return "\n".join(lines)
    note = conn.execute(
        "SELECT heading, body, tech_file, tokens_est FROM notes WHERE document_id = %s AND anchor = %s",
        (doc[0], anchor),
    ).fetchone()
    if not note:
        return f"Nota '{ref}' não encontrada. Use get_note('{source}') para ver as seções."
    heading, body, tech_file, tokens = note
    if tokens > max_tokens:
        body = body[: max_tokens * 4].rsplit("\n", 1)[0] + "\n[… cortada; aumente max_tokens]"
    head = f"{doc[1]} › {heading}" if heading else doc[1]
    file_line = f"\ncódigo: {tech_file}" if tech_file else ""
    return f"{head}\nref: {ref}{file_line}\n\n{mask_secrets(body)}"


def neighbors(conn: psycopg.Connection, source: str) -> str:
    """What a document links to (related first) and what links to it."""
    doc = conn.execute("SELECT id, title FROM documents WHERE source = %s", (source,)).fetchone()
    if not doc:
        return f"Documento '{source}' não encontrado."
    out = conn.execute(
        """
        SELECT l.dst_source, l.dst_anchor, l.related, t.title, t.description
        FROM links l LEFT JOIN documents t ON t.source = l.dst_source
        WHERE l.src_id = %s ORDER BY l.related DESC, l.id
        """,
        (doc[0],),
    ).fetchall()
    back = conn.execute(
        "SELECT DISTINCT d.source, d.title FROM links l JOIN documents d ON d.id = l.src_id"
        " WHERE l.dst_source = %s AND d.source <> %s ORDER BY d.source",
        (source, source),
    ).fetchall()
    lines = [f"{doc[1]} ({source})", "", "Aponta para:"]
    seen: set[tuple[str, str]] = set()
    for dst, anchor, related, title, desc in out:
        if (dst, anchor) in seen or dst == source:
            continue
        seen.add((dst, anchor))
        mark = "★ " if related else "  "
        ref = f"{dst}#{anchor}" if anchor else dst
        lines.append(f"{mark}{ref} · {title or '(não indexado)'}" + (f" — {desc}" if desc else ""))
    lines += ["", "É citado por:"] + [f"  {s} · {t}" for s, t in back]
    return "\n".join(lines)


def list_documents(
    conn: psycopg.Connection, *, kind: str | None = None, tag: str | None = None,
    group: str | None = None,
) -> str:
    where, params = _filters(kind, tag, group)
    rows = conn.execute(
        f"SELECT d.source, d.title, d.description FROM documents d WHERE TRUE{where} ORDER BY d.source",
        params,
    ).fetchall()
    return "\n".join(f"{s} · {t} — {d}" for s, t, d in rows) or "Nenhum documento."
