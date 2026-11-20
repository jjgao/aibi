"""The carry's outcomes per field and change, through the real session and the real re-import
(SPEC §12.3, D227, D228, D239–D241; M4.2b-2b-ii-0).

A plain table, ``trees``, is imported, curated in a real session and re-imported. Each cell of the
generator is a field of ``ColumnFields``, by the state its curation entry is in before the
re-import, by how the importer's inference of it relates between the two imports, by the pair of
D228's datatypes the column is inferred before and after:

- **the state:** ``absent`` (no value, no tombstone), the importer's own statuses ``imported``,
  ``imported_default`` and ``proposed``, ``asserted`` (an operator's ``set``, of a value other
  than the inference where there is one), ``confirmed`` (``confirm`` of the importer's value) and
  ``tombstoned`` (``remove`` of a field with an inference);
- **the relation** of the previous inference to the new one: ``equal``, ``different``, ``new``
  (none before), ``gone`` (none now) and ``none`` (neither import infers the field);
- **the pair** ``<before>><after>`` over D228's outcomes for a column of text: ``none`` (no
  PRESENT cell, no datatype), ``cat`` (at most 50 distinct values, at most half the present
  cells), ``str`` (a string that is no category) and ``list`` (``list<category>``: ``;``, else
  ``|``, in at least 2 cells, splitting on it giving fewer distinct items than distinct cells);
  the data are instances (2 values among 4 cells, 4 among 4, lists over ``a``, ``b``, ``c``).

A second generator, ``typed_cells``, takes a column of each of the four kinds (``none``, ``cat``,
``str``, ``list``) into D228's typed datatypes (``int``, ``num``, ``date``, ``bool``), for the
fields whose admission depends on the datatype (``units``, ``range``, ``permissible_values``,
``list_syntax``) and for the datatype itself; its outcomes are the literals of
``TYPED_EXPECTED``. A third, ``derived_cells``, puts an operator's derived column of each of the
model's operations (``arith``, ``date_diff``, ``unit_convert``, ``value_map``) over an input that
the re-import infers as each of D228's kinds, ``datetime`` included, or drops; its outcomes are the
literals of ``DERIVED_EXPECTED``, whose carried/refused split is held to ``derive.problems``'
rule (``reads``).

Each cell's curation is the field's edit with the least companion it needs to be accepted
(``units`` with ``datatype: number``, ``range`` with ``datatype: integer``, ``list_syntax`` with
``datatype: list<category>``, ``permissible_values`` with ``datatype: category`` on a column
inferred a string or none, and the removal of ``list_syntax`` where a datatype other than
``list<category>`` is asserted on a list), and its expected outcome is a literal of ``EXPECTED``:
what the re-import reported for the field (a ``carry.Happened``, or ``carried`` when it reported
nothing), with the field's value, its entry's status and whether a tombstone of it stands after;
or ``refused`` at the session or at the re-import, with the refusals' codes and paths. Every run
checks that the field reached the cell's state before the re-import, and that the two imports'
inferences are the cell's relation and pair. Every other cell is matched by a pattern of
``UNREACHABLE``, whose reason is a fact that ``test_the_importer_s_facts_over_every_small_column``
checks by brute force through the real importer, or that a session test or the cell's definition
gives; ``test_the_table_covers_the_generator_exactly`` holds the two to the generator's cells.

``maps_to`` names a concept, which only an installed pack registers (D405): the runs install a
test-only pack, ``orchard``, that registers one value concept and nothing else.

The cells reach D239's ``field()`` in every branch, and ``settle()`` where a carried field fails
the model against the new datatype (a refusal) and where a new proposal fails against a carried
tombstone (``dropped``: a list's ``list_syntax`` returning while its removed datatype stays
removed). ``settle()`` is not characterised in full here: replacing a carried guess, and sending a
failing new proposal back to the base's value, are reached by no column field through a session
and a core import; ``tests/core/store/test_carry.py`` covers them.
The literals were read off the real run and checked against ``carry.py`` by hand; SPEC D241 states
the table in words, with the operator's remedy for each outcome.
"""

import itertools
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

import aibi
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.run import Published, import_dataset, reimport_dataset
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import _OPS as MODEL_OPS  # the model's derive operations
from aibi.core.schema.descriptors import ColumnFields, ConceptDescriptor, ConceptFields
from aibi.core.schema.limits import MAX_CHANGE_EDITS, ImportLimits
from aibi.core.schema.pack_api import ImportOptions, Pack, PackManifest, PackRegistry
from aibi.core.store import sessions
from aibi.core.store.edits import EditRefused
from aibi.core.store.store import Store, StoreRefused

ADA = "operator:ada"
AT = "2026-01-01T00:00:00Z"
TABLE = "trees"
ALONE = "colour"
"""The column of a cell run alone: one whose outcome is a refusal."""
COLUMN = f"{TABLE}.{ALONE}"

FIELDS = (
    "datatype",
    "units",
    "range",
    "permissible_values",
    "missing_codes",
    "identifier",
    "concepts",
    "maps_to",
    "derived",
    "list_syntax",
    "completeness",
    "source",
    "absent",
)
"""``ColumnFields``'s fields when the table was written (``test_the_generator_s_fields_…``)."""
STATES = (
    "absent",
    "imported",
    "imported_default",
    "proposed",
    "asserted",
    "confirmed",
    "tombstoned",
)
RELATIONS = ("equal", "different", "new", "gone", "none")
KINDS = ("none", "cat", "str", "list")
PAIRS = tuple(f"{before}>{after}" for before in KINDS for after in KINDS)
TYPED = ("int", "num", "date", "bool")
"""D228's typed datatypes a column of text is re-imported into, by instance (integer, number,
date, boolean); ``datetime`` is not run."""
TYPED_PAIRS = tuple(f"{before}>{after}" for before in KINDS for after in TYPED)
DATATYPES: Mapping[str | None, str] = {
    None: "none",
    "category": "cat",
    "string": "str",
    "list<category>": "list",
    "integer": "int",
    "number": "num",
    "date": "date",
    "boolean": "bool",
}

Cell = tuple[str, str, str, str]
"""(field, state, relation, pair)."""


def cells() -> list[Cell]:
    """The generator: every field of the model, by state, by relation, by pair."""
    return list(product(tuple(ColumnFields.model_fields), STATES, RELATIONS, PAIRS))


def typed_cells() -> list[Cell]:
    """The typed generator: from each of D228's kinds for a column of text into each typed
    datatype, each field a datatype admits or not, asserted with its companion (a list's own
    ``list_syntax`` asserted over its inference, which the typed datatype takes away), and the
    datatype in each state the kind before allows."""
    admitted = ("units", "range", "permissible_values", "list_syntax")
    found: list[Cell] = []
    for pair in TYPED_PAIRS:
        before = pair.split(">")[0]
        for field in admitted:
            relation = "gone" if (field, before) == ("list_syntax", "list") else "none"
            found.append((field, "asserted", relation, pair))
        if before == "none":
            found += [("datatype", state, "new", pair) for state in ("absent", "asserted")]
        else:
            states = ("proposed", "asserted", "confirmed", "tombstoned")
            found += [("datatype", state, "different", pair) for state in states]
    return found


# --- The data -----------------------------------------------------------------------------------

CATEGORY = ("red", "red", "green", "green")
"""2 distinct values among 4 present cells: at most half, a category (D228)."""
CATEGORY_AGAIN = ("green", "red", "red", "green")
STRING = ("red", "green", "blue", "amber")
"""4 among 4: more than half, a string."""
STRING_AGAIN = ("amber", "blue", "green", "red")
LISTS = ("a;b", "a;b;c", "b;c", "a;c")
"""``;`` in at least 2 cells, and 3 distinct items for 4 distinct cells: a ``list<category>``
(D228; here ``;`` happens to be in every cell)."""
LISTS_AGAIN = ("a;c", "b;c", "a;b;c", "a;b")
PIPES = ("a|b", "a|b|c", "b|c", "a|c")
"""A list again, delimited by ``|``: another ``list_syntax``."""
IDENTIFYING = tuple(f"tree{index:02d}" for index in range(20))
"""20 distinct present cells of a string: proposed as an identifier (D228)."""
IDENTIFYING_AGAIN = tuple(reversed(IDENTIFYING))
SHAPES: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "none": ((), ()),
    "cat": (CATEGORY, CATEGORY_AGAIN),
    "str": (STRING, STRING_AGAIN),
    "list": (LISTS, LISTS_AGAIN),
    "int": (("1", "2", "3", "4"), ("4", "3", "2", "1")),
    "num": (("1.5", "2.5", "3.5", "4.5"), ("4.5", "3.5", "2.5", "1.5")),
    "date": (("2026-01-01", "2026-01-02", "2026-01-03"), ("2026-02-01", "2026-02-02")),
    "bool": (("yes", "no", "yes"), ("no", "yes", "no")),
}
"""A kind's cells on the first import and on the second; a column of fewer cells than the table's
rows is padded with empty cells, which are not PRESENT."""
VALUES = ("amber", "blue", "green", "red")
"""The values a curated ``permissible_values`` lists."""
CODES: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "equal": (("NA",), ("NA",)),
    "different": (("NA",), ("NA", "N/A")),
    "new": ((), ("NA",)),
    "gone": (("NA",), ()),
    "none": ((), ()),
}
"""Conventional missing codes (D227) added to the cells for ``missing_codes``, before and after;
missing cells are not present ones, so D228's count is unchanged."""


@dataclass(frozen=True)
class Scenario:
    first: tuple[str, ...]
    second: tuple[str, ...]
    renamed: bool
    """Whether the second import's header is the first's capitalised (``source`` differs)."""
    edits: tuple[dict[str, JsonValue], ...]


def _set(column: str, field: str, value: JsonValue) -> dict[str, JsonValue]:
    return {"op": "set", "descriptor": column, "pointer": f"/fields/{field}", "value": value}


def _remove(column: str, field: str) -> dict[str, JsonValue]:
    return {"op": "remove", "descriptor": column, "pointer": f"/fields/{field}"}


def _asserted(column: str, field: str, inferred: bool, base: str) -> list[dict[str, JsonValue]]:
    """The edits asserting ``field`` on a column inferred ``base``, with its least companion: a
    value other than the importer's when it infers one."""
    # A datatype other than list<category> asserted on a list leaves no list_syntax.
    unlisted = [_remove(column, "list_syntax")] if base == "list" else []
    if field == "datatype":
        return [_set(column, "datatype", "category" if base == "str" else "string"), *unlisted]
    if field == "units":
        return [_set(column, "datatype", "number"), *unlisted, _set(column, "units", "1")]
    if field == "range":
        return [_set(column, "datatype", "integer"), *unlisted, _set(column, "range", RANGE)]
    if field == "permissible_values":
        listed = _set(column, "permissible_values", LISTED)
        if base in ("str", "none"):
            return [_set(column, "datatype", "category"), listed]
        return [listed]
    if field == "missing_codes":
        return [_set(column, "missing_codes", NA_NOT_APPLICABLE if inferred else DASH)]
    if field == "identifier":
        return [_set(column, "identifier", not inferred)]
    if field == "concepts":
        return [_set(column, "concepts", REFS)]
    if field == "maps_to":
        return [_set(column, "maps_to", MAPPED)]
    if field == "derived":
        return [_set(column, "derived", {"op": "value_map", "input": "id", "map": {"t00": "x"}})]
    if field == "list_syntax":
        if base == "list":
            return [_set(column, "list_syntax", PIPED)]
        return [_set(column, "datatype", "list<category>"), _set(column, "list_syntax", SYNTAX)]
    if field == "completeness":
        return [_set(column, "completeness", "complete")]
    if field == "source":
        return [_set(column, "source", HUE)]
    assert field == "absent", field
    return [_set(column, "absent", ["x"])]


def scenario(cell: Cell, column: str) -> Scenario:
    """The column's cells on both imports and the curation between them, for a reachable cell."""
    field, state, relation, pair = cell
    before, after = pair.split(">")
    first, second = SHAPES[before][0], SHAPES[after][1]
    if field == "identifier":
        if relation in ("equal", "gone"):
            first = IDENTIFYING
        if relation in ("equal", "new"):
            second = IDENTIFYING_AGAIN
    if field == "list_syntax" and relation == "different":
        second = PIPES
    if field == "missing_codes":
        first, second = (*first, *CODES[relation][0]), (*second, *CODES[relation][1])
    pointer = f"/fields/{field}"
    edits: list[dict[str, JsonValue]] = []
    if state == "asserted":
        edits = _asserted(column, field, relation in ("equal", "different", "gone"), before)
    elif state == "confirmed":
        edits = [{"op": "confirm", "descriptor": column, "pointers": [pointer]}]
    elif state == "tombstoned":
        edits = [_remove(column, field)]
        if before == "list" and field in ("datatype", "list_syntax"):
            # A list's list_syntax and its list<category> go together.
            edits.append(_remove(column, "list_syntax" if field == "datatype" else "datatype"))
    renamed = field == "source" and relation == "different"
    return Scenario(first, second, renamed, tuple(edits))


# --- Running the cells ----------------------------------------------------------------------------

Outcome = tuple[Any, ...]
"""``(happened, value, status, tombstone)``, ``happened`` a ``carry.Happened`` or ``carried``;
or ``("refused", "curation" | "reimport", ((code, path), …))``."""


def registry() -> PackRegistry:
    """A test-only pack registering one value concept, so that ``maps_to`` can name one."""
    concept = ConceptDescriptor(
        kind="concept",
        id="orchard:colour",
        version=1,
        label="Colour",
        definition="A colour of a tree in an orchard.",
        fields=ConceptFields(sort="value"),  # type: ignore[arg-type]
    )
    manifest = PackManifest(
        id="orchard", version="1.0.0", results_version=1, requires_core=">=0.0.1"
    )
    return PackRegistry(
        [Pack(manifest=manifest, concepts=[concept])], core_version=aibi.__version__
    )


class Orchard:
    """A store, an import directory and the pack, for imports of ``trees`` tables."""

    def __init__(self, root: Path) -> None:
        self.inside = root / "imports"
        self.inside.mkdir(parents=True)
        self.confinement = Confinement.of(self.inside)
        ticks = itertools.count()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        self.store = Store(root / "store", clock=lambda: start + timedelta(seconds=next(ticks)))
        self.registry = registry()

    def write(self, dataset: str, columns: Mapping[str, Sequence[str]], round: str) -> Path:
        """``trees.csv`` of ``dataset``: a key, a column ``round`` (so that no re-import is
        ``NO_CHANGE``) and ``columns``, padded to the longest with empty cells."""
        directory = self.inside / dataset
        directory.mkdir(exist_ok=True)
        rows = max([1, *(len(values) for values in columns.values())])
        lines = [",".join(["id", "round", *columns])]
        for index in range(rows):
            cells = [values[index] if index < len(values) else "" for values in columns.values()]
            lines.append(",".join([f"t{index:02d}", round, *cells]))
        (directory / f"{TABLE}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return directory

    def options(self, dataset: str) -> ImportOptions:
        return ImportOptions(dataset=dataset, reader=self.confinement, limits=ImportLimits(), at=AT)

    def import_(self, dataset: str, columns: Mapping[str, Sequence[str]], round: str) -> Published:
        path = self.confinement.confine(self.write(dataset, columns, round))
        return import_dataset(self.store, path, self.options(dataset), ADA, registry=self.registry)

    def reimport(self, dataset: str, columns: Mapping[str, Sequence[str]]) -> Published:
        path = self.confinement.confine(self.write(dataset, columns, "second"))
        return reimport_dataset(
            self.store, path, self.options(dataset), ADA, registry=self.registry
        )

    def curate(self, dataset: str, *groups: Sequence[dict[str, JsonValue]]) -> None:
        """Changes of at most ``MAX_CHANGE_EDITS`` edits in one real session, published, each
        group of edits (a cell's) in one change; a refused change discards the session."""
        chunks: list[list[dict[str, JsonValue]]] = [[]]
        for group in groups:
            assert len(group) <= MAX_CHANGE_EDITS
            if len(chunks[-1]) + len(group) > MAX_CHANGE_EDITS:
                chunks.append([])
            chunks[-1].extend(group)
        opened = sessions.open_session(self.store, dataset, ADA)
        draft = opened.draft
        try:
            for chunk in chunks:
                request = ChangeRequest.model_validate({"edits": chunk})
                draft = sessions.change(
                    self.store, dataset, opened.handle, draft, request, ADA, registry=self.registry
                )
        except BaseException:
            sessions.discard(self.store, dataset, opened.handle, draft, ADA)
            raise
        sessions.publish(self.store, dataset, opened.handle, draft, ADA, registry=self.registry)

    def columns(self, manifest: str) -> dict[str, dict[str, Any]]:
        return {
            d.id: cast(dict[str, Any], d.model_dump(mode="json", exclude_none=True))
            for d in self.store.descriptors(manifest)
            if d.kind == "column"
        }

    def tombstoned(self, manifest: str) -> set[tuple[str, str]]:
        return {(t.descriptor, t.pointer) for t in self.store.tombstones(manifest)}


def _refusals(error: Exception) -> tuple[tuple[str, str | None], ...]:
    found = getattr(error, "refusals", None)
    if found is not None:
        return tuple((str(refusal.code), refusal.path) for refusal in found)
    refusal = cast(StoreRefused, error).refusal
    return ((str(refusal.code), refusal.path),)


def _inference(column: Mapping[str, Any], field: str) -> tuple[bool, JsonValue]:
    entry = column["curation"].get(f"/fields/{field}")
    if entry is None or "inferred" not in entry:
        return (False, None)
    return (True, entry["inferred"])


def relation_of(before: tuple[bool, JsonValue], after: tuple[bool, JsonValue]) -> str:
    """The relation of two imports' inferences of a field."""
    if not before[0]:
        return "new" if after[0] else "none"
    if not after[0]:
        return "gone"
    return "equal" if before[1] == after[1] else "different"


def state_of(column: Mapping[str, Any], field: str, tombstoned: bool) -> str:
    """The state of a column's field in a release."""
    if tombstoned:
        return "tombstoned"
    if field not in column["fields"]:
        return "absent"
    entry = column["curation"][f"/fields/{field}"]
    if entry["status"] != "asserted":
        return cast(str, entry["status"])
    confirmed = "inferred" in entry and entry["inferred"] == column["fields"][field]
    return "confirmed" if confirmed else "asserted"


@dataclass(frozen=True)
class Seen:
    """What a cell's run gave: its outcome, and what the cell claims, as the run found it."""

    outcome: Outcome
    state: str | None
    """The field's state before the re-import; ``None`` when the session refused."""
    relation: str
    pair: str


def _kind(column: Mapping[str, Any]) -> str:
    datatype = _inference(column, "datatype")
    return DATATYPES.get(cast(str | None, datatype[1]), str(datatype[1]))


def run(orchard: Orchard, dataset: str, group: Mapping[str, Cell]) -> dict[Cell, Seen]:
    """The cells of ``group`` (by column), each a column of one ``trees``, through an import, a
    session and a re-import; and a plain import of the second cells, for the inferences."""
    built = {column: scenario(cell, f"{TABLE}.{column}") for column, cell in group.items()}
    first = orchard.import_(dataset, {c: s.first for c, s in built.items()}, "first")
    headers = {c: c.capitalize() if s.renamed else c for c, s in built.items()}
    second = {headers[c]: s.second for c, s in built.items()}
    probe = orchard.import_(f"{dataset}_next", second, "second")
    before, after = orchard.columns(first.manifest), orchard.columns(probe.manifest)
    claimed: dict[str, tuple[str, str]] = {}
    for column, cell in group.items():
        identifier = f"{TABLE}.{column}"
        old, new = before[identifier], after[identifier]
        pair = f"{_kind(old)}>{_kind(new)}"
        relation = relation_of(_inference(old, cell[0]), _inference(new, cell[0]))
        claimed[column] = (relation, pair)
    groups = [s.edits for s in built.values() if s.edits]
    states: dict[str, str] = {}
    try:
        if groups:
            orchard.curate(dataset, *groups)
    except (EditRefused, StoreRefused) as error:
        refused = ("refused", "curation", _refusals(error))
        return {cell: Seen(refused, None, *claimed[c]) for c, cell in group.items()}
    latest = orchard.store.latest(dataset)
    assert latest is not None
    curated, tombstones = orchard.columns(latest.manifest), orchard.tombstoned(latest.manifest)
    for column, cell in group.items():
        identifier, pointer = f"{TABLE}.{column}", f"/fields/{cell[0]}"
        found = curated[identifier]
        states[column] = state_of(found, cell[0], (identifier, pointer) in tombstones)
    try:
        published = orchard.reimport(dataset, second)
    except ImportRefused as error:
        refused = ("refused", "reimport", _refusals(error))
        return {cell: Seen(refused, states[c], *claimed[c]) for c, cell in group.items()}
    carried, tombstones = (
        orchard.columns(published.manifest),
        orchard.tombstoned(published.manifest),
    )
    seen: dict[Cell, Seen] = {}
    for column, cell in group.items():
        identifier, pointer = f"{TABLE}.{column}", f"/fields/{cell[0]}"
        happened = [
            change.happened
            for change in published.changes
            if (change.descriptor, change.pointer) == (identifier, pointer)
        ]
        found = carried[identifier]
        entry = found["curation"].get(pointer)
        value = found["fields"].get(cell[0])
        if cell[0] == "source" and isinstance(value, dict):
            name = cast(str, cast(dict[str, Any], value).get("original_name"))
            spelled = {column: ALONE, column.capitalize(): ALONE.capitalize()}
            value = {**cast(dict[str, Any], value), "original_name": spelled.get(name, name)}
        outcome = (
            happened[0] if happened else "carried",
            value,
            None if entry is None else entry["status"],
            (identifier, pointer) in tombstones,
        )
        seen[cell] = Seen(outcome, states[column], *claimed[column])
    return seen


@pytest.fixture(scope="module")
def seen(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[Cell, Seen]]:
    """Every reachable cell's run: the cells the table expects to be carried or reported, each a
    column of one table; each cell the table expects refused, alone."""
    orchard = Orchard(tmp_path_factory.mktemp("orchard"))
    found: dict[Cell, Seen] = {}
    expected = {**EXPECTED, **TYPED_EXPECTED}
    batched = [cell for cell in sorted(expected) if expected[cell][0] != "refused"]
    alone = [cell for cell in sorted(expected) if expected[cell][0] == "refused"]
    found.update(run(orchard, "batch", {f"k{i:04d}": cell for i, cell in enumerate(batched)}))
    for index, cell in enumerate(alone):
        found.update(run(orchard, f"alone{index}", {ALONE: cell}))
    yield found
    orchard.store.close()


# --- Derived columns ----------------------------------------------------------------------------

DERIVE_OPS = ("arith", "date_diff", "unit_convert", "value_map")
"""The model's derive operations (``test_the_derived_generator_s_operations_are_the_model_s``)."""
INPUT_KINDS = ("none", "cat", "str", "list", "int", "num", "date", "datetime", "bool", "gone")
"""What the input of a derived column is inferred after the re-import: D228's every outcome;
or ``gone``, the input no longer a column of the table."""
INPUT_BEFORE: Mapping[str, str] = {
    "arith": "int",
    "date_diff": "date",
    "unit_convert": "int",
    "value_map": "cat",
}
"""An input kind each operation reads, so that the session accepts the derived column."""
DATETIMES = ("2026-02-01T10:00:00", "2026-02-02T11:00:00", "2026-02-03T12:00:00")
"""Naive datetimes: a ``datetime`` (D228), ``imported_default`` (D227)."""
PLANTED = ("2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04")
"""A date column every derived run holds, the other end of a ``date_diff``."""
DerivedCell = tuple[str, str]
"""(operation, the input's kind after the re-import)."""


def derived_cells() -> list[DerivedCell]:
    return list(product(DERIVE_OPS, INPUT_KINDS))


def _input_cells(kind: str, after: bool) -> tuple[str, ...]:
    return DATETIMES if kind == "datetime" else SHAPES[kind][1 if after else 0]


def derived_edits(op: str, input: str, derived: str) -> list[dict[str, JsonValue]]:
    """An operator's derived column ``derived`` reading ``input``, with the least companion its
    operation needs (``units`` on the input of a ``unit_convert``)."""
    operation: dict[str, JsonValue] = {
        "arith": {"op": "arith", "operator": "+", "args": [input, 1]},
        "date_diff": {"op": "date_diff", "from": input, "to": "planted", "units": "d"},
        "unit_convert": {"op": "unit_convert", "input": input, "units": "km"},
        "value_map": {"op": "value_map", "input": input, "map": {"red": "warm"}},
    }[op]
    put: dict[str, JsonValue] = {
        "op": "put",
        "descriptor": {
            "kind": "column",
            "id": f"{TABLE}.{derived}",
            "label": derived,
            "fields": {"derived": operation},
        },
    }
    companion = [_set(f"{TABLE}.{input}", "units", "m")] if op == "unit_convert" else []
    return [*companion, put]


def run_derived(
    orchard: Orchard, dataset: str, group: Mapping[tuple[str, str], DerivedCell]
) -> dict[DerivedCell, Outcome]:
    """Each cell of ``group`` (by its input and derived column) through an import, a session that
    puts the derived column, and a re-import of the input as the cell's kind: ``("carried",)``
    when the derived column is carried as written and nothing is reported of it."""
    first = {input: _input_cells(INPUT_BEFORE[op], False) for (input, _), (op, _) in group.items()}
    second = {
        input: _input_cells(kind, True) for (input, _), (_, kind) in group.items() if kind != "gone"
    }
    orchard.import_(dataset, {"planted": PLANTED, **first}, "first")
    orchard.curate(dataset, *(derived_edits(op, i, d) for (i, d), (op, _) in group.items()))
    latest = orchard.store.latest(dataset)
    assert latest is not None
    before = orchard.columns(latest.manifest)
    try:
        published = orchard.reimport(dataset, {"planted": PLANTED, **second})
    except ImportRefused as error:
        refused = ("refused", "reimport", _refusals(error))
        return dict.fromkeys(group.values(), refused)
    after = orchard.columns(published.manifest)
    found: dict[DerivedCell, Outcome] = {}
    for (_, derived), cell in group.items():
        identifier = f"{TABLE}.{derived}"
        reported = [c.happened for c in published.changes if c.descriptor == identifier]
        if identifier not in after:
            found[cell] = ("gone", reported)
            continue
        same = after[identifier] == before[identifier]
        found[cell] = ("carried",) if same and not reported else ("changed", reported)
    return found


@pytest.fixture(scope="module")
def derived_seen(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[DerivedCell, Outcome]]:
    """Every derived cell's run: those the table expects carried as columns of one table, each
    one it expects refused alone."""
    orchard = Orchard(tmp_path_factory.mktemp("derived"))
    found: dict[DerivedCell, Outcome] = {}
    carried = [c for c in sorted(DERIVED_EXPECTED) if DERIVED_EXPECTED[c][0] != "refused"]
    alone = [c for c in sorted(DERIVED_EXPECTED) if DERIVED_EXPECTED[c][0] == "refused"]
    group = {(f"i{n:02d}", f"d{n:02d}"): cell for n, cell in enumerate(carried)}
    found.update(run_derived(orchard, "batch", group))
    for index, cell in enumerate(alone):
        found.update(run_derived(orchard, f"alone{index}", {(ALONE, "derived"): cell}))
    yield found
    orchard.store.close()


# --- The literal table ---------------------------------------------------------------------------

NA: JsonValue = {"NA": "UNKNOWN"}
BOTH: JsonValue = {"N/A": "UNKNOWN", "NA": "UNKNOWN"}
NA_NOT_APPLICABLE: JsonValue = {"NA": "NOT_APPLICABLE"}
DASH: JsonValue = {"-": "UNKNOWN"}
HEADER: JsonValue = {"original_name": "colour"}
RENAMED: JsonValue = {"original_name": "Colour"}
HUE: JsonValue = {"original_name": "hue"}
LISTED: JsonValue = {"values": [{"value": value} for value in VALUES]}
REFS: JsonValue = [{"system": "orchard", "code": "C1", "label": "Colour", "relation": "related"}]
MAPPED: JsonValue = {"concept": "orchard:colour"}
SYNTAX: JsonValue = {"format": "delimited", "delimiter": ";"}
PIPED: JsonValue = {"format": "delimited", "delimiter": "|"}
RANGE: JsonValue = {"min": 0, "max": 10}
CAT, STR, LIST = "category", "string", "list<category>"
PROP, ASSERTED, IMPORTED, DEFAULT = "proposed", "asserted", "imported", "imported_default"
DRAFT, CARRIED = f"/draft/{COLUMN}", f"/descriptors/{COLUMN}"
"""The prefixes of a refusal's path at the session and at the re-import."""


def carried(value: JsonValue, status: str) -> Outcome:
    """Nothing reported: the base's value and entry."""
    return ("carried", value, status, False)


def proposed(value: JsonValue, status: str) -> Outcome:
    """Reported ``proposed``: the importer's new proposal and entry replace the base's."""
    return ("proposed", value, status, False)


def returned(value: JsonValue, status: str) -> Outcome:
    """Reported ``returned``: the tombstone consumed, the new proposal back."""
    return ("returned", value, status, False)


ABSENT: Outcome = ("carried", None, None, False)
"""Nothing reported, no value and no tombstone."""
STANDING: Outcome = ("carried", None, None, True)
"""Nothing reported: the tombstone stands, the field absent."""
REMOVED: Outcome = ("removed", None, None, False)
"""Reported ``removed``: the inference is gone, and the field with it."""


def at_curation(*refusals: tuple[str, str]) -> Outcome:
    return ("refused", "curation", refusals)


def at_reimport(*refusals: tuple[str, str]) -> Outcome:
    return ("refused", "reimport", refusals)


DROPPED: Outcome = ("dropped", None, None, False)
"""Reported ``dropped``: the new proposal failed against the carried descriptor (D241)."""
NOT_DERIVED = at_curation(("INVALID_VALUE", f"{DRAFT}/fields/derived"))
"""A source column is not derived (``check_release``)."""
NOT_DERIVED_LIST = at_curation(
    ("INVALID_VALUE", f"{DRAFT}/fields/datatype"), ("INVALID_VALUE", f"{DRAFT}/fields/derived")
)
"""As ``NOT_DERIVED``, and a derived column is not a ``list<category>``."""
NO_ABSENT = at_curation(
    ("INVALID_VALUE", f"{DRAFT}/fields/permissible_values"),
    ("INVALID_VALUE", "/draft/trees/fields/source"),
)
"""D401's rule: a column with absent values declares permissible values, in a table of source kind
``pack``, which only a pack's importer lays out."""
NO_ABSENT_UNLESS_CATEGORY = at_curation(
    ("INVALID_VALUE", f"{DRAFT}/fields/datatype"), *cast(Any, NO_ABSENT)[2]
)
"""As ``NO_ABSENT``, and D401's column is a ``category``."""

# fmt: off
ROWS: dict[tuple[str, str, str], dict[str, Outcome]] = {
    ("datatype", "absent", "new"): {
        "none>cat": proposed(CAT, PROP), "none>str": proposed(STR, PROP),
        "none>list": proposed(LIST, PROP),
    },
    ("datatype", "absent", "none"): {
        "none>none": ABSENT,
    },
    ("datatype", "proposed", "equal"): {
        "cat>cat": carried(CAT, PROP), "str>str": carried(STR, PROP),
        "list>list": carried(LIST, PROP),
    },
    ("datatype", "proposed", "different"): {
        "cat>str": proposed(STR, PROP), "cat>list": proposed(LIST, PROP),
        "str>cat": proposed(CAT, PROP), "str>list": proposed(LIST, PROP),
        "list>cat": proposed(CAT, PROP), "list>str": proposed(STR, PROP),
    },
    ("datatype", "proposed", "gone"): {
        "cat>none": REMOVED, "str>none": REMOVED, "list>none": REMOVED,
    },
    ("datatype", "asserted", "equal"): {
        "cat>cat": carried(STR, ASSERTED), "str>str": carried(CAT, ASSERTED),
        "list>list": carried(STR, ASSERTED),
    },
    ("datatype", "asserted", "different"): {
        "cat>str": proposed(STR, PROP), "cat>list": proposed(LIST, PROP),
        "str>cat": proposed(CAT, PROP), "str>list": proposed(LIST, PROP),
        "list>cat": proposed(CAT, PROP), "list>str": proposed(STR, PROP),
    },
    ("datatype", "asserted", "new"): {
        "none>cat": carried(STR, ASSERTED), "none>str": carried(STR, ASSERTED),
        "none>list": carried(STR, ASSERTED),
    },
    ("datatype", "asserted", "gone"): {
        "cat>none": REMOVED, "str>none": REMOVED, "list>none": REMOVED,
    },
    ("datatype", "asserted", "none"): {
        "none>none": carried(STR, ASSERTED),
    },
    ("datatype", "confirmed", "equal"): {
        "cat>cat": carried(CAT, ASSERTED), "str>str": carried(STR, ASSERTED),
        "list>list": carried(LIST, ASSERTED),
    },
    ("datatype", "confirmed", "different"): {
        "cat>str": proposed(STR, PROP), "cat>list": proposed(LIST, PROP),
        "str>cat": proposed(CAT, PROP), "str>list": proposed(LIST, PROP),
        "list>cat": proposed(CAT, PROP), "list>str": proposed(STR, PROP),
    },
    ("datatype", "confirmed", "gone"): {
        "cat>none": REMOVED, "str>none": REMOVED, "list>none": REMOVED,
    },
    ("datatype", "tombstoned", "equal"): {
        "cat>cat": STANDING, "str>str": STANDING, "list>list": STANDING,
    },
    ("datatype", "tombstoned", "different"): {
        "cat>str": returned(STR, PROP), "cat>list": returned(LIST, PROP),
        "str>cat": returned(CAT, PROP), "str>list": returned(LIST, PROP),
        "list>cat": returned(CAT, PROP), "list>str": returned(STR, PROP),
    },
    ("datatype", "tombstoned", "gone"): {
        "cat>none": ABSENT, "str>none": ABSENT, "list>none": ABSENT,
    },
    ("units", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("units", "asserted", "none"): {
        "none>none": carried("1", ASSERTED), "none>cat": carried("1", ASSERTED),
        "none>str": carried("1", ASSERTED), "none>list": carried("1", ASSERTED),
        "cat>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "cat>cat": carried("1", ASSERTED),
        "cat>str": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "cat>list": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "str>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "str>cat": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "str>str": carried("1", ASSERTED),
        "str>list": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "list>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "list>cat": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "list>str": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
        "list>list": carried("1", ASSERTED),
    },
    ("range", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("range", "asserted", "none"): {
        "none>none": carried(RANGE, ASSERTED), "none>cat": carried(RANGE, ASSERTED),
        "none>str": carried(RANGE, ASSERTED), "none>list": carried(RANGE, ASSERTED),
        "cat>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "cat>cat": carried(RANGE, ASSERTED),
        "cat>str": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "cat>list": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "str>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "str>cat": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "str>str": carried(RANGE, ASSERTED),
        "str>list": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "list>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "list>cat": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "list>str": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
        "list>list": carried(RANGE, ASSERTED),
    },
    ("permissible_values", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("permissible_values", "asserted", "none"): {
        "none>none": carried(LISTED, ASSERTED), "none>cat": carried(LISTED, ASSERTED),
        "none>str": carried(LISTED, ASSERTED), "none>list": carried(LISTED, ASSERTED),
        "cat>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
        "cat>cat": carried(LISTED, ASSERTED),
        "cat>str": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
        "cat>list": carried(LISTED, ASSERTED),
        "str>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
        "str>cat": carried(LISTED, ASSERTED), "str>str": carried(LISTED, ASSERTED),
        "str>list": carried(LISTED, ASSERTED),
        "list>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
        "list>cat": carried(LISTED, ASSERTED),
        "list>str": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
        "list>list": carried(LISTED, ASSERTED),
    },
    ("missing_codes", "absent", "new"): {
        "none>none": proposed(NA, DEFAULT), "none>cat": proposed(NA, DEFAULT),
        "none>str": proposed(NA, DEFAULT), "none>list": proposed(NA, DEFAULT),
        "cat>none": proposed(NA, DEFAULT), "cat>cat": proposed(NA, DEFAULT),
        "cat>str": proposed(NA, DEFAULT), "cat>list": proposed(NA, DEFAULT),
        "str>none": proposed(NA, DEFAULT), "str>cat": proposed(NA, DEFAULT),
        "str>str": proposed(NA, DEFAULT), "str>list": proposed(NA, DEFAULT),
        "list>none": proposed(NA, DEFAULT), "list>cat": proposed(NA, DEFAULT),
        "list>str": proposed(NA, DEFAULT), "list>list": proposed(NA, DEFAULT),
    },
    ("missing_codes", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("missing_codes", "imported_default", "equal"): {
        "none>none": carried(NA, DEFAULT), "none>cat": carried(NA, DEFAULT),
        "none>str": carried(NA, DEFAULT), "none>list": carried(NA, DEFAULT),
        "cat>none": carried(NA, DEFAULT), "cat>cat": carried(NA, DEFAULT),
        "cat>str": carried(NA, DEFAULT), "cat>list": carried(NA, DEFAULT),
        "str>none": carried(NA, DEFAULT), "str>cat": carried(NA, DEFAULT),
        "str>str": carried(NA, DEFAULT), "str>list": carried(NA, DEFAULT),
        "list>none": carried(NA, DEFAULT), "list>cat": carried(NA, DEFAULT),
        "list>str": carried(NA, DEFAULT), "list>list": carried(NA, DEFAULT),
    },
    ("missing_codes", "imported_default", "different"): {
        "none>none": proposed(BOTH, DEFAULT), "none>cat": proposed(BOTH, DEFAULT),
        "none>str": proposed(BOTH, DEFAULT), "none>list": proposed(BOTH, DEFAULT),
        "cat>none": proposed(BOTH, DEFAULT), "cat>cat": proposed(BOTH, DEFAULT),
        "cat>str": proposed(BOTH, DEFAULT), "cat>list": proposed(BOTH, DEFAULT),
        "str>none": proposed(BOTH, DEFAULT), "str>cat": proposed(BOTH, DEFAULT),
        "str>str": proposed(BOTH, DEFAULT), "str>list": proposed(BOTH, DEFAULT),
        "list>none": proposed(BOTH, DEFAULT), "list>cat": proposed(BOTH, DEFAULT),
        "list>str": proposed(BOTH, DEFAULT), "list>list": proposed(BOTH, DEFAULT),
    },
    ("missing_codes", "imported_default", "gone"): {
        "none>none": REMOVED, "none>cat": REMOVED, "none>str": REMOVED, "none>list": REMOVED,
        "cat>none": REMOVED, "cat>cat": REMOVED, "cat>str": REMOVED, "cat>list": REMOVED,
        "str>none": REMOVED, "str>cat": REMOVED, "str>str": REMOVED, "str>list": REMOVED,
        "list>none": REMOVED, "list>cat": REMOVED, "list>str": REMOVED, "list>list": REMOVED,
    },
    ("missing_codes", "asserted", "equal"): {
        "none>none": carried(NA_NOT_APPLICABLE, ASSERTED),
        "none>cat": carried(NA_NOT_APPLICABLE, ASSERTED),
        "none>str": carried(NA_NOT_APPLICABLE, ASSERTED),
        "none>list": carried(NA_NOT_APPLICABLE, ASSERTED),
        "cat>none": carried(NA_NOT_APPLICABLE, ASSERTED),
        "cat>cat": carried(NA_NOT_APPLICABLE, ASSERTED),
        "cat>str": carried(NA_NOT_APPLICABLE, ASSERTED),
        "cat>list": carried(NA_NOT_APPLICABLE, ASSERTED),
        "str>none": carried(NA_NOT_APPLICABLE, ASSERTED),
        "str>cat": carried(NA_NOT_APPLICABLE, ASSERTED),
        "str>str": carried(NA_NOT_APPLICABLE, ASSERTED),
        "str>list": carried(NA_NOT_APPLICABLE, ASSERTED),
        "list>none": carried(NA_NOT_APPLICABLE, ASSERTED),
        "list>cat": carried(NA_NOT_APPLICABLE, ASSERTED),
        "list>str": carried(NA_NOT_APPLICABLE, ASSERTED),
        "list>list": carried(NA_NOT_APPLICABLE, ASSERTED),
    },
    ("missing_codes", "asserted", "different"): {
        "none>none": proposed(BOTH, DEFAULT), "none>cat": proposed(BOTH, DEFAULT),
        "none>str": proposed(BOTH, DEFAULT), "none>list": proposed(BOTH, DEFAULT),
        "cat>none": proposed(BOTH, DEFAULT), "cat>cat": proposed(BOTH, DEFAULT),
        "cat>str": proposed(BOTH, DEFAULT), "cat>list": proposed(BOTH, DEFAULT),
        "str>none": proposed(BOTH, DEFAULT), "str>cat": proposed(BOTH, DEFAULT),
        "str>str": proposed(BOTH, DEFAULT), "str>list": proposed(BOTH, DEFAULT),
        "list>none": proposed(BOTH, DEFAULT), "list>cat": proposed(BOTH, DEFAULT),
        "list>str": proposed(BOTH, DEFAULT), "list>list": proposed(BOTH, DEFAULT),
    },
    ("missing_codes", "asserted", "new"): {
        "none>none": carried(DASH, ASSERTED), "none>cat": carried(DASH, ASSERTED),
        "none>str": carried(DASH, ASSERTED), "none>list": carried(DASH, ASSERTED),
        "cat>none": carried(DASH, ASSERTED), "cat>cat": carried(DASH, ASSERTED),
        "cat>str": carried(DASH, ASSERTED), "cat>list": carried(DASH, ASSERTED),
        "str>none": carried(DASH, ASSERTED), "str>cat": carried(DASH, ASSERTED),
        "str>str": carried(DASH, ASSERTED), "str>list": carried(DASH, ASSERTED),
        "list>none": carried(DASH, ASSERTED), "list>cat": carried(DASH, ASSERTED),
        "list>str": carried(DASH, ASSERTED), "list>list": carried(DASH, ASSERTED),
    },
    ("missing_codes", "asserted", "gone"): {
        "none>none": REMOVED, "none>cat": REMOVED, "none>str": REMOVED, "none>list": REMOVED,
        "cat>none": REMOVED, "cat>cat": REMOVED, "cat>str": REMOVED, "cat>list": REMOVED,
        "str>none": REMOVED, "str>cat": REMOVED, "str>str": REMOVED, "str>list": REMOVED,
        "list>none": REMOVED, "list>cat": REMOVED, "list>str": REMOVED, "list>list": REMOVED,
    },
    ("missing_codes", "asserted", "none"): {
        "none>none": carried(DASH, ASSERTED), "none>cat": carried(DASH, ASSERTED),
        "none>str": carried(DASH, ASSERTED), "none>list": carried(DASH, ASSERTED),
        "cat>none": carried(DASH, ASSERTED), "cat>cat": carried(DASH, ASSERTED),
        "cat>str": carried(DASH, ASSERTED), "cat>list": carried(DASH, ASSERTED),
        "str>none": carried(DASH, ASSERTED), "str>cat": carried(DASH, ASSERTED),
        "str>str": carried(DASH, ASSERTED), "str>list": carried(DASH, ASSERTED),
        "list>none": carried(DASH, ASSERTED), "list>cat": carried(DASH, ASSERTED),
        "list>str": carried(DASH, ASSERTED), "list>list": carried(DASH, ASSERTED),
    },
    ("missing_codes", "confirmed", "equal"): {
        "none>none": carried(NA, ASSERTED), "none>cat": carried(NA, ASSERTED),
        "none>str": carried(NA, ASSERTED), "none>list": carried(NA, ASSERTED),
        "cat>none": carried(NA, ASSERTED), "cat>cat": carried(NA, ASSERTED),
        "cat>str": carried(NA, ASSERTED), "cat>list": carried(NA, ASSERTED),
        "str>none": carried(NA, ASSERTED), "str>cat": carried(NA, ASSERTED),
        "str>str": carried(NA, ASSERTED), "str>list": carried(NA, ASSERTED),
        "list>none": carried(NA, ASSERTED), "list>cat": carried(NA, ASSERTED),
        "list>str": carried(NA, ASSERTED), "list>list": carried(NA, ASSERTED),
    },
    ("missing_codes", "confirmed", "different"): {
        "none>none": proposed(BOTH, DEFAULT), "none>cat": proposed(BOTH, DEFAULT),
        "none>str": proposed(BOTH, DEFAULT), "none>list": proposed(BOTH, DEFAULT),
        "cat>none": proposed(BOTH, DEFAULT), "cat>cat": proposed(BOTH, DEFAULT),
        "cat>str": proposed(BOTH, DEFAULT), "cat>list": proposed(BOTH, DEFAULT),
        "str>none": proposed(BOTH, DEFAULT), "str>cat": proposed(BOTH, DEFAULT),
        "str>str": proposed(BOTH, DEFAULT), "str>list": proposed(BOTH, DEFAULT),
        "list>none": proposed(BOTH, DEFAULT), "list>cat": proposed(BOTH, DEFAULT),
        "list>str": proposed(BOTH, DEFAULT), "list>list": proposed(BOTH, DEFAULT),
    },
    ("missing_codes", "confirmed", "gone"): {
        "none>none": REMOVED, "none>cat": REMOVED, "none>str": REMOVED, "none>list": REMOVED,
        "cat>none": REMOVED, "cat>cat": REMOVED, "cat>str": REMOVED, "cat>list": REMOVED,
        "str>none": REMOVED, "str>cat": REMOVED, "str>str": REMOVED, "str>list": REMOVED,
        "list>none": REMOVED, "list>cat": REMOVED, "list>str": REMOVED, "list>list": REMOVED,
    },
    ("missing_codes", "tombstoned", "equal"): {
        "none>none": STANDING, "none>cat": STANDING, "none>str": STANDING, "none>list": STANDING,
        "cat>none": STANDING, "cat>cat": STANDING, "cat>str": STANDING, "cat>list": STANDING,
        "str>none": STANDING, "str>cat": STANDING, "str>str": STANDING, "str>list": STANDING,
        "list>none": STANDING, "list>cat": STANDING, "list>str": STANDING, "list>list": STANDING,
    },
    ("missing_codes", "tombstoned", "different"): {
        "none>none": returned(BOTH, DEFAULT), "none>cat": returned(BOTH, DEFAULT),
        "none>str": returned(BOTH, DEFAULT), "none>list": returned(BOTH, DEFAULT),
        "cat>none": returned(BOTH, DEFAULT), "cat>cat": returned(BOTH, DEFAULT),
        "cat>str": returned(BOTH, DEFAULT), "cat>list": returned(BOTH, DEFAULT),
        "str>none": returned(BOTH, DEFAULT), "str>cat": returned(BOTH, DEFAULT),
        "str>str": returned(BOTH, DEFAULT), "str>list": returned(BOTH, DEFAULT),
        "list>none": returned(BOTH, DEFAULT), "list>cat": returned(BOTH, DEFAULT),
        "list>str": returned(BOTH, DEFAULT), "list>list": returned(BOTH, DEFAULT),
    },
    ("missing_codes", "tombstoned", "gone"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("identifier", "absent", "new"): {
        "none>str": proposed(True, PROP), "cat>str": proposed(True, PROP),
        "str>str": proposed(True, PROP), "list>str": proposed(True, PROP),
    },
    ("identifier", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("identifier", "proposed", "equal"): {
        "str>str": carried(True, PROP),
    },
    ("identifier", "proposed", "gone"): {
        "str>none": REMOVED, "str>cat": REMOVED, "str>str": REMOVED, "str>list": REMOVED,
    },
    ("identifier", "asserted", "equal"): {
        "str>str": carried(False, ASSERTED),
    },
    ("identifier", "asserted", "new"): {
        "none>str": carried(True, ASSERTED), "cat>str": carried(True, ASSERTED),
        "str>str": carried(True, ASSERTED), "list>str": carried(True, ASSERTED),
    },
    ("identifier", "asserted", "gone"): {
        "str>none": REMOVED, "str>cat": REMOVED, "str>str": REMOVED, "str>list": REMOVED,
    },
    ("identifier", "asserted", "none"): {
        "none>none": carried(True, ASSERTED), "none>cat": carried(True, ASSERTED),
        "none>str": carried(True, ASSERTED), "none>list": carried(True, ASSERTED),
        "cat>none": carried(True, ASSERTED), "cat>cat": carried(True, ASSERTED),
        "cat>str": carried(True, ASSERTED), "cat>list": carried(True, ASSERTED),
        "str>none": carried(True, ASSERTED), "str>cat": carried(True, ASSERTED),
        "str>str": carried(True, ASSERTED), "str>list": carried(True, ASSERTED),
        "list>none": carried(True, ASSERTED), "list>cat": carried(True, ASSERTED),
        "list>str": carried(True, ASSERTED), "list>list": carried(True, ASSERTED),
    },
    ("identifier", "confirmed", "equal"): {
        "str>str": carried(True, ASSERTED),
    },
    ("identifier", "confirmed", "gone"): {
        "str>none": REMOVED, "str>cat": REMOVED, "str>str": REMOVED, "str>list": REMOVED,
    },
    ("identifier", "tombstoned", "equal"): {
        "str>str": STANDING,
    },
    ("identifier", "tombstoned", "gone"): {
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
    },
    ("concepts", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("concepts", "asserted", "none"): {
        "none>none": carried(REFS, ASSERTED), "none>cat": carried(REFS, ASSERTED),
        "none>str": carried(REFS, ASSERTED), "none>list": carried(REFS, ASSERTED),
        "cat>none": carried(REFS, ASSERTED), "cat>cat": carried(REFS, ASSERTED),
        "cat>str": carried(REFS, ASSERTED), "cat>list": carried(REFS, ASSERTED),
        "str>none": carried(REFS, ASSERTED), "str>cat": carried(REFS, ASSERTED),
        "str>str": carried(REFS, ASSERTED), "str>list": carried(REFS, ASSERTED),
        "list>none": carried(REFS, ASSERTED), "list>cat": carried(REFS, ASSERTED),
        "list>str": carried(REFS, ASSERTED), "list>list": carried(REFS, ASSERTED),
    },
    ("maps_to", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("maps_to", "asserted", "none"): {
        "none>none": carried(MAPPED, ASSERTED), "none>cat": carried(MAPPED, ASSERTED),
        "none>str": carried(MAPPED, ASSERTED), "none>list": carried(MAPPED, ASSERTED),
        "cat>none": carried(MAPPED, ASSERTED), "cat>cat": carried(MAPPED, ASSERTED),
        "cat>str": carried(MAPPED, ASSERTED), "cat>list": carried(MAPPED, ASSERTED),
        "str>none": carried(MAPPED, ASSERTED), "str>cat": carried(MAPPED, ASSERTED),
        "str>str": carried(MAPPED, ASSERTED), "str>list": carried(MAPPED, ASSERTED),
        "list>none": carried(MAPPED, ASSERTED), "list>cat": carried(MAPPED, ASSERTED),
        "list>str": carried(MAPPED, ASSERTED), "list>list": carried(MAPPED, ASSERTED),
    },
    ("derived", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("derived", "asserted", "none"): {
        "none>none": NOT_DERIVED, "none>cat": NOT_DERIVED, "none>str": NOT_DERIVED,
        "none>list": NOT_DERIVED, "cat>none": NOT_DERIVED, "cat>cat": NOT_DERIVED,
        "cat>str": NOT_DERIVED, "cat>list": NOT_DERIVED, "str>none": NOT_DERIVED,
        "str>cat": NOT_DERIVED, "str>str": NOT_DERIVED, "str>list": NOT_DERIVED,
        "list>none": NOT_DERIVED_LIST, "list>cat": NOT_DERIVED_LIST, "list>str": NOT_DERIVED_LIST,
        "list>list": NOT_DERIVED_LIST,
    },
    ("list_syntax", "absent", "new"): {
        "none>list": proposed(SYNTAX, PROP), "cat>list": proposed(SYNTAX, PROP),
        "str>list": proposed(SYNTAX, PROP),
    },
    ("list_syntax", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "cat>none": ABSENT,
        "cat>cat": ABSENT, "cat>str": ABSENT, "str>none": ABSENT, "str>cat": ABSENT,
        "str>str": ABSENT,
    },
    ("list_syntax", "proposed", "equal"): {
        "list>list": carried(SYNTAX, PROP),
    },
    ("list_syntax", "proposed", "different"): {
        "list>list": proposed(PIPED, PROP),
    },
    ("list_syntax", "proposed", "gone"): {
        "list>none": REMOVED, "list>cat": REMOVED, "list>str": REMOVED,
    },
    ("list_syntax", "asserted", "equal"): {
        "list>list": carried(PIPED, ASSERTED),
    },
    ("list_syntax", "asserted", "different"): {
        "list>list": proposed(PIPED, PROP),
    },
    ("list_syntax", "asserted", "new"): {
        "none>list": carried(SYNTAX, ASSERTED), "cat>list": carried(SYNTAX, ASSERTED),
        "str>list": carried(SYNTAX, ASSERTED),
    },
    ("list_syntax", "asserted", "gone"): {
        "list>none": REMOVED, "list>cat": REMOVED, "list>str": REMOVED,
    },
    ("list_syntax", "asserted", "none"): {
        "none>none": carried(SYNTAX, ASSERTED), "none>cat": carried(SYNTAX, ASSERTED),
        "none>str": carried(SYNTAX, ASSERTED),
        "cat>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
        "cat>cat": carried(SYNTAX, ASSERTED),
        "cat>str": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
        "str>none": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
        "str>cat": at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
        "str>str": carried(SYNTAX, ASSERTED),
    },
    ("list_syntax", "confirmed", "equal"): {
        "list>list": carried(SYNTAX, ASSERTED),
    },
    ("list_syntax", "confirmed", "different"): {
        "list>list": proposed(PIPED, PROP),
    },
    ("list_syntax", "confirmed", "gone"): {
        "list>none": REMOVED, "list>cat": REMOVED, "list>str": REMOVED,
    },
    ("list_syntax", "tombstoned", "equal"): {
        "list>list": STANDING,
    },
    ("list_syntax", "tombstoned", "different"): {
        "list>list": DROPPED,
    },
    ("list_syntax", "tombstoned", "gone"): {
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT,
    },
    ("completeness", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("completeness", "asserted", "none"): {
        "none>none": carried("complete", ASSERTED), "none>cat": carried("complete", ASSERTED),
        "none>str": carried("complete", ASSERTED), "none>list": carried("complete", ASSERTED),
        "cat>none": carried("complete", ASSERTED), "cat>cat": carried("complete", ASSERTED),
        "cat>str": carried("complete", ASSERTED), "cat>list": carried("complete", ASSERTED),
        "str>none": carried("complete", ASSERTED), "str>cat": carried("complete", ASSERTED),
        "str>str": carried("complete", ASSERTED), "str>list": carried("complete", ASSERTED),
        "list>none": carried("complete", ASSERTED), "list>cat": carried("complete", ASSERTED),
        "list>str": carried("complete", ASSERTED), "list>list": carried("complete", ASSERTED),
    },
    ("source", "imported", "equal"): {
        "none>none": carried(HEADER, IMPORTED), "none>cat": carried(HEADER, IMPORTED),
        "none>str": carried(HEADER, IMPORTED), "none>list": carried(HEADER, IMPORTED),
        "cat>none": carried(HEADER, IMPORTED), "cat>cat": carried(HEADER, IMPORTED),
        "cat>str": carried(HEADER, IMPORTED), "cat>list": carried(HEADER, IMPORTED),
        "str>none": carried(HEADER, IMPORTED), "str>cat": carried(HEADER, IMPORTED),
        "str>str": carried(HEADER, IMPORTED), "str>list": carried(HEADER, IMPORTED),
        "list>none": carried(HEADER, IMPORTED), "list>cat": carried(HEADER, IMPORTED),
        "list>str": carried(HEADER, IMPORTED), "list>list": carried(HEADER, IMPORTED),
    },
    ("source", "imported", "different"): {
        "none>none": proposed(RENAMED, IMPORTED), "none>cat": proposed(RENAMED, IMPORTED),
        "none>str": proposed(RENAMED, IMPORTED), "none>list": proposed(RENAMED, IMPORTED),
        "cat>none": proposed(RENAMED, IMPORTED), "cat>cat": proposed(RENAMED, IMPORTED),
        "cat>str": proposed(RENAMED, IMPORTED), "cat>list": proposed(RENAMED, IMPORTED),
        "str>none": proposed(RENAMED, IMPORTED), "str>cat": proposed(RENAMED, IMPORTED),
        "str>str": proposed(RENAMED, IMPORTED), "str>list": proposed(RENAMED, IMPORTED),
        "list>none": proposed(RENAMED, IMPORTED), "list>cat": proposed(RENAMED, IMPORTED),
        "list>str": proposed(RENAMED, IMPORTED), "list>list": proposed(RENAMED, IMPORTED),
    },
    ("source", "asserted", "equal"): {
        "none>none": carried(HUE, ASSERTED), "none>cat": carried(HUE, ASSERTED),
        "none>str": carried(HUE, ASSERTED), "none>list": carried(HUE, ASSERTED),
        "cat>none": carried(HUE, ASSERTED), "cat>cat": carried(HUE, ASSERTED),
        "cat>str": carried(HUE, ASSERTED), "cat>list": carried(HUE, ASSERTED),
        "str>none": carried(HUE, ASSERTED), "str>cat": carried(HUE, ASSERTED),
        "str>str": carried(HUE, ASSERTED), "str>list": carried(HUE, ASSERTED),
        "list>none": carried(HUE, ASSERTED), "list>cat": carried(HUE, ASSERTED),
        "list>str": carried(HUE, ASSERTED), "list>list": carried(HUE, ASSERTED),
    },
    ("source", "asserted", "different"): {
        "none>none": proposed(RENAMED, IMPORTED), "none>cat": proposed(RENAMED, IMPORTED),
        "none>str": proposed(RENAMED, IMPORTED), "none>list": proposed(RENAMED, IMPORTED),
        "cat>none": proposed(RENAMED, IMPORTED), "cat>cat": proposed(RENAMED, IMPORTED),
        "cat>str": proposed(RENAMED, IMPORTED), "cat>list": proposed(RENAMED, IMPORTED),
        "str>none": proposed(RENAMED, IMPORTED), "str>cat": proposed(RENAMED, IMPORTED),
        "str>str": proposed(RENAMED, IMPORTED), "str>list": proposed(RENAMED, IMPORTED),
        "list>none": proposed(RENAMED, IMPORTED), "list>cat": proposed(RENAMED, IMPORTED),
        "list>str": proposed(RENAMED, IMPORTED), "list>list": proposed(RENAMED, IMPORTED),
    },
    ("source", "confirmed", "equal"): {
        "none>none": carried(HEADER, ASSERTED), "none>cat": carried(HEADER, ASSERTED),
        "none>str": carried(HEADER, ASSERTED), "none>list": carried(HEADER, ASSERTED),
        "cat>none": carried(HEADER, ASSERTED), "cat>cat": carried(HEADER, ASSERTED),
        "cat>str": carried(HEADER, ASSERTED), "cat>list": carried(HEADER, ASSERTED),
        "str>none": carried(HEADER, ASSERTED), "str>cat": carried(HEADER, ASSERTED),
        "str>str": carried(HEADER, ASSERTED), "str>list": carried(HEADER, ASSERTED),
        "list>none": carried(HEADER, ASSERTED), "list>cat": carried(HEADER, ASSERTED),
        "list>str": carried(HEADER, ASSERTED), "list>list": carried(HEADER, ASSERTED),
    },
    ("source", "confirmed", "different"): {
        "none>none": proposed(RENAMED, IMPORTED), "none>cat": proposed(RENAMED, IMPORTED),
        "none>str": proposed(RENAMED, IMPORTED), "none>list": proposed(RENAMED, IMPORTED),
        "cat>none": proposed(RENAMED, IMPORTED), "cat>cat": proposed(RENAMED, IMPORTED),
        "cat>str": proposed(RENAMED, IMPORTED), "cat>list": proposed(RENAMED, IMPORTED),
        "str>none": proposed(RENAMED, IMPORTED), "str>cat": proposed(RENAMED, IMPORTED),
        "str>str": proposed(RENAMED, IMPORTED), "str>list": proposed(RENAMED, IMPORTED),
        "list>none": proposed(RENAMED, IMPORTED), "list>cat": proposed(RENAMED, IMPORTED),
        "list>str": proposed(RENAMED, IMPORTED), "list>list": proposed(RENAMED, IMPORTED),
    },
    ("source", "tombstoned", "equal"): {
        "none>none": STANDING, "none>cat": STANDING, "none>str": STANDING, "none>list": STANDING,
        "cat>none": STANDING, "cat>cat": STANDING, "cat>str": STANDING, "cat>list": STANDING,
        "str>none": STANDING, "str>cat": STANDING, "str>str": STANDING, "str>list": STANDING,
        "list>none": STANDING, "list>cat": STANDING, "list>str": STANDING, "list>list": STANDING,
    },
    ("source", "tombstoned", "different"): {
        "none>none": returned(RENAMED, IMPORTED), "none>cat": returned(RENAMED, IMPORTED),
        "none>str": returned(RENAMED, IMPORTED), "none>list": returned(RENAMED, IMPORTED),
        "cat>none": returned(RENAMED, IMPORTED), "cat>cat": returned(RENAMED, IMPORTED),
        "cat>str": returned(RENAMED, IMPORTED), "cat>list": returned(RENAMED, IMPORTED),
        "str>none": returned(RENAMED, IMPORTED), "str>cat": returned(RENAMED, IMPORTED),
        "str>str": returned(RENAMED, IMPORTED), "str>list": returned(RENAMED, IMPORTED),
        "list>none": returned(RENAMED, IMPORTED), "list>cat": returned(RENAMED, IMPORTED),
        "list>str": returned(RENAMED, IMPORTED), "list>list": returned(RENAMED, IMPORTED),
    },
    ("absent", "absent", "none"): {
        "none>none": ABSENT, "none>cat": ABSENT, "none>str": ABSENT, "none>list": ABSENT,
        "cat>none": ABSENT, "cat>cat": ABSENT, "cat>str": ABSENT, "cat>list": ABSENT,
        "str>none": ABSENT, "str>cat": ABSENT, "str>str": ABSENT, "str>list": ABSENT,
        "list>none": ABSENT, "list>cat": ABSENT, "list>str": ABSENT, "list>list": ABSENT,
    },
    ("absent", "asserted", "none"): {
        "none>none": NO_ABSENT_UNLESS_CATEGORY, "none>cat": NO_ABSENT_UNLESS_CATEGORY,
        "none>str": NO_ABSENT_UNLESS_CATEGORY, "none>list": NO_ABSENT_UNLESS_CATEGORY,
        "cat>none": NO_ABSENT, "cat>cat": NO_ABSENT, "cat>str": NO_ABSENT, "cat>list": NO_ABSENT,
        "str>none": NO_ABSENT_UNLESS_CATEGORY, "str>cat": NO_ABSENT_UNLESS_CATEGORY,
        "str>str": NO_ABSENT_UNLESS_CATEGORY, "str>list": NO_ABSENT_UNLESS_CATEGORY,
        "list>none": NO_ABSENT_UNLESS_CATEGORY, "list>cat": NO_ABSENT_UNLESS_CATEGORY,
        "list>str": NO_ABSENT_UNLESS_CATEGORY, "list>list": NO_ABSENT_UNLESS_CATEGORY,
    },
}
# fmt: on
"""Every reachable cell's outcome, by (field, state, relation) and then pair, read off the real
run and checked against ``carry.py``."""

EXPECTED: dict[Cell, Outcome] = {
    (*key, pair): outcome for key, row in ROWS.items() for pair, outcome in row.items()
}

UNDATED_RANGE = at_reimport(
    ("WRONG_TYPE", f"{CARRIED}/fields/range/max"), ("WRONG_TYPE", f"{CARRIED}/fields/range/min")
)
"""A range of integers over a column re-imported as dates: its bounds are not dates."""

# fmt: off
TYPED_EXPECTED: dict[Cell, Outcome] = {
    ("units", "asserted", "none", "none>int"): carried("1", ASSERTED),
    ("range", "asserted", "none", "none>int"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "none>int"): carried(LISTED, ASSERTED),
    ("list_syntax", "asserted", "none", "none>int"): carried(SYNTAX, ASSERTED),
    ("datatype", "absent", "new", "none>int"): proposed("integer", PROP),
    ("datatype", "asserted", "new", "none>int"): carried(STR, ASSERTED),
    ("units", "asserted", "none", "none>num"): carried("1", ASSERTED),
    ("range", "asserted", "none", "none>num"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "none>num"): carried(LISTED, ASSERTED),
    ("list_syntax", "asserted", "none", "none>num"): carried(SYNTAX, ASSERTED),
    ("datatype", "absent", "new", "none>num"): proposed("number", PROP),
    ("datatype", "asserted", "new", "none>num"): carried(STR, ASSERTED),
    ("units", "asserted", "none", "none>date"): carried("1", ASSERTED),
    ("range", "asserted", "none", "none>date"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "none>date"): carried(LISTED, ASSERTED),
    ("list_syntax", "asserted", "none", "none>date"): carried(SYNTAX, ASSERTED),
    ("datatype", "absent", "new", "none>date"): proposed("date", PROP),
    ("datatype", "asserted", "new", "none>date"): carried(STR, ASSERTED),
    ("units", "asserted", "none", "none>bool"): carried("1", ASSERTED),
    ("range", "asserted", "none", "none>bool"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "none>bool"): carried(LISTED, ASSERTED),
    ("list_syntax", "asserted", "none", "none>bool"): carried(SYNTAX, ASSERTED),
    ("datatype", "absent", "new", "none>bool"): proposed("boolean", PROP),
    ("datatype", "asserted", "new", "none>bool"): carried(STR, ASSERTED),
    ("units", "asserted", "none", "cat>int"): carried("1", ASSERTED),
    ("range", "asserted", "none", "cat>int"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "cat>int"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "none", "cat>int"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
    ("datatype", "proposed", "different", "cat>int"): proposed("integer", PROP),
    ("datatype", "asserted", "different", "cat>int"): proposed("integer", PROP),
    ("datatype", "confirmed", "different", "cat>int"): proposed("integer", PROP),
    ("datatype", "tombstoned", "different", "cat>int"): returned("integer", PROP),
    ("units", "asserted", "none", "cat>num"): carried("1", ASSERTED),
    ("range", "asserted", "none", "cat>num"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "cat>num"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "none", "cat>num"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
    ("datatype", "proposed", "different", "cat>num"): proposed("number", PROP),
    ("datatype", "asserted", "different", "cat>num"): proposed("number", PROP),
    ("datatype", "confirmed", "different", "cat>num"): proposed("number", PROP),
    ("datatype", "tombstoned", "different", "cat>num"): returned("number", PROP),
    ("units", "asserted", "none", "cat>date"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
    ("range", "asserted", "none", "cat>date"): UNDATED_RANGE,
    ("permissible_values", "asserted", "none", "cat>date"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "none", "cat>date"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
    ("datatype", "proposed", "different", "cat>date"): proposed("date", PROP),
    ("datatype", "asserted", "different", "cat>date"): proposed("date", PROP),
    ("datatype", "confirmed", "different", "cat>date"): proposed("date", PROP),
    ("datatype", "tombstoned", "different", "cat>date"): returned("date", PROP),
    ("units", "asserted", "none", "cat>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
    ("range", "asserted", "none", "cat>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
    ("permissible_values", "asserted", "none", "cat>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "none", "cat>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
    ("datatype", "proposed", "different", "cat>bool"): proposed("boolean", PROP),
    ("datatype", "asserted", "different", "cat>bool"): proposed("boolean", PROP),
    ("datatype", "confirmed", "different", "cat>bool"): proposed("boolean", PROP),
    ("datatype", "tombstoned", "different", "cat>bool"): returned("boolean", PROP),
    ("units", "asserted", "none", "str>int"): carried("1", ASSERTED),
    ("range", "asserted", "none", "str>int"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "str>int"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "none", "str>int"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
    ("datatype", "proposed", "different", "str>int"): proposed("integer", PROP),
    ("datatype", "asserted", "different", "str>int"): proposed("integer", PROP),
    ("datatype", "confirmed", "different", "str>int"): proposed("integer", PROP),
    ("datatype", "tombstoned", "different", "str>int"): returned("integer", PROP),
    ("units", "asserted", "none", "str>num"): carried("1", ASSERTED),
    ("range", "asserted", "none", "str>num"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "str>num"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "none", "str>num"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
    ("datatype", "proposed", "different", "str>num"): proposed("number", PROP),
    ("datatype", "asserted", "different", "str>num"): proposed("number", PROP),
    ("datatype", "confirmed", "different", "str>num"): proposed("number", PROP),
    ("datatype", "tombstoned", "different", "str>num"): returned("number", PROP),
    ("units", "asserted", "none", "str>date"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
    ("range", "asserted", "none", "str>date"): UNDATED_RANGE,
    ("permissible_values", "asserted", "none", "str>date"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "none", "str>date"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
    ("datatype", "proposed", "different", "str>date"): proposed("date", PROP),
    ("datatype", "asserted", "different", "str>date"): proposed("date", PROP),
    ("datatype", "confirmed", "different", "str>date"): proposed("date", PROP),
    ("datatype", "tombstoned", "different", "str>date"): returned("date", PROP),
    ("units", "asserted", "none", "str>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
    ("range", "asserted", "none", "str>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
    ("permissible_values", "asserted", "none", "str>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "none", "str>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/list_syntax")),
    ("datatype", "proposed", "different", "str>bool"): proposed("boolean", PROP),
    ("datatype", "asserted", "different", "str>bool"): proposed("boolean", PROP),
    ("datatype", "confirmed", "different", "str>bool"): proposed("boolean", PROP),
    ("datatype", "tombstoned", "different", "str>bool"): returned("boolean", PROP),
    ("units", "asserted", "none", "list>int"): carried("1", ASSERTED),
    ("range", "asserted", "none", "list>int"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "list>int"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "gone", "list>int"): REMOVED,
    ("datatype", "proposed", "different", "list>int"): proposed("integer", PROP),
    ("datatype", "asserted", "different", "list>int"): proposed("integer", PROP),
    ("datatype", "confirmed", "different", "list>int"): proposed("integer", PROP),
    ("datatype", "tombstoned", "different", "list>int"): returned("integer", PROP),
    ("units", "asserted", "none", "list>num"): carried("1", ASSERTED),
    ("range", "asserted", "none", "list>num"): carried(RANGE, ASSERTED),
    ("permissible_values", "asserted", "none", "list>num"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "gone", "list>num"): REMOVED,
    ("datatype", "proposed", "different", "list>num"): proposed("number", PROP),
    ("datatype", "asserted", "different", "list>num"): proposed("number", PROP),
    ("datatype", "confirmed", "different", "list>num"): proposed("number", PROP),
    ("datatype", "tombstoned", "different", "list>num"): returned("number", PROP),
    ("units", "asserted", "none", "list>date"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
    ("range", "asserted", "none", "list>date"): UNDATED_RANGE,
    ("permissible_values", "asserted", "none", "list>date"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "gone", "list>date"): REMOVED,
    ("datatype", "proposed", "different", "list>date"): proposed("date", PROP),
    ("datatype", "asserted", "different", "list>date"): proposed("date", PROP),
    ("datatype", "confirmed", "different", "list>date"): proposed("date", PROP),
    ("datatype", "tombstoned", "different", "list>date"): returned("date", PROP),
    ("units", "asserted", "none", "list>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/units")),
    ("range", "asserted", "none", "list>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/range")),
    ("permissible_values", "asserted", "none", "list>bool"):
        at_reimport(("CONFLICTING_MEMBERS", CARRIED + "/fields/permissible_values")),
    ("list_syntax", "asserted", "gone", "list>bool"): REMOVED,
    ("datatype", "proposed", "different", "list>bool"): proposed("boolean", PROP),
    ("datatype", "asserted", "different", "list>bool"): proposed("boolean", PROP),
    ("datatype", "confirmed", "different", "list>bool"): proposed("boolean", PROP),
    ("datatype", "tombstoned", "different", "list>bool"): returned("boolean", PROP),
}
# fmt: on
"""Every typed cell's outcome, read off the real run and checked against ``carry.py``."""

CARRIED_AS: Outcome = ("carried",)
GONE_WITH_INPUT: Outcome = ("gone", ["gone"])
"""A derived column whose input is gone, reported ``gone`` with it (D239)."""
"""A derived column carried as written, nothing reported of it."""
DERIVED = f"/descriptors/{TABLE}.derived/fields/derived"
"""The prefix of a refusal's path at a derived column's operation, at the re-import."""
# fmt: off
DERIVED_EXPECTED: dict[DerivedCell, Outcome] = {
    ("arith", "none"): at_reimport(("INVALID_VALUE", f"{DERIVED}/args/0")),
    ("arith", "cat"): at_reimport(("INVALID_VALUE", f"{DERIVED}/args/0")),
    ("arith", "str"): at_reimport(("INVALID_VALUE", f"{DERIVED}/args/0")),
    ("arith", "list"): at_reimport(("INVALID_VALUE", f"{DERIVED}/args/0")),
    ("arith", "int"): CARRIED_AS,
    ("arith", "num"): CARRIED_AS,
    ("arith", "date"): at_reimport(("INVALID_VALUE", f"{DERIVED}/args/0")),
    ("arith", "datetime"): at_reimport(("INVALID_VALUE", f"{DERIVED}/args/0")),
    ("arith", "bool"): at_reimport(("INVALID_VALUE", f"{DERIVED}/args/0")),
    ("arith", "gone"): GONE_WITH_INPUT,
    ("date_diff", "none"): at_reimport(("INVALID_VALUE", f"{DERIVED}/from")),
    ("date_diff", "cat"): at_reimport(("INVALID_VALUE", f"{DERIVED}/from")),
    ("date_diff", "str"): at_reimport(("INVALID_VALUE", f"{DERIVED}/from")),
    ("date_diff", "list"): at_reimport(("INVALID_VALUE", f"{DERIVED}/from")),
    ("date_diff", "int"): at_reimport(("INVALID_VALUE", f"{DERIVED}/from")),
    ("date_diff", "num"): at_reimport(("INVALID_VALUE", f"{DERIVED}/from")),
    ("date_diff", "date"): CARRIED_AS,
    ("date_diff", "datetime"): CARRIED_AS,
    ("date_diff", "bool"): at_reimport(("INVALID_VALUE", f"{DERIVED}/from")),
    ("date_diff", "gone"): GONE_WITH_INPUT,
    ("unit_convert", "none"): at_reimport(("CONFLICTING_MEMBERS", f"{CARRIED}/fields/units")),
    ("unit_convert", "cat"): at_reimport(("CONFLICTING_MEMBERS", f"{CARRIED}/fields/units")),
    ("unit_convert", "str"): at_reimport(("CONFLICTING_MEMBERS", f"{CARRIED}/fields/units")),
    ("unit_convert", "list"): at_reimport(("CONFLICTING_MEMBERS", f"{CARRIED}/fields/units")),
    ("unit_convert", "int"): CARRIED_AS,
    ("unit_convert", "num"): CARRIED_AS,
    ("unit_convert", "date"): at_reimport(("CONFLICTING_MEMBERS", f"{CARRIED}/fields/units")),
    ("unit_convert", "datetime"): at_reimport(("CONFLICTING_MEMBERS", f"{CARRIED}/fields/units")),
    ("unit_convert", "bool"): at_reimport(("CONFLICTING_MEMBERS", f"{CARRIED}/fields/units")),
    ("unit_convert", "gone"): GONE_WITH_INPUT,
    ("value_map", "none"): CARRIED_AS,
    ("value_map", "cat"): CARRIED_AS,
    ("value_map", "str"): CARRIED_AS,
    ("value_map", "list"): at_reimport(("INVALID_VALUE", f"{DERIVED}/input")),
    ("value_map", "int"): CARRIED_AS,
    ("value_map", "num"): CARRIED_AS,
    ("value_map", "date"): CARRIED_AS,
    ("value_map", "datetime"): CARRIED_AS,
    ("value_map", "bool"): CARRIED_AS,
    ("value_map", "gone"): GONE_WITH_INPUT,
}
# fmt: on
"""Every derived cell's outcome, read off the real run."""

OPERATORS = (
    "units",
    "range",
    "permissible_values",
    "concepts",
    "maps_to",
    "derived",
    "completeness",
    "absent",
)
"""Fields the core's importer never infers (fact ``never``)."""


def _pairs(before: Sequence[str] = KINDS, after: Sequence[str] = KINDS) -> tuple[str, ...]:
    return tuple(f"{b}>{a}" for b in before for a in after)


SAME = ("cat>cat", "str>str", "list>list")
CHANGED = ("cat>str", "cat>list", "str>cat", "str>list", "list>cat", "list>str")
NEW = ("none>cat", "none>str", "none>list")
GONE = ("cat>none", "str>none", "list>none")
NONE = ("none>none",)
TEXTS = ("none", "cat", "list")
"""The kinds other than a string."""
LISTLESS = ("none", "cat", "str")

Part = str | tuple[str, ...]
Pattern = tuple[Part, Part, Part, Part]
UNREACHABLE: list[tuple[Pattern, str, str]] = [
    (
        ("*", "absent", ("equal", "different", "gone"), "*"),
        "a release records a field's inference only in its entry or its tombstone; a field with "
        "neither has none recorded, so its relation is new or none (a proposal the gate drops, "
        "D230, leaves neither and reads as new)",
        "definition",
    ),
    (
        ("*", ("imported", DEFAULT, "proposed"), ("new", "none"), "*"),
        "an importer's entry always holds its inference (D227)",
        "inferred",
    ),
    (
        ("*", ("confirmed", "tombstoned"), ("new", "none"), "*"),
        "confirming a field with no inference asserts the operator's value (the asserted state), "
        "and removing it leaves no tombstone (D239)",
        "edits",
    ),
    (
        (OPERATORS, "*", ("equal", "different", "new", "gone"), "*"),
        "the core's importer never infers the field",
        "never",
    ),
    (
        (("datatype", "identifier", "list_syntax"), ("imported", DEFAULT), "*", "*"),
        "on a column of text the core's importer proposes a datatype, an identifier and a "
        "list_syntax (D227); a datatype at another status (a datetime read without offsets, a "
        "type Parquet declares) is typed, never one of the pair's kinds",
        "statuses",
    ),
    (
        ("missing_codes", ("imported", "proposed"), "*", "*"),
        "conventional missing codes are imported_default (D227)",
        "statuses",
    ),
    (
        ("source", (DEFAULT, "proposed"), "*", "*"),
        "a column's source is imported (D227)",
        "statuses",
    ),
    (
        ("datatype", "*", "equal", (*CHANGED, *NEW, *GONE, *NONE)),
        "the pair is the datatype inferred before and after, so equal is the same kind",
        "definition",
    ),
    (
        ("datatype", "*", "different", (*SAME, *NEW, *GONE, *NONE)),
        "the pair is the datatype inferred before and after, so different is two kinds",
        "definition",
    ),
    (
        ("datatype", "*", "new", (*SAME, *CHANGED, *GONE, *NONE)),
        "the pair is the datatype inferred before and after, so new is from none",
        "definition",
    ),
    (
        ("datatype", "*", "gone", (*SAME, *CHANGED, *NEW, *NONE)),
        "the pair is the datatype inferred before and after, so gone is to none",
        "definition",
    ),
    (
        ("datatype", "*", "none", (*SAME, *CHANGED, *NEW, *GONE)),
        "the pair is the datatype inferred before and after, so none is none to none",
        "definition",
    ),
    (
        ("source", "*", ("new", "gone", "none"), "*"),
        "every source column has a source, its header (D227)",
        "source",
    ),
    (
        ("identifier", "*", "different", "*"),
        "the importer infers identifier only as true",
        "identifier",
    ),
    (
        ("identifier", "*", "equal", (*_pairs(TEXTS), *_pairs(("str",), TEXTS))),
        "an identifier is inferred only on a string (D228), so equal is string to string",
        "identifier",
    ),
    (
        ("identifier", "*", "new", _pairs(KINDS, TEXTS)),
        "an identifier is inferred only on a string (D228), so new is to a string",
        "identifier",
    ),
    (
        ("identifier", "*", "gone", _pairs(TEXTS, KINDS)),
        "an identifier is inferred only on a string (D228), so gone is from a string",
        "identifier",
    ),
    (
        (
            "list_syntax",
            "*",
            ("equal", "different"),
            (*_pairs(LISTLESS), *_pairs(("list",), LISTLESS)),
        ),
        "list_syntax is inferred exactly with list<category> (D228), so equal and different are "
        "list to list",
        "list",
    ),
    (
        ("list_syntax", "*", "new", (*_pairs(KINDS, LISTLESS), "list>list")),
        "list_syntax is inferred exactly with list<category> (D228), so new is to a list from "
        "another kind",
        "list",
    ),
    (
        ("list_syntax", "*", "gone", (*_pairs(LISTLESS, KINDS), "list>list")),
        "list_syntax is inferred exactly with list<category> (D228), so gone is from a list to "
        "another kind",
        "list",
    ),
    (
        ("list_syntax", "*", "none", (*_pairs(("list",), KINDS), *_pairs(LISTLESS, ("list",)))),
        "list_syntax is inferred exactly with list<category> (D228), so none has no list",
        "list",
    ),
]
"""Cells no data reaches, each by the first pattern that matches it (``*`` any value, a tuple any
of its values), with the reason and the fact it rests on: one the brute force checks
(``inferred``, ``never``, ``statuses``, ``source``, ``identifier``, ``list``), the session test
checks (``edits``), or the cell's own definition, which every run checks (``definition``)."""


def _matches(pattern: Pattern, cell: Cell) -> bool:
    return all(
        part == "*" or value == part or (isinstance(part, tuple) and value in part)
        for part, value in zip(pattern, cell, strict=True)
    )


def unreachable(cell: Cell) -> int | None:
    """The index in ``UNREACHABLE`` of the first pattern a cell matches, if any."""
    for index, (pattern, _, _) in enumerate(UNREACHABLE):
        if _matches(pattern, cell):
            return index
    return None


# --- Tests ----------------------------------------------------------------------------------------


@pytest.fixture
def orchard(tmp_path: Path) -> Iterator[Orchard]:
    opened = Orchard(tmp_path)
    yield opened
    opened.store.close()


def test_the_generator_s_fields_are_the_model_s() -> None:
    """A field added to ``ColumnFields`` fails here until the table states its outcomes."""
    assert tuple(ColumnFields.model_fields) == FIELDS


def test_the_table_covers_the_generator_exactly() -> None:
    """Every cell of the generator is either a literal of ``EXPECTED`` or matched by a pattern of
    ``UNREACHABLE``, never both; every pattern is the first match of some cell."""
    generated = cells()
    assert len(set(generated)) == len(generated) == 13 * 7 * 5 * 16
    assert sorted(set(EXPECTED) - set(generated)) == []
    assert [cell for cell in EXPECTED if unreachable(cell) is not None] == []
    rest = [cell for cell in generated if cell not in EXPECTED]
    assert [cell for cell in rest if unreachable(cell) is None] == []
    assert {unreachable(cell) for cell in rest} == set(range(len(UNREACHABLE)))
    assert (len(EXPECTED), len(rest)) == (800, 6480)


def test_the_typed_table_covers_the_typed_generator_exactly() -> None:
    assert sorted(TYPED_EXPECTED) == sorted(typed_cells())
    assert len(TYPED_EXPECTED) == 120


def test_the_derived_generator_s_operations_are_the_model_s() -> None:
    """An operation added to the model fails here until the derived table states its outcomes."""
    assert set(MODEL_OPS) == set(DERIVE_OPS)
    assert sorted(DERIVED_EXPECTED) == sorted(derived_cells())
    assert len(DERIVED_EXPECTED) == len(DERIVE_OPS) * len(INPUT_KINDS) == 40


def reads(op: str, kind: str) -> bool:
    """``derive.problems``' rule: whether an operation reads an input of ``kind``. None reads a
    list; ``arith`` and ``unit_convert`` read numbers (integer, number, time_offset); ``date_diff``
    reads dates and datetimes; ``value_map`` reads anything else, no datatype included."""
    if kind in ("list", "gone"):
        return False
    if op in ("arith", "unit_convert"):
        return kind in ("int", "num")
    if op == "date_diff":
        return kind in ("date", "datetime")
    return True


@pytest.mark.parametrize("cell", sorted(DERIVED_EXPECTED), ids="/".join)
def test_a_derived_column_s_outcome_is_the_table_s(
    cell: DerivedCell, derived_seen: dict[DerivedCell, Outcome]
) -> None:
    """Each operation, its input re-imported as each of D228's kinds: carried as written when the
    operation reads the input's new datatype; else refused, at the operation's input
    (``INVALID_VALUE``), or, for a ``unit_convert``, first at the input's ``units`` (D241(c))."""
    op, kind = cell
    expected = DERIVED_EXPECTED[cell]
    if kind == "gone":
        assert expected == GONE_WITH_INPUT
    else:
        assert (expected == CARRIED_AS) == reads(op, kind)
    if expected not in (CARRIED_AS, GONE_WITH_INPUT):
        [(code, path)] = expected[2]
        if op == "unit_convert":
            assert (code, path) == ("CONFLICTING_MEMBERS", f"{CARRIED}/fields/units")
        else:
            assert code == "INVALID_VALUE"
            assert path.startswith(f"{DERIVED}/")
    assert derived_seen[cell] == expected


@pytest.mark.parametrize("cell", sorted({**EXPECTED, **TYPED_EXPECTED}), ids="/".join)
def test_a_cell_s_outcome_is_the_table_s(cell: Cell, seen: dict[Cell, Seen]) -> None:
    expected = {**EXPECTED, **TYPED_EXPECTED}[cell]
    found = seen[cell]
    assert (found.relation, found.pair) == cell[2:], "the imports are not the cell's"
    if expected[:2] != ("refused", "curation"):
        assert found.state == cell[1], "the session did not make the cell's state"
    assert found.outcome == expected


ALPHABET = ("x", "y", "a", "b", "NA", "a;b", "b;a", "a|b", "a;b;c", "")
"""Values, a conventional missing code, delimited cells that make lists with ``a`` and ``b`` (and
strings without them) and an empty cell."""


def long_columns() -> list[tuple[str, ...]]:
    """Columns of 20 to 102 cells around D228's thresholds: ``k`` distinct values (1, 2, 10, 19,
    20, 21, 50, 51) repeated to ``n`` cells (20, 21, 40, 102), each also with its last cell a
    missing code; integers, numbers and dates of 19, 20, 21, 50 and 51 distinct cells, each also
    with a missing code, and 20 booleans; lists of 21 and 42 cells over 7 items, one with a
    single-item cell; and the typed and table shapes."""
    found: list[tuple[str, ...]] = []
    for k, n in product((1, 2, 10, 19, 20, 21, 50, 51), (20, 21, 40, 102)):
        if n >= k:
            column = tuple(f"v{index % k:02d}" for index in range(n))
            found += [column, (*column[:-1], "NA")]
    for k in (19, 20, 21, 50, 51):
        for column in (
            tuple(str(index) for index in range(k)),
            tuple(f"{index}.5" for index in range(k)),
            tuple(f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(k)),
        ):
            found += [column, (*column, "NA")]
    found += [("yes", "no") * 10]
    pairs = tuple(f"{a};{b}" for a, b in itertools.combinations("abcdefg", 2))
    found += [pairs, pairs * 2, (*pairs[:20], "a")]
    found += [shape for pair in SHAPES.values() for shape in pair]
    found += [IDENTIFYING, (*IDENTIFYING[:19], "NA"), IDENTIFYING[:19], LISTS_AGAIN, PIPES]
    found += [("2026-01-01T10:00:00", "2026-01-02T11:00:00", "2026-01-03T12:00:00")]
    return found


TEXT_STATUSES = {
    "datatype": "proposed",
    "missing_codes": DEFAULT,
    "identifier": "proposed",
    "list_syntax": "proposed",
    "source": "imported",
}
"""What the core's importer infers on a column of text, at the status it gives (D227)."""


def test_the_importer_s_facts_over_every_small_column(orchard: Orchard) -> None:
    """Every column of 6 cells over ``ALPHABET`` (every multiset: D228 and D227 count values and
    never read their order), and ``long_columns``, through the real importer: the facts
    ``UNREACHABLE``'s reasons rest on. Each fact is checked over columns where it could fail
    (``exercised``): a category or a list of 20 or more present cells (identifier), a string with
    a delimiter and a list with a single-item cell (list), a column of no present cell and one of
    one (none), a typed datatype at a status other than proposed (statuses)."""
    shapes = [*itertools.combinations_with_replacement(ALPHABET, 6), *long_columns()]
    columns = {f"c{index:04d}": shape for index, shape in enumerate(shapes)}
    found: dict[str, dict[str, Any]] = {}
    names = sorted(columns)
    for start in range(0, len(names), 2_000):
        chunk = {name: columns[name] for name in names[start : start + 2_000]}
        published = orchard.import_(f"brute{start}", chunk, "first")
        found |= orchard.columns(published.manifest)
    kinds: set[str] = set()
    facts = dict.fromkeys(
        ("inferred", "never", "statuses", "source", "identifier", "list", "none"), True
    )
    exercised: dict[str, int] = dict.fromkeys(
        (
            "identifier: a category of 20 present cells",
            "identifier: a category of 20 distinct values",
            "identifier: a typed column of 20 distinct present cells",
            "identifier: a string of 19 distinct present cells",
            "category: a category of 50 distinct values",
            "category: a string of 51 distinct values among 102 present cells",
            "missing: a category of 20 or more cells, one a missing code",
            "identifier: a list of 20 distinct present cells",
            "identifier: a string of 20 distinct present cells",
            "list: a string with a delimiter in 2 cells",
            "list: a list with a single-item cell",
            "none: no present cell",
            "none: one present cell",
            "statuses: a typed datatype not proposed",
            *(f"statuses: {field}" for field in TEXT_STATUSES),
        ),
        0,
    )
    for name, cells_of in columns.items():
        column = found[f"{TABLE}.{name}"]
        fields, curation = column["fields"], column["curation"]
        entries = {p.removeprefix("/fields/"): e for p, e in curation.items() if p != "/label"}
        datatype = fields.get("datatype")
        kind = DATATYPES.get(datatype, str(datatype))
        kinds.add(kind)
        codes = set(fields.get("missing_codes", {}))
        present = [cell for cell in cells_of if cell and cell not in codes]
        delimited = [cell for cell in present if ";" in cell or "|" in cell]
        text = kind in KINDS
        facts["inferred"] &= all("inferred" in entry for entry in entries.values())
        facts["never"] &= not set(entries) & set(OPERATORS)
        facts["statuses"] &= all(
            TEXT_STATUSES.get(f) == e["status"]
            for f, e in entries.items()
            if text or f != "datatype"
        )
        facts["statuses"] &= text or entries["datatype"]["status"] in (
            "proposed",
            DEFAULT,
            IMPORTED,
        )
        facts["source"] &= fields.get("source") == {"original_name": name}
        facts["identifier"] &= fields.get("identifier") in (None, True)
        facts["identifier"] &= "identifier" not in fields or datatype == "string"
        facts["list"] &= ("list_syntax" in fields) == (datatype == "list<category>")
        facts["none"] &= (datatype is None) == (not present)
        many = len(present) >= 20
        exercised["identifier: a category of 20 present cells"] += many and kind == "cat"
        distinct = len(set(present))
        exercised["identifier: a category of 20 distinct values"] += (
            kind == "cat" and distinct >= 20
        )
        exercised["identifier: a typed column of 20 distinct present cells"] += (
            not text and kind != "none" and many and distinct == len(present)
        )
        exercised["identifier: a string of 19 distinct present cells"] += (
            kind == "str" and distinct == len(present) == 19
        )
        exercised["category: a category of 50 distinct values"] += kind == "cat" and distinct == 50
        exercised["category: a string of 51 distinct values among 102 present cells"] += (
            kind == "str" and distinct == 51 and len(present) >= 102
        )
        exercised["missing: a category of 20 or more cells, one a missing code"] += (
            kind == "cat" and len(cells_of) >= 20 and "NA" in cells_of
        )
        exercised["identifier: a list of 20 distinct present cells"] += (
            many and kind == "list" and len(set(present)) == len(present)
        )
        exercised["identifier: a string of 20 distinct present cells"] += (
            many and kind == "str" and len(set(present)) == len(present)
        )
        exercised["list: a string with a delimiter in 2 cells"] += (
            kind == "str" and len(delimited) >= 2
        )
        exercised["list: a list with a single-item cell"] += kind == "list" and len(
            delimited
        ) < len(present)
        exercised["none: no present cell"] += not present
        exercised["none: one present cell"] += len(present) == 1
        exercised["statuses: a typed datatype not proposed"] += (
            not text and entries["datatype"]["status"] != "proposed"
        )
        for field in entries:
            if f"statuses: {field}" in exercised:
                exercised[f"statuses: {field}"] += 1
    assert facts == dict.fromkeys(facts, True)
    assert [what for what, count in exercised.items() if not count] == []
    # Every kind of the pairs occurs, and typed ones: boolean (D228 reads "y" as a word), the
    # instances of TYPED and a datetime, which the generator leaves out.
    assert kinds == {*KINDS, *TYPED, "datetime"}
    assert {fact for _, _, fact in UNREACHABLE} <= {*facts, "edits", "definition"}


def test_a_field_without_an_inference_is_absent_or_asserted(orchard: Orchard) -> None:
    """The fact ``edits``: a field the import did not infer is asserted when set or confirmed,
    with no inference in its entry, and leaves no tombstone when removed."""
    orchard.import_("d", {ALONE: CATEGORY}, "first")
    orchard.curate("d", [_set(COLUMN, "completeness", "complete")])
    orchard.curate(
        "d", [{"op": "confirm", "descriptor": COLUMN, "pointers": ["/fields/completeness"]}]
    )
    latest = orchard.store.latest("d")
    assert latest is not None
    column = orchard.columns(latest.manifest)[COLUMN]
    assert state_of(column, "completeness", False) == "asserted"
    assert "inferred" not in column["curation"]["/fields/completeness"]
    orchard.curate("d", [_remove(COLUMN, "completeness")])
    latest = orchard.store.latest("d")
    assert latest is not None
    assert (COLUMN, "/fields/completeness") not in orchard.tombstoned(latest.manifest)


def test_removing_an_assertion_over_an_inference_leaves_a_tombstone(orchard: Orchard) -> None:
    """A field an operator asserted over the importer's inference keeps the inference in its
    entry, so removing it leaves a tombstone of it, as removing the importer's value does: the
    ``tombstoned`` state does not depend on the entry's status."""
    orchard.import_("d", {ALONE: CATEGORY}, "first")
    orchard.curate("d", [_set(COLUMN, "datatype", "string")])
    orchard.curate("d", [_remove(COLUMN, "datatype")])
    latest = orchard.store.latest("d")
    assert latest is not None
    [tombstone] = [t for t in orchard.store.tombstones(latest.manifest) if t.descriptor == COLUMN]
    assert (tombstone.pointer, tombstone.inferred) == ("/fields/datatype", "category")


@pytest.mark.parametrize(
    ("kind", "edits", "refusal"),
    [
        ("str", [_set(COLUMN, "units", "1")], ("CONFLICTING_MEMBERS", f"{DRAFT}/fields/units")),
        ("str", [_set(COLUMN, "range", RANGE)], ("CONFLICTING_MEMBERS", f"{DRAFT}/fields/range")),
        (
            "str",
            [_set(COLUMN, "list_syntax", SYNTAX)],
            ("CONFLICTING_MEMBERS", f"{DRAFT}/fields/list_syntax"),
        ),
        (
            "str",
            [_set(COLUMN, "permissible_values", LISTED)],
            ("CONFLICTING_MEMBERS", f"{DRAFT}/fields/permissible_values"),
        ),
        (
            "list",
            [_set(COLUMN, "datatype", "string")],
            ("CONFLICTING_MEMBERS", f"{DRAFT}/fields/list_syntax"),
        ),
        (
            "list",
            [_remove(COLUMN, "datatype")],
            ("CONFLICTING_MEMBERS", f"{DRAFT}/fields/list_syntax"),
        ),
        (
            "list",
            [_remove(COLUMN, "list_syntax")],
            ("MISSING_MEMBER", f"{DRAFT}/fields/list_syntax"),
        ),
    ],
    ids=[
        "units",
        "range",
        "list_syntax",
        "permissible_values",
        "list-datatype",
        "list-remove",
        "list-syntax-remove",
    ],
)
def test_a_companion_is_the_least_edit_that_lets_a_field_be_curated(
    kind: str, edits: list[dict[str, JsonValue]], refusal: tuple[str, str], orchard: Orchard
) -> None:
    """Without its companion, each edit the cells pair with another is refused at the session:
    the model's rules that a datatype admits a field and that a list has its syntax."""
    orchard.import_("d", {ALONE: SHAPES[kind][0]}, "first")
    with pytest.raises(EditRefused) as error:
        orchard.curate("d", edits)
    assert _refusals(error.value) == (refusal,)


def test_a_source_column_is_never_removed_by_a_session(orchard: Orchard) -> None:
    """No descriptor-level removal applies to a source column: only a re-import removes one."""
    orchard.import_("d", {ALONE: CATEGORY}, "first")
    with pytest.raises(EditRefused) as error:
        orchard.curate("d", [{"op": "remove_descriptor", "descriptor": COLUMN}])
    assert _refusals(error.value) == (("COLUMNS_CHANGED", "/edits/0/descriptor"),)


def test_permissible_values_the_new_cells_outgrow_are_carried(orchard: Orchard) -> None:
    """An operator's permissible values that a re-import's cells outgrow are carried with nothing
    reported, and the gate does not refuse (§13.2 checks them only in a column with absent
    values): the new value is one no document's constant can name (§6.4)."""
    orchard.import_("d", {ALONE: CATEGORY}, "first")
    listed: JsonValue = {"values": [{"value": "green"}, {"value": "red"}]}
    orchard.curate("d", [_set(COLUMN, "permissible_values", listed)])
    second = orchard.reimport("d", {ALONE: ("red", "violet", "violet", "red")})
    column = orchard.columns(second.manifest)[COLUMN]
    assert [c for c in second.changes if c.descriptor == COLUMN] == []
    assert column["fields"]["permissible_values"] == listed
    assert column["curation"]["/fields/permissible_values"]["status"] == "asserted"


def test_permissible_values_naming_a_code_the_cells_bring_refuse_the_re_import(
    orchard: Orchard,
) -> None:
    """An operator's permissible values naming a value the re-import's cells make a missing code
    refuse the re-import: a permissible value is not also a missing code (the model's rule), the
    path naming the value. So the cells (permissible_values, asserted, none, cat>cat) and
    (missing_codes, absent, new, *) hold as the table says only while no curated value names the
    new code."""
    orchard.import_("d", {ALONE: CATEGORY}, "first")
    listed: JsonValue = {"values": [{"value": "red"}, {"value": "green"}, {"value": "NA"}]}
    orchard.curate("d", [_set(COLUMN, "permissible_values", listed)])
    with pytest.raises(ImportRefused) as error:
        orchard.reimport("d", {ALONE: (*CATEGORY_AGAIN, "NA")})
    path = f"{CARRIED}/fields/permissible_values/values/2/value"
    assert _refusals(error.value) == (("CONFLICTING_MEMBERS", path),)


def test_maps_to_refuses_the_re_import_once_its_pack_is_gone(orchard: Orchard) -> None:
    """A ``maps_to`` curated while its pack was installed refuses a re-import run with the pack
    gone: its concept is registered by no installed pack (D405)."""
    orchard.import_("d", {ALONE: CATEGORY}, "first")
    orchard.curate("d", [_set(COLUMN, "maps_to", MAPPED)])
    orchard.registry = PackRegistry([], core_version=aibi.__version__)
    with pytest.raises(ImportRefused) as error:
        orchard.reimport("d", {ALONE: CATEGORY_AGAIN})
    path = f"{CARRIED}/fields/maps_to/concept"
    assert _refusals(error.value) == (("UNKNOWN_DESCRIPTOR", path),)


def test_a_nested_operand_is_refused_where_it_sits(orchard: Orchard) -> None:
    """An ``arith`` nested in another refuses the re-import at the nested input's place, when the
    re-import infers that input a datatype it does not read (D241(g))."""
    integers = ("1", "2", "3", "4")
    orchard.import_("d", {"a": integers, "b": integers}, "first")
    nested: JsonValue = {"op": "arith", "operator": "*", "args": ["b", 2]}
    operation: JsonValue = {"op": "arith", "operator": "+", "args": ["a", nested]}
    descriptor: JsonValue = {
        "kind": "column",
        "id": f"{TABLE}.derived",
        "label": "derived",
        "fields": {"derived": operation},
    }
    orchard.curate("d", [{"op": "put", "descriptor": descriptor}])
    with pytest.raises(ImportRefused) as error:
        orchard.reimport("d", {"a": tuple(reversed(integers)), "b": STRING})
    assert _refusals(error.value) == (("INVALID_VALUE", f"{DERIVED}/args/1/args/0"),)


def test_a_value_map_keyed_on_a_value_that_becomes_missing_is_a_dead_key(orchard: Orchard) -> None:
    """An operator removes the column's missing codes, so that ``NA`` is a value, and keys a
    derived column's value map and the column's ``maps_to`` value map on it; a re-import whose
    inference of the codes differs returns them, and both maps are carried with nothing reported
    of them, their ``NA`` key now meeting only missing cells (D219): dead keys, which the operator
    re-keys."""
    orchard.import_("d", {ALONE: (*CATEGORY, "NA")}, "first")
    derived = {
        "kind": "column",
        "id": f"{TABLE}.flag",
        "label": "flag",
        "fields": {
            "datatype": "string",
            "derived": {"op": "value_map", "input": ALONE, "map": {"NA": "unrecorded"}},
        },
    }
    mapped: JsonValue = {
        "concept": "orchard:colour",
        "transform": {"value_map": {"NA": "unrecorded", "red": "red"}},
    }
    orchard.curate(
        "d",
        [
            _remove(COLUMN, "missing_codes"),
            _set(COLUMN, "maps_to", mapped),
            {"op": "put", "descriptor": derived},
        ],
    )
    latest = orchard.store.latest("d")
    assert latest is not None
    before = orchard.columns(latest.manifest)
    second = orchard.reimport("d", {ALONE: (*CATEGORY_AGAIN, "NA", "N/A")})
    after = orchard.columns(second.manifest)
    assert [(c.descriptor, c.pointer, c.happened) for c in second.changes] == [
        (COLUMN, "/fields/missing_codes", "returned")
    ]
    assert after[COLUMN]["fields"]["missing_codes"] == BOTH
    assert after[COLUMN]["fields"]["maps_to"] == before[COLUMN]["fields"]["maps_to"] == mapped
    assert after[f"{TABLE}.flag"] == before[f"{TABLE}.flag"]


def _spec_row(decision: str) -> str:
    spec = Path(__file__).resolve().parents[4] / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    [row] = [line for line in text.splitlines() if line.startswith(f"| {decision} |")]
    return row


def _d241() -> dict[str, str]:
    """D241's statements of the carry's outcomes, by their letter, and what precedes them."""
    row = _spec_row("D241")
    head, *parts = row.split("**(")
    return {"intro": head, **{part[0]: part for part in parts}}


STATEMENTS: Mapping[str, tuple[str, str]] = {
    "nothing": (
        "intro",
        "carried (nothing reported: the base's value and entry, or the field absent)",
    ),
    "attribution": (
        "intro",
        "A failing check is laid at the field its refusal's path names (`settle`): when the "
        "datatype a column is left with no longer admits a carried field, the carried field fails, "
        "not the importer's datatype, so D239's return of a failing new proposal to the base's "
        "value never applies to a datatype.",
    ),
    "statuses": (
        "intro",
        "On a column of text the core's importer infers its `datatype`, `identifier` and "
        "`list_syntax` (`proposed`), `missing_codes` (`imported_default`) and `source` "
        "(`imported`), and no other column field; a typed datatype may have another status (a "
        "datetime read without offsets is `imported_default`, a type Parquet or a database "
        "snapshot declares `imported`, D227, D307)",
    ),
    "importer": (
        "a",
        "An importer's value** is carried while its inference holds, takes the new proposal when "
        "it differs (`proposed`) and goes when the inference goes (`removed`: a datatype when the "
        "column has no PRESENT cell left); a field with neither a value nor an inference takes the "
        "importer's new one (`proposed`). The operator need do nothing.",
    ),
    "operator, carried": (
        "b",
        "An operator's value over an inference** (asserted, or confirmed, either keeping the "
        "inference in its entry, D245) is carried while the inference holds.",
    ),
    "operator, proposed": (
        "b",
        "When the inference differs, the field takes the new proposal and its status (`proposed`), "
        "whether that is another value (replaced: a confirmed `category` becomes `string`, or "
        "`integer` when the cells are whole numbers) or the same one (demoted: an asserted "
        "`string` on a column inferred a category stays `string`, at `proposed`, when the column "
        "is inferred a string); an asserted `missing_codes`, `source` or `list_syntax` takes the "
        "importer's likewise.",
    ),
    "operator, removed": (
        "b",
        "When the inference goes, the field is removed (`removed`: an asserted datatype when the "
        "column has no PRESENT cell left, an asserted `identifier: false`, `missing_codes` of "
        "another reason). Remedy: assert the value again in a session after the re-import.",
    ),
    "uninferred, carried": (
        "c",
        "An operator's value with no inference before** (every field of a source column that the "
        "core's importer never infers, and a `datatype`, `missing_codes`, `identifier` or "
        "`list_syntax` the import did not infer; a derived column's fields are (g)'s) is carried "
        "as written, a new inference not written into it, unless the datatype the column is left "
        "with no longer admits it.",
    ),
    "uninferred, refused": (
        "c",
        "Then it refuses the re-import with the model's code and path: `CONFLICTING_MEMBERS` at "
        "`/descriptors/<column>/fields/<field>`, or `WRONG_TYPE` at each bound of a `range` that "
        "the new datatype types otherwise (`/fields/range/min` and `/fields/range/max`: a range "
        "of integers over a column re-imported as dates); its companion datatype, the importer's "
        "or an operator's over an inference, was replaced or removed by (a) or (b), and the "
        "field, asserted, is never dropped.",
    ),
    "units, range": (
        "c",
        "So `units` (with `datatype: number`) and `range` (with `datatype: integer`) refuse it "
        "whenever the column's datatype inference differs or goes, unless the cells become "
        "numbers (`integer` or `number`, which admit both).",
    ),
    "list_syntax": (
        "c",
        "`list_syntax` (with `datatype: list<category>`) refuses it when a column inferred a "
        "category or a string is inferred another kind but a list.",
    ),
    "permissible_values": (
        "c",
        "`permissible_values` refuses it when its datatype gives way to one that admits none: a "
        "category inferred a string, none or a typed datatype; a list inferred a string, none or a "
        "typed datatype; an asserted `category` over a string that the column then has no "
        "PRESENT cell for, or that it is inferred a typed datatype.",
    ),
    "admitted": (
        "c",
        "Over a datatype an operator asserted with no inference, or one the new inference still "
        "admits, each is carried.",
    ),
    "new code": (
        "c",
        "A permissible value that the re-import's cells make a missing code refuses it too "
        "(`CONFLICTING_MEMBERS` at "
        "`/descriptors/<column>/fields/permissible_values/values/<i>/value`: a permissible value "
        "is not also a missing code).",
    ),
    "outgrown": (
        "c",
        "Permissible values the new cells outgrow are carried with nothing reported, and the gate "
        "does not refuse them (§13.2 checks permissible values only in a column with absent "
        "values): the new value is one no document's constant can name (§6.4).",
    ),
    "uninferred, remedy": (
        "c",
        "Remedy: drop the curated value and its companion in a session, re-import, then assert "
        "them again where they still apply; drop a permissible value the cells now make missing; "
        "widen permissible values the cells outgrow.",
    ),
    "tombstone": (
        "d",
        "A tombstone** stands while the inference holds (a removed `datatype` publishes the "
        "column with none), returns the field when the inference differs (`returned`), and is "
        "consumed with nothing reported when it goes (D240); a list's `list_syntax` that returns "
        "while the list's removed datatype stays removed fails against it and is dropped "
        "(`dropped`), its tombstone consumed. Remedy: remove the field again.",
    ),
    "absent": (
        "e",
        "`absent` (`INVALID_VALUE`: D401's rule, a category declaring permissible values in a "
        "table of source kind `pack`, which only a pack's importer lays out)",
    ),
    "derived": (
        "e",
        "a source column's own `derived` (`INVALID_VALUE`: a source column is not derived; on a "
        "list, its datatype too)",
    ),
    "companions": (
        "e",
        "without their companion, `units`, `range`, `list_syntax` and, on a string, "
        "`permissible_values` (`CONFLICTING_MEMBERS`: the datatype does not admit them); a "
        "datatype other than `list<category>` set on a list, or a list's datatype removed, with "
        "its `list_syntax` kept (`CONFLICTING_MEMBERS`); a list's `list_syntax` removed with its "
        "datatype kept (`MISSING_MEMBER`)",
    ),
    "maps_to": (
        "e",
        "`maps_to` naming a concept no installed pack registers (`UNKNOWN_DESCRIPTOR`, D405), "
        "which with such a pack is carried as (c) says.",
    ),
    "source column": (
        "e",
        "No descriptor-level removal applies to a source column (`remove_descriptor` is "
        "`COLUMNS_CHANGED`: only a re-import removes one). Remedy: make the edit with its "
        "companion, or not at all.",
    ),
    "maps_to at a re-import": (
        "e",
        "A `maps_to` curated while its pack was installed refuses a re-import run without that "
        "pack (`UNKNOWN_DESCRIPTOR` at `/descriptors/<column>/fields/maps_to/concept`, D405). "
        "Remedy: install the pack again, or remove the mapping in a session.",
    ),
    "dead key": (
        "f",
        "Keys the cells no longer hold:** a derived column's `value_map`, or a `maps_to` value "
        "map, keyed on a value the re-imported cells no longer hold is carried with no change "
        "reported of it: a dead key. One keyed on a value that a re-import makes missing (an "
        "operator removed the column's `missing_codes`, so that `NA` was a value, and the "
        "importer's inference of them changed, returning them) reports only the input's "
        "`missing_codes` `returned`; the key then meets only missing cells, whose derived value "
        "is UNKNOWN (D219), never a closed answer. Remedy: re-key it.",
    ),
    "derived column": (
        "g",
        "A derived column** (an operator's `put`, its fields holding no inference) is carried as "
        "written while each input is still a column of the table and its datatype is one its "
        "operation reads (`derive.problems`)",
    ),
    "derived column, gone": (
        "g",
        "one whose input is gone goes with it (`gone`, D239), nothing refused",
    ),
    "derived column, refused": (
        "g",
        ": no operation reads a list; `arith` and `unit_convert` read `integer`, `number` and "
        "`time_offset`; `date_diff` reads `date` and `datetime`; `value_map` reads any other, no "
        "datatype included. Otherwise it refuses the re-import, `INVALID_VALUE` at "
        "`/descriptors/<derived column>/fields/derived/<the input's place>` (`args/<i>`, nested "
        "as `args/<i>/args/<j>`, `input`, `from`, `to`); a `unit_convert`'s input, whose `units` "
        "it needs, refuses first by (c) (`CONFLICTING_MEMBERS` at "
        "`/descriptors/<input>/fields/units`). Remedy: re-point the derived column at an input its "
        "operation reads, or remove it, in a session, then re-import.",
    ),
    "session": (
        "e",
        "Refused at the session**, so never at a re-import run under the same packs:",
    ),
    "scope": (
        "intro",
        "`tests/core/importers/test_carry_outcomes.py` holds every outcome as a literal, run "
        "through a real session and a real re-import of a plain table: for each field of the "
        "column model by the state of its entry (absent, `imported`, `imported_default`, "
        "`proposed`, asserted, confirmed, tombstoned), by the relation of its previous inference "
        "to the new one (equal, different, new, gone, neither) and by the pair of datatypes D228 "
        "infers for a column of text before and after (none, `category`, `string`, "
        "`list<category>`); for a column of each of those four kinds re-imported into a typed "
        "datatype (`integer`, `number`, `date`, `boolean`), for the datatype and the fields a "
        "datatype admits or not; and for a derived column of each operation, its input "
        "re-imported into each of D228's kinds (those four, `integer`, `number`, `date`, "
        "`datetime`, `boolean`) or no longer a column of the table.",
    ),
    "never closed": (
        "g",
        "Every outcome is a refusal, a reported change or an unchanged field, never a silently "
        "closed answer",
    ),
}
"""Each statement of D241 the table's outcomes rest on, exactly, in its part."""

D239_ATTRIBUTION = (
    "A failure is laid at the field its refusal's path names, so a carried field that a new "
    "proposal no longer admits is the failing one, never the new proposal (D241)."
)


def statement_of(cell: Cell, outcome: Outcome) -> str:
    """The statement of D241 that gives a cell's outcome."""
    field, state, relation, _ = cell
    if outcome[:2] == ("refused", "reimport"):
        return {"units": "units, range", "range": "units, range"}.get(field, field)
    if outcome[:2] == ("refused", "curation"):
        return field
    if state == "tombstoned":
        return "tombstone"
    if state in ("imported", DEFAULT, "proposed") or (state == "absent" and relation == "new"):
        return "importer"
    if state in ("asserted", "confirmed") and relation in ("equal", "different", "gone"):
        return {"carried": "operator, carried", "removed": "operator, removed"}.get(
            outcome[0], "operator, proposed"
        )
    if state == "asserted":
        return "admitted" if outcome[0] == "carried" else "uninferred, carried"
    assert (state, outcome) == ("absent", ABSENT), (cell, outcome)
    return "nothing"


def test_d241_states_each_outcome_exactly() -> None:
    """Every statement of D241 the outcomes rest on is in its part word for word, with its
    remedy; every outcome of both tables has its statement; and D239 states the attribution the
    statements rest on."""
    parts = _d241()
    assert sorted(parts) == [*"abcdefg", "intro"]
    assert "`tests/core/importers/test_carry_outcomes.py`" in parts["intro"]
    for key, (part, fragment) in STATEMENTS.items():
        assert fragment in parts[part], key
    for cell, outcome in {**EXPECTED, **TYPED_EXPECTED}.items():
        assert statement_of(cell, outcome) in STATEMENTS, (cell, outcome)
    derived = {cell: statement_of_derived(cell, o) for cell, o in DERIVED_EXPECTED.items()}
    assert set(derived.values()) == {
        "derived column",
        "derived column, gone",
        "derived column, refused",
        "dead key",
    }
    assert {c for c, key in derived.items() if key == "dead key"} == {
        ("value_map", kind) for kind in INPUT_KINDS if kind not in ("cat", "list", "gone")
    }
    assert {c for c, key in derived.items() if key == "derived column, gone"} == {
        (op, "gone") for op in DERIVE_OPS
    }
    assert D239_ATTRIBUTION in _spec_row("D239")


def statement_of_derived(cell: DerivedCell, outcome: Outcome) -> str:
    """The statement of D241 that gives a derived cell's outcome: (f) for a ``value_map`` carried
    over cells that no longer hold its keys; (g)'s gone clause for an input gone; (g)'s rule for
    a refusal; (g)'s carried clause otherwise."""
    op, kind = cell
    if outcome == GONE_WITH_INPUT:
        return "derived column, gone"
    if outcome[0] == "refused":
        return "derived column, refused"
    if op == "value_map" and kind != INPUT_BEFORE[op]:
        return "dead key"
    return "derived column"
