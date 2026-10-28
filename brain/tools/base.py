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
    # Whether a non-READ tool asks the user before running. Only WRITE tools may opt out, for
    # things that are the user's own, reversible and auditable (the long-term memory: `juniper
    # memory list/forget`). DESTRUCTIVE tools always ask (ADR-006).
    confirm: ClassVar[bool] = True

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
        if not isinstance(cls.confirm, bool):
            raise ValueError(f"{label}: 'confirm' deve ser True ou False")
        if not cls.confirm and cls.tier is not Tier.WRITE:
            raise ValueError(f"{label}: só ferramentas WRITE podem dispensar a confirmação")

    @classmethod
    def available(cls) -> bool:
        """Whether the tool is offered to the LLM right now (e.g. false while its backend is off).

        It only hides the tool from the schemas; a call that arrives anyway still reaches ``run``.
        """
        return True

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        """Optional validation, run BEFORE the user is asked to confirm (ADR-006).

        Return a ``ToolResult`` (normally a failure) to short-circuit: the user is never
        asked to approve something that is bound to fail, and no LLM round trip is wasted.
        Return ``None`` to proceed. ``run`` must still re-validate (state may change).
        """
        return None

    async def canonical_arguments(self, **kwargs: Any) -> dict[str, Any]:
        """The arguments in the one form the tool checks, shows, logs and sends (after precheck).

        Lets a tool put equivalent spellings in a single canonical form (a URL), so the call the
        user approves is byte for byte the call that runs, and a device never re-parses what the
        brain only normalised. The default keeps the arguments as they came. A failure here is
        ignored: the original arguments are used.
        """
        return dict(kwargs)

    async def requires_confirmation(self, **kwargs: Any) -> bool:
        """Whether THIS call asks the user first (ADR-014: confirmation that depends on the call).

        The default follows the class ``confirm`` flag. A WRITE tool may answer ``False`` for
        calls that are safe by their own arguments (a link to a trusted domain). The dispatcher
        ignores the answer for DESTRUCTIVE tools and treats a failing hook as "ask".
        """
        return type(self).confirm

    async def describe(self, **kwargs: Any) -> str | None:
        """Optional one-line summary of what the call will do, shown when asking to confirm.

        Lets the user approve "apagar «Dentista», segunda 16:00" instead of an opaque id.
        Return ``None`` to fall back to the raw arguments. Failures here are ignored.
        """
        return None

    async def subjects(self, **kwargs: Any) -> list[str] | None:
        """Short names of what the call acts on (e.g. event titles), or None if unknown.

        Used to recognise approval *in the conversation*: if the user said "sim" to a question
        that named every subject, the action may run without asking again. Return None when
        the call cannot be tied to words the user could have heard (it is then asked for).
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
