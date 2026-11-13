"""Comandos de texto das conversas (T-226): /conversas, /renomear e /retomar no Telegram e no
terminal, e `juniper conversas` / `juniper renomear`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from brain.clients import cli, conversations, conversations_cli
from brain.clients import telegram as tg
from brain.clients.remote import RemoteClient
from brain.config import settings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.session import Session
from brain.llm.types import Message
from brain.memory.session import FileSessionStore
from tests.test_agent import FakeProvider, make_registry, say
from tests.test_chat_server import TOKEN, Scenario
from tests.test_session_title import Namer
from tests.test_telegram import CHAT, OWNER, FakeMessenger, factory_for, make_bridge, msg

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def entry(id_: str, title: str = "", preview: str = "", turns: int = 2, hours: float = 1) -> Any:
    stamp = (NOW - timedelta(hours=hours)).isoformat()
    return conversations.Entry(id_, title, preview, stamp, turns)


# --- parser, list and resolution ---------------------------------------------------------------


def test_parse_the_three_commands_and_their_aliases() -> None:
    assert conversations.parse("/conversas") == ("list", "")
    assert conversations.parse("/renomear Meu bolo") == ("rename", "Meu bolo")
    assert conversations.parse("/titulo Meu bolo") == ("rename", "Meu bolo")
    assert conversations.parse("/renomear@JuniperBot Bolo") == ("rename", "Bolo")
    assert conversations.parse("  /retomar 2 ") == ("resume", "2")
    assert conversations.parse("/novo") is None and conversations.parse("oi /conversas") is None


def test_the_list_is_numbered_by_title_and_falls_back_to_the_first_message() -> None:
    entries = [
        entry("a" * 8, "Receita de bolo", turns=6),
        entry("b" * 8, "", "como configuro o wifi", turns=1, hours=30),
        entry("c" * 8),
    ]
    text = conversations.format_list(entries, current="a" * 8, now=NOW)
    lines = text.splitlines()
    assert lines[1] == "1. Receita de bolo · há 1 h · 6 mensagens ← esta"
    assert lines[2] == "2. como configuro o wifi · há 30 h · 1 mensagem"
    assert lines[3].startswith("3. (sem título)")
    assert conversations.format_list([]) == conversations.NO_CONVERSATIONS


def test_resume_by_number_or_by_the_start_of_an_id() -> None:
    entries = [entry("20261113-100000-aaaaaa", "A"), entry("20261113-090000-bbbbbb", "B")]
    assert conversations.resolve("2", entries)[0] is entries[1]
    assert conversations.resolve("20261113-0900", entries)[0] is entries[1]
    assert conversations.resolve("9", entries)[0] is None
    assert conversations.resolve("20261113", entries)[1] == conversations.AMBIGUOUS
    assert conversations.resolve("zzzz", entries)[1] == conversations.NOT_FOUND
    assert conversations.resolve("", entries)[1] == conversations.RESUME_USAGE
    assert conversations.resolve("../x", entries)[1] == conversations.NOT_FOUND


# --- Telegram ----------------------------------------------------------------------------------


@pytest.fixture
async def bridges() -> AsyncIterator[list[tg.TelegramBridge]]:
    created: list[tg.TelegramBridge] = []
    yield created
    for bridge in created:
        await bridge.close()


async def test_telegram_lists_renames_and_resumes_by_title(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    provider = Namer([say("Bolo é fácil."), say("Carro precisa de óleo.")], ["Receita de bolo"])
    messenger = FakeMessenger()
    async with Scenario(provider, tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("como faço um bolo de cenoura?"))
        await asyncio.sleep(0.1)
        await bridge.handle(msg("/novo"))
        await bridge.handle(msg("meu carro precisa de óleo?"))
        await asyncio.sleep(0.1)

        await bridge.handle(msg("/conversas"))
        listing = messenger.texts[-1]
        assert listing.startswith("Conversas")
        assert "Receita de bolo" in listing and "1." in listing and "2." in listing

        await bridge.handle(msg("/renomear Meu carro"))
        assert "Meu carro" in messenger.texts[-1]
        await bridge.handle(msg("/conversas"))
        assert "Meu carro" in messenger.texts[-1]

        number = next(
            line.split(".")[0] for line in messenger.texts[-1].splitlines() if "Receita" in line
        )
        await bridge.handle(msg(f"/retomar {number}"))
        assert "Voltei para «Receita de bolo»" in messenger.texts[-1]
        (conn,) = s.server.connections
        assert s.store.load(conn.session_id).title == "Receita de bolo"  # type: ignore[union-attr]


async def test_telegram_commands_say_what_to_do_when_the_input_is_wrong(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("/renomear"))
        await bridge.handle(msg("/retomar"))
        await bridge.handle(msg("/retomar 7"))
        await bridge.handle(msg("/conversas"))
    assert messenger.texts == [
        conversations.RENAME_USAGE,
        conversations.RESUME_USAGE,
        conversations.NOT_FOUND,
        conversations.NO_CONVERSATIONS,
    ]


def test_telegram_help_lists_the_new_commands() -> None:
    for command in ("/conversas", "/renomear", "/retomar"):
        assert command in tg.HELP_TEXT
    assert factory_for and CHAT and OWNER and TOKEN  # imports used by the helpers above


# --- the terminal without a server ---------------------------------------------------------------


def local_agent(store: FileSessionStore, turns: list[Any]) -> Agent:
    reg, _ = make_registry()
    return Agent(
        Namer(turns, ["Receita de bolo", "Outro nome"]),
        Dispatcher(tool_registry=reg),
        Session("persona"),
        tool_registry=reg,
        store=store,
    )


class Quiet:
    """A printer that keeps what was said."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.out = Console(quiet=True)

    def say(self, text: str, _style: str = "") -> None:
        self.said.append(text)


async def test_the_local_terminal_lists_renames_and_resumes(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "s")
    first = local_agent(store, [say("a")])
    await first.run("como faço um bolo de cenoura?")
    await first.settle()
    second = local_agent(store, [say("b")])
    await second.run("meu carro precisa de óleo?")
    await second.settle()

    printer = Quiet()
    await cli._conversation_command(second, printer, ("list", ""))  # type: ignore[arg-type]
    assert "Receita de bolo" in printer.said[-1] and "← esta" in printer.said[-1]

    await cli._conversation_command(second, printer, ("rename", "Óleo do carro"))  # type: ignore[arg-type]
    assert second.session.title_source == "user"
    assert store.load(second.session.id).title == "Óleo do carro"  # type: ignore[union-attr]

    other = entry_of(store, "Receita de bolo")
    await cli._conversation_command(second, printer, ("resume", other))  # type: ignore[arg-type]
    assert second.session.id == first.session.id and "Voltei" in printer.said[-1]

    await cli._conversation_command(second, printer, ("rename", "  "))  # type: ignore[arg-type]
    assert printer.said[-1] == conversations.RENAME_USAGE


def entry_of(store: FileSessionStore, title: str) -> str:
    infos = store.list_sessions()
    number = next(i for i, info in enumerate(infos, start=1) if info.title == title)
    return str(number)


# --- juniper conversas / juniper renomear --------------------------------------------------------


def test_conversas_and_renomear_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FileSessionStore(tmp_path / "s")
    for title in ("Receita de bolo", "Óleo do carro"):
        session = Session("persona")
        session.add_user("pergunta")
        session.add(Message("assistant", "resposta"))
        session.title, session.title_source = title, "auto"
        store.save(session)
    monkeypatch.setattr(settings.deployment, "mode", "local")
    monkeypatch.setattr("brain.memory.stores.open_session_store", lambda: store)
    monkeypatch.setattr("brain.memory.stores.close_session_store", lambda _s: None)

    app = typer.Typer()
    conversations_cli.install(app, Console(width=120))
    runner = CliRunner()
    listed = runner.invoke(app, ["conversas"])
    assert listed.exit_code == 0 and "Receita de bolo" in listed.output
    done = runner.invoke(app, ["renomear", "1", "Meu", "título", "novo"])
    assert done.exit_code == 0 and "Meu título novo" in done.output
    assert {i.title for i in store.list_sessions()} >= {"Meu título novo"}
    assert runner.invoke(app, ["renomear", "9", "x"]).exit_code == 1
    assert runner.invoke(app, ["renomear", "1", "   "]).exit_code == 1
    assert settings.deployment.mode == "local" and cli and RemoteClient
