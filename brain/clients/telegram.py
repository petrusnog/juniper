"""O bot do Telegram: um cliente do protocolo (ADR-016, ADR-012; T-095).

O bot só leva o texto de quem está na lista de permissão ao cérebro e devolve a resposta. Não há
lógica de agente aqui, nem acesso ao MongoDB, e nenhuma tool de memória, perfil ou administração é
exposta: o canal é só conversa. O texto das mensagens **nunca** vai para log nem para evento.

`TelegramBridge` não conhece a biblioteca do Telegram (fala com um `Messenger`), então dá para
testá-lo sem rede. `run_bot` é o adaptador fino com `python-telegram-bot` (long polling).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

from brain.clients import protocol as p
from brain.clients.remote import (
    UNAVAILABLE_MESSAGE,
    ChatResult,
    ConnectionLostError,
    FatalConnectionError,
    RemoteError,
    ServerError,
    ServerUnavailableError,
)
from brain.eventlog import event_log

if TYPE_CHECKING:  # só para os tipos; o import real é tardio (a biblioteca é pesada)
    from telegram import Update

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT: Final = 4096
TYPING_INTERVAL_SECONDS: Final = 4.0
CONNECT_WAIT_SECONDS: Final = 15.0
RECOVER_WAIT_SECONDS: Final = 60.0
HEALTH_FILE: Final = Path("/tmp/telegram-health")
HEALTH_INTERVAL_SECONDS: Final = 15.0
HEALTH_MAX_AGE_SECONDS: Final = 60.0

HELP_TEXT: Final = (
    "Oi! Eu sou a Juniper. Escreva o que precisa, como faria no terminal.\n\n"
    "Comandos:\n"
    "/novo: começa uma conversa nova\n"
    "/ajuda: mostra esta mensagem\n\n"
    "Por aqui só entendo texto. Quando uma ação muda algo (como criar um evento), eu pergunto "
    'antes e você responde "sim".'
)
BUSY_TEXT: Final = "Ainda estou respondendo à mensagem anterior. Espera eu terminar?"
ONLY_TEXT: Final = "Por enquanto só entendo texto."
NO_FORWARDS: Final = "Não aceito mensagens encaminhadas. Escreva você mesmo o que precisa."
UNKNOWN_COMMAND: Final = "Não conheço esse comando. Use /ajuda."
NEW_CONVERSATION: Final = "Conversa nova. Pode falar."
EMPTY_REPLY: Final = "Não tenho o que responder agora. Tenta de outro jeito?"
UNFINISHED: Final = "Não consegui terminar essa resposta. Tenta de novo?"
AUTH_FAILED: Final = (
    "Não consegui falar com o servidor (autenticação). Avise quem cuida da Juniper."
)
LOST_TEXT: Final = "A conexão com o servidor caiu no meio da resposta. Vou tentar recuperá-la."
UNAVAILABLE_TEXT: Final = f"{UNAVAILABLE_MESSAGE} Tenta de novo daqui a pouco."


# ---------------------------------------------------------------------------------------------
# O que o bridge precisa do mundo (e que os testes trocam)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Incoming:
    """Uma mensagem do Telegram, já sem nada da biblioteca."""

    user_id: int
    chat_id: int
    chat_type: str
    message_id: int
    text: str | None
    forwarded: bool = False
    is_bot: bool = False


class Messenger(Protocol):
    async def send_text(self, chat_id: int, text: str) -> int | None:
        """Manda um texto e devolve o id da mensagem enviada (se souber)."""

    async def typing(self, chat_id: int) -> None:
        """Mostra "digitando…" no chat."""


class BrainClient(Protocol):
    """A parte do `RemoteClient` de que o bot depende."""

    async def start(self, resume: p.Resume | None = None, wait: float = ...) -> p.Welcome: ...
    async def chat(self, text: str, on_token: Callable[[str], None]) -> ChatResult: ...
    async def request(self, frame: dict[str, Any], timeout: float = ...) -> p.ServerMessage: ...
    async def history(self) -> list[p.HistoryItem]: ...
    async def wait_ready(self, timeout: float = ...) -> None: ...
    async def close(self) -> None: ...


ClientFactory = Callable[[int], BrainClient]


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Divide em partes de até `limit` caracteres, de preferência em parágrafo, linha ou espaço."""
    text = text.strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = limit
        for separator in ("\n\n", "\n", " "):
            found = text.rfind(separator, 0, limit)
            if found > limit // 2:  # não corta tão cedo que sobre uma parte minúscula
                cut = found + len(separator)
                break
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


@dataclass
class _User:
    client: BrainClient | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    known_turns: int = 0


# ---------------------------------------------------------------------------------------------
# O bridge
# ---------------------------------------------------------------------------------------------


class TelegramBridge:
    def __init__(
        self,
        allowed_user_ids: list[int],
        messenger: Messenger,
        client_factory: ClientFactory,
        *,
        typing_interval: float = TYPING_INTERVAL_SECONDS,
    ) -> None:
        self._allowed = frozenset(allowed_user_ids)
        self._messenger = messenger
        self._factory = client_factory
        self._typing_interval = typing_interval
        self._users: dict[int, _User] = {}

    async def close(self) -> None:
        for user in self._users.values():
            if user.client is not None:
                with contextlib.suppress(Exception):
                    await user.client.close()

    # --- entrada ---

    async def handle(self, msg: Incoming) -> None:
        """Trata uma mensagem. Nunca levanta: um erro aqui não pode derrubar o bot."""
        try:
            await self._handle(msg)
        except Exception as exc:  # o detalhe vai ao log (só o tipo), nunca ao usuário
            event_log.log("telegram", action="error", where="handle", type=type(exc).__name__)
            logger.exception("Falha ao tratar uma mensagem do Telegram")
            with contextlib.suppress(Exception):
                await self._say(msg.chat_id, "Algo deu errado aqui dentro. Tenta de novo?")

    async def _handle(self, msg: Incoming) -> None:
        if msg.is_bot or msg.chat_type != "private":  # grupos, canais e outros bots: ignorados
            event_log.log("telegram", action="ignored", reason="not_private", chat=msg.chat_type)
            return
        if msg.user_id not in self._allowed:
            # O ID (nunca o texto) vai ao log: é assim que o dono descobre o próprio ID.
            logger.warning("Telegram: mensagem de usuário não autorizado (id=%s)", msg.user_id)
            event_log.log("telegram", action="unauthorized", user_id=msg.user_id)
            return
        event_log.log(
            "telegram",
            action="message_in",
            user_id=msg.user_id,
            chars=len(msg.text or ""),
            forwarded=msg.forwarded,
        )
        if msg.forwarded:
            await self._say(msg.chat_id, NO_FORWARDS)
        elif msg.text is None:
            await self._say(msg.chat_id, ONLY_TEXT)
        elif msg.text.startswith("/"):
            await self._command(msg, msg.text)
        else:
            await self._turn(msg, msg.text)

    # --- comandos ---

    async def _command(self, msg: Incoming, text: str) -> None:
        name = text.split()[0][1:].split("@")[0].lower()  # "/novo@MeuBot" vale como "/novo"
        if name in ("start", "ajuda", "help"):
            await self._say(msg.chat_id, HELP_TEXT)
        elif name == "novo":
            await self._new_conversation(msg)
        else:
            await self._say(msg.chat_id, UNKNOWN_COMMAND)

    async def _new_conversation(self, msg: Incoming) -> None:
        user = self._user(msg.user_id)
        if user.lock.locked():
            await self._say(msg.chat_id, BUSY_TEXT)
            return
        async with user.lock:
            client = await self._connected(user, msg)
            if client is None:
                return
            try:
                await client.request({"type": "session.new"})
            except ServerError as exc:
                await self._say(msg.chat_id, exc.error.message)
                return
            except RemoteError as exc:
                await self._say(msg.chat_id, str(exc) or UNAVAILABLE_TEXT)
                return
            user.known_turns = 0
            await self._say(msg.chat_id, NEW_CONVERSATION)

    # --- um turno ---

    async def _turn(self, msg: Incoming, text: str) -> None:
        user = self._user(msg.user_id)
        if user.lock.locked():  # um turno por vez, sem fila (ADR-016)
            await self._say(msg.chat_id, BUSY_TEXT)
            return
        async with user.lock:
            client = await self._connected(user, msg)
            if client is None:
                return
            pieces: list[str] = []
            typing = asyncio.get_running_loop().create_task(self._typing_loop(msg.chat_id))
            started = time.monotonic()
            finish = "ok"
            try:
                result = await client.chat(text, pieces.append)
                user.known_turns += 1
                finish = result.finish
                reply = self._reply_for(result, "".join(pieces))
            except ConnectionLostError:
                user.known_turns += 1
                finish = "connection_lost"
                await self._say(msg.chat_id, LOST_TEXT)
                reply = await self._recover(client, user.known_turns)
            except ServerUnavailableError:
                finish = "unavailable"
                reply = UNAVAILABLE_TEXT
            except FatalConnectionError:
                finish = "fatal"
                reply = AUTH_FAILED
            finally:
                typing.cancel()
                await asyncio.gather(typing, return_exceptions=True)
            event_log.log(
                "telegram",
                action="turn",
                user_id=msg.user_id,
                finish=finish,
                duration_ms=round((time.monotonic() - started) * 1000),
                reply_chars=len(reply),
            )
            await self._say(msg.chat_id, reply)

    @staticmethod
    def _reply_for(result: ChatResult, text: str) -> str:
        if result.error is not None:
            # O servidor manda o texto amigável em PT-BR; o "Detalhe técnico" fica só no log dele.
            return result.error.message.split("\nDetalhe técnico:")[0].strip()
        if result.finish == "error":
            return UNFINISHED
        return text.strip() or EMPTY_REPLY

    async def _recover(self, client: BrainClient, turns_before: int) -> str:
        """Queda no meio do turno: ele termina no servidor e a resposta fica no histórico."""
        try:
            await client.wait_ready(RECOVER_WAIT_SECONDS)
        except ServerUnavailableError:
            return UNAVAILABLE_TEXT
        for _ in range(60):  # o turno pode ainda estar terminando
            try:
                history = await client.history()
                break
            except ServerError as exc:
                if exc.code != "turn_in_progress":
                    return UNFINISHED
                await asyncio.sleep(1)
            except RemoteError:
                return UNFINISHED
        else:
            return "Ainda estou respondendo no servidor. Daqui a pouco pergunte de novo."
        users = sum(1 for item in history if item.role == "user")
        if history and history[-1].role == "assistant" and users >= turns_before:
            return history[-1].text.strip() or EMPTY_REPLY
        return UNFINISHED

    # --- infraestrutura ---

    def _user(self, user_id: int) -> _User:
        return self._users.setdefault(user_id, _User())

    async def _connected(self, user: _User, msg: Incoming) -> BrainClient | None:
        """O cliente do usuário, conectado (retomando a conversa mais recente)."""
        if user.client is not None:
            return user.client
        client = self._factory(msg.user_id)
        try:
            welcome = await client.start(p.Resume(latest=True), CONNECT_WAIT_SECONDS)
        except ServerUnavailableError:
            await self._say(msg.chat_id, UNAVAILABLE_TEXT)
            return None
        except FatalConnectionError:
            event_log.log("telegram", action="error", where="auth")
            await self._say(msg.chat_id, AUTH_FAILED)
            return None
        user.client = client
        user.known_turns = welcome.session.turns
        return client

    async def _typing_loop(self, chat_id: int) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self._messenger.typing(chat_id)
            await asyncio.sleep(self._typing_interval)

    async def _say(self, chat_id: int, text: str) -> None:
        for part in split_message(text) or [EMPTY_REPLY]:
            try:
                await self._messenger.send_text(chat_id, part)
            except Exception as exc:  # o Telegram pode recusar/cair: só o tipo vai ao log
                event_log.log("telegram", action="error", where="send", type=type(exc).__name__)
                return


# ---------------------------------------------------------------------------------------------
# Saúde do contêiner
# ---------------------------------------------------------------------------------------------


def write_heartbeat(path: Path = HEALTH_FILE) -> None:
    with contextlib.suppress(OSError):
        path.write_text(str(time.time()), encoding="utf-8")


def is_healthy(path: Path = HEALTH_FILE, max_age: float = HEALTH_MAX_AGE_SECONDS) -> bool:
    """O laço de eventos do bot escreveu o sinal de vida recentemente?"""
    try:
        return time.time() - float(path.read_text(encoding="utf-8")) <= max_age
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------------------------
# Adaptador com a biblioteca (python-telegram-bot)
# ---------------------------------------------------------------------------------------------


def incoming_from_update(update: Update) -> Incoming | None:
    """O `Incoming` de um update de mensagem, ou `None` se não é uma mensagem de um usuário."""
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if message is None or user is None or chat is None:
        return None
    return Incoming(
        user_id=user.id,
        chat_id=chat.id,
        chat_type=chat.type,
        message_id=message.message_id,
        text=message.text,
        forwarded=message.forward_origin is not None,
        is_bot=bool(user.is_bot),
    )


class PTBMessenger:
    """`Messenger` sobre o `Bot` do python-telegram-bot. Texto puro: sem `parse_mode`."""

    def __init__(self, bot: Any) -> None:
        self._bot = bot

    async def send_text(self, chat_id: int, text: str) -> int | None:
        sent = await self._bot.send_message(chat_id=chat_id, text=text)
        return int(sent.message_id)

    async def typing(self, chat_id: int) -> None:
        await self._bot.send_chat_action(chat_id=chat_id, action="typing")


def make_client_factory() -> ClientFactory:
    """Um `RemoteClient` por usuário, no endereço e com o token que o config diz."""
    from brain.clients.remote import RemoteClient
    from brain.clients.remote_cli import read_token, server_url

    def factory(user_id: int) -> BrainClient:
        return RemoteClient(
            server_url(),
            read_token(),
            device_id=f"telegram-{user_id}",
            device_name="Telegram",
            kind="telegram",
        )

    return factory


def run_bot(token: str, allowed_user_ids: list[int]) -> None:
    """Sobe o bot (long polling) e só volta quando ele é parado (SIGINT/SIGTERM)."""
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters

    factory = make_client_factory()

    async def post_init(app: Application) -> None:  # type: ignore[type-arg]
        bridge = TelegramBridge(allowed_user_ids, PTBMessenger(app.bot), factory)
        app.bot_data["bridge"] = bridge
        app.bot_data["heartbeat"] = asyncio.get_running_loop().create_task(_heartbeat())
        event_log.log("telegram", action="start", allowed=len(allowed_user_ids))

    async def post_shutdown(app: Application) -> None:  # type: ignore[type-arg]
        task = app.bot_data.get("heartbeat")
        if task is not None:
            task.cancel()
        bridge = app.bot_data.get("bridge")
        if bridge is not None:
            await bridge.close()
        event_log.log("telegram", action="stop")

    async def on_update(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        assert isinstance(update, Update)
        incoming = incoming_from_update(update)
        if incoming is not None:
            await context.application.bot_data["bridge"].handle(incoming)

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Só o tipo: o erro pode conter o token na URL da requisição.
        event_log.log("telegram", action="error", where="ptb", type=type(context.error).__name__)

    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)  # senão uma resposta lenta trava o "ainda estou respondendo"
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(MessageHandler(filters.ALL, on_update))
    app.add_error_handler(on_error)
    # `drop_pending_updates`: depois de uma pausa, não responder a pedidos velhos de horas atrás.
    app.run_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=True)


async def _heartbeat() -> None:
    while True:
        write_heartbeat()
        await asyncio.sleep(HEALTH_INTERVAL_SECONDS)
