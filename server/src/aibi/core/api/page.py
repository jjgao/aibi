"""The read-only catalogue page (SPEC §11.1, §14, §15 M1; D311–D314).

Two pages, rendered by the server as HTML without a script, a form or a remote resource (D311):

- ``GET /`` lists the latest published release of every dataset, ``HITS`` at a time from
  ``?offset=``: its label, name, description, tags, data-use terms, packs and concepts, and its
  tables with their row counts.
- ``GET /datasets/<dataset>`` describes the latest published release of one dataset, its columns
  ``COLUMNS`` at a time from ``?columns_offset=``, in at most ``TABLES`` tables: the dataset
  descriptor and its standing caveats on every page, and for the tables whose columns the page
  lists, their part of the table graph (each relationship from them, with its child and parent
  columns and its cardinality), each one's descriptor, row count and columns, and the
  relationship, coverage and endpoint descriptors that start from them, every field with its
  curation status (A5).

Each runs its tool, ``search_catalog`` or ``describe_dataset``, through the server's tool calls
(``Calls.run``, as ``Calls.tool`` runs a tool): the same service function, disclosure settings,
places, limits and refusals as MCP and ``POST /api/tools/<name>``, so the page shows what an agent
is shown and nothing more (§1.1 goal 7, §11.1). The page is rendered in the call's worker thread
too, while it holds its place, never on the event loop. Every count is shown with its ``stat:``
reference, and a suppressed one as suppressed (A1, §8.4). A refusal is a page of its own with the
refusal's status (D265), ``Retry-After`` with a 503 (``errors.refused_page``), as every refusal at
a page path is, request protection's and routing's included (``chrome``). A query parameter the
page does not take, one given twice, or an offset that is not a number of at most 16 digits and
at most ``MAX_OFFSET`` is ``INVALID_VALUE``, naming the parameter and never echoing what was
given, and so is an address whose dataset is no dataset id, in plain words; the tool's refusals
are shown without their paths, which point into a request the browser never sent. Text from data
is cut as a descriptor member's value is, and lists of tags and facet values after ``LISTED``
members. The pages take ``GET`` alone; a link from another site may open them (D314).

All text is written by ``api.markup`` (D312), from data in ``<bdi>``s; links are built from the
server's root path, a dataset id the tool validated, a table id as a fragment and numbers alone.
Responses carry ``chrome.PAGE_HEADERS`` (D313); request protection adds its headers, and counts
each request at a page path against the client's ``page`` rate (D314). A curation screen is M5's,
on the operator router.
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import LiteralString, cast
from urllib.parse import quote

from fastapi import APIRouter, Request
from pydantic import JsonValue, TypeAdapter, ValidationError
from starlette.responses import Response

from aibi.core.api.chrome import (
    CATALOGUE_PATH,
    DATASETS_PREFIX,
    PAGE_HEADERS,
    STYLE,
    header,
    root_of,
)
from aibi.core.api.errors import refused_page
from aibi.core.api.markup import Child, Markup, data, document, element, joined, segments
from aibi.core.catalog.tools import BY_NAME, call
from aibi.core.mcp.calls import Calls
from aibi.core.schema.catalog import (
    MAX_HITS,
    MAX_OFFSET,
    CatalogHit,
    CatalogHits,
    DatasetDescription,
    DisclosureOut,
    OntologyTerm,
    ReleaseOut,
    StatCount,
    TableOut,
)
from aibi.core.schema.caveats import Caveat
from aibi.core.schema.ids import DatasetId
from aibi.core.schema.jsonio import MISSING, escape_token, lookup
from aibi.core.schema.output import Output, text
from aibi.core.schema.refusals import Refusal, RefusalCode

HITS = MAX_HITS
"""Datasets the catalogue lists at a time."""
COLUMNS = 500
"""Columns a dataset's page lists at a time."""
TABLES = 50
"""Tables a dataset's page shows at a time; the next page starts at the columns of the next."""
LISTED = 64
"""Members of a list from data (tags, facet values) shown before the rest are counted."""
VALUE_CHARACTERS = 2_000
"""Characters of a descriptor field's value shown; the rest is cut and marked (D312)."""
_OFFSET = re.compile(r"^[0-9]{1,16}$")
_ENVELOPE = ("kind", "id", "version", "label", "fields", "curation")
_DATASET_ID: TypeAdapter[str] = TypeAdapter(DatasetId)


# --- Answers ----------------------------------------------------------------------------------


def _page(body: bytes) -> Response:
    return Response(body, media_type="text/html", headers=dict(PAGE_HEADERS))


def _invalid(message: str, alternatives: Sequence[str] = ()) -> Refusal:
    return Refusal(
        code=RefusalCode.INVALID_VALUE,
        path=None,
        message=[text(message)],
        alternatives=[text(alternative) for alternative in alternatives],
    )


def _offset(request: Request, name: str) -> int | Refusal:
    """The page's one query parameter, ``name``, a number of items to skip; 0 when absent."""
    given = request.query_params.multi_items()
    if any(key != name for key, _ in given):
        return _invalid("This page takes one query parameter", [name])
    values = [value for _, value in given]
    if not values:
        return 0
    if len(values) > 1 or _OFFSET.fullmatch(values[0]) is None or int(values[0]) > MAX_OFFSET:
        return _invalid(
            f"The query parameter {name} is given once, as a number from 0 to {MAX_OFFSET}"
        )
    return int(values[0])


async def _rendered(
    calls: Calls,
    request: Request,
    name: str,
    arguments: dict[str, JsonValue],
    render: Callable[[Output], bytes],
) -> bytes | list[Refusal]:
    """The page ``render`` makes of the tool's answer, both in the call's worker thread while it
    holds its place; or the refusals, without their paths, which point into a request the
    browser never sent (the page's own refusals name the parameter instead)."""
    client = "" if request.client is None else request.client.host
    body = json.dumps(arguments).encode("utf-8")
    tool = BY_NAME[name]

    def work() -> bytes | list[Refusal]:
        found = call(calls.catalog, tool, body, client=calls.client_key(client))
        if isinstance(found, list):
            return [refusal.model_copy(update={"path": None}) for refusal in found]
        return render(found)

    return await calls.run(work, client=client)


def _dataset(given: str) -> Refusal | None:
    try:
        _DATASET_ID.validate_python(given)
    except ValidationError:
        return _invalid(
            "The address names no dataset: a dataset's id is a lower-case letter, then at most "
            "63 lower-case letters, digits and single underscores"
        )
    return None


def _cut(text: str) -> Markup:
    """Text from data, cut at ``VALUE_CHARACTERS`` and marked when cut."""
    return data(text[:VALUE_CHARACTERS], truncated=len(text) > VALUE_CHARACTERS)


# --- Parts ------------------------------------------------------------------------------------


def count(stat: StatCount) -> Markup:
    """A statistic's count with its reference; one that is null as suppressed, or with its
    reasons when they are others."""
    if stat.count is None:
        reasons = sorted({str(reason) for reason in (stat.not_estimable or {}).values()})
        said = (
            "suppressed"
            if reasons in ([], ["suppressed"])
            else f"not estimable ({', '.join(reasons)})"
        )
        shown = element("span", said, attributes={"class": "suppressed"})
    else:
        shown = element("span", str(stat.count), attributes={"class": "count"})
    return joined([shown, element("code", data(stat.reference), attributes={"class": "ref"})], " ")


def _release(release: ReleaseOut, disclosure: DisclosureOut) -> Markup:
    k = disclosure.min_cell_count
    parts: list[Child] = [
        f"release @{release.label} ({release.status}) · manifest ",
        element("code", release.manifest),
        " · disclosure: ",
        "none"
        if k is None
        else f"minimum cell count {k}: counts below it, and counts that would reveal them, "
        "are suppressed (§8.4)",
    ]
    return element("p", *parts, attributes={"class": "meta"})


def _caveats(caveats: Sequence[Caveat]) -> Markup:
    if not caveats:
        return Markup("")
    items: list[Child] = []
    for caveat in caveats:
        severity = str(caveat.severity)
        parts: list[Child] = [
            element("strong", caveat.code),
            f" ({severity}): ",
            segments(caveat.message),
        ]
        if caveat.affects:
            affects = joined((element("code", data(pointer)) for pointer in caveat.affects), ", ")
            parts += [
                element("br"),
                element("span", "Affects: ", affects, attributes={"class": "meta"}),
            ]
        items.append(element("li", *parts, attributes={"class": f"caveat {severity}"}))
    return joined([element("h2", "Caveats"), element("ul", *items)])


def _before(offset: int, total: int, size: int) -> int | None:
    """Where the page before one from ``offset`` starts: at most the last page's start."""
    return None if offset == 0 else max(0, min(offset, total) - size)


def _pages(root: str, path: str, name: str, before: int | None, after: int | None) -> Markup:
    links: list[Child] = []
    for label, offset in (("Previous", before), ("Next", after)):
        if offset is None:
            continue
        target = f"{root}{path}" + ("" if offset == 0 else f"?{name}={offset}")
        links.append(element("a", label, attributes={"href": target}))
    if not links:
        return Markup("")
    return element("nav", *links, attributes={"class": "pages"})


def _counted(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _table(heads: Sequence[str], rows: Sequence[Sequence[Child]]) -> Markup:
    head = element("tr", *(element("th", name) for name in heads))
    body = [element("tr", *(element("td", cell) for cell in row)) for row in rows]
    return element("table", element("thead", head), element("tbody", *body))


# --- Descriptors ------------------------------------------------------------------------------


def _value(value: object) -> Markup:
    if value is MISSING:
        return element("span", "no value", attributes={"class": "absent"})
    written = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return element("span", _cut(written), attributes={"class": "value"})


def _pointers(descriptor: Mapping[str, JsonValue]) -> list[str]:
    """The descriptor's members, as JSON Pointers: its label, its other envelope members, each
    extension and each field, in its order, then any other pointer its curation names."""
    found = ["/label"]
    for key, value in descriptor.items():
        if key in _ENVELOPE:
            continue
        if key == "extensions" and isinstance(value, dict):
            found += [f"/extensions/{escape_token(pack)}" for pack in value]
        else:
            found.append(f"/{escape_token(key)}")
    fields = descriptor.get("fields")
    if isinstance(fields, dict):
        found += [f"/fields/{escape_token(key)}" for key in fields]
    curation = descriptor.get("curation")
    if isinstance(curation, dict):
        found += sorted(pointer for pointer in curation if pointer not in found)
    return found


def descriptor_block(descriptor: Mapping[str, JsonValue], level: LiteralString = "h3") -> Markup:
    """A descriptor as a table of its members (``_pointers``), each with its value, its curation
    status and who set it (A5)."""
    curation = descriptor.get("curation")
    entries = cast(dict[str, JsonValue], curation) if isinstance(curation, dict) else {}
    rows: list[list[Child]] = []
    for pointer in _pointers(descriptor):
        entry = entries.get(pointer)
        status: Child = ""
        by: Child = ""
        if isinstance(entry, dict):
            members = cast(dict[str, JsonValue], entry)
            status = data(str(members.get("status", "")))
            by = data(str(members.get("by", "")))
        rows.append(
            [element("code", data(pointer)), _value(lookup(dict(descriptor), pointer)), status, by]
        )
    heading = element(
        level,
        data(str(descriptor.get("label", ""))),
        " ",
        element("code", data(str(descriptor.get("id", ""))), attributes={"class": "ref"}),
    )
    kind = element("p", f"{descriptor.get('kind', '')} descriptor", attributes={"class": "meta"})
    fields = _table(("Member", "Value", "Status", "By"), rows)
    return element("div", heading, kind, fields, attributes={"class": "descriptor"})


def _descriptors(title: str, found: Sequence[Mapping[str, JsonValue]]) -> Markup:
    if not found:
        return Markup("")
    return joined([element("h2", title), *(descriptor_block(descriptor) for descriptor in found)])


# --- The catalogue ----------------------------------------------------------------------------


def _term(term: OntologyTerm) -> Markup:
    code = joined([_cut(term.system.data), ":", _cut(term.code.data)])
    return joined([_cut(term.label.data), " (", element("code", code), ")"])


def _some(values: Sequence[str]) -> Markup:
    """Texts from data, the first ``LISTED`` of them, the rest counted."""
    shown: list[Child] = [_cut(value) for value in values[:LISTED]]
    if len(values) > LISTED:
        shown.append(f"and {len(values) - LISTED} more")
    return joined(shown, ", ")


def _hit(root: str, hit: CatalogHit) -> Markup:
    target = f"{root}{DATASETS_PREFIX}/{quote(hit.dataset, safe='')}"
    parts: list[Child] = [
        element("h2", element("a", _cut(hit.label.data), attributes={"href": target})),
        element(
            "p",
            "dataset ",
            element("code", hit.dataset),
            attributes={"class": "meta"},
        ),
        _release(hit.release, hit.disclosure),
    ]
    if hit.name is not None:
        parts.append(element("p", _cut(hit.name.data)))
    if hit.description is not None:
        parts.append(element("p", _cut(hit.description.data), attributes={"class": "text"}))
    listed: list[tuple[str, Markup]] = [
        ("Domain tags", _some([tag.data for tag in hit.domain_tags])),
        ("Data use", joined((_term(term) for term in hit.data_use), ", ")),
        ("Packs", joined((element("code", pack) for pack in hit.packs), ", ")),
        ("Concepts", joined((element("code", concept) for concept in hit.concepts), ", ")),
    ]
    listed += [
        (f"Facet {facet}", _some([value.data for value in values]))
        for facet, values in hit.facets.items()
    ]
    facts = [element("li", f"{label}: ", shown) for label, shown in listed if shown.html]
    if facts:
        parts.append(element("ul", *facts))
    rows: list[list[Child]] = [
        [
            element("code", table.id),
            _cut(table.label.data),
            "" if table.grain is None else _cut(table.grain.data),
            table.role or "",
            count(table.rows),
        ]
        for table in hit.tables
    ]
    parts.append(_table(("Table", "Label", "Grain", "Role", "Rows"), rows))
    if hit.tables_left_out:
        more = f"and {hit.tables_left_out} more tables, which the dataset's page lists"
        parts.append(element("p", more, attributes={"class": "meta"}))
    return element("article", *parts)


def catalogue_page(root: str, hits: CatalogHits, offset: int) -> bytes:
    """The catalogue: ``search_catalog``'s hits from ``offset``."""
    shown = len(hits.hits)
    if hits.total == 0:
        summary = "No dataset has a published release yet."
    elif shown == 0:
        summary = f"{_counted(hits.total, 'dataset')}; none from {offset + 1} on."
    else:
        summary = f"{_counted(hits.total, 'dataset')}; {offset + 1} to {offset + shown} listed."
    before = _before(offset, hits.total, HITS)
    return document(
        "Catalogue · aibi",
        STYLE,
        header(root),
        element("h1", "Catalogue"),
        element(
            "p",
            summary,
            " The latest published release of each dataset. Text in the descriptors comes from the "
            "datasets and is shown as it is.",
            attributes={"class": "meta"},
        ),
        _caveats(hits.caveats),
        *(_hit(root, hit) for hit in hits.hits),
        _pages(root, CATALOGUE_PATH, "offset", before, hits.next_offset),
    )


# --- A dataset --------------------------------------------------------------------------------


def _anchor(table: str) -> str:
    return "table-" + quote(table, safe="")


def _fields(descriptor: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    found = descriptor.get("fields")
    return cast(dict[str, JsonValue], found) if isinstance(found, dict) else {}


def _graph(described: DatasetDescription, shown: frozenset[str]) -> Markup:
    """The part of the table graph the page's tables start: the tables, and each edge whose
    child is one of them."""
    by_id = {str(found.get("id", "")): found for found in described.relationships}

    def columns(relationship: str, member: str) -> Child:
        listed = _fields(by_id.get(relationship, {})).get(member)
        if not isinstance(listed, list):
            return ""
        names = [str(name) for name in cast(list[JsonValue], listed)]
        return joined([" (", joined((data(name) for name in names), ", "), ")"])

    def table(name: str) -> Markup:
        return element("a", element("code", name), attributes={"href": f"#{_anchor(name)}"})

    rows: list[list[Child]] = [
        [
            element("code", edge.relationship),
            joined([table(edge.child), columns(edge.relationship, "child_columns")]),
            joined([element("code", edge.parent), columns(edge.relationship, "parent_columns")]),
            edge.cardinality,
        ]
        for edge in described.graph.edges
        if edge.child in shown
    ]
    here = [name for name in described.graph.tables if name in shown]
    tables: list[Child] = [joined((table(name) for name in here), ", ")]
    if len(here) < len(described.graph.tables):
        tables.append(f"; {len(described.graph.tables) - len(here)} more on other pages")
    parts: list[Child] = [element("h2", "Table graph"), element("p", "Tables: ", *tables)]
    if rows:
        parts.append(
            _table(("Relationship", "Child (columns)", "Parent (columns)", "Cardinality"), rows)
        )
    else:
        parts.append(
            element("p", "No relationship starts from these tables.", attributes={"class": "meta"})
        )
    return joined(parts)


def _table_part(table: TableOut) -> Markup:
    identifier = str(table.descriptor.get("id", ""))
    rows: list[list[Child]] = [
        [
            element("code", data(column.id)),
            _cut(column.label.data),
            column.datatype or "",
            "yes" if column.identifier else "",
        ]
        for column in table.columns
    ]
    parts: list[Child] = [
        element("p", "Rows: ", count(table.rows)),
        descriptor_block(table.descriptor, "h4"),
    ]
    if rows:
        parts.append(_table(("Column", "Label", "Datatype", "Identifier"), rows))
    return element(
        "section",
        element(
            "h3",
            _cut(str(table.descriptor.get("label", ""))),
            " ",
            element("code", data(identifier), attributes={"class": "ref"}),
        ),
        *parts,
        attributes={"id": _anchor(identifier)},
    )


def dataset_page(root: str, described: DatasetDescription, offset: int) -> bytes:
    """A dataset: ``describe_dataset``'s description, its columns from ``offset`` in at most
    ``TABLES`` tables, and what starts from those tables (module docstring)."""
    descriptor = described.descriptor
    label = str(descriptor.get("label", described.dataset))
    if described.columns_total == 0:
        tables = described.tables[:TABLES]
    else:
        tables = [table for table in described.tables if table.columns][:TABLES]
    shown = frozenset(str(table.descriptor.get("id", "")) for table in tables)
    listed = sum(len(table.columns) for table in tables)
    total = described.columns_total
    if total == 0:
        summary = "No columns."
    elif listed == 0:
        summary = f"{_counted(total, 'column')}; none from {offset + 1} on."
    else:
        summary = f"{_counted(total, 'column')}; {offset + 1} to {offset + listed} listed."
    if len(tables) < len(described.tables):
        summary += f" {len(tables)} of {_counted(len(described.tables), 'table')} shown here."
    after = offset + listed if listed and offset + listed < total else None
    before = _before(offset, total, COLUMNS)
    children = {
        str(found.get("id", "")): str(_fields(found).get("child_table", ""))
        for found in described.relationships
    }
    relationships = [d for d in described.relationships if _fields(d).get("child_table") in shown]
    coverage = [
        d
        for d in described.coverage
        if children.get(str(_fields(d).get("relationship", "")), "") in shown
        or (offset == 0 and str(_fields(d).get("relationship", "")) not in children)
    ]
    endpoints = [
        d
        for d in described.endpoints
        if _fields(d).get("table") in shown or (offset == 0 and "table" not in _fields(d))
    ]
    path = f"{DATASETS_PREFIX}/{quote(described.dataset, safe='')}"
    return document(
        f"{label} · aibi",
        STYLE,
        header(root),
        element("h1", _cut(label)),
        element("p", "dataset ", element("code", described.dataset), attributes={"class": "meta"}),
        _release(described.release, described.disclosure),
        _caveats(described.caveats),
        element("h2", "Dataset descriptor"),
        descriptor_block(descriptor),
        _graph(described, shown),
        element("h2", "Tables"),
        element("p", summary, attributes={"class": "meta"}),
        *(_table_part(table) for table in tables),
        _pages(root, path, "columns_offset", before, after),
        _descriptors("Relationships", relationships),
        _descriptors("Coverage", coverage),
        _descriptors("Endpoints", endpoints),
    )


# --- The router -------------------------------------------------------------------------------


def _catalogue_of(root: str, offset: int) -> Callable[[Output], bytes]:
    def render(found: Output) -> bytes:
        if not isinstance(found, CatalogHits):
            raise TypeError("search_catalog answers with its hits")
        return catalogue_page(root, found, offset)

    return render


def _dataset_of(root: str, offset: int) -> Callable[[Output], bytes]:
    def render(found: Output) -> bytes:
        if not isinstance(found, DatasetDescription):
            raise TypeError("describe_dataset answers with its description")
        return dataset_page(root, found, offset)

    return render


def page_router(calls: Calls) -> APIRouter:
    """``GET /`` and ``GET /datasets/<dataset>`` (module docstring)."""
    router = APIRouter()

    @router.get(CATALOGUE_PATH, include_in_schema=False)
    async def catalogue(request: Request) -> Response:
        root = root_of(request.scope)
        offset = _offset(request, "offset")
        if isinstance(offset, Refusal):
            return refused_page(root, [offset])
        arguments: dict[str, JsonValue] = {"offset": offset, "limit": HITS}
        found = await _rendered(
            calls, request, "search_catalog", arguments, _catalogue_of(root, offset)
        )
        return refused_page(root, found) if isinstance(found, list) else _page(found)

    @router.get(DATASETS_PREFIX + "/{dataset}", include_in_schema=False)
    async def dataset(request: Request, dataset: str) -> Response:
        root = root_of(request.scope)
        offset = _offset(request, "columns_offset")
        if isinstance(offset, Refusal):
            return refused_page(root, [offset])
        named = _dataset(dataset)
        if named is not None:
            return refused_page(root, [named])
        arguments: dict[str, JsonValue] = {
            "dataset": dataset,
            "columns_offset": offset,
            "columns_limit": COLUMNS,
        }
        found = await _rendered(
            calls, request, "describe_dataset", arguments, _dataset_of(root, offset)
        )
        return refused_page(root, found) if isinstance(found, list) else _page(found)

    return router


__all__ = [
    "COLUMNS",
    "HITS",
    "LISTED",
    "TABLES",
    "VALUE_CHARACTERS",
    "catalogue_page",
    "count",
    "dataset_page",
    "descriptor_block",
    "page_router",
]
