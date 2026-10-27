"""Every component of a path the reader is given (SPEC §14, D232), by brute force and by
property.

A brute force over each component's kind (a directory, a file, a FIFO, a symlink to a directory
or a file inside the roots, to a directory outside them, to itself, or to nothing) and ``..``, at
each position of a path of up to three components below the source, through ``files`` and
``read`` of the guarded reader (a pack's path, joined below its source) and through the
operator's ``confine`` then ``read`` or ``files``. A pack's path is read or listed exactly when no
component of it is a symlink, every component before the last is a directory, and it stays inside
the roots, and then what is read is the file the path names lexically; the operator's path is
resolved, and what is read is the file at the real path ``confine`` returned, inside the roots.
Everything else is a ``PATH_NOT_CONFINED`` refusal, never another exception.

A property over generated trees and chains of symlinks (39, 40, 41 and 2,000 links, which
Python's recursive ``realpath`` cannot follow): no exception but ``ImportRefused`` comes out of
``confine``, ``files`` or ``read``."""

import itertools
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.importers.checks import run_importer
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import ConfinedPath, ImportOptions, Refused, SourceReader
from aibi.core.schema.refusals import Refusal

AT = "2026-01-01T00:00:00Z"
DONE = "stub.DONE"
SYMLINK = "The path passes through a symlink: "
LINKS = ("to_dir", "to_file", "to_outside", "loop", "dangling")
KINDS = ("dir", "file", "fifo", *LINKS)
DEPTH = 3
Op = Literal["files", "read"]


def _build(directory: Path, depth: int, mirror: Path) -> None:
    """``directory`` holding one entry of each kind, its ``dir`` holding the same down to
    ``depth``; a file holds its own path, a link to a directory or a file leads into ``mirror``,
    which has the same shape."""
    directory.mkdir()
    (directory / "file").write_bytes(os.fsencode(directory / "file"))
    os.mkfifo(directory / "fifo")
    (directory / "to_dir").symlink_to(mirror)
    (directory / "to_file").symlink_to(mirror / "file")
    (directory / "loop").symlink_to("loop")
    (directory / "dangling").symlink_to("nowhere")
    if depth:
        _build(directory / "dir", depth - 1, mirror)
    else:
        (directory / "dir").mkdir()


def _tree(base: Path) -> tuple[Path, Path, Path]:
    """The root (holding ``src`` and its ``mirror``), the source and the outside, whose own
    ``mirror`` has the same shape and whose files hold their outside paths."""
    base = Path(os.path.realpath(base))
    inside, outside = base / "imports", base / "elsewhere"
    inside.mkdir()
    outside.mkdir()
    _build(outside / "mirror", DEPTH, outside / "mirror")
    _build(inside / "mirror", DEPTH, inside / "mirror")
    _build(inside / "src", DEPTH, inside / "mirror")
    for directory, _, _ in itertools.chain(os.walk(inside / "src"), os.walk(inside / "mirror")):
        Path(directory, "to_outside").symlink_to(outside / "mirror")
    return inside, inside / "src", outside


def _lexical(parts: tuple[str, ...], op: Op) -> list[str] | None:
    """The components below the root that ``src`` joined with ``parts`` names lexically, when
    it is the kind ``op`` takes, inside the root, with no symlink in it and every component
    before the last a directory; otherwise ``None``."""
    names, kinds = ["src"], ["dir", "dir"]  # the kinds of the root and of each name
    for part in parts:
        if kinds[-1] != "dir":
            return None
        if part == "..":
            if not names:
                return None
            names.pop()
            kinds.pop()
        elif not names or part in LINKS:
            return None
        else:
            names.append(part)
            kinds.append(part)
    return names if kinds[-1] == ("dir" if op == "files" else "file") else None


def _redirected(parts: tuple[str, ...], op: Op) -> bool:
    """Whether the path, with no ``..``, names a node of the kind ``op`` takes inside the roots
    once its symlinks are followed, though not lexically: the case whose refusal names the
    symlink."""
    if ".." in parts or _lexical(parts, op) is not None:
        return False
    kinds = ["dir", *({"to_dir": "dir", "to_file": "file"}.get(part, part) for part in parts)]
    if any(kind != "dir" for kind in kinds[:-1]) or any(k in parts for k in LINKS[2:]):
        return False
    return kinds[-1] == ("dir" if op == "files" else "file")


def _linked_after_a_dot_dot(src: Path, parts: tuple[str, ...], op: Op) -> bool:
    """Whether the path, with a ``..``, is refused for its first failing step being a symlink
    and not another failure of the walk, the path leading, once resolved, to a node of the kind
    ``op`` takes inside the roots (else ``confine`` refuses it first): the case whose refusal
    names the symlink, though a ``..`` may come before the link or after it."""
    if ".." not in parts or _lexical(parts, op) is not None:
        return False
    kinds = ["dir", "dir"]
    for part in parts:
        if kinds[-1] != "dir":
            return False
        if part == "..":
            if len(kinds) < 2:
                return False
            kinds.pop()
        elif part in LINKS:
            break
        else:
            kinds.append(part)
    else:
        return False
    if part in LINKS[2:]:
        return False
    try:
        real = Path(os.path.realpath(src.joinpath(*parts), strict=True))
    except OSError:
        return False
    mode = real.stat().st_mode
    return (stat.S_ISDIR if op == "files" else stat.S_ISREG)(mode) and real.is_relative_to(
        src.parent
    )


class _Stub:
    """A pack's importer: lists its source, as every importer does, then lists or reads the
    path ``below`` joined under it, reading each file listed."""

    def __init__(self, op: Op, below: tuple[str, ...]) -> None:
        self.op: Op = op
        self.below = below
        self.got: Any = None

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> Any:
        options.reader.files(source)
        self.got = _call(options.reader, self.op, ConfinedPath(Path(source).joinpath(*self.below)))
        raise Refused([Refusal(code=DONE, path=None, message=[text("done")])])


def _options(confinement: Confinement) -> ImportOptions:
    return ImportOptions(dataset="d", reader=confinement, limits=ImportLimits(), at=AT)


def _call(reader: SourceReader, op: Op, path: ConfinedPath) -> Any:
    """What ``read`` or ``files`` of ``path`` gives, each file listed read in its turn."""
    if op == "read":
        return reader.read(path, 1 << 16)
    return [
        (e.name, e.kind, e.path, None if e.path is None else reader.read(e.path, 1 << 16))
        for e in reader.files(path)
    ]


def _listed(directory: Path) -> list[tuple[str, str]]:
    """What a listing of ``directory`` holds, by name and kind, as the file system says."""

    def kind(name: str) -> str:
        mode = os.lstat(directory / name).st_mode
        if stat.S_ISLNK(mode):
            return "symlink"
        return "directory" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"

    return [(name, kind(name)) for name in sorted(os.listdir(directory))]


def _accepted(got: Any, op: Op, named: Path) -> None:
    """``got`` is exactly what is at ``named``, a real path: its bytes, or its listing with each
    file confined at its own real path and read as itself."""
    if op == "read":
        assert got == os.fsencode(named)
        return
    assert [(name, kind) for name, kind, _, _ in got] == _listed(named)
    for name, _, path, read in got:
        if path is not None:
            assert (Path(path), read) == (named / name, os.fsencode(named / name))
        else:
            assert read is None


PATHS = [
    parts for n in range(1, DEPTH + 1) for parts in itertools.product((*KINDS, ".."), repeat=n)
] + [
    # four components, a symlink after an earlier ``..``
    ("dir", "..", "to_dir", "file"),
    ("dir", "..", "to_dir", ".."),
    ("dir", "..", "to_dir", "dir"),
]


@pytest.mark.parametrize("op", ["files", "read"])
def test_a_pack_s_path_is_read_exactly_when_no_component_is_a_symlink(
    tmp_path: Path, op: Op
) -> None:
    inside, src, _ = _tree(tmp_path)
    tally = {"accepted": 0, "redirected": 0, "dot_dot": 0}
    for parts in PATHS:
        confinement = Confinement.of(inside)
        stub = _Stub(op, parts)
        with pytest.raises(ImportRefused) as refused:
            run_importer(stub, "stub", confinement.confine(src), _options(confinement))
        [refusal] = refused.value.refusals
        said = (refusal.code, refusal.message[0].model_dump()["text"])
        names = _lexical(parts, op)
        if names is None:
            assert refusal.code == "PATH_NOT_CONFINED", (parts, said)
            assert stub.got is None, parts
            if _redirected(parts, op):
                assert said[1] == SYMLINK, (parts, said)
                tally["redirected"] += 1
            elif _linked_after_a_dot_dot(src, parts, op):
                assert said[1] == SYMLINK, (parts, said)
                tally["dot_dot"] += 1
        else:
            assert refusal.code == DONE, (parts, said)
            named = inside.joinpath(*names)
            assert Path(os.path.normpath(src.joinpath(*parts))) == named
            _accepted(stub.got, op, named)
            tally["accepted"] += 1
    # read: file, dir/file, dir/dir/file, dir/../file; files: eight such directories.
    # and, with a ``..``, a symlink that names a node of the kind: 15 of directories, 4 of files.
    expected = {"accepted": 8 if op == "files" else 4, "redirected": 11}
    assert tally == {**expected, "dot_dot": 15 if op == "files" else 4}, tally


@pytest.mark.parametrize("op", ["files", "read"])
def test_the_operator_s_path_reads_the_real_file_it_resolves_to(tmp_path: Path, op: Op) -> None:
    """The operator's path is resolved (D232: inside a root after ``realpath``), and what is
    then read or listed is the file at the real path ``confine`` returns, which is inside the
    roots and named lexically by that path."""
    inside, src, _ = _tree(tmp_path)
    accepted = 0
    for parts in PATHS:
        confinement = Confinement.of(inside)
        given_path = src.joinpath(*parts)
        try:
            confined = Path(confinement.confine(given_path))
            got = _call(confinement, op, ConfinedPath(confined))
        except ImportRefused as refused:
            codes = [r.code for r in refused.refusals]
            assert codes == ["PATH_NOT_CONFINED"], parts
            continue
        assert confined == Path(os.path.realpath(given_path)), parts
        assert confined.is_relative_to(inside), parts
        _accepted(got, op, confined)
        accepted += 1
    assert accepted >= 20, accepted


def _chain(directory: Path, length: int) -> None:
    """``l0`` -> ``l1`` -> ... -> ``end``, a directory holding a file."""
    (directory / "end").mkdir()
    (directory / "end" / "a.txt").write_bytes(b"a\n")
    for i in range(length):
        (directory / f"l{i}").symlink_to(f"l{i + 1}" if i + 1 < length else "end")


class _Chained:
    """A pack's importer that lists and reads paths through the chain."""

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> Any:
        options.reader.files(source)
        codes: list[str] = []
        for below in (("l0",), ("l0", "..", "end"), ("end", "..", "l0", "a.txt")):
            path = ConfinedPath(Path(source).joinpath(*below))
            for call in (options.reader.files, lambda p: options.reader.read(p, 99)):
                try:
                    call(path)
                except ImportRefused as refused:
                    codes.extend(r.code for r in refused.refusals)
                else:
                    raise AssertionError(below)
        assert codes == ["PATH_NOT_CONFINED"] * 6
        raise Refused([Refusal(code=DONE, path=None, message=[text("done")])])


@pytest.mark.parametrize("length", [39, 40, 41, 2000])
def test_a_chain_of_symlinks_is_a_refusal_never_a_failure(tmp_path: Path, length: int) -> None:
    """A chain longer than Python's recursion allows ``realpath`` to follow is refused as one
    that cannot be resolved, through the guarded reader and from the operator's ``confine``;
    a chain it can follow is resolved for the operator and refused for a pack, as a symlink."""
    inside = Path(os.path.realpath(tmp_path)) / "imports"
    (inside / "lib").mkdir(parents=True)
    _chain(inside / "lib", length)
    confinement = Confinement.of(inside)
    source = confinement.confine(inside / "lib")
    with pytest.raises(ImportRefused) as refused:
        run_importer(_Chained(), "stub", source, _options(confinement))
    assert [r.code for r in refused.value.refusals] == [DONE]
    if length > 1000:
        with pytest.raises(ImportRefused) as refused:
            confinement.confine(inside / "lib" / "l0")
        assert refused.value.refusals[0].code == "PATH_NOT_CONFINED"
    else:
        assert confinement.confine(inside / "lib" / "l0") == inside / "lib" / "end"


Node = tuple[str, str]
TARGETS = (".", "..", "../..", "a", "b", "c", "nowhere", "@outside", "@outside/a", "l0")
NAMES = ("a", "b", "c", "..", "l0", "end")


@st.composite
def _trees(draw: st.DrawFn) -> tuple[list[Node], int, list[tuple[str, ...]]]:
    """Entries below the source (a path and its kind, or a symlink's target), a chain's length
    and the paths to try."""
    entries: list[Node] = []

    def fill(prefix: str, depth: int) -> None:
        for name in ("a", "b", "c"):
            kinds = ["dir", "file", "fifo", "link", "none"] if depth < 2 else ["file", "link"]
            kind = draw(st.sampled_from(kinds))
            path = f"{prefix}{name}"
            if kind == "link":
                entries.append((path, "->" + draw(st.sampled_from(TARGETS))))
            elif kind != "none":
                entries.append((path, kind))
            if kind == "dir":
                fill(path + "/", depth + 1)

    fill("", 0)
    length = draw(st.sampled_from([39, 40, 41, 2000]))
    paths = draw(st.lists(st.tuples(*[st.sampled_from(NAMES)] * 3), min_size=1, max_size=8))
    return entries, length, [p[: draw(st.integers(1, 3))] for p in paths]


@settings(max_examples=40, deadline=None)
@given(_trees())
def test_nothing_but_a_refusal_comes_out_of_confine_files_or_read(
    tree: tuple[list[Node], int, list[tuple[str, ...]]],
) -> None:
    entries, length, paths = tree
    with tempfile.TemporaryDirectory() as directory:
        base = Path(os.path.realpath(directory))
        inside, outside = base / "imports", base / "elsewhere"
        src = inside / "src"
        src.mkdir(parents=True)
        (outside / "a").mkdir(parents=True)
        (outside / "a" / "s.txt").write_bytes(b"outside\n")
        _chain(src, length)
        for path, kind in entries:
            target = src / path
            if kind == "dir":
                target.mkdir()
            elif kind == "file":
                target.write_bytes(path.encode())
            elif kind == "fifo":
                os.mkfifo(target)
            else:
                target.symlink_to(kind[2:].replace("@outside", str(outside)))
        for parts in paths:
            path = ConfinedPath(src.joinpath(*parts))
            for op in ("confine", "files", "read"):
                confinement = Confinement.of(inside)
                confinement.confine(src)
                try:
                    if op == "confine":
                        assert Path(confinement.confine(path)).is_relative_to(inside), parts
                    elif op == "read":
                        got = confinement.read(path, 1 << 16)
                        assert got == os.fsencode(os.path.relpath(path, src)), parts
                    else:
                        for entry in confinement.files(path):
                            assert entry.path is None or Path(entry.path).is_relative_to(src)
                except ImportRefused:
                    pass
