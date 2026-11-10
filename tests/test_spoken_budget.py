"""A reply meant to be heard is asked to fit the spoken limit (T-204, ADR-034 section 6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from brain.clients import protocol as p
from brain.config import VoiceSettings
from brain.tools.channel import Channel, channel_note, spoken_note
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import connect_client
from tests.test_voice_session import LONG, FakeStt, Rig, collect, say_phrase

# --- the note --------------------------------------------------------------------------------


def test_a_voice_channel_with_a_budget_tells_the_model_how_long_to_be() -> None:
    note = channel_note(Channel(kind="mobile", voice=True, spoken_budget=600))
    assert "(voz)" in note
    assert "600 caracteres" in note and "voz alta" in note
    assert "sem listas" in note and "detalhar" in note


def test_a_text_channel_never_gets_the_spoken_instruction() -> None:
    assert "voz alta" not in channel_note(Channel(kind="cli", voice=False, spoken_budget=600))
    assert "voz alta" not in channel_note(Channel(kind="telegram"))


def test_voice_without_a_budget_keeps_the_old_note() -> None:
    assert channel_note(Channel(kind="mobile", voice=True)) == channel_note(
        Channel(kind="mobile", voice=True, spoken_budget=None)
    )
    assert "voz alta" not in channel_note(Channel(kind="mobile", voice=True))


def test_the_budget_note_states_the_number_it_was_given() -> None:
    assert "250 caracteres" in spoken_note(250)


# --- the setting -----------------------------------------------------------------------------


def test_the_budget_defaults_to_three_quarters_of_the_limit() -> None:
    assert VoiceSettings().reply_budget == 600  # 75% of the default 800
    assert VoiceSettings(max_spoken_chars=1000).reply_budget == 750
    assert VoiceSettings(max_spoken_chars=100).reply_budget == 100  # never below the minimum


def test_an_explicit_budget_wins_and_may_equal_the_limit() -> None:
    assert VoiceSettings(reply_budget_chars=300).reply_budget == 300
    assert VoiceSettings(max_spoken_chars=500, reply_budget_chars=500).reply_budget == 500


def test_a_budget_larger_than_the_limit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="reply_budget_chars"):
        VoiceSettings(max_spoken_chars=500, reply_budget_chars=600)


def test_lowering_only_the_limit_does_not_make_the_defaults_invalid() -> None:
    assert VoiceSettings(max_spoken_chars=300).reply_budget == 225


# --- end to end ------------------------------------------------------------------------------


async def test_the_model_hears_the_budget_on_a_voice_turn_and_not_on_a_text_turn(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([say(LONG), say("resposta de texto")])
    async with Rig(tmp_path, provider, stt=FakeStt("que horas são")) as rig:
        client, _ = await rig.open()
        await say_phrase(client)
        await collect(client, p.ChatEnd, p.VoiceAudio)
        await client.send(type="voice.end", reason="client")
        text_client = await connect_client(rig.server, "terminal")
        await text_client.send(type="chat.send", id="m-2", text="oi por texto")
        await text_client.until(p.ChatEnd)

    def system_notes(call: int) -> str:
        return " ".join(m.content or "" for m in provider.seen[call] if m.role == "system")

    assert "voz alta" in system_notes(0) and "600 caracteres" in system_notes(0)
    assert "voz alta" not in system_notes(1)
