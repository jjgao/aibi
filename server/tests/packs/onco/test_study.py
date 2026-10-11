"""Discovering a study (M4.2a-1a, D409): what ``discover`` reads, refuses and notes.

- **Reads.** One listing; the meta files of Meta(S) (meta-named, not excluded), each at
  ``META_MOST`` bytes, and nothing else: an excluded entry, a non-file, a data file are never read
  (``RecordingReader``).
- **C.** A meta file of Meta(S) that cannot be read is ``META_FILE``, never skipped, whatever its
  role (the brute force of 6 kinds by 6 roles), and a reader refusal other than the meta read's own
  ``LIMIT_EXCEEDED`` passes out as the instance the reader raised (stubs, a file swapped after it
  is listed, and mode 000 where the user is not exempt from it).
- **The clinical role.** The generated grid (``clinical_grid.py``) and named cases.
- **Notes**, the order of the checks (the phases ``study.py`` states, each pair held), totality
  (Hypothesis) and a sentinel scan: no value a file holds reaches a refusal, a note, a log, an
  exception or its chain; a name only as ``data``.
"""

import dataclasses
import importlib
import itertools
import logging
import os
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import DataSegment, TextSegment, text
from aibi.core.schema.pack_api import Refused
from aibi.core.schema.refusals import Refusal, RefusalCode

META_MOST = 65_536
REFUSALS = "aibi.packs.onco.refusals"
MAF_META = (
    b"cancer_study_identifier: demo_study\ngenetic_alteration_type: MUTATION_EXTENDED\n"
    b"datatype: MAF\nstable_id: mutations\nshow_profile_in_analysis_tab: true\n"
    b"profile_name: M\nprofile_description: M.\ndata_filename: data_mutations.txt\n"
)
CANCER_TYPE_META = (
    b"genetic_alteration_type: CANCER_TYPE\ndatatype: CANCER_TYPE\ndata_filename: cancer_type.txt\n"
)
RESOURCE_META = (
    b"cancer_study_identifier: demo_study\nresource_type: SAMPLE\ndata_filename: resource.txt\n"
)
OTHER_STUDY = b"type_of_cancer: brca\ncancer_study_identifier: other\nname: x\ndescription: x\n"
METAS = ["meta_clinical_patient.txt", "meta_clinical_sample.txt", "meta_study.txt"]


def set_data(base: dict[str, bytes], meta: str, value: str) -> None:
    """The clinical meta ``meta`` naming ``value`` (its last ``data_filename`` line wins)."""
    base[meta] += b"data_filename: " + value.encode() + b"\n"


def read_names(reader: Any) -> list[str]:
    return [name for name, _ in reader.reads]


def listed_as(reader: Any, kinds: Mapping[str, str] | None = None, reverse: bool = False) -> None:
    """Stands a stub listing in for ``reader``'s: the entries named in ``kinds`` as that kind with
    no path (the reader could not confine them), and, with ``reverse``, in the opposite order of
    the names."""
    listed = reader.inner.files

    def files(source: Any) -> list[Any]:
        found = [
            dataclasses.replace(entry, kind=kinds[entry.name], path=None)
            if kinds and entry.name in kinds
            else entry
            for entry in listed(source)
        ]
        return sorted(found, key=lambda entry: entry.name, reverse=reverse)

    reader.files = files


# --- A study, and what is read --------------------------------------------------------------------


def test_the_base_study_imports(make_study: Any, discovery: Any, base: Any) -> None:
    directory = make_study(base)
    reader = discovery.reader(directory)
    found = discovery.run(directory, reader=reader)
    assert (found.sample.meta, found.sample.data.name) == (
        "meta_clinical_sample.txt",
        "data_clinical_sample.txt",
    )
    assert found.sample.data.kind == "file"
    assert found.sample.data.path == (directory / "data_clinical_sample.txt").resolve()
    assert found.patient is not None
    assert (found.patient.meta, found.patient.data.name) == (
        "meta_clinical_patient.txt",
        "data_clinical_patient.txt",
    )
    assert found.notes == ()
    assert reader.listed == [directory.name]
    assert reader.reads == [(name, META_MOST) for name in METAS]


def test_a_study_without_a_patient_meta_imports(make_study: Any, discovery: Any, base: Any) -> None:
    del base["meta_clinical_patient.txt"]
    found = discovery.run(make_study(base))
    assert found.patient is None
    assert found.sample.data.name == "data_clinical_sample.txt"


def test_a_single_file_source_is_the_readers_refusal(
    make_study: Any, discovery: Any, base: Any
) -> None:
    directory = make_study(base)
    reader = discovery.reader(directory)
    source = reader.inner.confine(directory / "meta_study.txt")
    with pytest.raises(ImportRefused) as raised:
        discovery.study.discover(reader, source, ImportLimits())
    assert [r.code for r in raised.value.refusals] == [RefusalCode.PATH_NOT_CONFINED]
    assert reader.reads == []


# --- Exclusions (V:4709-4715) ---------------------------------------------------------------------


def test_excluded_entries_are_never_read(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    """Each a second study meta if it were read (``meta_study.txt~``, ``.meta_study.txt``,
    ``ONCOKB_IMPORT_BACKUP_meta_study.txt``: the only cases that tell each exclusion apart)."""
    base.update(
        {
            "meta_study.txt~": OTHER_STUDY,
            ".meta_study.txt": OTHER_STUDY,
            "ONCOKB_IMPORT_BACKUP_meta_study.txt": OTHER_STUDY,
            ".hidden": b"x",
            "notes.txt~": b"x",
        }
    )
    directory = make_study(base)
    reader = discovery.reader(directory)
    found = discovery.run(directory, reader=reader)
    assert read_names(reader) == METAS
    assert notes_of(found) == [
        "skipped_source: a hidden file: «.hidden»",
        "skipped_source: a hidden file: «.meta_study.txt»",
        "skipped_source: an editor or OncoKB backup: «ONCOKB_IMPORT_BACKUP_meta_study.txt»",
        "skipped_source: an editor or OncoKB backup: «meta_study.txt~»",
        "skipped_source: an editor or OncoKB backup: «notes.txt~»",
    ]


def test_excluded_data_files_import_without_a_note(
    make_study: Any, discovery: Any, base: Any
) -> None:
    base["s.txt~"] = base.pop("data_clinical_sample.txt")
    base[".p.txt"] = base.pop("data_clinical_patient.txt")
    set_data(base, "meta_clinical_sample.txt", "s.txt~")
    set_data(base, "meta_clinical_patient.txt", "./.p.txt")
    directory = make_study(base)
    reader = discovery.reader(directory)
    found = discovery.run(directory, reader=reader)
    assert (found.sample.data.name, found.patient.data.name) == ("s.txt~", ".p.txt")
    assert found.notes == ()
    assert read_names(reader) == METAS


@pytest.mark.parametrize(
    "name", [".meta_hidden.txt", "ONCOKB_IMPORT_BACKUP_meta_x.txt", "meta_x.txt~"]
)
def test_meta_s_is_computed_after_the_exclusions(
    make_study: Any, discovery: Any, base: Any, name: str
) -> None:
    """An excluded meta-named entry is no member of Meta(S): a clinical meta may name it."""
    base[name] = base.pop("data_clinical_sample.txt")
    set_data(base, "meta_clinical_sample.txt", name)
    found = discovery.run(make_study(base))
    assert found.sample.data.name == name
    assert found.notes == ()


# --- The 256 cap ----------------------------------------------------------------------------------


def test_256_meta_files_are_read_and_257_refused(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    for number in range(253):
        base[f"meta_cancer_type_{number:03d}.txt"] = CANCER_TYPE_META
    base[".meta_excluded.txt"] = CANCER_TYPE_META
    directory = make_study(base)
    reader = discovery.reader(directory)
    found = discovery.run(directory, reader=reader)
    assert len(reader.reads) == 256
    notes = notes_of(found)
    assert len(notes) == 201
    assert notes[0] == "skipped_source: a hidden file: «.meta_excluded.txt»"
    assert notes[1] == (
        "skipped_source: a meta file of a type M4.2a does not import: «meta_cancer_type_000.txt»"
    )
    assert notes[-1] == "skipped_source: further entries M4.2a does not read (54)"

    base["meta_cancer_type_253.txt"] = CANCER_TYPE_META
    directory = make_study(base)
    reader = discovery.reader(directory)
    refusal = discovery.refusal(directory, reader=reader)
    assert refusal.code == "onco.TOO_MANY_FILES"
    assert refusal.message == [
        text("The study's directory holds more meta-named entries than M4.2a reads")
    ]
    assert reader.reads == []


def test_the_cap_counts_every_meta_named_entry_not_only_files(
    make_study: Any, discovery: Any, base: Any, kinds: Any
) -> None:
    """256 is the most meta-named entries of any kind: 3 of the base, 251 cancer-type metas and
    a directory, a symlink and a FIFO make 257, refused before any read."""
    for number in range(251):
        base[f"meta_cancer_type_{number:03d}.txt"] = CANCER_TYPE_META
    tree: dict[str, Any] = {
        **base,
        "meta_dir": {"x": b"x"},
        "meta_link": kinds.symlink("meta_study.txt"),
        "meta_fifo": kinds.fifo(),
    }
    directory = make_study(tree)
    reader = discovery.reader(directory)
    refusal = discovery.refusal(directory, reader=reader)
    assert refusal.code == "onco.TOO_MANY_FILES"
    assert reader.reads == []


# --- C: a meta file that cannot be read is refused, whatever its role -----------------------------

ROLES = {
    "meta_study.txt": None,
    "meta_clinical_sample.txt": None,
    "meta_clinical_patient.txt": None,
    "meta_mutations.txt": MAF_META,
    "meta_cancer_type.txt": CANCER_TYPE_META,
    "meta_resource.txt": RESOURCE_META,
}
KINDS = {
    "symlink": "is a symlink",
    "directory": "is a directory",
    "fifo": "is not a regular file",
    "latin1": "is not UTF-8",
    "no_colon": "has a line without ':'",
    "oversize": "is larger than a meta file may be",
}


def with_roles(base: dict[str, bytes]) -> dict[str, Any]:
    tree: dict[str, Any] = dict(base)
    for role, content in ROLES.items():
        if content is not None:
            tree[role] = content
    return tree


def test_every_role_imports_when_readable(make_study: Any, discovery: Any, base: Any) -> None:
    assert discovery.outcome(make_study(with_roles(base)))[0] == "ok"


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("role", list(ROLES))
def test_c_brute_force(
    make_study: Any, discovery: Any, base: Any, kinds: Any, role: str, kind: str
) -> None:
    tree = with_roles(base)
    content: bytes = tree[role]
    if kind == "symlink":
        tree["elsewhere.txt"] = content
        tree[role] = kinds.symlink("elsewhere.txt")
    elif kind == "directory":
        tree[role] = {"x.txt": content}
    elif kind == "fifo":
        tree[role] = kinds.fifo()
    elif kind == "latin1":
        tree[role] = content + b"name_of_it: caf\xe9\n"
    elif kind == "no_colon":
        tree[role] = content + b"just words\n"
    else:
        tree[role] = content + b"padding: " + b"x" * 70_000 + b"\n"
    directory = make_study(tree)
    reader = discovery.reader(directory)
    refusal = discovery.refusal(directory, reader=reader)
    assert refusal.code == "onco.META_FILE"
    assert refusal.message == [DataSegment(data=role), text(" " + KINDS[kind])]
    read = read_names(reader)
    if kind in ("symlink", "directory", "fifo"):
        assert role not in read
    else:
        assert read[-1] == role
    assert all(limit == META_MOST for _, limit in reader.reads)


def test_an_unconfined_meta_file_is_refused(make_study: Any, discovery: Any, base: Any) -> None:
    directory = make_study(base)
    reader = discovery.reader(directory)
    listed = reader.inner.files

    def files(source: Any) -> list[Any]:
        return [
            dataclasses.replace(entry, kind="unconfined", path=None)
            if entry.name == "meta_study.txt"
            else entry
            for entry in listed(source)
        ]

    reader.files = files
    refusal = discovery.refusal(directory, reader=reader)
    assert refusal.message == [DataSegment(data="meta_study.txt"), text(" cannot be confined")]
    assert "meta_study.txt" not in read_names(reader)


@pytest.mark.parametrize("meta", ["meta_clinical_sample.txt", "meta_clinical_patient.txt"])
def test_a_data_file_that_cannot_be_confined_is_refused(
    make_study: Any, discovery: Any, base: Any, meta: str
) -> None:
    """A listing entry with no path is never handed on: M4.2a-1b would read through its path."""
    data = "data_clinical_sample.txt" if "sample" in meta else "data_clinical_patient.txt"
    directory = make_study(base)
    reader = discovery.reader(directory)
    listed_as(reader, {data: "unconfined"})
    refusal = discovery.refusal(directory, reader=reader)
    assert refusal.code == "onco.DATA_FILE"
    assert refusal.message == [
        DataSegment(data=meta),
        text(" has a data_filename that names no regular file in the study's directory"),
    ]


def test_an_entry_that_cannot_be_confined_is_noted_as_not_a_regular_file(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    directory = make_study({**base, "other.txt": b"x"})
    reader = discovery.reader(directory)
    listed_as(reader, {"other.txt": "unconfined"})
    found = discovery.run(directory, reader=reader)
    assert notes_of(found) == ["skipped_source: not a regular file: «other.txt»"]


# --- The catch: only the meta read's own LIMIT_EXCEEDED -------------------------------------------


def padded(content: bytes, size: int) -> bytes:
    """``content`` with an unknown key added, ``size`` bytes in all."""
    line = b"padding: "
    return content + line + b"x" * (size - len(content) - len(line) - 1) + b"\n"


def test_meta_files_are_read_at_their_own_limit(make_study: Any, discovery: Any, base: Any) -> None:
    base["meta_study.txt"] = padded(base["meta_study.txt"], 70_000)
    directory = make_study(base)
    reader = discovery.reader(directory)
    refusal = discovery.refusal(directory, ImportLimits(import_bytes=100_000), reader)
    assert refusal.code == "onco.META_FILE"
    assert reader.reads == [(name, META_MOST) for name in METAS]


def test_a_meta_file_within_its_limit_imports(make_study: Any, discovery: Any, base: Any) -> None:
    study = base["meta_study.txt"]
    base["meta_study.txt"] = padded(study, META_MOST)
    assert discovery.outcome(make_study(base))[0] == "ok"
    base["meta_study.txt"] = padded(study, META_MOST + 1)
    assert discovery.outcome(make_study(base))[:2] == ("onco.META_FILE", "meta_study.txt")


@pytest.mark.parametrize(("import_bytes", "size"), [(32_768, 40_000), (65_536, 70_000)])
def test_the_operators_limit_is_the_readers_refusal(
    make_study: Any, discovery: Any, base: Any, import_bytes: int, size: int
) -> None:
    """Through the core's own reader, which caps the read at ``import_bytes``: the refusal is the
    reader's, naming the operator's limit, and no ``META_FILE``."""
    base["meta_study.txt"] = padded(base["meta_study.txt"], size)
    with pytest.raises(ImportRefused) as raised:
        discovery.through_core(make_study(base), import_bytes)
    (refusal,) = raised.value.refusals
    assert refusal.code == RefusalCode.LIMIT_EXCEEDED
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("import_bytes", import_bytes)


def test_the_default_limit_with_70_kb_is_meta_file(
    make_study: Any, discovery: Any, base: Any
) -> None:
    base["meta_study.txt"] = padded(base["meta_study.txt"], 70_000)
    with pytest.raises(ImportRefused) as raised:
        discovery.through_core(make_study(base))
    assert [r.model_dump(mode="json") for r in raised.value.refusals] == [
        {
            "code": "onco.META_FILE",
            "path": None,
            "message": [{"data": "meta_study.txt"}, {"text": " is larger than a meta file may be"}],
            "alternatives": [],
        }
    ]


def test_a_study_through_the_core(make_study: Any, discovery: Any, base: Any) -> None:
    """The core's checks take discover's notes, and refuse nothing of a 40 KB meta file."""
    base["meta_study.txt"] = padded(base["meta_study.txt"], 40_000)
    base["other.txt"] = b"x"
    result = discovery.through_core(make_study(base))
    assert [(n.kind, n.message) for n in result.notes] == [
        ("skipped_source", (text("a file M4.2a does not read: "), DataSegment(data="other.txt")))
    ]


def prepared(*codes: RefusalCode) -> ImportRefused:
    return ImportRefused(
        [Refusal(code=code, path=None, message=[text("prepared")]) for code in codes]
    )


LIMIT = RefusalCode.LIMIT_EXCEEDED
CONFINED = RefusalCode.PATH_NOT_CONFINED


@pytest.mark.parametrize(
    ("codes", "import_bytes"),
    [
        ((CONFINED,), None),
        ((LIMIT,), 32_768),
        ((LIMIT,), 65_536),
        ((LIMIT, CONFINED), None),
        ((), None),
    ],
)
def test_a_reader_refusal_passes_as_the_same_instance(
    make_study: Any,
    discovery: Any,
    base: Any,
    codes: tuple[RefusalCode, ...],
    import_bytes: int | None,
) -> None:
    error = prepared(*codes)
    directory = make_study(base)
    reader = discovery.reader(directory, fail={"meta_study.txt": error})
    limits = ImportLimits() if import_bytes is None else ImportLimits(import_bytes=import_bytes)
    with pytest.raises(ImportRefused) as raised:
        discovery.run(directory, limits, reader)
    assert raised.value is error


@pytest.mark.parametrize("import_bytes", [65_537, None])
def test_the_meta_reads_limit_is_meta_file(
    make_study: Any, discovery: Any, base: Any, import_bytes: int | None
) -> None:
    directory = make_study(base)
    reader = discovery.reader(directory, fail={"meta_study.txt": prepared(LIMIT)})
    limits = ImportLimits() if import_bytes is None else ImportLimits(import_bytes=import_bytes)
    refusal = discovery.refusal(directory, limits, reader)
    assert refusal.message == [
        DataSegment(data="meta_study.txt"),
        text(" is larger than a meta file may be"),
    ]


def test_a_file_swapped_after_the_listing_is_the_readers_refusal(
    make_study: Any, discovery: Any, base: Any
) -> None:
    """The reader's own refusal, the twin of mode 000 that runs as root: the reader refuses a
    file whose identity changed since it was listed, and that instance passes out."""
    directory = make_study(base)
    reader = discovery.reader(directory, swap=["meta_clinical_sample.txt"])
    with pytest.raises(ImportRefused) as raised:
        discovery.run(directory, reader=reader)
    assert [r.code for r in raised.value.refusals] == [CONFINED]
    assert raised.value is reader.raised[0]


@pytest.fixture
def without_dac(tmp_path: Path) -> None:
    """Skips a test where a mode-000 file can be read anyway (root, or ``CAP_DAC_OVERRIDE``)."""
    probe = tmp_path / "probe"
    probe.write_bytes(b"x")
    probe.chmod(0)
    try:
        probe.read_bytes()
    except PermissionError:
        return
    finally:
        probe.chmod(0o644)
    pytest.skip("this user reads a file of mode 000")


@pytest.mark.usefixtures("without_dac")
def test_a_meta_file_of_mode_000_is_the_readers_refusal(
    make_study: Any, discovery: Any, base: Any, kinds: Any
) -> None:
    tree: dict[str, Any] = dict(base)
    tree["meta_study.txt"] = kinds.mode(base["meta_study.txt"], 0)
    directory = make_study(tree)
    reader = discovery.reader(directory)
    with pytest.raises(ImportRefused) as raised:
        discovery.run(directory, reader=reader)
    assert [r.code for r in raised.value.refusals] == [CONFINED]
    assert raised.value is reader.raised[0]


@pytest.mark.usefixtures("without_dac")
def test_files_of_mode_000_that_are_not_meta_files_import(
    make_study: Any, discovery: Any, base: Any, kinds: Any, notes_of: Any
) -> None:
    tree: dict[str, Any] = dict(base)
    tree["data_clinical_sample.txt"] = kinds.mode(base["data_clinical_sample.txt"], 0)
    tree["other.txt"] = kinds.mode(b"x", 0)
    found = discovery.run(make_study(tree))
    assert found.sample.data.name == "data_clinical_sample.txt"
    assert notes_of(found) == ["skipped_source: a file M4.2a does not read: «other.txt»"]


# --- BOMs (cause rule 2) --------------------------------------------------------------------------


@pytest.mark.parametrize("name", [*METAS, "meta_mutations.txt"])
def test_one_bom_on_a_meta_file_imports(
    make_study: Any, discovery: Any, base: Any, name: str
) -> None:
    base["meta_mutations.txt"] = MAF_META
    base[name] = "\ufeff".encode() + base[name]
    assert discovery.outcome(make_study(base))[0] == "ok"


# --- Exit 3 and exit 1: what 1a does not import, it notes -----------------------------------------

CASE_LIST = (
    b"cancer_study_identifier: demo_study\nstable_id: demo_study_all\ncase_list_name: All\n"
    b"case_list_description: All\ncase_list_ids: S1\tS2\n"
)
EXIT_3: dict[str, tuple[dict[str, Any], list[str]]] = {
    "sample resource, data file missing": (
        {"meta_resource_sample.txt": RESOURCE_META},
        ["a meta file of a type M4.2a does not import: «meta_resource_sample.txt»"],
    ),
    "sample resource, data file not UTF-8": (
        {"meta_resource_sample.txt": RESOURCE_META, "resource.txt": b"caf\xe9\n"},
        [
            "a meta file of a type M4.2a does not import: «meta_resource_sample.txt»",
            "a file M4.2a does not read: «resource.txt»",
        ],
    ),
    "a Latin-1 case list": (
        {"case_lists": {"cases_all.txt": CASE_LIST + b"x: caf\xe9\n"}},
        ["a subdirectory (case lists are M4.2b's): «case_lists»"],
    ),
    "a directory in case_lists": (
        {"case_lists": {"cases_all.txt": {}}},
        ["a subdirectory (case lists are M4.2b's): «case_lists»"],
    ),
    "a case list naming another study": (
        {"case_lists": {"cases_all.txt": CASE_LIST.replace(b"demo_study", b"other")}},
        ["a subdirectory (case lists are M4.2b's): «case_lists»"],
    ),
    "a MAF missing its columns": (
        {"meta_mutations.txt": MAF_META, "data_mutations.txt": b"Hugo_Symbol\nTP53\n"},
        [
            "a file M4.2a does not read: «data_mutations.txt»",
            "a meta file of a type M4.2a does not import: «meta_mutations.txt»",
        ],
    ),
    "a missing MAF": (
        {"meta_mutations.txt": MAF_META},
        ["a meta file of a type M4.2a does not import: «meta_mutations.txt»"],
    ),
    "a bad timeline": (
        {
            "meta_timeline.txt": b"cancer_study_identifier: demo_study\n"
            b"genetic_alteration_type: CLINICAL\ndatatype: TIMELINE\ndata_filename: tl.txt\n",
            "tl.txt": b"no\ttimeline\n",
        },
        [
            "a meta file of a type M4.2a does not import: «meta_timeline.txt»",
            "a file M4.2a does not read: «tl.txt»",
        ],
    ),
    "a bad cancer-type file": (
        {"meta_cancer_type.txt": CANCER_TYPE_META, "cancer_type.txt": b"x\n"},
        [
            "a file M4.2a does not read: «cancer_type.txt»",
            "a meta file of a type M4.2a does not import: «meta_cancer_type.txt»",
        ],
    ),
    "a mutation meta without _sequenced": (
        {
            "meta_mutations.txt": MAF_META,
            "data_mutations.txt": b"Hugo_Symbol\tTumor_Sample_Barcode\nTP53\tS1\n",
            "case_lists": {"cases_all.txt": CASE_LIST},
        },
        [
            "a subdirectory (case lists are M4.2b's): «case_lists»",
            "a file M4.2a does not read: «data_mutations.txt»",
            "a meta file of a type M4.2a does not import: «meta_mutations.txt»",
        ],
    ),
}


@pytest.mark.parametrize("case", list(EXIT_3))
def test_exit_3_imports_with_its_notes(
    make_study: Any, discovery: Any, base: Any, notes_of: Any, case: str
) -> None:
    extra, notes = EXIT_3[case]
    found = discovery.run(make_study({**base, **extra}))
    assert notes_of(found) == [f"skipped_source: {note}" for note in notes]


def test_a_symlinked_case_lists_imports(
    make_study: Any, discovery: Any, base: Any, kinds: Any, notes_of: Any
) -> None:
    found = discovery.run(make_study({**base, "case_lists": kinds.symlink(".")}))
    assert notes_of(found) == ["skipped_source: a symlink: «case_lists»"]


def test_exit_1_a_study_without_an_all_list_imports(
    make_study: Any, discovery: Any, base: Any
) -> None:
    base["meta_study.txt"] = base["meta_study.txt"].replace(b"add_global_case_list: true\n", b"")
    assert discovery.outcome(make_study(base))[0] == "ok"


# --- Notes ----------------------------------------------------------------------------------------


def test_every_skip_reason(
    make_study: Any, discovery: Any, base: Any, kinds: Any, notes_of: Any
) -> None:
    tree: dict[str, Any] = {
        **base,
        ".hidden": b"x",
        "x~": b"x",
        "meta_mutations.txt": MAF_META,
        "data_mutations.txt": b"x",
        "case_lists": {"cases_all.txt": CASE_LIST},
        "subdirectory": {},
        "link": kinds.symlink("."),
        "fifo": kinds.fifo(),
        "tags.yml": b"x",
    }
    tree["meta_study.txt"] = base["meta_study.txt"] + b"tags_file: tags.yml\n"
    directory = make_study(tree)
    reader = discovery.reader(directory)
    found = discovery.run(directory, reader=reader)
    assert read_names(reader) == [
        "meta_clinical_patient.txt",
        "meta_clinical_sample.txt",
        "meta_mutations.txt",
        "meta_study.txt",
    ]
    assert notes_of(found) == [
        "skipped_source: a hidden file: «.hidden»",
        "skipped_source: a subdirectory (case lists are M4.2b's): «case_lists»",
        "skipped_source: a file M4.2a does not read: «data_mutations.txt»",
        "skipped_source: not a regular file: «fifo»",
        "skipped_source: a symlink: «link»",
        "skipped_source: a meta file of a type M4.2a does not import: «meta_mutations.txt»",
        "skipped_source: a subdirectory: «subdirectory»",
        "skipped_source: a file M4.2a does not read: «tags.yml»",
        "skipped_source: an editor or OncoKB backup: «x~»",
        "not_proposed: the study's tags file is not read by M4.2a",
    ]
    assert [note.subject for note in found.notes] == [None] * 10
    assert not any("named" in note for note in notes_of(found))


def test_an_empty_tags_file_still_has_the_note(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    """The note is for the key, not its value: ``tags_file:`` with nothing after it has it too."""
    base["meta_study.txt"] += b"tags_file:\n"
    found = discovery.run(make_study(base))
    assert notes_of(found) == ["not_proposed: the study's tags file is not read by M4.2a"]


def test_a_tags_file_of_a_meta_that_is_not_the_study_meta_adds_no_note(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    """The ``not_proposed`` note is for the study meta's ``tags_file`` alone: a clinical meta, or a
    meta of a type that is skipped, holding one adds none."""
    base["meta_clinical_sample.txt"] += b"tags_file: tags.yml\n"
    base["meta_clinical_patient.txt"] += b"tags_file:\n"
    base["meta_mutations.txt"] = MAF_META + b"tags_file: tags.yml\n"
    found = discovery.run(make_study(base))
    assert notes_of(found) == [
        "skipped_source: a meta file of a type M4.2a does not import: «meta_mutations.txt»"
    ]


def test_names_of_200_characters_are_shown_and_201_withheld(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    shown, withheld = "n" * 196 + ".txt", "n" * 197 + ".txt"
    found = discovery.run(make_study({**base, shown: b"x", withheld: b"x"}))
    assert notes_of(found) == [
        f"skipped_source: a file M4.2a does not read: «{shown}»",
        "skipped_source: a file M4.2a does not read: an entry whose name cannot be shown",
    ]
    meta_shown, meta_withheld = "meta_" + "m" * 191 + ".txt", "meta_" + "m" * 192 + ".txt"
    assert len(meta_shown) == 200
    refusal = discovery.refusal(make_study({**base, meta_shown: b"no colon\n"}))
    assert refusal.message[0] == DataSegment(data=meta_shown)
    refusal = discovery.refusal(make_study({**base, meta_withheld: b"no colon\n"}))
    assert refusal.message == [
        text("an entry whose name cannot be shown"),
        text(" has a line without ':'"),
    ]


def test_the_name_cap_is_in_characters_not_bytes(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    """D409 says "at most 200 characters": a name of 170 characters that is 220 bytes is shown, one
    of 200 characters (250 bytes) is shown, and one of 201 characters (231 bytes, so under
    ``NAME_MAX``) is withheld."""
    long_bytes = "é" * 50 + "n" * 116 + ".txt"
    most = "é" * 50 + "n" * 146 + ".txt"
    over = "é" * 30 + "n" * 167 + ".txt"
    assert [len(name) for name in (long_bytes, most, over)] == [170, 200, 201]
    assert [len(name.encode()) for name in (long_bytes, most, over)] == [220, 250, 231]
    found = discovery.run(make_study({**base, long_bytes: b"x", most: b"x", over: b"x"}))
    assert sorted(notes_of(found)) == sorted(
        [
            f"skipped_source: a file M4.2a does not read: «{long_bytes}»",
            f"skipped_source: a file M4.2a does not read: «{most}»",
            "skipped_source: a file M4.2a does not read: an entry whose name cannot be shown",
        ]
    )
    meta_name = "meta_" + "é" * 50 + "m" * 111 + ".txt"
    assert (len(meta_name), len(meta_name.encode())) == (170, 220)
    refusal = discovery.refusal(make_study({**base, meta_name: b"no colon\n"}))
    assert refusal.message[0] == DataSegment(data=meta_name)


def _is_unicode_text(name: str) -> bool:
    """RFC 7493's I-JSON text, written out here and not read from the code under test: no lone
    surrogate and no noncharacter."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(ord(char) & 0xFFFE == 0xFFFE or 0xFDD0 <= ord(char) <= 0xFDEF for char in name)


ANY_CHARACTER = st.characters(exclude_categories=())
"""Every character, controls, format characters, surrogates and noncharacters among them."""
SHOWN_NAMES = st.one_of(
    st.text(ANY_CHARACTER, max_size=230),
    st.text(ANY_CHARACTER, min_size=195, max_size=205),
    st.text(st.characters(exclude_categories=["Cs"]), min_size=195, max_size=205),
    st.text(st.characters(exclude_categories=["Cs"]), max_size=230),
    st.binary(max_size=230).map(os.fsdecode),
)


@settings(max_examples=500, deadline=None)
@example("a\x01b")
@example("a\u200bb")
@example("a b")
@example("a\x7fb")
@example("x" * 200)
@example("x" * 201)
@example("\udc80")
@example("a\ufffeb")
@given(name=SHOWN_NAMES)
def test_a_name_is_shown_as_data_when_it_is_text_of_at_most_200_characters(name: str) -> None:
    """The accepting side of ``shown``: every name that is Unicode text of at most 200 characters,
    controls and format characters included (a name is data, shown as plain text), is that name as
    ``data``; every other name is withheld."""
    refusals = importlib.import_module("aibi.packs.onco.refusals")
    if len(name) <= 200 and _is_unicode_text(name):
        assert refusals.shown(name) == [DataSegment(data=name)]
    else:
        assert refusals.shown(name) == [text("an entry whose name cannot be shown")]


def test_a_name_that_is_not_unicode_text_is_withheld(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    found = discovery.run(make_study({**base, b"s\xff.txt": b"x"}))
    assert notes_of(found) == [
        "skipped_source: a file M4.2a does not read: an entry whose name cannot be shown"
    ]
    refusal = discovery.refusal(make_study({**base, b"meta_\xff.txt": b"no colon\n"}))
    assert refusal.message[0] == text("an entry whose name cannot be shown")


def test_more_than_200_skipped_entries_are_counted(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    for number in range(205):
        base[f"x{number:03d}.txt"] = b"x"
    notes = notes_of(discovery.run(make_study(base)))
    assert len(notes) == 201
    assert notes[199] == "skipped_source: a file M4.2a does not read: «x199.txt»"
    assert notes[200] == "skipped_source: further entries M4.2a does not read (5)"


# --- One study, its id, its clinical metas --------------------------------------------------------


def test_no_meta_file_is_no_study_meta(make_study: Any, discovery: Any, base: Any) -> None:
    for tree in (
        {"data.txt": b"x"},
        {"meta_clinical_sample.txt": base["meta_clinical_sample.txt"]},
    ):
        assert discovery.outcome(make_study(tree)) == (
            "onco.NO_STUDY_META",
            "",
            "No meta file in the study's directory is a study's",
        )


def test_no_study_meta(make_study: Any, discovery: Any, base: Any) -> None:
    del base["meta_study.txt"]
    assert discovery.outcome(make_study(base))[0] == "onco.NO_STUDY_META"


def test_a_second_study_meta(make_study: Any, discovery: Any, base: Any) -> None:
    base["meta_study2.txt"] = base["meta_study.txt"]
    assert discovery.outcome(make_study(base))[:2] == (
        "onco.DUPLICATE_STUDY_META",
        "meta_study2.txt",
    )


def test_no_sample_meta(make_study: Any, discovery: Any, base: Any) -> None:
    del base["meta_clinical_sample.txt"]
    assert discovery.outcome(make_study(base)) == (
        "onco.NO_SAMPLE_META",
        "",
        "No meta file in the study's directory is a sample's",
    )


@pytest.mark.parametrize("kind", ["patient", "sample"])
def test_a_second_clinical_meta_of_a_kind(
    make_study: Any, discovery: Any, base: Any, kind: str
) -> None:
    base[f"meta_clinical_{kind}2.txt"] = base[f"meta_clinical_{kind}.txt"]
    assert discovery.outcome(make_study(base))[:2] == (
        "onco.DUPLICATE_CLINICAL_META",
        f"meta_clinical_{kind}2.txt",
    )


@pytest.mark.parametrize("name", ["meta_mutations.txt", "meta_clinical_patient.txt"])
def test_another_study_id(make_study: Any, discovery: Any, base: Any, name: str) -> None:
    base.setdefault(name, MAF_META)
    base[name] = base[name].replace(b"demo_study", b"other_study")
    assert discovery.outcome(make_study(base))[:2] == ("onco.STUDY_MISMATCH", name)


def test_the_first_failure_in_name_order_is_refused(
    make_study: Any, discovery: Any, base: Any, kinds: Any
) -> None:
    """Within the phase that reads each meta file, in the order of the names."""
    tree: dict[str, Any] = {
        **base,
        "meta_b.txt": b"no colon\n",
        "meta_a.txt": b"caf\xe9\n",
        "meta_c.txt": kinds.fifo(),
    }
    directory = make_study(tree)
    reader = discovery.reader(directory)
    refusal = discovery.refusal(directory, reader=reader)
    assert refusal.message == [DataSegment(data="meta_a.txt"), text(" is not UTF-8")]
    assert read_names(reader) == ["meta_a.txt"]
    tree["meta_a.txt"] = kinds.fifo()
    assert discovery.outcome(make_study(tree))[:2] == ("onco.META_FILE", "meta_a.txt")


def test_the_listing_is_ordered_by_name_whatever_the_reader_gives(
    make_study: Any, discovery: Any, base: Any, notes_of: Any
) -> None:
    """``discover`` sorts the one listing itself: a reader that lists in the opposite order
    changes neither the refusal, nor the second study meta named, nor the notes."""
    directory = make_study({**base, "meta_a.txt": b"caf\xe9\n", "meta_z.txt": b"no colon\n"})
    reader = discovery.reader(directory)
    listed_as(reader, reverse=True)
    refusal = discovery.refusal(directory, reader=reader)
    assert refusal.message == [DataSegment(data="meta_a.txt"), text(" is not UTF-8")]
    assert read_names(reader) == ["meta_a.txt"]

    second = {**base, "meta_study2.txt": base["meta_study.txt"]}
    directory = make_study(second)
    reader = discovery.reader(directory)
    listed_as(reader, reverse=True)
    assert discovery.refusal(directory, reader=reader).message[0] == DataSegment(
        data="meta_study2.txt"
    )

    directory = make_study({**base, "x_b.txt": b"x", "x_a.txt": b"x"})
    reader = discovery.reader(directory)
    listed_as(reader, reverse=True)
    assert notes_of(discovery.run(directory, reader=reader)) == [
        "skipped_source: a file M4.2a does not read: «x_a.txt»",
        "skipped_source: a file M4.2a does not read: «x_b.txt»",
    ]


def test_the_clinical_metas_are_checked_in_the_order_of_their_names_not_their_kinds(
    make_study: Any, discovery: Any, base: Any
) -> None:
    """Both clinical metas name a file that is not listed; the one whose name sorts first is the
    one refused, whichever kind it is (``PATIENT`` sorts before ``SAMPLE`` as a kind)."""
    for first, second in (("sample", "patient"), ("patient", "sample")):
        tree = dict(base)
        for kind, name in ((first, "meta_a.txt"), (second, "meta_z.txt")):
            tree[name] = tree.pop(f"meta_clinical_{kind}.txt")
            set_data(tree, name, f"missing_{kind}.txt")
        outcome = discovery.outcome(make_study(tree))
        assert outcome[:2] == ("onco.DATA_FILE", "meta_a.txt"), (first, second)


def _add(name: str, content: bytes) -> Callable[[dict[str, bytes]], None]:
    def change(tree: dict[str, bytes]) -> None:
        tree[name] = content

    return change


def _second_study(tree: dict[str, bytes]) -> None:
    tree["meta_x_study.txt"] = tree["meta_study.txt"]


def _second_sample(tree: dict[str, bytes]) -> None:
    tree["meta_v_sample2.txt"] = tree["meta_clinical_sample.txt"]


def _missing_data_file(tree: dict[str, bytes]) -> None:
    set_data(tree, "meta_clinical_patient.txt", "missing.txt")


def _bad_study_value(tree: dict[str, bytes]) -> None:
    tree["meta_study.txt"] += b"name: " + b"x" * 256 + b"\n"


PHASES: list[tuple[str, Callable[[dict[str, bytes]], None], tuple[str, str]]] = [
    ("read", _add("meta_z_read.txt", b"no colon\n"), ("onco.META_FILE", "meta_z_read.txt")),
    (
        "stable_id",
        _add("meta_y_stable.txt", MAF_META.replace(b"stable_id: mutations", b"stable_id: a b")),
        ("onco.META_VALUE", "meta_y_stable.txt"),
    ),
    ("study meta", _second_study, ("onco.DUPLICATE_STUDY_META", "meta_x_study.txt")),
    (
        "study id",
        _add(
            "meta_w_other.txt",
            MAF_META.replace(b"demo_study", b"other_study").replace(b"mutations", b"other"),
        ),
        ("onco.STUDY_MISMATCH", "meta_w_other.txt"),
    ),
    ("clinical metas", _second_sample, ("onco.DUPLICATE_CLINICAL_META", "meta_v_sample2.txt")),
    ("data file", _missing_data_file, ("onco.DATA_FILE", "meta_clinical_patient.txt")),
    ("study values", _bad_study_value, ("onco.META_VALUE", "meta_study.txt")),
]
"""The phases ``study.py`` runs in, each with a fault and the refusal it alone causes. The faults'
file names sort against the phases (the earlier the phase, the later the name, but for the last
two, whose names are the study's), so that an order of names alone would refuse another."""
PICKS = [(number,) for number in range(len(PHASES))]
PICKS += list(itertools.combinations(range(len(PHASES)), 2))


@pytest.mark.parametrize("picked", PICKS, ids=lambda picked: "+".join(PHASES[n][0] for n in picked))
def test_the_checks_run_in_phases_and_the_earliest_failure_is_refused(
    make_study: Any, discovery: Any, base: Any, picked: tuple[int, ...]
) -> None:
    """Two faults of two phases are refused as the earlier phase's, whatever the names; one fault
    is refused as itself (so that no pair passes for the wrong reason)."""
    for number in picked:
        PHASES[number][1](base)
    code, named = PHASES[picked[0]][2]
    assert discovery.outcome(make_study(base))[:2] == (code, named)


def test_a_bad_stable_id_before_a_file_that_cannot_be_read(
    make_study: Any, discovery: Any, base: Any
) -> None:
    """The reviewer's case: ``meta_a.txt`` holds a ``stable_id`` that is refused, ``meta_z.txt`` a
    line without ``:``; every file is read and parsed before any ``stable_id`` is checked."""
    base["meta_a.txt"] = MAF_META.replace(b"stable_id: mutations", b"stable_id: a b")
    base["meta_z.txt"] = b"no colon\n"
    assert discovery.outcome(make_study(base))[:2] == ("onco.META_FILE", "meta_z.txt")


@dataclasses.dataclass(frozen=True)
class Step:
    """One ordered step of phases 2, 3 and 6, with a fault that fails it alone: ``install`` puts
    the fault in the slot (a meta file's name) of a tree, and the refusal is ``code``, naming the
    slot (``named``) with ``reason``."""

    phase: int
    label: str
    install: Callable[[dict[str, Any], str, Any], None]
    code: str
    named: bool
    reason: str


def _fault_symlink(tree: dict[str, Any], slot: str, kinds: Any) -> None:
    tree[slot] = kinds.symlink("meta_study.txt")


def _fault_bytes(content: bytes) -> Callable[[dict[str, Any], str, Any], None]:
    def install(tree: dict[str, Any], slot: str, kinds: Any) -> None:
        tree[slot] = content

    return install


def _fault_stable_duplicate(tree: dict[str, Any], slot: str, kinds: Any) -> None:
    """``slot`` repeats the ``stable_id`` of a partner named like it without the extension, which
    sorts just before it."""
    tree[slot.removesuffix(".txt")] = MAF_META
    tree[slot] = MAF_META


def _fault_second_patient(tree: dict[str, Any], slot: str, kinds: Any) -> None:
    tree[slot] = tree["meta_clinical_patient.txt"]


def _fault_no_sample(tree: dict[str, Any], slot: str, kinds: Any) -> None:
    tree.pop("meta_clinical_sample.txt", None)


STEPS = [
    Step(2, "read", _fault_symlink, "onco.META_FILE", True, "is a symlink"),
    Step(2, "parse", _fault_bytes(b"no colon\n"), "onco.META_FILE", True, "has a line without ':'"),
    Step(2, "utf8", _fault_bytes(b"caf\xe9\n"), "onco.META_FILE", True, "is not UTF-8"),
    Step(
        2,
        "type",
        _fault_bytes(b"foo: bar\n"),
        "onco.META_FILE",
        True,
        "has no meta file type cBioPortal defines",
    ),
    Step(
        2,
        "mandatory",
        _fault_bytes(MAF_META.replace(b"profile_name: M\n", b"")),
        "onco.META_FIELD",
        True,
        "lacks the key profile_name",
    ),
    Step(
        3,
        "form",
        _fault_bytes(MAF_META.replace(b"stable_id: mutations", b"stable_id: a b")),
        "onco.META_VALUE",
        True,
        "holds a stable_id that M4.2a does not accept",
    ),
    Step(
        3,
        "unique",
        _fault_stable_duplicate,
        "onco.DUPLICATE_STABLE_ID",
        True,
        "repeats another meta file's stable_id",
    ),
    Step(
        6,
        "second",
        _fault_second_patient,
        "onco.DUPLICATE_CLINICAL_META",
        True,
        "is a second clinical meta file of its kind",
    ),
    Step(
        6,
        "no sample",
        _fault_no_sample,
        "onco.NO_SAMPLE_META",
        False,
        "No meta file in the study's directory is a sample's",
    ),
]
"""The steps phases 2, 3 and 6 order, from ``study.py``'s docstring and D409: in phase 2 each
meta file in turn (its read, parse, type, mandatory keys), in phase 3 each file's ``stable_id``
(its form, then its uniqueness), in phase 6 a second of a kind, then no sample meta. Phases 4, 5,
7 and 8 are held against these by ``PHASES``; the two checks of phase 7 cannot fail together (a
meta that names a missing file cannot name one entry with another)."""
SLOTS = ("meta_s1.txt", "meta_s2.txt")
"""The two meta files a pair of faults goes into; both sort after the base study's clinical metas
(a second patient meta is refused as itself) and the unique fault's partner sorts between them."""


def first(steps: tuple[Step, Step]) -> int:
    """The declared precedence: which of two faults, in ``SLOTS`` in turn, is refused. Phases run
    in order. Within phases 2 and 3 the files run in the order of their names, whatever the step
    each fails: a later step of an earlier file comes before an earlier step of a later one. In
    phase 6 the steps are ordered (a second of a kind, then no sample meta); a second of a kind in
    the order of the names."""
    (a, b) = steps
    if a.phase != b.phase:
        return 0 if a.phase < b.phase else 1
    if a.phase == 6:
        order = [step.label for step in STEPS if step.phase == 6]
        return 0 if order.index(a.label) <= order.index(b.label) else 1
    return 0


STEP_PAIRS = list(itertools.product(range(len(STEPS)), repeat=2))


@pytest.mark.parametrize(
    "picked", STEP_PAIRS, ids=lambda picked: "+".join(STEPS[n].label for n in picked)
)
def test_every_pair_of_steps_in_phases_2_3_and_6_is_refused_in_the_declared_order(
    make_study: Any, discovery: Any, base: Any, kinds: Any, picked: tuple[int, int]
) -> None:
    """The first of two faults is in the earlier-named meta file, the second in the later; the
    refusal is the one ``first`` declares, whichever kind each is (the pairs are ordered, so each
    pair is run with its faults in both orders of the names)."""
    steps = (STEPS[picked[0]], STEPS[picked[1]])
    for step, slot in zip(steps, SLOTS, strict=True):
        step.install(base, slot, kinds)
    winner = steps[first(steps)]
    slot = SLOTS[first(steps)]
    code, named, said = discovery.outcome(make_study(base))
    assert code == winner.code
    assert named == (slot if winner.named else "")
    assert winner.reason in said


def test_the_steps_table_is_the_order_the_code_states() -> None:
    """The table is the code's order: the docstring (and D409) name the same steps."""
    doc = importlib.import_module("aibi.packs.onco.study").__doc__ or ""
    assert "each meta file in turn: its read, its parse, its type and its mandatory keys" in doc
    assert "each meta file's ``stable_id`` in turn: its form, then that no earlier file" in doc
    assert "a second of a kind, then no sample meta" in doc
    assert [(step.phase, step.label) for step in STEPS] == [
        (2, "read"),
        (2, "parse"),
        (2, "utf8"),
        (2, "type"),
        (2, "mandatory"),
        (3, "form"),
        (3, "unique"),
        (6, "second"),
        (6, "no sample"),
    ]


# --- The clinical role ----------------------------------------------------------------------------


def test_the_clinical_grid(grid: Any, discovery: Any, tmp_path: Path) -> None:
    """The plan's 764 rows and 4 extra rows (``clinical_grid.py``), each with the outcome the plan
    expects: ``DATA_FILE`` naming the meta file with the reason, or the two data files."""
    root = tmp_path / "grid"
    rows = grid.build(root)
    plan = [row for row in rows.values() if row["plan"]]
    assert (len(plan), len(rows) - len(plan)) == (grid.PLAN_ROWS, grid.EXTRA_ROWS) == (764, 4)
    wrong = []
    for name, row in rows.items():
        outcome = discovery.outcome(root / name)
        expected = row["expected"]
        if "refused" in expected:
            right = outcome[:2] == (expected["refused"], expected["meta"]) and outcome[2] == (
                f"«{expected['meta']}» has a data_filename that {expected['reason']}"
            )
        else:
            right = outcome == ("ok", expected["sample"], expected["patient"])
        if not right:
            wrong.append((name, row["role"], row["spelling"], row["target"], row["axis"], outcome))
    assert wrong == []


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("subdirectory", "names no regular file in the study's directory"),
        ("fifo", "names no regular file in the study's directory"),
        ("alias.txt", "is a symlink"),
        ("meta_mutations.txt", "names a meta file"),
        ("nope.txt", "names no regular file in the study's directory"),
        ("./", "is not a file name M4.2a accepts"),
    ],
)
def test_a_clinical_meta_naming_what_is_no_data_file(
    make_study: Any, discovery: Any, base: Any, kinds: Any, value: str, reason: str
) -> None:
    tree: dict[str, Any] = {
        **base,
        "subdirectory": {},
        "fifo": kinds.fifo(),
        "alias.txt": kinds.symlink("data_clinical_sample.txt"),
        "meta_mutations.txt": MAF_META,
    }
    tree["meta_clinical_sample.txt"] = base["meta_clinical_sample.txt"] + (
        b"data_filename: " + value.encode() + b"\n"
    )
    directory = make_study(tree)
    reader = discovery.reader(directory)
    refusal = discovery.refusal(directory, reader=reader)
    assert refusal.code == "onco.DATA_FILE"
    assert refusal.message == [
        DataSegment(data="meta_clinical_sample.txt"),
        text(" has a data_filename that " + reason),
    ]


def _oracle_name_ok(name: str) -> bool:
    """A name that the plan's P0 rule says a clinical meta may name, and a file may be called, so
    that ``data_filename: <name>`` imports it: 1 to 255 UTF-8 bytes, no ``/`` and no NUL (a
    file's name cannot hold them), not ``.`` or ``..``, its own ``str.strip()`` (a value is
    stripped, as C strips it), no line end (a line ends there), and not meta-named (a meta file is
    not a data file; "meta" is kept out whatever its case)."""
    return (
        0 < len(name.encode()) <= 255
        and "/" not in name
        and "\0" not in name
        and name not in (".", "..")
        and name == name.strip()
        and not any(end in name for end in ("\r", "\n"))
        and "meta" not in name.lower()
    )


accepted_names = st.one_of(
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=60),
    st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="/"),
        min_size=200,
        max_size=255,
    ),
    st.sampled_from(["data..clinical.txt", "a\\b.txt", "-x.txt", "...", "a..", "..a", "a\x1fb"]),
).filter(_oracle_name_ok)
"""Names P0 must accept: any characters but the surrogates (so ``a\\b.txt`` and
``data..clinical.txt`` among them), and printable ASCII names of 200 to 255 bytes, up to the cap."""
STEPS_OF_DOTS = st.lists(st.sampled_from(["./", ".//", "././"]), max_size=6).map("".join)


@settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(name=accepted_names, steps=STEPS_OF_DOTS)
def test_a_clinical_meta_naming_a_name_p0_accepts_is_imported(
    name: str, steps: str, make_study: Any, discovery: Any, base: Any
) -> None:
    """The accepting side of P0, sampled: every name the plan's rule accepts is imported as the
    sample data file, with or without ``./`` steps before it, up to 255 bytes in all; a value of
    256 bytes is refused as P0 does (its reason), never as another."""
    value = steps + name
    tree = dict(base)  # the fixture outlives the examples
    assume(name not in tree)
    set_data(tree, "meta_clinical_sample.txt", value)
    tree[name] = base["data_clinical_sample.txt"]
    outcome = discovery.outcome(make_study(tree))
    if len(value.encode()) <= 255:
        assert outcome == ("ok", name, "data_clinical_patient.txt"), (value, outcome)
    else:
        assert outcome[:2] == ("onco.DATA_FILE", "meta_clinical_sample.txt")
        assert outcome[2].endswith("has a data_filename that is not a file name M4.2a accepts")


@pytest.mark.parametrize("name", ["data..clinical.txt", "a\\b.txt", "-x.txt", "...", "a..", "..a"])
def test_names_cbioportal_opens_are_accepted(
    make_study: Any, discovery: Any, base: Any, name: str
) -> None:
    """The names the property found, kept as cases: ``..`` inside a component, a backslash."""
    assert _oracle_name_ok(name)
    set_data(base, "meta_clinical_sample.txt", name)
    base[name] = base["data_clinical_sample.txt"]
    assert discovery.outcome(make_study(base)) == ("ok", name, "data_clinical_patient.txt")


def test_the_longest_name_is_accepted_and_no_longer_one(
    make_study: Any, discovery: Any, base: Any
) -> None:
    for name in ("n" * 251 + ".txt", "é" * 123 + "nnnnn.txt"):
        assert len(name.encode()) == 255
        tree = dict(base)
        set_data(tree, "meta_clinical_sample.txt", name)
        tree[name] = base["data_clinical_sample.txt"]
        assert discovery.outcome(make_study(tree)) == ("ok", name, "data_clinical_patient.txt")


def test_the_stray_data_filename_of_the_study_meta_is_ignored(
    make_study: Any, discovery: Any, base: Any
) -> None:
    for value in (b"x/", b"/etc/hostname", b"..", b"\x00", b"meta_study.txt"):
        tree = dict(base)
        tree["meta_study.txt"] = base["meta_study.txt"] + b"data_filename: " + value + b"\n"
        assert discovery.outcome(make_study(tree))[0] == "ok", value


# --- Totality -------------------------------------------------------------------------------------

NAMES = [
    "meta_study.txt",
    "meta_clinical_sample.txt",
    "meta_clinical_patient.txt",
    "meta_x.txt",
    "META_y.txt",
    "x_meta",
    ".meta_h.txt",
    "meta_z.txt~",
    "data_clinical_sample.txt",
    "data_clinical_patient.txt",
    "data.txt",
    "case_lists",
    "a",
]
KEYS = [
    "cancer_study_identifier",
    "type_of_cancer",
    "name",
    "description",
    "citation",
    "pmid",
    "groups",
    "short_name",
    "reference_genome",
    "tags_file",
    "genetic_alteration_type",
    "datatype",
    "data_filename",
    "stable_id",
    "resource_type",
    "profile_name",
    "profile_description",
    "show_profile_in_analysis_tab",
    " name",
    "\ufeffname",
]
VALUES = [
    "demo_study",
    "other",
    "brca",
    "CLINICAL",
    "SAMPLE_ATTRIBUTES",
    "PATIENT_ATTRIBUTES",
    "MUTATION_EXTENDED",
    "MAF",
    "CANCER_TYPE",
    "SAMPLE",
    "data_clinical_sample.txt",
    "./data_clinical_patient.txt",
    "meta_study.txt",
    "hg19",
    "12345",
    "",
    " ",
    "a/b",
    "..",
    "/abs",
    "x\x00",
    "\u2028",
]
name_text = st.text(
    alphabet=st.characters(blacklist_characters="/\x00", blacklist_categories=("Cs",)),
    min_size=1,
    max_size=40,
).filter(lambda name: name not in (".", "..") and len(name.encode()) <= 255)
line = st.tuples(
    st.one_of(st.sampled_from(KEYS), st.text(max_size=12)),
    st.sampled_from([":", ": ", "", "::"]),
    st.one_of(st.sampled_from(VALUES), st.text(max_size=30)),
).map(lambda parts: "".join(parts))
meta_content = st.one_of(
    st.lists(line, max_size=10).flatmap(
        lambda lines: st.sampled_from(["\n", "\r\n", "\r", "\u2028"]).map(
            lambda end: end.join(lines).encode("utf-8", "surrogatepass")
        )
    ),
    st.binary(max_size=60),
)
entry = st.one_of(meta_content, st.sampled_from(["directory", "symlink", "fifo"]))
trees = st.dictionaries(st.one_of(st.sampled_from(NAMES), name_text), entry, max_size=8)


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(tree=trees, keep_base=st.booleans())
def test_discover_is_total(
    tree: dict[str, Any],
    keep_base: bool,
    make_study: Any,
    discovery: Any,
    base: Any,
    kinds: Any,
) -> None:
    """Whatever the directory holds, ``discover`` returns a study, refuses it with one of its own
    codes as the core takes a pack's refusal (no path, no limit, no counts), or lets the reader's
    refusal out; it raises nothing else."""
    built: dict[str, Any] = dict(base) if keep_base else {}
    for name, node in tree.items():
        if node == "directory":
            built[name] = {"x": b"y"}
        elif node == "symlink":
            built[name] = kinds.symlink("meta_study.txt")
        elif node == "fifo":
            built[name] = kinds.fifo()
        else:
            built[name] = node
    directory = make_study(built)
    codes = {f"onco.{code}" for code in importlib.import_module(REFUSALS).CODES}
    raised: BaseException | None = None
    found: Any = None
    try:
        found = discovery.run(directory)
    except (Refused, ImportRefused) as error:
        raised = error
    if isinstance(raised, ImportRefused):
        return
    if raised is not None:
        assert type(raised) is Refused
        (refusal,) = raised.refusals
        assert refusal.code in codes
        assert (refusal.path, refusal.limit, refusal.counts) == (None, None, None)
        assert all(type(s) in (TextSegment, DataSegment) for s in refusal.message)
        assert all(len(s.data) <= 200 for s in refusal.message if isinstance(s, DataSegment))
        return
    assert found.sample.data.kind == "file"
    assert len(found.notes) <= 202


def test_the_refusal_codes_are_pinned() -> None:
    assert importlib.import_module(REFUSALS).CODES == (
        "META_FILE",
        "META_FIELD",
        "META_VALUE",
        "TOO_MANY_FILES",
        "NO_STUDY_META",
        "DUPLICATE_STUDY_META",
        "NO_SAMPLE_META",
        "DUPLICATE_CLINICAL_META",
        "STUDY_MISMATCH",
        "DATA_FILE",
        "DUPLICATE_STABLE_ID",
    )


# --- Sentinel: no value a file holds is shown -----------------------------------------------------

SENTINEL = "zQ7sEnTiNeL"


def scan(found: str) -> bool:
    """Whether ``found`` holds the sentinel, in any case."""
    return SENTINEL.lower() in found.lower()


def test_the_scan_finds_the_sentinel() -> None:
    """The self-check: the scan finds the sentinel in any case, and inside a longer word."""
    assert scan(SENTINEL.upper())
    assert scan("x" + SENTINEL.lower() + "y")
    refusal = Refusal(code="onco.META_FILE", path=None, message=[text(SENTINEL)])
    assert scan(refusal.model_dump_json())
    assert not scan(SENTINEL[:-1])


def sentinel_cases(base: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    """Studies refused, and one imported, each holding the sentinel in values and cells only."""
    marked = SENTINEL.encode()

    def study(extra: bytes) -> dict[str, Any]:
        return {**base, "meta_study.txt": base["meta_study.txt"] + extra}

    def sample(extra: bytes) -> dict[str, Any]:
        return {**base, "meta_clinical_sample.txt": base["meta_clinical_sample.txt"] + extra}

    def other(content: bytes) -> dict[str, Any]:
        return {**base, "meta_x.txt": content}

    cases = {
        f"study {key}": study(key.encode() + b": " + marked + b"\x01\n")
        for key in ("name", "description", "citation", "pmid", "groups", "short_name")
    }
    cases.update(
        {
            "study id": study(b"cancer_study_identifier: " + marked + b"\n"),
            "type_of_cancer": study(b"type_of_cancer: " + marked + b" x\n"),
            "genome": study(b"reference_genome: " + marked + b"\n"),
            "unknown pair": other(
                b"genetic_alteration_type: " + marked + b"\ndatatype: " + marked + b"\n"
            ),
            "missing key": other(MAF_META.replace(b"profile_name: M", b"x: " + marked)),
            "stable_id": other(MAF_META.replace(b"stable_id: mutations", b"stable_id: /" + marked)),
            "stable_id repeated": {
                **other(MAF_META.replace(b"stable_id: mutations", b"stable_id: " + marked)),
                "meta_y.txt": MAF_META.replace(b"stable_id: mutations", b"stable_id: " + marked),
            },
            "study mismatch": other(MAF_META.replace(b"demo_study", marked)),
            "oversize": other(MAF_META + b"padding: " + marked + b"x" * 70_000 + b"\n"),
            "no colon": other(marked + b"\n"),
            "not utf-8": other(marked + b": \xe9\n"),
            "data_filename P0": sample(b"data_filename: /" + marked + b"\n"),
            "data_filename none": sample(b"data_filename: " + marked + b"\n"),
            "data_filename meta": {
                **sample(b"data_filename: meta_" + marked + b".txt\n"),
                f"meta_{SENTINEL}.txt": MAF_META,
            },
            "imported": {
                **study(b"description: " + marked + b"\ntags_file: " + marked + b"\n"),
                "data_clinical_sample.txt": base["data_clinical_sample.txt"] + marked,
                "meta_x.txt": MAF_META.replace(b"profile_name: M", b"profile_name: " + marked),
                "data.txt": marked,
            },
        }
    )
    return cases


def test_no_value_reaches_a_refusal_a_note_or_a_log(
    make_study: Any, discovery: Any, base: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    cases = sentinel_cases(base)
    refused: list[str] = []
    for case, tree in cases.items():
        directory = make_study(tree)
        raised: Refused | None = None
        found: Any = None
        try:
            found = discovery.run(directory)
        except Refused as error:
            raised = error
        if raised is not None:
            refused.append(case)
            (refusal,) = raised.refusals
            chain = "".join(traceback.format_exception(raised))
            shown = [refusal.model_dump_json(), str(raised), repr(raised), repr(raised.args), chain]
            assert not any(scan(part) for part in shown), case
            # No exception hides behind the refusal: the chain as formatted is the refusal alone.
            assert raised.__cause__ is None, case
            assert chain.count("Traceback (most recent call last)") == 1, case
            continue
        assert not any(scan(repr(note.message)) for note in found.notes), case
        assert scan(found.fields.description)  # the declared value is kept, as written
    assert refused == [case for case in cases if case != "imported"]
    assert not any(scan(record.getMessage()) for record in caplog.records)


def test_an_entry_name_is_shown_as_data_only(make_study: Any, discovery: Any, base: Any) -> None:
    """The scan's other half: a name holding the sentinel is shown, and only in ``data``."""
    refusal = discovery.refusal(make_study({**base, f"meta_{SENTINEL}.txt": b"no colon\n"}))
    assert scan(refusal.model_dump_json())
    assert not any(scan(s.text) for s in refusal.message if isinstance(s, TextSegment))
    found = discovery.run(make_study({**base, f"{SENTINEL}.txt": b"x"}))
    (note,) = found.notes
    assert [type(s) for s in note.message] == [TextSegment, DataSegment]
    assert scan(note.message[1].data)
    assert not scan(note.message[0].text)
