"""Where the current turn came from, as the tools need to know it (ADR-014, ADR-022).

A turn that comes from the phone app (or a desktop client) can have the music opened *on that
device*; one that comes from the terminal, the Telegram bot or anything else cannot, so the link
is handed back to be sent in the reply. The chat handler sets this for the duration of each turn
(a context variable: it follows the turn's own task and never leaks to another conversation).

Tools that run on the brain only see the ``Channel``, never the connection or the hub, so
``brain/tools`` keeps not depending on ``brain/clients``.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any, Protocol

DEVICE_KINDS = frozenset({"mobile", "desktop"})


class Gateway(Protocol):
    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Run ``tool_name`` on the device of this turn; returns a `ToolResult`, never raises."""


@dataclass(frozen=True)
class Channel:
    kind: str | None  # hello.client.kind: "mobile", "cli", "telegram"...
    voice: bool = False
    can_open: bool = False  # a device of this very connection can open links
    gateway: Gateway | None = None


current_channel: contextvars.ContextVar[Channel | None] = contextvars.ContextVar(
    "current_channel", default=None
)


def link_only() -> bool:
    """True when this turn is served by a channel that cannot open links (so: send the link).

    Without a channel at all (the local CLI, the brain on the user's own machine) it is False:
    there the tool opens the browser itself.
    """
    channel = current_channel.get()
    return channel is not None and not channel.can_open


def channel_note(channel: Channel) -> str:
    """The system note that tells the model which channel it is talking through."""
    how = "voz" if channel.voice else "texto"
    if channel.can_open:
        return (
            f"[Canal: aplicativo do celular ({how}). Para tocar música, chame play_music direto: "
            "o pedido da pessoa já é a confirmação, então não pergunte de novo nem mande o link "
            "da música no texto, porque o aplicativo abre sozinho.]"
        )
    where = channel.kind or "este canal"
    return (
        f"[Canal: {where} ({how}). Aqui não dá para abrir links no celular. Para música, chame "
        "play_music e mande o link do resultado na resposta.]"
    )
