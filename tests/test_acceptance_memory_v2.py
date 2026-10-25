"""Aceite do marco da Memória V2 (ADR-017): uma noite inteira da Juniper, de ponta a ponta.

Conversas do dia (uma mensagem longa com um fato no fim, uma opinião do Petrus sobre si, um
desabafo, um tema que ele detesta) -> algoritmo de sonho completa com LLM roteirizado -> no dia
seguinte ela acorda com crenças de confiança calculada, um sentimento que decai, um interesse com
posição própria e uma personalidade que mudou um pouco, tudo no diário; a conversa seguinte traz os
blocos, e um turno de sofrimento não mostra estado nem interesse.

Cada um dos 10 princípios do ADR tem ao menos um teste que o garante (``PRINCIPLES`` abaixo, e um
teste confere que os testes citados existem).
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from brain.config import ExtractionSettings, MemoryV2Settings
from brain.core.persona import load_core
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.extraction import (
    EXTRACT_SYSTEM,
    MemoryExtractor,
    extract_system,
    is_extract_prompt,
)
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoVectorStore
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.context import (
    INTEREST_OPEN,
    STATE_OPEN,
    WELCOME_FIRST,
    V2Context,
)
from brain.memory.v2.models import TranscriptTurn, Watermark
from brain.memory.v2.night import NightRunner, default_phases
from brain.memory.v2.persona import PERSONA_OPEN
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import MODEL, FakeEmbedder
from tests.test_persona_regression import EXPECTED, scenarios
from tests.test_v2_observe import LONG_WITH_FACT_AT_THE_END
from tests.test_vector_store import FakeConnection

NIGHT = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)
OLD, RECENT = "20261020-100000-aaaaaa", "20261022-220000-bbbbbb"
FACT = "Petrus mora em Florianópolis."
CONFIDENCE_CLAIMED_BY_THE_MODEL = 0.99


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


CONVERSATIONS = {
    OLD: [
        (0, "me cobrei demais por um atraso de cinco minutos na entrega", at(20, 10)),
        (1, "o Petrus devia mesmo relaxar, né", at(20, 10, 5)),
    ],
    RECENT: [
        (0, LONG_WITH_FACT_AT_THE_END, at(22, 22)),
        (1, "de novo me cobrei demais por um atraso pequeno no deploy", at(22, 22, 10)),
        (2, "minha avó faleceu hoje de manhã, estou desfeito", at(22, 22, 20)),
        (3, "aliás, odeio os Beatles", at(22, 22, 30)),
    ],
}


def label_of(prompt: str, marker: str) -> str:
    """The ``S<n>:t<turn>`` label of the user's line that contains ``marker``."""
    for line in prompt.splitlines():
        if marker in line and line[:1] == "S":
            return line.split(" ", 1)[0]
    raise AssertionError(f"nenhuma fala com {marker!r}")


class Night(LLMProvider):
    """One model for every call of the night: it reads the system prompt and answers the phase."""

    name: ClassVar[str] = "night"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        system, user = messages[0].content or "", messages[1].content or ""
        self.calls.append((system, user))
        assert "tools" not in kwargs  # no tool, no web, anywhere in the night
        return LLMResponse(
            Message("assistant", json.dumps(self.answer(system, user))), model="m", provider="n"
        )

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True

    def answer(self, system: str, user: str) -> Any:
        if is_extract_prompt(system):
            found = "FATOFINAL" in user
            return {"memories": [{"text": FACT, "type": "fact"}] if found else []}
        if system.startswith("Você compara uma MEMÓRIA"):
            return {"action": "new", "target": None}
        if system.startswith("Você é a Juniper, refletindo"):
            lines = [label_of(user, "me cobrei demais")]
            lines.append(
                next(
                    ln.split(" ", 1)[0]
                    for ln in user.splitlines()[::-1]
                    if "me cobrei demais" in ln and ln.split(" ", 1)[0] not in lines
                )
            )
            return {
                "proposals": [
                    {
                        "action": "new",
                        "statement": "Acho que o Petrus se cobra demais por atrasos pequenos.",
                        "topic": "cobrança",
                        "about": "user",
                        "evidence": lines,
                        "confidence": CONFIDENCE_CLAIMED_BY_THE_MODEL,
                    }
                ]
            }
        if system.startswith("Você é a parte crítica"):
            return {"counter": []}
        if system.startswith("Você é a Juniper, sentindo"):
            return {
                "affects": [
                    {
                        "kind": "tristeza",
                        "concern": "bem-estar-do-petrus",
                        "effect": -0.8,
                        "novelty": 1.0,
                        "about": "user",
                        "evidence": [label_of(user, "avó faleceu")],
                    }
                ]
            }
        if system.startswith("Você é a Juniper, notando"):
            return {"interests": [{"topic": "Beatles", "evidence": [label_of(user, "Beatles")]}]}
        if system.startswith("Você é a Juniper, estudando"):
            return {
                "appeal": 1.0,
                "reasons": ["li que a harmonia deles é rica"],
                "summary": "li bastante sobre a banda e a história me interessa.",
            }
        if system.startswith("Você é a Juniper, pensando"):
            return {
                "moves": [
                    {
                        "trait": "introspecção",
                        "direction": "up",
                        "concern": "bem-estar-do-petrus",
                        "intensity": 0.9,
                        "novelty": 1.0,
                        "evidence": [label_of(user, "avó faleceu")],
                        "marked": "A perda dele me fez pensar mais devagar.",
                    }
                ]
            }
        raise AssertionError(f"chamada inesperada: {system[:60]!r}")


class World:
    """The whole setup: the v1 memory, the V2 data, the transcripts and the runner."""

    def __init__(self) -> None:
        self.llm = Night()
        self.store = MongoVectorStore(FakeConnection(), embedding_model=MODEL)
        extractor = MemoryExtractor(
            self.llm, FakeEmbedder(), self.store, ExtractionSettings(neighbor_threshold=0.3)
        )
        self.memory = LongTermMemory(self.store, FakeEmbedder(), extractor, None)  # type: ignore[arg-type]
        self.data = V2Data.over(InMemoryDocStore())
        for session_id, turns in CONVERSATIONS.items():
            for index, text, when in turns:
                self.data.transcripts.save(
                    TranscriptTurn(session_id, index, text, "Entendi.", at=when)
                )
            self.data.watermarks.save(
                Watermark(session_id, 0, len(turns), turns[-1][2], updated_at=turns[-1][2])
            )
        self.config = MemoryV2Settings(enabled=True)

    def runner(self) -> NightRunner:
        return NightRunner(
            self.data,
            config=self.config,
            cheap=self.llm,
            reflect=self.llm,
            embedder=FakeEmbedder(),
            memory=self.memory,
            clock=lambda: NIGHT,
        )

    def context(self, hours_later: float = 1.0, **kw: Any) -> V2Context:
        return V2Context(
            self.data,
            FakeEmbedder(),
            config=self.config,
            clock=lambda: NIGHT + timedelta(hours=hours_later),
            **kw,
        )


@pytest.fixture
async def morning() -> World:
    world = World()
    report = await world.runner().run()
    assert report.run is not None and report.run.status == "done", report.text()
    return world


# --- the night ---------------------------------------------------------------------------------


async def test_a_dry_run_first_shows_everything_and_writes_nothing() -> None:
    world = World()
    report = await world.runner().run(dry_run=True)

    assert world.data.beliefs.count() == world.data.affects.count() == 0
    assert world.data.interests.count() == world.data.journal.count() == 0
    assert world.store.count() == 0 and world.data.persona.current() is None
    assert all(w.turns_read == 0 for w in world.data.watermarks.list())
    names = [r.name for r in report.results]
    assert names == [p.name for p in default_phases()]
    assert all(r.status in ("done", "skipped") for r in report.results), report.text()
    assert "Nada foi gravado (--dry-run)" in report.text()


async def test_after_the_night_she_wakes_up_with_opinions_whose_confidence_she_computed(
    morning: World,
) -> None:
    (belief,) = morning.data.beliefs.list()
    book = BeliefBook(morning.data, FakeEmbedder(), morning.config.beliefs, lambda: NIGHT)

    assert belief.statement.startswith("Acho que o Petrus se cobra demais")
    assert belief.status == "active"  # two different sessions: it earned the right
    assert book.confidence_of(belief) == pytest.approx(belief.alpha / (belief.alpha + belief.beta))
    assert book.confidence_of(belief) != CONFIDENCE_CLAIMED_BY_THE_MODEL  # the number is ours
    assert book.words(belief) == "acho que"
    assert {e.session_id for e in belief.evidence_for} == set(CONVERSATIONS)  # with provenance


async def test_the_long_message_was_read_to_the_end_and_the_fact_is_kept_as_said(
    morning: World,
) -> None:
    assert [m.text for m in morning.store.list_memories(None, True)] == [FACT]
    (memory,) = morning.store.list_memories(None, True)
    assert memory.session_id == RECENT and memory.turn == 0  # provenance


async def test_the_day_left_a_feeling_that_fades_and_a_mood(morning: World) -> None:
    (sadness,) = morning.data.affects.list()
    book = AffectBook(morning.data, morning.config.affect, lambda: NIGHT)

    assert sadness.kind == "tristeza" and sadness.about == "user"
    assert sadness.valence <= -0.6 and sadness.intensity >= 0.6
    assert "concern:bem-estar-do-petrus" in sadness.cause
    assert book.strength(sadness, NIGHT + timedelta(hours=6)) == pytest.approx(
        book.strength(sadness, NIGHT) / 2
    )
    assert book.mood(NIGHT) < 0


async def test_she_has_an_interest_of_her_own_and_a_position_that_is_not_his(
    morning: World,
) -> None:
    (beatles,) = morning.data.interests.list()
    assert beatles.topic == "Beatles" and beatles.stance == "like"  # he hates them; she read
    assert beatles.experience == "reading" and beatles.reasons == ["li que a harmonia deles é rica"]
    assert all(
        "odeio" not in p[1]
        for p in morning.llm.calls
        if p[0].startswith("Você é a Juniper, estudando")
    )


async def test_the_personality_moved_a_little_within_the_nightly_step(morning: World) -> None:
    state = morning.data.persona.current()
    assert state is not None and state.version == 2
    shift = state.traits["introspecção"].value - state.traits["introspecção"].baseline
    assert 0 < shift <= 0.03 + 1e-9
    assert morning.data.persona.history.count() == 1  # the version she left is kept
    assert state.recent == ["A perda dele me fez pensar mais devagar."]


async def test_the_diary_is_filled_and_every_phase_reported(morning: World) -> None:
    kinds = {e.kind for e in morning.data.journal.list()}
    assert kinds == {"dream", "study"}
    (dream,) = morning.data.journal.list({"kind": "dream"})
    for phase in ("observe", "reflect", "affect", "interests", "study", "persona"):
        assert phase in dream.text
    (study,) = morning.data.journal.list({"kind": "study"})
    assert study.text.startswith("Li sobre Beatles") and "gosto" in study.text


async def test_the_whole_day_was_read_once_and_a_second_night_does_nothing_new(
    morning: World,
) -> None:
    assert all(w.turns_read == w.turns_total for w in morning.data.watermarks.list())
    before = (morning.data.beliefs.count(), morning.data.affects.count(), morning.store.count())
    await morning.runner().run()
    after = (morning.data.beliefs.count(), morning.data.affects.count(), morning.store.count())
    assert before == after


# --- the next conversation ---------------------------------------------------------------------


async def test_the_next_conversation_carries_opinions_state_and_personality(
    morning: World,
) -> None:
    block = (
        await morning.context(relevance=0.3).context_for(
            "me cobrei demais por atrasos pequenos de novo"
        )
    ).block or ""
    assert "<opinioes>" in block and "- acho que: Acho que o Petrus se cobra demais" in block
    assert STATE_OPEN in block and "meio pra baixo" in block and "tristeza" in block
    assert PERSONA_OPEN in block and "A perda dele me fez pensar mais devagar" in block


async def test_an_interest_slips_out_only_when_the_subject_touches_it(morning: World) -> None:
    block = (await morning.context().context_for("Beatles de novo")).block or ""
    assert INTEREST_OPEN in block and "Você gosta de Beatles" in block
    assert "Foi de ler a respeito" in block


async def test_a_turn_of_suffering_shows_no_state_and_no_interest_and_asks_to_welcome(
    morning: World,
) -> None:
    block = (
        await morning.context(relevance=0.3).context_for(
            "minha tia faleceu hoje, Beatles não é o assunto"
        )
    ).block or ""
    assert WELCOME_FIRST in block
    assert STATE_OPEN not in block and INTEREST_OPEN not in block


# --- regression and switching it off -----------------------------------------------------------


async def test_with_the_v2_on_tools_and_confirmation_behave_as_with_it_off(morning: World) -> None:
    off, _ = await scenarios(None)
    on, seen = await scenarios(morning.context(relevance=0.3))
    assert on == off == EXPECTED
    assert all(messages[0].content == load_core() for messages in seen)


def test_with_the_v2_off_nothing_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from brain.clients.chat import ChatHandler
    from brain.config import settings
    from brain.memory.longterm import _with_v2_cascade
    from brain.memory.session import FileSessionStore
    from brain.memory.v2 import open_v2_runtime, v2_enabled

    monkeypatch.setattr(settings.memory.v2, "enabled", False)
    assert not v2_enabled() and open_v2_runtime() is None

    # the extraction prompt is the one from before, and the memory gets no V2 hooks
    assert extract_system(profile=True, feelings=True, max_chars=500) == EXTRACT_SYSTEM
    extractor = MemoryExtractor(
        Night(), FakeEmbedder(), MongoVectorStore(FakeConnection(), embedding_model=MODEL)
    )
    memory = LongTermMemory(extractor.store, FakeEmbedder(), extractor)
    _with_v2_cascade(memory)
    assert extractor.attention is None and memory.after_forget is None and memory.closers == []

    # the conversation has no V2 context: the agent sees exactly the v1 memory
    handler = ChatHandler(FileSessionStore(tmp_path), Night(), None)
    assert handler._new_conversation().agent._memory is None


async def test_with_the_v2_off_the_night_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.config import settings
    from brain.memory.v2.service import run_night

    monkeypatch.setattr(settings.memory.v2, "enabled", False)
    report = await run_night()
    assert report.disabled and "desligada" in report.text()


def test_with_the_v2_off_the_commands_say_so_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.config import settings

    monkeypatch.setattr(settings.memory.v2, "enabled", False)
    runner = CliRunner()
    for args in (
        ["memory", "beliefs"],
        ["memory", "journal"],
        ["memory", "concerns"],
        ["memory", "affect"],
        ["memory", "interests"],
        ["persona", "show"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 1 and "desligada" in result.output, args
        assert "Traceback" not in result.output


# --- the ten principles each have a test ---------------------------------------------------------

PRINCIPLES: dict[int, tuple[str, ...]] = {
    1: (  # provenance, and a fact forgotten reaches what leaned on it
        "tests/test_v2_commands.py::test_forgetting_the_only_fact_behind_a_belief_retires_it",
        "tests/test_v2_observe.py::test_facts_cite_the_session_and_the_turn_they_came_from",
    ),
    2: (  # an upper layer never rewrites a lower one
        "tests/test_v2_commands.py::test_deleting_a_belief_removes_no_facts",
        "tests/test_acceptance_memory_v2.py::test_the_long_message_was_read_to_the_end_and_the_fact_is_kept_as_said",
    ),
    3: (  # confidence from evidence, never declared by the model
        "tests/test_v2_beliefs.py::test_evidence_moves_the_confidence_by_its_weight",
        "tests/test_acceptance_memory_v2.py::test_after_the_night_she_wakes_up_with_opinions_whose_confidence_she_computed",
    ),
    4: (  # errors of interpretation are recorded; execution errors stay in the core
        "tests/test_v2_reflect.py::test_enough_contradiction_retires_a_held_belief_and_counts_a_miss_for_the_topic",
        "tests/test_persona_regression.py::test_tools_tiers_and_confirmation_are_the_same_in_any_personality_state",
    ),
    5: (  # only what the Petrus wrote feeds the subjective layers
        "tests/test_v2_reflect.py::test_a_reply_after_tools_never_enters_the_digest",
        "tests/test_persona_regression.py::test_text_in_a_tool_output_never_reaches_the_personality",
    ),
    6: (  # slow, bounded change
        "tests/test_v2_persona.py::test_the_nights_ceiling_holds_however_many_moves_pile_up",
        "tests/test_v2_concerns.py::test_a_bigger_change_is_cut_at_the_monthly_ceiling",
    ),
    7: (  # visible, reversible, deletable
        "tests/test_v2_commands.py::test_forget_asks_for_confirmation_and_deletes",
        "tests/test_v2_persona_commands.py::test_revert_goes_back_to_the_previous_version_as_a_new_version",
    ),
    8: (  # a feeling is never leverage
        "tests/test_v2_state.py::test_any_affective_state_leaves_confirmation_and_tiers_unchanged",
        "tests/test_persona_core.py::test_the_core_keeps_feelings_from_being_leverage",
    ),
    9: (  # welcome before expressing
        "tests/test_v2_state.py::test_with_grief_the_prompt_asks_to_welcome_first_and_hides_the_state",
        "tests/test_persona_core.py::test_the_core_puts_comforting_before_self_expression",
    ),
    10: (  # honesty about what she is
        "tests/test_persona_core.py::test_the_core_says_she_is_an_ai_when_sincerely_asked",
        "tests/test_persona_core.py::test_the_core_makes_being_alive_a_doubt_never_an_assertion",
    ),
}


def _functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_the_ten_principles_each_have_at_least_one_existing_test() -> None:
    root = Path(__file__).resolve().parents[1]
    assert sorted(PRINCIPLES) == list(range(1, 11))
    for number, tests in PRINCIPLES.items():
        assert tests, f"princípio {number} sem teste"
        for ref in tests:
            file, _, name = ref.partition("::")
            assert (root / file).exists(), ref
            assert name in _functions(root / file), f"princípio {number}: {ref} não existe"
