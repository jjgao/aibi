"""Discovering a cBioPortal study in a directory (SPEC D409): its meta files, its study meta's
values, its clinical meta files' data files and every other path value its meta files hold
(M4.2a-1a, M4.2a-1a-paths).

``discover`` is the pack's one way into a study. It lists the directory once, through the
import's reader; reads only the study's meta files (Meta(S): the entries that are meta-named and
not excluded), each with ``reader.read(path, META_MOST)``; and resolves every path value it
checks to an entry of that listing, by name: nothing is read or opened through a path made from
text the study holds. A clinical data file is returned as its listing entry, for M4.2a-1b to read.

**The path values (D409, ``PATH_SITES``).** Besides the clinical role (phase 7), three values name
files: a non-clinical meta's ``data_filename``, a CNA_DISCRETE meta's
``pd_annotations_filename`` and the study meta's ``tags_file``. None is read; each is checked
once, against Reads(S), the clinical data files and Meta(S): (P0) the value as written is one
name (``meta.one_name``); (Kind) no entry of kind ``symlink`` matches it, exactly or under
``meta.fold``; (Reads) no member of Reads(S) matches it, exactly or under ``meta.fold``
(``DATA_FILE``; ``META_VALUE`` for the tags file). Only an exact match to a listed regular file
outside Reads(S) is accepted with a note for that entry (its earlier skip reason, if it had one,
wins); any other data or PD value is a file of a kind never imported that the reference cannot
open, and every other tags value is refused. Fold only ever adds refusals. The open exception
D409 kept until M4.2a-1a-paths is closed by this; the pack's ``PACK`` still has no importer, and
``tests/packs/onco/test_reachability.py`` holds the order of the PRs that wire one.

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
8. the study meta's values;
9. each meta file's data and PD values, the metas in the order of their names, and within one
   its ``data_filename`` before its ``pd_annotations_filename`` (the order of ``PATH_SITES``);
10. the study meta's ``tags_file``.

**What it says.** A refusal or a note names an entry as ``data`` only (``refusals.shown``), and
never a value a file holds. Every entry discover does not import gets one ``skipped_source``
note, at most ``NOTES_MOST`` of them and then one that counts the rest.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import Segment, text
from aibi.core.schema.pack_api import (
    ConfinedPath,
    DirectoryEntry,
    ImportNote,
    Refused,
    SourceReader,
)
from aibi.core.schema.refusals import RefusalCode
from aibi.packs.onco.meta import (
    CLINICAL,
    MANDATORY,
    META_MOST,
    PATIENT,
    SAMPLE,
    STUDY,
    StudyFields,
    check_mandatory,
    classify,
    excluded,
    file_name,
    fold,
    meta_named,
    one_name,
    parse,
    stable_id,
    study_fields,
)
from aibi.packs.onco.refusals import Code, entry_refused, refused, shown

META_FILES_MOST = 256
"""The most meta-named entries a study may hold. Stricter: cBioPortal reads any number (D409)."""
NOTES_MOST = 200
"""The most skip notes that name an entry; one more counts the rest."""

_KIND_REASONS: Mapping[str, str] = MappingProxyType(
    {
        "symlink": "is a symlink",
        "directory": "is a directory",
        "other": "is not a regular file",
        "unconfined": "cannot be confined",
    }
)
"""Why a meta-named entry that is not a regular file is ``META_FILE``."""


DATA = "data"
"""A ``PATH_SITES`` role: a value naming a data file of a kind M4.2a never imports (phase 9)."""
TAGS = "tags"
"""A ``PATH_SITES`` role: the value naming the study's tags file (phase 10)."""
LISTING = "listing"
"""A ``PATH_SITES`` role: the listing of the study's directory that Meta(S) is taken from."""
CASE_LISTS = "case lists"
"""A ``PATH_SITES`` role: what the reference reads of ``case_lists/``, which ``discover`` does
not list (exit 3 (19); the contents are M4.2b's)."""


@dataclass(frozen=True)
class PathSite:
    """Where the reference turns text a study holds into a path it opens (or a listing it reads),
    at cbioportal-core 47890bb: the meta key or the listing (``key``), the meta file types whose
    files it is read from (``meta_types``; empty for what no meta file holds), what ``discover``
    does with it (``role``) and the reference's lines, ``V:`` and ``C:`` as ``meta.py`` names
    them, each with a paraphrase of what happens there (``cites``)."""

    key: str
    meta_types: frozenset[str]
    role: str
    cites: tuple[str, ...]


NON_CLINICAL_DATA = (
    frozenset(kind for kind, keys in MANDATORY.items() if "data_filename" in keys) - CLINICAL
)
"""The types whose meta files name a data file M4.2a never imports: every type whose mandatory
keys hold ``data_filename`` (C:95-370; all but the study meta), but the clinical ones."""

PATH_SITES: tuple[PathSite, ...] = (
    PathSite(
        "data_filename",
        NON_CLINICAL_DATA,
        DATA,
        (
            "V:4854-4856: a validator is built for each meta file whose type's field map holds "
            "the key",
            "V:398: the value is joined to the study's directory as written; an absolute value "
            "replaces it",
            "V:437: the joined path opened to probe it; only an OSError is caught and logged",
            "V:445: opened again for a UTF-8 pass",
            "V:453: opened and read by the type's validator, whatever file it names",
        ),
    ),
    PathSite(
        "pd_annotations_filename",
        frozenset({"CNA_DISCRETE"}),
        DATA,
        (
            "V:1250-1253: the discrete CNA validator copies its meta with this value as the "
            "copy's data file name",
            "V:1251: the base is the lexical directory of the CNA data file's joined path",
            "V:1254: a validator built for the copy, reaching V:398, V:437, V:445 and V:453",
            "V:1232, V:1428: the DISCRETE_LONG validator is not a subclass, so it ignores the key",
        ),
    ),
    PathSite(
        "tags_file",
        frozenset({STUDY}),
        TAGS,
        (
            "V:4799, V:4845-4848: inside the study meta's branch, the value is joined to the "
            "study's directory as written",
            "V:5563: the tags file is validated when the key was set",
            "V:5062: opened as text and parsed as YAML; only a YAML error is caught",
        ),
    ),
    PathSite(
        "listing:meta",
        frozenset(MANDATORY),
        LISTING,
        (
            "V:4709-4710: one listing of the study's directory, sorted",
            "V:4711-4715: the meta-name pattern and the exclusions",
            "C:852: each meta file opened as text, following a symlink",
        ),
    ),
    PathSite(
        "listing:case_lists",
        frozenset(),
        CASE_LISTS,
        (
            "V:4900-4901: a listing of case_lists/, unsorted, dropping dot and ~ names only",
            "V:4909, C:852: each entry opened and parsed as a case list",
        ),
    ),
    PathSite(
        "isdir:case_lists",
        frozenset(),
        CASE_LISTS,
        ("V:5566-5567: whether case_lists is a directory, following a symlink",),
    ),
)
"""Every place the reference makes a path from a study's text, hand-written from a survey of
cbioportal-core 47890bb (no tool reading its source is part of the repository; a moved pin is
surveyed again by hand, D409). ``discover`` resolves the rows of the roles ``DATA`` (phase 9, in
this order within a meta file) and ``TAGS`` (phase 10), for the meta types each names; the
listing is its own; the case lists are not listed (exit 3 (19))."""

_DATA_FILE_NOTE = "a data file of a meta file M4.2a does not import"
_TAGS_NOTE = "the study's tags file"


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


def _path_value(
    site: PathSite,
    name: str,
    value: str,
    listed: Mapping[str, DirectoryEntry],
    folded: Mapping[str, Sequence[DirectoryEntry]],
    reads: frozenset[str],
) -> DirectoryEntry | None:
    """The entry a path value outside the clinical role names (phases 9 and 10), checked in
    order: P0 on the value as written, Kind (no symlink matches it, exactly or under ``fold``),
    Reads (no member of Reads(S) matches it, exactly or under ``fold``). Only an exact match to a
    listed regular file is returned; a data or PD value naming nothing else returns ``None`` (a
    file of a kind never imported, exit 3), a tags value is refused. The meta file ``name`` is
    named, and the key, never the value."""
    code: Code = "META_VALUE" if site.role == TAGS else "DATA_FILE"
    verb = "holds" if site.role == TAGS else "has"

    def refusal(reason: str) -> Refused:
        return entry_refused(code, name, f"{verb} a {site.key} that {reason}")

    if not one_name(value):
        raise refusal("is not a file name M4.2a accepts")
    alike = folded.get(fold(value), ())
    if any(entry.kind == "symlink" for entry in alike):
        raise refusal("names a symlink, exactly or under case and normalisation folding")
    if any(entry.name in reads for entry in alike):
        raise refusal(
            "names a meta file or a clinical data file, exactly or under case and normalisation "
            "folding"
        )
    entry = listed.get(value)
    if entry is not None and entry.kind == "file":
        return entry
    if site.role == TAGS:
        raise refusal("names no regular file in the study's directory")
    return None


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

    reads = frozenset({*in_meta, *(entry.name for entry in data.values())})
    folded: dict[str, list[DirectoryEntry]] = {}
    for entry in entries:
        folded.setdefault(fold(entry.name), []).append(entry)
    for role, note in ((DATA, _DATA_FILE_NOTE), (TAGS, _TAGS_NOTE)):
        for name, (kind, found) in metas.items():
            for site in PATH_SITES:
                if site.role != role or kind not in site.meta_types or site.key not in found:
                    continue
                named = _path_value(site, name, found[site.key], listed, folded, reads)
                if named is not None:
                    why.setdefault(named.name, note)

    used = frozenset({study_name, *clinical.values(), *(entry.name for entry in data.values())})
    for name in metas:
        why.setdefault(name, "a meta file of a type M4.2a does not import")
    notes = _notes(entries, used, why)
    if "tags_file" in study:
        message = [text("the study's tags file is not read by M4.2a")]
        notes.append(ImportNote("not_proposed", None, message))
    patient = ClinicalMeta(clinical[PATIENT], data[PATIENT]) if PATIENT in data else None
    return Study(fields, patient, ClinicalMeta(clinical[SAMPLE], data[SAMPLE]), tuple(notes))


__all__ = [
    "CASE_LISTS",
    "DATA",
    "LISTING",
    "META_FILES_MOST",
    "NON_CLINICAL_DATA",
    "NOTES_MOST",
    "PATH_SITES",
    "TAGS",
    "ClinicalMeta",
    "PathSite",
    "Study",
    "discover",
]
