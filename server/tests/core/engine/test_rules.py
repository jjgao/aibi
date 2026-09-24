"""§6.5 and §6.6 rules pinned one at a time, on a chain of four tables where the city fixture
would need a scenario for each: u ← a ← b ← c, each row a child of one in the table before, with
a category ``k`` and an integer ``n`` on every table.

The coverage of a and of b is ``all``. c's is a direct coverage table, ``listed``, naming b rows,
and proposed unless a test says otherwise, so an answer that relies on it carries
``COVERAGE_PROPOSED``."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import Evaluator
from aibi.core.schema.descriptors import Descriptor

Doc = Callable[..., dict[str, Any]]
Runner = Callable[..., Any]
Answer = tuple[str, frozenset[str], frozenset[tuple[str, str]]]

A, B, C = "rel:a.p", "rel:b.p", "rel:c.p"
CHAIN = (("u", None), ("a", "u"), ("b", "a"), ("c", "b"))


def world(
    *,
    coverage: Mapping[str, Mapping[str, Any]] | None = None,
    extra: Sequence[Descriptor] = (),
) -> list[Descriptor]:
    """The chain's descriptors; ``coverage`` replaces the keyword arguments of a relationship's
    coverage descriptor, by id, and a relationship given ``{}`` has none."""
    given = {
        A: {"parents": "all"},
        B: {"parents": "all"},
        C: {
            "parents": {"table": "listed", "parent_columns": {"b": "id"}},
            "statuses": {"parents": "proposed"},
        },
        **(coverage or {}),
    }
    descriptors = [build.dataset()]
    for table, parent in CHAIN:
        descriptors += [
            build.table(table, ["id"]),
            build.column(f"{table}.id", "string"),
            build.column(f"{table}.k", "category"),
            build.column(f"{table}.n", "integer", units="1"),
        ]
        if parent is not None:
            descriptors.append(build.column(f"{table}.p", "string"))
            descriptors.append(build.relationship(table, ["p"], parent, ["id"]))
            options = given[f"rel:{table}.p"]
            if options:
                descriptors.append(build.coverage(f"rel:{table}.p", **options))
    descriptors += [
        build.table("listed", None, role="coverage"),
        build.column("listed.b", "string"),
    ]
    return [*descriptors, *extra]


def rows(**tables: str) -> dict[str, list[dict[str, Any]]]:
    """Rows from a short notation: ``b="b0<a0 k=x, b1<a0"`` is b0 and b1, children of a0, b0
    with k x; ``n=`` takes an integer, and ``listed="b1 b2"`` lists b rows for c."""
    found: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in CHAIN}
    for table, written in tables.items():
        if table == "listed":
            found["listed"] = [{"b": name} for name in written.split()]
            continue
        for item in (part.strip() for part in written.split(",")):
            head, *members = item.split()
            name, _, parent = head.partition("<")
            row: dict[str, Any] = {"id": name}
            if parent:
                row["p"] = None if parent == "-" else parent
            for member in members:
                key, _, text = member.partition("=")
                row[key] = int(text) if key == "n" else text
            found[table].append(row)
    return found


def down(rel: str) -> dict[str, str]:
    return {"rel": rel, "dir": "down"}


def up(rel: str) -> dict[str, str]:
    return {"rel": rel, "dir": "up"}


def value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


def answers(run: Runner, doc: Doc, release: Release, clause: Any, unit: str) -> list[Answer]:
    """The answer for each row of the unit table, with each flag's relationship."""
    result = run(doc([clause], unit=unit), release)
    assert result.refusals == []
    return [
        (
            item.value.value,
            frozenset(reason.value for reason in item.reasons),
            frozenset((mark.flag.value, mark.relationship) for mark in item.marks),
        )
        for item in result.result.values
    ]


def true(*flags: tuple[str, str]) -> Answer:
    return ("TRUE", frozenset(), frozenset(flags))


def false(*flags: tuple[str, str]) -> Answer:
    return ("FALSE", frozenset(), frozenset(flags))


def unknown(*reasons: str, flags: Sequence[tuple[str, str]] = ()) -> Answer:
    return ("UNKNOWN", frozenset(reasons), frozenset(flags))


def proposed(rel: str) -> tuple[str, str]:
    return ("COVERAGE_PROPOSED", rel)


# --- Children and flags (§6.5, steps 3 and 7) -----------------------------------------------------


def test_a_child_is_dropped_only_when_every_reason_it_has_is_dropped(run: Runner, doc: Doc) -> None:
    """b0 is out of c's scope, and its n is missing: UNKNOWN for both reasons, so strict keeps
    it, and a0 is UNKNOWN for both rather than NOT_COVERED."""
    scope = value("b.k", values=["x"])
    release = build.release(
        world(coverage={C: {"parents": "all", "parent_scope": scope}}),
        rows(u="u0", a="a0<u0", b="b0<a0 k=y"),
    )
    question = {
        "kind": "exists",
        "table": "b",
        "where": [{"kind": "exists", "table": "c"}, value("b.n", range={"gt": 1})],
    }
    assert answers(run, doc, release, question, "a") == [unknown("OUT_OF_SCOPE", "NO_INFORMATION")]


def test_a_dropped_child_s_flags_are_carried_by_a_false_and_by_an_unknown(
    run: Runner, doc: Doc
) -> None:
    """Under assessed, b0 and b3, unlisted for c, are dropped with c's flag; b1 and b4 each have
    a c row, so the answers for them carry no flag of their own, and b2's k is missing."""
    release = build.release(
        world(),
        rows(
            u="u0",
            a="a0<u0, a1<u0",
            b="b0<a0 k=x, b1<a0 k=x, b3<a1 k=x, b4<a1 k=x, b2<a1",
            c="c1<b1 k=x, c4<b4 k=x",
            listed="b1 b2 b4",
        ),
    )
    no_x = {"not": {"kind": "exists", "table": "c", "where": [value("c.k", values=["x"])]}}
    question = {
        "kind": "exists",
        "table": "b",
        "lift": "assessed",
        "where": [value("b.k", values=["x"]), no_x],
    }
    assert answers(run, doc, release, question, "a") == [
        false(proposed(C)),
        unknown("NO_INFORMATION", flags=[proposed(C)]),
    ]


def test_a_true_from_some_carries_its_true_children_s_flags_only(run: Runner, doc: Doc) -> None:
    """b1 has no c row and is listed, so FALSE with c's flag; b0 has one."""
    release = build.release(
        world(), rows(u="u0", a="a0<u0", b="b0<a0, b1<a0", c="c0<b0", listed="b0 b1")
    )
    question = {"kind": "exists", "table": "b", "where": [{"kind": "exists", "table": "c"}]}
    assert answers(run, doc, release, question, "a") == [true()]


def test_an_unknown_carries_coverage_proposed_only_when_a_closedness_reason_was_added(
    run: Runner, doc: Doc
) -> None:
    """b0 is listed, and its c row's k is missing; b1 is not listed and has no c row."""
    release = build.release(
        world(), rows(u="u0", a="a0<u0", b="b0<a0, b1<a0", c="c0<b0", listed="b0")
    )
    question = {"kind": "exists", "table": "c", "where": [value("c.k", values=["x"])]}
    assert answers(run, doc, release, question, "b") == [
        unknown("NO_INFORMATION"),
        unknown("NOT_COVERED", flags=[proposed(C)]),
    ]


def test_a_null_key_before_a_down_step_has_no_parent(run: Runner, doc: Doc) -> None:
    release = build.release(world(), rows(u="u0", a="a0<u0", b="b0<a0, b1<-"))
    siblings = {"kind": "exists", "table": "b", "via": [up(B), down(B)]}
    assert answers(run, doc, release, siblings, "b") == [true(), unknown("NO_PARENT")]


# --- Canonical chains (§7.6) ----------------------------------------------------------------------


def test_a_leaf_s_lift_applies_to_every_intermediate_question_of_its_chain(
    run: Runner, doc: Doc
) -> None:
    """u0's only b rows: b0, unlisted for c, and b1, listed without c rows. Under assessed each
    of the two intermediate questions drops what is not covered, so u0 is FALSE; under strict
    b0 keeps it UNKNOWN."""
    release = build.release(
        world(coverage={C: {"parents": {"table": "listed", "parent_columns": {"b": "id"}}}}),
        rows(u="u0", a="a0<u0", b="b0<a0, b1<a0", listed="b1"),
    )
    for lift, expected in (("assessed", false()), ("strict", unknown("NOT_COVERED"))):
        leaf = value("c.k", values=["x"], lift=lift)
        assert answers(run, doc, release, leaf, "u") == [expected]


def test_exclude_self_leaves_out_the_start_row_at_the_first_down_step_only(
    run: Runner, doc: Doc
) -> None:
    """The other b rows of a0 (b1 for b0), each with at least two b rows beside it, itself
    included."""
    release = build.release(world(), rows(u="u0", a="a0<u0", b="b0<a0, b1<a0"))
    leaf = {
        "kind": "exists",
        "table": "b",
        "via": [up(B), down(B), up(B), down(B)],
        "quantifier": ["some", {"some": 2}],
        "exclude_self": True,
    }
    assert answers(run, doc, release, leaf, "b") == [true(), true()]


def test_trailing_lookups_are_made_first_in_the_questions_of_the_where(
    run: Runner, doc: Doc
) -> None:
    """From each a row, its h row has at least two a rows: the nested question starts at the a
    row and looks the h row up first (§7.6, step 5)."""
    extra = [
        build.table("h", ["id"]),
        build.column("h.id", "string"),
        build.column("a.h", "string"),
        build.relationship("a", ["h"], "h", ["id"]),
        build.coverage("rel:a.h", "all"),
    ]
    release = build.release(
        world(extra=extra),
        {
            **rows(u="u0, u1"),
            "h": [{"id": "h0"}, {"id": "h1"}],
            "a": [{"id": "a0", "p": "u0", "h": "h1"}, {"id": "a1", "p": "u1", "h": "h1"}],
        },
    )
    shared = {"kind": "exists", "table": "a", "via": [down("rel:a.h")], "quantifier": {"some": 2}}
    leaf = {"kind": "exists", "table": "h", "via": [down(A), up("rel:a.h")], "where": [shared]}
    assert answers(run, doc, release, leaf, "u") == [true(), true()]


# --- covered (§6.5) -------------------------------------------------------------------------------


def test_covered_carries_coverage_proposed_on_true_and_false(run: Runner, doc: Doc) -> None:
    release = build.release(world(), rows(u="u0", a="a0<u0", b="b0<a0, b1<a0", listed="b1"))
    covered = {"kind": "covered", "table": "c"}
    assert answers(run, doc, release, covered, "b") == [
        false(proposed(C)),
        true(proposed(C)),
    ]


def test_an_assessed_covered_carries_the_flags_of_each_step_s_coverage(
    run: Runner, doc: Doc
) -> None:
    """a0's b rows are proposed coverage, and b1 is listed for c: TRUE with both flags."""
    options = {B: {"parents": "all", "statuses": {"parents": "proposed"}}}
    release = build.release(
        world(coverage=options), rows(u="u0", a="a0<u0", b="b0<a0, b1<a0", listed="b1")
    )
    covered = {"kind": "covered", "table": "c", "via": [down(B), down(C)], "lift": "assessed"}
    assert answers(run, doc, release, covered, "a") == [true(proposed(B), proposed(C))]


def test_an_assessed_covered_is_false_only_where_the_step_is_closed(run: Runner, doc: Doc) -> None:
    """b's coverage is undeclared, so a0, whose b rows are all unlisted for c, is not FALSE."""
    release = build.release(world(coverage={B: {}}), rows(u="u0", a="a0<u0", b="b0<a0"))
    covered = {"kind": "covered", "table": "c", "via": [down(B), down(C)], "lift": "assessed"}
    assert answers(run, doc, release, covered, "a") == [unknown("NO_INFORMATION")]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, unknown("NOT_COVERED")),
        ({B: {}}, unknown("NOT_COVERED", "NO_INFORMATION")),
    ],
)
def test_an_earlier_step_of_covered_without_children_adds_the_closedness_reason(
    run: Runner, doc: Doc, options: dict[str, Any], expected: Answer
) -> None:
    """a0 has no b rows: UNKNOWN (NOT_COVERED), and, as for every, NO_INFORMATION when b's
    coverage is undeclared (§6.5, step 5)."""
    release = build.release(world(coverage=options), rows(u="u0", a="a0<u0"))
    for lift in ("strict", "assessed"):
        covered = {"kind": "covered", "table": "c", "via": [down(B), down(C)], "lift": lift}
        assert answers(run, doc, release, covered, "a") == [expected]


def test_an_earlier_step_s_parent_scope_applies_to_covered(run: Runner, doc: Doc) -> None:
    scope = value("a.k", values=["x"])
    release = build.release(
        world(coverage={B: {"parents": "all", "parent_scope": scope}}),
        rows(u="u0", a="a0<u0 k=x, a1<u0 k=y", b="b0<a0, b1<a1", listed="b0 b1"),
    )
    covered = {"kind": "covered", "table": "c", "via": [down(B), down(C)]}
    assert answers(run, doc, release, covered, "a") == [
        true(proposed(C)),
        unknown("OUT_OF_SCOPE"),
    ]


# --- lift_differs (§6.6) --------------------------------------------------------------------------


def _lift_differs(run: Runner, doc: Doc, release: Release, clause: Any, unit: str) -> int:
    result = run(doc([clause], unit=unit), release)
    assert result.refusals == []
    return result.result.lift_differs


def test_lift_differs_counts_the_units_whose_value_changes_not_their_reasons(
    run: Runner, doc: Doc
) -> None:
    """a0 is UNKNOWN under both lifts, for other reasons: b0 is unlisted for c, and b2's c row
    has no k."""
    release = build.release(
        world(), rows(u="u0", a="a0<u0", b="b0<a0, b2<a0", c="c2<b2", listed="b2")
    )
    question = {
        "kind": "exists",
        "table": "b",
        "where": [{"kind": "exists", "table": "c", "where": [value("c.k", values=["x"])]}],
    }
    assert answers(run, doc, release, question, "a") == [
        unknown("NOT_COVERED", "NO_INFORMATION", flags=[proposed(C)])
    ]
    assert _lift_differs(run, doc, release, question, "a") == 0


def test_lift_differs_flips_the_lift_of_covered(run: Runner, doc: Doc) -> None:
    """b0 is unlisted and b1 listed: FALSE under strict, TRUE under assessed."""
    release = build.release(world(), rows(u="u0", a="a0<u0", b="b0<a0, b1<a0", listed="b1"))
    covered = {"kind": "covered", "table": "c", "via": [down(B), down(C)]}
    assert answers(run, doc, release, covered, "a") == [false(proposed(C))]
    assert _lift_differs(run, doc, release, covered, "a") == 1


# --- Evaluation cost ------------------------------------------------------------------------------


def test_each_question_is_answered_once_for_each_row(
    run: Runner, doc: Doc, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested questions ask the same question of the same row again and again: the answers are
    kept, so evaluation is not exponential in the nesting (§6.5 decides each answer from the row
    alone)."""
    release = build.release(world(), rows(u="u0", a="a0<u0", b="b0<a0, b1<a0, b2<a0"))
    asked: list[tuple[int, str, int]] = []
    exists = Evaluator._exists  # pyright: ignore[reportPrivateUsage]

    def counted(self: Evaluator, node: Any, table: str, row: int) -> Any:
        asked.append((id(node), table, row))
        return exists(self, node, table, row)

    monkeypatch.setattr(Evaluator, "_exists", counted)
    siblings: Any = {"kind": "exists", "table": "b", "via": [up(B), down(B)]}
    for _ in range(3):
        siblings = {**siblings, "where": [dict(siblings)]}
    assert answers(run, doc, release, siblings, "b") == [true()] * 3
    assert asked
    assert len(asked) == len(set(asked))
