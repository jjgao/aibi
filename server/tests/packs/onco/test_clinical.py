"""Reading a clinical file (M4.2a-1b, D409's clinical files): every refusal with its place, the
order of the checks, the line splitter against the core's parse, the limits at their edges, the
strip class, totality and the sentinel.

The pack's reader is ``clinical.read``; the files are built here (``file``), and the outcome of a
patient and a sample file read together is ``outcome``: ``ok`` or the refusal's code and place.
"""

import ast
import gc
import importlib
import re
import tracemalloc
from collections.abc import Callable, Sequence
from types import ModuleType
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import DataSegment, TextSegment
from aibi.core.schema.pack_api import Refused
from aibi.core.store.sources import SourceError, parse_text

MODULE = "aibi.packs.onco.clinical"
CONTROLS = [chr(c) for c in (*range(0x20), 0x7F) if chr(c) not in "\t\n\r"]
STRIP_CLASS = [chr(c) for c in range(0x110000) if chr(c).isspace() and c > 0x20]
"""The code points Python's ``str.strip()`` removes and Java's trim keeps: computed here."""


def file(
    names: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    types: Sequence[str] | None = None,
    labels: Sequence[str] | None = None,
    defs: Sequence[str] | None = None,
    prio: Sequence[str] | None = None,
    eol: str = "\n",
) -> bytes:
    n = len(names)
    lines = [
        "#" + "\t".join(labels or ["L"] * n),
        "#" + "\t".join(defs or ["D"] * n),
        "#" + "\t".join(types or ["STRING"] * n),
        "#" + "\t".join(prio or ["1"] * n),
        "\t".join(names),
        *("\t".join(row) for row in rows),
    ]
    return (eol.join(lines) + eol).encode("utf-8", "surrogatepass")


PATIENT = file(["PATIENT_ID", "SEX"], [["P1", "F"], ["P2", "M"]])
SAMPLE = file(
    ["PATIENT_ID", "SAMPLE_ID", "TMB"], [["P1", "S1", "1"], ["P2", "S2", "2"]],
    types=["STRING", "STRING", "NUMBER"],
)  # fmt: skip


@pytest.fixture
def clinical() -> ModuleType:
    return importlib.import_module(MODULE)


Outcome = Callable[..., str]


@pytest.fixture
def outcome(clinical: ModuleType) -> Outcome:
    """``ok``, or ``CODE line L, column C`` of the refusal (``-`` where there is none)."""

    def found(
        patient: bytes | None = PATIENT, sample: bytes = SAMPLE, limits: ImportLimits | None = None
    ) -> str:
        limits = limits or ImportLimits()
        try:
            read = None
            if patient is not None:
                read = clinical.read(patient, "p.txt", "patient", limits)
            cells = read.cells if read is not None else 0
            clinical.read(sample, "s.txt", "sample", limits, cells_before=cells, patient=read)
        except Refused as error:
            if type(error) is not Refused:
                return "a subclass of Refused"
            (refusal,) = error.refusals
            said = "".join(s.text for s in refusal.message if isinstance(s, TextSegment))
            line = re.search(r"line (\d+)", said)
            column = re.search(r"column (\d+)", said)
            return (
                f"{str(refusal.code).removeprefix('onco.')} "
                f"line {line[1] if line else '-'}, column {column[1] if column else '-'}"
            )
        return "ok"

    return found


def patient_with(name: str, cell: str, kind: str = "STRING") -> bytes:
    """A patient file whose column ``name`` (of the datatype row ``kind``) holds ``cell`` on line 6
    and an empty cell on line 7."""
    return file(["PATIENT_ID", name], [["P1", cell], ["P2", ""]], types=["STRING", kind])


# --- Lines and line ends ---------------------------------------------------------------------


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize(
    "line",
    ["", "\t", " ", "\xa0", "\t \t", " \u3000"],
    ids=["empty", "tab", "space", "nbsp", "tabs", "ideo"],
)
def test_a_blank_line_is_refused_at_its_line(outcome: Outcome, eol: str, line: str) -> None:
    """In the patient and the sample file, under each line end: the validator's blank line
    (V:543), every field empty by ``str.strip()``."""
    names = ["PATIENT_ID", "SEX"]
    body = file(names, [["P1", "F"]], eol=eol) + (line + eol).encode() + f"P2\tM{eol}".encode()
    assert outcome(patient=body) == "BLANK_LINE line 7, column -"
    sample = file(["PATIENT_ID", "SAMPLE_ID"], [["P1", "S1"]], eol=eol) + (line + eol).encode()
    assert outcome(sample=sample) == "BLANK_LINE line 7, column -"


def test_one_final_line_end_is_no_line_and_two_are_a_blank_line(outcome: Outcome) -> None:
    assert outcome(patient=PATIENT) == "ok"
    assert outcome(patient=PATIENT + b"\n") == "BLANK_LINE line 8, column -"
    assert outcome(patient=PATIENT.rstrip(b"\n")) == "ok"
    assert outcome(patient=PATIENT.replace(b"\n", b"\r")) == "ok"
    assert outcome(patient=PATIENT.replace(b"\n", b"\r\n")) == "ok"
    assert outcome(patient=PATIENT.replace(b"\n", b"\r\n", 2)) == "ok"  # mixed: exit 1
    assert outcome(patient=PATIENT.replace(b"\nP2", b"\r\r\nP2")) == "BLANK_LINE line 7, column -"


def test_a_byte_order_mark_on_line_1_is_read_past_and_a_second_is_content(
    outcome: Outcome,
) -> None:
    bom = "\ufeff".encode()
    assert outcome(sample=bom + SAMPLE) == "ok"
    assert outcome(patient=bom + PATIENT) == "ok"
    assert outcome(patient=bom + bom + PATIENT) == "HEADER_ROWS line 1, column -"
    assert outcome(patient=PATIENT.replace(b"#D", bom + b"#D", 1)) == "HEADER_ROWS line 2, column -"


def test_the_first_header_row_of_a_file_with_a_byte_order_mark_is_read_from_past_it(
    clinical: ModuleType,
) -> None:
    """The mark is read past by an offset: the first label is what follows the ``#``, with
    neither the mark nor the ``#`` in it, as it is without the mark."""
    bom = "\ufeff".encode()
    labels = {}
    for prefix in (b"", bom):
        found = clinical.read(prefix + PATIENT, "p.txt", "patient", ImportLimits())
        labels[prefix] = [column.label for column in found.columns]
    assert labels[bom] == labels[b""] == ["L", "L"]


def test_the_line_splitter_is_the_core_s(clinical: ModuleType) -> None:
    """``\\r\\n``, ``\\n`` and ``\\r`` alone end a line; U+2028, U+2029, U+0085, a vertical tab
    and a form feed do not (``str.splitlines`` would split at them), and a file of lone carriage
    returns has its lines (a binary file's iteration would read it as one)."""
    data = b"a\r\nb\rc\nd\xe2\x80\xa8e\xc2\x85f\r"
    assert [line for _, line in clinical.lines(data)] == [
        b"a",
        b"b",
        b"c",
        "d\u2028e\u0085f".encode(),
    ]
    assert list(clinical.lines(b"")) == []
    assert list(clinical.lines(b"\n")) == [(1, b"")]
    assert list(clinical.lines(b"x\n\n")) == [(1, b"x"), (2, b"")]


# --- Encoding and control characters -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", [b"\xff", b"\xc0\x80", b"\xed\xa0\x80", b"\xe2\x82", b"\xed\xa0\xbd\xed\xb8\x80"]
)
def test_bytes_that_are_not_utf_8_are_refused_at_their_line(outcome: Outcome, raw: bytes) -> None:
    assert outcome(patient=PATIENT.replace(b"\tM", b"\tM" + raw)) == "NOT_UTF8 line 7, column -"
    assert outcome(patient=PATIENT.replace(b"#L\t", b"#L" + raw + b"\t")) == (
        "NOT_UTF8 line 1, column -"
    )


@pytest.mark.parametrize("control", CONTROLS, ids=[f"U{ord(c):04X}" for c in CONTROLS])
def test_each_control_character_is_refused_with_its_place(outcome: Outcome, control: str) -> None:
    assert len(CONTROLS) == 30
    cell = patient_with("SEX", "F" + control + "x")
    assert outcome(patient=cell) == "CONTROL_CHARACTER line 6, column 2"
    header = PATIENT.replace(b"#D\tD", ("#D\tD" + control).encode())
    assert outcome(patient=header) == "CONTROL_CHARACTER line 2, column 2"
    name = file(["PATIENT_ID", "SE" + control + "X"], [["P1", "F"]])
    assert outcome(patient=name) == "CONTROL_CHARACTER line 5, column 2"


@pytest.mark.parametrize("cell", ["F\u0085x", "F\u2028x", "F\u2029x", "F\x80x", "F\u200bx"])
def test_other_code_points_in_a_cell_import(outcome: Outcome, cell: str) -> None:
    assert outcome(patient=patient_with("SEX", cell)) == "ok"


def test_the_classes_are_refused_in_order(outcome: Outcome) -> None:
    """A later line that is not UTF-8 is refused before an earlier control character, which is
    refused before an earlier structure failure, which is before an earlier cell's."""
    bad = file(["PATIENT_ID", "SEX"], [["NA", "F\x01"], ["P2", "M"], ["P3"]])
    assert outcome(patient=bad) == "CONTROL_CHARACTER line 6, column 2"
    assert outcome(patient=bad + b"\xff\n") == "NOT_UTF8 line 9, column -"
    cells_then_row = file(["PATIENT_ID", "SEX"], [["NA", "F"], ["P2", "M"], ["P3"]])
    assert outcome(patient=cells_then_row) == "ROW_WIDTH line 8, column -"
    header_then_control = file(["PATIENT_ID", "Sex"], [["P1", "F"], ["P2", "M\x7f"]])
    assert outcome(patient=header_then_control) == "CONTROL_CHARACTER line 7, column 2"
    pipes = [[f"P{i}", "N" + "|" * (i + 1) + "A"] for i in range(65)]
    assert outcome(patient=file(["PATIENT_ID", "NOTE"], pipes)) == "MISSING_FORM line -, column 2"
    with_cell = file(["PATIENT_ID", "NOTE"], [*pipes, ["P99", "Mar-1"]])
    assert outcome(patient=with_cell) == "EXCEL_DATE line 71, column 2"


# --- The header -----------------------------------------------------------------------------------


def test_the_header_rows(outcome: Outcome) -> None:
    three = b"\n".join(PATIENT.split(b"\n")[1:])
    assert outcome(patient=three) == "HEADER_ROWS line 4, column -"
    assert outcome(patient=b"#x\t#x\n" + PATIENT) == "HEADER_ROWS line 5, column -"
    assert outcome(patient=PATIENT.replace(b"PATIENT_ID\tSEX\n", b"\n")) == (
        "HEADER_ROWS line 5, column -"
    )
    assert outcome(patient=b"#L\n#D\n") == "HEADER_ROWS line -, column -"
    assert outcome(patient=b"") == "HEADER_ROWS line -, column -"
    assert outcome(patient=PATIENT.replace(b"#STRING\tSTRING", b"#STRING")) == (
        "HEADER_ROW_WIDTH line 3, column -"
    )
    assert (
        outcome(patient=PATIENT.replace(b"#D\tD", b"#D\tD\t"))
        == "HEADER_ROW_WIDTH line 2, column -"
    )
    assert (
        outcome(patient=PATIENT.replace(b"#1\t1", b"#\t1\t1"))
        == "HEADER_ROW_WIDTH line 4, column -"
    )


def test_exactly_one_hash_is_stripped(outcome: Outcome) -> None:
    """V:2581 strips one ``#``: a second one is the field's (cells 04, 05 of rv1)."""
    assert outcome(patient=PATIENT.replace(b"#STRING\t", b"##STRING\t")) == (
        "DATATYPE_ROW line 3, column 1"
    )
    assert outcome(patient=PATIENT.replace(b"#1\t", b"##1\t")) == "PRIORITY_ROW line 4, column 1"
    assert outcome(patient=PATIENT.replace(b"#L\t", b"##L\t")) == "ok"


def test_too_many_columns(outcome: Outcome) -> None:
    names = ["PATIENT_ID", *(f"C{i}" for i in range(4))]
    wide = file(names, [["P1", "a", "b", "c", "d"]])
    assert outcome(patient=wide, limits=ImportLimits(table_columns=5)) == "ok"
    assert outcome(patient=wide, limits=ImportLimits(table_columns=4)) == (
        "TOO_MANY_COLUMNS line 5, column -"
    )


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("Sex", "ATTRIBUTE_NAME"),
        ("123", "ATTRIBUTE_NAME"),
        ("", "ATTRIBUTE_NAME"),
        ("A" * 4097, "ATTRIBUTE_NAME"),
        ("A" * 140_000, "ATTRIBUTE_NAME"),
        ("SEX\ufdd0", "ATTRIBUTE_NAME"),
        ("A" * 4096, "ok"),
        ("\xc4GE", "ok"),
        ("SEX 2", "ok"),
        ("#SEX", "ok"),
    ],
)
def test_attribute_names(outcome: Outcome, name: str, want: str) -> None:
    found = outcome(patient=file(["PATIENT_ID", name], [["P1", "x"], ["P2", "y"]]))
    assert found == (want if want == "ok" else f"{want} line 5, column 2")


def test_a_repeated_name_is_refused(outcome: Outcome) -> None:
    assert outcome(patient=file(["PATIENT_ID", "SEX", "SEX"], [["P1", "F", "F"]])) == (
        "DUPLICATE_ATTRIBUTE line 5, column 3"
    )
    twice = file(["PATIENT_ID", "SAMPLE_ID", "SAMPLE_ID"], [["P1", "S1", "S1"]])
    assert outcome(sample=twice) == "DUPLICATE_ATTRIBUTE line 5, column 3"


@pytest.mark.parametrize("row", [0, 1])
def test_rows_1_and_2(outcome: Outcome, row: int) -> None:
    """A null word by ``strip().lower()``, a cap in code points as written (a four-byte character
    is one), a noncharacter; a padded field is kept as written."""
    cap = (4096, 10_000)[row]

    def with_field(value: str) -> str:
        fields: list[list[str] | None] = [None, None]
        fields[row] = ["L", value]
        return outcome(
            patient=file(["PATIENT_ID", "SEX"], [["P1", "F"]], labels=fields[0], defs=fields[1])
        )

    refused = f"TEXT_VALUE line {row + 1}, column 2"
    for value in ["NA", "na ", "\u3000[Pending]", "", " ", "[NULL]", "[Not Applicable]"]:
        assert with_field(value) == refused, value
    for value in [
        "Pending",
        "N/A",
        "[Unknown]",
        " Sex ",
        "x" * cap,
        "x" * (cap - 1) + "\U0001f600",
    ]:
        assert with_field(value) == "ok", value
    for value in ["x" * (cap + 1), "x" * cap + " ", "x\ufffe", "\U0010ffff", "x\ufdd0"]:
        assert with_field(value) == refused, value[:10]


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("STRING", "ok"),
        ("BOOLEAN", "ok"),
        ("string", "DATATYPE_ROW"),
        ("String", "DATATYPE_ROW"),
        ("STRING ", "DATATYPE_ROW"),
        (" STRING", "DATATYPE_ROW"),
        ("\ufeffSTRING", "DATATYPE_ROW"),
        ("NA", "DATATYPE_ROW"),
        ("DATE", "DATATYPE_ROW"),
    ],
)
def test_the_datatype_row_is_exact(outcome: Outcome, value: str, want: str) -> None:
    found = outcome(patient=file(["PATIENT_ID", "FLAG"], [["P1", "TRUE"]], types=["STRING", value]))
    assert found == (want if want == "ok" else f"{want} line 3, column 2")


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("1", "ok"),
        ("-1", "ok"),
        ("0", "ok"),
        ("-0", "ok"),
        ("01", "ok"),
        ("0" * 16 + "1", "ok"),
        ("9" * 15, "ok"),
        ("9" * 16, "PRIORITY_ROW"),
        (str(2**53 - 1), "ok"),
        (str(2**53), "PRIORITY_ROW"),
        ("0" * 17 + "1", "PRIORITY_ROW"),
        ("-2", "PRIORITY_ROW"),
        (" 1", "PRIORITY_ROW"),
        ("1 ", "PRIORITY_ROW"),
        ("+1", "PRIORITY_ROW"),
        ("1_0", "PRIORITY_ROW"),
        ("\u0663", "PRIORITY_ROW"),
        ("\uff11", "PRIORITY_ROW"),
        ("1.0", "PRIORITY_ROW"),
        ("NA", "PRIORITY_ROW"),
        ("", "PRIORITY_ROW"),
    ],
)
def test_the_priority_row(outcome: Outcome, value: str, want: str) -> None:
    found = outcome(patient=file(["PATIENT_ID", "SEX"], [["P1", "F"]], prio=["1", value]))
    assert found == (want if want == "ok" else f"{want} line 4, column 2")


def test_the_header_rows_are_checked_on_a_key_column_too(outcome: Outcome) -> None:
    assert outcome(patient=file(["PATIENT_ID", "SEX"], [["P1", "F"]], labels=["NA", "L"])) == (
        "TEXT_VALUE line 1, column 1"
    )
    assert outcome(patient=file(["PATIENT_ID", "SEX"], [["P1", "F"]], prio=["x", "1"])) == (
        "PRIORITY_ROW line 4, column 1"
    )
    number_key = file(["PATIENT_ID", "SEX"], [["P1", "F"]], types=["NUMBER", "STRING"])
    assert outcome(patient=number_key) == "ok"


def test_predefined_banned_and_both_files(outcome: Outcome) -> None:
    os_months = file(["PATIENT_ID", "OS_MONTHS"], [["P1", "1"]])
    assert outcome(patient=os_months) == "PREDEFINED_ATTRIBUTE line 5, column 2"
    sample_type = file(["PATIENT_ID", "SAMPLE_TYPE"], [["P1", "Primary"]])
    assert outcome(patient=sample_type) == "PREDEFINED_ATTRIBUTE line 5, column 2"
    free = file(
        ["PATIENT_ID", "GLEASON_SCORE", "CANCER_TYPE"],
        [["P1", "7", "x"]],
        types=["STRING", "NUMBER", "STRING"],
    )
    assert outcome(patient=free) == "ok"
    banned = file(
        ["PATIENT_ID", "SAMPLE_ID", "MUTATION_COUNT"],
        [["P1", "S1", "1"]],
        types=["STRING", "STRING", "NUMBER"],
    )
    assert outcome(sample=banned) == "BANNED_ATTRIBUTE line 5, column 3"
    both_p = file(["PATIENT_ID", "FOO"], [["P1", "a"]])
    both_s = file(["PATIENT_ID", "SAMPLE_ID", "FOO"], [["P1", "S1", "a"]])
    assert outcome(patient=both_p, sample=both_s) == "ATTRIBUTE_IN_BOTH_FILES line 5, column 3"
    assert outcome(patient=None, sample=both_s) == "ok"


def test_key_columns(outcome: Outcome) -> None:
    assert outcome(patient=file(["SEX"], [["F"]])) == "NO_KEY_COLUMN line 5, column -"
    assert outcome(sample=file(["PATIENT_ID", "TMB"], [["P1", "1"]])) == (
        "NO_KEY_COLUMN line 5, column -"
    )
    assert outcome(sample=file(["SAMPLE_ID"], [["S1"]])) == "NO_KEY_COLUMN line 5, column -"
    assert outcome(patient=file(["PATIENT_ID", "SAMPLE_ID"], [["P1", "S1"]])) == (
        "SAMPLE_ID_IN_PATIENT_FILE line 5, column 2"
    )
    assert outcome(patient=file(["PATIENT_ID "], [["P1"]])) == "NO_KEY_COLUMN line 5, column -"


# --- Data lines ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "want"),
    [
        ("#P3\tF", "COMMENT_LINE line 8, column -"),
        (" #P3\tF", "COMMENT_LINE line 8, column -"),
        ("\t#x", "COMMENT_LINE line 8, column -"),
        ("PATIENT_ID\tSEX", "REPEATED_HEADER line 8, column -"),
        ("PATIENT_ID\tSEX\textra", "REPEATED_HEADER line 8, column -"),
        ("P3\tF\t", "ROW_WIDTH line 8, column -"),
        ("P3", "ROW_WIDTH line 8, column -"),
        ("P3\tF#", "ok"),
    ],
)
def test_data_lines(outcome: Outcome, line: str, want: str) -> None:
    assert outcome(patient=PATIENT + (line + "\n").encode()) == want


def test_a_field_s_length(outcome: Outcome) -> None:
    long = file(["PATIENT_ID", "NOTE"], [["P1", "a" * 131_072]])
    assert outcome(patient=long) == "ok"
    longer = file(["PATIENT_ID", "NOTE"], [["P1", "a" * 131_073]])
    assert outcome(patient=longer) == "LONG_FIELD line 6, column 2"


def test_cells_against_import_cells_at_the_limit(outcome: Outcome) -> None:
    """The tables' columns times their rows, at least one, summed (``checks._cells``): 2 x 2 in
    the patient file and 3 x 2 in the sample file are 10; a file of no rows counts one row."""
    assert outcome(limits=ImportLimits(import_cells=10)) == "ok"
    assert outcome(limits=ImportLimits(import_cells=11)) == "ok"
    assert outcome(limits=ImportLimits(import_cells=9)) == "TOO_MANY_CELLS line 7, column -"
    assert outcome(limits=ImportLimits(import_cells=3)) == "TOO_MANY_CELLS line 7, column -"
    empty = file(["PATIENT_ID", "SEX"], [])
    assert outcome(patient=empty, limits=ImportLimits(import_cells=8)) == "ok"
    assert outcome(patient=empty, limits=ImportLimits(import_cells=7)) == (
        "TOO_MANY_CELLS line 7, column -"
    )
    no_rows = file(["PATIENT_ID", "SAMPLE_ID", "TMB"], [], types=["STRING", "STRING", "NUMBER"])
    assert outcome(sample=no_rows, limits=ImportLimits(import_cells=7)) == "ok"
    assert outcome(sample=no_rows, limits=ImportLimits(import_cells=6)) == (
        "TOO_MANY_CELLS line -, column -"
    )


# --- Cells --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "want"),
    [
        ("NA", "KEY_MISSING"),
        ("[Not Available]", "KEY_MISSING"),
        ("", "KEY_MISSING"),
        ("   ", "KEY_MISSING"),
        ("Pending", "ok"),
        ("null", "ok"),
        ("P#1", "KEY_CHARACTERS"),
        ("P1 ", "PADDED_KEY"),
        (" P1", "PADDED_KEY"),
        ("P1\xa0", "KEY_CHARACTERS"),
        ("P1\u3000", "KEY_CHARACTERS"),
        ("P\u00e91", "KEY_CHARACTERS"),
        ("Mar-1", "EXCEL_DATE"),
        (" Mar-1", "EXCEL_DATE"),
        ("P.1_(a)[b]',+-:;", "ok"),
    ],
)
def test_keys(outcome: Outcome, cell: str, want: str) -> None:
    patient = file(["PATIENT_ID", "SEX"], [[cell, "F"], ["P2", "M"]])
    sample = file(["PATIENT_ID", "SAMPLE_ID"], [[cell, "S1"], ["P2", "S2"]])
    assert outcome(patient=patient) == (want if want == "ok" else f"{want} line 6, column 1")
    assert outcome(patient=None, sample=sample) == (
        want if want == "ok" else f"{want} line 6, column 1"
    )


def test_a_key_holds_exactly_the_id_characters_by_every_code_point(clinical: ModuleType) -> None:
    """The check of a key's characters (one compiled pattern, not a loop) accepts a code point,
    alone or between two letters, exactly when it is one of the 74 an id may hold: no case
    folding, no Unicode class, no line end (``$`` would let a final ``\\n`` pass)."""
    allowed = importlib.import_module("aibi.packs.onco.attributes").ID_CHARACTERS
    assert len(allowed) == 74
    wrong = [
        cp
        for cp in range(0x110000)
        if (clinical.ID_CELL.fullmatch(chr(cp)) is not None) != (chr(cp) in allowed)
        or (clinical.ID_CELL.fullmatch("a" + chr(cp) + "b") is not None) != (chr(cp) in allowed)
    ]
    assert wrong == []


def test_a_sample_id_s_length(outcome: Outcome) -> None:
    ok = file(["PATIENT_ID", "SAMPLE_ID"], [["P1", "S" * 63]])
    assert outcome(sample=ok) == "ok"
    long = file(["PATIENT_ID", "SAMPLE_ID"], [["P1", "S" * 64]])
    assert outcome(sample=long) == "SAMPLE_ID_LENGTH line 6, column 2"
    assert outcome(patient=file(["PATIENT_ID"], [["P" * 64]])) == "ok"


@pytest.mark.parametrize(
    ("column", "cell", "want"),
    [
        ("SITE", "Mar-1", "EXCEL_DATE"),
        ("SITE", "1-mar", "EXCEL_DATE"),
        ("SITE", "MAR-99", "EXCEL_DATE"),
        ("SITE", " Mar-1", "EXCEL_DATE"),
        ("SITE", "Mar-1 ", "EXCEL_DATE"),
        ("SITE", "\xa0Mar-1", "EXCEL_DATE"),
        ("SITE", "jan-feb", "EXCEL_DATE"),
        ("SITE", "Mar-1.5", "ok"),
        ("SITE", "Mar-1-2", "ok"),
        ("SITE", "March-1", "ok"),
        ("SITE", "\ufeffMar-1", "ok"),
        ("DATE_X", "Mar-1", "ok"),
        ("SURGERY_DATE", "Mar-1", "ok"),
    ],
)
def test_excel_dates(outcome: Outcome, column: str, cell: str, want: str) -> None:
    assert outcome(patient=patient_with(column, cell)) == (
        want if want == "ok" else f"{want} line 6, column 2"
    )


def test_an_excel_date_in_a_number_column(outcome: Outcome) -> None:
    assert outcome(patient=patient_with("AGE", "Mar-1", "NUMBER")) == "EXCEL_DATE line 6, column 2"


@pytest.mark.parametrize(
    ("cell", "want"),
    [
        ("TRUE", "ok"),
        ("FALSE", "ok"),
        ("TRUE ", "ok"),
        (" FALSE", "ok"),
        ("", "ok"),
        ("NA", "ok"),
        ("[Not Available]", "ok"),
        ("Pending", "ok"),
        (" NA", "ok"),
        ("true", "BOOLEAN_VALUE"),
        ("Yes", "BOOLEAN_VALUE"),
        ("1", "BOOLEAN_VALUE"),
        ("TRUE\xa0", "BOOLEAN_VALUE"),
        ("NA\u3000", "BOOLEAN_VALUE"),
        ("\u3000", "BOOLEAN_VALUE"),
    ],
)
def test_boolean_cells(outcome: Outcome, cell: str, want: str) -> None:
    assert outcome(patient=patient_with("FLAG", cell, "BOOLEAN")) == (
        want if want == "ok" else f"{want} line 6, column 2"
    )


@pytest.mark.parametrize(
    ("name", "cell", "want"),
    [
        ("OS_STATUS", "1:DECEASED", "ok"),
        ("OS_STATUS", "LIVING", "ok"),
        ("OS_STATUS", "1:DECEASED ", "ok"),
        ("OS_STATUS", "Pending", "ok"),
        ("OS_STATUS", "Not Available", "ok"),
        ("OS_STATUS", "Dead", "SURVIVAL_STATUS"),
        ("OS_STATUS", "1:Deceased", "SURVIVAL_STATUS"),
        ("OS_STATUS", "NA\xa0", "SURVIVAL_STATUS"),
        ("DFS_STATUS", "DiseaseFree", "ok"),
        ("DFS_STATUS", "1:Recurred", "ok"),
        ("DFS_STATUS", "Dead", "SURVIVAL_STATUS"),
        ("DFS_STATUS", "Recurred\u3000", "SURVIVAL_STATUS"),
    ],
)
def test_survival_status_values(outcome: Outcome, name: str, cell: str, want: str) -> None:
    assert outcome(patient=patient_with(name, cell)) == (
        want if want == "ok" else f"{want} line 6, column 2"
    )


def test_a_prefixed_status_beside_its_months_in_the_patient_file_alone(outcome: Outcome) -> None:
    def patient(status: str, cell: str) -> bytes:
        months = status.removesuffix("_STATUS") + "_MONTHS"
        return file(
            ["PATIENT_ID", status, months],
            [["P1", cell, "1"]],
            types=["STRING", "STRING", "NUMBER"],
        )

    assert outcome(patient=patient("PFS_STATUS", "1:PROGRESSION")) == "ok"
    assert outcome(patient=patient("PFS_STATUS", "0:")) == "ok"
    assert outcome(patient=patient("PFS_STATUS", "0:a:b")) == "ok"
    assert outcome(patient=patient("PFS_STATUS", "PROGRESSION")) == (
        "SURVIVAL_STATUS line 6, column 2"
    )
    assert outcome(patient=patient("PFS_STATUS", "2:x")) == "SURVIVAL_STATUS line 6, column 2"
    assert outcome(patient=patient("PFS_STATUS", "[Pending]")) == "ok"
    # DFS_STATUS beside DFS_MONTHS follows its own values, not the prefix rule
    assert outcome(patient=patient("DFS_STATUS", "DiseaseFree")) == "ok"
    no_months = file(["PATIENT_ID", "PFS_STATUS"], [["P1", "x"]])
    assert outcome(patient=no_months) == "ok"
    in_sample = file(
        ["PATIENT_ID", "SAMPLE_ID", "PFS_STATUS", "PFS_MONTHS"],
        [["P1", "S1", "x", "1"]],
        types=["STRING", "STRING", "STRING", "NUMBER"],
    )
    assert outcome(sample=in_sample) == "ok"


def test_the_strip_class_by_column_kind(outcome: Outcome) -> None:
    """Each of the 19 code points ``str.strip()`` removes and Java's trim keeps, computed here, on
    either side of a value the reference reads after ``strip()``: a boolean, a null word in a
    boolean or a status, a status value and a key are refused; a ``NUMBER`` cell is the core's
    counted ``unparsed``."""
    assert len(STRIP_CLASS) == 19
    for pad in STRIP_CLASS:
        for padded in (pad + "{}", "{}" + pad):
            for value in ("TRUE", "FALSE", "NA", "[Not Available]"):
                cell = patient_with("FLAG", padded.format(value), "BOOLEAN")
                assert outcome(patient=cell) == "BOOLEAN_VALUE line 6, column 2", (pad, value)
            for value in ("0:LIVING", "1:DECEASED", "LIVING", "NA"):
                cell = patient_with("OS_STATUS", padded.format(value))
                assert outcome(patient=cell) == "SURVIVAL_STATUS line 6, column 2", (pad, value)
            for value in ("DiseaseFree", "1:Recurred"):
                cell = patient_with("DFS_STATUS", padded.format(value))
                assert outcome(patient=cell) == "SURVIVAL_STATUS line 6, column 2", (pad, value)
            key = file(["PATIENT_ID", "SEX"], [[padded.format("P1"), "F"]])
            assert outcome(patient=key) == "KEY_CHARACTERS line 6, column 1", pad
            number = patient_with("AGE", padded.format("1"), "NUMBER")
            assert outcome(patient=number) == "ok", pad


def test_missing_spellings_beyond_the_fixed_list(outcome: Outcome) -> None:
    pipes = ["N" + "|" * k + "A" for k in range(1, 65)]
    assert max(len(p) for p in pipes) == 66
    sixty_four = [f"[{'[' * k}NA{']' * k}]" for k in range(1, 31)] + [
        f"{'|' * k}NA" for k in range(1, 35)
    ]
    assert len(set(sixty_four)) == 64
    assert max(map(len, sixty_four)) <= 64
    rows = [[f"P{i}", v] for i, v in enumerate(sixty_four)]
    assert outcome(patient=file(["PATIENT_ID", "NOTE"], rows)) == "ok"
    more = [*rows, ["P99", "N||||A|"]]
    assert outcome(patient=file(["PATIENT_ID", "NOTE"], more)) == "MISSING_FORM line -, column 2"
    longest = "[" * 31 + "NA" + "]" * 31
    assert len(longest) == 64
    assert outcome(patient=patient_with("NOTE", longest)) == "ok"
    assert outcome(patient=patient_with("NOTE", longest + "]")) == "MISSING_FORM line -, column 2"
    fixed = [[f"P{i}", v] for i, v in enumerate(["NA", "[Not Available]", "Not available", " "])]
    assert outcome(patient=file(["PATIENT_ID", "NOTE"], fixed)) == "ok"


# --- What is kept ---------------------------------------------------------------------------------


def test_what_a_file_keeps(clinical: ModuleType) -> None:
    data = file(
        ["PATIENT_ID", "SEX", "AGE", "SMOKER", "NOTE"],
        [
            ["P1", "F", "1", "TRUE", "a"],
            ["P2", "F ", "NA", "", "b"],
            ["P3", "M", " NA", "NA", "c"],
            ["P4", "[Not Applicable]", "4", "FALSE", "d"],
            ["P5", "M", "5", "TRUE", " e"],
        ],
        types=["STRING", "STRING", "NUMBER", "BOOLEAN", "STRING"],
        labels=["Patient", " Sex ", "Age", "Smoker", "Note"],
        prio=["1", "2", "-1", "0", "9007199254740991"],
    )
    found = clinical.read(data, "p.txt", "patient", ImportLimits())
    assert found.data == data
    assert found.rows == 5
    assert found.keys == frozenset({"P1", "P2", "P3", "P4", "P5"})
    by = {column.name: column for column in found.columns}
    assert [column.name for column in found.columns] == [
        "PATIENT_ID",
        "SEX",
        "AGE",
        "SMOKER",
        "NOTE",
    ]
    assert by["SEX"].label == " Sex "
    assert by["NOTE"].priority == 2**53 - 1
    assert by["AGE"].priority == -1
    assert by["PATIENT_ID"].key
    assert not by["SEX"].key
    assert not by["SEX"].category  # F, "F ", M, M: three distinct of four present cells
    assert by["NOTE"].category is False
    assert by["AGE"].additions == {" NA": "UNKNOWN"}
    assert by["SEX"].additions == {}
    assert (by["SEX"].padded, by["SEX"].padded_rows) == (1, (2,))
    assert (by["NOTE"].padded, by["NOTE"].padded_rows) == (1, (5,))
    assert by["AGE"].padded == 0


def test_the_category_rule(clinical: ModuleType) -> None:
    """D228's rule on present cells (neither empty nor missing): at most 50 distinct values and at
    most half of them."""

    def category(cells: list[str]) -> bool:
        rows = [[f"P{i}", cell] for i, cell in enumerate(cells)]
        found = clinical.read(file(["PATIENT_ID", "X"], rows), "p.txt", "patient", ImportLimits())
        return bool(found.columns[1].category)

    assert category(["a", "a", "b", "b"])
    assert not category(["a", "a", "b", "c"])
    assert category(["a", "a", "b", "b", "NA", "NA", "", "c" * 0])
    assert not category(["a", "b", "NA", "NA"])
    assert category([f"v{i % 50}" for i in range(100)])
    assert not category([f"v{i % 51}" for i in range(102)])
    assert not category([])


def test_dangling_samples(clinical: ModuleType) -> None:
    limits = ImportLimits()
    patient = clinical.read(PATIENT, "p.txt", "patient", limits)
    rows = [["P1", "S1"], ["P9", "S2"], ["P2", "S3"], *([["P8", f"S{i}"] for i in range(4, 10)])]
    sample = clinical.read(
        file(["PATIENT_ID", "SAMPLE_ID"], rows), "s.txt", "sample", limits, patient=patient
    )
    assert (sample.dangling, sample.dangling_rows) == (7, (2, 4, 5, 6, 7))
    alone = clinical.read(file(["PATIENT_ID", "SAMPLE_ID"], rows), "s.txt", "sample", limits)
    assert alone.dangling == 0


# --- The core's parse, the sentinel, totality -----------------------------------------------


def read_with_fields(
    clinical: ModuleType, data: bytes, role: str = "patient"
) -> tuple[object, list[tuple[str, ...]]]:
    """``clinical.read`` of ``data``, and the fields of every row the reader counted (a test-only
    hook on the reader's ``counted``, which sees each accepted row's fields)."""
    seen: list[tuple[str, ...]] = []
    original = clinical._Reading.counted  # pyright: ignore[reportPrivateUsage]

    def counted(self: object, row: int, fields: list[str]) -> None:
        seen.append(tuple(fields))
        original(self, row, fields)

    with mock.patch.object(clinical._Reading, "counted", counted):  # pyright: ignore[reportPrivateUsage]
        found = clinical.read(data, "p.txt", role, ImportLimits())
    return found, seen


def _agrees(clinical: ModuleType, data: bytes) -> None:
    """When the reader accepts a file, the core's parse reads the same names and the same cells:
    every field of every row."""
    try:
        found, seen = read_with_fields(clinical, data)
    except Refused:
        return
    try:
        parsed = parse_text(data, clinical.SETTINGS)
    except SourceError as error:
        raise AssertionError("the core cannot parse what the reader accepted") from error
    assert list(parsed.names) == [column.name for column in found.columns]  # type: ignore[attr-defined]
    assert len(parsed.rows) == found.rows  # type: ignore[attr-defined]
    assert [tuple(row) for row in parsed.rows] == seen


@pytest.mark.parametrize(
    "data",
    [
        PATIENT,
        PATIENT.replace(b"\n", b"\r"),
        PATIENT.replace(b"\n", b"\r\n"),
        PATIENT.replace(b"\n", b"\r\n", 3),
        b"\xef\xbb\xbf" + PATIENT,
        patient_with("SEX", "F\u2028x"),
        patient_with("SEX", "F\u2029x"),
        patient_with("SEX", "F\u0085x"),
        patient_with("SEX", '"F'),
        patient_with("SEX", 'F"x"'),
        patient_with("SEX", "'F'"),
        patient_with("SEX", "\\"),
        patient_with("NOTE", "a" * 131_072),
        file(["PATIENT_ID", "SEX"], []),
        PATIENT.rstrip(b"\n"),
    ],
)
def test_the_core_s_parse_reads_what_the_reader_accepts(clinical: ModuleType, data: bytes) -> None:
    _agrees(clinical, data)
    clinical.read(data, "p.txt", "patient", ImportLimits())


ALPHABET = st.sampled_from(
    [
        *("a", "B", "#", " ", "\t", "\n", "\r", "\r\n", '"', "'", "\\", "\xa0", "\u2028"),
        *("\u0085", "\ufeff", "\x01", "\x00", "NA", "PATIENT_ID", "P1", "\u00e9", "\U0001f600"),
    ]
)


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(body=st.lists(ALPHABET, max_size=40))
def test_the_reader_and_the_core_s_parse_agree_on_generated_files(body: list[str]) -> None:
    clinical = importlib.import_module(MODULE)
    data = (PATIENT.decode() + "".join(body)).encode()
    _agrees(clinical, data)


@settings(max_examples=300, deadline=None)
@given(data=st.binary(max_size=300))
def test_the_reader_is_total(data: bytes) -> None:
    """Any bytes: the reader returns, or refuses with one of the pack's codes."""
    clinical = importlib.import_module(MODULE)
    codes = importlib.import_module("aibi.packs.onco.refusals").CODES
    for prefix in (b"", PATIENT[:60], PATIENT):
        found: Refused | None = None
        try:
            clinical.read(prefix + data, "p.txt", "patient", ImportLimits())
        except Refused as error:
            found = error
        if found is not None:
            assert type(found) is Refused
            assert all(str(r.code).removeprefix("onco.") in codes for r in found.refusals)


SENTINEL = "zQ7sEnTiNeL"


def test_no_refusal_shows_what_the_file_holds(clinical: ModuleType) -> None:
    """A sentinel in every place a refusal is about (a cell, a header field, a name) is in no
    refusal's message; a refusal names the file, as data, and places by number."""
    s = SENTINEL
    cases = [
        patient_with("SEX", s + "\x01"),
        patient_with("FLAG", s, "BOOLEAN"),
        patient_with("OS_STATUS", s),
        patient_with("SITE", "Mar-" + s[:0] + "1"),
        file(["PATIENT_ID", "SEX"], [[s + "#", "F"]]),
        file(["PATIENT_ID", "SEX"], [[s + " ", "F"]]),
        file(["PATIENT_ID", s.lower()], [["P1", "F"]]),
        file(["PATIENT_ID", "SEX"], [["P1", "F"]], labels=["L", "NA"], defs=["D", s]),
        file(["PATIENT_ID", "SEX"], [["P1", "F"]], types=["STRING", s]),
        file(["PATIENT_ID", "SEX"], [["P1", "F"]], prio=["1", s]),
        PATIENT + (s + "\n").encode(),
        PATIENT + (s + "\tx\tx\n").encode(),
        PATIENT + s.encode() + b"\xff\n",
        file(["PATIENT_ID", "NOTE"], [["P1", "x" * 131_073 + s]]),
    ]
    for data in cases:
        with pytest.raises(Refused) as raised:
            clinical.read(data, "p.txt", "patient", ImportLimits())
        (refusal,) = raised.value.refusals
        said = "".join(
            seg.text if isinstance(seg, TextSegment) else seg.data for seg in refusal.message
        )
        assert s.lower() not in said.lower(), said
        assert [seg.data for seg in refusal.message if isinstance(seg, DataSegment)] == ["p.txt"]
        assert (refusal.path, refusal.limit, refusal.counts) == (None, None, None)
        assert raised.value.__context__ is None
        assert raised.value.__cause__ is None


def test_a_refusal_leaves_no_exception_chain(clinical: ModuleType) -> None:
    with pytest.raises(Refused) as raised:
        clinical.read(
            PATIENT + b"\xff" + SENTINEL.encode() + b"\n", "p.txt", "patient", ImportLimits()
        )
    assert raised.value.__context__ is None


def test_the_reader_holds_its_aggregates_bounded(clinical: ModuleType) -> None:
    """A column of many distinct values keeps at most 51 for its category rule, each as written
    when it is at most 64 characters and else as a 16-byte digest, and one of many missing
    spellings at most 65 for its cap, each of at most 64 characters, a longer one setting a flag."""
    rows = [
        [
            f"P{i}",
            f"v{i}",
            "N" + "|" * (i % 62 + 1) + "A" if i < 250 else "|" * (i % 62 + 1) + "NA",
            f"{i}" + "w" * 80,
            "N" + "|" * (70 + i) + "A",
        ]
        for i in range(500)
    ]
    data = file(["PATIENT_ID", "X", "Y", "Z", "W"], rows)
    reading = clinical._Reading("p.txt", "patient", ImportLimits(), 0, None)  # pyright: ignore[reportPrivateUsage]
    for number, raw in clinical.lines(data):
        line = raw.decode()
        if number <= 5:
            reading.header_line(number, line)
        else:
            reading.data_line(number, line)
    assert len(reading.distinct[1]) == 51
    assert all(isinstance(v, str) and len(v) <= 64 for v in reading.distinct[1])
    assert len(reading.distinct[3]) == 51
    assert all(isinstance(v, bytes) and len(v) == 16 for v in reading.distinct[3])
    assert len(reading.missing[2]) == 65
    assert max(map(len, reading.missing[2])) <= 64
    assert not reading.long_missing[2]
    assert reading.missing[4] == set()
    assert reading.long_missing[4]
    assert not reading.long_missing[1]


def test_a_distinct_long_value_is_kept_as_a_digest(clinical: ModuleType) -> None:
    assert clinical.identity("x" * 64) == "x" * 64
    digest = clinical.identity("x" * 65)
    assert isinstance(digest, bytes)
    assert len(digest) == 16
    assert digest != clinical.identity("x" * 64 + "y")
    assert digest == clinical.identity("x" * 65)
    assert clinical.identity("\U0001f600" * 65) != clinical.identity("\U0001f601" * 65)


def test_the_category_rule_holds_for_long_values(clinical: ModuleType) -> None:
    def category(cells: list[str]) -> bool:
        rows = [[f"P{i}", cell] for i, cell in enumerate(cells)]
        found = clinical.read(file(["PATIENT_ID", "X"], rows), "p.txt", "patient", ImportLimits())
        return bool(found.columns[1].category)

    one, two, three = ("a" * 100, "b" * 100, "c" * 100 + "d")
    assert category([one, one, two, two])
    assert not category([one, one, two, three])
    assert category([f"{i % 50:02d}" + "q" * 70 for i in range(100)])
    assert not category([f"{i % 51:02d}" + "q" * 70 for i in range(102)])


def traced[T](call: Callable[[], T]) -> tuple[T | Refused, int, int]:
    """``call``'s result (or the ``Refused`` it raises), the memory it still holds once it has
    returned and the most it held at once, in bytes (``tracemalloc``: what the call allocated, not
    what the test built before)."""
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        found: T | Refused
        try:
            found = call()
        except Refused as error:
            found = error.with_traceback(None)  # its frames hold the last line
        gc.collect()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return found, current - base, peak - base


def test_a_read_holds_no_cell_when_it_returns_or_while_it_reads(clinical: ModuleType) -> None:
    """Distinct cells of four-byte characters, in many columns: nothing of them outlives the call
    (no cache past a read) and no column's distinct set holds them (a set of the cells themselves
    would hold 51 of each column's)."""
    columns, rows = 20, 100
    cell = "x" * 3_990 + "\U0001f600" * 5
    body = [[f"P{i}", *(f"{i:04d}{c:02d}" + cell for c in range(columns))] for i in range(rows)]
    data = file(["PATIENT_ID", *(f"C{c}" for c in range(columns))], body)
    held = columns * rows * len(cell) * 4  # the cells as decoded: four bytes a character
    assert held > 30_000_000
    found, kept, peak = traced(lambda: clinical.read(data, "p.txt", "patient", ImportLimits()))
    assert not isinstance(found, Refused)
    assert found.rows == rows
    assert kept < held // 100, kept
    assert peak < held // 20, peak
    other = data.replace(b"x", b"y")  # a second read, of other cells, holds nothing more
    _, again, _ = traced(lambda: clinical.read(other, "p.txt", "patient", ImportLimits()))
    assert again < held // 100, again


def test_a_long_missing_spelling_is_held_as_a_flag(clinical: ModuleType) -> None:
    columns, rows = 20, 100
    body = [
        [f"P{i}", *("N" + "|" * (3_000 + i) + "A" for _ in range(columns))] for i in range(rows)
    ]
    data = file(["PATIENT_ID", *(f"C{c}" for c in range(columns))], body)
    found, kept, peak = traced(lambda: clinical.read(data, "p.txt", "patient", ImportLimits()))
    assert isinstance(found, Refused)
    (refusal,) = found.refusals
    assert str(refusal.code) == "onco.MISSING_FORM"
    assert kept < len(data) // 100, kept
    assert peak < len(data) // 4, peak


TABS = 1_000_000


@pytest.mark.parametrize(
    ("shape", "code", "most"),
    [
        ("row", "ROW_WIDTH", 2.5),
        ("row with a control character", "CONTROL_CHARACTER", 2.5),
        ("names line", "HEADER_ROW_WIDTH", 2.5),
        ("header row", "HEADER_ROW_WIDTH", 2.5),
        ("header rows and names line", "TOO_MANY_COLUMNS", 1.5),
    ],
)
def test_a_line_is_refused_for_its_width_before_it_is_split(
    clinical: ModuleType, shape: str, code: str, most: float
) -> None:
    """A line of a million tabs is counted, not split: the read holds the line as bytes and as
    text (twice the file; the last shape, five such lines, holds them as text and one as bytes,
    1.2 times it) and never its million fields (a list of them is 8 MB, eight times a one-line
    file and more than the whole of the last)."""
    many = "\t" * TABS
    lines = ["#L", "#D", "#STRING", "#1", "PATIENT_ID"]
    if shape.startswith("row"):
        lines.append("P1" + many + ("\x01" if "control" in shape else ""))
    elif shape == "names line":
        lines[4] = "PATIENT_ID" + many
    elif shape == "header row":
        lines[2] = "#STRING" + many
    else:
        lines = ["#" + many] * 4 + ["A" + many]
    data = ("\n".join(lines) + "\n").encode()
    found, _, peak = traced(lambda: clinical.read(data, "p.txt", "patient", ImportLimits()))
    assert isinstance(found, Refused)
    (refusal,) = found.refusals
    assert str(refusal.code) == f"onco.{code}"
    assert peak < most * len(data), (peak, len(data))


LABEL = 1_000_000
"""The characters of the long field of a header row below: a file of 1 MB, a row of one field."""


@pytest.mark.parametrize(
    ("row", "code", "bom"),
    [
        (0, "TEXT_VALUE", False),
        (0, "TEXT_VALUE", True),
        (1, "TEXT_VALUE", False),
        (2, "DATATYPE_ROW", False),
        (3, "PRIORITY_ROW", False),
    ],
)
def test_a_header_row_is_refused_for_its_field_before_the_field_is_cut_out(
    clinical: ModuleType, row: int, code: str, bom: bool
) -> None:
    """One field of a million characters, one of them astral (so the line is held at four bytes
    a character), in each header row: it is refused at its place, by its length and from the
    tabs' positions, and the read holds the line as text alone, decoded from the file's bytes
    where they are (five times the file: the decode's first pass and the text at four bytes a
    character; six with a byte order mark, whose first pass is wider) and no copy of the
    field: a copy of the line's bytes, the slice, ``strip()`` and ``lower()`` of the old order
    held four more, and the read peaked at eighteen times the file."""
    lines = ["#L", "#D", "#STRING", "#1"]
    lines[row] = "#" + "x" * LABEL + "\U0001f600"
    if bom:
        lines[0] = "\ufeff" + lines[0]
    data = ("\n".join([*lines, "PATIENT_ID"]) + "\n").encode()
    found, _, peak = traced(lambda: clinical.read(data, "p.txt", "patient", ImportLimits()))
    assert isinstance(found, Refused)
    (refusal,) = found.refusals
    assert str(refusal.code) == f"onco.{code}"
    said = "".join(s.text for s in refusal.message if isinstance(s, TextSegment))
    assert f"line {row + 1}, column 1" in said
    assert peak < 6.5 * len(data), (peak, len(data))


def test_a_header_field_over_its_limit_is_found_by_its_place(outcome: Outcome) -> None:
    """The first failing field of a row is the first by column, whether it is too long, a null
    word or not text: a long field after a null word is refused at the null word's column."""
    long = "x" * 5_000
    for labels, want in (
        (["L", long], "TEXT_VALUE line 1, column 2"),
        ([long, " na "], "TEXT_VALUE line 1, column 1"),
        (["L", "[Pending]", long], "TEXT_VALUE line 1, column 2"),
        (["L", "L"], "ok"),
    ):
        names = ["PATIENT_ID", "SEX", "AGE"][: len(labels)]
        data = file(names, [["P1", "F", "1"][: len(labels)]], labels=labels)
        assert outcome(data, SAMPLE) == want
    for types, want in (
        (["STRING", "S" * 6_000], "DATATYPE_ROW line 3, column 2"),
        (["STRING", "STRING"], "ok"),
        (["STRING", "BOOLEAN "], "DATATYPE_ROW line 3, column 2"),
    ):
        assert outcome(file(["PATIENT_ID", "SEX"], [["P1", "F"]], types=types), SAMPLE) == want
    for prio, want in (
        (["1", "9" * 6_000], "PRIORITY_ROW line 4, column 2"),
        (["1", "0" * 18], "PRIORITY_ROW line 4, column 2"),
        (["1", "0" * 17], "ok"),
        (["1", "-" + "0" * 17], "ok"),
        (["1", "-" + "0" * 18], "PRIORITY_ROW line 4, column 2"),
        (["1", "-1"], "ok"),
    ):
        assert outcome(file(["PATIENT_ID", "SEX"], [["P1", "F"]], prio=prio), SAMPLE) == want


def test_padded_rows_are_the_first_five(clinical: ModuleType) -> None:
    rows = [[f"P{i}", "x " if i % 2 else "x"] for i in range(20)]
    found = clinical.read(file(["PATIENT_ID", "X"], rows), "p.txt", "patient", ImportLimits())
    assert (found.columns[1].padded, found.columns[1].padded_rows) == (10, (2, 4, 6, 8, 10))


def test_the_codes_the_reader_can_refuse_with(clinical: ModuleType) -> None:
    """The reader's codes, every string constant of its module that is a code: the clinical
    files' 29; ``UNREADABLE_ROWS`` is not among them (nothing raises it)."""
    source = clinical.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    codes = set(importlib.import_module("aibi.packs.onco.refusals").CODES)
    found = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in codes
    }
    assert found == {
        "NOT_UTF8", "CONTROL_CHARACTER", "HEADER_ROWS", "HEADER_ROW_WIDTH", "TOO_MANY_COLUMNS",
        "ATTRIBUTE_NAME", "DUPLICATE_ATTRIBUTE", "TEXT_VALUE", "DATATYPE_ROW", "PRIORITY_ROW",
        "NO_KEY_COLUMN", "SAMPLE_ID_IN_PATIENT_FILE", "PREDEFINED_ATTRIBUTE", "BANNED_ATTRIBUTE",
        "ATTRIBUTE_IN_BOTH_FILES", "BLANK_LINE", "COMMENT_LINE", "REPEATED_HEADER", "ROW_WIDTH",
        "LONG_FIELD", "TOO_MANY_CELLS", "EXCEL_DATE", "KEY_MISSING", "PADDED_KEY",
        "KEY_CHARACTERS", "SAMPLE_ID_LENGTH", "BOOLEAN_VALUE", "SURVIVAL_STATUS", "MISSING_FORM",
    }  # fmt: skip
