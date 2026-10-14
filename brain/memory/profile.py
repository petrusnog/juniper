"""The user's profile: a short identity paragraph present in EVERY prompt (ADR-011, T-061).

Long-term memories are found by relevance, so "bom dia" would never bring back the user's name.
The profile is the exception: one paragraph (``memory.profile.max_chars``, 500 by default) with
who the user is, injected on every message without a search. That is why its size is capped: it
is paid for in tokens on every question.

The paragraph never carries the age. It holds ``{idade}`` and the age is computed from the birth
date when the prompt is built, so "27 anos" does not go stale. The structured ``fields`` (name,
birth date, nationality, gender) are what the age is computed from.

Only the user's own words may change it (the extractor reads user messages, never tool output),
and nothing that looks like a secret, an order to an AI, or an identity document is accepted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol, runtime_checkable

from brain.core import clock
from brain.memory.secretfilter import find_document, find_order, find_secret

AGE_MARKER = "{idade}"
PROFILE_ID = "user"
PROFILE_COLLECTION = "profile"

# What the profile may hold besides the paragraph, and how long each value may be.
FIELD_LIMITS: dict[str, int] = {"name": 100, "nationality": 60, "gender": 30}
FIELDS = ("name", "birth_date", "nationality", "gender")

# "de 27 anos", "tem 27 anos": an age stated as a fact about the person.
_AGE_PHRASE = re.compile(r"\b(de|com|tem|tinha)(\s+)(\d{1,3})(\s+anos)\b", re.I)
# The clause that carries the marker, for when there is no birth date to compute it from.
_AGE_CLAUSE = re.compile(r",?\s*[^,.;]*" + re.escape(AGE_MARKER) + r"[^,.;]*")


class ProfileError(ValueError):
    """The profile text or fields are not acceptable (the message says why, in Portuguese)."""


@dataclass
class Profile:
    text: str
    fields: dict[str, str] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def birth_date(self) -> date | None:
        raw = self.fields.get("birth_date")
        return date.fromisoformat(raw) if raw else None


@runtime_checkable
class ProfileStore(Protocol):
    def get(self) -> Profile | None: ...

    def set(self, profile: Profile) -> None: ...

    def clear(self) -> bool:
        """Delete the profile for good. False if there was none."""
        ...


def age_on(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def today() -> date:
    return clock.now().date()


def render(profile: Profile, on: date | None = None) -> str:
    """The paragraph as the LLM should read it: the age computed for ``on`` (default: today)."""
    text = profile.text
    birth = profile.birth_date
    if AGE_MARKER not in text:
        return text
    if birth is None:
        return _AGE_CLAUSE.sub("", text).strip(" ,") or text.replace(AGE_MARKER, "")
    return text.replace(AGE_MARKER, str(age_on(birth, on or today())))


def check_fields(raw: Mapping[str, Any]) -> dict[str, str]:
    """Validated fields, or ``ProfileError``. Unknown keys are ignored; empty values dropped."""
    fields: dict[str, str] = {}
    for key in FIELDS:
        value = raw.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not isinstance(value, str):
            raise ProfileError(f"O campo {key} precisa ser um texto.")
        value = re.sub(r"\s+", " ", value).strip()
        if key == "birth_date":
            try:
                born = date.fromisoformat(value)
            except ValueError:
                raise ProfileError("A data de nascimento precisa ser AAAA-MM-DD.") from None
            if born > today() or born.year < 1900:
                raise ProfileError("A data de nascimento não parece certa.")
        elif len(value) > FIELD_LIMITS[key]:
            raise ProfileError(f"O campo {key} passa de {FIELD_LIMITS[key]} caracteres.")
        reason = _forbidden(value)
        if reason:
            raise ProfileError(f"O campo {key} {reason}.")
        fields[key] = value
    return fields


def _forbidden(text: str) -> str | None:
    secret = find_secret(text)
    if secret:
        return f"parece conter {secret}"
    if find_document(text):
        return "parece conter um documento de identidade"
    if find_order(text):
        return "parece uma ordem, não um fato"
    return None


def build_profile(
    text: object,
    fields: Mapping[str, Any] | None,
    existing: Profile | None,
    max_chars: int,
    on: date | None = None,
) -> Profile:
    """The new profile from what the LLM (or the user) proposes, or ``ProfileError``.

    ``fields`` are merged over the existing ones. A literal age that matches the birth date
    is turned into the marker, so it keeps up with the calendar.
    """
    if not isinstance(text, str) or not text.strip():
        raise ProfileError("O perfil precisa ter texto.")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        raise ProfileError(f"O perfil passa de {max_chars} caracteres ({len(text)}).")
    reason = _forbidden(text)
    if reason:
        raise ProfileError(f"O perfil {reason}.")
    merged = {**(existing.fields if existing else {}), **check_fields(fields or {})}
    profile = Profile(text=text, fields=merged)
    birth = profile.birth_date
    if birth is not None:
        age = age_on(birth, on or today())

        def mark(m: re.Match[str]) -> str:
            near = abs(int(m[3]) - age) <= 1  # only an age that really is theirs
            return f"{m[1]}{m[2]}{AGE_MARKER}{m[4]}" if near else m[0]

        profile.text = _AGE_PHRASE.sub(mark, text)
    if len(render(profile, on)) > max_chars:
        raise ProfileError(f"O perfil passa de {max_chars} caracteres.")
    return profile


def profile_to_doc(profile: Profile) -> dict[str, Any]:
    return {
        "_id": PROFILE_ID,
        "text": profile.text,
        "fields": dict(profile.fields),
        "updated_at": profile.updated_at,
    }


def profile_from_doc(doc: Mapping[str, Any]) -> Profile:
    try:
        text = doc["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("sem texto")
        return Profile(
            text=text,
            fields={k: str(v) for k, v in (doc.get("fields") or {}).items() if k in FIELDS},
            updated_at=doc["updated_at"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ProfileError(f"perfil corrompido: {exc}") from exc


def export(profile: Profile | None) -> dict[str, Any] | None:
    """The profile as plain data (``None`` when there is none)."""
    if profile is None:
        return None
    return {
        "text": profile.text,
        "rendered": render(profile),
        "fields": dict(profile.fields),
        "updated_at": profile.updated_at.isoformat(),
    }
