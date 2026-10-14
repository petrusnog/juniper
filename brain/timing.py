"""Stage timing: where the time of a turn (or of the memory work) goes (T-062).

``Stages`` accumulates milliseconds per named stage. Agent turns and the memory extraction
log them as ``timing`` events in the structured log (and the CLI shows them when
``agent.show_timing`` is on), so a slow step can be pointed at instead of guessed at.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager


class Stages:
    """Milliseconds spent per stage, plus the total since creation."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self.ms: dict[str, float] = {}
        self.counts: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - started) * 1000)

    def add(self, name: str, ms: float) -> None:
        self.ms[name] = self.ms.get(name, 0.0) + ms
        self.counts[name] = self.counts.get(name, 0) + 1

    def total_ms(self) -> int:
        return round((time.perf_counter() - self._started) * 1000)

    def rounded(self) -> dict[str, int]:
        return {name: round(ms) for name, ms in self.ms.items()}


def human(ms: float) -> str:
    """``850ms`` below a second, ``2.3s`` above."""
    return f"{round(ms)}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


TURN_LABELS = {
    "recall": "memória",
    "llm": "LLM",
    "llm_first_token": "1ª palavra",
    "tools": "ferramentas",
    "persist": "salvar sessão",
}
RECALL_LABELS = {
    "profile": "perfil",
    "count": "contagem",
    "embed_model_load": "carga do modelo",
    "embed": "embedding",
    "search": "busca",
}
EXTRACTION_LABELS = {
    "profile_read": "ler perfil",
    "llm_extract": "LLM extração",
    "profile_write": "gravar perfil",
    "embed_model_load": "carga do modelo",
    "embed": "embedding",
    "search": "busca",
    "llm_judge": "LLM julgamento",
    "write": "gravação",
}


def summarize(
    total_ms: int, stages: Mapping[str, int], labels: Mapping[str, str] | None = None
) -> str:
    """One line for the terminal, stages in the order of ``labels``: ``total 2.4s · LLM 1.9s``."""
    labels = labels or {}
    names = [n for n in labels if n in stages] + [n for n in stages if n not in labels]
    parts = [
        f"{labels.get(name, name)} {human(stages[name])}" for name in names if stages[name] > 0
    ]
    return " · ".join([f"total {human(total_ms)}", *parts])


def summarize_turn(total_ms: int, stages: Mapping[str, int], recall: Mapping[str, int]) -> str:
    """The turn, with the memory step opened up (``memória 210ms (embedding 160ms, ...)``)."""
    line = summarize(total_ms, stages, TURN_LABELS)
    if recall and stages.get("recall", 0) > 0:
        detail = summarize(sum(recall.values()), recall, RECALL_LABELS).split(" · ", 1)
        if len(detail) == 2:
            line += f"  [memória: {detail[1]}]"
    return line
