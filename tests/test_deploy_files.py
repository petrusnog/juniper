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

from brain.config import GoogleSettings, Settings

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


def stage(name: str) -> str:
    """O texto de um estágio do Dockerfile (`FROM ... AS <name>` até o próximo `FROM`)."""
    parts = re.split(r"^FROM ", DOCKERFILE, flags=re.M)[1:]
    (text,) = (p for p in parts if re.match(rf"\S+ AS {name}\b", p))
    return text


def test_image_is_python_311_or_newer() -> None:
    bases = [b for b in instructions("FROM") if not b.endswith(("AS base", "AS dev", "AS prod"))]
    assert bases == []  # todo estágio sai de `python:...` (base) ou de outro estágio nosso
    version = re.match(r"python:(\d+)\.(\d+)", instructions("FROM")[0])
    assert version is not None and (int(version[1]), int(version[2])) >= (3, 11)


def test_prod_is_the_default_target() -> None:
    assert re.findall(r"^FROM .* AS (\w+)", DOCKERFILE, flags=re.M)[-1] == "prod"
    assert COMPOSE["services"]["brain"]["build"]["target"] == "prod"


def test_prod_carries_the_code_and_dev_does_not() -> None:
    assert "COPY brain" in stage("prod") and "COPY prompts" in stage("prod")
    assert "COPY brain" not in stage("dev") and "COPY prompts" not in stage("dev")
    assert "watchfiles" in stage("dev") and "watchfiles" not in stage("prod")
    assert "USER juniper" in stage("dev") and "USER juniper" in stage("prod")


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
    assert set(COMPOSE["services"]) == {"brain", "mongo", "telegram"}
    assert COMPOSE["name"] == "juniper-prod"  # não encosta nos volumes do compose de dev


@pytest.mark.parametrize("service", ["brain", "mongo"])
def test_services_restart_unless_stopped_and_cap_their_logs(service: str) -> None:
    config = COMPOSE["services"][service]
    assert config["restart"] == "unless-stopped"
    options = config["logging"]["options"]
    assert options["max-size"] and options["max-file"]


def test_brain_port_is_never_published_on_all_interfaces() -> None:
    ports = COMPOSE["services"]["brain"]["ports"]
    # o padrão é o loopback, nunca 0.0.0.0: o servidor (8765) e o dashboard (8766, ADR-020)
    assert ports == [
        "${TAILSCALE_IP:-127.0.0.1}:8765:8765",
        "${TAILSCALE_IP:-127.0.0.1}:8766:8766",
    ]


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
    assert {
        "juniper-mongo-data",
        "juniper-state",
        "juniper-logs",
        "juniper-models",
        "juniper-music",
    } <= set(COMPOSE["volumes"])
    mounts = COMPOSE["services"]["brain"]["volumes"]
    targets = {mount_target(m) for m in mounts}
    assert "/home/juniper/.juniper/models" in targets  # o modelo de embeddings não vai na imagem
    assert "juniper-mongo-data:/data/db" in COMPOSE["services"]["mongo"]["volumes"]


def test_the_music_state_is_a_named_volume_not_a_single_file_mount() -> None:
    """The login token is replaced by a rename, which a single-file bind mount cannot do; and
    without a volume the token and the quota counter would vanish with every new container."""
    mounts = COMPOSE["services"]["brain"]["volumes"]
    [music] = [m for m in mounts if mount_target(m) == "/home/juniper/.juniper/music"]
    assert str(music).startswith("juniper-music:")  # a named volume, not a host file path
    # both image stages create the directory, so a new named volume gets the right owner
    assert DOCKERFILE.count("/home/juniper/.juniper/music") == 2


def test_secrets_are_mounted_read_only_and_never_written_into_the_compose_file() -> None:
    mounts = COMPOSE["services"]["brain"]["volumes"]
    for target in (".env", "certs", "config.yaml", "config.base.yaml"):
        (mount,) = (m for m in mounts if mount_target(m).endswith("/.juniper/" + target))
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


# --- docker-compose.dev.yml ---------------------------------------------------------------

DEV: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text("utf-8"))
CONFIG_DEV = ROOT / "docker" / "config.dev.yaml"


def test_dev_has_the_same_topology_as_prod_plus_a_fake_llm() -> None:
    assert set(DEV["services"]) == (
        set(COMPOSE["services"]) - {"telegram"}  # o bot é opt-in na produção; no dev roda no host
    ) | {"fake-llm", "fix-perms"}
    assert DEV["name"] == "juniper-dev" != COMPOSE["name"]
    assert DEV["services"]["mongo"]["image"] == COMPOSE["services"]["mongo"]["image"]
    assert (
        DEV["services"]["brain"]["healthcheck"]["test"]
        == (COMPOSE["services"]["brain"]["healthcheck"]["test"])
    )


def test_dev_does_not_share_volumes_with_prod() -> None:
    assert not set(DEV["volumes"]) & set(COMPOSE["volumes"])
    dev_mounts = " ".join(DEV["services"]["brain"]["volumes"])
    for prod_volume in COMPOSE["volumes"]:
        assert prod_volume not in dev_mounts


def test_dev_mounts_the_code_so_edits_show_up_without_a_rebuild() -> None:
    mounts = DEV["services"]["brain"]["volumes"]
    assert "./brain:/app/brain:ro" in mounts and "./prompts:/app/prompts:ro" in mounts
    assert DEV["services"]["brain"]["build"]["target"] == "dev"


def test_dev_secrets_and_config_are_read_only_and_never_inline() -> None:
    mounts = DEV["services"]["brain"]["volumes"]
    for needle in (".env", "certs", "config"):
        matching = [m for m in mounts if needle in m.split(":/")[0]]
        assert matching, needle
        assert all(m.endswith(":ro") for m in matching), matching
    for service in DEV["services"].values():
        for key, value in service.get("environment", {}).items():
            if re.search(r"PASSWORD|API_KEY|TOKEN|SECRET", key):
                assert str(value).startswith("${"), key


def test_dev_ports_are_loopback_only() -> None:
    for name in ("brain", "mongo"):
        for port in DEV["services"][name]["ports"]:
            assert port.startswith("127.0.0.1:"), port
    assert "ports" not in DEV["services"]["fake-llm"]


def test_dev_state_lives_in_a_git_and_docker_ignored_folder() -> None:
    assert ".juniper-dev/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".juniper-dev/" in DOCKERIGNORE
    mounts = DEV["services"]["brain"]["volumes"]
    assert any(m.startswith("./.juniper-dev/state:") for m in mounts)


def test_dev_config_loads_and_ends_the_failover_in_the_fake_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(CONFIG_DEV))
    for var in (
        "JUNIPER_DEPLOYMENT__MODE",
        "JUNIPER_MEMORY__SESSION__STORE",
        "JUNIPER_LLM__PRIORITY",
    ):
        monkeypatch.delenv(var, raising=False)
    config = Settings(_env_file=None)  # type: ignore[call-arg]
    assert config.deployment.mode == "server" and config.memory.session.store == "mongo"
    assert config.memory.mongo.uri == "mongodb://mongo:27017"
    assert config.llm.priority[-1] == "ollama"
    assert config.llm.ollama.url == "http://fake-llm:11999"
    assert config.memory.mongo.password is None and config.groq_api_key is None
    prod = yaml.safe_load(CONFIG_PROD.read_text("utf-8"))
    dev = yaml.safe_load(CONFIG_DEV.read_text("utf-8"))
    assert dev["deployment"] == prod["deployment"] and dev["memory"] == prod["memory"]


def test_dev_brain_gets_the_users_config_as_a_base_layer() -> None:
    """A agenda do Google etc. vêm do seu config.yaml; a infraestrutura, do config.dev.yaml."""
    brain = DEV["services"]["brain"]
    base = "/home/juniper/.juniper/config.base.yaml"
    assert brain["environment"]["JUNIPER_BASE_CONFIG_PATH"] == base
    (mount,) = (m for m in brain["volumes"] if m.split(":/")[1].startswith(base.lstrip("/")))
    assert mount.startswith("${JUNIPER_BASE_CONFIG_FILE:-./config.yaml}:") and mount.endswith(":ro")


def test_dev_fixes_the_owner_of_the_state_folders_before_the_brain_starts() -> None:
    """Pasta criada pelo Docker é do root: sem isto o brain não grava o token."""
    fix = DEV["services"]["fix-perms"]
    assert fix["restart"] == "no" and "chown" in fix["command"]
    assert DEV["services"]["brain"]["depends_on"]["fix-perms"] == {
        "condition": "service_completed_successfully"
    }
    state_mounts = {m for m in DEV["services"]["brain"]["volumes"] if ".juniper-dev/" in m}
    for mount in state_mounts:  # as mesmas pastas do brain
        assert mount.split(":")[0] in " ".join(fix["volumes"])


# --- T-094: camada de configuração pessoal no servidor --------------------------------------

BASE_CONFIG = ROOT / "docker" / "config.base.yaml"


def test_prod_brain_gets_the_personal_config_as_a_read_only_base_layer() -> None:
    brain = COMPOSE["services"]["brain"]
    target = "/home/juniper/.juniper/config.base.yaml"
    assert brain["environment"]["JUNIPER_BASE_CONFIG_PATH"] == target
    (mount,) = (m for m in brain["volumes"] if mount_target(m) == target)
    # O padrão é um arquivo versionado: sem ele o Docker criaria uma PASTA (e root) no lugar.
    assert mount.startswith("${JUNIPER_BASE_CONFIG_FILE:-./docker/config.base.yaml}:")
    assert mount.endswith(":ro") and BASE_CONFIG.is_file()


def test_the_default_base_layer_is_an_empty_mapping_and_adds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert yaml.safe_load(BASE_CONFIG.read_text(encoding="utf-8")) == {}
    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(CONFIG_PROD))
    monkeypatch.setenv("JUNIPER_BASE_CONFIG_PATH", str(BASE_CONFIG))
    for var in ("JUNIPER_DEPLOYMENT__MODE", "JUNIPER_MEMORY__SESSION__STORE"):
        monkeypatch.delenv(var, raising=False)
    loaded = Settings(_env_file=None)  # type: ignore[call-arg]
    assert loaded.deployment.mode == "server" and loaded.google.calendar_id is None


def test_personal_config_completes_production_but_never_overrides_the_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    personal = tmp_path / "config.yaml"
    personal.write_text(
        "deployment:\n  mode: local\n  server:\n    host: 127.0.0.1\n"
        "memory:\n  session:\n    store: file\n"
        "llm:\n  priority: [ollama]\n"
        "google:\n  calendar_id: juniper@group.calendar.google.com\n"
        "  manage_calendar_ids: [eu@exemplo.com]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(CONFIG_PROD))
    monkeypatch.setenv("JUNIPER_BASE_CONFIG_PATH", str(personal))
    for var in ("JUNIPER_DEPLOYMENT__MODE", "JUNIPER_MEMORY__SESSION__STORE"):
        monkeypatch.delenv(var, raising=False)
    loaded = Settings(_env_file=None)  # type: ignore[call-arg]
    assert loaded.deployment.mode == "server"  # a produção manda
    assert loaded.deployment.server.host == "0.0.0.0"
    assert loaded.memory.session.store == "mongo"
    assert loaded.llm.priority == ["deepinfra", "groq"]
    assert loaded.google.calendar_id == "juniper@group.calendar.google.com"  # o pessoal completa
    assert loaded.google.manage_calendar_ids == ["eu@exemplo.com"]


def test_google_key_comes_from_the_read_only_certs_mount() -> None:
    """A chave da conta de serviço fica em ~/.juniper/certs, onde o Settings a procura."""
    mounts = COMPOSE["services"]["brain"]["volumes"]
    (certs,) = (m for m in mounts if mount_target(m) == "/home/juniper/.juniper/certs")
    assert certs.endswith(":ro")
    key = GoogleSettings().credentials_file
    assert (key.parent.name, key.name) == ("certs", "gcp-key.json")


# --- T-096: o bot do Telegram como serviço opt-in -------------------------------------------

BOT = COMPOSE["services"]["telegram"]


def test_the_telegram_bot_is_opt_in_so_deploys_without_it_are_unchanged() -> None:
    assert BOT["profiles"] == ["telegram"]  # só sobe com `--profile telegram`/COMPOSE_PROFILES
    assert "profiles" not in COMPOSE["services"]["brain"]
    assert "profiles" not in COMPOSE["services"]["mongo"]


def test_the_bot_runs_the_same_image_as_the_brain_with_the_telegram_command() -> None:
    assert BOT["image"] == COMPOSE["services"]["brain"]["image"]
    assert BOT["command"] == ["telegram"]
    assert BOT["restart"] == "unless-stopped"
    assert BOT["depends_on"]["brain"] == {"condition": "service_healthy"}


def test_the_bot_publishes_no_port_and_holds_no_privileges() -> None:
    assert "ports" not in BOT
    assert BOT["cap_drop"] == ["ALL"] and "no-new-privileges:true" in BOT["security_opt"]


def test_the_bot_gets_least_privilege_secrets() -> None:
    """Só o token do bot e a lista de permissão: nada de Mongo, de chaves de LLM nem do .env."""
    env = BOT["environment"]
    assert set(env) == {
        "JUNIPER_CONFIG_PATH",
        "JUNIPER_DEPLOYMENT__SERVER__HOST",
        "JUNIPER_TELEGRAM_BOT_TOKEN",
        "JUNIPER_TELEGRAM__ALLOWED_USER_IDS",
    }
    assert env["JUNIPER_TELEGRAM_BOT_TOKEN"].startswith("${")  # só referência, nunca o valor
    mounts = BOT["volumes"]
    # Sempre o config de produção (só `deployment`): nunca o pessoal nem a variável que o troca.
    assert "./docker/config.prod.yaml:/home/juniper/.juniper/config.yaml:ro" in mounts
    assert not any("JUNIPER_CONFIG_FILE" in m or "BASE_CONFIG" in m for m in mounts)
    assert not any(".env" in m.split(":/")[0] for m in mounts)
    assert not any("certs" in m for m in mounts)
    state = [m for m in mounts if mount_target(m) == "/home/juniper/.juniper/state"]
    assert state == ["juniper-state:/home/juniper/.juniper/state:ro"]  # só LÊ o token do brain
    assert all(m.endswith(":ro") for m in mounts)


def test_the_bot_talks_to_the_brain_inside_the_compose_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brain.clients import remote_cli

    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(CONFIG_PROD))
    monkeypatch.setenv(
        "JUNIPER_DEPLOYMENT__SERVER__HOST", BOT["environment"]["JUNIPER_DEPLOYMENT__SERVER__HOST"]
    )
    monkeypatch.delenv("JUNIPER_DEPLOYMENT__MODE", raising=False)
    loaded = Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr(remote_cli, "settings", loaded)
    assert remote_cli.server_url() == "ws://brain:8765/v1/ws"  # a escuta 0.0.0.0 é do servidor
    assert remote_cli.plaintext_warning() is None  # "brain" é um nome simples da rede privada
    assert loaded.deployment.auth.token_file == Path("/home/juniper/.juniper/state/token")


@pytest.mark.parametrize(
    ("raw", "expected"), [("[]", []), ("[123456789]", [123456789]), ("[1, 2]", [1, 2])]
)
def test_the_allowlist_variable_is_a_json_list_and_the_default_means_nobody(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[int]
) -> None:
    default = BOT["environment"]["JUNIPER_TELEGRAM__ALLOWED_USER_IDS"]
    assert default.endswith(":-[]}")  # sem a variável, a lista vem vazia
    monkeypatch.setenv("JUNIPER_TELEGRAM__ALLOWED_USER_IDS", raw)
    assert Settings(_env_file=None).telegram.allowed_user_ids == expected  # type: ignore[call-arg]


def test_the_bot_healthcheck_uses_the_health_flag() -> None:
    check = BOT["healthcheck"]
    assert check["test"] == ["CMD", "python", "-m", "brain", "telegram", "--health"]
    assert check["start_period"]  # dá tempo de conectar antes de contar falhas


def test_ci_validates_the_compose_file_with_the_telegram_profile_on() -> None:
    workflow = (ROOT / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")
    assert "--profile telegram" in workflow
