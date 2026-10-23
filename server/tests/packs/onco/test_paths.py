"""The path values outside the clinical role (M4.2a-1a-paths, D409's phases 9 and 10, D436).

A non-clinical meta's ``data_filename``, a CNA_DISCRETE meta's ``pd_annotations_filename`` and the
study meta's ``tags_file`` are checked once each, after the study meta's values; nothing they name
is read. The rule, as this module's oracle restates it (``expected``):

- **P0**, on the value as written: empty, over 255 bytes, a NUL, ``.`` or ``..``, or a ``/``
  (absolute, ``./x``, ``x/../y``, a subdirectory, a trailing separator) is refused;
- **Kind**: an entry of kind ``symlink`` equal to the value, exactly or under ``fold``, is
  refused;
- **Reads**: a member of Reads(S) (the clinical data files and Meta(S)) equal to it, exactly or
  under ``fold``, is refused (``DATA_FILE``; ``META_VALUE`` for the tags file);
- a value equal **exactly** to a listed regular file is accepted, and the entry is noted as what
  the value makes it (unless an earlier reason, a hidden file or a backup, already notes it); any
  other data or PD value is accepted with no note naming a file (a file of a kind never imported,
  exit 3 (20)); every other tags value is refused.

``fold`` is canonical caseless matching, ``NFD(casefold(NFD(s)))`` (The Unicode Standard §3.13,
definition D145). It is written here again, and so is every part of the oracle: from the pack,
this module calls only ``discover`` (through the ``discovery`` fixture or ``_discover``), and
``PATH_SITES`` only to pin it.

- **The corpus.** Every cell of the paths plan's live differential (the pinned validator run on
  synthetic studies, outside the repository) is a named case here, built from
  ``clinical_grid.BASE`` and the cell's own entries, with aibi's expected outcome (a refusal, or
  accepted with the notes it carries). Each cell also carries the reference's outcome as it was
  recorded, per error: data, not asserted (``REFERENCE``). Two rows are modelled, not run: APFS
  opens a name's fold-equal entry, so there the reference reads what Linux cannot open.
- **The brute force**: spellings by entry kind by role by membership of Reads(S), against the
  oracle; and a Hypothesis property over any value and any listing.
- **Ignored sites**, every type in the ``data_filename`` row, reads (nothing but Meta(S) is read
  or opened), totality and the sentinel (in ``test_study.py``).
"""

import importlib
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import DataSegment, TextSegment
from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry, Refused

STUDY_MODULE = "aibi.packs.onco.study"
DATA_NOTE = "a data file of a meta file M4.2a does not import"
TAGS_NOTE = "the study's tags file"
PLAIN = "a file M4.2a does not read"
UNSHOWN = "an entry whose name cannot be shown"
NOT_PROPOSED = "not_proposed: the study's tags file is not read by M4.2a"

P0 = "is not a file name M4.2a accepts"
SYMLINK = "names a symlink, exactly or under case and normalisation folding"
READS = "names a meta file or a clinical data file, exactly or under case and normalisation folding"
NONE = "names no regular file in the study's directory"


def fold(name: str) -> str:
    """D145's canonical caseless form, written here again (not the pack's)."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def p0(value: str) -> bool:
    """Whether P0 refuses the value, each shape D409 names checked on its own."""
    return (
        value == ""
        or len(value.encode("utf-8", "surrogatepass")) > 255
        or "\0" in value
        or value.startswith("/")
        or value in (".", "..")
        or ".." in value.split("/")
        or value.endswith("/")
        or "/" in value
    )


def kept_out(name: str) -> str | None:
    """Why an entry is excluded from Meta(S) (V:4713-4715), as its skip note says it: written here
    again, with the note's precedence (an earlier reason wins over a data file's or the tags
    file's)."""
    if name.startswith("."):
        return "a hidden file"
    if name.endswith("~") or name.startswith("ONCOKB_IMPORT_BACKUP"):
        return "an editor or OncoKB backup"
    return None


# --- The meta files the cells use (the live run's builders, re-expressed) ------------------------

SID = "demo_study"
MAF = (
    b"Hugo_Symbol\tEntrez_Gene_Id\tTumor_Sample_Barcode\tVariant_Classification\tHGVSp_Short\n"
    b"TP53\t7157\tS1\tMissense_Mutation\tp.R175H\n"
)
SEQ = (
    b"cancer_study_identifier: demo_study\nstable_id: demo_study_sequenced\n"
    b"case_list_name: s\ncase_list_description: s\ncase_list_ids: S1\tS2\n"
)
CNA = b"Hugo_Symbol\tEntrez_Gene_Id\tS1\nTP53\t7157\t0\n"
BAD_MAF = b"Hugo_Symbol\tfoo\nTP53\tx\n"
TL = b"PATIENT_ID\tSTART_DATE\tSTOP_DATE\tEVENT_TYPE\nP1\t0\t10\tTREATMENT\n"
PD = (
    b"SAMPLE_ID\tHugo_Symbol\tEntrez_Gene_Id\tcbp_driver\tcbp_driver_annotation\n"
    b"S1\tTP53\t7157\tPutative_Driver\tx\n"
)
YAML = b"a: 1\n"
DUAL_PATIENT = (
    b"#Patient\tStart\tStop\tEvent\n#a\tb\tc\td\n#STRING\tNUMBER\tNUMBER\tSTRING\n#1\t1\t1\t1\n"
    b"PATIENT_ID\tSTART_DATE\tSTOP_DATE\tEVENT_TYPE\nP1\t0\t10\tTREATMENT\nP2\t0\t5\tTREATMENT\n"
)
"""A patient file that is also a valid timeline file."""


def maf_meta(value: str) -> bytes:
    return (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: MUTATION_EXTENDED\n"
        "datatype: MAF\nstable_id: mutations\nshow_profile_in_analysis_tab: true\n"
        f"profile_name: M\nprofile_description: M\ndata_filename: {value}\n"
    ).encode()


def seg_meta(value: str) -> bytes:
    return (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
        f"datatype: SEG\nreference_genome_id: hg19\ndescription: s\ndata_filename: {value}\n"
    ).encode()


def timeline_meta(value: str) -> bytes:
    return (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: CLINICAL\n"
        f"datatype: TIMELINE\ndata_filename: {value}\n"
    ).encode()


def cancer_meta(value: str) -> bytes:
    return (
        f"genetic_alteration_type: CANCER_TYPE\ndatatype: CANCER_TYPE\ndata_filename: {value}\n"
    ).encode()


def resource_def_meta(value: str) -> bytes:
    return (
        f"cancer_study_identifier: {SID}\nresource_type: DEFINITION\ndata_filename: {value}\n"
    ).encode()


def cna_meta(value: str, pd: str | None, datatype: str = "DISCRETE") -> bytes:
    found = (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: COPY_NUMBER_ALTERATION\n"
        f"datatype: {datatype}\nstable_id: gistic\nshow_profile_in_analysis_tab: true\n"
        f"profile_name: C\nprofile_description: C\ndata_filename: {value}\n"
    )
    if pd is not None:
        found += f"pd_annotations_filename: {pd}\n"
    return found.encode()


def profile_meta(alteration: str, datatype: str, stable: str, value: str) -> bytes:
    return (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: {alteration}\n"
        f"datatype: {datatype}\nstable_id: {stable}\nshow_profile_in_analysis_tab: true\n"
        f"profile_name: p\nprofile_description: p\ndata_filename: {value}\n"
    ).encode()


def gpm_meta(value: str) -> bytes:
    return (
        f"cancer_study_identifier: {SID}\ngenetic_alteration_type: GENE_PANEL_MATRIX\n"
        f"datatype: GENE_PANEL_MATRIX\ndata_filename: {value}\n"
    ).encode()


# --- The corpus ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refuse:
    """Refused with ``code``, naming the meta file ``meta``: ``has a <key> that <reason>``."""

    code: str
    meta: str
    key: str
    reason: str


@dataclass(frozen=True)
class Accept:
    """Imported, and the entries named here have these skip notes (``None``: no note, the entry
    is imported). No other entry has a path value's note; the ``not_proposed`` note is there
    exactly when the study meta holds a ``tags_file``."""

    notes: Mapping[str, str | None] = field(default_factory=dict[str, "str | None"])


def data(meta: str, reason: str, key: str = "data_filename") -> Refuse:
    return Refuse("onco.DATA_FILE", meta, key, reason)


def tags(reason: str) -> Refuse:
    return Refuse("onco.META_VALUE", "meta_study.txt", "tags_file", reason)


Node = Any
Build = Callable[[Mapping[str, bytes], Path], dict[str, Node]]


def study_with_tags(base: Mapping[str, bytes], value: str) -> bytes:
    return base["meta_study.txt"] + f"tags_file: {value}\n".encode()


def sample_named(base: Mapping[str, bytes], name: str) -> bytes:
    return base["meta_clinical_sample.txt"].replace(b"data_clinical_sample.txt", name.encode())


SYM = "symlink"
FIFO = "fifo"
MODE = "mode"
SAMPLE_FILE = b"<base>data_clinical_sample.txt"
PATIENT_FILE = b"<base>data_clinical_patient.txt"
"""A cell's entry holding ``clinical_grid.BASE``'s own bytes of that clinical data file (the live
run's), not a placeholder: the reference parses them, so the cell is the one it ran."""
CNA_LIST = (
    f"cancer_study_identifier: {SID}\nstable_id: {SID}_cna\ncase_list_name: c\n"
    "case_list_description: c\ncase_list_ids: S1\tS2\n"
).encode()


def cell(
    *extra: tuple[str, Node],
    study: str | bytes | None = None,
    sample_meta: str | None = None,
    patient_meta: str | None = None,
    drop: Sequence[str] = (),
) -> Build:
    """A cell's study: ``clinical_grid.BASE`` less ``drop``, the study meta's ``tags_file``
    (a ``str``) or a whole study meta (``bytes``), the clinical metas naming other files, and the
    cell's entries, a path inside a subdirectory written ``sub/name``; a node is ``bytes``, ``{}``
    (a directory), ``(SYM, target)``, ``(MODE, content, mode)`` or ``FIFO``. As in the live run, a
    study with a ``meta_mut*`` meta gets ``case_lists/cases_sequenced.txt``."""

    def build(base: Mapping[str, bytes], shared: Path) -> dict[str, Node]:
        tree: dict[str, Node] = {
            name: content for name, content in base.items() if name not in drop
        }
        if isinstance(study, str):
            tree["meta_study.txt"] = study_with_tags(base, study)
        elif isinstance(study, bytes):
            tree["meta_study.txt"] = study
        if sample_meta is not None:
            tree["meta_clinical_sample.txt"] = sample_named(base, sample_meta)
        if patient_meta is not None:
            tree["meta_clinical_patient.txt"] = base["meta_clinical_patient.txt"].replace(
                b"data_clinical_patient.txt", patient_meta.encode()
            )
        for path, node in extra:
            value = node(shared) if callable(node) else node
            if value in (SAMPLE_FILE, PATIENT_FILE):
                value = base[value.removeprefix(b"<base>").decode()]
            head, _, name = path.rpartition("/")
            (tree.setdefault(head, {}) if head else tree)[name] = value
        if any(name.startswith("meta_mut") for name in tree) and "case_lists" not in tree:
            tree["case_lists"] = {"cases_sequenced.txt": SEQ}
        return tree

    return build


def _absolute(shared: Path) -> bytes:
    return maf_meta(str(shared / "data_mut.txt"))


NFC_SAMPLE = unicodedata.normalize("NFC", "data_clinical_samplé.txt")
NFD_SAMPLE = unicodedata.normalize("NFD", NFC_SAMPLE)
NFC_TAGS = unicodedata.normalize("NFC", "tagé.yml")
NFD_TAGS = unicodedata.normalize("NFD", NFC_TAGS)
CASES_SUBDIR = "a subdirectory (case lists are M4.2b's)"
BACKUP = "an editor or OncoKB backup"
HIDDEN = "a hidden file"


def _generic(index: int, target: str) -> tuple[str, Node]:
    alteration, datatype, stable = GENERIC[index]
    return ("meta_x.txt", profile_meta(alteration, datatype, stable, target))


GENERIC = [
    ("COPY_NUMBER_ALTERATION", "DISCRETE", "gistic"),
    ("STRUCTURAL_VARIANT", "SV", "structural_variants"),
    ("MRNA_EXPRESSION", "CONTINUOUS", "mrna"),
    ("PROTEIN_LEVEL", "LOG2-VALUE", "rppa"),
]
GENERIC_TARGETS = {
    "m": "meta_study.txt",
    "s": "meta_clinical_sample.txt",
    "p": "data_clinical_patient.txt",
}

CELLS: dict[str, tuple[Build, Refuse | Accept]] = {
    "02": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", (SYM, "data_clinical_sample.txt")),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "03": (
        cell(("meta_mut.txt", maf_meta("link/data_clinical_sample.txt")), ("link", (SYM, "."))),
        data("meta_mut.txt", P0),
    ),
    "04": (
        cell(
            ("meta_mut.txt", maf_meta("sub/data_mut.txt")),
            ("sub/data_mut.txt", (SYM, "../data_clinical_sample.txt")),
        ),
        data("meta_mut.txt", P0),
    ),
    "05": (
        cell(("meta_mut.txt", maf_meta("sub/data_mut.txt")), ("sub/data_mut.txt", MAF)),
        data("meta_mut.txt", P0),
    ),
    "06": (cell(("meta_mut.txt", maf_meta("../shared/data_mut.txt"))), data("meta_mut.txt", P0)),
    "07": (cell(("meta_mut.txt", _absolute)), data("meta_mut.txt", P0)),
    "08": (
        cell(("meta_mut.txt", maf_meta("data_clinical_sample.txt"))),
        data("meta_mut.txt", READS),
    ),
    "09": (cell(("meta_mut.txt", maf_meta("meta_study.txt"))), data("meta_mut.txt", READS)),
    "10": (cell(("meta_seg.txt", seg_meta("meta_study.txt"))), data("meta_seg.txt", READS)),
    "11": (cell(("meta_tl.txt", timeline_meta("meta_study.txt"))), data("meta_tl.txt", READS)),
    "12": (cell(("meta_ct.txt", cancer_meta("meta_study.txt"))), data("meta_ct.txt", READS)),
    "13": (cell(("meta_rd.txt", resource_def_meta("meta_study.txt"))), data("meta_rd.txt", READS)),
    "14": (
        cell(("meta_tl.txt", timeline_meta("data_clinical_sample.txt"))),
        data("meta_tl.txt", READS),
    ),
    "15": (cell(("sub/tags.yml", YAML), study="sub/tags.yml"), tags(P0)),
    "16": (cell(study="meta_clinical_sample.txt"), tags(READS)),
    "17": (cell(study="data_clinical_sample.txt"), tags(READS)),
    "18": (cell(("tags.yml", YAML), study="tags.yml"), Accept({"tags.yml": TAGS_NOTE})),
    "19": (
        cell(("case_lists/cases_x.txt", (SYM, "../data_clinical_sample.txt"))),
        Accept({"case_lists": CASES_SUBDIR}),
    ),
    "20": (
        cell(
            ("meta_cna.txt", cna_meta("link/data_cna.txt", "data_clinical_sample.txt")),
            ("link", (SYM, ".")),
            ("data_cna.txt", CNA),
        ),
        data("meta_cna.txt", P0),
    ),
    "21": (
        cell(
            ("meta_cna.txt", cna_meta("data_cna.txt", "data_clinical_sample.txt")),
            ("data_cna.txt", CNA),
        ),
        data("meta_cna.txt", READS, "pd_annotations_filename"),
    ),
    "22": (
        cell(("meta_cna.txt", cna_meta("data_cna.txt", None)), ("data_cna.txt", CNA)),
        Accept({"data_cna.txt": DATA_NOTE}),
    ),
    "23": (
        cell(("meta_mut.txt", maf_meta("DATA_CLINICAL_SAMPLE.txt"))),
        data("meta_mut.txt", READS),
    ),
    "24": (
        cell(("tags.yml", (SYM, "real.yml")), ("real.yml", YAML), study="tags.yml"),
        tags(SYMLINK),
    ),
    "25": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            (
                "meta_mut2.txt",
                maf_meta("data_mut.txt").replace(
                    b"stable_id: mutations", b"stable_id: mutations_uncalled"
                ),
            ),
            ("data_mut.txt", MAF),
        ),
        Accept({"data_mut.txt": DATA_NOTE}),
    ),
    "26": (cell(("meta_mut.txt", maf_meta("meta_mut.txt"))), data("meta_mut.txt", READS)),
    "27": (
        cell(
            (
                "meta_cnal.txt",
                cna_meta("data_cnal.txt", "data_clinical_sample.txt", "DISCRETE_LONG"),
            ),
            (
                "data_cnal.txt",
                b"Hugo_Symbol\tEntrez_Gene_Id\tSample_Id\tValue\nTP53\t7157\tS1\t0\n",
            ),
        ),
        Accept({"data_cnal.txt": DATA_NOTE}),
    ),
    **{
        f"3{index}{tag}": (cell(_generic(index, target)), data("meta_x.txt", READS))
        for index in range(len(GENERIC))
        for tag, target in GENERIC_TARGETS.items()
    },
    "40": (cell(("meta_gpm.txt", gpm_meta("meta_study.txt"))), data("meta_gpm.txt", READS)),
    "50": (cell(("tags.yml", YAML), study="TAGS.yml"), tags(NONE)),
    "51": (cell((NFC_TAGS, YAML), study=NFD_TAGS), tags(NONE)),
    "52": (cell(("straße.yml", YAML), study="strasse.yml"), tags(NONE)),
    "53": (cell(("tags.yml", YAML), study="tags.yml."), tags(NONE)),
    "54": (cell(study=""), tags(P0)),
    "55": (
        cell(
            ("meta_mut.txt", maf_meta("DATA_CLINICAL_SAMPLE.txt")),
            ("DATA_CLINICAL_SAMPLE.txt", MAF),
        ),
        data("meta_mut.txt", READS),
    ),
    "56": (
        cell(("meta_mut.txt", maf_meta("./data_mut.txt")), ("data_mut.txt", MAF)),
        data("meta_mut.txt", P0),
    ),
    "57": (
        cell(
            ("meta_tl.txt", timeline_meta("data_tl.txt")),
            ("meta_tl2.txt", timeline_meta("data_tl.txt")),
            ("data_tl.txt", TL),
        ),
        Accept({"data_tl.txt": DATA_NOTE}),
    ),
    "58": (
        cell(
            ("meta_tl.txt", timeline_meta("data_tl.txt")),
            ("meta_tl2.txt", timeline_meta("DATA_TL.txt")),
            ("data_tl.txt", TL),
        ),
        Accept({"data_tl.txt": DATA_NOTE}),
    ),
    "59": (
        cell(("meta_cna.txt", cna_meta("data_cna.txt", "nofile.txt")), ("data_cna.txt", CNA)),
        Accept({"data_cna.txt": DATA_NOTE}),
    ),
    "60": (
        cell(("meta_cna.txt", cna_meta("data_cna.txt", "")), ("data_cna.txt", CNA)),
        data("meta_cna.txt", P0, "pd_annotations_filename"),
    ),
    "61": (cell(("meta_mut.txt", maf_meta(""))), data("meta_mut.txt", P0)),
    "62": (cell(("meta_mut.txt", maf_meta("case_lists"))), Accept({"case_lists": CASES_SUBDIR})),
    "63": (cell(("meta_mut.txt", maf_meta("nofile.txt"))), Accept()),
    "64": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")), ("data_mut.txt", MAF), study="data_mut.txt"
        ),
        Accept({"data_mut.txt": DATA_NOTE}),
    ),
    "65": (
        cell(
            ("meta_mut.txt", maf_meta(NFD_SAMPLE)),
            (NFD_SAMPLE, MAF),
            (NFC_SAMPLE, SAMPLE_FILE),
            sample_meta=NFC_SAMPLE,
            drop=("data_clinical_sample.txt",),
        ),
        data("meta_mut.txt", READS),
    ),
    "66": (
        cell(("meta_cna.txt", cna_meta("data_cna.txt", "data_cna.txt")), ("data_cna.txt", CNA)),
        Accept({"data_cna.txt": DATA_NOTE}),
    ),
    "67": (
        cell(
            ("meta_tl.txt", timeline_meta("data_tl.txt")),
            ("data_tl.txt", TL),
            ("data_tl.txt~", TL),
        ),
        Accept({"data_tl.txt": DATA_NOTE, "data_tl.txt~": BACKUP}),
    ),
    "68": (
        cell(study=b"<base>data_filename: data_clinical_sample.txt\n"),
        Accept({"data_clinical_sample.txt": None}),
    ),
    "69": (cell(("case_lists", {}), study="case_lists"), tags(NONE)),
    "70": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", MAF),
            ("DATA_MUT.txt", (SYM, "data_mut.txt")),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "71": (
        cell(("tags.yml", YAML), ("TAGS.yml", YAML), study="tags.yml"),
        Accept({"tags.yml": TAGS_NOTE, "TAGS.yml": PLAIN}),
    ),
    "72": (
        cell(("tags.yml", YAML), ("TAGS.yml", (SYM, "tags.yml")), study="tags.yml"),
        tags(SYMLINK),
    ),
    "73": (cell(("tags.yml", b"a: \xff\xfe\n"), study="tags.yml"), Accept({"tags.yml": TAGS_NOTE})),
    "74": (cell(("tags.yml", b"a: [1,\n"), study="tags.yml"), Accept({"tags.yml": TAGS_NOTE})),
    "75": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")), ("data_mut.txt", MAF), study="data_mut.txt"
        ),
        Accept({"data_mut.txt": DATA_NOTE}),
    ),
    "76": (cell((".tags.yml", YAML), study=".tags.yml"), Accept({".tags.yml": HIDDEN})),
    "77": (
        cell(("meta_tags.txt~", YAML), study="meta_tags.txt~"),
        Accept({"meta_tags.txt~": BACKUP}),
    ),
    "78": (
        cell(
            ("tags.yml", b"a: !!python/object/apply:open ['data_clinical_sample.txt']\n"),
            study="tags.yml",
        ),
        Accept({"tags.yml": TAGS_NOTE}),
    ),
    "79": (
        cell(("meta_mut.txt", maf_meta(".data_mut.txt")), (".data_mut.txt", MAF)),
        Accept({".data_mut.txt": HIDDEN}),
    ),
    "80": (
        cell(
            ("meta_cna.txt", cna_meta("data_cna.txt", "data_pd.txt")),
            ("data_cna.txt", CNA),
            ("data_pd.txt", PD),
        ),
        Accept({"data_cna.txt": DATA_NOTE, "data_pd.txt": DATA_NOTE}),
    ),
    "81": (
        cell(
            ("meta_cna.txt", cna_meta("data_cna.txt", "DATA_PD.txt")),
            ("data_cna.txt", CNA),
            ("data_pd.txt", PD),
        ),
        Accept({"data_cna.txt": DATA_NOTE, "data_pd.txt": PLAIN}),
    ),
    "82": (
        cell(
            ("meta_cna.txt", cna_meta("data_cna.txt", "data_pd.txt")),
            ("data_cna.txt", CNA),
            ("data_pd.txt", (SYM, "real_pd.txt")),
            ("real_pd.txt", PD),
        ),
        data("meta_cna.txt", SYMLINK, "pd_annotations_filename"),
    ),
    "83": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", (SYM, "real_mut.txt")),
            ("real_mut.txt", MAF),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "84": (
        cell(("meta_mut.txt", maf_meta("sub/../data_mut.txt")), ("sub", {}), ("data_mut.txt", MAF)),
        data("meta_mut.txt", P0),
    ),
    "85": (
        cell(("meta_mut.txt", maf_meta("data_mut.txt/")), ("data_mut.txt", MAF)),
        data("meta_mut.txt", P0),
    ),
    "86": (
        cell(("meta_mut.txt", maf_meta("tags.yml")), ("tags.yml", YAML), study="tags.yml"),
        Accept({"tags.yml": DATA_NOTE}),
    ),
    "87": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", SAMPLE_FILE),
            sample_meta="data_mut.txt",
            drop=("data_clinical_sample.txt",),
        ),
        data("meta_mut.txt", READS),
    ),
    "88": (
        cell(
            ("meta_mut.txt", maf_meta("data_s.txt")),
            ("data_s.txt", MAF),
            ("Data_S.txt", SAMPLE_FILE),
            sample_meta="Data_S.txt",
            drop=("data_clinical_sample.txt",),
        ),
        data("meta_mut.txt", READS),
    ),
    "89": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", MAF),
            ("DATA_MUT.txt", {}),
        ),
        Accept({"data_mut.txt": DATA_NOTE, "DATA_MUT.txt": "a subdirectory"}),
    ),
    "90": (
        cell(
            ("tags.yml", YAML),
            ("TAGS.yml", SAMPLE_FILE),
            sample_meta="TAGS.yml",
            drop=("data_clinical_sample.txt",),
            study="tags.yml",
        ),
        tags(READS),
    ),
    "91": (cell(study="META_CLINICAL_PATIENT.txt"), tags(READS)),
    "92": (
        cell(("meta_mut.txt", maf_meta("meta_x.txt~")), ("meta_x.txt~", MAF)),
        Accept({"meta_x.txt~": BACKUP}),
    ),
    "93": (
        cell(
            (
                "meta_cna.txt",
                cna_meta("data_cna.txt", unicodedata.normalize("NFD", "Data_clinical_samplé.txt")),
            ),
            ("data_cna.txt", CNA),
        ),
        Accept({"data_cna.txt": DATA_NOTE}),
    ),
    "94": (
        cell(("meta_mut.txt", maf_meta("data_mut.txt")), ("data_mut.txt", FIFO)),
        Accept({"data_mut.txt": "not a regular file"}),
    ),
    "95": (cell(("tags.yml", {}), ("TAGS.yml", YAML), study="tags.yml"), tags(NONE)),
    "96": (
        cell(
            ("Data_clinical_sample.txt", PATIENT_FILE),
            patient_meta="Data_clinical_sample.txt",
            drop=("data_clinical_patient.txt",),
        ),
        Accept({"Data_clinical_sample.txt": None, "data_clinical_sample.txt": None}),
    ),
    "97": (cell(("tags.yml", b"[" * 20_000), study="tags.yml"), Accept({"tags.yml": TAGS_NOTE})),
    # The code reviews' cells (N, C, then D): each an instance of one branch of the rule, which
    # D409's items (27)-(35) hold by branch and by the reference's reported outcome alone, aibi
    # refusing whatever the reference reports.
    "N01": (
        cell(
            ("meta_tl.txt", timeline_meta("data_clinical_patient.txt")),
            ("data_clinical_patient.txt", DUAL_PATIENT),
        ),
        data("meta_tl.txt", READS),
    ),
    "N05": (cell(("tags.yml", FIFO), study="tags.yml"), tags(NONE)),
    "N22": (cell(("tags.yml", (SYM, "nowhere.yml")), study="tags.yml"), tags(SYMLINK)),
    "N25": (
        cell(
            ("meta_cna.txt", cna_meta("data_cna.txt", "meta_cna.txt")),
            ("data_cna.txt", CNA),
            ("case_lists/cases_cna.txt", CNA_LIST),
        ),
        data("meta_cna.txt", READS, "pd_annotations_filename"),
    ),
    "N13": (
        cell(("meta_mut.txt", maf_meta("data\\mut.txt")), ("data\\mut.txt", MAF)),
        Accept({"data\\mut.txt": DATA_NOTE}),
    ),
    "C01": (
        cell(("meta_mut.txt", maf_meta("data\0mut.txt")), ("data", MAF)),
        data("meta_mut.txt", P0),
    ),
    "C02": (cell(("meta_mut.txt", maf_meta("."))), data("meta_mut.txt", P0)),
    "C03": (cell(("meta_mut.txt", maf_meta(".."))), data("meta_mut.txt", P0)),
    "C04": (cell(("meta_mut.txt", maf_meta("a" * 256))), data("meta_mut.txt", P0)),
    "C05": (cell(study="."), tags(P0)),
    "C06": (cell(study=".."), tags(P0)),
    "C07": (cell(("t", YAML), study="t\0.yml"), tags(P0)),
    "C08": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", (SYM, "nowhere.txt")),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "C09": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", (SYM, "case_lists")),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "C10": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", (SYM, "real.txt")),
            ("real.txt", b"garbage\n"),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "C11": (
        cell(
            ("tags.yml", (SYM, "real.yml")),
            ("real.yml", b"a: \xff\xfe\n"),
            study="tags.yml",
        ),
        tags(SYMLINK),
    ),
    "C12": (
        cell(("tags.yml", (SYM, "case_lists")), ("case_lists", {}), study="tags.yml"),
        tags(SYMLINK),
    ),
    "C13": (
        cell(
            ("meta_mut.txt", maf_meta("link/data_mut.txt")),
            ("link", lambda shared: (SYM, str(shared))),
        ),
        data("meta_mut.txt", P0),
    ),
    "C14": (
        cell(
            ("meta_mut.txt", maf_meta("link/../data_mut.txt")),
            ("link", lambda shared: (SYM, str(shared / "sub"))),
        ),
        data("meta_mut.txt", P0),
    ),
    "C16": (
        cell(
            ("meta_cna.txt", cna_meta("data_cna.txt", "data_pd.txt")),
            ("data_cna.txt", CNA),
            ("DATA_PD.txt", (SYM, "real_pd.txt")),
            ("real_pd.txt", PD),
            ("case_lists/cases_cna.txt", CNA_LIST),
        ),
        data("meta_cna.txt", SYMLINK, "pd_annotations_filename"),
    ),
    "C18": (cell(("tags.yml", YAML), study="tags.yml/"), tags(P0)),
    "C20": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", (SYM, "pipe")),
            ("pipe", FIFO),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "C21": (
        cell(("tags.yml", (SYM, "real.yml")), ("real.yml", b"a: [1,\n"), study="tags.yml"),
        tags(SYMLINK),
    ),
    "C22": (
        cell(
            ("meta_mut.txt", maf_meta("sub/" + "b" * 200 + "/../../data_mut.txt")),
            ("sub/" + "b" * 200, {}),
            ("data_mut.txt", MAF),
        ),
        data("meta_mut.txt", P0),
    ),
    "C23": (cell(("tags.yml", YAML), study="./tags.yml"), tags(P0)),
    "C24": (
        cell(
            ("meta_cna.txt", cna_meta("data_cna.txt", "../../shared/data_mut.txt")),
            ("data_cna.txt", CNA),
            ("case_lists/cases_cna.txt", CNA_LIST),
        ),
        data("meta_cna.txt", P0, "pd_annotations_filename"),
    ),
    # Mode 000 (the reference ran them under setpriv without DAC capabilities): a file the
    # reference cannot open for its permissions alone is not refused, as aibi never opens it.
    "C30": (
        cell(("tags.yml", (MODE, YAML, 0)), study="tags.yml"),
        Accept({"tags.yml": TAGS_NOTE}),
    ),
    "C31": (
        cell(("meta_mut.txt", maf_meta("data_mut.txt")), ("data_mut.txt", (MODE, MAF, 0))),
        Accept({"data_mut.txt": DATA_NOTE}),
    ),
    # A hidden symlink and a backup-named one, excluded from Meta(S) but not from the listing:
    # the reference reads the sample file through the first and cannot open the second.
    "C40": (
        cell(
            ("meta_mut.txt", maf_meta(".lnk")),
            (".lnk", (SYM, "data_clinical_sample.txt")),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "C41": (
        cell(
            ("meta_mut.txt", maf_meta("LNK~")),
            ("lnk~", (SYM, "data_clinical_sample.txt")),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    # The third code review's cells: a value of each branch whose reference outcome depends on the
    # content of what it names (D00, a control, and D08, which is cell 09, are not repeated).
    "D01": (
        cell(
            ("meta_mut.txt", maf_meta("data_mut.txt")),
            ("data_mut.txt", (SYM, "real.txt")),
            ("real.txt", BAD_MAF),
        ),
        data("meta_mut.txt", SYMLINK),
    ),
    "D02": (
        cell(("meta_mut.txt", maf_meta("sub/nofile.txt")), ("sub", {})),
        data("meta_mut.txt", P0),
    ),
    "D03": (
        cell(("meta_mut.txt", maf_meta("sub/data_mut.txt")), ("sub/data_mut.txt", BAD_MAF)),
        data("meta_mut.txt", P0),
    ),
    "D04": (
        cell(("meta_mut.txt", maf_meta("./data_clinical_sample.txt"))),
        data("meta_mut.txt", P0),
    ),
    "D05": (cell(("meta_mut.txt", maf_meta("/nonexistent/x.txt"))), data("meta_mut.txt", P0)),
    "D06": (cell(("sub", {}), study="sub/nofile.yml"), tags(P0)),
    "D07": (
        cell(study=b"<base>citation: a: b\ntags_file: meta_study.txt\n"),
        tags(READS),
    ),
    "D09": (cell(("sub/t.yml", b"a: [1,\n"), study="sub/t.yml"), tags(P0)),
    "D10": (
        cell(("meta_mut.txt", maf_meta("lnk.txt")), ("lnk.txt", (SYM, "meta_study.txt"))),
        data("meta_mut.txt", SYMLINK),
    ),
    "D11": (
        cell(
            ("t.yml", (SYM, "meta_study.txt")),
            study=b"<base>citation: a: b\ntags_file: t.yml\n",
        ),
        tags(SYMLINK),
    ),
    "D12": (
        cell(("meta_mut.txt", maf_meta("../cell_D12/data_clinical_sample.txt"))),
        data("meta_mut.txt", P0),
    ),
    # Modelled, not run: on APFS the reference opens the name's fold-equal entry.
    "23-apfs": (
        cell(("meta_mut.txt", maf_meta("DATA_CLINICAL_SAMPLE.txt"))),
        data("meta_mut.txt", READS),
    ),
    "50-apfs": (cell(("tags.yml", YAML), study="TAGS.yml"), tags(NONE)),
}
"""Each cell of the live differential by its id, with aibi's expected outcome. The recorded cells:
02-27, 30m-33p (four types, each naming the study meta, a clinical meta or the patient file), 40,
50-97 (70, 72 and 89 among them), the code reviews' N01, N05, N22, N25 and N13, their C cells that
exist (C01-C14, C16, C18, C20-C24, C30, C31, C40 and C41: 25, C30 and C31 of mode 000, run only
where a file of that mode cannot be read) and their D cells that exist (D01-D07 and D09-D12: 11,
D08 being cell 09); and two modelled rows."""

RECORDED = [
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "20",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "30m",
    "30s",
    "30p",
    "31m",
    "31s",
    "31p",
    "32m",
    "32s",
    "32p",
    "33m",
    "33s",
    "33p",
    "40",
    "50",
    "51",
    "52",
    "53",
    "54",
    "55",
    "56",
    "57",
    "58",
    "59",
    "60",
    "61",
    "62",
    "63",
    "64",
    "65",
    "66",
    "67",
    "68",
    "69",
    "70",
    "71",
    "72",
    "73",
    "74",
    "75",
    "76",
    "77",
    "78",
    "79",
    "80",
    "81",
    "82",
    "83",
    "84",
    "85",
    "86",
    "87",
    "88",
    "89",
    "90",
    "91",
    "92",
    "93",
    "94",
    "95",
    "96",
    "97",
    "N01",
    "N05",
    "N22",
    "N25",
    "N13",
    "C01",
    "C02",
    "C03",
    "C04",
    "C05",
    "C06",
    "C07",
    "C08",
    "C09",
    "C10",
    "C11",
    "C12",
    "C13",
    "C14",
    "C16",
    "C18",
    "C20",
    "C21",
    "C22",
    "C23",
    "C24",
    "C30",
    "C31",
    "C40",
    "C41",
    "D01",
    "D02",
    "D03",
    "D04",
    "D05",
    "D06",
    "D07",
    "D09",
    "D10",
    "D11",
    "D12",
]
MODELLED = ("23-apfs", "50-apfs")

# The reference's outcome, per cell, as the live runs recorded it (cbioportal-core 47890bb, Linux,
# an audit hook on ``open``): its exit status, each error the cell causes, by its identity (no
# message of the reference is quoted), the members of Reads(S) it opens through the value (``via``)
# and the cell's D409 item. The outcome is data; what is asserted is the cell's item against it
# (``test_every_cell_names_its_d409_item``) and its branch against aibi's own refusal
# (``test_a_refused_cell_names_the_item_of_its_branch``). The kind of a refused cell is D409's one
# definition (``reports_no_error``): ``stricter`` when no error is left but the cascade's.
#   open-fail    the value's open fails, opening nothing of Reads(S)
#   tags-open    its open of the tags value fails (the run crashes)
#   tags-content the tags file is opened, and its content is an error or a crash
#   content      the file the value names is opened, and its content is an error in the role
#   blocked      the run blocks on the FIFO the value names (the harness's timeout)
#   cna          the `_cna` case list a DISCRETE CNA profile needs is missing: the cascade, which
#                the cell causes and the value does not
#   case-list    a case list's content
#   type-meta    a type-specific check on a meta file
# ``via`` is not an error, and not part of the kind: the entries of Reads(S) the reference opens
# more often than its own reading of the study does (the hook counts the opens of each file: a data
# file's three reads, a meta file's one), through the cell's value or, in 19, a case list's symlink.
# The modelled rows hold what APFS would give, never run.

CASCADE = "cna"


class Recorded(NamedTuple):
    """One cell's record: the reference's exit status, its errors by identity, the ``item`` of
    D409 the cell is an instance of, and ``via``: a refused cell's item is the number of a
    ``stricter:`` item (the reference reports no error for the value) or a ``same outcome:`` item
    (it reports an error, crashes or hangs); an accepted cell's is the number of the exit its
    errors fall under, or ``same`` when it has none (D409's list of what is the same as
    cBioPortal)."""

    rc: str
    errors: tuple[str, ...]
    item: str
    via: tuple[str, ...] = ()


def reports_no_error(record: Recorded) -> bool:
    """D409's one definition of ``stricter:``: the reference reports no error for the cell's value,
    that is, its errors, less the cascade's, are none."""
    return all(error == CASCADE for error in record.errors)


REFERENCE: dict[str, Recorded] = {
    "02": Recorded("1", ("content",), "31", via=("data_clinical_sample.txt",)),
    "03": Recorded("1", ("content",), "29", via=("data_clinical_sample.txt",)),
    "04": Recorded("1", ("content",), "29", via=("data_clinical_sample.txt",)),
    "05": Recorded("3", (), "28"),
    "06": Recorded("3", (), "28"),
    "07": Recorded("3", (), "28"),
    "08": Recorded("1", ("content",), "33", via=("data_clinical_sample.txt",)),
    "09": Recorded("3", (), "32", via=("meta_study.txt",)),
    "10": Recorded("3", (), "32", via=("meta_study.txt",)),
    "11": Recorded("3", (), "32", via=("meta_study.txt",)),
    "12": Recorded("3", (), "32", via=("meta_study.txt",)),
    "13": Recorded("3", (), "32", via=("meta_study.txt",)),
    "14": Recorded("1", ("content",), "33", via=("data_clinical_sample.txt",)),
    "15": Recorded("3", (), "28"),
    "16": Recorded("3", (), "32", via=("meta_clinical_sample.txt",)),
    "17": Recorded("1", ("tags-content",), "33", via=("data_clinical_sample.txt",)),
    "18": Recorded("3", (), "same"),
    "19": Recorded("1", ("case-list",), "19", via=("data_clinical_sample.txt",)),
    "20": Recorded("1", ("cna", "content"), "29", via=("data_clinical_sample.txt",)),
    "21": Recorded("1", ("cna", "content"), "33", via=("data_clinical_sample.txt",)),
    "22": Recorded("1", ("cna",), "24"),
    "23": Recorded("1", ("open-fail",), "33"),
    "24": Recorded("3", (), "30"),
    "25": Recorded("1", ("type-meta",), "25"),
    "26": Recorded("3", (), "32", via=("meta_mut.txt",)),
    "27": Recorded("3", (), "same"),
    "30m": Recorded("1", ("cna",), "32", via=("meta_study.txt",)),
    "30s": Recorded("1", ("cna",), "32", via=("meta_clinical_sample.txt",)),
    "30p": Recorded("1", ("cna", "content"), "33", via=("data_clinical_patient.txt",)),
    "31m": Recorded("3", (), "32", via=("meta_study.txt",)),
    "31s": Recorded("3", (), "32", via=("meta_clinical_sample.txt",)),
    "31p": Recorded("1", ("content",), "33", via=("data_clinical_patient.txt",)),
    "32m": Recorded("3", (), "32", via=("meta_study.txt",)),
    "32s": Recorded("3", (), "32", via=("meta_clinical_sample.txt",)),
    "32p": Recorded("1", ("content",), "33", via=("data_clinical_patient.txt",)),
    "33m": Recorded("3", (), "32", via=("meta_study.txt",)),
    "33s": Recorded("3", (), "32", via=("meta_clinical_sample.txt",)),
    "33p": Recorded("1", ("content",), "33", via=("data_clinical_patient.txt",)),
    "40": Recorded("3", (), "32", via=("meta_study.txt",)),
    "50": Recorded("1, crash", ("tags-open",), "35"),
    "51": Recorded("1, crash", ("tags-open",), "35"),
    "52": Recorded("1, crash", ("tags-open",), "35"),
    "53": Recorded("1, crash", ("tags-open",), "35"),
    "54": Recorded("1, crash", ("tags-open",), "27"),
    "55": Recorded("3", (), "32"),
    "56": Recorded("3", (), "28"),
    "57": Recorded("3", (), "same"),
    "58": Recorded("1", ("open-fail",), "20"),
    "59": Recorded("1", ("cna", "open-fail"), "20"),
    "60": Recorded("1", ("cna", "open-fail"), "27"),
    "61": Recorded("1", ("open-fail",), "27"),
    "62": Recorded("1", ("open-fail",), "20"),
    "63": Recorded("1", ("open-fail",), "20"),
    "64": Recorded("1", ("tags-content",), "21"),
    "65": Recorded("3", (), "32"),
    "66": Recorded("1", ("cna", "content"), "20"),
    "67": Recorded("3", (), "same"),
    "68": Recorded("3", (), "same"),
    "69": Recorded("1, crash", ("tags-open",), "35"),
    "70": Recorded("3", (), "30"),
    "71": Recorded("3", (), "same"),
    "72": Recorded("3", (), "30"),
    "73": Recorded("1, crash", ("tags-content",), "21"),
    "74": Recorded("1", ("tags-content",), "21"),
    "75": Recorded("1", ("tags-content",), "21"),
    "76": Recorded("3", (), "same"),
    "77": Recorded("3", (), "same"),
    "78": Recorded("1", ("tags-content",), "21"),
    "79": Recorded("3", (), "same"),
    "80": Recorded("1", ("cna",), "24"),
    "81": Recorded("1", ("cna", "open-fail"), "20"),
    "82": Recorded("1", ("cna",), "30"),
    "83": Recorded("3", (), "30"),
    "84": Recorded("3", (), "28"),
    "85": Recorded("1", ("open-fail",), "29"),
    "86": Recorded("3", (), "same"),
    "87": Recorded("1", ("content",), "33", via=("data_mut.txt",)),
    "88": Recorded("3", (), "32"),
    "89": Recorded("3", (), "same"),
    "90": Recorded("3", (), "32"),
    "91": Recorded("1, crash", ("tags-open",), "33"),
    "92": Recorded("3", (), "same"),
    "93": Recorded("1", ("cna", "open-fail"), "20"),
    "94": Recorded("timeout", ("blocked",), "20"),
    "95": Recorded("1, crash", ("tags-open",), "35"),
    "96": Recorded("3", (), "same"),
    "97": Recorded("1, crash", ("tags-content",), "21"),
    "N01": Recorded("3", (), "32", via=("data_clinical_patient.txt",)),
    "N05": Recorded("timeout", ("blocked",), "35"),
    "N22": Recorded("1, crash", ("tags-open",), "31"),
    "N25": Recorded("3", (), "32", via=("meta_cna.txt",)),
    "N13": Recorded("3", (), "same"),
    "C01": Recorded("1, crash", ("open-fail",), "27"),
    "C02": Recorded("1", ("open-fail",), "27"),
    "C03": Recorded("1", ("open-fail",), "27"),
    "C04": Recorded("1", ("open-fail",), "27"),
    "C05": Recorded("1, crash", ("tags-open",), "27"),
    "C06": Recorded("1, crash", ("tags-open",), "27"),
    "C07": Recorded("1, crash", ("tags-open",), "27"),
    "C08": Recorded("1", ("open-fail",), "31"),
    "C09": Recorded("1", ("open-fail",), "31"),
    "C10": Recorded("3", (), "30"),
    "C11": Recorded("1, crash", ("tags-content",), "31"),
    "C12": Recorded("1, crash", ("tags-open",), "31"),
    "C13": Recorded("3", (), "28"),
    "C14": Recorded("3", (), "28"),
    "C16": Recorded("1", ("open-fail",), "31"),
    "C18": Recorded("1, crash", ("tags-open",), "29"),
    "C20": Recorded("timeout", ("blocked",), "31"),
    "C21": Recorded("1", ("tags-content",), "31"),
    "C22": Recorded("3", (), "28"),
    "C23": Recorded("3", (), "28"),
    "C24": Recorded("1", ("open-fail",), "29"),
    "C30": Recorded("1, crash", ("tags-open",), "21"),
    "C31": Recorded("1", ("open-fail",), "20"),
    "C40": Recorded("1", ("content",), "31", via=("data_clinical_sample.txt",)),
    "C41": Recorded("1", ("open-fail",), "31"),
    "D01": Recorded("1", ("content",), "31"),
    "D02": Recorded("1", ("open-fail",), "29"),
    "D03": Recorded("1", ("content",), "29"),
    "D04": Recorded("1", ("content",), "29", via=("data_clinical_sample.txt",)),
    "D05": Recorded("1", ("open-fail",), "29"),
    "D06": Recorded("1, crash", ("tags-open",), "29"),
    "D07": Recorded("1", ("tags-content",), "33", via=("meta_study.txt",)),
    "D09": Recorded("1", ("tags-content",), "29"),
    "D10": Recorded("3", (), "30", via=("meta_study.txt",)),
    "D11": Recorded("1", ("tags-content",), "31", via=("meta_study.txt",)),
    "D12": Recorded("1", ("content",), "29", via=("data_clinical_sample.txt",)),
    "23-apfs": Recorded("model, not run", ("content",), "33", via=("data_clinical_sample.txt",)),
    "50-apfs": Recorded("model, not run", (), "34"),
}


def test_the_corpus_is_every_recorded_cell_and_the_modelled_rows() -> None:
    assert list(CELLS) == [*RECORDED, *MODELLED]
    assert set(REFERENCE) == set(CELLS)
    assert len(RECORDED) == 128


# --- D409's items, and the cell that is an instance of each --------------------------------------

SPEC = Path(__file__).resolve().parents[4] / "SPEC.md"
HEAD = re.compile(r"\((\d+)\) (stricter|same outcome):")
"""An item of D409 for the path values outside the clinical role: its number and, as a prefix, the
reference's outcome on the value (``stricter`` where it reports no error for the value)."""
ITEM = re.compile(r"\((\d+)\) (stricter|same outcome): \[([^\]]+)\] ")
"""The head: the prefix, then the branch tag: ``P0`` (a value with no ``/``), ``P0 with /``,
``Kind``, ``Reads`` or ``Tags`` (the tags file's own check)."""
LISTED = re.compile(r"\(cells ([^)]*)\)")
"""A list of instances under an item: it names cells, and claims nothing for another value."""


class Items(NamedTuple):
    kinds: dict[str, str]
    """Each item's prefix by its number."""
    branches: dict[str, str]
    """Each item's branch tag."""
    listed: dict[str, tuple[str, ...]]
    """The cells each item's text lists."""
    exits: frozenset[str]
    """The numbers of D409's exit 3 items."""
    same: bool
    """Whether it lists what is the same as cBioPortal."""


def d409() -> Items:
    """From D409's text: each item with a ``stricter:`` or ``same outcome:`` prefix by its number,
    with its branch tag and the cells it lists, the numbers of its exit 3 items, and whether it
    lists what is the same as cBioPortal."""
    (row,) = [line for line in SPEC.read_text().splitlines() if line.startswith("| D409 |")]
    heads = HEAD.findall(row)
    found = list(ITEM.finditer(row))
    assert [(m[1], m[2]) for m in found] == heads  # every head carries its branch tag
    assert row.count("Exit 3: ") == 1
    ends = [m.start() for m in found[1:]] + [row.index("Exit 1: ")]
    exits = row.partition("Exit 3: ")[2].partition("The same as cBioPortal:")[0]
    return Items(
        {m[1]: m[2] for m in found},
        {m[1]: m[3] for m in found},
        {
            m[1]: tuple(
                cell_id
                for text in LISTED.findall(row[m.end() : end])
                for cell_id in text.split(", ")
            )
            for m, end in zip(found, ends, strict=True)
        },
        frozenset(re.findall(r"(?:^|; )\((\d+)\) ", exits)),
        "The same as cBioPortal:" in row,
    )


def test_every_item_a_cell_names_exists_in_d409() -> None:
    items = d409()
    assert items.kinds
    for cell_id, record in REFERENCE.items():
        if record.item == "same":
            assert items.same, cell_id
        else:
            assert record.item in items.kinds or record.item in items.exits, (cell_id, record.item)


def test_every_stricter_or_same_outcome_item_is_named_by_a_cell() -> None:
    named = {record.item for record in REFERENCE.values()}
    assert sorted(set(d409().kinds) - named, key=int) == []


def test_every_cell_names_its_d409_item() -> None:
    """A refused cell's item has the kind of D409's one definition (``reports_no_error``):
    ``stricter:`` where the reference reports no error for the value (the cascade's, the missing
    ``_cna`` case list, is not the value's), ``same outcome:`` where it errs, crashes or hangs.
    An accepted cell names the exit its reference errors fall under, or ``same`` if it has none.
    The exit an accepted cell's errors map to, error by error, is PR B's."""
    items = d409()
    for cell_id, (_, expected) in CELLS.items():
        record = REFERENCE[cell_id]
        if not record.rc.startswith("model"):
            assert (record.errors == ()) == (record.rc == "3"), cell_id
        if isinstance(expected, Refuse):
            assert record.item in items.kinds, cell_id
            kind = "stricter" if reports_no_error(record) else "same outcome"
            assert items.kinds[record.item] == kind, cell_id
        else:
            assert (
                (record.item == "same") if record.errors == () else (record.item in items.exits)
            ), cell_id
            # D436's closure: no value an accepted cell holds makes the reference open a member
            # of Reads(S) more often than its own reading does, but through a case list's symlink
            # (19).
            assert record.via == () or record.item == "19", cell_id


def test_the_cells_an_item_lists_are_the_cells_that_name_it() -> None:
    """Each item lists its instances (``among them (cells ...)``), and the list is complete: it
    names exactly the cells of the corpus that name the item, each once."""
    items = d409()
    assert set(items.listed) == set(items.kinds)
    for item, listed in items.listed.items():
        assert listed, item
        assert len(set(listed)) == len(listed), item
        naming = {cell_id for cell_id, record in REFERENCE.items() if record.item == item}
        assert set(listed) == naming, (item, sorted(set(listed) ^ naming))


BRANCH = {P0: "P0", SYMLINK: "Kind", READS: "Reads", NONE: "Tags"}
REFUSAL = re.compile(r"«(.+)» (?:has|holds) a (\w+) that (.+)", re.DOTALL)


def branch_of(reason: str, value: str) -> str:
    """The branch of the rule a refusal says it is by: its reason, and for P0 whether the value
    has a ``/``."""
    tag = BRANCH[reason]
    return "P0 with /" if tag == "P0" and "/" in value else tag


def value_of(meta: bytes, key: str) -> str:
    """The value a meta file gives ``key`` (the last, which the parse keeps), as the parse strips
    it."""
    (*_, last) = re.findall(rb"^" + re.escape(key.encode()) + rb":(.*)$", meta, re.MULTILINE)
    return last.decode().strip()


@pytest.mark.parametrize("cell_id", [i for i, (_, e) in CELLS.items() if isinstance(e, Refuse)])
def test_a_refused_cell_names_the_item_of_its_branch(
    cell_id: str, make_study: Any, discovery: Any, base: Any, kinds: Any, tmp_path: Path
) -> None:
    """The item a refused cell names has the branch aibi's own refusal gives it: the reason it
    states (a P0 refusal, a symlink, a member of Reads(S), the tags file's own check) and, for P0,
    whether the value has a ``/``, read from the meta file the refusal names."""
    tree, directory = _study_of(cell_id, make_study, base, kinds, tmp_path)
    refusal = discovery.refusal(directory)
    found = REFUSAL.fullmatch(said(refusal.message))
    assert found is not None
    meta, key, reason = found.groups()
    value = value_of(tree[meta], key)
    assert d409().branches[REFERENCE[cell_id].item] == branch_of(reason, value), cell_id


def _expanded(tree: Mapping[str, Node], base: Mapping[str, bytes], kinds: Any) -> dict[str, Any]:
    """The tree ``make_study`` builds: the cell's nodes as the fixture's kinds."""
    built: dict[str, Any] = {}
    for name, node in tree.items():
        if isinstance(node, bytes) and node.startswith(b"<base>"):
            built[name] = base["meta_study.txt"] + node.removeprefix(b"<base>")
        elif isinstance(node, tuple) and node[0] == SYM:
            built[name] = kinds.symlink(node[1])
        elif isinstance(node, tuple) and node[0] == MODE:
            built[name] = kinds.mode(node[1], node[2])
        elif node == FIFO:
            built[name] = kinds.fifo()
        elif isinstance(node, Mapping):
            built[name] = _expanded(node, base, kinds)
        else:
            built[name] = node
    return built


def said(segments: Sequence[Any]) -> str:
    return "".join(s.text if isinstance(s, TextSegment) else f"«{s.data}»" for s in segments)


def notes_by_entry(found: Any) -> tuple[dict[str, str], list[str]]:
    """A study's skip notes as entry name to reason, and its other notes as text."""
    by_entry: dict[str, str] = {}
    other: list[str] = []
    for note in found.notes:
        message = note.message
        if note.kind == "skipped_source" and isinstance(message[-1], DataSegment):
            (reason,) = [s.text for s in message[:-1]]
            by_entry[message[-1].data] = reason.removesuffix(": ")
        else:
            other.append(f"{note.kind}: {said(message)}")
    return by_entry, other


def _study_of(
    cell_id: str, make_study: Any, base: Any, kinds: Any, tmp_path: Path
) -> tuple[dict[str, Node], Path]:
    """A cell's tree and its study directory."""
    build, _ = CELLS[cell_id]
    if cell_id in ("N05", "C20") and not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs here")  # the FIFO is made, never opened
    shared = tmp_path / "imports" / "shared"
    (shared / "sub").mkdir(parents=True, exist_ok=True)
    (shared / "data_mut.txt").write_bytes(MAF)
    tree = _expanded(build(base, shared), base, kinds)
    return tree, make_study(tree, name=f"cell_{cell_id}")


@pytest.mark.parametrize("cell_id", list(CELLS))
def test_a_corpus_cell(
    cell_id: str, make_study: Any, discovery: Any, base: Any, kinds: Any, tmp_path: Path
) -> None:
    """The cell's study, through ``discover``: refused as expected (the code, the meta file named
    and the reason), or imported with the notes expected."""
    _, expected = CELLS[cell_id]
    if cell_id in ("C30", "C31"):
        probe = tmp_path / "probe"
        probe.write_bytes(b"x")
        probe.chmod(0)
        try:
            probe.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("this user reads a file of mode 000")
        finally:
            probe.chmod(0o644)
    tree, directory = _study_of(cell_id, make_study, base, kinds, tmp_path)
    if isinstance(expected, Refuse):
        refusal = discovery.refusal(directory)
        verb = "holds" if expected.key == "tags_file" else "has"
        assert (str(refusal.code), said(refusal.message)) == (
            expected.code,
            f"«{expected.meta}» {verb} a {expected.key} that {expected.reason}",
        )
        return
    found = discovery.run(directory)
    by_entry, other = notes_by_entry(found)
    for name, reason in expected.notes.items():
        assert by_entry.get(name) == reason, name
    for name, reason in by_entry.items():
        if reason in (DATA_NOTE, TAGS_NOTE):
            assert expected.notes.get(name) == reason, name
    has_tags = b"tags_file" in tree["meta_study.txt"]
    assert other == ([NOT_PROPOSED] if has_tags else [])


# --- The brute force -----------------------------------------------------------------------------

ROOT = Path("/brute")


class ListedReader:
    """A reader over a listing given as data (name to kind and content), in the order given:
    ``discover`` sorts it. It records each read."""

    def __init__(self, entries: Mapping[str, tuple[str, bytes]]) -> None:
        self.entries = dict(entries)
        self.reads: list[str] = []

    def files(self, directory: ConfinedPath) -> list[DirectoryEntry]:
        assert directory == ROOT
        listed = []
        for name, (kind, _) in reversed(self.entries.items()):
            path = ConfinedPath(ROOT / name) if kind == "file" else None
            listed.append(DirectoryEntry(name, kind, path))  # pyright: ignore[reportArgumentType]
        return listed

    def read(self, path: ConfinedPath, limit: int) -> bytes:
        self.reads.append(path.name)
        content = self.entries[path.name][1]
        assert len(content) <= limit
        return content

    def location(self, path: ConfinedPath) -> str:
        return path.name


def _discover(study: ModuleType, entries: Mapping[str, tuple[str, bytes]]) -> tuple[Any, Any]:
    """``discover`` over a listing: ``(study, None)`` or ``(None, the one refusal)``."""
    reader = ListedReader(entries)
    try:
        found = study.discover(reader, ConfinedPath(ROOT), ImportLimits())
    except Refused as error:
        (refusal,) = error.refusals
        return None, refusal
    return found, None


@dataclass(frozen=True)
class Role:
    """Where a value is written: the meta file ``meta`` (its content made by ``content``) or the
    study meta (``meta_study.txt``), under ``key``. ``checked`` is false for a site the rule
    ignores. ``extra`` are entries the meta needs."""

    name: str
    meta: str
    key: str
    checked: bool
    content: Callable[[Mapping[str, bytes], str], bytes]
    extra: Mapping[str, bytes] = field(default_factory=dict[str, bytes])


ROLES = (
    Role(
        "MAF data_filename", "meta_mutations.txt", "data_filename", True, lambda b, v: maf_meta(v)
    ),
    Role(
        "timeline data_filename",
        "meta_timeline.txt",
        "data_filename",
        True,
        lambda b, v: timeline_meta(v),
    ),
    Role(
        "CNA_DISCRETE pd_annotations_filename",
        "meta_cna.txt",
        "pd_annotations_filename",
        True,
        lambda b, v: cna_meta("data_cna.txt", v),
        {"data_cna.txt": CNA},
    ),
    Role("study tags_file", "meta_study.txt", "tags_file", True, study_with_tags),
    Role(
        "study data_filename",
        "meta_study.txt",
        "data_filename",
        False,
        lambda b, v: b["meta_study.txt"] + f"data_filename: {v}\n".encode(),
    ),
    Role(
        "MAF tags_file",
        "meta_mutations.txt",
        "tags_file",
        False,
        lambda b, v: maf_meta("nofile.txt") + f"tags_file: {v}\n".encode(),
    ),
    Role(
        "sample tags_file",
        "meta_clinical_sample.txt",
        "tags_file",
        False,
        lambda b, v: b["meta_clinical_sample.txt"] + f"tags_file: {v}\n".encode(),
    ),
    Role(
        "CNA_DISCRETE_LONG pd_annotations_filename",
        "meta_cnal.txt",
        "pd_annotations_filename",
        False,
        lambda b, v: cna_meta("data_cnal.txt", v, "DISCRETE_LONG"),
        {"data_cnal.txt": CNA},
    ),
    Role(
        "MAF pd_annotations_filename",
        "meta_mutations.txt",
        "pd_annotations_filename",
        False,
        lambda b, v: maf_meta("nofile.txt") + f"pd_annotations_filename: {v}\n".encode(),
    ),
)

E_NFC = unicodedata.normalize("NFC", "é")
E_NFD = unicodedata.normalize("NFD", "é")
FOLDS: Mapping[str, tuple[str, str]] = {
    "exact": ("x", "x"),
    "case": ("x", "X"),
    "NFD of NFC": (E_NFC, E_NFD),
    "NFC of NFD": (E_NFD, E_NFC),
    "sharp s for ss": ("ss", "\u00df"),
    "long s for s": ("s", "\u017f"),
    "capital sharp s for ss": ("ss", "\u1e9e"),
    "Kelvin sign for k": ("k", "\u212a"),
    "ff ligature for ff": ("ff", "\ufb00"),
    "NFD and case": (E_NFC, unicodedata.normalize("NFD", "É")),
    "U+0345 reordered": ("a\u0301\u0345", "a\u0345\u0301"),
    "iota subscript across a mark": ("a\u1f80\u0302", "a\u1f00\u03b9\u0302"),
    "fullwidth d for d, a compatibility match only": ("d", "\uff44"),
}
"""Each way a value's stem is written against its entry's stem: the entry ``<prefix><stem>.txt``
and the value the same with the value's stem. The last row must not match: D145 is canonical
caseless matching, not compatibility matching (NFKD), so the fullwidth letter is another name."""


def _padded(value: str, size: int) -> str:
    """The value behind a run of two-byte characters (and one ASCII one for an odd size): ``size``
    bytes in UTF-8, though fewer characters."""
    pad = size - len(value.encode())
    return "\u00e9" * (pad // 2) + "a" * (pad % 2) + value


SHAPES: Mapping[str, Callable[[str], str]] = {
    "as is": lambda value: value,
    "trailing dot": lambda value: value + ".",
    "trailing space": lambda value: value + " ",
    "./": lambda value: "./" + value,
    "x/../": lambda value: "x/../" + value,
    "absolute": lambda value: "/brute/" + value,
    "trailing /": lambda value: value + "/",
    "NUL": lambda value: value + "\0",
    "over 255 bytes": lambda value: "a" * (256 - len(value.encode())) + value,
    "exactly 255 bytes": lambda value: "a" * (255 - len(value.encode())) + value,
    "multi-byte, exactly 255 bytes": lambda value: _padded(value, 255),
    "multi-byte, 256 bytes": lambda value: _padded(value, 256),
}
"""What is put around the spelled name (``trailing space`` is stripped by the parse, so it is
exact)."""
PLAIN_VALUES = ("", ".", "..")

TARGETS = ("regular", "clinical", "meta", "symlink", "directory", "other", "unconfined", "absent")
"""What the entry the value spells is: a regular file outside Reads(S), the patient data file, a
member of Meta(S) (the patient meta, renamed), another kind, or nothing at all."""
BESIDE = ("none", "regular", "clinical", "symlink", "directory")
"""What sits beside it under a fold-equal name (the sample data file, for ``clinical``)."""
KIND = {
    "symlink": "symlink",
    "directory": "directory",
    "other": "other",
    "unconfined": "unconfined",
}


@dataclass(frozen=True)
class Case:
    role: Role
    value: str
    entries: Mapping[str, tuple[str, bytes]]
    reads: frozenset[str]
    """The members of Reads(S): the clinical data files and the meta files."""
    meta_files: frozenset[str]
    used: frozenset[str]
    """What is imported: the study meta, the clinical metas and their data files."""


def build_case(
    base: Mapping[str, bytes],
    role: Role,
    value: str,
    target: str,
    target_name: str,
    beside: str,
    beside_name: str,
) -> Case:
    entries: dict[str, tuple[str, bytes]] = {
        name: ("file", content) for name, content in base.items()
    }
    sample_meta = base["meta_clinical_sample.txt"]
    patient_meta = base["meta_clinical_patient.txt"]
    patient_meta_name = "meta_clinical_patient.txt"
    sample_data, patient_data = "data_clinical_sample.txt", "data_clinical_patient.txt"
    if target == "regular":
        entries[target_name] = ("file", b"x")
    elif target == "clinical":
        del entries[patient_data]
        patient_data = target_name
        entries[patient_data] = ("file", base["data_clinical_patient.txt"])
    elif target == "meta":
        del entries[patient_meta_name]
        patient_meta_name = target_name
    elif target in KIND:
        entries[target_name] = (KIND[target], b"")
    if beside == "regular":
        entries[beside_name] = ("file", b"x")
    elif beside == "clinical":
        del entries[sample_data]
        sample_data = beside_name
        entries[sample_data] = ("file", base["data_clinical_sample.txt"])
    elif beside in KIND:
        entries[beside_name] = (KIND[beside], b"")
    sample_meta = sample_meta.replace(b"data_clinical_sample.txt", sample_data.encode())
    patient_meta = patient_meta.replace(b"data_clinical_patient.txt", patient_data.encode())
    entries["meta_clinical_sample.txt"] = ("file", sample_meta)
    entries[patient_meta_name] = ("file", patient_meta)
    current = {name: content for name, (_, content) in entries.items()}
    entries[role.meta] = ("file", role.content(current, value))
    for name, content in role.extra.items():
        entries[name] = ("file", content)
    meta_files = frozenset(
        {"meta_study.txt", "meta_clinical_sample.txt", patient_meta_name, role.meta}
    )
    data_files = {sample_data, patient_data}
    used = frozenset({"meta_study.txt", "meta_clinical_sample.txt", patient_meta_name, *data_files})
    return Case(role, value, entries, frozenset({*meta_files, *data_files}), meta_files, used)


def expected(case: Case) -> tuple[str, ...]:
    """The oracle: ``("refused", code, meta, reason)``, or ``("ok", entry, note)`` for each
    entry that is not imported, in name order."""
    role = case.role
    value = case.value.strip()  # the parse strips a value
    if role.checked:
        code = "onco.META_VALUE" if role.key == "tags_file" else "onco.DATA_FILE"
        verb = "holds" if role.key == "tags_file" else "has"
        reason = None
        alike = [name for name in case.entries if fold(name) == fold(value)]
        if p0(value):
            reason = P0
        elif any(case.entries[name][0] == "symlink" for name in alike):
            reason = SYMLINK
        elif any(name in case.reads for name in alike):
            reason = READS
        elif role.key == "tags_file" and case.entries.get(value, ("",))[0] != "file":
            reason = NONE
        if reason is not None:
            return ("refused", code, f"«{role.meta}» {verb} a {role.key} that {reason}")
    named = (
        value
        if role.checked and not p0(value) and case.entries.get(value, ("",))[0] == "file"
        else None
    )
    notes: list[str] = []
    for name in sorted(case.entries):
        kind = case.entries[name][0]
        if name in case.used:
            continue
        if name in case.meta_files:
            reason = "a meta file of a type M4.2a does not import"
        elif (why := kept_out(name)) is not None:
            reason = why
        elif name in role.extra:  # the meta's own data_filename names it
            reason = DATA_NOTE
        elif name == named:
            reason = TAGS_NOTE if role.key == "tags_file" else DATA_NOTE
        elif kind == "file":
            reason = PLAIN
        elif kind == "symlink":
            reason = "a symlink"
        elif kind == "directory":
            reason = "a subdirectory"
        else:
            reason = "not a regular file"
        notes.append(f"{name if _shown(name) else UNSHOWN}: {reason}")
    if role.meta == "meta_study.txt" and role.key == "tags_file":
        notes.append(NOT_PROPOSED)
    return ("ok", *notes)


def actual(study: ModuleType, case: Case) -> tuple[str, ...]:
    found, refusal = _discover(study, case.entries)
    if refusal is not None:
        return ("refused", str(refusal.code), said(refusal.message))
    notes = []
    for note in found.notes:
        if note.kind == "skipped_source":
            last = note.message[-1]
            name = last.data if isinstance(last, DataSegment) else last.text
            notes.append(f"{name}: {note.message[0].text.removesuffix(': ')}")
        else:
            notes.append(f"{note.kind}: {said(note.message)}")
    return ("ok", *notes)


EXCLUDED: Mapping[str, Callable[[str], str]] = {
    "plain": lambda name: name,
    "hidden": lambda name: "." + name,
    "tilde": lambda name: name + "~",
    "backup": lambda name: "ONCOKB_IMPORT_BACKUP_" + name,
}
"""How an entry's name is made an excluded one (a dot-name, a ``~`` name, a name that starts with
``ONCOKB_IMPORT_BACKUP``): never read as a meta file, but in the listing all the same, so that a
value is matched against it and a skip note names it."""
EXCLUDED_FOLDS = ("exact", "case", "NFD of NFC")
EXCLUDED_SHAPES = ("as is", "trailing space", "./")
LONG_NAMES: Mapping[str, str] = {
    "255 ASCII bytes": "a" * 247 + "long.txt",
    "255 bytes, multi-byte": _padded("long.txt", 255),
    "256 ASCII bytes": "a" * 248 + "long.txt",
    "256 bytes, multi-byte": _padded("long.txt", 256),
}
"""Entries named at the bound and past it, each written exactly as the value."""


def excluded_cases(base: Mapping[str, bytes], role: Role) -> Iterator[tuple[str, Case]]:
    """The target's name and the fold-equal neighbour's, each plain or excluded, but not both
    plain (those are the brute force's other cases): a value matched against excluded entries
    is refused and noted as the oracle says, the excluded note winning over the data file's."""
    for fold_name in EXCLUDED_FOLDS:
        entry_stem, value_stem = FOLDS[fold_name]
        for shape_name in EXCLUDED_SHAPES:
            for target in TARGETS:
                if target == "meta":
                    continue
                besides = BESIDE if role.checked else ("none",)
                for beside in besides:
                    for target_how, make_target in EXCLUDED.items():
                        for beside_how, make_beside in EXCLUDED.items():
                            plain = target_how == beside_how == "plain"
                            if plain or (beside == "none" and beside_how != "plain"):
                                continue
                            target_name = make_target(f"data_{entry_stem}.txt")
                            value = SHAPES[shape_name](make_target(f"data_{value_stem}.txt"))
                            beside_name = make_beside(f"DATA_{entry_stem}.TXT")
                            label = (
                                f"{role.name} | {fold_name} | {shape_name} | {target} "
                                f"({target_how}) | {beside} ({beside_how})"
                            )
                            yield (
                                label,
                                build_case(
                                    base, role, value, target, target_name, beside, beside_name
                                ),
                            )


def long_cases(base: Mapping[str, bytes], role: Role) -> Iterator[tuple[str, Case]]:
    """An entry named exactly by a value of 255 bytes is accepted and noted when it is a regular
    file outside Reads(S), whatever its kind otherwise; past 255 P0 refuses the value."""
    for long_name, name in LONG_NAMES.items():
        for target in TARGETS:
            # a clinical data file is itself held to 255 bytes by the clinical role
            if target != "meta" and (target != "clinical" or len(name.encode()) <= 255):
                label = f"{role.name} | {long_name} | {target}"
                yield label, build_case(base, role, name, target, name, "none", "")


SPACINGS: Mapping[str, Callable[[str], str]] = {
    "leading space": lambda name: " " + name,
    "trailing space": lambda name: name + " ",
    "leading and trailing space": lambda name: " " + name + " ",
    "leading no-break space": lambda name: "\u00a0" + name,
    "trailing no-break space": lambda name: name + "\u00a0",
    "trailing tab": lambda name: name + "\t",
}
"""White space put around an entry's name: the parse strips a value, never an entry's name, and
``fold`` strips neither, so such an entry is another name than the value spells."""
SPACED_TARGETS = ("absent", "regular", "symlink", "directory")
SPACED_BESIDE = ("none", "regular", "symlink", "directory")


def spaced_cases(base: Mapping[str, bytes], role: Role) -> Iterator[tuple[str, Case]]:
    """The value ``data_x.txt`` against an entry whose name is spaced, as the neighbour of the
    target the value spells and as the target itself (the oracle: no match, whatever its kind)."""
    plain = "data_x.txt"
    for spacing_name, spaced in SPACINGS.items():
        for target in SPACED_TARGETS:
            for beside in SPACED_BESIDE:
                label = f"{role.name} | {spacing_name} | {target} | {beside}"
                yield (
                    f"{label} | spaced neighbour",
                    build_case(base, role, plain, target, plain, beside, spaced(plain)),
                )
                yield (
                    f"{label} | spaced target",
                    build_case(base, role, plain, target, spaced(plain), beside, "DATA_X.TXT"),
                )


def brute_cases(base: Mapping[str, bytes]) -> Iterator[tuple[str, Case]]:
    for role in ROLES:
        for fold_name, (entry_stem, value_stem) in FOLDS.items():
            for shape_name, shape in SHAPES.items():
                for target in TARGETS:
                    prefix = "meta_" if target == "meta" else "data_"
                    target_name = f"{prefix}{entry_stem}.txt"
                    value = shape(f"{prefix}{value_stem}.txt")
                    besides = BESIDE if role.checked and prefix == "data_" else ("none",)
                    for beside in besides:
                        beside_name = f"DATA_{entry_stem}.TXT"
                        label = f"{role.name} | {fold_name} | {shape_name} | {target} | {beside}"
                        yield (
                            label,
                            build_case(base, role, value, target, target_name, beside, beside_name),
                        )
        for value in PLAIN_VALUES:
            yield (
                f"{role.name} | {value!r}",
                build_case(base, role, value, "absent", "", "none", ""),
            )
        yield from excluded_cases(base, role)
        yield from long_cases(base, role)
        yield from spaced_cases(base, role)


def test_the_brute_force(base: Any) -> None:
    """Every spelling of every target, of every kind, beside every fold-equal neighbour, in every
    role (the ignored sites among them), is what the oracle says: the refusal's code and reason,
    or the notes, each entry's. A case that disagrees is listed."""
    study = importlib.import_module(STUDY_MODULE)
    wrong = []
    count = 0
    for label, case in brute_cases(base):
        count += 1
        want, got = expected(case), actual(study, case)
        if want != got:
            wrong.append((label, want, got))
    assert count > 0
    assert (len(wrong), wrong[:5]) == (0, [])


def test_the_brute_force_is_the_size_it_is_said_to_be(base: Any) -> None:
    """Roles 9 (4 checked, 5 ignored), stems 13 by shapes 12, targets 8 (7 that take a neighbour of
    5 kinds where a role is checked), and 3 plain values per role; then the excluded names (3
    stems by 3 shapes by 7 targets, each with the target's name and a neighbour's plain or
    excluded in 3 ways, 3 + 4 * 15 cases where a role is checked and 3 where it is not) and the
    names at the bound (4 by 7 targets, less the clinical file past 255 bytes) in each role, and
    entries named with white space around them (6 spacings by 4 targets by 4 neighbours, each as
    the neighbour and as the target)."""
    checked = 4 * 13 * 12 * (7 * 5 + 1)
    ignored = 5 * 13 * 12 * 8
    excluded = 3 * 3 * 7 * (4 * (3 + 4 * 15) + 5 * 3)
    long = 9 * (4 * 7 - 2)
    spaced = 9 * 6 * 4 * 4 * 2
    assert sum(1 for _ in brute_cases(base)) == checked + ignored + 9 * 3 + excluded + long + spaced


# --- The table and the sites it names ------------------------------------------------------------

SEED_KEYS = (
    "data_filename",
    "pd_annotations_filename",
    "tags_file",
    "listing:meta",
    "listing:case_lists",
    "isdir:case_lists",
)
"""The keys of ``test_reachability.SEED_KEYS``, spelled again (test modules cannot import one
another)."""
DATA_TYPES = frozenset(
    {
        "CANCER_TYPE",
        "CNA_DISCRETE",
        "CNA_DISCRETE_LONG",
        "CNA_LOG2",
        "CNA_CONTINUOUS",
        "SEG",
        "MUTATION",
        "MUTATION_UNCALLED",
        "EXPRESSION",
        "METHYLATION",
        "PROTEIN",
        "GISTIC_GENES",
        "TIMELINE",
        "MUTATION_SIGNIFICANCE",
        "GENE_PANEL_MATRIX",
        "GSVA_PVALUES",
        "GSVA_SCORES",
        "GENERIC_ASSAY_CONTINUOUS",
        "GENERIC_ASSAY_BINARY",
        "GENERIC_ASSAY_CATEGORICAL",
        "STRUCTURAL_VARIANT",
        "SAMPLE_RESOURCES",
        "PATIENT_RESOURCES",
        "STUDY_RESOURCES",
        "RESOURCES_DEFINITION",
    }
)
"""Every type whose meta file names a data file M4.2a never imports (all but the study meta and
the two clinical types), written by hand."""


def test_path_sites_is_the_survey() -> None:
    """``PATH_SITES``: one row per seed key, in the order of phase 9 then 10 then the listings,
    each row's meta types and role, and its cites by line with a paraphrase."""
    study = importlib.import_module(STUDY_MODULE)
    rows = study.PATH_SITES
    assert tuple(row.key for row in rows) == SEED_KEYS
    assert [(row.role, row.meta_types) for row in rows] == [
        ("data", DATA_TYPES),
        ("data", frozenset({"CNA_DISCRETE"})),
        ("tags", frozenset({"STUDY"})),
        ("listing", frozenset({*DATA_TYPES, "STUDY", "SAMPLE_ATTRIBUTES", "PATIENT_ATTRIBUTES"})),
        ("case lists", frozenset()),
        ("case lists", frozenset()),
    ]
    for row in rows:
        assert row.cites, row.key
        for cite in row.cites:
            sites, colon, paraphrase = cite.partition(": ")
            assert colon, cite
            assert paraphrase, cite
            assert all(
                part.split(":")[0] in ("V", "C") and part.split(":")[1].replace("-", "").isdigit()
                for part in sites.split(", ")
            ), cite


PAIRS = (
    ("CANCER_TYPE", "CANCER_TYPE", "CANCER_TYPE"),
    ("CLINICAL", "TIMELINE", "TIMELINE"),
    ("PROTEIN_LEVEL", "LOG2-VALUE", "PROTEIN"),
    ("PROTEIN_LEVEL", "Z-SCORE", "PROTEIN"),
    ("PROTEIN_LEVEL", "CONTINUOUS", "PROTEIN"),
    ("COPY_NUMBER_ALTERATION", "DISCRETE", "CNA_DISCRETE"),
    ("COPY_NUMBER_ALTERATION", "DISCRETE_LONG", "CNA_DISCRETE_LONG"),
    ("COPY_NUMBER_ALTERATION", "CONTINUOUS", "CNA_CONTINUOUS"),
    ("COPY_NUMBER_ALTERATION", "LOG2-VALUE", "CNA_LOG2"),
    ("COPY_NUMBER_ALTERATION", "SEG", "SEG"),
    ("MRNA_EXPRESSION", "CONTINUOUS", "EXPRESSION"),
    ("MRNA_EXPRESSION", "Z-SCORE", "EXPRESSION"),
    ("MRNA_EXPRESSION", "DISCRETE", "EXPRESSION"),
    ("MUTATION_EXTENDED", "MAF", "MUTATION"),
    ("MUTATION_UNCALLED", "MAF", "MUTATION_UNCALLED"),
    ("METHYLATION", "CONTINUOUS", "METHYLATION"),
    ("GENE_PANEL_MATRIX", "GENE_PANEL_MATRIX", "GENE_PANEL_MATRIX"),
    ("STRUCTURAL_VARIANT", "SV", "STRUCTURAL_VARIANT"),
    ("GISTIC_GENES_AMP", "Q-VALUE", "GISTIC_GENES"),
    ("GISTIC_GENES_DEL", "Q-VALUE", "GISTIC_GENES"),
    ("MUTSIG", "Q-VALUE", "MUTATION_SIGNIFICANCE"),
    ("GENESET_SCORE", "GSVA-SCORE", "GSVA_SCORES"),
    ("GENESET_SCORE", "P-VALUE", "GSVA_PVALUES"),
    ("GENERIC_ASSAY", "LIMIT-VALUE", "GENERIC_ASSAY_CONTINUOUS"),
    ("GENERIC_ASSAY", "BINARY", "GENERIC_ASSAY_BINARY"),
    ("GENERIC_ASSAY", "CATEGORICAL", "GENERIC_ASSAY_CATEGORICAL"),
)
"""Every pair of a non-clinical type (C:651-689), written by hand, with the type it is."""
RESOURCES = {
    "PATIENT": "PATIENT_RESOURCES",
    "SAMPLE": "SAMPLE_RESOURCES",
    "STUDY": "STUDY_RESOURCES",
    "DEFINITION": "RESOURCES_DEFINITION",
}


def every_type_meta(value: str) -> Iterator[tuple[str, bytes]]:
    """A meta file of each pair and each resource type, naming ``value``: each holds every key
    any type needs, so that each passes its mandatory keys."""
    for alteration, datatype, kind in PAIRS:
        yield (
            kind,
            (
                f"cancer_study_identifier: {SID}\ngenetic_alteration_type: {alteration}\n"
                f"datatype: {datatype}\nstable_id: s\nshow_profile_in_analysis_tab: true\n"
                "profile_name: p\nprofile_description: p\nreference_genome_id: hg19\n"
                "description: d\nsource_stable_id: s\ngeneset_def_version: 1\n"
                f"generic_assay_type: g\ndata_filename: {value}\n"
            ).encode(),
        )
    for resource, kind in RESOURCES.items():
        yield (
            kind,
            (
                f"cancer_study_identifier: {SID}\nresource_type: {resource}\n"
                f"data_filename: {value}\n"
            ).encode(),
        )


@pytest.mark.parametrize("target", ["meta_study.txt", "meta_x.txt", "data_clinical_sample.txt"])
def test_every_non_clinical_type_naming_reads_is_refused(
    make_study: Any, discovery: Any, base: Any, target: str
) -> None:
    """A meta file of every type in the ``data_filename`` row, naming the study meta, itself or a
    clinical data file, is refused (cells 09-13, 26, 30-33 and 40 for a few types; stricter, for
    a meta file, than the reference, which is clean)."""
    seen = set()
    for kind, content in every_type_meta(target):
        seen.add(kind)
        directory = make_study({**base, "meta_x.txt": content})
        assert discovery.outcome(directory) == (
            "onco.DATA_FILE",
            "meta_x.txt",
            f"«meta_x.txt» has a data_filename that {READS}",
        ), kind
    assert seen == DATA_TYPES


def test_every_non_clinical_type_naming_a_plain_file_is_noted(
    make_study: Any, discovery: Any, base: Any
) -> None:
    for kind, content in every_type_meta("data.txt"):
        found = discovery.run(make_study({**base, "meta_x.txt": content, "data.txt": b"x"}))
        by_entry, _ = notes_by_entry(found)
        assert by_entry["data.txt"] == DATA_NOTE, kind


HOSTILE = ("", "/abs", "x/../data_clinical_sample.txt", "data_clinical_sample.txt", "link", "\0")


@pytest.mark.parametrize(
    ("meta", "content"),
    [
        ("meta_study.txt", lambda b, v: b["meta_study.txt"] + f"data_filename: {v}\n".encode()),
        ("meta_mutations.txt", lambda b, v: maf_meta("nofile.txt") + f"tags_file: {v}\n".encode()),
        (
            "meta_clinical_sample.txt",
            lambda b, v: b["meta_clinical_sample.txt"] + f"tags_file: {v}\n".encode(),
        ),
        (
            "meta_cnal.txt",
            lambda b, v: cna_meta("nofile.txt", v, "DISCRETE_LONG"),
        ),
        (
            "meta_mutations.txt",
            lambda b, v: maf_meta("nofile.txt") + f"pd_annotations_filename: {v}\n".encode(),
        ),
        (
            "meta_cna.txt",
            lambda b, v: cna_meta("nofile.txt", None) + f"tags_file: {v}\n".encode(),
        ),
    ],
    ids=[
        "study data_filename",
        "MAF tags_file",
        "sample tags_file",
        "DISCRETE_LONG pd",
        "MAF pd",
        "CNA tags_file",
    ],
)
def test_the_ignored_sites(
    make_study: Any,
    discovery: Any,
    base: Any,
    kinds: Any,
    meta: str,
    content: Callable[[Mapping[str, bytes], str], bytes],
) -> None:
    """The study meta's ``data_filename`` (cell 68), a ``tags_file`` outside the study meta and a
    ``pd_annotations_filename`` outside CNA_DISCRETE (cell 27) are not read by the reference and
    are ignored: no value there, however hostile, changes the outcome."""
    for value in HOSTILE:
        tree = {**base, "link": kinds.symlink("data_clinical_sample.txt")}
        tree[meta] = content(base, value)
        assert discovery.outcome(make_study(tree))[0] == "ok", value


def test_a_hard_link_is_the_stated_residual(
    make_study: Any, discovery: Any, base: Any, kinds: Any
) -> None:
    """A hard link to the sample data file under another name is a regular file outside Reads(S)
    by its listing entry: accepted, with the data file's note (D409: hard links cannot be seen
    without ``stat``)."""
    tree = {
        **base,
        "meta_mut.txt": maf_meta("hard.txt"),
        "hard.txt": kinds.hard_link("data_clinical_sample.txt"),
    }
    by_entry, _ = notes_by_entry(discovery.run(make_study(tree)))
    assert by_entry["hard.txt"] == DATA_NOTE


# --- What is read ---------------------------------------------------------------------------------

_OPENED: list[str] | None = None
_HOOKED = False


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _OPENED is not None and event in ("open", "os.listdir", "os.scandir") and args:
        path = args[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            _OPENED.append(os.fsdecode(path))  # pyright: ignore[reportUnknownArgumentType]


@pytest.fixture
def opened() -> Iterator[list[str]]:
    """Every path opened or listed while the test runs (an audit hook, installed once and idle
    outside this fixture)."""
    global _OPENED, _HOOKED
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED = True
    _OPENED = []
    try:
        yield _OPENED
    finally:
        _OPENED = None


def test_nothing_but_the_listing_and_meta_s_is_read(
    make_study: Any, discovery: Any, base: Any, opened: list[str]
) -> None:
    """Every path value accepted, each naming a file: ``discover`` lists the directory once and
    reads Meta(S), and opens nothing a value names (the audit hook sees every ``open``)."""
    tree = {
        **base,
        "meta_mut.txt": maf_meta("data_mut.txt"),
        "data_mut.txt": MAF,
        "meta_cna.txt": cna_meta("data_cna.txt", "data_pd.txt"),
        "data_cna.txt": CNA,
        "data_pd.txt": PD,
        "tags.yml": YAML,
        "meta_study.txt": study_with_tags(base, "tags.yml"),
    }
    directory = make_study(tree)
    reader = discovery.reader(directory)
    source = reader.inner.confine(directory)
    opened.clear()
    found = discovery.study.discover(reader, source, ImportLimits())
    metas = ["meta_clinical_patient.txt", "meta_clinical_sample.txt", "meta_cna.txt"]
    metas += ["meta_mut.txt", "meta_study.txt"]
    assert [name for name, _ in reader.reads] == metas
    assert reader.listed == [directory.name]
    touched = {Path(path).name if os.path.isabs(path) else path for path in opened}
    assert touched <= {directory.name, *metas}, touched
    assert not touched & {"data_mut.txt", "data_cna.txt", "data_pd.txt", "tags.yml"}
    by_entry, _ = notes_by_entry(found)
    assert by_entry["tags.yml"] == TAGS_NOTE


# --- Any value, any listing (Hypothesis) ----------------------------------------------------------


def _shown(name: str) -> bool:
    """D409's shown name, written again: at most 200 characters of Unicode text."""
    if len(name) > 200:
        return False
    if any(0xD800 <= ord(c) <= 0xDFFF for c in name):
        return False
    return not any(0xFDD0 <= ord(c) <= 0xFDEF or (ord(c) & 0xFFFE) == 0xFFFE for c in name)


def status(found: Any, refusal: Any) -> tuple[Any, ...]:
    if refusal is not None:
        return ("refused", str(refusal.code), said(refusal.message))
    noted = set()
    for note in found.notes:
        if note.kind == "skipped_source" and isinstance(note.message[-1], DataSegment):
            reason = note.message[0].text.removesuffix(": ")
            if reason in (DATA_NOTE, TAGS_NOTE):
                noted.add((note.message[-1].data, reason))
    return ("ok", frozenset(noted))


def expected_status(case: Case) -> tuple[Any, ...]:
    full = expected(case)
    if full[0] == "refused":
        return full
    noted = set()
    for line in full[1:]:
        name, _, reason = line.rpartition(": ")
        if reason in (DATA_NOTE, TAGS_NOTE) and name != UNSHOWN:
            noted.add((name, reason))
    return ("ok", frozenset(noted))


ANY_NAME = st.builds(
    lambda front, name, back: front + name + back,
    st.sampled_from(["", "", "", ".", "ONCOKB_IMPORT_BACKUP_"]),
    st.text(alphabet=st.characters(blacklist_characters="/\0"), min_size=1, max_size=12),
    st.sampled_from(["", "", "~"]),
).filter(lambda name: name not in (".", "..") and "meta" not in name.lower())
KIND_OF = st.sampled_from(["file", "symlink", "directory", "other", "unconfined"])
SPELL = st.sampled_from(
    [
        lambda name: name,
        str.upper,
        str.lower,
        str.casefold,
        str.swapcase,
        lambda name: unicodedata.normalize("NFD", name),
        lambda name: unicodedata.normalize("NFC", name),
        lambda name: unicodedata.normalize("NFKC", name),
        lambda name: "./" + name,
        lambda name: name + "/",
        lambda name: name + ".",
        lambda name: " " + name,
    ]
)
VALUE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r\n"), max_size=16
)


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    listing=st.dictionaries(ANY_NAME, KIND_OF, max_size=5),
    role_index=st.integers(0, len(ROLES) - 1),
    pick=st.integers(0, 10),
    spell=SPELL,
    free=VALUE_TEXT,
    use_free=st.booleans(),
)
def test_any_value_against_any_listing(
    grid: Any,
    listing: dict[str, str],
    role_index: int,
    pick: int,
    spell: Callable[[str], str],
    free: str,
    use_free: bool,
) -> None:
    """Totality and the oracle at once: whatever names the listing holds (any code points, lone
    surrogates among them) and whatever value is written, spelled from a listed name or free,
    ``discover`` returns or refuses with ``DATA_FILE`` or ``META_VALUE``, as the oracle says."""
    base: Mapping[str, bytes] = grid.BASE
    role = ROLES[role_index]
    names = sorted(name for name in listing if name not in base and name not in role.extra)
    value = free if use_free or not names else spell(names[pick % len(names)])
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        value = free
    if any(end in value for end in ("\r", "\n")):
        value = free
    entries: dict[str, tuple[str, bytes]] = {n: ("file", c) for n, c in base.items()}
    for name in names:
        entries[name] = (listing[name], b"x")
    entries[role.meta] = ("file", role.content(base, value))
    for name, content in role.extra.items():
        entries[name] = ("file", content)
    meta_files = frozenset(
        {"meta_study.txt", "meta_clinical_sample.txt", "meta_clinical_patient.txt", role.meta}
    )
    data_files = {"data_clinical_sample.txt", "data_clinical_patient.txt"}
    used = frozenset({"meta_study.txt", "meta_clinical_sample.txt", "meta_clinical_patient.txt"})
    case = Case(role, value, entries, meta_files | data_files, meta_files, used | data_files)
    study = importlib.import_module(STUDY_MODULE)
    found, refusal = _discover(study, entries)
    assert status(found, refusal) == expected_status(case)
