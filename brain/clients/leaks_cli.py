"""``juniper vazamentos``: the evidence of what leaked into her speech (T-165).

An administrative command like ``memory reindex``: it reads the MongoDB of the process where it
runs (on the VPS, inside the brain container) and changes nothing in the protocol.
"""

from __future__ import annotations

import re
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
# Terminal control characters (ESC included) never reach the admin's terminal: the texts come
# from the model, and through it from whatever it read.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _safe(value: object) -> str:
    """Any stored value, printable as plain text (no Rich markup, no control characters)."""
    return escape(_CONTROL.sub("�", str(value if value is not None else "")))


def _local(moment: datetime) -> str:
    return f"{moment.astimezone(ZoneInfo(settings.agent.timezone)):%d/%m/%Y %H:%M}"


def _flat(text: str | None, limit: int = _PREVIEW) -> str:
    flat = " ".join((text or "").split())
    return _safe(flat if len(flat) <= limit else f"{flat[:limit]}…")


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


def _list_reports(console: Console, reports: list[dict[str, Any]]) -> None:
    if not reports:
        voice.say(console, "Nenhum relato ainda. No Telegram ou no app, escreva /vazou.")
        return
    table = Table(header_style="bold")
    for column in ("Id", "Quando", "Canal", "Voz", "Interações", "Nota"):
        table.add_column(column, overflow="fold")
    for r in reports:
        table.add_row(
            _safe(r["_id"][:8]),
            _local(r["at"]),
            _safe(r.get("channel")),
            "sim" if r.get("voice") else "não",
            str(len(r.get("exchanges") or [])),
            _flat(r.get("note")),
        )
    console.print(table)
    voice.say(console, "Para ver um relato inteiro: juniper vazamentos --ver <id>")


def _show_report(console: Console, report: dict[str, Any]) -> None:
    console.print(
        f"[bold]Relato {_safe(report['_id'])}[/bold]  {_local(report['at'])}"
        f"  (você marcou com /vazou{' ou por voz' if report.get('voice') else ''})",
        highlight=False,
    )
    console.print(
        f"Sessão {_safe(report.get('session'))} · canal {_safe(report.get('channel'))}"
        f" · commit {_safe(report.get('commit') or '?')}",
        highlight=False,
    )
    if report.get("note"):
        console.print(f"Nota: {_safe(report['note'])}", highlight=False)
    if report.get("audit"):
        ids = ", ".join(str(i)[:8] for i in report["audit"])
        console.print(f"Registros automáticos da mesma sessão: {_safe(ids)}", highlight=False)
    for index, exchange in enumerate(report.get("exchanges") or [], start=1):
        stamp = f" · {_safe(exchange['at'])}" if exchange.get("at") else ""
        cut = " (você cortou a resposta)" if exchange.get("interrupted") else ""
        console.print(f"\n[bold]Interação {index}[/bold]{stamp}{cut}")
        console.print(f"Você: {_safe(exchange.get('user'))}", soft_wrap=True, highlight=False)
        console.print(
            f"Juniper: {_safe(exchange.get('assistant'))}", soft_wrap=True, highlight=False
        )


def _show(console: Console, record: dict[str, Any]) -> None:
    if record.get("quarantined"):
        status = "vazou e o turno ficou fora da memória"
    elif record.get("leaked"):
        status = "algo suspeito chegou a você (o turno seguiu para a memória)"
    else:
        status = "só o modelo escreveu (o guarda cortou)"
    console.print(
        f"[bold]Registro {_safe(record['_id'])}[/bold]  {_local(record['at'])}  {status}",
        highlight=False,
    )
    console.print(
        f"Sessão {_safe(record.get('session'))} · canal {_safe(record.get('channel'))}"
        f"{' (voz)' if record.get('voice') else ''}"
        f" · {_safe(record.get('provider'))}/{_safe(record.get('model'))}"
        f" · finish_reason {_safe(record.get('finish_reason'))}"
        f" · commit {_safe(record.get('commit') or '?')}",
        highlight=False,
    )
    if record.get("tool_calls"):
        console.print(f"Tools: {_safe(', '.join(record['tool_calls']))}", highlight=False)
    blocks = ", ".join(f"{b['kind']} ({b['chars']})" for b in record.get("blocks") or [])
    console.print(f"Blocos de sistema no prompt: {_safe(blocks or 'nenhum')}", highlight=False)
    console.print("\n[bold]Regras disparadas[/bold]")
    for hit in record.get("hits") or []:
        where = "entregue" if hit["where"] == "delivered" else "bruto"
        console.print(
            f"  {_safe(hit['rule'])} ({where}, pos. {_safe(hit['at'])}): «{_safe(hit['excerpt'])}»",
            highlight=False,
        )
    for title, key in (
        ("Mensagem do usuário", "user_message"),
        ("Texto bruto do modelo", "raw"),
        ("Texto entregue", "delivered"),
    ):
        console.print(f"\n[bold]{title}[/bold]")
        console.print(_safe(record.get(key) or "(vazio)"), soft_wrap=True, highlight=False)


def install(app: typer.Typer, console: Console) -> None:
    @app.command("vazamentos")
    def leaks(
        record_id: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--ver", help="Mostra um registro ou relato inteiro (o id ou o começo)."),
        ] = None,
        reports: Annotated[
            bool, typer.Option("--relatos", help="Lista os relatos que você marcou (/vazou).")
        ] = False,
        delete: Annotated[
            Optional[str],  # noqa: UP045
            typer.Option("--apagar", help="Apaga um relato (o id ou o começo dele)."),
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
        from brain.memory.leak_audit import LeakReportStore

        store = _open_store(console)
        report_store = LeakReportStore(store.connection)
        try:
            if delete is not None:
                if not report_store.delete(delete.strip().lower()):
                    voice.say(
                        console, "Não achei esse relato (ou o começo do id é ambíguo).", "red"
                    )
                    raise typer.Exit(1)
                voice.say(console, "Relato apagado.")
                return
            if record_id is not None:
                wanted = record_id.strip().lower()
                record = store.get(wanted)
                if record is not None:
                    _show(console, record)
                    return
                report = report_store.get(wanted)
                if report is None:
                    voice.say(
                        console, "Não achei esse registro (ou o começo do id é ambíguo).", "red"
                    )
                    raise typer.Exit(1)
                _show_report(console, report)
                return
            if reports:
                _list_reports(console, report_store.recent(limit=limit))
                return
            if summary:
                data = store.summary(days=days)
                voice.say(
                    console,
                    f"Últimos {days} dia(s): {data['total']} registro(s), {data['leaked']} "
                    f"chegaram a você ({data['quarantined']} fora da memória), "
                    f"{data['blocked']} com corte do guarda.",
                )
                for title, key in (("Regra", "rules"), ("Modelo", "models"), ("Canal", "channels")):
                    table = Table(header_style="bold")
                    table.add_column(title)
                    table.add_column("Vezes", justify="right")
                    for name, count in data[key]:
                        table.add_row(_safe(name), str(count))
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
        for column in ("Id", "Quando", "Canal", "Modelo", "Vazou", "Memória", "Regras", "Entregue"):
            table.add_column(column, overflow="fold")
        for r in records:
            table.add_row(
                _safe(r["_id"][:8]),
                _local(r["at"]),
                _safe(r.get("channel")),
                _safe(r.get("model")),
                "sim" if r.get("leaked") else "não",
                "fora" if r.get("quarantined") else "mantida",
                _safe(", ".join(r.get("rules") or [])),
                _flat(r.get("delivered")),
            )
        console.print(table)
        voice.say(console, "Para ver um caso inteiro: juniper vazamentos --ver <id>")
