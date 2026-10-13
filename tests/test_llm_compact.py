"""Tests for the compact prompt sent to slow local providers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.llm import compact
from brain.llm.errors import LLMUnavailableError
from brain.llm.ollama_provider import OllamaProvider
from brain.llm.router import LLMRouter
from brain.llm.types import Message
from brain.tools.registry import registry
from tests.test_llm_router import ScriptedProvider

LONG = (
    "Faz uma coisa muito útil. Use quando a pessoa pedir isso ou aquilo, e também em outros "
    "casos longos que só gastam tokens à toa."
)
SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "do_thing",
            "description": LONG,
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {"type": "string", "description": "Data, AAAA-MM-DD. " + LONG},
                    "mode": {"type": "string", "enum": ["a", "b"], "description": LONG},
                    "items": {"type": "array", "items": {"type": "string", "description": LONG}},
                },
                "required": ["when"],
            },
        },
    }
]


def test_ollama_asks_for_the_compact_prompt_and_groq_does_not() -> None:
    from brain.llm.groq_provider import GroqProvider

    assert OllamaProvider.compact_prompt is True
    assert GroqProvider.compact_prompt is False


def test_compact_tools_keep_structure_and_shorten_text() -> None:
    out = compact.compact_tools(SCHEMA)
    assert out is not None
    function = out[0]["function"]
    assert function["name"] == "do_thing"
    assert function["description"] == "Faz uma coisa muito útil."
    props = function["parameters"]["properties"]
    assert function["parameters"]["required"] == ["when"]
    assert props["mode"]["enum"] == ["a", "b"] and props["items"]["items"]["type"] == "string"
    assert props["when"]["description"] == "Data, AAAA-MM-DD."  # the format hint survives
    assert len(json.dumps(out)) < len(json.dumps(SCHEMA))


def test_compact_tools_do_not_modify_the_input() -> None:
    before = json.dumps(SCHEMA)
    compact.compact_tools(SCHEMA)
    assert json.dumps(SCHEMA) == before


def test_compact_tools_handle_no_tools() -> None:
    assert compact.compact_tools(None) is None
    assert compact.compact_tools([]) == []


def test_every_real_tool_survives_compaction() -> None:
    registry.discover()
    full, small = registry.schemas(), compact.compact_tools(registry.schemas())
    assert small is not None and len(small) == len(full)
    for before, after in zip(full, small, strict=True):
        b, a = before["function"], after["function"]
        assert a["name"] == b["name"]
        assert a["parameters"]["required"] == b["parameters"].get("required", [])
        assert a["parameters"]["properties"].keys() == b["parameters"]["properties"].keys()
    assert len(json.dumps(small)) < len(json.dumps(full))


def test_compact_messages_replace_only_the_system_prompt(tmp_path: Path) -> None:
    persona = tmp_path / "compact.md"
    persona.write_text("persona curta\n", encoding="utf-8")
    msgs = [
        Message("system", "persona enorme"),
        Message("user", "oi"),
        Message("assistant", "e aí"),
    ]
    out = compact.compact_messages(msgs, persona)
    assert out[0] == Message("system", "persona curta") and out[1:] == msgs[1:]
    assert msgs[0].content == "persona enorme"  # input untouched


def test_compact_messages_fall_back_when_nothing_to_swap(tmp_path: Path) -> None:
    msgs = [Message("system", "x"), Message("user", "oi")]
    assert compact.compact_messages(msgs, tmp_path / "nao-existe.md") is msgs
    no_system = [Message("user", "oi")]
    assert compact.compact_messages(no_system) is no_system


def test_the_shipped_compact_persona_is_much_shorter() -> None:
    full = (Path(compact.__file__).resolve().parents[2] / "prompts" / "juniper.md").read_text()
    short = compact.COMPACT_PERSONA_PATH.read_text()
    assert 0 < len(short) < len(full) / 3
    for must_mention in ("create_event", "git_search", "git_deploy", "[data e hora"):
        assert must_mention in short


class Recorder(ScriptedProvider):
    def __init__(self, name: str, *, compact_prompt: bool, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self.compact_prompt = compact_prompt
        self.received: list[tuple[list[Message], list[dict[str, Any]] | None]] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> Any:
        self.received.append((messages, kwargs.get("tools")))
        return await super().chat(messages, **kwargs)

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        self.received.append((messages, kwargs.get("tools")))
        return super().stream(messages, **kwargs)


async def test_router_sends_the_full_prompt_to_groq_and_the_compact_one_to_ollama() -> None:
    groq = Recorder("groq", compact_prompt=False, error=LLMUnavailableError("fora"))
    ollama = Recorder("ollama", compact_prompt=True)
    router = LLMRouter([groq, ollama])
    msgs = [Message("system", "PERSONA COMPLETA"), Message("user", "oi")]
    async for _ in router.stream(msgs, tools=SCHEMA):
        pass
    sent_groq, tools_groq = groq.received[0]
    sent_ollama, tools_ollama = ollama.received[0]
    assert sent_groq[0].content == "PERSONA COMPLETA" and tools_groq == SCHEMA
    assert sent_ollama[0].content != "PERSONA COMPLETA"
    assert tools_ollama is not None
    assert tools_ollama[0]["function"]["description"] == "Faz uma coisa muito útil."
    assert msgs[0].content == "PERSONA COMPLETA"  # the caller's list is never mutated


async def test_router_chat_also_compacts_for_ollama() -> None:
    ollama = Recorder("ollama", compact_prompt=True)
    await LLMRouter([ollama]).chat(
        [Message("system", "COMPLETA"), Message("user", "oi")], tools=SCHEMA
    )
    assert ollama.received[0][0][0].content != "COMPLETA"
