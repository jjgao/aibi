"""The tests D422 cites as pins are pins (SPEC D422): each exists and asserts each expression its
row of ``pin_table.PINS`` gives, as ``tests/core/pins.py`` reads it from the syntax tree; and the
guard that runs them (``pins.Guard``) refuses a pin that does not run what it asserts, as a table
of placements shows: each placement of an assert that cannot run, a test that is skipped, expected
to fail or deselected is refused, and each that can run is let through.

The guard's runs are in a project of their own in a subprocess, with its own table of pins."""

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from tests.core import pin_table, pins

# --- The cited tests ------------------------------------------------------------------------------


def test_d422_cites_exactly_the_pins_that_have_needles() -> None:
    assert pins.cited() == set(pin_table.PINS)


@pytest.mark.parametrize("pin", sorted(pin_table.PINS))
def test_each_cited_test_asserts_each_expression_somewhere(pin: str) -> None:
    """The syntactic half, which a test run alone (``pytest tests/core/test_pins.py``) has: the
    guard's trace of the pin itself is the run's, and needs the pin to run."""
    path, name = pin.split("::")
    assert pins.reasons_not_a_pin(path, name, pin_table.PINS[pin]) == []


# --- The reading of a test's syntax tree ----------------------------------------------------------


@pytest.fixture
def module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> object:
    """Write a module under a stand-in repository root and read its function ``test_x``."""
    monkeypatch.setattr(pins, "ROOT", tmp_path)
    (tmp_path / "server").mkdir()

    def write(code: str, needles: tuple[str, ...] = ("a.b == 'c'",)) -> list[str]:
        (tmp_path / "server" / "m.py").write_text(textwrap.dedent(code), encoding="utf-8")
        return pins.reasons_not_a_pin("m.py", "test_x", needles)

    return write


def test_an_assert_of_the_expression_is_found_wherever_it_stands_and_as_a_conjunct(
    module: Any,
) -> None:
    assert module("def test_x():\n    assert a.b == 'c'\n") == []
    assert module("def test_x():\n    for i in x:\n        assert a.b == 'c'\n") == []
    assert module("def test_x():\n    if x:\n        assert a.b == 'c'\n") == []
    assert module("def test_x():\n    assert a.b == 'c' and d == 1, 'msg'\n") == []
    assert (
        module("def test_x():\n    assert (\n        d == 1\n        and a.b == 'c'\n    )\n") == []
    )
    assert module("def test_x():\n    assert d == 1 and (a.b == 'c' and e)\n") == []


@pytest.mark.parametrize(
    "code",
    [
        "def test_x():\n    assert a.b == 'c' or True\n",
        "def test_x():\n    assert d == 1 or a.b == 'c'\n",
        "def test_x():\n    # assert a.b == 'c'\n    note = \"assert a.b == 'c'\"\n    assert 1\n",
        "def test_x():\n    assert 'a.b == c' in 'text'\n",
        "def test_x():\n    assert a.b != 'c'\n",
        "def test_x():\n    assert not (a.b == 'c')\n",
        "def test_x():\n    assert (a.b == 'c') if d else True\n",
        "def test_x():\n    assert a.b == 'c'\n",
    ],
)
def test_an_assert_that_does_not_hold_the_expression_as_a_conjunct_is_none(
    module: Any, code: str
) -> None:
    needles = ("a.b == 'd'",) if code.endswith("'c'\n") and "or" not in code else ("a.b == 'c'",)
    assert module(code, needles) != []


def test_a_second_expression_is_needed_as_well_as_the_first(module: Any) -> None:
    code = "def test_x():\n    assert a.b == 'c'\n"
    assert module(code, ("a.b == 'c'", "d == 1")) == ["asserts nothing of d == 1"]


def test_function_node_matches_a_name_exactly() -> None:
    path = "tests/core/test_pins.py"
    name = "test_function_node_matches_a_name_exactly"
    assert pins.function_node(path, name).name == name
    for other in (name[:-1], name + "s", name[1:]):
        with pytest.raises(AssertionError, match="has no function"):
            pins.function_node(path, other)


# --- The guard, in a project of its own -----------------------------------------------------------

PRELUDE = """\
import contextlib

import pytest


class A:
    b = "c"


a = A()
d = 1
"""
NEEDLE = "a.b == 'c'"
SUMMARY = "a cited pin did not run"
"""What the guard writes when a cited pin did not run."""
CASES: dict[str, tuple[bool, str]] = {}
"""Each placement of the needle: whether the guard lets it through, and the module that holds it
(a function ``test_x`` and what it needs)."""


def case(name: str, passes: bool, body: str) -> None:
    CASES[name] = (passes, textwrap.dedent(body))


# What cannot run. The first five are the reviewer's P01-P05.
case(
    "return_before", False, "def test_x():\n    assert d == 1\n    return\n    assert a.b == 'c'\n"
)
case(
    "except_that_never_runs",
    False,
    """
    def test_x():
        try:
            assert d == 1
        except ValueError:
            assert a.b == 'c'
    """,
)
case(
    "after_the_raise_in_raises",
    False,
    """
    def test_x():
        with pytest.raises(ValueError):
            raise ValueError
            assert a.b == 'c'
    """,
)
case("for_over_nothing", False, "def test_x():\n    for _ in ():\n        assert a.b == 'c'\n")
case("while_false", False, "def test_x():\n    while False:\n        assert a.b == 'c'\n")
case(
    "if_false",
    False,
    "def test_x():\n    assert d == 1\n    if False:\n        assert a.b == 'c'\n",
)
case(
    "if_on_a_variable_that_is_false",
    False,
    "def test_x():\n    flag = d == 2\n    if flag:\n        assert a.b == 'c'\n    assert d\n",
)
case(
    "else_of_a_true_condition",
    False,
    """
    def test_x():
        if d == 1:
            pass
        else:
            assert a.b == 'c'
    """,
)
case(
    "match_case_not_taken",
    False,
    """
    def test_x():
        match d:
            case 2:
                assert a.b == 'c'
        assert d == 1
    """,
)
case(
    "after_break",
    False,
    """
    def test_x():
        for _ in range(2):
            break
            assert a.b == 'c'
        assert d == 1
    """,
)
case(
    "after_return_in_try",
    False,
    """
    def test_x():
        try:
            return
        finally:
            pass
        assert a.b == 'c'
    """,
)
case(
    "after_the_raise_in_suppress",
    False,
    """
    def test_x():
        with contextlib.suppress(ValueError):
            raise ValueError
            assert a.b == 'c'
    """,
)
case(
    "nested_never_called",
    False,
    """
    def test_x():
        def inner():
            assert a.b == 'c'
        assert d == 1
    """,
)
case(
    "nested_called",
    False,
    """
    def test_x():
        def inner():
            assert a.b == 'c'
        inner()
    """,
)
case("only_another_assert", False, "def test_x():\n    assert d == 1\n")
case("disjunction", False, "def test_x():\n    assert a.b == 'c' or d == 2\n")
case("disjunction_with_true", False, "def test_x():\n    assert d == 2 or a.b == 'c'\n")
case("in_a_string", False, "def test_x():\n    assert 'a.b == c' in 'text'\n")
case("in_a_comment", False, "def test_x():\n    # assert a.b == 'c'\n    assert d == 1\n")
case("a_conditional_expression", False, "def test_x():\n    assert (a.b == 'c') if d else True\n")
case(
    "parametrized_and_dead",
    False,
    "@pytest.mark.parametrize('n', [1, 2])\ndef test_x(n):\n    return\n    assert a.b == 'c'\n",
)
case(
    "parametrized_and_skipped",
    False,
    "@pytest.mark.parametrize('n', [1, 2])\ndef test_x(n):\n    pytest.skip('x')\n",
)
case(
    "a_name_defined_twice_the_later_one_is_the_test",
    False,
    """
    def test_x():
        assert a.b == 'c'


    def test_x():
        assert d == 1
    """,
)
case(
    "a_name_defined_twice_and_the_later_one_asserts",
    True,
    """
    def test_x():
        assert d == 1


    def test_x():
        assert a.b == 'c'
    """,
)
# What is skipped or expected to fail, however it came to be.
case("skip_decorator", False, "@pytest.mark.skip\ndef test_x():\n    assert a.b == 'c'\n")
case("xfail_decorator", False, "@pytest.mark.xfail\ndef test_x():\n    assert a.b != 'c'\n")
case(
    "skipif_through_a_variable",
    False,
    "SKIP = pytest.mark.skipif(True, reason='x')\n\n\n@SKIP\ndef test_x():\n    assert a.b == 'c'",
)
case(
    "pytestmark_skip",
    False,
    "pytestmark = pytest.mark.skip\n\n\ndef test_x():\n    assert a.b == 'c'\n",
)
case("skip_call", False, "def test_x():\n    pytest.skip('x')\n    assert a.b == 'c'\n")
case(
    "importorskip",
    False,
    "def test_x():\n    pytest.importorskip('no_such_module')\n    assert a.b == 'c'\n",
)
case(
    "skip_in_a_fixture",
    False,
    """
    @pytest.fixture
    def gate():
        pytest.skip("a fixture")


    def test_x(gate):
        assert a.b == 'c'
    """,
)
case(
    "skip_in_a_helper",
    False,
    """
    def helper():
        pytest.skip("a helper")


    def test_x():
        helper()
        assert a.b == 'c'
    """,
)
case("xfail_at_run_time", False, "def test_x():\n    pytest.xfail('x')\n    assert a.b == 'c'\n")
case(
    "xfail_that_passes",
    False,
    "@pytest.mark.xfail(reason='x')\ndef test_x():\n    assert a.b == 'c'\n",
)
# What can run: each is a pin.
case("top_level", True, "def test_x():\n    assert a.b == 'c'\n")
case("in_a_for", True, "def test_x():\n    for _ in (1,):\n        assert a.b == 'c'\n")
case(
    "in_a_with", True, "def test_x():\n    with contextlib.suppress():\n        assert a.b == 'c'\n"
)
case(
    "in_a_try_and_a_finally",
    True,
    """
    def test_x():
        try:
            assert a.b == 'c'
        finally:
            assert d == 1
    """,
)
case(
    "in_an_except_that_runs",
    True,
    """
    def test_x():
        try:
            raise ValueError
        except ValueError:
            assert a.b == 'c'
    """,
)
case("in_a_live_if", True, "def test_x():\n    if d == 1:\n        assert a.b == 'c'\n")
case(
    "in_the_else_of_a_loop",
    True,
    "def test_x():\n    for _ in (1,):\n        pass\n    else:\n        assert a.b == 'c'\n",
)
case(
    "in_a_while_that_runs_once",
    True,
    "def test_x():\n    n = 0\n    while n < 1:\n        assert a.b == 'c'\n        n += 1\n",
)
case(
    "in_the_case_taken",
    True,
    "def test_x():\n    match d:\n        case 1:\n            assert a.b == 'c'\n",
)
case(
    "after_the_with_of_raises",
    True,
    """
    def test_x():
        with pytest.raises(ValueError):
            raise ValueError
        assert a.b == 'c'
    """,
)
case("a_conjunct_with_a_message", True, "def test_x():\n    assert a.b == 'c' and d == 1, 'msg'\n")
case(
    "a_conjunct_on_a_later_line",
    True,
    "def test_x():\n    assert (\n        d == 1\n        and a.b == 'c'\n    )\n",
)
case(
    "parametrized",
    True,
    "@pytest.mark.parametrize('n', [1, 2])\ndef test_x(n):\n    assert a.b == 'c'\n",
)
case(
    "a_skip_in_a_nested_function_never_called",
    True,
    "def test_x():\n    def later():\n        pytest.skip('x')\n    assert a.b == 'c'\n",
)
case(
    "a_mark_that_skips_nothing",
    True,
    "@pytest.mark.filterwarnings('ignore')\ndef test_x():\n    assert a.b == 'c'\n",
)
case(
    "live_next_to_a_dead_one",
    True,
    "def test_x():\n    if False:\n        assert a.b == 'c'\n    assert a.b == 'c'\n",
)
case(
    "a_fixture_that_does_not_skip",
    True,
    "@pytest.fixture\ndef gate():\n    return 1\n\n\ndef test_x(gate):\n    assert a.b == 'c'\n",
)

SERVER = Path(pins.__file__).resolve().parents[2]


def project(
    root: Path,
    files: dict[str, str],
    table: dict[str, tuple[str, ...]],
    *,
    conftest: str = "",
    ini: str = "[pytest]\n",
) -> None:
    """A project under ``root`` whose conftest registers a guard of ``table``."""
    for name, source in files.items():
        (root / name).write_text(source, encoding="utf-8")
    (root / "pytest.ini").write_text(ini, encoding="utf-8")
    (root / "conftest.py").write_text(
        "from tests.core import pins\n\n\ndef pytest_configure(config):\n"
        f"    config.pluginmanager.register(pins.Guard({table!r}), 'aibi-pins')\n\n\n{conftest}",
        encoding="utf-8",
    )


def run(
    root: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[int, dict[str, str], str]:
    """``pytest`` in the project: its exit code, each test's outcome by node id (parameters
    folded: one failure of a function is its outcome) and its output."""
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-rA",
            "--tb=short",
            *args,
        ],
        cwd=root,
        env={"PYTHONPATH": str(SERVER), "PATH": "/usr/bin:/bin", **(env or {})},
        capture_output=True,
        text=True,
        timeout=120,
    )
    outcomes: dict[str, str] = {}
    for line in ran.stdout.splitlines():
        word, _, rest = line.partition(" ")
        if word in ("PASSED", "FAILED", "ERROR") and "::" in rest:
            node = rest.split(" ")[0].split("[", 1)[0]
            if word != "PASSED" or node not in outcomes:
                outcomes[node] = "PASSED" if word == "PASSED" else "FAILED"
    return ran.returncode, outcomes, ran.stdout


def test_the_guard_refuses_each_placement_that_cannot_run_and_lets_the_others_through(
    tmp_path: Path,
) -> None:
    files = {f"test_{name}.py": PRELUDE + "\n\n" + source for name, (_, source) in CASES.items()}
    table = {f"test_{name}.py::test_x": (NEEDLE,) for name in CASES}
    project(tmp_path, files, table)
    _, outcomes, out = run(tmp_path)
    assert SUMMARY not in out, out
    expected = {
        f"test_{name}.py::test_x": "PASSED" if passes else "FAILED"
        for name, (passes, _) in CASES.items()
    }
    wrong = {
        node: (want, outcomes.get(node))
        for node, want in expected.items()
        if outcomes.get(node) != want
    }
    assert not wrong, (wrong, out[-3000:])
    assert len(CASES) >= 40
    assert sum(1 for passes, _ in CASES.values() if passes) >= 12


def test_the_table_of_placements_is_one_function_a_module_named_for_its_case() -> None:
    for name, (_, source) in CASES.items():
        assert name.isidentifier(), name
        assert "def test_x(" in source, name


# --- A pin that was deselected, ignored or never reached ------------------------------------------

PIN_FILE = (
    PRELUDE + "\n\ndef test_x():\n    assert a.b == 'c'\n\n\ndef test_other():\n    assert d == 1\n"
)
OTHER_FILE = PRELUDE + "\n\ndef test_free():\n    assert d == 1\n"
PIN = {"test_pin.py::test_x": (NEEDLE,)}
DESELECTING = """
def pytest_collection_modifyitems(items):
    items[:] = [each for each in items if each.name != "test_x"]
"""
DESELECTING_REPORTED = """
def pytest_collection_modifyitems(config, items):
    gone = [each for each in items if each.name == "test_x"]
    config.hook.pytest_deselected(items=gone)
    items[:] = [each for each in items if each.name != "test_x"]
"""
MARKING = """
import pytest


def pytest_collection_modifyitems(items):
    for each in items:
        if each.name == "test_x":
            each.add_marker(pytest.mark.slow)
"""
MARKERS = '[pytest]\nmarkers = slow: a marker\naddopts = -m "not slow"\n'


def pin_project(
    root: Path, *, conftest: str = "", ini: str = "[pytest]\n", extra: dict[str, str] | None = None
) -> None:
    project(
        root,
        {"test_pin.py": PIN_FILE, "test_free.py": OTHER_FILE, **(extra or {})},
        PIN,
        conftest=conftest,
        ini=ini,
    )


def test_a_pin_that_nothing_deselects_runs_and_the_session_passes(tmp_path: Path) -> None:
    pin_project(tmp_path)
    code, outcomes, out = run(tmp_path)
    assert (code, outcomes["test_pin.py::test_x"]) == (0, "PASSED"), out
    assert SUMMARY not in out


@pytest.mark.parametrize(
    ("conftest", "ini"),
    [
        (DESELECTING, "[pytest]\n"),
        (DESELECTING_REPORTED, "[pytest]\n"),
        (MARKING, MARKERS),
    ],
    ids=["a_hook", "a_hook_that_reports_it", "a_marker_and_the_configuration_s_m"],
)
def test_a_pin_that_a_hook_or_a_marker_deselects_fails_the_session(
    tmp_path: Path, conftest: str, ini: str
) -> None:
    pin_project(tmp_path, conftest=conftest, ini=ini)
    for args in ((), ("test_pin.py",), (".",), ("test_pin.py::test_x",)):
        code, outcomes, out = run(tmp_path, *args)
        assert "test_pin.py::test_x" not in outcomes, out
        assert code == 1, (args, out)
        assert SUMMARY in out, (args, out)
        assert "test_pin.py::test_x: deselected" in out, (args, out)


def test_a_pin_in_a_module_that_is_ignored_fails_the_session_that_asks_for_it(
    tmp_path: Path,
) -> None:
    pin_project(tmp_path, conftest='collect_ignore = ["test_pin.py"]\n')
    # ``collect_ignore`` ignores a module found in a directory, not one named
    for args in ((), (".",)):
        code, outcomes, out = run(tmp_path, *args)
        assert code != 0, (args, out)
        assert "test_pin.py::test_x" not in outcomes, (args, out)
    code, _, out = run(tmp_path, ".")
    assert "test_pin.py::test_x: not collected" in out, out
    # a run that does not ask for the module is not failed for it
    code, _, out = run(tmp_path, "test_free.py")
    assert (code, SUMMARY in out) == (0, False), out


@pytest.mark.parametrize(
    "args",
    [
        ("-k", "other"),
        ("-kother",),
        ("-m", "not slow"),
        ("-qm", "not slow"),
        ("--deselect", "test_pin.py::test_x"),
        ("--deselect=test_pin.py::test_x",),
        ("--ignore", "test_pin.py"),
        ("--collect-only",),
        ("test_free.py",),
        ("test_pin.py::test_other",),
    ],
)
def test_a_run_a_person_narrowed_is_not_failed_for_a_pin_it_left_out(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    pin_project(tmp_path, ini=MARKERS.replace('addopts = -m "not slow"\n', ""), conftest=MARKING)
    code, _, out = run(tmp_path, *args)
    assert (code, SUMMARY in out) == (0, False), out


def test_a_run_narrowed_in_the_environment_is_not_failed_either(tmp_path: Path) -> None:
    pin_project(tmp_path, conftest=DESELECTING)
    code, _, out = run(tmp_path, env={"PYTEST_ADDOPTS": "-k other"})
    assert (code, SUMMARY in out) == (0, False), out
    code, _, out = run(tmp_path, env={"PYTEST_ADDOPTS": "-q"})
    assert (code, SUMMARY in out) == (1, True), out


def test_a_run_that_stopped_at_a_failure_is_not_failed_for_a_pin_it_did_not_reach(
    tmp_path: Path,
) -> None:
    pin_project(tmp_path, extra={"test_a_fails.py": "def test_fails():\n    assert False\n"})
    code, outcomes, out = run(tmp_path, "-x", "test_a_fails.py", "test_pin.py")
    assert (code, SUMMARY in out) == (1, False), out
    assert outcomes == {"test_a_fails.py::test_fails": "FAILED"}


def test_a_pin_that_fails_on_its_own_is_one_failure_and_not_also_a_missing_pin(
    tmp_path: Path,
) -> None:
    failing = PRELUDE + "\n\ndef test_x():\n    assert a.b == 'd'\n"
    project(tmp_path, {"test_pin.py": failing}, PIN)
    code, outcomes, out = run(tmp_path)
    assert (code, outcomes, SUMMARY in out) == (1, {"test_pin.py::test_x": "FAILED"}, False), out


# --- The guard in the core suite ------------------------------------------------------------------


def test_the_guard_is_installed_for_the_core_suite(request: pytest.FixtureRequest) -> None:
    guard = request.config.pluginmanager.get_plugin("aibi-pins")
    assert isinstance(guard, pins.Guard)
    assert guard.table is pin_table.PINS
    assert pins.skipped_pin(type("R", (), {"skipped": True})(), "a.py::b", {"a.py::b"})
    assert not pins.skipped_pin(type("R", (), {"skipped": True})(), "a.py::c", {"a.py::b"})
    assert not pins.skipped_pin(type("R", (), {"skipped": False})(), "a.py::b", {"a.py::b"})
