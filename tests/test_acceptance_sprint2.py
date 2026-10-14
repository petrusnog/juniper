"""T-060: teste de aceite do marco da Sprint 2, de ponta a ponta e sem rede.

Cenário da issue: a Juniper lembra, entre sessões, que o usuário prefere ZSH, odeia tabulação com
espaço e que o projeto X usa Laravel 11; associa "framework PHP" a "Laravel"; atualiza para
Laravel 12 sem manter a antiga ativa; e esquece o projeto X quando pedido.

O que é REAL aqui: o chat (`cli._chat`), o agente, a sessão, a extração, a deduplicação e a
atualização, o filtro de segredos, o `VectorStore`, a recuperação e a injeção no prompt, as tools e
a confirmação.
O que é SIMULADO: o LLM (regras fixas que leem o bloco de memórias que chegou ao prompt), o modelo
de embeddings (um mapa de conceitos, para dar a associação semântica sem baixar nada) e o MongoDB
(collection em memória). A validação com LLM, modelo e banco reais é o roteiro manual
(docs/html/T-060.html).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar

import pytest

from brain.clients import cli
from brain.config import ExtractionSettings, RetrievalSettings, settings
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message, StreamChunk, ToolCall
from brain.memory.extraction import EXTRACT_SYSTEM, JUDGE_SYSTEM, MemoryExtractor
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoVectorStore
from brain.memory.retrieval import CLOSE_TAG, HEADER, OPEN_TAG
from tests.test_vector_store import FakeConnection

MODEL = "conceitos-simulados"

# --- embeddings simulados: palavras do mesmo assunto caem na mesma dimensão ---------------

_CONCEPTS = [
    {"shell", "zsh", "bash", "fish", "terminal"},
    {"indentar", "indentacao", "tabulacao", "tab", "tabs", "espaco", "espacos"},
    {"php", "framework", "laravel", "symfony"},
    {"projeto", "x"},
]
_NUMBERS = ["11", "12"]


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.casefold())
    return re.findall(r"\w+", "".join(c for c in text if not unicodedata.combining(c)))


class ConceptEmbedder:
    model_id = MODEL
    dimension = len(_CONCEPTS) + len(_NUMBERS)

    def embed(self, text: str, kind: str = "passage") -> list[float]:
        words = _words(text)
        vec = [float(sum(w in group for w in words)) for group in _CONCEPTS]
        vec += [0.3 * words.count(n) for n in _NUMBERS]
        return vec if any(vec) else [1e-6] + [0.0] * (self.dimension - 1)

    def embed_batch(self, texts: Sequence[str], kind: str = "passage") -> list[list[float]]:
        return [self.embed(t, kind) for t in texts]


# --- LLM simulado -------------------------------------------------------------------------


def _strip_clock(text: str) -> str:
    return re.sub(r"^\[[^\]]*\]\s*", "", text)


def _memory_lines(messages: list[Message]) -> list[str]:
    for m in messages:
        content = m.content or ""
        if m.role == "system" and content.startswith(HEADER):
            body = content.split(OPEN_TAG, 1)[1].split(CLOSE_TAG, 1)[0]
            return [ln.strip() for ln in body.splitlines() if ln.strip()]
    return []


class SimulatedLLM(LLMProvider):
    """Extrai, julga e conversa por regras. Só sabe o que está no bloco <memorias> do prompt."""

    name: ClassVar[str] = "simulado"

    def __init__(self) -> None:
        self.prompts: list[list[Message]] = []
        self.on_failover = None

    async def aclose(self) -> None:
        return None

    # extraction / judge
    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        system, user = messages[0].content or "", messages[1].content or ""
        if system == EXTRACT_SYSTEM:
            data: dict[str, Any] = {"memories": self._extract(user)}
        else:
            assert system == JUDGE_SYSTEM
            data = self._judge(user)
        import json

        return LLMResponse(Message("assistant", json.dumps(data)), model="sim", provider="sim")

    @staticmethod
    def _extract(transcript: str) -> list[dict[str, str]]:
        said = " ".join(
            _words(ln) and ln for ln in transcript.splitlines() if ln.startswith("Usuário:")
        )
        said = " ".join(_words(said))
        found: list[dict[str, str]] = []
        if "prefiro zsh" in said:
            found.append({"text": "Prefere ZSH.", "type": "preference"})
        if "odeio tabulacao com espaco" in said:
            found.append({"text": "Odeia tabulação com espaço.", "type": "preference"})
        if match := re.search(r"projeto x usa laravel (\d+)", said):
            found.append({"text": f"O projeto X usa Laravel {match[1]}.", "type": "project"})
        if match := re.search(r"migrei o x para o laravel (\d+)", said):
            found.append({"text": f"O projeto X usa Laravel {match[1]}.", "type": "project"})
        return found

    @staticmethod
    def _judge(prompt: str) -> dict[str, Any]:
        new = re.search(r'Memória nova: "(.*?)"', prompt)
        assert new is not None
        new_version = re.search(r"Laravel (\d+)", new[1])
        for old_id, old_text in re.findall(r'- id=(\S+) \(\w+\): "(.*?)"', prompt):
            old_version = re.search(r"Laravel (\d+)", old_text)
            if new_version and old_version and new_version[1] != old_version[1]:
                return {"action": "supersede", "target": old_id}
        return {"action": "new", "target": None}

    # conversation
    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        self.prompts.append(list(messages))
        non_system = [m for m in messages if m.role != "system"]
        last = non_system[-1]
        users = [m for m in non_system if m.role == "user"]
        asked = _strip_clock(users[-1].content or "")
        said = " ".join(_words(asked))
        lines = _memory_lines(messages)

        if last.role == "tool" and (last.content or "").startswith("PENDENTE"):
            text = "Vou apagar tudo o que sei do projeto X. Posso?"
        elif last.role == "tool":
            text = "Pronto, esqueci."
        elif "esquece" in said:
            yield StreamChunk(tool_calls=(ToolCall("f1", "forget", {"query": "projeto X"}),))
            return
        elif "shell" in said:
            text = "Você usa ZSH." if any("ZSH" in ln for ln in lines) else "Não lembro disso."
        elif "indentar" in said:
            ok = any("tabulação" in ln for ln in lines)
            text = "Com tabulação, não com espaços." if ok else "Não lembro disso."
        elif "framework" in said:
            version = next((m[1] for ln in lines if (m := re.search(r"Laravel (\d+)", ln))), None)
            text = f"O projeto X usa Laravel {version}." if version else "Não lembro disso."
        else:
            text = "Anotado."
        yield StreamChunk(delta=text, finish_reason="stop")

    async def health_check(self) -> bool:
        return True


class ScriptedReader:
    """Stands in for the terminal: types the given lines, then Ctrl+D."""

    def __init__(self, *lines: str) -> None:
        self.lines = list(lines)

    async def read(self, prompt: str) -> str:
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


# --- the scenario -------------------------------------------------------------------------


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> tuple[SimulatedLLM, MongoVectorStore]:
    """A "machine": one LLM, one database, and a way to open the chat again and again."""
    monkeypatch.setattr(settings.memory.session, "store", "file")  # sessions in tmp files
    monkeypatch.setattr(settings.memory, "retrieval", RetrievalSettings(threshold=0.5))
    llm = SimulatedLLM()
    store = MongoVectorStore(FakeConnection(), embedding_model=MODEL)
    embedder = ConceptEmbedder()

    def open_memory(router: Any) -> LongTermMemory:
        extractor = MemoryExtractor(
            llm, embedder, store, ExtractionSettings(neighbor_threshold=0.3)
        )
        return LongTermMemory(store, embedder, extractor)

    monkeypatch.setattr(cli, "build_router", lambda provider: llm)
    monkeypatch.setattr(cli, "open_long_term_memory", open_memory)
    return llm, store


async def session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *lines: str
) -> str:
    """One whole chat: open it, type ``lines``, close it with Ctrl+D. Returns what it printed."""
    monkeypatch.setattr(cli, "LineReader", lambda: ScriptedReader(*lines))
    capsys.readouterr()
    assert await cli._chat(None, None) == 0
    return capsys.readouterr().out


def active(store: MongoVectorStore) -> list[str]:
    return sorted(m.text for m in store.list_memories())


async def test_the_milestone(
    world: tuple[SimulatedLLM, MongoVectorStore],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    llm, store = world

    # 1. Session 1: the user says three durable things and closes the terminal.
    out = await session(
        monkeypatch,
        capsys,
        "Oi! Prefiro ZSH.",
        "Odeio tabulação com espaço.",
        "O projeto X usa Laravel 11.",
    )
    assert "Guardei 3 coisas sobre você" in out
    assert active(store) == [
        "O projeto X usa Laravel 11.",
        "Odeia tabulação com espaço.",
        "Prefere ZSH.",
    ]
    assert {m.type for m in store.list_memories()} == {"preference", "project"}

    # 2. Session 2 (new process state, same database): the memories come back by meaning.
    out = await session(
        monkeypatch,
        capsys,
        "Qual shell eu uso?",
        "Como devo indentar?",
        "Que framework PHP o projeto X usa?",
    )
    assert "Você usa ZSH." in out
    assert "Com tabulação, não com espaços." in out
    assert "O projeto X usa Laravel 11." in out  # "framework PHP" found "Laravel"
    assert "Não lembro" not in out and "Guardei" not in out  # nothing new to keep

    # Only what is relevant reaches each prompt (the threshold keeps the rest out).
    shell_prompt = next(p for p in llm.prompts if "Qual shell" in (p[-1].content or ""))
    block = " ".join(_memory_lines(shell_prompt))
    assert "ZSH" in block and "Laravel" not in block and "tabulação" not in block

    # 3. Update: the new version replaces the old one, which stops being active.
    out = await session(monkeypatch, capsys, "Migrei o X para o Laravel 12.")
    assert "Guardei 1 coisa sobre você" in out
    assert "O projeto X usa Laravel 12." in active(store)
    assert "O projeto X usa Laravel 11." not in active(store)
    old = next(m for m in store.list_memories(None, True) if "Laravel 11" in m.text)
    assert old.status == "superseded" and old.superseded_by is not None
    out = await session(monkeypatch, capsys, "Que framework PHP o projeto X usa?")
    assert "O projeto X usa Laravel 12." in out and "Laravel 11" not in out

    # 4. Forget: it asks first, deletes for good (old versions too), and stops recalling.
    out = await session(monkeypatch, capsys, "Esquece o projeto X.")
    assert "Posso?" in out
    assert any("Laravel" in m.text for m in store.list_memories(None, True))  # not yet
    out = await session(monkeypatch, capsys, "Esquece o projeto X.", "sim")
    assert "esqueci" in out.lower()
    assert [m.text for m in store.list_memories(None, True) if "projeto X" in m.text] == []
    assert not any("Laravel" in m.text for m in store.list_memories(None, True))
    assert active(store) == ["Odeia tabulação com espaço.", "Prefere ZSH."]  # the rest stays
    out = await session(monkeypatch, capsys, "Que framework PHP o projeto X usa?")
    assert "Não lembro disso." in out


@pytest.mark.integration
def test_real_embedding_model_associates_and_separates(tmp_path: Any) -> None:
    """Com o modelo REAL (baixa ~120 MB): a associação semântica do marco e a calibração.

    Rode com `pytest -m integration -s` e use as notas impressas para ajustar
    `memory.retrieval.threshold`: o corte deve ficar entre o pior "relevante" e o melhor
    "irrelevante".
    """
    from brain.config import EmbeddingSettings
    from brain.memory.embeddings import LocalEmbeddingProvider

    model = LocalEmbeddingProvider(EmbeddingSettings(dir=tmp_path))
    memories = ["Prefere ZSH.", "Odeia tabulação com espaço.", "O projeto X usa Laravel 11."]
    pairs = [  # (pergunta, memória que deve responder)
        ("Qual shell eu uso?", memories[0]),
        ("Como devo indentar?", memories[1]),
        ("Que framework PHP o projeto X usa?", memories[2]),
    ]
    vectors = dict(zip(memories, model.embed_batch(memories, "passage"), strict=True))

    def score(question: str, memory: str) -> float:
        q = model.embed(question, "query")
        return sum(a * b for a, b in zip(q, vectors[memory], strict=True))

    relevant = [score(q, m) for q, m in pairs]
    irrelevant = [score(q, other) for q, m in pairs for other in memories if other != m]
    print(f"relevantes:   {[round(x, 3) for x in relevant]}")
    print(f"irrelevantes: {[round(x, 3) for x in irrelevant]}")
    assert min(relevant) > max(irrelevant), "o modelo não separa o que é do que não é relevante"
