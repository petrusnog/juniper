"""Postgres implementation of ``Store``. One connection, used under the Board's lock."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from kanbai.domain import (
    Agent,
    AgentState,
    Column,
    Comment,
    CommentKind,
    Event,
    Kai,
    Project,
    Role,
    Task,
    TaskStatus,
)

SCHEMA = Path(__file__).with_name("schema.sql")


def dsn() -> str:
    url = os.environ.get("KANBAI_DATABASE_URL")
    if url:
        return url
    password = os.environ.get("KANBAI_PG_PASSWORD", "")
    host = os.environ.get("KANBAI_PG_HOST", "127.0.0.1")
    port = os.environ.get("KANBAI_PG_PORT", "5434")
    return f"postgresql://kanbai:{password}@{host}:{port}/kanbai"


class PgStore:
    def __init__(self, url: str | None = None) -> None:
        self.conn = psycopg.connect(url or dsn(), row_factory=dict_row, autocommit=True)

    def migrate(self) -> None:
        self.conn.execute(SCHEMA.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.conn.transaction():
            yield

    def next_id(self, kind: str) -> int:
        row = self.conn.execute(f"SELECT nextval('kanbai_{_ident(kind)}_seq') AS n").fetchone()
        return int(row["n"])  # type: ignore[index]

    # ---- projects ----
    def add_project(self, p: Project) -> None:
        self.conn.execute(
            "INSERT INTO projects (id, name, repo_path, base_branch, branch_prefix, id_pattern)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (p.id, p.name, p.repo_path, p.base_branch, p.branch_prefix, p.id_pattern),
        )

    def get_project(self, project_id: str) -> Project | None:
        r = self.conn.execute("SELECT * FROM projects WHERE id=%s", (project_id,)).fetchone()
        return Project(**r) if r else None

    # ---- agents ----
    def add_agent(self, a: Agent) -> Agent:
        self.conn.execute(
            "INSERT INTO agents (id,name,role,image_url,model,state,current_kai,token_hash)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (a.id, a.name, a.role.value, a.image_url, a.model, a.state.value, a.current_kai, a.token_hash),
        )
        return a

    def _agent(self, r: dict) -> Agent:  # type: ignore[type-arg]
        return Agent(r["id"], r["name"], Role(r["role"]), r["image_url"], r["model"],
                     AgentState(r["state"]), r["current_kai"], r["token_hash"])

    def get_agent(self, agent_id: int) -> Agent | None:
        r = self.conn.execute("SELECT * FROM agents WHERE id=%s", (agent_id,)).fetchone()
        return self._agent(r) if r else None

    def list_agents(self) -> list[Agent]:
        return [self._agent(r) for r in self.conn.execute("SELECT * FROM agents ORDER BY id")]

    def find_agent_by_token_hash(self, token_hash: str) -> Agent | None:
        if not token_hash:
            return None
        r = self.conn.execute("SELECT * FROM agents WHERE token_hash=%s", (token_hash,)).fetchone()
        return self._agent(r) if r else None

    # ---- tasks ----
    def add_task(self, t: Task) -> Task:
        self.conn.execute(
            "INSERT INTO tasks (id,project_id,key,title,description,acceptance_criteria,branch,"
            "status,pr_url,source_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (t.id, t.project_id, t.key, t.title, t.description, t.acceptance_criteria, t.branch,
             t.status.value, t.pr_url, t.source_url),
        )
        return t

    def _task(self, r: dict) -> Task:  # type: ignore[type-arg]
        return Task(**{**r, "status": TaskStatus(r["status"])})

    def get_task(self, task_id: int) -> Task | None:
        r = self.conn.execute("SELECT * FROM tasks WHERE id=%s", (task_id,)).fetchone()
        return self._task(r) if r else None

    def find_task(self, project_id: str, key: str) -> Task | None:
        r = self.conn.execute("SELECT * FROM tasks WHERE project_id=%s AND key=%s", (project_id, key)).fetchone()
        return self._task(r) if r else None

    def list_tasks(self, project_id: str | None = None) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE (%s::text IS NULL OR project_id=%s) ORDER BY id", (project_id, project_id)
        )
        return [self._task(r) for r in rows]

    # ---- kais ----
    def add_kai(self, k: Kai) -> Kai:
        self.conn.execute(
            "INSERT INTO kais (id,task_id,title,description,points,col,executor_id,model,comments,commit_hash)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
            (k.id, k.task_id, k.title, k.description, k.points, k.column.value, k.executor_id,
             k.model, _dump_comments(k.comments), k.commit_hash),
        )
        return k

    def _kai(self, r: dict) -> Kai:  # type: ignore[type-arg]
        comments = [Comment(CommentKind(c["kind"]), c["author"], c["text"], datetime.fromisoformat(c["at"]))
                    for c in r["comments"]]
        return Kai(r["id"], r["task_id"], r["title"], r["description"], r["points"], Column(r["col"]),
                   r["executor_id"], r["model"], comments, r["commit_hash"])

    def get_kai(self, kai_id: int) -> Kai | None:
        r = self.conn.execute("SELECT * FROM kais WHERE id=%s", (kai_id,)).fetchone()
        return self._kai(r) if r else None

    def list_kais(self, task_id: int | None = None) -> list[Kai]:
        rows = self.conn.execute(
            "SELECT * FROM kais WHERE (%s::int IS NULL OR task_id=%s) ORDER BY id", (task_id, task_id)
        )
        return [self._kai(r) for r in rows]

    # ---- events ----
    def add_event(self, e: Event) -> None:
        self.conn.execute(
            "INSERT INTO events (kai_id,task_id,who,source,target,at) VALUES (%s,%s,%s,%s,%s,%s)",
            (e.kai_id, e.task_id, e.who, e.source, e.target, e.at),
        )

    def list_events(self) -> list[Event]:
        rows = self.conn.execute("SELECT * FROM events ORDER BY id")
        return [Event(r["kai_id"], r["task_id"], r["who"], r["source"], r["target"], r["at"]) for r in rows]

    # ---- persist in-place changes ----
    def save(self, obj: Agent | Task | Kai) -> None:
        if isinstance(obj, Agent):
            self.conn.execute(
                "UPDATE agents SET name=%s,image_url=%s,model=%s,state=%s,current_kai=%s,token_hash=%s WHERE id=%s",
                (obj.name, obj.image_url, obj.model, obj.state.value, obj.current_kai, obj.token_hash, obj.id),
            )
        elif isinstance(obj, Task):
            self.conn.execute(
                "UPDATE tasks SET title=%s,description=%s,acceptance_criteria=%s,branch=%s,status=%s,"
                "pr_url=%s,source_url=%s WHERE id=%s",
                (obj.title, obj.description, obj.acceptance_criteria, obj.branch, obj.status.value,
                 obj.pr_url, obj.source_url, obj.id),
            )
        else:
            self.conn.execute(
                "UPDATE kais SET title=%s,description=%s,points=%s,col=%s,executor_id=%s,model=%s,"
                "comments=%s::jsonb,commit_hash=%s WHERE id=%s",
                (obj.title, obj.description, obj.points, obj.column.value, obj.executor_id, obj.model,
                 _dump_comments(obj.comments), obj.commit_hash, obj.id),
            )

    def reset(self) -> None:
        """Test helper: empties every table (never called by the service)."""
        self.conn.execute("TRUNCATE events, kais, tasks, agents, projects RESTART IDENTITY")
        for seq in ("agent", "task", "kai"):
            self.conn.execute(f"ALTER SEQUENCE kanbai_{seq}_seq RESTART")


def _ident(kind: str) -> str:
    if kind not in {"agent", "task", "kai"}:
        raise ValueError(f"unknown id kind: {kind}")
    return kind


def _dump_comments(comments: list[Comment]) -> str:
    return json.dumps(
        [{"kind": c.kind.value, "author": c.author, "text": c.text, "at": c.at.isoformat()} for c in comments]
    )
