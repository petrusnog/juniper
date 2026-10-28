"""Busca local na documentação, para consultar sem abrir páginas inteiras.

Usa o mesmo índice estatístico (BM25) com que a Juniper responde sobre si mesma
(``brain/metadocs``, ADR-025): sem LLM, sem rede. Imprime as seções mais relevantes com o
caminho, a linha e um trecho, para você abrir só o que precisa (``Read`` com ``offset``).

    python scripts/docs_search.py "como a confirmação é interpretada"
    python scripts/docs_search.py --outline agente        # os títulos de uma página, com a linha
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brain.metadocs.index import excerpt, get_index  # noqa: E402

HEADING = re.compile(r"^(#{1,3})\s+(.*?)(?:\s*\{#([\w-]+)[^}]*\})?\s*$")


def line_of(source: str, anchor: str) -> int:
    """A linha do título da seção no arquivo (1 se não achar: o começo)."""
    if not anchor:
        return 1
    for number, line in enumerate((ROOT / source).read_text(encoding="utf-8").split("\n"), 1):
        if f"{{#{anchor}" in line:
            return number
    return 1


def outline(name: str) -> int:
    matches = [
        path
        for pattern in (f"docs/pages/{name}.md", f"docs/decisions/{name}.md", f"docs/{name}.md")
        for path in sorted(ROOT.glob(pattern))
    ]
    if not matches:
        print(f"Sem página '{name}'. Veja docs/MAPA.md.", file=sys.stderr)
        return 1
    fence = False
    for number, line in enumerate(matches[0].read_text(encoding="utf-8").split("\n"), 1):
        fence ^= line.lstrip().startswith("```")
        match = None if fence else HEADING.match(line)
        if match:
            level = len(match.group(1))
            print(
                f"{number:>5}  {'  ' * (level - 1)}{re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', match.group(2))}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Busca local na documentação da Juniper.")
    parser.add_argument("query", nargs="*", help="a pergunta ou as palavras-chave")
    parser.add_argument("--outline", metavar="PAGINA", help="lista os títulos de uma página")
    parser.add_argument("--top", type=int, default=4, help="quantas seções mostrar (4)")
    parser.add_argument("--chars", type=int, default=500, help="tamanho do trecho por seção (500)")
    args = parser.parse_args(argv)
    if args.outline:
        return outline(args.outline.removesuffix(".md"))
    query = " ".join(args.query).strip()
    if not query:
        parser.error("diga o que procurar, ou use --outline PAGINA")
    hits = get_index().search(query, max(1, args.top))
    if not hits:
        print("Nada na documentação. Veja docs/MAPA.md .")
        return 1
    for hit in hits:
        section = hit.section
        print(
            f"{section.source}:{line_of(section.source, section.anchor)}  [{section.page}"
            f"{' › ' + section.heading if section.heading else ''}]  ({hit.score:.1f})"
        )
        print(excerpt(section, query, args.chars), end="\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
