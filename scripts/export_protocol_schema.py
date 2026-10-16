"""Exporta o JSON Schema do protocolo cliente ↔ brain (ADR-012, T-074).

python scripts/export_protocol_schema.py          # grava docs/protocol/schema.json
python scripts/export_protocol_schema.py --check  # falha se o arquivo estiver desatualizado
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "protocol" / "schema.json"

sys.path.insert(0, str(ROOT))

from brain.clients.protocol import schema_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="só verifica, não grava")
    args = parser.parse_args()

    expected = schema_text()
    current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else None
    if args.check:
        if current != expected:
            print(f"{SCHEMA_PATH.relative_to(ROOT)} desatualizado: rode {Path(__file__).name}.")
            return 1
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(expected, encoding="utf-8")
    print(f"Escrito {SCHEMA_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
