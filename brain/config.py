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


class LLMReflectionSettings(BaseModel):
    """O modelo da reflexão e do estudo da passada noturna (ADR-017, seção 11).

    Vazio = o mesmo do chat. A reflexão roda uma vez por dia, então um modelo mais capaz custa
    pouco no total; o modelo barato continua conversando.
    """

    provider: str | None = None
    model: str | None = None

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, v: str | None) -> str | None:
        if v is not None and v not in {"deepinfra", "groq", "ollama"}:
            raise ValueError(
                f"llm.reflection.provider desconhecido: {v!r} (use deepinfra, groq ou ollama)"
            )
        return v


class LLMSettings(BaseModel):
    """Configuração agregada dos providers de LLM."""

    reflection: LLMReflectionSettings = Field(default_factory=LLMReflectionSettings)
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
    # Mostra no terminal quanto cada etapa de cada resposta levou (memória, LLM, ferramentas).
    # Os tempos vão para o log estruturado de qualquer jeito (evento `timing`).
    show_timing: bool = False

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
    # Threads de CPU do onnxruntime. 0 = automático (usa todos os núcleos, o que pode pesar na
    # máquina enquanto você trabalha); menos threads deixam a máquina mais livre e o embedding
    # mais lento.
    threads: int = Field(default=0, ge=0, le=64)
    # Tempo máximo (s) para baixar cada arquivo do modelo.
    download_timeout: int = Field(default=300, ge=5, le=3600)

    @field_validator("dir")
    @classmethod
    def _expand_user(cls, v: Path) -> Path:
        return v.expanduser()


class RetrievalSettings(BaseModel):
    """Busca por similaridade na memória de longo prazo (ver ADR-011, T-055)."""

    # Quantas memórias, no máximo, uma busca devolve.
    top_k: int = Field(default=5, ge=1, le=100)
    # Similaridade de cosseno mínima (-1 a 1) para uma memória contar como relevante. O modelo
    # e5 dá notas altas até para textos sem relação (em geral 0.7 a 0.8), então 0.0 não filtraria
    # nada. 0.80 é um ponto de partida; ajuste com `juniper memory search` (T-058), que mostra
    # a nota de cada resultado.
    threshold: float = Field(default=0.80, ge=-1.0, le=1.0)
    # Orçamento (estimado) de tokens das memórias injetadas no prompt de cada mensagem.
    max_tokens: int = Field(default=500, ge=50, le=8000)


class ExtractionSettings(BaseModel):
    """Como a Juniper tira fatos duráveis da conversa (ver ADR-011, T-056)."""

    # Liga a memória de longo prazo. Só vale com `memory.session.store: mongo`: é lá que as
    # memórias ficam. Desligue para não gastar chamadas de LLM nem baixar o modelo de embeddings.
    enabled: bool = True
    # Teto de memórias novas guardadas por conversa.
    max_per_run: int = Field(default=5, ge=1, le=50)
    # Tamanho máximo do texto de uma memória (uma afirmação curta).
    max_chars: int = Field(default=300, ge=40, le=2000)
    # Quantos turnos recentes vão ao LLM de extração.
    max_turns: int = Field(default=20, ge=1, le=200)
    # Memórias parecidas com a candidata que o LLM compara (para decidir duplicata/contradição).
    neighbors: int = Field(default=3, ge=1, le=10)
    # Similaridade (cosseno) mínima para uma memória existente contar como "parecida".
    neighbor_threshold: float = Field(default=0.75, ge=-1.0, le=1.0)
    # Quando extrair: "turn" = em segundo plano, logo depois de cada resposta (sem atrasar a
    # conversa, e a Juniper já sabe do que você disse na mensagem seguinte); "exit" = só ao
    # encerrar o chat (uma chamada de LLM por conversa, mas perde o que ficou se você fechar
    # com Ctrl+C e demora ao sair).
    mode: Literal["turn", "exit"] = "turn"
    # Guarda sentimentos e acontecimentos pessoais ("Petrus se sentiu frustrado em 14/10/2026").
    # É conteúdo sensível: o trecho analisado vai ao provider do chat. Desligue para não guardar.
    save_feelings: bool = True


class ProfileSettings(BaseModel):
    """Perfil do usuário: um parágrafo de identidade presente em todo prompt (ADR-011, T-061)."""

    # Só vale com a memória de longo prazo ligada (`memory.session.store: mongo`).
    enabled: bool = True
    # Teto do parágrafo, imposto ao gravar e ao injetar: é o que custa tokens em TODA mensagem.
    max_chars: int = Field(default=500, ge=100, le=2000)


def _hhmm(value: str, name: str) -> str:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        raise ValueError(f"{name} inválido: {value!r} (use HH:MM, ex.: 03:00)") from None
    return f"{hour:02d}:{minute:02d}"


class V2NightSettings(BaseModel):
    """A passada noturna, o "inconsciente" (ADR-017, seção 9)."""

    # Hora diária (HH:MM, fuso do agente) em que o servidor roda a passada.
    at: str = "03:00"
    # Orçamento da noite: tokens estimados de LLM e minutos. Estourou, a fase termina e o resto
    # fica para a noite seguinte.
    max_tokens: int = Field(default=60_000, ge=1_000, le=5_000_000)
    max_minutes: int = Field(default=20, ge=1, le=240)

    @field_validator("at")
    @classmethod
    def _validate_at(cls, v: str) -> str:
        return _hhmm(v, "memory.v2.night.at")


class V2BeliefSettings(BaseModel):
    """Crenças: como a confiança se forma e decai (ADR-017, seção 5.1). Pontos de partida."""

    # Sem reforço, alpha e beta encolhem na direção do a priori com esta meia-vida.
    half_life_days: float = Field(default=60.0, ge=1.0, le=3650.0)
    # Evidência mínima (n = alpha + beta - 2, de dias ou sessões diferentes) para uma crença valer.
    min_evidence: int = Field(default=2, ge=1, le=20)
    # Teto de crenças novas por noite.
    max_new_per_night: int = Field(default=5, ge=0, le=50)


class V2InterestSettings(BaseModel):
    """Interesses próprios da Juniper (ADR-017, seção 5.6)."""

    # Temas estudados por noite (sem web: usa o conhecimento do modelo avançado).
    max_per_night: int = Field(default=2, ge=0, le=10)


class V2AffectSettings(BaseModel):
    """Sentimentos (ADR-017, seção 7). O Petrus pode ajustar ou desligar."""

    enabled: bool = True
    # Escala o registro e a expressão dos sentimentos (1 = normal; 0 = nada é registrado).
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)


class V2PersonaSettings(BaseModel):
    """Personalidade viva (ADR-017, seção 8)."""

    # Deixa os traços mudarem com o que ela vive. Desligado, ficam no baseline.
    evolves: bool = True
    # Passo máximo de um traço por noite.
    max_step: float = Field(default=0.03, ge=0.0, le=0.2)


class MemoryV2Settings(BaseModel):
    """Memória V2: do fato à personalidade viva (ADR-017)."""

    # Liga a V2 inteira. Exige `memory.session.store: mongo`. Desligada, nada muda em relação à v1.
    enabled: bool = False
    # Teto (estimado) do bloco da V2 no prompt da conversa.
    max_tokens: int = Field(default=600, ge=100, le=8000)
    night: V2NightSettings = Field(default_factory=V2NightSettings)
    beliefs: V2BeliefSettings = Field(default_factory=V2BeliefSettings)
    interests: V2InterestSettings = Field(default_factory=V2InterestSettings)
    affect: V2AffectSettings = Field(default_factory=V2AffectSettings)
    persona: V2PersonaSettings = Field(default_factory=V2PersonaSettings)


class MemorySettings(BaseModel):
    """Memória da Juniper (ver ADR-011). Cada task da Sprint 2 acrescenta a sua seção."""

    session: SessionMemorySettings = Field(default_factory=SessionMemorySettings)
    mongo: MongoSettings = Field(default_factory=MongoSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)
    profile: ProfileSettings = Field(default_factory=ProfileSettings)
    v2: MemoryV2Settings = Field(default_factory=MemoryV2Settings)


class TraceSettings(BaseModel):
    """O diário das mensagens (`message.log`): cada interação contada, camada por camada."""

    # Desligado por padrão: o arquivo guarda o texto das conversas. Ligue para ver o caminho que
    # uma mensagem percorre (o CLI escreve o arquivo; o servidor manda os passos dele pelo
    # WebSocket, então ligar no config.yaml pessoal liga o CLI e o contêiner de dev juntos).
    enabled: bool = False
    # Onde escrever. None = logs/message.log, ao lado dos logs de eventos.
    file: Path | None = None


class LoggingSettings(BaseModel):
    """O log técnico: o que a Juniper fez por baixo dos panos, e quanto tempo ele fica no disco."""

    # Grava no log cada requisição e resposta HTTP aos provedores de LLM e à agenda do Google
    # (método, URL, status, tempo, corpo). O corpo traz o que você disse e o que voltou: por isso
    # o arquivo é 600 e os logs somem depois de `retention_days`. Segredos são sempre mascarados.
    wire: bool = True
    # Maior corpo (em caracteres) gravado por requisição/resposta; o resto é cortado com a contagem.
    wire_max_chars: int = Field(default=20000, ge=500, le=1_000_000)
    # Logs de dias anteriores a hoje são compactados (.gz); os com mais de `retention_days` dias
    # são apagados. Hoje + `retention_days` dias anteriores ficam no disco.
    retention_days: int = Field(default=3, ge=1, le=365)
    # Hora diária (HH:MM, no fuso do agente) em que o servidor compacta e apaga logs antigos.
    rotate_at: str = "00:10"

    @field_validator("rotate_at")
    @classmethod
    def _validate_rotate_at(cls, v: str) -> str:
        try:
            hour, minute = (int(part) for part in v.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            raise ValueError(f"logging.rotate_at inválido: {v!r} (use HH:MM, ex.: 00:10)") from None
        return f"{hour:02d}:{minute:02d}"


class TelegramSettings(BaseModel):
    """O bot do Telegram, um cliente do protocolo (ADR-016). O token do bot fica no .env."""

    # IDs numéricos de quem pode falar com o bot. Lista vazia = ninguém (nunca "todos"). Para
    # descobrir o seu ID, mande uma mensagem ao bot e leia o log (`docker compose logs telegram`).
    allowed_user_ids: list[int] = Field(default_factory=list)
    # Mensagens por minuto por usuário (contam as dele, não as do bot). Acima disso o bot avisa e
    # NÃO chama o cérebro: um erro de loop não vira conta de LLM.
    rate_limit_per_minute: int = Field(default=20, ge=1, le=600)
    # Apagar a mensagem do usuário e a resposta do bot depois de N horas. 0 = não apaga. O
    # Telegram só deixa o bot apagar mensagens com menos de 48 h, por isso o máximo é 48.
    delete_after_hours: float = Field(default=0.0, ge=0.0, le=48.0)


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


def _locate_base_config() -> Path | None:
    """O config.yaml de base (JUNIPER_BASE_CONFIG_PATH), se a variável existe e é um arquivo."""
    explicit = os.environ.get("JUNIPER_BASE_CONFIG_PATH")
    if not explicit:
        return None
    path = Path(explicit).expanduser()
    return path if path.is_file() else None  # o Docker cria uma pasta se a origem não existe


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
    trace: TraceSettings = Field(default_factory=TraceSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)

    # Segredos (sempre via env)
    groq_api_key: SecretStr | None = Field(default=None, alias="JUNIPER_GROQ_API_KEY")
    deepinfra_api_key: SecretStr | None = Field(default=None, alias="JUNIPER_DEEPINFRA_API_KEY")
    telegram_bot_token: SecretStr | None = Field(default=None, alias="JUNIPER_TELEGRAM_BOT_TOKEN")

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
            5. config de base (JUNIPER_BASE_CONFIG_PATH), só o que falta nos de cima
            6. Secrets files
        """
        config_path = _locate_config_file()

        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
        ]

        if config_path.exists():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=config_path))

        # Camada base (opcional, só o que o arquivo principal não define): o ambiente Docker de
        # desenvolvimento monta aqui o seu config.yaml pessoal (agenda do Google, persona...) e
        # deixa a infraestrutura (deployment, mongo) com o config.dev.yaml.
        base_path = _locate_base_config()
        if base_path is not None and base_path != config_path:
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=base_path))

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
