"""The oncology pack's importer of a cBioPortal study directory (SPEC §10.1, §13.1, D409, M4.2a-1b).

``OncoImporter.import_source`` is ``PACK.importer``'s: it discovers the study (``study.discover``,
D409's phases, which refuse or let the reader's refusal out), reads the patient file, when the
study has one, then the sample file, each once through the import's reader at the operator's
``import_bytes`` and by its listing entry (``clinical.read``), and describes them
(``build.describe``). Every note, discovery's first, goes through one ``notes.Budget``. It returns
the importer's result, which the core checks (``importers.checks``), or raises ``Refused`` with one
of ``refusals.CODES``; what the reader refuses passes out as the reader's instance.

A clinical data file's name becomes its table's ``original_name``, a descriptor's string: one that
is not Unicode text (a noncharacter, which a meta file's UTF-8 value can hold) is refused,
``DATA_FILE`` naming the meta file (stricter than cBioPortal, D409). The dataset's ``source``
records the reader's location of the study's directory where it is Unicode text of at most a
descriptor string's characters; where it is not, the dataset records no source and a note says so.
"""

import os

from aibi.core.schema.jsonio import is_text
from aibi.core.schema.limits import MAX_STRING
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import (
    ConfinedPath,
    ImportNote,
    ImportOptions,
    ImportResult,
    SourceReader,
)
from aibi.packs.onco.build import describe
from aibi.packs.onco.clinical import Clinical, Role, read
from aibi.packs.onco.facets import PACK_ID
from aibi.packs.onco.notes import Budget
from aibi.packs.onco.refusals import entry_refused
from aibi.packs.onco.study import ClinicalMeta, discover

VERSION = "0.2.0"
"""The pack's version, which its manifest states and its curation entries name."""
BY = f"importer:{PACK_ID}@{VERSION}"
NO_LOCATION = (
    "the study's directory has a name that is not Unicode text or is too long, so the dataset "
    "records no source location"
)


def _file(
    reader: SourceReader,
    clinical: ClinicalMeta,
    role: Role,
    options: ImportOptions,
    cells_before: int,
    patient: Clinical | None,
) -> Clinical:
    entry = clinical.data
    if entry.path is None or len(entry.name) > MAX_STRING or not is_text(entry.name):
        raise entry_refused(
            "DATA_FILE",
            clinical.meta,
            "has a data_filename that names a file whose name is not Unicode text",
        )
    data = reader.read(entry.path, options.limits.import_bytes)
    return read(data, entry.name, role, options.limits, cells_before=cells_before, patient=patient)


class OncoImporter:
    """``PACK.importer``: a cBioPortal study directory's clinical files (the module's
    docstring)."""

    def import_source(self, source: ConfinedPath, options: ImportOptions) -> ImportResult:
        reader = options.reader
        study = discover(reader, source, options.limits)
        files: list[Clinical] = []
        patient = None
        if study.patient is not None:
            patient = _file(reader, study.patient, "patient", options, 0, None)
            files.append(patient)
        cells = patient.cells if patient is not None else 0
        files.append(_file(reader, study.sample, "sample", options, cells, patient))
        location = reader.location(source)
        if location == ".":
            location = os.path.basename(source)
        shown = len(location) <= MAX_STRING and is_text(location) and location != ""
        described = describe(
            study.fields,
            files,
            by=BY,
            at=options.at,
            location=location if shown else None,
            name=options.name,
            previous=options.previous,
        )
        budget = Budget()
        budget.add(study.notes)
        if not shown:
            budget.add([ImportNote("not_proposed", None, [text(NO_LOCATION)])])
        budget.add(described.notes)
        return ImportResult(
            sources=described.sources,
            layouts=described.layouts,
            descriptors=described.descriptors,
            notes=budget.notes,
        )


__all__ = ["BY", "VERSION", "OncoImporter"]
