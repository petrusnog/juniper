"""Onde ficam usuários e credenciais: o MongoDB (ADR-027 §5), atrás do ``guard`` do projeto."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from brain.auth.credentials import Credential, User, now
from brain.memory.mongo import MongoConnection

USERS_COLLECTION = "auth_users"
CREDENTIALS_COLLECTION = "auth_credentials"


class UserStore:
    def __init__(self, connection: MongoConnection | None = None) -> None:
        self.connection = connection if connection is not None else MongoConnection()

    def _users(self) -> Any:
        return self.connection.collection(USERS_COLLECTION)

    def _credentials(self) -> Any:
        return self.connection.collection(CREDENTIALS_COLLECTION)

    # --- users ---------------------------------------------------------------------------------

    def add_user(self, user: User) -> None:
        with self.connection.guard():
            self._users().insert_one(user.to_document())

    def get_user(self, user_id: str) -> User | None:
        with self.connection.guard():
            doc = self._users().find_one({"_id": user_id})
        return User.from_document(doc) if doc else None

    def find_user_by_name(self, name: str) -> User | None:
        with self.connection.guard():
            doc = self._users().find_one({"name_key": name.strip().casefold()})
        return User.from_document(doc) if doc else None

    def list_users(self) -> list[User]:
        with self.connection.guard():
            docs = list(self._users().find({}).sort("created_at", 1))
        return [User.from_document(d) for d in docs]

    def set_status(self, user_id: str, status: str) -> bool:
        with self.connection.guard():
            result = self._users().update_one({"_id": user_id}, {"$set": {"status": status}})
        return bool(result.matched_count)

    # --- credentials ---------------------------------------------------------------------------

    def add_credential(self, credential: Credential) -> None:
        with self.connection.guard():
            self._credentials().insert_one(credential.to_document())

    def get_credential(self, client_id: str) -> Credential | None:
        with self.connection.guard():
            doc = self._credentials().find_one({"_id": client_id})
        return Credential.from_document(doc) if doc else None

    def list_credentials(self, user_id: str | None = None) -> list[Credential]:
        query = {"user_id": user_id} if user_id else {}
        with self.connection.guard():
            docs = list(self._credentials().find(query).sort("created_at", 1))
        return [Credential.from_document(d) for d in docs]

    def replace_hash(self, client_id: str, secret_hash: str) -> bool:
        with self.connection.guard():
            result = self._credentials().update_one(
                {"_id": client_id, "revoked_at": None},
                {"$set": {"secret_hash": secret_hash, "created_at": now()}},
            )
        return bool(result.matched_count)

    def revoke(self, client_id: str, when: datetime | None = None) -> bool:
        with self.connection.guard():
            result = self._credentials().update_one(
                {"_id": client_id, "revoked_at": None}, {"$set": {"revoked_at": when or now()}}
            )
        return bool(result.matched_count)

    def touch(self, client_id: str) -> None:
        with self.connection.guard():
            self._credentials().update_one({"_id": client_id}, {"$set": {"last_used_at": now()}})
