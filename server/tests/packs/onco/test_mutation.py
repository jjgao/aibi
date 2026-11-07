"""The MAF reader and the case-list reader beyond the corpus (M4.2b-1b, D444): the stricter bounds
no corpus cell reaches at the default limits, the order of a row's checks, totality over hostile
bytes, what is declared (no coverage, the relationships always imported), and erasure through a
sample with mutation rows.

The corpus (``test_mutation_corpus.py``) holds each class against the reference's recorded
outcomes; this module holds what needs other limits, a store or many bytes.
"""

import contextlib
import hashlib
import importlib
import importlib.util
import itertools
import random
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.descriptors import RelationshipDescriptor, TableDescriptor
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import PackRegistry, Refused
from aibi.core.store.erasure import erase
from aibi.core.store.store import StoreRefused

HERE = Path(__file__).resolve().parent


def corpus() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutation_corpus", HERE / "mutation_corpus.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def modules() -> tuple[Any, Any]:
    return (
        importlib.import_module("aibi.packs.onco.mutation"),
        importlib.import_module("aibi.packs.onco.meta"),
    )


SAMPLES = frozenset({"S1", "S2", "S3"})


def read(data: bytes, limits: ImportLimits | None = None, **fields: Any) -> Any:
    mutation, meta = modules()
    found = meta.MutationFields(
        fields.get("filter"), fields.get("normals"), fields.get("namespaces", ()), "GRCh37"
    )
    return mutation.read(data, "m.txt", SAMPLES, found, limits or ImportLimits())


def code(data: bytes, limits: ImportLimits | None = None, **fields: Any) -> str:
    try:
        read(data, limits, **fields)
    except Refused as error:
        (refusal,) = error.refusals
        return str(refusal.code)
    return "ok"


def test_the_column_bound_is_counted_before_the_header_is_split() -> None:
    """Stricter (D444): more columns than ``table_columns``, from the header's tabs."""
    c = corpus()
    assert code(c.maf([c.R1]), ImportLimits(table_columns=24)) == "ok"
    assert code(c.maf([c.R1]), ImportLimits(table_columns=23)) == "onco.TOO_MANY_COLUMNS"
    assert code(b"Hugo_Symbol" + b"\t" * 10**6 + b"\n") == "onco.TOO_MANY_COLUMNS"


def test_the_cell_bound_counts_the_import_s_cells() -> None:
    """Stricter (D444): the MAF's columns times its rows, with the cells before it, counted at
    each row and, for a file of no row, at its end (one row's cells); held here, since no corpus
    cell can pass the default bound (``HELD`` of ``test_mutation_corpus``): "the study's files
    have more cells than an import may"."""
    reason = "the study's files have more cells than an import may"
    c = corpus()
    data = c.maf([c.R1, c.R2])
    assert code(data, ImportLimits(import_cells=48)) == "ok"
    with pytest.raises(Refused) as raised:
        read(data, ImportLimits(import_cells=47))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 4: {reason}")]
    mutation, meta = modules()
    fields = meta.MutationFields(None, None, (), "GRCh37")
    limits = ImportLimits(import_cells=60)
    with pytest.raises(Refused) as raised:
        mutation.read(data, "m.txt", SAMPLES, fields, limits, cells_before=13)
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" line 4: {reason}")]
    header_only = c.maf([])
    assert code(header_only, ImportLimits(import_cells=24)) == "ok"
    with pytest.raises(Refused) as raised:
        read(header_only, ImportLimits(import_cells=23))
    assert texts(raised.value) == [("onco.TOO_MANY_CELLS", f" {reason}")]


def texts(error: Refused | ImportRefused) -> list[tuple[str, str]]:
    """Each refusal's code and its message's text segments (the data segments left out)."""
    return [
        (
            str(refusal.code),
            "".join(getattr(segment, "text", "") for segment in refusal.message),
        )
        for refusal in error.refusals
    ]


def test_a_header_name_over_a_descriptor_string_is_refused_uncut() -> None:
    c = corpus()
    long = "X" * 4097
    assert code(c.maf([c.R1], (*c.COLUMNS, long))) == "onco.COLUMN_NAME"
    assert code(c.maf([c.R1], (*c.COLUMNS, "X" * 4096))) == "ok"
    assert code(c.maf([c.R1], (*c.COLUMNS, "X￾"))) == "onco.COLUMN_NAME"


def test_a_row_s_first_failure_is_by_column() -> None:
    """The earliest column's failure is the one refused: a barcode (column 14) before a count
    (column 21), a blank symbol (column 1) before the barcode."""
    c = corpus()
    row = {**c.R1, "Tumor_Sample_Barcode": "S9", "t_alt_count": "abc"}
    assert code(c.maf([row])) == "onco.UNKNOWN_SAMPLE"
    row = {**row, "Hugo_Symbol": ""}
    assert code(c.maf([row])) == "onco.REQUIRED_VALUE"


def test_a_refusal_names_no_value() -> None:
    """A refusal names the file and a place, never a cell (D400, A6)."""
    c = corpus()
    secret = "SeCrEt9"
    for row in (
        {**c.R1, "Tumor_Sample_Barcode": secret},
        {**c.R1, "t_alt_count": secret},
        {**c.R1, "HGVSp_Short": f"p.{secret};x"},
        {**c.R1, "NCBI_Build": secret},
    ):
        with pytest.raises(Refused) as raised:
            read(c.maf([row]))
        assert secret not in repr(raised.value.refusals)


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=600))
def test_the_reader_is_total_over_bytes(data: bytes) -> None:
    """Any bytes after a valid header: ``Maf`` or ``Refused``, never another exception."""
    c = corpus()
    with contextlib.suppress(Refused):
        read(c.maf([c.R1]) + data)


@settings(max_examples=200, deadline=None)
@given(st.binary(max_size=400))
def test_the_case_list_parse_is_total_over_bytes(data: bytes) -> None:
    caselists = importlib.import_module("aibi.packs.onco.caselists")
    with contextlib.suppress(Refused):
        caselists.read_entry("x.txt", data)


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


def test_the_case_list_bounds(world: Any, registry: PackRegistry) -> None:
    """Stricter (D444): a case list over ``CASE_LIST_MOST`` bytes, and more than
    ``CASE_LIST_FILES_MOST`` entries in ``case_lists/``, each ``CASE_LIST``."""
    caselists = importlib.import_module("aibi.packs.onco.caselists")
    c = corpus()
    big = c.case_list("sequenced", ["S1"]) + b"x: " + b"y" * caselists.CASE_LIST_MOST + b"\n"
    with pytest.raises(ImportRefused) as raised:
        world.publish(
            "a",
            write(world, "a", c.study(case_lists__cases_sequenced_DOT_txt=big)),
            registry,
            pack="onco",
        )
    assert [str(r.code) for r in raised.value.refusals] == ["onco.CASE_LIST"]
    many = {
        f"case_lists__c{k}_DOT_txt": c.case_list(f"l{k}", ["S1"], None)
        for k in range(caselists.CASE_LIST_FILES_MOST)
    }
    with pytest.raises(ImportRefused) as raised:
        world.publish("b", write(world, "b", c.study(**many)), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.CASE_LIST"]


def test_the_import_counts_every_table_s_cells(world: Any, registry: PackRegistry) -> None:
    """``import_cells`` counts the clinical tables, the case lists' tables and the MAF together
    (the corpus's valid study: 6 + 6 clinical, 4 + 6 for its one list, 48 for its MAF), and the
    case-list reader refuses as soon as a list takes the import past it, before the MAF is
    read."""
    c = corpus()
    directory = write(world, "c", c.study())
    clinical = 2 * 3 + 2 * 3
    lists = 4 * 1 + 2 * 3
    total = clinical + lists + 24 * 2
    limits = ImportLimits(import_cells=total)
    world.publish("c", directory, registry, pack="onco", limits=limits)
    with pytest.raises(ImportRefused) as raised:
        world.publish(
            "d", directory, registry, pack="onco", limits=ImportLimits(import_cells=total - 1)
        )
    assert [str(r.code) for r in raised.value.refusals] == ["onco.TOO_MANY_CELLS"]
    with pytest.raises(ImportRefused) as raised:
        world.publish(
            "e2",
            directory,
            registry,
            pack="onco",
            limits=ImportLimits(import_cells=clinical + lists - 1),
        )
    (refusal,) = raised.value.refusals
    assert str(refusal.code) == "onco.TOO_MANY_CELLS"
    assert "cases_sequenced.txt" in repr(refusal.message)
    assert texts(raised.value)[0][1].endswith(
        "takes the study's files past the cells an import may"
    )


def test_a_case_list_entry_of_no_file_kind() -> None:
    """Stricter (D444, review r3 m6): an entry of ``case_lists/`` that is neither a file, a
    directory nor a symlink (a FIFO, a socket, a device: kind ``other``), or one that could not be
    confined (``unconfined``), is ``CASE_LIST``, "is not a regular file" and "cannot be
    confined", before anything is read; held here, since the reference's run blocks on a FIFO
    and no corpus cell can be one (``HELD`` of ``test_mutation_corpus``)."""
    from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry

    caselists = importlib.import_module("aibi.packs.onco.caselists")

    class Listing:
        def __init__(self, kind: Any) -> None:
            self.kind = kind

        def files(self, directory: ConfinedPath) -> list[DirectoryEntry]:
            return [DirectoryEntry("cases_x.txt", self.kind)]

        def read(self, path: ConfinedPath, limit: int) -> bytes:
            raise AssertionError("an entry of no file kind is never read")

    for kind, reason in (("other", "is not a regular file"), ("unconfined", "cannot be confined")):
        with pytest.raises(Refused) as raised:
            caselists.read(Listing(kind), ConfinedPath(Path("/s")), "s", SAMPLES, ImportLimits())
        ((found, text),) = texts(raised.value)
        assert found == "onco.CASE_LIST"
        assert text.endswith(reason), text


def test_no_coverage_is_declared_and_every_relationship_is_imported(
    world: Any, registry: PackRegistry
) -> None:
    """D444 (B1'): no table is a coverage, whatever the study; the relationships from the MAF's
    barcodes and the case lists' ids to the samples are always ``imported`` (B2, M-D)."""
    c = corpus()
    meta = c.META + "gene_panel: IMPACT468\n"
    for name, files in (
        ("e", c.study()),
        ("f", c.study(meta_mutations_DOT_txt=meta.encode())),
    ):
        published = world.publish(name, write(world, name, files), registry, pack="onco")
        found = {d.id: d for d in world.store.descriptors(published.manifest)}
        tables = [d for d in found.values() if isinstance(d, TableDescriptor)]
        assert all(table.fields.role != "coverage" for table in tables)
        assert not any(d.kind == "coverage" for d in found.values())
        for relationship in (d for d in found.values() if isinstance(d, RelationshipDescriptor)):
            if relationship.fields.child_table in ("mutations", "case_list_members"):
                for field in (
                    "child_table",
                    "child_columns",
                    "parent_table",
                    "parent_columns",
                    "cardinality",
                ):
                    status = relationship.curation[f"/fields/{field}"].status
                    assert status == "imported", (relationship.id, field)


def test_every_sample_id_column_is_a_declared_relationship(
    world: Any, registry: PackRegistry
) -> None:
    """M-D: every column of the pack's tables that holds sample ids (an ``identifier`` that is
    not a key of its own table, but the normal barcode, which names a normal sample no file
    lists) is the child of an imported relationship to ``samples``."""
    c = corpus()
    published = world.publish("g", write(world, "g", c.study()), registry, pack="onco")
    found = {d.id: d for d in world.store.descriptors(published.manifest)}
    children = {
        f"{d.fields.child_table}.{column}"
        for d in found.values()
        if isinstance(d, RelationshipDescriptor) and d.fields.parent_table == "samples"
        for column in d.fields.child_columns
    }
    assert children == {"mutations.tumor_sample_barcode", "case_list_members.sample_id"}


def test_erasing_a_sample_with_mutation_rows(world: Any, registry: PackRegistry) -> None:
    """B2 as a test: erasing S1, which has mutation rows and case-list rows, is refused while a
    live release holds it; a re-import that drops S1 from the samples but keeps its mutation row
    or its list id is refused by the pack (``UNKNOWN_SAMPLE``, the onco code, not only the
    gate's); once the samples, the MAF and the lists all drop it, the erasure goes through."""
    c = corpus()
    world.publish("h", write(world, "h", c.study()), registry, pack="onco")
    with pytest.raises(StoreRefused) as blocked:
        erase(world.store, "h", "samples", ["S1"], "operator:Ada")
    assert str(blocked.value.refusal.code) == "ERASURE_BLOCKED"

    samples = c.SAMPLES.replace(b"P1\tS1\n", b"")
    patients = c.PATIENTS.replace(b"P1\tF\n", b"")
    without = {"data_clinical_sample.txt": samples, "data_clinical_patient.txt": patients}
    kept_row = c.study(
        **{k.replace(".", "_DOT_"): v for k, v in without.items()},
        case_lists__cases_sequenced_DOT_txt=c.case_list("sequenced", ["S2", "S3"]),
    )
    with pytest.raises(ImportRefused) as raised:
        world.reimport("h", write(world, "h", kept_row), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.UNKNOWN_SAMPLE"]

    maf = c.maf([c.R2, {**c.R1, "Tumor_Sample_Barcode": "S3"}])
    kept_id = c.study(
        **{k.replace(".", "_DOT_"): v for k, v in without.items()}, data_mutations_DOT_txt=maf
    )
    with pytest.raises(ImportRefused) as raised:
        world.reimport("h", write(world, "h", kept_id), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.UNKNOWN_SAMPLE"]

    clean = c.study(
        **{k.replace(".", "_DOT_"): v for k, v in without.items()},
        data_mutations_DOT_txt=maf,
        case_lists__cases_sequenced_DOT_txt=c.case_list("sequenced", ["S2", "S3"]),
    )
    world.reimport("h", write(world, "h", clean), registry, pack="onco")
    erased = erase(world.store, "h", "samples", ["S1"], "operator:Ada")
    assert erased.withdrawn


def test_the_reader_s_check_is_the_pack_s_own(world: Any, registry: PackRegistry) -> None:
    """Review r2 m5: with the gate there, the onco code is what a dangling barcode is refused
    with; the gate's ``DANGLING_REFERENCE`` is the backstop, not the check."""
    c = corpus()
    files = c.study(data_mutations_DOT_txt=c.maf([c.R2, {**c.R1, "Tumor_Sample_Barcode": "S9"}]))
    with pytest.raises(ImportRefused) as raised:
        world.publish("i", write(world, "i", files), registry, pack="onco")
    assert [str(r.code) for r in raised.value.refusals] == ["onco.UNKNOWN_SAMPLE"]


def test_the_skip_predicate_s_every_arm() -> None:
    """The filter replaces the default list (v01), ``Unknown`` with no Entrez column is a skip
    row (w05), and ``silent`` in lower case is validated (w07): each arm, on a row the reference
    would refuse were it validated (a count ``abc``)."""
    c = corpus()
    bad = {**c.R1, "t_alt_count": "abc"}
    assert code(c.maf([c.R2, {**bad, "Variant_Classification": "Silent"}])) == "ok"
    assert code(c.maf([c.R2, {**bad, "Variant_Classification": "silent"}])) == "onco.COUNT_VALUE"
    filtered = ("Missense_Mutation",)
    assert code(c.maf([c.R2, bad]), filter=filtered) == "ok"
    silent = {**bad, "Variant_Classification": "Silent"}
    assert code(c.maf([c.R2, silent]), filter=filtered) == "onco.COUNT_VALUE"
    no_entrez = tuple(name for name in c.COLUMNS if name != "Entrez_Gene_Id")
    unknown = {**bad, "Hugo_Symbol": "Unknown"}
    assert code(c.maf([c.R2, unknown], no_entrez)) == "ok"
    assert code(c.maf([c.R2, {**unknown, "Entrez_Gene_Id": "0"}])) == "ok"
    assert code(c.maf([c.R2, {**unknown, "Entrez_Gene_Id": " 0"}])) == "onco.NO_GENE"


def test_tokens_by_column_class_are_the_reference_s_rule() -> None:
    """A brute force of tokens by column class on a validated row, against the reference's rule
    as D444 states it (``int()`` for a count, ``isdigit`` and ``float()`` for a position, an
    Entrez id that ``int()`` reads as 0 or less): the corpus records the reference on a sample of
    these; this holds every token of a wider set, including whitespace and digits of every
    script, to the rule."""
    c = corpus()
    tokens = [
        "",
        " ",
        ".",
        "NA",
        "0",
        "-0",
        "7",
        "+7",
        " 7",
        "7 ",
        "1_0",
        "1e3",
        "1.5",
        "inf",
        "nan",
        "abc",
        "\u0665",
        "\u00b2",
        "\u00bd",
        "\u2167",
        "\uff17",
        "0x10",
        "--1",
    ]

    def as_int(token: str) -> int | None:
        try:
            return int(token)
        except ValueError:
            return None

    def as_float(token: str) -> bool:
        try:
            float(token.strip("'").strip('"'))
        except ValueError:
            return False
        return True

    for token, column in itertools.product(tokens, ("t_alt_count", "Start_Position")):
        row = {**c.R1, column: token, "End_Position": "7578406"}
        found = code(c.maf([c.R2, row]))
        if column == "t_alt_count":
            want = token in ("", ".") or as_int(token) is not None
            assert (found == "ok") == want, (token, column, found)
        else:
            blank = token.strip() == "" or token == "NA"
            want = blank or (token.isdigit() and as_float(token))
            assert (found == "ok") == want, (token, column, found)
    for token in tokens:
        found = code(c.maf([c.R2, {**c.R1, "Entrez_Gene_Id": token}]))
        stripped = token.strip()
        core = modules()[0].core_integer(token)
        if stripped in ("", "NA", "0"):
            want = not (stripped == "0" and token != "0" and core)
        else:
            number = as_int(stripped)
            want = number > 0 if number is not None else not core
        assert (found == "ok") == want, (token, found)


def test_the_mutations_table_under_k_and_d271_s_residual(
    world: Any, registry: PackRegistry
) -> None:
    """D444's disclosure paragraph, run (review r2 m4): under a floor of 5, ``describe_column``
    serves the ``mutations`` table's column statistics by D271's rule on rows, as any table's: a
    column whose rows are fewer than *k* is suppressed. The residual D444 states: rows are not
    units, so 6 rows of one sample in one class are 6 rows, at or over *k*, though they are one
    sample's."""
    from aibi.core.catalog.service import Catalog
    from aibi.core.schema.catalog import DescribeColumn

    c = corpus()
    one_sample = [
        {**c.R1, "Start_Position": str(100 + i), "End_Position": str(100 + i)} for i in range(6)
    ]
    files = c.study(data_mutations_DOT_txt=c.maf([c.R2, *one_sample]))
    world.publish("k", write(world, "k", files), registry, pack="onco")
    catalog = Catalog(world.store, registry=registry, floor=5)
    found = catalog.describe_column(
        DescribeColumn.model_validate({"dataset": "k", "column": "mutations.variant_type"})
    )
    assert found.disclosure.min_cell_count == 5
    dumped = found.model_dump(mode="json")
    assert dumped["statistics"]["states"]["PRESENT"]["count"] == 7
    small = c.study(data_mutations_DOT_txt=c.maf([c.R2, c.R1]))
    world.publish("k2", write(world, "k2", small), registry, pack="onco")
    found = catalog.describe_column(
        DescribeColumn.model_validate({"dataset": "k2", "column": "mutations.variant_type"})
    )
    assert found.model_dump(mode="json")["statistics"]["states"]["PRESENT"]["count"] is None


# --- Review r1 MJ4: what no corpus outcome holds --------------------------------------------------


def refusal_of(data: bytes) -> tuple[str, str]:
    """The refusal's code and its message, as text."""
    with pytest.raises(Refused) as raised:
        read(data)
    (refusal,) = raised.value.refusals
    text = "".join(getattr(segment, "text", "") for segment in refusal.message)
    return str(refusal.code), text


def test_the_first_failing_row_is_the_one_refused() -> None:
    """X39: of two rows that fail, the earlier line is named."""
    c = corpus()
    first = {**c.R1, "t_alt_count": "abc"}
    second = {**c.R2, "t_alt_count": "abc"}
    assert refusal_of(c.maf([first, second]))[1].startswith(" line 3, column 21:")


def test_a_control_character_comes_before_the_structure() -> None:
    """X40: a line of another width before a line with a control character: the control
    character's class is earlier, so it is the one refused, though its line is later."""
    c = corpus()
    data = (
        c.maf([c.R1]) + b"x\ty\n" + c.maf([{**c.R2, "HGVSc": "c.\x01"}], top=b"").split(b"\n", 1)[1]
    )
    assert refusal_of(data)[0] == "onco.CONTROL_CHARACTER"


def test_bytes_that_are_not_utf8_come_before_a_control_character() -> None:
    """X41: a control character on line 3 and bytes that are not UTF-8 on line 5, past a valid
    line 4: decoding goes on to the end of the file past the control line, and the earliest
    class, not UTF-8, is refused."""
    c = corpus()
    later = {**c.R2, "HGVSp_Short": "p.G13D"}
    rows = c.maf([c.R2, later], top=b"").split(b"\n", 1)[1]
    data = c.maf([{**c.R1, "HGVSc": "c.\x01"}]) + rows.replace(b"p.G13D", b"p.G13\xff")
    assert refusal_of(data)[0] == "onco.NOT_UTF8"


def test_d228_s_half_bound() -> None:
    """X35: a categorical column is a category when its distinct values are at most half its
    present cells, and not one past that."""
    c = corpus()
    rows = [
        {**c.R1, "Start_Position": str(i), "End_Position": str(i), "Variant_Type": kind}
        for i, kind in enumerate(("SNP", "SNP", "DNP", "DNP"), start=1)
    ]
    found = {column.name: column.category for column in read(c.maf(rows)).columns}
    assert found["Variant_Type"] is True
    rows[3] = {**rows[3], "Variant_Type": "TNP"}
    found = {column.name: column.category for column in read(c.maf(rows)).columns}
    assert found["Variant_Type"] is False


def test_the_categorical_sets_are_bounded_within_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X34: however many distinct values a categorical column holds, the reader keeps at most
    ``CATEGORY_VALUES + 1`` of them while it reads (each through ``identity``)."""
    mutation, _ = modules()
    calls: list[str] = []
    original = mutation.identity

    def counted(cell: str) -> str | bytes:
        calls.append(cell)
        return original(cell)

    monkeypatch.setattr(mutation, "identity", counted)
    c = corpus()
    rows = [
        {**c.R1, "Start_Position": str(i), "End_Position": str(i), "Variant_Type": f"T{i}"}
        for i in range(1, 2001)
    ]
    read(c.maf(rows))
    kept = {cell for cell in calls if cell.startswith("T") and cell[1:].isdigit()}
    assert len(kept) <= mutation.CATEGORY_VALUES + 1


def imported(tmp_path: Path, files: Mapping[str, Any]) -> Any:
    """``PACK.importer``'s result for a study written below ``tmp_path``."""
    from aibi.core.importers.confine import Confinement
    from aibi.core.schema.pack_api import ImportOptions

    directory = tmp_path / "imports" / "study"
    for path, content in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    confinement = Confinement.of(directory.parent)
    options = ImportOptions(
        dataset="study", reader=confinement, limits=ImportLimits(), at="2026-01-01T00:00:00Z"
    )
    onco = importlib.import_module("aibi.packs.onco")
    return onco.PACK.importer.import_source(confinement.confine(directory), options)


def test_a_list_s_repeated_ids_are_kept(tmp_path: Path) -> None:
    """C16, plan M-D: a list's repeated ids are read without loss, one member row each."""
    c = corpus()
    files = c.study(
        case_lists__cases_sequenced_DOT_txt=c.case_list("sequenced", ["S1", "S2", "S1"])
    )
    result = imported(tmp_path, files)
    members = result.sources["case_list_members"]
    assert [row[1] for row in members.rows] == ["S1", "S2", "S1"]


def test_the_normal_barcode_is_an_identifier_and_no_relationship_child(tmp_path: Path) -> None:
    """B04, MN5: ``Matched_Norm_Sample_Barcode`` is an identifier (its ids are erasure terms) and
    the child of no relationship: the reference checks it against the meta's
    ``normal_samples_list`` only, never against the sample file (D444)."""
    result = imported(tmp_path, corpus().study())
    found = {d.id: d for d in result.descriptors}
    normal = found["mutations.matched_norm_sample_barcode"]
    assert normal.fields.identifier is True
    children = {
        (d.fields.child_table, column)
        for d in found.values()
        if d.kind == "relationship"
        for column in d.fields.child_columns
    }
    assert ("mutations", "matched_norm_sample_barcode") not in children


def test_a_declared_missing_spelling_is_missing_not_unparsed(
    world: Any, registry: PackRegistry
) -> None:
    """B07, MN6: a count of ``.``, an Entrez id of ``0`` and a symbol of ``NA`` are the reference's
    spellings of no value: the published column reads them as missing, with no ``unparsed``
    note."""
    c = corpus()
    row = {**c.R1, "t_alt_count": ".", "Entrez_Gene_Id": "0", "Hugo_Symbol": "TP53"}
    other = {**c.R2, "Hugo_Symbol": "NA", "HGVSp_Short": "NULL"}
    files = c.study(data_mutations_DOT_txt=c.maf([row, other]))
    published = world.publish("m", write(world, "m", files), registry, pack="onco")
    subjects = {note.subject for note in published.notes if note.kind == "unparsed"}
    assert not any(str(subject).startswith("mutations.") for subject in subjects)
    found = {d.id: d for d in world.store.descriptors(published.manifest)}
    assert found["mutations.t_alt_count"].fields.missing_codes == {".": "UNKNOWN"}
    assert found["mutations.hugo_symbol"].fields.missing_codes == {
        "NA": "UNKNOWN",
        "Unknown": "UNKNOWN",
    }
    assert found["mutations.hgvsp_short"].fields.missing_codes == {
        "NA": "UNKNOWN",
        "NULL": "UNKNOWN",
    }


def test_the_meta_s_lists_are_stripped_and_lowered() -> None:
    """T01, T02, T05: the filter's items and the normal samples are stripped, the namespaces
    stripped and lowered (V:2272, V:2213, V:1414)."""
    _, meta = modules()
    found = meta.mutation_fields(
        {
            "stable_id": "mutations",
            "variant_classification_filter": " Silent , Intron",
            "normal_samples_list": " N1 ,N2",
            "namespaces": " ZYGOSITY, Ascn ",
        },
        "meta_mutations.txt",
        None,
    )
    assert found.filter == ("Silent", "Intron")
    assert found.normals == frozenset({"N1", "N2"})
    assert found.namespaces == ("zygosity", "ascn")


ATOMS = (
    "\t",
    "\n",
    "\r",
    "\r\n",
    "#",
    " ",
    "﻿",
    "\u00a0",
    "\x85",
    "￾",
    "￿",
    "\U0010ffff",
    '"',
    "'",
    "NA",
    ".",
    "0",
    "Unknown",
    "Silent",
    "S1",
    "S9",
    "p.",
    ";",
    ",",
    "\u0665",
    "1e3",
    "xxxxx",
    "\u2028",
    "\x1f",
    "\x00",
)
RAW = (b"\xed\xa0\x80", b"\xff", b"\xc0\xaf", b"\xef\xbb\xbf")


@pytest.mark.parametrize("seed", range(4))
def test_a_structured_fuzz_through_the_publish_is_total(
    seed: int, world: Any, registry: PackRegistry
) -> None:
    """MN8, after the review's probe: the MAF or the ``_sequenced`` list, each mutated by up to
    four insertions or replacements of the tokens the readers split or judge (tab, CR, LF, ``#``,
    a BOM, U+2028, U+0085, noncharacters, surrogate and overlong bytes, NUL, the null spellings),
    published through the store: every outcome is ``ok`` or one ``onco.`` refusal, never a raw
    exception or ``PACK_FAILED``. 15 cases a seed, seeded."""
    c = corpus()
    rng = random.Random(seed)
    base_maf = c.maf([c.R1, c.R2])
    base_list = c.case_list("sequenced", ["S1", "S2", "S3"])
    for index in range(15):
        target = rng.choice(("maf", "maf", "list"))
        data = base_maf if target == "maf" else base_list
        for _ in range(rng.randint(1, 4)):
            at = rng.randint(0, len(data))
            piece = rng.choice(RAW) if rng.random() < 0.1 else rng.choice(ATOMS).encode()
            end = min(len(data), at + rng.randint(1, 10)) if rng.random() < 0.3 else at
            data = data[:at] + piece + data[end:]
        files = (
            c.data(data) if target == "maf" else c.study(case_lists__cases_sequenced_DOT_txt=data)
        )
        name = f"f{seed}_{index}"
        try:
            world.publish(name, write(world, name, files), registry, pack="onco")
        except ImportRefused as error:
            codes = {str(refusal.code) for refusal in error.refusals}
            assert all(code.startswith("onco.") for code in codes), (data, codes)


def test_every_namespace_of_the_meta_is_checked() -> None:
    """X48: each distinct namespace needs a column, not only the first in any order."""
    c = corpus()
    columns = (*c.COLUMNS, "aaa.x")
    data = c.maf([{**c.R1, "aaa.x": "1"}], columns)
    assert code(data, namespaces=("aaa",)) == "ok"
    assert code(data, namespaces=("aaa", "zzz")) == "onco.MISSING_COLUMN"
    assert code(data, namespaces=("zzz", "aaa")) == "onco.MISSING_COLUMN"


def test_a_case_list_is_read_within_the_operator_s_import_bytes(
    world: Any, registry: PackRegistry
) -> None:
    """MN1: a case list is read at most the smaller of ``CASE_LIST_MOST`` and the operator's
    ``import_bytes``; past ``import_bytes`` the reader's own ``LIMIT_EXCEEDED`` passes out."""
    c = corpus()
    big = c.case_list("sequenced", ["S1", "S2", "S3"] * 20_000)
    assert len(big) > 150_000
    files = c.study(case_lists__cases_sequenced_DOT_txt=big)
    directory = write(world, "j", files)
    with pytest.raises(ImportRefused) as raised:
        world.publish(
            "j", directory, registry, pack="onco", limits=ImportLimits(import_bytes=150_000)
        )
    assert [str(r.code) for r in raised.value.refusals] == ["LIMIT_EXCEEDED"]
    world.publish("j2", directory, registry, pack="onco")


# --- Review r2 ------------------------------------------------------------------------------------


TYPES_AND_IDS_COUNT = 65
TYPES_AND_IDS_DIGEST = "44fd54f53af9261e90ee787019e82d4e56e206e43ae7321be2476d7902997ec7"
"""The count and the SHA-256 of the sorted entries of ``allowed_combinations`` (C:738-791), taken
from the reference's source by an AST walk when the table was written (never read here): the
pack's ``meta.TYPES_AND_IDS`` must be exactly that table (review r2, N05, N06); the corpus's
``types`` cells hold a literal entry and a wildcard live."""


def test_the_type_combinations_are_the_reference_s_table() -> None:
    _, meta = modules()
    entries = sorted(meta.TYPES_AND_IDS)
    assert len(entries) == TYPES_AND_IDS_COUNT
    digest = hashlib.sha256(repr(entries).encode()).hexdigest()
    assert digest == TYPES_AND_IDS_DIGEST


EXPECTED_MISSING: Mapping[str, Mapping[str, str] | None] = {
    "Entrez_Gene_Id": {"0": "UNKNOWN", "NA": "UNKNOWN"},
    "Start_Position": {"NA": "UNKNOWN"},
    "End_Position": {"NA": "UNKNOWN"},
    "t_ref_count": {".": "UNKNOWN"},
    "t_alt_count": {".": "UNKNOWN"},
    "n_ref_count": {".": "UNKNOWN"},
    "n_alt_count": {".": "UNKNOWN"},
    "Hugo_Symbol": {"NA": "UNKNOWN", "Unknown": "UNKNOWN"},
    "HGVSp_Short": {"NA": "UNKNOWN", "NULL": "UNKNOWN"},
    "Amino_Acid_Change": {"NA": "UNKNOWN", "NULL": "UNKNOWN"},
}
"""Every MAF column with missing codes, and its map; every other column has none (review r2)."""


def test_every_maf_column_s_missing_codes_and_their_provenance(tmp_path: Path) -> None:
    """The missing-codes map of every column of a MAF with every column the corpus knows (the 24
    standard ones, ``Amino_Acid_Change`` and a custom one), each ``imported`` by the pack's
    importer with the evidence that the reference reads these spellings as no value."""
    c = corpus()
    columns = (*c.COLUMNS, "Amino_Acid_Change", "My_Column")
    files = c.study(data_mutations_DOT_txt=c.maf([{**c.R1, "Amino_Acid_Change": "R175H"}], columns))
    found = {d.id: d for d in imported(tmp_path, files).descriptors}
    for name in columns:
        descriptor = found[f"mutations.{name.lower()}"]
        want = EXPECTED_MISSING.get(name)
        assert descriptor.fields.missing_codes == want, name
        if want is not None:
            entry = descriptor.curation["/fields/missing_codes"]
            assert entry.status == "imported", name
            assert entry.evidence is not None
            assert "reads as no value" in entry.evidence, name


def test_the_pack_s_own_byte_cap_at_the_operator_s_bound(
    world: Any, registry: PackRegistry
) -> None:
    """N12: with ``import_bytes`` exactly ``CASE_LIST_MOST`` a list over it is the reader's own
    ``LIMIT_EXCEEDED`` (the operator's bound); one byte more of ``import_bytes`` and it is the
    pack's ``CASE_LIST`` (its own cap)."""
    caselists = importlib.import_module("aibi.packs.onco.caselists")
    c = corpus()
    big = c.case_list("sequenced", ["S1"]) + b"x: " + b"y" * caselists.CASE_LIST_MOST + b"\n"
    directory = write(world, "k8", c.study(case_lists__cases_sequenced_DOT_txt=big))
    for import_bytes, code in (
        (caselists.CASE_LIST_MOST, "LIMIT_EXCEEDED"),
        (caselists.CASE_LIST_MOST + 1, "onco.CASE_LIST"),
    ):
        limits = ImportLimits(import_bytes=import_bytes)
        with pytest.raises(ImportRefused) as raised:
            world.publish("k8", directory, registry, pack="onco", limits=limits)
        assert [str(r.code) for r in raised.value.refusals] == [code]


def test_a_long_skip_filter_costs_no_more_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r2 m2: the skip list is a set built once, so a filter of 32,000 items costs a row
    no more than one of 8 (within a small factor), never a scan of the filter per row."""
    c = corpus()
    rows = [
        {**c.R1, "Start_Position": str(i), "End_Position": str(i), "Variant_Classification": "X"}
        for i in range(1, 3001)
    ]
    data = c.maf(rows)

    def timed(items: int) -> float:
        filter_ = tuple(f"Class_{index}" for index in range(items))
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            read(data, filter=filter_)
            best = min(best, time.perf_counter() - started)
        return best

    assert timed(32_000) < 4 * timed(8) + 0.05


def test_the_one_column_sniff_follows_the_reference_s_sample() -> None:
    """The reference checks nothing of a file whose header and five lines after it hold no tab
    (V:470-495), and checks the header of one whose sample holds one: the first is the stricter
    no-tab refusal, the second a missing column."""
    no_tab = b"#v\nHugo_Symbol\nTP53\n"
    assert refusal_of(no_tab) == (
        "onco.MISSING_COLUMN",
        " line 2: the line of column names and the five lines after it hold no tab",
    )
    tab_later = b"Hugo_Symbol\nA\nB\nC\nD\nE\tx\n"
    assert refusal_of(tab_later)[1].endswith("the file has no Tumor_Sample_Barcode column")
    tab_too_late = b"Hugo_Symbol\nA\nB\nC\nD\nE\nF\tx\n"
    assert refusal_of(tab_too_late)[1].endswith("hold no tab")


def test_core_integer_is_the_core_s_integer_parse() -> None:
    """``mutation.core_integer``, the core's integer parse re-expressed (a pack may not import
    ``aibi.core.store.cells``), agrees with the core's own over a brute force of tokens: digits of
    every kind, signs, decimals, exponents, the 2^53 and 2^63 bounds, and padding the core strips
    or keeps."""
    from aibi.core.store.cells import _integer  # pyright: ignore[reportPrivateUsage]

    mutation, _ = modules()
    bodies = [
        "0",
        "7",
        "+7",
        "-7",
        "7.0",
        "7.5",
        "1e3",
        "1E3",
        "1e-3",
        "1e20",
        "1e400",
        ".5",
        "5.",
        "nan",
        "inf",
        "1_0",
        "\u0665",
        "0x10",
        "--1",
        "",
        "NA",
        "abc",
        str(2**53 - 1),
        f"{2**53 - 1}.0",
        f"-{2**53 - 1}.0",
        f"{2**53 - 1}.5",
        f"{2**53}.0",
        f"-{2**53}.0",
        f"{2**53 + 1}.0",
        "9.007199254740991e15",
        "9.007199254740992e15",
        "9007199254740991e0",
        str(2**53),
        f"{2**53}.0",
        str(2**63 - 1),
        str(2**63),
        "9" * 5000,
    ]
    pads = ["", " ", "\u00a0", "\u2028", "\u3000"]
    for body in bodies:
        for left in pads:
            for right in pads:
                token = left + body + right
                assert mutation.core_integer(token) == (_integer(token, token) is not None), token


def test_a_categorical_column_s_present_cells_are_the_core_s() -> None:
    """Review r3 Q40: whether a categorical column of the MAF is a category agrees with the core's
    inference over the same cells, for every column of two to four cells drawn from values, the
    empty cell and cells of whitespace alone (which the core reads as present values)."""
    from aibi.core.importers.infer import SourceTable, infer

    c = corpus()
    tokens = ("SNP", "DNP", "", " ", "  ", "\u00a0", "\u3000")
    for size in range(2, 5):
        for cells in itertools.product(tokens, repeat=size):
            if all(cell == "" for cell in cells):
                continue
            rows = [
                {**c.R1, "Start_Position": str(i), "End_Position": str(i), "Variant_Type": cell}
                for i, cell in enumerate(cells, start=1)
            ]
            found = {column.name: column.category for column in read(c.maf(rows)).columns}
            table = SourceTable("t", ("v",), [[cell] for cell in cells])
            (guess,) = infer([table]).tables
            assert found["Variant_Type"] == (guess.columns["v"].datatype == "category"), cells


ENTREZ_BODIES = (
    "7157",
    "+7157",
    "7157.0",
    "+7157.0",
    "0.0",
    "9007199254740991.0",
    "9007199254740992.0",
    "1e3",
    "1E3",
    "1e-3",
    "1e400",
    "\u0661",
    "\uff17\uff11\uff15\uff17",
    "1_0",
    "nan",
    "inf",
    "1.5",
    "-5",
    "-0",
    "0",
    "NA",
    "",
    "abc",
    "0x10",
)
"""Entrez tokens of every kind D444 names: whole numbers with a decimal point, exponents, signs,
non-ASCII digits, ``int()``'s underscores, non-numbers, the null spellings."""
PADS = ("", " ", "  ", "\u00a0", "\u3000", "\u2002", "\u200b")
"""Pads ``str.strip()`` removes (ASCII and Unicode whitespace) or keeps (a zero-width space)."""
ROW_CLASSES = ("Missense_Mutation", "Silent")
SKIPPED = frozenset({"Silent"})


def padded(body: str) -> list[str]:
    """``body`` as written, and padded before, after and around by each of ``PADS``."""
    found = [body]
    for pad in PADS[1:]:
        found += [pad + body, body + pad, pad + body + pad]
    return found


def reference_int(token: str) -> int | None:
    """The reference's reading of an Entrez id: ``int()`` of the stripped token, or ``None``."""
    try:
        return int(token.strip())
    except ValueError:
        return None


def core_reads_integer(token: str) -> bool:
    """Whether the published column holds a value for this Entrez cell: the cell is none of the
    column's missing codes as written (``""``, ``0``, ``NA``) and the core's integer parse reads
    it (``aibi.core.store.cells``, the core's own function)."""
    from aibi.core.store.cells import _integer  # pyright: ignore[reportPrivateUsage]

    return token not in ("", "0", "NA") and _integer(token, token) is not None


def rule(symbol: str, entrez: str, classification: str) -> str:
    """D444's rule for a row's symbol and Entrez id, written from the reference's reading as D444
    states it (never read from ``mutation_outcomes.json``): the code aibi must refuse the row
    with, or ``ok``.

    A row the reference skips (a skip class, or exactly ``Unknown`` and ``0``) is checked for
    nothing. On a validated row, by column (the symbol's first): a symbol ``str.strip()`` makes
    ``NA`` or ``Unknown``, not written so, beside a given Entrez id (one the reference reads as an
    id: stripped, neither empty, ``NA`` nor ``0``) is ``PADDED_VALUE`` (M27); such a symbol, padded
    or not, with no given Entrez id is ``NO_GENE`` (M21). Then the Entrez id: one that strips to
    ``0``, not written so, that the core would publish as 0 is ``PADDED_VALUE`` (M27); one
    ``int()`` reads as 0 or less is ``ENTREZ_VALUE`` (M19); one ``int()`` cannot read, which the
    reference warns it will not load, is ``ENTREZ_VALUE`` where the core would publish it as an
    integer (M20), else accepted and counted."""
    if classification in SKIPPED or (symbol == "Unknown" and entrez == "0"):
        return "ok"
    stripped = entrez.strip()
    given = stripped not in ("", "NA", "0")
    if symbol.strip() in ("NA", "Unknown"):
        if symbol != symbol.strip() and given:
            return "onco.PADDED_VALUE"
        if not given:
            return "onco.NO_GENE"
    if stripped == "0":
        padded_zero = entrez != stripped and core_reads_integer(entrez)
        return "onco.PADDED_VALUE" if padded_zero else "ok"
    if not given:
        return "ok"
    number = reference_int(entrez)
    if number is not None:
        return "onco.ENTREZ_VALUE" if number <= 0 else "ok"
    return "onco.ENTREZ_VALUE" if core_reads_integer(entrez) else "ok"


def row_code(symbol: str, entrez: str, classification: str) -> str:
    c = corpus()
    changed = {
        **c.R1,
        "Hugo_Symbol": symbol,
        "Entrez_Gene_Id": entrez,
        "Variant_Classification": classification,
    }
    return code(c.maf([c.R2, changed]))


def test_m20_every_entrez_token_follows_the_rule() -> None:
    """Review r4 (split, M20 and M19), independent of the recorded outcomes: every Entrez body
    of ``ENTREZ_BODIES`` under every padding, on a validated and a skipped row, is refused or
    accepted exactly as ``rule`` says, so that narrowing the check (a decimal point exempted, an
    exponent exempted) fails here whatever outcomes are re-recorded."""
    wrong = [
        (token, classification, found, expected)
        for body in ENTREZ_BODIES
        for token in padded(body)
        for classification in ROW_CLASSES
        if (found := row_code("TP53", token, classification))
        != (expected := rule("TP53", token, classification))
    ]
    assert wrong == []


def test_m27_every_padded_null_symbol_follows_the_rule() -> None:
    """Review r4 (split, M27 and M21), independent of the recorded outcomes: a symbol ``NA``,
    ``Unknown`` or a gene's, as written and under every padding, beside an Entrez id given in
    digits, given as ``+7157`` or padded, empty, ``0``, ``NA``, padded ``0`` or padded ``NA``, on
    a validated and a skipped row, is refused or accepted exactly as ``rule`` says (a padded null
    symbol beside a padded ``0`` is ``NO_GENE``, never ``PADDED_VALUE``)."""
    entrez_ids = ("7157", "+7157", " 7157", "", "0", "NA", " 0 ", "0 ", " NA")
    wrong = [
        (symbol, entrez, classification, found, expected)
        for body in ("NA", "Unknown", "TP53")
        for symbol in padded(body)
        for entrez in entrez_ids
        for classification in ROW_CLASSES
        if (found := row_code(symbol, entrez, classification))
        != (expected := rule(symbol, entrez, classification))
    ]
    assert wrong == []
