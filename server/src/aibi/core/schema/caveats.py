"""Caveats (SPEC §8.3).

A caveat names a code, its severity, a message and the parts of the output it affects. Codes are
a stable enum: core codes are unprefixed, pack codes are namespaced (``library.RENEWALS_ESTIMATED``)
and every code, core or pack, declares its severity. ``warn`` and ``block`` caveats have to be
shown to the user; an output with a ``block`` caveat is returned for inspection but must not be
presented as an answer.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Self

from pydantic import AfterValidator, ConfigDict, Field, field_validator, model_validator

from aibi.core.schema.errors import problem
from aibi.core.schema.ids import PACK_CODE_RE, AnyCode, JsonPointer
from aibi.core.schema.jsonio import is_text, utf16_key
from aibi.core.schema.limits import MAX_POINTER
from aibi.core.schema.output import DATA_MARK, LAX, Output, Segment, TextSegment


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class CaveatCode(StrEnum):
    """The core caveat codes, in the order SPEC §8.3 lists them."""

    UNKNOWN_EXCLUDED = "UNKNOWN_EXCLUDED"
    INVALID_EXCLUDED = "INVALID_EXCLUDED"
    SCOPE_PARTIAL = "SCOPE_PARTIAL"
    COVERAGE_PROPOSED = "COVERAGE_PROPOSED"
    UNCONFIRMED_SEMANTICS = "UNCONFIRMED_SEMANTICS"
    UNMAPPED_COMPARISON = "UNMAPPED_COMPARISON"
    TIME_ORIGIN_MISMATCH = "TIME_ORIGIN_MISMATCH"
    CONFOUNDED_WITH_DATASET = "CONFOUNDED_WITH_DATASET"
    COHORTS_OVERLAP = "COHORTS_OVERLAP"
    SMALL_N = "SMALL_N"
    PH_VIOLATED = "PH_VIOLATED"
    DRAFT_RELEASE = "DRAFT_RELEASE"
    SUPPRESSED = "SUPPRESSED"
    NOT_ESTIMABLE = "NOT_ESTIMABLE"
    LIFT_DIFFERS = "LIFT_DIFFERS"
    POOLED_ACROSS_DATASETS = "POOLED_ACROSS_DATASETS"


CORE_SEVERITIES: Mapping[CaveatCode, Severity] = MappingProxyType(
    {
        CaveatCode.UNKNOWN_EXCLUDED: Severity.WARN,
        CaveatCode.INVALID_EXCLUDED: Severity.WARN,
        CaveatCode.SCOPE_PARTIAL: Severity.WARN,
        CaveatCode.COVERAGE_PROPOSED: Severity.WARN,
        CaveatCode.UNCONFIRMED_SEMANTICS: Severity.WARN,
        CaveatCode.UNMAPPED_COMPARISON: Severity.WARN,
        CaveatCode.TIME_ORIGIN_MISMATCH: Severity.BLOCK,
        CaveatCode.CONFOUNDED_WITH_DATASET: Severity.WARN,
        CaveatCode.COHORTS_OVERLAP: Severity.WARN,
        CaveatCode.SMALL_N: Severity.WARN,
        CaveatCode.PH_VIOLATED: Severity.WARN,
        CaveatCode.DRAFT_RELEASE: Severity.WARN,
        CaveatCode.SUPPRESSED: Severity.INFO,
        CaveatCode.NOT_ESTIMABLE: Severity.INFO,
        CaveatCode.LIFT_DIFFERS: Severity.INFO,
        CaveatCode.POOLED_ACROSS_DATASETS: Severity.INFO,
    }
)
"""The severity every core code declares (SPEC §8.3). Packs declare theirs in the pack API."""


def _caveat_schema(schema: dict[str, Any]) -> None:
    """JSON Schema: a core code carries its declared severity, and an unprefixed code is one."""
    schema["properties"]["code"]["anyOf"] = [
        {"pattern": PACK_CODE_RE.pattern},
        {"enum": [code.value for code in CaveatCode]},
    ]
    schema["allOf"] = [
        {
            "if": {"properties": {"code": {"const": code.value}}},
            "then": {"properties": {"severity": {"const": severity.value}}},
        }
        for code, severity in CORE_SEVERITIES.items()
    ]


def _unicode(value: str) -> str:
    if not is_text(value):
        raise problem("invalid_text", "Text must be Unicode: no lone surrogates or noncharacters")
    return value


class Caveat(Output):
    """``affects`` holds JSON Pointers relative to the output's root, sorted and de-duplicated."""

    model_config = ConfigDict(json_schema_extra=_caveat_schema)

    code: AnyCode
    severity: Annotated[Severity, LAX]
    message: list[Segment]
    affects: list[
        Annotated[
            JsonPointer,
            Field(max_length=MAX_POINTER, json_schema_extra=DATA_MARK),
            AfterValidator(_unicode),
        ]
    ]
    """Its tokens can be keys from data, so it is data (A6)."""

    @field_validator("affects")
    @classmethod
    def _sort_affects(cls, affects: list[str]) -> list[str]:
        return sorted(set(affects), key=utf16_key)

    @model_validator(mode="after")
    def _check_code(self) -> Self:
        if "." in self.code:
            return self
        if self.code not in CaveatCode:
            raise problem(
                "caveat_code",
                "Not a core caveat code, and pack codes are namespaced: ",
                shown=self.code,
            )
        declared = CORE_SEVERITIES[CaveatCode(self.code)]
        if self.severity != declared:
            raise problem(
                "caveat_severity",
                "{code} has severity {severity}",
                code=self.code,
                severity=declared.value,
            )
        return self

    def sort_key(
        self,
    ) -> tuple[bytes, tuple[bytes, ...], str, tuple[tuple[bool, bytes, bool], ...]]:
        """Caveats are sorted by code, affected paths, severity and message, each segment by
        kind, text and truncation, strings compared as UTF-16 (SPEC §9.3): a total order."""
        message = tuple(
            (True, utf16_key(segment.text), False)
            if isinstance(segment, TextSegment)
            else (False, utf16_key(segment.data), bool(segment.truncated))
            for segment in self.message
        )
        return (
            utf16_key(self.code),
            tuple(utf16_key(path) for path in self.affects),
            str(self.severity),
            message,
        )


def sort_caveats(caveats: list[Caveat]) -> list[Caveat]:
    """Caveats in output order, exact duplicates removed."""
    unique = {caveat.model_dump_json(): caveat for caveat in caveats}
    return sorted(unique.values(), key=Caveat.sort_key)


__all__ = [
    "CORE_SEVERITIES",
    "Caveat",
    "CaveatCode",
    "Severity",
    "sort_caveats",
]
