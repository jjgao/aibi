"""The guard at the sites canonicalisation calls a pack at (D285, D287, D388): a leaf compiler, a
summary and a caveat rule, each against every kind of raise (``raisers``); the caveat rule's twin
of #78's sibling-compiler test; and a view of its own per pack."""

from collections.abc import Callable, Mapping
from typing import Any

import pytest
from pydantic import JsonValue
from tests.core import raisers
from tests.core.engine.test_packs import (
    THAI_ANY,
    Kind,
    clean,
    hygiene,
    kinds,
    only,
    refusals,
    registry,
    severe,
    with_packs,
)

from aibi.core.engine.canonical import Canonicalisation
from aibi.core.engine.data import Release
from aibi.core.schema.caveats import Severity
from aibi.core.schema.document import Clause, PackLeaf
from aibi.core.schema.pack_api import CaveatRule, Pack, PackManifest, PackRegistry, ReleaseView

City = Callable[..., Release]
Doc = Callable[..., dict[str, Any]]
Canon = Callable[..., Canonicalisation]
CORE = "0.0.1"


class _Raising(Kind):
    def __init__(self, raise_: Callable[..., Any], *, summary: bool = False) -> None:
        super().__init__(lambda m: [])
        self._raise = raise_
        self._summarising = summary

    def compile(
        self, leaf: PackLeaf, release: ReleaseView, pack_version: str
    ) -> list[Clause] | tuple[Clause, ...]:
        if not self._summarising:
            self._raise()
        return []

    def summary(self, leaf: PackLeaf) -> Any:
        if self._summarising:
            self._raise()
        return []


def _leaf_registry(raise_: Callable[..., Any], *, summary: bool) -> PackRegistry:
    leaf_kinds = {**kinds(), "hygiene.raising": _Raising(raise_, summary=summary)}
    return registry(hygiene(leaf_kinds=leaf_kinds))


def _rule_registry(raise_: Callable[..., Any]) -> PackRegistry:
    return registry(hygiene(rule=raise_))


_SITES = ("compiler", "summary", "rule")


def _canon(site: str, raise_: Callable[..., Any], canon: Canon, city: City, doc: Doc) -> Any:
    if site == "rule":
        return canon(doc([clean()]), city(), registry=_rule_registry(raise_))
    written = doc({"a": [{"kind": "hygiene.raising"}], "b": [clean()]})
    return canon(written, city(), registry=_leaf_registry(raise_, summary=site == "summary"))


@pytest.mark.parametrize("site", _SITES)
@pytest.mark.parametrize("kind", list(raisers.FAILURES))
def test_whatever_a_compiler_summary_or_rule_raises_is_pack_failed_and_quoted_nowhere(
    canon: Canon,
    city: City,
    doc: Doc,
    site: str,
    kind: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = _canon(site, raisers.raising(raisers.FAILURES[kind]), canon, city, doc)
    found = refusals(result)
    assert [code for code, _ in found] == ["PACK_FAILED"]
    assert found[0][1] == ("/cohorts/c/all" if site == "rule" else "/cohorts/a/all/0")
    assert raisers.SECRET not in result.refusals[0].model_dump_json()
    assert raisers.SECRET not in caplog.text
    if site != "rule":
        assert set(result.cohorts) == {"b"}


@pytest.mark.parametrize("site", _SITES)
@pytest.mark.parametrize("kind", list(raisers.PASSING))
def test_a_passed_type_a_compiler_summary_or_rule_raises_passes_anew(
    canon: Canon, city: City, doc: Doc, site: str, kind: str
) -> None:
    passed = raisers.PASSING[kind]
    with pytest.raises(passed) as raised:
        _canon(site, raisers.passing(passed), canon, city, doc)
    raisers.passed_anew(raised.value, passed)


# --- The caveat rule's twin of the sibling-compiler test --------------------------------------


SEEN = "hygiene.SEEN"


def _marking(release: ReleaseView, form: Mapping[str, JsonValue]) -> list[str] | tuple[str, ...]:
    """A rule that marks its view, and raises ``SEEN`` when it finds the mark already there."""
    fields: Any = release.descriptors["violations.severity"].fields
    seen = "zzz" in fields.missing_codes
    fields.missing_codes["zzz"] = "UNKNOWN"
    return [SEEN] if seen else []


def test_a_cohort_s_caveats_never_depend_on_what_a_sibling_s_rule_did_to_its_view(
    canon: Canon, city: City, doc: Doc
) -> None:
    pack = Pack(
        manifest=PackManifest(id="hygiene", version="1", results_version=1, requires_core=">=0"),
        leaf_kinds=kinds(),
        caveat_codes={SEEN: Severity.WARN},
        caveat_rule=_marking,
    )
    packs = PackRegistry([pack], core_version=CORE)
    release = with_packs(city(), ["hygiene"])
    alone = canon(doc({"b": [THAI_ANY]}), release, registry=packs)
    siblings = canon(doc({"a": [severe(1)], "b": [THAI_ANY]}), release, registry=packs)
    assert siblings.refusals == []
    assert siblings.cohorts["b"].id == alone.cohorts["b"].id
    assert siblings.cohorts["b"].caveats == alone.cohorts["b"].caveats == ()


# --- No view is shared across packs -----------------------------------------------------------


def _audit(rule: CaveatRule, compile: Kind | None = None) -> Pack:
    return Pack(
        manifest=PackManifest(id="audit", version="1", results_version=1, requires_core=">=0"),
        leaf_kinds={} if compile is None else {"audit.reading": compile},
        caveat_codes={"audit.SEEN": Severity.WARN},
        caveat_rule=rule,
    )


def _reading(release: ReleaseView, form: Mapping[str, JsonValue]) -> list[str] | tuple[str, ...]:
    fields: Any = release.descriptors["violations.severity"].fields
    return ["audit.SEEN"] if "zzz" in fields.missing_codes else []


def test_one_pack_s_rule_never_sees_what_another_pack_s_rule_did_to_its_view(
    canon: Canon, city: City, doc: Doc
) -> None:
    def marking(release: ReleaseView, form: Mapping[str, JsonValue]) -> list[str] | tuple[str, ...]:
        fields: Any = release.descriptors["violations.severity"].fields
        fields.missing_codes["zzz"] = "UNKNOWN"
        return []

    packs = PackRegistry([hygiene(rule=marking), _audit(_reading)], core_version=CORE)
    release = with_packs(city(), ["hygiene", "audit"])
    cohort = only(canon(doc([THAI_ANY]), release, registry=packs))
    assert cohort.caveats == ()
    assert "zzz" not in release.by_id["violations.severity"].fields.missing_codes  # type: ignore[union-attr]


def test_one_pack_s_compiler_never_sees_what_another_pack_s_compiler_did_to_its_view(
    canon: Canon, city: City, doc: Doc
) -> None:
    class Marking(Kind):
        def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> Any:
            fields: Any = release.descriptors["violations.severity"].fields
            fields.missing_codes["zzz"] = "UNKNOWN"
            return []

    class Reading(Kind):
        def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> Any:
            fields: Any = release.descriptors["violations.severity"].fields
            return [] if "zzz" not in fields.missing_codes else [{"kind": "not a clause"}]

    packs = PackRegistry(
        [
            hygiene(rule=None, leaf_kinds={**kinds(), "hygiene.marking": Marking(lambda m: [])}),
            _audit(lambda release, form: [], Reading(lambda m: [])),
        ],
        core_version=CORE,
    )
    result = canon(
        doc([{"kind": "hygiene.marking"}, {"kind": "audit.reading"}]), city(), registry=packs
    )
    assert result.refusals == []
