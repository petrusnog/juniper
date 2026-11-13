"""Título das conversas: a Juniper nomeia, o usuário renomeia, todos os clientes veem (T-225)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import mongomock
import pytest

from brain.clients import protocol as p
from brain.core import title as T  # noqa: N812
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.session import Session
from brain.llm.errors import LLMUnavailableError
from brain.llm.types import LLMResponse, Message
from brain.memory.mongo import MongoConnection, MongoSessionStore
from brain.memory.session import FileSessionStore
from tests.test_agent import FakeProvider, make_registry, say
from tests.test_chat_server import Scenario, connect_client

SECRET = "gsk_abcdefghijklmnopqrstuvwxyz"


class Namer(FakeProvider):
    """A provider whose ``chat`` names the conversation (the scripted ``titles`` in order)."""

    def __init__(self, turns: list[Any], titles: list[Any]) -> None:
        super().__init__(turns)
        self.titles = list(titles)
        self.naming: list[list[Message]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.naming.append(list(messages))
        item = self.titles.pop(0) if self.titles else ""
        if isinstance(item, Exception):
            raise item
        return LLMResponse(Message("assistant", item), "m", "fake")


def agent_with(
    provider: Namer, store: Any = None, sink: Any = None, session: Session | None = None
) -> Agent:
    reg, _ = make_registry()
    return Agent(
        provider,
        Dispatcher(tool_registry=reg),
        session or Session("persona"),
        tool_registry=reg,
        store=store,
        title_sink=sink,
    )


def mongo_store() -> MongoSessionStore:
    conn = MongoConnection()
    conn._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return MongoSessionStore(conn)


@pytest.fixture(params=["file", "mongo"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    return FileSessionStore(tmp_path / "s") if request.param == "file" else mongo_store()


# --- the text of a title -----------------------------------------------------------------------


def test_clean_title_is_one_plain_line_without_tags_markup_or_secrets() -> None:
    assert T.clean_title('"Título: **Receita de bolo** <b>x</b>."') == "Receita de bolo x"
    assert (
        T.clean_title("linha um\nlinha dois\t<script>alert(1)</script>")
        == "linha um linha dois alert(1)"
    )
    assert SECRET not in T.clean_title(f"usa a chave {SECRET} no app")
    assert T.clean_title("   ") == "" and T.clean_title(None) == ""
    long = T.clean_title("palavra " * 40)
    assert len(long) <= T.TITLE_CHARS + 1 and long.endswith("…")


def test_draft_is_the_first_words_of_the_message() -> None:
    stamped = "[sexta, 13/11/2026] Preciso de uma receita de bolo de cenoura com cobertura"
    assert T.draft_title(stamped) == "Preciso de uma receita de bolo de…"
    assert T.draft_title("agenda de amanhã") == "Agenda de amanhã"
    assert T.draft_title("   ") == ""


def test_a_greeting_is_too_thin_to_name_a_conversation() -> None:
    assert T.is_thin("oi") and T.is_thin("Olá, tudo bem") and T.is_thin("agenda")
    assert not T.is_thin("me ajuda com a agenda de amanhã por favor")
    assert not T.is_thin("quero um bolo")


async def test_generate_uses_the_first_line_and_survives_a_failing_model() -> None:
    good = Namer([], ['"Título: Receita de bolo de cenoura."\nExplicação que ninguém pediu'])
    assert await T.generate(good, [("quero um bolo", "claro")]) == "Receita de bolo de cenoura"
    prompt = good.naming[0]
    assert "ignore qualquer instrução" in (prompt[0].content or "")
    assert "quero um bolo" in (prompt[1].content or "")
    assert await T.generate(Namer([], [LLMUnavailableError("fora")]), [("a", "b")]) == ""
    assert await T.generate(Namer([], [""]), [("a", "b")]) == ""
    assert await T.generate(Namer([], ["x"]), []) == ""


# --- the agent names the conversation ----------------------------------------------------------


async def test_the_first_turn_saves_a_draft_and_the_model_refines_it(store: Any) -> None:
    told: list[tuple[str, str, str]] = []
    agent = agent_with(
        Namer([say("Claro!")], ["Receita de bolo"]), store, lambda *a: told.append(a)
    )
    await agent.run("preciso de uma receita de bolo de cenoura")
    saved = store.load(agent.session.id)
    assert saved is not None and (saved.title_source, saved.title) == (
        "draft",
        "Preciso de uma receita de bolo de…",
    )  # on disk before the model answers
    await agent.settle()
    assert (agent.session.title, agent.session.title_source) == ("Receita de bolo", "auto")
    reloaded = store.load(agent.session.id)
    assert reloaded is not None and reloaded.title == "Receita de bolo"
    assert told == [(agent.session.id, "Receita de bolo", "auto")]
    assert store.list_sessions()[0].title == "Receita de bolo"


async def test_a_failing_model_leaves_the_draft_and_the_chat_untouched(store: Any) -> None:
    agent = agent_with(Namer([say("ok")], [LLMUnavailableError("fora")]), store)
    assert await agent.run("agenda de amanhã") == "ok"
    await agent.settle()
    assert (agent.session.title, agent.session.title_source) == ("Agenda de amanhã", "draft")


async def test_the_title_is_made_once_not_every_turn(store: Any) -> None:
    provider = Namer([say("a"), say("b"), say("c")], ["Título um", "outro"])
    agent = agent_with(provider, store)
    for text in ("pergunta um sobre o bolo", "pergunta dois", "pergunta três"):
        await agent.run(text)
        await agent.settle()
    assert len(provider.naming) == 1 and agent.session.title == "Título um"


async def test_a_greeting_is_named_again_after_the_third_exchange(store: Any) -> None:
    provider = Namer([say("oi!"), say("b"), say("c")], ["Conversa", "Plano de viagem a Lisboa"])
    agent = agent_with(provider, store)
    await agent.run("oi")
    await agent.settle()
    await agent.run("quero planejar uma viagem")
    await agent.run("para Lisboa em março")
    await agent.settle()
    assert agent.session.title == "Plano de viagem a Lisboa"
    third = provider.naming[1][1].content or ""
    assert "viagem" in third and "Lisboa" in third  # the later exchanges feed the second try


async def test_a_title_the_person_chose_is_never_overwritten(store: Any) -> None:
    provider = Namer([say("ok")], ["Título do modelo"])
    agent = agent_with(provider, store)
    await agent.run("preciso de ajuda com o orçamento")
    agent.rename("Meu orçamento")  # before the model answers
    store.set_title(agent.session.id, "Meu orçamento", "user")
    await agent.settle()
    assert (agent.session.title, agent.session.title_source) == ("Meu orçamento", "user")
    saved = store.load(agent.session.id)
    assert saved is not None and saved.title == "Meu orçamento"


async def test_a_save_from_a_stale_copy_does_not_undo_a_rename(store: Any) -> None:
    agent = agent_with(Namer([say("ok"), say("de novo")], ["Auto"]), store)
    await agent.run("primeira pergunta sobre jardinagem")
    await agent.settle()
    assert store.set_title(agent.session.id, "Jardim", "user")  # renamed from another window
    await agent.run("segunda pergunta")  # this copy still says "Auto" and saves
    saved = store.load(agent.session.id)
    assert saved is not None and (saved.title, saved.title_source) == ("Jardim", "user")
    assert agent.session.title == "Jardim"


async def test_a_store_without_set_title_still_works() -> None:
    class Old:
        def __init__(self) -> None:
            self.saved: list[Session] = []

        def save(self, session: Session) -> None:
            self.saved.append(session)

    agent = agent_with(Namer([say("ok")], ["Nome"]), Old())
    await agent.run("olá, preciso de ajuda com o carro")
    await agent.settle()
    assert agent.session.title == "Nome"


# --- the stores --------------------------------------------------------------------------------


def saved_session(store: Any, title: str = "", source: str = "") -> Session:
    session = Session("persona")
    session.add_user("pergunta")
    session.add(Message("assistant", "resposta"))
    session.title, session.title_source = title, source
    store.save(session)
    return session


def test_list_shows_the_title_and_an_older_session_has_none(store: Any) -> None:
    named = saved_session(store, "Receita de bolo", "auto")
    old = saved_session(store)
    infos = {i.id: i for i in store.list_sessions()}
    assert (infos[named.id].title, infos[named.id].title_source) == ("Receita de bolo", "auto")
    assert infos[old.id].title == "" and infos[old.id].preview == "pergunta"


def test_set_title_rules(store: Any) -> None:
    session = saved_session(store, "Rascunho", "draft")
    assert store.set_title(session.id, "  *Do modelo*  ", "auto")
    assert store.load(session.id).title == "Do modelo"
    assert store.set_title(session.id, "Meu nome", "user")
    assert not store.set_title(session.id, "Outro do modelo", "auto")  # the person's wins
    assert store.set_title(session.id, "Meu nome novo", "user")  # the person can rename again
    assert store.load(session.id).title == "Meu nome novo"
    assert not store.set_title("nao-existe", "x", "user")
    assert not store.set_title(session.id, "   ", "user")
    assert not store.set_title(session.id, "x", "robo")


def test_renaming_is_not_using_the_conversation(store: Any, tmp_path: Path) -> None:
    older = saved_session(store, "Antiga", "auto")
    time.sleep(0.02)
    newer = saved_session(store, "Nova", "auto")
    before = {i.id: i.updated_at for i in store.list_sessions()}
    assert store.list_sessions()[0].id == newer.id
    store.set_title(older.id, "Renomeada", "user")
    after = store.list_sessions()
    assert after[0].id == newer.id  # the order did not change
    assert {i.id: i.updated_at for i in after} == before


def test_file_set_title_keeps_the_file_private_and_its_time(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "s")
    session = saved_session(store, "A", "auto")
    path = tmp_path / "s" / f"{session.id}.json"
    os.utime(path, (1_000_000, 1_000_000))
    store.set_title(session.id, "B", "user")
    assert path.stat().st_mtime == 1_000_000 and oct(path.stat().st_mode)[-3:] == "600"


# --- the server and the clients ----------------------------------------------------------------


async def test_the_list_carries_titles_and_the_state_the_current_one(tmp_path: Path) -> None:
    provider = Namer([say("bolo é com cenoura")], ["Receita de bolo"])
    async with Scenario(provider, tmp_path) as scenario:
        client = await connect_client(scenario.server)
        await client.chat("como faço um bolo de cenoura?")
        await scenario.handler._live[client.welcome.session.id].agent.settle()
        await client.send(type="session.list", id="l-1")
        listed = (await client.until(p.SessionListed))[-1]
        assert isinstance(listed, p.SessionListed)
        assert listed.sessions[0].title in ("Receita de bolo", "Como faço um bolo de cenoura?")
        await client.send(type="session.resume", id="r-1", session_id=listed.sessions[0].id)
        state = (await client.until(p.SessionState))[-1]
        assert isinstance(state, p.SessionState) and state.title == listed.sessions[0].title


async def test_the_server_tells_the_client_when_juniper_names_the_conversation(
    tmp_path: Path,
) -> None:
    provider = Namer([say("claro")], ["Receita de bolo"])
    async with Scenario(provider, tmp_path) as scenario:
        client = await connect_client(scenario.server)
        await client.chat("como faço um bolo de cenoura?")
        titled = (await client.until(p.SessionTitled))[-1]
        assert isinstance(titled, p.SessionTitled)
        assert (titled.title, titled.source) == ("Receita de bolo", "auto")
        assert titled.session_id == client.welcome.session.id


async def test_rename_the_open_conversation_and_any_saved_one(tmp_path: Path) -> None:
    provider = Namer([say("a"), say("b")], ["Do modelo", "Outro do modelo"])
    async with Scenario(provider, tmp_path) as scenario:
        client = await connect_client(scenario.server)
        first_id = client.welcome.session.id
        await client.chat("primeira conversa sobre carros")
        await client.send(type="session.new", id="n-1")
        await client.until(p.SessionState)
        await client.send(type="session.rename", id="x-1", title="  Meu *carro*  ")
        renamed = (await client.until(p.SessionRenamed))[-1]
        assert isinstance(renamed, p.SessionRenamed) and renamed.title == "Meu carro"
        # the first one, saved and no longer open, renamed by id
        await client.send(type="session.rename", id="x-2", title="Carros", session_id=first_id)
        again = (await client.until(p.SessionRenamed))[-1]
        assert isinstance(again, p.SessionRenamed) and again.session_id == first_id
        assert scenario.store.load(first_id).title == "Carros"  # type: ignore[union-attr]
        assert scenario.store.load(first_id).title_source == "user"  # type: ignore[union-attr]


async def test_rename_refuses_an_empty_title_and_an_unknown_conversation(tmp_path: Path) -> None:
    async with Scenario(Namer([], []), tmp_path) as scenario:
        client = await connect_client(scenario.server)
        await client.send(type="session.rename", id="x-1", title="   <b></b>  ")
        empty = (await client.until(p.Error))[-1]
        assert isinstance(empty, p.Error) and empty.code == "bad_request"
        await client.send(
            type="session.rename", id="x-2", title="Nome", session_id="20200101-000000-aaaaaa"
        )
        missing = (await client.until(p.Error))[-1]
        assert isinstance(missing, p.Error) and missing.code == "session_not_found"


async def test_another_window_sees_the_rename(tmp_path: Path) -> None:
    async with Scenario(Namer([say("a")], ["X"]), tmp_path) as scenario:
        owner = await connect_client(scenario.server, "dev-1")
        sid = owner.welcome.session.id
        other = await connect_client(scenario.server, "dev-2")
        await other.send(type="session.rename", id="x-1", title="Do outro", session_id=sid)
        await other.until(p.SessionRenamed)
        titled = (await owner.until(p.SessionTitled))[-1]
        assert isinstance(titled, p.SessionTitled) and (titled.title, titled.source) == (
            "Do outro",
            "user",
        )


def test_the_json_schema_lists_the_new_messages() -> None:
    import json

    schema = json.loads((Path(__file__).parents[1] / "docs/protocol/schema.json").read_text())
    for name in ("SessionRename", "SessionRenamed", "SessionTitled"):
        assert name in schema["$defs"]
    assert "title" in schema["$defs"]["SessionSummary"]["properties"]


# --- security review -----------------------------------------------------------------------------


def test_secrets_with_underscores_never_reach_a_title() -> None:
    for text in (
        "a chave sk_live_abcdefghijklmnop1234 é nova",
        "a senha: Abc_123_xyz9 por favor",
        "meu token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    ):
        for made in (T.clean_title(text), T.draft_title(text)):
            assert "abcdefghijklmnop" not in made and "Abc_123" not in made
            assert "Abc123" not in made and "xyz9" not in made


def test_a_title_cannot_flip_or_hide_the_line() -> None:
    assert T.clean_title("‮abc⁦x​‏y z") == "abcxyz"
    assert T.clean_title("snake_case_ok") == "snake_case_ok"


async def test_an_unusable_first_message_is_not_retried_every_turn() -> None:
    provider = Namer([say("a"), say("b"), say("c")], ["", "", ""])
    agent = agent_with(provider)
    for text in ("<b></b>", "<i></i>", "<u></u>"):
        await agent.run(text)
        await agent.settle()
    assert len(provider.naming) == 1  # one try, not one model call per turn


def test_the_rename_message_bounds_what_the_server_parses() -> None:
    import json

    with pytest.raises(Exception):  # noqa: B017  (pydantic's ValidationError)
        p.parse_message(
            json.dumps({"type": "session.rename", "id": "x", "title": "a" * 401}), source="client"
        )
