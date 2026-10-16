"""Infraestrutura de produção (T-080): o que dá para conferir sem Docker.

O build da imagem e o `compose up` de verdade são validados no CI (job `docker`); aqui ficam os
arquivos: a imagem não leva segredos nem roda como root, o compose segue o ADR-013 e a
configuração do contêiner carrega os valores esperados.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from brain.config import Settings

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
COMPOSE: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text("utf-8"))
CONFIG_PROD = ROOT / "docker" / "config.prod.yaml"


def mount_target(mount: str) -> str:
    """O destino de um volume `origem:destino[:ro]` (a origem pode ter `${VAR:-padrão}`)."""
    parts = mount.rsplit(":", 2) if mount.endswith((":ro", ":rw")) else mount.rsplit(":", 1)
    return parts[-2] if mount.endswith((":ro", ":rw")) else parts[-1]


def instructions(name: str) -> list[str]:
    return [
        line.split(None, 1)[1].strip()
        for line in DOCKERFILE.splitlines()
        if line.upper().startswith(f"{name} ")
    ]


# --- Dockerfile ---------------------------------------------------------------------------


def test_image_is_python_311_or_newer() -> None:
    (base,) = instructions("FROM")
    version = re.match(r"python:(\d+)\.(\d+)", base)
    assert version is not None and (int(version[1]), int(version[2])) >= (3, 11)


def test_container_runs_as_an_unprivileged_user() -> None:
    users = instructions("USER")
    assert users and users[-1] not in {"root", "0"}
    assert "useradd" in DOCKERFILE


def test_dependencies_come_from_requirements_txt() -> None:
    assert any("requirements.txt" in c for c in instructions("COPY"))
    assert any("pip install -r requirements.txt" in c for c in instructions("RUN"))


def test_no_secret_or_local_state_is_copied_into_the_image() -> None:
    copied = " ".join(instructions("COPY") + instructions("ADD"))
    for forbidden in (".env", "certs", "config.yaml", "token", "gcp-key", "sessions", ".git"):
        assert forbidden not in copied.replace("requirements.txt", ""), forbidden
    assert "ENV JUNIPER_" not in DOCKERFILE and "ARG JUNIPER_" not in DOCKERFILE
    assert not re.search(r"(api[_-]?key|password|secret)\s*=", DOCKERFILE, re.I)


def test_image_has_a_healthcheck_on_the_health_endpoint() -> None:
    assert "HEALTHCHECK" in DOCKERFILE and "/health" in DOCKERFILE


def test_dockerignore_keeps_secrets_and_clutter_out_of_the_build_context() -> None:
    for entry in (".env", ".git/", "tests/", "certs/", "config.yaml", "sessions/", "*.pem"):
        assert entry in DOCKERIGNORE, entry
    assert any("service-account" in line for line in DOCKERIGNORE)


# --- docker-compose.prod.yml --------------------------------------------------------------


def test_compose_has_brain_and_mongo() -> None:
    assert set(COMPOSE["services"]) == {"brain", "mongo"}
    assert COMPOSE["name"] == "juniper-prod"  # não encosta nos volumes do compose de dev


@pytest.mark.parametrize("service", ["brain", "mongo"])
def test_services_restart_unless_stopped_and_cap_their_logs(service: str) -> None:
    config = COMPOSE["services"][service]
    assert config["restart"] == "unless-stopped"
    options = config["logging"]["options"]
    assert options["max-size"] and options["max-file"]


def test_brain_port_is_never_published_on_all_interfaces() -> None:
    (port,) = COMPOSE["services"]["brain"]["ports"]
    assert port == "${TAILSCALE_IP:-127.0.0.1}:8765:8765"  # o padrão é o loopback, nunca 0.0.0.0


def test_mongo_is_not_reachable_from_outside_the_compose_network() -> None:
    mongo = COMPOSE["services"]["mongo"]
    for port in mongo.get("ports", []):
        assert str(port).startswith("127.0.0.1:")


def test_brain_waits_for_a_healthy_mongo_and_has_its_own_healthcheck() -> None:
    brain = COMPOSE["services"]["brain"]
    assert brain["depends_on"]["mongo"]["condition"] == "service_healthy"
    assert "/health" in " ".join(brain["healthcheck"]["test"])
    assert "mongosh" in " ".join(COMPOSE["services"]["mongo"]["healthcheck"]["test"])


def test_named_volumes_hold_the_state() -> None:
    assert {"juniper-mongo-data", "juniper-state", "juniper-logs", "juniper-models"} <= set(
        COMPOSE["volumes"]
    )
    mounts = COMPOSE["services"]["brain"]["volumes"]
    targets = {mount_target(m) for m in mounts}
    assert "/home/juniper/.juniper/models" in targets  # o modelo de embeddings não vai na imagem
    assert "juniper-mongo-data:/data/db" in COMPOSE["services"]["mongo"]["volumes"]


def test_secrets_are_mounted_read_only_and_never_written_into_the_compose_file() -> None:
    mounts = COMPOSE["services"]["brain"]["volumes"]
    for needle in (".env", "certs", "config"):
        (mount,) = (m for m in mounts if needle in m.split(":/")[0])
        assert mount.endswith(":ro"), mount
    text = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for service in COMPOSE["services"].values():
        for key, value in service.get("environment", {}).items():
            if re.search(r"PASSWORD|API_KEY|TOKEN|SECRET", key):
                assert str(value).startswith("${"), key  # só referência, nunca o valor
    assert ":?" in text  # a senha do Mongo é obrigatória


def test_image_is_tagged_by_a_variable_so_deploys_can_pin_a_sha() -> None:
    assert COMPOSE["services"]["brain"]["image"].startswith("${JUNIPER_IMAGE:-")


# --- configuração do contêiner ------------------------------------------------------------


@pytest.fixture
def prod_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(CONFIG_PROD))
    for var in ("JUNIPER_DEPLOYMENT__MODE", "JUNIPER_MEMORY__SESSION__STORE"):
        monkeypatch.delenv(var, raising=False)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_production_config_loads_the_expected_values(prod_settings: Settings) -> None:
    assert prod_settings.deployment.mode == "server"
    assert prod_settings.deployment.server.host == "0.0.0.0"
    assert prod_settings.deployment.server.port == 8765
    assert prod_settings.deployment.auth.token_file == Path("/home/juniper/.juniper/state/token")
    assert prod_settings.memory.session.store == "mongo"
    assert prod_settings.memory.mongo.uri == "mongodb://mongo:27017"
    assert prod_settings.llm.priority == ["deepinfra", "groq"]


def test_production_config_has_no_secrets(prod_settings: Settings) -> None:
    assert prod_settings.memory.mongo.password is None
    assert prod_settings.memory.mongo.username is None
    assert prod_settings.groq_api_key is None
    text = CONFIG_PROD.read_text(encoding="utf-8").lower()
    assert "password" not in yaml.safe_load(text).get("memory", {}).get("mongo", {})
    assert "api_key" not in text


def test_secrets_reach_the_container_through_the_mounted_env_file() -> None:
    """O brain lê ~/.juniper/.env: é onde o compose monta o .env do servidor."""
    mounts = COMPOSE["services"]["brain"]["volumes"]
    assert any(mount_target(m) == "/home/juniper/.juniper/.env" for m in mounts)
    env = Settings.model_config["env_file"]
    assert isinstance(env, tuple) and str(env[0]).endswith("/.juniper/.env")
