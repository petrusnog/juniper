"""Base da Memória V2 (T-100): configuração, modelos, armazenamento e proveniência.

O armazenamento roda nos mesmos testes de contrato em MongoDB (`mongomock`, sem servidor) e em
memória; os dois têm de se comportar igual.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import mongomock
import pytest
from pydantic import ValidationError

from brain.config import LLMReflectionSettings, MemoryV2Settings, Settings, settings
from brain.core.session import Session, Turn
from brain.memory.errors import MemoryUnavailableError
from brain.memory.mongo import MongoConnection
from brain.memory.reindex import reindex_v2
from brain.memory.v2 import NEEDS_MONGO, require_v2, v2_enabled, v2_unavailable_reason
from brain.memory.v2.models import (
    Affect,
    Belief,
    Concern,
    ConcernChange,
    DreamRun,
    Evidence,
    Interest,
    JournalEntry,
    PersonaState,
    Trait,
    TranscriptTurn,
    V2DataError,
    Watermark,
    as_dt,
)
from brain.memory.v2.semantic import semantic_search
from brain.memory.v2.store import DocStore, InMemoryDocStore, MongoDocStore, V2Data

# --- configuration ----------------------------------------------------------------------------


def test_the_v2_is_off_by_default_with_the_documented_defaults() -> None:
    v2 = MemoryV2Settings()
    assert v2.enabled is False and v2.max_tokens == 600
    assert (v2.night.at, v2.night.max_tokens, v2.night.max_minutes) == ("03:00", 60_000, 20)
    assert (v2.beliefs.half_life_days, v2.beliefs.min_evidence, v2.beliefs.max_new_per_night) == (
        60,
        2,
        5,
    )
    assert v2.interests.max_per_night == 2
    assert (v2.affect.enabled, v2.affect.intensity) == (True, 1.0)
    assert (v2.persona.evolves, v2.persona.max_step) == (True, 0.03)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("NIGHT__AT", "25:00"),
        ("NIGHT__AT", "de madrugada"),
        ("NIGHT__MAX_MINUTES", "0"),
        ("BELIEFS__HALF_LIFE_DAYS", "0.5"),
        ("BELIEFS__MIN_EVIDENCE", "0"),
        ("AFFECT__INTENSITY", "1.5"),
        ("AFFECT__INTENSITY", "-0.1"),
        ("PERSONA__MAX_STEP", "0.9"),
        ("INTERESTS__MAX_PER_NIGHT", "99"),
    ],
)
def test_v2_options_out_of_range_are_refused(
    monkeypatch: pytest.MonkeyPatch, path: str, value: str
) -> None:
    monkeypatch.setenv(f"JUNIPER_MEMORY__V2__{path}", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_night_at_is_normalized() -> None:
    assert MemoryV2Settings(night={"at": "3:5"}).night.at == "03:05"  # type: ignore[arg-type]


def test_the_reflection_model_is_optional_and_the_provider_must_be_known() -> None:
    assert LLMReflectionSettings().provider is None and LLMReflectionSettings().model is None
    assert LLMReflectionSettings(provider="groq", model="x").provider == "groq"
    with pytest.raises(ValidationError):
        LLMReflectionSettings(provider="openai")


def test_enabling_the_v2_with_files_is_refused_with_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.memory.v2, "enabled", True)
    monkeypatch.setattr(settings.memory.session, "store", "file")

    assert v2_unavailable_reason() == NEEDS_MONGO and not v2_enabled()
    with pytest.raises(MemoryUnavailableError, match="MongoDB"):
        require_v2()


def test_the_v2_runs_with_mongo_and_says_it_is_off_when_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.memory.session, "store", "mongo")
    monkeypatch.setattr(settings.memory.v2, "enabled", False)
    assert not v2_enabled() and v2_unavailable_reason() is None
    with pytest.raises(MemoryUnavailableError, match="desligada"):
        require_v2()
    monkeypatch.setattr(settings.memory.v2, "enabled", True)
    assert v2_enabled()
    require_v2()


# --- models -----------------------------------------------------------------------------------

NOW = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)


def make_belief(**kw: Any) -> Belief:
    base: dict[str, Any] = {
        "statement": "Acho que o Petrus se cobra demais.",
        "topic": "cobrança",
        "evidence_for": [Evidence("observation", "s1", 3, None, 1.0, NOW)],
        "evidence_against": [Evidence("contradiction", "s2", 1, None, 1.0, NOW)],
        "alpha": 2.0,
        "beta": 1.0,
        "embedding": [1.0, 0.0, 0.0],
        "embedding_model": "m1",
        "created_at": NOW,
        "last_reviewed_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return Belief(**base)


MODELS: list[tuple[str, Callable[[], Any], type]] = [
    ("belief", make_belief, Belief),
    (
        "interest",
        lambda: Interest(
            "Beatles",
            intensity=0.6,
            stance="mixed",
            reasons=["melodia"],
            origin=Evidence("observation", "s1", 2, None, 1.0, NOW),
            last_studied_at=NOW,
            embedding=[0.0, 1.0],
            embedding_model="m1",
            created_at=NOW,
            updated_at=NOW,
        ),
        Interest,
    ),
    (
        "affect",
        lambda: Affect(
            "tristeza",
            -0.7,
            0.6,
            ["fact:mem_1", "concern:bem-estar"],
            occurred_at=NOW,
            created_at=NOW,
        ),
        Affect,
    ),
    (
        "concern",
        lambda: Concern(
            "bem-estar",
            "o bem-estar do Petrus",
            0.9,
            0.9,
            history=[ConcernChange(NOW, 0.02, "evento", "evt_1")],
            updated_at=NOW,
        ),
        Concern,
    ),
    (
        "journal",
        lambda: JournalEntry("dream", "Estudei X.", day="2026-10-22", data={"n": 1}, at=NOW),
        JournalEntry,
    ),
    (
        "run",
        lambda: DreamRun(
            started_at=NOW,
            finished_at=NOW,
            status="done",
            phases=[{"name": "observe"}],
            tokens_used=10,
            sessions_read=2,
        ),
        DreamRun,
    ),
    ("watermark", lambda: Watermark("s1", 4, NOW), Watermark),
    ("transcript", lambda: TranscriptTurn("s1", 3, "oi", "olá", used_tools=True, at=NOW), TranscriptTurn),
]


@pytest.mark.parametrize(("name", "make", "cls"), MODELS, ids=[m[0] for m in MODELS])
def test_every_model_survives_a_round_trip(name: str, make: Callable[[], Any], cls: type) -> None:
    original = make()
    assert cls.from_doc(original.to_doc()) == original


def test_persona_state_round_trips_and_keeps_traits_in_range() -> None:
    state = PersonaState(
        traits={"brincadeira": Trait(0.6, 0.5, 0.2, 0.9)},
        recent=["Fiquei pensando na morte."],
        version=3,
        changed_by=["evt_1"],
        updated_at=NOW,
    )
    assert PersonaState.from_doc(state.to_doc()) == state
    with pytest.raises(V2DataError):
        PersonaState(traits={"x": Trait(0.95, 0.5, 0.2, 0.9)}).to_doc()  # outside [min, max]
    with pytest.raises(V2DataError):
        PersonaState(recent=["a", "b", "c", "d"]).to_doc()  # at most three lines


def test_belief_evidence_total_is_derived_from_alpha_and_beta() -> None:
    assert make_belief(alpha=3.0, beta=2.0).n == 3.0
    assert make_belief().to_doc()["n"] == 1.0


@pytest.mark.parametrize(
    "bad",
    [
        {"statement": "  "},
        {"about": "terceiro"},
        {"status": "talvez"},
        {"origin": "sonho"},
        {"user_confirmed": "quem sabe"},
        {"alpha": 0.0},
    ],
)
def test_invalid_beliefs_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(V2DataError):
        make_belief(**bad).to_doc()


def test_other_models_refuse_out_of_range_values() -> None:
    with pytest.raises(V2DataError):
        Interest("x", intensity=1.5).to_doc()
    with pytest.raises(V2DataError):
        Interest("x", stance="adoro").to_doc()
    with pytest.raises(V2DataError):
        Affect("alegria", 1.5, 0.5).to_doc()
    with pytest.raises(V2DataError):
        Affect("alegria", 0.5, 0.5, about="terceiro").to_doc()
    with pytest.raises(V2DataError):
        Concern("a", "A", 1.2, 0.5).to_doc()
    with pytest.raises(V2DataError):
        JournalEntry("sonho", "x").to_doc()
    with pytest.raises(V2DataError):
        TranscriptTurn("s1", -1, "x").to_doc()


@pytest.mark.parametrize(
    "cls", [Belief, Interest, Affect, Concern, JournalEntry, DreamRun, TranscriptTurn]
)
def test_a_damaged_document_raises_a_typed_error(cls: type) -> None:
    with pytest.raises(V2DataError):
        cls.from_doc({"_id": "x", "alpha": "não é número", "created_at": "não é data"})


def test_dates_come_back_timezone_aware() -> None:
    assert as_dt(datetime(2026, 1, 1)).tzinfo is UTC
    assert as_dt("2026-01-01T00:00:00").tzinfo is UTC
    with pytest.raises(V2DataError):
        as_dt("ontem")


# --- document store contract (MongoDB and memory behave the same) -----------------------------


def _mongo_store() -> MongoDocStore:
    connection = MongoConnection()
    connection._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return MongoDocStore(connection)


@pytest.fixture(params=["memory", "mongo"])
def docs(request: pytest.FixtureRequest) -> Iterator[DocStore]:
    yield InMemoryDocStore() if request.param == "memory" else _mongo_store()


def test_both_stores_satisfy_the_interface() -> None:
    assert isinstance(InMemoryDocStore(), DocStore)
    assert isinstance(_mongo_store(), DocStore)


def test_put_get_replace_and_delete(docs: DocStore) -> None:
    docs.put("c", {"_id": "a", "v": 1})
    docs.put("c", {"_id": "a", "v": 2})
    assert docs.get("c", "a") == {"_id": "a", "v": 2}
    assert docs.get("c", "nao-existe") is None and docs.count("c") == 1
    assert docs.delete("c", "a") is True and docs.delete("c", "a") is False


def test_find_filters_sorts_and_limits(docs: DocStore) -> None:
    for i, status in enumerate(["active", "retired", "active", "candidate"]):
        docs.put("c", {"_id": f"d{i}", "status": status, "n": i, "nested": {"k": i % 2}})

    assert {d["_id"] for d in docs.find("c", {"status": "active"})} == {"d0", "d2"}
    assert [
        d["_id"] for d in docs.find("c", {"status": {"$in": ["active", "candidate"]}}, [("n", -1)])
    ] == ["d3", "d2", "d0"]
    assert [d["_id"] for d in docs.find("c", {"n": {"$gte": 1, "$lt": 3}}, [("n", 1)])] == [
        "d1",
        "d2",
    ]
    assert [d["_id"] for d in docs.find("c", {"status": {"$ne": "active"}}, [("n", 1)])] == [
        "d1",
        "d3",
    ]
    assert {d["_id"] for d in docs.find("c", {"nested.k": 1})} == {"d1", "d3"}
    assert [d["_id"] for d in docs.find("c", None, [("n", -1)], limit=2)] == ["d3", "d2"]
    assert docs.count("c", {"status": "active"}) == 2


def test_update_sets_fields_and_reports_a_missing_document(docs: DocStore) -> None:
    docs.put("c", {"_id": "a", "v": 1, "keep": True})
    assert docs.update("c", "a", {"v": 9}) is True
    assert docs.get("c", "a") == {"_id": "a", "v": 9, "keep": True}
    assert docs.update("c", "nope", {"v": 1}) is False


def test_delete_many_removes_only_the_matches(docs: DocStore) -> None:
    for i in range(4):
        docs.put("c", {"_id": f"d{i}", "even": i % 2 == 0})
    assert docs.delete_many("c", {"even": True}) == 2
    assert [d["_id"] for d in docs.find("c", None, [("_id", 1)])] == ["d1", "d3"]


def test_versioned_put_detects_a_concurrent_writer(docs: DocStore) -> None:
    assert docs.put_if_version("p", {"_id": "current", "version": 1}, None) is True
    assert (
        docs.put_if_version("p", {"_id": "current", "version": 9}, None) is False
    )  # already exists
    assert docs.put_if_version("p", {"_id": "current", "version": 2}, 1) is True
    assert docs.put_if_version("p", {"_id": "current", "version": 3}, 1) is False  # stale
    assert (
        docs.put_if_version("p", {"_id": "ghost", "version": 2}, 1) is False
    )  # nothing to replace
    assert docs.get("p", "current") == {"_id": "current", "version": 2}


def test_documents_are_copied_in_and_out_of_memory() -> None:
    store = InMemoryDocStore()
    doc = {"_id": "a", "items": [1]}
    store.put("c", doc)
    doc["items"].append(2)
    got = store.get("c", "a")
    assert got == {"_id": "a", "items": [1]}
    got["items"].append(3)  # type: ignore[index]
    assert store.get("c", "a") == {"_id": "a", "items": [1]}


# --- repositories -----------------------------------------------------------------------------


@pytest.fixture
def data(docs: DocStore) -> V2Data:
    return V2Data.over(docs)


def test_repos_round_trip_and_list_newest_first(data: V2Data) -> None:
    older = make_belief(statement="Acho que A.", created_at=NOW)
    newer = make_belief(statement="Acho que B.", created_at=NOW + timedelta(days=1))
    data.beliefs.save(older)
    data.beliefs.save(newer)

    assert data.beliefs.get(older.id) == older
    listed = data.beliefs.list(sort=[("created_at", -1)])
    assert [b.statement for b in listed] == ["Acho que B.", "Acho que A."]
    assert data.beliefs.count() == 2 and data.beliefs.count({"status": "retired"}) == 0


def test_a_damaged_document_does_not_hide_the_good_ones(data: V2Data) -> None:
    good = make_belief()
    data.beliefs.save(good)
    data.docs.put("beliefs", {"_id": "bel_ruim", "statement": "", "created_at": NOW})

    assert [b.id for b in data.beliefs.list()] == [good.id]
    assert data.beliefs.get("bel_ruim") is None


def test_update_refreshes_updated_at(data: V2Data) -> None:
    belief = make_belief(updated_at=NOW)
    data.beliefs.save(belief)

    assert data.beliefs.update(belief.id, status="active") is True

    got = data.beliefs.get(belief.id)
    assert got is not None and got.status == "active" and got.updated_at > NOW
    assert data.beliefs.update("nope", status="active") is False


def test_delete_and_delete_where(data: V2Data) -> None:
    for status in ("active", "retired", "retired"):
        data.beliefs.save(make_belief(status=status))
    assert data.beliefs.delete_where({"status": "retired"}) == 2
    (left,) = data.beliefs.list()
    assert data.beliefs.delete(left.id) is True and data.beliefs.count() == 0


def test_the_personality_is_written_with_optimistic_versions_and_keeps_history(
    data: V2Data,
) -> None:
    first = PersonaState(traits={"carinho": Trait(0.5, 0.5)}, version=1, updated_at=NOW)
    assert data.persona.current() is None
    assert data.persona.save(first, None) is True

    second = PersonaState(
        traits={"carinho": Trait(0.52, 0.5)}, version=2, changed_by=["evt"], updated_at=NOW
    )
    assert data.persona.save(second, expected_version=1, reason="evento marcante") is True
    stale = PersonaState(traits={"carinho": Trait(0.9, 0.5)}, version=2, updated_at=NOW)
    assert data.persona.save(stale, expected_version=1) is False  # somebody wrote first

    current = data.persona.current()
    assert current is not None and current.version == 2 and current.traits["carinho"].value == 0.52
    (old,) = data.persona.history.list()
    assert old.state.version == 1 and old.reason == "evento marcante"


def test_every_layer_has_its_collection(data: V2Data) -> None:
    data.interests.save(Interest("Beatles"))
    data.affects.save(Affect("alegria", 0.5, 0.5))
    data.concerns.save(Concern("c", "C", 0.5, 0.5))
    data.journal.save(JournalEntry("note", "oi"))
    data.runs.save(DreamRun())
    data.watermarks.save(Watermark("s1", 2))
    data.transcripts.save(TranscriptTurn("s1", 0, "oi"))

    names = {
        n: data.docs.count(n)
        for n in (
            "interests",
            "affects",
            "concerns",
            "journal",
            "dream_runs",
            "dream_watermarks",
            "transcript_turns",
        )
    }
    assert set(names.values()) == {1}


def test_a_transcript_turn_written_twice_is_one_document(data: V2Data) -> None:
    data.transcripts.save(TranscriptTurn("s1", 4, "oi", "olá"))
    data.transcripts.save(TranscriptTurn("s1", 4, "oi", "olá"))
    data.transcripts.save(TranscriptTurn("s1", 5, "e aí"))

    turns = data.transcripts.list({"session_id": "s1"}, [("index", 1)])
    assert [t.index for t in turns] == [4, 5]


# --- semantic search --------------------------------------------------------------------------


def test_semantic_search_ranks_by_meaning_and_ignores_other_models() -> None:
    near = make_belief(statement="perto", embedding=[1.0, 0.1], embedding_model="m1")
    far = make_belief(statement="longe", embedding=[0.0, 1.0], embedding_model="m1")
    other_model = make_belief(statement="outro modelo", embedding=[1.0, 0.1], embedding_model="m2")
    wrong_size = make_belief(statement="tamanho errado", embedding=[1.0], embedding_model="m1")

    hits = semantic_search(
        [far, other_model, wrong_size, near], [1.0, 0.0], model="m1", top_k=5, threshold=0.5
    )

    assert [h.item.statement for h in hits] == ["perto"]
    assert hits[0].score > 0.9
    all_hits = semantic_search([far, near], [1.0, 0.0], model="m1", top_k=1, threshold=-1)
    assert [h.item.statement for h in all_hits] == ["perto"]
    assert (
        semantic_search([], [1.0], model="m1") == []
        and semantic_search([near], [1.0, 0.0], model="m1", top_k=0) == []
    )


# --- reindex ----------------------------------------------------------------------------------


class _Embedder:
    model_id = "novo-modelo"

    def embed_batch(self, texts: list[str], kind: str) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]


def test_reindex_v2_refreshes_beliefs_and_interests_made_by_another_model(data: V2Data) -> None:
    stale = make_belief(embedding_model="velho")
    fresh = make_belief(embedding_model="novo-modelo", statement="Acho que C.")
    interest = Interest("Beatles", embedding_model="velho", embedding=[1.0, 0.0])
    for belief in (stale, fresh):
        data.beliefs.save(belief)
    data.interests.save(interest)

    result = reindex_v2(data.docs, _Embedder())

    assert (result.total, result.reindexed, result.skipped) == (3, 2, 1)
    got = data.beliefs.get(stale.id)
    assert (
        got is not None
        and got.embedding_model == "novo-modelo"
        and got.embedding == [float(len(stale.statement)), 1.0]
    )
    got_interest = data.interests.get(interest.id)
    assert got_interest is not None and got_interest.embedding_model == "novo-modelo"
    assert reindex_v2(data.docs, _Embedder()).reindexed == 0  # safe to re-run
    assert reindex_v2(data.docs, _Embedder(), force=True).reindexed == 3


# --- provenance -------------------------------------------------------------------------------


def test_the_session_counts_turns_across_compaction_and_resume() -> None:
    from brain.memory.session import session_from_dict, session_to_dict

    session = Session()
    session.turns_total = 40  # the history keeps only the last turns, the counter does not
    restored = session_from_dict(session_to_dict(session))
    assert restored.turns_total == 40


def test_an_old_save_without_the_counter_falls_back_to_the_visible_turns() -> None:
    from brain.llm.types import Message
    from brain.memory.session import session_from_dict, session_to_dict

    session = Session()
    session.add(Message("user", "oi"))
    session.add(Message("assistant", "olá"))
    data = session_to_dict(session)
    del data["turns_total"]

    assert session_from_dict(data).turns_total == 1


async def test_facts_cite_the_last_new_turn_they_came_from() -> None:
    from brain.memory.mongo import MongoVectorStore
    from tests.test_extraction import ScriptedLLM, extractor

    store = MongoVectorStore(_mongo_store().connection, "fake-model")  # type: ignore[arg-type]
    llm = ScriptedLLM([{"memories": [{"text": "Petrus usa ZSH.", "type": "preference"}]}])
    turns = [
        Turn("contexto", "ok", index=6),
        Turn("uso ZSH", "anotado", index=7),
        Turn("e vim", "certo", index=8),
    ]

    result = await extractor(llm, store).extract(turns, "s1", context=1)  # type: ignore[arg-type]

    (memory,) = result.added
    assert memory.turn == 8  # the last NEW turn; the context turn (6) does not count
    saved = store.get(memory.id)
    assert saved is not None and saved.turn == 8 and saved.session_id == "s1"
