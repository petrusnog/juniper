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
    for asset in ("layout.js", "search.js", "style.css", "theme.js"):
        assert (HTML_DIR / "assets" / asset).exists()
    for name in ("index.html", "agente.html", "status.html"):
        text = outputs.get(HTML_DIR / name) or (HTML_DIR / name).read_text(encoding="utf-8")
        assert "assets/docs-data.js" in text and "assets/search.js" in text
