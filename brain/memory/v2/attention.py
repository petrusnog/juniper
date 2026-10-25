"""The Umwelt as a filter of attention (ADR-017, section 5.7).

What interests her decides what she notices. The strongest interests and the firmest held beliefs
become a very short list of topics (at most 3) that goes into the extraction prompt: a fact that
touches one of them, even said in passing, gets a closer look. The ceiling of 3 is on purpose:
without it the bias would decide what she sees and see only what confirms the bias. The list
changes nothing about what may be stored: the secret filters, the size limits and every rule of
the extraction prompt stay as they were.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from brain.memory.v2 import confidence as conf
from brain.memory.v2.beliefs import HELD

if TYPE_CHECKING:
    from brain.memory.v2.store import V2Data

MAX_TOPICS = 3
MIN_INTEREST = 0.25  # an interest that faded below this no longer catches her eye
MIN_BELIEF_CONFIDENCE = 0.45
FIRMNESS_SCALE = 4.0  # n / (n + 4): half the weight at n = 4, never quite 1
GENERIC_TOPICS = {"geral", "outro", "outros", ""}


def attention_topics(data: V2Data, limit: int = MAX_TOPICS) -> list[str]:
    """Up to ``limit`` (never more than 3) topics, the strongest first. Empty with no V2 data."""
    limit = max(0, min(limit, MAX_TOPICS))
    scored: list[tuple[float, str]] = []
    for interest in data.interests.list():
        if interest.intensity >= MIN_INTEREST:
            scored.append((interest.intensity, interest.topic))
    for belief in data.beliefs.list({"status": {"$in": list(HELD)}}):
        topic = belief.topic.strip()
        confidence = conf.confidence(belief.alpha, belief.beta)
        if topic.casefold() in GENERIC_TOPICS or confidence < MIN_BELIEF_CONFIDENCE:
            continue
        firmness = max(0.0, belief.n) / (max(0.0, belief.n) + FIRMNESS_SCALE)
        scored.append((firmness * confidence, topic))
    scored.sort(key=lambda item: (-item[0], item[1]))
    topics: list[str] = []
    for _, topic in scored:
        if len(topics) >= limit:
            break
        if topic.casefold() not in {t.casefold() for t in topics}:
            topics.append(topic)
    return topics


class AttentionSource:
    """The attention list for the extractor, over a lazily opened database handle.

    It is what ``MemoryExtractor(attention=...)`` calls before each extraction; ``close`` releases
    the handle with the memory it belongs to.
    """

    def __init__(self) -> None:
        self._data: V2Data | None = None

    def __call__(self) -> list[str]:
        if self._data is None:
            from brain.memory.v2 import open_v2_data

            self._data = open_v2_data()
        return attention_topics(self._data)

    def close(self) -> None:
        if self._data is not None:
            self._data.close()
            self._data = None
