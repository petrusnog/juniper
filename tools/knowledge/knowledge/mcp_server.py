"""Read-only MCP server (stdio) over the knowledge base, for Claude, Codex or any MCP client.

Expected failures (database or Ollama down, unknown note) come back as plain text for the
agent, never as an exception that drops the connection. Nothing here writes to the database.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from knowledge import db, search
from knowledge.embed import EmbeddingError

mcp = MCPServer(
    "juniper-knowledge",
    instructions=(
        "Documentação da Juniper em notas. Para uma dúvida pontual use search_notes (devolve "
        "trechos curtos, barato) e só depois get_note da nota exata; evite ler arquivos de "
        "docs/ inteiros. neighbors mostra as notas relacionadas."
    ),
)


def _run(fn):  # type: ignore[no-untyped-def]
    try:
        with db.connect(migrate_schema=False) as conn:
            return fn(conn)
    except EmbeddingError as exc:
        return f"Busca indisponível: {exc}"
    except Exception as exc:  # noqa: BLE001 - a tool must answer, not crash the server
        return f"Base de conhecimento indisponível ({type(exc).__name__}): {exc}"


@mcp.tool()
def search_notes(
    query: str,
    limit: int = 5,
    max_tokens: int = 1500,
    kind: str | None = None,
    tag: str | None = None,
) -> str:
    """Busca híbrida (significado + palavras) nas notas da documentação.

    Devolve até `limit` notas com um trecho cada, somando no máximo ~`max_tokens` tokens.
    `kind`: "page" (como é hoje) ou "adr" (por que foi decidido). `tag`: filtra por tag.
    """
    limit = max(1, min(limit, 10))
    max_tokens = max(200, min(max_tokens, 6000))

    def go(conn):  # type: ignore[no-untyped-def]
        hits = search.search(conn, query, limit=limit, kind=kind, tag=tag)
        return search.format_hits(hits, query, max_tokens)

    return _run(go)


@mcp.tool()
def get_note(ref: str, max_tokens: int = 4000) -> str:
    """Lê uma nota inteira (`docs/pages/x.md#ancora`). Sem âncora, lista as seções do documento."""
    return _run(lambda conn: search.get_note(conn, ref, max_tokens=max(200, max_tokens)))


@mcp.tool()
def neighbors(source: str) -> str:
    """Notas ligadas a um documento (`docs/pages/x.md`): para onde aponta e quem o cita."""
    return _run(lambda conn: search.neighbors(conn, source))


@mcp.tool()
def list_documents(kind: str | None = None, tag: str | None = None, group: str | None = None) -> str:
    """Lista os documentos (arquivo, título e descrição), com filtros opcionais."""
    return _run(lambda conn: search.list_documents(conn, kind=kind, tag=tag, group=group))


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
