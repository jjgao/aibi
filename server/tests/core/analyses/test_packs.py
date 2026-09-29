"""A pack's analysis run on the inputs its ``requires`` name, its phase 2 and its refusals, and
pack leaves in a variable's ``where`` (SPEC §7.3, §8.4, §9.1, §10.1; D341–D345), with a test-only
pack, ``tallies``, over the shop, run by the reference evaluator."""

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

import pytest
from pydantic import JsonValue

import aibi
from aibi.core.analyses import packs, views
from aibi.core.analyses import registry as registry_module
from aibi.core.analyses.existence import CohortAt
from aibi.core.analyses.registry import Analyses
from aibi.core.engine import build
from aibi.core.engine.evaluate import evaluate
from aibi.core.engine.inputs import Listed, listed, ordered
from aibi.core.engine.resolve import identifying
from aibi.core.engine.worker import CallerDeadline
from aibi.core.schema.caveats import CaveatCode
from aibi.core.schema.descriptors import AnalysisDescriptor
from aibi.core.schema.document import Clause, PackLeaf
from aibi.core.schema.jsonio import canonical
from aibi.core.schema.jsonschemas import UNEVALUABLE, Checker, Failure, OutOfTime, TimedBudget
from aibi.core.schema.limits import MAX_INPUT_CELLS, MAX_LISTED, MAX_RESULT_VALUES
from aibi.core.schema.loading import load_document
from aibi.core.schema.output import Segment, text
from aibi.core.schema.pack_api import (
    AnalysisInputs,
    JsonTooLarge,
    Pack,
    PackError,
    PackManifest,
    PackRegistry,
    ReleaseView,
    plain_json,
)
from aibi.core.schema.refusals import RefusalCode
from aibi.core.schema.semantics import ExclusionReason

Analyse = Callable[..., list[Any]]
Check = Callable[..., Any]
Shop = Callable[..., Any]
Rows = Callable[..., dict[str, list[dict[str, object]]]]

OLD = {"kind": "value", "column": "customers.age", "range": {"gte": 50}}
YOUNG = {"kind": "value", "column": "customers.age", "range": {"lt": 50}}
TIER = {"column": "customers.tier"}
AGE = {"column": "customers.age"}
ORDERS = {"column": "orders.order_id", "aggregate": "count"}
REQUIRES: list[dict[str, Any]] = [
    {"role": "cohorts", "min": 1, "max": 3},
    {"role": "measure", "kind": "column", "min": 1, "max": 2},
    {"role": "extra", "kind": "column", "min": 0},
]
OPTIONS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"scale": {"type": "number", "minimum": 0}},
    "additionalProperties": False,
}
MANY_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"xs": {"type": "array", "items": {"type": "integer"}}},
}
RETURNS: dict[str, JsonValue] = {"type": "object", "required": ["positions", "view"]}
WEB_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"kind": {"const": "tallies.web"}},
    "required": ["kind"],
    "additionalProperties": False,
}


def entry(
    requires: Sequence[Mapping[str, Any]] = REQUIRES,
    *,
    version: str = "1.0.0",
    independent: bool = False,
    reference: bool = False,
    params: Mapping[str, JsonValue] = OPTIONS,
    returns: Mapping[str, JsonValue] = RETURNS,
) -> AnalysisDescriptor:
    return AnalysisDescriptor.model_validate(
        {
            "kind": "analysis",
            "id": "tallies.echo",
            "version": version,
            "label": "Echo",
            "fields": {
                "requires": list(requires),
                "params": dict(params),
                "returns": dict(returns),
                "methods": {},
                "assumptions": [],
                "uses_reference": reference,
                "assumes_independent_groups": independent,
                "cross_dataset": None,
                "caveats": [],
            },
        }
    )


def echoed(inputs: AnalysisInputs) -> dict[str, Any]:
    """Every input, as values: what the pack is handed is what its result shows."""
    return {
        "positions": [
            {
                "units": position.units,
                "values": [list(column) for column in position.values],
                "excluded": [[list(reasons) for reasons in column] for column in position.excluded],
            }
            for position in inputs.positions
        ],
        "view": {
            "columns": [[c.role, c.column, c.kind, c.function, c.datatype] for c in inputs.columns],
            "options": dict(inputs.options),
            "reference": inputs.reference,
            "overlapping": inputs.overlapping,
            "seed": str(inputs.seed),
        },
    }


class Echo:
    """The ``tallies`` pack's analysis: it gives what ``give`` makes of its inputs, and keeps
    each inputs it is handed."""

    def __init__(
        self, found: AnalysisDescriptor, give: Callable[[AnalysisInputs], object] = echoed
    ) -> None:
        self._entry = found
        self._give = give
        self.handed: list[AnalysisInputs] = []

    @property
    def entry(self) -> AnalysisDescriptor:
        return self._entry

    def run(self, inputs: AnalysisInputs) -> Mapping[str, JsonValue]:
        self.handed.append(inputs)
        return self._give(inputs)  # type: ignore[return-value]


def web_clauses() -> list[Clause]:
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "orders",
        "cohorts": {
            "c": {"all": [{"kind": "value", "column": "orders.channel", "values": ["web"]}]}
        },
    }
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None
    return list(loaded.document.cohorts["c"].all)


class Web:
    """The ``tallies.web`` leaf kind: an order placed on the web; ``schema`` its kind's."""

    def __init__(self, schema: Mapping[str, JsonValue] = WEB_SCHEMA) -> None:
        self._schema = schema

    @property
    def schema(self) -> Mapping[str, JsonValue]:
        return self._schema

    def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> Sequence[Clause]:
        return web_clauses()

    def summary(self, leaf: PackLeaf) -> Sequence[Segment]:
        return [text("orders placed on the web")]


def tallies(
    analysis: Echo | None = None,
    *,
    results_version: int = 1,
    predicates: Mapping[str, Callable[[ReleaseView], bool]] | None = None,
) -> Analyses:
    pack = Pack(
        manifest=PackManifest(
            id="tallies", version="1.0.0", results_version=results_version, requires_core=">=0.0.1"
        ),
        analyses=[analysis or Echo(entry())],
        leaf_kinds={"tallies.web": Web(), "tallies.many": Web(MANY_SCHEMA)},
        requirement_predicates=dict(predicates or {}),
    )
    return Analyses(PackRegistry([pack], core_version=aibi.__version__))


def document(
    columns: Mapping[str, Sequence[Any]] | None = None,
    cohorts: Mapping[str, Sequence[Any]] | None = None,
    **view: Any,
) -> dict[str, Any]:
    given = cohorts if cohorts is not None else {"old": [OLD]}
    params: dict[str, Any] = {
        "columns": dict(columns if columns is not None else {"measure": [AGE]})
    }
    params |= view.pop("params", {})
    return {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {name: {"all": list(clauses)} for name, clauses in given.items()},
        "views": [{"analysis": "tallies.echo", "params": params, **view}],
    }


def refusals(found: Any) -> list[tuple[str, str | None]]:
    return [(refusal.code, refusal.path) for refusal in found.refusals]


def said(refusal: Any) -> str:
    return json.dumps([part.model_dump() for part in refusal.message])


def ages(rows: Rows, cohort: Callable[[int], bool]) -> list[int]:
    """The ages of a cohort's customers, in their keys' order, found independently: each key's
    JSON text compared as UTF-16 code units."""
    found = [row for row in rows()["customers"] if cohort(int(str(row["age"])))]
    found.sort(
        key=lambda row: json.dumps([row["customer_id"]], ensure_ascii=False).encode("utf-16-be")
    )
    return [int(str(row["age"])) for row in found]


# --- Running -------------------------------------------------------------------------------------


def test_a_pack_s_analysis_is_handed_each_member_s_values_in_its_key_s_order(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    echo = Echo(entry())
    [found] = analyse(document(), shop(), analyses=tallies(echo))
    [position] = found.result.values.positions
    assert position["values"] == [ages(rows, lambda age: age >= 50)]
    assert position["units"] == len(position["values"][0])
    assert position["excluded"] == [[[]] * position["units"]]
    assert found.result.analysed[0].n == position["units"]
    [handed] = echo.handed
    assert handed.analysis == "tallies.echo"
    assert handed.version == "1.0.0"
    assert [(c.role, c.column, c.kind, c.datatype) for c in handed.columns] == [
        ("measure", "customers.age", "column", "integer")
    ]
    assert found.result.charts == []


@pytest.mark.parametrize(
    ("column", "datatype"),
    [
        ({"column": "orders.amount", "aggregate": "mean"}, "number"),
        ({"column": "orders.amount", "aggregate": "max"}, "number"),
        ({"column": "customers.age"}, "integer"),
        ({"column": "orders.order_id", "aggregate": "count"}, "integer"),
        ({"column": "orders.channel", "aggregate": "some", "values": ["web"]}, "boolean"),
        ({"column": "customers.tier"}, "category"),
    ],
)
def test_each_input_column_is_handed_the_datatype_of_its_values(
    analyse: Analyse, shop: Shop, column: dict[str, Any], datatype: str
) -> None:
    echo = Echo(entry())
    analyse(document({"measure": [column]}), shop(extended=True), analyses=tallies(echo))
    [handed] = echo.handed
    assert [c.datatype for c in handed.columns] == [datatype]


@pytest.mark.parametrize(
    "given",
    [
        {"positions": [{}], "view": {}, "extra": 1},
        {"positions": [{}, {}], "view": {}},
        {"positions": [1], "view": {}},
        {"positions": [{}], "view": []},
    ],
)
def test_what_is_not_shaped_as_values_is_refused_saying_what_values_are(
    check: Check, shop: Shop, given: dict[str, Any]
) -> None:
    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, Echo(entry(), lambda inputs: given))
    assert "what is not values" in said(failed.value)


def test_a_returns_schema_that_cannot_evaluate_the_values_refuses_them() -> None:
    class Unevaluable:
        def failures(self, value: object, *, budget: object) -> list[Failure]:
            return [Failure((), UNEVALUABLE)]

    with pytest.raises(packs.PackFailed) as failed:
        packs._checked(  # pyright: ignore[reportPrivateUsage]
            "tallies.echo",
            {"positions": [{}], "view": {}},
            cast(Checker, Unevaluable()),
            1,
            None,
        )
    assert "could not evaluate" in said(failed.value)
    assert "unevaluable" not in said(failed.value)


def test_a_pack_s_analysis_is_handed_no_unit_key(analyse: Analyse, shop: Shop) -> None:
    echo = Echo(entry())
    analyse(document(), shop(), analyses=tallies(echo))
    [handed] = echo.handed
    assert "c1" not in repr(handed)
    assert "customer_id" not in repr(handed)


def test_excluded_members_are_handed_with_their_reasons_and_counted_by_reason(
    analyse: Analyse, shop: Shop
) -> None:
    [found] = analyse(document({"measure": [TIER]}), shop(), analyses=tallies())
    [position] = found.result.values.positions
    [values], [excluded] = position["values"], position["excluded"]
    missing = [reasons for value, reasons in zip(values, excluded, strict=True) if value is None]
    assert missing
    assert all(reasons == ["NOT_ASSESSED"] for reasons in missing)
    analysed = found.result.analysed[0]
    assert analysed.excluded_units == len(missing)
    assert analysed.excluded is not None
    assert analysed.excluded["NOT_ASSESSED"] == len(missing)
    assert CaveatCode.UNKNOWN_EXCLUDED in {caveat.code for caveat in found.result.caveats}


def test_aggregates_and_several_roles_are_handed_in_the_entry_s_order_of_roles(
    analyse: Analyse, shop: Shop
) -> None:
    written = document({"extra": [ORDERS], "measure": [TIER, AGE]})
    [found] = analyse(written, shop(), analyses=tallies())
    view = found.result.values.view
    assert view["columns"] == [
        ["measure", "customers.tier", "column", None, "category"],
        ["measure", "customers.age", "column", None, "integer"],
        ["extra", "orders.order_id", "aggregate", "count", "integer"],
    ]
    analysed = found.result.analysed[0]
    assert analysed.variables is not None
    assert len(analysed.variables) == 3


def test_the_options_are_handed_as_written_and_the_seed_is_the_computation_s(
    analyse: Analyse, shop: Shop
) -> None:
    [found] = analyse(document(params={"options": {"scale": 2.5}}), shop(), analyses=tallies())
    view = found.result.values.view
    assert view["options"] == {"scale": 2.5}
    computation = found.view.identity.computation_id
    expected = hashlib.sha256(canonical({"computation": computation})).digest()
    assert view["seed"] == str(int.from_bytes(expected))


def test_a_pack_s_analysis_gives_the_same_digest_every_time(analyse: Analyse, shop: Shop) -> None:
    first = analyse(document(), shop(), analyses=tallies())[0].result
    second = analyse(document(), shop(), analyses=tallies())[0].result
    assert first.digest == second.digest
    assert first.derivation.id == second.derivation.id


def test_the_view_s_ids_hash_the_pack_s_results_version_and_the_entry_s_version(
    check: Check, shop: Shop
) -> None:
    [first] = check(document(), shop(), analyses=tallies()).views
    [bumped] = check(document(), shop(), analyses=tallies(results_version=2)).views
    [newer] = check(document(), shop(), analyses=tallies(Echo(entry(version="1.1.0")))).views
    assert first.packs["tallies"].results_version == 1
    ids = {first.identity.id, bumped.identity.id, newer.identity.id}
    assert len(ids) == 3


def test_the_canonical_parameters_hold_each_role_s_forms_and_the_options(
    check: Check, shop: Shop
) -> None:
    written = document({"measure": [AGE]}, params={"options": {"scale": 1}})
    [view] = check(written, shop(), analyses=tallies()).views
    assert view.identity.params == {
        "columns": {"measure": [{"column": "customers.age"}]},
        "options": {"scale": 1},
    }


def test_the_readback_names_the_analysis_each_role_s_columns_and_the_options(
    check: Check, shop: Shop
) -> None:
    written = document({"measure": [AGE]}, params={"options": {"scale": 1}})
    [view] = check(written, shop(), analyses=tallies()).views
    shown = json.dumps([part.model_dump() for part in view.readback()])
    for expected in ("tallies.echo", "Echo", "measure", '{\\"scale\\":1}'):
        assert expected in shown


def test_a_pack_s_analysis_that_raises_fails_with_a_message_of_the_core_s(
    check: Check, shop: Shop
) -> None:
    def fail(inputs: AnalysisInputs) -> object:
        raise RuntimeError("the secret c1")

    echo = Echo(entry(), fail)
    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, echo)
    assert "c1" not in said(failed.value)
    assert "secret" not in str(failed.value)


@pytest.mark.parametrize("passed", [MemoryError, KeyboardInterrupt, SystemExit])
def test_the_process_out_of_memory_or_asked_to_stop_passes_without_the_pack_s_message(
    check: Check, shop: Shop, passed: type[BaseException]
) -> None:
    def stop(inputs: AnalysisInputs) -> object:
        raise passed("c1 secret")

    with pytest.raises(passed) as raised:
        run_view(check, shop, Echo(entry(), stop))
    assert type(raised.value) is passed
    assert raised.value.args == ()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize("base", [MemoryError, KeyboardInterrupt, SystemExit])
def test_a_pack_s_own_subclass_of_what_passes_is_its_failure(
    check: Check, shop: Shop, base: type[BaseException]
) -> None:
    kind = type("Secret", (base,), {"__module__": "builtins"})

    def stop(inputs: AnalysisInputs) -> object:
        raise kind("c1 secret")

    with pytest.raises(packs.PackFailed):
        run_view(check, shop, Echo(entry(), stop))


def test_the_log_names_no_type_a_pack_made_even_one_that_claims_to_be_built_in(
    check: Check, shop: Shop, caplog: pytest.LogCaptureFixture
) -> None:
    kind = type("c1 secret", (Exception,), {"__module__": "builtins"})

    def fail(inputs: AnalysisInputs) -> object:
        raise kind("c1 secret")

    with pytest.raises(packs.PackFailed):
        run_view(check, shop, Echo(entry(), fail))
    assert "secret" not in caplog.text
    assert "an exception of its own" in caplog.text


@pytest.mark.parametrize(
    "raised",
    [
        JsonTooLarge("c1 secret", 7),
        JsonTooLarge("C1 SECRET not a pattern", 7),
        JsonTooLarge("result_values", 7),
    ],
)
def test_a_limit_s_error_a_pack_raises_is_its_failure_and_names_nothing_it_gave(
    check: Check, shop: Shop, raised: JsonTooLarge
) -> None:
    class Raising(dict[str, Any]):
        def items(self) -> Any:
            raise raised

    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, Echo(entry(), lambda inputs: Raising(echoed(inputs))))
    assert failed.value.limit is None
    assert "secret" not in said(failed.value).lower()
    assert "7" not in said(failed.value)


@pytest.mark.parametrize(
    "given",
    [
        float("nan"),
        {"positions": [{"n": float("inf")}], "view": {}},
        {"positions": [{"n": 2**53}], "view": {}},
        {"positions": [{"n": {1, 2}}], "view": {}},
        {"positions": [], "view": {}},
        {"positions": [{}, {}], "view": {}},
        {"positions": [1], "view": {}},
        {"positions": [{}], "view": []},
        {"positions": [{}], "view": {}, "more": 1},
        {"positions": [{}]},
        {"positions": [{"n": 1, "not_estimable": {"/n": "no_units"}}], "view": {}},
        {"positions": [{"n": list(range(MAX_RESULT_VALUES))}], "view": {}},
    ],
)
def test_what_is_not_values_refuses_the_call(check: Check, shop: Shop, given: object) -> None:
    with pytest.raises(packs.PackFailed):
        run_view(check, shop, Echo(entry(), lambda inputs: given))


def test_values_that_fail_the_returns_schema_refuse_the_call(check: Check, shop: Shop) -> None:
    strict: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"positions": {"type": "array", "items": {"required": ["n"]}}},
    }
    echo = Echo(entry(returns=strict), lambda inputs: {"positions": [{}], "view": {}})
    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, echo)
    assert "required" in said(failed.value)


def test_a_value_that_holds_itself_refuses_the_call(check: Check, shop: Shop) -> None:
    looped: dict[str, Any] = {"view": {}}
    looped["positions"] = [looped]
    with pytest.raises(packs.PackFailed):
        run_view(check, shop, Echo(entry(), lambda inputs: looped))


def test_what_a_pack_does_to_its_inputs_reaches_nothing_else(check: Check, shop: Shop) -> None:
    def spoil(inputs: AnalysisInputs) -> object:
        inputs.options["scale"] = 99  # type: ignore[index]
        return echoed(inputs)

    written = document(params={"options": {"scale": 1}})
    [view] = check(written, shop(), analyses=tallies()).views
    outcome = run_checked(view, Echo(entry(), spoil))
    assert outcome.values.view["options"] == {"scale": 99}
    assert view.identity.params["options"] == {"scale": 1}  # type: ignore[index]
    assert view.params.options == {"scale": 1}
    assert '{\\"scale\\":1}' in json.dumps([part.model_dump() for part in view.readback()])


def test_inputs_of_more_cells_than_a_view_may_have_refuse_the_call(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    [view] = check(document(), shop(), analyses=tallies()).views
    members = evaluate(view.cohorts[0].resolved).n_true
    monkeypatch.setattr(packs, "MAX_INPUT_CELLS", members)
    assert run_checked(view, Echo(entry())).analysed[0].n == members
    monkeypatch.setattr(packs, "MAX_INPUT_CELLS", members - 1)
    with pytest.raises(packs.TooManyCells) as many:
        run_checked(view, Echo(entry()))
    assert many.value.cells == members


def test_a_view_without_columns_counts_a_cell_per_member(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    requires = [{"role": "extra", "kind": "column", "min": 0}]
    echo = Echo(entry(requires))
    [view] = check(document({}), shop(), analyses=tallies(echo)).views
    members = evaluate(view.cohorts[0].resolved).n_true
    [analysed] = run_checked(view, echo).analysed
    assert (analysed.n, analysed.excluded_units, analysed.variables) == (members, 0, None)
    assert echo.handed[0].positions[0].units == members
    monkeypatch.setattr(packs, "MAX_INPUT_CELLS", members - 1)
    with pytest.raises(packs.TooManyCells) as many:
        run_checked(view, echo)
    assert many.value.cells == members


def test_values_the_returns_schema_cannot_check_within_its_steps_refuse_the_call(
    check: Check, shop: Shop
) -> None:
    string: JsonValue = {"type": "string"}
    integer: JsonValue = {"type": "integer"}
    branches: list[JsonValue] = [*([string] * 30), integer]
    costly: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "view": {
                "type": "object",
                "properties": {
                    "xs": {
                        "type": "array",
                        "items": {"anyOf": branches},
                    }
                },
            }
        },
    }
    echo = Echo(
        entry(returns=costly), lambda inputs: {"positions": [{}], "view": {"xs": [1] * 5000}}
    )
    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, echo)
    assert "could not evaluate" in said(failed.value)


def test_the_call_s_deadline_is_looked_at_before_the_pack_runs(check: Check, shop: Shop) -> None:
    echo = Echo(entry())
    with pytest.raises(CallerDeadline):
        run_view(check, shop, echo, ends=0.0)
    assert echo.handed == []


def test_cohorts_that_share_units_are_told_to_the_pack_and_raise_cohorts_overlap(
    analyse: Analyse, shop: Shop
) -> None:
    written = document(cohorts={"old": [OLD], "all": []})
    written["views"][0] |= {"cohorts": ["old", "all"], "overlap": "allow"}
    [found] = analyse(written, shop(), analyses=tallies(Echo(entry(independent=True))))
    assert found.result.values.view["overlapping"] is True
    assert CaveatCode.COHORTS_OVERLAP in {caveat.code for caveat in found.result.caveats}


def test_the_reference_is_handed_by_position(analyse: Analyse, shop: Shop) -> None:
    written = document(cohorts={"old": [OLD], "young": [YOUNG]})
    written["views"][0] |= {"cohorts": ["old", "young"], "reference": "young"}
    [found] = analyse(written, shop(), analyses=tallies(Echo(entry(reference=True))))
    assert found.result.values.view["reference"] == 1
    assert found.result.values.view["overlapping"] is False


def run_checked(view: Any, echo: Echo, ends: float | None = None) -> packs.Outcome:
    variables = [variable.resolved for variable in view.variables]
    found = [ordered(listed(cohort.resolved, variables)) for cohort in view.cohorts]
    positions = [CohortAt(cohort, evaluate(cohort.resolved)) for cohort in view.cohorts]
    analyses = tallies(echo)
    _, _, returns = analyses.implementation("tallies.echo")
    return packs.run_pack(
        echo,
        returns,
        positions,
        list(zip(view.roles, view.variables, strict=True)),
        found,
        view.params,
        reference=view.reference,
        overlapping=False,
        computation=view.identity.computation_id,
        ends=ends,
    )


def run_view(check: Check, shop: Shop, echo: Echo, ends: float | None = None) -> packs.Outcome:
    [view] = check(document(), shop(), analyses=tallies(echo)).views
    return run_checked(view, echo, ends)


# --- Phase 2 -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("columns", "options", "expected"),
    [
        ({"measure": [AGE], "other": [AGE]}, {}, ("UNKNOWN_MEMBER", "/params/columns/other")),
        ({"extra": [AGE]}, {}, ("MISSING_MEMBER", "/params/columns")),
        ({"measure": [AGE] * 3}, {}, ("INVALID_VALUE", "/params/columns/measure")),
        ({"measure": [AGE]}, {"scale": -1}, ("INVALID_VALUE", "/params/options/scale")),
        ({"measure": [AGE]}, {"shade": 1}, ("INVALID_VALUE", "/params/options")),
        ({"measure": [AGE] * 2, "extra": [AGE] * 7}, {}, ("LIMIT_EXCEEDED", "/params/columns")),
        ({"measure": []}, {}, ("INVALID_VALUE", "/params/columns/measure")),
    ],
)
def test_parameters_the_entry_does_not_take_are_refused_where_they_are_written(
    check: Check,
    shop: Shop,
    columns: dict[str, Any],
    options: dict[str, Any],
    expected: tuple[str, str],
) -> None:
    written = document(columns, params={"options": options} if options else {})
    found = check(written, shop(), analyses=tallies())
    code, at = expected
    assert (code, "/views/0" + at) in refusals(found)
    assert found.views == []


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("floor", [None, 3])
def test_a_count_of_rows_is_refused_for_a_pack_s_analysis_before_phase_1_whatever_it_assumes(
    check: Check, shop: Shop, independent: bool, floor: int | None
) -> None:
    """A pack's analysis is handed one value per unit (D335, D378), under a disclosure setting
    or not: ``count: "rows"`` is refused where it is written as the view is parsed, before phase
    1 resolves it and before the pack runs."""
    echo = Echo(entry(independent=independent))
    written = document({"measure": [{"column": "orders.amount", "count": "rows"}]})
    found = check(written, shop(extended=True), analyses=tallies(echo), floor=floor)
    assert refusals(found) == [("INVALID_VALUE", "/views/0/params/columns/measure/0/count")]
    assert found.views == []
    assert echo.handed == []


def test_a_column_requirement_without_a_minimum_needs_one_column(check: Check, shop: Shop) -> None:
    requires = [
        {"role": "measure", "kind": "column"},
        {"role": "extra", "kind": "column", "min": 0},
    ]
    found = check(document({"extra": [AGE]}), shop(), analyses=tallies(Echo(entry(requires))))
    assert refusals(found) == [("MISSING_MEMBER", "/views/0/params/columns")]


def test_a_view_with_more_cohorts_than_the_entry_takes_is_refused(check: Check, shop: Shop) -> None:
    given = {name: [OLD] for name in ("a", "b", "c", "d")}
    found = check(document(cohorts=given), shop(), analyses=tallies())
    assert refusals(found) == [("INVALID_VALUE", "/views/0/cohorts")]


ENDPOINT = {"role": "time", "kind": "endpoint", "on": "unit"}


def rowed(inputs: AnalysisInputs) -> dict[str, Any]:
    """Every input, its endpoints' rows included, as values."""
    found = echoed(inputs)
    found["view"]["endpoints"] = [[e.role, e.endpoint, e.units, e.entry] for e in inputs.endpoints]
    for shown, position in zip(found["positions"], inputs.positions, strict=True):
        shown["endpoints"] = [
            [None if row is None else list(row) for row in rows] for rows in position.endpoints
        ]
        shown["endpoint_excluded"] = [
            [list(reasons) for reasons in column] for column in position.endpoint_excluded
        ]
    return found


def retention_rows(rows: Rows, delayed: bool) -> list[Any]:
    """The old customers' retention rows, in their keys' order, found independently."""
    found = [row for row in rows(survived=True)["customers"] if int(str(row["age"])) >= 50]
    found.sort(
        key=lambda row: json.dumps([row["customer_id"]], ensure_ascii=False).encode("utf-16-be")
    )
    return [
        None
        if row["left"] == "?"
        else [row["joined"] if delayed else None, row["tenure"], row["left"] == "yes"]
        for row in found
    ]


@pytest.mark.parametrize("entered", ["delayed", "origin"])
def test_a_pack_s_analysis_is_handed_each_member_s_endpoint_row_in_its_key_s_order(
    analyse: Analyse, shop: Shop, rows: Rows, entered: str
) -> None:
    echo = Echo(entry([*REQUIRES, ENDPOINT]), rowed)
    [found] = analyse(document(), shop(survived=entered), analyses=tallies(echo))
    [position] = found.result.values.positions
    expected = retention_rows(rows, entered == "delayed")
    assert position["endpoints"] == [expected]
    assert position["endpoint_excluded"] == [
        [["NOT_ASSESSED"] if row is None else [] for row in expected]
    ]
    assert found.result.values.view["endpoints"] == [
        ["time", "ep:retention", "mo", entered == "delayed"]
    ]
    analysed = found.result.analysed[0]
    assert analysed.variables is not None
    assert len(analysed.variables) == 2
    assert analysed.variables[1].n == sum(row is not None for row in expected)
    assert found.view.identity.params["endpoints"] == {
        "time": {"id": "ep:retention", "time": "customers.tenure"}
    }


def test_a_row_that_section_5_8_calls_invalid_is_excluded_and_its_caveat_raised(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    given = rows(survived=True)
    old = [row for row in given["customers"] if int(str(row["age"])) >= 50]
    old[0]["joined"] = old[0]["tenure"]
    old[1]["left"] = "?"
    echo = Echo(entry([*REQUIRES, ENDPOINT]), rowed)
    [found] = analyse(document(), shop(given, survived="delayed"), analyses=tallies(echo))
    [position] = found.result.values.positions
    assert ["INVALID_VALUE"] in position["endpoint_excluded"][0]
    assert ["NOT_ASSESSED"] in position["endpoint_excluded"][0]
    raised = {caveat.code for caveat in found.result.caveats}
    assert {CaveatCode.INVALID_EXCLUDED, CaveatCode.UNKNOWN_EXCLUDED} <= raised


def test_a_view_binds_the_endpoint_it_names_to_its_role(check: Check, shop: Shop) -> None:
    other = build.descriptor(
        "endpoint",
        "ep:other",
        {
            "table": "customers",
            "time_column": "tenure",
            "status_column": "left",
            "event_coding": {"event": ["no"], "censored": ["yes"]},
        },
    )
    release = shop(survived="origin", extras=[other])
    requires = [*REQUIRES, ENDPOINT]
    found = check(document(), release, analyses=tallies(Echo(entry(requires))))
    assert refusals(found) == [("MISSING_MEMBER", "/views/0/params/endpoints/time")]
    named = document(params={"endpoints": {"time": "ep:other"}})
    [view] = check(named, release, analyses=tallies(Echo(entry(requires)))).views
    assert view.identity.params["endpoints"]["time"]["id"] == "ep:other"


@pytest.mark.parametrize(
    ("member", "column", "coding"),
    [
        ("status_column", "customer_id", ["c1", "c2"]),
        ("status_column", "email", ["a", "b"]),
        ("time_column", "serial", ["yes", "no"]),
        ("entry", "serial", ["yes", "no"]),
    ],
    ids=["a status that is the key", "a declared status", "a declared time", "a declared entry"],
)
def test_an_endpoint_that_reads_an_identifier_column_is_never_handed(
    check: Check, shop: Shop, member: str, column: str, coding: list[str]
) -> None:
    fields: dict[str, Any] = {
        "table": "customers",
        "time_column": "tenure",
        "status_column": "left",
        "event_coding": {"event": coding[:1], "censored": coding[1:]},
    }
    if member == "entry":
        fields["entry"] = {"column": column}
    else:
        fields[member] = column
    keyed = build.descriptor("endpoint", "ep:keyed", fields)
    extras = [
        build.column("customers.email", "string", identifier=True),
        build.column("customers.serial", "time_offset", identifier=True, units="mo"),
        keyed,
    ]
    release = shop(survived="origin", extras=extras)
    named = document(params={"endpoints": {"time": "ep:keyed"}})
    found = check(named, release, analyses=tallies(Echo(entry([*REQUIRES, ENDPOINT]))))
    assert refusals(found) == [("ROW_IDS_NOT_ALLOWED", "/views/0/params/endpoints/time")]
    [refusal] = found.refusals
    assert f"customers.{column}" in json.dumps(refusal.model_dump(mode="json"))


def test_an_endpoint_below_the_unit_is_refused_since_a_unit_has_many_of_its_rows(
    check: Check, shop: Shop
) -> None:
    below = build.descriptor(
        "endpoint",
        "ep:orders",
        {
            "table": "orders",
            "time_column": "days",
            "status_column": "channel",
            "event_coding": {"event": ["web"], "censored": ["shop"]},
        },
    )
    extras = [build.column("orders.days", "time_offset", units="d"), below]
    release = shop(survived="origin", extras=extras)
    named = document(params={"endpoints": {"time": "ep:orders"}})
    anywhere = {"role": "time", "kind": "endpoint"}
    found = check(named, release, analyses=tallies(Echo(entry([*REQUIRES, anywhere]))))
    assert refusals(found) == [("INVALID_VALUE", "/views/0/params/endpoints/time")]
    assert "goes below the unit" in said(found.refusals[0])


def test_an_endpoint_without_a_table_meets_no_endpoint_requirement_on_any_table(
    check: Check, shop: Shop, rows: Rows
) -> None:
    tableless = build.descriptor(
        "endpoint",
        "ep:retention",
        {
            "time_column": "tenure",
            "status_column": "left",
            "event_coding": {"event": ["yes"], "censored": ["no"]},
        },
    )
    given = shop(survived="origin")
    descriptors = [d for d in given.descriptors if d.id != "ep:retention"]
    release = build.release([*descriptors, tableless], rows(survived=True))
    analyses = tallies(Echo(entry([*REQUIRES, {"role": "time", "kind": "endpoint"}])))
    found = analyses.applicable(
        list(release.descriptors), dataset=release.dataset, manifest=release.manifest
    )
    [echoed] = [item for item in found if item.analysis == "tallies.echo"]
    assert (echoed.status, echoed.missing) == ("unavailable", ["time"])
    checked = check(document(), release, analyses=analyses)
    assert refusals(checked) == [("MISSING_MEMBER", "/views/0/params/endpoints/time")]


def test_a_view_without_a_usable_endpoint_for_a_required_role_is_refused_where_it_goes(
    check: Check, shop: Shop
) -> None:
    requires = [*REQUIRES, ENDPOINT]
    found = check(document(), shop(), analyses=tallies(Echo(entry(requires))))
    assert refusals(found) == [("MISSING_MEMBER", "/views/0/params/endpoints/time")]


def test_an_endpoint_of_a_role_the_analysis_does_not_require_is_refused_with_the_roles_it_does(
    check: Check, shop: Shop
) -> None:
    written = document(params={"endpoints": {"when": "ep:retention"}})
    requires = [*REQUIRES, ENDPOINT]
    found = check(written, shop(survived="origin"), analyses=tallies(Echo(entry(requires))))
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (
        RefusalCode.UNKNOWN_MEMBER,
        "/views/0/params/endpoints/when",
    )
    assert [part.model_dump() for part in refusal.alternatives] == [{"data": "time"}]


def test_an_endpoint_requirement_without_on_unit_reads_an_endpoint_through_a_lookup(
    analyse: Analyse, check: Check, shop: Shop
) -> None:
    loose = {"role": "time", "kind": "endpoint"}
    written = document({"measure": [{"column": "orders.channel"}]}, cohorts={"all": []})
    written["unit"] = "orders"
    echo = Echo(entry([*REQUIRES, loose]), rowed)
    [found] = analyse(written, shop(survived="origin"), analyses=tallies(echo))
    [position] = found.result.values.positions
    assert len(position["endpoints"][0]) == position["units"]
    strict = tallies(Echo(entry([*REQUIRES, ENDPOINT])))
    refused = check(written, shop(survived="origin"), analyses=strict)
    assert refusals(refused) == [("MISSING_MEMBER", "/views/0/params/endpoints/time")]
    named = document({"measure": [{"column": "orders.channel"}]}, cohorts={"all": []})
    named["unit"] = "orders"
    named["views"][0]["params"]["endpoints"] = {"time": "ep:retention"}
    refused = check(named, shop(survived="origin"), analyses=strict)
    assert refusals(refused) == [("INVALID_VALUE", "/views/0/params/endpoints/time")]


def test_an_optional_endpoint_requirement_before_a_required_one_leaves_it_bound(
    analyse: Analyse, shop: Shop
) -> None:
    optional = {"role": "before", "kind": "endpoint", "min": 0}
    echo = Echo(entry([*REQUIRES, optional, ENDPOINT]), rowed)
    [found] = analyse(document(), shop(survived="origin"), analyses=tallies(echo))
    assert [row[0] for row in found.result.values.view["endpoints"]] == ["time"]


def test_members_excluded_from_every_input_are_counted_together_by_reason(
    analyse: Analyse, shop: Shop, rows: Rows
) -> None:
    given = rows(survived=True)
    for row in given["customers"]:
        if int(str(row["age"])) >= 50:
            row["tier"], row["left"] = "?", "?"
            break
    echo = Echo(entry([*REQUIRES, ENDPOINT]), rowed)
    written = document({"measure": [TIER]})
    [found] = analyse(written, shop(given, survived="origin"), analyses=tallies(echo))
    [position] = found.result.values.positions
    both = [
        index
        for index, reasons in enumerate(position["excluded"][0])
        if reasons and position["endpoint_excluded"][0][index]
    ]
    analysed = found.result.analysed[0]
    assert len(both) == 1
    assert analysed.excluded_units == 1
    assert analysed.excluded is not None
    assert analysed.excluded["NOT_ASSESSED"] == 1
    assert analysed.n == position["units"] - 1


def test_an_optional_endpoint_is_bound_only_when_it_is_named(analyse: Analyse, shop: Shop) -> None:
    optional = {**ENDPOINT, "min": 0}
    echo = Echo(entry([*REQUIRES, optional]), rowed)
    [unbound] = analyse(document(), shop(survived="origin"), analyses=tallies(echo))
    assert unbound.result.values.view["endpoints"] == []
    assert "endpoints" not in unbound.view.identity.params
    named = document(params={"endpoints": {"time": "ep:retention"}})
    [bound] = analyse(named, shop(survived="origin"), analyses=tallies(echo))
    assert bound.result.values.view["endpoints"] == [["time", "ep:retention", "mo", False]]
    assert unbound.view.identity.id != bound.view.identity.id


@pytest.mark.parametrize(
    ("requirement", "column", "expected"),
    [
        ({"datatype": "number"}, AGE, ("INVALID_VALUE", "/measure/0/column")),
        ({"on": "unit"}, ORDERS, ("INVALID_VALUE", "/measure/0/column")),
        ({}, {"column": "customers.joined"}, ("NOT_SUPPORTED", "/measure/0/column")),
        (
            {"on": "unit"},
            {"column": "customers.labels", "aggregate": "some", "values": ["a"]},
            ("INVALID_VALUE", "/measure/0/column"),
        ),
    ],
)
def test_a_column_its_role_does_not_take_is_refused_where_it_is_written(
    check: Check,
    shop: Shop,
    requirement: dict[str, Any],
    column: dict[str, Any],
    expected: tuple[str, str],
) -> None:
    requires = [{"role": "measure", "kind": "column", **requirement}]
    release = joined_shop(shop)
    found = check(document({"measure": [column]}), release, analyses=tallies(Echo(entry(requires))))
    code, at = expected
    assert refusals(found) == [(code, "/views/0/params/columns" + at)]


def joined_shop(shop: Shop) -> Any:
    extras = [
        build.column("customers.joined", "date"),
        build.column(
            "customers.labels",
            "list<category>",
            permissible_values={"values": [{"value": "a"}, {"value": "b"}]},
        ),
    ]
    release = shop(extras=extras)
    return release


@pytest.mark.parametrize("holds", [lambda release: False, lambda release: 1 / 0])
def test_a_requirement_predicate_that_does_not_hold_refuses_the_view(
    check: Check, shop: Shop, holds: Callable[[ReleaseView], bool]
) -> None:
    requires = [*REQUIRES, {"role": "stock", "predicate": "tallies.stocked"}]
    analyses = tallies(Echo(entry(requires)), predicates={"stocked": holds})
    found = check(document(), shop(), analyses=analyses)
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (RefusalCode.NOT_SUPPORTED, "/views/0/analysis")
    assert "stock" in said(refusal)


def test_a_requirement_predicate_that_holds_lets_the_view_run(analyse: Analyse, shop: Shop) -> None:
    requires = [*REQUIRES, {"role": "stock", "predicate": "tallies.stocked"}]
    analyses = tallies(Echo(entry(requires)), predicates={"stocked": lambda release: True})
    [found] = analyse(document(), shop(), analyses=analyses)
    assert found.result.values.positions[0]["units"] > 0


@pytest.mark.parametrize(
    ("member", "schema"),
    [("params", {"type": "object", "pattern": "x"}), ("returns", {"type": "nothing"})],
)
def test_a_pack_whose_analysis_s_schemas_are_refused_is_not_registered(
    member: str, schema: dict[str, JsonValue]
) -> None:
    found = entry(**{member: schema})  # type: ignore[arg-type]
    pack = Pack(
        manifest=PackManifest(
            id="tallies", version="1.0.0", results_version=1, requires_core=">=0.0.1"
        ),
        analyses=[Echo(found)],
    )
    with pytest.raises(PackError) as refused:
        PackRegistry([pack], core_version=aibi.__version__)
    assert f"the {member} schema of analysis tallies.echo is refused" in str(refused.value)


# --- Disclosure ----------------------------------------------------------------------------------


@pytest.mark.parametrize("setting", ["dataset", "floor", "both"])
@pytest.mark.parametrize("k", [2, 3, 4, 5])
@pytest.mark.parametrize("cohort", [[OLD], [YOUNG], [], [{"not": OLD}]])
def test_under_any_disclosure_setting_a_pack_s_analysis_is_refused_and_never_run(
    check: Check, shop: Shop, setting: str, k: int, cohort: list[Any]
) -> None:
    echo = Echo(entry())
    disclosure = {"min_cell_count": k} if setting != "floor" else None
    floor = k if setting != "dataset" else None
    written = document({"measure": [AGE, TIER]}, cohorts={"c": cohort})
    found = check(written, shop(disclosure=disclosure), floor=floor, analyses=tallies(echo))
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (RefusalCode.WITHHELD_UNDER_K, "/views/0/analysis")
    assert found.views == []
    assert echo.handed == []
    shown = json.dumps(refusal.model_dump(mode="json"))
    for name in ("c1", "c2", "gold", "silver", "bronze"):
        assert f'"{name}"' not in shown
    assert f"{k})" in said(refusal)


@pytest.mark.parametrize(("disclosure", "k"), [(None, 3), ({"min_cell_count": 4}, 4)])
def test_a_pack_s_analysis_is_unavailable_under_a_disclosure_setting_naming_it(
    shop: Shop, disclosure: dict[str, Any] | None, k: int
) -> None:
    release = shop(disclosure=disclosure)
    analyses = tallies()
    for given in (None, k):
        found = {
            item.analysis: (item.status, item.missing)
            for item in analyses.applicable(
                list(release.descriptors),
                dataset=release.dataset,
                manifest=release.manifest,
                k=given,
            )
        }
        expected = ("available", []) if given is None else ("unavailable", ["min_cell_count"])
        assert found["tallies.echo"] == expected


# --- Pack leaves in a variable's where (D345) ----------------------------------------------------

WEB_ORDERS = {"column": "orders.order_id", "aggregate": "count", "where": [{"kind": "tallies.web"}]}
WEB_WRITTEN = {
    "column": "orders.order_id",
    "aggregate": "count",
    "where": [{"kind": "value", "column": "orders.channel", "values": ["web"]}],
}


def test_a_pack_leaf_in_a_column_s_where_is_expanded_into_the_canonical_form(
    check: Check, shop: Shop
) -> None:
    [leaf] = check(document({"measure": [WEB_ORDERS]}), shop(), analyses=tallies()).views
    [plain] = check(document({"measure": [WEB_WRITTEN]}), shop(), analyses=tallies()).views
    assert leaf.identity.params == plain.identity.params
    assert leaf.variables[0].packs["tallies"].results_version == 1
    assert leaf.identity.id == plain.identity.id


def test_a_pack_leaf_in_a_column_s_where_counts_the_rows_its_expansion_names(
    analyse: Analyse, shop: Shop
) -> None:
    [leaf] = analyse(document({"measure": [WEB_ORDERS]}), shop(), analyses=tallies())
    [plain] = analyse(document({"measure": [WEB_WRITTEN]}), shop(), analyses=tallies())
    assert leaf.result.values == plain.result.values
    assert leaf.result.digest == plain.result.digest


def test_a_pack_leaf_in_a_core_analysis_s_column_hashes_its_pack_s_results_version(
    check: Check, shop: Shop
) -> None:
    written = document()
    written["views"][0] = {
        "analysis": "summary.distribution",
        "params": {"columns": [WEB_ORDERS]},
    }
    [first] = check(written, shop(), analyses=tallies()).views
    [bumped] = check(written, shop(), analyses=tallies(results_version=2)).views
    assert first.packs["tallies"].results_version == 1
    assert first.identity.id != bumped.identity.id
    shown = json.dumps([part.model_dump() for part in first.readback()])
    assert "orders placed on the web" in shown


def test_a_pack_leaf_in_a_predicate_covariate_hashes_its_pack_s_results_version_once_read(
    check: Check, shop: Shop
) -> None:
    """A predicate covariate's packs, leaves and summaries are its predicate's, which the view
    holds among its predicates (D371): they enter the id and the readback once."""
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"all": {"all": []}},
        "views": [
            {
                "analysis": "survival.cox",
                "cohorts": ["all"],
                "params": {"covariates": [{"predicate": {"kind": "tallies.web"}}]},
            }
        ],
    }
    [first] = check(written, shop(survived="origin"), analyses=tallies()).views
    [bumped] = check(written, shop(survived="origin"), analyses=tallies(results_version=2)).views
    assert first.packs["tallies"].results_version == 1
    assert first.identity.id != bumped.identity.id
    shown = json.dumps([part.model_dump() for part in first.readback()])
    assert shown.count("orders placed on the web") == 1
    [predicate] = first.predicates
    assert list(predicate.leaves) != []


def test_a_pack_leaf_its_kind_s_schema_refuses_is_refused_where_it_is_written(
    check: Check, shop: Shop
) -> None:
    bad = {**WEB_ORDERS, "where": [{"kind": "tallies.web", "size": 2}]}
    found = check(document({"measure": [bad]}), shop(), analyses=tallies())
    [(code, at)] = refusals(found)
    assert code == RefusalCode.INVALID_VALUE
    assert at is not None
    assert at.startswith("/views/0/params/columns/measure/0/where/0")


def test_more_pack_leaves_in_a_column_s_where_than_a_cohort_may_have_are_refused(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "MAX_PACK_LEAVES", 1)
    many = {**WEB_ORDERS, "where": [{"kind": "tallies.web"}, {"kind": "tallies.web"}]}
    found = check(document({"measure": [many]}), shop(), analyses=tallies())
    assert refusals(found) == [("LIMIT_EXCEEDED", "/views/0/params/columns/measure/0/where")]


def test_a_pack_view_s_golden_ids_and_digest_are_unchanged(analyse: Analyse, shop: Shop) -> None:
    """The ids and the digest of a view of ``tallies.echo`` when they were checked in: a change to
    the inputs a pack is handed, their order, or the view's canonical form fails here unless a
    version the ids hash was bumped (§7.6, D342)."""
    written = document(
        {"extra": [ORDERS], "measure": [TIER, AGE]}, params={"options": {"scale": 2}}
    )
    [found] = analyse(written, shop(), analyses=tallies())
    assert (
        found.result.derivation.id,
        found.view.identity.computation_id,
        found.result.digest,
    ) == (
        "drv:ad33b62644b9993edb9ccd060b6c0da34ecd9aa2a597c02c1e2006768aa860ef",
        "drv:0cae12a83cfb492d87c4d9c00546f0579feeafb7ce9d820a7a6be0105fe6a6df",
        "sha256:36d68f494b1eb5e4f168d898f66f511d292bc6c9a257919deb2adb36582fe02a",
    )


# --- Round 1 of review -----------------------------------------------------------------------


class Raising(dict[str, Any]):
    """Values whose reading raises: the pack's code, run while the core copies them."""

    def items(self) -> Any:
        raise KeyError("c1 secret")


class Own(BaseException):
    """An exception of the pack's own, not an ``Exception``."""


@pytest.mark.parametrize(
    "give",
    [
        lambda inputs: Raising(positions=[{}], view={}),
        lambda inputs: (_ for _ in ()).throw(Own("c1 secret")),
    ],
)
def test_what_a_pack_raises_while_it_runs_or_while_its_values_are_read_refuses_the_call(
    check: Check, shop: Shop, caplog: pytest.LogCaptureFixture, give: Callable[..., object]
) -> None:
    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, Echo(entry(), give))
    assert "c1" not in said(failed.value)
    assert "secret" not in caplog.text
    assert "Own" not in caplog.text
    assert "KeyError" in caplog.text or "an exception of its own" in caplog.text


def test_a_returns_failure_quotes_no_key_the_pack_gave(check: Check, shop: Shop) -> None:
    strict: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"view": {"additionalProperties": {"type": "integer"}}},
    }
    echo = Echo(entry(returns=strict), lambda inputs: {"positions": [{}], "view": {"c1": "x"}})
    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, echo)
    assert "c1" not in said(failed.value)
    assert "type" in said(failed.value)


class Counted(list[int]):
    """A list that counts the items read from it."""

    read = 0

    def __iter__(self) -> Any:
        for item in super().__iter__():
            Counted.read += 1
            yield item


@pytest.mark.parametrize(
    ("give", "name"),
    [
        ({"positions": [{"s": "x" * 10_001}], "view": {}}, "text_characters"),
        ({"positions": [{"s": ["x" * 10_000] * 900}], "view": {}}, "result_characters"),
        (
            {"positions": [{f"{n:04}" + "x" * 9_000: 0 for n in range(1_000)}], "view": {}},
            "result_characters",
        ),
        ({"positions": [{"n": [0] * 200_000}], "view": {}}, "result_values"),
    ],
)
def test_values_larger_than_a_result_holds_refuse_the_call_naming_the_limit(
    check: Check, shop: Shop, give: object, name: str
) -> None:
    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, Echo(entry(), lambda inputs: give))
    assert failed.value.limit is not None
    assert failed.value.limit.name == name


def test_values_are_counted_before_they_are_copied_and_the_cap_is_inclusive(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packs, "MAX_RESULT_VALUES", 10)
    exact = {"positions": [{"n": [0] * 5}], "view": {}}
    assert run_view(check, shop, Echo(entry(), lambda inputs: exact)).values.positions[0]["n"]
    over = {"positions": [{"n": [0] * 6}], "view": {}}
    with pytest.raises(packs.PackFailed):
        run_view(check, shop, Echo(entry(), lambda inputs: over))
    Counted.read = 0
    many = {"positions": [{"n": Counted([0] * 1_000)}], "view": {}}
    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, Echo(entry(), lambda inputs: many))
    assert failed.value.limit is not None
    assert failed.value.limit.max == 10
    assert Counted.read <= 10


def test_the_call_s_deadline_is_looked_at_after_the_pack_runs(check: Check, shop: Shop) -> None:
    ends = time.monotonic() + 0.2

    read: list[bool] = []

    class Watched(dict[str, Any]):
        def items(self) -> Any:
            read.append(True)
            return super().items()

    def slow(inputs: AnalysisInputs) -> object:
        time.sleep(0.3)
        return Watched(echoed(inputs))

    with pytest.raises(CallerDeadline):
        run_view(check, shop, Echo(entry(), slow), ends=ends)
    assert read == []


def test_checking_the_returns_schema_stops_at_the_call_s_deadline() -> None:
    checker = Checker({"type": "array", "items": {"type": "integer"}})
    many: JsonValue = [0 for _ in range(20_000)]
    with pytest.raises(OutOfTime):
        checker.failures(many, budget=TimedBudget(10**6, time.monotonic() - 1))
    assert checker.failures(many, budget=TimedBudget(10**6, time.monotonic() + 60)) == []


def test_counting_several_columns_stops_at_the_call_s_deadline() -> None:
    values: tuple[tuple[Any, ...], ...] = ((1, None), (None, None))
    reasons = frozenset({ExclusionReason.NO_INFORMATION})
    excluded = ((frozenset(), reasons), (reasons, reasons))
    inputs = list(zip(values, excluded, strict=True))
    with pytest.raises(CallerDeadline):
        packs._analysed(2, inputs, time.monotonic() - 1)  # pyright: ignore[reportPrivateUsage]


def test_two_columns_are_counted_together_by_the_units_some_column_has_a_value_for() -> None:
    no_info = frozenset({ExclusionReason.NO_INFORMATION})
    assessed = frozenset({ExclusionReason.NOT_ASSESSED})
    values: tuple[tuple[Any, ...], ...] = ((1, None, None), (None, 2, None))
    excluded = ((frozenset(), no_info, no_info), (assessed, frozenset(), assessed))
    inputs = list(zip(values, excluded, strict=True))
    found = packs._analysed(3, inputs)  # pyright: ignore[reportPrivateUsage]
    assert (found.n, found.excluded_units) == (2, 1)
    assert found.excluded is not None
    assert found.excluded[ExclusionReason.NO_INFORMATION] == 1
    assert found.excluded[ExclusionReason.NOT_ASSESSED] == 1
    assert found.variables is not None
    assert [(v.n, v.excluded_units) for v in found.variables] == [(1, 2), (1, 2)]


def test_members_excluded_only_as_not_applicable_raise_no_unknown_excluded(
    check: Check, shop: Shop
) -> None:
    [view] = check(document(), shop(), analyses=tallies()).views
    [cohort] = view.cohorts
    [rows] = [ordered(listed(cohort.resolved, [v.resolved for v in view.variables]))]
    applicable = frozenset({ExclusionReason.NOT_APPLICABLE})
    given = Listed(
        rows.keys,
        rows.rows,
        (tuple(None for _ in rows.keys),),
        (tuple(applicable for _ in rows.keys),),
        (frozenset(),),
    )
    _, _, returns = tallies().implementation("tallies.echo")
    outcome = packs.run_pack(
        Echo(entry()),
        returns,
        [CohortAt(cohort, evaluate(cohort.resolved))],
        list(zip(view.roles, view.variables, strict=True)),
        [given],
        view.params,
        reference=0,
        overlapping=False,
        computation=view.identity.computation_id,
    )
    assert CaveatCode.UNKNOWN_EXCLUDED not in {caveat.code for caveat in outcome.caveats}


def test_what_a_pack_does_to_a_column_s_form_reaches_nothing_else(check: Check, shop: Shop) -> None:
    def spoil(inputs: AnalysisInputs) -> object:
        form = inputs.columns[0].form
        assert isinstance(form, dict)
        form["column"] = "customers.tier"
        return echoed(inputs)

    [view] = check(document(), shop(), analyses=tallies()).views
    before = view.identity.id
    run_checked(view, Echo(entry(), spoil))
    assert view.variables[0].form == {"column": "customers.age"}
    assert view.identity.id == before


@pytest.mark.parametrize(
    ("dataset", "floor", "published", "setting"),
    [
        ({"disclosure": {"min_cell_count": 5}}, None, None, "the dataset's min_cell_count, 5"),
        ({}, 3, None, "the deployment's floor, 3"),
        ({}, None, 5, "the latest published release's min_cell_count, 5"),
        ({"disclosure": {"min_cell_count": 2}}, 3, 5, "the latest published release's"),
    ],
)
def test_the_refusal_under_a_disclosure_setting_names_the_setting_that_binds(
    check: Check,
    shop: Shop,
    dataset: dict[str, Any],
    floor: int | None,
    published: int | None,
    setting: str,
) -> None:
    release = shop(disclosure=dataset.get("disclosure"))
    found = check(document(), release, floor=floor, published=published, analyses=tallies())
    [refusal] = found.refusals
    assert setting in said(refusal)


def test_a_dataset_that_allows_no_row_ids_refuses_every_view_and_lists_it_unavailable(
    check: Check, shop: Shop
) -> None:
    echo = Echo(entry())
    release = shop(disclosure={"min_cell_count": 3, "allow_row_ids": False})
    found = check(document(), release, analyses=tallies(echo))
    [refusal] = found.refusals
    assert (refusal.code, refusal.path) == (RefusalCode.ROW_IDS_NOT_ALLOWED, "/views/0/analysis")
    assert echo.handed == []
    statuses = {
        item.analysis: (item.status, item.missing)
        for item in tallies().applicable(
            list(release.descriptors), dataset=release.dataset, manifest=release.manifest, k=3
        )
    }
    assert statuses["tallies.echo"] == ("unavailable", ["allow_row_ids", "min_cell_count"])


def identified_shop(shop: Shop) -> Any:
    """The shop with a declared identifier on the unit table and one below it (§5.4)."""
    extras = [
        build.column("customers.email", "string", identifier=True),
        build.column("orders.tracking", "string", identifier=True),
    ]
    return shop(extras=extras)


@pytest.mark.parametrize(
    "column",
    [
        {"column": "customers.customer_id"},
        {"column": "orders.customer_id", "aggregate": "every", "values": ["c1"]},
        {"column": "returns.order_id", "aggregate": "some", "values": ["o1"]},
        {"column": "orders.order_id", "aggregate": "some", "values": ["o1"]},
        {"column": "customers.email"},
        {"column": "orders.tracking", "aggregate": "some", "values": ["t1"]},
    ],
)
def test_an_identifier_column_s_values_are_never_handed(
    check: Check, shop: Shop, column: dict[str, Any]
) -> None:
    found = check(document({"measure": [column]}), identified_shop(shop), analyses=tallies())
    assert refusals(found) == [("ROW_IDS_NOT_ALLOWED", "/views/0/params/columns/measure/0/column")]


IDENTIFYING = {
    "customers.customer_id",
    "customers.email",
    "orders.order_id",
    "orders.customer_id",
    "orders.tracking",
    "returns.return_id",
    "returns.order_id",
}
"""The shop's identifier columns (§5.4): its tables' keys, its relationships' columns on both
sides and the columns declared ``identifier``, listed by hand."""


def _bound(release: Any, column: str) -> dict[str, Any]:
    """A variable that reads a column's values: the column itself on the unit table, and below
    it the one aggregate or question its datatype takes."""
    table, _, name = column.partition(".")
    if table == "customers":
        return {"column": column}
    descriptor = release.column(table, name)
    datatype = descriptor.fields.datatype
    if datatype in ("integer", "number"):
        return {"column": column, "aggregate": "max"}
    levels = descriptor.fields.permissible_values
    value = levels.values[0].value if levels is not None else "x"
    return {"column": column, "aggregate": "some", "values": [value]}


def test_every_column_of_the_shop_is_handed_exactly_when_it_identifies_nothing(
    check: Check, shop: Shop
) -> None:
    """The one rule over every column the shop's units reach (D342): each column's values are
    refused as a pack's input exactly when it is an identifier by §5.4, however it is read."""
    release = identified_shop(shop)
    columns = sorted(
        f"{table}.{name}"
        for table in ("customers", "orders", "returns")
        for name in release.columns(table)
    )
    assert set(columns) >= IDENTIFYING
    found = {
        column: refusals(
            check(document({"measure": [_bound(release, column)]}), release, analyses=tallies())
        )
        for column in columns
    }
    at = "/views/0/params/columns/measure/0/column"
    assert {column for column, given in found.items() if given} == IDENTIFYING
    assert all(found[column] == [("ROW_IDS_NOT_ALLOWED", at)] for column in IDENTIFYING)


@pytest.mark.parametrize(
    "where",
    [
        {"kind": "value", "column": "orders.order_id", "values": ["o1"]},
        {"kind": "value", "column": "orders.tracking", "values": ["t1"]},
        {"any": [{"not": {"kind": "value", "column": "orders.order_id", "values": ["o1"]}}]},
        {
            "any": [
                {"kind": "value", "column": "orders.channel", "values": ["web"]},
                {"kind": "value", "column": "orders.tracking", "values": ["t1"]},
            ]
        },
        {
            "all": [
                {"kind": "value", "column": "orders.channel", "values": ["web"]},
                {"known": {"kind": "value", "column": "orders.order_id", "values": ["o1"]}},
            ]
        },
    ],
)
def test_a_variable_whose_where_tests_an_identifier_column_is_never_handed(
    check: Check, shop: Shop, where: dict[str, Any]
) -> None:
    counted = {"column": "orders.order_id", "aggregate": "count", "where": [where]}
    found = check(document({"measure": [counted]}), identified_shop(shop), analyses=tallies())
    assert refusals(found) == [("ROW_IDS_NOT_ALLOWED", "/views/0/params/columns/measure/0/where")]
    counted["where"] = [{"kind": "value", "column": "orders.channel", "values": ["web"]}]
    found = check(document({"measure": [counted]}), identified_shop(shop), analyses=tallies())
    assert refusals(found) == []


def test_a_key_column_s_rows_may_be_counted(analyse: Analyse, shop: Shop) -> None:
    [found] = analyse(document({"measure": [ORDERS]}), shop(), analyses=tallies())
    assert found.result.values.positions[0]["units"] > 0


@pytest.mark.parametrize(
    ("requirement", "missing"),
    [
        ({"role": "when", "kind": "endpoint", "min": 0}, []),
        ({"role": "day", "kind": "column", "datatype": "date"}, ["day"]),
        ({"role": "day", "kind": "column", "datatype": "date", "min": 0}, []),
    ],
)
@pytest.mark.parametrize("k", [None, 3])
def test_an_analysis_no_view_of_which_can_run_is_unavailable_naming_the_role(
    shop: Shop, requirement: dict[str, Any], missing: list[str], k: int | None
) -> None:
    release = joined_shop(shop)
    analyses = tallies(Echo(entry([*REQUIRES, requirement])))
    [item] = [
        a
        for a in analyses.applicable(
            list(release.descriptors), dataset=release.dataset, manifest=release.manifest, k=k
        )
        if a.analysis == "tallies.echo"
    ]
    named = [*missing, *([] if k is None else ["min_cell_count"])]
    assert item.status == ("unavailable" if named else "available")
    assert sorted(item.missing) == sorted(named)


def test_an_analysis_that_requires_an_endpoint_can_be_run_where_the_release_has_one(
    shop: Shop,
) -> None:
    requires = [*REQUIRES, {"role": "when", "kind": "endpoint", "on": "unit"}]
    assert packs_registry_unrun(requires) == []
    release = shop(survived="origin")
    found = {
        item.analysis: (item.status, item.missing)
        for item in tallies(Echo(entry(requires))).applicable(
            list(release.descriptors), dataset=release.dataset, manifest=release.manifest
        )
    }
    assert found["tallies.echo"] == ("available", [])


def packs_registry_unrun(requires: list[dict[str, Any]]) -> list[str]:
    registered = tallies(Echo(entry(requires))).get("tallies.echo")
    assert registered is not None
    return registry_module._unrun(registered)  # pyright: ignore[reportPrivateUsage]


def managers() -> Any:
    """People and their managers, people too: a column of the unit read through a lookup."""
    return build.release(
        [
            build.dataset(),
            build.table("people", ["person_id"]),
            build.column("people.person_id", "string"),
            build.column("people.manager_id", "string"),
            build.column("people.age", "integer"),
            build.relationship("people", ["manager_id"], "people", ["person_id"], role="manager"),
            build.coverage("rel:people.manager", "all"),
        ],
        {
            "people": [
                {"person_id": "p1", "manager_id": None, "age": 50},
                {"person_id": "p2", "manager_id": "p1", "age": 30},
            ]
        },
    )


def test_a_role_on_the_unit_takes_no_column_read_through_a_lookup(check: Check) -> None:
    requires = [{"role": "measure", "kind": "column", "on": "unit"}]
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "people",
        "cohorts": {"all": {"all": []}},
        "views": [
            {
                "analysis": "tallies.echo",
                "params": {
                    "columns": {
                        "measure": [
                            {
                                "column": "people.age",
                                "via": [{"rel": "rel:people.manager", "dir": "up"}],
                            }
                        ]
                    }
                },
            }
        ],
    }
    found = check(written, managers(), analyses=tallies(Echo(entry(requires))))
    assert refusals(found) == [("INVALID_VALUE", "/views/0/params/columns/measure/0/column")]
    written["views"][0]["params"]["columns"]["measure"][0].pop("via")
    assert check(written, managers(), analyses=tallies(Echo(entry(requires)))).refusals == []


def test_a_column_s_pack_leaves_share_the_document_s_budget_of_steps(
    check: Check, shop: Shop
) -> None:
    """Without a cohort's pack leaf, the budget is the base's alone unless a column's leaves are
    counted in it, and checking a leaf of 15,000 values takes more steps than that (D345)."""
    many = {**WEB_ORDERS, "where": [{"kind": "tallies.many", "xs": [0] * 15_000}]}
    found = check(document({"measure": [many]}), shop(), analyses=tallies())
    assert found.refusals == []


def test_the_call_s_deadline_is_looked_at_after_the_pack_s_values_are_read(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    counted: list[bool] = []
    counting = packs._analysed  # pyright: ignore[reportPrivateUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:
        counted.append(True)
        return counting(*args, **kwargs)

    monkeypatch.setattr(packs, "_analysed", spy)

    class Slow(dict[str, Any]):
        def items(self) -> Any:
            time.sleep(0.3)
            return super().items()

    ends = time.monotonic() + 0.2
    with pytest.raises(CallerDeadline):
        run_view(check, shop, Echo(entry(), lambda inputs: Slow(echoed(inputs))), ends=ends)
    assert counted == []


def test_the_call_s_deadline_is_looked_at_after_the_units_are_counted(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    counting = packs._analysed  # pyright: ignore[reportPrivateUsage]

    def slow(*args: Any, **kwargs: Any) -> Any:
        found = counting(*args, **kwargs)
        time.sleep(0.3)
        return found

    monkeypatch.setattr(packs, "_analysed", slow)
    with pytest.raises(CallerDeadline):
        run_view(check, shop, Echo(entry()), ends=time.monotonic() + 0.2)


def test_the_returns_schema_is_checked_by_the_call_s_deadline(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float | None] = []
    timed = packs.TimedBudget

    def spy(steps: int, ends: float | None) -> Any:
        seen.append(ends)
        return timed(steps, ends)

    monkeypatch.setattr(packs, "TimedBudget", spy)
    ends = time.monotonic() + 60
    run_view(check, shop, Echo(entry()), ends=ends)
    assert seen == [ends]


@pytest.mark.parametrize(("least", "refused"), [(0, False), (1, True), (None, True)])
def test_only_an_endpoint_a_view_must_meet_is_missing_from_a_release_without_one(
    check: Check, shop: Shop, least: int | None, refused: bool
) -> None:
    requirement: dict[str, Any] = {"role": "when", "kind": "endpoint"}
    if least is not None:
        requirement["min"] = least
    analyses = tallies(Echo(entry([*REQUIRES, requirement])))
    found = check(document(), shop(), analyses=analyses)
    expected = [("MISSING_MEMBER", "/views/0/params/endpoints/when")] if refused else []
    assert refusals(found) == expected


def test_counting_one_column_stops_at_the_call_s_deadline() -> None:
    values: tuple[tuple[Any, ...], ...] = ((1, 2),)
    one = Listed([("a",), ("b",)], [0, 1], values, ((frozenset(), frozenset()),), (frozenset(),))
    inputs = packs._inputs_of(one, 1, [])  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(CallerDeadline):
        packs._analysed(one.members, inputs, time.monotonic() - 1)  # pyright: ignore[reportPrivateUsage]
    found = packs._analysed(one.members, inputs, time.monotonic() + 60)  # pyright: ignore[reportPrivateUsage]
    assert found.n == 2


def test_the_inputs_hold_as_many_cells_as_a_listing_reads_members() -> None:
    """At 1,000,000 cells a listing ends within ``tool_seconds`` (D342, §14)."""
    assert MAX_INPUT_CELLS == MAX_LISTED == 1_000_000


def test_a_covered_leaf_whose_scope_is_an_identifier_column_is_never_handed(
    check: Check, shop: Shop, rows: Rows
) -> None:
    """A ``covered`` leaf's scope tests its columns' values as a value leaf does (D342)."""
    extras = [build.column("returns.tag", "string", identifier=True)]
    descriptors = [d for d in shop(extras=extras).descriptors if d.id != "cov:returns.order"]
    descriptors += [
        build.column("checked_orders.tag", "string"),
        build.coverage(
            "rel:returns.order",
            {
                "table": "checked_orders",
                "parent_columns": {"order_id": "order_id"},
                "scope_columns": {"tag": "tag"},
            },
        ),
    ]
    given = rows()
    for row in [*given["checked_orders"], *given["returns"]]:
        row["tag"] = "T1"
    release = build.release(descriptors, given)
    leaf = {"kind": "covered", "table": "returns", "scope": {"tag": ["T1"]}}
    counted = {"column": "orders.order_id", "aggregate": "count", "where": [leaf]}
    found = check(document({"measure": [counted]}), release, analyses=tallies())
    assert refusals(found) == [("ROW_IDS_NOT_ALLOWED", "/views/0/params/columns/measure/0/where")]
    unscoped = {**counted, "where": [{"kind": "covered", "table": "returns"}]}
    assert refusals(check(document({"measure": [unscoped]}), release, analyses=tallies())) == []


def test_a_type_that_says_it_equals_what_passes_is_the_pack_s_failure(
    check: Check, shop: Shop
) -> None:
    class Equal(type):
        def __eq__(cls, other: object) -> bool:
            return True

        def __hash__(cls) -> int:
            return hash(MemoryError)

    class SecretError(Exception, metaclass=Equal):
        def __init__(self, *given: object) -> None:
            super().__init__("c1 secret from init")

    def fail(inputs: AnalysisInputs) -> object:
        raise SecretError

    with pytest.raises(packs.PackFailed) as failed:
        run_view(check, shop, Echo(entry(), fail))
    assert failed.value.__context__ is None


def test_a_string_is_counted_by_its_own_length_whatever_it_says() -> None:
    class Short(str):
        def __len__(self) -> int:
            return 0

    for given in ({"s": Short("x" * 11)}, {Short("x" * 11): 0}):
        with pytest.raises(JsonTooLarge) as large:
            plain_json(given, values=10, characters=100, text=10)
        assert large.value.name == "text_characters"


def test_a_relationship_s_parent_columns_are_identifiers_even_off_the_parent_s_key() -> None:
    """``identifying`` reads a relationship's columns on both sides (§5.4), whatever the
    release's own checks hold of them."""
    release = build.release(
        [
            build.dataset(),
            build.table("people", ["person_id"]),
            build.column("people.person_id", "string"),
            build.column("people.badge", "string"),
            build.table("visits", ["visit_id"], role="event"),
            build.column("visits.visit_id", "string"),
            build.column("visits.badge", "string"),
            build.relationship("visits", ["badge"], "people", ["badge"], role="visitor"),
        ],
        {"people": [], "visits": []},
        check=False,
    )
    badge = release.column("people", "badge")
    assert badge is not None
    assert identifying(release, badge)


@pytest.mark.parametrize(
    ("analysis", "params", "at"),
    [
        ("compare.existence", "predicates", "/views/0/params/predicates/0"),
        ("survival.cox", "covariates", "/views/0/params/covariates/0/predicate"),
    ],
)
def test_more_pack_leaves_in_a_predicate_than_a_cohort_may_have_are_refused(
    check: Check, shop: Shop, monkeypatch: pytest.MonkeyPatch, analysis: str, params: str, at: str
) -> None:
    monkeypatch.setattr(views, "MAX_PACK_LEAVES", 1)
    clause = {"all": [{"kind": "tallies.web"}, {"kind": "tallies.web"}]}
    given: list[Any] = [clause] if analysis == "compare.existence" else [{"predicate": clause}]
    written = {
        "aibi": "1",
        "dataset": "d",
        "unit": "customers",
        "cohorts": {"all": {"all": []}},
        "views": [{"analysis": analysis, "cohorts": ["all"], "params": {params: given}}],
    }
    found = check(written, shop(survived="origin"), analyses=tallies())
    assert refusals(found) == [("LIMIT_EXCEEDED", at)]
    monkeypatch.setattr(views, "MAX_PACK_LEAVES", 2)
    assert check(written, shop(survived="origin"), analyses=tallies()).refusals == []
