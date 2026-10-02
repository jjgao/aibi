"""What ``summary.distribution`` shows of memberships under *k* pinning no count it hides (SPEC
§8.4; D383, D384).

A unit's answers for a column's declared categories are read by the reference evaluator from a
**palette**: a release whose units realise every small pattern of rows the configuration's
descriptors allow (each multiset of up to a few rows over its alphabet, covered or not), each
unit's answers a **type** (per category its truth value and reasons, and whether an answer is
flagged). A world is a multiset of units over the types:

- **A** runs the rule (``disclosure.membership_shown``) over every world, projected to each
  category's TRUE, FALSE and UNKNOWN units and the units UNKNOWN for every category, and finds
  the secrets the outputs that read the same pin to one value from 1 to *k* − 1 that what they
  show does not give; **A2** adds each category's UNKNOWN units by reason, which no output shows;
  **A3** takes any number of categories in the plain model, whose holdings are independent given
  the units' defaults.
- **B** runs ``summarise`` whole over worlds of palette units, their memberships as
  ``materialise_over(…, declared=True)`` gives them, and holds its output to a function of A's
  projection.
- **C** holds the memberships' part of an output beside a column of the unit to its own worlds.
- **D** checks the examples §8.4 quotes, and that where the descriptors bound the rows each unit
  reaches the rule does pin a count, which is why the analysis withholds memberships there.
"""

import itertools
import json
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import pytest

from aibi.core.analyses.disclosure import Split, membership_shown, small
from aibi.core.analyses.distribution import Outcome
from aibi.core.engine import build
from aibi.core.engine.canonical import category_key
from aibi.core.engine.data import Release
from aibi.core.engine.memberships import (
    Answers,
    bounded_rows,
    evaluated,
    fixed,
    materialise_over,
)
from aibi.core.engine.suppression import suppressed
from aibi.core.engine.truth import TruthValue
from aibi.core.schema.descriptors import Descriptor
from aibi.core.schema.refusals import RefusalCode

Check = Callable[..., Any]
Summarised = Callable[..., Outcome]
Answer = tuple[str, tuple[str, ...]]
"""A unit's answer for one category: ``T``, ``F`` or ``U``, and its reasons."""
Type = tuple[tuple[Answer, ...], bool]
"""A unit's answers for each declared category, and whether one is flagged."""

TAGS = "rel:tags.person"
CHECKED = {"table": "checked", "parent_columns": {"person_id": "person_id"}}
EVERYONE = {"kind": "value", "column": "people.kind", "values": ["p"]}


# --- Palettes -------------------------------------------------------------------------------------


def _people(*columns: Descriptor) -> list[Descriptor]:
    return [
        build.dataset(),
        build.table("people", ["person_id"]),
        build.column("people.person_id", "string", identifier=True),
        build.column("people.kind", "category", permissible_values={"values": [{"value": "p"}]}),
        *columns,
    ]


def _tags(
    declared: Sequence[str] = ("a", "b"),
    *,
    datatype: str = "category",
    coverage: Descriptor | None = None,
    extra: Sequence[Descriptor] = (),
    one_to_one: bool = False,
    key: Sequence[str] = ("tag_id",),
) -> list[Descriptor]:
    fields: dict[str, Any] = {}
    if datatype == "category":
        fields = {
            "permissible_values": {"values": [{"value": v} for v in declared]},
            "missing_codes": {"?": "NOT_ASSESSED"},
        }
    return [
        build.table("tags", list(key), role="event"),
        *([build.column("tags.tag_id", "string")] if "tag_id" in key else []),
        build.column("tags.person_id", "string"),
        build.column("tags.tag", datatype, **fields),
        build.relationship("tags", ["person_id"], "people", role="person", one_to_one=one_to_one),
        coverage if coverage is not None else build.coverage(TAGS, CHECKED),
        *(
            [
                build.table("checked", ["person_id"], role="coverage"),
                build.column("checked.person_id", "string"),
            ]
            if coverage is None
            else []
        ),
        *extra,
    ]


def _multisets(alphabet: Sequence[Any], most: int) -> Iterator[tuple[Any, ...]]:
    for size in range(most + 1):
        yield from itertools.combinations_with_replacement(alphabet, size)


@dataclass(frozen=True)
class Palette:
    """A configuration's release of every small pattern and the variable asked of it."""

    release: Release
    variable: dict[str, Any]


def _rows_palette(
    descriptors: list[Descriptor],
    alphabet: Sequence[Mapping[str, Any]],
    rows: int,
    *,
    covered: Sequence[bool] = (False, True),
    people: Sequence[Mapping[str, Any]] = ({},),
    checklists: Sequence[Sequence[str]] | None = None,
    variable: Mapping[str, Any] | None = None,
) -> Palette:
    """Each unit: covered or not, with every multiset of up to ``rows`` rows over ``alphabet``
    (each a row's cells), and each of ``people``' cells of its own; with ``checklists``, the
    values its coverage lists (covered by listing some, of none by listing none)."""
    table: dict[str, list[dict[str, object]]] = {"people": [], "tags": [], "checked": []}
    n = t = 0
    lists = [None] if checklists is None else list(checklists)
    for held in _multisets(range(len(alphabet)), rows):
        for is_covered in covered if checklists is None else (True,):
            for own in people:
                for listed in lists:
                    n += 1
                    person = f"p{n}"
                    table["people"].append({"person_id": person, "kind": "p", **own})
                    if listed is None:
                        if is_covered:
                            table["checked"].append({"person_id": person})
                    else:
                        for value in listed:
                            table["checked"].append({"person_id": person, "tag": value})
                    for at in held:
                        t += 1
                        table["tags"].append(
                            {"tag_id": f"t{t}", "person_id": person, **alphabet[at]}
                        )
    ids = {descriptor.id for descriptor in descriptors}
    if "checked" not in ids:
        del table["checked"]
    if "tags.tag_id" not in ids:
        for row in table["tags"]:
            del row["tag_id"]
    return Palette(
        build.release(descriptors, table),
        dict(variable or {"column": "tags.tag", "each": "category"}),
    )


def _tag(*values: object) -> list[dict[str, object]]:
    return [{"tag": value} for value in values]


def plain(rows: int, declared: Sequence[str] = ("a", "b")) -> Palette:
    """A child table's category, recorded for the people ``checked`` lists (direct coverage),
    with rows of each declared value, an undeclared ``z``, an empty cell and ``?`` (not
    assessed)."""
    return _rows_palette(_people(*_tags(declared)), _tag(*declared, "z", None, "?"), rows)


def three(rows: int) -> Palette:
    """``plain`` with three declared categories."""
    return _rows_palette(_people(*_tags(("a", "b", "c"))), _tag("a", "b", "c", "z", None), rows)


def filtered(rows: int) -> Palette:
    """``plain``, with a record filter on another column of the rows (``kind`` ``x1``), whose
    cell may be unknown or empty, so that a row's category may be UNKNOWN."""
    kinds = build.column(
        "tags.kind",
        "category",
        permissible_values={"values": [{"value": "x1"}]},
        missing_codes={"?": "NOT_ASSESSED"},
    )
    coverage = build.coverage(TAGS, CHECKED, record_filter={"kind": ["x1"]})
    alphabet = [
        {"tag": tag, "kind": kind} for tag in ("a", "b", "z", None, "?") for kind in ("x1", "?")
    ]
    descriptors = _people(
        *_tags(coverage=coverage, extra=[kinds]),
        build.table("checked", ["person_id"], role="coverage"),
        build.column("checked.person_id", "string"),
    )
    return _rows_palette(descriptors, alphabet, rows)


def scoped(rows: int) -> Palette:
    """``plain``, recorded only for the values each person's checklist lists (coverage scoped by
    ``tags.tag``, §5.6), a checklist of each subset of a, b and z."""
    coverage = build.coverage(TAGS, {**CHECKED, "scope_columns": {"tag": "tag"}})
    descriptors = _people(
        *_tags(coverage=coverage),
        build.table("checked", ["person_id", "tag"], role="coverage"),
        build.column("checked.person_id", "string"),
        build.column("checked.tag", "string"),
    )
    lists = [list(chosen) for size in range(4) for chosen in itertools.combinations("abz", size)]
    return _rows_palette(descriptors, _tag("a", "b", "z", None, "?"), rows, checklists=lists)


def parent(rows: int) -> Palette:
    """``plain``, recorded only for people in scope (``people.group`` ``in``, a parent scope):
    each person in scope, out of it or unknown."""
    group = build.column(
        "people.group",
        "category",
        permissible_values={"values": [{"value": v} for v in ("in", "out")]},
        missing_codes={"?": "NOT_ASSESSED"},
    )
    coverage = build.coverage(
        TAGS,
        CHECKED,
        parent_scope={"kind": "value", "column": "people.group", "values": ["in"]},
    )
    descriptors = _people(
        group,
        *_tags(coverage=coverage),
        build.table("checked", ["person_id"], role="coverage"),
        build.column("checked.person_id", "string"),
    )
    people = [{"group": value} for value in ("in", "out", "?")]
    return _rows_palette(descriptors, _tag("a", "b", "z", None, "?"), rows, people=people)


def boolean(rows: int) -> Palette:
    """A child table's boolean, recorded for the people ``checked`` lists."""
    return _rows_palette(_people(*_tags(datatype="boolean")), _tag(True, False, None), rows)


def one_to_one(rows: int) -> Palette:
    """A one-to-one child's category (a, b and c declared), every parent's recorded: each unit
    holds at most one category."""
    del rows
    coverage = build.coverage(TAGS, "all")
    descriptors = _people(*_tags(("a", "b", "c"), coverage=coverage, one_to_one=True))
    return _rows_palette(descriptors, _tag("a", "b", "c", "z", None), 1, covered=(True,))


def keyed(rows: int) -> Palette:
    """A child keyed by its parent's key (its primary key its foreign key), many-to-one as
    declared, recorded for the people ``checked`` lists: each unit holds at most one category."""
    del rows
    descriptors = _people(*_tags(("a", "b", "c"), key=("person_id",)))
    alphabet = _tag("a", "b", "c", "z", None)
    palette = _rows_palette(descriptors, alphabet, 1)
    return palette


def filtered_key(rows: int) -> Palette:
    """A child keyed by its parent's key and a column its coverage's record filter restricts to
    one value (``season`` ``x1``), which the gate enforces: each unit has at most one row, so
    holds at most one category, though the relationship is many-to-one and the key is not the
    foreign key alone (round 1 of #74's review, B1)."""
    del rows
    season = build.column(
        "tags.season", "category", permissible_values={"values": [{"value": "x1"}]}
    )
    coverage = build.coverage(TAGS, CHECKED, record_filter={"season": ["x1"]})
    descriptors = _people(
        *_tags(("a", "b", "c"), coverage=coverage, key=("person_id", "season"), extra=[season]),
        build.table("checked", ["person_id"], role="coverage"),
        build.column("checked.person_id", "string"),
    )
    alphabet = [{"tag": tag, "season": "x1"} for tag in ("a", "b", "c", "z", None)]
    return _rows_palette(descriptors, alphabet, 1)


def boolean_key(rows: int) -> Palette:
    """A child keyed by its parent's key and a boolean (``first``), four categories declared:
    each unit has at most two rows, one of each ``first``, so holds at most two categories
    (round 1 of #74's review, B1)."""
    del rows
    first = build.column("tags.first", "boolean")
    descriptors = _people(*_tags(("a", "b", "c", "d"), key=("person_id", "first"), extra=[first]))
    options: list[object] = ["a", "b", "c", "d", "z", None, "absent"]
    table: dict[str, list[dict[str, object]]] = {"people": [], "tags": [], "checked": []}
    n = 0
    for true_tag in options:
        for false_tag in options:
            for covered in (False, True):
                n += 1
                person = f"p{n}"
                table["people"].append({"person_id": person, "kind": "p"})
                if covered:
                    table["checked"].append({"person_id": person})
                for flag, tag in ((True, true_tag), (False, false_tag)):
                    if tag != "absent":
                        table["tags"].append({"person_id": person, "first": flag, "tag": tag})
    return Palette(build.release(descriptors, table), {"column": "tags.tag", "each": "category"})


def listed(rows: int) -> Palette:
    """A list of categories on the unit (a and b declared), its items each declared value, an
    undeclared ``z`` and ``?``, its cell empty, not assessed or not applicable."""
    column = build.column(
        "people.tags",
        "list<category>",
        permissible_values={"values": [{"value": v} for v in ("a", "b")]},
        missing_codes={"?": "NOT_ASSESSED", "n/a": "NOT_APPLICABLE"},
    )
    cells: list[object] = [None, "?", "n/a"]
    cells += [list(items) for items in _multisets(("a", "b", "z", "?"), rows)]
    table = {
        "people": [
            {"person_id": f"p{n}", "kind": "p", "tags": cell} for n, cell in enumerate(cells)
        ]
    }
    return Palette(
        build.release(_people(column), table), {"column": "people.tags", "each": "category"}
    )


PALETTES: dict[str, tuple[Callable[[int], Palette], int]] = {
    "plain": (plain, 3),
    "three": (three, 3),
    "filtered": (filtered, 3),
    "scoped": (scoped, 3),
    "parent": (parent, 3),
    "boolean": (boolean, 2),
    "list": (listed, 3),
    "one_to_one": (one_to_one, 1),
    "keyed": (keyed, 1),
    "filtered_key": (filtered_key, 1),
    "boolean_key": (boolean_key, 1),
}
"""Each configuration and the rows per unit at which its palette realises every type."""
BOUNDED = ("one_to_one", "keyed", "filtered_key", "boolean_key")
"""The configurations where the descriptors bound the rows each unit reaches, and so the
categories it can hold (``memberships.bounded_rows``)."""
ONE_EACH = ("one_to_one", "keyed", "filtered_key")
"""The bounded configurations where each unit holds at most one category."""


def _document(variable: Mapping[str, Any], *others: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "people",
        "cohorts": {"all": {"all": [EVERYONE]}},
        "views": [
            {
                "analysis": "summary.distribution",
                "cohorts": ["all"],
                "params": {"columns": [dict(variable), *map(dict, others)]},
            }
        ],
    }


def _answer(found: TruthValue) -> Answer:
    truth = "T" if found.is_true else "F" if found.is_false else "U"
    return truth, tuple(sorted(reason.value for reason in found.reasons))


def _type(answers: Answers, row: int) -> Type:
    given = [answers.answer(row, category) for category in answers.fixed]
    return tuple(_answer(one) for one in given), any(one.marks for one in given)


_READ: dict[tuple[str, int], tuple[Palette, Any, Answers, tuple[Type, ...]]] = {}


def _palette(check: Check, name: str, rows: int) -> tuple[Palette, Any, Answers, tuple[Type, ...]]:
    """A configuration's palette, its view checked, its memberships' answers and each unit's
    type, by row of the unit table, read once per session."""
    found = _READ.get((name, rows))
    if found is not None:
        return found
    palette = PALETTES[name][0](rows)
    checked = check(_document(palette.variable), palette.release)
    assert checked.refusals == [], checked.refusals
    [view] = checked.views
    [variable] = view.variables
    answers = evaluated(variable.resolved)
    assert isinstance(answers, Answers)
    types = tuple(_type(answers, row) for row in range(len(answers.defaults)))
    found = _READ[(name, rows)] = (palette, view, answers, types)
    return found


def _types(check: Check, name: str) -> tuple[Type, ...]:
    return tuple(sorted(set(_palette(check, name, PALETTES[name][1])[3])))


# --- A: the rule over worlds ----------------------------------------------------------------------

Vector = tuple[int, ...]


def _projection(kind: Type, *, reasons: Sequence[str] = ()) -> Vector:
    """A type's part of a world's counts: per category its TRUE, FALSE and UNKNOWN (1 or 0) and,
    with ``reasons``, whether it is UNKNOWN under each; then whether it is UNKNOWN for every
    category."""
    found: list[int] = []
    answers, _ = kind
    for truth, why in answers:
        found += [truth == "T", truth == "F", truth == "U"]
        found += [reason in why for reason in reasons]
    found.append(bool(answers) and all(truth == "U" for truth, _ in answers))
    return tuple(int(x) for x in found)


def _worlds(
    types: Sequence[Type], size: int, *, reasons: Sequence[str] = ()
) -> set[tuple[Vector, bool]]:
    """Every world of ``size`` units over ``types``, as its counts and whether a unit's answer is
    flagged, each once."""
    parts = {(_projection(kind, reasons=reasons), kind[1]) for kind in types}
    width = len(next(iter(parts))[0])
    found: set[tuple[Vector, bool]] = {((0,) * width, False)}
    for _ in range(size):
        found = {
            (tuple(a + b for a, b in zip(counts, part, strict=True)), flag or flagged)
            for counts, flag in found
            for part, flagged in parts
        }
    return found


def _rank(rows: list[list[Fraction]]) -> int:
    rows = [list(row) for row in rows]
    rank = 0
    for column in range(len(rows[0]) if rows else 0):
        pivot = next((r for r in range(rank, len(rows)) if rows[r][column] != 0), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        for r in range(len(rows)):
            if r != rank and rows[r][column] != 0:
                factor = rows[r][column] / rows[rank][column]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[rank], strict=True)]
        rank += 1
    return rank


def given(vector: Vector, shown: Sequence[Vector]) -> bool:
    """Whether what is shown gives ``vector`` by a linear combination."""
    rows = [[Fraction(x) for x in row] for row in shown]
    return _rank([*rows, [Fraction(x) for x in vector]]) == _rank(rows)


def _unit(width: int, *at: int) -> Vector:
    return tuple(int(i in at) for i in range(width))


def _pins(
    types: Sequence[Type], size: int, k: int, *, reasons: Sequence[str] = ()
) -> tuple[list[Any], list[Any]]:
    """The counts shown from 1 to *k* − 1 over every world of ``size`` units, and the secrets
    the worlds whose outputs read the same pin to one value from 1 to *k* − 1 that what they
    show, with the size, does not give: per category its TRUE, FALSE and UNKNOWN units, their
    sums by two, and with ``reasons`` its UNKNOWN units under each; and the units UNKNOWN for
    every category."""
    categories = len(types[0][0])
    step = 3 + len(reasons)
    width = categories * step + 1
    sizes = [_unit(width, c * step, c * step + 1, c * step + 2) for c in range(categories)]
    secrets: list[tuple[str, Vector]] = [("none known", _unit(width, width - 1))]
    for c in range(categories):
        t, f, u = c * step, c * step + 1, c * step + 2
        secrets += [
            (f"T{c}", _unit(width, t)),
            (f"F{c}", _unit(width, f)),
            (f"U{c}", _unit(width, u)),
            (f"T+F{c}", _unit(width, t, f)),
            (f"F+U{c}", _unit(width, f, u)),
            (f"T+U{c}", _unit(width, t, u)),
            *((f"{why}{c}", _unit(width, u + 1 + r)) for r, why in enumerate(reasons)),
        ]
    groups: dict[Any, list[Vector]] = defaultdict(list)
    shown_of: dict[Any, list[Vector]] = {}
    small_shown: list[Any] = []
    for counts, flag in _worlds(types, size, reasons=reasons):
        output: list[Any] = [flag]
        shown: list[Vector] = list(sizes)
        for c in range(categories):
            t, f, u = counts[c * step], counts[c * step + 1], counts[c * step + 2]
            seen = membership_shown(True, Split(t, f, u), k)
            output += [t + f if seen.split else None, t if seen.numerator else None]
            if seen.split:
                shown.append(_unit(width, c * step, c * step + 1))
                small_shown += [(counts, "split") for n in (t + f, u) if small(k, n)]
            if seen.numerator:
                shown.append(_unit(width, c * step))
                small_shown += [(counts, "numerator")] if small(k, t) else []
        key = tuple(output)
        groups[key].append(counts)
        shown_of[key] = shown
    pinned: list[Any] = []
    for key, worlds in groups.items():
        for name, vector in secrets:
            values = {sum(x * y for x, y in zip(vector, w, strict=True)) for w in worlds}
            one = next(iter(values))
            if len(values) == 1 and small(k, one) and not given(vector, shown_of[key]):
                pinned.append((size, k, name, one, key))
    return small_shown, pinned


def test_every_palette_realises_every_type_its_descriptors_allow(check: Check) -> None:
    """The palettes' closure: one more row per unit realises no type they do not, so that the
    worlds A and B run over are every world the descriptors allow, not only those within the
    palettes' rows (a unit with two rows, one null, realises a type one row does not)."""
    for name, (_, rows) in PALETTES.items():
        if name in BOUNDED:
            continue
        wider = set(_palette(check, name, rows + 1)[3])
        assert wider == set(_palette(check, name, rows)[3]), name


@pytest.mark.parametrize(
    ("name", "size", "k"),
    [
        *(
            (name, size, k)
            for name in ("plain", "filtered", "scoped", "parent", "boolean", "list")
            for size, k in ((8, 3), (10, 3), (11, 4), (12, 5))
        ),
        ("three", 7, 3),
        ("three", 8, 4),
    ],
)
def test_a_the_rule_pins_no_count_of_a_category_in_any_world(
    check: Check, name: str, size: int, k: int
) -> None:
    """A: over every world of a configuration's types, under *k* from 3 to 5, no count shown is
    from 1 to *k* − 1, and the worlds that read the same (each category's split and TRUE units
    as the rule shows them, and whether an answer is flagged) leave each category's TRUE, FALSE
    and UNKNOWN units, their sums by two and the units UNKNOWN for every category two values or
    more, unless what they show gives them (D383)."""
    small_shown, pinned = _pins(_types(check, name), size, k)
    assert small_shown == []
    assert pinned == []


@pytest.mark.parametrize(("name", "size", "k"), [("plain", 6, 3), ("filtered", 5, 3)])
def test_a2_no_category_s_units_by_reason_are_pinned(
    check: Check, name: str, size: int, k: int
) -> None:
    """A2: with each category's UNKNOWN units under each reason among the secrets, which no
    output shows (its ``excluded`` is always ``null``), none is pinned from 1 to *k* − 1."""
    types = _types(check, name)
    reasons = sorted({why for kind in types for _, whys in kind[0] for why in whys})
    assert reasons
    small_shown, pinned = _pins(types, size, k, reasons=reasons)
    assert small_shown == []
    assert pinned == []


def _any_categories(size: int, k: int) -> list[Any]:
    """A3: in the plain model a unit's answer for a category it holds is TRUE and for any other
    its default, FALSE or UNKNOWN. Given the units whose default is FALSE (``fd``), each
    category's holders among them (``f``) and among the others (``u``) can be chosen apart from
    every other category's, so an output is a product over categories of a function of ``fd``,
    and what several categories add is only the intersection of the ``fd`` each allows. Every
    intersection of those sets, over any number of categories, is checked for each category's
    secrets."""
    pre: dict[tuple[Any, int], list[tuple[int, int, int]]] = defaultdict(list)
    for fd in range(size + 1):
        for u in range(size - fd + 1):
            for f in range(fd + 1):
                t, false, unknown = u + f, fd - f, size - fd - u
                seen = membership_shown(True, Split(t, false, unknown), k)
                output = (t + false if seen.split else None, t if seen.numerator else None)
                pre[(output, fd)].append((t, false, unknown))
    outputs = {output for output, _ in pre}
    allowed = {o: frozenset(fd for (p, fd) in pre if p == o) for o in outputs}
    family = set(allowed.values())
    closure, frontier = set(family), set(family)
    while frontier:
        new = {a & b for a in frontier for b in family if a & b} - closure
        closure |= new
        frontier = new
    pins: list[Any] = []
    for held in closure:
        for output in outputs:
            if not held <= allowed[output]:
                continue
            values: dict[str, set[int]] = defaultdict(set)
            for fd in held:
                for t, false, unknown in pre[(output, fd)]:
                    for name, v in (
                        ("T", t),
                        ("F", false),
                        ("U", unknown),
                        ("T+F", t + false),
                        ("F+U", false + unknown),
                        ("T+U", t + unknown),
                    ):
                        values[name].add(v)
            for name, found in values.items():
                if len(found) == 1 and small(k, next(iter(found))):
                    pins.append((size, k, sorted(held), output, name))
    return pins


@pytest.mark.parametrize("k", [3, 4, 5])
def test_a3_any_number_of_categories_pins_no_count_in_the_plain_model(k: int) -> None:
    """A3: a column may declare up to 150 categories, more than A enumerates; in the plain model,
    where a unit may hold any set of them (no descriptor bounds its rows, which is where D383
    discloses memberships), no number of them pins a category's count, for cohorts of 4 to 20
    units."""
    for size in range(max(k, 4), 21):
        assert _any_categories(size, k) == [], size


# --- B: the whole output --------------------------------------------------------------------------


def _members(representatives: Sequence[int], world: Sequence[int]) -> list[int]:
    return [row for row, times in zip(representatives, world, strict=True) for _ in range(times)]


def _compositions(total: int, parts: int) -> Iterator[tuple[int, ...]]:
    for bars in itertools.combinations(range(total + parts - 1), parts - 1):
        edges = (-1, *bars, total + parts - 1)
        yield tuple(edges[i + 1] - edges[i] - 1 for i in range(parts))


def _dump(outcome: Outcome) -> str:
    return json.dumps(
        [
            [p.model_dump(mode="json") for p in outcome.population],
            [a.model_dump(mode="json") for a in outcome.analysed],
            outcome.values.model_dump(mode="json"),
            [c.model_dump(mode="json") for c in outcome.caveats],
        ],
        sort_keys=True,
    )


def _numbers(value: Any) -> Iterator[int]:
    if isinstance(value, dict):
        for name, inner in value.items():
            if name in ("numerator", "denominator", "n", "n_true", "n_false", "n_unknown"):
                if isinstance(inner, int) and not isinstance(inner, bool):
                    yield inner
            else:
                yield from _numbers(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _numbers(inner)


@pytest.mark.parametrize(("size", "k"), [(6, 3), (8, 4)])
def test_b_the_whole_output_is_a_function_of_what_the_rule_reads(
    check: Check, summarised: Summarised, size: int, k: int
) -> None:
    """B: ``summarise`` over every world of units of the plain palette, of values a, b and an
    undeclared z, covered or not, their memberships as the engines give them under *k*
    (``declared``, D384): the whole output (population, analysed, values, caveats, messages
    included) is a function of each category's TRUE, FALSE and UNKNOWN units and the flags; it
    names no undeclared value nor its key; it shows no count from 1 to *k* − 1; and the
    memberships' ``analysed`` is ``null``."""
    palette = plain(2, ("a", "b"))
    checked = check(_document(palette.variable), palette.release, floor=k)
    assert checked.refusals == []
    [view] = checked.views
    [variable] = view.variables
    answers = evaluated(variable.resolved)
    assert isinstance(answers, Answers)
    rows = palette.release.rows("tags")
    has_null = {
        rows.cell(row, "person_id").value
        for row in range(len(rows))
        if rows.cell(row, "tag").value in (None, "?")
    }
    people = palette.release.rows("people")
    firsts: dict[Type, int] = {}
    for row in range(len(people)):
        if people.cell(row, "person_id").value not in has_null:
            firsts.setdefault(_type(answers, row), row)
    kinds = list(firsts)
    representatives = [firsts[kind] for kind in kinds]
    assert 5 <= len(kinds) <= 8
    undeclared_key = category_key(variable.resolved.question, "z")
    seen: dict[Any, str] = {}
    for world in _compositions(size, len(kinds)):
        members = _members(representatives, world)
        made = materialise_over([variable.resolved], [answers], members, declared=True)
        outcome = summarised(view, [size], [made], k=k)
        dumped = _dump(outcome)
        projection = (
            tuple(
                sum(times * part for times, part in zip(world, column, strict=True))
                for column in zip(*(_projection(kind) for kind in kinds), strict=True)
            ),
            any(kind[1] for kind, times in zip(kinds, world, strict=True) if times),
        )
        assert seen.setdefault(projection, dumped) == dumped, world
        assert '"z"' not in dumped
        assert undeclared_key not in dumped
        assert not any(small(k, n) for n in _numbers(json.loads(dumped))), world
        [analysed] = outcome.analysed
        assert (analysed.n, analysed.excluded_units, analysed.excluded) == (None, None, None)
    assert len(seen) > 1


# --- C: beside a column of the unit ---------------------------------------------------------------


def test_c_memberships_beside_another_table_s_column_depend_on_their_own_worlds_alone(
    check: Check, summarised: Summarised
) -> None:
    """C: memberships beside a category of the unit, which shares no relationship or coverage
    with them: over every world of five units whose memberships and grades combine freely, each
    variable's part of the output (its column, and its entry of ``analysed``' variables) depends
    on its own worlds alone, the memberships' entry is ``null``, and the joint counts are too
    (D383's product argument, as D320's for independent predicates)."""
    grade = build.column(
        "people.grade",
        "category",
        permissible_values={"values": [{"value": v} for v in ("g1", "g2")]},
        missing_codes={"?": "NOT_ASSESSED"},
    )
    palette = _rows_palette(
        _people(grade, *_tags()),
        _tag("a", "b"),
        1,
        people=[{"grade": g} for g in ("g1", "?")],
    )
    k = 3
    checked = check(
        _document(palette.variable, {"column": "people.grade"}), palette.release, floor=k
    )
    assert checked.refusals == []
    [view] = checked.views
    resolved = [variable.resolved for variable in view.variables]
    given = [evaluated(variable) for variable in resolved]
    answers = given[0]
    assert isinstance(answers, Answers)
    people = palette.release.rows("people")
    firsts: dict[tuple[Type, object], int] = {}
    for row in range(len(people)):
        firsts.setdefault((_type(answers, row), people.cell(row, "grade").value), row)
    kinds = list(firsts)
    representatives = [firsts[kind] for kind in kinds]
    parts: list[dict[Any, str]] = [{}, {}]
    for world in _compositions(5, len(kinds)):
        members = _members(representatives, world)
        made = materialise_over(resolved, given, members, declared=True)
        outcome = summarised(view, [5], [made], k=k)
        position = outcome.values.positions[0]
        [analysed] = outcome.analysed
        assert analysed.n is None
        assert analysed.variables is not None
        first, second = analysed.variables
        assert (first.n, first.excluded_units, first.excluded) == (None, None, None)
        for at, own in enumerate((0, 1)):
            sub = [(kind[own], times) for kind, times in zip(kinds, world, strict=True) if times]
            merged: dict[Any, int] = defaultdict(int)
            for part, times in sub:
                merged[part] += times
            key = tuple(sorted(merged.items(), key=repr))
            dumped = json.dumps(
                [
                    position.columns[at].model_dump(mode="json"),
                    (first, second)[at].model_dump(mode="json"),
                ],
                sort_keys=True,
            )
            assert parts[at].setdefault(key, dumped) == dumped, world


# --- D: the examples ------------------------------------------------------------------------------


def test_d_a_numerator_shown_beside_a_hidden_split_is_pinned_by_a_category_no_unit_holds() -> None:
    """Example (a) of §8.4 (D383): 10 units under *k* = 3; a category no unit holds splits them
    as their defaults do, 0, 3 and 7, bounding another's FALSE by 3 and UNKNOWN by 7. Of that
    one's splits with 5 TRUE, 5, f and 5 − f for f from 0 to 3, D320's split shows 5 alone only
    for 5, 3 and 2, which pins its UNKNOWN at 2; alone, 5, 4 and 1 reads the same. The rule of
    D383 shows none of 5, 3 and 2, and whatever it shows of the others leaves the UNKNOWN two
    values."""
    alone = {f: suppressed(5, f, 5 - f, 3) for f in range(4)}
    assert alone[3] == {"n_false", "n_unknown"}
    assert [f for f, hidden in alone.items() if hidden == alone[3]] == [3]
    assert suppressed(5, 4, 1, 3) == alone[3]
    default = membership_shown(True, Split(0, 3, 7), 3)
    assert (default.split, default.numerator) == (True, True)
    shown = {f: membership_shown(True, Split(5, f, 5 - f), 3) for f in range(4)}
    assert (shown[3].split, shown[3].numerator) == (False, False)
    assert (shown[2].split, shown[2].numerator) == (True, False)


def test_d_reason_maps_of_categories_that_share_defaults_are_never_shown(
    check: Check, summarised: Summarised
) -> None:
    """Example (b) of §8.4 (D383): categories whose UNKNOWN units share their defaults' reasons
    would pin one another's reasons through their maps, so under *k* no category's ``excluded``
    is shown, whatever its counts, in a world of the plain palette whose maps D329's rule for a
    variable would show."""
    _, view, answers, types = _palette(check, "plain", 2)
    wanted = [row for row, kind in enumerate(types) if kind[0][0][0] == "U"][:8]
    made = materialise_over([view.variables[0].resolved], [answers], wanted, declared=True)
    outcome = summarised(view, [len(wanted)], [made], k=3)
    rows = outcome.values.positions[0].columns[0].model_dump(mode="json")["categories"]
    assert rows
    assert all(row["proportion"]["excluded"] is None for row in rows)


def test_d_a_cohort_on_the_same_rows_gives_its_own_hidden_count() -> None:
    """Example (c) of §8.4's Limits (D383): of 10 units under *k* = 3, a cohort of the 5 that
    hold a category, all of whose other units are UNKNOWN by default, has its UNKNOWN and FALSE
    units (2 and 3) suppressed as a cohort's count; a category no unit holds shows its split in
    both positions (0 known of the cohort, 3 of every unit), so 7 − 5 gives the cohort's 2. The
    Limits disclaim it, as they do for D297, D320 and D329."""
    assert suppressed(5, 3, 2, 3) >= {"n_false", "n_unknown"}
    every = membership_shown(True, Split(0, 3, 7), 3)
    cohort = membership_shown(True, Split(0, 0, 5), 3)
    assert every.split
    assert cohort.split
    assert 7 - 5 == 2


@pytest.mark.parametrize("name", ONE_EACH)
def test_d_where_each_unit_holds_one_category_the_rule_would_pin_a_count(
    check: Check, name: str
) -> None:
    """Where each unit holds at most one category (a one-to-one child, one keyed by its parent,
    or keyed by its parent and a column its record filter fixes), the TRUE counts sum to at most
    the units, and the rule pins a hidden one: 6 units under *k* = 3, three categories each
    split 1, 2 and 3, each shown as 3 and 3 with its TRUE hidden, give each TRUE 1. So the
    analysis withholds such memberships (D383), which the engine decides from the descriptors
    (``memberships.bounded_rows``)."""
    _, view, _, types = _palette(check, name, 1)
    assert bounded_rows(view.variables[0].resolved)
    _, pinned = _pins(tuple(sorted(set(types))), 6, 3)
    assert pinned


def test_d_where_each_unit_holds_two_of_four_categories_the_rule_would_pin_a_count(
    check: Check,
) -> None:
    """Round 1 of #74's review, B1: a boolean in a child's key beside its parent's gives each
    unit two rows at most, so of four categories the TRUE counts sum to at most twice the units.
    Of 10 units, a held by all, b by 5, d by 4 and c by 1 show 10, 5, hidden and 4 over 10 known
    each; c's TRUE is at most 20 − 19 = 1, and 0 would be shown, so it is 1. Every such split is
    a world of the palette, which the descriptors bound (``memberships.bounded_rows``)."""
    _, view, _, types = _palette(check, "boolean_key", 1)
    assert bounded_rows(view.variables[0].resolved)
    most = max(sum(truth == "T" for truth, _ in kind[0]) for kind in types)
    assert most == 2
    splits = [Split(10, 0, 0), Split(5, 5, 0), Split(1, 9, 0), Split(4, 6, 0)]
    shown = [membership_shown(True, split, 3) for split in splits]
    assert [(one.split, one.numerator) for one in shown] == [
        (True, True),
        (True, True),
        (True, False),
        (True, True),
    ]
    zero = membership_shown(True, Split(0, 10, 0), 3)
    assert (zero.split, zero.numerator) == (True, True)
    assert 2 * 10 - (10 + 5 + 4) == 1


@pytest.mark.parametrize("name", BOUNDED)
def test_d_phase_2_withholds_memberships_whose_rows_per_unit_the_descriptors_bound(
    check: Check, name: str
) -> None:
    """D383: under *k*, memberships where the descriptors bound the rows each unit reaches are
    ``WITHHELD_UNDER_K`` at their ``each``, citing D383 and offering ``some`` and ``every`` with
    ``values``, each of which runs; the bare column's refusal offers no ``each`` there, whereas
    without *k* it does and memberships run."""
    palette = PALETTES[name][0](1)
    document = _document(palette.variable)
    found = check(document, palette.release, floor=3)
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/0/each",
    )
    said = "".join(part.model_dump().get("text", "") for part in refusal.message)
    assert said.endswith("(§8.4, D383)")
    assert "D379" not in said
    offered = [one.text for one in refusal.alternatives or []]
    assert offered == ['aggregate: "some" with values', 'aggregate: "every" with values']
    for name_ in ("some", "every"):
        led = _document({"column": "tags.tag", "aggregate": name_, "values": ["a"]})
        assert check(led, palette.release, floor=3).refusals == [], name_
    assert check(document, palette.release).refusals == []
    bare = _document({"column": "tags.tag"})
    [under_k] = check(bare, palette.release, floor=3).refusals
    assert 'each: "category"' not in [one.text for one in under_k.alternatives or []]
    [without] = check(bare, palette.release).refusals
    assert 'each: "category"' in [one.text for one in without.alternatives or []]


def _keyed_by(
    extra: str, datatype: str, *, foreign: bool = False, filtered: bool = False
) -> Release:
    """A child keyed by its parent's key and ``extra`` (of ``datatype``), which with
    ``foreign`` is a foreign key to a table of places, and with ``filtered`` a column its
    coverage's record filter restricts."""
    fields: dict[str, Any] = (
        {"permissible_values": {"values": [{"value": "x1"}]}} if datatype == "category" else {}
    )
    column = build.column(f"tags.{extra}", datatype, **fields)
    coverage = build.coverage(TAGS, CHECKED, record_filter={extra: ["x1"]} if filtered else None)
    places = (
        [
            build.table("places", [extra]),
            build.column(f"places.{extra}", datatype),
            build.relationship("tags", [extra], "places", role="place"),
        ]
        if foreign
        else []
    )
    descriptors = _people(
        *_tags(("a", "b"), coverage=coverage, key=("person_id", extra), extra=[column, *places]),
        build.table("checked", ["person_id"], role="coverage"),
        build.column("checked.person_id", "string"),
    )
    value: object = {"category": "x1", "string": "x1", "boolean": True}.get(datatype, 1)
    rows: dict[str, list[dict[str, object]]] = {
        "people": [{"person_id": "p1", "kind": "p"}],
        "tags": [{"person_id": "p1", extra: value, "tag": "a"}],
        "checked": [{"person_id": "p1"}],
    }
    if foreign:
        rows["places"] = [{extra: value}]
    return build.release(descriptors, rows)


@pytest.mark.parametrize(
    ("extra", "datatype", "foreign", "filtered", "withheld"),
    [
        ("n", "integer", False, False, False),
        ("season", "category", False, False, False),
        ("season", "category", False, True, True),
        ("first", "boolean", False, False, True),
        ("place_id", "string", True, False, True),
    ],
)
def test_a_key_column_beyond_the_foreign_key_bounds_the_rows_unless_its_values_are_open(
    check: Check, extra: str, datatype: str, foreign: bool, filtered: bool, withheld: bool
) -> None:
    """D383 (round 1 of #74's review, B1): a child keyed by its parent's key and another column
    reaches any number of rows per parent, and its memberships are disclosed under *k*, where
    that column's values are open (an integer, or a category, whose undeclared values the gate
    allows); a boolean (two rows), a column the record filter restricts (as many as it allows)
    or a foreign key (as many as its table's rows) bounds them, and they are withheld."""
    release = _keyed_by(extra, datatype, foreign=foreign, filtered=filtered)
    found = check(_document({"column": "tags.tag", "each": "category"}), release, floor=3)
    if withheld:
        [refusal] = found.refusals
        assert refusal.code == RefusalCode.WITHHELD_UNDER_K
        return
    assert found.refusals == []
    assert not bounded_rows(found.views[0].variables[0].resolved)


def test_d_a_list_on_a_one_to_one_child_holds_several_categories_and_is_not_withheld(
    check: Check,
) -> None:
    """A list's items are several per unit whatever its path, so memberships of a list on a
    one-to-one child are disclosed under *k*, not withheld (``resolve.bounded_rows``)."""
    column = build.column(
        "tags.tag",
        "list<category>",
        permissible_values={"values": [{"value": v} for v in ("a", "b")]},
    )
    descriptors = [
        *_people(),
        build.table("tags", ["tag_id"], role="event"),
        build.column("tags.tag_id", "string"),
        build.column("tags.person_id", "string"),
        column,
        build.relationship("tags", ["person_id"], "people", role="person", one_to_one=True),
        build.coverage(TAGS, "all"),
    ]
    release = build.release(
        descriptors,
        {
            "people": [{"person_id": "p1", "kind": "p"}],
            "tags": [{"tag_id": "t1", "person_id": "p1", "tag": ["a", "b"]}],
        },
    )
    found = check(_document({"column": "tags.tag", "each": "category"}), release, floor=3)
    assert found.refusals == []
    assert not bounded_rows(found.views[0].variables[0].resolved)
    assert fixed(found.views[0].variables[0].resolved) == ("a", "b")


@pytest.mark.parametrize("first", ["count", "each"])
def test_the_first_withheld_member_in_the_columns_order_is_refused(
    check: Check, first: str
) -> None:
    """Round 1 of #74's review, m3: a view with a count of rows and withheld memberships is
    refused at whichever comes first among its columns, the one ``withheld_forms`` withholds or
    the one ``distribution.withheld_under_k`` does."""
    palette = keyed(1)
    rows = {"column": "tags.tag", "count": "rows"}
    columns = [rows, palette.variable] if first == "count" else [palette.variable, rows]
    [refusal] = check(_document(*columns), palette.release, floor=3).refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        f"/views/0/params/columns/0/{first}",
    )


def test_summarise_refuses_memberships_whose_rows_per_unit_the_descriptors_bound(
    check: Check, summarised: Summarised
) -> None:
    """Round 1 of #74's review, m3: were memberships the analysis withholds under *k* to reach
    ``summarise`` (phase 2 refuses them), it raises rather than show them (D383)."""
    _, view, answers, _ = _palette(check, "keyed", 1)
    made = materialise_over([view.variables[0].resolved], [answers], [0, 1, 2], declared=True)
    with pytest.raises(ValueError, match="never summarised"):
        summarised(view, [3], [made], k=3)
