"""The domain job's fixtures (SPEC D449): the engine tests' helpers, which resolve and evaluate a
document and run its SQL in a helper process (``tests/core/engine/conftest.py``), for the derived
domain of grouped coverage and the cost cells of the one-group path. Every module here carries
the ``domain`` marker, which ``addopts`` deselects: CI's ``domain`` job runs them."""

from tests.core.engine.conftest import (
    blobs,
    doc,
    force,
    materialised,
    run,
    sessions,
    variables_of,
)

__all__ = ["blobs", "doc", "force", "materialised", "run", "sessions", "variables_of"]
