"""The statistical methods of the core's analyses (SPEC §9.3, §9.5; D321, D328).

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

``summary.distribution``'s (D328) take a multiset of values, each distinct value in increasing
order with the number of units that have it (``Weighted``), and agree with R's calls in the same
fixture:

- ``mean``: the exact mean, correctly rounded (``engine.variables.exact_mean``: the exact sum
  of the values as integers over a power of two, divided once by Python's correctly rounded
  division of integers), which R's ``mean`` matches to 1e-10 and exactly but under catastrophic
  cancellation, where R's two passes are the inexact ones.
- ``sd``: the sample standard deviation, as R's ``sd``: the square root of ``math.fsum`` of
  each squared deviation from ``mean`` over n − 1; not estimable for fewer than two values or
  every value the same (§9.5, ``zero_variance``).
- ``quantile``: R's default (``type = 7``), as ``quantile.default`` writes it: the order
  statistics at ``floor`` and ``ceiling`` of 1 + (n − 1)·p, weighted ``(1 − h)`` and ``h``.

The squares of ``sd`` are taken over the values scaled by the power of two that brings the
largest magnitude into [0.5, 1), and scaled back: that is exact, so it gives the same bits
wherever the unscaled sums neither overflow nor underflow, and finite results where they would
(a deviation of 1e200 squared).

``compare.columns``' (D338) take the same multisets, and agree with R's calls in
``tests/core/analyses/reference/columns.R``, degrees of freedom up to 1e8 and ``pbeta``'s
parameters up to 8e6 included:

- ``beta_tail``: the regularised incomplete beta function I_x(a, b), on the side of the mode
  where its continued fraction converges: for a of 15 or more, b of 40 or less and x above 0.7,
  by the asymptotic expansion of DiDonato and Morris (TOMS 708's ``bgrat``), b first reduced to
  (0, 1] by a recurrence of positive terms, and otherwise by the continued fraction (modified
  Lentz), whose first terms cancel for large a near 1, where the expansion is exact; its leading
  factor x^a·(1 − x)^b / B(a, b) is written, for large a or b, by Stirling's series with
  ``log1p`` of a ratio less 1 where that is below ½ (TOMS 708's ``rlog1``), so that no
  difference of large log-gammas cancels. ``t_tail`` (Student's two-sided tail), ``t_quantile``
  (its inverse, a safeguarded Newton iteration) and ``f_tail`` (the F upper tail) are its special
  cases, as R's ``pt``, ``qt`` and ``pf`` give them. Against R 4.3.3 over 16,000 points (a and b
  from 0.05 to 1e7, degrees of freedom from 1 to 1e8), t and F tails agree to 3e-13 relative,
  t quantiles at levels to 0.999 to 1e-13, and I_x(a, b) to 3e-13 where one parameter is 40
  or less (against mpmath, over 258 points of the expansion's region) and to 2e-10 where both
  exceed it near the mode (the continued fraction's, which Welch's tests never reach); ``qt``
  is itself inexact at levels beyond 0.999 (1e-7 relative at 1 − 1e-9, where ``t_quantile`` is
  within 2e-14 of the exact quantile), so the fixture's levels stop there.
- ``moments``: a group's n, mean and standard deviation, computed once, the mean also exact
  (a ``Fraction``) and the deviation as a mantissa and a binary exponent, over the values scaled
  by a power of two as ``sd``'s are, less the square of the mean's rounding, so that a spread of
  a few ulps is exact to rounding; a deviation below the least double is none, and one beyond a
  double infinite. A value many units hold adds one exact product, so the cost is the distinct
  values'.
- ``welch``: Welch's two-sample *t* test, as R's ``t.test(var.equal = FALSE)``, the statistic
  the first group's mean less the second's, the Welch–Satterthwaite degrees of freedom, and the
  interval of the difference at a level; ``welch_anova``: Welch's one-way test, as R's
  ``oneway.test(var.equal = FALSE)``. Welch's *t* is the same whatever power of two scales every
  value, so it is computed over the errors scaled by the largest group's exponent; the one-way
  test takes its weights, their shares and the weighted centre as exact rationals and each
  group's distance from the centre on that group's own scale, so groups whose spreads differ by
  any power of two a double holds are tested. Each agrees to 1e-12 with the exact rational
  statistic (t², F and both degrees of freedom are rationals of the values) over groups scaled
  from 2^-1074 to 1, and neither overflows unless its statistic truly exceeds a double.
- ``mann_whitney``: the Wilcoxon rank-sum test, as R's ``wilcox.test(correct = TRUE)``: ``W`` of
  the first group, exact when both groups hold fewer than 50 values and no two values of either
  are tied (the counts of the Gaussian binomial coefficient, as integers), else the normal
  approximation with its correction for ties and for continuity.
- ``kruskal_wallis``: as R's ``kruskal.test``, corrected for ties; its tail is ``upper_gamma``.
  Ranks are mid-ranks of the values as doubles, as R's ``rank`` gives them, and the correction
  for ties groups the values that R's ``as.character`` prints alike, as R's ``table`` does:
  ``_printed`` is R 4.3's ``formatReal`` at 15 digits, its long double scaling emulated exactly,
  fixed notation wherever it is no wider (so 0.1 + 0.2 and 0.3 are "0.3", while 1234567890123456
  and 1234567890123457 print every digit and are two), and it agrees with R's own strings over
  420,000 doubles of every magnitude, exact halves at the 16th digit, subnormals and neighbours
  of powers of ten included. It is x86-64 R's: ``scientific`` scales in a long double, x87's
  64-bit mantissa there, and an R without one prints some values otherwise, so its ties differ:
  on arm64 macOS (a long double is a double, ``KP_MAX`` 22) about 1 in 100 of values near a
  rounding half, on aarch64 Linux (a quad) a few in 10⁵; the fixture records the platform that
  wrote it (``columns.R``). Where every value prints alike R's statistic is infinite (it
  divides by a correction of 0), and here it is not computed (``zero_variance``). Both rank
  tests' sums are integers of doubled mid-ranks, and their statistics exact rationals, rounded
  once. They, ``moments``, ``quantile`` and ``bootstrap_medians`` check the call's deadline every
  65,536 values (``_CHUNK``), and ``checked_sort`` sorts in runs of that many, merged two at a
  time, so that no pass over five million values goes unchecked (D336).
- ``bootstrap_medians``: the medians of resamples of a group, drawn with replacement, each drawn
  from its exact distribution (the resample's order statistics are the values at ⌊n·U₍ⱼ₎⌋ of
  the sorted values, U₍ⱼ₎ the uniform order statistics), so that a replicate costs the same
  whatever the group's size; ``percentile_interval`` takes the ⌈B·alpha/2⌉-th and
  ⌈B·(1 − alpha/2)⌉-th of B replicates (§9.5).

Determinism (§9.3) is of the inputs: the bootstrap's stream is seeded from them (D338), and no
clock or state is read. ``fisher``'s bits are the same on every platform, since it uses basic
operations only; the methods of comparisons also call ``math.log``, ``log1p``, ``expm1``,
``lgamma`` and ``sqrt``, of which only ``sqrt`` is correctly rounded everywhere, so a platform's
library can change a p-value's last bit, or flip one acceptance of the gamma sampler (about 1e-13
a call), whose bounds are values of the data or their midpoints either way.
"""

import bisect
import math
import random
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import accumulate, chain, repeat
from operator import mul
from statistics import NormalDist
from typing import Any, cast

from aibi.core.engine.variables import exact_mean, exact_sum
from aibi.core.engine.worker import CallerDeadline

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
    return math.exp(scale) * _gamma_fraction(a, x)


def _gamma_fraction(a: float, x: float) -> float:
    """Q(a, x)·Γ(a)·e^x / x^a for x ≥ a + 1, by the continued fraction (modified Lentz)."""
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
            return h
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


# --- Descriptive statistics (D328) ----------------------------------------------------------------

Weighted = Sequence[tuple[int | float, int]]
"""Values, each distinct value once in increasing order, with how many units have it (at least
one each)."""


def _count(values: Weighted, ends: float | None = None) -> int:
    """The number of values, ``_CHUNK`` of them at a time with the call's deadline, ``ends``,
    checked between."""
    found = 0
    for start in range(0, len(values), _CHUNK):
        _check(ends)
        found += sum(times for _, times in values[start : start + _CHUNK])
    return found


def _scale(values: Weighted) -> int:
    """The power of two that brings the largest magnitude among doubles into [0.5, 1): scaling
    by it is exact, so sums and squares over the scaled values are those over the values,
    scaled, without overflow (module docstring)."""
    largest = max((abs(float(value)) for value, _ in values), default=0.0)
    return math.frexp(largest)[1] if largest else 0


def mean(values: Weighted) -> float:
    """The mean of at least one value (module docstring)."""
    if _count(values) <= 0:
        raise ValueError("a mean is of at least one value")
    return exact_mean(values)


def sd(values: Weighted, centre: float) -> float | None:
    """The sample standard deviation around the values' mean ``centre``; ``None`` for fewer
    than two values or all of them the same (module docstring)."""
    n = _count(values)
    if n < 2 or len(values) < 2:
        return None
    shift = _scale(values)
    middle = math.ldexp(centre, -shift)
    squares = math.fsum(
        square
        for value, times in values
        for square in repeat((math.ldexp(float(value), -shift) - middle) ** 2, times)
    )
    return math.ldexp(math.sqrt(squares / (n - 1)), shift)


def order_statistic(values: Weighted, rank: int, ends: float | None = None) -> int | float:
    """The value at ``rank``, counted from 1, in the values' increasing order; the call's
    deadline, ``ends``, checked every ``_CHUNK`` values."""
    seen = 0
    for at, (value, times) in enumerate(values):
        if at % _CHUNK == 0:
            _check(ends)
        seen += times
        if rank <= seen:
            return value
    raise ValueError("a rank is at most the number of values")


def quantile(values: Weighted, probability: float, ends: float | None = None) -> float:
    """The quantile at ``probability`` of at least one value, as R's ``quantile(type = 7)``; the
    call's deadline, ``ends``, checked every ``_CHUNK`` values."""
    n = _count(values, ends)
    if n <= 0 or not 0 <= probability <= 1:
        raise ValueError("a quantile is of at least one value, at a probability from 0 to 1")
    index = 1 + (n - 1) * probability
    low, high = math.floor(index), math.ceil(index)
    found = float(order_statistic(values, low, ends))
    upper = float(order_statistic(values, high, ends))
    if index > low and upper != found:
        weight = index - low
        found = (1 - weight) * found + weight * upper
    return found


# --- Comparisons of numbers (D338) ----------------------------------------------------------------

_BETA_EPSILON = 1e-16
_HALF_LOG_TWO_PI = 0.5 * math.log(2 * math.pi)
_STIRLING = 10.0
"""The argument from which Stirling's series gives a log-gamma's tail to a double's precision."""
_LARGE = 15.0
"""The parameter from which ``_expansion`` gives I_x(a, b) (TOMS 708's bound for ``bgrat``)."""
_REDUCED = 40.0
"""The other parameter, at most, that the expansion's recurrence reduces to (0, 1]."""
_TERMS = 30
"""The expansion's terms at most, as TOMS 708 sums them."""


def _stirling(z: float) -> float:
    """ln Γ(z) less its Stirling approximation, (z − ½)·ln z − z + ½·ln 2π, for z ≥ 10."""
    inverse = 1 / z
    square = inverse * inverse
    return inverse * (
        1 / 12 - square * (1 / 360 - square * (1 / 1260 - square * (1 / 1680 - square / 1188)))
    )


def _log_gamma_ratio(a: float, b: float) -> float:
    """ln Γ(a + b) − ln Γ(a), for a ≥ 10, without the cancellation of two large log-gammas."""
    return (a - 0.5) * math.log1p(b / a) + b * math.log(a + b) - b + _stirling(a + b) - _stirling(a)


def _log(x: float, y: float) -> float:
    """ln x, where y = 1 − x is known exactly: ``log1p(−y)`` near 1."""
    return math.log1p(-y) if x > 0.5 else math.log(x)


def _log_ratio(ratio: float, less_one: float) -> float:
    """ln ``ratio``, where ``less_one`` is ``ratio`` − 1 computed without cancellation:
    ``log1p`` of it near 1, as TOMS 708's ``rlog1`` does, else ``log`` of the ratio."""
    return math.log1p(less_one) if abs(less_one) < 0.5 else math.log(ratio)


def _log_front(a: float, b: float, x: float, y: float) -> float:
    """ln(x^a·y^b / B(a, b)), y = 1 − x (module docstring)."""
    if a >= _STIRLING and b >= _STIRLING:
        total = a + b
        return (
            a * _log_ratio(x * total / a, (x * b - y * a) / a)
            + b * _log_ratio(y * total / b, (y * a - x * b) / b)
            + 0.5 * math.log(a * b / total)
            - _HALF_LOG_TWO_PI
            - (_stirling(a) + _stirling(b) - _stirling(total))
        )
    if a >= _STIRLING:
        return a * _log(x, y) + b * _log(y, x) - math.lgamma(b) + _log_gamma_ratio(a, b)
    if b >= _STIRLING:
        return a * _log(x, y) + b * _log(y, x) - math.lgamma(a) + _log_gamma_ratio(b, a)
    return a * _log(x, y) + b * _log(y, x) - math.lgamma(a) - math.lgamma(b) + math.lgamma(a + b)


def _beta_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of I_x(a, b), by the modified Lentz method."""
    total, up, down = a + b, a + 1, a - 1
    c = 1.0
    d = 1 - total * x / up
    d = 1 / (d if abs(d) >= _TINY else _TINY)
    h = d
    for m in range(1, _ITERATIONS):
        twice = 2 * m
        for term in (
            m * (b - m) * x / ((down + twice) * (a + twice)),
            -(a + m) * (total + m) * x / ((a + twice) * (up + twice)),
        ):
            d = 1 + term * d
            d = 1 / (d if abs(d) >= _TINY else _TINY)
            c = 1 + term / c
            c = c if abs(c) >= _TINY else _TINY
            delta = d * c
            h *= delta
        if abs(delta - 1) < _BETA_EPSILON:
            return h
    raise ArithmeticError("the incomplete beta fraction did not converge")


def beta_tail(a: float, b: float, x: float, y: float) -> float:
    """I_x(a, b), the regularised incomplete beta function, for a, b > 0 and x = 1 − y in
    [0, 1], ``y`` given so that a tail near 1 keeps its precision."""
    if a <= 0 or b <= 0 or not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("I_x(a, b) is defined here for a, b > 0 and x in [0, 1]")
    if x == 0 or y == 0:
        return 0.0 if x == 0 else 1.0
    if x < (a + 1) / (a + b + 2):
        return min(1.0, _beta_side(a, b, x, y))
    return max(0.0, 1 - _beta_side(b, a, y, x))


def _beta_side(a: float, b: float, x: float, y: float) -> float:
    """I_x(a, b) on the side of the mode where the continued fraction converges: for a large, b
    of 40 or less and x above 0.7, by the asymptotic expansion, b reduced to (0, 1] by
    I_x(a, b + 1) = I_x(a, b) + x^a·y^b / (b·B(a, b)), whose terms are all positive; else by the
    fraction."""
    if a >= _LARGE and b <= _REDUCED and x > 0.7:
        steps = math.ceil(b) - 1
        least = b - steps
        return _expansion(a, least, x, y) + math.fsum(
            math.exp(_log_front(a, least + i, x, y)) / (least + i) for i in range(steps)
        )
    return math.exp(_log_front(a, b, x, y)) * _beta_fraction(a, b, x) / a


def _expansion(a: float, b: float, x: float, y: float) -> float:
    """I_x(a, b) for a ≥ 15, 0 < b ≤ 1 and x above 0.7, by the asymptotic expansion of
    DiDonato and Morris (1992, §9), as TOMS 708's ``bgrat`` sums it: u·Σ dₙ·Jₙ with
    z = −(a + (b − 1)/2)·ln x, J₀ = Q(b, z)/r and r = z^b·e^(−z)/Γ(b)."""
    less = b - 1
    nu = a + less / 2
    logged = math.log1p(-y)
    z = -nu * logged
    log_r = b * math.log(z) - z - math.lgamma(b)
    log_u = log_r + _log_gamma_ratio(a, b) - b * math.log(nu)
    j = _gamma_over_front(b, z, log_r)
    v = 0.25 / (nu * nu)
    squared = 0.25 * logged * logged
    total, t, cn, twice = j, 1.0, 1.0, 0.0
    c: list[float] = []
    d: list[float] = []
    for n in range(1, _TERMS + 1):
        shifted = b + twice
        j = (shifted * (shifted + 1) * j + (z + shifted + 1) * t) * v
        twice += 2
        t *= squared
        cn /= twice * (twice + 1)
        c.append(cn)
        s = 0.0
        coefficient = b - n
        for i in range(1, n):
            s += coefficient * c[i - 1] * d[n - 1 - i]
            coefficient += b
        d.append(less * cn + s / n)
        term = d[-1] * j
        total += term
        if abs(term) <= _BETA_EPSILON * total:
            break
    return math.exp(log_u + math.log(total))


def _gamma_over_front(a: float, x: float, log_front: float) -> float:
    """Q(a, x)/r for a ≤ 1, r = x^a·e^(−x)/Γ(a) whose logarithm is ``log_front``: by the series
    of P(a, x) below a + 1, Q = 1 − r·Σ (which a ≤ 1 keeps from cancelling), else by
    ``upper_gamma``'s fraction, Q = r·h."""
    if x < a + 1:
        term = total = 1 / a
        n = a
        for _ in range(_ITERATIONS):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * _EPSILON:
                return math.exp(-log_front) - total
        raise ArithmeticError("the incomplete gamma series did not converge")
    return _gamma_fraction(a, x)


def t_tail(statistic: float, df: float) -> float:
    """The two-sided p-value of Student's *t* at ``statistic`` with ``df`` degrees of freedom,
    as R's ``2 * pt(-abs(t), df)``."""
    if df <= 0:
        raise ValueError("degrees of freedom are above zero")
    square = statistic * statistic
    if math.isinf(square):
        return 0.0
    total = df + square
    return beta_tail(df / 2, 0.5, df / total, square / total)


def _t_density(statistic: float, df: float) -> float:
    half = df / 2
    log_scale = (
        _log_gamma_ratio(half, 0.5)
        if half >= _STIRLING
        else math.lgamma(half + 0.5) - math.lgamma(half)
    )
    return math.exp(
        log_scale - 0.5 * math.log(df * math.pi) - (df + 1) / 2 * math.log1p(statistic**2 / df)
    )


def t_quantile(level: float, df: float) -> float:
    """The *t* with ``df`` degrees of freedom whose two-sided tail is 1 − ``level``, as R's
    ``qt((1 + level) / 2, df)``: a Newton iteration on ``t_tail``, kept within a bracket that
    halves when a step would leave it, to a double's precision."""
    if not 0 < level < 1 or df <= 0:
        raise ValueError("a level lies strictly between 0 and 1, and df above zero")
    wanted = 1 - level
    low, high = 0.0, max(1.0, z(level))
    while t_tail(high, df) > wanted:
        low, high = high, high * 2
        if math.isinf(high):
            raise ArithmeticError("the t quantile is beyond a double")
    guess = (low + high) / 2
    for _ in range(_ITERATIONS):
        gap = t_tail(guess, df) - wanted
        if gap > 0:
            low = guess
        else:
            high = guess
        density = _t_density(guess, df)
        step = guess + gap / (2 * density) if density > 0 else math.nan
        following = step if low < step < high else (low + high) / 2
        if following == guess or abs(following - guess) <= 4e-16 * following:
            return following
        guess = following
    raise ArithmeticError("the t quantile did not converge")


def f_tail(statistic: float, numerator: float, denominator: float) -> float:
    """The upper tail of the F distribution with ``numerator`` and ``denominator`` degrees of
    freedom at ``statistic``, as R's ``pf(f, df1, df2, lower.tail = FALSE)``."""
    if numerator <= 0 or denominator <= 0 or statistic < 0:
        raise ValueError("an F tail is at a statistic from 0, with degrees of freedom above 0")
    scaled = numerator * statistic
    if math.isinf(scaled):
        return 0.0
    total = denominator + scaled
    return beta_tail(denominator / 2, numerator / 2, denominator / total, scaled / total)


@dataclass(frozen=True)
class Moments:
    """A group's size, mean and standard deviation (``moments``): the mean as a double and
    exactly, and the deviation as a mantissa in [0.5, 1) and a binary exponent, which Welch's
    tests read, so that values and spreads of any size in a double's range keep their
    precision; ``spread`` is ``None`` for fewer than two values, all of them the same, or a
    deviation below the least double."""

    n: int
    mean: float
    exact: Fraction
    spread: float | None
    exponent: int = 0

    @property
    def sd(self) -> float | None:
        """The standard deviation, a double above zero (``inf`` beyond a double's range, which
        no output holds), or ``None`` (``spread``)."""
        if self.spread is None:
            return None
        try:
            return math.ldexp(self.spread, self.exponent)
        except OverflowError:
            return math.inf


def moments(values: Weighted, ends: float | None = None) -> Moments:
    """The moments of at least one value: the mean as ``mean`` gives it, and the standard
    deviation as ``sd`` computes it, over the values scaled by the power of two that brings the
    largest magnitude into [0.5, 1), around their exact mean scaled and rounded (``middle``), less
    n·δ², δ the rounding of that mean, since Σ(x − middle)² = Σ(x − mean)² + n·δ², so that a
    spread of a few ulps of the mean keeps its precision. A value held by many units adds its
    square times their number as one exact product (``_times``), so the sum costs the distinct
    values, not the units. Each pass over the values checks the call's deadline, ``ends``, every
    ``_CHUNK`` of them."""
    total, power, n = _chunked_sum(values, ends)
    if n <= 0:
        raise ValueError("a mean is of at least one value")
    exact = Fraction(total, n << -power)
    centre = total / (n << -power)
    if n < 2 or len(values) < 2:
        return Moments(n, centre, exact, None)
    largest = 0.0
    for start in range(0, len(values), _CHUNK):
        _check(ends)
        largest = max(largest, *(abs(float(value)) for value, _ in values[start : start + _CHUNK]))
    shift = math.frexp(largest)[1] if largest else 0
    scaled = exact / 2**shift if shift >= 0 else exact * 2**-shift
    middle = float(scaled)
    rounding = float(scaled - Fraction(middle))
    terms: list[float] = []
    for at, (value, times) in enumerate(values):
        if at % _CHUNK == 0:
            _check(ends)
        deviation = math.ldexp(float(value), -shift) - middle
        if times == 1:
            terms.append(deviation * deviation)
        else:
            terms += _times(deviation * deviation, times)
    squares = math.fsum(terms) - n * rounding * rounding
    mantissa, exponent = math.frexp(math.sqrt(max(squares, 0.0) / (n - 1)))
    if not mantissa or (exponent + shift < 0 and math.ldexp(mantissa, exponent + shift) == 0):
        return Moments(n, centre, exact, None)
    return Moments(n, centre, exact, mantissa, exponent + shift)


def _chunked_sum(values: Weighted, ends: float | None) -> tuple[int, int, int]:
    """``exact_sum`` of the values, ``_CHUNK`` of them at a time with the deadline checked
    between: each part's exact sum aligned to the least power of two, so the sum is exact."""
    total, power, count = 0, 0, 0
    for start in range(0, len(values), _CHUNK):
        _check(ends)
        part, least, counted = exact_sum(values[start : start + _CHUNK])
        common = min(power, least)
        total = (total << (power - common)) + (part << (least - common))
        power, count = common, count + counted
    return total, power, count


_SPLIT = 134217729.0
"""2^27 + 1, Veltkamp's constant for splitting a double into halves of 26 bits."""


def _times(square: float, times: int) -> tuple[float, ...]:
    """``square``·``times`` as a sum of doubles, exactly (Dekker's product): a count is at most
    2^53 and a square of scaled values at most 4, so no part overflows, and a part too small to
    be exact is below any sum it joins by far more than a double's precision."""
    count = float(times)
    product = square * count
    first = _SPLIT * square
    high = first - (first - square)
    low = square - high
    second = _SPLIT * count
    count_high = second - (second - count)
    count_low = count - count_high
    error = ((high * count_high - product) + high * count_low + low * count_high) + low * count_low
    return (product, error)


def _scaled(exact: Fraction, power: int) -> float:
    """``exact``·2^−``power``, correctly rounded; raises ``OverflowError`` beyond a double."""
    return float(exact / 2**power if power >= 0 else exact * 2**-power)


@dataclass(frozen=True)
class Welch:
    """Welch's two-sample *t* test of a first group against a second: the difference of their
    means (first less second), its standard error as ``error``·2^``exponent``, the statistic, the
    Welch–Satterthwaite degrees of freedom and the two-sided p-value."""

    difference: float
    error: float
    exponent: int
    statistic: float
    df: float
    p: float

    def interval(self, level: float) -> tuple[float, float]:
        """The difference's interval at ``level``, as ``t.test``'s ``conf.int``."""
        half = math.ldexp(t_quantile(level, self.df) * self.error, self.exponent)
        return self.difference - half, self.difference + half


def _errors(groups: Sequence[Moments]) -> tuple[list[float], int]:
    """Each group's standard error of its mean over 2^e, e the largest group's exponent: Welch's
    statistics and degrees of freedom are the same whatever power of two scales every group, so
    they are computed over these, which neither overflow nor underflow where they matter."""
    if any(group.spread is None for group in groups):
        raise ValueError("a group of Welch's test holds two different values or more")
    top = max(group.exponent for group in groups)
    return [
        math.ldexp(cast(float, group.spread), group.exponent - top) / math.sqrt(group.n)
        for group in groups
    ], top


def welch(first: Moments, second: Moments) -> Welch:
    """Welch's test (module docstring) of two groups' moments, each of two different values or
    more. Its statistic is at most about 2^52·n in size, since a group's deviation is at least
    an ulp of its mean over √n, so it cannot overflow, as Welch's one-way test can (its weights
    reach 1/5e-324²)."""
    (error1, error2), top = _errors([first, second])
    error = math.hypot(error1, error2)
    share1, share2 = error1 / error, error2 / error
    df = 1 / (share1**4 / (first.n - 1) + share2**4 / (second.n - 1))
    statistic = _scaled(first.exact - second.exact, top) / error
    return Welch(first.mean - second.mean, error, top, statistic, df, t_tail(statistic, df))


@dataclass(frozen=True)
class WelchAnova:
    """Welch's one-way test: its statistic, its degrees of freedom and its p-value."""

    statistic: float
    numerator: int
    denominator: float
    p: float


def welch_anova(groups: Sequence[Moments]) -> WelchAnova:
    """Welch's one-way test of three groups' moments or more (module docstring), as
    ``oneway.test`` computes it, each group of two different values or more. Its weights, n over
    the variances, the weighted centre and the weights' shares are exact rationals (six groups
    at most), and each group's distance from the centre is standardised on its own scale,
    zᵢ = (meanᵢ − centre)·√nᵢ / sdᵢ, whose squares sum to the statistic's numerator: so groups
    whose spreads differ by any power of two in a double's range are tested. The centre lies
    with the group of least variance, and every other group's deviation is at least an ulp of
    its mean over √n, so each |zᵢ| is at most about 2^53·n and the statistic cannot overflow."""
    if len(groups) < 3:
        raise ValueError("Welch's one-way test is of three groups or more here")
    if any(group.spread is None for group in groups):
        raise ValueError("a group of Welch's test holds two different values or more")
    weights = [
        Fraction(group.n) / (Fraction(cast(float, group.spread)) ** 2 * _power(2 * group.exponent))
        for group in groups
    ]
    total = sum(weights, Fraction(0))
    centre = sum((w * group.exact for w, group in zip(weights, groups, strict=True)), Fraction(0))
    centre /= total
    k = len(groups)
    tmp = float(
        sum(
            (
                (1 - w / total) ** 2 / (group.n - 1)
                for w, group in zip(weights, groups, strict=True)
            ),
            Fraction(0),
        )
    ) / (k * k - 1)
    distances = [
        float((group.exact - centre) / _power(group.exponent))
        * math.sqrt(group.n)
        / cast(float, group.spread)
        for group in groups
    ]
    between = math.fsum(z * z for z in distances)
    statistic = between / ((k - 1) * (1 + 2 * (k - 2) * tmp))
    denominator = 1 / (3 * tmp)
    return WelchAnova(statistic, k - 1, denominator, f_tail(statistic, k - 1, denominator))


def _power(exponent: int) -> Fraction:
    """2^``exponent``, exactly."""
    return Fraction(2) ** exponent


@dataclass(frozen=True)
class _Ranked:
    """The groups' values ranked together: each group's sum of doubled mid-ranks, an integer,
    and each distinct value, in increasing order, with the number of values tied at it."""

    sums: list[int]
    distinct: list[float]
    tied: list[int]

    @property
    def total(self) -> int:
        return sum(self.tied)


_CHUNK = 65_536
"""The values taken between two checks of the call's deadline (as D333's listing does)."""


def _check(ends: float | None) -> None:
    """Raise ``CallerDeadline`` once ``time.monotonic()`` has passed ``ends``."""
    if ends is not None and time.monotonic() >= ends:
        raise CallerDeadline


def checked_sort[T](items: list[T], ends: float | None, key: Callable[[T], Any]) -> list[T]:
    """``items`` in increasing order of ``key``, sorted in runs of ``_CHUNK`` and the runs then
    merged two at a time, the call's deadline, ``ends``, checked before each sort and each merge:
    so no step sorts more than a run or merges more than two, where one sort of the five million
    values the answer cap holds takes seconds."""
    runs: list[list[T]] = []
    for start in range(0, len(items), _CHUNK):
        _check(ends)
        runs.append(sorted(items[start : start + _CHUNK], key=key))
    while len(runs) > 1:
        paired: list[list[T]] = []
        for at in range(0, len(runs), 2):
            _check(ends)
            both = runs[at] + runs[at + 1] if at + 1 < len(runs) else runs[at]
            both.sort(key=key)
            paired.append(both)
        runs = paired
    return runs[0] if runs else []


def _ranks(groups: Sequence[Weighted], ends: float | None = None) -> _Ranked:
    """The groups' ranks (``_Ranked``): a value's doubled mid-rank is 2·below + tied + 1, an
    integer, so the sums are exact without a rational until the statistic. The values are sorted
    (``checked_sort``), and each group's values placed among the distinct ones by bisection,
    ``_CHUNK`` at a time, the deadline checked between chunks (``CallerDeadline``)."""
    values: list[list[float]] = [[] for _ in groups]
    times: list[list[int]] = [[] for _ in groups]
    for group, floats, counts in zip(groups, values, times, strict=True):
        for start in range(0, len(group), _CHUNK):
            _check(ends)
            block = group[start : start + _CHUNK]
            floats += [float(value) for value, _ in block]
            counts += [count for _, count in block]
    ordered = checked_sort(list(chain.from_iterable(values)), ends, float)
    distinct: list[float] = []
    for start in range(0, len(ordered), _CHUNK):
        _check(ends)
        block = ordered[start : start + _CHUNK]
        before = [distinct[-1] if distinct else math.nan, *block[:-1]]
        distinct += [value for value, last in zip(block, before, strict=True) if value != last]
    tied = [0] * len(distinct)
    places: list[list[int]] = []
    for group, counts in zip(values, times, strict=True):
        place: list[int] = []
        for start in range(0, len(group), _CHUNK):
            _check(ends)
            block = list(map(bisect.bisect_left, repeat(distinct), group[start : start + _CHUNK]))
            for at, count in zip(block, counts[start : start + _CHUNK], strict=True):
                tied[at] += count
            place += block
        places.append(place)
    _check(ends)
    below = list(accumulate(tied, initial=0))
    sums = [
        2 * sum(map(mul, counts, map(below.__getitem__, place)))
        + sum(map(mul, counts, map(tied.__getitem__, place)))
        + sum(counts)
        for place, counts in zip(places, times, strict=True)
    ]
    return _Ranked(sums, distinct, tied)


_EXACT_BELOW = 50
"""R's ``wilcox.test`` computes its exact p-value when both groups hold fewer values."""


def _rank_sum_counts(m: int, n: int) -> list[int]:
    """The number of arrangements of ``m`` and ``n`` distinct values whose ``W`` is each u from 0
    to m·n: the coefficients of the Gaussian binomial coefficient [m + n, m], as integers."""
    counts = [1]
    for i in range(1, m + 1):
        grown = counts + [0] * (n + i)
        for u in range(len(grown) - 1, n + i - 1, -1):
            grown[u] -= grown[u - n - i]
        for u in range(i, len(grown)):
            grown[u] += grown[u - i]
        counts = grown[: len(counts) + n]
    return counts


@dataclass(frozen=True)
class RankTest:
    """A rank test: its statistic, its degrees of freedom where it has them, its p-value and
    whether the p-value is exact."""

    statistic: float
    p: float
    df: int | None = None
    exact: bool = False


def mann_whitney(first: Weighted, second: Weighted, ends: float | None = None) -> RankTest | None:
    """The Wilcoxon rank-sum test of a first group against a second (module docstring);
    ``None`` when every value is tied, whose variance is 0; ``ends`` as ``_ranks``'."""
    m, n = _count(first, ends), _count(second, ends)
    if not m or not n:
        raise ValueError("each group of a rank test holds a value")
    ranked = _ranks([first, second], ends)
    ties, total = ranked.tied, ranked.total
    statistic = Fraction(ranked.sums[0] - m * (m + 1), 2)
    if m < _EXACT_BELOW and n < _EXACT_BELOW and all(tied == 1 for tied in ties):
        counts = _rank_sum_counts(m, n)
        observed = int(statistic)
        if 2 * observed > m * n:
            tail = Fraction(sum(counts[observed:]), sum(counts))
        else:
            tail = Fraction(sum(counts[: observed + 1]), sum(counts))
        return RankTest(float(statistic), float(min(Fraction(1), 2 * tail)), exact=True)
    correction = Fraction(sum(tied**3 - tied for tied in ties), total * (total - 1))
    variance = Fraction(m * n, 12) * (total + 1 - correction)
    if variance <= 0:
        return None
    centred = statistic - Fraction(m * n, 2)
    shifted = centred - (Fraction(1, 2) if centred > 0 else -Fraction(1, 2) if centred else 0)
    score = float(shifted) / math.sqrt(float(variance))
    return RankTest(float(statistic), min(1.0, math.erfc(abs(score) / math.sqrt(2))))


_PRINTED = 15
"""The significant digits of R's ``as.character`` of a double (``DBL_DIG``)."""
_EXTENDED = 64
"""The bits of an x87 long double's mantissa, in which R's ``scientific`` scales a value."""
_EXACT_POWER = 22
"""The greatest power of ten a double holds exactly."""
_POWERS = tuple(Fraction(float(f"1e{k}")) for k in range(28))
"""R's ``tbl``: the double literals 1e0 to 1e27, held as long doubles (``KP_MAX`` 27)."""


def _extended(value: Fraction) -> Fraction:
    """``value`` rounded to a long double's 64-bit mantissa, ties to even, as x87 arithmetic
    rounds each operation (no long double here underflows or overflows)."""
    if not value:
        return value
    size = abs(value)
    power = size.numerator.bit_length() - size.denominator.bit_length()
    if size >= Fraction(2) ** power:
        power += 1
    scale = Fraction(2) ** (_EXTENDED - power)
    return Fraction(round(value * scale)) / scale


def _printed(value: float) -> str:
    """``value`` as R 4.3's ``as.character`` writes a double (``_printed_exactly``), by the
    correctly rounded 15 digits that C's ``printf`` gives wherever R's long double scaling cannot
    round otherwise: R's digits are those unless the digits beyond the 15th lie within 1e-3 of a
    last digit's half (its scaling is off by 2^-64 at most), the rounding carried into the next
    power of ten, the value lies at a power of ten (where ``log10`` may round), or it scales by a
    power beyond 1e22 (whose double literal is inexact, or ``powl``), all of which
    ``_printed_exactly`` computes as R does."""
    if value == 0:
        return "0"
    size = abs(value)
    rounded = format(size, ".14e")
    finer = format(size, ".21e")
    power = int(rounded[17:])
    beyond = int(finer[16:23])
    if (
        abs(beyond - 5_000_000) <= 10_000
        or int(finer[24:]) != power
        or math.floor(math.log10(size)) != power
        or not -_EXACT_POWER <= power - _PRINTED + 1 <= _EXACT_POWER
    ):
        return _printed_exactly(value)
    mantissa = rounded[0] + rounded[2:16]
    digits = len(mantissa.rstrip("0")) or 1
    return _written(value, power, digits, widens=False)


def _printed_exactly(value: float) -> str:
    """``value`` as R 4.3's ``as.character`` writes a double (``StringFromReal``): R's
    ``scientific`` at 15 digits, its scaling by a power of ten rounded as long doubles are (``tbl``
    exact to 1e22 and the double literals beyond, ``powl`` beyond 1e27 taken as correctly
    rounded), then ``formatReal``'s choice of fixed notation whenever it is no wider than
    scientific, printed as C's ``printf`` prints it and its trailing zeros dropped
    (``EncodeRealDrop0``). So 0.1 + 0.2 and 0.3 are both "0.3", and 1234567890123456 and
    1234567890123457 two strings where 1.7e15 and 1.7e15 + 1 are one (D338)."""
    if value == 0:
        return "0"
    size = abs(value)
    exact = Fraction(size)
    kp = math.floor(math.log10(size)) - _PRINTED + 1
    if abs(kp) < len(_POWERS):
        scaled = _extended(exact / _POWERS[kp]) if kp > 0 else _extended(exact * _POWERS[-kp])
    else:
        scaled = _extended(exact / _extended(Fraction(10) ** kp))
    if scaled < _POWERS[_PRINTED - 1]:
        scaled = _extended(scaled * 10)
        kp -= 1
    alpha = round(scaled)
    digits = _PRINTED
    for _ in range(_PRINTED):
        if alpha % 10:
            break
        alpha //= 10
        digits -= 1
    if digits == 0:
        digits = 1
        kp += 1
    kpower = kp + _PRINTED - 1
    cut = min(max(_PRINTED - kpower, 0), len(_POWERS) - 1)
    fuzz = 0.5 / float(_POWERS[cut])
    widens = 0 < kpower < len(_POWERS) and exact < _extended(_POWERS[kpower] - Fraction(fuzz))
    return _written(value, kpower, digits, widens=widens)


def _written(value: float, kpower: int, digits: int, *, widens: bool) -> str:
    """``formatReal``'s choice for one value of ``digits`` significant digits at 10^``kpower``,
    and ``EncodeRealDrop0``'s text of it (``_printed_exactly``)."""
    negative = int(value < 0)
    left = kpower + 1 - widens
    width = negative + (1 if left <= 0 else left)
    if left < 0:
        width = 1 + negative
    right = max(digits - left, 0)
    fixed = width + right + (right != 0)
    places = digits - 1
    scientific = negative + (places > 0) + places + 4 + (2 if left > 100 or left <= -99 else 1)
    text = (
        format(value, f".{right}f")
        if fixed <= scientific
        else format(value, f"#.{places}e" if places else f".{places}e")
    )
    point = text.find(".")
    if point < 0:
        return text
    end = point + 1
    while end < len(text) and text[end].isdigit():
        end += 1
    kept = end
    while kept > point + 1 and text[kept - 1] == "0":
        kept -= 1
    if kept == point + 1:
        kept = point
    return text[:kept] + text[end:]


def kruskal_wallis(groups: Sequence[Weighted], ends: float | None = None) -> RankTest | None:
    """The Kruskal–Wallis test of two groups or more (module docstring), ``ends`` as
    ``_ranks``'. As ``kruskal.test``, the ranks are ``rank``'s, whose ties are values equal as
    doubles, and the correction for ties is ``table``'s, whose ties are values that
    ``as.character`` prints alike (``_printed``): 0.1 + 0.2 and 0.3 are two ranks and one tie.
    ``None`` when every value is tied, as doubles or as R prints them (where R's statistic is
    infinite: it divides by a correction of 0)."""
    sizes = [_count(group, ends) for group in groups]
    if len(groups) < 2 or not all(sizes):
        raise ValueError("a rank test is of two groups or more, each holding a value")
    ranked = _ranks(groups, ends)
    total = ranked.total
    correction = 1 - Fraction(
        sum(tied**3 - tied for tied in _printed_ties(ranked, ends)), total**3 - total
    )
    if correction <= 0:
        return None
    between = sum(
        (Fraction(rank * rank, 4 * size) for rank, size in zip(ranked.sums, sizes, strict=True)),
        Fraction(0),
    )
    statistic = (12 * between / (total * (total + 1)) - 3 * (total + 1)) / correction
    df = len(groups) - 1
    value = float(statistic)
    return RankTest(value, upper_gamma(df / 2, max(value, 0.0) / 2), df=df)


_CLOSE = 1e-12
"""The relative gap below which two distinct values may print alike: a string of R's names
values within half a unit of its 15th significant digit, 5e-15 of them."""


def _printed_ties(ranked: _Ranked, ends: float | None) -> list[int]:
    """The number of values that print alike, for each string R's ``table`` counts: runs of
    distinct values each within ``_CLOSE`` of the next are printed and counted by string, and any
    other value is a tie of its own, which spares printing values that cannot collide."""
    found: list[int] = []
    distinct, tied = ranked.distinct, ranked.tied
    start = 0
    while start < len(distinct):
        if start % _CHUNK == 0:
            _check(ends)
        end = start + 1
        while end < len(distinct) and distinct[end] - distinct[end - 1] <= _CLOSE * max(
            abs(distinct[end]), abs(distinct[end - 1])
        ):
            end += 1
        if end - start == 1:
            found.append(tied[start])
        else:
            printed: Counter[str] = Counter()
            for at in range(start, end):
                if at % _CHUNK == 0:
                    _check(ends)
                printed[_printed(distinct[at])] += tied[at]
            found += printed.values()
        start = end
    return found


def median(values: Weighted, ends: float | None = None) -> float:
    """The median of at least one value, R's ``quantile(type = 7)`` at ½."""
    return quantile(values, 0.5, ends)


REPLICATES = 2000
"""The bootstrap's replicates (§9.5)."""


class _Draws:
    """Uniform, normal and gamma variates from one seeded stream: Python's Mersenne Twister,
    whose ``random`` Python keeps the same for an integer seed, a normal by the inverse of its
    distribution (``NormalDist``, AS 241), and a gamma by Marsaglia and Tsang's method."""

    def __init__(self, seed: int) -> None:
        self.stream = random.Random(seed)

    def uniform(self) -> float:
        """A uniform variate in (0, 1): 0 is drawn again."""
        while True:
            found = self.stream.random()
            if found > 0:
                return found

    def gamma(self, shape: int) -> float:
        """A gamma variate of shape ``shape`` ≥ 1 and scale 1."""
        d = shape - 1 / 3
        c = 1 / math.sqrt(9 * d)
        while True:
            x = _STANDARD.inv_cdf(self.uniform())
            v = 1 + c * x
            if v <= 0:
                continue
            v = v * v * v
            u = self.uniform()
            if u < 1 - 0.0331 * x**4 or math.log(u) < 0.5 * x * x + d * (1 - v + math.log(v)):
                return d * v


def bootstrap_medians(
    values: Weighted, seed: int, replicates: int = REPLICATES, ends: float | None = None
) -> list[float]:
    """The medians of ``replicates`` resamples, with replacement, of a group of at least one value,
    from a stream seeded with ``seed`` (module docstring). A resample of n values sorted is the
    values at ⌊n·U₍ⱼ₎⌋ of the group's sorted values, since each draw is the value at ⌊n·U⌋ for a
    uniform U, and that is monotone: the median is the ⌈n/2⌉-th, or the mean of the (n/2)-th and
    the next, drawn as U₍ⱼ₎ ~ Beta(j, n − j + 1), the ratio of two gammas, and the next as
    U₍ⱼ₊₁₎ = U₍ⱼ₎ + (1 − U₍ⱼ₎)·(1 − V^(1/(n − j))), the least of n − j uniforms above it. The
    values are read ``_CHUNK`` at a time, the call's deadline, ``ends``, checked between."""
    n = 0
    cumulative: list[int] = []
    ordered: list[float] = []
    for start in range(0, len(values), _CHUNK):
        _check(ends)
        block = values[start : start + _CHUNK]
        sums = list(accumulate((times for _, times in block), initial=n))
        cumulative += sums[1:]
        n = sums[-1]
        ordered += [float(value) for value, _ in block]
    if n <= 0:
        raise ValueError("a bootstrap resamples at least one value")

    def at(uniform: float) -> float:
        rank = min(n - 1, math.floor(n * uniform))
        return ordered[bisect.bisect_right(cumulative, rank)]

    draws = _Draws(seed)
    even = n % 2 == 0
    j = n // 2 if even else (n + 1) // 2
    found: list[float] = []
    for _ in range(replicates):
        first = draws.gamma(j)
        second = draws.gamma(n - j + 1)
        low = first / (first + second)
        value = at(low)
        if even:
            gap = -math.expm1(math.log(draws.uniform()) / (n - j))
            upper = at(low + (1 - low) * gap)
            if upper != value:
                value = 0.5 * value + 0.5 * upper
        found.append(value)
    return found


def percentile_ranks(count: int, level: float) -> tuple[int, int]:
    """The ranks, from 1, of a bootstrap interval's bounds at ``level`` over ``count``
    replicates (§9.5, D338): ⌈B·alpha/2⌉ and ⌈B·(1 − alpha/2)⌉ within 1 to B, alpha = 1 −
    ``level`` read as the decimal JSON writes it, so that 0.95 gives the 50th and the 1950th of
    2000, where the double's own alpha, a little above 0.05, would give the 51st."""
    if not count or not 0 < level < 1:
        raise ValueError("an interval of at least one replicate, at a level between 0 and 1")
    alpha = 1 - Fraction(repr(level))
    low = math.ceil(count * alpha / 2)
    high = math.ceil(count * (1 - alpha / 2))
    return max(low, 1), min(high, count)


def percentile_interval(replicates: Sequence[float], level: float) -> tuple[float, float]:
    """The bootstrap interval at ``level`` of replicates (§9.5): their order statistics at
    ``percentile_ranks``."""
    low, high = percentile_ranks(len(replicates), level)
    ordered = sorted(replicates)
    return ordered[low - 1], ordered[high - 1]


__all__ = [
    "REPLICATES",
    "ChiSquared",
    "RankTest",
    "Weighted",
    "Welch",
    "WelchAnova",
    "benjamini_hochberg",
    "beta_tail",
    "bootstrap_medians",
    "chi_squared",
    "f_tail",
    "fisher",
    "katz",
    "kruskal_wallis",
    "mann_whitney",
    "mean",
    "median",
    "newcombe",
    "order_statistic",
    "percentile_interval",
    "percentile_ranks",
    "quantile",
    "sd",
    "t_quantile",
    "t_tail",
    "upper_gamma",
    "welch",
    "welch_anova",
    "wilson",
    "z",
]
