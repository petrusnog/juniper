"""Tests for brain.tools.base: Tool, ToolResult, Tier, Locality."""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar

import pytest

from brain.tools.base import Locality, Tier, Tool, ToolResult

PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": "URL ou nome do app. Ex: 'https://music.youtube.com' ou 'firefox'",
        }
    },
    "required": ["target"],
}


class OpenApp(Tool):
    name = "open_app"
    description = "Abre um aplicativo ou URL no sistema operacional do usuário"
    parameters = PARAMS

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, output=f"abri {kwargs['target']}")


def test_tool_is_abstract() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]


def test_to_schema_matches_adr_002() -> None:
    assert OpenApp.to_schema() == {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Abre um aplicativo ou URL no sistema operacional do usuário",
            "parameters": PARAMS,
        },
    }


def test_to_schema_returns_independent_copy() -> None:
    schema = OpenApp.to_schema()
    schema["function"]["parameters"]["properties"].clear()
    assert OpenApp.parameters["properties"]  # class metadata untouched


def test_defaults_are_write_and_executor() -> None:
    assert OpenApp.tier is Tier.WRITE
    assert OpenApp.locality is Locality.EXECUTOR


def test_explicit_tier_and_locality() -> None:
    class Fetch(Tool):
        name = "web_fetch"
        description = "Baixa uma página web"
        parameters = {"type": "object", "properties": {}}
        tier = Tier.READ
        locality = Locality.CLOUD

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True)

    assert (Fetch.tier, Fetch.locality) == (Tier.READ, Locality.CLOUD)


async def test_run_returns_tool_result() -> None:
    result = await OpenApp().run(target="firefox")
    assert result == ToolResult(success=True, output="abri firefox")
    assert result.error is None and result.metadata is None


def test_tool_result_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        ToolResult(success=True).success = False  # type: ignore[misc]


def test_enum_values() -> None:
    assert [t.value for t in Tier] == ["read", "write", "destructive", "forbidden"]
    assert [loc.value for loc in Locality] == ["cloud", "executor"]


def test_abstract_intermediate_base_skips_validation() -> None:
    class Base(Tool):  # no metadata, no run: still abstract
        pass

    class Child(Base):
        name = "child_tool"
        description = "Filha"
        parameters = {"type": "object", "properties": {}}

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True)

    assert Child.to_schema()["function"]["name"] == "child_tool"


def _define(**attrs: Any) -> type[Tool]:
    """Build a concrete Tool subclass from the given class attributes."""

    async def run(self: Tool, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True)

    base: dict[str, Any] = {
        "name": "ok_tool",
        "description": "Descrição",
        "parameters": {"type": "object", "properties": {}},
        "run": run,
    }
    return type("Bad", (Tool,), {**base, **attrs})


def test_valid_definition_helper_works() -> None:
    assert _define().name == "ok_tool"


@pytest.mark.parametrize("name", ["OpenApp", "open-app", "open app", "_x", "x_", "1x", "a__b", ""])
def test_rejects_non_snake_case_name(name: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        _define(name=name)


def test_rejects_missing_name() -> None:
    async def run(self: Tool, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True)

    with pytest.raises(ValueError, match="name"):
        type(
            "NoName",
            (Tool,),
            {"description": "d", "parameters": {"type": "object", "properties": {}}, "run": run},
        )


@pytest.mark.parametrize("description", ["", "   ", None, 42])
def test_rejects_bad_description(description: object) -> None:
    with pytest.raises(ValueError, match="description"):
        _define(description=description)


@pytest.mark.parametrize(
    "params",
    [
        None,
        [],
        {"type": "array", "properties": {}},
        {"type": "object"},
        {"type": "object", "properties": []},
        {"type": "object", "properties": {"a": {}}, "required": "a"},
        {"type": "object", "properties": {"a": {}}, "required": ["b"]},
    ],
)
def test_rejects_bad_parameters(params: object) -> None:
    with pytest.raises(ValueError, match="parameters|required"):
        _define(parameters=params)


def test_rejects_bad_tier_and_locality() -> None:
    with pytest.raises(ValueError, match="tier"):
        _define(tier="read")
    with pytest.raises(ValueError, match="locality"):
        _define(locality="cloud")


def test_classvar_annotations_do_not_break_subclassing() -> None:
    class Extra(Tool):
        name = "extra_tool"
        description = "Com atributo extra"
        parameters = {"type": "object", "properties": {}}
        note: ClassVar[str] = "x"

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True)

    assert Extra.note == "x"


async def test_precheck_defaults_to_none_and_can_short_circuit() -> None:
    assert await OpenApp().precheck(target="firefox") is None

    class Picky(OpenApp):
        name = "picky_tool"

        async def precheck(self, **kwargs: Any) -> ToolResult | None:
            return ToolResult(success=False, error="não vai dar")

    assert await Picky().precheck() == ToolResult(success=False, error="não vai dar")
