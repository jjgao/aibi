"""The consequences of §6.5 as the spec states them, on its own biomedical fixture.

The core is domain-neutral (SPEC P8): this fixture is data, exercised beside the neutral one of
``test_scenarios.py``. Patients have samples, of type primary, metastasis or blood normal; a
sample is sequenced on a gene panel, and so assessed for the panel's genes (grouped coverage,
scoped by gene, with whole-exome panels covering every gene); mutations and copy-number calls
are recorded for tumour samples only (a parent scope: a blood normal has none). Participants
enrol in trials, whose phase each enrolment looks up, and have adverse events with grades.
"""

from collections.abc import Callable
from typing import Any

import pytest

from aibi.core.engine import build
from aibi.core.engine.data import Release

Doc = Callable[..., dict[str, Any]]
Runner = Callable[..., Any]

TUMOUR = {"kind": "value", "column": "samples.sample_type", "values": ["primary", "metastasis"]}
PANELS = {
    "assignment": {
        "table": "sample_panels",
        "parent_columns": {"sample_id": "sample_id"},
        "group_column": "panel",
    },
    "groups": {
        "table": "panel_genes",
        "group_column": "panel",
        "scope_columns": {"gene": "gene"},
        "covers_all_column": "whole_exome",
    },
}


def _descriptors() -> list[Any]:
    table, column, relationship, coverage = (
        build.table,
        build.column,
        build.relationship,
        build.coverage,
    )
    genes = {"values": [{"value": gene} for gene in ("TP53", "EGFR", "KRAS")]}
    return [
        build.dataset(),
        table("patients", ["patient_id"]),
        column("patients.patient_id", "string"),
        table("samples", ["sample_id"]),
        column("samples.sample_id", "string"),
        column("samples.patient_id", "string"),
        column(
            "samples.sample_type",
            "category",
            permissible_values={
                "values": [{"value": v} for v in ("primary", "metastasis", "blood_normal")]
            },
        ),
        relationship("samples", ["patient_id"], "patients", role="patient"),
        coverage("rel:samples.patient", "all"),
        table("mutations", ["mutation_id"], role="event"),
        column("mutations.mutation_id", "string"),
        column("mutations.sample_id", "string"),
        column("mutations.gene", "category", permissible_values=genes),
        relationship("mutations", ["sample_id"], "samples", role="sample"),
        coverage("rel:mutations.sample", PANELS, parent_scope=TUMOUR),
        table("copy_number", ["call_id"], role="event"),
        column("copy_number.call_id", "string"),
        column("copy_number.sample_id", "string"),
        column("copy_number.gene", "category", permissible_values=genes),
        column(
            "copy_number.alteration",
            "category",
            permissible_values={"values": [{"value": "amplification"}, {"value": "deletion"}]},
        ),
        relationship("copy_number", ["sample_id"], "samples", role="sample"),
        coverage("rel:copy_number.sample", PANELS, parent_scope=TUMOUR),
        table("sample_panels", ["sample_id"], role="coverage"),
        column("sample_panels.sample_id", "string"),
        column("sample_panels.panel", "string"),
        table("panel_genes", ["panel", "gene"], role="coverage"),
        column("panel_genes.panel", "string"),
        column("panel_genes.gene", "string"),
        column("panel_genes.whole_exome", "boolean"),
        table("participants", ["participant_id"]),
        column("participants.participant_id", "string"),
        table("trials", ["trial_id"]),
        column("trials.trial_id", "string"),
        column("trials.phase", "integer", units="1"),
        table("enrolments", ["enrolment_id"], role="link"),
        column("enrolments.enrolment_id", "string"),
        column("enrolments.participant_id", "string"),
        column("enrolments.trial_id", "string"),
        relationship("enrolments", ["participant_id"], "participants", role="participant"),
        relationship("enrolments", ["trial_id"], "trials", role="trial"),
        coverage("rel:enrolments.participant", "all"),
        table("adverse_events", ["event_id"], role="event"),
        column("adverse_events.event_id", "string"),
        column("adverse_events.participant_id", "string"),
        column("adverse_events.grade", "integer", units="1"),
        relationship("adverse_events", ["participant_id"], "participants", role="participant"),
        coverage("rel:adverse_events.participant", "all"),
    ]


class Cohort:
    """Patients, their samples (a type, a panel or none) and the genes found mutated."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {
            "patients": [],
            "samples": [],
            "mutations": [],
            "copy_number": [],
            "sample_panels": [],
            "panel_genes": [
                {"panel": "exome", "gene": "TP53", "whole_exome": True},
                {"panel": "small", "gene": "EGFR", "whole_exome": False},
            ],
        }

    def patient(self, *samples: dict[str, Any]) -> None:
        name = f"p{len(self.rows['patients'])}"
        self.rows["patients"].append({"patient_id": name})
        for given in samples:
            sample = f"s{len(self.rows['samples'])}"
            row: dict[str, Any] = {"sample_id": sample, "patient_id": name}
            if "type" in given:
                row["sample_type"] = given["type"]
            self.rows["samples"].append(row)
            if given.get("panel"):
                self.rows["sample_panels"].append({"sample_id": sample, "panel": given["panel"]})
            for gene in given.get("mutated", ()):
                self.rows["mutations"].append(
                    {
                        "mutation_id": f"m{len(self.rows['mutations'])}",
                        "sample_id": sample,
                        "gene": gene,
                    }
                )
            for gene in given.get("amplified", ()):
                self.rows["copy_number"].append(
                    {
                        "call_id": f"c{len(self.rows['copy_number'])}",
                        "sample_id": sample,
                        "gene": gene,
                        "alteration": "amplification",
                    }
                )

    def release(self) -> Release:
        return build.release(_descriptors(), self.rows)


def wild_type(sample_type: str | None = "primary") -> dict[str, Any]:
    """A tumour sample sequenced on the whole exome, with no mutation."""
    given: dict[str, Any] = {"panel": "exome"}
    if sample_type is not None:
        given["type"] = sample_type
    return given


def unassessed(sample_type: str = "primary") -> dict[str, Any]:
    return {"type": sample_type}


def blood_normal() -> dict[str, Any]:
    return {"type": "blood_normal", "panel": "exome"}


def value(column: str, **predicate: Any) -> dict[str, Any]:
    return {"kind": "value", "column": column, **predicate}


TP53 = value("mutations.gene", values=["TP53"])


def exists(table: str, *where: Any, **members: Any) -> dict[str, Any]:
    return {"kind": "exists", "table": table, "where": list(where), **members}


def ask(run: Runner, doc: Doc, release: Release, clause: Any, unit: str = "patients") -> list[str]:
    result = run(doc([clause], unit=unit), release)
    assert result.refusals == []
    return [
        item.value.value
        if not item.is_unknown
        else "UNKNOWN(" + ",".join(sorted(reason.value for reason in item.reasons)) + ")"
        for item in result.result.values
    ]


def test_not_a_tp53_mutation_is_true_only_where_tp53_was_assessed(run: Runner, doc: Doc) -> None:
    cohort = Cohort()
    cohort.patient(
        wild_type(),
        {"type": "primary", "panel": "small"},
        unassessed(),
        {"type": "primary", "panel": "exome", "mutated": ["TP53"]},
        blood_normal(),
    )
    no_tp53 = {"not": exists("mutations", TP53)}
    assert ask(run, doc, cohort.release(), no_tp53, unit="samples") == [
        "TRUE",
        "UNKNOWN(NOT_COVERED)",  # the small panel does not cover TP53
        "UNKNOWN(NOT_COVERED)",
        "FALSE",
        "UNKNOWN(OUT_OF_SCOPE)",
    ]
    either = exists("mutations", {"any": [TP53, value("mutations.gene", values=["EGFR"])]})
    refused = run(doc([either], unit="samples"), cohort.release())
    assert {code for code, _ in refused.refusals} == {"SCOPE_COLUMN_MENTION"}
    listed = exists("mutations", value("mutations.gene", values=["TP53", "EGFR"]))
    assert run(doc([listed], unit="samples"), cohort.release()).refusals == []


def test_a_missing_grade_leaves_a_question_and_its_negation_unknown(run: Runner, doc: Doc) -> None:
    release = build.release(
        _descriptors(),
        {
            "participants": [{"participant_id": "q0"}],
            "adverse_events": [{"event_id": "a0", "participant_id": "q0"}],
        },
    )
    severe = exists("adverse_events", value("adverse_events.grade", range={"gte": 3}))
    unit = "participants"
    assert ask(run, doc, release, severe, unit=unit) == ["UNKNOWN(NO_INFORMATION)"]
    assert ask(run, doc, release, {"not": severe}, unit=unit) == ["UNKNOWN(NO_INFORMATION)"]


FORMS = [
    TP53,
    exists("mutations", TP53),
    exists("samples", exists("mutations", TP53)),
]


@pytest.mark.parametrize("form", FORMS, ids=["direct", "two-step", "nested"])
def test_an_unassessed_tumour_sample_leaves_strict_unknown_and_assessed_false(
    run: Runner, doc: Doc, form: dict[str, Any]
) -> None:
    cohort = Cohort()
    cohort.patient(wild_type(), unassessed())
    cohort.patient(wild_type(), unassessed(), blood_normal())
    cohort.patient()
    cohort.patient(blood_normal())
    release = cohort.release()
    assert ask(run, doc, release, form) == ["UNKNOWN(NOT_COVERED)"] * 4
    assert ask(run, doc, release, {**form, "lift": "assessed"}) == [
        "FALSE",
        "FALSE",  # the blood normal changes nothing
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
    ]
    trees = [
        run(doc([other], unit="patients"), release).resolution.cohorts["c"].tree for other in FORMS
    ]
    assert trees[0] == trees[1] == trees[2]


@pytest.mark.parametrize("lift", ["strict", "assessed"])
def test_a_primary_sample_counting_only_assessed_samples(run: Runner, doc: Doc, lift: str) -> None:
    cohort = Cohort()
    cohort.patient(unassessed(), wild_type("metastasis"))  # primary samples all unassessed
    cohort.patient(wild_type("metastasis"))  # no primary sample
    cohort.patient(wild_type(None))  # the only assessed wild-type sample has no type
    question = exists(
        "samples",
        value("samples.sample_type", values=["primary"]),
        exists("mutations", TP53),
        lift=lift,
    )
    assert ask(run, doc, cohort.release(), question) == [
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED)",
        "UNKNOWN(NOT_COVERED,NO_INFORMATION)",
    ]


def test_a_tp53_mutation_and_an_egfr_amplification_in_one_sample(run: Runner, doc: Doc) -> None:
    cohort = Cohort()
    cohort.patient()
    cohort.patient(
        {"type": "primary", "panel": "exome", "mutated": ["TP53"], "amplified": ["EGFR"]}
    )
    cohort.patient(
        {"type": "primary", "panel": "exome", "mutated": ["TP53"], "amplified": ["EGFR"]},
        blood_normal(),
    )
    both = exists(
        "samples",
        exists("mutations", TP53),
        exists(
            "copy_number",
            value("copy_number.gene", values=["EGFR"]),
            value("copy_number.alteration", values=["amplification"]),
        ),
    )
    assert ask(run, doc, cohort.release(), both) == [
        "UNKNOWN(NOT_COVERED)",
        "TRUE",
        "TRUE",  # the blood normal changes nothing
    ]


def test_no_enrolments_is_false_for_a_phase_3_trial(run: Runner, doc: Doc) -> None:
    release = build.release(
        _descriptors(),
        {
            "participants": [{"participant_id": "q0"}, {"participant_id": "q1"}],
            "trials": [{"trial_id": "t0", "phase": 3}],
            "enrolments": [{"enrolment_id": "n0", "participant_id": "q0", "trial_id": "t0"}],
        },
    )
    phase_three = value("trials.phase", values=[3])
    assert ask(run, doc, release, phase_three, unit="participants") == ["TRUE", "FALSE"]
