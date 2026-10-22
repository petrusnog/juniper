"""Backup da memória (scripts/backup_memory.sh e restore_memory.sh), sem Docker.

O script roda de verdade contra um `git` real (um repositório "remoto" bare no tmp) e o `age` real;
só o `docker` é falso: ele devolve bytes de mentira no lugar do mongodump e guarda o que o
mongorestore recebeu. Assim dá para ver a criptografia, o versionamento e a restauração.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup_memory.sh"
RESTORE = ROOT / "scripts" / "restore_memory.sh"

needs_age = pytest.mark.skipif(shutil.which("age") is None, reason="age não instalado")

FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$*" in
  *" ps "*) [ -n "${MONGO_UP:-1}" ] && [ "${MONGO_UP:-1}" != "0" ] && echo mongo ;;
  *mongodump*) [ -n "${DUMP_FAILS:-}" ] && exit 1; printf '%s' "${FAKE_DUMP:-DUMP-v1}" ;;
  *mongorestore*) cat > "$FAKE_RESTORED" ;;
esac
exit 0
"""


def sh(
    *args: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture
def box(tmp_path: Path) -> Path:
    """Uma pasta ~/juniper de mentira com os scripts, um .env e `docker` falso no PATH."""
    work = tmp_path / "juniper"
    (work / "scripts").mkdir(parents=True)
    for script in (BACKUP, RESTORE):
        shutil.copy(script, work / "scripts" / script.name)
    (work / "docker-compose.prod.yml").write_text("services: {}\n")
    (work / ".env").write_text("JUNIPER_IMAGE=ghcr.io/x/juniper:abc123\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    return work


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    sh("git", "init", "-q", "--bare", "-b", "main", str(path), cwd=tmp_path)
    return path


@pytest.fixture
def keys(tmp_path: Path) -> tuple[Path, str]:
    """(arquivo da chave privada, chave pública) de uma identidade age de teste."""
    identity = tmp_path / "backup.key"
    sh("age-keygen", "-o", str(identity), cwd=tmp_path)
    public = sh("age-keygen", "-y", str(identity), cwd=tmp_path).stdout.strip()
    return identity, public


def env_for(box: Path, **extra: str) -> dict[str, str]:
    return {
        "PATH": f"{box.parent / 'bin'}:{os.environ['PATH']}",
        "FAKE_LOG": str(box.parent / "calls.log"),
        "FAKE_RESTORED": str(box.parent / "restored.bin"),
        **extra,
    }


def backup(box: Path, reason: str = "manual", **extra: str) -> subprocess.CompletedProcess[str]:
    return sh("bash", "scripts/backup_memory.sh", reason, cwd=box, env=env_for(box, **extra))


def restore(box: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return sh("bash", "scripts/restore_memory.sh", *args, cwd=box, env=env_for(box, **extra))


def configure(box: Path, remote: Path, recipient: str = "") -> None:
    lines = [f"BACKUP_REMOTE={remote}"]
    if recipient:
        lines.append(f"BACKUP_AGE_RECIPIENT={recipient}")
    (box / "backup.env").write_text("\n".join(lines) + "\n")


def commits(remote: Path) -> list[str]:
    out = sh("git", "log", "--format=%s", "main", cwd=remote).stdout
    return out.splitlines()


def blob(remote: Path, ref: str, name: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{ref}:{name}"], cwd=remote, capture_output=True, check=True
    ).stdout


# --- sem configuração: só a cópia local -------------------------------------------------------


def test_without_backup_env_only_a_private_local_copy_is_made(box: Path) -> None:
    result = backup(box)

    assert result.returncode == 0, result.stderr
    assert "AVISO" in result.stderr and "BACKUP_REMOTE" in result.stderr
    (copy,) = (box / "backups" / "local").glob("memory-*")
    assert copy.read_bytes() == b"DUMP-v1" and stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert not (box / "backups" / "repo").exists()


def test_without_a_running_mongo_there_is_nothing_to_copy(box: Path) -> None:
    result = backup(box, MONGO_UP="0")
    assert result.returncode == 0 and "nada a copiar" in result.stdout
    assert not (box / "backups").exists()


def test_a_failed_dump_stops_everything(box: Path, remote: Path, keys: tuple[Path, str]) -> None:
    configure(box, remote, keys[1])

    result = backup(box, DUMP_FAILS="1")

    assert result.returncode == 1 and "mongodump falhou" in result.stderr
    assert not list((box / "backups" / "local").glob("memory-*"))
    assert commits(remote) == [] or commits(remote) == [""]


def test_the_local_copies_are_capped_at_five(box: Path) -> None:
    for n in range(7):
        assert backup(box, FAKE_DUMP=f"dump-{n}").returncode == 0
        for old in (box / "backups" / "local").glob("memory-*"):
            os.utime(old, (1_000_000 + n, 1_000_000 + n))  # distinct ages even within one second
    assert len(list((box / "backups" / "local").glob("memory-*"))) == 5


# --- criptografado e versionado ---------------------------------------------------------------


@needs_age
def test_the_memory_goes_to_github_only_encrypted(
    box: Path, remote: Path, keys: tuple[Path, str]
) -> None:
    identity, public = keys
    configure(box, remote, public)

    result = backup(box, "pre-deploy", FAKE_DUMP="Petrus conversou sobre o dentista")

    assert result.returncode == 0, result.stderr
    assert (
        commits(remote)[0].startswith("backup (pre-deploy) ")
        and "imagem abc123" in commits(remote)[0]
    )
    stored = blob(remote, "main", "juniper-memory.archive.gz.age")
    assert b"dentista" not in stored and stored.startswith(b"age-encryption.org")
    plain = subprocess.run(
        ["age", "-d", "-i", str(identity)], input=stored, capture_output=True, check=True
    ).stdout
    assert plain == b"Petrus conversou sobre o dentista"


@needs_age
def test_every_backup_is_a_new_version_of_the_same_file(
    box: Path, remote: Path, keys: tuple[Path, str]
) -> None:
    identity, public = keys
    configure(box, remote, public)

    backup(box, "daily", FAKE_DUMP="versao-1")
    backup(box, "daily", FAKE_DUMP="versao-2")

    assert len(commits(remote)) == 2
    tree = sh("git", "ls-tree", "--name-only", "main", cwd=remote).stdout.split()
    assert tree == ["juniper-memory.archive.gz.age"]
    old = subprocess.run(
        ["age", "-d", "-i", str(identity)],
        input=blob(remote, "main~1", "juniper-memory.archive.gz.age"),
        capture_output=True,
        check=True,
    ).stdout
    assert old == b"versao-1"


@needs_age
def test_a_push_failure_keeps_the_local_copy_and_signals_it(
    box: Path, remote: Path, keys: tuple[Path, str]
) -> None:
    configure(box, remote, keys[1])
    assert backup(box).returncode == 0
    shutil.rmtree(remote)  # o GitHub "saiu do ar"

    result = backup(box, FAKE_DUMP="depois-da-queda")

    assert result.returncode == 4 and "push falhou" in result.stderr
    assert len(list((box / "backups" / "local").glob("memory-*"))) == 2


@needs_age
def test_a_missing_age_binary_stops_instead_of_sending_plaintext(
    box: Path, remote: Path, keys: tuple[Path, str]
) -> None:
    configure(box, remote, keys[1])
    no_age = box.parent / "bin-no-age"
    no_age.mkdir()
    for tool in (
        "bash",
        "git",
        "grep",
        "mktemp",
        "install",
        "cp",
        "chmod",
        "ls",
        "tail",
        "xargs",
        "du",
        "cut",
        "date",
        "rm",
        "dirname",
        "cat",
        "head",
        "sh",
        "tee",
    ):
        found = shutil.which(tool)
        if found:
            (no_age / tool).symlink_to(found)
    (no_age / "docker").write_text(FAKE_DOCKER)
    (no_age / "docker").chmod(0o755)

    result = sh(
        str(Path(shutil.which("bash") or "bash")),
        "scripts/backup_memory.sh",
        cwd=box,
        env={"PATH": str(no_age), "FAKE_LOG": str(box.parent / "calls.log")},
    )

    assert result.returncode == 1 and "age" in result.stderr
    assert commits(remote) in ([], [""])


# --- sem criptografia: só se pedirem ----------------------------------------------------------


def test_without_a_recipient_nothing_leaves_the_server(box: Path, remote: Path) -> None:
    configure(box, remote)  # remoto sim, chave age não

    result = backup(box)

    assert result.returncode == 0 and "sem BACKUP_AGE_RECIPIENT" in result.stderr
    assert commits(remote) in ([], [""])
    assert list((box / "backups" / "local").glob("memory-*"))


def test_plaintext_is_sent_only_with_the_explicit_opt_in(box: Path, remote: Path) -> None:
    configure(box, remote)

    result = backup(box, BACKUP_ALLOW_PLAINTEXT="1")

    assert result.returncode == 0, result.stderr
    assert blob(remote, "main", "juniper-memory.archive.gz") == b"DUMP-v1"


@needs_age
def test_moving_from_plaintext_to_encrypted_removes_the_plaintext_file(
    box: Path, remote: Path, keys: tuple[Path, str]
) -> None:
    configure(box, remote)
    backup(box, BACKUP_ALLOW_PLAINTEXT="1")
    configure(box, remote, keys[1])

    backup(box)

    tree = sh("git", "ls-tree", "--name-only", "main", cwd=remote).stdout.split()
    assert tree == ["juniper-memory.archive.gz.age"]


def test_the_backup_files_never_enter_the_projects_git() -> None:
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "backups/" in ignored and "backup.env" in ignored
    dockerignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert "backups/" in dockerignored and "backup.env" in dockerignored


# --- restauração ------------------------------------------------------------------------------


@needs_age
def test_restore_decrypts_a_chosen_version_into_mongorestore(
    box: Path, remote: Path, keys: tuple[Path, str]
) -> None:
    identity, public = keys
    configure(box, remote, public)
    backup(box, FAKE_DUMP="ontem")
    backup(box, FAKE_DUMP="hoje")
    first = commits(remote)  # newest first
    first_sha = sh("git", "rev-parse", "main~1", cwd=remote).stdout.strip()
    assert len(first) == 2

    latest = restore(box, "--identity", str(identity), "--yes")
    assert latest.returncode == 0, latest.stderr
    assert (box.parent / "restored.bin").read_bytes() == b"hoje"

    older = restore(box, "--identity", str(identity), "--commit", first_sha, "--yes")
    assert older.returncode == 0, older.stderr
    assert (box.parent / "restored.bin").read_bytes() == b"ontem"
    calls = (box.parent / "calls.log").read_text()
    assert calls.index("stop brain") < calls.index("mongorestore") < calls.index("up -d brain")
    assert "--nsInclude 'juniper.*'" in calls and "--drop" in calls


@needs_age
def test_restore_does_nothing_without_yes_or_without_the_key(
    box: Path, remote: Path, keys: tuple[Path, str]
) -> None:
    identity, public = keys
    configure(box, remote, public)
    backup(box)

    no_yes = restore(box, "--identity", str(identity))
    assert no_yes.returncode == 2 and "--yes" in no_yes.stderr
    no_key = restore(box, "--yes")
    assert no_key.returncode == 2 and "--identity" in no_key.stderr
    assert not (box.parent / "restored.bin").exists()
    assert "stop brain" not in (box.parent / "calls.log").read_text()


def test_restore_refuses_when_there_is_no_backup_repository(box: Path) -> None:
    result = restore(box, "--yes")
    assert result.returncode == 2 and "repositório de backup" in result.stderr
