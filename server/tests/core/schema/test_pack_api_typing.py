"""``pack_api``'s protocols accept a pack's narrower annotations (D388, round 2 of #79's review,
m3): ``pyright`` checks this module, so each example is pack code that type-checks against the
protocols, and each is also driven through its copier, which takes it, since at run time it is the
exact ``list``, ``tuple`` or ``dict`` the copiers demand."""

from collections.abc import Mapping

from pydantic import JsonValue

from aibi.core.schema import guards
from aibi.core.schema.copiers import (
    Expansion,
    expansion,
    facet_values,
    pack_refusals,
    proposals,
    segments,
    translation,
)
from aibi.core.schema.document import PackLeaf, ValueLeaf
from aibi.core.schema.guards import Hook
from aibi.core.schema.output import TextSegment, text
from aibi.core.schema.pack_api import (
    Facet,
    LeafKind,
    Proposal,
    Proposer,
    ReleaseView,
    TranslationNote,
    Translator,
    Validator,
)
from aibi.core.schema.refusals import Refusal

PACK = "library"
NAMES = ("result_values", "result_characters", "text_characters")


class Overdue:
    """A leaf kind whose methods give exact lists of one core type each, narrower than
    ``Clauses`` and ``Segments``."""

    @property
    def schema(self) -> Mapping[str, JsonValue]:
        return {"type": "object"}

    def compile(self, leaf: PackLeaf, release: ReleaseView, pack_version: str) -> list[ValueLeaf]:
        return [ValueLeaf.model_validate({"kind": "value", "column": "loans.days", "values": [1]})]

    def summary(self, leaf: PackLeaf) -> list[TextSegment]:
        return [text("loans returned late")]


class Checks:
    """A validator whose answers are a ``list`` and a ``tuple`` of ``Refusal``s."""

    def validate_source(self, source: object, result: object) -> list[Refusal]:
        return []

    def validate_descriptors(self, release: ReleaseView) -> tuple[Refusal, ...]:
        return ()


class Translates:
    """A translator whose notes are a ``list`` of notes, and its message a ``list`` of text."""

    def translate(self, document: JsonValue) -> tuple[dict[str, JsonValue], list[TranslationNote]]:
        return {"aibi": "1"}, [TranslationNote("/a", [text("a note")])]


def facet_lists(release: ReleaseView) -> dict[str, list[str]]:
    return {"region": ["north"]}


def facet_tuples(release: ReleaseView) -> dict[str, tuple[str, ...]]:
    return {"region": ("north",)}


def propose(release: ReleaseView) -> list[Proposal]:
    return [Proposal("sites", "/label", "Places")]


KIND: LeafKind = Overdue()
VALIDATOR: Validator = Checks()
TRANSLATOR: Translator = Translates()
FACETS: tuple[Facet, ...] = (facet_lists, facet_tuples)
PROPOSER: Proposer = propose


def test_a_pack_s_narrower_annotations_are_what_the_copiers_take() -> None:
    leaf = PackLeaf.model_validate({"kind": "library.overdue"})
    view: object = object()
    kind = Hook(KIND, PACK, "compiler")
    found = kind.call(lambda h: expansion(h.compile, leaf, view, "1.0", pack=PACK))
    assert isinstance(found, Expansion)
    assert kind.call(lambda h: segments(h.summary, leaf)) == (text("loans returned late"),)
    for facet in FACETS:
        held = Hook(facet, PACK, "facet")
        assert held.call(lambda h: facet_values(h, view)) == {"region": ("north",)}
    checks = Hook(VALIDATOR, PACK, "validator")
    assert checks.call(lambda h: pack_refusals(h.validate_descriptors, view, pack=PACK)) == ()
    allowance = guards.allowance(1_000, 10_000, 1_000, NAMES)
    translator = Hook(TRANSLATOR, PACK, "translator")
    translated = translator.call(lambda h: translation(h.translate, {}, allowance=allowance))
    assert isinstance(translated, tuple)
    assert [note.pointer for note in translated[1]] == ["/a"]
    allowances = guards.allowances(1_000, 10_000, 1_000, NAMES)
    proposer = Hook(PROPOSER, PACK, "proposer")
    [item] = proposer.call(lambda h: proposals(h, view, pack=PACK, allowances=allowances))
    assert item.proposal is not None
