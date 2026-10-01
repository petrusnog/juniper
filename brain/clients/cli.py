"""Cliente CLI da Juniper.

Entry point oficial (`juniper = brain.clients.cli:app` no pyproject).
Por enquanto, apenas placeholder até T-040.
"""

import typer
from rich.console import Console

from brain import __version__

app = typer.Typer(
    name="juniper",
    help="Juniper — agente autônomo de automação e conversação.",
    no_args_is_help=True,
)

console = Console()


@app.command()
def version() -> None:
    """Exibe a versão da Juniper."""
    console.print(f"[bold green]🌿 Juniper v{__version__}[/bold green]")
    console.print("[dim]Arquitetura v4 — brain/core + brain/clients[/dim]")


@app.command()
def chat(message: str) -> None:
    """Conversa com a Juniper (implementação completa em T-040)."""
    console.print(f"[yellow]TODO(T-040):[/yellow] chat ainda não implementado.")
    console.print(f"[dim]Mensagem recebida: {message}[/dim]")


if __name__ == "__main__":
    app()