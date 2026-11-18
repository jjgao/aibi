"""The discrete copy-number corpus of M4.2b-3 (D446): cells made by generators, never listed one by
one, each a small study that differs from a valid one by one change (or, for the pairs, two).

This is not a test: pytest does not collect it, and it imports nothing of the pack or of aibi. It
loads ``mutation_corpus.py`` (the slice-1b corpus, a stable helper: the study meta, the clinical
files and the case-list builder) by its path, so that the scratch harness that runs each cell on
the pinned reference (cbioportal-core 47890bb, offline, not relaxed; D409) can load it as it is,
and ``test_cna_corpus.py`` loads the same cells. The reference's outcome of every cell is recorded
in ``cna_reference.json`` by error identity, with the file each error names: whether it crashed and
the identity of each error it reported, in order (no message of the reference).

A cell is a mapping of a path below the study's directory to its content (``bytes``,
``DIRECTORY``, or ``("symlink", target)``), as in ``mutation_corpus``; ``CELLS`` maps each cell's
id, ``<generator>:<case>``, to it. The valid study is the slice-1b study without its MAF (no
mutation meta, so no ``_sequenced`` list is needed), with a discrete CNA meta, a matrix of two genes
by the three samples and the ``_cna`` case list. The generators:

- ``byte``: the matrix's bytes (byte order marks, line ends, blank and comment lines, the repeated
  header, row widths, an empty file, control characters, bytes that are not UTF-8, long fields, a
  file with no tab, the data file missing, a directory, a symlink);
- ``header``: the header's names (the gene columns' prefix in every order, a gene column among the
  samples, no gene column, no sample, an unknown, padded or repeated sample, too many names, the
  sniffer's tab);
- ``cell``: a token in a sample's cell, on a row of a class (``plain``: a symbol alone; ``entrez``:
  a symbol and an Entrez id; ``cyto``: a ``Cytoband`` before them);
- ``gene``: a row's symbol and Entrez id;
- ``dup``: pairs of rows the reference keys alike or not, and rows aibi keys alike;
- ``meta``: the CNA meta's values, a second one, its case list, its PD file, the CNA meta naming
  another imported file, and another meta naming the CNA file;
- ``role``: each line role (a top line, the header, a data line) crossed with hostile atoms;
- ``pair``: two classes of fault in one study, in both orders;
- ``review``: the classes code review round 1 found (a PD file the reference reads and errs on,
  near-miss ``_cna`` lists, whitespace-only keys in pairs, the sniffer's unterminated last line, a
  field counted in characters, an over-long field on every data-line role, a mark-led ``#`` line
  before the sniffer's window, the C table's other CNA datatypes);
- ``unread``: the files the pack never reads, by file (review round 2): the PD file's whole error
  domain under a dotted and a dotless name, the PD value's path faults, the never-imported
  copy-number metas with their faults and their data files', and a matrix refusal beside an error
  in a file the pack never reads;
- ``name``: each gene column's name near its spelling (padded, in another case, beside an empty
  name) at each position of the header (review round 2);
- ``mark``: a byte order mark on each meta and case list of each of eight studies, each study
  with no ``_all`` list, no ``_cna`` list or neither, and each never-imported copy-number meta
  twice (review round 3, D409's (13) and (14));
- ``compare``: the ``_cna`` list's id and header sample ids spelt near the ones the pack compares
  them with (review round 3; the rest of that domain is ``spellings``, which the brute forces
  run);
- ``shared``: the faults of a meta or a case list the shared parsers judge, out of the CNA map
  (review round 3), and the spellings they accept;
- ``type``: the study beside a meta of each C-table type the pack never imports, its data file
  valid, faulty or missing (review round 4);
- ``review4``: a repeated meta key, ``add_global_case_list`` in other cases, an OncoKB backup meta
  (review round 4).
"""

import importlib.util
import itertools
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType


def _base() -> ModuleType:
    path = Path(__file__).resolve().parent / "mutation_corpus.py"
    spec = importlib.util.spec_from_file_location("mutation_corpus_for_cna", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = _base()
DIRECTORY = BASE.DIRECTORY
SID = BASE.SID
BOM = BASE.BOM
CNA_FILE = "data_cna.txt"
CNA_META = "meta_cna.txt"
CNA_LIST = "case_lists/cases_cna.txt"
SAMPLES = ("S1", "S2", "S3")


def meta(
    stable: str = "cna",
    datatype: str = "DISCRETE",
    data: str = CNA_FILE,
    extra: str = "",
    show: str = "true",
) -> bytes:
    return (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
        f"datatype: {datatype}\nstable_id: {stable}\nshow_profile_in_analysis_tab: {show}\n"
        f"profile_name: CNA\nprofile_description: CNA\ndata_filename: {data}\n{extra}"
    ).encode()


def cna_list(ids: Sequence[str] = SAMPLES, suffix: str = "cna") -> bytes:
    return BASE.case_list(suffix, list(ids), category="all_cases_with_cna_data")


def tsv(rows: Sequence[Sequence[str]], end: str = "\n", top: bytes = b"") -> bytes:
    """A matrix of ``rows`` (the header first), each line ended by ``end``, after ``top``."""
    return top + (end.join("\t".join(row) for row in rows) + end).encode()


HEADER = ("Hugo_Symbol", *SAMPLES)
R1 = ("TP53", "0", "2", "NA")
R2 = ("EGFR", "-2", "-1.5", "0")
VALID = tsv([HEADER, R1, R2])
HEADER_E = ("Hugo_Symbol", "Entrez_Gene_Id", *SAMPLES)
E1 = ("TP53", "7157", "0", "2", "NA")
E2 = ("EGFR", "1956", "-2", "-1.5", "0")
HEADER_C = ("Cytoband", "Hugo_Symbol", "Entrez_Gene_Id", *SAMPLES)
C1 = ("17p13.1", "TP53", "7157", "0", "2", "NA")
C2 = ("7p11.2", "EGFR", "1956", "-2", "-1.5", "0")

Content = bytes | str | tuple[str, str]
Cell = dict[str, Content]


def study(**over: Content | None) -> Cell:
    """The valid study (the slice-1b study without its MAF, with the CNA meta, matrix and list),
    with ``over`` applied as ``mutation_corpus.study`` applies it."""
    files: Cell = BASE.study(
        meta_mutations_DOT_txt=None,
        data_mutations_DOT_txt=None,
        case_lists__cases_sequenced_DOT_txt=None,
    )
    files[CNA_META] = meta()
    files[CNA_FILE] = VALID
    files[CNA_LIST] = cna_list()
    for key, value in over.items():
        path = key.replace("__", "/").replace("_DOT_", ".")
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value
    return files


def data(content: bytes) -> Cell:
    return study(data_cna_DOT_txt=content)


CELLS: dict[str, Cell] = {}


def cell(generator: str, case: str, files: Cell) -> None:
    key = f"{generator}:{case}"
    assert key not in CELLS, key
    CELLS[key] = files


# --- byte -----------------------------------------------------------------------------------------

CONTROLS = (
    ("nul", "\x00"),
    ("soh", "\x01"),
    ("vt", "\x0b"),
    ("ff", "\x0c"),
    ("esc", "\x1b"),
    ("fs", "\x1c"),
    ("us", "\x1f"),
    ("del", "\x7f"),
)
"""Control characters the pack refuses anywhere (D409): NUL, SOH, VT, FF, ESC, FS to US and DEL;
VT, FF and FS to US are also whitespace ``str.strip()`` removes."""


def _bytes() -> None:
    cell("byte", "valid", study())
    cell("byte", "bom", data(BOM + VALID))
    cell("byte", "bom_with_top", data(BOM + tsv([HEADER, R1], top=b"#v\n")))
    cell("byte", "bom_comment_header", data(BOM + b"#c\n" + VALID))
    cell("byte", "bom_cytoband", data(BOM + tsv([HEADER_C, C1, C2])))
    cell("byte", "bom_no_tab", data(BOM + b"Hugo_Symbol\nTP53\n"))
    cell("byte", "bom_twice", data(BOM + BOM + VALID))
    cell("byte", "bom_alone_first_line", data(BOM + b"\n" + VALID))
    cell("byte", "bom_at_row", data(tsv([HEADER, ("\ufeffTP53", "0", "2", "0")])))
    cell("byte", "bom_in_cell", data(tsv([HEADER, ("TP53", "\ufeff2", "2", "0")])))
    cell("byte", "top_lines", data(tsv([HEADER, R1, R2], top=b"#a\n#b\n")))
    cell("byte", "top_line_hash_only", data(tsv([HEADER, R1], top=b"#\n")))
    cell("byte", "crlf", data(VALID.replace(b"\n", b"\r\n")))
    cell("byte", "cr", data(VALID.replace(b"\n", b"\r")))
    cell("byte", "mixed_first", data(VALID.replace(b"\n", b"\r\n", 1)))
    cell("byte", "mixed_last_cr", data(VALID[:-1] + b"\r"))
    cell("byte", "mixed_cr_lf", data(VALID.replace(b"\n", b"\r", 1)))
    cell("byte", "mixed_top", data(b"#a\r\n" + VALID))
    cell("byte", "cr_inside_cell", data(b"Hugo_Symbol\tS1\tS2\tS3\nTP53\t0\r2\t0\n"))
    cell("byte", "cr_lf_lf", data(VALID.replace(b"\n", b"\r\n\n", 1)))
    cell("byte", "no_final_line_end", data(VALID[:-1]))
    cell("byte", "header_only_no_line_end", data(b"\t".join(h.encode() for h in HEADER)))
    cell("byte", "header_only", data(tsv([HEADER])))
    cell("byte", "header_only_crlf", data(tsv([HEADER], "\r\n")))
    cell("byte", "trailing_blank", data(VALID + b"\n"))
    cell("byte", "blank_line", data(b"Hugo_Symbol\tS1\tS2\tS3\n\nTP53\t0\t2\t0\n"))
    cell("byte", "blank_tabs", data(tsv([HEADER, R1, ("", "", "", ""), R2])))
    cell("byte", "blank_spaces", data(tsv([HEADER, R1]) + b"   \n" + tsv([R2])))
    cell("byte", "blank_nbsp", data(tsv([HEADER, R1]) + "\xa0\t\xa0\t\t\n".encode()))
    cell("byte", "hash_line", data(tsv([HEADER, R1, ("#TP", "0", "2", "0")])))
    cell("byte", "hash_after_space", data(tsv([HEADER, R1, (" #TP", "0", "2", "0")])))
    cell("byte", "hash_after_header", data(tsv([HEADER, R1]) + b"#late\n"))
    cell("byte", "hash_between", data(tsv([HEADER]) + b"#c\n" + tsv([R1])))
    cell("byte", "repeated_header", data(tsv([HEADER, R1, HEADER, R2])))
    cell("byte", "repeated_header_last", data(tsv([HEADER, R1, HEADER])))
    cell("byte", "repeated_header_longer", data(tsv([HEADER, R1, (*HEADER[:-1], "S3x")])))
    cell("byte", "extra_field", data(tsv([HEADER, R1, (*R2, "0")])))
    cell("byte", "short_row", data(tsv([HEADER, R1, R2[:-1]])))
    cell("byte", "short_row_one", data(tsv([HEADER, ("TP53",)])))
    cell("byte", "empty", data(b""))
    cell("byte", "line_end_only", data(b"\n"))
    cell("byte", "top_only", data(b"#a\n#b\n"))
    cell("byte", "top_only_no_line_end", data(b"#a"))
    cell("byte", "blank_first_line", data(b"\n" + VALID))
    cell("byte", "spaces_first_line", data(b"   \n" + VALID))
    for name, character in CONTROLS:
        cell(
            "byte", f"control_cell_{name}", data(tsv([HEADER, ("TP53", "2" + character, "0", "0")]))
        )
        cell(
            "byte",
            f"control_cell_pre_{name}",
            data(tsv([HEADER, ("TP53", character + "2", "0", "0")])),
        )
        cell(
            "byte",
            f"control_symbol_{name}",
            data(tsv([HEADER, ("TP" + character + "53", "0", "2", "0")])),
        )
        cell(
            "byte", f"control_top_{name}", data(tsv([HEADER, R1], top=f"#a{character}\n".encode()))
        )
    cell("byte", "control_cytoband", data(tsv([HEADER_C, ("17\x01p", *C1[1:])])))
    cell("byte", "control_entrez", data(tsv([HEADER_E, ("TP53", "71\x01", "0", "2", "0")])))
    cell("byte", "control_header", data(tsv([("Hugo_Symbol", "S\x011", "S2", "S3"), R1])))
    cell("byte", "not_utf8_cell", data(b"Hugo_Symbol\tS1\tS2\tS3\nTP53\t0\xff\t2\t0\n"))
    cell("byte", "not_utf8_header", data(b"Hugo_Symbol\tS1\xff\tS2\tS3\nTP53\t0\t2\t0\n"))
    cell("byte", "not_utf8_top", data(b"#\xff\n" + VALID))
    cell("byte", "not_utf8_surrogate", data(b"Hugo_Symbol\tS1\tS2\tS3\n\xed\xa0\x80\t0\t2\t0\n"))
    cell("byte", "not_utf8_overlong", data(b"Hugo_Symbol\tS1\tS2\tS3\nTP\xc0\xaf\t0\t2\t0\n"))
    cell("byte", "long_symbol_131072", data(tsv([HEADER, ("G" * 131_072, "0", "2", "0")])))
    cell("byte", "long_symbol_131073", data(tsv([HEADER, ("G" * 131_073, "0", "2", "0")])))
    cell("byte", "long_cell", data(tsv([HEADER, ("TP53", "2" + " " * 140_000, "2", "0")])))
    cell("byte", "long_cytoband", data(tsv([HEADER_C, ("x" * 140_000, *C1[1:])])))
    cell("byte", "long_entrez", data(tsv([HEADER_E, ("TP53", "1" * 140_000, "0", "2", "0")])))
    cell("byte", "long_header_name", data(tsv([("Hugo_Symbol", "S" * 140_000, "S2", "S3"), R1])))
    cell("byte", "long_short_row", data(tsv([HEADER, ("G" * 140_000, "0", "2")])))
    cell("byte", "file_missing", study(data_cna_DOT_txt=None))
    cell("byte", "file_directory", study(data_cna_DOT_txt=DIRECTORY))
    cell(
        "byte",
        "file_symlink",
        study(data_cna_DOT_txt=("symlink", "real_cna.txt"), real_cna_DOT_txt=VALID),
    )
    cell("byte", "quoted_cell", data(tsv([HEADER, ("TP53", '"2"', "0", "0")])))
    cell("byte", "quoted_symbol", data(tsv([HEADER, ('"TP53"', "0", "2", "0")])))


# --- header ---------------------------------------------------------------------------------------


def _header() -> None:
    for name, header, row in (
        ("cytoband_first", HEADER_C, C1),
        (
            "entrez_then_hugo",
            ("Entrez_Gene_Id", "Hugo_Symbol", *SAMPLES),
            ("7157", "TP53", "0", "2", "0"),
        ),
        (
            "hugo_then_cytoband",
            ("Hugo_Symbol", "Cytoband", *SAMPLES),
            ("TP53", "17p", "0", "2", "0"),
        ),
        ("hugo_entrez", HEADER_E, E1),
        ("cytoband_hugo", ("Cytoband", "Hugo_Symbol", *SAMPLES), ("17p", "TP53", "0", "2", "0")),
        (
            "entrez_cytoband_hugo",
            ("Entrez_Gene_Id", "Cytoband", "Hugo_Symbol", *SAMPLES),
            ("7157", "17p", "TP53", "0", "2", "0"),
        ),
        ("entrez_only", ("Entrez_Gene_Id", *SAMPLES), ("7157", "0", "2", "0")),
        (
            "cytoband_entrez",
            ("Cytoband", "Entrez_Gene_Id", *SAMPLES),
            ("17p", "7157", "0", "2", "0"),
        ),
        ("cytoband_only", ("Cytoband", *SAMPLES), ("17p", "0", "2", "0")),
        ("no_gene_column", SAMPLES, ("0", "2", "0")),
        ("lower_hugo", ("hugo_symbol", *SAMPLES), ("TP53", "0", "2", "0")),
        ("padded_hugo_name", ("Hugo_Symbol ", *SAMPLES), ("TP53", "0", "2", "0")),
        (
            "composite_ref",
            ("Hugo_Symbol", "Composite.Element.REF", "S1", "S2"),
            ("TP53", "x", "0", "2"),
        ),
        ("hugo_twice", ("Hugo_Symbol", "Hugo_Symbol", *SAMPLES), ("TP53", "TP53", "0", "2", "0")),
        (
            "entrez_twice",
            ("Hugo_Symbol", "Entrez_Gene_Id", "Entrez_Gene_Id", *SAMPLES),
            ("TP53", "1", "1", "0", "2", "0"),
        ),
        ("hugo_after_sample", ("S1", "Hugo_Symbol", "S2", "S3"), ("0", "TP53", "2", "0")),
        (
            "entrez_among_samples",
            ("Hugo_Symbol", "S1", "Entrez_Gene_Id", "S3"),
            ("TP53", "0", "7157", "0"),
        ),
        ("hugo_last", ("Entrez_Gene_Id", "S1", "S2", "Hugo_Symbol"), ("7157", "0", "2", "TP53")),
        (
            "cytoband_among_samples",
            ("Hugo_Symbol", "S1", "Cytoband", "S3"),
            ("TP53", "0", "17p", "0"),
        ),
        ("cytoband_last", ("Hugo_Symbol", *SAMPLES, "Cytoband"), ("TP53", "0", "2", "0", "17p")),
        ("unknown_sample", ("Hugo_Symbol", "S1", "S2", "S9"), ("TP53", "0", "2", "0")),
        ("padded_sample", ("Hugo_Symbol", "S1", " S2", "S3"), ("TP53", "0", "2", "0")),
        ("padded_sample_after", ("Hugo_Symbol", "S1", "S2 ", "S3"), ("TP53", "0", "2", "0")),
        ("nbsp_sample", ("Hugo_Symbol", "S1", "\xa0S2", "S3"), ("TP53", "0", "2", "0")),
        ("lower_sample", ("Hugo_Symbol", "s1", "S2", "S3"), ("TP53", "0", "2", "0")),
        ("duplicate_sample", ("Hugo_Symbol", "S1", "S1", "S3"), ("TP53", "0", "2", "0")),
        (
            "duplicate_sample_last",
            ("Hugo_Symbol", "S1", "S2", "S3", "S3"),
            ("TP53", "0", "2", "0", "0"),
        ),
        ("empty_sample_name", ("Hugo_Symbol", "S1", "", "S3"), ("TP53", "0", "2", "0")),
        ("subset_of_samples", ("Hugo_Symbol", "S2"), ("TP53", "2")),
        ("samples_reordered", ("Hugo_Symbol", "S3", "S1", "S2"), ("TP53", "0", "2", "NA")),
        ("quoted_sample", ("Hugo_Symbol", '"S1"', "S2", "S3"), ("TP53", "0", "2", "0")),
        (
            "too_many_columns",
            ("Hugo_Symbol", "Entrez_Gene_Id", "Cytoband", *SAMPLES, "S1"),
            ("TP53", "1", "x", "0", "2", "0", "0"),
        ),
        ("duplicates_4097", ("Hugo_Symbol", *(["S1"] * 4097)), ("TP53", *(["0"] * 4097))),
    ):
        cell("header", name, data(tsv([header, row])))
    cell("header", "trailing_tab", data(b"Hugo_Symbol\tS1\tS2\tS3\t\nTP53\t0\t2\t0\t\n"))
    cell("header", "no_samples_tab", data(b"Hugo_Symbol\tEntrez_Gene_Id\nTP53\t7157\n"))
    cell("header", "no_samples_no_tab", data(b"Hugo_Symbol\nTP53\n"))
    cell("header", "no_samples_only", data(b"Hugo_Symbol\n"))
    cell(
        "header",
        "one_column_many_rows",
        data(b"Hugo_Symbol\n" + b"".join(b"G%d\n" % i for i in range(12))),
    )
    for later in range(1, 8):
        lines = b"".join(b"G%d\n" % i for i in range(1, later)) + b"TP53\t0\n"
        cell("header", f"one_column_tab_on_line_{later}", data(b"Hugo_Symbol\n" + lines))
    cell("header", "empty_header_line_tab_next", data(b"\n" + VALID))
    cell("header", "empty_header_line_no_tab", data(b"\nTP53\n"))
    cell("header", "spaces_header_line", data(b"   \nTP53\t0\n"))
    cell("header", "sample_named_s_space", data(tsv([("Hugo_Symbol", "S 1", "S2", "S3"), R1])))


# --- cell -----------------------------------------------------------------------------------------

TOKENS = (
    "0",
    "NA",
    "-2",
    "-1.5",
    "-1",
    "1",
    "2",
    "na",
    "Na",
    "nA",
    "N/A",
    "nan",
    "NaN",
    "",
    "2.0",
    "-2.0",
    "0.0",
    "+2",
    "+0",
    "-0",
    "1.5",
    "-1.50",
    "3",
    "-3",
    "1e0",
    "\uff12",
    "\u0662",
    "--2",
    "02",
    "x",
    "NULL",
    "Unknown",
)
"""A sample cell's tokens as written: the reference's seven, and spellings it refuses."""
PADDINGS = (
    " ",
    "\x85",
    "\xa0",
    "\u1680",
    "\u2000",
    "\u2005",
    "\u200a",
    "\u2028",
    "\u2029",
    "\u202f",
    "\u205f",
    "\u3000",
)
"""Some of the 20 characters ``str.strip()`` removes that a cell can hold (the unit brute force
runs all 20 against the row check)."""
NON_STRIPPED = ("\u200b", "\ufeff", "\u2060")
"""Characters a token may hold that ``str.strip()`` leaves."""


def _row_class(name: str, value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if name == "plain":
        return HEADER, ("TP53", value, "2", "0")
    if name == "entrez":
        return HEADER_E, ("TP53", "7157", value, "2", "0")
    return HEADER_C, ("17p13.1", "TP53", "7157", value, "2", "0")


def _cells() -> None:
    for (index, token), name in itertools.product(enumerate(TOKENS), ("plain", "entrez", "cyto")):
        header, row = _row_class(name, token)
        cell("cell", f"{name}_token{index}", data(tsv([header, row])))
    for (index, pad), token in itertools.product(enumerate(PADDINGS), ("2", "0", "NA", "-1.5")):
        header, row = _row_class("plain", pad + token)
        cell("cell", f"plain_pre{index}_{token}", data(tsv([header, row])))
    for index, pad in enumerate(PADDINGS):
        for token in ("2", "NA"):
            header, row = _row_class("entrez", token + pad)
            cell("cell", f"entrez_post{index}_{token}", data(tsv([header, row])))
        header, row = _row_class("cyto", pad + "0" + pad)
        cell("cell", f"cyto_around{index}", data(tsv([header, row])))
        header, row = _row_class("plain", pad)
        cell("cell", f"plain_alone{index}", data(tsv([header, row])))
        header, row = _row_class("plain", pad + "x")
        cell("cell", f"plain_pad_bad{index}", data(tsv([header, row])))
    for index, character in enumerate(NON_STRIPPED):
        header, row = _row_class("plain", character + "2")
        cell("cell", f"plain_unstripped{index}", data(tsv([header, row])))
    # a padded cell before an invalid one, and after it, on one row
    cell("cell", "padded_then_bad", data(tsv([HEADER, ("TP53", " 2", "x", "0")])))
    cell("cell", "bad_then_padded", data(tsv([HEADER, ("TP53", "x", " 2", "0")])))
    cell(
        "cell",
        "padded_row1_bad_row2",
        data(tsv([HEADER, ("TP53", " 2", "0", "0"), ("EGFR", "x", "0", "0")])),
    )
    cell(
        "cell",
        "bad_row1_padded_row2",
        data(tsv([HEADER, ("TP53", "x", "0", "0"), ("EGFR", " 2", "0", "0")])),
    )
    cell(
        "cell",
        "all_na",
        data(tsv([HEADER, ("TP53", "NA", "NA", "NA"), ("EGFR", "NA", "NA", "NA")])),
    )
    cell("cell", "all_zero", data(tsv([HEADER, ("TP53", "0", "0", "0")])))
    cell(
        "cell",
        "all_calls",
        data(tsv([HEADER, ("TP53", "2", "-2", "1"), ("EGFR", "-1", "-1.5", "2")])),
    )
    cell(
        "cell",
        "one_sample_all_na",
        data(tsv([HEADER, ("TP53", "NA", "2", "0"), ("EGFR", "NA", "0", "0")])),
    )
    cell(
        "cell",
        "gene_na_everywhere",
        data(tsv([HEADER, ("TP53", "NA", "NA", "NA"), ("EGFR", "0", "2", "0")])),
    )
    cell(
        "cell",
        "three_patterns",
        data(
            tsv(
                [
                    HEADER,
                    ("TP53", "NA", "2", "0"),
                    ("EGFR", "0", "NA", "0"),
                    ("KRAS", "2", "0", "0"),
                ]
            )
        ),
    )


# --- gene -----------------------------------------------------------------------------------------

SYMBOLS = (
    "TP53",
    "",
    " ",
    "\xa0",
    " TP53",
    "TP53 ",
    "\u3000TP53",
    "TP53\u2028",
    "NA",
    "Unknown",
    "1TP53",
    "TP 53",
    "TP53|7157",
    "|x",
    "|",
    "TP53|",
    "tp53",
    "\xdf",
    "\xe9",
    "e\u0301",
    "\u200bTP53",
    "#TP53",
    "G" * 5_000,
)
"""A row's symbol: valid, empty, padded, whitespace alone, the reference's spellings of a value, a
cut at ``|``, cases and normalisations."""
ENTREZ = (
    "7157",
    "",
    " ",
    "0",
    "-1",
    "-5",
    "00",
    " 0 ",
    "abc",
    "7157.0",
    "1e3",
    " 7157",
    "7157 ",
    "\xa07157",
    " 7 157",
    "7_157",
    "+7157",
    "\uff17\uff11\uff15\uff17",
    "\u0667\u0661\u0665\u0667",
    "99999999999999999999999",
    "1" * 4_301,
    "NA",
    "07157",
    "-0",
)
"""A row's Entrez id: valid, empty, not positive, what ``int()`` reads beyond ASCII digits, what it
does not read (the reference skips the row), padded."""


def _genes() -> None:
    for index, symbol in enumerate(SYMBOLS):
        cell("gene", f"symbol{index}", data(tsv([HEADER, (symbol, "0", "2", "0")])))
        cell(
            "gene", f"symbol{index}_entrez", data(tsv([HEADER_E, (symbol, "7157", "0", "2", "0")]))
        )
    for index, entrez in enumerate(ENTREZ):
        cell("gene", f"entrez{index}", data(tsv([HEADER_E, ("TP53", entrez, "0", "2", "0")])))
        cell("gene", f"entrez{index}_no_symbol", data(tsv([HEADER_E, ("", entrez, "0", "2", "0")])))
        cell(
            "gene",
            f"entrez{index}_bad_value",
            data(tsv([HEADER_E, ("TP53", entrez, "bad", "", "0")])),
        )
        cell(
            "gene",
            f"entrez{index}_padded_value",
            data(tsv([HEADER_E, ("TP53", entrez, " 2", "0", "0")])),
        )
    cell("gene", "padded_symbol_bad_value", data(tsv([HEADER, (" TP53", "x", "0", "0")])))
    cell(
        "gene",
        "padded_entrez_padded_symbol",
        data(tsv([HEADER_E, (" TP53", " 7157", "0", "2", "0")])),
    )
    cell("gene", "no_symbol_padded_value", data(tsv([HEADER_E, ("", "7157", " 2", "0", "0")])))
    cell("gene", "cytoband_padded", data(tsv([HEADER_C, (" 17p", *C1[1:])])))
    cell("gene", "cytoband_empty", data(tsv([HEADER_C, ("", *C1[1:])])))


# --- dup ------------------------------------------------------------------------------------------

PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("same", "TP53", "TP53", ""),
    ("case", "TP53", "tp53", ""),
    ("bar", "TP53", "TP53|x", ""),
    ("bar_first", "TP53|x", "TP53", ""),
    ("trailing_bar", "TP53|", "TP53", ""),
    ("bars", "|x", "|y", ""),
    ("bar_vs_empty_key", "|", "|y", ""),
    ("sharp_s", "\xdf", "SS", ""),
    ("ligature", "\ufb01", "FI", ""),
    ("dotless_i", "\u0131", "I", ""),
    ("dotted_i", "\u0130", "I\u0307", ""),
    ("space_before_bar", "TP53 |x", "TP53", ""),
    ("nfc_nfd", "\xc9X", "E\u0301X", ""),
    ("cut_case", "tp53|a", "TP53|b", ""),
    ("two_bars", "TP53|x|y", "TP53", ""),
)
"""Pairs of symbols on two rows (no Entrez column): the reference's key is the symbol stripped,
cut at its first ``|`` and upper-cased; aibi's the symbol as written."""
ENTREZ_PAIRS: tuple[tuple[str, tuple[str, str], tuple[str, str]], ...] = (
    ("same_entrez_other_symbol", ("TP53", "7157"), ("P53", "7157")),
    ("same_symbol_other_entrez", ("TP53", "7157"), ("TP53", "999")),
    ("same_symbol_blank_entrez", ("TP53", "7157"), ("TP53", "")),
    ("blank_then_same_symbol", ("TP53", ""), ("TP53", "7157")),
    ("leading_zero", ("TP53", "7157"), ("P53", "07157")),
    ("padded_same_entrez", ("TP53", "7157"), ("P53", " 7157")),
    ("skip_then_valid", ("TP53", "abc"), ("TP53", "7157")),
    ("valid_then_skip", ("TP53", "7157"), ("TP53", "abc")),
    ("symbol_is_other_entrez", ("7157", ""), ("P53", "7157")),
    ("case_with_entrez", ("TP53", "7157"), ("tp53", "1")),
    ("blank_symbols_same_entrez", ("", "7157"), ("", "7157")),
    ("plus_entrez", ("TP53", "7157"), ("P53", "+7157")),
)
"""Pairs of rows with an Entrez column: the reference keys a row by its Entrez id where it has one
(stripped, as a string), else by its symbol."""


def _dups() -> None:
    for name, first, second, _ in PAIRS:
        cell("dup", name, data(tsv([HEADER, (first, "0", "2", "0"), (second, "2", "2", "2")])))
        cell(
            "dup",
            f"{name}_bad_later",
            data(tsv([HEADER, (first, "0", "2", "0"), (second, "x", "", "2")])),
        )
    for name, (s1, e1), (s2, e2) in ENTREZ_PAIRS:
        cell("dup", name, data(tsv([HEADER_E, (s1, e1, "0", "2", "0"), (s2, e2, "2", "2", "2")])))
    cell(
        "dup", "three_rows_last_repeats_first", data(tsv([HEADER, R1, R2, ("TP53", "2", "2", "2")]))
    )
    cell(
        "dup",
        "repeat_after_bad",
        data(tsv([HEADER, ("TP53", "x", "2", "0"), ("TP53", "0", "0", "0")])),
    )


# --- meta -----------------------------------------------------------------------------------------

SV_META = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: STRUCTURAL_VARIANT\ndatatype: SV\n"
    "stable_id: structural_variants\nshow_profile_in_analysis_tab: true\nprofile_name: SV\n"
    "profile_description: SV\ndata_filename: {}\n"
)
SV = b"Sample_Id\tSV_Status\tSite1_Hugo_Symbol\tSite2_Hugo_Symbol\nS1\tSOMATIC\tEML4\tALK\n"
PD = b"SAMPLE_ID\tHugo_Symbol\tcbp_driver\tcbp_driver_annotation\nS1\tTP53\tPutative_Driver\tx\n"
LOG2 = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
    "datatype: LOG2-VALUE\nstable_id: log2CNA\nshow_profile_in_analysis_tab: false\n"
    "profile_name: L\nprofile_description: L\ndata_filename: {}\n"
)
TIMELINE = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: CLINICAL\ndatatype: TIMELINE\n"
    "data_filename: {}\n"
)


def _metas() -> None:
    for stable in (
        "cna",
        "gistic",
        "cna_rae",
        "cna_consensus",
        "other",
        "CNA",
        " cna ",
        "cna\t",
        "Gistic",
        "linear_CNA",
        "log2CNA",
    ):
        name = stable.strip() + ("_space" if stable.startswith(" ") else "")
        name += "_tab" if stable.endswith("\t") else ""
        cell("meta", f"stable_{name}", study(meta_cna_DOT_txt=meta(stable=stable)))
    cell(
        "meta",
        "second_discrete",
        study(
            meta_cna2_DOT_txt=meta(stable="gistic", data="data_cna2.txt"), data_cna2_DOT_txt=VALID
        ),
    )
    cell("meta", "second_discrete_same_file", study(meta_cna2_DOT_txt=meta(stable="gistic")))
    cell(
        "meta",
        "second_same_stable_id",
        study(meta_cna2_DOT_txt=meta(data="data_cna2.txt"), data_cna2_DOT_txt=VALID),
    )
    cell(
        "meta",
        "discrete_long_beside",
        study(
            meta_cnal_DOT_txt=meta(stable="gistic", datatype="DISCRETE_LONG", data="data_cnal.txt"),
            data_cnal_DOT_txt=b"Sample_Id\tHugo_Symbol\tValue\nS1\tTP53\t2\n",
        ),
    )
    cell(
        "meta",
        "discrete_long_alone",
        study(
            meta_cna_DOT_txt=meta(datatype="DISCRETE_LONG"),
            data_cna_DOT_txt=b"Sample_Id\tHugo_Symbol\tValue\nS1\tTP53\t2\n",
            case_lists__cases_cna_DOT_txt=None,
        ),
    )
    cell("meta", "no_cna_list", study(case_lists__cases_cna_DOT_txt=None))
    cell(
        "meta",
        "no_case_lists",
        study(case_lists__cases_cna_DOT_txt=None, case_lists__cases_all_DOT_txt=None),
    )
    cell(
        "meta", "list_other_suffix", study(case_lists__cases_cna_DOT_txt=cna_list(suffix="cnaseq"))
    )
    cell("meta", "list_subset", study(case_lists__cases_cna_DOT_txt=cna_list(("S1", "S2"))))
    cell(
        "meta",
        "list_unknown_id",
        study(case_lists__cases_cna_DOT_txt=cna_list(("S1", "S2", "S3", "S9"))),
    )
    cell("meta", "list_empty", study(case_lists__cases_cna_DOT_txt=cna_list(())))
    cell(
        "meta",
        "matrix_subset_of_list",
        data(tsv([("Hugo_Symbol", "S1", "S2"), ("TP53", "0", "2")])),
    )
    cell("meta", "show_maybe", study(meta_cna_DOT_txt=meta(show="maybe")))
    cell("meta", "show_false", study(meta_cna_DOT_txt=meta(show="false")))
    cell(
        "meta",
        "no_show",
        study(meta_cna_DOT_txt=meta().replace(b"show_profile_in_analysis_tab: true\n", b"")),
    )
    cell("meta", "no_stable_id", study(meta_cna_DOT_txt=meta().replace(b"stable_id: cna\n", b"")))
    cell(
        "meta",
        "no_data_filename",
        study(meta_cna_DOT_txt=meta().replace(b"data_filename: data_cna.txt\n", b"")),
    )
    cell("meta", "datatype_lower", study(meta_cna_DOT_txt=meta(datatype="discrete")))
    cell(
        "meta",
        "other_study",
        study(
            meta_cna_DOT_txt=meta().replace(
                f"cancer_study_identifier: {SID}".encode(), b"cancer_study_identifier: other_study"
            )
        ),
    )
    cell("meta", "gene_panel", study(meta_cna_DOT_txt=meta(extra="gene_panel: NOPANEL\n")))
    cell(
        "meta",
        "pd_named",
        study(
            meta_cna_DOT_txt=meta(extra="pd_annotations_filename: data_pd.txt\n"),
            data_pd_DOT_txt=PD,
        ),
    )
    cell(
        "meta",
        "pd_missing",
        study(meta_cna_DOT_txt=meta(extra="pd_annotations_filename: data_pd.txt\n")),
    )
    cell(
        "meta",
        "pd_names_data",
        study(meta_cna_DOT_txt=meta(extra="pd_annotations_filename: data_cna.txt\n")),
    )
    cell(
        "meta",
        "pd_names_sample",
        study(meta_cna_DOT_txt=meta(extra="pd_annotations_filename: data_clinical_sample.txt\n")),
    )
    for name, value in (
        ("names_sample", "data_clinical_sample.txt"),
        ("names_patient", "data_clinical_patient.txt"),
        ("names_study_meta", "meta_study.txt"),
        ("names_own_meta", "meta_cna.txt"),
        ("names_sample_folded", "DATA_CLINICAL_SAMPLE.txt"),
        ("names_dot_slash", "./data_cna.txt"),
        ("names_subdirectory", "sub/data_cna.txt"),
        ("names_parent", "../data_cna.txt"),
        ("names_empty", ""),
        ("names_absolute", "/data_cna.txt"),
        ("names_noncharacter", "data_cna\ufdd0.txt"),
        ("names_case_list", "case_lists"),
    ):
        files = study(meta_cna_DOT_txt=meta(data=value))
        if value == "data_cna\ufdd0.txt":
            files[value] = VALID
        if value == "sub/data_cna.txt":
            files[value] = VALID
        cell("meta", name, files)
    cell(
        "meta",
        "names_symlink_to_data",
        study(
            meta_cna_DOT_txt=meta(data="link_cna.txt"), link_cna_DOT_txt=("symlink", "data_cna.txt")
        ),
    )
    cell(
        "meta",
        "names_folded_symlink",
        study(
            meta_cna_DOT_txt=meta(data="Data_CNA.txt"), Data_CNA_DOT_txt=("symlink", "data_cna.txt")
        ),
    )
    cell(
        "meta",
        "names_folded_other",
        study(
            meta_cna_DOT_txt=meta(data="DATA_CNA.txt"),
            data_cna_DOT_txt=None,
            DATA_CNA_DOT_txt=VALID,
        ),
    )
    cell(
        "meta",
        "sv_and_cna",
        study(meta_sv_DOT_txt=SV_META.format("data_sv.txt").encode(), data_sv_DOT_txt=SV),
    )
    cell("meta", "sv_names_cna_file", study(meta_sv_DOT_txt=SV_META.format(CNA_FILE).encode()))
    cell(
        "meta",
        "cna_names_sv_file",
        study(
            meta_sv_DOT_txt=SV_META.format("data_sv.txt").encode(),
            data_sv_DOT_txt=SV,
            meta_cna_DOT_txt=meta(data="data_sv.txt"),
        ),
    )
    cell("meta", "log2_names_cna_file", study(meta_log2_DOT_txt=LOG2.format(CNA_FILE).encode()))
    cell(
        "meta",
        "log2_names_cna_folded",
        study(meta_log2_DOT_txt=LOG2.format("DATA_CNA.txt").encode()),
    )
    cell(
        "meta",
        "log2_own_file",
        study(
            meta_log2_DOT_txt=LOG2.format("data_log2.txt").encode(),
            data_log2_DOT_txt=tsv([HEADER, ("TP53", "0.1", "0.2", "NA")]),
        ),
    )
    cell(
        "meta",
        "timeline_names_cna_file",
        study(meta_timeline_DOT_txt=TIMELINE.format(CNA_FILE).encode()),
    )
    cell(
        "meta",
        "maf_and_cna",
        BASE.study(
            meta_cna_DOT_txt=meta(),
            data_cna_DOT_txt=VALID,
            case_lists__cases_cna_DOT_txt=cna_list(),
        ),
    )
    cell(
        "meta",
        "maf_names_cna_file",
        BASE.study(
            meta_cna_DOT_txt=meta(),
            data_cna_DOT_txt=VALID,
            case_lists__cases_cna_DOT_txt=cna_list(),
            meta_mutations_DOT_txt=BASE.META.replace("data_mutations.txt", CNA_FILE).encode(),
        ),
    )
    cell(
        "meta",
        "cna_names_maf",
        BASE.study(
            meta_cna_DOT_txt=meta(data="data_mutations.txt"),
            case_lists__cases_cna_DOT_txt=cna_list(),
        ),
    )


# --- role: line roles x hostile atoms -------------------------------------------------------------

ATOMS = (
    ("tab_end", "\t"),
    ("hash", "#"),
    ("space", " "),
    ("nbsp", "\xa0"),
    ("bom", "\ufeff"),
    ("nel", "\x85"),
    ("ls", "\u2028"),
    ("noncharacter", "\ufffe"),
    ("nul", "\x00"),
    ("quote", '"'),
    ("bar", "|"),
)


def _roles() -> None:
    for (name, atom), at in itertools.product(ATOMS, ("start", "end")):

        def put(text: str, atom: str = atom, at: str = at) -> str:
            return atom + text if at == "start" else text + atom

        top = f"{put('#top')}\n".encode()
        cell("role", f"top_{name}_{at}", data(top + VALID))
        header = "\t".join(HEADER)
        cell("role", f"header_{name}_{at}", data((put(header) + "\n").encode() + tsv([R1])))
        line = "\t".join(R1)
        cell("role", f"data_{name}_{at}", data(tsv([HEADER]) + (put(line) + "\n").encode()))
        cell(
            "role",
            f"header_after_top_{name}_{at}",
            data(b"#t\n" + (put(header) + "\n").encode() + tsv([R1])),
        )


# --- pair: two classes of fault, in both orders ---------------------------------------------------

FAULTS: Mapping[str, tuple[str, bytes]] = {
    "control": ("line", b"TP53\t0\x01\t2\t0"),
    "structure": ("line", b"TP53\t0\t2"),
    "blank": ("line", b""),
    "cell": ("line", b"TP53\tx\t2\t0"),
    "padded": ("line", b"TP53\t 2\t2\t0"),
    "dup": ("line", b"EGFR\t0\t0\t0"),
    "skip": ("line", b"TP53\t0\t2\t0"),
    "not_utf8": ("line", b"TP\xff53\t0\t2\t0"),
}
"""A faulty data line of each class (``dup`` repeats the base row, ``skip`` is a clean line)."""


def _pairs() -> None:
    names = [name for name in FAULTS if name != "skip"]
    for first, second in itertools.permutations(names, 2):
        lines = [
            b"Hugo_Symbol\tS1\tS2\tS3",
            b"EGFR\t-2\t-1.5\t0",
            FAULTS[first][1],
            b"KRAS\t0\t0\t0",
            FAULTS[second][1],
        ]
        cell("pair", f"{first}_then_{second}", data(b"\n".join(lines) + b"\n"))
    for name in names:
        lines = [b"Hugo_Symbol\tS1\tS2\tS3", b"EGFR\t-2\t-1.5\t0", FAULTS[name][1]]
        content = b"\n".join(lines) + b"\n"
        cell(
            "pair",
            f"{name}_with_meta_stable_id",
            study(data_cna_DOT_txt=content, meta_cna_DOT_txt=meta(stable="other")),
        )
        cell(
            "pair",
            f"{name}_with_no_list",
            study(data_cna_DOT_txt=content, case_lists__cases_cna_DOT_txt=None),
        )
        cell("pair", f"{name}_with_header_unknown", data(content.replace(b"S3", b"S9", 1)))
        cell("pair", f"{name}_with_mixed_ends", data(content.replace(b"\n", b"\r\n", 1)))
        cell("pair", f"{name}_with_bom", data(BOM + content))


# --- review: the classes code review round 1 found ---------------------------------------------

PD_HEAD = "SAMPLE_ID\tHugo_Symbol\tcbp_driver\tcbp_driver_annotation\n"
PD_META = meta(extra="pd_annotations_filename: data_pd.txt\n")
SEG_META = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
    "datatype: SEG\nreference_genome_id: hg19\ndescription: Segments\n"
    "data_filename: data_cna.seg\n"
).encode()
SEG = b"ID\tchrom\tloc.start\tloc.end\tnum.mark\tseg.mean\nS1\t1\t1000\t2000\t10\t0.1\n"


def _review() -> None:
    """Round 1's classes: a PD file the reference reads and errs on (exit CE2), near-miss `_cna`
    lists, whitespace-only keys in pairs, the sniffer's unterminated last line, a field counted in
    characters, an over-long field on every data-line role, a mark-led ``#`` line before the
    sniffer's window, and the CNA metas of the C table's other datatypes."""
    for name, content in (
        ("gene_not_in_cna", (PD_HEAD + "S1\tKRAS\tPutative_Driver\tx\n").encode()),
        ("no_sample_column", b"Hugo_Symbol\tcbp_driver\nTP53\tPutative_Driver\n"),
        ("unknown_sample", (PD_HEAD + "S9\tTP53\tPutative_Driver\tx\n").encode()),
        ("bad_driver", (PD_HEAD + "S1\tTP53\tMaybe\tx\n").encode()),
        ("empty", b""),
        ("valid", (PD_HEAD + "S1\tTP53\tPutative_Driver\tx\n").encode()),
    ):
        cell("review", f"pd_{name}", study(meta_cna_DOT_txt=PD_META, data_pd_DOT_txt=content))
    for name, suffix in (("other_cna", "other_cna"), ("upper", "CNA"), ("cna_x", "cna_x")):
        cell("review", f"list_{name}", study(case_lists__cases_cna_DOT_txt=cna_list(suffix=suffix)))
    for name, rows in (
        (
            "bar_then_entrez_ws",
            [HEADER_E, ("|x", "", "0", "2", "NA"), ("TP53", " ", "0", "2", "NA")],
        ),
        (
            "bar_then_entrez_nbsp",
            [HEADER_E, ("|x", "", "0", "2", "NA"), ("TP53", "\xa0", "0", "2", "NA")],
        ),
        (
            "entrez_ws_then_bar",
            [HEADER_E, ("TP53", "", "0", "2", "NA"), ("|y", " ", "0", "2", "NA")],
        ),
        ("symbol_ws_entrez_ws", [HEADER_E, ("\u3000", " ", "0", "2", "NA")]),
        ("two_halves_one_row", [HEADER, ("TP53", "-1.5", "-1.5", "NA")]),
        ("multibyte_131072", [HEADER, ("\xe9" * 131_072, "0", "2", "0")]),
        ("multibyte_131073", [HEADER, ("\xe9" * 131_073, "0", "2", "0")]),
    ):
        cell("review", name, data(tsv(rows)))
    cell("review", "notab_tab_unterminated_5", data(b"Hugo_Symbol\n" + b"x\n" * 4 + b"TP53\t2"))
    cell("review", "notab_tab_unterminated_6", data(b"Hugo_Symbol\n" + b"x\n" * 5 + b"TP53\t2"))
    long = "x" * 140_000
    head = ("\t".join(HEADER) + "\n").encode()
    for name, line in (
        ("long_blank_data", " " * 140_000),
        ("long_blank_tabs", "\t".join([" " * 140_000, "", "", ""])),
        ("long_comment_data", "#" + long),
        ("long_comment_tabs", "#" + long + "\t0\t0\t0"),
        ("long_hash_row", "#\t" + long + "\t0\t0"),
        ("long_repeated_header", "\t".join(HEADER) + "\t" + long),
    ):
        cell("review", name, data(VALID + (line + "\n").encode()))
    cell("review", "long_comment_in_sniff", data(head + ("#" + long + "\n").encode() + tsv([R1])))
    cell("review", "long_top_comment", data(("#" + long + "\n").encode() + VALID))
    cell("review", "bom_hash_then_tabless", data(BOM + b"#c\n" + b"x\n" * 5 + VALID))
    cell("review", "bom_hash_then_tab", data(BOM + b"#c\n" + b"x\n" * 3 + VALID))
    cell("review", "seg_meta", study(meta_seg_DOT_txt=SEG_META, data_cna_DOT_seg=SEG))
    cell(
        "review",
        "continuous_meta",
        study(
            meta_cont_DOT_txt=meta(
                stable="linear_CNA", datatype="CONTINUOUS", data="data_linear.txt", show="false"
            ),
            data_linear_DOT_txt=tsv([HEADER, ("TP53", "0.5", "-0.2", "NA")]),
        ),
    )


# --- unread: the files the pack never reads, and the metas it never imports ----------------------

PD_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "driver": ("SAMPLE_ID", "Hugo_Symbol", "cbp_driver", "cbp_driver_annotation"),
    "entrez": ("SAMPLE_ID", "Hugo_Symbol", "Entrez_Gene_Id", "cbp_driver", "cbp_driver_annotation"),
    "tiers": ("SAMPLE_ID", "Hugo_Symbol", "cbp_driver_tiers", "cbp_driver_tiers_annotation"),
}
"""The PD file's headers: a driver with its annotation, the same with an Entrez id, tiers."""
PD_VALUES: Mapping[str, str] = {
    "SAMPLE_ID": "S1",
    "Hugo_Symbol": "TP53",
    "Entrez_Gene_Id": "7157",
    "cbp_driver": "Putative_Driver",
    "cbp_driver_annotation": "x",
    "cbp_driver_tiers": "Tier1",
    "cbp_driver_tiers_annotation": "x",
}
PD_FIELD_FAULTS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "SAMPLE_ID": (("unknown", "S9"), ("blank", ""), ("padded", " S1")),
    "Hugo_Symbol": (("not_in_cna", "KRAS"), ("blank", ""), ("padded", " TP53")),
    "Entrez_Gene_Id": (("negative", "-1"), ("zero", "0"), ("abc", "abc")),
    "cbp_driver": (("unknown", "Maybe"), ("blank", "")),
    "cbp_driver_annotation": (("long", "x" * 81), ("blank", "")),
    "cbp_driver_tiers": (("long", "x" * 81), ("blank", "")),
    "cbp_driver_tiers_annotation": (("blank", ""),),
}
"""Each PD column's faulty values: an unknown, blank or padded id, a gene the matrix does not
hold, an Entrez id the reference refuses or skips, a driver value it does not know, an over-long
tier or annotation."""
PD_ANY_FIELD = (("control", "x\x01"), ("huge", "x" * 140_000))
"""A fault any field can hold: a control character, a field over the csv reader's limit."""


def _pd_bytes(header: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    return tsv([header, *rows])


def _pd_files() -> list[tuple[str, bytes]]:
    """The PD file's error domain, generated: each header crossed with each column's faulty values
    and each field fault, and with each line fault (the file's structure)."""
    out: list[tuple[str, bytes]] = []
    for name, header in PD_COLUMNS.items():
        row = [PD_VALUES[column] for column in header]
        out.append((f"{name}_valid", _pd_bytes(header, [row])))
        for at, column in enumerate(header):
            for fault, value in (*PD_FIELD_FAULTS[column], *PD_ANY_FIELD):
                changed = [*row[:at], value, *row[at + 1 :]]
                out.append((f"{name}_{column}_{fault}", _pd_bytes(header, [changed])))
        head = ("\t".join(header) + "\n").encode()
        line = ("\t".join(row) + "\n").encode()
        for fault, content in (
            ("empty", b""),
            ("header_only", head),
            ("blank_line", head + line + b"\n" + line),
            ("comment_line", head + line + b"#x\n"),
            ("repeated_header", head + head + line),
            ("short_row", head + "\t".join(row[:-1]).encode() + b"\n"),
            ("long_row", head + line.rstrip(b"\n") + b"\tx\n"),
            ("mixed_ends", head + line.rstrip(b"\n") + b"\r\n" + line),
            ("cr_ends", head.replace(b"\n", b"\r") + line.replace(b"\n", b"\r")),
            ("no_end", head + line.rstrip(b"\n")),
            ("not_utf8", head + line.replace(b"x\n", b"\xff\n")),
            ("bom", BOM + head + line),
            ("dup_column", _pd_bytes((*header, header[1]), [(*row, row[1])])),
            ("no_sample_column", _pd_bytes(header[1:], [row[1:]])),
            ("no_gene_column", _pd_bytes((header[0], *header[2:]), [(row[0], *row[2:])])),
            ("no_tab", b"SAMPLE_ID\nS1\n"),
            ("binary", b"\x00\x01\x02"),
        ):
            out.append((f"{name}_{fault}", content))
    return out


def pd_meta(value: str, data_value: str = CNA_FILE) -> bytes:
    return meta(data=data_value, extra=f"pd_annotations_filename: {value}\n")


NEVER_METAS: Mapping[str, tuple[str, str, bytes]] = {
    "continuous": (
        "CONTINUOUS",
        "linear_CNA",
        tsv([HEADER, ("TP53", "0.5", "-0.2", "NA")]),
    ),
    "log2": ("LOG2-VALUE", "log2CNA", tsv([HEADER, ("TP53", "0.5", "-0.2", "NA")])),
    "long": (
        "DISCRETE_LONG",
        "cna_long",
        b"Hugo_Symbol\tEntrez_Gene_Id\tSample_Id\tValue\nTP53\t7157\tS1\t2\n",
    ),
}
"""The C table's never-imported copy-number datatypes with a profile meta: the datatype, its
``stable_id`` and a data file it reads."""


def never_meta(datatype: str, stable: str, show: str = "false", data_value: str = "d.txt") -> bytes:
    return meta(stable=stable, datatype=datatype, data=data_value, show=show)


def _unread() -> None:
    """Review r2 MJ-A: every file the pack never reads, by file. The PD file's whole error domain
    (``_pd_files``) under a PD value with an extension and a dotless one; the PD value's path
    faults, each with a valid and a faulty target; the never-imported copy-number metas, each
    with its own faults and its data file's; and a matrix refusal next to an error in a file the
    pack never reads, which must not decide its kind."""
    files = _pd_files()
    bad = dict(files)["driver_SAMPLE_ID_unknown"]
    for name, content in files:
        cell(
            "unread",
            f"pd_{name}",
            study(meta_cna_DOT_txt=pd_meta("data_pd.txt"), data_pd_DOT_txt=content),
        )
    for name in ("driver_valid", "driver_SAMPLE_ID_unknown", "driver_long_row", "driver_empty"):
        content = dict(files)[name]
        cell("unread", f"dotless_{name}", study(meta_cna_DOT_txt=pd_meta("pdfile"), pdfile=content))
    for target, label in ((PD, "valid"), (bad, "bad")):
        for case, value, extra in (
            ("dot_slash", "./data_pd.txt", {"data_pd_DOT_txt": target}),
            ("subdir", "pd/data_pd.txt", {"pd__data_pd_DOT_txt": target}),
            ("parent", "../data_pd.txt", {"data_pd_DOT_txt": target}),
            ("absolute", "/data_pd.txt", {"data_pd_DOT_txt": target}),
            (
                "symlink",
                "pdlink.txt",
                {"data_pd_DOT_txt": target, "pdlink_DOT_txt": ("symlink", "data_pd.txt")},
            ),
            ("fold_only", "DATA_PD.TXT", {"data_pd_DOT_txt": target}),
            ("trailing_slash", "data_pd.txt/", {"data_pd_DOT_txt": target}),
            ("study_meta", "meta_study.txt", {}),
            ("sample_file", "data_clinical_sample.txt", {}),
        ):
            cell(
                "unread", f"pdpath_{case}_{label}", study(meta_cna_DOT_txt=pd_meta(value), **extra)
            )
    for case, value in (
        ("missing", "data_pd.txt"),
        ("directory", "case_lists"),
        ("directory_slash", "case_lists/"),
        ("dotless_missing", "pdfile"),
    ):
        cell("unread", f"pdpath_{case}", study(meta_cna_DOT_txt=pd_meta(value)))
    for name, (datatype, stable, content) in NEVER_METAS.items():
        valid = never_meta(datatype, stable)
        header = content.split(b"\n", 1)[0]
        for fault, over in (
            ("valid", {}),
            ("stable_id", {"meta_other_DOT_txt": never_meta(datatype, "cna")}),
            ("show", {"meta_other_DOT_txt": never_meta(datatype, stable, show="true")}),
            ("value", {"d_DOT_txt": content.replace(b"\t2\n", b"\tx\n").replace(b"0.5", b"x")}),
            ("unknown_sample", {"d_DOT_txt": content.replace(b"S1", b"S9")}),
            ("empty", {"d_DOT_txt": b""}),
            ("header_only", {"d_DOT_txt": header + b"\n"}),
            ("missing", {"d_DOT_txt": None}),
            ("directory", {"d_DOT_txt": None, "d_DOT_txt__x": b"x"}),
            ("not_utf8", {"d_DOT_txt": content.replace(b"TP53", b"TP\xff3")}),
            ("long_field", {"d_DOT_txt": content.replace(b"TP53", b"x" * 140_000)}),
        ):
            files_ = {"meta_other_DOT_txt": valid, "d_DOT_txt": content, **over}
            cell("unread", f"never_{name}_{fault}", study(**files_))
    seg = (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
        "datatype: SEG\nreference_genome_id: hg19\ndescription: Segments\ndata_filename: d.seg\n"
    ).encode()
    segment = b"ID\tchrom\tloc.start\tloc.end\tnum.mark\tseg.mean\nS1\t1\t1000\t2000\t10\t0.1\n"
    for fault, content in (
        ("valid", segment),
        ("value", segment.replace(b"0.1\n", b"x\n")),
        ("unknown_sample", segment.replace(b"S1", b"S9")),
        ("empty", b""),
        ("missing", None),
    ):
        cell("unread", f"never_seg_{fault}", study(meta_seg_DOT_txt=seg, d_DOT_seg=content))
    matrices = {
        "blank_line": VALID + b"\n",
        "unknown_sample": tsv([("Hugo_Symbol", "S1", "S2", "S9"), R1]),
        "value": tsv([HEADER, ("TP53", "0", "2", "x")]),
        "padded_value": tsv([HEADER, ("TP53", "0", " 2", "NA")]),
        "duplicate_gene": tsv([HEADER, R1, R1]),
    }
    beside = {
        "pd_ok": {"meta_cna_DOT_txt": pd_meta("data_pd.txt"), "data_pd_DOT_txt": PD},
        "pd_bad": {"meta_cna_DOT_txt": pd_meta("data_pd.txt"), "data_pd_DOT_txt": bad},
        "never_bad": {
            "meta_other_DOT_txt": never_meta("CONTINUOUS", "linear_CNA", show="true"),
            "d_DOT_txt": tsv([("Hugo_Symbol", "S9"), ("TP53", "x")]),
        },
    }
    for (fault, matrix), (label, over) in itertools.product(matrices.items(), beside.items()):
        cell("unread", f"beside_{fault}_{label}", study(data_cna_DOT_txt=matrix, **over))
    for label, target in (("pd_ok", PD), ("pd_bad", bad)):
        cell(
            "unread",
            f"fold_reads_{label}",
            study(
                meta_cna_DOT_txt=pd_meta("data_pd.txt", "DATA_CLINICAL_SAMPLE.TXT"),
                DATA_CLINICAL_SAMPLE_DOT_TXT=VALID,
                data_pd_DOT_txt=target,
            ),
        )


# --- name: the header's gene-column names, near and exact ----------------------------------------


def _padding(pad: str, where: str) -> Callable[[str], str]:
    def made(name: str) -> str:
        return pad + name if where == "pre" else name + pad

    return made


NAME_VARIANTS: tuple[tuple[str, Callable[[str], str]], ...] = (
    *(
        (
            f"{where}_{label}",
            _padding(pad, where),
        )
        for label, pad in (("space", " "), ("nbsp", "\xa0"), ("zwsp", "\u200b"))
        for where in ("pre", "post")
    ),
    ("lower", str.lower),
    ("upper", str.upper),
    ("swapcase", str.swapcase),
)
"""A gene column's name made near: padded before or after by a space, a no-break space or a zero
width space, or in another case."""
NAME_POSITIONS = ("first", "after_gene", "among_samples")


def name_header(name: str, variant: str, position: str) -> tuple[str, ...]:
    """A header holding ``variant`` (a near spelling of the gene column ``name``) at ``position``:
    first, after a valid gene column (``Hugo_Symbol``, or ``Entrez_Gene_Id`` for the symbol's own),
    or among the samples."""
    other = "Entrez_Gene_Id" if name == "Hugo_Symbol" else "Hugo_Symbol"
    if position == "first":
        return (variant, other, *SAMPLES)
    if position == "after_gene":
        return (other, variant, *SAMPLES)
    return (other, "S1", variant, "S2", "S3")


def name_row(header: Sequence[str]) -> tuple[str, ...]:
    values = {"Hugo_Symbol": "TP53", "Entrez_Gene_Id": "7157", "Cytoband": "17p"}
    return tuple(values.get(column, "0") for column in header)


def _names() -> None:
    """Review r2 MJ-B: each gene column's name, near (``NAME_VARIANTS``) at each position, and
    next to an empty name (a tab beside it)."""
    for name in ("Cytoband", "Hugo_Symbol", "Entrez_Gene_Id"):
        for (label, made), position in itertools.product(NAME_VARIANTS, NAME_POSITIONS):
            header = name_header(name, made(name), position)
            cell("name", f"{name}_{label}_{position}", data(tsv([header, name_row(header)])))
        for position in NAME_POSITIONS:
            header = name_header(name, name, position)
            at = header.index(name)
            beside = (*header[: at + 1], "", *header[at + 1 :])
            cell("name", f"{name}_tab_after_{position}", data(tsv([beside, name_row(beside)])))


# --- mark: a byte order mark on every meta and case list, and the lists the reference requires ---

SEG_TEMPLATE = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
    "datatype: SEG\nreference_genome_id: hg19\ndescription: Segments\ndata_filename: {}\n"
)
SEGMENT = b"ID\tchrom\tloc.start\tloc.end\tnum.mark\tseg.mean\nS1\t1\t1000\t2000\t10\t0.1\n"


def _mark_bases() -> dict[str, Cell]:
    """The studies whose every meta and case list the ``mark`` generator leads by a mark: the
    valid one, one with a PD file, one with a refused matrix, and one with each never-imported
    copy-number meta."""
    bases: dict[str, Cell] = {
        "valid": study(),
        "pd": study(meta_cna_DOT_txt=pd_meta("data_pd.txt"), data_pd_DOT_txt=PD),
        "badmatrix": data(tsv([HEADER, ("TP53", "0", "x", "NA")])),
        "never_seg": study(
            meta_seg_DOT_txt=SEG_TEMPLATE.format("d.seg").encode(), d_DOT_seg=SEGMENT
        ),
    }
    for name, (datatype, stable, content) in NEVER_METAS.items():
        bases[f"never_{name}"] = study(
            meta_other_DOT_txt=never_meta(datatype, stable), d_DOT_txt=content
        )
    return bases


def marked_files(files: Mapping[str, Content]) -> list[str]:
    """A cell's metas and case lists, the files a leading mark can lead."""
    return sorted(
        name
        for name, content in files.items()
        if isinstance(content, bytes)
        and (name.startswith("case_lists/") or ("/" not in name and "meta" in name))
    )


def _marks() -> None:
    """Review r3 MJ-1: each meta and each case list of each base led by a byte order mark (and,
    on the valid study, with CRLF line ends too); each base with no ``_all`` list (the study
    meta's ``add_global_case_list`` gone), with no ``_cna`` list, and with neither; each
    never-imported copy-number meta type twice; and the round 3 review's directed cells."""
    no_global = BASE.STUDY.replace(b"add_global_case_list: true\n", b"")
    for base, files in _mark_bases().items():
        for name in marked_files(files):
            label = name.replace("case_lists/", "list_").replace(".", "_")
            content = files[name]
            assert isinstance(content, bytes)
            cell("mark", f"{base}_bom_{label}", {**files, name: BOM + content})
            if base == "valid":
                crlf = BOM + content.replace(b"\n", b"\r\n")
                cell("mark", f"{base}_bomcrlf_{label}", {**files, name: crlf})
        cell("mark", f"{base}_no_all", {**files, "meta_study.txt": no_global})
        without = {key: value for key, value in files.items() if key != CNA_LIST}
        cell("mark", f"{base}_no_cna_list", without)
        cell("mark", f"{base}_no_all_no_cna_list", {**without, "meta_study.txt": no_global})
    for name, (datatype, stable, content) in NEVER_METAS.items():
        cell(
            "mark",
            f"twice_{name}",
            study(
                meta_other_DOT_txt=never_meta(datatype, stable),
                meta_other2_DOT_txt=never_meta(datatype, stable, data_value="e.txt"),
                d_DOT_txt=content,
                e_DOT_txt=content,
            ),
        )
    cell(
        "mark",
        "twice_seg",
        study(
            meta_seg_DOT_txt=SEG_TEMPLATE.format("a.seg").encode(),
            meta_seg2_DOT_txt=SEG_TEMPLATE.format("b.seg").encode(),
            a_DOT_seg=SEGMENT,
            b_DOT_seg=SEGMENT,
        ),
    )
    plain = meta()
    for name, files in (
        ("bom_meta", study(meta_cna_DOT_txt=BOM + plain)),
        ("bom_meta_crlf", study(meta_cna_DOT_txt=BOM + plain.replace(b"\n", b"\r\n"))),
        ("bom_list", study(case_lists__cases_cna_DOT_txt=BOM + cna_list())),
        (
            "bom_meta_badmatrix",
            study(
                meta_cna_DOT_txt=BOM + plain,
                data_cna_DOT_txt=tsv([HEADER, ("TP53", "0", "x", "NA")]),
            ),
        ),
        ("cna_only_no_all_list", study(meta_study_DOT_txt=no_global)),
        (
            "never_seg_twice",
            study(
                meta_seg_DOT_txt=SEG_TEMPLATE.format("a.seg").encode(),
                meta_seg2_DOT_txt=SEG_TEMPLATE.format("b.seg").encode(),
                a_DOT_seg=SEGMENT,
                b_DOT_seg=SEGMENT,
            ),
        ),
    ):
        cell("mark", f"r3_{name}", files)


# --- compare: every name and id the pack compares, spelt near ------------------------------------

STRIPPED = (
    " ",
    "\x85",
    "\xa0",
    "\u1680",
    *(chr(point) for point in range(0x2000, 0x200B)),
    "\u2028",
    "\u2029",
    "\u202f",
    "\u205f",
    "\u3000",
)
"""The 20 characters ``str.strip()`` removes that a cell can hold (``test_cna.PADDING``)."""


def fullwidth(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if c.isascii() and c.isalnum() else c for c in text)


def spellings(base: str) -> list[tuple[str, str]]:
    """Review r3 MJ-2: ``base`` and every spelling near it the pack must not take for it: padded
    before or after by each of the 20 characters ``str.strip()`` removes and by the three it
    keeps (``NON_STRIPPED``), in NFC and NFD, with its compatibility forms (a Kelvin sign for
    ``K``, full-width letters and digits, whose NFKC is ``base``), and in other cases."""
    out = [("exact", base)]
    for pad in (*STRIPPED, *NON_STRIPPED):
        out += [(f"pre_{ord(pad):04x}", pad + base), (f"post_{ord(pad):04x}", base + pad)]
    out += [
        ("nfc", unicodedata.normalize("NFC", base)),
        ("nfd", unicodedata.normalize("NFD", base)),
        ("kelvin", base.replace("K", "\u212a").replace("k", "\u212a")),
        ("fullwidth", fullwidth(base)),
        ("lower", base.lower()),
        ("upper", base.upper()),
        ("swapcase", base.swapcase()),
    ]
    seen: set[str] = {base}
    unique = [out[0]]
    for label, spelled in out[1:]:
        if spelled not in seen:
            seen.add(spelled)
            unique.append((label, spelled))
    return unique


def with_samples(*ids: str) -> bytes:
    """The sample file with ``ids`` added, each a sample of patient ``P1``."""
    return BASE.SAMPLES + "".join(f"P1\t{found}\n" for found in ids).encode()


def _compare() -> None:
    """Review r3 MJ-2's live cells: a ``_cna`` list's id near the study's (a zero width space, a
    no-break space, full-width letters), header sample ids near the sample file's (a Kelvin sign,
    full-width; a Kelvin sign is K under NFC and NFKC alike, and a sample id is ASCII, so no NFD
    spelling of one is another) and two ids the sample file holds that differ in case alone."""
    for name, suffix in (
        ("zwsp", "cna\u200b"),
        ("nbsp", "cna\xa0"),
        ("fullwidth", "\uff43na"),
        ("upper", "CNA"),
    ):
        cell(
            "compare",
            f"list_stable_{name}",
            study(case_lists__cases_cna_DOT_txt=cna_list(suffix=suffix)),
        )
    for name, sample, written in (
        ("kelvin_header", "K1", "\u212a1"),
        ("fullwidth_header", "S4", fullwidth("S4")),
        ("case_pair", "s1", "s1"),
    ):
        header = ("Hugo_Symbol", "S1", "S2", "S3", written)
        files = study(
            data_clinical_sample_DOT_txt=with_samples(sample),
            data_cna_DOT_txt=tsv([header, ("TP53", "0", "2", "NA", "2")]),
        )
        cell("compare", name, files)


# --- shared: the shared modules' refusals of the CNA meta and its lists, out of the CNA map -------


def _shared() -> None:
    """Review r3 minors 3 and 4: the faults of a meta or a case list the shared parsers judge
    (``meta``, ``study``, ``caselists``; D409, D444), which the oracle predicts exactly and the
    judge holds to ``OUT_OF_MAP``, and the meta and list spellings they accept."""
    plain = meta()
    listed = cna_list()
    stable = f"stable_id: {SID}_cna".encode()
    for name, content in (
        ("meta_value_vt_mid", plain.replace(b"stable_id: cna", b"stable_id: cna\x0bstable_id: x")),
        ("meta_latin1", plain.replace(b"profile_name: CNA", b"profile_name: CN\xe9")),
        ("meta_no_colon", plain + b"no colon here\n"),
        ("meta_cr_only", plain.replace(b"\n", b"\r")),
        ("meta_no_final_end", plain.rstrip(b"\n")),
        ("meta_stable_nbsp", plain.replace(b"stable_id: cna", "stable_id: cna\xa0".encode())),
        ("meta_stable_zwsp", plain.replace(b"stable_id: cna", "stable_id: cna\u200b".encode())),
    ):
        cell("shared", name, study(meta_cna_DOT_txt=content))
    for key in ("cancer_study_identifier", "stable_id", "profile_name", "data_filename"):
        lines = never_meta("CONTINUOUS", "linear_CNA").split(b"\n")
        kept = b"\n".join(line for line in lines if not line.startswith(key.encode() + b":"))
        cell(
            "shared",
            f"never_missing_{key}",
            study(meta_other_DOT_txt=kept, d_DOT_txt=NEVER_METAS["continuous"][2]),
        )
    for name, content in (
        ("list_key_bom_mid", listed.replace(b"stable_id:", BOM + b"stable_id:")),
        ("list_latin1", listed.replace(b"case_list_name: n", b"case_list_name: \xe9")),
        ("list_no_colon", listed + b"no colon here\n"),
        ("list_control", listed.replace(b"case_list_name: n", b"case_list_name: n\x01")),
        ("list_category", listed.replace(b"all_cases_with_cna_data", b"all_cases_with_x")),
        (
            "list_other_study",
            listed.replace(
                b"cancer_study_identifier: " + SID.encode(), b"cancer_study_identifier: other"
            ),
        ),
        ("list_prefix", listed.replace(stable, b"stable_id: other_cna")),
        ("list_cr_only", listed.replace(b"\n", b"\r")),
        ("list_tab_pad", listed.replace(stable, b"stable_id:\t" + stable[11:] + b"\t")),
        ("list_ids_padded", listed.replace(b"S1\tS2\tS3", b"S1 \t S2\tS3")),
    ):
        cell("shared", name, study(case_lists__cases_cna_DOT_txt=content))
    cell("shared", "list_repeat", study(case_lists__cases_cna2_DOT_txt=listed))
    without = study(case_lists__cases_cna_DOT_txt=None)
    for name, path in (
        ("list_backup_only", "case_lists/cases_cna.txt~"),
        ("list_hidden_only", "case_lists/.cases_cna.txt"),
    ):
        cell("shared", name, {**without, path: listed})
    cell("shared", "list_plus_backup", {**study(), "case_lists/cases_cna.txt~": listed})


# --- type: the CNA study beside a meta of each C-table type the pack never imports ----------------


def _oracle_tables() -> ModuleType:
    path = Path(__file__).resolve().parent / "cna_oracle.py"
    spec = importlib.util.spec_from_file_location("cna_oracle_for_corpus", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TYPE_VALUES: Mapping[str, str] = {
    "cancer_study_identifier": SID,
    "show_profile_in_analysis_tab": "false",
    "reference_genome_id": "hg19",
    "geneset_def_version": "1",
    "source_stable_id": "s_source",
}


def type_meta(pair: str, keys: Sequence[str], data_value: str) -> bytes:
    """A meta of the C table's type ``pair`` (``<genetic_alteration_type>:<datatype>``, or
    ``resource:<resource_type>``) holding its mandatory ``keys``."""
    kind, _, datatype = pair.partition(":")
    values = dict(TYPE_VALUES)
    if kind == "resource":
        values["resource_type"] = datatype
    else:
        values["genetic_alteration_type"] = kind
        values["datatype"] = datatype
    values["stable_id"] = "s_" + "".join(c for c in pair.lower() if c.isalnum())
    values["data_filename"] = data_value
    lines = [f"{key}: {values.get(key, 'x')}" for key in keys]
    return ("\n".join(lines) + "\n").encode()


def _types() -> None:
    """Review r4 m1: the CNA study beside a meta of each C-table type the pack never imports (each
    pair of ``PAIR_TYPES`` but the clinical, MAF, SV and discrete CNA ones, and each resource
    type), its data file valid, naming a sample the study does not hold, or missing."""
    tables = _oracle_tables()
    pairs = {
        pair: kind for pair, kind in tables.PAIR_TYPES.items() if kind not in tables.IMPORTED_TYPES
    }
    pairs.update({f"resource:{key}": kind for key, kind in tables.RESOURCE_TYPES.items()})
    # The gene panel matrix's type, imported from M4.2b-2a (D447), keeps its three cells: the
    # matrix is refused before the CNA file is read, out of this map (``cna_checks.SHARED``).
    pairs["GENE_PANEL_MATRIX:GENE_PANEL_MATRIX"] = "GENE_PANEL_MATRIX"
    for pair, kind in sorted(pairs.items()):
        label = "".join(c if c.isalnum() else "_" for c in pair.lower())
        keys = tables.MANDATORY[kind]
        meta_file = type_meta(pair, keys, "d_type.txt")
        for fault, content in (
            ("valid", b"Hugo_Symbol\tS1\nTP53\t1\n"),
            ("unknown_sample", b"Hugo_Symbol\tS9\nTP53\t1\n"),
            ("missing", None),
        ):
            cell(
                "type",
                f"{label}_{fault}",
                study(meta_type_DOT_txt=meta_file, d_type_DOT_txt=content),
            )


def _review4() -> None:
    """Review r4 m3 and m5: a repeated meta key (the last wins), ``add_global_case_list`` in other
    cases beside an ``_all`` list (the global list repeats it), and an OncoKB backup meta the
    reference and the pack skip."""
    cell(
        "review4",
        "meta_repeat_key_last_wins",
        study(
            meta_cna_DOT_txt=meta().replace(b"stable_id: cna", b"stable_id: other\nstable_id: cna")
        ),
    )
    all_list = BASE.case_list("all", list(SAMPLES), category="all_cases_in_study")
    for spelled in ("TRUE", "True", "true"):
        cell(
            "review4",
            f"global_{spelled}_with_all_list",
            study(
                meta_study_DOT_txt=BASE.STUDY.replace(b": true\n", f": {spelled}\n".encode()),
                case_lists__cases_all_DOT_txt=all_list,
            ),
        )
    cell(
        "review4",
        "oncokb_backup_meta",
        study(
            ONCOKB_IMPORT_BACKUP_meta_cna_DOT_txt=meta(stable="gistic", data="data_cna2.txt"),
            data_cna2_DOT_txt=VALID,
        ),
    )
    # The fourth review's named cells, reduced to the files that decide them (a meta of a
    # type the pack never imports, its data file faulty or missing; a meta of a mark alone).
    for name, files in (
        (
            "q9401_81",
            {
                "meta_gene_panel_matrix.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: GENE_PANEL_MATRIX\n"
                    b"datatype: GENE_PANEL_MATRIX\n"
                    b"stable_id: gene_panel_matrix\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: nope.txt\n"
                ),
            },
        ),
        (
            "q9401_96",
            {
                "meta_rna_seq_mrna_median_Zscores.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: MRNA_EXPRESSION\n"
                    b"datatype: Z-SCORE\n"
                    b"stable_id: rna_seq_mrna_median_Zscores\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: data_mutations.txt\n"
                ),
            },
        ),
        (
            "q9404_24",
            {
                "data_rppa.txt": (b"Hugo_Symbol\tEntrez_Gene_Id\tS1\tS9\nTP53\t7157\tabc\t1\n"),
                "meta_rppa.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: PROTEIN_LEVEL\n"
                    b"datatype: LOG2-VALUE\n"
                    b"stable_id: rppa\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: data_rppa.txt\n"
                ),
            },
        ),
        (
            "q9404_85",
            {
                "data_rna_seq_mrna.txt": (
                    b"Hugo_Symbol\tEntrez_Gene_Id\tS1\tS2\tS3\nTP53\t7157\t0.5\t1.2\tNA\n"
                ),
                "data_rna_seq_mrna_median_Zscores.txt": (
                    b"Hugo_Symbol\tEntrez_Gene_Id\tS1\tS9\nTP53\t7157\tabc\t1\n"
                ),
                "meta_rna_seq_mrna.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: MRNA_EXPRESSION\n"
                    b"datatype: CONTINUOUS\n"
                    b"stable_id: rna_seq_mrna\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: data_rna_seq_mrna.txt\n"
                ),
                "meta_rna_seq_mrna_median_Zscores.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: MRNA_EXPRESSION\n"
                    b"datatype: Z-SCORE\n"
                    b"stable_id: rna_seq_mrna_median_Zscores\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: data_rna_seq_mrna_median_Zscores.txt\n"
                ),
            },
        ),
        (
            "q9406_28",
            {
                "data_rppa.txt": (b"Hugo_Symbol\tEntrez_Gene_Id\tS1\tS9\nTP53\t7157\tabc\t1\n"),
                "data_timeline.txt": (
                    b"PATIENT_ID\tSTART_DATE\tSTOP_DATE\tEVENT_TYPE\nP1\t0\t1\tTREATMENT\n"
                ),
                "meta_rppa.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: PROTEIN_LEVEL\n"
                    b"datatype: LOG2-VALUE\n"
                    b"stable_id: rppa\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: data_rppa.txt\n"
                ),
                "meta_timeline.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: CLINICAL\n"
                    b"datatype: TIMELINE\n"
                    b"data_filename: data_timeline.txt\n"
                ),
            },
        ),
        (
            "q9406_34",
            {
                "data_rppa.txt": (b"Hugo_Symbol\tEntrez_Gene_Id\tS1\tS9\nTP53\t7157\tabc\t1\n"),
                "meta_rppa.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: PROTEIN_LEVEL\n"
                    b"datatype: LOG2-VALUE\n"
                    b"stable_id: rppa\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: data_rppa.txt\n"
                ),
            },
        ),
        (
            "q9406_67",
            {
                "data_rna_seq_mrna.txt": (
                    b"Hugo_Symbol\tEntrez_Gene_Id\tS1\tS2\tS3\nTP53\t7157\t0.5\t1.2\tNA\n"
                ),
                "data_rna_seq_mrna_median_Zscores.txt": (
                    b"Hugo_Symbol\tEntrez_Gene_Id\tS1\tS9\nTP53\t7157\tabc\t1\n"
                ),
                "meta_rna_seq_mrna.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: MRNA_EXPRESSION\n"
                    b"datatype: CONTINUOUS\n"
                    b"stable_id: rna_seq_mrna\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: data_rna_seq_mrna.txt\n"
                ),
                "meta_rna_seq_mrna_median_Zscores.txt": (
                    b"cancer_study_identifier: demo_study\n"
                    b"genetic_alteration_type: MRNA_EXPRESSION\n"
                    b"datatype: Z-SCORE\n"
                    b"stable_id: rna_seq_mrna_median_Zscores\n"
                    b"show_profile_in_analysis_tab: false\n"
                    b"profile_name: x\n"
                    b"profile_description: x\n"
                    b"data_filename: data_rna_seq_mrna_median_Zscores.txt\n"
                ),
            },
        ),
        (
            "q9403_104",
            {
                "meta_cna.txt": (b"\xef\xbb\xbf"),
            },
        ),
    ):
        cell("review4", name, {**study(), **files})


_bytes()
_header()
_cells()
_genes()
_dups()
_metas()
_roles()
_pairs()
_review()
_unread()
_names()
_marks()
_compare()
_shared()
_types()
_review4()
