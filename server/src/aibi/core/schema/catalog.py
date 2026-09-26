"""What the catalogue tools exchange (SPEC §8.1, §8.4, §9.4, §11.1, D270–D280).

**Requests** are strict, closed and frozen, as the operator's are (D260): a member a model does
not name is refused, every list and string is bounded, and a union is chosen by a function. A
tool's arguments are read by ``loading.load_request`` whichever transport carries them, the MCP
server's or the HTTP API's (D278, D280). ``by`` is never part of a request: ``propose_descriptor``
names the client's agent in ``agent``, and the server attributes the proposal to
``agent:<agent>`` (§5.1, §11.1, D277).

**Outputs** are ``Output``s (§8.1). Every count is a ``StatCount``: the count, ``null`` when the
disclosure settings suppress it with its reason in ``not_estimable``, and its release-scoped
reference, ``stat:<manifest>/<descriptor id><pointer>`` (§8.1, D272). Text from data (labels,
grains, values, facet values) is carried as ``{"data": …}``, and whole descriptors verbatim, their
schema nodes marked ``x-aibi-data`` (A6).
"""

from typing import Annotated, Literal, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    WithJsonSchema,
    model_validator,
)
from pydantic_core import PydanticCustomError

from aibi.core.schema.caveats import Caveat
from aibi.core.schema.curation import CurationQueue, ProposalInput
from aibi.core.schema.descriptors import By, Datatype, PositiveInt
from aibi.core.schema.ids import (
    MAX_SAFE_INTEGER,
    AnalysisId,
    ColumnRef,
    ConceptId,
    DatasetId,
    PackId,
    RelationshipId,
    Sha256,
    TableId,
)
from aibi.core.schema.limits import ENTRIES, MAX_ENTRIES, MAX_NAME, LimitName, map_cap
from aibi.core.schema.numbers import ComputedCount, Estimable
from aibi.core.schema.output import (
    DATA_MARK,
    OUTPUT_JSON_MARK,
    Count,
    Data,
    Finite,
    FiniteJsonObject,
    Output,
)
from aibi.core.schema.refusals import BIDI_FORMATTING, holds_secret
from aibi.core.schema.results import StatRef

MAX_SEARCH_TEXT = 256
"""Characters of a search's text."""
MAX_HITS = 50
"""Datasets a search returns at a time."""
DEFAULT_HITS = 20
MAX_HIT_TABLES = 100
"""Tables a search hit lists; the others are counted (``tables_left_out``)."""
MAX_DESCRIBED_COLUMNS = 10_000
"""Columns ``describe_dataset`` lists at a time (``columns_limit``)."""
DEFAULT_DESCRIBED_COLUMNS = 2_000
MAX_OFFSET = MAX_SAFE_INTEGER

Role = Literal["entity", "link", "measurement", "event", "coverage"]
FacetName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}\.[a-z][a-z0-9_]{0,63}$"),
    Field(json_schema_extra={"not": {"pattern": "__"}}),
]
"""A pack's facet, ``<pack id>.<name>`` (D273)."""


DescriptorJson = Annotated[
    FiniteJsonObject,
    WithJsonSchema(
        {"type": "object", "additionalProperties": {OUTPUT_JSON_MARK: True}, **DATA_MARK}
    ),
]
"""A descriptor as the release stores it: data (A6)."""


_SEPARATORS = frozenset({0x2028, 0x2029})
"""The line and paragraph separators, which a name may not hold (§5.1)."""


def _agent(value: str) -> str:
    if not BIDI_FORMATTING.isdisjoint(value) or holds_secret(value):
        raise PydanticCustomError(
            "invalid_name",
            "An agent's name holds no bidi formatting and no token's or handle's shape "
            "standing alone",
        )
    if any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F or ord(c) in _SEPARATORS for c in value):
        raise PydanticCustomError(
            "invalid_name", "An agent's name has no control characters or line breaks"
        )
    return value


AgentName = Annotated[
    str,
    Field(min_length=1, max_length=MAX_NAME, json_schema_extra=DATA_MARK),
    AfterValidator(_agent),
]
"""The name an external client declares for itself (§5.1, D277)."""


def _pin(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return "label"
    if value == "draft":
        return "draft"
    if isinstance(value, str):
        return "manifest"
    return None


ReleasePin = Annotated[
    Annotated[PositiveInt, Tag("label")]
    | Annotated[Literal["draft"], Tag("draft")]
    | Annotated[Sha256, Tag("manifest")],
    Discriminator(
        _pin,
        custom_error_type="wrong_type",
        custom_error_message='A release is a label (an integer), "draft" or a manifest hash',
    ),
]
"""A release of a dataset: a published label, ``"draft"`` (the open session's) or a manifest
hash (§7.1); none is the latest published release."""


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


# --- search_catalog ---------------------------------------------------------------------------


class OntologyCode(_Request):
    system: Annotated[str, Field(min_length=1, max_length=MAX_NAME)]
    code: Annotated[str, Field(min_length=1, max_length=4_096)]


class Completeness(_Request):
    """Some column, with the concept or datatype if given, whose PRESENT cells are at least
    ``min_present`` of its table's rows (D274)."""

    min_present: Annotated[float, Field(ge=0, le=1)]
    concept: ConceptId | None = None
    datatype: Datatype | None = None


_Filters = Annotated[list[str], Field(min_length=1, max_length=MAX_ENTRIES), LimitName(ENTRIES)]


class SearchCatalog(_Request):
    """Every filter given must hold; none given lists every dataset (D274)."""

    text: Annotated[str, Field(min_length=1, max_length=MAX_SEARCH_TEXT)] | None = None
    """Words (split on white space) that each occur, case folded, in the dataset's name, label,
    description or tags, or in a table's or a column's id, label or definition, or a grain."""
    domain_tags: _Filters | None = None
    data_use: (
        Annotated[
            list[OntologyCode], Field(min_length=1, max_length=MAX_ENTRIES), LimitName(ENTRIES)
        ]
        | None
    ) = None
    concepts: (
        Annotated[list[ConceptId], Field(min_length=1, max_length=MAX_ENTRIES), LimitName(ENTRIES)]
        | None
    ) = None
    """Concepts a table, column or endpoint maps to by an asserted mapping (§5.7)."""
    roles: Annotated[list[Role], Field(min_length=1, max_length=5)] | None = None
    min_rows: Count | None = None
    """Some table other than a coverage table has at least this many rows, as disclosed."""
    completeness: Completeness | None = None
    facets: (
        Annotated[
            dict[FacetName, _Filters],
            Field(min_length=1, max_length=16),
            map_cap(16),
            LimitName(ENTRIES),
        ]
        | None
    ) = None
    """Each pack facet holds every value listed."""
    offset: Annotated[Count, Field(le=MAX_OFFSET)] = 0
    limit: Annotated[int, Field(ge=1, le=MAX_HITS)] = DEFAULT_HITS


# --- describe_dataset, describe_column, curation_queue ----------------------------------------


class DescribeDataset(_Request):
    dataset: DatasetId
    release: ReleasePin | None = None
    columns_offset: Annotated[Count, Field(le=MAX_OFFSET)] = 0
    """The first column listed, counting every table's columns in table and column order."""
    columns_limit: Annotated[int, Field(ge=1, le=MAX_DESCRIBED_COLUMNS)] = DEFAULT_DESCRIBED_COLUMNS


class DescribeColumn(_Request):
    dataset: DatasetId
    column: ColumnRef
    """``<table>.<column>``."""
    release: ReleasePin | None = None


class QueueRequest(_Request):
    dataset: DatasetId
    release: PositiveInt | None = None
    """A published label; the latest published release when absent."""


class ProposeDescriptor(ProposalInput):
    """A proposal (D248) for a dataset's latest published release, by the agent named."""

    dataset: DatasetId
    agent: AgentName


# --- Outputs ----------------------------------------------------------------------------------


class StatCount(Estimable):
    """A catalogue statistic's count: ``null`` when suppressed (§8.4), with its reference."""

    count: ComputedCount
    reference: StatRef


class StatValue(Output):
    """A value a statistic reports (a smallest or largest value), with its reference."""

    value: Finite | Data
    reference: StatRef


class ReleaseOut(Output):
    label: PositiveInt | Literal["draft"]
    manifest: Sha256
    status: Literal["published", "draft"]

    @model_validator(mode="after")
    def _draft(self) -> Self:
        if (self.label == "draft") != (self.status == "draft"):
            raise PydanticCustomError(
                "release_label", 'A release is labelled "draft" exactly when it is the draft'
            )
        return self


class DisclosureOut(Output):
    """The effective disclosure setting: the largest of the floor and the dataset's (§8.4)."""

    min_cell_count: Annotated[int, Field(ge=2, le=MAX_SAFE_INTEGER)] | None


class StateCounts(Output):
    PRESENT: StatCount
    NOT_APPLICABLE: StatCount
    NOT_ASSESSED: StatCount
    UNKNOWN: StatCount


class CategoryCount(StatCount):
    value: Data


class CategoriesOut(Output):
    kind: Literal["categories"]
    categories: list[CategoryCount]
    pooled: StatCount | None = None
    """The categories pooled under the disclosure settings (§8.4), when some were."""
    multi_membership: bool
    """A row counts under each of its list's values (§9.2)."""


class Bin(StatCount):
    low: Finite | Data | None = None
    """Absent for the open bin below the first edge."""
    high: Finite | Data | None = None
    """Absent for the open bin above the last edge."""
    includes_low: bool
    """Whether the bin holds ``low``: every bin but the one above the last edge does."""
    includes_high: bool
    """Whether the bin holds ``high``: only the bin that ends at the last edge does."""


class HistogramOut(Output):
    kind: Literal["histogram"]
    edges_from: Literal["range", "data"]
    """The column's declared range, or, without disclosure settings only, its values."""
    bins: list[Bin]
    min: StatValue | None = None
    """Not reported under disclosure settings (§8.4)."""
    max: StatValue | None = None


NoneReason = Literal[
    "identifier",
    "text",
    "undeclared",
    "categories",
    "out_of_range",
    "empty",
    "no_declared_range",
    "suppressed",
    "unrepresentable",
]
"""Why a column has no distribution: ``store.statistics``'s reasons, and those of the
disclosure pass (D271, D276)."""


class NoDistribution(Output):
    kind: Literal["none"]
    reason: NoneReason


def _kind(value: object) -> str | None:
    found = (
        cast(dict[str, object], value).get("kind")
        if isinstance(value, dict)
        else getattr(value, "kind", None)
    )
    return found if found in ("categories", "histogram", "none") else None


Distribution = Annotated[
    Annotated[CategoriesOut, Tag("categories")]
    | Annotated[HistogramOut, Tag("histogram")]
    | Annotated[NoDistribution, Tag("none")],
    Discriminator(
        _kind,
        custom_error_type="unknown_kind",
        custom_error_message="kind is categories, histogram or none",
    ),
]


class ColumnStatistics(Output):
    rows: StatCount
    """The table's rows."""
    states: StateCounts
    distribution: Distribution


class ColumnDescription(Output):
    dataset: DatasetId
    release: ReleaseOut
    disclosure: DisclosureOut
    descriptor: DescriptorJson
    identifier: bool
    """Declared, or a key or foreign-key column (§5.4)."""
    statistics: ColumnStatistics
    caveats: list[Caveat]


class ColumnBrief(Output):
    id: ColumnRef
    label: Data
    datatype: Datatype | None = None
    identifier: bool


class TableOut(Output):
    descriptor: DescriptorJson
    rows: StatCount
    columns: list[ColumnBrief]


class GraphEdge(Output):
    relationship: RelationshipId
    child: TableId
    parent: TableId
    cardinality: Literal["many-to-one", "one-to-one"]


class TableGraph(Output):
    """The tables other than coverage tables, and the relationships between them (§6.1)."""

    tables: list[TableId]
    edges: list[GraphEdge]


class ApplicableAnalysis(Output):
    """An analysis's applicability (§9.4); the list is empty until the registry exists (M3)."""

    analysis: AnalysisId
    status: Literal["available", "unavailable", "available_with_caveats"]


class DatasetDescription(Output):
    dataset: DatasetId
    release: ReleaseOut
    disclosure: DisclosureOut
    descriptor: DescriptorJson
    tables: list[TableOut]
    relationships: list[DescriptorJson]
    coverage: list[DescriptorJson]
    endpoints: list[DescriptorJson]
    graph: TableGraph
    applicable_analyses: list[ApplicableAnalysis]
    caveats: list[Caveat]
    """Standing caveats (§11.1, D275), and ``DRAFT_RELEASE`` and ``SUPPRESSED`` where they
    apply."""
    columns_total: Count
    """Every column of every table; ``tables`` lists those from ``columns_offset`` on."""
    columns_next: Count | None = None
    """The ``columns_offset`` that lists the next columns, when some were left out."""


class OntologyTerm(Output):
    system: Data
    code: Data
    label: Data


class TableSummary(Output):
    id: TableId
    label: Data
    grain: Data | None = None
    role: Role | None = None
    rows: StatCount


class CatalogHit(Output):
    dataset: DatasetId
    release: ReleaseOut
    disclosure: DisclosureOut
    label: Data
    name: Data | None = None
    description: Data | None = None
    domain_tags: list[Data]
    data_use: list[OntologyTerm]
    packs: list[PackId]
    concepts: list[ConceptId]
    facets: Annotated[dict[FacetName, list[Data]], Field(max_length=MAX_ENTRIES)]
    tables: list[TableSummary]
    tables_left_out: Count


class CatalogHits(Output):
    hits: list[CatalogHit]
    total: Count
    """The datasets that match, on every page."""
    next_offset: Count | None = None
    """The ``offset`` of the next page, when there is one."""
    caveats: list[Caveat]


class QueueOut(Output):
    """The curation queue as a tool gives it: disclosed, every count with its reference
    (D277)."""

    queue: CurationQueue
    disclosure: DisclosureOut
    caveats: list[Caveat]


class Proposed(Output):
    dataset: DatasetId
    proposal: PositiveInt
    release: Sha256
    """The manifest of the release it was made against, the latest published one."""
    label: PositiveInt
    by: By


TOOL_MODELS: dict[str, tuple[type[BaseModel], type[Output]]] = {
    "search_catalog": (SearchCatalog, CatalogHits),
    "describe_dataset": (DescribeDataset, DatasetDescription),
    "describe_column": (DescribeColumn, ColumnDescription),
    "curation_queue": (QueueRequest, QueueOut),
    "propose_descriptor": (ProposeDescriptor, Proposed),
}
"""Each tool's request and output models, from which every schema of the tool is generated."""


__all__ = [
    "DEFAULT_DESCRIBED_COLUMNS",
    "DEFAULT_HITS",
    "MAX_DESCRIBED_COLUMNS",
    "MAX_HITS",
    "MAX_HIT_TABLES",
    "MAX_SEARCH_TEXT",
    "TOOL_MODELS",
    "AgentName",
    "ApplicableAnalysis",
    "Bin",
    "CatalogHit",
    "CatalogHits",
    "CategoriesOut",
    "CategoryCount",
    "ColumnBrief",
    "ColumnDescription",
    "ColumnStatistics",
    "Completeness",
    "DatasetDescription",
    "DescribeColumn",
    "DescribeDataset",
    "DescriptorJson",
    "DisclosureOut",
    "Distribution",
    "FacetName",
    "GraphEdge",
    "HistogramOut",
    "NoDistribution",
    "NoneReason",
    "OntologyCode",
    "OntologyTerm",
    "ProposeDescriptor",
    "Proposed",
    "QueueOut",
    "QueueRequest",
    "ReleaseOut",
    "ReleasePin",
    "Role",
    "SearchCatalog",
    "StatCount",
    "StatRef",
    "StatValue",
    "StateCounts",
    "TableGraph",
    "TableOut",
    "TableSummary",
]
