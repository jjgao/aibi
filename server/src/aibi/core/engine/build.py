"""Releases built in code from plain values, for tests and examples.

The helpers build release descriptors whose every field with a value has a curation entry,
``asserted`` by an operator unless another status is given (§5.1). ``release`` checks the
descriptors as a release is checked (§13.2), coverages' parent scopes resolved against it, and
builds its tables from rows of plain values, typed by their columns:

- a ``Cell`` is kept as it is, and ``None`` is an empty cell (UNKNOWN);
- a string that is one of the column's missing codes takes that code's state (§6.2);
- a list or tuple is a list cell's items, each typed the same way;
- for date and datetime columns, a string is read as an ISO date or an RFC 3339 date-time;
- any other value is PRESENT as it is.

Relationship ids are ``rel:<child>.<role>``, or ``rel:<child>.<columns joined by +>`` without a
role; a coverage descriptor takes its relationship's id with ``cov:``.
"""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast

from pydantic import JsonValue, TypeAdapter

from aibi.core.engine.data import EMPTY, PRESENT, Cell, Release, ReleaseError, Row, Table
from aibi.core.engine.resolve import check_parent_scopes
from aibi.core.schema.descriptors import ColumnDescriptor, Descriptor
from aibi.core.schema.jsonio import escape_token
from aibi.core.schema.refusals import Refusal
from aibi.core.schema.release import check_release
from aibi.core.schema.semantics import ObservationState

AT = "2026-01-01T00:00:00Z"
"""When every fixture status was set."""

_BY = {
    "asserted": "operator:fixture",
    "imported": "importer:fixture@1.0",
    "imported_default": "importer:fixture@1.0",
    "proposed": "agent:fixture",
}
_ADAPTER: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)


def descriptor(
    kind: str,
    id: str,
    fields: Mapping[str, Any],
    *,
    status: str = "asserted",
    statuses: Mapping[str, str] | None = None,
) -> Descriptor:
    """A release descriptor with one curation entry per field with a value: ``status``, or the
    status ``statuses`` gives the field by name."""
    written: dict[str, Any] = {
        "kind": kind,
        "id": id,
        "version": 1,
        "label": id,
        "fields": dict(fields),
    }
    curation: dict[str, JsonValue] = {"/label": {"status": status, "by": _BY[status], "at": AT}}
    for name in fields:
        chosen = (statuses or {}).get(name, status)
        curation[f"/fields/{escape_token(name)}"] = {"status": chosen, "by": _BY[chosen], "at": AT}
    written["curation"] = curation
    return _ADAPTER.validate_python(written)


def dataset(**fields: Any) -> Descriptor:
    return descriptor("dataset", "dataset", {"name": "Fixture", **fields})


def table(
    id: str, key: Sequence[str] | None = None, role: str = "entity", **fields: Any
) -> Descriptor:
    given: dict[str, Any] = {"role": role, **fields}
    if key is not None:
        given["primary_key"] = list(key)
    return descriptor("table", id, given)


def column(id: str, datatype: str | None, *, status: str = "asserted", **fields: Any) -> Descriptor:
    """A column; ``datatype`` ``None`` leaves it undeclared."""
    given: dict[str, Any] = dict(fields)
    if datatype is not None:
        given = {"datatype": datatype, **given}
        if datatype == "list<category>" and "list_syntax" not in given:
            given["list_syntax"] = {"format": "delimited", "delimiter": ";"}
    return descriptor("column", id, given, status=status)


def relationship_id(child: str, columns: Sequence[str], role: str | None = None) -> str:
    return f"rel:{child}.{role if role is not None else '+'.join(columns)}"


def relationship(
    child: str,
    columns: Sequence[str],
    parent: str,
    parent_columns: Sequence[str] | None = None,
    *,
    role: str | None = None,
    one_to_one: bool = False,
    status: str = "asserted",
) -> Descriptor:
    """A relationship; the parent's key columns are named like the child's unless given."""
    fields: dict[str, Any] = {
        "child_table": child,
        "child_columns": list(columns),
        "parent_table": parent,
        "parent_columns": list(columns if parent_columns is None else parent_columns),
        "cardinality": "one-to-one" if one_to_one else "many-to-one",
    }
    if role is not None:
        fields["role"] = role
    return descriptor("relationship", relationship_id(child, columns, role), fields, status=status)


def coverage(
    relationship: str,
    parents: Any = None,
    *,
    record_filter: Mapping[str, Sequence[str]] | None = None,
    parent_scope: Any = None,
    status: str = "asserted",
    statuses: Mapping[str, str] | None = None,
) -> Descriptor:
    """The coverage of ``relationship`` (its id); members left ``None`` are undeclared."""
    fields: dict[str, Any] = {"relationship": relationship}
    if parents is not None:
        fields["parents"] = parents
    if record_filter is not None:
        fields["record_filter"] = {name: list(values) for name, values in record_filter.items()}
    if parent_scope is not None:
        fields["parent_scope"] = parent_scope
    return descriptor(
        "coverage",
        "cov:" + relationship.removeprefix("rel:"),
        fields,
        status=status,
        statuses=statuses,
    )


def cell(value: object, datatype: str | None, codes: Mapping[str, str] | None = None) -> Cell:
    """A cell of a column of ``datatype`` with ``codes`` as its missing codes, from a plain
    value (see the module's docstring)."""
    if isinstance(value, Cell):
        return value
    if value is None:
        return EMPTY
    if isinstance(value, str) and codes and value in codes:
        return Cell(ObservationState(codes[value]))
    if datatype == "list<category>" and isinstance(value, list | tuple):
        items = cast(Sequence[object], value)
        return Cell(PRESENT, tuple(cell(item, "category", codes) for item in items))
    if isinstance(value, str) and datatype == "date":
        return Cell(PRESENT, date.fromisoformat(value))
    if isinstance(value, str) and datatype == "datetime":
        return Cell(PRESENT, datetime.fromisoformat(value))
    return Cell(PRESENT, cast(Any, value))


def release(
    descriptors: Sequence[Descriptor],
    rows: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    dataset: str = "d",
    manifest: str | None = None,
    check: bool = True,
) -> Release:
    """A release of ``dataset`` from its descriptors and each table's rows. With ``check``,
    descriptors that would not make a valid release are refused (``ReleaseError``)."""
    if check:
        refusals = check_release(descriptors)
        if refusals:
            raise ReleaseError("; ".join(_plain(refusal) for refusal in refusals))
    columns = {
        descriptor.id: descriptor
        for descriptor in descriptors
        if isinstance(descriptor, ColumnDescriptor)
    }
    tables: dict[str, Table] = {}
    for name, given in rows.items():
        built: list[Row] = []
        for row in given:
            cells: dict[str, Cell] = {}
            for column_name, value in row.items():
                found = columns.get(f"{name}.{column_name}")
                fields = None if found is None else found.fields
                cells[column_name] = cell(
                    value,
                    None if fields is None else fields.datatype,
                    None if fields is None else fields.missing_codes,
                )
            built.append(cells)
        tables[name] = Table(name, tuple(built))
    digest = manifest or "sha256:" + hashlib.sha256(dataset.encode()).hexdigest()
    made = Release(dataset, digest, tuple(descriptors), tables)
    if check:
        refusals = check_parent_scopes(made)
        if refusals:
            raise ReleaseError("; ".join(_plain(refusal) for refusal in refusals))
    return made


def _plain(refusal: Refusal) -> str:
    parts = [segment.model_dump() for segment in refusal.message]
    return f"{refusal.code} at {refusal.path}: " + "".join(
        str(part.get("text", part.get("data", ""))) for part in parts
    )


__all__ = [
    "AT",
    "cell",
    "column",
    "coverage",
    "dataset",
    "descriptor",
    "relationship",
    "relationship_id",
    "release",
    "table",
]
