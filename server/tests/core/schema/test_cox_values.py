"""``survival.cox``'s values refuse the shapes D365 rules out (SPEC §8.2; D365)."""

from typing import Any

import pytest
from pydantic import ValidationError

from aibi.core.schema.analyses import CoxTerm, ModelTest

INTERVAL = {"method": "wald", "level": 0.95, "low": 1.0, "high": 2.0}
NONE = {"method": "wald", "level": 0.95, "low": None, "high": None}
MISSING = {
    "/estimate": "separation",
    "/ci/low": "separation",
    "/ci/high": "separation",
    "/p": "separation",
}


def term(**given: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "kind": "covariate",
        "covariate": 0,
        "estimate": 1.5,
        "ci": INTERVAL,
        "p": 0.2,
    }
    found.update(given)
    return found


@pytest.mark.parametrize(
    "given",
    [
        {"kind": "cohort", "covariate": None},
        {"kind": "cohort", "position": 1, "covariate": 0},
        {
            "kind": "cohort",
            "position": 1,
            "covariate": None,
            "level": {"data": "a"},
            "baseline": {"data": "b"},
        },
        {"position": 1},
        {"covariate": None},
        {"level": {"data": "a"}},
        {"baseline": {"data": "b"}},
        {"direction": "infinity"},
        {
            "estimate": None,
            "ci": NONE,
            "p": None,
            "direction": "zero",
            "not_estimable": dict.fromkeys(MISSING, "zero_variance"),
        },
    ],
)
def test_a_term_refuses_a_combination_d365_rules_out(given: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CoxTerm.model_validate(term(**given))


def test_a_term_takes_the_combinations_d365_allows() -> None:
    CoxTerm.model_validate(term())
    CoxTerm.model_validate(term(level={"data": "a"}, baseline={"data": "b"}))
    CoxTerm.model_validate(term(kind="cohort", position=1, covariate=None))
    CoxTerm.model_validate(
        term(estimate=None, ci=NONE, p=None, direction="zero", not_estimable=MISSING)
    )


@pytest.mark.parametrize(
    "given",
    [
        {"terms": [1, 0], "df": 2, "statistic": 1.0, "p": 0.5},
        {"terms": [0, 0], "df": 2, "statistic": 1.0, "p": 0.5},
        {"terms": [], "df": None, "statistic": 1.0, "p": 0.5},
        {"terms": [0, 1], "df": 3, "statistic": 1.0, "p": 0.5},
        {
            "terms": [0],
            "df": None,
            "statistic": None,
            "p": None,
            "not_estimable": {"/statistic": "zero_variance", "/p": "zero_variance"},
        },
    ],
)
def test_a_model_test_refuses_terms_that_are_not_what_it_tested(given: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ModelTest.model_validate({"method": "grambsch_therneau", **given})


def test_a_model_test_names_its_terms_and_its_degrees_of_freedom_count_them() -> None:
    ModelTest.model_validate(
        {"method": "grambsch_therneau", "terms": [0, 2], "df": 2, "statistic": 1.0, "p": 0.5}
    )
