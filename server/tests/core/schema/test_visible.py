"""The visible form's classes and their export (D312, D428): ``schemas/visible-form.json`` is what
``aibi.core.schema.visible`` writes, the server's page escapes exactly the code points the file
lists, and the O-2 record's numbers hold.

The classes come from the interpreter's Unicode data, so the file records the version that wrote
it: the comparisons run under that version alone, and say loudly which version wrote the file when
this interpreter's is another (m13 of the plan's round 1). The pinned Python (``.python-version``)
must be the one whose Unicode version the file records.
"""

import json
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from aibi.core.api import markup
from aibi.core.schema import export, visible

ROOT = Path(__file__).resolve().parents[4]
SCHEMA = ROOT / "schemas" / visible.VISIBLE_FILE
PINNED = (ROOT / "server" / ".python-version").read_text(encoding="utf-8").strip()
UNIDATA = {"3.12": "15.0.0", "3.13": "15.1.0", "3.14": "16.0.0"}
"""The Unicode version of each Python's ``unicodedata``."""


def _file() -> dict[str, Any]:
    found: dict[str, Any] = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return found


def _same_unicode() -> None:
    """Skip, loudly, under an interpreter whose Unicode data is not the file's."""
    written = _file()["unidata_version"]
    if unicodedata.unidata_version != written:
        pytest.skip(
            f"schemas/{visible.VISIBLE_FILE} was written under Unicode {written}; this "
            f"interpreter (Python {sys.version_info.major}.{sys.version_info.minor}) has Unicode "
            f"{unicodedata.unidata_version}: the comparison runs under the pinned Python "
            f"{PINNED} alone"
        )


def _flags(ranges: list[list[int]]) -> bytearray:
    flags = bytearray(visible.LAST + 1)
    for first, last in ranges:
        flags[first : last + 1] = b"\x01" * (last - first + 1)
    return flags


def test_the_file_was_written_under_the_pinned_python() -> None:
    assert _file()["unidata_version"] == UNIDATA[PINNED], (
        f"schemas/{visible.VISIBLE_FILE} was written under Unicode {_file()['unidata_version']}, "
        f"not under the pinned Python {PINNED}'s {UNIDATA[PINNED]}: regenerate it with that Python"
    )


def test_the_checked_in_file_is_current() -> None:
    _same_unicode()
    assert SCHEMA.read_text(encoding="utf-8") == export.render(visible.document()), (
        f"schemas/{visible.VISIBLE_FILE} is stale: run "
        "`uv run python -m aibi.core.schema.export ../schemas`, then `npm run generate` in web/"
    )


def test_the_export_command_writes_the_file(tmp_path: Path) -> None:
    export.write(tmp_path)
    assert (tmp_path / visible.VISIBLE_FILE).read_text(encoding="utf-8") == export.render(
        visible.document()
    )


def test_the_page_escapes_exactly_the_code_points_the_file_lists() -> None:
    """Over all 1,114,112 code points, ``markup``'s own runs (the decision the page writes by)
    against the file's list: a class the export lost or the page gained fails here."""
    _same_unicode()
    listed = _flags(_file()["unsafe"])
    step = 0x10000
    differ: list[int] = []
    for start in range(0, visible.LAST + 1, step):
        # One string of the plane (no carriage return is followed by a line feed in code point
        # order), its runs read back to a flag per code point.
        text = "".join(chr(point) for point in range(start, start + step))
        flags = bytearray()
        for unsafe, part in markup._runs(text):  # pyright: ignore[reportPrivateUsage]
            flags.extend((b"\x01" if unsafe else b"\x00") * len(part))
        markup._unsafe.cache_clear()  # pyright: ignore[reportPrivateUsage]
        differ.extend(start + i for i in range(step) if flags[i] != listed[start + i])
    assert differ == []
    assert sum(listed) == sum(last - first + 1 for first, last in _file()["unsafe"])


def test_the_page_writes_the_escapes_of_the_module() -> None:
    assert markup.escape is visible.escape
    assert markup._unsafe.__wrapped__ is visible.unsafe  # pyright: ignore[reportPrivateUsage, reportFunctionMemberAccess]
    assert markup.escaped("a\u202eb\r\nc\ud800\U000e0001") == "a\\u202eb\nc\\ud800\\U000e0001"


def test_the_kept_members_and_the_classes() -> None:
    assert {0x09, 0x0A, 0x200C, 0x200D} == visible.KEPT
    assert [visible.unsafe_point(point) for point in (0x09, 0x0A, 0x200C, 0x200D)] == [False] * 4
    for point in (0x00, 0x0D, 0x1F, 0x7F, 0x85, 0x9F, 0x2028, 0x2029, 0x202E, 0xD800, 0xFEFF):
        assert visible.unsafe_point(point), hex(point)
    for point in (0x20, 0x41, 0xA0, 0x034F, 0x3164, 0xFE0F, 0x2800, 0x10FFFF):
        assert not visible.unsafe_point(point), hex(point)
    assert [visible.escape(chr(point)) for point in (0x0D, 0xAD, 0x202E, 0xE0001)] == [
        "\\x0d",
        "\\xad",
        "\\u202e",
        "\\U000e0001",
    ]


def test_the_o2_record() -> None:
    """D428's O-2 record: what D312 leaves raw, by this Unicode's data."""
    _same_unicode()
    raw = _file()["raw_by_d312"]
    assert raw["default_ignorable_assigned_count"] == 267
    assert raw["default_ignorable_unassigned_count"] == 3769
    assert raw["space_separators_count"] == 16
    assert raw["braille_blank"] == 0x2800
    assigned = _flags(raw["default_ignorable_assigned"])
    categories = {
        unicodedata.category(chr(point)) for point in range(len(assigned)) if assigned[point]
    }
    assert categories == {"Mn", "Lo"}
    unsafe = _flags(_file()["unsafe"])
    for name in ("default_ignorable_assigned", "default_ignorable_unassigned", "space_separators"):
        listed = _flags(raw[name])
        assert not any(a and b for a, b in zip(listed, unsafe, strict=True)), name


def test_the_default_ignorable_table_is_of_its_categories() -> None:
    """``DEFAULT_IGNORABLE`` is written out from the UCD (``unicodedata`` has no such property):
    each of its code points is a format character, a mark, a letter or unassigned, and each format
    character of it is escaped but the two kept; the web's test holds the table to the engine's
    ``\\p{Default_Ignorable_Code_Point}``."""
    for first, last in visible.DEFAULT_IGNORABLE:
        for point in range(first, last + 1):
            category = unicodedata.category(chr(point))
            assert category in {"Cf", "Mn", "Lo", "Cn"}, hex(point)
            if category == "Cf":
                assert visible.unsafe_point(point) or point in visible.KEPT, hex(point)
