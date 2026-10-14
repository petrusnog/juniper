"""Local text embeddings for the long-term memory (ADR-011, T-054).

``EmbeddingProvider`` is the interface the rest of the memory code uses; ``LocalEmbeddingProvider``
is the first implementation: a multilingual model (``multilingual-e5-small`` by default) run on
CPU with ONNX Runtime. The model is downloaded on first use and loaded lazily, so importing this
module (and every command that does not touch the memory) costs nothing. Memory text never leaves
the machine to be embedded.

Vectors are L2-normalized, so cosine similarity is a plain dot product (T-055).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import httpx

from brain.config import EmbeddingSettings, settings
from brain.eventlog import event_log
from brain.memory.errors import MemoryUnavailableError

if TYPE_CHECKING:
    import numpy as np

Kind = Literal["passage", "query"]

# The e5 family was trained with these prefixes; without them the vectors are noticeably worse.
_PREFIXES: dict[str, str] = {"passage": "passage: ", "query": "query: "}
_HUB = "https://huggingface.co/{model}/resolve/main/{path}"
_FILES = ("tokenizer.json", "onnx/model.onnx")


class EmbeddingUnavailableError(MemoryUnavailableError):
    """The embedding model cannot be fetched or loaded (no network, missing dependency...)."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors. Vectors of different ``model_id`` are not comparable."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, text: str, kind: Kind = "passage") -> list[float]: ...

    def embed_batch(self, texts: Sequence[str], kind: Kind = "passage") -> list[list[float]]: ...


class LocalEmbeddingProvider:
    """ONNX model on CPU. ``kind`` is ``passage`` for stored memories, ``query`` for searches."""

    def __init__(self, config: EmbeddingSettings | None = None) -> None:
        self.config = config if config is not None else settings.memory.embeddings
        self._session: Any = None
        self._tokenizer: Any = None
        self._input_names: set[str] = set()
        self._dimension: int | None = None
        self._lock = threading.Lock()
        # Timing of the last call, for whoever wants to say where the time went (T-062):
        # ``last_load_ms`` is the one-off model load (0 once loaded), ``last_infer_ms`` the work.
        self.last_load_ms = 0.0
        self.last_infer_ms = 0.0

    # --- interface -----------------------------------------------------

    @property
    def model_id(self) -> str:
        return self.config.model

    @property
    def dimension(self) -> int:
        """Vector size. Loads the model if it is not loaded yet."""
        if self._dimension is None:
            self._load()
            assert self._dimension is not None
        return self._dimension

    def embed(self, text: str, kind: Kind = "passage") -> list[float]:
        return self.embed_batch([text], kind)[0]

    def embed_batch(self, texts: Sequence[str], kind: Kind = "passage") -> list[list[float]]:
        if kind not in _PREFIXES:
            raise ValueError(f"kind inválido: {kind!r} (use 'passage' ou 'query')")
        if not texts:
            return []
        self.last_load_ms = 0.0
        self._load()
        prefix = _PREFIXES[kind]
        vectors: list[list[float]] = []
        size = self.config.batch_size
        started = time.perf_counter()
        for start in range(0, len(texts), size):
            chunk = [prefix + t for t in texts[start : start + size]]
            vectors.extend(v.tolist() for v in self._run(chunk))
        self.last_infer_ms = (time.perf_counter() - started) * 1000
        event_log.log(
            "embedding",
            kind=kind,
            texts=len(texts),
            chars=sum(len(t) for t in texts),
            infer_ms=round(self.last_infer_ms),
            load_ms=round(self.last_load_ms),
            threads=self.config.threads or "auto",
        )
        return vectors

    # --- model ---------------------------------------------------------

    @property
    def model_dir(self) -> Path:
        return self.config.dir / self.config.model.replace("/", "--")

    def is_downloaded(self) -> bool:
        return all((self.model_dir / f).is_file() for f in _FILES)

    def _load(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            load_started = time.perf_counter()
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer
            except ImportError as exc:
                raise EmbeddingUnavailableError(
                    "Faltam dependências dos embeddings (onnxruntime, tokenizers, numpy). "
                    "Rode `pip install -e .` na pasta da Juniper."
                ) from exc
            download_started = time.perf_counter()
            self._download()
            download_ms = (time.perf_counter() - download_started) * 1000
            try:
                tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
                tokenizer.enable_truncation(max_length=self.config.max_length)
                tokenizer.enable_padding()
                options = ort.SessionOptions()
                if self.config.threads:
                    options.intra_op_num_threads = self.config.threads
                session = ort.InferenceSession(
                    str(self.model_dir / "onnx" / "model.onnx"),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
            except Exception as exc:
                raise EmbeddingUnavailableError(
                    f"Não consegui carregar o modelo de embeddings {self.model_id} em "
                    f"{self.model_dir}: {exc}. Apague essa pasta para baixá-lo de novo."
                ) from exc
            self._tokenizer = tokenizer
            self._input_names = {i.name for i in session.get_inputs()}
            self._session = session
            self._dimension = int(self._probe_dimension())
            self.last_load_ms = (time.perf_counter() - load_started) * 1000
            event_log.log(
                "embedding_model_load",
                model=self.model_id,
                total_ms=round(self.last_load_ms),
                download_ms=round(download_ms),
                threads=self.config.threads or "auto",
            )

    def _probe_dimension(self) -> int:
        return len(self._run(["passage: dimensão"])[0])

    def _download(self) -> None:
        for name in _FILES:
            target = self.model_dir / name
            if target.is_file():
                continue
            url = _HUB.format(model=self.config.model, path=name)
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            try:
                with httpx.stream(
                    "GET",
                    url,
                    follow_redirects=True,
                    timeout=self.config.download_timeout,
                ) as response:
                    response.raise_for_status()
                    with partial.open("wb") as fh:
                        for block in response.iter_bytes():
                            fh.write(block)
                partial.replace(target)  # atomic: a cut download never looks complete
            except (httpx.HTTPError, OSError) as exc:
                partial.unlink(missing_ok=True)
                raise EmbeddingUnavailableError(
                    f"Não consegui baixar o modelo de embeddings {self.model_id} ({name}): "
                    f"{exc}. Confira a conexão e tente de novo; ou copie o modelo para "
                    f"{self.model_dir}."
                ) from exc

    def _run(self, texts: list[str]) -> list[np.ndarray[Any, Any]]:
        import numpy as np

        encodings = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        inputs = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            inputs["token_type_ids"] = np.zeros_like(ids)
        inputs = {k: v for k, v in inputs.items() if k in self._input_names}
        hidden = self._session.run(None, inputs)[0]  # (batch, tokens, dim)
        return list(_mean_pool_normalize(hidden, mask))


def _mean_pool_normalize(hidden: Any, mask: Any) -> Any:
    """Average the token vectors that are not padding, then scale each to unit length."""
    import numpy as np

    weights = mask[:, :, None].astype(hidden.dtype)
    pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return pooled / np.clip(norms, 1e-12, None)


def open_embedding_provider() -> EmbeddingProvider:
    """The configured provider (always the local one for now)."""
    return LocalEmbeddingProvider()
