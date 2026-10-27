"""A subdirectory a pack lists or reads through its guarded reader (D232, D400): the pack names it
by a path it joins below its confined source, and the reader confines that path in its turn. A
path that is a symlink (to a directory inside the roots, to ``.``, to ``..``, to a file, in a
loop or out of the roots), one that passes through a symlink in an earlier component (even one a
later ``..`` cancels), one through ``..`` out of the roots, and a directory swapped for a symlink
after its parent was listed are each the reader's refusal (``PATH_NOT_CONFINED``), never the pack's
failure (``PACK_FAILED``) from a ``KeyError`` of the reader's; a symlink inside the roots is
refused with a text that names it. A real subdirectory, and a ``..`` path with no symlink that
resolves inside the roots, list and read as before."""

import logging
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import aibi
from aibi.core.importers.checks import run_importer
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import ConfinedPath, ImportOptions, PackRegistry, Refused
from aibi.core.schema.refusals import Refusal

Roots = Any
Birds = Any
Lifecycle = Any
Change = Callable[[Path], None]

LISTED = "birds.LISTED"
OUTSIDE = "The path resolves outside every import root: "
MISSING = "No such file or directory: "
CHANGED = "The directory changed after it was confined: "
FILE_CHANGED = "The file changed after it was confined: "
SYMLINK = "The path passes through a symlink: "
NOT_REAL = "The path is not its own real path: "


class _Lister:
    """An importer that lists its source, lets ``between`` change it, then lists (or, with
    ``read``, reads) the path ``below`` joined under it, reads each file listed there, and
    stops with a refusal of its own when all of that passed."""

    def __init__(self, below: str, between: Change | None = None, *, read: bool = False) -> None:
        self.below, self.between, self.read = below, between, read
        self.top: dict[str, str] = {}
        self.found: list[tuple[str, str, bytes | None]] = []

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> Any:
        reader = options.reader
        self.top = {entry.name: entry.kind for entry in reader.files(source)}
        if self.between is not None:
            self.between(Path(source))
        path = ConfinedPath(Path(source) / self.below)
        if self.read:
            self.found = [("", "file", reader.read(path, 100))]
        else:
            for entry in reader.files(path):
                read = None if entry.path is None else reader.read(entry.path, 100)
                self.found.append((entry.name, entry.kind, read))
        raise Refused([Refusal(code=LISTED, path=None, message=[text("Listed")])])


def _library(roots: Roots) -> Path:
    """A library's export: ``library/`` with ``shelves/a.txt``; ``annex/b.txt`` beside it, inside
    the roots; and ``deep/secret.txt`` outside them."""
    roots.write("library/catalogue.txt", b"catalogue\n")
    roots.write("library/shelves/a.txt", b"shelf a\n")
    roots.write("annex/b.txt", b"annex\n")
    (roots.outside / "deep").mkdir()
    (roots.outside / "deep" / "secret.txt").write_bytes(b"secret\n")
    return roots.inside / "library"


def _relink(name: str, target: str | Path) -> Change:
    """``shelves`` (or ``name``) replaced by a symlink to ``target``, relative to the library."""

    def change(library: Path) -> None:
        path = library / name
        if path.is_dir() and not path.is_symlink():
            os.rename(path, library.parent / f"kept-{name}")
        path.symlink_to(target)

    return change


def _outside(library: Path) -> Path:
    return library.parent.parent / "elsewhere" / "deep"


SETUPS: dict[str, tuple[str, Change | None]] = {
    "real": ("shelves", None),
    "symlink_inside": ("shelves", _relink("shelves", "../annex")),
    "symlink_dot": ("shelves", _relink("shelves", ".")),
    "symlink_dot_dot": ("shelves", _relink("shelves", "..")),
    "symlink_to_a_file": ("shelves", _relink("shelves", "../annex/b.txt")),
    "symlink_loop": ("shelves", _relink("shelves", "shelves")),
    "symlink_outside": ("shelves", lambda library: _relink("shelves", _outside(library))(library)),
    "through_a_symlink_outside": (
        "out/deep",
        lambda library: (library / "out").symlink_to(_outside(library).parent),
    ),
    "dot_dot_outside": ("shelves/../../..", None),
    "dot_dot_inside": ("shelves/../../annex", None),
    "through_a_symlink_inside": ("via/annex", _relink("via", "..")),
    "through_a_symlink_to_itself": ("via/shelves", _relink("via", ".")),
    "a_symlink_a_dot_dot_cancels": ("via/../shelves", _relink("via", "shelves")),
    "a_dot_dot_out_of_a_symlink": ("via/../library/shelves", _relink("via", "../annex")),
    "a_symlink_after_a_dot_dot_a_dot_dot_cancels": (
        "shelves/../via/../shelves",
        _relink("via", "shelves"),
    ),
    "a_file_as_a_directory": ("catalogue.txt/../shelves", None),
}

EXPECTED: dict[str, tuple[str, str]] = {
    "real": (LISTED, "Listed"),
    "symlink_inside": ("PATH_NOT_CONFINED", SYMLINK),
    "symlink_dot": ("PATH_NOT_CONFINED", SYMLINK),
    "symlink_dot_dot": ("PATH_NOT_CONFINED", SYMLINK),
    "symlink_to_a_file": ("PATH_NOT_CONFINED", SYMLINK),
    "symlink_loop": ("PATH_NOT_CONFINED", MISSING),
    "symlink_outside": ("PATH_NOT_CONFINED", OUTSIDE),
    "through_a_symlink_outside": ("PATH_NOT_CONFINED", OUTSIDE),
    "dot_dot_outside": ("PATH_NOT_CONFINED", OUTSIDE),
    "dot_dot_inside": (LISTED, "Listed"),
    "through_a_symlink_inside": ("PATH_NOT_CONFINED", SYMLINK),
    "through_a_symlink_to_itself": ("PATH_NOT_CONFINED", SYMLINK),
    "a_symlink_a_dot_dot_cancels": ("PATH_NOT_CONFINED", SYMLINK),
    "a_dot_dot_out_of_a_symlink": ("PATH_NOT_CONFINED", SYMLINK),
    "a_symlink_after_a_dot_dot_a_dot_dot_cancels": ("PATH_NOT_CONFINED", SYMLINK),
    "a_file_as_a_directory": ("PATH_NOT_CONFINED", MISSING),
}


def _outcome(roots: Roots, importer: _Lister, source: Path) -> tuple[str, str]:
    """What the guarded reader's import of ``source`` by ``importer`` refuses with."""
    with pytest.raises(ImportRefused) as refused:
        run_importer(importer, "birds", roots.confinement.confine(source), roots.options())
    [refusal] = refused.value.refusals
    return refusal.code, refusal.message[0].model_dump()["text"]


@pytest.mark.parametrize("case", SETUPS)
def test_a_subdirectory_is_listed_or_refused_by_the_reader_never_failed(
    roots: Roots, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    library = _library(roots)
    below, setup = SETUPS[case]
    if setup is not None:
        setup(library)
    importer = _Lister(below)
    with caplog.at_level(logging.WARNING):
        assert _outcome(roots, importer, library) == EXPECTED[case]
    assert "KeyError" not in caplog.text
    if case == "real":
        assert importer.found == [("a.txt", "file", b"shelf a\n")]
    elif case == "dot_dot_inside":
        assert importer.found == [("b.txt", "file", b"annex\n")]
    else:
        assert importer.found == []


def test_a_symlink_to_a_file_is_not_read_through_the_reader(roots: Roots) -> None:
    library = _library(roots)
    _relink("shelves", "../annex/b.txt")(library)
    importer = _Lister("shelves", read=True)
    assert _outcome(roots, importer, library) == ("PATH_NOT_CONFINED", SYMLINK)
    assert importer.found == []


@pytest.mark.parametrize(
    ("below", "setup"),
    [
        ("via/b.txt", _relink("via", "../annex")),
        ("via/annex/b.txt", _relink("via", "..")),
        ("via/../catalogue.txt", _relink("via", "shelves")),
        ("shelves/via/../shelves/a.txt", _relink("shelves/via", ".")),
    ],
)
def test_a_file_read_through_a_symlinked_component_is_refused(
    roots: Roots, below: str, setup: Change
) -> None:
    """A symlink before the last component is not followed either: ``O_NOFOLLOW`` binds only
    the last, so the reader checks every component of the path it is given. The file the link
    leads to exists inside the roots, and is still not read."""
    library = _library(roots)
    setup(library)
    importer = _Lister(below, read=True)
    assert _outcome(roots, importer, library) == ("PATH_NOT_CONFINED", SYMLINK)
    assert importer.found == []


def test_a_path_whose_lexical_form_is_not_its_real_path_is_refused(roots: Roots) -> None:
    """The rule's other half: with no symlink in it, the path normalised lexically must still
    be its real path, which ``//`` (kept by a lexical normalisation, POSIX leaving its meaning
    to the system) is not."""
    library = _library(roots)
    importer = _Lister("/" + str(library / "shelves"))
    assert importer.below.startswith("//")
    assert _outcome(roots, importer, library) == ("PATH_NOT_CONFINED", NOT_REAL)
    assert importer.found == []


def test_a_file_swapped_between_its_confining_and_its_identity_is_refused(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity of a path the reader confines is the one ``confine`` recorded, never one
    taken again from the path afterwards: a file swapped for another just after it was
    confined is refused as changed, not read."""
    library = _library(roots)
    target = library / "shelves" / "a.txt"
    other = roots.write("other.txt", b"other\n")
    real = os.stat
    swapped: list[str] = []

    def swapping(path: Any, *rest: Any, **given: Any) -> os.stat_result:
        status = real(path, *rest, **given)
        if os.fspath(path) == str(target) and not given.get("follow_symlinks", True):
            os.replace(other, target)
            swapped.append(os.fspath(path))
        return status

    monkeypatch.setattr(os, "stat", swapping)
    importer = _Lister("shelves/a.txt", read=True)
    assert _outcome(roots, importer, library) == ("PATH_NOT_CONFINED", FILE_CHANGED)
    assert swapped == [str(target)]
    assert importer.found == []


def test_a_directory_swapped_for_a_symlink_after_the_listing_is_refused(roots: Roots) -> None:
    """The race of a directory swapped between its parent's listing and its own: the listing
    saw a directory, the path is a symlink when it is listed, and the listing does not follow
    it."""
    library = _library(roots)
    importer = _Lister("shelves", _relink("shelves", "../annex"))
    assert _outcome(roots, importer, library) == ("PATH_NOT_CONFINED", SYMLINK)
    assert importer.top == {"catalogue.txt": "file", "shelves": "directory"}
    assert importer.found == []


def test_a_directory_swapped_between_its_confining_and_its_opening_is_refused(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity the reader confines a path that is not yet confined under is checked against
    the directory it opens: ``annex``, reached through ``..``, swapped for another directory just
    before it is opened, is refused as changed."""
    library = _library(roots)
    annex = roots.inside / "annex"
    real = os.open
    swapped: list[str] = []

    def swapping(path: Any, flags: int, *rest: Any, **given: Any) -> int:
        if flags & os.O_DIRECTORY and os.fspath(path).endswith("/annex") and not swapped:
            os.rename(annex, roots.inside / "kept-annex")
            annex.mkdir()
            (annex / "c.txt").write_bytes(b"swapped\n")
            swapped.append(os.fspath(path))
        return real(path, flags, *rest, **given)

    monkeypatch.setattr(os, "open", swapping)
    importer = _Lister("shelves/../../annex")
    assert _outcome(roots, importer, library) == ("PATH_NOT_CONFINED", CHANGED)
    assert swapped == [str(library / "shelves/../../annex")]
    assert importer.found == []


def test_a_symlinked_subdirectory_refuses_the_import_as_the_reader_s(
    roots: Roots, birds: Birds, lifecycle: Lifecycle
) -> None:
    """What the operator sees of the whole import: the reader's refusal, not the pack's
    failure."""
    library = _library(roots)
    _relink("shelves", "../annex")(library)
    pack = replace(birds.pack, importer=_Lister("shelves"))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as refused:
        lifecycle.import_(library, registry=registry, pack="birds")
    [refusal] = refused.value.refusals
    assert (refusal.code, refusal.message[0].model_dump()["text"]) == (
        "PATH_NOT_CONFINED",
        SYMLINK,
    )
