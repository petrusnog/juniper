"""Domain types of the board. Pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Column(StrEnum):
    TODO = "a_fazer"
    DOING = "fazendo"
    REVIEWING = "revisando"
    CORRECTION = "correcao"
    HUMAN = "revisao_humana"
    DONE = "feito"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"  # initial state; stays until the PR is open
    PR_OPEN = "pr_aberto"


class Role(StrEnum):
    DELEGATOR = "delegadora"
    EXECUTOR = "executora"
    QA = "qa"
    HUMAN = "humano"


class AgentState(StrEnum):
    IDLE = "ociosa"
    WORKING = "trabalhando"
    ENDED = "encerrada"


class CommentKind(StrEnum):
    REPORT = "relatorio"  # executor's report
    VERDICT = "parecer"  # QA verdict
    HUMAN_SUMMARY = "resumo_humano"
    HUMAN_NOTE = "nota_humana"


# KAI complexity: Fibonacci story points.
STORY_POINTS = (1, 2, 3, 5, 8, 13)


@dataclass
class Project:
    id: str  # slug, e.g. "juniper"
    name: str
    repo_path: str
    base_branch: str = "master"
    branch_prefix: str = "feature/"
    id_pattern: str = r"T-?\d+"  # what a task key looks like in this project


@dataclass
class Agent:
    id: int
    name: str
    role: Role
    image_url: str = ""
    model: str = ""
    state: AgentState = AgentState.IDLE
    current_kai: int | None = None
    token_hash: str = ""  # sha256 of the MCP bearer token; the token itself is never stored


@dataclass
class Task:
    id: int
    project_id: str
    key: str  # "T256"
    title: str
    description: str
    acceptance_criteria: str
    branch: str
    status: TaskStatus = TaskStatus.BACKLOG
    pr_url: str = ""
    source_url: str = ""  # e.g. the GitHub issue it was imported from


@dataclass
class Comment:
    kind: CommentKind
    author: str  # agent name or "humano"
    text: str
    at: datetime


@dataclass
class Kai:
    id: int
    task_id: int
    title: str
    description: str
    points: int
    column: Column = Column.TODO
    executor_id: int | None = None
    model: str = ""  # picked from the points when the executor starts
    comments: list[Comment] = field(default_factory=list)
    commit_hash: str = ""


@dataclass
class Event:
    kai_id: int | None
    task_id: int | None
    who: str
    source: str
    target: str
    at: datetime


@dataclass(frozen=True)
class Actor:
    """Who is acting. Agents are identified by id; the human has no agent."""

    role: Role
    agent_id: int | None = None


@dataclass
class Result:
    """Expected failures are a typed result, never an exception."""

    ok: bool
    error: str = ""
    data: object = None

    @staticmethod
    def fail(error: str) -> Result:
        return Result(False, error)

    @staticmethod
    def success(data: object = None) -> Result:
        return Result(True, "", data)
