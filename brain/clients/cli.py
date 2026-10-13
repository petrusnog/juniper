"""Cliente CLI da Juniper.

Entry point oficial (`juniper = brain.clients.cli:app` no pyproject).
Por enquanto, apenas placeholder até T-040.
"""

import webbrowser
from pathlib import Path

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


def _find_docs() -> Path | None:
    """Localiza o livro de bolso (docs/index.html)."""
    candidates = [
        Path(__file__).resolve().parents[2] / "docs" / "index.html",
        Path.home() / ".juniper" / "docs" / "index.html",
    ]
    return next((p for p in candidates if p.is_file()), None)


@app.command()
def docs() -> None:
    """Abre a documentação (livro de bolso) no navegador padrão."""
    index = _find_docs()
    if index is None:
        console.print("[red]Documentação não encontrada (docs/index.html).[/red]")
        raise typer.Exit(code=1)

    url = index.as_uri()
    if webbrowser.open_new_tab(url):
        console.print(f"[green]📖 Documentação aberta no navegador.[/green] [dim]{url}[/dim]")
    else:
        console.print(f"[yellow]Não consegui abrir o navegador. Abra manualmente:[/yellow] {url}")


@app.command()
def chat(message: str) -> None:
    """Conversa com a Juniper (implementação completa em T-040)."""
    console.print(f"[yellow]TODO(T-040):[/yellow] chat ainda não implementado.")
    console.print(f"[dim]Mensagem recebida: {message}[/dim]")


if __name__ == "__main__":
    app()