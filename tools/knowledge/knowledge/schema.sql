-- Knowledge base schema. Idempotent: safe to run on every start.
-- The Markdown files stay the source of truth; these tables are a derived index.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id           SERIAL PRIMARY KEY,
    source       TEXT NOT NULL UNIQUE,          -- docs/pages/agente.md
    kind         TEXT NOT NULL,                 -- page | adr
    title        TEXT NOT NULL,
    grp          TEXT NOT NULL DEFAULT '',
    section      TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT '',
    tags         TEXT[] NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL,
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notes (
    id          SERIAL PRIMARY KEY,
    document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    anchor      TEXT NOT NULL,
    heading     TEXT NOT NULL,
    level       SMALLINT NOT NULL,
    position    INT NOT NULL,
    tech_kind   TEXT NOT NULL DEFAULT '',
    tech_file   TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL,
    body_hash   TEXT NOT NULL,
    tokens_est  INT NOT NULL,                   -- rough token count (chars / 4)
    fts         TSVECTOR NOT NULL,
    embedding   vector(1024),
    UNIQUE (document_id, anchor)
);

CREATE TABLE IF NOT EXISTS links (
    id          SERIAL PRIMARY KEY,
    src_id      INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    dst_source  TEXT NOT NULL,                  -- no FK: the target may not exist (yet)
    dst_anchor  TEXT NOT NULL DEFAULT '',
    label       TEXT NOT NULL,
    related     BOOLEAN NOT NULL DEFAULT FALSE  -- listed under "Para saber mais"
);

CREATE INDEX IF NOT EXISTS notes_fts_idx ON notes USING GIN (fts);
CREATE INDEX IF NOT EXISTS notes_embedding_idx ON notes USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS notes_document_idx ON notes (document_id, position);
CREATE INDEX IF NOT EXISTS documents_tags_idx ON documents USING GIN (tags);
CREATE INDEX IF NOT EXISTS links_src_idx ON links (src_id);
CREATE INDEX IF NOT EXISTS links_dst_idx ON links (dst_source);
