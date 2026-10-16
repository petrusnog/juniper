"""O diário das mensagens (message.log): cada interação em texto corrido, camada por camada."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients import cli, remote
from brain.clients.chat import ChatHandler
from brain.config import settings
from brain.eventlog import event_log
from brain.llm.router import LLMRouter
from brain.memory.session import FileSessionStore
from brain.trace import FileSink, message_trace
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import server_side
from tests.test_remote_cli import LiveServer, jun, serve, server_mode  # noqa: F401

runner = CliRunner(env={"COLUMNS": "250"})


@pytest.fixture
def log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Liga o diário e aponta o arquivo para a pasta do teste."""
    path = tmp_path / "trace" / "message.log"
    monkeypatch.setattr(settings.trace, "enabled", True)
    monkeypatch.setattr(settings.trace, "file", path)
    return path


def text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def in_order(text: str, *needles: str) -> None:
    """Cada trecho aparece depois do anterior (a ordem é a das camadas)."""
    position = 0
    for needle in needles:
        found = text.find(needle, position)
        assert found >= 0, f"faltou (ou fora de ordem): {needle!r}\n---\n{text}"
        position = found + len(needle)


# --- o mecanismo --------------------------------------------------------------------------


def test_disabled_by_default_and_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "message.log"
    monkeypatch.setattr(settings.trace, "file", path)
    assert settings.trace.enabled is False
    message_trace.begin("conversa x · modo local")
    message_trace.step("Terminal (CLI)", "Você digitou «oi».")
    assert not path.exists()


def test_a_interaction_has_a_header_and_timestamped_prose_lines(log: Path) -> None:
    message_trace.begin("conversa abc · modo local")
    message_trace.step("Terminal (CLI)", "Você digitou «oi».")
    message_trace.step("Agente", "Recebi a mensagem.")
    text = text_of(log)
    assert re.search(r"INTERAÇÃO \d+ · \w+(-feira)?, \d\d/\d\d/\d{4} às \d\d:\d\d:\d\d", text)
    assert "conversa abc · modo local" in text
    assert re.search(
        r"^\[\d\d:\d\d:\d\d\.\d{3}\] Terminal \(CLI\)\. Você digitou «oi»\.$", text, re.M
    )
    in_order(text, "Terminal (CLI)", "Agente")


def test_the_file_is_private(log: Path) -> None:
    message_trace.begin("x")
    assert (log.stat().st_mode & 0o777) == 0o600


def test_secrets_are_masked_like_in_the_event_log(log: Path) -> None:
    message_trace.begin("x")
    from brain.trace import quote

    message_trace.step(
        "Terminal (CLI)",
        f"Você digitou {quote('minha chave é gsk_abcdefghijklmnopqrstuvwxyz0123')}.",
    )
    assert "gsk_abcdefghij" not in text_of(log) and "***" in text_of(log)


def test_long_text_is_clipped(log: Path) -> None:
    from brain.trace import quote

    out = quote("a" * 5000)
    assert len(out) < 700 and "caracteres" in out


def test_event_log_events_become_sentences(log: Path) -> None:
    message_trace.begin("x")
    event_log.log("llm_request", messages=4, tools=9)
    event_log.log(
        "llm_response",
        provider="deepinfra",
        latency_ms=812,
        finish_reason="stop",
        usage={"total_tokens": 1840},
        tool_calls=["list_events"],
        chars=120,
    )
    event_log.log(
        "tool_call",
        name="list_events",
        tier="read",
        args={"days": 7},
        decision="auto",
        success=True,
        output="3 eventos",
        duration_ms=45,
    )
    event_log.log(
        "tool_call",
        name="delete_event",
        tier="destructive",
        args={"id": "1"},
        decision="pending",
        success=True,
        output="PENDENTE",
        duration_ms=2,
    )
    event_log.log(
        "memory_recall",
        found=2,
        injected=[{"id": "m1", "type": "fact", "score": 0.91}],
        tokens=30,
        profile_tokens=80,
        timings_ms={},
    )
    event_log.log("failover", provider="deepinfra", type="LLMTimeoutError", message="x")
    event_log.log(
        "timing",
        scope="turn",
        total_ms=1500,
        stages_ms={"llm": 1200, "llm_first_token": 300},
        llm_calls=2,
        recall_ms={},
    )
    text = text_of(log)
    for fragment in (
        "4 mensagens",
        "9 ferramentas",
        "deepinfra respondeu em 812 ms",
        "1840 tokens",
        "list_events",
        "só leitura",
        "executei direto",
        "NÃO executei",
        "pendente até você confirmar",
        "injetei 1 no prompt",
        "perfil também foi junto",
        "falhou (LLMTimeoutError)",
        "turno inteiro levou 1500 ms",
        "llm_first_token 300 ms",
    ):
        assert fragment in text, fragment


def test_events_outside_an_interaction_are_ignored(log: Path) -> None:
    from brain.trace import _current

    _current.set(None)
    event_log.log("llm_request", messages=1, tools=0)
    assert not log.exists()


def test_file_sink_survives_an_unwritable_path(tmp_path: Path) -> None:
    blocker = tmp_path / "arquivo"
    blocker.write_text("x")
    FileSink(blocker / "dentro" / "message.log").write("linha\n")  # não levanta


# --- a travessia completa -----------------------------------------------------------------


def test_server_mode_walks_every_layer_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: Path
) -> None:
    provider = FakeProvider([say("Amanhã ", "tem dentista.")])
    with serve(tmp_path, provider) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun("o que tenho amanhã?")
    assert result.exit_code == 0, result.output
    text = text_of(log)
    in_order(
        text,
        "INTERAÇÃO",
        "Terminal (CLI). Você digitou «o que tenho amanhã?»",
        "Cliente remoto · WebSocket. Montei a mensagem chat.send",
        "Servidor · WebSocket. Recebi o quadro chat.send",
        "Servidor · conversa. A conversa",
        "Cliente remoto · WebSocket. Recebi chat.started",
        "Agente. Recebi a mensagem «o que tenho amanhã?»",
        "Agente → roteador de LLMs. Montei o pedido",
        "Cliente remoto · WebSocket. Chegou o primeiro pedaço",
        "LLM. O provedor fake respondeu",
        "Agente. A resposta final ficou pronta",
        "Servidor · conversa. O agente terminou o turno",
        "Cliente remoto · WebSocket. Recebi chat.end",
        "Terminal (CLI). Mostrei a resposta no terminal",
    )
    assert "«Amanhã tem dentista.»" in text and "modo server" in text
    assert text.count("INTERAÇÃO") == 1


def test_each_message_is_its_own_interaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: Path
) -> None:
    with serve(tmp_path, FakeProvider([say("Um."), say("Dois.")])) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        result = jun(input="primeira\nsegunda\n/sair\n")
    assert result.exit_code == 0, result.output
    text = text_of(log)
    assert text.count("INTERAÇÃO") == 2
    in_order(text, "«primeira»", "«Um.»", "INTERAÇÃO", "«segunda»", "«Dois.»")


def test_tool_use_and_confirmation_show_up_in_the_story(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: Path
) -> None:
    from tests.test_agent import batch_registry, several

    reg, _ = batch_registry()
    server_side(reg)
    provider = FakeProvider(
        [several(("wipe", {"id": "1"})), say("Posso apagar o item 1?"), say("Apagado!")]
    )
    with serve(tmp_path, provider, tool_registry=reg) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        assert jun(input="apaga o item 1\nsim\n/sair\n").exit_code == 0
    text = text_of(log)
    in_order(
        text,
        "pediu a ferramenta «wipe»",
        "ficou pendente até você confirmar",
        "você respondeu sim",
        "pediu a ferramenta «wipe»",
        "executei porque você confirmou",
    )


def test_server_steps_are_not_sent_when_the_server_has_it_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: Path
) -> None:
    """O cliente pede, mas quem manda no que o servidor entrega é o `trace.enabled` dele."""
    with serve(tmp_path, FakeProvider([say("Oi.")])) as live:
        monkeypatch.setattr(settings.deployment.server, "port", live.port)
        original = remote.RemoteClient._hello

        def hello_without_server_permission(self: Any) -> Any:
            monkeypatch.setattr(
                settings.trace, "enabled", False
            )  # o servidor (esta thread) lê isto
            try:
                return original(self)
            finally:
                monkeypatch.setattr(settings.trace, "enabled", True)

        monkeypatch.setattr(remote.RemoteClient, "_hello", hello_without_server_permission)
        assert jun("oi").exit_code == 0
    text = text_of(log)
    assert "Servidor · WebSocket" not in text  # nenhum passo do servidor chegou
    assert "Terminal (CLI)" in text


def test_local_mode_walks_the_local_layers_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: Path
) -> None:
    monkeypatch.setattr(cli, "build_router", lambda name: LLMRouter([FakeProvider([say("Olá!")])]))
    monkeypatch.setattr(settings.deployment, "mode", "local")
    result = runner.invoke(cli.app, ["oi"])
    assert result.exit_code == 0, result.output
    text = text_of(log)
    in_order(
        text,
        "Terminal (CLI). Você digitou «oi». No modo local não há servidor nem rede",
        "Agente. Recebi a mensagem «oi»",
        "Agente → roteador de LLMs",
        "Agente. A resposta final ficou pronta",
        "Terminal (CLI). Mostrei a resposta no terminal",
    )
    assert "Servidor" not in text and "Cliente remoto" not in text


def test_utc_timestamps_from_the_server_are_shown_in_local_time(log: Path) -> None:
    moment = datetime(2026, 10, 16, 4, 12, 3, 482000, tzinfo=UTC)
    message_trace.file_line("Servidor · WebSocket", "Recebi.", moment)
    assert re.search(r"^\[0\d:12:03\.482\] Servidor", text_of(log), re.M)


def test_handler_import_is_wired() -> None:
    assert ChatHandler and FileSessionStore  # os módulos de apoio importam sem ciclo
