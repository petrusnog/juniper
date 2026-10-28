"""``docs_search``: the Juniper reads her own documentation (ADR-025).

READ, runs on the brain. The search is local and statistical (``brain.metadocs``): no LLM call
and no network. It only ever reads ``docs/pages`` and ``docs/decisions``; the query is the only
parameter, and the excerpts come back with secrets masked.
"""

from __future__ import annotations

from typing import Any

from brain.config import settings
from brain.eventlog import event_log
from brain.metadocs import available as docs_available
from brain.metadocs import render, search
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool

MAX_QUERY_CHARS = 300


@register_tool
class DocsSearch(Tool):
    name = "docs_search"
    description = (
        "Busca na sua própria documentação (arquitetura, memória, ferramentas, segurança, "
        "clientes, deploy, decisões). Use em perguntas sobre como você funciona ou foi feita."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "O assunto, em poucas palavras."},
        },
        "required": ["query"],
    }
    tier = Tier.READ
    locality = Locality.CLOUD

    @classmethod
    def available(cls) -> bool:
        return settings.metadocs.enabled and docs_available()

    async def run(self, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query") or "").strip()[:MAX_QUERY_CHARS]
        if not query:
            return ToolResult(success=False, error="Diga o assunto a procurar na documentação.")
        try:
            hits = search(query)
        except OSError as exc:
            event_log.log("error", where="metadocs.tool", type=type(exc).__name__)
            return ToolResult(success=False, error="Não consegui ler a minha documentação agora.")
        event_log.log(
            "metadocs",
            action="tool",
            results=len(hits),
            best=round(hits[0].score, 2) if hits else 0,
            sources=[h.section.where for h in hits],
        )
        if not hits:
            return ToolResult(
                success=True,
                output="A documentação não fala disso. Diga que não sabe, sem inventar.",
                metadata={"results": 0},
            )
        return ToolResult(
            success=True,
            output=render(hits, query, settings.metadocs.max_chars),
            metadata={"results": len(hits), "sources": [h.section.where for h in hits]},
        )
