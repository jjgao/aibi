"""The tests D422 cites as pins are pins (SPEC D422): they exist, are not decorated, skipped or
expected to fail, and assert what D422 says, as ``tests/core/pins.py`` reads them from their
syntax trees; and the guard that reports a cited test skipped at run time as failed does."""

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from tests.core import pins

from aibi.core.api import errors
from aibi.core.mcp.calls import PER_CLIENT

PLACE = "tests/core/mcp/test_place_policy.py"
TRANSPORT = "tests/core/mcp/test_transport.py"
PAGE = "tests/core/mcp/test_page.py"
WRITES = "tests/core/catalog/test_validate_writes_nothing.py"
PINS: dict[str, tuple[str, ...]] = {
    f"{TRANSPORT}::test_one_client_runs_at_most_its_share_of_the_places_and_others_still_get_one": (
        f"(share.code, share.limit.max, status_of(share)) == ('LIMIT_EXCEEDED', {PER_CLIENT}, 503)",
        "sorted((r.limit.name for r in refused)) == "
        "['client_tool_calls', 'tool_seconds', 'tool_seconds']",
    ),
    f"{TRANSPORT}::test_clients_are_counted_by_the_key_of_their_address": (
        "refused[0].limit.name == 'client_tool_calls'",
    ),
    f"{PAGE}::test_a_call_that_gets_no_place_is_a_page_with_retry_after_and_its_limit": (
        "answered.status_code == 503",
        f"answered.headers['retry-after'] == '{errors.RETRY_IMPORT}'",
        f"'Limit: client_tool_calls, at most {PER_CLIENT}' in parsed(answered.text).text",
    ),
    f"{PLACE}::test_a_json_tool_call_with_no_place_is_a_503_naming_client_tool_calls_with_retry_after": (  # noqa: E501
        f"limit_of(third) == (503, 'client_tool_calls', PER_CLIENT, '{errors.RETRY_IMPORT}')",
        "third.headers['retry-after'] == str(errors.RETRY_IMPORT)",
    ),
    f"{PLACE}::test_after_tool_seconds_a_json_call_is_a_503_and_its_place_may_still_be_held": (
        f"limit_of(answered) == (503, 'tool_seconds', 1, '{errors.RETRY_IMPORT}')",
        "limit_of(served.api('search_catalog', {})) == "
        f"(503, 'client_tool_calls', 2, '{errors.RETRY_IMPORT}')",
    ),
    f"{PLACE}::test_the_servers_own_request_limit_is_a_503_without_retry_after_on_every_path": (
        "status == 503",
        "headers['content-type'].startswith('text/plain')",
        "'retry-after' not in headers",
        "body == b'Service Unavailable'",
    ),
    f"{PLACE}::test_an_mcp_call_with_no_place_is_a_200_tool_error_without_retry_after": (
        "answered.status_code == 200",
        "'retry-after' not in answered.headers",
        "result['isError'] is True",
        "refusal['limit'] == {'name': 'client_tool_calls', 'max': PER_CLIENT}",
    ),
    f"{PLACE}::test_an_mcp_body_of_inline_bytes_is_inline_and_one_byte_more_is_not": (
        "inline.status_code == 200",
        "inline.json()['result']['isError'] is True",
        "'retry-after' not in inline.headers",
        "beyond.status_code != 200",
    ),
    f"{WRITES}::test_validate_document_changes_no_row_and_no_file": (
        "after_rows == before_rows",
        "after_files == before_files",
    ),
}
"""The tests D422 cites, each with the expressions it must assert."""


def test_d422_cites_exactly_the_pins_that_have_needles() -> None:
    assert pins.cited() == set(PINS)


@pytest.mark.parametrize("pin", sorted(PINS))
def test_each_cited_test_is_a_pin(pin: str) -> None:
    path, name = pin.split("::")
    assert pins.reasons_not_a_pin(path, name, PINS[pin]) == []


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


def test_a_live_assertion_is_a_pin_at_the_top_in_a_loop_a_with_and_a_try(module: Any) -> None:
    assert module("def test_x():\n    assert a.b == 'c'\n") == []
    assert module("def test_x():\n    for i in x:\n        assert a.b == 'c'\n") == []
    assert module("def test_x():\n    with x:\n        assert a.b == 'c'\n") == []
    assert (
        module("def test_x():\n    try:\n        assert a.b == 'c'\n    finally:\n        pass\n")
        == []
    )
    assert module("def test_x():\n    assert a.b == 'c' and d == 1, 'msg'\n") == []


@pytest.mark.parametrize(
    "code",
    [
        "def test_x():\n    if False:\n        assert a.b == 'c'\n    assert 1\n",
        "def test_x():\n    def inner():\n        assert a.b == 'c'\n    assert 1\n",
        "def test_x():\n    assert a.b == 'c' or True\n",
        "def test_x():\n    # assert a.b == 'c'\n    note = \"assert a.b == 'c'\"\n    assert 1\n",
        "def test_x():\n    assert 'a.b == c' in 'text'\n",
        "def test_x():\n    assert a.b != 'c'\n",
        "def test_x():\n    x = lambda: [(yield)]\n    assert 1\n",
        "def test_x():\n    match x:\n        case 1:\n            assert a.b == 'c'\n    assert 1\n",  # noqa: E501
    ],
)
def test_a_dead_or_absent_assertion_is_no_pin(module: Any, code: str) -> None:
    assert module(code) != []


@pytest.mark.parametrize(
    "code",
    [
        "import pytest\n@pytest.mark.skip(reason='x')\ndef test_x():\n    assert a.b == 'c'\n",
        "import pytest\n@pytest.mark.xfail\ndef test_x():\n    assert a.b == 'c'\n",
        "import pytest\nS = pytest.mark.skipif(1)\n@S\ndef test_x():\n    assert a.b == 'c'\n",
        "import pytest\npytestmark = pytest.mark.skip\ndef test_x():\n    assert a.b == 'c'\n",
        "import pytest\ndef test_x():\n    pytest.skip('x')\n    assert a.b == 'c'\n",
        "import pytest\ndef test_x():\n    pytest.importorskip('nowhere')\n    assert a.b == 'c'\n",
        "import pytest\ndef test_x():\n    if x:\n        pytest.xfail()\n    assert a.b == 'c'\n",
        "import pytest\ndef test_x():\n  def f():\n    pytest.skip(1)\n  assert a.b == 'c'\n",
    ],
)
def test_a_skipped_or_expected_to_fail_test_is_no_pin(module: Any, code: str) -> None:
    assert module(code) != []


def test_a_parametrized_pin_is_still_a_pin(module: Any) -> None:
    code = (
        "import pytest\n@pytest.mark.parametrize('x', [1])\ndef test_x(x):\n    assert a.b == 'c'\n"
    )
    assert module(code) == []


def test_function_node_matches_a_name_exactly() -> None:
    path = "tests/core/test_pins.py"
    name = "test_function_node_matches_a_name_exactly"
    assert pins.function_node(path, name).name == name
    for other in (name[:-1], name + "s", name[1:]):
        with pytest.raises(AssertionError, match="has no function"):
            pins.function_node(path, other)


# --- The guard ------------------------------------------------------------------------------------

PROJECT = """
import pytest

SKIP = pytest.mark.skipif(True, reason="a condition in a variable")


def test_cited_and_skipped():
    pytest.skip("at run time")


@SKIP
def test_cited_and_skipped_by_a_variable():
    assert True


@pytest.mark.xfail(reason="expected")
def test_cited_and_xfailed():
    assert False


@pytest.mark.xfail(reason="expected, and it passes")
def test_cited_and_xpassing():
    assert True


def test_cited_and_passing():
    assert True


def test_cited_and_failing():
    assert False


def test_free_and_skipped():
    pytest.skip("not a pin")


@pytest.mark.parametrize("n", [1, 2])
def test_cited_and_parametrized_skipped(n):
    pytest.skip("per parameter")
"""
CONFTEST = """
import tests.core.pins as pins

pins.PINNED = {
    "test_project.py::test_cited_and_skipped",
    "test_project.py::test_cited_and_skipped_by_a_variable",
    "test_project.py::test_cited_and_xfailed",
    "test_project.py::test_cited_and_xpassing",
    "test_project.py::test_cited_and_passing",
    "test_project.py::test_cited_and_failing",
    "test_project.py::test_cited_and_parametrized_skipped",
}
from tests.core.pins import pytest_runtest_makereport  # noqa: F401, E402
"""


def test_the_guard_fails_a_cited_test_that_is_skipped_or_expected_to_fail(tmp_path: Path) -> None:
    (tmp_path / "test_project.py").write_text(PROJECT, encoding="utf-8")
    (tmp_path / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    server = Path(pins.__file__).resolve().parents[2]
    ran = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rA", "test_project.py"],
        cwd=tmp_path,
        env={"PYTHONPATH": str(server), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = ran.stdout.splitlines()
    failed = {
        line.split("::", 1)[1].split(" ")[0]
        for line in lines
        if line.startswith(("FAILED", "ERROR"))
    }
    assert failed == {
        "test_cited_and_skipped",
        "test_cited_and_skipped_by_a_variable",
        "test_cited_and_xfailed",
        "test_cited_and_xpassing",
        "test_cited_and_failing",
        "test_cited_and_parametrized_skipped[1]",
        "test_cited_and_parametrized_skipped[2]",
    }, ran.stdout
    assert any(line.startswith("PASSED") and "test_cited_and_passing" in line for line in lines)
    assert any(line.startswith("SKIPPED") and "not a pin" in line for line in lines)
    summary = lines[-1]
    assert "1 skipped" in summary, summary
    assert "xfailed" not in summary, summary


def test_the_guard_is_installed_for_the_core_suite(request: pytest.FixtureRequest) -> None:
    plugins = request.config.pluginmanager.get_plugins()
    assert any(
        hasattr(each, "pytest_runtest_makereport") and "conftest" in str(each) for each in plugins
    )
    assert pins.skipped_pin(type("R", (), {"skipped": True})(), "a.py::b", {"a.py::b"})
    assert not pins.skipped_pin(type("R", (), {"skipped": True})(), "a.py::c", {"a.py::b"})
    assert not pins.skipped_pin(type("R", (), {"skipped": False})(), "a.py::b", {"a.py::b"})
