"""Tests for the knowledge base: parsing and ranking are pure; the database ones need the
compose Postgres (skipped when it is not up) and use a fake embedder, so no Ollama is needed."""

from __future__ import annotations

import hashlib
import math

import psycopg
import pytest

from knowledge import db, indexer, search
from knowledge.parse import parse

PAGE = """---
title: Agente
group: Núcleo
description: Como o agente decide.
tags: agente dispatcher confirmacao
---

Texto de abertura.

## Visão geral {#visao-geral}

O agente recebe a mensagem. Veja [Tools](tools.md#tiers) e [ADR-010](../decisions/ADR-010.md).

## Confirmação na conversa {#confirmacao kind=funcao file=brain/core/consent.py}

Não há prompt [y/N].

```python
print("segredo: sk-abcdefghijklmnopqrstuvwx")
```

```html-raw
<div>ignorado</div>
```

## Visão geral {#visao-geral}

Duplicada.

## Para saber mais {#para-saber-mais}

- [Memória](memoria.md) (Núcleo): a memória.
"""


def test_parse_front_matter_and_sections() -> None:
    doc = parse(PAGE, "docs/pages/agente.md", "page")
    assert (doc.title, doc.group, doc.kind) == ("Agente", "Núcleo", "page")
    assert doc.tags == ["agente", "dispatcher", "confirmacao"]
    anchors = [n.anchor for n in doc.notes]
    assert anchors == ["abertura", "visao-geral", "confirmacao", "visao-geral-2"]
    assert "para-saber-mais" not in anchors  # a link list is not a note


def test_parse_technical_heading_and_raw_html() -> None:
    doc = parse(PAGE, "docs/pages/agente.md", "page")
    note = next(n for n in doc.notes if n.anchor == "confirmacao")
    assert (note.tech_kind, note.tech_file) == ("funcao", "brain/core/consent.py")
    assert "```python" in note.body and "ignorado" not in note.body


def test_parse_links_resolved_and_related() -> None:
    doc = parse(PAGE, "docs/pages/agente.md", "page")
    pairs = {(l.dst_source, l.dst_anchor, l.related) for l in doc.links}
    assert ("docs/pages/tools.md", "tiers", False) in pairs
    assert ("docs/decisions/ADR-010.md", "", False) in pairs
    assert ("docs/pages/memoria.md", "", True) in pairs


def test_body_hash_changes_with_text() -> None:
    a = parse(PAGE, "x.md", "page").notes[1]
    b = parse(PAGE.replace("recebe a mensagem", "recebe o texto"), "x.md", "page").notes[1]
    assert a.body_hash != b.body_hash


def test_rrf_rewards_agreement() -> None:
    scores = search.rrf([[1, 2, 3], [3, 2, 9]])
    assert scores[2] > scores[1] and scores[3] > scores[1]
    assert scores[2] == pytest.approx(1 / 62 + 1 / 62)


def test_fts_query_is_or_and_safe() -> None:
    q = search.fts_query("Como o agente (confirma) 'DROP'; a ação?")
    assert " | " in q and "'" not in q and ";" not in q and "(" not in q


def test_excerpt_picks_matching_paragraph_and_masks_secrets() -> None:
    body = "\n\n".join(["intro " * 40, "o dispatcher decide a tool " * 3, "fim " * 40])
    out = search.excerpt(body, "dispatcher", 30)
    assert "dispatcher" in out and "intro" not in out
    assert "sk-" not in search.excerpt("chave sk-abcdefghijklmnopqrstuvwx ok", "chave", 50)


# --- database ---------------------------------------------------------------------------------


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """A deterministic bag-of-words vector, so similar texts are close without a model."""
    out = []
    for text in texts:
        v = [0.0] * 1024
        for w in text.lower().split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 1024] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


@pytest.fixture()
def conn(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    # A throwaway database, so a test run never touches (or locks) the real index.
    try:
        admin = psycopg.connect(db.dsn(), autocommit=True)
    except Exception:  # noqa: BLE001
        pytest.skip("Postgres do compose não está no ar")
    admin.execute("DROP DATABASE IF EXISTS knowledge_test WITH (FORCE)")
    admin.execute("CREATE DATABASE knowledge_test")
    monkeypatch.setenv("KNOWLEDGE_DATABASE_URL", db.dsn().rsplit("/", 1)[0] + "/knowledge_test")
    c = db.connect()
    monkeypatch.setattr(indexer, "embed", _fake_embed)
    monkeypatch.setattr(search, "embed_one", lambda t: _fake_embed([t])[0])
    yield c
    c.close()
    admin.execute("DROP DATABASE IF EXISTS knowledge_test WITH (FORCE)")
    admin.close()


def test_index_is_idempotent_and_incremental(conn) -> None:  # type: ignore[no-untyped-def]
    doc = parse(PAGE, "docs/pages/agente.md", "page")
    r1 = indexer.index_documents(conn, [doc])
    assert r1.notes_embedded == 4
    r2 = indexer.index_documents(conn, [parse(PAGE, "docs/pages/agente.md", "page")])
    assert r2.skipped == 1 and r2.notes_embedded == 0

    changed = parse(PAGE.replace("Não há prompt", "Sem prompt"), "docs/pages/agente.md", "page")
    r3 = indexer.index_documents(conn, [changed])
    assert (r3.notes_embedded, r3.notes_reused) == (1, 3)

    r4 = indexer.index_documents(conn, [])
    assert r4.documents_removed == 1
    assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 0


def test_hybrid_search_get_note_and_neighbors(conn) -> None:  # type: ignore[no-untyped-def]
    other = parse("---\ntitle: Memória\n---\n\n## Visão geral {#visao-geral}\n\nGuarda fatos.\n",
                  "docs/pages/memoria.md", "page")
    indexer.index_documents(conn, [parse(PAGE, "docs/pages/agente.md", "page"), other])

    hits = search.search(conn, "confirmação prompt", limit=3)
    assert hits and hits[0].ref == "docs/pages/agente.md#confirmacao"
    assert search.search(conn, "prompt", kind="adr") == []

    text = search.get_note(conn, "docs/pages/agente.md#confirmacao")
    assert "brain/core/consent.py" in text and "sk-abcdefghijkl" not in text
    assert "confirmacao" in search.get_note(conn, "docs/pages/agente.md")  # outline
    assert "não encontrada" in search.get_note(conn, "docs/pages/agente.md#nada")

    near = search.neighbors(conn, "docs/pages/memoria.md")
    assert "docs/pages/agente.md" in near  # cited by
    assert "★" in search.neighbors(conn, "docs/pages/agente.md")  # related link marked


def test_adr_opening_metadata_is_not_ranked(conn) -> None:  # type: ignore[no-untyped-def]
    adr = parse(
        "# ADR-099 — X\n\n- **Relaciona-se com:** ADR-012 protocolo\n\n## Decisão {#decisao}\n\n"
        "O protocolo usa protocol_version.\n",
        "docs/decisions/ADR-099.md",
        "adr",
    )
    indexer.index_documents(conn, [adr])
    refs = [h.ref for h in search.search(conn, "ADR-012 protocolo")]
    assert refs == ["docs/decisions/ADR-099.md#decisao"]
    assert "Relaciona-se" in search.get_note(conn, "docs/decisions/ADR-099.md#abertura")
