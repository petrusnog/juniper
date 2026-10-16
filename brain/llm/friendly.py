"""LLM failures in words the user can act on (PT-BR). Shared by every client."""

from __future__ import annotations

import re

from brain.llm.errors import LLMError

# Gendered article + display name, so the messages read right ("a Groq", "o Ollama").
PROVIDER_PT = {"deepinfra": ("a", "DeepInfra"), "groq": ("a", "Groq"), "ollama": ("o", "Ollama")}
KEY_VARS = {"deepinfra": "JUNIPER_DEEPINFRA_API_KEY", "groq": "JUNIPER_GROQ_API_KEY"}


def friendly_error(exc: LLMError) -> str:
    """Translate an LLM failure into an actionable PT-BR message (plain text)."""
    detail = str(exc)
    hints: list[str] = []
    for name in re.findall(r"(\w+): LLMAuthError", detail):
        if name in KEY_VARS:
            hints.append(
                f"{PROVIDER_PT[name][1]} sem chave válida: defina {KEY_VARS[name]} no .env."
            )
    for name in re.findall(r"(\w+): LLMRateLimitError", detail):
        art, shown = PROVIDER_PT.get(name, ("o", name))
        hints.append(f"Limite d{art} {shown} atingido: espere um minuto.")
    if re.search(r"ollama: (LLMUnavailableError|LLMTimeoutError)", detail):
        hints.append("Ollama não respondeu: ele está rodando? (`ollama serve`)")
    text = "Não consegui falar com nenhum LLM agora."
    if hints:
        text += "\n" + "\n".join(f"· {h}" for h in hints)
    return f"{text}\nDetalhe técnico: {detail}"
