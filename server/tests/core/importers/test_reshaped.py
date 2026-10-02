"""Tables a pack's importer unpivoted (SPEC §10.1, D386), with the test-only ``grid`` pack: the
core writes their absent values, counts what was dropped, checks the coverage against what the
pack declared, freezes what that check read, and answers questions about dropped cells as what
they are, in the reference evaluator and in SQL alike."""

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, ClassVar, cast

import pytest
from pydantic import TypeAdapter

from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.queries import run_cohorts
from aibi.core.engine.resolve import resolve
from aibi.core.engine.worker import Workers
from aibi.core.importers.errors import ImportRefused
from aibi.core.schema.curation import ChangeRequest
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.limits import ImportLimits, QueryLimits
from aibi.core.schema.loading import load_document
from aibi.core.schema.pack_api import ImportResult, cell_digest
from aibi.core.store.build import Layout
from aibi.core.store.edits import EditRefused
from aibi.core.store.sessions import change, open_session, publish
from aibi.core.store.sources import SourceValue, TextSource, TypedSource
from aibi.core.store.store import Store

Roots = Any
Importing = Any
Grid = Any
Lifecycle = Any
ADA = "operator:ada"
_DESCRIPTOR: TypeAdapter[Descriptor] = TypeAdapter(Descriptor)

# t_empty: autumn empty, spring A; t_missing: not in the matrix; t_zero: 0 in both seasons;
# t_graded: B in autumn, A in spring; t_spring: 0 in autumn, A in spring.
TREES = (
    ("t_empty", "apple"),
    ("t_missing", "apple"),
    ("t_zero", "pear"),
    ("t_graded", "pear"),
    ("t_spring", "plum"),
)
HEADER = ("t_empty", "t_zero", "t_graded", "t_spring")
GRADES = (("autumn", "", "0", "B", "0"), ("spring", "A", "0", "A", "A"))
PICKINGS = (("t_graded", "ann", "bob"), ("t_spring", "ann", ""))


def _orchard(grid: Grid, **given: Any) -> Any:
    written: dict[str, Any] = {
        "trees": TREES,
        "header": HEADER,
        "grades": GRADES,
        "pickings": PICKINGS,
    }
    written.update(given)
    return grid.write(**written)


def _import(importing: Importing, grid: Grid, change: Callable[..., Any] | None = None) -> str:
    registry = grid.registry if change is None else grid.with_change(change)
    imported = importing(_orchard(grid), registry=registry, pack="grid")
    return cast(str, imported.built.manifest.hash)


def _refused(importing: Importing, grid: Grid, change: Callable[..., Any]) -> list[Any]:
    with pytest.raises(ImportRefused) as refused:
        _import(importing, grid, change)
    return list(refused.value.refusals)


def _said(refusal: Any) -> str:
    return "".join(json.dumps(segment.model_dump()) for segment in refusal.message)


def _descriptor_changed(id_: str, update: Callable[[dict[str, Any]], None]) -> Callable[..., Any]:
    """A change of the pack's result that edits one descriptor's JSON."""

    def change(result: ImportResult) -> ImportResult:
        descriptors = []
        for descriptor in result.descriptors:
            if descriptor.id == id_:
                dumped = descriptor.model_dump(mode="json")
                update(dumped)
                descriptor = _DESCRIPTOR.validate_python(dumped)
            descriptors.append(descriptor)
        return replace(result, descriptors=descriptors)

    return change


def _set(id_: str, field: str, value: Any, **more: Any) -> Callable[..., Any]:
    def update(dumped: dict[str, Any]) -> None:
        for name, given in {field: value, **more}.items():
            if given is None:
                dumped["fields"].pop(name, None)
                dumped["curation"].pop(f"/fields/{name}", None)
            else:
                dumped["fields"][name] = given
                dumped["curation"][f"/fields/{name}"] = dumped["curation"]["/label"]

    return _descriptor_changed(id_, update)


def _declared(**changes: Any) -> Callable[..., Any]:
    def change(result: ImportResult) -> ImportResult:
        return replace(result, reshaped={"yields": replace(result.reshaped["yields"], **changes)})

    return change


# --- Questions, in both engines -------------------------------------------------------------------


def _answers(store: Store, manifest: str, clause: dict[str, Any], unit: str = "trees") -> list[str]:
    """Each unit's answer, in the unit table's order, as ``VALUE`` with its reasons and marks
    (``UNKNOWN:NOT_COVERED``, ``FALSE+SCOPE_PARTIAL``), the SQL compiler's equal to the
    reference evaluator's."""
    written = {"aibi": "1", "dataset": "d", "unit": unit, "cohorts": {"c": {"all": [clause]}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    resolution = resolve(loaded.document, {"d": store.load(manifest)}, loaded.positions)
    assert resolution.refusals == []
    expected = evaluate(resolution.cohorts["c"])
    outline = resolve(loaded.document, {"d": store.outline(manifest)}, loaded.positions)
    with store.pin() as pin:
        pin.manifest(manifest)
        [counted] = run_cohorts(
            [outline.cohorts["c"]],
            {manifest: store.sources(manifest)},
            Workers(QueryLimits()),
            values=True,
        )
    assert counted.values is not None
    assert tuple(counted.values) == expected.values
    found = []
    for value in expected.values:
        text = value.value.value
        if value.reasons:
            text += ":" + ",".join(sorted(str(r.value) for r in value.reasons))
        if value.marks:
            text += "+" + ",".join(sorted(str(m.flag.value) for m in value.marks))
        found.append(text)
    return found


def _refusal_codes(store: Store, manifest: str, clause: dict[str, Any]) -> list[str]:
    written = {"aibi": "1", "dataset": "d", "unit": "trees", "cohorts": {"c": {"all": [clause]}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    resolution = resolve(loaded.document, {"d": store.load(manifest)}, loaded.positions)
    return [str(refusal.code) for refusal in resolution.refusals]


def _exists(table: str, *where: dict[str, Any], **given: Any) -> dict[str, Any]:
    return {"kind": "exists", "table": table, "where": list(where), **given}


def _value(column: str, *values: str, **given: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, "values": list(values), **given}


# --- Import -------------------------------------------------------------------------------------


def test_the_core_writes_the_absent_values_and_keeps_the_long_rows(
    importing: Importing, grid: Grid, store: Store
) -> None:
    manifest = _import(importing, grid)
    descriptors: dict[str, Any] = {d.id: d for d in store.descriptors(manifest)}
    grade = descriptors["yields.grade"]
    assert grade.fields.absent == ["0"]
    entry = grade.curation["/fields/absent"]
    assert (entry.status, entry.by, entry.inferred) == ("imported", "importer:grid@1.0.0", ["0"])
    yields = store.load(manifest).tables["yields"]
    assert sorted(
        (r["tree_id"].value, r["season"].value, r["grade"].value) for r in yields.rows
    ) == [
        ("t_empty", "spring", "A"),
        ("t_graded", "autumn", "B"),
        ("t_graded", "spring", "A"),
        ("t_spring", "spring", "A"),
    ]


def test_the_report_counts_what_the_reshape_dropped(importing: Importing, grid: Grid) -> None:
    imported = importing(_orchard(grid), registry=grid.registry, pack="grid")
    notes = [n for n in imported.notes if n.kind == "reshaped"]
    assert sorted((n.subject, n.count) for n in notes) == [("yields.grade", 1), ("yields.grade", 3)]
    [absent] = [n for n in notes if n.count == 3]
    assert any(s.model_dump().get("data") == "0" for s in absent.message)


def test_a_reshaped_note_is_in_fixed_words_and_uncounted_under_a_setting() -> None:
    from aibi.core.catalog import service
    from aibi.core.schema.curation import QueueNote

    note = QueueNote(kind="reshaped", subject="yields.grade", message=[], count=3)
    assert service.NOTE_TEXT["reshaped"] == "Source cells a pack's importer reshaped away"
    assert not service._shown(note, {})  # pyright: ignore[reportPrivateUsage]


def test_descriptor_text_does_not_depend_on_the_cells(
    roots: Roots, grid: Grid, store: Store, importing: Importing
) -> None:
    """D271: two matrices that differ only in their cells, one without a ``0``, give the same
    descriptors."""
    first = importing(_orchard(grid), registry=grid.registry, pack="grid", dataset="a")
    other = (("autumn", "C", "A", "B", "B"), ("spring", "A", "C", "A", "A"))
    second = importing(
        _orchard(grid, grades=other, name="other"), registry=grid.registry, pack="grid", dataset="b"
    )
    dumped = [
        sorted(
            json.dumps(d.model_dump(mode="json"), sort_keys=True)
            for d in imported.built.descriptors
        )
        for imported in (first, second)
    ]
    assert dumped[0] == dumped[1]


@pytest.mark.parametrize(
    "change",
    [
        _declared(absent=()),
        _declared(absent=("0", "0")),
        _declared(absent=("",)),
        _declared(absent=("x",) * 65),
        _declared(digest="a" * 63),
        _declared(digest="A" * 64),
        _declared(dropped=-1),
        _declared(digest=1 << 64),
        _declared(column="colour"),
        lambda result: replace(result, reshaped={"orchard": result.reshaped["yields"]}),
        lambda result: replace(
            result,
            sources={
                **result.sources,
                "yields": TypedSource(("tree_id", "season", "grade"), (("t_zero", "autumn", "0"),)),
            },
        ),
        lambda result: replace(
            result,
            sources={
                **result.sources,
                "yields": TypedSource(("tree_id", "season", "grade"), (("t_zero", "autumn", ""),)),
            },
        ),
        lambda result: replace(
            result,
            sources={
                **result.sources,
                "yields": TypedSource(
                    ("tree_id", "season", "grade"), (("t_zero", "autumn", None),)
                ),
            },
        ),
        lambda result: replace(
            result, sources={**result.sources, "yields": TextSource(b"tree_id,season,grade\n")}
        ),
        _set("yields.grade", "absent", ["0"]),
    ],
    ids=[
        "no absent values",
        "a repeated absent value",
        "an empty absent value",
        "65 absent values",
        "a digest of 63 digits",
        "an upper-case digest",
        "a negative count",
        "a digest of 65 bits",
        "a column it does not lay out",
        "a reshaped table that is not one it laid out",
        "a kept cell that is absent",
        "a kept cell that is empty",
        "a kept cell that is null",
        "a text source",
        "absent values the pack writes itself",
    ],
)
def test_a_malformed_declaration_is_pack_failed(
    importing: Importing, grid: Grid, store: Store, change: Callable[..., Any]
) -> None:
    [refusal] = _refused(importing, grid, change)
    assert refusal.code == "PACK_FAILED"
    assert store.labels("d") == []


def _grade_codes(codes: dict[str, str]) -> Callable[..., Any]:
    return _set("yields.grade", "missing_codes", codes)


_PANELS = {
    "assignment": {
        "table": "tree_groups",
        "parent_columns": {"tree_id": "tree_id"},
        "group_column": "group",
    },
    "groups": {
        "table": "group_seasons",
        "group_column": "group",
        "scope_columns": {"season": "season"},
        "covers_all_column": "group",
    },
}


@pytest.mark.parametrize(
    ("change", "where"),
    [
        (_set("yields", "role", "coverage"), "/fields/absent"),
        (
            _set("yields", "source", {"kind": "sheet", "name": "yields", "original_name": "g"}),
            "/fields/source",
        ),
        (_set("yields.grade", "permissible_values", None, datatype="string"), "/fields/datatype"),
        (_set("yields.grade", "permissible_values", None), "/fields/permissible_values"),
        (
            _set(
                "yields.grade",
                "permissible_values",
                {"values": [{"value": v} for v in ("A", "B", "C", "0")]},
            ),
            "/fields/permissible_values",
        ),
        (_grade_codes({"0": "UNKNOWN"}), "/fields/missing_codes"),
        (_set("yields", "primary_key", ["tree_id", "season", "grade"]), "/fields/primary_key"),
        (_set("cov:yields.tree_id", "parents", "all"), "/fields/parents"),
        (_set("cov:yields.tree_id", "parents", _PANELS), "/fields/parents"),
        (_set("group_seasons.season", "datatype", "category"), "/fields/parents"),
        (
            _set("cov:yields.tree_id", "record_filter", {"grade": ["A", "B", "C", "0"]}),
            "/fields/record_filter/grade",
        ),
    ],
    ids=[
        "a coverage table",
        "another source kind",
        "a string column",
        "no permissible values",
        "an absent permissible value",
        "an absent missing code",
        "a key holding the column",
        "coverage of every parent",
        "a group covering every value",
        "key columns of two datatypes",
        "a filter listing an absent value",
    ],
)
def test_the_rule_holds_at_import(
    importing: Importing, grid: Grid, change: Callable[..., Any], where: str
) -> None:
    refusals = _refused(importing, grid, change)
    assert {r.code for r in refusals} == {"INVALID_VALUE"}
    assert any((r.path or "").endswith(where) for r in refusals)


def test_a_derived_map_of_an_absent_value_is_refused(importing: Importing, grid: Grid) -> None:
    def change(result: ImportResult) -> ImportResult:
        derived = _DESCRIPTOR.validate_python(
            {
                "kind": "column",
                "id": "yields.call",
                "version": 1,
                "label": "call",
                "fields": {
                    "datatype": "category",
                    "derived": {
                        "op": "value_map",
                        "input": "grade",
                        "map": {"0": "none", "A": "a"},
                    },
                },
                "curation": {
                    "/label": {
                        "status": "imported",
                        "by": "importer:grid@1.0.0",
                        "at": "2026-01-01T00:00:00Z",
                    },
                    "/fields/datatype": {
                        "status": "imported",
                        "by": "importer:grid@1.0.0",
                        "at": "2026-01-01T00:00:00Z",
                    },
                    "/fields/derived": {
                        "status": "imported",
                        "by": "importer:grid@1.0.0",
                        "at": "2026-01-01T00:00:00Z",
                    },
                },
            }
        )
        return replace(result, descriptors=[*result.descriptors, derived])

    refusals = _refused(importing, grid, change)
    assert any((r.path or "").endswith("/fields/derived/map") for r in refusals)


def test_kept_cells_hold_only_permissible_values(importing: Importing, grid: Grid) -> None:
    """A kept cell the column does not permit (an unknown token kept as a value) is refused, so
    it can't read FALSE; a missing code equal to a permissible value is refused by the column's
    model, so no kept cell can be made missing either."""

    def change(result: ImportResult) -> ImportResult:
        rows = (
            (*row[:2], "D") if row[:2] == ("t_graded", "autumn") else row
            for row in cast(TypedSource, result.sources["yields"]).rows
        )
        yields = TypedSource(("tree_id", "season", "grade"), tuple(rows))
        return replace(result, sources={**result.sources, "yields": yields})

    refusals = _refused(importing, grid, change)
    assert [r.code for r in refusals] == ["INVALID_VALUE"]
    assert (refusals[0].path or "").endswith("/fields/permissible_values")


# --- The coverage, checked against the declaration (C) -----------------------------------------


def _coverage_rows(
    assigned: Sequence[tuple[str, str]], seasons: Sequence[tuple[str, str]]
) -> Callable[..., Any]:
    def change(result: ImportResult) -> ImportResult:
        return replace(
            result,
            sources={
                **result.sources,
                "tree_groups": TypedSource(("tree_id", "group"), tuple(assigned)),
                "group_seasons": TypedSource(("group", "season"), tuple(seasons)),
            },
        )

    return change


def test_an_honest_coverage_passes(importing: Importing, grid: Grid) -> None:
    _import(importing, grid)


def test_cancelling_errors_are_caught_by_the_digest(importing: Importing, grid: Grid) -> None:
    """Round 1's probe: the empty autumn cell of ``t_empty`` listed, and the absent autumn cell
    of ``t_spring`` left out. The counts agree (3 dropped, 3 listed without a row); the sets do
    not."""
    assigned = [
        ("t_empty", "both"),
        ("t_zero", "both"),
        ("t_graded", "both"),
        ("t_spring", "spring"),
    ]
    seasons = [("both", "autumn"), ("both", "spring"), ("spring", "spring")]
    [refusal] = _refused(importing, grid, _coverage_rows(assigned, seasons))
    assert refusal.code == "INVALID_VALUE"
    assert refusal.path is not None
    assert refusal.path.endswith("/fields/parents")
    assert "not the cells the importer dropped" in _said(refusal)


def test_a_coverage_that_leaves_a_row_out_is_refused(importing: Importing, grid: Grid) -> None:
    assigned = [
        ("t_empty", "spring"),
        ("t_zero", "both"),
        ("t_graded", "spring"),
        ("t_spring", "both"),
    ]
    seasons = [("both", "autumn"), ("both", "spring"), ("spring", "spring")]
    [refusal] = _refused(importing, grid, _coverage_rows(assigned, seasons))
    assert "outside the cells the coverage lists" in _said(refusal)


def test_a_count_that_does_not_match_is_refused(importing: Importing, grid: Grid) -> None:
    """Fewer cells declared than the coverage lists without a row: the listing is larger than
    the table's rows and the declared cells, so it is refused before it is built."""
    [refusal] = _refused(importing, grid, _declared(dropped=2, digest=cell_digest([])))
    assert "lists 7 cells, more than the table's rows and the cells dropped" in _said(refusal)


def test_more_cells_declared_than_listed_are_refused_counted(
    importing: Importing, grid: Grid
) -> None:
    [refusal] = _refused(importing, grid, _declared(dropped=4, digest=cell_digest([])))
    assert "3 listed, 4 dropped" in _said(refusal)


def test_cells_as_many_but_other_are_refused_as_other_cells(
    importing: Importing, grid: Grid
) -> None:
    [refusal] = _refused(importing, grid, _declared(digest=cell_digest([("x", "y")])))
    assert "as many, 3, but other cells" in _said(refusal)


def test_a_parent_with_two_assignments_is_refused(importing: Importing, grid: Grid) -> None:
    assigned = [
        ("t_empty", "spring"),
        ("t_zero", "both"),
        ("t_graded", "both"),
        ("t_spring", "both"),
        ("t_spring", "spring"),
    ]
    seasons = [("both", "autumn"), ("both", "spring"), ("spring", "spring")]
    [refusal] = _refused(importing, grid, _coverage_rows(assigned, seasons))
    assert "more than one assignment row" in _said(refusal)


# --- Questions about dropped cells (item 8), in both engines -------------------------------------


@pytest.fixture
def orchard(importing: Importing, grid: Grid) -> str:
    return _import(importing, grid)


def test_a_held_absent_cell_is_false_and_an_empty_one_not_covered(
    store: Store, orchard: str
) -> None:
    autumn = _value("yields.season", "autumn")
    # t_empty, t_missing, t_zero, t_graded, t_spring
    assert _answers(store, orchard, _exists("yields", autumn)) == [
        "UNKNOWN:NOT_COVERED",
        "UNKNOWN:NOT_COVERED",
        "FALSE",
        "TRUE",
        "FALSE",
    ]
    assert _answers(store, orchard, _exists("yields", autumn, _value("yields.grade", "A"))) == [
        "UNKNOWN:NOT_COVERED",
        "UNKNOWN:NOT_COVERED",
        "FALSE",
        "FALSE",
        "FALSE",
    ]
    assert (
        _answers(store, orchard, _exists("yields", _value("yields.season", "winter")))
        == ["UNKNOWN:NOT_COVERED"] * 5
    )


def test_a_false_without_the_scope_column_is_partial(store: Store, orchard: str) -> None:
    assert _answers(store, orchard, _exists("yields", _value("yields.grade", "C"))) == [
        "FALSE+SCOPE_PARTIAL",
        "UNKNOWN:NOT_COVERED",
        "FALSE+SCOPE_PARTIAL",
        "FALSE+SCOPE_PARTIAL",
        "FALSE+SCOPE_PARTIAL",
    ]


def test_every_over_no_rows_is_unknown(store: Store, orchard: str) -> None:
    answers = _answers(
        store, orchard, _exists("yields", _value("yields.grade", "A", "B"), quantifier="every")
    )
    assert answers[2] == "UNKNOWN:NO_ROWS"
    assert answers[3].startswith("TRUE")


def test_a_down_step_into_a_reshaped_table_is_bounded(store: Store, orchard: str) -> None:
    """The gate restricts a reshaped table's key to the cells its coverage lists and its value
    column to its permissible values (D386), so D383 takes the step as bounded and withholds its
    memberships under a disclosure setting; the explode's step is open."""
    from aibi.core.engine.resolve import free_column, open_step

    release = store.load(orchard)
    assert not open_step(release, "rel:yields.tree_id")
    assert not free_column(release, "yields", "season")
    assert not free_column(release, "yields", "grade")
    assert open_step(release, "rel:pickings.tree_id")


def test_a_question_about_the_absent_value_is_refused(store: Store, orchard: str) -> None:
    assert "NOT_PERMISSIBLE" in _refusal_codes(
        store, orchard, _exists("yields", _value("yields.grade", "0"))
    )


def test_an_exploded_empty_slot_is_no_row(store: Store, orchard: str) -> None:
    """``t_spring`` was picked by ann alone: bob is FALSE. The trees ``pickings.tsv`` never
    named are not covered (its coverage lists the trees it held, m6)."""
    answers = _answers(store, orchard, _exists("pickings", _value("pickings.picker", "bob")))
    assert answers == [
        "UNKNOWN:NOT_COVERED",
        "UNKNOWN:NOT_COVERED",
        "UNKNOWN:NOT_COVERED",
        "TRUE",
        "FALSE",
    ]


def test_the_reshaped_table_as_the_unit_holds_observed_cells(store: Store, orchard: str) -> None:
    """Rows in the matrix's order: t_graded's autumn, then the spring row's three."""
    assert _answers(store, orchard, _value("yields.season", "autumn"), unit="yields") == [
        "TRUE",
        "FALSE",
        "FALSE",
        "FALSE",
    ]


# --- Changes: what the importer owns is frozen ---------------------------------------------------


def _change(store: Store, manifest: str, *edits: dict[str, Any]) -> str:
    opened = open_session(store, "d", ADA)
    request = ChangeRequest.model_validate({"edits": list(edits)})
    return change(store, "d", opened.handle, opened.draft, request, ADA)


def _change_codes(store: Store, manifest: str, *edits: dict[str, Any]) -> list[str]:
    with pytest.raises(EditRefused) as refused:
        _change(store, manifest, *edits)
    return [str(refusal.code) for refusal in refused.value.refusals]


def _setting(descriptor: str, pointer: str, value: Any) -> dict[str, Any]:
    return {"op": "set", "descriptor": descriptor, "pointer": pointer, "value": value}


@pytest.mark.parametrize(
    ("edit", "code"),
    [
        (_setting("yields.grade", "/fields/absent", ["0", "N"]), "COLUMNS_CHANGED"),
        (
            {"op": "remove", "descriptor": "yields.grade", "pointer": "/fields/absent"},
            "COLUMNS_CHANGED",
        ),
        (_setting("yields.season", "/fields/absent", ["x"]), "INVALID_VALUE"),
        (
            _setting(
                "yields", "/fields/source", {"kind": "sheet", "name": "y", "original_name": "y"}
            ),
            "INVALID_VALUE",
        ),
        (
            _setting("yields", "/fields/primary_key", ["tree_id", "season", "grade"]),
            "INVALID_VALUE",
        ),
        (_setting("cov:yields.tree_id", "/fields/parents", "all"), "INVALID_VALUE"),
        (
            _setting(
                "cov:yields.tree_id",
                "/fields/parents",
                {
                    "table": "group_seasons",
                    "parent_columns": {"group": "tree_id"},
                    "scope_columns": {"season": "season"},
                },
            ),
            "COLUMNS_CHANGED",
        ),
        (_setting("yields.grade", "/fields/missing_codes", {"0": "UNKNOWN"}), "INVALID_VALUE"),
        (
            _setting(
                "yields.grade",
                "/fields/permissible_values",
                {"values": [{"value": "A"}, {"value": "C"}]},
            ),
            "INVALID_VALUE",
        ),
        (_setting("yields.grade", "/fields/datatype", "string"), "CONFLICTING_MEMBERS"),
        ({"op": "remove_descriptor", "descriptor": "rel:yields.tree_id"}, "UNKNOWN_DESCRIPTOR"),
    ],
    ids=[
        "absent values changed",
        "absent values removed",
        "absent values on another column",
        "another source kind",
        "another key",
        "coverage of every parent",
        "the coverage repointed",
        "an absent missing code",
        "a held value no longer permitted",
        "another datatype",
        "the relationship removed alone",
    ],
)
def test_a_change_to_what_the_importer_owns_is_refused(
    store: Store, orchard: str, edit: dict[str, Any], code: str
) -> None:
    assert code in _change_codes(store, orchard, edit)


def test_removing_the_relationship_into_a_reshaped_table_is_refused(
    store: Store, orchard: str
) -> None:
    """Alone, its coverage names a relationship the release lacks (``check_release`` runs first,
    D246); with its coverage, the importer owns it."""
    codes = _change_codes(
        store,
        orchard,
        {"op": "remove_descriptor", "descriptor": "cov:yields.tree_id"},
        {"op": "remove_descriptor", "descriptor": "rel:yields.tree_id"},
    )
    assert codes == ["COLUMNS_CHANGED"]


def test_removing_the_coverage_leaves_it_open(store: Store, orchard: str) -> None:
    changed = _change(
        store,
        orchard,
        {"op": "remove", "descriptor": "cov:yields.tree_id", "pointer": "/fields/parents"},
    )
    autumn = _exists("yields", _value("yields.season", "autumn"))
    assert _answers(store, changed, autumn) == [
        "UNKNOWN:NO_INFORMATION",
        "UNKNOWN:NO_INFORMATION",
        "UNKNOWN:NO_INFORMATION",
        "TRUE",
        "UNKNOWN:NO_INFORMATION",
    ]


def test_allowed_changes_reuse_the_blob(store: Store, orchard: str) -> None:
    changed = _change(
        store,
        orchard,
        _setting("yields", "/label", "Harvests"),
        {"op": "confirm", "descriptor": "yields.grade", "pointers": ["/fields/absent"]},
        _setting(
            "yields.grade",
            "/fields/permissible_values",
            {"values": [{"value": v} for v in ("A", "B", "C", "D")], "ordered": True},
        ),
    )
    before, after = store.manifest(orchard).table("yields"), store.manifest(changed).table("yields")
    assert before is not None
    assert after is not None
    assert before.hash == after.hash


# --- Re-import ------------------------------------------------------------------------------------


def test_a_reimport_keeps_curation_and_the_importer_s_fields(
    roots: Roots, lifecycle: Lifecycle, grid: Grid, store: Store
) -> None:
    path = _orchard(grid)
    lifecycle.import_(path, registry=grid.registry, pack="grid")
    other = (("autumn", "C", "0", "B", "0"), ("spring", "A", "0", "A", "A"))
    _orchard(grid, grades=other)
    published = lifecycle.reimport(path, registry=grid.registry, pack="grid")
    descriptors: dict[str, Any] = {d.id: d for d in store.descriptors(published.manifest)}
    assert descriptors["yields.grade"].fields.absent == ["0"]
    assert descriptors["yields"].fields.source is not None
    assert descriptors["yields"].fields.source.kind == "pack"


def test_a_reimport_that_changes_only_empty_cells_is_no_change(
    roots: Roots, lifecycle: Lifecycle, grid: Grid
) -> None:
    path = _orchard(grid)
    lifecycle.import_(path, registry=grid.registry, pack="grid")
    _orchard(grid, header=(*HEADER, "t_missing"), grades=tuple((*row, "") for row in GRADES))
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(path, registry=grid.registry, pack="grid")
    assert [r.code for r in refused.value.refusals] == ["NO_CHANGE"]


_REMOVALS = [
    {"op": "remove", "descriptor": "cov:yields.tree_id", "pointer": "/fields/parents"},
    {"op": "remove_descriptor", "descriptor": "cov:yields.tree_id"},
]


@pytest.mark.parametrize("edit", _REMOVALS, ids=["its parents", "the descriptor"])
def test_a_coverage_removed_in_a_session_stays_removed_across_a_reimport(
    roots: Roots, lifecycle: Lifecycle, grid: Grid, store: Store, edit: dict[str, Any]
) -> None:
    """Removal is the operator's way out of the importer's coverage (D386): a removed field's
    tombstone holds while the importer infers the same (D239), so the relationship stays open."""
    path = _orchard(grid)
    lifecycle.import_(path, registry=grid.registry, pack="grid")
    opened = open_session(store, "d", ADA)
    draft = change(
        store,
        "d",
        opened.handle,
        opened.draft,
        ChangeRequest.model_validate({"edits": [edit]}),
        ADA,
    )
    publish(store, "d", opened.handle, draft, ADA)
    _orchard(grid, grades=(("autumn", "C", "0", "B", "0"), ("spring", "A", "0", "A", "A")))
    published = lifecycle.reimport(path, registry=grid.registry, pack="grid")
    descriptors: dict[str, Any] = {d.id: d for d in store.descriptors(published.manifest)}
    coverage = descriptors.get("cov:yields.tree_id")
    assert coverage is None or coverage.fields.parents is None
    autumn = _exists("yields", _value("yields.season", "autumn"))
    assert "FALSE" not in _answers(store, published.manifest, autumn)


def test_the_core_s_importer_writes_no_absent_values(
    roots: Roots, lifecycle: Lifecycle, grid: Grid, store: Store
) -> None:
    published = lifecycle.import_(_orchard(grid))
    assert all(
        getattr(d.fields, "absent", None) is None for d in store.descriptors(published.manifest)
    )


# --- Erasure --------------------------------------------------------------------------------------


def test_a_tree_in_a_reshaped_table_blocks_erasure_until_a_reimport_drops_it(
    roots: Roots, lifecycle: Lifecycle, grid: Grid, store: Store
) -> None:
    from aibi.core.store.erasure import erase
    from aibi.core.store.store import StoreRefused

    path = _orchard(grid)
    lifecycle.import_(path, registry=grid.registry, pack="grid")
    with pytest.raises(StoreRefused) as refused:
        erase(store, "d", "trees", ["t_graded"], ADA)
    assert refused.value.refusal.code == "ERASURE_BLOCKED"
    _orchard(
        grid,
        trees=[t for t in TREES if t[0] != "t_graded"],
        header=[h for h in HEADER if h != "t_graded"],
        grades=tuple((row[0], row[1], row[2], row[4]) for row in GRADES),
        pickings=[p for p in PICKINGS if p[0] != "t_graded"],
    )
    lifecycle.reimport(path, registry=grid.registry, pack="grid")
    erased = erase(store, "d", "trees", ["t_graded"], ADA)
    assert erased.withdrawn == (1,)


# --- Round 1 of #77's review: C on every build, a commitment, and the gaps ---------------------


def test_retyping_the_group_columns_is_refused(store: Store, orchard: str) -> None:
    """B1: retyped to ``integer``, groups named ``01`` and ``1`` would merge, listing a cell
    the matrix left empty; the group columns are string or category of one datatype."""
    codes = _change_codes(
        store,
        orchard,
        _setting("tree_groups.group", "/fields/datatype", "integer"),
        _setting("group_seasons.group", "/fields/datatype", "integer"),
    )
    assert "INVALID_VALUE" in codes


def test_a_derived_column_in_the_coverage_is_refused(importing: Importing, grid: Grid) -> None:
    """B1: a derived column the operator could edit would change what the coverage lists."""

    def change(result: ImportResult) -> ImportResult:
        entry = result.descriptors[0].model_dump(mode="json")["curation"]["/label"]
        label = _DESCRIPTOR.validate_python(
            {
                "kind": "column",
                "id": "group_seasons.alias",
                "version": 1,
                "label": "alias",
                "fields": {
                    "datatype": "string",
                    "derived": {"op": "value_map", "input": "group", "map": {"g1": "g1"}},
                },
                "curation": {
                    "/label": entry,
                    "/fields/datatype": entry,
                    "/fields/derived": entry,
                },
            }
        )
        descriptors = []
        for descriptor in result.descriptors:
            if descriptor.id == "cov:yields.tree_id":
                dumped = descriptor.model_dump(mode="json")
                dumped["fields"]["parents"]["groups"]["group_column"] = "alias"
                descriptor = _DESCRIPTOR.validate_python(dumped)
            descriptors.append(descriptor)
        return replace(result, descriptors=[*descriptors, label])

    refusals = _refused(importing, grid, change)
    assert any("stored columns, not derived ones" in _said(r) for r in refusals)


def test_the_declaration_is_kept_with_the_table_and_checked_at_every_change(
    store: Store, orchard: str
) -> None:
    """B1, the class: the gate checks the coverage against the import's declaration on every
    build, so a change that alters what it lists is refused whatever field it edits."""
    from aibi.core.store import build
    from aibi.core.store.build import BuildRefused

    manifest = store.manifest(orchard)
    entry = manifest.table("yields")
    assert entry is not None
    assert entry.reshaped is not None
    assert (entry.reshaped.column, entry.reshaped.dropped) == ("grade", 3)
    changed = _change(store, orchard, _setting("yields", "/label", "Harvests"))
    kept = store.manifest(changed).table("yields")
    assert kept is not None
    assert kept.reshaped == entry.reshaped
    other = entry.model_copy(update={"reshaped": entry.reshaped.model_copy(update={"dropped": 2})})
    base = manifest.model_copy(
        update={"tables": tuple(other if t.id == "yields" else t for t in manifest.tables)}
    )
    descriptors = store.descriptors(orchard)
    with store.pin() as pin:
        pin.manifest(orchard)
        with pytest.raises(BuildRefused) as refused:
            build.change_release(store.blobs, base, descriptors, descriptors, holder=pin)
    assert [r.code for r in refused.value.refusals] == ["INVALID_VALUE"]
    assert all(str(r.path).endswith("/fields/parents") for r in refused.value.refusals)
    assert all("(D386)" in _said(r) for r in refused.value.refusals)


def test_other_tables_entries_hold_no_declaration(store: Store, orchard: str) -> None:
    manifest = store.manifest(orchard)
    assert [t.id for t in manifest.tables if t.reshaped is not None] == ["yields"]
    assert "reshaped" not in json.dumps(
        manifest.table("trees").model_dump(mode="json", exclude_none=True)  # type: ignore[union-attr]
    )


def test_the_digest_is_a_commitment_to_the_set() -> None:
    """M1: SHA-256 over the sorted, length-prefixed cells; order and repeats do not count; a
    value's boundary does; and four cells chosen to match four others by a sum of 64-bit hashes
    (round 1 of #77's review, probe p11) do not match here."""
    import hashlib

    assert cell_digest([]) == hashlib.sha256(b"").hexdigest()

    def encoded(*values: str) -> bytes:
        return b"".join(len(v.encode()).to_bytes(8, "big") + v.encode() for v in values)

    one = encoded("t1", "autumn")
    expected = hashlib.sha256(len(one).to_bytes(8, "big") + one).hexdigest()
    assert cell_digest([("t1", "autumn")]) == expected
    cells = [("t1", "autumn"), ("t2", "spring"), ("t3", "é")]
    assert cell_digest(cells) == cell_digest(list(reversed(cells))) == cell_digest(cells * 2)
    assert cell_digest([("ab", "c")]) != cell_digest([("a", "bc")])
    assert cell_digest([("ab",)]) != cell_digest([("a", "b")])
    fakes = [("t_x", f"fake{i}_{n}") for i, n in enumerate((1236, 39149, 80318, 58365))]
    zeros = [("t_x", f"zero{i + 4}_{n}") for i, n in enumerate((99499, 130074, 9239, 63746))]
    assert cell_digest(fakes) != cell_digest(zeros)
    assert len(cell_digest(zeros)) == 64


def _dishonest(result: ImportResult) -> ImportResult:
    """The coverage lists t_empty's empty autumn cell as held."""
    groups = cast(TypedSource, result.sources["tree_groups"])
    rows = tuple((tree, "both" if tree == "t_empty" else group) for tree, group in groups.rows)
    seasons = cast(TypedSource, result.sources["group_seasons"])
    extra = (("both", "autumn"), ("both", "spring"))
    return replace(
        result,
        sources={
            **result.sources,
            "tree_groups": TypedSource(groups.columns, rows),
            "group_seasons": TypedSource(seasons.columns, (*seasons.rows, *extra)),
        },
    )


def test_the_coverage_is_checked_at_a_reimport(
    roots: Roots, lifecycle: Lifecycle, grid: Grid
) -> None:
    path = _orchard(grid)
    lifecycle.import_(path, registry=grid.registry, pack="grid")
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(path, registry=grid.with_change(_dishonest), pack="grid")
    assert [r.code for r in refused.value.refusals] == ["INVALID_VALUE"]
    assert all(str(r.path).endswith("/fields/parents") for r in refused.value.refusals)
    assert all("(D386)" in _said(r) for r in refused.value.refusals)


def _direct(result: ImportResult) -> ImportResult:
    """The coverage as one table listing each held cell, in place of groups."""
    groups = {
        str(tree): str(group)
        for tree, group in cast(TypedSource, result.sources["tree_groups"]).rows
    }
    seasons: dict[str, list[str]] = {}
    for group, season in cast(TypedSource, result.sources["group_seasons"]).rows:
        seasons.setdefault(str(group), []).append(str(season))
    cells: tuple[tuple[SourceValue, ...], ...] = tuple(
        (tree, season) for tree, group in groups.items() for season in seasons[group]
    )
    sources = {
        name: source
        for name, source in result.sources.items()
        if name not in ("tree_groups", "group_seasons")
    }
    sources["cells"] = TypedSource(("tree_id", "season"), cells)
    layouts = {
        name: layout
        for name, layout in result.layouts.items()
        if name not in ("tree_groups", "group_seasons")
    }
    layouts["cells"] = Layout("cells", (("tree_id", "tree_id"), ("season", "season")))
    descriptors = []
    for descriptor in result.descriptors:
        if descriptor.id.split(".")[0] in ("tree_groups", "group_seasons"):
            continue
        dumped = descriptor.model_dump(mode="json")
        if descriptor.id == "cov:yields.tree_id":
            dumped["fields"]["parents"] = {
                "table": "cells",
                "parent_columns": {"tree_id": "tree_id"},
                "scope_columns": {"season": "season"},
            }
        descriptors.append(_DESCRIPTOR.validate_python(dumped))
    curation = result.descriptors[0].model_dump(mode="json")["curation"]["/label"]
    for id_, fields in (
        ("cells", {"role": "coverage"}),
        ("cells.tree_id", {"datatype": "string"}),
        ("cells.season", {"datatype": "string"}),
    ):
        descriptors.append(
            _DESCRIPTOR.validate_python(
                {
                    "kind": "table" if "." not in id_ else "column",
                    "id": id_,
                    "version": 1,
                    "label": id_,
                    "fields": fields,
                    "curation": {"/label": curation, **{f"/fields/{k}": curation for k in fields}},
                }
            )
        )
    return replace(result, sources=sources, layouts=layouts, descriptors=descriptors)


def test_a_direct_coverage_is_checked_too(importing: Importing, grid: Grid, store: Store) -> None:
    manifest = _import(importing, grid, _direct)
    autumn = _value("yields.season", "autumn")
    assert _answers(store, manifest, _exists("yields", autumn)) == [
        "UNKNOWN:NOT_COVERED",
        "UNKNOWN:NOT_COVERED",
        "FALSE",
        "TRUE",
        "FALSE",
    ]

    def dishonest(result: ImportResult) -> ImportResult:
        direct = _direct(result)
        cells = cast(TypedSource, direct.sources["cells"])
        more = TypedSource(cells.columns, (*cells.rows, ("t_empty", "autumn")))
        return replace(direct, sources={**direct.sources, "cells": more})

    refusals = _refused(importing, grid, dishonest)
    assert any("not the cells the importer dropped" in _said(r) for r in refusals)


def test_a_coverage_removed_in_one_change_is_not_put_back_in_the_next(
    store: Store, orchard: str
) -> None:
    """Each change is compared with the draft before it: once removed, ``parents`` comes back
    only by discarding the session (D386)."""
    found = next(d for d in store.descriptors(orchard) if d.id == "cov:yields.tree_id")
    held = found.model_dump(mode="json")["fields"]["parents"]
    removal = {"op": "remove", "descriptor": "cov:yields.tree_id", "pointer": "/fields/parents"}
    opened = open_session(store, "d", ADA)
    first = change(
        store,
        "d",
        opened.handle,
        opened.draft,
        ChangeRequest.model_validate({"edits": [removal]}),
        ADA,
    )
    back = ChangeRequest.model_validate(
        {"edits": [_setting("cov:yields.tree_id", "/fields/parents", held)]}
    )
    with pytest.raises(EditRefused) as refused:
        change(store, "d", opened.handle, first, back, ADA)
    assert "COLUMNS_CHANGED" in [str(r.code) for r in refused.value.refusals]


def test_a_proposal_to_change_what_the_importer_owns_is_refused(store: Store, orchard: str) -> None:
    from aibi.core.schema.curation import ProposalInput
    from aibi.core.store.proposals import propose_descriptor

    given = ProposalInput(
        descriptor="yields.grade", pointer="/fields/absent", value=cast(Any, ["0", "N"])
    )
    with pytest.raises(EditRefused) as refused:
        propose_descriptor(store, "d", given, "agent:helper")
    assert "COLUMNS_CHANGED" in [str(r.code) for r in refused.value.refusals]


def test_a_reimport_that_first_reshapes_a_table_takes_the_importer_s_fields(
    roots: Roots, lifecycle: Lifecycle, grid: Grid, store: Store
) -> None:
    """``owned``: the operator keyed the table by its grade while it was no unpivot; when the
    pack first declares it, the importer's key replaces the operator's, which would hold the
    value column (rule 4 (d))."""
    path = _orchard(grid)
    first = lifecycle.import_(
        path, registry=grid.with_change(lambda r: replace(r, reshaped={})), pack="grid"
    )
    opened = open_session(store, "d", ADA)
    request = ChangeRequest.model_validate(
        {"edits": [_setting("yields", "/fields/primary_key", ["tree_id", "season", "grade"])]}
    )
    draft = change(store, "d", opened.handle, opened.draft, request, ADA)
    publish(store, "d", opened.handle, draft, ADA)
    assert first.manifest != draft
    published = lifecycle.reimport(path, registry=grid.registry, pack="grid")
    descriptors: dict[str, Any] = {d.id: d for d in store.descriptors(published.manifest)}
    assert descriptors["yields"].fields.primary_key == ["tree_id", "season"]
    assert descriptors["yields.grade"].fields.absent == ["0"]


def test_a_reimport_that_stops_declaring_a_table_it_unpivoted_is_pack_failed(
    roots: Roots, lifecycle: Lifecycle, grid: Grid
) -> None:
    """m5: the pack still drops cells, which the core could no longer check."""
    path = _orchard(grid)
    lifecycle.import_(path, registry=grid.registry, pack="grid")
    stopped = grid.with_change(lambda r: replace(r, reshaped={}))
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(path, registry=stopped, pack="grid")
    [refusal] = refused.value.refusals
    assert refusal.code == "PACK_FAILED"
    assert "without declaring what it dropped" in _said(refusal)


def test_absent_values_are_written_sorted(importing: Importing, grid: Grid, store: Store) -> None:
    manifest = _import(importing, grid, _declared(absent=("Z", "0")))
    found = {d.id: d for d in store.descriptors(manifest)}
    assert found["yields.grade"].fields.absent == ["0", "Z"]  # type: ignore[union-attr]


def test_the_key_may_list_the_scope_first(importing: Importing, grid: Grid, store: Store) -> None:
    """The cells are digested in the key's order, here the season's first."""
    keyed = _set("yields", "primary_key", ["season", "tree_id"])
    zeros = [("autumn", "t_zero"), ("spring", "t_zero"), ("autumn", "t_spring")]
    redigested = _declared(digest=cell_digest(zeros))
    manifest = _import(importing, grid, lambda result: redigested(keyed(result)))
    autumn = _value("yields.season", "autumn")
    assert _answers(store, manifest, _exists("yields", autumn))[0] == "UNKNOWN:NOT_COVERED"


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (
            lambda result: replace(
                result,
                sources={**result.sources, "yields": TextSource(b"tree_id,season,grade\n")},
            ),
            "not laid out on a typed source",
        ),
        (
            lambda result: replace(result, reshaped={"orchard": result.reshaped["yields"]}),
            "a reshaped table that is not one it laid out",
        ),
        (_declared(column="colour"), "a column its table does not lay out"),
        (_declared(absent=("\ud800",)), "distinct, non-empty"),
    ],
    ids=["a text source", "a table not laid out", "a column not laid out", "a surrogate"],
)
def test_a_malformed_declaration_says_why(
    importing: Importing, grid: Grid, change: Callable[..., Any], said: str
) -> None:
    [refusal] = _refused(importing, grid, change)
    assert refusal.code == "PACK_FAILED"
    assert said in _said(refusal)


def test_a_declaration_of_another_type_is_pack_failed(importing: Importing, grid: Grid) -> None:
    from aibi.core.schema.pack_api import Reshaped

    sub = type("SubReshaped", (Reshaped,), {})

    def change(result: ImportResult) -> ImportResult:
        found = result.reshaped["yields"]
        copied = sub(found.column, found.absent, found.dropped, found.digest, found.empty)
        return replace(result, reshaped={"yields": copied})

    [refusal] = _refused(importing, grid, change)
    assert refusal.code == "PACK_FAILED"


# --- Round 2: check C at a change, its bound, and the gaps ----------------------------------------


class _Hashing(str):
    """An absent value that is no exact ``str``, whose hash would run the pack's code."""

    hashed: ClassVar[list[str]] = []

    def __hash__(self) -> int:
        _Hashing.hashed.append("hash")
        raise RuntimeError("chosen words from the pack")


def test_absent_values_are_checked_before_they_are_hashed(importing: Importing, grid: Grid) -> None:
    """m1: no code of the pack's runs within the checks (D385)."""
    _Hashing.hashed.clear()
    [refusal] = _refused(importing, grid, _declared(absent=(_Hashing("0"), _Hashing("0"))))
    assert refusal.code == "PACK_FAILED"
    assert "chosen words" not in _said(refusal)
    assert _Hashing.hashed == []


def test_cells_dropped_past_import_cells_are_refused_naming_it(
    importing: Importing, grid: Grid
) -> None:
    """The declared counts bound the coverage's listing, so they are bounded themselves."""
    with pytest.raises(ImportRefused) as refused:
        importing(
            _orchard(grid),
            registry=grid.with_change(_declared(empty=1000)),
            pack="grid",
            limits=ImportLimits(import_cells=999),
        )
    [refusal] = refused.value.refusals
    assert refusal.code == "LIMIT_EXCEEDED"
    assert refusal.limit is not None
    assert refusal.limit.name == "import_cells"


def test_a_listing_larger_than_the_rows_and_drops_is_refused_unbuilt(
    importing: Importing, grid: Grid
) -> None:
    """A group of a thousand seasons for every tree lists far more cells than the table's rows
    and the cells dropped as absent; it is counted, not built, and refused."""
    trees = [tree for tree, _ in TREES]
    seasons = [("g", f"s{n}") for n in range(1000)]
    refusals = _refused(importing, grid, _coverage_rows([(t, "g") for t in trees], seasons))
    assert [r.code for r in refusals] == ["INVALID_VALUE"]
    assert "lists 5000 cells, more than the table's rows" in _said(refusals[0])


def _declarations_checked(monkeypatch: pytest.MonkeyPatch) -> list[set[str]]:
    from aibi.core.store import gate

    seen: list[set[str]] = []
    original = gate._Checks.declarations  # pyright: ignore[reportPrivateUsage]

    def spy(self: Any, declared: Any) -> None:
        seen.append(set(declared))
        original(self, declared)

    monkeypatch.setattr(gate._Checks, "declarations", spy)  # pyright: ignore[reportPrivateUsage]
    return seen


@pytest.mark.parametrize(
    ("edit", "checked"),
    [
        (_setting("yields", "/label", "Harvests"), set()),
        (_setting("trees", "/label", "Orchard trees"), set()),
        (_setting("group_seasons.season", "/definition", "When it was picked"), set()),
        (
            _setting(
                "yields.grade",
                "/fields/permissible_values",
                {
                    "values": [
                        {"value": "A", "label": "First"},
                        {"value": "B", "label": "Second"},
                        {"value": "C"},
                    ]
                },
            ),
            set(),
        ),
        (
            _setting(
                "yields.grade",
                "/fields/permissible_values",
                {"values": [{"value": "A"}, {"value": "B"}, {"value": "C"}, {"value": "Z"}]},
            ),
            set(),
        ),
        (_setting("yields.grade", "/fields/completeness", "partial"), set()),
        (_setting("group_seasons.season", "/fields/completeness", "partial"), set()),
        (_setting("yields", "/fields/grain", "One tree in one season"), set()),
        (
            {"op": "remove", "descriptor": "cov:yields.tree_id", "pointer": "/fields/parents"},
            {"yields"},
        ),
        (
            {
                "op": "put",
                "descriptor": {
                    "kind": "column",
                    "id": "yields.good",
                    "label": "Good",
                    "fields": {
                        "datatype": "category",
                        "derived": {"op": "value_map", "input": "grade", "map": {"A": "yes"}},
                    },
                },
            },
            {"yields"},
        ),
    ],
    ids=[
        "its label",
        "the parent's label",
        "a definition",
        "a category's labels",
        "a category added",
        "a column's completeness",
        "a coverage column's completeness",
        "its grain",
        "the coverage's parents",
        "a derived column, which rebuilds it",
    ],
)
def test_a_change_checks_the_coverage_again_only_when_what_it_reads_changed(
    store: Store,
    orchard: str,
    monkeypatch: pytest.MonkeyPatch,
    edit: dict[str, Any] | list[dict[str, Any]],
    checked: set[str],
) -> None:
    """M1 (rounds 2 and 3): the check's cost is the matrix's, so a change pays it only when what
    it reads (the key, the absent values, the relationships' columns, the coverages' ``parents``
    and the tables' contents) differs from what the base's check read, not for any other field
    of those descriptors."""
    seen = _declarations_checked(monkeypatch)
    changed = _change(store, orchard, *(edit if isinstance(edit, list) else [edit]))
    assert seen == [checked]
    entry = store.manifest(changed).table("yields")
    before = store.manifest(orchard).table("yields")
    assert entry is not None
    assert before is not None
    assert entry.reshaped is not None
    assert before.reshaped is not None
    assert (entry.reshaped.checked == before.reshaped.checked) == (checked == set())


def test_the_digest_of_what_the_check_read_is_kept_with_the_declaration(
    store: Store, orchard: str
) -> None:
    from aibi.core.store import gate

    manifest = store.manifest(orchard)
    entry = manifest.table("yields")
    assert entry is not None
    assert entry.reshaped is not None
    hashes = {table.id: table.hash for table in manifest.tables}
    descriptors = store.descriptors(orchard)
    expected = gate.declaration_inputs("yields", entry.reshaped, descriptors, hashes)
    assert entry.reshaped.checked == expected
    shuffled = list(reversed(descriptors))
    assert gate.declaration_inputs("yields", entry.reshaped, shuffled, hashes) == expected
    for table in ("yields", "tree_groups", "group_seasons"):
        moved = {**hashes, table: "0" * 64}
        assert gate.declaration_inputs("yields", entry.reshaped, descriptors, moved) != expected
    unread = {**hashes, "pickings": "0" * 64}
    assert gate.declaration_inputs("yields", entry.reshaped, descriptors, unread) == expected

    declared = entry.reshaped

    def edited(id_: str, change: Callable[[dict[str, Any]], None]) -> str:
        written: list[Descriptor] = []
        for descriptor in descriptors:
            if descriptor.id == id_:
                dumped = descriptor.model_dump(mode="json")
                change(dumped)
                descriptor = _DESCRIPTOR.validate_python(dumped)
            written.append(descriptor)
        return gate.declaration_inputs("yields", declared, written, hashes)

    def setting(*path: str, value: Any) -> Callable[[dict[str, Any]], None]:
        def change(dumped: dict[str, Any]) -> None:
            held = dumped
            for part in path[:-1]:
                held = held[part]
            held[path[-1]] = value
            if path[0] == "fields":
                dumped["curation"][f"/fields/{path[1]}"] = dumped["curation"]["/label"]

        return change

    read = [
        edited("yields", setting("fields", "primary_key", value=["season", "tree_id"])),
        edited("yields.grade", setting("fields", "absent", value=["0", "N"])),
        edited("rel:yields.tree_id", setting("fields", "parent_columns", value=["variety"])),
        edited("cov:yields.tree_id", setting("fields", "parents", value="all")),
        edited(
            "cov:yields.tree_id",
            setting("fields", "parents", "assignment", "group_column", value="tree_id"),
        ),
    ]
    assert all(found != expected for found in read)
    unread_fields = [
        edited("yields", setting("label", value="Harvests")),
        edited("yields", setting("fields", "grain", value="One tree in one season")),
        edited("yields.grade", setting("fields", "completeness", value="partial")),
        edited("group_seasons.season", setting("fields", "completeness", value="partial")),
        edited("trees", setting("fields", "grain", value="A tree")),
    ]
    assert all(found == expected for found in unread_fields)
    other = entry.reshaped.model_copy(update={"dropped": 2})
    assert gate.declaration_inputs("yields", other, descriptors, hashes) != expected


def test_the_digest_holds_the_check_s_version(
    store: Store, orchard: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """m1 (round 3): a release checked by an earlier version of the check is checked again at its
    next change, whatever it changes."""
    from aibi.core.store import gate

    monkeypatch.setattr(gate, "CHECK_VERSION", "aibi.reshaped-coverage/2")
    seen = _declarations_checked(monkeypatch)
    _change(store, orchard, _setting("yields", "/label", "Harvests"))
    assert seen == [{"yields"}]


def test_a_declaration_naming_a_column_without_absent_values_is_refused(
    store: Store, orchard: str
) -> None:
    """m4: the declaration's column is the one the import wrote ``absent`` on."""
    from aibi.core.store import build
    from aibi.core.store.build import BuildRefused

    manifest = store.manifest(orchard)
    entry = manifest.table("yields")
    assert entry is not None
    assert entry.reshaped is not None
    other = entry.model_copy(
        update={"reshaped": entry.reshaped.model_copy(update={"column": "season", "checked": None})}
    )
    base = manifest.model_copy(
        update={"tables": tuple(other if t.id == "yields" else t for t in manifest.tables)}
    )
    descriptors = store.descriptors(orchard)
    with store.pin() as pin:
        pin.manifest(orchard)
        with pytest.raises(BuildRefused) as refused:
            build.change_release(store.blobs, base, descriptors, descriptors, holder=pin)
    assert any("holds no absent values" in _said(r) for r in refused.value.refusals)


def _derived(id_: str, input_: str, mapping: dict[str, str]) -> Descriptor:
    entry = {"status": "imported", "by": "importer:grid@1.0.0", "at": "2026-01-01T00:00:00Z"}
    return _DESCRIPTOR.validate_python(
        {
            "kind": "column",
            "id": id_,
            "version": 1,
            "label": id_.split(".")[1],
            "fields": {
                "datatype": "category",
                "derived": {"op": "value_map", "input": input_, "map": mapping},
            },
            "curation": {
                "/label": entry,
                "/fields/datatype": entry,
                "/fields/derived": entry,
            },
        }
    )


def test_a_chained_derived_map_of_an_absent_value_is_refused(
    importing: Importing, grid: Grid
) -> None:
    """R8: a derived column whose input chain reaches the value column maps no absent value."""

    def change(result: ImportResult) -> ImportResult:
        first = _derived("yields.call", "grade", {"A": "a", "B": "b"})
        second = _derived("yields.recall", "call", {"0": "none", "a": "x"})
        return replace(result, descriptors=[*result.descriptors, first, second])

    refusals = _refused(importing, grid, change)
    assert any((r.path or "").endswith("/fields/derived/map") for r in refusals)


def _integer_keys(result: ImportResult) -> ImportResult:
    for column in ("trees", "yields", "tree_groups", "pickings", "picked"):
        result = _set(f"{column}.tree_id", "datatype", "integer")(result)
    return result


def test_integer_key_columns_are_refused(importing: Importing, grid: Grid) -> None:
    """R17: the key columns C compares are text, so ``01`` and ``1`` never merge (rule 4(e))."""
    numbered = {
        "trees": (("1", "apple"), ("2", "pear")),
        "header": ("1", "2"),
        "grades": (("autumn", "0", "B"), ("spring", "A", "A")),
        "pickings": (("1", "ann", "bob"),),
    }
    registry = grid.with_change(_integer_keys)
    with pytest.raises(ImportRefused) as refused:
        importing(_orchard(grid, **numbered), registry=registry, pack="grid")
    assert any(
        r.code == "INVALID_VALUE" and (r.path or "").endswith("/fields/parents")
        for r in refused.value.refusals
    )


@pytest.mark.parametrize(
    "edit",
    [
        _setting(
            "yields",
            "/fields/source",
            {"kind": "pack", "name": "other", "original_name": "other"},
        ),
        _setting("yields", "/fields/primary_key", ["season", "tree_id"]),
    ],
    ids=["a source of the same kind", "the key reordered"],
)
def test_the_importer_s_table_fields_are_frozen_whatever_the_value(
    store: Store, orchard: str, edit: dict[str, Any]
) -> None:
    """B6, B7: a change to a reshaped table's ``source`` that keeps its kind, or one that only
    reorders its key, is refused too."""
    codes = _change_codes(store, orchard, edit)
    assert codes
    assert set(codes) <= {"COLUMNS_CHANGED", "INVALID_VALUE"}


def test_a_reimport_notes_and_drops_the_tombstones_of_the_importer_s_fields(
    roots: Roots, lifecycle: Lifecycle, grid: Grid, store: Store
) -> None:
    """RS10, RS11: when a pack first reshapes a table, the operator's removal of its key is no
    longer kept (its tombstone goes), and each field the import changed has a ``reimported``
    note."""
    path = _orchard(grid)
    lifecycle.import_(
        path, registry=grid.with_change(lambda r: replace(r, reshaped={})), pack="grid"
    )
    opened = open_session(store, "d", ADA)
    request = ChangeRequest.model_validate(
        {"edits": [{"op": "remove", "descriptor": "yields", "pointer": "/fields/primary_key"}]}
    )
    draft = change(store, "d", opened.handle, opened.draft, request, ADA)
    publish(store, "d", opened.handle, draft, ADA)
    assert any(t.pointer == "/fields/primary_key" for t in store.tombstones(draft))
    published = lifecycle.reimport(path, registry=grid.registry, pack="grid")
    kept = store.tombstones(published.manifest)
    assert not any((t.descriptor, t.pointer) == ("yields", "/fields/primary_key") for t in kept)
    notes = {(n.kind, n.subject) for n in published.notes}
    assert ("reimported", "yields/fields/primary_key") in notes
    assert ("reimported", "yields.grade/fields/absent") in notes


def test_a_reimport_that_no_longer_lays_out_a_reshaped_table_is_not_refused_for_it(
    roots: Roots, lifecycle: Lifecycle, grid: Grid, store: Store
) -> None:
    """RS7: ``undeclared`` holds only a table the new import lays out again."""
    path = _orchard(grid)
    lifecycle.import_(path, registry=grid.registry, pack="grid")
    published = lifecycle.reimport(path, registry=grid.with_change(_without_yields), pack="grid")
    manifest = store.manifest(published.manifest)
    assert manifest.table("yields") is None
    assert all(entry.reshaped is None for entry in manifest.tables)


def _without_yields(result: ImportResult) -> ImportResult:
    gone = ("yields", "rel:yields", "cov:yields")
    return replace(
        result,
        sources={name: s for name, s in result.sources.items() if name != "yields"},
        layouts={t: laid for t, laid in result.layouts.items() if t != "yields"},
        descriptors=[d for d in result.descriptors if not d.id.startswith(gone)],
        reshaped={},
    )


def test_the_declared_cells_count_toward_import_cells(roots: Roots, grid: Grid) -> None:
    """m3, m4 (round 3): the cells a pack dropped are cells of the matrix it read, so they count
    toward ``import_cells`` with the rest of the import's, at the limit and one past it."""
    from aibi.core.importers.checks import checked

    result = grid.importer.import_source(roots.confinement.confine(_orchard(grid)), roots.options())
    sources = list(result.sources.values())
    assert all(isinstance(source, TypedSource) for source in sources)
    counted = sum(
        len(cast(TypedSource, s).columns) * max(1, len(cast(TypedSource, s).rows)) for s in sources
    )
    declared = replace(result.reshaped["yields"], empty=0)
    only_dropped = replace(result, reshaped={"yields": declared})
    total = counted + declared.dropped
    checked(only_dropped, ImportLimits(import_cells=total))
    with pytest.raises(ImportRefused) as refused:
        checked(only_dropped, ImportLimits(import_cells=total - 1))
    [refusal] = refused.value.refusals
    assert refusal.limit is not None
    assert refusal.limit.name == "import_cells"


def test_a_validator_sees_the_declaration_and_changes_nothing_of_it(
    importing: Importing, grid: Grid, store: Store
) -> None:
    """The validator's copy (D385) carries the declaration, made anew: a validator that writes
    through ``object.__setattr__`` changes nothing the import keeps."""
    seen: list[Any] = []

    class _Writing:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def validate_source(self, source: Any, result: ImportResult) -> Any:
            seen.append(dict(result.reshaped))
            for declaration in result.reshaped.values():
                object.__setattr__(declaration, "dropped", 0)
                object.__setattr__(declaration, "digest", "0" * 64)
            return []

        def validate_descriptors(self, release: Any) -> Any:
            return []

    from dataclasses import replace as replaced

    import aibi
    from aibi.core.schema.pack_api import PackRegistry

    pack = replaced(grid.pack, validator=_Writing(None))
    registry = PackRegistry([pack], core_version=aibi.__version__)
    imported = importing(_orchard(grid), registry=registry, pack="grid")
    assert [set(found) for found in seen] == [{"yields"}]
    entry = store.manifest(imported.built.manifest.hash).table("yields")
    assert entry is not None
    assert entry.reshaped is not None
    assert entry.reshaped.dropped == 3


def test_a_declaration_whose_fields_hold_a_key_of_the_pack_s_is_pack_failed(
    importing: Importing, grid: Grid
) -> None:
    """D385's rule for every object the pack builds: a declaration's fields are read only from a
    plain instance dictionary of exact ``str`` keys, so no key's comparison runs in the checks."""
    compared: list[str] = []

    class _Key:
        def __hash__(self) -> int:
            return hash("column")

        def __eq__(self, other: object) -> bool:
            compared.append("eq")
            return False

    def change(result: ImportResult) -> ImportResult:
        declared = result.reshaped["yields"]
        given = vars(declared)
        fields: dict[object, object] = {_Key(): given["column"]}
        fields.update(given)
        object.__setattr__(declared, "__dict__", fields)
        compared.clear()
        return result

    [refusal] = _refused(importing, grid, change)
    assert refusal.code == "PACK_FAILED"
    assert compared == []


def test_a_reimport_after_the_check_s_version_changes_is_no_change(
    roots: Roots, lifecycle: Lifecycle, grid: Grid, monkeypatch: pytest.MonkeyPatch
) -> None:
    """m2 (round 1 after the redesign): a re-import checks the coverage again, so a digest that
    differs only by the check's version is no change of content."""
    from aibi.core.store import gate

    path = _orchard(grid)
    lifecycle.import_(path, registry=grid.registry, pack="grid")
    monkeypatch.setattr(gate, "CHECK_VERSION", "aibi.reshaped-coverage/2")
    with pytest.raises(ImportRefused) as refused:
        lifecycle.reimport(path, registry=grid.registry, pack="grid")
    assert [r.code for r in refused.value.refusals] == ["NO_CHANGE"]


def test_the_digest_holds_a_relationship_s_child_columns_and_a_direct_coverage_s_table(
    importing: Importing, grid: Grid, store: Store
) -> None:
    """m3 (round 1 after the redesign): each part of what the check reads is in the digest on
    its own, whether or not another check would refuse its change first."""
    from aibi.core.schema.descriptors import RelationshipDescriptor
    from aibi.core.store import gate

    manifest = store.manifest(_import(importing, grid, _direct))
    entry = manifest.table("yields")
    assert entry is not None
    assert entry.reshaped is not None
    hashes = {table.id: table.hash for table in manifest.tables}
    descriptors = store.descriptors(manifest.hash)
    expected = gate.declaration_inputs("yields", entry.reshaped, descriptors, hashes)
    moved = {**hashes, "cells": "0" * 64}
    assert gate.declaration_inputs("yields", entry.reshaped, descriptors, moved) != expected
    changed = [
        d.model_copy(update={"fields": d.fields.model_copy(update={"child_columns": ["season"]})})
        if isinstance(d, RelationshipDescriptor) and d.fields.child_table == "yields"
        else d
        for d in descriptors
    ]
    assert gate.declaration_inputs("yields", entry.reshaped, changed, hashes) != expected
