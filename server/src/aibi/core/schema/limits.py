"""Default size limits for documents (SPEC §7.1, §14). Every refusal names the limit it hit."""

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_LIST = 10_000
"""Members of a ``values``, ``ids`` or scope value list (SPEC §14)."""
MAX_STRING = 4_096
"""Characters in a constant or other short string."""
MAX_TEXT = 10_000
"""Characters in ``notes`` and ``note``."""
MAX_NAME = 200
"""Characters in a self-declared name (``agent:`` and ``operator:`` in ``drafted_by``)."""
MAX_PATH_STEPS = 16
MAX_CLAUSES = 256
"""Clauses in one ``all``, ``any`` or ``where`` list, before the canonical caps of M2."""
MAX_COHORTS = 6
MAX_VIEWS = 8
MAX_PARAMS = 256
