"""`open_url`: ask the user's device to open a link (ADR-014, section 6).

The brain never opens anything itself. On the server the dispatcher hands the call to the device
that is connected as an executor (the phone), which applies the same URL policy again and opens
the link; a link to YouTube Music lands in the YouTube Music app. The call is a WRITE: the user is
asked with the exact URL, unless the domain is in ``tools.open_url.trusted_domains`` (the URL
policy of ``url_policy.py`` is never lifted).

``run`` only executes where the brain *is* the user's machine (local mode, no executor): it opens
the default browser there.
"""

from __future__ import annotations

import asyncio
import webbrowser
from typing import Any
from urllib.parse import urlsplit

from brain.config import settings
from brain.tools import music as music_tools
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.channel import link_only
from brain.tools.registry import register_tool
from brain.tools.url_policy import check_url, is_trusted

_SEARCH_PAGES = {
    "music.youtube.com": "/search",
    "www.youtube.com": "/results",
    "youtube.com": "/results",
}


def _is_search_page(url: str, host: str | None) -> bool:
    """A YouTube (Music) search *page*: it opens a list of results and plays nothing."""
    prefix = _SEARCH_PAGES.get(host or "")
    return prefix is not None and urlsplit(url).path.rstrip("/") == prefix


def _denied(reason: str | None) -> ToolResult:
    return ToolResult(
        success=False,
        error=f"Não posso abrir esse endereço: {reason or 'endereço recusado'}.",
        metadata={"code": "policy_denied"},
    )


@register_tool
class OpenUrl(Tool):
    name = "open_url"
    description = (
        "Abre um link no dispositivo da pessoa: o navegador ou o aplicativo que trata o endereço "
        "(por exemplo, o YouTube Music para um link de música). Use quando ela pedir para abrir "
        "um site, uma busca na web ou um vídeo, ou quando você já tiver o link de uma música "
        "para tocar. Só aceita endereços completos http ou https; nunca invente um endereço, use "
        "o que a pessoa passou ou o que outra ferramenta devolveu. Para MÚSICA, nunca monte "
        "links de busca: use antes music_search e abra o link do resultado."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Endereço completo, começando com https:// (ou http://).",
            }
        },
        "required": ["url"],
    }
    tier = Tier.WRITE
    locality = Locality.EXECUTOR

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        result = check_url(kwargs.get("url"))
        if not result.ok:
            return _denied(result.reason)
        if link_only():
            # From the terminal, Telegram... nothing is opened on the phone: the link is the answer.
            return ToolResult(
                success=True,
                output=(
                    "Esta conversa não é do aplicativo do celular, então não abri nada. Mande "
                    f"este link na resposta: {result.url}"
                ),
                metadata={"url": result.url, "opened": False},
            )
        if music_tools.enabled() and _is_search_page(result.url or "", result.host):
            # The model likes to "search" by building a results-page link, which opens a list and
            # plays nothing (and may carry a typo). Send it to the tool that finds the track.
            return ToolResult(
                success=False,
                error=(
                    "Esse endereço é só uma página de busca: não toca nada. Para tocar ou achar "
                    "uma música, chame music_search e abra o link do resultado que ele devolver."
                ),
                metadata={"code": "use_music_search"},
            )
        return None

    async def canonical_arguments(self, **kwargs: Any) -> dict[str, Any]:
        # The user is asked about, and the device receives, the normalised URL: not a second
        # spelling that a different parser could read differently.
        result = check_url(kwargs.get("url"))
        return {**kwargs, "url": result.url} if result.ok else dict(kwargs)

    async def requires_confirmation(self, **kwargs: Any) -> bool:
        result = check_url(kwargs.get("url"))
        if not result.ok or result.host is None:
            return True
        return not is_trusted(result.host, settings.tools.open_url.trusted_domains)

    async def describe(self, **kwargs: Any) -> str | None:
        result = check_url(kwargs.get("url"))
        return f"abrir {result.url}" if result.ok else None

    async def run(self, **kwargs: Any) -> ToolResult:
        result = check_url(kwargs.get("url"))
        if not result.ok or result.url is None:  # re-validate: state may have changed
            return _denied(result.reason)
        opened = await asyncio.to_thread(webbrowser.open, result.url)
        if not opened:
            return ToolResult(
                success=False,
                error="Não consegui abrir o navegador neste dispositivo.",
                metadata={"code": "os_error", "url": result.url},
            )
        return ToolResult(
            success=True,
            output=f"Abri {result.url} no navegador.",
            metadata={"url": result.url},
        )
