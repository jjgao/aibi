"""The disclosure of ``summary.members`` checked by brute force (SPEC §8.4; D331, D332).

Under any disclosure setting, set by the dataset or by the floor, and wherever the dataset allows
no row ids, a view of it is refused, whatever its cohort, and nothing a document is checked with
names a unit's key; without either, its whole output, over every way a cohort of up to 6 units can
split into TRUE, FALSE and UNKNOWN units, a page of 1, 3, all or 1,000 keys at a time, names each
member's key once, in the canonical form's order, and no other unit's key."""

import itertools
import json
from collections.abc import Callable, Iterator, Sequence
from typing import Any, cast

import pytest
from pydantic import JsonValue

from aibi.core.analyses import members
from aibi.core.analyses.existence import CohortAt
from aibi.core.analyses.results import envelope
from aibi.core.engine import build
from aibi.core.engine.data import Release
from aibi.core.engine.members import Key, ordered
from aibi.core.engine.sql import Accounting
from aibi.core.schema.analyses import MAX_MEMBERS, MembersParams
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import Reason

Check = Callable[..., Any]

YES = {"kind": "value", "column": "things.flag", "values": ["yes"]}
FLAGS = {"T": "yes", "F": "no", "U": None}
SETTINGS: list[tuple[dict[str, Any], int | None]] = [
    *(({"disclosure": {"min_cell_count": k}}, None) for k in range(2, 6)),
    *(({}, k) for k in range(2, 6)),
    *(({"disclosure": {"min_cell_count": k, "allow_row_ids": False}}, None) for k in (2, 5)),
    ({"disclosure": {"min_cell_count": 3}}, 5),
]
"""Each disclosure setting: the dataset's, the floor, a dataset without row ids, and both."""


def things(pattern: str, **dataset: Any) -> Release:
    """Units keyed ``key-<n>``, each TRUE, FALSE or UNKNOWN for ``YES`` as ``pattern`` says."""
    return build.release(
        [
            build.dataset(**dataset),
            build.table("things", ["thing_id"]),
            build.column("things.thing_id", "string"),
            build.column(
                "things.flag",
                "category",
                permissible_values={"values": [{"value": "yes"}, {"value": "no"}]},
            ),
        ],
        {
            "things": [
                {"thing_id": f"key-{index}", "flag": FLAGS[state]}
                for index, state in enumerate(pattern)
            ]
        },
    )


def document(**view: Any) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "things",
        "cohorts": {"yes": {"all": [YES]}},
        "views": [{"analysis": "summary.members", **view}],
    }


def patterns(most: int) -> Iterator[str]:
    for size in range(most + 1):
        for states in itertools.product("TFU", repeat=size):
            yield "".join(states)


def strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in cast(list[object], value):
            yield from strings(item)
    elif isinstance(value, dict):
        for key, item in cast(dict[str, object], value).items():
            yield key
            yield from strings(item)


def named(value: object) -> set[str]:
    """The units' keys an output names anywhere."""
    return {text for text in strings(value) if text.startswith("key-")}


@pytest.mark.parametrize(("dataset", "floor"), SETTINGS)
def test_under_any_disclosure_setting_no_view_lists_a_key_and_nothing_checked_names_one(
    check: Check, dataset: dict[str, Any], floor: int | None
) -> None:
    for pattern in ["", *("T" * size for size in (1, 4, 8)), "TTTTFFUU", "TFUTFUTF"]:
        found = check(document(), things(pattern, **dataset), floor=floor)
        [refusal] = found.refusals
        assert (refusal.code, refusal.path) == (
            RefusalCode.ROW_IDS_NOT_ALLOWED,
            "/views/0/analysis",
        )
        assert found.views == []
        assert named(refusal.model_dump(mode="json")) == set()
        for cohort in found.canonical.cohorts.values():
            assert named(cohort.identity.hashed()) == set()


def _accounting(pattern: str) -> Accounting:
    unknown = pattern.count("U")
    return Accounting(
        n_true=pattern.count("T"),
        n_false=pattern.count("F"),
        n_unknown=unknown,
        unknown_by_reason={
            reason: unknown if reason is Reason.NO_INFORMATION else 0 for reason in Reason
        },
        unknown_by_clause=(unknown,),
        lift_differs=0,
        marks=frozenset(),
    )


def _pages(view: Any, pattern: str, keys: Sequence[Key], limit: int) -> list[JsonValue]:
    """Every page of a cohort's keys, as its result envelopes carry them."""
    position = CohortAt(view.cohorts[0], _accounting(pattern))
    pages: list[JsonValue] = []
    offset, more = 0, True
    while more:
        params = MembersParams(offset=offset, limit=limit)
        outcome = members.list_members(position, keys, params, k=None)
        result = envelope(
            view,
            outcome,
            issuance="iss:01J0000000000000000000000A",
            written=document(),
            params={},
            engine="aibi test",
        )
        dumped = result.model_dump(mode="json")
        digested = {part: dumped[part] for part in ("population", "analysed", "values")}
        assert named(digested) <= {str(key[0]) for key in keys}
        [at] = dumped["values"]["positions"]
        assert (at["offset"], len(at["keys"]) <= limit) == (offset, True)
        assert (
            dumped["analysed"][0]["n"] + dumped["analysed"][0]["excluded_units"]
            == (dumped["population"][0]["n_true"])
        )
        pages += at["keys"]
        offset, more = offset + limit, at["more"]
    return pages


def test_without_a_setting_every_page_of_every_cohort_names_its_members_once_in_order(
    check: Check,
) -> None:
    [view] = check(document(), things("T")).views
    for pattern in patterns(6):
        member_keys = [(f"key-{index}",) for index, state in enumerate(pattern) if state == "T"]
        expected = sorted(
            (key[0] for key in member_keys),
            key=lambda key: json.dumps([key]).encode("utf-16-be"),
        )
        listing = ordered(member_keys)
        for limit in {1, 3, max(len(listing), 1), MAX_MEMBERS}:
            pages = _pages(view, pattern, listing, limit)
            assert pages == [[{"data": key}] for key in expected], (pattern, limit)
