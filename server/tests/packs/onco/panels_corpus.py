"""The gene panel matrix corpus of M4.2b-2a (D447): cells made by generators, never listed one by
one, each a small study that differs from a valid one by one change (or, for the pairs, two).

This is not a test: pytest does not collect it, and it imports nothing of the pack or of aibi. It
loads ``mutation_corpus.py`` (the slice-1b corpus, a stable helper: the study meta, the clinical
files, the MAF and the case-list builder) by its path, so that the scratch harness that runs each
cell on the pinned reference (cbioportal-core 47890bb, offline, not relaxed; D409) can load it as it
is, and ``test_panels_corpus.py`` loads the same cells. The reference's outcome of every cell is
recorded in ``panels_reference.json`` by error identity, with the file each error names: whether it
crashed and the identity of each error it reported, in order (no message of the reference).

A cell is a mapping of a path below the study's directory to its content (``bytes``,
``DIRECTORY``, or ``("symlink", target)``), as in ``mutation_corpus``; ``CELLS`` maps each cell's
id, ``<generator>:<case>``, to it. The valid study is the slice-1b study (its MAF and its
``_sequenced`` list) with a gene panel matrix meta and a matrix of the three samples, ``SAMPLE_ID``
and ``mutations``, the third ``NA``. The generators, each over a class:

- ``byte``: the matrix's bytes (line ends, a byte order mark on each line role, blank and comment
  lines, the repeated header, row widths, an empty file, bytes that are not UTF-8 on each line
  role, the field bound in characters and bytes on each column, the data file missing, a
  directory, a symlink);
- ``control``: each of the 30 control characters and the three line separators on each line role
  (a top line, a name, a sample id, a cell), and the separators beside a gated file;
- ``gate``: the one-column header and the sniffer's window of five lines (a tab on each line from
  the first to the seventh after it, after ``#`` and blank lines, with ``\\r`` ends), and every
  fault the reference does not see behind it;
- ``header``: the header's names (``SAMPLE_ID`` not first or in another case, every padding of
  each name at each position, a name no meta's, an empty one, a trailing tab, a repeated one, too
  many, quoted ones, collisions of their ids);
- ``cell``: a token in the column ``mutations`` (spellings of ``NA``, ``WGS``, quotes, blanks,
  every padding), on a sample in the ``_sequenced`` list and on one out of it, and in a column no
  MAF's;
- ``sample``: the sample column (repeats as written and near it, unknown, padded, blank, quoted,
  short rows), each with the panel cell ``NA`` and not;
- ``seq``: the ``_sequenced`` list (none, two lists the reference may take holding the same ids, so
  that the reference's outcome does not depend on the order it lists them in, the column
  ``mutations`` a meta other than the MAF's names, no mutation meta);
- ``dict``: the reference's dictionary of meta files (V:3363-3373, V:4740-4778): a study or
  resource meta holding ``stable_id`` with or without ``genetic_alteration_type`` and
  ``datatype``, padded keys, a byte order mark leading the ``stable_id`` or the
  ``genetic_alteration_type`` line, crossed with every early exit of the matrix's validation;
- ``meta``: the matrix meta's values, a second one, the matrix naming another file and another
  meta naming the matrix, a ``gene_panel`` key on each profile meta with and without its column;
- ``beside``: each other imported role beside the matrix, its column in the matrix, and a fault in
  the matrix with a fault in the other role, both read;
- ``type``: a meta of each C-table type the pack never imports beside the matrix, its column in
  the matrix or not;
- ``typing``: every rule by which the reference reads and types a meta (C:852-989), on each pair
  the pack never imports, its id a matrix column, and blank meta lines of every character
  ``str.strip()`` removes (review r1 MAJOR-1).
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType


def _base() -> ModuleType:
    path = Path(__file__).resolve().parent / "mutation_corpus.py"
    spec = importlib.util.spec_from_file_location("mutation_corpus_for_panels", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = _base()
DIRECTORY = BASE.DIRECTORY
SID = BASE.SID
BOM = BASE.BOM
MATRIX_FILE = "data_gene_panel_matrix.txt"
MATRIX_META = "meta_gene_panel_matrix.txt"
SEQUENCED = BASE.SEQUENCED
SAMPLES = ("S1", "S2", "S3")


def meta(data: str = MATRIX_FILE, extra: str = "") -> bytes:
    return (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: GENE_PANEL_MATRIX\n"
        f"datatype: GENE_PANEL_MATRIX\ndata_filename: {data}\n{extra}"
    ).encode()


def tsv(rows: Sequence[Sequence[str]], end: str = "\n", top: bytes = b"") -> bytes:
    """A matrix of ``rows`` (the header first), each line ended by ``end``, after ``top``."""
    return top + (end.join("\t".join(row) for row in rows) + end).encode()


HEADER = ("SAMPLE_ID", "mutations")
R1 = ("S1", "IMPACT1")
R2 = ("S2", "IMPACT1")
R3 = ("S3", "NA")
VALID = tsv([HEADER, R1, R2, R3])

Content = bytes | str | tuple[str, str]
Cell = dict[str, Content]


def study(**over: Content | None) -> Cell:
    """The valid study (the slice-1b study, with the matrix meta and matrix), with ``over``
    applied as ``mutation_corpus.study`` applies it."""
    files: Cell = BASE.study()
    files[MATRIX_META] = meta()
    files[MATRIX_FILE] = VALID
    for key, value in over.items():
        path = key.replace("__", "/").replace("_DOT_", ".")
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value
    return files


def data(content: bytes, **over: Content | None) -> Cell:
    return study(data_gene_panel_matrix_DOT_txt=content, **over)


CELLS: dict[str, Cell] = {}


def cell(generator: str, case: str, files: Cell) -> None:
    key = f"{generator}:{case}"
    assert key not in CELLS, key
    CELLS[key] = files


STRIPPED = (
    " ",
    "\t",
    "\xa0",
    "\u1680",
    *(chr(point) for point in range(0x2000, 0x200B)),
    "\u202f",
    "\u205f",
    "\u3000",
)
"""The characters ``str.strip()`` removes that a field can hold and the pack does not refuse as a
control character or a line separator (``\\t`` splits a field; it pads a whole line)."""
NON_STRIPPED = ("\u200b", "\ufeff", "\u2060")
"""Characters a token may hold that ``str.strip()`` leaves."""
CONTROLS = tuple(chr(point) for point in (*range(0x20), 0x7F) if chr(point) not in "\t\n\r")
SEPARATORS = ("\x85", "\u2028", "\u2029")
STUDY_META = "meta_study.txt"


def _label(character: str) -> str:
    return f"{ord(character):04x}"


# --- byte -----------------------------------------------------------------------------------------


def _bytes() -> None:
    rows = [HEADER, R1, R2, R3]
    cell("byte", "valid", study())
    cell("byte", "crlf", data(tsv(rows, "\r\n")))
    cell("byte", "cr", data(tsv(rows, "\r")))
    cell("byte", "no_trailing_newline", data(VALID[:-1]))
    for at in range(1, 4):
        lines = VALID.split(b"\n")
        mixed = b"\n".join(lines[:at]) + b"\r\n" + b"\n".join(lines[at:])
        cell("byte", f"mixed_crlf_at_{at}", data(mixed))
        cell("byte", f"mixed_cr_at_{at}", data(mixed.replace(b"\r\n", b"\r")))
    cell("byte", "header_only", data(tsv([HEADER])))
    cell("byte", "header_only_no_newline", data(tsv([HEADER])[:-1]))
    cell("byte", "header_only_top", data(tsv([HEADER], top=b"#c\n")))
    cell("byte", "empty", data(b""))
    cell("byte", "newline_only", data(b"\n"))
    cell("byte", "hash_only", data(b"#a\n#b\n"))
    cell("byte", "top_comment", data(tsv(rows, top=b"#c\n")))
    cell("byte", "top_comments", data(tsv(rows, top=b"#a\n#b\t\n# c\n")))
    cell("byte", "space_hash_top", data(tsv(rows, top=b" #c\n")))
    for name, line in (
        ("empty", ""),
        ("space", " "),
        ("tabs", "\t"),
        ("nbsp", "\xa0"),
        ("ideographic", "\u3000"),
        ("tab_nbsp", "\xa0\t\xa0"),
        ("zwsp", "\u200b"),
    ):
        content = (
            "\t".join(HEADER) + "\n" + "\t".join(R1) + "\n" + line + "\n" + "\t".join(R2) + "\n"
        ).encode()
        cell("byte", f"blank_{name}", data(content))
    cell("byte", "blank_last", data(VALID + b"\n"))
    cell("byte", "blank_first_line", data(b"\n" + VALID))
    cell("byte", "hash_row", data(tsv([HEADER, R1, ("#S2", "IMPACT1")])))
    cell("byte", "hash_late", data(VALID + b"#late\n"))
    cell("byte", "space_hash_row", data(tsv([HEADER, R1, (" #S2", "IMPACT1")])))
    cell("byte", "repeated_header", data(tsv([HEADER, R1, HEADER, R2])))
    cell("byte", "repeated_header_longer", data(tsv([HEADER, R1, (*HEADER, "x"), R2])))
    # a line that starts with the header's text but not at a field's end (review r1 m5)
    cell("byte", "repeated_header_prefix", data(VALID + b"SAMPLE_ID\tmutationsX\n"))
    cell("byte", "long_row", data(tsv([HEADER, ("S1", "P", "Q")])))
    cell("byte", "short_row", data(tsv([HEADER, ("S1",), R2])))
    cell("byte", "short_row_last", data(tsv([HEADER, R1, ("S2",)])))
    cell("byte", "cr_inside_cell", data(b"SAMPLE_ID\tmutations\nS1\tIMP\rACT\nS2\tP\n"))
    for column, (left, right) in (("sample", ("{}", "P")), ("panel", ("S1", "{}"))):
        for unit, width in (("ascii", 1), ("two_byte", 2)):
            char = "x" if width == 1 else "\xe9"
            for length in (131_072, 131_073):
                value = char * length
                row = (left.format(value), right.format(value))
                cell("byte", f"long_{column}_{unit}_{length}", data(tsv([HEADER, row])))
    for length in (131_072, 131_073):
        cell("byte", f"long_header_{length}", data(tsv([("SAMPLE_ID", "x" * length), R1])))
    for role, content in (
        ("header", b"SAMPLE_ID\tmut\xffations\nS1\tP\n"),
        ("row", b"SAMPLE_ID\tmutations\nS1\tP\xff\n"),
        ("top", b"#\xff\nSAMPLE_ID\tmutations\nS1\tP\n"),
        ("onecol", b"SAMPLE_ID\nS\xff1\n"),
        ("surrogate", b"SAMPLE_ID\tmutations\nS1\t\xed\xa0\x80\n"),
    ):
        cell("byte", f"not_utf8_{role}", data(content))
    for role, content in (
        ("tabbed", BOM + VALID),
        ("only", BOM),
        ("newline", BOM + b"\n"),
        ("onecol", BOM + b"SAMPLE_ID\nS1\nS2\n"),
        ("hash_then_tabbed", BOM + b"#c\n" + VALID),
        ("hash_shift", BOM + b"#c\nSAMPLE_ID\nS1\nS2\nS3\nS1\nS9\tP\n"),
        ("crlf", BOM + VALID.replace(b"\n", b"\r\n")),
        ("at_row", tsv([HEADER, ("\ufeffS1", "P")])),
        ("at_panel", tsv([HEADER, ("S1", "\ufeffP")])),
        ("mid_panel", tsv([HEADER, ("S1", "IMP\ufeffACT")])),
        ("after_top", b"#c\n" + BOM + VALID),
    ):
        cell("byte", f"bom_{role}", data(content))
    cell("byte", "nobom_hash_shift", data(b"#c\nSAMPLE_ID\nS1\nS2\nS3\nS1\nS9\tP\n"))
    # the one mark the pack exempts is the file's first character (review r2 MINOR-A)
    for name, head in (
        ("two_marks", BOM + BOM + b"SAMPLE_ID"),
        ("mark_then_control", BOM + b"\x01SAMPLE_ID"),
        ("control_then_header", b"\x01SAMPLE_ID"),
        ("mark_header_control_end", BOM + b"SAMPLE_ID\x01"),
        ("mark_hash_control", BOM + b"#a\x01\nSAMPLE_ID"),
    ):
        cell("byte", f"edge_{name}", data(head + b"\tmutations\nS1\tP\nS2\tP\nS3\tNA\n"))
    cell("byte", "missing", study(data_gene_panel_matrix_DOT_txt=None))
    cell(
        "byte",
        "directory",
        study(data_gene_panel_matrix_DOT_txt=None, data_gene_panel_matrix_DOT_txt__x=b"x"),
    )
    cell(
        "byte",
        "symlink",
        study(data_gene_panel_matrix_DOT_txt=("symlink", "data_mutations.txt")),
    )


# --- control --------------------------------------------------------------------------------------


def _controls() -> None:
    for character in (*CONTROLS, *SEPARATORS, "\ufeff"):
        label = _label(character)
        cell("control", f"{label}_panel", data(tsv([HEADER, R1, ("S2", "IMP" + character)])))
        cell("control", f"{label}_sample", data(tsv([HEADER, R1, ("S2" + character, "P")])))
        cell(
            "control",
            f"{label}_top",
            data(tsv([HEADER, R1, R2], top=("#a" + character + "\n").encode())),
        )
        cell("control", f"{label}_name", data(tsv([("SAMPLE_ID", "mut" + character), R1])))
    for character in SEPARATORS:
        cell(
            "control",
            f"{_label(character)}_gated",
            data(("SAMPLE_ID\nS1" + character + "\nS2\n").encode()),
        )
        cell(
            "control", f"{_label(character)}_na", data(tsv([HEADER, R1, ("S3", "NA" + character)]))
        )


# --- gate -----------------------------------------------------------------------------------------


def _gate() -> None:
    cell("gate", "onecol", data(b"SAMPLE_ID\nS1\nS2\nS3\n"))
    cell("gate", "onecol_header_only", data(b"SAMPLE_ID\n"))
    cell("gate", "onecol_wrong_name", data(b"sample\nS1\n"))
    cell("gate", "onecol_dups", data(b"SAMPLE_ID\nS1\nS1\nS9\n"))
    cell("gate", "onecol_mixed", data(b"SAMPLE_ID\r\nS1\nS2\n"))
    cell("gate", "onecol_no_newline", data(b"SAMPLE_ID"))
    cell("gate", "onecol_long", data(("SAMPLE_ID\n" + "x" * 140_000 + "\n").encode()))
    cell("gate", "onecol_hash_row", data(b"SAMPLE_ID\nS1\n#S2\n"))
    cell("gate", "onecol_blank_row", data(b"SAMPLE_ID\nS1\n\nS2\n"))
    cell("gate", "newline_only", data(b"\n\n"))
    cell("gate", "spaces", data(b"  \n \n"))
    cell("gate", "hash_then_blank", data(b"#a\n\n"))
    for after in range(1, 8):
        lines = ["S1", "S2", "S3", "S1", "S2", "S3", "S1"][:after]
        lines[after - 1] = "S9\tP"
        content = ("SAMPLE_ID\n" + "\n".join(lines) + "\n").encode()
        cell("gate", f"tab_after_{after}", data(content))
        cell("gate", f"tab_after_{after}_cr", data(content.replace(b"\n", b"\r")))
        unterminated = ("SAMPLE_ID\n" + "\n".join(lines)).encode()
        cell("gate", f"tab_after_{after}_unterminated", data(unterminated))
    for after in (4, 5, 6):
        filler = ["#x", "", "S1", "S2", "S3", "S1"][: after - 1]
        content = ("SAMPLE_ID\n" + "\n".join([*filler, "S9\tP"]) + "\n").encode()
        cell("gate", f"tab_after_hash_blank_{after}", data(content))
    cell("gate", "tab_in_top_line", data(b"#a\tb\nSAMPLE_ID\nS1\n"))
    # a tab on a '#' line inside the window: the sniffer sees it (mutation pass, review of 2a)
    cell("gate", "tab_in_hash_after_header", data(b"SAMPLE_ID\n#a\tb\nS1\n"))
    cell("gate", "tab_in_hash_sixth", data(b"SAMPLE_ID\nS1\nS2\nS3\nS1\nS2\n#a\tb\n"))


# --- header ---------------------------------------------------------------------------------------


def _header() -> None:
    def named(*names: str, rows: Sequence[Sequence[str]] = ()) -> bytes:
        body = rows or [("S1", *("P" for _ in names[1:]))]
        return tsv([names, *body])

    cell("header", "first_not_sample", data(named("mutations", "SAMPLE_ID", rows=[("P", "S1")])))
    cell("header", "sample_lower", data(named("sample_id", "mutations")))
    cell("header", "sample_title", data(named("Sample_Id", "mutations")))
    cell("header", "mutations_case", data(named("SAMPLE_ID", "Mutations")))
    cell("header", "unknown_name", data(named("SAMPLE_ID", "mutations", "foo")))
    cell("header", "unknown_only", data(named("SAMPLE_ID", "foo")))
    cell("header", "empty_name", data(named("SAMPLE_ID", "", "mutations")))
    cell("header", "trailing_tab", data(tsv([(*HEADER, ""), ("S1", "P", "")])))
    cell("header", "repeated_name", data(named("SAMPLE_ID", "mutations", "mutations")))
    cell("header", "sample_twice", data(named("SAMPLE_ID", "SAMPLE_ID", rows=[("S1", "S1")])))
    cell("header", "too_many", data(named("SAMPLE_ID", "mutations", "a", "b", "c")))
    cell("header", "quoted_name", data(named("SAMPLE_ID", '"mutations"')))
    cell("header", "quoted_sample", data(named('"SAMPLE_ID"', "mutations")))
    cell("header", "blank_header_then_valid", data(b"\n" + VALID))
    cell("header", "sample_only_tabbed_rows", data(b"SAMPLE_ID\nS1\tP\n"))
    for name, at in (("SAMPLE_ID", 0), ("mutations", 1)):
        for pad in (*STRIPPED[:1], *STRIPPED[2:], *NON_STRIPPED):
            for side in ("pre", "post"):
                names: list[str] = list(HEADER)
                names[at] = pad + name if side == "pre" else name + pad
                cell("header", f"pad_{name}_{side}_{_label(pad)}", data(named(*names)))


# --- cell -----------------------------------------------------------------------------------------

TOKENS = (
    ("NA", "NA"),
    ("na", "na"),
    ("Na", "Na"),
    ("nA", "nA"),
    ("NaN", "NaN"),
    ("nan", "nan"),
    ("n_a", "N/A"),
    ("dash", "-"),
    ("empty", ""),
    ("space", " "),
    ("nbsp", "\xa0"),
    ("wgs", "WGS"),
    ("wxs", "WXS"),
    ("wxs_wgs", "WXS/WGS"),
    ("zero", "0"),
    ("quoted", '"IMPACT1"'),
    ("single_quoted", "'IMPACT1'"),
    ("half_quoted", 'IMPACT1"'),
    ("zwsp", "IMP\u200bACT"),
    ("fullwidth_na", "\uff2e\uff21"),
    ("long_id", "P" * 5_000),
)


def _cells() -> None:
    off = BASE.case_list("sequenced", ["S1", "S2"])
    for label, token in TOKENS:
        cell("cell", f"{label}_on", data(tsv([HEADER, R1, R2, ("S3", token)])))
        cell(
            "cell",
            f"{label}_off",
            data(tsv([HEADER, R1, R2, ("S3", token)]), case_lists__cases_sequenced_DOT_txt=off),
        )
    for base_label, base in (("panel", "IMPACT1"), ("na", "NA")):
        for pad in (*STRIPPED[:1], *STRIPPED[2:], *NON_STRIPPED):
            for side in ("pre", "post", "both"):
                token = {"pre": pad + base, "post": base + pad, "both": pad + base + pad}[side]
                case = f"pad_{base_label}_{side}_{_label(pad)}"
                cell("cell", f"{case}_on", data(tsv([HEADER, R1, R2, ("S3", token)])))
                cell(
                    "cell",
                    f"{case}_off",
                    data(
                        tsv([HEADER, R1, R2, ("S3", token)]),
                        case_lists__cases_sequenced_DOT_txt=off,
                    ),
                )
    cell(
        "cell",
        "all_na_off",
        data(
            tsv([HEADER, ("S1", "NA"), ("S2", "NA"), ("S3", "NA")]),
            case_lists__cases_sequenced_DOT_txt=BASE.case_list("sequenced", ["S1"]),
        ),
    )
    cell(
        "cell",
        "first_row_off",
        data(VALID, case_lists__cases_sequenced_DOT_txt=BASE.case_list("sequenced", ["S2", "S3"])),
    )


# --- sample ---------------------------------------------------------------------------------------


def _samples() -> None:
    for panel_label, panel in (("panel", "P"), ("na", "NA")):
        for label, rows in (
            ("dup", [("S1", "P"), ("S1", panel)]),
            ("dup_first_na", [("S1", "NA"), ("S1", panel)]),
            ("dup_later", [("S1", "P"), ("S2", "P"), ("S1", panel)]),
            ("dup_padded", [("S1", "P"), (" S1", panel)]),
            ("dup_case", [("S1", "P"), ("s1", panel)]),
            ("unknown", [("S1", "P"), ("S9", panel)]),
            ("unknown_first", [("S9", panel), ("S1", "P")]),
            ("unknown_then_dup", [("S9", panel), ("S9", panel)]),
            ("lower", [("s1", panel)]),
            ("quoted", [('"S1"', panel)]),
            ("blank", [("", panel)]),
            ("space", [(" ", panel)]),
            ("short_dup", [("S1", "P"), ("S1",)]),
            ("short_unknown", [("S9",), ("S1", "P")]),
            ("short_known", [("S1",), ("S2", "P")]),
        ):
            cell("sample", f"{label}_{panel_label}", data(tsv([HEADER, *rows])))
    for pad in (*STRIPPED[:1], *STRIPPED[2:], *NON_STRIPPED):
        for side in ("pre", "post"):
            sample = pad + "S1" if side == "pre" else "S1" + pad
            cell("sample", f"pad_{side}_{_label(pad)}", data(tsv([HEADER, (sample, "P"), R2])))
    cell("sample", "missing_some", data(tsv([HEADER, R1])))
    cell("sample", "none", data(tsv([HEADER])))


# --- seq ------------------------------------------------------------------------------------------


def _seq() -> None:
    other = BASE.case_list("other_sequenced", ["S1", "S2", "S3"])
    cell("seq", "no_list", study(case_lists__cases_sequenced_DOT_txt=None))
    cell(
        "seq",
        "no_list_bad_matrix",
        study(
            case_lists__cases_sequenced_DOT_txt=None,
            data_gene_panel_matrix_DOT_txt=tsv([HEADER, ("S9", "P")]),
        ),
    )
    cell("seq", "two_lists_alike", study(case_lists__cases_x_DOT_txt=other))
    cell(
        "seq",
        "suffix_only_list",
        study(case_lists__cases_x_DOT_txt=BASE.case_list("x_sequenced", ["S1", "S2", "S3"])),
    )
    no_maf = {"meta_mutations_DOT_txt": None, "data_mutations_DOT_txt": None}
    cell("seq", "no_maf", study(**no_maf))
    cell("seq", "no_maf_no_list", study(**no_maf, case_lists__cases_sequenced_DOT_txt=None))
    cell(
        "seq",
        "no_maf_matrix_stable_mutations",
        study(
            **no_maf,
            meta_gene_panel_matrix_DOT_txt=meta(extra="stable_id: mutations\n"),
            case_lists__cases_sequenced_DOT_txt=BASE.case_list("sequenced", ["S1"]),
        ),
    )
    cell(
        "seq",
        "no_maf_matrix_stable_mutations_short",
        study(
            **no_maf,
            meta_gene_panel_matrix_DOT_txt=meta(extra="stable_id: mutations\n"),
            data_gene_panel_matrix_DOT_txt=tsv([HEADER, R1, ("S2",)]),
        ),
    )
    cell(
        "seq",
        "no_maf_clinical_stable_mutations",
        study(
            **no_maf,
            meta_clinical_sample_DOT_txt=BASE.SAMPLE_META + b"stable_id: mutations\n",
            case_lists__cases_sequenced_DOT_txt=BASE.case_list("sequenced", ["S1"]),
        ),
    )
    cell(
        "seq",
        "maf_absent_column",
        data(
            tsv([("SAMPLE_ID",), ("S1",)]),
            meta_gene_panel_matrix_DOT_txt=meta(extra="stable_id: gpm\n"),
        ),
    )


# --- dict -----------------------------------------------------------------------------------------

RESOURCE_DEFINITION = (
    b"RESOURCE_ID\tRESOURCE_TYPE\tDISPLAY_NAME\nRS\tSAMPLE\tR s\nRP\tPATIENT\tR p\nRT\tSTUDY\tR t\n"
)
RESOURCES = {
    "DEFINITION": ("data_resource_definition.txt", RESOURCE_DEFINITION),
    "SAMPLE": (
        "data_resource_sample.txt",
        b"SAMPLE_ID\tPATIENT_ID\tRESOURCE_ID\tURL\nS1\tP1\tRS\thttps://e.org/a\n",
    ),
    "PATIENT": (
        "data_resource_patient.txt",
        b"PATIENT_ID\tRESOURCE_ID\tURL\nP1\tRP\thttps://e.org/b\n",
    ),
    "STUDY": ("data_resource_study.txt", b"RESOURCE_ID\tURL\nRT\thttps://e.org/c\n"),
}


def resource_meta(kind: str, extra: bytes = b"", lead: bytes = b"") -> bytes:
    name = RESOURCES[kind][0]
    return (
        lead
        + (
            f"cancer_study_identifier: {SID}\nresource_type: {kind}\ndata_filename: {name}\n"
        ).encode()
        + extra
    )


def resources(on: str | None = None, extra: bytes = b"", lead: bytes = b"") -> dict[str, Content]:
    """The four resource metas and their data files (as ``study``'s keys), ``extra`` added to the
    meta of type ``on`` and ``lead`` written before its lines."""
    found: dict[str, Content] = {}
    for kind, (name, content) in RESOURCES.items():
        mine = kind == on
        found[f"meta_resource_{kind.lower()}_DOT_txt"] = resource_meta(
            kind, extra if mine else b"", lead if mine else b""
        )
        found[name.replace(".", "_DOT_")] = content
    return found


MATRICES = {
    "tabbed": VALID,
    "not_utf8": b"SAMPLE_ID\tmutations\nS1\tP\xff\n",
    "empty": b"",
    "hash_only": b"#a\n",
    "newline_only": b"\n",
    "onecol": b"SAMPLE_ID\nS1\nS2\n",
    "tab_after_5": b"SAMPLE_ID\nS1\nS2\nS3\nS1\nS9\tP\n",
    "tab_after_6": b"SAMPLE_ID\nS1\nS2\nS3\nS1\nS2\nS9\tP\n",
    "header_only": tsv([HEADER]),
    "bad_header": tsv([("mutations", "SAMPLE_ID"), ("P", "S1")]),
    "bom_tabbed": BOM + VALID,
    "control": tsv([HEADER, ("S1", "P\x01")]),
}
"""The matrix's early exits (V:437-496, V:511) and two later faults."""
GAT = "genetic_alteration_type"
VARIANTS: dict[str, tuple[bytes, bytes]] = {
    "sid": (b"stable_id: foo\n", b""),
    "sid_gat_mutext": (b"genetic_alteration_type: MUTATION_EXTENDED\nstable_id: foo\n", b""),
    "sid_gat_mutext_padded": (
        b"genetic_alteration_type:   MUTATION_EXTENDED \nstable_id: foo\n",
        b"",
    ),
    "sid_gat_mutext_lower": (b"genetic_alteration_type: mutation_extended\nstable_id: foo\n", b""),
    "sid_gat_other": (b"genetic_alteration_type: CLINICAL\nstable_id: foo\n", b""),
    "gat_mutext_no_sid": (b"genetic_alteration_type: MUTATION_EXTENDED\n", b""),
    "no_sid": (b"", b""),
    "sid_key_space": (b" stable_id: foo\n", b""),
    "sid_key_trailing_space": (b"stable_id : foo\n", b""),
    "gat_key_space": (b" genetic_alteration_type: X\nstable_id: foo\n", b""),
    "sid_gene_panel": (b"stable_id: foo\ngene_panel: P\n", b""),
}
"""A study or resource meta's added lines (``extra``, after its own) and what leads it."""
LEAD_VARIANTS: dict[str, tuple[bytes, bytes]] = {
    "bom_sid": (b"", BOM + b"stable_id: foo\n"),
    "bom_gat": (b"stable_id: foo\n", BOM + b"genetic_alteration_type: X\n"),
    "bom_gat_no_sid": (b"", BOM + b"genetic_alteration_type: X\n"),
    "bom_gat_mutext": (b"stable_id: foo\n", BOM + b"genetic_alteration_type: MUTATION_EXTENDED\n"),
    "bom_blank": (b"stable_id: foo\n", BOM + b"\n"),
}


def _dicts() -> None:
    for variant, (extra, lead) in {**VARIANTS, **LEAD_VARIANTS}.items():
        for exit_, matrix in MATRICES.items():
            content = lead + BASE.STUDY + extra
            cell(
                "dict",
                f"study_{variant}_{exit_}",
                data(matrix, meta_study_DOT_txt=content),
            )
    kept = ("sid", "sid_gat_mutext", "bom_sid", "bom_gat", "sid_gat_other", "no_sid")
    exits = ("tabbed", "onecol", "header_only", "tab_after_5", "tab_after_6", "not_utf8")
    for kind in RESOURCES:
        for variant in kept:
            extra, lead = {**VARIANTS, **LEAD_VARIANTS}[variant]
            for exit_ in exits:
                cell(
                    "dict",
                    f"resource_{kind.lower()}_{variant}_{exit_}",
                    data(MATRICES[exit_], **resources(kind, extra, lead)),
                )
    cell("dict", "resources_plain", study(**resources()))
    cell(
        "dict",
        "study_and_resource_same_sid",
        study(
            meta_study_DOT_txt=BASE.STUDY + b"stable_id: foo\n",
            **resources("DEFINITION", b"stable_id: foo\n"),
        ),
    )
    cell(
        "dict",
        "study_sid_column",
        data(
            tsv([("SAMPLE_ID", "mutations", "foo"), ("S1", "P", "P")]),
            meta_study_DOT_txt=BASE.STUDY + b"stable_id: foo\n",
        ),
    )
    cell(
        "dict",
        "no_matrix_study_sid",
        study(
            meta_study_DOT_txt=BASE.STUDY + b"stable_id: foo\n",
            meta_gene_panel_matrix_DOT_txt=None,
            data_gene_panel_matrix_DOT_txt=None,
        ),
    )
    cell(
        "dict",
        "bom_maf_meta",
        study(meta_mutations_DOT_txt=BOM + BASE.META.encode()),
    )
    cell(
        "dict",
        "bom_matrix_meta",
        study(meta_gene_panel_matrix_DOT_txt=BOM + meta()),
    )
    cell(
        "dict",
        "bom_matrix_meta_sid_first",
        study(meta_gene_panel_matrix_DOT_txt=BOM + b"stable_id: gpm\n" + meta()),
    )


# --- meta -----------------------------------------------------------------------------------------

CNA_META = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
    "datatype: DISCRETE\nstable_id: cna\nshow_profile_in_analysis_tab: true\n"
    "profile_name: CNA\nprofile_description: CNA\ndata_filename: data_cna.txt\n"
).encode()
CNA = b"Hugo_Symbol\tS1\tS2\tS3\nTP53\t0\t2\tNA\n"
CNA_LIST = BASE.case_list("cna", list(SAMPLES), category="all_cases_with_cna_data")
SV_META = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: STRUCTURAL_VARIANT\n"
    "datatype: SV\nstable_id: structural_variants\nshow_profile_in_analysis_tab: true\n"
    "profile_name: SV\nprofile_description: SV\ndata_filename: data_sv.txt\n"
).encode()
SV = b"Sample_Id\tSV_Status\tSite1_Hugo_Symbol\tSite2_Hugo_Symbol\nS1\tSOMATIC\tEML4\tALK\n"


def with_cna(**over: Content | None) -> Cell:
    files: dict[str, Content | None] = {
        "meta_cna_DOT_txt": CNA_META,
        "data_cna_DOT_txt": CNA,
        "case_lists__cases_cna_DOT_txt": CNA_LIST,
    }
    return study(**{**files, **over})


def with_sv(**over: Content | None) -> Cell:
    files: dict[str, Content | None] = {"meta_sv_DOT_txt": SV_META, "data_sv_DOT_txt": SV}
    return study(**{**files, **over})


def _metas() -> None:
    cell(
        "meta",
        "second_meta",
        study(meta_gene_panel_matrix2_DOT_txt=meta("data_gpm2.txt"), data_gpm2_DOT_txt=VALID),
    )
    cell("meta", "second_meta_same_file", study(meta_gene_panel_matrix2_DOT_txt=meta()))
    cell(
        "meta",
        "second_meta_bad",
        study(meta_gene_panel_matrix2_DOT_txt=meta("data_gpm2.txt"), data_gpm2_DOT_txt=b"x\n"),
    )
    cell(
        "meta",
        "names_maf",
        study(
            meta_gene_panel_matrix_DOT_txt=meta("data_mutations.txt"),
            data_gene_panel_matrix_DOT_txt=None,
        ),
    )
    cell(
        "meta",
        "names_samples",
        study(
            meta_gene_panel_matrix_DOT_txt=meta("data_clinical_sample.txt"),
            data_gene_panel_matrix_DOT_txt=None,
        ),
    )
    cell(
        "meta",
        "names_meta",
        study(
            meta_gene_panel_matrix_DOT_txt=meta("meta_study.txt"),
            data_gene_panel_matrix_DOT_txt=None,
        ),
    )
    cell(
        "meta",
        "names_cna",
        with_cna(
            meta_gene_panel_matrix_DOT_txt=meta("data_cna.txt"), data_gene_panel_matrix_DOT_txt=None
        ),
    )
    cell(
        "meta",
        "names_sv",
        with_sv(
            meta_gene_panel_matrix_DOT_txt=meta("data_sv.txt"), data_gene_panel_matrix_DOT_txt=None
        ),
    )
    cell(
        "meta",
        "names_folded",
        study(
            meta_gene_panel_matrix_DOT_txt=meta("DATA_MUTATIONS.txt"),
            data_gene_panel_matrix_DOT_txt=None,
        ),
    )
    cell("meta", "names_missing", study(meta_gene_panel_matrix_DOT_txt=meta("nothere.txt")))
    cell(
        "meta",
        "names_subdirectory",
        study(meta_gene_panel_matrix_DOT_txt=meta("sub/data.txt"), sub__data_DOT_txt=VALID),
    )
    cell("meta", "names_case_lists", study(meta_gene_panel_matrix_DOT_txt=meta("case_lists")))
    cell(
        "meta",
        "no_data_filename",
        study(
            meta_gene_panel_matrix_DOT_txt=meta().replace(
                b"data_filename: data_gene_panel_matrix.txt\n", b""
            )
        ),
    )
    cell(
        "meta",
        "no_datatype",
        study(meta_gene_panel_matrix_DOT_txt=meta().replace(b"datatype: GENE_PANEL_MATRIX\n", b"")),
    )
    cell("meta", "crlf", study(meta_gene_panel_matrix_DOT_txt=meta().replace(b"\n", b"\r\n")))
    cell(
        "meta",
        "repeated_key_last",
        study(
            meta_gene_panel_matrix_DOT_txt=meta("nothere.txt")
            + b"data_filename: data_gene_panel_matrix.txt\n"
        ),
    )
    cell(
        "meta",
        "repeated_key_bad_last",
        study(meta_gene_panel_matrix_DOT_txt=meta() + b"data_filename: nothere.txt\n"),
    )
    cell("meta", "extra_key", study(meta_gene_panel_matrix_DOT_txt=meta(extra="profile_name: x\n")))
    cell(
        "meta",
        "stable_id_column",
        data(
            tsv([("SAMPLE_ID", "mutations", "gpm"), ("S1", "P", "P")]),
            meta_gene_panel_matrix_DOT_txt=meta(extra="stable_id: gpm\n"),
        ),
    )
    cell(
        "meta",
        "stable_id_mutations",
        study(meta_gene_panel_matrix_DOT_txt=meta(extra="stable_id: mutations\n")),
    )
    cell(
        "meta",
        "gene_panel_key",
        study(meta_gene_panel_matrix_DOT_txt=meta(extra="gene_panel: P\n")),
    )
    cell(
        "meta",
        "gene_panel_key_sid_column",
        data(
            tsv([("SAMPLE_ID", "mutations", "gpm"), ("S1", "P", "P")]),
            meta_gene_panel_matrix_DOT_txt=meta(extra="stable_id: gpm\ngene_panel: P\n"),
        ),
    )
    cell(
        "meta",
        "maf_gene_panel",
        study(meta_mutations_DOT_txt=(BASE.META + "gene_panel: IMPACT468\n").encode()),
    )
    cell(
        "meta",
        "maf_gene_panel_no_column",
        data(
            tsv([("SAMPLE_ID", "cna"), ("S1", "P")]),
            meta_mutations_DOT_txt=(BASE.META + "gene_panel: IMPACT468\n").encode(),
            meta_cna_DOT_txt=CNA_META,
            data_cna_DOT_txt=CNA,
            case_lists__cases_cna_DOT_txt=CNA_LIST,
        ),
    )
    cell(
        "meta",
        "clinical_gene_panel_sid_column",
        data(
            tsv([("SAMPLE_ID", "mutations", "bar"), ("S1", "P", "P")]),
            meta_clinical_sample_DOT_txt=BASE.SAMPLE_META + b"stable_id: bar\ngene_panel: P\n",
        ),
    )
    cell(
        "meta",
        "clinical_sid_column",
        data(
            tsv([("SAMPLE_ID", "mutations", "bar"), ("S1", "P", "P")]),
            meta_clinical_sample_DOT_txt=BASE.SAMPLE_META + b"stable_id: bar\n",
        ),
    )
    cell(
        "meta",
        "timeline_names_matrix",
        study(
            meta_timeline_DOT_txt=(
                f"cancer_study_identifier: {SID}\ngenetic_alteration_type: CLINICAL\n"
                "datatype: TIMELINE\ndata_filename: data_gene_panel_matrix.txt\n"
            ).encode()
        ),
    )
    cell(
        "meta",
        "cna_names_matrix",
        study(
            meta_cna_DOT_txt=CNA_META.replace(b"data_cna.txt", b"data_gene_panel_matrix.txt"),
            case_lists__cases_cna_DOT_txt=CNA_LIST,
        ),
    )
    cell(
        "meta",
        "name_not_text",
        study(
            meta_gene_panel_matrix_DOT_txt=meta("data_\ufdd0.txt"),
            data_gene_panel_matrix_DOT_txt=None,
            **{"data_\ufdd0_DOT_txt": VALID},
        ),
    )


# --- beside ---------------------------------------------------------------------------------------


def _beside() -> None:
    three = ("SAMPLE_ID", "mutations", "cna", "structural_variants")
    both = {"meta_sv_DOT_txt": SV_META, "data_sv_DOT_txt": SV}
    cell("beside", "cna", with_cna())
    cell(
        "beside",
        "cna_column",
        with_cna(
            data_gene_panel_matrix_DOT_txt=tsv(
                [("SAMPLE_ID", "mutations", "cna"), ("S1", "P", "P"), ("S2", "P", "NA")]
            )
        ),
    )
    cell(
        "beside",
        "cna_column_only",
        with_cna(
            data_gene_panel_matrix_DOT_txt=tsv([("SAMPLE_ID", "cna"), ("S1", "P"), ("S2", "P")])
        ),
    )
    cell(
        "beside",
        "cna_column_six_sample_shape",
        with_cna(
            data_gene_panel_matrix_DOT_txt=tsv(
                [
                    ("SAMPLE_ID", "mutations", "cna"),
                    ("S1", "P", "P"),
                    ("S2", "P", "P"),
                    ("S3", "NA", "P"),
                ]
            ),
            case_lists__cases_cna_DOT_txt=BASE.case_list(
                "cna", ["S1", "S2"], category="all_cases_with_cna_data"
            ),
            data_cna_DOT_txt=b"Hugo_Symbol\tS1\tS2\nTP53\t0\t2\n",
        ),
    )
    cell(
        "beside",
        "cna_column_gene_panel",
        with_cna(
            meta_cna_DOT_txt=CNA_META + b"gene_panel: P\n",
            data_gene_panel_matrix_DOT_txt=tsv(
                [("SAMPLE_ID", "mutations", "cna"), ("S1", "P", "P")]
            ),
        ),
    )
    cell(
        "beside",
        "cna_gene_panel_no_column",
        with_cna(meta_cna_DOT_txt=CNA_META + b"gene_panel: P\n"),
    )
    # the MAF's column not last (mutation pass): an NA there, a panel in the next
    off = BASE.case_list("sequenced", ["S1", "S2"])
    first = tsv(
        [("SAMPLE_ID", "mutations", "cna"), ("S1", "P", "P"), ("S2", "P", "P"), ("S3", "NA", "P")]
    )
    cell(
        "beside",
        "mutations_not_last",
        with_cna(data_gene_panel_matrix_DOT_txt=first, case_lists__cases_sequenced_DOT_txt=off),
    )
    cell(
        "beside",
        "sv_column",
        with_sv(
            data_gene_panel_matrix_DOT_txt=tsv(
                [("SAMPLE_ID", "mutations", "structural_variants"), ("S1", "P", "P")]
            )
        ),
    )
    cell(
        "beside",
        "all_columns",
        with_cna(
            **both,
            data_gene_panel_matrix_DOT_txt=tsv(
                [three, ("S1", "P", "P", "P"), ("S2", "P", "NA", "NA"), ("S3", "NA", "NA", "NA")]
            ),
        ),
    )
    cell(
        "beside",
        "all_columns_sv_gene_panel",
        with_cna(
            meta_sv_DOT_txt=SV_META + b"gene_panel: P\n",
            data_sv_DOT_txt=SV,
            data_gene_panel_matrix_DOT_txt=tsv([three, ("S1", "P", "P", "P")]),
        ),
    )
    cell(
        "beside",
        "cna_short_row_no_crash",
        with_cna(
            data_gene_panel_matrix_DOT_txt=tsv([("SAMPLE_ID", "cna"), ("S1",), ("S2", "P")]),
            meta_mutations_DOT_txt=None,
            data_mutations_DOT_txt=None,
        ),
    )
    bad_matrix = tsv([HEADER, ("S9", "P")])
    bad_maf = BASE.maf([BASE.R1, {**BASE.R2, "Tumor_Sample_Barcode": "S9"}])
    cell(
        "beside",
        "bad_matrix_bad_maf",
        study(data_gene_panel_matrix_DOT_txt=bad_matrix, data_mutations_DOT_txt=bad_maf),
    )
    cell("beside", "good_matrix_bad_maf", study(data_mutations_DOT_txt=bad_maf))
    cell(
        "beside",
        "bad_matrix_bad_cna",
        with_cna(
            data_gene_panel_matrix_DOT_txt=bad_matrix,
            data_cna_DOT_txt=b"Hugo_Symbol\tS9\nTP53\t2\n",
        ),
    )
    cell("beside", "good_matrix_bad_cna", with_cna(data_cna_DOT_txt=b"Hugo_Symbol\tS9\nTP53\t2\n"))
    cell(
        "beside",
        "bad_matrix_bad_sv",
        with_sv(
            data_gene_panel_matrix_DOT_txt=bad_matrix, data_sv_DOT_txt=SV.replace(b"S1\t", b"S9\t")
        ),
    )
    cell(
        "beside",
        "bad_matrix_bad_list",
        study(
            data_gene_panel_matrix_DOT_txt=bad_matrix,
            case_lists__cases_x_DOT_txt=BASE.case_list("x", ["S9"]),
        ),
    )
    cell(
        "beside",
        "bad_matrix_no_cna_list",
        with_cna(data_gene_panel_matrix_DOT_txt=bad_matrix, case_lists__cases_cna_DOT_txt=None),
    )
    cell("beside", "maf_sample_not_in_matrix", data(tsv([HEADER, R1])))


# --- type -----------------------------------------------------------------------------------------

TYPE_VALUES = {
    "cancer_study_identifier": SID,
    "show_profile_in_analysis_tab": "false",
    "reference_genome_id": "hg19",
    "geneset_def_version": "1",
    "source_stable_id": "s_source",
    "generic_assay_type": "MUTATIONAL_SIGNATURE",
    "profile_name": "x",
    "profile_description": "x",
    "description": "x",
}
TYPES: dict[str, tuple[str, ...]] = {
    "CANCER_TYPE:CANCER_TYPE": ("genetic_alteration_type", "datatype", "data_filename"),
    "CLINICAL:TIMELINE": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "data_filename",
    ),
    "PROTEIN_LEVEL:Z-SCORE": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "COPY_NUMBER_ALTERATION:LOG2-VALUE": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "COPY_NUMBER_ALTERATION:SEG": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "reference_genome_id",
        "data_filename",
        "description",
    ),
    "MRNA_EXPRESSION:Z-SCORE": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "METHYLATION:CONTINUOUS": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "show_profile_in_analysis_tab",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "MUTATION_UNCALLED:MAF": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "datatype",
        "stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
    ),
    "GENERIC_ASSAY:LIMIT-VALUE": (
        "cancer_study_identifier",
        "genetic_alteration_type",
        "generic_assay_type",
        "datatype",
        "stable_id",
        "profile_name",
        "profile_description",
        "data_filename",
        "show_profile_in_analysis_tab",
    ),
}
"""Some types of the C table the pack never imports, with their mandatory keys (C:94-371)."""
STABLE_FOR = {
    "PROTEIN_LEVEL:Z-SCORE": "rppa_Zscores",
    "COPY_NUMBER_ALTERATION:LOG2-VALUE": "log2CNA",
    "MRNA_EXPRESSION:Z-SCORE": "mrna_median_Zscores",
    "METHYLATION:CONTINUOUS": "methylation_hm27",
    "MUTATION_UNCALLED:MAF": "mutations_uncalled",
    "GENERIC_ASSAY:LIMIT-VALUE": "ga_sig",
}


def type_meta(pair: str, keys: Sequence[str], value: str = "d_type.txt") -> bytes:
    kind, _, datatype = pair.partition(":")
    values = {**TYPE_VALUES, "genetic_alteration_type": kind, "datatype": datatype}
    values["stable_id"] = STABLE_FOR.get(pair, "x")
    values["data_filename"] = value
    return ("".join(f"{key}: {values[key]}\n" for key in keys)).encode()


def _types() -> None:
    for pair, keys in TYPES.items():
        label = "".join(c if c.isalnum() else "_" for c in pair.lower())
        found = type_meta(pair, keys)
        cell("type", f"{label}_beside", study(meta_type_DOT_txt=found, d_type_DOT_txt=b"x\n"))
        stable = STABLE_FOR.get(pair)
        if stable is not None:
            matrix = tsv([("SAMPLE_ID", "mutations", stable), ("S1", "P", "P"), ("S2", "P", "NA")])
            cell(
                "type",
                f"{label}_column",
                study(
                    meta_type_DOT_txt=found,
                    d_type_DOT_txt=b"x\n",
                    data_gene_panel_matrix_DOT_txt=matrix,
                ),
            )
            cell(
                "type",
                f"{label}_column_gene_panel",
                study(
                    meta_type_DOT_txt=found + b"gene_panel: P\n",
                    d_type_DOT_txt=b"x\n",
                    data_gene_panel_matrix_DOT_txt=matrix,
                ),
            )
    for index, extras in enumerate(COLLISIONS):
        over: dict[str, Content] = {
            "meta_gene_panel_matrix_DOT_txt": meta(extra=f"stable_id: {extras[0]}\n")
        }
        if len(extras) > 1:
            over["meta_clinical_sample_DOT_txt"] = (
                BASE.SAMPLE_META + f"stable_id: {extras[1]}\n".encode()
            )
        names = ("SAMPLE_ID", "mutations", *extras)
        matrix = tsv([names, ("S1", "P", *("P" for _ in extras))])
        cell("type", f"names_{index}", study(**over, data_gene_panel_matrix_DOT_txt=matrix))


COLLISIONS: tuple[tuple[str, ...], ...] = (
    ("Mutations",),
    ("a-b", "a_b"),
    ("sample_id",),
    ("1a", "c_1a"),
    ("x" * 70, "x" * 64),
    ("()", "c_3"),
    ("x" * 5_000,),
    ("x" * 4_096,),
    ("y" * 4_096, "y" * 4_097),
    ("MUTATIONS",),
    ("a.b",),
    ("a.b", "a_b"),
)
"""Stable ids for the matrix meta and the sample meta, each a column of the matrix beside
``mutations``: names whose column ids collide (D447's stricter item, plan r4 m8), a name longer
than a descriptor's string, and names that only near one."""


# --- typing ---------------------------------------------------------------------------------------


def _tables() -> ModuleType:
    """Slice 3's oracle, for the C tables alone (it imports nothing of the pack)."""
    path = Path(__file__).resolve().parent / "cna_oracle.py"
    spec = importlib.util.spec_from_file_location("cna_oracle_for_panels_corpus", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMPORTED_TYPES = frozenset(
    {
        "SAMPLE_ATTRIBUTES",
        "PATIENT_ATTRIBUTES",
        "MUTATION",
        "STRUCTURAL_VARIANT",
        "CNA_DISCRETE",
        "GENE_PANEL_MATRIX",
    }
)
VALID_IDS = {
    "COPY_NUMBER_ALTERATION:DISCRETE_LONG": "gistic",
    "COPY_NUMBER_ALTERATION:CONTINUOUS": "linear_CNA",
    "COPY_NUMBER_ALTERATION:LOG2-VALUE": "log2CNA",
    "MRNA_EXPRESSION:CONTINUOUS": "mrna",
    "MRNA_EXPRESSION:Z-SCORE": "mrna_median_Zscores",
    "MRNA_EXPRESSION:DISCRETE": "mrna_outliers",
    "MUTATION_UNCALLED:MAF": "mutations_uncalled",
    "METHYLATION:CONTINUOUS": "methylation_hm27",
    "PROTEIN_LEVEL:LOG2-VALUE": "rppa",
    "PROTEIN_LEVEL:Z-SCORE": "rppa_Zscores",
    "PROTEIN_LEVEL:CONTINUOUS": "protein_quantification",
    "GENESET_SCORE:GSVA-SCORE": "gsva_scores",
    "GENESET_SCORE:P-VALUE": "gsva_pvalues",
    "GENERIC_ASSAY:LIMIT-VALUE": "ga_limit",
    "GENERIC_ASSAY:BINARY": "ga_binary",
    "GENERIC_ASSAY:CATEGORICAL": "ga_categorical",
}
"""For each pair whose type requires a ``stable_id``, an id the type and id combination allows
(C:738-791, ``*`` for the generic assays)."""
TYPING_VALUES = {
    "cancer_study_identifier": SID,
    "show_profile_in_analysis_tab": "false",
    "reference_genome_id": "hg19",
    "geneset_def_version": "1",
    "source_stable_id": "s_source",
    "generic_assay_type": "MUTATIONAL_SIGNATURE",
    "profile_name": "x",
    "profile_description": "x",
    "description": "x",
    "data_filename": "d_typed.txt",
}
BLANKS = tuple(chr(p) for p in range(0x110000) if chr(p).strip() == "" and chr(p) not in "\n\r")
"""Every character ``str.strip()`` removes but the two line ends: a line of one is blank to the
reference (C:858) and to ``meta.parse`` alike."""


def typed_lines(pair: str, keys: Sequence[str], stable: str) -> list[str]:
    """A meta of the pair's type: its mandatory ``keys`` in order and its ``stable_id``."""
    kind, _, datatype = pair.partition(":")
    values = {**TYPING_VALUES, "genetic_alteration_type": kind, "datatype": datatype}
    values["stable_id"] = stable
    lines = [f"{key}: {values[key]}" for key in keys]
    if "stable_id" not in keys:
        lines.append(f"stable_id: {stable}")
    if kind == "GENERIC_ASSAY":
        lines.append("generic_entity_meta_properties: NAME")
    return lines


def _typing() -> None:
    """Review r1 MAJOR-1: every rule by which the reference reads and types a meta (C:852-989),
    for each pair of the C table the pack never imports, the meta's id a column of the matrix:
    kept; with ``gene_panel``, and with a padded `` gene_panel`` key; a padded ``stable_id`` key;
    each of its lines led by a byte order mark (the key it holds lost to the reference); a type and
    id combination the reference refuses; a segment or GISTIC genome other than hg19 and hg38; a
    ``swissprot_identifier`` the reference refuses and one it takes; and a blank line of every
    character ``str.strip()`` removes, beside the meta and on a resource meta the reference's
    header check crashes on."""
    tables = _tables()
    pairs = {pair: kind for pair, kind in tables.PAIR_TYPES.items() if kind not in IMPORTED_TYPES}
    for pair, kind in sorted(pairs.items()):
        label = "".join(c if c.isalnum() else "_" for c in pair.lower())
        keys = tables.MANDATORY[kind]
        stable = VALID_IDS.get(pair, f"s_{label}")
        lines = typed_lines(pair, keys, stable)
        matrix = tsv([("SAMPLE_ID", "mutations", stable), ("S1", "P", "P"), ("S2", "P", "NA")])

        def put(
            case: str,
            written: Sequence[str],
            lead: bytes = b"",
            matrix: bytes = matrix,
            label: str = label,
        ) -> None:
            content = lead + ("\n".join(written) + "\n").encode()
            files = {
                "meta_typed_DOT_txt": content,
                "d_typed_DOT_txt": b"x\n",
                "data_gene_panel_matrix_DOT_txt": matrix,
            }
            cell("typing", f"{label}_{case}", study(**files))

        put("kept", lines)
        put("gene_panel", [*lines, "gene_panel: P"])
        put("gene_panel_padded_key", [*lines, " gene_panel: P"])
        put("stable_id_padded_key", [line.replace("stable_id:", " stable_id:") for line in lines])
        for at, line in enumerate(lines):
            key = line.partition(":")[0]
            put(f"bom_{key}", [line, *lines[:at], *lines[at + 1 :]], BOM)
        if "stable_id" in keys:
            bad = [line.replace(f"stable_id: {stable}", "stable_id: s_bad") for line in lines]
            put("bad_combination", bad)
        if "reference_genome_id" in keys:
            put("genome_hg18", [line.replace("hg19", "hg18") for line in lines])
            put("genome_hg38", [line.replace("hg19", "hg38") for line in lines])
        if kind == "MUTATION_UNCALLED":
            put("swissprot_bogus", [*lines, "swissprot_identifier: bogus"])
            put("swissprot_name", [*lines, "swissprot_identifier: name"])
    expression = typed_lines("MRNA_EXPRESSION:CONTINUOUS", tables.MANDATORY["EXPRESSION"], "mrna")
    matrix = tsv([("SAMPLE_ID", "mutations", "mrna"), ("S1", "P", "P"), ("S2", "P", "NA")])
    for character in BLANKS:
        blank = character + "\n"
        content = ("\n".join(expression[:2]) + "\n" + blank + "\n".join(expression[2:])).encode()
        cell(
            "typing",
            f"blank_{ord(character):04x}_typed",
            study(
                meta_typed_DOT_txt=content,
                d_typed_DOT_txt=b"x\n",
                data_gene_panel_matrix_DOT_txt=matrix,
            ),
        )
        resource = resource_meta("DEFINITION", (blank + "stable_id: foo\n").encode())
        cell(
            "typing",
            f"blank_{ord(character):04x}_resource",
            study(**{**resources(), "meta_resource_definition_DOT_txt": resource}),
        )


_bytes()
_controls()
_gate()
_header()
_cells()
_samples()
_seq()
_dicts()
_metas()
_beside()
_types()
_typing()

__all__ = ["CELLS", "DIRECTORY", "MATRIX_FILE", "MATRIX_META", "study"]
