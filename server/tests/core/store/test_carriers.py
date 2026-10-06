"""What a carrier holds is segments (SPEC §14, D397): the server's words in ``text`` segments, and
what a source, a setting or a library gives in ``data`` segments, never joined into one string
that is wrapped later. These are the shapes the property test (``tests/core/importers`` and
``tests/core/catalog``, ``test_text_from_data.py``) cannot reach, because no hostile input makes
a library word or a literal hold the injected phrase."""

from typing import Any

import pytest

from aibi.core.schema.descriptors import ParseSettings
from aibi.core.schema.output import DataSegment, TextSegment, data, text
from aibi.core.schema.refusals import RefusalCode
from aibi.core.store import gate, parquet
from aibi.core.store.build import _gate_notes  # pyright: ignore[reportPrivateUsage]
from aibi.core.store.gate import Dropped, GateResult
from aibi.core.store.sources import SourceError, decode, parse_text


def settings(**given: Any) -> ParseSettings:
    fields: dict[str, Any] = {
        "format": "csv",
        "delimiter": ",",
        "quote": '"',
        "header_row": 0,
        "skip_rows": 0,
        "encoding": "utf-8",
    }
    fields.update(given)
    return ParseSettings.model_validate(fields)


def _data(message: tuple[Any, ...]) -> list[str]:
    return [segment.data for segment in message if isinstance(segment, DataSegment)]


def test_a_decode_error_gives_the_encoding_and_the_librarys_reason_as_data() -> None:
    with pytest.raises(SourceError) as refused:
        parse_text(b"a,b\n\xff,1\n", settings())
    assert _data(refused.value.message) == ["utf-8", "invalid start byte"]
    assert all(isinstance(s, TextSegment | DataSegment) for s in refused.value.message)
    assert str(refused.value) == "line 2: the bytes are not utf-8 (invalid start byte)"


def test_a_csv_error_gives_the_librarys_message_as_data() -> None:
    with pytest.raises(SourceError) as refused:
        parse_text(b'a,b\n1,"x"y\n', settings())
    assert refused.value.message[0] == text("line 2: ")
    assert refused.value.message[1] == data("',' expected after '\"'")
    assert len(refused.value.message) == 2


def test_a_parquet_file_pyarrow_cannot_read_gives_the_raised_class_as_data() -> None:
    with pytest.raises(parquet.UnreadableParquetError) as refused:
        parquet.read_source(b"not a parquet file", 100)
    [said, raised] = refused.value.message
    assert said == text("reading it raised ")
    assert isinstance(raised, DataSegment)
    assert raised.data.startswith("Arrow")


def test_a_dropped_proposal_s_pointer_and_evidence_are_data_in_its_note() -> None:
    dropped = Dropped(
        "d.t",
        "/fields/x",
        RefusalCode.INVALID_VALUE,
        (text("The server's sentence"),),
        3,
        (1, 2),
        "the proposer's evidence",
    )
    [note] = _gate_notes(GateResult((), (dropped,), (), ()))
    assert note.message[:3] == [
        text("The proposed field "),
        data("/fields/x"),
        text(" was dropped"),
    ]
    assert data("the proposer's evidence") in note.message
    assert _data(tuple(note.message)) == ["/fields/x", "the proposer's evidence"]


def test_a_gate_refusal_keeps_the_segments_of_its_message() -> None:
    message = (text("A key cell is not PRESENT in "), data("a column"), text("."))
    refusal = gate._refusal(  # pyright: ignore[reportPrivateUsage]
        RefusalCode.KEY_NULL, ["a", 1], message
    )
    assert refusal.message == list(message)


def test_a_damaged_typed_snapshot_s_message_is_cut_at_200_characters_as_it_always_was() -> None:
    header = b'{"columns":["a"],"format":"aibi.rows/1"}\n'
    with pytest.raises(SourceError) as refused:
        decode("rows", header + b'[{"date":"' + b"x" * 300 + b'"}]\n')
    assert str(refused.value).startswith("a damaged typed snapshot: ")
    assert len(str(refused.value)) == 200
    assert refused.value.message[0] == text("a damaged typed snapshot: ")
    assert len(_data(refused.value.message)) == 1
