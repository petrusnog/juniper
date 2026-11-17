"""Rules of the board, on the in-memory store (no database, network or git needed)."""

from __future__ import annotations

import os

import pytest

from kanbai.board import Board, commit_message
from kanbai.domain import Actor, AgentState, Column, CommentKind, Project, Role, TaskStatus
from kanbai.modelmap import ModelMap
from kanbai.store import MemoryStore

HUMAN = Actor(Role.HUMAN)


def _make_store():  # type: ignore[no-untyped-def]
    """Memory by default. With KANBAI_TEST_DATABASE_URL the same tests run on Postgres (run
    pytest twice), so the two stores keep the same behaviour."""
    url = os.environ.get("KANBAI_TEST_DATABASE_URL")
    if not url:
        return MemoryStore()
    from kanbai.pgstore import PgStore

    store = PgStore(url)
    store.migrate()
    store.reset()
    return store


class Env:
    def __init__(self, store=None, **kw) -> None:  # type: ignore[no-untyped-def]
        self.commits: list[str] = []
        self.prs: list[str] = []
        self.board = Board(
            store or _make_store(),
            ModelMap.load(),
            committer=lambda p, t, k: (self.commits.append(commit_message(t, k)) or f"sha{k.id}"),
            pr_opener=lambda p, t: (self.prs.append(t.branch) or f"https://gh/pr/{t.key}"),
            **kw,
        )
        self.board.create_project(Project("juniper", "Juniper", "/repo"))
        self.delegator = self.agent("Aiko", Role.DELEGATOR)
        self.qa = self.agent("Rei", Role.QA)

    def agent(self, name: str, role: Role) -> Actor:
        agent = self.board.register_agent(name, role).data
        return Actor(role, agent.id)  # type: ignore[attr-defined]

    def task(self, key: str = "T256") -> int:
        return self.board.create_task(HUMAN, "juniper", key, "Título", "d", "criterios").data.id  # type: ignore[attr-defined]

    def kai(self, task_id: int, points: int = 3) -> int:
        return self.board.create_kai(self.delegator, task_id, "Faz X", "detalhes", points).data.id  # type: ignore[attr-defined]


def test_create_task_is_idempotent_and_derives_branch() -> None:
    env = Env()
    first = env.board.create_task(HUMAN, "juniper", "T-256", "A", source_url="https://gh/issues/256")
    again = env.board.create_task(HUMAN, "juniper", "T-256", "A de novo")
    assert first.ok and again.ok and first.data == again.data
    assert first.data.branch == "feature/T256"  # type: ignore[attr-defined]
    assert len(env.board.store.list_tasks()) == 1


def test_same_key_in_two_projects_is_two_tasks() -> None:
    env = Env()
    env.board.create_project(Project("outro", "Outro", "/x", base_branch="main", branch_prefix="feat/"))
    a = env.board.create_task(HUMAN, "juniper", "T1", "A").data
    b = env.board.create_task(HUMAN, "outro", "T1", "B").data
    assert a != b and b.branch == "feat/T1"  # type: ignore[attr-defined]


def test_executor_cannot_create_tasks_or_kais() -> None:
    env = Env()
    ex = env.agent("Mika", Role.EXECUTOR)
    assert not env.board.create_task(ex, "juniper", "T1", "A").ok
    tid = env.task()
    assert not env.board.create_kai(ex, tid, "t", "d", 3).ok


@pytest.mark.parametrize("points", [0, 4, 6, 100])
def test_points_must_be_fibonacci(points: int) -> None:
    env = Env()
    assert not env.board.create_kai(env.delegator, env.task(), "t", "d", points).ok


def test_points_pick_the_model() -> None:
    env = Env()
    tid = env.task()
    cheap, mid, big = env.kai(tid, 1), env.kai(tid, 3), env.kai(tid, 13)
    got = {}
    for kid in (cheap, mid, big):
        ex = env.agent(f"E{kid}", Role.EXECUTOR)
        assert env.board.start_kai(ex, kid).ok, kid
        got[kid] = env.board.store.get_kai(kid).model  # type: ignore[union-attr]
        env.board.max_executors += 1
    assert "haiku" in got[cheap] and "sonnet" in got[mid] and "sonnet" in got[big]  # two tiers only
    assert "opus" in env.board.models.qa.model


def test_limit_of_three_executors() -> None:
    env = Env()
    tid = env.task()
    kais = [env.kai(tid) for _ in range(4)]
    for kid in kais[:3]:
        assert env.board.start_kai(env.agent(f"E{kid}", Role.EXECUTOR), kid).ok
    fourth = env.board.start_kai(env.agent("E4", Role.EXECUTOR), kais[3])
    assert not fourth.ok and "Limite" in fourth.error
    assert env.board.store.get_kai(kais[3]).column == Column.TODO  # type: ignore[union-attr]


def test_executor_has_one_kai_and_cannot_touch_others() -> None:
    env = Env()
    tid = env.task()
    k1, k2 = env.kai(tid), env.kai(tid)
    ex, other = env.agent("A", Role.EXECUTOR), env.agent("B", Role.EXECUTOR)
    assert env.board.start_kai(ex, k1).ok
    assert not env.board.start_kai(ex, k2).ok  # already has one
    assert not env.board.submit(other, k1, "relatório").ok  # not hers
    assert not env.board.start_kai(ex, 999).ok  # unknown: typed failure


def test_submit_needs_report_and_right_column() -> None:
    env = Env()
    kid = env.kai(env.task())
    ex = env.agent("A", Role.EXECUTOR)
    assert not env.board.submit(ex, kid, "x").ok  # still in A fazer
    env.board.start_kai(ex, kid)
    assert not env.board.submit(ex, kid, "  ").ok
    assert env.board.submit(ex, kid, "fiz tudo").ok
    assert env.board.store.get_kai(kid).column == Column.REVIEWING  # type: ignore[union-attr]


def _to_review(env: Env, kid: int) -> Actor:
    ex = env.agent("Exec", Role.EXECUTOR)
    env.board.start_kai(ex, kid)
    env.board.submit(ex, kid, "meu raciocínio secreto")
    return ex


def test_qa_failure_goes_to_correction_and_same_executor_fixes() -> None:
    env = Env()
    kid = env.kai(env.task())
    ex = _to_review(env, kid)
    assert not env.board.review(ex, kid, True, "ok").ok  # executor cannot review
    assert env.board.review(env.qa, kid, False, "falta teste").ok
    kai = env.board.store.get_kai(kid)
    assert kai.column == Column.CORRECTION and kai.executor_id == ex.agent_id  # type: ignore[union-attr]
    assert env.board.submit(ex, kid, "corrigi").ok
    assert env.board.store.get_kai(kid).column == Column.REVIEWING  # type: ignore[union-attr]


def test_review_packet_hides_the_executor_report() -> None:
    env = Env()
    kid = env.kai(env.task())
    _to_review(env, kid)
    packet = env.board.review_packet(kid).data
    assert "criterios" in packet["acceptance_criteria"]  # type: ignore[index]
    assert "secreto" not in str(packet)


def test_only_human_decides_and_done_commits() -> None:
    env = Env()
    kid = env.kai(env.task())
    ex = _to_review(env, kid)
    env.board.review(env.qa, kid, True, "tudo certo")
    assert env.board.set_human_summary(env.qa, kid, "resumo").ok
    assert not env.board.human_decision(env.qa, kid, True).ok
    assert not env.board.human_decision(ex, kid, True).ok
    assert env.board.human_decision(HUMAN, kid, True).ok
    kai = env.board.store.get_kai(kid)
    assert kai.column == Column.DONE and kai.commit_hash == f"sha{kid}"  # type: ignore[union-attr]
    assert env.commits == ["feature(T256): Faz X"]
    agent = env.board.store.get_agent(ex.agent_id)  # type: ignore[arg-type]
    assert agent.state == AgentState.ENDED and agent.current_kai is None  # type: ignore[union-attr]
    kinds = [c.kind for c in kai.comments]  # type: ignore[union-attr]
    assert kinds == [CommentKind.REPORT, CommentKind.VERDICT, CommentKind.HUMAN_SUMMARY]


def test_human_rejection_goes_to_correction_with_note() -> None:
    env = Env()
    kid = env.kai(env.task())
    _to_review(env, kid)
    env.board.review(env.qa, kid, True, "ok")
    assert env.board.human_decision(HUMAN, kid, False, "refaz o nome").ok
    kai = env.board.store.get_kai(kid)
    assert kai.column == Column.CORRECTION and kai.comments[-1].kind == CommentKind.HUMAN_NOTE  # type: ignore[union-attr]


def test_commit_failure_keeps_kai_in_human_review() -> None:
    env = Env()
    env.board.committer = lambda p, t, k: (_ for _ in ()).throw(RuntimeError("git quebrou"))
    kid = env.kai(env.task())
    _to_review(env, kid)
    env.board.review(env.qa, kid, True, "ok")
    res = env.board.human_decision(HUMAN, kid, True)
    assert not res.ok and "git quebrou" in res.error
    assert env.board.store.get_kai(kid).column == Column.HUMAN  # type: ignore[union-attr]


def test_pr_opens_only_when_all_kais_are_done() -> None:
    env = Env()
    tid = env.task()
    k1, k2 = env.kai(tid), env.kai(tid)
    for kid in (k1, k2):
        _to_review(env, kid)
        env.board.review(env.qa, kid, True, "ok")
    env.board.human_decision(HUMAN, k1, True)
    task = env.board.store.get_task(tid)
    assert task.status == TaskStatus.BACKLOG and env.prs == []  # type: ignore[union-attr]
    env.board.human_decision(HUMAN, k2, True)
    task = env.board.store.get_task(tid)  # re-read: a database store hands out fresh objects
    assert task.status == TaskStatus.PR_OPEN and task.pr_url.endswith("T256")  # type: ignore[union-attr]
    assert env.prs == ["feature/T256"]
    assert not env.board.create_kai(env.delegator, tid, "t", "d", 1).ok  # closed to new KAIs


def test_events_record_every_move() -> None:
    env = Env()
    kid = env.kai(env.task())
    _to_review(env, kid)
    moves = [(e.source, e.target) for e in env.board.store.list_events() if e.kai_id == kid]
    assert moves == [("-", "a_fazer"), ("a_fazer", "fazendo"), ("fazendo", "revisando")]


def test_token_identifies_the_agent_and_only_the_hash_is_stored() -> None:
    env = Env()
    ex = env.agent("Mika", Role.EXECUTOR)
    token = env.board.issue_token(ex.agent_id).data  # type: ignore[arg-type]
    assert env.board.actor_for_token(token) == ex
    assert env.board.actor_for_token("errado") is None and env.board.actor_for_token("") is None
    assert token not in str(env.board.store.get_agent(ex.agent_id).__dict__)  # type: ignore[arg-type]
    new = env.board.issue_token(ex.agent_id).data  # type: ignore[arg-type]
    assert env.board.actor_for_token(token) is None and env.board.actor_for_token(new) == ex


def test_ended_agent_token_stops_working() -> None:
    env = Env()
    kid = env.kai(env.task())
    ex = _to_review(env, kid)
    token = env.board.issue_token(ex.agent_id).data  # type: ignore[arg-type]
    env.board.review(env.qa, kid, True, "ok")
    env.board.human_decision(HUMAN, kid, True)
    assert env.board.actor_for_token(token) is None


@pytest.mark.skipif(not os.environ.get("KANBAI_TEST_DATABASE_URL"), reason="needs Postgres")
def test_state_survives_a_new_connection() -> None:
    env = Env()
    kid = env.kai(env.task())
    _to_review(env, kid)
    from kanbai.pgstore import PgStore

    other = PgStore(os.environ["KANBAI_TEST_DATABASE_URL"])
    kai = other.get_kai(kid)
    assert kai.column == Column.REVIEWING and kai.comments[0].kind == CommentKind.REPORT  # type: ignore[union-attr]
    assert other.get_agent(kai.executor_id).state == AgentState.WORKING  # type: ignore[union-attr,arg-type]
    other.close()
