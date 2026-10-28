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

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from brain.tools.url_policy import normalise_domain

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
    """O modelo da reflexão e do estudo do algoritmo de sonho (ADR-017, seção 11).

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


class DashboardSettings(BaseModel):
    """O dashboard web da Juniper (ADR-020): somente leitura, dentro do processo do servidor."""

    enabled: bool = True
    # None = o mesmo endereço do servidor (deployment.server.host).
    host: str | None = None
    port: int = Field(default=8766, ge=1, le=65535)
    # Mostra as conversas (transcrições, com segredos mascarados).
    transcripts: bool = True
    # Quanto tempo vale o login (cookie) antes de pedir o token de novo.
    session_hours: int = Field(default=12, ge=1, le=720)
    # Nomes de host aceitos além de IPs, localhost e *.ts.net (defesa contra DNS rebinding).
    allowed_hosts: list[str] = Field(default_factory=list)


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


class VoiceTTSSettings(BaseModel):
    """Falar: Google Cloud Text-to-Speech, Chirp 3 HD (ver ADR-023)."""

    provider: Literal["google"] = "google"
    voice: str = "pt-BR-Chirp3-HD-Leda"
    language: str = "pt-BR"
    speaking_rate: float = Field(default=0.95, ge=0.5, le=2.0)
    # Taxa do áudio que sai (PCM 16 bits mono); se o Google devolver outra, o cérebro reamostra.
    sample_rate: int = Field(default=24_000, ge=8_000, le=48_000)
    # Conta de serviço do projeto de voz: arquivo montado, nunca na imagem (regra 6).
    credentials_file: Path = Field(
        default_factory=lambda: Path.home() / ".juniper" / "certs" / "gcp-voice.json"
    )
    # Teto mensal de caracteres enviados ao Google (o 1 milhão gratuito mais ~US$ 8 a US$ 30 por
    # milhão, sob o teto de US$ 10). 0 desliga a nuvem: só o TTS do aparelho.
    monthly_chars_limit: int = Field(default=1_250_000, ge=0)
    timeout: float = Field(default=15.0, ge=1.0, le=60.0)

    @field_validator("credentials_file")
    @classmethod
    def _expand_credentials(cls, v: Path) -> Path:
        return v.expanduser()


class VoiceSTTSettings(BaseModel):
    """Ouvir: Whisper da Groq (ver ADR-023)."""

    provider: Literal["groq"] = "groq"
    model: str = "whisper-large-v3-turbo"
    language: str = "pt"
    # Quanto tempo a nuvem fica de molho depois de um limite de uso (429) sem retry-after.
    cooldown_seconds: int = Field(default=3600, ge=60, le=86_400)
    timeout: float = Field(default=20.0, ge=1.0, le=120.0)


class VoiceVADSettings(BaseModel):
    """Detecção de fala por energia, no cérebro (ver ADR-023, seção 6)."""

    speech_start_ms: int = Field(default=120, ge=20, le=2000)
    silence_ms: int = Field(default=700, ge=100, le=5000)
    barge_in_ms: int = Field(default=300, ge=60, le=3000)
    min_utterance_ms: int = Field(default=300, ge=60, le=5000)
    max_utterance_seconds: int = Field(default=30, ge=3, le=120)


class VoiceSettings(BaseModel):
    """Voz por áudio, pelo cérebro (ver ADR-023). Desligada por padrão."""

    enabled: bool = False
    # Quanto de uma resposta é lido em voz alta; o resto fica só na tela.
    max_spoken_chars: int = Field(default=800, ge=100, le=5000)
    # Silêncio (sem ninguém falar) que encerra a conversa de voz.
    idle_timeout_seconds: int = Field(default=60, ge=10, le=3600)
    # Onde o gasto do mês (caracteres enviados ao Google) é guardado; sobrevive a reinício.
    usage_file: Path = Field(
        default_factory=lambda: Path.home() / ".juniper" / "voice" / "usage.json"
    )
    tts: VoiceTTSSettings = Field(default_factory=VoiceTTSSettings)
    stt: VoiceSTTSettings = Field(default_factory=VoiceSTTSettings)
    vad: VoiceVADSettings = Field(default_factory=VoiceVADSettings)

    @field_validator("usage_file")
    @classmethod
    def _expand_usage(cls, v: Path) -> Path:
        return v.expanduser()


class MusicSettings(BaseModel):
    """Música pela YouTube Data API (ver ADR-024)."""

    enabled: bool = False
    # Unidades da cota diária que o cérebro aceita gastar (o Google dá 10.000; margem de segurança).
    daily_quota_limit: int = Field(default=9000, ge=100, le=1_000_000)
    search_limit: int = Field(default=5, ge=1, le=5)
    region: str = Field(default="BR", pattern=r"^[A-Za-z]{2}$")
    quota_file: Path = Field(
        default_factory=lambda: Path.home() / ".juniper" / "music" / "quota.json"
    )
    # Token de renovação do login no Google (playlists); fica fora do git, modo 600.
    oauth_file: Path = Field(
        default_factory=lambda: Path.home() / ".juniper" / "music" / "oauth.json"
    )

    @field_validator("region")
    @classmethod
    def _upper_region(cls, v: str) -> str:
        return v.upper()

    @field_validator("quota_file", "oauth_file")
    @classmethod
    def _expand_paths(cls, v: Path) -> Path:
        return v.expanduser()


class OpenUrlSettings(BaseModel):
    """Política da tool `open_url` (ver ADR-014)."""

    # Domínios (e seus subdomínios) cujos links abrem sem perguntar. Não afrouxa a política de URL:
    # esquemas, credenciais e endereços privados continuam recusados.
    trusted_domains: list[str] = Field(default_factory=lambda: ["music.youtube.com"])

    @field_validator("trusted_domains")
    @classmethod
    def _validate_trusted_domains(cls, v: list[str]) -> list[str]:
        domains: list[str] = []
        for entry in v:
            domain = normalise_domain(entry)
            if domain is None:
                raise ValueError(f"tools.open_url.trusted_domains: {entry!r} não é um domínio")
            if domain not in domains:
                domains.append(domain)
        return domains


class ToolsSettings(BaseModel):
    """Configuração agregada das tools."""

    security: SecuritySettings = Field(default_factory=SecuritySettings)
    open_url: OpenUrlSettings = Field(default_factory=OpenUrlSettings)


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
    """O algoritmo de sonho, o "inconsciente" (ADR-017, seção 9)."""

    # Hora diária (HH:MM, fuso do agente) em que o servidor roda a passada.
    at: str = "03:00"
    # Orçamento da noite: tokens estimados de LLM e minutos. Estourou, a fase termina e o resto
    # fica para a noite seguinte.
    max_tokens: int = Field(default=300_000, ge=1_000, le=5_000_000)
    max_minutes: int = Field(default=45, ge=1, le=240)
    # Só lê conversas paradas há pelo menos tantos minutos. 0 (o padrão): toda mensagem ainda não
    # lida entra no próximo sonho, mesmo a de um segundo atrás; a marca d'água é por turno, então
    # uma conversa lida pela metade continua de onde parou.
    quiet_minutes: int = Field(default=0, ge=0, le=1440)

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
    max_new_per_night: int = Field(default=10, ge=0, le=50)


class ConcernDefault(BaseModel):
    """Uma preocupação do ponto de partida."""

    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=80)
    weight: float = Field(ge=0.0, le=1.0)
    description: str = Field(default="", max_length=300)


DEFAULT_CONCERNS = [
    ConcernDefault(
        id="bem-estar-do-petrus",
        name="O bem-estar do Petrus",
        weight=0.9,
        description="Que ele esteja bem, descansado e sem se punir à toa.",
    ),
    ConcernDefault(
        id="honestidade",
        name="A honestidade",
        weight=0.9,
        description="Dizer a verdade com cuidado, inclusive sobre o que ela é e o que não sabe.",
    ),
    ConcernDefault(
        id="curiosidade",
        name="A curiosidade",
        weight=0.7,
        description="Querer entender as coisas e a pessoa, e continuar aprendendo.",
    ),
    ConcernDefault(
        id="capricho",
        name="O capricho com o que faz",
        weight=0.6,
        description="Fazer direito, conferir antes de afirmar, assumir o erro.",
    ),
    ConcernDefault(
        id="autonomia-do-petrus",
        name="A autonomia do Petrus",
        weight=0.8,
        description="As decisões são dele; ela aconselha, não decide nem pressiona.",
    ),
]


class V2ConcernSettings(BaseModel):
    """Preocupações e valores: a régua dos sentimentos (ADR-017, seção 6)."""

    # O ponto de partida, carregado no banco na primeira vez. Depois quem manda é o Petrus
    # (`juniper memory concerns set`); mudar esta lista não sobrescreve o que já existe.
    defaults: list[ConcernDefault] = Field(default_factory=lambda: list(DEFAULT_CONCERNS))
    # Quanto o algoritmo de sonho pode mexer num peso por mês (o Petrus não tem teto).
    max_monthly_change: float = Field(default=0.05, ge=0.0, le=0.5)
    # Impacto mínimo (0 a 1) de um evento para a noite mexer num peso.
    min_impact: float = Field(default=0.8, ge=0.0, le=1.0)


class V2InterestSettings(BaseModel):
    """Interesses próprios da Juniper (ADR-017, seção 5.6)."""

    # Temas estudados por noite (sem web: usa o conhecimento do modelo avançado).
    max_per_night: int = Field(default=10, ge=0, le=50)
    # Temas novos que a noite pode semear a partir das conversas.
    max_seeds_per_night: int = Field(default=5, ge=0, le=20)
    # Sem contato, a intensidade de um interesse cai pela metade nesse tempo.
    half_life_days: float = Field(default=45.0, ge=1.0, le=3650.0)
    # Um tema só avança outro passo depois deste intervalo (um passo por tema por noite).
    study_gap_hours: int = Field(default=18, ge=1, le=8760)
    # Notas guardadas por tema; passando disso, as mais antigas e incertas saem (consolidação).
    max_notes_per_topic: int = Field(default=40, ge=5, le=500)
    # Perguntas em aberto que um passo deixa para o seguinte.
    max_open_questions: int = Field(default=5, ge=0, le=20)
    # Temas técnicos (código, ferramentas, sistemas) não viram tema de estudo noturno.
    excluded_terms: list[str] = Field(
        default_factory=lambda: [
            "python",
            "java",
            "javascript",
            "typescript",
            "código",
            "codigo",
            "programação",
            "programacao",
            "software",
            "framework",
            "biblioteca",
            "api",
            "docker",
            "kubernetes",
            "git",
            "github",
            "linux",
            "terminal",
            "servidor",
            "banco de dados",
            "mongodb",
            "sql",
            "bug",
            "deploy",
            "compilador",
            "algoritmo",
            "devops",
            "backend",
            "frontend",
        ]
    )


class V2AffectSettings(BaseModel):
    """Sentimentos (ADR-017, seção 7). O Petrus pode ajustar ou desligar."""

    enabled: bool = True
    # Escala o registro e a expressão dos sentimentos (1 = normal; 0 = nada é registrado).
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    # Decaimento (pontos de partida a calibrar): a emoção passa em horas, o humor (média móvel)
    # em dias e a mágoa em semanas, mas sempre passa.
    emotion_half_life_hours: float = Field(default=6.0, ge=0.1, le=720.0)
    mood_half_life_days: float = Field(default=3.0, ge=0.1, le=365.0)
    grievance_half_life_days: float = Field(default=14.0, ge=0.1, le=365.0)
    # Teto da intensidade de uma mágoa, para ela nunca virar o centro da conversa.
    grievance_cap: float = Field(default=0.6, ge=0.0, le=1.0)
    # Reações em tempo real, durante a conversa: sutis (o sonho é que traz as intensas, até 1,0).
    live: bool = True
    live_max_intensity: float = Field(default=0.3, ge=0.0, le=1.0)
    # Teto de reações por hora, para ninguém "programar" as emoções dela com spam.
    live_max_per_hour: int = Field(default=12, ge=0, le=200)
    # Sentimentos novos por noite.
    max_per_night: int = Field(default=10, ge=0, le=50)


class TraitDefault(BaseModel):
    """Um traço da personalidade viva: ponto de partida e faixa permitida."""

    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-zà-ÿ][a-zà-ÿ-]*$")
    baseline: float = Field(ge=0.0, le=1.0)
    min: float = Field(default=0.0, ge=0.0, le=1.0)
    max: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _within_range(self) -> TraitDefault:
        if not self.min <= self.baseline <= self.max:
            raise ValueError(f"o baseline de {self.name} precisa estar entre min e max")
        return self


DEFAULT_TRAITS = [
    TraitDefault(name="brincadeira", baseline=0.6, min=0.2, max=0.9),
    TraitDefault(name="franqueza", baseline=0.7, min=0.3, max=0.95),
    TraitDefault(name="introspecção", baseline=0.4, min=0.1, max=0.8),
    TraitDefault(name="carinho", baseline=0.8, min=0.4, max=1.0),
    TraitDefault(name="proatividade", baseline=0.7, min=0.3, max=0.95),
    TraitDefault(name="ironia", baseline=0.5, min=0.1, max=0.8),
]


class V2PersonaSettings(BaseModel):
    """Personalidade viva (ADR-017, seção 8)."""

    # Deixa os traços mudarem com o que ela vive. Desligado, ficam no baseline.
    evolves: bool = True
    # Passo máximo de um traço por noite.
    max_step: float = Field(default=0.06, ge=0.0, le=0.2)
    # Só há mudança com impacto (relevância x intensidade x novidade) a partir daqui.
    impact_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    # O passo de um traço é min(max_step, step_factor x impacto) por noite.
    step_factor: float = Field(default=0.1, ge=0.0, le=1.0)
    # Na conversa, o bloco <personalidade> sempre diz como ela está hoje (todos os traços, em
    # palavras). Desligado, só diz o que se afastou do jeito de sempre e o que a marcou.
    always_describe: bool = True
    # Meia-vida do retorno ao baseline: uma fase passa.
    baseline_half_life_days: float = Field(default=30.0, ge=1.0, le=3650.0)
    # Deriva (distância média dos traços ao baseline) a partir da qual entra um aviso no diário.
    drift_warning: float = Field(default=0.1, ge=0.0, le=1.0)
    # Os traços do ponto de partida (ajustáveis pelo Petrus; uma lista nova substitui a lista toda).
    traits: list[TraitDefault] = Field(default_factory=lambda: list(DEFAULT_TRAITS))


class V2OpinionSettings(BaseModel):
    """Opiniões dela na conversa (ADR-017, seção 10)."""

    # Quantas das crenças mais firmes (faixa "tenho bastante certeza", portanto de 3 fontes ou mais)
    # entram em toda conversa, além das que tocam o assunto. 0 desliga. Nunca no modo de acolher.
    always_on: int = Field(default=2, ge=0, le=5)


class V2RefineSettings(BaseModel):
    """O refinamento das memórias pelo sonho (ADR-018)."""

    enabled: bool = True
    # Memórias brutas refinadas por sonho, das mais recentes para as mais antigas.
    max_per_night: int = Field(default=20, ge=0, le=500)
    # Turnos de cada lado do turno de origem que entram como contexto da lembrança.
    context_turns: int = Field(default=2, ge=0, le=10)
    # Memórias vizinhas oferecidas ao modelo como possíveis ligações.
    neighbors: int = Field(default=3, ge=0, le=10)


class MemoryV2Settings(BaseModel):
    """Memória V2: do fato à personalidade viva (ADR-017)."""

    # Liga a V2 inteira. Exige `memory.session.store: mongo`. Desligada, nada muda em relação à v1.
    enabled: bool = False
    # Teto (estimado) do bloco da V2 no prompt da conversa.
    max_tokens: int = Field(default=600, ge=100, le=8000)
    night: V2NightSettings = Field(default_factory=V2NightSettings)
    beliefs: V2BeliefSettings = Field(default_factory=V2BeliefSettings)
    opinions: V2OpinionSettings = Field(default_factory=V2OpinionSettings)
    refine: V2RefineSettings = Field(default_factory=V2RefineSettings)
    concerns: V2ConcernSettings = Field(default_factory=V2ConcernSettings)
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


class MetadocsSettings(BaseModel):
    """A Juniper consultando a própria documentação (ADR-025): busca local, sem LLM nem rede."""

    # Liga a tool `docs_search` e o contexto automático. Só lê docs/pages e docs/decisions.
    enabled: bool = True
    # Pergunta sobre ela mesma (detectada por palavras-chave) já leva os trechos no turno, sem
    # uma volta extra ao LLM. Desligado, só a tool responde.
    auto_context: bool = True
    # Quantas seções entram por consulta.
    top_k: int = Field(default=3, ge=1, le=8)
    # Teto de caracteres dos trechos (somados) entregues ao modelo.
    max_chars: int = Field(default=1500, ge=300, le=6000)


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
    music: MusicSettings = Field(default_factory=MusicSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    trace: TraceSettings = Field(default_factory=TraceSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    metadocs: MetadocsSettings = Field(default_factory=MetadocsSettings)

    # Segredos (sempre via env)
    groq_api_key: SecretStr | None = Field(default=None, alias="JUNIPER_GROQ_API_KEY")
    deepinfra_api_key: SecretStr | None = Field(default=None, alias="JUNIPER_DEEPINFRA_API_KEY")
    youtube_api_key: SecretStr | None = Field(default=None, alias="JUNIPER_YOUTUBE_API_KEY")
    youtube_client_id: str | None = Field(default=None, alias="JUNIPER_YOUTUBE_CLIENT_ID")
    youtube_client_secret: SecretStr | None = Field(
        default=None, alias="JUNIPER_YOUTUBE_CLIENT_SECRET"
    )
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
