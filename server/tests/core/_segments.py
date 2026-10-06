"""Segments and outputs rebuilt from JSON in tests (D399).

Server text is made by ``text()``, re-validated as an exact instance, or rebuilt at a listed
boundary; a test that holds a golden document, or a segment written as ``{"text": ...}``, reads
it back as the result cache reads what the server wrote. Each helper is one validating call whose
validation context is the capability, and the capability reaches that call's validators and
nothing else: it is never held around a call into the server, so a test cannot mask a mistake in
the code it calls (a test scans ``tests/`` for the capability's names outside this module and the
tests of the boundaries themselves).
"""

from pydantic import TypeAdapter

from aibi.core.schema.output import Boundary, Segment, admitted_at

_SEGMENTS: TypeAdapter[list[Segment]] = TypeAdapter(list[Segment])


def segments_from(value: object) -> list[Segment]:
    """``value``, a list of segments as JSON values, read back as segments."""
    return _SEGMENTS.validate_python(value, context=admitted_at(Boundary.RESULT_CACHE))


def validated[T](kind: type[T], value: object) -> T:
    """``value``, ``kind`` as JSON text or as JSON values, read back as ``kind``."""
    adapter = TypeAdapter(kind)
    if isinstance(value, str | bytes):
        return adapter.validate_json(value, context=admitted_at(Boundary.RESULT_CACHE))
    return adapter.validate_python(value, context=admitted_at(Boundary.RESULT_CACHE))
