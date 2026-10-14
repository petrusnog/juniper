"""Testes para brain.config."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from brain.config import Settings


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cria um HOME temporário isolado para os testes."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Reaponta Path.home() para o fake
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home


@pytest.fixture
def config_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cria um config.yaml na raiz de trabalho e muda o cwd para lá."""
    config = tmp_path / "config.yaml"
    config.write_text(
        dedent(
            """
            llm:
              priority: [ollama]
              ollama:
                url: http://from-yaml:11434
                model: from-yaml-model
            agent:
              max_iterations: 3
            """
        ).strip()
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(config))
    return config


def test_defaults_when_no_config(isolated_home: Path) -> None:
    """Sem config.yaml e sem env, usa defaults do código."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.priority == ["deepinfra", "groq", "ollama"]
    assert s.llm.deepinfra.model.startswith("deepseek-ai/")
    assert s.deepinfra_api_key is None
    assert s.llm.ollama.model == "llama3.1"
    assert s.agent.max_iterations == 5
    assert s.deployment.mode == "local"
    assert s.groq_api_key is None


def test_loads_from_yaml(config_yaml: Path) -> None:
    """config.yaml é lido quando presente."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.priority == ["ollama"]
    assert s.llm.ollama.url == "http://from-yaml:11434"
    assert s.llm.ollama.model == "from-yaml-model"
    assert s.agent.max_iterations == 3


def test_env_overrides_yaml(config_yaml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Variável de ambiente vence config.yaml."""
    monkeypatch.setenv("JUNIPER_LLM__OLLAMA__URL", "http://from-env:9999")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.ollama.url == "http://from-env:9999"
    # Valor não sobrescrito continua do YAML
    assert s.llm.ollama.model == "from-yaml-model"


def test_groq_api_key_redacted(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Segredo nunca aparece em repr()."""
    monkeypatch.setenv("JUNIPER_GROQ_API_KEY", "super-secret-123")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.groq_api_key is not None
    assert "super-secret-123" not in repr(s)
    # Mas o valor real está acessível
    assert s.groq_api_key.get_secret_value() == "super-secret-123"


def test_paths_calculated_from_config_location(config_yaml: Path) -> None:
    """paths.home/logs/config derivam da localização do config.yaml."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.paths is not None
    assert s.paths.config == config_yaml.resolve()
    assert s.paths.home == config_yaml.parent
    assert s.paths.logs == config_yaml.parent / "logs"


def test_invalid_priority_rejected(config_yaml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Providers desconhecidos em llm.priority são rejeitados."""
    monkeypatch.setenv("JUNIPER_LLM__PRIORITY", '["openai"]')
    with pytest.raises(Exception, match="providers desconhecidos"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_invalid_max_iterations_rejected(
    config_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_iterations fora do range é rejeitado."""
    monkeypatch.setenv("JUNIPER_AGENT__MAX_ITERATIONS", "999")
    with pytest.raises(Exception):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_ollama_url_trailing_slash_stripped(
    config_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URL do Ollama perde barra final."""
    monkeypatch.setenv("JUNIPER_LLM__OLLAMA__URL", "http://localhost:11434/")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.ollama.url == "http://localhost:11434"


def test_google_defaults(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem bloco google, a integração fica desconfigurada mas com defaults seguros."""
    monkeypatch.chdir(tmp_path)  # sem config.yaml à vista: nada do repositório real vaza
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.google.calendar_id is None
    assert s.google.read_calendar_ids == []
    assert s.google.default_duration_minutes == 60
    assert s.google.credentials_file == isolated_home / ".juniper" / "certs" / "gcp-key.json"


def test_google_from_yaml_expands_home(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """credentials_file com ~ é expandido; calendar_id e leituras vêm do YAML."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "google:\n  credentials_file: ~/k/key.json\n  calendar_id: abc@group.calendar.google.com\n"
        "  read_calendar_ids: [eu@gmail.com]\n  reminder_minutes: 10\n"
    )
    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(cfg))
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.google.credentials_file == isolated_home / "k" / "key.json"
    assert s.google.calendar_id == "abc@group.calendar.google.com"
    assert s.google.read_calendar_ids == ["eu@gmail.com"]
    assert s.google.reminder_minutes == 10


def test_invalid_timezone_is_rejected(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fuso inexistente falha cedo, em vez de quebrar o relógio depois."""
    monkeypatch.setenv("JUNIPER_AGENT__TIMEZONE", "Marte/Olympus")
    with pytest.raises(ValueError, match="timezone"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_groq_reasoning_effort_default_and_validation(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reasoning_effort é opcional e só aceita low/medium/high."""
    monkeypatch.chdir(tmp_path)
    assert Settings(_env_file=None).llm.groq.reasoning_effort is None  # type: ignore[call-arg]
    monkeypatch.setenv("JUNIPER_LLM__GROQ__REASONING_EFFORT", "low")
    assert Settings(_env_file=None).llm.groq.reasoning_effort == "low"  # type: ignore[call-arg]
    monkeypatch.setenv("JUNIPER_LLM__GROQ__REASONING_EFFORT", "extreme")
    with pytest.raises(ValueError, match="reasoning_effort"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_env_file_is_found_from_any_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secret in ~/.juniper/.env must load even when running inside another project."""
    env_files = Settings.model_config["env_file"]
    assert isinstance(env_files, tuple)
    assert env_files[0] == Path.home() / ".juniper" / ".env"  # absolute, not cwd-relative

    home_env = tmp_path / ".env"
    home_env.write_text("JUNIPER_GROQ_API_KEY=chave-do-home\n")
    elsewhere = tmp_path / "projeto"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("JUNIPER_GROQ_API_KEY", raising=False)
    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(tmp_path / "nao-existe.yaml"))
    s = Settings(_env_file=(home_env, ".env"))  # type: ignore[call-arg]
    assert s.groq_api_key is not None and s.groq_api_key.get_secret_value() == "chave-do-home"


def test_deepinfra_key_and_settings_from_env(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JUNIPER_DEEPINFRA_API_KEY", "di-secret-123456")
    monkeypatch.setenv("JUNIPER_LLM__DEEPINFRA__MODEL", "meta-llama/Meta-Llama-3.1-70B-Instruct")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.deepinfra_api_key is not None
    assert s.deepinfra_api_key.get_secret_value() == "di-secret-123456"
    assert "di-secret" not in repr(s)  # SecretStr never leaks through repr
    assert s.llm.deepinfra.model == "meta-llama/Meta-Llama-3.1-70B-Instruct"


def test_priority_accepts_deepinfra_in_any_order(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JUNIPER_LLM__PRIORITY", '["ollama", "deepinfra"]')
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.priority == ["ollama", "deepinfra"]


def test_calendar_management_settings(
    isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JUNIPER_CONFIG_PATH", str(tmp_path / "nao-existe.yaml"))  # not the real one
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.google.manage_calendar_ids == [] and s.google.default_calendar_id is None
    monkeypatch.setenv("JUNIPER_GOOGLE__MANAGE_CALENDAR_IDS", '["eu@gmail.com"]')
    monkeypatch.setenv("JUNIPER_GOOGLE__DEFAULT_CALENDAR_ID", "eu@gmail.com")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.google.manage_calendar_ids == ["eu@gmail.com"]
    assert s.google.default_calendar_id == "eu@gmail.com"
