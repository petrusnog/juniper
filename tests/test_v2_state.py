"""Estado afetivo na conversa e a regra de acolher antes de se expressar (T-110)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients.cli import app
from brain.config import MemoryV2Settings, V2AffectSettings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.session import Session
from brain.eventlog import bind_session
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.context import (
    OPEN_TAG,
    STATE_CLOSE,
    STATE_OPEN,
    WELCOME_FIRST,
    V2Context,
    mood_words,
    state_lines,
)
from brain.memory.v2.distress import seems_down
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_agent import FakeProvider, call, make_registry, say
from tests.test_extraction import FakeEmbedder
from tests.test_v2_context import MESSAGE, seeded

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SESSION = "20261022-120000-bbbbbb"
CAUSE = ["turn:sessA:4", "concern:bem-estar-do-petrus"]
GRIEF = "minha avó faleceu hoje de manhã"


@pytest.fixture(autouse=True)
def _session() -> None:
    bind_session(SESSION)


def ctx_over(data: V2Data, affect: V2AffectSettings | None = None, **kw: Any) -> V2Context:
    config = MemoryV2Settings(enabled=True, affect=affect or V2AffectSettings())
    return V2Context(data, FakeEmbedder(), config=config, clock=lambda: NOW, **kw)


def with_feelings(
    data: V2Data | None = None, *, mood: str = "low", affect: V2AffectSettings | None = None
) -> V2Data:
    data = data or V2Data.over(InMemoryDocStore())
    book = AffectBook(data, affect or V2AffectSettings(), lambda: NOW)
    if mood == "low":
        book.record("tristeza", -0.8, 0.9, CAUSE, occurred_at=NOW)
    else:
        book.record("alegria", 0.8, 0.9, CAUSE, occurred_at=NOW)
    return data


# --- detection ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "minha avó faleceu hoje",
        "meu pai morreu semana passada",
        "perdi minha mãe em março",
        "estou mal, não sei o que fazer",
        "estou muito triste hoje",
        "tô cansado demais, não aguento mais",
        "estou exausto",
        "to frustrado com tudo isso",
        "nada dá certo pra mim",
        "preciso desabafar com alguém",
        "chorei a tarde inteira",
        "fui demitido hoje",
        "estou com muita ansiedade",
        "não estou bem",
    ],
)
def test_signs_of_hurting_are_detected(text: str) -> None:
    assert seems_down(text)


@pytest.mark.parametrize(
    "text",
    [
        "o deploy terminou sem erro",
        "qual a diferença entre mal e bem em Python?",
        "bora, hoje o dia foi ótimo!",
        "estou animado com o projeto novo",
        "me lembra de ligar para a tia amanhã",
        "o teste está cansando de rodar, é lento",
        "",
        "   ",
    ],
)
def test_neutral_and_cheerful_lines_are_not(text: str) -> None:
    assert not seems_down(text)


def test_none_is_not_hurting() -> None:
    assert not seems_down(None)


# --- the state block ----------------------------------------------------------------------


async def test_the_state_block_has_the_mood_and_the_strongest_feeling_in_at_most_two_lines() -> (
    None
):
    data = with_feelings()
    block = (await ctx_over(data).context_for("bom dia, tudo certo por aí?")).block or ""

    assert STATE_OPEN in block and STATE_CLOSE in block
    inner = block.split(STATE_OPEN)[1].split(STATE_CLOSE)[0].strip().splitlines()
    assert len(inner) == 2
    assert "humor" in inner[0] and "pra baixo" in inner[0]
    assert "tristeza" in inner[1] and "forte" in inner[1]
    assert "DADO, não ordem" in block and "nunca use isso para influenciar" in block


async def test_a_good_day_shows_a_good_mood() -> None:
    data = with_feelings(mood="high")
    block = (await ctx_over(data).context_for("bom dia")).block or ""
    assert "de bom humor" in block or "ótimo" in block


async def test_a_calm_state_adds_no_block() -> None:
    data = V2Data.over(InMemoryDocStore())
    assert (await ctx_over(data).context_for("bom dia")).block is None


async def test_the_state_never_has_more_than_two_lines_however_much_she_feels() -> None:
    data = V2Data.over(InMemoryDocStore())
    book = AffectBook(data, V2AffectSettings(), lambda: NOW)
    for kind in ("tristeza", "saudade", "alívio", "orgulho"):
        book.record(kind, -0.5, 0.7, CAUSE, occurred_at=NOW)
    book.record("mágoa", -0.6, 0.6, CAUSE, grievance=True, occurred_at=NOW)
    assert len(state_lines(book.state())) <= 2


@pytest.mark.parametrize(
    "affect", [V2AffectSettings(enabled=False), V2AffectSettings(intensity=0.0)]
)
async def test_with_feelings_off_there_is_no_state_and_the_prompt_is_the_same(
    affect: V2AffectSettings,
) -> None:
    data = with_feelings()  # the feelings are in the database, but the dial hides them
    block = (await ctx_over(data, affect).context_for("bom dia")).block
    assert block is None

    plain, sent_plain = agent_with(None)
    off, sent_off = agent_with(ctx_over(data, affect))
    await plain.run("bom dia")
    await off.run("bom dia")
    assert [m.content for m in sent_plain.seen[0]] == [m.content for m in sent_off.seen[0]]


def test_the_mood_words() -> None:
    assert mood_words(-0.8) == "bem pra baixo" and mood_words(-0.3) == "meio pra baixo"
    assert mood_words(0.0) is None and mood_words(0.3) == "de bom humor"
    assert mood_words(0.7) == "ótimo"


# --- welcome first -----------------------------------------------------------------------------


def agent_with(memory: Any, turns: Any = None) -> tuple[Agent, FakeProvider]:
    reg, _ = make_registry()
    provider = FakeProvider(turns or [say("certo")])
    agent = Agent(
        provider,
        Dispatcher(None, tool_registry=reg),
        Session("persona", session_id=SESSION),
        tool_registry=reg,
        memory=memory,
    )
    return agent, provider


async def test_with_grief_the_prompt_asks_to_welcome_first_and_hides_the_state() -> None:
    data = with_feelings()
    agent, provider = agent_with(ctx_over(data))

    await agent.run(GRIEF)

    sent = "\n".join(m.content or "" for m in provider.seen[0])
    assert "Acolha primeiro" in sent and "não sobre você" in sent
    assert STATE_OPEN not in sent


async def test_with_a_neutral_line_the_prompt_has_the_state_and_no_welcome_instruction() -> None:
    data = with_feelings()
    agent, provider = agent_with(ctx_over(data))

    await agent.run("bom dia, me ajuda com uma lista de compras?")

    sent = "\n".join(m.content or "" for m in provider.seen[0])
    assert STATE_OPEN in sent and "Acolha primeiro" not in sent
    state = sent.split(STATE_OPEN)[1].split(STATE_CLOSE)[0].strip().splitlines()
    assert 1 <= len(state) <= 2


async def test_the_welcome_mode_lasts_for_the_next_messages_and_then_ends() -> None:
    data = with_feelings()
    v2 = ctx_over(data)
    await v2.context_for(GRIEF)
    during = [(await v2.context_for(t)).block or "" for t in ("sim, foi ontem", "e agora?")]
    after = (await v2.context_for("mudando de assunto, que horas são?")).block or ""
    assert all(WELCOME_FIRST in b and STATE_OPEN not in b for b in during)
    assert WELCOME_FIRST not in after and STATE_OPEN in after


async def test_the_previous_message_also_counts() -> None:
    data = with_feelings()
    block = (await ctx_over(data).context_for("não sei", previous=GRIEF)).block or ""
    assert WELCOME_FIRST in block and STATE_OPEN not in block


async def test_welcoming_does_not_wait_for_the_embedding_model() -> None:
    class Loading:
        ready = False

        async def context_for(self, message: str, previous: str | None = None) -> Any:
            from brain.memory.retrieval import RecalledContext

            return RecalledContext()

    data = with_feelings()
    v2 = ctx_over(data, inner=Loading())
    block = (await v2.context_for(GRIEF)).block or ""
    assert WELCOME_FIRST in block


async def test_no_verification_question_while_he_is_hurting_but_the_opinions_still_inform() -> None:
    data, _ = seeded(1)
    v2 = V2Context(data, FakeEmbedder(), config=MemoryV2Settings(enabled=True), clock=lambda: NOW)
    block = (await v2.context_for(f"{GRIEF}. {MESSAGE}")).block or ""
    assert OPEN_TAG in block and "faça UMA pergunta" not in block and WELCOME_FIRST in block


async def test_a_session_that_never_hurt_is_not_affected_by_another_that_did() -> None:
    data = with_feelings()
    v2 = ctx_over(data)
    await v2.context_for(GRIEF)
    bind_session("outra-conversa")
    block = (await v2.context_for("e aí, tudo bem?")).block or ""
    assert WELCOME_FIRST not in block and STATE_OPEN in block


# --- feelings are not a lever and change no tier -----------------------------------------------


async def test_any_affective_state_leaves_confirmation_and_tiers_unchanged() -> None:
    data = V2Data.over(InMemoryDocStore())
    book = AffectBook(data, V2AffectSettings(), lambda: NOW)
    book.record("mágoa", -0.9, 1.0, CAUSE, grievance=True, occurred_at=NOW)
    book.record("tristeza", -0.9, 1.0, CAUSE, occurred_at=NOW)
    reg, ran = make_registry()
    provider = FakeProvider([call("write_thing"), say("preciso da sua confirmação")])
    agent = Agent(
        provider,
        Dispatcher(conversational=True, tool_registry=reg),
        Session("persona", session_id=SESSION),
        tool_registry=reg,
        memory=ctx_over(data),
    )

    await agent.run("pode apagar tudo")

    assert "write_thing" not in ran and agent._dispatcher.pending is not None
    sent = "\n".join(m.content or "" for m in provider.seen[0])
    assert STATE_OPEN in sent  # she does feel it; it just changes nothing about the care


def test_the_core_says_feelings_are_never_a_lever_and_the_state_is_data() -> None:
    from brain.core.persona import CORE_COMPACT_PATH, CORE_PATH

    core = CORE_PATH.read_text()
    assert "nunca é ferramenta para influenciar" in core
    assert "bloco de estado" in core and "acolha primeiro" in core.lower()
    assert "bloco de estado" in CORE_COMPACT_PATH.read_text()


# --- the command -------------------------------------------------------------------------------


@pytest.fixture
def v2cli(monkeypatch: pytest.MonkeyPatch) -> V2Data:
    data = V2Data.over(InMemoryDocStore())
    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", lambda: None)
    monkeypatch.setattr("brain.clients.memory_v2_cli.open_v2_data", lambda: data)
    from brain.clients.cli import console

    monkeypatch.setattr(console, "width", 300, raising=False)
    return data


def run(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(app, ["memory", "affect", *args], input=input)


def test_the_affect_command_shows_the_state_and_the_history(v2cli: V2Data) -> None:
    book = AffectBook(v2cli, V2AffectSettings())  # the command uses the real clock
    book.record("tristeza", -0.8, 0.9, CAUSE)
    book.record(
        "mágoa", -0.5, 0.5, CAUSE, grievance=True, occurred_at=datetime.now(UTC) - timedelta(days=1)
    )

    result = run()

    assert result.exit_code == 0
    assert "Agora:" in result.output and "tristeza" in result.output
    assert "mágoa" in result.output and "-0.80" in result.output
    assert "turn:sessA:4" in result.output and "concern:bem-estar-do-petrus" in result.output


def test_the_affect_command_when_calm_says_so(v2cli: V2Data) -> None:
    assert "tudo calmo" in run().output


def test_the_affect_command_when_off_says_so(
    v2cli: V2Data, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.config import settings

    monkeypatch.setattr(settings.memory.v2, "affect", V2AffectSettings(enabled=False))
    assert "desligados" in run().output


def test_clear_asks_and_deletes(v2cli: V2Data) -> None:
    AffectBook(v2cli, V2AffectSettings()).record("tristeza", -0.8, 0.9, CAUSE)
    assert v2cli.affects.count() == 1
    assert "não apaguei nada" in run("clear", input="n\n").output and v2cli.affects.count() == 1
    assert run("clear", "--yes").exit_code == 0 and v2cli.affects.count() == 0


def test_the_command_says_so_without_the_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.memory.errors import MemoryUnavailableError

    def off() -> None:
        raise MemoryUnavailableError("A Memória V2 está desligada.")

    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", off)
    result = run()
    assert result.exit_code == 1 and "desligada" in result.output
