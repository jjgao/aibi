"""A thread that races walks by flipping a component between a directory and a symlink out
(``test_walk.py``, ``test_confine_walk.py``; issues #146, #150).

A flipper that only flips in a tight loop may starve the walks of either state on a loaded
machine: a walk then always meets a flip, and never gets through, however long it runs. So the
flipper keeps a cycle of three phases, each a fixed number of the walks the racing thread
reports (``walked``), not a time:

- ``tight`` walks during a burst of free flips, swap and swap back with no yield: the race
  itself, a flip landing inside a walk when the machine runs the two threads so;
- ``held`` walks with the component held unswapped, and then ``held`` with it held swapped.

At the end of each phase the walker waits in ``walked`` until the flipper has flipped to the next
phase's state and opened it, so no walk is ever in flight across a phase's change: every walk of
a held phase sees its state alone, one walk is enough for each, and a cycle is exactly ``tight``
and twice ``held`` walks however the machine schedules the two threads. Got-through walks,
refused walks and cycles are therefore guaranteed, up to the deadline the flipper is given.

A free flip lands inside a walk only when the machine runs the flipper while the walk is on its
way, which on a busy processor is rare. So some of a burst's walks straddle a flip by
construction (``inject_at_opens``): before every ``INJECT_EVERY``-th walk the flipper pauses its
free flips with the component unswapped, and the walk asks it for exactly one flip before one of
its opens (a different one from one such walk to the next, ``SPREAD``) and waits until it is
made: the walk begins inside, and its opens see the swap at a known point, on any machine. Each
swap and swap back bumps ``generation``, whose parity is the state; ``walked`` counts the walk
``straddled`` when a flip was injected in it, and ``raced`` when, with none injected, it saw both
states (at its start, an open or its end): the free race, which only the scheduler makes."""

import os
import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any

import pytest

TIGHT = 128
"""Walks of each burst of free flips."""
HELD = 1
"""Walks of each held phase: each is wholly in the state held, since none is in flight across a
change."""
INJECT_EVERY = 4
"""Every this-many-th walk of a burst begins unswapped, with the free flips paused, and has one
flip injected before one of its opens."""
SPREAD = 16
"""The open a flip is injected before: the ``k``-th, ``k`` cycling through ``SPREAD`` positions
from one injected walk to the next (a walk with fewer opens has none injected)."""
GRACE = 1.0
"""Seconds past its deadline that the flipper is given to stop."""


class Flipper:
    """The flipping thread, started and stopped as a context; the walker reads ``generation``
    before each walk and calls ``walked`` with it after, while ``running``. A failure of the
    flipper's own calls ends the race, and so does a flipper that has not moved on by the
    deadline; the first, or a flipper that has not stopped a grace after the deadline, is raised
    on exit (or noted on the body's error, when the body raised)."""

    def __init__(
        self,
        swap: Callable[[], None],
        unswap: Callable[[], None],
        deadline: float,
        tight: int = TIGHT,
        held: int = HELD,
        free: bool = True,
    ) -> None:
        self._swap, self._unswap = swap, unswap
        self._deadline, self._tight, self._held, self._free = deadline, tight, held, free
        self._condition = threading.Condition()
        self._stop = False
        self._walks = 0
        self._until = tight
        """The count of walks that ends the current phase: the walker waits there."""
        self._requested = self._served = 0
        """Flips the walker asked for, and the flipper made."""
        self._injecting = False
        self._quiet = self._settled = False
        """Whether walks are injected; whether the current walk is one (the free flips paused),
        and whether the flipper has paused, unswapped."""
        self._opens = 0
        self._seen: set[int] = set()
        self._injected = False
        """In the current walk: its opens, the states its opens saw, and whether a flip was
        injected."""
        self._error: BaseException | None = None
        self._stalled = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.holding: bool | None = None
        """The state held in the phase the next walk is in (swapped or not), or ``None`` in a
        burst."""
        self.generation = 0
        """Swaps and swaps back made: odd while swapped."""
        self.straddled = 0
        """Walks with a flip injected during them."""
        self.raced = 0
        """Walks with none injected that saw both states: the free race."""
        self.cycles = 0
        """Cycles completed: each a burst and both states held."""

    @property
    def running(self) -> bool:
        return not (self._stop or self._error is not None or self._stalled)

    def walked(self, generation: int) -> None:
        """A walk that began at ``generation`` ended. Returns when the next may begin, or at the
        deadline, which ends the race."""
        seen = self._seen | {generation % 2, self.generation % 2}
        if self._injected:
            self.straddled += 1
        elif len(seen) == 2:
            self.raced += 1
        self._opens, self._seen, self._injected = 0, set(), False
        with self._condition:
            self._walks += 1
            self._quiet = False
            self._condition.notify_all()
            if not self._condition.wait_for(
                lambda: self._stop or self._walks < self._until, self._left()
            ):
                self._stalled = True
            elif self._injecting and self.holding is None and self._walks % INJECT_EVERY == 0:
                self._quiet, self._settled = True, False
                self._condition.notify_all()
                if not self._condition.wait_for(lambda: self._stop or self._settled, self._left()):
                    self._stalled = True

    def _left(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def _inject(self) -> None:
        """One flip asked of the flipper, and waited for (up to the deadline)."""
        with self._condition:
            self._requested += 1
            wanted = self._requested
            self._condition.notify_all()
            if self._condition.wait_for(lambda: self._stop or self._served >= wanted, self._left()):
                self._injected = self._injected or self._served >= wanted
            else:
                self._stalled = True

    def _toggle(self) -> None:
        (self._unswap if self.generation % 2 else self._swap)()
        self.generation += 1

    def _next(self, holding: bool | None, walks: int) -> bool:
        """The current phase's end awaited, then the next opened: ``walks`` walks, ``holding``;
        false, and nothing opened, once stopped."""
        with self._condition:
            self._condition.wait_for(lambda: self._stop or self._walks >= self._until)
            if self._stop:
                return False
            self.holding = holding
            self._until += walks
            self._condition.notify_all()
            return True

    def _burst(self) -> None:
        """Free flips (when ``free``, and no injected walk is on its way), and the flips the
        walker asks for, one at a time, until the burst's walks are made; it ends unswapped."""
        while not self._stop and self._walks < self._until:
            if self._served < self._requested:
                self._toggle()
                with self._condition:
                    self._served += 1
                    self._condition.notify_all()
            elif self._quiet and not self._settled:
                if self.generation % 2:
                    self._toggle()
                with self._condition:
                    self._settled = True
                    self._condition.notify_all()
            elif self._quiet or not self._free:
                with self._condition:
                    self._condition.wait_for(
                        lambda: (
                            self._stop
                            or self._walks >= self._until
                            or self._served < self._requested
                            or (self._quiet and not self._settled)
                            or (not self._quiet and self._free)
                        )
                    )
            else:
                self._toggle()
                self._toggle()
        if self.generation % 2:
            self._toggle()

    def _run(self) -> None:
        try:
            while True:
                self._burst()
                if not self._next(False, self._held) or not self._next(True, 0):
                    return
                self._toggle()
                if not self._next(True, self._held) or not self._next(None, 0):
                    return
                self._toggle()
                self.cycles += 1
                if not self._next(None, self._tight):
                    return
        except BaseException as error:  # raised again on exit, in the test's thread
            self._error = error
        finally:
            with self._condition:
                self._stop = True
                self._condition.notify_all()
            if self.generation % 2 and self._error is None:
                self._unswap()
                self.generation += 1

    def inject_at_opens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every ``INJECT_EVERY``-th walk ``n`` of a burst begun with the free flips paused,
        unswapped, and ``os.open`` made to ask for one flip, and wait for it, before its open
        numbered ``(n // INJECT_EVERY) % SPREAD``; and to note the state each open saw."""
        real = os.open
        self._injecting = True

        def opening(*given: Any, **more: Any) -> int:
            at = (self._walks // INJECT_EVERY) % SPREAD
            if self._quiet and not self._injected and self._opens == at:
                self._inject()
            self._seen.add(self.generation % 2)
            self._opens += 1
            return real(*given, **more)

        monkeypatch.setattr(os, "open", opening)

    def __enter__(self) -> "Flipper":
        self._thread.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        self._thread.join(self._left() + GRACE)
        if self._error is not None:
            failure = AssertionError(f"the flipper failed: {self._error!r}")
        elif self._thread.is_alive():
            failure = AssertionError(
                f"the flipper had not moved on by the deadline, nor stopped {GRACE:g} s after it "
                f"({self.counts()}, in a phase holding {self.holding})"
            )
        else:
            return
        if value is not None:
            value.add_note(f"and {failure}")
            return
        raise failure from self._error

    def counts(self) -> str:
        return (
            f"{self.cycles} flipper cycles, {self.generation} flips, {self.straddled} walks "
            f"straddling an injected flip, {self.raced} racing the free flips"
        )
