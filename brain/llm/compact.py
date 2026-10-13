"""Compact prompt for slow local providers.

A CPU-only Ollama reads ~30 prompt tokens per second, so the full persona plus every tool
description (~2,200 tokens) means over a minute of silence before the first streamed token.
For providers that set ``compact_prompt`` the router sends a short persona
(``prompts/juniper.compact.md``) and trimmed tool schemas instead. Names, parameter names,
types, ``required`` and ``enum`` are untouched, so tool calling keeps working.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from brain.llm.types import Message

COMPACT_PERSONA_PATH = Path(__file__).resolve().parents[2] / "prompts" / "juniper.compact.md"

MAX_TOOL_DESCRIPTION = 100
MAX_PARAM_DESCRIPTION = 60


def _first_sentence(text: str, limit: int) -> str:
    text = " ".join(text.split())
    cut = text.find(". ")
    sentence = text if cut == -1 else text[: cut + 1]
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip() + "…"
    return sentence


def _trim_properties(schema: Any) -> Any:
    """Shorten every ``description`` inside a JSON Schema fragment."""
    if isinstance(schema, dict):
        return {
            key: (
                _first_sentence(value, MAX_PARAM_DESCRIPTION)
                if key == "description" and isinstance(value, str)
                else _trim_properties(value)
            )
            for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [_trim_properties(item) for item in schema]
    return schema


def compact_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Same tools with short descriptions (the input is not modified)."""
    if not tools:
        return tools
    result = copy.deepcopy(tools)
    for tool in result:
        function = tool.get("function", {})
        function["description"] = _first_sentence(
            str(function.get("description", "")), MAX_TOOL_DESCRIPTION
        )
        if "parameters" in function:
            function["parameters"] = _trim_properties(function["parameters"])
    return result


def compact_messages(
    messages: list[Message], persona_path: Path = COMPACT_PERSONA_PATH
) -> list[Message]:
    """Swap the leading system message for the compact persona, if that file exists."""
    if not messages or messages[0].role != "system" or not persona_path.exists():
        return messages
    persona = persona_path.read_text(encoding="utf-8").strip()
    return [Message("system", persona), *messages[1:]]
