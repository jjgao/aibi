"""Resolution: the canonical tree before sorting (§7.6), and every refusal with its code, path
and alternatives."""

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from itertools import combinations, pairwise
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import Release, ReleaseError
from aibi.core.engine.graph import Graph, Step
from aibi.core.engine.resolve import check_parent_scopes, resolve, typed_constant
from aibi.core.engine.resolved import (
    Bounds,
    RAll,
    RAny,
    RExists,
    RNot,
    RValue,
    Values,
    document,
)
from aibi.core.schema.document import Document
from aibi.core.schema.limits import MAX_CLAUSE_DEPTH, MAX_LEAVES, MAX_PATH_STEPS
from aibi.core.schema.refusals import Refusal

Run = Any
City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Runner = Callable[..., Run]

INSPECTED = "rel:inspections.establishment"
VIOLATIONS = "rel:violations.inspection"
READINGS = "rel:readings.inspection"
OWNER = "rel:establishments.owner"


def value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


def alternatives(refusal: Refusal) -> list[str]:
    found: list[str] = []
    for segment in refusal.alternatives:
        dumped = segment.model_dump()
        found.append(str(dumped.get("data", dumped.get("text"))))
    return found


def said(refusal: Refusal) -> str:
    """A refusal's message as plain text."""
    parts = [segment.model_dump() for segment in refusal.message]
    return "".join(str(part.get("text", part.get("data", ""))) for part in parts)


def refused(run: Runner, city: City, doc: Doc, *clauses: Any, **options: Any) -> Refusal:
    """The one refusal a cohort of ``clauses`` gets."""
    extra = options.pop("extra", ())
    unit = options.pop("unit", "establishments")
    result = run(doc(list(clauses), unit=unit, **options), city(extra=extra))
    assert result.results == {}
    return result.only()


# --- The canonical tree ---------------------------------------------------------------------------


def tree(run: Runner, city: City, doc: Doc, *clauses: Any, unit: str = "establishments") -> Any:
    result = run(doc(list(clauses), unit=unit), city())
    assert result.refusals == []
    return result.resolution.cohorts["c"].tree


def test_names_become_ids_and_forms_become_values_or_ranges(
    run: Runner, city: City, doc: Doc
) -> None:
    assert tree(run, city, doc, value("establishments.seats", op=">=", value=10)) == RValue(
        "establishments.seats", Bounds(gte=10), units="1"
    )
    assert tree(run, city, doc, value("establishments.cuisine", op="!=", value="thai")) == RValue(
        "establishments.cuisine", Values(("thai",)), negate=True
    )
    # not around a single-valued leaf folds into negate, and twice cancels.
    folded = {"not": {"not": {"not": value("establishments.cuisine", values=["thai"])}}}
    assert tree(run, city, doc, folded) == RValue(
        "establishments.cuisine", Values(("thai",)), negate=True
    )


def test_single_members_and_nested_combinators_are_flattened(
    run: Runner, city: City, doc: Doc
) -> None:
    a = value("establishments.cuisine", values=["thai"])
    b = value("establishments.chain", values=[True])
    c = value("establishments.seats", values=[3])
    resolved = tree(run, city, doc, {"any": [a]}, {"all": [b, {"all": [c]}]})
    assert isinstance(resolved, RAll)
    assert [getattr(member, "column", None) for member in resolved.members] == [
        "establishments.cuisine",
        "establishments.chain",
        "establishments.seats",
    ]
    nested = tree(run, city, doc, {"any": [a, {"any": [b, c]}]})
    assert isinstance(nested, RAny)
    assert len(nested.members) == 3


def test_a_lookup_is_a_via_of_up_steps(run: Runner, city: City, doc: Doc) -> None:
    assert tree(run, city, doc, value("owners.region", values=["north"])) == RValue(
        "owners.region", Values(("north",)), via=(Step(OWNER, "up"),)
    )


def test_the_direct_multi_step_and_nested_forms_are_one_tree(
    run: Runner, city: City, doc: Doc
) -> None:
    """§6.1: a reference below the unit, the exists over its path and the nested exists."""
    leaf = value("violations.code", values=["pest"])
    direct = tree(run, city, doc, leaf)
    multi = tree(run, city, doc, {"kind": "exists", "table": "violations", "where": [leaf]})
    nested = tree(
        run,
        city,
        doc,
        {
            "kind": "exists",
            "table": "inspections",
            "where": [{"kind": "exists", "table": "violations", "where": [leaf]}],
        },
    )
    assert direct == multi == nested
    assert isinstance(direct, RExists)
    assert (direct.table, direct.via, direct.quantifier, direct.lift) == (
        "inspections",
        (Step(INSPECTED, "down"),),
        "some",
        "strict",
    )
    [inner] = direct.where
    assert isinstance(inner, RExists)
    assert inner.lift is None  # a final question
    assert inner.where == (RValue("violations.code", Values(("pest",))),)


def test_trailing_lookups_move_into_the_last_where(run: Runner, city: City, doc: Doc) -> None:
    """§7.6 step 5: the tier is looked up from each licence, so the question is final."""
    resolved = tree(run, city, doc, value("licence_types.tier", values=[3]))
    assert resolved == RExists(
        "licences",
        (Step("rel:licences.establishment", "down"),),
        "some",
        (
            RValue(
                "licence_types.tier",
                Values((3,)),
                via=(Step("rel:licences.type", "up"),),
                units="1",
            ),
        ),
        min_count=1,
    )


def test_a_quantifier_list_gives_one_quantifier_per_down_step(
    run: Runner, city: City, doc: Doc
) -> None:
    leaf = value("violations.code", values=["pest"], quantifier=["every", {"some": 2}])
    resolved = tree(run, city, doc, leaf)
    assert isinstance(resolved, RExists)
    assert (resolved.quantifier, resolved.min_count) == ("every", None)
    [inner] = resolved.where
    assert isinstance(inner, RExists)
    assert (inner.quantifier, inner.min_count) == ("some", 2)


def test_min_count_goes_on_the_last_down_step(run: Runner, city: City, doc: Doc) -> None:
    leaf = {
        "kind": "exists",
        "table": "violations",
        "quantifier": "some",
        "min_count": 3,
        "where": [value("violations.code", values=["pest"])],
    }
    resolved = tree(run, city, doc, leaf)
    assert isinstance(resolved, RExists)
    assert resolved.min_count == 1
    [inner] = resolved.where
    assert isinstance(inner, RExists)
    assert inner.min_count == 3


def test_cohort_leaves_are_inlined(run: Runner, city: City, doc: Doc) -> None:
    thai = value("establishments.cuisine", values=["thai"])
    seats = value("establishments.seats", op=">", value=10)
    written = doc({"small": [seats], "c": [thai, {"kind": "cohort", "cohort": "small"}]})
    result = run(written, city())
    assert result.refusals == []
    cohort = result.resolution.cohorts["c"]
    assert cohort.clauses == (
        RValue("establishments.cuisine", Values(("thai",))),
        RValue("establishments.seats", Bounds(gt=10), units="1"),
    )
    # The cohort leaf as written maps to the clause it became; the referenced cohort's leaves
    # are in its own map.
    assert cohort.leaves == {("cohorts", "c", "all", 0): {0}, ("cohorts", "c", "all", 1): {1}}
    assert result.resolution.cohorts["small"].leaves == {("cohorts", "small", "all", 0): {0}}


def test_resolving_the_documents_form_again_changes_nothing(
    run: Runner, city: City, doc: Doc
) -> None:
    clauses = [
        value("readings.celsius", range={"lt": 8}, quantifier="every"),
        {"not": value("establishments.tags", values=["vegan"], match="all")},
        value("establishments.frontage", range={"gt": 100, "lte": 300}, units="cm"),
        value("establishments.last_seen", op="<", value="2026-01-01T01:00:00+01:00"),
        {"kind": "covered", "table": "violations", "scope": {"code": ["pest"]}, "lift": "assessed"},
    ]
    first = run(doc(clauses), city())
    assert first.refusals == []
    tree_ = first.resolution.cohorts["c"].tree
    members = list(tree_.members) if isinstance(tree_, RAll) else [tree_]
    again = run(doc([document(member) for member in members]), city())
    assert again.refusals == []
    assert again.resolution.cohorts["c"].tree == tree_


def test_unit_keys_are_written_with_the_manifest_hash(run: Runner, city: City, doc: Doc) -> None:
    """§7.6 step 4: `{"dataset": <manifest hash>, "key": [...]}`, in key order and typed."""
    release = city()
    result = run(doc([{"kind": "ids", "ids": ["d:e1", {"dataset": "d", "key": ["e2"]}]}]), release)
    assert document(result.resolution.cohorts["c"].tree) == {
        "kind": "ids",
        "ids": [
            {"dataset": release.manifest, "key": ["e1"]},
            {"dataset": release.manifest, "key": ["e2"]},
        ],
    }


@pytest.mark.parametrize(
    ("given", "datatype", "expected"),
    [
        (3, "integer", 3),
        (3.5, "integer", None),
        (True, "integer", None),
        ("9007199254740993", "integer", 9_007_199_254_740_993),
        ("12", "integer", None),  # within ±(2^53 − 1): written as a number
        (2.5, "number", 2.5),
        ("x", "category", "x"),
        (1, "category", None),
        (False, "boolean", False),
        (0, "boolean", None),
        ("2024-02-29", "date", date(2024, 2, 29)),
        ("2023-02-29", "date", None),
        ("0000-01-01", "date", None),
        ("2026-01-01T00:00:00+01:00", "datetime", datetime(2025, 12, 31, 23, tzinfo=UTC)),
        ("2026-01-01T00:00:00.1234560Z", "datetime", datetime(2026, 1, 1, 0, 0, 0, 123456, UTC)),
        ("2026-01-01T00:00:00.1234567Z", "datetime", None),  # finer than a microsecond
        ("2026-01-01T00:00:00", "datetime", None),  # no offset
        ("2026-01-01T24:00:00Z", "datetime", None),
        ("0001-01-01T00:00:00+01:00", "datetime", None),  # before the first UTC instant
        (str(2**63 - 1), "integer", 2**63 - 1),
        (str(-(2**63)), "integer", -(2**63)),
        (str(2**63), "integer", None),  # beyond 64 bits, as no integer column's values are
        # A number column's values are doubles, and so is a big integer compared with them.
        ("9007199254740993", "number", 9007199254740992.0),
        ("9007199254740993", "time_offset", 9007199254740992.0),
        ("1" + "0" * 308, "number", 1e308),
        ("1" + "0" * 309, "number", None),  # beyond every double
        ("1" + "0" * 400, "number", None),
        ("-" + "9" * 400, "integer", None),
    ],
)
def test_constants_are_typed_by_their_column(given: Any, datatype: str, expected: Any) -> None:
    assert typed_constant(given, datatype) == expected


# --- Refusals -------------------------------------------------------------------------------------


def test_an_unknown_table_lists_the_graphs_tables(run: Runner, city: City, doc: Doc) -> None:
    refusal = refused(run, city, doc, value("inspection.kind", values=["routine"]))
    assert (refusal.code, refusal.path) == ("UNKNOWN_TABLE", "/cohorts/c/all/0/column")
    assert "inspections" in alternatives(refusal)
    assert "checklist_items" not in alternatives(refusal)
    unit = run(doc([], unit="shops"), city())
    assert unit.refusals == [("UNKNOWN_TABLE", "/unit")]


def test_an_unknown_column_lists_the_tables_columns(run: Runner, city: City, doc: Doc) -> None:
    refusal = refused(run, city, doc, value("inspections.kinds", values=["routine"]))
    assert (refusal.code, refusal.path) == ("UNKNOWN_COLUMN", "/cohorts/c/all/0/column")
    assert alternatives(refusal) == [
        "establishment_id",
        "inspection_id",
        "kind",
        "on",
        "rating",
        "score",
    ]


@pytest.mark.parametrize(
    ("leaf", "path"),
    [
        (value("establishments.seats", values=["3"]), "/values/0"),
        (value("establishments.seats", values=[3, 2.5]), "/values/1"),
        (value("establishments.frontage", values=[True]), "/values/0"),
        (value("establishments.chain", values=[1]), "/values/0"),
        (value("establishments.name", op="=", value=3), "/value"),
        (value("establishments.opened", range={"gte": "2026-13-01"}), "/range/gte"),
        (value("establishments.last_seen", op=">", value="2026-01-01T00:00:00"), "/value"),
    ],
)
def test_a_constant_of_the_wrong_type_is_refused(
    run: Runner, city: City, doc: Doc, leaf: dict[str, Any], path: str
) -> None:
    refusal = refused(run, city, doc, leaf)
    assert (refusal.code, refusal.path) == ("INVALID_CONSTANT", "/cohorts/c/all/0" + path)


def test_a_constant_outside_the_permissible_values_lists_them(
    run: Runner, city: City, doc: Doc
) -> None:
    refusal = refused(run, city, doc, value("establishments.cuisine", values=["thai", "sushi"]))
    assert (refusal.code, refusal.path) == ("NOT_PERMISSIBLE", "/cohorts/c/all/0/values/1")
    assert alternatives(refusal) == ["thai", "pizza", "bakery"]
    scope = {"kind": "covered", "table": "violations", "scope": {"code": ["mould"]}}
    refusal = refused(run, city, doc, scope)
    assert (refusal.code, refusal.path) == ("NOT_PERMISSIBLE", "/cohorts/c/all/0/scope/code/0")


@pytest.mark.parametrize(
    ("leaf", "code", "member"),
    [
        (value("establishments.frontage", values=[3], units="g"), "UNITS_UNCONVERTIBLE", "units"),
        (value("establishments.revenue", values=[3], units="1"), "UNITS_UNCONVERTIBLE", "units"),
        (
            value("establishments.cuisine", values=["thai"], units="1"),
            "MEMBER_NOT_APPLICABLE",
            "units",
        ),
        (
            value("establishments.cuisine", values=["thai"], match="all"),
            "MEMBER_NOT_APPLICABLE",
            "match",
        ),
        (value("establishments.name", range={"gt": "a"}), "RANGE_NOT_ALLOWED", "range"),
        (value("establishments.chain", op=">", value=False), "RANGE_NOT_ALLOWED", "op"),
        (value("establishments.cuisine", op="<=", value="thai"), "RANGE_NOT_ALLOWED", "op"),
        (value("establishments.notes", values=["x"]), "UNDECLARED_DATATYPE", "column"),
    ],
)
def test_a_predicate_its_column_cannot_take_is_refused(
    run: Runner, city: City, doc: Doc, leaf: dict[str, Any], code: str, member: str
) -> None:
    refusal = refused(run, city, doc, leaf)
    assert (refusal.code, refusal.path) == (code, f"/cohorts/c/all/0/{member}")


def test_the_scope_column_mentioned_inside_any_is_refused(
    run: Runner, city: City, doc: Doc
) -> None:
    """§6.5: `where: [{any: [code = pest, code = temp]}]` is refused; `code in [pest, temp]`
    is the accepted form."""
    either = {
        "any": [
            value("violations.code", values=["pest"]),
            value("violations.code", values=["temp"]),
        ]
    }
    leaf = {"kind": "exists", "table": "violations", "via": [{"rel": VIOLATIONS, "dir": "down"}]}
    result = run(doc([leaf | {"where": [either]}], unit="inspections"), city())
    assert result.refusals == [
        ("SCOPE_COLUMN_MENTION", "/cohorts/c/all/0/where/0/any/0"),
        ("SCOPE_COLUMN_MENTION", "/cohorts/c/all/0/where/0/any/1"),
    ]
    accepted = leaf | {"where": [value("violations.code", values=["pest", "temp"])]}
    assert run(doc([accepted], unit="inspections"), city()).refusals == []


@pytest.mark.parametrize(
    "where",
    [
        [value("violations.code", values=["pest"], negate=True)],
        [{"not": value("violations.code", values=["pest"])}],
        [value("violations.code", op="!=", value="pest")],
    ],
)
def test_a_scope_column_is_mentioned_only_in_a_top_level_values_conjunct(
    run: Runner, city: City, doc: Doc, where: list[Any]
) -> None:
    leaf = {"kind": "exists", "table": "violations", "where": where}
    assert refused(run, city, doc, leaf, unit="inspections").code == "SCOPE_COLUMN_MENTION"


def test_every_may_not_mention_a_scope_column(run: Runner, city: City, doc: Doc) -> None:
    leaf = {
        "kind": "exists",
        "table": "violations",
        "quantifier": "every",
        "where": [value("violations.code", values=["pest"])],
    }
    refusal = refused(run, city, doc, leaf, unit="inspections")
    assert (refusal.code, refusal.path) == ("SCOPE_COLUMN_MENTION", "/cohorts/c/all/0/where/0")


@pytest.mark.parametrize(
    ("where", "accepted"),
    [
        ([value("complaints.channel", values=["phone"])], True),
        ([value("complaints.channel", values=["phone", "web"])], True),
        ([value("complaints.channel", values=["letter"])], False),
        ([value("complaints.channel", values=["phone"], negate=True)], False),
        ([{"any": [value("complaints.channel", values=["phone"])]}], True),  # flattened
        (
            [
                {
                    "any": [
                        value("complaints.channel", values=["phone"]),
                        value("complaints.severity", values=[1]),
                    ]
                }
            ],
            False,
        ),
    ],
)
def test_a_filtered_column_is_mentioned_only_within_the_filter(
    run: Runner, city: City, doc: Doc, where: list[Any], accepted: bool
) -> None:
    leaf = {"kind": "exists", "table": "complaints", "where": where}
    result = run(doc([leaf]), city())
    if accepted:
        assert result.refusals == []
    else:
        [(code, _)] = result.refusals
        assert code == "FILTER_COLUMN_MENTION"
        assert alternatives(result.only()) == ["phone", "web"]


def test_a_filtered_column_looked_up_on_another_row_is_no_mention(
    run: Runner, city: City, doc: Doc
) -> None:
    """A lookup is about another row, even one of the same table (D205)."""
    previous = build.relationship(
        "complaints", ["previous_id"], "complaints", ["complaint_id"], role="previous"
    )
    extra = [build.column("complaints.previous_id", "string"), previous]
    looked_up = value(
        "complaints.channel", values=["letter"], via=[{"rel": previous.id, "dir": "up"}]
    )
    leaf = {"kind": "exists", "table": "complaints", "where": [looked_up]}
    assert run(doc([leaf]), city(extra=extra)).refusals == []


ID = {"complaints": "complaint_id", "violations": "violation_id"}


@pytest.mark.parametrize(
    ("unit", "table", "mentioned"),
    [
        ("establishments", "complaints", value("complaints.channel", values=["phone"])),
        ("inspections", "violations", value("violations.code", values=["pest"])),
    ],
)
def test_mentions_are_read_after_duplicates_are_removed(
    run: Runner, city: City, doc: Doc, unit: str, table: str, mentioned: dict[str, Any]
) -> None:
    """D205: a where is read for mentions in canonical form, where `any: [x, x]` is the
    top-level conjunct x; a mention that is refused there is refused once, at its first copy
    in the order of the document."""
    question = {"kind": "exists", "table": table}
    twice = question | {"where": [{"any": [mentioned, mentioned]}]}
    assert tree(run, city, doc, twice, unit=unit) == tree(
        run, city, doc, question | {"where": [mentioned]}, unit=unit
    )
    negated = {"not": mentioned}
    result = run(doc([question | {"where": [{"any": [negated, negated]}]}], unit=unit), city())
    [(code, path)] = result.refusals
    assert code.endswith("_COLUMN_MENTION")
    assert path == "/cohorts/c/all/0/where/0/any/0/not"
    # Nine other conjuncts first: the copies sit at 9 and 10, which sort the other way as text.
    others = [value(f"{table}.{ID[table]}", values=[str(index)]) for index in range(9)]
    for copy, at in [(negated, "not"), ({"known": mentioned}, "known")]:
        where = [*others, copy, copy]
        [(_, path)] = run(doc([question | {"where": where}], unit=unit), city()).refusals
        assert path == f"/cohorts/c/all/0/where/9/{at}"


def _two_paths() -> list[Any]:
    """A second way from an establishment to a licence type: its preferred one."""
    return [
        build.column("establishments.preferred_type", "string"),
        build.relationship(
            "establishments", ["preferred_type"], "licence_types", ["type_id"], role="preferred"
        ),
    ]


def test_an_ambiguous_path_lists_the_paths(run: Runner, city: City, doc: Doc) -> None:
    refusal = refused(run, city, doc, value("licence_types.tier", values=[3]), extra=_two_paths())
    assert (refusal.code, refusal.path) == ("AMBIGUOUS_PATH", "/cohorts/c/all/0/column")
    assert alternatives(refusal) == [
        '[{"rel":"rel:establishments.preferred","dir":"up"}]',
        '[{"rel":"rel:licences.establishment","dir":"down"},{"rel":"rel:licences.type","dir":"up"}]',
    ]
    via = [{"rel": "rel:establishments.preferred", "dir": "up"}]
    chosen = run(doc([value("licence_types.tier", values=[3], via=via)]), city(extra=_two_paths()))
    assert chosen.refusals == []


def test_no_path_reaches_a_coverage_table_or_an_island(run: Runner, city: City, doc: Doc) -> None:
    refusal = refused(run, city, doc, value("checklist_items.code", values=["pest"]))
    assert (refusal.code, refusal.path) == ("NO_PATH", "/cohorts/c/all/0/column")
    island = [build.table("weather", ["day"]), build.column("weather.day", "date")]
    refusal = refused(run, city, doc, value("weather.day", values=["2026-01-01"]), extra=island)
    assert (refusal.code, refusal.path) == ("NO_PATH", "/cohorts/c/all/0/column")


def _dense(*, shortcut: bool) -> list[Any]:
    """Twenty tables t00 to t19, each related to every other, and a target ten relationships
    from t18 and from t19: a path to it must reach one of them within six steps, which the
    search, trying the tables in order, does only after all but a few of the others. With
    ``shortcut``, the target is also two relationships from t00, which the search tries
    first (their ids sort first)."""
    names = [f"t{index:02}" for index in range(20)]
    extra: list[Any] = [build.table("target", ["id"]), build.column("target.id", "string")]
    for index, name in enumerate(names):
        extra += [build.table(name, ["id"]), build.column(f"{name}.id", "string")]
        for other in names[index + 1 :]:
            extra.append(build.column(f"{name}.to_{other}", "string"))
            extra.append(build.relationship(name, [f"to_{other}"], other, ["id"]))
    for last in ("t18", "t19"):
        chain = [last, *(f"z{last}_{index}" for index in range(1, 10)), "target"]
        for parent, child in pairwise(chain):
            if child != "target":
                extra += [build.table(child, ["id"]), build.column(f"{child}.id", "string")]
            extra.append(build.column(f"{child}.up_{last}", "string"))
            extra.append(build.relationship(child, [f"up_{last}"], parent, ["id"]))
    if shortcut:
        extra += [build.table("a_one", ["id"]), build.column("a_one.id", "string")]
        extra += [
            build.column("a_one.up", "string"),
            build.relationship("a_one", ["up"], "t00", ["id"]),
        ]
        extra.append(build.column("target.a_one", "string"))
        extra.append(build.relationship("target", ["a_one"], "a_one", ["id"]))
    return extra


@pytest.mark.parametrize("shortcut", [False, True])
def test_a_path_search_that_takes_too_long_is_refused(
    run: Runner, city: City, doc: Doc, shortcut: bool
) -> None:
    """Refused even when the search found one path before it stopped: another may exist."""
    leaf = value("target.id", values=["x"])
    refusal = refused(run, city, doc, leaf, unit="t00", extra=_dense(shortcut=shortcut))
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/cohorts/c/all/0/column")
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("path_search", 100_000)


def test_copies_of_a_leaf_share_one_path_search(
    run: Runner, city: City, doc: Doc, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each written leaf resolves its path, and duplicates collapse only afterwards, so a search
    is done once per release and pair of tables (D212): copies of an expensive leaf cost one."""
    searches: list[tuple[str, str]] = []
    search = Graph._paths  # pyright: ignore[reportPrivateUsage]

    def counted(graph: Graph, source: str, target: str, limit: int) -> Any:
        searches.append((source, target))
        return search(graph, source, target, limit)

    monkeypatch.setattr(Graph, "_paths", counted)
    leaf = value("target.id", values=["x"])
    result = run(doc([leaf] * 50, unit="t00"), city(extra=_dense(shortcut=True)))
    assert len(result.refusals) == 50
    assert searches.count(("t00", "target")) == 1


def _chain(steps: int, *, shortcut: bool = False) -> list[Any]:
    """Tables c00 ← c01 ← … ← c<steps>, each a child of the one before; with ``shortcut``, the
    last is also a child of the first."""
    names = [f"c{index:02}" for index in range(steps + 1)]
    extra: list[Any] = []
    for index, name in enumerate(names):
        extra += [build.table(name, ["id"]), build.column(f"{name}.id", "string")]
        extra.append(build.column(f"{name}.v", "category"))
        if index:
            extra.append(build.column(f"{name}.up", "string"))
            extra.append(build.relationship(name, ["up"], names[index - 1], ["id"]))
    if shortcut:
        extra.append(build.column(f"{names[-1]}.first", "string"))
        extra.append(build.relationship(names[-1], ["first"], names[0], ["id"]))
    return extra


def test_an_implicit_path_has_at_most_sixteen_steps(run: Runner, city: City, doc: Doc) -> None:
    """A path that visits no table twice but is longer than a via may be is no implicit path
    (§6.1): alone it is refused as too long, and beside a short one it is not a second path."""
    leaf = value("c00.v", values=["x"])
    last = f"c{MAX_PATH_STEPS:02}"
    assert run(doc([leaf], unit=last), city(extra=_chain(MAX_PATH_STEPS))).refusals == []
    too_long = f"c{MAX_PATH_STEPS + 1:02}"
    extra = _chain(MAX_PATH_STEPS + 1)
    refusal = refused(run, city, doc, leaf, unit=too_long, extra=extra)
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/cohorts/c/all/0/column")
    assert refusal.limit is not None
    assert (refusal.limit.name, refusal.limit.max) == ("path_steps", MAX_PATH_STEPS)
    result = run(doc([leaf], unit=too_long), city(extra=_chain(MAX_PATH_STEPS + 1, shortcut=True)))
    assert result.refusals == []
    assert result.resolution.cohorts["c"].tree == RValue(
        "c00.v", Values(("x",)), via=(Step(f"rel:{too_long}.first", "up"),)
    )


def _hub(middles: int) -> list[Any]:
    """A hub and a sink, and ``middles`` tables each a child of both."""
    extra: list[Any] = []
    for name in ("hub", "sink"):
        extra += [build.table(name, ["id"]), build.column(f"{name}.id", "string")]
    for index in range(middles):
        name = f"m{index:02}"
        extra += [build.table(name, ["id"]), build.column(f"{name}.id", "string")]
        for parent in ("hub", "sink"):
            extra.append(build.column(f"{name}.{parent}", "string"))
            extra.append(build.relationship(name, [parent], parent, ["id"]))
    return extra


@pytest.mark.parametrize(("middles", "more"), [(64, False), (65, True), (80, True)])
def test_an_ambiguous_path_lists_at_most_64_and_says_when_there_are_more(
    run: Runner, city: City, doc: Doc, middles: int, more: bool
) -> None:
    """The search stops at one path more than a refusal lists, so it cannot count the rest."""
    leaf = value("sink.id", values=["x"])
    refusal = refused(run, city, doc, leaf, unit="hub", extra=_hub(middles))
    assert refusal.code == "AMBIGUOUS_PATH"
    listed = alternatives(refusal)
    assert len(listed) == 64 + more
    assert (listed[-1] == "and more") is more
    assert listed[0] == '[{"rel":"rel:m00.hub","dir":"down"},{"rel":"rel:m00.sink","dir":"up"}]'


@pytest.mark.parametrize(
    ("leaf", "code", "path", "listed"),
    [
        (
            value("owners.region", values=["n"], via=[{"rel": "rel:nothing.x", "dir": "up"}]),
            "UNKNOWN_RELATIONSHIP",
            "/via/0/rel",
            True,
        ),
        (
            value("owners.region", values=["n"], via=[{"rel": INSPECTED, "dir": "up"}]),
            "INVALID_PATH",
            "/via/0",
            True,
        ),
        (
            value("owners.region", values=["n"], via=[{"rel": INSPECTED, "dir": "down"}]),
            "INVALID_PATH",
            "/via",
            False,
        ),
        (
            {"kind": "exists", "table": "owners", "where": [value("owners.region", values=["n"])]},
            "INVALID_PATH",
            "/table",
            False,
        ),
        (
            {
                "kind": "exists",
                "table": "owners",
                "via": [
                    {"rel": INSPECTED, "dir": "down"},
                    {"rel": INSPECTED, "dir": "up"},
                    {"rel": OWNER, "dir": "up"},
                ],
            },
            "INVALID_PATH",
            "/via",
            False,
        ),
        ({"kind": "covered", "table": "owners"}, "INVALID_PATH", "/table", False),
    ],
)
def test_a_path_that_does_not_fit_is_refused(
    run: Runner, city: City, doc: Doc, leaf: dict[str, Any], code: str, path: str, listed: bool
) -> None:
    refusal = refused(run, city, doc, leaf)
    assert (refusal.code, refusal.path) == (code, "/cohorts/c/all/0" + path)
    if listed:
        assert alternatives(refusal) == [
            '[{"rel":"rel:complaints.establishment","dir":"down"}]',
            '[{"rel":"rel:establishments.owner","dir":"up"}]',
            '[{"rel":"rel:inspections.establishment","dir":"down"}]',
            '[{"rel":"rel:licences.establishment","dir":"down"}]',
            '[{"rel":"rel:staff.establishment","dir":"down"}]',
        ]


@pytest.mark.parametrize(
    ("leaf", "steps"),
    [
        (value("violations.code", values=["pest"], quantifier=["some"]), 2),
        (value("violations.code", values=["pest"], quantifier=["some", "every", "some"]), 2),
        ({"kind": "exists", "table": "inspections", "quantifier": ["some", "some"]}, 1),
        # No down step: a list has no entry to give, so any list is refused.
        (value("establishments.cuisine", values=["thai"], quantifier=["every"]), 0),
        (value("owners.region", values=["north"], quantifier=["some", "every"]), 0),
    ],
)
def test_a_quantifier_list_of_the_wrong_length_is_refused(
    run: Runner, city: City, doc: Doc, leaf: dict[str, Any], steps: int
) -> None:
    """A list has exactly one entry per down step of the resolved path, and the refusal shows
    the path (§7.2)."""
    refusal = refused(run, city, doc, leaf)
    assert (refusal.code, refusal.path) == ("QUANTIFIER_MISMATCH", "/cohorts/c/all/0/quantifier")
    counted = {0: "0 down steps", 1: "1 down step", 2: "2 down steps"}[steps]
    entries = {0: "0 entries", 1: "1 entry", 2: "2 entries"}[steps]
    assert said(refusal).startswith(
        f"The resolved path has {counted}, so a quantifier list has {entries}, not"
    )
    assert said(refusal).endswith("]")


def test_a_single_quantifier_on_a_leaf_without_a_down_step_is_vacuous(
    run: Runner, city: City, doc: Doc
) -> None:
    leaf = value("establishments.cuisine", values=["thai"], quantifier="every")
    assert tree(run, city, doc, leaf) == RValue("establishments.cuisine", Values(("thai",)))


def test_exclude_self_needs_a_path_back_into_the_rows_table(
    run: Runner, city: City, doc: Doc
) -> None:
    others = {
        "kind": "exists",
        "table": "inspections",
        "via": [{"rel": INSPECTED, "dir": "up"}, {"rel": INSPECTED, "dir": "down"}],
        "exclude_self": True,
    }
    assert run(doc([others], unit="inspections"), city()).refusals == []
    wrong = {"kind": "exists", "table": "violations", "exclude_self": True}
    refusal = refused(run, city, doc, wrong, unit="inspections")
    assert (refusal.code, refusal.path) == (
        "EXCLUDE_SELF_NOT_ALLOWED",
        "/cohorts/c/all/0/exclude_self",
    )


@pytest.mark.parametrize("member", ["exclude_self", "not"])
def test_exclude_self_is_refused_in_a_where_that_trailing_lookups_serve(
    run: Runner, city: City, doc: Doc, member: str
) -> None:
    """Such a where's questions are asked from the row before the lookups (§7.6, step 5), so
    the row to leave out would be another (D211)."""
    back = [
        {"rel": INSPECTED, "dir": "down"},
        {"rel": VIOLATIONS, "dir": "down"},
        {"rel": VIOLATIONS, "dir": "up"},
    ]
    others: Any = {
        "kind": "exists",
        "table": "inspections",
        "via": [{"rel": INSPECTED, "dir": "up"}, {"rel": INSPECTED, "dir": "down"}],
        "exclude_self": True,
    }
    at = "/cohorts/c/all/0/where/0/exclude_self"
    if member == "not":
        others, at = {"not": others}, "/cohorts/c/all/0/where/0/not/exclude_self"
    leaf = {"kind": "exists", "table": "inspections", "via": back, "where": [others]}
    refusal = refused(run, city, doc, leaf)
    assert (refusal.code, refusal.path) == ("EXCLUDE_SELF_NOT_ALLOWED", at)
    # Asked from each inspection itself, the question may leave it out.
    asked = [{"kind": "exists", "table": "violations"}, others]
    assert (
        run(doc([{"kind": "exists", "table": "inspections", "where": asked}]), city()).refusals
        == []
    )


def test_row_ids_are_refused_where_the_dataset_withholds_them(
    run: Runner, city: City, doc: Doc
) -> None:
    closed = city(allow_row_ids=False)
    for column in ("establishments.establishment_id", "establishments.owner_id"):
        result = run(doc([value(column, values=["x"])]), closed)
        assert result.refusals == [("ROW_IDS_NOT_ALLOWED", "/cohorts/c/all/0/column")]
    result = run(doc([{"kind": "ids", "ids": ["d:e1"]}]), closed)
    assert result.refusals == [("ROW_IDS_NOT_ALLOWED", "/cohorts/c/all/0")]
    # A key column is an identifier (§5.4), even one that no relationship uses.
    result = run(doc([value("violations.violation_id", values=["v0"])], unit="violations"), closed)
    assert result.refusals == [("ROW_IDS_NOT_ALLOWED", "/cohorts/c/all/0/column")]


def _menus() -> list[Any]:
    """A unit with a two-column key."""
    return [
        build.table("menus", ["establishment_id", "number"]),
        build.column("menus.establishment_id", "string"),
        build.column("menus.number", "integer"),
    ]


@pytest.mark.parametrize(
    ("ids", "code", "at"),
    [
        (["d:e1"], "INVALID_KEY", "/ids/0"),
        ([{"dataset": "d", "key": ["e1"]}], "INVALID_KEY", "/ids/0"),
        ([{"dataset": "d", "key": ["e1", "one"]}], "INVALID_KEY", "/ids/0"),
        (
            [{"dataset": "d", "key": ["e1", 1]}, {"dataset": "other", "key": ["e1", 1]}],
            "UNKNOWN_DATASET",
            "/ids/1",
        ),
    ],
)
def test_unit_keys_are_typed_against_the_key(
    run: Runner, city: City, doc: Doc, ids: list[Any], code: str, at: str
) -> None:
    refusal = refused(run, city, doc, {"kind": "ids", "ids": ids}, unit="menus", extra=_menus())
    assert (refusal.code, refusal.path) == (code, "/cohorts/c/all/0" + at)
    if code == "INVALID_KEY":
        assert alternatives(refusal) == ["establishment_id", "number"]


def _keyed(datatype: str | None) -> list[Any]:
    """A unit ``lots`` with a key of one column of ``datatype``."""
    return [build.table("lots", ["lot"]), build.column("lots.lot", datatype)]


@pytest.mark.parametrize(
    ("datatype", "key", "found"),
    [
        ("number", "d:10", True),
        ("number", "d:1e1", True),  # 1e1 and 10.0 are 10, as JSON writes numbers
        ("number", "d:10.0", True),
        ("number", "d:-0.5", False),
        ("integer", "d:10", True),
        ("integer", "d:9223372036854775807", False),
        ("number", "d:1_0", None),  # not a JSON number
        ("number", "d: 10", None),
        ("number", "d:10 ", None),
        ("number", "d:+10", None),
        ("number", "d:010", None),
        ("number", "d:1e400", None),
        ("number", "d:NaN", None),
        ("integer", "d:10.0", None),  # an integer key is written in decimal
        ("integer", "d:9223372036854775808", None),
        ("boolean", "d:true", True),
        ("boolean", "d:false", False),
        ("boolean", "d:True", None),
    ],
)
def test_a_unit_key_in_text_is_typed_as_json_writes_it(
    run: Runner, city: City, doc: Doc, datatype: str, key: str, found: bool | None
) -> None:
    release = city(
        {"lots": [{"lot": 10 if datatype != "boolean" else True}]}, extra=_keyed(datatype)
    )
    result = run(doc([{"kind": "ids", "ids": [key]}], unit="lots"), release)
    if found is None:
        assert result.refusals == [("INVALID_KEY", "/cohorts/c/all/0/ids/0")]
    else:
        assert result.refusals == []
        assert result.result.members == ((0,) if found else ())


@pytest.mark.parametrize("key", ["d:10", {"dataset": "d", "key": [10]}])
def test_a_unit_key_of_an_undeclared_datatype_cannot_be_typed(
    run: Runner, city: City, doc: Doc, key: Any
) -> None:
    refusal = refused(
        run, city, doc, {"kind": "ids", "ids": [key]}, unit="lots", extra=_keyed(None)
    )
    assert (refusal.code, refusal.path) == ("UNDECLARED_DATATYPE", "/cohorts/c/all/0/ids")


def test_a_unit_is_a_keyed_table_of_the_graph(run: Runner, city: City, doc: Doc) -> None:
    assert run(doc([], unit="checklist_items"), city()).refusals == [("INVALID_UNIT", "/unit")]
    unkeyed = [build.table("visits", None, role="event"), build.column("visits.day", "date")]
    assert run(doc([], unit="visits"), city(extra=unkeyed)).refusals == [("INVALID_UNIT", "/unit")]
    # An unknown unit lists the tables that could be one.
    refusal = run(doc([], unit="nowhere"), city(extra=unkeyed)).only()
    assert (refusal.code, refusal.path) == ("UNKNOWN_TABLE", "/unit")
    listed = alternatives(refusal)
    assert "establishments" in listed
    assert "visits" not in listed
    assert "checklist_items" not in listed


def test_a_dataset_without_a_release_is_refused(run: Runner, city: City, doc: Doc) -> None:
    written = doc([]) | {"dataset": "elsewhere"}
    assert run(written, {"d": city()}).refusals == [("UNKNOWN_DATASET", "/dataset")]


def test_one_document_uses_one_release_of_a_dataset(run: Runner, city: City, doc: Doc) -> None:
    written = doc({"a": [], "b": []})
    written["cohorts"]["b"]["dataset"] = "d@2"
    old = city()
    new = build.release(list(old.descriptors), {}, manifest="sha256:" + "1" * 64)
    result = run(written, {"d": old, "d@2": new})
    assert result.refusals == [("MIXED_RELEASES", "/cohorts/b/dataset")]


@pytest.mark.parametrize(
    ("clause", "path"),
    [
        (value("core:age_years", range={"gt": 3}), "/cohorts/c/all/0/column"),
        ({"kind": "exists", "table": "core:person"}, "/cohorts/c/all/0/table"),
    ],
)
def test_what_later_milestones_resolve_is_refused_as_not_supported(
    run: Runner, city: City, doc: Doc, clause: dict[str, Any], path: str
) -> None:
    refusal = refused(run, city, doc, clause)
    assert (refusal.code, refusal.path) == ("NOT_SUPPORTED", path)


def test_a_concept_unit_and_cross_dataset_cohorts_are_not_supported(
    run: Runner, city: City, doc: Doc
) -> None:
    assert run(doc([], unit="core:person"), city()).refusals == [("NOT_SUPPORTED", "/unit")]
    written = doc({"c": []})
    del written["dataset"]
    written["cohorts"]["c"]["datasets"] = ["d", "e"]
    written["unit"] = "core:person"
    result = run(written, {"d": city(), "e": city()})
    assert ("NOT_SUPPORTED", "/cohorts/c/datasets") in result.refusals


def _scope_refusals(release: Release) -> list[tuple[str, str | None]]:
    """``check_parent_scopes``' refusals, with paths from the violations coverage's descriptor."""
    [index] = [
        at
        for at, found in enumerate(release.descriptors)
        if found.id == "cov:violations.inspection"
    ]
    found: list[tuple[str, str | None]] = []
    for refusal in check_parent_scopes(release):
        assert refusal.path is not None
        assert refusal.path.startswith(f"/{index}/fields/parent_scope")
        found.append((refusal.code, refusal.path.removeprefix(f"/{index}/fields")))
    return found


def _asked(run: Runner, doc: Doc, release: Release) -> Refusal:
    """The one refusal a question about violations gets from an inspection."""
    result = run(doc([{"kind": "exists", "table": "violations"}], unit="inspections"), release)
    return result.only()


@pytest.mark.parametrize(
    ("scope", "at"),
    [
        ({"kind": "exists", "table": "readings"}, ""),
        # The scope's own relationship, which resolving the scope would ask about again.
        ({"kind": "exists", "table": "violations"}, ""),
        ({"any": [{"kind": "covered", "table": "readings"}]}, "/any/0"),
        # A column of readings, which hang below inspections as violations do.
        (value("readings.appliance", values=["fridge"]), "/column"),
        # A column of the violations themselves, whose coverage the scope is.
        (value("violations.code", values=["pest"]), "/column"),
        (
            {
                "not": value(
                    "readings.celsius", range={"gt": 5}, via=[{"rel": READINGS, "dir": "down"}]
                )
            },
            "/not/via",
        ),
    ],
)
def test_a_parent_scope_asking_a_question_is_not_supported(
    run: Runner, city: City, doc: Doc, scope: Any, at: str
) -> None:
    """In v1 a parent scope holds value predicates on the parent row or rows it looks up
    (D207); a value leaf below the parent table is a question (§6.1). The release is refused,
    and a document asking about the relationship is refused at the leaf that asks."""
    options = {"violations": {"parents": "all", "parent_scope": scope}}
    with pytest.raises(ReleaseError, match="NOT_SUPPORTED"):
        city(**options)
    release = city(check=False, **options)
    assert _scope_refusals(release) == [("NOT_SUPPORTED", "/parent_scope" + at)]
    refusal = _asked(run, doc, release)
    assert (refusal.code, refusal.path) == ("NOT_SUPPORTED", "/cohorts/c/all/0")
    assert said(refusal).startswith("The parent_scope of cov:violations.inspection does not")


@pytest.mark.parametrize(
    ("scope", "code", "at", "listed"),
    [
        (
            value("inspections.kind", values=["annual"]),
            "NOT_PERMISSIBLE",
            "/values/0",
            ["routine", "follow_up", "courtesy"],
        ),
        (value("inspections.kind", range={"gt": "routine"}), "RANGE_NOT_ALLOWED", "/range", []),
        (
            value("establishments.notes", values=["x"], via=[{"rel": INSPECTED, "dir": "up"}]),
            "UNDECLARED_DATATYPE",
            "/column",
            [],
        ),
    ],
)
def test_a_parent_scope_that_does_not_resolve_is_refused_with_its_cause(
    run: Runner, city: City, doc: Doc, scope: Any, code: str, at: str, listed: list[str]
) -> None:
    options = {"violations": {"parents": "all", "parent_scope": scope}}
    with pytest.raises(ReleaseError, match=code):
        city(**options)
    release = city(check=False, **options)
    assert _scope_refusals(release) == [(code, "/parent_scope" + at)]
    refusal = _asked(run, doc, release)
    assert (refusal.code, refusal.path) == (code, "/cohorts/c/all/0")
    assert f"at /parent_scope{at}: " in said(refusal)
    assert alternatives(refusal) == listed


def test_a_parent_scope_with_several_problems_is_refused_with_its_first(
    run: Runner, city: City, doc: Doc
) -> None:
    scope = {
        "all": [
            value("inspections.kind", values=["annual"]),
            value("inspections.kind", range={"gt": "routine"}),
        ]
    }
    release = city(check=False, violations={"parents": "all", "parent_scope": scope})
    assert _scope_refusals(release) == [
        ("NOT_PERMISSIBLE", "/parent_scope/all/0/values/0"),
        ("RANGE_NOT_ALLOWED", "/parent_scope/all/1/range"),
    ]
    refusal = _asked(run, doc, release)
    assert (refusal.code, refusal.path) == ("NOT_PERMISSIBLE", "/cohorts/c/all/0")
    assert "at /parent_scope/all/0/values/0: " in said(refusal)


def test_a_parent_scope_on_the_parent_row_and_its_lookups_resolves(city: City) -> None:
    scope = {
        "all": [
            value("inspections.kind", values=["routine"]),
            value("owners.region", values=["north"]),
        ]
    }
    assert check_parent_scopes(city(violations={"parents": "all", "parent_scope": scope})) == []


def test_a_cohort_that_uses_a_refused_one_is_left_out_without_its_own_refusal(
    run: Runner, city: City, doc: Doc
) -> None:
    written = doc(
        {
            "bad": [value("establishments.nothing", values=[1])],
            "c": [{"kind": "cohort", "cohort": "bad"}],
            "fine": [value("establishments.cuisine", values=["thai"])],
        }
    )
    result = run(written, city())
    assert result.refusals == [("UNKNOWN_COLUMN", "/cohorts/bad/all/0/column")]
    assert set(result.resolution.cohorts) == {"fine"}


def test_a_refusal_inside_a_parameter_value_points_at_the_reference(
    run: Runner, city: City, doc: Doc
) -> None:
    written = doc([value("establishments.seats", values="$sizes")], params={"sizes": [1, "2"]})
    result = run(written, city())
    refusal = result.only()
    assert (refusal.code, refusal.path) == ("INVALID_CONSTANT", "/cohorts/c/all/0/values")
    assert "sizes" in json_text(refusal)


def json_text(refusal: Refusal) -> str:
    return str(refusal.model_dump()["message"])


def test_scope_columns_of_covered_are_the_coverages(run: Runner, city: City, doc: Doc) -> None:
    leaf = {"kind": "covered", "table": "violations", "scope": {"severity": [1]}}
    refusal = refused(run, city, doc, leaf, unit="inspections")
    assert (refusal.code, refusal.path) == (
        "UNKNOWN_SCOPE_COLUMN",
        "/cohorts/c/all/0/scope/severity",
    )
    assert alternatives(refusal) == ["code"]


def test_not_around_a_question_stays_a_not(run: Runner, city: City, doc: Doc) -> None:
    leaf = {
        "kind": "exists",
        "table": "violations",
        "where": [value("violations.code", values=["pest"])],
    }
    resolved = tree(run, city, doc, {"not": leaf}, unit="inspections")
    assert isinstance(resolved, RNot)
    listed = tree(run, city, doc, {"not": value("establishments.tags", values=["x"])})
    assert isinstance(listed, RNot)  # a list column is multi-valued: negate goes per item


def test_a_via_by_dataset_is_not_supported_in_a_document_built_in_code(city: City) -> None:
    """The loader refuses it for a one-dataset cohort (CROSS_DATASET_ONLY); a document built in
    code reaches resolution."""
    leaf = value("owners.region", values=["n"], via={"d": [{"rel": OWNER, "dir": "up"}]})
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "establishments",
        "cohorts": {"c": {"all": [leaf]}},
    }
    resolution = resolve(Document.model_validate(written), {"d": city()})
    assert [(r.code, r.path) for r in resolution.refusals] == [
        ("NOT_SUPPORTED", "/cohorts/c/all/0/via")
    ]


def test_the_members_of_a_where_are_all_checked_when_one_is_refused(
    run: Runner, city: City, doc: Doc
) -> None:
    """validate_document returns every refusal (§8.6), so a refused member does not hide what is
    wrong with its siblings."""
    where = [
        value("violations.code", values=["pest"], negate=True),
        {"kind": "exists", "table": "nowhere"},
    ]
    leaf = {"kind": "exists", "table": "violations", "where": where}
    result = run(doc([leaf], unit="inspections"), city())
    assert result.refusals == [
        ("SCOPE_COLUMN_MENTION", "/cohorts/c/all/0/where/0"),
        ("UNKNOWN_TABLE", "/cohorts/c/all/0/where/1/table"),
    ]


def test_known_around_a_scope_column_is_no_top_level_values_conjunct(
    run: Runner, city: City, doc: Doc
) -> None:
    where = [{"known": value("violations.code", values=["pest"])}]
    leaf = {"kind": "exists", "table": "violations", "where": where}
    refusal = refused(run, city, doc, leaf, unit="inspections")
    assert (refusal.code, refusal.path) == (
        "SCOPE_COLUMN_MENTION",
        "/cohorts/c/all/0/where/0/known",
    )


def test_an_existence_question_goes_down_a_step(run: Runner, city: City, doc: Doc) -> None:
    here = refused(run, city, doc, {"kind": "exists", "table": "establishments"})
    assert (here.code, here.path) == ("INVALID_PATH", "/cohorts/c/all/0/table")
    assert said(here) == (
        "An existence question goes down at least one step; the table is the one the question "
        "is asked from, establishments"
    )
    # The implicit path to a licence's type ends with a lookup, which needs a where to serve;
    # the refusal points at the table, as no via is written.
    looked_up = refused(run, city, doc, {"kind": "exists", "table": "licence_types"})
    assert (looked_up.code, looked_up.path) == ("INVALID_PATH", "/cohorts/c/all/0/table")


def test_a_constant_too_large_for_any_double_is_invalid(run: Runner, city: City, doc: Doc) -> None:
    huge = "1" + "0" * 400
    leaf = value("establishments.frontage", op=">", value=huge, units="km")
    refusal = refused(run, city, doc, leaf)
    assert (refusal.code, refusal.path) == ("INVALID_CONSTANT", "/cohorts/c/all/0/value")
    leaf = value("establishments.seats", values=[huge])
    refusal = refused(run, city, doc, leaf)
    assert (refusal.code, refusal.path) == ("INVALID_CONSTANT", "/cohorts/c/all/0/values/0")


# --- The caps (§7.1) ------------------------------------------------------------------------------


def _nested(depth: int, leaf: Any, column: str) -> Any:
    """``leaf`` inside ``depth - 1`` combinators, any and all by turns with any outermost, each
    with a value leaf on ``column`` beside it."""
    clause = leaf
    for level in range(depth - 1):
        kind = "any" if (depth - 2 - level) % 2 == 0 else "all"
        clause = {kind: [clause, value(column, values=[f"v{level}"])]}
    return clause


def _limit(refusal: Refusal) -> tuple[str, int]:
    assert refusal.limit is not None
    return refusal.limit.name, refusal.limit.max


def test_a_cohort_nests_clauses_at_most_eight_deep(run: Runner, city: City, doc: Doc) -> None:
    leaf = value("establishments.name", values=["x"])
    deepest = _nested(MAX_CLAUSE_DEPTH, leaf, "establishments.name")
    assert run(doc([deepest]), city()).refusals == []
    refusal = refused(run, city, doc, _nested(MAX_CLAUSE_DEPTH + 1, leaf, "establishments.name"))
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/cohorts/c/all")
    assert _limit(refusal) == ("clause_depth", MAX_CLAUSE_DEPTH)


def test_each_down_step_of_a_path_is_a_level_of_the_depth(
    run: Runner, city: City, doc: Doc
) -> None:
    """From an owner, a violation's code is three questions deep, and the value a fourth."""
    leaf = value("violations.code", values=["pest"])
    within = _nested(MAX_CLAUSE_DEPTH - 3, leaf, "owners.region")
    assert run(doc([within], unit="owners"), city()).refusals == []
    beyond = _nested(MAX_CLAUSE_DEPTH - 2, leaf, "owners.region")
    refusal = refused(run, city, doc, beyond, unit="owners")
    assert _limit(refusal) == ("clause_depth", MAX_CLAUSE_DEPTH)


def _seats(count: int, start: int = 0) -> list[Any]:
    return [value("establishments.seats", values=[index]) for index in range(start, start + count)]


def test_a_cohort_has_at_most_64_leaves(run: Runner, city: City, doc: Doc) -> None:
    assert run(doc(_seats(MAX_LEAVES)), city()).refusals == []
    refusal = refused(run, city, doc, *_seats(MAX_LEAVES + 1))
    assert (refusal.code, refusal.path) == ("LIMIT_EXCEEDED", "/cohorts/c/all")
    assert _limit(refusal) == ("leaves_per_cohort", MAX_LEAVES)


@pytest.mark.parametrize("empty", [{"any": []}, {"not": {"all": []}}])
def test_an_empty_all_or_any_counts_as_a_leaf(
    run: Runner, city: City, doc: Doc, empty: Any
) -> None:
    """A node without children is a leaf for the caps (§7.1, D212)."""
    assert run(doc([*_seats(MAX_LEAVES - 1), empty]), city()).refusals == []
    refusal = refused(run, city, doc, *_seats(MAX_LEAVES), empty)
    assert _limit(refusal) == ("leaves_per_cohort", MAX_LEAVES)


def test_clauses_without_leaves_do_not_escape_the_caps(run: Runner, city: City, doc: Doc) -> None:
    """References multiply distinct clauses of empty combinators as they do any other. Were
    they no leaves, each cohort's form would be bounded by its depth alone, and grow with every
    level of references; the first that references another is past the cap."""
    atoms: list[Any] = [{"all": []}, {"any": []}]
    wrapped = atoms + [{wrapper: atom} for wrapper in ("not", "known", "unknown") for atom in atoms]
    shapes = [{op: [x, y]} for op in ("any", "all") for x, y in combinations(wrapped, 2)]
    cohorts: dict[str, list[Any]] = {"c0": [{"any": shapes[:20]}]}
    for level in (1, 2):
        reference = {"kind": "cohort", "cohort": f"c{level - 1}"}
        cohorts[f"c{level}"] = [{"any": [{"all": [reference, shape]} for shape in shapes]}]
    started = time.perf_counter()
    result = run(doc(cohorts), city())
    assert time.perf_counter() - started < 5
    assert result.refusals == [("LIMIT_EXCEEDED", "/cohorts/c1/all")]
    assert _limit(result.only()) == ("leaves_per_cohort", MAX_LEAVES)


def test_leaves_are_counted_in_the_canonical_form(run: Runner, city: City, doc: Doc) -> None:
    """Inside every where, one per question of a path, duplicates once (§7.6, step 8)."""
    code = value("violations.code", values=["pest"])  # two questions and a value: three leaves
    assert run(doc([*_seats(MAX_LEAVES - 3), code]), city()).refusals == []
    assert _limit(refused(run, city, doc, *_seats(MAX_LEAVES - 2), code)) == (
        "leaves_per_cohort",
        MAX_LEAVES,
    )
    a, b = value("establishments.chain", values=[True]), value("establishments.name", values=["x"])
    twice = [
        *_seats(MAX_LEAVES - 3),
        _seats(1)[0],  # the same leaf again
        {"any": [a, b]},
        {"any": [b, a]},  # the same any, in another order
        {"not": {"all": [a, a]}},  # a with negate, once the all is left with one member
        value("establishments.chain", values=[True], negate=True),
    ]
    assert run(doc(twice), city()).refusals == []


def test_a_where_left_with_an_all_splices_it(run: Runner, city: City, doc: Doc) -> None:
    """The two alls are one once sorted, and the any left with it is unwrapped into the where,
    which splices it (§7.6, steps 6 and 8): the question is two deep, not three."""
    a, b = value("inspections.kind", values=["routine"]), value("inspections.score", values=[1])
    question = {
        "kind": "exists",
        "table": "inspections",
        "where": [{"any": [{"all": [a, b]}, {"all": [b, a]}]}],
    }
    column = "establishments.name"
    assert run(doc([_nested(MAX_CLAUSE_DEPTH - 1, question, column)]), city()).refusals == []
    refusal = refused(run, city, doc, _nested(MAX_CLAUSE_DEPTH, question, column))
    assert _limit(refusal) == ("clause_depth", MAX_CLAUSE_DEPTH)


def test_a_referenced_cohort_s_leaves_count_where_it_is_inlined(
    run: Runner, city: City, doc: Doc
) -> None:
    written = doc(
        {"base": _seats(40), "c": [{"kind": "cohort", "cohort": "base"}, *_seats(30, 100)]}
    )
    result = run(written, city())
    assert result.refusals == [("LIMIT_EXCEEDED", "/cohorts/c/all")]
    assert set(result.resolution.cohorts) == {"base"}


def test_an_empty_referenced_cohort_is_true_where_it_is_inlined(
    run: Runner, city: City, doc: Doc
) -> None:
    """Its leaf maps to the clause it is part of, even with nothing to add to it (§6.6)."""
    written = doc({"base": [], "c": [{"not": {"kind": "cohort", "cohort": "base"}}]})
    rows = {"establishments": [{"establishment_id": "e0"}, {"establishment_id": "e1"}]}
    result = run(written, city(rows))
    cohort = result.resolution.cohorts["c"]
    assert cohort.leaves == {("cohorts", "c", "all", 0, "not"): frozenset({0})}
    assert (result.results["c"].n_false, result.results["base"].n_true) == (2, 2)


# --- Fields read (D208) ---------------------------------------------------------------------------


def _reads(
    run: Runner, city: City, doc: Doc, cohorts: Any, unit: str = "establishments"
) -> set[tuple[str, str, str]]:
    result = run(doc(cohorts, unit=unit), city())
    assert result.refusals == []
    return {
        (read.descriptor, read.pointer, read.status)
        for read in result.resolution.cohorts["c"].fields
    }


def test_the_fields_read_are_those_an_answer_depends_on(run: Runner, city: City, doc: Doc) -> None:
    """An absent field is read where its absence changes the answer."""
    revenue = value("establishments.revenue", range={"gt": 1})
    assert ("establishments.revenue", "/fields/units", "undeclared") in _reads(
        run, city, doc, [revenue]
    )
    staff = {"kind": "exists", "table": "staff"}
    assert ("cov:staff.establishment", "/fields/parents", "undeclared") in _reads(
        run, city, doc, [staff]
    )
    # The parent scope's leaves, the record filter's columns and the unit's key columns.
    violations = {"kind": "exists", "table": "violations"}
    found = _reads(run, city, doc, [violations], unit="inspections")
    assert ("inspections.kind", "/fields/permissible_values", "asserted") in found
    assert ("cov:violations.inspection", "/fields/parent_scope", "asserted") in found
    complaints = {"kind": "exists", "table": "complaints"}
    assert ("complaints.channel", "/fields/datatype", "asserted") in _reads(
        run, city, doc, [complaints]
    )
    ids = {"kind": "ids", "ids": ["d:e0"]}
    assert ("establishments.establishment_id", "/fields/datatype", "asserted") in _reads(
        run, city, doc, [ids]
    )
    # A referenced cohort's reads are the referencing cohort's too.
    inlined = {"base": [revenue], "c": [{"kind": "cohort", "cohort": "base"}]}
    assert ("establishments.revenue", "/fields/units", "undeclared") in _reads(
        run, city, doc, inlined
    )


def test_a_proposed_key_datatype_is_unconfirmed_for_ids(run: Runner, city: City, doc: Doc) -> None:
    proposed = [
        build.table("lots", ["lot"]),
        build.column("lots.lot", "string", status="proposed"),
    ]
    result = run(doc([{"kind": "ids", "ids": ["d:x"]}], unit="lots"), city(extra=proposed))
    assert [
        (read.descriptor, read.pointer) for read in result.resolution.cohorts["c"].unconfirmed
    ] == [("lots.lot", "/fields/datatype")]


# --- Canonical members (§7.6, step 7) -------------------------------------------------------------


def test_each_leaf_is_written_with_exactly_its_canonical_members(
    run: Runner, city: City, doc: Doc
) -> None:
    exists = document(tree(run, city, doc, {"kind": "exists", "table": "inspections"}))
    assert exists == {
        "kind": "exists",
        "table": "inspections",
        "via": [{"rel": INSPECTED, "dir": "down"}],
        "quantifier": "some",
        "min_count": 1,
        "where": [],
    }
    big = document(tree(run, city, doc, value("establishments.seats", values=["9007199254740993"])))
    assert big == {
        "kind": "value",
        "column": "establishments.seats",
        "values": ["9007199254740993"],
        "units": "1",
    }
    # A number column's big integer is the double nearest it, as its values are.
    near: Any = document(
        tree(run, city, doc, value("establishments.frontage", values=["9007199254740993"]))
    )
    assert near["values"] == ["9007199254740992"]
    # covered has a lift exactly when its path has more than one down step.
    one = {"kind": "covered", "table": "readings", "lift": "assessed"}
    written: Any = document(tree(run, city, doc, one, unit="inspections"))
    assert "lift" not in written
    two: Any = document(tree(run, city, doc, {"kind": "covered", "table": "violations"}))
    assert two["lift"] == "strict"


# --- Served wheres, duplicates and cohort references ----------------------------------------------


def _others() -> dict[str, Any]:
    """The other inspections of the same establishment."""
    up_down = [{"rel": INSPECTED, "dir": "up"}, {"rel": INSPECTED, "dir": "down"}]
    return {"kind": "exists", "table": "inspections", "via": up_down, "exclude_self": True}


def _served(*where: Any) -> dict[str, Any]:
    """An inspection's question over its violations, then back to it: the lookup serves where."""
    back = [
        {"rel": INSPECTED, "dir": "up"},
        {"rel": INSPECTED, "dir": "down"},
        {"rel": VIOLATIONS, "dir": "down"},
        {"rel": VIOLATIONS, "dir": "up"},
    ]
    return {"kind": "exists", "table": "inspections", "via": back, "where": list(where)}


@pytest.mark.parametrize(
    "clause",
    [
        # One question below the served where: that question's own start row is left out.
        {
            "kind": "exists",
            "table": "inspections",
            "via": [
                {"rel": INSPECTED, "dir": "down"},
                {"rel": VIOLATIONS, "dir": "down"},
                {"rel": VIOLATIONS, "dir": "up"},
            ],
            "where": [
                {
                    "kind": "exists",
                    "table": "violations",
                    "where": [
                        {
                            "kind": "exists",
                            "table": "violations",
                            "via": [
                                {"rel": VIOLATIONS, "dir": "up"},
                                {"rel": VIOLATIONS, "dir": "down"},
                            ],
                            "exclude_self": True,
                        }
                    ],
                }
            ],
        },
        # A sibling of a served where, in a where no lookup serves.
        {
            "kind": "exists",
            "table": "inspections",
            "where": [_served(value("inspections.kind", values=["routine"])), _others()],
        },
    ],
    ids=["one question below", "a sibling"],
)
def test_exclude_self_beside_or_below_a_served_where_is_allowed(
    run: Runner, city: City, doc: Doc, clause: Any
) -> None:
    assert run(doc([clause]), city()).refusals == []


def test_a_question_s_own_trailing_lookups_leave_its_exclude_self_alone(
    run: Runner, city: City, doc: Doc
) -> None:
    """exclude_self is the question's first down step's; its lookups serve only its where."""
    clause = {**_served(value("inspections.kind", values=["routine"])), "exclude_self": True}
    assert run(doc([clause], unit="inspections"), city()).refusals == []


def test_cohort_references_cost_what_the_canonical_form_does(
    run: Runner, city: City, doc: Doc
) -> None:
    """Each cohort holds 256 references to the one before; written out, the fifth is 256^5
    leaves, but its canonical form is one, which is what resolution keeps and evaluation walks."""
    cohorts: dict[str, list[Any]] = {"c0": [value("establishments.cuisine", values=["thai"])]}
    for level in range(1, 6):
        reference = {"not": {"kind": "cohort", "cohort": f"c{level - 1}"}}
        cohorts[f"c{level}"] = [{"any": [reference] * 256}]
    rows = {"establishments": [{"establishment_id": f"e{i}", "cuisine": "thai"} for i in range(20)]}
    started = time.perf_counter()
    result = run(doc(cohorts), city(rows))
    assert time.perf_counter() - started < 5
    assert result.refusals == []
    assert [result.results[f"c{level}"].n_true for level in range(6)] == [20, 0, 20, 0, 20, 0]
    assert result.resolution.cohorts["c5"].clauses == result.resolution.cohorts["c1"].clauses


def test_duplicates_are_one_clause_holding_every_leaf_they_came_from(
    run: Runner, city: City, doc: Doc
) -> None:
    thai = value("establishments.cuisine", values=["thai"])
    result = run(doc([thai, {"any": [thai, thai]}]), city())
    cohort = result.resolution.cohorts["c"]
    assert len(cohort.clauses) == 1
    assert cohort.leaves == {
        ("cohorts", "c", "all", 0): {0},
        ("cohorts", "c", "all", 1, "any", 0): {0},
        ("cohorts", "c", "all", 1, "any", 1): {0},
    }


def test_a_top_level_reference_to_an_empty_cohort_maps_to_no_clause(
    run: Runner, city: City, doc: Doc
) -> None:
    written = doc(
        {
            "base": [],
            "c": [
                {"kind": "cohort", "cohort": "base"},
                value("establishments.seats", op=">", value=5),
            ],
        }
    )
    cohort = run(written, city()).resolution.cohorts["c"]
    assert cohort.leaves == {("cohorts", "c", "all", 0): set(), ("cohorts", "c", "all", 1): {0}}


def _leaf_map(run: Runner, city: City, doc: Doc, cohorts: Any) -> dict[str, set[int]]:
    """Cohort c's leaf map, by position written as a path below ``/cohorts/c/all``."""
    result = run(doc(cohorts), city())
    assert result.refusals == []
    leaves = result.resolution.cohorts["c"].leaves
    assert all(position[:3] == ("cohorts", "c", "all") for position in leaves)
    return {"/".join(map(str, position[3:])): set(found) for position, found in leaves.items()}


_A = value("establishments.cuisine", values=["thai"])
_B = value("establishments.name", values=["x"])
_C = value("establishments.chain", values=[True])
_D = value("establishments.seats", values=[1])
_BASE = {"kind": "cohort", "cohort": "base"}


@pytest.mark.parametrize(
    ("cohorts", "expected"),
    [
        (
            {"c": [{"any": [{"all": [_A, _B]}, {"all": [_A, _B]}]}]},
            {
                "0/any/0/all/0": {0},
                "0/any/0/all/1": {1},
                "0/any/1/all/0": {0},
                "0/any/1/all/1": {1},
            },
        ),
        (
            {"c": [{"any": [{"all": [_A, {"any": [_B, _C]}]}, {"all": [_A, {"any": [_B, _C]}]}]}]},
            {
                "0/any/0/all/0": {0},
                "0/any/0/all/1/any/0": {1},
                "0/any/0/all/1/any/1": {1},
                "0/any/1/all/0": {0},
                "0/any/1/all/1/any/0": {1},
                "0/any/1/all/1/any/1": {1},
            },
        ),
        (
            {"base": [_A, _B], "c": [{"any": [{"all": [_BASE]}, {"all": [_BASE]}]}]},
            {"0/any/0/all/0": {0, 1}, "0/any/1/all/0": {0, 1}},
        ),
        (
            {"base": [_A, _B], "c": [{"any": [_BASE, _BASE]}]},
            {"0/any/0": {0, 1}, "0/any/1": {0, 1}},
        ),
        (
            {"base": [{"any": [_A, _B]}], "c": [{"any": [_BASE, _C]}, _D]},
            {"0/any/0": {0}, "0/any/1": {0}, "1": {1}},
        ),
        (
            {"base": [], "c": [{"any": [{"all": [_BASE, _A]}, _B]}]},
            {"0/any/0/all/0": {0}, "0/any/0/all/1": {0}, "0/any/1": {0}},
        ),
        (
            {"base": [], "c": [{"all": [_BASE, _A, _B]}]},
            {"0/all/0": {0, 1}, "0/all/1": {0}, "0/all/2": {1}},
        ),
        (
            {"base": [], "c": [{"all": [_BASE, _A, _A]}, _B]},
            {"0/all/0": {0}, "0/all/1": {0}, "0/all/2": {0}, "1": {1}},
        ),
        (
            {"base": [], "c": [{"any": [{"all": [{"any": []}, _BASE]}, _A]}, _B]},
            {"0/any/0/all/1": {0}, "0/any/1": {0}, "1": {1}},
        ),
    ],
)
def test_every_copy_of_a_duplicate_maps_to_the_clauses_its_own_leaves_became(
    run: Runner, city: City, doc: Doc, cohorts: Any, expected: dict[str, set[int]]
) -> None:
    """A kept clause holds the leaves of its duplicates, and so do the members of a kept all,
    which can become top-level clauses; a combinator unwrapped or spliced into its parent gives
    its own leaves to each member it leaves, as a reference does to the clauses of its
    expansion (§6.6), even an empty any that a reference to an empty cohort left alone."""
    assert _leaf_map(run, city, doc, cohorts) == expected


def test_a_cohort_s_leaf_map_holds_its_own_leaves_only(run: Runner, city: City, doc: Doc) -> None:
    """A reference stands for the referenced cohort's leaves, which are in that cohort's map."""
    result = run(doc({"base": [_A, _B], "c": [_BASE, _BASE, _C]}), city())
    assert result.refusals == []
    cohort = result.resolution.cohorts["c"]
    assert cohort.leaves == {
        ("cohorts", "c", "all", 0): {0, 1},
        ("cohorts", "c", "all", 1): {0, 1},
        ("cohorts", "c", "all", 2): {2},
    }
    for clause in cohort.clauses:
        assert all(position[:2] == ("cohorts", "c") for position in clause.origin)


@pytest.mark.parametrize("pairs", [MAX_LEAVES // 2, MAX_LEAVES // 2 + 1])
def test_a_leaf_and_its_negation_are_two_leaves(
    run: Runner, city: City, doc: Doc, pairs: int
) -> None:
    clauses = [
        leaf
        for index in range(pairs)
        for leaf in (
            value("establishments.seats", values=[index]),
            value("establishments.seats", values=[index], negate=True),
        )
    ]
    refusals = run(doc(clauses), city()).refusals
    assert refusals == ([] if pairs * 2 <= MAX_LEAVES else [("LIMIT_EXCEEDED", "/cohorts/c/all")])


def test_a_parent_scope_may_name_an_identifier_where_row_ids_are_withheld(
    run: Runner, city: City, doc: Doc
) -> None:
    """The row-id rule is for documents; a parent scope is written by curators."""
    scope = value("inspections.inspection_id", values=["i0"])
    release = city(allow_row_ids=False, violations={"parents": "all", "parent_scope": scope})
    written = doc([{"kind": "exists", "table": "violations"}], unit="inspections")
    assert run(written, release).refusals == []


def test_the_caps_tell_apart_leaves_that_differ_in_any_member() -> None:
    """Duplicates are the leaves step 8 writes alike: 1 and 1.0 are one constant and true is
    another, while a negation, a lookup, exclude_self or a covered scope makes another leaf
    (§7.6)."""
    from aibi.core.engine.resolved import RCovered, RKnown, measure

    lookup = (Step(OWNER, "up"),)
    one, other = RValue("t.x", Values((1,))), RValue("t.x", Values((1.0,)))
    assert measure((one, other))[1:] == (1, 1)
    for variant in (
        replace(one, negate=True),
        replace(one, via=lookup),
        RValue("t.x", Values((1, 2))),
        RValue("t.x", Values((True,))),
    ):
        assert measure((one, variant))[1:] == (1, 2)
    question = RExists("t", (Step(INSPECTED, "down"),), "some", ())
    assert measure((question, replace(question, exclude_self=True)))[2] == 2
    covered = RCovered("t", (Step(INSPECTED, "down"),))
    scoped = replace(covered, scope=(("code", ("pest",)),))
    assert measure((covered, scoped))[2] == 2
    # known and unknown are clause objects of their own, whose member is deduplicated too.
    assert measure((RKnown(RKnown(RAll((one, other)))),))[1:] == (3, 1)


@pytest.mark.parametrize(
    ("value", "written"),
    [(2**53 - 1, 2**53 - 1), (2**53, "9007199254740992"), (-(2**53 - 1), -(2**53 - 1))],
)
def test_integers_beyond_two_to_the_53_are_written_as_strings(value: int, written: Any) -> None:
    from aibi.core.engine.resolved import constant_json

    assert constant_json(value) == written
    assert constant_json(float(value)) == written


def test_a_number_key_in_text_is_the_nearest_double(run: Runner, city: City, doc: Doc) -> None:
    release = city({"lots": [{"lot": 9007199254740992.0}]}, extra=_keyed("number"))
    result = run(doc([{"kind": "ids", "ids": ["d:9007199254740993"]}], unit="lots"), release)
    assert result.result.members == (0,)
    ids: Any = document(result.resolution.cohorts["c"].tree)
    assert ids["ids"][0]["key"] == ["9007199254740992"]
