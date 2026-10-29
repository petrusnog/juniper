"""The read-only routes of the dashboard (ADR-020): one function per page's data.

Every route only reads. The synchronous reads (MongoDB) run in a thread so the chat server's event
loop never waits on them. What leaves goes through ``sanitize`` (secrets masked, no vectors).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any, TypeVar

from brain.config import settings
from brain.core.persona import load_core
from brain.dashboard import occurrences
from brain.dashboard.api import Api, ApiError, Query, first
from brain.dashboard.search import SEARCH_TYPES, run_search
from brain.dashboard.serialize import contains, number, paged, sanitize
from brain.dashboard.sources import Sources
from brain.dashboard.xlsx import MIME as XLSX_MIME
from brain.dashboard.xlsx import build_xlsx
from brain.memory.retrieval import estimate_tokens
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.confidence import distinct_sources
from brain.memory.v2.context import V2Context, state_lines
from brain.memory.v2.interests import STANCE_WORDS
from brain.memory.v2.knowledge import NoteBook
from brain.memory.v2.models import utcnow
from brain.memory.v2.persona import PersonaBook, drift, persona_lines
from brain.memory.v2.trace import trace_affect
from brain.memory.vector import Memory

T = TypeVar("T")

MAX_PROMPT_CHARS = 2000
MAX_QUERY_CHARS = 200
OFF_V1 = {"enabled": False, "reason": "a memória de longo prazo está desligada"}
OFF_V2 = {"enabled": False, "reason": "a Memória V2 está desligada"}
OFF_LEAKS = "as ocorrências precisam do MongoDB (memory.session.store: mongo)"
MAX_OCCURRENCES = 500  # per source, in the list and in the spreadsheet


async def _blocking(fn: Callable[[], T]) -> T:
    return await asyncio.to_thread(fn)


def build_api(sources: Sources) -> Api:
    """The API over what the running server has open."""
    api = Api()
    data = sources.data
    memory = sources.memory

    def need_v2() -> Any:
        if data is None:
            raise ApiError(503, OFF_V2["reason"])
        return data

    def need_v1() -> Any:
        if memory is None or getattr(memory, "store", None) is None:
            raise ApiError(503, OFF_V1["reason"])
        return memory

    # --- session and overview -----------------------------------------------------------------

    async def session(query: Query) -> Any:
        return {"ok": True, "v1": memory is not None, "v2": data is not None}

    async def overview(query: Query) -> Any:
        return await _blocking(_overview)

    def _overview() -> dict[str, Any]:
        out: dict[str, Any] = {
            "v1": {"enabled": memory is not None},
            "v2": {"enabled": data is not None},
            "transcripts": sources.transcripts,
        }
        if memory is not None and getattr(memory, "store", None) is not None:
            rows = memory.store.list_memories(None, True)
            out["v1"].update(
                active=sum(1 for m in rows if m.status == "active"),
                superseded=sum(1 for m in rows if m.status == "superseded"),
                bruta=sum(1 for m in rows if m.status == "active" and m.status_reflexao == "bruta"),
                refinada=sum(
                    1 for m in rows if m.status == "active" and m.status_reflexao == "refinada"
                ),
            )
        if data is not None:
            out["v2"].update(
                beliefs=data.beliefs.count(),
                interests=data.interests.count(),
                notes=data.notes.count({"kind": "afirmacao"}),
                questions=data.notes.count({"kind": "pergunta"}),
                affects=data.affects.count(),
                journal=data.journal.count(),
                dreams=data.runs.count(),
            )
            out["affect"] = _affect_now()
            out["persona"] = _persona_summary()
            runs = data.runs.list({"dry_run": False}, [("started_at", -1)], 1)
            out["lastDream"] = _run(runs[0]) if runs else None
        return out

    def _affect_now() -> dict[str, Any]:
        book = AffectBook(data)
        state = book.state()

        def felt(f: Any) -> dict[str, Any]:
            return {
                "id": f.affect.id,
                "kind": f.affect.kind,
                "valence": f.affect.valence,
                "strength": round(f.strength, 3),
                "origin": f.affect.origin,
                "grievance": f.affect.persistence == "grievance",
            }

        return {
            "active": book.active,
            "mood": round(state.mood, 3),
            "lines": state_lines(state),
            "feelings": [felt(f) for f in state.feelings],
            "grievances": [felt(f) for f in state.grievances],
            "echoes": [felt(f) for f in state.echoes],
        }

    def _persona_summary() -> dict[str, Any] | None:
        state = PersonaBook(data).current()
        if state is None:
            return None
        return {
            "version": state.version,
            "drift": round(drift(state), 4),
            "traits": [
                {"name": n, "value": t.value, "baseline": t.baseline, "min": t.min, "max": t.max}
                for n, t in sorted(state.traits.items())
            ],
            "recent": state.recent,
        }

    # --- memories (v1, with the refinement) ---------------------------------------------------

    def _memory_row(m: Memory) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": m.id,
            "text": m.text,
            "type": m.type,
            "status": m.status,
            "status_reflexao": m.status_reflexao,
            "superseded_by": m.superseded_by,
            "source_kind": m.source_kind,
            "session_id": m.session_id,
            "turn": m.turn,
            "created_at": m.created_at,
            "updated_at": m.updated_at,
        }
        if m.status_reflexao == "refinada":
            row["lembranca"] = {
                "contexto": m.contexto,
                "sentimento": m.sentimento,
                "como_eu_era": m.como_eu_era,
                "ligacoes": m.ligacoes,
                "relevancia": m.relevancia,
                "refinada_em": m.refinada_em,
                "refinada_de": m.refinada_de,
            }
        return row

    async def memories(query: Query) -> Any:
        mem = need_v1()
        kind, reflexao = first(query, "type"), first(query, "reflexao")
        status, text = first(query, "status", "active"), first(query, "q")

        def read() -> dict[str, Any]:
            rows = mem.store.list_memories(None, True)
            keep = [
                m
                for m in rows
                if (status == "all" or m.status == status)
                and (not kind or m.type == kind)
                and (not reflexao or m.status_reflexao == reflexao)
                and contains(m.text, text)
            ]
            return paged([_memory_row(m) for m in keep], query)

        return await _blocking(read)

    async def memory_detail(query: Query) -> Any:
        mem = need_v1()
        memory_id = query["_id"][0]

        def read() -> dict[str, Any]:
            found = mem.store.get(memory_id)
            if found is None:
                raise ApiError(404, "memória não encontrada")
            row = _memory_row(found)
            row["from"] = _short(mem.store.get(found.refinada_de)) if found.refinada_de else None
            row["into"] = (
                _short(mem.store.get(found.superseded_by)) if found.superseded_by else None
            )
            links = found.ligacoes or {}
            row["linked"] = {
                "memorias": [_short(mem.store.get(i)) for i in links.get("memorias", [])],
                "crencas": [
                    {"id": b.id, "statement": b.statement}
                    for b in (data.beliefs.get(i) for i in links.get("crencas", []))
                    if b is not None
                ]
                if data is not None
                else [],
            }
            return row

        return await _blocking(read)

    def _short(m: Memory | None) -> dict[str, Any] | None:
        if m is None:
            return None
        return {
            "id": m.id,
            "text": m.text,
            "status": m.status,
            "status_reflexao": m.status_reflexao,
            "created_at": m.created_at,
        }

    # --- search -------------------------------------------------------------------------------

    async def search(query: Query) -> Any:
        text = (first(query, "q") or "").strip()[:MAX_QUERY_CHARS]
        if not text:
            raise ApiError(400, "escreva o que procurar")
        limit = number(query, "limit", 8, 1, 20)
        wanted = set(query.get("type", [])) or set(SEARCH_TYPES)
        return await _blocking(lambda: run_search(sources, text, limit, wanted))

    # --- the prompt simulator -----------------------------------------------------------------

    async def prompt(query: Query) -> Any:
        store = need_v2()
        message = (first(query, "message") or "").strip()[:MAX_PROMPT_CHARS]
        if not message:
            raise ApiError(400, "escreva a mensagem")
        previous = (first(query, "previous") or "").strip()[:MAX_PROMPT_CHARS] or None
        # A context of its own: the simulation must not touch the live chat's in-memory state
        # (the interest gap, the verification question), nor the database.
        found = await V2Context(store, sources.embedder).preview(message, previous)
        blocks = [
            {"name": name, "text": text, "tokens": estimate_tokens(text)} for name, text in found
        ]
        return {"message": message, "sections": blocks, "tokens": sum(b["tokens"] for b in blocks)}

    # --- beliefs ------------------------------------------------------------------------------

    def _belief_row(book: BeliefBook, b: Any, detail: bool = False) -> dict[str, Any]:
        evidence = [*b.evidence_for, *b.evidence_against]
        row = {
            "id": b.id,
            "statement": b.statement,
            "topic": b.topic,
            "about": b.about,
            "status": b.status,
            "confidence": round(book.confidence_of(b), 3),
            "words": book.words(b),
            "n": round(b.n, 2),
            "sources": distinct_sources(b.evidence_for),
            "evidence_for": len(b.evidence_for),
            "evidence_against": len(b.evidence_against),
            "user_confirmed": b.user_confirmed,
            "created_at": b.created_at,
            "updated_at": b.updated_at,
        }
        if detail:
            row["evidence"] = [
                {**e.to_doc(), "side": "for" if e in b.evidence_for else "against"}
                for e in sorted(evidence, key=lambda e: e.at, reverse=True)
            ]
        return row

    async def beliefs(query: Query) -> Any:
        store = need_v2()
        status, topic, text = first(query, "status"), first(query, "topic"), first(query, "q")

        def read() -> dict[str, Any]:
            book = BeliefBook(store, sources.embedder, settings.memory.v2.beliefs)
            items = [
                b
                for b in store.beliefs.list(sort=[("updated_at", -1)])
                if (not status or b.status == status)
                and (not topic or b.topic == topic)
                and contains(b.statement, text)
            ]
            rows = [_belief_row(book, b) for b in items]
            if first(query, "order") == "confidence":
                rows.sort(key=lambda r: -r["confidence"])
            return paged(rows, query)

        return await _blocking(read)

    async def belief_detail(query: Query) -> Any:
        store = need_v2()

        def read() -> dict[str, Any]:
            found = store.beliefs.get(query["_id"][0])
            if found is None:
                raise ApiError(404, "crença não encontrada")
            book = BeliefBook(store, sources.embedder, settings.memory.v2.beliefs)
            return _belief_row(book, found, detail=True)

        return await _blocking(read)

    # --- affects ------------------------------------------------------------------------------

    def _affect_row(book: AffectBook, a: Any) -> dict[str, Any]:
        return {
            "id": a.id,
            "kind": a.kind,
            "valence": a.valence,
            "intensity": a.intensity,
            "strength": round(book.strength(a), 3),
            "origin": a.origin,
            "about": a.about,
            "grievance": a.persistence == "grievance",
            "resolved": a.resolved_at is not None,
            "cause": a.cause,
            "occurred_at": a.occurred_at,
        }

    async def affects(query: Query) -> Any:
        store = need_v2()
        origin, kind = first(query, "origin"), first(query, "kind")

        def read() -> dict[str, Any]:
            book = AffectBook(store)
            items = sorted(store.affects.list(), key=lambda a: a.occurred_at, reverse=True)
            rows = [
                _affect_row(book, a)
                for a in items
                if (not origin or a.origin == origin) and (not kind or a.kind == kind)
            ]
            return {**paged(rows, query), "active": book.active, "now": _affect_now()}

        return await _blocking(read)

    async def affect_detail(query: Query) -> Any:
        store = need_v2()

        def read() -> dict[str, Any]:
            found = store.affects.get(query["_id"][0])
            if found is None:
                raise ApiError(404, "sentimento não encontrado")
            return {
                **_affect_row(AffectBook(store), found),
                "trace": trace_affect(store, found, transcripts=sources.transcripts),
            }

        return await _blocking(read)

    async def mood(query: Query) -> Any:
        store = need_v2()
        days = number(query, "days", 14, 1, 90)

        def read() -> dict[str, Any]:
            book = AffectBook(store)
            now = utcnow()
            points = []
            for step in range(days * 4, -1, -1):
                moment = now - timedelta(hours=6 * step)
                points.append({"at": moment, "mood": round(book.mood(moment), 3)})
            return {"days": days, "points": points}

        return await _blocking(read)

    # --- persona, concerns, core --------------------------------------------------------------

    async def persona(query: Query) -> Any:
        store = need_v2()

        def read() -> dict[str, Any]:
            book = PersonaBook(store)
            state = book.current()
            if state is None:
                return {"enabled": True, "state": None, "history": []}
            history = [
                {
                    "version": v.state.version,
                    "at": v.at,
                    "reason": v.reason,
                    "traits": {n: t.value for n, t in v.state.traits.items()},
                }
                for v in book.history()
            ]
            history.append(
                {
                    "version": state.version,
                    "at": state.updated_at,
                    "reason": "versão atual",
                    "traits": {n: t.value for n, t in state.traits.items()},
                }
            )
            return {
                "enabled": True,
                "state": _persona_summary(),
                "words": persona_lines(state, settings.memory.v2.persona.always_describe),
                "drift_warning": book.drift_warning(state),
                "changed_by": state.changed_by,
                "history": history,
            }

        return await _blocking(read)

    async def concerns(query: Query) -> Any:
        store = need_v2()

        def read() -> dict[str, Any]:
            items = sorted(store.concerns.list(), key=lambda c: -c.weight)
            return {
                "items": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "weight": c.weight,
                        "baseline": c.baseline_weight,
                        "description": c.description,
                        "updated_at": c.updated_at,
                    }
                    for c in items
                ]
            }

        return await _blocking(read)

    async def core(query: Query) -> Any:
        profile = None
        if memory is not None:
            found = await memory.get_profile()
            if found is not None:
                profile = {
                    "text": found.text,
                    "fields": found.fields,
                    "updated_at": found.updated_at,
                }
        return {"core": await _blocking(load_core), "profile": profile}

    # --- interests and notes ------------------------------------------------------------------

    def _interest_row(book: NoteBook, i: Any) -> dict[str, Any]:
        return {
            "id": i.id,
            "topic": i.topic,
            "level": i.level,
            "stance": i.stance,
            "stance_words": STANCE_WORDS.get(i.stance, i.stance),
            "intensity": i.intensity,
            "reasons": i.reasons,
            "exposures": i.exposures,
            "last_studied_at": i.last_studied_at,
            "created_at": i.created_at,
            "notes": len(book.claims(i.id)),
            "questions": len(book.questions(i.id)),
        }

    def _note_row(n: Any, topic: str | None = None) -> dict[str, Any]:
        return {
            "id": n.id,
            "interest_id": n.interest_id,
            "topic": topic,
            "text": n.text,
            "kind": n.kind,
            "uncertainty": n.uncertainty,
            "source": n.source,
            "level": n.level,
            "created_at": n.created_at,
        }

    async def interests(query: Query) -> Any:
        store = need_v2()

        def read() -> dict[str, Any]:
            book = NoteBook(store, sources.embedder)
            items = sorted(store.interests.list(), key=lambda i: (-i.level, -i.intensity, i.topic))
            return paged([_interest_row(book, i) for i in items], query)

        return await _blocking(read)

    async def interest_detail(query: Query) -> Any:
        store = need_v2()

        def read() -> dict[str, Any]:
            found = store.interests.get(query["_id"][0])
            if found is None:
                raise ApiError(404, "tema não encontrado")
            book = NoteBook(store, sources.embedder)
            notes = book.of(found.id)
            steps: dict[int, int] = {}
            for n in notes:
                if n.kind == "afirmacao":
                    steps[n.level] = steps.get(n.level, 0) + 1
            return {
                **_interest_row(book, found),
                "notes_list": [_note_row(n, found.topic) for n in notes],
                "steps": [{"level": lv, "notes": c} for lv, c in sorted(steps.items())],
            }

        return await _blocking(read)

    async def notes(query: Query) -> Any:
        store = need_v2()
        interest, uncertainty = first(query, "interest"), first(query, "uncertainty")
        source, kind, text = first(query, "source"), first(query, "kind"), first(query, "q")

        def read() -> dict[str, Any]:
            topics = {i.id: i.topic for i in store.interests.list()}
            items = sorted(store.notes.list(), key=lambda n: n.created_at, reverse=True)
            rows = [
                _note_row(n, topics.get(n.interest_id))
                for n in items
                if (not interest or n.interest_id == interest)
                and (not uncertainty or n.uncertainty == uncertainty)
                and (not source or n.source == source)
                and (not kind or n.kind == kind)
                and contains(n.text, text)
            ]
            return paged(rows, query)

        return await _blocking(read)

    # --- dreams and journal -------------------------------------------------------------------

    def _run(r: Any) -> dict[str, Any]:
        seconds = (r.finished_at - r.started_at).total_seconds() if r.finished_at else None
        return {
            "id": r.id,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "seconds": seconds,
            "dry_run": r.dry_run,
            "status": r.status,
            "tokens_used": r.tokens_used,
            "sessions_read": r.sessions_read,
            "error": r.error,
            "phases": r.phases,
        }

    async def dreams(query: Query) -> Any:
        store = need_v2()

        def read() -> dict[str, Any]:
            runs = store.runs.list(sort=[("started_at", -1)])
            return paged([_run(r) for r in runs], query)

        return await _blocking(read)

    async def journal(query: Query) -> Any:
        store = need_v2()
        days = number(query, "days", 30, 1, 3650)
        kind = first(query, "kind")

        def read() -> dict[str, Any]:
            since = (utcnow() - timedelta(days=days)).isoformat()
            items = store.journal.list(
                {"at": {"$gte": utcnow() - timedelta(days=days)}}, [("at", -1)]
            )
            rows = [
                {"id": j.id, "kind": j.kind, "day": j.day, "text": j.text, "at": j.at}
                for j in items
                if not kind or j.kind == kind
            ]
            return {**paged(rows, query), "since": since}

        return await _blocking(read)

    # --- conversations ------------------------------------------------------------------------

    def need_transcripts() -> Any:
        if not sources.transcripts:
            raise ApiError(404, "as conversas estão desligadas (dashboard.transcripts)")
        return need_v2()

    async def sessions(query: Query) -> Any:
        store = need_transcripts()

        def read() -> dict[str, Any]:
            marks = sorted(
                store.watermarks.list(),
                key=lambda m: m.last_turn_at or m.updated_at,
                reverse=True,
            )
            return paged(
                [
                    {
                        "session_id": m.session_id,
                        "turns": m.turns_total,
                        "read_by_dream": m.turns_read,
                        "last_turn_at": m.last_turn_at,
                    }
                    for m in marks
                ],
                query,
            )

        return await _blocking(read)

    async def session_turns(query: Query) -> Any:
        store = need_transcripts()
        session_id = query["_id"][0]

        def read() -> dict[str, Any]:
            turns = store.transcripts.list({"session_id": session_id}, [("index", 1)])
            if not turns:
                raise ApiError(404, "conversa não encontrada")
            rows = [
                {
                    "index": t.index,
                    "user": t.user,
                    # her reply may carry tool output: it stays out, as in the dream
                    "assistant": None if t.used_tools else t.assistant,
                    "used_tools": t.used_tools,
                    "leaked": t.leaked,
                    "at": t.at,
                }
                for t in turns
            ]
            return {"session_id": session_id, **paged(rows, query)}

        return await _blocking(read)

    # --- occurrences: the leaks the user reported and the audit found (T-168) ---------------------

    def pick_kind(query: Query) -> str:
        kind = first(query, "kind", "todos") or "todos"
        if kind not in (*occurrences.KINDS, "todos"):
            raise ApiError(400, "kind deve ser relato, automatico ou todos")
        return kind

    def collect(query: Query) -> list[tuple[str, dict[str, Any]]]:
        """The occurrences of the filter, newest first: reports and audit records together."""
        if sources.reports is None and sources.audit is None:
            raise ApiError(503, OFF_LEAKS)
        kind = pick_kind(query)
        days = number(query, "days", 30, 1, 365)
        pairs: list[tuple[str, dict[str, Any]]] = []
        if kind in ("todos", "relato") and sources.reports is not None:
            pairs += [("relato", d) for d in sources.reports.recent(limit=MAX_OCCURRENCES)]
        if kind in ("todos", "automatico") and sources.audit is not None:
            pairs += [
                ("automatico", d) for d in sources.audit.recent(days=days, limit=MAX_OCCURRENCES)
            ]
        pairs.sort(key=lambda pair: pair[1]["at"], reverse=True)
        return pairs

    def find(occurrence_id: str) -> tuple[str, dict[str, Any]]:
        if sources.reports is None and sources.audit is None:
            raise ApiError(503, OFF_LEAKS)
        wanted = occurrence_id.strip().lower()
        if sources.reports is not None and (found := sources.reports.get(wanted)) is not None:
            return "relato", found
        if sources.audit is not None and (found := sources.audit.get(wanted)) is not None:
            return "automatico", found
        raise ApiError(404, "ocorrência não encontrada")

    async def occurrence_list(query: Query) -> Any:
        def read() -> dict[str, Any]:
            rows = [occurrences.item(kind, doc) for kind, doc in collect(query)]
            return {"kinds": list(occurrences.KINDS), **paged(rows, query)}

        return await _blocking(read)

    async def occurrence_detail(query: Query) -> Any:
        def read() -> dict[str, Any]:
            kind, doc = find(query["_id"][0])
            return occurrences.detail(kind, doc)

        return await _blocking(read)

    async def occurrence_export(query: Query) -> tuple[str, str, bytes]:
        def read() -> tuple[str, str, bytes]:
            wanted = first(query, "id")
            pairs = [find(wanted)] if wanted else collect(query)
            sheets = occurrences.workbook(pairs)
            for sheet in sheets:  # the same masking as every other answer of the dashboard
                sheet.rows = [[sanitize(cell) for cell in row] for row in sheet.rows]
            stamp = utcnow().astimezone().strftime("%Y%m%d-%H%M")
            return f"ocorrencias-juniper-{stamp}.xlsx", XLSX_MIME, build_xlsx(sheets)

        return await _blocking(read)

    api.file_route("/occurrences/export.xlsx", occurrence_export)

    for path, handler in {
        "/session": session,
        "/overview": overview,
        "/memories": memories,
        "/memories/": memory_detail,
        "/search": search,
        "/prompt": prompt,
        "/beliefs": beliefs,
        "/beliefs/": belief_detail,
        "/affects": affects,
        "/affects/": affect_detail,
        "/mood": mood,
        "/persona": persona,
        "/concerns": concerns,
        "/core": core,
        "/interests": interests,
        "/interests/": interest_detail,
        "/notes": notes,
        "/dreams": dreams,
        "/journal": journal,
        "/sessions": sessions,
        "/sessions/": session_turns,
        "/occurrences": occurrence_list,
        "/occurrences/": occurrence_detail,
    }.items():
        api.route(path, handler)
    return api
