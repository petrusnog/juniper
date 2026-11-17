-- Kanbai schema (idempotent). Own database; the knowledge base lives in another one.
CREATE SEQUENCE IF NOT EXISTS kanbai_agent_seq;
CREATE SEQUENCE IF NOT EXISTS kanbai_task_seq;
CREATE SEQUENCE IF NOT EXISTS kanbai_kai_seq;

CREATE TABLE IF NOT EXISTS projects (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    repo_path     text NOT NULL,
    base_branch   text NOT NULL DEFAULT 'master',
    branch_prefix text NOT NULL DEFAULT 'feature/',
    id_pattern    text NOT NULL DEFAULT 'T-?\d+'
);

CREATE TABLE IF NOT EXISTS agents (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    role        text NOT NULL,
    image_url   text NOT NULL DEFAULT '',
    model       text NOT NULL DEFAULT '',
    state       text NOT NULL DEFAULT 'ociosa',
    current_kai integer,
    token_hash  text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS agents_token_idx ON agents (token_hash) WHERE token_hash <> '';

CREATE TABLE IF NOT EXISTS tasks (
    id                  integer PRIMARY KEY,
    project_id          text NOT NULL REFERENCES projects (id),
    key                 text NOT NULL,
    title               text NOT NULL,
    description         text NOT NULL DEFAULT '',
    acceptance_criteria text NOT NULL DEFAULT '',
    branch              text NOT NULL,
    status              text NOT NULL DEFAULT 'backlog',
    pr_url              text NOT NULL DEFAULT '',
    source_url          text NOT NULL DEFAULT '',
    UNIQUE (project_id, key)
);

CREATE TABLE IF NOT EXISTS kais (
    id          integer PRIMARY KEY,
    task_id     integer NOT NULL REFERENCES tasks (id),
    title       text NOT NULL,
    description text NOT NULL,
    points      integer NOT NULL,
    col         text NOT NULL DEFAULT 'a_fazer',
    executor_id integer,
    model       text NOT NULL DEFAULT '',
    comments    jsonb NOT NULL DEFAULT '[]',
    commit_hash text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS kais_task_idx ON kais (task_id);

CREATE TABLE IF NOT EXISTS events (
    id      bigserial PRIMARY KEY,
    kai_id  integer,
    task_id integer,
    who     text NOT NULL,
    source  text NOT NULL,
    target  text NOT NULL,
    at      timestamptz NOT NULL
);
