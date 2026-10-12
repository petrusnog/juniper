"""Tests for the legacy-command bridge (real git + zsh on throwaway repos, no network)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from brain.tools import juniper_cmds
from brain.tools.base import Tier
from brain.tools.juniper_cmds import GitDeploy, GitSearch, strip_ansi

pytestmark = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh necessário")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "Tester")
    return path


def commit(path: Path, message: str, filename: str) -> None:
    (path / filename).write_text(message)
    git(path, "add", filename)
    git(path, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = init_repo(tmp_path / "repo")
    commit(path, "feature(1339): login", "a.txt")
    commit(path, "hotfix(1339): corrige login", "b.txt")
    commit(path, "chore: outra coisa", "c.txt")
    monkeypatch.chdir(path)
    return path


def test_strip_ansi() -> None:
    assert strip_ansi("\x1b[92mHASH\x1b[0m x") == "HASH x"


def test_legacy_argv_keeps_arguments_separate() -> None:
    argv = juniper_cmds.legacy_argv(["search", "a; rm -rf ~", "$(x)"])
    assert argv[-2:] == ["a; rm -rf ~", "$(x)"]  # separate argv entries, never one shell string


def test_search_tool_metadata() -> None:
    assert GitSearch.tier is Tier.READ
    assert GitDeploy.tier is Tier.DESTRUCTIVE


async def test_search_finds_commits_without_ansi(repo: Path) -> None:
    result = await GitSearch().run(terms=["1339"])
    assert result.success and result.output
    assert "feature(1339): login" in result.output and "hotfix(1339)" in result.output
    assert "chore" not in result.output and "\x1b" not in result.output


async def test_search_multiple_terms_is_or(repo: Path) -> None:
    result = await GitSearch().run(terms=["login", "outra"])
    assert (
        result.output and "chore: outra coisa" in result.output and "feature(1339)" in result.output
    )


async def test_search_no_match_is_not_a_failure(repo: Path) -> None:
    result = await GitSearch().run(terms=["inexistente"])
    assert result.success and "Nenhum commit" in (result.output or "")


async def test_search_metacharacters_are_literal(repo: Path) -> None:
    result = await GitSearch().run(terms=["x; touch pwned", "$(touch pwned)", "`touch pwned`"])
    assert result.success
    assert not (repo / "pwned").exists()


@pytest.mark.parametrize("terms", [[], "1339", [""], [1], ["x"] * 11, ["a" * 201]])
async def test_search_rejects_bad_terms(repo: Path, terms: object) -> None:
    result = await GitSearch().run(terms=terms)
    assert not result.success


async def test_search_outside_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = await GitSearch().run(terms=["1339"])
    assert not result.success and "repositório git" in (result.error or "")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"feature_id": "13;rm"},
        {"feature_id": "$(x)"},
        {"feature_id": ""},
        {"feature_id": "1", "extra_tasks": ["a"]},
        {"feature_id": "1", "extra_tasks": "2"},
        {"feature_id": "1", "target": "prod"},
    ],
)
async def test_deploy_rejects_bad_arguments(repo: Path, kwargs: dict[str, object]) -> None:
    result = await GitDeploy().precheck(**kwargs)
    assert result is not None and not result.success


async def test_deploy_precheck_blocks_dirty_tree_and_unknown_feature(repo: Path) -> None:
    (repo / "a.txt").write_text("alterado")
    dirty = await GitDeploy().precheck(feature_id="1339")
    assert dirty is not None and "não commitadas" in (dirty.error or "")
    git(repo, "checkout", "--", "a.txt")
    missing = await GitDeploy().precheck(feature_id="9999")
    assert missing is not None and "nenhum commit" in (missing.error or "")
    assert await GitDeploy().precheck(feature_id="1339", extra_tasks=["9999"]) is None


def test_deploy_plan_builds_argv() -> None:
    plan = GitDeploy._plan({"feature_id": "6531", "extra_tasks": ["7432"], "target": "stage"})
    assert plan == (["deploy", "6531", "--tasks", "7432", "--stage"], ["6531", "7432"])
    assert GitDeploy._plan({"feature_id": "1"}) == (["deploy", "1"], ["1"])


async def test_deploy_end_to_end_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "-q", "--bare", "-b", "main")
    work = init_repo(tmp_path / "work")
    commit(work, "base", "base.txt")
    git(work, "remote", "add", "origin", str(remote))
    for branch in ("develop", "stage", "feature/42-develop", "feature/42-stage"):
        git(work, "branch", branch)
    git(
        work,
        "push",
        "-q",
        "origin",
        "main",
        "develop",
        "stage",
        "feature/42-develop",
        "feature/42-stage",
    )
    commit(work, "feature(42): nova tela", "tela.txt")
    monkeypatch.chdir(work)

    tool = GitDeploy()
    assert await tool.precheck(feature_id="42") is None
    result = await tool.run(feature_id="42", target="develop")
    assert result.success, result.error
    assert "nova tela" in git(remote, "log", "-1", "--format=%s", "feature/42-develop")
    assert git(work, "branch", "--show-current") == "main"  # goes back to the original branch

    again = await tool.run(feature_id="42", target="develop")
    assert again.success and "já está atualizada" in (again.output or "")
    assert git(remote, "rev-list", "--count", "feature/42-develop") == "2"  # no duplicate commit


async def test_deploy_never_blocks_waiting_for_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Branch that does not exist would prompt; with no terminal it must refuse and finish."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "-q", "--bare", "-b", "main")
    work = init_repo(tmp_path / "work")
    commit(work, "base", "base.txt")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "branch", "develop")
    git(work, "push", "-q", "origin", "main", "develop")
    commit(work, "feature(7): algo", "x.txt")
    monkeypatch.chdir(work)
    monkeypatch.setattr(juniper_cmds, "DEPLOY_TIMEOUT", 60.0)
    result = await GitDeploy().run(feature_id="7", target="develop")
    assert "recusada" in (result.output or result.error or "")


async def test_deploy_subjects_are_the_task_ids() -> None:
    assert await GitDeploy().subjects(feature_id="6531", extra_tasks=["7432"]) == ["6531", "7432"]
    assert await GitDeploy().subjects(feature_id="x;y") is None
