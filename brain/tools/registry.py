"""Tool registry with decorator-based registration and package auto-discovery.

A tool file only needs ``@register_tool`` on its class; ``registry.discover()`` imports
every module under ``brain/tools/`` so no central list has to be edited.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any

from brain.tools.base import Tool

# Infrastructure modules: importing them registers nothing.
_SKIP_MODULES = frozenset({"base", "registry"})


class ToolRegistry:
    """Maps tool names to tool classes (ADR-002)."""

    def __init__(self) -> None:
        self._tools: dict[str, type[Tool]] = {}

    def register(self, cls: type[Tool]) -> type[Tool]:
        """Register ``cls`` under ``cls.name``. Usable as a class decorator.

        Re-registering the very same class is a no-op (module reloads); a different
        class with an already-taken name is an error.
        """
        if not (inspect.isclass(cls) and issubclass(cls, Tool)) or inspect.isabstract(cls):
            raise TypeError(f"{cls!r} não é uma subclasse concreta de Tool")
        existing = self._tools.get(cls.name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"Tool '{cls.name}' já registrada por {existing.__module__}.{existing.__qualname__}"
            )
        self._tools[cls.name] = cls
        return cls

    def get(self, name: str) -> type[Tool] | None:
        """Return the tool class, or None if unknown (the dispatcher reports it to the LLM)."""
        return self._tools.get(name)

    def all(self) -> list[type[Tool]]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        """Schemas of all tools in the ADR-002 format, ready for the LLM ``tools`` field."""
        return [cls.to_schema() for cls in self._tools.values()]

    def discover(self, package: str = "brain.tools") -> None:
        """Import every module of ``package`` so their ``@register_tool`` calls run.

        Import errors propagate on purpose: a broken tool should fail loudly at startup,
        not silently vanish from the agent's toolbox.
        """
        pkg = importlib.import_module(package)
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name in _SKIP_MODULES or info.name.startswith("_"):
                continue
            importlib.import_module(f"{package}.{info.name}")

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


registry = ToolRegistry()
register_tool = registry.register
