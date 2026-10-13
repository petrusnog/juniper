"""Tests for short-term memory (T-051): token budget, saving, resuming (ADR-011)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients import cli
from brain.config import Settings, settings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.session import Session, estimate_tokens, load_system_prompt
from brain.llm.errors import LLMUnavailableError
from brain.llm.router import LLMRouter
from brain.llm.types import Message, ToolCall
from brain.memory.session import (
    FileSessionStore,
    SessionStoreError,
    message_from_dict,
    message_to_dict,
)
from tests.test_agent import FakeProvider, make_registry, say

runner = CliRunner()


def turn(session: Session, text: str, reply: str = "ok", tools: bool = False) -> None:
    """One finished turn: user, optional tool exchange, assistant."""
    session.add(Message("user", text))
    if tools:
        call = ToolCall(f"c-{text[:6]}", "echo", {"text": text})
        session.add(Message("assistant", None, tool_calls=(call,)))
        session.add(Message("tool", "resultado", tool_call_id=call.id))
    session.add(Message("assistant", reply))


def users(session: Session) -> list[str]:
    return [m.content or "" for m in session.history if m.role == "user"]


def assert_no_orphan_tool_messages(session: Session) -> None:
    seen: set[str] = set()
    for message in session.history:
        seen.update(c.id for c in message.tool_calls)
        if message.role == "tool":
            assert message.tool_call_id in seen


# --- token budget -------------------------------------------------------------------------


def test_estimate_counts_text_and_tool_call_arguments() -> None:
    plain = estimate_tokens(Message("assistant", "a" * 400))
    with_call = estimate_tokens(
        Message("assistant", "a" * 400, tool_calls=(ToolCall("c", "echo", {"text": "b" * 400}),))
    )
    assert plain >= 100 and with_call >= plain + 100


def test_history_within_budget_is_left_alone() -> None:
    s = Session("p", max_turns=10, max_tokens=5000)
    turn(s, "primeiro")
    turn(s, "segundo")
    s.compact()
    assert users(s) == ["primeiro", "segundo"]


def test_over_budget_drops_the_oldest_turns_whole() -> None:
    s = Session("p", max_turns=50, max_tokens=300)
    for i in range(6):
        turn(s, f"pergunta {i} " + "x" * 400, reply="r" * 400)
    s.compact()
    kept = users(s)
    assert kept and kept[-1].startswith("pergunta 5")
    assert not any(u.startswith("pergunta 0") for u in kept)
    assert s.tokens() <= s.max_tokens
    assert s.history[0].role == "user"  # never starts mid-turn


def test_the_turn_in_progress_is_never_dropped() -> None:
    s = Session("p", max_turns=50, max_tokens=100)
    turn(s, "antiga " + "x" * 400)
    s.add(Message("user", "atual " + "y" * 4000))  # alone it is over budget
    s.compact()
    assert users(s) == ["atual " + "y" * 4000]


def test_budget_does_not_orphan_tool_messages() -> None:
    s = Session("p", max_turns=50, max_tokens=400)
    turn(s, "um " + "x" * 600, tools=True)
    turn(s, "dois " + "x" * 600, tools=True)
    turn(s, "tres " + "x" * 600, tools=True)
    s.compact()
    assert_no_orphan_tool_messages(s)
    assert users(s)[-1].startswith("tres")


def test_turn_cap_still_applies_with_a_large_budget() -> None:
    s = Session("p", max_turns=2, max_tokens=100_000)
    for i in range(4):
        turn(s, f"u{i}")
    s.compact()
    assert users(s) == ["u2", "u3"]


def test_limits_come_from_the_settings() -> None:
    s = Session("p")
    assert s.max_turns == settings.memory.session.max_turns
    assert s.max_tokens == settings.memory.session.max_tokens


def test_session_limits_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUNIPER_MEMORY__SESSION__MAX_TOKENS", "10")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_each_session_has_its_own_sortable_id() -> None:
    a, b = Session("p"), Session("p")
    assert a.id != b.id and len(a.id) > 10


# --- serialization ------------------------------------------------------------------------


def test_message_roundtrip_keeps_tool_calls_and_unicode() -> None:
    call = ToolCall("c1", "create_event", {"título": "dentista", "when": {"h": 16}})
    for message in (
        Message("user", "olá, ação"),
        Message("assistant", None, tool_calls=(call,)),
        Message("tool", "feito", tool_call_id="c1", name="create_event"),
    ):
        assert message_from_dict(json.loads(json.dumps(message_to_dict(message)))) == message


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError):
        message_from_dict({"role": "robot", "content": "x"})


# --- FileSessionStore ---------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> FileSessionStore:
    return FileSessionStore(tmp_path / "store")


def test_save_and_load_roundtrip(store: FileSessionStore) -> None:
    s = Session("p")
    turn(s, "marca dentista", reply="marcado", tools=True)
    store.save(s)
    loaded = store.load(s.id)
    assert loaded is not None
    assert loaded.history == s.history
    assert (loaded.id, loaded.created_at, loaded.updated_at) == (s.id, s.created_at, s.updated_at)


def test_empty_session_is_not_saved(store: FileSessionStore) -> None:
    store.save(Session("p"))
    assert not store.directory.exists()


def test_system_prompt_is_not_stored_and_resume_uses_the_current_persona(
    store: FileSessionStore,
) -> None:
    s = Session("persona antiga e secreta")
    turn(s, "oi")
    store.save(s)
    assert "persona antiga" not in (store.directory / f"{s.id}.json").read_text()
    loaded = store.load(s.id)
    assert loaded is not None and loaded.system_prompt == load_system_prompt()


def test_files_are_private_to_the_user(store: FileSessionStore) -> None:
    s = Session("p")
    turn(s, "oi")
    store.save(s)
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.directory / f"{s.id}.json").stat().st_mode) == 0o600


def test_saving_again_replaces_the_file_without_leftovers(store: FileSessionStore) -> None:
    s = Session("p")
    turn(s, "um")
    store.save(s)
    turn(s, "dois")
    store.save(s)
    loaded = store.load(s.id)
    assert loaded is not None and users(loaded) == ["um", "dois"]
    assert [p.name for p in store.directory.iterdir()] == [f"{s.id}.json"]


def test_a_failed_save_keeps_the_previous_version(
    store: FileSessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = Session("p")
    turn(s, "um")
    store.save(s)
    turn(s, "dois")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disco cheio")

    monkeypatch.setattr("brain.memory.session.json.dump", boom)
    with pytest.raises(OSError):
        store.save(s)
    monkeypatch.undo()
    loaded = store.load(s.id)
    assert loaded is not None and users(loaded) == ["um"]
    assert [p.name for p in store.directory.iterdir()] == [f"{s.id}.json"]


def test_load_of_unknown_id_is_none(store: FileSessionStore) -> None:
    assert store.load("nao-existe") is None


def test_corrupt_file_raises_a_clear_error(store: FileSessionStore) -> None:
    store.directory.mkdir(parents=True)
    (store.directory / "quebrada.json").write_text("{isto não é json")
    with pytest.raises(SessionStoreError, match="corrompida"):
        store.load("quebrada")


def test_unsupported_version_is_refused(store: FileSessionStore) -> None:
    store.directory.mkdir(parents=True)
    (store.directory / "futura.json").write_text(json.dumps({"version": 99}))
    with pytest.raises(SessionStoreError):
        store.load("futura")


def test_ids_cannot_escape_the_directory(store: FileSessionStore, tmp_path: Path) -> None:
    (tmp_path / "segredo.json").write_text("{}")
    with pytest.raises(SessionStoreError):
        store.load("../segredo")
    evil = Session("p", session_id="../fora")
    turn(evil, "oi")
    with pytest.raises(SessionStoreError):
        store.save(evil)
    assert not (tmp_path / "fora.json").exists()


def test_latest_is_the_most_recently_saved(store: FileSessionStore) -> None:
    old, new = Session("p"), Session("p")
    turn(old, "antiga")
    turn(new, "nova")
    store.save(old)
    store.save(new)
    os.utime(store.directory / f"{old.id}.json", (1, 1))  # make "old" clearly older
    latest = store.latest()
    assert latest is not None and latest.id == new.id
    # resuming and saving "old" again makes it the latest
    store.save(old)
    latest = store.latest()
    assert latest is not None and latest.id == old.id


def test_latest_skips_damaged_files(store: FileSessionStore) -> None:
    good = Session("p")
    turn(good, "boa")
    store.save(good)
    os.utime(store.directory / f"{good.id}.json", (1, 1))
    (store.directory / "estragada.json").write_text("lixo")  # newer, but unreadable
    latest = store.latest()
    assert latest is not None and latest.id == good.id


def test_latest_and_list_without_any_session(store: FileSessionStore) -> None:
    assert store.latest() is None and store.list_sessions() == []


def test_list_sessions_summarizes_newest_first(store: FileSessionStore) -> None:
    first, second = Session("p"), Session("p")
    first.updated_at = "2026-10-12T10:00:00+00:00"
    second.updated_at = "2026-10-13T10:00:00+00:00"
    turn(first, "a" * 200)
    turn(first, "outra")
    turn(second, "só uma")
    store.save(first)
    store.save(second)
    infos = store.list_sessions()
    assert [i.id for i in infos] == [second.id, first.id]
    assert infos[0].turns == 1 and infos[1].turns == 2
    assert infos[1].preview == "a" * 60


# --- agent: remembers, saves, resumes -----------------------------------------------------


def make_agent(
    turns: list[Any], store: FileSessionStore | None, session: Session | None = None
) -> tuple[Agent, FakeProvider]:
    reg, _ = make_registry()
    provider = FakeProvider(turns)
    agent = Agent(
        provider,
        Dispatcher(tool_registry=reg),
        session or Session("persona"),
        tool_registry=reg,
        store=store,
    )
    return agent, provider


def sent(provider: FakeProvider, request: int) -> str:
    return "\n".join(m.content or "" for m in provider.seen[request])


async def test_the_conversation_remembers_what_was_said(store: FileSessionStore) -> None:
    agent, provider = make_agent([say("Anotado."), say("Você disse Laravel.")], store)
    await agent.run("meu projeto usa Laravel 11")
    await agent.run("o que eu disse?")
    assert "Laravel 11" in sent(provider, 1) and "Anotado." in sent(provider, 1)


async def test_each_finished_turn_is_saved(store: FileSessionStore) -> None:
    agent, _ = make_agent([say("um"), say("dois")], store)
    await agent.run("oi")
    saved = store.load(agent.session.id)
    assert saved is not None and saved.history == agent.session.history
    await agent.run("e agora?")
    saved = store.load(agent.session.id)
    assert saved is not None and len(users(saved)) == 2


async def test_a_resumed_conversation_has_the_whole_context(store: FileSessionStore) -> None:
    first, _ = make_agent([say("Prefere ZSH, anotei.")], store)
    await first.run("eu prefiro ZSH")

    resumed = store.latest()
    assert resumed is not None
    second, provider = make_agent([say("ZSH.")], store, session=resumed)
    await second.run("qual shell eu prefiro?")

    request = sent(provider, 0)
    assert "eu prefiro ZSH" in request and "Prefere ZSH, anotei." in request
    final = store.load(resumed.id)
    assert final is not None and len(users(final)) == 2


async def test_a_failed_turn_is_rolled_back_and_the_saved_copy_matches(
    store: FileSessionStore,
) -> None:
    agent, _ = make_agent([say("tudo certo")], store)  # the second turn has no script: it fails
    await agent.run("primeiro")
    with pytest.raises(LLMUnavailableError):
        await agent.run("segundo")
    saved = store.load(agent.session.id)
    assert saved is not None and users(saved) == users(agent.session)
    assert len(users(saved)) == 1


async def test_a_broken_store_never_ends_the_chat() -> None:
    class BrokenStore:
        def save(self, session: Session) -> None:
            raise OSError("sem permissão")

        def load(self, session_id: str) -> Session | None:
            return None

        def latest(self) -> Session | None:
            return None

        def list_sessions(self) -> list[Any]:
            return []

    agent, _ = make_agent([say("segue"), say("firme")], BrokenStore())
    assert await agent.run("oi") == "segue"
    assert await agent.run("ainda aí?") == "firme"


async def test_the_budget_is_applied_before_each_request(store: FileSessionStore) -> None:
    session = Session("persona", max_turns=50, max_tokens=300)
    agent, provider = make_agent([say("r" * 400) for _ in range(5)], store, session=session)
    for i in range(5):
        await agent.run(f"pergunta {i} " + "x" * 400)
    last = sent(provider, 4)
    assert "pergunta 4" in last and "pergunta 0" not in last


# --- CLI: juniper --resume ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.registry, "discover", lambda *a, **k: None)


def use_provider(monkeypatch: pytest.MonkeyPatch, provider: FakeProvider) -> None:
    monkeypatch.setattr(cli, "build_router", lambda name: LLMRouter([provider]))


def test_every_chat_is_saved_where_the_settings_say(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("Oi!")]))
    assert runner.invoke(cli.app, ["oi"]).exit_code == 0
    files = list(settings.memory.session.dir.glob("*.json"))
    assert len(files) == 1 and files[0].stat().st_mode & 0o077 == 0


def test_resume_without_a_previous_conversation_starts_a_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_provider(monkeypatch, FakeProvider([say("Oi!")]))
    result = runner.invoke(cli.app, ["--resume", "oi"])
    assert result.exit_code == 0
    assert "Não achei nenhuma conversa anterior" in result.output and "Oi!" in result.output


def test_resume_picks_the_conversation_back_up(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("Anotei: ZSH.")]))
    runner.invoke(cli.app, ["eu", "prefiro", "ZSH"])

    second = FakeProvider([say("ZSH, claro.")])
    use_provider(monkeypatch, second)
    result = runner.invoke(cli.app, ["-r", "qual", "shell", "eu", "uso?"])

    assert result.exit_code == 0
    assert "Retomando a conversa de" in result.output and "1 mensagem" in result.output
    assert "ZSH, claro." in result.output
    assert "eu prefiro ZSH" in sent(second, 0) and "Anotei: ZSH." in sent(second, 0)
    assert len(list(settings.memory.session.dir.glob("*.json"))) == 1  # same conversation


def test_without_resume_a_new_conversation_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("um")]))
    runner.invoke(cli.app, ["primeira"])
    second = FakeProvider([say("dois")])
    use_provider(monkeypatch, second)
    runner.invoke(cli.app, ["segunda"])
    assert "primeira" not in sent(second, 0)
    assert len(list(settings.memory.session.dir.glob("*.json"))) == 2


def test_resume_in_the_interactive_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("Anotei: Laravel 11.")]))
    runner.invoke(cli.app, ["o", "projeto", "X", "usa", "Laravel", "11"])
    second = FakeProvider([say("Laravel 11.")])
    use_provider(monkeypatch, second)
    result = runner.invoke(cli.app, ["--resume"], input="que framework o X usa?\n/sair\n")
    assert result.exit_code == 0 and "Laravel 11." in result.output
    assert "projeto X usa Laravel 11" in sent(second, 0)
