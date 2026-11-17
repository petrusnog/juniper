"""Splits the Markdown documentation into notes (one per ``##``/``###`` section).

Unlike the BM25 index of ``brain/metadocs`` (ADR-025), the body of a note keeps its Markdown
(code blocks, tables), because the consumers are development agents that need the exact text.
Only the raw-HTML blocks (``html-raw``) are dropped. Pure functions, no I/O besides
``load_documents``.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
SOURCES: tuple[tuple[str, str, str], ...] = (
    ("pages", "*.md", "page"),
    ("decisions", "ADR-*.md", "adr"),
)
SEE_ALSO = "para saber mais"

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^(#{2,3})\s+(.*?)\s*(\{[^{}]*\})?\s*$")
_FRONT = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_KV = re.compile(r"(\w+)=(\S+)")


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-") or "secao"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Link:
    dst_source: str  # "docs/pages/agente.md"
    dst_anchor: str  # "" for the whole page
    label: str
    related: bool  # listed under "Para saber mais"


@dataclass
class Note:
    anchor: str
    heading: str  # "" for the opening text before the first heading
    level: int  # 0 for the opening text, 2 or 3 otherwise
    position: int
    body: str
    tech_kind: str = ""  # the ``kind=`` of a technical heading (classe, funcao, tool...)
    tech_file: str = ""  # the ``file=`` of a technical heading

    @property
    def body_hash(self) -> str:
        return sha(f"{self.heading}\n{self.body}")


@dataclass
class Document:
    source: str  # "docs/pages/agente.md"
    kind: str  # "page" | "adr"
    title: str
    group: str = ""
    section: str = ""
    description: str = ""
    status: str = ""
    tags: list[str] = field(default_factory=list)
    content_hash: str = ""
    notes: list[Note] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            meta[key.strip()] = value.strip().strip("\"'")
    return meta, text[match.end() :]


def _resolve(source: str, href: str) -> tuple[str, str] | None:
    """The (docs path, anchor) a relative ``.md`` link points to, or ``None`` if it is not one."""
    if "://" in href:
        return None
    path, _, anchor = href.partition("#")
    if not path.endswith(".md"):
        return None
    resolved = PurePosixPath(source).parent
    for part in PurePosixPath(path).parts:
        if part == "..":
            resolved = resolved.parent
        elif part != ".":
            resolved = resolved / part
    return resolved.as_posix(), anchor


def parse(text: str, source: str, kind: str) -> Document:
    text = text.replace("\r\n", "\n")
    meta, text = _front_matter(text)
    doc = Document(
        source=source,
        kind=kind,
        title=meta.get("title", ""),
        group=meta.get("group", ""),
        section=meta.get("section", ""),
        description=meta.get("description", ""),
        status=meta.get("status", ""),
        tags=meta.get("tags", "").replace(",", " ").split(),
        content_hash=sha(text),
    )
    notes: list[Note] = []
    seen: set[str] = set()
    heading, anchor, level, tech = "", "", 0, ""
    lines: list[str] = []
    in_fence = raw = False
    see_also = False

    def flush() -> None:
        nonlocal see_also
        body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        if not (body or heading):
            return
        base = anchor or "abertura"
        unique, n = base, 2
        while unique in seen:
            unique, n = f"{base}-{n}", n + 1
        seen.add(unique)
        attrs = dict(_KV.findall(tech))
        notes.append(
            Note(
                anchor=unique,
                heading=heading,
                level=level,
                position=len(notes),
                body=body,
                tech_kind=attrs.get("kind", ""),
                tech_file=attrs.get("file", ""),
            )
        )

    for line in text.split("\n"):
        if _FENCE.match(line):
            if not in_fence:
                raw = "html-raw" in line
            in_fence = not in_fence
            if not raw:
                lines.append(line.rstrip())
            continue
        if in_fence:
            if not raw:
                lines.append(line.rstrip())
            continue
        if not doc.title and line.startswith("# "):
            doc.title = _LINK.sub(r"\1", line[2:]).strip()
            continue
        match = _HEADING.match(line)
        if match:
            flush()
            heading = _LINK.sub(r"\1", match.group(2)).replace("`", "").strip()
            attrs_text = (match.group(3) or "").strip("{}")
            anchor = next((t[1:] for t in attrs_text.split() if t.startswith("#")), "")
            anchor = anchor or slugify(heading)
            tech = attrs_text
            level = len(match.group(1))
            see_also = fold(heading) == SEE_ALSO
            lines = []
            continue
        lines.append(line.rstrip())
        for label, href in _LINK.findall(line):
            target = _resolve(source, href)
            if target:
                doc.links.append(Link(target[0], target[1], label.strip(), see_also))
    flush()
    doc.notes = [n for n in notes if fold(n.heading) != SEE_ALSO]
    return doc


def load_documents(root: Path = DOCS_DIR) -> list[Document]:
    """All sources under ``root`` (a symlink pointing outside it is skipped)."""
    base = root.resolve()
    docs: list[Document] = []
    for folder, pattern, kind in SOURCES:
        for path in sorted((base / folder).glob(pattern)):
            real = path.resolve()
            if not (real.is_file() and real.is_relative_to(base)):
                continue
            source = f"docs/{real.relative_to(base).as_posix()}"
            doc = parse(real.read_text(encoding="utf-8"), source, kind)
            doc.title = doc.title or real.stem
            docs.append(doc)
    return docs
