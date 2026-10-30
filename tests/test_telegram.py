"""O bot do Telegram (T-095): o núcleo contra um cérebro de verdade, sem rede externa."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import ANY

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
        self.deleted: list[tuple[int, int]] = []
        self.fail_delete = False

    async def send_text(self, chat_id: int, text: str) -> int | None:
        if self.fail:
            raise RuntimeError("telegram fora do ar")
        self.sent.append((chat_id, text))
        return len(self.sent)

    async def typing(self, chat_id: int) -> None:
        self.typing_calls += 1

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        if self.fail_delete:
            raise RuntimeError("mensagem velha demais")
        self.deleted.append((chat_id, message_id))

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
    def make(user_id: int, bearer: str | None = None) -> tg.BrainClient:
        return RemoteClient(
            f"ws://127.0.0.1:{s.server.port}/v1/ws",
            bearer or TOKEN,
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
        (
            "send_message",
            {"chat_id": 10, "text": "*oi* [link](http://x)", "link_preview_options": ANY},
        ),  # sem parse_mode
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

    seen: list[tuple[str, list[int], dict[str, Any]]] = []
    monkeypatch.setattr(
        tg, "run_bot", lambda token, allowed, **kw: seen.append((token, allowed, kw))
    )
    monkeypatch.setattr(settings.telegram, "rate_limit_per_minute", 7)
    monkeypatch.setattr(settings.telegram, "delete_after_hours", 12.0)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123456:ABC-fake"))
    monkeypatch.setattr(settings.telegram, "allowed_user_ids", [10, 20])
    result = CliRunner().invoke(app, ["telegram"])
    assert result.exit_code == 0
    assert seen == [
        (
            "123456:ABC-fake",
            [10, 20],
            {"rate_limit_per_minute": 7, "delete_after_hours": 12.0, "require_login": False},
        )
    ]
    assert "ABC-fake" not in result.output and "2 usuário(s)" in result.output


def test_command_warns_loudly_when_nobody_is_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.config import settings

    monkeypatch.setattr(tg, "run_bot", lambda token, allowed, **kw: None)
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


# --- T-097: limite de mensagens ------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_messages_above_the_limit_are_refused_and_never_reach_the_brain(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    provider = FakeProvider([say("um"), say("dois"), say("três"), say("não deveria")])
    messenger = FakeMessenger()
    clock = Clock()
    async with Scenario(provider, tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, rate_limit_per_minute=3, clock=clock)
        for i in range(6):
            await bridge.handle(msg(f"mensagem {i}"))
        # as 3 primeiras foram ao cérebro; a 4ª recebeu o aviso; a 5ª e a 6ª, silêncio
        assert messenger.texts == ["um", "dois", "três", tg.RATE_LIMITED]
        assert len(provider.seen) == 3  # o LLM só foi chamado 3 vezes

    assert messenger.texts[-1] == tg.RATE_LIMITED


async def test_the_limit_recovers_when_the_window_passes(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    provider = FakeProvider([say("a1"), say("a2")])
    messenger = FakeMessenger()
    clock = Clock()
    async with Scenario(provider, tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, rate_limit_per_minute=1, clock=clock)
        await bridge.handle(msg("a"))
        await bridge.handle(msg("a de novo"))  # passou do limite
        clock.now += tg.RATE_WINDOW_SECONDS + 1
        await bridge.handle(msg("a outra vez"))  # a janela passou: volta a valer
    assert messenger.texts == ["a1", tg.RATE_LIMITED, "a2"]


def test_rate_limiter_warns_once_per_window_then_drops() -> None:
    clock = Clock()
    limiter = tg.RateLimiter(2, window=60, clock=clock)
    verdicts = [limiter.check(1) for _ in range(5)]
    assert verdicts == ["allow", "allow", "warn", "drop", "drop"]
    clock.now += 61
    assert limiter.check(1) == "allow"
    assert limiter.check(2) == "allow"  # outro usuário não é afetado


async def test_a_burst_of_simultaneous_messages_runs_one_turn_and_tells_the_rest_to_wait(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    provider = GatedProvider("ok")
    messenger = FakeMessenger()
    async with Scenario(provider, tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        first = asyncio.create_task(bridge.handle(msg("a")))
        await asyncio.wait_for(provider.started.wait(), 3)
        await asyncio.gather(*(bridge.handle(msg(f"rajada {i}")) for i in range(9)))
        assert messenger.texts == [tg.BUSY_TEXT] * 9  # nenhuma foi ao cérebro
        provider.release.set()
        await asyncio.wait_for(first, 3)
    assert messenger.texts[-1] == "parcial ok"


async def test_a_stranger_that_keeps_writing_never_gets_an_answer_and_logs_once(
    tmp_path: Path,
    bridges: list[tg.TelegramBridge],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = RecordingEventLog()
    monkeypatch.setattr(tg, "event_log", recorder)
    caplog.set_level(logging.WARNING, logger="brain")
    messenger = FakeMessenger()
    clock = Clock()
    async with Scenario(FakeProvider([]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, clock=clock)
        for i in range(50):
            await bridge.handle(msg(f"abre aí {i}", user_id=666, chat_id=666))
        assert s.server.connections == []
        warnings = [r for r in caplog.records if "id=666" in r.getMessage()]
        assert len(warnings) == 1  # um aviso, não cinquenta
        clock.now += tg.UNAUTHORIZED_LOG_INTERVAL_SECONDS + 1
        await bridge.handle(msg("ainda aqui", user_id=666, chat_id=666))
        assert len([r for r in caplog.records if "id=666" in r.getMessage()]) == 2
    assert messenger.sent == []
    assert [k["action"] for _, k in recorder.calls].count("unauthorized") == 2


async def test_an_oversized_message_is_refused(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("não deveria")]), tmp_path) as s:
        await make_bridge(bridges, s, messenger).handle(msg("x" * 5000))
        assert s.server.connections == []
    assert messenger.texts == [tg.TOO_LONG]


# --- T-097: apagar depois de N horas -------------------------------------------------------


class Sleeper:
    """Um `sleep` que não espera: guarda os prazos pedidos."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def drain(bridge: tg.TelegramBridge) -> None:
    await asyncio.gather(*bridge._deletions)


async def test_with_delete_after_hours_the_users_message_and_the_reply_are_deleted(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    sleeper = Sleeper()
    async with Scenario(FakeProvider([say("Resposta.")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, delete_after_hours=2.0, sleep=sleeper)
        await bridge.handle(msg("oi", message_id=77))
        await drain(bridge)
    assert sleeper.delays == [2 * 3600]
    # a mensagem do usuário (77) e a resposta do bot (a 1ª enviada, id 1 no mensageiro de teste)
    assert messenger.deleted == [(CHAT, 77), (CHAT, 1)]


async def test_notices_and_command_replies_are_deleted_too(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, delete_after_hours=1.0, sleep=Sleeper())
        await bridge.handle(msg("/ajuda", message_id=5))
        await bridge.handle(msg(None, message_id=6))  # áudio
        await drain(bridge)
    assert (CHAT, 5) in messenger.deleted and (CHAT, 6) in messenger.deleted
    assert len(messenger.deleted) == 4  # 2 do usuário + 2 do bot


async def test_with_delete_after_hours_off_nothing_is_ever_scheduled_or_deleted(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    sleeper = Sleeper()
    async with Scenario(FakeProvider([say("Resposta.")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, delete_after_hours=0.0, sleep=sleeper)
        await bridge.handle(msg("oi"))
        assert bridge._deletions == set()
    assert sleeper.delays == [] and messenger.deleted == []


async def test_a_stranger_message_is_never_deleted_by_the_bot(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, delete_after_hours=1.0, sleep=Sleeper())
        await bridge.handle(msg("oi", user_id=9, chat_id=9))
        assert bridge._deletions == set()
    assert messenger.deleted == []


async def test_the_delay_is_capped_below_the_48_hour_api_limit(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    sleeper = Sleeper()
    async with Scenario(FakeProvider([say("ok")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, FakeMessenger(), delete_after_hours=48.0, sleep=sleeper)
        await bridge.handle(msg("oi"))
        await drain(bridge)
    assert sleeper.delays == [tg.MAX_DELETE_DELAY_HOURS * 3600]
    assert tg.MAX_DELETE_DELAY_HOURS < 48


async def test_a_failed_deletion_never_breaks_the_bot(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    messenger.fail_delete = True
    async with Scenario(FakeProvider([say("a"), say("b")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, delete_after_hours=1.0, sleep=Sleeper())
        await bridge.handle(msg("oi"))
        await drain(bridge)  # as exclusões falharam em silêncio
        await bridge.handle(msg("e agora?"))
    assert messenger.texts == ["a", "b"]


async def test_closing_the_bridge_cancels_pending_deletions_and_keeps_the_messages(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()

    async def never(_: float) -> None:
        await asyncio.sleep(3600)

    async with Scenario(FakeProvider([say("ok")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger, delete_after_hours=1.0, sleep=never)
        await bridge.handle(msg("oi"))
        assert len(bridge._deletions) == 1
        await bridge.close()
        assert bridge._deletions == set()
    assert messenger.deleted == []


async def test_the_messenger_deletes_through_the_bot_api() -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def delete_message(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    bot = FakeBot()
    await tg.PTBMessenger(bot).delete_message(10, 42)
    assert bot.calls == [{"chat_id": 10, "message_id": 42}]


# --- T-097: valores inválidos na configuração ----------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DELETE_AFTER_HOURS", "-1"),
        ("DELETE_AFTER_HOURS", "49"),
        ("RATE_LIMIT_PER_MINUTE", "0"),
        ("RATE_LIMIT_PER_MINUTE", "-5"),
        ("RATE_LIMIT_PER_MINUTE", "100000"),
    ],
)
def test_invalid_limits_are_rejected_at_load_time(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    from pydantic import ValidationError

    from brain.config import Settings

    monkeypatch.setenv(f"JUNIPER_TELEGRAM__{name}", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_valid_limits_load_and_the_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.config import Settings

    for name in ("DELETE_AFTER_HOURS", "RATE_LIMIT_PER_MINUTE"):
        monkeypatch.delenv(f"JUNIPER_TELEGRAM__{name}", raising=False)
    default = Settings(_env_file=None).telegram  # type: ignore[call-arg]
    assert (default.rate_limit_per_minute, default.delete_after_hours) == (
        20,
        0.0,
    )  # nada é apagado
    monkeypatch.setenv("JUNIPER_TELEGRAM__DELETE_AFTER_HOURS", "48")
    monkeypatch.setenv("JUNIPER_TELEGRAM__RATE_LIMIT_PER_MINUTE", "5")
    loaded = Settings(_env_file=None).telegram  # type: ignore[call-arg]
    assert (loaded.rate_limit_per_minute, loaded.delete_after_hours) == (5, 48.0)


# --- outra janela assume a conversa ---------------------------------------------------------


async def test_when_another_window_takes_the_conversation_the_bot_opens_a_new_one(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    """O CLI com `--resume` assume a conversa do bot: o bot não trava nem rouba de volta."""
    from tests.test_chat_server import connect_client

    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("Primeira."), say("Segunda.")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("oi"))
        (conn,) = s.server.connections
        session = conn.session_id
        taker = await connect_client(s.server, "cli", session_id=session)  # `juniper --resume`
        await wait_until(lambda: conn.state is None)  # o bot perdeu a conversa
        async with asyncio.timeout(5):  # antes da correção, isto travava para sempre
            await bridge.handle(msg("e agora?"))
        assert conn.session_id != session  # o bot abriu uma conversa nova
        assert taker.welcome.session.id == session  # e a do terminal continua com o terminal
    assert messenger.texts == ["Primeira.", tg.TAKEN_OVER, "Segunda."]


async def test_a_takeover_in_the_middle_of_the_bots_turn_is_reported_and_does_not_hang(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    from tests.test_chat_server import connect_client

    provider = GatedProvider("pronto")
    messenger = FakeMessenger()
    async with Scenario(provider, tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        running = asyncio.create_task(bridge.handle(msg("conta uma história")))
        await asyncio.wait_for(provider.started.wait(), 3)
        (conn,) = s.server.connections
        await connect_client(s.server, "cli", session_id=conn.session_id)  # `juniper --resume`
        async with asyncio.timeout(5):
            await running
        provider.release.set()
    assert messenger.texts == [
        "Outra janela assumiu esta conversa; o resto da resposta vai aparecer lá."
    ]


# --- achados da revisão de segurança -------------------------------------------------------


async def test_after_slash_novo_the_clients_reconnection_keeps_the_new_conversation(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    messenger = FakeMessenger()
    async with Scenario(FakeProvider([say("Velha.")]), tmp_path) as s:
        bridge = make_bridge(bridges, s, messenger)
        await bridge.handle(msg("oi"))
        (conn,) = s.server.connections
        old = conn.session_id
        await bridge.handle(msg("/novo"))
        client = bridge._users[OWNER].client
        assert client is not None and isinstance(client, RemoteClient)
        assert client.session_id == conn.session_id != old  # o cliente sabe qual é a conversa
        assert client._resume is not None and client._resume.session_id == conn.session_id


async def test_replies_are_sent_without_link_previews() -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def send_message(self, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return type("Sent", (), {"message_id": 1})()

    bot = FakeBot()
    await tg.PTBMessenger(bot).send_text(10, "veja https://exemplo.com/?d=segredo")
    preview = bot.kwargs["link_preview_options"]
    assert preview.is_disabled is True  # o Telegram não busca a URL sozinho
    assert "parse_mode" not in bot.kwargs


def test_a_rejected_token_never_reaches_the_traceback_or_the_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr
    from telegram.error import InvalidToken
    from telegram.ext import Application
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.config import settings

    secret = "123456:ABC-token-que-o-telegram-recusou"

    def rejected(self: Any, *args: Any, **kwargs: Any) -> None:
        raise InvalidToken(f"The token `{secret}` was rejected by the server.")

    monkeypatch.setattr(Application, "run_polling", rejected)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr(secret))
    monkeypatch.setattr(settings.telegram, "allowed_user_ids", [10])
    result = CliRunner().invoke(app, ["telegram"])
    assert result.exit_code == 1
    assert "recusou o token do bot" in result.output
    assert secret not in result.output and secret not in repr(result.exception)
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_a_telegram_outage_at_startup_also_hides_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram.error import NetworkError
    from telegram.ext import Application

    def down(self: Any, *args: Any, **kwargs: Any) -> None:
        raise NetworkError("falhou em https://api.telegram.org/bot123456:ABC/getMe")

    monkeypatch.setattr(Application, "run_polling", down)
    monkeypatch.setattr(tg, "make_client_factory", lambda: lambda _: None)  # type: ignore[arg-type,return-value]
    with pytest.raises(tg.BotStartError) as info:
        tg.run_bot("123456:ABC", [10])
    assert "123456:ABC" not in str(info.value) and "NetworkError" in str(info.value)


def test_more_than_one_authorized_id_gets_a_loud_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr
    from typer.testing import CliRunner

    from brain.clients.cli import app
    from brain.config import settings

    monkeypatch.setattr(tg, "run_bot", lambda token, allowed, **kw: None)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123456:ABC-fake"))
    monkeypatch.setattr(settings.telegram, "allowed_user_ids", [10, 20])
    result = CliRunner().invoke(app, ["telegram"])
    flat = " ".join(result.output.split())  # o terminal quebra linhas no meio da frase
    assert "Mais de um ID autorizado" in flat and "Use um único ID" in flat


async def test_command_errors_also_hide_the_technical_detail(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    class RefusingClient(DroppingClient):
        async def request(self, frame: dict[str, Any], timeout: float = 60.0) -> p.ServerMessage:
            raise ServerError(
                p.Error.from_code("internal", "Algo deu errado.\nDetalhe técnico: Traceback (x)")
            )

    messenger = FakeMessenger()
    bridge = tg.TelegramBridge([OWNER], messenger, lambda _: RefusingClient([]))
    await bridge.handle(msg("/novo"))
    assert messenger.texts == ["Algo deu errado."]


async def test_the_unauthorized_log_is_pruned_so_it_cannot_grow_without_bound(
    tmp_path: Path, bridges: list[tg.TelegramBridge]
) -> None:
    clock = Clock()
    async with Scenario(FakeProvider([]), tmp_path) as s:
        bridge = make_bridge(bridges, s, FakeMessenger(), clock=clock)
        for uid in range(100, 150):
            await bridge.handle(msg("oi", user_id=uid, chat_id=uid))
        assert len(bridge._unauthorized_logged) == 50
        clock.now += tg.UNAUTHORIZED_LOG_INTERVAL_SECONDS + 1
        await bridge.handle(msg("oi", user_id=999, chat_id=999))
        assert set(bridge._unauthorized_logged) == {999}  # os velhos saíram


# --- a ligação real com a biblioteca (o mesmo caminho do `run_bot`) -------------------------


def _fake_polling(updates: list[dict[str, Any]]) -> Callable[..., None]:
    """Troca o `run_polling` (rede) por: initialize, post_init e entregar `updates`."""
    from unittest.mock import AsyncMock, patch

    import telegram

    def run_polling(self: Any, *args: Any, **kwargs: Any) -> None:
        async def go() -> None:
            with patch.object(telegram.Bot, "initialize", new=AsyncMock()):
                await self.initialize()
                await self.post_init(self)  # o `run_polling` de verdade chama isto
                for raw in updates:
                    await self.process_update(telegram.Update.de_json(raw, bot=self.bot))
                await asyncio.sleep(0.3)  # deixa os handlers concorrentes terminarem
                await self.post_shutdown(self)

        asyncio.run(go())

    return run_polling


def _private_message(user_id: int, text: str, update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 5,
            "date": 0,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "P"},
            "text": text,
        },
    }


def test_the_real_wiring_logs_the_id_of_a_stranger_the_way_the_guide_promises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """É assim que o dono descobre o ID: a linha tem de sair para o log do contêiner."""
    from telegram.ext import Application

    monkeypatch.setattr(Application, "run_polling", _fake_polling([_private_message(4242, "oi")]))
    monkeypatch.setattr(tg, "make_client_factory", lambda: lambda _: None)  # type: ignore[arg-type,return-value]
    caplog.set_level(logging.INFO, logger="brain")
    tg.run_bot("123456:ABC-fake", [])  # lista vazia: ninguém autorizado
    assert "id=4242" in caplog.text
    assert "escutando mensagens" in caplog.text  # a linha de "estou conectado" também aparece


def test_the_real_wiring_answers_the_owner_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Update do Telegram -> handlers da biblioteca -> bridge -> cérebro -> send_message."""
    from unittest.mock import AsyncMock, patch

    import telegram
    from telegram.ext import Application

    sent: list[dict[str, Any]] = []

    async def send_message(self: Any, **kwargs: Any) -> Any:
        sent.append(kwargs)
        return type("Sent", (), {"message_id": 9})()

    async def main() -> None:
        async with Scenario(FakeProvider([say("Oi, Petrus!")]), tmp_path) as s:
            monkeypatch.setattr(tg, "make_client_factory", lambda: factory_for(s))
            monkeypatch.setattr(
                Application, "run_polling", _fake_polling([_private_message(OWNER, "oi")])
            )
            with (
                patch.object(telegram.Bot, "send_message", new=send_message),
                patch.object(telegram.Bot, "send_chat_action", new=AsyncMock()),
            ):
                await asyncio.to_thread(tg.run_bot, "123456:ABC-fake", [OWNER])

    asyncio.run(main())
    assert [call["text"] for call in sent] == ["Oi, Petrus!"]
    assert sent[0]["chat_id"] == OWNER


def test_library_errors_are_visible_in_the_container_log_without_the_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from telegram.ext import Application

    async def boom(self: Any, msg: tg.Incoming) -> None:
        raise RuntimeError("detalhe com o token 123456:ABC-fake")

    monkeypatch.setattr(tg.TelegramBridge, "handle", boom)
    monkeypatch.setattr(Application, "run_polling", _fake_polling([_private_message(OWNER, "oi")]))
    monkeypatch.setattr(tg, "make_client_factory", lambda: lambda _: None)  # type: ignore[arg-type,return-value]
    caplog.set_level(logging.INFO, logger="brain")
    tg.run_bot("123456:ABC-fake", [OWNER])
    assert "erro da biblioteca (RuntimeError)" in caplog.text
    assert "123456:ABC-fake" not in caplog.text  # só o tipo


def test_logging_setup_is_idempotent_and_never_touches_httpx() -> None:
    tg._setup_logging()
    tg._setup_logging()
    ours = [h for h in tg.logger.handlers if getattr(h, "_juniper_telegram", False)]
    assert len(ours) == 1
    assert logging.getLogger("httpx").level in (logging.NOTSET, logging.WARNING)  # intocado
