"""Identifiers (SPEC §5.1).

Patterns are written without look-around so that the same expressions work in Pydantic (Rust
regex), in Python's ``re`` and in JSON Schema validators (ECMA-262).
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Annotated, Literal

from pydantic import StringConstraints

# An identifier: ``[a-z][a-z0-9_]*`` without ``__``. The trailing ``_?`` keeps a single final
# underscore legal, as the grammar allows, while ``(?:_[a-z0-9]+)*`` forbids ``__``.
IDENT = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*_?"
NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_COLUMNS = rf"{IDENT}(?:\+{IDENT})*"

IDENTIFIER_RE = re.compile(rf"^{IDENT}$")
COLUMN_REF_RE = re.compile(rf"^{IDENT}\.{IDENT}$")
CONCEPT_ID_RE = re.compile(rf"^{IDENT}:{IDENT}(?:\.{IDENT})*$")
RELATIONSHIP_ID_RE = re.compile(rf"^rel:{IDENT}\.{_COLUMNS}$")
COVERAGE_ID_RE = re.compile(rf"^cov:{IDENT}\.{_COLUMNS}$")
ENDPOINT_ID_RE = re.compile(rf"^ep:{IDENT}$")
ANALYSIS_ID_RE = re.compile(rf"^{IDENT}\.{IDENT}$")
MODEL_CARD_ID_RE = re.compile(rf"^model:{IDENT}$")
PACK_LEAF_KIND_RE = re.compile(rf"^{IDENT}\.{IDENT}$")
DATASET_REF_RE = re.compile(rf"^{IDENT}(?:@(?:[1-9][0-9]*|sha256:[0-9a-f]{{64}}|draft))?$")
NAME_RE = re.compile(rf"^{NAME}$")
JSON_POINTER_RE = re.compile(r"^(?:/(?:[^~/]|~[01])*)*$")

MAX_SAFE_INTEGER = 2**53 - 1
"""Integers beyond ±(2^53 - 1) are carried as decimal strings (SPEC §5.1)."""

CORE_ANALYSIS_FAMILIES = frozenset({"summary", "compare", "survival"})
CORE_LEAF_KINDS = frozenset({"value", "exists", "covered", "ids", "cohort"})
RESERVED_PACK_IDS = CORE_ANALYSIS_FAMILIES | CORE_LEAF_KINDS | {"core"}
"""Pack ids equal no core analysis family, no core leaf kind and not the ``core`` namespace."""

DATASET_DESCRIPTOR_ID = "dataset"
"""The dataset descriptor's id, which no table may take (SPEC §5.1)."""


def _pattern(regex: re.Pattern[str]) -> StringConstraints:
    return StringConstraints(pattern=regex.pattern)


Identifier = Annotated[str, _pattern(IDENTIFIER_RE)]
DatasetId = Identifier
TableId = Identifier
ColumnId = Identifier
PackId = Identifier
ColumnRef = Annotated[str, _pattern(COLUMN_REF_RE)]
"""``<table>.<column>``: a column's descriptor id."""
ConceptId = Annotated[str, _pattern(CONCEPT_ID_RE)]
RelationshipId = Annotated[str, _pattern(RELATIONSHIP_ID_RE)]
CoverageId = Annotated[str, _pattern(COVERAGE_ID_RE)]
EndpointId = Annotated[str, _pattern(ENDPOINT_ID_RE)]
AnalysisId = Annotated[str, _pattern(ANALYSIS_ID_RE)]
ModelCardId = Annotated[str, _pattern(MODEL_CARD_ID_RE)]
DatasetRef = Annotated[str, _pattern(DATASET_REF_RE)]
"""A dataset id, optionally pinned: ``x``, ``x@3``, ``x@sha256:<hex>`` or ``x@draft``."""
Name = Annotated[str, _pattern(NAME_RE)]
"""A cohort or parameter name."""
JsonPointer = Annotated[str, _pattern(JSON_POINTER_RE)]


def is_pack_id(value: str) -> bool:
    return IDENTIFIER_RE.match(value) is not None and value not in RESERVED_PACK_IDS


def normalise(name: str) -> str:
    """Normalise one source name, before prefixes and collisions (SPEC §5.1)."""
    lowered = unicodedata.normalize("NFKC", name).lower()
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


def normalise_names(
    names: Iterable[str],
    kind: Literal["table", "column"],
    previous: Mapping[str, str] | None = None,
) -> list[str]:
    """Derive unique ids from source names, in source order (SPEC §5.1).

    Empty results become ``t_<position>`` or ``c_<position>`` (1-based); results starting with a
    digit get the ``t_`` or ``c_`` prefix. An id already assigned, or the table id ``dataset``, is
    a collision, resolved by the smallest suffix ``_<n>``, n ≥ 2, that gives an unassigned id.

    On re-import, ``previous`` maps the original names of the previous release to their ids: a
    name found there keeps its id, and the other names are assigned around the kept ids.
    """
    names = list(names)
    kept = dict(previous or {})
    prefix = "t_" if kind == "table" else "c_"
    taken: set[str] = {DATASET_DESCRIPTOR_ID} if kind == "table" else set()
    taken.update(kept[name] for name in names if name in kept)
    ids: list[str] = []
    for position, name in enumerate(names, start=1):
        if name in kept:
            ids.append(kept.pop(name))
            continue
        base = normalise(name)
        if not base:
            base = f"{prefix}{position}"
        elif base[0].isdigit():
            base = prefix + base
        candidate = base
        n = 2
        while candidate in taken:
            candidate = f"{base}_{n}"
            n += 1
        taken.add(candidate)
        ids.append(candidate)
    return ids
