"""Several devices hear one "Juniper": only one answers (T-212, ADR-034 section 5)."""

from __future__ import annotations

import asyncio

from brain.voice.arbiter import WakeArbiter

WINDOW = 0.05


async def race(arbiter: WakeArbiter, user: str, *claims: tuple[str, float | None]) -> dict:
    results = await asyncio.gather(*(arbiter.claim(user, t, s) for t, s in claims))
    return {token: won for (token, _), won in zip(claims, results, strict=True)}


async def test_a_lone_device_wins_after_the_window() -> None:
    arbiter = WakeArbiter(WINDOW)
    loop = asyncio.get_running_loop()
    began = loop.time()
    assert await arbiter.claim("ana", "phone", 0.7) is True
    assert WINDOW * 0.8 <= loop.time() - began < WINDOW * 6  # waits for others, but not long
    assert arbiter.active("ana") == "phone"


async def test_the_highest_score_wins_inside_the_window() -> None:
    arbiter = WakeArbiter(WINDOW)
    got = await race(arbiter, "ana", ("laptop", 0.62), ("phone", 0.91), ("tablet", 0.8))
    assert got == {"laptop": False, "phone": True, "tablet": False}
    assert arbiter.active("ana") == "phone"


async def test_a_device_that_says_no_score_loses_to_one_that_does() -> None:
    got = await race(WakeArbiter(WINDOW), "ana", ("old", None), ("new", 0.3))
    assert got == {"old": False, "new": True}


async def test_a_tie_goes_to_whoever_arrived_first() -> None:
    got = await race(WakeArbiter(WINDOW), "ana", ("first", 0.8), ("second", 0.8))
    assert got == {"first": True, "second": False}


async def test_different_users_do_not_compete() -> None:
    arbiter = WakeArbiter(WINDOW)
    results = await asyncio.gather(
        arbiter.claim("ana", "ana-phone", 0.5), arbiter.claim("bia", "bia-phone", 0.4)
    )
    assert results == [True, True]


async def test_a_claim_after_the_window_does_not_join_the_round() -> None:
    arbiter = WakeArbiter(WINDOW)
    assert await arbiter.claim("ana", "phone", 0.5) is True
    assert await arbiter.claim("ana", "laptop", 0.99) is False  # too late: hers is open


async def test_an_open_session_is_never_taken_over() -> None:
    arbiter = WakeArbiter(WINDOW)
    await arbiter.claim("ana", "phone", 0.5)
    assert await arbiter.claim("ana", "laptop", 0.99) is False
    assert await arbiter.claim("ana", "phone", 0.99) is False  # the same device twice
    assert arbiter.active("ana") == "phone"


async def test_after_the_session_ends_the_next_wake_word_wins_again() -> None:
    arbiter = WakeArbiter(WINDOW)
    await arbiter.claim("ana", "phone", 0.5)
    arbiter.release("ana", "phone")
    assert arbiter.active("ana") is None
    assert await arbiter.claim("ana", "laptop", 0.4) is True


async def test_releasing_someone_elses_place_changes_nothing() -> None:
    arbiter = WakeArbiter(WINDOW)
    await arbiter.claim("ana", "phone", 0.5)
    arbiter.release("ana", "laptop")
    assert arbiter.active("ana") == "phone"


async def test_a_touch_beats_a_round_that_is_still_open() -> None:
    arbiter = WakeArbiter(WINDOW)
    waiting = asyncio.create_task(arbiter.claim("ana", "laptop", 0.99))
    await asyncio.sleep(WINDOW / 5)
    assert arbiter.touch("ana", "phone") == []
    assert await waiting is False
    assert arbiter.active("ana") == "phone"


async def test_a_touch_closes_a_wake_session_on_another_device_but_not_its_own() -> None:
    arbiter = WakeArbiter(WINDOW)
    await arbiter.claim("ana", "laptop", 0.5)
    assert arbiter.touch("ana", "phone") == ["laptop"]
    assert arbiter.touch("ana", "tablet") == []  # a touch session is not evicted by another touch
    assert arbiter.active("ana") == "tablet"


async def test_a_device_that_disappears_while_waiting_does_not_win_or_hold_the_place() -> None:
    arbiter = WakeArbiter(WINDOW)
    gone = asyncio.create_task(arbiter.claim("ana", "gone", 0.99))
    stays = asyncio.create_task(arbiter.claim("ana", "stays", 0.2))
    await asyncio.sleep(WINDOW / 5)
    gone.cancel()
    assert await stays is True  # the cancelled one cannot take the round with it
    assert arbiter.active("ana") == "stays"


async def test_a_window_of_zero_means_no_arbitration() -> None:
    arbiter = WakeArbiter(0)
    assert await arbiter.claim("ana", "phone", 0.1) is True
    assert await arbiter.claim("ana", "laptop", 0.9) is False  # still one session at a time
