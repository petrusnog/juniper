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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# --- Submodelos -------------------------------------------------------


class LLMDeepInfraSettings(BaseModel):
    """Configuração do provider DeepInfra (API compatível com a da OpenAI)."""

    # Modelo usado nos exemplos de tool calling da própria documentação da DeepInfra.
    model: str = "deepseek-ai/DeepSeek-V4-Flash-0731"
    timeout: int = Field(default=30, ge=1, le=600)


class LLMGroqSettings(BaseModel):
    """Configuração do provider Groq."""

    model: str = "openai/gpt-oss-20b"
    timeout: int = Field(default=30, ge=1, le=600)
    # gpt-oss only: "low" cuts the (billed) reasoning tokens. None = provider default.
    reasoning_effort: Literal["low", "medium", "high"] | None = None


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

    priority: list[str] = Field(default_factory=lambda: ["deepinfra", "groq", "ollama"])
    deepinfra: LLMDeepInfraSettings = Field(default_factory=LLMDeepInfraSettings)
    groq: LLMGroqSettings = Field(default_factory=LLMGroqSettings)
    ollama: LLMOllamaSettings = Field(default_factory=LLMOllamaSettings)

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, v: list[str]) -> list[str]:
        allowed = {"deepinfra", "groq", "ollama"}
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

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"agent.timezone inválido: {v!r}") from exc
        return v


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


class GoogleSettings(BaseModel):
    """Integração com o Google Calendar via conta de serviço (ver ADR-008)."""

    credentials_file: Path = Field(
        default_factory=lambda: Path.home() / ".juniper" / "certs" / "gcp-key.json"
    )
    # Agenda "Juniper": a única onde a Juniper cria e apaga eventos.
    calendar_id: str | None = None
    # Agendas só lidas, para detectar conflitos (ex.: o Gmail pessoal).
    read_calendar_ids: list[str] = Field(default_factory=list)
    # Agendas do usuário em que a Juniper também cria, altera e apaga eventos (ADR-010). Precisam
    # estar compartilhadas com a conta de serviço com permissão de "fazer alterações em eventos".
    manage_calendar_ids: list[str] = Field(default_factory=list)
    # Onde novos eventos caem quando o usuário não diz. None = a primeira de manage_calendar_ids
    # (a agenda pessoal); sem nenhuma, a agenda Juniper (calendar_id).
    default_calendar_id: str | None = None
    default_duration_minutes: int = Field(default=60, ge=5, le=1440)
    reminder_minutes: int = Field(default=30, ge=0, le=40320)

    @field_validator("credentials_file")
    @classmethod
    def _expand_user(cls, v: Path) -> Path:
        return v.expanduser()


class SessionMemorySettings(BaseModel):
    """Memória de sessão: quanto da conversa vai ao LLM e onde ela é guardada (ver ADR-011)."""

    max_turns: int = Field(default=8, ge=1, le=100)
    # Orçamento (estimado) de tokens do histórico enviado ao LLM; turnos antigos saem primeiro.
    max_tokens: int = Field(default=6000, ge=500, le=200_000)
    dir: Path = Field(default_factory=lambda: Path.home() / ".juniper" / "sessions")
    # Onde as conversas ficam: arquivos JSON (padrão) ou MongoDB (memory.mongo).
    store: Literal["file", "mongo"] = "file"

    @field_validator("dir")
    @classmethod
    def _expand_user(cls, v: Path) -> Path:
        return v.expanduser()


class MongoSettings(BaseModel):
    """Conexão com o MongoDB da memória (ver ADR-011, T-053).

    A URI não leva credenciais: usuário e senha vêm do .env (ADR-003) e são entregues ao
    driver à parte, então senhas com caracteres especiais não precisam ser escapadas.
    """

    uri: str = "mongodb://localhost:27017"
    database: str = Field(default="juniper", min_length=1)
    username: str | None = None
    password: SecretStr | None = None
    # Quanto esperar por um servidor antes de desistir; curto, para o erro aparecer logo.
    timeout_ms: int = Field(default=3000, ge=100, le=60_000)


class EmbeddingSettings(BaseModel):
    """Modelo local de embeddings da memória de longo prazo (ver ADR-011, T-054)."""

    # Multilíngue, roda em CPU via ONNX. Trocar de modelo exige `juniper memory reindex`.
    model: str = Field(default="intfloat/multilingual-e5-small", min_length=3)
    # Onde o modelo baixado fica (uma pasta por modelo).
    dir: Path = Field(default_factory=lambda: Path.home() / ".juniper" / "models")
    # Teto de tokens por texto; o que passar disso é cortado.
    max_length: int = Field(default=512, ge=8, le=8192)
    batch_size: int = Field(default=16, ge=1, le=256)
    # Tempo máximo (s) para baixar cada arquivo do modelo.
    download_timeout: int = Field(default=300, ge=5, le=3600)

    @field_validator("dir")
    @classmethod
    def _expand_user(cls, v: Path) -> Path:
        return v.expanduser()


class MemorySettings(BaseModel):
    """Memória da Juniper (ver ADR-011). Cada task da Sprint 2 acrescenta a sua seção."""

    session: SessionMemorySettings = Field(default_factory=SessionMemorySettings)
    mongo: MongoSettings = Field(default_factory=MongoSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)


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
    google: GoogleSettings = Field(default_factory=GoogleSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)

    # Segredos (sempre via env)
    groq_api_key: SecretStr | None = Field(default=None, alias="JUNIPER_GROQ_API_KEY")
    deepinfra_api_key: SecretStr | None = Field(default=None, alias="JUNIPER_DEEPINFRA_API_KEY")

    # Paths (calculados no model_post_init)
    paths: PathsSettings | None = None

    model_config = SettingsConfigDict(
        env_prefix="JUNIPER_",
        env_nested_delimiter="__",
        # ~/.juniper/.env first so the key is found from any directory (the git tools need to
        # run inside a project repo); ./.env, if present, overrides it.
        env_file=(Path.home() / ".juniper" / ".env", ".env"),
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
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=config_path))

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
