"""Board rules: who may move what, the executor limit, the review isolation.

Every operation takes an ``Actor`` and returns a ``Result``; an expected failure (wrong column,
wrong role, limit reached) is ``Result.fail(...)``, never an exception. Git and GitHub effects
(commit on Done, PR when a task completes) are injected callables, so this layer stays pure.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from kanbai.domain import (
    STORY_POINTS,
    Actor,
    Agent,
    AgentState,
    Column,
    Comment,
    CommentKind,
    Event,
    Kai,
    Project,
    Result,
    Role,
    Task,
    TaskStatus,
)
from kanbai.modelmap import ModelMap
from kanbai.store import Store

MAX_EXECUTORS = 3  # V1 cap on executors running in parallel (DOING + CORRECTION)

Committer = Callable[[Project, Task, Kai], str]  # returns the commit hash; raises on failure
PrOpener = Callable[[Project, Task], str]  # returns the PR url (opened, never merged)


def commit_message(task: Task, kai: Kai) -> str:
    """git-flow message: ``feature(T256): what the KAI did``."""
    return f"feature({task.key}): {kai.title}"


class Board:
    def __init__(
        self,
        store: Store,
        models: ModelMap,
        *,
        committer: Committer | None = None,
        pr_opener: PrOpener | None = None,
        max_executors: int = MAX_EXECUTORS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.models = models
        self.committer = committer
        self.pr_opener = pr_opener
        self.max_executors = max_executors
        self.clock = clock
        self._lock = threading.RLock()  # agents call in parallel; every mutation is serialized

    # ---- setup -----------------------------------------------------------------------

    def create_project(self, project: Project) -> Result:
        with self._lock:
            if self.store.get_project(project.id):
                return Result.fail(f"Projeto '{project.id}' já existe.")
            self.store.add_project(project)
            return Result.success(project)

    def register_agent(self, name: str, role: Role, image_url: str = "") -> Result:
        if role == Role.HUMAN:
            return Result.fail("O humano não é uma agente.")
        with self._lock:
            model = self.models.qa.model if role == Role.QA else ""
            agent = Agent(self.store.next_id("agent"), name, role, image_url, model)
            return Result.success(self.store.add_agent(agent))

    # ---- tasks (backlog) -------------------------------------------------------------

    def create_task(
        self,
        actor: Actor,
        project_id: str,
        key: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        source_url: str = "",
    ) -> Result:
        """Registers a task in the Backlog. Idempotent per (project, key): the importer
        (Claude reading GitHub issues) can run twice without duplicating anything."""
        if actor.role not in (Role.HUMAN, Role.DELEGATOR):
            return Result.fail("Só a humana ou a delegadora cadastram tarefas.")
        key = key.strip()
        if not key or not title.strip():
            return Result.fail("A tarefa precisa de chave (ex.: T256) e título.")
        with self._lock:
            project = self.store.get_project(project_id)
            if not project:
                return Result.fail(f"Projeto '{project_id}' não existe.")
            existing = self.store.find_task(project_id, key)
            if existing:
                return Result.success(existing)  # already there: nothing changes
            task = Task(
                id=self.store.next_id("task"),
                project_id=project_id,
                key=key,
                title=title.strip(),
                description=description,
                acceptance_criteria=acceptance_criteria,
                branch=f"{project.branch_prefix}{key.replace('-', '')}",
                source_url=source_url,
            )
            self.store.add_task(task)
            self._event(None, task.id, actor, "-", TaskStatus.BACKLOG)
            return Result.success(task)

    def create_kai(
        self, actor: Actor, task_id: int, title: str, description: str, points: int
    ) -> Result:
        """The delegator refines a task into KAIs; they start in "A fazer"."""
        if actor.role != Role.DELEGATOR:
            return Result.fail("Só a delegadora cria KAIs.")
        if points not in STORY_POINTS:
            return Result.fail(f"Story points devem ser da escala {list(STORY_POINTS)}.")
        if not title.strip() or not description.strip():
            return Result.fail("O KAI precisa de título e descrição.")
        with self._lock:
            task = self.store.get_task(task_id)
            if not task:
                return Result.fail(f"Tarefa {task_id} não existe.")
            if task.status != TaskStatus.BACKLOG:
                return Result.fail("A tarefa já tem PR aberto; não aceita novos KAIs.")
            kai = Kai(self.store.next_id("kai"), task_id, title.strip(), description, points)
            self.store.add_kai(kai)
            self._event(kai.id, task_id, actor, "-", Column.TODO)
            return Result.success(kai)

    # ---- the pipeline ----------------------------------------------------------------

    def running_executors(self) -> int:
        return sum(1 for k in self.store.list_kais() if k.column in (Column.DOING, Column.CORRECTION))

    def start_kai(self, actor: Actor, kai_id: int) -> Result:
        """A fazer -> Fazendo (IA). The points pick the model; the limit counts new launches."""
        with self._lock:
            found = self._kai_for(actor, Role.EXECUTOR, kai_id)
            if isinstance(found, Result):
                return found
            kai, agent = found
            if kai.column != Column.TODO:
                return Result.fail(f"O KAI está em '{kai.column}', não em A fazer.")
            if agent.current_kai is not None:
                return Result.fail("Esta executora já tem um KAI; ela só encerra quando ele for aprovado.")
            if self.running_executors() >= self.max_executors:
                return Result.fail(f"Limite de {self.max_executors} executoras em paralelo atingido.")
            choice = self.models.for_points(kai.points)
            kai.model = f"{choice.provider}:{choice.model}"
            kai.executor_id = agent.id
            agent.model = kai.model
            agent.state = AgentState.WORKING
            agent.current_kai = kai.id
            self._move(kai, Column.DOING, actor)
            return Result.success(kai)

    def submit(self, actor: Actor, kai_id: int, report: str) -> Result:
        """Fazendo (IA) or Correção -> Revisando (IA). The report goes as a comment."""
        with self._lock:
            found = self._kai_for(actor, Role.EXECUTOR, kai_id)
            if isinstance(found, Result):
                return found
            kai, agent = found
            if kai.executor_id != agent.id:
                return Result.fail("Este KAI pertence a outra executora.")
            if kai.column not in (Column.DOING, Column.CORRECTION):
                return Result.fail(f"O KAI está em '{kai.column}'; só Fazendo ou Correção enviam à revisão.")
            if not report.strip():
                return Result.fail("Escreva o relatório do que foi feito antes de enviar à revisão.")
            self._comment(kai, CommentKind.REPORT, agent.name, report)
            self._move(kai, Column.REVIEWING, actor)
            return Result.success(kai)

    def review(self, actor: Actor, kai_id: int, approved: bool, verdict: str) -> Result:
        """Revisando (IA) -> Revisão Humana (approved) or Correção (failures found)."""
        with self._lock:
            found = self._kai_for(actor, Role.QA, kai_id)
            if isinstance(found, Result):
                return found
            kai, agent = found
            if kai.column != Column.REVIEWING:
                return Result.fail(f"O KAI está em '{kai.column}', não em Revisando.")
            if not verdict.strip():
                return Result.fail("O parecer da QA não pode ser vazio.")
            self._comment(kai, CommentKind.VERDICT, agent.name, verdict)
            self._move(kai, Column.HUMAN if approved else Column.CORRECTION, actor)
            return Result.success(kai)

    def set_human_summary(self, actor: Actor, kai_id: int, summary: str) -> Result:
        """The simple summary for the human review (QA or delegator writes it)."""
        with self._lock:
            if actor.role not in (Role.QA, Role.DELEGATOR):
                return Result.fail("Só a QA ou a delegadora escrevem o resumo da revisão humana.")
            kai = self.store.get_kai(kai_id)
            if not kai:
                return Result.fail(f"KAI {kai_id} não existe.")
            if kai.column != Column.HUMAN:
                return Result.fail("O resumo só vale para KAI em Revisão Humana.")
            agent = self.store.get_agent(actor.agent_id) if actor.agent_id else None
            self._comment(kai, CommentKind.HUMAN_SUMMARY, agent.name if agent else "?", summary)
            return Result.success(kai)

    def human_decision(self, actor: Actor, kai_id: int, approve: bool, note: str = "") -> Result:
        """Revisão Humana -> Feito (the Kanbai commits) or Correção. Only the human."""
        with self._lock:
            if actor.role != Role.HUMAN:
                return Result.fail("Só o humano decide na Revisão Humana.")
            kai = self.store.get_kai(kai_id)
            if not kai:
                return Result.fail(f"KAI {kai_id} não existe.")
            if kai.column != Column.HUMAN:
                return Result.fail(f"O KAI está em '{kai.column}', não em Revisão Humana.")
            if note.strip():
                self._comment(kai, CommentKind.HUMAN_NOTE, "humano", note)
            if not approve:
                self._move(kai, Column.CORRECTION, actor)
                return Result.success(kai)
            return self._finish(actor, kai)

    # ---- queries ---------------------------------------------------------------------

    def review_packet(self, kai_id: int) -> Result:
        """What the QA receives: architecture and acceptance criteria of the task, the KAI's
        own brief. Deliberately NOT the executor's report or any earlier comment, so the review
        is not biased by the executor's reasoning. (The diff is added by the git layer.)"""
        kai = self.store.get_kai(kai_id)
        task = self.store.get_task(kai.task_id) if kai else None
        if not kai or not task:
            return Result.fail(f"KAI {kai_id} não existe.")
        return Result.success(
            {
                "task": task.key,
                "task_title": task.title,
                "task_description": task.description,
                "acceptance_criteria": task.acceptance_criteria,
                "kai_title": kai.title,
                "kai_description": kai.description,
                "branch": task.branch,
            }
        )

    def columns(self, project_id: str | None = None) -> dict[str, list[Kai]]:
        """KAIs grouped by column, for the UI and the MCP list tool."""
        task_ids = {t.id for t in self.store.list_tasks(project_id)}
        out: dict[str, list[Kai]] = {c.value: [] for c in Column}
        for kai in self.store.list_kais():
            if kai.task_id in task_ids:
                out[kai.column.value].append(kai)
        return out

    # ---- internals -------------------------------------------------------------------

    def _kai_for(self, actor: Actor, role: Role, kai_id: int) -> tuple[Kai, Agent] | Result:
        if actor.role != role:
            return Result.fail(f"Só {role.value} faz isso.")
        agent = self.store.get_agent(actor.agent_id) if actor.agent_id is not None else None
        if not agent or agent.role != role:
            return Result.fail("Agente desconhecida.")
        kai = self.store.get_kai(kai_id)
        if not kai:
            return Result.fail(f"KAI {kai_id} não existe.")
        return kai, agent

    def _finish(self, actor: Actor, kai: Kai) -> Result:
        task = self.store.get_task(kai.task_id)
        project = self.store.get_project(task.project_id) if task else None
        if not task or not project:
            return Result.fail("Tarefa ou projeto do KAI não existe.")
        if self.committer is None:
            return Result.fail("Nenhum commiter configurado; o KAI continua em Revisão Humana.")
        try:
            kai.commit_hash = self.committer(project, task, kai)
        except Exception as exc:  # noqa: BLE001 - a git failure must not break the board
            return Result.fail(f"Commit falhou ({type(exc).__name__}): {exc}. O KAI continua em Revisão Humana.")
        self._move(kai, Column.DONE, actor)
        if kai.executor_id is not None and (agent := self.store.get_agent(kai.executor_id)):
            agent.state, agent.current_kai = AgentState.ENDED, None  # approved: she is done
        self._maybe_open_pr(actor, project, task)
        return Result.success(kai)

    def _maybe_open_pr(self, actor: Actor, project: Project, task: Task) -> None:
        kais = self.store.list_kais(task.id)
        if task.status != TaskStatus.BACKLOG or not kais or any(k.column != Column.DONE for k in kais):
            return
        if self.pr_opener is None:
            return
        try:
            task.pr_url = self.pr_opener(project, task)
        except Exception:  # noqa: BLE001 - the KAIs are done; the PR can be retried
            return
        task.status = TaskStatus.PR_OPEN
        self._event(None, task.id, actor, TaskStatus.BACKLOG, TaskStatus.PR_OPEN)

    def _comment(self, kai: Kai, kind: CommentKind, author: str, text: str) -> None:
        kai.comments.append(Comment(kind, author, text, self.clock()))

    def _move(self, kai: Kai, target: Column, actor: Actor) -> None:
        source, kai.column = kai.column, target
        self._event(kai.id, kai.task_id, actor, source, target)

    def _event(self, kai_id: int | None, task_id: int | None, actor: Actor, source: object, target: object) -> None:
        agent = self.store.get_agent(actor.agent_id) if actor.agent_id is not None else None
        who = agent.name if agent else actor.role.value
        self.store.add_event(Event(kai_id, task_id, who, str(source), str(target), self.clock()))
