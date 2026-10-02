"""Hashes, ids and digests (SPEC §7.6, §9.3; D282, D283).

Every hash is the lowercase hexadecimal SHA-256 of the RFC 8785 serialisation of the object it
names, in UTF-8: derivation ids are ``drv:`` and leaf keys ``leaf:`` followed by it, digests
``sha256:``. Sorting by a canonical serialisation compares it as UTF-16 code units, as RFC 8785
orders keys.

A digest hashes exactly an output's digested members, as the output carries them: members whose
name ends in ``_text`` are left out at every depth (rendered text, outside every digest), caveats
are reduced to ``{code, severity, affects}``, sorted and de-duplicated, with ``DRAFT_RELEASE``
left out, and every floating-point number is rounded (``rounded``). Integers are never rounded.
"""

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from pydantic import JsonValue

from aibi.core.schema.caveats import Caveat, CaveatCode
from aibi.core.schema.jsonio import canonical, utf16_key
from aibi.core.schema.numbers import Proportion
from aibi.core.schema.results import Population, ResultEnvelope

ROUNDING_FLOOR = 1e-12
"""Floating-point values of smaller magnitude are 0 before hashing (§9.3)."""
SIGNIFICANT_DIGITS = 10
"""Floating-point values are rounded to this many significant digits before hashing (§9.3)."""

COUNT_MEMBERS = ("population", "size", "caveats")
"""The digested members of a cohort count (§7.6)."""
RESULT_MEMBERS = ("cohorts", "population", "analysed", "values", "caveats")
"""The digested members of a result envelope (§7.6)."""


def hashed(value: JsonValue) -> str:
    """The lowercase hexadecimal SHA-256 of ``value``'s RFC 8785 serialisation."""
    return hashlib.sha256(canonical(value)).hexdigest()


def derivation_id(value: JsonValue) -> str:
    """``drv:`` and the hash of the object a cohort or result id names (§7.6)."""
    return "drv:" + hashed(value)


def leaf_key(clause: JsonValue) -> str:
    """``leaf:`` and the hash of a canonical top-level clause (§6.6, §7.6)."""
    return "leaf:" + hashed(clause)


def order_key(value: JsonValue) -> bytes:
    """A value's canonical serialisation as UTF-16 code units: the order of step 8 (§7.6)."""
    return utf16_key(canonical(value).decode())


def sorted_unique(values: Iterable[JsonValue]) -> list[JsonValue]:
    """Values sorted by their canonical serialisation, those serialised alike kept once."""
    found: dict[bytes, JsonValue] = {}
    for value in values:
        found.setdefault(order_key(value), value)
    return [found[key] for key in sorted(found)]


def rounded(value: float) -> float:
    """A floating-point value as it is hashed (§9.3, D283): 0 below ``ROUNDING_FLOOR`` in
    magnitude, and otherwise the double's exact value correctly rounded to
    ``SIGNIFICANT_DIGITS`` significant digits, ties to even, read back as the nearest double."""
    if not math.isfinite(value):
        raise ValueError("a digested number is finite (§8.2)")
    if abs(value) < ROUNDING_FLOOR:
        return 0.0
    found = float(format(value, f".{SIGNIFICANT_DIGITS - 1}e"))
    if not math.isfinite(found):
        raise ValueError("a digested number stays finite when it is rounded")
    return found


def digested(value: JsonValue) -> JsonValue:
    """A value as a digest hashes it: ``_text`` members left out at every depth, and floats
    rounded; integers, strings and booleans as they are."""
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    if isinstance(value, float):
        return rounded(value)
    if isinstance(value, list):
        return [digested(item) for item in value]
    return {key: digested(member) for key, member in value.items() if not key.endswith("_text")}


def reduced(caveats: Sequence[Caveat]) -> list[JsonValue]:
    """Caveats as a digest holds them: ``{code, severity, affects}``, ``DRAFT_RELEASE`` left out
    since it describes a release's status, not the computation, sorted and de-duplicated."""
    return sorted_unique(
        {
            "code": caveat.code,
            "severity": caveat.severity.value,
            "affects": cast(list[JsonValue], list(caveat.affects)),
        }
        for caveat in caveats
        if caveat.code != CaveatCode.DRAFT_RELEASE
    )


def digest(members: Mapping[str, JsonValue]) -> str:
    """``sha256:`` and the hash of an output's digested members, prepared as ``digested``
    prepares them; the caller gives exactly the members of §7.6, caveats already reduced."""
    return "sha256:" + hashed(digested(dict(members)))


def count_digest(population: Population, size: Proportion, caveats: Sequence[Caveat]) -> str:
    """The digest of a cohort count, over its population, size and caveats as it carries them,
    after the disclosure pass (§7.6, §8.4)."""
    return digest(
        {
            "population": cast(JsonValue, population.model_dump(mode="json")),
            "size": cast(JsonValue, size.model_dump(mode="json")),
            "caveats": reduced(caveats),
        }
    )


def result_digest(envelope: ResultEnvelope) -> str:
    """The digest of a result envelope, over its cohorts, population, analysed counts, values
    and caveats as it carries them (§7.6)."""
    dumped = cast(dict[str, JsonValue], envelope.model_dump(mode="json"))
    members = {member: dumped[member] for member in RESULT_MEMBERS if member != "caveats"}
    return digest({**members, "caveats": reduced(envelope.caveats)})


__all__ = [
    "COUNT_MEMBERS",
    "RESULT_MEMBERS",
    "ROUNDING_FLOOR",
    "SIGNIFICANT_DIGITS",
    "count_digest",
    "derivation_id",
    "digest",
    "digested",
    "hashed",
    "leaf_key",
    "order_key",
    "reduced",
    "result_digest",
    "rounded",
    "sorted_unique",
]
