"""The core suite must pass with no pack registered (SPEC P8), so it must not load one either;
and every store a test opens is closed, so that no derivation log's pruning thread outlives the
suite (D300)."""

import sys
import threading
from pathlib import Path

import pytest

CORE_TESTS = Path(__file__).parent.resolve()
_loaded: list[str] = []
_pruning: list[str] = []
PRUNING = "aibi-log-pruning"
"""The name of a derivation log's pruning thread (``DerivationLog.start_pruning``)."""


def _only_core_tests_collected(config: pytest.Config) -> bool:
    # Decide on what the run was asked to collect, not on what -k, -m or --deselect kept:
    # collecting any other test module may already have imported a pack.
    root = config.invocation_params.dir
    return all(
        (root / arg.split("::", 1)[0]).resolve().is_relative_to(CORE_TESTS) for arg in config.args
    )


def pytest_sessionfinish(session: pytest.Session) -> None:
    _pruning[:] = [thread.name for thread in threading.enumerate() if thread.name == PRUNING]
    if _pruning:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if not session.items or not _only_core_tests_collected(session.config):
        return
    _loaded[:] = sorted(m for m in sys.modules if m == "aibi.packs" or m.startswith("aibi.packs."))
    if _loaded:
        # Don't raise: pytest.exit here would suppress the whole terminal summary.
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if _pruning:
        terminalreporter.section("a store was left open", sep="=", red=True)
        terminalreporter.line(f"{len(_pruning)} derivation log pruning threads are still running")
    if _loaded:
        terminalreporter.section("the core tests loaded packs (SPEC P8)", sep="=", red=True)
        terminalreporter.line(", ".join(_loaded))
