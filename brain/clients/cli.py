"""Cliente CLI da Juniper.

Entry point oficial (`juniper = brain.clients.cli:app` no pyproject). Cliente fino: captura
o texto, mostra os tokens em streaming e pergunta `[y/N]` quando uma tool pede confirmação.
Toda a lógica vive em `brain.core`.
"""

from __future__ import annotations

import asyncio
import json
import readline  # noqa: F401  (enables UTF-8-aware line editing in input())
import subprocess
import threading
from collections.abc import Callable
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from brain import __version__
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.llm.errors import LLMError
from brain.llm.router import PROVIDERS, LLMRouter
from brain.tools.base import Tier
from brain.tools.juniper_cmds import legacy_argv
from brain.tools.registry import registry

app = typer.Typer(
    name="juniper",
    help="Juniper — agente autônomo de automação e conversação.",
    no_args_is_help=True,
)

console = Console()

_YES = {"y", "yes", "s", "sim"}
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


class Printer:
    """Console output that tracks whether the cursor is mid-line (streaming)."""

    def __init__(self, out: Console) -> None:
        self.out = out
        self._mid_line = False

    def token(self, text: str) -> None:
        self.out.print(text, end="", markup=False, highlight=False, soft_wrap=True)
        self._mid_line = not text.endswith("\n")

    def start_reply(self) -> None:
        self.out.print("[bold magenta]juniper>[/bold magenta] ", end="")
        self._mid_line = True

    def end_line(self) -> None:
        if self._mid_line:
            self.out.print()
            self._mid_line = False


def make_confirm(
    printer: Printer, reader: LineReader
) -> Callable[[str, dict[str, Any], Tier], Any]:
    """Build the dispatcher's ``confirm`` callback (the timeout is enforced by the dispatcher)."""

    async def confirm(name: str, args: dict[str, Any], tier: Tier) -> bool:
        printer.end_line()
        if tier is Tier.DESTRUCTIVE:
            printer.out.print(f"[bold red]⚠ {name}[/bold red] [red](IRREVERSÍVEL)[/red]")
        else:
            printer.out.print(
                f"[bold yellow]⚠ {name}[/bold yellow] [yellow](altera coisas)[/yellow]"
            )
        printer.out.print(
            f"  [dim]{escape(json.dumps(args, ensure_ascii=False))}[/dim]",
            markup=True,
            highlight=False,
        )
        try:
            answer = await reader.read("  Confirma? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in _YES

    return confirm


def friendly_error(exc: LLMError) -> str:
    """Translate an LLM failure into an actionable PT-BR message."""
    detail = str(exc)
    hints: list[str] = []
    if "chave de API ausente" in detail or "LLMAuthError" in detail:
        hints.append("Groq sem chave válida: defina JUNIPER_GROQ_API_KEY no arquivo .env.")
    if "LLMRateLimitError" in detail:
        hints.append("Limite da Groq atingido: espere um minuto e tente de novo.")
    if "ollama" in detail and ("LLMUnavailableError" in detail or "LLMTimeoutError" in detail):
        hints.append("Ollama não respondeu: ele está rodando? (`ollama serve`)")
    text = "Nenhum LLM respondeu."
    if hints:
        text += "\n" + "\n".join(f"  · {h}" for h in hints)
    return f"{text}\n[dim]{escape(detail)}[/dim]"


def build_router(provider: str | None) -> LLMRouter:
    """Router over ``llm.priority``, or over a single provider when ``--provider`` is given."""
    if provider is None:
        return LLMRouter()
    if provider not in PROVIDERS:
        raise typer.BadParameter(
            f"provider desconhecido: {provider!r} (use {' ou '.join(sorted(PROVIDERS))})"
        )
    return LLMRouter([PROVIDERS[provider]()])


async def converse(agent: Agent, printer: Printer, reader: LineReader, message: str | None) -> int:
    """One-shot when ``message`` is given, REPL otherwise. Returns the exit code."""
    pending = message
    while True:
        if pending is None:
            try:
                text = (await reader.read("você> ")).strip()
            except EOFError:
                printer.out.print()
                return 0
            if text in _EXIT:
                return 0
            if not text:
                continue
        else:
            text = pending
        printer.start_reply()
        code = 0
        try:
            await agent.run(text, on_token=printer.token)
        except LLMError as exc:
            printer.end_line()
            printer.out.print(f"[red]{friendly_error(exc)}[/red]")
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
    reader = LineReader()
    agent = Agent(router, Dispatcher(make_confirm(printer, reader)))
    if message is None:
        console.print(
            f"[bold green]🌿 Juniper v{__version__}[/bold green] [dim]· /sair para encerrar[/dim]\n"
        )
    try:
        return await converse(agent, printer, reader, message)
    finally:
        await router.aclose()


@app.command()
def version() -> None:
    """Exibe a versão da Juniper."""
    console.print(f"[bold green]🌿 Juniper v{__version__}[/bold green]")
    console.print("[dim]Arquitetura v4 — brain/core + brain/clients[/dim]")


@app.command()
def chat(
    message: Annotated[
        Optional[str],  # noqa: UP045  (typer needs a runtime-evaluable annotation)
        typer.Argument(help="Mensagem única. Sem ela, abre o chat interativo."),
    ] = None,
    provider: Annotated[
        Optional[str],  # noqa: UP045
        typer.Option("--provider", "-p", help="Força um provider (groq ou ollama), sem failover."),
    ] = None,
) -> None:
    """Conversa com a Juniper: `juniper chat "oi"` ou `juniper chat` (interativo)."""
    try:
        code = asyncio.run(_chat(message, provider))
    except KeyboardInterrupt:
        console.print("\n[dim]Até logo.[/dim]")
        raise typer.Exit(130) from None
    if code:
        raise typer.Exit(code)


def run_legacy_command(args: list[str]) -> int:
    """Run a legacy shell command attached to the terminal (its prompts work as in v2)."""
    try:
        return subprocess.run(legacy_argv(args), check=False).returncode
    except FileNotFoundError:
        console.print("[red]zsh não encontrado: os comandos legados precisam dele.[/red]")
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
    console.print(table)


if __name__ == "__main__":
    app()
