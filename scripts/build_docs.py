#!/usr/bin/env python3
"""Gera o livro de bolso (docs/html) a partir de Markdown.

Fontes:
    docs/pages/*.md        páginas escritas à mão (com um cabeçalho de metadados no topo)
    docs/decisions/*.md    os ADRs (cada um vira docs/html/adr-NNN.html)

Saídas (todas em docs/html, versionadas, porque o GitHub Pages e `juniper docs` servem a pasta
como está):
    <pagina>.html          uma página por arquivo de docs/pages
    adr.html, adr-NNN.html índice e páginas dos ADRs
    entidades.html         índice das entidades (títulos marcados com `kind=`)
    assets/docs-data.js    menu lateral + índice de busca (usados por layout.js e search.js)

Uso:
    python scripts/build_docs.py           # regenera tudo
    python scripts/build_docs.py --check   # só confere se está em dia (sai com 1 se não estiver)

Formato de uma página (docs/pages/exemplo.md):

    ---
    title: Agente
    group: Núcleo            # um dos GROUPS abaixo
    order: 1                 # posição dentro do grupo
    description: Uma frase que aparece no índice e no topo da página.
    status: pronta           # pronta | guia | proposto | planejada | ferramenta (opcional)
    tags: loop, orquestração # palavras extras para a busca (opcional)
    ---

    ## Seção {#secao}
    ### Dispatcher {#dispatcher kind=classe file=brain/core/dispatcher.py}

Títulos com `kind=` entram no índice de entidades. O Markdown aceito é um subconjunto pequeno
(títulos, listas, tabelas, código, citações, negrito, itálico, links); um bloco ```html-raw
passa HTML direto.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "docs" / "pages"
ADR_DIR = ROOT / "docs" / "decisions"
OUT_DIR = ROOT / "docs" / "html"
DATA_FILE = OUT_DIR / "assets" / "docs-data.js"

GROUPS = [
    "Começando",
    "Núcleo",
    "Integrações",
    "Memória",
    "Clientes",
    "Produção",
    "Referência",
    "Ferramentas",
]
STATUSES = {"pronta", "guia", "proposto", "planejada", "ferramenta"}
STATUS_TAG = {"pronta": "g", "guia": "v", "proposto": "a", "planejada": "a", "ferramenta": "v"}

# Páginas feitas à mão, fora do gerador, que aparecem no menu.
EXTRA_NAV = [
    {
        "group": "Ferramentas",
        "title": "Painel de status",
        "file": "status.html",
        "desc": "Testa até onde a Juniper está funcional.",
        "status": "ferramenta",
    }
]

MARK = "<!-- GERADO por scripts/build_docs.py a partir de {src}. Não edite este arquivo. -->"
MARK_PREFIX = "<!-- GERADO por scripts/build_docs.py"

SNIPPET_LIMIT = 1800  # caracteres de texto guardados por seção no índice de busca


# ---------------------------------------------------------------------------
# Markdown (subconjunto)
# ---------------------------------------------------------------------------


def fold(text: str) -> str:
    """Tira os acentos sem mudar o tamanho do texto (cada caractere vira o seu base)."""
    return "".join(unicodedata.normalize("NFD", ch)[0] if ord(ch) > 127 else ch for ch in text)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", fold(text).lower()).strip("-")
    return slug or "secao"


def plain(text: str) -> str:
    """Texto sem marcação inline (para ids e resumos)."""
    text = re.sub(r"`+([^`]*)`+", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    return text.strip()


_SAFE_URL = re.compile(r"^(https?://|mailto:|#|[\w./-]+(\.html|\.md)?(#[\w-]*)?$)", re.IGNORECASE)


def inline(text: str) -> str:
    """Markdown inline -> HTML (código, links, negrito, itálico)."""
    codes: list[str] = []

    def stash(match: re.Match[str]) -> str:
        codes.append(match.group(2).strip())
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", stash, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"&lt;(/?)kbd&gt;", r"<\1kbd>", text)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), html.unescape(match.group(2))
        if not _SAFE_URL.match(url):
            return match.group(0)
        external = url.startswith(("http://", "https://"))
        extra = ' target="_blank" rel="noopener"' if external else ""
        return f'<a href="{html.escape(url, quote=True)}"{extra}>{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)", link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(
        r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", text
    )


def split_row(line: str) -> list[str]:
    """Divide uma linha de tabela em células, respeitando `código` e `\\|`."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    cells.append("".join(current).strip())
    return cells


_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
_HEADING = re.compile(r"^(#{1,4})\s+(.*?)\s*(\{[^{}]*\})?\s*$")
_CALLOUTS = {
    "nota": "info",
    "dica": "tip",
    "atenção": "warn",
    "atencao": "warn",
    "aviso": "warn",
    "importante": "warn",
    "cuidado": "warn",
}


@dataclass
class Heading:
    level: int
    text: str
    id: str
    attrs: dict[str, str] = field(default_factory=dict)
    summary: str = ""


@dataclass
class Rendered:
    html: str
    headings: list[Heading]


class Renderer:
    """Converte Markdown em HTML. `raw=True` aceita blocos ```html-raw (só nas páginas nossas)."""

    def __init__(self, *, raw: bool = True) -> None:
        self.raw = raw
        self.headings: list[Heading] = []
        self._ids: set[str] = set()
        self._summary_for: Heading | None = None

    # -- blocos ------------------------------------------------------------

    def render(self, source: str) -> Rendered:
        lines = source.replace("\r\n", "\n").split("\n")
        body = self._blocks(lines)
        return Rendered(body, self.headings)

    def _unique_id(self, wanted: str) -> str:
        candidate, n = wanted, 2
        while candidate in self._ids:
            candidate = f"{wanted}-{n}"
            n += 1
        self._ids.add(candidate)
        return candidate

    def _blocks(self, lines: list[str]) -> str:
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                i += 1
                continue
            if stripped.startswith("```"):
                i = self._fence(lines, i, out)
                continue
            heading = _HEADING.match(stripped)
            if heading and not line.startswith((" ", "\t")):
                self._heading(heading, out)
                i += 1
                continue
            if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
                out.append("<hr>")
                i += 1
                continue
            if stripped.startswith(">"):
                i = self._quote(lines, i, out)
                continue
            if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
                i = self._table(lines, i, out)
                continue
            if _ITEM.match(line):
                i = self._list(lines, i, out)
                continue
            i = self._paragraph(lines, i, out)
        return "\n".join(out)

    def _fence(self, lines: list[str], i: int, out: list[str]) -> int:
        opener = lines[i].strip()
        lang = opener[3:].strip().lower()
        indent = len(lines[i]) - len(lines[i].lstrip())
        body: list[str] = []
        i += 1
        while i < len(lines) and not lines[i].strip().startswith("```"):
            body.append(lines[i][indent:] if lines[i][:indent].strip() == "" else lines[i])
            i += 1
        i += 1  # fecha
        if lang == "html-raw" and self.raw:
            out.append("\n".join(body))
        else:
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body))}</code></pre>")
        return i

    def _heading(self, match: re.Match[str], out: list[str]) -> None:
        level = len(match.group(1))
        text = match.group(2).strip()
        attrs: dict[str, str] = {}
        wanted = ""
        for token in (match.group(3) or "")[1:-1].split():
            if token.startswith("#"):
                wanted = token[1:]
            elif "=" in token:
                key, _, value = token.partition("=")
                attrs[key] = value
        heading_id = self._unique_id(wanted or slugify(plain(text)))
        entry = Heading(level, plain(text), heading_id, attrs)
        self.headings.append(entry)
        self._summary_for = entry if attrs.get("kind") else None
        out.append(f'<h{level} id="{heading_id}">{inline(text)}</h{level}>')

    def _quote(self, lines: list[str], i: int, out: list[str]) -> int:
        inner: list[str] = []
        while i < len(lines) and lines[i].strip().startswith(">"):
            inner.append(re.sub(r"^\s*>\s?", "", lines[i]))
            i += 1
        first = next((s for s in inner if s.strip()), "")
        label = re.match(r"^\*\*([^*:.]+)[:.]?\*\*", first.strip())
        kind = _CALLOUTS.get(label.group(1).strip().lower()) if label else None
        body = Renderer(raw=self.raw)
        body._ids = self._ids
        rendered = body._blocks(inner)
        if kind:
            out.append(f'<div class="callout {kind}">{rendered}</div>')
        else:
            out.append(f"<blockquote>{rendered}</blockquote>")
        return i

    def _table(self, lines: list[str], i: int, out: list[str]) -> int:
        head = split_row(lines[i])
        i += 2
        rows: list[list[str]] = []
        while i < len(lines) and lines[i].strip() and "|" in lines[i]:
            rows.append(split_row(lines[i]))
            i += 1
        width = len(head)
        parts = ['<div class="tablewrap"><table class="tbl">', "<thead><tr>"]
        parts += [f"<th>{inline(c)}</th>" for c in head]
        parts.append("</tr></thead><tbody>")
        for row in rows:
            row = (row + [""] * width)[:width]
            parts.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
        parts.append("</tbody></table></div>")
        out.append("".join(parts))
        return i

    def _list(self, lines: list[str], i: int, out: list[str]) -> int:
        items: list[tuple[int, str, list[str]]] = []  # (indent, marker, linhas do item)
        while i < len(lines):
            line = lines[i]
            match = _ITEM.match(line)
            if match:
                indent = len(match.group(1).expandtabs(2))
                marker = match.group(2)
                top = items[0] if items else None
                if (
                    top is not None
                    and indent <= top[0]
                    and marker[0].isdigit() != top[1][0].isdigit()
                ):
                    break  # outro tipo de lista (ordenada x não ordenada) começa uma lista nova
                items.append((indent, marker, [match.group(3)]))
                i += 1
            elif line.strip() and line.startswith((" ", "\t")) and items:
                items[-1][2].append(line.strip())
                i += 1
            elif (
                not line.strip()
                and i + 1 < len(lines)
                and (_ITEM.match(lines[i + 1]) or lines[i + 1].startswith(("  ", "\t")))
            ):
                i += 1
            else:
                break
        out.append(self._render_items(items))
        return i

    def _render_items(self, items: list[tuple[int, str, list[str]]]) -> str:
        def build(pos: int, indent: int) -> tuple[str, int]:
            ordered = items[pos][1][0].isdigit()
            tag = "ol" if ordered else "ul"
            parts = [f"<{tag}>"]
            while pos < len(items) and items[pos][0] >= indent:
                level, _marker, text_lines = items[pos]
                if level > indent:
                    break
                pos += 1
                child = ""
                if pos < len(items) and items[pos][0] > level:
                    child, pos = build(pos, items[pos][0])
                parts.append(f"<li>{inline(' '.join(text_lines))}{child}</li>")
            parts.append(f"</{tag}>")
            return "".join(parts), pos

        rendered, _ = build(0, items[0][0])
        return rendered

    def _paragraph(self, lines: list[str], i: int, out: list[str]) -> int:
        buf: list[str] = []
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith(("```", ">"))
                or (_HEADING.match(stripped) and not line.startswith((" ", "\t")))
                or _ITEM.match(line)
                or ("|" in stripped and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]))
            ):
                break
            buf.append(stripped)
            i += 1
        text = " ".join(buf)
        if self._summary_for is not None and not self._summary_for.summary:
            self._summary_for.summary = plain(text)
            self._summary_for = None
        out.append(f"<p>{inline(text)}</p>")
        return i


def parse_front_matter(text: str, path: Path) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text.replace("\r\n", "\n"), re.DOTALL)
    if not match:
        raise ValueError(f"{path}: faltou o cabeçalho de metadados (--- ... ---)")
    meta: dict[str, str] = {}
    for raw in match.group(1).split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            raise ValueError(f"{path}: linha de metadados inválida: {raw!r}")
        meta[key.strip()] = re.sub(r"\s+#.*$", "", value).strip()
    return meta, match.group(2)


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------


@dataclass
class Page:
    file: str  # nome do .html
    title: str
    group: str
    order: float
    description: str
    status: str
    tags: str
    source: str  # caminho relativo da fonte
    body: str
    headings: list[Heading]
    crumb_group: str = ""
    kind: str = "page"  # page | adr | generated


def load_pages() -> list[Page]:
    pages: list[Page] = []
    for path in sorted(PAGES_DIR.glob("*.md")):
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"), path)
        for required in ("title", "group", "order", "description"):
            if required not in meta:
                raise ValueError(f"{path}: falta '{required}' no cabeçalho")
        if meta["group"] not in GROUPS:
            raise ValueError(f"{path}: grupo desconhecido {meta['group']!r} (use um de {GROUPS})")
        status = meta.get("status", "")
        if status and status not in STATUSES:
            raise ValueError(f"{path}: status inválido {status!r} (use um de {sorted(STATUSES)})")
        rendered = Renderer(raw=True).render(body)
        pages.append(
            Page(
                file=f"{path.stem}.html",
                title=meta["title"],
                group=meta["group"],
                order=float(meta["order"]),
                description=meta["description"],
                status=status,
                tags=meta.get("tags", ""),
                source=str(path.relative_to(ROOT)),
                body=rendered.html,
                headings=rendered.headings,
            )
        )
    return pages


@dataclass
class Adr:
    number: str
    title: str
    meta: dict[str, str]
    file: str
    source: str
    body: str
    headings: list[Heading]


def load_adrs() -> list[Adr]:
    adrs: list[Adr] = []
    for path in sorted(ADR_DIR.glob("ADR-*.md")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        lines = text.split("\n")
        title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), path.stem)
        number = re.search(r"(\d+)", path.stem).group(1)  # type: ignore[union-attr]
        meta: dict[str, str] = {}
        body_lines: list[str] = []
        seen_title = False
        in_meta = True
        for line in lines:
            if not seen_title:
                seen_title = line.startswith("# ")
                continue
            if in_meta:
                m = re.match(r"^- \*\*([^*]+?):\*\*\s*(.*)$", line)
                if m:
                    meta[m.group(1).strip()] = m.group(2).strip()
                    continue
                if not line.strip() or line.strip() == "---":
                    continue
                in_meta = False
            body_lines.append(line)
        rendered = Renderer(raw=False).render("\n".join(body_lines))
        adrs.append(
            Adr(
                number=number,
                title=title,
                meta=meta,
                file=f"adr-{number.zfill(3)}.html",
                source=str(path.relative_to(ROOT)),
                body=rendered.html,
                headings=rendered.headings,
            )
        )
    return adrs


# ---------------------------------------------------------------------------
# HTML das páginas
# ---------------------------------------------------------------------------


def status_tag(status: str) -> str:
    if not status:
        return ""
    return f' <span class="tag {STATUS_TAG[status]}">{html.escape(status)}</span>'


def wrap(
    *,
    title: str,
    heading: str,
    lead: str,
    crumb: str,
    body: str,
    source: str,
    status: str = "",
    description: str = "",
) -> str:
    lead_html = f'<p class="lead">{inline(lead)}</p>\n' if lead else ""
    title_html = f"<h1>{inline(heading)}{status_tag(status)}</h1>\n" if heading else ""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(plain(title))} — Livro de bolso Juniper</title>
<meta name="description" content="{html.escape(plain(description or lead), quote=True)}">
<link rel="stylesheet" href="assets/style.css">
<link rel="icon" type="image/png" href="assets/juniper.png">
<script src="assets/theme.js"></script>
{MARK.format(src=source)}
</head>
<body>
<main id="content">
<div class="crumb">{crumb}</div>
{title_html}{lead_html}{body}
</main>
<script src="assets/docs-data.js"></script>
<script src="assets/layout.js"></script>
<script src="assets/search.js"></script>
</body>
</html>
"""


def page_html(page: Page) -> str:
    if page.file == "index.html":
        crumb = "Início"
    else:
        crumb = (
            f'<a href="index.html">Início</a> / {html.escape(page.group)} / '
            f"{html.escape(page.title)}"
        )
    return wrap(
        title=page.title,
        heading=page.title if page.file != "index.html" else "",
        lead=page.description if page.file != "index.html" else "",
        crumb=crumb,
        body=page.body,
        source=page.source,
        status=page.status,
        description=page.description,
    )


def adr_status_tag(status: str) -> str:
    lowered = status.lower()
    if lowered.startswith(("aprovado", "aceito")):
        return '<span class="tag g">aceito</span>'
    if lowered.startswith("proposto"):
        return '<span class="tag a">proposto</span>'
    return f'<span class="tag v">{html.escape(status.split(" (")[0])}</span>'


def adr_html(adr: Adr) -> str:
    def meta_value(key: str, value: str) -> str:
        if key != "Status":
            return inline(value)
        rest = re.sub(r"^(aprovado|aceito|proposto)\s*", "", value, flags=re.IGNORECASE)
        return adr_status_tag(value) + (f" {inline(rest)}" if rest else "")

    rows = "".join(
        f"<dt>{html.escape(k)}</dt><dd>{meta_value(k, v)}</dd>" for k, v in adr.meta.items()
    )
    meta_html = f'<dl class="adrmeta">{rows}</dl>\n' if rows else ""
    crumb = (
        f'<a href="index.html">Início</a> / <a href="adr.html">Decisões</a> / '
        f"ADR-{adr.number.zfill(3)}"
    )
    return wrap(
        title=adr.title,
        heading=adr.title,
        lead="",
        crumb=crumb,
        body=meta_html + adr.body,
        source=adr.source,
        description=adr.title,
    )


def adr_index(adrs: list[Adr]) -> Page:
    rows = []
    for adr in adrs:
        status = adr.meta.get("Status", "")
        related = adr.meta.get("Relaciona-se com", "")
        short_title = re.sub(r"^ADR-\d+\s*[—-]\s*", "", adr.title)
        rows.append(
            f'<tr><td><a href="{adr.file}">ADR-{adr.number.zfill(3)}</a></td>'
            f"<td>{inline(short_title)}</td>"
            f"<td>{adr_status_tag(status)}</td>"
            f"<td>{html.escape(adr.meta.get('Data', ''))}</td>"
            f"<td>{inline(related)}</td></tr>"
        )
    table = (
        '<div class="tablewrap"><table class="tbl"><thead><tr><th>ADR</th><th>Decisão</th>'
        "<th>Status</th><th>Data</th><th>Relaciona-se com</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    intro = (
        "<p>Cada decisão de arquitetura da Juniper fica registrada num ADR (<em>Architecture "
        "Decision Record</em>): o contexto, o que foi decidido, as consequências e as "
        "alternativas descartadas. Os ADRs <strong>propostos</strong> descrevem o que está "
        "planejado e ainda não foi construído; os <strong>aceitos</strong> descrevem o que "
        "vale hoje. Os arquivos-fonte ficam em <code>docs/decisions/</code>.</p>"
    )
    page = Page(
        file="adr.html",
        title="Decisões (ADRs)",
        group="Referência",
        order=2,
        description="As decisões de arquitetura: contexto, escolha, consequências e alternativas.",
        status="",
        tags="adr architecture decision record decisões arquitetura",
        source="docs/decisions/",
        body=f'<h2 id="lista">Todas as decisões</h2>\n{intro}\n{table}',
        headings=[Heading(2, "Todas as decisões", "lista")],
        kind="generated",
    )
    return page


def entities_page(pages: list[Page]) -> Page:
    entities: list[tuple[str, Heading, Page]] = []
    for page in pages:
        for heading in page.headings:
            if heading.attrs.get("kind"):
                entities.append((heading.text.lower(), heading, page))
    entities.sort(key=lambda item: fold(item[0]))
    rows = []
    for _key, heading, page in entities:
        where = heading.attrs.get("file", "")
        rows.append(
            f'<tr><td><a href="{page.file}#{heading.id}">'
            f"<code>{html.escape(heading.text)}</code></a></td>"
            f"<td>{html.escape(heading.attrs['kind'])}</td>"
            f"<td>{'<code>' + html.escape(where) + '</code>' if where else ''}</td>"
            f"<td>{html.escape(page.title)}</td>"
            f"<td>{html.escape(heading.summary[:160])}</td></tr>"
        )
    body = (
        '<h2 id="lista">Todas as entidades</h2>\n'
        "<p>Classes, funções, tools e conceitos que têm uma seção própria na documentação. "
        "Clique para ir direto à seção; a busca do livro (<kbd>Ctrl</kbd>+<kbd>K</kbd>) também "
        "encontra todas pelo nome.</p>\n"
        '<p><input id="entity-filter" class="filter" type="search" '
        'placeholder="Filtrar entidades…" aria-label="Filtrar entidades"></p>\n'
        '<div class="tablewrap"><table class="tbl" id="entity-table"><thead><tr><th>Entidade</th>'
        "<th>Tipo</th><th>Arquivo</th><th>Página</th><th>O que é</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    return Page(
        file="entidades.html",
        title="Entidades",
        group="Referência",
        order=1,
        description=(
            "Índice de todas as classes, tools e conceitos documentados, com link para a seção."
        ),
        status="",
        tags="glossário índice classes funções entities",
        source="docs/pages/*.md",
        body=body,
        headings=[Heading(2, "Todas as entidades", "lista")],
        kind="generated",
    )


# ---------------------------------------------------------------------------
# Índice de busca e dados do menu
# ---------------------------------------------------------------------------


_BLOCKS = frozenset(
    {"p", "li", "tr", "td", "th", "br", "pre", "div", "ul", "ol", "table", "blockquote", "dt", "dd"}
)


class _Sections(HTMLParser):
    """Quebra uma página em seções (uma por título com id) com o texto de cada uma.

    Também extrai as **linhas de tabela** cuja primeira célula é só um `código` (opções do
    config, tools, comandos): cada uma vira uma entrada própria, para a busca achar
    `memory.retrieval.threshold` direto, e não só a seção que o contém.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[dict[str, str]] = []
        self.rows: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._heading: dict[str, str] | None = None
        self._skip = 0
        self._text: list[str] = []
        self._in_main = False
        self._anchor = ""
        self._row: list[dict[str, object]] | None = None
        self._cell: dict[str, object] | None = None
        self._in_code = False

    def _flush(self) -> None:
        if self._current is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self._current["x"] = text[:SNIPPET_LIMIT]
            self.sections.append(self._current)
        self._text = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "main":
            self._in_main = True
        if tag in ("script", "style"):
            self._skip += 1
        if not self._in_main:
            return
        if tag in _BLOCKS:
            self._text.append(" ")
        if tag in ("h1", "h2", "h3"):
            self._flush()
            self._heading = {"h": "", "a": a.get("id") or ""}
            self._current = self._heading
            if a.get("id"):
                self._anchor = a["id"]
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"th": tag == "th", "text": [], "codes": []}
        elif tag == "code" and self._cell is not None:
            self._in_code = True
            self._cell["codes"].append("")  # type: ignore[union-attr]

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag in ("h1", "h2", "h3"):
            self._heading = None
        if tag in _BLOCKS and self._in_main:
            self._text.append(" ")
        if tag == "code":
            self._in_code = False
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._finish_row(self._row)
            self._row = None

    def _finish_row(self, row: list[dict[str, object]]) -> None:
        if len(row) < 2 or row[0]["th"]:
            return
        first = row[0]
        codes = first["codes"]
        label = "".join(first["text"]).strip()  # type: ignore[arg-type]
        if len(codes) != 1 or not codes[0].strip() or codes[0].strip() != label:  # type: ignore[arg-type, index]
            return
        rest = " ".join(
            re.sub(r"\s+", " ", "".join(cell["text"])).strip()
            for cell in row[1:]  # type: ignore[arg-type]
        )
        self.rows.append({"h": label, "a": self._anchor, "x": rest[:SNIPPET_LIMIT]})

    def handle_data(self, data: str) -> None:
        if self._skip or not self._in_main:
            return
        if self._heading is not None:
            self._heading["h"] += data
            return
        self._text.append(data)
        if self._cell is not None:
            self._cell["text"].append(data)  # type: ignore[union-attr]
            if self._in_code and self._cell["codes"]:
                self._cell["codes"][-1] += data  # type: ignore[index]

    def close(self) -> None:
        super().close()
        self._flush()


def search_entries(page: Page, rendered_html: str) -> list[dict[str, str]]:
    parser = _Sections()
    parser.feed(rendered_html)
    parser.close()
    kinds = {h.id: h for h in page.headings if h.attrs.get("kind")}
    group = page.crumb_group or page.group
    entries: list[dict[str, str]] = []
    for sec in parser.sections:
        heading = sec["h"].strip()
        anchor = sec["a"]
        entry = {
            "p": page.file,
            "t": page.title,
            "g": group,
            "h": heading if anchor else page.title,
            "a": anchor,
            "x": sec.get("x", ""),
        }
        if anchor in kinds:
            entry["k"] = kinds[anchor].attrs["kind"]
        if not anchor and page.tags:
            entry["w"] = page.tags
        entries.append(entry)
    seen = {(e["h"].lower(), e["p"]) for e in entries}
    for row in [] if page.kind == "generated" else parser.rows:
        if (row["h"].lower(), page.file) in seen:
            continue  # a seção com esse nome já está no índice
        entries.append(
            {
                "p": page.file,
                "t": page.title,
                "g": group,
                "h": row["h"],
                "a": row["a"],
                "x": row["x"],
                "k": "opção" if page.file == "configuracao.html" else "item",
            }
        )
    return entries


def nav_entries(pages: list[Page]) -> list[dict[str, str | float]]:
    entries: list[dict[str, str | float]] = []
    for page in pages:
        if page.kind == "adr" or page.file == "index.html":
            continue
        entries.append(
            {
                "group": page.group,
                "title": page.title,
                "file": page.file,
                "desc": page.description,
                "status": page.status,
                "order": page.order,
            }
        )
    for extra in EXTRA_NAV:
        entries.append({**extra, "order": 99})
    entries.sort(key=lambda e: (GROUPS.index(str(e["group"])), float(e["order"]), str(e["title"])))
    return entries


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def build() -> dict[Path, str]:
    """Todas as saídas, em memória: caminho -> conteúdo."""
    pages = load_pages()
    adrs = load_adrs()
    outputs: dict[Path, str] = {}

    adr_pages = [
        Page(
            file=adr.file,
            title=adr.title,
            group="Referência",
            order=0,
            description=adr.title,
            status="",
            tags="adr decisão " + adr.number,
            source=adr.source,
            body=adr.body,
            headings=adr.headings,
            crumb_group="Decisões",
            kind="adr",
        )
        for adr in adrs
    ]
    generated = [adr_index(adrs), entities_page(pages)]
    everything = pages + generated

    names = [p.file for p in everything + adr_pages]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"nomes de página repetidos: {dupes}")

    index: list[dict[str, str]] = []
    for page in everything:
        content = page_html(page)
        outputs[OUT_DIR / page.file] = content
        index += search_entries(page, content)
    for adr, adr_page in zip(adrs, adr_pages, strict=True):
        content = adr_html(adr)
        outputs[OUT_DIR / adr.file] = content
        index += search_entries(adr_page, content)
    for extra in EXTRA_NAV:
        index.append(
            {
                "p": extra["file"],
                "t": extra["title"],
                "g": extra["group"],
                "h": extra["title"],
                "a": "",
                "x": extra["desc"],
            }
        )

    nav = nav_entries(everything)
    data = (
        "/* gerado por scripts/build_docs.py — não edite */\n"
        f"window.JUNIPER_NAV = {json.dumps(nav, ensure_ascii=False, separators=(',', ':'))};\n"
        f"window.JUNIPER_SEARCH = {json.dumps(index, ensure_ascii=False, separators=(',', ':'))};\n"
    )
    outputs[DATA_FILE] = data
    return outputs


def stale_generated_files(outputs: dict[Path, str]) -> list[Path]:
    """Arquivos gerados antes que não existem mais (por exemplo, um ADR apagado)."""
    stale = []
    for path in OUT_DIR.glob("*.html"):
        if path in outputs:
            continue
        if MARK_PREFIX in path.read_text(encoding="utf-8")[:2000]:
            stale.append(path)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera o livro de bolso (docs/html).")
    parser.add_argument("--check", action="store_true", help="só confere se está em dia")
    args = parser.parse_args(argv)
    outputs = build()
    stale = stale_generated_files(outputs)

    changed = [
        p for p, c in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != c
    ]
    if args.check:
        if changed or stale:
            for path in changed:
                print(f"desatualizado: {path.relative_to(ROOT)}")
            for path in stale:
                print(f"sobrando:      {path.relative_to(ROOT)}")
            print("Rode: python scripts/build_docs.py")
            return 1
        print(f"Livro de bolso em dia ({len(outputs)} arquivos).")
        return 0

    for path in changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outputs[path], encoding="utf-8")
    for path in stale:
        path.unlink()
    print(f"{len(outputs)} arquivos gerados ({len(changed)} alterados, {len(stale)} removidos).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
