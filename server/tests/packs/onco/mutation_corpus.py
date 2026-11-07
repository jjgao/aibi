"""The MAF and case-list corpus of M4.2b-1b (D444): cells made by generators, never listed one by
one, each a small study that differs from a valid one by one change.

This is not a test: pytest does not collect it, and it imports nothing (of the pack or of aibi), so
that the scratch harness that runs each cell on the pinned reference (cbioportal-core 47890bb,
offline, not relaxed, ``strict_maf_checks`` off; D409) can load it as it is, by its path, and so
that ``test_mutation_corpus.py`` loads the same cells. The reference's outcome of every cell is
recorded in ``mutation_reference.json`` by error identity (no message of the reference is kept):
whether it crashed and the identity of each error it reported, in order.

A cell is a mapping of a path below the study's directory to its content: ``bytes`` for a file,
``DIRECTORY`` for an empty directory, ``("symlink", target)`` for a symlink. ``CELLS`` maps each
cell's id to it; an id is ``<generator>:<case>``. The generators:

- ``byte``: the MAF's bytes (byte order marks, line ends, blank and comment lines, the repeated
  header, row widths, an empty file, control characters, bytes that are not UTF-8, long fields);
- ``header``: the MAF's columns (each column dropped in turn, the required pair, a repeated, a
  padded or an empty name, a name that repeats another but for case, the driver pairs, the
  namespaces and their columns);
- ``row``: a token in a column of a class (``count``, ``position``, ``position-alone``, ``entrez``,
  ``barcode``, ``amino``, ``driver``), on a row of a class (``valid``: validated; ``silent``: in
  the default skip list; ``unknown0``: ``Unknown`` with Entrez ``0``; ``filtered``: in a meta's
  ``variant_classification_filter`` that replaces the default list; ``silent-filtered``:
  ``Silent`` under that filter, which the reference then validates);
- ``build``: an ``NCBI_Build`` token under each ``reference_genome`` (none, ``hg19``, ``hg38``,
  ``mm10``);
- ``normal``: a ``Matched_Norm_Sample_Barcode`` against a meta's ``normal_samples_list``;
- ``meta``: the mutation meta's values, a second mutation meta, another meta naming the MAF, an
  uncalled-mutation meta;
- ``list``: a case list's keys, values, ids, bytes and file names, and the ``case_lists``
  directory itself.
"""

from collections.abc import Callable, Mapping

DIRECTORY = "directory"
SID = "demo_study"

STUDY = (
    f"type_of_cancer: brca\ncancer_study_identifier: {SID}\nname: Demo study\n"
    "description: A synthetic study.\nadd_global_case_list: true\n"
).encode()
SAMPLE_META = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: CLINICAL\n"
    "datatype: SAMPLE_ATTRIBUTES\ndata_filename: data_clinical_sample.txt\n"
).encode()
PATIENT_META = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: CLINICAL\n"
    "datatype: PATIENT_ATTRIBUTES\ndata_filename: data_clinical_patient.txt\n"
).encode()
SAMPLES = (
    b"#Patient\tSample\n#p\ts\n#STRING\tSTRING\n#1\t1\nPATIENT_ID\tSAMPLE_ID\n"
    b"P1\tS1\nP2\tS2\nP3\tS3\n"
)
PATIENTS = b"#Patient\tSex\n#p\ts\n#STRING\tSTRING\n#1\t1\nPATIENT_ID\tSEX\nP1\tF\nP2\tM\nP3\tF\n"
META = (
    f"cancer_study_identifier: {SID}\ngenetic_alteration_type: MUTATION_EXTENDED\ndatatype: MAF\n"
    "stable_id: mutations\nshow_profile_in_analysis_tab: true\nprofile_name: Mutations\n"
    "profile_description: Mutations\ndata_filename: data_mutations.txt\n"
)
COLUMNS = (
    "Hugo_Symbol",
    "Entrez_Gene_Id",
    "Center",
    "NCBI_Build",
    "Chromosome",
    "Start_Position",
    "End_Position",
    "Strand",
    "Variant_Classification",
    "Variant_Type",
    "Reference_Allele",
    "Tumor_Seq_Allele1",
    "Tumor_Seq_Allele2",
    "Tumor_Sample_Barcode",
    "Matched_Norm_Sample_Barcode",
    "Mutation_Status",
    "HGVSc",
    "HGVSp",
    "HGVSp_Short",
    "t_ref_count",
    "t_alt_count",
    "n_ref_count",
    "n_alt_count",
    "SWISSPROT",
)
R1: Mapping[str, str] = dict(
    zip(
        COLUMNS,
        (
            "TP53",
            "7157",
            "MSKCC",
            "GRCh37",
            "17",
            "7578406",
            "7578406",
            "+",
            "Missense_Mutation",
            "SNP",
            "C",
            "C",
            "T",
            "S1",
            "N1",
            "Somatic",
            "c.524G>A",
            "p.Arg175His",
            "p.R175H",
            "50",
            "12",
            "50",
            "0",
            "P53_HUMAN",
        ),
        strict=True,
    )
)
R2: Mapping[str, str] = {
    **R1,
    "Hugo_Symbol": "KRAS",
    "Entrez_Gene_Id": "3845",
    "Chromosome": "12",
    "Start_Position": "25398284",
    "End_Position": "25398284",
    "Tumor_Sample_Barcode": "S2",
    "HGVSp_Short": "p.G12D",
    "SWISSPROT": "RASK_HUMAN",
}


def maf(
    rows: list[Mapping[str, str]],
    columns: tuple[str, ...] = COLUMNS,
    top: bytes = b"#version 2.4\n",
) -> bytes:
    """A MAF of ``rows`` under ``columns`` (a cell a row lacks is empty), after ``top``."""
    lines = ["\t".join(columns), *("\t".join(row.get(c, "") for c in columns) for row in rows)]
    return top + ("\n".join(lines) + "\n").encode()


def case_list(
    suffix: str,
    ids: list[str],
    category: str | None = "all_cases_with_mutation_data",
    extra: str = "",
) -> bytes:
    """A case list of the study: ``<study>_<suffix>``, its ids joined by tabs."""
    head = f"cancer_study_identifier: {SID}\nstable_id: {SID}_{suffix}\ncase_list_name: n\n"
    head += "case_list_description: d\n"
    if category is not None:
        head += f"case_list_category: {category}\n"
    return (head + extra + "case_list_ids: " + "\t".join(ids) + "\n").encode()


Content = bytes | str | tuple[str, str]
Cell = dict[str, Content]

SEQUENCED = "case_lists/cases_sequenced.txt"


def study(**over: Content | None) -> Cell:
    """The valid study, with ``over`` (a path, ``/`` written ``__`` and ``.`` written ``_DOT_``,
    to its content, or ``None`` to remove it) applied."""
    files: Cell = {
        "meta_study.txt": STUDY,
        "meta_clinical_sample.txt": SAMPLE_META,
        "meta_clinical_patient.txt": PATIENT_META,
        "data_clinical_sample.txt": SAMPLES,
        "data_clinical_patient.txt": PATIENTS,
        "meta_mutations.txt": META.encode(),
        "data_mutations.txt": maf([R1, R2]),
        SEQUENCED: case_list("sequenced", ["S1", "S2", "S3"]),
    }
    for key, value in over.items():
        path = key.replace("__", "/").replace("_DOT_", ".")
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value
    return files


def data(content: bytes) -> Cell:
    return study(data_mutations_DOT_txt=content)


def row(**changes: str) -> dict[str, str]:
    return {**R1, **changes}


CELLS: dict[str, Cell] = {}


def cell(generator: str, case: str, files: Cell) -> None:
    key = f"{generator}:{case}"
    assert key not in CELLS, key
    CELLS[key] = files


# --- byte -----------------------------------------------------------------------------------------

BOM = b"\xef\xbb\xbf"
VALID = maf([R1, R2])


def _bytes() -> None:
    cell("byte", "valid", study())
    cell("byte", "bom_with_top", data(BOM + VALID))
    cell("byte", "bom_no_top", data(BOM + maf([R1, R2], top=b"")))
    cell("byte", "bom_before_header", data(maf([R1, R2], top=b"#a\n" + BOM)))
    cell("byte", "bom_at_row", data(maf([R2, row(Hugo_Symbol="﻿KRAS")])))
    cell("byte", "crlf", data(VALID.replace(b"\n", b"\r\n")))
    cell("byte", "cr", data(VALID.replace(b"\n", b"\r")))
    cell("byte", "mixed", data(VALID.replace(b"\n", b"\r\n", 1)))
    cell("byte", "no_top", data(maf([R1, R2], top=b"")))
    cell("byte", "many_top", data(maf([R1, R2], top=b"#a\n#b\n#c\n#d\n#e\n")))
    cell("byte", "space_hash_top", data(maf([R1, R2], top=b" #x\n")))
    cell("byte", "trailing_blank", data(VALID + b"\n"))
    cell("byte", "blank_tabs", data(maf([R1]) + b"\t\t\n" + maf([R2], top=b"").split(b"\n", 1)[1]))
    cell("byte", "hash_after_header", data(VALID + b"#late\n"))
    cell("byte", "hash_first_field", data(maf([R2, row(Hugo_Symbol="#TP53")])))
    cell("byte", "space_hash_line", data(VALID + b" #x\t" + b"\t" * (len(COLUMNS) - 2) + b"\n"))
    cell("byte", "repeated_header", data(maf([R1, dict(zip(COLUMNS, COLUMNS, strict=True)), R2])))
    extra = ("\t".join(R2[c] for c in COLUMNS) + "\textra\n").encode()
    cell("byte", "extra_field", data(maf([R1]) + extra))
    short = ("\t".join(R2[c] for c in COLUMNS[:-1]) + "\n").encode()
    cell("byte", "short_row", data(maf([R1]) + short))
    cell("byte", "header_only", data(maf([])))
    cell("byte", "empty", data(b""))
    cell("byte", "top_only", data(b"#a\n#b\n"))
    for name, character in (
        ("nul", "\x00"),
        ("u0001", "\x01"),
        ("vt", "\x0b"),
        ("del", "\x7f"),
        ("esc", "\x1b"),
    ):
        cell("byte", f"control_{name}", data(maf([R2, row(HGVSc="c.1" + character)])))
        silent = row(HGVSc="c.1" + character, Variant_Classification="Silent")
        cell("byte", f"control_{name}_silent", data(maf([R1, silent])))
    bad = maf([R2, row(HGVSc="c.é")]).replace("c.é".encode(), b"c.\xff")
    cell("byte", "not_utf8", data(bad))
    cell("byte", "field_131073", data(maf([R2, row(HGVSc="c" * 131073)])))
    cell("byte", "field_131072", data(maf([R2, row(HGVSc="c" * 131072)])))
    cell("byte", "quote_cell", data(maf([R2, row(HGVSc='"c.1A>G"')])))
    cell("byte", "missing_file", study(data_mutations_DOT_txt=None))
    cell("byte", "maf_is_directory", study(data_mutations_DOT_txt=DIRECTORY))
    cell(
        "byte",
        "maf_symlink",
        study(data_mutations_DOT_txt=("symlink", "real_maf.txt"), real_maf_DOT_txt=VALID),
    )
    cell("byte", "duplicate_key", data(maf([R1, R1])))
    cell("byte", "no_tab", data(b"#v\nHugo_Symbol\nTP53\n"))
    cell("byte", "bare_hash_line", data(VALID + b"#\n"))
    first_empty = ("", *COLUMNS)
    cell("byte", "empty_first_name", data(maf([{**R1, "": "x"}], first_empty)))
    maf_meta = META.replace("data_mutations.txt", "data_\ufffe.txt").encode()
    cell(
        "byte",
        "maf_name_noncharacter",
        study(
            meta_mutations_DOT_txt=maf_meta,
            data_mutations_DOT_txt=None,
            **{"data_\ufffe_DOT_txt": VALID},
        ),
    )
    sample_meta = SAMPLE_META.replace(b"data_clinical_sample.txt", "data_\ufffe.txt".encode())
    cell(
        "byte",
        "sample_name_noncharacter",
        study(
            meta_clinical_sample_DOT_txt=sample_meta,
            data_clinical_sample_DOT_txt=None,
            **{"data_\ufffe_DOT_txt": SAMPLES},
        ),
    )
    cell("byte", "blank_first_line", data(b"\n" + maf([R1, R2], top=b"")))
    cell("byte", "bom_alone_first_line", data(BOM + b"\n" + maf([R1, R2], top=b"")))
    header_line = "\t".join(COLUMNS).encode()
    cell("byte", "header_no_line_end", data(b"#v\n" + header_line))
    cell("byte", "header_only_no_top_no_line_end", data(header_line))
    tab_hash = {**R1, "Hugo_Symbol": "", "Entrez_Gene_Id": "#5", "Variant_Classification": "Silent"}
    cell("byte", "tab_hash_silent", data(maf([R2, tab_hash])))
    cell("byte", "space_hash_hugo", data(maf([R1, {**R2, "Hugo_Symbol": " #KRAS"}])))
    cell("byte", "duplicate_key_padded", data(maf([R2, row(HGVSp_Short="p.R175H ")])))
    cell("byte", "duplicate_key_silent", data(maf([row(Variant_Classification="Silent")] * 2)))


# --- header ---------------------------------------------------------------------------------------


def _header() -> None:
    for column in COLUMNS:
        kept = tuple(c for c in COLUMNS if c != column)
        cell("header", f"without_{column}", data(maf([R1, R2], columns=kept)))
    amino = (*(c for c in COLUMNS if c != "HGVSp_Short"), "Amino_Acid_Change")
    cell("header", "amino_acid_change_only", data(maf([row(Amino_Acid_Change="R175H")], amino)))
    minimal = ("Hugo_Symbol", "Tumor_Sample_Barcode", "Variant_Classification", "HGVSp_Short")
    cell("header", "minimal", data(maf([R1, R2], columns=minimal)))
    cell("header", "repeated_name", data(maf([R1], columns=(*COLUMNS, "Hugo_Symbol"))))
    padded = (" Hugo_Symbol", *COLUMNS[1:])
    cell("header", "padded_required", data(maf([{**R1, " Hugo_Symbol": "TP53"}], padded)))
    cell("header", "padded_other", data(maf([{**R1, "Center ": "x"}], (*COLUMNS, "Center "))))
    cell("header", "empty_name", data(maf([{**R1, "": "x"}], (*COLUMNS, ""))))
    cell(
        "header", "case_repeat", data(maf([{**R1, "hugo_symbol": "x"}], (*COLUMNS, "hugo_symbol")))
    )
    cell("header", "custom", data(maf([{**R1, "My_Column": "x"}], (*COLUMNS, "My_Column"))))
    for columns in (
        ("cbp_driver",),
        ("cbp_driver_annotation",),
        ("cbp_driver_tiers",),
        ("cbp_driver_tiers_annotation",),
        ("cbp_driver", "cbp_driver_annotation"),
        ("cbp_driver_tiers", "cbp_driver_tiers_annotation"),
    ):
        name = "+".join(columns)
        cell("header", f"driver_{name}", data(maf([R1, R2], columns=(*COLUMNS, *columns))))
    meta = (META + "namespaces: zygosity\n").encode()
    with_ns = maf([{**R1, "zygosity.status": "het"}], (*COLUMNS, "zygosity.status"))
    cell(
        "header",
        "namespace_with_column",
        study(meta_mutations_DOT_txt=meta, data_mutations_DOT_txt=with_ns),
    )
    upper = maf([{**R1, "Zygosity.Status": "het"}], (*COLUMNS, "Zygosity.Status"))
    cell(
        "header",
        "namespace_column_case",
        study(meta_mutations_DOT_txt=meta, data_mutations_DOT_txt=upper),
    )
    cell("header", "namespace_without_column", study(meta_mutations_DOT_txt=meta))
    for name, value in (("upper", "ZYGOSITY"), ("padded", " zygosity"), ("second", "x, zygosity")):
        spaced = (META + f"namespaces: {value}\n").encode()
        both = maf(
            [{**R1, "zygosity.status": "het", "x.y": "1"}], (*COLUMNS, "zygosity.status", "x.y")
        )
        cell(
            "header",
            f"namespace_{name}",
            study(meta_mutations_DOT_txt=spaced, data_mutations_DOT_txt=both),
        )
    ascn_one = maf([{**R1, "ASCN.ASCN_METHOD": "x"}], (*COLUMNS, "ASCN.ASCN_METHOD"))
    cell(
        "header",
        "ascn_partial",
        study(
            meta_mutations_DOT_txt=(META + "namespaces: ascn\n").encode(),
            data_mutations_DOT_txt=ascn_one,
        ),
    )
    ascn = (META + "namespaces: ascn\n").encode()
    cell("header", "ascn_without_columns", study(meta_mutations_DOT_txt=ascn))
    columns = (
        *COLUMNS,
        "ASCN.ASCN_METHOD",
        "ASCN.ASCN_INTEGER_COPY_NUMBER",
        "ASCN.TOTAL_COPY_NUMBER",
        "ASCN.MINOR_COPY_NUMBER",
        "ASCN.CCF_EXPECTED_COPIES",
        "ASCN.CCF_EXPECTED_COPIES_UPPER",
        "ASCN.CLONAL",
        "ASCN.EXPECTED_ALT_COPIES",
    )
    full = maf([{**R1, **dict.fromkeys(columns[len(COLUMNS) :], "1")}], columns)
    cell(
        "header",
        "ascn_with_columns",
        study(meta_mutations_DOT_txt=ascn, data_mutations_DOT_txt=full),
    )


# --- row ------------------------------------------------------------------------------------------

TOKENS: Mapping[str, tuple[str, ...]] = {
    "count": ("", ".", "5", "0", "-3", " 5", "+5", "1_0", "\u0665", "1.5", "1e3", "NA", "abc"),
    "position": (
        "",
        "NA",
        "0",
        "5",
        " 5",
        "-5",
        "+5",
        "1.5",
        "1e3",
        "inf",
        "abc",
        "\u00b2",
        "\u0665",
        "1_0",
        " NA",
    ),
    "entrez": (
        "",
        "NA",
        "0",
        "-0",
        "7157",
        " 7157",
        "+7157",
        "-5",
        "abc",
        "7157.0",
        "1e3",
        "1_0",
        "\u0665",
        " 0",
        "0 ",
        " NA",
        "0\u00a0",
        "\u20280",
        "\u3000NA",
        "nan",
        "inf",
        "1e400",
        "+7157.0",
        "NA ",
        "9007199254740991.0",
        "9007199254740992.0",
        "1e3\u00a0",
        "\u0661",
        "\uff17\uff11\uff15\uff17",
        "7157\u00a0",
    ),
    "barcode": ("S1", " S1", "S1 ", "S9", "", "s1", "N1", "S1\u00a0"),
    "amino": (
        "p.R175H",
        "",
        "NULL",
        "NA",
        "p.R175Hp.R176H",
        "p.R1;p.R2",
        "p.R1,R2",
        " p.R1",
        "p" * 255,
        "p" * 256,
        "Xp.Y",
        "p.R1;R2",
    ),
    "hugo": (
        "TP53",
        "",
        " ",
        "NA",
        "Unknown",
        "7SK",
        " NA",
        "Unknown ",
        "NA\u00a0",
        "\u2028Unknown",
        "\u3000NA",
    ),
    "classification": ("", " ", "Foo", "missense_mutation", "nonsynonymous_SNV"),
    "driver": ("Putative_Driver", "Putative_Passenger", "NA", "Unknown", "", "Driver", " NA"),
}
"""Each column class's tokens: what the reference accepts, what it reports, and what the core's
typing reads otherwise, in both directions (D444, review r2 M-A)."""
TOKEN_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "count": ("t_alt_count",),
    "position": ("Start_Position",),
    "entrez": ("Entrez_Gene_Id",),
    "barcode": ("Tumor_Sample_Barcode",),
    "amino": ("HGVSp_Short",),
    "hugo": ("Hugo_Symbol",),
    "classification": ("Variant_Classification",),
    "driver": ("cbp_driver",),
}
ROW_CLASSES: Mapping[str, Mapping[str, str]] = {
    "valid": {},
    "silent": {"Variant_Classification": "Silent"},
    "unknown0": {"Hugo_Symbol": "Unknown", "Entrez_Gene_Id": "0", "Variant_Classification": "IGR"},
    "filtered": {"Variant_Classification": "Missense_Mutation"},
    "silent-filtered": {"Variant_Classification": "Silent"},
}
FILTER = (META + "variant_classification_filter: Missense_Mutation, Nonsense_Mutation\n").encode()


def _rows() -> None:
    for kind, tokens in TOKENS.items():
        (column,) = TOKEN_COLUMNS[kind]
        for index, token in enumerate(tokens):
            for name, changes in ROW_CLASSES.items():
                if kind in ("hugo", "classification") and name != "valid":
                    continue
                changed = {**R1, **changes, column: token}
                columns = COLUMNS
                if kind == "driver":
                    columns = (*COLUMNS, "cbp_driver", "cbp_driver_annotation")
                over: dict[str, Content | None] = {
                    "data_mutations_DOT_txt": maf([R2, changed], columns)
                }
                if name in ("filtered", "silent-filtered"):
                    over["meta_mutations_DOT_txt"] = FILTER
                cell("row", f"{kind}_{index}_{name}", study(**over))
    for index, token in enumerate(TOKENS["position"]):
        alone = tuple(c for c in COLUMNS if c != "End_Position")
        changed = {**R1, "Start_Position": token}
        cell("row", f"position-alone_{index}_valid", data(maf([R2, changed], alone)))
    for index, (hugo, entrez) in enumerate(
        (
            ("Unknown", ""),
            ("", ""),
            ("NA", "NA"),
            ("Unknown", "NA"),
            ("", "0"),
            ("NA", "7157"),
            ("Unknown ", "0"),
            (" NA", ""),
            (" NA", " 0 "),
        )
    ):
        changed = {**R1, "Hugo_Symbol": hugo, "Entrez_Gene_Id": entrez}
        cell("row", f"gene_{index}_valid", data(maf([R2, changed])))
    no_entrez = tuple(c for c in COLUMNS if c != "Entrez_Gene_Id")
    for barcode in ("S1", "S9"):
        unknown = {**R1, "Hugo_Symbol": "Unknown", "Tumor_Sample_Barcode": barcode}
        cell("row", f"unknown_no_entrez_{barcode}", data(maf([R2, unknown], no_entrez)))
    for name, value in (("tiers_long", "t" * 51), ("tiers_50", "t" * 50)):
        columns = (*COLUMNS, "cbp_driver_tiers", "cbp_driver_tiers_annotation")
        changed = {**R1, "cbp_driver_tiers": value}
        cell("row", f"driver_{name}", data(maf([R2, changed], columns)))
    columns = (*COLUMNS, "cbp_driver_tiers", "cbp_driver_tiers_annotation")
    lone = {**R1, "cbp_driver_tiers_annotation": "why"}
    cell("row", "driver_tier_annotation_alone", data(maf([R2, lone], columns)))
    columns = (*COLUMNS, "cbp_driver", "cbp_driver_annotation")
    for name, value in (("annotation_81", "a" * 81), ("annotation_80", "a" * 80)):
        changed = {**R1, "cbp_driver_annotation": value}
        cell("row", f"driver_{name}", data(maf([R2, changed], columns)))
    statuses = ("LOH", "None", "wildtype", "Germline", "weird", "")
    for index, status in enumerate(statuses):
        cell(
            "row",
            f"status_{index}",
            data(maf([R2, row(Mutation_Status=status, Tumor_Sample_Barcode="S2")])),
        )
    columns = (*COLUMNS, "cbp_driver_tiers", "cbp_driver_tiers_annotation")
    padded_tier = {**R1, "cbp_driver_tiers": "t" * 50 + " "}
    cell("row", "driver_tiers_50_padded", data(maf([R2, padded_tier], columns)))
    padded_class = row(Variant_Classification="Silent ", t_alt_count="abc")
    cell("row", "silent_padded", data(maf([R2, padded_class])))
    nonsense_bad = row(Variant_Classification="Nonsense_Mutation", t_alt_count="abc")
    cell(
        "row",
        "filter_second_item",
        study(meta_mutations_DOT_txt=FILTER, data_mutations_DOT_txt=maf([R2, nonsense_bad])),
    )
    padded_filter = (META + "variant_classification_filter:  Silent ,Missense_Mutation\n").encode()
    silent_bad_count = row(Variant_Classification="Silent", t_alt_count="abc")
    cell(
        "row",
        "filter_padded",
        study(
            meta_mutations_DOT_txt=padded_filter, data_mutations_DOT_txt=maf([R2, silent_bad_count])
        ),
    )
    cell(
        "row",
        "silent_lower",
        data(maf([R2, row(Variant_Classification="silent", t_alt_count="abc")])),
    )
    empty_filter = (META + "variant_classification_filter: \n").encode()
    silent_bad = row(Variant_Classification="Silent", t_alt_count="abc")
    cell(
        "row",
        "empty_filter_silent",
        study(meta_mutations_DOT_txt=empty_filter, data_mutations_DOT_txt=maf([R2, silent_bad])),
    )


# --- build and normal -----------------------------------------------------------------------------

BUILDS = ("GRCh37", "37", "38", "GRCh38", "hg19", "", "GRCh37-lite", "grch37", " 37", "GRCm38")
GENOMES = (None, "hg19", "hg38", "mm10")


def _builds() -> None:
    for genome in GENOMES:
        meta_study = STUDY if genome is None else STUDY + f"reference_genome: {genome}\n".encode()
        for index, token in enumerate(BUILDS):
            body = maf([{**R2, "NCBI_Build": ""}, row(NCBI_Build=token)])
            cell(
                "build",
                f"{genome}_{index}",
                study(meta_study_DOT_txt=meta_study, data_mutations_DOT_txt=body),
            )
    silent = maf([R2, row(NCBI_Build="GRCh38", Variant_Classification="Silent")])
    cell("build", "silent_GRCh38", data(silent))
    for name, listed in (
        ("listed", "N1, N3"),
        ("listed_second", "N3, N1"),
        ("listed_padded", " N1 ,N3"),
        ("not_listed", "N2,N3"),
        ("empty", ""),
    ):
        meta = (META + f"normal_samples_list: {listed}\n").encode()
        cell("normal", name, study(meta_mutations_DOT_txt=meta))
    meta = (META + "normal_samples_list: N2\n").encode()
    for name, value in (("blank", ""), ("padded", " N2"), ("silent_unlisted", "N9")):
        changes = {"Matched_Norm_Sample_Barcode": value}
        if name == "silent_unlisted":
            changes["Variant_Classification"] = "Silent"
        cell(
            "normal",
            name,
            study(meta_mutations_DOT_txt=meta, data_mutations_DOT_txt=maf([row(**changes)])),
        )


# --- meta -----------------------------------------------------------------------------------------


def _metas() -> None:
    for name, value in (("muts", "muts"), ("uncalled", "mutations_uncalled")):
        meta = META.replace("stable_id: mutations", f"stable_id: {value}").encode()
        cell("meta", f"stable_id_{name}", study(meta_mutations_DOT_txt=meta))
    for value in ("name", "accession", "foo", ""):
        meta = (META + f"swissprot_identifier: {value}\n").encode()
        cell("meta", f"swissprot_{value or 'empty'}", study(meta_mutations_DOT_txt=meta))
    cell(
        "meta",
        "gene_panel",
        study(meta_mutations_DOT_txt=(META + "gene_panel: IMPACT468\n").encode()),
    )
    cell(
        "meta",
        "datatype_discrete",
        study(meta_mutations_DOT_txt=META.replace("datatype: MAF", "datatype: DISCRETE").encode()),
    )
    second = META.replace("data_mutations.txt", "data_mutations2.txt").encode()
    cell(
        "meta",
        "second_same_id",
        study(meta_mutations2_DOT_txt=second, data_mutations2_DOT_txt=VALID),
    )
    other = META.replace("stable_id: mutations", "stable_id: muts2").replace(
        "data_mutations.txt", "data_mutations2.txt"
    )
    cell(
        "meta",
        "second_other_id",
        study(meta_mutations2_DOT_txt=other.encode(), data_mutations2_DOT_txt=VALID),
    )
    sv = (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: STRUCTURAL_VARIANT\n"
        "datatype: SV\nstable_id: structural_variants\nshow_profile_in_analysis_tab: true\n"
        "profile_name: SV\nprofile_description: SV\ndata_filename: data_mutations.txt\n"
    ).encode()
    cell("meta", "sv_names_the_maf", study(meta_sv_DOT_txt=sv))
    uncalled = (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: MUTATION_UNCALLED\n"
        "datatype: MAF\nstable_id: mutations_uncalled\nprofile_name: U\n"
        "profile_description: U\ndata_filename: data_uncalled.txt\n"
    ).encode()
    cell(
        "meta", "uncalled_valid", study(meta_uncalled_DOT_txt=uncalled, data_uncalled_DOT_txt=VALID)
    )
    cell(
        "meta",
        "uncalled_bad",
        study(meta_uncalled_DOT_txt=uncalled, data_uncalled_DOT_txt=maf([row(t_alt_count="abc")])),
    )
    cell(
        "meta",
        "data_names_clinical",
        study(
            meta_mutations_DOT_txt=META.replace(
                "data_mutations.txt", "data_clinical_sample.txt"
            ).encode()
        ),
    )
    cell(
        "meta",
        "data_dot_slash",
        study(
            meta_mutations_DOT_txt=META.replace(
                "data_mutations.txt", "./data_mutations.txt"
            ).encode()
        ),
    )
    cell(
        "meta", "no_mutation_meta", study(meta_mutations_DOT_txt=None, data_mutations_DOT_txt=None)
    )


# --- list -----------------------------------------------------------------------------------------

LIST = case_list("sequenced", ["S1", "S2", "S3"])


def lists(**over: Content | None) -> Cell:
    return study(**{f"case_lists__{key}": value for key, value in over.items()})


def _lists() -> None:
    for key in (
        "cancer_study_identifier",
        "stable_id",
        "case_list_name",
        "case_list_description",
        "case_list_ids",
        "case_list_category",
    ):
        kept = b"\n".join(line for line in LIST.split(b"\n") if not line.startswith(key.encode()))
        cell("list", f"without_{key}", lists(cases_sequenced_DOT_txt=kept))
    cell("list", "no_sequenced", lists(cases_sequenced_DOT_txt=None))
    cell(
        "list",
        "no_directory",
        study(**{"case_lists__cases_sequenced_DOT_txt": None, "case_lists": None}),
    )
    cell(
        "list",
        "directory_symlink",
        study(
            case_lists__cases_sequenced_DOT_txt=None,
            case_lists=("symlink", "lists"),
            lists__cases_sequenced_DOT_txt=LIST,
        ),
    )
    cell(
        "list",
        "no_sequenced_no_maf",
        study(
            case_lists__cases_sequenced_DOT_txt=None,
            meta_mutations_DOT_txt=None,
            data_mutations_DOT_txt=None,
            case_lists__cases_x_DOT_txt=case_list("x", ["S1"]),
        ),
    )
    other_name = case_list("sequenced", ["S1", "S2"])
    cell("list", "other_file_name", lists(cases_sequenced_DOT_txt=None, x_DOT_txt=other_name))
    cell(
        "list",
        "suffix_only",
        lists(cases_sequenced_DOT_txt=None, cases_x_DOT_txt=case_list("foo_sequenced", ["S1"])),
    )
    cell("list", "readme", lists(README_DOT_md=b"# Case lists\n"))
    cell("list", "no_extension", lists(cases_y=case_list("y", ["S1"])))
    cell("list", "hidden", lists(_DOT_hidden=b"garbage"))
    cell("list", "backup", lists(**{"cases_x_DOT_txt~": b"garbage"}))
    cell("list", "subdirectory", lists(sub=DIRECTORY))
    cell("list", "entry_symlink", lists(cases_x_DOT_txt=("symlink", "cases_sequenced.txt")))
    cell(
        "list",
        "entry_symlink_unique",
        study(
            case_lists__cases_x_DOT_txt=("symlink", "../lists/x.txt"),
            lists__x_DOT_txt=case_list("x", ["S1"]),
        ),
    )
    for name, key, value in (
        ("stable_id_201", "stable_id", f"{SID}_" + "s" * (201 - len(SID) - 1)),
        ("stable_id_200", "stable_id", f"{SID}_" + "s" * (200 - len(SID) - 1)),
        ("name_4097", "case_list_name", "n" * 4097),
        ("name_4096", "case_list_name", "n" * 4096),
        ("name_nonchar", "case_list_name", "n\uffff"),
        ("stable_id_nonchar", "stable_id", f"{SID}_x\uffff"),
        ("description_nonchar", "case_list_description", "d\uffff"),
        ("name_empty", "case_list_name", ""),
        ("description_10001", "case_list_description", "d" * 10001),
        ("description_10000", "case_list_description", "d" * 10000),
        ("description_empty", "case_list_description", ""),
        ("description_colon", "case_list_description", "a: b"),
        ("prefix_no_underscore", "stable_id", f"{SID}x"),
        ("stable_id_empty", "stable_id", ""),
        ("stable_id_nbsp", "stable_id", "\u00a0"),
    ):
        text = case_list("x", ["S1"]).decode().split("\n")
        text = [f"{key}: {value}" if line.startswith(key + ":") else line for line in text]
        cell("list", name, lists(cases_x_DOT_txt="\n".join(text).encode()))
    cell("list", "whitespace_line", lists(cases_sequenced_DOT_txt=b" \t \n" + LIST))
    for name, extra in (
        ("extra_types_maf", "genetic_alteration_type: MUTATION_EXTENDED\ndatatype: MAF\n"),
        ("extra_types_generic", "genetic_alteration_type: GENERIC_ASSAY\ndatatype: BINARY\n"),
        ("extra_type_alone", "genetic_alteration_type: MUTATION_EXTENDED\n"),
    ):
        cell("list", name, lists(cases_x_DOT_txt=case_list("x", ["S1"], extra=extra)))
    cell(
        "list",
        "bad_prefix",
        lists(
            cases_x_DOT_txt=case_list("x", ["S1"]).replace(
                f"stable_id: {SID}_x".encode(), b"stable_id: other_x"
            )
        ),
    )
    cell("list", "duplicate_stable_id", lists(cases_x_DOT_txt=case_list("sequenced", ["S1"])))
    cell("list", "bad_category", lists(cases_x_DOT_txt=case_list("x", ["S1"], "nonsense")))
    cell("list", "repeated_category", lists(cases_x_DOT_txt=case_list("x", ["S1"])))
    cell(
        "list",
        "other_study",
        lists(
            cases_x_DOT_txt=case_list("x", ["S1"]).replace(
                f"cancer_study_identifier: {SID}".encode(), b"cancer_study_identifier: other"
            )
        ),
    )
    cell(
        "list",
        "extra_key",
        lists(cases_sequenced_DOT_txt=case_list("sequenced", ["S1"], extra="foo: bar\n")),
    )
    for name, ids in (
        ("duplicate_id", ["S1", "S2", "S1"]),
        ("undefined_id", ["S1", "S9"]),
        ("space_in_id", ["S1", "S 2"]),
        ("padded_id", ["S1", " S2 "]),
        ("nbsp_padded_id", ["S1", "\u00a0S2"]),
        ("comma_ids", ["S1,S2"]),
        ("empty_ids", [""]),
        ("trailing_tab", ["S1", "S2", ""]),
        ("inner_empty", ["S1", "", "S2"]),
        ("normal_id", ["S1", "N1"]),
        ("lower_id", ["s1"]),
    ):
        cell("list", name, lists(cases_sequenced_DOT_txt=case_list("sequenced", ids)))
    cell(
        "list",
        "leading_tab",
        lists(cases_sequenced_DOT_txt=LIST.replace(b"case_list_ids: ", b"case_list_ids: \t")),
    )
    cell(
        "list", "nul_in_id", lists(cases_sequenced_DOT_txt=case_list("sequenced", ["S1", "S\x002"]))
    )
    cell(
        "list",
        "u0001_in_name",
        lists(cases_sequenced_DOT_txt=LIST.replace(b"case_list_name: n", b"case_list_name: n\x01")),
    )
    cell("list", "bom", lists(cases_sequenced_DOT_txt=BOM + LIST))
    cell("list", "blank_line", lists(cases_sequenced_DOT_txt=b"\n" + LIST))
    cell("list", "hash_line", lists(cases_sequenced_DOT_txt=b"#comment\n" + LIST))
    cell("list", "hash_key_line", lists(cases_sequenced_DOT_txt=b"#note: x\n" + LIST))
    cell("list", "second_ids_line", lists(cases_sequenced_DOT_txt=LIST + b"S2\tS3\n"))
    cell("list", "repeated_ids_key", lists(cases_sequenced_DOT_txt=LIST + b"case_list_ids: S2\n"))
    repeated = LIST + f"stable_id: {SID}_x\n".encode()
    cell("list", "repeated_stable_id_key", lists(cases_sequenced_DOT_txt=repeated))
    cell("list", "cr_only", lists(cases_sequenced_DOT_txt=LIST.replace(b"\n", b"\r")))
    cell("list", "crlf", lists(cases_sequenced_DOT_txt=LIST.replace(b"\n", b"\r\n")))
    cell(
        "list",
        "not_utf8",
        lists(
            cases_x_DOT_txt=case_list("x", ["S1"]).replace(
                b"case_list_name: n", b"case_list_name: \xff"
            )
        ),
    )
    cell("list", "no_colon", lists(cases_x_DOT_txt=b"just text\n"))
    many = ["S1", "S2", "S3"] * 25_000
    cell("list", "many_ids", lists(cases_sequenced_DOT_txt=case_list("sequenced", many)))


NAMESPACE_CASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("before", "aaa", ()),
    ("among", "center2", ()),
    ("after", "zzz", ()),
    ("exact_prefix_column", "zygosity", ("zygosity.",)),
    ("no_dot", "zygosity", ("zygosity",)),
    ("sharp_s", "stra\u00dfe", ("STRA\u1e9eE.x",)),
    ("dotted_i", "\u0130d", ("\u0130D.x",)),
    ("kelvin", "kelvin", ("\u212aELVIN.x",)),
    ("duplicate", "zygosity, zygosity", ("zygosity.status",)),
    ("two_one_missing", "zygosity, zzz", ("zygosity.status",)),
)
"""The namespaces dimension (V:1414-1420): a namespace sorting before, among and after the
columns, an exact ``ns.`` column, a column with no dot, Unicode case (sharp s, dotted capital I,
the Kelvin sign), a repeated namespace, and two of which one has no column."""
TYPE_CASES: tuple[tuple[str, str], ...] = (
    ("padded", "genetic_alteration_type:  MUTATION_EXTENDED \ndatatype:  MAF \n"),
    ("lower", "genetic_alteration_type: mutation_extended\ndatatype: maf\n"),
    ("empty", "genetic_alteration_type: \ndatatype: \n"),
    ("limit_value", "genetic_alteration_type: GENERIC_ASSAY\ndatatype: LIMIT-VALUE\n"),
    ("categorical", "genetic_alteration_type: GENERIC_ASSAY\ndatatype: CATEGORICAL\n"),
    ("generic_lower", "genetic_alteration_type: generic_assay\ndatatype: binary\n"),
    ("key_padded", " genetic_alteration_type: MUTATION_EXTENDED\ndatatype: MAF\n"),
)
"""A case list's type keys and values (C:896-903, C:805-818)."""


def renamed(files: Cell, study_id: str) -> Cell:
    """``files`` with the study id ``demo_study`` written ``study_id`` in every file."""
    old = SID.encode()
    return {
        key: value.replace(old, study_id.encode()) if isinstance(value, bytes) else value
        for key, value in files.items()
    }


LIST_PADS: tuple[tuple[str, str, str], ...] = (
    ("bom", "\ufeff", ""),
    ("nbsp", "\u00a0", "\u00a0"),
    ("ideographic", "\u3000", "\u3000"),
    ("tab", "\t", "\t"),
    ("nbsp_value", "", "\u00a0"),
    ("ideographic_value", "", "\u3000"),
)
"""How a case list's line is padded: before its key (a byte order mark, which on line 1 alone is
read past, or whitespace) and around its value (whitespace, which ``str.strip()`` removes)."""


def _list_padding() -> None:
    lines = LIST.decode().rstrip("\n").split("\n")
    for index in range(len(lines)):
        for name, before, after in LIST_PADS:
            padded = list(lines)
            key, _, value = padded[index].partition(": ")
            padded[index] = f"{before}{key}: {after}{value}{after}"
            text = ("\n".join(padded) + "\n").encode()
            cell("padding", f"line{index}_{name}", lists(cases_sequenced_DOT_txt=text))


SNIFF_LINES = ("Hugo_Symbol", "A", "B", "C", "D", "E", "F")
"""A one-column file's lines: the header and six after it, one more than the reference's sniffer
reads (V:470-475)."""


def _sniff() -> None:
    """Review r3 Q10/Q11: a file of 1 to 7 lines with no tab or a tab on any one line, its last
    line ended or not: the sniff the reader re-expresses, line by line, against the reference."""
    for count in range(1, len(SNIFF_LINES) + 1):
        for tab in (None, *range(count)):
            for ended in (True, False):
                lines: list[str] = list(SNIFF_LINES[:count])
                if tab is not None:
                    lines[tab] += "\tx"
                text = "\n".join(lines) + ("\n" if ended else "")
                where = "none" if tab is None else str(tab)
                name = f"lines{count}_tab{where}_{'ended' if ended else 'open'}"
                cell("sniff", name, data(text.encode()))


def _empty_names() -> None:
    """Review r3 Q14: an empty column name at each position of the header."""
    for index in range(len(COLUMNS) + 1):
        columns = (*COLUMNS[:index], "", *COLUMNS[index:])
        cell("header", f"empty_name_at_{index}", data(maf([{**R1, "": "x"}], columns)))


def _dimensions() -> None:
    _list_padding()
    _sniff()
    _empty_names()
    for name, spaces, columns in NAMESPACE_CASES:
        meta = (META + f"namespaces: {spaces}\n").encode()
        body = maf([{**R1, **dict.fromkeys(columns, "1")}], (*COLUMNS, *columns))
        cell("namespace", name, study(meta_mutations_DOT_txt=meta, data_mutations_DOT_txt=body))
    for name, extra in TYPE_CASES:
        cell("types", name, lists(cases_x_DOT_txt=case_list("x", ["S1"], extra=extra)))
    for name, datatype in (("continuous", "CONTINUOUS"), ("zscore", "Z-SCORE")):
        extra = f"genetic_alteration_type: MRNA_EXPRESSION\ndatatype: {datatype}\n"
        files = study(case_lists__cases_x_DOT_txt=case_list("U133", ["S1"], extra=extra))
        cell("types", f"mrna_study_{name}", renamed(files, "mrna"))
    cell("bound", "name_nonchar", data(maf([{**R1, "X\ufffe": "1"}], (*COLUMNS, "X\ufffe"))))
    cell("bound", "name_4097", data(maf([{**R1, "X" * 4097: "1"}], (*COLUMNS, "X" * 4097))))
    wide = (*COLUMNS, *(f"c{index}" for index in range(4097 - len(COLUMNS) + 1)))
    cell("bound", "columns_4097", data(maf([R1], wide)))
    entries: dict[str, Content | None] = {
        f"case_lists__extra_{index:03d}_DOT_txt": case_list(f"e{index}", ["S1"], None)
        for index in range(256)
    }
    cell("bound", "lists_257", study(**entries))
    big = case_list("sequenced", ["S1"]) + b"x: " + b"y" * (8 * 2**20) + b"\n"
    cell("bound", "list_over_8_mib", study(case_lists__cases_sequenced_DOT_txt=big))


def _study_meta() -> None:
    """The study meta's ``add_global_case_list`` (V:4806-4808), each spelling, crossed with a case
    list holding ``<study>_all`` or none."""
    base = STUDY.replace(b"add_global_case_list: true\n", b"")
    for name, line in (
        ("true", b"add_global_case_list: true\n"),
        ("TRUE", b"add_global_case_list: TRUE\n"),
        ("padded", b"add_global_case_list:  True \n"),
        ("false", b"add_global_case_list: false\n"),
        ("yes", b"add_global_case_list: yes\n"),
        ("absent", b""),
    ):
        for listed in (False, True):
            over: dict[str, Content | None] = {"meta_study_DOT_txt": base + line}
            if listed:
                over["case_lists__cases_all_DOT_txt"] = case_list("all", ["S1", "S2", "S3"])
            cell("study", f"global_{name}_{'all' if listed else 'none'}", study(**over))


GENERATORS: tuple[Callable[[], None], ...] = (
    _bytes,
    _header,
    _rows,
    _builds,
    _metas,
    _lists,
    _study_meta,
    _dimensions,
)
for _generator in GENERATORS:
    _generator()
