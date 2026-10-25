"""Dashboard: o frontend estático (T-132): casca, páginas, segurança e sintaxe."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from brain.dashboard.server import STATIC_DIR, DashboardServer
from tests.test_dashboard_server import BEARER, TOKEN, get

ASSETS = STATIC_DIR / "assets"
SCRIPTS = sorted(ASSETS.rglob("*.js"))
MARKUP = [STATIC_DIR / "index.html", STATIC_DIR / "login.html"]


def pages_registry() -> list[tuple[str, str]]:
    """(id, module) of each page in ``pages/index.js``."""
    source = (ASSETS / "pages" / "index.js").read_text()
    found = re.findall(r'id: "([^"]+)", title: "[^"]+", module: "([^"]+)"', source)
    return [(page_id, module) for page_id, module in found]


def test_every_registered_page_has_its_module_and_a_render_function() -> None:
    pages = pages_registry()
    ids = [p[0] for p in pages]
    assert ids == [
        "visao", "memorias", "central", "estudos", "sonhos", "conversas", "pesquisa", "afetos",
    ]  # fmt: skip
    assert len({p[0] for p in pages}) == len(pages)  # unique ids
    for _, module in pages:
        path = STATIC_DIR / module.lstrip("/")
        assert path.is_file(), module
        assert "export async function render" in path.read_text(), module


def test_data_never_goes_into_the_page_as_html() -> None:
    """Everything the server sends (a memory, a note) is shown with textContent."""
    banned = re.compile(
        r"\.innerHTML|\.outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function\("
    )
    for script in SCRIPTS:
        assert not banned.search(script.read_text()), script.name


def test_the_markup_respects_the_content_security_policy() -> None:
    for page in MARKUP:
        text = page.read_text()
        assert "<script>" not in text and "style=" not in text, page.name
        assert 'lang="pt-BR"' in text and 'name="viewport"' in text, page.name
        assert 'name="color-scheme"' in text, page.name
    for script in SCRIPTS:
        assert 'setAttribute("style"' not in script.read_text(), script.name  # CSSOM is allowed


def test_the_shell_is_accessible_and_responsive() -> None:
    index = (STATIC_DIR / "index.html").read_text()
    assert 'class="skip"' in index and 'aria-label="Seções"' in index and 'tabindex="-1"' in index
    css = (ASSETS / "app.css").read_text()
    assert "prefers-color-scheme: dark" in css and "@media (min-width: 800px)" in css
    assert "focus-visible" in css and "max-width: 600px" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="sem node para checar a sintaxe")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_script_parses_as_a_module(script) -> None:  # type: ignore[no-untyped-def]
    # `node --check` reads .js as a script; the modules need the explicit input type
    result = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=script.read_text(),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


async def test_the_shell_and_every_module_are_served_after_login() -> None:
    dash = DashboardServer("127.0.0.1", 0, TOKEN)
    assert await dash.start()
    try:
        status, _, page = await get(dash, "/", headers=BEARER)
        assert status == 200 and b'type="module" src="/assets/app.js"' in page
        for script in SCRIPTS:
            relative = "/" + script.relative_to(STATIC_DIR).as_posix()
            status, headers, body = await get(dash, relative)
            assert status == 200 and headers["content-type"].startswith("text/javascript"), relative
            assert body == script.read_bytes()
    finally:
        await dash.stop()


def test_the_studies_page_shows_each_notes_uncertainty_source_and_step() -> None:
    page = (ASSETS / "pages" / "studies.js").read_text()
    for needle in ("n.uncertainty", "n.source", "n.level", "pergunta em aberto", "/notes"):
        assert needle in page, needle
    # the labels the person reads for the three uncertainty levels
    for label in ("firme", "incerteza média", "incerta"):
        assert label in page, label


def test_conversations_never_show_a_reply_that_followed_tools_and_handle_the_switch_off() -> None:
    page = (ASSETS / "pages" / "conversations.js").read_text()
    assert "used_tools" in page and "a resposta usou ferramentas" in page
    assert "error.status === 404" in page and "dashboard.transcripts" in page


def test_the_dreams_page_flags_phases_that_hit_the_budget_or_failed() -> None:
    page = (ASSETS / "pages" / "dreams.js").read_text()
    for needle in (
        'budget: ["orçamento", "warn"]',
        'error: ["erro", "warn"]',
        "p.error",
        "r.error",
    ):
        assert needle in page, needle


def test_the_search_page_sends_many_types_and_the_api_helper_supports_lists() -> None:
    api = (ASSETS / "api.js").read_text()
    assert "Array.isArray(value)" in api and "query.append" in api
    page = (ASSETS / "pages" / "search.js").read_text()
    assert "type: [...state.types]" in page and "data.warning" in page
