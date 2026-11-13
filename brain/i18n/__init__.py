"""Internationalisation of the texts Juniper writes about herself (T-220).

The activity log (``brain/activity``) stores a *message key* plus its parameters, never only a
finished sentence, so the same record can be read in any language. The key is the English source
text (the gettext ``msgid``); a catalog in ``locale/<lang>/LC_MESSAGES/juniper.po`` translates it.

* ``N_(msgid)`` / ``N_(singular, plural)`` mark a text for extraction (``scripts/i18n.py``) and
  register its plural, so the English fallback is right even without a catalog.
* ``render(key, params, locale)`` returns the sentence: the catalog's translation when there is
  one, else the English source. A missing parameter never raises: it is left as ``{name}``.
* Plural forms follow the catalog's ``Plural-Forms`` header (evaluated by ``gettext.c2py``, the
  standard library's own parser), the ``count`` parameter picks the form.

Catalogs are plain ``.po`` files read directly: no compile step, any translation tool edits them.
"""

from __future__ import annotations

import gettext
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

DEFAULT_LOCALE: Final = "pt_BR"
SOURCE_LOCALE: Final = "en"
LOCALE_DIR: Final = Path(__file__).resolve().parents[2] / "locale"
DOMAIN: Final = "juniper"
COUNT: Final = "count"

# singular msgid -> plural msgid, filled by ``N_`` at import time.
_PLURALS: dict[str, str] = {}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def N_(singular: str, plural: str | None = None) -> str:  # noqa: N802  (gettext's own name)
    """Mark ``singular`` (and its ``plural``) for translation; returns ``singular`` unchanged."""
    if plural is not None:
        _PLURALS[singular] = plural
    return singular


@lru_cache(maxsize=1)
def _source_plurals() -> dict[str, str]:
    """The plurals the template ``locale/juniper.pot`` declares: the registry of ``N_`` only knows
    the texts of the modules already executed, and a record is rendered long after it was made."""
    path = LOCALE_DIR / f"{DOMAIN}.pot"
    if not path.is_file():
        return {}
    entries = parse_po(path.read_text(encoding="utf-8"))
    return {e.msgid: e.msgid_plural for e in entries if e.msgid and e.msgid_plural}


def plural_of(key: str) -> str | None:
    return _PLURALS.get(key) or _source_plurals().get(key)


# --- the .po reader ---------------------------------------------------------------------------

_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _unquote(line: str) -> str:
    match = _STRING.search(line)
    if match is None:
        return ""
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), match.group(1))


@dataclass
class Entry:
    msgid: str = ""
    msgid_plural: str = ""
    msgstr: list[str] = field(default_factory=list)
    fuzzy: bool = False


def parse_po(text: str) -> list[Entry]:
    """The entries of a ``.po`` file (the header is the entry with an empty ``msgid``)."""
    entries: list[Entry] = []
    current: Entry | None = None
    field_name = ""
    flags_fuzzy = False
    for raw in [*text.splitlines(), ""]:
        line = raw.strip()
        if not line:
            if current is not None:
                entries.append(current)
            current, field_name, flags_fuzzy = None, "", False
            continue
        if line.startswith("#,"):
            flags_fuzzy = "fuzzy" in line
            continue
        if line.startswith("#"):
            continue
        if line.startswith("msgctxt"):
            field_name = "msgctxt"
            continue
        if line.startswith("msgid_plural"):
            field_name = "msgid_plural"
            if current is not None:
                current.msgid_plural = _unquote(line)
        elif line.startswith("msgid"):
            if current is not None:
                entries.append(current)
            current = Entry(msgid=_unquote(line), fuzzy=flags_fuzzy)
            field_name = "msgid"
        elif line.startswith("msgstr["):
            field_name = "msgstr"
            if current is not None:
                current.msgstr.append(_unquote(line))
        elif line.startswith("msgstr"):
            field_name = "msgstr"
            if current is not None:
                current.msgstr = [_unquote(line)]
        elif line.startswith('"') and current is not None:
            piece = _unquote(line)
            if field_name == "msgid":
                current.msgid += piece
            elif field_name == "msgid_plural":
                current.msgid_plural += piece
            elif field_name == "msgstr" and current.msgstr:
                current.msgstr[-1] += piece
    return entries


@dataclass
class Catalog:
    """One language: translations by msgid and the plural rule of the language."""

    locale: str
    messages: dict[str, list[str]] = field(default_factory=dict)
    plural: Callable[[int], int] = lambda n: 0 if n == 1 else 1  # noqa: E731

    def lookup(self, key: str, count: int | None) -> str | None:
        forms = self.messages.get(key)
        if not forms:
            return None
        index = 0
        if len(forms) > 1 and count is not None:
            index = min(max(int(self.plural(count)), 0), len(forms) - 1)
        return forms[index] or None


def _plural_rule(header: str) -> Callable[[int], int]:
    match = re.search(r"plural\s*=\s*([^;]+);?", header)
    if match is None:
        return lambda n: 0 if n == 1 else 1
    try:
        rule = gettext.c2py(match.group(1).strip())
    except ValueError:
        return lambda n: 0 if n == 1 else 1
    return lambda n: int(rule(n))


def load_catalog(locale: str, directory: Path | None = None) -> Catalog | None:
    """The catalog of ``locale`` (``pt_BR``, or just ``pt``), or None when there is none."""
    root = directory or LOCALE_DIR
    for name in dict.fromkeys((locale, locale.split("_")[0])):
        path = root / name / "LC_MESSAGES" / f"{DOMAIN}.po"
        if not path.is_file():
            continue
        catalog = Catalog(locale=name)
        for entry in parse_po(path.read_text(encoding="utf-8")):
            if entry.msgid == "":
                header = entry.msgstr[0] if entry.msgstr else ""
                catalog.plural = _plural_rule(header)
            elif not entry.fuzzy and any(entry.msgstr):
                catalog.messages[entry.msgid] = entry.msgstr
        return catalog
    return None


@lru_cache(maxsize=16)
def _cached(locale: str) -> Catalog | None:
    return load_catalog(locale)


def reload() -> None:
    """Forget the loaded catalogs (after editing a ``.po`` at runtime, and in tests)."""
    _cached.cache_clear()
    _source_plurals.cache_clear()


def normalize_locale(locale: str | None) -> str:
    """``pt-br`` / ``PT_BR`` / ``pt_BR.UTF-8`` -> ``pt_BR``; anything empty -> the default."""
    text = (locale or "").split(".")[0].replace("-", "_").strip()
    if not text:
        return DEFAULT_LOCALE
    head, _, tail = text.partition("_")
    return f"{head.lower()}_{tail.upper()}" if tail else head.lower()


def available() -> list[str]:
    """The languages with a catalog, plus the English source."""
    found = sorted(p.parent.parent.name for p in LOCALE_DIR.glob(f"*/LC_MESSAGES/{DOMAIN}.po"))
    return [SOURCE_LOCALE, *[name for name in found if name != SOURCE_LOCALE]]


class _Params(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fill(template: str, params: Mapping[str, Any]) -> str:
    safe = _Params(params)
    return _PLACEHOLDER.sub(lambda m: str(safe[m.group(1)]), template)


def render(key: str, params: Mapping[str, Any] | None = None, locale: str | None = None) -> str:
    """The sentence for ``key`` in ``locale`` (English source when there is no translation)."""
    values = dict(params or {})
    raw_count = values.get(COUNT)
    count: int | None = None
    if isinstance(raw_count, int | float | str) and not isinstance(raw_count, bool):
        try:
            count = int(raw_count)
        except ValueError:
            count = None
    wanted = normalize_locale(locale)
    template: str | None = None
    if wanted.split("_")[0] != SOURCE_LOCALE:
        catalog = _cached(wanted)
        template = catalog.lookup(key, count) if catalog is not None else None
    if template is None:
        plural = plural_of(key)
        template = plural if plural is not None and count is not None and count != 1 else key
    return _fill(template, values)
