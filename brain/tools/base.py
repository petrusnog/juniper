"""`Tool` base class, `ToolResult` and the `Tier` / `Locality` enums.

Tools are described to the LLM in the OpenAI function-calling format (ADR-002) and
carry a security tier (ADR-006). Registration lives in ``registry.py`` (T-021).
"""

from __future__ import annotations

import copy
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


class Tier(StrEnum):
    """Security tier (ADR-006); decides whether the dispatcher asks for confirmation."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    FORBIDDEN = "forbidden"


class Locality(StrEnum):
    """Where a tool runs (ADR-007). In Sprint 1 everything runs locally."""

    CLOUD = "cloud"
    EXECUTOR = "executor"


@dataclass(frozen=True)
class ToolResult:
    """Uniform return of every tool; the agent loop feeds it back to the LLM."""

    success: bool
    output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None


class Tool(ABC):
    """Base class for tools. Subclasses declare their metadata as class attributes.

    A concrete subclass (one that implements ``run``) is validated when the class is
    defined, so a malformed tool fails at import time instead of mid-conversation.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]
    # Default WRITE (ADR-006): a tool nobody classified asks for confirmation.
    tier: ClassVar[Tier] = Tier.WRITE
    locality: ClassVar[Locality] = Locality.EXECUTOR

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Intermediate abstract bases may leave the metadata to their children.
        if not getattr(cls.run, "__isabstractmethod__", False):
            cls._validate()

    @classmethod
    def _validate(cls) -> None:
        label = cls.__name__
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not _SNAKE_CASE.match(name):
            raise ValueError(f"{label}: 'name' deve ser uma string snake_case, recebido {name!r}")

        description = getattr(cls, "description", None)
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{label}: 'description' deve ser uma string não vazia")

        params = getattr(cls, "parameters", None)
        if not isinstance(params, dict) or params.get("type") != "object":
            raise ValueError(f"{label}: 'parameters' deve ser um JSON Schema com type='object'")
        properties = params.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"{label}: 'parameters' precisa do campo 'properties' (dict)")
        required = params.get("required", [])
        if not isinstance(required, list) or not set(required) <= properties.keys():
            raise ValueError(f"{label}: 'required' deve listar apenas chaves de 'properties'")

        if not isinstance(cls.tier, Tier):
            raise ValueError(f"{label}: 'tier' deve ser um Tier")
        if not isinstance(cls.locality, Locality):
            raise ValueError(f"{label}: 'locality' deve ser uma Locality")

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        """Optional validation, run BEFORE the user is asked to confirm (ADR-006).

        Return a ``ToolResult`` (normally a failure) to short-circuit: the user is never
        asked to approve something that is bound to fail, and no LLM round trip is wasted.
        Return ``None`` to proceed. ``run`` must still re-validate (state may change).
        """
        return None

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool. Expected failures return ``ToolResult(success=False)``."""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """Return the tool in the canonical ADR-002 (OpenAI function calling) format."""
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": copy.deepcopy(cls.parameters),
            },
        }
