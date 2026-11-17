"""MCP server of the board (streamable HTTP, loopback). Model-agnostic: Claude, Codex or any MCP
client. Each agent authenticates with its own bearer token; the token decides WHO is acting, the
Board decides what that role may do. Expected failures come back as ``Erro: ...`` text for the
agent, never as an exception that drops the connection.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from kanbai.board import Board
from kanbai.domain import Actor, Result


def _plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items() if k != "token_hash"}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _out(result: Result) -> str:
    if not result.ok:
        return f"Erro: {result.error}"
    return json.dumps(_plain(result.data), ensure_ascii=False)


def bearer(headers: Any) -> str:
    value = headers.get("authorization", "") if headers else ""
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def build_mcp(board: Board) -> MCPServer:
    mcp = MCPServer(
        "kanbai",
        instructions=(
            "Quadro kanban movido por agentes. Seu cargo (delegadora, executora, qa) vem do seu "
            "token; o quadro recusa o que o cargo não permite. Fluxo: delegadora cria KAIs; "
            "executora start_kai -> submit (com relatório); qa review_packet -> review."
        ),
    )

    def actor_of(ctx: Context) -> Actor | None:
        return board.actor_for_token(bearer(ctx.headers))

    def acting(ctx: Context, fn):  # type: ignore[no-untyped-def]
        actor = actor_of(ctx)
        if actor is None:
            return "Erro: token inválido ou agente encerrada."
        try:
            return _out(fn(actor))
        except Exception as exc:  # noqa: BLE001 - a tool must answer, not crash the server
            return f"Erro interno ({type(exc).__name__}): {exc}"

    @mcp.tool()
    def whoami(ctx: Context) -> str:
        """Quem sou eu: nome, cargo, KAI atual e modelo."""
        actor = actor_of(ctx)
        agent = board.store.get_agent(actor.agent_id) if actor and actor.agent_id else None
        return _out(Result.success(agent)) if agent else "Erro: token inválido ou agente encerrada."

    @mcp.tool()
    def list_board(ctx: Context, project_id: str | None = None) -> str:
        """KAIs agrupados por coluna (opcionalmente de um projeto)."""
        return acting(ctx, lambda a: Result.success(board.columns(project_id)))

    @mcp.tool()
    def list_tasks(ctx: Context, project_id: str | None = None) -> str:
        """Tarefas (com branch, status e link do PR)."""
        return acting(ctx, lambda a: Result.success(board.store.list_tasks(project_id)))

    @mcp.tool()
    def get_kai(ctx: Context, kai_id: int) -> str:
        """Um KAI com os comentários. A QA deve preferir review_packet (sem o relatório)."""
        def go(a: Actor) -> Result:
            kai = board.store.get_kai(kai_id)
            return Result.success(kai) if kai else Result.fail(f"KAI {kai_id} não existe.")
        return acting(ctx, go)

    @mcp.tool()
    def create_task(
        ctx: Context,
        project_id: str,
        key: str,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        source_url: str = "",
    ) -> str:
        """Cadastra uma tarefa no Backlog. Idempotente por (projeto, chave): repetir não duplica.
        É o que a agente importadora de issues chama."""
        return acting(ctx, lambda a: board.create_task(a, project_id, key, title, description, acceptance_criteria, source_url))

    @mcp.tool()
    def create_kai(ctx: Context, task_id: int, title: str, description: str, points: int) -> str:
        """Delegadora: cria um KAI em A fazer. `points` na escala 1, 2, 3, 5, 8, 13."""
        return acting(ctx, lambda a: board.create_kai(a, task_id, title, description, points))

    @mcp.tool()
    def start_kai(ctx: Context, kai_id: int) -> str:
        """Executora: A fazer -> Fazendo (IA). Respeita o limite de executoras em paralelo."""
        return acting(ctx, lambda a: board.start_kai(a, kai_id))

    @mcp.tool()
    def submit(ctx: Context, kai_id: int, report: str) -> str:
        """Executora: envia o KAI (Fazendo ou Correção) para Revisando (IA) com o relatório."""
        return acting(ctx, lambda a: board.submit(a, kai_id, report))

    @mcp.tool()
    def review_packet(ctx: Context, kai_id: int) -> str:
        """QA: o que ela pode ver (critérios de aceite e briefing), sem o relatório da executora."""
        return acting(ctx, lambda a: board.review_packet(kai_id) if a.role.value == "qa" else Result.fail("Só a QA."))

    @mcp.tool()
    def review(ctx: Context, kai_id: int, approved: bool, verdict: str) -> str:
        """QA: aprova (-> Revisão Humana) ou reprova (-> Correção) com o parecer."""
        return acting(ctx, lambda a: board.review(a, kai_id, approved, verdict))

    @mcp.tool()
    def set_human_summary(ctx: Context, kai_id: int, summary: str) -> str:
        """QA ou delegadora: o resumo simples para a revisão humana."""
        return acting(ctx, lambda a: board.set_human_summary(a, kai_id, summary))

    return mcp


class _BearerGate:
    """Rejects requests without a valid agent token before they reach the MCP layer."""

    def __init__(self, app: ASGIApp, board: Board) -> None:
        self.app, self.board = app, board

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if self.board.actor_for_token(bearer(headers)) is None:
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(board: Board) -> Starlette:
    app = build_mcp(board).streamable_http_app(stateless_http=True, json_response=True)
    app.add_middleware(_BearerGate, board=board)  # type: ignore[arg-type]
    return app
