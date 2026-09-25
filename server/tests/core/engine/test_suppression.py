"""The disclosure pass over cohort counts (SPEC §8.4; D297): each rule, and every count the pass
leaves builds a cohort count whose checks hold."""

import itertools
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from aibi.core.engine.canonical import CanonicalCohort, Canonicalisation
from aibi.core.engine.counts import CountParts, count_parts
from aibi.core.engine.data import Release
from aibi.core.engine.suppression import disclosed, suppressed
from aibi.core.engine.truth import Mark
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.output import TextSegment
from aibi.core.schema.results import CohortCount, Disclosure, Issuance, ReleaseRef
from aibi.core.schema.semantics import Flag, Reason

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Canon = Callable[..., Canonicalisation]
Runner = Callable[..., Any]

ISSUANCE = "iss:01J8Z3S4T5V6W7X8Y9ZABCDEFG"


@dataclass(frozen=True)
class Tally:
    n_true: int
    n_false: int
    n_unknown: int
    unknown_by_reason: Mapping[Reason, int] = field(default_factory=dict[Reason, int])
    unknown_by_clause: tuple[int, ...] = ()
    lift_differs: int = 0
    marks: frozenset[Mark] = frozenset()


def tally(n_true: int, n_false: int, n_unknown: int, *, lift_differs: int = 0) -> Tally:
    """Every unknown unit NOT_ASSESSED, and unknown for the one top-level clause."""
    reasons = dict.fromkeys(Reason, 0)
    reasons[Reason.NOT_ASSESSED] = n_unknown
    return Tally(n_true, n_false, n_unknown, reasons, (n_unknown,), lift_differs)


def one_clause(canon: Canon, city: City, doc: Doc) -> CanonicalCohort:
    written = doc([{"kind": "value", "column": "establishments.cuisine", "values": ["thai"]}])
    result = canon(written, city())
    [cohort] = result.cohorts.values()
    return cohort


def served(cohort: CanonicalCohort, parts: CountParts, k: int | None) -> CohortCount:
    """The count ``count_cohort`` would serve: every check of the model holds for it."""
    return CohortCount(
        id=cohort.id,
        digest=parts.digest,
        population=parts.population,
        size=parts.size,
        disclosure=Disclosure(min_cell_count=k),
        readback=[TextSegment(text="a cohort")],
        caveats=list(parts.caveats),
        releases=[
            ReleaseRef(dataset="d", label=1, manifest=cohort.release.manifest, status="published")
        ],
        issuance=Issuance(id=ISSUANCE, cache_hit=False, values_from=ISSUANCE),
    )


def codes(parts: CountParts) -> set[str]:
    return {caveat.code for caveat in parts.caveats}


def test_without_a_min_cell_count_nothing_changes(canon: Canon, city: City, doc: Doc) -> None:
    cohort = one_clause(canon, city, doc)
    parts = count_parts(cohort, tally(1, 2, 1, lift_differs=1))
    assert disclosed(parts, None) is parts


def test_a_small_n_false_takes_n_true_with_it(canon: Canon, city: City, doc: Doc) -> None:
    """``n_true`` and ``n_false`` go together, as ``known X`` would show their sum (D297)."""
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(20, 3, 9)), 5)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, None, 9)
    assert population.suppressed == ["/lift_differs", "/n_false", "/n_true"]
    assert parts.size.numerator is None
    assert parts.size.reasons() == {"/estimate": "suppressed", "/numerator": "suppressed"}
    assert CaveatCode.SUPPRESSED in codes(parts)
    served(cohort, parts, 5)


def test_a_small_count_whose_partner_is_zero_takes_n_unknown_with_it(
    canon: Canon, city: City, doc: Doc
) -> None:
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(0, 3, 20)), 5)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (0, None, None)
    served(cohort, parts, 5)


def test_a_small_n_unknown_takes_the_smaller_of_the_others_and_both_on_a_tie(
    canon: Canon, city: City, doc: Doc
) -> None:
    """On a tie neither is the smaller, so both go: ``not X`` swaps them, and would otherwise
    show the one ``X`` hides (D297)."""
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(12, 9, 2)), 5)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (12, None, None)
    served(cohort, parts, 5)
    tie = disclosed(count_parts(cohort, tally(9, 9, 2)), 5)
    population = tie.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, None, None)
    assert tie.size.denominator == 20
    served(cohort, tie, 5)


def test_a_count_of_zero_is_shown_and_takes_nothing_with_it(
    canon: Canon, city: City, doc: Doc
) -> None:
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(0, 30, 0)), 5)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (0, 30, 0)
    assert population.suppressed == ["/lift_differs"]
    beside = disclosed(count_parts(cohort, tally(3, 0, 20)), 5)
    population = beside.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, 0, None)
    served(cohort, beside, 5)


def test_a_unit_table_under_k_suppresses_its_size_and_every_count(
    canon: Canon, city: City, doc: Doc
) -> None:
    """A table of 1 to *k* − 1 units hides its size, as the catalogue hides its ``n_rows``
    (D271), and all three counts, zeros included: beside two zeros one count would be the
    table (D297)."""
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(24, 0, 0)), 30)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, None, None)
    assert parts.size.denominator is None
    assert parts.size.reasons() == {
        "/denominator": "suppressed",
        "/estimate": "suppressed",
        "/numerator": "suppressed",
    }
    assert {CaveatCode.SUPPRESSED, CaveatCode.UNKNOWN_EXCLUDED} <= codes(parts)
    served(cohort, parts, 30)


def test_a_unit_table_of_three_shows_its_size_and_none_of_its_counts(
    canon: Canon, city: City, doc: Doc
) -> None:
    """Under *k* = 2 a table of 3 units shows its size, but none of the three counts: three
    suppressed counts of 1 would be read off it (D297)."""
    cohort = one_clause(canon, city, doc)
    for counts in (tally(1, 1, 1), tally(3, 0, 0)):
        parts = disclosed(count_parts(cohort, counts), 2)
        population = parts.population
        assert (population.n_true, population.n_false, population.n_unknown) == (None, None, None)
        assert parts.size.denominator == 3
        served(cohort, parts, 2)


def test_two_counts_that_would_each_be_1_split_one_way_and_take_the_third_with_them(
    canon: Canon, city: City, doc: Doc
) -> None:
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(1, 1, 5)), 2)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, None, None)
    assert parts.size.denominator == 7
    served(cohort, parts, 2)


def test_two_counts_the_pass_would_split_only_one_way_take_the_third_with_them(
    canon: Canon, city: City, doc: Doc
) -> None:
    """Under *k* = 5, 4 and 4 beside 5 are both suppressed, and 8 splits only as 4 and 4: 3
    and 5 would take ``n_true`` with the 3, and 5 and 3 both others with the 3, so the 5 is
    suppressed too."""
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(5, 4, 4)), 5)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, None, None)
    assert parts.size.denominator == 13
    served(cohort, parts, 5)


def test_a_small_complement_suppresses_the_size_numerator(
    canon: Canon, city: City, doc: Doc
) -> None:
    """The units outside the cohort are the numerator's complement: 2 of them, beside a shown
    unit table, would show; so ``n_true`` is suppressed, and with it the smallest other."""
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(40, 2, 0)), 5)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, None, 0)
    assert parts.size.denominator == 42
    served(cohort, parts, 5)


def test_any_count_whose_complement_is_small_is_suppressed(
    canon: Canon, city: City, doc: Doc
) -> None:
    """Each count's complement is a count of another cohort over the table (``not X``,
    ``known X``, ``unknown X``), so none is shown beside a small one (D297)."""
    cohort = one_clause(canon, city, doc)
    for counts, shown in (
        ((0, 40, 2), (0, None, None)),
        ((2, 0, 40), (None, 0, None)),
        ((1, 2, 40), (None, None, None)),
    ):
        parts = disclosed(count_parts(cohort, tally(*counts)), 5)
        population = parts.population
        assert (population.n_true, population.n_false, population.n_unknown) == shown
        served(cohort, parts, 5)


def _digits(parts: CountParts, code: CaveatCode) -> str:
    [found] = [c for c in parts.caveats if c.code == code]
    return "".join(
        ch for s in found.message if isinstance(s, TextSegment) for ch in s.text if ch.isdigit()
    )


def test_a_small_lift_differs_is_suppressed(canon: Canon, city: City, doc: Doc) -> None:
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(20, 20, 0, lift_differs=3)), 5)
    population = parts.population
    assert (population.n_true, population.n_false, population.lift_differs) == (20, 20, None)
    assert _digits(parts, CaveatCode.LIFT_DIFFERS) == ""
    served(cohort, parts, 5)


def test_lift_differs_is_suppressed_under_any_k_whatever_it_and_the_counts_are(
    canon: Canon, city: City, doc: Doc
) -> None:
    """A cohort's ``lift_differs`` counts units of one side of its accounting, which ``not``,
    ``known`` and ``unknown`` of it count in another place, so one shown beside any counts,
    all shown and large included, could bound a suppressed count of another of them; it is
    suppressed, and ``LIFT_DIFFERS`` carried without a number, whatever it was (D297)."""
    cohort = one_clause(canon, city, doc)
    for counts, lift in (((3, 20, 9), 0), ((3, 20, 9), 9), ((20, 20, 9), 9), ((20, 20, 9), 0)):
        parts = disclosed(count_parts(cohort, tally(*counts, lift_differs=lift)), 5)
        population = parts.population
        assert population.lift_differs is None
        assert "/lift_differs" in population.suppressed
        assert _digits(parts, CaveatCode.LIFT_DIFFERS) == ""
        assert CaveatCode.SUPPRESSED in codes(parts)
        served(cohort, parts, 5)
    whole = count_parts(cohort, tally(20, 20, 9, lift_differs=9))
    assert disclosed(whole, None).population.lift_differs == 9


def test_a_lift_differs_equal_to_the_unknown_units_does_not_show_a_suppressed_n_true(
    canon: Canon, city: City, run: Runner, doc: Doc
) -> None:
    """One establishment with a pest violation, on its one assessed inspection, and 21 with an
    assessed and an unassessed inspection each: under *k* = 5 the 1 and the 21 unknown are
    suppressed, and the 21 units the other lift rule would make definite would have shown
    both, beside a size of 22."""
    rows: dict[str, list[dict[str, object]]] = {
        "establishments": [{"establishment_id": f"e{n}"} for n in range(22)],
        "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"}],
        "inspection_checklists": [{"inspection_id": "i0", "checklist": "basic"}],
        "checklist_items": [{"checklist": "basic", "code": "pest", "all_codes": False}],
        "violations": [{"violation_id": "v0", "inspection_id": "i0", "code": "pest"}],
    }
    for n in range(1, 22):
        for inspection in (f"a{n}", f"b{n}"):
            given: dict[str, object] = {
                "inspection_id": inspection,
                "establishment_id": f"e{n}",
                "kind": "routine",
            }
            rows["inspections"].append(given)
        rows["inspection_checklists"].append({"inspection_id": f"a{n}", "checklist": "basic"})
    release = city(rows)
    written = doc([{"kind": "value", "column": "violations.code", "values": ["pest"]}])
    result = run(written, release).result
    assert (result.n_true, result.n_false, result.n_unknown, result.lift_differs) == (1, 0, 21, 21)
    [cohort] = canon(written, release).cohorts.values()
    parts = disclosed(count_parts(cohort, result), 5)
    population = parts.population
    assert (population.n_true, population.n_false, population.n_unknown) == (None, 0, None)
    assert population.lift_differs is None
    assert parts.size.denominator == 22
    assert _digits(parts, CaveatCode.LIFT_DIFFERS) == ""
    served(cohort, parts, 5)


def test_a_breakdown_with_a_small_count_is_null_as_a_whole(
    canon: Canon, city: City, doc: Doc
) -> None:
    cohort = one_clause(canon, city, doc)
    reasons = dict.fromkeys(Reason, 0)
    reasons[Reason.NOT_ASSESSED] = 7
    reasons[Reason.NO_INFORMATION] = 2
    given = Tally(20, 20, 9, reasons, (9,))
    parts = disclosed(count_parts(cohort, given), 5)
    population = parts.population
    assert population.n_unknown == 9
    assert population.unknown_by_reason is None
    assert population.unknown_by_leaf == {cohort.keys[0]: 9}
    assert population.suppressed == ["/lift_differs", "/unknown_by_reason"]
    served(cohort, parts, 5)


def test_a_leaf_breakdown_with_a_small_count_is_null_beside_a_shown_n_unknown(
    canon: Canon, city: City, doc: Doc
) -> None:
    written = doc(
        [
            {"kind": "value", "column": "establishments.cuisine", "values": ["thai"]},
            {"kind": "value", "column": "establishments.grade", "values": ["A"]},
        ]
    )
    [cohort] = canon(written, city()).cohorts.values()
    reasons = dict.fromkeys(Reason, 0)
    reasons[Reason.NOT_ASSESSED] = 9
    parts = disclosed(count_parts(cohort, Tally(20, 20, 9, reasons, (7, 2))), 5)
    population = parts.population
    assert population.n_unknown == 9
    assert population.unknown_by_reason == reasons
    assert population.unknown_by_leaf is None
    assert population.suppressed == ["/lift_differs", "/unknown_by_leaf"]
    served(cohort, parts, 5)


def test_messages_quote_only_the_counts_the_pass_shows(canon: Canon, city: City, doc: Doc) -> None:
    cohort = one_clause(canon, city, doc)
    parts = disclosed(count_parts(cohort, tally(20, 20, 3)), 5)
    [unknown] = [c for c in parts.caveats if c.code == CaveatCode.UNKNOWN_EXCLUDED]
    written = "".join(s.text for s in unknown.message if isinstance(s, TextSegment))
    assert "3" not in written
    assert "suppressed" in written
    shown = disclosed(count_parts(cohort, tally(20, 20, 9)), 5)
    [unknown] = [c for c in shown.caveats if c.code == CaveatCode.UNKNOWN_EXCLUDED]
    assert "9 units" in "".join(s.text for s in unknown.message if isinstance(s, TextSegment))


def test_the_digest_is_taken_after_the_pass(canon: Canon, city: City, doc: Doc) -> None:
    cohort = one_clause(canon, city, doc)
    before = count_parts(cohort, tally(20, 3, 9))
    after = disclosed(before, 5)
    assert after.digest != before.digest
    assert served(cohort, after, 5).digest == after.digest
    assert disclosed(count_parts(cohort, tally(20, 30, 9)), None).digest == (
        count_parts(cohort, tally(20, 30, 9)).digest
    )


@settings(max_examples=300, deadline=None)
@given(
    n_true=st.integers(0, 12),
    n_false=st.integers(0, 12),
    n_unknown=st.integers(0, 12),
    lift=st.integers(0, 12),
    k=st.integers(2, 6),
)
def test_every_count_the_pass_leaves_is_a_valid_cohort_count(
    canon: Canon,
    city: City,
    doc: Doc,
    n_true: int,
    n_false: int,
    n_unknown: int,
    lift: int,
    k: int,
) -> None:
    """No shown count lies from 1 to *k* − 1, the unit table's size included, and the model's
    checks of the pass all hold (§8.4)."""
    cohort = one_clause(canon, city, doc)
    lift = min(lift, n_true + n_false + n_unknown)
    parts = disclosed(count_parts(cohort, tally(n_true, n_false, n_unknown, lift_differs=lift)), k)
    population = parts.population
    shown = [
        population.n_true,
        population.n_false,
        population.n_unknown,
        population.lift_differs,
        parts.size.denominator,
        *(population.unknown_by_reason or {}).values(),
        *(population.unknown_by_leaf or {}).values(),
    ]
    assert not [count for count in shown if count is not None and 0 < count < k]
    served(cohort, parts, k)


LARGEST = 12
"""The largest count the exhaustive check gives each of ``n_true``, ``n_false`` and
``n_unknown``: every unit table of up to this many units is then covered by all its splits."""
MARKED = 6
"""The largest count the exhaustive check over ``lift_differs`` and the flags gives each."""
FLAGGED = frozenset(
    {
        Mark(Flag.SCOPE_PARTIAL, "rel:inspections.establishment"),
        Mark(Flag.COVERAGE_PROPOSED, "rel:inspections.establishment"),
    }
)


def _pinned(
    cohort: CanonicalCohort,
    k: int,
    inputs: Iterable[Tally],
    largest: int,
    names: tuple[str, ...] = ("n_true", "n_false", "n_unknown", "lift_differs"),
) -> list[tuple[str, list[Tally]]]:
    """Each suppressed member of ``names``, of those counted from ``inputs``, that what the pass
    shows the same way (population, size and caveats, messages included) pins to one value; the
    size under *k* = 2 aside, pinned as a 1 is (D271), and a ``lift_differs`` pinned to 0, which
    only a bound of the inputs' own, read off the counts shown, can do."""
    seen: dict[str, list[Tally]] = defaultdict(list)
    sizes: dict[str, int | None] = {}
    for counts in inputs:
        parts = disclosed(count_parts(cohort, counts), k)
        shown = json.dumps(
            [
                parts.population.model_dump(mode="json"),
                parts.size.model_dump(mode="json"),
                [caveat.model_dump(mode="json") for caveat in parts.caveats],
            ],
            sort_keys=True,
        )
        seen[shown].append(counts)
        sizes[shown] = parts.size.denominator
    pinned: list[tuple[str, list[Tally]]] = []
    for shown, found in seen.items():
        size = sizes[shown]
        if size is not None and size > largest:
            continue
        population = json.loads(shown)[0]
        for name in names:
            values = {getattr(g, name) for g in found}
            aside = name == "lift_differs" and values == {0}
            if population[name] is None and len(values) < 2:
                pinned.extend([] if aside else [(name, found)])
        total = {g.n_true + g.n_false + g.n_unknown for g in found}
        if size is None and k > 2 and len(total) < 2:
            pinned.append(("size", found))
    return pinned


def test_what_the_pass_shows_pins_no_suppressed_count_to_one_value(
    canon: Canon, city: City, doc: Doc
) -> None:
    """For each *k* from 2 to 6 and every count over a table of at most ``LARGEST`` units, the
    counts the pass shows the same way leave every suppressed count two values or more: its
    complement, the size, the breakdowns and the caveats together recover none. One leaf and
    one reason stand for any: the breakdowns count units of ``n_unknown`` alone and are shown
    only beside it."""
    cohort = one_clause(canon, city, doc)
    for k in range(2, 7):
        counted = (tally(*c) for c in itertools.product(range(LARGEST + 1), repeat=3))
        assert _pinned(cohort, k, counted, LARGEST, ("n_true", "n_false", "n_unknown")) == []


def test_lift_differs_and_the_flags_caveats_pin_no_suppressed_count_either(
    canon: Canon, city: City, doc: Doc
) -> None:
    """As above, over tables of at most ``MARKED`` units, with ``lift_differs`` at 0, 1,
    *k* − 1, *k* and *k* + 1 (at most the size) and without or with the units' flags, whose
    caveats say that some unit carries them: a ``lift_differs``, suppressed under every *k*, is
    pinned only where the counts bound it to 0."""
    cohort = one_clause(canon, city, doc)
    for k in range(2, 6):
        counted = [
            replace(tally(*c, lift_differs=lift), marks=marks)
            for c in itertools.product(range(MARKED + 1), repeat=3)
            for lift in sorted({0, 1, k - 1, k, k + 1})
            if lift <= sum(c)
            for marks in (frozenset[Mark](), FLAGGED)
            if sum(c) or not marks
        ]
        assert _pinned(cohort, k, counted, MARKED) == []


BOUNDED = 6
"""The largest count the exhaustive check of a ``lift_differs`` bounded by the counts gives
each."""


def test_a_lift_differs_bounded_by_the_counts_pins_no_suppressed_count(
    canon: Canon, city: City, doc: Doc
) -> None:
    """A strict cohort of ``some`` steps turns only unknown units definite when its lifts flip,
    so its ``lift_differs`` is at most ``n_unknown``; other cohorts bound it by ``n_true`` +
    ``n_false``. Over every table of at most ``BOUNDED`` units, *k* from 2 to 5, and every
    ``lift_differs`` either bound allows, a writer who knows the bound is left every suppressed
    count two values or more; a ``lift_differs``, suppressed under every *k*, is pinned only
    where the counts shown bound it to 0 (D297)."""
    cohort = one_clause(canon, city, doc)
    bounds: tuple[Callable[[tuple[int, ...]], int], ...] = (
        lambda c: c[2],
        lambda c: c[0] + c[1],
    )
    for k in range(2, 6):
        for bound in bounds:
            counted = [
                tally(*c, lift_differs=lift)
                for c in itertools.product(range(BOUNDED + 1), repeat=3)
                for lift in range(bound(c) + 1)
            ]
            assert _pinned(cohort, k, counted, BOUNDED) == []


FAMILY = 40
"""The largest unit table the check of a cohort beside its negation and knowns covers."""


def _family(n_true: int, n_false: int, n_unknown: int) -> list[tuple[int, int, int]]:
    """The accounting of ``X``, ``not X``, ``known X`` and ``unknown X`` from ``X``'s (§6.3)."""
    known = n_true + n_false
    return [
        (n_true, n_false, n_unknown),
        (n_false, n_true, n_unknown),
        (known, n_unknown, 0),
        (n_unknown, known, 0),
    ]


def _shown(counts: tuple[int, int, int], k: int) -> tuple[int | None, ...]:
    hidden = suppressed(*counts, k)
    names = ("n_true", "n_false", "n_unknown")
    total = None if "size" in hidden else sum(counts)
    return (
        total,
        *(None if name in hidden else count for name, count in zip(names, counts, strict=True)),
    )


def test_a_cohort_its_negation_and_its_knowns_counted_together_pin_no_suppressed_count() -> None:
    """``X``, ``not X``, ``known X`` and ``unknown X``, counted in one call over one table of
    at most ``FAMILY`` units, show nothing, together, that pins a suppressed count of any of
    them to one value, for *k* from 3 to 6; ``known X`` and ``unknown X`` have no unknown
    units, which their writer knows. Under *k* = 2, where 1 to *k* − 1 is one value, they pin
    only an ``n_unknown`` of 1, or an ``n_true`` and an ``n_false`` of 1 each, and what those
    leave of the size (D297)."""
    for k in range(2, 7):
        seen: dict[tuple[tuple[int | None, ...], ...], list[tuple[int, int, int]]]
        seen = defaultdict(list)
        for n_true, n_false, n_unknown in itertools.product(range(FAMILY + 1), repeat=3):
            if n_true + n_false + n_unknown <= FAMILY:
                family = _family(n_true, n_false, n_unknown)
                seen[tuple(_shown(member, k) for member in family)].append(family[0])
        pinned: list[tuple[int, int, int, list[tuple[int, int, int]]]] = []
        for shown, found in seen.items():
            if k == 2 and (all(c[2] == 1 for c in found) or all(c[0] == c[1] == 1 for c in found)):
                continue
            for index, member in enumerate(shown):
                for place in range(3 if index < 2 else 2):
                    values = {_family(*c)[index][place] for c in found}
                    if member[1 + place] is None and len(values) < 2:
                        pinned.append((k, index, place, found))
            if shown[0][0] is None and k > 2 and len({sum(c) for c in found}) < 2:
                pinned.append((k, -1, -1, found))
        assert pinned == []


FAMILY_LIFTED = 12
"""The largest unit table the check of a cohort beside its negation and knowns, each with its
``lift_differs``, covers."""


def test_a_cohort_its_negation_and_its_knowns_with_their_lift_differs_pin_no_suppressed_count(
    canon: Canon, city: City, doc: Doc
) -> None:
    """As above, over every table of at most ``FAMILY_LIFTED`` units, each of the four with
    its ``lift_differs`` and all that the pass shows of it (size, counts, breakdowns and
    caveats, messages included). The units the other lift rule changes lie on one side of
    ``X``, true, false or unknown, which the writer is taken to know, and are the same units
    in each of the four: a change of ``X``'s value changes ``not X``'s, and one between
    unknown and definite ``known X``'s and ``unknown X``'s, which the check takes every change
    to be, so that each member's ``lift_differs`` is bounded by the side of it that can
    change. Nothing shown pins a suppressed count of any of them to one value, a
    ``lift_differs`` included, but one that side bounds to 0, for *k* from 3 to 5 (D297)."""
    cohort = one_clause(canon, city, doc)
    names = ("n_true", "n_false", "n_unknown")
    cache: dict[tuple[tuple[int, int, int], int, int], str] = {}

    def shown(counts: tuple[int, int, int], lift: int, k: int) -> str:
        key = (counts, lift, k)
        if key not in cache:
            parts = disclosed(count_parts(cohort, tally(*counts, lift_differs=lift)), k)
            cache[key] = json.dumps(
                [
                    parts.population.model_dump(mode="json"),
                    parts.size.model_dump(mode="json"),
                    [caveat.model_dump(mode="json") for caveat in parts.caveats],
                ],
                sort_keys=True,
            )
        return cache[key]

    tables = [
        (n_true, n_false, n_unknown)
        for n_true, n_false, n_unknown in itertools.product(range(FAMILY_LIFTED + 1), repeat=3)
        if n_true + n_false + n_unknown <= FAMILY_LIFTED
    ]
    pinned: list[tuple[int, int, int, str]] = []
    for k in range(3, 6):
        for side in range(3):
            seen: dict[tuple[str, ...], list[tuple[tuple[int, int, int], int]]]
            seen = defaultdict(list)
            for counts in tables:
                for lift in range(counts[side] + 1):
                    family = _family(*counts)
                    seen[tuple(shown(member, lift, k) for member in family)].append((counts, lift))
            for key, found in seen.items():
                for index, member in enumerate(key):
                    population, size = json.loads(member)[:2]
                    for place, name in enumerate(names[: 3 if index < 2 else 2]):
                        values = {_family(*c)[index][place] for c, _ in found}
                        if population[name] is None and len(values) < 2:
                            pinned.append((k, side, index, name))
                    lifts = {lift for _, lift in found}
                    if population["lift_differs"] is None and len(lifts) < 2 and lifts != {0}:
                        pinned.append((k, side, index, "lift_differs"))
                    if size["denominator"] is None and len({sum(c) for c, _ in found}) < 2:
                        pinned.append((k, side, index, "size"))
    assert pinned == []


def test_the_counts_a_negation_or_a_known_once_pinned_are_suppressed(
    canon: Canon, city: City, doc: Doc
) -> None:
    """Under *k* = 5, ``X`` of 1, 6 and 5 shows neither its 1 nor its 6, which ``known X``'s 7
    would turn into each other; and ``X`` of 10, 10 and 3 shows neither 10, which ``not X``
    swaps (D297)."""
    cohort = one_clause(canon, city, doc)

    def shown(counts: tuple[int, int, int]) -> tuple[int | None, ...]:
        population = disclosed(count_parts(cohort, tally(*counts)), 5).population
        return (population.n_true, population.n_false, population.n_unknown)

    assert [shown(member) for member in _family(1, 6, 5)] == [
        (None, None, 5),
        (None, None, 5),
        (7, 5, 0),
        (5, 7, 0),
    ]
    assert [shown(member) for member in _family(10, 10, 3)] == [
        (None, None, None),
        (None, None, None),
        (None, None, 0),
        (None, None, 0),
    ]


def test_a_negation_and_the_knowns_of_a_cohort_count_as_the_family_check_assumes(
    run: Runner, city: City, doc: Doc
) -> None:
    release = city(
        {
            "establishments": [
                {"establishment_id": "e0", "grade": "A"},
                {"establishment_id": "e1", "grade": "B"},
                {"establishment_id": "e2", "grade": "B"},
                {"establishment_id": "e3", "grade": "pending"},
            ]
        }
    )
    x = {"kind": "value", "column": "establishments.grade", "values": ["A"]}
    written = doc(
        {"x": [x], "not": [{"not": x}], "known": [{"known": x}], "unknown": [{"unknown": x}]}
    )
    results = run(written, release).results
    found = [
        (results[name].n_true, results[name].n_false, results[name].n_unknown)
        for name in ("x", "not", "known", "unknown")
    ]
    assert found == _family(1, 2, 1)


def test_known_x_counted_beside_x_does_not_show_the_n_true_x_suppressed(
    canon: Canon, city: City, run: Runner, doc: Doc
) -> None:
    """One establishment with a pest violation on its one inspection, and 21 with an assessed
    and an unassessed inspection each; ``x`` asks for a pest violation with lift
    ``assessed``. Under *k* = 5 ``x`` suppresses its 1, and ``known x``, whose counts are all
    large, would have shown a ``lift_differs`` of 21, the units the other lift rule makes
    unknown, all of them among ``x``'s false ones: the size less it is the 1 (D297)."""
    rows: dict[str, list[dict[str, object]]] = {
        "establishments": [{"establishment_id": f"e{n}"} for n in range(22)],
        "inspections": [{"inspection_id": "i0", "establishment_id": "e0", "kind": "routine"}],
        "inspection_checklists": [{"inspection_id": "i0", "checklist": "basic"}],
        "checklist_items": [{"checklist": "basic", "code": "pest", "all_codes": False}],
        "violations": [{"violation_id": "v0", "inspection_id": "i0", "code": "pest"}],
    }
    for n in range(1, 22):
        for inspection in (f"a{n}", f"b{n}"):
            given: dict[str, object] = {
                "inspection_id": inspection,
                "establishment_id": f"e{n}",
                "kind": "routine",
            }
            rows["inspections"].append(given)
        rows["inspection_checklists"].append({"inspection_id": f"a{n}", "checklist": "basic"})
    release = city(rows)
    x = {"kind": "value", "column": "violations.code", "values": ["pest"], "lift": "assessed"}
    written = doc(
        {"x": [x], "not": [{"not": x}], "known": [{"known": x}], "unknown": [{"unknown": x}]}
    )
    results = run(written, release).results
    cohorts = canon(written, release).cohorts
    found = {
        name: (result.n_true, result.n_false, result.n_unknown, result.lift_differs)
        for name, result in results.items()
    }
    assert found == {
        "x": (1, 21, 0, 21),
        "not": (21, 1, 0, 21),
        "known": (22, 0, 0, 21),
        "unknown": (0, 22, 0, 21),
    }
    for name, result in results.items():
        parts = disclosed(count_parts(cohorts[name], result), 5)
        assert parts.population.lift_differs is None
        assert _digits(parts, CaveatCode.LIFT_DIFFERS) == ""
        served(cohorts[name], parts, 5)
    shown = disclosed(count_parts(cohorts["x"], results["x"]), 5).population
    assert (shown.n_true, shown.n_false, shown.n_unknown) == (None, None, 0)
