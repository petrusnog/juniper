"""Measures the saving: for real questions, tokens of reading the whole document that holds the
answer versus tokens of ``search_notes``, and whether the right section was found.

    python -m knowledge.bench

Tokens are estimated as chars / 4 (the same rough rule the index uses). "Whole document" is the
honest baseline of an agent with no index: it opens the file that has the answer.
"""

from __future__ import annotations

import sys

from knowledge import db, search
from knowledge.parse import DOCS_DIR

# (question, the documents that answer it, and the one an agent would open without an index).
# The accepted sets were widened after a first run showed the index returned other valid answers
# (a product page, an ADR) where only one page had been listed: the sets are a judgement call.
QUESTIONS: list[tuple[str, tuple[str, ...], str]] = [
    ("quem interpreta a confirmação de uma tool destrutiva",
     ("docs/pages/agente.md", "docs/decisions/ADR-010.md"), "docs/pages/agente.md"),
    ("como o sistema esquece memórias antigas",
     ("docs/pages/memoria-longo-prazo.md", "docs/pages/produto-memoria.md",
      "docs/pages/memoria.md", "docs/decisions/ADR-011.md"), "docs/pages/memoria-longo-prazo.md"),
    ("por que o cliente não pode ter lógica de agente",
     ("docs/pages/architecture.md", "docs/decisions/ADR-007.md"), "docs/pages/architecture.md"),
    ("como é feito o failover entre provedores de LLM",
     ("docs/pages/llm.md", "docs/pages/produto-llm.md", "docs/decisions/ADR-009.md"),
     "docs/pages/llm.md"),
    ("o que é o docs_search e quando o detector dispara",
     ("docs/pages/metadocs.md", "docs/decisions/ADR-025.md"), "docs/pages/metadocs.md"),
    ("como o título de uma conversa é gerado",
     ("docs/pages/titulos.md", "docs/decisions/ADR-036.md"), "docs/pages/titulos.md"),
    ("onde ficam os segredos em produção",
     ("docs/pages/producao.md", "docs/decisions/ADR-013.md", "docs/pages/seguranca.md"),
     "docs/pages/producao.md"),
    ("como funciona a wake word da voz",
     ("docs/pages/voz.md", "docs/pages/voz-audio.md", "docs/decisions/ADR-034.md"),
     "docs/pages/voz.md"),
    ("qual o tier de uma tool desconhecida do servidor",
     ("docs/pages/tools.md", "docs/decisions/ADR-014.md", "docs/decisions/ADR-006.md"),
     "docs/pages/tools.md"),
    ("como o deploy contínuo evita apagar volumes",
     ("docs/pages/deploy-continuo.md", "docs/decisions/ADR-013.md"), "docs/pages/deploy-continuo.md"),
]


def tokens(text: str) -> int:
    return max(1, len(text) // 4)


def main() -> int:
    rows = []
    with db.connect(migrate_schema=False) as conn:
        for question, accepted, baseline in QUESTIONS:
            path = DOCS_DIR.parent / baseline
            whole = tokens(path.read_text(encoding="utf-8")) if path.is_file() else 0
            hits = search.search(conn, question, limit=3)
            out = tokens(search.format_hits(hits, question, 800))
            rank = next(
                (i for i, h in enumerate(hits, 1) if h.source in accepted),
                0,
            )
            rows.append((question, whole, out, rank))
    print(f"{'pergunta':58} {'doc inteiro':>11} {'search':>7} {'achou':>6}")
    for q, whole, out, rank in rows:
        print(f"{q[:58]:58} {whole:>11} {out:>7} {('#' + str(rank)) if rank else 'não':>6}")
    total_whole = sum(r[1] for r in rows)
    total_out = sum(r[2] for r in rows)
    found = sum(1 for r in rows if r[3])
    print(f"\nachou a fonte certa em {found}/{len(rows)} perguntas")
    print(f"tokens: {total_whole} (ler o documento) → {total_out} (search_notes): "
          f"{100 * (1 - total_out / max(total_whole, 1)):.0f}% a menos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
