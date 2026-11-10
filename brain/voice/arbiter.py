"""Which device answers when several hear the same "Juniper" (ADR-034, section 5).

Two phones and a laptop on the same desk hear one wake word and all three open a voice session:
three greetings, three answers. The devices of one person are one *user* to the brain, so the
brain picks. Rules, in order:

1. A session already open for that user stays; a newer wake word on another device does not take
   it over (that would cut a conversation in the middle).
2. A session opened by **touch** always wins: a wake round that is still open loses to it, and a
   wake session of the same user on another device is closed.
3. Otherwise the devices that claim within ``window`` seconds of the first one form a round, and
   the highest wake score wins (a device that does not say its score loses to one that does; a
   tie goes to the first to arrive). A lone device waits the window and then wins.

It only decides and remembers; ending a session or telling a device is the caller's job. Users
are plain strings, devices are plain tokens (the connection id), so the module knows neither.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Final

DEFAULT_WINDOW: Final = 0.3


@dataclass
class _Claim:
    token: str
    score: float
    order: int
    won: asyncio.Future[bool] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


@dataclass
class _Round:
    claims: list[_Claim] = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None
    manual: bool = False  # a touch-opened session took the user while the round was open


class WakeArbiter:
    def __init__(self, window: float = DEFAULT_WINDOW) -> None:
        self._window = window
        self._active: dict[str, tuple[str, bool]] = {}  # user -> (token, opened by touch)
        self._rounds: dict[str, _Round] = {}
        self._order = 0

    @property
    def window(self) -> float:
        return self._window

    def active(self, user: str) -> str | None:
        """The device whose session is open for ``user`` (None when no one's)."""
        held = self._active.get(user)
        return held[0] if held else None

    async def claim(self, user: str, token: str, score: float | None) -> bool:
        """A device heard the wake word. True when it may open the session, False when not."""
        if user in self._active:
            return False  # rule 1 (and the same device asking twice: its first one is open)
        if self._window <= 0:
            self._active[user] = (token, False)
            return True
        round_ = self._rounds.get(user)
        if round_ is None:
            round_ = self._rounds[user] = _Round()
            round_.timer = asyncio.get_running_loop().call_later(
                self._window, self._close_round, user, round_
            )
        elif any(c.token == token for c in round_.claims):
            return False
        self._order += 1
        claim = _Claim(token, -1.0 if score is None else score, self._order)
        round_.claims.append(claim)
        try:
            return await claim.won
        except asyncio.CancelledError:
            # the device went away while waiting: it must not win, nor hold the place
            round_.claims = [c for c in round_.claims if c is not claim]
            if claim.won.done() and not claim.won.cancelled() and claim.won.result():
                self.release(user, token)
            raise

    def touch(self, user: str, token: str) -> list[str]:
        """A session was opened by touch (rule 2). Returns the devices whose session must close."""
        evicted: list[str] = []
        held = self._active.get(user)
        if held is not None and held[0] != token and not held[1]:
            evicted.append(held[0])
        round_ = self._rounds.get(user)
        if round_ is not None:
            round_.manual = True  # its claims lose when it closes
        self._active[user] = (token, True)
        return evicted

    def release(self, user: str, token: str) -> None:
        """The session of ``token`` ended."""
        held = self._active.get(user)
        if held is not None and held[0] == token:
            del self._active[user]

    def _close_round(self, user: str, round_: _Round) -> None:
        if self._rounds.get(user) is round_:
            del self._rounds[user]
        claims = [c for c in round_.claims if not c.won.done()]
        winner: _Claim | None = None
        if claims and not round_.manual and user not in self._active:
            winner = max(claims, key=lambda c: (c.score, -c.order))
            self._active[user] = (winner.token, False)
        for claim in claims:
            claim.won.set_result(claim is winner)
