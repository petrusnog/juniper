"""Tests for brain.tools.registry: registration, lookup and auto-discovery."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from brain.tools import registry as registry_module
from brain.tools.base import Tool, ToolResult
from brain.tools.registry import ToolRegistry, register_tool, registry

PARAMS: dict[str, Any] = {"type": "object", "properties": {}}


def make_tool(name: str, description: str = "Faz algo") -> type[Tool]:
    async def run(self: Tool, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True)

    return type(
        f"Tool_{name}",
        (Tool,),
        {"name": name, "description": description, "parameters": PARAMS, "run": run},
    )


@pytest.fixture
def fresh_global_registry(monkeypatch: pytest.MonkeyPatch) -> ToolRegistry:
    """Isolate the module-level registry so tests don't leak tools into each other."""
    monkeypatch.setattr(registry, "_tools", {})
    return registry


def test_register_and_get() -> None:
    reg = ToolRegistry()
    tool = make_tool("alpha")
    assert reg.register(tool) is tool
    assert reg.get("alpha") is tool
    assert "alpha" in reg and len(reg) == 1


def test_get_unknown_returns_none() -> None:
    assert ToolRegistry().get("nope") is None


def test_all_and_schemas() -> None:
    reg = ToolRegistry()
    a, b = make_tool("alpha", "A"), make_tool("beta", "B")
    reg.register(a)
    reg.register(b)
    assert reg.all() == [a, b]
    assert reg.schemas() == [a.to_schema(), b.to_schema()]
    assert reg.schemas()[1]["function"]["name"] == "beta"


def test_duplicate_name_is_an_error() -> None:
    reg = ToolRegistry()
    reg.register(make_tool("alpha"))
    with pytest.raises(ValueError, match="alpha"):
        reg.register(make_tool("alpha"))


def test_registering_same_class_twice_is_idempotent() -> None:
    reg = ToolRegistry()
    tool = make_tool("alpha")
    reg.register(tool)
    reg.register(tool)
    assert len(reg) == 1


def test_rejects_non_tool_and_abstract() -> None:
    reg = ToolRegistry()
    with pytest.raises(TypeError):
        reg.register(int)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        reg.register(Tool)  # type: ignore[type-abstract]


def test_decorator_registers_in_global_registry(fresh_global_registry: ToolRegistry) -> None:
    @register_tool
    class Decorated(Tool):
        name = "decorated_tool"
        description = "Via decorator"
        parameters = PARAMS

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True)

    assert fresh_global_registry.get("decorated_tool") is Decorated


def _write_package(root: Path, pkg: str, files: dict[str, str]) -> None:
    folder = root / pkg
    folder.mkdir()
    (folder / "__init__.py").write_text("")
    for filename, body in files.items():
        (folder / filename).write_text(textwrap.dedent(body))


TOOL_SOURCE = """
    from typing import Any
    from brain.tools.base import Tool, ToolResult
    from brain.tools.registry import register_tool

    @register_tool
    class {cls}(Tool):
        name = "{name}"
        description = "Tool de teste"
        parameters = {{"type": "object", "properties": {{}}}}

        async def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True)
"""


def test_discover_finds_new_tool_file_without_central_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_global_registry: ToolRegistry
) -> None:
    _write_package(
        tmp_path,
        "tmp_tools_a",
        {
            "one.py": TOOL_SOURCE.format(cls="One", name="tool_one"),
            "two.py": TOOL_SOURCE.format(cls="Two", name="tool_two"),
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in [m for m in sys.modules if m.startswith("tmp_tools_a")]:
        monkeypatch.delitem(sys.modules, mod)

    assert len(fresh_global_registry) == 0
    fresh_global_registry.discover("tmp_tools_a")

    assert {"tool_one", "tool_two"} <= {c.name for c in fresh_global_registry.all()}


def test_discover_skips_infrastructure_and_private_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_global_registry: ToolRegistry
) -> None:
    boom = "raise RuntimeError('should not be imported')\n"
    _write_package(
        tmp_path,
        "tmp_tools_b",
        {
            "base.py": boom,
            "registry.py": boom,
            "_private.py": boom,
            "real.py": TOOL_SOURCE.format(cls="Real", name="real_tool"),
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in [m for m in sys.modules if m.startswith("tmp_tools_b")]:
        monkeypatch.delitem(sys.modules, mod)

    fresh_global_registry.discover("tmp_tools_b")
    assert fresh_global_registry.get("real_tool") is not None


def test_discover_propagates_import_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_global_registry: ToolRegistry
) -> None:
    _write_package(tmp_path, "tmp_tools_c", {"broken.py": "import module_that_does_not_exist\n"})
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ImportError):
        fresh_global_registry.discover("tmp_tools_c")


def test_discover_default_package_runs_without_error(
    fresh_global_registry: ToolRegistry,
) -> None:
    fresh_global_registry.discover()  # brain.tools: stubs register nothing yet
    assert registry_module.registry is fresh_global_registry
