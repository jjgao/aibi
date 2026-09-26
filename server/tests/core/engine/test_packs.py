"""Pack leaves, pack versions and caveat rules in canonicalisation (§7.3, §7.6, §10.1; D285–D287),
exercised with a test-only hygiene pack over the city's food-safety records."""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from pydantic import BaseModel, JsonValue

from aibi.core.engine import build, resolve
from aibi.core.engine.canonical import (
    CanonicalCohort,
    Canonicalisation,
    as_document,
    canonicalise,
)
from aibi.core.engine.counts import count_parts
from aibi.core.engine.data import Release
from aibi.core.engine.evaluate import evaluate
from aibi.core.schema.caveats import Severity
from aibi.core.schema.document import Clause, Document, IdsLeaf, PackLeaf
from aibi.core.schema.jsonschemas import STEPS_BASE
from aibi.core.schema.limits import MAX_PACK_LEAVES, MAX_VALUES
from aibi.core.schema.loading import load_document
from aibi.core.schema.output import DataSegment, Segment, data, text
from aibi.core.schema.pack_api import Pack, PackManifest, PackRegistry, Refused, ReleaseView
from aibi.core.schema.refusals import Limit, Refusal

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Canon = Callable[..., Canonicalisation]

CORE = "0.0.1"
THAI_ANY = {"kind": "value", "column": "establishments.cuisine", "values": ["thai"]}
SELF_REPORTED = "hygiene.SELF_REPORTED"


def severe(worst: int) -> dict[str, Any]:
    """What ``hygiene.clean`` means: no violation more severe than ``worst``."""
    return {
        "not": {
            "kind": "exists",
            "table": "violations",
            "where": [{"kind": "value", "column": "violations.severity", "range": {"gt": worst}}],
        }
    }


def _clauses(written: list[Any]) -> list[Clause]:
    """Clauses as a document holds them."""
    written_document = {
        "aibi": "1",
        "dataset": "d",
        "unit": "e",
        "cohorts": {"c": {"all": written}},
    }
    loaded = load_document(json.dumps(written_document))
    assert loaded.document is not None, loaded.refusals
    return list(loaded.document.cohorts["c"].all)


class Kind:
    """A leaf kind of the hygiene pack: ``expand`` gives the clauses for the leaf's members."""

    def __init__(
        self,
        expand: Callable[[Mapping[str, Any]], object],
        schema: Mapping[str, JsonValue] | None = None,
        summary: Callable[[PackLeaf], object] | None = None,
    ) -> None:
        self._expand = expand
        self._schema: Mapping[str, JsonValue] = schema or {"type": "object"}
        self._summary = summary
        self.calls: list[tuple[str, str]] = []

    @property
    def schema(self) -> Mapping[str, JsonValue]:
        return self._schema

    def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> Sequence[Clause]:
        self.calls.append((release.manifest, pack_version))
        return self._expand(leaf.model_extra or {})  # type: ignore[return-value]

    def summary(self, leaf: PackLeaf) -> Sequence[Segment]:
        if self._summary is not None:
            return self._summary(leaf)  # type: ignore[return-value]
        return [text("establishments with no violation above "), data(str(leaf.model_extra))]


def _refuse(members: Mapping[str, Any]) -> object:
    raise Refused(
        [
            Refusal.model_validate(
                {
                    "code": "hygiene.NO_SUCH_GRADE",
                    "path": "/worst",
                    "message": [{"text": "grades run from 1 to 5"}],
                }
            )
        ]
    )


CLEAN_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "kind": {"const": "hygiene.clean"},
        "worst": {"type": "integer", "minimum": 0},
    },
    "required": ["kind", "worst"],
    "additionalProperties": False,
}


STRING: dict[str, JsonValue] = {"type": "string"}
ANY_OF: list[JsonValue] = [*([STRING] * 30), {"type": "integer"}]
COSTLY_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"xs": {"type": "array", "items": {"anyOf": ANY_OF}}},
}
"""Each item of ``xs`` costs some 30 steps, more than the 8 each JSON value brings."""


def _raises(members: Mapping[str, Any]) -> object:
    raise RuntimeError(f"cannot expand {members}")


def _greedy(members: Mapping[str, Any]) -> object:
    """Changes the leaf it is given before expanding it."""
    worst = members["worst"]
    members["worst"] = 999  # type: ignore[index]
    return _clauses([severe(worst)])


def _sized(members: Mapping[str, Any]) -> object:
    names = [f"{members['tag']}-{index}" for index in range(members["n"])]
    return _clauses([{"kind": "value", "column": "establishments.name", "values": names}])


def kinds() -> dict[str, Kind]:
    return {
        "hygiene.clean": Kind(lambda m: _clauses([severe(m["worst"])]), CLEAN_SCHEMA),
        "hygiene.nothing": Kind(lambda m: []),
        "hygiene.ids": Kind(lambda m: [IdsLeaf.model_validate({"kind": "ids", "ids": ["d:e1"]})]),
        "hygiene.nested_pack": Kind(lambda m: _clauses([{"not": clean(1)}])),
        "hygiene.not_a_list": Kind(lambda m: {"kind": "value"}),
        "hygiene.dicts": Kind(lambda m: [severe(1)]),
        "hygiene.refuses": Kind(_refuse),
        "hygiene.wrong_column": Kind(
            lambda m: _clauses([{"kind": "value", "column": "violations.colour", "values": ["x"]}])
        ),
        "hygiene.big": Kind(
            lambda m: _clauses(
                [
                    {
                        "kind": "value",
                        "column": "establishments.name",
                        "values": [f"{m['n']}-{index}" for index in range(10_000)],
                    }
                ]
            )
        ),
        "hygiene.costly": Kind(lambda m: [], COSTLY_SCHEMA),
        "hygiene.bad_summary": Kind(lambda m: [], summary=lambda leaf: ["plain text"]),
        "hygiene.long_summary": Kind(lambda m: [], summary=lambda leaf: [text("x")] * 65),
        "hygiene.raises": Kind(_raises),
        "hygiene.raising_summary": Kind(lambda m: [], summary=lambda leaf: _raises({})),
        "hygiene.greedy": Kind(_greedy),
        "hygiene.sized": Kind(_sized),
        "hygiene.dollar": Kind(
            lambda m: _clauses(
                [{"kind": "value", "column": "establishments.name", "values": ["$$x", "$$$"]}]
            )
        ),
    }


def _rule(release: ReleaseView, form: Mapping[str, JsonValue]) -> Sequence[str]:
    return [SELF_REPORTED] if "violations" in str(form) else []


def hygiene(
    *,
    version: str = "1.2.0",
    results_version: int = 3,
    rule: Callable[[ReleaseView, Mapping[str, JsonValue]], Sequence[str]] | None = _rule,
    leaf_kinds: Mapping[str, Kind] | None = None,
) -> Pack:
    return Pack(
        manifest=PackManifest(
            id="hygiene", version=version, results_version=results_version, requires_core=">=0"
        ),
        leaf_kinds=dict(leaf_kinds if leaf_kinds is not None else kinds()),
        caveat_codes={SELF_REPORTED: Severity.WARN},
        caveat_rule=rule,
    )


def registry(pack: Pack | None = None) -> PackRegistry:
    return PackRegistry([pack or hygiene()], core_version=CORE)


def only(result: Canonicalisation) -> CanonicalCohort:
    assert result.refusals == [], result.refusals
    [cohort] = result.cohorts.values()
    return cohort


def refusals(result: Canonicalisation) -> list[tuple[str, str | None]]:
    return [(refusal.code, refusal.path) for refusal in result.refusals]


def clean(worst: int = 3) -> dict[str, Any]:
    return {"kind": "hygiene.clean", "worst": worst}


def with_packs(release: Release, packs: list[str]) -> Release:
    descriptors = [d for d in release.descriptors if d.id != "dataset"]
    return build.release([build.dataset(packs=packs), *descriptors], {})


def test_a_pack_leaf_and_its_expansion_written_out_have_one_form_and_one_id(
    canon: Canon, city: City, doc: Doc
) -> None:
    packs = registry()
    expanded = only(canon(doc([clean(3)]), city(), registry=packs))
    written = only(canon(doc([severe(3)]), city(), registry=packs))
    assert expanded.form == written.form
    assert expanded.identity.packs == {"hygiene": 3}
    assert written.identity.packs == {}
    assert expanded.id != written.id  # the pack's results version is hashed (§7.6)
    assert expanded.resolved.packs == frozenset({"hygiene"})


def test_a_pack_leaf_is_one_leaf_as_written(canon: Canon, city: City, doc: Doc) -> None:
    cohort = only(canon(doc([clean(2), {"all": [clean(4)]}]), city(), registry=registry()))
    first, second = cohort.keys
    assert cohort.leaves == {"/cohorts/c/all/0": (first,), "/cohorts/c/all/1/all/0": (second,)}


def test_an_empty_expansion_is_every_row(canon: Canon, city: City, doc: Doc) -> None:
    packs = registry()
    cohort = only(canon(doc([{"kind": "hygiene.nothing"}, THAI_ANY]), city(), registry=packs))
    assert cohort.form == only(canon(doc([{"all": []}, THAI_ANY]), city(), registry=packs)).form
    assert cohort.form == only(canon(doc([THAI_ANY]), city(), registry=packs)).form


def test_a_pack_leaf_inside_a_where_expands_where_it_is(canon: Canon, city: City, doc: Doc) -> None:
    written = doc([{"kind": "exists", "table": "inspections", "where": [clean(1)]}])
    inline = doc([{"kind": "exists", "table": "inspections", "where": [severe(1)]}])
    packs = registry()
    assert (
        only(canon(written, city(), registry=packs)).form
        == only(canon(inline, city(), registry=packs)).form
    )


def test_the_results_version_is_hashed_and_the_version_is_not(
    canon: Canon, city: City, doc: Doc
) -> None:
    ids = {
        (version, results): only(
            canon(
                doc([clean()]),
                city(),
                registry=registry(hygiene(version=version, results_version=results)),
            )
        ).id
        for version, results in (("1.2.0", 3), ("1.2.1", 3), ("1.3.0", 4))
    }
    assert ids["1.2.0", 3] == ids["1.2.1", 3]
    assert ids["1.2.0", 3] != ids["1.3.0", 4]
    cohort = only(canon(doc([clean()]), city(), registry=registry(hygiene(version="1.2.1"))))
    assert cohort.packs["hygiene"].model_dump() == {"version": "1.2.1", "results_version": 3}


def test_a_leaf_its_kind_s_schema_refuses_is_refused_where_it_fails(
    canon: Canon, city: City, doc: Doc
) -> None:
    result = canon(
        doc([{"kind": "hygiene.clean", "worst": -1, "extra": 1}]), city(), registry=registry()
    )
    assert refusals(result) == [
        ("INVALID_VALUE", "/cohorts/c/all/0"),
        ("INVALID_VALUE", "/cohorts/c/all/0/worst"),
    ]
    assert result.cohorts == {}
    missing = canon(doc([{"kind": "hygiene.clean"}]), city(), registry=registry())
    assert refusals(missing) == [("INVALID_VALUE", "/cohorts/c/all/0")]


def test_a_leaf_s_schema_failure_in_a_parameter_points_at_the_reference(
    canon: Canon, city: City, doc: Doc
) -> None:
    written = doc(["$leaf"], params={"leaf": {"kind": "hygiene.clean", "worst": "bad"}})
    result = canon(written, city(), registry=registry())
    assert refusals(result) == [("INVALID_VALUE", "/cohorts/c/all/0")]
    assert "leaf" in str(result.refusals[0].message)


def test_an_unknown_leaf_kind_is_refused_listing_the_installed_kinds(
    canon: Canon, city: City, doc: Doc
) -> None:
    for kind, packs in (
        ("hygiene.unheard_of", registry()),
        ("elsewhere.clean", registry()),
        ("hygiene.clean", None),
    ):
        result = canon(doc([{"kind": kind, "worst": 1}]), city(), registry=packs)
        [refusal] = result.refusals
        assert (refusal.code, refusal.path) == ("UNKNOWN_KIND", "/cohorts/c/all/0/kind")
        listed = [
            segment.data for segment in refusal.alternatives if isinstance(segment, DataSegment)
        ]
        assert listed == ([] if packs is None else sorted(kinds()))


def test_a_compiler_s_refusals_are_placed_below_the_leaf(
    canon: Canon, city: City, doc: Doc
) -> None:
    result = canon(doc([{"kind": "hygiene.refuses", "worst": 9}]), city(), registry=registry())
    assert refusals(result) == [("hygiene.NO_SUCH_GRADE", "/cohorts/c/all/0/worst")]


@pytest.mark.parametrize(
    "kind", ["hygiene.ids", "hygiene.nested_pack", "hygiene.not_a_list", "hygiene.dicts"]
)
def test_what_a_compiler_gives_that_is_no_expansion_is_refused(
    canon: Canon, city: City, doc: Doc, kind: str
) -> None:
    result = canon(doc([{"kind": kind}]), city(), registry=registry())
    assert refusals(result) == [("PACK_FAILED", "/cohorts/c/all/0")]


def test_refusals_inside_an_expansion_are_placed_at_the_pack_leaf(
    canon: Canon, city: City, doc: Doc
) -> None:
    result = canon(doc([THAI_ANY, {"kind": "hygiene.wrong_column"}]), city(), registry=registry())
    [refusal] = result.refusals
    assert (refusal.code, refusal.path) == ("UNKNOWN_COLUMN", "/cohorts/c/all/1")
    assert refusal.message[:2] == [
        text("In the expansion of the pack leaf "),
        data("hygiene.wrong_column"),
    ]


def test_a_pack_leaf_is_compiled_once_per_distinct_leaf_and_release(
    canon: Canon, city: City, doc: Doc
) -> None:
    found = kinds()
    packs = registry(hygiene(leaf_kinds=found))
    written = doc({"a": [clean(1), {"not": clean(1)}], "b": [clean(1), clean(2)]})
    assert canon(written, city(), registry=packs).refusals == []
    assert len(found["hygiene.clean"].calls) == 2
    assert {call[1] for call in found["hygiene.clean"].calls} == {"1.2.0"}


def test_the_pack_s_summary_is_kept_beside_the_form_and_outside_the_id(
    canon: Canon, city: City, doc: Doc
) -> None:
    cohort = only(canon(doc([clean(3)]), city(), registry=registry()))
    assert cohort.summaries == {
        "/cohorts/c/all/0": (text("establishments with no violation above "), data("{'worst': 3}"))
    }
    assert b"establishments with" not in str(cohort.identity.hashed()).encode()


@pytest.mark.parametrize("kind", ["hygiene.bad_summary", "hygiene.long_summary"])
def test_a_summary_that_is_not_a_short_list_of_segments_is_refused(
    canon: Canon, city: City, doc: Doc, kind: str
) -> None:
    result = canon(doc([{"kind": kind}]), city(), registry=registry())
    assert refusals(result) == [("PACK_FAILED", "/cohorts/c/all/0")]


def test_the_document_s_packs_are_installed_at_versions_their_specifiers_admit(
    canon: Canon, city: City, doc: Doc
) -> None:
    unknown = canon(doc([], packs={"archive": ">=1"}), city(), registry=registry())
    assert refusals(unknown) == [("PACK_UNAVAILABLE", "/packs/archive")]
    assert [s.data for s in unknown.refusals[0].alternatives if isinstance(s, DataSegment)] == [
        "hygiene 1.2.0"
    ]
    too_new = canon(doc([clean()], packs={"hygiene": ">=2"}), city(), registry=registry())
    assert refusals(too_new) == [("PACK_UNAVAILABLE", "/packs/hygiene")]
    admitted = canon(doc([clean()], packs={"hygiene": ">=1.2,<2"}), city(), registry=registry())
    assert admitted.refusals == []
    pre = registry(hygiene(version="1.3.0rc1"))
    assert canon(doc([clean()], packs={"hygiene": ">=1.2"}), city(), registry=pre).refusals == []


def test_the_packs_a_dataset_lists_are_installed_and_hashed_into_its_ids(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = with_packs(city(), ["hygiene"])
    missing = canon(doc([THAI_ANY]), release)
    assert refusals(missing) == [("PACK_UNAVAILABLE", "/dataset")]
    cohort = only(canon(doc([THAI_ANY]), release, registry=registry()))
    assert cohort.identity.packs == {"hygiene": 3}


def test_caveat_rules_raise_declared_codes_into_the_count(
    canon: Canon, city: City, doc: Doc
) -> None:
    cohort = only(canon(doc([clean()]), city(), registry=registry()))
    assert [(c.code, c.severity, c.pack) for c in cohort.caveats] == [
        (SELF_REPORTED, Severity.WARN, "hygiene")
    ]
    parts = count_parts(cohort, evaluate(cohort.resolved))
    raised = [c for c in parts.caveats if c.code == SELF_REPORTED]
    assert [(c.severity, c.affects) for c in raised] == [(Severity.WARN, ["/population"])]
    quiet = only(canon(doc([clean()]), city(), registry=registry(hygiene(rule=None))))
    assert quiet.caveats == ()
    assert count_parts(quiet, evaluate(quiet.resolved)).digest != parts.digest


def test_a_caveat_rule_that_gives_undeclared_codes_refuses_the_cohort(
    canon: Canon, city: City, doc: Doc
) -> None:
    for given in (["hygiene.UNDECLARED"], ["SMALL_N"], "hygiene.SELF_REPORTED", [3]):
        rule = lambda release, form, given=given: given  # noqa: E731
        result = canon(doc([clean()]), city(), registry=registry(hygiene(rule=rule)))  # type: ignore[arg-type]
        assert refusals(result) == [("PACK_FAILED", "/cohorts/c/all")]
        assert result.cohorts == {}


def test_a_caveat_rule_reads_a_copy_of_the_form(canon: Canon, city: City, doc: Doc) -> None:
    def rule(release: ReleaseView, form: Mapping[str, JsonValue]) -> Sequence[str]:
        form.clear()  # type: ignore[attr-defined]
        return []

    cohort = only(canon(doc([clean()]), city(), registry=registry(hygiene(rule=rule))))
    assert cohort.form != {}


def test_the_expansions_of_a_document_hold_at_most_as_many_values_as_a_document(
    canon: Canon, city: City, doc: Doc
) -> None:
    leaves = [{"any": [{"kind": "hygiene.big", "n": n}]} for n in range(21)]
    result = canon(doc({"a": leaves[:10], "b": leaves[10:]}), city(), registry=registry())
    limits = {(r.code, r.limit.name if r.limit else None) for r in result.refusals}
    assert limits == {("LIMIT_EXCEEDED", "expansion_values")}


def test_checking_a_document_s_pack_leaves_shares_one_budget_of_steps(
    canon: Canon, city: City, doc: Doc
) -> None:
    costly = {"kind": "hygiene.costly", "xs": list(range(2_000))}
    result = canon(doc([costly]), city(), registry=registry())
    assert [(r.code, r.limit.name if r.limit else None) for r in result.refusals] == [
        ("LIMIT_EXCEEDED", "pack_leaf_steps")
    ]
    cheap = {"kind": "hygiene.costly", "xs": list(range(20))}
    assert canon(doc([cheap]), city(), registry=registry()).refusals == []


def test_a_cohort_as_written_holds_at_most_256_pack_leaves(doc: Doc) -> None:
    leaves = [{"kind": "hygiene.clean", "worst": n} for n in range(MAX_PACK_LEAVES + 1)]
    written = doc([{"all": leaves[:128]}, {"any": leaves[128:]}])
    loaded = load_document(json.dumps(written))
    assert [(r.code, r.path, r.limit.name if r.limit else None) for r in loaded.refusals] == [
        ("LIMIT_EXCEEDED", "/cohorts/c/all", "pack_leaves")
    ]


def test_a_compiler_that_raises_is_refused_at_its_leaf_without_its_message(
    canon: Canon, city: City, doc: Doc
) -> None:
    written = doc({"a": [{"kind": "hygiene.raises", "secret": "s-3"}], "b": [clean()]})
    result = canon(written, city(), registry=registry())
    assert refusals(result) == [("PACK_FAILED", "/cohorts/a/all/0")]
    assert result.refusals[0].message == [text("The pack's compiler of this leaf failed")]
    assert "s-3" not in result.refusals[0].model_dump_json()
    assert set(result.cohorts) == {"b"}


def test_a_summary_or_caveat_rule_that_raises_is_refused_as_pack_failed(
    canon: Canon, city: City, doc: Doc
) -> None:
    summary = canon(doc([{"kind": "hygiene.raising_summary"}]), city(), registry=registry())
    assert refusals(summary) == [("PACK_FAILED", "/cohorts/c/all/0")]

    def rule(release: ReleaseView, form: Mapping[str, JsonValue]) -> Sequence[str]:
        raise KeyError(str(form))

    ruled = canon(doc([clean()]), city(), registry=registry(hygiene(rule=rule)))
    assert refusals(ruled) == [("PACK_FAILED", "/cohorts/c/all")]
    assert "violations" not in ruled.refusals[0].model_dump_json()


def _canonicalised(written: Document, release: Release, positions: Any) -> CanonicalCohort:
    return only(
        canonicalise(
            written,
            {"d": release},
            labels={release.manifest: 1},
            registry=registry(),
            positions=positions,
        )
    )


def test_a_compiler_that_changes_its_leaf_changes_neither_the_document_nor_later_readers(
    city: City, doc: Doc
) -> None:
    loaded = load_document(json.dumps(doc([{"kind": "hygiene.greedy", "worst": 3}])))
    assert loaded.document is not None
    release = city()
    first, second = (_canonicalised(loaded.document, release, loaded.positions) for _ in range(2))
    leaf = loaded.document.cohorts["c"].all[0]
    assert isinstance(leaf, PackLeaf)
    assert leaf.model_extra == {"worst": 3}
    assert second.summaries["/cohorts/c/all/0"][1] == data("{'worst': 3}")
    assert first.form == second.form
    inline = load_document(json.dumps(doc([severe(3)])))
    assert inline.document is not None
    assert first.form == _canonicalised(inline.document, release, inline.positions).form


def test_packs_read_no_release_label_so_a_draft_and_its_release_share_ids_and_caveats(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = city()
    cohorts = [
        only(canon(doc([clean()]), release, labels={release.manifest: label}, registry=registry()))
        for label in (1, "draft")
    ]
    assert cohorts[0].id == cohorts[1].id
    assert cohorts[0].caveats == cohorts[1].caveats
    digests = [count_parts(c, evaluate(c.resolved)).digest for c in cohorts]
    assert digests[0] == digests[1]

    def by_label(view: ReleaseView, form: Mapping[str, JsonValue]) -> Sequence[str]:
        return [SELF_REPORTED] if view.label == "draft" else []

    for label in (1, "draft"):
        result = canon(
            doc([clean()]),
            release,
            labels={release.manifest: label},
            registry=registry(hygiene(rule=by_label)),
        )
        assert refusals(result) == [("PACK_FAILED", "/cohorts/c/all")]


def test_a_caveat_rule_that_changes_the_inside_of_its_copy_leaves_the_form_as_it_was(
    canon: Canon, city: City, doc: Doc
) -> None:
    def rule(release: ReleaseView, form: Mapping[str, JsonValue]) -> Sequence[str]:
        for tree in form.values():
            cast_tree: dict[str, Any] = tree  # type: ignore[assignment]
            cast_tree.clear()
        return []

    quiet = only(canon(doc([clean()]), city(), registry=registry(hygiene(rule=None))))
    changing = only(canon(doc([clean()]), city(), registry=registry(hygiene(rule=rule))))
    assert changing.form == quiet.form
    assert changing.identity.hashed() == quiet.identity.hashed()
    assert changing.id == quiet.id


def test_a_cohort_that_references_one_with_a_pack_leaf_hashes_the_pack_s_results_version(
    canon: Canon, city: City, doc: Doc
) -> None:
    result = canon(
        doc({"base": [clean()], "rest": [{"kind": "cohort", "cohort": "base"}]}),
        city(),
        registry=registry(),
    )
    assert result.refusals == []
    base, rest = result.cohorts["base"], result.cohorts["rest"]
    assert rest.identity.packs == {"hygiene": 3}
    assert rest.id == base.id


def _sized_values(n: int) -> int:
    """The JSON values ``hygiene.sized`` gives for ``n`` names, as the expansion's JSON holds
    them."""
    [clause] = _clauses([{"kind": "value", "column": "establishments.name", "values": ["x"] * n}])
    pending: list[Any] = [[clause.model_dump(mode="json", by_alias=True)]]
    count = 0
    while pending:
        current = pending.pop()
        count += 1
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return count


def test_the_expansions_of_a_document_may_hold_exactly_as_many_values_as_a_document(
    canon: Canon, city: City, doc: Doc
) -> None:
    full = _sized_values(10_000)
    last = MAX_VALUES - 19 * full - (_sized_values(1) - 1)
    for extra, refused in ((0, False), (1, True)):
        sizes = [10_000] * 19 + [last + extra]
        leaves = [
            {"any": [{"kind": "hygiene.sized", "n": n, "tag": f"t{index}"}]}
            for index, n in enumerate(sizes)
        ]
        assert sum(_sized_values(n) for n in sizes) == MAX_VALUES + extra
        result = canon(doc({"a": leaves[:10], "b": leaves[10:]}), city(), registry=registry())
        limits = [r.limit.name for r in result.refusals if r.limit]
        assert limits == (["expansion_values"] if refused else [])


def test_the_step_budget_of_a_document_s_pack_leaves_is_capped_as_an_operator_s_write_is(
    canon: Canon, city: City, doc: Doc, monkeypatch: pytest.MonkeyPatch
) -> None:
    costly = {"kind": "hygiene.costly", "xs": list(range(100))}
    assert canon(doc([costly]), city(), registry=registry()).refusals == []
    monkeypatch.setattr(resolve, "WRITE_STEPS_MAX", STEPS_BASE // 10)
    result = canon(doc([costly]), city(), registry=registry())
    assert [(r.code, r.limit) for r in result.refusals if r.limit] == [
        ("LIMIT_EXCEEDED", Limit(name="pack_leaf_steps", max=STEPS_BASE // 10))
    ]


def test_an_expansion_s_strings_that_start_with_a_dollar_read_back_escaped(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = city()
    cohort = only(canon(doc([{"kind": "hygiene.dollar"}]), release, registry=registry()))
    assert cohort.clauses == (
        {"kind": "value", "column": "establishments.name", "values": ["$$", "$x"]},
    )
    written = [as_document(clause, {release.manifest: "d"}) for clause in cohort.clauses]
    assert written[0]["values"] == ["$$$", "$$x"]  # type: ignore[index]
    again = only(canon(doc(written), release, registry=registry()))
    assert again.form == cohort.form


def _meddling(release: ReleaseView, form: Mapping[str, JsonValue]) -> Sequence[str]:
    """Changes a descriptor of the release it is shown."""
    fields: Any = release.descriptors["violations.severity"].fields
    fields.missing_codes["pending"] = "UNKNOWN"
    return []


def test_a_caveat_rule_that_changes_a_descriptor_changes_neither_the_release_nor_a_digest(
    canon: Canon, city: City, doc: Doc
) -> None:
    release = city()
    before = release.by_id["violations.severity"].model_dump_json()
    written = doc([clean()])
    quiet = only(canon(written, release, registry=registry(hygiene(rule=None))))
    digest = count_parts(quiet, evaluate(quiet.resolved)).digest
    only(canon(written, release, registry=registry(hygiene(rule=_meddling))))
    assert release.by_id["violations.severity"].model_dump_json() == before
    again = only(canon(written, release, registry=registry(hygiene(rule=None))))
    assert (again.id, count_parts(again, evaluate(again.resolved)).digest) == (quiet.id, digest)


def test_a_pack_cannot_replace_a_descriptor_of_the_release_it_is_shown(
    canon: Canon, city: City, doc: Doc
) -> None:
    def rule(release: ReleaseView, form: Mapping[str, JsonValue]) -> Sequence[str]:
        descriptors: Any = release.descriptors
        descriptors["violations.severity"] = None
        return []

    release = city()
    result = canon(doc([clean()]), release, registry=registry(hygiene(rule=rule)))
    assert refusals(result) == [("PACK_FAILED", "/cohorts/c/all")]
    assert release.by_id["violations.severity"] is not None


def test_a_compiler_that_changes_a_descriptor_leaves_the_release_as_it_was(
    canon: Canon, city: City, doc: Doc
) -> None:
    def expand(members: Mapping[str, Any]) -> object:
        return _clauses([severe(members["worst"])])

    class Meddling(Kind):
        def compile(
            self, leaf: PackLeaf, release: ReleaseView, pack_version: str
        ) -> Sequence[Clause]:
            _meddling(release, {})
            return super().compile(leaf, release, pack_version)

    release = city()
    before = release.by_id["violations.severity"].model_dump_json()
    meddling = {**kinds(), "hygiene.clean": Meddling(expand, CLEAN_SCHEMA)}
    changing = only(canon(doc([clean()]), release, registry=registry(hygiene(leaf_kinds=meddling))))
    assert release.by_id["violations.severity"].model_dump_json() == before
    assert changing.id == only(canon(doc([clean()]), release, registry=registry())).id


def _exhausted(*given: object) -> Any:
    raise MemoryError


@pytest.mark.parametrize("stage", ["compiler", "summary", "caveat rule"])
def test_a_pack_that_runs_out_of_memory_is_not_blamed_for_it(
    canon: Canon, city: City, doc: Doc, stage: str
) -> None:
    leaf_kinds = {
        **kinds(),
        "hygiene.exhausted": Kind(_exhausted if stage == "compiler" else lambda m: []),
        "hygiene.exhausted_summary": Kind(lambda m: [], summary=_exhausted),
    }
    pack = hygiene(leaf_kinds=leaf_kinds, rule=_exhausted if stage == "caveat rule" else _rule)
    leaf = {"kind": "hygiene.exhausted_summary" if stage == "summary" else "hygiene.exhausted"}
    with pytest.raises(MemoryError):
        canon(doc([leaf]), city(), registry=registry(pack))


def test_a_pack_whose_recursion_runs_too_deep_is_refused_as_pack_failed(
    canon: Canon, city: City, doc: Doc
) -> None:
    def deep(members: Mapping[str, Any]) -> object:
        return deep(members)

    pack = hygiene(leaf_kinds={**kinds(), "hygiene.deep": Kind(deep)})
    result = canon(doc([{"kind": "hygiene.deep"}]), city(), registry=registry(pack))
    assert refusals(result) == [("PACK_FAILED", "/cohorts/c/all/0")]


def test_a_pack_s_view_copies_a_descriptor_only_when_it_is_read_and_once(
    city: City, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = city()
    copied: list[str] = []
    original = BaseModel.model_copy

    def counting(self: BaseModel, *given: Any, **options: Any) -> Any:
        copied.append(str(getattr(self, "id", "")))
        return original(self, *given, **options)

    monkeypatch.setattr(BaseModel, "model_copy", counting)
    view = resolve.PackView.of(release)
    assert copied == []
    assert len(view.descriptors) == len(release.by_id)
    assert list(view.descriptors) == list(release.by_id)
    assert "violations.severity" in view.descriptors
    assert "violations.colour" not in view.descriptors
    first = view.descriptors["violations.severity"]
    assert view.descriptors["violations.severity"] is first
    assert first is not release.by_id["violations.severity"]
    assert first == release.by_id["violations.severity"]
    assert copied == ["violations.severity"]


@pytest.mark.parametrize(("rule", "views"), [(None, 0), (_rule, 1)])
def test_a_view_is_made_for_a_cohort_s_caveat_rules_only_when_a_pack_has_one(
    canon: Canon,
    city: City,
    doc: Doc,
    monkeypatch: pytest.MonkeyPatch,
    rule: Any,
    views: int,
) -> None:
    made: list[str] = []
    original = resolve.PackView.of

    def counting(release: Release) -> resolve.PackView:
        made.append(release.manifest)
        return original(release)

    monkeypatch.setattr(resolve.PackView, "of", counting)
    release = with_packs(city(), ["hygiene"])
    only(canon(doc([THAI_ANY]), release, registry=registry(hygiene(rule=rule))))
    assert len(made) == views


def test_a_cohort_s_expansion_never_depends_on_what_a_sibling_s_compiler_did_to_its_view(
    canon: Canon, city: City, doc: Doc
) -> None:
    class Meddling(Kind):
        def compile(
            self, leaf: PackLeaf, release: ReleaseView, pack_version: str
        ) -> Sequence[Clause]:
            fields: Any = release.descriptors["violations.severity"].fields
            fields.missing_codes["zzz"] = "UNKNOWN"
            return super().compile(leaf, release, pack_version)

    def reading(release: ReleaseView) -> list[Clause]:
        fields: Any = release.descriptors["violations.severity"].fields
        return _clauses([severe(1 if "zzz" in fields.missing_codes else 2)])

    class Reading(Kind):
        def compile(
            self, leaf: PackLeaf, release: ReleaseView, pack_version: str
        ) -> Sequence[Clause]:
            return reading(release)

    leaf_kinds = {
        **kinds(),
        "hygiene.meddling": Meddling(lambda m: []),
        "hygiene.reading": Reading(lambda m: []),
    }
    packs = registry(hygiene(leaf_kinds=leaf_kinds))
    release = city()
    alone = canon(doc({"b": [{"kind": "hygiene.reading"}]}), release, registry=packs)
    siblings = canon(
        doc({"a": [{"kind": "hygiene.meddling"}], "b": [{"kind": "hygiene.reading"}]}),
        release,
        registry=packs,
    )
    assert siblings.refusals == []
    assert siblings.cohorts["b"].form == alone.cohorts["b"].form
    assert siblings.cohorts["b"].id == alone.cohorts["b"].id


def test_a_pack_s_view_holds_no_attribute_that_reaches_the_release_s_descriptors(
    city: City,
) -> None:
    release = city()
    copies = resolve.PackView.of(release).descriptors
    assert isinstance(copies, resolve.DescriptorCopies)
    held = [getattr(copies, name) for name in resolve.DescriptorCopies.__slots__]
    assert not hasattr(copies, "__dict__")
    assert all(value is not release.by_id for value in held)
    assert not any(isinstance(value, Mapping) for value in held)


def _marking(mark: str, other: str, code: str) -> Callable[..., Sequence[str]]:
    """A caveat rule that marks a descriptor of its view and raises ``code`` if it finds the
    other rule's mark there."""

    def rule(release: ReleaseView, form: Mapping[str, JsonValue]) -> Sequence[str]:
        fields: Any = release.descriptors["violations.severity"].fields
        seen = other in fields.missing_codes
        fields.missing_codes[mark] = "UNKNOWN"
        return [code] if seen else []

    return rule


def test_each_pack_s_caveat_rule_reads_a_view_of_its_own(
    canon: Canon, city: City, doc: Doc
) -> None:
    def pack(name: str, other: str) -> Pack:
        return Pack(
            manifest=PackManifest(id=name, version="1.0.0", results_version=1, requires_core=">=0"),
            leaf_kinds={},
            caveat_codes={f"{name}.SEEN": Severity.WARN},
            caveat_rule=_marking(name, other, f"{name}.SEEN"),
        )

    packs = PackRegistry([pack("alpha", "beta"), pack("beta", "alpha")], core_version=CORE)
    release = with_packs(city(), ["alpha", "beta"])
    assert only(canon(doc([THAI_ANY]), release, registry=packs)).caveats == ()
