"""The clinical files' corpus against the reference's recorded outcomes (M4.2a-1b, D409).

Every cell of ``clinical_corpus.py`` (7,936 of them: whole studies, header rows, data cells and the
Excel date class by brute force)
is read by the pack, and its outcome is held twice:

- **per cell, to the plan's predicate**: aibi's refusal code, or that it accepts, is exactly what
  ``clinical_corpus.expected_header``, ``expected_hdr2`` and ``expected_cell`` give, or for a whole
  study what ``WHOLE`` states (``test_aibi_s_code_is_the_plan_s_on_every_cell``); so a mutant that
  loosens a stricter rule (a cap one off, a priority read by ``int()``) dies here, though the
  reference accepted the cell too;
- **per cell, to the reference**: ``clinical_reference.json`` is each cell's recorded outcome on
  cbioportal-core 47890bb (``.`` no error; else the identities of its errors, in the pack's words,
  no message of the reference quoted). A refused cell names the D409 item of its code and kind,
  and the kind is D409's one definition: ``stricter`` where the reference reports no error,
  ``same outcome`` where it errs or crashes. An accepted cell with errors names, for each, the
  exit it falls under (``EXITS``). Every item of D409's clinical files is named by a cell, and the
  cell each item gives as an example names it.

The recorded outcomes are data: what is asserted is that aibi's refusals and accepts are explained
by D409 against them. The runs themselves, and their logs, stay outside the repository.
"""

import functools
import hashlib
import json
import re
import string
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple
from unittest import mock

import pytest

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import Refused

HERE = Path(__file__).resolve().parent
SPEC = HERE.parents[3] / "SPEC.md"
RECORDED = json.loads((HERE / "clinical_reference.json").read_text(encoding="utf-8"))
ALPHABET = string.digits + string.ascii_letters
DOMAINS = ("hdr2", "hdr3", "hdr4", "hdr4b", "cells", "excel")

WHOLE: Mapping[str, str] = {
    "00_control": "ok",
    "01_dup_attr_patient": "DUPLICATE_ATTRIBUTE",
    "02_dup_attr_sample": "DUPLICATE_ATTRIBUTE",
    "03_dup_key_col_sample": "DUPLICATE_ATTRIBUTE",
    "04_datatype_double_hash": "DATATYPE_ROW",
    "05_priority_double_hash": "PRIORITY_ROW",
    "06_label_double_hash": "ok",
    "07_label_only_hashes": "ok",
    "08_repeated_header_sample": "REPEATED_HEADER",
    "09_bom_sample": "ok",
    "10_mixed_eol_patient": "ok",
    "11_cr_only_patient": "ok",
    "12_trailing_blank_line": "BLANK_LINE",
    "13_nbsp_only_line": "BLANK_LINE",
    "14_tab_hash_line": "COMMENT_LINE",
    "15_space_hash_line": "COMMENT_LINE",
    "16_label_NA": "TEXT_VALUE",
    "17_def_ideographic_space": "TEXT_VALUE",
    "18_label_Pending": "ok",
    "19_bool_TRUE_sp": "ok",
    "19_bool_true": "BOOLEAN_VALUE",
    "19_bool_Yes": "BOOLEAN_VALUE",
    "19_bool_NA": "ok",
    "19_bool_Pending": "ok",
    "19_bool_TRUE_nbsp": "BOOLEAN_VALUE",
    "19_bool_NA_nbsp": "BOOLEAN_VALUE",
    "19_bool_ideo": "BOOLEAN_VALUE",
    "20_os_Pending": "ok",
    "20_os_Dead": "SURVIVAL_STATUS",
    "20_os_NA_nbsp": "SURVIVAL_STATUS",
    "20_os_pad": "ok",
    "20_os_NotAvail": "ok",
    "21_pfs_x_patient": "SURVIVAL_STATUS",
    "22_pfs_x_sample": "ok",
    "23_pfs_1colon_patient": "ok",
    "24_pkey_NA": "KEY_MISSING",
    "24_pkey_Pending": "ok",
    "24_pkey_null": "ok",
    "24_pkey_hash": "KEY_CHARACTERS",
    "24_pkey_pad": "PADDED_KEY",
    "24_pkey_nbsp": "KEY_CHARACTERS",
    "24_pkey_excel": "EXCEL_DATE",
    "25_sid_64": "SAMPLE_ID_LENGTH",
    "26_sid_63": "ok",
    "27_excel_string": "EXCEL_DATE",
    "28_excel_date_col": "ok",
    "29_excel_number": "EXCEL_DATE",
    "30_excel_padded": "EXCEL_DATE",
    "31_prio_space1": "PRIORITY_ROW",
    "31_prio_plus1": "PRIORITY_ROW",
    "31_prio_under": "PRIORITY_ROW",
    "31_prio_m2": "PRIORITY_ROW",
    "31_prio_big": "PRIORITY_ROW",
    "31_prio_arabic": "PRIORITY_ROW",
    "32_type_lower": "DATATYPE_ROW",
    "33_key_type_number": "ok",
    "34_os_months_string": "PREDEFINED_ATTRIBUTE",
    "35_sample_type_in_patient": "PREDEFINED_ATTRIBUTE",
    "36_mutation_count": "BANNED_ATTRIBUTE",
    "37_attr_both": "PREDEFINED_ATTRIBUTE",
    "38_name_lower": "ATTRIBUTE_NAME",
    "39_name_digits": "ATTRIBUTE_NAME",
    "40_name_trailing_space": "ok",
    "41_field_131072": "ok",
    "42_field_131073": "LONG_FIELD",
    "43_num_gt": "ok",
    "43_num_under": "ok",
    "43_num_inf": "ok",
    "43_num_nan": "ok",
    "43_num_arabic": "ok",
    "43_num_NotAvail": "ok",
    "43_num_hex": "ok",
    "44_row_width_extra_tab": "ROW_WIDTH",
    "45_dangling_sample": "ok",
    "46_tcga_dup": "core:KEY_NOT_UNIQUE",
    "47_no_data_rows_patient": "ok",
    "48_quote_cells": "ok",
    "49_u2028_cell": "ok",
    "50_u0085_cell": "ok",
    "51_five_hash_rows": "HEADER_ROWS",
    "52_formfeed_cell": "CONTROL_CHARACTER",
    "53_crcrlf": "BLANK_LINE",
    "54_empty_name": "ATTRIBUTE_NAME",
    "55_repeated_header_patient_padded": "REPEATED_HEADER",
    "60_sid_notavail": "KEY_MISSING",
    "61_no_sample_id_col": "NO_KEY_COLUMN",
    "62_sample_id_in_patient": "SAMPLE_ID_IN_PATIENT_FILE",
    "63_sample_zero_rows": "ok",
    "64_os_LIVING_with_months": "ok",
    "65_dfs_padded": "ok",
    "66_row3_short": "HEADER_ROW_WIDTH",
    "67_nonascii_upper_name": "ok",
    "68_tabs_short_line": "PREDEFINED_ATTRIBUTE",
    "69_key_name_padded": "NO_KEY_COLUMN",
    "70_hash_inside_name": "ok",
    "71_second_bom_in_cell": "ok",
    "72_type_key_bad": "DATATYPE_ROW",
    "73_prio_key_bad": "PRIORITY_ROW",
    "74_label_key_NA": "TEXT_VALUE",
    "75_num_padded_nbsp": "ok",
    "76_pid_sample_excel_notdate": "EXCEL_DATE",
    "80_not_utf8_cell": "NOT_UTF8",
    "81_comment_line": "COMMENT_LINE",
    "82_not_utf8_label": "NOT_UTF8",
    "83_surrogate_bytes": "NOT_UTF8",
    "85_single_column_patient_na": "KEY_MISSING",
    "86_space_hash_nonkey_first": "COMMENT_LINE",
    "86_repeated_header_prefix_field": "ok",
    "86_tab_hash_nonkey_second": "COMMENT_LINE",
    "87_name_nonchar": "ATTRIBUTE_NAME",
    "88_name_4097": "ATTRIBUTE_NAME",
    "89_noncharacter_data_file": "DATA_FILE",
    "90_missing_forms_65": "MISSING_FORM",
    "91_missing_forms_64": "MISSING_FORM",
    "92_missing_form_65_chars": "MISSING_FORM",
    "93_missing_form_64_chars": "ok",
    "94_attr_both_plain": "ATTRIBUTE_IN_BOTH_FILES",
    "95_single_column_patient_dup": "core:KEY_NOT_UNIQUE",
    "96_patient_dup": "core:KEY_NOT_UNIQUE",
    "97_sample_dup": "core:KEY_NOT_UNIQUE",
    "98_dfs_generic_shape": "ok",
    "99_many_columns": "TOO_MANY_COLUMNS",
    "00_control@cells": "TOO_MANY_CELLS",
}
"""aibi's outcome on each whole study, written by hand from the plan's rules: ``ok`` (published),
a pack code, or ``core:<code>`` for the gate's refusal."""

EXITS: Mapping[str, str] = {
    "cascade": "13",
    "line-ends": "C42",
    "boolean": "C43",
    "survival-status": "C43",
    "number": "C43",
}
"""The exit each error of an accepted cell falls under, by its identity, written by hand from
D409: (13) a byte order mark on an imported file, the only cause an accepted cell's cascade has
(cause rule 2); (C42) mixed line ends, read without loss; (C43) a cell aibi counts by reason, a
Java null word outside the validator's list in a ``NUMBER``, ``BOOLEAN`` or status column, or a
``NUMBER`` cell ``float()`` refuses."""


class Outcome(NamedTuple):
    """A cell's outcome: aibi's (``code``: ``None`` when it accepts) and the reference's errors
    (``()`` when it reports none)."""

    code: str | None
    errors: tuple[str, ...]


def recorded(domain: str, ids: list[str]) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Each cell's recorded errors, in the order the domain's generator makes the cells."""
    found = RECORDED[domain]
    assert found["cells"] == len(ids)
    hashed = hashlib.sha256("\n".join(ids).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    assert found["ids"] == hashed, domain
    for cell, mark in zip(ids, found["outcomes"], strict=True):
        yield cell, () if mark == "." else tuple(found["errors"][ALPHABET.index(mark)])


def read(clinical: ModuleType, patient: bytes, sample: bytes) -> str | None:
    """The pack's reader on a patient and a sample file: ``None``, or its refusal's code."""
    limits = ImportLimits()
    try:
        found = clinical.read(patient, "data_clinical_patient.txt", "patient", limits)
        clinical.read(
            sample,
            "data_clinical_sample.txt",
            "sample",
            limits,
            cells_before=found.cells,
            patient=found,
        )
    except Refused as error:
        (refusal,) = error.refusals
        return str(refusal.code).removeprefix("onco.") if type(error) is Refused else "subclass"
    return None


@pytest.fixture(scope="module")
def outcomes(corpus: ModuleType) -> dict[str, tuple[Outcome, str | None]]:
    """Each header and data cell (``<domain>:<cell>``): aibi's outcome with the reference's
    errors, and the plan's code."""
    import importlib

    clinical = importlib.import_module("aibi.packs.onco.clinical")
    found: dict[str, tuple[Outcome, str | None]] = {}
    for domain in ("hdr3", "hdr4", "hdr4b"):
        cells = getattr(corpus, domain)()
        for cell, errors in recorded(domain, list(cells)):
            row, col, raw = cells[cell]
            got = read(clinical, corpus.header_file(row, col, raw), corpus.GOOD_S)
            found[f"{domain}:{cell}"] = (
                Outcome(got, errors),
                corpus.expected_header(row, col, raw),
            )
    files = corpus.hdr2()
    for cell, errors in recorded("hdr2", list(files)):
        got = read(clinical, files[cell], corpus.GOOD_S)
        found[f"hdr2:{cell}"] = (Outcome(got, errors), corpus.expected_hdr2(files[cell].decode()))
    for domain in ("cells", "excel"):
        cells = getattr(corpus, domain)()
        for cell, errors in recorded(domain, list(cells)):
            tag, value, patient, sample = cells[cell]
            found[f"{domain}:{cell}"] = (
                Outcome(read(clinical, patient, sample), errors),
                corpus.expected_cell(tag, value),
            )
    return found


def _imported(directory: Path, registry: Any, limits: ImportLimits) -> str | None:
    """``directory`` imported through ``PACK.importer`` into a store of its own: ``None``, or the
    first refusal's code (``core:<code>`` for the core's)."""
    from aibi.core.importers.confine import Confinement
    from aibi.core.importers.run import import_dataset
    from aibi.core.schema.pack_api import ImportOptions
    from aibi.core.store.store import Store

    store = Store(directory.parent.parent / "data")
    try:
        confinement = Confinement.of(directory.parent)
        options = ImportOptions(dataset="study", reader=confinement, limits=limits, at=store.now())
        source = confinement.confine(directory)
        import_dataset(store, source, options, "operator:Ada", registry=registry, pack="onco")
    except ImportRefused as error:
        code = str(error.refusals[0].code)
        return code.removeprefix("onco.") if code.startswith("onco.") else f"core:{code}"
    finally:
        store.close()
    return None


@pytest.fixture(scope="module")
def whole(
    corpus: ModuleType, grid: ModuleType, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Outcome]:
    """Each whole study, written beside ``clinical_grid.BASE``'s meta files (the sample meta
    naming its sample file) and imported through ``PACK.importer``."""
    import importlib

    import aibi
    from aibi.core.schema.pack_api import PackRegistry

    onco = importlib.import_module("aibi.packs.onco")
    registry = PackRegistry([onco.PACK], core_version=aibi.__version__)
    found: dict[str, Outcome] = {}
    for index, (cell, study) in enumerate(corpus.rv1().items()):
        directory = tmp_path_factory.mktemp(f"whole{index}") / "imports" / "study"
        directory.mkdir(parents=True)
        files = {name: content for name, content in grid.BASE.items() if "data_" not in name}
        files["data_clinical_patient.txt"] = study.patient
        files[study.sample_name] = study.sample
        files["meta_clinical_sample.txt"] = grid.SAMPLE_META.replace(
            b"data_clinical_sample.txt", study.sample_name.encode()
        )
        for name, content in files.items():
            (directory / name).write_bytes(content)
        got = _imported(directory, registry, ImportLimits(**(study.limits or {})))
        found[cell] = Outcome(got, tuple(RECORDED["rv1"][cell]["errors"]))
    return found


def test_the_corpus_is_every_recorded_cell(corpus: ModuleType) -> None:
    assert set(corpus.rv1()) == set(RECORDED["rv1"]) == set(WHOLE)
    assert len(WHOLE) == 123
    sizes = {domain: RECORDED[domain]["cells"] for domain in DOMAINS}
    assert sizes == {
        "hdr2": 280,
        "hdr3": 2940,
        "hdr4": 1440,
        "hdr4b": 170,
        "cells": 1938,
        "excel": 1045,
    }
    for domain in DOMAINS:
        list(recorded(domain, list(getattr(corpus, domain)())))


def test_aibi_s_code_is_the_plan_s_on_every_cell(
    outcomes: dict[str, tuple[Outcome, str | None]], whole: dict[str, Outcome]
) -> None:
    """The per-cell assertion: aibi's code is the plan's predicate's, cell by cell."""
    wrong = [(cell, got.code, want) for cell, (got, want) in outcomes.items() if got.code != want]
    assert wrong == []
    for cell, got in whole.items():
        assert (got.code or "ok") == WHOLE[cell], cell


# --- D409's clinical files: its items, and the cells that are instances of each -------------------

ITEM = re.compile(r"\((C\d+)\) (stricter|same outcome|exit 1|exit 2): \[([^\]]+)\] ")
EXAMPLE = re.compile(r"\(e\.g\. cells? ([^)]*)\)")


class Items(NamedTuple):
    refusals: dict[tuple[str, str], str]
    """Each refusal item's number, by its code and kind."""
    exits: dict[str, str]
    """Each exit item's kind, by its number."""
    examples: dict[str, tuple[str, ...]]
    main_exits: frozenset[str]
    """The numbers of D409's main exit 1 items, which the clinical files use too."""


@functools.cache
def d409() -> Items:
    (row,) = [line for line in SPEC.read_text().splitlines() if line.startswith("| D409 |")]
    section = row.partition("**Clinical files**")[2]
    assert section, "D409 has no clinical files section"
    found = list(ITEM.finditer(section))
    refusals: dict[tuple[str, str], str] = {}
    exits: dict[str, str] = {}
    examples: dict[str, tuple[str, ...]] = {}
    ends = [m.start() for m in found[1:]] + [len(section)]
    for match, end in zip(found, ends, strict=True):
        number, kind, tag = match[1], match[2], match[3]
        if kind.startswith("exit"):
            exits[number] = kind
        else:
            assert (tag, kind) not in refusals, (tag, kind)
            refusals[(tag, kind)] = number
        listed = EXAMPLE.findall(section[match.end() : end])
        examples[number] = tuple(cell for text in listed for cell in text.split(", "))
    main = row.partition("Exit 1: ")[2].partition(" P: ")[0]
    numbers = [m[1] for m in found]
    assert numbers == [f"C{n}" for n in range(1, len(numbers) + 1)]
    return Items(refusals, exits, examples, frozenset(re.findall(r"\((\d+)\) ", main)))


def named(outcome: Outcome) -> str | None:
    """The D409 item a refused cell is an instance of: its code's item of D409's one definition of
    the kind (``stricter`` where the reference reports no error)."""
    if outcome.code is None:
        return None
    kind = "same outcome" if outcome.errors else "stricter"
    return d409().refusals.get((outcome.code, kind))


def every_cell(
    outcomes: dict[str, tuple[Outcome, str | None]], whole: dict[str, Outcome]
) -> dict[str, Outcome]:
    return {
        **{cell: got for cell, (got, _) in outcomes.items()},
        **{f"rv1:{c}": o for c, o in whole.items()},
    }


def test_every_refused_cell_names_its_d409_item(
    outcomes: dict[str, tuple[Outcome, str | None]], whole: dict[str, Outcome]
) -> None:
    unnamed = sorted(
        {
            (got.code, "same outcome" if got.errors else "stricter")
            for got in every_cell(outcomes, whole).values()
            if got.code is not None and named(got) is None
        },
        key=str,
    )
    assert unnamed == []


def test_every_accepted_cell_s_errors_are_exits(
    outcomes: dict[str, tuple[Outcome, str | None]], whole: dict[str, Outcome]
) -> None:
    """An accepted cell's errors each fall under an exit of D409 (``EXITS``): every entry of the
    map is an exit item of D409's clinical files or of its main list, and witnessed."""
    items = d409()
    carried: set[str] = set()
    for cell, got in every_cell(outcomes, whole).items():
        if got.code is not None:
            continue
        for error in got.errors:
            assert error in EXITS, (cell, error)
            carried.add(error)
    for identity, exit_ in EXITS.items():
        assert exit_ in items.exits or exit_ in items.main_exits, identity
        assert identity in carried, identity


@pytest.fixture(scope="module")
def inputs(corpus: ModuleType) -> dict[str, tuple[bytes, ...]]:
    """The files of each cell (``<domain>:<cell>``), as ``outcomes`` and ``whole`` read them."""
    found: dict[str, tuple[bytes, ...]] = {}
    for domain in ("hdr3", "hdr4", "hdr4b"):
        for cell, (row, col, raw) in getattr(corpus, domain)().items():
            found[f"{domain}:{cell}"] = (corpus.header_file(row, col, raw), corpus.GOOD_S)
    for cell, data in corpus.hdr2().items():
        found[f"hdr2:{cell}"] = (data, corpus.GOOD_S)
    for domain in ("cells", "excel"):
        for cell, (_, _, patient, sample) in getattr(corpus, domain)().items():
            found[f"{domain}:{cell}"] = (patient, sample)
    for cell, study in corpus.rv1().items():
        found[f"rv1:{cell}"] = (study.patient, study.sample)
    return found


BOM = b"\xef\xbb\xbf"
COUNTED: Mapping[str, tuple[str, str, str]] = {
    "cb_bool": ("patients", "flag", "boolean"),
    "cb_os": ("patients", "os_status", "survival-status"),
    "cb_excel_num": ("patients", "age", "number"),
}
"""For each ``cells`` domain with an accepted cell the reference errs on: the table and column it
is published as, and the identity of the reference's error."""
COUNTED_WHOLE: Mapping[str, tuple[str, str, str]] = {
    "19_bool_Pending": ("patients", "flag", "Pending"),
    "20_os_NotAvail": ("patients", "os_status", "Not Available"),
    "20_os_Pending": ("patients", "os_status", "Pending"),
    "43_num_NotAvail": ("samples", "tmb", "Not Available"),
    "43_num_hex": ("samples", "tmb", "0x10"),
}
"""The same for the whole studies: the table, the column and the cell's value."""


def _published(
    root: Path, registry: Any, files: Mapping[str, bytes]
) -> tuple[dict[str, Any], list[Any]]:
    """A study's ``files`` imported through ``PACK.importer`` into a store of their own: the
    published descriptors by id, and the notes."""
    from aibi.core.importers.confine import Confinement
    from aibi.core.importers.run import import_dataset
    from aibi.core.schema.pack_api import ImportOptions
    from aibi.core.store.store import Store

    directory = root / "imports" / "study"
    directory.mkdir(parents=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)
    store = Store(root / "data")
    try:
        confinement = Confinement.of(directory.parent)
        options = ImportOptions(
            dataset="study", reader=confinement, limits=ImportLimits(), at=store.now()
        )
        source = confinement.confine(directory)
        published = import_dataset(
            store, source, options, "operator:Ada", registry=registry, pack="onco"
        )
        return {d.id: d for d in store.descriptors(published.manifest)}, list(published.notes)
    finally:
        store.close()


def test_every_accepted_cell_the_reference_errs_on_is_counted(
    outcomes: dict[str, tuple[Outcome, str | None]],
    whole: dict[str, Outcome],
    inputs: dict[str, tuple[bytes, ...]],
    corpus: ModuleType,
    grid: ModuleType,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The exits are not excused by an error's identity alone (D409 (13), (C42), (C43)): a cell
    the reference errs on and aibi accepts is shown to be what its exit says, per cell. A
    ``cascade`` is a byte order mark at the start of one of the cell's files; ``line-ends``, mixed
    line ends in one of them; and a ``boolean``, ``survival-status`` or ``number`` error is on a
    cell the published column counts by reason, as one of its ``missing_codes`` (the fixed list
    and the column's additions as the reader gives them) or, in a ``NUMBER`` column, as
    ``unparsed`` (by a real import through the pack and the core): the cell's value is read from
    the cell."""
    import importlib

    import aibi
    from aibi.core.schema.pack_api import PackRegistry

    onco = importlib.import_module("aibi.packs.onco")
    registry = PackRegistry([onco.PACK], core_version=aibi.__version__)
    cells = {domain: getattr(corpus, domain)() for domain in ("cells", "excel")}
    counted: list[tuple[str, str, str, str, str]] = []  # cell, table, column, value, identity
    seen: set[str] = set()
    for cell, got in every_cell(outcomes, whole).items():
        if got.code is not None:
            continue
        domain, _, name = cell.partition(":")
        for error in got.errors:
            seen.add(error)
            if error == "cascade":
                assert any(file.startswith(BOM) for file in inputs[cell]), cell
            elif error == "line-ends":
                assert any(
                    b"\r\n" in file and re.search(rb"(?<!\r)\n", file) for file in inputs[cell]
                ), cell
            elif domain == "rv1":
                table, column, value = COUNTED_WHOLE[name]
                assert value.encode() in b"".join(inputs[cell]), cell
                counted.append((cell, table, column, value, error))
            else:
                tag, value, _, _ = cells[domain][name]
                table, column, identity = COUNTED[tag]
                assert identity == error, cell
                counted.append((cell, table, column, value, error))
    assert seen == set(EXITS)
    wholes = {cell.removeprefix("rv1:") for cell, *_ in counted if cell.startswith("rv1:")}
    assert wholes == set(COUNTED_WHOLE)
    assert len(counted) == 8 + len(COUNTED_WHOLE)
    clinical = importlib.import_module("aibi.packs.onco.clinical")
    missing = importlib.import_module("aibi.packs.onco.missing")
    for index, (cell, table, column, value, error) in enumerate(counted):
        patient, sample = inputs[cell]
        role = "patient" if table == "patients" else "sample"
        data = patient if table == "patients" else sample
        columns = {
            c.name.lower(): c for c in clinical.read(data, "f.txt", role, ImportLimits()).columns
        }
        declared = {**missing.FIXED, **columns[column].additions}
        if error == "number" and value not in declared:
            # a NUMBER cell float() refuses: the core's counted ``unparsed``, by a real import
            files = {n: c for n, c in grid.BASE.items() if "data_" not in n}
            files["data_clinical_patient.txt"] = patient
            files["data_clinical_sample.txt"] = sample
            found, notes = _published(tmp_path_factory.mktemp(f"counted{index}"), registry, files)
            descriptor = found[f"{table}.{column}"]
            assert descriptor.fields.missing_codes is not None
            assert value not in descriptor.fields.missing_codes, cell
            unparsed = [n for n in notes if n.kind == "unparsed" and n.subject == descriptor.id]
            assert unparsed, cell
        else:
            assert value in declared, cell  # a Java null word: a missing code, counted by reason


def test_every_item_is_named_by_a_cell_and_its_examples_name_it(
    outcomes: dict[str, tuple[Outcome, str | None]], whole: dict[str, Outcome]
) -> None:
    items = d409()
    cells = every_cell(outcomes, whole)
    naming: dict[str, set[str]] = {}
    for cell, got in cells.items():
        item = named(got)
        if item is not None:
            naming.setdefault(item, set()).add(cell)
        elif got.code is None:
            for error in got.errors:
                naming.setdefault(EXITS[error], set()).add(cell)
    for number in [*items.refusals.values(), *items.exits]:
        assert naming.get(number), number
        assert items.examples[number], number
        for example in items.examples[number]:
            assert example in naming[number], (number, example)


def test_the_core_s_parse_reads_every_accepted_cell_as_the_reader_does(
    corpus: ModuleType,
) -> None:
    """The parse differential over the corpus: every file the reader accepts, the core's
    ``parse_text`` reads with the same names and the same cells, every field of every row (a
    test-only hook on the reader's ``counted`` sees them; the pack holds the reader to the parse
    instead of parsing each file a second time)."""
    import importlib

    from aibi.core.store.sources import parse_text

    clinical = importlib.import_module("aibi.packs.onco.clinical")
    files: list[bytes] = [corpus.GOOD_S, *corpus.hdr2().values()]
    for domain in ("hdr3", "hdr4", "hdr4b"):
        files += [corpus.header_file(*cell) for cell in getattr(corpus, domain)().values()]
    files += [pair for _, _, *found in corpus.cells().values() for pair in found]
    files += [file for study in corpus.rv1().values() for file in (study.patient, study.sample)]
    compared = 0
    for data in files:
        names = data.split(b"\n", 5)[4] if data.count(b"\n") >= 5 else b""
        role = "sample" if b"SAMPLE_ID" in names.split(b"\t") else "patient"
        seen: list[tuple[str, ...]] = []
        original = clinical._Reading.counted

        def counted(self: Any, row: int, fields: list[str], *, seen: list[tuple[str, ...]] = seen,
                    original: Any = original) -> None:  # fmt: skip
            seen.append(tuple(fields))
            original(self, row, fields)

        try:
            with mock.patch.object(clinical._Reading, "counted", counted):
                found = clinical.read(data, "f.txt", role, ImportLimits())
        except Refused:
            continue
        parsed = parse_text(data, clinical.SETTINGS)
        assert list(parsed.names) == [column.name for column in found.columns]
        assert len(parsed.rows) == found.rows
        assert [tuple(row) for row in parsed.rows] == seen
        compared += 1
    assert compared > 3_000


def test_a_cell_the_reference_gives_two_identities_is_recorded_with_both(
    outcomes: dict[str, tuple[Outcome, str | None]],
) -> None:
    """A Java cell that is an Excel date in a ``NUMBER`` column or a key is two errors of the
    reference's (the date, and the number or the characters): the record holds both, as the live
    run does, and every cell aibi refuses as an Excel date has the date among its errors."""
    refused = {cell: got for cell, (got, _) in outcomes.items() if got.code == "EXCEL_DATE"}
    assert refused
    assert all("excel-date" in got.errors for got in refused.values())
    two = {
        cell: got.errors
        for cell, (got, _) in outcomes.items()
        if cell.startswith("cells:") and len(got.errors) == 2
    }
    assert len(two) == 59
    assert set(two.values()) == {
        ("excel-date", "number"),
        ("excel-date", "id-characters"),
    }
    assert {cell.partition(":")[2].partition(":")[0] for cell in two} == {
        "cb_excel_num",
        "cb_pid",
        "cb_sid",
    }
