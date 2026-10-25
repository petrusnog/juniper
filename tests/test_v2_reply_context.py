"""A resposta da Juniper como contexto e a confirmação por resposta (T-116)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.evolve import PersonaPhase
from brain.memory.v2.models import TranscriptTurn
from brain.memory.v2.night import SessionMaterial
from brain.memory.v2.reflect import REFLECT_SYSTEM
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.memory.v2.study import InterestSeedPhase
from tests.test_v2_interests import Scholar
from tests.test_v2_interests import ctx_for as interests_ctx
from tests.test_v2_persona import Proposer, phase_ctx
from tests.test_v2_reflect import (
    NOW,
    Reflector,
    ctx_for,
    held_belief,
    material,
    new,
    reflect,
    tturn,
)

HYPOTHESIS = (
    "A tua crônica, a visão de Deus como símbolo do teu próprio self. E a psicodelia é uma viagem "
    "pra dentro. A Abbey Road foi tua escola, o Hendrix te deu a paleta."
)
STATEMENT = "Acho que o Petrus traduz um estado interno em música psicodélica."


def psychedelia(*, first_used_tools: bool = False) -> list[SessionMaterial]:
    first = TranscriptTurn(
        "sessP",
        0,
        "Porque você acha que psicodelia é a minha cara?",
        HYPOTHESIS,
        used_tools=first_used_tools,
        at=NOW - timedelta(hours=3),
    )
    second = tturn(
        "sessP",
        1,
        "Você tá certa, Jun. Eu sou profundamente conectado com a psicodelia. A maioria das "
        "minhas composições gira em torno dessa estética.",
        "Faz sentido agora.",
    )
    return material(("sessP", [first, second]))


def confirming(**changes: Any) -> dict[str, Any]:
    return new(STATEMENT, ["S1:t1"], topic="música", confirms=True, **changes)


# --- the reference conversation -----------------------------------------------------------------


async def test_his_you_are_right_confirms_her_hypothesis_with_weight_four() -> None:
    llm = Reflector({"proposals": [confirming()]})
    ctx = ctx_for(llm, psychedelia())

    result = await reflect(ctx)

    (belief,) = ctx.data.beliefs.list()
    assert "símbolo do teu próprio self" in llm.reflection_calls()[0]  # she can read her own thesis
    assert [e.kind for e in belief.evidence_for] == ["confirmation"]
    assert belief.evidence_for[0].session_id == "sessP" and belief.evidence_for[0].turn == 1
    assert belief.alpha == pytest.approx(1.0 + 4.0) and belief.user_confirmed == "confirmed"
    assert belief.status == "active" and "1 confirmada(s) por resposta" in result.summary
    assert BeliefBook(ctx.data).calibration("música").hits == 1.0  # she proposed, he agreed


async def test_what_only_she_said_is_not_evidence_without_his_confirmation() -> None:
    # the model cites his question (t0); her details in the reply are context, never evidence
    proposal = new("Acho que o Petrus aprendeu com a Abbey Road.", ["S1:t0"], topic="música")
    ctx = ctx_for(Reflector({"proposals": [proposal]}), psychedelia())
    await reflect(ctx)
    (belief,) = ctx.data.beliefs.list()
    assert [e.kind for e in belief.evidence_for] == ["observation"]
    assert belief.alpha == pytest.approx(2.0) and belief.user_confirmed is None


def test_the_prompt_forbids_attributing_to_him_what_only_she_said() -> None:
    assert "nunca atribua à pessoa o que só você afirmou" in REFLECT_SYSTEM
    assert "só para o que essa fala" in REFLECT_SYSTEM and "nunca são evidência" in REFLECT_SYSTEM


async def test_her_context_cannot_be_cited_as_evidence() -> None:
    for label in ("S1:t0r", "S1:r0", "contexto"):
        ctx = ctx_for(Reflector({"proposals": [new(evidence=[label])]}), psychedelia())
        result = await reflect(ctx)
        assert ctx.data.beliefs.count() == 0 and "sem_evidencia×1" in result.summary


# --- when a confirmation does not hold up ------------------------------------------------------


async def test_confirms_without_a_reply_to_confirm_is_refused() -> None:
    proposal = new(STATEMENT, ["S1:t0"], topic="música", confirms=True)  # t0 answers nothing
    ctx = ctx_for(Reflector({"proposals": [proposal]}), psychedelia())
    result = await reflect(ctx)
    assert ctx.data.beliefs.count() == 0 and "confirma_sem_contexto×1" in result.summary


async def test_a_confirmation_of_a_reply_that_used_tools_is_refused() -> None:
    ctx = ctx_for(Reflector({"proposals": [confirming()]}), psychedelia(first_used_tools=True))
    result = await reflect(ctx)
    assert ctx.data.beliefs.count() == 0 and "confirma_sem_contexto×1" in result.summary


async def test_confirming_cannot_weaken_a_belief() -> None:
    data = V2Data.over(InMemoryDocStore())
    belief = held_belief(data)
    proposal = {"action": "weaken", "target": belief.id, "evidence": ["S1:t1"], "confirms": True}
    ctx = ctx_for(Reflector({"proposals": [proposal]}), psychedelia(), data=data)
    result = await reflect(ctx)
    after = data.beliefs.get(belief.id)
    assert after is not None and after.beta == belief.beta
    assert "confirma_invalida×1" in result.summary


async def test_a_non_boolean_confirms_is_not_a_confirmation() -> None:
    proposal = confirming()
    proposal["confirms"] = "true"
    ctx = ctx_for(Reflector({"proposals": [proposal]}), psychedelia())
    await reflect(ctx)
    (belief,) = ctx.data.beliefs.list()
    assert [e.kind for e in belief.evidence_for] == ["observation"]  # just an observation


async def test_confirming_a_belief_she_already_holds_adds_four_and_a_hit() -> None:
    data = V2Data.over(InMemoryDocStore())
    belief = held_belief(data)
    proposal = {"action": "reinforce", "target": belief.id, "evidence": ["S1:t1"], "confirms": True}
    ctx = ctx_for(Reflector({"proposals": [proposal]}), psychedelia(), data=data)

    result = await reflect(ctx)

    after = data.beliefs.get(belief.id)
    assert after is not None and after.alpha == pytest.approx(belief.alpha + 4.0)
    assert after.user_confirmed == "confirmed" and "1 confirmada(s) por resposta" in result.summary
    assert BeliefBook(data).calibration(belief.topic).hits == 1.0


async def test_the_critic_is_told_never_to_cite_her_context() -> None:
    from brain.memory.v2.reflect import CRITIC_SYSTEM

    assert 'nunca cite uma linha "(contexto da Juniper' in CRITIC_SYSTEM


# --- who sees her reply ---------------------------------------------------------------------------


async def test_the_interest_seeds_still_come_from_his_words_only() -> None:
    llm = Scholar(["Beatles"])
    ctx = interests_ctx(llm, "oi")
    ctx.material = psychedelia()
    await InterestSeedPhase().run(ctx)
    prompt = llm.prompts[0][1]
    assert "Porque você acha" in prompt and "Abbey Road" not in prompt
    assert "contexto da Juniper" not in prompt


async def test_the_personality_phase_reads_her_reply_as_context() -> None:
    llm = Proposer([])
    ctx = phase_ctx(llm)
    ctx.material = psychedelia()
    await PersonaPhase().run(ctx)
    prompt = llm.calls[0][1]
    assert "(contexto da Juniper:" in prompt and "símbolo do teu próprio self" in prompt


async def test_the_reply_context_survives_a_night_without_replies() -> None:
    mat = material(("s", [tturn("s", 0, "oi")]))
    llm = Reflector({"proposals": []})
    await reflect(ctx_for(llm, mat))
    assert "contexto da Juniper" not in llm.reflection_calls()[0].split("<fatos_novos>")[0]
