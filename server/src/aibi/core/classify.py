"""The one classification of a request, which request protection records in its scope (D414).

Request protection classifies each request once, by the path the routes match (``chrome.
route_path``, before any mount rewrites the scope), and records ``Classified`` in its scope under
``CLASSIFIED_KEY``, which it drops from an incoming scope first, as it drops an attribution:

- ``kind``: ``navigable`` (a page a link from another site may open, D314), ``page`` (any other
  page path, D311), both only when the application serves the pages; ``operator`` (``/operator``
  and below); ``asset`` (``/assets`` and below, D410); and ``other``.
- ``root``: the server's root path as links start from it (``chrome.root_link``), or ``None``
  when it is not plain path segments (D312): unusable, and refused loudly where a link or the web
  bundle needs it, never dropped.
- ``browser``: whether a browser sent the request (an ``Origin`` or a ``Sec-Fetch-*`` header,
  D263).

The error handlers, the catalogue page, the web bundle's routes and the operator router (a
take-over's ``browser``, D415) read it (``classified``, ``root_of``) and never classify a
request again: inside a mount the scope's
``root_path`` and ``path`` are the mount's, and a percent-encoded path is another string than
the one protection classified. Without the record (an application that runs without request
protection, which ``create_app`` never builds) a request is ``other`` with the root ``""``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Scope = Mapping[str, object]
"""An ASGI scope, as read here; the module needs no web framework, so that the operator router
reads the record as the HTTP application does."""

CLASSIFIED_KEY = "aibi.classified"
"""The scope's key of the record; request protection drops an incoming one."""
Kind = Literal["navigable", "page", "operator", "asset", "other"]
_UNUSABLE = "the server's root path is not a path of plain segments"


@dataclass(frozen=True)
class Classified:
    """What request protection decided of a request (module docstring)."""

    kind: Kind
    root: str | None
    """The root links start from; ``None`` when the server's root path is unusable."""
    browser: bool

    @property
    def page(self) -> bool:
        """Whether every answer to the request is a page (D311)."""
        return self.kind in ("navigable", "page")


def classified(scope: Scope) -> Classified | None:
    """The record request protection made of the request, if any."""
    found = scope.get(CLASSIFIED_KEY)
    return found if isinstance(found, Classified) else None


def root_of(scope: Scope) -> str:
    """The server's root path links start from, as recorded; ``""`` without a record, and
    ``ValueError`` when it is unusable (D312), so that no page is served with links outside the
    application."""
    found = classified(scope)
    if found is None:
        return ""
    if found.root is None:
        raise ValueError(_UNUSABLE)
    return found.root


__all__ = ["CLASSIFIED_KEY", "Classified", "Kind", "classified", "root_of"]
