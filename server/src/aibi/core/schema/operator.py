"""What the operator router and the server's own routes exchange (SPEC §11.2, §14, D260–D269).

**Requests** are strict, closed and frozen, as ``curation``'s are: a member a model does not name
is refused (``UNKNOWN_MEMBER``), and every list and string is bounded. A body is read by
``loading.load_request``, the bounded JSON parser of documents followed by these models, never by
the web framework, so duplicate keys, nesting and value counts are refused as in documents
(D260). Every union is chosen by a function, so that a refusal names the member a client gave
rather than the union's alternatives one by one.

**Secrets** never travel in a URL: a session handle is in bodies only (``SessionChange``,
``SessionEnd`` and ``SessionOpened``), and an erasure key in ``EraseRequest`` only; neither is
shown by a ``repr``, and no validation error of these models quotes its input (D267, D269).
``by`` is never part of a request: the server attributes every operator request to the operator
its header names (§5.1, D262). Nor does a request store a secret: text the server keeps from a
request (an import's ``name`` and ``original_name``, and a change's values, evidence and whole
descriptors put, member names included) that holds a token's or a handle's shape standing alone
(``SECRET_ALONE_RE``), as written or percent-decoded, or the configured curator token anywhere
(``holds_token_of``), is refused (``stored_secrets``, ``INVALID_VALUE``), since it would otherwise
be published in a release that every reader sees (D262, D265); a longer word that holds the shape,
such as an ordinary long column name, is kept.

**Outputs** are ``Output``s (§8.1). A dataset's state shows its open session's id, base, draft
and opener, never its handle.
"""

from collections.abc import Iterator
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    Tag,
)

from aibi.core.schema.catalog import ProposeDescriptor
from aibi.core.schema.cohorts import (
    ENGINE_RE,
    TIME_RE,
    CountCohort,
    DataObject,
    RecordJson,
)
from aibi.core.schema.curation import ChangeRequest, DescriptorId, QueueNote, QueueText
from aibi.core.schema.descriptors import (
    By,
    CurationPointer,
    DataScalar,
    Label,
    PositiveInt,
    String,
)
from aibi.core.schema.ids import (
    MAX_COLUMNS,
    DatasetId,
    DerivationId,
    Identifier,
    IssuanceId,
    PackId,
    Sha256,
    TableId,
)
from aibi.core.schema.jsonio import escape_token
from aibi.core.schema.limits import KEY_COLUMNS, MAX_STRING, STRING_CHARACTERS, LimitName
from aibi.core.schema.output import DATA_MARK, Count, FiniteJsonObject, Output, text
from aibi.core.schema.refusals import (
    SECRET_BLANK,
    Refusal,
    RefusalCode,
    blank_secrets,
    finish_refusals,
    holds_secret,
    holds_token_of,
)

HANDLE_RE = r"^ses_[A-Za-z0-9_-]{43}$"
"""A session handle (D244): ``ses_`` and 43 base64url characters."""
UPLOAD_EXTENSIONS = ("csv", "tsv", "txt", "xlsx", "xlsm", "ods", "parquet", "zip")
"""The extensions of the upload area (D234): those the file importer reads."""
UPLOAD_NAME_RE = rf"^[0-9a-f]{{64}}\.(?:{'|'.join(UPLOAD_EXTENSIONS)})$"
"""An upload's name in the upload area: its SHA-256 in hexadecimal and its extension."""

_HIDDEN = ConfigDict(hide_input_in_errors=True)


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


Handle = Annotated[str, StringConstraints(pattern=HANDLE_RE)]
"""A session handle as a request carries it; the store decides whether it is the current one
(``CONFLICT``)."""
UploadName = Annotated[str, StringConstraints(pattern=UPLOAD_NAME_RE)]


class PathSource(_Request):
    """An absolute path on the server, inside the upload area or an import directory (D232)."""

    path: Annotated[
        str,
        StringConstraints(min_length=1, max_length=MAX_STRING),
        LimitName(STRING_CHARACTERS),
    ]


class UploadSource(_Request):
    """An upload of the same dataset, by its name in the upload area (D234)."""

    upload: UploadName


class ConnectionSource(_Request):
    """A named connection of the server's configuration, by its name (§14, D305): never a
    host, a path or a credential."""

    connection: Identifier


_SOURCES = {"path": PathSource, "upload": UploadSource, "connection": ConnectionSource}


def _source(value: object) -> str | None:
    if isinstance(value, dict):
        given = [tag for tag in _SOURCES if tag in cast(dict[str, object], value)]
        return given[0] if len(given) == 1 else None
    return next((tag for tag, model in _SOURCES.items() if isinstance(value, model)), None)


Source = Annotated[
    Annotated[PathSource, Tag("path")]
    | Annotated[UploadSource, Tag("upload")]
    | Annotated[ConnectionSource, Tag("connection")],
    Discriminator(
        _source,
        custom_error_type="unknown_kind",
        custom_error_message=(
            "A source is a path on the server (path), an upload (upload) or a named connection "
            "(connection)"
        ),
    ),
]


class ImportRequest(_Request):
    """An import or a re-import (D266). ``original_name`` is an upload's file name, which names
    the dataset and its tables (D226); ``pack`` names the pack whose importer reads the source,
    never a named connection's (D305). The limits are the server's, never a request's (D253)."""

    source: Source
    name: Label | None = None
    original_name: String | None = None
    pack: PackId | None = None


def _release(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return "label"
    return "manifest" if isinstance(value, str) else None


ReleaseRef = Annotated[
    Annotated[PositiveInt, Tag("label")] | Annotated[Sha256, Tag("manifest")],
    Discriminator(
        _release,
        custom_error_type="release_type",
        custom_error_message="A release is a label, a positive integer, or a manifest hash",
    ),
]


class WithdrawRequest(_Request):
    release: ReleaseRef


class EraseRequest(_Request):
    """An erasure (D223, D269): the person's key in ``table``, its values in the key's order."""

    table: TableId
    key: Annotated[
        list[DataScalar], Field(min_length=1, max_length=MAX_COLUMNS), LimitName(KEY_COLUMNS)
    ] = Field(repr=False)
    redact_only: StrictBool = False


class Empty(_Request):
    """A body with nothing to say: ``{}``."""


class RejectProposals(_Request):
    """A rejection of every open proposal of one ``proposer``, or of every proposer of a
    ``kind``: exactly one of the two (D277)."""

    proposer: By | None = None
    kind: Literal["agent", "model", "importer"] | None = None


class IssuanceRequest(_Request):
    """An issuance of the derivation log, by its id, which the body carries so that no URL,
    and so no access log, holds it (D302)."""

    id: IssuanceId


class PruneRequest(_Request):
    """A pruning of every issuance recorded before ``before``, an RFC 3339 time with its offset;
    without it, of those of counts and of results before their configured periods
    (``keep_count_issuances_days``, ``keep_result_issuances_days``; D300, D318)."""

    before: Annotated[str, StringConstraints(max_length=64)] | None = None


class SessionChange(ChangeRequest):
    """A change to the draft (D245), with the session's handle and the draft it expects."""

    model_config = _HIDDEN

    handle: Handle = Field(repr=False)
    expected: Sha256


class SessionEnd(_Request):
    """A publish or a discard, with the session's handle and the draft it expects."""

    handle: Handle = Field(repr=False)
    expected: Sha256


_STORED_MESSAGE = (
    "Text the server keeps holds no token or handle, as written or percent-decoded; the value "
    "is not repeated here"
)


def _secret_pointers(value: JsonValue, at: str, digest: bytes | None) -> Iterator[str]:
    if isinstance(value, str):
        if _holds(value, digest):
            yield at
    elif isinstance(value, list):
        for index, member in enumerate(value):
            yield from _secret_pointers(member, f"{at}/{index}", digest)
    elif isinstance(value, dict):
        for key, member in value.items():
            secret = _holds(key, digest)
            where = f"{at}/{SECRET_BLANK if secret else escape_token(key)}"
            if secret:
                yield where
            yield from _secret_pointers(member, where, digest)


def _holds(value: str, digest: bytes | None) -> bool:
    return holds_secret(value) or (digest is not None and holds_token_of(value, digest))


def _stored(request: BaseModel) -> Iterator[tuple[str, JsonValue]]:
    """The text a request gives that the server keeps, by its pointer: an import's names, a
    change's values, evidence and whole descriptors put, a proposal's value and evidence, and the
    document a count records in the derivation log (D300)."""
    if isinstance(request, CountCohort):
        yield "/document", cast(JsonValue, dict(request.document))
    elif isinstance(request, ImportRequest):
        for member in ("name", "original_name"):
            given = cast(str | None, getattr(request, member))
            if given is not None:
                yield f"/{member}", given
    elif isinstance(request, ProposeDescriptor):
        written = cast(dict[str, JsonValue], request.model_dump(mode="json", exclude_unset=True))
        for member in ("value", "evidence"):
            if member in written:
                yield f"/{member}", written[member]
    elif isinstance(request, SessionChange):
        for index, edit in enumerate(request.edits):
            written = cast(dict[str, JsonValue], edit.model_dump(mode="json", exclude_unset=True))
            kept = ["value", "evidence", *(["descriptor"] if written.get("op") == "put" else [])]
            for member in kept:
                if member in written:
                    yield f"/edits/{index}/{member}", written[member]


def stored_secrets(request: BaseModel, digest: bytes | None = None) -> list[Refusal]:
    """A refusal (``INVALID_VALUE``) at each string or member name of the text a request gives
    that the server keeps, that holds a token's or a handle's shape standing alone, as written
    or percent-decoded (``holds_secret``, D262, D265), or, given the configured hash's
    ``digest``, the curator token anywhere (``holds_token_of``); a member name so refused is written
    ``<secret>`` in the path, and no value is quoted. Pointers and ids, which name what exists,
    are the services' to refuse."""
    found = [
        Refusal(code=RefusalCode.INVALID_VALUE, path=where, message=[text(_STORED_MESSAGE)])
        for at, value in _stored(request)
        for where in _secret_pointers(value, at, digest)
    ]
    return [blank_secrets(refusal) for refusal in finish_refusals(found)]


# --- Outputs ---------------------------------------------------------------------------------


class Health(Output):
    status: Literal["ok"]


class Csrf(Output):
    """The CSRF token a browser's operator requests carry (D263)."""

    csrf: str


class LabelState(Output):
    label: PositiveInt
    manifest: Sha256
    status: Literal["published", "withdrawn"]


class OpenSession(Output):
    """A dataset's open curation session, without its handle."""

    session: PositiveInt
    base: Sha256
    draft: Sha256
    opened_by: By


class DatasetState(Output):
    dataset: DatasetId
    latest: PositiveInt | None
    """The latest published release; ``null`` when every release was withdrawn."""
    labels: list[LabelState]
    session: OpenSession | None


class Datasets(Output):
    datasets: list[DatasetState]


class Uploaded(Output):
    dataset: DatasetId
    upload: UploadName
    """What an import names as its ``upload`` source."""
    bytes: Count


class ChangeNote(Output):
    """What a re-import changed (D239), as its notes describe it (D242)."""

    descriptor: DescriptorId
    pointer: CurationPointer
    """``""`` for a whole descriptor."""
    happened: Literal["proposed", "removed", "returned", "added", "gone", "dropped"]


class SkippedProposal(Output):
    """A pack's proposal that was not recorded, or a proposer that failed (no ``descriptor``),
    with the codes of why (D249)."""

    pack: PackId
    descriptor: QueueText | None = None
    pointer: QueueText | None = None
    codes: list[str]


class ProposersRan(Output):
    proposals: list[PositiveInt]
    """The proposals recorded, or found open already."""
    skipped: list[SkippedProposal]


class ImportPublished(Output):
    dataset: DatasetId
    label: PositiveInt
    manifest: Sha256
    notes: list[QueueNote]
    """The import report's notes (D231)."""
    changes: list[ChangeNote]
    """What a re-import changed; empty for an import."""
    proposers: ProposersRan | None = None


class Withdrawn(Output):
    dataset: DatasetId
    labels: list[PositiveInt]


class ErasedOut(Output):
    """An erasure done (D223): no key, only what was withdrawn and counted."""

    dataset: DatasetId
    withdrawn: list[PositiveInt]
    terms: Count
    redacted: bool
    """Whether the app DB was redacted and checkpointed now, rather than once no pin holds it."""
    uploads_pending: bool


class SessionOpened(Output):
    """A session opened or taken over: its handle, returned this once (D244, D267)."""

    model_config = _HIDDEN

    dataset: DatasetId
    session: PositiveInt
    base: Sha256
    draft: Sha256
    handle: Handle = Field(repr=False)


class DraftChanged(Output):
    dataset: DatasetId
    draft: Sha256


class SessionPublishedOut(Output):
    dataset: DatasetId
    label: PositiveInt
    proposers: ProposersRan | None = None


class SessionEnded(Output):
    dataset: DatasetId
    outcome: Literal["discarded"]


class Rejected(Output):
    dataset: DatasetId
    proposal: PositiveInt


class ProposalsRejected(Output):
    """What a rejection of a proposer's open proposals did: those rejected, and those kept
    because the open draft accepted and holds them (D277)."""

    dataset: DatasetId
    rejected: Count
    kept: Count


class DescriptorShown(Output):
    """One descriptor of a release or of the draft, as stored: data (A6)."""

    dataset: DatasetId
    release: Sha256
    label: PositiveInt | Literal["draft"]
    descriptor: Annotated[FiniteJsonObject, Field(json_schema_extra=DATA_MARK)]


class Pruned(Output):
    """What a pruning of the derivation log did (§12.2, D300, D318): the issuances it removed,
    the time before which it removed those of counts (``before``) and those of results
    (``results_before``), each absent when they are kept until an operator prunes, and the
    bytes the log holds now (``log_bytes``)."""

    pruned: Count
    before: Annotated[str, StringConstraints(pattern=TIME_RE)] | None = None
    log_bytes: Count
    results_before: Annotated[str, StringConstraints(pattern=TIME_RE)] | None = None


class LoggedIssuance(Output):
    """One issuance as the log holds it, its request included: the document as written and the
    parameters used, which ``explain`` never gives (D302). Everything from a document is data
    (A6)."""

    id: IssuanceId
    derivation: DerivationId
    tool: Literal["count_cohort", "run_analysis"]
    document: RecordJson
    params: RecordJson
    sql: RecordJson | None = None
    values_from: IssuanceId
    engine: Annotated[str, StringConstraints(pattern=ENGINE_RE)]
    packs: DataObject
    at: Annotated[str, StringConstraints(pattern=TIME_RE)]


class Refusals(Output):
    """Every error response over HTTP (§8.6, D265)."""

    refusals: Annotated[list[Refusal], Field(min_length=1)]


__all__ = [
    "HANDLE_RE",
    "UPLOAD_EXTENSIONS",
    "UPLOAD_NAME_RE",
    "ChangeNote",
    "ConnectionSource",
    "Csrf",
    "DatasetState",
    "Datasets",
    "DescriptorShown",
    "DraftChanged",
    "Empty",
    "EraseRequest",
    "ErasedOut",
    "Handle",
    "Health",
    "ImportPublished",
    "ImportRequest",
    "IssuanceRequest",
    "LabelState",
    "LoggedIssuance",
    "OpenSession",
    "PathSource",
    "ProposalsRejected",
    "ProposersRan",
    "PruneRequest",
    "Pruned",
    "Refusals",
    "RejectProposals",
    "Rejected",
    "ReleaseRef",
    "SessionChange",
    "SessionEnd",
    "SessionEnded",
    "SessionOpened",
    "SessionPublishedOut",
    "SkippedProposal",
    "Source",
    "UploadSource",
    "Uploaded",
    "WithdrawRequest",
    "Withdrawn",
    "stored_secrets",
]
