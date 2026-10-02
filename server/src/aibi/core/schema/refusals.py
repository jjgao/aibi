"""Refusals (SPEC §8.6).

A refusal names the problem, where it is and what is available instead (A3). Its messages are
segments (SPEC §8.1).
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from aibi.core.schema.ids import JsonPointer, PackCode
from aibi.core.schema.limits import MAX_REFUSALS, REFUSALS
from aibi.core.schema.output import DATA_MARK, LAX, Count, Output, Segment, text
from aibi.core.schema.results import CohortCount


class RefusalCode(StrEnum):
    """Stable refusal codes of the core. Pack codes are namespaced and not listed here."""

    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    INTEGER_OUT_OF_RANGE = "INTEGER_OUT_OF_RANGE"
    NULL_NOT_ALLOWED = "NULL_NOT_ALLOWED"
    MISSING_MEMBER = "MISSING_MEMBER"
    UNKNOWN_MEMBER = "UNKNOWN_MEMBER"
    WRONG_TYPE = "WRONG_TYPE"
    INVALID_VALUE = "INVALID_VALUE"
    UNKNOWN_KIND = "UNKNOWN_KIND"
    CONFLICTING_MEMBERS = "CONFLICTING_MEMBERS"
    UNKNOWN_PARAMETER = "UNKNOWN_PARAMETER"
    INVALID_PARAMETER_REFERENCE = "INVALID_PARAMETER_REFERENCE"
    UNKNOWN_COHORT = "UNKNOWN_COHORT"
    COHORT_CYCLE = "COHORT_CYCLE"
    COHORT_MISMATCH = "COHORT_MISMATCH"
    LEAF_NOT_ALLOWED = "LEAF_NOT_ALLOWED"
    DATASET_MISSING = "DATASET_MISSING"
    DUPLICATE_ENTRY = "DUPLICATE_ENTRY"
    REFERENCE_NOT_IN_VIEW = "REFERENCE_NOT_IN_VIEW"
    CONCEPT_REQUIRED = "CONCEPT_REQUIRED"
    CROSS_DATASET_ONLY = "CROSS_DATASET_ONLY"
    UNKNOWN_DATASET = "UNKNOWN_DATASET"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    UNKNOWN_DESCRIPTOR = "UNKNOWN_DESCRIPTOR"
    """A descriptor refers to one the release does not hold (§13.2)."""
    # Resolving a document against its releases (§6, §7.2, §7.6):
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    UNKNOWN_RELATIONSHIP = "UNKNOWN_RELATIONSHIP"
    INVALID_UNIT = "INVALID_UNIT"
    """The unit is not a keyed table of the table graph (§5.3)."""
    INVALID_PATH = "INVALID_PATH"
    """A ``via`` whose steps do not connect, or a path of the wrong shape for its leaf."""
    NO_PATH = "NO_PATH"
    AMBIGUOUS_PATH = "AMBIGUOUS_PATH"
    QUANTIFIER_MISMATCH = "QUANTIFIER_MISMATCH"
    EXCLUDE_SELF_NOT_ALLOWED = "EXCLUDE_SELF_NOT_ALLOWED"
    UNDECLARED_DATATYPE = "UNDECLARED_DATATYPE"
    """A predicate on a column whose datatype nobody declared: its constants cannot be typed."""
    INVALID_CONSTANT = "INVALID_CONSTANT"
    NOT_PERMISSIBLE = "NOT_PERMISSIBLE"
    UNITS_UNCONVERTIBLE = "UNITS_UNCONVERTIBLE"
    RANGE_NOT_ALLOWED = "RANGE_NOT_ALLOWED"
    MEMBER_NOT_APPLICABLE = "MEMBER_NOT_APPLICABLE"
    """A member that does not apply to its column: ``units`` or ``match``."""
    SCOPE_COLUMN_MENTION = "SCOPE_COLUMN_MENTION"
    FILTER_COLUMN_MENTION = "FILTER_COLUMN_MENTION"
    UNKNOWN_SCOPE_COLUMN = "UNKNOWN_SCOPE_COLUMN"
    ROW_IDS_NOT_ALLOWED = "ROW_IDS_NOT_ALLOWED"
    INVALID_KEY = "INVALID_KEY"
    MIXED_RELEASES = "MIXED_RELEASES"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    """Not supported until a later milestone, which the message names."""
    # Building a release from its raw snapshots (§12.2, §13.2):
    UNPARSEABLE_SOURCE = "UNPARSEABLE_SOURCE"
    """A raw snapshot its parse settings cannot read; the message names the line."""
    COLUMNS_CHANGED = "COLUMNS_CHANGED"
    """A change to a table's source columns, which only a re-import makes (§12.2, §12.3)."""
    UNKNOWN_RELEASE = "UNKNOWN_RELEASE"
    """No release of the dataset has that label, hash or draft."""
    RELEASE_WITHDRAWN = "RELEASE_WITHDRAWN"
    """The release was withdrawn: it is never published or withdrawn again (§12.3)."""
    ERASURE_BLOCKED = "ERASURE_BLOCKED"
    """An erasure whose first step is missing: the latest release still holds the rows."""
    # The validation gate's structural checks (§13.2, D230):
    KEY_NULL = "KEY_NULL"
    """A primary-key cell that is not PRESENT."""
    KEY_NOT_UNIQUE = "KEY_NOT_UNIQUE"
    """Two rows with the same key, or the same parent columns of a relationship."""
    CARDINALITY_VIOLATED = "CARDINALITY_VIOLATED"
    """Child rows of a one-to-one relationship that share their parent."""
    DANGLING_REFERENCE = "DANGLING_REFERENCE"
    """A foreign key, every cell PRESENT, that no parent row has (§5.5)."""
    COVERAGE_NULL = "COVERAGE_NULL"
    """A cell that is not PRESENT in a column a coverage names (§5.6)."""
    COVERAGE_UNKNOWN = "COVERAGE_UNKNOWN"
    """A coverage or assignment row naming a parent or group that does not exist (§5.6)."""
    OUTSIDE_RECORD_FILTER = "OUTSIDE_RECORD_FILTER"
    """A PRESENT value outside its relationship's ``record_filter`` (§5.6)."""
    NOT_APPLICABLE_IN_FILTER = "NOT_APPLICABLE_IN_FILTER"
    """A NOT_APPLICABLE cell in a column a ``record_filter`` names (§5.6)."""
    # Importing files (§13.1, §14, D224–D235):
    PATH_NOT_CONFINED = "PATH_NOT_CONFINED"
    """A path that is not an absolute path inside the upload area or an import directory."""
    ARCHIVE_REFUSED = "ARCHIVE_REFUSED"
    """An archive entry the import refuses: a link, an absolute or ``..`` name, encryption."""
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    """A file or column type no importer reads; the alternatives list those it reads."""
    EMPTY_SOURCE = "EMPTY_SOURCE"
    """A source in which no table was found."""


class Limit(Output):
    """The limit a refusal hit: one of the names in ``aibi.core.schema.limits``, or a pack's."""

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    max: Count


class Refusal(Output):
    """A structured error (SPEC §8.6).

    ``path`` is a JSON Pointer into the document as written, or ``None``.
    """

    code: Annotated[RefusalCode, LAX] | PackCode
    path: Annotated[JsonPointer, Field(json_schema_extra=DATA_MARK)] | None
    """Its tokens are the document's own keys, so it is data (A6)."""
    message: list[Segment]
    alternatives: list[Segment] = Field(default_factory=list[Segment])
    limit: Limit | None = None
    counts: list[CohortCount] | None = None
    """Cohort counts, with their ids, where a refusal reports numbers (e.g. an overlap, §7.4)."""


def sort_refusals(refusals: list[Refusal]) -> list[Refusal]:
    """Refusals in the order ``validate_document`` returns them: by path, then code (§8.6)."""
    return sorted(refusals, key=lambda refusal: (refusal.path or "", refusal.code))


def finish_refusals(refusals: list[Refusal]) -> list[Refusal]:
    """Refusals as they are returned (§8.6): the first of each (path, code), sorted, and at most
    ``MAX_REFUSALS``, then one that says how many more were found."""
    kept: dict[tuple[str | None, str], Refusal] = {}
    for refusal in refusals:
        kept.setdefault((refusal.path, str(refusal.code)), refusal)
    ordered = sort_refusals(list(kept.values()))
    if len(ordered) <= MAX_REFUSALS:
        return ordered
    return [
        *ordered[:MAX_REFUSALS],
        Refusal(
            code=RefusalCode.LIMIT_EXCEEDED,
            path=None,
            message=[text(f"{len(ordered) - MAX_REFUSALS} more refusals were left out")],
            limit=Limit(name=REFUSALS, max=MAX_REFUSALS),
        ),
    ]
