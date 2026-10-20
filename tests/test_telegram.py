"""O bot do Telegram (T-095): o núcleo contra um cérebro de verdade, sem rede externa."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from brain.clients import protocol as p
from brain.clients import telegram as tg
from brain.clients.remote import ChatResult, ConnectionLostError, RemoteClient, ServerError
from brain.llm.errors import LLMRateLimitError
from tests.test_agent import FakeProvider, batch_registry, say, several
from tests.test_chat_server import (
    TOKEN,
    FailingProvider,
    GatedProvider,
    Scenario,
    server_side,
    wait_until,
)

OWNER = 1001
CHAT = 1001


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.typing_calls = 0
        self.fail = False

    async def send_text(self, chat_id: int, text: str) -> int | None:
        if self.fail:
            raise RuntimeError("telegram fora do ar")
        self.sent.append((chat_id, text))
        return len(self.sent)

    async def typing(self, chat_id: int) -> None:
        self.typing_calls += 1

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


def msg(text: str | None = "oi", **kwargs: Any) -> tg.Incoming:
    fields: dict[str, Any] = {
        "user_id": OWNER,
        "chat_id": CHAT,
        "chat_type": "private",
        "message_id": 1,
        "text": text,
    }
    fields.update(kwargs)
    return tg.Incoming(**fields)


def factory_for(s: Scenario) -> tg.ClientFactory:
    def make(user_id: int) -> tg.BrainClient:
        return RemoteClient(
            f"ws://127.0.0.1:{s.server.port}/v1/ws",
            TOKEN,
            device_id=f"telegram-{user_id}",
            device_name="Telegram",
            kind="telegram",
        )

    return make


@pytest.fixture
async def bridges() -> AsyncIterator[list[tg.TelegramBridge]]:
    """Fecha os bridges (e as conexões) no fim do teste."""
    created: list[tg.TelegramBridge] = []
    yield created
    for bridge in created:
        await bridge.close()


def make_bridge(
    bridges: list[tg.TelegramBridge],
    s: Scenario,
    messenger: FakeMessenger,
    allowed: list[int] | None = None,
    **kwargs: Any,
) -> tg.TelegramBridge:
    bridge = tg.TelegramBridge(
        [OWNER] if allowed is None else allowed, messenger, factory_for(s), **kwargs
    )
    bridges.append(bridge)
    return bridge


# --- o caminho feliz -----------------------------------------------------------------------


async def test_authorized_message_gets_the_brains_reply(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("Oi, tudo bem?")]), tmp_path) as s:
        await make_bridge(bridges, s, messenger).handle(msg("oi"))
    assert messenger.sent == [(CHAT, "Oi, tudo bem?")]
    assert messenger.typing_calls >= 1


async def test_the_bot_identifies_itself_as_a_telegram_client(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    async with Scenario(FakeProvider([say("ok")]), tmp_path) as s:
        await make_bridge(bridges, s, FakeMessenger()).handle(msg("oi"))
        (conn,) = s.server.connections
        assert (conn.kind, conn.device_id, conn.device_name) == (
            "telegram",
            f"telegram-{OWNER}",
            "Telegram",
        )


async def test_confirmation_is_the_next_message(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    registry, ran = batch_registry()
    server_side(registry)  # a tool roda no servidor, não em um dispositivo
    provider = FakeProvider(
        [several(("wipe", {"id": "1"})), say("Posso apagar o item 1?"), say("Apagado!")]
    )
    messenger = FakeMessenger()
    async with Scenario(provider, tmp_path, tool_registry=registry) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("apaga o item 1"))
        assert ran == []
        await bridge.handle(msg("Sim."))
    assert messenger.texts == ["Posso apagar o item 1?", "Apagado!"]
    assert ran == ["wipe:1"]


async def test_the_conversation_continues_in_the_same_session_after_a_restart(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    async with Scenario(FakeProvider([say("Primeira."), say("Segunda.")]), tmp_path) as s:
        first = make_bridge(bridges, s, FakeMessenger())
        await first.handle(msg("um"))
        (conn,) = s.server.connections
        session = conn.session_id
        await first.close()
        await wait_until(lambda: not s.server.connections)

        messenger = FakeMessenger()
        await make_bridge(bridges, s, messenger).handle(msg("dois"))  # um bot novo
        (again,) = s.server.connections
        assert again.session_id == session  # `resume: latest`
    assert messenger.texts == ["Segunda."]


# --- quem pode falar com o bot -------------------------------------------------------------


async def test_unauthorized_user_gets_no_reply_and_never_reaches_the_brain(
    tmp_path: Path, bridges: list[tg.TelegramBridge], caplog: pytest.LogCaptureFixture
) -> None:
    messenger = FakeMessenger()
    caplog.set_level(logging.WARNING)
    async with Scenario(FakeProvider([say("não deveria")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("ei, sou eu de outro número", user_id=555, chat_id=555))
        assert s.server.connections == []
    assert messenger.sent == []
    assert "id=555" in caplog.text  # é assim que o dono descobre o próprio ID
    assert "outro número" not in caplog.text  # o texto nunca vai ao log


async def test_an_empty_allowlist_means_nobody(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("não deveria")]), tmp_path) as s:
        await make_bridge(bridges, s, messenger, allowed=[]).handle(msg("oi"))
        assert s.server.connections == []
    assert messenger.sent == []


@pytest.mark.parametrize("kind", ["group", "supergroup", "channel"])
async def test_groups_and_channels_are_ignored_even_for_the_owner(
    tmp_path: Path, bridges: list[tg.TelegramBridge], kind: str
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("não deveria")]), tmp_path) as s:
        await make_bridge(bridges, s, messenger).handle(msg("oi", chat_type=kind))
        assert s.server.connections == []
    assert messenger.sent == []


async def test_other_bots_are_ignored(tmp_path: Path, bridges: list[tg.TelegramBridge]) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("não deveria")]), tmp_path) as s:
        await make_bridge(bridges, s, messenger).handle(msg("oi", is_bot=True))
    assert messenger.sent == []


# --- o que o bot não faz -------------------------------------------------------------------


async def test_forwarded_and_non_text_messages_get_a_notice_and_never_reach_the_brain(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("não deveria")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("texto de terceiros", forwarded=True))
        await bridge.handle(msg(None))  # áudio, foto, arquivo...
        assert s.server.connections == []
    assert messenger.texts == [tg.NO_FORWARDS, tg.ONLY_TEXT]


async def test_commands_help_new_conversation_and_unknown(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("Primeira.")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("/ajuda"))
        await bridge.handle(msg("/start"))
        await bridge.handle(msg("/inexistente"))
        await bridge.handle(msg("oi"))
        (conn,) = s.server.connections
        before = conn.session_id
        await bridge.handle(msg("/novo@JuniperFromPetrusBot"))
        assert conn.session_id != before  # session.new
    assert messenger.texts == [
        tg.HELP_TEXT,
        tg.HELP_TEXT,
        tg.UNKNOWN_COMMAND,
        "Primeira.",
        tg.NEW_CONVERSATION,
    ]


async def test_commands_from_unauthorized_users_are_ignored(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([]), tmp_path) as s:
        await make_bridge(bridges, s, messenger).handle(msg("/ajuda", user_id=7, chat_id=7))
    assert messenger.sent == []


# --- um turno por vez, respostas longas e erros --------------------------------------------


async def test_a_second_message_during_a_turn_is_told_to_wait(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    provider = GatedProvider("pronto")
    messenger = FakeMessenger()
    async with Scenario(provider, tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        first = asyncio.create_task(bridge.handle(msg("primeira")))
        await asyncio.wait_for(provider.started.wait(), 3)
        await bridge.handle(msg("segunda"))
        assert messenger.texts == [tg.BUSY_TEXT]  # sem fila: respondeu na hora
        provider.release.set()
        await asyncio.wait_for(first, 3)
    assert messenger.texts == [tg.BUSY_TEXT, "parcial pronto"]


async def test_long_replies_are_split_under_the_telegram_limit(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    long = " ".join(f"palavra{i}" for i in range(2500))  # ~20.000 caracteres
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say(long)]), tmp_path) as s:
        await make_bridge(bridges, s, messenger).handle(msg("conta uma história"))
    assert len(messenger.sent) >= 5
    assert all(len(text) <= tg.TELEGRAM_MESSAGE_LIMIT for text in messenger.texts)
    assert " ".join(messenger.texts).split() == long.split()  # nada se perde


def test_split_message_prefers_paragraphs_then_lines_then_spaces() -> None:
    assert tg.split_message("curto") == ["curto"]
    assert tg.split_message("  ") == []
    text = "a" * 60 + "\n\n" + "b" * 60
    assert tg.split_message(text, 100) == ["a" * 60, "b" * 60]
    assert tg.split_message("x" * 250, 100) == ["x" * 100, "x" * 100, "x" * 50]  # sem separador


async def test_llm_failure_becomes_a_friendly_message_without_technical_details(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    provider = FailingProvider(LLMRateLimitError("groq: LLMRateLimitError: 429"), [say("Voltei!")])
    async with Scenario(provider, tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("oi"))
        await bridge.handle(msg("de novo"))  # o bot e a conexão sobrevivem
    first, second = messenger.texts
    assert "Não consegui falar com nenhum LLM" in first
    assert "Detalhe técnico" not in first and "429" not in first
    assert second == "Voltei!"


async def test_server_down_gets_the_unavailable_message_and_retries_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tg, "CONNECT_WAIT_SECONDS", 0.4)
    messenger = FakeMessenger()

    def dead(user_id: int) -> tg.BrainClient:
        return RemoteClient("ws://127.0.0.1:1/v1/ws", TOKEN, device_id="x", kind="telegram")

    bridge = tg.TelegramBridge([OWNER], messenger, dead)
    await bridge.handle(msg("oi"))
    await bridge.handle(msg("oi de novo"))  # tenta conectar outra vez, não fica preso no erro
    assert messenger.texts == [tg.UNAVAILABLE_TEXT, tg.UNAVAILABLE_TEXT]


async def test_a_telegram_failure_never_breaks_the_bot(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    messenger.fail = True
    async with Scenario(FakeProvider([say("a"), say("b")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("oi"))  # o Telegram recusou o envio: não levanta
        messenger.fail = False
        await bridge.handle(msg("e agora?"))
    assert messenger.texts == ["b"]


# --- queda da conexão no meio do turno (com um cliente de mentira) --------------------------


class DroppingClient:
    """Cai no meio do primeiro turno; a resposta ficou salva no servidor."""

    def __init__(self, history: list[p.HistoryItem], busy_first: int = 0) -> None:
        self._history = history
        self._busy = busy_first

    async def start(self, resume: p.Resume | None = None, wait: float = 15.0) -> p.Welcome:
        return p.Welcome(
            protocol_version=p.PROTOCOL_VERSION,
            server_version="t",
            heartbeat_seconds=30,
            session=p.WelcomeSession(id="s", resumed=False, turns=0),
        )

    async def chat(self, text: str, on_token: Callable[[str], None]) -> ChatResult:
        raise ConnectionLostError("caiu")

    async def request(self, frame: dict[str, Any], timeout: float = 60.0) -> p.ServerMessage:
        raise NotImplementedError

    async def history(self) -> list[p.HistoryItem]:
        if self._busy:
            self._busy -= 1
            raise ServerError(p.Error.from_code("turn_in_progress", "ainda respondendo"))
        return self._history

    async def wait_ready(self, timeout: float = 5.0) -> None:
        return None

    async def close(self) -> None:
        return None


async def test_a_connection_drop_mid_turn_recovers_the_reply_from_the_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(tg.asyncio, "sleep", no_sleep)
    history = [
        p.HistoryItem(role="user", text="oi"),
        p.HistoryItem(role="assistant", text="Pronto!"),
    ]
    messenger = FakeMessenger()
    bridge = tg.TelegramBridge([OWNER], messenger, lambda _: DroppingClient(history, busy_first=2))
    await bridge.handle(msg("oi"))
    assert messenger.texts == [tg.LOST_TEXT, "Pronto!"]


async def test_a_drop_with_no_saved_reply_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    history = [p.HistoryItem(role="user", text="oi")]  # o servidor só tem a pergunta
    messenger = FakeMessenger()
    bridge = tg.TelegramBridge([OWNER], messenger, lambda _: DroppingClient(history))
    await bridge.handle(msg("oi"))
    assert messenger.texts == [tg.LOST_TEXT, tg.UNFINISHED]


# --- privacidade: o texto nunca vai ao log -------------------------------------------------


class RecordingEventLog:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def log(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


async def test_message_text_never_reaches_the_event_log_or_logs(
    tmp_path: Path,
    bridges: list[tg.TelegramBridge],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = RecordingEventLog()
    monkeypatch.setattr(tg, "event_log", recorder)
    caplog.set_level(
        logging.DEBUG, logger="brain"
    )  # só o nosso código (a websockets em DEBUG imprime quadros)
    secret = "minha-senha-do-banco-9f3a"
    async with Scenario(FakeProvider([say("resposta-secreta-77")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, FakeMessenger())
        await bridge.handle(msg(f"guarda isso: {secret}"))
        await bridge.handle(msg(f"{secret} de intruso", user_id=9, chat_id=9))
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("brain"))
    dump = repr(recorder.calls) + ours
    assert secret not in dump and "resposta-secreta-77" not in dump
    actions = {kwargs.get("action") for _, kwargs in recorder.calls}
    assert {"message_in", "turn", "unauthorized"} <= actions


# --- o adaptador com a biblioteca ----------------------------------------------------------


def _update(**message: Any) -> Any:
    from telegram import Update

    body: dict[str, Any] = {
        "message_id": 5,
        "date": 0,
        "chat": {"id": 10, "type": "private"},
        "from": {"id": 10, "is_bot": False, "first_name": "P"},
        "text": "oi",
    }
    body.update(message)
    return Update.de_json({"update_id": 1, "message": body}, bot=None)


def test_a_telegram_update_becomes_an_incoming() -> None:
    incoming = tg.incoming_from_update(_update())
    assert incoming == tg.Incoming(
        user_id=10, chat_id=10, chat_type="private", message_id=5, text="oi"
    )


def test_forwarded_and_non_text_updates_are_flagged() -> None:
    forwarded = tg.incoming_from_update(
        _update(
            forward_origin={
                "type": "user",
                "date": 0,
                "sender_user": {"id": 3, "is_bot": False, "first_name": "X"},
            }
        )
    )
    assert forwarded is not None and forwarded.forwarded
    photo = tg.incoming_from_update(
        _update(text=None, photo=[{"file_id": "a", "file_unique_id": "b", "width": 1, "height": 1}])
    )
    assert photo is not None and photo.text is None


def test_an_update_without_a_message_is_not_an_incoming() -> None:
    from telegram import Update

    assert tg.incoming_from_update(Update.de_json({"update_id": 2}, bot=None)) is None


async def test_the_messenger_sends_plain_text_with_no_parse_mode() -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def send_message(self, **kwargs: Any) -> Any:
            self.calls.append(("send_message", kwargs))
            return type("Sent", (), {"message_id": 42})()

        async def send_chat_action(self, **kwargs: Any) -> None:
            self.calls.append(("send_chat_action", kwargs))

    bot = FakeBot()
    messenger = tg.PTBMessenger(bot)
    assert await messenger.send_text(10, "*oi* [link](http://x)") == 42
    await messenger.typing(10)
    assert bot.calls == [
        ("send_message", {"chat_id": 10, "text": "*oi* [link](http://x)"}),  # sem parse_mode
        ("send_chat_action", {"chat_id": 10, "action": "typing"}),
    ]


def test_the_ptb_application_can_be_built_without_network() -> None:
    from telegram.ext import Application

    app = Application.builder().token("123456:ABC-fake-token").concurrent_updates(True).build()
    assert app.bot.token == "123456:ABC-fake-token"


# --- saúde do contêiner --------------------------------------------------------------------


def test_heartbeat_marks_the_bot_alive_then_stale(tmp_path: Path) -> None:
    path = tmp_path / "health"
    assert tg.is_healthy(path) is False  # nunca escreveu
    tg.write_heartbeat(path)
    assert tg.is_healthy(path) is True
    path.write_text(str(time.time() - 600), encoding="utf-8")  # o laço travou há 10 minutos
    assert tg.is_healthy(path) is False
    path.write_text("lixo", encoding="utf-8")
    assert tg.is_healthy(path) is False


# --- a linha de comando e a configuração ---------------------------------------------------


def test_command_refuses_to_start_without_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.config import settings

    monkeypatch.setattr(settings, "telegram_bot_token", None)
    result = CliRunner().invoke(app, ["telegram"])
    assert result.exit_code == 1 and "JUNIPER_TELEGRAM_BOT_TOKEN" in result.output


def test_command_passes_the_token_and_the_allowlist_and_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.config import settings

    seen: list[tuple[str, list[int]]] = []
    monkeypatch.setattr(tg, "run_bot", lambda token, allowed: seen.append((token, allowed)))
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123456:ABC-fake"))
    monkeypatch.setattr(settings.telegram, "allowed_user_ids", [10, 20])
    result = CliRunner().invoke(app, ["telegram"])
    assert result.exit_code == 0 and seen == [("123456:ABC-fake", [10, 20])]
    assert "ABC-fake" not in result.output and "2 usuário(s)" in result.output


def test_command_warns_loudly_when_nobody_is_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.config import settings

    monkeypatch.setattr(tg, "run_bot", lambda token, allowed: None)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123456:ABC-fake"))
    monkeypatch.setattr(settings.telegram, "allowed_user_ids", [])
    result = CliRunner().invoke(app, ["telegram"])
    assert "Ninguém está autorizado" in result.output


@pytest.mark.parametrize(("alive", "code"), [(True, 0), (False, 1)])
def test_health_flag_exit_codes(monkeypatch: pytest.MonkeyPatch, alive: bool, code: int) -> None:
    from typer.testing import CliRunner

    from brain.clients.cli import app

    monkeypatch.setattr(tg, "is_healthy", lambda: alive)
    assert CliRunner().invoke(app, ["telegram", "--health"]).exit_code == code


def test_settings_read_the_token_as_a_secret_and_the_allowlist_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brain.config import Settings

    monkeypatch.setenv("JUNIPER_TELEGRAM_BOT_TOKEN", "123456:ABC-fake")
    monkeypatch.setenv("JUNIPER_TELEGRAM__ALLOWED_USER_IDS", "[10, 20]")
    loaded = Settings(_env_file=None)  # type: ignore[call-arg]
    assert loaded.telegram.allowed_user_ids == [10, 20]
    assert loaded.telegram_bot_token is not None
    assert loaded.telegram_bot_token.get_secret_value() == "123456:ABC-fake"
    assert "ABC-fake" not in repr(loaded)  # nunca aparece num repr/log
    assert Settings(_env_file=None).telegram.allowed_user_ids == [10, 20]  # type: ignore[call-arg]


def test_the_default_allowlist_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.config import Settings

    monkeypatch.delenv("JUNIPER_TELEGRAM__ALLOWED_USER_IDS", raising=False)
    assert Settings(_env_file=None).telegram.allowed_user_ids == []  # type: ignore[call-arg]
