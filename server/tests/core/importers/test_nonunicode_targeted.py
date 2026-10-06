"""Names that are not Unicode text: the cases beyond the brute force (SPEC §8.1, §14, D225, D232,
D253, D307, D309, D310, D397, D398)."""

import io
import os
import re
import sqlite3
import zipfile
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from tests.core.importers.builders import Entry, build_xlsx, build_zip
from tests.core.importers.nonunicode import check_shown, said

from aibi.core.importers.confine import Confinement
from aibi.core.importers.databases import Connection, DatabaseImporter, resolve
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.files import FileImporter
from aibi.core.schema.limits import MAX_STRING, ImportLimits
from aibi.core.schema.output import DataSegment, TextSegment
from aibi.core.schema.pack_api import ImportOptions, ImportResult

AT = "2026-01-01T00:00:00Z"


def _options(confinement: Confinement) -> ImportOptions:
    return ImportOptions(dataset="d", reader=confinement, limits=ImportLimits(), at=AT)


def _refused(run: object, *, checked: bool = True) -> ImportRefused:
    """The refusal ``run`` raises, each segment checked (``check_shown``) unless it may show what
    a name never does: confine's ``\\0`` for a NUL, a library's class for a cell (D397)."""
    assert callable(run)
    with pytest.raises(ImportRefused) as raised:
        run()
    for refusal in raised.value.refusals:
        if checked:
            check_shown([*refusal.message, *refusal.alternatives])
    return raised.value


def _written(error: ImportRefused) -> str:
    return "".join(
        s.text if isinstance(s, TextSegment) else s.data for s in error.refusals[0].message
    )


# --- confine, each layer alone with its words (D232, D398) --------------------------------------


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("x" + chr(0xD800), "A path that cannot be a file's name: "),
        ("x" + chr(0xDBFF), "A path that cannot be a file's name: "),
        ("x" + chr(0xDC00), "A path that cannot be a file's name: "),
        ("x" + chr(0xDC80), "No such file or directory: "),
        ("x" + chr(0xDCFF), "No such file or directory: "),
        ("x\ufffe", "No such file or directory: "),
        ("a\0b", "A path cannot hold a NUL character: "),
        ("x\0" + chr(0xD800), "A path that cannot be a file's name: "),
    ],
    ids=["D800", "DBFF", "DC00", "DC80", "DCFF", "FFFE", "NUL", "NUL and D800"],
)
def test_confine_refuses_in_order_the_pre_check_then_nul_then_the_rest(
    tmp_path: Path, name: str, words: str
) -> None:
    confinement = Confinement.of(tmp_path)
    error = _refused(lambda: confinement.confine(f"{tmp_path}/{name}"), checked="\0" not in name)
    assert error.refusals[0].code == "PATH_NOT_CONFINED"
    assert said(error.refusals[0].message) == words


@pytest.mark.parametrize(
    "name", ["x" + chr(0xD800), "x" + chr(0xDBFF), "x" + chr(0xDC00)], ids=["D800", "DBFF", "DC00"]
)
def test_confine_s_second_layer_alone_refuses_a_path_that_cannot_be_encoded(
    tmp_path: Path, name: str
) -> None:
    confinement = Confinement.of(tmp_path)
    resolved = confinement._resolved  # pyright: ignore[reportPrivateUsage]
    error = _refused(lambda: resolved(f"{tmp_path}/{name}"))
    assert said(error.refusals[0].message) == "No such file or directory: "


def test_a_name_in_the_surrogate_escape_range_is_a_file_s_name(tmp_path: Path) -> None:
    os.mkdir(os.path.join(os.fsencode(tmp_path), b"\xff"))
    confined = Confinement.of(tmp_path).confine(f"{tmp_path}/\udcff")
    assert os.fsencode(confined).endswith(b"/\xff")


# --- A database file below a text root, in a directory that is not UTF-8 (D310, D398) -----------


def _database(kind: str, path: Path) -> None:
    if kind == "sqlite":
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE t (a INT)")
        connection.close()
    else:
        with duckdb.connect(str(path)) as connection:
            connection.execute("CREATE TABLE t (a INT)")


@pytest.mark.parametrize("kind", ["sqlite", "duckdb"])
def test_a_connection_s_file_in_a_directory_that_is_not_utf8_is_refused_by_its_location(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    folder = os.path.join(os.fsencode(root), b"\xffsub")
    os.mkdir(folder)
    made = tmp_path / "made.db"
    _database(kind, made)
    os.rename(made, os.path.join(folder, b"x.db"))
    os.symlink(os.path.join(folder, b"x.db"), root / "link.db")
    confinement = Confinement.of(root)
    error = _refused(
        lambda: DatabaseImporter().import_database(
            resolve(Connection("c", kind, root / "link.db"), confinement, {}),  # type: ignore[arg-type]
            _options(confinement),
        )
    )
    assert error.refusals[0].code == "UNPARSEABLE_SOURCE"
    assert _written(error) == (
        "The source's location is not Unicode text (a lone surrogate or a noncharacter): "
        "\\udcffsub/x.db"
    )


@pytest.mark.parametrize("kind", ["sqlite", "duckdb"])
@pytest.mark.parametrize("ending", [b"\xff", "\uffff".encode()], ids=["not-utf8", "noncharacter"])
def test_an_import_directory_whose_real_path_is_not_text_is_refused_naming_no_path(
    tmp_path: Path, kind: str, ending: bytes
) -> None:
    root = os.path.join(os.fsencode(tmp_path), b"r" + ending)
    os.mkdir(root)
    made = tmp_path / "made.db"
    _database(kind, made)
    os.rename(made, os.path.join(root, b"x.db"))
    confinement = Confinement.of(Path(os.fsdecode(root)))
    path = Path(os.fsdecode(os.path.join(root, b"x.db")))
    error = _refused(
        lambda: resolve(Connection("c", kind, path), confinement, {})  # type: ignore[arg-type]
    )
    assert error.refusals[0].code == "UNPARSEABLE_SOURCE"
    assert _written(error) == (
        "The real path of the import directory that holds the connection's file is not Unicode "
        "text: x.db"
    )
    shown = error.refusals[0].model_dump_json()
    assert str(tmp_path) not in shown
    assert "r\\\\udcff" not in shown


# --- The long cell next to a column whose name is not text (D397, D398) -----------------------


def test_a_long_cell_beside_a_column_whose_name_is_not_text_is_cut_between_escapes(
    tmp_path: Path,
) -> None:
    column = "a" * 195 + "\ufffe"
    path = tmp_path / "l.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(f'CREATE TABLE t ("{column}" TEXT)')
        connection.execute("INSERT INTO t VALUES (?)", ("x" * 131_073,))
    connection.close()
    confinement = Confinement.of(tmp_path)
    error = _refused(
        lambda: DatabaseImporter().import_database(
            resolve(Connection("c", "sqlite", path), confinement, {}), _options(confinement)
        )
    )
    tokens = [s for s in error.refusals[0].message if isinstance(s, DataSegment)]
    assert DataSegment(data="a" * 195, truncated=True) in tokens


# --- A workbook's duration a timedelta cannot hold, through the worker (D225) ------------------


def test_a_duration_too_long_for_a_timedelta_is_refused_naming_the_library_class(
    tmp_path: Path,
) -> None:
    built = zipfile.ZipFile(io.BytesIO(build_xlsx([("S", [["h"], [date(2020, 1, 1)]])])))
    parts = {info.filename: built.read(info) for info in built.infolist()}
    sheet = parts["xl/worksheets/sheet1.xml"].decode()
    parts["xl/worksheets/sheet1.xml"] = re.sub(
        r'<c r="A2" s="\d+"><v>[^<]*</v>', '<c r="A2" s="4"><v>1e15</v>', sheet
    ).encode()
    written = io.BytesIO()
    with zipfile.ZipFile(written, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    (tmp_path / "w.xlsx").write_bytes(written.getvalue())
    confinement = Confinement.of(tmp_path)
    error = _refused(
        lambda: FileImporter().import_source(
            confinement.confine(tmp_path / "w.xlsx"), _options(confinement)
        )
    )
    assert error.refusals[0].code == "UNPARSEABLE_SOURCE"
    assert _written(error) == "In w.xlsx: The workbook cannot be read (OverflowError)"


# --- A Parquet file's names are refused as they are read, its cells later (D397, D398) ----------


def _parquet(table: pa.Table, before: bytes, after: bytes) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, store_schema=False, use_dictionary=False, compression="none")
    content = buffer.getvalue()
    assert content.count(before) >= 1
    return content.replace(before, after)


def _import_parquet(tmp_path: Path, content: bytes, *, checked: bool = True) -> ImportRefused:
    (tmp_path / "p.parquet").write_bytes(content)
    confinement = Confinement.of(tmp_path)
    return _refused(
        lambda: FileImporter().import_source(
            confinement.confine(tmp_path / "p.parquet"), _options(confinement)
        ),
        checked=checked,
    )


def test_a_parquet_name_that_is_not_utf8_is_refused_naming_it(tmp_path: Path) -> None:
    content = _parquet(pa.table({"QQQQ": ["v"]}), b"QQQQ", b"Q\xffQQ")
    error = _import_parquet(tmp_path, content)
    assert _written(error) == (
        "In p.parquet: Not a Parquet file that can be read: a column's or a field's name is not "
        "UTF-8: Q\\udcffQQ"
    )


def test_a_parquet_cell_that_is_not_utf8_is_refused_by_the_library_s_class(
    tmp_path: Path,
) -> None:
    content = _parquet(pa.table({"c": ["vWWWv"]}), b"vWWWv", b"v\xffWWv")
    error = _import_parquet(tmp_path, content, checked=False)
    assert "reading it raised " in _written(error)
    assert "UnicodeDecodeError" in [
        s.data for s in error.refusals[0].message if isinstance(s, DataSegment)
    ]


# --- A zip whose folder's name is not text; names that are text import ------------------------


def test_a_zip_member_in_a_folder_whose_name_is_not_text_is_read_by_its_own_name(
    tmp_path: Path,
) -> None:
    (tmp_path / "s.zip").write_bytes(
        build_zip([Entry("d\uffff/x.csv", b"a\n1\n"), Entry(".h\uffff.csv", b"a\n1\n")])
    )
    confinement = Confinement.of(tmp_path)
    result = FileImporter().import_source(
        confinement.confine(tmp_path / "s.zip"), _options(confinement)
    )
    assert list(result.layouts) == ["x"]
    [note] = result.notes
    check_shown(note.message)
    assert [s.data for s in note.message if isinstance(s, DataSegment)] == [".h\\uffff.csv"]


def _large_file_refused(root: Path, name: bytes) -> ImportRefused:
    """The refusal of a directory of one file of ``name``, over ``import_bytes``."""
    root.mkdir()
    with open(os.path.join(os.fsencode(root), name), "wb") as file:
        file.write(b"a\n" + b"1\n" * 100)
    confinement = Confinement.of(root)
    options = ImportOptions(
        dataset="d", reader=confinement, limits=ImportLimits(import_bytes=64), at=AT
    )
    return _refused(lambda: FileImporter().import_source(confinement.confine(root), options))


def test_a_file_that_is_too_large_and_whose_name_is_not_text_is_refused_by_its_name(
    tmp_path: Path,
) -> None:
    """The listing refuses a name before the file is read (D398's order), where the read would
    refuse the size: the pair that shows which comes first. The twin shows the size refuses."""
    twin = _large_file_refused(tmp_path / "text", b"a.csv")
    assert twin.refusals[0].code == "LIMIT_EXCEEDED"
    error = _large_file_refused(tmp_path / "bytes", b"a\xff.csv")
    assert error.refusals[0].code == "UNPARSEABLE_SOURCE"
    assert _written(error) == (
        "A file's name is not Unicode text (a lone surrogate or a noncharacter): a\\udcff.csv"
    )


def _import_csv(tmp_path: Path, name: str, content: bytes) -> ImportResult:
    (tmp_path / name).write_bytes(content)
    confinement = Confinement.of(tmp_path)
    return FileImporter().import_source(confinement.confine(tmp_path / name), _options(confinement))


def test_names_with_nul_and_control_characters_are_text_and_import(tmp_path: Path) -> None:
    result = _import_csv(tmp_path, "c.csv", "a\0b,c\x85d\n1,2\n".encode())
    [layout] = result.layouts.values()
    assert [original for _, original in layout.columns] == ["a\0b", "c\x85d"]
    buffer = io.BytesIO()
    pq.write_table(pa.table({"a\x85b": ["v"]}), buffer)
    (tmp_path / "p.parquet").write_bytes(buffer.getvalue())
    confinement = Confinement.of(tmp_path)
    found = FileImporter().import_source(
        confinement.confine(tmp_path / "p.parquet"), _options(confinement)
    )
    [layout] = found.layouts.values()
    assert [original for _, original in layout.columns] == ["a\x85b"]


# --- A name both too long and not text: its length is refused first (n7) -----------------------


def test_a_sqlite_cell_in_bytes_that_are_not_utf8_is_still_refused_by_the_library_s_class(
    tmp_path: Path,
) -> None:
    """Only the catalogue is read as surrogateescape decodes it; a cell is read strictly, as D397
    has it, so a cell never becomes a string with a lone surrogate in it."""
    path = tmp_path / "c.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE t (a TEXT)")
        connection.execute("INSERT INTO t VALUES (CAST(X'76FF' AS TEXT))")
    connection.close()
    confinement = Confinement.of(tmp_path)
    error = _refused(
        lambda: DatabaseImporter().import_database(
            resolve(Connection("c", "sqlite", path), confinement, {}), _options(confinement)
        )
    )
    assert error.refusals[0].code == "UNPARSEABLE_SOURCE"
    assert _written(error) == "The table t cannot be read (OperationalError)"


_CATALOGUE_AS_NOT_TEXT = {
    "a name stored as a blob of UTF-8": (
        "UPDATE sqlite_schema SET name = CAST(name AS BLOB)",
        "TypeError",
    ),
    "a name stored as a blob that is not UTF-8": (
        "UPDATE sqlite_schema SET name = CAST(name AS BLOB) || X'FF'",
        "UnicodeDecodeError",
    ),
    "a name stored as a number": ("UPDATE sqlite_schema SET name = 7", "DatabaseError"),
    "a table's sql stored as a blob": (
        "UPDATE sqlite_schema SET sql = CAST(sql AS BLOB)",
        "TypeError",
    ),
}
"""What SQLite lets a client write into its catalogue, which holds a name as text only by its
own convention, and the class the refusal names."""


@pytest.mark.parametrize(
    ("update", "library_class"), _CATALOGUE_AS_NOT_TEXT.values(), ids=list(_CATALOGUE_AS_NOT_TEXT)
)
def test_a_catalogue_entry_that_is_not_text_stays_cannot_be_read_naming_a_class(
    tmp_path: Path, update: str, library_class: str
) -> None:
    """D398's stated residual: a catalogue SQLite holds as a blob or a number, or rejects as
    malformed, is not read as text, so the database is "cannot be read", naming the library's
    class and never a name: ``TypeError`` where the catalogue's names are sorted together
    (``snapshot``), ``DatabaseError``, or ``UnicodeDecodeError`` where SQLite's own message
    cannot be decoded. A change of any of these is a change of D398's sentence."""
    path = tmp_path / "c.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE t (a TEXT)")
    connection.close()
    edited = sqlite3.connect(path)
    edited.execute("PRAGMA writable_schema = ON")
    edited.execute(update + " WHERE name = 't'")
    edited.commit()
    edited.close()
    confinement = Confinement.of(tmp_path)
    error = _refused(
        lambda: DatabaseImporter().import_database(
            resolve(Connection("c", "sqlite", path), confinement, {}), _options(confinement)
        ),
        checked=False,
    )
    assert error.refusals[0].code == "UNPARSEABLE_SOURCE"
    assert _written(error) == f"The database cannot be read ({library_class})"


def _sqlite_of_two_tables(path: Path, second: str) -> None:
    """A file of tables ``a`` and a second, named ``second``: as bytes (``text`` encoded with
    ``surrogateescape``) in the catalogue, written as SQLite's C API would."""
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE a (x INT)")
        connection.execute("CREATE TABLE bQQ (x INT)")
    connection.close()
    raw = ("b" + second).encode("utf-8", "surrogateescape")
    patched = sqlite3.connect(path)
    patched.text_factory = bytes
    patched.execute("PRAGMA writable_schema = ON")
    for rowid, name, sql in patched.execute(
        "SELECT rowid, name, sql FROM sqlite_schema WHERE name = 'bQQ'"
    ).fetchall():
        patched.execute(
            "UPDATE sqlite_schema SET name = CAST(? AS TEXT), tbl_name = CAST(? AS TEXT), "
            "sql = CAST(? AS TEXT) WHERE rowid = ?",
            [raw, raw, sql.replace(name, raw), rowid],
        )
    patched.commit()
    patched.close()


@pytest.mark.parametrize("second", ["\ufffe", "\udcff"], ids=["noncharacter", "bytes"])
def test_a_database_with_too_many_tables_one_of_them_not_text_is_refused_for_the_limit(
    tmp_path: Path, second: str
) -> None:
    """The table limit is checked before any name is: it counts the tables listed, a name that is
    not text among them, and refuses first (D398's order for a database). The twin shows the
    name alone is refused."""
    confinement = Confinement.of(tmp_path)

    def refused(limits: ImportLimits) -> ImportRefused:
        path = tmp_path / f"t{limits.import_tables}.sqlite"
        _sqlite_of_two_tables(path, second)
        options = ImportOptions(dataset="d", reader=confinement, limits=limits, at=AT)
        return _refused(
            lambda: DatabaseImporter().import_database(
                resolve(Connection("c", "sqlite", path), confinement, {}), options
            )
        )

    error = refused(ImportLimits(import_tables=1))
    assert error.refusals[0].code == "LIMIT_EXCEEDED"
    assert _written(error) == "The database has more than 1 tables"
    twin = refused(ImportLimits(import_tables=2))
    assert twin.refusals[0].code == "UNPARSEABLE_SOURCE"
    assert _written(twin).startswith("A table's name is not Unicode text")


def test_a_database_name_too_long_and_not_text_is_refused_for_its_length(tmp_path: Path) -> None:
    path = tmp_path / "n.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(f'CREATE TABLE "{"t" * MAX_STRING}\ufffe" (a INT)')
    connection.close()
    confinement = Confinement.of(tmp_path)
    error = _refused(
        lambda: DatabaseImporter().import_database(
            resolve(Connection("c", "sqlite", path), confinement, {}), _options(confinement)
        )
    )
    assert error.refusals[0].code == "LIMIT_EXCEEDED"
    assert _written(error) == f"A table's name has more than {MAX_STRING} characters"


def test_a_header_too_long_and_not_text_is_refused_for_its_length(tmp_path: Path) -> None:
    (tmp_path / "c.csv").write_bytes(("h" * MAX_STRING + "\ufffe,b\n1,2\n").encode())
    confinement = Confinement.of(tmp_path)
    error = _refused(
        lambda: FileImporter().import_source(
            confinement.confine(tmp_path / "c.csv"), _options(confinement)
        )
    )
    assert error.refusals[0].code == "LIMIT_EXCEEDED"
    assert _written(error) == f"In c.csv: A column's name has more than {MAX_STRING} characters"
