"""Caveats (SPEC §8.3) and the vocabulary of evaluation (§6.2, §6.3, §6.6)."""

from typing import Any

import pytest
from pydantic import ValidationError

from aibi.core.schema.caveats import CORE_SEVERITIES, Caveat, CaveatCode, Severity, sort_caveats
from aibi.core.schema.output import text
from aibi.core.schema.semantics import ExclusionReason, Flag, ObservationState, Reason


def caveat(code: str, severity: str, affects: list[str] | None = None) -> Caveat:
    value: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": [{"text": "x"}],
        "affects": affects or [],
    }
    return Caveat.model_validate(value)


def test_every_core_code_declares_a_severity() -> None:
    assert set(CORE_SEVERITIES) == set(CaveatCode)
    assert CORE_SEVERITIES[CaveatCode.TIME_ORIGIN_MISMATCH] is Severity.BLOCK
    assert {CORE_SEVERITIES[code] for code in CaveatCode} == set(Severity)


def test_core_codes_carry_their_declared_severity() -> None:
    assert caveat("SMALL_N", "warn").severity is Severity.WARN
    with pytest.raises(ValidationError, match="SMALL_N has severity warn"):
        caveat("SMALL_N", "info")
    with pytest.raises(ValidationError, match="Not a core caveat code"):
        caveat("SMALL_SAMPLE", "warn")


def test_pack_codes_are_namespaced() -> None:
    assert caveat("testpack.EDITION_PIN", "info").code == "testpack.EDITION_PIN"
    for bad in ("testpack.edition", "TESTPACK.EDITION", "a__b.CODE", "testpack."):
        with pytest.raises(ValidationError):
            caveat(bad, "info")


def test_affected_paths_are_sorted_and_unique() -> None:
    made = caveat("SUPPRESSED", "info", ["/values/view", "/population/0", "/values/view"])
    assert made.affects == ["/population/0", "/values/view"]
    with pytest.raises(ValidationError):
        caveat("SUPPRESSED", "info", ["population"])


def test_caveats_sort_by_code_then_paths_and_drop_duplicates() -> None:
    a = caveat("SUPPRESSED", "info", ["/population/1"])
    b = caveat("SUPPRESSED", "info", ["/population/0"])
    c = caveat("NOT_ESTIMABLE", "info", ["/values/view"])
    assert sort_caveats([a, b, c, a]) == [c, b, a]


def test_messages_are_segments() -> None:
    made = caveat("SMALL_N", "warn")
    assert made.message == [text("x")]
    with pytest.raises(ValidationError):
        Caveat.model_validate(
            {"code": "SMALL_N", "severity": "warn", "message": "plain", "affects": []}
        )


def test_the_vocabulary_of_evaluation() -> None:
    assert [state.value for state in ObservationState] == [
        "PRESENT",
        "ABSENT",
        "NOT_ASSESSED",
        "NOT_APPLICABLE",
        "UNKNOWN",
    ]
    assert [reason.value for reason in Reason] == [
        "NOT_ASSESSED",
        "NOT_COVERED",
        "NO_INFORMATION",
        "NO_PARENT",
        "OUT_OF_SCOPE",
        "NO_ROWS",
    ]
    assert {flag.value for flag in Flag} == {"SCOPE_PARTIAL", "COVERAGE_PROPOSED"}
    assert {reason.value for reason in ExclusionReason} == {r.value for r in Reason} | {
        "NOT_APPLICABLE",
        "INVALID_VALUE",
    }


def test_caveat_order_is_total() -> None:
    from aibi.core.schema.output import data

    as_text = Caveat(
        code="SMALL_N", severity=Severity.WARN, message=[text("x")], affects=["/values"]
    )
    as_data = as_text.model_copy(update={"message": [data("x")]})
    assert sort_caveats([as_data, as_text]) == sort_caveats([as_text, as_data])


def test_segments_hold_unicode_text() -> None:
    from aibi.core.schema.output import Data, DataSegment, TextSegment

    for model, member in ((TextSegment, "text"), (DataSegment, "data"), (Data, "data")):
        for bad in ("caf\udce9", "x\ufffe"):
            with pytest.raises(ValidationError):
                model.model_validate({member: bad})


def test_caveats_built_without_validation_sort_too() -> None:
    built = Caveat.model_construct(
        code=CaveatCode.SMALL_N,
        severity=CORE_SEVERITIES[CaveatCode.SMALL_N],
        message=[text("x")],
        affects=["/values"],
    )
    other = caveat("DRAFT_RELEASE", CORE_SEVERITIES[CaveatCode.DRAFT_RELEASE].value, ["/values"])
    assert sort_caveats([built, other]) == [other, built]
