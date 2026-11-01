"""O serviço de identidade (ADR-027): autentica credenciais e cadastra usuários.

Falha de autenticação é resultado (``AuthResult``), nunca exceção; o motivo exato só vai para o
log, e quem chamou mostra sempre a mesma mensagem (não revela se o ``client_id`` existe). Erros de
cadastro (``AuthError``) têm texto seguro para mostrar ao dono.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brain.auth.credentials import (
    ROLES,
    Credential,
    Principal,
    User,
    format_bearer,
    generate_client_id,
    generate_secret,
    hash_secret,
    hashes_match,
    load_pepper,
    new_user_id,
    now,
    parse_bearer,
    valid_name,
)
from brain.auth.limiter import LoginLimiter
from brain.auth.store import UserStore
from brain.config import settings
from brain.eventlog import event_log
from brain.memory.errors import MemoryUnavailableError

LEGACY_CLIENT_ID = "legacy-token"
CACHE_SECONDS = 15.0
DOWN_SECONDS = 30.0
DEFAULT_LABEL = "padrão"


class AuthError(Exception):
    """Um pedido de cadastro que não pode ser atendido; ``str(exc)`` é seguro de mostrar."""


@dataclass(frozen=True)
class AuthResult:
    principal: Principal | None
    # ok | invalid | locked: o motivo real vai para o log, o cliente vê só "recusado".
    reason: str = "invalid"

    @property
    def ok(self) -> bool:
        return self.principal is not None


@dataclass(frozen=True)
class NewCredential:
    """O que sai do cadastro ou da rotação. O ``secret`` não existe em nenhum outro lugar."""

    user: User
    credential: Credential
    secret: str

    @property
    def bearer(self) -> str:
        return format_bearer(self.credential.client_id, self.secret)


class _NoStore(UserStore):
    """A store that is never there: every call reports the database as unavailable."""

    def __init__(self) -> None:
        pass

    def _down(self) -> Any:
        raise MemoryUnavailableError("sem banco de usuários")

    def get_credential(self, client_id: str) -> Any:
        return self._down()

    def get_user(self, user_id: str) -> Any:
        return self._down()

    def list_users(self) -> Any:
        return self._down()

    def list_credentials(self, user_id: str | None = None) -> Any:
        return self._down()


class AuthService:
    def __init__(
        self,
        store: UserStore,
        pepper: bytes,
        *,
        mirror_file: Path | None = None,
        legacy_token: str | None = None,
        allow_testers: bool = False,
        limiter: LoginLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self._pepper = pepper
        self._mirror_file = mirror_file
        self._legacy = legacy_token
        self.allow_testers = allow_testers
        self.limiter = limiter or LoginLimiter()
        self._clock = clock
        self._cache: dict[str, tuple[float, Principal | None]] = {}
        # Banco fora do ar: por um tempo, vai direto ao espelho em vez de esperar o timeout de novo.
        self._down_until = 0.0
        # Um hash de mentira: um client_id desconhecido gasta o mesmo tempo que um conhecido.
        self._decoy = hash_secret(pepper, "decoy")

    @classmethod
    def legacy_only(cls, token: str) -> AuthService:
        """Só o token estático (ADR-007): sem banco, sem usuários. Para quem não tem o serviço."""
        return cls(_NoStore(), token.encode(), legacy_token=token)

    @property
    def pepper(self) -> bytes:
        return self._pepper

    @classmethod
    def from_settings(cls, legacy_token: str | None = None) -> AuthService:
        cfg = settings.deployment.auth
        return cls(
            UserStore(),
            load_pepper(cfg.pepper_file),
            mirror_file=cfg.owner_mirror_file,
            legacy_token=legacy_token if cfg.legacy_token else None,
            allow_testers=cfg.allow_testers,
        )

    # --- authenticate --------------------------------------------------------------------------

    def authenticate(self, bearer: str | None, client: str = "?") -> AuthResult:
        """Who holds this ``Bearer`` value. ``client`` (an address) feeds the attempt limit."""
        if self.limiter.blocked(client):
            event_log.log("auth", action="locked", client=client)
            return AuthResult(None, "locked")
        parsed = parse_bearer(bearer)
        if parsed is None:
            return self._legacy_or_fail(bearer, client)
        client_id, secret = parsed
        if self.limiter.blocked(client_id):
            event_log.log("auth", action="locked", client=client, client_id=client_id)
            return AuthResult(None, "locked")
        principal = self._check(client_id, secret)
        if principal is None:
            self.limiter.failed(client)
            self.limiter.failed(client_id)
            event_log.log("auth", action="failed", client=client, client_id=client_id)
            return AuthResult(None, "invalid")
        self.limiter.succeeded(client)
        self.limiter.succeeded(client_id)
        event_log.log(
            "auth", action="login", client_id=client_id, user_id=principal.user_id, client=client
        )
        return AuthResult(principal, "ok")

    def _legacy_or_fail(self, bearer: str | None, client: str) -> AuthResult:
        given = (bearer or "").strip()
        if self._legacy and given and hmac.compare_digest(given.encode(), self._legacy.encode()):
            event_log.log("auth", action="login", client_id=LEGACY_CLIENT_ID, client=client)
            self.limiter.succeeded(client)
            return AuthResult(self._legacy_principal(), "ok")
        hashes_match(self._decoy, hash_secret(self._pepper, given))
        self.limiter.failed(client)
        event_log.log("auth", action="failed", client=client)
        return AuthResult(None, "invalid")

    def _legacy_principal(self) -> Principal:
        name = "dono"
        try:
            owner = next(
                (u for u in self.store.list_users() if u.role == "owner" and u.active), None
            )
            name = owner.name if owner else name
            user_id = owner.user_id if owner else "owner"
        except MemoryUnavailableError:
            user_id = "owner"
        return Principal(
            user_id, name, "owner", LEGACY_CLIENT_ID, "token estático", self._legacy_fingerprint()
        )

    def _legacy_fingerprint(self) -> str:
        return hash_secret(self._pepper, self._legacy or "")[:12]

    def _check(self, client_id: str, secret: str) -> Principal | None:
        given = hash_secret(self._pepper, secret)
        if self._clock() < self._down_until:
            return self._check_mirror(client_id, given)
        try:
            credential = self.store.get_credential(client_id)
            if credential is None or not hashes_match(credential.secret_hash, given):
                if credential is None:
                    hashes_match(self._decoy, given)
                return None
            if credential.revoked:
                return None
            user = self.store.get_user(credential.user_id)
            if user is None or not user.active:
                return None
            with contextlib.suppress(MemoryUnavailableError):
                self.store.touch(client_id)
            return Principal(
                user.user_id,
                user.name,
                user.role,
                client_id,
                credential.label,
                credential.secret_hash[:12],
            )
        except MemoryUnavailableError:
            self._down_until = self._clock() + DOWN_SECONDS
            event_log.log("auth", action="store_down", client_id=client_id)
            return self._check_mirror(client_id, given)

    def _check_mirror(self, client_id: str, given: str) -> Principal | None:
        """Banco fora do ar: só o dono entra, pelo espelho em arquivo (ADR-027 §5)."""
        mirror = self._read_mirror()
        if mirror is None:
            return None
        for entry in mirror.get("credentials", []):
            if entry.get("client_id") == client_id and hashes_match(
                str(entry.get("secret_hash", "")), given
            ):
                user = mirror.get("user", {})
                return Principal(
                    str(user.get("id", "")),
                    str(user.get("name", "")),
                    "owner",
                    client_id,
                    str(entry.get("label", "")),
                    str(entry.get("secret_hash", ""))[:12],
                )
        return None

    def principal_for(self, client_id: str) -> Principal | None:
        """Whether a logged-in credential is still valid (cookie sessions); cached for 15 s."""
        cached = self._cache.get(client_id)
        if cached and cached[0] > self._clock():
            return cached[1]
        principal = self._current(client_id)
        self._cache[client_id] = (self._clock() + CACHE_SECONDS, principal)
        return principal

    def _current(self, client_id: str) -> Principal | None:
        if client_id == LEGACY_CLIENT_ID:
            return self._legacy_principal() if self._legacy else None
        try:
            if self._clock() < self._down_until:
                raise MemoryUnavailableError("banco fora do ar")
            credential = self.store.get_credential(client_id)
            user = self.store.get_user(credential.user_id) if credential else None
        except MemoryUnavailableError:
            mirror = self._read_mirror() or {}
            for entry in mirror.get("credentials", []):
                if entry.get("client_id") == client_id:
                    u = mirror.get("user", {})
                    return Principal(
                        str(u.get("id", "")),
                        str(u.get("name", "")),
                        "owner",
                        client_id,
                        str(entry.get("label", "")),
                        str(entry.get("secret_hash", ""))[:12],
                    )
            return None
        if credential is None or credential.revoked or user is None or not user.active:
            return None
        return Principal(
            user.user_id,
            user.name,
            user.role,
            client_id,
            credential.label,
            credential.secret_hash[:12],
        )

    # --- users ---------------------------------------------------------------------------------

    def create_user(
        self, name: str, role: str = "tester", label: str = DEFAULT_LABEL
    ) -> NewCredential:
        name = name.strip()
        if not valid_name(name):
            raise AuthError(
                "O nome deve ter de 1 a 60 caracteres (letras, números, espaço, . ' -)."
            )
        if role not in ROLES:
            raise AuthError(f"Papel inválido: use {' ou '.join(ROLES)}.")
        if role == "tester" and not self.allow_testers:
            raise AuthError(
                "Cadastrar `tester` está desligado: ligue deployment.auth.allow_testers. "
                "Atenção: ele enxerga o mesmo cérebro que você (ADR-027)."
            )
        try:
            users = self.store.list_users()
            if role == "owner" and any(u.role == "owner" for u in users):
                raise AuthError("Já existe um dono.")
            if any(u.name.casefold() == name.casefold() for u in users):
                raise AuthError(f"Já existe um usuário chamado {name}.")
            user = User(new_user_id(), name, role, "active", now())
            self.store.add_user(user)
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc
        event_log.log("auth", action="user_added", user_id=user.user_id, role=role)
        return self._issue(user, label)

    def add_credential(self, user_id: str, label: str = DEFAULT_LABEL) -> NewCredential:
        user = self._user(user_id)
        if not user.active:
            raise AuthError("O usuário está desativado.")
        return self._issue(user, label)

    def _issue(self, user: User, label: str) -> NewCredential:
        secret = generate_secret()
        credential = Credential(
            client_id=generate_client_id(),
            user_id=user.user_id,
            secret_hash=hash_secret(self._pepper, secret),
            label=(label or DEFAULT_LABEL).strip()[:60],
            created_at=now(),
        )
        try:
            self.store.add_credential(credential)
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc
        event_log.log(
            "auth",
            action="credential_created",
            user_id=user.user_id,
            client_id=credential.client_id,
        )
        self._changed()
        return NewCredential(user, credential, secret)

    def rotate(self, client_id: str) -> NewCredential:
        """A new secret for the same ``client_id``; the old one stops working at once."""
        credential = self._credential(client_id)
        if credential.revoked:
            raise AuthError("Essa credencial foi revogada; crie outra.")
        user = self._user(credential.user_id)
        secret = generate_secret()
        try:
            self.store.replace_hash(client_id, hash_secret(self._pepper, secret))
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc
        event_log.log("auth", action="rotated", user_id=user.user_id, client_id=client_id)
        self._changed()
        return NewCredential(user, self._credential(client_id), secret)

    def revoke(self, client_id: str) -> None:
        credential = self._credential(client_id)
        user = self._user(credential.user_id)
        if user.role == "owner" and not self._other_owner_credentials(client_id):
            raise AuthError("Essa é a última credencial do dono: crie outra antes de revogá-la.")
        try:
            self.store.revoke(client_id)
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc
        event_log.log("auth", action="revoked", user_id=user.user_id, client_id=client_id)
        self._changed()

    def set_active(self, user_id: str, active: bool) -> User:
        user = self._user(user_id)
        if not active and user.role == "owner":
            raise AuthError("O dono não pode ser desativado.")
        try:
            self.store.set_status(user_id, "active" if active else "disabled")
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc
        event_log.log("auth", action="user_enabled" if active else "user_disabled", user_id=user_id)
        self._changed()
        return self._user(user_id)

    def users(self) -> list[User]:
        try:
            return self.store.list_users()
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc

    def credentials(self, user_id: str | None = None) -> list[Credential]:
        try:
            return self.store.list_credentials(user_id)
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc

    def find_user(self, ref: str) -> User:
        """A user by id or by name."""
        try:
            user = self.store.get_user(ref) or self.store.find_user_by_name(ref)
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc
        if user is None:
            raise AuthError(f"Não achei o usuário {ref}.")
        return user

    # --- helpers -------------------------------------------------------------------------------

    def _user(self, user_id: str) -> User:
        return self.find_user(user_id)

    def _credential(self, client_id: str) -> Credential:
        try:
            credential = self.store.get_credential(client_id)
        except MemoryUnavailableError as exc:
            raise AuthError(str(exc)) from exc
        if credential is None:
            raise AuthError(f"Não achei a credencial {client_id}.")
        return credential

    def _other_owner_credentials(self, client_id: str) -> bool:
        owners = {u.user_id for u in self.users() if u.role == "owner" and u.active}
        return any(
            c.client_id != client_id and c.user_id in owners and not c.revoked
            for c in self.credentials()
        )

    def _changed(self) -> None:
        self._cache.clear()
        self._write_mirror()

    # --- the owner's mirror (ADR-027 §5) -------------------------------------------------------

    def _read_mirror(self) -> dict[str, Any] | None:
        if self._mirror_file is None:
            return None
        try:
            data = json.loads(self._mirror_file.expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write_mirror(self) -> None:
        if self._mirror_file is None:
            return
        try:
            owner = next(
                (u for u in self.store.list_users() if u.role == "owner" and u.active), None
            )
            if owner is None:
                return
            entries = [
                {"client_id": c.client_id, "secret_hash": c.secret_hash, "label": c.label}
                for c in self.store.list_credentials(owner.user_id)
                if not c.revoked
            ]
        except MemoryUnavailableError:
            return
        path = self._mirror_file.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"user": {"id": owner.user_id, "name": owner.name}, "credentials": entries}
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
