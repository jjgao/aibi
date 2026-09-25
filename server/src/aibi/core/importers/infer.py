"""What the file importer infers from the data (SPEC §5.3–§5.6, §13.1, D227–D229).

Columns (D228), on each cell's canonical string (§12.2):

- **Missing codes.** Only conventional tokens that occur in the column are declared, all as
  UNKNOWN: ``CONVENTIONAL``. Any other token that does not parse is left an undeclared missing
  code, UNKNOWN, and counted in the import report.
- **Datatype.** The first of integer, number, date, datetime and boolean that parses every
  non-missing cell wins. Only when none does, the first that parses all but at most one in
  twenty of at least 20 such cells wins, and the cells left stay UNKNOWN and are reported; but
  never for a column that is a key or a foreign key typed as a string, since a stray token there
  is an id (a column whose tolerated datatype no key has, a number say, is neither). Boolean
  counts only when a cell is a word (``true``, ``yes``, ``f``…), so a column of 0 and 1 is an
  integer. Otherwise the column is a string, or has no datatype if no cell is PRESENT.
- **Lists.** A string column is ``list<category>`` when every PRESENT cell is a JSON array of
  scalars (``json``), or else a Python list or tuple of scalars (``python``), or when ``;`` or
  else ``|`` is in at least 2 cells and splitting on it gives fewer distinct items than there
  are distinct cells (``delimited``).
- **Categories.** A string column that is not a key or a foreign key is a ``category`` when it
  has at most 50 distinct values and they are at most half of its PRESENT cells.
- **Identifiers.** A string column that is not a key, a foreign key or a category, whose PRESENT
  cells (at least 20) are all distinct, is proposed as an identifier. From 2 to 19 is too few to
  tell, and one note per table names such columns.

Tables (D229):

- **Key.** The first column in source order, of datatype string or integer, that is PRESENT and
  distinct in every row. Failing that, the first pair of foreign-key columns that is PRESENT and
  distinct together in every row (a link table). Failing that, no key, and a note says so.
- **Relationships**, by containment: a string or integer column of one table, with a PRESENT
  cell, every one of whose PRESENT values (as canonical strings) is a key of another table's
  single-column key. Integers match only integers, and only when the column's id is the key's
  id, ``<parent table>_<key>`` or ``<parent table in the singular>_<key>`` (a trailing ``s``
  dropped, or ``es`` after s, x, z, ch or sh: ``book_id`` for ``books.id``), since small numbers
  are contained by chance. A column that is its own table's key is never a foreign key, of
  strings or integers, since two keys' values fit by chance (``books.id`` and ``members.id``).
  These, a column contained in two parents' keys and a column contained in its own table's key
  are not proposed, and a note says so: a join path is never picked silently.
  The relationship is one-to-one when the child column's PRESENT values are distinct.
- **Roles.** ``link`` for a table whose key is two foreign keys; ``entity`` for a parent of a
  relationship, or a keyed table with no relationship to a parent; ``event`` for another table
  with a date or datetime column; ``measurement`` otherwise. ``coverage`` is never proposed.
- **Grain.** ``One row per <key columns>`` for a keyed table.
- **Coverage.** ``parents: "all"`` for every relationship whose child table is an entity or a
  link table (§5.6).

Declared keys (D307): a database's declared primary key is the table's key, ``imported``, and no
other is looked for; its declared foreign keys are relationships whose tables and columns are
``imported``, their cardinality proposed from the rows as above, and no relationship is proposed
by containment from a column they hold. Roles and coverage follow from declared and proposed keys
and relationships alike.

Evidence names rules and counts, never a cell value (A6).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast

from aibi.core.engine.data import PRESENT, Cell
from aibi.core.schema.descriptors import ListSyntax
from aibi.core.schema.output import text
from aibi.core.schema.pack_api import ImportNote
from aibi.core.store.cells import ColumnCells
from aibi.core.store.sources import SourceValue, canonical_string

Datatype = Literal[
    "integer", "number", "date", "datetime", "boolean", "string", "category", "list<category>"
]
Status = Literal["imported", "imported_default", "proposed"]
Role = Literal["entity", "link", "measurement", "event"]

CONVENTIONAL = (
    "NA", "N/A", "n/a", "#N/A", "NULL", "null", "None", "NaN", "-", ".", "?", "unknown",
    "Unknown",
)  # fmt: skip
"""Tokens declared as UNKNOWN missing codes, with ``imported_default``, where they occur."""
TRIED = ("integer", "number", "date", "datetime", "boolean")
CATEGORY_VALUES = 50
IDENTIFIER_CELLS = 20
TOLERANCE_CELLS = 20
"""Non-missing cells from which one in twenty may fail to parse and stay UNKNOWN."""
LIST_DELIMITERS = (";", "|")
_WORDS = frozenset({"true", "t", "yes", "y", "false", "f", "no", "n"})
_OFFSET = re.compile(r"(?:[Zz]|[+-][0-9]{2}:[0-9]{2})[ \t\r\n\f\v]*$")


@dataclass(frozen=True)
class ForeignKey:
    """A foreign key a database declares (D307), by column and table ids."""

    columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]


@dataclass(frozen=True)
class SourceTable:
    """A table as read, before inference: its id, its column ids and rows of source values."""

    id: str
    columns: tuple[str, ...]
    rows: Sequence[Sequence[SourceValue]]
    declared: Mapping[str, tuple[Datatype, Status]] = field(
        default_factory=dict[str, tuple[Datatype, Status]]
    )
    """Datatypes the source declares (a Parquet file's), with their status (D227)."""
    primary_key: tuple[str, ...] | None = None
    """The primary key a database declares, ``imported``; ``None`` when none is (D307)."""
    foreign_keys: tuple[ForeignKey, ...] = ()
    """The foreign keys a database declares, to tables of the same import (D307)."""


@dataclass(frozen=True)
class ColumnGuess:
    datatype: Datatype | None
    status: Status
    """The datatype's status."""
    evidence: str
    missing_codes: Mapping[str, str]
    list_syntax: ListSyntax | None = None
    list_evidence: str | None = None
    identifier: bool = False
    identifier_evidence: str | None = None


@dataclass(frozen=True)
class TableGuess:
    id: str
    columns: Mapping[str, ColumnGuess]
    key: tuple[str, ...] | None
    key_evidence: str | None
    role: Role
    role_evidence: str
    key_status: Status = "proposed"
    """``imported`` for a key the database declares (D307)."""


@dataclass(frozen=True)
class RelationshipGuess:
    child: str
    columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]
    one_to_one: bool
    evidence: str
    coverage: bool
    """Whether coverage ``parents: "all"`` is proposed: the child is an entity or a link."""
    status: Status = "proposed"
    """Of its tables and columns: ``imported`` for a foreign key the database declares (D307);
    its cardinality is always proposed, from the rows."""

    @property
    def id(self) -> str:
        return f"rel:{self.child}.{'+'.join(self.columns)}"


@dataclass(frozen=True)
class _Link:
    child: str
    columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]
    declared: bool = False


@dataclass(frozen=True)
class Inferred:
    tables: tuple[TableGuess, ...]
    relationships: tuple[RelationshipGuess, ...]
    notes: tuple[ImportNote, ...]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _tolerated(unparsed: int, cells: int) -> bool:
    return unparsed == 0 or (cells >= TOLERANCE_CELLS and unparsed * 20 <= cells)


@dataclass
class _Column:
    guess: ColumnGuess
    cells: tuple[Cell, ...]
    keys: list[str | None]
    """Each cell's canonical string where it is a PRESENT scalar, else ``None``."""
    candidate: bool
    """A string column that would be a category if it is no key or foreign key."""
    category_evidence: str | None
    distinct: bool
    """Whether its PRESENT values, of which there is at least one, are distinct."""


def _tokens(values: Sequence[SourceValue]) -> list[str | None]:
    return [canonical_string(value) for value in values]


def _codes(tokens: Sequence[str | None]) -> dict[str, str]:
    found = set(tokens)
    return {token: "UNKNOWN" for token in CONVENTIONAL if token in found}


def _naive(value: SourceValue, token: str) -> bool:
    if isinstance(value, datetime):
        return value.utcoffset() is None
    return _OFFSET.search(token) is None


def _typed(
    datatype: str | None,
    values: Sequence[SourceValue],
    codes: Mapping[str, str],
    syntax: ListSyntax | None = None,
) -> tuple[ColumnCells, tuple[Cell, ...]]:
    typer = ColumnCells(datatype, codes, syntax)
    return typer, tuple(typer.cell(value) for value in values)


def _list(
    values: Sequence[SourceValue], codes: Mapping[str, str], present: list[str]
) -> tuple[ListSyntax | None, str | None]:
    if all(token.startswith("[") for token in present):
        syntax = ListSyntax(format="json")
        typer, _ = _typed("list<category>", values, codes, syntax)
        if not typer.unparsed:
            return syntax, "Every present cell is a JSON array of scalars (D228)"
    if all(token.startswith(("[", "(")) for token in present):
        syntax = ListSyntax(format="python")
        typer, _ = _typed("list<category>", values, codes, syntax)
        if not typer.unparsed:
            return syntax, "Every present cell is a Python list or tuple of scalars (D228)"
    distinct = set(present)
    for delimiter in LIST_DELIMITERS:
        holding = sum(1 for token in present if delimiter in token)
        items = {item for token in distinct for item in token.split(delimiter)}
        if holding >= 2 and len(items) < len(distinct):
            evidence = (
                f"{_plural(holding, 'cell')} hold the delimiter {delimiter}, and splitting on it "
                f"gives {len(items)} distinct items for {len(distinct)} distinct cells (D228)"
            )
            return ListSyntax(format="delimited", delimiter=delimiter), evidence
    return None, None


def _attempts(
    values: Sequence[SourceValue], codes: Mapping[str, str], present: list[str]
) -> list[tuple[Datatype, int, int]]:
    """Each datatype tried, with how many non-missing cells it parses and leaves unparsed."""
    found: list[tuple[Datatype, int, int]] = []
    for tried in TRIED:
        if tried == "boolean" and not any(t.strip().lower() in _WORDS for t in present):
            continue
        typer, cells = _typed(tried, values, codes)
        parsed = sum(1 for cell in cells if cell.state is PRESENT)
        found.append((cast(Datatype, tried), parsed, typer.unparsed_cells))
    return found


def _chosen(
    attempts: Sequence[tuple[Datatype, int, int]], *, tolerant: bool
) -> tuple[Datatype, str] | None:
    """The first datatype tried that parses every non-missing cell, or, when ``tolerant``, all
    but one in twenty of at least 20; with its evidence."""
    for tried, parsed, unparsed in attempts:
        if not parsed or (unparsed and not (tolerant and _tolerated(unparsed, parsed + unparsed))):
            continue
        evidence = f"{parsed} of {_plural(parsed + unparsed, 'non-missing cell')} parse as {tried}"
        if unparsed:
            return tried, (
                evidence + f", and {unparsed} that do not are UNKNOWN, one in twenty at most: no "
                "datatype parses them all, and the column is no key or foreign key (D228)"
            )
        return tried, (
            evidence + ", the first of integer, number, date, datetime and boolean to parse them "
            "all (D228)"
        )
    return None


def _column(
    values: Sequence[SourceValue],
    tokens: Sequence[str | None],
    codes: Mapping[str, str],
    present: list[str],
    chosen: tuple[Datatype, str] | None,
    declared: tuple[Datatype, Status] | None,
) -> _Column:
    datatype: Datatype | None = None
    status: Status = "proposed"
    evidence = "No cell is PRESENT, so the datatype is left undeclared (D228)"
    syntax: ListSyntax | None = None
    list_evidence: str | None = None
    if declared is not None:
        datatype, status = declared
        evidence = (
            "The source declares the type, without an offset for datetimes, which are read as "
            "UTC (§12.2)"
            if status == "imported_default"
            else "The source declares the type (D227)"
        )
    elif present:
        if chosen is not None:
            datatype, evidence = chosen
        if datatype == "datetime" and any(
            _naive(value, token)
            for value, token in zip(values, tokens, strict=True)
            if token is not None and token not in codes and token != ""
        ):
            status = "imported_default"
            evidence += "; without an offset, times are read as UTC (§12.2)"
        if datatype is None:
            datatype = "string"
            evidence = "Not a column of integers, numbers, dates, datetimes or booleans (D228)"
            syntax, list_evidence = _list(values, codes, present)
            if syntax is not None:
                datatype = "list<category>"
    _, cells = _typed(datatype, values, codes, syntax)
    keys: list[str | None] = []
    for cell in cells:
        value = cell.value
        scalar = cell.state is PRESENT and value is not None and not isinstance(value, tuple)
        keys.append(canonical_string(cast(SourceValue, value)) if scalar else None)
    kept = [key for key in keys if key is not None]
    distinct_values = len(set(kept))
    candidate = datatype == "string" and (
        distinct_values <= CATEGORY_VALUES and 2 * distinct_values <= len(kept)
    )
    category_evidence = (
        f"{_plural(distinct_values, 'distinct value')} among {_plural(len(kept), 'present cell')}: "
        f"at most {CATEGORY_VALUES}, and at most half (D228)"
        if candidate
        else None
    )
    guess = ColumnGuess(datatype, status, evidence, codes, syntax, list_evidence)
    return _Column(
        guess,
        cells,
        keys,
        candidate,
        category_evidence,
        bool(kept) and distinct_values == len(kept),
    )


@dataclass(frozen=True)
class _Typed:
    strict: _Column
    tolerated: _Column | None
    """Typed with the tolerance, when only the tolerance gives it a datatype."""

    @property
    def keyed(self) -> _Column:
        """What keys and foreign keys are found in: the strict column, unless the tolerance gives
        a datatype that no key has (a number, say), which is the column then."""
        if self.tolerated is None or self.tolerated.guess.datatype == "integer":
            return self.strict
        return self.tolerated


def _columns(values: Sequence[SourceValue], declared: tuple[Datatype, Status] | None) -> _Typed:
    """The column typed strictly, and typed with the tolerance where no datatype parses it."""
    tokens = _tokens(values)
    codes = _codes(tokens)
    present = [t for t in tokens if t is not None and t != "" and t not in codes]
    attempts = _attempts(values, codes, present) if declared is None and present else []
    chosen = _chosen(attempts, tolerant=False)
    strict = _column(values, tokens, codes, present, chosen, declared)
    tolerant = None if chosen is not None else _chosen(attempts, tolerant=True)
    if tolerant is None:
        return _Typed(strict, None)
    return _Typed(strict, _column(values, tokens, codes, present, tolerant, declared))


def _singular(name: str) -> str:
    if re.search(r"(?:s|x|z|ch|sh)es$", name):
        return name[:-2]
    return name.removesuffix("s")


def _named(column: str, parent: str, key: str) -> bool:
    """Whether an integer column's id names a parent's key: the key's id, ``<parent>_<key>`` or
    ``<singular parent>_<key>``."""
    return column in (key, f"{parent}_{key}", f"{_singular(parent)}_{key}")


def _note(kind: Literal["not_proposed"], subject: str, message: str) -> ImportNote:
    return ImportNote(kind, subject, [text(message)])


def infer(tables: Sequence[SourceTable]) -> Inferred:
    """Every proposal for the tables of one import, in the order given."""
    notes: list[ImportNote] = []
    typed: dict[str, dict[str, _Typed]] = {}
    for table in tables:
        typed[table.id] = {
            column: _columns([row[i] for row in table.rows], table.declared.get(column))
            for i, column in enumerate(table.columns)
        }
    columns = {
        table: {column: found.keyed for column, found in found_columns.items()}
        for table, found_columns in typed.items()
    }
    sizes = {table.id: len(table.rows) for table in tables}
    keys: dict[str, tuple[str, ...] | None] = {}
    key_evidence: dict[str, str] = {}
    key_status: dict[str, Status] = {}
    for table in tables:
        if table.primary_key is not None:
            keys[table.id] = table.primary_key
            key_evidence[table.id] = "The database declares the primary key (D307)"
            key_status[table.id] = "imported"
            continue
        for column, found in columns[table.id].items():
            if (
                found.guess.datatype in ("string", "integer")
                and sizes[table.id] > 0
                and all(key is not None for key in found.keys)
                and found.distinct
            ):
                keys[table.id] = (column,)
                key_evidence[table.id] = (
                    f"The first string or integer column whose {_plural(sizes[table.id], 'cell')} "
                    "are all present and distinct (D229)"
                )
                break
    key_sets = {
        table: {key for key in columns[table][key_columns[0]].keys if key is not None}
        for table, key_columns in keys.items()
        if key_columns is not None and len(key_columns) == 1
    }
    declared_links = [
        _Link(table.id, key.columns, key.parent, key.parent_columns, declared=True)
        for table in tables
        for key in table.foreign_keys
    ]
    declared_columns = {(link.child, column) for link in declared_links for column in link.columns}
    found_parents: dict[tuple[str, str], list[str]] = {}
    for table in tables:
        for column, found in columns[table.id].items():
            if found.guess.datatype not in ("string", "integer"):
                continue
            if (table.id, column) in declared_columns:
                continue
            values = {key for key in found.keys if key is not None}
            if not values:
                continue
            own_key = keys.get(table.id) == (column,)
            keyed: list[str] = []
            for parent, parent_keys in key_sets.items():
                parent_column = cast(tuple[str, ...], keys[parent])[0]
                parent_type = columns[parent][parent_column].guess.datatype
                if found.guess.datatype != parent_type:
                    continue
                if not values <= parent_keys or (parent, parent_column) == (table.id, column):
                    continue
                if own_key and parent != table.id:
                    keyed.append(parent)
                    continue
                if found.guess.datatype == "integer" and not _named(column, parent, parent_column):
                    notes.append(
                        _note(
                            "not_proposed",
                            f"{table.id}.{column}",
                            f"The column's integers are keys of {parent}, but its id is none of "
                            f"the key's, {parent}_<key> and {_singular(parent)}_<key>, so no "
                            "relationship is proposed: small numbers are contained by chance "
                            "(D229)",
                        )
                    )
                    continue
                found_parents.setdefault((table.id, column), []).append(parent)
            if keyed:
                notes.append(
                    _note(
                        "not_proposed",
                        f"{table.id}.{column}",
                        f"The column is its table's key, and its values are keys of "
                        f"{', '.join(keyed)}: a relationship between two tables' keys is never "
                        "proposed, since two keys' values fit by chance (D229)",
                    )
                )
    relationships: list[_Link] = list(declared_links)
    for (table, column), parents in found_parents.items():
        if table in parents:
            notes.append(
                _note(
                    "not_proposed",
                    f"{table}.{column}",
                    "The column's values are keys of its own table; a relationship to itself is "
                    "not proposed (D229)",
                )
            )
            continue
        if len(parents) > 1:
            notes.append(
                _note(
                    "not_proposed",
                    f"{table}.{column}",
                    f"The column's values are keys of {len(parents)} tables, so no relationship "
                    "is proposed: a join path is never picked silently (D229)",
                )
            )
            continue
        parent_key = cast(tuple[str, ...], keys[parents[0]])
        relationships.append(_Link(table, (column,), parents[0], parent_key))
    foreign = {(link.child, column) for link in relationships for column in link.columns}
    for table in tables:
        if table.id in keys:
            continue
        candidates = [c for c in table.columns if (table.id, c) in foreign]
        found_pair: tuple[str, str] | None = None
        for i, first in enumerate(candidates):
            for second in candidates[i + 1 :]:
                pairs = list(
                    zip(columns[table.id][first].keys, columns[table.id][second].keys, strict=True)
                )
                if (
                    pairs
                    and all(a is not None and b is not None for a, b in pairs)
                    and len(set(pairs)) == len(pairs)
                ):
                    found_pair = (first, second)
                    break
            if found_pair is not None:
                break
        if found_pair is not None:
            keys[table.id] = found_pair
            key_evidence[table.id] = (
                "No single column is present and distinct in every row; the first pair of "
                f"foreign keys is, together, in all {_plural(sizes[table.id], 'row')} (D229)"
            )
        else:
            keys[table.id] = None
            notes.append(
                _note(
                    "not_proposed",
                    table.id,
                    "No key is proposed: no string or integer column, nor pair of foreign keys, "
                    "is present and distinct in every row (D229)",
                )
            )
    for table in tables:
        in_key = set(keys[table.id] or ())
        for column, found in typed[table.id].items():
            special = column in in_key or (table.id, column) in foreign
            if found.tolerated is not None and not special:
                columns[table.id][column] = found.tolerated
    parents_of = {link.parent for link in relationships}
    children_of = {link.child for link in relationships}
    roles: dict[str, tuple[Role, str]] = {}
    for table in tables:
        key = keys[table.id]
        guesses = columns[table.id]
        if key is not None and len(key) == 2 and all((table.id, c) in foreign for c in key):
            roles[table.id] = ("link", "Its key is two foreign keys (D229)")
        elif table.id in parents_of:
            roles[table.id] = ("entity", "It is the parent of a relationship (D229)")
        elif key is not None and table.id not in children_of:
            roles[table.id] = ("entity", "It has a key and no relationship to a parent (D229)")
        elif any(g.guess.datatype in ("date", "datetime") for g in guesses.values()):
            roles[table.id] = ("event", "It has a date or datetime column (D229)")
        else:
            roles[table.id] = ("measurement", "None of the other roles' rules applies (D229)")
    proposed: list[RelationshipGuess] = []
    for link in relationships:
        found_keys = list(zip(*(columns[link.child][c].keys for c in link.columns), strict=True))
        present = [key for key in found_keys if None not in key]
        count = len(present)
        one_to_one = bool(present) and len(set(present)) == count
        if link.declared:
            evidence = "The database declares the foreign key (D307)" + (
                "; its present values are distinct, so the relationship is one-to-one"
                if one_to_one
                else ""
            )
        else:
            evidence = (
                f"Each of the column's {_plural(count, 'present value')} is a key of "
                f"{link.parent}, and of no other table (containment, D229)"
                + ("; they are distinct, so the relationship is one-to-one" if one_to_one else "")
            )
        proposed.append(
            RelationshipGuess(
                child=link.child,
                columns=link.columns,
                parent=link.parent,
                parent_columns=link.parent_columns,
                one_to_one=one_to_one,
                evidence=evidence,
                coverage=roles[link.child][0] in ("entity", "link"),
                status="imported" if link.declared else "proposed",
            )
        )
    guessed: list[TableGuess] = []
    for table in tables:
        key = keys[table.id]
        in_key = set(key or ())
        described: dict[str, ColumnGuess] = {}
        few: list[str] = []
        for column, found in columns[table.id].items():
            guess = found.guess
            special = column in in_key or (table.id, column) in foreign
            if found.candidate and not special:
                guess = ColumnGuess(
                    "category",
                    guess.status,
                    cast(str, found.category_evidence),
                    guess.missing_codes,
                )
            elif guess.datatype == "string" and not special and found.distinct:
                present = sum(1 for key_value in found.keys if key_value is not None)
                if present >= IDENTIFIER_CELLS:
                    guess = ColumnGuess(
                        guess.datatype,
                        guess.status,
                        guess.evidence,
                        guess.missing_codes,
                        identifier=True,
                        identifier_evidence=(
                            f"All {_plural(present, 'present value')} are distinct (D228)"
                        ),
                    )
                elif present >= 2:
                    few.append(column)
            described[column] = guess
        if few:
            notes.append(
                _note(
                    "not_proposed",
                    table.id,
                    f"No identifier is proposed among {', '.join(few)}: "
                    f"{'its' if len(few) == 1 else 'their'} present values are distinct, but "
                    f"fewer than {IDENTIFIER_CELLS}, too few to tell (D228)",
                )
            )
        role, role_evidence = roles[table.id]
        guessed.append(
            TableGuess(
                table.id,
                described,
                key,
                key_evidence.get(table.id),
                role,
                role_evidence,
                key_status.get(table.id, "proposed"),
            )
        )
    return Inferred(tuple(guessed), tuple(proposed), tuple(notes))


__all__ = [
    "CATEGORY_VALUES",
    "CONVENTIONAL",
    "IDENTIFIER_CELLS",
    "TOLERANCE_CELLS",
    "ColumnGuess",
    "Datatype",
    "ForeignKey",
    "Inferred",
    "RelationshipGuess",
    "Role",
    "SourceTable",
    "Status",
    "TableGuess",
    "infer",
]
