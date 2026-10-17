"""The visible form's classes and their export (D312, D428): ``schemas/visible-form.json`` is what
``aibi.core.schema.visible`` writes, the server's page escapes exactly the code points the file
lists, and the O-2 record's numbers hold.

The classes come from the interpreter's Unicode data, so the file records the version that wrote
it: the comparisons run under that version alone, and say loudly which version wrote the file when
this interpreter's is another (m13 of the plan's round 1). The pinned Python (``.python-version``)
must be the one whose Unicode version the file records.
"""

import ast
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from aibi.core.api import markup, page
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


def test_the_list_cut_is_held_equal_on_the_page_and_in_the_export() -> None:
    """D312's lists (domain tags, facet values) are cut after ``LISTED`` members on the server's
    page and in the web's ``List``: the page's name is bound to the export's, and the web's literal
    is the generator's, which refuses a ``listed`` other than 64 (D430: held equal).
    """
    assert visible.LISTED == 64
    assert visible.document()["listed"] == visible.LISTED
    assert _file()["listed"] == visible.LISTED
    assert page.LISTED == visible.LISTED


_FOLDS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.FloorDiv: lambda a, b: a // b if b else None,
    ast.Mod: lambda a, b: a % b if b else None,
    ast.Pow: lambda a, b: a**b if 0 <= b <= 64 and abs(a) <= 1024 else None,
    ast.LShift: lambda a, b: a << b if 0 <= b <= 64 else None,
    ast.RShift: lambda a, b: a >> b if 0 <= b <= 64 else None,
    ast.BitOr: lambda a, b: a | b,
    ast.BitAnd: lambda a, b: a & b,
    ast.BitXor: lambda a, b: a ^ b,
}
_UNARY = {ast.USub: lambda a: -a, ast.UAdd: lambda a: a, ast.Invert: lambda a: ~a}


def _fold(node: ast.AST) -> int | None:
    """The integer a constant expression of integer literals evaluates to (the operators of
    ``_FOLDS`` and ``_UNARY``), or None: ``2**6``, ``1 << 6`` and ``128 // 2`` are 64."""
    if isinstance(node, ast.Constant):
        return node.value if type(node.value) is int else None
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        inner = _fold(node.operand)
        return None if inner is None else _UNARY[type(node.op)](inner)
    if isinstance(node, ast.BinOp) and type(node.op) in _FOLDS:
        left, right = _fold(node.left), _fold(node.right)
        return None if left is None or right is None else _FOLDS[type(node.op)](left, right)
    return None


def _spelled(source: str, value: int) -> list[int]:
    """The lines of ``source`` where an expression evaluates to ``value`` by constants alone."""
    return sorted(
        {
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.expr) and _fold(node) == value
        }
    )


@pytest.mark.parametrize(
    "spelling",
    [
        "64",
        "2**6",
        "1 << 6",
        "128 // 2",
        "0b1000000",
        "0x40",
        "-(-64)",
        "60 + 4",
        "8 * 8",
        "~(-65)",
    ],
)
def test_a_cut_of_64_is_seen_by_value_however_it_is_spelled(spelling: str) -> None:
    assert _spelled(f"x = values[:{spelling}]\n", 64) == [1]
    assert _spelled("x = values[:LISTED]\n", 64) == []
    assert _spelled("x = values[:65]\n", 64) == []


@pytest.mark.parametrize("spelling", ["(1 << 65) >> 59", "2**100 // 2**94"])
def test_a_64_that_needs_an_exponent_or_a_shift_count_above_64_is_not_seen(spelling: str) -> None:
    """The residual D430 names: ``_FOLDS`` folds ``**``, ``<<`` and ``>>`` only up to 64."""
    assert _spelled(f"x = values[:{spelling}]\n", 64) == []


def test_the_page_binds_its_list_cut_to_the_export_and_writes_no_64_of_its_own() -> None:
    """``page.LISTED`` is assigned ``visible.LISTED`` (the name bound to the export), and no
    expression of ``page.py`` evaluates to 64 by integer constants under ``_FOLDS`` and ``_UNARY``
    (``64``, ``2**6``, ``1 << 6``: an ``is`` between two 64s proves nothing, CPython caches small
    integers). What this does not hold: a 64 reached through a name or a call, a subscript, a
    conditional or a boolean (``(32, 32)[0] * 2``, ``True << 6``), or one that needs an exponent or
    a shift count above 64, which ``_FOLDS`` does not fold (``(1 << 65) >> 59``,
    ``2**100 // 2**94``); the page's cut is 64 by ``test_page``. The web's 64 is held equal by the
    generator."""
    source = Path(page.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "LISTED" for t in node.targets)
    ]
    assert len(assigned) == 1
    assert ast.dump(assigned[0]) == ast.dump(
        ast.Attribute(value=ast.Name(id="visible", ctx=ast.Load()), attr="LISTED", ctx=ast.Load())
    )
    assert _spelled(source, visible.LISTED) == []


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
