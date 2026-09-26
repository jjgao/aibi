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
MAX_PACK_LEAVES = 256
"""Pack leaves in one cohort as written: each is compiled by its pack and its expansion
resolved, so they bound that work as ``MAX_COHORT_REFERENCES`` bounds inlining (D285)."""
MAX_SUMMARY_SEGMENTS = 64
"""Segments of a pack leaf's summary (D285)."""
MAX_COHORTS = 6
MAX_DATASETS = 64
"""Datasets in one cross-dataset cohort, and entries in a ``via`` map by dataset."""
MAX_PACKS = 16
"""Packs a document names."""
MAX_VIEWS = 8
MAX_PREDICATES = 16
"""Predicates of one view (``compare.existence``'s ``predicates``, D319): each is resolved and
queried like a cohort."""
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
PREDICATES = "predicates"
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
AGENT_PROPOSALS = "agent_proposals"
"""Open proposals of one dataset by agents, whom the public tools let propose without a token
(D277)."""
CLIENT_PROPOSALS = "client_proposals"
"""Open proposals of one dataset that agents made from one client, keyed as the rates key it
(D277)."""
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
PACK_LEAVES = "pack_leaves"
"""Pack leaves in one cohort as written (D285)."""
PACK_LEAF_STEPS = "pack_leaf_steps"
"""Steps of evaluating a document's pack leaves against their kinds' schemas, together (D285)."""
EXPANSION_VALUES = "expansion_values"
"""JSON values of the expansions of a document's pack leaves, together (D285)."""
MAX_OPEN_PROPOSALS = 10_000
MAX_AGENT_PROPOSALS = MAX_OPEN_PROPOSALS // 2
"""Agents' share of ``MAX_OPEN_PROPOSALS``: the rest stays for the importers' and the models'
proposals, however many an agent makes (D277)."""
MAX_CLIENT_PROPOSALS = MAX_AGENT_PROPOSALS // 10
"""One client's share of ``MAX_AGENT_PROPOSALS``, so that ten clients at least are needed to use
the agents' share up (D277)."""
MAX_PROPOSAL_BYTES = 64 * 1024
"""Bytes of a proposed value in RFC 8785 form, so that ``MAX_OPEN_PROPOSALS`` bounds the queue's
size too; less than a field can hold (a ``permissible_values`` list of ``MAX_LIST`` entries), which
an operator's session or an importer writes instead (D248)."""
MAX_CHANGE_EDITS = 256
MAX_QUEUE_ITEMS = 10_000
MAX_QUEUE_BYTES = 8 * 2**20
"""Bytes of a curation queue's items in JSON: four descriptors of ``descriptor_bytes``, or about a
hundred proposals of ``MAX_PROPOSAL_BYTES`` with their evidence (D250)."""

REQUEST_BYTES = "request_bytes"
"""Bytes of one request body over HTTP; an upload's are bounded by ``import_bytes`` instead
(D260)."""
OPERATOR_REQUESTS = "operator_requests"
"""Requests a client makes to the operator router, per minute (D259)."""
API_REQUESTS = "api_requests"
"""Requests a client makes to every other route, per minute (D259)."""
PAGE_REQUESTS = "page_requests"
"""Requests a client makes to the catalogue page's paths, per minute (D314)."""
TOKEN_FAILURES = "token_failures"
"""Operator requests a client makes with a missing or wrong curator token, per minute (D259)."""
CONCURRENT_IMPORTS = "concurrent_imports"
"""Uploads, imports, re-imports and erasures that run at once in the server (D266)."""
UPLOAD_IDLE_SECONDS = "upload_idle_seconds"
"""Seconds an upload, or any request's body (a JSON body, a tool call's), may send nothing before
it is refused, freeing its place (D266, D278)."""
UPLOAD_SECONDS = "upload_seconds"
"""Seconds an upload, or any request's body, may take in all: ``upload_idle_seconds``, and its
length at the slowest rate the server accepts (D266, D278)."""
TOOL_SECONDS = "tool_seconds"
"""Seconds a tool call may take, waiting for its place included (D278)."""
QUERY_SECONDS = "query_seconds"
"""Seconds a query worker may run a document's queries for, its start included (D293)."""
QUERY_MEMORY = "query_memory"
"""Bytes of resident memory a query worker may hold; DuckDB's memory limit is half of it
(D293)."""
QUERY_WORKERS = "query_workers"
"""Query workers that run at once in a server; a query waits for one at most
``query_seconds`` (D293)."""
QUERY_ANSWER_BYTES = "query_answer_bytes"
"""Bytes of the rows a query worker answers with (D293)."""
LOG_BYTES = "log_bytes"
"""Bytes the derivation log may take in the app DB, counting all that pruning can free (its texts,
and its derivations and issuances with their rows); a call that would record past it is refused
until the log is pruned (D300)."""
TOOL_CALLS = "tool_calls"
"""Tool calls that run at once in a server; a call that gets no place within ``tool_seconds`` is
refused (D278)."""
CLIENT_TOOL_CALLS = "client_tool_calls"
"""Tool calls that one client runs at once, its share of ``tool_calls``; a call that gets no
place within ``tool_seconds`` is refused (D278)."""
TOOL_BODY_IDLE_SECONDS = "tool_body_idle_seconds"
"""Seconds a tool call's body, which comes with no token, may send nothing before it is refused
(D278)."""
PROPOSAL_REQUESTS = "proposal_requests"
"""``propose_descriptor`` calls a client makes, per minute (D277)."""
MAX_BODY_BYTES = 8 * 1024 * 1024
"""The default ``request_bytes``: enough for a change that puts a descriptor at its limits, or
several smaller ones; the document's value limit bounds a body too (D260)."""

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


MIN_QUERY_MEMORY = 512 << 20
"""The least ``query_memory``: an interpreter with DuckDB loaded, and room to query."""
MAX_QUERY_ANSWER_BYTES = 64 << 20
"""``query_answer_bytes``: the largest answer the server reads from a query worker, its rows'
integers packed column by column (D293)."""


@dataclass(frozen=True)
class QueryLimits:
    """The limits of the workers that run queries (SPEC §14, D293), and their DuckDB threads."""

    query_seconds: int = 25
    query_memory: int = 2 << 30
    query_workers: int = 2
    query_threads: int = 1

    def __post_init__(self) -> None:
        for name in ("query_seconds", "query_memory", "query_workers", "query_threads"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} is at least 1, not {getattr(self, name)}")
        if self.query_memory < MIN_QUERY_MEMORY:
            raise ValueError(
                f"query_memory is at least {MIN_QUERY_MEMORY} bytes, what a worker needs to start"
            )


@dataclass(frozen=True)
class LogLimits:
    """How long the derivation log keeps the issuances of counts and of results, and how large it
    may grow (SPEC §12.2, §14, D300, D318): ``keep_count_issuances_days`` and
    ``keep_result_issuances_days`` (``None`` keeps them until an operator prunes) and
    ``log_bytes``. A result is kept longer than a count, since it is the id a finding cites; both
    expire, so that pruning can always free the log (a document run again gives the same ids and
    digests)."""

    keep_count_issuances_days: int | None = 30
    log_bytes: int = 8 << 30
    keep_result_issuances_days: int | None = 365

    def __post_init__(self) -> None:
        for name in ("keep_count_issuances_days", "keep_result_issuances_days"):
            days = getattr(self, name)
            if days is not None and not 1 <= days <= MAX_KEEP_DAYS:
                raise ValueError(f"{name} is from 1 to {MAX_KEEP_DAYS}, not {days}")
        if self.log_bytes < MIN_LOG_BYTES:
            raise ValueError(f"log_bytes is at least {MIN_LOG_BYTES}, not {self.log_bytes}")


MIN_LOG_BYTES = 1 << 20
"""The least ``log_bytes``: room for a few documents at their limits."""
MAX_KEEP_DAYS = 36500
"""The most ``keep_count_issuances_days`` and ``keep_result_issuances_days``: a century, whose
start the store's clock can still write."""


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
    "AGENT_PROPOSALS",
    "ALL_POINTER_CHARACTERS",
    "API_REQUESTS",
    "ARCHIVE_BYTES",
    "ARCHIVE_MEMBERS",
    "ARCHIVE_RATIO",
    "CHANGE_EDITS",
    "CLAUSES",
    "CLAUSE_DEPTH",
    "CLIENT_PROPOSALS",
    "CLIENT_TOOL_CALLS",
    "COHORTS",
    "COHORT_REFERENCES",
    "CONCURRENT_IMPORTS",
    "CONSTANT_CHARACTERS",
    "DATASETS",
    "DECODED_BYTES",
    "DESCRIPTOR_BYTES",
    "DOCUMENT_BYTES",
    "ENTRIES",
    "EXPANSION_VALUES",
    "EXTENSION_STEPS",
    "IDENTIFIER_CHARACTERS",
    "IMPORT_BYTES",
    "IMPORT_CELLS",
    "IMPORT_TABLES",
    "JSON_VALUES",
    "KEY_COLUMNS",
    "LEAVES",
    "LIST_MEMBERS",
    "LOG_BYTES",
    "MAX_AGENT_PROPOSALS",
    "MAX_BODY_BYTES",
    "MAX_CHANGE_EDITS",
    "MAX_CLAUSES",
    "MAX_CLAUSE_DEPTH",
    "MAX_CLIENT_PROPOSALS",
    "MAX_COHORTS",
    "MAX_COHORT_REFERENCES",
    "MAX_COLUMNS",
    "MAX_DATASETS",
    "MAX_DEPTH",
    "MAX_DOCUMENT_BYTES",
    "MAX_ENTRIES",
    "MAX_IDENTIFIER",
    "MAX_KEEP_DAYS",
    "MAX_LEAVES",
    "MAX_LIST",
    "MAX_NAME",
    "MAX_OPEN_PROPOSALS",
    "MAX_PACKS",
    "MAX_PACK_LEAVES",
    "MAX_PARAMS",
    "MAX_PATH_STEPS",
    "MAX_POINTER",
    "MAX_POINTERS",
    "MAX_PREDICATES",
    "MAX_PROPOSAL_BYTES",
    "MAX_QUERY_ANSWER_BYTES",
    "MAX_QUEUE_BYTES",
    "MAX_QUEUE_ITEMS",
    "MAX_REFUSALS",
    "MAX_STRING",
    "MAX_SUMMARY_SEGMENTS",
    "MAX_TEXT",
    "MAX_VALUES",
    "MAX_VIEWS",
    "MEMBER_BYTES",
    "MIN_LOG_BYTES",
    "MIN_QUERY_MEMORY",
    "NAME_CHARACTERS",
    "NESTING_DEPTH",
    "NOTE_CHARACTERS",
    "OPEN_PROPOSALS",
    "OPERATOR_REQUESTS",
    "PACKS",
    "PACK_LEAF_STEPS",
    "PACK_LEAVES",
    "PAGE_REQUESTS",
    "PARAMETERS",
    "PATH_SEARCH",
    "PATH_STEPS",
    "POINTER_CHARACTERS",
    "PREDICATES",
    "PROPOSAL_BYTES",
    "PROPOSAL_REQUESTS",
    "QUERY_ANSWER_BYTES",
    "QUERY_MEMORY",
    "QUERY_SECONDS",
    "QUERY_WORKERS",
    "QUEUE_BYTES",
    "QUEUE_ITEMS",
    "RATIO_FLOOR",
    "READER_MEMORY",
    "READER_SECONDS",
    "READER_WORKERS",
    "REFERENCE_CHARACTERS",
    "REFUSALS",
    "REQUEST_BYTES",
    "SCOPE_COLUMNS",
    "STRING_CHARACTERS",
    "SUBSTITUTED_BYTES",
    "TABLE_COLUMNS",
    "TEXT_CHARACTERS",
    "TOKEN_FAILURES",
    "TOOL_BODY_IDLE_SECONDS",
    "TOOL_CALLS",
    "TOOL_SECONDS",
    "VIEWS",
    "ImportLimits",
    "LimitName",
    "LogLimits",
    "QueryLimits",
    "map_cap",
]
