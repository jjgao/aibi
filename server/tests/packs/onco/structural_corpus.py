"""The structural variant corpus of M4.2b-4 (D445): cells made by generators, never listed one by
one, each a small study that differs from a valid one by one change.

This is not a test: pytest does not collect it, and it imports nothing of the pack or of aibi. It
loads ``mutation_corpus.py`` (the slice-1b corpus, a stable helper: the study meta, the clinical
files and the case list) by its path, so that the scratch harness that runs each cell on the pinned
reference (cbioportal-core 47890bb, offline, not relaxed; D409) can load it as it is, and
``test_structural_corpus.py`` loads the same cells. The reference's outcome of every cell is
recorded in ``structural_reference.json`` by error identity, with the file each error names: whether
it crashed and the identity of each error it reported, in order (no message of the reference).

A cell is a mapping of a path below the study's directory to its content (``bytes``,
``DIRECTORY``, or ``("symlink", target)``), as in ``mutation_corpus``; ``CELLS`` maps each cell's
id, ``<generator>:<case>``, to it. The valid study is the slice-1b study without its MAF (no
mutation meta, so no ``_sequenced`` list is needed), with an SV meta and an SV file of two events.
The generators:

- ``byte``: the SV file's bytes (byte order marks, line ends, blank and comment lines, the repeated
  header, row widths, an empty file, control characters, bytes that are not UTF-8, long fields, a
  file with no tab, the data file missing, a directory, a symlink);
- ``header``: the SV file's columns (each dropped in turn, a site's pair, a repeated, padded or
  empty name, the namespaces);
- ``cell``: a token in one of the seven columns the reference reads (``sample``, ``status``,
  ``build``, a site's ``hugo`` and ``entrez``) on a row of a class (``gene``: the other site names
  a gene; ``alone``: no other column names one), and the positions, which it never reads;
- ``build``: two rows' builds, and the study's genome;
- ``dup``: rows the reference keys alike, or not;
- ``meta``: the SV meta's values, a second SV meta, the SV meta naming another imported file, and
  another meta naming the SV file.
"""

import importlib.util
import itertools
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType


def _base() -> ModuleType:
    path = Path(__file__).resolve().parent / "mutation_corpus.py"
    spec = importlib.util.spec_from_file_location("mutation_corpus_for_sv", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = _base()
DIRECTORY = BASE.DIRECTORY
SID = BASE.SID
BOM = BASE.BOM
SV_FILE = "data_sv.txt"
SV_META = "meta_sv.txt"

META = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: STRUCTURAL_VARIANT\ndatatype: SV\n"
    "stable_id: structural_variants\nshow_profile_in_analysis_tab: true\nprofile_name: SV\n"
    f"profile_description: SV\ndata_filename: {SV_FILE}\n"
)
COLUMNS = (
    "Sample_Id",
    "SV_Status",
    "Site1_Hugo_Symbol",
    "Site1_Entrez_Gene_Id",
    "Site1_Chromosome",
    "Site1_Position",
    "Site2_Hugo_Symbol",
    "Site2_Entrez_Gene_Id",
    "Site2_Chromosome",
    "Site2_Position",
    "NCBI_Build",
    "Event_Info",
    "Class",
)
R1: Mapping[str, str] = dict(
    zip(
        COLUMNS,
        (
            "S1",
            "SOMATIC",
            "EML4",
            "27436",
            "2",
            "42522656",
            "ALK",
            "238",
            "2",
            "29446394",
            "GRCh37",
            "EML4-ALK fusion",
            "FUSION",
        ),
        strict=True,
    )
)
R2: Mapping[str, str] = {
    **R1,
    "Sample_Id": "S2",
    "Site1_Hugo_Symbol": "TMPRSS2",
    "Site1_Entrez_Gene_Id": "7113",
    "Site1_Chromosome": "21",
    "Site1_Position": "42870045",
    "Site2_Hugo_Symbol": "ERG",
    "Site2_Entrez_Gene_Id": "2078",
    "Site2_Chromosome": "21",
    "Site2_Position": "39817544",
    "Event_Info": "TMPRSS2-ERG fusion",
}


def sv(
    rows: list[Mapping[str, str]],
    columns: tuple[str, ...] = COLUMNS,
    top: bytes = b"",
    end: str = "\n",
) -> bytes:
    """An SV file of ``rows`` under ``columns`` (a cell a row lacks is empty), after ``top``."""
    lines = ["\t".join(columns), *("\t".join(row.get(c, "") for c in columns) for row in rows)]
    return top + (end.join(lines) + end).encode()


Content = bytes | str | tuple[str, str]
Cell = dict[str, Content]
VALID = sv([R1, R2])


def study(**over: Content | None) -> Cell:
    """The valid study (the slice-1b study without its MAF, with the SV meta and file), with
    ``over`` applied as ``mutation_corpus.study`` applies it."""
    files: Cell = BASE.study(meta_mutations_DOT_txt=None, data_mutations_DOT_txt=None)
    files[SV_META] = META.encode()
    files[SV_FILE] = VALID
    for key, value in over.items():
        path = key.replace("__", "/").replace("_DOT_", ".")
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value
    return files


def data(content: bytes) -> Cell:
    return study(data_sv_DOT_txt=content)


def row(**changes: str) -> dict[str, str]:
    return {**R1, **changes}


CELLS: dict[str, Cell] = {}


def cell(generator: str, case: str, files: Cell) -> None:
    key = f"{generator}:{case}"
    assert key not in CELLS, key
    CELLS[key] = files


# --- byte -----------------------------------------------------------------------------------------


def _bytes() -> None:
    cell("byte", "valid", study())
    cell("byte", "bom", data(BOM + VALID))
    cell("byte", "bom_with_top", data(BOM + sv([R1, R2], top=b"#v\n")))
    cell("byte", "bom_at_row", data(sv([R2, row(Event_Info="\ufeffx")])))
    cell("byte", "top_lines", data(sv([R1, R2], top=b"#a\n#b\n")))
    cell("byte", "crlf", data(VALID.replace(b"\n", b"\r\n")))
    cell("byte", "cr", data(VALID.replace(b"\n", b"\r")))
    cell("byte", "mixed", data(VALID.replace(b"\n", b"\r\n", 1)))
    cell("byte", "trailing_blank", data(VALID + b"\n"))
    blank_tabs = sv([R1]) + b"\t" * (len(COLUMNS) - 1) + b"\n" + sv([R2]).split(b"\n", 1)[1]
    cell("byte", "blank_tabs", data(blank_tabs))
    cell("byte", "hash_line", data(sv([R1, row(Sample_Id="#S3")])))
    cell("byte", "hash_after_space", data(sv([R1, row(Sample_Id=" #S3")])))
    cell("byte", "hash_after_header", data(VALID + b"#late\n"))
    repeated: dict[str, str] = dict(zip(COLUMNS, COLUMNS, strict=True))
    cell("byte", "repeated_header", data(sv([R1, repeated, R2])))
    longer = {**repeated, "Class": "ClassX"}
    cell("byte", "repeated_header_last_name_longer", data(sv([R1, longer, R2])))
    extra = ("\t".join(R2[c] for c in COLUMNS) + "\textra\n").encode()
    cell("byte", "extra_field", data(sv([R1]) + extra))
    short = ("\t".join(R2[c] for c in COLUMNS[:-1]) + "\n").encode()
    cell("byte", "short_row", data(sv([R1]) + short))
    cell("byte", "header_only", data(sv([])))
    header = "\t".join(COLUMNS).encode()
    cell("byte", "header_no_line_end", data(header))
    cell("byte", "header_no_line_end_top", data(b"#v\n" + header))
    cell("byte", "empty", data(b""))
    cell("byte", "top_only", data(b"#a\n#b\n"))
    cell("byte", "blank_first_line", data(b"\n" + VALID))
    cell("byte", "bom_alone_first_line", data(BOM + b"\n" + VALID))
    for name, character in (
        ("nul", "\x00"),
        ("u0001", "\x01"),
        ("vt", "\x0b"),
        ("del", "\x7f"),
        ("esc", "\x1b"),
        ("u001f", "\x1f"),
    ):
        cell("byte", f"control_{name}", data(sv([R2, row(Event_Info="x" + character)])))
        cell("byte", f"control_{name}_sample", data(sv([R2, row(Sample_Id="S1" + character)])))
    bad = sv([R2, row(Event_Info="x\u00e9")]).replace("x\u00e9".encode(), b"x\xff")
    cell("byte", "not_utf8", data(bad))
    cell("byte", "field_131073", data(sv([R2, row(Event_Info="e" * 131073)])))
    cell("byte", "field_131072", data(sv([R2, row(Event_Info="e" * 131072)])))
    cell("byte", "quote_cell", data(sv([R2, row(Event_Info='"x"')])))
    cell("byte", "missing_file", study(data_sv_DOT_txt=None))
    cell("byte", "sv_is_directory", study(data_sv_DOT_txt=DIRECTORY))
    cell(
        "byte",
        "sv_symlink",
        study(data_sv_DOT_txt=("symlink", "real_sv.txt"), real_sv_DOT_txt=VALID),
    )
    cell("byte", "no_tab", data(b"Sample_Id\nS1\nS2\n"))
    cell("byte", "no_tab_seven_lines", data(b"Sample_Id\nS1\nS2\nS3\nS1\nS2\nS3\nx\ty\n"))
    cell("byte", "one_column_tab_later", data(b"Sample_Id\nS1\nS2\tx\n"))
    noncharacter = META.replace(SV_FILE, "data_\ufffe.txt").encode()
    cell(
        "byte",
        "sv_name_noncharacter",
        study(meta_sv_DOT_txt=noncharacter, data_sv_DOT_txt=None, **{"data_\ufffe_DOT_txt": VALID}),
    )


# --- header ---------------------------------------------------------------------------------------


def _header() -> None:
    for column in COLUMNS:
        kept = tuple(c for c in COLUMNS if c != column)
        cell("header", f"without_{column}", data(sv([R1, R2], kept)))
    for site in ("Site1", "Site2"):
        pair = (f"{site}_Hugo_Symbol", f"{site}_Entrez_Gene_Id")
        kept = tuple(c for c in COLUMNS if c not in pair)
        cell("header", f"without_{site}_pair", data(sv([R1, R2], kept)))
    entrez = ("Sample_Id", "SV_Status", "Site1_Entrez_Gene_Id", "Site2_Entrez_Gene_Id")
    cell("header", "minimal_entrez", data(sv([R1, R2], entrez)))
    hugo = ("Sample_Id", "SV_Status", "Site1_Hugo_Symbol", "Site2_Hugo_Symbol")
    cell("header", "minimal_hugo", data(sv([R1, R2], hugo)))
    cell("header", "repeated_name", data(sv([R1], (*COLUMNS, "Class"))))
    padded = (" Sample_Id", *COLUMNS[1:])
    cell("header", "padded_required", data(sv([{**R1, " Sample_Id": "S1"}], padded)))
    lower = ("sample_id", *COLUMNS[1:])
    cell("header", "lower_required", data(sv([{**R1, "sample_id": "S1"}], lower)))
    lower_site = tuple(
        c.lower() if c in ("Site1_Hugo_Symbol", "Site1_Entrez_Gene_Id") else c for c in COLUMNS
    )
    renamed = {**R1, "site1_hugo_symbol": "EML4", "site1_entrez_gene_id": "27436"}
    cell("header", "lower_site_pair", data(sv([renamed], lower_site)))
    cell("header", "custom", data(sv([{**R1, "My_Column": "x"}], (*COLUMNS, "My_Column"))))
    for index in range(0, len(COLUMNS) + 1, 4):
        columns = (*COLUMNS[:index], "", *COLUMNS[index:])
        cell("header", f"empty_name_at_{index}", data(sv([{**R1, "": "x"}], columns)))
    cell("header", "name_nonchar", data(sv([{**R1, "X\ufffe": "1"}], (*COLUMNS, "X\ufffe"))))
    cell("header", "name_4097", data(sv([{**R1, "X" * 4097: "1"}], (*COLUMNS, "X" * 4097))))
    wide = (*COLUMNS, *(f"c{index}" for index in range(4097 - len(COLUMNS) + 1)))
    cell("header", "columns_4097", data(sv([R1], wide)))


NAMESPACES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("with_column", "fusion", ("fusion.caller",)),
    ("without_column", "fusion", ()),
    ("column_case", "fusion", ("FUSION.Caller",)),
    ("upper", "FUSION", ("fusion.caller",)),
    ("padded", " fusion ", ("fusion.caller",)),
    ("second_missing", "fusion, zzz", ("fusion.caller",)),
    ("trailing_comma", "fusion,", ("fusion.caller",)),
    ("empty", "", ("fusion.caller",)),
    ("no_dot", "fusion", ("fusion",)),
    ("ascn", "ascn", ("ascn.x",)),
    ("ascn_upper", "ASCN", ("ascn.x",)),
    ("ascn_two", "ascn,foo", ("ascn.x", "foo.y")),
    ("ascn_without_column", "ascn", ()),
    ("kelvin", "kelvin", ("\u212aELVIN.x",)),
    ("exact_prefix_column", "fusion", ("fusion.",)),
    ("second_padded", "fusion,  ascn ", ("fusion.caller", "ascn.x")),
    ("before_every_column", "aaa", ("aaa.x",)),
    ("after_every_column", "zzz", ("zzz.x",)),
    ("between_columns", "site1_x", ("Site1_X.y",)),
)
"""The namespaces dimension (V:1415-1424): a namespace with its column, without, in another case,
padded, two of which one has no column, a trailing comma and an empty value (an empty namespace,
which only a column starting with ``.`` matches), a column with no dot, ``ascn`` (which the SV
reference does not ask its ASCN columns for), and Unicode case."""


def _namespaces() -> None:
    for name, spaces, columns in NAMESPACES:
        meta = (META + f"namespaces: {spaces}\n").encode()
        body = sv([{**R1, **dict.fromkeys(columns, "1")}, R2], (*COLUMNS, *columns))
        cell("namespace", name, study(meta_sv_DOT_txt=meta, data_sv_DOT_txt=body))


# --- cell -----------------------------------------------------------------------------------------

SAMPLE_TOKENS = ("S1", " S1", "S1 ", "S1\u00a0", "\u3000S1", "S9", "", " ", "NA", "0", "Unknown")
SAMPLE_TOKENS += (" NA", "s1", "S1\u2028", "N/A")
STATUS_TOKENS = ("SOMATIC", "somatic", "Somatic", " SOMATIC", "SOMATIC\u00a0", "GERMLINE")
STATUS_TOKENS += ("germline", "", " ", "NA", "0", "Unknown", " NA", "NA\u3000", "\u00a00")
STATUS_TOKENS += ("unknown", "N/A", "LIKELY_SOMATIC", "[Not Available]")
BUILD_TOKENS = ("GRCh37", "grch37", " GRCh37", "37", "hg19", "GRCh38", "GRCm38", "", " ", "NA")
BUILD_TOKENS += ("0", "Unknown", " Unknown", "NA\u2029")
HUGO_TOKENS = ("EML4", "", " ", "NA", "Unknown", "0", " NA", "Unknown ", "NA\u00a0", "\u30000")
HUGO_TOKENS += (" 0", "unknown", "N/A", "123ABC", " EML4")
ENTREZ_TOKENS = ("27436", "", " ", "NA", "Unknown", "0", " 0", "0 ", " 0 ", "\u00a00", "0\u3000")
ENTREZ_TOKENS += ("00", "+0", "-0", "-1", "\u0660", "0_0", "+5", "1e3", "-1e3", "0.0", "7157.0")
ENTREZ_TOKENS += (" NA", "NA ", "abc", "\u0661", "1_0", "7157\u00a0", "nan", "inf", "0x10")
ENTREZ_TOKENS += ("\uff17", " 7157", "9223372036854775808")
POSITION_TOKENS = ("29446394", "", "NA", "abc", "1.5", "-3", "1e3", " 5", "\u0665", "1_0")
TOKENS: Mapping[str, tuple[str, ...]] = {
    "sample": SAMPLE_TOKENS,
    "status": STATUS_TOKENS,
    "build": BUILD_TOKENS,
    "hugo1": HUGO_TOKENS,
    "hugo2": HUGO_TOKENS,
    "entrez1": ENTREZ_TOKENS,
    "entrez2": ENTREZ_TOKENS,
    "position": POSITION_TOKENS,
}
"""Each column class's tokens: what the reference accepts, what it reports, and what the core's
typing reads otherwise, in both directions (D445)."""
TOKEN_COLUMNS: Mapping[str, str] = {
    "sample": "Sample_Id",
    "status": "SV_Status",
    "build": "NCBI_Build",
    "hugo1": "Site1_Hugo_Symbol",
    "hugo2": "Site2_Hugo_Symbol",
    "entrez1": "Site1_Entrez_Gene_Id",
    "entrez2": "Site2_Entrez_Gene_Id",
    "position": "Site1_Position",
}
SITE_GENES = {
    1: ("Site1_Hugo_Symbol", "Site1_Entrez_Gene_Id"),
    2: ("Site2_Hugo_Symbol", "Site2_Entrez_Gene_Id"),
}


def _row_classes(kind: str) -> Mapping[str, Mapping[str, str]]:
    """``gene``: the row as R1 has it (the other columns name genes); ``alone`` for a gene
    column: no other gene column names one, so the token alone decides whether the row names a
    gene."""
    if kind[:-1] not in ("hugo", "entrez"):
        return {"gene": {}}
    site = int(kind[-1])
    own = TOKEN_COLUMNS[kind]
    blank = {column: "" for pair in SITE_GENES.values() for column in pair if column != own}
    other = {
        column: ""
        for column in SITE_GENES[site]
        if column != own  # the same site's other column, so the other site names the gene
    }
    return {"gene": other, "alone": blank}


def _cells() -> None:
    for kind, tokens in TOKENS.items():
        column = TOKEN_COLUMNS[kind]
        for index, token in enumerate(tokens):
            for name, changes in _row_classes(kind).items():
                changed = {**R1, **changes, column: token}
                cell("cell", f"{kind}_{index}_{name}", data(sv([R2, changed])))
    defined = (
        b"#Patient\tSample\n#p\ts\n#STRING\tSTRING\n#1\t1\nPATIENT_ID\tSAMPLE_ID\n"
        b"P1\tS1\nP2\tS2\nP3\tS3\nP3\t0\nP3\tUnknown\n"
    )
    for token in ("0", "Unknown", " 0"):
        cell(
            "cell",
            f"sample_defined_{token.strip()}{'_padded' if token != token.strip() else ''}",
            study(
                data_clinical_sample_DOT_txt=defined,
                data_sv_DOT_txt=sv([R2, row(Sample_Id=token)]),
            ),
        )
    cell("cell", "sample_defined_valid", study(data_clinical_sample_DOT_txt=defined))
    gene_rows: tuple[tuple[str, dict[str, str]], ...] = (
        ("none", {"Site1_Hugo_Symbol": "", "Site1_Entrez_Gene_Id": ""}),
        ("all_null", {"Site1_Hugo_Symbol": "NA", "Site1_Entrez_Gene_Id": "0"}),
    )
    for name, changes in gene_rows:
        both = {**changes, "Site2_Hugo_Symbol": "Unknown", "Site2_Entrez_Gene_Id": ""}
        cell("cell", f"gene_{name}", data(sv([R2, row(**both)])))


# --- build ----------------------------------------------------------------------------------------


def _builds() -> None:
    for name, first, second in (
        ("same", "GRCh37", "GRCh37"),
        ("37_38", "GRCh37", "GRCh38"),
        ("case", "GRCh37", "grch37"),
        ("padded", "GRCh37", " GRCh37"),
        ("na_then_38", "NA", "GRCh38"),
        ("empty_then_37", "", "GRCh37"),
        ("invalid_then_valid", "37", "GRCh37"),
        ("lower_only", "grch38", "grch38"),
    ):
        body = sv([row(NCBI_Build=first), {**R2, "NCBI_Build": second}])
        cell("build", name, data(body))
    for genome in ("hg19", "hg38", "mm10"):
        meta = BASE.STUDY + f"reference_genome: {genome}\n".encode()
        cell("build", f"genome_{genome}", study(meta_study_DOT_txt=meta))
        mouse = sv([row(NCBI_Build="GRCm38"), {**R2, "NCBI_Build": "GRCm38"}])
        cell(
            "build", f"genome_{genome}_mouse", study(meta_study_DOT_txt=meta, data_sv_DOT_txt=mouse)
        )


# --- dup ------------------------------------------------------------------------------------------


def _duplicates() -> None:
    cell("dup", "identical", data(sv([R1, R1, R2])))
    cell("dup", "status_differs", data(sv([R1, row(SV_Status="GERMLINE"), R2])))
    cell("dup", "class_differs", data(sv([R1, row(Class="DELETION"), R2])))
    cell("dup", "build_differs_case", data(sv([R1, row(NCBI_Build="grch37")])))
    cell("dup", "padded_sample", data(sv([R1, row(Sample_Id="S1 "), R2])))
    cell("dup", "na_vs_empty_event", data(sv([R1, row(Event_Info="NA")])))
    cell("dup", "event_trailing_space", data(sv([R1, row(Event_Info="EML4-ALK fusion ")])))
    last = (*COLUMNS[:-2], "Class", "Event_Info")
    cell("dup", "last_trailing_space", data(sv([R1, row(Event_Info="EML4-ALK fusion ")], last)))
    subset = ("Sample_Id", "SV_Status", "Site1_Hugo_Symbol", "Site2_Hugo_Symbol")
    cell("dup", "subset_columns", data(sv([R1, row(SV_Status="GERMLINE")], subset)))
    cell("dup", "position_differs", data(sv([R1, row(Site1_Position="1")])))
    crlf = sv([R1]).replace(b"\n", b"\r\n") + sv([R1]).split(b"\n", 1)[1]
    cell("dup", "crlf_vs_lf", data(crlf))
    lone_cr = sv([R1]) + sv([R1]).split(b"\n", 1)[1].replace(b"\n", b"\r") + b"\n"
    cell("dup", "lone_cr", data(lone_cr))
    key_last = (*COLUMNS[:-2], "Class", "Event_Info")
    crlf_last = sv([R1], key_last).replace(b"\n", b"\r\n") + sv([R1], key_last).split(b"\n", 1)[1]
    cell("dup", "crlf_vs_lf_key_last", data(crlf_last))
    cr_last = (
        sv([R1], key_last) + sv([R1], key_last).split(b"\n", 1)[1].replace(b"\n", b"\r") + b"\n"
    )
    cell("dup", "lone_cr_key_last", data(cr_last))


# --- meta -----------------------------------------------------------------------------------------


def _metas() -> None:
    for name, value in (("sv", "sv"), ("padded", " structural_variants "), ("fusion", "fusion")):
        meta = META.replace("stable_id: structural_variants", f"stable_id: {value}").encode()
        cell("meta", f"stable_id_{name}", study(meta_sv_DOT_txt=meta))
    fusion = META.replace("datatype: SV", "datatype: FUSION").encode()
    cell("meta", "datatype_fusion", study(meta_sv_DOT_txt=fusion))
    for name, extra in (
        ("gene_panel", "gene_panel: IMPACT468\n"),
        ("reference_genome", "reference_genome: hg19\n"),
        ("show_maybe", ""),
    ):
        meta = META + extra
        if name == "show_maybe":
            meta = meta.replace(
                "show_profile_in_analysis_tab: true", "show_profile_in_analysis_tab: maybe"
            )
        cell("meta", name, study(meta_sv_DOT_txt=meta.encode()))
    no_show = META.replace("show_profile_in_analysis_tab: true\n", "")
    cell("meta", "no_show_profile", study(meta_sv_DOT_txt=no_show.encode()))
    second = META.replace(SV_FILE, "data_sv2.txt").encode()
    cell("meta", "second_same_id", study(meta_sv2_DOT_txt=second, data_sv2_DOT_txt=sv([R2])))
    other = META.replace(SV_FILE, "data_sv2.txt").replace(
        "stable_id: structural_variants", "stable_id: sv2"
    )
    cell(
        "meta",
        "second_other_id",
        study(meta_sv2_DOT_txt=other.encode(), data_sv2_DOT_txt=sv([R2])),
    )
    for name, target in (
        ("sample", "data_clinical_sample.txt"),
        ("patient", "data_clinical_patient.txt"),
        ("study_meta", "meta_study.txt"),
        ("own_meta", SV_META),
        ("sample_case", "DATA_CLINICAL_SAMPLE.txt"),
        ("dot_slash", f"./{SV_FILE}"),
        ("subdirectory", f"sub/{SV_FILE}"),
        ("absolute", f"/{SV_FILE}"),
        ("empty", ""),
    ):
        meta = META.replace(f"data_filename: {SV_FILE}", f"data_filename: {target}").encode()
        cell("meta", f"names_{name}", study(meta_sv_DOT_txt=meta))
    with_maf = BASE.study(meta_sv_DOT_txt=META.encode(), data_sv_DOT_txt=VALID)
    cell("meta", "with_maf", with_maf)
    names_maf = META.replace(SV_FILE, "data_mutations.txt").encode()
    cell("meta", "names_maf", BASE.study(meta_sv_DOT_txt=names_maf))
    maf_meta = BASE.META.replace("data_mutations.txt", SV_FILE).encode()
    cell(
        "meta",
        "maf_names_sv",
        BASE.study(
            meta_sv_DOT_txt=META.encode(),
            data_sv_DOT_txt=VALID,
            meta_mutations_DOT_txt=maf_meta,
            data_mutations_DOT_txt=None,
        ),
    )
    sample_meta = BASE.SAMPLE_META.replace(b"data_clinical_sample.txt", SV_FILE.encode())
    cell(
        "meta",
        "sample_names_sv",
        study(meta_clinical_sample_DOT_txt=sample_meta, data_clinical_sample_DOT_txt=None),
    )
    timeline = (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: CLINICAL\ndatatype: TIMELINE\n"
        f"data_filename: {SV_FILE}\n"
    ).encode()
    cell("meta", "timeline_names_sv", study(meta_timeline_DOT_txt=timeline))
    cell("meta", "no_sv_meta", study(meta_sv_DOT_txt=None, data_sv_DOT_txt=None))
    cell("meta", "sv_file_without_meta", study(meta_sv_DOT_txt=None))


SNIFF_LINES = ("Sample_Id", "S1", "S2", "S3", "S1", "S2", "S3")
"""A one-column file's lines: the header and six after it, one more than the reference's sniffer
reads (V:485-490)."""


def _sniff() -> None:
    """A one-column file of 1 to 7 lines with no tab or a tab on any one line: the sniff the
    reader re-expresses, line by line, against the reference."""
    for count in range(1, len(SNIFF_LINES) + 1):
        for tab in (None, *range(count)):
            lines: list[str] = list(SNIFF_LINES[:count])
            if tab is not None:
                lines[tab] += "\tx"
            where = "none" if tab is None else str(tab)
            cell("sniff", f"lines{count}_tab{where}", data(("\n".join(lines) + "\n").encode()))


# --- line roles x hostile atoms, and pairs of refusal classes (review r1 MJ-1) --------------------

ATOMS: Mapping[str, str] = {
    "bom": "\ufeff",
    "hash": "#",
    "control": "\x01",
    "nbsp_field": "\xa0",
    "trailing_tab": "\t",
}
"""The hostile atoms each line role is crossed with: a byte order mark, ``#``, a control
character, a field of no-break spaces, a trailing tab."""
HEADER = "\t".join(COLUMNS)


def _putter(atom: str, where: str) -> Callable[[str], str]:
    def put(line: str) -> str:
        return atom + line if where == "start" else line + atom

    return put


def _line_roles() -> None:
    """Each atom at the start and at the end of each line role: a top ``#`` line (before the
    header), the header line (with and without a top line before it), and a data line; and a data
    line whose every field is no-break spaces."""
    body = VALID.split(b"\n", 1)[1].decode()
    first, rest = body.split("\n", 1)
    for name, atom in ATOMS.items():
        for where in ("start", "end"):
            put = _putter(atom, where)
            top = put("#v") + "\n" + HEADER + "\n" + body
            cell("role", f"top_{name}_{where}", data(top.encode()))
            header = put(HEADER) + "\n" + body
            cell("role", f"header_{name}_{where}", data(header.encode()))
            later = "#v\n" + put(HEADER) + "\n" + body
            cell("role", f"header_after_top_{name}_{where}", data(later.encode()))
            row = HEADER + "\n" + put(first) + "\n" + rest
            cell("role", f"data_{name}_{where}", data(row.encode()))
    nbsp = "\t".join("\xa0" for _ in COLUMNS)
    cell("role", "data_nbsp_line", data((HEADER + "\n" + first + "\n" + nbsp + "\n").encode()))
    ideographic = "\t".join("\u3000\xa0" for _ in COLUMNS)
    line = HEADER + "\n" + first + "\n" + ideographic + "\n"
    cell("role", "data_ideographic_line", data(line.encode()))
    cell("role", "bom_hash_line2", data(b"#c\n\xef\xbb\xbf#d\n" + VALID))


FAULTS: Mapping[str, str] = {
    "bytes": "bytes",
    "control": "control",
    "structure": "structure",
    "cell": "cell",
}
"""The reader's refusal classes, in the pack's order; each pair is put in one file with the later
class's fault on the earlier line, and the other way round, so that the order of classes, not of
lines, is observable."""


def _fault(kind: str) -> bytes:
    """One data line of the class ``kind``'s fault."""
    if kind == "bytes":
        return ("\t".join(R2[c] for c in COLUMNS) + "\n").encode().replace(b"TMPRSS2", b"T\xff")
    if kind == "control":
        return ("\t".join({**R2, "Event_Info": "e\x01"}[c] for c in COLUMNS) + "\n").encode()
    if kind == "structure":
        return ("\t".join(R2[c] for c in COLUMNS[:-1]) + "\n").encode()
    return ("\t".join({**R2, "Sample_Id": "S9"}[c] for c in COLUMNS) + "\n").encode()


def _class_pairs() -> None:
    kinds = list(FAULTS)
    head = (HEADER + "\n").encode()
    for earlier, later in itertools.combinations(kinds, 2):
        for name, lines in (("later_first", (later, earlier)), ("earlier_first", (earlier, later))):
            body = b"".join(_fault(kind) for kind in lines)
            cell("pair", f"{earlier}_{later}_{name}", data(head + body))
    stable = META.replace("stable_id: structural_variants", "stable_id: sv").encode()
    for kind in kinds:
        cell(
            "pair",
            f"meta_{kind}",
            study(meta_sv_DOT_txt=stable, data_sv_DOT_txt=head + _fault(kind)),
        )


BOM_ROWS: Mapping[str, Mapping[str, str]] = {
    "null_sample": {"Sample_Id": "NA"},
    "undefined_sample": {"Sample_Id": "S9"},
    "status": {"SV_Status": "LIKELY"},
    "build": {"NCBI_Build": "hg19"},
    "second_build": {"NCBI_Build": "GRCh38"},
    "nonpositive": {"Site1_Entrez_Gene_Id": "-1"},
    "no_gene": dict.fromkeys(
        ("Site1_Hugo_Symbol", "Site1_Entrez_Gene_Id", "Site2_Hugo_Symbol", "Site2_Entrez_Gene_Id"),
        "",
    ),
    "padded_sample": {"Sample_Id": " S1"},
    "padded_null": {"Site1_Hugo_Symbol": " NA"},
    "padded_zero": {"Site1_Entrez_Gene_Id": " 0"},
    "not_integer": {"Site1_Entrez_Gene_Id": "1e3"},
}
"""Each row item's fault, on a file that a byte order mark leads (review r1 m1): the reference
stops at the header it reads the mark into, so every refusal there is the same outcome."""


def _bom_rows() -> None:
    for name, changes in BOM_ROWS.items():
        cell("bom", name, data(BOM + sv([R1, {**R2, **changes}])))
    lines = {
        "comment_line": b"#late\n",
        "blank_line": b"\n",
        "repeated_header": (HEADER + "\n").encode(),
        "row_width": ("\t".join(R2[c] for c in COLUMNS[:-1]) + "\n").encode(),
        "long_field": (
            "\t".join({**R2, "Event_Info": "e" * 131073}[c] for c in COLUMNS) + "\n"
        ).encode(),
    }
    for name, line in lines.items():
        cell("bom", name, data(BOM + sv([R1]) + line))


def _studies() -> None:
    other = META.replace(f"cancer_study_identifier: {SID}", "cancer_study_identifier: other")
    cell("meta", "other_study", study(meta_sv_DOT_txt=other.encode()))
    cell("meta", "one_row", data(sv([R1])))
    tabbed = b"".join(line + b"\t\n" for line in VALID.splitlines())
    cell("header", "trailing_tab_rows_too", data(tabbed))


# --- byte order marks, header-less files, meta pairs, boundaries (review r2 MJ-2) -----------------

BOM_PLACES: tuple[tuple[str, str], ...] = (
    ("start", "top"),
    ("start", "header"),
    ("line2", "header"),
    ("line2", "data"),
    ("field", "header"),
    ("field", "data"),
    ("after_hash", "top"),
)
"""Where the marks go (byte 0, the start of line 2, inside a field, after a top line's ``#``) and
the line role they land in (a top ``#`` line, the header, a data line)."""
BOM_CLASSES = ("none", "bytes", "control", "structure", "cell", "meta")


def _bom_file(count: int, place: str, role: str) -> str:
    marks = "\ufeff" * count
    body = VALID.split(b"\n", 1)[1].decode()
    first, rest = body.split("\n", 1)
    top = role == "top" or (place == "line2" and role == "header")
    header = HEADER
    if place == "start" and role == "header":
        header = marks + HEADER
    if place == "line2" and role == "header":
        header = marks + HEADER
    if place == "field" and role == "header":
        header = HEADER.replace("\tEvent_Info", "\tEvent" + marks + "_Info")
    if place == "line2" and role == "data":
        first = marks + first
    if place == "field" and role == "data":
        first = first.replace("EML4-ALK fusion", "EML4" + marks + "-ALK fusion")
    lines = ["#v"] if top else []
    if place == "start" and role == "top":
        lines = [marks + "#v"]
    if place == "after_hash" and role == "top":
        lines = ["#" + marks + "v"]
    return "\n".join([*lines, header, first, rest.rstrip("\n")]) + "\n"


def _boms() -> None:
    head_fault = {"bytes": "bytes", "control": "control", "structure": "structure", "cell": "cell"}
    stable = META.replace("stable_id: structural_variants", "stable_id: sv").encode()
    for count in (1, 2):
        for place, role in BOM_PLACES:
            text = _bom_file(count, place, role).encode()
            for fault in BOM_CLASSES:
                name = f"n{count}_{place}_{role}_{fault}"
                if fault == "none":
                    cell("boms", name, data(text))
                elif fault == "meta":
                    cell("boms", name, study(meta_sv_DOT_txt=stable, data_sv_DOT_txt=text))
                else:
                    cell("boms", name, data(text + _fault(head_fault[fault])))
    first_free = ("Event_Info", *(c for c in COLUMNS if c != "Event_Info"))
    padded = sv([R1, {**R2, "Site1_Hugo_Symbol": " NA"}], first_free)
    cell("boms", "first_free_padded", data(BOM + padded))
    cell("boms", "first_free", data(BOM + sv([R1, R2], first_free)))


HEADERLESS: Mapping[str, bytes] = {
    "empty": b"",
    "lf": b"\n",
    "crlf": b"\r\n",
    "blank_lines": b"\n\n\n",
    "bom": BOM,
    "bom_lf": BOM + b"\n",
    "bom_crlf": BOM + b"\r\n",
    "comment": b"#c\n",
    "bom_comment": BOM + b"#c\n",
    "top_no_header": b"#v\n#w\n",
    "spaces_line": b"   \n",
    "tab_on_sixth": b"\n" + b"a\n" * 4 + b"x\ty\n",
    "tab_on_seventh": b"\n" + b"a\n" * 5 + b"x\ty\n",
}
"""Files with no line of column names (review r2 MJ-2 (c))."""


def _headerless() -> None:
    for name, content in HEADERLESS.items():
        cell("headerless", name, data(content))


def _meta_pairs() -> None:
    other = f"cancer_study_identifier: {SID}"
    for name, change in (
        ("duplicate_stable_id", lambda m: m),
        ("stable_id_sv", lambda m: m.replace("stable_id: structural_variants", "stable_id: sv")),
        ("datatype", lambda m: m.replace("datatype: SV", "datatype: FUSION")),
        ("missing_key", lambda m: m.replace("profile_name: SV\n", "")),
        (
            "stable_id_mutations",
            lambda m: m.replace("stable_id: structural_variants", "stable_id: mutations"),
        ),
    ):
        meta = change(META.replace(other, "cancer_study_identifier: other"))
        over: dict[str, Content | None] = {"meta_sv_DOT_txt": meta.encode()}
        if name == "duplicate_stable_id":
            over["meta_sv2_DOT_txt"] = META.replace(SV_FILE, "data_sv2.txt").encode()
            over["data_sv2_DOT_txt"] = sv([R2])
        cell("metapair", f"other_study_{name}", study(**over))
    case = META.replace(other, f"cancer_study_identifier: {SID.upper()}")
    cell("metapair", "study_case", study(meta_sv_DOT_txt=case.encode()))


def _boundaries() -> None:
    for size in (4095, 4096):
        cell("bound", f"name_{size}", data(sv([{**R1, "X" * size: "1"}], (*COLUMNS, "X" * size))))
    repeated = HEADER + "\textra"
    body = VALID.decode().split("\n", 1)[1]
    wide = "\t".join(R1[c] for c in COLUMNS) + "\tx"
    text = HEADER + "\tExtra\n" + wide + "\n" + repeated + "x\n"
    cell("bound", "repeated_header_tab_field", data(text.encode()))
    cell(
        "bound",
        "repeated_header_tab_field_short",
        data((HEADER + "\n" + body + HEADER + "\tx\n").encode()),
    )
    both = row(Site1_Entrez_Gene_Id="-1", Site2_Entrez_Gene_Id="-2")
    cell("bound", "both_sites_nonpositive", data(sv([R2, both])))


# --- a leading mark renames the first column; a mark-led ``#`` line (review r3 MJ-3) --------------


def _first(column: str, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    return (column, *(c for c in COLUMNS if c != column), *extra)


FIRST_FAULTS: Mapping[str, tuple[tuple[str, list[Mapping[str, str]]], ...]] = {
    "Sample_Id": (("undefined", [R1, {**R2, "Sample_Id": "S9"}]),),
    "SV_Status": (
        ("bad", [R1, {**R2, "SV_Status": "LIKELY"}]),
        ("padded", [R1, {**R2, "SV_Status": " NA"}]),
    ),
    "NCBI_Build": (
        ("bad", [R1, {**R2, "NCBI_Build": "hg19"}]),
        ("second", [R1, {**R2, "NCBI_Build": "GRCh38"}]),
        ("padded", [R1, {**R2, "NCBI_Build": " NA"}]),
    ),
    "Site1_Hugo_Symbol": (
        (
            "only_gene",
            [
                R1,
                {
                    **R2,
                    "Site1_Entrez_Gene_Id": "NA",
                    "Site2_Hugo_Symbol": "NA",
                    "Site2_Entrez_Gene_Id": "NA",
                },
            ],
        ),
        ("dup_key", [R1, {**R1, "Site1_Hugo_Symbol": "OTHER"}]),
        ("padded", [R1, {**R2, "Site1_Hugo_Symbol": " NA"}]),
    ),
    "Site2_Hugo_Symbol": (
        (
            "only_gene",
            [
                R1,
                {
                    **R2,
                    "Site2_Entrez_Gene_Id": "NA",
                    "Site1_Hugo_Symbol": "NA",
                    "Site1_Entrez_Gene_Id": "NA",
                },
            ],
        ),
    ),
    "Site1_Entrez_Gene_Id": (
        ("nonpositive", [R1, {**R2, "Site1_Entrez_Gene_Id": "-1"}]),
        ("float", [R1, {**R2, "Site1_Entrez_Gene_Id": "1e3"}]),
        ("padded_zero", [R1, {**R2, "Site1_Entrez_Gene_Id": " 0"}]),
        (
            "only_gene",
            [
                R1,
                {
                    **R2,
                    "Site1_Hugo_Symbol": "NA",
                    "Site2_Hugo_Symbol": "NA",
                    "Site2_Entrez_Gene_Id": "NA",
                },
            ],
        ),
    ),
    "Site2_Entrez_Gene_Id": (("nonpositive", [R1, {**R2, "Site2_Entrez_Gene_Id": "-1"}]),),
    "Event_Info": (("dup_key", [R1, {**R1, "Event_Info": "other"}]),),
    "Site1_Position": (("dup_key", [R1, {**R1, "Site1_Position": "1"}]),),
}
"""Each column's own faults, the column first under one leading mark (which the reference reads into
its name, so it never checks it by that name)."""


def _marked_first() -> None:
    for column in COLUMNS:
        first = _first(column)
        cell("first", f"{column}_valid", data(BOM + sv([R1, R2], first)))
        cell("first", f"{column}_repeated", data(BOM + sv([R1, R2], _first(column, (column,)))))
        for name, rows in FIRST_FAULTS.get(column, ()):
            cell("first", f"{column}_{name}", data(BOM + sv(rows, first)))
    meta = (META + "namespaces: zz\n").encode()
    rows: list[Mapping[str, str]] = [{**R1, "zz.a": "1"}, {**R2, "zz.a": "2"}]
    cell(
        "first",
        "namespace_column",
        study(meta_sv_DOT_txt=meta, data_sv_DOT_txt=BOM + sv(rows, ("zz.a", *COLUMNS))),
    )
    cell("first", "one_column_no_tab", data(BOM + b"Sample_Id\nS1\nS2\n"))
    empty = ("Sample_Id", *COLUMNS[1:], "")
    cell("first", "sample_first_empty_name", data(BOM + sv([{**R1, "": "x"}], empty)))
    long = ("Sample_Id", *COLUMNS[1:], "X" * 4097)
    cell("first", "sample_first_long_name", data(BOM + sv([{**R1, "X" * 4097: "x"}], long)))


MARKED_HASH: Mapping[str, bytes] = {
    "no_tab_then_header": b"#c\n" + VALID,
    "tab_then_header": b"#c\tx\n" + VALID,
    "tab_alone": b"#c\tx\n",
    "two_lines_tab_second": b"#c\n#d\tx\n",
    "tab_within_five": b"#c\n#d\n#e\n#f\tx\n",
    "tab_crlf": b"#c\tx\r\n",
    "no_tab_alone": b"#c\n",
}
HEADER_FAULTS: Mapping[str, bytes] = {
    "repeated_name": sv([R1], (*COLUMNS, "Class")),
    "no_sample": sv([R1], COLUMNS[1:]),
    "empty_name": sv([{**R1, "": "x"}], (*COLUMNS, "")),
    "repeats_build": sv([R1], (*COLUMNS, "NCBI_Build")),
}


def _marked_hash() -> None:
    for name, content in MARKED_HASH.items():
        cell("markhash", name, data(BOM + content))
    for top in (b"#c\n", b"#c\tx\n"):
        for name, header in HEADER_FAULTS.items():
            tab = "tab" if b"\t" in top else "no_tab"
            cell("markhash", f"{tab}_then_{name}", data(BOM + top + header))
    meta = (META + "namespaces: zz\n").encode()
    rows = sv([{**R1, "zz.a": "1"}], (*COLUMNS, "zz.a"))
    cell(
        "markhash",
        "tab_then_namespace",
        study(meta_sv_DOT_txt=meta, data_sv_DOT_txt=BOM + b"#c\tx\n" + rows),
    )
    bad = sv([{**R1, "zz.a": "1"}, {**R2, "zz.a": "2", "Sample_Id": "S9"}], (*COLUMNS, "zz.a"))
    cell(
        "markhash",
        "no_tab_then_namespace_bad_row",
        study(meta_sv_DOT_txt=meta, data_sv_DOT_txt=BOM + b"#c\n" + bad),
    )


def _round_three() -> None:
    cell("sniff", "tab_on_unterminated_last_line", data(b"Sample_Id\nS1\nS2\tx"))
    for size in (4096, 4097):
        name = "\u00e9" * size
        cell("bound", f"multibyte_name_{size}", data(sv([{**R1, name: "1"}], (*COLUMNS, name))))
    nope = META.replace("stable_id: structural_variants", "stable_id: sv").replace(
        f"data_filename: {SV_FILE}", "data_filename: nope.txt"
    )
    cell("metapair", "stable_id_sv_no_file", study(meta_sv_DOT_txt=nope.encode()))
    generic = (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: GENERIC_ASSAY\n"
        "generic_assay_type: g\ndatatype: LIMIT-VALUE\nstable_id: structural_variants\n"
        "profile_name: p\nprofile_description: p\ndata_filename: data_zz.txt\n"
        "show_profile_in_analysis_tab: true\n"
    ).encode()
    cell(
        "metapair",
        "generic_repeats_stable_id",
        study(meta_zz_DOT_txt=generic, data_zz_DOT_txt=b"x\n"),
    )


def _twins() -> None:
    """Every cell whose SV file one byte order mark leads, again without the mark (review r3
    MJ-3): the twin's recorded outcome is what the reference reports when the mark does not
    rename its first column, which SE1 and (S33) are judged against."""
    for key, files in list(CELLS.items()):
        content = files.get(SV_FILE)
        if (
            isinstance(content, bytes)
            and content.startswith(BOM)
            and not content.startswith(2 * BOM)
        ):
            CELLS[f"twin:{key}"] = {**files, SV_FILE: content[len(BOM) :]}


GENERATORS: tuple[Callable[[], None], ...] = (
    _bytes,
    _header,
    _namespaces,
    _cells,
    _builds,
    _duplicates,
    _metas,
    _sniff,
    _line_roles,
    _class_pairs,
    _bom_rows,
    _studies,
    _boms,
    _headerless,
    _meta_pairs,
    _boundaries,
    _marked_first,
    _marked_hash,
    _round_three,
)
for _generator in GENERATORS:
    _generator()
_twins()
