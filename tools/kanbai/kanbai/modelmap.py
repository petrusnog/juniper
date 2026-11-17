"""Story points -> executor model, read from a TOML file (editable without touching code)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FILE = Path(__file__).with_name("models.default.toml")


@dataclass(frozen=True)
class ModelChoice:
    provider: str  # "claude" | "codex" | ...
    model: str


class ModelMap:
    def __init__(self, tiers: list[tuple[int, ModelChoice]], qa: ModelChoice) -> None:
        # tiers: (max_points, choice), checked in ascending order; last one catches the rest.
        self.tiers = sorted(tiers, key=lambda t: t[0])
        self.qa = qa

    def for_points(self, points: int) -> ModelChoice:
        for max_points, choice in self.tiers:
            if points <= max_points:
                return choice
        return self.tiers[-1][1]

    @classmethod
    def load(cls, path: Path | None = None) -> ModelMap:
        data = tomllib.loads((path or DEFAULT_FILE).read_text(encoding="utf-8"))
        tiers = [
            (int(t["max_points"]), ModelChoice(t["provider"], t["model"]))
            for t in data["executor"]
        ]
        if not tiers:
            raise ValueError("models file needs at least one [[executor]] tier")
        qa = data["qa"]
        return cls(tiers, ModelChoice(qa["provider"], qa["model"]))
