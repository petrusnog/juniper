"""Throwaway REPL to try LLMRouter (Groq → Ollama) by hand (replaced by `juniper` in T-040).

Usage: python scripts/try_groq.py                  (type /sair or Ctrl+D to quit)
       python scripts/try_groq.py --simulate-down  (Groq gets an invalid key → watch the failover)
Requires JUNIPER_GROQ_API_KEY in .env (https://console.groq.com/keys); Ollama is the fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import readline  # noqa: F401  (enables UTF-8-aware line editing in input())
from pathlib import Path

from brain.config import settings
from brain.llm.errors import LLMError
from brain.llm.groq_provider import GroqProvider
from brain.llm.ollama_provider import OllamaProvider
from brain.llm.router import LLMRouter
from brain.llm.types import Message

PERSONA = Path(__file__).resolve().parent.parent / "prompts" / "juniper.md"


async def main(simulate_down: bool) -> None:
    # Failover warnings go through `logging`; show them so the switch is visible.
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")

    if simulate_down:
        router = LLMRouter([GroqProvider(api_key="gsk_invalid_on_purpose"), OllamaProvider()])
        print("Simulação: Groq com chave inválida → esperado cair no Ollama.")
    else:
        router = LLMRouter()
    print(f"Prioridade: {' → '.join(p.name for p in router.providers)}")
    print(f"  groq: {settings.llm.groq.model} · ollama: {settings.llm.ollama.model}")

    for provider in router.providers:
        ok = await provider.health_check()
        print(f"  {provider.name}: {'ok' if ok else 'INDISPONÍVEL'}")
    if not await router.health_check():
        print("Nenhum provider respondeu. Verifique o .env e o `ollama serve`.")
        await router.aclose()
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
        started = False
        try:
            async for chunk in router.stream(history):
                if not started:  # first chunk: the router has already picked its provider
                    started = True
                    print(f"juniper [{router.last_provider}]> ", end="", flush=True)
                if chunk.delta:
                    parts.append(chunk.delta)
                    print(chunk.delta, end="", flush=True)
                if chunk.usage:
                    usage = chunk.usage
        except LLMError as exc:
            print(f"\n[{type(exc).__name__}] {exc}")
            history.pop()
            continue
        print()
        history.append(Message("assistant", "".join(parts)))
        if usage:
            print(f"         ({usage.total_tokens} tokens)\n")

    await router.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--simulate-down", action="store_true", help="invalid Groq key")
    asyncio.run(main(parser.parse_args().simulate_down))
