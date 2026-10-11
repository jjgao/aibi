"""What a guard around a pack's code passes on (SPEC §10.1, D285, D343, D402).

A pack's code is contained: whatever it raises is its failure, but for ``PASSED``, the process
out of memory or asked to stop, which only these types themselves pass, compared by identity
(never a subclass, nor a type whose metaclass says it equals one), each raised again as a new
instance, without the pack's message, traceback or context.
"""

PASSED: tuple[type[BaseException], ...] = (MemoryError, KeyboardInterrupt, SystemExit)


def passed(error: BaseException) -> type[BaseException] | None:
    """The type of ``PASSED`` that ``error`` is exactly, by identity, if any; it runs no code of
    the error's (its type's ``__eq__`` and ``__hash__`` included)."""
    kind = type(error)
    return next((one for one in PASSED if kind is one), None)


__all__ = ["PASSED", "passed"]
