"""juniper memory prompt: o que a V2 acrescenta ao prompt de uma mensagem (T-121)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients.cli import app
from brain.config import V2AffectSettings, V2OpinionSettings, V2PersonaSettings, settings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.persona import PersonaBook
from brain.memory.v2.store import V2Data
from tests.test_extraction import FakeEmbedder
from tests.test_v2_context import MESSAGE, seeded


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> V2Data:
    data, _ = seeded(2)
    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", lambda: None)
    monkeypatch.setattr("brain.clients.memory_v2_cli.open_v2_data", lambda: data)
    monkeypatch.setattr("brain.memory.embeddings.open_embedding_provider", FakeEmbedder)
    # the command reads the real settings: silence the always-on parts so only the data speaks
    monkeypatch.setattr(settings.memory.v2, "persona", V2PersonaSettings(always_describe=False))
    monkeypatch.setattr(settings.memory.v2, "opinions", V2OpinionSettings(always_on=0))
    from brain.clients.cli import console

    monkeypatch.setattr(console, "width", 300, raising=False)
    return data


def run(*args: str) -> Any:
    return CliRunner().invoke(app, ["memory", "prompt", *args])


def snapshot(data: V2Data) -> list[Any]:
    return [
        [d.to_doc() for d in repo.list()]
        for repo in (data.beliefs, data.interests, data.affects, data.concerns, data.journal)
    ] + [data.persona.current(), data.persona.history.count()]


def test_it_prints_each_block_with_its_name_and_the_token_estimate(cli: V2Data) -> None:
    PersonaBook(cli, V2PersonaSettings()).ensure()
    AffectBook(cli, V2AffectSettings()).record(
        "tristeza", -0.8, 0.9, ["turn:s:1", "concern:bem-estar-do-petrus"]
    )

    result = run(MESSAGE)

    assert result.exit_code == 0, result.output
    out = result.output
    assert "── opinioes (~" in out and "<opinioes>" in out and "acho que:" in out
    assert "── estado (~" in out and "<estado>" in out
    assert "A V2 acrescentaria" in out and "bloco(s)" in out and "tokens" in out


def test_the_message_can_be_given_without_quotes(cli: V2Data) -> None:
    assert "<opinioes>" in run(*MESSAGE.split()).output


def test_when_there_is_nothing_to_add_it_says_so(cli: V2Data) -> None:
    result = run("xyzzy plugh")
    assert result.exit_code == 0 and "Nenhum bloco" in result.output


def test_a_message_of_suffering_shows_the_welcome_block_and_no_state(cli: V2Data) -> None:
    AffectBook(cli, V2AffectSettings()).record("tristeza", -0.8, 0.9, ["turn:s:1"])
    out = run("minha avó faleceu hoje").output
    assert "── acolher (~" in out and "Acolha primeiro" in out and "── estado" not in out


def test_running_it_changes_nothing_in_the_database(cli: V2Data) -> None:
    before = snapshot(cli)
    run(MESSAGE)
    run(MESSAGE)
    assert snapshot(cli) == before


def test_the_previous_message_option_is_accepted(cli: V2Data) -> None:
    result = run("sim", "--anterior", MESSAGE)
    assert result.exit_code == 0 and "<opinioes>" in result.output


def test_an_empty_message_is_refused(cli: V2Data) -> None:
    result = run("   ")
    assert result.exit_code == 1 and "Escreva a mensagem" in result.output


def test_without_the_v2_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def off() -> None:
        raise MemoryUnavailableError("A Memória V2 está desligada.")

    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", off)
    result = run("bom dia")
    assert result.exit_code == 1 and "desligada" in result.output


def test_preview_uses_the_same_blocks_as_the_conversation(cli: V2Data) -> None:
    import asyncio

    from brain.memory.v2.context import V2Context

    context = V2Context(cli, FakeEmbedder(), clock=lambda: datetime(2026, 10, 22, tzinfo=UTC))
    sections = asyncio.run(context.preview(MESSAGE))
    real = asyncio.run(
        V2Context(
            cli, FakeEmbedder(), clock=lambda: datetime(2026, 10, 22, tzinfo=UTC)
        ).context_for(MESSAGE)
    )
    assert "\n\n".join(text for _, text in sections) == real.block
