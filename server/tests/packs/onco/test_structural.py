"""The structural variant reader beyond the corpus (M4.2b-4, D445): the brute forces of the cell
items over their token domains (each calling the reader's row check and D445's predicate
directly, never a whole-study import), the bounds no corpus cell reaches, totality, the one-file
rule over every pair of imported roles, the shared modules pinned to main's checks, what is
declared, and erasure through a sample with SV rows.

The corpus (``test_structural_corpus.py``) holds each class against the reference's recorded
outcomes and the predicates; this module holds what needs other limits, a store or many tokens.
"""

import collections
import contextlib
import functools
import importlib
import importlib.util
import itertools
import random
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.descriptors import ColumnDescriptor, RelationshipDescriptor, TableDescriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import PackRegistry, Refused
from aibi.core.store.erasure import erase
from aibi.core.store.store import StoreRefused

HERE = Path(__file__).resolve().parent


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_sv_unit", HERE / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def corpus() -> ModuleType:
    return _load("structural_corpus")


@functools.cache
def oracle() -> ModuleType:
    return _load("structural_oracle")


def structural() -> Any:
    return importlib.import_module("aibi.packs.onco.structural")


SAMPLES = frozenset({"S1", "S2", "S3"})


def read(
    data: bytes,
    limits: ImportLimits | None = None,
    namespaces: tuple[str, ...] = (),
    cells_before: int = 0,
) -> Any:
    module = structural()
    fields = module.StructuralFields(namespaces)
    return module.read(
        data, "sv.txt", SAMPLES, fields, limits or ImportLimits(), cells_before=cells_before
    )


def code(data: bytes, limits: ImportLimits | None = None, **kwargs: Any) -> str:
    try:
        read(data, limits, **kwargs)
    except Refused as error:
        (refusal,) = error.refusals
        return str(refusal.code)
    return "ok"


def texts(error: Refused | ImportRefused) -> list[tuple[str, str]]:
    return [
        (str(r.code), "".join(getattr(s, "text", "") for s in r.message)) for r in error.refusals
    ]


# --- Bounds ---------------------------------------------------------------------------------------


def test_the_column_bound_is_counted_before_the_header_is_split() -> None:
    c = corpus()
    assert code(c.sv([c.R1]), ImportLimits(table_columns=13)) == "ok"
    assert code(c.sv([c.R1]), ImportLimits(table_columns=12)) == "onco.TOO_MANY_COLUMNS"
    assert code(b"Sample_Id" + b"\t" * 10**6 + b"\n") == "onco.TOO_MANY_COLUMNS"


def test_the_cell_bound_counts_the_sv_file_s_cells() -> None:
    """Stricter (D445): the SV file's columns times its rows, with the cells before it, counted at
    each row and, for a file of no row, at its end; held here, since no corpus cell passes the
    default bound (``HELD`` of ``test_structural_corpus``)."""
    reason = "the study's files have more cells than an import may"
    c = corpus()
    data = c.sv([c.R1, c.R2])
    assert code(data, ImportLimits(import_cells=26)) == "ok"
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=25))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 3: {reason}")]
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=30), cells_before=5)
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 3: {reason}")]
    assert code(c.sv([]), ImportLimits(import_cells=13)) == "ok"
    with pytest.raises(Refused) as raised:
        read(c.sv([]), ImportLimits(import_cells=12))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" {reason}")]


def _least(root: Path, files: Mapping[str, Any]) -> int:
    """The least ``import_cells`` the pack's importer accepts the study at, by bisection."""
    low, high = 1, 2_000
    assert _import(root / "top", files, _cells(high)) == "ok"
    while low < high:
        middle = (low + high) // 2
        if _import(root / str(middle), files, _cells(middle)) == "ok":
            high = middle
        else:
            low = middle + 1
    return low


def test_the_import_counts_the_maf_s_cells_before_the_sv_file_s(tmp_path: Path) -> None:
    """``structural.imported`` reads after the clinical files, the case lists and the MAF, all
    counted: adding a MAF of 24 columns and 2 rows raises the least bound by its 48 cells, and
    one below it is ``TOO_MANY_CELLS``."""
    base = _load("mutation_corpus")
    c = corpus()
    without = c.study()
    with_maf = c.study(meta_mutations_DOT_txt=base.META.encode(), data_mutations_DOT_txt=base.VALID)
    least = _least(tmp_path / "a", without)
    least_maf = _least(tmp_path / "b", with_maf)
    assert least_maf - least == 48
    assert _import(tmp_path / "c", with_maf, _cells(least_maf - 1)) == "onco.TOO_MANY_CELLS"
    # review r1 m6 (N33): the least bound is every table's cells, the clinical files' (2 x 3 and
    # 2 x 3) and the case lists' (4 x 1 and 2 x 3) counted before the SV file's (13 x 2), and the
    # refusal one below it is the SV reader's own, naming the SV file
    assert least == 6 + 6 + 10 + 26
    with pytest.raises(Refused) as raised:
        _import_raising(tmp_path / "d", without, _cells(least - 1))
    (refusal,) = raised.value.refusals
    assert str(refusal.code) == "onco.TOO_MANY_CELLS"
    assert getattr(refusal.message[0], "data", None) == "data_sv.txt"


def _cells(n: int) -> ImportLimits:
    return ImportLimits(import_cells=n)


def _import(root: Path, files: Mapping[str, Any], limits: ImportLimits) -> str:
    try:
        _import_raising(root, files, limits)
    except Refused as error:
        return str(error.refusals[0].code)
    return "ok"


def _import_raising(root: Path, files: Mapping[str, Any], limits: ImportLimits) -> None:
    from aibi.core.importers.confine import Confinement
    from aibi.core.schema.pack_api import ImportOptions

    directory = root / "imports" / "study"
    for path, content in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    confinement = Confinement.of(directory.parent)
    options = ImportOptions(
        dataset="study", reader=confinement, limits=limits, at="2026-01-01T00:00:00Z"
    )
    onco = importlib.import_module("aibi.packs.onco")
    onco.PACK.importer.import_source(confinement.confine(directory), options)


def test_a_refusal_names_no_value() -> None:
    c = corpus()
    secret = "SeCrEt9"
    for row in (
        {**c.R1, "Sample_Id": secret},
        {**c.R1, "SV_Status": secret},
        {**c.R1, "NCBI_Build": secret},
        {**c.R1, "Site1_Entrez_Gene_Id": f"-{secret.count('e')}"},
        {**c.R1, "Site1_Hugo_Symbol": " NA", "Event_Info": secret},
    ):
        with pytest.raises(Refused) as raised:
            read(c.sv([row]))
        assert secret not in repr(raised.value.refusals)


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=600))
def test_the_reader_is_total_over_bytes(data: bytes) -> None:
    """Any bytes after a valid header: ``Structural`` or ``Refused``, never another exception."""
    c = corpus()
    with contextlib.suppress(Refused):
        read(c.sv([c.R1]) + data)


@settings(max_examples=200, deadline=None)
@given(st.binary(max_size=300))
def test_the_reader_is_total_over_any_bytes(data: bytes) -> None:
    with contextlib.suppress(Refused):
        read(data)


# --- The brute forces: each item's predicate against the reader's row check -----------------------

PADDING = (
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
"""The 20 characters ``str.strip()`` removes that a cell can hold: the 29 it removes less tab,
LF and CR (no cell holds them) and the six the pack refuses as control characters (D445)."""


def test_the_padding_is_every_character_strip_removes_that_a_cell_holds() -> None:
    control = {chr(p) for p in (*range(0x20), 0x7F)} - {"\t", "\n", "\r"}
    stripped = {chr(p) for p in range(sys.maxunicode + 1) if chr(p).strip() == ""}
    assert len(stripped) == 29
    assert set(PADDING) == stripped - control - {"\t", "\n", "\r"}
    assert len(PADDING) == 20
    assert "\u200b".strip() == "\u200b"
    assert "\ufeff".strip() == "\ufeff"


def padded_tokens(bases: tuple[str, ...]) -> list[str]:
    """Each base with each padding character before, after and around it, and each padding alone
    and doubled."""
    out: list[str] = []
    for pad in PADDING:
        out += [pad, pad + pad]
        for base in bases:
            out += [pad + base, base + pad, pad + base + pad]
    return out


ENTREZ_DOMAIN = (
    *(corpus().ENTREZ_TOKENS),
    "1E3",
    ".0",
    "0.",
    "+0.0",
    "-0e0",
    "0E-3",
    "0e5",
    "-0.0",
    "-1.0",
    "\u0966",
    "\U0001d7ce",
    "\uff10",
    " +0 ",
    "-" + "9" * 5000,
    "9" * 4300,
    "9" * 5000,
    "\u0967",
    "\u0967\u0968",
)
"""The Entrez tokens of review r3 (MJ-A, 224 live cells), the corpus's, and their neighbours."""
SHORT_ENTREZ = tuple(token for token in dict.fromkeys(ENTREZ_DOMAIN) if len(token) < 30)
"""Every Entrez token but the very long ones, each padded below (review r1 MJ-1 (c): M20' tokens
padded too, ``1e3`` before a no-break space)."""
DOMAINS: Mapping[str, tuple[str, ...]] = {
    "Sample_Id": ("S1", "S9", *oracle().NULL, *padded_tokens(("S1", "S9", *oracle().NULL))),
    "SV_Status": (
        "SOMATIC",
        "germline",
        "LIKELY",
        *oracle().NULL,
        *padded_tokens(("SOMATIC", *oracle().NULL)),
    ),
    "NCBI_Build": (
        "GRCh37",
        "grch38",
        "37",
        *oracle().NULL,
        *padded_tokens(("GRCh37", *oracle().NULL)),
    ),
    "Site1_Hugo_Symbol": ("EML4", *oracle().NULL, *padded_tokens(("EML4", *oracle().NULL))),
    "Site2_Hugo_Symbol": ("ALK", *oracle().NULL, *padded_tokens(("ALK", *oracle().NULL))),
    "Site1_Entrez_Gene_Id": (*ENTREZ_DOMAIN, *padded_tokens(("27436", *SHORT_ENTREZ))),
    "Site2_Entrez_Gene_Id": (*ENTREZ_DOMAIN, *padded_tokens(("238", *SHORT_ENTREZ))),
}
"""The seven columns the reference reads, each with its token domain: the null spellings and a
value, each padded by every padding character, and for the Entrez ids the numbers both parses
read."""
GENES = ("Site1_Hugo_Symbol", "Site1_Entrez_Gene_Id", "Site2_Hugo_Symbol", "Site2_Entrez_Gene_Id")


def _row_classes(column: str) -> Mapping[str, Mapping[str, str]]:
    """``gene``: another site names a gene; ``alone``: no other column names one."""
    if column not in GENES:
        return {"gene": {}, "alone": dict.fromkeys(GENES, "")}
    blank = {name: "" for name in GENES if name != column}
    same_site = {name: "" for name in GENES if name[:5] == column[:5] and name != column}
    return {"gene": same_site, "alone": blank}


def _reading() -> Any:
    module = structural()
    reading = module._Reading(  # pyright: ignore[reportPrivateUsage]
        "sv.txt", SAMPLES, module.StructuralFields(()), ImportLimits(), 0
    )
    assert reading.header(1, "\t".join(corpus().COLUMNS), 0) is None
    return reading


def _found(reading: Any, fields: list[str]) -> str | None:
    failure = reading.checked(fields)
    if failure is None:
        return None
    _, code_, reason = failure
    return f"onco.{code_}|{reason}"


def test_every_cell_item_follows_its_predicate_on_the_seven_columns() -> None:
    """Review r2 MJ1, r3 MJ-A: every token of each of the seven columns' domain, on each row class,
    through the reader's row check (``_Reading.checked``, called directly), refused by exactly the
    first item whose predicate holds (``structural_oracle.row_item``), accepted when none does."""
    c, o = corpus(), oracle()
    names = list(c.COLUMNS)
    cases = 0
    for column, tokens in DOMAINS.items():
        for name, changes in _row_classes(column).items():
            for token in tokens:
                fields = [{**c.R1, **changes, column: token}[n] for n in names]
                wanted = o.row_item(names, fields, SAMPLES)
                found = _found(_reading(), fields)
                expected = None if wanted is None else o.said(wanted)
                assert found == expected, (column, name, token)
                cases += 1
    assert cases > 4_000


BUILD_DOMAIN = (
    "GRCh37",
    "grch37",
    "GRCh38",
    "37",
    "hg19",
    *oracle().NULL,
    *padded_tokens(("GRCh37", "grch37", "GRCh38", *oracle().NULL)),
)


def test_every_build_in_the_first_and_the_second_row_follows_its_predicate() -> None:
    """Review r1 MJ-1 (c): every build token, padded or not, as the first row's build before a
    second row's ``GRCh37`` and as the second row's after a first row's, through the reader's row
    check twice (its first build kept between the rows) and the predicates."""
    c, o = corpus(), oracle()
    names = list(c.COLUMNS)
    for token in BUILD_DOMAIN:
        for builds in ((token, "GRCh37"), ("GRCh37", token), (" GRCh37", token)):
            reading = _reading()
            first: str | None = None
            for build in builds:
                fields = [{**c.R1, "NCBI_Build": build}[n] for n in names]
                found = _found(reading, fields)
                wanted = o.row_item(names, fields, SAMPLES, first)
                assert found == (None if wanted is None else o.said(wanted)), builds
                if found is not None:
                    break
                if first is None and not o.stripped_null(build):
                    first = build.strip()


def test_m20_and_m27_hold_on_every_entrez_token_of_review_r3() -> None:
    """M20' and M27' (review r3 MJ-A): an Entrez id ``int()`` does not read, stripped, that the
    core reads as a number is refused (stricter), as is one ``str.strip()`` makes ``0`` that the
    core reads as 0; one the core does not read is counted, never refused; and non-positive is by
    ``int()`` alone. The three never hold together."""
    o = oracle()
    for token in ENTREZ_DOMAIN:
        stripped = token.strip()
        if stripped in o.NULL:
            continue
        number = o.as_int(stripped)
        m20 = number is None and o.core_reads(token)
        m27 = stripped == "0" and token != "0" and o.core_reads(token)
        nonpositive = number is not None and number <= 0
        assert sum((m20, m27, nonpositive)) <= 1, token
    for token in ("0.0", "-1e3", "1e3", "7157.0", "238.0", "0e5", "-0.0"):
        assert o.as_int(token) is None, token
        assert o.core_reads(token), token
    for token in ("nan", "inf", "0x10", "\u0661", "1_0", "7157\xa0", "9" * 5000):
        assert not o.core_reads(token), token
    for token in ("\u0660", "0_0", "00", "+0", "-0", "\uff10", "\U0001d7ce"):
        number = o.as_int(token.strip())
        assert number is not None, token
        assert number <= 0, token


def test_core_integer_is_the_core_s_integer_parse_over_the_entrez_domain() -> None:
    """``mutation.core_integer``, which ``structural`` imports, equals the core's integer parse
    over every Entrez token here (review r3 m1)."""
    from aibi.core.store.cells import _integer  # pyright: ignore[reportPrivateUsage]

    mutation = importlib.import_module("aibi.packs.onco.mutation")
    for token in (*ENTREZ_DOMAIN, *DOMAINS["Site1_Entrez_Gene_Id"]):
        assert mutation.core_integer(token) == (_integer(token, token) is not None), token


def test_a_padded_zero_alone_is_no_gene_first() -> None:
    """Review r4 m2, the per-row order: `` 0`` as the only gene cell is ``sv-no-gene`` to the
    reference (same outcome), which comes before M27' (stricter)."""
    c = corpus()
    alone = {"Site1_Hugo_Symbol": "", "Site2_Hugo_Symbol": "", "Site2_Entrez_Gene_Id": ""}
    fields = [{**c.R1, **alone, "Site1_Entrez_Gene_Id": " 0"}[n] for n in c.COLUMNS]
    assert (_found(_reading(), fields) or "").startswith("onco.NO_GENE|")
    fields = [{**c.R1, "Site1_Entrez_Gene_Id": " 0"}[n] for n in c.COLUMNS]
    assert (_found(_reading(), fields) or "").startswith("onco.PADDED_VALUE|the Entrez")


def test_the_build_is_the_file_s_first_stripped_case_sensitive() -> None:
    """V:3224-3241: validity is case-insensitive after stripping, sameness the stripped string as
    written; the first build is kept even when it is invalid."""
    c = corpus()

    def rows(*builds: str) -> bytes:
        return c.sv(
            [{**c.R1, "NCBI_Build": b, "Sample_Id": f"S{i % 3 + 1}"} for i, b in enumerate(builds)]
        )

    assert code(rows("GRCh37", " GRCh37", "GRCh37\u3000")) == "ok"
    assert code(rows("grch38", "grch38")) == "ok"
    assert code(rows("GRCh37", "grch37")) == "onco.NCBI_BUILD"
    assert code(rows("NA", "GRCh38")) == "ok"
    assert code(rows("37", "GRCh37")) == "onco.NCBI_BUILD"
    assert code(rows("GRCh37", "GRCh38")) == "onco.NCBI_BUILD"


def test_the_namespaces_are_the_sv_reference_s_rule() -> None:
    """V:1415-1424 alone (review r3 m3): ``ascn`` asks for no ASCN columns, ``foo,`` and an empty
    value name an empty namespace."""
    c = corpus()
    with_ascn = c.sv([{**c.R1, "ascn.x": "1"}], (*c.COLUMNS, "ascn.x"))
    assert code(with_ascn, namespaces=("ascn",)) == "ok"
    assert code(c.sv([c.R1]), namespaces=("ascn",)) == "onco.MISSING_COLUMN"
    assert code(with_ascn, namespaces=("ascn", "")) == "onco.MISSING_COLUMN"
    dotted = c.sv([{**c.R1, ".x": "1"}], (*c.COLUMNS, ".x"))
    assert code(dotted, namespaces=("",)) == "ok"


# --- One file, one role (review r4 MJ-D) ----------------------------------------------------------

KINDS = ("patient", "sample", "maf", "sv")
FILES = {
    "patient": "data_clinical_patient.txt",
    "sample": "data_clinical_sample.txt",
    "maf": "data_mutations.txt",
    "sv": "data_sv.txt",
}
METAS = {
    "patient": "meta_clinical_patient.txt",
    "sample": "meta_clinical_sample.txt",
    "maf": "meta_mutations.txt",
    "sv": "meta_sv.txt",
}


def _listing() -> dict[str, tuple[str, bytes]]:
    base = _load("mutation_corpus")
    files = base.study(meta_sv_DOT_txt=corpus().META.encode(), data_sv_DOT_txt=corpus().VALID)
    return {
        name: ("file", content)
        for name, content in files.items()
        if "/" not in name and isinstance(content, bytes)
    }


def test_no_two_imported_metas_name_one_data_file() -> None:
    """Review r4 MJ-D, brute force over every ordered pair of imported roles: the second's meta
    naming the first's data file (its own removed) is refused ``DATA_FILE``, by the rule that
    already refused the pair (D409's phase 7 for the clinical files and the MAF, phase 9's Reads
    rule for the SV file in either direction)."""
    paths = _load("paths_oracle")
    study = importlib.import_module("aibi.packs.onco.study")
    found, refusal = paths.discover_listing(study, _listing())
    assert refusal is None
    assert found.structural is not None
    pairs = 0
    for first, second in itertools.permutations(KINDS, 2):
        entries = _listing()
        meta = entries[METAS[second]][1].replace(FILES[second].encode(), FILES[first].encode())
        entries[METAS[second]] = ("file", meta)
        del entries[FILES[second]]
        _, refusal = paths.discover_listing(study, entries)
        assert refusal is not None, (first, second)
        assert str(refusal.code) == "onco.DATA_FILE", (first, second)
        pairs += 1
    assert pairs == 12


@pytest.mark.parametrize("name", ["data_sv.txt", "DATA_SV.txt", "data_SV.TXT"])
def test_a_meta_never_imported_naming_the_sv_file_is_refused(name: str) -> None:
    """The SV file joins Reads(S): a TIMELINE meta naming it, exactly or under folding, is refused
    by phase 9's rule."""
    paths = _load("paths_oracle")
    study = importlib.import_module("aibi.packs.onco.study")
    entries = _listing()
    timeline = (
        "cancer_study_identifier: demo_study\ngenetic_alteration_type: CLINICAL\n"
        f"datatype: TIMELINE\ndata_filename: {name}\n"
    )
    entries["meta_timeline.txt"] = ("file", timeline.encode())
    _, refusal = paths.discover_listing(study, entries)
    assert refusal is not None
    said = paths.said(refusal.message)
    assert said == f"\u00abmeta_timeline.txt\u00bb has a data_filename that {paths.READS}"


# --- The shared modules gain no check (review r4 MJ-B') -------------------------------------------

MAIN_SHARED: Mapping[str, tuple[int, int, Mapping[str, int]]] = {
    "study": (
        21,
        20,
        {
            "No meta file in the study's directory is a sample's": 1,
            "No meta file in the study's directory is a study's": 1,
            "The study's directory holds more meta-named entries than M4.2a reads": 1,
            "has a data_filename that is a symlink": 1,
            "has a data_filename that is not a file name M4.2a accepts": 1,
            "has a data_filename that names a clinical meta file's data file": 1,
            "has a data_filename that names a meta file": 1,
            "has a data_filename that names no regular file in the study's directory": 1,
            "has a data_filename that names the other clinical meta file's data file": 1,
            "holds a stable_id that M4.2a does not accept": 1,
            "is a second clinical meta file of its kind": 1,
            "is a second mutation meta file": 1,
            "is a second study meta file": 1,
            "is a symlink, which the pack never follows": 1,
            "is larger than a meta file may be": 1,
            "names another study than the study meta": 1,
            "repeats another meta file's stable_id": 1,
            "{} a {} that is not a file name M4.2a accepts": 1,
            (
                "{} a {} that names a meta file or a clinical data file, exactly or under case "
                "and normalisation folding"
            ): 1,
            "{} a {} that names a symlink, exactly or under case and normalisation folding": 1,
            "{} a {} that names no regular file in the study's directory": 1,
        },
    ),
    "meta": (
        12,
        10,
        {
            "has a line without ':'": 1,
            "has no meta file type cBioPortal defines": 2,
            "holds a cancer_study_identifier that M4.2a does not accept": 1,
            "holds a pmid that M4.2a does not accept": 1,
            "holds a reference_genome that M4.2a does not accept": 1,
            "holds a stable_id that a mutation meta may not": 1,
            "holds a swissprot_identifier that is neither name nor accession": 1,
            "holds a type_of_cancer that M4.2a does not accept": 1,
            "holds a {} that M4.2a does not accept": 1,
            "is not UTF-8": 1,
            "lacks the key {}": 1,
        },
    ),
    "importer": (
        3,
        3,
        {
            "has a data_filename that names a MAF whose name is not Unicode text": 1,
            "has a data_filename that names a file whose name is not Unicode text": 1,
            (
                "is a mutation meta file, and no case list's stable_id is the study id followed "
                "by _sequenced"
            ): 1,
        },
    ),
}
"""Main's (7a99a62) refusal sites, checks and templates (a multiset) of the modules slice 1b's
walker holds, which slice 4 changes in place only (review r4 MJ-B'): every check of the SV meta
and file is in ``structural``, reached through calls that hold none."""


def test_the_shared_modules_are_main_s() -> None:
    walker = _load("structural_checks")
    for module, (sites, checks, templates) in MAIN_SHARED.items():
        found = walker.sites(module)
        assert len(found) == sites, module
        assert len(walker.checks(module)) == checks, module
        counted = collections.Counter(t for _, ts in found for t in ts)
        assert counted == collections.Counter(templates), module


def test_the_shared_pin_fails_on_a_check_added_to_study(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pin's self-check (review r3 m7, protoB): a new ``if`` in ``study.py`` reusing a slice-1b
    template, as the walker reads it, fails it."""
    walker = _load("structural_checks")
    source = (walker.WALKER.PACK / "study.py").read_text(encoding="utf-8")
    added = source.replace(
        "    reads = reads | frozenset(structural.used(sv))\n",
        "    reads = reads | frozenset(structural.used(sv))\n"
        "    if sv is not None and sv.data.name in reads:\n"
        '        raise entry_refused("DATA_FILE", sv.meta, "has a data_filename that names a '
        "clinical meta file's data file\")\n",
    )
    assert added != source
    original = walker.WALKER.PACK

    class Pack:
        def __truediv__(self, name: str) -> Any:
            if name == "study.py":
                return _Text(added)
            return original / name

    monkeypatch.setattr(walker.WALKER, "PACK", Pack())
    found = walker.sites("study")
    assert len(found) == MAIN_SHARED["study"][0] + 1


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text

    def read_text(self, encoding: str = "utf-8") -> str:
        del encoding
        return self.text


# --- What is declared, and erasure ----------------------------------------------------------------


def write(world: Any, name: str, files: Mapping[str, Any]) -> Path:
    directory = world.root / "imports" / name
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    for path, content in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return directory


def test_the_table_s_columns_and_relationship(world: Any, registry: PackRegistry) -> None:
    """D445: one keyless measurement table, ``onco:structural_variant``, rows as the file holds
    them; the Entrez ids and positions integers, the status and build categories where D228 makes
    them so, the symbols mapped to ``onco:gene_symbol``; the four null spellings declared for the
    seven columns but the sample id (which is refused as no sample instead); ``Sample_Id`` an
    identifier and the child of an imported relationship to ``samples``; no coverage."""
    c = corpus()
    published = world.publish("t", write(world, "t", c.study()), registry, pack="onco")
    found = {d.id: d for d in world.store.descriptors(published.manifest)}
    table = found["structural_variants"]
    assert isinstance(table, TableDescriptor)
    assert table.fields.role == "measurement"
    assert table.fields.primary_key is None
    assert table.fields.maps_to is not None
    assert table.fields.maps_to.concept == "onco:structural_variant"
    assert table.extensions["onco"] == {"meta": "STRUCTURAL_VARIANT:SV"}
    columns = {d.id: d for d in found.values() if isinstance(d, ColumnDescriptor)}
    prefix = "structural_variants."
    types = {
        k[len(prefix) :]: v.fields.datatype for k, v in columns.items() if k.startswith(prefix)
    }
    assert types == {
        "sample_id": "string",
        "sv_status": "category",
        "site1_hugo_symbol": "string",
        "site1_entrez_gene_id": "integer",
        "site1_chromosome": "string",
        "site1_position": "integer",
        "site2_hugo_symbol": "string",
        "site2_entrez_gene_id": "integer",
        "site2_chromosome": "string",
        "site2_position": "integer",
        "ncbi_build": "category",
        "event_info": "string",
        "class": "string",
    }
    codes = {"": "UNKNOWN", "0": "UNKNOWN", "NA": "UNKNOWN", "Unknown": "UNKNOWN"}
    for column in (
        "sv_status",
        "ncbi_build",
        "site1_hugo_symbol",
        "site2_hugo_symbol",
        "site1_entrez_gene_id",
        "site2_entrez_gene_id",
    ):
        assert columns[prefix + column].fields.missing_codes == codes, column
    assert columns[prefix + "sample_id"].fields.missing_codes is None
    assert columns[prefix + "sample_id"].fields.identifier is True
    for column in ("site1_hugo_symbol", "site2_hugo_symbol"):
        mapped = columns[prefix + column].fields.maps_to
        assert mapped is not None
        assert mapped.concept == "onco:gene_symbol"
    relationship = found["rel:structural_variants.sample_id"]
    assert isinstance(relationship, RelationshipDescriptor)
    assert relationship.fields.parent_table == "samples"
    for field in ("child_table", "child_columns", "parent_table", "parent_columns", "cardinality"):
        assert relationship.curation[f"/fields/{field}"].status == "imported", field
    assert not any(d.kind == "coverage" for d in found.values())


def test_a_one_row_file_s_status_and_build_are_strings(world: Any, registry: PackRegistry) -> None:
    """Review r1 m7 (N37): D228's half bound decides the category, so one row's status and build
    (one distinct value, one present cell) are ``string``, not ``category``."""
    c = corpus()
    published = world.publish("o", write(world, "o", c.data(c.sv([c.R1]))), registry, pack="onco")
    found = {d.id: d for d in world.store.descriptors(published.manifest)}
    for column in ("sv_status", "ncbi_build"):
        descriptor = found[f"structural_variants.{column}"]
        assert isinstance(descriptor, ColumnDescriptor)
        assert descriptor.fields.datatype == "string", column


def test_d228_s_value_bound_on_the_status() -> None:
    """D228's bound on distinct values (review r1, N42): 50 spellings of ``somatic`` (each a case
    of it, which the reference accepts) on two rows each make a category; 51 do not."""
    c = corpus()
    spellings = sorted(
        {
            "".join(ch.upper() if i >> k & 1 else ch for k, ch in enumerate("somatic"))
            for i in range(128)
        }
    )

    def category(count: int) -> bool:
        rows = [
            {**c.R1, "SV_Status": spelling, "Sample_Id": f"S{n % 3 + 1}"}
            for n, spelling in enumerate(spellings[:count] * 2)
        ]
        found = {column.name: column.category for column in read(c.sv(rows)).columns}
        return found["SV_Status"]

    assert category(50) is True
    assert category(51) is False


def test_d228_counts_present_cells_and_keeps_padded_spellings_apart() -> None:
    """Review r2 MJ-2 (e), D2 and D3: an empty status is no present cell (one ``SOMATIC`` among
    three empty cells is one present value of one: no category), and a padded spelling is a value
    of its own (four spellings of ``SOMATIC`` padded apart on four rows: no category)."""
    c = corpus()

    def status_category(statuses: list[str]) -> bool:
        rows = [
            {**c.R1, "SV_Status": status, "Sample_Id": f"S{n % 3 + 1}"}
            for n, status in enumerate(statuses)
        ]
        return {col.name: col.category for col in read(c.sv(rows)).columns}["SV_Status"]

    assert status_category(["SOMATIC", "SOMATIC", "", ""]) is True
    assert status_category(["SOMATIC", "", "", ""]) is False
    assert status_category(["SOMATIC", " SOMATIC", "SOMATIC ", "SOMATIC\u3000"]) is False


def test_both_sites_non_positive_names_site_1_first() -> None:
    """Review r2 MJ-2 (e), E6: with both Entrez ids non-positive, the refusal names site 1's
    column (column 4), the first by the per-row order."""
    c = corpus()
    with pytest.raises(Refused) as raised:
        read(c.sv([c.R2, {**c.R1, "Site1_Entrez_Gene_Id": "-1", "Site2_Entrez_Gene_Id": "-2"}]))
    assert texts(raised.value) == [
        ("onco.ENTREZ_VALUE", " line 3, column 4: the Entrez gene id is not positive")
    ]


def test_a_declared_null_spelling_is_missing_not_unparsed_or_a_value(
    world: Any, registry: PackRegistry
) -> None:
    """D445: an Entrez id or a symbol of ``0``, ``NA`` or ``Unknown``, and a status of ``NA``, are
    the reference's no value: no ``unparsed`` note, and the status column states none of them."""
    c = corpus()
    rows = [
        {**c.R1, "Site1_Entrez_Gene_Id": "0", "Site1_Hugo_Symbol": "0", "SV_Status": "NA"},
        {**c.R2, "Site2_Entrez_Gene_Id": "Unknown", "Site2_Hugo_Symbol": "NA"},
        {**c.R1, "Sample_Id": "S3", "Site1_Entrez_Gene_Id": "NA", "SV_Status": ""},
    ]
    files = c.study(data_sv_DOT_txt=c.sv(rows))
    published = world.publish("n", write(world, "n", files), registry, pack="onco")
    subjects = {note.subject for note in published.notes if note.kind == "unparsed"}
    assert not any(str(subject).startswith("structural_variants.") for subject in subjects)


def test_erasing_a_sample_with_sv_rows(world: Any, registry: PackRegistry) -> None:
    """Erasing S1, which has an SV row, is refused while a live release holds it; a re-import that
    drops S1 from the samples but keeps its SV row is refused by the pack (``UNKNOWN_SAMPLE``);
    once the SV file drops it too, the erasure goes through."""
    c = corpus()
    base = _load("mutation_corpus")
    world.publish("e", write(world, "e", c.study()), registry, pack="onco")
    with pytest.raises(StoreRefused) as blocked:
        erase(world.store, "e", "samples", ["S1"], "operator:Ada")
    assert str(blocked.value.refusal.code) == "ERASURE_BLOCKED"
    samples = base.SAMPLES.replace(b"P1\tS1\n", b"")
    patients = base.PATIENTS.replace(b"P1\tF\n", b"")
    lists = base.case_list("sequenced", ["S2", "S3"])
    kept = c.study(
        data_clinical_sample_DOT_txt=samples,
        data_clinical_patient_DOT_txt=patients,
        case_lists__cases_sequenced_DOT_txt=lists,
    )
    with pytest.raises(ImportRefused) as raised:
        world.reimport("e", write(world, "e", kept), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.UNKNOWN_SAMPLE"]
    clean = c.study(
        data_clinical_sample_DOT_txt=samples,
        data_clinical_patient_DOT_txt=patients,
        case_lists__cases_sequenced_DOT_txt=lists,
        data_sv_DOT_txt=c.sv([c.R2, {**c.R1, "Sample_Id": "S3"}]),
    )
    world.reimport("e", write(world, "e", clean), registry, pack="onco")
    erased = erase(world.store, "e", "samples", ["S1"], "operator:Ada")
    assert erased.withdrawn


ATOMS = ("\t", "\n", "\r", "#", " ", "\ufeff", "\xa0", "\x85", "\ufffe", "\u2028", "\x00")
ATOMS += ("NA", "0", " 0", "Unknown", "S1", "S9", "1e3", "-1", "GRCh38", "germline", "x")
RAW = (b"\xed\xa0\x80", b"\xff", b"\xc0\xaf", b"\xef\xbb\xbf")


@pytest.mark.parametrize("seed", range(2))
def test_a_structured_fuzz_through_the_publish_is_total(
    seed: int, world: Any, registry: PackRegistry
) -> None:
    """The SV file mutated by up to four insertions or replacements of the tokens the reader splits
    or judges, published through the store: every outcome is ``ok`` or one ``onco.`` refusal,
    never a raw exception or ``PACK_FAILED``. 12 cases a seed, seeded."""
    c = corpus()
    rng = random.Random(seed)
    for index in range(12):
        data = c.VALID
        for _ in range(rng.randint(1, 4)):
            at = rng.randint(0, len(data))
            piece = rng.choice(RAW) if rng.random() < 0.1 else rng.choice(ATOMS).encode()
            end = min(len(data), at + rng.randint(1, 10)) if rng.random() < 0.3 else at
            data = data[:at] + piece + data[end:]
        name = f"f{seed}_{index}"
        try:
            world.publish(name, write(world, name, c.data(data)), registry, pack="onco")
        except ImportRefused as error:
            codes = {str(refusal.code) for refusal in error.refusals}
            assert all(code_.startswith("onco.") for code_ in codes), (data, codes)
