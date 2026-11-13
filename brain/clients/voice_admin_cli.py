"""``juniper voice status``: what the voice is set up to do and what it has spent this month.

An administrative command like ``juniper music status``: it runs where the brain's files are and
never prints a key; it only says whether the credential file is there.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from brain.clients import voice
from brain.config import settings
from brain.memory.errors import MemoryUnavailableError
from brain.voice.budget import VoiceBudget
from brain.voice.glossary_store import GlossaryError, GlossaryStore


def _glossary_store() -> GlossaryStore:
    return GlossaryStore(max_terms=settings.voice.stt.learned_max)


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

    glossary_app = typer.Typer(
        help="O vocabulário que ajuda a transcrição a acertar nomes (o que ela aprendeu e o seu).",
        no_args_is_help=True,
    )
    voice_app.add_typer(glossary_app, name="glossary")

    def fail(message: str) -> None:
        voice.say(console, message, "red")
        raise typer.Exit(1) from None

    @glossary_app.command("list")
    def glossary_list() -> None:
        """Mostra os termos fixos (config.yaml) e os que a Juniper aprendeu."""
        try:
            learned = _glossary_store().info()
        except MemoryUnavailableError as exc:
            fail(str(exc))
            return
        fixed = settings.voice.stt.glossary
        voice.say(
            console, "Fixos (voice.stt.glossary): " + (", ".join(fixed) if fixed else "nenhum")
        )
        if not learned:
            voice.say(console, "Aprendidos: nenhum")
            return
        voice.say(console, f"Aprendidos ({len(learned)}), do mais recente ao mais antigo:")
        for doc in learned:
            by = f" · por {doc['added_by']}" if doc.get("added_by") else ""
            voice.say(console, f"  {doc['term']}{by}")

    @glossary_app.command("add")
    def glossary_add(
        term: Annotated[str, typer.Argument(help="O nome ou termo, como deve ser escrito.")],
    ) -> None:
        """Acrescenta um termo (vale em até 5 minutos no servidor que já está rodando)."""
        try:
            outcome = _glossary_store().add(term, "cli")
        except GlossaryError as exc:
            fail(f"Não anotei: {exc}.")
            return
        except MemoryUnavailableError as exc:
            fail(str(exc))
            return
        if outcome.status == "full":
            fail("O vocabulário está cheio (voice.stt.learned_max). Tire algum termo antes.")
        voice.say(
            console,
            f"«{outcome.term}» já estava no vocabulário."
            if outcome.status == "exists"
            else f"Anotei «{outcome.term}».",
        )

    @glossary_app.command("remove")
    def glossary_remove(
        term: Annotated[str, typer.Argument(help="O termo a tirar.")],
    ) -> None:
        """Tira um termo que ela aprendeu."""
        try:
            removed = _glossary_store().remove(term)
        except MemoryUnavailableError as exc:
            fail(str(exc))
            return
        if not removed:
            fail(f"«{term}» não está entre os termos aprendidos.")
        voice.say(console, f"Tirei «{term}».")
