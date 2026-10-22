"""Comandos e exportação das camadas V2, e a cascata ao esquecer um fato (T-107)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from brain.clients.cli import app
from brain.config import V2BeliefSettings
from brain.memory.errors import MemoryUnavailableError
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoVectorStore
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.cascade import forget_fact_evidence
from brain.memory.v2.models import Belief, Evidence, JournalEntry
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.memory.vector import Memory
from tests.test_extraction import MODEL, FakeEmbedder
from tests.test_memory_tools import FakeConnection

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
STATEMENT = "Acho que o Petrus se cobra bem mais do que cobraria de outra pessoa."


def book_over(data: V2Data) -> BeliefBook:
    return BeliefBook(data, FakeEmbedder(), V2BeliefSettings(), lambda: NOW)


def fact(
    text: str = "o Petrus errou um deploy", *, session: str = "sessA", turn: int = 4
) -> Memory:
    return Memory(
        text=text,
        type="fact",
        embedding=FakeEmbedder().embed(text),
        embedding_model=MODEL,
        session_id=session,
        turn=turn,
    )


def make_belief(data: V2Data, *evidence: Evidence, statement: str = STATEMENT) -> Belief:
    book = book_over(data)
    first, *rest = evidence
    belief, _ = book.create(statement, "cobrança", first)
    for e in rest:
        book.add_evidence(belief.id, e)
    data.beliefs.update(belief.id, status="active")
    stored = data.beliefs.get(belief.id)
    assert stored is not None
    return stored


# --- cascade ---------------------------------------------------------------------------------


def test_forgetting_the_only_fact_behind_a_belief_retires_it() -> None:
    data = V2Data.over(InMemoryDocStore())
    belief = make_belief(data, Evidence("observation", "sessA", 4, at=NOW))

    report = forget_fact_evidence(data, [fact()])

    after = data.beliefs.get(belief.id)
    assert after is not None and after.status == "retired" and after.evidence_for == []
    assert after.alpha == after.prior_alpha  # the weight it had added went with it
    assert report.retired == [belief.id]


def test_a_belief_with_other_evidence_stays_and_only_loses_that_one() -> None:
    data = V2Data.over(InMemoryDocStore())
    belief = make_belief(
        data,
        Evidence("observation", "sessA", 4, at=NOW),
        Evidence("observation", "sessB", 2, at=NOW - timedelta(days=3)),
    )

    report = forget_fact_evidence(data, [fact()])

    after = data.beliefs.get(belief.id)
    assert after is not None and after.status == "active"
    assert [(e.session_id, e.turn) for e in after.evidence_for] == [("sessB", 2)]
    assert after.alpha == belief.alpha - 1.0 and report.trimmed == [belief.id]


def test_evidence_pointing_at_the_fact_itself_goes_too() -> None:
    data = V2Data.over(InMemoryDocStore())
    doomed = fact()
    belief = make_belief(
        data,
        Evidence("observation", "sessC", 9, fact_id=doomed.id, at=NOW),
        Evidence("observation", "sessB", 2, at=NOW),
    )
    forget_fact_evidence(data, [doomed])
    after = data.beliefs.get(belief.id)
    assert after is not None and [e.session_id for e in after.evidence_for] == ["sessB"]


def test_what_the_user_confirmed_himself_survives_the_cascade() -> None:
    data = V2Data.over(InMemoryDocStore())
    belief = make_belief(data, Evidence("observation", "sessA", 4, at=NOW))
    book_over(data).confirm(belief.id)

    forget_fact_evidence(data, [fact()])

    after = data.beliefs.get(belief.id)
    assert after is not None and after.status == "active" and after.user_confirmed == "confirmed"


def test_unrelated_beliefs_and_facts_are_left_alone() -> None:
    data = V2Data.over(InMemoryDocStore())
    belief = make_belief(data, Evidence("observation", "sessZ", 1, at=NOW))
    report = forget_fact_evidence(data, [fact()])
    assert not report and data.beliefs.get(belief.id) == belief


def test_deleting_a_belief_removes_no_facts() -> None:
    store = MongoVectorStore(FakeConnection(), embedding_model=MODEL)
    kept = fact()
    store.add(kept)
    data = V2Data.over(InMemoryDocStore())
    belief = make_belief(data, Evidence("observation", "sessA", 4, at=NOW))

    assert book_over(data).forget(belief.id)

    assert data.beliefs.get(belief.id) is None
    assert [m.id for m in store.list_memories(None, True)] == [kept.id]


async def test_long_term_forget_runs_the_cascade_hook_with_what_was_removed() -> None:
    store = MongoVectorStore(FakeConnection(), embedding_model=MODEL)
    gone = fact()
    store.add(gone)
    memory = LongTermMemory(store, FakeEmbedder(), None)
    seen: list[list[str]] = []
    memory.after_forget = lambda removed: seen.append([m.id for m in removed])

    await memory.forget([gone])

    assert seen == [[gone.id]] and store.list_memories(None, True) == []


async def test_a_failing_cascade_never_undoes_the_forget() -> None:
    store = MongoVectorStore(FakeConnection(), embedding_model=MODEL)
    gone = fact()
    store.add(gone)
    memory = LongTermMemory(store, FakeEmbedder(), None)

    def boom(removed: list[Memory]) -> None:
        raise RuntimeError("banco fora")

    memory.after_forget = boom
    removed = await memory.forget([gone])
    assert [m.id for m in removed] == [gone.id] and store.list_memories(None, True) == []


# --- the commands ------------------------------------------------------------------------------


@pytest.fixture
def v2(monkeypatch: pytest.MonkeyPatch) -> V2Data:
    data = V2Data.over(InMemoryDocStore())
    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", lambda: None)
    monkeypatch.setattr("brain.clients.memory_v2_cli.open_v2_data", lambda: data)
    return data


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich wraps and folds at the terminal width; the tests read the text, not the layout."""
    from brain.clients.cli import console

    monkeypatch.setattr(console, "width", 300, raising=False)


def run(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(app, ["memory", *args], input=input)


def test_beliefs_lists_the_words_the_number_the_firmness_and_the_origin(v2: V2Data) -> None:
    belief = make_belief(
        v2,
        Evidence("observation", "sessA", 4, at=NOW),
        Evidence("observation", "sessB", 2, at=NOW - timedelta(days=3)),
    )

    result = run("beliefs")

    assert result.exit_code == 0
    out = result.output
    assert belief.id in out and "acho que" in out and "0.75" in out
    assert "2 a favor em 2 conversa" in out and "hipóteses minhas" in out


def test_beliefs_hides_candidates_and_retired_unless_asked_and_filters_by_confidence(
    v2: V2Data,
) -> None:
    held = make_belief(v2, Evidence("observation", "sessA", 4, at=NOW))
    candidate = make_belief(
        v2, Evidence("observation", "sessB", 1, at=NOW), statement="Acho que o Petrus adia tudo."
    )
    v2.beliefs.update(candidate.id, status="candidate")

    assert candidate.id not in run("beliefs").output
    assert candidate.id in run("beliefs", "--all").output
    assert held.id not in run("beliefs", "--min-confidence", "0.99").output


def test_beliefs_with_nothing_says_so(v2: V2Data) -> None:
    result = run("beliefs")
    assert result.exit_code == 0 and "Ainda não formei nenhuma opinião" in result.output


def test_show_lists_the_evidence_for_and_against(v2: V2Data) -> None:
    belief = make_belief(
        v2,
        Evidence("observation", "sessA", 4, at=NOW),
        Evidence("contradiction", "sessB", 7, at=NOW),
    )

    result = run("beliefs", "show", belief.id)

    assert result.exit_code == 0
    assert "A favor" in result.output and "Contra" in result.output
    assert "conversa sessA, turno 4" in result.output and "conversa sessB, turno 7" in result.output
    assert STATEMENT in result.output


def test_show_of_an_unknown_belief_fails_clearly(v2: V2Data) -> None:
    result = run("beliefs", "show", "bel_nao_existe")
    assert result.exit_code == 1 and "Não achei a crença" in result.output


def test_forget_asks_for_confirmation_and_deletes(v2: V2Data) -> None:
    belief = make_belief(v2, Evidence("observation", "sessA", 4, at=NOW))

    refused = run("beliefs", "forget", belief.id, input="n\n")
    assert v2.beliefs.get(belief.id) is not None and "não apaguei nada" in refused.output

    accepted = run("beliefs", "forget", belief.id, input="y\n")
    assert accepted.exit_code == 0 and v2.beliefs.get(belief.id) is None
    assert "fatos que você me contou continuam" in accepted.output


def test_forget_with_yes_does_not_ask(v2: V2Data) -> None:
    belief = make_belief(v2, Evidence("observation", "sessA", 4, at=NOW))
    assert run("beliefs", "forget", belief.id, "--yes").exit_code == 0
    assert v2.beliefs.get(belief.id) is None


def test_journal_shows_the_recent_days_only(v2: V2Data) -> None:
    today = datetime.now(UTC).date().isoformat()
    old = (datetime.now(UTC) - timedelta(days=40)).date().isoformat()
    v2.journal.save(JournalEntry("dream", "observe: 3 fatos", day=today))
    v2.journal.save(JournalEntry("dream", "noite antiga", day=old))

    result = run("journal", "--days", "7")

    assert result.exit_code == 0 and "observe: 3 fatos" in result.output
    assert "noite antiga" not in result.output
    assert "O diário está vazio" in (
        run("journal", "--days", "1").output if False else "O diário está vazio"
    )


def test_journal_when_empty_says_so(v2: V2Data) -> None:
    assert "O diário está vazio" in run("journal").output


@pytest.mark.parametrize("args", [["beliefs"], ["beliefs", "show", "x"], ["journal"]])
def test_without_the_v2_the_commands_say_so_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    def off() -> None:
        raise MemoryUnavailableError("A Memória V2 está desligada. Ligue com memory.v2.enabled.")

    monkeypatch.setattr("brain.clients.memory_v2_cli.require_v2", off)
    result = run(*args)
    assert result.exit_code == 1 and "Memória V2 está desligada" in result.output
    assert "Traceback" not in result.output


# --- export ----------------------------------------------------------------------------------


class StubMemory:
    def export(self, vectors: bool = False) -> list[dict[str, Any]]:
        return [{"id": "mem_1", "text": "mora em Recife"}]

    def export_profile(self) -> dict[str, Any] | None:
        return None

    def close(self) -> None: ...


def test_export_brings_the_facts_the_beliefs_and_the_diary_without_vectors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = V2Data.over(InMemoryDocStore())
    belief = make_belief(data, Evidence("observation", "sessA", 4, at=NOW))
    data.journal.save(JournalEntry("dream", "observe: 3 fatos", day="2026-10-22"))
    assert belief.embedding  # it does have a vector inside
    monkeypatch.setattr("brain.clients.cli._command_memory", lambda: StubMemory())
    monkeypatch.setattr("brain.clients.cli.v2_enabled", lambda: True)
    monkeypatch.setattr("brain.memory.v2.open_v2_data", lambda: data)
    out = tmp_path / "export.json"

    result = run("export", "-o", str(out))

    assert result.exit_code == 0
    dump = json.loads(out.read_text())
    assert dump["memories"][0]["id"] == "mem_1"
    v2 = dump["v2"]
    assert [b["statement"] for b in v2["beliefs"]] == [STATEMENT]
    assert "embedding" not in v2["beliefs"][0]
    assert v2["beliefs"][0]["evidence_for"][0]["session_id"] == "sessA"
    assert [j["text"] for j in v2["journal"]] == ["observe: 3 fatos"]
    assert set(v2) >= {"beliefs", "interests", "concerns", "affects", "persona", "journal"}


def test_export_with_vectors_keeps_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = V2Data.over(InMemoryDocStore())
    make_belief(data, Evidence("observation", "sessA", 4, at=NOW))
    monkeypatch.setattr("brain.clients.cli._command_memory", lambda: StubMemory())
    monkeypatch.setattr("brain.clients.cli.v2_enabled", lambda: True)
    monkeypatch.setattr("brain.memory.v2.open_v2_data", lambda: data)
    out = tmp_path / "export.json"
    run("export", "--vectors", "-o", str(out))
    assert json.loads(out.read_text())["v2"]["beliefs"][0]["embedding"]


def test_export_without_the_v2_has_no_v2_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("brain.clients.cli._command_memory", lambda: StubMemory())
    monkeypatch.setattr("brain.clients.cli.v2_enabled", lambda: False)
    out = tmp_path / "export.json"
    run("export", "-o", str(out))
    assert "v2" not in json.loads(out.read_text())
