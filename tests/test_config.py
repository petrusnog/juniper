"""Testes para brain.config."""

from __future__ import annotations

import os
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
    assert s.llm.priority == ["groq", "ollama"]
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


def test_env_overrides_yaml(
    config_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Variável de ambiente vence config.yaml."""
    monkeypatch.setenv("JUNIPER_LLM__OLLAMA__URL", "http://from-env:9999")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.ollama.url == "http://from-env:9999"
    # Valor não sobrescrito continua do YAML
    assert s.llm.ollama.model == "from-yaml-model"


def test_groq_api_key_redacted(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_ollama_url_trailing_slash_stripped(config_yaml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """URL do Ollama perde barra final."""
    monkeypatch.setenv("JUNIPER_LLM__OLLAMA__URL", "http://localhost:11434/")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.ollama.url == "http://localhost:11434"