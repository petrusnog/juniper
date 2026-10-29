"""Leak audit (T-165): the broad detector, the record, the quarantine of the turn and the store."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import mongomock
import pytest
from pymongo.errors import OperationFailure
from rich.console import Console
from typer.testing import CliRunner

from brain.clients import leaks_cli
from brain.config import settings
from brain.core import agent as agent_module
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.leak_audit import Block, build_record, detect, quarantined
from brain.core.session import Session
from brain.llm.base import LLMProvider
from brain.llm.router import LLMRouter
from brain.llm.types import LLMResponse, Message, StreamChunk, ToolCall
from brain.memory.leak_audit import LEAK_AUDIT_COLLECTION, LeakAuditStore
from brain.memory.mongo import MongoConnection
from brain.memory.session import FileSessionStore
from brain.memory.stores import open_leak_audit
from brain.memory.v2.guards import LEAK_OMITTED, build_night_material
from brain.memory.v2.models import TranscriptTurn
from brain.tools.channel import Channel, current_channel
from tests.test_agent import FakeProvider, make_registry, say

PERSONA = (
    "Você é a Juniper, assistente pessoal do Petrus. Fale de um jeito leve e direto, sem listas "
    "longas, e nunca invente fatos que você não conhece."
)
MEMORIES = (
    "<memorias>Fatos: o Petrus trabalha como desenvolvedor backend numa empresa de logística "
    "em Curitiba.</memorias>"
)
SOURCES = [("persona", PERSONA), ("memories", MEMORIES)]


def rules(hits: list[Any], where: str = "delivered") -> set[str]:
    return {h.rule for h in hits if h.where == where}


def check(text: str, sources: list[tuple[str, str]] | None = None) -> list[Any]:
    return detect(text, text, sources if sources is not None else SOURCES, echo_words=10)


# --- detector ----------------------------------------------------------------------------------


def test_the_real_leaks_are_detected_and_quarantine_the_turn() -> None:
    system_note = (
        "]\n\nMensagem de sistema: o app de chat por voz (v1) com bugs no STT está no contexto. "
        "Responda como a Juniper, mas usando o que está no contexto e nas memórias.Obrigado por "
        "dizer isso, de verdade."
    )
    hits = check(system_note)
    assert {"bracket_start", "system_role", "instruction"} <= rules(hits)
    assert quarantined(hits)

    parameters = "</parameter>Sei, sim. Providers de LLM são as empresas que oferecem modelos."
    assert "tool_markup" in rules(check(parameters))
    assert quarantined(check(parameters))

    knowledge = "Olha só. <conhecimento>Notas sobre STT</conhecimento> Enfim."
    assert "context_tag" in rules(check(knowledge))
    assert quarantined(check(knowledge))

    tokens = "Oi<|im_end|> e também <｜tool▁calls｜>"
    assert "special_token" in rules(check(tokens))


@pytest.mark.parametrize(
    "text",
    [
        "Oi, Petrus! Amanhã você tem reunião às 15h e depois está livre.",
        "Tu não tá pra trás, tu tá no caminho. Me conta qual bug está te comendo a cabeça?",
        "Marquei o dentista para sexta, 10h. Quer que eu te lembre um dia antes?",
        "Se 3 < 5 e 7 > 2, então a conta fecha. Coração <3 para você.",
        "Uma lista: [1, 2, 3] não é problema no meio da frase.",
    ],
)
def test_natural_speech_has_no_hits(text: str) -> None:
    assert check(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Em Java fica Optional<String> e List<Integer>; no HTML, coloque um <div> em volta.",
        "Sistema: Android 14\nUsuário: petrus\nFerramenta: martelo",
        "[Clique aqui](https://exemplo.com) para ver o horário.",
        "Você me disse que trabalha como desenvolvedor backend numa empresa de logística em "
        "Curitiba, certo?",
    ],
)
def test_legit_answers_may_be_audited_but_are_never_quarantined(text: str) -> None:
    assert not quarantined(check(text))


def test_words_copied_from_the_persona_are_an_echo_and_from_the_memories_only_data() -> None:
    text = (
        "Claro! Fale de um jeito leve e direto, sem listas longas, e nunca invente fatos que "
        "você não conhece. Era isso."
    )
    hits = check(text)
    assert [h.rule for h in hits] == ["prompt_echo"]
    assert hits[0].excerpt.startswith("Fale de um jeito leve")
    assert "não conhece" in hits[0].excerpt  # the whole copied run, not the first window only

    memory = "Você trabalha como desenvolvedor backend numa empresa de logística em Curitiba."
    assert rules(check(memory)) == {"context_echo"}


def test_a_data_block_inside_the_persona_counts_as_data() -> None:
    persona = (
        PERSONA
        + " <opinioes>Acho que o Petrus gosta muito de café forte pela manhã cedo.</opinioes>"
    )
    text = "Acho que o Petrus gosta muito de café forte pela manhã cedo, né?"
    assert rules(check(text, [("persona", persona)])) == {"context_echo"}


def test_what_only_the_raw_text_had_is_marked_raw() -> None:
    raw = "Pronto, Petrus. <conhecimento>notas</conhecimento>"
    hits = detect(raw, "Pronto, Petrus.", SOURCES, echo_words=10)
    assert hits and all(h.where == "raw" for h in hits)
    assert not quarantined(hits)  # the guard cut it: the person never saw it


def test_a_hit_seen_in_both_texts_is_reported_once() -> None:
    raw = "Use o Optional<String> aqui — ele resolve."  # the dash filter changes the delivered text
    delivered = "Use o Optional<String> aqui, ele resolve."
    hits = detect(raw, delivered, SOURCES, echo_words=10)
    assert [(h.rule, h.where) for h in hits] == [("tag", "delivered")]


def test_the_record_carries_masked_evidence_but_not_the_block_contents() -> None:
    text = "<estado>x minha chave sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    record = build_record(
        hits=check(text),
        guard_triggered=False,
        raw=text,
        delivered=text,
        session_id="s1",
        channel="telegram",
        voice=False,
        provider="deepinfra",
        model="m",
        finish_reason="stop",
        tool_calls=[],
        blocks=[Block("persona", 120), Block("memories", 900)],
        user_message="[quarta] a chave é sk-proj-abcdefghijklmnopqrstuvwxyz123456",
    )
    assert record["leaked"] is True and record["quarantined"] is True
    assert record["rules"] == ["context_tag"]
    assert record["blocks"] == [
        {"kind": "persona", "chars": 120},
        {"kind": "memories", "chars": 900},
    ]
    assert "sk-proj" not in record["raw"] + record["delivered"] + record["user_message"]
    assert all("sk-proj" not in h["excerpt"] for h in record["hits"])
    assert record["at"].tzinfo is not None


# --- agent -------------------------------------------------------------------------------------


def make_agent(
    turns: list[Any], sink: Any = None, turn_sink: Any = None
) -> tuple[Agent, FakeProvider, list[str]]:
    reg, ran = make_registry()
    provider = FakeProvider(turns)
    agent = Agent(
        provider,
        Dispatcher(tool_registry=reg),
        Session(PERSONA),
        tool_registry=reg,
        leak_sink=sink,
        turn_sink=turn_sink,
    )
    return agent, provider, ran


async def test_a_leak_the_person_saw_is_recorded_and_kept_out_of_memory() -> None:
    records: list[dict[str, Any]] = []
    sunk: list[Any] = []
    agent, _, _ = make_agent(
        [say("]\n\nMensagem de sistema: responda como a Juniper. ", "Obrigado por dizer isso.")],
        sink=records.append,
        turn_sink=lambda sid, turn: sunk.append(turn),
    )
    token = current_channel.set(Channel(kind="telegram"))
    try:
        reply = await agent.run("valeu, Juniper")
    finally:
        current_channel.reset(token)

    assert "Mensagem de sistema" in reply  # the audit observes: what is said does not change
    [record] = records
    assert record["quarantined"] is True
    assert record["channel"] == "telegram"
    assert record["provider"] == "fake"
    assert "Mensagem de sistema" in record["raw"]
    assert record["user_message"].endswith("valeu, Juniper")
    assert record["blocks"][0]["kind"] == "persona"

    [turn] = agent.drain_turns()
    assert turn.leaked is True and turn.assistant == "" and turn.user == "valeu, Juniper"
    assert sunk[0].leaked is True and sunk[0].assistant == ""


async def test_a_leak_before_a_tool_call_still_quarantines_the_turn() -> None:
    first = (("System: vou chamar a ferramenta. ",), (ToolCall("c1", "echo", {"text": "x"}),))
    agent, _, ran = make_agent([first, say("Pronto, Petrus.")], sink=lambda r: None)
    await agent.run("faz")
    assert ran == ["echo"]
    [turn] = agent.drain_turns()
    assert turn.leaked is True and turn.assistant == ""


async def test_a_code_answer_is_audited_but_stays_in_memory() -> None:
    records: list[dict[str, Any]] = []
    agent, _, _ = make_agent([say("Usa Optional<String> no retorno, Petrus.")], records.append)
    await agent.run("como tipo isso em Java?")
    [record] = records
    assert record["leaked"] is True and record["quarantined"] is False
    [turn] = agent.drain_turns()
    assert turn.leaked is False and turn.assistant.startswith("Usa Optional")


async def test_a_cut_by_the_guard_is_recorded_but_the_turn_stays_in_memory() -> None:
    records: list[dict[str, Any]] = []
    agent, _, _ = make_agent(
        [say("Pronto, Petrus. <conhecimento>notas</conhecimento>")], records.append
    )
    reply = await agent.run("oi")

    assert reply == "Pronto, Petrus."
    [record] = records
    assert record["guard_triggered"] is True and record["leaked"] is False
    [turn] = agent.drain_turns()
    assert turn.leaked is False and turn.assistant == "Pronto, Petrus."


async def test_a_clean_answer_records_nothing() -> None:
    records: list[dict[str, Any]] = []
    agent, _, _ = make_agent([say("Oi, Petrus! Tudo certo por aqui.")], records.append)
    await agent.run("oi")
    assert records == []
    assert agent.drain_turns()[0].leaked is False


async def test_a_broken_sink_never_costs_the_answer() -> None:
    def broken(record: dict[str, Any]) -> None:
        raise RuntimeError("mongo caiu")

    agent, _, _ = make_agent([say("Assistant: ok, feito.")], broken)
    assert await agent.run("oi") == "Assistant: ok, feito."


async def test_a_slow_sink_holds_the_turn_at_most_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_module, "SINK_TIMEOUT", 0.05)
    agent, _, _ = make_agent([say("Assistant: ok.")], lambda r: time.sleep(0.5))
    started = time.monotonic()
    assert await agent.run("oi") == "Assistant: ok."
    assert time.monotonic() - started < 0.4


async def test_with_the_audit_off_nothing_is_recorded_but_the_turn_is_still_kept_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.leak_audit, "enabled", False)
    records: list[dict[str, Any]] = []
    agent, _, _ = make_agent([say("Assistant: oi, tudo bem?")], records.append)
    await agent.run("oi")
    assert records == []
    assert agent.drain_turns()[0].leaked is True


# --- who served the call -----------------------------------------------------------------------


class Named(LLMProvider):
    name: ClassVar[str] = "named"

    def __init__(self, model: str, delay: float = 0.0) -> None:
        self._model = model
        self.delay = delay

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        await asyncio.sleep(self.delay)
        yield StreamChunk(delta="ok", finish_reason="stop")

    async def health_check(self) -> bool:
        return True


def test_a_provider_tells_its_model() -> None:
    assert Named("m1").model == "m1"
    assert FakeProvider([]).model is None


async def test_the_router_tells_each_task_who_served_it() -> None:
    slow = LLMRouter([Named("lento", delay=0.05)])
    fast = LLMRouter([Named("rápido")])
    shared = LLMRouter([Named("a")])

    async def consume(router: LLMRouter) -> tuple[str | None, str | None]:
        async for _ in router.stream([]):
            pass
        return router.served()

    assert await consume(slow) == ("named", "lento")
    assert fast.model is None
    assert await consume(fast) == ("named", "rápido") and fast.model == "rápido"

    # Two conversations on the same router: each one sees its own call.
    shared._providers = [Named("primeiro", delay=0.05)]
    first = asyncio.create_task(consume(shared))
    await asyncio.sleep(0)
    shared._providers = [Named("segundo")]
    second = asyncio.create_task(consume(shared))
    assert (await first)[1] == "primeiro"
    assert (await second)[1] == "segundo"
    assert shared.last_model == "primeiro"  # whoever finished last: not to be trusted per task


# --- memory and the night ----------------------------------------------------------------------


def test_a_leaked_transcript_turn_round_trips() -> None:
    turn = TranscriptTurn("s1", 3, "oi", "", leaked=True)
    assert TranscriptTurn.from_doc(turn.to_doc()).leaked is True
    old = turn.to_doc()
    del old["leaked"]  # turns written before T-165
    assert TranscriptTurn.from_doc(old).leaked is False


def test_the_night_sees_that_the_reply_was_separated() -> None:
    material = build_night_material([TranscriptTurn("s1", 4, "valeu", "", leaked=True)])
    assert material.text == f"[t4] Usuário: valeu\n[t4] Juniper: {LEAK_OMITTED}"
    assert material.omitted == 1


# --- store -------------------------------------------------------------------------------------


def make_store(retention_days: int = 30) -> LeakAuditStore:
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return LeakAuditStore(connection, retention_days=retention_days)


def record(**overrides: Any) -> dict[str, Any]:
    base = build_record(
        hits=check("<estado>"),
        guard_triggered=False,
        raw="<estado>",
        delivered="<estado>",
        session_id="s1",
        channel="telegram",
        voice=False,
        provider="deepinfra",
        model="m1",
        finish_reason="stop",
        tool_calls=[],
        blocks=[],
        user_message="oi",
    )
    base.update(overrides)
    return base


def test_the_store_keeps_lists_finds_and_summarizes() -> None:
    store = make_store()
    first = record()
    store.add(first)
    store.add(
        record(
            leaked=False,
            quarantined=False,
            guard_triggered=True,
            rules=["tool_markup"],
            model="m2",
        )
    )
    store.add(record(at=datetime.now(UTC) - timedelta(days=10)))

    assert len(store.recent(days=7)) == 2
    assert len(store.recent(days=7, leaked_only=True)) == 1
    assert store.get(first["_id"][:8])["_id"] == first["_id"]
    assert store.get("nope!") is None
    assert store.get("zz") is None

    summary = store.summary(days=7)
    assert summary["total"] == 2 and summary["leaked"] == 1 and summary["blocked"] == 1
    assert summary["quarantined"] == 1
    assert dict(summary["models"]) == {"deepinfra/m1": 1, "deepinfra/m2": 1}


def test_the_records_expire_by_ttl() -> None:
    store = make_store()
    store.add(record())
    indexes = store.connection.collection(LEAK_AUDIT_COLLECTION).index_information()
    ttl = [i for i in indexes.values() if i.get("expireAfterSeconds") is not None]
    assert ttl and ttl[0]["expireAfterSeconds"] == 30 * 86400


def test_a_changed_retention_updates_the_existing_index() -> None:
    commands: list[tuple[Any, ...]] = []

    class Database:
        def command(self, *args: Any, **kwargs: Any) -> None:
            commands.append((args, kwargs))

    class Collection:
        database = Database()

        def create_index(self, *args: Any, **kwargs: Any) -> None:
            if "expireAfterSeconds" in kwargs:
                raise OperationFailure("conflict", code=85)

    make_store(retention_days=7)._ensure_ttl(Collection())
    assert commands == [
        (
            ("collMod", LEAK_AUDIT_COLLECTION),
            {"index": {"keyPattern": {"at": 1}, "expireAfterSeconds": 7 * 86400}},
        )
    ]


def test_without_mongo_there_is_no_store(tmp_path: Any) -> None:
    assert open_leak_audit(FileSessionStore(tmp_path)) is None


# --- juniper vazamentos ------------------------------------------------------------------------


def run_cli(store: LeakAuditStore, *args: str, monkeypatch: pytest.MonkeyPatch) -> str:
    import typer

    app = typer.Typer()
    console = Console(width=200, force_terminal=False, color_system=None)
    leaks_cli.install(app, console)
    app.command("x", hidden=True)(lambda: None)  # keeps "vazamentos" a subcommand
    monkeypatch.setattr(leaks_cli, "_open_store", lambda c: store)
    monkeypatch.setattr(store.connection, "close", lambda: None)  # the command closes it
    with console.capture() as captured:
        result = CliRunner().invoke(app, ["vazamentos", *args])
    assert result.exit_code == 0, result.output
    return captured.get() + result.output


def test_the_command_lists_shows_and_summarizes(monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store()
    hostile = record(
        tool_calls=["[/bold]", "[link=https://evil.example]x[/link]"],
        raw="texto com \x1b[2J controle",
        model="[red]m[/red]",
    )
    store.add(hostile)

    listing = run_cli(store, monkeypatch=monkeypatch)
    assert hostile["_id"][:8] in listing and "fora" in listing

    shown = run_cli(store, "--ver", hostile["_id"][:8], monkeypatch=monkeypatch)
    assert "[/bold]" in shown and "[link=https://evil.example]" in shown  # printed, not obeyed
    assert "\x1b" not in shown

    summary = run_cli(store, "--resumo", monkeypatch=monkeypatch)
    assert "context_tag" in summary and "1 fora da memória" in summary
