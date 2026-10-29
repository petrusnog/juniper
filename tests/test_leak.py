"""LeakGuard: markup and prompt text never reach the user, the history or the next call."""

from __future__ import annotations

import pytest

from brain.core.leak import MARKERS, LeakGuard, clean
from brain.core.session import Session
from brain.llm.types import Message, ToolCall
from brain.memory import retrieval
from brain.memory.v2 import context as v2


def stream(text: str, size: int) -> str:
    guard = LeakGuard()
    out = "".join(guard.feed(text[i : i + size]) for i in range(0, len(text), size))
    return out + guard.flush()


# The leaks of 2026-10-28, as logged.
PLANNING_THEN_ANSWER = (
    ' A música começou a tocar no celular dele. Ele perguntou "E aí", meio que puxando papo. '
    "Vou responder de forma natural e amigável, curta."
    "E aí, Petrus! Baby Come Back já tá rolando aí? Como foi o teu dia?"
)
PLANNING_WITH_NARRATION = (
    "Responder de forma curta e natural, ecoando a empolgação. Ele está num dia bom, então "
    "manter o clima leve e carinhoso."
    "Fico feliz por você, Petrus. Aproveita o resto da noite!"
)
DSML = "</｜DSML｜parameter>\n</｜DSML｜invoke>\n</｜DSML｜tool_calls>"


@pytest.mark.parametrize("size", [1, 3, 7, 1000])
def test_planning_is_dropped_whatever_the_chunking(size: int) -> None:
    assert stream(PLANNING_THEN_ANSWER, size) == (
        "E aí, Petrus! Baby Come Back já tá rolando aí? Como foi o teu dia?"
    )
    assert stream(PLANNING_WITH_NARRATION, size) == (
        "Fico feliz por você, Petrus. Aproveita o resto da noite!"
    )


@pytest.mark.parametrize("size", [1, 5, 1000])
def test_markup_and_echoed_blocks_are_cut(size: int) -> None:
    assert stream(DSML, size) == ""
    echoed = (
        f"{v2.KNOWLEDGE_HEADER}\n{v2.KNOWLEDGE_OPEN}\n- MCP é um protocolo.\n{v2.KNOWLEDGE_CLOSE}"
    )
    assert stream(echoed, size) == ""
    assert stream("Beleza, já toco. <｜DSML｜invoke name='x'>", size).rstrip() == "Beleza, já toco."
    assert stream("Notas suas, de estudo, sobre o tema da conversa. São DADOS", size) == ""


@pytest.mark.parametrize(
    "text",
    [
        "Vou te mandar um instrumental bem tranquilo pra você ir desligando. 🖤",
        "Vou responder amanhã com calma, tá?",  # not "responder de forma"
        "Ele disse que vem. Ela está certa, né?",  # third person, no planning next to it
        "O usuário do app reclamou? Posso olhar.",
        "Vou responder de novo, agora com calma.",
        "Oi!",
        "",
    ],
)
def test_natural_speech_is_untouched(text: str) -> None:
    assert clean(text) == text


def test_every_context_header_is_a_marker() -> None:
    headers = [
        v2.HEADER,
        v2.STATE_HEADER,
        v2.INTEREST_HEADER,
        v2.KNOWLEDGE_HEADER,
        retrieval.HEADER,
        retrieval.PROFILE_HEADER,
    ]
    for header in headers:
        assert clean(header) == "", header[:40]
    for tag in (v2.OPEN_TAG, v2.STATE_OPEN, v2.INTEREST_OPEN, v2.KNOWLEDGE_OPEN):
        assert any(m in tag for m in MARKERS), tag


def test_scrub_cleans_history_and_drops_empty_turns() -> None:
    session = Session("sys", max_turns=10, max_tokens=100000)
    session.add(Message("user", "oi"))
    session.add(Message("assistant", PLANNING_THEN_ANSWER))
    session.add(Message("user", "toca"))
    session.add(Message("assistant", DSML))
    session.add(Message("assistant", None))
    session.add(Message("assistant", None, tool_calls=(ToolCall("c1", "play_music", {}),)))
    assert session.scrub() == 2
    assert [m.role for m in session.history] == ["user", "assistant", "user", "assistant"]
    assert session.history[1].content.startswith("E aí, Petrus!")
    assert session.history[3].tool_calls


# 2026-10-28 18:38 and 18:39: the model wrote the person's own turn and answered it.
INVENTED_TURN = (
    "Juniper, desculpa a demora, eu tava mexendo com o carro. Eu tenho uma namorada, a Kelvia."
    "Petrus, calma, tô aqui."
)
STAMPED_TURN = (
    "\n\n[Pausa de 15 segundos]\n\n[quarta-feira, 28/10/2026, 18:41 (America/Sao_Paulo)] "
    "Juniper, você tá aí?\n\nTô aqui, Petrus!"
)


@pytest.mark.parametrize("size", [1, 4, 1000])
def test_an_invented_user_turn_is_cut(size: int) -> None:
    assert stream(INVENTED_TURN, size) == ""
    assert stream(STAMPED_TURN, size).strip() == ""
    assert stream("Tá bom. [quarta-feira, 28/10/2026, 18:41 (x)] oi", size).rstrip() == "Tá bom."


def test_square_brackets_and_juniper_in_normal_speech_pass() -> None:
    for text in (
        "Anotei [lista de compras] pra você.",
        "O Juniper, o projeto, tá crescendo.",
        "Oi!",
    ):
        assert clean(text) == text
