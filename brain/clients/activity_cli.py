"""``juniper atividade``: what Juniper did, in sentences, searchable (Observabilidade V2).

An administrative command like ``juniper vazamentos``: it reads the MongoDB of the process where
it runs. The same filters as the dashboard's Auditoria screen, with dates read in UTC-3.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from brain import i18n
from brain.activity.model import public
from brain.clients import voice
from brain.config import settings
from brain.dashboard import activity as panel
from brain.dashboard.api import ApiError
from brain.memory.activity import ActivityStore
from brain.memory.errors import MemoryUnavailableError

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_OUTCOME_STYLE = {"failed": "red", "pending": "yellow", "denied": "yellow", "partial": "cyan"}


def _safe(value: object) -> str:
    """Any stored value as plain text: no Rich markup, no terminal control characters."""
    return str(escape(_CONTROL.sub("�", str(value if value is not None else ""))))


def _open_store(console: Console) -> ActivityStore:
    from brain.memory.mongo import MongoConnection

    if settings.memory.session.store != "mongo":
        voice.say(
            console,
            "O registro de atividade vive no MongoDB, e aqui as conversas estão em arquivos "
            "(memory.session.store: file). O log técnico segue em: juniper logs.",
            "yellow",
        )
        raise typer.Exit(1)
    connection = MongoConnection()
    try:
        connection.check()
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    return ActivityStore(connection)


def _query(**values: Any) -> dict[str, list[str]]:
    return {k: [str(v)] for k, v in values.items() if v not in (None, "")}


def _show(console: Console, record: dict[str, Any], locale: str) -> None:
    shown = public(record, locale)
    console.print(f"[bold]{_safe(shown['summary'])}[/bold]", highlight=False)
    console.print(
        f"Registro {_safe(shown['id'])} · {panel.clock(record.get('ts'))}"
        f" · {_safe(record.get('category'))}/{_safe(record.get('action'))}"
        f" · desfecho {_safe(record.get('outcome'))}",
        highlight=False,
    )
    facts = [
        ("Sessão", record.get("session")),
        ("Mensagem", record.get("turn")),
        ("Canal", record.get("channel")),
        (
            "Provedor/modelo",
            f"{record.get('provider')}/{record.get('model')}"
            if record.get("provider") or record.get("model")
            else None,
        ),
        ("Ferramenta", record.get("tool")),
        ("Duração (ms)", record.get("duration_ms")),
        ("Tokens", record.get("tokens")),
    ]
    for label, value in facts:
        if value not in (None, ""):
            console.print(f"{label}: {_safe(value)}", highlight=False)
    console.print("\n[bold]Log técnico[/bold]")
    console.print(_safe(record.get("raw")), soft_wrap=True, highlight=False)


def install(app: typer.Typer, console: Console) -> None:
    @app.command("atividade")
    def activity(
        record_id: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--ver", help="Mostra um registro inteiro (o id ou o começo)."),
        ] = None,
        term: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--buscar", "-b", help="Termo no texto amigável e no técnico."),
        ] = None,
        period: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--periodo", help="1h, today, yesterday, 7d ou 30d (dias de UTC-3)."),
        ] = "today",
        since: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--de", help="AAAA-MM-DD ou AAAA-MM-DDTHH:MM, em UTC-3."),
        ] = None,
        until: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--ate", help="Fim do intervalo (um dia inteiro se vier só a data)."),
        ] = None,
        model: Annotated[
            Optional[str], typer.Option("--modelo", help="Modelo usado.")  # noqa: UP045
        ] = None,
        category: Annotated[
            Optional[str], typer.Option("--tipo", help="Categoria: music, web, llm…")  # noqa: UP045
        ] = None,
        action: Annotated[
            Optional[str], typer.Option("--acao", help="Ação exata: music.play_music…")  # noqa: UP045
        ] = None,
        outcome: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--desfecho", help="ok, failed, pending, denied ou partial."),
        ] = None,
        session: Annotated[
            Optional[str], typer.Option("--sessao", help="Id da sessão.")  # noqa: UP045
        ] = None,
        lang: Annotated[
            Optional[str], typer.Option("--idioma", help="Idioma do texto (pt_BR, en).")  # noqa: UP045
        ] = None,
        limit: Annotated[int, typer.Option("--limite", "-n", help="Quantos listar.")] = 30,
    ) -> None:
        """O que a Juniper fez, em frases: busca por data, termo, modelo, tipo de ação e desfecho.

        Os horários são os de UTC-3. Rode onde o MongoDB está (na VPS: dentro do contêiner).
        """
        store = _open_store(console)
        locale = i18n.normalize_locale(lang or settings.activity.locale)
        try:
            if record_id is not None:
                record = store.get(record_id.strip().lower())
                if record is None:
                    voice.say(console, "Não achei esse registro.", "red")
                    raise typer.Exit(1)
                _show(console, record, locale)
                return
            custom = bool(since or until)
            filters = panel.filters_from(
                _query(
                    q=term,
                    period=None if custom else period,
                    **{"from": since, "to": until},
                    model=model,
                    category=category,
                    action=action,
                    outcome=outcome,
                    session=session,
                )
            )
            rows, total = store.search(filters, limit=limit)
        except ApiError as exc:
            voice.say(console, exc.message, "red")
            raise typer.Exit(1) from None
        finally:
            store.connection.close()
        if not rows:
            voice.say(console, "Nada com esses filtros.")
            return
        table = Table(header_style="bold")
        for column in ("Id", f"Quando ({panel.offset_label()})", "O que a Juniper fez", "Modelo"):
            table.add_column(column, overflow="fold")
        for record in rows:
            shown = public(record, locale)
            style = _OUTCOME_STYLE.get(str(record.get("outcome")), "")
            summary = _safe(shown["summary"])
            table.add_row(
                _safe(shown["id"][:8]),
                _safe(panel.clock(record.get("ts")).replace(f" ({panel.offset_label()})", "")),
                f"[{style}]{summary}[/{style}]" if style else summary,
                _safe(record.get("model") or ""),
            )
        console.print(table)
        voice.say(
            console,
            f"{len(rows)} de {total}. Para ver um registro inteiro: juniper atividade --ver <id>",
        )
