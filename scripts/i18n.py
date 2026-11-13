#!/usr/bin/env python3
"""Translation catalogs of the activity log (T-220): extract, update, check.

    python scripts/i18n.py extract   # rewrite locale/juniper.pot from the N_() calls in brain/
    python scripts/i18n.py update    # add the new texts (empty) to every locale/*/…/juniper.po
    python scripts/i18n.py check     # fail if the .pot is stale, or a .po misses/breaks a text

A text is marked in code with ``N_("source text")`` or ``N_("singular", "plural")`` (English is
the source language). The files are standard gettext ``.pot``/``.po``: Poedit, Weblate or any
tool edits them. A new language is ``locale/<lang>/LC_MESSAGES/juniper.po`` (copy the ``.pot``).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brain.i18n import DOMAIN, LOCALE_DIR, parse_po  # noqa: E402

POT = LOCALE_DIR / f"{DOMAIN}.pot"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
HEADER = (
    'msgid ""\nmsgstr ""\n'
    '"Project-Id-Version: Juniper\\n"\n'
    '"MIME-Version: 1.0\\n"\n'
    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Content-Transfer-Encoding: 8bit\\n"\n'
    '"Language: {lang}\\n"\n'
    '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n'
)


def messages() -> dict[str, tuple[str | None, str]]:
    """``msgid -> (msgid_plural, first place)`` for every ``N_`` call under ``brain/``."""
    found: dict[str, tuple[str | None, str]] = {}
    for path in sorted((ROOT / "brain").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "N_" or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            plural = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                plural = str(node.args[1].value)
            found.setdefault(first.value, (plural, str(path.relative_to(ROOT))))
    return dict(sorted(found.items()))


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def render_pot(found: dict[str, tuple[str | None, str]]) -> str:
    out = [HEADER.format(lang="").replace('"Language: \\n"\n', '"Language: \\n"\n')]
    for msgid, (plural, place) in found.items():
        out.append(f"#: {place}\nmsgid {_quote(msgid)}")
        if plural is not None:
            out.append(f'msgid_plural {_quote(plural)}\nmsgstr[0] ""\nmsgstr[1] ""')
        else:
            out.append('msgstr ""')
        out.append("")
    return "\n".join(out)


def _po_files() -> list[Path]:
    return sorted(LOCALE_DIR.glob(f"*/LC_MESSAGES/{DOMAIN}.po"))


def extract() -> int:
    POT.parent.mkdir(parents=True, exist_ok=True)
    POT.write_text(render_pot(messages()), encoding="utf-8")
    print(f"{POT.relative_to(ROOT)}: {len(messages())} textos")
    return 0


def update() -> int:
    extract()
    found = messages()
    for path in _po_files():
        known = {e.msgid for e in parse_po(path.read_text(encoding="utf-8"))}
        lang = path.parent.parent.name
        additions = []
        for msgid, (plural, place) in found.items():
            if msgid in known:
                continue
            block = f"\n#: {place}\nmsgid {_quote(msgid)}\n"
            if plural is not None:
                block += f'msgid_plural {_quote(plural)}\nmsgstr[0] ""\nmsgstr[1] ""\n'
            else:
                block += 'msgstr ""\n'
            additions.append(block)
        if additions:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("".join(additions))
        print(f"{path.relative_to(ROOT)}: +{len(additions)} ({lang})")
    return 0


def check() -> int:
    problems: list[str] = []
    found = messages()
    if not POT.is_file() or POT.read_text(encoding="utf-8") != render_pot(found):
        problems.append("locale/juniper.pot está velho: rode `python scripts/i18n.py extract`")
    for path in _po_files():
        name = str(path.relative_to(ROOT))
        entries = {e.msgid: e for e in parse_po(path.read_text(encoding="utf-8")) if e.msgid}
        for msgid, (plural, _) in found.items():
            entry = entries.get(msgid)
            if entry is None or not any(entry.msgstr):
                problems.append(f"{name}: falta traduzir «{msgid}»")
                continue
            wanted = set(_PLACEHOLDER.findall(msgid))
            for form in entry.msgstr:
                if form and set(_PLACEHOLDER.findall(form)) - wanted - set(
                    _PLACEHOLDER.findall(plural or "")
                ):
                    problems.append(f"{name}: parâmetro desconhecido em «{msgid}»")
            if plural is not None and len([f for f in entry.msgstr if f]) < 2:
                problems.append(f"{name}: «{msgid}» pede as formas do plural")
        for msgid in entries.keys() - found.keys():
            problems.append(f"{name}: texto que não existe mais no código: «{msgid}»")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    commands = {"extract": extract, "update": update, "check": check}
    if len(argv) != 2 or argv[1] not in commands:
        print(__doc__)
        return 2
    return commands[argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
