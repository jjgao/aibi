"""The forms a path is written in (SPEC §14, D232, issue #146): what confinement does with a path
written with a trailing separator, a ``.`` or a ``..`` component, held against what the module did
before the walk.

``HEAD`` is the table recorded from the module as it was before it walked (main at 279fafb), for
a file (``a.csv``) and a directory (``d``) in a source directory, each written with nine suffixes,
in the operator's mode (``confine`` of the text, then the operation on what it returns) and as the
text given to the operation itself (``given``: a ``str`` handed to ``Confinement`` directly, since a
``Path`` holds none of the trailing forms and the pack's reader takes nothing but a ``Path``, so no
pack can reach them), for ``identity``, ``read`` and ``files``. The given text was recorded in each
state of the confinement (fresh, confined, and listed), and each row held in all three. What
differs now is stated in ``STATED``, and nowhere else: ``src/file/..`` is missing to every
operation, as ``src/file/x`` is, where it had the identity of ``src`` and was refused by the read
and the listing with their own texts (D232). A file written with a trailing separator or ``.`` is
still refused by the read ("cannot be opened as confined", as the open of ``a.csv/`` was), though
the walk opens it by its own name: the form is judged on the descriptor the read opens."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.pack_api import ConfinedPath

OK = "ok:identity"
MISSING = "R:No such file or directory: "
CANNOT = "R:The file cannot be opened as confined: "
NOT_LISTABLE = "R:Not a directory that can be opened as confined: "
NOT_REGULAR = "R:Not a regular file: "
D_LIST = "ok:x:file,y.csv:file"
SRC_LIST = "ok:a.csv:file,d:directory,x:file"

Row = tuple[str, str, str]
HEAD: dict[Row, tuple[str, str, str]] = {
    ("operator", "directory", ""): (OK, NOT_REGULAR, D_LIST),
    ("operator", "directory", "/"): (OK, NOT_REGULAR, D_LIST),
    ("operator", "directory", "//"): (OK, NOT_REGULAR, D_LIST),
    ("operator", "directory", "/."): (OK, NOT_REGULAR, D_LIST),
    ("operator", "directory", "/./."): (OK, NOT_REGULAR, D_LIST),
    ("operator", "directory", "/.//"): (OK, NOT_REGULAR, D_LIST),
    ("operator", "directory", "/.."): (OK, NOT_REGULAR, SRC_LIST),
    ("operator", "directory", "/./x"): (OK, "ok:dx", NOT_LISTABLE),
    ("operator", "directory", "/../x"): (OK, "ok:srcx", NOT_LISTABLE),
    ("operator", "file", ""): (OK, "ok:A", NOT_LISTABLE),
    ("operator", "file", "/"): (OK, "ok:A", NOT_LISTABLE),
    ("operator", "file", "//"): (OK, "ok:A", NOT_LISTABLE),
    ("operator", "file", "/."): (OK, "ok:A", NOT_LISTABLE),
    ("operator", "file", "/./."): (OK, "ok:A", NOT_LISTABLE),
    ("operator", "file", "/.//"): (OK, "ok:A", NOT_LISTABLE),
    ("operator", "file", "/.."): (OK, NOT_REGULAR, SRC_LIST),
    ("operator", "file", "/./x"): (MISSING, MISSING, MISSING),
    ("operator", "file", "/../x"): (OK, "ok:srcx", NOT_LISTABLE),
    ("given", "directory", ""): (OK, NOT_REGULAR, D_LIST),
    ("given", "directory", "/"): (OK, NOT_REGULAR, D_LIST),
    ("given", "directory", "//"): (OK, NOT_REGULAR, D_LIST),
    ("given", "directory", "/."): (OK, NOT_REGULAR, D_LIST),
    ("given", "directory", "/./."): (OK, NOT_REGULAR, D_LIST),
    ("given", "directory", "/.//"): (OK, NOT_REGULAR, D_LIST),
    ("given", "directory", "/.."): (OK, NOT_REGULAR, SRC_LIST),
    ("given", "directory", "/./x"): (OK, "ok:dx", NOT_LISTABLE),
    ("given", "directory", "/../x"): (OK, "ok:srcx", NOT_LISTABLE),
    ("given", "file", ""): (OK, "ok:A", NOT_LISTABLE),
    ("given", "file", "/"): (OK, CANNOT, NOT_LISTABLE),
    ("given", "file", "//"): (OK, CANNOT, NOT_LISTABLE),
    ("given", "file", "/."): (OK, CANNOT, NOT_LISTABLE),
    ("given", "file", "/./."): (OK, CANNOT, NOT_LISTABLE),
    ("given", "file", "/.//"): (OK, CANNOT, NOT_LISTABLE),
    ("given", "file", "/.."): (OK, CANNOT, NOT_LISTABLE),
    ("given", "file", "/./x"): (MISSING, MISSING, MISSING),
    ("given", "file", "/../x"): (MISSING, MISSING, MISSING),
}
STATED: dict[Row, tuple[str, str, str]] = {
    ("given", "file", "/.."): (MISSING, MISSING, MISSING),
}
"""A file's ``..`` is missing, as the file's ``x`` is (D232)."""
STATES = ("fresh", "confined", "listed")
NAMES = {"file": "a.csv", "directory": "d"}


def _outcome(call: Callable[[], Any]) -> str:
    try:
        value = call()
    except ImportRefused as error:
        [refusal] = error.refusals
        return "R:" + refusal.message[0].model_dump()["text"]
    if isinstance(value, bytes):
        return "ok:" + value.decode().strip()
    if isinstance(value, list):
        return "ok:" + ",".join(f"{entry.name}:{entry.kind}" for entry in value)
    return OK


def _tree(base: Path) -> Path:
    src = base / "root" / "src"
    (src / "d").mkdir(parents=True)
    (src / "a.csv").write_bytes(b"A\n")
    (src / "x").write_bytes(b"srcx\n")
    (src / "d" / "x").write_bytes(b"dx\n")
    (src / "d" / "y.csv").write_bytes(b"Y\n")
    return src


def _do(confinement: Confinement, mode: str, operation: str, written: str) -> str:
    """The outcome of ``operation`` on the path ``written``: given to it as it is, or
    confined first (the operator's)."""

    def run() -> Any:
        path = confinement.confine(written) if mode == "operator" else cast(ConfinedPath, written)
        if operation == "identity":
            return confinement.identity(path)
        if operation == "read":
            return confinement.read(path, 100)
        return confinement.files(path)

    return _outcome(run)


@pytest.mark.parametrize("mode", ["operator", "given"])
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_a_path_in_every_form_is_what_it_was_before_the_walk_but_for_what_is_stated(
    tmp_path: Path, mode: str, kind: str
) -> None:
    src = _tree(tmp_path.resolve())
    real = src / NAMES[kind]
    suffixes = sorted(suffix for (m, k, suffix) in HEAD if (m, k) == (mode, kind))
    assert len(suffixes) == 9
    wrong: list[tuple[Any, ...]] = []
    for suffix in suffixes:
        expected = STATED.get((mode, kind, suffix), HEAD[(mode, kind, suffix)])
        for state in STATES if mode == "given" else ("fresh",):
            found: list[str] = []
            for operation in ("identity", "read", "files"):
                confinement = Confinement.of(src.parent)
                if state == "confined":
                    confinement.confine(real)
                elif state == "listed":
                    confinement.files(confinement.confine(src))
                found.append(_do(confinement, mode, operation, f"{real}{suffix}"))
            if tuple(found) != expected:
                wrong.append((suffix, state, found, expected))
    assert wrong == []


def test_every_stated_difference_is_a_difference_and_a_row_of_the_table() -> None:
    assert STATED
    for row, outcomes in STATED.items():
        assert row in HEAD
        assert outcomes != HEAD[row]


def test_a_file_written_with_a_trailing_separator_is_refused_by_the_read_not_the_identity(
    tmp_path: Path,
) -> None:
    """The rows that the walk would have loosened, held on their own: ``a.csv/``, ``a.csv/.`` and
    ``a.csv//`` have the file's identity and are refused by the read ("cannot be opened as
    confined"), fresh or confined, as the open of ``a.csv/`` was; the file itself is read."""
    src = _tree(tmp_path.resolve())
    file = src / "a.csv"
    for suffix in ("/", "/.", "//", "/./", "/./."):
        for confined_first in (False, True):
            confinement = Confinement.of(src.parent)
            if confined_first:
                confinement.confine(file)
            written = f"{file}{suffix}"
            assert _do(confinement, "given", "identity", written) == OK
            assert _do(confinement, "given", "read", written) == CANNOT
    assert _do(Confinement.of(src.parent), "given", "read", str(file)) == "ok:A"
