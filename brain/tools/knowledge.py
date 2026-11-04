"""``learn_topic``: she learns on the spot from the web, and answers from her notes (ADR-031).

Offered only while the V2 is on (her notes live there) and the web can search. It writes to her
memory, silently, like ``remember``: ``WRITE`` with ``confirm = False``, auditable in the dashboard
and in ``juniper memory knowledge``. What returns to the model is her notes on the topic, not the
pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from brain.config import settings
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool
from brain.web.search import SearchError

if TYPE_CHECKING:
    from brain.memory.v2.learn import Learner

_learner: Learner | None = None


def bind(learner: Learner | None) -> None:
    """Point the tool at a learner (``None`` hides the tool)."""
    global _learner
    _learner = learner


def learner() -> Learner | None:
    """The bound learner (``None`` when the web study is off)."""
    return _learner


@register_tool
class LearnTopic(Tool):
    name = "learn_topic"
    description = (
        "Aprende agora sobre um tema que você ainda não estudou (ou em que falta algo): procura "
        "na web, lê as fontes, grava notas de estudo na sua memória e devolve as notas. Chame "
        "ANTES de responder quando o bloco <conhecimento> não sustenta a resposta, e sempre "
        "que o Petrus pedir para pesquisar, ver notícia ou saber de algo atual. Depois, "
        "responda a partir das notas que voltarem."
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "O tema, curto e geral (ex.: 'protocolo HTTP/3').",
            },
            "questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Até 3 perguntas que você quer responder sobre o tema.",
            },
        },
        "required": ["topic"],
    }
    tier = Tier.WRITE
    blocked_when_tainted = True
    confirm = False  # her own notes: auditable and deletable (juniper memory knowledge)
    locality = Locality.CLOUD

    @classmethod
    def available(cls) -> bool:
        return settings.web.enabled and _learner is not None and _learner.usable()

    async def describe(self, **kwargs: Any) -> str | None:
        return f"aprender sobre «{str(kwargs.get('topic', '')).strip()}» na web"

    async def run(self, **kwargs: Any) -> ToolResult:
        from brain.memory.v2.learn import LearnError

        if _learner is None:
            return ToolResult(success=False, error="O estudo na web está desligado agora.")
        questions = kwargs.get("questions")
        asks = [str(q) for q in questions] if isinstance(questions, list) else []
        try:
            report = await _learner.learn(str(kwargs.get("topic") or ""), asks)
        except LearnError as exc:
            return ToolResult(success=False, error=str(exc), metadata={"code": "topic"})
        except SearchError as exc:
            return ToolResult(success=False, error=exc.message, metadata={"code": exc.code})
        return ToolResult(
            success=True,
            output=report.render(),
            metadata={
                "provider": report.provider,
                "sources": report.sources,
                "added": report.added,
                "known": len(report.notes),
            },
        )
