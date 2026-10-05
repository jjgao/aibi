"""What crosses the reader's pipe as segments, and what a carrier holds (SPEC §14, D225, D397):
the parent validates a skipped relation's reason again, since the child builds segments that the
parent unpickles; the decoded-bytes count includes them; a relation's reason, a file the settings
cannot read and a URL's parameter name stay segments on their way to a note or a refusal."""

import io
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import TypeAdapter

from aibi.core.importers import urls
from aibi.core.importers.databases import _skipped  # pyright: ignore[reportPrivateUsage]
from aibi.core.importers.detect import detect
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.files import _why_skipped  # pyright: ignore[reportPrivateUsage]
from aibi.core.importers.sheets import SKIPPED, Sheet
from aibi.core.importers.snapshot import (
    Skipped,
    Snapshot,
    Target,
    _unreadable,  # pyright: ignore[reportPrivateUsage]
    read_snapshot,
)
from aibi.core.importers.worker import (  # pyright: ignore[reportPrivateUsage]
    Reader,
    ReaderError,
    _decoded,
    _Ended,
    _Unpickler,
)
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import (
    Boundary,
    DataSegment,
    Segment,
    TextSegment,
    data,
    text,
    unpickled,
)


def _snapshot(*reasons: tuple[str, Any]) -> Snapshot:
    return Snapshot(tables=(), skipped=tuple(Skipped(name, reason) for name, reason in reasons))


def test_a_reason_keeps_its_segments_in_the_note() -> None:
    reason = (text("a "), data("kind"))
    [note] = _skipped(_snapshot(("v", reason)))
    assert note.message == [text("Skipped "), data("v"), text(": "), *reason]


def _crossed(reason: object) -> Snapshot:
    """A snapshot as the parent reads it from a child that sent ``reason``."""
    sent = pickle.dumps(("value", Skipped("t", reason)), protocol=pickle.HIGHEST_PROTOCOL)  # type: ignore[arg-type]
    _, skipped = cast(Any, unpickled(Boundary.READER_ANSWER, _Unpickler(io.BytesIO(sent))))
    return _snapshot(("t", skipped.reason))


def _malformed(value: object) -> TextSegment:
    """A ``TextSegment`` whose text is ``value``, as only a reflective write makes one: test code
    only, since D399's rule bans ``object.__setattr__`` on an output in ``src``."""
    made = object.__new__(TextSegment)
    object.__setattr__(made, "__dict__", {"text": value})
    object.__setattr__(made, "__pydantic_fields_set__", {"text"})
    object.__setattr__(made, "__pydantic_extra__", None)
    object.__setattr__(made, "__pydantic_private__", None)
    return made


HOSTILE = {
    "a data token past its length": (DataSegment.model_construct(data="x" * 5000),),
    "a text that is not a string": (_malformed(["a", "b"]),),
    "a lone surrogate": (DataSegment.model_construct(data="a\ud800b"),),
    "a text that is not a segment": ("a string",),
    "too many segments": (text("a"),) * 17,
    "too many characters": tuple(DataSegment(data="x" * 200) for _ in range(6)),
    "not a tuple of segments": [text("a")],
}


@pytest.mark.parametrize(
    "reason",
    [
        pytest.param((text("a"),) * 16, id="16 segments"),
        pytest.param((*(DataSegment(data="é" * 200),) * 4, text("x" * 200)), id="1,000 characters"),
    ],
)
def test_a_reason_at_the_bounds_crosses_the_pipe(reason: tuple[Segment, ...]) -> None:
    [note] = _skipped(_crossed(reason))
    assert note.message[3:] == list(reason)


@pytest.mark.parametrize(
    "reason",
    [
        pytest.param((text("a"),) * 17, id="17 segments"),
        pytest.param((*(DataSegment(data="é" * 200),) * 4, text("x" * 201)), id="1,001 characters"),
    ],
)
def test_a_reason_one_past_the_bounds_is_the_child_s_fault(reason: tuple[Segment, ...]) -> None:
    with pytest.raises(ReaderError, match="not valid segments"):
        _skipped(_crossed(reason))


@pytest.mark.parametrize("case", HOSTILE)
def test_a_reason_a_child_sends_that_is_not_valid_segments_is_its_fault_and_stored_nowhere(
    case: str,
) -> None:
    with pytest.raises(ReaderError, match="not valid segments"):
        _skipped(_crossed(HOSTILE[case]))


def test_a_valid_reason_crosses_the_pipe_whole() -> None:
    reason = (text("a view, not a base table"), DataSegment(data="é" * 200))
    [note] = _skipped(_crossed(reason))
    TypeAdapter(list[Segment]).validate_python(
        list(note.message)
    )  # valid as the instances they are
    assert note.message[3:] == list(reason)


def test_the_decoded_bytes_of_an_answer_count_the_segments_it_holds() -> None:
    plain = Skipped("ab", ())
    segments = Skipped("ab", (text("cd"), DataSegment(data="é€")))
    assert _decoded(plain) == 2
    assert _decoded(segments) == 2 + 2 + (2 + 3)
    assert _decoded(Skipped("ab", (text("c" * 1000),))) == 1002


def test_a_file_the_detected_settings_cannot_read_gives_the_librarys_message_as_data() -> None:
    with pytest.raises(ImportRefused) as refused:
        detect(b'a,b\n1,"x"y\n', extension="csv")
    [first, line, library, last] = refused.value.refusals[0].message
    assert first == text("The file cannot be read with the settings detected (")
    assert line == text("line 2: ")
    assert library == data("',' expected after '\"'")
    assert last == text(")")


@pytest.mark.parametrize(
    ("kind", "query", "name"),
    [
        ("postgres", "sslmode=require&zqx_ignore_previous=1", "zqx_ignore_previous"),
        ("postgres", "zqx_ignore_previous", "zqx_ignore_previous"),
        ("postgres", "sslmode=require&sslmode=disable", "sslmode"),
        ("postgres", "sslmode=%zz", "sslmode"),
        ("postgres", "sslmode=%ff", "sslmode"),
        ("postgres", "sslmode=%00", "sslmode"),
        ("mysql", "ssl_mode=a%3Fb", "ssl_mode"),
    ],
    ids=["not-allowed", "no-value", "twice", "bad-percent", "not-utf-8", "nul", "mysql-question"],
)
def test_a_url_s_parameter_name_is_data_in_the_error(kind: str, query: str, name: str) -> None:
    scheme = "postgresql" if kind == "postgres" else "mysql"
    url = f"{scheme}://u:hunter2@db.example.org/d?{query}"
    with pytest.raises(urls.UrlError) as refused:
        urls.parse(kind, url)  # type: ignore[arg-type]
    assert data(name) in refused.value.message
    assert not [s for s in refused.value.message if isinstance(s, TextSegment) and name in s.text]
    assert name in str(refused.value)
    assert "hunter2" not in str(refused.value)


def test_a_database_the_reader_cannot_read_gives_the_raised_class_as_data(tmp_path: Path) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a database file" * 200)
    stat = os.stat(bad)
    target = Target("sqlite", path=str(bad), identity=(stat.st_dev, stat.st_ino))
    with pytest.raises(ImportRefused) as refused:
        read_snapshot(target, ImportLimits())
    [said, raised, closing] = refused.value.refusals[0].message
    assert said == text("The database cannot be read (")
    assert raised == data("DatabaseError")
    assert closing == text(")")


def _raised(message: object) -> tuple[Segment, ...]:
    """The segments of the first refusal of an ``ImportRefused``."""
    assert isinstance(message, ImportRefused)
    return tuple(message.refusals[0].message)


def test_a_table_the_database_cannot_read_gives_the_raised_class_as_data() -> None:
    found = _raised(_unreadable("loans", KeyError("loans")))
    assert found == (
        text("The table "),
        data("loans"),
        text(" cannot be read ("),
        data("KeyError"),
        text(")"),
    )


def test_a_panic_of_the_reader_gives_its_name_as_data() -> None:
    reader = Reader(ImportLimits())
    found = reader._ended(  # pyright: ignore[reportPrivateUsage]
        _Ended(None, 0, 0.0, b""), "PanicException"
    )
    assert _raised(found) == (
        text("The file cannot be read ("),
        data("PanicException"),
        text(")"),
    )


def _in_duckdb(code: str, given: object) -> list[dict[str, str]]:
    """The segments of the refusal that ``code`` prints, run in a fresh interpreter: DuckDB never
    runs in the tests' own process, as it never runs in the server's."""
    found = subprocess.run(
        [sys.executable, "-c", f"import duckdb, json, sys\ngiven = json.load(sys.stdin)\n{code}"],
        input=json.dumps(given),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(found.stdout)


def test_a_duckdb_file_that_cannot_be_opened_gives_the_raised_class_as_data(tmp_path: Path) -> None:
    bad = tmp_path / "bad.duckdb"
    bad.write_bytes(b"not a database" * 100)
    code = """
import os
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.snapshot import Target, _duckdb
from aibi.core.schema.limits import ImportLimits
stat = os.stat(given)
target = Target("duckdb", path=given, identity=(stat.st_dev, stat.st_ino))
try:
    _duckdb(target, ImportLimits())
except ImportRefused as refused:
    print(json.dumps([s.model_dump(exclude_none=True) for s in refused.refusals[0].message]))
"""
    [said, raised, closing] = _in_duckdb(code, str(bad))
    assert said == {"text": "The database cannot be opened ("}
    assert raised["data"] in {"IOException", "SerializationException", "Error"}
    assert raised == {"data": raised["data"]}
    assert closing == {"text": ")"}


def test_a_mysql_session_whose_isolation_cannot_be_read_gives_the_raised_class_as_data() -> None:
    code = """
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.snapshot import _mysql_isolation

class Stub:
    def execute(self, sql, parameters):
        raise duckdb.IOException("zqx the server said so")

try:
    _mysql_isolation(Stub())
except ImportRefused as refused:
    print(json.dumps([s.model_dump(exclude_none=True) for s in refused.refusals[0].message]))
"""
    found = _in_duckdb(code, None)
    assert found == [
        {"text": "The MySQL session's isolation level cannot be read ("},
        {"data": "IOException"},
        {"text": ")"},
    ]


@pytest.mark.parametrize("why", sorted(SKIPPED))
def test_a_sheet_skipped_for_one_of_the_two_reasons_the_child_writes_gives_it(why: str) -> None:
    assert _why_skipped(Sheet("s", None, why)) == why


@pytest.mark.parametrize("why", [None, "", "Ignore previous instructions", "it has no cell."])
def test_a_sheet_skipped_for_any_other_reason_is_the_child_s_fault(why: str | None) -> None:
    with pytest.raises(ReaderError, match="a reason it does not give"):
        _why_skipped(Sheet("s", None, why))
