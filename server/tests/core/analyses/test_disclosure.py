"""The disclosure pass over a predicate's split of a cohort's units (SPEC §8.4; D320), and what a
result shows of it pinning no suppressed count."""

import itertools
import json
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from fractions import Fraction

import pytest

from aibi.core.analyses.disclosure import Hidden, Split, hidden
from aibi.core.analyses.existence import Outcome
from aibi.core.analyses.views import CheckedView
from aibi.core.engine.sql import Crossed, CrossedSplit, Crossing
from aibi.core.engine.suppression import suppressed
from aibi.core.schema.semantics import Reason

Form = tuple[int, int, int]
"""A count as a sum of a base split's TRUE, FALSE and UNKNOWN units."""
T, F, U, NONE = (1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0)


def plus(first: Form, second: Form) -> Form:
    return (first[0] + second[0], first[1] + second[1], first[2] + second[2])


FAMILIES: dict[str, list[tuple[Form, Form, Form]]] = {
    "P": [(T, F, U)],
    "P, not P": [(T, F, U), (F, T, U)],
    "P, known P": [(T, F, U), (plus(T, F), U, NONE)],
    "P, unknown P": [(T, F, U), (U, plus(T, F), NONE)],
    "P, not P, known P, unknown P": [
        (T, F, U),
        (F, T, U),
        (plus(T, F), U, NONE),
        (U, plus(T, F), NONE),
    ],
}
"""Predicates a view can ask beside ``P``, each as the forms of its split's TRUE, FALSE and
UNKNOWN units over ``P``'s."""


def value(form: Form, base: Split) -> int:
    return form[0] * base.true + form[1] * base.false + form[2] * base.unknown


def determined(form: Form, shown: Sequence[Form]) -> bool:
    """Whether ``form`` is a combination of the forms shown and the size (``T`` + ``F`` +
    ``U``), which the cohort's count shows: whether what is shown gives it."""
    rows = [[Fraction(x) for x in row] for row in (*shown, (1, 1, 1))]
    rank = _rank(rows)
    return _rank([*rows, [Fraction(x) for x in form]]) == rank


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


def _analysed(members: Sequence[tuple[Form, Form, Form]]) -> Form:
    """The form of the units for which no predicate is known: ``P``'s unknown ones, or none
    beside ``known P`` or ``unknown P``."""
    return U if all(member[2] == U for member in members) else NONE


def _pinned(name: str, size: int, k: int) -> list[tuple[Split, str]]:
    members = FAMILIES[name]
    none_known = _analysed(members)
    seen: dict[tuple[object, ...], list[Split]] = defaultdict(list)
    shown_of: dict[tuple[object, ...], list[Form]] = {}
    for true, false in itertools.product(range(size + 1), repeat=2):
        if true + false > size:
            continue
        base = Split(true, false, size - true - false)
        splits = [Split(*(value(form, base) for form in member)) for member in members]
        excluded = value(none_known, base)
        hides = hidden(True, splits, k)
        shown: list[Form] = []
        projection: list[int | None] = []
        for index, (true_form, false_form, unknown_form) in enumerate(members):
            known_form = plus(true_form, false_form)
            for form, gone in (
                (true_form, hides.true[index]),
                (known_form, hides.unknown[index]),
                (unknown_form, hides.unknown[index]),
            ):
                projection.append(None if gone else value(form, base))
                shown.extend([] if gone else [form])
        projection.append(None if hides.analysed else excluded)
        shown.extend([] if hides.analysed else [none_known])
        key = tuple(projection)
        seen[key].append(base)
        shown_of[key] = shown
    pinned: list[tuple[Split, str]] = []
    for key, found in seen.items():
        for place, form in (("true", T), ("false", F), ("unknown", U)):
            if determined(form, shown_of[key]):
                continue
            if len({value(form, base) for base in found}) < 2:
                pinned.append((found[0], place))
    return pinned


def test_a_split_is_disclosed_as_a_cohort_count_over_the_cohort_s_units() -> None:
    for true, false, unknown in ((9, 0, 1), (9, 1, 0), (8, 2, 0), (8, 0, 2), (5, 5, 0)):
        found = hidden(True, [Split(true, false, unknown)], 3)
        expected = suppressed(true, false, unknown, 3)
        assert (found.true[0], found.unknown[0]) == ("n_true" in expected, "n_unknown" in expected)


def test_a_numerator_of_nine_beside_one_unknown_unit_is_suppressed() -> None:
    found = hidden(True, [Split(9, 0, 1)], 3)
    assert found == Hidden((True,), (True,), True)


def test_two_counts_of_1_take_the_third_with_them_on_a_later_pass() -> None:
    """The pass repeats until nothing changes (D297): the TRUE and FALSE units, each 1, are
    suppressed first, and their sum splits only one way, so the UNKNOWN units go too."""
    found = hidden(True, [Split(1, 1, 10)], 3)
    assert found == Hidden((True,), (True,), True)
    assert hidden(True, [Split(1, 2, 10)], 3).unknown == (False,)


def test_a_position_whose_cohort_size_is_suppressed_shows_none_of_its_splits() -> None:
    found = hidden(False, [Split(10, 10, 10), Split(20, 10, 0)], 3)
    assert found == Hidden((True, True), (True, True), True)


def test_a_predicate_and_its_not_are_disclosed_alike() -> None:
    for true, false, unknown in itertools.product(range(8), repeat=3):
        split = Split(true, false, unknown)
        found = hidden(True, [split, Split(false, true, unknown)], 3)
        assert found.unknown[0] == found.unknown[1]
        assert found.true[1] == ("n_false" in suppressed(true, false, unknown, 3))


def test_the_units_analysed_of_one_predicate_go_with_its_unknown_units() -> None:
    assert not hidden(True, [Split(10, 10, 5)], 3).analysed
    assert hidden(True, [Split(10, 10, 2)], 3).analysed


def test_no_figure_combining_several_predicates_is_shown_under_k() -> None:
    """The units for which some predicate is known are bounded by each predicate's split from
    both sides, so with two predicates or more they are never shown (D320)."""
    for splits in ([Split(10, 10, 5), Split(12, 10, 3)], [Split(10, 10, 0), Split(10, 10, 0)]):
        assert hidden(True, splits, 3).analysed


def test_what_a_result_shows_of_a_predicate_and_its_family_pins_no_suppressed_count() -> None:
    """For every split of a cohort of up to 14 units, *k* from 3 to 5, and ``P`` alone or beside
    its ``not``, ``known`` and ``unknown``, the splits a result shows the same way leave every
    count it does not show two values or more (D320; the cohort's size is shown)."""
    pinned = [
        (name, size, k, found)
        for name in FAMILIES
        for k in range(3, 6)
        for size in range(1, 15)
        for found in _pinned(name, size, k)
    ]
    assert pinned == []


def test_a_predicate_alone_pins_no_suppressed_count_under_k_of_2() -> None:
    pinned = [(size, found) for size in range(1, 15) for found in _pinned("P", size, 2)]
    assert pinned == []


def _forms(outcome: Outcome, members: Sequence[tuple[Form, Form, Form]]) -> list[Form]:
    """The forms of the counts a result shows of a view's predicates (``members``)."""
    [position] = outcome.values.positions
    [analysed] = outcome.analysed
    found: list[Form] = []
    for share, (true, false, _) in zip(position.predicates, members, strict=True):
        proportion = share.proportion
        found.extend([true] if proportion.numerator is not None else [])
        found.extend([plus(true, false)] if proportion.denominator is not None else [])
    if analysed.variables is None:
        found.extend([members[0][2]] if analysed.excluded_units is not None else [])
    else:
        for variable, (_, _, unknown) in zip(analysed.variables, members, strict=True):
            found.extend([unknown] if variable.excluded_units is not None else [])
        found.extend([U] if analysed.excluded_units is not None else [])
    return found


PATTERN = {T: "T", F: "F", U: "I"}


def _pattern(member: tuple[Form, Form, Form], base: Split) -> str:
    """A predicate's truth values over the cohort's units, ordered TRUE, FALSE and UNKNOWN units
    of ``P``, as the member's forms say each is answered."""
    found = []
    for form, count in ((T, base.true), (F, base.false), (U, base.unknown)):
        letter = next(
            PATTERN[place] for place, got in zip(member, (T, F, U), strict=True) if got == form
        )
        found.append(letter * count)
    return "".join(found)


def test_a_result_shows_nothing_of_a_predicate_or_its_not_that_pins_a_suppressed_count(
    viewed: Callable[..., CheckedView], compared: Callable[..., Outcome]
) -> None:
    """Every split of cohorts of 8 to 14 units, under *k* from 3 to 5, run through
    ``compare.existence`` with ``P`` alone or beside ``not P``: the results that read the same
    (population, analysed, values and caveats, messages included) leave every count they do not
    show, even by difference, two values or more; ``TTTTTTTTTI`` among them (D320)."""
    pinned: list[tuple[str, int, int, Split, str]] = []
    for name in ("P", "P, not P"):
        members = FAMILIES[name]
        for size, k in ((8, 3), (10, 3), (12, 4), (14, 5)):
            view = viewed(1, len(members), k=k)
            seen: dict[str, list[Split]] = defaultdict(list)
            forms: dict[str, list[Form]] = {}
            for true, false in itertools.product(range(size + 1), repeat=2):
                if true + false > size:
                    continue
                base = Split(true, false, size - true - false)
                outcome = compared(view, ["T" * size], [_pattern(m, base) for m in members], k=k)
                key = json.dumps(
                    [
                        [p.model_dump(mode="json") for p in outcome.population],
                        [a.model_dump(mode="json") for a in outcome.analysed],
                        outcome.values.model_dump(mode="json"),
                        [c.model_dump(mode="json") for c in outcome.caveats],
                    ],
                    sort_keys=True,
                )
                seen[key].append(base)
                forms[key] = _forms(outcome, members)
            for key, found in seen.items():
                for place, form in (("true", T), ("false", F), ("unknown", U)):
                    if (
                        not determined(form, forms[key])
                        and len({value(form, base) for base in found}) < 2
                    ):
                        pinned.append((name, size, k, found[0], place))
    assert pinned == []


# --- Independent predicates -----------------------------------------------------------------

Marginal = tuple[int, ...]
"""A predicate's units at a position, by answer: ``T``, ``F``, and UNKNOWN for ``I`` (no
information) or ``A`` (not assessed)."""
Vector = tuple[int, ...]
"""A count as a sum of the varied position's (predicate, answer) counts."""
REASON = {"I": Reason.NO_INFORMATION, "A": Reason.NOT_ASSESSED}


def _marginals(size: int, answers: str) -> list[Marginal]:
    return [
        found
        for found in itertools.product(range(size + 1), repeat=len(answers))
        if sum(found) == size
    ]


def _crossed(marginals: Sequence[Marginal], answers: str) -> Crossed:
    """A position's predicates answering as their marginals say, their units in the order
    ``answers`` lists them: the crossing a set of units so answered gives."""
    size = sum(marginals[0])
    splits: list[CrossedSplit] = []
    for marginal in marginals:
        of = dict(zip(answers, marginal, strict=True))
        by_reason = dict.fromkeys(Reason, 0)
        by_reason.update({REASON[a]: of[a] for a in answers if a in REASON})
        splits.append(
            CrossedSplit(of["T"], of["F"], size - of["T"] - of["F"], by_reason, 0, frozenset())
        )
    known = max(split.true + split.false for split in splits)
    none_by_reason = dict.fromkeys(Reason, 0)
    for unit in range(known, size):
        reasons = set()
        for marginal in marginals:
            at = 0
            for answer, count in zip(answers, marginal, strict=True):
                if at <= unit < at + count and answer in REASON:
                    reasons.add(REASON[answer])
                at += count
        for reason in reasons:
            none_by_reason[reason] += 1
    return Crossed(tuple(splits), known, size - known, none_by_reason)


def _of(answers: str, letters: str) -> Vector:
    return tuple(int(a in letters) for a in answers)


def _secrets(answers: str) -> list[tuple[str, Vector]]:
    """A predicate's TRUE, FALSE and UNKNOWN units, and its UNKNOWN units by reason."""
    found = [(name, _of(answers, letters)) for name, letters in _PARTS]
    return found + [(a, _of(answers, a)) for a in answers if a in REASON]


_PARTS = (("true", "T"), ("false", "F"), ("unknown", "IA"))
_LEAF = re.compile(r"leaf:[0-9a-f]{64}")


def _shown_vectors(outcome: Outcome, position: int, predicate: int, answers: str) -> list[Vector]:
    """The counts a result shows of a predicate at a position, each as a sum of its counts by
    answer: the position's size, its numerator, denominator, UNKNOWN units and their reasons."""
    found: list[Vector] = []
    if outcome.population[position].n_true is not None:
        found.append(_of(answers, answers))
    proportion = outcome.values.positions[position].predicates[predicate].proportion
    maps = [proportion.excluded]
    shown = {"T": proportion.numerator is not None, "TF": proportion.denominator is not None}
    variables = outcome.analysed[position].variables
    if variables is not None:
        shown["TF"] = shown["TF"] or variables[predicate].n is not None
        shown["IA"] = variables[predicate].excluded_units is not None
        maps.append(variables[predicate].excluded)
    found += [_of(answers, a) for a, is_shown in shown.items() if is_shown]
    if any(excluded is not None for excluded in maps):
        found += [_of(answers, a) for a in answers if a in REASON]
    return found


def _counts(outcome: Outcome, position: int) -> list[int]:
    """Every count a result shows of a position's predicates."""
    found: list[int] = []
    variables = outcome.analysed[position].variables or []
    for share in outcome.values.positions[position].predicates:
        proportion = share.proportion
        found += [n for n in (proportion.numerator, proportion.denominator) if n is not None]
        found += list((proportion.excluded or {}).values())
    for variable in variables:
        found += [n for n in (variable.n, variable.excluded_units) if n is not None]
        found += list((variable.excluded or {}).values())
    return found


def _given(vector: Vector, shown: Sequence[Vector]) -> bool:
    rows = [[Fraction(x) for x in row] for row in shown]
    if not rows:
        return not any(vector)
    return _rank([*rows, [Fraction(x) for x in vector]]) == _rank(rows)


def _whole(outcome: Outcome) -> str:
    return json.dumps(
        [
            [p.model_dump(mode="json") for p in outcome.population],
            [a.model_dump(mode="json") for a in outcome.analysed],
            outcome.values.model_dump(mode="json"),
            [c.model_dump(mode="json") for c in outcome.caveats],
        ],
        sort_keys=True,
    )


def _columns(outcome: Outcome) -> tuple[str, list[str]]:
    """A result as what it shows of no predicate and what it shows of each (its values at every
    position, its `variables` entries and its contrasts, its leaf key left out), so that
    results that differ only in their predicates' order read the same once the columns are
    sorted."""
    values = outcome.values.model_dump(mode="json")
    analysed = [a.model_dump(mode="json") for a in outcome.analysed]
    count = len(values["view"]["predicates"])
    columns = [
        _LEAF.sub(
            "leaf",
            json.dumps(
                [
                    [position["predicates"][j] for position in values["positions"]],
                    [(a.get("variables") or [None] * count)[j] for a in analysed],
                    values["view"]["predicates"][j],
                ],
                sort_keys=True,
            ),
        )
        for j in range(count)
    ]
    rest = json.dumps(
        [
            [p.model_dump(mode="json") for p in outcome.population],
            [{k: v for k, v in a.items() if k != "variables"} for a in analysed],
            values["view"]["family"],
            [c.model_dump(mode="json") for c in outcome.caveats],
        ],
        sort_keys=True,
    )
    return rest, columns


OTHERS = {"shown": (4, 4, 4, 0), "small": (1, 5, 5, 1)}
"""The other position's 12 units, as each of its predicates answers them by ``TFIA``: every
count shown, or small counts beside large ones."""


@pytest.mark.parametrize(
    ("predicates", "answers", "runs"),
    [
        (2, "TFIA", [(0, "shown"), (1, "small")]),
        (3, "TFI", [(0, "small"), (1, "shown")]),
    ],
)
def test_a_result_of_independent_predicates_pins_no_suppressed_count(
    viewed: Callable[..., CheckedView],
    counted: Callable[..., Outcome],
    predicates: int,
    answers: str,
    runs: list[tuple[int, str]],
) -> None:
    """Every way the 6 units of one of two positions can split over two predicates answered
    ``TFIA`` or three answered ``TFI``, under *k* = 3, beside the other position's 12 units:
    no result shows a count from 1 to 2 or anything that combines predicates, and the results
    that read the same (population, analysed, values and caveats, messages included) leave
    every count of a predicate's
    split, and its UNKNOWN units by reason, that they do not show, even by difference, two
    values or more. What a result shows of a split depends on its predicate's own counts, which
    independent predicates take in every combination, so their marginals stand for every way
    the units can answer. The view's predicates are alike, so each set of marginals is taken in
    one order, and a result is read with its predicates' columns sorted: a predicate's counts
    are those of every predicate, in every result that reads the same, whose column reads as
    its own. Fixing the other position only splits what reads the same more finely, so a count
    left two values here is left them by every result (D320)."""
    size, rest = 6, 12
    view = viewed(2, predicates, k=3)
    secrets = _secrets(answers)
    marginals = _marginals(size, answers)
    combined: list[str] = []
    small: list[str] = []
    pinned: list[tuple[int, str, Marginal, str]] = []
    for at, name in runs:
        fixed = dict(zip("TFIA", OTHERS[name], strict=True))
        if "A" not in answers:
            fixed["I"] += fixed.pop("A")
        other = _crossed([tuple(fixed[a] for a in answers)] * predicates, answers)
        members = ["T" * size + "F" * rest, "F" * size + "T" * rest]
        if at == 1:
            members = ["T" * rest + "F" * size, "F" * rest + "T" * size]
        seen: dict[str, dict[str, set[Marginal]]] = defaultdict(lambda: defaultdict(set))
        shown: dict[str, list[Vector]] = {}
        for world in itertools.combinations_with_replacement(marginals, predicates):
            mine = _crossed(world, answers)
            cohorts = (mine, other) if at == 0 else (other, mine)
            outcome = counted(view, members, Crossing(cohorts, (0,)), k=3)
            analysed = outcome.analysed[at]
            if (analysed.n, analysed.excluded_units, analysed.excluded) != (None, None, None):
                combined.append(f"{at} {name} {world}")
            small += [f"{at} {name} {world}" for count in _counts(outcome, at) if 1 <= count < 3]
            common, columns = _columns(outcome)
            key = json.dumps([common, sorted(columns)])
            for j, column in enumerate(columns):
                seen[key][column].add(world[j])
                shown.setdefault(column, _shown_vectors(outcome, at, j, answers))
        for by_column in seen.values():
            for column, found in by_column.items():
                for secret, vector in secrets:
                    values = {sum(x * y for x, y in zip(vector, m, strict=True)) for m in found}
                    if len(values) < 2 and not _given(vector, shown[column]):
                        pinned.append((at, name, min(found), secret))
    assert combined == []
    assert small == []
    assert pinned == []


@pytest.mark.parametrize(
    ("given", "other"),
    [
        (
            ["I" * 11 + "T" * 5 + "F" * 4, "T" * 5 + "F" * 4 + "I" * 11],
            ["I" * 11 + "T" * 5 + "F" * 4, "T" * 5 + "F" * 3 + "I" * 11 + "F"],
        ),
        (["A" + "I" * 19, "A" * 10 + "I" * 10], ["AA" + "I" * 18, "A" * 10 + "I" * 10]),
    ],
)
def test_a_result_of_two_predicates_reads_the_same_whatever_their_suppressed_joint_counts(
    viewed: Callable[..., CheckedView],
    compared: Callable[..., Outcome],
    given: list[str],
    other: list[str],
) -> None:
    """The review's cases, 20 units under *k* = 3: two predicates each with 11 UNKNOWN units,
    of which 2 or 3 are UNKNOWN for both; and one predicate with 1 or 2 units not assessed beside
    one with 10. Each pair reads the same, so neither the units for which no predicate is known
    nor the suppressed reason count is pinned (D320)."""
    view = viewed(1, 2, k=3)
    first = compared(view, ["T" * 20], given, k=3)
    second = compared(view, ["T" * 20], other, k=3)
    assert _whole(first) == _whole(second)
    assert first.analysed[0].n is None
    assert first.analysed[0].excluded is None
