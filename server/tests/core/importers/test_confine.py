"""Confining what an import reads (SPEC §14, D232): every file resolves inside the upload area
or an import directory, and is read once, as it was when confined."""

import os
from pathlib import Path
from typing import Any

import pytest

from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused

Roots = Any


def _refusal(error: pytest.ExceptionInfo[ImportRefused]) -> tuple[str, str]:
    [refusal] = error.value.refusals
    return refusal.code, refusal.message[0].model_dump()["text"]


def test_a_file_inside_a_root_is_read(roots: Roots) -> None:
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    assert roots.confinement.read(confined, 100) == b"id\n1\n"
    assert roots.confinement.location(confined) == "a.csv"


def test_a_file_outside_every_root_is_refused(roots: Roots) -> None:
    outside = roots.outside / "b.csv"
    outside.write_bytes(b"id\n")
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine(outside)
    assert _refusal(refused) == (
        "PATH_NOT_CONFINED",
        "The path resolves outside every import root: ",
    )


def test_dot_dot_traversal_is_resolved_before_it_is_checked(roots: Roots) -> None:
    (roots.outside / "b.csv").write_bytes(b"id\n")
    with pytest.raises(ImportRefused):
        roots.confinement.confine(f"{roots.inside}/../elsewhere/b.csv")


def test_a_symlink_out_is_refused_and_one_within_is_read(roots: Roots) -> None:
    (roots.outside / "b.csv").write_bytes(b"secret\n")
    (roots.inside / "out.csv").symlink_to(roots.outside / "b.csv")
    with pytest.raises(ImportRefused):
        roots.confinement.confine(roots.inside / "out.csv")
    target = roots.write("real.csv", b"id\n1\n")
    (roots.inside / "within.csv").symlink_to(target)
    confined = roots.confinement.confine(roots.inside / "within.csv")
    assert confined == target.resolve()
    assert roots.confinement.read(confined, 100) == b"id\n1\n"


def test_a_root_that_is_itself_a_symlink_is_resolved(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.csv").write_bytes(b"id\n")
    (tmp_path / "link").symlink_to(real)
    confinement = Confinement.of(tmp_path / "link")
    assert confinement.roots == (real.resolve(),)
    assert confinement.confine(tmp_path / "link" / "a.csv") == (real / "a.csv").resolve()


@pytest.mark.parametrize(
    "given",
    ["http://example.org/a.csv", "file:///etc/passwd", "s3://bucket/a.csv", "data:,x"],
)
def test_urls_are_refused(roots: Roots, given: str) -> None:
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine(given)
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "A URL is not a path: ")


@pytest.mark.parametrize("pattern", ["*.csv", "a?.csv", "[ab].csv"])
def test_globs_are_refused(roots: Roots, pattern: str) -> None:
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine(f"{roots.inside}/{pattern}")
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "A glob is not a path: ")


def test_relative_and_missing_paths_are_refused(roots: Roots) -> None:
    with pytest.raises(ImportRefused) as relative:
        roots.confinement.confine("imports/a.csv")
    assert _refusal(relative)[1] == "The path is not absolute: "
    with pytest.raises(ImportRefused) as missing:
        roots.confinement.confine(roots.inside / "none.csv")
    assert _refusal(missing)[1] == "No such file or directory: "


def test_a_refusal_carries_the_path_as_data(roots: Roots) -> None:
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine("http://example.org/a.csv")
    [refusal] = refused.value.refusals
    assert refusal.message[1].model_dump() == {"data": "http://example.org/a.csv"}
    assert refusal.alternatives


def test_directory_entries_are_classified_by_their_own_names_before_anything_is_resolved(
    roots: Roots,
) -> None:
    roots.write("dir/a.csv", b"id\n")
    roots.write("dir/[odd] name.csv", b"id\n")
    roots.write("dir/sub/c.csv", b"id\n")
    (roots.outside / "b.csv").write_bytes(b"id\n")
    (roots.inside / "dir" / "z.csv").symlink_to(roots.outside / "b.csv")
    (roots.inside / "dir" / "y.csv").symlink_to(roots.inside / "dir" / "a.csv")
    os.mkfifo(roots.inside / "dir" / "pipe.csv")
    directory = roots.confinement.confine(roots.inside / "dir")
    entries = roots.confinement.files(directory)
    assert [(entry.name, entry.kind) for entry in entries] == [
        ("[odd] name.csv", "file"),
        ("a.csv", "file"),
        ("pipe.csv", "other"),
        ("sub", "directory"),
        ("y.csv", "symlink"),
        ("z.csv", "symlink"),
    ]
    assert entries[1].path == (roots.inside / "dir" / "a.csv").resolve()
    assert [entry.path for entry in entries[2:]] == [None] * 4


def test_a_directory_swapped_after_it_was_confined_is_not_listed(roots: Roots) -> None:
    roots.write("dir/a.csv", b"id\n")
    (roots.outside / "secret.csv").write_bytes(b"id\n")
    directory = roots.confinement.confine(roots.inside / "dir")
    os.rename(roots.inside / "dir", roots.inside / "was")
    (roots.inside / "dir").symlink_to(roots.outside)
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.files(directory)
    assert _refusal(refused) == (
        "PATH_NOT_CONFINED",
        "Not a directory that can be opened as confined: ",
    )
    (roots.inside / "dir").unlink()
    roots.write("dir/b.csv", b"id\n")
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.files(directory)
    assert _refusal(refused)[1] == "The directory changed after it was confined: "


def test_a_file_swapped_while_its_directory_is_listed_is_not_confined(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = roots.write("dir/a.csv", b"id\n1\n")
    os.link(listed, roots.inside / "kept.csv")  # so that its inode is not reused
    directory = roots.confinement.confine(roots.inside / "dir")
    real = os.lstat

    def swapping(path: str, **given: Any) -> os.stat_result:
        found = real(path, **given)
        if given:
            os.replace(roots.write("b.csv", b"id\n2\n"), listed)
        return found

    monkeypatch.setattr(os, "lstat", swapping)
    [entry] = roots.confinement.files(directory)
    assert (entry.name, entry.kind, entry.path) == ("a.csv", "unconfined", None)


def test_a_path_with_a_nul_character_is_refused(roots: Roots) -> None:
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.confine(f"{roots.inside}/a\0.csv")
    assert _refusal(refused) == ("PATH_NOT_CONFINED", "A path cannot hold a NUL character: ")


def test_a_name_with_glob_characters_that_exists_is_a_path(roots: Roots) -> None:
    for name in ("[draft] a.csv", "what?.csv", "all*.csv"):
        path = roots.write(name, b"id\n")
        assert roots.confinement.confine(str(path)) == path.resolve()


def test_a_file_that_grows_after_its_size_was_read_is_refused(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size ``fstat`` gives is no bound: the read stops one byte past the limit."""
    confined = roots.confinement.confine(roots.write("a.csv", b"x" * 101))
    real = os.fstat

    def shrunk(descriptor: int) -> os.stat_result:
        found = list(real(descriptor))
        found[6] = 50
        return os.stat_result(found)

    monkeypatch.setattr(os, "fstat", shrunk)
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert refused.value.refusals[0].code == "LIMIT_EXCEEDED"
    assert roots.confinement.read(confined, 101) == b"x" * 101


def test_a_file_swapped_after_it_was_confined_is_refused(roots: Roots) -> None:
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    replacement = roots.write("b.csv", b"id\n2\n")
    os.replace(replacement, path)
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    assert _refusal(refused)[1] == "The file changed after it was confined: "


def test_a_file_swapped_for_a_symlink_is_not_followed(roots: Roots) -> None:
    path = roots.write("a.csv", b"id\n1\n")
    confined = roots.confinement.confine(path)
    (roots.outside / "b.csv").write_bytes(b"secret\n")
    path.unlink()
    path.symlink_to(roots.outside / "b.csv")
    with pytest.raises(ImportRefused):
        roots.confinement.read(confined, 100)


def test_a_file_over_the_limit_is_refused(roots: Roots) -> None:
    confined = roots.confinement.confine(roots.write("a.csv", b"x" * 101))
    assert roots.confinement.read(confined, 101) == b"x" * 101
    with pytest.raises(ImportRefused) as refused:
        roots.confinement.read(confined, 100)
    [refusal] = refused.value.refusals
    assert refusal.code == "LIMIT_EXCEEDED"
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == (
        "import_bytes",
        100,
    )


def test_several_roots_are_each_allowed(roots: Roots, tmp_path: Path) -> None:
    (roots.outside / "b.csv").write_bytes(b"id\n")
    confinement = Confinement.of(roots.inside, roots.outside)
    confined = confinement.confine(roots.outside / "b.csv")
    assert confinement.location(confined) == "b.csv"
    with pytest.raises(ImportRefused):
        confinement.confine(tmp_path)
