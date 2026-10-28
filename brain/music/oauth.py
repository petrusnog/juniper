"""Google login for the playlist tools (ADR-024, section 5): OAuth device-code flow.

``juniper music login`` asks Google for a code, the user authorises it in any browser, and the
brain keeps the refresh token in ``music.oauth_file`` (mode 600, outside git). From then on
``TokenProvider.access_token()`` hands out a valid access token and renews it by itself. The
client id and secret come from the environment (``JUNIPER_YOUTUBE_CLIENT_ID`` /
``JUNIPER_YOUTUBE_CLIENT_SECRET``); the client must be of the "TVs and Limited Input devices" type.

Expected failures are typed `MusicError`s with fixed PT-BR messages: no token, secret, URL or raw
Google response is ever put in a message. If the OAuth app is in "testing" mode Google may expire
the refresh token after 7 days; the next call then reports ``auth_expired``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

import httpx

from brain.config import settings
from brain.httplog import logged_transport
from brain.music.base import MusicError

DEVICE_CODE_URL: Final = "https://oauth2.googleapis.com/device/code"
TOKEN_URL: Final = "https://oauth2.googleapis.com/token"
SCOPE: Final = "https://www.googleapis.com/auth/youtube"
DEVICE_GRANT: Final = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_SKEW_SECONDS: Final = 60
MAX_POLL_HICCUPS: Final = 3

LOGIN_HINT: Final = "rode `juniper music login`"
NO_LOGIN: Final = f"Falta entrar na sua conta do YouTube: {LOGIN_HINT}."
EXPIRED: Final = f"O acesso à sua conta do YouTube expirou ou foi revogado: {LOGIN_HINT} de novo."


@dataclass(frozen=True)
class DeviceCode:
    """What the user needs to authorise: open ``verification_url`` and type ``user_code``."""

    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class StoredToken:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds
    scope: str = SCOPE
    token_type: str = "Bearer"


class TokenStore:
    """The token file: written atomically, mode 600; a missing or corrupt file means "no login"."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> StoredToken | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return StoredToken(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expires_at=float(data["expires_at"]),
                scope=str(data.get("scope", SCOPE)),
                token_type=str(data.get("token_type", "Bearer")),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, token: StoredToken) -> None:
        """Write the token: a fresh private file (mode 600 from the start), flushed to disk, then
        moved over the old one, so a crash never leaves a half-written or world-readable token."""
        directory = self._path.parent
        tmp_name: str | None = None
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{self._path.name}.")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(asdict(token), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._path)
            tmp_name = None
        except OSError as exc:
            raise MusicError(
                "storage_error",
                "Não consegui gravar o acesso à sua conta do YouTube (confira a pasta e as "
                "permissões; em contêiner, monte o diretório, não o arquivo).",
            ) from exc
        finally:
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self._path.unlink()


class GoogleOAuth:
    """The two endpoints of the device-code flow, with typed errors."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(
            timeout=timeout, transport=logged_transport("google_oauth", transport)
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, url: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
        try:
            response = await self._http.post(url, data={"client_id": self._client_id, **data})
        except httpx.TimeoutException as exc:
            raise MusicError("network_error", "O Google não respondeu a tempo.") from exc
        except httpx.HTTPError as exc:
            raise MusicError("network_error", "Não consegui falar com o Google agora.") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    async def request_device_code(self) -> DeviceCode:
        status, body = await self._post(DEVICE_CODE_URL, {"scope": SCOPE})
        if status != 200 or "device_code" not in body:
            if body.get("error") in {"invalid_client", "unauthorized_client", "invalid_scope"}:
                raise MusicError(
                    "not_configured",
                    "O Google recusou o cliente OAuth: confira o ID, a chave e se o tipo é "
                    '"TVs e dispositivos de entrada limitada".',
                )
            raise MusicError("api_error", f"O Google não deu o código de acesso ({status}).")
        try:
            return DeviceCode(
                device_code=str(body["device_code"]),
                user_code=str(body["user_code"]),
                verification_url=str(body.get("verification_url") or body["verification_uri"]),
                expires_in=int(body.get("expires_in", 1800)),
                interval=int(body.get("interval", 5)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MusicError("api_error", "O Google devolveu uma resposta inesperada.") from exc

    async def poll_for_token(
        self,
        code: DeviceCode,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> StoredToken:
        """Wait until the user authorises the code (or it expires, or they refuse)."""
        sleep = sleep or asyncio.sleep  # looked up now, so tests can replace it
        deadline = clock() + code.expires_in
        interval = max(1, code.interval)
        hiccups = 0  # a few network/5xx failures in a row must not cancel a login in progress
        while True:
            if clock() >= deadline:
                raise MusicError(
                    "auth_expired", f"O código expirou antes da autorização: {LOGIN_HINT} de novo."
                )
            await sleep(interval)
            try:
                status, body = await self._post(
                    TOKEN_URL,
                    {
                        "client_secret": self._client_secret,
                        "device_code": code.device_code,
                        "grant_type": DEVICE_GRANT,
                    },
                )
            except MusicError as exc:
                hiccups += 1
                if exc.code != "network_error" or hiccups > MAX_POLL_HICCUPS:
                    raise
                continue
            if status >= 500 and not body.get("error"):
                hiccups += 1
                if hiccups > MAX_POLL_HICCUPS:
                    raise MusicError("api_error", f"O Google está instável agora ({status}).")
                continue
            hiccups = 0
            if status == 200 and "access_token" in body:
                refresh = body.get("refresh_token")
                if not isinstance(refresh, str) or not refresh:
                    raise MusicError("api_error", "O Google não devolveu o acesso permanente.")
                return self._token(body, refresh)
            error = body.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "access_denied":
                raise MusicError("auth_required", "A autorização foi negada no Google.")
            if error == "expired_token":
                raise MusicError(
                    "auth_expired", f"O código expirou antes da autorização: {LOGIN_HINT} de novo."
                )
            raise MusicError("api_error", f"O Google recusou o login ({status}).")

    async def refresh(self, token: StoredToken) -> StoredToken:
        """A fresh access token from the stored refresh token (Google omits the refresh token)."""
        status, body = await self._post(
            TOKEN_URL,
            {
                "client_secret": self._client_secret,
                "refresh_token": token.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if status == 200 and "access_token" in body:
            return self._token(body, token.refresh_token)
        error = body.get("error")
        if error == "invalid_grant":
            raise MusicError("auth_expired", EXPIRED)
        if error in {"invalid_client", "unauthorized_client"}:
            raise MusicError("not_configured", "O Google recusou o cliente OAuth configurado.")
        raise MusicError("api_error", f"O Google não renovou o acesso ({status}).")

    @staticmethod
    def _token(
        body: dict[str, Any], refresh_token: str, now: Callable[[], float] = time.time
    ) -> StoredToken:
        try:
            return StoredToken(
                access_token=str(body["access_token"]),
                refresh_token=refresh_token,
                expires_at=now() + int(body.get("expires_in", 3600)),
                scope=str(body.get("scope", SCOPE)),
                token_type=str(body.get("token_type", "Bearer")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MusicError("api_error", "O Google devolveu uma resposta inesperada.") from exc


class TokenProvider:
    """Hands out a valid access token, renewing it when it is about to expire."""

    def __init__(
        self,
        store: TokenStore,
        oauth: GoogleOAuth | None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._oauth = oauth
        self._clock = clock
        self._lock = asyncio.Lock()

    @property
    def store(self) -> TokenStore:
        return self._store

    async def aclose(self) -> None:
        if self._oauth is not None:
            await self._oauth.aclose()

    async def access_token(self) -> str:
        async with self._lock:  # two calls at once renew once
            token = self._store.load()
            if token is None:
                raise MusicError("auth_required", NO_LOGIN)
            if token.expires_at - self._clock() > REFRESH_SKEW_SECONDS:
                return token.access_token
            if self._oauth is None:
                raise MusicError("not_configured", client_hint())
            renewed = await self._oauth.refresh(token)
            self._store.save(renewed)
            return renewed.access_token


def client_hint() -> str:
    return (
        "Faltam JUNIPER_YOUTUBE_CLIENT_ID e JUNIPER_YOUTUBE_CLIENT_SECRET para entrar na "
        "conta do YouTube."
    )


def build_oauth(transport: httpx.AsyncBaseTransport | None = None) -> GoogleOAuth | None:
    """The OAuth client from the settings, or ``None`` if the id or the secret is missing."""
    client_id, secret = settings.youtube_client_id, settings.youtube_client_secret
    if not client_id or secret is None:
        return None
    return GoogleOAuth(client_id, secret.get_secret_value(), transport=transport)


def build_token_provider() -> TokenProvider:
    return TokenProvider(TokenStore(settings.music.oauth_file), build_oauth())


async def login(
    oauth: GoogleOAuth,
    store: TokenStore,
    say: Callable[[str], None],
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> StoredToken:
    """The whole ``juniper music login``: show the code, wait, save. Prints no secret."""
    code = await oauth.request_device_code()
    say(f"Abra {code.verification_url} num navegador e digite o código: {code.user_code}")
    say("Estou esperando você autorizar (o código vale uns minutos)...")
    token = await oauth.poll_for_token(code, sleep=sleep, clock=clock)
    store.save(token)
    return token
