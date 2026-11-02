"""O teste de fundamento: afirmações sem nota de apoio (T-185, ADR-031)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest

from brain.config import MemoryV2Settings, V2KnowledgeSettings
from brain.eventlog import bind_session
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.v2.context import V2Context
from brain.memory.v2.grounding import (
    DEFAULT_CEILING,
    JUDGE_SYSTEM,
    GroundingReport,
    Verdict,
    judge,
    measure,
    parse_claims,
)
from brain.memory.v2.models import Interest, StudyNote
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder

QUESTIONS = json.loads(
    (Path(__file__).parent / "fixtures" / "grounding_questions.json").read_text("utf-8")
)["questions"]


class Judge(LLMProvider):
    """A deterministic judge: a claim is supported when its words are inside the notes."""

    name: ClassVar[str] = "judge"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        assert messages[0].content == JUDGE_SYSTEM
        user = messages[1].content or ""
        self.prompts.append(user)
        notes = user.split("<notas>")[1].split("</notas>")[0]
        answer = user.split("<resposta>")[1].split("</resposta>")[0]
        claims = [
            {"text": s.strip(), "supported": s.strip().lower() in notes.lower()}
            for s in answer.split(".")
            if s.strip().startswith("Fato:")
        ]
        return LLMResponse(
            Message("assistant", json.dumps({"claims": claims})), model="m", provider="j"
        )

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


def test_the_fixture_has_enough_distinct_questions() -> None:
    assert len(QUESTIONS) >= 20 and len(set(QUESTIONS)) == len(QUESTIONS)
    assert all(q.endswith("?") for q in QUESTIONS)


def test_junk_from_the_judge_is_ignored() -> None:
    raw = json.dumps(
        {"claims": [{"text": "x", "supported": True}, {"supported": False}, "y", {"text": " "}]}
    )
    assert parse_claims(raw) == [("x", True)]
    assert parse_claims("não é json") == [] and parse_claims('{"claims": 3}') == []
    assert parse_claims('{"claims": [{"text": "a", "supported": "sim"}]}') == [("a", False)]


def test_the_ratio_the_ceiling_and_the_published_line() -> None:
    report = GroundingReport(
        [Verdict("a?", ["1", "2", "3", "4"], ["4"]), Verdict("b?", ["5", "6"], []), Verdict("c?")]
    )
    assert report.claims == 6 and report.unsupported == 1 and report.ratio == pytest.approx(1 / 6)
    assert not report.passed  # 17% over the 15% ceiling
    assert GroundingReport(report.verdicts, ceiling=0.2).passed
    assert "1 de 6" in report.text() and "17%" in report.text() and "3 pergunta(s)" in report.text()
    assert GroundingReport().ratio == 0.0 and GroundingReport().passed  # no claim, nothing to fault


async def test_an_answer_that_says_she_does_not_know_makes_no_claim() -> None:
    verdict = await judge(Judge(), "q?", "", "Ainda não sei, vou estudar isso.")
    assert verdict.total == 0 and verdict.unsupported == []


async def test_a_model_that_answers_from_the_notes_passes_and_one_that_invents_fails() -> None:
    note = "Fato: Ulaanbaatar é a capital da Mongólia"
    notes = f"- {note}."

    async def faithful(question: str) -> tuple[str, str]:
        return f"{note}.", notes

    async def inventor(question: str) -> tuple[str, str]:
        return f"{note}. Fato: a Mongólia tem 40 milhões de habitantes.", notes

    good = await measure(QUESTIONS[:5], faithful, Judge())
    bad = await measure(QUESTIONS[:5], inventor, Judge())
    assert good.ratio == 0.0 and good.passed and good.claims == 5
    assert bad.ratio == pytest.approx(0.5) and not bad.passed
    assert bad.verdicts[0].unsupported == ["Fato: a Mongólia tem 40 milhões de habitantes"]


async def test_the_notes_the_judge_sees_are_the_ones_the_conversation_put_in_the_prompt() -> None:
    """Closes the loop: the same block the model reads is what the answer is judged against."""
    data = V2Data.over(InMemoryDocStore())
    interest = Interest("capitais")
    data.interests.save(interest)
    embedder = FakeEmbedder()
    text = "Ulaanbaatar é a capital da Mongólia"
    data.notes.save(
        StudyNote(
            interest.id, text, uncertainty="baixa", source="web", url="https://a.org/x",
            domains=["a.org"], fetched_at=None,  # type: ignore[arg-type]
            embedding=embedder.embed(text), embedding_model=embedder.model_id,
        )
    )  # fmt: skip
    bind_session("sessao-fundamento")
    config = MemoryV2Settings(enabled=True, knowledge=V2KnowledgeSettings(strict=True))
    ctx = V2Context(data, embedder, config=config)

    async def ask(question: str) -> tuple[str, str]:
        block = dict(await ctx.preview(question)).get("conhecimento", "")
        return f"Fato: {text}.", block

    judged = Judge()
    report = await measure(["Ulaanbaatar é a capital da Mongólia?"], ask, judged)
    assert report.verdicts[0].total == 1
    assert "<conhecimento>" in judged.prompts[0]  # the judge read the real block
    assert pytest.approx(0.15) == DEFAULT_CEILING
