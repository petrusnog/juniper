"""O livro de bolso (docs/html) é gerado de docs/pages e docs/decisions por scripts/build_docs.py.

Estes testes garantem que ele está em dia, que não há link nem âncora quebrados, que as
referências a arquivos e testes são reais, que toda opção do config está documentada e que o
índice de busca acha o que deve.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = ROOT / "docs" / "html"

_spec = importlib.util.spec_from_file_location("build_docs", ROOT / "scripts" / "build_docs.py")
assert _spec is not None and _spec.loader is not None
build_docs = importlib.util.module_from_spec(_spec)
sys.modules["build_docs"] = build_docs
_spec.loader.exec_module(build_docs)


# ---------------------------------------------------------------------------
# O conversor de Markdown
# ---------------------------------------------------------------------------


def render(md: str, *, raw: bool = True) -> str:
    return build_docs.Renderer(raw=raw).render(md).html


def test_table_keeps_pipes_inside_code_and_escaped_pipes() -> None:
    html = render("| a | b |\n|---|---|\n| `x\\|y` | um \\| dois |\n")
    assert "<th>a</th>" in html
    assert "<code>x|y</code>" in html
    assert "um | dois" in html


def test_table_cell_with_code_containing_a_pipe_is_not_split() -> None:
    html = render("| col | outra |\n|---|---|\n| `a | b` | c |\n")
    assert html.count("<td>") == 2
    assert "<code>a | b</code>" in html


def test_nested_lists_and_ordered_lists() -> None:
    html = render("- um\n  - filho\n- dois\n\n1. primeiro\n2. segundo\n")
    assert "<ul><li>um<ul><li>filho</li></ul></li><li>dois</li></ul>" in html
    assert "<ol><li>primeiro</li><li>segundo</li></ol>" in html


def test_callouts_by_label() -> None:
    assert 'class="callout info"' in render("> **Nota:** texto\n")
    assert 'class="callout warn"' in render("> **Atenção:** texto\n")
    assert 'class="callout tip"' in render("> **Dica:** texto\n")
    assert "<blockquote>" in render("> só uma citação\n")


def test_code_fence_is_escaped_and_html_raw_is_opt_in() -> None:
    assert "&lt;script&gt;" in render("```html\n<script>x</script>\n```\n")
    assert "<div>ok</div>" in render("```html-raw\n<div>ok</div>\n```\n", raw=True)
    # Nos ADRs o HTML cru é escapado.
    assert "&lt;div&gt;" in render("```html-raw\n<div>ok</div>\n```\n", raw=False)


def test_inline_html_is_escaped_but_kbd_is_allowed() -> None:
    html = render("texto <b>x</b> e <kbd>Ctrl</kbd>\n")
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<kbd>Ctrl</kbd>" in html


def test_unsafe_links_are_not_turned_into_anchors() -> None:
    html = render("[mau](javascript:alert(1)) e [bom](pagina.html#a)\n")
    assert 'href="javascript' not in html
    assert 'href="pagina.html#a"' in html


def test_heading_attributes_become_id_and_entity_data() -> None:
    result = build_docs.Renderer().render(
        "## Dispatcher {#dispatcher kind=classe file=brain/core/dispatcher.py}\n\nResumo da peça.\n"
    )
    assert '<h2 id="dispatcher">' in result.html
    heading = result.headings[0]
    assert heading.attrs == {"kind": "classe", "file": "brain/core/dispatcher.py"}
    assert heading.summary == "Resumo da peça."


def test_duplicate_headings_get_unique_ids() -> None:
    html = render("## Testes\n\n## Testes\n")
    assert 'id="testes"' in html and 'id="testes-2"' in html


def test_front_matter_is_required() -> None:
    with pytest.raises(ValueError):
        build_docs.parse_front_matter("sem cabeçalho", Path("x.md"))


# ---------------------------------------------------------------------------
# O site gerado
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def outputs() -> dict[Path, str]:
    return build_docs.build()


def test_generated_files_are_up_to_date(outputs: dict[Path, str]) -> None:
    stale = [
        str(path.relative_to(ROOT))
        for path, content in outputs.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    assert not stale, f"rode `python scripts/build_docs.py` (desatualizados: {stale})"
    leftovers = [p.name for p in build_docs.stale_generated_files(outputs)]
    assert not leftovers, f"arquivos gerados que não existem mais na fonte: {leftovers}"


def test_every_adr_has_a_page_and_is_listed(outputs: dict[Path, str]) -> None:
    index = outputs[HTML_DIR / "adr.html"]
    for adr in sorted((ROOT / "docs" / "decisions").glob("ADR-*.md")):
        number = re.search(r"(\d+)", adr.stem).group(1)  # type: ignore[union-attr]
        page = f"adr-{number.zfill(3)}.html"
        assert HTML_DIR / page in outputs, f"falta a página do {adr.name}"
        assert page in index, f"{adr.name} não aparece no índice de ADRs"


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.links: list[str] = []
        self.h1 = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(a["id"] or "")
        if tag == "a" and a.get("href"):
            self.links.append(a["href"] or "")
        if tag == "h1":
            self.h1 += 1


def _collect(outputs: dict[Path, str]) -> dict[str, _Collector]:
    pages: dict[str, _Collector] = {}
    for path in HTML_DIR.glob("*.html"):
        collector = _Collector()
        collector.feed(outputs.get(path) or path.read_text(encoding="utf-8"))
        pages[path.name] = collector
    return pages


def test_every_page_has_one_h1_and_unique_ids(outputs: dict[Path, str]) -> None:
    for name, page in _collect(outputs).items():
        assert page.h1 == 1, f"{name}: esperava um <h1>, achei {page.h1}"
        duplicated = {i for i in page.ids if page.ids.count(i) > 1}
        assert not duplicated, f"{name}: ids repetidos {sorted(duplicated)}"


def test_internal_links_and_anchors_resolve(outputs: dict[Path, str]) -> None:
    pages = _collect(outputs)
    broken: list[str] = []
    for name, page in pages.items():
        for href in page.links:
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            target, _, anchor = href.partition("#")
            target = target or name
            if target not in pages:
                broken.append(f"{name} -> {href} (página não existe)")
            elif anchor and anchor not in pages[target].ids:
                broken.append(f"{name} -> {href} (âncora não existe)")
    assert not broken, "\n".join(broken)


def test_navigation_lists_every_page(outputs: dict[Path, str]) -> None:
    nav = build_docs.nav_entries(
        build_docs.load_pages() + [build_docs.adr_index([]), build_docs.entities_page([])]
    )
    listed = {entry["file"] for entry in nav}
    for entry in nav:
        assert (HTML_DIR / str(entry["file"])).exists(), entry["file"]
    for page in build_docs.load_pages():
        if page.file != "index.html":
            assert page.file in listed, f"{page.file} não está no menu"
    assert {"adr.html", "entidades.html", "status.html"} <= listed


def test_entities_point_to_real_files() -> None:
    missing = []
    for page in build_docs.load_pages():
        for heading in page.headings:
            where = heading.attrs.get("file")
            if where and not (ROOT / where).exists():
                missing.append(f"{page.file}#{heading.id}: {where}")
    assert not missing, "\n".join(missing)


def test_pages_only_mention_test_files_that_exist() -> None:
    missing = []
    for source in sorted((ROOT / "docs" / "pages").glob("*.md")):
        for name in set(re.findall(r"tests/(test_\w+\.py)", source.read_text(encoding="utf-8"))):
            if not (ROOT / "tests" / name).exists():
                missing.append(f"{source.name}: tests/{name}")
    assert not missing, "\n".join(missing)


def test_pages_only_mention_scripts_that_exist() -> None:
    missing = []
    for source in sorted((ROOT / "docs" / "pages").glob("*.md")):
        for name in set(
            re.findall(r"scripts/([\w.-]+\.(?:py|sh))", source.read_text(encoding="utf-8"))
        ):
            if not (ROOT / "scripts" / name).exists():
                missing.append(f"{source.name}: scripts/{name}")
    assert not missing, "\n".join(missing)


# ---------------------------------------------------------------------------
# Configuração documentada
# ---------------------------------------------------------------------------


def _option_keys(model: type[BaseModel], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for name, field in model.model_fields.items():
        annotation: Any = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            keys += _option_keys(annotation, f"{prefix}{name}.")
        else:
            keys.append(f"{prefix}{name}")
    return keys


def test_every_config_option_is_documented() -> None:
    from brain.config import Settings

    secrets_and_computed = {
        "groq_api_key",
        "deepinfra_api_key",
        "youtube_api_key",
        "youtube_client_id",
        "youtube_client_secret",
        "telegram_bot_token",
        "paths",
    }
    keys = [key for key in _option_keys(Settings) if key.split(".")[0] not in secrets_and_computed]
    assert len(keys) > 30  # sanidade: a varredura achou as opções aninhadas
    page = (ROOT / "docs" / "pages" / "configuracao.md").read_text(encoding="utf-8")
    undocumented = [key for key in keys if f"`{key}`" not in page]
    assert not undocumented, f"opções sem documentação em configuracao.md: {undocumented}"


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def search_index(outputs: dict[Path, str]) -> list[dict[str, str]]:
    data = outputs[build_docs.DATA_FILE]
    match = re.search(r"window\.JUNIPER_SEARCH = (.*);\n", data)
    assert match is not None
    return json.loads(match.group(1))  # type: ignore[no-any-return]


def test_search_index_covers_every_page(
    search_index: list[dict[str, str]], outputs: dict[Path, str]
) -> None:
    indexed = {entry["p"] for entry in search_index}
    for path in outputs:
        if path.suffix == ".html":
            assert path.name in indexed, f"{path.name} não está no índice de busca"
    assert "status.html" in indexed


def test_search_index_finds_entities_with_their_kind(search_index: list[dict[str, str]]) -> None:
    by_heading = {(e["h"], e["p"]): e for e in search_index}
    dispatcher = by_heading[("Dispatcher", "agente.html")]
    assert dispatcher["k"] == "classe"
    assert dispatcher["a"] == "dispatcher"
    assert ("LLMRouter", "llm.html") in by_heading


def test_search_index_has_one_entry_per_config_option(search_index: list[dict[str, str]]) -> None:
    options = {e["h"] for e in search_index if e.get("k") == "opção"}
    assert "memory.retrieval.threshold" in options
    assert "llm.priority" in options
    assert "agent.max_iterations" in options


def test_search_index_stays_small(outputs: dict[Path, str]) -> None:
    assert len(outputs[build_docs.DATA_FILE].encode()) < 1_000_000


def test_nav_and_index_are_published_for_the_browser(outputs: dict[Path, str]) -> None:
    data = outputs[build_docs.DATA_FILE]
    assert "window.JUNIPER_NAV = " in data and "window.JUNIPER_SEARCH = " in data
    for asset in ("layout.js", "search.js", "style.css", "logo.png"):
        assert (HTML_DIR / "assets" / asset).exists()
    for name in ("index.html", "agente.html", "status.html"):
        text = outputs.get(HTML_DIR / name) or (HTML_DIR / name).read_text(encoding="utf-8")
        assert "assets/docs-data.js" in text and "assets/search.js" in text


# --- notas ligadas, mapa e Obsidian ---------------------------------------------------------------

DOCS = ROOT / "docs"
_SOURCE_LINK = re.compile(r"\]\(([^)\s]+)\)")
GENERATED_PAGES = {"adr.html", "entidades.html", "status.html"}


def _sources() -> dict[Path, str]:
    paths = sorted((DOCS / "pages").glob("*.md")) + sorted((DOCS / "decisions").glob("ADR-*.md"))
    return {path: path.read_text(encoding="utf-8") for path in paths}


def _internal_targets(path: Path, text: str) -> set[Path]:
    """The source files that ``text`` links to (resolved like Obsidian resolves them)."""
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    found = set()
    for url in _SOURCE_LINK.findall(text):
        target = url.partition("#")[0]
        if target.endswith(".md") and "://" not in target:
            found.add((path.parent / target).resolve())
    return found


def test_source_links_point_to_existing_md_files_not_html() -> None:
    problems = []
    for path, text in _sources().items():
        stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        for url in _SOURCE_LINK.findall(stripped):
            target = url.partition("#")[0]
            if "://" in target:
                continue
            if target.endswith(".html") and target not in GENERATED_PAGES:
                problems.append(f"{path.name}: {url} (use o .md; o Obsidian não resolve .html)")
            elif target.endswith(".md") and not (path.parent / target).exists():
                problems.append(f"{path.name}: {url} (arquivo não existe)")
    assert not problems, "\n".join(problems)


def test_no_note_is_an_orphan() -> None:
    sources = _sources()
    linked_to: set[Path] = set()
    linking: set[Path] = set()
    for path, text in sources.items():
        targets = _internal_targets(path, text) - {path.resolve()}
        if targets:
            linking.add(path.resolve())
        linked_to |= targets
    orphans = [
        path.name
        for path in sources
        if path.resolve() not in linked_to and path.resolve() not in linking
    ]
    assert not orphans, f"notas sem nenhum link entrando ou saindo: {orphans}"
    # A rede também precisa ser lida: nenhuma página fica sem ser citada por outra nota.
    uncited = [
        path.name for path in sources if path.resolve() not in linked_to and path.name != "index.md"
    ]
    assert not uncited, f"notas que nenhuma outra nota cita: {uncited}"


def test_every_page_ends_with_related_notes() -> None:
    missing = [
        path.name
        for path in (DOCS / "pages").glob("*.md")
        if path.name != "index.md" and "## Para saber mais" not in path.read_text(encoding="utf-8")
    ]
    assert not missing, f"páginas sem a seção 'Para saber mais': {missing}"


def test_map_lists_every_page_and_adr(outputs: dict[Path, str]) -> None:
    text = outputs[DOCS / "MAPA.md"]
    for path in _sources():
        folder = path.parent.name
        assert f"]({folder}/{path.name})" in text or path.name == "index.md", path.name
    assert len(text) < 16_000, "o mapa cresceu demais: ele existe para custar poucos tokens"
    assert text.startswith(build_docs.MARK_PREFIX)


def test_shared_obsidian_config_is_valid_and_hides_generated_html() -> None:
    app = json.loads((DOCS / ".obsidian" / "app.json").read_text(encoding="utf-8"))
    graph = json.loads((DOCS / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
    assert app["useMarkdownLinks"] is True and app["newLinkFormat"] == "relative"
    assert "html/" in app["userIgnoreFilters"]
    assert graph["colorGroups"]


# --- o visual de terminal e a marca animada (T-170) ------------------------------------------

TOKENS = ROOT / "mobile" / "src" / "theme" / "tokens.ts"
BOOK_PALETTE = {
    "background": "--bg", "card": "--card", "ink": "--ink", "muted": "--muted", "border": "--line",
    "accent": "--accent", "cyan": "--cyan", "blue": "--blue", "violet": "--violet",
    "green": "--green", "amber": "--amber", "red": "--red",
}  # fmt: skip


def test_the_book_uses_the_palette_of_the_app() -> None:
    css = (HTML_DIR / "assets" / "style.css").read_text(encoding="utf-8")
    tokens = TOKENS.read_text(encoding="utf-8")
    for key, variable in BOOK_PALETTE.items():
        app = re.search(rf"\b{key}:\s*'(#[0-9a-fA-F]{{6}})'", tokens)
        mine = re.search(rf"{re.escape(variable)}:\s*(#[0-9a-fA-F]{{6}})\s*;", css)
        assert app and mine, (key, variable)
        assert mine.group(1).lower() == app.group(1).lower(), (key, variable)


def test_the_book_has_one_dark_look_without_a_theme_toggle(outputs: dict[Path, str]) -> None:
    css = (HTML_DIR / "assets" / "style.css").read_text(encoding="utf-8")
    assert "color-scheme:dark" in css and "prefers-color-scheme" not in css
    assert "monospace" in css and "--mono" in css
    layout = (HTML_DIR / "assets" / "layout.js").read_text(encoding="utf-8")
    assert "themebtn" not in layout and "JuniperTheme" not in layout
    assert not (HTML_DIR / "assets" / "theme.js").exists()
    for name in ("index.html", "agente.html", "status.html"):
        text = outputs.get(HTML_DIR / name) or (HTML_DIR / name).read_text(encoding="utf-8")
        assert '<meta name="color-scheme" content="dark">' in text and "theme.js" not in text


def test_the_side_menu_shows_the_animated_mark_instead_of_the_book_title() -> None:
    layout = (HTML_DIR / "assets" / "layout.js").read_text(encoding="utf-8")
    brand = layout[
        layout.index('<a class="brand"') : layout.index("</a>", layout.index('<a class="brand"'))
    ]
    assert brand.index('src="assets/logo.png"') < brand.index('class="brand-name">Juniper<')
    assert "Livro de bolso" not in brand and "·" not in brand


def test_the_home_page_has_the_mark_where_the_giant_title_was(outputs: dict[Path, str]) -> None:
    text = outputs.get(HTML_DIR / "index.html") or (HTML_DIR / "index.html").read_text("utf-8")
    hero = text[
        text.index('<div class="hero">') : text.index("</div>", text.index('<div class="hero">'))
    ]
    assert hero.index('src="assets/logo.png"') < hero.index('class="brand-name">Juniper<')
    assert "Livro de bolso da Juniper</h1>" not in text  # the giant title is gone


def test_the_cursor_blinks_and_stands_still_when_the_system_asks_for_less_motion() -> None:
    css = (HTML_DIR / "assets" / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.brand-name::after\{content:\"_\"[^}]*animation:blink", css)
    assert re.search(
        r"@media \(prefers-reduced-motion:reduce\)\{\.brand-name::after\{animation:none\}\}", css
    )


def test_the_book_logo_is_a_transparent_png() -> None:
    data = (HTML_DIR / "assets" / "logo.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[25] == 6  # color type 6: RGBA
    assert len(data) < 150_000
