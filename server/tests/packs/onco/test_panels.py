"""The gene panel matrix beyond the corpus (M4.2b-2a, D447): the brute forces of the row, header,
gate and dictionary items over their domains (each calling the reader's checks or model and D447's
predicate directly, never a whole-study import), the cell count against the core's, the order of
the reads, the bound no corpus cell reaches, totality, what is declared, what the published table
answers (TRUE or UNKNOWN, never FALSE), disclosure under *k*, a re-import, and erasure through a
sample with a matrix row.

The corpus (``test_panels_corpus.py``) holds each class against the reference's recorded outcomes
and the predicates; this module holds what needs other limits, a store or many tokens.
"""

import contextlib
import functools
import importlib
import importlib.util
import itertools
import json
import random
import shutil
import sys
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
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import PackRegistry, Refused
from aibi.core.store.erasure import erase
from aibi.core.store.store import StoreRefused

HERE = Path(__file__).resolve().parent


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_panels_unit", HERE / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def corpus() -> ModuleType:
    return _load("panels_corpus")


@functools.cache
def oracle() -> ModuleType:
    return _load("panels_oracle")


def panels() -> Any:
    return importlib.import_module("aibi.packs.onco.panels")


SAMPLES = frozenset({"S1", "S2", "S3"})
SEQUENCED = frozenset({"S1", "S2"})
KEYS = frozenset({"mutations", "cna", "structural_variants", "sample_id_x"})


def profiles(
    keys: frozenset[str] = KEYS, gene_panel: frozenset[str] = frozenset(), crashes: bool = False
) -> Any:
    return panels().Profiles(keys, gene_panel, crashes)


def read(
    data: bytes,
    limits: ImportLimits | None = None,
    cells_before: int = 0,
    found: Any = None,
    sequenced: frozenset[str] | None = SEQUENCED,
) -> Any:
    return panels().read(
        data,
        "data_gene_panel_matrix.txt",
        SAMPLES,
        limits or ImportLimits(),
        found=found if found is not None else profiles(),
        sequenced=sequenced,
        cells_before=cells_before,
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


def with_samples(*ids: str) -> dict[str, bytes]:
    """The study's sample and patient files and ``_sequenced`` list with ``ids`` added, each a
    sample of its own patient."""
    base = corpus().BASE
    samples = base.SAMPLES + "".join(f"Q{s}\t{s}\n" for s in ids).encode()
    patients = base.PATIENTS + "".join(f"Q{s}\tF\n" for s in ids).encode()
    listed = base.case_list("sequenced", ["S1", "S2", "S3", *ids])
    return {
        "data_clinical_sample_DOT_txt": samples,
        "data_clinical_patient_DOT_txt": patients,
        "case_lists__cases_sequenced_DOT_txt": listed,
    }


# --- What is declared -----------------------------------------------------------------------------


def test_the_table_its_columns_and_relationship(world: Any, registry: PackRegistry) -> None:
    """D447: ``gene_panel_matrix`` a measurement table (never an entity, which erasure would not
    descend into), keyed by its ``SAMPLE_ID`` column, an identifier; every other column text, a
    category where D228 makes one, with ``NA`` its one missing code, UNKNOWN; the relationship to
    the samples imported and one-to-one; no coverage declared, and no table of the matrix's is a
    coverage table."""
    c = corpus()
    published = world.publish("t", write(world, "t", c.study()), registry, pack="onco")
    found = {d.id: d for d in world.store.descriptors(published.manifest)}
    table = found["gene_panel_matrix"]
    assert isinstance(table, TableDescriptor)
    assert table.fields.role == "measurement"
    assert table.fields.primary_key == ["sample_id"]
    assert table.fields.maps_to is not None
    assert table.fields.maps_to.concept == "onco:gene_panel_matrix"
    assert table.fields.source is not None
    assert table.fields.source.kind == "file"
    assert table.fields.source.original_name == "data_gene_panel_matrix.txt"
    assert table.extensions["onco"] == {"meta": "GENE_PANEL_MATRIX:GENE_PANEL_MATRIX"}
    key = found["gene_panel_matrix.sample_id"]
    assert isinstance(key, ColumnDescriptor)
    assert key.fields.datatype == "string"
    assert key.fields.identifier is True
    assert not key.fields.missing_codes
    panel = found["gene_panel_matrix.mutations"]
    assert isinstance(panel, ColumnDescriptor)
    assert panel.fields.datatype in ("category", "string")
    codes = panel.fields.missing_codes
    assert codes is not None
    assert {k: str(v) for k, v in codes.items()} == {"NA": "UNKNOWN"}
    relationship = found["rel:gene_panel_matrix.sample_id"]
    assert isinstance(relationship, RelationshipDescriptor)
    assert relationship.fields.parent_table == "samples"
    assert relationship.fields.cardinality == "one-to-one"
    for field in ("child_table", "child_columns", "parent_table", "parent_columns", "cardinality"):
        assert relationship.curation[f"/fields/{field}"].status == "imported", field
    assert not any(d.kind == "coverage" and "gene_panel" in d.id for d in found.values())
    tables = [d for d in found.values() if isinstance(d, TableDescriptor)]
    assert all(t.fields.role != "coverage" for t in tables if "panel" in t.id)


def test_d228_makes_a_panel_column_a_category_from_its_present_cells() -> None:
    """D228 over the present cells (``NA`` is missing, not a value): one panel on two samples is a
    category; two panels on two samples are not; a column of ``NA`` alone is text."""
    c = corpus()
    one = read(c.tsv([c.HEADER, ("S1", "P"), ("S2", "P"), ("S3", "NA")]))
    two = read(c.tsv([c.HEADER, ("S1", "P"), ("S2", "Q"), ("S3", "NA")]))
    none = read(c.tsv([c.HEADER, ("S1", "NA"), ("S3", "NA")]))
    assert [col.category for col in one.columns] == [False, True]
    assert [col.category for col in two.columns] == [False, False]
    assert [col.category for col in none.columns] == [False, False]


def test_a_gene_panel_key_is_noted_on_every_meta_and_never_read(
    world: Any, registry: PackRegistry
) -> None:
    """D447 (the owner's D10): a ``gene_panel`` key on the MAF's meta, on the matrix meta and on a
    meta of a type the pack never imports is accepted and noted, one note a meta, naming it."""
    c = corpus()
    expression = (
        "cancer_study_identifier: demo_study\ngenetic_alteration_type: MRNA_EXPRESSION\n"
        "datatype: CONTINUOUS\nstable_id: mrna\nshow_profile_in_analysis_tab: false\n"
        "profile_name: p\nprofile_description: d\ndata_filename: d_mrna.txt\ngene_panel: P\n"
    )
    files = c.study(
        meta_mutations_DOT_txt=(c.BASE.META + "gene_panel: IMPACT468\n").encode(),
        meta_gene_panel_matrix_DOT_txt=c.meta(extra="gene_panel: P\n"),
        meta_expression_DOT_txt=expression.encode(),
        d_mrna_DOT_txt=b"x\n",
    )
    world.publish("n", write(world, "n", files), registry, pack="onco")
    result = _import(_study_dir(world.root / "n_import", files), 10**7)
    said = [
        "".join(getattr(s, "text", "") or getattr(s, "data", "") for s in note.message)
        for note in result.notes
        if note.message and "gene_panel" in "".join(getattr(s, "text", "") for s in note.message)
    ]
    assert sorted(said) == sorted(
        f"{name} holds a gene_panel key, which the pack does not read (D447)"
        for name in ("meta_mutations.txt", "meta_gene_panel_matrix.txt", "meta_expression.txt")
    )


# --- What the published table answers -------------------------------------------------------------


def test_the_matrix_answers_true_or_unknown_never_false(world: Any, registry: PackRegistry) -> None:
    """D447: with no coverage, nothing closes a sample's absent matrix row and nothing makes a
    panel the sample was not given false: ``exists`` of a matrix row naming a panel is TRUE where
    the sample's row names it and UNKNOWN everywhere else (another panel, ``NA``, no row), and its
    negation is FALSE or UNKNOWN; ``NA`` reads as UNKNOWN, never as the value ``NA``. Never a
    FALSE ``exists``, over every panel the matrix holds and one it does not."""
    from aibi.core.engine.evaluate import evaluate
    from aibi.core.engine.resolve import resolve
    from aibi.core.schema.loading import load_document

    c = corpus()
    data = c.tsv([c.HEADER, ("S1", "IMPACT505"), ("S2", "NA"), ("Q4", "IMPACT341")])
    files = c.study(data_gene_panel_matrix_DOT_txt=data, **with_samples("Q4", "Q5"))
    published = world.publish("q", write(world, "q", files), registry, pack="onco")
    release = world.store.load(published.manifest)

    def answers(clause: Mapping[str, Any]) -> dict[str, str]:
        written = {
            "aibi": "1",
            "dataset": "q",
            "unit": "samples",
            "cohorts": {"c": {"all": [clause]}},
        }
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

    def exists(*values: str) -> dict[str, Any]:
        where = [{"kind": "value", "column": "gene_panel_matrix.mutations", "values": list(values)}]
        return {"kind": "exists", "table": "gene_panel_matrix", "where": where}

    assert answers(exists("IMPACT505")) == {
        "S1": "TRUE",
        "S2": "UNKNOWN",
        "S3": "UNKNOWN",
        "Q4": "UNKNOWN",
        "Q5": "UNKNOWN",
    }
    assert answers(exists("IMPACT341")) == {
        "S1": "UNKNOWN",
        "S2": "UNKNOWN",
        "S3": "UNKNOWN",
        "Q4": "TRUE",
        "Q5": "UNKNOWN",
    }
    assert answers({"not": exists("IMPACT505")})["S1"] == "FALSE"
    for values in (("IMPACT505",), ("IMPACT341",), ("NA",), ("IMPACT-HEME-400",)):
        found = answers(exists(*values))
        assert set(found.values()) <= {"TRUE", "UNKNOWN"}, values
        assert "TRUE" not in answers({"not": exists(*values)}).values(), values
    assert set(answers(exists("NA")).values()) == {"UNKNOWN"}
    every = {"kind": "exists", "table": "gene_panel_matrix", "where": []}
    assert answers(every) == {
        "S1": "TRUE",
        "S2": "TRUE",
        "S3": "UNKNOWN",
        "Q4": "TRUE",
        "Q5": "UNKNOWN",
    }


def test_disclosure_of_the_matrix_under_k(world: Any, registry: PackRegistry) -> None:
    """Plan r3 m2: the matrix's memberships are withheld under any *k* (its path from the samples
    is one bounded step down, D383); its catalogue statistics follow D271: under a floor of 3 a
    panel on 4 samples is shown and two panels on 2 samples each are pooled into one row of 4; the
    real study's shape, one panel on 2 samples (IMPACT-HEME-400) beside one on 4, leaves a pooled
    row of 2, so the distribution is suppressed; and two sources that differ only in their rows
    give the same descriptor text."""
    from aibi.core.catalog.service import Catalog
    from aibi.core.engine.graph import Step
    from aibi.core.engine.resolve import bounded_rows, open_path
    from aibi.core.schema.catalog import DescribeColumn

    c = corpus()
    extra = ("Q4", "Q5", "Q6", "Q7", "Q8")
    big = [("S1", "IMPACT505"), ("S2", "IMPACT505"), ("S3", "IMPACT505"), ("Q4", "IMPACT505")]
    small = [("Q5", "IMPACT-HEME-400"), ("Q6", "IMPACT-HEME-400")]
    rows = [*big, *small, ("Q7", "IMPACT341"), ("Q8", "IMPACT341")]
    files = c.study(data_gene_panel_matrix_DOT_txt=c.tsv([c.HEADER, *rows]), **with_samples(*extra))
    published = world.publish("k", write(world, "k", files), registry, pack="onco")
    release = world.store.load(published.manifest)
    path = (Step("rel:gene_panel_matrix.sample_id", "down"),)
    assert not open_path(release, path)
    assert bounded_rows(release, path, "category")

    def distribution(dataset: str, floor: int | None) -> dict[str, Any]:
        catalog = Catalog(world.store, registry=registry, floor=floor)
        found = catalog.describe_column(
            DescribeColumn.model_validate(
                {"dataset": dataset, "column": "gene_panel_matrix.mutations"}
            )
        )
        return found.model_dump(mode="json")["statistics"]["distribution"]

    pooled = json.dumps(distribution("k", 3))
    assert "IMPACT505" in pooled
    assert "IMPACT-HEME-400" not in pooled
    assert "IMPACT341" not in pooled
    assert '"pooled"' in pooled
    shape = c.study(
        data_gene_panel_matrix_DOT_txt=c.tsv([c.HEADER, *big, *small]), **with_samples(*extra)
    )
    world.publish("h", write(world, "h", shape), registry, pack="onco")
    assert distribution("h", 3) == {"kind": "none", "reason": "suppressed"}
    assert "IMPACT-HEME-400" in json.dumps(distribution("h", None))
    other = [("S1", "IMPACT341"), *rows[1:6], ("Q7", "IMPACT505"), ("Q8", "IMPACT-HEME-400")]
    files2 = c.study(
        data_gene_panel_matrix_DOT_txt=c.tsv([c.HEADER, *other]), **with_samples(*extra)
    )
    second = world.publish("k2", write(world, "k2", files2), registry, pack="onco")
    first = {d.id: d for d in world.store.descriptors(published.manifest)}
    then = {d.id: d for d in world.store.descriptors(second.manifest)}
    for name in ("gene_panel_matrix", "gene_panel_matrix.sample_id", "gene_panel_matrix.mutations"):
        one = first[name].model_dump(mode="json", exclude={"curation"})
        two = then[name].model_dump(mode="json", exclude={"curation"})
        assert one == two, name


# --- The count, the order of the reads and the bound ----------------------------------------------


def test_the_running_count_is_the_core_s_and_refuses_at_its_first_overflow() -> None:
    """Stricter (D447): the matrix's cells counted as the core counts its table (its columns times
    its rows, one row for a table with none), with the cells before it, at each row and at the end;
    the first overflow is the pack's own refusal, never the core's; held here, since no corpus cell
    passes the default bound (``HELD`` of ``test_panels_corpus``)."""
    reason = "the study's files have more cells than an import may"
    c = corpus()
    data = c.tsv([c.HEADER, ("S1", "P"), ("S2", "P"), ("S3", "NA")])
    total = read(data).cells
    assert total == 2 * 3 == 6
    assert code(data, ImportLimits(import_cells=total)) == "ok"
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=total - 1))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 4: {reason}")]
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=total + 4), cells_before=5)
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 4: {reason}")]
    assert code(data, ImportLimits(import_cells=total + 4), cells_before=4) == "ok"
    for bound, line in ((1, 2), (2, 3), (3, 3), (4, 4), (5, 4)):
        with pytest.raises(Refused) as raised:
            read(data, ImportLimits(import_cells=bound))
        assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line {line}: {reason}")], bound
    header_only = c.tsv([c.HEADER])
    assert read(header_only).cells == 2
    assert code(header_only, ImportLimits(import_cells=2)) == "ok"
    with pytest.raises(Refused) as raised:
        read(header_only, ImportLimits(import_cells=1))
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


def _refused_file(directory: Path, cells: int) -> str | None:
    with pytest.raises(Refused) as raised:
        _import(directory, cells)
    (refusal,) = raised.value.refusals
    assert str(refusal.code) == "onco.TOO_MANY_CELLS"
    return next((getattr(s, "data", None) for s in refusal.message if hasattr(s, "data")), None)


def test_the_pack_s_count_is_the_core_s_over_generated_studies(tmp_path: Path) -> None:
    """Plan r3 MJ-B: over generated matrices (with the clinical files, the case lists and the MAF
    around them), the least bound the pack accepts the study at is exactly the core's count of its
    result (``checks._limits``), and one below it is the pack's ``TOO_MANY_CELLS``, never the
    core's ``LIMIT_EXCEEDED``; a study without its MAF names the matrix."""
    c = corpus()
    rng = random.Random(11)
    for index in range(20):
        chosen = rng.sample(sorted(SAMPLES), rng.randint(0, 3))
        rows = [(s, rng.choice(("NA", "P", "Q"))) for s in chosen]
        no_maf = rng.random() < 0.5
        over: dict[str, Any] = {}
        if no_maf:
            over = {"meta_mutations_DOT_txt": None, "data_mutations_DOT_txt": None}
            header: Sequence[str] = ("SAMPLE_ID", "gpm")
            over["meta_gene_panel_matrix_DOT_txt"] = c.meta(extra="stable_id: gpm\n")
        else:
            header = c.HEADER
        files = c.study(data_gene_panel_matrix_DOT_txt=c.tsv([header, *rows]), **over)
        directory = _study_dir(tmp_path / str(index), files)
        least = _core_count(_import(directory, 10**7))
        _import(directory, least)
        file = _refused_file(directory, least - 1)
        if no_maf:
            assert file == "data_gene_panel_matrix.txt", index


def test_the_matrix_is_read_after_the_lists_and_counted_before_the_other_files(
    tmp_path: Path,
) -> None:
    """Plan r4 MJ-1, r5 n1: the matrix is read after ``NO_SEQUENCED_LIST`` and the case lists and
    before the MAF, and its cells are counted into the MAF's, the SV file's and the CNA matrix's
    reads: with every role read, the least bound is the core's count and one below it names the
    CNA file, read last; a bound that admits the cells before the matrix and the matrix's but not
    the MAF's names the MAF; one that admits only the cells before the matrix names the matrix."""
    c = corpus()
    sv = _load("structural_corpus")
    copies = _load("cna_corpus")
    full = c.study(
        meta_cna_DOT_txt=copies.meta(),
        data_cna_DOT_txt=copies.VALID,
        case_lists__cases_cna_DOT_txt=copies.cna_list(),
        meta_sv_DOT_txt=sv.META.encode(),
        data_sv_DOT_txt=sv.VALID,
    )
    directory = _study_dir(tmp_path / "full", full)
    result = _import(directory, 10**7)
    assert {"mutations", "structural_variants", "cna_calls", "gene_panel_matrix"} <= set(
        result.layouts
    )
    least = _core_count(result)
    _import(directory, least)
    assert _refused_file(directory, least - 1) == "data_cna.txt"
    matrix_only = c.study()
    directory = _study_dir(tmp_path / "m", matrix_only)
    least = _core_count(_import(directory, 10**7))
    maf_cells = len(c.BASE.COLUMNS) * 2
    assert _refused_file(directory, least - maf_cells) == "data_mutations.txt"
    assert _refused_file(directory, least - maf_cells - 1) == "data_gene_panel_matrix.txt"
    orders = corpus().CELLS
    outcomes = json.loads((HERE / "panels_outcomes.json").read_text())
    assert outcomes["seq:no_list_bad_matrix"].startswith("onco.NO_SEQUENCED_LIST|")
    assert outcomes["beside:bad_matrix_bad_maf"].startswith("onco.UNKNOWN_SAMPLE|the row's")
    assert outcomes["beside:bad_matrix_bad_cna"].startswith("onco.UNKNOWN_SAMPLE|the row's")
    assert outcomes["beside:bad_matrix_bad_sv"].startswith("onco.UNKNOWN_SAMPLE|the row's")
    assert outcomes["beside:bad_matrix_no_cna_list"].startswith("onco.UNKNOWN_SAMPLE|the row's")
    assert outcomes["beside:bad_matrix_bad_list"].startswith("onco.UNKNOWN_SAMPLE|lists an id")
    assert "seq:no_list_bad_matrix" in orders


# --- The brute forces -----------------------------------------------------------------------------

PADDING = tuple(
    chr(p)
    for p in range(sys.maxunicode + 1)
    if chr(p).strip() == "" and chr(p) not in "\t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85\u2028\u2029"
)
"""The characters ``str.strip()`` removes that a cell can hold and the pack refuses as no control
character or line separator: 17 of the 29."""


def test_the_padding_is_every_character_strip_removes_but_the_refused() -> None:
    stripped = {chr(p) for p in range(sys.maxunicode + 1) if chr(p).strip() == ""}
    assert len(stripped) == 29
    assert len(PADDING) == 17
    refused = panels().REFUSED
    assert all(
        refused.search(character) for character in stripped - set(PADDING) - {"\t", "\n", "\r"}
    )


@pytest.mark.skipif(
    unicodedata.unidata_version != "15.0.0",
    reason="the reference's cells were recorded under Unicode 15.0.0; this Python's differ",
)
def test_the_oracle_s_unicode_is_the_reference_s() -> None:
    """``str.strip()`` follows the Python's Unicode tables; the reference's cells were recorded
    under Unicode 15.0.0, and this Python has the same tables (skipped under another's)."""
    assert unicodedata.unidata_version == oracle().UNICODE == "15.0.0"


def item_of(failure: Any) -> str | None:
    """The oracle's item a reader's failure is, by its code and reason."""
    if failure is None:
        return None
    _, code_, reason = failure
    matches = [name for name, said in oracle().REASONS.items() if said == f"{code_}|{reason}"]
    assert len(matches) == 1, failure
    return matches[0]


ROW_TOKENS = ("IMPACT1", "NA", "na", "WGS", "", "x y", '"P"', "\ufeffP", "P\u200b", "N/A", "0")


def padded(bases: Sequence[str]) -> list[str]:
    out: list[str] = []
    for pad in PADDING:
        out += [pad, pad + pad]
        for base in bases:
            out += [pad + base, base + pad, pad + base + pad]
    return out


def test_every_row_item_follows_its_predicate_on_every_token_padding_and_position() -> None:
    """Each panel token and sample id, as written and padded by each of the 17 characters
    ``str.strip()`` removes, in each column, on a sample in and out of the ``_sequenced`` list,
    after rows that hold it or not, with and without the MAF's column, through ``_Reading.checked``
    and D447's predicate directly: the same item, or none."""
    reader = panels()._Reading
    o = oracle()
    headers = (
        ("SAMPLE_ID", "mutations"),
        ("SAMPLE_ID", "cna", "mutations"),
        ("SAMPLE_ID", "mutations", "cna"),
        ("SAMPLE_ID", "cna"),
    )
    tokens = [*ROW_TOKENS, *padded(ROW_TOKENS[:3])]
    samples = ["S1", "S3", "S9", "", " S1", "S1\xa0", "s1", '"S1"']
    checked = 0
    for header in headers:
        for sequenced in (SEQUENCED, None):
            reading = reader("m.txt", SAMPLES, profiles(), sequenced, ImportLimits(), 0)
            assert reading.header(1, "\t".join(header), True) is None
            for seen in ((), ("S1",)):
                reading.seen = set(seen)
                for sample in samples:
                    for token in tokens:
                        for at in range(1, len(header)):
                            fields = [sample, *(["P"] * (len(header) - 1))]
                            fields[at] = token
                            found = item_of(reading.checked(fields))
                            items = o.row_items(header, fields, SAMPLES, seen, sequenced)
                            assert found == (items[0] if items else None), (header, fields, seen)
                            checked += 1
    assert checked > 20_000


NAMES = ("SAMPLE_ID", "mutations", "cna", "Mutations", "sample_id", "a-b", "a_b", "1a", "c_1a")


def test_every_header_follows_its_predicate() -> None:
    """Every header of up to four names drawn from ``SAMPLE_ID``, profiles' stable ids, names that
    normalise onto them, names no meta holds and padded names, against dictionaries with and
    without them, with ``gene_panel`` on a profile, with and without the MAF: ``_Reading.header``
    and D447's predicate give the same item (the gate passed)."""
    o = oracle()
    reader = panels()._Reading
    domain = [*NAMES, "", "foo", " mutations", "mutations\xa0", "x" * 70, "x" * 64, "()", "c_3"]
    keys_of = (
        frozenset({"mutations", "cna"}),
        frozenset({"mutations", "cna", "Mutations", "sample_id", "a-b", "a_b", "1a", "c_1a"}),
        frozenset({"mutations", "x" * 70, "x" * 64, "()", "c_3", "foo"}),
    )
    checked = 0
    for keys in keys_of:
        for panel_keys in (frozenset(), frozenset({"cna"}), frozenset({"mutations"})):
            found = profiles(keys, panel_keys)
            model = o.Profiles(keys, panel_keys, False)
            for sequenced in (SEQUENCED, None):
                for size in (1, 2, 3):
                    for rest in itertools.product(domain, repeat=size):
                        for first in ("SAMPLE_ID", "mutations"):
                            names = (first, *rest)
                            line = "\t".join(names)
                            reading = reader("m.txt", SAMPLES, found, sequenced, ImportLimits(), 0)
                            refused = reading.header(1, line, True)
                            got = None
                            if refused is not None:
                                text = "".join(
                                    getattr(s, "text", "") for s in refused.refusals[0].message
                                )
                                reason = text.split(": ", 1)[1]
                                got = item_of(
                                    (0, str(refused.refusals[0].code).removeprefix("onco."), reason)
                                )
                            assert got == o.header_item(names, model, sequenced is not None), names
                            checked += 1
    assert checked > 10_000


def test_the_gate_window_is_the_reference_s() -> None:
    """Plan r4 m3, r5 n2: a one-column header is gated where neither it nor the five lines after it
    (``#`` lines, blank lines and lines split at ``\\r`` among them) holds a tab: a tab on each line
    from the first to the seventh after the header, after ``#`` and blank lines, with each line
    end, through the reader and the predicate."""
    o = oracle()
    fillers = ("S1", "#x", "", "S2", "#a\tb")
    checked = 0
    for after in range(1, 8):
        for filler in fillers:
            for end in ("\n", "\r", "\r\n"):
                lines = ["SAMPLE_ID", *([filler] * (after - 1)), "S9\tP", "S2"]
                data = end.join(lines).encode() + end.encode()
                gated = code(data) == "onco.MISSING_COLUMN"
                assert gated is o.gated(lines, 0), (after, filler, end)
                assert gated is (after > 5 and "\t" not in filler), (after, filler, end)
                checked += 1
    assert checked == 105


META_LINES = {
    "sid": "stable_id: foo\n",
    "sid_space": " stable_id: foo\n",
    "sid_trailing": "stable_id : foo\n",
    "gat_mutext": "genetic_alteration_type: MUTATION_EXTENDED\n",
    "gat_mutext_padded": "genetic_alteration_type:  MUTATION_EXTENDED \n",
    "gat_other": "genetic_alteration_type: X\n",
    "gat_space": " genetic_alteration_type: MUTATION_EXTENDED\n",
    "datatype": "datatype: MAF\n",
    "gene_panel": "gene_panel: P\n",
}
LATER_LINES = {
    "gene_panel_padded": " gene_panel: P\n",
    "swissprot_bogus": "swissprot_identifier: bogus\n",
    "swissprot_name": "swissprot_identifier: name\n",
    "genome_hg18": "reference_genome_id: hg18\n",
    "genome_hg38": "reference_genome_id: hg38\n",
    "blank_nbsp": "\xa0\n",
    "blank_vt": "\x0b\n",
    "blank_ideographic": "\u3000\n",
    "blank_tab": "\t\n",
}
"""Review r1 MAJOR-1: the lines of the typing rules beyond the mandatory keys and the study id
(the swissprot rule, the segment and GISTIC genome rule), a padded ``gene_panel`` key, and blank
lines of characters ``str.strip()`` removes; combined in pairs (the original lines in threes)."""
BASES = {
    "study": (
        "type_of_cancer: brca\ncancer_study_identifier: demo_study\nname: n\ndescription: d\n"
    ),
    "resource": (
        "cancer_study_identifier: demo_study\nresource_type: DEFINITION\ndata_filename: r\n"
    ),
    "other_study": (
        "type_of_cancer: brca\ncancer_study_identifier: other\nname: n\ndescription: d\n"
    ),
    "untyped": "cancer_study_identifier: demo_study\n",
    "uncalled": (
        "cancer_study_identifier: demo_study\ngenetic_alteration_type: MUTATION_UNCALLED\n"
        "datatype: MAF\nstable_id: mutations_uncalled\nprofile_name: p\nprofile_description: d\n"
        "data_filename: u\n"
    ),
    "seg": (
        "cancer_study_identifier: demo_study\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
        "datatype: SEG\nreference_genome_id: hg19\ndata_filename: s\ndescription: d\n"
    ),
    "gistic": (
        "cancer_study_identifier: demo_study\ngenetic_alteration_type: GISTIC_GENES_AMP\n"
        "datatype: Q-VALUE\nreference_genome_id: hg19\ndata_filename: g\n"
    ),
    "expression": (
        "cancer_study_identifier: demo_study\ngenetic_alteration_type: MRNA_EXPRESSION\n"
        "datatype: CONTINUOUS\nstable_id: mrna\nshow_profile_in_analysis_tab: false\n"
        "profile_name: p\nprofile_description: d\ndata_filename: e\n"
    ),
    "cancer_type": (
        "genetic_alteration_type: CANCER_TYPE\ndatatype: CANCER_TYPE\ndata_filename: c\n"
    ),
}


def test_the_dictionary_model_is_the_oracle_s_over_every_meta_variant() -> None:
    """Plan r5 MJ: ``panels.profiles``, the pack's model of V:3363-3373 over V:4740-4778 on the
    reference's reading of the metas, and the oracle's, written apart, agree on every variant of a
    meta (each base; each subset of up to three added lines, in each order; a byte order mark
    before the first line or not; a line without ``:`` or a blank line after the mark), beside a
    MAF meta, alone and as a pair sharing one ``stable_id``."""
    o = oracle()
    model = panels().profiles
    maf = corpus().BASE.META.encode()
    checked = 0
    lines = {**META_LINES, **LATER_LINES}
    keys = sorted(lines)
    for base_name, base in BASES.items():
        for size in range(4):
            for chosen in itertools.permutations(keys, size):
                if size == 3 and any(k in LATER_LINES for k in chosen):
                    continue
                body = base + "".join(lines[k] for k in chosen)
                for lead in (b"", b"\xef\xbb\xbf", b"\xef\xbb\xbf\n", b"\xef\xbb\xbfjunk\n"):
                    meta = lead + body.encode()
                    plain = (BASES["study"] + META_LINES["sid"]).encode()
                    for metas in (
                        {"meta_a.txt": meta, "meta_mutations.txt": maf},
                        {"meta_a.txt": meta, "meta_b.txt": body.encode()},
                        {"meta_a.txt": meta, "meta_b.txt": plain},
                        {"meta_a.txt": plain, "meta_b.txt": meta},
                    ):
                        mine = model(metas)
                        theirs = o.profiles(metas)
                        assert (mine.keys, mine.panels, mine.crashes) == (
                            theirs.keys,
                            theirs.panels,
                            theirs.crashes,
                        ), (base_name, chosen, lead, sorted(metas))
                        checked += 1
    assert checked > 50_000


def test_column_base_is_the_core_s() -> None:
    """``panels.column_base`` (and the oracle's, written apart) is the core's ``_base`` for a
    column, over names near every rule of SPEC 5.1 (empty, digits, cuts, compatibility forms)."""
    from aibi.core.schema import ids

    o = oracle()
    names = [
        "",
        "()",
        "1a",
        "a-b",
        "A B",
        "x" * 70,
        "_" * 70,
        "1" + "x" * 70,
        "\uff2e\uff21",
        "\u212a",
    ]
    names += ["sample_id", "SAMPLE_ID", "c_3", "x" * 63 + "_y", "\xe9", "a" * 64 + "_"]
    for name in names:
        for position in (1, 3, 12):
            core = ids._base(name, "column", position)  # pyright: ignore[reportPrivateUsage]
            assert panels().column_base(name, position) == core, (name, position)
            assert o.column_id_base(name, position) == core, (name, position)


def test_the_pack_takes_the_exact_sequenced_list(world: Any, registry: PackRegistry) -> None:
    """D447's exit (the reference takes the last list whose id ends in ``_sequenced``, in the
    order ``os.listdir`` gives, which depends on the file system; plan r3 m4): the pack holds a
    panel's sample to ``<study>_sequenced`` alone, whatever other list ends so, both ways."""
    c = corpus()
    other = c.BASE.case_list("other_sequenced", ["S1"])
    mine = c.BASE.case_list("sequenced", ["S1"])
    every = c.BASE.case_list("other_sequenced", ["S1", "S2", "S3"])
    accepted = c.study(case_lists__cases_x_DOT_txt=other)
    world.publish("a", write(world, "a", accepted), registry, pack="onco")
    refused = c.study(case_lists__cases_sequenced_DOT_txt=mine, case_lists__cases_x_DOT_txt=every)
    with pytest.raises(ImportRefused) as raised:
        world.publish("b", write(world, "b", refused), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.NOT_SEQUENCED"]


def test_the_six_sample_cna_shape_is_not_refused(world: Any, registry: PackRegistry) -> None:
    """Plan r2 (i): the matrix's ``cna`` and ``structural_variants`` cells are tied to no case
    list and no data file: the real study's shape (a ``cna`` panel on samples the ``_cna`` list
    and the CNA file leave out) is accepted."""
    c = corpus()
    copies = _load("cna_corpus")
    matrix = c.tsv(
        [("SAMPLE_ID", "mutations", "cna"), ("S1", "P", "P"), ("S2", "P", "P"), ("S3", "NA", "P")]
    )
    files = c.study(
        meta_cna_DOT_txt=copies.meta(),
        data_cna_DOT_txt=copies.tsv([("Hugo_Symbol", "S1"), ("TP53", "2")]),
        case_lists__cases_cna_DOT_txt=copies.cna_list(("S1",)),
        data_gene_panel_matrix_DOT_txt=matrix,
    )
    world.publish("s", write(world, "s", files), registry, pack="onco")


# --- Totality -------------------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=400))
def test_the_reader_is_total_over_any_bytes(data: bytes) -> None:
    with contextlib.suppress(Refused):
        read(data)


PIECES = ("\t", "\n", "\r", "#", " ", "NA", "S1", "S2", "S9", "P", "SAMPLE_ID", "mutations")
PIECES += ("\ufeff", "\xa0", "cna", "\x00", "\u2028", '"', "x")


@settings(max_examples=300, deadline=None)
@given(st.lists(st.sampled_from(PIECES), max_size=40), st.booleans())
def test_the_reader_is_total_over_structured_text(pieces: list[str], crashes: bool) -> None:
    data = corpus().VALID + "".join(pieces).encode()
    with contextlib.suppress(Refused):
        read(data, found=profiles(crashes=crashes))


ATOMS = ("\t", "\n", "\r", "#", " ", "\ufeff", "\xa0", "\x85", "\u2028", "\x00", "NA", "S1")
ATOMS += ("S9", "mutations", "SAMPLE_ID", "P", "x", '"')
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
    """No refusal's text holds a value the matrix holds: a sample id, a panel or a name is named
    by its place alone (D400, D447)."""
    c = corpus()
    secret = "SECRETPANEL"
    for data in (
        c.tsv([c.HEADER, (secret, "P")]),
        c.tsv([c.HEADER, ("S1", "P"), ("S1", secret)]),
        c.tsv([c.HEADER, ("S3", secret)]),
        c.tsv([c.HEADER, ("S1", " " + secret)]),
        c.tsv([("SAMPLE_ID", secret), ("S1", "P")]),
        c.tsv([(secret, "mutations"), ("S1", "P")]),
        c.tsv([c.HEADER, ("S1", secret + "\x01")]),
    ):
        with pytest.raises(Refused) as raised:
            read(data)
        assert secret not in str(texts(raised.value)), data


# --- Erasure and a re-import ----------------------------------------------------------------------


def test_a_re_import_of_the_same_bytes_changes_nothing(world: Any, registry: PackRegistry) -> None:
    c = corpus()
    directory = write(world, "r", c.study())
    world.publish("r", directory, registry, pack="onco")
    with pytest.raises(ImportRefused) as raised:
        world.reimport("r", directory, registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["NO_CHANGE"]


def test_erasing_a_sample_with_a_matrix_row(world: Any, registry: PackRegistry) -> None:
    """Plan r4 m2: the matrix is a measurement table, so erasure descends into it: erasing S2,
    which has a matrix row (and MAF rows), is refused while a live release holds it; a re-import
    that drops S2 from the samples but keeps its matrix row is refused by the pack
    (``UNKNOWN_SAMPLE``); once the matrix, the MAF and the lists drop it too, the erasure goes
    through."""
    c = corpus()
    base = c.BASE
    world.publish("e", write(world, "e", c.study()), registry, pack="onco")
    with pytest.raises(StoreRefused) as blocked:
        erase(world.store, "e", "samples", ["S2"], "operator:Ada")
    assert str(blocked.value.refusal.code) == "ERASURE_BLOCKED"
    samples = base.SAMPLES.replace(b"P2\tS2\n", b"")
    patients = base.PATIENTS.replace(b"P2\tM\n", b"")
    over = {
        "data_clinical_sample_DOT_txt": samples,
        "data_clinical_patient_DOT_txt": patients,
        "case_lists__cases_sequenced_DOT_txt": base.case_list("sequenced", ["S1", "S3"]),
        "data_mutations_DOT_txt": base.maf([base.R1]),
    }
    kept = c.study(**over)
    with pytest.raises(ImportRefused) as raised:
        world.reimport("e", write(world, "e", kept), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.UNKNOWN_SAMPLE"]
    clean = c.study(**over, data_gene_panel_matrix_DOT_txt=c.tsv([c.HEADER, c.R1, c.R3]))
    world.reimport("e", write(world, "e", clean), registry, pack="onco")
    erased = erase(world.store, "e", "samples", ["S2"], "operator:Ada")
    assert erased.withdrawn


# --- Review round 1 ---------------------------------------------------------------------------


def test_a_re_import_never_gives_a_suffixed_column_id(world: Any, registry: PackRegistry) -> None:
    """Review r1 m2: the collision check counts the previous release's ids, which the build keeps
    (D238): release 1 publishes the name ``-`` as ``c_3``; a re-import whose header holds ``-``
    elsewhere and a column ``c_3`` would publish ``c_3_2``, and is refused ``COLUMN_NAME``
    instead; a fresh import of the same bytes publishes ``c_2`` and ``c_3``, and a re-import
    that keeps every name's place is accepted with its ids."""
    c = corpus()
    first = c.study(
        meta_gene_panel_matrix_DOT_txt=c.meta(extra="stable_id: -\n"),
        data_gene_panel_matrix_DOT_txt=c.tsv([("SAMPLE_ID", "mutations", "-"), ("S1", "P", "P")]),
    )
    published = world.publish("c", write(world, "c", first), registry, pack="onco")
    found = {d.id for d in world.store.descriptors(published.manifest)}
    assert "gene_panel_matrix.c_3" in found
    second = c.study(
        meta_gene_panel_matrix_DOT_txt=c.meta(extra="stable_id: -\n"),
        meta_clinical_sample_DOT_txt=c.BASE.SAMPLE_META + b"stable_id: c_3\n",
        data_gene_panel_matrix_DOT_txt=c.tsv(
            [("SAMPLE_ID", "-", "mutations", "c_3"), ("S1", "P", "P", "P")]
        ),
    )
    with pytest.raises(ImportRefused) as raised:
        world.reimport("c", write(world, "c", second), registry, pack="onco")
    assert texts(raised.value) == [
        (
            "onco.COLUMN_NAME",
            " line 1, column 4: the column name's id is another column's",
        )
    ]
    fresh = world.publish("f", write(world, "f", second), registry, pack="onco")
    ids = {d.id for d in world.store.descriptors(fresh.manifest)}
    assert {"gene_panel_matrix.c_2", "gene_panel_matrix.c_3"} <= ids
    assert not any(i.startswith("gene_panel_matrix.c_3_") for i in ids)
    moved = c.study(
        meta_gene_panel_matrix_DOT_txt=c.meta(extra="stable_id: -\n"),
        data_gene_panel_matrix_DOT_txt=c.tsv(
            [("SAMPLE_ID", "-", "mutations"), ("S1", "P", "P"), ("S2", "P", "P")]
        ),
    )
    again = world.reimport("c", write(world, "c", moved), registry, pack="onco")
    ids = {d.id for d in world.store.descriptors(again.manifest)}
    assert "gene_panel_matrix.c_3" in ids  # ``-`` moved to position 2 and kept its id
    assert "gene_panel_matrix.c_2" not in ids


class _Recording:
    """A reader that records the name of every file it reads, in order."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.read_names: list[str] = []

    def read(self, path: Any, limit: int) -> bytes:
        self.read_names.append(Path(str(path)).name)
        return self.inner.read(path, limit)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def test_the_importer_reads_the_matrix_after_the_lists_and_before_the_maf(tmp_path: Path) -> None:
    """Review r1 m3: the census cannot see the importer's deferral of the MAF's read (a tuple of
    no or one mutation meta, read by ``next``, D447), so its order of reads is pinned: with a MAF
    and a matrix, the case lists, then the matrix, then the MAF; with no matrix, the MAF after
    the lists; with no MAF, the matrix after the lists; with neither, neither."""
    from aibi.core.importers.confine import Confinement
    from aibi.core.schema.pack_api import ImportOptions

    onco = importlib.import_module("aibi.packs.onco")
    c = corpus()
    no_maf = {
        "meta_mutations_DOT_txt": None,
        "data_mutations_DOT_txt": None,
        "meta_gene_panel_matrix_DOT_txt": c.meta(extra="stable_id: mutations\n"),
    }
    no_matrix = {"meta_gene_panel_matrix_DOT_txt": None, "data_gene_panel_matrix_DOT_txt": None}
    for label, over, wanted in (
        ("both", {}, ["data_gene_panel_matrix.txt", "data_mutations.txt"]),
        ("no_matrix", no_matrix, ["data_mutations.txt"]),
        ("no_maf", no_maf, ["data_gene_panel_matrix.txt"]),
        ("neither", {**no_maf, **no_matrix}, []),
    ):
        directory = _study_dir(tmp_path / label, c.study(**over))
        confinement = Confinement.of(directory.parent)
        reader: Any = _Recording(confinement)
        options = ImportOptions(
            dataset="s", reader=reader, limits=ImportLimits(), at="2026-01-01T00:00:00Z"
        )
        onco.PACK.importer.import_source(confinement.confine(directory), options)
        names = reader.read_names
        last_list = max(i for i, name in enumerate(names) if name.startswith("cases_"))
        after = [n for n in names[last_list + 1 :] if n.startswith("data_")]
        assert after == wanted, (label, names)


POOL = ("-", "()", "c_2", "c_3", "c_4", "a-b", "a_b", "1a", "c_1a", "mutations", "x")


def test_the_collision_rule_with_a_previous_release_is_the_oracle_s() -> None:
    """Review r2 MINOR-B: every header of one to three names from a pool of names whose ids
    collide, against no previous release and against 150 previous releases (each a header the
    pack accepted, with the ids the build gave it), through the reader and the oracle's
    ``collides(names, previous)``, written apart: refused (always as a collision) exactly where
    the oracle says a name's id would be neither its base nor its kept id; and the oracle's
    assignment is the core's ``normalise_names`` on every case, so the predicate reads the ids the
    build publishes."""
    from aibi.core.schema.ids import normalise_names

    o = oracle()
    found = profiles(frozenset(POOL))
    headers = [p for n in range(1, 4) for p in itertools.permutations(POOL, n)]
    releases: list[tuple[tuple[str, str], ...] | None] = []
    for header in headers:
        names = ["SAMPLE_ID", *header]
        if not o.collides(names):
            releases.append(tuple(zip(names, normalise_names(names, "column"), strict=True)))
    rng = random.Random(23)
    checked = refused = 0
    for previous in [None, *rng.sample(releases, 150)]:
        for header in headers:
            names = ["SAMPLE_ID", *header]
            assert o.assigned_ids(names, previous) == normalise_names(names, "column", previous)
            data = ("\t".join(names) + "\nS1" + "\tP" * len(header) + "\n").encode()
            try:
                panels().read(
                    data,
                    "m.txt",
                    frozenset({"S1"}),
                    ImportLimits(),
                    found=found,
                    sequenced=None,
                    previous=previous,
                )
                got = False
            except Refused as error:
                (refusal,) = error.refusals
                assert str(refusal.code) == "onco.COLUMN_NAME", (names, previous)
                got = True
            assert got is o.collides(names, previous), (names, previous)
            refused += got
            checked += 1
    assert checked > 150_000
    assert 0 < refused < checked


REFUSED_CHARACTERS = (
    *(chr(p) for p in (*range(0x20), 0x7F) if chr(p) not in "\t\n\r"),
    "\x85",
    "\u2028",
    "\u2029",
    "\ufeff",
)


def test_the_one_exempt_mark_is_the_file_s_first_character() -> None:
    """Review r2 MINOR-A: each refused character (the 30 controls, the three separators, U+FEFF)
    at positions 0, 1, 2 and the end of each line role (the first line as a header or as a top
    ``#`` line, a later header, a data line), with and without a mark leading the file, through
    the reader and the oracle's ``read_item``: the same item, so that the one mark the pack
    exempts is exactly the file's first character."""
    o = oracle()
    found = profiles()
    body = ["SAMPLE_ID\tmutations", "S1\tP", "S2\tP", "S3\tNA"]
    roles = {
        "header": (body, 0),
        "top": (["#top", *body], 0),
        "later_header": (["#top", *body], 1),
        "row": (body, 1),
    }
    checked = 0
    for role, (lines, at) in roles.items():
        for character in REFUSED_CHARACTERS:
            for position in (0, 1, 2, -1):
                for lead in ("", "\ufeff"):
                    changed = list(lines)
                    line = changed[at]
                    where = len(line) if position == -1 else position
                    changed[at] = line[:where] + character + line[where:]
                    text = "\n".join(changed) + "\n"
                    data = (lead + text).encode()
                    try:
                        read(data, found=found)
                        got = None
                    except Refused as error:
                        (refusal,) = error.refusals
                        reason = "".join(getattr(s, "text", "") for s in refusal.message)
                        got = item_of(
                            (
                                0,
                                str(refusal.code).removeprefix("onco."),
                                reason.split(": ", 1)[-1].strip(),
                            )
                        )
                    model = o.Profiles(found.keys, found.panels, found.crashes)
                    wanted = o.read_item(data, SAMPLES, model, SEQUENCED)
                    assert got == wanted, (role, repr(character), position, lead)
                    checked += 1
    assert checked == 4 * len(REFUSED_CHARACTERS) * 4 * 2
