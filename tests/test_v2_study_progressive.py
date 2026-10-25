"""Estudo progressivo: um passo por tema por noite, partindo das notas anteriores (T-125)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from brain.clients.cli import app
from brain.config import MemoryV2Settings, V2InterestSettings
from brain.llm.base import LLMProvider
from brain.llm.types import LLMResponse, Message
from brain.memory.v2.export import export_v2
from brain.memory.v2.guards import check_interest, is_technical
from brain.memory.v2.interests import InterestBook
from brain.memory.v2.knowledge import NoteBook
from brain.memory.v2.models import Evidence, Interest, StudyNote, TranscriptTurn
from brain.memory.v2.night import Budget, PhaseContext, SessionMaterial
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.memory.v2.study import FOCUS, InterestSeedPhase, StudyPhase, focus_of
from tests.test_extraction import FakeEmbedder

DAY1 = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
ORIGIN = Evidence("observation", "sessA", 4, at=DAY1)


class Teacher(LLMProvider):
    """Seeds topics from a script; studies by answering with notes that name the topic and step."""

    name: ClassVar[str] = "teacher"

    def __init__(self, topics: list[str] | None = None, **reply: Any) -> None:
        self.topics = topics or []
        self.reply = reply
        self.study_calls: list[str] = []

    async def chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        system = messages[0].content or ""
        if system.startswith("Você é a Juniper, notando"):
            out: Any = {"interests": [{"topic": t, "evidence": ["S1:t4"]} for t in self.topics]}
        else:
            self.study_calls.append(system)
            topic = re.search(r"<tema>(.*?)</tema>", system).group(1)  # type: ignore[union-attr]
            step = int(re.search(r"passo (\d+) do seu estudo", system).group(1))  # type: ignore[union-attr]
            out = {
                "appeal": 0.8,
                "reasons": ["li que é um tema rico"],
                "summary": f"li sobre {topic}.",
                "notes": [
                    {"text": f"{topic}: fato firme do passo {step}", "uncertainty": "baixa"},
                    {"text": f"{topic}: palpite do passo {step}", "uncertainty": "alta"},
                ],
                "questions": [f"o que veio depois do passo {step} em {topic}?"],
            }
            out.update(self.reply)
        text = out if isinstance(out, str) else json.dumps(out)
        return LLMResponse(Message("assistant", text), model="m", provider="t")

    def stream(self, messages: list[Message], **kwargs: Any) -> Any:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


class Calendar:
    def __init__(self) -> None:
        self.now = DAY1

    def __call__(self) -> datetime:
        return self.now

    def next_day(self) -> None:
        self.now += timedelta(days=1)


def ctx(llm: LLMProvider, data: V2Data, clock: Calendar, **interests: Any) -> PhaseContext:
    turns = [TranscriptTurn("sessA", 4, "falei de um tema", at=clock() - timedelta(hours=1))]
    return PhaseContext(
        data=data,
        config=MemoryV2Settings(enabled=True, interests=V2InterestSettings(**interests)),
        material=[SessionMaterial("sessA", turns)],
        budget=Budget(10**9, 10**6),
        cheap=llm,
        reflect=llm,
        embedder=FakeEmbedder(),
        clock=clock,
    )


def levels(data: V2Data) -> dict[str, int]:
    return {i.topic: i.level for i in data.interests.list()}


async def night(llm: Teacher, data: V2Data, clock: Calendar, **interests: Any) -> None:
    await InterestSeedPhase().run(ctx(llm, data, clock, **interests))
    await StudyPhase().run(ctx(llm, data, clock, **interests))


# --- the example: three days ---------------------------------------------------------------------


async def test_day_one_one_topic_day_two_three_topics_day_three_all_advance() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()

    await night(Teacher(["ficção científica"]), data, clock)
    assert levels(data) == {"ficção científica": 1}

    clock.next_day()
    await night(Teacher(["psicodelia", "jardinagem"]), data, clock)
    assert levels(data) == {"ficção científica": 2, "psicodelia": 1, "jardinagem": 1}

    clock.next_day()
    await night(Teacher([]), data, clock)  # no new topic: everyone still moves one step
    assert levels(data) == {"ficção científica": 3, "psicodelia": 2, "jardinagem": 2}


async def test_each_step_receives_the_notes_and_the_open_question_of_the_last() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    llm = Teacher(["psicodelia"])
    await night(llm, data, clock)
    clock.next_day()
    await night(Teacher([]), data, clock)
    second = Teacher([])
    clock.next_day()
    await night(second, data, clock)

    prompt = second.study_calls[0]
    assert "passo 3 do seu estudo" in prompt and focus_of(3) in prompt
    assert "psicodelia: fato firme do passo 1" in prompt  # earlier notes come back
    assert "psicodelia: palpite do passo 2 (incerto)" in prompt  # with the mark of uncertainty
    assert "o que veio depois do passo 2 em psicodelia?" in prompt  # the open question
    assert "o que veio depois do passo 1" not in prompt  # old questions are replaced


async def test_the_focus_changes_with_the_level_and_goes_on_without_a_ceiling() -> None:
    assert [focus_of(n) for n in range(1, 6)] == [FOCUS[n] for n in range(1, 6)]
    assert focus_of(6) == focus_of(40) and focus_of(6) not in FOCUS.values()
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    await night(Teacher(["jazz"]), data, clock)
    for _ in range(11):
        clock.next_day()
        await night(Teacher([]), data, clock)
    assert levels(data) == {"jazz": 12}


# --- limits and order ----------------------------------------------------------------------------


async def test_at_most_ten_topics_a_night_never_studied_first_then_the_strongest() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    book = InterestBook(data, FakeEmbedder(), V2InterestSettings(), clock)
    topics = [
        "xadrez", "balé", "ópera", "origami", "surf", "astronomia",
        "cerâmica", "poesia", "culinária", "ciclismo", "fotografia", "aquarela",
    ]  # fmt: skip
    for n, topic in enumerate(topics):  # saved directly: the fake embedder merges look-alikes
        data.interests.save(Interest(topic, intensity=0.2 + n * 0.05, origin=ORIGIN))
    old = book.find("xadrez")
    assert old is not None
    old.last_studied_at, old.level = DAY1 - timedelta(days=3), 4
    data.interests.save(old)

    await StudyPhase().run(ctx(Teacher(), data, clock))

    done = {t for t, lv in levels(data).items() if lv > 0 and t != "xadrez"}
    assert len(done) == 10
    assert levels(data)["xadrez"] == 4  # the studied one waits behind the never studied...
    assert "balé" not in done  # ...and the weakest of those is the one left out


async def test_the_same_topic_waits_for_the_gap() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    await night(Teacher(["jazz"]), data, clock)
    clock.now += timedelta(hours=10)
    await StudyPhase().run(ctx(Teacher(), data, clock))
    assert levels(data) == {"jazz": 1}
    clock.now += timedelta(hours=9)  # 19h since the last step
    await StudyPhase().run(ctx(Teacher(), data, clock))
    assert levels(data) == {"jazz": 2}


# --- the notes -----------------------------------------------------------------------------------


async def test_notes_carry_uncertainty_source_and_step_and_duplicates_are_dropped() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    same = {"notes": [{"text": "O jazz nasceu em Nova Orleans", "uncertainty": "baixa"}]}
    await night(Teacher(["jazz"], **same), data, clock)
    clock.next_day()
    await night(Teacher([], **same), data, clock)  # the model says the same thing again

    (interest,) = data.interests.list()
    claims = NoteBook(data).claims(interest.id)
    assert [n.text for n in claims] == ["O jazz nasceu em Nova Orleans"]
    assert claims[0].uncertainty == "baixa" and claims[0].source == "modelo"
    assert claims[0].level == 1 and claims[0].embedding  # searchable


async def test_a_note_without_a_valid_uncertainty_counts_as_the_most_uncertain() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    reply = {"notes": ["texto solto", {"text": "outro", "uncertainty": "talvez"}]}
    await night(Teacher(["jazz"], **reply), data, clock)
    (interest,) = data.interests.list()
    assert {n.uncertainty for n in NoteBook(data).claims(interest.id)} == {"alta"}


async def test_the_notes_of_a_topic_are_capped_dropping_the_oldest_least_reliable() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    book = InterestBook(data, FakeEmbedder(), V2InterestSettings(), clock)
    interest, _ = book.seed("jazz", ORIGIN)
    notes = NoteBook(data, FakeEmbedder(), V2InterestSettings(max_notes_per_topic=5), clock)
    kinds = ["alta", "baixa", "alta", "média", "baixa", "alta", "baixa"]
    names = ["swing", "bebop", "improviso", "blues", "saxofone", "trompete", "ritmo"]
    for name, uncertainty in zip(names, kinds, strict=True):
        clock.now += timedelta(minutes=1)
        assert notes.add_claim(interest, name, uncertainty, 1)

    assert notes.consolidate(interest) == 2
    left = [n.text for n in notes.claims(interest.id)]
    assert "swing" not in left and "improviso" not in left  # the oldest "alta" ones go first
    assert len(left) == 5 and "bebop" in left  # the reliable ones survive


async def test_a_step_that_claims_to_have_lived_it_is_refused_whole() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    lived = {"notes": [{"text": "Eu toquei esse disco ontem", "uncertainty": "baixa"}]}
    llm = Teacher(["jazz"], **lived)
    await InterestSeedPhase().run(ctx(llm, data, clock))
    result = await StudyPhase().run(ctx(llm, data, clock))

    (interest,) = data.interests.list()
    assert interest.level == 0 and interest.last_studied_at is None
    assert data.notes.count() == 0 and "vivencia×1" in result.summary


async def test_long_notes_are_trimmed_not_lost() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    long_note = {"notes": [{"text": "o jazz é muito rico " * 30, "uncertainty": "baixa"}]}
    await night(Teacher(["jazz"], **long_note), data, clock)
    (note,) = NoteBook(data).claims(data.interests.list()[0].id)
    assert len(note.text) <= 220 and note.text.endswith("…")


async def test_the_users_opinion_never_reaches_the_study_prompt() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    llm = Teacher(["Beatles"])
    turns = [TranscriptTurn("sessA", 4, "odeio os Beatles, detesto", at=clock())]
    c = ctx(llm, data, clock)
    c.material = [SessionMaterial("sessA", turns)]
    await InterestSeedPhase().run(c)
    await StudyPhase().run(ctx(llm, data, clock))
    assert llm.study_calls and all("odeio" not in p and "detesto" not in p for p in llm.study_calls)


# --- technical topics ----------------------------------------------------------------------------


def test_technical_topics_are_recognised_by_whole_words() -> None:
    terms = V2InterestSettings().excluded_terms
    assert is_technical("Programação em Python", terms) and is_technical("Docker", terms)
    assert is_technical("banco de dados", terms) and is_technical("CÓDIGO limpo", terms)
    assert not is_technical("jazz", terms) and not is_technical("gitano", terms)
    rejection = check_interest("Docker", excluded=terms)
    assert rejection is not None and rejection.code == "tecnico"
    assert check_interest("Docker") is None  # the guard alone does not know the list


async def test_a_technical_topic_is_not_seeded_and_an_old_one_is_not_studied() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    llm = Teacher(["Docker", "jazz"])
    result = await InterestSeedPhase().run(ctx(llm, data, clock))
    assert [i.topic for i in data.interests.list()] == ["jazz"] and "tecnico×1" in result.summary

    book = InterestBook(data, FakeEmbedder(), V2InterestSettings(), clock)
    book.seed("Kubernetes", ORIGIN)  # e.g. taught before the rule, or by hand
    await StudyPhase().run(ctx(llm, data, clock))
    assert levels(data) == {"jazz": 1, "Kubernetes": 0}


async def test_the_excluded_list_is_configurable() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    await InterestSeedPhase().run(ctx(Teacher(["Docker"]), data, clock, excluded_terms=[]))
    assert [i.topic for i in data.interests.list()] == ["Docker"]


# --- forgetting, export, dry-run -----------------------------------------------------------------


async def test_forgetting_an_interest_deletes_its_notes() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    await night(Teacher(["jazz", "rock"]), data, clock)
    jazz = InterestBook(data).find("jazz")
    assert jazz is not None and data.notes.count() == 6

    InterestBook(data).forget(jazz.id)

    assert {n.interest_id for n in data.notes.list()} == {InterestBook(data).find("rock").id}  # type: ignore[union-attr]


async def test_the_export_carries_the_notes() -> None:
    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    await night(Teacher(["jazz"]), data, clock)
    out = export_v2(data)
    assert len(out["study_notes"]) == 3 and "embedding" not in out["study_notes"][0]
    assert out["interests"][0]["level"] == 1


async def test_a_dry_run_through_the_overlay_writes_no_note_and_no_level() -> None:
    from brain.memory.v2.overlay import OverlayDocStore

    data, clock = V2Data.over(InMemoryDocStore()), Calendar()
    await InterestSeedPhase().run(ctx(Teacher(["jazz"]), data, clock))
    overlay = OverlayDocStore(data.docs)
    await StudyPhase().run(ctx(Teacher(), V2Data.over(overlay), clock))
    assert data.notes.count() == 0 and levels(data) == {"jazz": 0}
    assert overlay.changes()  # but it knows what it would have written


# --- the commands --------------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> V2Data:
    data = V2Data.over(InMemoryDocStore())
    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", lambda: None)
    monkeypatch.setattr("brain.clients.memory_v2_cli.open_v2_data", lambda: data)
    from brain.clients.cli import console

    monkeypatch.setattr(console, "width", 300, raising=False)
    return data


async def studied(data: V2Data) -> None:
    await night(Teacher(["jazz"]), data, Calendar())


async def test_knowledge_lists_shows_and_forgets(cli: V2Data) -> None:
    runner = CliRunner()
    assert "Ainda não estudei" in runner.invoke(app, ["memory", "knowledge"]).output
    await studied(cli)

    listing = runner.invoke(app, ["memory", "knowledge"]).output
    assert "jazz" in listing and "segunda mão" in listing

    shown = runner.invoke(app, ["memory", "knowledge", "show", "jazz"]).output
    assert "fato firme do passo 1" in shown and "baixa" in shown and "alta" in shown
    assert "pergunta" in shown and "modelo" in shown

    declined = runner.invoke(app, ["memory", "knowledge", "forget", "jazz"], input="n\n")
    assert "não apaguei nada" in declined.output and cli.notes.count() == 3
    done = runner.invoke(app, ["memory", "knowledge", "forget", "jazz", "--yes"])
    assert done.exit_code == 0 and cli.notes.count() == 0
    assert InterestBook(cli).find("jazz").level == 0  # type: ignore[union-attr]
    assert InterestBook(cli).find("jazz") is not None  # the interest itself stays


def test_knowledge_of_an_unknown_topic_says_so(cli: V2Data) -> None:
    for sub in ("show", "forget"):
        out = CliRunner().invoke(
            app, ["memory", "knowledge", sub, "xadrez", "--yes"][: 4 if sub == "forget" else 4]
        )
        assert out.exit_code in (1, 2)


def test_a_note_validates_its_fields() -> None:
    from brain.memory.v2.models import V2DataError

    with pytest.raises(V2DataError):
        StudyNote("int_x", "texto", uncertainty="talvez").validate()
    with pytest.raises(V2DataError):
        StudyNote(
            "int_x",
            "",
        ).validate()
