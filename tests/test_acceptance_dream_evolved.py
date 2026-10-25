"""Aceite do sonho evoluído (ADR-018 e ADR-019): refinamento, estudo progressivo, conhecimento na
conversa e sentimentos em tempo real, de ponta a ponta sobre o mesmo cenário do marco."""

from __future__ import annotations

from datetime import timedelta

import pytest

from brain.memory.v2.context import KNOWLEDGE_OPEN
from brain.memory.v2.knowledge import NoteBook
from brain.memory.v2.live import LiveReactor
from tests.test_acceptance_memory_v2 import FACT, NIGHT, RECENT, World, morning  # noqa: F401
from tests.test_v2_live_affect import PROVOKE, Judge, config


async def test_the_dream_refined_the_raw_fact_into_a_remembered_one(morning: World) -> None:  # noqa: F811
    everything = morning.store.list_memories(None, True)
    raw = next(m for m in everything if m.status_reflexao == "bruta")
    (refined,) = morning.store.list_memories(None, False)

    assert raw.status == "superseded" and raw.superseded_by == refined.id
    assert refined.text == raw.text == FACT  # the fact itself was not rewritten
    assert refined.status_reflexao == "refinada" and refined.refinada_de == raw.id
    assert refined.contexto and refined.relevancia == 0.6
    assert refined.como_eu_era["versao"] == 2  # the personality the dream had just moved
    assert refined.session_id == RECENT and refined.turn == 0  # provenance survives


async def test_the_study_is_progressive_with_notes_that_carry_from_night_to_night(
    morning: World,  # noqa: F811
) -> None:
    (beatles,) = morning.data.interests.list()
    assert beatles.level == 1
    notes = NoteBook(morning.data)
    assert {n.text for n in notes.claims(beatles.id)} == {
        "os Beatles se formaram em Liverpool",
        "a banda gravou em Abbey Road",
    }
    assert [n.text for n in notes.questions(beatles.id)] == ["como foi o fim da banda?"]

    morning.llm.calls.clear()
    second = await morning.runner(NIGHT + timedelta(days=1)).run()

    assert second.run is not None and second.run.status == "done", second.text()
    assert morning.data.interests.get(beatles.id).level == 2  # type: ignore[union-attr]
    (system, _) = next(
        c for c in morning.llm.calls if c[0].startswith("Você é a Juniper, estudando")
    )
    assert "passo 2 do seu estudo" in system
    assert "os Beatles se formaram em Liverpool" in system  # the earlier notes came back
    assert "(não tenho certeza total)" in system  # with their uncertainty
    assert "como foi o fim da banda?" in system  # and the open question


async def test_the_next_conversation_answers_from_her_notes(morning: World) -> None:  # noqa: F811
    sections = dict(
        await morning.context().preview(
            "os Beatles se formaram em Liverpool e gravaram em Abbey Road"
        )
    )

    assert "conhecimento" in sections and KNOWLEDGE_OPEN in sections["conhecimento"]
    assert "os Beatles se formaram em Liverpool" in sections["conhecimento"]
    assert "Fundamente a resposta nestas notas" in sections["conhecimento"]


async def test_a_provocation_in_the_conversation_is_subtle_and_chains(morning: World) -> None:  # noqa: F811
    from brain.core.session import Turn

    live = LiveReactor(morning.data, Judge(PROVOKE), config=morning.config, clock=lambda: NIGHT)
    for i in range(3):
        await live.react("20261023-100000-cccccc", Turn("você falhou de novo", index=i))

    reactions = [a for a in morning.data.affects.list() if a.origin == "conversa"]
    assert len(reactions) == 3 and all(a.intensity <= 0.3 for a in reactions)
    dream = [a for a in morning.data.affects.list() if a.origin == "sonho"]
    assert dream and dream[0].intensity >= 0.6  # the intense one came from the dream
    assert config().affect.live_max_intensity == pytest.approx(0.3)
