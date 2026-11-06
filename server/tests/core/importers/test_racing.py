"""The flipper the thread races use (``racing.py``), with fake swaps that record the state: each
phase is a fixed number of walks, no walk is in flight across a change of state, a burst's walks
have exactly one flip injected where ``INJECT_EVERY`` and ``SPREAD`` say and are counted
``straddled``, and only a walk that saw both states without one is counted ``raced``; the walker
waits while the flipper has not moved on, up to the deadline; nothing is flipped after the stop
but the swap back; the flipper's errors reach the test."""

import os
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from tests.core.importers import racing
from tests.core.importers.racing import GRACE, Flipper

TIGHT = 3


class _Fake:
    """A component's state, swapped or not, and each flip made (with whether the race had
    stopped then). ``gate`` holds a swap made in a phase that holds ``gated``, and any flip for
    which ``stuck`` is true (given the flip's name), until it is set; when ``fails``, a swap
    raises."""

    def __init__(
        self,
        gated: bool | None = None,
        stuck: Callable[[str], bool] | None = None,
        fails: bool = False,
    ) -> None:
        self.swapped = False
        self.flips: list[tuple[str, bool]] = []
        self.gate = threading.Event()
        self.gated, self.stuck, self.fails = gated, stuck, fails
        self.flipper: Flipper | None = None

    def _held(self, flip: str) -> None:
        if self.stuck is not None and self.stuck(flip):
            self.gate.wait(30)

    def _note(self, flip: str) -> None:
        self.flips.append((flip, self.flipper is not None and not self.flipper.running))

    def swap(self) -> None:
        if self.fails:
            raise OSError("swap failed")
        held = self.flipper is not None and self.flipper.holding is self.gated
        if self.gated is not None and held:
            self.gate.wait(30)
        self._held("swap")
        self._note("swap")
        self.swapped = True

    def unswap(self) -> None:
        self._held("unswap")
        self._note("unswap")
        self.swapped = False


def _flipper(fake: _Fake, seconds: float, free: bool = True, tight: int = TIGHT) -> Flipper:
    flipper = Flipper(
        fake.swap, fake.unswap, time.monotonic() + seconds, tight=tight, held=1, free=free
    )
    fake.flipper = flipper
    return flipper


def _until(condition: Callable[[], bool], seconds: float = 10) -> bool:
    ends = time.monotonic() + seconds
    while not condition() and time.monotonic() < ends:
        time.sleep(0.001)
    return condition()


def test_each_cycle_is_a_burst_and_then_each_state_held_for_a_whole_walk() -> None:
    """Every walk ends in the phase it began in; a walk of a held phase sees its state alone and
    no flip, and is never counted raced; and the phases come as a burst of ``TIGHT`` walks, one
    unswapped, one swapped, for exactly as many walks as the cycles counted. A walk here sleeps,
    so that the free flips land during it."""
    fake = _Fake()
    walks: list[tuple[bool | None, bool | None, bool, bool, bool]] = []
    with _flipper(fake, 30) as flipper:
        while flipper.running and flipper.cycles < 20:
            begun = flipper.generation
            holding, swapped = flipper.holding, fake.swapped
            time.sleep(0.0002)
            unflipped = begun == flipper.generation
            walks.append((holding, flipper.holding, swapped, fake.swapped, unflipped))
            flipper.walked(begun)
    assert flipper.cycles == 20
    assert [walk[0] for walk in walks] == ([None] * TIGHT + [False, True]) * 20
    for began, ended, swapped_then, swapped_after, unflipped in walks:
        assert began is ended, walks
        if began is not None:
            assert (swapped_then, swapped_after, unflipped) == (began, began, True), walks
    assert 0 < flipper.raced <= TIGHT * 20
    assert flipper.straddled == 0
    assert not fake.swapped


@pytest.mark.parametrize("free", [False, True])
def test_an_injected_flip_is_one_flip_before_the_open_named_and_straddles_the_walk(
    monkeypatch: pytest.MonkeyPatch, free: bool
) -> None:
    """The walks of a burst numbered a multiple of ``INJECT_EVERY`` (but the first) begin
    unswapped with the free flips paused, and have one flip injected, before the open
    ``(n // INJECT_EVERY) % SPREAD`` when they make that many: their opens see the component
    unswapped before it and swapped from it on, and they alone are counted straddled. With no
    free flips no other walk sees a change, and none is counted raced; with them, only walks of a
    burst that saw both states are."""
    opens = 5
    seen: list[int] = []
    fake = _Fake()
    flipper = _flipper(fake, 30, free=free)

    def fake_open(*given: Any, **more: Any) -> int:
        seen.append(flipper.generation % 2)
        return -1

    monkeypatch.setattr(os, "open", fake_open)
    flipper.inject_at_opens(monkeypatch)
    walks: list[tuple[int, bool | None, int, list[int]]] = []
    with flipper:
        for number in range(40 * racing.INJECT_EVERY):
            begun, holding = flipper.generation, flipper.holding
            seen.clear()
            for _ in range(opens):
                os.open("anything", os.O_RDONLY)
            walks.append((number, holding, begun % 2, list(seen)))
            flipper.walked(begun)
    injected = others = 0
    for number, holding, began, states in walks:
        at = (number // racing.INJECT_EVERY) % racing.SPREAD
        if holding is None and number and number % racing.INJECT_EVERY == 0 and at < opens:
            injected += 1
            assert (began, states) == (0, [0] * at + [1] * (opens - at)), (number, states)
        elif holding is None:
            others += 1
            if not free:
                assert states == [began] * opens, (number, states)
    assert injected > 0
    assert flipper.straddled == injected
    assert flipper.raced <= others if free else flipper.raced == 0
    assert not fake.swapped


def _walks_with_opens(
    monkeypatch: pytest.MonkeyPatch,
    flipper: Flipper,
    walks: int,
    opens: int,
    during: Callable[[int, int], None] = lambda walk, at: None,
) -> list[tuple[int, bool | None, int, list[int], bool | None]]:
    """``walks`` walks of ``opens`` fake opens each, injected at: for each, its number, the phase
    it began in, the state it began in, the state each open saw, and the phase it ended in.
    ``during`` is called before each open with the walk's number and the open's."""
    seen: list[int] = []

    def fake_open(*given: Any, **more: Any) -> int:
        seen.append(flipper.generation % 2)
        return -1

    monkeypatch.setattr(os, "open", fake_open)
    flipper.inject_at_opens(monkeypatch)
    made: list[tuple[int, bool | None, int, list[int], bool | None]] = []
    with flipper:
        for number in range(walks):
            begun, holding = flipper.generation, flipper.holding
            seen.clear()
            for at in range(opens):
                during(number, at)
                os.open("anything", os.O_RDONLY)
            made.append((number, holding, begun % 2, list(seen), flipper.holding))
            flipper.walked(begun)
    return made


def test_every_injected_walk_of_a_long_burst_begins_unswapped_and_the_flips_cover_the_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bursts of many injected walks among free flips: each injected walk begins unswapped (the
    flipper unswaps when it pauses) and meets one flip, before each of its opens in turn; and
    each held phase holds its state, the unswapped one included (a burst ends unswapped)."""
    opens = 12
    fake = _Fake()
    tight = 8 * racing.INJECT_EVERY
    flipper = _flipper(fake, 60, tight=tight)
    walks = _walks_with_opens(monkeypatch, flipper, 6 * (tight + 2), opens)
    positions: set[int] = set()
    for number, holding, began, states, _ in walks:
        quiet = holding is None and number and number % racing.INJECT_EVERY == 0
        if quiet and states != [0] * opens:
            at = states.index(1)
            assert (began, states) == (0, [0] * at + [1] * (opens - at)), (number, states)
            positions.add(at)
        elif quiet:
            assert began == 0, (number, states)
        elif holding is not None:
            assert (began, states) == (int(holding), [int(holding)] * opens), (number, states)
    assert positions == set(range(opens))
    assert not fake.swapped


def test_a_change_seen_only_at_the_opens_is_counted_raced(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no free flips, a burst's walk that meets a flip after its second open and the flip
    back after its fourth begins and ends in one state and saw the other at its opens: it is
    counted raced, and no walk that did not is."""
    fake = _Fake()
    flipper = _flipper(fake, 30, free=False, tight=12)
    flipped: set[int] = set()

    def during(number: int, at: int) -> None:
        if number % racing.INJECT_EVERY == 2 and flipper.holding is None and at in (2, 4):
            flipper._toggle()  # pyright: ignore[reportPrivateUsage]
            flipped.add(number)

    walks = _walks_with_opens(monkeypatch, flipper, 5 * 14, 5, during)
    assert flipped
    for number, _, began, states, _ in walks:
        if number in flipped:
            assert states == [began] * 2 + [1 - began] * 2 + [began], (number, states)
    assert flipper.raced == len(flipped)
    assert not fake.swapped


def test_a_burst_is_most_of_a_cycle() -> None:
    """The free race and the injected flips happen in bursts alone: the held phases, a walk each
    way, are a small share of a cycle."""
    assert racing.TIGHT >= 50 * 2 * racing.HELD


def test_the_walker_waits_while_the_flipper_has_not_moved_on() -> None:
    """The held swap kept waiting: the walk that ends the unswapped phase does not return from
    ``walked`` until the swap is made, and the next walk then sees it."""
    fake = _Fake(gated=True)
    returned: list[bool] = []

    def walker(flipper: Flipper) -> None:
        while flipper.running and len(returned) < TIGHT + 1:
            flipper.walked(flipper.generation)
            returned.append(fake.swapped)

    with _flipper(fake, 30) as flipper:
        thread = threading.Thread(target=walker, args=(flipper,))
        thread.start()
        assert _until(lambda: flipper.holding is True and len(returned) == TIGHT)
        assert not _until(lambda: len(returned) > TIGHT, 0.2)  # a while, and still waiting
        assert not fake.swapped
        fake.gate.set()
        thread.join(10)
    assert returned[TIGHT:] == [True]


@pytest.mark.parametrize("stopped_holding", [False, True])
def test_nothing_is_flipped_after_the_stop_but_the_swap_back(stopped_holding: bool) -> None:
    """The race stopped while a state is held: no flip is made after the stop, but the swap back
    of a component held swapped, which is made."""
    fake = _Fake()
    with _flipper(fake, 30) as flipper:
        while flipper.holding is not stopped_holding:
            flipper.walked(flipper.generation)
    after = [flip for flip, stopped in fake.flips if stopped]
    assert after == (["unswap"] if stopped_holding else [])
    assert not fake.swapped


def _race(flipper: Flipper, body_raises: bool = False, opens: int = 0) -> None:
    """A race's loop, walks of ``opens`` opens, until the flipper stops it (or 10 s pass); then
    the body raises, or not."""
    ends = time.monotonic() + 10
    with flipper:
        while flipper.running and time.monotonic() < ends:
            begun = flipper.generation
            for _ in range(opens):
                os.open("anything", os.O_RDONLY)
            flipper.walked(begun)
        if body_raises:
            raise ValueError("the body")


STUCK: dict[str, Callable[[Flipper, str], bool]] = {
    "the unswap ending a burst": lambda flipper, flip: (
        flip == "unswap" and flipper.holding is None and not flipper._quiet
    ),
    "the held swap": lambda flipper, flip: flipper.holding is True,
    "an injected flip": lambda flipper, flip: flipper._quiet and flipper._settled,
    "the unswap of a pause": lambda flipper, flip: flipper._quiet and not flipper._settled,
}
"""Where the flipper is stuck, by each wait of the walker: a burst's end, a held phase's end, an
injected flip and the flipper's pause (with no free flips, the burst of walks 0 to 11 has flips
injected in walks 4 and 8, the second after a pause that unswaps, and ends swapped)."""


@pytest.mark.parametrize("site", STUCK)
def test_a_flipper_stuck_at_the_deadline_fails_the_race_by_its_deadline(
    monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    """A flip that never returns, at each place the walker waits for the flipper: the wait ends
    at the deadline, the race's loop ends, and the exit fails, saying so, a grace after it."""
    fake = _Fake()
    flipper = _flipper(fake, 0.5, free=False, tight=12)
    fake.stuck = lambda flip: STUCK[site](flipper, flip)
    monkeypatch.setattr(os, "open", lambda *given, **more: -1)
    flipper.inject_at_opens(monkeypatch)
    started = time.monotonic()
    try:
        with pytest.raises(AssertionError, match="had not moved on by the deadline"):
            _race(flipper, opens=5)
        assert time.monotonic() - started < 0.5 + GRACE + 1.0
    finally:
        fake.gate.set()


def test_an_error_of_the_flipper_reaches_the_test() -> None:
    """Raised on exit, caused by the flipper's error; or, when the body raised too, noted on the
    body's error."""
    with pytest.raises(AssertionError, match="the flipper failed") as failed:
        _race(_flipper(_Fake(fails=True), 10))
    assert isinstance(failed.value.__cause__, OSError)
    with pytest.raises(ValueError, match="the body") as raised:
        _race(_flipper(_Fake(fails=True), 10), body_raises=True)
    assert any("the flipper failed" in note for note in raised.value.__notes__)
