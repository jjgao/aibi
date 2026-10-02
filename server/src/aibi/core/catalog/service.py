"""The catalogue's service functions, which the MCP server and the HTTP API both call (SPEC
§11.1, §12.1, D274–D279).

``Catalog`` holds what they read: the store, the pack registry the server runs with, the
deployment's disclosure floor and the model cards of its configuration. Each function reads
published releases, or the open session's draft when a request pins ``draft``, and changes none:
``propose_descriptor`` adds a proposal to the queue, which changes no release (§12.3). None of
them is an operator operation (§11.2): importing, curating, accepting, withdrawing and erasing
stay on the operator router.

- ``search_catalog`` filters the catalogue index (``index``, D273) by every filter given (D274):
  words of text, domain tags, data-use codes, concepts by asserted mapping, table roles, a least
  number of rows, a completeness threshold and pack facets, over disclosed statistics, so a
  suppressed count matches no threshold. Hits are in dataset order, a page at a time.
- ``describe_dataset`` gives a release's dataset descriptor, its tables with their row counts and
  brief columns (a page of them), its relationships, coverage and endpoints, the table graph, the
  applicable analyses over its keyed tables (§9.4, D316) and the standing caveats (D275).
- ``list_analyses`` gives the registry's entries, and for a dataset their applicability (D316).
- ``describe_column`` gives a column's descriptor with its observation-state counts and value
  distribution, disclosed (D276).
- ``curation_queue`` gives the queue of §11.1 (D250), its counts disclosed and referenced (D277).
- ``propose_descriptor`` records an agent's proposal (D248), attributed to ``agent:<name>``.
- ``resources`` and ``read_resource`` serve every descriptor as an MCP resource (D279), the
  registry's entries included (``aibi://analysis/<id>@<version>``).

Refusals are raised as ``ToolRefused``, the store's ``StoreRefused`` or the proposal checks'
``EditRefused``; ``refusals_of`` gives the refusals of any of them. They name what is available
(A3): the datasets, the tables or the columns there are.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal, cast

from pydantic import JsonValue, TypeAdapter

from aibi.core.analyses.registry import Analyses
from aibi.core.catalog.disclosure import (
    DisclosedTable,
    References,
    column_output,
    disclose_table,
    effective_k,
)
from aibi.core.catalog.index import (
    Entry,
    dataset_k,
    disclosed_tables,
    entries,
    kept_statistics,
)
from aibi.core.engine.worker import Workers
from aibi.core.schema.catalog import (
    MAX_HIT_TABLES,
    AnalysisListing,
    CatalogHit,
    CatalogHits,
    ColumnBrief,
    ColumnDescription,
    DatasetDescription,
    DescribeColumn,
    DescribeDataset,
    DisclosureOut,
    GraphEdge,
    ListAnalyses,
    OntologyTerm,
    Proposed,
    ProposeDescriptor,
    QueueOut,
    QueueRequest,
    ReleaseOut,
    Role,
    SearchCatalog,
    TableGraph,
    TableOut,
    TableSummary,
)
from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, sort_caveats
from aibi.core.schema.concepts import CORE_CONCEPTS
from aibi.core.schema.curation import DESCRIPTOR_ID_RE, QueueField, QueueNote, QueueProposal
from aibi.core.schema.descriptors import (
    By,
    ColumnDescriptor,
    ConceptDescriptor,
    CoverageDescriptor,
    DatasetDescriptor,
    Descriptor,
    EndpointDescriptor,
    ModelCardDescriptor,
    RelationshipDescriptor,
    TableDescriptor,
)
from aibi.core.schema.jsonio import canonical
from aibi.core.schema.numbers import NotEstimableReason
from aibi.core.schema.output import Data, Output, Segment, data, text
from aibi.core.schema.pack_api import NoteKind, PackRegistry
from aibi.core.schema.refusals import Refusal, RefusalCode
from aibi.core.schema.semantics import ObservationState
from aibi.core.store import statistics
from aibi.core.store.blobs import MissingBlobError
from aibi.core.store.edits import EditRefused
from aibi.core.store.manifest import Manifest
from aibi.core.store.proposals import curation_queue as store_queue
from aibi.core.store.proposals import propose_descriptor as store_propose
from aibi.core.store.store import Pin, Store, StoreRefused

UNCONFIRMED = frozenset({"imported_default", "proposed", "undeclared"})
"""The statuses of a field read that raise ``UNCONFIRMED_SEMANTICS`` (§5.1)."""
LISTED = 16
"""Fields a standing caveat names before it counts the rest."""
ALTERNATIVES = 64
"""What a refusal lists as available before it counts the rest."""
MAX_RESOURCES = 1_000
"""Resources ``resources`` lists: the latest dataset descriptors, concepts and model cards."""
MAX_URI = 2_048
_NUMERIC = ("number", "integer", "time_offset")
_BY: TypeAdapter[str] = TypeAdapter(By)
_DATASET_URI = re.compile(
    r"^aibi://dataset/(?P<dataset>[a-z][a-z0-9_]{0,63})@"
    r"(?P<release>[1-9][0-9]{0,15}|draft|sha256:[0-9a-f]{64})/(?P<descriptor>[^/?#]+)$"
)
_CONCEPT_URI = re.compile(r"^aibi://concept/(?P<id>[^/?#]+)$")
_MODEL_URI = re.compile(r"^aibi://model/(?P<id>[^/?#]+)$")
_ANALYSIS_URI = re.compile(r"^aibi://analysis/(?P<id>[^/?#@]+)@(?P<version>[^/?#]+)$")
NOTE_TEXT: Mapping[NoteKind, str] = {
    "skipped_source": "A source was skipped at import",
    "renamed": "A name was changed to make an id",
    "not_proposed": "The importer proposed nothing here",
    "dropped": "A proposal was dropped at import: it did not hold against the data",
    "unparsed": "Cells whose value does not parse as the column's datatype, which are UNKNOWN",
    "gap": "Rows that the coverage or the endpoint's coding does not account for",
    "reimported": "Changed by a re-import",
}
"""The message of an import report's note in the public queue under a disclosure setting, by its
kind: the report's own words may quote counts (D277)."""
RESOURCE_TEMPLATES = (
    ("aibi://dataset/{dataset}@{release}/{descriptor}", "descriptor"),
    ("aibi://concept/{concept}", "concept"),
    ("aibi://analysis/{analysis}@{version}", "analysis"),
    ("aibi://model/{model}", "model"),
)
"""The resource templates (§11.1): a release is a label, ``draft`` or ``sha256:<hex>``."""


class ToolRefused(Exception):  # noqa: N818 - the spec's word
    def __init__(self, refusals: Sequence[Refusal]) -> None:
        super().__init__("; ".join(f"{refusal.code} at {refusal.path}" for refusal in refusals))
        self.refusals = tuple(refusals)


def refusals_of(error: BaseException) -> tuple[Refusal, ...] | None:
    """The refusals of a refusal raised by a service function; ``None`` for anything else."""
    if isinstance(error, ToolRefused | EditRefused):
        return tuple(error.refusals)
    if isinstance(error, StoreRefused):
        return (error.refusal,)
    return None


def _refused(
    code: RefusalCode, path: str | None, message: str, available: Iterable[str] = ()
) -> ToolRefused:
    listed = list(available)
    alternatives: list[Segment] = [data(item) for item in listed[:ALTERNATIVES]]
    if len(listed) > ALTERNATIVES:
        alternatives.append(text(f"and {len(listed) - ALTERNATIVES} more"))
    return ToolRefused(
        [Refusal(code=code, path=path, message=[text(message)], alternatives=alternatives)]
    )


def _caveat(code: CaveatCode, message: list[Segment], affects: Sequence[str]) -> Caveat:
    return Caveat(code=code, severity=CORE_SEVERITIES[code], message=message, affects=list(affects))


def _suppressed(affects: Sequence[str]) -> Caveat:
    return _caveat(
        CaveatCode.SUPPRESSED,
        [
            text(
                "Counts from 1 to min_cell_count - 1, and what would reveal them, are suppressed "
                "(null), pooled or merged under the disclosure settings"
            )
        ],
        affects,
    )


def _draft(affects: Sequence[str]) -> Caveat:
    return _caveat(
        CaveatCode.DRAFT_RELEASE,
        [text("This is a curation session's draft, which may change or be discarded")],
        affects,
    )


_WITHDRAWN = "The release was withdrawn: its data and descriptors are not kept"


def _dumped(descriptor: Descriptor) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], descriptor.model_dump(mode="json"))


def _served(descriptor: Descriptor, k: int | None) -> dict[str, JsonValue]:
    """A descriptor as a tool or a resource gives it: under a disclosure setting, without the
    ``evidence`` of its curation entries, which may quote counts (D271)."""
    dumped = _dumped(descriptor)
    if k is None:
        return dumped
    entries = cast(dict[str, dict[str, JsonValue]], dumped["curation"])
    dumped["curation"] = {
        pointer: {name: value for name, value in entry.items() if name != "evidence"}
        for pointer, entry in entries.items()
    }
    return dumped


@dataclass(frozen=True)
class _Release:
    dataset: str
    manifest: str
    label: int | Literal["draft"]

    @property
    def out(self) -> ReleaseOut:
        status: Literal["published", "draft"] = "draft" if self.label == "draft" else "published"
        return ReleaseOut(label=self.label, manifest=self.manifest, status=status)


def _field_reads(descriptors: Sequence[Descriptor]) -> list[tuple[str, str, str]]:
    """The fields queries read (§5.1) whose status raises ``UNCONFIRMED_SEMANTICS``, as
    (descriptor id, pointer, status); a proposed ``parents`` is ``COVERAGE_PROPOSED``'s."""
    found: list[tuple[str, str, str]] = []

    def read(descriptor: Descriptor, pointer: str, *, absent_counts: bool = False) -> None:
        entry = descriptor.curation.get(pointer)
        if entry is not None and entry.status in UNCONFIRMED:
            found.append((descriptor.id, pointer, entry.status))
        elif entry is None and absent_counts:
            found.append((descriptor.id, pointer, "undeclared"))

    covered = {d.fields.relationship for d in descriptors if isinstance(d, CoverageDescriptor)}
    for descriptor in descriptors:
        if isinstance(descriptor, ColumnDescriptor):
            fields = descriptor.fields
            read(descriptor, "/fields/datatype")
            if fields.datatype in _NUMERIC:
                read(descriptor, "/fields/units", absent_counts=True)
            for name in ("permissible_values", "missing_codes"):
                read(descriptor, f"/fields/{name}")
        elif isinstance(descriptor, TableDescriptor):
            read(descriptor, "/fields/primary_key")
        elif isinstance(descriptor, RelationshipDescriptor):
            for name in ("child_columns", "parent_columns"):
                read(descriptor, f"/fields/{name}")
            if descriptor.id not in covered:
                coverage = "cov:" + descriptor.id.removeprefix("rel:")
                found.append((coverage, "/fields/parents", "undeclared"))
        elif isinstance(descriptor, CoverageDescriptor):
            parents = descriptor.curation.get("/fields/parents")
            if parents is None:
                found.append((descriptor.id, "/fields/parents", "undeclared"))
            elif parents.status == "imported_default":
                found.append((descriptor.id, "/fields/parents", parents.status))
            for name in ("record_filter", "parent_scope"):
                read(descriptor, f"/fields/{name}")
    return found


def _unconfirmed(reads: Sequence[tuple[str, str, str]], affects: Sequence[str]) -> Caveat:
    message: list[Segment] = [
        text(
            f"{len(reads)} descriptor fields that queries read are not confirmed by an operator "
            "(imported by default, proposed or undeclared): "
        )
    ]
    for index, (descriptor, pointer, status) in enumerate(reads[:LISTED]):
        if index:
            message.append(text(", "))
        message.extend((data(descriptor + pointer), text(f" ({status})")))
    if len(reads) > LISTED:
        message.append(text(f", and {len(reads) - LISTED} more"))
    message.append(text(". The curation queue lists every one."))
    return _caveat(CaveatCode.UNCONFIRMED_SEMANTICS, message, affects)


@dataclass(frozen=True)
class Deadline:
    """When a tool call must be answered: ``at``, a ``time.monotonic()`` instant, which is
    ``seconds`` (``tool_seconds``) from when its body arrived."""

    at: float
    seconds: float


DEADLINE: ContextVar[Deadline | None] = ContextVar("aibi_tool_deadline", default=None)
"""The deadline of the tool call this thread runs (set by ``mcp.calls``); ``None`` outside a
call (D301)."""


def within[T](deadline: Deadline, function: Callable[[], T]) -> T:
    """``function``'s result, called with ``DEADLINE`` set to ``deadline``."""
    token = DEADLINE.set(deadline)
    try:
        return function()
    finally:
        DEADLINE.reset(token)


@dataclass(frozen=True)
class Catalog:
    """What the catalogue's service functions read (module docstring)."""

    store: Store
    registry: PackRegistry | None = None
    floor: int | None = None
    """The deployment's disclosure floor (§8.4, D253)."""
    models: tuple[ModelCardDescriptor, ...] = field(default=())
    token_digest: bytes | None = field(default=None, repr=False)
    """The configured curator token's SHA-256, which text a proposal stores may not hold
    anywhere (D265)."""
    workers: Workers | None = field(default=None, repr=False)
    """The query workers that ``count_cohort`` runs a document's queries in (§14, D293); a
    catalogue without them counts nothing."""

    @property
    def analyses(self) -> Analyses:
        """The analysis registry over the server's packs (§9, D316)."""
        return Analyses(self.registry)

    def _references(self, manifest: str) -> References:
        return References(manifest, self.floor)

    def _labels(self, dataset: str) -> list[str]:
        return [str(label.label) for label in self.store.labels(dataset) if not label.withdrawn]

    def _refused_release(self, dataset: str, refusal: Refusal, path: str | None) -> ToolRefused:
        """A release refusal at ``path``, listing the dataset's published labels (A3)."""
        listed = self._labels(dataset)
        alternatives: list[Segment] = [data(label) for label in listed[:ALTERNATIVES]]
        if len(listed) > ALTERNATIVES:
            alternatives.append(text(f"and {len(listed) - ALTERNATIVES} more"))
        return ToolRefused(
            [refusal.model_copy(update={"path": path, "alternatives": alternatives})]
        )

    def _uncounted(self, dataset: str) -> ToolRefused:
        """The refusal of a release built before catalogue statistics were kept (D270)."""
        message: list[Segment] = [
            text(
                "The release was built before catalogue statistics were kept, and the catalogue "
                "reads no rows: an operator publishes a new release of the dataset (a curation "
                "session's change or a re-import of changed files), whose build counts them"
            )
        ]
        refusal = Refusal(code=RefusalCode.NOT_SUPPORTED, path=None, message=message)
        return self._refused_release(dataset, refusal, "/release")

    def _held(self, pin: Pin, release: _Release, path: str | None = "/release") -> Manifest:
        """Pin a release that was resolved a moment ago: one withdrawn and swept meanwhile is
        refused as withdrawn."""
        try:
            return pin.manifest(release.manifest)
        except MissingBlobError:
            message: list[Segment] = [text(_WITHDRAWN)]
            refusal = Refusal(code=RefusalCode.RELEASE_WITHDRAWN, path=None, message=message)
            raise self._refused_release(release.dataset, refusal, path) from None

    def _k(self, pin: Pin, release: _Release, descriptors: Sequence[Descriptor]) -> int | None:
        """The effective *k* of a release (§8.4); a draft's is at least the latest published
        release's too, so that a session cannot lower it before it is published (D275)."""
        if release.label != "draft":
            return effective_k(self.floor, dataset_k(descriptors))
        published = self.published_k(pin, release.dataset)
        return effective_k(self.floor, dataset_k(descriptors), published)

    def published_k(self, pin: Pin, dataset: str) -> int | None:
        """The ``min_cell_count`` of the dataset's latest published release that is still kept,
        pinned: at least what a draft's outputs use (D275, D300)."""
        for label in reversed(self.store.labels(dataset)):
            if label.withdrawn:
                continue
            try:
                pin.manifest(label.manifest)
            except MissingBlobError:
                continue
            return dataset_k(self.store.descriptors(label.manifest))
        return None

    def _known(self, dataset: str, path: str | None = "/dataset") -> None:
        if not self.store.labels(dataset):
            available = [d for d in self.store.datasets() if self.store.latest(d) is not None]
            raise _refused(
                RefusalCode.UNKNOWN_DATASET,
                path,
                "No release of the dataset was published; these datasets have one",
                available,
            )

    def _release(
        self, dataset: str, pin: int | str | None, path: str | None = "/release"
    ) -> _Release:
        """The release a request pins; refused at ``path``, listing the published labels."""
        self._known(dataset)
        try:
            resolution = self.store.resolve(dataset, pin)
        except StoreRefused as refused:
            raise self._refused_release(dataset, refused.refusal, path) from None
        if resolution.status == "withdrawn":
            withdrawn: list[Segment] = [text(_WITHDRAWN)]
            found = Refusal(code=RefusalCode.RELEASE_WITHDRAWN, path=None, message=withdrawn)
            raise self._refused_release(dataset, found, path)
        if resolution.status == "discarded" or resolution.label is None:
            discarded: list[Segment] = [text("That draft state was discarded")]
            found = Refusal(code=RefusalCode.UNKNOWN_RELEASE, path=None, message=discarded)
            raise self._refused_release(dataset, found, path)
        return _Release(dataset, resolution.manifest, resolution.label)

    # --- search_catalog ------------------------------------------------------------------------

    def search_catalog(self, request: SearchCatalog) -> CatalogHits:
        found = [e for e in entries(self.store, self.registry, self.floor) if _matches(e, request)]
        page = found[request.offset : request.offset + request.limit]
        hits = [self._hit(entry) for entry in page]
        suppressed = [
            f"/hits/{position}/tables/{index}/rows"
            for position, hit in enumerate(hits)
            for index, table in enumerate(hit.tables)
            if table.rows.count is None
        ]
        end = request.offset + request.limit
        return CatalogHits(
            hits=hits,
            total=len(found),
            next_offset=end if end < len(found) else None,
            caveats=[_suppressed(suppressed)] if suppressed else [],
        )

    def _hit(self, entry: Entry) -> CatalogHit:
        references = self._references(entry.manifest)
        listed = entry.tables[:MAX_HIT_TABLES]
        return CatalogHit(
            dataset=entry.dataset,
            release=ReleaseOut(label=entry.label, manifest=entry.manifest, status="published"),
            disclosure=DisclosureOut(min_cell_count=entry.k),
            label=Data(data=entry.title),
            name=None if entry.name is None else Data(data=entry.name),
            description=None if entry.description is None else Data(data=entry.description),
            domain_tags=[Data(data=tag) for tag in entry.domain_tags],
            data_use=[
                OntologyTerm(system=Data(data=s), code=Data(data=c), label=Data(data=label))
                for s, c, label in entry.data_use
            ],
            packs=list(entry.packs),
            concepts=list(entry.concepts),
            facets={name: [Data(data=v) for v in values] for name, values in entry.facets.items()},
            tables=[
                TableSummary(
                    id=table.id,
                    label=Data(data=table.label),
                    grain=None if table.grain is None else Data(data=table.grain),
                    role=cast(Role | None, table.role),
                    rows=references.count(table.rows, table.id, "n_rows"),
                )
                for table in listed
            ],
            tables_left_out=len(entry.tables) - len(listed),
        )

    # --- describe_dataset ----------------------------------------------------------------------

    def describe_dataset(self, request: DescribeDataset) -> DatasetDescription:
        release = self._release(request.dataset, request.release)
        with self.store.pin() as pin:
            manifest = self._held(pin, release)
            descriptors = self.store.descriptors(release.manifest)
            k = self._k(pin, release, descriptors)
            disclosed = disclosed_tables(self.store, manifest, k)
        if disclosed is None:
            raise self._uncounted(release.dataset)
        return self._described(release, descriptors, disclosed, k, request)

    def _described(
        self,
        release: _Release,
        descriptors: Sequence[Descriptor],
        disclosed: Mapping[str, DisclosedTable],
        k: int | None,
        request: DescribeDataset,
    ) -> DatasetDescription:
        references = self._references(release.manifest)
        marked = statistics.identifiers(descriptors)
        dataset: DatasetDescriptor | None = None
        tables: list[TableDescriptor] = []
        columns: dict[str, list[ColumnDescriptor]] = {}
        relationships: list[RelationshipDescriptor] = []
        coverage: list[CoverageDescriptor] = []
        endpoints: list[EndpointDescriptor] = []
        for descriptor in descriptors:
            if isinstance(descriptor, DatasetDescriptor):
                dataset = descriptor
            elif isinstance(descriptor, TableDescriptor):
                tables.append(descriptor)
            elif isinstance(descriptor, ColumnDescriptor):
                columns.setdefault(descriptor.id.split(".", 1)[0], []).append(descriptor)
            elif isinstance(descriptor, RelationshipDescriptor):
                relationships.append(descriptor)
            elif isinstance(descriptor, CoverageDescriptor):
                coverage.append(descriptor)
            elif isinstance(descriptor, EndpointDescriptor):
                endpoints.append(descriptor)
        assert dataset is not None, "a release holds its dataset descriptor"
        start, end = request.columns_offset, request.columns_offset + request.columns_limit
        position = 0
        tables_out: list[TableOut] = []
        suppressed: list[str] = []
        for index, table in enumerate(tables):
            found = disclosed.get(table.id)
            listed: list[ColumnBrief] = []
            for column in columns.get(table.id, []):
                if start <= position < end:
                    name = column.id.split(".", 1)[1]
                    listed.append(
                        ColumnBrief(
                            id=column.id,
                            label=Data(data=column.label),
                            datatype=column.fields.datatype,
                            identifier=name in marked.get(table.id, set()),
                        )
                    )
                position += 1
            rows = None if found is None else found.n_rows
            if found is not None and rows is None:
                suppressed.append(f"/tables/{index}/rows")
            tables_out.append(
                TableOut(
                    descriptor=_served(table, k),
                    rows=references.count(rows, table.id, "n_rows"),
                    columns=listed,
                )
            )
        caveats = self._standing(descriptors, tables, relationships, coverage)
        if release.label == "draft":
            caveats.append(_draft(["/release"]))
        if suppressed:
            caveats.append(_suppressed(suppressed))
        graphed = [table.id for table in tables if table.fields.role != "coverage"]
        return DatasetDescription(
            dataset=release.dataset,
            release=release.out,
            disclosure=DisclosureOut(min_cell_count=k),
            descriptor=_served(dataset, k),
            tables=tables_out,
            relationships=[_served(d, k) for d in relationships],
            coverage=[_served(d, k) for d in coverage],
            endpoints=[_served(d, k) for d in endpoints],
            graph=TableGraph(
                tables=graphed,
                edges=[
                    GraphEdge(
                        relationship=d.id,
                        child=d.fields.child_table,
                        parent=d.fields.parent_table,
                        cardinality=d.fields.cardinality,
                    )
                    for d in relationships
                ],
            ),
            applicable_analyses=self.analyses.applicable(
                descriptors, dataset=release.dataset, manifest=release.manifest
            ),
            caveats=sort_caveats(caveats),
            columns_total=position,
            columns_next=end if end < position else None,
        )

    def _standing(
        self,
        descriptors: Sequence[Descriptor],
        tables: Sequence[TableDescriptor],
        relationships: Sequence[RelationshipDescriptor],
        coverage: Sequence[CoverageDescriptor],
    ) -> list[Caveat]:
        """The caveats a query may raise from these descriptors alone (§11.1, D275)."""
        where: dict[str, str] = {"dataset": "/descriptor"}
        where.update({table.id: f"/tables/{i}" for i, table in enumerate(tables)})
        for i, relationship in enumerate(relationships):
            where[relationship.id] = f"/relationships/{i}"
            where["cov:" + relationship.id.removeprefix("rel:")] = f"/relationships/{i}"
        where.update({d.id: f"/coverage/{i}" for i, d in enumerate(coverage)})
        caveats: list[Caveat] = []
        reads = _field_reads(descriptors)
        if reads:
            affects = {
                where.get(descriptor, where.get(descriptor.split(".", 1)[0], "/descriptor"))
                for descriptor, _, _ in reads
            }
            caveats.append(_unconfirmed(reads, sorted(affects)))
        for i, found in enumerate(coverage):
            entry = found.curation.get("/fields/parents")
            if entry is not None and entry.status == "proposed":
                message: list[Segment] = [
                    text("The coverage of "),
                    data(found.fields.relationship),
                    text(
                        " is proposed: answers that rely on it carry COVERAGE_PROPOSED until an "
                        "operator confirms it"
                    ),
                ]
                caveats.append(_caveat(CaveatCode.COVERAGE_PROPOSED, message, [f"/coverage/{i}"]))
        return caveats

    # --- describe_column -----------------------------------------------------------------------

    def describe_column(self, request: DescribeColumn) -> ColumnDescription:
        release = self._release(request.dataset, request.release)
        table, name = request.column.split(".", 1)
        with self.store.pin() as pin:
            manifest = self._held(pin, release)
            descriptors = self.store.descriptors(release.manifest)
            by_id = {descriptor.id: descriptor for descriptor in descriptors}
            column = by_id.get(request.column)
            if not isinstance(column, ColumnDescriptor):
                if not isinstance(by_id.get(table), TableDescriptor):
                    known = [d.id for d in descriptors if isinstance(d, TableDescriptor)]
                    raise _refused(
                        RefusalCode.UNKNOWN_TABLE, "/column", "The release has no such table", known
                    )
                known = [
                    d.id
                    for d in descriptors
                    if isinstance(d, ColumnDescriptor) and d.id.startswith(table + ".")
                ]
                raise _refused(
                    RefusalCode.UNKNOWN_COLUMN,
                    "/column",
                    "The table has no such column; these are its columns",
                    known,
                )
            k = self._k(pin, release, descriptors)
            kept = kept_statistics(self.store, manifest)
        if kept is None:
            raise self._uncounted(release.dataset)
        disclosed = disclose_table(kept[table], k)
        references = self._references(release.manifest)
        output = column_output(table, name, disclosed, references)
        caveats: list[Caveat] = []
        reads = [read for read in _field_reads([column]) if read[0] == column.id]
        if reads:
            caveats.append(_unconfirmed(reads, ["/descriptor"]))
        if release.label == "draft":
            caveats.append(_draft(["/release"]))
        if disclosed.suppressed and (
            name in disclosed.pooled
            or any(count is None for count in disclosed.states[name].values())
            or disclosed.distributions[name].get("reason") == "suppressed"
        ):
            caveats.append(_suppressed(["/statistics"]))
        return ColumnDescription(
            dataset=release.dataset,
            release=release.out,
            disclosure=DisclosureOut(min_cell_count=k),
            descriptor=_served(column, k),
            identifier=name in statistics.identifiers(descriptors).get(table, set()),
            statistics=output,
            caveats=sort_caveats(caveats),
        )

    # --- curation_queue ------------------------------------------------------------------------

    def curation_queue(self, request: QueueRequest) -> QueueOut:
        """The queue of a published label (D277). Under a disclosure setting the report's notes
        are disclosed with the release's statistics, as one linked pass: an ``unparsed`` note
        counts cells its column's ``UNKNOWN`` counts, so its count is shown only while it is that
        count as disclosed, and every other note's count, which counts rows the statistics do
        not, is suppressed; a note's message is its kind's fixed text (``NOTE_TEXT``), a note
        without a count lists no rows, and no field or proposal carries its evidence, since all
        may quote counts. What is left out is left out before the queue's byte budget is spent
        (``_Public``), and the release is resolved first, so that the queue is the release's
        whose statistics disclosed it."""
        release = self._release(request.dataset, request.release)
        assert release.label != "draft", "the queue's request pins a published label"
        with self.store.pin() as pin:
            manifest = self._held(pin, release)
            descriptors = self.store.descriptors(release.manifest)
            k = effective_k(self.floor, dataset_k(descriptors))
            disclosed = {} if k is None else disclosed_tables(self.store, manifest, k)
        if disclosed is None:
            raise self._uncounted(release.dataset)
        public = _Public(self._references(release.manifest), disclosed, k)
        try:
            queue = store_queue(self.store, request.dataset, release=release.label, shown=public)
        except StoreRefused as refused:
            raise self._refused_release(release.dataset, refused.refusal, "/release") from None
        return QueueOut(
            queue=queue,
            disclosure=DisclosureOut(min_cell_count=k),
            caveats=[_suppressed(["/queue/notes"])] if _hidden(queue.notes) else [],
        )

    # --- propose_descriptor --------------------------------------------------------------------

    def propose_descriptor(self, request: ProposeDescriptor, *, client: str = "") -> Proposed:
        """Record an agent's proposal from the client whose key is ``client`` (D277)."""
        self._known(request.dataset)
        by = _BY.validate_python(f"agent:{request.agent}")
        proposal = store_propose(
            self.store, request.dataset, request, by, registry=self.registry, client=client
        )
        latest = self.store.latest(request.dataset)
        assert latest is not None, "a proposal is made against the latest release"
        return Proposed(
            dataset=request.dataset,
            proposal=proposal,
            release=latest.manifest,
            label=latest.label,
            by=by,
        )

    # --- list_analyses (§9.1, §9.4, D316) -----------------------------------------------------

    def list_analyses(self, request: ListAnalyses) -> AnalysisListing:
        """Every registry entry, by id, and for a dataset each one's applicability to the release
        the request pins, for its unit or for each of the release's keyed tables."""
        analyses = self.analyses
        entries = [_dumped(analysis.entry) for analysis in analyses.all()]
        if request.dataset is None:
            return AnalysisListing(analyses=entries)
        release = self._release(request.dataset, request.release)
        with self.store.pin() as pin:
            self._held(pin, release)
            descriptors = self.store.descriptors(release.manifest)
        tables = [d.id for d in descriptors if isinstance(d, TableDescriptor)]
        if request.unit is not None and request.unit not in tables:
            raise _refused(
                RefusalCode.UNKNOWN_TABLE, "/unit", "The release has no such table", tables
            )
        return AnalysisListing(
            analyses=entries,
            dataset=release.dataset,
            release=release.out,
            unit=request.unit,
            applicable=analyses.applicable(
                descriptors,
                dataset=release.dataset,
                manifest=release.manifest,
                unit=request.unit,
            ),
        )

    # --- Resources (§11.1, D279) ---------------------------------------------------------------

    def _concepts(self) -> list[ConceptDescriptor]:
        packs = [] if self.registry is None else self.registry.concepts()
        return sorted([*CORE_CONCEPTS, *packs], key=lambda concept: concept.id)

    def resources(self) -> list[tuple[str, str]]:
        """The resources listed, as (URI, name): each dataset's descriptor in its latest
        release, every concept and every model card, at most ``MAX_RESOURCES``."""
        found: list[tuple[str, str]] = []
        for dataset in self.store.datasets():
            latest = self.store.latest(dataset)
            if latest is not None:
                found.append((f"aibi://dataset/{dataset}@{latest.label}/dataset", dataset))
        found.extend((f"aibi://concept/{c.id}", c.id) for c in self._concepts())
        found.extend(
            (f"aibi://analysis/{a.id}@{a.entry.version}", a.id) for a in self.analyses.all()
        )
        found.extend((f"aibi://model/{m.id}", m.id) for m in self.models)
        return found[:MAX_RESOURCES]

    def read_resource(self, uri: str) -> str:
        """A descriptor, in RFC 8785 form, by its resource URI."""
        if len(uri) > MAX_URI:
            raise _refused(RefusalCode.NOT_FOUND, None, "No resource has so long a URI")
        if (match := _DATASET_URI.fullmatch(uri)) is not None:
            descriptor = match["descriptor"]
            if re.fullmatch(DESCRIPTOR_ID_RE, descriptor) is None or "__" in descriptor:
                raise _refused(RefusalCode.NOT_FOUND, None, "Not a descriptor id of a release")
            given = match["release"]
            release = self._release(
                match["dataset"], int(given) if given.isdigit() else given, path=None
            )
            with self.store.pin() as pin:
                self._held(pin, release, path=None)
                descriptors = self.store.descriptors(release.manifest)
                k = self._k(pin, release, descriptors)
            found = next((d for d in descriptors if d.id == descriptor), None)
            if found is None:
                raise _refused(
                    RefusalCode.NOT_FOUND, None, "The release holds no descriptor of that id"
                )
            return canonical(_served(found, k)).decode("utf-8")
        if (match := _CONCEPT_URI.fullmatch(uri)) is not None:
            concept = next((c for c in self._concepts() if c.id == match["id"]), None)
            if concept is None:
                known = [c.id for c in self._concepts()]
                raise _refused(RefusalCode.NOT_FOUND, None, "No such concept", known)
            return canonical(_dumped(concept)).decode("utf-8")
        if (match := _MODEL_URI.fullmatch(uri)) is not None:
            model = next((m for m in self.models if m.id == match["id"]), None)
            if model is None:
                known = [m.id for m in self.models]
                raise _refused(RefusalCode.NOT_FOUND, None, "No such model card", known)
            return canonical(_dumped(model)).decode("utf-8")
        if (match := _ANALYSIS_URI.fullmatch(uri)) is not None:
            analyses = self.analyses.all()
            analysis = next(
                (
                    a
                    for a in analyses
                    if a.id == match["id"] and a.entry.version == match["version"]
                ),
                None,
            )
            if analysis is None:
                known = [f"{a.id}@{a.entry.version}" for a in analyses]
                raise _refused(RefusalCode.NOT_FOUND, None, "No such analysis version", known)
            return canonical(_dumped(analysis.entry)).decode("utf-8")
        raise _refused(
            RefusalCode.NOT_FOUND,
            None,
            "Not an aibi resource URI",
            [template for template, _ in RESOURCE_TEMPLATES],
        )


@dataclass(frozen=True)
class _Public:
    """What the public queue shows of each item (``curation_queue``): a note's reference, and,
    under a disclosure setting, fixed words, no evidence and suppressed counts."""

    references: References
    disclosed: Mapping[str, DisclosedTable]
    k: int | None

    def __call__(self, kind: str, position: int, item: Output) -> Output:
        if isinstance(item, QueueNote):
            return self._note(position, item)
        if self.k is not None and isinstance(item, QueueField | QueueProposal):
            return item.model_copy(update={"evidence": None})
        return item

    def _note(self, position: int, note: QueueNote) -> QueueNote:
        update: dict[str, object] = {}
        if note.count is not None:
            update["reference"] = self.references.of("dataset", "report", position)
        if self.k is not None:
            update["message"] = [text(NOTE_TEXT[note.kind])]
            if note.count is None:
                update["rows"] = []
            elif not _shown(note, self.disclosed):
                update.update(
                    count=None,
                    rows=[],
                    not_estimable={"/count": NotEstimableReason.SUPPRESSED},
                )
        return note.model_copy(update=update)


def _hidden(notes: Sequence[QueueNote]) -> bool:
    """Whether the queue holds a note whose count was suppressed."""
    return any(note.count is None and note.not_estimable for note in notes)


def _shown(note: QueueNote, disclosed: Mapping[str, DisclosedTable]) -> bool:
    """Whether a note's count is shown under a disclosure setting (``curation_queue``)."""
    if note.kind != "unparsed" or note.subject is None or "." not in note.subject:
        return False
    table, column = note.subject.split(".", 1)
    found = disclosed.get(table)
    if found is None or column not in found.states:
        return False
    return found.states[column][ObservationState.UNKNOWN.value] == note.count


def _matches(entry: Entry, request: SearchCatalog) -> bool:
    if request.text is not None and not all(
        word in entry.words for word in request.text.casefold().split()
    ):
        return False
    if request.domain_tags is not None:
        tags = {tag.casefold() for tag in entry.domain_tags}
        if not all(tag.casefold() in tags for tag in request.domain_tags):
            return False
    if request.data_use is not None:
        codes = {(system, code) for system, code, _ in entry.data_use}
        if not all((given.system, given.code) in codes for given in request.data_use):
            return False
    if request.concepts is not None and not set(request.concepts) <= set(entry.concepts):
        return False
    if request.roles is not None:
        roles = {table.role for table in entry.tables}
        if not set(request.roles) <= roles:
            return False
    if request.min_rows is not None:
        least = request.min_rows
        if not any(
            t.rows is not None and t.rows >= least for t in entry.tables if t.role != "coverage"
        ):
            return False
    if request.completeness is not None and not _complete(entry, request):
        return False
    if request.facets is not None:
        for name, values in request.facets.items():
            if not set(values) <= set(entry.facets.get(name, ())):
                return False
    return True


def _complete(entry: Entry, request: SearchCatalog) -> bool:
    """Whether some column's PRESENT count is at least ``min_present`` of its table's rows,
    compared as rationals, the threshold being the decimal its shortest spelling writes, so that
    7 of 100 meets 0.07 (D274)."""
    wanted = request.completeness
    assert wanted is not None
    least = Fraction(repr(wanted.min_present))
    for table in entry.tables:
        if not table.rows:
            continue
        for column in table.columns:
            if wanted.concept is not None and wanted.concept not in column.concepts:
                continue
            if wanted.datatype is not None and column.datatype != wanted.datatype:
                continue
            if column.present is not None and Fraction(column.present, table.rows) >= least:
                return True
    return False


__all__ = [
    "DEADLINE",
    "RESOURCE_TEMPLATES",
    "Catalog",
    "Deadline",
    "ToolRefused",
    "refusals_of",
    "within",
]
