"""Detecting how a text file is read (SPEC §5.3, §13.1, D224)."""

import codecs

import pytest

from aibi.core.importers.detect import detect
from aibi.core.importers.errors import ImportRefused
from aibi.core.store.sources import TooManyCells


@pytest.mark.parametrize(
    ("delimiter", "expected_format"),
    [(",", "csv"), (";", "csv"), ("|", "csv"), ("\t", "tsv")],
)
def test_each_delimiter_is_detected(delimiter: str, expected_format: str) -> None:
    rows = [["id", "name", "size"], ["1", "a", "10"], ["2", "b", "20"]]
    data = "\n".join(delimiter.join(row) for row in rows).encode()
    detected = detect(data, extension="csv")
    assert detected.settings.delimiter == delimiter
    assert detected.settings.format == expected_format
    assert detected.parsed.names == ("id", "name", "size")


def test_quoted_delimiters_and_line_breaks_do_not_mislead_it() -> None:
    data = b'id,note\n1,"a; b; c"\n2,"two\nlines"\n3,"x|y"\n'
    detected = detect(data, extension="csv")
    assert detected.settings.delimiter == ","
    assert [row[1] for row in detected.parsed.rows] == ["a; b; c", "two\nlines", "x|y"]


def test_a_tsv_file_tries_the_tab_first() -> None:
    data = b"a\tb,c\n1\t2,3\n"
    assert detect(data, extension="tsv").settings.delimiter == "\t"
    assert detect(data, extension="csv").settings.delimiter == ","


def test_a_preamble_becomes_skip_rows() -> None:
    data = b"# exported\n\nlibrary report\nid,name\n1,a\n2,b\n"
    detected = detect(data, extension="csv")
    assert (detected.settings.skip_rows, detected.settings.header_row) == (3, 0)
    assert detected.parsed.names == ("id", "name")
    assert "3 leading lines before the header skipped" in detected.evidence


def test_a_comment_with_the_header_s_field_count_is_skipped_but_a_hash_header_is_kept() -> None:
    commented = detect(b"# columns: id, name\n## v2\n#\nid,name\n1,a\n2,b\n", extension="csv")
    assert commented.settings.skip_rows == 3
    assert commented.parsed.names == ("id", "name")
    hashed = detect(b"#id,name\n1,a\n2,b\n", extension="csv")
    assert hashed.settings.skip_rows == 0
    assert hashed.parsed.names == ("#id", "name")


def test_a_text_file_past_its_cells_raises_while_it_is_parsed() -> None:
    with pytest.raises(TooManyCells):
        detect(b"id,name\n" + b"1,a\n" * 50, extension="csv", max_cells=99)
    assert len(detect(b"id,name\n" + b"1,a\n" * 50, extension="csv", max_cells=100).parsed.rows)


def test_a_file_of_one_column_is_read_with_a_comma() -> None:
    detected = detect(b"id\n1\n2\n", extension="txt")
    assert (detected.settings.delimiter, detected.settings.format) == (",", "csv")
    assert "one column" in detected.evidence


@pytest.mark.parametrize(
    ("data", "encoding"),
    [
        ("id,title\n1,Café\n".encode(), "utf-8"),
        (codecs.BOM_UTF8 + "id,title\n1,Café\n".encode(), "utf-8-sig"),
        ("id,title\n1,Café\n".encode("utf-16"), "utf-16"),
        ("id,title\n1,Café – bar\n".encode("cp1252"), "cp1252"),
        (b"id,title\n1,\x81\xe9\n", "latin-1"),
    ],
)
def test_the_encoding_is_detected(data: bytes, encoding: str) -> None:
    detected = detect(data, extension="csv")
    assert detected.settings.encoding == encoding
    assert detected.parsed.names == ("id", "title")
    assert str(detected.parsed.rows[0][1]).startswith(("Caf", "\x81"))


def test_a_file_with_no_record_is_unparseable() -> None:
    with pytest.raises(ImportRefused) as refused:
        detect(b"\n\n", extension="csv")
    assert [r.code for r in refused.value.refusals] == ["UNPARSEABLE_SOURCE"]


def test_a_ragged_file_is_unparseable_and_names_the_line() -> None:
    with pytest.raises(ImportRefused) as refused:
        detect(b"a,b\n1,2\n3,4\n5,6,7\n", extension="csv")
    [refusal] = refused.value.refusals
    assert refusal.code == "UNPARSEABLE_SOURCE"
    assert "line 4" in refusal.message[0].model_dump()["text"]


def test_the_evidence_names_the_rules_and_no_value() -> None:
    detected = detect(b"id;secret\n1;hunter2\n2;swordfish\n", extension="csv")
    assert "semicolon" in detected.evidence
    assert "3 of 3 sampled records with 2 fields" in detected.evidence
    assert "hunter2" not in detected.evidence
    assert "secret" not in detected.evidence
