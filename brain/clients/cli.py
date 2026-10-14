"""Cliente CLI da Juniper.

Entry point oficial (`juniper = brain.clients.cli:app` no pyproject). Cliente fino: captura
o texto e mostra os tokens em streaming. Confirmações acontecem na própria conversa (sem `[y/N]`).
Toda a lógica vive em `brain.core`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import readline  # noqa: F401  (enables UTF-8-aware line editing in input())
import subprocess
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated, Any, Optional
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.status import Status
from rich.table import Table
from rich.text import Text
from typer.core import TyperGroup

from brain import __version__
from brain.clients import voice
from brain.config import settings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.session import Session
from brain.llm.errors import LLMAuthError, LLMError, LLMRateLimitError
from brain.llm.router import PROVIDERS, LLMRouter
from brain.memory.errors import MemoryUnavailableError
from brain.memory.longterm import (
    LongTermMemory,
    open_long_term_memory,
    open_memory_for_commands,
)
from brain.memory.session import SessionStore
from brain.memory.stores import close_session_store, open_session_store
from brain.tools import memory as memory_tools
from brain.tools.base import Tier
from brain.tools.juniper_cmds import legacy_argv
from brain.tools.registry import registry

console = Console()

# The terminal speaks only through voice.py; library warnings (e.g. the router's failover
# warning) go to the structured event log instead of leaking raw into the conversation.
logging.getLogger("brain").addHandler(logging.NullHandler())


class JuniperGroup(TyperGroup):
    """Command group where anything that is not a command is a chat.

    ``juniper`` opens the chat, ``juniper <mensagem>`` sends one message. Typed loosely on
    purpose: typer >= 0.2x vendors click, older versions depend on it.
    """

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        args = list(args)
        if args and args[0] in ("--help", "-h"):
            return super().parse_args(ctx, args)
        if not args or args[0] not in self.commands:
            args = [CHAT_COMMAND, *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    name="juniper",
    help="Juniper — agente autônomo de automação e conversação.",
    cls=JuniperGroup,
)

# Gendered article + display name, so the messages read right ("a Groq", "o Ollama").
_PROVIDER_PT = {"deepinfra": ("a", "DeepInfra"), "groq": ("a", "Groq"), "ollama": ("o", "Ollama")}
_EXIT = {"/sair", "/exit"}
CHAT_COMMAND = "_chat"


class LineReader:
    """Reads stdin lines without blocking the event loop, and survives timeouts.

    ``input()`` cannot be cancelled. If a read times out (confirmation), the reader thread
    stays alive; the next ``read`` reuses it instead of starting a second one that would
    race for the same line. The thread is a daemon, so it never blocks process exit.
    """

    def __init__(self) -> None:
        self._pending: asyncio.Future[str] | None = None

    async def read(self, prompt: str) -> str:
        if self._pending is None:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()

            def worker() -> None:
                try:
                    line = input(prompt)
                    # Undecodable bytes arrive as lone surrogates and would break JSON encoding.
                    line = line.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
                    loop.call_soon_threadsafe(_resolve, future, line, None)
                except BaseException as exc:  # EOFError, KeyboardInterrupt in the thread...
                    loop.call_soon_threadsafe(_resolve, future, None, exc)

            self._pending = future
            threading.Thread(target=worker, daemon=True).start()
        try:
            return await asyncio.shield(self._pending)
        finally:
            if self._pending is not None and self._pending.done():
                self._pending = None


def _resolve(future: asyncio.Future[str], value: str | None, exc: BaseException | None) -> None:
    if future.done():
        return
    if exc is not None:
        future.set_exception(exc)
    else:
        future.set_result(value or "")


# The model sometimes opens with its own name ("Juniper:", "🌿 JUNIPER:"); the terminal already
# printed the tag, so a leading echo of it is dropped.
_SELF_TAG = re.compile(r"^\s*(?:🌿\s*)?juniper\s*:\s*", re.IGNORECASE)
# How much of a reply is held back to look for that echo (a token or two).
_HEAD_CHARS = 16


class Printer:
    """Console output that tracks whether the cursor is mid-line (streaming).

    The Juniper tag is printed lazily, right before the first text of a reply, so a turn that
    fails before producing text does not leave an empty "🌿 JUNIPER:" line behind.
    """

    def __init__(self, out: Console) -> None:
        self.out = out
        self._mid_line = False
        self._need_tag = False
        self._head: str | None = None  # start of the reply, held while we look for a self-tag
        self._status: Status | None = None

    def begin_reply(self) -> None:
        """The next streamed token opens a Juniper line; until it arrives, show a spinner."""
        self._need_tag = True
        self._head = ""
        self._spin("pensando...")

    def _spin(self, text: str) -> None:
        """Spinner for the wait (cold LLMs can take minutes). Only on a real terminal."""
        self._unspin()
        if self.out.is_terminal:
            self._status = self.out.status(Text.assemble(voice.tag_text(), (text, "dim")))
            self._status.start()

    def _unspin(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def notify_failover(self, failed: str, exc: LLMError, following: str | None) -> None:
        """A provider failed and another takes over: say so instead of going silent."""
        art, name = _PROVIDER_PT.get(failed, ("o", failed))
        if isinstance(exc, LLMAuthError):
            why = f"{art} {name} recusou as credenciais"
        elif isinstance(exc, LLMRateLimitError):
            why = f"o limite d{art} {name} acabou"
        else:
            why = f"{art} {name} não respondeu"
        if following is None:
            self.say(f"Falhou: {why}.", "yellow")
            return
        next_art, next_name = _PROVIDER_PT.get(following, ("o", following))
        extra = " Sem GPU isso pode levar alguns minutos." if following == "ollama" else ""
        self.say(f"{why[0].upper()}{why[1:]}. Vou tentar {next_art} {next_name}.{extra}", "yellow")
        self._spin(f"esperando {next_art} {next_name}...")

    def token(self, text: str) -> None:
        if self._head is not None:
            self._head += text
            if len(self._head) < _HEAD_CHARS and "\n" not in self._head:
                return
            self._flush_head()
            return
        self._write(text)

    def _flush_head(self) -> None:
        head, self._head = self._head or "", None
        head = _SELF_TAG.sub("", head, count=1)
        if head:
            self._write(head)

    def _write(self, text: str) -> None:
        self._unspin()
        if self._need_tag:
            voice.start_reply(self.out)
            self._need_tag = False
        self.out.print(text, end="", markup=False, highlight=False, soft_wrap=True)
        self._mid_line = not text.endswith("\n")

    def say(self, text: str, style: str | None = None) -> None:
        """A fixed Juniper line (always on its own line, after any streamed text)."""
        self._unspin()
        self.end_line()
        voice.say(self.out, text, style)
        self._need_tag = True  # whatever the model streams next is a new Juniper line
        self._head = ""

    def end_line(self) -> None:
        self._unspin()
        if self._head:
            self._flush_head()
        self._head = None
        if self._mid_line:
            self.out.print()
            self._mid_line = False


_KEY_VARS = {"deepinfra": "JUNIPER_DEEPINFRA_API_KEY", "groq": "JUNIPER_GROQ_API_KEY"}


def friendly_error(exc: LLMError) -> str:
    """Translate an LLM failure into an actionable PT-BR message (plain text)."""
    detail = str(exc)
    hints: list[str] = []
    for name in re.findall(r"(\w+): LLMAuthError", detail):
        if name in _KEY_VARS:
            hints.append(
                f"{_PROVIDER_PT[name][1]} sem chave válida: defina {_KEY_VARS[name]} no .env."
            )
    for name in re.findall(r"(\w+): LLMRateLimitError", detail):
        art, shown = _PROVIDER_PT.get(name, ("o", name))
        hints.append(f"Limite d{art} {shown} atingido: espere um minuto.")
    if re.search(r"ollama: (LLMUnavailableError|LLMTimeoutError)", detail):
        hints.append("Ollama não respondeu: ele está rodando? (`ollama serve`)")
    text = "Não consegui falar com nenhum LLM agora."
    if hints:
        text += "\n" + "\n".join(f"· {h}" for h in hints)
    return f"{text}\nDetalhe técnico: {detail}"


def build_router(provider: str | None) -> LLMRouter:
    """Router over ``llm.priority``, or over a single provider when ``--provider`` is given."""
    if provider is None:
        return LLMRouter()
    if provider not in PROVIDERS:
        raise ValueError(
            f"provider desconhecido: {provider!r} (use {' ou '.join(sorted(PROVIDERS))})"
        )
    return LLMRouter([PROVIDERS[provider]()])


async def converse(agent: Agent, printer: Printer, reader: LineReader, message: str | None) -> int:
    """One-shot when ``message`` is given, REPL otherwise. Returns the exit code."""
    pending = message
    while True:
        if pending is None:
            try:
                text = (await reader.read(voice.user_prompt())).strip()
            except EOFError:
                printer.out.print()
                printer.say("Até logo!")
                return 0
            if text in _EXIT:
                printer.say("Até logo!")
                return 0
            if not text:
                continue
        else:
            text = pending
        printer.begin_reply()
        code = 0
        try:
            await agent.run(text, on_token=printer.token)
        except LLMError as exc:
            printer.say(friendly_error(exc), "red")
            code = 1
        printer.end_line()
        if message is not None:
            return code
        printer.out.print()
        pending = None


def _when(timestamp: str) -> str:
    """``2026-10-13T22:15:00+00:00`` as local time: ``13/10 às 19:15``."""
    moment = datetime.fromisoformat(timestamp).astimezone(ZoneInfo(settings.agent.timezone))
    return f"{moment:%d/%m} às {moment:%H:%M}"


def previous_session(store: SessionStore) -> tuple[Session | None, str]:
    """The conversation ``--resume`` picks up, and what Juniper says about it."""
    try:
        session = store.latest()
    except (OSError, MemoryUnavailableError):
        session = None
    if session is None:
        return None, "Não achei nenhuma conversa anterior, então vamos começar uma nova."
    turns = sum(1 for m in session.history if m.role == "user")
    plural = "mensagem" if turns == 1 else "mensagens"
    return session, f"Retomando a conversa de {_when(session.updated_at)} ({turns} {plural} suas)."


async def prepare_memory(memory: LongTermMemory | None, printer: Printer) -> None:
    """Fetch the embedding model up front, saying so, instead of freezing the first reply."""
    if memory is None:
        return
    try:
        if memory.needs_model_download():
            printer.say("Baixando o modelo da minha memória (~120 MB, só desta vez)...")
            await asyncio.to_thread(lambda: memory.embedder.dimension)
    except Exception as exc:  # recall will notice and skip memory; the chat itself goes on
        printer.say(f"Vou conversar sem consultar a memória agora: {exc}", "yellow")


async def remember_conversation(
    agent: Agent, memory: LongTermMemory | None, printer: Printer
) -> None:
    """At the end of the chat, keep what is worth remembering. Never breaks the exit."""
    turns = agent.drain_turns()
    if memory is None or not turns:
        return
    try:
        result = await memory.extract(turns, agent.session.id)
    except Exception as exc:  # the chat is over: a memory problem must not turn into a crash
        printer.say(f"Não consegui guardar memórias desta conversa: {exc}", "yellow")
        return
    if result.error:
        printer.say(f"Não guardei memórias desta conversa: {result.error}", "yellow")
        return
    if result.added or result.superseded:
        n = len(result.added)
        printer.say(f"Guardei {n} {'coisa' if n == 1 else 'coisas'} sobre você para a próxima.")


async def _chat(message: str | None, provider: str | None, resume: bool = False) -> int:
    registry.discover()
    router = build_router(provider)
    printer = Printer(console)
    router.on_failover = printer.notify_failover
    reader = LineReader()
    try:
        store = open_session_store()
    except MemoryUnavailableError as exc:
        await router.aclose()
        printer.say(str(exc), "red")
        return 1
    session: Session | None = None
    note: str | None = None
    if resume:
        session, note = previous_session(store)
    try:
        memory = open_long_term_memory(router)
    except MemoryUnavailableError as exc:
        close_session_store(store)
        await router.aclose()
        printer.say(str(exc), "red")
        return 1
    agent = Agent(
        router, Dispatcher(conversational=True), session=session, store=store, memory=memory
    )
    if note is not None:
        printer.say(note)
    await prepare_memory(memory, printer)
    memory_tools.bind(memory)
    if message is None:
        printer.say("Oi! Pode falar, tô te ouvindo. (/sair para encerrar)")
        console.print()
    try:
        code = await converse(agent, printer, reader, message)
        await remember_conversation(agent, memory, printer)
        return code
    finally:
        memory_tools.bind(None)
        if memory is not None:
            memory.close()
        await router.aclose()
        close_session_store(store)


@app.callback()
def main() -> None:
    """Sem comando, abre o chat; `juniper <mensagem>` manda uma mensagem isolada."""


@app.command()
def version() -> None:
    """Exibe a versão da Juniper."""
    voice.say(
        console, f"Estou na versão {__version__} (arquitetura v4: brain/core + brain/clients)."
    )


@app.command(CHAT_COMMAND, hidden=True)
def chat(
    words: Annotated[
        Optional[list[str]],  # noqa: UP045  (typer needs a runtime-evaluable annotation)
        typer.Argument(help="Mensagem única. Sem ela, abre o chat interativo."),
    ] = None,
    provider: Annotated[
        Optional[str],  # noqa: UP045
        typer.Option(
            "--provider", "-p", help="Força um provider (deepinfra, groq ou ollama), sem failover."
        ),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option("--resume", "-r", help="Retoma a conversa mais recente."),
    ] = False,
) -> None:
    """Conversa com a Juniper: `juniper oi` (uma mensagem) ou `juniper` (chat interativo)."""
    message = " ".join(words).strip() if words else None
    try:
        code = asyncio.run(_chat(message or None, provider, resume))
    except ValueError as exc:  # unknown --provider
        voice.say(console, f"{exc}", "red")
        raise typer.Exit(2) from None
    except KeyboardInterrupt:
        console.print()
        voice.say(console, "Até logo!")
        raise typer.Exit(130) from None
    if code:
        raise typer.Exit(code)


def run_legacy_command(args: list[str]) -> int:
    """Run a legacy shell command attached to the terminal (its prompts work as in v2)."""
    try:
        return subprocess.run(legacy_argv(args), check=False).returncode
    except FileNotFoundError:
        voice.say(console, "Não achei o zsh, e esses comandos dependem dele.", "red")
        return 127


_PASSTHROUGH: dict[str, Any] = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
    "help_option_names": [],
}


@app.command("search", context_settings=_PASSTHROUGH)
@app.command("grep", hidden=True, context_settings=_PASSTHROUGH)
@app.command("gitgrep", hidden=True, context_settings=_PASSTHROUGH)
def search(ctx: typer.Context) -> None:
    """Busca commits por termo(s): `juniper search 4911 6531`."""
    raise typer.Exit(run_legacy_command(["search", *ctx.args]))


@app.command("deploy", context_settings=_PASSTHROUGH)
@app.command("deployfeature", hidden=True, context_settings=_PASSTHROUGH)
def deploy(ctx: typer.Context) -> None:
    """Deploy de feature por cherry-pick (develop/stage): `juniper deploy 4911`."""
    raise typer.Exit(run_legacy_command(["deploy", *ctx.args]))


INSTALL_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install.sh"


@app.command("install", context_settings=_PASSTHROUGH)
def install(ctx: typer.Context) -> None:
    """Instala/repara a Juniper (venv + dependências). Opções: --dev, --clean, --check, --yes."""
    if not INSTALL_SCRIPT.exists():
        voice.say(console, f"Não encontrei o instalador ({INSTALL_SCRIPT}).", "red")
        raise typer.Exit(1)
    # exec: this process lives inside the venv the installer may rebuild, so it must not stay alive.
    os.execvp("bash", ["bash", str(INSTALL_SCRIPT), *ctx.args])


DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "html"
DOCS_BASE_PORT = 47831  # incomum de propósito; em conflito, tenta a próxima
DOCS_PORT_ATTEMPTS = 50


class _DocsHandler(SimpleHTTPRequestHandler):
    """Serve apenas a pasta docs/, sem poluir o terminal com logs de acesso."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def _bind_docs_server(base_port: int = DOCS_BASE_PORT) -> ThreadingHTTPServer:
    """Sobe o servidor na primeira porta livre a partir de `base_port` (só em 127.0.0.1)."""
    for port in range(base_port, base_port + DOCS_PORT_ATTEMPTS):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), _DocsHandler)
        except OSError:
            continue
    raise OSError(f"nenhuma porta livre entre {base_port} e {base_port + DOCS_PORT_ATTEMPTS - 1}")


@app.command()
def docs() -> None:
    """Serve a documentação (livro de bolso) em http://localhost:<porta>. Ctrl+C encerra."""
    if not (DOCS_DIR / "index.html").is_file():
        voice.say(console, f"Não encontrei a documentação ({DOCS_DIR / 'index.html'}).", "red")
        raise typer.Exit(1)
    try:
        server = _bind_docs_server()
    except OSError as exc:
        voice.say(console, f"Não consegui subir o servidor de docs: {exc}", "red")
        raise typer.Exit(1) from None

    url = f"http://localhost:{server.server_address[1]}/"
    voice.say(console, f"Livro de bolso no ar: {url}  (abra no navegador; Ctrl+C para encerrar)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        voice.say(console, "Docs encerradas.")


memory_app = typer.Typer(help="Memória de longo prazo.", no_args_is_help=True)
app.add_typer(memory_app, name="memory")


@memory_app.command("reindex")
def memory_reindex(
    force: Annotated[
        bool,
        typer.Option("--force", help="Refaz todos os vetores, mesmo os do modelo atual."),
    ] = False,
) -> None:
    """Refaz os vetores das memórias que não foram gerados pelo modelo de embeddings atual."""
    from brain.memory.embeddings import open_embedding_provider
    from brain.memory.mongo import MongoConnection
    from brain.memory.reindex import MEMORIES_COLLECTION, reindex_memories

    connection = MongoConnection()
    try:
        connection.check()
        provider = open_embedding_provider()
        voice.say(console, f"Reindexando com {provider.model_id} (o 1º uso baixa o modelo)...")
        with connection.guard():
            result = reindex_memories(
                connection.collection(MEMORIES_COLLECTION), provider, force=force
            )
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        connection.close()
    voice.say(
        console,
        f"Pronto: {result.reindexed} memória(s) reindexada(s), {result.skipped} já estavam "
        f"em dia (de {result.total}).",
    )


def _command_memory() -> LongTermMemory:
    """Memory for a ``juniper memory`` command, or exit 1 saying why it cannot be used."""
    try:
        return open_memory_for_commands()
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None


def _local(moment: datetime) -> str:
    return f"{moment.astimezone(ZoneInfo(settings.agent.timezone)):%d/%m/%Y %H:%M}"


@memory_app.command("list")
def memory_list(
    memory_type: Annotated[
        Optional[str],  # noqa: UP045
        typer.Option("--type", "-t", help="Só deste tipo: preference, project ou fact."),
    ] = None,
    include_all: Annotated[
        bool,
        typer.Option("--all", "-a", help="Inclui as memórias substituídas (só para auditoria)."),
    ] = False,
) -> None:
    """Lista as memórias guardadas, das mais novas para as mais antigas."""
    memory = _command_memory()
    try:
        memories = memory.store.list_memories(memory_type, include_all)
    except (MemoryUnavailableError, ValueError) as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    if not memories:
        voice.say(console, "Ainda não guardei nenhuma memória.")
        return
    table = Table(header_style="bold")
    for column in ("Id", "Tipo", "Status", "Criada em", "Texto"):
        table.add_column(column, overflow="fold")
    for m in memories:
        table.add_row(m.id, m.type, m.status, _local(m.created_at), m.text)
    console.print(table)
    voice.say(console, f"{len(memories)} memória(s). Para apagar uma: juniper memory forget <id>")


@memory_app.command("search")
def memory_search(
    words: Annotated[list[str], typer.Argument(help="O que procurar, por significado.")],
    limit: Annotated[
        Optional[int],  # noqa: UP045
        typer.Option("--limit", "-n", min=1, help="Quantos resultados (padrão: retrieval.top_k)."),
    ] = None,
    threshold: Annotated[
        Optional[float],  # noqa: UP045
        typer.Option(
            "--threshold", min=-1.0, max=1.0, help="Nota mínima (padrão: retrieval.threshold)."
        ),
    ] = None,
) -> None:
    """Busca por significado nas memórias e mostra a nota de cada resultado."""
    query = " ".join(words).strip()
    memory = _command_memory()
    try:
        hits = asyncio.run(memory.recall(query, limit, threshold))
    except (MemoryUnavailableError, ValueError) as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    if not hits:
        cut = settings.memory.retrieval.threshold if threshold is None else threshold
        voice.say(
            console,
            f"Nada passou da nota mínima ({cut:.2f}). Tente `--threshold 0` para ver as notas "
            "de tudo e ajustar o retrieval.threshold no config.yaml.",
        )
        return
    table = Table(header_style="bold")
    for column in ("Nota", "Id", "Tipo", "Texto"):
        table.add_column(column, overflow="fold")
    for h in hits:
        table.add_row(f"{h.score:.3f}", h.memory.id, h.memory.type, h.memory.text)
    console.print(table)


@memory_app.command("forget")
def memory_forget(
    memory_id: Annotated[str, typer.Argument(help="Id da memória (veja `juniper memory list`).")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Não pergunta antes de apagar.")] = False,
) -> None:
    """Apaga uma memória para sempre, junto com as versões antigas que ela substituiu."""
    memory = _command_memory()
    try:
        found = memory.store.get(memory_id.strip())
        if found is None:
            voice.say(console, f"Não achei a memória {memory_id}.", "red")
            raise typer.Exit(1)
        voice.say(console, f"Vou apagar para sempre: «{found.text}» ({found.type})")
        if not yes and not typer.confirm("Pode apagar?", default=False):
            voice.say(console, "Ok, não apaguei nada.")
            return
        removed = asyncio.run(memory.forget([found]))
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    extra = f" (e {len(removed) - 1} versão(ões) antiga(s))" if len(removed) > 1 else ""
    voice.say(console, f"Esqueci.{extra}")


@memory_app.command("export")
def memory_export(
    vectors: Annotated[
        bool, typer.Option("--vectors", help="Inclui os vetores de embedding (ficam enormes).")
    ] = False,
    output: Annotated[
        Optional[Path],  # noqa: UP045
        typer.Option("--output", "-o", help="Grava neste arquivo em vez de imprimir."),
    ] = None,
) -> None:
    """Exporta todas as memórias em JSON (sem os vetores, a menos que você peça)."""
    memory = _command_memory()
    try:
        rows = memory.export(vectors)
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    text = json.dumps(rows, ensure_ascii=False, indent=2)
    if output is None:
        typer.echo(text)
        return
    output.write_text(text + "\n", encoding="utf-8")
    voice.say(console, f"{len(rows)} memória(s) exportadas para {output}.")


@app.command()
def tools() -> None:
    """Lista as tools registradas: nome, tier, locality e descrição."""
    registry.discover()
    table = Table(header_style="bold")
    for column in ("Nome", "Tier", "Locality", "Descrição"):
        table.add_column(column, overflow="fold")
    colors = {
        Tier.READ: "green",
        Tier.WRITE: "yellow",
        Tier.DESTRUCTIVE: "red",
        Tier.FORBIDDEN: "bold red",
    }
    for tool in sorted(registry.all(), key=lambda t: t.name):
        color = colors[tool.tier]
        table.add_row(
            tool.name, f"[{color}]{tool.tier}[/{color}]", str(tool.locality), tool.description
        )
    voice.say(console, "Isto é o que sei fazer hoje:")
    console.print(table)


if __name__ == "__main__":
    app()
