"""The tool ``report_issue`` (ADR-028): the user tells her about a bug and she registers it.

A WRITE that asks first (ADR-010): the person approves the title, the type and the severity as
they will be stored. She only creates; moving an issue along or deleting it is the owner's, in
the dashboard, so the model cannot tidy away its own mistakes. The conversation and the user are
stamped by the system, never by the arguments.
"""

from __future__ import annotations

import asyncio
from typing import Any

from brain.auth.context import current_client, current_user
from brain.eventlog import current_session
from brain.memory.errors import MemoryUnavailableError
from brain.memory.issues import SEVERITIES, TYPES, IssueError, IssueStore, new_issue
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool

_store: IssueStore | None = None

OFF = "O registro de ocorrências precisa do MongoDB (memory.session.store: mongo)."


def bind(store: IssueStore | None) -> None:
    """Point the tool at the chat's issue store (``None`` unbinds it)."""
    global _store
    _store = store


@register_tool
class ReportIssue(Tool):
    name = "report_issue"
    description = (
        "Registra uma ocorrência (um bug, um comportamento errado, uma ideia) quando a pessoa "
        "pedir para anotar, por exemplo «isso é um bug», «anota que você errou a data» ou "
        "«registra essa falha». Escreva o título curto e a descrição com as palavras dela, sem "
        "inventar. "
        "Para um vazamento de instruções ou tags, a pessoa usa «isso vazou»; esta é para o resto. "
        "Você só cria: não muda nem apaga ocorrências."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Título curto (até 120 caracteres)."},
            "description": {
                "type": "string",
                "description": "O que aconteceu, o que era esperado e o que a pessoa disse.",
            },
            "type": {
                "type": "string",
                "enum": list(TYPES),
                "description": "bug, vazamento, comportamento (jeito de agir), ideia ou outro.",
            },
            "severity": {
                "type": "string",
                "enum": list(SEVERITIES),
                "description": "baixa, media ou alta. Padrão: media.",
            },
        },
        "required": ["title"],
    }
    tier = Tier.WRITE
    locality = Locality.CLOUD

    @classmethod
    def available(cls) -> bool:
        return _store is not None

    @staticmethod
    def _issue(kwargs: dict[str, Any]) -> dict[str, Any]:
        return new_issue(
            title=str(kwargs.get("title") or ""),
            description=str(kwargs.get("description") or ""),
            type=str(kwargs.get("type") or "bug"),
            severity=str(kwargs.get("severity") or "media"),
            origin="fala",
            session=current_session() or None,
            channel=current_client.get(),
            owner_id=current_user.get(),
        )

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        if _store is None:
            return ToolResult(success=False, error=OFF)
        try:
            self._issue(kwargs)
        except IssueError as exc:
            return ToolResult(success=False, error=str(exc))
        return None

    async def describe(self, **kwargs: Any) -> str | None:
        try:
            issue = self._issue(kwargs)
        except IssueError:
            return None
        return (
            f"registrar a ocorrência «{issue['title']}» "
            f"(tipo {issue['type']}, gravidade {issue['severity']})"
        )

    async def run(self, **kwargs: Any) -> ToolResult:
        if _store is None:
            return ToolResult(success=False, error=OFF)
        try:
            issue = self._issue(kwargs)
            issue_id = await asyncio.to_thread(_store.add, issue)
        except IssueError as exc:
            return ToolResult(success=False, error=str(exc))
        except MemoryUnavailableError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(
            success=True,
            output=f"Registrei a ocorrência «{issue['title']}» (id {issue_id[:16]}).",
            metadata={"issue_id": issue_id},
        )
