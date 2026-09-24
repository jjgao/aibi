"""The core suite must pass with no pack registered (SPEC P8), so it must not load one either."""

import sys
from pathlib import Path

import pytest

CORE_TESTS = Path(__file__).parent


def pytest_sessionfinish(session: pytest.Session) -> None:
    # A run that also collected other tests may load packs; only a core-only run is checked.
    if not session.items or not all(item.path.is_relative_to(CORE_TESTS) for item in session.items):
        return
    loaded = sorted(m for m in sys.modules if m == "aibi.packs" or m.startswith("aibi.packs."))
    if loaded:
        pytest.exit("the core tests loaded " + ", ".join(loaded), returncode=1)
