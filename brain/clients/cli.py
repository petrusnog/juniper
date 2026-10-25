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
from brain.core.resume import previous_session
from brain.core.session import Session
from brain.eventlog import event_log
from brain.llm.errors import LLMAuthError, LLMError, LLMRateLimitError
from brain.llm.friendly import PROVIDER_PT, friendly_error
from brain.llm.router import PROVIDERS, LLMRouter
from brain.memory.background import BackgroundExtractor
from brain.memory.errors import MemoryUnavailableError
from brain.memory.extraction import ExtractionResult
from brain.memory.longterm import (
    LongTermMemory,
    long_term_enabled,
    open_long_term_memory,
    open_memory_for_commands,
)
from brain.memory.profile import ProfileError, render
from brain.memory.stores import close_session_store, open_session_store
from brain.memory.v2 import open_v2_runtime, v2_enabled
from brain.timing import EXTRACTION_LABELS, summarize, summarize_turn
from brain.tools import memory as memory_tools
from brain.tools.base import Tier
from brain.tools.juniper_cmds import legacy_argv
from brain.tools.registry import registry
from brain.trace import message_trace, quote

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
    pretty_exceptions_show_locals=False,  # um traceback nunca deve imprimir variáveis (tokens)
)

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
        art, name = PROVIDER_PT.get(failed, ("o", failed))
        if isinstance(exc, LLMAuthError):
            why = f"{art} {name} recusou as credenciais"
        elif isinstance(exc, LLMRateLimitError):
            why = f"o limite d{art} {name} acabou"
        else:
            why = f"{art} {name} não respondeu"
        if following is None:
            self.say(f"Falhou: {why}.", "yellow")
            return
        next_art, next_name = PROVIDER_PT.get(following, ("o", following))
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

    def busy(self, text: str) -> None:
        """Show a spinner while something runs without output (only on a real terminal)."""
        self._spin(text)

    def idle(self) -> None:
        self._unspin()

    def timing(self, text: str) -> None:
        """A dim line saying where the time went (``agent.show_timing``)."""
        self.end_line()
        self.out.print(f"[dim]⏱ {text}[/dim]", highlight=False, soft_wrap=True)

    def end_line(self) -> None:
        self._unspin()
        if self._head:
            self._flush_head()
        self._head = None
        if self._mid_line:
            self.out.print()
            self._mid_line = False


def build_router(provider: str | None) -> LLMRouter:
    """Router over ``llm.priority``, or over a single provider when ``--provider`` is given."""
    if provider is None:
        return LLMRouter()
    if provider not in PROVIDERS:
        raise ValueError(
            f"provider desconhecido: {provider!r} (use {' ou '.join(sorted(PROVIDERS))})"
        )
    return LLMRouter([PROVIDERS[provider]()])


async def converse(
    agent: Agent,
    printer: Printer,
    reader: LineReader,
    message: str | None,
    background: BackgroundExtractor | None = None,
) -> int:
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
        message_trace.begin(f"conversa {agent.session.id} · modo local")
        message_trace.step(
            "Terminal (CLI)",
            f"Você digitou {quote(text)}. No modo local não há servidor nem rede: o agente roda "
            "neste mesmo processo e a mensagem vai direto do terminal ao Agent.",
        )
        printer.begin_reply()
        code = 0
        try:
            reply = await agent.run(text, on_token=printer.token)
            message_trace.step(
                "Terminal (CLI)",
                f"Mostrei a resposta no terminal, pedaço a pedaço: {quote(reply, 1500)}",
            )
        except LLMError as exc:
            printer.say(friendly_error(exc), "red")
            code = 1
        printer.end_line()
        if background is not None:  # hand the finished turn over; this returns at once
            for turn in agent.drain_turns():
                background.submit(turn)
        if settings.agent.show_timing and agent.last_timing is not None:
            total, stages = agent.last_timing
            printer.timing(summarize_turn(total, stages, agent._recall_timings))
        if message is not None:
            return code
        printer.out.print()
        pending = None


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


def _report(printer: Printer, added: int, superseded: int, profile: bool) -> None:
    if added or superseded:
        printer.say(
            f"Guardei {added} {'coisa' if added == 1 else 'coisas'} sobre você para a próxima."
        )
    if profile:
        printer.say("Atualizei o seu perfil.")


def _timing_line(result: ExtractionResult) -> str:
    return (
        "memória (extração) "
        + summarize(result.total_ms, result.timings, EXTRACTION_LABELS)
        + f" · {result.llm_calls} chamada(s) ao LLM"
        + f" · {result.candidates} candidata(s), {len(result.rejected)} recusada(s)"
    )


async def remember_conversation(
    agent: Agent,
    memory: LongTermMemory | None,
    printer: Printer,
    background: BackgroundExtractor | None = None,
) -> None:
    """At the end of the chat, keep what is worth remembering. Never breaks the exit.

    With a ``background`` extractor (``memory.extraction.mode: turn``) the memories were saved
    along the way; this only waits for the last one and says what was kept.
    """
    if background is not None:
        if background.busy:
            printer.busy("guardando o que aprendi...")
        try:
            summary = await background.finish()
        except Exception as exc:
            printer.say(f"Não consegui guardar memórias desta conversa: {exc}", "yellow")
            return
        finally:
            printer.idle()
        if settings.agent.show_timing:
            for result in summary.results:
                if result.timings:
                    printer.timing(_timing_line(result))
        if summary.error and not summary.saved_something:
            printer.say(f"Não guardei memórias desta conversa: {summary.error}", "yellow")
            return
        _report(printer, summary.added, summary.superseded, summary.profile_updated)
        return
    turns = agent.drain_turns()
    if memory is None or not turns:
        return
    printer.busy("guardando o que aprendi...")  # a visible wait, not a silent hang
    try:
        result = await memory.extract(turns, agent.session.id)
    except Exception as exc:  # the chat is over: a memory problem must not turn into a crash
        printer.say(f"Não consegui guardar memórias desta conversa: {exc}", "yellow")
        return
    finally:
        printer.idle()
    if settings.agent.show_timing and result.timings:
        printer.timing(_timing_line(result))
    if result.error:
        printer.say(f"Não guardei memórias desta conversa: {result.error}", "yellow")
        return
    _report(printer, len(result.added), len(result.superseded), result.profile is not None)


async def _chat(message: str | None, provider: str | None, resume: bool = False) -> int:
    if settings.deployment.mode == "server":  # o brain é o do servidor (T-086)
        from brain.clients import remote_cli

        return await remote_cli.chat(message, resume, Printer(console), LineReader())
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
    # Background extraction gets its own router: it must not print failover notices into the
    # middle of what the user is typing.
    background_mode = long_term_enabled() and settings.memory.extraction.mode == "turn"
    memory_llm = build_router(provider) if background_mode else router
    try:
        memory = open_long_term_memory(memory_llm)
    except MemoryUnavailableError as exc:
        close_session_store(store)
        await router.aclose()
        if memory_llm is not router:
            await memory_llm.aclose()
        printer.say(str(exc), "red")
        return 1
    background: BackgroundExtractor | None = None
    if background_mode and memory is not None:
        background = BackgroundExtractor(memory, lambda: agent.session.id)
    v2 = None
    try:
        v2 = open_v2_runtime(memory.embedder if memory is not None else None, memory_llm)
    except MemoryUnavailableError as exc:  # the chat still works; only the V2 is lost
        printer.say(str(exc), "yellow")
    if v2 is not None:
        v2.context.inner = memory
    agent = Agent(
        router,
        Dispatcher(conversational=True),
        session=session,
        store=store,
        memory=v2.context if v2 else memory,
        turn_sink=v2.sink if v2 else None,
    )
    if note is not None:
        printer.say(note)
    await prepare_memory(memory, printer)
    memory_tools.bind(memory)
    if message is None:
        printer.say("Oi! Pode falar, tô te ouvindo. (/sair para encerrar)")
        console.print()
    try:
        code = await converse(agent, printer, reader, message, background)
        await remember_conversation(agent, memory, printer, background)
        return code
    finally:
        memory_tools.bind(None)
        if v2 is not None:
            v2.close()
        if memory is not None:
            memory.close()
        if memory_llm is not router:
            await memory_llm.aclose()
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


@app.command()
def serve() -> None:
    """Sobe o brain como servidor WebSocket para os clientes remotos (deployment.mode: server)."""
    from brain.clients.chat import open_chat_handler
    from brain.clients.server import BrainServer, ensure_token, run_server

    if settings.deployment.mode != "server":
        voice.say(
            console,
            "O servidor só sobe com `deployment.mode: server` no config.yaml "
            "(ou JUNIPER_DEPLOYMENT__MODE=server).",
            "red",
        )
        raise typer.Exit(1)
    token_file = settings.deployment.auth.token_file
    token = ensure_token(token_file)
    host, port = settings.deployment.server.host, settings.deployment.server.port

    async def run() -> None:
        async with open_chat_handler() as handler:
            await run_server(BrainServer(host, port, token, handler, health=handler.health))

    voice.say(console, f"Subindo em ws://{host}:{port}/v1/ws (token em {token_file}).")
    try:
        asyncio.run(run())
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    except OSError as exc:  # porta ocupada, endereço inválido...
        voice.say(console, f"Não consegui abrir {host}:{port}: {exc}", "red")
        raise typer.Exit(1) from None
    voice.say(console, "Servidor encerrado.")


@app.command()
def telegram(
    health: Annotated[
        bool,
        typer.Option("--health", help="Sai com 0 se o bot está vivo (healthcheck do contêiner)."),
    ] = False,
) -> None:
    """Sobe o bot do Telegram, um cliente do brain remoto (ADR-016)."""
    from brain.clients import telegram as bot

    if health:
        raise typer.Exit(0 if bot.is_healthy() else 1)
    token = settings.telegram_bot_token
    if token is None or not token.get_secret_value().strip():
        voice.say(
            console,
            "Falta JUNIPER_TELEGRAM_BOT_TOKEN no .env (o token que o @BotFather deu).",
            "red",
        )
        raise typer.Exit(1)
    allowed = settings.telegram.allowed_user_ids
    if not allowed:
        voice.say(
            console,
            "Ninguém está autorizado (telegram.allowed_user_ids vazio): o bot não responde a "
            "ninguém. Mande uma mensagem a ele e leia o seu ID no log.",
            "yellow",
        )
    if len(allowed) > 1:
        voice.say(
            console,
            "Mais de um ID autorizado: todos compartilham a conversa mais recente do servidor "
            "(ele é de um usuário só) e leem o histórico uns dos outros. Use um único ID.",
            "yellow",
        )
    voice.say(console, f"Bot do Telegram no ar ({len(allowed)} usuário(s) autorizado(s)).")
    try:
        _run_bot(bot, token.get_secret_value(), allowed)
    except bot.BotStartError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    voice.say(console, "Bot encerrado.")


def _run_bot(bot: Any, token: str, allowed: list[int]) -> None:
    bot.run_bot(
        token,
        allowed,
        rate_limit_per_minute=settings.telegram.rate_limit_per_minute,
        delete_after_hours=settings.telegram.delete_after_hours,
    )


logs_app = typer.Typer(
    help="O log técnico: tudo que a Juniper fez por baixo dos panos.", invoke_without_command=True
)
app.add_typer(logs_app, name="logs")


@logs_app.callback()
def logs_show(
    ctx: typer.Context,
    lines: Annotated[
        int, typer.Option("--lines", "-n", help="Quantos eventos mostrar (0 = todos).")
    ] = 100,
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Acompanha ao vivo, como `tail -f`.")
    ] = False,
    day: Annotated[
        Optional[str], typer.Option("--date", help="Um dia específico (AAAA-MM-DD).")  # noqa: UP045
    ] = None,
    days: Annotated[
        int, typer.Option("--days", help="Quantos dias para trás (contando hoje).")
    ] = 1,
    session: Annotated[
        Optional[str], typer.Option("--session", "-s", help="Só uma conversa (um pedaço do id).")  # noqa: UP045
    ] = None,
    run: Annotated[
        Optional[str], typer.Option("--run", help="Só uma execução do processo.")  # noqa: UP045
    ] = None,
    event: Annotated[
        Optional[list[str]],  # noqa: UP045
        typer.Option("--event", "-e", help="Só estes eventos (nome ou prefixo, ex.: http)."),
    ] = None,
    errors: Annotated[
        bool,
        typer.Option("--errors", help="Só falhas: erros, failover, HTTP 4xx/5xx, tool com erro."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Mostra tudo de cada evento (cabeçalhos e corpos)."),
    ] = False,
    raw: Annotated[bool, typer.Option("--json", help="Imprime as linhas JSON cruas.")] = False,
) -> None:
    """Mostra o log técnico de hoje (requisições aos provedores, agenda, tools, erros).

    Cada sessão do dia está no mesmo arquivo; `--session` isola uma conversa. O log vive onde o
    cérebro roda: na VPS use `docker compose exec brain python -m brain logs`.
    """
    if ctx.invoked_subcommand is not None:
        return
    from datetime import date as date_type

    from rich.markup import escape

    from brain import logview
    from brain.logfiles import today_local

    directory = event_log.directory
    try:
        chosen = (
            {date_type.fromisoformat(day)}
            if day
            else logview.days_back(today_local(), max(1, days))
        )
    except ValueError:
        voice.say(console, "Data inválida: use AAAA-MM-DD.", "red")
        raise typer.Exit(2) from None
    predicate = lambda r: logview.matches(  # noqa: E731
        r, session=session, run=run, events=event or (), errors_only=errors
    )

    def show(record: dict[str, Any]) -> None:
        if raw:
            print(json.dumps(record, ensure_ascii=False))
            return
        sess = str(record.get("session", ""))[-6:] or "------"
        line = f"{logview.clock(record)}  {sess}  {record.get('event', '?'):<14} "
        text = escape(line + logview.summary(record))
        console.print(f"[{logview.style(record)}]{text}[/]" if logview.style(record) else text)
        if verbose:
            console.print(escape(logview.details(record)), style="dim")

    for record in logview.select(directory, chosen, predicate, lines):
        show(record)
    if follow:
        console.print("[dim]Acompanhando... (Ctrl+C para sair)[/]")
        try:
            for record in logview.follow(directory, predicate, today=today_local):
                show(record)
        except KeyboardInterrupt:
            pass


@logs_app.command("ls")
def logs_list() -> None:
    """Lista os arquivos de log e o tamanho de cada um."""
    from brain.logfiles import daily_files

    files = daily_files(event_log.directory)
    if not files:
        voice.say(console, f"Nenhum log em {event_log.directory}.")
        return
    for _, path in files:
        console.print(f"{path.name:<24} {path.stat().st_size / 1024:>10.1f} KB")


@logs_app.command("rotate")
def logs_rotate(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Só mostra o que seria feito.")
    ] = False,
) -> None:
    """Compacta os logs de dias anteriores e apaga os mais velhos que `logging.retention_days`.

    O servidor já faz isso sozinho todo dia (`logging.rotate_at`); use este comando no modo local
    ou num cron do sistema (`10 0 * * * juniper logs rotate`). É seguro rodar mais de uma vez.
    """
    from brain.logfiles import rotate, run_rotation, today_local

    cfg = settings.logging
    if dry_run:
        report = rotate(event_log.directory, today_local(), cfg.retention_days, dry_run=True)
    else:
        report = run_rotation()
    verb = "Compactaria" if dry_run else "Compactei"
    voice.say(console, f"{verb}: {', '.join(report.compressed) or 'nada'}.")
    voice.say(
        console, f"{'Apagaria' if dry_run else 'Apaguei'}: {', '.join(report.deleted) or 'nada'}."
    )
    for problem in report.errors:
        voice.say(console, f"Falhou: {problem}", "red")
    if report.errors:
        raise typer.Exit(1)


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
        v2_result = None
        if settings.memory.v2.enabled:  # the V2 layers carry embeddings too (ADR-017)
            from brain.memory.reindex import reindex_v2
            from brain.memory.v2.store import MongoDocStore

            v2_result = reindex_v2(MongoDocStore(connection), provider, force=force)
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
    if v2_result is not None:
        voice.say(
            console,
            f"Memória V2: {v2_result.reindexed} item(ns) reindexado(s) (crenças e interesses), "
            f"{v2_result.skipped} já estavam em dia (de {v2_result.total}).",
        )


@memory_app.command("dream")
def memory_dream(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Roda o sonho sem gravar nada e mostra o que gravaria."),
    ] = False,
) -> None:
    """Roda o algoritmo de sonho agora (o "inconsciente" da Memória V2, ADR-017).

    Lê as conversas com novidade, reflete e atualiza as camadas. O servidor já a roda sozinho
    todo dia em `memory.v2.night.at`; use este comando para testar ou para recuperar uma noite.
    É administrativo: acessa o banco do processo onde roda (na VPS, pelo atalho do contêiner).
    """
    from brain.memory.v2.service import run_night

    try:
        report = asyncio.run(run_night(dry_run=dry_run))
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    voice.say(console, report.text())


def _command_memory() -> LongTermMemory:
    """Memory for a ``juniper memory`` command, or exit 1 saying why it cannot be used.

    In ``server`` mode it is the server's memory, reached over the protocol (T-086).
    """
    try:
        if settings.deployment.mode == "server":
            from brain.clients.remote_cli import RemoteMemory

            return RemoteMemory()  # type: ignore[return-value]  # same interface as LongTermMemory
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
    reflexao: Annotated[
        Optional[str],  # noqa: UP045
        typer.Option(
            "--reflexao",
            help="Só as brutas ou só as refinadas pelo sonho: bruta ou refinada.",
        ),
    ] = None,
) -> None:
    """Lista as memórias guardadas, das mais novas para as mais antigas."""
    if reflexao is not None and reflexao not in ("bruta", "refinada"):
        voice.say(console, "--reflexao aceita: bruta ou refinada.", "red")
        raise typer.Exit(1)
    memory = _command_memory()
    try:
        memories = memory.store.list_memories(memory_type, include_all)
    except (MemoryUnavailableError, ValueError) as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    if reflexao is not None:
        memories = [m for m in memories if m.status_reflexao == reflexao]
    if not memories:
        voice.say(console, "Ainda não guardei nenhuma memória.")
        return
    table = Table(header_style="bold")
    for column in ("Id", "Tipo", "Status", "Reflexão", "Criada em", "Texto"):
        table.add_column(column, overflow="fold")
    for m in memories:
        table.add_row(m.id, m.type, m.status, m.status_reflexao, _local(m.created_at), m.text)
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
        profile_export = memory.export_profile()
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    payload: dict[str, Any] = {"profile": profile_export, "memories": rows}
    if v2_enabled():  # the layers above the facts (ADR-017): all of them, vectors only on request
        from brain.memory.v2 import open_v2_data
        from brain.memory.v2.export import export_v2

        try:
            data = open_v2_data()
            try:
                payload["v2"] = export_v2(data, vectors)
            finally:
                data.close()
        except MemoryUnavailableError as exc:
            voice.say(console, str(exc), "red")
            raise typer.Exit(1) from None
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        typer.echo(text)
        return
    output.write_text(text + "\n", encoding="utf-8")
    voice.say(console, f"Perfil e {len(rows)} memória(s) exportados para {output}.")


from brain.clients import memory_v2_cli  # noqa: E402

memory_v2_cli.install(app, memory_app, console)

profile_app = typer.Typer(
    help="Seu perfil: o parágrafo de identidade que a Juniper lê em toda mensagem.",
    invoke_without_command=True,
)
memory_app.add_typer(profile_app, name="profile")


@profile_app.callback()
def memory_profile(ctx: typer.Context) -> None:
    """Mostra o perfil (nome, nascimento, nacionalidade). Subcomandos: set, forget."""
    if ctx.invoked_subcommand is not None:
        return
    memory = _command_memory()
    try:
        profile = asyncio.run(memory.get_profile())
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    if profile is None:
        voice.say(
            console,
            "Ainda não tenho o seu perfil. Conte seu nome numa conversa, ou use "
            '`juniper memory profile set "O usuário se chama ..."`.',
        )
        return
    voice.say(console, f"Como eu leio o seu perfil hoje: {render(profile)}")
    console.print(f"[dim]Guardado como:[/dim] {profile.text}")
    for key, value in profile.fields.items():
        console.print(f"[dim]{key}:[/dim] {value}")
    console.print(f"[dim]Atualizado em {_local(profile.updated_at)}[/dim]")


@profile_app.command("set")
def memory_profile_set(
    text: Annotated[
        str, typer.Argument(help="O parágrafo completo. Use {idade} onde iria a idade.")
    ],
    name: Annotated[Optional[str], typer.Option("--name", help="Nome.")] = None,  # noqa: UP045
    birth_date: Annotated[
        Optional[str],  # noqa: UP045
        typer.Option("--birth-date", help="Data de nascimento, AAAA-MM-DD."),
    ] = None,
    nationality: Annotated[
        Optional[str],  # noqa: UP045
        typer.Option("--nationality", help="Nacionalidade."),
    ] = None,
    gender: Annotated[Optional[str], typer.Option("--gender", help="Gênero.")] = None,  # noqa: UP045
) -> None:
    """Define o parágrafo do perfil (os campos informados se somam aos que já existem)."""
    fields = {
        k: v
        for k, v in (
            ("name", name),
            ("birth_date", birth_date),
            ("nationality", nationality),
            ("gender", gender),
        )
        if v is not None
    }
    memory = _command_memory()
    try:
        profile = asyncio.run(memory.set_profile(text, fields))
    except (MemoryUnavailableError, ProfileError) as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    voice.say(console, f"Perfil guardado: {render(profile)}")


@profile_app.command("forget")
def memory_profile_forget(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Não pergunta antes de apagar.")] = False,
) -> None:
    """Apaga o perfil para sempre (as memórias gerais não são tocadas)."""
    if not yes and not typer.confirm("Apagar o seu perfil para sempre?", default=False):
        voice.say(console, "Ok, não apaguei nada.")
        return
    memory = _command_memory()
    try:
        removed = asyncio.run(memory.forget_profile())
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    finally:
        memory.close()
    voice.say(console, "Esqueci o seu perfil." if removed else "Você não tinha perfil guardado.")


def _server_tools() -> list[tuple[str, Tier, str, str]]:
    """The tools the server's Juniper has right now, or exit 1 saying why not."""
    from brain.clients import remote_cli
    from brain.clients.remote import RemoteError

    try:
        listed = asyncio.run(remote_cli.list_tools())
    except RemoteError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    return [(t.name, Tier(t.tier), t.locality, t.description) for t in listed]


@app.command()
def tools() -> None:
    """Lista as tools registradas: nome, tier, locality e descrição."""
    rows: list[tuple[str, Tier, str, str]]
    if settings.deployment.mode == "server":
        rows = _server_tools()
    else:
        registry.discover()
        rows = [
            (t.name, t.tier, str(t.locality), t.description)
            for t in sorted(registry.all(), key=lambda t: t.name)
        ]
    table = Table(header_style="bold")
    for column in ("Nome", "Tier", "Locality", "Descrição"):
        table.add_column(column, overflow="fold")
    colors = {
        Tier.READ: "green",
        Tier.WRITE: "yellow",
        Tier.DESTRUCTIVE: "red",
        Tier.FORBIDDEN: "bold red",
    }
    for name, tier, locality, description in rows:
        color = colors[tier]
        table.add_row(name, f"[{color}]{tier}[/{color}]", locality, description)
    voice.say(console, "Isto é o que sei fazer hoje:")
    console.print(table)


if __name__ == "__main__":
    app()
