"""The requests and outputs of the query tools: ``validate_document``, ``count_cohort`` and
``explain`` (M2; SPEC §7.7, §8.1, §11.1; D299–D302), and ``run_analysis`` (M3, D318).

A request carries the document as written (``document``), which the server loads as §7.1 says:
its refusals point into that document, not into the request (§8.6, D299). ``validate_document``
also takes a ``format``, ``<pack id>.<name>``, whose pack's translator makes an aibi document of
it first (§10.1, D303).

Every string of an output that comes from a document or from data is marked as data (A6): cohort
names, analysis ids as a document names them, readbacks' data tokens, the documents themselves
and the log's records of them.
"""

import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    WithJsonSchema,
)
from pydantic_core import PydanticCustomError

from aibi.core.schema.caveats import Caveat
from aibi.core.schema.document import PackKey
from aibi.core.schema.ids import (
    DERIVATION_ID_RE,
    ISSUANCE_ID_RE,
    DerivationId,
    IssuanceId,
    JsonPointer,
    LeafKey,
    TableId,
)
from aibi.core.schema.jsonio import JsonError, json_value
from aibi.core.schema.output import (
    DATA_MARK,
    OUTPUT_JSON_MARK,
    Count,
    Data,
    FiniteJsonObject,
    Output,
    Segment,
)
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.results import (
    AnalysisRef,
    CohortCount,
    Disclosure,
    PackVersion,
    ReleaseRef,
    ResultEnvelope,
)

DOCUMENT_MARK = "x-aibi-document"
"""Marks a request's ``document`` in its schema; the export puts the schema of a document as
written there (§11.1)."""
FORMAT_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}\.[a-z][a-z0-9_]{0,63}$")
TIME_RE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$"
ENGINE_RE = r"^aibi [0-9A-Za-z.+!_-]{1,64}$"


def _finite(value: JsonValue) -> JsonValue:
    try:
        return json_value(value)
    except JsonError as error:
        raise PydanticCustomError(
            "output_json",
            "The value holds a number that is not finite or lies beyond ±(2^53 - 1), text that "
            "is not Unicode, or nesting deeper than 64 (SPEC §8.2)",
            {"code": error.code},
        ) from None


RecordJson = Annotated[
    JsonValue,
    AfterValidator(_finite),
    WithJsonSchema({OUTPUT_JSON_MARK: True, **DATA_MARK}),
]
"""A JSON value the derivation log recorded from a document, a client's parameters or the SQL as
run: data (A6)."""
DataObject = Annotated[
    FiniteJsonObject,
    WithJsonSchema(
        {"type": "object", "additionalProperties": {OUTPUT_JSON_MARK: True}, **DATA_MARK}
    ),
]
"""A JSON object from a document: data (A6)."""


def _format(value: str) -> str:
    if "__" in value:
        raise PydanticCustomError("invalid_format", "A format's names hold no __")
    return value


FormatName = Annotated[
    str,
    StringConstraints(pattern=FORMAT_RE.pattern),
    AfterValidator(_format),
    Field(json_schema_extra={"not": {"pattern": "__"}}),
]
"""A document format, ``<pack id>.<name>`` (§10.1)."""


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


RequestDocument = Annotated[FiniteJsonObject, Field(json_schema_extra={DOCUMENT_MARK: True})]
"""The document as written (§7.1), loaded by the server after the request is read."""


class ValidateDocument(_Request):
    document: FiniteJsonObject
    """The document as written; in another format when ``format`` names it."""
    format: FormatName | None = None


class CountCohort(_Request):
    document: RequestDocument


class Explain(_Request):
    id: Annotated[
        str,
        StringConstraints(pattern=f"(?:{DERIVATION_ID_RE.pattern})|(?:{ISSUANCE_ID_RE.pattern})"),
    ]
    """A derivation id (``drv:…``) or an issuance id (``iss:…``)."""


# --- Outputs -------------------------------------------------------------------------------------


class RunAnalysis(_Request):
    document: RequestDocument


class Parameters(Output):
    """The parameters the document used, echoed (outside every digest), and those it declared
    but did not use (§7.1)."""

    used: DataObject
    unused: list[Data]


class TranslationNote(Output):
    """A place where a translation changed meaning or could not be exact (§7.1, D303)."""

    pointer: Annotated[JsonPointer, Field(json_schema_extra=DATA_MARK)]
    """A JSON Pointer into the document given, as written in its format; or, for a note of the
    core's about a ``not``, into the document translated (``translated``)."""
    translated: StrictBool
    message: list[Segment]


class Translation(Output):
    format: FormatName
    document: DataObject
    """The aibi document the translator made: the refusals point into it."""
    notes: list[TranslationNote]


IdStatus = Literal["issued", "not_issued", "withdrawn", "discarded", "unknown_release", "erased"]


class CohortCheck(Output):
    """A cohort of a document validated (§11.1, D299): its canonical form, its ids as the log
    knows them, its readback and the caveats of its count that need no data."""

    cohort: Data
    id: DerivationId
    computation_id: DerivationId
    status: IdStatus
    """What the derivation log says of the id: ``not_issued`` until ``count_cohort`` issues it."""
    form: DataObject
    """The canonical form: each dataset's manifest hash, and its clause tree (§7.6)."""
    unit: TableId
    release: ReleaseRef
    disclosure: Disclosure
    packs: dict[PackKey, PackVersion]
    leaves: Annotated[
        dict[Annotated[JsonPointer, Field(min_length=1)], list[LeafKey]],
        Field(json_schema_extra=DATA_MARK),
    ]
    """Each leaf as written, by its pointer, and the keys of the top-level clauses it became part
    of (§6.6, §8.1)."""
    readback: list[Segment]
    caveats: list[Caveat]
    """The caveats its count will carry that need no data, as the count carries them."""


class ViewCheck(Output):
    """A view that checked against its analysis (§7.4, §7.6, D317): its canonical form, its ids
    as the log knows them, its readback and the caveats of its result that need no data."""

    position: Count
    """Its index in the document's ``views``."""
    analysis: AnalysisRef
    id: DerivationId
    computation_id: DerivationId
    status: IdStatus
    """What the derivation log says of the result id: ``not_issued`` until ``run_analysis``
    issues it."""
    form: DataObject
    """The canonical view: its analysis, its cohorts' ids in view order, its reference and
    overlap where its analysis has them, and its canonical parameters (§7.6)."""
    readback: list[Segment]
    caveats: list[Caveat]


class DocumentValidation(Output):
    """What ``validate_document`` gives (§8.6, §11.1): every refusal, sorted, and every cohort
    that canonicalised."""

    valid: StrictBool
    """Whether the document has no refusal."""
    refusals: list[Refusal]
    translation: Translation | None = None
    cohorts: list[CohortCheck]
    """The cohorts that canonicalised, by name; one refused, or that depends on one, is left
    out."""
    views: list[ViewCheck]
    """The views that checked, in document order; one refused, or one of whose cohorts or
    predicates was, is left out."""
    params: Parameters | None = None
    """Absent when the document did not load."""


class NamedCount(Output):
    cohort: Data
    count: CohortCount
    leaves: Annotated[
        dict[Annotated[JsonPointer, Field(min_length=1)], list[LeafKey]],
        Field(json_schema_extra=DATA_MARK),
    ]
    """Each leaf of the cohort as written, and the keys of the top-level clauses it became part
    of: the keys of ``unknown_by_leaf`` (§6.6, §8.1); outside the digest."""


class CohortCounts(Output):
    """What ``count_cohort`` gives (§8.1, D300): each cohort's count, by name."""

    counts: Annotated[list[NamedCount], Field(min_length=1)]
    views: list[ViewCheck]
    params: Parameters


class AnalysisResults(Output):
    """What ``run_analysis`` gives (§8.1, §11.1, D318): one result envelope per view, in
    document order, and the parameters used."""

    results: Annotated[list[ResultEnvelope], Field(min_length=1)]
    params: Parameters


class DerivationRecord(Output):
    id: DerivationId
    kind: Literal["cohort", "result"]
    object: DataObject | None = None
    """The object the id hashes: the canonical form with its unit, versions, disclosure setting
    and packs (§7.6); absent once erasure took it (§12.2)."""
    releases: list[ReleaseRef]


class IssuanceRecord(Output):
    """What ``explain`` gives of an issuance: never the request, the document as written and
    the parameters, which may hold another client's other cohorts, notes and names, and which
    the operator router alone serves (D302)."""

    id: IssuanceId
    derivation: DerivationId
    tool: Literal["count_cohort", "run_analysis"]
    sql: RecordJson | None = None
    """The SQL as run, ``{"statements", "parameters"}`` with blobs as their digests; absent for a
    cache hit, whose values came from ``values_from``."""
    values_from: IssuanceId
    engine: Annotated[str, StringConstraints(pattern=ENGINE_RE)]
    packs: DataObject
    at: Annotated[str, StringConstraints(pattern=TIME_RE)]


class Explanation(Output):
    """What ``explain`` gives (§7.6, §11.1, §12.2; D302)."""

    id: Annotated[
        str,
        StringConstraints(pattern=f"(?:{DERIVATION_ID_RE.pattern})|(?:{ISSUANCE_ID_RE.pattern})"),
    ]
    status: Literal[
        "issued", "not_issued", "unknown", "withdrawn", "discarded", "unknown_release", "erased"
    ]
    derivation: DerivationRecord | None = None
    issuance: IssuanceRecord | None = None


__all__ = [
    "DOCUMENT_MARK",
    "ENGINE_RE",
    "TIME_RE",
    "AnalysisResults",
    "CohortCheck",
    "CohortCounts",
    "CountCohort",
    "DataObject",
    "DerivationRecord",
    "DocumentValidation",
    "Explain",
    "Explanation",
    "FormatName",
    "IdStatus",
    "IssuanceRecord",
    "NamedCount",
    "Parameters",
    "RecordJson",
    "RunAnalysis",
    "Translation",
    "TranslationNote",
    "ValidateDocument",
    "ViewCheck",
]
