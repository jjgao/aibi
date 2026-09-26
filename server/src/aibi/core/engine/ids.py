"""Hashes, ids and digests (SPEC §7.6, §9.3; D282, D283).

Every hash is the lowercase hexadecimal SHA-256 of the RFC 8785 serialisation of the object it
names, in UTF-8: derivation ids are ``drv:`` and leaf keys ``leaf:`` followed by it, digests
``sha256:``. The hashing, rounding and digests themselves are ``schema.digests``', so that an
output can check its own digest (D298); they are named here too, beside the ids.
"""

from collections.abc import Sequence
from typing import cast

from pydantic import JsonValue

from aibi.core.schema.caveats import Caveat
from aibi.core.schema.digests import (
    COUNT_MEMBERS,
    RESULT_MEMBERS,
    ROUNDING_FLOOR,
    SIGNIFICANT_DIGITS,
    digest,
    digested,
    hashed,
    order_key,
    output_digest,
    reduced,
    rounded,
    sorted_unique,
)
from aibi.core.schema.numbers import Proportion
from aibi.core.schema.results import Population, ResultEnvelope


def derivation_id(value: JsonValue) -> str:
    """``drv:`` and the hash of the object a cohort or result id names (§7.6)."""
    return "drv:" + hashed(value)


def leaf_key(clause: JsonValue) -> str:
    """``leaf:`` and the hash of a canonical top-level clause (§6.6, §7.6)."""
    return "leaf:" + hashed(clause)


def count_digest(population: Population, size: Proportion, caveats: Sequence[Caveat]) -> str:
    """The digest of a cohort count, over its population, size and caveats as it carries them,
    after the disclosure pass (§7.6, §8.4)."""
    dumped = {
        "population": cast(JsonValue, population.model_dump(mode="json")),
        "size": cast(JsonValue, size.model_dump(mode="json")),
    }
    return output_digest(dumped, caveats, COUNT_MEMBERS)


def result_digest(envelope: ResultEnvelope) -> str:
    """The digest of a result envelope, over its cohorts, population, analysed counts, values
    and caveats as it carries them (§7.6)."""
    dumped = cast(dict[str, JsonValue], envelope.model_dump(mode="json"))
    return output_digest(dumped, envelope.caveats, RESULT_MEMBERS)


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
