"""The statistical methods of ``compare.existence`` (SPEC §9.3, §9.5; D321).

Each is a plain function of counts, deterministic as §9.3 requires: nothing here reads DuckDB's
aggregates, every sum is correctly rounded (``math.fsum``), and the order of every operation is
fixed. The methods are those R computes with the calls the reference fixture names
(``tests/core/analyses/reference/existence.R``), and they agree with it to 1e-10 relative:

- ``z``: the standard normal quantile for a two-sided level, as R's ``qnorm((1 + level) / 2)``
  (Wichura's AS 241, as ``statistics.NormalDist`` implements it).
- ``wilson``: the Wilson score interval without continuity correction, written as R's
  ``prop.test(correct = FALSE)`` computes it, clipped to [0, 1].
- ``newcombe``: Newcombe's hybrid score interval for a difference of proportions (method 10 of
  Newcombe 1998), from the two Wilson intervals.
- ``katz``: the Katz log interval for a ratio of proportions, defined when both numerators are
  above zero.
- ``fisher``: Fisher's exact test of a 2x2 table, two-sided as R's ``fisher.test``: the
  probabilities of the tables with the observed margins no larger than the observed one's, to a
  relative 1e-7. They are computed by the ratio of consecutive hypergeometric probabilities from
  the mode outwards, in doubles, with basic operations only, so every platform computes the same
  bits, and summed with ``math.fsum``.
- ``chi_squared``: Pearson's test of independence of a table without continuity correction, as
  R's ``chisq.test(correct = FALSE)``, whose upper tail is ``upper_gamma``.
- ``upper_gamma``: the regularised upper incomplete gamma function Q(a, x), by its series below
  a + 1 and by its continued fraction (modified Lentz) above.
- ``benjamini_hochberg``: q-values as R's ``p.adjust(method = "BH")``.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

_STANDARD = NormalDist()
_RELATIVE = 1 + 1e-7
"""R's ``fisher.test`` counts a table as no more probable than the observed one within this
relative tolerance."""
_EPSILON = 1e-15
"""The relative size of a term at which the incomplete gamma's series and fraction stop."""
_TINY = 1e-300
_ITERATIONS = 100_000
_FLOOR = -1100
"""The binary exponent of a hypergeometric probability, relative to the mode's, below which
the tails are left out: a p-value that small is below the least double, and they cannot change a
sum with the mode's in it."""


def z(level: float) -> float:
    """The two-sided standard normal quantile of ``level``, 0 < level < 1."""
    if not 0 < level < 1:
        raise ValueError("a level lies strictly between 0 and 1")
    return _STANDARD.inv_cdf((1 + level) / 2)


def wilson(numerator: int, denominator: int, level: float) -> tuple[float, float]:
    """The Wilson score interval of ``numerator / denominator``, as R's ``prop.test(correct =
    FALSE)`` writes it; ``denominator`` is above zero."""
    if not 0 <= numerator <= denominator or denominator <= 0:
        raise ValueError("a proportion's numerator lies from 0 to its positive denominator")
    quantile = z(level)
    estimate = numerator / denominator
    half = quantile * quantile / (2 * denominator)
    spread = estimate * (1 - estimate) / denominator + half / (2 * denominator)
    upper = (
        1.0 if estimate >= 1 else (estimate + half + quantile * math.sqrt(spread)) / (1 + 2 * half)
    )
    lower = (
        0.0 if estimate <= 0 else (estimate + half - quantile * math.sqrt(spread)) / (1 + 2 * half)
    )
    return max(lower, 0.0), min(upper, 1.0)


def newcombe(
    numerator: int, denominator: int, versus: tuple[int, int], level: float
) -> tuple[float, float, float]:
    """The difference of the proportion ``numerator / denominator`` less the reference's
    (``versus``, its numerator and denominator), and Newcombe's hybrid score interval around it;
    both denominators are above zero."""
    first = numerator / denominator
    second = versus[0] / versus[1]
    low1, high1 = wilson(numerator, denominator, level)
    low2, high2 = wilson(versus[0], versus[1], level)
    difference = first - second
    lower = difference - math.sqrt((first - low1) ** 2 + (high2 - second) ** 2)
    upper = difference + math.sqrt((high1 - first) ** 2 + (second - low2) ** 2)
    return difference, lower, upper


def katz(
    numerator: int, denominator: int, versus: tuple[int, int], level: float
) -> tuple[float, float, float]:
    """The ratio of the proportion ``numerator / denominator`` to the reference's, and its Katz
    log interval; both numerators are above zero."""
    if numerator <= 0 or versus[0] <= 0:
        raise ValueError("a risk ratio's Katz interval needs both numerators above zero")
    ratio = (numerator / denominator) / (versus[0] / versus[1])
    error = math.sqrt(1 / numerator - 1 / denominator + 1 / versus[0] - 1 / versus[1])
    quantile = z(level)
    logged = math.log(ratio)
    return ratio, math.exp(logged - quantile * error), math.exp(logged + quantile * error)


def fisher(table: tuple[tuple[int, int], tuple[int, int]]) -> float:
    """The two-sided p-value of Fisher's exact test of a 2x2 table of counts, as R's
    ``fisher.test`` gives it. Each probability, relative to the mode's, is held as a mantissa and
    a binary exponent (``math.frexp``), which scaling by powers of two leaves exact, so that one
    far in the tail is summed where a double would be 0, and the p-value is 0 only below the
    least double (D321)."""
    (a, b), (c, d) = table
    if min(a, b, c, d) < 0:
        raise ValueError("a table holds counts")
    first, second, drawn = a + c, b + d, a + b
    low, high = max(0, drawn - second), min(drawn, first)
    if low == high:
        return 1.0
    mode = _mode(first, second, drawn, low, high)
    weights: dict[int, tuple[float, int]] = {mode: math.frexp(1.0)}
    mantissa, exponent = weights[mode]
    for x in range(mode, high):
        scaled, shift = math.frexp(
            mantissa * ((first - x) * (drawn - x) / ((x + 1) * (second - drawn + x + 1)))
        )
        mantissa, exponent = scaled, exponent + shift
        if exponent < _FLOOR:
            break
        weights[x + 1] = (mantissa, exponent)
    mantissa, exponent = weights[mode]
    for x in range(mode, low, -1):
        scaled, shift = math.frexp(
            mantissa * (x * (second - drawn + x) / ((first - x + 1) * (drawn - x + 1)))
        )
        mantissa, exponent = scaled, exponent + shift
        if exponent < _FLOOR:
            break
        weights[x - 1] = (mantissa, exponent)
    if a not in weights:
        return 0.0
    observed, base = weights[a]
    bound = observed * _RELATIVE
    total = math.fsum(math.ldexp(*weights[x]) for x in sorted(weights))
    kept = math.fsum(
        relative
        for x in sorted(weights)
        if weights[x][1] - base <= 2
        and (relative := math.ldexp(weights[x][0], weights[x][1] - base)) <= bound
    )
    return min(1.0, math.ldexp(kept / total, base))


def _mode(first: int, second: int, drawn: int, low: int, high: int) -> int:
    """The most probable count of the first column's hypergeometric distribution: the floor of
    (drawn + 1)(first + 1) / (first + second + 2), within the support."""
    return min(high, max(low, (drawn + 1) * (first + 1) // (first + second + 2)))


@dataclass(frozen=True)
class ChiSquared:
    """Pearson's test of a table: its statistic, degrees of freedom and upper-tail p-value."""

    statistic: float
    df: int
    p: float
    least_expected: float
    """The smallest expected count, which ``SMALL_N`` reads (§8.3)."""


def chi_squared(table: Sequence[Sequence[int]]) -> ChiSquared:
    """Pearson's chi-squared test of independence of a table of counts without continuity
    correction, as R's ``chisq.test(correct = FALSE)``; every row and column total is above
    zero, and the table has at least two rows and two columns."""
    rows = [sum(row) for row in table]
    columns = [sum(column) for column in zip(*table, strict=True)]
    total = sum(rows)
    if len(rows) < 2 or len(columns) < 2 or min(rows) <= 0 or min(columns) <= 0:
        raise ValueError("a table with two or more rows and columns, none of them empty")
    expected = [[row * column / total for column in columns] for row in rows]
    terms = [
        (observed - wanted) ** 2 / wanted
        for column in range(len(columns))
        for observed, wanted in (
            (table[row][column], expected[row][column]) for row in range(len(rows))
        )
    ]
    statistic = math.fsum(terms)
    df = (len(rows) - 1) * (len(columns) - 1)
    return ChiSquared(
        statistic=statistic,
        df=df,
        p=upper_gamma(df / 2, statistic / 2),
        least_expected=min(min(row) for row in expected),
    )


def upper_gamma(a: float, x: float) -> float:
    """Q(a, x), the regularised upper incomplete gamma function, for a > 0 and x ≥ 0: the upper
    tail of a chi-squared variable with 2a degrees of freedom at 2x."""
    if a <= 0 or x < 0:
        raise ValueError("Q(a, x) is defined here for a > 0 and x >= 0")
    if x == 0:
        return 1.0
    scale = a * math.log(x) - x - math.lgamma(a)
    if x < a + 1:
        term = total = 1 / a
        n = a
        for _ in range(_ITERATIONS):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * _EPSILON:
                return max(0.0, 1 - total * math.exp(scale))
        raise ArithmeticError("the incomplete gamma series did not converge")
    b = x + 1 - a
    c = 1 / _TINY
    d = 1 / b
    h = d
    for i in range(1, _ITERATIONS):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        c = b + an / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < _EPSILON:
            return math.exp(scale) * h
    raise ArithmeticError("the incomplete gamma fraction did not converge")


def benjamini_hochberg(p: Sequence[float]) -> list[float]:
    """Benjamini–Hochberg q-values of ``p``, in the order given, as R's ``p.adjust(p, "BH")``."""
    n = len(p)
    order = sorted(range(n), key=lambda index: p[index], reverse=True)
    found = [0.0] * n
    least = math.inf
    for rank, index in zip(range(n, 0, -1), order, strict=True):
        least = min(least, n / rank * p[index])
        found[index] = min(1.0, least)
    return found


__all__ = [
    "ChiSquared",
    "benjamini_hochberg",
    "chi_squared",
    "fisher",
    "katz",
    "newcombe",
    "upper_gamma",
    "wilson",
    "z",
]
