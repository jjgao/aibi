"""What confinement refuses when a system call fails (SPEC §14, D232, issue #146): one table of
operation, step and error to outcome, held against what the module did before the walk.

``HEAD`` is the table recorded, by fault injection, from the module as it was before it walked
(main at 279fafb): for six operations (``confine``; ``identity`` of a path a pack joined; ``read``
of a confined file and of a pack's path; ``files`` of a confined directory and of a pack's path),
every call of ``os.open``, ``stat``, ``lstat``, ``fstat``, ``readlink`` and ``scandir`` made during
the operation was failed in turn with each of eleven errors, and the outcome recorded under the
call's step (``resolve`` is ``realpath``'s, ``unlinked`` the pack path's components', ``leafstat``
the identity's, ``read.open`` and ``files.open`` the one open, ``entry`` a listed entry's
classifying ``lstat``). It was written here before the walk was.

The walk replaces the one open (and the identity's ``stat``) by several calls; each projects onto
the step it replaces, and takes that step's outcome whatever the call: ``read``'s walk the open's
("cannot be opened as confined", or the lease's text for ``EAGAIN``), ``files``' walk the
directory's open's ("Not a directory that can be opened as confined"), the identity's walk the leaf
``stat``'s ("No such file or directory"). The step of a call is read from the call site (the stack),
never from anything the walk says. The test fails on a call it cannot place, on a step with no row,
and on a row that never fired; and every run leaves no descriptor open.

What differs from ``HEAD`` is stated in ``STATED`` and ``GONE``, and nowhere else: the identity's
walk out of descriptors (``EMFILE``, ``ENFILE``) is the read's resource text, not "No such file or
directory" (the walk needs two descriptors where the ``stat`` needed none); and a listed entry is no
longer confined again by its path, so the steps that did (``realpath`` and the ``stat`` of each
entry) are gone from the listing."""

import errno
import os
import posixpath
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any

import pytest

from aibi.core.importers import walk
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.pack_api import ConfinedPath

CALLS = ("open", "stat", "lstat", "fstat", "readlink", "scandir")
ERRORS = (
    "ELOOP",
    "ENOTDIR",
    "ENOENT",
    "ESTALE",
    "EACCES",
    "EMFILE",
    "ENFILE",
    "ENOMEM",
    "EAGAIN",
    "EIO",
    "ValueError",
)
OPERATIONS = ("confine", "identity-pack", "read", "read-pack", "files", "files-pack")

MISSING = "R:No such file or directory: "
CANNOT = "R:The file cannot be opened as confined: "
LOCKED = "R:The file is locked by another process, and opening it does not wait: "
NOT_A_DIRECTORY = "R:Not a directory that can be opened as confined: "
RAW = "RAW"
"""The error injected came out (no refusal), as it did before: its class and ``errno`` (the walk
gives a ``ValueError`` of its own text, which names no component)."""
ONE_UNCONFINED = "OK:one unconfined"
"""The listing, the entry whose call failed returned as ``unconfined``."""


def _every(outcome: str, **but: str) -> dict[str, frozenset[str]]:
    return {name: frozenset({but.get(name, outcome)}) for name in ERRORS}


def _either(*outcomes: str) -> dict[str, frozenset[str]]:
    return {name: frozenset(outcomes) for name in ERRORS}


_READ_OPEN = _every(CANNOT, EAGAIN=LOCKED, ValueError=RAW)
HEAD: dict[tuple[str, str], dict[str, frozenset[str]]] = {
    ("confine", "resolve.lstat"): _every(MISSING),
    ("confine", "leafstat.stat"): _every(MISSING),
    ("identity-pack", "resolve.lstat"): _every(MISSING),
    ("identity-pack", "leafstat.stat"): _every(MISSING),
    ("identity-pack", "unlinked.lstat"): _every(MISSING, ValueError=RAW),
    ("read", "read.open"): _READ_OPEN,
    ("read", "read.fstat"): _every(RAW),
    ("read-pack", "resolve.lstat"): _every(MISSING),
    ("read-pack", "leafstat.stat"): _every(MISSING),
    ("read-pack", "unlinked.lstat"): _every(MISSING, ValueError=RAW),
    ("read-pack", "read.open"): _READ_OPEN,
    ("read-pack", "read.fstat"): _every(RAW),
    ("files", "files.open"): _every(NOT_A_DIRECTORY, ValueError=RAW),
    ("files", "files.fstat"): _every(RAW),
    ("files", "files.scandir"): _every(RAW),
    ("files", "entry.lstat"): _every(ONE_UNCONFINED, ValueError=RAW),
    ("files", "resolve.lstat"): _every(ONE_UNCONFINED),
    ("files", "leafstat.stat"): _every(ONE_UNCONFINED),
    ("files-pack", "resolve.lstat"): _either(MISSING, ONE_UNCONFINED),
    ("files-pack", "leafstat.stat"): _either(MISSING, ONE_UNCONFINED),
    ("files-pack", "unlinked.lstat"): _every(MISSING, ValueError=RAW),
    ("files-pack", "files.open"): _every(NOT_A_DIRECTORY, ValueError=RAW),
    ("files-pack", "files.fstat"): _every(RAW),
    ("files-pack", "files.scandir"): _every(RAW),
    ("files-pack", "entry.lstat"): _every(ONE_UNCONFINED, ValueError=RAW),
}
"""The table recorded from the module before the walk (279fafb), folded by step: every call of a
step gave the same outcome for an error, but ``files-pack``'s ``resolve`` and ``leafstat``, whose
calls were the directory's confining ("No such file or directory") and each entry's (that entry
``unconfined``)."""
HEAD_SUCCESS = {
    "confine": "OK",
    "identity-pack": "OK",
    "read": "OK:inside\n",
    "read-pack": "OK:inside\n",
    "files": "OK:files",
    "files-pack": "OK:files",
}

IDENTIFYING = ("confine", "identity-pack", "read-pack", "files-pack")
"""The operations that take a path's identity, by the walk, while they run."""
STATED: dict[tuple[str, str, str], frozenset[str]] = {
    (operation, "leafstat.stat", name): frozenset({CANNOT})
    for operation in IDENTIFYING
    for name in ("EMFILE", "ENFILE")
}
"""The identity's walk (or its ``stat`` through the walked directory) out of descriptors is the
read's resource text: the walk holds two descriptors where the ``stat`` by path held none, and a
file that ran out of them is not missing (D232; borrowed from ``read`` for a directory as well)."""
GONE = {("files", "resolve.lstat"), ("files", "leafstat.stat")}
"""A listed entry is no longer confined again by its path (``realpath`` and ``stat``): its identity
is the listing's own ``lstat`` through the directory's descriptor, which the read checks."""


def expected(operation: str, step: str, name: str) -> frozenset[str]:
    """What the walking module must give for ``name`` failing at ``step`` of ``operation``."""
    if (operation, step, name) in STATED:
        return STATED[(operation, step, name)]
    outcomes = HEAD[(operation, step)][name]
    if operation == "files-pack" and step in ("resolve.lstat", "leafstat.stat"):
        return outcomes - {ONE_UNCONFINED}  # the directory's own confining alone (GONE)
    return outcomes


def _error(name: str) -> BaseException:
    if name == "ValueError":
        return ValueError("embedded null byte")
    if name == "EAGAIN":
        return BlockingIOError(errno.EAGAIN, name)
    return OSError(getattr(errno, name), name)


def _confining(frame: FrameType) -> str | None:
    """The step a call through the walk projects onto: the confinement's method that walked."""
    while frame is not None:
        name = frame.f_code.co_name
        if name == "_status":
            return "leafstat.stat"
        if name in ("_opened", "read"):
            return "read.open"
        if name == "files":
            return "files.open"
        frame = frame.f_back  # pyright: ignore[reportAssignmentType]
    return None


def _step(call: str, frame: FrameType) -> str:
    """The step of the call ``frame`` makes, from its call site alone."""
    module = frame.f_globals.get("__name__")
    if module == posixpath.__name__:  # ``realpath``, whatever its helpers are called
        return f"resolve.{call}"
    if module == walk.__name__:
        return _confining(frame) or f"?walk.{call}"
    name = frame.f_code.co_name
    if name == "_unlinked":
        return f"unlinked.{call}"
    if name == "_entry":
        return f"entry.{call}"
    if name in ("_status", "_resolved"):
        return "leafstat.stat"
    if name == "_opened":
        return "read.open"
    if name in ("read", "files"):
        return f"{name}.{call}"
    return f"?{name}.{call}"


class _Injected:
    """Fails the call numbered ``at`` (from 0) with ``name``; records each call's step."""

    def __init__(self, at: int | None, name: str | None) -> None:
        self.at, self.name = at, name
        self.steps: list[str] = []
        self.raised: BaseException | None = None
        self.on = True

    @contextmanager
    def installed(self) -> Iterator[None]:
        saved = {call: getattr(os, call) for call in CALLS}
        for call in CALLS:
            setattr(os, call, self._hook(call, saved[call]))
        try:
            yield
        finally:
            for call, function in saved.items():
                setattr(os, call, function)

    def _hook(self, call: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def hooked(*given: Any, **more: Any) -> Any:
            if self.on:
                self.on = False
                try:
                    step = _step(call, sys._getframe(1))  # pyright: ignore[reportPrivateUsage]
                finally:
                    self.on = True
                self.steps.append(step)
                if len(self.steps) - 1 == self.at and self.name is not None:
                    self.raised = _error(self.name)
                    raise self.raised
            return real(*given, **more)

        return hooked


def _tree(base: Path) -> Path:
    inside = base / "imports"
    leaf = inside / "l1" / "l2" / "l3"
    leaf.mkdir(parents=True)
    (leaf / "a.txt").write_bytes(b"inside\n")
    (leaf / "b.txt").write_bytes(b"b\n")
    return inside


def _descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


def _run(operation: str, inside: Path, at: int | None, name: str | None) -> tuple[str, list[str]]:
    """The outcome of ``operation`` with the call ``at`` failing with ``name``, and the steps."""
    confinement = Confinement.of(inside)
    leaf = inside / "l1" / "l2" / "l3" / "a.txt"
    src = Path(confinement.confine(inside / "l1"))
    confined = confinement.confine(leaf) if operation == "read" else None
    directory = confinement.confine(leaf.parent) if operation == "files" else None
    joined = ConfinedPath(src / "l2" / "l3" / "a.txt")
    injected = _Injected(at, name)
    before = _descriptors()
    try:
        with injected.installed():
            if operation == "confine":
                confinement.confine(leaf)
                got = "OK"
            elif operation == "identity-pack":
                confinement.identity(joined)
                got = "OK"
            elif operation in ("read", "read-pack"):
                path = confined if confined is not None else joined
                got = "OK:" + confinement.read(path, 100).decode()
            else:
                path = directory if directory is not None else ConfinedPath(joined.parent)
                kinds = ",".join(sorted(entry.kind for entry in confinement.files(path)))
                got = {"file,file": "OK:files", "file,unconfined": ONE_UNCONFINED}.get(
                    kinds, f"OK:{kinds}"
                )
    except ImportRefused as refused:
        got = "R:" + refused.refusals[0].message[0].model_dump()["text"]
    except BaseException as error:  # what came out, whatever it is, is the outcome
        raised = injected.raised
        same = type(error) is type(raised) and getattr(error, "errno", None) == getattr(
            raised, "errno", None
        )
        got = RAW if same else f"OTHER:{type(error).__name__}"
    assert _descriptors() == before, (operation, at, name)
    return got, injected.steps


@pytest.mark.parametrize("operation", OPERATIONS)
def test_every_call_failing_with_every_error_gives_the_recorded_outcome(
    tmp_path: Path, operation: str
) -> None:
    inside = _tree(tmp_path)
    got, steps = _run(operation, inside, None, None)
    assert got == HEAD_SUCCESS[operation]
    unplaced = sorted({step for step in steps if (operation, step) not in HEAD})
    assert unplaced == [], unplaced
    fired = set(steps)
    recorded = {step for (each, step) in HEAD if each == operation}
    assert fired == recorded - {step for (each, step) in GONE if each == operation}
    wrong: list[tuple[int, str, str, str, list[str]]] = []
    for at, step in enumerate(steps):
        for name in ERRORS:
            outcome, again = _run(operation, inside, at, name)
            assert again[: at + 1] == steps[: at + 1], (at, name)
            if outcome not in expected(operation, step, name):
                wrong.append((at, step, name, outcome, sorted(expected(operation, step, name))))
    assert wrong == [], wrong[:8]


def test_the_stated_rows_are_the_only_ones_that_differ_from_the_recording() -> None:
    """``STATED`` and ``GONE`` change rows ``HEAD`` has, and nothing else changes."""
    assert all((operation, step) in HEAD for operation, step, _ in STATED)
    assert set(HEAD) >= GONE
    changed = {
        (operation, step, name)
        for (operation, step), row in HEAD.items()
        for name in ERRORS
        if (operation, step) not in GONE and expected(operation, step, name) != row[name]
    }
    narrowed = {
        (operation, step, name)
        for operation, step, name in changed
        if operation == "files-pack" and (operation, step, name) not in STATED
    }
    assert changed - narrowed == set(STATED)
    assert all(expected(*row) == frozenset({MISSING}) for row in narrowed)
