"""Google login for playlists (T-155, ADR-024): device-code flow, token file, renewal, CLI."""

from __future__ import annotations

import asyncio
import json
import stat
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from brain.clients import cli
from brain.config import settings
from brain.music import oauth
from brain.music.base import MusicError
from brain.music.oauth import (
    DeviceCode,
    GoogleOAuth,
    StoredToken,
    TokenProvider,
    TokenStore,
)

CLIENT_ID = "123-abc.apps.googleusercontent.com"
SECRET = "GOCSPX-segredo-do-cliente"
ACCESS = "ya29.token-de-acesso"
REFRESH = "1//refresh-token-secreto"
DEVICE = "device-code-123"

CODE = DeviceCode(DEVICE, "ABCD-EFGH", "https://www.google.com/device", 600, 5)


def token_body(**extra: Any) -> dict[str, Any]:
    return {
        "access_token": ACCESS,
        "expires_in": 3599,
        "refresh_token": REFRESH,
        "scope": oauth.SCOPE,
        "token_type": "Bearer",
        **extra,
    }


class FakeGoogle:
    """A scripted Google: the device-code endpoint and a queue of token-endpoint answers."""

    def __init__(self, token_answers: list[httpx.Response] | None = None) -> None:
        self.token_answers = list(token_answers or [])
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == oauth.DEVICE_CODE_URL:
            return httpx.Response(
                200,
                json={
                    "device_code": DEVICE,
                    "user_code": "ABCD-EFGH",
                    "verification_url": "https://www.google.com/device",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        assert str(request.url) == oauth.TOKEN_URL
        return self.token_answers.pop(0)

    def client(self) -> GoogleOAuth:
        return GoogleOAuth(CLIENT_ID, SECRET, transport=httpx.MockTransport(self.handler))

    @staticmethod
    def form(request: httpx.Request) -> dict[str, str]:
        return dict(httpx.QueryParams(request.content.decode()))


def error(code: str, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"error": code})


class Sleeper:
    def __init__(self) -> None:
        self.waited: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waited.append(seconds)


class Ticker:
    """A monotonic clock that moves only when asked."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def pending() -> httpx.Response:
    return error("authorization_pending", 428)


# --- device code --------------------------------------------------------------------------


async def test_device_code_request_carries_the_client_and_the_scope() -> None:
    google = FakeGoogle()
    code = await google.client().request_device_code()
    assert code == CODE
    form = google.form(google.requests[0])
    assert form == {"client_id": CLIENT_ID, "scope": oauth.SCOPE}
    assert SECRET not in google.requests[0].content.decode()  # the secret is for the token step


@pytest.mark.parametrize("answer", ["invalid_client", "unauthorized_client", "invalid_scope"])
async def test_a_refused_client_is_not_configured(answer: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return error(answer)

    client = GoogleOAuth(CLIENT_ID, SECRET, transport=httpx.MockTransport(handler))
    with pytest.raises(MusicError) as exc:
        await client.request_device_code()
    assert exc.value.code == "not_configured"
    assert "TVs" in exc.value.message and SECRET not in exc.value.message


async def test_a_broken_device_code_answer_is_an_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"device_code": "x"})  # no user_code, no url

    client = GoogleOAuth(CLIENT_ID, SECRET, transport=httpx.MockTransport(handler))
    with pytest.raises(MusicError) as exc:
        await client.request_device_code()
    assert exc.value.code == "api_error"


@pytest.mark.parametrize("failure", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
async def test_network_failures_are_typed(failure: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    client = GoogleOAuth(CLIENT_ID, SECRET, transport=httpx.MockTransport(handler))
    with pytest.raises(MusicError) as exc:
        await client.request_device_code()
    assert exc.value.code == "network_error"


# --- polling ------------------------------------------------------------------------------


async def test_polling_waits_while_pending_then_returns_the_token() -> None:
    google = FakeGoogle([pending(), pending(), httpx.Response(200, json=token_body())])
    sleeper = Sleeper()
    token = await google.client().poll_for_token(CODE, sleep=sleeper, clock=Ticker())
    assert (token.access_token, token.refresh_token) == (ACCESS, REFRESH)
    assert token.expires_at > time.time() + 3000
    assert sleeper.waited == [5, 5, 5]
    form = google.form(google.requests[0])
    assert form["device_code"] == DEVICE and form["grant_type"] == oauth.DEVICE_GRANT
    assert form["client_secret"] == SECRET and form["client_id"] == CLIENT_ID


async def test_slow_down_makes_it_wait_longer() -> None:
    google = FakeGoogle([error("slow_down"), pending(), httpx.Response(200, json=token_body())])
    sleeper = Sleeper()
    await google.client().poll_for_token(CODE, sleep=sleeper, clock=Ticker())
    assert sleeper.waited == [5, 10, 10]


async def test_denying_the_access_stops_the_wait() -> None:
    google = FakeGoogle([pending(), error("access_denied", 403)])
    with pytest.raises(MusicError) as exc:
        await google.client().poll_for_token(CODE, sleep=Sleeper(), clock=Ticker())
    assert exc.value.code == "auth_required" and "negada" in exc.value.message


async def test_an_expired_code_is_reported_by_google_or_by_the_clock() -> None:
    google = FakeGoogle([error("expired_token")])
    with pytest.raises(MusicError) as exc:
        await google.client().poll_for_token(CODE, sleep=Sleeper(), clock=Ticker())
    assert exc.value.code == "auth_expired"

    clock = Ticker()

    async def sleeper(seconds: float) -> None:
        clock.now += 400  # the user never shows up

    slow = FakeGoogle([pending(), pending(), pending()])
    with pytest.raises(MusicError) as exc2:
        await slow.client().poll_for_token(CODE, sleep=sleeper, clock=clock)
    assert exc2.value.code == "auth_expired" and len(slow.requests) == 2


async def test_a_success_without_a_refresh_token_is_refused() -> None:
    body = token_body()
    del body["refresh_token"]
    google = FakeGoogle([httpx.Response(200, json=body)])
    with pytest.raises(MusicError) as exc:
        await google.client().poll_for_token(CODE, sleep=Sleeper(), clock=Ticker())
    assert exc.value.code == "api_error"


async def test_an_unknown_error_is_an_api_error_without_the_raw_answer() -> None:
    google = FakeGoogle([httpx.Response(500, json={"error": "boom", "detail": SECRET})])
    with pytest.raises(MusicError) as exc:
        await google.client().poll_for_token(CODE, sleep=Sleeper(), clock=Ticker())
    assert exc.value.code == "api_error" and SECRET not in exc.value.message


# --- token file ---------------------------------------------------------------------------


def stored(expires_in: float = 3600) -> StoredToken:
    return StoredToken(ACCESS, REFRESH, time.time() + expires_in)


class TestTokenStore:
    def test_round_trip_and_private_file(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "music" / "oauth.json")
        original = stored()
        store.save(original)
        assert store.load() == original
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
        assert not list(store.path.parent.glob("*.tmp"))  # atomic: no leftover

    def test_overwriting_keeps_it_private(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "oauth.json")
        store.save(stored())
        store.path.chmod(0o644)
        store.save(stored(100))
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    @pytest.mark.parametrize("content", ["", "{", "[]", '{"access_token": "x"}', "null"])
    def test_a_missing_or_corrupt_file_means_no_login(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / "oauth.json"
        assert TokenStore(path).load() is None
        path.write_text(content)
        assert TokenStore(path).load() is None

    def test_clear_removes_it(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "oauth.json")
        store.save(stored())
        store.clear()
        store.clear()  # twice is fine
        assert store.load() is None


# --- providing and renewing ---------------------------------------------------------------


async def test_no_login_says_how_to_log_in(tmp_path: Path) -> None:
    provider = TokenProvider(TokenStore(tmp_path / "t.json"), None)
    with pytest.raises(MusicError) as exc:
        await provider.access_token()
    assert exc.value.code == "auth_required" and "juniper music login" in exc.value.message


async def test_a_valid_token_is_returned_without_calling_google(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "t.json")
    store.save(stored(3600))
    google = FakeGoogle()
    assert await TokenProvider(store, google.client()).access_token() == ACCESS
    assert google.requests == []


async def test_a_token_about_to_expire_is_renewed_and_saved_keeping_the_refresh_token(
    tmp_path: Path,
) -> None:
    store = TokenStore(tmp_path / "t.json")
    store.save(stored(30))  # inside the 60 s skew
    renewed = {"access_token": "ya29.novo", "expires_in": 3599, "token_type": "Bearer"}
    google = FakeGoogle([httpx.Response(200, json=renewed)])
    assert await TokenProvider(store, google.client()).access_token() == "ya29.novo"
    form = google.form(google.requests[0])
    assert form["grant_type"] == "refresh_token" and form["refresh_token"] == REFRESH
    saved = store.load()
    assert (
        saved is not None and saved.access_token == "ya29.novo" and saved.refresh_token == REFRESH
    )
    assert saved.expires_at > time.time() + 3000


async def test_a_revoked_or_expired_refresh_token_is_auth_expired(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "t.json")
    store.save(stored(-10))
    google = FakeGoogle([error("invalid_grant")])
    with pytest.raises(MusicError) as exc:
        await TokenProvider(store, google.client()).access_token()
    assert exc.value.code == "auth_expired" and "juniper music login" in exc.value.message
    assert REFRESH not in exc.value.message
    assert store.load() is not None  # nothing is deleted behind the user's back


async def test_renewal_without_a_configured_client_is_not_configured(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "t.json")
    store.save(stored(-10))
    with pytest.raises(MusicError) as exc:
        await TokenProvider(store, None).access_token()
    assert exc.value.code == "not_configured" and "JUNIPER_YOUTUBE_CLIENT_ID" in exc.value.message


@pytest.mark.parametrize("answer", ["invalid_client", "unauthorized_client"])
async def test_a_refused_client_on_renewal_is_not_configured(tmp_path: Path, answer: str) -> None:
    store = TokenStore(tmp_path / "t.json")
    store.save(stored(-10))
    google = FakeGoogle([error(answer, 401)])
    with pytest.raises(MusicError) as exc:
        await TokenProvider(store, google.client()).access_token()
    assert exc.value.code == "not_configured"


async def test_concurrent_calls_renew_only_once(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "t.json")
    store.save(stored(-10))
    renewed = {"access_token": "ya29.novo", "expires_in": 3599}
    google = FakeGoogle([httpx.Response(200, json=renewed)])  # a second renewal would IndexError
    provider = TokenProvider(store, google.client())
    tokens = await asyncio.gather(*(provider.access_token() for _ in range(5)))
    assert tokens == ["ya29.novo"] * 5 and len(google.requests) == 1


# --- the login command --------------------------------------------------------------------


async def test_login_shows_the_code_saves_the_token_and_prints_no_secret(tmp_path: Path) -> None:
    google = FakeGoogle([pending(), httpx.Response(200, json=token_body())])
    store = TokenStore(tmp_path / "oauth.json")
    said: list[str] = []
    token = await oauth.login(google.client(), store, said.append, sleep=Sleeper(), clock=Ticker())
    assert store.load() == token
    shown = "\n".join(said)
    assert "https://www.google.com/device" in shown and "ABCD-EFGH" in shown
    for secret in (SECRET, ACCESS, REFRESH, DEVICE):
        assert secret not in shown


# --- configuration and the CLI ------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def music_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings.music, "oauth_file", tmp_path / "oauth.json")
    monkeypatch.setattr(settings.music, "quota_file", tmp_path / "quota.json")
    monkeypatch.setattr(settings.music, "enabled", True)
    monkeypatch.setattr(settings, "youtube_api_key", None)
    monkeypatch.setattr(settings, "youtube_client_id", None)
    monkeypatch.setattr(settings, "youtube_client_secret", None)
    return tmp_path


def test_status_tells_what_is_missing(music_cfg: Path) -> None:
    result = runner.invoke(cli.app, ["music", "status"])
    assert result.exit_code == 0
    assert "Música: ligada" in result.output
    assert "Chave da YouTube Data API: FALTA" in result.output
    assert "Cliente OAuth (ID e chave): FALTA" in result.output
    assert "sem login" in result.output and "0 de 9000" in result.output


def test_status_never_prints_a_secret(music_cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "youtube_api_key", SecretStr("AIza-chave-da-api"))
    monkeypatch.setattr(settings, "youtube_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "youtube_client_secret", SecretStr(SECRET))
    TokenStore(settings.music.oauth_file).save(stored(7200))
    result = runner.invoke(cli.app, ["music", "status"])
    assert "configurada" in result.output and "configurado" in result.output
    assert "conectada" in result.output and "vale mais 1h" in result.output
    for secret in ("AIza-chave-da-api", CLIENT_ID, SECRET, ACCESS, REFRESH):
        assert secret not in result.output


def test_login_without_the_client_says_what_to_set(music_cfg: Path) -> None:
    result = runner.invoke(cli.app, ["music", "login"])
    assert result.exit_code == 1
    assert "JUNIPER_YOUTUBE_CLIENT_ID" in result.output


def test_login_command_runs_the_flow_and_reports_the_file(
    music_cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    google = FakeGoogle([httpx.Response(200, json=token_body())])
    monkeypatch.setattr(settings, "youtube_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "youtube_client_secret", SecretStr(SECRET))
    monkeypatch.setattr(oauth, "build_oauth", lambda transport=None: google.client())

    async def instant(seconds: float) -> None:
        return None

    monkeypatch.setattr(oauth.asyncio, "sleep", instant)
    result = runner.invoke(cli.app, ["music", "login"])
    assert result.exit_code == 0, result.output
    assert "ABCD-EFGH" in result.output and "Pronto" in result.output
    assert str(settings.music.oauth_file) in result.output
    for secret in (SECRET, ACCESS, REFRESH):
        assert secret not in result.output
    assert TokenStore(settings.music.oauth_file).load() is not None


def test_login_command_reports_a_denial_without_a_traceback(
    music_cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    google = FakeGoogle([error("access_denied", 403)])
    monkeypatch.setattr(oauth, "build_oauth", lambda transport=None: google.client())

    async def instant(seconds: float) -> None:
        return None

    monkeypatch.setattr(oauth.asyncio, "sleep", instant)
    result = runner.invoke(cli.app, ["music", "login"])
    assert result.exit_code == 1 and "negada" in result.output
    assert "Traceback" not in result.output
    assert TokenStore(settings.music.oauth_file).load() is None


def test_client_credentials_come_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.config import Settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JUNIPER_YOUTUBE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("JUNIPER_YOUTUBE_CLIENT_SECRET", SECRET)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.youtube_client_id == CLIENT_ID
    assert s.youtube_client_secret is not None
    assert SECRET not in repr(s.youtube_client_secret)
    assert s.music.oauth_file == tmp_path / ".juniper" / "music" / "oauth.json"


def test_build_oauth_needs_both_the_id_and_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "youtube_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "youtube_client_secret", None)
    assert oauth.build_oauth() is None
    monkeypatch.setattr(settings, "youtube_client_id", None)
    monkeypatch.setattr(settings, "youtube_client_secret", SecretStr(SECRET))
    assert oauth.build_oauth() is None
    monkeypatch.setattr(settings, "youtube_client_id", CLIENT_ID)
    assert oauth.build_oauth() is not None


def test_the_wire_log_masks_what_the_login_sends() -> None:
    """The log redacts by key name: the client secret and the refresh token never reach disk."""
    from brain.eventlog import REDACTED, redact

    out = json.dumps(
        redact(
            {
                "client_secret": SECRET,
                "refresh_token": REFRESH,
                "access_token": ACCESS,
                "device_code": DEVICE,
                "user_code": "ABCD-EFGH",
            }
        )
    )
    assert SECRET not in out and REFRESH not in out and ACCESS not in out
    assert REDACTED in out
