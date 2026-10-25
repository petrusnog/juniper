"""Crenças e confiança por evidência (T-101): a conta, o decaimento, a formação e a metacognição."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from brain.config import V2BeliefSettings
from brain.memory.v2 import confidence as conf
from brain.memory.v2.beliefs import BeliefBook
from brain.memory.v2.models import Belief, Evidence
from brain.memory.v2.store import InMemoryDocStore, V2Data
from tests.test_extraction import FakeEmbedder

DAY0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CFG = V2BeliefSettings()  # half-life 60 days, min evidence 2


def ev(kind: str, session: str = "s1", turn: int = 0, day: int = 0) -> Evidence:
    return Evidence(kind, session, turn, None, 1.0, DAY0 + timedelta(days=day))


class Clock:
    def __init__(self, now: datetime = DAY0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: float) -> None:
        self.now += timedelta(days=days)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def book(clock: Clock) -> BeliefBook:
    return BeliefBook(V2Data.over(InMemoryDocStore()), FakeEmbedder(), CFG, clock)  # type: ignore[arg-type]


STATEMENT = "Acho que o Petrus se cobra bem mais do que cobraria de outra pessoa."


# --- the arithmetic ---------------------------------------------------------------------------


def test_the_weights_of_each_source() -> None:
    assert [
        conf.weight_of(k)
        for k in ("observation", "contradiction", "explicit", "confirmation", "denial")
    ] == [1, 1, 3, 4, 4]
    with pytest.raises(ValueError):
        conf.weight_of("palpite")


def test_a_fresh_belief_is_pure_uncertainty() -> None:
    assert conf.confidence(1.0, 1.0) == 0.5
    assert conf.firmness(1.0, 1.0) == 0.0


def test_evidence_moves_the_confidence_by_its_weight() -> None:
    belief = Belief(STATEMENT)
    conf.apply_evidence(belief, ev("observation"))  # alpha 2
    assert (belief.alpha, belief.beta) == (2.0, 1.0)
    conf.apply_evidence(belief, ev("contradiction", turn=1))  # beta 2
    conf.apply_evidence(belief, ev("explicit", turn=2))  # alpha 5
    assert (belief.alpha, belief.beta) == (5.0, 2.0)
    assert conf.confidence(belief.alpha, belief.beta) == pytest.approx(5 / 7)
    assert belief.n == 5.0


def test_confirmation_and_denial_weigh_four_and_mark_the_user_answer() -> None:
    yes, no = Belief(STATEMENT), Belief(STATEMENT)
    conf.apply_evidence(yes, ev("confirmation"))
    conf.apply_evidence(no, ev("denial"))
    assert (yes.alpha, yes.user_confirmed) == (5.0, "confirmed")
    assert (no.beta, no.user_confirmed) == (5.0, "denied")


def test_the_same_reference_counts_only_once() -> None:
    belief = Belief(STATEMENT)
    assert conf.apply_evidence(belief, ev("observation", "s1", 3)) is True
    assert conf.apply_evidence(belief, ev("observation", "s1", 3)) is False  # a re-run of the night
    assert belief.alpha == 2.0 and len(belief.evidence_for) == 1
    assert conf.apply_evidence(belief, ev("observation", "s1", 4)) is True


def test_references_are_capped_but_the_counts_are_kept() -> None:
    belief = Belief(STATEMENT)
    for turn in range(conf.MAX_EVIDENCE_REFS + 10):
        conf.apply_evidence(belief, ev("observation", "s1", turn))
    assert len(belief.evidence_for) == conf.MAX_EVIDENCE_REFS
    assert belief.alpha == 1.0 + conf.MAX_EVIDENCE_REFS + 10


def test_decay_halves_the_evidence_every_half_life() -> None:
    alpha, beta = conf.decay(5.0, 1.0, days=60, half_life_days=60)
    assert (alpha, beta) == (3.0, 1.0)  # 4 of evidence -> 2
    alpha, beta = conf.decay(5.0, 1.0, days=120, half_life_days=60)
    assert (alpha, beta) == (2.0, 1.0)
    assert conf.decay(5.0, 3.0, days=0, half_life_days=60) == (5.0, 3.0)
    assert conf.decay(5.0, 3.0, days=-4, half_life_days=60) == (5.0, 3.0)


def test_decay_returns_to_uncertainty_not_to_error() -> None:
    alpha, beta = conf.decay(9.0, 1.0, days=3650, half_life_days=60)
    assert conf.confidence(alpha, beta) == pytest.approx(0.5, abs=1e-6)


def test_decay_goes_toward_a_skeptical_prior_when_that_was_the_start() -> None:
    alpha, beta = conf.decay(
        6.0, 3.2, days=10_000, half_life_days=60, prior_alpha=1.0, prior_beta=2.2
    )
    assert (alpha, beta) == (pytest.approx(1.0), pytest.approx(2.2))


@pytest.mark.parametrize(
    ("confidence", "n", "word"),
    [
        (0.20, 9, "talvez"),
        (0.44, 9, "talvez"),
        (0.45, 0, "acho que"),
        (0.69, 9, "acho que"),
        (0.85, 2, "acho que"),  # high confidence on little evidence never goes past "acho que"
        (0.85, 5.9, "acho que"),
        (0.85, 6, "tenho bastante certeza"),
        (0.70, 6, "tenho bastante certeza"),
    ],
)
def test_the_words_follow_the_confidence_and_the_evidence(
    confidence: float, n: float, word: str
) -> None:
    assert conf.band(confidence, n) == word


# --- when a hypothesis becomes a belief -------------------------------------------------------


def test_one_observation_is_a_hunch_not_a_belief() -> None:
    belief = Belief(STATEMENT)
    conf.apply_evidence(belief, ev("observation"))
    assert conf.is_formed(belief, CFG) is False and conf.next_status(belief, CFG) == "candidate"


def test_several_observations_in_the_same_session_and_day_do_not_form_a_belief() -> None:
    belief = Belief(STATEMENT)
    for turn in range(4):
        conf.apply_evidence(belief, ev("observation", "s1", turn))
    assert belief.alpha - 1 >= 2 and conf.is_formed(belief, CFG) is False


def test_two_observations_on_different_days_form_a_belief() -> None:
    belief = Belief(STATEMENT)
    conf.apply_evidence(belief, ev("observation", "s1", 1, day=0))
    conf.apply_evidence(belief, ev("observation", "s1", 5, day=3))
    assert conf.is_formed(belief, CFG) is True and conf.next_status(belief, CFG) == "active"


def test_two_observations_in_different_sessions_form_a_belief() -> None:
    belief = Belief(STATEMENT)
    conf.apply_evidence(belief, ev("observation", "s1", 1))
    conf.apply_evidence(belief, ev("observation", "s2", 1))
    assert conf.is_formed(belief, CFG) is True


def test_an_explicit_statement_forms_a_belief_at_once() -> None:
    belief = Belief(STATEMENT)
    conf.apply_evidence(belief, ev("explicit"))
    assert conf.next_status(belief, CFG) == "active"


def test_the_minimum_evidence_is_configurable() -> None:
    strict = V2BeliefSettings(min_evidence=4)
    belief = Belief(STATEMENT)
    conf.apply_evidence(belief, ev("observation", "s1", 1, day=0))
    conf.apply_evidence(belief, ev("observation", "s2", 1, day=2))
    assert conf.is_formed(belief, strict) is False and conf.is_formed(belief, CFG) is True


# --- when a belief retires --------------------------------------------------------------------


def test_a_denial_retires_at_once() -> None:
    belief = Belief(STATEMENT, status="active")
    conf.apply_evidence(belief, ev("explicit"))
    conf.apply_evidence(belief, ev("denial", turn=3))
    assert conf.next_status(belief, CFG) == "retired"


def test_clear_contradiction_retires_but_only_with_enough_evidence() -> None:
    few = Belief(STATEMENT, status="active", beta=2.0)  # confidence 1/3, n = 1
    assert conf.next_status(few, CFG) == "active"
    many = Belief(STATEMENT, status="active", alpha=1.0, beta=6.0)  # confidence 1/7, n = 5
    assert conf.next_status(many, CFG) == "retired"


def test_a_belief_that_faded_without_reinforcement_retires_unless_the_user_confirmed_it() -> None:
    faded = Belief(STATEMENT, status="active", alpha=1.1, beta=1.0)
    assert conf.next_status(faded, CFG) == "retired"
    confirmed = Belief(STATEMENT, status="active", alpha=1.1, beta=1.0, user_confirmed="confirmed")
    assert conf.next_status(confirmed, CFG) == "active"


def test_retirement_is_final() -> None:
    belief = Belief(STATEMENT, status="retired", alpha=9.0)
    assert conf.next_status(belief, CFG) == "retired"


# --- the book ---------------------------------------------------------------------------------


def test_a_new_hypothesis_is_a_candidate_with_a_reference(book: BeliefBook) -> None:
    belief, created = book.create(STATEMENT, "cobrança", ev("observation", "s1", 3))

    assert created is True and belief.status == "candidate"
    assert (belief.alpha, belief.beta, belief.n) == (2.0, 1.0, 1.0)
    saved = book.get(belief.id)
    assert (
        saved is not None
        and saved.evidence_for[0].session_id == "s1"
        and saved.embedding_model == "fake-embed"
    )
    assert book.held() == [] and [b.id for b in book.candidates()] == [belief.id]


def test_a_near_duplicate_reinforces_the_existing_belief(book: BeliefBook) -> None:
    first, _ = book.create(STATEMENT, "cobrança", ev("observation", "s1", 3, day=0))
    again, created = book.create(STATEMENT, "cobrança", ev("observation", "s2", 7, day=2))

    assert created is False and again.id == first.id
    assert again.status == "active" and again.alpha == 3.0  # two sources: it became a belief
    assert len(book.data.beliefs.list()) == 1


def test_a_different_subject_is_not_merged(book: BeliefBook) -> None:
    book.create(STATEMENT, "cobrança", ev("observation", "s1", 1))
    _, created = book.create(
        "Prefere trabalhar de madrugada com música alta.", "rotina", ev("observation", "s1", 2)
    )
    assert created is True and len(book.data.beliefs.list()) == 2


def test_confirming_raises_the_confidence_and_counts_a_hit(book: BeliefBook) -> None:
    belief, _ = book.create(STATEMENT, "cobrança", ev("observation", "s1", 1))

    confirmed = book.confirm(belief.id, "s1", 9)

    assert (
        confirmed is not None
        and confirmed.user_confirmed == "confirmed"
        and confirmed.status == "active"
    )
    assert confirmed.alpha == 6.0 and book.words(confirmed) == "acho que"
    assert book.calibration("cobrança").hits == 1.0


def test_denying_retires_the_belief_and_counts_a_miss_only_if_it_was_held(book: BeliefBook) -> None:
    held, _ = book.create(STATEMENT, "cobrança", ev("explicit", "s1", 1))
    hunch, _ = book.create(
        "Gosta de acordar tarde nos domingos.", "rotina", ev("observation", "s1", 2)
    )

    book.deny(held.id, "s1", 5)
    book.deny(hunch.id, "s1", 6)

    assert book.get(held.id).status == "retired" and book.get(hunch.id).status == "retired"  # type: ignore[union-attr]
    assert book.calibration("cobrança").misses == 1.0  # she held it, and was wrong
    assert book.calibration("rotina").misses == 0.0  # a mere hunch was never her opinion


def test_a_repeated_confirmation_reference_is_not_counted_twice(book: BeliefBook) -> None:
    belief, _ = book.create(STATEMENT, "cobrança", ev("observation", "s1", 1))
    book.confirm(belief.id, "s1", 9)
    again = book.confirm(belief.id, "s1", 9)
    assert again is not None and again.alpha == 6.0


def test_unknown_belief_ids_are_handled(book: BeliefBook) -> None:
    assert book.add_evidence("nope", ev("observation")) is None
    assert book.confirm("nope") is None and book.deny("nope") is None
    assert book.forget("nope") is False


# --- the nightly review -----------------------------------------------------------------------


def test_review_decays_beliefs_and_reports_what_changed(book: BeliefBook, clock: Clock) -> None:
    solid, _ = book.create(STATEMENT, "cobrança", ev("explicit", "s1", 1))  # alpha 4
    clock.advance(60)

    report = book.review_all()

    assert report.reviewed == 1 and report.retired == []
    after = book.get(solid.id)
    assert after is not None and after.alpha == pytest.approx(1 + 3 * 0.5)  # 3 of evidence -> 1.5
    assert after.last_reviewed_at == clock.now


def test_review_retires_what_faded_to_nothing(book: BeliefBook, clock: Clock) -> None:
    hunch, _ = book.create(
        "Gosta de acordar tarde nos domingos.", "rotina", ev("observation", "s1", 1)
    )
    clock.advance(200)  # more than three half-lives: n < 0.25
    report = book.review_all()
    assert report.retired == [hunch.id] and book.get(hunch.id).status == "retired"  # type: ignore[union-attr]
    assert book.calibration("rotina").misses == 0.0  # fading is not being wrong


def test_review_turns_a_candidate_into_a_belief_when_it_has_earned_it(
    book: BeliefBook, clock: Clock
) -> None:
    belief, _ = book.create(STATEMENT, "cobrança", ev("observation", "s1", 1, day=0))
    book.data.beliefs.update(
        belief.id,
        alpha=3.0,
        evidence_for=[
            ev("observation", "s1", 1, day=0).to_doc(),
            ev("observation", "s2", 1, day=3).to_doc(),
        ],
    )

    report = book.review_all()

    assert report.formed == [belief.id] and book.get(belief.id).status == "active"  # type: ignore[union-attr]


def test_review_skips_retired_beliefs(book: BeliefBook, clock: Clock) -> None:
    belief, _ = book.create(STATEMENT, "cobrança", ev("explicit", "s1", 1))
    book.deny(belief.id)
    clock.advance(10)
    assert book.review_all().reviewed == 0


# --- searching --------------------------------------------------------------------------------


def test_search_finds_held_beliefs_by_meaning(book: BeliefBook) -> None:
    mine, _ = book.create(STATEMENT, "cobrança", ev("explicit", "s1", 1))
    book.create(
        "Prefere trabalhar de madrugada com música alta.", "rotina", ev("observation", "s1", 2)
    )  # a candidate

    hits = book.search(
        FakeEmbedder().embed("o Petrus se cobra demais quando erra", "query"), threshold=0.2
    )

    assert [h.item.id for h in hits] == [mine.id]  # candidates are not opinions yet
    assert BeliefBook(book.data).search([1.0]) == []  # no embedder, no search


# --- metacognition ----------------------------------------------------------------------------


def test_a_topic_she_gets_wrong_starts_more_skeptical(book: BeliefBook) -> None:
    for i in range(4):  # four held beliefs on "humor", all denied
        belief, _ = book.create(
            f"Acho que o humor do Petrus {['melhora', 'piora', 'oscila', 'some'][i]} às segundas.",
            "humor",
            ev("explicit", "s1", i),
        )
        book.deny(belief.id, "s1", 20 + i)
    assert book.calibration("humor").misses == 4.0

    fresh, _ = book.create(
        "Acho que o Petrus fica ranzinza antes do café.", "humor", ev("observation", "s9", 1)
    )
    neutral, _ = book.create(
        "Acho que o Petrus planeja tudo na véspera.", "planejamento", ev("observation", "s9", 2)
    )

    assert fresh.prior_beta > 1.0 and neutral.prior_beta == 1.0
    assert book.confidence_of(fresh) < book.confidence_of(neutral)
    assert fresh.n == pytest.approx(1.0) and neutral.n == 1.0  # the prior is not evidence


def test_a_good_record_does_not_make_new_beliefs_overconfident() -> None:
    assert conf.prior_for(hits=9, misses=0) == (1.0, 1.0)
    assert conf.prior_for(hits=0, misses=0) == (1.0, 1.0)
    alpha, beta = conf.prior_for(hits=0, misses=6)
    assert alpha == 1.0 and beta > 1.0


def test_the_hit_rate_is_smoothed() -> None:
    assert conf.hit_rate(0, 0) == 0.5
    assert conf.hit_rate(3, 1) == pytest.approx(4 / 6)
