"""Picking up the previous conversation (``--resume``), the same for every client."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from brain.config import settings
from brain.core.session import Session
from brain.memory.errors import MemoryUnavailableError
from brain.memory.session import SessionStore


def when(timestamp: str) -> str:
    """``2026-10-13T22:15:00+00:00`` as local time: ``13/10 às 19:15``."""
    moment = datetime.fromisoformat(timestamp).astimezone(ZoneInfo(settings.agent.timezone))
    return f"{moment:%d/%m} às {moment:%H:%M}"


def resume_note(session: Session) -> str:
    """What Juniper says when it picks ``session`` up."""
    turns = sum(1 for m in session.history if m.role == "user")
    plural = "mensagem" if turns == 1 else "mensagens"
    return f"Retomando a conversa de {when(session.updated_at)} ({turns} {plural} suas)."


NO_PREVIOUS_NOTE = "Não achei nenhuma conversa anterior, então vamos começar uma nova."


def previous_session(store: SessionStore) -> tuple[Session | None, str]:
    """The conversation ``--resume`` picks up, and what Juniper says about it."""
    try:
        session = store.latest()
    except (OSError, MemoryUnavailableError):
        session = None
    if session is None:
        return None, NO_PREVIOUS_NOTE
    return session, resume_note(session)
