"""Opening a directory one name at a time, from ``/``, following no symlink (SPEC §14, D232).

``realpath`` and a later open of the same path text each resolve it afresh, and ``O_NOFOLLOW``
binds the last component alone, so a directory swapped for a symlink between the two is followed
by the open (issue #146). ``walk`` opens ``/``, then each component of a resolved path by its own
name relative to the descriptor of the one before it (``os.open(name, dir_fd=parent)``), every
one with ``O_NOFOLLOW`` and ``O_DIRECTORY``: a component that is a symlink, or no directory, when
its turn comes fails the walk, and nothing on the way is followed. The descriptor it returns is
the directory at that path, as each component was when it was opened, with no symlink in it.

The directories passed through are opened ``O_PATH`` where the system has it, so that one its user
may search but not read (mode ``0o111``) is passed through, as the kernel's own resolution passes
it; where it has not (macOS), ``O_RDONLY``, which needs the read permission too. ``O_PATH`` is never
used without ``O_DIRECTORY``: with ``O_NOFOLLOW`` alone it opens a symlink itself rather than fail.
The last component is opened with the flags its caller gives, ``O_DIRECTORY``, ``O_NOFOLLOW`` and
``O_CLOEXEC`` always added.

A walk holds two descriptors at most, closes each parent once its child is open, and closes what
it holds on every failure. It names nothing it met: an ``OSError`` of a step becomes ``Unwalked``,
which holds the ``errno`` alone (no file name, no step), raised from nothing, so that no
component's name reaches a message, a refusal or a log; a name the system cannot take (a NUL, a
lone surrogate, which ``Confinement.confine`` refuses before any walk) is a ``ValueError`` of fixed
text. A path that is not absolute, or that holds a ``..``, is not one ``realpath`` gives, and is a
``ValueError`` before anything is opened.
"""

import os
from pathlib import Path

_O_PATH = getattr(os, "O_PATH", os.O_RDONLY)
"""``O_PATH`` where the system has it (Linux); else ``O_RDONLY``, which needs read permission."""
_BOUND = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
"""What every open of the walk holds: a directory, never through a symlink, not inherited."""
SEARCH = _O_PATH | _BOUND
"""The flags of a directory the walk passes through."""
LIST = os.O_RDONLY | _BOUND
"""The flags of a directory to list (``scandir`` reads no ``O_PATH`` descriptor)."""

_UNOPENED = "a component of the path cannot be opened"
_UNNAMED = "a component of the path is not a name the system takes"
_UNRESOLVED = "the path to walk is not an absolute path without '..'"


class Unwalked(OSError):  # noqa: N818 - the walk's own word for its failure
    """A step of a walk failed with ``errno``; it says no more (neither the name nor the step)."""

    def __init__(self, code: int | None) -> None:
        super().__init__(code, _UNOPENED)


def _names(path: Path) -> tuple[str, ...]:
    if path.anchor != "/" or any(name in (os.curdir, os.pardir) for name in path.parts):
        raise ValueError(_UNRESOLVED)
    return path.parts[1:]


def _open(name: str, flags: int, parent: int | None) -> int:
    try:
        return os.open(name, flags) if parent is None else os.open(name, flags, dir_fd=parent)
    except OSError as error:
        raise Unwalked(error.errno) from None
    except ValueError:
        raise ValueError(_UNNAMED) from None


def walk(path: Path, last: int = SEARCH) -> int:
    """A descriptor of the directory at ``path`` (absolute and resolved: no ``..``), reached from
    ``/`` one component at a time, no symlink followed; its last component (``/`` itself for
    ``/``) opened with ``last``. The caller closes it."""
    names = _names(path)
    final = last | _BOUND
    descriptor = _open("/", SEARCH if names else final, None)
    for index, name in enumerate(names):
        try:
            child = _open(name, final if index == len(names) - 1 else SEARCH, descriptor)
        finally:
            os.close(descriptor)
        descriptor = child
    return descriptor


__all__ = ["LIST", "SEARCH", "Unwalked", "walk"]
