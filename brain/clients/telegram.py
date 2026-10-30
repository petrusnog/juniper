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
import contextvars
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
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
RATE_WINDOW_SECONDS: Final = 60.0
UNAUTHORIZED_LOG_INTERVAL_SECONDS: Final = 600.0
# O Telegram só deixa apagar mensagens com menos de 48 h: com N = 48 a do usuário (um pouco mais
# velha que a resposta) já teria passado do prazo.
MAX_DELETE_DELAY_HOURS: Final = 47.5

HELP_TEXT: Final = (
    "Oi! Eu sou a Juniper. Escreva o que precisa, como faria no terminal.\n\n"
    "Comandos:\n"
    "/novo: começa uma conversa nova\n"
    "/login <client_id> <client_secret>: entra (quando o servidor pede login)\n"
    "/sair: encerra a sessão\n"
    "/vazou [nota]: marca que uma resposta minha vazou instruções ou texto interno\n"
    "/ajuda: mostra esta mensagem\n\n"
    "Por aqui só entendo texto. Quando uma ação muda algo (como criar um evento), eu pergunto "
    'antes e você responde "sim".'
)
BUSY_TEXT: Final = "Ainda estou respondendo à mensagem anterior. Espera eu terminar?"
ONLY_TEXT: Final = "Por enquanto só entendo texto."
NO_FORWARDS: Final = "Não aceito mensagens encaminhadas. Escreva você mesmo o que precisa."
UNKNOWN_COMMAND: Final = "Não conheço esse comando. Use /ajuda."
NEW_CONVERSATION: Final = "Conversa nova. Pode falar."
TAKEN_OVER: Final = (
    "Outra janela (o terminal ou o desktop) assumiu a nossa conversa, então abri uma conversa nova."
)
EMPTY_REPLY: Final = "Não tenho o que responder agora. Tenta de outro jeito?"
UNFINISHED: Final = "Não consegui terminar essa resposta. Tenta de novo?"
AUTH_FAILED: Final = (
    "Não consegui falar com o servidor (autenticação). Avise quem cuida da Juniper."
)
LOST_TEXT: Final = "A conexão com o servidor caiu no meio da resposta. Vou tentar recuperá-la."
UNAVAILABLE_TEXT: Final = f"{UNAVAILABLE_MESSAGE} Tenta de novo daqui a pouco."
RATE_LIMITED: Final = "Muitas mensagens em pouco tempo. Espera um minuto e tenta de novo."
LOGIN_REQUIRED: Final = (
    "Antes de conversar, entre com /login <client_id> <client_secret>. "
    "Apago a sua mensagem em seguida."
)
LOGIN_USAGE: Final = "Use /login <client_id> <client_secret>."
LOGIN_REFUSED: Final = "Não consegui entrar: credenciais recusadas."
LOGIN_ENDED: Final = "Sua sessão foi encerrada. Entre de novo com /login."
LOGGED_OUT: Final = "Sessão encerrada."
OPEN_COMMANDS: Final = frozenset({"start", "ajuda", "help", "login"})
LOGIN_IDLE_HOURS: Final = 12.0
TOO_LONG: Final = "Essa mensagem é grande demais. Manda em partes menores?"


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

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        """Apaga uma mensagem do chat (só as com menos de 48 h)."""


class BrainClient(Protocol):
    """A parte do `RemoteClient` de que o bot depende."""

    detached: bool  # outra conexão (o CLI com `--resume`, o desktop) assumiu a conversa

    async def start(self, resume: p.Resume | None = None, wait: float = ...) -> p.Welcome: ...
    async def chat(self, text: str, on_token: Callable[[str], None]) -> ChatResult: ...
    async def request(self, frame: dict[str, Any], timeout: float = ...) -> p.ServerMessage: ...
    async def history(self) -> list[p.HistoryItem]: ...
    async def wait_ready(self, timeout: float = ...) -> None: ...
    async def close(self) -> None: ...


# `(user_id)` ou `(user_id, bearer)`: o `bearer` (client_id.client_secret) só vem com o login por
# Telegram (ADR-027).
ClientFactory = Callable[..., BrainClient]


def _friendly(message: str) -> str:
    """O texto amigável do servidor, sem o "Detalhe técnico" (que fica só no log dele)."""
    return message.split("\nDetalhe técnico:")[0].strip()


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


class RateLimiter:
    """Janela deslizante por usuário. Na primeira recusa de uma janela avisa; depois, silêncio."""

    def __init__(
        self,
        limit: int,
        window: float = RATE_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}
        self._warned: set[int] = set()

    def check(self, user_id: int) -> str:
        """`"allow"`, `"warn"` (recusa e avisa) ou `"drop"` (recusa em silêncio)."""
        now = self._clock()
        hits = self._hits.setdefault(user_id, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) < self._limit:
            hits.append(now)
            self._warned.discard(user_id)
            return "allow"
        if user_id in self._warned:
            return "drop"
        self._warned.add(user_id)
        return "warn"


@dataclass
class _User:
    client: BrainClient | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    known_turns: int = 0
    # Só em memória (nunca em disco ou log): a credencial do login por Telegram (ADR-027 §6).
    bearer: str | None = None
    last_active: float = 0.0


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
        rate_limit_per_minute: int = 20,
        delete_after_hours: float = 0.0,
        require_login: bool = False,
        login_idle_hours: float = LOGIN_IDLE_HOURS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._require_login = require_login
        self._login_idle = login_idle_hours * 3600
        self._allowed = frozenset(allowed_user_ids)
        self._messenger = messenger
        self._factory = client_factory
        self._typing_interval = typing_interval
        self._limiter = RateLimiter(rate_limit_per_minute, clock=clock)
        self._delete_after = min(max(delete_after_hours, 0.0), MAX_DELETE_DELAY_HOURS) * 3600
        self._clock = clock
        self._sleep = sleep
        self._users: dict[int, _User] = {}
        self._unauthorized_logged: dict[int, float] = {}
        self._deletions: set[asyncio.Task[None]] = set()
        # Os ids das mensagens do turno que está sendo tratado (cada tarefa tem o seu).
        self._sent_ids: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
            "telegram_sent_ids", default=None
        )

    async def close(self) -> None:
        for task in list(self._deletions):  # as mensagens ficam: o agendamento é só em memória
            task.cancel()
        await asyncio.gather(*self._deletions, return_exceptions=True)
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
            self._log_unauthorized(msg.user_id)
            return
        verdict = self._limiter.check(msg.user_id)
        if verdict != "allow":  # excedeu o limite: nada chega ao cérebro
            event_log.log("telegram", action="rate_limited", user_id=msg.user_id, notified=verdict)
            if verdict == "warn":
                await self._say(msg.chat_id, RATE_LIMITED)
            return
        event_log.log(
            "telegram",
            action="message_in",
            user_id=msg.user_id,
            chars=len(msg.text or ""),
            forwarded=msg.forwarded,
        )
        sent: list[int] = []
        token = self._sent_ids.set(sent)
        try:
            if msg.forwarded:
                await self._say(msg.chat_id, NO_FORWARDS)
            elif msg.text is None:
                await self._say(msg.chat_id, ONLY_TEXT)
            elif len(msg.text) > TELEGRAM_MESSAGE_LIMIT:
                await self._say(msg.chat_id, TOO_LONG)
            elif self._require_login and not await self._may_talk(msg.user_id, msg.text):
                await self._say(msg.chat_id, LOGIN_REQUIRED)
            elif msg.text.startswith("/"):
                await self._command(msg, msg.text)
            else:
                await self._turn(msg, msg.text)
        finally:
            self._sent_ids.reset(token)
        self._schedule_deletion(msg.chat_id, [msg.message_id, *sent])

    def _log_unauthorized(self, user_id: int) -> None:
        """O ID (nunca o texto) vai ao log: é assim que o dono descobre o próprio ID.

        No máximo uma vez a cada 10 minutos por ID: quem insiste não enche o log.
        """
        now = self._clock()
        last = self._unauthorized_logged.get(user_id)
        if last is not None and now - last < UNAUTHORIZED_LOG_INTERVAL_SECONDS:
            return
        self._unauthorized_logged = {  # poda: só os ainda dentro do intervalo
            uid: at
            for uid, at in self._unauthorized_logged.items()
            if now - at < UNAUTHORIZED_LOG_INTERVAL_SECONDS
        }
        self._unauthorized_logged[user_id] = now
        logger.warning("Telegram: mensagem de usuário não autorizado (id=%s)", user_id)
        event_log.log("telegram", action="unauthorized", user_id=user_id)

    # --- apagar depois de N horas (opcional) ---

    def _schedule_deletion(self, chat_id: int, message_ids: list[int]) -> None:
        if self._delete_after <= 0 or not message_ids:
            return
        task = asyncio.get_running_loop().create_task(self._delete_later(chat_id, message_ids))
        self._deletions.add(task)
        task.add_done_callback(self._deletions.discard)

    async def _delete_later(self, chat_id: int, message_ids: list[int]) -> None:
        await self._sleep(self._delete_after)
        for message_id in message_ids:
            try:
                await self._messenger.delete_message(chat_id, message_id)
            except Exception as exc:  # já apagada, velha demais, Telegram fora: não importa
                event_log.log("telegram", action="error", where="delete", type=type(exc).__name__)

    # --- comandos ---

    async def _command(self, msg: Incoming, text: str) -> None:
        name = text.split()[0][1:].split("@")[0].lower()  # "/novo@MeuBot" vale como "/novo"
        if name in ("start", "ajuda", "help"):
            await self._say(msg.chat_id, HELP_TEXT)
        elif name == "login" and self._require_login:
            await self._login(msg, text)
        elif name == "sair" and self._require_login:
            await self._logout(self._user(msg.user_id))
            await self._say(msg.chat_id, LOGGED_OUT)
        elif name == "novo":
            await self._new_conversation(msg)
        elif name == "vazou":  # the server stores the report and answers (T-167)
            await self._turn(msg, text, counts=False)
        else:
            await self._say(msg.chat_id, UNKNOWN_COMMAND)

    # --- login por Telegram (ADR-027 §6) ---

    async def _may_talk(self, user_id: int, text: str) -> bool:
        """Com login exigido: só quem tem sessão viva conversa (os comandos abertos passam)."""
        if text.startswith("/") and text.split()[0][1:].split("@")[0].lower() in OPEN_COMMANDS:
            return True
        user = self._users.get(user_id)
        if user is None or user.bearer is None:
            return False
        if self._clock() - user.last_active > self._login_idle:  # inatividade: pede de novo
            await self._logout(user)
            event_log.log("telegram", action="login_expired", user_id=user_id)
            return False
        user.last_active = self._clock()
        return True

    def _new_client(self, user_id: int, bearer: str | None) -> BrainClient:
        return self._factory(user_id) if bearer is None else self._factory(user_id, bearer)

    async def _logout(self, user: _User) -> None:
        client, user.client, user.bearer, user.known_turns = user.client, None, None, 0
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()

    async def _login(self, msg: Incoming, text: str) -> None:
        """`/login id segredo`: apaga a mensagem na hora e confere abrindo a conexão com o servidor.

        O segredo só passa por aqui e pela memória do cliente; nunca vai ao log."""
        with contextlib.suppress(Exception):
            await self._messenger.delete_message(msg.chat_id, msg.message_id)
        parts = text.split()
        if len(parts) != 3:
            await self._say(msg.chat_id, LOGIN_USAGE)
            return
        user = self._user(msg.user_id)
        if user.lock.locked():
            await self._say(msg.chat_id, BUSY_TEXT)
            return
        async with user.lock:
            await self._logout(user)
            client = self._new_client(msg.user_id, f"{parts[1]}.{parts[2]}")
            try:
                welcome = await client.start(p.Resume(latest=True), CONNECT_WAIT_SECONDS)
            except ServerUnavailableError:
                await self._say(msg.chat_id, UNAVAILABLE_TEXT)
                return
            except FatalConnectionError:
                event_log.log("telegram", action="login_refused", user_id=msg.user_id)
                await self._say(msg.chat_id, LOGIN_REFUSED)
                return
            user.client, user.bearer = client, f"{parts[1]}.{parts[2]}"
            user.known_turns, user.last_active = welcome.session.turns, self._clock()
            event_log.log("telegram", action="login", user_id=msg.user_id)
            who = welcome.user.name if welcome.user else ""
            await self._say(msg.chat_id, f"Entrei{', ' + who if who else ''}. Pode falar.")

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
                await self._say(msg.chat_id, _friendly(exc.error.message))
                return
            except RemoteError as exc:
                await self._say(msg.chat_id, _friendly(str(exc)) or UNAVAILABLE_TEXT)
                return
            user.known_turns = 0
            await self._say(msg.chat_id, NEW_CONVERSATION)

    # --- um turno ---

    async def _turn(self, msg: Incoming, text: str, *, counts: bool = True) -> None:
        """Um turno. ``counts=False`` para um comando do servidor (``/vazou``): ele responde como
        um turno, mas não entra no histórico, então não conta para a recuperação de queda."""
        user = self._user(msg.user_id)
        if user.lock.locked():  # um turno por vez, sem fila (ADR-016)
            await self._say(msg.chat_id, BUSY_TEXT)
            return
        async with user.lock:
            client = await self._connected(user, msg)
            if client is None:
                return
            if not await self._reattach(client, user, msg):
                return
            pieces: list[str] = []
            typing = asyncio.get_running_loop().create_task(self._typing_loop(msg.chat_id))
            started = time.monotonic()
            finish = "ok"
            try:
                result = await client.chat(text, pieces.append)
                user.known_turns += counts
                finish = result.finish
                reply = self._reply_for(result, "".join(pieces))
            except ConnectionLostError:
                user.known_turns += counts
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

    async def _reattach(self, client: BrainClient, user: _User, msg: Incoming) -> bool:
        """Se outra conexão assumiu a conversa, abre uma nova (sem roubá-la de volta)."""
        if not getattr(client, "detached", False):
            return True
        try:
            await client.request({"type": "session.new"})
        except RemoteError as exc:
            await self._say(msg.chat_id, _friendly(str(exc)) or UNAVAILABLE_TEXT)
            return False
        client.detached = False
        user.known_turns = 0
        event_log.log("telegram", action="reattached", user_id=msg.user_id)
        await self._say(msg.chat_id, TAKEN_OVER)
        return True

    @staticmethod
    def _reply_for(result: ChatResult, text: str) -> str:
        if result.error is not None:
            # O servidor manda o texto amigável em PT-BR; o "Detalhe técnico" fica só no log dele.
            return _friendly(result.error.message)
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
        client = self._new_client(msg.user_id, user.bearer)
        try:
            welcome = await client.start(p.Resume(latest=True), CONNECT_WAIT_SECONDS)
        except ServerUnavailableError:
            await self._say(msg.chat_id, UNAVAILABLE_TEXT)
            return None
        except FatalConnectionError:
            event_log.log("telegram", action="error", where="auth")
            if self._require_login:  # credencial revogada ou usuário desativado
                await self._logout(user)
                await self._say(msg.chat_id, LOGIN_ENDED)
            else:
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
        sent = self._sent_ids.get()
        for part in split_message(text) or [EMPTY_REPLY]:
            try:
                message_id = await self._messenger.send_text(chat_id, part)
            except Exception as exc:  # o Telegram pode recusar/cair: só o tipo vai ao log
                event_log.log("telegram", action="error", where="send", type=type(exc).__name__)
                return
            if sent is not None and message_id is not None:
                sent.append(message_id)


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
        from telegram import LinkPreviewOptions

        # Sem pré-visualização: o Telegram buscaria sozinho qualquer URL da resposta (um convite de
        # calendário malicioso poderia pedir ao LLM uma URL com dados na query), sem clique.
        sent = await self._bot.send_message(
            chat_id=chat_id, text=text, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        return int(sent.message_id)

    async def typing(self, chat_id: int) -> None:
        await self._bot.send_chat_action(chat_id=chat_id, action="typing")

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        await self._bot.delete_message(chat_id=chat_id, message_id=message_id)


def _setup_logging() -> None:
    """Faz as linhas do bot aparecerem em `docker compose logs` (só o NOSSO logger).

    Não mexe no logger raiz nem no `httpx`: em INFO ele imprime a URL de cada requisição, e ela
    leva o token do bot.
    """
    if any(getattr(h, "_juniper_telegram", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    handler._juniper_telegram = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _bot_username(bot: Any) -> str:
    try:
        return str(bot.username)
    except Exception:  # ainda não inicializado (testes)
        return "?"


class BotStartError(Exception):
    """O bot não conseguiu subir. `str(exc)` é seguro de imprimir: nunca leva o token."""


def _start_failure(exc: Exception) -> str:
    if type(exc).__name__ == "InvalidToken":
        return (
            "O Telegram recusou o token do bot. Confira JUNIPER_TELEGRAM_BOT_TOKEN no .env "
            "(se você revogou o token no @BotFather, use o novo)."
        )
    return f"Não consegui falar com o Telegram ao subir ({type(exc).__name__}). Tento de novo."


def make_client_factory() -> ClientFactory:
    """Um `RemoteClient` por usuário, no endereço e com o token que o config diz."""
    from brain.clients.remote import RemoteClient
    from brain.clients.remote_cli import read_token, server_url

    def factory(user_id: int, bearer: str | None = None) -> BrainClient:
        return RemoteClient(
            server_url(),
            bearer or read_token(),
            device_id=f"telegram-{user_id}",
            device_name="Telegram",
            kind="telegram",
        )

    return factory


def run_bot(
    token: str,
    allowed_user_ids: list[int],
    *,
    rate_limit_per_minute: int = 20,
    delete_after_hours: float = 0.0,
    require_login: bool = False,
) -> None:
    """Sobe o bot (long polling) e só volta quando ele é parado (SIGINT/SIGTERM)."""
    from telegram import Update
    from telegram.error import TelegramError
    from telegram.ext import Application, ContextTypes, MessageHandler, filters

    _setup_logging()
    factory = make_client_factory()

    async def post_init(app: Application) -> None:  # type: ignore[type-arg]
        bridge = TelegramBridge(
            allowed_user_ids,
            PTBMessenger(app.bot),
            factory,
            rate_limit_per_minute=rate_limit_per_minute,
            delete_after_hours=delete_after_hours,
            require_login=require_login,
        )
        app.bot_data["bridge"] = bridge
        app.bot_data["heartbeat"] = asyncio.get_running_loop().create_task(_heartbeat())
        logger.info(
            "Telegram: conectado como @%s; %d usuário(s) autorizado(s); escutando mensagens.",
            _bot_username(app.bot),
            len(allowed_user_ids),
        )
        event_log.log(
            "telegram",
            action="start",
            allowed=len(allowed_user_ids),
            rate_limit=rate_limit_per_minute,
            delete_after_hours=delete_after_hours,
            require_login=require_login,
        )

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
        error_type = type(context.error).__name__
        event_log.log("telegram", action="error", where="ptb", type=error_type)
        logger.warning("Telegram: erro da biblioteca (%s)", error_type)  # só o tipo, nunca o texto

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
    try:
        # `drop_pending_updates`: depois de uma pausa, não responde a pedidos velhos de horas.
        app.run_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=True)
    except TelegramError as exc:
        # A mensagem da biblioteca inclui o token ("The token `...` was rejected"): só o tipo sai.
        event_log.log("telegram", action="error", where="start", type=type(exc).__name__)
        raise BotStartError(_start_failure(exc)) from None


async def _heartbeat() -> None:
    while True:
        write_heartbeat()
        await asyncio.sleep(HEALTH_INTERVAL_SECONDS)
