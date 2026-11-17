"""Local embeddings through Ollama (default model ``bge-m3``, 1024 dimensions, multilingual).

No tokens are spent and nothing leaves the machine. The model is configurable by environment so
a different Ollama model can be tried; the dimension is checked against the database schema.
"""

from __future__ import annotations

import os

import httpx

MODEL = os.environ.get("KNOWLEDGE_EMBED_MODEL", "bge-m3")
OLLAMA_URL = os.environ.get("KNOWLEDGE_OLLAMA_URL", "http://127.0.0.1:11434")
DIMENSIONS = 1024
BATCH = 16
MAX_CHARS = 6000  # a section longer than this is embedded by its head (the full text is stored)


class EmbeddingError(RuntimeError):
    pass


def embed(texts: list[str]) -> list[list[float]]:
    """One vector per text. Raises ``EmbeddingError`` (never a raw HTTP error) on failure."""
    out: list[list[float]] = []
    try:
        with httpx.Client(timeout=300) as client:
            for start in range(0, len(texts), BATCH):
                batch = [t[:MAX_CHARS] for t in texts[start : start + BATCH]]
                resp = client.post(
                    f"{OLLAMA_URL}/api/embed", json={"model": MODEL, "input": batch}
                )
                if resp.status_code == 404:
                    raise EmbeddingError(f"modelo '{MODEL}' ausente: rode `ollama pull {MODEL}`")
                resp.raise_for_status()
                out.extend(resp.json()["embeddings"])
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"Ollama indisponível em {OLLAMA_URL}: {exc}") from exc
    if out and len(out[0]) != DIMENSIONS:
        raise EmbeddingError(f"o modelo devolve {len(out[0])} dimensões; o schema espera {DIMENSIONS}")
    return out


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
