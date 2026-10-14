#!/usr/bin/env python3
"""Sets ``memory.session.store`` (file | mongo) in a config.yaml, keeping comments and the rest.

Used by scripts/install.sh. A text edit, not a YAML round trip, so the user's comments and
formatting survive.

    python scripts/set_memory_store.py ~/.juniper/config.yaml mongo
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VALUES = ("file", "mongo")
_BLANK_OR_COMMENT = re.compile(r"^\s*(#.*)?$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block_end(lines: list[str], start: int, indent: int, limit: int) -> int:
    """First line after ``start`` that is back at ``indent`` or less (the block's end)."""
    for i in range(start + 1, limit):
        if not _BLANK_OR_COMMENT.match(lines[i]) and _indent(lines[i]) <= indent:
            return i
    return limit


def set_store(text: str, value: str) -> str:
    """``text`` with ``memory.session.store`` set to ``value`` (created if missing)."""
    if value not in VALUES:
        raise ValueError(f"valor inválido: {value!r} (use {' ou '.join(VALUES)})")
    lines = text.splitlines()

    top = re.compile(r"^memory:\s*(#.*)?$")
    memory = next((i for i, line in enumerate(lines) if top.match(line)), None)
    if memory is None:
        block = ["memory:", "  session:", f"    store: {value}"]
        gap = [""] if lines and lines[-1].strip() else []
        return "\n".join([*lines, *gap, *block]) + "\n"

    memory_end = _block_end(lines, memory, 0, len(lines))
    session = next(
        (i for i in range(memory + 1, memory_end) if re.match(r"^\s+session:\s*(#.*)?$", lines[i])),
        None,
    )
    if session is None:
        lines[memory + 1 : memory + 1] = ["  session:", f"    store: {value}"]
        return "\n".join(lines) + "\n"

    session_end = _block_end(lines, session, _indent(lines[session]), memory_end)
    for i in range(session + 1, session_end):
        if re.match(r"^\s+store:\s*\S", lines[i]):
            lines[i] = re.sub(r"(store:\s*)[^\s#]+", rf"\g<1>{value}", lines[i], count=1)
            return "\n".join(lines) + "\n"

    child = " " * (_indent(lines[session]) + 2)
    lines.insert(session + 1, f"{child}store: {value}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in VALUES:
        print(f"uso: {argv[0]} <config.yaml> <{'|'.join(VALUES)}>", file=sys.stderr)
        return 2
    path = Path(argv[1]).expanduser()
    path.write_text(set_store(path.read_text(encoding="utf-8"), argv[2]), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
