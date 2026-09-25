"""Archives checked entry by entry, under size and ratio limits that headers cannot fool (SPEC
§14, D233)."""

import random
import struct
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from aibi.core.importers import archives, sheets
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits

MakeZip = Callable[..., bytes]
LIMITS = ImportLimits()


def _code(error: pytest.ExceptionInfo[ImportRefused]) -> tuple[str, str | None]:
    [refusal] = error.value.refusals
    return refusal.code, None if refusal.limit is None else refusal.limit.name


def test_members_are_read_in_the_archive_order(make_zip: MakeZip, entry: Any) -> None:
    data = make_zip([entry("b/one.csv", b"id\n1\n"), entry("dir/", b""), entry("a.csv", b"x\n")])
    assert archives.members(data, LIMITS) == [("b/one.csv", b"id\n1\n"), ("a.csv", b"x\n")]
    kept = archives.members(data, LIMITS, keep=lambda name: name == "a.csv")
    assert kept == [("b/one.csv", None), ("a.csv", b"x\n")]


def test_a_member_over_the_floor_within_the_ratio_passes(make_zip: MakeZip, entry: Any) -> None:
    varied = random.Random(0).randbytes(2 << 20)
    data = make_zip([entry("a.csv", varied), entry("b.csv", b"0" * (512 << 10))])
    assert [name for name, _ in archives.members(data, LIMITS)] == ["a.csv", "b.csv"]


def test_a_small_member_is_never_a_bomb_however_it_compresses(
    make_zip: MakeZip, entry: Any
) -> None:
    data = make_zip([entry("a.csv", b"0" * (1 << 20))])
    assert len(data) * 100 < 1 << 20
    assert archives.members(data, LIMITS) == [("a.csv", b"0" * (1 << 20))]


def test_every_limit_is_reached_without_being_passed(make_zip: MakeZip, entry: Any) -> None:
    content = bytes(range(256)) * 8
    data = make_zip([entry("a.csv", content), entry("b.csv", content)])
    exact = replace(LIMITS, archive_members=2, member_bytes=2048, archive_bytes=4096)
    assert len(archives.members(data, exact)) == 2
    stored = make_zip([entry("a.csv", bytes(range(256)) * 4097, stored=True)])
    assert len(archives.members(stored, replace(LIMITS, archive_ratio=1))) == 1


def test_a_member_that_decompresses_too_far_is_a_bomb(make_zip: MakeZip, entry: Any) -> None:
    data = make_zip([entry("a.csv", b"0" * (3 << 20))])
    with pytest.raises(ImportRefused) as refused:
        archives.members(data, LIMITS)
    assert _code(refused) == ("LIMIT_EXCEEDED", "archive_ratio")


def test_a_bomb_beside_a_large_member_is_measured_against_its_own_size(
    make_zip: MakeZip, entry: Any
) -> None:
    varied = random.Random(0).randbytes(4 << 20)
    data = make_zip([entry("a.csv", varied), entry("b.csv", b"0" * (3 << 20))])
    with pytest.raises(ImportRefused) as refused:
        archives.members(data, LIMITS)
    assert _code(refused) == ("LIMIT_EXCEEDED", "archive_ratio")


def test_the_total_ratio_is_limited_too(make_zip: MakeZip, entry: Any) -> None:
    small = [entry(f"{i}.csv", b"0" * (900 << 10)) for i in range(4)]
    data = make_zip(small)
    with pytest.raises(ImportRefused) as refused:
        archives.members(data, replace(LIMITS, archive_ratio=50))
    assert _code(refused) == ("LIMIT_EXCEEDED", "archive_ratio")


def test_too_many_members_are_refused(make_zip: MakeZip, entry: Any) -> None:
    data = make_zip([entry(f"{i}.csv", b"id\n") for i in range(4)])
    with pytest.raises(ImportRefused) as refused:
        archives.members(data, replace(LIMITS, archive_members=3))
    assert _code(refused) == ("LIMIT_EXCEEDED", "archive_members")


def test_members_too_large_alone_or_together_are_refused(make_zip: MakeZip, entry: Any) -> None:
    data = make_zip([entry("a.csv", bytes(range(256)) * 8), entry("b.csv", bytes(range(256)) * 8)])
    with pytest.raises(ImportRefused) as alone:
        archives.members(data, replace(LIMITS, member_bytes=2047))
    assert _code(alone) == ("LIMIT_EXCEEDED", "member_bytes")
    with pytest.raises(ImportRefused) as together:
        archives.members(data, replace(LIMITS, archive_bytes=3000))
    assert _code(together) == ("LIMIT_EXCEEDED", "archive_bytes")


def test_a_header_that_lies_about_its_size_is_caught_while_streaming(
    make_zip: MakeZip, entry: Any, declare_size: Callable[..., bytes]
) -> None:
    """A bomb declared small passes any check of the headers; decompressing it does not."""
    bomb = make_zip([entry("a.csv", b"0" * (8 << 20))])
    lying = declare_size(bomb, "a.csv", 1000)
    with pytest.raises(ImportRefused) as refused:
        archives.members(lying, LIMITS)
    assert _code(refused) == ("ARCHIVE_REFUSED", None)
    with pytest.raises(ImportRefused) as honest:
        archives.members(bomb, replace(LIMITS, archive_ratio=10_000, member_bytes=1 << 20))
    assert _code(honest) == ("LIMIT_EXCEEDED", "member_bytes")


@pytest.mark.parametrize(
    "name", ["/etc/a.csv", "C:/a.csv", "dir\\a.csv", "../a.csv", "dir/../../a.csv"]
)
def test_names_that_lead_out_are_refused(make_zip: MakeZip, entry: Any, name: str) -> None:
    with pytest.raises(ImportRefused) as refused:
        archives.members(make_zip([entry(name, b"id\n")]), LIMITS)
    assert _code(refused) == ("ARCHIVE_REFUSED", None)


def test_symlinks_encrypted_members_and_nested_archives_are_refused(
    make_zip: MakeZip, entry: Any
) -> None:
    for bad in (
        entry("link.csv", b"/etc/passwd", symlink=True),
        entry("a.csv", b"id\n", encrypted=True),
        entry("inner.zip", make_zip([entry("a.csv", b"id\n")])),
    ):
        with pytest.raises(ImportRefused) as refused:
            archives.members(make_zip([bad]), LIMITS)
        assert _code(refused) == ("ARCHIVE_REFUSED", None)


def test_repeated_names_are_refused(make_zip: MakeZip, entry: Any) -> None:
    with pytest.warns(UserWarning, match="Duplicate name"):
        data = make_zip([entry("a.csv", b"1\n"), entry("a.csv", b"2\n")])
    with pytest.raises(ImportRefused) as refused:
        archives.members(data, LIMITS)
    assert _code(refused) == ("ARCHIVE_REFUSED", None)


def test_bytes_that_are_no_archive_are_refused() -> None:
    with pytest.raises(ImportRefused) as refused:
        archives.members(b"not a zip", LIMITS)
    assert _code(refused) == ("ARCHIVE_REFUSED", None)


def test_an_archive_the_zip_module_trips_on_is_refused(make_zip: MakeZip, entry: Any) -> None:
    """A central directory one byte off makes the zip module seek before the file's start
    (``ValueError``), which no narrower list of its errors names."""
    data = bytearray(make_zip([entry("a.csv", b"id\n1\n"), entry("b.csv", b"id\n2\n")]))
    end = data.rfind(b"PK\x05\x06")
    offset = struct.unpack_from("<I", data, end + 16)[0]
    struct.pack_into("<I", data, end + 16, offset + 1)
    with pytest.raises(ImportRefused) as refused:
        archives.members(bytes(data), LIMITS)
    assert _code(refused) == ("ARCHIVE_REFUSED", None)
    with pytest.raises(ImportRefused) as container:
        archives.check_container(bytes(data), LIMITS)
    assert _code(container) == ("ARCHIVE_REFUSED", None)


@pytest.mark.parametrize("stored", [False, True])
def test_a_member_shorter_than_its_header_says_is_refused(
    make_zip: MakeZip, entry: Any, declare_size: Callable[..., bytes], stored: bool
) -> None:
    lying = declare_size(make_zip([entry("a.csv", b"id\n1\n", stored=stored)]), "a.csv", 10**6)
    with pytest.raises(ImportRefused) as refused:
        archives.members(lying, LIMITS)
    assert _code(refused) == ("ARCHIVE_REFUSED", None)


def test_a_workbook_bomb_is_refused_before_calamine_reads_it(
    make_xlsx: Callable[..., bytes], make_zip: MakeZip, entry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*_: object, **__: object) -> None:
        raise AssertionError("calamine was called")

    monkeypatch.setattr(sheets.CalamineWorkbook, "from_filelike", never)
    data = make_zip([entry("xl/worksheets/sheet1.xml", b" " * (4 << 20))])
    with pytest.raises(ImportRefused) as refused:
        sheets.read_workbook(data, LIMITS, LIMITS.import_cells)
    assert _code(refused) == ("LIMIT_EXCEEDED", "archive_ratio")
    assert make_xlsx([("S", [["a"]])])  # the builder itself is a valid container
    archives.check_container(make_xlsx([("S", [["a"]])]), LIMITS)
    archives.check_container(make_zip([entry("xl/embeddings/object.zip", b"PK")]), LIMITS)
