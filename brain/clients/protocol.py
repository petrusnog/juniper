"""Contrato do protocolo cliente ↔ brain (ADR-012) em código.

Os modelos pydantic daqui são a fonte da verdade: deles sai o JSON Schema versionado
(`docs/protocol/schema.json`, gerado por `scripts/export_protocol_schema.py`) e, dele, os tipos
TypeScript do Electron (ADR-015). Em caso de divergência com o ADR, vale o schema gerado.

Este módulo só descreve e valida mensagens: não abre conexão nem conhece o agente. O núcleo
(`brain/core`) não importa nada de `brain/clients`.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from brain.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from brain.memory.errors import MemoryUnavailableError

PROTOCOL_VERSION: Final = 1
"""Só muda em quebra de compatibilidade (campos e tipos novos não mudam)."""

MAX_MESSAGE_BYTES: Final = 1024 * 1024
MAX_CHAT_TEXT_CHARS: Final = 32_000

# ---------------------------------------------------------------------------------------------
# Códigos de erro e de fechamento
# ---------------------------------------------------------------------------------------------

ErrorCode = Literal[
    "unauthorized",
    "unsupported_version",
    "bad_request",
    "turn_in_progress",
    "session_not_found",
    "llm_auth",
    "llm_rate_limit",
    "llm_unavailable",
    "llm_timeout",
    "llm_error",
    "memory_unavailable",
    "not_found",
    "executor_unavailable",
    "internal",
]

RETRYABLE: Final[dict[str, bool]] = {
    "unauthorized": False,
    "unsupported_version": False,
    "bad_request": False,
    "turn_in_progress": True,
    "session_not_found": False,
    "llm_auth": False,
    "llm_rate_limit": True,
    "llm_unavailable": True,
    "llm_timeout": True,
    "llm_error": False,
    "memory_unavailable": True,
    "not_found": False,
    "executor_unavailable": True,
    "internal": True,
}

CLOSE_PROTOCOL_VIOLATION: Final = 4400
CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_HEARTBEAT_TIMEOUT: Final = 4408
CLOSE_REPLACED: Final = 4409
CLOSE_UNSUPPORTED_VERSION: Final = 4426
CLOSE_GOING_AWAY: Final = 1001
CLOSE_SERVICE_RESTART: Final = 1012

# Do mais específico para o mais geral: `LLMError` fica por último.
_LLM_ERROR_CODES: Final[tuple[tuple[type[Exception], ErrorCode], ...]] = (
    (LLMAuthError, "llm_auth"),
    (LLMRateLimitError, "llm_rate_limit"),
    (LLMUnavailableError, "llm_unavailable"),
    (LLMTimeoutError, "llm_timeout"),
    (LLMError, "llm_error"),
    (MemoryUnavailableError, "memory_unavailable"),
)


def error_code_for(exc: BaseException) -> ErrorCode:
    """Código do protocolo para uma exceção do núcleo; o que não é conhecido vira `internal`."""
    if isinstance(exc, ProtocolError):
        return exc.code
    for exc_type, code in _LLM_ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    return "internal"


# ---------------------------------------------------------------------------------------------
# Erros tipados de (de)serialização
# ---------------------------------------------------------------------------------------------


class ProtocolError(Exception):
    """Mensagem que o protocolo não aceita. `code` é o `error.code` a devolver ao cliente."""

    code: ErrorCode = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidMessageError(ProtocolError):
    """JSON inválido, grande demais, tipo desconhecido, campo ausente ou fora do esperado."""

    code: ErrorCode = "bad_request"


class UnsupportedVersionError(ProtocolError):
    """`protocol_version` diferente da suportada (fechar com 4426)."""

    code: ErrorCode = "unsupported_version"

    def __init__(self, received: object) -> None:
        super().__init__(
            f"Versão do protocolo não suportada: {received!r} (suportada: {PROTOCOL_VERSION})."
        )
        self.received = received


# ---------------------------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------------------------


class _Message(BaseModel):
    """Campos desconhecidos são ignorados (compatibilidade para frente)."""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------------------------
# Conexão: hello / welcome / heartbeat / error
# ---------------------------------------------------------------------------------------------


def _require_one_target(session_id: str | None, latest: bool | None) -> None:
    """A retomada diz *qual* conversa: `session_id` ou `latest: true`, nunca os dois nem nenhum."""
    if bool(session_id) == bool(latest):
        raise ValueError("informe 'session_id' ou 'latest: true' (apenas um dos dois)")


class ClientInfo(_Message):
    kind: str
    """`cli` ou `desktop`; novos tipos entram sem quebrar nada."""
    version: str
    device_id: str
    device_name: str | None = None


class Resume(_Message):
    """Retomada da conversa: `session_id` ou `latest`."""

    session_id: str | None = None
    latest: bool | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Resume:
        _require_one_target(self.session_id, self.latest)
        return self


class Hello(_Message):
    type: Literal["hello"] = "hello"
    protocol_version: int
    client: ClientInfo
    resume: Resume | None = None
    trace: bool | None = None
    """Pede ao servidor os passos de cada turno (mensagens `trace`) para o `message.log`."""


class WelcomeSession(_Message):
    id: str
    resumed: bool
    turns: int


class Welcome(_Message):
    type: Literal["welcome"] = "welcome"
    protocol_version: int
    server_version: str
    heartbeat_seconds: int
    session: WelcomeSession


class Ping(_Message):
    type: Literal["ping"] = "ping"


class Pong(_Message):
    type: Literal["pong"] = "pong"


class Trace(_Message):
    """Um passo que o servidor deu (só vem se o `hello` pediu e o servidor permite)."""

    type: Literal["trace"] = "trace"
    layer: str
    text: str
    at: str
    """Instante do passo no servidor (ISO 8601, com fuso)."""


class Error(_Message):
    type: Literal["error"] = "error"
    reply_to: str | None = None
    code: ErrorCode
    message: str
    retryable: bool

    @classmethod
    def from_code(cls, code: ErrorCode, message: str, reply_to: str | None = None) -> Error:
        return cls(reply_to=reply_to, code=code, message=message, retryable=RETRYABLE[code])

    @classmethod
    def from_exception(cls, exc: BaseException, message: str, reply_to: str | None = None) -> Error:
        """Erro de pedido a partir de uma exceção do núcleo (LLM, memória, protocolo)."""
        return cls.from_code(error_code_for(exc), message, reply_to)


# ---------------------------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------------------------

FinishReason = Literal["ok", "cancelled", "max_iterations", "error"]


class ChatSend(_Message):
    type: Literal["chat.send"] = "chat.send"
    id: str
    text: str = Field(max_length=MAX_CHAT_TEXT_CHARS)


class ChatStarted(_Message):
    type: Literal["chat.started"] = "chat.started"
    reply_to: str
    turn_id: str
    session_id: str


class ChatToken(_Message):
    type: Literal["chat.token"] = "chat.token"
    turn_id: str
    text: str


class ChatEnd(_Message):
    type: Literal["chat.end"] = "chat.end"
    turn_id: str
    finish: FinishReason
    usage: dict[str, Any] | None = None
    timing: dict[str, Any] | None = None


class ChatCancel(_Message):
    type: Literal["chat.cancel"] = "chat.cancel"
    turn_id: str


# ---------------------------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------------------------


class SessionNew(_Message):
    type: Literal["session.new"] = "session.new"
    id: str


class SessionResume(_Message):
    type: Literal["session.resume"] = "session.resume"
    id: str
    session_id: str | None = None
    latest: bool | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> SessionResume:
        _require_one_target(self.session_id, self.latest)
        return self


class SessionList(_Message):
    type: Literal["session.list"] = "session.list"
    id: str
    limit: int | None = Field(default=None, ge=1)


class HistoryItem(_Message):
    role: str
    text: str


class SessionState(_Message):
    type: Literal["session.state"] = "session.state"
    reply_to: str
    session_id: str
    turns: int
    history: list[HistoryItem] | None = None
    note: str = ""


class SessionSummary(_Message):
    id: str
    updated_at: str
    turns: int
    preview: str


class SessionListed(_Message):
    type: Literal["session.listed"] = "session.listed"
    reply_to: str
    sessions: list[SessionSummary]


class SessionDetached(_Message):
    type: Literal["session.detached"] = "session.detached"
    session_id: str
    reason: str


# ---------------------------------------------------------------------------------------------
# Memória: pedido discriminado por `op`; resposta com `data` livre por operação
# ---------------------------------------------------------------------------------------------


class MemoryListArgs(_Message):
    type: str | None = None
    all: bool | None = None


class MemorySearchArgs(_Message):
    query: str
    limit: int | None = Field(default=None, ge=1)
    threshold: float | None = None


class MemoryForgetArgs(_Message):
    id: str


class MemoryExportArgs(_Message):
    vectors: bool | None = None


class MemoryNoArgs(_Message):
    """Operações sem argumentos (`profile.get`, `profile.forget`)."""


class MemoryProfileSetArgs(_Message):
    text: str
    name: str | None = None
    birth_date: str | None = None
    nationality: str | None = None
    gender: str | None = None


class _MemoryRequestBase(_Message):
    type: Literal["memory.request"] = "memory.request"
    id: str


class MemoryListRequest(_MemoryRequestBase):
    op: Literal["list"] = "list"
    args: MemoryListArgs = Field(default_factory=MemoryListArgs)


class MemorySearchRequest(_MemoryRequestBase):
    op: Literal["search"] = "search"
    args: MemorySearchArgs


class MemoryForgetRequest(_MemoryRequestBase):
    op: Literal["forget"] = "forget"
    args: MemoryForgetArgs


class MemoryExportRequest(_MemoryRequestBase):
    op: Literal["export"] = "export"
    args: MemoryExportArgs = Field(default_factory=MemoryExportArgs)


class MemoryProfileGetRequest(_MemoryRequestBase):
    op: Literal["profile.get"] = "profile.get"
    args: MemoryNoArgs = Field(default_factory=MemoryNoArgs)


class MemoryProfileSetRequest(_MemoryRequestBase):
    op: Literal["profile.set"] = "profile.set"
    args: MemoryProfileSetArgs


class MemoryProfileForgetRequest(_MemoryRequestBase):
    op: Literal["profile.forget"] = "profile.forget"
    args: MemoryNoArgs = Field(default_factory=MemoryNoArgs)


_MemoryRequestUnion = (
    MemoryListRequest
    | MemorySearchRequest
    | MemoryForgetRequest
    | MemoryExportRequest
    | MemoryProfileGetRequest
    | MemoryProfileSetRequest
    | MemoryProfileForgetRequest
)
MemoryRequest = Annotated[_MemoryRequestUnion, Field(discriminator="op")]
"""`memory reindex` é administrativo e local ao servidor: não existe como `op`."""


class MemoryResult(_Message):
    type: Literal["memory.result"] = "memory.result"
    reply_to: str
    ok: bool
    data: dict[str, Any] | None = None


# ---------------------------------------------------------------------------------------------
# Executor (ADR-007 / ADR-014)
# ---------------------------------------------------------------------------------------------


class ToolSpec(_Message):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    tier: str


class Register(_Message):
    type: Literal["register"] = "register"
    tools: list[ToolSpec]


class Registered(_Message):
    type: Literal["registered"] = "registered"
    tools: list[ToolSpec]


class Invoke(_Message):
    type: Literal["invoke"] = "invoke"
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(gt=0)
    tier: str | None = None
    """O tier que o executor declarou no `register`; o cliente confere (ADR-014, seção 5)."""


class Result(_Message):
    type: Literal["result"] = "result"
    call_id: str
    success: bool
    output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------------------------
# Tools do servidor (`juniper tools` no modo server)
# ---------------------------------------------------------------------------------------------


class ToolsRequest(_Message):
    type: Literal["tools.request"] = "tools.request"
    id: str


class ToolInfo(_Message):
    name: str
    description: str
    tier: str
    locality: str


class ToolsListed(_Message):
    type: Literal["tools.listed"] = "tools.listed"
    reply_to: str
    tools: list[ToolInfo]


# ---------------------------------------------------------------------------------------------
# Uniões e (de)serialização
# ---------------------------------------------------------------------------------------------

ClientMessage = (
    Hello
    | Pong
    | ChatSend
    | ChatCancel
    | SessionNew
    | SessionResume
    | SessionList
    | MemoryRequest
    | Register
    | Result
    | ToolsRequest
)
ServerMessage = (
    Welcome
    | Ping
    | Error
    | ChatStarted
    | ChatToken
    | ChatEnd
    | SessionState
    | SessionListed
    | SessionDetached
    | MemoryResult
    | Registered
    | Invoke
    | ToolsListed
    | Trace
)
Message = Annotated[ClientMessage | ServerMessage, Field(discriminator="type")]
"""Todas as mensagens, discriminadas por `type` (e `memory.request` também por `op`)."""

_ADAPTER: Final[TypeAdapter[Any]] = TypeAdapter(Message)

_CLIENT_TYPES: Final = frozenset(
    {
        "hello",
        "pong",
        "chat.send",
        "chat.cancel",
        "session.new",
        "session.resume",
        "session.list",
        "memory.request",
        "register",
        "result",
        "tools.request",
    }
)
_SERVER_TYPES: Final = frozenset(
    {
        "welcome",
        "ping",
        "error",
        "chat.started",
        "chat.token",
        "chat.end",
        "session.state",
        "session.listed",
        "session.detached",
        "memory.result",
        "registered",
        "invoke",
        "tools.listed",
        "trace",
    }
)


def parse_message(
    raw: str | bytes | dict[str, Any], *, source: Literal["client", "server", "any"] = "any"
) -> ClientMessage | ServerMessage:
    """Converte um quadro JSON (ou dict) no modelo da mensagem.

    `source` restringe quem pode ter enviado: o servidor usa `"client"`, o cliente `"server"`.
    Levanta `InvalidMessageError` (malformada) ou `UnsupportedVersionError` (versão do
    `hello`/`welcome` diferente de `PROTOCOL_VERSION`).
    """
    if isinstance(raw, (str, bytes)):
        if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_MESSAGE_BYTES:
            raise InvalidMessageError("Mensagem maior que o limite de 1 MiB.")
        try:
            payload: Any = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidMessageError(f"JSON inválido: {exc}") from exc
    else:
        payload = raw

    if not isinstance(payload, dict):
        raise InvalidMessageError("A mensagem deve ser um objeto JSON.")
    msg_type = payload.get("type")
    if not isinstance(msg_type, str):
        raise InvalidMessageError("Campo 'type' ausente ou inválido.")
    if msg_type not in _CLIENT_TYPES | _SERVER_TYPES:
        raise InvalidMessageError(f"Tipo de mensagem desconhecido: {msg_type!r}.")
    if (source == "client" and msg_type not in _CLIENT_TYPES) or (
        source == "server" and msg_type not in _SERVER_TYPES
    ):
        raise InvalidMessageError(f"Tipo {msg_type!r} não é aceito nesta direção.")

    if msg_type in ("hello", "welcome"):
        version = payload.get("protocol_version")
        if version != PROTOCOL_VERSION or isinstance(version, bool):
            raise UnsupportedVersionError(version)

    try:
        message: ClientMessage | ServerMessage = _ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise InvalidMessageError(_describe(exc)) from exc
    return message


def parse_client_message(raw: str | bytes | dict[str, Any]) -> ClientMessage:
    """`parse_message` para quem recebe do cliente (o servidor)."""
    return cast(ClientMessage, parse_message(raw, source="client"))


def parse_server_message(raw: str | bytes | dict[str, Any]) -> ServerMessage:
    """`parse_message` para quem recebe do servidor (o cliente)."""
    return cast(ServerMessage, parse_message(raw, source="server"))


def serialize_message(message: BaseModel) -> str:
    """JSON compacto, UTF-8, uma mensagem por quadro. Campos `None` são omitidos."""
    text = message.model_dump_json(exclude_none=True)
    if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise InvalidMessageError("Mensagem maior que o limite de 1 MiB.")
    return text


def _describe(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return f"Mensagem inválida em '{where}': {first['msg']}."


# ---------------------------------------------------------------------------------------------
# JSON Schema versionado
# ---------------------------------------------------------------------------------------------


def json_schema() -> dict[str, Any]:
    """JSON Schema do protocolo (determinístico), com a versão em `x-protocol-version`."""
    schema: dict[str, Any] = _ADAPTER.json_schema(mode="validation")
    schema["title"] = "Juniper client-brain protocol"
    schema["x-protocol-version"] = PROTOCOL_VERSION
    schema["x-error-codes"] = {code: RETRYABLE[code] for code in sorted(RETRYABLE)}
    schema["x-max-message-bytes"] = MAX_MESSAGE_BYTES
    return schema


def schema_text() -> str:
    """O conteúdo exato de `docs/protocol/schema.json`."""
    return json.dumps(json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
