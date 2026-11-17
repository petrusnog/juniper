"""Indexes the Markdown documentation into Postgres (incremental, idempotent).

A document whose content hash did not change is skipped. In a changed document only the notes
whose text changed are embedded again; notes that disappeared are deleted. Documents whose file
is gone are removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from knowledge.embed import embed
from knowledge.parse import DOCS_DIR, Document, Note, load_documents


@dataclass
class Report:
    documents: int = 0
    skipped: int = 0
    notes_embedded: int = 0
    notes_reused: int = 0
    notes_removed: int = 0
    documents_removed: int = 0
    warnings: list[str] = field(default_factory=list)


def embed_text(doc: Document, note: Note) -> str:
    """What is embedded: where the note lives plus its text (the context sharpens the vector)."""
    head = f"{doc.title} › {note.heading}" if note.heading else doc.title
    return f"{head}\n{doc.description}\n{note.body}".strip()


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _fts_text(doc: Document, note: Note) -> str:
    # Heading and page title repeat so a title match outweighs a passing mention.
    return " ".join([note.heading, note.heading, doc.title, " ".join(doc.tags), note.body])


def index_documents(
    conn: psycopg.Connection, docs: list[Document], *, force: bool = False
) -> Report:
    report = Report()
    existing = dict(conn.execute("SELECT source, content_hash FROM documents").fetchall())
    live = {d.source for d in docs}

    for source in sorted(set(existing) - live):
        conn.execute("DELETE FROM documents WHERE source = %s", (source,))
        report.documents_removed += 1

    for doc in docs:
        report.documents += 1
        if not force and existing.get(doc.source) == doc.content_hash:
            report.skipped += 1
            continue
        row = conn.execute(
            """
            INSERT INTO documents (source, kind, title, grp, section, description, status, tags,
                                   content_hash, indexed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (source) DO UPDATE SET
                kind = EXCLUDED.kind, title = EXCLUDED.title, grp = EXCLUDED.grp,
                section = EXCLUDED.section, description = EXCLUDED.description,
                status = EXCLUDED.status, tags = EXCLUDED.tags,
                content_hash = EXCLUDED.content_hash, indexed_at = now()
            RETURNING id
            """,
            (doc.source, doc.kind, doc.title, doc.group, doc.section, doc.description,
             doc.status, doc.tags, doc.content_hash),
        ).fetchone()
        assert row is not None
        doc_id = row[0]

        old = {
            anchor: (body_hash, has_vec)
            for anchor, body_hash, has_vec in conn.execute(
                "SELECT anchor, body_hash, embedding IS NOT NULL FROM notes WHERE document_id = %s",
                (doc_id,),
            )
        }
        fresh = [n for n in doc.notes if force or old.get(n.anchor, (None, False)) != (n.body_hash, True)]
        vectors = dict(zip((n.anchor for n in fresh), embed([embed_text(doc, n) for n in fresh]))) if fresh else {}

        for note in doc.notes:
            fts = _fts_text(doc, note)
            if note.anchor in vectors:
                conn.execute(
                    """
                    INSERT INTO notes (document_id, anchor, heading, level, position, tech_kind,
                                       tech_file, body, body_hash, tokens_est, fts, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            to_tsvector('portuguese', %s), %s)
                    ON CONFLICT (document_id, anchor) DO UPDATE SET
                        heading = EXCLUDED.heading, level = EXCLUDED.level,
                        position = EXCLUDED.position, tech_kind = EXCLUDED.tech_kind,
                        tech_file = EXCLUDED.tech_file, body = EXCLUDED.body,
                        body_hash = EXCLUDED.body_hash, tokens_est = EXCLUDED.tokens_est,
                        fts = EXCLUDED.fts, embedding = EXCLUDED.embedding
                    """,
                    (doc_id, note.anchor, note.heading, note.level, note.position, note.tech_kind,
                     note.tech_file, note.body, note.body_hash, _tokens(note.body), fts,
                     vectors[note.anchor]),
                )
                report.notes_embedded += 1
            else:
                conn.execute(
                    "UPDATE notes SET position = %s WHERE document_id = %s AND anchor = %s",
                    (note.position, doc_id, note.anchor),
                )
                report.notes_reused += 1

        keep = [n.anchor for n in doc.notes]
        gone = conn.execute(
            "DELETE FROM notes WHERE document_id = %s AND NOT (anchor = ANY(%s))", (doc_id, keep)
        ).rowcount
        report.notes_removed += gone

        conn.execute("DELETE FROM links WHERE src_id = %s", (doc_id,))
        for link in dict.fromkeys(doc.links):
            conn.execute(
                "INSERT INTO links (src_id, dst_source, dst_anchor, label, related)"
                " VALUES (%s, %s, %s, %s, %s)",
                (doc_id, link.dst_source, link.dst_anchor, link.label, link.related),
            )
        conn.commit()

    broken = conn.execute(
        """
        SELECT d.source, l.dst_source FROM links l JOIN documents d ON d.id = l.src_id
        WHERE NOT EXISTS (SELECT 1 FROM documents t WHERE t.source = l.dst_source)
        """
    ).fetchall()
    report.warnings = sorted({f"{s} → {t} (link para nota inexistente)" for s, t in broken})
    conn.commit()
    return report


def index_docs_dir(conn: psycopg.Connection, root: Path = DOCS_DIR, *, force: bool = False) -> Report:
    return index_documents(conn, load_documents(root), force=force)
