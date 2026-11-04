"""O cérebro serve o instalador do desktop pela rede privada (ADR-033, no molde do ADR-026)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from brain.clients.app_update import (
    APK_NAME,
    DESKTOP_ARTIFACT,
    DESKTOP_META,
    MAX_INSTALLER_BYTES,
    AppUpdates,
)
from brain.clients.server import BrainServer

TOKEN = "segredo-de-teste-123"
INSTALLER = b"MZ" + b"juniper-setup" * 80


def desktop(directory: Path) -> AppUpdates:
    return AppUpdates(
        directory, artifact=DESKTOP_ARTIFACT, meta=DESKTOP_META, max_bytes=MAX_INSTALLER_BYTES
    )


def publish(
    directory: Path, data: bytes = INSTALLER, *, code: int = 12, **override: object
) -> None:
    meta = {
        "version_code": code,
        "version_name": f"0.1.{code}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "released_at": "2026-11-04T12:00:00Z",
        **override,
    }
    (directory / DESKTOP_ARTIFACT).write_bytes(data)
    (directory / DESKTOP_META).write_text(json.dumps(meta))


def _get(address: str, token: str | None = TOKEN) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(address)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


async def serve(
    desktop_updates: AppUpdates | None, app_updates: AppUpdates | None = None
) -> tuple[BrainServer, str]:
    server = BrainServer(
        "127.0.0.1", 0, TOKEN, app_updates=app_updates, desktop_updates=desktop_updates
    )
    await server.start()
    return server, f"http://127.0.0.1:{server.port}"


def test_the_desktop_and_the_phone_do_not_see_each_others_files(tmp_path: Path) -> None:
    publish(tmp_path)
    assert desktop(tmp_path).release() is not None
    assert AppUpdates(tmp_path).release() is None  # no APK or latest.json in this folder
    (tmp_path / APK_NAME).write_bytes(b"PK")
    assert desktop(tmp_path).release() is not None


@pytest.mark.parametrize(
    "override",
    [{"size": 5}, {"sha256": "xyz"}, {"version_code": 0}, {"size": MAX_INSTALLER_BYTES + 1}],
)
def test_an_inconsistent_installer_publication_is_not_offered(
    tmp_path: Path, override: dict[str, object]
) -> None:
    publish(tmp_path, **override)
    assert desktop(tmp_path).release() is None


async def test_both_routes_need_the_token(tmp_path: Path) -> None:
    publish(tmp_path)
    server, base = await serve(desktop(tmp_path))
    try:
        for path in ("/desktop/update", "/desktop/update/installer"):
            assert (await asyncio.to_thread(_get, base + path, None))[0] == 401
            assert (await asyncio.to_thread(_get, base + path, "errado"))[0] == 401
    finally:
        await server.stop()


async def test_the_check_describes_the_installer_and_the_download_matches(tmp_path: Path) -> None:
    publish(tmp_path)
    server, base = await serve(desktop(tmp_path))
    try:
        status, headers, body = await asyncio.to_thread(_get, base + "/desktop/update")
        d_status, d_headers, data = await asyncio.to_thread(
            _get, base + "/desktop/update/installer"
        )
    finally:
        await server.stop()
    assert status == 200 and headers["Cache-Control"] == "no-store"
    info = json.loads(body)
    assert info["available"] is True and info["version_code"] == 12
    assert info["sha256"] == hashlib.sha256(INSTALLER).hexdigest()
    assert d_status == 200 and data == INSTALLER
    assert d_headers["Content-Length"] == str(len(INSTALLER))


async def test_nothing_published_answers_unavailable_and_404(tmp_path: Path) -> None:
    server, base = await serve(desktop(tmp_path))
    try:
        status, _, body = await asyncio.to_thread(_get, base + "/desktop/update")
        d_status, _, _ = await asyncio.to_thread(_get, base + "/desktop/update/installer")
    finally:
        await server.stop()
    assert status == 200 and json.loads(body) == {"available": False}
    assert d_status == 404


async def test_without_a_desktop_publisher_the_routes_still_answer_cleanly() -> None:
    server, base = await serve(None)
    try:
        status, _, body = await asyncio.to_thread(_get, base + "/desktop/update")
        d_status, _, _ = await asyncio.to_thread(_get, base + "/desktop/update/installer")
    finally:
        await server.stop()
    assert status == 200 and json.loads(body) == {"available": False}
    assert d_status == 404


async def test_the_phone_route_is_untouched(tmp_path: Path) -> None:
    server, base = await serve(desktop(tmp_path), AppUpdates(tmp_path))
    try:
        status, _, body = await asyncio.to_thread(_get, base + "/app/update")
    finally:
        await server.stop()
    assert status == 200 and json.loads(body) == {"available": False}
