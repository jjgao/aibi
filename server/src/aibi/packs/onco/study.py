"""Discovering a cBioPortal study in a directory (SPEC D409): its meta files, its study meta's
values and its clinical meta files' data files (M4.2a-1a).

``discover`` is the pack's one way into a study. It lists the directory once, through the
import's reader; reads only the study's meta files (Meta(S): the entries that are meta-named and
not excluded), each with ``reader.read(path, META_MOST)``; and resolves only the clinical role, a
clinical meta's ``data_filename``, to an entry of that listing, by name: nothing is read or opened
through a path made from text the study holds. A data file is returned as its listing entry, for
M4.2a-1b to read.

**The open exception (D409).** No other path value is resolved here: not a non-clinical meta
file's ``data_filename`` or ``pd_annotations_filename``, nor the study meta's ``tags_file``.
Where cBioPortal, through such a value, reads a file aibi imports, or fails on the tags file's
name, discover accepts a study cBioPortal refuses. So no import reaches ``discover`` until
M4.2a-1a-paths has merged and added ``PATH_SITES`` here: the pack's ``PACK`` has no importer, and
``tests/packs/onco/test_reachability.py`` fails if it gains one first.

**Totality.** ``discover`` returns a ``Study``, raises ``Refused`` with one of
``refusals.CODES``, or lets the reader's ``ImportRefused`` out as the instance the reader raised.
The one reader refusal it takes is the ``LIMIT_EXCEEDED`` of a meta file's read, while the
operator's ``import_bytes`` is over ``META_MOST``, so that the limit was the pack's own: that is
``META_FILE``.

**The order of the checks.** They run in phases, and the first failure of the earliest phase is
the one refused; within a phase, the order is that of the entries' names, the one listing sorted
by name:

1. more than ``META_FILES_MOST`` meta-named entries (``TOO_MANY_FILES``), before any read;
2. each meta file in turn: its read, its parse, its type and its mandatory keys;
3. each meta file's ``stable_id`` in turn: its form, then that no earlier file holds it;
4. the study meta: none, or a second;
5. the study id of every meta file against the study meta's;
6. the clinical metas: a second of a kind, then no sample meta;
7. each clinical meta's data file, the metas in the order of their names, then two metas naming
   one entry;
8. the study meta's values.

**What it says.** A refusal or a note names an entry as ``data`` only (``refusals.shown``), and
never a value a file holds. Every entry discover does not import gets one ``skipped_source``
note, at most ``NOTES_MOST`` of them and then one that counts the rest.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import Segment, text
from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry, ImportNote, SourceReader
from aibi.core.schema.refusals import RefusalCode
from aibi.packs.onco.meta import (
    CLINICAL,
    META_MOST,
    PATIENT,
    SAMPLE,
    STUDY,
    StudyFields,
    check_mandatory,
    classify,
    excluded,
    file_name,
    meta_named,
    parse,
    stable_id,
    study_fields,
)
from aibi.packs.onco.refusals import entry_refused, refused, shown

META_FILES_MOST = 256
"""The most meta-named entries a study may hold. Stricter: cBioPortal reads any number (D409)."""
NOTES_MOST = 200
"""The most skip notes that name an entry; one more counts the rest."""

_KIND_REASONS: Mapping[str, str] = {
    "symlink": "is a symlink",
    "directory": "is a directory",
    "other": "is not a regular file",
    "unconfined": "cannot be confined",
}
"""Why a meta-named entry that is not a regular file is ``META_FILE``."""


@dataclass(frozen=True)
class ClinicalMeta:
    """A clinical meta file, by its entry's name, and its data file, a listed regular file."""

    meta: str
    data: DirectoryEntry


@dataclass(frozen=True)
class Study:
    """What ``discover`` found: the study meta's values, the clinical metas and the notes."""

    fields: StudyFields
    patient: ClinicalMeta | None
    sample: ClinicalMeta
    notes: tuple[ImportNote, ...]


def _read(reader: SourceReader, entry: DirectoryEntry, limits: ImportLimits) -> dict[str, str]:
    """The meta file ``entry``, read and parsed; ``META_FILE`` for an entry that is not a
    regular file, or that is larger than ``META_MOST`` while the operator's limit is larger."""
    if entry.kind != "file" or entry.path is None:
        reason = _KIND_REASONS.get(entry.kind, "cannot be confined")
        raise entry_refused("META_FILE", entry.name, reason)
    try:
        raw = reader.read(entry.path, META_MOST)
    except ImportRefused as error:
        mine = limits.import_bytes > META_MOST and bool(error.refusals)
        if not (mine and all(r.code == RefusalCode.LIMIT_EXCEEDED for r in error.refusals)):
            raise
        raise entry_refused("META_FILE", entry.name, "is larger than a meta file may be") from None
    return parse(raw, entry.name)


def _clinical_data(
    name: str,
    found: Mapping[str, str],
    listed: Mapping[str, DirectoryEntry],
    metas: frozenset[str],
) -> DirectoryEntry:
    """The data file the clinical meta ``name`` names: its ``data_filename``, through P0
    (``meta.file_name``), must be exactly the name of a listed regular file outside Meta(S).
    There is no case or normalisation fold, and nothing is followed."""
    normal = file_name(found["data_filename"])
    if normal is None:
        raise entry_refused(
            "DATA_FILE", name, "has a data_filename that is not a file name M4.2a accepts"
        )
    entry = None if "/" in normal else listed.get(normal)
    if entry is not None and entry.kind == "symlink":
        raise entry_refused("DATA_FILE", name, "has a data_filename that is a symlink")
    if entry is None or entry.kind != "file":
        raise entry_refused(
            "DATA_FILE",
            name,
            "has a data_filename that names no regular file in the study's directory",
        )
    if entry.name in metas:
        raise entry_refused("DATA_FILE", name, "has a data_filename that names a meta file")
    return entry


def _skip_reason(entry: DirectoryEntry, why: Mapping[str, str]) -> str:
    """Why the entry is not imported, as its skip note says it. No reason says what names it."""
    if entry.name in why:
        return why[entry.name]
    if entry.kind == "directory":
        if entry.name == "case_lists":
            return "a subdirectory (case lists are M4.2b's)"
        return "a subdirectory"
    if entry.kind == "symlink":
        return "a symlink"
    if entry.kind != "file":
        return "not a regular file"
    return "a file M4.2a does not read"


def _notes(
    entries: Sequence[DirectoryEntry], used: frozenset[str], why: Mapping[str, str]
) -> list[ImportNote]:
    notes: list[ImportNote] = []
    skipped = 0
    for entry in entries:
        if entry.name in used:
            continue
        skipped += 1
        if skipped <= NOTES_MOST:
            message: list[Segment] = [text(_skip_reason(entry, why) + ": "), *shown(entry.name)]
            notes.append(ImportNote("skipped_source", None, message))
    if skipped > NOTES_MOST:
        rest = [text("further entries M4.2a does not read")]
        notes.append(ImportNote("skipped_source", None, rest, count=skipped - NOTES_MOST))
    return notes


def discover(reader: SourceReader, source: ConfinedPath, limits: ImportLimits) -> Study:
    """The study in the directory ``source`` (D409; the module's docstring says what it reads and
    what it refuses)."""
    entries = sorted(reader.files(source), key=lambda entry: entry.name)
    listed = {entry.name: entry for entry in entries}
    why: dict[str, str] = {}
    candidates: list[DirectoryEntry] = []
    for entry in entries:
        reason = excluded(entry.name)
        if reason is not None:
            why[entry.name] = reason
        elif meta_named(entry.name):
            candidates.append(entry)
    if len(candidates) > META_FILES_MOST:
        raise refused(
            "TOO_MANY_FILES",
            text("The study's directory holds more meta-named entries than M4.2a reads"),
        )
    metas: dict[str, tuple[str, dict[str, str]]] = {}
    for entry in candidates:
        found = _read(reader, entry, limits)
        kind = classify(found, entry.name)
        check_mandatory(kind, found, entry.name)
        metas[entry.name] = (kind, found)

    stable_ids: set[str] = set()
    for name, (_, found) in metas.items():
        if "stable_id" not in found:
            continue
        stable = stable_id(found["stable_id"])
        if stable is None:
            raise entry_refused("META_VALUE", name, "holds a stable_id that M4.2a does not accept")
        if stable in stable_ids:
            raise entry_refused(
                "DUPLICATE_STABLE_ID", name, "repeats another meta file's stable_id"
            )
        stable_ids.add(stable)

    studies = [name for name, (kind, _) in metas.items() if kind == STUDY]
    if not studies:
        raise refused("NO_STUDY_META", text("No meta file in the study's directory is a study's"))
    if len(studies) > 1:
        raise entry_refused("DUPLICATE_STUDY_META", studies[1], "is a second study meta file")
    study_name = studies[0]
    study = metas[study_name][1]
    identifier = study["cancer_study_identifier"]
    for name, (_, found) in metas.items():
        if found.get("cancer_study_identifier", identifier) != identifier:
            raise entry_refused("STUDY_MISMATCH", name, "names another study than the study meta")

    clinical: dict[str, str] = {}
    for name, (kind, _) in metas.items():
        if kind in CLINICAL:
            if kind in clinical:
                raise entry_refused(
                    "DUPLICATE_CLINICAL_META", name, "is a second clinical meta file of its kind"
                )
            clinical[kind] = name
    if SAMPLE not in clinical:
        raise refused("NO_SAMPLE_META", text("No meta file in the study's directory is a sample's"))

    in_meta = frozenset(entry.name for entry in candidates)
    data: dict[str, DirectoryEntry] = {}
    for kind, name in sorted(clinical.items(), key=lambda item: item[1]):
        data[kind] = _clinical_data(name, metas[name][1], listed, in_meta)
    if PATIENT in data and data[PATIENT].name == data[SAMPLE].name:
        raise entry_refused(
            "DATA_FILE",
            clinical[PATIENT],
            "has a data_filename that names the other clinical meta file's data file",
        )

    fields = study_fields(study, study_name)
    used = frozenset({study_name, *clinical.values(), *(entry.name for entry in data.values())})
    for name in metas:
        why.setdefault(name, "a meta file of a type M4.2a does not import")
    notes = _notes(entries, used, why)
    if "tags_file" in study:
        message = [text("the study's tags file is not read by M4.2a")]
        notes.append(ImportNote("not_proposed", None, message))
    patient = ClinicalMeta(clinical[PATIENT], data[PATIENT]) if PATIENT in data else None
    return Study(fields, patient, ClinicalMeta(clinical[SAMPLE], data[SAMPLE]), tuple(notes))


__all__ = ["META_FILES_MOST", "NOTES_MOST", "ClinicalMeta", "Study", "discover"]
