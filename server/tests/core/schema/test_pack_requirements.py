"""The cap on the requirements of the installed packs' analyses (D420).

``MAX_PACK_REQUIREMENTS`` (4,096) bounds every element of every ``requires`` of every pack's
analysis, together across the packs: those with a kind, those with only a predicate and those
without either (a view's cohorts). Registration refuses one more, naming the limit, the total
and each pack's share in pack id order, whatever order the packs were given in. Packs and
analyses here are letters and numbers.
"""

from collections.abc import Callable
from typing import Any

import pytest
from tests.core.analyses.applicability_oracle import Implementation, entry, manifest

import aibi
from aibi.core.schema.limits import MAX_ENTRIES, MAX_PACK_REQUIREMENTS
from aibi.core.schema.pack_api import Pack, PackError, PackRegistry

KINDS: list[Callable[[str, int], dict[str, Any]]] = [
    lambda pack, role: {"role": f"r{role}", "kind": "column", "on": "unit", "min": role % 3},
    lambda pack, role: {"role": f"r{role}", "predicate": f"{pack}.yes"},
    lambda pack, role: {"role": f"r{role}", "min": 1, "max": 6},
    lambda pack, role: {"role": f"r{role}", "kind": "table", "predicate": f"{pack}.yes"},
    lambda pack, role: {"role": f"r{role}"},
]
"""A requirement with a kind, one with only a predicate, one with neither (of a ``min`` and a
``max``), one with both, and a bare one: a role alone."""


def pack(name: str, sizes: list[int]) -> Pack:
    """A pack whose analyses have ``sizes`` requirements each, of every kind in turn."""
    analyses = [
        Implementation(
            entry(name, number, [KINDS[role % len(KINDS)](name, role) for role in range(size)])
        )
        for number, size in enumerate(sizes)
    ]
    return Pack(
        manifest=manifest(name),
        analyses=analyses,
        requirement_predicates={"yes": lambda release: True},
    )


def packs(extra: int, *, extra_in: str = "pf") -> list[Pack]:
    """Sixteen packs whose analyses have 4,096 requirements in all, 256 each, and ``extra``
    more in another analysis of the pack ``extra_in``."""
    names = [f"p{number:x}" for number in range(16)]
    assert MAX_PACK_REQUIREMENTS == 16 * 4 * MAX_ENTRIES
    return [
        pack(name, [MAX_ENTRIES] * 4 + ([extra] if extra and name == extra_in else []))
        for name in names
    ]


def problems(given: list[Pack], labels: list[str] | None = None) -> tuple[str, ...]:
    with pytest.raises(PackError) as raised:
        PackRegistry(given, core_version=aibi.__version__, labels=labels)
    return raised.value.problems


def test_the_packs_may_have_as_many_requirements_as_the_cap() -> None:
    registry = PackRegistry(packs(0), core_version=aibi.__version__)
    counted = sum(len(one.entry.fields.requires) for one in registry.analyses())
    assert counted == MAX_PACK_REQUIREMENTS


@pytest.mark.parametrize("kind", range(len(KINDS)))
def test_one_more_requirement_of_any_kind_is_refused_naming_every_pack_s_share(
    kind: int,
) -> None:
    """One more, of each kind (with a kind, a predicate alone, neither, both, a bare role), in a
    pack of its own share well under the cap, is refused: the cap is over the packs together."""
    extra = Pack(
        manifest=manifest("pg"),
        analyses=[Implementation(entry("pg", 0, [KINDS[kind]("pg", 0)]))],
        requirement_predicates={"yes": lambda release: True},
    )
    found = problems([*packs(0), extra])
    shares = ", ".join(f"p{number:x} has 256" for number in range(16))
    assert found == (
        "the packs' analyses have 4097 requirements in all, more than the 4096 allowed "
        f"(MAX_PACK_REQUIREMENTS): {shares}, pg has 1",
    )


def test_the_order_of_the_packs_or_their_modules_does_not_decide_who_is_named() -> None:
    given = packs(1)
    found = problems(given)
    assert found == problems(list(reversed(given)))
    assert "pf has 257" in found[0]
    modules = [f"m{number}" for number in range(16)]
    labelled = problems(given, modules)
    reordered = problems(list(reversed(given)), list(reversed(modules)))
    assert labelled == reordered
    assert labelled[0].endswith(
        ", ".join(f"the module m{number} (pack p{number:x}) has 256" for number in range(15))
        + ", the module m15 (pack pf) has 257"
    )


def test_the_cap_is_reported_with_the_packs_other_problems() -> None:
    """The cap is checked when every pack has been read, beside the other problems of the
    packs, which it does not hide."""
    given = [*packs(1), Pack(manifest=manifest("pf"))]
    found = problems(given)
    assert any("is registered twice" in one for one in found)
    assert any(one.startswith("the packs' analyses have 4097 requirements") for one in found)
