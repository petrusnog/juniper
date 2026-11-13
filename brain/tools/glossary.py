"""Tools for her own transcription vocabulary: ``glossary_add``, ``glossary_remove`` and
``glossary_list`` (T-218, ADR-034 section 4).

The speech-to-text spells "Media Works" as "mídia works" because nothing tells it such a project
exists. Here the Juniper keeps that list herself: when the person says a name or corrects how a
word was written, the model saves the term, and it counts from the next utterance on.

Like ``remember``, saving and removing are WRITE tools that run **without asking**
(``confirm = False``): the vocabulary is hers, small and auditable (``glossary_list``,
``juniper voice glossary``). What makes that safe is what they refuse:

- after a file was read in the conversation (``blocked_when_tainted``, ADR-033) and after the web
  was read in it (``blocked_when_web_read``): text from outside must not be able to teach her a
  word, because a term would later replace what the person *said* in every transcription;
- a term that is not a plain name (see `clean_term`), and a list that is full.

While no store is bound (voice off, or no MongoDB) the tools are not offered to the model at all.
"""

from __future__ import annotations

import asyncio
from typing import Any

from brain.auth.context import current_user
from brain.config import settings
from brain.eventlog import event_log
from brain.memory.errors import MemoryUnavailableError
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool
from brain.voice.glossary import Glossary
from brain.voice.glossary_store import GlossaryError, GlossaryStore, clean_term

_store: GlossaryStore | None = None
_glossary: Glossary | None = None

OFF = "O vocabulário de voz precisa da voz ligada (voice.enabled) e do MongoDB."


def bind(store: GlossaryStore | None, glossary: Glossary | None = None) -> None:
    """Point the tools at the server's store and live glossary (``None`` unbinds them)."""
    global _store, _glossary
    _store, _glossary = store, glossary


def _refresh() -> None:
    if _glossary is not None:
        _glossary.invalidate()  # the next utterance already uses the change


class _GlossaryTool(Tool):
    locality = Locality.CLOUD

    @classmethod
    def available(cls) -> bool:
        return _store is not None

    @staticmethod
    def store() -> GlossaryStore:
        if _store is None:
            raise MemoryUnavailableError(OFF)
        return _store


@register_tool
class GlossaryAdd(_GlossaryTool):
    name = "glossary_add"
    description = (
        "Anota no seu vocabulário de voz um NOME PRÓPRIO ou termo que o reconhecimento de fala "
        "costuma errar (pessoa, projeto, cliente, lugar, sigla). Use quando a pessoa pedir, ou "
        "quando ela corrigir como uma palavra foi escrita («não, é Media Works»). Só nomes e "
        "termos que ELA disse ou confirmou: nunca palavras comuns, frases, links, nem nada que "
        "veio de uma página ou de um arquivo. Grava direto, sem pedir permissão, e vale a partir "
        "da próxima fala. Depois diga em uma frase curta o que anotou."
    )
    parameters = {
        "type": "object",
        "properties": {
            "term": {
                "type": "string",
                "description": "O termo como deve ser escrito, ex.: 'Media Works'.",
            }
        },
        "required": ["term"],
    }
    tier = Tier.WRITE
    blocked_when_tainted = True  # never learn from a file's text (ADR-033)
    blocked_when_web_read = True  # nor from the web (T-218)
    confirm = False  # silent, like `remember`: `glossary_list` / the CLI show and delete it all

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        if _store is None:
            return ToolResult(success=False, error=OFF)
        try:
            clean_term(kwargs.get("term"))
        except GlossaryError as exc:
            return ToolResult(success=False, error=f"Não anotei: {exc}.")
        return None

    async def describe(self, **kwargs: Any) -> str | None:
        return f"anotar no vocabulário de voz: «{kwargs.get('term', '')}»"

    async def run(self, **kwargs: Any) -> ToolResult:
        owner = str(current_user.get() or "")
        try:
            outcome = await asyncio.to_thread(self.store().add, kwargs.get("term"), owner)
        except GlossaryError as exc:
            return ToolResult(success=False, error=f"Não anotei: {exc}.")
        except MemoryUnavailableError as exc:
            return ToolResult(success=False, error=str(exc))
        if outcome.status == "full":
            return ToolResult(
                success=False,
                error="O vocabulário está cheio. Remova algum termo antes (glossary_remove).",
            )
        event_log.log("voice", action="glossary_add", status=outcome.status, term=outcome.term)
        _refresh()
        if outcome.status == "exists":
            return ToolResult(success=True, output=f"«{outcome.term}» já estava no vocabulário.")
        return ToolResult(
            success=True,
            output=f"Anotei «{outcome.term}» no vocabulário. Vale a partir da próxima fala.",
        )


@register_tool
class GlossaryRemove(_GlossaryTool):
    name = "glossary_remove"
    description = (
        "Tira um termo do seu vocabulário de voz (quando a pessoa pedir, ou quando um termo "
        "anotado estiver atrapalhando). Grava direto, sem pedir permissão."
    )
    parameters = {
        "type": "object",
        "properties": {"term": {"type": "string", "description": "O termo a tirar."}},
        "required": ["term"],
    }
    tier = Tier.WRITE
    blocked_when_tainted = True
    blocked_when_web_read = True
    confirm = False

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        if _store is None:
            return ToolResult(success=False, error=OFF)
        return None

    async def describe(self, **kwargs: Any) -> str | None:
        return f"tirar do vocabulário de voz: «{kwargs.get('term', '')}»"

    async def run(self, **kwargs: Any) -> ToolResult:
        term = str(kwargs.get("term", ""))
        try:
            removed = await asyncio.to_thread(self.store().remove, term)
        except MemoryUnavailableError as exc:
            return ToolResult(success=False, error=str(exc))
        if not removed:
            return ToolResult(success=True, output=f"«{term}» não estava no vocabulário aprendido.")
        event_log.log("voice", action="glossary_remove", term=" ".join(term.split())[:60])
        _refresh()
        return ToolResult(success=True, output=f"Tirei «{term}» do vocabulário.")


@register_tool
class GlossaryList(_GlossaryTool):
    name = "glossary_list"
    description = (
        "Mostra o seu vocabulário de voz: os termos fixos da configuração e os que você aprendeu."
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    tier = Tier.READ

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            learned = await asyncio.to_thread(self.store().terms)
        except MemoryUnavailableError as exc:
            return ToolResult(success=False, error=str(exc))
        fixed = list(settings.voice.stt.glossary)
        lines = [
            "Fixos (configuração): " + (", ".join(fixed) if fixed else "nenhum"),
            "Aprendidos: " + (", ".join(learned) if learned else "nenhum"),
        ]
        return ToolResult(success=True, output="\n".join(lines))
