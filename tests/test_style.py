"""A Juniper não escreve travessões: o prompt pede, e o `DashFilter` garante."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from brain.core.style import DashFilter, speak

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("Petrus — tem dentista amanhã.", "Petrus, tem dentista amanhã."),
        (
            "Petrus — tem dentista amanhã — das 10h às 11h.",
            "Petrus, tem dentista amanhã, das 10h às 11h.",
        ),
        ("casa—ou apartamento", "casa, ou apartamento"),
        ("oi – tudo bem?", "oi, tudo bem?"),
        ("oi ― tudo", "oi, tudo"),
        ("oi, — tudo", "oi, tudo"),
        ("de 1997–2003", "de 1997 a 2003"),
        ("— começou com travessão", "começou com travessão"),
        ("linha um\n— linha dois", "linha um\nlinha dois"),
        ("terminou assim —", "terminou assim"),
        ("terminou assim — ", "terminou assim"),
        ("— — dois", "dois"),
        ("sem nada", "sem nada"),
        ("guarda-chuva e 2 - 1 ficam", "guarda-chuva e 2 - 1 ficam"),
        ("espaços   finais  ", "espaços   finais  "),
    ],
)
def test_dashes_become_speech(written: str, spoken: str) -> None:
    assert speak(written) == spoken


def test_no_dash_ever_survives_whatever_the_chunking() -> None:
    text = "Petrus — tem dentista amanhã – das 10h às 11h ― e casa—ou 1997–2003. —fim —"
    expected = speak(text)
    rng = random.Random(7)
    for _ in range(300):
        cuts = sorted(rng.sample(range(1, len(text)), rng.randint(1, 12)))
        pieces = [text[a:b] for a, b in zip([0, *cuts], [*cuts, len(text)], strict=True)]
        filt = DashFilter()
        streamed = "".join(filt.feed(p) for p in pieces) + filt.flush()
        assert streamed == expected, pieces
        assert not any(d in streamed for d in "–—―")


def test_one_character_at_a_time_gives_the_same_result() -> None:
    text = "a — b – c―d"
    filt = DashFilter()
    streamed = "".join(filt.feed(ch) for ch in text) + filt.flush()
    assert streamed == speak(text) == "a, b, c, d"


def test_both_personas_forbid_the_dash() -> None:
    for name in ("juniper.md", "juniper.compact.md"):
        text = (PROMPTS / name).read_text(encoding="utf-8")
        assert "Nunca use travessão" in text, name
