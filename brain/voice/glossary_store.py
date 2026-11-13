"""The terms the Juniper learned for her own vocabulary (T-218, ADR-034 section 4).

The transcription glossary has a fixed part (``voice.stt.glossary`` in the config) and a part she
fills herself: the person says "o projeto se chama Media Works", or corrects a transcription, and
the model calls ``glossary_add``. Those terms live here, in MongoDB, and reach the speech-to-text
hint and the sound-alike corrector from the next utterance on.

Because a model writes them, a term is treated as untrusted input:

- it is checked against a **strict allowlist** (letters, digits, spaces and a few name marks), so
  no markup, no path, no URL, no control character can get in;
- it is short (60 characters, five words) and the list has a cap, so it can neither flood the hint
  nor the corrector;
- the tools that write it are refused once the conversation has read the web or a file (see the
  dispatcher), the main way untrusted text could try to put a term here;
- every addition and removal is logged and ``glossary_list`` / ``juniper voice glossary`` show and
  delete all of it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from brain.memory.mongo import MongoConnection
from brain.voice.stt import normalise

COLLECTION: Final = "voice_glossary"
MAX_TERM_CHARS: Final = 60
MAX_TERM_WORDS: Final = 5
# What a name or a project may be made of: letters (any language), digits, space and . ' & + -
_ALLOWED: Final = re.compile(r"^(?:[^\W_]|[ .'&+\-])+$", re.UNICODE)


class GlossaryError(Exception):
    """A term that cannot be kept; the message says why, for the person."""


def clean_term(term: object) -> str:
    """The term as it will be stored. Raises `GlossaryError` if it must not be kept."""
    if not isinstance(term, str):
        raise GlossaryError("o termo precisa ser um texto")
    text = " ".join(term.split())  # one line, single spaces
    if not text:
        raise GlossaryError("o termo está vazio")
    if len(text) > MAX_TERM_CHARS:
        raise GlossaryError(f"o termo passa de {MAX_TERM_CHARS} caracteres")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in text):
        raise GlossaryError("o termo tem caracteres de controle")
    if not _ALLOWED.match(text):
        raise GlossaryError("o termo só pode ter letras, números, espaço e . ' & + -")
    if not any(c.isalpha() for c in text):
        raise GlossaryError("o termo precisa ter ao menos uma letra")
    if len(text.split()) > MAX_TERM_WORDS:
        raise GlossaryError(f"o termo passa de {MAX_TERM_WORDS} palavras: é um nome, não uma frase")
    return text


@dataclass(frozen=True)
class AddOutcome:
    status: str  # "added", "exists" or "full"
    term: str


class GlossaryStore:
    def __init__(
        self,
        connection: MongoConnection | None = None,
        *,
        max_terms: int = 100,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.connection = connection if connection is not None else MongoConnection()
        self.max_terms = max_terms
        self._clock = clock

    def _collection(self) -> Any:
        return self.connection.collection(COLLECTION)

    def add(self, term: str, added_by: str = "") -> AddOutcome:
        """Keep ``term`` (cleaned). A term that is already there only refreshes its recency."""
        text = clean_term(term)
        key = normalise(text)  # case, accents and spacing do not make a new term
        if not key:
            raise GlossaryError("o termo precisa ter ao menos uma letra")
        now = self._clock()
        with self.connection.guard():
            collection = self._collection()
            if collection.find_one({"_id": key}) is not None:
                collection.update_one({"_id": key}, {"$set": {"last_used": now}})
                return AddOutcome("exists", text)
            if collection.count_documents({}) >= self.max_terms:
                return AddOutcome("full", text)
            collection.insert_one(
                {
                    "_id": key,
                    "term": text,
                    "added_by": added_by,
                    "created_at": now,
                    "last_used": now,
                }
            )
        return AddOutcome("added", text)

    def remove(self, term: str) -> bool:
        key = normalise(" ".join(str(term).split()))
        if not key:
            return False
        with self.connection.guard():
            return bool(self._collection().delete_one({"_id": key}).deleted_count)

    def terms(self) -> list[str]:
        """The learned terms, the most recently used first (what fits the hint is the head)."""
        with self.connection.guard():
            docs = list(self._collection().find({}).sort("last_used", -1))
        return [str(doc["term"]) for doc in docs]

    def info(self) -> list[dict[str, Any]]:
        with self.connection.guard():
            return list(self._collection().find({}).sort("last_used", -1))

    def touch(self, term: str) -> None:
        """The corrector used this term just now: it stays near the head of the hint."""
        key = normalise(term)
        with self.connection.guard():
            self._collection().update_one({"_id": key}, {"$set": {"last_used": self._clock()}})
