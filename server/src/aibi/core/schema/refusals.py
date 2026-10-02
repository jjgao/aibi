"""Refusals (SPEC §8.6).

A refusal names the problem, where it is and what is available instead (A3). Its messages are
segments (SPEC §8.1). A refusal never writes a curator token or a session handle (D265):
``blank_secrets`` blanks anything of their shapes (``SECRET_RE``) that a refusal took from a
request, such as a member name, even inside a longer word, and each word that holds one once
percent-decoded. Input is refused only for a shape that stands alone (``SECRET_ALONE_RE``,
``holds_secret``), so that an ordinary long name that happens to hold one, such as ``courses_``
and 43 more letters, can be stored and named; and for the configured curator token wherever it
sits (``holds_token_of``), which only the token itself matches.
"""

import hashlib
import hmac
import re
import urllib.parse
from collections.abc import Iterator
from enum import StrEnum
from typing import Annotated

from pydantic import Field

from aibi.core.schema.ids import JsonPointer, PackCode
from aibi.core.schema.limits import MAX_REFUSALS, REFUSALS
from aibi.core.schema.output import DATA_MARK, LAX, Count, DataSegment, Output, Segment, data, text
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
    # The release lifecycle, curation sessions and the proposal queue (§12.3, D236–D252):
    DATASET_BUSY = "DATASET_BUSY"
    """Another operation runs on the dataset, or a curation session is open on it (D236)."""
    DATASET_EXISTS = "DATASET_EXISTS"
    """An import of a dataset that has a published release: that is a re-import (D237)."""
    CONFLICT = "CONFLICT"
    """A session handle that is not the current one, a draft that is not at the state expected,
    or a base that is no longer the latest published release (§12.3, D244)."""
    NO_SESSION = "NO_SESSION"
    """No curation session is open on the dataset."""
    NO_CHANGE = "NO_CHANGE"
    """A publish or re-import whose manifest equals the latest published release's (§12.3)."""
    UNKNOWN_PROPOSAL = "UNKNOWN_PROPOSAL"
    """No open proposal of the dataset has that id."""
    INVALID_EXTENSION = "INVALID_EXTENSION"
    """An extension object its pack's JSON Schema refuses, or has none for (§10.1, D247)."""
    # Request protection and the operator router over HTTP (§11.2, §14, D255–D265):
    HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
    """A request whose ``Host`` is missing, repeated, malformed or not allowed (D256)."""
    ORIGIN_NOT_ALLOWED = "ORIGIN_NOT_ALLOWED"
    """A request from an origin that is not allowed, or a cross-site one without an allowed
    ``Origin``, or a CORS preflight that is not answered (D257, D258)."""
    TOKEN_REQUIRED = "TOKEN_REQUIRED"
    """An operator request without the curator token as a bearer token (D261)."""
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"
    """An operator request that does not name its operator in one valid header (D262)."""
    CSRF_REQUIRED = "CSRF_REQUIRED"
    """A browser's operator request without the CSRF token (D263)."""
    NOT_FOUND = "NOT_FOUND"
    """No route, or nothing at the route, has that path."""
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    """The route does not take that method; the alternatives list those it takes."""
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    """A request body of a content type the route does not read (D263)."""
    LENGTH_REQUIRED = "LENGTH_REQUIRED"
    """An upload that does not declare its length in one ``Content-Length``, or that
    ``Transfer-Encoding`` frames (D266)."""
    INTERNAL_ERROR = "INTERNAL_ERROR"
    """The server failed; the refusal says no more, and the server logs the rest (D265)."""


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


SECRET_RE = re.compile(r"(?:aibi|ses)_[A-Za-z0-9_-]{43}")
"""A curator token (``aibi_``, D261) or a session handle (``ses_``, D267), anywhere in a text,
inside a longer word too: what logs, refusals and usage errors blank."""
_BASE64URL = "A-Za-z0-9_-"
SECRET_ALONE_RE = re.compile(
    rf"(?<![{_BASE64URL}])(?:aibi|ses)_[{_BASE64URL}]{{43}}(?![{_BASE64URL}])"
)
"""A token or a handle that stands alone: no base64url character touches either end, as none
does where a secret is pasted or swapped in. What input is refused for (D262, D265, D268)."""
SECRET_BLANK = "<secret>"
"""What stands for a secret's shape in a refusal, a log line or a usage error."""
BIDI_FORMATTING = frozenset(
    chr(point) for point in (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
"""The bidi formatting characters: the Arabic letter mark, the marks, the embeddings and
overrides, and the isolates, which a self-declared name may not hold (D262)."""
SECRET_DECODINGS = 3
"""How many times a text is percent-decoded when a secret is looked for in it: a server decodes
a URL once, and a proxy, a log or a client may once more."""


def _decodings(value: str) -> Iterator[str]:
    """``value``, then percent-decoded, up to ``SECRET_DECODINGS`` times, while that changes it."""
    yield value
    for _ in range(SECRET_DECODINGS):
        decoded = urllib.parse.unquote(value)
        if decoded == value:
            return
        value = decoded
        yield value


def holds_secret(value: str) -> bool:
    """Whether ``value`` holds a token's or a handle's shape standing alone
    (``SECRET_ALONE_RE``), as written or percent-decoded up to ``SECRET_DECODINGS`` times."""
    return any(SECRET_ALONE_RE.search(found) is not None for found in _decodings(value))


_TOKEN_WINDOW = re.compile(r"(?=(aibi_[A-Za-z0-9_-]{43}))")
"""Every ``aibi_`` window of a token's length, overlapping ones too."""


def holds_token_of(value: str, digest: bytes) -> bool:
    """Whether ``value`` holds the curator token whose SHA-256 is ``digest`` anywhere, inside a
    longer word too, as written or percent-decoded up to ``SECRET_DECODINGS`` times (D261,
    D265): each ``aibi_`` window of 48 characters is hashed and compared in constant time, so
    the real token touching ``_`` or ``-`` is found while a name that only holds its shape is
    not."""
    for found in _decodings(value):
        for window in _TOKEN_WINDOW.finditer(found):
            hashed = hashlib.sha256(window.group(1).encode("ascii")).digest()
            if hmac.compare_digest(hashed, digest):
                return True
    return False


_WORD = re.compile(r"[^\s/]+")
"""A word of a text, or a segment of a pointer: what is blanked whole when it holds a secret's
shape only once percent-decoded."""


def _encoded_secret(word: re.Match[str]) -> str:
    found = any(SECRET_RE.search(decoded) is not None for decoded in _decodings(word.group()))
    return SECRET_BLANK if found else word.group()


def blank(value: str) -> str:
    """``value`` with every curator token or session handle shape in it blanked, and each word
    (or pointer segment) that holds one percent-decoded up to ``SECRET_DECODINGS`` times written
    ``SECRET_BLANK`` whole, as ``blank_path`` blanks a logged path's segment."""
    blanked = SECRET_RE.sub(SECRET_BLANK, value)
    return _WORD.sub(_encoded_secret, blanked) if "%" in blanked else blanked


def _blank_segment(segment: Segment) -> Segment:
    if isinstance(segment, DataSegment):
        blanked = blank(segment.data)
        return segment if blanked == segment.data else data(blanked)
    blanked = blank(segment.text)
    return segment if blanked == segment.text else text(blanked)


def blank_secrets(refusal: Refusal) -> Refusal:
    """``refusal`` with every token or handle shape in its path, message and alternatives
    blanked (D265): a member name or a value it took from a request is not written back."""
    path = None if refusal.path is None else blank(refusal.path)
    message = [_blank_segment(segment) for segment in refusal.message]
    alternatives = [_blank_segment(segment) for segment in refusal.alternatives]
    if path == refusal.path and message == refusal.message and alternatives == refusal.alternatives:
        return refusal
    return refusal.model_copy(
        update={"path": path, "message": message, "alternatives": alternatives}
    )


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
