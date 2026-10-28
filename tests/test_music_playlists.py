"""Playlists (T-156, ADR-024): provider calls on the Data API and the three playlist tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from brain.config import settings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.llm.types import ToolCall
from brain.music.base import (
    MAX_TRACKS_PER_CALL,
    MusicError,
    MusicProvider,
    Playlist,
    SearchMode,
    Track,
    clean_video_ids,
    extract_video_id,
    is_playlist_id,
    valid_title,
)
from brain.music.oauth import TokenProvider, TokenStore
from brain.music.quota import COST_READ, COST_WRITE, QuotaTracker
from brain.music.youtube import YouTubeProvider
from brain.tools import music as music_tools
from brain.tools.base import Tier
from brain.tools.registry import ToolRegistry, registry
from tests.test_agent import FakeProvider, call, say

ACCESS = "ya29.token-de-acesso-secreto"
V1, V2, V3 = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"
PL = "PLabcdefghijklmnopqrstuvwxyz012345"


class FakeTokens(TokenProvider):
    """Hands out a fixed token (or raises), counting how often it was asked."""

    def __init__(self, tmp_path: Path, error: MusicError | None = None) -> None:
        super().__init__(TokenStore(tmp_path / "t.json"), None)
        self.error = error
        self.asked = 0

    async def access_token(self) -> str:
        self.asked += 1
        if self.error is not None:
            raise self.error
        return ACCESS


def ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


def err(status: int, reason: str = "") -> httpx.Response:
    return httpx.Response(status, json={"error": {"errors": [{"reason": reason}]}})


class Api:
    """A scripted YouTube: answers by method and path, recording every request."""

    def __init__(self, items: list[httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.item_answers = list(items or [])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/playlists") and request.method == "POST":
            return ok({"id": PL})
        if path.endswith("/playlists"):
            return ok(
                {
                    "items": [
                        {
                            "id": PL,
                            "snippet": {"title": "Foco &amp; Calma"},
                            "contentDetails": {"itemCount": 7},
                        },
                        {"id": "PLsecond0000000", "snippet": {"title": "Treino"}},
                    ]
                }
            )
        assert path.endswith("/playlistItems")
        return self.item_answers.pop(0) if self.item_answers else ok({"id": "item"})

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.content]


def make(
    api: Api, tmp_path: Path, *, limit: int = 5000, tokens: TokenProvider | None | str = "default"
) -> tuple[YouTubeProvider, QuotaTracker, FakeTokens | None]:
    quota = QuotaTracker(tmp_path / "quota.json", limit)
    fake = FakeTokens(tmp_path) if tokens == "default" else None
    used = fake if tokens == "default" else tokens
    provider = YouTubeProvider(
        "chave-da-api",
        quota=quota,
        tokens=used if isinstance(used, TokenProvider) else None,
        transport=httpx.MockTransport(api.handler),
    )
    return provider, quota, fake


# --- helpers on ids and titles -------------------------------------------------------------


class TestHelpers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (V1, V1),
            (f"https://music.youtube.com/watch?v={V1}&list=RDAMVM{V1}", V1),
            (f"https://www.youtube.com/watch?v={V1}", V1),
            (f"https://youtu.be/{V1}", V1),
            (f"  {V1}  ", V1),
            (f"https://evil.example/watch?v={V1}", None),
            (f"javascript:alert('{V1}')", None),
            ("curto", None),
            (None, None),
            (5, None),
        ],
    )
    def test_extract_video_id(self, value: object, expected: str | None) -> None:
        assert extract_video_id(value) == expected

    def test_clean_video_ids_keeps_order_drops_repeats_and_rejects_any_bad_one(self) -> None:
        assert clean_video_ids([V2, f"https://youtu.be/{V1}", V2]) == [V2, V1]
        assert clean_video_ids([V1, "ruim"]) is None
        assert clean_video_ids("não é lista") is None
        assert clean_video_ids([]) == []

    def test_titles(self) -> None:
        assert valid_title("  Foco   Profundo ") == "Foco Profundo"
        for bad in ("", "   ", "a<b", "a>b", "x" * 151, None, 5):
            assert valid_title(bad) is None

    def test_playlist_ids(self) -> None:
        assert is_playlist_id(PL) and not is_playlist_id("curto") and not is_playlist_id(None)
        assert not is_playlist_id("PL/../../etc")


# --- provider: listing ---------------------------------------------------------------------


async def test_listing_uses_the_login_not_the_api_key_and_costs_one_unit(tmp_path: Path) -> None:
    api = Api()
    provider, quota, tokens = make(api, tmp_path)
    playlists = await provider.list_playlists()
    [request] = api.requests
    assert request.headers["authorization"] == f"Bearer {ACCESS}"
    assert "x-goog-api-key" not in request.headers
    assert dict(request.url.params)["mine"] == "true"
    assert playlists == [
        Playlist(PL, "Foco & Calma", 7),
        Playlist("PLsecond0000000", "Treino", None),
    ]
    assert playlists[0].url == f"https://music.youtube.com/playlist?list={PL}"
    assert quota.used() == COST_READ and tokens is not None and tokens.asked == 1


# --- provider: creating --------------------------------------------------------------------


async def test_create_makes_a_private_playlist_then_adds_in_order(tmp_path: Path) -> None:
    api = Api()
    provider, quota, _ = make(api, tmp_path)
    playlist = await provider.create_playlist("  Foco   Profundo ", [V1, f"https://youtu.be/{V2}"])
    assert playlist == Playlist(PL, "Foco Profundo", 2)
    create, first, second = api.requests
    assert create.method == "POST" and create.url.path.endswith("/playlists")
    body = json.loads(create.content)
    assert body["status"] == {"privacyStatus": "private"}  # always private
    assert body["snippet"]["title"] == "Foco Profundo"
    assert first.url.path.endswith("/playlistItems") and second.url.path.endswith("/playlistItems")
    ids = [
        b["snippet"]["resourceId"]["videoId"]
        for b in (json.loads(first.content), json.loads(second.content))
    ]
    assert ids == [V1, V2]
    assert json.loads(first.content)["snippet"]["playlistId"] == PL
    assert quota.used() == COST_WRITE * 3  # the playlist and two tracks


async def test_create_without_tracks_is_allowed(tmp_path: Path) -> None:
    api = Api()
    provider, quota, _ = make(api, tmp_path)
    assert (await provider.create_playlist("Vazia", [])).track_count == 0
    assert len(api.requests) == 1 and quota.used() == COST_WRITE


async def test_the_whole_cost_is_reserved_before_anything_is_created(tmp_path: Path) -> None:
    api = Api()
    provider, quota, tokens = make(api, tmp_path, limit=COST_WRITE * 2)  # room for 1 track only
    with pytest.raises(MusicError) as exc:
        await provider.create_playlist("Foco", [V1, V2])  # needs 150
    assert exc.value.code == "quota_exhausted"
    assert api.requests == [] and quota.used() == 0  # no half-made playlist, nothing counted
    assert tokens is not None and tokens.asked == 1  # the login came first: it is free to check


async def test_an_unavailable_or_duplicate_track_is_skipped_not_fatal(tmp_path: Path) -> None:
    api = Api([err(404, "videoNotFound"), err(409, "videoAlreadyInPlaylist"), ok({"id": "i"})])
    provider, _, _ = make(api, tmp_path)
    playlist = await provider.create_playlist("Foco", [V1, V2, V3])
    assert playlist.track_count == 1 and len(api.requests) == 4


async def test_a_fatal_failure_midway_says_the_playlist_exists(tmp_path: Path) -> None:
    api = Api([ok({"id": "i"}), err(403, "quotaExceeded")])
    provider, quota, _ = make(api, tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.create_playlist("Foco", [V1, V2, V3])
    assert exc.value.code == "quota_exhausted"
    assert "«Foco» foi criada" in exc.value.message
    assert len(api.requests) == 3  # stopped, did not go on to the third track
    assert quota.remaining() == 0  # Google said the quota is gone: stop calling today


@pytest.mark.parametrize(
    ("title", "ids"),
    [
        ("", [V1]),
        ("a<b", [V1]),
        ("x" * 151, [V1]),
        ("Foco", ["ruim"]),
        ("Foco", [f"https://evil.example/watch?v={V1}"]),
        ("Foco", [f"{i:011d}" for i in range(MAX_TRACKS_PER_CALL + 1)]),
    ],
)
async def test_bad_create_input_is_refused_before_any_call_or_cost(
    tmp_path: Path, title: str, ids: list[str]
) -> None:
    api = Api()
    provider, quota, tokens = make(api, tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.create_playlist(title, ids)
    assert exc.value.code == "invalid_request"
    assert api.requests == [] and quota.used() == 0 and tokens is not None and tokens.asked == 0


# --- provider: adding ----------------------------------------------------------------------


async def test_add_counts_what_was_added_and_costs_fifty_each(tmp_path: Path) -> None:
    api = Api([ok({"id": "i"}), err(404, "videoNotFound")])
    provider, quota, _ = make(api, tmp_path)
    assert await provider.add_to_playlist(PL, [V1, V2]) == 1
    assert quota.used() == COST_WRITE * 2
    assert all(r.headers["authorization"] == f"Bearer {ACCESS}" for r in api.requests)


@pytest.mark.parametrize(
    ("playlist", "ids"),
    [("curto", [V1]), (PL, []), (PL, ["ruim"]), (PL, [f"{i:011d}" for i in range(26)])],
)
async def test_bad_add_input_is_refused_without_a_call(
    tmp_path: Path, playlist: str, ids: list[str]
) -> None:
    api = Api()
    provider, quota, _ = make(api, tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.add_to_playlist(playlist, ids)
    assert exc.value.code == "invalid_request" and api.requests == [] and quota.used() == 0


# --- provider: login and API errors --------------------------------------------------------


async def test_without_a_login_provider_playlists_are_not_configured(tmp_path: Path) -> None:
    api = Api()
    provider, quota, _ = make(api, tmp_path, tokens=None)
    with pytest.raises(MusicError) as exc:
        await provider.list_playlists()
    assert exc.value.code == "not_configured" and api.requests == []
    assert quota.used() == 0


@pytest.mark.parametrize("code", ["auth_required", "auth_expired"])
async def test_a_missing_or_expired_login_comes_through_as_is(tmp_path: Path, code: Any) -> None:
    api = Api()
    tokens = FakeTokens(tmp_path, MusicError(code, "Faça o login: rode `juniper music login`."))
    provider, quota, _ = make(api, tmp_path, tokens=tokens)
    for attempt in (
        lambda: provider.add_to_playlist(PL, [V1, V2]),
        lambda: provider.create_playlist("Foco", [V1]),
        lambda: provider.list_playlists(),
    ):
        with pytest.raises(MusicError) as exc:
            await attempt()
        assert exc.value.code == code
    assert api.requests == [] and quota.used() == 0  # no login, no cost


@pytest.mark.parametrize(
    ("response", "code", "fragment"),
    [
        (err(401), "auth_expired", "juniper music login"),
        (err(403, "insufficientPermissions"), "auth_expired", "permissões"),
        (err(403, "youtubeSignupRequired"), "api_error", "canal"),
        (err(403, "playlistContainsMaximumNumberOfVideos"), "api_error", "cheia"),
        (err(404, "playlistNotFound"), "invalid_request", "Não encontrei"),
        (err(500), "api_error", "500"),
    ],
)
async def test_api_errors_on_writes_are_typed_and_never_leak_the_token(
    tmp_path: Path, response: httpx.Response, code: str, fragment: str
) -> None:
    provider, _, _ = make(Api([response]), tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.add_to_playlist(PL, [V1])
    assert exc.value.code == code and fragment in exc.value.message
    assert ACCESS not in exc.value.message


# --- tools ---------------------------------------------------------------------------------


class FakeLists(MusicProvider):
    def __init__(self) -> None:
        self.created: list[tuple[str, list[str]]] = []
        self.added: list[tuple[str, list[str]]] = []
        self.error: MusicError | None = None
        self.partial = False
        self.playlists = [Playlist(PL, "Foco", 7), Playlist("PLother0000000", "Treino", None)]

    async def search(
        self, query: str, *, limit: int = 5, mode: SearchMode = "track"
    ) -> list[Track]:
        return []

    async def list_playlists(self) -> list[Playlist]:
        if self.error:
            raise self.error
        return self.playlists

    async def create_playlist(self, title: str, video_ids: list[str]) -> Playlist:
        if self.error:
            raise self.error
        self.created.append((title, video_ids))
        return Playlist(PL, title, len(video_ids) - (1 if self.partial else 0))

    async def add_to_playlist(self, playlist_id: str, video_ids: list[str]) -> int:
        if self.error:
            raise self.error
        self.added.append((playlist_id, video_ids))
        return len(video_ids) - (1 if self.partial else 0)


@pytest.fixture
def fake() -> Any:
    lists = FakeLists()
    music_tools.bind(lists)
    yield lists
    music_tools.bind(None)


def test_declarations() -> None:
    assert music_tools.MusicPlaylists.tier is Tier.READ
    for tool in (music_tools.MusicPlaylistCreate, music_tools.MusicPlaylistAdd):
        assert tool.tier is Tier.WRITE and tool.confirm is True
    assert music_tools.MusicPlaylistCreate.parameters["required"] == ["title", "video_ids"]
    assert music_tools.MusicPlaylistAdd.parameters["required"] == ["playlist_id", "video_ids"]
    assert "PRIVADA" in music_tools.MusicPlaylistCreate.description


def test_availability_needs_the_oauth_client_not_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.music, "enabled", True)
    monkeypatch.setattr(settings, "youtube_api_key", SecretStr("k"))
    monkeypatch.setattr(settings, "youtube_client_id", None)
    monkeypatch.setattr(settings, "youtube_client_secret", None)
    assert not music_tools.MusicPlaylists.available()  # a key alone does not log anyone in
    monkeypatch.setattr(settings, "youtube_client_id", "id")
    assert not music_tools.MusicPlaylistCreate.available()
    monkeypatch.setattr(settings, "youtube_client_secret", SecretStr("segredo"))
    for tool in (
        music_tools.MusicPlaylists,
        music_tools.MusicPlaylistCreate,
        music_tools.MusicPlaylistAdd,
    ):
        assert tool.available()
    monkeypatch.setattr(settings.music, "enabled", False)
    assert not music_tools.MusicPlaylists.available()


def test_the_tools_are_registered() -> None:
    registry.discover()
    for name in ("music_playlists", "music_playlist_create", "music_playlist_add"):
        assert registry.get(name) is not None


async def test_listing_shows_names_counts_and_ids(fake: FakeLists) -> None:
    result = await music_tools.MusicPlaylists().run()
    assert result.success and result.output is not None
    assert f"- «Foco» (7 músicas) [id: {PL}]" in result.output
    assert "- «Treino» [id: PLother0000000]" in result.output


async def test_create_describes_what_it_will_do_and_runs(fake: FakeLists) -> None:
    tool = music_tools.MusicPlaylistCreate()
    args = {"title": "  Foco   Profundo ", "video_ids": [V1, f"https://youtu.be/{V2}"]}
    assert await tool.precheck(**args) is None
    assert await tool.describe(**args) == "criar a playlist privada «Foco Profundo» com 2 músicas"
    assert await tool.subjects(**args) == ["Foco Profundo"]
    canonical = await tool.canonical_arguments(**args)
    assert canonical == {"title": "Foco Profundo", "video_ids": [V1, V2]}
    result = await tool.run(**canonical)
    assert result.success and fake.created == [("Foco Profundo", [V1, V2])]
    assert f"https://music.youtube.com/playlist?list={PL}" in (result.output or "")
    assert result.metadata == {
        "playlist_id": PL,
        "url": f"https://music.youtube.com/playlist?list={PL}",
        "added": 2,
        "requested": 2,
    }


async def test_a_partial_result_is_reported_honestly(fake: FakeLists) -> None:
    fake.partial = True
    result = await music_tools.MusicPlaylistCreate().run(title="Foco", video_ids=[V1, V2])
    assert result.success and "1 não puderam ser adicionadas" in (result.output or "")
    assert result.metadata is not None and result.metadata["added"] == 1
    added = await music_tools.MusicPlaylistAdd().run(playlist_id=PL, video_ids=[V1, V2])
    assert added.success and "1 não puderam" in (added.output or "")


@pytest.mark.parametrize(
    "args",
    [
        {"title": "", "video_ids": [V1]},
        {"title": "a<b", "video_ids": [V1]},
        {"title": "Foco", "video_ids": ["ruim"]},
        {"title": "Foco", "video_ids": [f"{i:011d}" for i in range(26)]},
    ],
)
async def test_create_refuses_bad_input_before_asking(
    fake: FakeLists, args: dict[str, Any]
) -> None:
    refusal = await music_tools.MusicPlaylistCreate().precheck(**args)
    assert refusal is not None and refusal.metadata == {"code": "invalid_request"}
    assert fake.created == []


async def test_add_names_the_playlist_the_id_really_is(fake: FakeLists) -> None:
    """The question carries the real title (found by id), not a name the LLM chose."""
    tool = music_tools.MusicPlaylistAdd()
    args = {"playlist_id": PL, "video_ids": [V1, f"https://youtu.be/{V2}"]}
    assert await tool.precheck(**args) is None
    assert await tool.describe(**args) == "adicionar 2 músicas a «Foco»"
    assert await tool.subjects(**args) == ["Foco"]
    canonical = await tool.canonical_arguments(**{**args, "playlist_id": f" {PL} "})
    assert canonical == {"playlist_id": PL, "video_ids": [V1, V2]}
    result = await tool.run(**canonical)
    assert result.success and fake.added == [(PL, [V1, V2])]
    assert "playlist_title" not in music_tools.MusicPlaylistAdd.parameters["properties"]


async def test_add_refuses_an_id_that_is_not_one_of_the_users_playlists(fake: FakeLists) -> None:
    tool = music_tools.MusicPlaylistAdd()
    refusal = await tool.precheck(playlist_id="PLoutra0000000000", video_ids=[V1])
    assert refusal is not None and refusal.metadata == {"code": "invalid_request"}
    assert "não é uma das da pessoa" in (refusal.error or "")
    assert await tool.describe(playlist_id="PLoutra0000000000", video_ids=[V1]) == (
        "adicionar 1 música a a playlist PLoutra0000000000"
    )
    assert await tool.subjects(playlist_id="PLoutra0000000000", video_ids=[V1]) is None


async def test_add_without_login_fails_in_precheck_before_asking(fake: FakeLists) -> None:
    fake.error = MusicError("auth_required", "Falta entrar: rode `juniper music login`.")
    refusal = await music_tools.MusicPlaylistAdd().precheck(playlist_id=PL, video_ids=[V1])
    assert refusal is not None and refusal.metadata == {"code": "auth_required"}


async def test_a_very_short_playlist_name_names_nothing_so_it_is_asked(fake: FakeLists) -> None:
    """A title like "a" is a substring of any question: it cannot stand for a "sim" in context."""
    create = music_tools.MusicPlaylistCreate()
    assert await create.subjects(title="a", video_ids=[V1]) is None
    assert await create.subjects(title="mi", video_ids=[V1]) is None
    assert await create.subjects(title="Mix", video_ids=[V1]) == ["Mix"]
    fake.playlists = [Playlist(PL, "A", 1)]
    add = music_tools.MusicPlaylistAdd()
    assert await add.precheck(playlist_id=PL, video_ids=[V1]) is None
    assert await add.subjects(playlist_id=PL, video_ids=[V1]) is None


@pytest.mark.parametrize(
    "args",
    [
        {"playlist_id": "curto", "video_ids": [V1]},
        {"playlist_id": PL, "video_ids": []},
        {"playlist_id": PL, "video_ids": ["ruim"]},
    ],
)
async def test_add_refuses_bad_input_before_asking(fake: FakeLists, args: dict[str, Any]) -> None:
    refusal = await music_tools.MusicPlaylistAdd().precheck(**args)
    assert refusal is not None and refusal.metadata == {"code": "invalid_request"}


@pytest.mark.parametrize("code", ["auth_required", "auth_expired", "quota_exhausted", "api_error"])
async def test_tool_failures_are_typed(fake: FakeLists, code: Any) -> None:
    fake.error = MusicError(code, "Mensagem segura.")
    results = [
        await music_tools.MusicPlaylists().run(),
        await music_tools.MusicPlaylistCreate().run(title="Foco", video_ids=[V1]),
        await music_tools.MusicPlaylistAdd().run(playlist_id=PL, video_ids=[V1]),
    ]
    for result in results:
        assert not result.success and result.error == "Mensagem segura."
        assert result.metadata == {"code": code}


async def test_through_the_dispatcher_creating_asks_but_listing_does_not(fake: FakeLists) -> None:
    asked: list[tuple[str, str | None]] = []

    async def confirm(
        name: str, args: dict[str, Any], tier: Any, summary: str | None = None
    ) -> bool:
        asked.append((name, summary))
        return True

    registry.discover()
    dispatcher = Dispatcher(confirm)
    await dispatcher.dispatch(ToolCall("c1", "music_playlists", {}))
    assert asked == []
    await dispatcher.dispatch(
        ToolCall("c2", "music_playlist_create", {"title": "Foco", "video_ids": [V1]})
    )
    assert asked == [("music_playlist_create", "criar a playlist privada «Foco» com 1 música")]
    assert fake.created == [("Foco", [V1])]


async def test_a_refused_create_is_never_asked(fake: FakeLists) -> None:
    asked: list[str] = []

    async def confirm(
        name: str, args: dict[str, Any], tier: Any, summary: str | None = None
    ) -> bool:
        asked.append(name)
        return True

    registry.discover()
    message = await Dispatcher(confirm).dispatch(
        ToolCall("c1", "music_playlist_create", {"title": "a<b", "video_ids": [V1]})
    )
    assert asked == [] and fake.created == []
    assert (message.content or "").startswith("ERRO: O nome da playlist")


async def test_in_conversation_the_named_playlist_is_created_on_a_yes(fake: FakeLists) -> None:
    """The question names the playlist («Foco»), so the user's "sim" is enough (ADR-010)."""
    reg = ToolRegistry()
    reg.register(music_tools.MusicPlaylistCreate)
    provider = FakeProvider(
        [
            call("music_playlist_create", title="Foco", video_ids=[V1, V2]),
            say("Posso criar a playlist «Foco» com 2 músicas?"),
            say("Criada."),
        ]
    )
    agent = Agent(provider, Dispatcher(conversational=True, tool_registry=reg), tool_registry=reg)
    await agent.run("cria uma playlist Foco com essas duas")
    assert fake.created == []  # asked first, nothing created yet
    await agent.run("sim")
    assert fake.created == [("Foco", [V1, V2])]


# --- halfway failures say what exists --------------------------------------------------------


async def test_a_failure_while_creating_says_which_playlist_exists_and_how_far_it_got(
    tmp_path: Path,
) -> None:
    api = Api([ok({"id": "i"}), err(403, "quotaExceeded")])
    provider, _, _ = make(api, tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.create_playlist("Foco", [V1, V2, V3])
    error = exc.value
    assert error.code == "quota_exhausted"
    assert (error.playlist_id, error.added) == (PL, 1)
    assert PL in error.message and "1 de 3" in error.message
    assert "music_playlist_add" in error.message and "não crie outra" in error.message


async def test_a_network_failure_halfway_also_reports_progress(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/playlists"):
            return ok({"id": PL})
        if calls == 3:
            raise httpx.ConnectError("x")
        return ok({"id": "i"})

    quota = QuotaTracker(tmp_path / "q.json", 5000)
    provider = YouTubeProvider(
        "k", quota=quota, tokens=FakeTokens(tmp_path), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(MusicError) as exc:
        await provider.create_playlist("Foco", [V1, V2, V3])
    assert exc.value.code == "network_error" and (exc.value.playlist_id, exc.value.added) == (PL, 1)


async def test_a_failure_while_adding_says_how_many_went_in(tmp_path: Path) -> None:
    api = Api([ok({"id": "i"}), ok({"id": "i"}), err(500)])
    provider, _, _ = make(api, tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.add_to_playlist(PL, [V1, V2, V3])
    assert exc.value.added == 2 and exc.value.playlist_id == PL
    assert "Adicionei 2 de 3" in exc.value.message


async def test_a_failure_before_any_track_is_a_plain_error(tmp_path: Path) -> None:
    provider, _, _ = make(Api([err(500)]), tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.add_to_playlist(PL, [V1])
    assert exc.value.playlist_id is None and "Adicionei" not in exc.value.message


async def test_the_tool_hands_the_partial_result_to_the_llm(fake: FakeLists) -> None:
    fake.error = MusicError("quota_exhausted", "Parou na faixa 2.", playlist_id=PL, added=1)
    result = await music_tools.MusicPlaylistCreate().run(title="Foco", video_ids=[V1, V2])
    assert not result.success and result.error == "Parou na faixa 2."
    assert result.metadata == {
        "code": "quota_exhausted",
        "playlist_id": PL,
        "url": f"https://music.youtube.com/playlist?list={PL}",
        "added": 1,
    }


# --- listing pages ---------------------------------------------------------------------------


async def test_listing_follows_the_pages(tmp_path: Path) -> None:
    pages: list[dict[str, Any]] = [
        {
            "items": [{"id": "PLfirst00000000", "snippet": {"title": "Um"}}],
            "nextPageToken": "T2",
        },
        {"items": [{"id": "PLsecond0000000", "snippet": {"title": "Dois"}}]},
    ]
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return ok(pages[len(seen) - 1])

    quota = QuotaTracker(tmp_path / "q.json", 5000)
    provider = YouTubeProvider(
        "k", quota=quota, tokens=FakeTokens(tmp_path), transport=httpx.MockTransport(handler)
    )
    assert [p.title for p in await provider.list_playlists()] == ["Um", "Dois"]
    assert "pageToken" not in seen[0] and seen[1]["pageToken"] == "T2"
    assert quota.used() == 2 * COST_READ


# --- ids and titles are cleaned and strict ---------------------------------------------------


def test_a_trailing_newline_never_passes_for_an_id() -> None:
    assert extract_video_id(f"https://music.youtube.com/watch?v={V1}%0A") is None
    assert clean_video_ids([V1, f"https://music.youtube.com/watch?v={V1}%0A"]) is None
    assert not is_playlist_id(PL + "\n")


def test_titles_lose_control_and_direction_characters() -> None:
    assert valid_title("Treino\u202egnp.exe") == "Treinognp.exe"
    assert valid_title("a\x00b\x1bc") == "abc"
    assert valid_title("zero\u200bwidth") == "zerowidth"
    assert valid_title("\u202e\x00") is None  # nothing left
    assert valid_title("duas\n linhas\t aqui") == "duas linhas aqui"
