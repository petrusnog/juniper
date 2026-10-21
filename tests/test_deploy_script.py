"""O deploy da VPS (scripts/deploy.sh) e o workflow que o chama, sem Docker nem GitHub.

O script roda de verdade contra `git` e `docker` falsos: dá para ver a troca de imagem, o backup, o
rollback quando o brain não fica saudável e a recusa de entradas ruins.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy.sh"
WORKFLOW: dict[Any, Any] = yaml.safe_load(
    (ROOT / ".github/workflows/deploy.yml").read_text("utf-8")
)
GOOD = "a" * 40
BAD = "b" * 40

FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  inspect)  # saudável só se o .env aponta para uma imagem que não é a "ruim"
    if grep -q "^JUNIPER_IMAGE=.*${BAD_TAG}" .env; then echo unhealthy; else echo healthy; fi ;;
  compose)
    case "$*" in
      *" ps "*) [ -n "${MONGO_UP:-}" ] && echo mongo ;;
      *" exec "*) [ -n "${DUMP_FAILS:-}" ] && exit 1; echo "fake-dump" ;;
    esac ;;
esac
exit 0
"""


@pytest.fixture
def box(tmp_path: Path) -> Path:
    """Uma pasta ~/juniper de mentira com o script, um .env e `git`/`docker` falsos no PATH."""
    work = tmp_path / "juniper"
    (work / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, work / "scripts" / "deploy.sh")
    (work / "docker-compose.prod.yml").write_text("services: {}\n")
    (work / ".env").write_text("JUNIPER_IMAGE=ghcr.io/x/juniper:old\nSEGREDO=1\n")
    (work / ".env").chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "docker": FAKE_DOCKER,
        "git": '#!/usr/bin/env bash\necho "git $*" >> "$FAKE_LOG"\n',
        "flock": "#!/usr/bin/env bash\nexit 0\n",
    }.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    return work


def run(box: Path, sha: str, **env: str) -> subprocess.CompletedProcess[str]:
    log = box.parent / "calls.log"
    environment = {
        **os.environ,
        "PATH": f"{box.parent / 'bin'}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "BAD_TAG": BAD,
        "HEALTH_TRIES": "2",
        "HEALTH_INTERVAL": "0",
        "JUNIPER_REPO_IMAGE": "ghcr.io/x/juniper",
        **env,
    }
    return subprocess.run(
        ["bash", str(box / "scripts" / "deploy.sh"), sha],
        cwd=box,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def calls(box: Path) -> str:
    log = box.parent / "calls.log"
    return log.read_text() if log.exists() else ""


def image_in_env(box: Path) -> str:
    return next(
        line.split("=", 1)[1]
        for line in (box / ".env").read_text().splitlines()
        if line.startswith("JUNIPER_IMAGE=")
    )


def test_the_script_is_valid_bash_and_executable() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize(
    "sha", ["", "abc", "main", "../../etc/passwd", "a" * 39, "A" * 40, f"{GOOD};ls"]
)
def test_anything_but_a_full_sha_is_refused_before_touching_anything(box: Path, sha: str) -> None:
    result = run(box, sha)
    assert result.returncode == 2
    assert calls(box) == "" and image_in_env(box).endswith(":old")


def test_a_healthy_deploy_pins_the_new_image_and_keeps_the_env_private(box: Path) -> None:
    result = run(box, GOOD)

    assert result.returncode == 0, result.stderr
    assert image_in_env(box) == f"ghcr.io/x/juniper:{GOOD}"
    assert "SEGREDO=1" in (box / ".env").read_text()
    assert stat.S_IMODE((box / ".env").stat().st_mode) == 0o600
    log = calls(box)
    assert f"git fetch -q origin {GOOD}" in log and f"git reset -q --hard {GOOD}" in log
    assert "pull brain" in log and "up -d" in log
    assert "-v" not in [a for line in log.splitlines() if "compose" in line for a in line.split()]


def test_an_unhealthy_brain_rolls_back_to_the_previous_image_and_fails(box: Path) -> None:
    result = run(box, BAD)

    assert result.returncode == 1
    assert image_in_env(box) == "ghcr.io/x/juniper:old"
    assert "voltando para ghcr.io/x/juniper:old" in result.stderr and "rollback ok" in result.stderr
    assert calls(box).count("up -d") == 2  # o novo e o de volta


def test_the_mongo_is_dumped_before_the_swap_and_old_dumps_are_pruned(box: Path) -> None:
    backups = box / "backups"
    backups.mkdir()
    for n in range(7):
        old = backups / f"mongo-2026010{n}-000000.archive.gz"
        old.write_text("x")
        os.utime(old, (n + 1, n + 1))

    result = run(box, GOOD, MONGO_UP="1")

    assert result.returncode == 0, result.stderr
    dumps = sorted(backups.glob("mongo-*.archive.gz"))
    assert len(dumps) == 5
    fresh = max(dumps, key=lambda p: p.stat().st_mtime)
    assert fresh.read_text().strip() == "fake-dump" and stat.S_IMODE(fresh.stat().st_mode) == 0o600
    log = calls(box)
    assert log.index("exec -T mongo") < log.index("pull brain")


def test_a_failed_backup_cancels_the_deploy_without_changing_the_image(box: Path) -> None:
    result = run(box, GOOD, MONGO_UP="1", DUMP_FAILS="1")

    assert result.returncode == 1
    assert "backup do Mongo falhou" in result.stderr
    assert image_in_env(box).endswith(":old")
    assert "pull brain" not in calls(box)
    assert not list((box / "backups").glob("*.archive.gz"))


def test_without_a_running_mongo_there_is_no_backup_and_the_deploy_goes_on(box: Path) -> None:
    result = run(box, GOOD)
    assert result.returncode == 0 and "sem backup" in result.stdout


# --- workflow ---------------------------------------------------------------------------------


def test_deploy_starts_only_after_tests_pass_on_master_or_by_hand() -> None:
    triggers = WORKFLOW[True]
    assert triggers["workflow_run"] == {
        "workflows": ["Tests"],
        "types": ["completed"],
        "branches": ["master"],
    }
    assert "workflow_dispatch" in triggers
    condition = WORKFLOW["jobs"]["build"]["if"]
    assert "workflow_run.conclusion == 'success'" in condition


def test_one_deploy_at_a_time_and_nothing_is_cancelled_midway() -> None:
    assert WORKFLOW["concurrency"] == {"group": "deploy", "cancel-in-progress": False}


def test_the_workflow_asks_for_the_least_permissions() -> None:
    assert WORKFLOW["permissions"] == {"contents": "read", "packages": "write"}


def test_the_image_is_the_prod_target_tagged_with_the_commit_sha() -> None:
    steps = WORKFLOW["jobs"]["build"]["steps"]
    push = next(s for s in steps if str(s.get("uses", "")).startswith("docker/build-push-action"))
    assert push["with"]["target"] == "prod" and push["with"]["push"] is True
    assert push["with"]["tags"].endswith(":${{ steps.commit.outputs.sha }}")


def test_the_runner_joins_the_tailnet_as_tag_ci_and_uses_only_the_documented_secrets() -> None:
    text = (ROOT / ".github/workflows/deploy.yml").read_text("utf-8")
    steps = WORKFLOW["jobs"]["deploy"]["steps"]
    tailscale = next(
        s for s in steps if str(s.get("uses", "")).startswith("tailscale/github-action")
    )
    assert tailscale["with"]["tags"] == "tag:ci"
    used = {part.split("}}")[0].strip() for part in text.split("secrets.")[1:]}
    assert used == {
        "GITHUB_TOKEN",
        "TS_OAUTH_CLIENT_ID",
        "TS_OAUTH_SECRET",
        "DEPLOY_SSH_KEY",
        "DEPLOY_KNOWN_HOSTS",
        "DEPLOY_HOST",
        "DEPLOY_USER",
    }


def test_ssh_checks_the_server_identity_and_secrets_never_go_into_the_command_line() -> None:
    run_steps = [s["run"] for s in WORKFLOW["jobs"]["deploy"]["steps"] if "run" in s]
    joined = "\n".join(run_steps)
    assert "StrictHostKeyChecking=yes" in joined
    assert "secrets." not in joined  # os segredos entram por `env:`, não colados no script
    assert "set -x" not in joined
