"""The recession cone of a Cox model's likelihood and the columns it decides (SPEC §9.5; D360,
D361), held to brute force: every pair formed, and an exact programme solved by enumerating
its vertices."""

import itertools
import json
import math
import random
import time
from collections.abc import Callable, Sequence
from fractions import Fraction
from pathlib import Path

import pytest

from aibi.core.analyses import cone, coxfit, coxph, timetoevent
from aibi.core.analyses.coxph import Unit
from aibi.core.analyses.timetoevent import ORIGIN, EventTime, Watch, risk_table
from aibi.core.engine.worker import CallerDeadline

REFERENCE = json.loads(
    (Path(__file__).parent / "reference" / "coxph.json").read_text(encoding="utf-8")
)

Ends = list[tuple[float, float, bool]]


def designs(
    seed: int, count: int, *, units: tuple[int, int], m: int, values: Sequence[int]
) -> list[tuple[Ends, list[int], list[tuple[int, ...]]]]:
    rng = random.Random(seed)
    found: list[tuple[Ends, list[int], list[tuple[int, ...]]]] = []
    for _ in range(count):
        n = rng.randint(*units)
        entered = rng.random() < 0.5
        ends: Ends = []
        for _ in range(n):
            t = float(rng.randint(1, 5))
            entry = float(rng.randint(0, int(t) - 1)) if entered else ORIGIN
            ends.append((entry, t, rng.random() < 0.6))
        classes = [rng.randint(0, 1) for _ in range(n)]
        x = [tuple(rng.choice(values) for _ in range(m)) for _ in range(n)]
        found.append((ends, classes, x))
    return found


def pairs(ends: Ends, classes: Sequence[int]) -> list[tuple[int, int]]:
    n = len(ends)
    return [
        (e, h)
        for e in range(n)
        for h in range(n)
        if ends[e][2] and classes[e] == classes[h] and ends[h][0] < ends[e][1] <= ends[h][1]
    ]


def differences(
    ends: Ends, classes: Sequence[int], x: Sequence[Sequence[int]]
) -> list[tuple[int, ...]]:
    return [tuple(a - b for a, b in zip(x[e], x[h], strict=True)) for e, h in pairs(ends, classes)]


def rank(vectors: Sequence[Sequence[int]]) -> int:
    rows: list[list[Fraction]] = []
    for vector in vectors:
        v = [Fraction(a) for a in vector]
        for row in rows:
            p = next(k for k, a in enumerate(row) if a)
            if v[p]:
                f = v[p] / row[p]
                v = [a - f * b for a, b in zip(v, row, strict=True)]
        if any(v):
            rows.append(v)
    return len(rows)


def maximum(vectors: Sequence[Sequence[int]], c: Sequence[int]) -> Fraction:
    """max c·d over {v·d ≥ 0, −1 ≤ d ≤ 1}, by every vertex."""
    m = len(c)
    rows = [tuple(Fraction(a) for a in v) for v in vectors]
    bounds = []
    for i in range(m):
        for sign in (1, -1):
            bounds.append(tuple(Fraction(-sign if k == i else 0) for k in range(m)))
    constraints = [(row, Fraction(0)) for row in rows] + [(row, Fraction(-1)) for row in bounds]
    best: Fraction | None = None
    for chosen in itertools.combinations(constraints, m):
        matrix = [[*row, rhs] for row, rhs in chosen]
        solvable = True
        for col in range(m):
            p = next((r for r in range(col, m) if matrix[r][col]), None)
            if p is None:
                solvable = False
                break
            matrix[col], matrix[p] = matrix[p], matrix[col]
            for r in range(m):
                if r != col and matrix[r][col]:
                    f = matrix[r][col] / matrix[col][col]
                    matrix[r] = [a - f * b for a, b in zip(matrix[r], matrix[col], strict=True)]
        if not solvable:
            continue
        d = [matrix[i][m] / matrix[i][i] for i in range(m)]
        if all(
            sum((a * b for a, b in zip(row, d, strict=True)), Fraction(0)) >= rhs
            for row, rhs in constraints
        ):
            value = sum((Fraction(a) * b for a, b in zip(c, d, strict=True)), Fraction(0))
            best = value if best is None else max(best, value)
    assert best is not None
    return best


def test_a_design_s_columns_are_scaled_to_integers_by_a_power_of_2_each() -> None:
    rows = [[0.1, 1e300, -3.0], [0.25, -(2.0**-1074), 5.0], [0.0, 1.0, 1.5]]
    found = cone.integers(rows, Watch(None))
    for j in range(3):
        column = [Fraction(row[j]) for row in rows]
        scale = (
            Fraction(found[0][j]) / column[0] if column[0] else Fraction(found[1][j]) / column[1]
        )
        assert scale.denominator == 1
        assert scale.numerator & (scale.numerator - 1) == 0
        assert all(Fraction(found[i][j]) == column[i] * scale for i in range(3))
    assert found[1][1] == -1


@pytest.mark.parametrize("seed", range(4))
def test_the_span_of_a_design_s_pairs_is_that_of_each_unit_less_its_component_s_first(
    seed: int,
) -> None:
    for ends, classes, x in designs(seed, 500, units=(1, 9), m=3, values=range(-2, 3)):
        watch = Watch(None)
        found = cone.span(ends, classes, x, watch)
        vectors = differences(ends, classes, x)
        assert len(found.rows) == rank(vectors)
        assert all(found.contains(v) for v in vectors)
        assert cone.gradient(ends, classes, x, watch) == tuple(
            sum(v[k] for v in vectors) for k in range(3)
        )


def test_an_echelon_form_s_pivots_are_the_first_column_of_each_dependency() -> None:
    watch = Watch(None)
    found = cone.Echelon(4, watch)
    for vector in [(0, 2, 2, 0), (0, 1, 1, 0), (3, 0, 0, 6), (1, 1, 1, 2)]:
        found.add(vector)
    assert found.pivots == (0, 1)
    assert not found.holds(0)
    assert not found.holds(2)
    assert found.contains((1, 0, 0, 2))
    assert found.contains((0, -5, -5, 0))


@pytest.mark.parametrize("m", [1, 2, 3])
def test_the_master_s_optimum_is_the_programme_s_and_its_direction_is_in_the_cone(
    m: int,
) -> None:
    rng = random.Random(m)
    for ends, classes, x in designs(10 + m, 150, units=(2, 7), m=m, values=range(-2, 3)):
        watch = Watch(None)
        vectors = differences(ends, classes, x)
        objectives = [cone.gradient(ends, classes, x, watch)]
        objectives += [tuple(rng.randint(-3, 3) for _ in range(m)) for _ in range(2)]
        pool: cone.Pool = {}
        for c in objectives:
            found = cone.optimum(ends, classes, x, c, pool, watch)
            assert all(
                sum(a * b for a, b in zip(v, found.direction, strict=True)) >= 0 for v in vectors
            )
            assert found.positive == (maximum(vectors, c) > 0)


def test_the_master_s_pivots_divide_exactly_and_keep_det_times_the_inverse() -> None:
    watch = Watch(None)
    rng = random.Random(5)
    for _ in range(200):
        m = rng.randint(1, 4)
        master = cone.Master([rng.randint(-5, 5) for _ in range(m)], watch)
        for _ in range(rng.randint(1, 8)):
            master.cut([rng.randint(-4, 4) for _ in range(m)])
        master.solve()
        basis = [[Fraction(master.columns[j][i]) for j in master.basis] for i in range(m)]
        for i in range(m):
            for k in range(m):
                product = sum(
                    (Fraction(master.adjugate[i][r]) * basis[r][k] for r in range(m)), Fraction(0)
                )
                assert product == (master.det if i == k else 0)


def test_at_an_optimum_of_0_the_master_s_basis_certifies_it() -> None:
    watch = Watch(None)
    ends: Ends = [(ORIGIN, 1.0, True), (ORIGIN, 2.0, True), (ORIGIN, 3.0, False)]
    ends.append((ORIGIN, 3.0, False))
    x = [(0, 0), (1, 1), (0, 1), (1, 0)]
    classes = [0, 0, 0, 0]
    pool: cone.Pool = {}
    g = cone.gradient(ends, classes, x, watch)
    master = cone.Master(g, watch)
    found = cone.optimum(ends, classes, x, g, pool, watch)
    assert maximum(differences(ends, classes, x), g) == 0
    assert not found.positive
    for v in pool.values():
        master.cut(v)
    master.solve()
    weights = master.certificate()
    total = [Fraction(0)] * 2
    for j, (numerator, denominator) in weights.items():
        assert Fraction(numerator, denominator) > 0
        for k in range(2):
            total[k] -= Fraction(numerator, denominator) * master.columns[j][k]
    assert total == [-Fraction(a) for a in g]


def reference_classes(
    ends: Ends, strata: Sequence[int], x: Sequence[Sequence[int]]
) -> tuple[set[int], dict[int, int], set[int]]:
    """Estimated, separated (with their signs) and not identified, from the cone alone: its
    implicit equalities (the pairs no d of the cone is strict on) span aff(C)'s complement, and
    d_j's signs on it by max ±d_j."""
    m = len(x[0])
    vectors = differences(ends, strata, x)
    equalities = [v for v in vectors if any(v) and maximum(vectors, v) == 0]
    base = rank(equalities)
    estimated: set[int] = set()
    separated: dict[int, int] = {}
    unidentified: set[int] = set()
    for j in range(m):
        unit = tuple(1 if k == j else 0 for k in range(m))
        if rank([*equalities, unit]) == base:
            estimated.add(j)
            continue
        up = maximum(vectors, unit) > 0
        down = maximum(vectors, tuple(-a for a in unit)) > 0
        if up and down:
            unidentified.add(j)
        else:
            separated[j] = 1 if up else -1
    return estimated, separated, unidentified


def units_of(ends: Ends, strata: Sequence[int], x: Sequence[Sequence[int]]) -> list[Unit]:
    return [
        Unit(s, e, t, ev, tuple(float(v) for v in row))
        for (e, t, ev), s, row in zip(ends, strata, x, strict=True)
    ]


@pytest.mark.parametrize("m", [1, 2, 3])
def test_a_design_s_columns_are_decided_by_its_likelihood_s_recession_cone(m: int) -> None:
    for ends, strata, x in designs(
        20 + m, 120 if m < 3 else 40, units=(2, 6), m=m, values=(-1, 0, 1)
    ):
        units = units_of(ends, strata, x)
        counting = ends[0][0] != ORIGIN
        found = coxph.separated(units, Watch(None), counting=counting)
        estimated, separated, unidentified = reference_classes(ends, strata, x)
        assert set(found.separation) <= set(separated)
        assert all(found.separation[j] == separated[j] for j in found.separation)
        assert set(found.estimated) <= estimated
        numeric = set(found.unidentified) - unidentified - set(found.unscalable)
        assert numeric <= estimated
        assert set(found.estimated) | numeric == estimated


def cohorts(seed: int, count: int) -> list[tuple[Ends, list[int]]]:
    rng = random.Random(seed)
    found: list[tuple[Ends, list[int]]] = []
    for _ in range(count):
        n = rng.randint(3, 14)
        entered = rng.random() < 0.5
        ends: Ends = []
        groups: list[int] = []
        for _ in range(n):
            t = float(rng.randint(1, 6))
            entry = float(rng.randint(0, int(t) - 1)) if entered else ORIGIN
            ends.append((entry, t, rng.random() < 0.6))
            groups.append(rng.randint(0, 3))
        found.append((ends, groups))
    return found


def test_on_cohort_indicators_the_cone_is_the_risk_sets_graph_rule() -> None:
    expected = {
        coxfit.Standing.LOWER: -1,
        coxfit.Standing.HIGHER: 1,
    }
    for ends, groups in cohorts(31, 400):
        eventful = sorted({g for g, (_, _, event) in zip(groups, ends, strict=True) if event})
        if 0 not in eventful:
            continue
        times = sorted({t for _, t, event in ends if event}, reverse=True)
        table = [
            EventTime(
                when,
                tuple(
                    sum(
                        1
                        for (e, t, _), g in zip(ends, groups, strict=True)
                        if g == k and e < when <= t
                    )
                    for k in range(4)
                ),
                tuple(
                    sum(
                        1
                        for (_, t, ev), g in zip(ends, groups, strict=True)
                        if g == k and ev and t == when
                    )
                    for k in range(4)
                ),
            )
            for when in times
        ]
        standings = coxfit.standing(table, 4, 0, Watch(None))
        x = [tuple(1 if g == k else 0 for k in range(1, 4)) for g in groups]
        found = coxph.separated(
            units_of(ends, [0] * len(ends), x), Watch(None), counting=ends[0][0] != ORIGIN
        )
        for group in eventful[1:]:
            k = group - 1
            state = standings[group]
            if state is coxfit.Standing.ESTIMATED:
                assert k in found.estimated or k in found.unidentified
            elif state is coxfit.Standing.APART:
                assert k in found.unidentified
            else:
                assert found.separation.get(k) == expected[state]


@pytest.mark.parametrize("name", sorted(case["name"] for case in REFERENCE["cases"]))
def test_where_nothing_separates_the_finite_part_is_coxph_s_fit(name: str) -> None:
    case = next(c for c in REFERENCE["cases"] if c["name"] == name)
    units = [
        Unit(
            int(case["stratum"][i]),
            ORIGIN if case["entry"] is None else case["entry"][i],
            case["time"][i],
            bool(case["status"][i]),
            tuple(case["x"][i]),
        )
        for i in range(len(case["time"]))
    ]
    found = coxph.separated(units, Watch(None), counting=case["entry"] is not None)
    assert not found.separation
    missing = {j for j, c in enumerate(case["coef"]) if c is None}
    assert missing <= set(found.unidentified)
    assert found.fit is not None
    loose = 1e-5 if {"near", "far"} & set(case["kinds"]) else 1e-10
    for j in found.estimated:
        got = found.fit.coefficients[found.free.index(j)]
        assert math.isclose(got, case["coef"][j], rel_tol=loose, abs_tol=loose)


def dependent(tenths: bool) -> list[Unit]:
    rng = random.Random(8)
    units: list[Unit] = []
    for _ in range(150):
        a, b = rng.randint(-3, 3), rng.randint(-3, 3)
        c = a + rng.gauss(0, 1)
        x1, x2 = (a / 10, b / 10) if tenths else (float(a), float(b))
        units.append(
            Unit(0, ORIGIN, float(rng.randint(1, 40)), rng.random() < 0.7, (x1, x2, x1 + x2, c))
        )
    return units


def test_an_exact_dependency_leaves_every_column_of_it_unidentified_and_the_rest_r_s() -> None:
    exact = coxph.separated(dependent(False), Watch(None), counting=False)
    assert exact.unidentified == (0, 1, 2)
    assert exact.estimated == (3,)
    whole = coxph.fit(dependent(False), Watch(None), counting=False)
    assert whole.dropped == (2,)
    assert exact.fit is not None
    assert whole.fit is not None
    assert exact.fit.coefficients[exact.free.index(3)] == pytest.approx(
        whole.fit.coefficients[whole.kept.index(3)], rel=1e-12
    )


def test_a_dependency_rounding_hides_is_decided_as_r_decides_it() -> None:
    rounded = coxph.separated(dependent(True), Watch(None), counting=False)
    assert 2 in rounded.unidentified
    assert 2 not in rounded.free
    assert set(rounded.estimated) == {0, 1, 3}


def test_a_group_whose_one_event_has_another_at_risk_is_apart_and_the_other_higher() -> None:
    units = [
        Unit(0, 2.5, 3.0, True, (0.0, 0.0)),
        Unit(0, 0.0, 1.0, False, (0.0, 0.0)),
        Unit(0, 0.0, 2.0, True, (1.0, 0.0)),
        Unit(0, 0.0, 1.0, True, (0.0, 1.0)),
    ]
    found = coxph.separated(units, Watch(None), counting=True)
    assert found.separation == {1: 1}
    assert found.unidentified == (0,)
    assert found.estimated == ()


def reference_units(case: dict[str, object]) -> tuple[list[Unit], bool]:
    entries = case["entry"]
    strata, times, status, x = (case[k] for k in ("stratum", "time", "status", "x"))
    assert isinstance(times, list)
    units = [
        Unit(
            int(strata[i]),  # pyright: ignore[reportIndexIssue, reportUnknownArgumentType]
            ORIGIN if entries is None else entries[i],  # pyright: ignore[reportIndexIssue]
            times[i],
            bool(status[i]),  # pyright: ignore[reportIndexIssue]
            tuple(x[i]),  # pyright: ignore[reportIndexIssue, reportUnknownArgumentType]
        )
        for i in range(len(times))  # pyright: ignore[reportUnknownArgumentType]
    ]
    return units, entries is not None


@pytest.mark.parametrize("name", sorted(case["name"] for case in REFERENCE["cases"]))
def test_where_nothing_separates_the_finite_part_s_test_is_coxph_s(name: str) -> None:
    case = next(c for c in REFERENCE["cases"] if c["name"] == name)
    units, counting = reference_units(case)
    found = coxph.separated(units, Watch(None), counting=counting)
    whole = coxph.fit(units, Watch(None), counting=counting)
    if set(found.unidentified) != set(whole.dropped):
        return
    tested = coxph.separated_hazards(units, found, Watch(None))
    expected = coxph.proportional_hazards(units, whole, Watch(None))
    if expected is None:
        assert tested is None
        return
    assert tested is not None
    assert tested.df == expected.df
    assert math.isclose(tested.statistic, expected.statistic, rel_tol=1e-12, abs_tol=1e-12)


def test_on_cohort_indicators_all_estimated_the_test_is_the_risk_table_s() -> None:
    checked = 0
    for ends, groups in cohorts(41, 300):
        if len(set(groups)) < 2 or 0 not in groups:
            continue
        present = sorted(set(groups))
        x = [tuple(1.0 if g == k else 0.0 for k in present[1:]) for g in groups]
        units = [Unit(0, e, t, ev, row) for (e, t, ev), row in zip(ends, x, strict=True)]
        counting = ends[0][0] != ORIGIN
        found = coxph.separated(units, Watch(None), counting=counting)
        if len(found.estimated) != len(present) - 1 or found.fit is None:
            continue
        if not found.fit.converged:
            continue
        [table] = risk_table(
            [[end for end, g in zip(ends, groups, strict=True) if g == k] for k in present]
        )
        fitted = list(range(len(present)))
        reduced = coxfit.fit(table, fitted, 0, Watch(None), counting=counting)
        sizes = [groups.count(k) for k in present]
        expected = coxfit.proportional_hazards(table, fitted, sizes, 0, reduced, Watch(None))
        tested = coxph.separated_hazards(units, found, Watch(None))
        if expected is None or tested is None:
            continue
        assert math.isclose(tested.statistic, expected.statistic, rel_tol=1e-8, abs_tol=1e-10)
        checked += 1
    assert checked > 50


def test_the_finite_part_s_estimates_and_test_do_not_depend_on_the_columns_order() -> None:
    rng = random.Random(12)
    units: list[Unit] = []
    for _ in range(60):
        z = round(rng.gauss(0, 1), 3)
        units.append(Unit(0, ORIGIN, float(rng.randint(10, 60)), rng.random() < 0.8, (z, 0.0, 0.0)))
    for t, event, b in [(1, True, 1), (2, True, 1), (3, True, 0), (4, False, 0), (5, False, 0)]:
        units.append(Unit(0, ORIGIN, float(t), event, (0.3, 1.0, float(b))))
    order = (2, 0, 1)
    permuted = [
        Unit(u.stratum, u.entry, u.time, u.event, tuple(u.x[j] for j in order)) for u in units
    ]
    first = coxph.separated(units, Watch(None), counting=False)
    second = coxph.separated(permuted, Watch(None), counting=False)
    assert {order[j] for j in second.estimated} == set(first.estimated)
    assert first.fit is not None
    assert second.fit is not None
    for j in first.estimated:
        k = order.index(j)
        a = first.fit.coefficients[first.free.index(j)]
        b = second.fit.coefficients[second.free.index(k)]
        assert math.isclose(a, b, rel_tol=1e-9)
    one = coxph.separated_hazards(units, first, Watch(None))
    two = coxph.separated_hazards(permuted, second, Watch(None))
    assert one is not None
    assert two is not None
    assert math.isclose(one.statistic, two.statistic, rel_tol=1e-9)


def test_a_column_r_drops_before_its_exact_partner_leaves_the_others_r_s() -> None:
    rng = random.Random(7)
    units: list[Unit] = []
    for _ in range(300):
        x1, x2 = float(rng.randint(0, 10**8)), float(rng.randint(0, 5))
        x4 = rng.gauss(0, 1)
        t = rng.expovariate(math.exp(0.5 * x2 + 0.3 * x4))
        units.append(Unit(0, ORIGIN, t, rng.random() < 0.8, (x4, x1, x1 + x2, x2)))
    whole = coxph.fit(units, Watch(None), counting=False)
    assert whole.dropped == (2,)
    found = coxph.separated(units, Watch(None), counting=False)
    assert found.unidentified == (1, 2, 3)
    assert found.estimated == (0,)
    assert found.fit is not None
    assert whole.fit is not None
    assert math.isclose(
        found.fit.coefficients[found.free.index(0)],
        whole.fit.coefficients[whole.kept.index(0)],
        rel_tol=1e-9,
    )


def windows(rng: random.Random, low: float, high: float, n: int, rate: float) -> Ends:
    found: Ends = []
    for _ in range(n):
        entry = low + rng.random() * 5
        end = entry + 0.1 + round(rng.expovariate(rate), 2)
        found.append((entry, min(end, high), end < high and rng.random() < 0.85))
    return found


def test_a_cohort_never_at_risk_beside_the_others_is_not_identified_the_rest_d357_s() -> None:
    rng = random.Random(3)
    reference = windows(rng, 0, 50, 40, 0.08) + windows(rng, 80, 130, 40, 0.08)
    other = windows(rng, 0, 50, 40, 0.12) + windows(rng, 80, 130, 40, 0.3)
    apart = windows(rng, 55, 75, 60, 0.3)
    [table] = risk_table([reference, other, apart])
    standings = coxfit.standing(table, 3, 0, Watch(None))
    assert standings[2] is coxfit.Standing.APART
    reduced = coxfit.fit(table, [0, 1], 0, Watch(None), counting=True)
    units = [
        Unit(0, e, t, ev, (1.0 if g == 1 else 0.0, 1.0 if g == 2 else 0.0))
        for g, group in enumerate([reference, other, apart])
        for e, t, ev in group
    ]
    found = coxph.separated(units, Watch(None), counting=True)
    assert found.estimated == (0,)
    assert found.unidentified == (1,)
    assert found.fit is not None
    assert math.isclose(found.fit.coefficients[0], reduced.coefficients[0], rel_tol=1e-9)
    whole = coxph.fit(units, Watch(None), counting=True)
    assert whole.dropped == (1,)
    tested = coxph.separated_hazards(units, found, Watch(None))
    expected = coxph.proportional_hazards(units, whole, Watch(None))
    assert tested is not None
    assert expected is not None
    assert math.isclose(tested.statistic, expected.statistic, rel_tol=1e-12)


CONE = json.loads((Path(__file__).parent / "reference" / "cone.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", [case["name"] for case in CONE["cases"]])
def test_r_s_fits_from_every_start_agree_with_the_cone_s_columns(name: str) -> None:
    case = next(c for c in CONE["cases"] if c["name"] == name)
    units, counting = reference_units(case)
    found = coxph.separated(units, Watch(None), counting=counting)
    assert found.fit is None or found.fit.converged
    fits = [fit for fit in case["fits"] if fit is not None]
    assert fits
    kept = [fit for fit in fits if all(fit[j] is not None for j in found.separation)]
    for j in found.estimated:
        got = found.fit.coefficients[found.free.index(j)] if found.fit else math.nan
        for fit in kept:
            if fit[j] is not None:
                assert math.isclose(got, fit[j], rel_tol=1e-4, abs_tol=1e-4)
    for j, sign in found.separation.items():
        for fit in fits:
            assert fit[j] is None or fit[j] * sign > 5
    if found.fit is not None:
        supremum = found.fit.loglik[1]
        logliks = [
            loglik
            for fit, loglik in zip(case["fits"], case["loglik"], strict=True)
            if fit is not None and all(fit[j] is not None for j in found.separation)
        ]
        assert all(loglik <= supremum + 1e-9 * abs(supremum) for loglik in logliks)
        if logliks:
            assert max(logliks) >= supremum - 1e-6 * abs(supremum)
    for j, value in enumerate(case["finite"] or []):
        if value is not None:
            assert found.fit is not None
            assert math.isclose(found.fit.coefficients[found.free.index(j)], value, rel_tol=1e-9)
    if case["hazards"] is not None:
        tested = coxph.separated_hazards(units, found, Watch(None))
        assert tested is not None
        assert tested.df == len(found.estimated)
        assert math.isclose(tested.statistic, case["hazards"], rel_tol=1e-9)


def _reads(monkeypatch: pytest.MonkeyPatch, every: int) -> list[int]:
    reads: list[int] = []

    def clock() -> float:
        reads.append(1)
        return 0.0

    monkeypatch.setattr(timetoevent, "time", type("Clock", (), {"monotonic": staticmethod(clock)}))
    monkeypatch.setattr(timetoevent, "LOOK_EVERY", every)
    return reads


class Counted(Watch):
    __slots__ = ("total",)

    def __init__(self, ends: float | None) -> None:
        super().__init__(ends)
        self.total = 0

    def spend(self, work: int = 1) -> None:
        self.total += work
        super().spend(work)


def separable(n: int, m: int) -> tuple[Ends, list[int], list[tuple[int, ...]]]:
    rng = random.Random(n)
    ends: Ends = [(ORIGIN, float(i + 1), rng.random() < 0.8) for i in range(n)]
    x = [(-(i + 1), *(rng.randint(-9, 9) for _ in range(m - 1))) for i in range(n)]
    return ends, [0] * n, x


def test_every_exact_pass_spends_a_unit_s_columns_at_least() -> None:
    ends, classes, x = separable(300, 4)
    for run, floor in (
        (lambda w: cone.span(ends, classes, x, w), 300),
        (lambda w: cone.gradient(ends, classes, x, w), 300 * 4),
        (lambda w: cone.violated(ends, classes, x, (1, 0, 0, 0), w), 300 * 4),
        (lambda w: cone.cosets(classes, x, cone.Echelon(4, w), w), 300 * 4),
        (lambda w: cone.integers([[float(v) for v in row] for row in x], w), 300 * 4),
    ):
        watch = Counted(None)
        run(watch)
        assert watch.total >= floor


def test_the_cone_s_programmes_and_levels_stop_at_the_call_s_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ends, classes, x = separable(300, 4)
    units = units_of(ends, classes, x)
    past = time.monotonic() - 1
    for run in (
        lambda w: cone.optimum(ends, classes, x, (1, 1, 0, 0), {}, w),
        lambda w: cone.levels(ends, classes, x, {}, w),
        lambda w: coxph.separated(units, w, counting=False),
    ):
        with pytest.raises(CallerDeadline):
            run(Watch(past))
    with monkeypatch.context() as patch:
        _reads(patch, 64)
        patch.setattr(
            timetoevent, "time", type("Clock", (), {"monotonic": staticmethod(lambda: 2.0)})
        )
        for run in (
            lambda w: cone.span(ends, classes, x, w),
            lambda w: cone.gradient(ends, classes, x, w),
            lambda w: cone.violated(ends, classes, x, (1, 0, 0, 0), w),
        ):
            with pytest.raises(CallerDeadline):
                run(Watch(1.0))


def entering_by_rule(master: cone.Master, bland: bool) -> int:
    """The column Bland's rule (the least improving index) or Dantzig's (the most improving,
    the least index among ties) enters, from the basis inverse in Fractions."""
    m = master.m
    costs = [Fraction(master.costs[j]) for j in master.basis]
    y = [
        sum((costs[i] * Fraction(master.adjugate[i][k], master.det) for i in range(m)), Fraction(0))
        for k in range(m)
    ]
    reduced = {
        j: master.costs[j] - sum((y[k] * column[k] for k in range(m)), Fraction(0))
        for j, column in enumerate(master.columns)
        if j not in master.basis
    }
    improving = [j for j, r in reduced.items() if r < 0]
    if bland:
        return min(improving)
    best = min(reduced[j] for j in improving)
    return min(j for j in improving if reduced[j] == best)


def objective(master: cone.Master) -> Fraction:
    """The master's objective at its basis: Σ cost·x over the basis, x = adjugate·c / det."""
    return sum(
        (
            Fraction(master.costs[j] * master.values[i], master.det)
            for i, j in enumerate(master.basis)
        ),
        Fraction(0),
    )


def test_the_master_takes_bland_s_rule_after_its_degenerate_pivots_and_dantzig_s_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cone.DEGENERATE == 20
    monkeypatch.setattr(cone, "DEGENERATE", 1)
    rng = random.Random(14)
    seen: list[bool] = []
    for _ in range(300):
        m = rng.randint(2, 4)
        master = cone.Master([rng.choice([0, 0, 1, -1]) for _ in range(m)], Watch(None))
        for _ in range(rng.randint(3, 9)):
            master.cut([rng.randint(-2, 2) for _ in range(m)])
        pivot = master._pivot  # pyright: ignore[reportPrivateUsage]
        last = [False]

        def spy(
            entering: int,
            master: cone.Master = master,
            pivot: Callable[[int], bool] = pivot,
            last: list[bool] = last,
        ) -> bool:
            assert entering == entering_by_rule(master, last[0])
            before = objective(master)
            moved = pivot(entering)
            assert moved == (objective(master) < before)
            last[0] = not moved
            seen.append(last[0])
            return moved

        monkeypatch.setattr(master, "_pivot", spy)
        master.solve()
    assert any(seen)
    assert not all(seen)


def test_a_stratum_whose_units_share_their_covariates_is_in_the_test_as_r_pools_it() -> None:
    rng = random.Random(2)
    units: list[Unit] = []
    for _ in range(80):
        z, b = round(rng.gauss(0, 1), 3), float(rng.random() < 0.5)
        units.append(
            Unit(0, ORIGIN, round(rng.expovariate(1.0) * 10, 2), rng.random() < 0.8, (z, b))
        )
    for _ in range(80):
        units.append(
            Unit(1, ORIGIN, round(rng.expovariate(0.3) * 10, 2), rng.random() < 0.8, (0.5, 1.0))
        )
    found = coxph.separated(units, Watch(None), counting=False)
    assert found.estimated == (0, 1)
    whole = coxph.fit(units, Watch(None), counting=False)
    tested = coxph.separated_hazards(units, found, Watch(None))
    expected = coxph.proportional_hazards(units, whole, Watch(None))
    assert tested is not None
    assert expected is not None
    assert math.isclose(tested.statistic, expected.statistic, rel_tol=1e-12)


def test_a_column_the_finite_part_cannot_scale_is_not_identified_and_has_no_estimate() -> None:
    tiny = 1e-160
    units = [
        Unit(0, 0.0, 1.0, True, (0.0, 0.0)),
        Unit(0, 0.0, 2.0, True, (tiny, 0.0)),
        Unit(0, 0.0, 3.0, False, (0.0, 0.0)),
        Unit(0, 0.0, 3.0, True, (tiny, 0.0)),
        Unit(0, 0.0, 5.0, False, (1.0, 1.0)),
        Unit(0, 0.0, 5.0, False, (2.0, 1.0)),
    ]
    assert coxph.columns_of(units, Watch(None), counting=True).kept == (0, 1)
    found = coxph.separated(units, Watch(None), counting=True)
    assert found.estimated == ()
    assert 0 in found.unidentified
    assert 0 not in found.separation
    assert coxph.separated_hazards(units, found, Watch(None)) is None


def test_a_column_is_separated_or_not_identified_never_both() -> None:
    rng = random.Random(4)
    units: list[Unit] = []
    for _ in range(60):
        z = round(rng.gauss(0, 1), 3)
        near = z + rng.gauss(0, 1) * 1e-9
        units.append(Unit(0, ORIGIN, float(rng.randint(10, 60)), rng.random() < 0.8, (near, z, z)))
    units += [
        Unit(0, ORIGIN, 1.0, True, (0.0, 1.0, 0.0)),
        Unit(0, ORIGIN, 2.0, True, (0.0, 1.0, 0.0)),
        Unit(0, ORIGIN, 3.0, False, (0.0, 0.0, 0.0)),
    ]
    units += [Unit(0, ORIGIN, 4.0 + k, True, (0.0, 0.0, 0.0)) for k in range(3)]
    units += [Unit(0, ORIGIN, 9.5, False, (0.0, 0.0, float(k))) for k in (1, 2, 5)]
    found = coxph.separated(units, Watch(None), counting=False)
    assert found.separation == {1: 1, 2: -1}
    assert not set(found.separation) & set(found.unidentified)
    assert not set(found.estimated) & set(found.unidentified)
    assert set(found.estimated) <= set(found.free)


def test_a_column_r_drops_near_a_separated_one_is_not_identified_and_not_fitted() -> None:
    rng = random.Random(6)
    units: list[Unit] = []
    for i in range(80):
        z = round(rng.gauss(0, 1), 3)
        units.append(
            Unit(0, ORIGIN, float(10 + i), rng.random() < 0.8, (-(10.0 + i), z, z + 1e-12 * i))
        )
    whole = coxph.columns_of(units, Watch(None), counting=False)
    assert whole.dropped == (2,)
    found = coxph.separated(units, Watch(None), counting=False)
    assert found.separation == {0: 1}
    assert 2 in found.unidentified
    assert 2 not in found.free
    assert 2 not in found.separation


def test_the_finite_part_s_test_does_not_depend_on_which_dependent_column_is_fixed() -> None:
    rng = random.Random(5)
    rows: list[tuple[int, float, bool, tuple[float, ...]]] = []
    for _ in range(60):
        e, ab = round(rng.gauss(0, 1), 3), float(rng.randint(0, 2))
        rows.append((0, round(rng.expovariate(1.0) * 10, 2), rng.random() < 0.8, (e, ab, ab, 0.0)))
    for _ in range(60):
        a = float(rng.randint(0, 2))
        rows.append(
            (1, round(rng.expovariate(0.4) * 10, 2), rng.random() < 0.8, (0.0, a, 1.0, 1.0 - a))
        )
    statistics: list[float] = []
    for order in ((0, 1, 2, 3), (0, 3, 2, 1), (0, 2, 3, 1)):
        units = [Unit(g, ORIGIN, t, ev, tuple(x[j] for j in order)) for g, t, ev, x in rows]
        found = coxph.separated(units, Watch(None), counting=False)
        assert found.estimated == (0,)
        tested = coxph.separated_hazards(units, found, Watch(None))
        assert tested is not None
        statistics.append(tested.statistic)
    assert all(math.isclose(v, statistics[0], rel_tol=1e-9) for v in statistics)


def test_a_non_finite_value_leaves_its_column_out_as_unscalable() -> None:
    for bad in (math.inf, -math.inf, math.nan):
        units = [
            Unit(0, ORIGIN, float(i + 1), i % 3 != 1, (float(i % 3), bad if i == 2 else float(i)))
            for i in range(12)
        ]
        assert coxph.columns_of(units, Watch(None), counting=False).unscalable == (1,)
        found = coxph.separated(units, Watch(None), counting=False)
        assert found.unscalable == (1,)
        assert 1 not in found.unidentified


def test_an_unscalable_column_is_only_unscalable_and_the_test_is_r_s() -> None:
    rng = random.Random(2)
    units: list[Unit] = []
    for i in range(80):
        z, b = round(rng.gauss(0, 1), 3), float(rng.random() < 0.5)
        x = (z, b, 1e200 if i == 0 else 0.0)
        units.append(Unit(0, ORIGIN, round(rng.expovariate(1.0) * 10, 2), rng.random() < 0.8, x))
    for _ in range(80):
        x = (0.5, 1.0, 0.0)
        units.append(Unit(1, ORIGIN, round(rng.expovariate(0.3) * 10, 2), rng.random() < 0.8, x))
    found = coxph.separated(units, Watch(None), counting=False)
    assert found.unscalable == (2,)
    assert found.unidentified == ()
    assert found.estimated == (0, 1)
    whole = coxph.fit(units, Watch(None), counting=False)
    tested = coxph.separated_hazards(units, found, Watch(None))
    expected = coxph.proportional_hazards(units, whole, Watch(None))
    assert tested is not None
    assert expected is not None
    assert math.isclose(tested.statistic, expected.statistic, rel_tol=1e-12)


def test_a_rounding_residue_in_the_finite_part_leaves_an_exact_separation_separated() -> None:
    ends: Ends = [
        (1.0, 5.0, False),
        (5.0, 20.0, True),
        (4.0, 7.0, True),
        (6.0, 20.0, True),
        (6.0, 19.0, True),
        (1.0, 19.0, False),
        (12.0, 17.0, True),
        (2.0, 17.0, True),
        (7.0, 10.0, True),
    ]
    x = [
        (-1.63, -1.63, -0.01, 0.78, 0.24),
        (0.25, 0.25, -0.0, 1.27, 2.73),
        (0.78, 1.78, -3.2, 0.43, -0.16),
        (-0.03, -0.03, -1.09, 1.59, -1.29),
        (0.98, 0.98, 1.35, -0.65, 1.05),
        (-1.01, -1.01, 1.58, -0.06, -0.48),
        (0.65, 1.65, -1.97, -0.83, 0.72),
        (-0.21, 0.79, 0.72, -0.78, -0.39),
        (0.57, 1.5699999999999998, -1.54, -0.4, 0.1),
    ]
    units = [Unit(0, e, t, ev, row) for (e, t, ev), row in zip(ends, x, strict=True)]
    found = coxph.separated(units, Watch(None), counting=True)
    assert found.separation.get(1) == 1
    assert 1 not in found.unidentified
