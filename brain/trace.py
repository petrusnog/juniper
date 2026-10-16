"""O diário das mensagens (`message.log`): cada interação contada em texto corrido.

Serve para ver, na prática, o caminho que uma mensagem percorre: o terminal, o cliente remoto, o
WebSocket, o servidor, a conversa, o agente, a memória, o LLM, as ferramentas e a volta. É
separado do log de eventos em JSON (`logs/AAAA-MM-DD.jsonl`) e fica desligado por padrão:
`trace.enabled: true` no config.yaml liga (o config.yaml pessoal chega também ao contêiner de
desenvolvimento, então uma chave só liga os dois lados).

Quem escreve o arquivo é sempre o **cliente**. Os passos que acontecem no servidor viajam até ele
numa mensagem `trace` do protocolo (só quando o cliente pediu no `hello`), e entram no arquivo na
ordem em que chegam. No modo `local` não há servidor: tudo acontece no mesmo processo e as camadas
do servidor simplesmente não existem.

Os eventos que o `event_log` já registra (pedido ao LLM, resposta, ferramentas, memória, tempos,
erros) são traduzidos para frases aqui, em vez de cada módulo ganhar mais um `print`. Segredos
passam pelo mesmo mascaramento do `event_log`.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from brain.config import settings
from brain.eventlog import redact

# Quantos caracteres de um texto (mensagem, resposta, saída de tool) vão inteiros para o arquivo.
MAX_QUOTE: Final = 600

LineSink = Callable[[str, str, datetime], None]
"""`sink(camada, texto, instante)`: para onde vai um passo."""


def _clip(text: object, limit: int = MAX_QUOTE) -> str:
    flat = " ".join(str(redact(str(text))).split())  # segredos mascarados, como no event_log
    return flat if len(flat) <= limit else f"{flat[:limit]}… (+{len(flat) - limit} caracteres)"


def _quote(text: object, limit: int = MAX_QUOTE) -> str:
    return f"«{_clip(text, limit)}»"


def _ms(value: object) -> str:
    return f"{value} ms" if isinstance(value, int | float) else "?"


_DECISIONS: Final = {
    "auto": "executei direto, porque essa ferramenta não precisa de confirmação",
    "confirmed": "executei porque você confirmou",
    "confirmed_in_context": "executei porque você já tinha dito sim a uma pergunta que citava isso",
    "pending": "NÃO executei: a ação ficou pendente até você confirmar na conversa",
    "denied": "NÃO executei: a confirmação foi negada",
    "forbidden": "NÃO executei: essa ferramenta é proibida",
    "unknown": "NÃO executei: a ferramenta não existe",
    "invalid": "NÃO executei: os argumentos não batem com o esquema da ferramenta",
    "precheck_failed": "NÃO executei: a checagem prévia já mostrou que daria errado",
    "crashed": "a ferramenta falhou ao executar",
}

_TIERS: Final = {
    "read": "só leitura",
    "write": "altera algo",
    "destructive": "destrutiva",
    "forbidden": "proibida",
}


@dataclass
class Interaction:
    """Uma interação (uma mensagem sua e tudo o que ela provoca) e para onde seus passos vão."""

    sink: LineSink
    number: int = 0
    notes: dict[str, Any] = field(default_factory=dict)


_current: contextvars.ContextVar[Interaction | None] = contextvars.ContextVar(
    "message_trace_interaction", default=None
)


class FileSink:
    """Acrescenta linhas a um arquivo (modo 600), de qualquer thread ou processo."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(text)
        except OSError:
            return  # o diário nunca derruba a aplicação


def _stamp(moment: datetime) -> str:
    return f"{moment:%H:%M:%S}.{moment.microsecond // 1000:03d}"


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.agent.timezone))


class MessageTrace:
    def __init__(self) -> None:
        self._counter = 0
        self._file: FileSink | None = None

    # --- ligado? onde? ---

    @property
    def enabled(self) -> bool:
        return settings.trace.enabled

    def path(self) -> Path:
        configured = settings.trace.file
        if configured is not None:
            return configured.expanduser()
        home = settings.paths.logs if settings.paths is not None else Path.home() / ".juniper/logs"
        return home / "message.log"

    def _file_sink(self) -> FileSink:
        path = self.path()
        if self._file is None or self._file.path != path:
            self._file = FileSink(path)
        return self._file

    def file_line(self, layer: str, text: str, moment: datetime | None = None) -> None:
        """Escreve um passo direto no arquivo (é o que o cliente faz com o que chega)."""
        if not self.enabled:
            return
        moment = moment or _now()
        self._file_sink().write(f"[{_stamp(moment)}] {layer}. {text}\n")

    # --- interações ---

    def begin(self, title: str, sink: LineSink | None = None) -> Interaction:
        """Abre uma interação: escreve o cabeçalho e passa a valer para o código que se segue.

        Sem `sink`, os passos vão para o arquivo. No servidor, o `sink` leva os passos ao cliente.
        """
        self._counter += 1
        interaction = Interaction(sink or self._to_file, self._counter)
        _current.set(interaction)
        if sink is None and self.enabled:
            moment = _now()
            bar = "═" * 78
            weekday = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][
                moment.weekday()
            ] + ("-feira" if moment.weekday() < 5 else "")
            self._file_sink().write(
                f"\n{bar}\nINTERAÇÃO {self._counter} · {weekday}, {moment:%d/%m/%Y} às "
                f"{moment:%H:%M:%S} · {title}\n{bar}\n"
            )
        return interaction

    def _to_file(self, layer: str, text: str, moment: datetime) -> None:
        self.file_line(layer, text, moment)

    @contextlib.contextmanager
    def bound(self, sink: LineSink) -> Iterator[Interaction]:
        """Faz os passos do código dentro do bloco irem para `sink` (o servidor usa isto)."""
        interaction = Interaction(sink)
        token = _current.set(interaction)
        try:
            yield interaction
        finally:
            _current.reset(token)

    def bind(self, sink: LineSink) -> Interaction:
        """Como `bound`, mas vale para o resto do contexto (a tarefa do turno herda)."""
        interaction = Interaction(sink)
        _current.set(interaction)
        return interaction

    def step(self, layer: str, text: str) -> None:
        """Um passo da interação em andamento. Sem interação (ou desligado), não faz nada."""
        interaction = _current.get()
        if interaction is None:
            return
        try:
            interaction.sink(layer, text, _now())
        except Exception:  # noqa: BLE001  (o diário nunca derruba a aplicação)
            return

    @property
    def active(self) -> bool:
        return _current.get() is not None

    # --- tradução dos eventos do event_log ---

    def on_event(self, event: str, fields: dict[str, Any]) -> None:
        if _current.get() is None:
            return
        translate = _TRANSLATORS.get(event)
        if translate is None:
            return
        line = translate(fields)
        if line is not None:
            self.step(*line)


# ---------------------------------------------------------------------------------------------
# Eventos → frases
# ---------------------------------------------------------------------------------------------

Line = tuple[str, str] | None


def _llm_request(f: dict[str, Any]) -> Line:
    return (
        "Agente → roteador de LLMs",
        f"Montei o pedido ao modelo com {f.get('messages')} mensagens (a persona, o histórico e o "
        f"que a memória trouxe) e {f.get('tools')} ferramentas disponíveis, e o entreguei ao "
        "roteador de provedores.",
    )


def _llm_response(f: dict[str, Any]) -> Line:
    usage = f.get("usage") or {}
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    calls = f.get("tool_calls") or []
    text = (
        f"O provedor {f.get('provider')} respondeu em {_ms(f.get('latency_ms'))} "
        f"(motivo de parada: {f.get('finish_reason') or 'não informado'})"
    )
    text += f", gastando {tokens} tokens" if tokens else ""
    text += f". Veio {f.get('chars', 0)} caracteres de texto"
    text += f" e o pedido para usar as ferramentas: {', '.join(calls)}." if calls else "."
    return "LLM", text


def _failover(f: dict[str, Any]) -> Line:
    return (
        "Roteador de LLMs",
        f"O provedor {f.get('provider')} falhou ({f.get('type')}), então passei para o próximo da "
        "fila de failover.",
    )


def _tool_call(f: dict[str, Any]) -> Line:
    tier = _TIERS.get(str(f.get("tier")), str(f.get("tier")))
    decision = _DECISIONS.get(str(f.get("decision")), str(f.get("decision")))
    result = (
        f"funcionou e devolveu {_quote(f.get('output') or 'ok', 300)}"
        if f.get("success")
        else f"não deu certo: {_quote(f.get('error') or 'sem detalhe', 300)}"
    )
    return (
        "Ferramentas",
        f"O modelo pediu a ferramenta «{f.get('name')}» ({tier}) com os argumentos "
        f"{_clip(f.get('args'), 300)}. Decisão do despachante: {decision}. Resultado: {result} "
        f"({_ms(f.get('duration_ms'))}).",
    )


def _memory_recall(f: dict[str, Any]) -> Line:
    injected = f.get("injected") or []
    profile = f.get("profile_tokens") or 0
    parts = ["Busquei na memória de longo prazo pelo significado da sua mensagem."]
    if injected:
        kinds = ", ".join(f"{i.get('type')} (nota {i.get('score')})" for i in injected)
        parts.append(
            f"Achei {f.get('found')} lembrança(s) acima da nota mínima e injetei "
            f"{len(injected)} no prompt: {kinds}."
        )
    else:
        parts.append("Nada passou da nota mínima, então nenhuma lembrança entrou no prompt.")
    if profile:
        parts.append(f"O seu perfil também foi junto (cerca de {profile} tokens).")
    return "Memória de longo prazo", " ".join(parts)


def _timing(f: dict[str, Any]) -> Line:
    if f.get("scope") != "turn":
        return None
    stages = f.get("stages_ms") or {}
    detail = ", ".join(f"{name} {_ms(ms)}" for name, ms in stages.items())
    return (
        "Cronômetro",
        f"O turno inteiro levou {_ms(f.get('total_ms'))} no agente, com {f.get('llm_calls', 0)} "
        f"chamada(s) ao LLM. Por etapa: {detail or 'sem detalhe'}.",
    )


def _error(f: dict[str, Any]) -> Line:
    return "Erro", f"Algo falhou em {f.get('where')}: {f.get('type')}."


def _extraction(f: dict[str, Any]) -> Line:
    if f.get("scope") != "conversation":
        return None
    return (
        "Memória de longo prazo",
        f"A extração em segundo plano terminou: {f.get('added', 0)} lembrança(s) nova(s), "
        f"{f.get('superseded', 0)} atualizada(s)"
        + (f", com o aviso «{_clip(f['error'], 120)}»." if f.get("error") else "."),
    )


_TRANSLATORS: Final[dict[str, Callable[[dict[str, Any]], Line]]] = {
    "llm_request": _llm_request,
    "llm_response": _llm_response,
    "failover": _failover,
    "tool_call": _tool_call,
    "memory_recall": _memory_recall,
    "timing": _timing,
    "error": _error,
    "memory_extraction": _extraction,
}

message_trace = MessageTrace()


def quote(text: object, limit: int = MAX_QUOTE) -> str:
    """Um texto entre aspas, numa linha só e sem passar do limite (para os passos escreverem)."""
    return _quote(text, limit)
