"""Throwaway REPL to try OllamaProvider by hand (replaced by `juniper chat` in T-040).

Usage: python scripts/try_ollama.py   (type /sair or Ctrl+D to quit)
"""

from __future__ import annotations

import asyncio
import readline  # noqa: F401  (enables UTF-8-aware line editing in input())
from pathlib import Path

from brain.config import settings
from brain.llm.errors import LLMError
from brain.llm.ollama_provider import OllamaProvider
from brain.llm.types import Message

PERSONA = Path(__file__).resolve().parent.parent / "prompts" / "juniper.md"


async def main() -> None:
    provider = OllamaProvider()
    cfg = settings.llm.ollama
    print(f"Ollama: {cfg.url} · modelo: {cfg.model}")

    if not await provider.health_check():
        print("Ollama não respondeu. Está rodando? (ollama serve)")
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
        try:
            resp = await provider.chat(history)
        except LLMError as exc:
            print(f"[{type(exc).__name__}] {exc}")
            history.pop()
            continue
        history.append(resp.message)
        print(f"juniper> {resp.message.content}")
        if resp.usage:
            print(f"         ({resp.usage.total_tokens} tokens)\n")

    await provider.aclose()


if __name__ == "__main__":
    asyncio.run(main())
