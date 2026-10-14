"""T-062: onde o tempo vai (turno do agente, recuperação, extração, embedding) e o log."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from brain.config import EmbeddingSettings, ExtractionSettings, RetrievalSettings, settings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.core.session import Turn
from brain.eventlog import event_log
from brain.memory.embeddings import LocalEmbeddingProvider
from brain.memory.extraction import MemoryExtractor
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoProfileStore, MongoVectorStore
from brain.memory.retrieval import recall_context
from brain.memory.vector import Memory
from brain.timing import EXTRACTION_LABELS, Stages, human, summarize, summarize_turn
from tests.test_agent import FakeProvider, call, make_registry, say
from tests.test_embeddings import _provider
from tests.test_extraction import MODEL, FakeEmbedder, ScriptedLLM, mems
from tests.test_vector_store import FakeConnection


def events(kind: str) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for path in event_log.directory.glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    return [e for e in rows if e["event"] == kind]


# --- the stopwatch ------------------------------------------------------------------------


def test_stages_accumulate_and_count() -> None:
    stages = Stages()
    with stages.stage("a"):
        time.sleep(0.01)
    with stages.stage("a"):
        time.sleep(0.01)
    stages.add("b", 5.4)
    assert stages.rounded()["a"] >= 20 and stages.rounded()["b"] == 5
    assert stages.counts == {"a": 2, "b": 1}
    assert stages.total_ms() >= 20


def test_a_failing_stage_is_still_timed() -> None:
    stages = Stages()
    with pytest.raises(RuntimeError), stages.stage("x"):
        raise RuntimeError("boom")
    assert "x" in stages.ms


def test_human_and_summaries() -> None:
    assert human(850) == "850ms" and human(2300) == "2.3s" and human(0) == "0ms"
    line = summarize(
        2400, {"llm": 1900, "recall": 210, "tools": 0}, {"recall": "memória", "llm": "LLM"}
    )
    assert line == "total 2.4s · memória 210ms · LLM 1.9s"  # label order, zero stages hidden
    turn = summarize_turn(
        2400, {"recall": 210, "llm": 1900}, {"embedding" if False else "embed": 160, "search": 40}
    )
    assert "memória 210ms" in turn and "[memória: embedding 160ms · busca 40ms]" in turn
    assert summarize(10, {"x": 5}) == "total 10ms · x 5ms"


# --- the agent turn -----------------------------------------------------------------------


async def test_a_turn_logs_where_its_time_went() -> None:
    registry, _ = make_registry()
    agent = Agent(
        FakeProvider([call("echo", text="oi"), say("pronto")]),
        Dispatcher(registry, conversational=True),
        tool_registry=registry,
    )
    await agent.run("faz")
    [timing] = events("timing")
    assert timing["scope"] == "turn" and timing["llm_calls"] == 2
    stages = timing["stages_ms"]
    assert {"recall", "llm", "llm_first_token", "tools", "persist"} <= set(stages)
    assert timing["total_ms"] >= sum(v for k, v in stages.items() if k != "llm_first_token") - 5
    assert agent.last_timing is not None and agent.last_timing[0] == timing["total_ms"]


async def test_a_failed_turn_is_timed_too() -> None:
    from brain.llm.errors import LLMUnavailableError

    agent = Agent(FakeProvider([]), Dispatcher(conversational=True))
    with pytest.raises(LLMUnavailableError):
        await agent.run("oi")
    assert len(events("timing")) == 1


# --- recall -------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.memory, "retrieval", RetrievalSettings(threshold=0.4))


def stores() -> tuple[MongoVectorStore, MongoProfileStore]:
    conn = FakeConnection()
    return MongoVectorStore(conn, embedding_model=MODEL), MongoProfileStore(conn)


async def test_recall_splits_the_model_load_from_the_embedding() -> None:
    store, profiles = stores()
    text = "O projeto X usa Laravel 11."
    store.add(Memory(text, "project", FakeEmbedder().embed(text), MODEL))

    class Cold(FakeEmbedder):
        last_load_ms = 0.0

        def embed(self, text: str, kind: str = "passage") -> list[float]:
            time.sleep(0.05)
            self.last_load_ms = 30.0  # of those 50ms, 30 were the one-off model load
            return super().embed(text, kind)

    context = await recall_context(store, Cold(), "Que framework o projeto X usa?", None, profiles)
    t = context.timings
    assert {"profile", "count", "embed_model_load", "embed", "search"} <= set(t)
    assert t["embed_model_load"] == 30 and t["embed"] >= 15
    assert context.audit()["timings_ms"] == t


async def test_an_empty_store_has_no_embedding_stage() -> None:
    store, profiles = stores()
    context = await recall_context(store, FakeEmbedder(), "qualquer coisa aqui", None, profiles)
    assert "embed" not in context.timings and "count" in context.timings


# --- extraction ---------------------------------------------------------------------------


async def test_extraction_times_every_stage_and_counts_llm_calls() -> None:
    store, profiles = stores()
    text = "O projeto X usa Laravel 11."
    store.add(Memory(text, "project", FakeEmbedder().embed(text), MODEL))
    llm = ScriptedLLM(
        extract={**mems(("O projeto X usa Laravel 12.", "project")), "profile": None},
        judge={"action": "new", "target": None},
    )
    extractor = MemoryExtractor(
        llm, FakeEmbedder(), store, ExtractionSettings(neighbor_threshold=0.3), profiles
    )
    result = await extractor.extract([Turn("Migrei o X para o Laravel 12", "Eita")], None)
    assert {"llm_extract", "profile_read", "embed", "search", "llm_judge", "write"} <= set(
        result.timings
    )
    assert result.llm_calls == 2 and result.total_ms >= max(result.timings.values()) - 1
    [timing] = events("timing")
    assert timing["scope"] == "extraction" and timing["memories_added"] == 1
    assert timing["counts"]["llm_judge"] == 1 and timing["error"] is False


async def test_a_conversation_with_nothing_to_say_is_not_timed() -> None:
    store, _ = stores()
    extractor = MemoryExtractor(ScriptedLLM(extract=mems()), FakeEmbedder(), store)
    result = await extractor.extract([Turn("  ", "oi")], None)
    assert result.timings == {} and events("timing") == []


async def test_extraction_failures_are_timed_with_the_error_flag() -> None:
    from brain.llm.errors import LLMUnavailableError

    store, _ = stores()
    extractor = MemoryExtractor(
        ScriptedLLM(extract=LLMUnavailableError("fora")), FakeEmbedder(), store
    )
    result = await extractor.extract([Turn("oi, uso ZSH", "ok")], None)
    assert result.error and events("timing")[0]["error"] is True


# --- embedder -----------------------------------------------------------------------------


def test_the_embedder_reports_inference_time_and_threads(tmp_path: Path) -> None:
    provider, _ = _provider(tmp_path)
    provider.embed_batch(["a", "bb", "ccc"])
    [row] = events("embedding")
    assert row["texts"] == 3 and row["kind"] == "passage" and row["load_ms"] == 0
    assert row["infer_ms"] >= 0 and row["threads"] == "auto"
    assert provider.last_infer_ms >= 0


def test_the_threads_setting_is_passed_to_onnxruntime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import onnxruntime as ort

    seen: dict[str, Any] = {}

    class FakeInput:
        name = "input_ids"

    class FakeSession:
        def __init__(self, path: str, sess_options: Any = None, providers: Any = None) -> None:
            seen["threads"] = sess_options.intra_op_num_threads

        def get_inputs(self) -> list[Any]:
            return [FakeInput()]

        def run(self, _o: Any, inputs: dict[str, Any]) -> list[Any]:
            import numpy as np

            return [np.ones((inputs["input_ids"].shape[0], inputs["input_ids"].shape[1], 4))]

    class FakeTokenizer:
        @classmethod
        def from_file(cls, path: str) -> FakeTokenizer:
            return cls()

        def enable_truncation(self, **kw: Any) -> None: ...

        def enable_padding(self) -> None: ...

        def encode_batch(self, texts: list[str]) -> list[Any]:
            class E:
                ids = [1, 2]
                attention_mask = [1, 1]

            return [E() for _ in texts]

    import tokenizers

    monkeypatch.setattr(ort, "InferenceSession", FakeSession)
    monkeypatch.setattr(tokenizers, "Tokenizer", FakeTokenizer)
    provider = LocalEmbeddingProvider(EmbeddingSettings(dir=tmp_path, threads=2))
    monkeypatch.setattr(provider, "_download", lambda: None)
    provider.embed("oi")
    assert seen["threads"] == 2
    [load] = events("embedding_model_load")
    assert load["threads"] == 2 and load["total_ms"] >= 0 and load["download_ms"] >= 0
    assert provider.last_load_ms == 0 or provider.last_load_ms > 0  # set during the first call
    assert events("embedding")[-1]["load_ms"] >= 0


def test_threads_defaults_to_automatic() -> None:
    assert EmbeddingSettings().threads == 0
    with pytest.raises(ValueError):
        EmbeddingSettings(threads=-1)


# --- terminal ------------------------------------------------------------------------------


class SlowProvider(FakeProvider):
    """A provider that takes a measurable time, so the stages are not all rounded to 0ms."""

    async def stream(self, messages: Any, **kwargs: Any) -> Any:
        import asyncio

        await asyncio.sleep(0.02)
        async for chunk in super().stream(messages, **kwargs):
            yield chunk


class SlowLLM(ScriptedLLM):
    async def chat(self, messages: Any, **kwargs: Any) -> Any:
        import asyncio

        await asyncio.sleep(0.02)
        return await super().chat(messages, **kwargs)


class _Printer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def timing(self, text: str) -> None:
        self.lines.append(text)

    def say(self, text: str, style: str | None = None) -> None: ...

    def begin_reply(self) -> None: ...

    def end_line(self) -> None: ...

    def token(self, text: str) -> None: ...

    @property
    def out(self) -> Any:
        return type("O", (), {"print": staticmethod(lambda *a, **k: None)})()


class _Reader:
    def __init__(self, *lines: str) -> None:
        self.lines = list(lines)

    async def read(self, prompt: str) -> str:
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


async def test_the_terminal_shows_timing_only_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.clients.cli import converse

    for show, expected in ((False, 0), (True, 1)):
        monkeypatch.setattr(settings.agent, "show_timing", show)
        agent = Agent(SlowProvider([say("oi")]), Dispatcher(conversational=True))
        printer = _Printer()
        await converse(agent, printer, _Reader("bom dia"), None)  # type: ignore[arg-type]
        assert len(printer.lines) == expected
        if show:
            assert printer.lines[0].startswith("total ") and "LLM" in printer.lines[0]


async def test_the_terminal_shows_extraction_timing_and_a_spinner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brain.clients.cli import remember_conversation

    store, profiles = stores()
    llm = SlowLLM(extract={**mems(("Petrus prefere ZSH.", "preference")), "profile": None})
    memory = LongTermMemory(
        store,
        FakeEmbedder(),
        MemoryExtractor(llm, FakeEmbedder(), store, ExtractionSettings()),
        profiles,
    )
    agent = Agent(FakeProvider([say("ok")]), Dispatcher(conversational=True))
    await agent.run("prefiro ZSH")

    class P(_Printer):
        def __init__(self) -> None:
            super().__init__()
            self.said: list[str] = []
            self.spun: list[str | None] = []

        def say(self, text: str, style: str | None = None) -> None:
            self.said.append(text)

        def busy(self, text: str) -> None:
            self.spun.append(text)

        def idle(self) -> None:
            self.spun.append(None)

    monkeypatch.setattr(settings.agent, "show_timing", True)
    printer = P()
    await remember_conversation(agent, memory, printer)  # type: ignore[arg-type]
    assert printer.spun == ["guardando o que aprendi...", None]  # shown while it works, then gone
    assert printer.said == ["Guardei 1 coisa sobre você para a próxima."]
    assert "LLM extração" in printer.lines[0] and "chamada(s) ao LLM" in printer.lines[0]
    assert EXTRACTION_LABELS["llm_judge"] == "LLM julgamento"
