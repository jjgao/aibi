"""Default size limits for documents (SPEC §7.1, §14).

Every refusal names the limit it hit, by one of the names below; clients may depend on them.
"""

from dataclasses import dataclass
from typing import cast

from pydantic import BeforeValidator
from pydantic_core import PydanticCustomError

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
"""Bytes in a document as written, and in the document after ``params`` substitution."""
MAX_DEPTH = 64
"""Nesting of arrays and objects in a document as written."""
MAX_VALUES = 200_000
"""JSON values (strings, numbers, booleans, arrays and objects) in a document, as written and
after substitution; it bounds the work of validating a document that is wrong everywhere."""
MAX_LIST = 10_000
"""Members of a ``values``, ``ids`` or scope value list (SPEC §14)."""
MAX_STRING = 4_096
"""Characters in a constant or other short string."""
MAX_TEXT = 10_000
"""Characters in ``notes`` and ``note``."""
MAX_NAME = 200
"""Characters in a self-declared name (``agent:`` and ``operator:`` in ``drafted_by``)."""
MAX_IDENTIFIER = 64
"""Characters in an identifier, and in a cohort or parameter name (SPEC §5.1)."""
MAX_PATH_STEPS = 16
"""Steps in a path, and entries in a quantifier list."""
MAX_COLUMNS = 16
"""Columns in a unit key, and scope columns in a ``covered`` leaf."""
MAX_CLAUSES = 256
"""Clauses in one ``all``, ``any`` or ``where`` list, before the canonical caps of M2."""
MAX_COHORTS = 6
MAX_DATASETS = 64
"""Datasets in one cross-dataset cohort, and entries in a ``via`` map by dataset."""
MAX_PACKS = 16
"""Packs a document names."""
MAX_VIEWS = 8
MAX_PARAMS = 256
MAX_REFUSALS = 1_000
"""Refusals returned for one document; one more says that the rest were left out."""

MAX_POINTER = 16_384
"""Characters in the JSON Pointer to any value."""
MAX_POINTERS = 64 * 2**20
"""Characters in the JSON Pointers to all of a document's values, together: long keys above
many values would otherwise make validation errors, which each carry their path, large."""

DOCUMENT_BYTES = "document_bytes"
SUBSTITUTED_BYTES = "substituted_document_bytes"
NESTING_DEPTH = "nesting_depth"
JSON_VALUES = "json_values"
LIST_MEMBERS = "list_members"
CONSTANT_CHARACTERS = "constant_characters"
NOTE_CHARACTERS = "note_characters"
NAME_CHARACTERS = "name_characters"
IDENTIFIER_CHARACTERS = "identifier_characters"
REFERENCE_CHARACTERS = "reference_characters"
"""A compound id or reference, such as ``<table>.<column>``, as a whole; each identifier in it is
bounded by ``identifier_characters``."""
PATH_STEPS = "path_steps"
KEY_COLUMNS = "key_columns"
SCOPE_COLUMNS = "scope_columns"
CLAUSES = "clauses_per_list"
COHORTS = "cohorts"
DATASETS = "datasets"
PACKS = "packs"
VIEWS = "views"
PARAMETERS = "parameters"
REFUSALS = "refusals"
POINTER_CHARACTERS = "pointer_characters"
ALL_POINTER_CHARACTERS = "all_pointer_characters"


@dataclass(frozen=True)
class LimitName:
    """Annotation metadata naming the limit that a length constraint enforces."""

    name: str


def map_cap(maximum: int) -> BeforeValidator:
    """Annotation metadata refusing a map with more than ``maximum`` entries before any entry
    is checked, as Pydantic refuses a list: it checks a dict's length only when every entry is
    valid, so a map too large with a bad entry would be refused for the entry alone. The map's
    ``Field(max_length=…)`` stays, for the JSON Schema."""

    def check(value: object) -> object:
        size = _entries(value)
        if size > maximum:
            raise PydanticCustomError(
                "too_long",
                "{field_type} should have at most {max_length} items, not {actual_length}",
                {"field_type": "Dictionary", "max_length": maximum, "actual_length": size},
            )
        return value

    return BeforeValidator(check)


def _entries(value: object) -> int:
    return len(cast(dict[object, object], value)) if isinstance(value, dict) else 0


__all__ = [
    "ALL_POINTER_CHARACTERS",
    "CLAUSES",
    "COHORTS",
    "CONSTANT_CHARACTERS",
    "DATASETS",
    "DOCUMENT_BYTES",
    "IDENTIFIER_CHARACTERS",
    "JSON_VALUES",
    "KEY_COLUMNS",
    "LIST_MEMBERS",
    "MAX_CLAUSES",
    "MAX_COHORTS",
    "MAX_COLUMNS",
    "MAX_DATASETS",
    "MAX_DEPTH",
    "MAX_DOCUMENT_BYTES",
    "MAX_IDENTIFIER",
    "MAX_LIST",
    "MAX_NAME",
    "MAX_PACKS",
    "MAX_PARAMS",
    "MAX_PATH_STEPS",
    "MAX_POINTER",
    "MAX_POINTERS",
    "MAX_REFUSALS",
    "MAX_STRING",
    "MAX_TEXT",
    "MAX_VALUES",
    "MAX_VIEWS",
    "NAME_CHARACTERS",
    "NESTING_DEPTH",
    "NOTE_CHARACTERS",
    "PACKS",
    "PARAMETERS",
    "PATH_STEPS",
    "POINTER_CHARACTERS",
    "REFERENCE_CHARACTERS",
    "REFUSALS",
    "SCOPE_COLUMNS",
    "SUBSTITUTED_BYTES",
    "VIEWS",
    "LimitName",
    "map_cap",
]
