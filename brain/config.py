"""Configuração centralizada da Juniper.

Ponto único de acesso a preferências, credenciais e paths.

Precedência (maior vence):
    1. Variáveis de ambiente (prefixo JUNIPER_)
    2. config.yaml (na raiz do projeto)
    3. Defaults no código

Uso:
    from brain.config import settings
    print(settings.llm.priority)
    print(settings.paths.home)

Ver ADR-003 (docs/decisions/ADR-003-configuracao.md).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


# --- Submodelos -------------------------------------------------------


class LLMGroqSettings(BaseModel):
    """Configuração do provider Groq."""

    model: str = "llama-3.3-70b-versatile"
    timeout: int = Field(default=30, ge=1, le=600)


class LLMOllamaSettings(BaseModel):
    """Configuração do provider Ollama."""

    url: str = "http://localhost:11434"
    model: str = "llama3.1"
    timeout: int = Field(default=120, ge=1, le=3600)

    @field_validator("url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class LLMSettings(BaseModel):
    """Configuração agregada dos providers de LLM."""

    priority: list[str] = Field(default_factory=lambda: ["groq", "ollama"])
    groq: LLMGroqSettings = Field(default_factory=LLMGroqSettings)
    ollama: LLMOllamaSettings = Field(default_factory=LLMOllamaSettings)

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, v: list[str]) -> list[str]:
        allowed = {"groq", "ollama"}
        if not v:
            raise ValueError("llm.priority não pode ser vazio")
        unknown = set(v) - allowed
        if unknown:
            raise ValueError(
                f"llm.priority contém providers desconhecidos: {sorted(unknown)}. "
                f"Permitidos: {sorted(allowed)}"
            )
        return v


class AgentSettings(BaseModel):
    """Configuração do loop agentic."""

    max_iterations: int = Field(default=5, ge=1, le=50)
    language: str = "pt-BR"
    timezone: str = "America/Sao_Paulo"


class ServerSettings(BaseModel):
    """Configuração do servidor HTTP/WS (Sprint 2+)."""

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)


class AuthSettings(BaseModel):
    """Configuração de autenticação (Sprint 2+)."""

    token_file: Path = Field(default_factory=lambda: Path.home() / ".juniper" / "token")


class DeploymentSettings(BaseModel):
    """Modo de deployment do brain."""

    mode: Literal["local", "server"] = "local"
    server: ServerSettings = Field(default_factory=ServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)


class SecuritySettings(BaseModel):
    """Política de segurança de execução de tools (ver ADR-006)."""

    shell_allowlist: list[str] = Field(
        default_factory=lambda: [
            "git",
            "ls",
            "cat",
            "grep",
            "rg",
            "fd",
            "jq",
            "find",
            "wc",
            "head",
            "tail",
        ]
    )
    shell_require_confirmation: bool = True
    confirmation_timeout: int = Field(default=60, ge=1, le=600)


class ToolsSettings(BaseModel):
    """Configuração agregada das tools."""

    security: SecuritySettings = Field(default_factory=SecuritySettings)


class PathsSettings(BaseModel):
    """Paths canônicos do runtime da Juniper.

    Calculados a partir da localização do config.yaml, não configuráveis
    por variáveis de ambiente (evita inconsistência).
    """

    home: Path
    logs: Path
    config: Path


# --- Settings principal -----------------------------------------------


def _locate_config_file() -> Path:
    """Localiza o config.yaml.

    Ordem:
        1. JUNIPER_CONFIG_PATH (override explícito)
        2. ~/.juniper/config.yaml (instalação padrão)
        3. ./config.yaml (desenvolvimento na raiz do repo)

    Se nenhum existir, retorna ~/.juniper/config.yaml como destino
    canônico (mesmo que ainda não exista).
    """
    explicit = os.environ.get("JUNIPER_CONFIG_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()

    home_config = Path.home() / ".juniper" / "config.yaml"
    if home_config.exists():
        return home_config

    cwd_config = Path.cwd() / "config.yaml"
    if cwd_config.exists():
        return cwd_config.resolve()

    return home_config


class Settings(BaseSettings):
    """Configuração raiz da Juniper.

    Uso:
        from brain.config import settings
    """

    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    deployment: DeploymentSettings = Field(default_factory=DeploymentSettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)

    # Segredos (sempre via env)
    groq_api_key: SecretStr | None = Field(default=None, alias="JUNIPER_GROQ_API_KEY")

    # Paths (calculados no model_post_init)
    paths: PathsSettings | None = None

    model_config = SettingsConfigDict(
        env_prefix="JUNIPER_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Define a ordem das fontes de configuração.

        Ordem (primeira vence):
            1. Argumentos passados no construtor
            2. Variáveis de ambiente
            3. .env
            4. config.yaml
            5. Secrets files
        """
        config_path = _locate_config_file()

        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]

        if config_path.exists():
            sources.append(
                YamlConfigSettingsSource(settings_cls, yaml_file=config_path)
            )

        sources.append(file_secret_settings)
        return tuple(sources)

    def model_post_init(self, __context: Any) -> None:  # noqa: ARG002
        """Calcula paths a partir da localização do config.yaml."""
        config_path = _locate_config_file()
        home = config_path.parent
        self.paths = PathsSettings(
            home=home,
            logs=home / "logs",
            config=config_path,
        )


# --- Singleton --------------------------------------------------------

settings = Settings()