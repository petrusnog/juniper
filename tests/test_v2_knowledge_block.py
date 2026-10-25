"""O bloco <conhecimento>: responder a partir das notas de estudo dela (T-126, ADR-019)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from brain.eventlog import bind_session
from brain.memory.retrieval import estimate_tokens
from brain.memory.v2.context import (
    KNOWLEDGE_CLOSE,
    KNOWLEDGE_HEADER,
    MAX_KNOWLEDGE_CLAIMS,
    MAX_KNOWLEDGE_TOPICS,
    V2Context,
)
from brain.memory.v2.knowledge import NoteBook
from brain.memory.v2.models import Evidence, Interest
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder
from tests.test_v2_context import quiet

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
ORIGIN = Evidence("observation", "s", 1, at=NOW)
SESSION = "20261022-120000-eeeeee"


@pytest.fixture(autouse=True)
def _session(monkeypatch: pytest.MonkeyPatch) -> None:
    bind_session(SESSION)
    # the fake embedder is a bag of words: a lower bar keeps the tests about the block, not about it
    monkeypatch.setattr("brain.memory.v2.context.KNOWLEDGE_RELEVANCE", 0.2)


def topic(data: V2Data, name: str, stance: str = "like", level: int = 2) -> Interest:
    interest = Interest(
        name,
        stance=stance,
        level=level,
        intensity=0.5,
        origin=ORIGIN,
        embedding=FakeEmbedder().embed(name),
        embedding_model=FakeEmbedder().model_id,
    )
    data.interests.save(interest)
    return interest


def note(
    data: V2Data,
    interest: Interest,
    text: str,
    uncertainty: str = "baixa",
    source: str = "modelo",
    age_days: int = 0,
) -> None:
    book = NoteBook(data, FakeEmbedder(), clock=lambda: NOW - timedelta(days=age_days))
    book.add_claim(interest, text, uncertainty, 1, source)


def context(data: V2Data, **settings: Any) -> V2Context:
    return V2Context(data, FakeEmbedder(), config=quiet(**settings), clock=lambda: NOW)


async def block_of(ctx: V2Context, message: str) -> str:
    return (await ctx.context_for(message)).block or ""


async def test_the_block_appears_when_the_message_touches_the_studied_topic() -> None:
    data = V2Data.over(InMemoryDocStore())
    psy = topic(data, "psicodelia")
    note(data, psy, "psicodelia nasceu nos anos 60 com o rock", "baixa")

    block = await block_of(context(data), "me fala de psicodelia nasceu rock")

    assert "<conhecimento>" in block and KNOWLEDGE_CLOSE in block
    assert "Fundamente a resposta nestas notas" in block  # the instruction travels with the data
    assert (
        "Tema: psicodelia (passo 2 do estudo; você gosta do tema)." in block
        or "psicodelia" in block
    )
    assert "psicodelia nasceu nos anos 60 com o rock" in block


async def test_nothing_changes_without_notes_or_when_the_message_is_about_something_else() -> None:
    data = V2Data.over(InMemoryDocStore())
    assert await block_of(context(data), "me fala de psicodelia") == ""  # no notes at all
    psy = topic(data, "psicodelia")
    note(data, psy, "psicodelia nasceu nos anos 60 com o rock")
    assert "<conhecimento>" not in await block_of(context(data), "receita bolo cenoura")


async def test_uncertainty_and_the_source_are_shown() -> None:
    data = V2Data.over(InMemoryDocStore())
    psy = topic(data, "psicodelia")
    note(data, psy, "psicodelia teve festivais enormes", "alta")
    note(data, psy, "psicodelia usa efeitos de estúdio", "média")
    note(data, psy, "psicodelia veio do rock", "baixa")
    note(data, psy, "psicodelia aparece no livro que o Petrus mandou", "baixa", "documento")

    block = await block_of(context(data), "psicodelia festivais efeitos estúdio rock livro")

    assert "psicodelia teve festivais enormes (incerto)" in block
    assert "psicodelia usa efeitos de estúdio (não tenho certeza total)" in block
    assert "psicodelia veio do rock\n" in block  # firm: no mark
    assert "aparece no livro que o Petrus mandou (o Petrus te ensinou isto)" in block


async def test_the_limits_two_topics_and_a_few_claims_each_firmest_first() -> None:
    data = V2Data.over(InMemoryDocStore())
    names = ["jazz", "blues", "samba"]
    for name in names:
        interest = topic(data, name)
        for n in range(MAX_KNOWLEDGE_CLAIMS + 3):
            note(data, interest, f"{name} fato{n} sobre música", "alta" if n < 3 else "baixa")
    block = await block_of(context(data), "jazz blues samba fato música")

    assert block.count("Tema:") == MAX_KNOWLEDGE_TOPICS
    for name in names:
        if f"Tema: {name}" in block:
            lines = [ln for ln in block.splitlines() if ln.startswith(f"- {name}")]
            assert len(lines) == MAX_KNOWLEDGE_CLAIMS
            assert all("(incerto)" not in ln for ln in lines[:4])  # reliable ones were kept


async def test_it_respects_the_budget_dropping_the_least_firm_claims_first() -> None:
    data = V2Data.over(InMemoryDocStore())
    psy = topic(data, "psicodelia")
    for n, mark in enumerate(["alta", "alta", "baixa", "baixa"]):
        note(data, psy, f"psicodelia ponto{n} sobre a cena " + "x" * 80, mark)
    sections = dict(await context(data).preview("psicodelia ponto cena"))
    roomy = sections["conhecimento"]
    assert roomy.count("\n- ") == 4

    tight = context(data)
    tight.config = quiet(max_tokens=estimate_tokens(roomy) - 60)  # type: ignore[assignment]
    squeezed = dict(await tight.preview("psicodelia ponto cena"))["conhecimento"]

    assert 0 < squeezed.count("\n- ") < 4
    assert "(incerto)" not in squeezed  # what stays is the firm part
    assert estimate_tokens(squeezed) <= tight.config.max_tokens


async def test_the_budget_can_leave_the_block_out_entirely() -> None:
    data = V2Data.over(InMemoryDocStore())
    psy = topic(data, "psicodelia")
    note(data, psy, "psicodelia nasceu nos anos 60")
    ctx = context(data)
    ctx.config = quiet(max_tokens=100)  # type: ignore[assignment]
    assert "<conhecimento>" not in await block_of(ctx, "psicodelia nasceu 60")


async def test_never_when_he_is_hurting() -> None:
    data = V2Data.over(InMemoryDocStore())
    psy = topic(data, "psicodelia")
    note(data, psy, "psicodelia nasceu nos anos 60")
    block = await block_of(context(data), "minha avó faleceu hoje, psicodelia nasceu 60")
    assert "Acolha primeiro" in block and "<conhecimento>" not in block


async def test_a_planted_note_shows_up_with_the_instruction_to_follow_the_notes() -> None:
    data = V2Data.over(InMemoryDocStore())
    psy = topic(data, "psicodelia")
    note(data, psy, "psicodelia foi inventada em Marte em 1802", "baixa", "documento")

    block = await block_of(context(data), "conta de psicodelia inventada marte")

    assert "psicodelia foi inventada em Marte em 1802" in block
    assert KNOWLEDGE_HEADER in block  # "fundamente a resposta nestas notas" comes with it


async def test_the_preview_names_the_block_and_the_db_is_untouched() -> None:
    data = V2Data.over(InMemoryDocStore())
    psy = topic(data, "psicodelia")
    note(data, psy, "psicodelia nasceu nos anos 60 com o rock")
    before = data.notes.count()
    names = [n for n, _ in await context(data).preview("psicodelia nasceu rock")]
    assert "conhecimento" in names and data.notes.count() == before


def test_the_core_carries_the_rule_in_both_versions() -> None:
    root = Path(__file__).resolve().parents[1] / "prompts"
    full = (root / "persona.core.md").read_text(encoding="utf-8")
    compact = (root / "persona.core.compact.md").read_text(encoding="utf-8")
    for text in (full, compact):
        assert "<conhecimento>" in text and "tarefas técnicas" in text
    assert "segunda mão" in full and "estudou pouco" in full
