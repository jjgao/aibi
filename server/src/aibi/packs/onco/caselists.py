"""Reading a cBioPortal study's case lists (SPEC D444, M4.2b-1b; the reference V:4874-5014,
C:852-893).

``read`` lists ``case_lists/`` once, through the import's reader, and reads every entry the
reference reads: each whose name neither starts with ``.`` nor ends with ``~`` (V:4900-4901), in
the order of their names, whatever the name's extension (a ``README.md`` is read as a case list,
and refused as one; a ``cases_y`` with no extension is one). Each must be a regular file (a
subdirectory, a symlink, any other kind or an entry that cannot be confined is ``CASE_LIST``: the
reference crashes on a subdirectory, cell c13, and follows a symlink, which the pack never does),
read at most ``CASE_LIST_MOST`` bytes, and parsed as the reference parses a case list (C:852-870,
the meta parse): lines end at ``\\r\\n``, ``\\n`` and ``\\r`` alone; each line is decoded on its
own, strict UTF-8; one byte order mark at the start of line 1 is read past (D409's exit 1 (13): the
reference reads it into the first key, cell z08); a line blank by ``str.strip()`` is skipped
(z16); any other is split at its first ``:``, the key as written and the value
``str.strip()``ped, the last of a repeated key winning (z07, z07b: the reference's, which drops
the earlier ids silently); a line without ``:`` is ``CASE_LIST`` (c19, z19). The 30 control
characters anywhere are ``CONTROL_CHARACTER`` (stricter: the reference reads U+0001, w08).

**The checks**, in the order of the files' names, and within a file in this order: the keys the
reference requires (C:255-261: ``cancer_study_identifier``, ``stable_id``, ``case_list_name``,
``case_list_description``, ``case_list_ids``; c09); the type combination (C:896-903: a list
that holds a ``genetic_alteration_type`` and a ``datatype`` must make, with its ``stable_id``, a
combination of ``meta.TYPES_AND_IDS``); the study id (C:917-930; c18); the ``stable_id``, which
must start with the study id and ``_`` (V:4920; c03) and repeat no earlier list's, the study
meta's ``<study>_all`` among them when its ``add_global_case_list`` is ``true`` (V:4806-4808,
V:4925; c04); the category, where given, one of ``CATEGORIES`` (V:4935-4950; c05; a
category another list has is the reference's warning, c17, and accepted); each value, as text
a cell may hold (``text.written``: a ``stable_id`` of at most 200 characters, a name of at most
4,096 and a description of at most 10,000, none holding a noncharacter; stricter, D444; an empty
name or description is read as written, as the reference reads it); then the ids: the value split at
tabs, each id ``str.strip()``ped (V:4969: so ``S1<tab><tab>S2`` holds an empty id, z01, and a
trailing tab is stripped first, c12b), each distinct id in turn a sample id of the sample file
(``UNKNOWN_SAMPLE``: V:4978; c07, c11, c12), which also refuses an id with a space (V:4984, c08):
no sample id holds one (the clinical reader's ``KEY_CHARACTERS``). A repeated id is
the reference's warning (c06) and is kept: the members table is keyless (read without loss).

**Memory.** A file is held while it is read (at most ``CASE_LIST_MOST`` bytes), its lines decoded
one at a time; what is kept of a list is its four values and its ids, each a sample id of the
sample file. ``CASE_LIST_FILES_MOST`` bounds the entries read. The module keeps no cache past a
read (D438).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import MAX_STRING, MAX_TEXT, ImportLimits
from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry, SourceReader
from aibi.core.schema.refusals import RefusalCode
from aibi.packs.onco.clinical import CONTROL, spans
from aibi.packs.onco.meta import types_and_id
from aibi.packs.onco.refusals import entry_refused, placed
from aibi.packs.onco.text import Checked, written

CASE_LIST_MOST = 8 * 2**20
"""The most bytes a case list may hold: 100,000 ids of 64 characters and their tabs, with room.
cBioPortal reads any size (stricter, D444); the real study's lists are about 450 KB."""
CASE_LIST_FILES_MOST = 256
"""The most entries of ``case_lists/`` the pack reads (stricter, D444)."""
STABLE_ID_MOST = 200
"""The most characters of a list's ``stable_id`` (a meta ``stable_id``'s, ``schemas.TABLE``)."""
CATEGORY_MOST = 64
LIST_CELLS = 4
"""The ``case_lists`` table's columns: ``stable_id``, ``name``, ``description``, ``category``."""
MEMBER_CELLS = 2
"""The ``case_list_members`` table's columns: ``case_list`` and ``sample_id``."""
REQUIRED = (
    "cancer_study_identifier",
    "stable_id",
    "case_list_name",
    "case_list_description",
    "case_list_ids",
)
"""The keys a case list must hold, in C:255-261's order."""
CATEGORIES = frozenset(
    {
        "all_cases_in_study",
        "all_cases_with_mutation_data",
        "all_cases_with_cna_data",
        "all_cases_with_log2_cna_data",
        "all_cases_with_methylation_data",
        "all_cases_with_mrna_array_data",
        "all_cases_with_mrna_rnaseq_data",
        "all_cases_with_rppa_data",
        "all_cases_with_microrna_data",
        "all_cases_with_mutation_and_cna_data",
        "all_cases_with_mutation_and_cna_and_mrna_data",
        "all_cases_with_gsva_data",
        "all_cases_with_sv_data",
        "other",
    }
)
"""The categories the reference accepts (V:4935-4949)."""
HIDDEN = "."
BACKUP = "~"
BOM = "﻿"
TAB = "\t"
ALL = "_all"
"""The suffix of the list the study meta's ``add_global_case_list`` defines (V:4806-4808,
V:4864-4868): with ``global_list``, a case list holding ``<study>_all`` repeats it."""
SEQUENCED = "_sequenced"
"""The suffix of the list the reference requires of a study with a MAF, after the study id
(V:5034-5040): found by its exact ``stable_id``, never by a file name or a suffix alone."""
_KIND: Mapping[str, str] = MappingProxyType(
    {
        "directory": "is a directory",
        "other": "is not a regular file",
        "unconfined": "cannot be confined",
    }
)


@dataclass(frozen=True)
class CaseList:
    """A case list, read: its entry's name, its values and its ids in the order written
    (repeats kept)."""

    file: str
    stable_id: Checked
    name: Checked
    description: Checked
    category: Checked | None
    ids: tuple[str, ...]


def read_entry(name: str, data: bytes) -> dict[str, str]:
    """The keys and values of the case list ``name`` (C:852-870), line by line; refused as
    ``NOT_UTF8``, ``CONTROL_CHARACTER`` or ``CASE_LIST`` (a line without ``:``)."""
    view = memoryview(data)
    found: dict[str, str] = {}
    for number, start, end in spans(data):
        try:
            line = str(view[start:end], "utf-8")
        except UnicodeDecodeError:
            line = None
        if line is None:
            raise placed("NOT_UTF8", name, number, None, "the case list's line is not UTF-8")
        if number == 1 and line.startswith(BOM):
            line = line[len(BOM) :]
        if CONTROL.search(line) is not None:
            raise placed(
                "CONTROL_CHARACTER", name, number, None, "the line holds a control character"
            )
        if line.strip() == "":
            continue
        key, colon, value = line.partition(":")
        if not colon:
            raise placed("CASE_LIST", name, number, None, "the line has no ':'")
        found[key] = value.strip()
    return found


def _checked(
    name: str,
    found: Mapping[str, str],
    study_id: str,
    seen: Mapping[str, str],
    samples: frozenset[str],
) -> CaseList:
    for key in REQUIRED:
        if key not in found:
            raise entry_refused("CASE_LIST", name, f"lacks the key {key}")
    if not types_and_id(found):
        raise entry_refused(
            "CASE_LIST",
            name,
            "holds a genetic_alteration_type and datatype that make no combination cBioPortal "
            "accepts with its stable_id",
        )
    if found["cancer_study_identifier"] != study_id:
        raise entry_refused("CASE_LIST", name, "names another study than the study meta")
    if len(found["stable_id"]) > STABLE_ID_MOST:
        raise entry_refused("CASE_LIST", name, "holds a stable_id longer than 200 characters")
    stable = written(found["stable_id"], STABLE_ID_MOST, 0)
    if stable is None:
        raise entry_refused("CASE_LIST", name, "holds a stable_id that is not Unicode text")
    if not stable.startswith(study_id + "_"):
        raise entry_refused(
            "CASE_LIST", name, "holds a stable_id that does not start with the study id and '_'"
        )
    if stable in seen:
        raise entry_refused("CASE_LIST", name, "repeats another case list's stable_id")
    category: Checked | None = None
    if "case_list_category" in found:
        category = written(found["case_list_category"], CATEGORY_MOST)
        if category is None or category not in CATEGORIES:
            raise entry_refused(
                "CASE_LIST", name, "holds a case_list_category that cBioPortal does not define"
            )
    if len(found["case_list_name"]) > MAX_STRING:
        raise entry_refused("CASE_LIST", name, "holds a case_list_name longer than a label may be")
    label = written(found["case_list_name"], MAX_STRING, 0)
    if label is None:
        raise entry_refused("CASE_LIST", name, "holds a case_list_name that is not Unicode text")
    if len(found["case_list_description"]) > MAX_TEXT:
        raise entry_refused(
            "CASE_LIST", name, "holds a case_list_description longer than a text may be"
        )
    description = written(found["case_list_description"], MAX_TEXT, 0)
    if description is None:
        raise entry_refused(
            "CASE_LIST", name, "holds a case_list_description that is not Unicode text"
        )
    ids = tuple(item.strip() for item in found["case_list_ids"].split(TAB))
    checked: set[str] = set()
    for sample in ids:
        if sample in checked:
            continue
        checked.add(sample)
        if sample not in samples:
            raise entry_refused(
                "UNKNOWN_SAMPLE", name, "lists an id that is no sample id of the sample file"
            )
    return CaseList(name, stable, label, description, category, ids)


def _content(reader: SourceReader, entry: DirectoryEntry, limits: ImportLimits) -> bytes:
    if entry.kind == "symlink":
        raise entry_refused("CASE_LIST", entry.name, "is a symlink, which the pack never follows")
    if entry.kind != "file" or entry.path is None:
        raise entry_refused("CASE_LIST", entry.name, _KIND.get(entry.kind, "cannot be confined"))
    try:
        return reader.read(entry.path, min(CASE_LIST_MOST, limits.import_bytes))
    except ImportRefused as error:
        mine = limits.import_bytes > CASE_LIST_MOST and bool(error.refusals)
        if mine and all(r.code == RefusalCode.LIMIT_EXCEEDED for r in error.refusals):
            raise entry_refused(
                "CASE_LIST", entry.name, "is larger than a case list may be"
            ) from None
        raise


def read(
    reader: SourceReader,
    source: ConfinedPath,
    study_id: str,
    samples: frozenset[str],
    limits: ImportLimits,
    *,
    cells_before: int = 0,
    global_list: bool = False,
) -> tuple[CaseList, ...]:
    """The case lists of the study in ``source`` (the module's docstring), in the order of their
    entries' names, with ``cells_before`` cells of the import counted before them: the two
    tables' cells (``LIST_CELLS`` a list and ``MEMBER_CELLS`` an id) are counted after each
    list, and more than the import's ``import_cells`` is ``TOO_MANY_CELLS`` there, so that the
    ids kept never grow past the bound (stricter, D444). Raises ``Refused``, or lets the
    reader's refusal out. With ``global_list`` (the study meta's ``add_global_case_list``), the
    ``stable_id`` ``<study>_all`` is taken before the first list is read (V:4864-4873).
    Each list is read at most ``CASE_LIST_MOST`` bytes, or the operator's ``import_bytes`` when
    that is smaller."""
    directory = ConfinedPath(Path(source) / "case_lists")
    entries = sorted(
        (
            entry
            for entry in reader.files(directory)
            if not (entry.name.startswith(HIDDEN) or entry.name.endswith(BACKUP))
        ),
        key=lambda entry: entry.name,
    )
    if len(entries) > CASE_LIST_FILES_MOST:
        raise entry_refused(
            "CASE_LIST", "case_lists", "holds more entries than the pack reads as case lists"
        )
    lists: list[CaseList] = []
    seen: dict[str, str] = {f"{study_id}{ALL}": "meta_study.txt"} if global_list else {}
    members = 0
    for entry in entries:
        found = read_entry(entry.name, _content(reader, entry, limits))
        case_list = _checked(entry.name, found, study_id, seen, samples)
        seen[case_list.stable_id] = entry.name
        lists.append(case_list)
        members += len(case_list.ids)
        cells = LIST_CELLS * len(lists) + MEMBER_CELLS * max(1, members)
        if cells_before + cells > limits.import_cells:
            raise entry_refused(
                "TOO_MANY_CELLS", entry.name, "takes the study's files past the cells an import may"
            )
    return tuple(lists)


__all__ = [
    "CASE_LIST_FILES_MOST",
    "CASE_LIST_MOST",
    "CATEGORIES",
    "SEQUENCED",
    "CaseList",
    "read",
    "read_entry",
]
