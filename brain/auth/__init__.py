"""Usuários e credenciais (ADR-027): quem pode falar com a Juniper e como prova isso."""

from brain.auth.credentials import (
    Credential,
    Principal,
    User,
    format_bearer,
    generate_client_id,
    generate_secret,
    hash_secret,
    parse_bearer,
)
from brain.auth.service import AuthError, AuthResult, AuthService, NewCredential

__all__ = [
    "AuthError",
    "AuthResult",
    "AuthService",
    "Credential",
    "NewCredential",
    "Principal",
    "User",
    "format_bearer",
    "generate_client_id",
    "generate_secret",
    "hash_secret",
    "parse_bearer",
]
