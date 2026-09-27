"""Floating-point arithmetic as C gives it, and the time-to-event code held to it (D349)."""

import ast
import math
from pathlib import Path

import pytest

from aibi.core.analyses import ieee, survival, timetoevent

MODULES = [Path(timetoevent.__file__), Path(survival.__file__)]

PREDICATES = frozenset({"isfinite", "isinf", "isnan", "copysign"})

ALLOWED = {
    ("replicate", "int"): "⌊n·U⌋ of a uniform U in [0, 1) and n ≥ 1 rows, an index below n",
    ("median_interval", "math.ceil"): "the ceiling of an exact Fraction",
}


def _callee(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _literal(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value != 0
    )


def _stride(node: ast.expr) -> bool:
    """``LOOK_EVERY``, a positive integer by which an index is counted."""
    return (isinstance(node, ast.Name) and node.id == "LOOK_EVERY") or (
        isinstance(node, ast.Attribute) and node.attr == "LOOK_EVERY"
    )


def _raising(path: Path) -> list[str]:
    """Every operation in a module that Python raises on where C gives ±∞ or NaN."""
    found: list[str] = []

    def visit(node: ast.AST, within: str) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            within = node.name
        if isinstance(node, ast.ImportFrom) and node.module == "math":
            found.append(f"{node.lineno}: from math import")
        operator: ast.operator | None = None
        right: ast.expr | None = None
        left: ast.expr | None = None
        line = 0
        if isinstance(node, ast.BinOp):
            operator, left, right, line = node.op, node.left, node.right, node.lineno
        elif isinstance(node, ast.AugAssign):
            operator, right, line = node.op, node.value, node.lineno
        if isinstance(operator, ast.Div | ast.FloorDiv) and not (right and _literal(right)):
            found.append(f"{line}: a division by a non-literal in {within}")
        if isinstance(operator, ast.Pow) and not (
            right and _literal(right) and left is not None and _literal(left)
        ):
            found.append(f"{line}: a power in {within}")
        if isinstance(operator, ast.Mod) and not (right and (_literal(right) or _stride(right))):
            found.append(f"{line}: a remainder in {within}")
        if isinstance(node, ast.Call):
            callee = _callee(node)
            raising = callee in ("int", "float", "round", "pow", "divmod") or (
                callee is not None
                and callee.startswith("math.")
                and callee.removeprefix("math.") not in PREDICATES
            )
            if raising and (within, callee) not in ALLOWED:
                found.append(f"{node.lineno}: {callee} in {within}")
        for child in ast.iter_child_nodes(node):
            visit(child, within)

    visit(ast.parse(path.read_text(encoding="utf-8")), "<module>")
    return found


@pytest.mark.parametrize("path", MODULES, ids=lambda path: path.name)
def test_the_time_to_event_code_does_every_floating_operation_as_c_does(path: Path) -> None:
    assert _raising(path) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x = a / b", ["1: a division by a non-literal in <module>"]),
        ("x /= b", ["1: a division by a non-literal in <module>"]),
        ("x = a // b", ["1: a division by a non-literal in <module>"]),
        ("x = a / 0", ["1: a division by a non-literal in <module>"]),
        ("def f():\n    return math.exp(a)", ["2: math.exp in f"]),
        ("x = math.log(a)", ["1: math.log in <module>"]),
        ("x = math.sqrt(a)", ["1: math.sqrt in <module>"]),
        ("x = math.fsum(a)", ["1: math.fsum in <module>"]),
        ("x = a ** b", ["1: a power in <module>"]),
        ("x = int(a)", ["1: int in <module>"]),
        ("x = float(a)", ["1: float in <module>"]),
        ("x = a % b", ["1: a remainder in <module>"]),
        ("from math import exp", ["1: from math import"]),
        ("x = a / 2 + math.isfinite(a) + i % LOOK_EVERY + i % t.LOOK_EVERY + 2.0 ** 0.5", []),
    ],
)
def test_the_check_finds_each_operation_python_raises_on(
    source: str, expected: list[str], tmp_path: Path
) -> None:
    written = tmp_path / "module.py"
    written.write_text(source, encoding="utf-8")
    assert _raising(written) == expected


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (1.0, 0.0, math.inf),
        (-1.0, 0.0, -math.inf),
        (1.0, -0.0, -math.inf),
        (-1.0, -0.0, math.inf),
        (3, 0, math.inf),
        (6.0, 3.0, 2.0),
        (1e308, 1e-10, math.inf),
    ],
)
def test_a_division_is_ieee_754_s(numerator: float, denominator: float, expected: float) -> None:
    assert ieee.div(numerator, denominator) == expected


@pytest.mark.parametrize(("numerator", "denominator"), [(0.0, 0.0), (math.nan, 0.0), (0, 0)])
def test_nothing_over_zero_is_not_a_number(numerator: float, denominator: float) -> None:
    assert math.isnan(ieee.div(numerator, denominator))


def test_exp_log_and_sqrt_give_infinities_and_nan_where_python_raises() -> None:
    assert ieee.exp(1000.0) == math.inf
    assert ieee.exp(-1000.0) == 0.0
    assert math.isnan(ieee.exp(math.nan))
    assert ieee.log(0.0) == -math.inf
    assert math.isnan(ieee.log(-1.0))
    assert math.isnan(ieee.log(math.nan))
    assert ieee.log(math.e) == 1.0
    assert math.isnan(ieee.sqrt(-1.0))
    assert ieee.sqrt(4.0) == 2.0


def test_a_sum_that_overflows_or_meets_both_infinities_is_c_s() -> None:
    assert ieee.fsum([1e308, 1e308]) == math.inf
    assert math.isnan(ieee.fsum([math.inf, -math.inf]))
    assert ieee.fsum([0.1] * 10) == 1.0
    assert ieee.fsum(x for x in (1.0, 2.0)) == 3.0


def test_an_integer_beyond_a_double_is_infinite() -> None:
    assert ieee.real(10**400) == math.inf
    assert ieee.real(-(10**400)) == -math.inf
    assert math.copysign(1.0, ieee.real(-0.0)) == 1.0
    assert ieee.real(3) == 3.0
