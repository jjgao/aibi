"""The core's file importer (SPEC §13.1, D225–D227): every format, directories and archives,
sheets and their edge cases, Parquet types, and the names it gives."""

import csv
import dataclasses
import io
import os
import random
import string
import struct
import zipfile
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aibi.core.importers import detect as detect_module
from aibi.core.importers import sheets, worker
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.files import FileImporter
from aibi.core.importers.sheets import duration
from aibi.core.schema.descriptors import ColumnDescriptor, TableDescriptor
from aibi.core.schema.limits import MAX_STRING, ImportLimits
from aibi.core.schema.pack_api import ImportResult
from aibi.core.store import parquet, sources
from aibi.core.store.sources import TextSource, TypedSource

Roots = Any
Make = Callable[..., bytes]


def _import(roots: Roots, path: Path, **options: Any) -> ImportResult:
    return FileImporter().import_source(roots.confinement.confine(path), roots.options(**options))


def _tables(result: ImportResult) -> dict[str, TableDescriptor]:
    return {d.id: d for d in result.descriptors if isinstance(d, TableDescriptor)}


def _datatypes(result: ImportResult, table: str) -> dict[str, str | None]:
    return {
        d.id.split(".", 1)[1]: d.fields.datatype
        for d in result.descriptors
        if isinstance(d, ColumnDescriptor) and d.id.startswith(table + ".")
    }


def _notes(result: ImportResult, kind: str) -> list[tuple[str | None, str]]:
    return [
        (
            note.subject,
            "".join(
                str(s.model_dump().get("text", s.model_dump().get("data"))) for s in note.message
            ),
        )
        for note in result.notes
        if note.kind == kind
    ]


def _parquet(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def test_a_csv_file_is_text_with_detected_settings(roots: Roots) -> None:
    result = _import(roots, roots.write("Members List.csv", b"Member ID,Age\nm1,30\nm2,41\n"))
    [table] = _tables(result).values()
    assert table.id == "members_list"
    assert table.label == "Members List"
    assert table.fields.source is not None
    assert table.fields.source.model_dump(mode="json", exclude={"parse"}) == {
        "kind": "file",
        "name": "members_list",
        "original_name": "Members List.csv",
    }
    source = table.curation["/fields/source"]
    assert source.status == "imported_default"
    assert source.evidence is not None
    assert source.evidence.startswith("Detected (D224)")
    assert result.sources == {"members_list": TextSource(b"Member ID,Age\nm1,30\nm2,41\n")}
    assert result.layouts["members_list"].columns == (("member_id", "Member ID"), ("age", "Age"))
    assert _notes(result, "renamed") == [
        ("members_list", "The original name was Members List"),
        ("members_list.member_id", "The original name was Member ID"),
        ("members_list.age", "The original name was Age"),
    ]


def test_a_tsv_file_is_read_with_tabs(roots: Roots) -> None:
    result = _import(roots, roots.write("books.tsv", b"id\ttitle\n1\tDune\n"))
    source = _tables(result)["books"].fields.source
    assert source is not None
    assert source.parse is not None
    assert (source.parse.format, source.parse.delimiter) == ("tsv", "\t")


def test_a_workbook_gives_a_table_per_sheet_by_sheet_name(roots: Roots, make_xlsx: Make) -> None:
    data = make_xlsx(
        [
            ("Loans", [["Loan", "When"], [1, datetime(2024, 1, 2, 10, 0)], [2, None]]),
            ("Notes", []),
            ("Header only", [["a", "b"]]),
        ]
    )
    result = _import(roots, roots.write("loans.xlsx", data))
    assert list(_tables(result)) == ["loans", "header_only"]
    assert result.sources["loans"] == TypedSource(
        ("Loan", "When"), ((1.0, datetime(2024, 1, 2, 10, 0)), (2.0, None))
    )
    assert result.sources["header_only"] == TypedSource(("a", "b"), ())
    assert _notes(result, "skipped_source") == [(None, "Skipped Notes: it has no cell")]
    loans = _tables(result)["loans"]
    assert loans.fields.source is not None
    assert loans.fields.source.kind == "sheet"
    assert loans.curation["/fields/source"].status == "imported"
    assert _datatypes(result, "loans") == {"loan": "integer", "when": "datetime"}


def test_a_sheet_is_trimmed_of_empty_edges_and_blank_rows(roots: Roots, make_xlsx: Make) -> None:
    rows = [
        [],
        [None, "id", "size", None],
        [None, "a", 1, None],
        [None, None, None, None],
        [None, "b", 2],
    ]
    result = _import(roots, roots.write("s.xlsx", make_xlsx([("S", rows)])))
    assert result.sources["s"] == TypedSource(("id", "size"), (("a", 1.0), ("b", 2.0)))


def test_error_time_and_duration_cells(roots: Roots, make_xlsx: Make, error_cell: Any) -> None:
    rows = [
        ["e", "t", "d"],
        [error_cell("#N/A"), time(12, 30), timedelta(days=1, hours=6)],
        ["x", time(0, 0, 5), timedelta(minutes=90)],
    ]
    result = _import(roots, roots.write("s.xlsx", make_xlsx([("S", rows)])))
    source = result.sources["s"]
    assert isinstance(source, TypedSource)
    assert source.rows == ((None, "12:30:00", "P1DT6H"), ("x", "00:00:05", "PT1H30M"))


def test_durations_are_iso_8601() -> None:
    assert duration(timedelta(0)) == "PT0S"
    assert duration(timedelta(seconds=1.5)) == "PT1.5S"
    assert duration(-timedelta(days=2)) == "-P2D"


def test_an_ods_workbook_reads_as_an_xlsx_one(
    roots: Roots, make_xlsx: Make, make_ods: Make
) -> None:
    sheets = [("Loans", [["Loan", "Day", "Back"], [1, date(2024, 1, 2), True], [2.5, None, False]])]
    from_xlsx = _import(roots, roots.write("a/loans.xlsx", make_xlsx(sheets)))
    from_ods = _import(roots, roots.write("b/loans.ods", make_ods(sheets)))
    assert from_ods.sources == from_xlsx.sources
    assert _datatypes(from_ods, "loans") == {"loan": "number", "day": "date", "back": "boolean"}


def test_a_workbook_that_cannot_be_read_is_unparseable(roots: Roots) -> None:
    for name in ("broken.xlsx", "broken.ods"):
        with pytest.raises(ImportRefused) as refused:
            _import(roots, roots.write(name, b"not a workbook"))
        assert [r.code for r in refused.value.refusals] == ["UNPARSEABLE_SOURCE"]


def test_an_xls_workbook_is_refused_with_the_kinds_to_convert_to(roots: Roots) -> None:
    """python-calamine allocates an XLS sheet's extent before anything can bound it (D225)."""
    with pytest.raises(ImportRefused) as refused:
        _import(roots, roots.write("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(600)))
    [refusal] = refused.value.refusals
    assert refusal.code == "UNSUPPORTED_FORMAT"
    alternatives = [segment.model_dump()["text"] for segment in refusal.alternatives]
    assert alternatives == [".xlsx", ".ods", ".csv"]


def test_parquet_types_map_to_source_values(roots: Roots) -> None:
    table = pa.table(
        {
            "i": pa.array([1, None], pa.int16()),
            "u": pa.array([2**63, 1], pa.uint64()),
            "f": pa.array([1.5, float("nan")], pa.float32()),
            "b": pa.array([True, False]),
            "s": pa.array(["x", "y"]).dictionary_encode(),
            "d": pa.array([date(2024, 1, 2), None], pa.date32()),
            "tz": pa.array([datetime(2024, 1, 2, 10), None], pa.timestamp("ns", tz="+02:00")),
            "naive": pa.array([datetime(2024, 1, 2, 10, 0, 0, 1), None], pa.timestamp("us")),
            "dec": pa.array([Decimal("1.50"), Decimal("1E+3")], pa.decimal128(8, 2)),
            "t": pa.array([time(1, 2, 3), None], pa.time32("s")),
            "l": pa.array([["a", None], []], pa.list_(pa.string())),
            "n": pa.array([None, None], pa.null()),
        }
    )
    result = _import(roots, roots.write("things.parquet", _parquet(table)))
    source = result.sources["things"]
    assert isinstance(source, TypedSource)
    first = dict(zip(source.columns, source.rows[0], strict=True))
    assert first["i"] == 1
    assert first["u"] == 2**63
    assert first["b"] is True
    assert first["s"] == "x"
    assert first["d"] == date(2024, 1, 2)
    assert isinstance(first["tz"], datetime)
    assert first["tz"].utcoffset() == timedelta(hours=2)
    assert first["naive"] == datetime(2024, 1, 2, 10, 0, 0, 1)
    assert (first["dec"], source.rows[1][8]) == ("1.50", "1000.00")
    assert first["t"] == "01:02:03"
    assert first["l"] == '["a", null]'
    assert first["n"] is None
    datatypes = _datatypes(result, "things")
    assert datatypes["i"] == "integer"
    assert datatypes["tz"] == "datetime"
    statuses = {
        d.id.split(".")[1]: d.curation["/fields/datatype"].status
        for d in result.descriptors
        if isinstance(d, ColumnDescriptor) and "/fields/datatype" in d.curation
    }
    assert statuses["i"] == statuses["tz"] == statuses["b"] == "imported"
    assert statuses["naive"] == "imported_default"
    naive = next(d for d in result.descriptors if d.id == "things.naive")
    assert "read as UTC" in (naive.curation["/fields/datatype"].evidence or "")
    tz = next(d for d in result.descriptors if d.id == "things.tz")
    assert tz.curation["/fields/datatype"].evidence == "The source declares the type (D227)"
    assert statuses["dec"] == statuses["s"] == "proposed"
    assert datatypes["l"] == "list<category>"
    assert datatypes["n"] is None
    table_source = _tables(result)["things"].fields.source
    assert table_source is not None
    assert table_source.parse is None


def test_unsupported_parquet_types_are_refused_with_those_read(roots: Roots) -> None:
    table = pa.table({"blob": pa.array([b"x"], pa.binary())})
    with pytest.raises(ImportRefused) as refused:
        _import(roots, roots.write("blobs.parquet", _parquet(table)))
    [refusal] = refused.value.refusals
    assert refusal.code == "UNSUPPORTED_FORMAT"
    said = [segment.model_dump() for segment in refusal.message]
    assert said[:3] == [{"text": "In "}, {"data": "blobs.parquet"}, {"text": ": "}]
    assert "binary" in said[3]["text"]
    assert "bool" in [segment.model_dump()["text"] for segment in refusal.alternatives]


def test_an_unsupported_file_is_refused_with_the_kinds_read(roots: Roots) -> None:
    with pytest.raises(ImportRefused) as refused:
        _import(roots, roots.write("notes.docx", b"x"))
    [refusal] = refused.value.refusals
    assert refusal.code == "UNSUPPORTED_FORMAT"
    assert ".parquet" in [segment.model_dump()["text"] for segment in refusal.alternatives]


def test_a_directory_is_read_flat_and_notes_what_it_skips(roots: Roots, make_xlsx: Make) -> None:
    roots.write("lib/members.csv", b"member_id\nm1\n")
    roots.write(
        "lib/loans.xlsx", make_xlsx([("Loans", [["loan_id"], [1]]), ("Fees", [["fee"], [2]])])
    )
    roots.write("lib/.hidden.csv", b"x\n1\n")
    roots.write("lib/readme.md", b"# library\n")
    roots.write("lib/old/members.csv", b"member_id\nm1\n")
    roots.write("lib/inner.zip", b"PK")
    roots.write("lib/old.xls", b"x")
    (roots.outside / "secret.csv").write_bytes(b"secret\n1\n")
    (roots.inside / "lib" / "copy.csv").symlink_to(roots.inside / "lib" / "members.csv")
    (roots.inside / "lib" / ".link.csv").symlink_to(roots.inside / "lib" / "members.csv")
    (roots.inside / "lib" / "out.csv").symlink_to(roots.outside / "secret.csv")
    os.mkfifo(roots.inside / "lib" / "pipe.csv")
    result = _import(roots, roots.inside / "lib")
    assert list(_tables(result)) == ["loans_loans", "loans_fees", "members"]
    assert _notes(result, "skipped_source") == [
        (None, "Skipped .hidden.csv: a hidden file"),
        (None, "Skipped .link.csv: a hidden file"),
        (None, "Skipped copy.csv: a symbolic link, which is never followed"),
        (None, "Skipped inner.zip: an archive inside the source, which is not read (D225)"),
        (None, "Skipped old: a directory, and directories are flat"),
        (
            None,
            "Skipped old.xls: a kind of file that is not read; convert it to .xlsx, .ods, .csv "
            "(D225)",
        ),
        (None, "Skipped out.csv: a symbolic link, which is never followed"),
        (None, "Skipped pipe.csv: neither a regular file nor a directory"),
        (None, "Skipped readme.md: not a kind of file that is read"),
    ]
    dataset = result.descriptors[0]
    assert dataset.fields.model_dump(mode="json") == {
        "name": "lib",
        "source": {"kind": "files", "location": "lib"},
        "packs": [],
    }


def test_a_zip_is_read_at_any_depth(roots: Roots, make_zip: Make, entry: Any) -> None:
    data = make_zip(
        [
            entry("export/members.csv", b"member_id\nm1\n"),
            entry("export/deep/books.tsv", b"book_id\nb1\n"),
            entry("export/__MACOSX/.x.csv", b"x\n"),
            entry("export/notes.txt.bak", b"x"),
        ]
    )
    result = _import(roots, roots.write("export.zip", data))
    assert list(_tables(result)) == ["books", "members"]
    assert [subject for subject, _ in _notes(result, "skipped_source")] == [None, None]


def test_tables_named_alike_get_distinct_ids(roots: Roots) -> None:
    roots.write("dup/Members.csv", b"a\n1\n")
    roots.write("dup/members.tsv", b"a\n1\n")
    roots.write("dup/dataset.csv", b"a\n1\n")
    result = _import(roots, roots.inside / "dup")
    assert list(_tables(result)) == ["members", "dataset_2", "members_2"]


def test_a_source_with_no_table_is_refused(roots: Roots, make_xlsx: Make) -> None:
    with pytest.raises(ImportRefused) as refused:
        _import(roots, roots.write("empty.xlsx", make_xlsx([("A", []), ("B", [])])))
    assert [r.code for r in refused.value.refusals] == ["EMPTY_SOURCE"]


def test_the_cell_limit_counts_every_row(roots: Roots) -> None:
    roots.write("rows/a.csv", b"x,y\n1,2\n3,4\n5,6\n")
    roots.write("rows/b.csv", b"x,y\n1,2\n3,4\n5,6\n")
    assert len(_tables(_import(roots, roots.inside / "rows", limits=ImportLimits(import_cells=12))))
    with pytest.raises(ImportRefused) as refused:
        _import(roots, roots.inside / "rows", limits=ImportLimits(import_cells=11))
    [refusal] = refused.value.refusals
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("import_cells", 11)


def test_a_text_file_s_cells_are_counted_while_it_is_parsed(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows: list[int] = []
    real = sources.parse_text

    def counting(data: bytes, settings: Any, *, max_cells: int | None = None) -> Any:
        rows.append(-1 if max_cells is None else max_cells)
        return real(data, settings, max_cells=max_cells)

    monkeypatch.setattr(detect_module, "parse_text", counting)
    path = roots.write("big.csv", b"x,y\n" + b"1,2\n" * 1000)
    with pytest.raises(ImportRefused) as refused:
        _import(roots, path, limits=ImportLimits(import_cells=100))
    assert refused.value.refusals[0].limit is not None
    assert refused.value.refusals[0].limit.name == "import_cells"
    assert rows == [100]


def test_the_dataset_s_location_is_its_path_below_the_root(roots: Roots) -> None:
    path = roots.write("exports/2024/members.csv", b"id\n1\n")
    dataset = _import(roots, path).descriptors[0]
    assert dataset.fields.model_dump(mode="json")["source"] == {
        "kind": "files",
        "location": "exports/2024/members.csv",
    }
    roots.write("b.csv", b"id\n2\n")
    whole = _import(roots, roots.inside).descriptors[0]
    assert whole.fields.model_dump(mode="json")["source"]["location"] == roots.inside.name


def test_the_limits_on_tables_columns_and_cells_apply(roots: Roots) -> None:
    roots.write("many/a.csv", b"x,y\n1,2\n")
    roots.write("many/b.csv", b"x,y\n1,2\n")
    cases = [
        (ImportLimits(import_tables=1), "import_tables"),
        (ImportLimits(table_columns=1), "table_columns"),
        (ImportLimits(import_cells=3), "import_cells"),
    ]
    assert _tables(_import(roots, roots.inside / "many", limits=ImportLimits(table_columns=2)))
    for limits, name in cases:
        with pytest.raises(ImportRefused) as refused:
            _import(roots, roots.inside / "many", limits=limits)
        [refusal] = refused.value.refusals
        assert refusal.limit is not None
        assert refusal.limit.name == name


LONG = "n" * (MAX_STRING + 1)


def _string_limit(error: pytest.ExceptionInfo[ImportRefused]) -> tuple[str, str | None]:
    [refusal] = error.value.refusals
    return refusal.code, None if refusal.limit is None else refusal.limit.name


def test_names_longer_than_a_descriptor_s_string_are_refused(
    roots: Roots, make_xlsx: Make, make_ods: Make, make_zip: Make, entry: Any
) -> None:
    sources: list[tuple[str, bytes]] = [
        ("header.csv", f"id,{LONG}\n1,2\n".encode()),
        ("header.xlsx", make_xlsx([("S", [["id", LONG], [1, 2]])])),
        ("header.ods", make_ods([("S", [["id", LONG], [1, 2]])])),
        ("sheet.xlsx", make_xlsx([(LONG, [["id"], [1]])])),
        ("column.parquet", _parquet(pa.table({"id": [1], LONG: [2]}))),
        ("member.zip", make_zip([entry(LONG + ".csv", b"id\n1\n")])),
    ]
    for name, content in sources:
        with pytest.raises(ImportRefused) as refused:
            _import(roots, roots.write(name, content), limits=ImportLimits())
        assert _string_limit(refused) == ("LIMIT_EXCEEDED", "string_characters"), name
    exact = _import(roots, roots.write("exact.csv", f"id,{LONG[1:]}\n1,2\n".encode()))
    assert exact.layouts["exact"].columns[1][1] == LONG[1:]
    path = roots.write("fine.csv", b"id\n1\n")
    for options in ({"name": LONG}, {"original_name": LONG + ".csv"}):
        with pytest.raises(ImportRefused) as refused:
            _import(roots, path, **options)
        assert _string_limit(refused) == ("LIMIT_EXCEEDED", "string_characters")


def _hostile_parquet() -> dict[str, bytes]:
    utf8 = pa.Array.from_buffers(
        pa.string(), 1, [None, pa.py_buffer(struct.pack("<ii", 0, 2)), pa.py_buffer(b"\xff\xfe")]
    )
    return {
        "same names": _parquet(pa.Table.from_arrays([pa.array([1]), pa.array([2])], ["a", "a"])),
        "seconds past 9999": _parquet(pa.table({"t": pa.array([10**13], pa.timestamp("s"))})),
        "a far date": _parquet(pa.table({"d": pa.array([10**8], pa.date32())})),
        "an unknown zone": _parquet(
            pa.table({"t": pa.array([0], pa.timestamp("us", tz="Mars/Olympus"))})
        ),
        "a thrift header of garbage": b"PAR1" + bytes(100) + b"PAR1",
        "invalid UTF-8": _parquet(pa.table({"s": utf8})),
    }


@pytest.mark.parametrize("case", list(_hostile_parquet()))
def test_a_hostile_parquet_file_is_unparseable(roots: Roots, case: str) -> None:
    path = roots.write("hostile.parquet", _hostile_parquet()[case])
    with pytest.raises(ImportRefused) as refused:
        _import(roots, path)
    assert [r.code for r in refused.value.refusals] == ["UNPARSEABLE_SOURCE"]


# --- Workbooks and Parquet files, read in the worker process (D225) ----------------------------

SMALL = ImportLimits(reader_memory=512 << 20, reader_seconds=5)
"""Limits under which a hostile file fails fast, in the tests' own process."""
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_HEADER = '<row r="1"><c r="A1" t="inlineStr"><is><t>id</t></is></c></row>'


def _parts(data: bytes) -> dict[str, bytes]:
    archive = zipfile.ZipFile(io.BytesIO(data))
    return {info.filename: archive.read(info) for info in archive.infolist()}


def _zipped(parts: dict[str, bytes]) -> bytes:
    written = io.BytesIO()
    with zipfile.ZipFile(written, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return written.getvalue()


def _with_sheet(make_xlsx: Make, sheet: str, part: str = "xl/worksheets/sheet1.xml") -> bytes:
    """A workbook whose one worksheet's XML is ``sheet``, in the part ``part``."""
    parts = _parts(make_xlsx([("S", [["id"], [1]])]))
    del parts["xl/worksheets/sheet1.xml"]
    parts[part] = f'<?xml version="1.0"?><worksheet xmlns="{_MAIN}">{sheet}</worksheet>'.encode()
    for name in ("xl/_rels/workbook.xml.rels", "[Content_Types].xml"):
        written = parts[name].decode().replace("worksheets/sheet1.xml", part.removeprefix("xl/"))
        parts[name] = written.encode()
    return _zipped(parts)


def _with_part(make_xlsx: Make, part: str, content: str) -> bytes:
    return _zipped({**_parts(make_xlsx([("S", [["id"], [1]])])), part: content.encode()})


def _cells(*cells: str) -> str:
    return f'<sheetData>{_HEADER}<row r="1048576">{"".join(cells)}</row></sheetData>'


FAR = _cells('<c r="XFD1048576"><v>1</v></c>')
BAD = '<sheetData><row r="1"><c r="ZZZZZZZZ99999999999"><v>1</v></c></row></sheetData>'


def _refusal(roots: Roots, name: str, content: bytes, limits: ImportLimits = SMALL) -> Any:
    with pytest.raises(ImportRefused) as refused:
        _import(roots, roots.write(name, content), limits=limits)
    [refusal] = refused.value.refusals
    return refusal.code, None if refusal.limit is None else refusal.limit.name


def _far_workbooks(make_xlsx: Make) -> dict[str, bytes]:
    padding = "".join(random.Random(0).choices(string.ascii_letters, k=1 << 20))
    return {
        "far": _with_sheet(make_xlsx, FAR),
        "a part not named .xml": _with_sheet(make_xlsx, FAR, "xl/worksheets/sheet1.bin"),
        "a part with no extension": _with_sheet(make_xlsx, FAR, "xl/worksheets/sheet1"),
        "r inside another value": _with_sheet(
            make_xlsx, _cells("""<c foo=' r="A2"' r="XFD1048576"><v>1</v></c>""")
        ),
        "r twice": _with_sheet(make_xlsx, _cells('<c r="A2" r="XFD1048576"><v>1</v></c>')),
        "< in a value past 1 MiB": _with_sheet(
            make_xlsx, _cells(f'<c foo="<{padding}" r="XFD1048576"><v>1</v></c>')
        ),
        "shared strings of a huge count": _with_part(
            make_xlsx,
            "xl/sharedStrings.xml",
            f'<?xml version="1.0"?><sst xmlns="{_MAIN}" count="4000000000" '
            'uniqueCount="4000000000"><si><t>a</t></si></sst>',
        ),
    }


def test_a_tiny_workbook_that_reaches_far_is_refused_in_the_worker(
    roots: Roots, make_xlsx: Make
) -> None:
    """Each allocates hundreds of GiB in calamine; the worker dies of it, and the tests do not."""
    for case, content in _far_workbooks(make_xlsx).items():
        assert len(content) < 1 << 20, case
        assert _refusal(roots, "far.xlsx", content) == ("LIMIT_EXCEEDED", "reader_memory"), case
    assert _refusal(roots, "bad.xlsx", _with_sheet(make_xlsx, BAD))[0] in (
        "LIMIT_EXCEEDED",
        "UNPARSEABLE_SOURCE",
    )


def _shared(make_xlsx: Make, text: str, n: int) -> bytes:
    """A workbook of one column whose ``n`` rows each name one shared string, ``text``."""
    shared = f'<sst xmlns="{_MAIN}" uniqueCount="1"><si><t>{text}</t></si></sst>'
    rows = "".join(f'<row r="{i + 2}"><c r="A{i + 2}" t="s"><v>0</v></c></row>' for i in range(n))
    content = _with_sheet(make_xlsx, f"<sheetData>{_HEADER}{rows}</sheetData>")
    return _zipped({**_parts(content), "xl/sharedStrings.xml": shared.encode()})


def test_a_shared_string_every_cell_names_is_refused(roots: Roots, make_xlsx: Make) -> None:
    text = os.urandom(50_000).hex()
    for n, limits, name in (
        (6000, SMALL, "reader_memory"),
        (10, dataclasses.replace(SMALL, decoded_bytes=999_999), "decoded_bytes"),
    ):
        content = _shared(make_xlsx, text, n)
        assert _refusal(roots, "shared.xlsx", content, limits) == ("LIMIT_EXCEEDED", name)
    under = dataclasses.replace(SMALL, decoded_bytes=1_000_003)  # with "id" and "S"
    assert _tables(
        _import(roots, roots.write("ten.xlsx", _shared(make_xlsx, text, 10)), limits=under)
    )


def test_no_typed_cell_holds_more_than_a_text_file_s_field(roots: Roots, make_xlsx: Make) -> None:
    """A text file's field holds at most 131,072 characters, and so does a sheet's or a Parquet
    file's cell: the server holds no longer string from any file (D225)."""
    assert csv.field_size_limit() == sources.FIELD_CHARACTERS == 131_072
    for size, accepted in ((131_072, True), (131_073, False)):
        text = "\U0001f600" + "a" * (size - 1)
        files = {
            "long.xlsx": _shared(make_xlsx, text, 1),
            "long.parquet": _parquet(pa.table({"id": [1, 2], "s": ["a", text]})),
        }
        for name, content in files.items():
            if accepted:
                [raw] = _import(roots, roots.write(name, content)).sources.values()
                assert isinstance(raw, TypedSource)
                assert raw.rows[-1][-1] == text
                continue
            table, cell = (
                ("The sheet S", "the column id, row 1")
                if name.endswith("xlsx")
                else ("The file", "the column s, row 2")
            )
            assert _long_cell(roots, name, content) == _long(name, table, cell)


def _long_cell(roots: Roots, name: str, content: bytes) -> str:
    """The message of the refusal of a file with a cell too long."""
    with pytest.raises(ImportRefused) as refused:
        _import(roots, roots.write(name, content))
    [refusal] = refused.value.refusals
    assert refusal.code == "UNPARSEABLE_SOURCE", name
    return "".join(
        str(s.model_dump().get("text", s.model_dump().get("data"))) for s in refusal.message
    )


def _long(name: str, table: str, cell: str) -> str:
    return (
        f"In {name}: {table} has a cell of more than 131072 characters, the most a cell holds: "
        f"{cell} of the table"
    )


def test_a_cell_too_long_is_named_by_its_column_s_header_and_its_row_in_the_table(
    roots: Roots, make_xlsx: Make
) -> None:
    """Not by the sheet's coordinates: here the header is the sheet's third row, a blank row is
    dropped and column A is empty, so the cell at D7 is row 3 of the table, in the column y. A
    column with no name is named by its place in the table."""
    text = "a" * 131_073
    blank: list[object] = []
    rows = [blank, blank, [None, "id", "x", "y"], [None, 1, "a", "b"], blank, [None, 2, "a", "b"]]
    rows.append([None, 3, "a", text])
    for name, content, table, cell in (
        ("offset.xlsx", make_xlsx([("S", rows)]), "The sheet S", "the column y, row 3"),
        (
            "nameless.xlsx",
            make_xlsx([("S", [["id", None], [1, text]])]),
            "The sheet S",
            "column 2, which has no name, row 1",
        ),
        (
            "nameless.parquet",
            _parquet(pa.table({"id": [1, 2], "": ["a", text]})),
            "The file",
            "column 2, which has no name, row 2",
        ),
    ):
        assert _long_cell(roots, name, content) == _long(name, table, cell)


def _ods(rows: str, close: str = "</table:table>") -> bytes:
    namespaces = (
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    )
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {namespaces} '
        'office:version="1.2"><office:body><office:spreadsheet><table:table table:name="S">'
        f"{rows}{close}</office:spreadsheet></office:body></office:document-content>"
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:'
        'names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry '
        'manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.'
        'spreadsheet"/><manifest:file-entry manifest:full-path="content.xml" '
        'manifest:media-type="text/xml"/></manifest:manifest>'
    )
    written = io.BytesIO()
    with zipfile.ZipFile(written, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        archive.writestr("META-INF/manifest.xml", manifest)
        archive.writestr("content.xml", content)
    return written.getvalue()


_VALUE = (
    '<table:table-cell office:value-type="float" office:value="1"{}><text:p>1</text:p>'
    "</table:table-cell>"
)


def test_an_ods_string_repeated_is_refused(roots: Roots) -> None:
    text = os.urandom(1 << 19).hex()
    cell = (
        '<table:table-cell table:number-columns-repeated="30" office:value-type="string">'
        f"<text:p>{text}</text:p></table:table-cell>"
    )
    rows = f'<table:table-row table:number-rows-repeated="100">{cell}</table:table-row>'
    assert _refusal(roots, "repeated.ods", _ods(rows)) == ("LIMIT_EXCEEDED", "reader_memory")


def test_an_ods_table_never_closed_is_refused_when_its_reading_takes_too_long(
    roots: Roots,
) -> None:
    limits = ImportLimits(reader_seconds=1)
    for close in ("", "</table:tabl"):
        content = _ods(f"<table:table-row>{_VALUE.format('')}</table:table-row>", close)
        assert _refusal(roots, "open.ods", content, limits) == ("LIMIT_EXCEEDED", "reader_seconds")


def test_a_parquet_dictionary_decoded_into_every_row_is_refused(roots: Roots) -> None:
    text = os.urandom(1 << 19).hex()
    column = pa.DictionaryArray.from_arrays(pa.array([0] * 1000, pa.int32()), pa.array([text]))
    content = _parquet(pa.table({"id": pa.array(range(1000)), "s": column}))
    assert len(content) < 2 << 20
    assert _refusal(roots, "bomb.parquet", content) == ("LIMIT_EXCEEDED", "reader_memory")


def test_an_xlsb_workbook_is_refused_whatever_its_extension(roots: Roots, make_xlsx: Make) -> None:
    for part in ("xl/workbook.bin", "XL/Workbook.bin"):
        content = _with_part(make_xlsx, part, "")
        assert _refusal(roots, "binary.xlsx", content) == ("UNSUPPORTED_FORMAT", None)


def test_the_reads_of_one_import_share_one_worker(
    roots: Roots, make_xlsx: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[int] = []
    real = worker.Reader._start

    def counting(self: worker.Reader) -> Any:
        if self._child is None:
            started.append(1)
        return real(self)

    monkeypatch.setattr(worker.Reader, "_start", counting)
    roots.write("both/a.xlsx", make_xlsx([("S", [["id"], [1]])]))
    roots.write("both/b.xlsx", make_xlsx([("S", [["id"], [2]])]))
    roots.write("both/c.parquet", _parquet(pa.table({"id": [3]})))
    roots.write("both/d.csv", b"id\n4\n")
    assert len(_tables(_import(roots, roots.inside / "both"))) == 4
    assert started == [1]
    started.clear()
    _import(roots, roots.write("text/only.csv", b"id\n1\n"))
    assert started == []


@pytest.mark.parametrize(
    ("sheet", "cells"),
    [
        ('<sheetData><row r="5000"><c><v>1</v></c><c><v>2</v></c></row></sheetData>', 10_000),
        (
            "<sheetData><row><c><v>1</v></c></row><row><c><v>1</v></c></row><row><c><v>1</v></c>"
            "<c><v>2</v></c></row></sheetData>",
            6,
        ),
        (
            '<sheetData><row r="3"><c r="C3"><v>1</v></c></row><row><c/><c/><c/></row>'
            '<row><c r="E1"><v>2</v></c></row></sheetData>',
            15,
        ),
    ],
    ids=["positional", "unnumbered", "rows"],
)
def test_a_sheet_s_extent_is_counted_from_a1_before_its_cells_become_values(
    make_xlsx: Make, sheet: str, cells: int
) -> None:
    data = _with_sheet(make_xlsx, sheet)
    limits = ImportLimits(import_cells=cells - 1)
    with pytest.raises(ImportRefused) as refused:
        sheets.read_workbook(data, limits, limits.import_cells)
    assert refused.value.refusals[0].limit is not None
    assert refused.value.refusals[0].limit.name == "import_cells"
    assert sheets.read_workbook(data, ImportLimits(import_cells=cells), cells)


def test_the_cells_left_bound_a_workbook_read_after_other_files(make_xlsx: Make) -> None:
    data = make_xlsx([("S", [["a", "b"], [1, 2]])])
    with pytest.raises(ImportRefused) as refused:
        sheets.read_workbook(data, ImportLimits(import_cells=10), 3)
    assert refused.value.refusals[0].limit is not None
    assert (refused.value.refusals[0].limit.name, refused.value.refusals[0].limit.max) == (
        "import_cells",
        10,
    )


def test_a_declared_dimension_and_empty_far_cells_do_not_count(make_xlsx: Make) -> None:
    sheet = (
        '<dimension ref="A1:XFD1048576"/><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>'
        'id</t></is></c></row><row r="2"><c r="A2"><v>1</v></c><c r="XFD1048576" s="1"/></row>'
        '</sheetData><mergeCells count="1"><mergeCell ref="A1:XFD1048576"/></mergeCells>'
    )
    limits = dataclasses.replace(SMALL, import_cells=2)
    with worker.Reader(limits) as reader:
        [read] = reader.run(sheets.read_workbook, _with_sheet(make_xlsx, sheet), limits, 2)
    assert read.source == TypedSource(("id",), ((1.0,),))


def test_an_ods_sheet_counts_its_repeats_up_to_its_last_value() -> None:
    trailing = (
        f"<table:table-row>{_VALUE.format('')}</table:table-row><table:table-row "
        'table:number-rows-repeated="1048575"><table:table-cell '
        'table:number-columns-repeated="1024"/></table:table-row>'
    )
    closed = (
        '<table:table-row table:number-rows-repeated="5000"><table:table-cell '
        'office:value-type="float" office:value="1" table:number-columns-repeated="4"/>'
        "</table:table-row>"
    )
    one = dataclasses.replace(SMALL, import_cells=1)
    with worker.Reader(one) as reader:
        [sheet] = reader.run(sheets.read_workbook, _ods(trailing), one, 1)
    assert sheet.source == TypedSource(("1",), ())
    under = dataclasses.replace(SMALL, import_cells=19_999)
    with worker.Reader(under) as reader, pytest.raises(ImportRefused) as refused:
        reader.run(sheets.read_workbook, _ods(closed), under, 19_999)
    assert refused.value.refusals[0].limit is not None
    assert refused.value.refusals[0].limit.name == "import_cells"


def test_sheets_together_are_bounded_too(make_xlsx: Make) -> None:
    data = make_xlsx([("A", [["a", "b"], [1, 2]]), ("B", [["a", "b"], [1, 2]])])
    assert len(sheets.read_workbook(data, ImportLimits(import_cells=8), 8)) == 2
    with pytest.raises(ImportRefused) as refused:
        sheets.read_workbook(data, ImportLimits(import_cells=7), 7)
    assert refused.value.refusals[0].code == "LIMIT_EXCEEDED"
    assert "together" in str(refused.value.refusals[0].message)


def test_a_cell_reference_or_repeat_that_is_no_number_is_refused(make_xlsx: Make) -> None:
    for data in (
        _with_sheet(make_xlsx, '<sheetData><row r="1"><c r="1A"><v>1</v></c></row></sheetData>'),
        _with_sheet(make_xlsx, '<sheetData><row r="x"><c><v>1</v></c></row></sheetData>'),
        _ods(
            '<table:table-row table:number-rows-repeated="-3"><table:table-cell/></table:table-row>'
        ),
    ):
        with worker.Reader(SMALL) as reader, pytest.raises(ImportRefused) as refused:
            reader.run(sheets.read_workbook, data, SMALL, SMALL.import_cells)
        assert refused.value.refusals[0].code == "UNPARSEABLE_SOURCE"


def test_a_parquet_file_s_cells_are_its_rows_times_its_columns() -> None:
    content = _parquet(pa.table({"a": [1, 2], "b": [3, 4], "c": [5, 6]}))
    with pytest.raises(parquet.CellLimitError) as refused:
        parquet.read_source(content, 5)
    assert refused.value.cells == 6
    assert len(parquet.read_source(content, 6).rows) == 2
