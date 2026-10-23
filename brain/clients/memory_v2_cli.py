"""``juniper memory beliefs`` and ``journal``: seeing and deleting what she concluded.

Administrative commands like ``memory reindex``: they read the database of the process where they
run (on the VPS, through the container shortcut) and change nothing in the protocol. Without the
Memória V2 they say so instead of failing (ADR-017, principle 7: everything she concludes is
visible, reversible and deletable).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from brain.clients import voice
from brain.config import settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.v2 import open_v2_data, require_v2
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.beliefs import HELD, BeliefBook
from brain.memory.v2.concerns import ConcernBook, ConcernError
from brain.memory.v2.context import state_lines
from brain.memory.v2.models import Belief
from brain.memory.v2.store import V2Data

HYPOTHESIS_NOTE = "Lembre: são hipóteses minhas, não fatos. Posso estar errada."


def _local(moment: datetime) -> str:
    return f"{moment.astimezone(ZoneInfo(settings.agent.timezone)):%d/%m/%Y %H:%M}"


def _open(console: Console) -> V2Data:
    """The V2 data, or exit 1 saying why it cannot be used (off, or no MongoDB)."""
    try:
        require_v2()
        return open_v2_data()
    except MemoryUnavailableError as exc:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None


def _provenance(belief: Belief) -> str:
    sessions = {e.session_id for e in belief.evidence_for if e.session_id}
    return (
        f"{len(belief.evidence_for)} a favor em {len(sessions)} conversa(s), "
        f"{len(belief.evidence_against)} contra"
    )


def install(memory_app: typer.Typer, console: Console) -> None:
    """Register the commands on ``juniper memory``."""
    beliefs_app = typer.Typer(
        help="O que eu acho de você (hipóteses, não fatos).", invoke_without_command=True
    )
    memory_app.add_typer(beliefs_app, name="beliefs")

    @beliefs_app.callback()
    def beliefs(
        ctx: typer.Context,
        include_all: Annotated[
            bool,
            typer.Option("--all", "-a", help="Inclui candidatas e aposentadas (auditoria)."),
        ] = False,
        min_confidence: Annotated[
            Optional[float],  # noqa: UP045
            typer.Option("--min-confidence", min=0.0, max=1.0, help="Só com esta confiança."),
        ] = None,
    ) -> None:
        """Lista as crenças com a faixa em palavras, o número por baixo, a firmeza e a origem."""
        if ctx.invoked_subcommand is not None:
            return
        data = _open(console)
        try:
            book = BeliefBook(data)
            rows = data.beliefs.list(None if include_all else {"status": {"$in": list(HELD)}})
        finally:
            data.close()
        if min_confidence is not None:
            rows = [b for b in rows if book.confidence_of(b) >= min_confidence]
        rows.sort(key=lambda b: -book.confidence_of(b))
        if not rows:
            voice.say(console, "Ainda não formei nenhuma opinião sobre você.")
            return
        table = Table(header_style="bold")
        for column in ("Id", "Faixa", "Conf.", "Firmeza", "Status", "Origem", "Crença"):
            table.add_column(column, overflow="fold")
        for b in rows:
            table.add_row(
                b.id,
                book.words(b),
                f"{book.confidence_of(b):.2f}",
                f"{b.n:.1f}",
                b.status,
                _provenance(b),
                b.statement,
            )
        console.print(table)
        voice.say(
            console,
            f"{len(rows)} crença(s). {HYPOTHESIS_NOTE} Para ver as evidências: "
            "juniper memory beliefs show <id>. Para apagar: juniper memory beliefs forget <id>",
        )

    @beliefs_app.command("show")
    def beliefs_show(
        belief_id: Annotated[str, typer.Argument(help="Id da crença (veja `memory beliefs`).")],
    ) -> None:
        """Mostra uma crença com as evidências a favor e contra (só referências, não o texto)."""
        data = _open(console)
        try:
            belief = data.beliefs.get(belief_id.strip())
            if belief is None:
                voice.say(console, f"Não achei a crença {belief_id}.", "red")
                raise typer.Exit(1)
            book = BeliefBook(data)
            words, number = book.words(belief), book.confidence_of(belief)
        finally:
            data.close()
        voice.say(console, f"{words.capitalize()}: {belief.statement}")
        console.print(
            f"[dim]confiança {number:.2f} · firmeza {belief.n:.1f} · tema {belief.topic} · "
            f"status {belief.status} · criada em {_local(belief.created_at)}[/dim]"
        )
        if belief.user_confirmed:
            console.print(f"[dim]Você {_confirmation(belief.user_confirmed)}.[/dim]")
        for title, items in (
            ("A favor", belief.evidence_for),
            ("Contra", belief.evidence_against),
        ):
            console.print(f"[bold]{title}[/bold] ({len(items)})")
            for e in items:
                where = f"conversa {e.session_id}" if e.session_id else "sem conversa"
                turn = f", turno {e.turn}" if e.turn is not None else ""
                console.print(f"  - {e.kind} (peso {e.weight:g}) · {where}{turn} · {_local(e.at)}")
        voice.say(console, HYPOTHESIS_NOTE)

    @beliefs_app.command("forget")
    def beliefs_forget(
        belief_id: Annotated[str, typer.Argument(help="Id da crença (veja `memory beliefs`).")],
        yes: Annotated[
            bool, typer.Option("--yes", "-y", help="Não pergunta antes de apagar.")
        ] = False,
    ) -> None:
        """Apaga uma crença para sempre. Os fatos que a sustentaram não são tocados."""
        data = _open(console)
        try:
            belief = data.beliefs.get(belief_id.strip())
            if belief is None:
                voice.say(console, f"Não achei a crença {belief_id}.", "red")
                raise typer.Exit(1)
            voice.say(console, f"Vou apagar para sempre esta opinião minha: «{belief.statement}»")
            if not yes and not typer.confirm("Pode apagar?", default=False):
                voice.say(console, "Ok, não apaguei nada.")
                return
            BeliefBook(data).forget(belief.id)
        finally:
            data.close()
        voice.say(console, "Esqueci essa opinião. Os fatos que você me contou continuam guardados.")

    concerns_app = typer.Typer(
        help="O que importa para mim (a régua dos meus sentimentos).", invoke_without_command=True
    )
    memory_app.add_typer(concerns_app, name="concerns")

    @concerns_app.callback()
    def concerns(ctx: typer.Context) -> None:
        """Lista as preocupações e os pesos (de 0 a 1). Subcomandos: show, set."""
        if ctx.invoked_subcommand is not None:
            return
        data = _open(console)
        try:
            rows = ConcernBook(data).ensure_defaults()
        finally:
            data.close()
        table = Table(header_style="bold")
        for column in ("Id", "Peso", "Ponto de partida", "O que é"):
            table.add_column(column, overflow="fold")
        for c in rows:
            table.add_row(c.id, f"{c.weight:.2f}", f"{c.baseline_weight:.2f}", c.name)
        console.print(table)
        voice.say(
            console,
            "Isso é o que importa para mim, uma escolha e não uma estimativa. Você muda um peso "
            "com `juniper memory concerns set <id> <0 a 1>`; sozinha eu só mexo muito devagar.",
        )

    @concerns_app.command("show")
    def concerns_show(
        concern_id: Annotated[str, typer.Argument(help="Id da preocupação.")],
    ) -> None:
        """Mostra uma preocupação e o histórico de mudanças, com o evento de cada uma."""
        data = _open(console)
        try:
            book = ConcernBook(data)
            book.ensure_defaults()
            concern = book.get(concern_id.strip())
        finally:
            data.close()
        if concern is None:
            voice.say(console, f"Não conheço a preocupação {concern_id}.", "red")
            raise typer.Exit(1)
        voice.say(console, f"{concern.name}: peso {concern.weight:.2f}")
        console.print(f"[dim]{concern.description}[/dim]")
        console.print(f"[dim]ponto de partida {concern.baseline_weight:.2f}[/dim]")
        if not concern.history:
            console.print("Nunca mudou.")
        for c in concern.history:
            who = "você" if c.source == "petrus" else "passada noturna"
            event = f" · evento {c.event_ref}" if c.event_ref else ""
            console.print(f"  - {_local(c.at)} · {c.delta:+.2f} · {who} · {c.reason}{event}")

    @concerns_app.command("set")
    def concerns_set(
        concern_id: Annotated[str, typer.Argument(help="Id da preocupação.")],
        weight: Annotated[float, typer.Argument(help="Novo peso, de 0 a 1.")],
        reason: Annotated[
            str, typer.Option("--reason", "-r", help="Por que mudou (fica no histórico).")
        ] = "",
    ) -> None:
        """Muda o peso de uma preocupação. Você pode mexer livremente; fica registrado."""
        data = _open(console)
        try:
            book = ConcernBook(data)
            book.ensure_defaults()
            try:
                concern = book.set_weight(concern_id.strip(), weight, reason)
            except ConcernError as exc:
                voice.say(console, str(exc), "red")
                raise typer.Exit(1) from None
        finally:
            data.close()
        voice.say(console, f"Anotado: {concern.name} agora pesa {concern.weight:.2f}.")

    affect_app = typer.Typer(
        help="Como eu estou: humor, sentimentos e mágoas.", invoke_without_command=True
    )
    memory_app.add_typer(affect_app, name="affect")

    @affect_app.callback()
    def affect(ctx: typer.Context) -> None:
        """Mostra o estado afetivo de agora e os sentimentos recentes. Subcomando: clear."""
        if ctx.invoked_subcommand is not None:
            return
        data = _open(console)
        try:
            book = AffectBook(data)
            state = book.state()
            rows = sorted(data.affects.list(), key=lambda a: a.occurred_at, reverse=True)
            strengths = {a.id: book.strength(a) for a in rows}
            active = book.active
        finally:
            data.close()
        if not active:
            voice.say(
                console, "Os sentimentos estão desligados (memory.v2.affect.enabled / intensity)."
            )
            return
        voice.say(console, "Agora: " + (" ".join(state_lines(state)) or "tudo calmo."))
        if not rows:
            return
        table = Table(header_style="bold")
        for column in ("Quando", "Sentimento", "Valência", "Registrada", "Agora", "Sobre", "Causa"):
            table.add_column(column, overflow="fold")
        for a in rows[:20]:
            now = "desfeita" if a.resolved_at else f"{strengths[a.id]:.2f}"
            kind = f"{a.kind} (mágoa)" if a.persistence == "grievance" else a.kind
            table.add_row(
                _local(a.occurred_at),
                kind,
                f"{a.valence:+.2f}",
                f"{a.intensity:.2f}",
                now,
                a.about,
                ", ".join(a.cause),
            )
        console.print(table)

    @affect_app.command("clear")
    def affect_clear(
        yes: Annotated[
            bool, typer.Option("--yes", "-y", help="Não pergunta antes de apagar.")
        ] = False,
    ) -> None:
        """Apaga todos os sentimentos guardados (o humor volta ao neutro)."""
        if not yes and not typer.confirm(
            "Apagar todos os meus sentimentos guardados?", default=False
        ):
            voice.say(console, "Ok, não apaguei nada.")
            return
        data = _open(console)
        try:
            removed = AffectBook(data).forget_all()
        finally:
            data.close()
        voice.say(console, f"Pronto, esqueci {removed} sentimento(s).")

    @memory_app.command("journal")
    def journal(
        days: Annotated[
            int, typer.Option("--days", "-d", min=1, max=365, help="Quantos dias mostrar.")
        ] = 7,
    ) -> None:
        """Mostra o diário da Juniper: o que mudou nas noites e o que ela estudou."""
        data = _open(console)
        try:
            since = (datetime.now(UTC) - timedelta(days=days)).astimezone(
                ZoneInfo(settings.agent.timezone)
            )
            entries = [
                e for e in data.journal.list(sort=[("at", 1)]) if e.day >= f"{since:%Y-%m-%d}"
            ]
        finally:
            data.close()
        if not entries:
            voice.say(console, f"O diário está vazio nos últimos {days} dia(s).")
            return
        current = ""
        for entry in entries:
            if entry.day != current:
                current = entry.day
                console.print(f"[bold]{current}[/bold]")
            console.print(f"  - [{entry.kind}] {entry.text}")
        voice.say(console, f"{len(entries)} anotação(ões) em {days} dia(s).")


def _confirmation(value: str) -> str:
    return {"confirmed": "confirmou isto", "denied": "negou isto"}.get(value, value)
