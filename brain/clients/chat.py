"""Chat pelo servidor (ADR-012, seções 5, 6 e 11; ADR-010; T-076).

O `Agent` roda aqui, no servidor: o cliente só manda texto e recebe os tokens. O estado da
conversa (histórico, confirmação pendente) vive numa `Conversation`, que o cliente não vê.

Uma conversa fica ligada a **uma conexão** por vez. Um turno pertence à *conversa*, não à
conexão: se o cliente cai no meio, o turno termina aqui, a conversa é salva e a extração de
memória conclui o que já foi entregue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass, field
from typing import Any, Final

from brain.clients import protocol as p
from brain.clients.server import Connection
from brain.config import settings
from brain.core.agent import LIMIT_MESSAGE, NOTHING_PENDING_HINT, Agent
from brain.core.dispatcher import Dispatcher
from brain.core.resume import NO_PREVIOUS_NOTE, resume_note
from brain.core.session import Session
from brain.eventlog import event_log
from brain.llm.base import LLMProvider
from brain.llm.errors import LLMError
from brain.llm.friendly import friendly_error
from brain.memory.background import BackgroundExtractor
from brain.memory.errors import MemoryUnavailableError
from brain.memory.longterm import LongTermMemory
from brain.memory.session import SessionStore, SessionStoreError
from brain.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

TURN_TIMEOUT_SECONDS: Final = 120.0
"""Tempo total de um turno (ADR-012, seção 11). Passou disso, o turno acaba com erro."""
SHUTDOWN_GRACE_SECONDS: Final = 10.0
DEFAULT_LIST_LIMIT: Final = 50

_DATE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
UNEXPECTED_MESSAGE = "Algo deu errado aqui dentro. Tenta de novo daqui a pouco?"
TIMEOUT_MESSAGE = "A resposta demorou demais e parei por aqui. Tenta de novo?"
NO_CONVERSATION_MESSAGE = (
    "Esta conexão não tem uma conversa (outra conexão assumiu). Use session.new ou session.resume."
)
BUSY_MESSAGE = "Ainda estou respondendo à mensagem anterior. Espera eu terminar ou cancela."


@dataclass
class TurnRun:
    """Um turno em andamento. `owner` é a conexão que o começou: só ela recebe o stream."""

    turn_id: str
    reply_to: str
    owner: Connection
    task: asyncio.Task[None] | None = None
    cancelled: bool = False


@dataclass
class Conversation:
    agent: Agent
    background: BackgroundExtractor | None = None
    owner: Connection | None = None
    turn: TurnRun | None = None
    finalizing: bool = field(default=False, repr=False)

    @property
    def id(self) -> str:
        return self.agent.session.id

    @property
    def busy(self) -> bool:
        return self.turn is not None and (self.turn.task is None or not self.turn.task.done())


def visible_history(session: Session) -> list[p.HistoryItem]:
    """O que o usuário vê: suas mensagens e as respostas, sem datas, dicas nem tools."""
    items: list[p.HistoryItem] = []
    for message in session.history:
        if message.role not in ("user", "assistant") or not message.content:
            continue
        text = message.content
        if message.role == "user":
            text = _DATE_PREFIX.sub("", text, count=1).replace(f" {NOTHING_PENDING_HINT}", "")
        items.append(p.HistoryItem(role=message.role, text=text))
    return items


def count_turns(session: Session) -> int:
    return sum(1 for m in session.history if m.role == "user")


class ChatHandler:
    """`ConnectionHandler` do chat: conversas, turnos em stream e sessões."""

    def __init__(
        self,
        store: SessionStore,
        provider: LLMProvider,
        memory: LongTermMemory | None = None,
        *,
        background_extraction: bool = False,
        tool_registry: ToolRegistry | None = None,
        turn_timeout: float = TURN_TIMEOUT_SECONDS,
    ) -> None:
        self._store = store
        self._provider = provider
        self._memory = memory
        self._background = background_extraction and memory is not None
        self._registry = tool_registry
        self._turn_timeout = turn_timeout
        self._live: dict[str, Conversation] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # --- ConnectionHandler ---

    async def open(self, conn: Connection, hello: p.Hello) -> p.WelcomeSession:
        conversation: Conversation | None = None
        resume = hello.resume
        if resume is not None:
            try:
                if resume.session_id:
                    conversation = await self._lookup(resume.session_id)
                elif resume.latest:
                    conversation, _ = await self._latest()
            except (SessionStoreError, MemoryUnavailableError, OSError) as exc:
                # Não dá para retomar, mas dá para conversar: começa uma conversa nova.
                event_log.log("error", where="chat.resume", type=type(exc).__name__)
        resumed = conversation is not None
        conversation = conversation or self._new_conversation()
        await self._attach(conn, conversation)
        return p.WelcomeSession(
            id=conversation.id, resumed=resumed, turns=count_turns(conversation.agent.session)
        )

    async def handle(self, conn: Connection, message: p.ClientMessage) -> None:
        if isinstance(message, p.ChatSend):
            await self._chat_send(conn, message)
        elif isinstance(message, p.ChatCancel):
            self._chat_cancel(conn, message)
        elif isinstance(message, p.SessionNew):
            await self._session_new(conn, message)
        elif isinstance(message, p.SessionResume):
            await self._session_resume(conn, message)
        elif isinstance(message, p.SessionList):
            await self._session_list(conn, message)
        else:
            reply_to = getattr(message, "id", None)
            await conn.send(
                p.Error.from_code("bad_request", "Essa mensagem ainda não é suportada.", reply_to)
            )

    async def close(self, conn: Connection) -> None:
        await self._release(conn)

    async def shutdown(self) -> None:
        """Servidor saindo: dá um tempo para os turnos acabarem e conclui a extração."""
        running = [c.turn.task for c in self._live.values() if c.turn and c.turn.task]
        pending = [t for t in running if not t.done()]
        if pending:
            _, still = await asyncio.wait(pending, timeout=SHUTDOWN_GRACE_SECONDS)
            for task in still:
                task.cancel()
            await asyncio.gather(*still, return_exceptions=True)
        for conversation in list(self._live.values()):
            conversation.owner = None
            self._retire(conversation)
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # --- conversas ---

    def _new_conversation(self, session: Session | None = None) -> Conversation:
        dispatcher = Dispatcher(conversational=True, tool_registry=self._registry)
        agent = Agent(
            self._provider,
            dispatcher,
            session=session,
            tool_registry=self._registry,
            store=self._store,
            memory=self._memory,
        )
        background: BackgroundExtractor | None = None
        if self._background:
            background = BackgroundExtractor(self._memory, lambda: agent.session.id)
        return Conversation(agent, background)

    async def _lookup(self, session_id: str) -> Conversation | None:
        """A conversa com esse id: a que está em memória, ou a salva. `None` se não existe."""
        live = self._live.get(session_id)
        if live is not None:
            return live
        session = await asyncio.to_thread(self._store.load, session_id)
        return None if session is None else self._new_conversation(session)

    async def _latest(self) -> tuple[Conversation | None, str]:
        session = await asyncio.to_thread(self._store.latest)
        if session is None:
            return None, NO_PREVIOUS_NOTE
        live = self._live.get(session.id)
        return live or self._new_conversation(session), resume_note(session)

    async def _attach(self, conn: Connection, conversation: Conversation) -> None:
        """Liga a conversa à conexão, tomando-a de outra se for o caso (`session.detached`)."""
        previous = conversation.owner
        if previous is not None and previous is not conn:
            previous.state = None
            await previous.send(
                p.SessionDetached(session_id=conversation.id, reason="taken_by_other_connection")
            )
        conversation.owner = conn
        conn.state = conversation
        conn.session_id = conversation.id
        self._live[conversation.id] = conversation

    async def _release(self, conn: Connection) -> None:
        """A conexão larga a conversa (caiu, ou foi para outra)."""
        conversation: Conversation | None = conn.state
        conn.state = None
        if conversation is None or conversation.owner is not conn:
            return
        conversation.owner = None
        if not conversation.busy:
            self._retire(conversation)

    def _retire(self, conversation: Conversation) -> None:
        """Ninguém usa mais a conversa: tira da memória e conclui a extração de memória."""
        if conversation.owner is not None or conversation.finalizing:
            return
        conversation.finalizing = True
        if self._live.get(conversation.id) is conversation:
            del self._live[conversation.id]
        self._start(self._finalize(conversation))

    async def _finalize(self, conversation: Conversation) -> None:
        try:
            if conversation.background is not None:
                summary = await conversation.background.finish()
                event_log.log(
                    "memory_extraction",
                    scope="conversation",
                    session_id=conversation.id,
                    runs=summary.runs,
                    added=summary.added,
                    superseded=summary.superseded,
                    error=summary.error,
                )
            elif self._memory is not None:
                turns = conversation.agent.drain_turns()
                if turns:
                    await self._memory.extract(turns, conversation.id)
        except Exception as exc:  # a conversa já acabou: memória com problema não propaga
            event_log.log("error", where="chat.finalize", type=type(exc).__name__, message=str(exc))

    def _start(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- chat ---

    async def _chat_send(self, conn: Connection, message: p.ChatSend) -> None:
        conversation: Conversation | None = conn.state
        if conversation is None:
            await conn.send(p.Error.from_code("bad_request", NO_CONVERSATION_MESSAGE, message.id))
            return
        if conversation.busy:
            await conn.send(p.Error.from_code("turn_in_progress", BUSY_MESSAGE, message.id))
            return
        run = TurnRun(turn_id=f"t-{secrets.token_hex(4)}", reply_to=message.id, owner=conn)
        conversation.turn = run  # a partir daqui a conversa está ocupada
        await conn.send(
            p.ChatStarted(reply_to=message.id, turn_id=run.turn_id, session_id=conversation.id)
        )
        run.task = self._start(self._run_turn(conversation, run, message.text))

    def _chat_cancel(self, conn: Connection, message: p.ChatCancel) -> None:
        conversation: Conversation | None = conn.state
        run = conversation.turn if conversation is not None else None
        if run is None or run.turn_id != message.turn_id or run.owner is not conn:
            return  # o turno já acabou (ou não é seu): cancelar de novo é inofensivo
        if run.task is not None and not run.task.done():
            run.cancelled = True
            run.task.cancel()

    async def _deliver(
        self, conversation: Conversation, run: TurnRun, msg: p.ServerMessage
    ) -> None:
        """Manda ao cliente que começou o turno, se ele ainda for o dono da conversa."""
        if conversation.owner is run.owner:
            await run.owner.send(msg)

    async def _run_turn(self, conversation: Conversation, run: TurnRun, text: str) -> None:
        finish: p.FinishReason = "ok"

        async def on_token(piece: str) -> None:
            await self._deliver(conversation, run, p.ChatToken(turn_id=run.turn_id, text=piece))

        try:
            try:
                async with asyncio.timeout(self._turn_timeout):
                    reply = await conversation.agent.run(text, on_token=on_token)
                if reply == LIMIT_MESSAGE:
                    finish = "max_iterations"
            except asyncio.CancelledError:
                if not run.cancelled:
                    raise
                finish = "cancelled"
            except TimeoutError:
                finish = "error"
                await self._fail(conversation, run, "llm_timeout", TIMEOUT_MESSAGE)
            except (LLMError, MemoryUnavailableError) as exc:
                finish = "error"
                message = friendly_error(exc) if isinstance(exc, LLMError) else str(exc)
                await self._fail(conversation, run, p.error_code_for(exc), message)
            except Exception as exc:
                finish = "error"
                logger.exception("Turno falhou")
                event_log.log("error", where="chat.turn", type=type(exc).__name__)
                await self._fail(conversation, run, "internal", UNEXPECTED_MESSAGE)
            await self._deliver(
                conversation,
                run,
                p.ChatEnd(turn_id=run.turn_id, finish=finish, timing=self._timing(conversation)),
            )
        finally:
            self._turn_done(conversation, run)

    def _timing(self, conversation: Conversation) -> dict[str, object] | None:
        last = conversation.agent.last_timing
        if not settings.agent.show_timing or last is None:
            return None
        total, stages = last
        return {"total_ms": total, "stages_ms": stages}

    async def _fail(
        self, conversation: Conversation, run: TurnRun, code: p.ErrorCode, message: str
    ) -> None:
        await self._deliver(conversation, run, p.Error.from_code(code, message, run.reply_to))

    def _turn_done(self, conversation: Conversation, run: TurnRun) -> None:
        if conversation.turn is run:
            conversation.turn = None
        if conversation.background is not None:  # entrega ao extrator o que já foi respondido
            for turn in conversation.agent.drain_turns():
                conversation.background.submit(turn)
        if conversation.owner is None:  # o cliente caiu durante o turno
            self._retire(conversation)

    # --- sessões ---

    async def _state(self, conn: Connection, reply_to: str, note: str) -> None:
        conversation: Conversation = conn.state
        session = conversation.agent.session
        await conn.send(
            p.SessionState(
                reply_to=reply_to,
                session_id=conversation.id,
                turns=count_turns(session),
                history=visible_history(session),
                note=note,
            )
        )

    async def _switch(self, conn: Connection, conversation: Conversation) -> None:
        if conn.state is conversation:
            return
        await self._release(conn)
        await self._attach(conn, conversation)

    async def _session_new(self, conn: Connection, message: p.SessionNew) -> None:
        current: Conversation | None = conn.state
        if current is not None and current.busy:
            await conn.send(p.Error.from_code("turn_in_progress", BUSY_MESSAGE, message.id))
            return
        await self._switch(conn, self._new_conversation())
        await self._state(conn, message.id, "Conversa nova.")

    async def _session_resume(self, conn: Connection, message: p.SessionResume) -> None:
        current: Conversation | None = conn.state
        if current is not None and current.busy:
            await conn.send(p.Error.from_code("turn_in_progress", BUSY_MESSAGE, message.id))
            return
        try:
            if message.session_id:
                found = await self._lookup(message.session_id)
                if found is None:
                    await conn.send(
                        p.Error.from_code(
                            "session_not_found", "Não achei essa conversa.", message.id
                        )
                    )
                    return
                conversation = found
                note = resume_note(found.agent.session)
            elif message.latest:
                latest, note = await self._latest()
                conversation = latest or self._new_conversation()
            else:
                await conn.send(
                    p.Error.from_code(
                        "bad_request", "Informe 'session_id' ou 'latest'.", message.id
                    )
                )
                return
        except SessionStoreError:
            await conn.send(
                p.Error.from_code("session_not_found", "Não achei essa conversa.", message.id)
            )
            return
        except (MemoryUnavailableError, OSError) as exc:
            await conn.send(p.Error.from_exception(exc, str(exc), message.id))
            return
        await self._switch(conn, conversation)
        await self._state(conn, message.id, note)

    async def _session_list(self, conn: Connection, message: p.SessionList) -> None:
        try:
            infos = await asyncio.to_thread(self._store.list_sessions)
        except (MemoryUnavailableError, OSError) as exc:
            await conn.send(p.Error.from_exception(exc, str(exc), message.id))
            return
        limit = message.limit or DEFAULT_LIST_LIMIT
        await conn.send(
            p.SessionListed(
                reply_to=message.id,
                sessions=[
                    p.SessionSummary(
                        id=i.id, updated_at=i.updated_at, turns=i.turns, preview=i.preview
                    )
                    for i in infos[:limit]
                ],
            )
        )


@contextlib.asynccontextmanager
async def open_chat_handler() -> AsyncIterator[ChatHandler]:
    """Monta o que o servidor precisa (LLM, sessões, memória) e solta tudo ao sair.

    Levanta `MemoryUnavailableError` (com mensagem para o usuário) se o MongoDB não responde:
    é o healthcheck de partida.
    """
    from brain.llm.router import LLMRouter
    from brain.memory.longterm import long_term_enabled, open_long_term_memory
    from brain.memory.stores import close_session_store, open_session_store
    from brain.tools import memory as memory_tools
    from brain.tools.registry import registry

    registry.discover()
    store = open_session_store()
    router = LLMRouter()
    # A extração ganha o próprio router: ela não pode competir com a conversa pelo failover.
    memory_llm = LLMRouter()
    background = long_term_enabled() and settings.memory.extraction.mode == "turn"
    try:
        memory = open_long_term_memory(memory_llm)
    except MemoryUnavailableError:
        close_session_store(store)
        await router.aclose()
        await memory_llm.aclose()
        raise
    memory_tools.bind(memory)
    handler = ChatHandler(store, router, memory, background_extraction=background)
    try:
        yield handler
    finally:
        await handler.shutdown()
        memory_tools.bind(None)
        if memory is not None:
            memory.close()
        await memory_llm.aclose()
        await router.aclose()
        close_session_store(store)
