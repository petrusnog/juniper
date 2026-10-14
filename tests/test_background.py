"""T-063: guardar em segundo plano, a cada resposta, sem atrasar nem poluir a conversa."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from brain.clients import cli
from brain.config import ExtractionSettings, Settings, settings
from brain.core.session import Turn
from brain.memory.background import BackgroundExtractor, is_trivial
from brain.memory.extraction import (
    CONTEXT_MARK,
    ExtractionResult,
    MemoryExtractor,
    build_transcript,
    extract_system,
)
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoProfileStore, MongoVectorStore
from brain.memory.vector import Memory
from tests.test_extraction import MODEL, FakeEmbedder, ScriptedLLM, mems
from tests.test_vector_store import FakeConnection


class Recorder:
    """Stands in for LongTermMemory: records each extraction call, optionally slow or failing."""

    def __init__(self, delay: float = 0.0, fail_first: bool = False) -> None:
        self.calls: list[tuple[list[Turn], int]] = []
        self.delay = delay
        self.fail_first = fail_first

    async def extract(
        self, turns: list[Turn], session_id: str | None = None, context: int = 0
    ) -> ExtractionResult:
        self.calls.append((list(turns), context))
        await asyncio.sleep(self.delay)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("banco caiu")
        result = ExtractionResult()
        result.added.append(Memory("x", "fact", [1.0], MODEL))
        return result


T = Turn


# --- what is worth a call -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["ok", "Valeu!", "Bom dia!", "sim, pode", "obrigada :)", "kkk", "  ", "tchau", "Oi, tudo bem?"],
)
def test_pleasantries_are_skipped(text: str) -> None:
    assert is_trivial(text)


@pytest.mark.parametrize(
    "text",
    [
        "Prefiro ZSH.",
        "Moro em Floripa",
        "Sim, pode marcar a castração do Ollie",
        "meu gato se chama Ollie",
        "e a Mel?",
        "Olá, gostaria de agendar no próximo sábado a castração do meu gatinho",
    ],
)
def test_anything_that_could_carry_a_fact_is_not(text: str) -> None:
    assert not is_trivial(text)


# --- the worker ---------------------------------------------------------------------------


async def test_submit_returns_at_once_and_the_work_happens_meanwhile() -> None:
    rec = Recorder(delay=0.2)
    bg = BackgroundExtractor(rec, lambda: "s1")
    started = time.perf_counter()
    bg.submit(T("Meu gato se chama Ollie", "Fofo!"))
    assert time.perf_counter() - started < 0.05  # never waits for the LLM
    assert bg.busy
    summary = await bg.finish()
    assert summary.runs == 1 and summary.added == 1 and not bg.busy


async def test_turns_that_arrive_meanwhile_are_batched_with_the_previous_one_as_context() -> None:
    rec = Recorder(delay=0.1)
    bg = BackgroundExtractor(rec, lambda: "s1")
    bg.submit(T("Meu gato se chama Ollie", "Fofo!"))
    await asyncio.sleep(0.02)  # the first extraction is running
    bg.submit(T("Ele tem 3 anos", "Ah, é novinho"))
    bg.submit(T("E gosta de ração de peixe", "Boa"))
    await bg.finish()
    assert len(rec.calls) == 2  # one run at a time: the two late turns went together
    first, second = rec.calls
    assert first == ([T("Meu gato se chama Ollie", "Fofo!")], 0)
    assert second[1] == 1 and [t.user for t in second[0]] == [
        "Meu gato se chama Ollie",  # context: so "ele" still means the cat
        "Ele tem 3 anos",
        "E gosta de ração de peixe",
    ]


async def test_pleasantries_cost_nothing_but_still_serve_as_context() -> None:
    rec = Recorder()
    bg = BackgroundExtractor(rec, lambda: "s1")
    bg.submit(T("Valeu!", "De nada"))
    summary = await bg.finish()
    assert rec.calls == [] and summary.runs == 0
    bg.submit(T("Moro em Florianópolis", "Que massa"))
    await bg.finish()
    assert rec.calls[0] == ([T("Valeu!", "De nada"), T("Moro em Florianópolis", "Que massa")], 1)


async def test_a_failing_extraction_never_reaches_the_chat_and_the_next_one_still_runs() -> None:
    rec = Recorder(fail_first=True)
    bg = BackgroundExtractor(rec, lambda: "s1")
    bg.submit(T("Meu gato se chama Ollie", "Fofo!"))
    await asyncio.sleep(0.05)
    bg.submit(T("Moro em Florianópolis", "Que massa"))
    summary = await bg.finish()
    assert summary.runs == 2 and summary.added == 1  # the second one saved
    assert summary.error == "banco caiu"


async def test_finish_with_nothing_queued_returns_at_once() -> None:
    summary = await BackgroundExtractor(Recorder(), lambda: None).finish()
    assert summary.runs == 0 and not summary.saved_something and summary.error is None


async def test_the_session_id_travels_with_each_extraction() -> None:
    seen: list[str | None] = []

    class M(Recorder):
        async def extract(self, turns: Any, session_id: Any = None, context: int = 0) -> Any:
            seen.append(session_id)
            return await super().extract(turns, session_id, context)

    bg = BackgroundExtractor(M(), lambda: "20261014-aaaaaa")
    bg.submit(T("Meu gato se chama Ollie", "Fofo!"))
    await bg.finish()
    assert seen == ["20261014-aaaaaa"]


# --- context in the extraction itself -----------------------------------------------------


def test_context_turns_are_marked_for_the_llm() -> None:
    turns = [T("Meu gato é o Ollie", "Fofo!"), T("Ele tem 3 anos", "Novinho")]
    text = build_transcript(turns, 20, context=1)
    assert text.splitlines() == [
        f"{CONTEXT_MARK}Usuário: Meu gato é o Ollie",
        f"{CONTEXT_MARK}Juniper: Fofo!",
        "Usuário: Ele tem 3 anos",
        "Juniper: Novinho",
    ]
    assert CONTEXT_MARK not in build_transcript(turns, 20)
    assert "(contexto)" in extract_system(profile=False)  # the prompt explains the mark


def test_context_survives_the_turn_window() -> None:
    turns = [T(f"msg {i}", "") for i in range(6)]
    text = build_transcript(turns, max_turns=3, context=4)  # 4 old; the window keeps msg 3..5
    assert text.splitlines() == [
        f"{CONTEXT_MARK}Usuário: msg 3",
        "Usuário: msg 4",
        "Usuário: msg 5",
    ]


async def test_the_pet_hidden_inside_an_appointment_request_is_kept() -> None:
    """The case that started this: scheduling a vet visit reveals the cat's name."""
    conn = FakeConnection()
    store = MongoVectorStore(conn, embedding_model=MODEL)
    profiles = MongoProfileStore(conn)
    llm = ScriptedLLM(
        extract={**mems(("Petrus tem um gato chamado Ollie.", "fact")), "profile": None}
    )
    memory = LongTermMemory(
        store,
        FakeEmbedder(),
        MemoryExtractor(llm, FakeEmbedder(), store, ExtractionSettings(), profiles),
        profiles,
    )
    bg = BackgroundExtractor(memory, lambda: "s1")
    bg.submit(
        T(
            "Gostaria de agendar no sábado a castração do meu gatinho Ollie, das 10h às 12h",
            "Marcado",
        )
    )
    summary = await bg.finish()
    assert summary.added == 1
    assert [m.text for m in store.list_memories()] == ["Petrus tem um gato chamado Ollie."]
    # and the prompt it was asked with teaches exactly this reading between the lines
    system = llm.calls[0][0]
    assert (
        "PEDIDOS PONTUAIS" in system
        and "DENTRO do pedido" in system
        and "bichos de estimação" in system
    )


# --- configuration ------------------------------------------------------------------------


def test_the_default_is_background_per_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ExtractionSettings().mode == "turn"
    monkeypatch.setenv("JUNIPER_MEMORY__EXTRACTION__MODE", "exit")
    assert Settings(_env_file=None).memory.extraction.mode == "exit"  # type: ignore[call-arg]
    monkeypatch.setenv("JUNIPER_MEMORY__EXTRACTION__MODE", "sempre")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


# --- the chat -----------------------------------------------------------------------------


class _Printer:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.spun: list[str | None] = []

    def say(self, text: str, style: str | None = None) -> None:
        self.said.append(text)

    def busy(self, text: str) -> None:
        self.spun.append(text)

    def idle(self) -> None:
        self.spun.append(None)

    def timing(self, text: str) -> None:
        self.said.append(f"⏱ {text}")


async def test_the_chat_summarizes_once_at_the_end() -> None:
    from brain.core.agent import Agent
    from brain.core.dispatcher import Dispatcher
    from tests.test_agent import FakeProvider, say

    rec = Recorder(delay=0.05)
    bg = BackgroundExtractor(rec, lambda: "s1")
    agent = Agent(FakeProvider([say("ok")]), Dispatcher(conversational=True))
    await agent.run("meu gato se chama Ollie")
    for turn in agent.drain_turns():
        bg.submit(turn)
    printer = _Printer()
    await cli.remember_conversation(agent, None, printer, bg)  # type: ignore[arg-type]
    assert printer.said == ["Guardei 1 coisa sobre você para a próxima."]
    assert printer.spun == ["guardando o que aprendi...", None]  # it was still working: said so


async def test_nothing_to_wait_for_means_no_spinner_and_no_noise() -> None:
    from brain.core.agent import Agent
    from brain.core.dispatcher import Dispatcher
    from tests.test_agent import FakeProvider

    printer = _Printer()
    agent = Agent(FakeProvider([]), Dispatcher(conversational=True))
    await cli.remember_conversation(
        agent,
        None,
        printer,
        BackgroundExtractor(Recorder(), lambda: None),  # type: ignore[arg-type]
    )
    assert printer.said == [] and printer.spun == [None]


async def test_a_background_failure_is_reported_once_at_the_end() -> None:
    from brain.core.agent import Agent
    from brain.core.dispatcher import Dispatcher
    from tests.test_agent import FakeProvider

    class Down(Recorder):
        async def extract(self, turns: Any, session_id: Any = None, context: int = 0) -> Any:
            result = ExtractionResult()
            result.error = "O MongoDB recusou a operação."
            return result

    bg = BackgroundExtractor(Down(), lambda: "s1")
    bg.submit(T("Meu gato se chama Ollie", "Fofo!"))
    printer = _Printer()
    agent = Agent(FakeProvider([]), Dispatcher(conversational=True))
    await cli.remember_conversation(agent, None, printer, bg)  # type: ignore[arg-type]
    assert printer.said == ["Não guardei memórias desta conversa: O MongoDB recusou a operação."]


def test_settings_object_has_the_mode() -> None:
    assert settings.memory.extraction.mode in ("turn", "exit")
