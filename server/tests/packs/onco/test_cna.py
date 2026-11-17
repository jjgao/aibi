"""The discrete copy-number reader beyond the corpus (M4.2b-3, D446): the brute forces of the cell
and row items over their token domains (each calling the reader's row check and D446's predicate
directly, never a whole-study import), pairs of rows for the gene keys, the cell count against the
core's, the bounds no corpus cell reaches, totality, memory, the one-file rule over every pair of
imported roles, the shared modules pinned, what is declared, what the published table answers, and
erasure through a sample with CNA rows.

The corpus (``test_cna_corpus.py``) holds each class against the reference's recorded outcomes and
the predicates; this module holds what needs other limits, a store or many tokens.
"""

import contextlib
import functools
import importlib
import importlib.util
import itertools
import random
import shutil
import sys
import tracemalloc
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.importers import checks as core_checks
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    CoverageDescriptor,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import PackRegistry, Refused, cell_digest
from aibi.core.store.erasure import erase
from aibi.core.store.store import StoreRefused

HERE = Path(__file__).resolve().parent


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_cna_unit", HERE / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def corpus() -> ModuleType:
    return _load("cna_corpus")


@functools.cache
def oracle() -> ModuleType:
    return _load("cna_oracle")


def cna() -> Any:
    return importlib.import_module("aibi.packs.onco.cna")


SAMPLES = frozenset({"S1", "S2", "S3"})


def read(data: bytes, limits: ImportLimits | None = None, cells_before: int = 0) -> Any:
    return cna().read(data, "cna.txt", SAMPLES, limits or ImportLimits(), cells_before=cells_before)


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


# --- What is declared -----------------------------------------------------------------------------


def test_the_tables_columns_relationship_and_coverage(world: Any, registry: PackRegistry) -> None:
    """D446: ``cna_calls`` keyed by sample and gene, a pack's unpivoted table with its declaration
    (D401), its ``cna`` column a category of the five levels in order with the value map onto
    ``onco:cna_level`` (``-1.5`` as ``-2``), imported; the relationship to ``samples`` imported;
    the grouped coverage by ``NA`` pattern, scoped by the gene."""
    c = corpus()
    published = world.publish("t", write(world, "t", c.study()), registry, pack="onco")
    found = {d.id: d for d in world.store.descriptors(published.manifest)}
    table = found["cna_calls"]
    assert isinstance(table, TableDescriptor)
    assert table.fields.role == "measurement"
    assert table.fields.primary_key == ["sample_id", "gene"]
    assert table.fields.maps_to is not None
    assert table.fields.maps_to.concept == "onco:cna_call"
    assert table.fields.source is not None
    assert table.fields.source.kind == "pack"
    assert table.fields.source.original_name == "data_cna.txt"
    assert table.extensions["onco"] == {"meta": "COPY_NUMBER_ALTERATION:DISCRETE"}
    level = found["cna_calls.cna"]
    assert isinstance(level, ColumnDescriptor)
    assert level.fields.datatype == "category"
    assert level.fields.permissible_values is not None
    values = [v.value for v in level.fields.permissible_values.values]
    assert values == ["-2", "-1.5", "-1", "1", "2"]
    assert level.fields.permissible_values.ordered is True
    assert level.fields.absent == ["0"]
    mapped = level.fields.maps_to
    assert mapped is not None
    assert mapped.concept == "onco:cna_level"
    assert mapped.transform is not None
    assert mapped.transform.model_dump(mode="json") == {
        "value_map": {"-2": "-2", "-1.5": "-2", "-1": "-1", "1": "1", "2": "2"}
    }
    assert level.curation["/fields/maps_to"].status == "imported"
    gene = found["cna_calls.gene"]
    assert isinstance(gene, ColumnDescriptor)
    assert gene.fields.maps_to is not None
    assert gene.fields.maps_to.concept == "onco:gene_symbol"
    relationship = found["rel:cna_calls.sample_id"]
    assert isinstance(relationship, RelationshipDescriptor)
    assert relationship.fields.parent_table == "samples"
    assert relationship.curation["/fields/cardinality"].status == "imported"
    coverage = found["cov:cna_calls.sample_id"]
    assert isinstance(coverage, CoverageDescriptor)
    parents = coverage.fields.parents
    assert parents is not None
    assert parents != "all"
    written = parents.model_dump(mode="json", exclude_none=True)
    assert written == {
        "assignment": {
            "table": "cna_assignment",
            "parent_columns": {"sample_id": "sample_id"},
            "group_column": "group",
        },
        "groups": {
            "table": "cna_groups",
            "group_column": "group",
            "scope_columns": {"gene": "gene"},
        },
    }
    for name in ("cna_assignment", "cna_groups"):
        coverage_table = found[name]
        assert isinstance(coverage_table, TableDescriptor)
        assert coverage_table.fields.role == "coverage"


def test_the_tables_rows_and_the_declaration() -> None:
    """The calls are the non-neutral cells as written, gene-major in the file's order; the coverage
    is one group per distinct ``NA`` pattern, its id the digest of the genes it assessed; the
    declaration counts the ``0`` cells (dropped, digested in ``CellDigest``'s order) and the ``NA``
    cells (empty)."""
    c = corpus()
    data = c.tsv([c.HEADER, ("TP53", "0", "2", "NA"), ("EGFR", "-2", "-1.5", "0")])
    found = read(data)
    assert found.calls == (("S2", "TP53", "2"), ("S1", "EGFR", "-2"), ("S2", "EGFR", "-1.5"))
    both = cell_digest([("TP53",), ("EGFR",)])
    egfr = cell_digest([("EGFR",)])
    assert set(found.assignment) == {("S1", both), ("S2", both), ("S3", egfr)}
    assert set(found.groups) == {(both, "TP53"), (both, "EGFR"), (egfr, "EGFR")}
    assert found.dropped == 2
    assert found.digest == cell_digest([("S1", "TP53"), ("S3", "EGFR")])
    assert found.empty == 1
    assert found.halves == 1
    assert found.cells == 3 * 3 + 2 * 3 + 2 * 3 + 2 + 1


def test_the_notes_count_every_half_and_name_the_unread_columns(tmp_path: Path) -> None:
    """Review r1 R29 and R45: the ``-1.5`` note counts cells, not rows (a row holding two counts
    two); the unread note names the gene columns the file holds among ``Cytoband`` and
    ``Entrez_Gene_Id``, in that order, and is absent when it holds neither."""
    c = corpus()
    cases = (
        ("plain", [c.HEADER, ("TP53", "-1.5", "-1.5", "NA"), ("EGFR", "-1.5", "0", "0")], 3, []),
        ("entrez", [c.HEADER_E, ("TP53", "7157", "-1.5", "-1.5", "NA")], 2, ["Entrez_Gene_Id"]),
        ("cytoband", [c.HEADER_C, c.C1, c.C2], 1, ["Cytoband", "Entrez_Gene_Id"]),
    )
    for name, rows, halves, unread in cases:
        assert read(c.tsv(rows)).halves == halves, name
        result = _import(_study_dir(tmp_path / name, c.data(c.tsv(rows))), 10**6)
        found = {
            (note.subject, note.count): [getattr(segment, "data", None) for segment in note.message]
            for note in result.notes
            if note.subject is not None and note.subject.startswith("cna_calls")
        }
        assert found[("cna_calls.cna", halves)] == [None, "cna", None], name
        columns = [
            [token for token in said if token is not None]
            for (subject, _), said in found.items()
            if subject == "cna_calls"
        ]
        if unread:
            assert columns == [["data_cna.txt", *unread]], name
        else:
            assert columns == [], name


def test_an_all_na_sample_has_no_group_and_an_empty_group_is_refused_by_the_gate(
    world: Any, registry: PackRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r1 minor 8: a sample whose every cell is ``NA`` gets no assignment (it is not
    covered, so it answers UNKNOWN); a group of no gene, were the pack to make one, is refused by
    the gate (``COVERAGE_UNKNOWN``), so the rule is load-bearing; a header-only matrix gives empty
    tables, ``dropped`` 0 and the digest of no cell, and publishes."""
    c = corpus()
    data = c.tsv([c.HEADER, ("TP53", "NA", "2", "0"), ("EGFR", "NA", "0", "0")])
    found = read(data)
    assert {sample for sample, _ in found.assignment} == {"S2", "S3"}
    world.publish("na", write(world, "na", c.data(data)), registry, pack="onco")
    empty = read(c.tsv([c.HEADER]))
    assert (empty.calls, empty.assignment, empty.groups) == ((), (), ())
    assert (empty.dropped, empty.empty, empty.digest) == (0, 0, cell_digest([]))
    world.publish("empty", write(world, "empty", c.data(c.tsv([c.HEADER]))), registry, pack="onco")
    module = cna()
    original = module._Coverage.add

    def add(self: Any, sample: int, column: bytes) -> None:
        if column.count(78) == self.genes:
            self.of.append(b"empty")
            self.assigned += 1
            self.first.setdefault(b"empty", sample)
            return
        original(self, sample, column)

    monkeypatch.setattr(module._Coverage, "add", add)
    with pytest.raises(ImportRefused) as raised:
        world.publish("bad", write(world, "bad", c.data(data)), registry, pack="onco")
    assert "COVERAGE_UNKNOWN" in {str(r.code) for r in raised.value.refusals}


def test_the_digest_is_cell_digest_s_in_the_encoded_order() -> None:
    """``CellDigest`` is fed the dropped cells by sample then gene, each by UTF-8 length then
    bytes, never Python's order of the texts: over random matrices whose ids differ in length and
    script, the digest is ``cell_digest`` of the ``0`` cells."""
    rng = random.Random(3)
    ids = ("b", "ab", "S1", "\xe9", "e\u0301", "zz", "A", "\u4e00")
    for _ in range(60):
        samples = rng.sample(ids, rng.randint(1, len(ids)))
        genes = rng.sample(["g", "GG", "ab", "b", "\xdf", "\xe9", "x|y", "1"], rng.randint(0, 6))
        rows = [[rng.choice(("0", "0", "NA", "2", "-1.5")) for _ in samples] for _ in genes]
        data = corpus().tsv(
            [("Hugo_Symbol", *samples), *[(g, *r) for g, r in zip(genes, rows, strict=True)]]
        )
        found = cna().read(data, "m.txt", frozenset(ids), ImportLimits())
        zeros = [
            (sample, gene)
            for gene, row in zip(genes, rows, strict=True)
            for sample, value in zip(samples, row, strict=True)
            if value == "0"
        ]
        assert found.digest == cell_digest(zeros)
        assert found.dropped == len(zeros)


# --- The cell count, the core's (review r2 MJ3) ---------------------------------------------------


def test_the_running_count_is_the_core_s_and_refuses_at_its_first_overflow() -> None:
    """Stricter (D446): the matrix's cells counted as the core counts its tables (three a call,
    one a ``0`` or ``NA``, two an assignment or a group row, one row for a table with none), with
    the cells before it, at each row and, with the coverage, at the end; the first overflow is the
    pack's own refusal, never the core's; held here, since no corpus cell passes the default bound
    (``HELD`` of ``test_cna_corpus``)."""
    reason = "the study's files have more cells than an import may"
    c = corpus()
    data = c.tsv([c.HEADER, ("TP53", "0", "2", "NA"), ("EGFR", "-2", "-1.5", "0")])
    total = read(data).cells
    assert total == 3 * 3 + 2 + 1 + 2 * 3 + 2 * 3 == 24
    assert code(data, ImportLimits(import_cells=total)) == "ok"
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=total - 1))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" {reason}")]
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=total + 4), cells_before=5)
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" {reason}")]
    # the running count: 3 + 1 + 1 after row 2, and 3 + 3 + 1 more after row 3
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=11))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 3: {reason}")]
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=4))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 2: {reason}")]
    # review r2 R34: a count equal to the limit after row 2 passes it; the overflow is row 3's
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=5))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 3: {reason}")]
    header_only = c.tsv([c.HEADER])
    assert read(header_only).cells == 7
    assert code(header_only, ImportLimits(import_cells=7)) == "ok"
    with pytest.raises(Refused) as raised:
        read(header_only, ImportLimits(import_cells=6))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" {reason}")]


def _core_count(result: Any) -> int:
    """The least ``import_cells`` the core's ``_limits`` passes the result at, by bisection."""
    low, high = 1, 10**7
    while low < high:
        middle = (low + high) // 2
        try:
            core_checks._limits(result, ImportLimits(import_cells=middle))  # pyright: ignore[reportPrivateUsage]
        except Exception:  # the core's refusal, whatever its class
            low = middle + 1
        else:
            high = middle
    return low


def _study_dir(root: Path, files: Mapping[str, Any]) -> Path:
    directory = root / "study"
    directory.mkdir(parents=True)
    for name, content in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(content)
    return directory


def _import(directory: Path, cells: int) -> Any:
    from aibi.core.importers.confine import Confinement
    from aibi.core.schema.pack_api import ImportOptions

    onco = importlib.import_module("aibi.packs.onco")
    confinement = Confinement.of(directory.parent)
    options = ImportOptions(
        dataset="s",
        reader=confinement,
        limits=ImportLimits(import_cells=cells),
        at="2026-01-01T00:00:00Z",
    )
    return onco.PACK.importer.import_source(confinement.confine(directory), options)


def test_the_pack_s_count_is_the_core_s_over_generated_studies(tmp_path: Path) -> None:
    """Review r2 MJ3: over generated matrices (with the clinical files and the case lists before
    them), the least bound the pack accepts the study at is exactly the core's count of its result
    (``checks._limits``), and one below it is the pack's ``TOO_MANY_CELLS`` naming the CNA file,
    never the core's ``LIMIT_EXCEEDED``."""
    c = corpus()
    rng = random.Random(7)
    for index in range(25):
        samples = rng.sample(sorted(SAMPLES), rng.randint(1, 3))
        genes = [f"G{n}" for n in range(rng.randint(0, 5))]
        rows = [[rng.choice(("0", "NA", "2", "-1")) for _ in samples] for _ in genes]
        data = c.tsv(
            [("Hugo_Symbol", *samples), *[(g, *r) for g, r in zip(genes, rows, strict=True)]]
        )
        directory = _study_dir(tmp_path / str(index), c.data(data))
        least = _core_count(_import(directory, 10**7))
        _import(directory, least)
        with pytest.raises(Refused) as raised:
            _import(directory, least - 1)
        (refusal,) = raised.value.refusals
        assert str(refusal.code) == "onco.TOO_MANY_CELLS", index
        assert "data_cna.txt" in [getattr(s, "data", None) for s in refusal.message], index


def test_the_import_counts_the_maf_and_sv_cells_before_the_matrix_s(tmp_path: Path) -> None:
    """``cna.imported`` reads after the clinical files, the case lists, the MAF and the SV file,
    all counted: the least bound the pack accepts a study with a MAF and an SV file at is the
    core's count, and one below it is the pack's refusal naming the CNA file."""
    c = corpus()
    sv = _load("structural_corpus")
    full = c.BASE.study(
        meta_cna_DOT_txt=c.meta(),
        data_cna_DOT_txt=c.VALID,
        case_lists__cases_cna_DOT_txt=c.cna_list(),
        meta_sv_DOT_txt=sv.META.encode(),
        data_sv_DOT_txt=sv.VALID,
    )
    directory = _study_dir(tmp_path, full)
    result = _import(directory, 10**7)
    assert {"mutations", "structural_variants", "cna_calls"} <= set(result.layouts)
    least = _core_count(result)
    _import(directory, least)
    with pytest.raises(Refused) as raised:
        _import(directory, least - 1)
    (refusal,) = raised.value.refusals
    assert str(refusal.code) == "onco.TOO_MANY_CELLS"
    assert "data_cna.txt" in [getattr(s, "data", None) for s in refusal.message]


def test_memory_is_bounded_by_the_limit_not_by_the_file() -> None:
    """Review r2 MJ3: a hostile all-calls matrix is refused at the first row past the bound, so the
    reader's peak follows ``import_cells``, never the file: the same bytes under a small bound peak
    far below their read under a bound that admits them."""
    ids = [f"S{n}" for n in range(1, 1501)]
    samples = frozenset(ids)
    lines = ["\t".join(("Hugo_Symbol", *ids))]
    lines += ["\t".join((f"G{g}", *("2",) * len(ids))) for g in range(200)]
    data = ("\n".join(lines) + "\n").encode()

    def peak(cells: int) -> tuple[int, str]:
        tracemalloc.start()
        try:
            cna().read(data, "m.txt", samples, ImportLimits(import_cells=cells))
            found = "ok"
        except Refused as error:
            found = str(error.refusals[0].code)
        finally:
            _, top = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        return top, found

    small, refused = peak(30_000)
    large, accepted = peak(10**7)
    assert refused == "onco.TOO_MANY_CELLS"
    assert accepted == "ok"
    assert small < 4 * 2**20, small
    assert large > 8 * small, (small, large)


def test_a_matrix_wider_than_table_columns_is_read() -> None:
    """The header's width is bounded by the gene columns and the sample file's samples, never by
    ``table_columns`` (which bounds a laid-out table's columns, three here): 5,000 samples read;
    one name more than the gene columns and the samples is ``TOO_MANY_COLUMNS``, before a split."""
    ids = [f"S{n}" for n in range(5_000)]
    data = "\t".join(("Hugo_Symbol", *ids)) + "\n" + "\t".join(("TP53", *("0",) * 5_000)) + "\n"
    found = cna().read(data.encode(), "m.txt", frozenset(ids), ImportLimits())
    assert found.dropped == 5_000
    assert ImportLimits().table_columns < 5_000
    wide = ("Hugo_Symbol" + "\t" * 4 + "\n").encode()
    with pytest.raises(Refused) as raised:
        cna().read(wide, "m.txt", frozenset({"S1"}), ImportLimits())
    assert str(raised.value.refusals[0].code) == "onco.TOO_MANY_COLUMNS"


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
LF and CR (no cell holds them) and the six the pack refuses as control characters (D445, D446)."""


def test_the_padding_is_every_character_strip_removes_that_a_cell_holds() -> None:
    control = {chr(p) for p in (*range(0x20), 0x7F)} - {"\t", "\n", "\r"}
    stripped = {chr(p) for p in range(sys.maxunicode + 1) if chr(p).strip() == ""}
    assert len(stripped) == 29
    assert set(PADDING) == stripped - control - {"\t", "\n", "\r"}
    assert len(PADDING) == 20


@pytest.mark.skipif(
    unicodedata.unidata_version != "15.0.0",
    reason="the reference's cells were recorded under Unicode 15.0.0; this Python's differ",
)
def test_the_oracle_s_unicode_is_the_reference_s() -> None:
    """Review r2 MJ1 (c): ``str.upper()`` and ``str.strip()`` follow the Python's Unicode tables;
    the reference's cells were recorded under Unicode 15.0.0 (its Python 3.12.3), and this Python
    has the same tables. Under another Python's tables the test is skipped, as ``test_visible``'s
    are (review r1 m6): the pinned Python (``server/.python-version``) runs it."""
    assert unicodedata.unidata_version == oracle().UNICODE == "15.0.0"


def reading(header: Sequence[str]) -> Any:
    found = cna()._Reading("cna.txt", SAMPLES, ImportLimits(), 0)
    assert found.header(1, "\t".join(header), True) is None
    return found


def item_of(failure: Any) -> str | None:
    """The oracle's item a reader's failure is, by its code and reason."""
    if failure is None:
        return None
    _, code_, reason = failure
    matches = [name for name, said in oracle().REASONS.items() if said == f"{code_}|{reason}"]
    assert len(matches) == 1, failure
    return matches[0]


TOKENS = (*corpus().TOKENS[:7], "na", "Na", "N/A", "", "2.0", "+2", "-0", "1e0", "\uff12", "x")


def padded(bases: Sequence[str]) -> list[str]:
    out: list[str] = []
    for pad in PADDING:
        out += [pad, pad + pad]
        for base in bases:
            out += [pad + base, base + pad, pad + base + pad]
    return out


ROW_CLASSES: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "plain": (corpus().HEADER, ("TP53",)),
    "entrez": (corpus().HEADER_E, ("TP53", "7157")),
    "skipped": (corpus().HEADER_E, ("TP53", "abc")),
    "nonpositive": (corpus().HEADER_E, ("TP53", "0")),
    "alone": (corpus().HEADER_E, ("", "7157")),
    "padded": (corpus().HEADER, (" TP53",)),
}
"""Each row class: a header and the row's gene fields. ``skipped`` is a row the reference does
not load (its cells never checked), ``nonpositive`` one it refuses, ``alone`` one named by its
Entrez id."""


def test_every_cell_item_follows_its_predicate_on_every_token_and_padding() -> None:
    """The six values, ``NA`` and its spellings, and tokens the reference refuses, each as written
    and padded by each of the 20 characters, in each cell position, on each row class (skipped or
    not), through ``_Reading.checked`` and D446's predicate directly: the same item, or none."""
    tokens = [*TOKENS, *padded(TOKENS)]
    checked = 0
    for name, (header, genes) in ROW_CLASSES.items():
        for token, at in itertools.product(tokens, range(3)):
            cells = ["0", "2", "NA"]
            cells[at] = token
            fields = (*genes, *cells)
            found = item_of(reading(header).checked(fields))
            assert found == oracle().row_item(header, fields), (name, token, at)
            checked += 1
    assert checked > 10_000


SYMBOL_DOMAIN = (
    "TP53",
    "tp53",
    "TP53|x",
    "TP53|",
    "TP53|x|y",
    "|x",
    "|y",
    "|",
    "\xdf",
    "SS",
    "\ufb01",
    "FI",
    "\u0131",
    "I",
    "TP53 |x",
    unicodedata.normalize("NFC", "\xc9X"),
    unicodedata.normalize("NFD", "\xc9X"),
    "P53",
    "7157",
    "",
    " TP53",
    " ",
    "\u3000",
    "\xa0",
)
ENTREZ_DOMAIN = ("7157", "07157", "+7157", "", "999", "abc", " 7157", "0", " ", "\xa0", "\u3000")
"""Review r1 R24: each domain holds whitespace alone (a space, a no-break space, an ideographic
space), which ``str.strip()`` empties: a symbol or an Entrez id of whitespace alone is a missing
key to the reference and padded to the pack."""


def test_every_pair_of_rows_follows_the_one_predicate_over_both_keys() -> None:
    """Review r2 MJ1 (c): every ordered pair of rows over a symbol by Entrez domain holding the
    live duplicates (sharp s and ``SS``, the ``fi`` ligature and ``FI``, dotless i and ``I``,
    ``TP53|`` and ``TP53``, ``|x`` and ``|y``) and non-duplicates (``TP53 |x`` and ``TP53``, NFC
    and NFD), with and without an Entrez column: the second row, after a first the file kept, is
    refused by the item D446's predicate gives (``cna_oracle.collision`` within ``row_item``),
    never by an exception."""
    pairs = 0
    rows: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
        (corpus().HEADER, (symbol,)) for symbol in SYMBOL_DOMAIN
    ]
    rows += [
        (corpus().HEADER_E, (symbol, entrez))
        for symbol, entrez in itertools.product(SYMBOL_DOMAIN, ENTREZ_DOMAIN)
    ]
    for (header, first), (other, second) in itertools.product(rows, repeat=2):
        if header != other:
            continue
        found = reading(header)
        one = (*first, "0", "2", "NA")
        if found.checked(one) is not None:
            continue
        two = (*second, "2", "0", "0")
        kept = [(first[0], first[1] if len(first) > 1 else None)]
        assert item_of(found.checked(two)) == oracle().row_item(header, two, kept), (first, second)
        pairs += 1
    assert pairs > 1_000


def outcome_of(data: bytes) -> str:
    """The reader's outcome of a matrix, ``ok`` or ``onco.<CODE>|<reason>``, as the judge's."""
    try:
        read(data)
    except Refused as error:
        refusal = error.refusals[0]
        return f"{refusal.code}|{_load('cna_checks').reason_of(refusal.message)}"
    return "ok"


def predicted(data: bytes) -> str:
    found = oracle().read_item(data, SAMPLES)
    return "ok" if found is None else oracle().said(found[0], *found[1])


NAME_MAKERS: tuple[tuple[str, Any], ...] = (
    ("exact", lambda name: name),
    *(
        (f"{where}_{ord(pad):04x}", corpus()._padding(pad, where))
        for pad in (*PADDING, *corpus().NON_STRIPPED, "\t")
        for where in ("pre", "post")
    ),
    ("lower", str.lower),
    ("upper", str.upper),
    ("swapcase", str.swapcase),
    ("title", str.title),
    ("casefold", str.casefold),
    ("snake_upper", lambda name: name.replace("_", "").upper()),
)
"""Review r2 MJ-B: a gene column's name as written and near it: padded before or after by each of
the 20 characters ``str.strip()`` removes that a cell holds, by the three it keeps (zero width
space, byte order mark, word joiner) and by a tab (an empty name beside it), and in five other
cases."""


def test_every_header_name_follows_its_predicate_at_every_position() -> None:
    """Review r2 MJ-B: each gene column's name (``Cytoband``, ``Hugo_Symbol``, ``Entrez_Gene_Id``)
    made near by each of ``NAME_MAKERS``, at each position (first, after a valid gene column,
    among the samples), through the reader and D446's predicate: the same outcome, item and
    reason. A prefix name compared after a strip or a case fold fails here."""
    checked = 0
    for name in ("Cytoband", "Hugo_Symbol", "Entrez_Gene_Id"):
        for (label, made), position in itertools.product(NAME_MAKERS, corpus().NAME_POSITIONS):
            header = corpus().name_header(name, made(name), position)
            row = corpus().name_row(header)
            data = corpus().tsv([header, row])
            assert outcome_of(data) == predicted(data), (name, label, position)
            checked += 1
    assert checked == 3 * len(NAME_MAKERS) * 3


COMPARED_SAMPLES = ("S1", "K1", "ab_1")
"""Sample ids whose spellings the brute force runs: a plain one, one a Kelvin sign spells under
NFC and NFKC, one in lower case with an underscore."""


def outcome_with(data: bytes, samples: frozenset[str]) -> str:
    """The reader's outcome over ``samples``, as ``outcome_of``."""
    try:
        cna().read(data, "cna.txt", samples, ImportLimits())
    except Refused as error:
        refusal = error.refusals[0]
        return f"{refusal.code}|{_load('cna_checks').reason_of(refusal.message)}"
    return "ok"


def predicted_with(data: bytes, samples: frozenset[str]) -> str:
    found = oracle().read_item(data, samples)
    return "ok" if found is None else oracle().said(found[0], *found[1])


def test_every_compared_sample_id_follows_its_predicate() -> None:
    """Review r3 MJ-2: each name the reader compares after reading it, spelt near
    (``cna_corpus.spellings``: the 20 characters ``str.strip()`` removes and the three it keeps,
    before or after; NFC, NFD, a Kelvin sign, full-width; other cases), through the reader and the
    predicates: a header's sample id against the sample file's (unknown, or a sample); and the
    duplicate-column set, a header holding an id and its spelling, both samples. A comparison
    after a normalisation, a strip or a case fold fails here."""
    checked = 0
    for base in COMPARED_SAMPLES:
        for label, spelled in corpus().spellings(base):
            for samples in (frozenset({base}), frozenset({base, spelled})):
                for header in (("Hugo_Symbol", spelled), ("Hugo_Symbol", base, spelled)):
                    if label == "exact" and len(header) == 3:
                        continue
                    row = ("TP53", *("2",) * (len(header) - 1))
                    data = corpus().tsv([header, row])
                    found = outcome_with(data, samples)
                    assert found == predicted_with(data, samples), (base, label, header, samples)
                    checked += 1
    assert checked > 400


def test_the_collision_predicate_holds_the_live_cells() -> None:
    """The reference's live outcomes (``dup:*`` cells), as the predicate states them: duplicates
    under its key where ``upper()`` is not one to one and at the first ``|``; none where a space
    precedes the bar, for NFC against NFD, or for ``07157`` against ``7157``; one symbol with two
    Entrez ids repeats aibi's key (the symbol as written)."""
    o = oracle()
    for one, two in (("\xdf", "SS"), ("\ufb01", "FI"), ("\u0131", "I"), ("TP53|", "TP53")):
        assert o.collision((one, None), (two, None)) == "key_repeat", (one, two)
    assert o.collision(("|x", None), ("|y", None)) == "key_repeat"
    assert o.collision(("TP53|x|y", None), ("TP53", None)) == "key_repeat"
    assert o.collision(("TP53 |x", None), ("TP53", None)) is None
    nfc = unicodedata.normalize("NFC", "\xc9X")
    assert o.collision((nfc, None), (unicodedata.normalize("NFD", nfc), None)) is None
    assert o.collision(("TP53", "7157"), ("TP53", "999")) == "symbol_repeat"
    assert o.collision(("TP53", "7157"), ("P53", "07157")) is None
    assert o.collision(("TP53", "abc"), ("TP53x", "abc")) is None
    reference = json_reference()
    for cell in (
        "dup:sharp_s",
        "dup:ligature",
        "dup:dotless_i",
        "dup:bars",
        "dup:trailing_bar",
        "dup:two_bars",
    ):
        assert reference[cell]["rc"] == 3, cell
    for cell in ("dup:space_before_bar", "dup:nfc_nfd", "dup:leading_zero"):
        assert reference[cell]["errors"] == [], cell


@functools.cache
def json_reference() -> Mapping[str, Any]:
    import json

    loaded: Mapping[str, Any] = json.loads((HERE / "cna_reference.json").read_text())
    return loaded


# --- Totality -------------------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=400))
def test_the_reader_is_total_over_any_bytes(data: bytes) -> None:
    with contextlib.suppress(Refused):
        read(data)


PIECES = ("\t", "\n", "\r", "#", " ", "0", "2", "-1.5", "NA", "S1", "S2", "S9", "TP53", "|")
PIECES += (
    "\ufeff",
    "\xa0",
    "Hugo_Symbol",
    "Entrez_Gene_Id",
    "Cytoband",
    "7157",
    "abc",
    "x",
    "\x00",
)
ALPHABET = st.sampled_from(PIECES)


@settings(max_examples=300, deadline=None)
@given(st.lists(ALPHABET, max_size=40))
def test_the_reader_is_total_over_structured_text(pieces: list[str]) -> None:
    data = corpus().VALID + "".join(pieces).encode()
    with contextlib.suppress(Refused):
        read(data)


ATOMS = ("\t", "\n", "\r", "#", " ", "\ufeff", "\xa0", "\x85", "\ufffe", "\u2028", "\x00")
ATOMS += ("NA", "0", " 0", "-1.5", "S1", "S9", "TP53", "|", "7157", "abc", "x")
RAW = (b"\xed\xa0\x80", b"\xff", b"\xc0\xaf", b"\xef\xbb\xbf")


@pytest.mark.parametrize("seed", range(2))
def test_a_structured_fuzz_through_the_publish_is_total(
    seed: int, world: Any, registry: PackRegistry
) -> None:
    """The matrix mutated by up to four insertions or replacements of the tokens the reader splits
    or judges, published through the store and its gate: every outcome is ``ok`` or one ``onco.``
    refusal, never a raw exception, ``PACK_FAILED`` or a gate refusal. 12 cases a seed, seeded."""
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
            assert len(codes) == 1, (data, codes)
            assert all(code_.startswith("onco.") for code_ in codes), (data, codes)


def test_a_refusal_names_no_value() -> None:
    """No refusal's text holds a value the matrix holds: a symbol, a sample id or a cell is named
    by its place alone (D400, D446)."""
    c = corpus()
    secret = "SECRETGENE"
    for data in (
        c.tsv([c.HEADER, (secret, "x", "0", "0")]),
        c.tsv([c.HEADER, (" " + secret, "0", "0", "0")]),
        c.tsv([c.HEADER, (secret, "0", "0", "0"), (secret, "0", "0", "0")]),
        c.tsv([("Hugo_Symbol", "S1", secret), (secret, "0", "0")]),
        c.tsv([c.HEADER, (secret, "0", "0", " 2" + secret)]),
    ):
        with pytest.raises(Refused) as raised:
            read(data)
        assert secret not in str(texts(raised.value)), data


# --- The one-file rule ---------------------------------------------------------------------------


def test_a_never_imported_meta_naming_the_matrix_is_refused() -> None:
    """Review r1 minor 9 (live ``meta:log2_same_file``: the reference reads a DISCRETE and a LOG2
    meta naming one file): the CNA file joins Reads(S), so a LOG2 meta naming it, exactly or under
    folding, is refused by phase 9's rule (stricter, an existing item)."""
    paths = _load("paths_oracle")
    study = importlib.import_module("aibi.packs.onco.study")
    for name in ("data_cna.txt", "DATA_CNA.txt", "data_CNA.TXT"):
        files = corpus().study(meta_log2_DOT_txt=corpus().LOG2.format(name).encode())
        entries = {
            path: ("file", content)
            for path, content in files.items()
            if "/" not in path and isinstance(content, bytes)
        }
        _, refusal = paths.discover_listing(study, entries)
        assert refusal is not None, name
        said = paths.said(refusal.message)
        assert said == f"\xabmeta_log2.txt\xbb has a data_filename that {paths.READS}", name


# --- What the published table answers, erasure, a re-import ---------------------------------------


def test_what_the_published_table_answers(world: Any, registry: PackRegistry) -> None:
    """``exists`` of a call (gene TP53, level 2) over the samples, by the reference evaluator: TRUE
    where the matrix calls it, FALSE where it holds ``0`` (assessed, neutral, through the grouped
    coverage), UNKNOWN where it holds ``NA`` or the sample is in no group (D401's answers)."""
    from aibi.core.engine.evaluate import evaluate
    from aibi.core.engine.resolve import resolve
    from aibi.core.schema.loading import load_document

    c = corpus()
    data = c.tsv([c.HEADER, ("TP53", "0", "2", "NA"), ("EGFR", "NA", "0", "2")])
    published = world.publish("q", write(world, "q", c.data(data)), registry, pack="onco")
    release = world.store.load(published.manifest)

    def answers(*where: Mapping[str, Any]) -> dict[str, str]:
        clause = {"kind": "exists", "table": "cna_calls", "where": list(where)}
        written = {
            "aibi": "1",
            "dataset": "q",
            "unit": "samples",
            "cohorts": {"c": {"all": [clause]}},
        }
        import json

        loaded = load_document(json.dumps(written))
        assert loaded.document is not None
        resolved = resolve(loaded.document, {"q": release}, loaded.positions, registry=registry)
        assert resolved.refusals == []
        found = evaluate(resolved.cohorts["c"])
        units = release.rows("samples")
        return {
            str(units.cell(row, "sample_id").value): value.value.value
            for row, value in enumerate(found.values)
        }

    def value(column: str, *values: str) -> dict[str, Any]:
        return {"kind": "value", "column": column, "values": list(values)}

    tp53 = answers(value("cna_calls.gene", "TP53"), value("cna_calls.cna", "2"))
    assert tp53 == {"S1": "FALSE", "S2": "TRUE", "S3": "UNKNOWN"}
    egfr = answers(value("cna_calls.gene", "EGFR"), value("cna_calls.cna", "2"))
    assert egfr == {"S1": "UNKNOWN", "S2": "FALSE", "S3": "TRUE"}


def test_a_re_import_of_the_same_bytes_changes_nothing(world: Any, registry: PackRegistry) -> None:
    c = corpus()
    directory = write(world, "r", c.study())
    world.publish("r", directory, registry, pack="onco")
    with pytest.raises(ImportRefused) as raised:
        world.reimport("r", directory, registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["NO_CHANGE"]


def test_erasing_a_sample_with_cna_rows(world: Any, registry: PackRegistry) -> None:
    """Erasing S2, which has CNA calls and an assignment row, is refused while a live release holds
    it; a re-import that drops S2 from the samples but keeps it in the matrix is refused by the
    pack (``UNKNOWN_SAMPLE``); once the matrix and its list drop it too, the erasure goes through
    (D408: the assignment table's sample ids are a link into the samples)."""
    c = corpus()
    base = c.BASE
    world.publish("e", write(world, "e", c.study()), registry, pack="onco")
    with pytest.raises(StoreRefused) as blocked:
        erase(world.store, "e", "samples", ["S2"], "operator:Ada")
    assert str(blocked.value.refusal.code) == "ERASURE_BLOCKED"
    samples = base.SAMPLES.replace(b"P2\tS2\n", b"")
    patients = base.PATIENTS.replace(b"P2\tM\n", b"")
    lists = {
        "case_lists__cases_cna_DOT_txt": c.cna_list(("S1", "S3")),
    }
    kept = c.study(
        data_clinical_sample_DOT_txt=samples, data_clinical_patient_DOT_txt=patients, **lists
    )
    with pytest.raises(ImportRefused) as raised:
        world.reimport("e", write(world, "e", kept), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.UNKNOWN_SAMPLE"]
    clean = c.study(
        data_clinical_sample_DOT_txt=samples,
        data_clinical_patient_DOT_txt=patients,
        data_cna_DOT_txt=c.tsv(
            [("Hugo_Symbol", "S1", "S3"), ("TP53", "0", "NA"), ("EGFR", "-2", "0")]
        ),
        **lists,
    )
    world.reimport("e", write(world, "e", clean), registry, pack="onco")
    erased = erase(world.store, "e", "samples", ["S2"], "operator:Ada")
    assert erased.withdrawn
