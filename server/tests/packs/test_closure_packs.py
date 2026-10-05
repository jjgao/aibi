"""The generated set of D399 over the packs (``aibi.packs``): the packs' types that hold server
text, seeded with the core's, computed here since ``tests/core`` loads no pack. The guard's
tests run over the core's set (``tests/core/schema/test_closure.py``); a pack's types meet the
same guard, and its count is checked in so that a pack type that holds segments is seen. The
packs are imported by the test, not by the module, so that collecting it loads no pack. A
session of both suites depends on the order pytest gives them: ``tests/core`` runs first
(alphabetically), as CI runs them (``pytest tests/core``, then the rest), and a pack loaded
before it would be a pack the core suite must not see (SPEC P8); run alone, this test is
independent of the core suite."""

from tests.core import generated_set

PACKS_SET_SIZE = 0
"""The packs' types that hold server text (none on this base; the oncology pack adds its own)."""

NOT_WALKED: dict[str, str] = {}
"""The packs' plain classes that carry such a type, and protocols returning one, with why."""


def test_the_packs_generated_set_is_the_checked_in_size() -> None:
    import aibi.core
    import aibi.packs

    core = generated_set.generated_set(aibi.core)
    found = generated_set.generated_set(aibi.packs, seeds=core.values())
    assert len(found) == PACKS_SET_SIZE, sorted(found)
    bearing = {*core.values(), *found.values()}
    assert generated_set.carriers(aibi.packs, bearing).keys() == NOT_WALKED.keys()
