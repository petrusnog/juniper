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

from brain.config import settings
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool
from brain.tools.url_policy import check_url, is_trusted


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
        "um site, uma busca ou um vídeo, ou quando você já tiver o link de uma música para tocar. "
        "Só aceita endereços completos http ou https; nunca invente um endereço, use o que a "
        "pessoa passou ou o que outra ferramenta devolveu."
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
        return None if result.ok else _denied(result.reason)

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
