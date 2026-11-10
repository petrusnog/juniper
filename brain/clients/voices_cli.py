"""``juniper voices``: the voices she is allowed to recognise (T-213, ADR-034 section 3).

An administrative command: it runs where the server is and talks to the same MongoDB. A voice is
only registered with the person's consent, from a few recordings (WAV, mono, 16 bits) that are
read, turned into a vector and not kept. Removing is immediate and complete.
"""

from __future__ import annotations

import getpass
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from brain.auth.store import UserStore
from brain.clients import voice
from brain.voice.errors import VoiceError
from brain.voice.evaluation import EvalError, read_audio
from brain.voice.speaker import EnrollError, SpeakerIdentifier, enroll, open_identifier

SAMPLE_RATE = 16_000
CONSENT_TEXT = (
    "Vou guardar um «vetor de voz» (números que descrevem a sua voz, cifrados) para a Juniper "
    "reconhecer quando é você que fala. As gravações não ficam guardadas, e você pode pedir para "
    "apagar a qualquer momento."
)


def _identifier() -> SpeakerIdentifier:
    return open_identifier()


def _users() -> UserStore:
    return UserStore()


def _when(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d/%m/%Y %H:%M") if moment else "—"


def install(app: typer.Typer, console: Console) -> None:
    voices_app = typer.Typer(
        help="Vozes que a Juniper reconhece (cadastro, lista, teste e exclusão).",
        no_args_is_help=True,
    )
    app.add_typer(voices_app, name="voices")

    def fail(message: str) -> None:
        voice.say(console, message, "red")
        raise typer.Exit(1) from None

    def open_ident() -> SpeakerIdentifier:
        try:
            return _identifier()
        except VoiceError as exc:
            fail(str(exc))
            raise  # unreachable: fail() exits

    @voices_app.command("add")
    def add(
        name: Annotated[str, typer.Argument(help="O usuário (já cadastrado em `juniper users`).")],
        wav: Annotated[
            list[Path],
            typer.Option("--wav", "-w", help="Uma gravação da pessoa falando (3 ou mais)."),
        ],
        label: Annotated[str, typer.Option("--label", "-l", help="Ex.: normal, rouca.")] = "normal",
        consent: Annotated[
            bool,
            typer.Option("--consent", help="A pessoa concordou com o cadastro da própria voz."),
        ] = False,
    ) -> None:
        """Cadastra a voz de um usuário a partir de gravações dele."""
        user = _users().find_user_by_name(name)
        if user is None:
            fail(f"Usuário «{name}» não existe. Use `juniper users add`.")
            return
        if not consent:
            voice.say(console, CONSENT_TEXT)
            consent = typer.confirm(f"{user.name} concorda?", default=False)
        samples: list[tuple[bytes, int]] = []
        for path in wav:
            try:
                samples.append((read_audio(path.expanduser()), SAMPLE_RATE))
            except EvalError as exc:
                fail(str(exc))
        identifier = open_ident()
        try:
            created = enroll(
                identifier,
                user_id=user.user_id,
                label=label,
                samples=samples,
                consent=consent,
                consent_by=getpass.getuser(),
            )
        except EnrollError as exc:
            fail(str(exc))
            return
        voice.say(
            console,
            f"Voz de {user.name} cadastrada («{created.label}», "
            f"{created.samples} amostras, id {created.voiceprint_id}).",
        )

    @voices_app.command("list")
    def list_voices() -> None:
        """Lista as vozes cadastradas (nunca mostra o vetor)."""
        try:
            rows = open_ident().store.info()
        except Exception as exc:  # noqa: BLE001 - a database that is down is a sentence
            fail(f"Não consegui ler o cadastro de vozes ({type(exc).__name__}).")
            return
        if not rows:
            voice.say(console, "Nenhuma voz cadastrada.")
            return
        users = {u.user_id: u.name for u in _users().list_users()}
        table = Table(show_header=True, header_style="bold")
        for column in ("id", "usuário", "rótulo", "amostras", "modelo", "aceitou", "criada"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                row["_id"],
                users.get(row["user_id"], row["user_id"]),
                row.get("label", ""),
                str(row.get("samples", "")),
                row.get("model_id", ""),
                f"{row.get('consent_by', '')} · {_when(row.get('consent_at'))}",
                _when(row.get("created_at")),
            )
        console.print(table)

    @voices_app.command("remove")
    def remove(
        voiceprint_id: Annotated[str, typer.Argument(help="O id (veja em `juniper voices list`).")],
    ) -> None:
        """Apaga uma voz cadastrada, na hora."""
        ident = open_ident()
        if not ident.store.remove(voiceprint_id):
            fail(f"Não achei a voz {voiceprint_id}.")
        ident.invalidate()
        voice.say(console, f"Voz {voiceprint_id} apagada.")

    @voices_app.command("forget")
    def forget(
        name: Annotated[str, typer.Argument(help="O usuário cujas vozes serão todas apagadas.")],
        yes: Annotated[bool, typer.Option("--yes", "-y", help="Não perguntar.")] = False,
    ) -> None:
        """Apaga todas as vozes de um usuário, na hora."""
        user = _users().find_user_by_name(name)
        if user is None:
            fail(f"Não achei o usuário «{name}».")
            return
        if not yes and not typer.confirm(f"Apagar todas as vozes de {user.name}?", default=False):
            voice.say(console, "Nada apagado.")
            return
        ident = open_ident()
        count = ident.store.remove_user(user.user_id)
        ident.invalidate()
        voice.say(console, f"{count} voz(es) de {user.name} apagada(s).")

    @voices_app.command("test")
    def test(
        wav: Annotated[Path, typer.Argument(help="Uma gravação para ver de quem ela soa.")],
    ) -> None:
        """Diz de quem soa uma gravação (ajuda a calibrar `voice.speaker.threshold`)."""
        try:
            pcm = read_audio(wav.expanduser())
        except EvalError as exc:
            fail(str(exc))
            return
        try:
            heard = open_ident().identify(pcm, SAMPLE_RATE)
        except VoiceError as exc:
            fail(str(exc))
            return
        if not heard.enrolled:
            voice.say(console, "Ninguém tem voz cadastrada ainda.")
            return
        users = {u.user_id: u.name for u in _users().list_users()}
        who = users.get(heard.user_id or "", "ninguém conhecido")
        lead = "—" if heard.margin is None else f"{heard.margin:.2f}"
        voice.say(
            console,
            f"Soa como: {who} · semelhança {heard.score:.2f} · vantagem {lead}",
        )
