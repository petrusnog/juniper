"""Modelos e primitivas das credenciais (ADR-027).

O ``client_secret`` é sorteado (256 bits) e só existe em claro na resposta que o cria. O que se
guarda é ``HMAC-SHA256(pepper, secret)``: o *pepper* mora num arquivo do servidor, fora do banco,
então um vazamento só do MongoDB não deixa nem testar palpites. Um hash rápido basta porque o
segredo não é uma senha escolhida por gente.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Role = Literal["owner", "tester"]
Status = Literal["active", "disabled"]

ROLES: tuple[str, ...] = ("owner", "tester")
CLIENT_ID_PREFIX = "jc_"
SECRET_PREFIX = "jsk_"
_NAME = re.compile(r"^[\w .'-]{1,60}$", re.UNICODE)


def now() -> datetime:
    return datetime.now(UTC)


def generate_client_id() -> str:
    return CLIENT_ID_PREFIX + secrets.token_hex(8)


def generate_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def new_user_id() -> str:
    return secrets.token_hex(8)


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name.strip()))


def load_pepper(path: Path) -> bytes:
    """The server's pepper: read from ``path`` or created (mode 600) on first use."""
    path = path.expanduser()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value.encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    return value.encode()


def hash_secret(pepper: bytes, secret: str) -> str:
    return hmac.new(pepper, secret.encode(), hashlib.sha256).hexdigest()


def hashes_match(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode(), given.encode())


def format_bearer(client_id: str, secret: str) -> str:
    """The value that goes after ``Bearer``: ``client_id.client_secret``."""
    return f"{client_id}.{secret}"


def parse_bearer(value: str | None) -> tuple[str, str] | None:
    """``(client_id, secret)`` from a bearer value, or ``None`` when it does not look like one."""
    if not value:
        return None
    client_id, dot, secret = value.strip().partition(".")
    if not dot or not client_id.startswith(CLIENT_ID_PREFIX) or not secret:
        return None
    return client_id, secret


@dataclass(frozen=True)
class User:
    user_id: str
    name: str
    role: str
    status: str = "active"
    created_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status == "active"

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.user_id,
            "name": self.name,
            "name_key": self.name.strip().casefold(),
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at or now(),
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> User:
        return cls(
            user_id=str(doc["_id"]),
            name=str(doc.get("name", "")),
            role=str(doc.get("role", "tester")),
            status=str(doc.get("status", "active")),
            created_at=doc.get("created_at"),
        )


@dataclass(frozen=True)
class Credential:
    client_id: str
    user_id: str
    secret_hash: str
    label: str = ""
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.client_id,
            "user_id": self.user_id,
            "secret_hash": self.secret_hash,
            "label": self.label,
            "created_at": self.created_at or now(),
            "last_used_at": self.last_used_at,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> Credential:
        return cls(
            client_id=str(doc["_id"]),
            user_id=str(doc.get("user_id", "")),
            secret_hash=str(doc.get("secret_hash", "")),
            label=str(doc.get("label", "")),
            created_at=doc.get("created_at"),
            last_used_at=doc.get("last_used_at"),
            revoked_at=doc.get("revoked_at"),
        )


@dataclass(frozen=True)
class Principal:
    """Who is on the other end of an authenticated connection."""

    user_id: str
    name: str
    role: str
    client_id: str
    label: str = ""
    # Começo do hash do segredo: rotacionar muda isso e derruba as sessões (cookies) da credencial.
    fingerprint: str = ""

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def public(self) -> dict[str, str]:
        return {"id": self.user_id, "name": self.name, "role": self.role}
