"""The stages of the V3 dream that need no model: what the web taught becomes belief, her own
history becomes self-knowledge, the emotional charge of memories cools and what is weak goes
(ADR-030, section 9).

- ``world`` (NREM): a study note that two or more different domains agree on becomes a belief about
  the world, with one ``source`` evidence per domain. A note with one source stays a note.
- ``self`` (REM): from her own history, in her own voice, by fixed templates: a feeling that kept
  coming back, a trait that moved away from her usual, an interest she likes. Each is a belief
  about herself backed by ``self_observation`` evidence (one per piece of history).
- ``rem`` (REM): "sleep to forget, sleep to remember" (Walker): the charge of the feelings kept in
  refined memories falls a little every night, never below a floor; the fact never changes.
- ``prune``: feelings that faded to nothing and web notes long past their validity go.

All of it is code over her own data: idempotent (the evidence has stable references), isolated
(a failure costs the stage, not the night) and visible in ``--dry-run`` (the data is an overlay).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from brain.eventlog import event_log
from brain.memory.v2.affect import AffectBook
from brain.memory.v2.beliefs import LIVE, BeliefBook
from brain.memory.v2.knowledge import expired, validity
from brain.memory.v2.models import Belief, Evidence
from brain.memory.v2.night import PhaseContext, PhaseResult
from brain.memory.v2.persona import WORDS, PersonaBook

MAX_NOTES = 8
# A feeling is "recurrent" when it came back this many times, on at least two days.
RECURRENT_TIMES = 3
RECURRENT_DAYS = 3
MIN_FEELING = 0.4
# A trait is worth a belief when it moved this far from her usual self.
TRAIT_SHIFT = 0.08
LIKED_INTENSITY = 0.6


class WorldBeliefPhase:
    """Notes that different sites agree on become beliefs about the world."""

    name = "world"
    consumes_material = False

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        now = ctx.clock()
        book = BeliefBook(ctx.data, ctx.embedder, ctx.config.beliefs, ctx.clock)
        interests = {i.id: i for i in ctx.data.interests.list()}
        firm = [
            n
            for n in ctx.data.notes.list({"kind": "afirmacao", "source": "web"})
            if n.uncertainty == "baixa"
            and len(n.domains) >= 2
            and not expired(n, now, ctx.config.knowledge)
        ]
        created = reinforced = 0
        notes: list[str] = []
        for note in firm:
            if created >= ctx.config.beliefs.max_new_per_night:
                break
            topic = interests[note.interest_id].topic if note.interest_id in interests else "geral"
            before = _evidence_count(book, note.text)
            belief, made = book.create(
                note.text,
                topic,
                Evidence("source", domain=note.domains[0], at=now),
                about="world",
            )
            for domain in note.domains[1:]:
                book.add_source(belief.id, domain)
            if made:
                created += 1
                notes.append(f"acharia (sobre o mundo): {note.text}")
            elif _evidence_count(book, note.text) > before:
                reinforced += 1
        summary = (
            f"{len(firm)} nota(s) firme(s) da web: {created} crença(s) nova(s) sobre o mundo, "
            f"{reinforced} reforçada(s)"
        )
        return PhaseResult(
            self.name, "done", summary, created + reinforced, notes=notes[:MAX_NOTES]
        )


def _evidence_count(book: BeliefBook, statement: str) -> int:
    live = [
        b for b in book.data.beliefs.list({"status": {"$in": list(LIVE)}}) if b.about == "world"
    ]
    match = next((b for b in live if b.statement == statement), None)
    return len(match.evidence_for) if match else 0


class SelfBeliefPhase:
    """What her own history says about her, in her own voice."""

    name = "self"
    consumes_material = False

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        now = ctx.clock()
        book = BeliefBook(ctx.data, ctx.embedder, ctx.config.beliefs, ctx.clock)
        claims = [*self._feelings(ctx, now), *self._traits(ctx), *self._interests(ctx)]
        created = reinforced = challenged = 0
        notes: list[str] = []
        for statement, opposite, refs in claims:
            if created >= ctx.config.beliefs.max_new_per_night:
                break
            mine = _live_self(book)
            rival = mine.get(opposite) if opposite else None
            if rival is not None:
                for ref in refs:
                    book.add_evidence(rival.id, Evidence("contradiction", ref=ref, at=now))
                challenged += 1
            belief = mine.get(statement)
            if belief is None:
                belief, made = book.create(
                    statement,
                    "si mesma",
                    Evidence("self_observation", ref=refs[0], at=now),
                    about="self",
                    merge=False,
                )
                created += int(made)
                notes.append(f"acharia (sobre mim): {statement}")
                refs = refs[1:]
            for ref in refs:
                before = len(belief.evidence_for)
                updated = book.add_self_observation(belief.id, ref)
                if updated is not None and len(updated.evidence_for) > before:
                    reinforced += 1
                    belief = updated
        summary = (
            f"{len(claims)} sinal(is) do meu próprio histórico: {created} crença(s) nova(s) "
            f"sobre mim, {reinforced} evidência(s) somada(s), {challenged} contestada(s)"
        )
        return PhaseResult(
            self.name, "done", summary, created + challenged, notes=notes[:MAX_NOTES]
        )

    @staticmethod
    def _feelings(ctx: PhaseContext, now: Any) -> list[tuple[str, str | None, list[str]]]:
        """A feeling that came back on several days: "tenho andado com irritação"."""
        since = now - timedelta(days=RECURRENT_DAYS)
        by_kind: dict[str, list[Any]] = {}
        for a in ctx.data.affects.list():
            if a.origin == "manual" or a.about == "situation" or a.occurred_at < since:
                continue
            if a.intensity >= MIN_FEELING:
                by_kind.setdefault(a.kind, []).append(a)
        claims = []
        for kind, items in sorted(by_kind.items()):
            days = {a.occurred_at.date() for a in items}
            if len(items) >= RECURRENT_TIMES and len(days) >= 2:
                refs = [f"affect:{a.id}" for a in sorted(items, key=lambda a: a.occurred_at)]
                claims.append((f"Tenho andado com {kind} nos últimos dias.", None, refs))
        return claims

    @staticmethod
    def _traits(ctx: PhaseContext) -> list[tuple[str, str | None, list[str]]]:
        """A trait that moved away from her usual self: "tenho andado mais curiosa"."""
        state = PersonaBook(ctx.data, ctx.config.persona, ctx.clock).current()
        if state is None:
            return []
        claims = []
        for name, trait in sorted(state.traits.items()):
            word = WORDS.get(name)
            shift = trait.value - trait.baseline
            if word is None or abs(shift) < TRAIT_SHIFT:
                continue
            more, less = f"Tenho andado mais {word}.", f"Tenho andado menos {word}."
            claims.append(
                (more, less, [f"persona:{name}:v{state.version}"])
                if shift > 0
                else (less, more, [f"persona:{name}:v{state.version}"])
            )
        return claims

    @staticmethod
    def _interests(ctx: PhaseContext) -> list[tuple[str, str | None, list[str]]]:
        """An interest she likes and keeps studying: "gosto de música psicodélica"."""
        claims = []
        for i in ctx.data.interests.list():
            if i.stance == "like" and i.intensity >= LIKED_INTENSITY:
                claims.append((f"Gosto de {i.topic}.", None, [f"interest:{i.id}:{i.level}"]))
        return claims


def _live_self(book: BeliefBook) -> dict[str, Belief]:
    return {
        b.statement: b
        for b in book.data.beliefs.list({"status": {"$in": list(LIVE)}})
        if b.about == "self"
    }


class EmotionRestPhase:
    """REM: the emotional charge of the memories cools, the fact stays."""

    name = "rem"
    consumes_material = False

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        night = ctx.config.night
        base = getattr(ctx.memory, "extractor", None)
        if base is None or night.rem_relief <= 0:
            return PhaseResult(self.name, "skipped", "sem memória refinada ou o REM está desligado")
        store: Any = base.store
        cooled = 0
        for memory in store.list_memories(None, False):
            if memory.status_reflexao != "refinada" or not memory.sentimento:
                continue
            softer = []
            for item in memory.sentimento:
                value = float(item.get("intensity", 0.0))
                floor = min(value, night.rem_floor)
                softer.append(
                    {**item, "intensity": round(max(floor, value * (1 - night.rem_relief)), 4)}
                )
            if softer != memory.sentimento:
                if not ctx.dry_run:
                    store.update(memory.id, sentimento=softer)
                cooled += 1
        return PhaseResult(
            self.name,
            "done",
            f"{cooled} lembrança(s) com a carga emocional um pouco mais fria",
            changed=cooled,
        )


class PrunePhase:
    """What is weak goes: faded feelings and web notes long past their validity."""

    name = "prune"
    consumes_material = False

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        now = ctx.clock()
        feelings = AffectBook(ctx.data, ctx.config.affect, ctx.clock).prune(now)
        notes = 0
        for note in ctx.data.notes.list({"kind": "afirmacao", "source": "web"}):
            span = validity(note, ctx.config.knowledge)
            late = span is not None and note.fetched_at is not None
            if late and now - note.fetched_at > span * ctx.config.night.prune_after:  # type: ignore[operator]
                ctx.data.notes.delete(note.id)
                notes += 1
        event_log.log("dream_prune", feelings=feelings, notes=notes)
        return PhaseResult(
            self.name,
            "done",
            f"{feelings} sentimento(s) esgotado(s) e {notes} nota(s) da web vencida(s) saíram",
            changed=feelings + notes,
        )
