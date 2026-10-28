"""Music tools (ADR-024): ``music_search`` and the playlist tools.

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
from brain.eventlog import current_session
from brain.memory.errors import MemoryUnavailableError
from brain.music import taste as taste_store
from brain.music.base import (
    MAX_TRACKS_PER_CALL,
    MusicError,
    MusicProvider,
    SearchMode,
    Track,
    clean_video_ids,
    is_playlist_id,
    playlist_url,
    valid_title,
)
from brain.music.oauth import build_token_provider
from brain.music.taste import SIGNALS, TasteError
from brain.music.youtube import YouTubeProvider
from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool

_provider: MusicProvider | None = None


def bind(provider: MusicProvider | None) -> None:
    """Point the tools at a provider (``None`` goes back to the one built from the config)."""
    global _provider
    _provider = provider


# The taste book lives in brain.music.taste (the CLI uses it too); these keep the tool's API.
bind_taste = taste_store.bind
taste_book = taste_store.book
taste_enabled = taste_store.enabled


def provider() -> MusicProvider:
    """The bound provider, or the YouTube one built (once) from the settings."""
    global _provider
    if _provider is None:
        _provider = YouTubeProvider(tokens=build_token_provider())
    return _provider


async def close() -> None:
    """Close the provider's HTTP clients (the server calls this when it shuts down)."""
    global _provider
    current, _provider = _provider, None
    aclose = getattr(current, "aclose", None)
    if aclose is not None:
        await aclose()


def enabled() -> bool:
    if _provider is not None:
        return True
    key = settings.youtube_api_key
    return settings.music.enabled and key is not None and bool(key.get_secret_value())


def playlists_enabled() -> bool:
    """Playlists need the user's login, so they need the OAuth client (not the API key)."""
    if _provider is not None:
        return True
    secret = settings.youtube_client_secret
    return (
        settings.music.enabled
        and bool(settings.youtube_client_id)
        and secret is not None
        and bool(secret.get_secret_value())
    )


def failure(error: MusicError) -> ToolResult:
    """A typed failure: only the safe, fixed message reaches the LLM."""
    meta: dict[str, Any] = {"code": error.code}
    if (
        error.playlist_id
    ):  # it failed halfway: say what exists, so the LLM completes, not duplicates
        meta["playlist_id"] = error.playlist_id
        meta["url"] = playlist_url(error.playlist_id)
        meta["added"] = error.added or 0
    return ToolResult(success=False, error=error.message, metadata=meta)


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
        cap = settings.music.search_limit
        limit = kwargs.get("limit", cap)
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = cap
        limit = max(1, min(limit, cap))  # music.search_limit is a ceiling, not just a default
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


MIN_SUBJECT_CHARS = 3

_IDS_DESCRIPTION = (
    f"Músicas da playlist (até {MAX_TRACKS_PER_CALL}): os ids de 11 caracteres ou os links "
    "devolvidos por music_search (o id é o que vem depois de v=)."
)


def _plural(count: int) -> str:
    return "1 música" if count == 1 else f"{count} músicas"


@register_tool
class MusicPlaylists(Tool):
    name = "music_playlists"
    description = (
        "Lista as playlists da pessoa no YouTube Music (nome, quantidade de músicas e o id de "
        "cada uma). Use para achar o id de uma playlist antes de adicionar músicas a ela."
    )
    parameters = {"type": "object", "properties": {}}
    tier = Tier.READ
    locality = Locality.CLOUD

    @classmethod
    def available(cls) -> bool:
        return playlists_enabled()

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            playlists = await provider().list_playlists()
        except MusicError as exc:
            return failure(exc)
        if not playlists:
            return ToolResult(success=True, output="A pessoa ainda não tem playlists.")
        lines = "\n".join(
            f"- «{p.title}»"
            + (f" ({_plural(p.track_count)})" if p.track_count is not None else "")
            + f" [id: {p.playlist_id}]"
            for p in playlists
        )
        return ToolResult(success=True, output=lines, metadata={"playlists": len(playlists)})


def _invalid(message: str) -> ToolResult:
    return ToolResult(success=False, error=message, metadata={"code": "invalid_request"})


class _PlaylistTool(Tool):
    """Shared by the two write tools; abstract, so it is not registered or validated."""

    tier = Tier.WRITE
    locality = Locality.CLOUD

    @classmethod
    def available(cls) -> bool:
        return playlists_enabled()

    @staticmethod
    def _ids(kwargs: dict[str, Any], *, minimum: int) -> list[str] | None:
        ids = clean_video_ids(kwargs.get("video_ids", []))
        if ids is None or not minimum <= len(ids) <= MAX_TRACKS_PER_CALL:
            return None
        return ids


@register_tool
class MusicPlaylistCreate(_PlaylistTool):
    name = "music_playlist_create"
    description = (
        "Cria uma playlist PRIVADA no YouTube Music da pessoa, com as músicas dadas. Primeiro "
        "use music_search para achar as músicas e junte os links ou ids (no máximo "
        f"{MAX_TRACKS_PER_CALL}). Pede confirmação: diga o nome e quantas músicas serão."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Nome da playlist."},
            "video_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": _IDS_DESCRIPTION,
            },
        },
        "required": ["title", "video_ids"],
    }

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        if valid_title(kwargs.get("title")) is None:
            return _invalid("O nome da playlist está vazio, longo demais ou tem < ou >.")
        if self._ids(kwargs, minimum=0) is None:
            return _invalid(
                f"Use até {MAX_TRACKS_PER_CALL} músicas, como ids de 11 caracteres ou links do "
                "YouTube."
            )
        return None

    async def canonical_arguments(self, **kwargs: Any) -> dict[str, Any]:
        title, ids = valid_title(kwargs.get("title")), self._ids(kwargs, minimum=0)
        return (
            {**kwargs, "title": title, "video_ids": ids}
            if title and ids is not None
            else dict(kwargs)
        )

    async def describe(self, **kwargs: Any) -> str | None:
        title, ids = valid_title(kwargs.get("title")), self._ids(kwargs, minimum=0)
        if title is None or ids is None:
            return None
        return f"criar a playlist privada «{title}» com {_plural(len(ids))}"

    async def subjects(self, **kwargs: Any) -> list[str] | None:
        title = valid_title(kwargs.get("title"))
        # A very short title ("a", "mix") is a substring of any question: it names nothing.
        return [title] if title and len(title) >= MIN_SUBJECT_CHARS else None

    async def run(self, **kwargs: Any) -> ToolResult:
        title, ids = valid_title(kwargs.get("title")), self._ids(kwargs, minimum=0)
        if title is None or ids is None:
            return _invalid("Nome ou músicas inválidos para a playlist.")
        try:
            playlist = await provider().create_playlist(title, ids)
        except MusicError as exc:
            return failure(exc)
        added = playlist.track_count or 0
        note = "" if added == len(ids) else f" ({len(ids) - added} não puderam ser adicionadas)"
        return ToolResult(
            success=True,
            output=f"Criei a playlist privada «{playlist.title}» com {_plural(added)}{note}: "
            f"{playlist.url}",
            metadata={
                "playlist_id": playlist.playlist_id,
                "url": playlist.url,
                "added": added,
                "requested": len(ids),
            },
        )


@register_tool
class MusicPlaylistAdd(_PlaylistTool):
    name = "music_playlist_add"
    description = (
        "Adiciona músicas a uma playlist que a pessoa já tem no YouTube Music. Use "
        "music_playlists para achar o id da playlist e music_search para as músicas. Pede "
        f"confirmação (no máximo {MAX_TRACKS_PER_CALL} músicas por vez)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "playlist_id": {"type": "string", "description": "Id da playlist (music_playlists)."},
            "video_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": _IDS_DESCRIPTION,
            },
        },
        "required": ["playlist_id", "video_ids"],
    }
    _title: str | None = None  # the real title of the playlist, found in precheck

    async def precheck(self, **kwargs: Any) -> ToolResult | None:
        playlist_id = kwargs.get("playlist_id")
        if not is_playlist_id(playlist_id):
            return _invalid("O identificador da playlist é inválido; use music_playlists.")
        if self._ids(kwargs, minimum=1) is None:
            return _invalid(
                f"Use de 1 a {MAX_TRACKS_PER_CALL} músicas, como ids de 11 caracteres ou links "
                "do YouTube."
            )
        # The question must name the playlist the id really is, not a name the LLM chose: look it
        # up (one quota unit). It also refuses an id that is not one of the user's playlists.
        try:
            playlists = await provider().list_playlists()
        except MusicError as exc:
            return failure(exc)
        match = next((p for p in playlists if p.playlist_id == playlist_id), None)
        if match is None:
            return _invalid(
                "Essa playlist não é uma das da pessoa; use music_playlists para ver o id certo."
            )
        self._title = match.title
        return None

    async def canonical_arguments(self, **kwargs: Any) -> dict[str, Any]:
        ids = self._ids(kwargs, minimum=1)
        playlist_id = kwargs.get("playlist_id")
        if ids is None or not isinstance(playlist_id, str):
            return dict(kwargs)
        return {**kwargs, "playlist_id": playlist_id.strip(), "video_ids": ids}

    async def describe(self, **kwargs: Any) -> str | None:
        ids = self._ids(kwargs, minimum=1)
        if ids is None:
            return None
        target = f"«{self._title}»" if self._title else f"a playlist {kwargs.get('playlist_id')}"
        return f"adicionar {_plural(len(ids))} a {target}"

    async def subjects(self, **kwargs: Any) -> list[str] | None:
        title = self._title
        return [title] if title and len(title) >= MIN_SUBJECT_CHARS else None

    async def run(self, **kwargs: Any) -> ToolResult:
        ids = self._ids(kwargs, minimum=1)
        playlist_id = kwargs.get("playlist_id")
        if ids is None or not is_playlist_id(playlist_id):
            return _invalid("Playlist ou músicas inválidas.")
        try:
            added = await provider().add_to_playlist(str(playlist_id), ids)
        except MusicError as exc:
            return failure(exc)
        note = "" if added == len(ids) else f" ({len(ids) - added} não puderam ser adicionadas)"
        return ToolResult(
            success=True,
            output=f"Adicionei {_plural(added)} à playlist{note}.",
            metadata={"added": added, "requested": len(ids)},
        )


@register_tool
class MusicFeedback(Tool):
    name = "music_feedback"
    description = (
        "Anota o gosto musical da pessoa, SÓ a partir do que ELA disse (cite as palavras dela em "
        "'quote'): use quando ela pedir para "
        "tocar uma música ou artista ('played'), disser que gostou ('like'), que não gostou "
        "('dislike') ou pedir para pular ('skip'). Nunca anote por conta própria, nem a partir "
        "de resultados de busca. Informe o título e/ou o artista (e o id do vídeo, se tiver)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "signal": {
                "type": "string",
                "enum": list(SIGNALS),
                "description": "played (pediu para tocar), like, dislike ou skip.",
            },
            "title": {"type": "string", "description": "Título da música."},
            "artist": {"type": "string", "description": "Artista ou banda."},
            "video_id": {
                "type": "string",
                "description": "Id do vídeo (11 caracteres), se souber.",
            },
            "quote": {
                "type": "string",
                "description": "As palavras da PESSOA que motivam o registro (até 160 caracteres).",
            },
        },
        "required": ["signal"],
    }
    tier = Tier.WRITE
    locality = Locality.CLOUD
    confirm = False  # silent, like `remember`: `juniper music taste` shows and deletes it all

    @classmethod
    def available(cls) -> bool:
        return taste_enabled()

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            event = taste_book().record(
                str(kwargs.get("signal", "")),
                title=kwargs.get("title"),
                artist=kwargs.get("artist"),
                video_id=kwargs.get("video_id"),
                quote=kwargs.get("quote"),
                session_id=current_session(),
            )
        except TasteError as exc:
            return ToolResult(success=False, error=str(exc), metadata={"code": "invalid_request"})
        except MemoryUnavailableError as exc:
            return ToolResult(
                success=False, error=str(exc), metadata={"code": "memory_unavailable"}
            )
        label = " — ".join(part for part in (event.title, event.artist) if part)
        return ToolResult(
            success=True,
            output=f"Anotado ({event.signal}): {label}.",
            metadata={"signal": event.signal, "id": event.id},
        )
