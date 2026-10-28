"""Music (T-154, ADR-024): quota counter, YouTube search provider and the `music_search` tool."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from brain.config import MusicSettings, Settings, settings
from brain.core.dispatcher import Dispatcher
from brain.llm.types import ToolCall
from brain.music.base import (
    MusicError,
    MusicProvider,
    SearchMode,
    Track,
    is_video_id,
    radio_url,
    track_url,
)
from brain.music.quota import COST_SEARCH, QuotaTracker
from brain.music.youtube import YouTubeProvider, clean_title, rank
from brain.tools import music as music_tools
from brain.tools.registry import registry

KEY = "AIza-chave-secreta-de-teste"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


class Clock:
    def __init__(self, now: str = "2026-10-27T18:00:00") -> None:
        self.now = at(now)

    def __call__(self) -> datetime:
        return self.now


def item(video_id: str, title: str, channel: str) -> dict[str, Any]:
    return {"id": {"videoId": video_id}, "snippet": {"title": title, "channelTitle": channel}}


ITEMS = [
    item("aaaaaaaaaaa", "Djavan - Oceano (Ao Vivo)", "Djavan"),
    item("bbbbbbbbbbb", "Oceano", "Djavan - Topic"),
    item("ccccccccccc", "Oceano &amp; Mar &#39;69", "Canal Qualquer"),
    item("bbbbbbbbbbb", "Oceano (duplicado)", "Djavan - Topic"),
    item("ddddddddddd", "Oceano (cover)", "Fulano"),
]


# --- quota --------------------------------------------------------------------------------


class TestQuota:
    def test_reserve_counts_and_persists(self, tmp_path: Path) -> None:
        path = tmp_path / "music" / "quota.json"
        quota = QuotaTracker(path, 300, clock=Clock())
        quota.reserve(100)
        quota.reserve(100)
        assert quota.used() == 200 and quota.remaining() == 100
        assert QuotaTracker(path, 300, clock=Clock()).used() == 200  # survives a restart

    def test_it_refuses_before_going_past_the_limit_and_does_not_count_the_refusal(
        self, tmp_path: Path
    ) -> None:
        quota = QuotaTracker(tmp_path / "q.json", 250, clock=Clock())
        quota.reserve(100)
        quota.reserve(100)
        with pytest.raises(MusicError) as exc:
            quota.reserve(100)
        assert exc.value.code == "quota_exhausted"
        assert quota.used() == 200

    def test_the_count_resets_at_midnight_pacific(self, tmp_path: Path) -> None:
        clock = Clock("2026-10-28T06:59:00")  # 23:59 PDT on the 27th
        quota = QuotaTracker(tmp_path / "q.json", 200, clock=clock)
        quota.reserve(200)
        assert quota.remaining() == 0
        clock.now = at("2026-10-28T07:00:00")  # 00:00 PDT on the 28th
        assert quota.remaining() == 200 and quota.used() == 0

    def test_resets_in_counts_down_to_the_pacific_midnight(self, tmp_path: Path) -> None:
        quota = QuotaTracker(tmp_path / "q.json", 100, clock=Clock("2026-10-28T04:30:00"))
        assert quota.resets_in().total_seconds() == 2.5 * 3600  # 21:30 PDT -> 2h30 left
        assert "2h30" in quota.exhausted_message()

    @pytest.mark.parametrize("content", ["", "{", "[]", '{"day": 5}', '{"day":"x","used":"y"}'])
    def test_a_corrupt_file_starts_the_day_at_zero(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / "q.json"
        path.write_text(content)
        quota = QuotaTracker(path, 100, clock=Clock())
        assert quota.used() == 0
        quota.reserve(50)
        assert json.loads(path.read_text())["used"] == 50

    def test_the_file_is_private(self, tmp_path: Path) -> None:
        path = tmp_path / "q.json"
        QuotaTracker(path, 100, clock=Clock()).reserve(10)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_mark_exhausted_blocks_the_rest_of_the_day(self, tmp_path: Path) -> None:
        quota = QuotaTracker(tmp_path / "q.json", 500, clock=Clock())
        quota.reserve(100)
        quota.mark_exhausted()
        with pytest.raises(MusicError):
            quota.reserve(1)


# --- ranking ------------------------------------------------------------------------------


class TestRanking:
    def test_official_topic_audio_comes_first(self) -> None:
        tracks = rank(ITEMS, "Djavan Oceano", 5, "track")
        assert tracks[0].video_id == "bbbbbbbbbbb"
        assert tracks[0].kind == "official" and tracks[0].artist == "Djavan"

    def test_unwanted_versions_go_down_unless_asked_for(self) -> None:
        ids = [t.video_id for t in rank(ITEMS, "Djavan Oceano", 5, "track")]
        assert ids.index("aaaaaaaaaaa") > ids.index("ccccccccccc")  # "Ao Vivo"
        assert ids.index("ddddddddddd") > ids.index("ccccccccccc")  # "cover"
        asked = [t.video_id for t in rank(ITEMS, "Djavan Oceano ao vivo", 5, "track")]
        assert asked.index("aaaaaaaaaaa") < asked.index("ddddddddddd")

    def test_duplicates_are_dropped_and_the_limit_applies(self) -> None:
        tracks = rank(ITEMS, "oceano", 2, "track")
        assert len(tracks) == 2 and len({t.video_id for t in tracks}) == 2

    def test_html_in_titles_is_decoded(self) -> None:
        titles = {t.title for t in rank(ITEMS, "oceano", 5, "track")}
        assert "Oceano & Mar '69" in titles
        assert clean_title("  a &amp;   b\n") == "a & b"

    def test_radio_mode_builds_the_mix_link(self) -> None:
        [first] = rank(ITEMS, "oceano", 1, "radio")
        assert first.url == "https://music.youtube.com/watch?v=bbbbbbbbbbb&list=RDAMVMbbbbbbbbbbb"

    def test_malformed_items_are_skipped(self) -> None:
        raw: list[dict[str, Any]] = [
            {},
            {"id": {}, "snippet": {"title": "x"}},
            {"id": {"videoId": "eeeeeeeeeee"}, "snippet": {}},
            item("fffffffffff", "Boa", "Canal"),
        ]
        assert [t.video_id for t in rank(raw, "x", 5, "track")] == ["fffffffffff"]

    def test_video_id_shape_and_links(self) -> None:
        assert is_video_id("dQw4w9WgXcQ") and not is_video_id("curto") and not is_video_id(None)
        assert track_url("dQw4w9WgXcQ") == "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        assert radio_url("dQw4w9WgXcQ").endswith("&list=RDAMVMdQw4w9WgXcQ")


# --- YouTube provider ---------------------------------------------------------------------


def make_provider(
    handler: Any, tmp_path: Path, *, key: str | None = KEY, limit: int = 1000
) -> tuple[YouTubeProvider, QuotaTracker]:
    quota = QuotaTracker(tmp_path / "quota.json", limit, clock=Clock())
    provider = YouTubeProvider(
        key, quota=quota, region="BR", transport=httpx.MockTransport(handler)
    )
    return provider, quota


def ok(items: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"items": items})


def error(status: int, reason: str = "") -> httpx.Response:
    return httpx.Response(status, json={"error": {"errors": [{"reason": reason}]}})


async def test_search_calls_the_music_category_with_the_key_in_a_header(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(ITEMS)

    provider, quota = make_provider(handler, tmp_path)
    tracks = await provider.search("  Djavan   Oceano ", limit=3)
    [request] = seen
    assert request.url.path == "/youtube/v3/search"
    params = dict(request.url.params)
    assert params["videoCategoryId"] == "10" and params["type"] == "video"
    assert params["q"] == "Djavan Oceano" and params["regionCode"] == "BR"
    assert request.headers["x-goog-api-key"] == KEY
    assert KEY not in str(request.url)  # never in a URL: URLs get logged
    assert [t.video_id for t in tracks][0] == "bbbbbbbbbbb" and len(tracks) == 3
    assert quota.used() == COST_SEARCH


async def test_limit_is_clamped_to_one_through_five(tmp_path: Path) -> None:
    provider, _ = make_provider(lambda r: ok(ITEMS), tmp_path)
    assert len(await provider.search("x", limit=0)) == 1
    assert len(await provider.search("x", limit=99)) == 4  # only 4 distinct ids in ITEMS


@pytest.mark.parametrize("query", ["", "   ", "x" * 121])
async def test_bad_queries_are_refused_without_a_call_or_a_cost(tmp_path: Path, query: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar a API")

    provider, quota = make_provider(handler, tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.search(query)
    assert exc.value.code == "invalid_request" and quota.used() == 0


async def test_without_a_key_nothing_is_called(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar a API")

    provider, quota = make_provider(handler, tmp_path, key=None)
    with pytest.raises(MusicError) as exc:
        await provider.search("oceano")
    assert exc.value.code == "not_configured" and quota.used() == 0


async def test_the_key_is_read_lazily_from_the_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", SecretStr(KEY))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["x-goog-api-key"])
        return ok(ITEMS)

    provider, _ = make_provider(handler, tmp_path, key=None)
    await provider.search("oceano")
    assert seen == [KEY]


async def test_an_exhausted_quota_never_reaches_the_api(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar a API")

    provider, quota = make_provider(handler, tmp_path, limit=150)
    quota.reserve(100)
    with pytest.raises(MusicError) as exc:
        await provider.search("oceano")
    assert exc.value.code == "quota_exhausted"
    assert "cota diária" in exc.value.message and "Pacífico" in exc.value.message


async def test_a_quota_error_from_google_blocks_further_calls(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return error(403, "quotaExceeded")

    provider, _ = make_provider(handler, tmp_path)
    for _ in range(2):
        with pytest.raises(MusicError) as exc:
            await provider.search("oceano")
        assert exc.value.code == "quota_exhausted"
    assert calls == 1  # the second one was refused locally


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (error(400, "keyInvalid"), "not_configured"),
        (error(403, "ipRefererBlocked"), "not_configured"),
        (error(403, "forbidden"), "auth_required"),
        (error(401), "auth_required"),
        (error(400, "badRequest"), "invalid_request"),
        (error(500), "api_error"),
        (httpx.Response(200, text="não é json"), "api_error"),
    ],
)
async def test_api_failures_are_typed_and_never_leak_the_key(
    tmp_path: Path, response: httpx.Response, code: str
) -> None:
    provider, _ = make_provider(lambda r: response, tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.search("oceano")
    assert exc.value.code == code
    assert KEY not in exc.value.message and "googleapis" not in exc.value.message


@pytest.mark.parametrize("failure", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
async def test_network_failures_are_typed(tmp_path: Path, failure: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    provider, _ = make_provider(handler, tmp_path)
    with pytest.raises(MusicError) as exc:
        await provider.search("oceano")
    assert exc.value.code == "network_error"


async def test_no_items_is_an_empty_list(tmp_path: Path) -> None:
    provider, _ = make_provider(lambda r: ok([]), tmp_path)
    assert await provider.search("zzzz") == []


# --- the tool -----------------------------------------------------------------------------


class FakeMusic(MusicProvider):
    def __init__(self, tracks: list[Track] | MusicError) -> None:
        self.tracks = tracks
        self.calls: list[tuple[str, int, SearchMode]] = []

    async def search(
        self, query: str, *, limit: int = 5, mode: SearchMode = "track"
    ) -> list[Track]:
        self.calls.append((query, limit, mode))
        if isinstance(self.tracks, MusicError):
            raise self.tracks
        return self.tracks


TRACK = Track("bbbbbbbbbbb", "Oceano", "Djavan", track_url("bbbbbbbbbbb"), "official")


@pytest.fixture(autouse=True)
def unbind() -> Any:
    yield
    music_tools.bind(None)


def test_the_tool_is_hidden_until_music_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.music, "enabled", False)
    monkeypatch.setattr(settings, "youtube_api_key", None)
    assert not music_tools.MusicSearch.available()
    monkeypatch.setattr(settings.music, "enabled", True)
    assert not music_tools.MusicSearch.available()  # enabled, but no key
    monkeypatch.setattr(settings, "youtube_api_key", SecretStr(KEY))
    assert music_tools.MusicSearch.available()
    monkeypatch.setattr(settings.music, "enabled", False)
    assert not music_tools.MusicSearch.available()


def test_a_bound_provider_makes_it_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.music, "enabled", False)
    music_tools.bind(FakeMusic([TRACK]))
    assert music_tools.MusicSearch.available()


def test_it_is_registered_and_offered_only_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    registry.discover()
    assert registry.get("music_search") is music_tools.MusicSearch
    monkeypatch.setattr(settings.music, "enabled", False)
    monkeypatch.setattr(settings, "youtube_api_key", None)
    assert "music_search" not in {s["function"]["name"] for s in registry.schemas()}
    music_tools.bind(FakeMusic([TRACK]))
    assert "music_search" in {s["function"]["name"] for s in registry.schemas()}


def test_declaration() -> None:
    from brain.tools.base import Locality, Tier

    tool = music_tools.MusicSearch
    assert tool.tier is Tier.READ and tool.locality is Locality.CLOUD
    assert tool.parameters["required"] == ["query"]
    assert tool.parameters["properties"]["mode"]["enum"] == ["track", "radio"]
    assert "open_url" in tool.description


async def test_run_lists_results_with_links_and_points_to_open_url() -> None:
    fake = FakeMusic(
        [TRACK, Track("ccccccccccc", "Oceano ao vivo", "Fulano", track_url("ccccccccccc"))]
    )
    music_tools.bind(fake)
    result = await music_tools.MusicSearch().run(query="Djavan Oceano")
    assert result.success and result.metadata == {"results": 2, "mode": "track"}
    assert result.output is not None
    assert (
        "1. Oceano — Djavan (oficial) | https://music.youtube.com/watch?v=bbbbbbbbbbb"
        in result.output
    )
    assert (
        "2. Oceano ao vivo — Fulano | https://music.youtube.com/watch?v=ccccccccccc"
        in result.output
    )
    assert "open_url" in result.output
    assert fake.calls == [("Djavan Oceano", 5, "track")]


async def test_run_passes_mode_and_a_sane_limit() -> None:
    fake = FakeMusic([TRACK])
    music_tools.bind(fake)
    await music_tools.MusicSearch().run(query="x", mode="radio", limit=3)
    await music_tools.MusicSearch().run(query="x", mode="qualquer", limit="muitos")
    await music_tools.MusicSearch().run(query="x", limit=True)
    assert fake.calls == [("x", 3, "radio"), ("x", 5, "track"), ("x", 5, "track")]


async def test_run_without_results_is_a_successful_answer() -> None:
    music_tools.bind(FakeMusic([]))
    result = await music_tools.MusicSearch().run(query="zzzz")
    assert result.success and "Não achei" in (result.output or "")


@pytest.mark.parametrize(
    "code", ["quota_exhausted", "not_configured", "api_error", "network_error", "invalid_request"]
)
async def test_run_turns_music_errors_into_typed_results(code: Any) -> None:
    music_tools.bind(FakeMusic(MusicError(code, "Mensagem segura em PT-BR.")))
    result = await music_tools.MusicSearch().run(query="x")
    assert not result.success
    assert result.error == "Mensagem segura em PT-BR." and result.metadata == {"code": code}


async def test_through_the_dispatcher_a_read_tool_runs_without_asking() -> None:
    music_tools.bind(FakeMusic([TRACK]))
    asked: list[str] = []

    async def confirm(
        name: str, args: dict[str, Any], tier: Any, summary: str | None = None
    ) -> bool:
        asked.append(name)
        return False

    registry.discover()
    message = await Dispatcher(confirm).dispatch(
        ToolCall("c1", "music_search", {"query": "Djavan Oceano"})
    )
    assert asked == [] and "Oceano — Djavan" in (message.content or "")


# --- configuration ------------------------------------------------------------------------


def test_music_defaults_are_conservative() -> None:
    cfg = MusicSettings()
    assert cfg.enabled is False and cfg.daily_quota_limit == 9000
    assert cfg.search_limit == 5 and cfg.region == "BR"
    assert cfg.quota_file == Path.home() / ".juniper" / "music" / "quota.json"


@pytest.mark.parametrize(
    "kwargs",
    [{"daily_quota_limit": 10}, {"search_limit": 0}, {"search_limit": 6}, {"region": "BRA"}],
)
def test_music_settings_reject_bad_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        MusicSettings(**kwargs)


def test_region_is_upper_cased_and_the_quota_file_expands_home() -> None:
    cfg = MusicSettings(region="br", quota_file=Path("~/x/q.json"))
    assert cfg.region == "BR" and cfg.quota_file == Path.home() / "x" / "q.json"


def test_the_api_key_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JUNIPER_YOUTUBE_API_KEY", KEY)
    monkeypatch.setenv("JUNIPER_MUSIC__ENABLED", "true")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.youtube_api_key is not None and s.youtube_api_key.get_secret_value() == KEY
    assert s.music.enabled is True
    assert KEY not in repr(s.youtube_api_key)  # SecretStr keeps it out of reprs and logs
