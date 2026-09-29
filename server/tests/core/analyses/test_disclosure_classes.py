"""DisclosureClass classes: one rule for what the pass cannot protect (SPEC §8.4, §9.4; D353).

Every analysis the registry holds, the core's and a test pack's, is checked under every kind of
disclosure setting: phase 2 refuses exactly the ``refused`` ones under a *k* and, without row ids,
the ones that list or hand units' values in the keys' order, with one code each, naming the
setting and no value, before any pack code runs; applicability says ``unavailable`` for exactly
the same analyses and settings."""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

import aibi
from aibi.core.analyses import registry
from aibi.core.analyses.registry import CORE, DISCLOSED, REFUSED, Analyses, DisclosureClass
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.pack_api import (
    AnalysisInputs,
    Pack,
    PackManifest,
    PackRegistry,
    ReleaseView,
)
from aibi.core.schema.refusals import RefusalCode

Check = Callable[..., Any]
Shop = Callable[..., Any]

OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 50}}
YOUNG = {"kind": "value", "column": "customers.age", "range": {"lt": 50}}
TIER = {"column": "customers.tier"}
GOLD = {"kind": "value", "column": "customers.tier", "values": ["gold"]}
PACKED = "tallies.echo"

VIEWS: Mapping[str, dict[str, Any]] = {
    "compare.existence": {"cohorts": ["old", "young"], "params": {"predicates": [GOLD]}},
    "summary.distribution": {"cohorts": ["old", "young"], "params": {"columns": [TIER]}},
    "compare.columns": {"cohorts": ["old", "young"], "params": {"columns": [TIER]}},
    "summary.members": {"cohorts": ["old"], "params": {}},
    "survival.km": {"cohorts": ["old", "young"], "params": {}},
    "survival.cox": {"cohorts": ["old", "young"], "params": {"covariates": [TIER]}},
    PACKED: {"cohorts": ["old"], "params": {"columns": {"measure": [TIER]}}},
}
"""A view of each analysis the registry holds, over the shop."""

SETTINGS: Mapping[str, tuple[dict[str, Any] | None, int | None, int | None]] = {
    "none": (None, None, None),
    "the dataset's": ({"min_cell_count": 3}, None, None),
    "the floor": (None, 4, None),
    "both": ({"min_cell_count": 3}, 5, None),
    "a draft's published": (None, None, 5),
    "no row ids": ({"min_cell_count": 3, "allow_row_ids": False}, None, None),
}
"""Each disclosure setting: the dataset's ``disclosure``, the deployment's floor and a draft's
latest published release's ``min_cell_count``."""

SOURCES = {
    "the dataset's": "the dataset's min_cell_count, 3",
    "the floor": "the deployment's floor, 4",
    "both": "the deployment's floor, 5",
    "a draft's published": "the latest published release's min_cell_count, 5",
    "no row ids": "the dataset's min_cell_count, 3",
}
"""What each setting's refusal says binds."""


class Echo:
    """A pack's analysis that counts what it is handed and, through its requirement predicate,
    what phase 2 asks of the release."""

    def __init__(self) -> None:
        self.handed: list[AnalysisInputs] = []
        self.asked: list[ReleaseView] = []

    @property
    def entry(self) -> AnalysisDescriptor:
        return AnalysisDescriptor.model_validate(
            {
                "kind": "analysis",
                "id": PACKED,
                "version": "1.0.0",
                "label": "Echo",
                "fields": {
                    "requires": [
                        {"role": "cohorts", "min": 1, "max": 2},
                        {"role": "measure", "kind": "column", "min": 1, "max": 1},
                        {"role": "stock", "predicate": "tallies.stocked"},
                    ],
                    "params": {"type": "object"},
                    "returns": {"type": "object"},
                    "methods": {},
                    "assumptions": [],
                    "uses_reference": False,
                    "assumes_independent_groups": False,
                    "cross_dataset": None,
                    "caveats": [],
                },
            }
        )

    def run(self, inputs: AnalysisInputs) -> Mapping[str, Any]:
        self.handed.append(inputs)
        return {}

    def stocked(self, release: ReleaseView) -> bool:
        self.asked.append(release)
        return True


def tallies(echo: Echo) -> Analyses:
    pack = Pack(
        manifest=PackManifest(
            id="tallies", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        analyses=[echo],
        requirement_predicates={"stocked": echo.stocked},
    )
    return Analyses(PackRegistry([pack], core_version=aibi.__version__))


def document(analysis: str) -> dict[str, Any]:
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"old": {"all": [OLD]}, "young": {"all": [YOUNG]}},
        "views": [{"analysis": analysis, **VIEWS[analysis]}],
    }


def expected(analysis: str, setting: str) -> RefusalCode | None:
    """What phase 2 refuses a view of ``analysis`` for disclosure under ``setting`` (D353)."""
    disclosure, floor, published = SETTINGS[setting]
    k = max(
        (x for x in (floor, published, (disclosure or {}).get("min_cell_count")) if x),
        default=None,
    )
    rowless = (disclosure or {}).get("allow_row_ids") is False
    listing = analysis in ("summary.members", PACKED)
    if listing and rowless:
        return RefusalCode.ROW_IDS_NOT_ALLOWED
    if k is not None and analysis not in DISCLOSED:
        return RefusalCode.WITHHELD_UNDER_K
    return None


def test_every_core_analysis_states_its_class_and_a_refused_one_says_why() -> None:
    assert set(CORE) == DISCLOSED | REFUSED
    assert {"compare.existence", "summary.distribution", "compare.columns"} == DISCLOSED
    assert {"summary.members", "survival.km", "survival.cox"} == REFUSED
    for analysis in CORE.values():
        assert (analysis.disclosure is DisclosureClass.REFUSED) == bool(analysis.because)
    with pytest.raises(ValueError, match="says why"):
        registry.CoreAnalysis(
            CORE["survival.km"].entry,
            CORE["survival.km"].params,
            CORE["survival.km"].values,
            DisclosureClass.REFUSED,
        )
    with pytest.raises(ValueError, match="says why"):
        registry.CoreAnalysis(
            CORE["compare.existence"].entry,
            CORE["compare.existence"].params,
            CORE["compare.existence"].values,
            DisclosureClass.DISCLOSED,
            because="no",
        )
    with pytest.raises(ValueError, match="says why"):
        registry.CoreAnalysis(
            CORE["survival.km"].entry,
            CORE["survival.km"].params,
            CORE["survival.km"].values,
            DisclosureClass.REFUSED,
            because="",
        )
    with pytest.raises(ValueError, match="lists keys"):
        registry.CoreAnalysis(
            CORE["compare.existence"].entry,
            CORE["compare.existence"].params,
            CORE["compare.existence"].values,
            DisclosureClass.DISCLOSED,
            lists_keys=True,
        )


def test_a_pack_s_analysis_is_refused_and_hands_its_members_values_whatever_it_says() -> None:
    [packed] = [a for a in tallies(Echo()).all() if a.pack is not None]
    disclosure, lists_keys, because = registry.disclosure_of(packed)
    assert (disclosure, lists_keys) == (DisclosureClass.REFUSED, True)
    assert because == registry.PACK_BECAUSE
    core = Analyses().get("compare.existence")
    assert core is not None
    assert registry.disclosure_of(core)[0] is DisclosureClass.DISCLOSED
    assert registry.withheld(packed, 2)
    assert not registry.withheld(packed, None)
    assert not registry.withheld(core, 2)


@pytest.mark.parametrize("setting", list(SETTINGS))
@pytest.mark.parametrize("analysis", list(VIEWS))
def test_phase_2_refuses_exactly_the_refused_analyses_under_k_and_the_listing_ones_without_row_ids(
    check: Check, shop: Shop, analysis: str, setting: str
) -> None:
    disclosure, floor, published = SETTINGS[setting]
    echo = Echo()
    release = shop(survived="delayed", disclosure=disclosure)
    found = check(
        document(analysis), release, floor=floor, published=published, analyses=tallies(echo)
    )
    code = expected(analysis, setting)
    at = [r for r in found.refusals if r.path == "/views/0/analysis"]
    if code is None:
        assert at == []
        assert [view.analysis.id for view in found.views] == [analysis]
        if analysis == PACKED:
            assert len(echo.asked) == 1
        return
    [refusal] = at
    assert found.refusals == [refusal]
    assert found.views == []
    assert refusal.code == code
    assert echo.handed == []
    assert echo.asked == []
    assert [part.model_dump()["data"] for part in refusal.alternatives] == [
        "count_cohort",
        "compare.columns",
        "compare.existence",
        "summary.distribution",
    ]
    shown = [part.model_dump() for part in refusal.message]
    named = [part["data"] for part in shown if "data" in part]
    said = "".join(part.get("text", "") for part in shown)
    if code is RefusalCode.WITHHELD_UNDER_K:
        assert named == [analysis]
        assert f"({SOURCES[setting]})" in said
        assert "D353" in said
        [registered] = [a for a in tallies(Echo()).all() if a.id == analysis]
        because = registry.disclosure_of(registered)[2]
        assert because
        assert because in said
    else:
        assert named == [analysis, "d"]
    dumped = json.dumps(refusal.model_dump(mode="json"))
    for value in ('"c1"', '"gold"', '"silver"'):
        assert value not in dumped


def _statuses(
    release: Any, analyses: Analyses, k: int | None
) -> dict[str, tuple[str, Sequence[str]]]:
    found = analyses.applicable(
        list(release.descriptors),
        dataset=release.dataset,
        manifest=release.manifest,
        unit="customers",
        k=k,
    )
    return {item.analysis: (item.status, item.missing) for item in found}


@pytest.mark.parametrize("setting", list(SETTINGS))
def test_applicability_says_unavailable_exactly_where_phase_2_refuses_for_disclosure(
    shop: Shop, setting: str
) -> None:
    disclosure, floor, published = SETTINGS[setting]
    release = shop(survived="delayed", disclosure=disclosure)
    k = max(
        (x for x in (floor, published, (disclosure or {}).get("min_cell_count")) if x),
        default=None,
    )
    found = _statuses(release, tallies(Echo()), k)
    for analysis in VIEWS:
        status, missing = found[analysis]
        code = expected(analysis, setting)
        withheld = [name for name in missing if name in ("min_cell_count", "allow_row_ids")]
        if code is None:
            assert withheld == [], analysis
            assert status != "unavailable", analysis
            continue
        assert status == "unavailable", analysis
        wanted = ["min_cell_count"] if k is not None and analysis not in DISCLOSED else []
        if code is RefusalCode.ROW_IDS_NOT_ALLOWED or (
            analysis in ("summary.members", PACKED) and "row ids" in setting
        ):
            wanted.append("allow_row_ids")
        assert sorted(withheld) == sorted(wanted), analysis


@pytest.mark.parametrize("k", [None, 3])
def test_under_k_applicability_still_names_every_requirement_a_release_does_not_meet(
    shop: Shop, k: int | None
) -> None:
    release = shop()
    found = _statuses(release, Analyses(), k)
    status, missing = found["survival.km"]
    assert status == "unavailable"
    assert sorted(missing) == sorted(["endpoint", *([] if k is None else ["min_cell_count"])])


ROWS = {"column": "orders.channel", "count": "rows"}
"""A form ``summary.distribution`` withholds under any setting (D379)."""


def test_only_a_disclosed_analysis_withholds_a_form_and_says_why() -> None:
    for analysis in CORE.values():
        if analysis.withheld_forms:
            assert analysis.disclosure is DisclosureClass.DISCLOSED
            assert all(why.strip() for why in analysis.withheld_forms.values())
    assert set(CORE["summary.distribution"].withheld_forms) == {
        "columns/*/count",
        "columns/*/each",
    }
    distribution = CORE["summary.distribution"]
    for disclosure, forms, because in (
        (DisclosureClass.REFUSED, {"columns/*/count": "why"}, "no"),
        (DisclosureClass.DISCLOSED, {"columns/*/count": " "}, None),
        (DisclosureClass.DISCLOSED, {"columns//count": "why"}, None),
        (DisclosureClass.DISCLOSED, {"columns/*/cuont": "why"}, None),
        (DisclosureClass.DISCLOSED, {"colums/*/count": "why"}, None),
        (DisclosureClass.DISCLOSED, {"columns/count": "why"}, None),
        (DisclosureClass.DISCLOSED, {"columns/*/column/*": "why"}, None),
        (DisclosureClass.DISCLOSED, {"columns/*/count/rows": "why"}, None),
    ):
        with pytest.raises(ValueError, match="withh"):
            registry.CoreAnalysis(
                distribution.entry,
                distribution.params,
                distribution.values,
                disclosure,
                because=because,
                withheld_forms=forms,
            )


def _withholding(form: str) -> registry.CoreAnalysis:
    distribution = CORE["summary.distribution"]
    return registry.CoreAnalysis(
        distribution.entry,
        distribution.params,
        distribution.values,
        DisclosureClass.DISCLOSED,
        withheld_forms={form: "why"},
    )


def test_a_withheld_form_names_no_member_through_a_union_of_models() -> None:
    """D379: a clause is one of several models, so a path below it names no member of a model
    the form lies below, even one that some of them have."""
    with pytest.raises(ValueError, match="withheld form names a member"):
        _withholding("columns/*/where/*/column")


def test_a_withheld_form_lies_below_an_optional_list_through_its_items() -> None:
    for form in ("columns/*/where", "columns/*/where/*", "columns/*/values/*"):
        assert set(_withholding(form).withheld_forms) == {form}


def test_a_withheld_form_is_found_where_the_parameters_give_it_under_k_alone() -> None:
    [summary] = [a for a in Analyses().all() if a.id == "summary.distribution"]
    given = CORE["summary.distribution"].params.model_validate({"columns": [TIER, ROWS, ROWS]})
    because = CORE["summary.distribution"].withheld_forms["columns/*/count"]
    assert registry.withheld_form(summary, given, 3) == (("columns", 1, "count"), because)
    assert registry.withheld_form(summary, given, None) is None
    plain = CORE["summary.distribution"].params.model_validate({"columns": [TIER]})
    assert registry.withheld_form(summary, plain, 3) is None
    [packed] = [a for a in tallies(Echo()).all() if a.pack is not None]
    assert registry.withheld_form(packed, given, 3) is None


def test_the_first_withheld_member_is_the_first_the_parameters_give_whatever_the_forms_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D379: the first member that a form withholds, in the parameters' order (a model's members
    as it declares them, a list's items in turn), not the order the forms are declared."""
    distribution = CORE["summary.distribution"]
    forms = {"columns/*/count": "rows", "columns/*/bins": "edges"}
    two = registry.CoreAnalysis(
        distribution.entry,
        distribution.params,
        distribution.values,
        DisclosureClass.DISCLOSED,
        withheld_forms=forms,
    )
    monkeypatch.setattr(registry, "CORE", {**CORE, "summary.distribution": two})
    [summary] = [a for a in Analyses().all() if a.id == "summary.distribution"]
    binned = {"column": "orders.amount", "bins": [0, 10]}
    for columns, expected in (
        ([TIER, binned, ROWS], (("columns", 1, "bins"), "edges")),
        ([TIER, ROWS, binned], (("columns", 1, "count"), "rows")),
        ([{**binned, "count": "rows"}], (("columns", 0, "bins"), "edges")),
        ([TIER], None),
    ):
        given = distribution.params.model_validate({"columns": columns})
        assert registry.withheld_form(summary, given, 3) == expected


@pytest.mark.parametrize("setting", list(SETTINGS))
def test_phase_2_withholds_a_disclosed_analysis_s_form_exactly_under_k_and_it_stays_available(
    check: Check, shop: Shop, setting: str
) -> None:
    disclosure, floor, published = SETTINGS[setting]
    release = shop(survived="delayed", disclosure=disclosure)
    written = document("summary.distribution")
    written["views"][0]["params"] = {"columns": [TIER, ROWS]}
    found = check(written, release, floor=floor, published=published)
    k = max(
        (x for x in (floor, published, (disclosure or {}).get("min_cell_count")) if x),
        default=None,
    )
    status, missing = _statuses(release, Analyses(), k)["summary.distribution"]
    assert status != "unavailable"
    assert "min_cell_count" not in missing
    if k is None:
        assert found.refusals == []
        assert [view.analysis.id for view in found.views] == ["summary.distribution"]
        return
    [refusal] = found.refusals
    assert found.views == []
    assert (refusal.code, refusal.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/1/count",
    )
    shown = [part.model_dump() for part in refusal.message]
    said = "".join(part.get("text", "") for part in shown)
    assert [part["data"] for part in shown if "data" in part] == ["summary.distribution"]
    assert f"({SOURCES[setting]})" in said
    assert CORE["summary.distribution"].withheld_forms["columns/*/count"] in said
    dumped = json.dumps(refusal.model_dump(mode="json"))
    for value in ('"c1"', '"gold"', '"web"', '"orders.channel"'):
        assert value not in dumped


EACH = {"column": "orders.channel", "each": "category"}
"""Memberships, which ``summary.distribution`` withholds under any setting (D382)."""


@pytest.mark.parametrize("setting", list(SETTINGS))
def test_phase_2_withholds_memberships_exactly_under_k_offering_the_questions_of_one_category(
    check: Check, shop: Shop, setting: str
) -> None:
    """D382: under every source of *k*, memberships are ``WITHHELD_UNDER_K`` at their ``each``,
    the first withheld member in the parameters' order, naming the setting and why and no
    value, offering ``some`` and ``every`` with ``values``; the analysis stays available."""
    disclosure, floor, published = SETTINGS[setting]
    release = shop(survived="delayed", disclosure=disclosure)
    written = document("summary.distribution")
    written["views"][0]["params"] = {"columns": [TIER, EACH, ROWS]}
    found = check(written, release, floor=floor, published=published)
    k = max(
        (x for x in (floor, published, (disclosure or {}).get("min_cell_count")) if x),
        default=None,
    )
    status, missing = _statuses(release, Analyses(), k)["summary.distribution"]
    assert status != "unavailable"
    assert "min_cell_count" not in missing
    if k is None:
        assert found.refusals == []
        [view] = found.views
        assert view.variables[1].resolved.kind == "memberships"
        return
    [refusal] = found.refusals
    assert found.views == []
    assert (refusal.code, refusal.path) == (
        RefusalCode.WITHHELD_UNDER_K,
        "/views/0/params/columns/1/each",
    )
    shown = [part.model_dump() for part in refusal.message]
    said = "".join(part.get("text", "") for part in shown)
    assert [part["data"] for part in shown if "data" in part] == ["summary.distribution"]
    assert f"({SOURCES[setting]})" in said
    assert CORE["summary.distribution"].withheld_forms["columns/*/each"] in said
    assert [one.text for one in refusal.alternatives or []] == [
        'aggregate: "some" with values',
        'aggregate: "every" with values',
    ]
    dumped = json.dumps(refusal.model_dump(mode="json"))
    for value in ('"c1"', '"gold"', '"web"', '"orders.channel"'):
        assert value not in dumped
    [summary] = [a for a in Analyses().all() if a.id == "summary.distribution"]
    given = CORE["summary.distribution"].params.model_validate({"columns": [TIER, EACH, ROWS]})
    assert registry.withheld_form(summary, given, k) == (
        ("columns", 1, "each"),
        CORE["summary.distribution"].withheld_forms["columns/*/each"],
    )
