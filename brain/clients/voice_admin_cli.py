"""``juniper voice status``: what the voice is set up to do and what it has spent this month.

An administrative command like ``juniper music status``: it runs where the brain's files are and
never prints a key; it only says whether the credential file is there.
"""

from __future__ import annotations

import typer
from rich.console import Console

from brain.clients import voice
from brain.config import settings
from brain.voice.budget import VoiceBudget


def status_lines() -> list[str]:
    cfg = settings.voice
    budget = VoiceBudget(cfg.usage_file, cfg.tts.monthly_chars_limit)
    credentials = cfg.tts.credentials_file.is_file()
    used = budget.used()
    limit = cfg.tts.monthly_chars_limit
    cloud = (
        "desligada (monthly_chars_limit: 0): só a voz do aparelho"
        if limit == 0
        else (f"{used} de {limit} caracteres neste mês ({max(0, limit - used)} restantes)")
    )
    return [
        f"Voz: {'ligada' if cfg.enabled else 'desligada (voice.enabled: false)'}",
        f"Voz do Google ({cfg.tts.voice}): {cloud}",
        f"Credencial do Google: {'encontrada' if credentials else 'FALTA'}"
        f" ({cfg.tts.credentials_file})",
        f"Chave da Groq (fala para texto): {'configurada' if settings.groq_api_key else 'FALTA'}",
        f"Lê no máximo {cfg.max_spoken_chars} caracteres por resposta; encerra após "
        f"{cfg.idle_timeout_seconds}s de silêncio.",
    ]


def install(app: typer.Typer, console: Console) -> None:
    voice_app = typer.Typer(help="Voz por áudio: estado da configuração.", no_args_is_help=True)
    app.add_typer(voice_app, name="voice")

    @voice_app.command("status")
    def status() -> None:
        """Mostra se a voz está ligada, o que está configurado e o gasto do mês (sem chaves)."""
        voice.say(console, "\n".join(status_lines()))
