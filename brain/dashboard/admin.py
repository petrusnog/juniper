"""Usuários e credenciais no dashboard (ADR-027 §10): as únicas rotas de escrita.

Só o dono entra aqui. Os parâmetros vêm na *query* do ``POST`` (o servidor HTTP do dashboard lê
cabeçalhos, não corpo) e nenhum é segredo. Quem chama já conferiu o login, o token anti-CSRF e a
origem. Ações destrutivas (desativar, rotacionar, revogar) pedem ``confirm=`` igual ao nome ou ao
``client_id``: a confirmação é conferida aqui, não só na tela. O único segredo que sai é o
``client_secret`` de um cadastro ou de uma rotação, devolvido à parte (``reveal``) porque o
mascaramento do dashboard o apagaria.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from brain.auth import AuthError, AuthService, NewCredential, Principal
from brain.eventlog import event_log

Query = Mapping[str, list[str]]
Result = tuple[int, dict[str, Any], dict[str, Any] | None]


def _one(query: Query, name: str, default: str = "") -> str:
    values = query.get(name)
    return values[0].strip() if values else default


class AdminApi:
    def __init__(self, auth: AuthService) -> None:
        self._auth = auth

    @staticmethod
    def handles(path: str) -> bool:
        return path == "/api/users" or path.startswith(("/api/users/", "/api/credentials/"))

    async def handle(self, method: str, path: str, query: Query, who: Principal) -> Result:
        if not who.is_owner:
            return 403, {"error": "só o dono administra usuários"}, None
        try:
            return await asyncio.to_thread(self._dispatch, method, path, query, who)
        except AuthError as exc:
            return 400, {"error": str(exc)}, None

    # --- dispatch (blocking: runs in a thread) --------------------------------------------------

    def _dispatch(self, method: str, path: str, query: Query, who: Principal) -> Result:
        parts = path.removeprefix("/api/").split("/")
        if method == "GET" and parts == ["users"]:
            return 200, self._listing(), None
        if method != "POST":
            return 405, {"error": "método não permitido"}, None
        if parts == ["users"]:
            return self._create(query, who)
        if len(parts) == 3 and parts[0] == "users" and parts[2] in ("disable", "enable"):
            return self._set_active(parts[1], parts[2] == "enable", query, who)
        if len(parts) == 3 and parts[0] == "users" and parts[2] == "credentials":
            user = self._auth.find_user(parts[1])
            new = self._auth.add_credential(user.user_id, _one(query, "label", "padrão"))
            return self._issued(new, who, "credential_created")
        if len(parts) == 3 and parts[0] == "credentials" and parts[2] in ("rotate", "revoke"):
            return self._credential_op(parts[1], parts[2], query, who)
        return 404, {"error": "não existe", "path": path}, None

    def _listing(self) -> dict[str, Any]:
        users, credentials = self._auth.users(), self._auth.credentials()
        return {
            "allow_testers": self._auth.allow_testers,
            "users": [
                {
                    "id": u.user_id,
                    "name": u.name,
                    "role": u.role,
                    "status": u.status,
                    "created_at": u.created_at,
                    "credentials": [
                        {
                            "client_id": c.client_id,
                            "label": c.label,
                            "created_at": c.created_at,
                            "last_used_at": c.last_used_at,
                            "revoked": c.revoked,
                        }
                        for c in credentials
                        if c.user_id == u.user_id
                    ],
                }
                for u in users
            ],
        }

    def _create(self, query: Query, who: Principal) -> Result:
        new = self._auth.create_user(
            _one(query, "name"), _one(query, "role", "tester"), _one(query, "label", "padrão")
        )
        return self._issued(new, who, "user_created")

    def _set_active(self, ref: str, active: bool, query: Query, who: Principal) -> Result:
        user = self._auth.find_user(ref)
        if not active:
            self._confirm(query, user.name)
        updated = self._auth.set_active(user.user_id, active)
        self._audit(who, "user_enabled" if active else "user_disabled", user.user_id)
        return 200, {"ok": True, "id": updated.user_id, "status": updated.status}, None

    def _credential_op(self, client_id: str, op: str, query: Query, who: Principal) -> Result:
        self._confirm(query, client_id)
        if op == "revoke":
            self._auth.revoke(client_id)
            self._audit(who, "credential_revoked", client_id)
            return 200, {"ok": True, "client_id": client_id}, None
        return self._issued(self._auth.rotate(client_id), who, "credential_rotated")

    # --- helpers ---------------------------------------------------------------------------------

    @staticmethod
    def _confirm(query: Query, expected: str) -> None:
        if _one(query, "confirm") != expected:
            raise AuthError(f"Confirme digitando «{expected}».")

    def _issued(self, new: NewCredential, who: Principal, action: str) -> Result:
        self._audit(who, action, new.credential.client_id)
        body = {
            "ok": True,
            "user": {"id": new.user.user_id, "name": new.user.name, "role": new.user.role},
            "credential": {"client_id": new.credential.client_id, "label": new.credential.label},
            "warning": "Guarde o client_secret agora: ele não aparece de novo.",
        }
        return 200, body, {"client_secret": new.secret}

    @staticmethod
    def _audit(who: Principal, action: str, target: str) -> None:
        event_log.log("dashboard", action="admin", op=action, actor=who.user_id, target=target)
