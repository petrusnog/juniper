"""Throwaway POC: talk to Juniper and let her use the Google Calendar tools.

Minimal stand-in for the agent loop (T-030) and the CLI (T-040): LLMRouter + the tool
registry + a [y/N] prompt for WRITE/DESTRUCTIVE tools (ADR-006).

Token economy (Groq's free tier is ~8k tokens/min, so every call counts):
  * the system prompt is static, so the provider can cache it (the clock goes in the user message);
  * `precheck` catches conflicts before the [y/N], saving an LLM round trip;
  * old tool exchanges are dropped from the history, and only the last turns are kept;
  * every call prints its token usage.

Usage: python scripts/try_calendar.py        (type /sair or Ctrl+D to quit)
       python scripts/try_calendar.py --yes  (auto-confirms every [y/N]: scripted demos only)
Try:   "marca um dentista amanhã às 16h"  ·  "o que tenho marcado amanhã?"  ·  "desmarca o teste"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import readline  # noqa: F401  (enables UTF-8-aware line editing in input())
from pathlib import Path

from brain.config import settings
from brain.core import clock
from brain.llm.errors import LLMError
from brain.llm.router import LLMRouter
from brain.llm.types import Message, TokenUsage
from brain.tools.base import Tier, ToolResult
from brain.tools.registry import registry

PERSONA = Path(__file__).resolve().parent.parent / "prompts" / "juniper.md"
MAX_TURNS = 8  # user turns kept in the history

RULES = """
# Agenda

Toda mensagem do usuário começa com [data e hora de agora]: use isso para "amanhã", "sexta" etc.
Para marcar, chame create_event direto: ele checa os conflitos. Em conflito, diga o NOME e o
horário e pergunte se desmarca (só eventos com id, da agenda Juniper, podem ser apagados).
Chame delete_event só depois de o usuário concordar. Nunca invente ids.
"""

AUTO_YES = False


def system_prompt() -> str:
    """Static on purpose: an identical prefix lets the provider reuse its prompt cache."""
    persona = PERSONA.read_text(encoding="utf-8") if PERSONA.exists() else "Você é a Juniper."
    return f"{persona}\n{RULES}"


def compact(history: list[Message]) -> None:
    """Trim the history in place: cap the turns and drop old tool exchanges.

    The previous turn keeps its tool messages (so "apaga esse evento" still has the id);
    anything older keeps only what the user and the assistant said.
    """
    users = [i for i, m in enumerate(history) if m.role == "user"]
    if len(users) > MAX_TURNS:
        del history[: users[-MAX_TURNS]]
        users = [i for i, m in enumerate(history) if m.role == "user"]
    if len(users) >= 2:
        keep_from = users[-2]
        older = [m for m in history[:keep_from] if m.role != "tool" and not m.tool_calls]
        history[:keep_from] = older


def confirm(name: str, tier: Tier, args: dict[str, object]) -> bool:
    warning = "IRREVERSÍVEL" if tier is Tier.DESTRUCTIVE else "altera sua agenda"
    print(f"\n  ⚠ {name} ({warning})\n    {json.dumps(args, ensure_ascii=False)}")
    if AUTO_YES:
        print("  Confirma? [y/N] y (--yes)")
        return True
    try:
        return input("  Confirma? [y/N] ").strip().lower() in {"y", "s", "sim"}
    except EOFError:
        return False


async def execute(name: str, args: dict[str, object]) -> ToolResult:
    tool_cls = registry.get(name)
    if tool_cls is None:
        return ToolResult(success=False, error=f"ferramenta desconhecida: {name}")
    if tool_cls.tier is Tier.FORBIDDEN:
        return ToolResult(success=False, error="ação proibida")
    tool = tool_cls()
    try:
        # Fail early: never ask the user to approve something bound to fail.
        early = await tool.precheck(**args)
        if early is not None:
            return early
        if tool_cls.tier is not Tier.READ and not confirm(name, tool_cls.tier, args):
            return ToolResult(success=False, error="o usuário negou a confirmação")
        return await tool.run(**args)
    except Exception as exc:  # a tool bug must not kill the REPL; type only, never the message
        kind = type(exc).__name__
        return ToolResult(success=False, error=f"falha interna da ferramenta ({kind})")


def show_usage(usage: TokenUsage | None, total: list[int]) -> None:
    if usage is None or usage.total_tokens is None:
        return
    cached = f" (cache {usage.cached_tokens})" if usage.cached_tokens else ""
    print(
        f"    · tokens: prompt {usage.prompt_tokens}{cached} + resposta {usage.completion_tokens}"
        f" = {usage.total_tokens}"
    )
    total[0] += usage.total_tokens


async def answer(router: LLMRouter, history: list[Message]) -> None:
    schemas = registry.schemas()
    total = [0]
    for _ in range(settings.agent.max_iterations):
        resp = await router.chat([Message("system", system_prompt()), *history], tools=schemas)
        show_usage(resp.usage, total)
        history.append(resp.message)
        if not resp.message.tool_calls:
            print(f"juniper [{router.last_provider}]> {resp.message.content}")
            print(f"    · total do pedido: {total[0]} tokens\n")
            return
        for call in resp.message.tool_calls:
            print(f"  → {call.name}({json.dumps(call.arguments, ensure_ascii=False)})")
            result = await execute(call.name, call.arguments)
            text = result.output if result.success else f"ERRO: {result.error}"
            print(f"    {'✓' if result.success else '✗'} {text}".replace("\n", "\n      "))
            history.append(Message("tool", text, tool_call_id=call.id, name=call.name))
    print("juniper> Dei voltas demais e parei. Reformula o pedido?\n")


async def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
    registry.discover()
    router = LLMRouter()
    fixed = len(system_prompt()) + len(json.dumps(registry.schemas(), ensure_ascii=False))
    print(f"Agora: {clock.describe()}")
    print(f"Tools: {', '.join(c.name for c in registry.all())}")
    print(f"Prompt fixo por chamada: ≈ {fixed // 4} tokens (estimativa)")
    print("Conectado. Digite sua mensagem (/sair para encerrar).\n")

    history: list[Message] = []
    while True:
        try:
            text = input("você> ").strip()
        except EOFError:
            break
        # Undecodable bytes arrive as lone surrogates and would crash JSON encoding.
        text = text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
        if text in {"/sair", "/exit"}:
            break
        if not text:
            continue
        history.append(Message("user", f"[{clock.describe()}] {text}"))
        compact(history)
        try:
            await answer(router, history)
        except LLMError as exc:
            print(f"[{type(exc).__name__}] {exc}\n")
            history.pop()

    await router.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="POC: Juniper + Google Calendar tools")
    parser.add_argument("--yes", action="store_true", help="auto-confirm every [y/N] prompt")
    AUTO_YES = parser.parse_args().yes
    asyncio.run(main())
