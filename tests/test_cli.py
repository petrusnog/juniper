"""Tests for the CLI client (brain.clients.cli) with a scripted fake provider."""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from brain.clients import cli, voice
from brain.llm.errors import LLMAuthError, LLMError, LLMRateLimitError, LLMUnavailableError
from brain.llm.router import LLMRouter
from tests.test_agent import FakeProvider, say

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.registry, "discover", lambda *a, **k: None)


def use_provider(monkeypatch: pytest.MonkeyPatch, provider: FakeProvider) -> None:
    monkeypatch.setattr(cli, "build_router", lambda name: LLMRouter([provider]))


def test_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.output.startswith(voice.JUNIPER_TAG) and "versão" in result.output


def test_one_shot_streams_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("Oi, ", "pessoa.")]))
    result = runner.invoke(cli.app, ["oi"])
    assert result.exit_code == 0
    assert f"{voice.JUNIPER_TAG} Oi, pessoa." in result.output


def test_reply_with_brackets_is_not_treated_as_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("use [bold]isso[/bold] e [x]")]))
    result = runner.invoke(cli.app, ["oi"])
    assert "[bold]isso[/bold] e [x]" in result.output


def test_repl_runs_until_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("um"), say("dois")]))
    result = runner.invoke(cli.app, [], input="oi\ntchau\n/sair\n")
    assert result.exit_code == 0
    assert "um" in result.output and "dois" in result.output


def test_repl_ends_on_eof_and_skips_blank_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("um")]))
    result = runner.invoke(cli.app, [], input="\noi\n")
    assert result.exit_code == 0
    assert "um" in result.output


def test_unknown_provider_is_rejected() -> None:
    result = runner.invoke(cli.app, ["oi", "--provider", "gpt"])
    assert result.exit_code != 0
    assert "provider desconhecido" in result.output


def test_provider_option_builds_single_provider_router() -> None:
    router = cli.build_router("ollama")
    assert [p.name for p in router.providers] == ["ollama"]


def test_missing_groq_key_shows_env_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoKey(FakeProvider):
        async def stream(self, messages: list[Any], **kwargs: Any) -> Any:  # type: ignore[override]
            raise LLMAuthError("Groq: chave de API ausente. Defina JUNIPER_GROQ_API_KEY no .env")
            yield  # pragma: no cover

    use_provider(monkeypatch, NoKey([]))
    result = runner.invoke(cli.app, ["oi"])
    assert result.exit_code == 1
    assert ".env" in result.output


def test_repl_survives_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # First turn: no script left → LLMUnavailableError; the REPL keeps going.
    use_provider(monkeypatch, FakeProvider([]))
    result = runner.invoke(cli.app, [], input="oi\n/sair\n")
    assert result.exit_code == 0
    assert "Não consegui falar com nenhum LLM" in result.output


def test_friendly_error_hints() -> None:
    text = cli.friendly_error(
        LLMError("Todos falharam (groq: LLMRateLimitError: x; ollama: LLMUnavailableError: y)")
    )
    assert "Limite da Groq" in text and "ollama serve" in text


async def test_line_reader_reuses_pending_read_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading

    gate = threading.Event()
    calls: list[str] = []

    def fake_input(prompt: str = "") -> str:
        calls.append(prompt)
        gate.wait(5)
        return "resposta"

    monkeypatch.setattr("builtins.input", fake_input)
    reader = cli.LineReader()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(reader.read("a> "), timeout=0.05)
    gate.set()
    assert await reader.read("b> ") == "resposta"
    assert calls == ["a> "]  # no second reader thread was started


@pytest.fixture
def legacy_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(args: list[str]) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(cli, "run_legacy_command", fake)
    return calls


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["search", "4911", "6531"], ["search", "4911", "6531"]),
        (["grep", "4911"], ["search", "4911"]),
        (["gitgrep", "4911"], ["search", "4911"]),
        (
            ["deploy", "6531", "--tasks", "7432", "--stage"],
            ["deploy", "6531", "--tasks", "7432", "--stage"],
        ),
        (["deploy", "4911", "abc123", "--hash"], ["deploy", "4911", "abc123", "--hash"]),
        (["deployfeature", "4911"], ["deploy", "4911"]),
    ],
)
def test_legacy_commands_pass_arguments_through(
    legacy_calls: list[list[str]], argv: list[str], expected: list[str]
) -> None:
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0
    assert legacy_calls == [expected]


def test_legacy_exit_code_is_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_legacy_command", lambda args: 3)
    assert runner.invoke(cli.app, ["search", "x"]).exit_code == 3


def test_aliases_are_hidden_from_help() -> None:
    output = runner.invoke(cli.app, ["--help"]).output
    assert "search" in output and "deploy" in output and "tools" in output
    assert "gitgrep" not in output and "deployfeature" not in output


def test_tools_command_lists_registered_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_agent import make_registry

    reg, _ = make_registry()
    monkeypatch.setattr(cli, "registry", reg)
    result = runner.invoke(cli.app, ["tools"])
    assert result.exit_code == 0
    for text in ("echo", "write_thing", "nuke", "read", "forbidden", "executor"):
        assert text in result.output


# --- Voz da Juniper (padrão de fala) ------------------------------------------------


def test_every_line_juniper_says_carries_the_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([say("Oi.")]))
    result = runner.invoke(cli.app, [], input="oi\n/sair\n")
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines[0].startswith(voice.JUNIPER_TAG)  # greeting
    assert any(f"{voice.JUNIPER_TAG} Oi." in line for line in lines)  # AI reply
    assert lines[-1].endswith(f"{voice.JUNIPER_TAG} Até logo!")  # goodbye (after the prompt)


def test_repl_prompt_uses_the_user_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    async def fake_read(self: cli.LineReader, prompt: str) -> str:
        prompts.append(prompt)
        raise EOFError

    use_provider(monkeypatch, FakeProvider([]))
    monkeypatch.setattr(cli.LineReader, "read", fake_read)
    result = runner.invoke(cli.app, [])
    assert prompts == [f"{voice.USER_TAG} "] and result.exit_code == 0


def test_no_arguments_opens_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([]))
    result = runner.invoke(cli.app, [], input="/sair\n")
    assert result.exit_code == 0 and "tô te ouvindo" in result.output


def test_free_words_are_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider([say("Oi!")])
    use_provider(monkeypatch, provider)
    result = runner.invoke(cli.app, ["o", "que", "tenho", "amanhã?"])
    assert result.exit_code == 0 and "Oi!" in result.output


def test_help_still_lists_commands() -> None:
    output = runner.invoke(cli.app, ["--help"]).output
    assert "search" in output and "_chat" not in output


def test_tools_command_introduces_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_agent import make_registry

    monkeypatch.setattr(cli, "registry", make_registry()[0])
    assert runner.invoke(cli.app, ["tools"]).output.startswith(voice.JUNIPER_TAG)


def test_unknown_provider_is_said_by_juniper() -> None:
    result = runner.invoke(cli.app, ["chat", "oi", "-p", "gpt"])
    assert result.exit_code == 2 and result.output.startswith(voice.JUNIPER_TAG)


def test_say_formats_single_and_multiline_speech() -> None:
    out = Console(record=True, width=80)
    voice.say(out, "primeira\nsegunda")
    assert out.export_text(clear=False) == f"{voice.JUNIPER_TAG} primeira\n   segunda\n"


def test_say_never_parses_markup() -> None:
    out = Console(record=True, width=80)
    voice.say(out, "use [bold]isto[/bold] e [x]")
    assert "[bold]isto[/bold] e [x]" in out.export_text(clear=False)


def test_no_stray_tag_when_the_llm_fails_before_streaming(monkeypatch: pytest.MonkeyPatch) -> None:
    use_provider(monkeypatch, FakeProvider([]))
    result = runner.invoke(cli.app, ["oi"])
    assert not any(line.strip() == voice.JUNIPER_TAG for line in result.output.splitlines())


def test_shell_and_python_voices_are_identical() -> None:
    """shell/core/voice.sh is the shell twin of brain/clients/voice.py: same strings."""
    from pathlib import Path

    text = (Path(cli.__file__).resolve().parents[2] / "shell" / "core" / "voice.sh").read_text()
    assert f'JUNIPER_TAG="{voice.JUNIPER_TAG}"' in text
    assert f'JUNIPER_USER_TAG="{voice.USER_TAG}"' in text
    for script in ("install.sh", "uninstall.sh"):
        body = (Path(cli.__file__).resolve().parents[2] / "scripts" / script).read_text()
        assert f'JUNIPER_TAG="{voice.JUNIPER_TAG}"' in body
        assert f'USER_TAG="{voice.USER_TAG}"' in body


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["🌿 JUNIPER: ", "Oi!"], "Oi!"),
        (["Jun", "iper", ": Oi", "!"], "Oi!"),
        (["juniper: oi"], "oi"),
        (
            ["Oi, ", "tudo bem? Aqui vai uma resposta comprida."],
            "Oi, tudo bem? Aqui vai uma resposta comprida.",
        ),
        (["Oi"], "Oi"),  # shorter than the look-ahead window: flushed at the end of the reply
        (
            ["Falei com a Juniper: ela disse oi"],
            "Falei com a Juniper: ela disse oi",
        ),  # not at start
    ],
)
def test_model_echo_of_its_own_name_is_dropped(tokens: list[str], expected: str) -> None:
    out = Console(record=True, width=120)
    printer = cli.Printer(out)
    printer.begin_reply()
    for token in tokens:
        printer.token(token)
    printer.end_line()
    assert out.export_text(clear=False) == f"{voice.JUNIPER_TAG} {expected}\n"


def test_tag_is_reprinted_after_a_fixed_line() -> None:
    """Text streamed after a confirmation (a new Juniper line) gets its own tag."""
    out = Console(record=True, width=120)
    printer = cli.Printer(out)
    printer.begin_reply()
    printer.token("Vou olhar a agenda, um segundo.")
    printer.say("Posso executar create_event? [y/N]")
    printer.token("Marquei!")
    printer.end_line()
    lines = out.export_text(clear=False).splitlines()
    assert [line.split(":")[0] for line in lines] == ["🌿 JUNIPER"] * 3


# --- Failover visível ---------------------------------------------------------------


def failover_router() -> LLMRouter:
    from tests.test_llm_router import ScriptedProvider

    return LLMRouter(
        [
            ScriptedProvider("groq", error=LLMAuthError("credenciais recusadas (HTTP 401)")),
            ScriptedProvider("ollama", chunks=("Oi, ", "sou o Ollama.")),
        ]
    )


def test_failover_is_announced_in_juniper_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "build_router", lambda name: failover_router())
    result = runner.invoke(cli.app, ["oi"])
    assert result.exit_code == 0
    assert "A Groq recusou as credenciais. Vou tentar o Ollama." in result.output
    assert "Sem GPU" in result.output
    assert "Oi, sou o Ollama." in result.output
    assert "Failover:" not in result.output  # no raw library warning in the conversation


def test_router_calls_on_failover_with_the_next_provider() -> None:
    import asyncio

    router = failover_router()
    seen: list[tuple[str, str, str | None]] = []
    router.on_failover = lambda failed, exc, nxt: seen.append((failed, type(exc).__name__, nxt))

    async def drain() -> None:
        async for _ in router.stream([]):
            pass

    asyncio.run(drain())
    assert seen == [("groq", "LLMAuthError", "ollama")]


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (LLMRateLimitError("x"), "O limite da Groq acabou"),
        (LLMUnavailableError("x"), "A Groq não respondeu"),
    ],
)
def test_failover_reasons(exc: LLMError, expected: str) -> None:
    out = Console(record=True, width=120)
    cli.Printer(out).notify_failover("groq", exc, "ollama")
    assert expected in out.export_text(clear=False)


def test_last_provider_failing_is_said_without_a_next_step() -> None:
    out = Console(record=True, width=120)
    cli.Printer(out).notify_failover("ollama", LLMUnavailableError("x"), None)
    assert "Falhou: o Ollama não respondeu." in out.export_text(clear=False)


def use_registry(monkeypatch: pytest.MonkeyPatch, reg: Any) -> None:
    """Make the chat command (agent and dispatcher) use a test registry."""
    from brain.core import agent as agent_module
    from brain.core import dispatcher as dispatcher_module

    monkeypatch.setattr(cli, "registry", reg)
    monkeypatch.setattr(agent_module, "default_registry", reg)
    monkeypatch.setattr(dispatcher_module, "default_registry", reg)


# --- approval happens in the conversation, not in a [y/N] prompt ---------------------------


def test_chat_confirms_by_conversation_not_by_a_yn_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_agent import batch_registry, several

    reg, ran = batch_registry()
    use_registry(monkeypatch, reg)
    use_provider(
        monkeypatch,
        FakeProvider(
            [
                several(("wipe", {"id": "1"}), ("wipe", {"id": "2"})),
                say("Posso apagar esses dois agendamentos? É irreversível."),
                say("Apagado!"),
            ]
        ),
    )
    result = runner.invoke(cli.app, [], input="cancela os de amanhã\nSim.\n/sair\n")
    assert result.exit_code == 0
    assert "Posso apagar esses dois agendamentos? É irreversível." in result.output
    assert "Apagado!" in result.output
    assert "[y/N]" not in result.output and "IRREVERSÍVEL" not in result.output
    assert ran == ["wipe:1", "wipe:2"]
    # the dialogue the user wants: JUNIPER asks first, and only after the "sim" reports it done
    flat = " ".join(result.output.split())
    assert flat.index("Posso apagar") < flat.index("Apagado!")


def test_chat_answering_no_leaves_everything_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_agent import batch_registry, several

    reg, ran = batch_registry()
    use_registry(monkeypatch, reg)
    use_provider(
        monkeypatch,
        FakeProvider(
            [several(("wipe", {"id": "1"})), say("Posso apagar?"), say("Tranquilo, deixei.")]
        ),
    )
    result = runner.invoke(cli.app, [], input="apaga\nNão\n/sair\n")
    assert result.exit_code == 0 and ran == []
    assert "Tranquilo, deixei." in result.output
