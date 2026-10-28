"""Local, statistical search over Juniper's own documentation (ADR-025).

The documentation is split into sections (one per ``##``/``###`` heading) and ranked with BM25
over accent-folded, lightly stemmed Portuguese tokens, with a small lay-to-technical synonym
table for the query. No LLM, no embeddings, no network: answering "how do you work?" costs only
the tokens of the few excerpts handed to the model.

Safety: only the versioned sources are read (``docs/pages/*.md`` and
``docs/decisions/ADR-*.md``, resolved inside the docs folder; a symlink pointing elsewhere is
skipped). Nothing here takes a path from the caller, so ``.env``, ``config.yaml``, ``certs/``
and the state folder are out of reach, and every excerpt goes through the secret filter before
it leaves this module.
"""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from brain.memory.secretfilter import mask_secrets

DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
SOURCES: tuple[tuple[str, str], ...] = (("pages", "*.md"), ("decisions", "ADR-*.md"))

K1 = 1.2
B = 0.5  # milder than the usual 0.75: tiny sections (a link list) should not win on length
HEADING_WEIGHT = 3  # a heading word counts as this many body words
TITLE_WEIGHT = 2
SYNONYM_WEIGHT = 0.5  # an expanded synonym counts half of a word the user typed
MAX_PER_PAGE = 2  # at most this many sections of the same page in one answer
# The ADRs are the history of a decision; the pages describe what holds today.
SOURCE_WEIGHT = {"pages": 1.0, "decisions": 0.75}
# An overview answers a general question better than a detail that happens to share the words.
OVERVIEW_BONUS = 1.25
OVERVIEW_HEADINGS = frozenset({"", "visao geral", "o que e", "a ideia", "como funciona"})
# Sections that only point elsewhere or list test commands do not answer anything.
SKIPPED_HEADINGS = frozenset({"testes", "para saber mais", "tasks", "mais"})

_STOPWORDS_TEXT = """
a o as os um uma uns umas de da do das dos d em na no nas nos num numa por pela pelo pelas
pelos para pra pro com sem e ou que se como qual quais quando onde porque quem cujo isso
isto esse essa esses essas este esta estes estas aquele aquela ela ele elas eles voce voces
vc tu te ti teu tua seu sua seus suas meu minha meus minhas me mim eu nos lhe ja mais muito
muita mas nao sim ao aos sobre entre ate so tambem cada ser sao foi era tem ha estar estao
la aqui ai entao tipo coisa coisas pode podes consegue fazer faz fala fale explica explique
conta conte sabe saber the of and to is funciona funcionam funcionamento usa usar uso ter
faco fazem acontece existe
"""
STOPWORDS = frozenset(_STOPWORDS_TEXT.split())

# Lay words → the technical words the documentation uses (folded, before stemming).
SYNONYMS: dict[str, tuple[str, ...]] = {
    "ferramenta": ("tool",),
    "ferramentas": ("tool",),
    "tool": ("ferramenta",),
    "tools": ("ferramenta",),
    "lembra": ("memoria",),
    "lembrar": ("memoria",),
    "lembranca": ("memoria", "refinada"),
    "esquece": ("forget", "memoria"),
    "esquecer": ("forget", "memoria"),
    "modelo": ("llm",),
    "ia": ("llm",),
    "inteligencia": ("llm",),
    "permissao": ("confirmacao", "tier"),
    "autorizacao": ("confirmacao",),
    "pergunta": ("confirmacao",),
    "arquitetura": ("camada", "cerebro"),
    "seguranca": ("tier", "segredo"),
    "agenda": ("calendar",),
    "calendario": ("calendar",),
    "musica": ("youtube",),
    "celular": ("mobile", "android"),
    "android": ("mobile",),
    "aplicativo": ("app", "mobile"),
    "servidor": ("server",),
    "deploy": ("producao", "vps"),
    "implantar": ("deploy", "producao"),
    "sentimento": ("afeto",),
    "sentimentos": ("afeto",),
    "emocao": ("afeto",),
    "humor": ("afeto",),
    "personalidade": ("persona", "traco"),
    "opiniao": ("crenca",),
    "opinioes": ("crenca",),
    "sonho": ("dream", "noite"),
    "sonha": ("dream", "noite"),
    "voz": ("stt", "tts"),
    "ouve": ("stt",),
    "conversa": ("sessao",),
    "terminal": ("cli",),
    "painel": ("dashboard",),
    "falha": ("failover", "erro"),
    "provedor": ("provider",),
    "cerebro": ("brain",),
    "banco": ("mongodb",),
    "dados": ("mongodb", "privacidade"),
    "privacidade": ("segredo",),
    "decisao": ("adr",),
    "decisoes": ("adr",),
}

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^(#{2,3})\s+(.*?)\s*(\{[^{}]*\})?\s*$")
_FRONT = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_WORD = re.compile(r"[a-z0-9_]+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_SPACES = re.compile(r"[ \t]+")


def fold(text: str) -> str:
    """Lowercase, without accents."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def stem(word: str) -> str:
    """A light Portuguese stem: plurals and the final vowel. Crude, but the same on both sides."""
    if len(word) <= 3 or word.isdigit():
        return word
    for suffix, replacement in (
        ("coes", "cao"),
        ("soes", "sao"),
        ("oes", "ao"),
        ("aes", "ao"),
        ("ais", "al"),
        ("eis", "el"),
        ("ns", "m"),
    ):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            word = word[: -len(suffix)] + replacement
            break
    else:
        if word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
    if len(word) > 4 and word[-1] in "aeo":
        word = word[:-1]
    return word


def words(text: str) -> list[str]:
    """Folded words of ``text`` without stopwords (not stemmed)."""
    return [w for w in _WORD.findall(fold(text)) if len(w) > 1 and w not in STOPWORDS]


def tokens(text: str) -> list[str]:
    return [stem(w) for w in words(text)]


def query_terms(text: str) -> dict[str, float]:
    """The weighted stems of a query: what was typed (1.0) plus lay synonyms (0.5)."""
    weights: dict[str, float] = {}
    for word in words(text):
        weights[stem(word)] = 1.0
        for extra in SYNONYMS.get(word, ()):
            weights.setdefault(stem(extra), SYNONYM_WEIGHT)
    return weights


@dataclass(frozen=True)
class Section:
    source: str  # "docs/pages/agente.md"
    page: str  # the page (or ADR) title
    heading: str  # the section heading ("" for the text before the first heading)
    anchor: str
    text: str  # plain text, paragraphs separated by blank lines

    @property
    def where(self) -> str:
        return f"{self.source}#{self.anchor}" if self.anchor else self.source


@dataclass(frozen=True)
class Hit:
    section: Section
    score: float


@dataclass
class _Doc:
    section: Section
    tf: Counter[str]
    length: int


@dataclass
class DocsIndex:
    """BM25 over the sections. Build with ``DocsIndex.build``; search with ``search``."""

    docs: list[_Doc] = field(default_factory=list)
    df: Counter[str] = field(default_factory=Counter)
    avg_length: float = 1.0

    @classmethod
    def build(cls, sections: list[Section]) -> DocsIndex:
        index = cls()
        for section in sections:
            if fold(section.heading) in SKIPPED_HEADINGS:
                continue
            tf: Counter[str] = Counter(tokens(section.text))
            for term in tokens(section.heading):
                tf[term] += HEADING_WEIGHT
            for term in tokens(section.page):
                tf[term] += TITLE_WEIGHT
            if not tf:
                continue
            index.docs.append(_Doc(section, tf, sum(tf.values())))
            index.df.update(tf.keys())
        if index.docs:
            index.avg_length = sum(d.length for d in index.docs) / len(index.docs)
        return index

    def idf(self, term: str) -> float:
        n, df = len(self.docs), self.df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 3) -> list[Hit]:
        terms = query_terms(query)
        if not terms or not self.docs:
            return []
        scored: list[Hit] = []
        for doc in self.docs:
            score = 0.0
            for term, weight in terms.items():
                freq = doc.tf.get(term)
                if not freq:
                    continue
                norm = freq * (K1 + 1) / (freq + K1 * (1 - B + B * doc.length / self.avg_length))
                score += weight * self.idf(term) * norm
            if score > 0:
                folder = doc.section.source.split("/")[1]
                score *= SOURCE_WEIGHT.get(folder, 1.0)
                if fold(doc.section.heading) in OVERVIEW_HEADINGS:
                    score *= OVERVIEW_BONUS
                scored.append(Hit(doc.section, score))
        scored.sort(key=lambda h: h.score, reverse=True)
        out: list[Hit] = []
        per_page: Counter[str] = Counter()
        for hit in scored:
            if per_page[hit.section.source] >= MAX_PER_PAGE:
                continue
            per_page[hit.section.source] += 1
            out.append(hit)
            if len(out) >= top_k:
                break
        return out


# --- reading the sources ---------------------------------------------------------------------


def _plain(markdown_line: str) -> str:
    line = _LINK.sub(r"\1", markdown_line)
    line = line.replace("**", "").replace("`", "")
    line = re.sub(r"^\s*(>\s?)+", "", line)
    if line.strip().startswith("|"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            return ""
        line = " · ".join(c for c in cells if c)
    return _SPACES.sub(" ", line).strip()


def slugify(text: str) -> str:
    """The same heading id the docs generator gives a heading without an explicit one."""
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-") or "secao"


def _heading(match: re.Match[str]) -> tuple[str, str]:
    title = _plain(match.group(2))
    anchor = ""
    for token in (match.group(3) or "").strip("{}").split():
        if token.startswith("#"):
            anchor = token[1:]
    return title, anchor or slugify(title)


def parse(text: str, source: str) -> list[Section]:
    """The sections of one Markdown file (front matter and raw-HTML blocks are dropped)."""
    text = text.replace("\r\n", "\n")
    page = ""
    front = _FRONT.match(text)
    if front:
        for line in front.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "title":
                page = value.strip()
        text = text[front.end() :]
    sections: list[Section] = []
    heading, anchor = "", ""
    lines: list[str] = []
    in_fence = raw = False

    def flush() -> None:
        body = "\n".join(lines).strip()
        body = re.sub(r"\n{3,}", "\n\n", body)
        if body or heading:
            sections.append(Section(source, page, heading, anchor, body))

    for line in text.split("\n"):
        if _FENCE.match(line):
            if not in_fence:
                raw = "html-raw" in line
            in_fence = not in_fence
            continue
        if in_fence:
            if not raw:
                lines.append(line.rstrip())
            continue
        if not page and line.startswith("# "):
            page = _plain(line[2:])
            continue
        match = _HEADING.match(line)
        if match:
            flush()
            heading, anchor = _heading(match)
            lines = []
            continue
        lines.append(_plain(line))
    flush()
    return sections


def source_files(root: Path = DOCS_DIR) -> list[Path]:
    """The documentation files to index, all resolved inside ``root``."""
    base = root.resolve()
    found: list[Path] = []
    for folder, pattern in SOURCES:
        for path in sorted((base / folder).glob(pattern)):
            real = path.resolve()
            if real.is_file() and real.is_relative_to(base):
                found.append(real)
    return found


def load_sections(root: Path = DOCS_DIR) -> list[Section]:
    sections: list[Section] = []
    base = root.resolve()
    for path in source_files(root):
        source = f"docs/{path.relative_to(base).as_posix()}"
        sections.extend(parse(path.read_text(encoding="utf-8"), source))
    return sections


_lock = threading.Lock()
_cache: tuple[tuple[tuple[str, int, int], ...], DocsIndex] | None = None


def _signature(files: list[Path]) -> tuple[tuple[str, int, int], ...]:
    out = []
    for path in files:
        info = path.stat()
        out.append((str(path), info.st_mtime_ns, info.st_size))
    return tuple(out)


def get_index(root: Path = DOCS_DIR) -> DocsIndex:
    """The index, rebuilt only when a source file changed (a cheap ``stat`` per call)."""
    global _cache
    with _lock:
        files = source_files(root)
        signature = _signature(files)
        if _cache is None or _cache[0] != signature:
            _cache = (signature, DocsIndex.build(load_sections(root)))
        return _cache[1]


def available(root: Path = DOCS_DIR) -> bool:
    return bool(source_files(root))


# --- excerpts --------------------------------------------------------------------------------


def excerpt(section: Section, query: str, budget: int) -> str:
    """The most relevant paragraphs of ``section`` for ``query``, within ``budget`` characters,
    with secrets masked."""
    terms = set(query_terms(query))
    paragraphs = [p.strip() for p in section.text.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    scores = [sum(1 for t in tokens(p) if t in terms) for p in paragraphs]
    best = max(range(len(paragraphs)), key=lambda i: (scores[i], -i))
    chosen = {best}
    used = len(paragraphs[best])
    for i in sorted(range(len(paragraphs)), key=lambda i: (-scores[i], abs(i - best))):
        if i in chosen:
            continue
        if used + len(paragraphs[i]) + 1 > budget:
            continue
        chosen.add(i)
        used += len(paragraphs[i]) + 1
    text = "\n".join(paragraphs[i] for i in sorted(chosen))
    text = mask_secrets(_SPACES.sub(" ", text))
    if len(text) > budget:
        text = text[:budget].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return text
