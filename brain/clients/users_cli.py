"""``juniper users``: cadastro de usuários e credenciais (ADR-027).

Um comando administrativo: roda onde o servidor está (na VPS, dentro do contêiner) e fala com o
mesmo MongoDB do cérebro. O ``client_secret`` sai uma vez só, aqui, e nunca mais.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from brain.auth import AuthError, AuthService, NewCredential
from brain.clients import voice
from brain.config import settings


def _service() -> AuthService:
    return AuthService.from_settings()


def _when(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d/%m/%Y %H:%M") if moment else "—"


def show_secret(console: Console, new: NewCredential) -> None:
    """The one place a secret is printed. Plain text, outside any log."""
    voice.say(
        console,
        f"Usuário [bold]{new.user.name}[/bold] ({new.user.role}), "
        f"credencial «{new.credential.label}».\n"
        f"  client_id:     [green]{new.credential.client_id}[/green]\n"
        f"  client_secret: [green]{new.secret}[/green]\n"
        "[yellow]Guarde o client_secret agora: ele não aparece de novo[/yellow] "
        "(se perder, gere outro com `juniper users credentials rotate`).",
    )


def install(app: typer.Typer, console: Console) -> None:
    install_login(app, console)
    users_app = typer.Typer(
        help="Usuários e credenciais (client_id e client_secret).", no_args_is_help=True
    )
    app.add_typer(users_app, name="users")
    creds_app = typer.Typer(help="Credenciais de um usuário.", no_args_is_help=True)
    users_app.add_typer(creds_app, name="credentials")

    def fail(exc: AuthError) -> None:
        voice.say(console, str(exc), "red")
        raise typer.Exit(1) from None

    @users_app.command("add")
    def add(
        name: Annotated[str, typer.Argument(help="O nome do usuário.")],
        role: Annotated[str, typer.Option("--role", "-r", help="owner ou tester.")] = "tester",
        label: Annotated[
            str, typer.Option("--label", "-l", help="Para que dispositivo.")
        ] = "padrão",
    ) -> None:
        """Cadastra um usuário e mostra o client_id e o client_secret (uma vez)."""
        try:
            show_secret(console, _service().create_user(name, role, label))
        except AuthError as exc:
            fail(exc)

    @users_app.command("list")
    def list_users() -> None:
        """Lista os usuários e quantas credenciais ativas cada um tem."""
        try:
            service = _service()
            users, credentials = service.users(), service.credentials()
        except AuthError as exc:
            fail(exc)
            return
        if not users:
            voice.say(
                console, "Nenhum usuário. Cadastre o dono: `juniper users add Nome --role owner`."
            )
            return
        table = Table("Id", "Nome", "Papel", "Estado", "Credenciais", "Criado em")
        table.columns[0].no_wrap = True
        for user in users:
            active = sum(1 for c in credentials if c.user_id == user.user_id and not c.revoked)
            table.add_row(
                user.user_id, user.name, user.role, user.status, str(active), _when(user.created_at)
            )
        console.print(table)

    @users_app.command("disable")
    def disable(ref: Annotated[str, typer.Argument(help="Id ou nome.")]) -> None:
        """Desativa o usuário: nenhuma credencial dele entra."""
        try:
            service = _service()
            user = service.set_active(service.find_user(ref).user_id, False)
        except AuthError as exc:
            fail(exc)
            return
        voice.say(console, f"{user.name} desativado.")

    @users_app.command("enable")
    def enable(ref: Annotated[str, typer.Argument(help="Id ou nome.")]) -> None:
        """Reativa o usuário."""
        try:
            service = _service()
            user = service.set_active(service.find_user(ref).user_id, True)
        except AuthError as exc:
            fail(exc)
            return
        voice.say(console, f"{user.name} reativado.")

    @creds_app.command("add")
    def credential_add(
        ref: Annotated[str, typer.Argument(help="Id ou nome do usuário.")],
        label: Annotated[
            str, typer.Option("--label", "-l", help="Para que dispositivo.")
        ] = "padrão",
    ) -> None:
        """Gera outra credencial (por exemplo, para um aparelho novo)."""
        try:
            service = _service()
            show_secret(console, service.add_credential(service.find_user(ref).user_id, label))
        except AuthError as exc:
            fail(exc)

    @creds_app.command("list")
    def credential_list(
        ref: Annotated[str | None, typer.Argument(help="Id ou nome (todos, se omitido).")] = None,
    ) -> None:
        """Lista as credenciais (nunca o segredo): dispositivo, criação, último uso e estado."""
        try:
            service = _service()
            user_id = service.find_user(ref).user_id if ref else None
            credentials = service.credentials(user_id)
            names = {u.user_id: u.name for u in service.users()}
        except AuthError as exc:
            fail(exc)
            return
        table = Table("client_id", "Usuário", "Dispositivo", "Criada", "Último uso", "Estado")
        table.columns[0].no_wrap = True
        for c in credentials:
            table.add_row(
                c.client_id,
                names.get(c.user_id, "?"),
                c.label,
                _when(c.created_at),
                _when(c.last_used_at),
                "revogada" if c.revoked else "ativa",
            )
        console.print(table)

    @creds_app.command("rotate")
    def credential_rotate(client_id: Annotated[str, typer.Argument(help="O client_id.")]) -> None:
        """Gera um client_secret novo; o antigo deixa de valer na hora."""
        try:
            show_secret(console, _service().rotate(client_id))
        except AuthError as exc:
            fail(exc)

    @creds_app.command("revoke")
    def credential_revoke(client_id: Annotated[str, typer.Argument(help="O client_id.")]) -> None:
        """Revoga a credencial (um aparelho perdido)."""
        try:
            _service().revoke(client_id)
        except AuthError as exc:
            fail(exc)
            return
        voice.say(console, f"{client_id} revogada.")


def install_login(app: typer.Typer, console: Console) -> None:
    """``juniper login`` e ``juniper logout``: a credencial deste computador (ADR-027)."""

    @app.command("login")
    def login(
        client_id: Annotated[str, typer.Option(prompt="client_id", help="O client_id.")],
        client_secret: Annotated[
            str, typer.Option(prompt="client_secret", hide_input=True, help="O client_secret.")
        ],
    ) -> None:
        """Guarda a credencial deste computador (arquivo modo 600) para falar com o servidor."""
        from brain.auth import format_bearer, parse_bearer

        bearer = format_bearer(client_id.strip(), client_secret.strip())
        if parse_bearer(bearer) is None:
            voice.say(console, "Isso não parece um client_id/client_secret da Juniper.", "red")
            raise typer.Exit(1)
        path = settings.deployment.auth.credential_file.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(bearer + "\n")
        voice.say(console, f"Credencial guardada em {path}.")

    @app.command("logout")
    def logout() -> None:
        """Apaga a credencial guardada neste computador."""
        path = settings.deployment.auth.credential_file.expanduser()
        if path.is_file():
            path.unlink()
            voice.say(console, "Credencial apagada deste computador.")
        else:
            voice.say(console, "Não havia credencial guardada aqui.")
