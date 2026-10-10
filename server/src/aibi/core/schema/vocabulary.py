"""The JSON Schema 2020-12 vocabulary, partitioned for a form renderer (SPEC §10.1, §12.4, D422).

A form is drawn from a schema the server serves: a core analysis's ``params`` (``list_analyses``)
or a pack's leaf kind (``list_leaf_kinds``). The renderer draws a form from the keywords it
knows and **refuses** a schema that holds one it does not, rather than drawing a form that
accepts or rejects other values than the server does (SPEC §3: refuse rather than approximate).
This module states which keywords those are, so that M5.3's renderer takes its refusal list from
the generated ``schemas/vocabulary.json`` (``document``) and not from a list of its own.

The keywords are the 57 of the seven vocabularies of the 2020-12 metaschema (a test reads them
from the ``jsonschema_specifications`` package), each in exactly one class:

- *rendered*: the renderer can draw a form that agrees with the server about it. A core
  analysis's ``params`` use only these (a test walks the registry's), but 12 rendered keywords
  are used by no core ``params`` and are rendered because a form can honour them, not because
  anything needs them: ``$comment``, ``$id``, ``allOf``, ``deprecated``, ``else``, ``examples``,
  ``format``, ``minLength``, ``multipleOf``, ``readOnly``, ``uniqueItems`` and ``writeOnly``. The
  annotations a form needs (``title``, ``description``, ``default``, ``examples``, ``readOnly``,
  ``deprecated``, ``$comment``) are shown, never applied; so is ``format``, which the server
  never asserts (D247), so a renderer that applied it would reject values the server accepts.
  A root ``$schema`` and ``$id`` are dropped before a value is checked (D406), so a renderer
  ignores them; a subschema's is refused at registration. ``pattern`` and ``patternProperties``
  are rendered for the core's own schemas: a pack's may not hold them (D247).
- *refused*: the renderer refuses a schema that holds one: those that need an engine's work the
  renderer does not do (anchors and dynamic references, ``contains`` and its counts,
  ``prefixItems``, ``dependentSchemas``, the ``unevaluated`` keywords, which need what other
  keywords evaluated) and the content keywords, which say how a string is encoded.

Three further rules make the list complete: the metaschema's own top level also names
``definitions``, ``dependencies``, ``$recursiveAnchor`` and ``$recursiveRef`` (the older
dialects' keywords, outside the 57), which are *refused*; and **any keyword in none of these
lists is refused too**: D247 admits a pack's unknown keywords (``x-widget``, ``foo``), so the
rule is the renderer's, not the registration's, and a renderer's test of it is M5.3's. The
classification of the keywords no core ``params`` uses is a choice, not a finding: a keyword
leaves *refused* when its renderer is written, a deliberate edit of this table, its generated
file, the golden in the tests and D422 together.
"""

from dataclasses import dataclass

from pydantic import JsonValue

from aibi.core.schema.jsonschemas import DIALECT

VOCABULARY_FILE = "vocabulary.json"
"""The generated file under ``schemas/`` (``schema.export`` writes it)."""
VOCABULARY_BASE = DIALECT.rsplit("/", 1)[0] + "/meta/"
"""Where the dialect's vocabularies are: a vocabulary's id is this and its name."""


@dataclass(frozen=True)
class Vocabulary:
    """One vocabulary's keywords by class."""

    rendered: tuple[str, ...]
    refused: tuple[str, ...]

    @property
    def keywords(self) -> frozenset[str]:
        return frozenset((*self.rendered, *self.refused))


VOCABULARIES: dict[str, Vocabulary] = {
    "core": Vocabulary(
        rendered=("$comment", "$defs", "$id", "$ref", "$schema"),
        refused=("$anchor", "$dynamicAnchor", "$dynamicRef", "$vocabulary"),
    ),
    "applicator": Vocabulary(
        rendered=(
            "additionalProperties",
            "allOf",
            "anyOf",
            "else",
            "if",
            "items",
            "not",
            "oneOf",
            "patternProperties",
            "properties",
            "propertyNames",
            "then",
        ),
        refused=("contains", "dependentSchemas", "prefixItems"),
    ),
    "unevaluated": Vocabulary(
        rendered=(),
        refused=("unevaluatedItems", "unevaluatedProperties"),
    ),
    "validation": Vocabulary(
        rendered=(
            "const",
            "dependentRequired",
            "enum",
            "exclusiveMaximum",
            "exclusiveMinimum",
            "maxItems",
            "maxLength",
            "maxProperties",
            "maximum",
            "minItems",
            "minLength",
            "minProperties",
            "minimum",
            "multipleOf",
            "pattern",
            "required",
            "type",
            "uniqueItems",
        ),
        refused=("maxContains", "minContains"),
    ),
    "meta-data": Vocabulary(
        rendered=(
            "default",
            "deprecated",
            "description",
            "examples",
            "readOnly",
            "title",
            "writeOnly",
        ),
        refused=(),
    ),
    "format-annotation": Vocabulary(rendered=("format",), refused=()),
    "content": Vocabulary(
        rendered=(),
        refused=("contentEncoding", "contentMediaType", "contentSchema"),
    ),
}
"""The 57 keywords of the dialect's seven vocabularies, each rendered or refused."""
OUTSIDE = ("$recursiveAnchor", "$recursiveRef", "definitions", "dependencies")
"""The keywords the metaschema's own top level names beside its vocabularies: refused."""

RENDERED = frozenset(k for vocabulary in VOCABULARIES.values() for k in vocabulary.rendered)
"""The keywords a renderer can draw a form for: it shows the annotations and `format`, never
applies them, and applies the rest."""
REFUSED = frozenset(k for vocabulary in VOCABULARIES.values() for k in vocabulary.refused)
"""The vocabulary's keywords a renderer refuses a schema for."""

UNLISTED = "refused"
"""What a renderer does with a keyword in neither class nor ``OUTSIDE`` (D247 admits them)."""


def refuses(keyword: str) -> bool:
    """Whether a renderer refuses a schema that holds ``keyword``: all but the rendered ones."""
    return keyword not in RENDERED


def document() -> dict[str, JsonValue]:
    """The generated ``schemas/vocabulary.json``: the partition by vocabulary and in all, the
    keywords outside the vocabulary and the rule for any other."""
    vocabularies: dict[str, JsonValue] = {}
    for name, vocabulary in sorted(VOCABULARIES.items()):
        member: dict[str, JsonValue] = {
            "id": VOCABULARY_BASE + name,
            "rendered": list[JsonValue](sorted(vocabulary.rendered)),
            "refused": list[JsonValue](sorted(vocabulary.refused)),
        }
        vocabularies[name] = member
    return {
        "dialect": DIALECT,
        "rule": (
            "A form renderer draws a form from the rendered keywords (annotations and format are "
            "shown, never applied; a root $id and $schema are ignored) and refuses a schema that "
            "holds any other: a refused one, one of those outside the vocabulary (outside), or "
            "one in no list (unlisted)."
        ),
        "unlisted": UNLISTED,
        "vocabularies": vocabularies,
        "rendered": list[JsonValue](sorted(RENDERED)),
        "refused": list[JsonValue](sorted(REFUSED)),
        "outside": list[JsonValue](sorted(OUTSIDE)),
    }


__all__ = [
    "OUTSIDE",
    "REFUSED",
    "RENDERED",
    "UNLISTED",
    "VOCABULARIES",
    "VOCABULARY_BASE",
    "VOCABULARY_FILE",
    "Vocabulary",
    "document",
    "refuses",
]
