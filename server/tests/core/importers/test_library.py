"""The lending library of ``fixtures/library``, imported as a directory (SPEC §13.1, D224–D231):
what the importer detects and proposes, that a workbook reads the same in each format, and that
the same bytes give the same release."""

import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from aibi.core.schema.descriptors import (
    ColumnDescriptor,
    CoverageDescriptor,
    Descriptor,
    TableDescriptor,
)
from aibi.core.store.build import read_report
from aibi.core.store.sources import TypedSource, canonical_string, parse
from aibi.core.store.store import Store

Roots = Any
Importing = Any
FIXTURE = Path(__file__).resolve().parents[4] / "fixtures" / "library"


@pytest.fixture
def library(roots: Roots) -> Path:
    return Path(shutil.copytree(FIXTURE, roots.inside / "library"))


def _by_id(descriptors: tuple[Descriptor, ...]) -> dict[str, Any]:
    return {descriptor.id: descriptor for descriptor in descriptors}


def test_the_text_files_parse_settings_are_detected(library: Path, importing: Importing) -> None:
    found = _by_id(importing(library).built.descriptors)
    members, books = found["members"].fields.source.parse, found["books"].fields.source.parse
    assert (members.skip_rows, members.delimiter, members.encoding) == (1, ",", "utf-8")
    assert (books.format, books.encoding) == ("tsv", "cp1252")


def test_the_columns_proposals(library: Path, importing: Importing) -> None:
    found = _by_id(importing(library).built.descriptors)
    interests = found["members.interests"].fields
    assert interests.datatype == "list<category>"
    assert interests.list_syntax.model_dump() == {"format": "delimited", "delimiter": ";"}
    age = found["members.age"]
    assert (age.fields.datatype, age.fields.missing_codes) == ("integer", {"NA": "UNKNOWN"})
    assert age.curation["/fields/missing_codes"].status == "imported_default"
    assert found["members.name"].fields.identifier is True
    borrowed = found["loans_loans.borrowed"]
    assert borrowed.fields.datatype == "datetime"
    assert borrowed.curation["/fields/datatype"].status == "imported_default"
    assert found["books.genre"].fields.datatype == "category"
    assert found["shelves.capacity"].curation["/fields/datatype"].status == "imported"


def test_the_tables_relationships_and_coverage(library: Path, importing: Importing) -> None:
    found = _by_id(importing(library).built.descriptors)
    roles = {id: d.fields.role for id, d in found.items() if isinstance(d, TableDescriptor)}
    assert roles == {
        "books": "entity",
        "copies": "entity",
        "loans_loans": "event",
        "members": "entity",
        "shelves": "entity",
        "shelvings": "link",
    }
    assert found["shelvings"].fields.primary_key == ["book_id", "shelf_id"]
    assert found["shelvings"].fields.grain == "One row per book_id and shelf_id"
    assert sorted(id for id in found if id.startswith("rel:")) == [
        "rel:copies.book_id",
        "rel:loans_loans.copy_id",
        "rel:loans_loans.member_id",
        "rel:shelvings.book_id",
        "rel:shelvings.shelf_id",
    ]
    coverage = [d for d in found.values() if isinstance(d, CoverageDescriptor)]
    assert sorted(
        (d.id, d.fields.parents, d.curation["/fields/parents"].status) for d in coverage
    ) == [
        ("cov:copies.book_id", "all", "proposed"),
        ("cov:shelvings.book_id", "all", "proposed"),
        ("cov:shelvings.shelf_id", "all", "proposed"),
    ]


def test_the_report_notes_what_the_curation_queue_needs(
    library: Path, importing: Importing, store: Store
) -> None:
    built = importing(library).built
    notes = read_report(store.blobs.read(built.manifest.report or ""))
    assert [(n["kind"], n.get("subject"), n.get("count"), n.get("rows")) for n in notes] == [
        ("not_proposed", "books", None, None),
        ("renamed", "loans_loans", None, None),
        ("skipped_source", None, None, None),
        ("skipped_source", None, None, None),
        ("skipped_source", None, None, None),
        ("unparsed", "members.age", 1, [8]),
    ]
    skipped = [str(n["message"]) for n in notes if n["kind"] == "skipped_source"]
    assert ["README.md" in s for s in skipped] == [True, False, False]


def test_the_workbook_reads_the_same_as_xlsx_and_ods(roots: Roots, importing: Importing) -> None:
    xlsx = importing(
        roots.write("x/loans.xlsx", (FIXTURE / "loans.xlsx").read_bytes()), dataset="x"
    )
    ods = importing(
        roots.write("o/loans.ods", (FIXTURE / "formats" / "loans.ods").read_bytes()), dataset="o"
    )
    assert xlsx.result.sources == ods.result.sources
    assert xlsx.built.descriptors[1:] == ods.built.descriptors[1:]
    assert isinstance(xlsx.result.sources["loans"], TypedSource)


def test_the_same_bytes_give_the_same_release(
    library: Path, roots: Roots, importing: Importing
) -> None:
    first = importing(library, publish=False)
    again = importing(library)
    assert first.built.manifest.hash == again.built.manifest.hash


_WORD = re.compile(r"[A-Za-z][A-Za-z ,;'\-]{3,}")


def test_no_evidence_or_note_holds_a_cell_value(
    library: Path, importing: Importing, store: Store
) -> None:
    imported = importing(library)
    values: set[str] = set()
    for table, source in imported.result.sources.items():
        descriptor = _by_id(imported.built.descriptors)[table]
        settings = descriptor.fields.source.parse
        for row in parse(source, settings).rows:
            for value in row:
                token = canonical_string(value)
                if token and _WORD.fullmatch(token):
                    values.add(token)
    assert {"Grace", "maths", "novel", "north"} <= values
    said = [
        entry.evidence or ""
        for descriptor in imported.built.descriptors
        for entry in descriptor.curation.values()
    ]
    said.append(store.blobs.read(imported.built.manifest.report or "").decode())
    for text in said:
        for value in values:
            assert not re.search(rf"\b{re.escape(value)}\b", text), (value, text)


def test_no_field_is_asserted_and_every_status_is_the_importers(
    library: Path, importing: Importing
) -> None:
    for descriptor in importing(library).built.descriptors:
        for entry in descriptor.curation.values():
            assert entry.status != "asserted"
            assert entry.by.startswith("importer:aibi.files@")
        if isinstance(descriptor, ColumnDescriptor):
            assert descriptor.curation["/fields/source"].status == "imported"
