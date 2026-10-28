"""Music tools (ADR-024): ``music_search``.

To play something the LLM calls ``music_search``, picks a result and then calls ``open_url`` with
its ``url`` (``music.youtube.com`` is a trusted domain, so it opens without asking, and the phone
app presses play). The tool is only offered while music is enabled and a provider is usable;
tests (and later the playlist tools) bind a provider with ``bind``.

Search results are data, never instructions (ADR-006), and never feed the subjective memory
layers (ADR-017).
"""

from __future__ import annotations

from typing import Any

from brain.config import settings
from brain.music.base import MusicError, MusicProvider, SearchMode, Track
from brain.music.youtube import YouTubeProvider
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool

_provider: MusicProvider | None = None


def bind(provider: MusicProvider | None) -> None:
    """Point the tools at a provider (``None`` goes back to the one built from the config)."""
    global _provider
    _provider = provider


def provider() -> MusicProvider:
    """The bound provider, or the YouTube one built (once) from the settings."""
    global _provider
    if _provider is None:
        _provider = YouTubeProvider()
    return _provider


def enabled() -> bool:
    if _provider is not None:
        return True
    return settings.music.enabled and settings.youtube_api_key is not None


def failure(error: MusicError) -> ToolResult:
    """A typed failure: only the safe, fixed message reaches the LLM."""
    return ToolResult(success=False, error=error.message, metadata={"code": error.code})


def _line(index: int, track: Track) -> str:
    official = " (oficial)" if track.kind == "official" else ""
    return f"{index}. {track.title} — {track.artist}{official} | {track.url}"


@register_tool
class MusicSearch(Tool):
    name = "music_search"
    description = (
        "Procura músicas no YouTube Music e devolve até 5 resultados com o link de cada um. "
        "Para TOCAR uma música: chame esta ferramenta, escolha o resultado que melhor combina "
        "com o pedido e chame open_url com o link dele (abre no celular e toca). Em 'mode' use "
        "'radio' para tocar algo parecido, em sequência. Pesquise por nome da música e do artista."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "O que procurar: música, artista ou álbum. Ex.: 'Djavan Oceano'.",
            },
            "mode": {
                "type": "string",
                "enum": ["track", "radio"],
                "description": "'track' (padrão) abre a faixa; 'radio' abre uma fila parecida.",
            },
            "limit": {
                "type": "integer",
                "description": "Quantos resultados (1 a 5). Padrão 5.",
            },
        },
        "required": ["query"],
    }
    tier = Tier.READ
    locality = Locality.CLOUD

    @classmethod
    def available(cls) -> bool:
        return enabled()

    async def run(self, **kwargs: Any) -> ToolResult:
        mode: SearchMode = "radio" if kwargs.get("mode") == "radio" else "track"
        limit = kwargs.get("limit", settings.music.search_limit)
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = settings.music.search_limit
        try:
            music = provider()
            tracks = await music.search(str(kwargs.get("query", "")), limit=limit, mode=mode)
        except MusicError as exc:
            return failure(exc)
        if not tracks:
            return ToolResult(
                success=True,
                output="Não achei nenhuma música para essa busca. Tente outro nome ou artista.",
                metadata={"results": 0},
            )
        lines = "\n".join(_line(i, t) for i, t in enumerate(tracks, start=1))
        return ToolResult(
            success=True,
            output=f"{lines}\nPara tocar, chame open_url com o link do resultado escolhido.",
            metadata={"results": len(tracks), "mode": mode},
        )
