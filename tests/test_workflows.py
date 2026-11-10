"""The delete-branch workflow: what it may touch and how it handles the branch name."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "delete-branch.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_runs_only_when_a_pr_into_master_closes() -> None:
    triggers = _load()[True]  # YAML 1.1 reads the key `on` as the boolean True
    assert list(triggers) == ["pull_request"]
    assert triggers["pull_request"] == {"types": ["closed"], "branches": ["master"]}


def test_only_acts_on_merged_branches_of_this_repository() -> None:
    condition = _load()["jobs"]["delete"]["if"]
    assert "pull_request.merged == true" in condition
    assert "head.repo.full_name == github.repository" in condition  # never a fork
    assert "default_branch" in condition  # never the default branch


def test_needs_only_the_permission_to_delete_a_ref() -> None:
    assert _load()["permissions"] == {"contents": "write"}


def test_branch_name_never_reaches_the_shell_by_interpolation() -> None:
    steps = _load()["jobs"]["delete"]["steps"]
    for step in steps:
        script = step.get("run", "")
        assert "${{" not in script, "put the value in env: and read it as a shell variable"
    delete = steps[0]
    assert delete["env"]["BRANCH"] == "${{ github.event.pull_request.head.ref }}"
    assert 'safe="/"' in delete["run"]  # feature/T203 keeps its slash in the ref path
