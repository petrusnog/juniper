"""Throwaway REPL to try GroqProvider by hand (replaced by `juniper chat` in T-040).

Usage: python scripts/try_groq.py   (type /sair or Ctrl+D to quit)
Requires JUNIPER_GROQ_API_KEY in .env (https://console.groq.com/keys).
"""

from __future__ import annotations

import asyncio
import readline  # noqa: F401  (enables UTF-8-aware line editing in input())
from pathlib import Path

from brain.config import settings
from brain.llm.errors import LLMAuthError, LLMError
from brain.llm.groq_provider import GroqProvider
from brain.llm.types import Message

PERSONA = Path(__file__).resolve().parent.parent / "prompts" / "juniper.md"


async def main() -> None:
    provider = GroqProvider()
    cfg = settings.llm.groq
    print(f"Groq · modelo: {cfg.model}")

    if not await provider.health_check():
        print("Groq não respondeu. Verifique JUNIPER_GROQ_API_KEY no .env e a conexão.")
        await provider.aclose()
        return

    system = PERSONA.read_text(encoding="utf-8") if PERSONA.exists() else "Você é a Juniper."
    history = [Message("system", system)]
    print("Conectado. Digite sua mensagem (/sair para encerrar).\n")

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
        history.append(Message("user", text))

        parts: list[str] = []
        usage = None
        print("juniper> ", end="", flush=True)
        try:
            async for chunk in provider.stream(history):
                if chunk.delta:
                    parts.append(chunk.delta)
                    print(chunk.delta, end="", flush=True)
                if chunk.usage:
                    usage = chunk.usage
        except LLMAuthError as exc:
            print(f"\n[{type(exc).__name__}] {exc}")
            history.pop()
            break
        except LLMError as exc:
            print(f"\n[{type(exc).__name__}] {exc}")
            history.pop()
            continue
        print()
        history.append(Message("assistant", "".join(parts)))
        if usage:
            print(f"         ({usage.total_tokens} tokens)\n")

    await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())
