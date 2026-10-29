"""Leak audit (T-165): the broad detector, the record, the quarantine of the turn and the store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import mongomock
import pytest

from brain.config import settings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.leak_audit import Block, build_record, detect
from brain.core.session import Session
from brain.memory.leak_audit import LEAK_AUDIT_COLLECTION, LeakAuditStore
from brain.memory.mongo import MongoConnection
from brain.memory.v2.models import TranscriptTurn
from brain.tools.channel import Channel, current_channel
from brain.tools.registry import ToolRegistry
from tests.test_agent import FakeProvider, say

PERSONA = (
    "Você é a Juniper, assistente pessoal do Petrus. Fale de um jeito leve e direto, sem listas "
    "longas, e nunca invente fatos que você não conhece."
)


def rules(hits: list[Any], where: str = "delivered") -> set[str]:
    return {h.rule for h in hits if h.where == where}


# --- detector ----------------------------------------------------------------------------------


def test_the_real_leaks_are_detected() -> None:
    system_note = (
        "]\n\nMensagem de sistema: o app de chat por voz (v1) com bugs no STT está no contexto. "
        "Responda como a Juniper, mas usando o que está no contexto e nas memórias.Obrigado por "
        "dizer isso, de verdade."
    )
    found = rules(detect(system_note, system_note, [], echo_words=10))
    assert {"bracket_start", "role_prefix", "instruction"} <= found

    parameters = "</parameter>Sei, sim. Providers de LLM são as empresas que oferecem modelos."
    assert {"tool_markup", "tag"} <= rules(detect(parameters, parameters, [], echo_words=10))

    knowledge = "Olha só. <conhecimento>Notas sobre STT</conhecimento> Enfim."
    assert "tag" in rules(detect(knowledge, knowledge, [], echo_words=10))

    tokens = "Oi<|im_end|> e também <｜tool▁calls｜>"
    assert "special_token" in rules(detect(tokens, tokens, [], echo_words=10))


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
    assert detect(text, text, [PERSONA], echo_words=10) == []


def test_a_run_of_words_copied_from_a_system_block_is_an_echo() -> None:
    text = (
        "Claro! Fale de um jeito leve e direto, sem listas longas, e nunca invente fatos que "
        "você não conhece. Era isso."
    )
    hits = detect(text, text, [PERSONA], echo_words=10)
    assert [h.rule for h in hits] == ["prompt_echo"]
    assert hits[0].excerpt.startswith("Fale de um jeito leve")
    assert "não conhece" in hits[0].excerpt  # the whole copied run, not the first window only


def test_what_only_the_raw_text_had_is_marked_raw() -> None:
    raw = "Pronto, Petrus. <conhecimento>notas</conhecimento>"
    hits = detect(raw, "Pronto, Petrus.", [], echo_words=10)
    assert hits and all(h.where == "raw" for h in hits)


def test_the_record_carries_the_evidence_but_not_the_block_contents() -> None:
    hits = detect("<estado>x", "<estado>x", [], echo_words=10)
    record = build_record(
        hits=hits,
        guard_triggered=False,
        raw="<estado>x",
        delivered="<estado>x",
        session_id="s1",
        channel="telegram",
        voice=False,
        provider="deepinfra",
        model="m",
        finish_reason="stop",
        tool_calls=[],
        blocks=[Block("persona", 120), Block("memories", 900)],
        user_message="[quarta] oi",
    )
    assert record["leaked"] is True
    assert record["rules"] == ["tag"]
    assert record["blocks"] == [
        {"kind": "persona", "chars": 120},
        {"kind": "memories", "chars": 900},
    ]
    assert record["at"].tzinfo is not None


# --- agent -------------------------------------------------------------------------------------


def make_agent(
    turns: list[Any], sink: Any = None, turn_sink: Any = None
) -> tuple[Agent, FakeProvider]:
    reg = ToolRegistry()
    provider = FakeProvider(turns)
    agent = Agent(
        provider,
        Dispatcher(tool_registry=reg),
        Session(PERSONA),
        tool_registry=reg,
        leak_sink=sink,
        turn_sink=turn_sink,
    )
    return agent, provider


async def test_a_leak_the_person_saw_is_recorded_and_kept_out_of_memory() -> None:
    records: list[dict[str, Any]] = []
    sunk: list[Any] = []
    agent, _ = make_agent(
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
    assert record["leaked"] is True
    assert record["channel"] == "telegram"
    assert record["provider"] == "fake"
    assert "Mensagem de sistema" in record["raw"]
    assert record["user_message"].endswith("valeu, Juniper")
    assert record["blocks"][0]["kind"] == "persona"

    [turn] = agent.drain_turns()
    assert turn.leaked is True and turn.assistant == "" and turn.user == "valeu, Juniper"
    assert sunk[0].leaked is True and sunk[0].assistant == ""


async def test_a_cut_by_the_guard_is_recorded_but_the_turn_stays_in_memory() -> None:
    records: list[dict[str, Any]] = []
    agent, _ = make_agent(
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
    agent, _ = make_agent([say("Oi, Petrus! Tudo certo por aqui.")], records.append)
    await agent.run("oi")
    assert records == []
    assert agent.drain_turns()[0].leaked is False


async def test_a_broken_sink_never_costs_the_answer() -> None:
    def broken(record: dict[str, Any]) -> None:
        raise RuntimeError("mongo caiu")

    agent, _ = make_agent([say("Assistant: ok, feito.")], broken)
    assert await agent.run("oi") == "Assistant: ok, feito."  # the turn ends normally


async def test_with_the_audit_off_nothing_is_recorded_but_the_turn_is_still_kept_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.leak_audit, "enabled", False)
    records: list[dict[str, Any]] = []
    agent, _ = make_agent([say("Assistant: oi, tudo bem?")], records.append)
    await agent.run("oi")
    assert records == []
    assert agent.drain_turns()[0].leaked is True


def test_a_leaked_transcript_turn_round_trips() -> None:
    turn = TranscriptTurn("s1", 3, "oi", "", leaked=True)
    assert TranscriptTurn.from_doc(turn.to_doc()).leaked is True
    old = turn.to_doc()
    del old["leaked"]  # turns written before T-165
    assert TranscriptTurn.from_doc(old).leaked is False


# --- store -------------------------------------------------------------------------------------


def make_store() -> LeakAuditStore:
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return LeakAuditStore(connection, retention_days=30)


def record(**overrides: Any) -> dict[str, Any]:
    base = build_record(
        hits=detect("<estado>", "<estado>", [], echo_words=10),
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
    store.add(record(leaked=False, guard_triggered=True, rules=["tool_markup"], model="m2"))
    store.add(record(at=datetime.now(UTC) - timedelta(days=10)))

    assert len(store.recent(days=7)) == 2
    assert len(store.recent(days=7, leaked_only=True)) == 1
    assert store.get(first["_id"][:8])["_id"] == first["_id"]
    assert store.get("nope!") is None
    assert store.get("zz") is None

    summary = store.summary(days=7)
    assert summary["total"] == 2 and summary["leaked"] == 1 and summary["blocked"] == 1
    assert dict(summary["models"]) == {"deepinfra/m1": 1, "deepinfra/m2": 1}


def test_the_records_expire_by_ttl() -> None:
    store = make_store()
    store.add(record())
    indexes = store.connection.collection(LEAK_AUDIT_COLLECTION).index_information()
    ttl = [i for i in indexes.values() if i.get("expireAfterSeconds") is not None]
    assert ttl and ttl[0]["expireAfterSeconds"] == 30 * 86400
