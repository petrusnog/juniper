"""Storage contract and the in-memory implementation (used by the tests and the rules phase).

The Postgres implementation will satisfy the same ``Store`` protocol, so the rules in
``board.py`` do not change when the persistence arrives.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from typing import Protocol

from kanbai.domain import Agent, Event, Kai, Project, Task


class Store(Protocol):
    def add_project(self, project: Project) -> None: ...
    def get_project(self, project_id: str) -> Project | None: ...
    def add_agent(self, agent: Agent) -> Agent: ...
    def get_agent(self, agent_id: int) -> Agent | None: ...
    def list_agents(self) -> list[Agent]: ...
    def add_task(self, task: Task) -> Task: ...
    def get_task(self, task_id: int) -> Task | None: ...
    def find_task(self, project_id: str, key: str) -> Task | None: ...
    def list_tasks(self, project_id: str | None = None) -> list[Task]: ...
    def add_kai(self, kai: Kai) -> Kai: ...
    def get_kai(self, kai_id: int) -> Kai | None: ...
    def list_kais(self, task_id: int | None = None) -> list[Kai]: ...
    def add_event(self, event: Event) -> None: ...
    def list_events(self) -> list[Event]: ...
    def next_id(self, kind: str) -> int: ...
    def find_agent_by_token_hash(self, token_hash: str) -> Agent | None: ...
    def save(self, obj: Agent | Task | Kai) -> None: ...  # persists in-place changes
    def transaction(self) -> AbstractContextManager[None]: ...


class MemoryStore:
    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._agents: dict[int, Agent] = {}
        self._tasks: dict[int, Task] = {}
        self._kais: dict[int, Kai] = {}
        self._events: list[Event] = []
        self._ids: dict[str, int] = {}

    def next_id(self, kind: str) -> int:
        self._ids[kind] = self._ids.get(kind, 0) + 1
        return self._ids[kind]

    def add_project(self, project: Project) -> None:
        self._projects[project.id] = project

    def get_project(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def add_agent(self, agent: Agent) -> Agent:
        self._agents[agent.id] = agent
        return agent

    def get_agent(self, agent_id: int) -> Agent | None:
        return self._agents.get(agent_id)

    def list_agents(self) -> list[Agent]:
        return list(self._agents.values())

    def add_task(self, task: Task) -> Task:
        self._tasks[task.id] = task
        return task

    def get_task(self, task_id: int) -> Task | None:
        return self._tasks.get(task_id)

    def find_task(self, project_id: str, key: str) -> Task | None:
        for task in self._tasks.values():
            if task.project_id == project_id and task.key == key:
                return task
        return None

    def list_tasks(self, project_id: str | None = None) -> list[Task]:
        return [t for t in self._tasks.values() if project_id in (None, t.project_id)]

    def add_kai(self, kai: Kai) -> Kai:
        self._kais[kai.id] = kai
        return kai

    def get_kai(self, kai_id: int) -> Kai | None:
        return self._kais.get(kai_id)

    def list_kais(self, task_id: int | None = None) -> list[Kai]:
        return [k for k in self._kais.values() if task_id in (None, k.task_id)]

    def add_event(self, event: Event) -> None:
        self._events.append(replace(event))

    def list_events(self) -> list[Event]:
        return list(self._events)

    def find_agent_by_token_hash(self, token_hash: str) -> Agent | None:
        for agent in self._agents.values():
            if agent.token_hash and agent.token_hash == token_hash:
                return agent
        return None

    def save(self, obj: Agent | Task | Kai) -> None:
        """No-op: objects are kept by reference."""

    def transaction(self) -> AbstractContextManager[None]:
        return nullcontext()
