"""``juniper conversas`` and ``juniper renomear``: the conversations by title (T-226).

Work where the conversations live: in the terminal's own store (local mode) or, with
``deployment.mode: server``, through the server's protocol (``session.list`` / ``session.rename``).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console

from brain.clients import conversations, voice
from brain.config import settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.session import SessionStoreError


async def fetch_entries() -> list[conversations.Entry]:
    """The saved conversations, newest first."""
    if settings.deployment.mode == "server":
        from brain.clients import remote_cli

        client = remote_cli.make_client()
        try:
            await client.start(None, remote_cli.CONNECT_WAIT_SECONDS)
            listed = await client.request({"type": "session.list", "limit": 200})
        finally:
            await client.close()
        return conversations.entries_of(listed)
    from brain.memory.stores import close_session_store, open_session_store

    store = open_session_store()
    try:
        infos = await asyncio.to_thread(store.list_sessions)
    finally:
        close_session_store(store)
    return [conversations.Entry(i.id, i.title, i.preview, i.updated_at, i.turns) for i in infos]


async def rename_entry(session_id: str, title: str) -> bool:
    """Rename one saved conversation; False when it does not exist."""
    if settings.deployment.mode == "server":
        from brain.clients import remote_cli
        from brain.clients.remote import ServerError

        client = remote_cli.make_client()
        try:
            await client.start(None, remote_cli.CONNECT_WAIT_SECONDS)
            await client.request(
                {"type": "session.rename", "session_id": session_id, "title": title}
            )
        except ServerError as exc:
            if exc.error.code == "session_not_found":
                return False
            raise
        finally:
            await client.close()
        return True
    from brain.memory.stores import close_session_store, open_session_store

    store = open_session_store()
    try:
        return await asyncio.to_thread(store.set_title, session_id, title, "user")
    finally:
        close_session_store(store)


def install(app: typer.Typer, console: Console) -> None:
    @app.command("conversas")
    def list_conversations() -> None:
        """Lista as conversas pelo título (as mais recentes primeiro)."""
        try:
            entries = asyncio.run(fetch_entries())
        except (MemoryUnavailableError, SessionStoreError) as exc:
            voice.say(console, str(exc), "red")
            raise typer.Exit(1) from None
        console.print(conversations.format_list(entries), highlight=False)

    @app.command("renomear")
    def rename(
        reference: Annotated[
            str, typer.Argument(help="Número da lista (juniper conversas) ou id.")
        ],
        title: Annotated[list[str], typer.Argument(help="O novo título.")],
    ) -> None:
        """Renomeia uma conversa. A Juniper nunca mais troca um título que você escolheu."""
        new = conversations.new_title(" ".join(title))
        if not new:
            voice.say(console, conversations.RENAME_USAGE, "yellow")
            raise typer.Exit(1)
        try:
            entries = asyncio.run(fetch_entries())
            chosen, problem = conversations.resolve(reference, entries)
            if chosen is None:
                voice.say(console, problem, "yellow")
                raise typer.Exit(1)
            if not asyncio.run(rename_entry(chosen.id, new)):
                voice.say(console, conversations.NOT_FOUND, "yellow")
                raise typer.Exit(1)
        except (MemoryUnavailableError, SessionStoreError) as exc:
            voice.say(console, str(exc), "red")
            raise typer.Exit(1) from None
        console.print(conversations.renamed_text(new), highlight=False)
