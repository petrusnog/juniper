"""Mede o STT da Juniper num conjunto de frases gravadas (T-203, ADR-034 seção 9).

    python scripts/voice_eval.py ~/juniper-voz/conjunto
    python scripts/voice_eval.py ~/juniper-voz/conjunto --json antes.json
    python scripts/voice_eval.py ~/juniper-voz/conjunto --json depois.json --compare antes.json

O conjunto é uma pasta com `nome.wav` (mono, 16 bits) e `nome.txt` (o que foi dito) por frase,
mais um `termos.txt` opcional com nomes próprios e termos técnicos. Veja o roteiro de gravação
em docs/pages/voz-avaliacao.md. Usa a chave da Groq que o cérebro já usa (JUNIPER_GROQ_API_KEY);
a chave nunca é impressa. As gravações e o relatório têm a sua voz e o que você disse: ficam fora
do git.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.config import settings  # noqa: E402
from brain.voice.evaluation import EvalError, evaluate, load_cases, load_terms, render  # noqa: E402
from brain.voice.stt import GroqTranscriber  # noqa: E402

# The free tier of the Groq transcription allows about 20 calls a minute.
DEFAULT_PAUSE = 3.2


def _compare(now: dict[str, Any], before_path: Path) -> str:
    try:
        before = json.loads(before_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"\nNão consegui ler {before_path} para comparar (precisa ser um relatório --json)."

    def line(label: str, key: str, percent: bool = True) -> str:
        a, b = before.get(key), now.get(key)
        if a is None or b is None:
            return f"  {label}: n/d"
        scale = 100 if percent else 1
        unit = "%" if percent else ""
        return f"  {label}: {a * scale:.1f}{unit} → {b * scale:.1f}{unit}"

    lat_a = (before.get("latency") or {}).get("p50_ms")
    lat_b = (now.get("latency") or {}).get("p50_ms")
    return "\n".join(
        [
            f"\nComparado com {before_path.name} (antes → agora):",
            line("WER", "wer"),
            line("CER", "cer"),
            line("Termos próprios errados", "term_miss_rate"),
            f"  Latência (mediana): {lat_a:.0f} ms → {lat_b:.0f} ms"
            if lat_a is not None and lat_b is not None
            else "  Latência (mediana): n/d",
        ]
    )


async def _run(args: argparse.Namespace) -> int:
    key = settings.groq_api_key
    if key is None or not key.get_secret_value():
        print("Falta a chave da Groq (JUNIPER_GROQ_API_KEY no .env).", file=sys.stderr)
        return 2
    folder = Path(args.folder).expanduser()
    try:
        cases = load_cases(folder)
    except EvalError as exc:
        print(exc, file=sys.stderr)
        return 2
    transcriber = GroqTranscriber(model=args.model, language=args.language)
    try:
        report = await evaluate(transcriber, cases, terms=load_terms(folder), pause=args.pause)
    except EvalError as exc:
        print(exc, file=sys.stderr)
        return 2
    finally:
        await transcriber.aclose()
    print(render(report))
    data = report.to_dict()
    if args.compare:
        print(_compare(data, Path(args.compare).expanduser()))
    if args.json:
        out = Path(args.json).expanduser()
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out.chmod(0o600)  # it repeats what was said: private to the user
        print(f"\nRelatório gravado em {args.json}")
    return 1 if report.failures else 0  # a run with failed calls is not a valid measurement


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", help="pasta com os .wav e os .txt de referência")
    parser.add_argument("--json", help="grava o relatório completo neste arquivo")
    parser.add_argument("--compare", help="relatório JSON anterior, para ver antes → agora")
    parser.add_argument(
        "--pause", type=float, default=DEFAULT_PAUSE, help="segundos entre chamadas"
    )
    parser.add_argument("--model", default=None, help="modelo (padrão: voice.stt.model)")
    parser.add_argument("--language", default=None, help="idioma (padrão: voice.stt.language)")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
