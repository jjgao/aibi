"""The paths oracle (D409, D436): what ``test_paths.py`` and the closure guard of
``test_reachability.py`` state alike about the path values outside the clinical role.

This is not a test: pytest does not collect it, and it imports nothing of the pack (no module of
``aibi.packs``, by any form ``test_lazy.py`` scans for), so each test module loads it by its path
inside a fixture (``conftest.oracle``) or a function. What it holds is written by hand, from D409
and the survey of cbioportal-core 47890bb, never read from ``study``:

- ``ROWS``, the test's own statement of each site of ``study.PATH_SITES``: its key, its role and
  the meta types it is read from. The guard holds the pack's table to them, row by row.
- ``DATA_TYPES``, ``PAIRS`` and ``RESOURCES``: every non-clinical type, and the type pairs and
  resource types that name each (C:651-689). The guard holds them to the pack's key tables
  (``meta.PAIR_TYPES``, ``meta.RESOURCE_TYPES``) and generates its own heads from those tables,
  never from these.
- ``discover_listing``, the pack's ``discover`` (given as a module) over a listing given as data.
- The guard's cases: a study of ``clinical_grid.BASE`` with one value at one site of one meta type
  (``guard_case``), every other site clean, and the outcome the hand rows give it
  (``guard_expected``); and a declared row's case (``declared_case``), accepted.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.output import TextSegment
from aibi.core.schema.pack_api import ConfinedPath, DirectoryEntry, Refused

DATA = "data"
TAGS = "tags"
LISTING = "listing"
CASE_LISTS = "case lists"
CHECKED = (DATA, TAGS)
"""The roles whose values ``discover`` resolves (phases 9 and 10); the others are declared."""
STUDY = "STUDY"

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


@dataclass(frozen=True)
class Row:
    """A site as the test states it: the meta key or the listing, the role, the meta types."""

    key: str
    role: str
    types: frozenset[str]


ROWS = (
    Row("data_filename", DATA, DATA_TYPES),
    Row("pd_annotations_filename", DATA, frozenset({"CNA_DISCRETE"})),
    Row("tags_file", TAGS, frozenset({STUDY})),
    Row("listing:meta", LISTING, DATA_TYPES | {STUDY, "SAMPLE_ATTRIBUTES", "PATIENT_ATTRIBUTES"}),
    Row("listing:case_lists", CASE_LISTS, frozenset()),
    Row("isdir:case_lists", CASE_LISTS, frozenset()),
)
"""The sites, in the order of phase 9 (a meta file's ``data_filename`` before its
``pd_annotations_filename``), then phase 10, then the listings: V:4854-4856 and V:1250-1254 for the
data and PD values (V:1232 and V:1428: not the DISCRETE_LONG validator), V:4799 and V:5563 for the
tags file, which the study meta alone holds; the listing of the study's directory, every meta
file's type; the case lists, which ``discover`` does not list (exit 3 (19))."""

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

SID = "demo_study"
EVERY_KEY = (
    f"cancer_study_identifier: {SID}\nstable_id: s\nshow_profile_in_analysis_tab: true\n"
    "profile_name: p\nprofile_description: p\nreference_genome_id: hg19\ndescription: d\n"
    "source_stable_id: s\ngeneset_def_version: 1\ngeneric_assay_type: g\n"
)
"""Every key a non-clinical type needs, so that a meta file of any pair passes its mandatory
keys."""


def fields(text: str) -> dict[str, str]:
    """A meta file's text (one ``key: value`` a line, as written here) as its fields."""
    return dict(line.split(": ", 1) for line in text.splitlines())


# --- Discovering a listing given as data ---------------------------------------------------------

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


def discover_listing(study: Any, entries: Mapping[str, tuple[str, bytes]]) -> tuple[Any, Any]:
    """``study.discover`` over a listing: ``(study, None)`` or ``(None, the one refusal)``."""
    reader = ListedReader(entries)
    try:
        found = study.discover(reader, ConfinedPath(ROOT), ImportLimits())
    except Refused as error:
        (refusal,) = error.refusals
        return None, refusal
    return found, None


def said(segments: Sequence[Any]) -> str:
    """A message as text, each ``data`` segment between ``«`` and ``»``."""
    return "".join(s.text if isinstance(s, TextSegment) else f"«{s.data}»" for s in segments)


# --- The guard's cases ---------------------------------------------------------------------------

VALUE = "meta_study.txt"
"""The value at the case's site: a member of Reads(S), which every checked site refuses."""
PLAIN = "plain.txt"
"""A listed regular file outside Reads(S), the clean ``data_filename`` of a case whose site is
another key."""
READS = "names a meta file or a clinical data file, exactly or under case and normalisation folding"


def guard_case(
    base: Mapping[str, bytes], kind: str, head: str | None, key: str
) -> dict[str, tuple[str, bytes]]:
    """``base`` with ``key: VALUE`` in a meta file of the type ``kind``: the study meta for
    ``STUDY``, else ``meta_x.txt`` of ``head`` with every key a type needs; its ``data_filename``
    is ``PLAIN`` unless that is ``key``. No other site holds a value."""
    entries = {name: ("file", content) for name, content in base.items()}
    entries[PLAIN] = ("file", b"x")
    if kind == STUDY:
        entries["meta_study.txt"] = ("file", base["meta_study.txt"] + f"{key}: {VALUE}\n".encode())
    else:
        assert head is not None
        clean = "" if key == "data_filename" else f"data_filename: {PLAIN}\n"
        entries["meta_x.txt"] = ("file", f"{EVERY_KEY}{head}{clean}{key}: {VALUE}\n".encode())
    return entries


def declared_case(base: Mapping[str, bytes], row: Row) -> dict[str, tuple[str, bytes]]:
    """``base`` with what a declared row is about, every path value clean: a ``case_lists``
    directory for the case lists (exit 3 (19)), a meta file of a type never imported naming
    ``PLAIN`` for the listing. Both are accepted."""
    entries = {name: ("file", content) for name, content in base.items()}
    entries[PLAIN] = ("file", b"x")
    if row.role == CASE_LISTS:
        entries["case_lists"] = ("directory", b"")
    else:
        head = "genetic_alteration_type: MUTATION_EXTENDED\ndatatype: MAF\n"
        entries["meta_x.txt"] = ("file", f"{EVERY_KEY}{head}data_filename: {PLAIN}\n".encode())
    return entries


def guard_expected(row: Row, kind: str) -> tuple[str, str] | None:
    """The outcome the hand row ``row`` gives its key's value in a meta file of the type ``kind``:
    accepted (``None``) where the row does not read that type, else refused naming the meta file
    and the key, with the code and the verb of the row's role."""
    if kind not in row.types:
        return None
    meta = "meta_study.txt" if kind == STUDY else "meta_x.txt"
    code, verb = ("onco.META_VALUE", "holds") if row.role == TAGS else ("onco.DATA_FILE", "has")
    return code, f"«{meta}» {verb} a {row.key} that {READS}"


def outcome(study: Any, entries: Mapping[str, tuple[str, bytes]]) -> tuple[str, str] | None:
    """``None`` when ``study.discover`` accepts the listing, else its refusal's code and text."""
    _, refusal = discover_listing(study, entries)
    return None if refusal is None else (str(refusal.code), said(refusal.message))
