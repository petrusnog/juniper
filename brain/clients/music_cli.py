"""``juniper music``: log in to YouTube for playlists, and see the state of the music setup.

Administrative commands like ``memory reindex``: they run where the brain's files are (on the VPS,
through the container shortcut) and change nothing in the protocol. Nothing here prints a key, a
token or the client secret; ``status`` only says whether each one is set.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from rich.console import Console

from brain.clients import voice
from brain.config import settings
from brain.memory.errors import MemoryUnavailableError
from brain.music import oauth
from brain.music import taste as taste_store
from brain.music.base import MusicError
from brain.music.quota import QuotaTracker
from brain.music.taste import TasteBook

SIGNAL_WORDS = {"like": "gostou", "dislike": "não gostou", "skip": "pulou", "played": "pediu"}


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"{days}d{hours:02d}h"
    return f"{hours}h{rest // 60:02d}"


def status_lines() -> list[str]:
    """What is configured and what is not, without revealing any secret."""
    cfg = settings.music
    quota = QuotaTracker(cfg.quota_file, cfg.daily_quota_limit)
    token = oauth.TokenStore(cfg.oauth_file).load()
    if token is None:
        login = f"sem login (para playlists, {oauth.LOGIN_HINT})"
    elif token.expires_at > time.time():
        login = f"conectada (o acesso atual vale mais {_duration(token.expires_at - time.time())})"
    else:
        login = "conectada (o acesso será renovado na próxima vez que precisar)"
    return [
        f"Música: {'ligada' if cfg.enabled else 'desligada (music.enabled: false)'}",
        f"Chave da YouTube Data API: {'configurada' if settings.youtube_api_key else 'FALTA'}",
        f"Cota de hoje: {quota.used()} de {quota.limit} unidades "
        f"(volta em {_duration(quota.resets_in().total_seconds())})",
        "Cliente OAuth (ID e chave): "
        + ("configurado" if oauth.build_oauth() is not None else "FALTA (só para playlists)"),
        f"Conta do YouTube: {login}",
    ]


def install(app: typer.Typer, console: Console) -> None:
    """Register ``juniper music`` on the main app."""
    music_app = typer.Typer(
        help="Música: login no YouTube e estado da configuração.", no_args_is_help=True
    )
    app.add_typer(music_app, name="music")

    @music_app.command("status")
    def status() -> None:
        """Mostra o que está configurado (sem revelar nenhuma chave) e a cota de hoje."""
        voice.say(console, "\n".join(status_lines()))

    @music_app.command("login")
    def login() -> None:
        """Entra na sua conta do YouTube (código de dispositivo) para criar playlists."""
        client = oauth.build_oauth()
        if client is None:
            voice.say(console, oauth.client_hint(), "red")
            raise typer.Exit(1)

        async def run() -> None:
            try:
                await oauth.login(
                    client,
                    oauth.TokenStore(settings.music.oauth_file),
                    lambda line: voice.say(console, line),
                )
            finally:
                await client.aclose()

        try:
            asyncio.run(run())
        except MusicError as exc:
            voice.say(console, exc.message, "red")
            raise typer.Exit(1) from None
        except KeyboardInterrupt:
            voice.say(console, "Login cancelado.", "yellow")
            raise typer.Exit(1) from None
        voice.say(
            console,
            "Pronto: entrei na sua conta do YouTube. O acesso fica guardado em "
            f"{settings.music.oauth_file} (só você lê).",
        )

    taste_app = typer.Typer(
        help="O que anotei do seu gosto musical (só o que você pediu e comentou).",
        invoke_without_command=True,
    )
    music_app.add_typer(taste_app, name="taste")

    def book() -> TasteBook:
        try:
            return taste_store.book()
        except MemoryUnavailableError as exc:
            voice.say(console, str(exc), "red")
            raise typer.Exit(1) from None

    @taste_app.callback()
    def taste(
        ctx: typer.Context,
        limit: Annotated[int, typer.Option("--limit", "-n", help="Quantos eventos mostrar.")] = 15,
    ) -> None:
        """Mostra o resumo e os últimos registros do seu gosto musical."""
        if ctx.invoked_subcommand is not None:
            return
        taste_book = book()
        summary = taste_book.summary()
        if summary.total == 0:
            voice.say(console, "Ainda não anotei nada do seu gosto musical.")
            return
        zone = ZoneInfo(settings.agent.timezone)
        lines = [f"Anotei {summary.total} registro(s) do seu gosto musical."]
        lines.append(
            "Por sinal: "
            + ", ".join(
                f"{SIGNAL_WORDS.get(k, k)} {v}" for k, v in sorted(summary.by_signal.items())
            )
        )
        if summary.artists:
            lines.append(
                "Artistas em alta: "
                + ", ".join(f"{a} ({score:+d})" for a, score in summary.artists)
            )
        lines.append("Últimos registros:")
        for event in taste_book.recent(limit):
            what = " — ".join(p for p in (event.title, event.artist) if p)
            when = event.at.astimezone(zone).strftime("%d/%m/%Y %H:%M")
            lines.append(f"  {when}  {SIGNAL_WORDS.get(event.signal, event.signal)}: {what}")
        voice.say(console, "\n".join(lines))

    @taste_app.command("clear")
    def clear(
        yes: Annotated[
            bool, typer.Option("--yes", "-y", help="Não pergunta antes de apagar.")
        ] = False,
    ) -> None:
        """Apaga TODO o gosto musical anotado (não dá para desfazer)."""
        taste_book = book()
        total = taste_book.count()
        if total == 0:
            voice.say(console, "Não há nada anotado para apagar.")
            return
        if not yes and not typer.confirm(f"Apagar os {total} registro(s) do seu gosto musical?"):
            voice.say(console, "Ok, não apaguei nada.")
            return
        voice.say(console, f"Apaguei {taste_book.clear()} registro(s).")
