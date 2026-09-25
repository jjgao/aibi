"""Default size limits for documents, descriptors and imports (SPEC §7.1, §14).

Every refusal names the limit it hit, by one of the names below; clients may depend on them.
"""

from dataclasses import dataclass
from typing import cast

from pydantic import BeforeValidator
from pydantic_core import PydanticCustomError

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
"""Bytes in a document as written, and in the document after ``params`` substitution."""
MAX_DEPTH = 64
"""Nesting of arrays and objects in a document as written, and in a descriptor."""
MAX_VALUES = 200_000
"""JSON values (strings, numbers, booleans, arrays and objects) in a document, as written and
after substitution, and in a descriptor; it bounds the work of validating one that is wrong
everywhere."""
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
"""Columns in a unit key, a scope, a table key or a relationship."""
MAX_ENTRIES = 64
"""Members of the other short lists and maps in descriptors (metadata, event codes, caveats)."""
MAX_CLAUSES = 256
"""Clauses in one ``all``, ``any`` or ``where`` list, before the canonical caps of M2."""
MAX_CLAUSE_DEPTH = 8
"""Clause objects on the longest chain of a cohort's canonical form, from a member of its
top-level ``all`` to a leaf, both included (SPEC §7.1)."""
MAX_LEAVES = 64
"""Leaves of a cohort's canonical form, those inside every ``where`` included (SPEC §7.1)."""
MAX_COHORT_REFERENCES = 256
"""``cohort`` leaves in one cohort as written (SPEC §7.1): each inlines the cohort it names, so
they bound the work of resolving a cohort before its canonical form is measured."""
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
"""Characters in the JSON Pointer to any value of a document or descriptor."""
MAX_POINTERS = 64 * 2**20
"""Characters in the JSON Pointers to all of a document's or descriptor's values, together: long
keys above many values would otherwise make validation errors, which each carry their path,
large."""

DOCUMENT_BYTES = "document_bytes"
DESCRIPTOR_BYTES = "descriptor_bytes"
SUBSTITUTED_BYTES = "substituted_document_bytes"
NESTING_DEPTH = "nesting_depth"
JSON_VALUES = "json_values"
LIST_MEMBERS = "list_members"
CONSTANT_CHARACTERS = "constant_characters"
NOTE_CHARACTERS = "note_characters"
STRING_CHARACTERS = "string_characters"
TEXT_CHARACTERS = "text_characters"
ENTRIES = "entries"
NAME_CHARACTERS = "name_characters"
IDENTIFIER_CHARACTERS = "identifier_characters"
REFERENCE_CHARACTERS = "reference_characters"
"""A compound id or reference, such as ``<table>.<column>``, as a whole; each identifier in it is
bounded by ``identifier_characters``."""
PATH_STEPS = "path_steps"
PATH_SEARCH = "path_search"
"""Steps an implicit path search takes before it gives up (the engine's ``PATH_SEARCH``)."""
KEY_COLUMNS = "key_columns"
SCOPE_COLUMNS = "scope_columns"
CLAUSES = "clauses_per_list"
CLAUSE_DEPTH = "clause_depth"
LEAVES = "leaves_per_cohort"
COHORT_REFERENCES = "cohort_references"
COHORTS = "cohorts"
DATASETS = "datasets"
PACKS = "packs"
VIEWS = "views"
PARAMETERS = "parameters"
REFUSALS = "refusals"
POINTER_CHARACTERS = "pointer_characters"
ALL_POINTER_CHARACTERS = "all_pointer_characters"
IMPORT_BYTES = "import_bytes"
"""Bytes of one file an import reads, an upload included."""
ARCHIVE_MEMBERS = "archive_members"
ARCHIVE_BYTES = "archive_bytes"
"""Bytes of an archive's members, uncompressed, together."""
MEMBER_BYTES = "member_bytes"
"""Bytes of one archive member, uncompressed."""
ARCHIVE_RATIO = "archive_ratio"
"""Uncompressed bytes per compressed byte, of a member over 1 MiB and of an archive's total."""
IMPORT_TABLES = "import_tables"
TABLE_COLUMNS = "table_columns"
IMPORT_CELLS = "import_cells"
"""Cells of every table of an import, together."""
READER_MEMORY = "reader_memory"
"""Bytes of address space of the worker process that reads an import's workbooks and Parquet
files."""
READER_SECONDS = "reader_seconds"
"""Seconds that worker process reads for, over the whole import; an import waits for one to
start as long, and 5 seconds more, in which one stopped at its deadline is reaped."""
READER_WORKERS = "reader_workers"
"""Worker processes of that kind that run at once in a server process, over every import: at
least 1. They bound the workers, not the imports, whose memory in the server adds up (D225)."""
DECODED_BYTES = "decoded_bytes"
"""Bytes of the strings read from an import's workbooks and Parquet files, together, in UTF-8."""

OPEN_PROPOSALS = "open_proposals"
"""Open proposals of one dataset (SPEC §14, D248)."""
PROPOSAL_BYTES = "proposal_bytes"
"""Bytes of one proposal's value, in RFC 8785 form (D248)."""
CHANGE_EDITS = "change_edits"
"""Edits in one change to a draft (D245)."""
QUEUE_ITEMS = "queue_items"
"""Items of one curation queue (D250)."""
QUEUE_BYTES = "queue_bytes"
"""Bytes of the items of one curation queue, in JSON (D250)."""
EXTENSION_STEPS = "extension_steps"
"""Steps of evaluating one extension object against its pack's schema (D247)."""
MAX_OPEN_PROPOSALS = 10_000
MAX_PROPOSAL_BYTES = 64 * 1024
"""Bytes of a proposed value in RFC 8785 form, so that ``MAX_OPEN_PROPOSALS`` bounds the queue's
size too; less than a field can hold (a ``permissible_values`` list of ``MAX_LIST`` entries), which
an operator's session or an importer writes instead (D248)."""
MAX_CHANGE_EDITS = 256
MAX_QUEUE_ITEMS = 10_000
MAX_QUEUE_BYTES = 8 * 2**20
"""Bytes of a curation queue's items in JSON: four descriptors of ``descriptor_bytes``, or about a
hundred proposals of ``MAX_PROPOSAL_BYTES`` with their evidence (D250)."""

RATIO_FLOOR = 1 << 20
"""Uncompressed bytes from which ``archive_ratio`` applies, to a member or to the total."""


@dataclass(frozen=True)
class ImportLimits:
    """The limits of one import (SPEC §14, D233), each named by the refusal that hits it."""

    import_bytes: int = 1 << 30
    archive_members: int = 10_000
    archive_bytes: int = 4 << 30
    member_bytes: int = 1 << 30
    archive_ratio: int = 100
    import_tables: int = 1_000
    table_columns: int = 4_096
    import_cells: int = 20_000_000
    reader_memory: int = 4 << 30
    reader_seconds: int = 300
    reader_workers: int = 2
    decoded_bytes: int = 1 << 28

    def __post_init__(self) -> None:
        if self.reader_workers < 1:
            raise ValueError(
                f"reader_workers is at least 1, not {self.reader_workers}: an import reads its "
                "workbooks and Parquet files in a worker"
            )


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
    "ARCHIVE_BYTES",
    "ARCHIVE_MEMBERS",
    "ARCHIVE_RATIO",
    "CHANGE_EDITS",
    "CLAUSES",
    "CLAUSE_DEPTH",
    "COHORTS",
    "COHORT_REFERENCES",
    "CONSTANT_CHARACTERS",
    "DATASETS",
    "DECODED_BYTES",
    "DESCRIPTOR_BYTES",
    "DOCUMENT_BYTES",
    "ENTRIES",
    "EXTENSION_STEPS",
    "IDENTIFIER_CHARACTERS",
    "IMPORT_BYTES",
    "IMPORT_CELLS",
    "IMPORT_TABLES",
    "JSON_VALUES",
    "KEY_COLUMNS",
    "LEAVES",
    "LIST_MEMBERS",
    "MAX_CHANGE_EDITS",
    "MAX_CLAUSES",
    "MAX_CLAUSE_DEPTH",
    "MAX_COHORTS",
    "MAX_COHORT_REFERENCES",
    "MAX_COLUMNS",
    "MAX_DATASETS",
    "MAX_DEPTH",
    "MAX_DOCUMENT_BYTES",
    "MAX_ENTRIES",
    "MAX_IDENTIFIER",
    "MAX_LEAVES",
    "MAX_LIST",
    "MAX_NAME",
    "MAX_OPEN_PROPOSALS",
    "MAX_PACKS",
    "MAX_PARAMS",
    "MAX_PATH_STEPS",
    "MAX_POINTER",
    "MAX_POINTERS",
    "MAX_PROPOSAL_BYTES",
    "MAX_QUEUE_BYTES",
    "MAX_QUEUE_ITEMS",
    "MAX_REFUSALS",
    "MAX_STRING",
    "MAX_TEXT",
    "MAX_VALUES",
    "MAX_VIEWS",
    "MEMBER_BYTES",
    "NAME_CHARACTERS",
    "NESTING_DEPTH",
    "NOTE_CHARACTERS",
    "OPEN_PROPOSALS",
    "PACKS",
    "PARAMETERS",
    "PATH_SEARCH",
    "PATH_STEPS",
    "POINTER_CHARACTERS",
    "PROPOSAL_BYTES",
    "QUEUE_BYTES",
    "QUEUE_ITEMS",
    "RATIO_FLOOR",
    "READER_MEMORY",
    "READER_SECONDS",
    "READER_WORKERS",
    "REFERENCE_CHARACTERS",
    "REFUSALS",
    "SCOPE_COLUMNS",
    "STRING_CHARACTERS",
    "SUBSTITUTED_BYTES",
    "TABLE_COLUMNS",
    "TEXT_CHARACTERS",
    "VIEWS",
    "ImportLimits",
    "LimitName",
    "map_cap",
]
