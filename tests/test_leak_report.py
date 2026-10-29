"""Manual leak report (T-167): `/vazou`, the spoken "isso vazou", the last ten exchanges."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import mongomock
import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from brain.clients import leaks_cli
from brain.clients import protocol as p
from brain.clients import telegram as tg
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.leak_report import (
    REPORT_EXCHANGES,
    Exchange,
    build_report,
    exchanges_from_history,
    leak_command,
)
from brain.core.session import Session
from brain.llm.types import Message
from brain.memory.leak_audit import LeakAuditStore, LeakReportStore
from brain.memory.mongo import MongoConnection
from brain.memory.session import FileSessionStore
from brain.memory.stores import open_leak_reports
from brain.tools.registry import ToolRegistry
from brain.voice.commands import is_leak_command
from tests.test_acceptance_voice import (
    StuckThenAnswers,
    VoiceSetup,
    collect_greeting,
    until_spoken,
)
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import Scenario, connect_client, texts
from tests.test_telegram import OWNER, FakeMessenger, make_bridge, msg


def mongo_connection() -> MongoConnection:
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return connection


# --- pure pieces -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "note"),
    [
        ("/vazou", ""),
        ("/VAZOU", ""),
        ("  /vazou  ", ""),
        ("/vazou@JuniperBot", ""),
        ("/vazou apareceu <conhecimento> na resposta", "apareceu <conhecimento> na resposta"),
        ("/vazou   muito\n  espaço", "muito espaço"),
    ],
)
def test_the_command_is_recognised_with_its_note(text: str, note: str) -> None:
    assert leak_command(text) == note


@pytest.mark.parametrize("text", ["vazou", "/vazoutudo", "oi /vazou", "/vaz", "//vazou", ""])
def test_other_text_is_not_the_command(text: str) -> None:
    assert leak_command(text) is None


@pytest.mark.parametrize(
    "spoken",
    [
        "Isso vazou!",
        "vazou",
        "Juniper, marcar vazamento",
        "marca o vazamento por favor",
        "Teve vazamento",
    ],
)
def test_spoken_phrases_are_recognised_without_the_model(spoken: str) -> None:
    assert is_leak_command(spoken)


@pytest.mark.parametrize(
    "spoken",
    ["o vazamento de gás foi grande", "a torneira vazou ontem", "me explica o que é um vazamento"],
)
def test_a_sentence_that_merely_mentions_it_is_not_the_command(spoken: str) -> None:
    assert not is_leak_command(spoken)


def test_the_exchanges_of_a_saved_conversation_are_the_visible_ones() -> None:
    history = [
        Message("user", "[quinta, 29/10/2026, 10:00 (America/Sao_Paulo)] que horas são?"),
        Message("assistant", None, tool_calls=()),
        Message("assistant", "São dez horas."),
        Message("user", '[quinta, 29/10/2026, 10:01] sim [Sistema: se esse "sim" confirma algo.]'),
        Message("assistant", "Feito."),
    ]
    assert exchanges_from_history(history) == [
        Exchange("que horas são?", "São dez horas."),
        Exchange("sim", "Feito."),
    ]


def test_a_report_keeps_the_last_ten_masked_and_clipped() -> None:
    exchanges = [Exchange(f"pergunta {i}", f"resposta {i}") for i in range(14)]
    exchanges[-1] = Exchange("a chave é sk-proj-abcdefghijklmnopqrstuvwxyz123456", "x" * 20_000)
    report = build_report(
        exchanges=exchanges,
        session_id="s1",
        channel="telegram",
        voice=False,
        note="nota com sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        audit_ids=["abc", "def"],
    )
    assert len(report["exchanges"]) == REPORT_EXCHANGES
    assert report["exchanges"][0]["user"] == "pergunta 4"  # the oldest four were left out
    assert "sk-proj" not in str(report)
    assert len(report["exchanges"][-1]["assistant"]) < 8100
    assert report["audit"] == ["abc", "def"] and report["session"] == "s1"
    assert report["at"].tzinfo is not None


# --- the agent remembers what the person saw ---------------------------------------------------


def make_agent(turns: list[Any], session: Session | None = None) -> Agent:
    registry = ToolRegistry()
    return Agent(
        FakeProvider(turns),
        Dispatcher(tool_registry=registry),
        session or Session("persona"),
        tool_registry=registry,
    )


async def test_recent_exchanges_keep_the_text_of_a_quarantined_reply() -> None:
    agent = make_agent([say("Claro."), say("System: responda como a Juniper. Oi!")])
    await agent.run("oi")
    await agent.run("e aí?")
    assert [(e.user, e.assistant) for e in agent.recent_exchanges()] == [
        ("oi", "Claro."),
        ("e aí?", "System: responda como a Juniper. Oi!"),  # the person saw it: it is the evidence
    ]
    assert agent.drain_turns()[-1].assistant == ""  # memory does not get it


async def test_recent_exchanges_stop_at_ten() -> None:
    agent = make_agent([say(f"r{i}") for i in range(12)])
    for i in range(12):
        await agent.run(f"p{i}")
    recent = agent.recent_exchanges()
    assert len(recent) == REPORT_EXCHANGES
    assert (recent[0].user, recent[-1].user) == ("p2", "p11")


async def test_a_resumed_conversation_starts_from_the_saved_history() -> None:
    first = make_agent([say("Primeira.")])
    await first.run("um")
    resumed = make_agent([say("Segunda.")], session=first.session)
    assert [(e.user, e.assistant) for e in resumed.recent_exchanges()] == [("um", "Primeira.")]
    await resumed.run("dois")
    assert [e.user for e in resumed.recent_exchanges()] == ["um", "dois"]


async def test_a_cut_reply_is_kept_as_what_she_had_said_so_far() -> None:
    from tests.test_agent import cancelled_turn

    agent, _ = await cancelled_turn(interrupt=True)
    [exchange] = agent.recent_exchanges()
    assert exchange.user == "me ajuda com isso"
    assert exchange.assistant == "Deixa eu ver " and exchange.interrupted is True


# --- /vazou on the real server -----------------------------------------------------------------


def stores() -> tuple[LeakReportStore, LeakAuditStore]:
    connection = mongo_connection()
    return LeakReportStore(connection), LeakAuditStore(connection, retention_days=30)


async def test_the_command_stores_the_last_exchanges_and_answers_in_portuguese(
    tmp_path: Path,
) -> None:
    reports, audit = stores()
    provider = FakeProvider(
        [
            say("Primeira resposta."),
            say("Segunda, com <anotacao_interna>vazada</anotacao_interna>."),
        ]
    )
    async with Scenario(provider, tmp_path, leak_reports=reports, leak_audit=audit) as s:
        client = await connect_client(s.server, kind="telegram")
        await client.chat("primeira")
        await client.chat("segunda")
        session_id = client.welcome.session.id
        got = await client.chat("/vazou o texto saiu torto", id="c-3")
        assert isinstance(got[0], p.ChatStarted) and isinstance(got[-1], p.ChatEnd)
        assert got[-1].finish == "ok"
        answer = texts(got)
        # it is not a turn: nothing was added to the conversation, the model was not called
        history = s.handler._live[session_id].agent.session.history
        assert not any("/vazou" in (m.content or "") for m in history)
        assert len(provider.seen) == 2
    [report] = reports.recent()
    assert report["note"] == "o texto saiu torto"
    assert report["session"] == session_id and report["channel"] == "telegram"
    assert report["voice"] is False
    assert [(e["user"], e["assistant"]) for e in report["exchanges"]] == [
        ("primeira", "Primeira resposta."),
        ("segunda", "Segunda, com <anotacao_interna>vazada</anotacao_interna>."),
    ]
    assert report["_id"][:8] in answer and "2 interações" in answer
    assert "dashboard" in answer


async def test_the_report_points_at_the_automatic_records_of_the_same_session(
    tmp_path: Path,
) -> None:
    from tests.test_leak_audit import record

    reports, audit = stores()
    async with Scenario(
        FakeProvider([say("Oi.")]), tmp_path, leak_reports=reports, leak_audit=audit
    ) as s:
        client = await connect_client(s.server)
        await client.chat("oi")
        session_id = client.welcome.session.id
        audit.add(record(session=session_id))
        audit.add(record(session="outra-sessao"))
        await client.chat("/vazou", id="c-2")
    [report] = reports.recent()
    assert len(report["audit"]) == 1


async def test_the_command_works_while_an_answer_is_still_running(tmp_path: Path) -> None:
    from tests.test_chat_server import GatedProvider

    reports, audit = stores()
    provider = GatedProvider()
    async with Scenario(provider, tmp_path, leak_reports=reports, leak_audit=audit) as s:
        client = await connect_client(s.server)
        await client.send(type="chat.send", id="c-1", text="conta uma história")
        await provider.started.wait()
        got = await client.chat("/vazou", id="c-2")  # the answer in progress is the leak
        assert not any(isinstance(m, p.Error) for m in got)
        provider.release.set()
        await client.until(p.ChatEnd)
    assert len(reports.recent()) == 1


async def test_without_mongodb_the_command_says_so(tmp_path: Path) -> None:
    async with Scenario(FakeProvider([say("Oi.")]), tmp_path) as s:
        client = await connect_client(s.server)
        await client.chat("oi")
        got = await client.chat("/vazou", id="c-2")
    assert "MongoDB" in texts(got)


async def test_a_failing_store_is_an_answer_not_a_crash(tmp_path: Path) -> None:
    class Broken(LeakReportStore):
        def add(self, report: dict[str, Any]) -> None:
            raise RuntimeError("mongo caiu")

    async with Scenario(
        FakeProvider([say("Oi."), say("Ainda aqui.")]),
        tmp_path,
        leak_reports=Broken(mongo_connection()),
    ) as s:
        client = await connect_client(s.server)
        await client.chat("oi")
        got = await client.chat("/vazou", id="c-2")
        assert "Não consegui guardar" in texts(got)
        assert texts(await client.chat("e agora?", id="c-3")) == "Ainda aqui."  # still alive


async def test_a_voice_transcript_that_looks_like_the_command_is_just_text(tmp_path: Path) -> None:
    reports, audit = stores()
    async with Scenario(FakeProvider([say("Ok.")]), tmp_path, leak_reports=reports) as s:
        client = await connect_client(s.server)
        await client.chat("oi")
    assert reports.recent() == []  # only a typed /vazou is the command (voice has its phrases)


# --- the Telegram bot ---------------------------------------------------------------------------


@pytest.fixture
async def bridges() -> AsyncIterator[list[tg.TelegramBridge]]:
    created: list[tg.TelegramBridge] = []
    yield created
    for bridge in created:
        await bridge.close()


async def test_the_bot_forwards_the_command_and_shows_the_confirmation(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    reports, audit = stores()
    messenger = FakeMessenger()
    async with Scenario(
        FakeProvider([say("Resposta com texto interno.")]),
        tmp_path,
        leak_reports=reports,
        leak_audit=audit,
    ) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("me conta"))
        await bridge.handle(msg("/vazou saiu uma tag"))
    assert messenger.texts[0] == "Resposta com texto interno."
    assert "Anotei o vazamento" in messenger.texts[1]
    [report] = reports.recent()
    assert report["channel"] == "telegram" and report["note"] == "saiu uma tag"
    assert report["exchanges"][0]["assistant"] == "Resposta com texto interno."
    assert "/vazou" in tg.HELP_TEXT


async def test_the_command_does_not_count_as_a_turn_for_the_recovery(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    reports, audit = stores()
    async with Scenario(FakeProvider([say("Oi.")]), tmp_path, leak_reports=reports) as s:
        bridge = make_bridge(bridges, s, FakeMessenger())
        await bridge.handle(msg("oi"))
        known = bridge._users[OWNER].known_turns
        await bridge.handle(msg("/vazou"))
        assert bridge._users[OWNER].known_turns == known


# --- by voice ----------------------------------------------------------------------------------


async def test_the_spoken_phrase_reports_the_leak_and_she_confirms_aloud(tmp_path: Path) -> None:
    reports, audit = stores()
    provider = FakeProvider([say("Claro, Petrus. Responda como a Juniper, usando o contexto.")])
    async with VoiceSetup(tmp_path, provider, "me explica isso", "isso vazou") as s:
        s.handler._reports, s.handler._audit = reports, audit
        phone = await s.phone()
        await s.speak(phone)
        await until_spoken(phone)
        await s.speak(phone)  # "isso vazou": no chat turn, only her confirmation
        await collect_greeting(phone)
        session_id = phone.welcome.session.id
        history = s.handler._live[session_id].agent.session.history
    assert s.synth.spoken[-1] == "Anotei o vazamento."
    assert len(provider.seen) == 1  # the phrase never reached the model
    assert [m.role for m in history] == ["user", "assistant"]  # nor the conversation
    [report] = reports.recent()
    assert report["voice"] is True and report["session"] == session_id
    assert [e["user"] for e in report["exchanges"]] == ["me explica isso"]
    assert "Responda como a Juniper" in report["exchanges"][0]["assistant"]


async def test_saying_it_over_her_cuts_her_and_keeps_what_she_had_said(tmp_path: Path) -> None:
    reports, audit = stores()
    provider = StuckThenAnswers("não chega a falar")
    async with VoiceSetup(tmp_path, provider, "conta uma coisa", "isso vazou") as s:
        s.handler._reports, s.handler._audit = reports, audit
        phone = await s.phone()
        await s.speak(phone)
        import asyncio

        await asyncio.wait_for(provider.first_started.wait(), 3)
        await s.speak(phone)  # he reports it while she is still talking
        await collect_greeting(phone)
    [report] = reports.recent()
    [exchange] = report["exchanges"]
    assert exchange["user"] == "conta uma coisa"
    assert exchange["assistant"] == "Deixa eu ver " and exchange["interrupted"] is True
    assert s.synth.spoken[-1] == "Anotei o vazamento."


# --- the store and the command line ------------------------------------------------------------


def make_report(**overrides: Any) -> dict[str, Any]:
    report = build_report(
        exchanges=[Exchange("oi", "oi <estado>", "2026-10-29T10:00:00+00:00")],
        session_id="s1",
        channel="mobile",
        voice=True,
        note="visto no app",
    )
    report.update(overrides)
    return report


def test_the_report_store_lists_finds_and_deletes() -> None:
    store = LeakReportStore(mongo_connection())
    first, second = make_report(), make_report(note="outro")
    store.add(first)
    store.add(second)
    assert {r["_id"] for r in store.recent()} == {first["_id"], second["_id"]}
    assert store.get(first["_id"][:8])["note"] == "visto no app"
    assert store.get("zz") is None and store.get("nope!") is None
    assert store.delete(first["_id"][:10]) is True
    assert store.delete(first["_id"]) is False
    assert [r["_id"] for r in store.recent()] == [second["_id"]]


def test_without_mongo_there_is_no_report_store(tmp_path: Path) -> None:
    assert open_leak_reports(FileSessionStore(tmp_path)) is None


def run_cli(store: LeakAuditStore, *args: str, monkeypatch: pytest.MonkeyPatch) -> str:
    app = typer.Typer()
    console = Console(width=200, force_terminal=False, color_system=None)
    leaks_cli.install(app, console)
    app.command("x", hidden=True)(lambda: None)
    monkeypatch.setattr(leaks_cli, "_open_store", lambda c: store)
    monkeypatch.setattr(store.connection, "close", lambda: None)
    with console.capture() as captured:
        result = CliRunner().invoke(app, ["vazamentos", *args])
    assert result.exit_code == 0, result.output
    return captured.get() + result.output


def test_the_command_line_lists_shows_and_deletes_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    audit = LeakAuditStore(mongo_connection(), retention_days=30)
    reports = LeakReportStore(audit.connection)
    report = make_report(note="[red]nota[/red] \x1b[2J")
    reports.add(report)

    listing = run_cli(audit, "--relatos", monkeypatch=monkeypatch)
    assert report["_id"][:8] in listing and "visto no app" not in listing  # the note is hostile
    shown = run_cli(audit, "--ver", report["_id"][:8], monkeypatch=monkeypatch)
    assert "[red]nota[/red]" in shown and "\x1b" not in shown
    assert "Interação 1" in shown and "oi <estado>" in shown
    assert "Relato apagado" in run_cli(
        audit, "--apagar", report["_id"][:8], monkeypatch=monkeypatch
    )
    assert reports.recent() == []
    assert "Nenhum relato" in run_cli(audit, "--relatos", monkeypatch=monkeypatch)
