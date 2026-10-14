"""T-054: EmbeddingProvider local e reindexação. Sem rede e sem baixar o modelo real."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from brain.config import EmbeddingSettings
from brain.memory import embeddings as emb
from brain.memory.embeddings import (
    EmbeddingProvider,
    EmbeddingUnavailableError,
    LocalEmbeddingProvider,
    _mean_pool_normalize,
)
from brain.memory.errors import MemoryUnavailableError
from brain.memory.reindex import reindex_memories


class _Enc:
    def __init__(self, ids: list[int], mask: list[int]) -> None:
        self.ids, self.attention_mask = ids, mask


class _FakeTokenizer:
    """One token per character, padded to the longest text in the batch."""

    def encode_batch(self, texts: list[str]) -> list[_Enc]:
        longest = max(len(t) for t in texts)
        return [
            _Enc(
                [ord(c) for c in t] + [0] * (longest - len(t)),
                [1] * len(t) + [0] * (longest - len(t)),
            )
            for t in texts
        ]


class _FakeSession:
    seen: list[dict[str, Any]]

    def __init__(self) -> None:
        self.seen = []

    def run(self, _out: Any, inputs: dict[str, Any]) -> list[Any]:
        self.seen.append(inputs)
        ids = inputs["input_ids"]
        hidden = np.stack([ids, ids * 2, np.ones_like(ids)], axis=-1).astype(np.float32)
        return [hidden]


def _provider(tmp_path: Path, batch_size: int = 2) -> tuple[LocalEmbeddingProvider, _FakeSession]:
    p = LocalEmbeddingProvider(EmbeddingSettings(dir=tmp_path, batch_size=batch_size))
    session = _FakeSession()
    p._tokenizer = _FakeTokenizer()
    p._session = session
    p._input_names = {"input_ids", "attention_mask"}
    p._dimension = 3
    return p, session


def test_satisfies_interface(tmp_path: Path) -> None:
    p, _ = _provider(tmp_path)
    assert isinstance(p, EmbeddingProvider)
    assert p.model_id == "intfloat/multilingual-e5-small"
    assert p.dimension == 3


def test_vectors_are_unit_length(tmp_path: Path) -> None:
    p, _ = _provider(tmp_path)
    vec = p.embed("Prefere ZSH.")
    assert len(vec) == 3
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-5)


def test_padding_does_not_change_the_vector(tmp_path: Path) -> None:
    p, _ = _provider(tmp_path)
    alone = p.embed("abc")
    batched = p.embed_batch(["abc", "um texto bem mais longo"])[0]
    assert alone == pytest.approx(batched, abs=1e-6)


def test_batch_keeps_order_and_splits_by_batch_size(tmp_path: Path) -> None:
    p, session = _provider(tmp_path, batch_size=2)
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    out = p.embed_batch(texts)
    assert len(out) == 5
    assert len(session.seen) == 3  # 2 + 2 + 1
    assert out[2] == p.embed("ccc")


def test_empty_batch_does_not_load_the_model(tmp_path: Path) -> None:
    p = LocalEmbeddingProvider(EmbeddingSettings(dir=tmp_path))
    assert p.embed_batch([]) == []
    assert p._session is None


def test_e5_prefixes(tmp_path: Path) -> None:
    p, session = _provider(tmp_path)
    p.embed("oi", "query")
    p.embed("oi", "passage")
    assert chr(session.seen[0]["input_ids"][0][0]) == "q"
    assert chr(session.seen[1]["input_ids"][0][0]) == "p"
    with pytest.raises(ValueError):
        p.embed("oi", "outro")  # type: ignore[arg-type]


def test_pooling_ignores_masked_tokens() -> None:
    hidden = np.array([[[3.0, 0.0], [0.0, 4.0], [100.0, 100.0]]], dtype=np.float32)
    out = _mean_pool_normalize(hidden, np.array([[1, 1, 0]]))
    assert out[0] == pytest.approx([0.6, 0.8], abs=1e-6)


def test_download_failure_is_friendly_and_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    def boom(*_a: Any, **_k: Any) -> Any:
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(emb.httpx, "stream", boom)
    p = LocalEmbeddingProvider(EmbeddingSettings(dir=tmp_path))
    with pytest.raises(EmbeddingUnavailableError, match="baixar o modelo"):
        p.embed("oi")
    assert issubclass(EmbeddingUnavailableError, MemoryUnavailableError)
    assert not list(tmp_path.rglob("*.part"))
    assert not p.is_downloaded()


def test_corrupt_model_is_reported(tmp_path: Path) -> None:
    p = LocalEmbeddingProvider(EmbeddingSettings(dir=tmp_path))
    (p.model_dir / "onnx").mkdir(parents=True)
    (p.model_dir / "tokenizer.json").write_text("{}")
    (p.model_dir / "onnx" / "model.onnx").write_bytes(b"lixo")
    assert p.is_downloaded()
    with pytest.raises(EmbeddingUnavailableError, match="carregar"):
        p.embed("oi")


# --- reindex ----------------------------------------------------------


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, _flt: Any, _proj: Any = None) -> list[dict[str, Any]]:
        return [dict(d) for d in self.docs]

    def update_one(self, flt: dict[str, Any], update: dict[str, Any]) -> None:
        for d in self.docs:
            if d["_id"] == flt["_id"]:
                d.update(update["$set"])


class _StubProvider:
    model_id = "novo"
    dimension = 2

    def embed(self, text: str, kind: str = "passage") -> list[float]:
        return [float(len(text)), 0.0]

    def embed_batch(self, texts: Any, kind: str = "passage") -> list[list[float]]:
        return [self.embed(t) for t in texts]


def test_reindex_only_rewrites_other_models() -> None:
    col = _FakeCollection(
        [
            {"_id": 1, "text": "um", "embedding_model": "velho", "embedding": [9]},
            {"_id": 2, "text": "dois", "embedding_model": "novo", "embedding": [7]},
            {"_id": 3, "text": "três", "embedding_model": "velho", "status": "superseded"},
            {"_id": 4, "text": "  "},
        ]
    )
    steps: list[tuple[int, int]] = []
    result = reindex_memories(
        col, _StubProvider(), batch_size=1, progress=lambda d, t: steps.append((d, t))
    )
    assert (result.total, result.reindexed, result.skipped) == (4, 2, 2)
    assert col.docs[0]["embedding"] == [2.0, 0.0] and col.docs[0]["embedding_model"] == "novo"
    assert col.docs[1]["embedding"] == [7]  # untouched
    assert col.docs[2]["embedding_model"] == "novo"
    assert steps == [(1, 2), (2, 2)]


def test_reindex_force_and_idempotent() -> None:
    col = _FakeCollection([{"_id": 1, "text": "a", "embedding_model": "novo"}])
    assert reindex_memories(col, _StubProvider()).reindexed == 0
    assert reindex_memories(col, _StubProvider(), force=True).reindexed == 1


def test_cli_reindex_reports_unavailable_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.clients import cli
    from brain.memory import mongo

    def down(self: Any) -> None:
        raise MemoryUnavailableError("banco fora do ar")

    monkeypatch.setattr(mongo.MongoConnection, "check", down)
    result = CliRunner().invoke(cli.app, ["memory", "reindex"])
    assert result.exit_code == 1
    assert "banco fora do ar" in result.output


@pytest.mark.integration
def test_real_model_ranks_by_meaning(tmp_path: Path) -> None:
    """Baixa o modelo (~120 MB): rode com `pytest -m integration`."""
    p = LocalEmbeddingProvider(EmbeddingSettings(dir=tmp_path))
    assert p.dimension == 384
    docs = p.embed_batch(["O projeto X usa Laravel 11.", "Prefere ZSH como shell."])
    query = p.embed("qual framework PHP o projeto usa?", "query")
    scores = [sum(a * b for a, b in zip(query, d, strict=True)) for d in docs]
    assert scores[0] > scores[1]
