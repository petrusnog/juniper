"""``juniper vazamentos``: the evidence of what leaked into her speech (T-165).

An administrative command like ``memory reindex``: it reads the MongoDB of the process where it
runs (on the VPS, inside the brain container) and changes nothing in the protocol.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from brain.clients import voice
from brain.config import settings
from brain.memory.errors import MemoryUnavailableError

_PREVIEW = 70


def _local(moment: datetime) -> str:
    return f"{moment.astimezone(ZoneInfo(settings.agent.timezone)):%d/%m/%Y %H:%M}"


def _flat(text: str | None, limit: int = _PREVIEW) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def _open_store(console: Console) -> Any:
    from brain.memory.leak_audit import LeakAuditStore
    from brain.memory.mongo import MongoConnection

    if settings.memory.session.store != "mongo":
        voice.say(
            console,
            "A auditoria de vazamentos vive no MongoDB, e aqui as conversas estão em arquivos "
            "(memory.session.store: file). Só o evento leak_audit vai ao log: "
            "juniper logs -e leak.",
            "yellow",
        )
        raise typer.Exit(1)
    connection = MongoConnection()
    try:
        connection.check()
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None
    return LeakAuditStore(connection)


def _show(console: Console, record: dict[str, Any]) -> None:
    status = (
        "vazou (você viu)" if record.get("leaked") else "só o modelo escreveu (o guarda cortou)"
    )
    console.print(f"[bold]Registro {record['_id']}[/bold]  {_local(record['at'])}  {status}")
    console.print(
        f"Sessão {record.get('session')} · canal {record.get('channel')}"
        f"{' (voz)' if record.get('voice') else ''}"
        f" · {record.get('provider')}/{record.get('model')}"
        f" · finish_reason {record.get('finish_reason')} · commit {record.get('commit') or '?'}"
    )
    if record.get("tool_calls"):
        console.print(f"Tools: {', '.join(record['tool_calls'])}")
    blocks = ", ".join(f"{b['kind']} ({b['chars']})" for b in record.get("blocks") or [])
    console.print(f"Blocos de sistema no prompt: {blocks or 'nenhum'}")
    console.print("\n[bold]Regras disparadas[/bold]")
    for hit in record.get("hits") or []:
        where = "entregue" if hit["where"] == "delivered" else "bruto"
        console.print(f"  {hit['rule']} ({where}, pos. {hit['at']}): «{escape(hit['excerpt'])}»")
    for title, key in (
        ("Mensagem do usuário", "user_message"),
        ("Texto bruto do modelo", "raw"),
        ("Texto entregue", "delivered"),
    ):
        console.print(f"\n[bold]{title}[/bold]")
        console.print(escape(record.get(key) or "(vazio)"), soft_wrap=True)


def install(app: typer.Typer, console: Console) -> None:
    @app.command("vazamentos")
    def leaks(
        record_id: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--ver", help="Mostra um registro inteiro (o id ou o começo dele)."),
        ] = None,
        summary: Annotated[
            bool, typer.Option("--resumo", help="Conta por regra, modelo e canal.")
        ] = False,
        days: Annotated[int, typer.Option("--dias", help="Quantos dias para trás.")] = 7,
        leaked_only: Annotated[
            bool,
            typer.Option(
                "--vazados", help="Só os que chegaram a você (não os que o guarda cortou)."
            ),
        ] = False,
        limit: Annotated[int, typer.Option("--limite", "-n", help="Quantos listar.")] = 30,
    ) -> None:
        """Mostra as respostas em que a Juniper deixou escapar tags, instruções ou o prompt.

        Cada registro guarda o texto bruto do modelo, o texto entregue e o que disparou. Rode onde
        o MongoDB está (na VPS: dentro do contêiner do brain).
        """
        store = _open_store(console)
        try:
            if record_id is not None:
                record = store.get(record_id.strip().lower())
                if record is None:
                    voice.say(
                        console, "Não achei esse registro (ou o começo do id é ambíguo).", "red"
                    )
                    raise typer.Exit(1)
                _show(console, record)
                return
            if summary:
                data = store.summary(days=days)
                voice.say(
                    console,
                    f"Últimos {days} dia(s): {data['total']} registro(s), {data['leaked']} "
                    f"chegaram a você, {data['blocked']} com corte do guarda.",
                )
                for title, key in (("Regra", "rules"), ("Modelo", "models"), ("Canal", "channels")):
                    table = Table(header_style="bold")
                    table.add_column(title)
                    table.add_column("Vezes", justify="right")
                    for name, count in data[key]:
                        table.add_row(escape(str(name)), str(count))
                    console.print(table)
                return
            records = store.recent(days=days, leaked_only=leaked_only, limit=limit)
        except MemoryUnavailableError as exc:
            voice.say(console, str(exc), "red")
            raise typer.Exit(1) from None
        finally:
            store.connection.close()
        if not records:
            voice.say(console, f"Nenhum registro nos últimos {days} dia(s).")
            return
        table = Table(header_style="bold")
        for column in ("Id", "Quando", "Canal", "Modelo", "Vazou", "Regras", "Entregue"):
            table.add_column(column, overflow="fold")
        for r in records:
            table.add_row(
                r["_id"][:8],
                _local(r["at"]),
                str(r.get("channel")),
                str(r.get("model")),
                "sim" if r.get("leaked") else "não",
                ", ".join(r.get("rules") or []),
                escape(_flat(r.get("delivered"))),
            )
        console.print(table)
        voice.say(console, "Para ver um caso inteiro: juniper vazamentos --ver <id>")
