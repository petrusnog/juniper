"""Connection and migration. The password comes from the environment / ``tools/knowledge/.env``."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parent / ".env"


def _load_env_file() -> None:
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def dsn() -> str:
    _load_env_file()
    url = os.environ.get("KNOWLEDGE_DATABASE_URL")
    if url:
        return url
    password = os.environ.get("KNOWLEDGE_PG_PASSWORD", "")
    port = os.environ.get("KNOWLEDGE_PG_PORT", "5433")
    return f"postgresql://knowledge:{password}@127.0.0.1:{port}/knowledge"


def migrate(conn: psycopg.Connection) -> None:
    """Applies the schema (idempotent). The extension goes first so the vector type exists."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    conn.execute((HERE / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()


def connect(*, migrate_schema: bool = True) -> psycopg.Connection:
    conn = psycopg.connect(dsn())
    if migrate_schema:
        migrate(conn)
    else:
        register_vector(conn)
    return conn
