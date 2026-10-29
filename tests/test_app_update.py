"""O cérebro serve o APK do app Android pela rede privada (ADR-026)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from brain.clients.app_update import APK_NAME, MAX_APK_BYTES, META_NAME, AppUpdates
from brain.clients.server import BrainServer

TOKEN = "segredo-de-teste-123"
APK = b"PK\x03\x04" + b"juniper-apk" * 50


def publish(
    directory: Path, apk: bytes = APK, *, code: int = 7, name: str = "0.1.7", **override: object
) -> None:
    meta = {
        "version_code": code,
        "version_name": name,
        "sha256": hashlib.sha256(apk).hexdigest(),
        "size": len(apk),
        "released_at": "2026-10-29T12:00:00Z",
        **override,
    }
    (directory / APK_NAME).write_bytes(apk)
    (directory / META_NAME).write_text(json.dumps(meta))


# --- a pasta ----------------------------------------------------------------------------------


def test_nothing_published_means_no_release(tmp_path: Path) -> None:
    assert AppUpdates(tmp_path).release() is None
    assert AppUpdates(tmp_path / "nao-existe").release() is None


def test_a_valid_publication_is_read(tmp_path: Path) -> None:
    publish(tmp_path)
    release = AppUpdates(tmp_path).release()
    assert release is not None
    assert (release.version_code, release.version_name, release.size) == (7, "0.1.7", len(APK))
    assert release.sha256 == hashlib.sha256(APK).hexdigest()


@pytest.mark.parametrize(
    "override",
    [
        {"size": 5},  # upload pela metade ou latest.json de outra versão
        {"sha256": "xyz"},
        {"sha256": "A" * 10},
        {"version_code": 0},
        {"version_code": "sete"},
    ],
)
def test_an_inconsistent_publication_is_not_offered(
    tmp_path: Path, override: dict[str, object]
) -> None:
    publish(tmp_path, **override)
    assert AppUpdates(tmp_path).release() is None


def test_a_corrupt_or_incomplete_metadata_file_is_not_offered(tmp_path: Path) -> None:
    (tmp_path / APK_NAME).write_bytes(APK)
    (tmp_path / META_NAME).write_text("{não é json")
    assert AppUpdates(tmp_path).release() is None
    (tmp_path / META_NAME).write_text(json.dumps({"version_code": 3}))
    assert AppUpdates(tmp_path).release() is None


def test_metadata_without_the_apk_is_not_offered(tmp_path: Path) -> None:
    publish(tmp_path)
    (tmp_path / APK_NAME).unlink()
    assert AppUpdates(tmp_path).release() is None


def test_an_oversized_apk_is_refused(tmp_path: Path) -> None:
    publish(tmp_path, size=MAX_APK_BYTES + 1)
    assert AppUpdates(tmp_path).release() is None


def test_the_apk_that_changed_after_the_check_is_not_served(tmp_path: Path) -> None:
    publish(tmp_path)
    updates = AppUpdates(tmp_path)
    release = updates.release()
    assert release is not None
    (tmp_path / APK_NAME).write_bytes(APK + b"mais")
    assert updates.apk_bytes(release) is None


# --- HTTP -------------------------------------------------------------------------------------


def _get(address: str, token: str | None = TOKEN) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(address)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


async def serve(updates: AppUpdates | None) -> tuple[BrainServer, str]:
    server = BrainServer("127.0.0.1", 0, TOKEN, app_updates=updates)
    await server.start()
    return server, f"http://127.0.0.1:{server.port}"


async def test_the_version_check_needs_the_token(tmp_path: Path) -> None:
    publish(tmp_path)
    server, base = await serve(AppUpdates(tmp_path))
    try:
        for path in ("/app/update", "/app/update/apk"):
            assert (await asyncio.to_thread(_get, base + path, None))[0] == 401
            assert (await asyncio.to_thread(_get, base + path, "errado"))[0] == 401
    finally:
        await server.stop()


async def test_the_version_check_describes_the_published_release(tmp_path: Path) -> None:
    publish(tmp_path)
    server, base = await serve(AppUpdates(tmp_path))
    try:
        status, headers, body = await asyncio.to_thread(_get, base + "/app/update")
    finally:
        await server.stop()
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {
        "available": True,
        "version_code": 7,
        "version_name": "0.1.7",
        "sha256": hashlib.sha256(APK).hexdigest(),
        "size": len(APK),
        "released_at": "2026-10-29T12:00:00Z",
    }


async def test_nothing_published_is_a_normal_answer_not_an_error(tmp_path: Path) -> None:
    for updates in (AppUpdates(tmp_path), None):
        server, base = await serve(updates)
        try:
            status, _, body = await asyncio.to_thread(_get, base + "/app/update")
        finally:
            await server.stop()
        assert status == 200
        assert json.loads(body) == {"available": False}


async def test_the_apk_is_downloaded_whole(tmp_path: Path) -> None:
    publish(tmp_path)
    server, base = await serve(AppUpdates(tmp_path))
    try:
        status, headers, body = await asyncio.to_thread(_get, base + "/app/update/apk")
    finally:
        await server.stop()
    assert status == 200
    assert headers["Content-Type"] == "application/vnd.android.package-archive"
    assert headers["Content-Length"] == str(len(APK))
    assert body == APK


async def test_the_apk_is_404_when_nothing_valid_is_published(tmp_path: Path) -> None:
    publish(tmp_path, size=3)
    server, base = await serve(AppUpdates(tmp_path))
    try:
        assert (await asyncio.to_thread(_get, base + "/app/update/apk"))[0] == 404
    finally:
        await server.stop()


def test_the_apk_is_read_once_while_the_file_does_not_change(tmp_path: Path) -> None:
    publish(tmp_path)
    updates = AppUpdates(tmp_path)
    release = updates.release()
    assert release is not None
    first = updates.apk_bytes(release)
    assert first == APK
    assert updates.apk_bytes(release) is first  # the same copy, not read again
