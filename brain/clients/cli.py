"""Cliente CLI da Juniper.

Entry point oficial (`juniper = brain.clients.cli:app` no pyproject). Cliente fino: captura
o texto e mostra os tokens em streaming. Confirmações acontecem na própria conversa (sem `[y/N]`).
Toda a lógica vive em `brain.core`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import readline  # noqa: F401  (enables UTF-8-aware line editing in input())
import subprocess
import threading
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.status import Status
from rich.table import Table
from rich.text import Text
from typer.core import TyperGroup

from brain import __version__
from brain.clients import voice
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.llm.errors import LLMAuthError, LLMError, LLMRateLimitError
from brain.llm.router import PROVIDERS, LLMRouter
from brain.tools.base import Tier
from brain.tools.juniper_cmds import legacy_argv
from brain.tools.registry import registry

console = Console()

# The terminal speaks only through voice.py; library warnings (e.g. the router's failover
# warning) go to the structured event log instead of leaking raw into the conversation.
logging.getLogger("brain").addHandler(logging.NullHandler())


class JuniperGroup(TyperGroup):
    """Command group that reports an unknown command in Juniper's voice.

    Typed loosely on purpose: typer >= 0.2x vendors click, older versions depend on it,
    so ``click`` cannot be imported by name here.
    """

    def resolve_command(self, ctx: Any, args: list[str]) -> Any:
        try:
            return super().resolve_command(ctx, args)
        except Exception as exc:
            if type(exc).__name__ != "UsageError" or not args:
                raise
            voice.say(
                console,
                f"Não conheço o comando '{args[0]}'. Rode `juniper` para ver o que sei fazer.",
                "red",
            )
            ctx.exit(2)


app = typer.Typer(
    name="juniper",
    help="Juniper — agente autônomo de automação e conversação.",
    cls=JuniperGroup,
)

# Gendered article + display name, so the messages read right ("a Groq", "o Ollama").
_PROVIDER_PT = {"deepinfra": ("a", "DeepInfra"), "groq": ("a", "Groq"), "ollama": ("o", "Ollama")}
_EXIT = {"/sair", "/exit"}


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


async def _chat(message: str | None, provider: str | None) -> int:
    registry.discover()
    router = build_router(provider)
    printer = Printer(console)
    router.on_failover = printer.notify_failover
    reader = LineReader()
    agent = Agent(router, Dispatcher(conversational=True))
    if message is None:
        printer.say("Oi! Pode falar, tô te ouvindo. (/sair para encerrar)")
        console.print()
    try:
        return await converse(agent, printer, reader, message)
    finally:
        await router.aclose()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Sem comando, a Juniper se apresenta e mostra o que sabe fazer."""
    if ctx.invoked_subcommand is None:
        voice.say(console, "Olá, sou Juniper, sua assistente de automações favorita!")
        voice.say(console, "Use `juniper chat` para conversar. O resto está logo abaixo:")
        console.print()
        console.print(ctx.get_help())
        raise typer.Exit(0)


@app.command()
def version() -> None:
    """Exibe a versão da Juniper."""
    voice.say(
        console, f"Estou na versão {__version__} (arquitetura v4: brain/core + brain/clients)."
    )


@app.command()
def chat(
    message: Annotated[
        Optional[str],  # noqa: UP045  (typer needs a runtime-evaluable annotation)
        typer.Argument(help="Mensagem única. Sem ela, abre o chat interativo."),
    ] = None,
    provider: Annotated[
        Optional[str],  # noqa: UP045
        typer.Option(
            "--provider", "-p", help="Força um provider (deepinfra, groq ou ollama), sem failover."
        ),
    ] = None,
) -> None:
    """Conversa com a Juniper: `juniper chat "oi"` ou `juniper chat` (interativo)."""
    try:
        code = asyncio.run(_chat(message, provider))
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
