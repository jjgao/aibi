"""``params`` substitution on the document as written (SPEC §7.1).

A string that is exactly ``"$name"`` is replaced by that parameter's value, whatever its type;
``"$$…"`` stands for the literal string with one ``$`` removed; any other string starting with
``$`` is refused, so that a mistyped reference is never taken literally. Object keys and the
``params`` member itself are never substituted, and substituted values are not scanned again.
"""

import copy
import re
from dataclasses import dataclass, field

from pydantic import JsonValue

from aibi.core.schema.ids import NAME
from aibi.core.schema.jsonio import pointer
from aibi.core.schema.refusals import Refusal, RefusalCode, data, text

_REFERENCE = re.compile(rf"^\$({NAME})$")


@dataclass
class Substitution:
    """The substituted document, the parameters used and the positions they filled."""

    document: JsonValue
    used: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    unused: list[str] = field(default_factory=list[str])
    positions: dict[str, str] = field(default_factory=dict[str, str])
    """JSON Pointer of each substituted position -> parameter name."""
    refusals: list[Refusal] = field(default_factory=list[Refusal])


def substitute(document: dict[str, JsonValue]) -> Substitution:
    params_value = document.get("params", {})
    if not isinstance(params_value, dict):
        return Substitution(
            document=document,
            refusals=[
                Refusal(
                    code=RefusalCode.WRONG_TYPE,
                    path="/params",
                    message=[text("params must be an object mapping names to values")],
                )
            ],
        )
    params: dict[str, JsonValue] = params_value
    result = Substitution(document=None)

    def walk(value: JsonValue, path: list[str | int]) -> JsonValue:
        if isinstance(value, dict):
            return {key: walk(member, [*path, key]) for key, member in value.items()}
        if isinstance(value, list):
            return [walk(item, [*path, index]) for index, item in enumerate(value)]
        if isinstance(value, str) and value.startswith("$"):
            if value.startswith("$$"):
                return value[1:]
            where = pointer(path)
            match = _REFERENCE.match(value)
            if match is None:
                result.refusals.append(
                    Refusal(
                        code=RefusalCode.INVALID_PARAMETER_REFERENCE,
                        path=where,
                        message=[
                            text("Not a parameter reference: "),
                            data(value),
                            text('. Write "$name" for a parameter or "$$…" for a literal $'),
                        ],
                    )
                )
                return value
            name = match.group(1)
            if name not in params:
                result.refusals.append(
                    Refusal(
                        code=RefusalCode.UNKNOWN_PARAMETER,
                        path=where,
                        message=[text("Unknown parameter "), data(name)],
                        alternatives=[data(declared) for declared in sorted(params)],
                    )
                )
                return value
            result.used[name] = params[name]
            result.positions[where] = name
            return copy.deepcopy(params[name])
        return value

    substituted: dict[str, JsonValue] = {}
    for key, member in document.items():
        substituted[key] = member if key == "params" else walk(member, [key])
    result.document = substituted
    result.unused = sorted(set(params) - set(result.used))
    return result
