"""CLI: ``python -m knowledge migrate | index [--force] | search "pergunta" | serve``."""

from __future__ import annotations

import argparse
import sys

from knowledge import db, indexer, search
from knowledge.embed import EmbeddingError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="knowledge")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="aplica o schema")
    p_index = sub.add_parser("index", help="indexa docs/ (incremental)")
    p_index.add_argument("--force", action="store_true", help="reindexa tudo")
    p_search = sub.add_parser("search", help="busca de teste")
    p_search.add_argument("query", nargs="+")
    p_search.add_argument("--limit", type=int, default=5)
    p_search.add_argument("--max-tokens", type=int, default=1500)
    sub.add_parser("serve", help="servidor MCP (stdio)")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        from knowledge.mcp_server import main as serve

        serve()
        return 0
    try:
        with db.connect() as conn:
            if args.cmd == "index":
                r = indexer.index_docs_dir(conn, force=args.force)
                print(
                    f"{r.documents} documentos ({r.skipped} sem mudança); notas: "
                    f"{r.notes_embedded} embutidas, {r.notes_reused} reaproveitadas, "
                    f"{r.notes_removed} removidas; documentos removidos: {r.documents_removed}"
                )
                for w in r.warnings:
                    print(f"aviso: {w}", file=sys.stderr)
            elif args.cmd == "search":
                q = " ".join(args.query)
                print(search.format_hits(search.search(conn, q, limit=args.limit), q, args.max_tokens))
    except EmbeddingError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
