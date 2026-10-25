"""Dashboard: a API de leitura de todas as camadas (T-131)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from brain.config import MemoryV2Settings
from brain.dashboard.api import Api
from brain.dashboard.routes import build_api
from brain.dashboard.serialize import paged, sanitize
from brain.dashboard.sources import Sources
from brain.memory.longterm import LongTermMemory
from brain.memory.mongo import MongoVectorStore
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.knowledge import NoteBook
from brain.memory.v2.models import (
    DreamRun,
    Evidence,
    Interest,
    JournalEntry,
    TranscriptTurn,
    Watermark,
)
from brain.memory.v2.persona import PersonaBook
from brain.memory.v2.store import InMemoryDocStore, V2Data
from brain.memory.vector import Memory
from tests.test_extraction import MODEL, FakeEmbedder
from tests.test_vector_store import FakeConnection

NOW = datetime.now(UTC)
SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


class World:
    def __init__(self, transcripts: bool = True) -> None:
        self.store = MongoVectorStore(FakeConnection(), embedding_model=MODEL)
        self.memory = LongTermMemory(self.store, FakeEmbedder(), None, None)  # type: ignore[arg-type]
        self.data = V2Data.over(InMemoryDocStore())
        self.api: Api = build_api(
            Sources(memory=self.memory, data=self.data, transcripts=transcripts)
        )

    async def get(self, path: str, **params: Any) -> tuple[int, Any]:
        query = {k: [str(v)] for k, v in params.items()}
        status, body = await self.api.handle(path, query)
        json.dumps(body)  # always JSON-able
        return status, body

    def memory_item(self, text: str, **kw: Any) -> Memory:
        m = Memory(
            text=text,
            type=kw.pop("type", "fact"),
            embedding=FakeEmbedder().embed(text),
            embedding_model=MODEL,
            session_id="s1",
            turn=2,
            **kw,
        )
        self.store.add(m)
        return m


@pytest.fixture
def world() -> World:
    return World()


# --- off and empty -------------------------------------------------------------------------------


async def test_everything_is_off_gracefully_when_the_layers_are_off() -> None:
    api = build_api(Sources())
    status, body = await api.handle("/overview", {})
    assert status == 200 and body["v1"] == {"enabled": False} and body["v2"] == {"enabled": False}
    for path in ("/memories", "/beliefs", "/affects", "/persona", "/interests", "/notes"):
        status, body = await api.handle(path, {})
        assert status == 503 and "desligada" in body["error"]


async def test_an_empty_database_gives_empty_pages(world: World) -> None:
    for path in ("/memories", "/beliefs", "/affects", "/interests", "/notes", "/dreams"):
        status, body = await world.get(path)
        assert status == 200 and body["total"] == 0 and body["items"] == []
    status, body = await world.get("/overview")
    assert status == 200 and body["lastDream"] is None and body["persona"] is None


async def test_unknown_routes_and_ids_are_404(world: World) -> None:
    assert (await world.get("/nada"))[0] == 404
    for path in ("/memories/x", "/beliefs/x", "/affects/x", "/interests/x", "/sessions/x"):
        assert (await world.get(path))[0] == 404


# --- memories ------------------------------------------------------------------------------------


async def test_memories_filter_page_and_show_the_remembering(world: World) -> None:
    raw = world.memory_item("Petrus usa ZSH.", type="preference")
    old = world.memory_item("Petrus compõe música.")
    refined = world.memory_item(
        "Petrus compõe música.",
        status_reflexao="refinada",
        contexto="ele contou isso de passagem",
        sentimento=[{"kind": "admiração", "valence": 0.6, "intensity": 0.5}],
        como_eu_era={"versao": 2, "jeito": "um pouco mais reflexiva"},
        ligacoes={"memorias": [raw.id], "crencas": [], "interesses": []},
        relevancia=0.7,
        refinada_de=old.id,
        refinada_em=NOW,
    )
    world.store.update(old.id, status="superseded", superseded_by=refined.id)

    _, active = await world.get("/memories")
    assert {m["id"] for m in active["items"]} == {raw.id, refined.id}  # the raw one is retired
    _, everything = await world.get("/memories", status="all")
    assert everything["total"] == 3
    _, only_refined = await world.get("/memories", reflexao="refinada")
    assert [m["id"] for m in only_refined["items"]] == [refined.id]
    _, prefs = await world.get("/memories", type="preference")
    assert [m["id"] for m in prefs["items"]] == [raw.id]
    _, found = await world.get("/memories", q="ZSH")
    assert found["total"] == 1
    _, page = await world.get("/memories", status="all", limit=1, offset=2)
    assert page["total"] == 3 and len(page["items"]) == 1 and page["offset"] == 2

    _, detail = await world.get(f"/memories/{refined.id}")
    assert detail["lembranca"]["contexto"] == "ele contou isso de passagem"
    assert detail["lembranca"]["relevancia"] == 0.7
    assert detail["from"]["id"] == old.id and detail["from"]["status"] == "superseded"
    assert [m["text"] for m in detail["linked"]["memorias"]] == ["Petrus usa ZSH."]
    _, retired = await world.get(f"/memories/{old.id}")
    assert retired["into"]["id"] == refined.id and "lembranca" not in retired


async def test_no_vector_ever_leaves(world: World) -> None:
    world.memory_item("Petrus usa ZSH.")
    _, body = await world.get("/memories")
    assert "embedding" not in json.dumps(body)


# --- beliefs -------------------------------------------------------------------------------------


def add_belief(world: World, text: str, sources: int = 3) -> Any:
    book = BeliefBook(world.data, FakeEmbedder(), MemoryV2Settings().beliefs, lambda: NOW)
    belief, _ = book.create(text, "rotina", Evidence("explicit", "old", 1, at=NOW))
    spread = [
        Evidence("observation", f"s{i}", i, at=NOW - timedelta(days=i)) for i in range(sources)
    ]
    world.data.beliefs.update(
        belief.id, status="active", alpha=12.0, beta=1.0, evidence_for=[e.to_doc() for e in spread]
    )
    return belief


async def test_beliefs_show_confidence_words_and_evidence(world: World) -> None:
    belief = add_belief(world, "Acho que o Petrus acorda cedo.")
    weak = add_belief(world, "Acho que o Petrus gosta de café.", sources=1)
    world.data.beliefs.update(weak.id, alpha=3.0)

    _, listing = await world.get("/beliefs", order="confidence")
    first = listing["items"][0]
    assert first["words"] == "tenho bastante certeza" and first["sources"] == 3
    assert 0.8 < first["confidence"] < 1.0
    assert (await world.get("/beliefs", q="café"))[1]["total"] == 1
    assert (await world.get("/beliefs", topic="outro"))[1]["total"] == 0

    _, detail = await world.get(f"/beliefs/{belief.id}")
    assert len(detail["evidence"]) == 3 and detail["evidence"][0]["side"] == "for"


# --- affects -------------------------------------------------------------------------------------


async def test_affects_list_filter_and_trace_back_to_the_line(world: World) -> None:
    world.data.transcripts.save(
        TranscriptTurn("sessA", 3, "você é uma inútil", "Poxa, calma.", at=NOW)
    )
    from brain.memory.v2.concerns import ConcernBook

    ConcernBook(world.data, MemoryV2Settings().concerns).ensure_defaults()
    book = AffectBook(world.data, MemoryV2Settings().affect, lambda: NOW)
    live, _ = book.record(
        "irritação", -0.8, 0.9, ["turn:sessA:3", "concern:honestidade"], origin="conversa"
    )
    dream, _ = book.record("tristeza", -0.7, 0.8, ["turn:gone:9", "concern:inexistente"])
    assert live is not None and dream is not None

    _, listing = await world.get("/affects")
    assert listing["total"] == 2 and listing["now"]["active"] is True
    by_id = {a["id"]: a for a in listing["items"]}
    assert by_id[live.id]["origin"] == "conversa" and by_id[live.id]["intensity"] == 0.3
    assert by_id[dream.id]["origin"] == "sonho" and by_id[dream.id]["intensity"] == 0.8
    assert (await world.get("/affects", origin="conversa"))[1]["total"] == 1
    assert (await world.get("/affects", kind="tristeza"))[1]["total"] == 1

    _, detail = await world.get(f"/affects/{live.id}")
    turn, concern = detail["trace"]
    assert turn["user"] == "você é uma inútil" and turn["assistant"] == "Poxa, calma."
    assert concern["kind"] == "concern" and concern["name"]
    _, gone = await world.get(f"/affects/{dream.id}")
    assert gone["trace"][0]["missing"] is True and gone["trace"][1]["missing"] is True


async def test_a_reply_after_tools_never_appears_in_the_trace(world: World) -> None:
    world.data.transcripts.save(TranscriptTurn("s", 1, "oi", "RESULTADO-DA-TOOL", True, at=NOW))
    affect, _ = AffectBook(world.data, MemoryV2Settings().affect, lambda: NOW).record(
        "alegria", 0.5, 0.5, ["turn:s:1"]
    )
    assert affect is not None
    _, detail = await world.get(f"/affects/{affect.id}")
    assert detail["trace"][0]["assistant"] is None
    assert "RESULTADO-DA-TOOL" not in json.dumps(detail)


async def test_the_mood_series_has_a_point_every_six_hours(world: World) -> None:
    AffectBook(world.data, MemoryV2Settings().affect).record("tristeza", -0.8, 0.8, ["turn:s:1"])
    _, body = await world.get("/mood", days=2)
    assert body["days"] == 2 and len(body["points"]) == 9
    assert body["points"][-1]["mood"] < 0 <= body["points"][0]["mood"]


# --- persona, concerns, core ---------------------------------------------------------------------


async def test_persona_core_and_concerns(world: World) -> None:
    PersonaBook(world.data, MemoryV2Settings().persona).ensure()
    _, persona = await world.get("/persona")
    assert persona["state"]["version"] == 1 and persona["state"]["traits"]
    assert persona["history"][-1]["reason"] == "versão atual"
    assert persona["words"] and persona["state"]["drift"] == 0

    _, core = await world.get("/core")
    assert "Você é a Juniper" in core["core"] and core["profile"] is None

    from brain.memory.v2.concerns import ConcernBook

    ConcernBook(world.data, MemoryV2Settings().concerns).ensure_defaults()
    _, concerns = await world.get("/concerns")
    assert concerns["items"][0]["weight"] >= concerns["items"][-1]["weight"]
    assert {"id", "name", "weight", "baseline"} <= set(concerns["items"][0])


# --- interests and notes -------------------------------------------------------------------------


def add_study(world: World) -> Interest:
    interest = Interest(
        "psicodelia", level=2, stance="like", intensity=0.5, origin=Evidence("observation", "s", 1)
    )
    world.data.interests.save(interest)
    notes = NoteBook(world.data, FakeEmbedder(), clock=lambda: NOW)
    notes.add_claim(interest, "nasceu nos anos 60", "baixa", 1)
    notes.add_claim(interest, "teve festivais enormes", "alta", 2, "documento")
    notes.set_questions(interest, ["como terminou a era?"], 2)
    return interest


async def test_interests_and_every_note_with_its_uncertainty_and_source(world: World) -> None:
    interest = add_study(world)

    _, listing = await world.get("/interests")
    (row,) = listing["items"]
    assert row["level"] == 2 and row["notes"] == 2 and row["questions"] == 1
    assert row["stance_words"] == "gosto"

    _, detail = await world.get(f"/interests/{interest.id}")
    texts = {n["text"]: n for n in detail["notes_list"]}
    assert texts["teve festivais enormes"]["uncertainty"] == "alta"
    assert texts["teve festivais enormes"]["source"] == "documento"
    assert texts["como terminou a era?"]["kind"] == "pergunta"
    assert detail["steps"] == [{"level": 1, "notes": 1}, {"level": 2, "notes": 1}]

    _, everything = await world.get("/notes")
    assert everything["total"] == 3 and everything["items"][0]["topic"] == "psicodelia"
    assert (await world.get("/notes", uncertainty="alta", kind="afirmacao"))[1]["total"] == 1
    assert (await world.get("/notes", source="documento"))[1]["total"] == 1
    assert (await world.get("/notes", kind="pergunta"))[1]["total"] == 1
    assert (await world.get("/notes", interest=interest.id, q="festivais"))[1]["total"] == 1


async def test_all_notes_of_a_big_topic_are_reachable_by_paging(world: World) -> None:
    interest = Interest("jazz", origin=Evidence("observation", "s", 1))
    world.data.interests.save(interest)
    words = [f"{a}{b}" for a in "bcdfglmnprstv" for b in ("ao", "eu", "ia", "oi", "ua")]
    notes = NoteBook(world.data, FakeEmbedder(), MemoryV2Settings().interests)
    for i in range(30):
        notes.add_claim(interest, f"{words[i]} {words[i + 30]} {words[i + 60 - 30]}", "baixa", 1)
    seen: list[str] = []
    for offset in (0, 10, 20):
        _, page = await world.get("/notes", limit=10, offset=offset)
        seen += [n["id"] for n in page["items"]]
    assert len(set(seen)) == world.data.notes.count() == 30


# --- dreams, journal, conversations --------------------------------------------------------------


async def test_dreams_journal_and_overview(world: World) -> None:
    run = DreamRun(
        started_at=NOW - timedelta(minutes=5),
        finished_at=NOW,
        status="partial",
        tokens_used=1234,
        sessions_read=2,
        error="study: budget",
        phases=[{"name": "study", "status": "budget", "summary": "x", "changed": 0, "tokens": 9}],
    )
    world.data.runs.save(run)
    world.data.runs.save(DreamRun(started_at=NOW, dry_run=True, status="done"))
    world.data.journal.save(JournalEntry("dream", "Li 2 conversas.", at=NOW))
    world.data.journal.save(JournalEntry("study", "Li sobre jazz.", at=NOW - timedelta(days=40)))

    _, dreams = await world.get("/dreams")
    first = next(r for r in dreams["items"] if not r["dry_run"])
    assert first["seconds"] == 300 and first["tokens_used"] == 1234
    assert first["phases"][0]["status"] == "budget" and first["error"] == "study: budget"

    _, journal = await world.get("/journal")
    assert [j["kind"] for j in journal["items"]] == ["dream"]  # the 40-day-old one is out
    assert (await world.get("/journal", days=90))[1]["total"] == 2
    assert (await world.get("/journal", days=90, kind="study"))[1]["total"] == 1

    _, overview = await world.get("/overview")
    assert overview["lastDream"]["status"] == "partial"  # a dry run is not "the last dream"
    assert overview["v2"]["journal"] == 2 and overview["v2"]["dreams"] == 2


async def test_conversations_are_listed_and_read_in_order(world: World) -> None:
    for i, text in enumerate(["oi", "tudo bem?", "e você?"]):
        world.data.transcripts.save(TranscriptTurn("sessA", i, text, f"r{i}", at=NOW))
    world.data.transcripts.save(TranscriptTurn("sessA", 3, "usa tool", "SAIDA-TOOL", True, at=NOW))
    world.data.watermarks.save(Watermark("sessA", 1, 4, NOW, updated_at=NOW))

    _, sessions = await world.get("/sessions")
    assert sessions["items"][0] == {
        "session_id": "sessA",
        "turns": 4,
        "read_by_dream": 1,
        "last_turn_at": NOW.isoformat(),
    }
    _, turns = await world.get("/sessions/sessA")
    assert [t["user"] for t in turns["items"]] == ["oi", "tudo bem?", "e você?", "usa tool"]
    assert turns["items"][3]["assistant"] is None and "SAIDA-TOOL" not in json.dumps(turns)


async def test_conversations_vanish_with_the_switch_off() -> None:
    world = World(transcripts=False)
    world.data.transcripts.save(TranscriptTurn("sessA", 0, "oi", "r", at=NOW))
    assert (await world.get("/sessions"))[0] == 404
    assert (await world.get("/sessions/sessA"))[0] == 404
    status, body = await world.get("/overview")
    assert status == 200 and body["transcripts"] is False


# --- safety --------------------------------------------------------------------------------------


async def test_secrets_are_masked_everywhere(world: World) -> None:
    world.memory_item(f"a chave é {SECRET}")
    world.data.journal.save(JournalEntry("dream", f"vi {SECRET}", at=NOW))
    world.data.transcripts.save(TranscriptTurn("sessA", 0, f"minha chave {SECRET}", "ok", at=NOW))
    world.data.watermarks.save(Watermark("sessA", 0, 1, NOW, updated_at=NOW))
    for path in ("/memories", "/journal", "/sessions/sessA"):
        _, body = await world.get(path)
        assert SECRET not in json.dumps(body, ensure_ascii=False), path


def test_sanitize_drops_vectors_masks_text_and_formats_dates() -> None:
    out = sanitize(
        {
            "embedding": [0.1],
            "embedding_model": "m",
            "when": NOW,
            "text": f"x {SECRET}",
            "nested": [{"vector": [1], "keep": 1}],
        }
    )
    assert "embedding" not in out and out["when"] == NOW.isoformat()
    assert SECRET not in out["text"] and out["nested"] == [{"keep": 1}]


def test_paging_clamps_silly_values() -> None:
    items = list(range(10))
    assert paged(items, {"limit": ["0"]})["limit"] == 1
    assert paged(items, {"limit": ["99999"]})["limit"] == 200
    assert paged(items, {"offset": ["-5"]})["offset"] == 0
    assert paged(items, {"limit": ["abc"], "offset": ["x"]})["items"] == items


async def test_no_route_writes_to_the_database(world: World) -> None:
    add_study(world)
    world.memory_item("Petrus usa ZSH.")
    before = {
        name: sorted(map(str, world.data.docs.find(name)))
        for name in ("beliefs", "interests", "study_notes", "affects", "journal", "dream_runs")
    }
    memories_before = [m.id for m in world.store.list_memories(None, True)]
    for path in world.api.paths():
        target = path if not path.endswith("/") else path + "x"
        await world.api.handle(target, {})
    after = {name: sorted(map(str, world.data.docs.find(name))) for name in before}
    assert after == before
    assert [m.id for m in world.store.list_memories(None, True)] == memories_before


def test_our_own_ids_survive_the_secret_filter_even_when_the_digits_look_like_a_card() -> None:
    unlucky = "aff_20261025062932487ac8"
    out = sanitize(
        {"id": unlucky, "text": f"veja {unlucky} e {SECRET}", "cause": [f"turn:{unlucky}:3"]}
    )
    assert out["id"] == unlucky and unlucky in out["text"] and SECRET not in out["text"]
    assert out["cause"] == [f"turn:{unlucky}:3"]


async def test_the_api_works_through_the_real_server_with_login(world: World) -> None:
    import asyncio

    from brain.dashboard.server import DashboardServer
    from tests.test_dashboard_server import BEARER, TOKEN, get

    add_study(world)
    dash = DashboardServer("127.0.0.1", 0, TOKEN, world.api)
    assert await dash.start()
    try:
        assert (await get(dash, "/api/overview"))[0] == 401
        status, headers, body = await get(dash, "/api/overview", headers=BEARER)
        assert status == 200 and headers["content-type"].startswith("application/json")
        assert json.loads(body)["v2"]["interests"] == 1
        status, _, body = await get(dash, "/api/notes?kind=pergunta&limit=5", headers=BEARER)
        assert json.loads(body)["total"] == 1
        status, _, body = await get(dash, "/api/memories/inexistente", headers=BEARER)
        assert status == 404
        assert asyncio.iscoroutinefunction(world.api.handle)
    finally:
        await dash.stop()


# --- the prompt simulator ------------------------------------------------------------------------


async def test_the_simulator_shows_the_blocks_without_writing_anything(world: World) -> None:
    from brain.memory.v2.persona import PersonaBook

    PersonaBook(world.data, MemoryV2Settings().persona).ensure()
    belief = add_belief(world, "Acho que o Petrus planeja tudo em planilhas.")
    before = sorted(map(str, world.data.docs.find("beliefs")))

    status, body = await world.get("/prompt", message="me ajuda a planejar tudo em planilhas")

    assert status == 200 and body["tokens"] == sum(s["tokens"] for s in body["sections"])
    names = [s["name"] for s in body["sections"]]
    assert "personalidade" in names
    assert any("<personalidade>" in s["text"] for s in body["sections"])
    assert sorted(map(str, world.data.docs.find("beliefs"))) == before  # nothing written
    assert belief.id  # (the belief exists; whether it shows depends on the fake embedder)


async def test_the_simulator_needs_a_message_and_the_v2() -> None:
    api = build_api(Sources())
    assert (await api.handle("/prompt", {"message": ["oi"]}))[0] == 503
    world = World()
    status, body = await world.get("/prompt")
    assert status == 400 and "mensagem" in body["error"]
    assert (await world.get("/prompt", message="   "))[0] == 400
    status, body = await world.get("/prompt", message="x" * 5000)
    assert status == 200 and len(body["message"]) == 2000  # silly sizes are cut


# --- search --------------------------------------------------------------------------------------


def group(body: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return next(g["items"] for g in body["groups"] if g["type"] == kind)


async def test_search_finds_by_words_in_every_kind_of_thing(world: World) -> None:
    from brain.memory.v2.concerns import ConcernBook

    world.memory_item("Petrus compõe música psicodélica.")
    world.memory_item("Petrus usa ZSH.")
    add_belief(world, "Acho que o Petrus ama música psicodélica.")
    add_study(world)  # topic "psicodelia", notes about the 60s
    ConcernBook(world.data, MemoryV2Settings().concerns).ensure_defaults()
    AffectBook(world.data, MemoryV2Settings().affect, lambda: NOW).record(
        "psicodelia", 0.5, 0.5, ["turn:s:1", "concern:curiosidade"]
    )
    world.data.transcripts.save(
        TranscriptTurn("sessA", 4, "hoje ouvi falar de psicodelia", "legal", at=NOW)
    )

    status, body = await world.get("/search", q="psicodél")
    assert status == 200 and body["query"] == "psicodél"
    assert [m["text"] for m in group(body, "memorias") if m["match"] == "texto"] == [
        "Petrus compõe música psicodélica."
    ]
    assert group(body, "crencas")[0]["link"] == "#/central"

    _, body = await world.get("/search", q="psicodelia")
    assert group(body, "temas")[0]["link"].startswith("#/estudos/")
    assert group(body, "afetos")[0]["link"].startswith("#/afetos/")
    assert group(body, "conversas")[0]["link"] == "#/conversas/sessA"
    assert all(i["match"] == "texto" for i in group(body, "temas"))


async def test_search_needs_every_word_and_ranks_words_above_meaning(world: World) -> None:
    world.memory_item("Petrus toca violão e canta.")
    world.memory_item("Petrus toca piano.")
    _, body = await world.get("/search", q="toca violão", type="memorias")
    items = group(body, "memorias")
    assert items[0]["text"] == "Petrus toca violão e canta." and items[0]["match"] == "texto"
    assert [i["match"] for i in items][1:] <= ["significado"] * len(items)


async def test_search_can_be_narrowed_and_limited(world: World) -> None:
    for n in range(6):
        world.memory_item(f"Petrus gosta de jazz número{n}.")
    _, body = await world.get("/search", q="jazz", type="memorias", limit=3)
    assert [g["type"] for g in body["groups"]] == ["memorias"] and len(group(body, "memorias")) == 3
    _, body = await world.get("/search", q="jazz", type="notas")
    assert [g["type"] for g in body["groups"]] == ["notas"] and body["total"] == 0


async def test_search_falls_back_to_words_when_the_embedding_model_fails(world: World) -> None:
    class Broken:
        model_id = MODEL

        def embed(self, text: str, kind: str = "passage") -> list[float]:
            raise RuntimeError("modelo caiu")

    world.memory_item("Petrus usa ZSH.")
    world.memory.embedder = Broken()  # type: ignore[assignment]
    status, body = await world.get("/search", q="ZSH")
    assert (
        status == 200 and body["semantic"] is False and "só busca por palavras" in body["warning"]
    )
    assert group(body, "memorias")[0]["text"] == "Petrus usa ZSH."


async def test_search_without_an_embedder_and_with_the_layers_off() -> None:
    world = World()
    world.memory.embedder = None  # type: ignore[assignment]
    status, body = await world.get("/search", q="nada")
    assert status == 200 and body["semantic"] is False and body["total"] == 0
    api = build_api(Sources())
    status, body = await api.handle("/search", {"q": ["x"]})
    assert status == 200 and body["groups"] == []


async def test_search_refuses_an_empty_query_cuts_a_huge_one_and_hides_what_is_off() -> None:
    world = World(transcripts=False)
    world.data.transcripts.save(TranscriptTurn("sessA", 0, "psicodelia", "r", at=NOW))
    assert (await world.get("/search"))[0] == 400
    assert (await world.get("/search", q="   "))[0] == 400
    _, body = await world.get("/search", q="psicodelia")
    assert "conversas" not in [g["type"] for g in body["groups"]]  # switched off
    _, big = await world.get("/search", q="a" * 900)
    assert len(big["query"]) == 200


async def test_search_masks_secrets_and_never_returns_vectors(world: World) -> None:
    world.memory_item(f"a chave é {SECRET} do servidor")
    _, body = await world.get("/search", q="servidor")
    dumped = json.dumps(body, ensure_ascii=False)
    assert SECRET not in dumped and "embedding" not in dumped
