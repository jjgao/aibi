"""The server's wording compared with the base branch's (D374, D399).

``test_wording.py`` holds the wording to the file checked in beside it, and the file is
self-consistent, so a change of a template and of the file together, with ``CACHE_WORDING``
untouched, passes it. The base's copy is what a change cannot rewrite: CI (``ci.yml``) writes the
base branch's ``wording.json`` and ``text_projection.json`` to the directory ``AIBI_WORDING_BASE``
names, and these functions say what the head must have done since: raised ``CACHE_WORDING``
whenever the templates, the classification or the text of a result changed, never lowered either
number, and kept the history it extends.
"""

import os
from pathlib import Path
from typing import Any

ENV = "AIBI_WORDING_BASE"
"""The directory of the base's copies: ``wording.json`` and ``text_projection.json``, each there
only if the base has it (a file the change introduces has no base)."""
EVENT = "GITHUB_EVENT_NAME"
"""CI's name of the event that started the run: ``pull_request`` for the run the base is for."""


class BaseMissingError(AssertionError):
    """The base's copies were due and are not there: a check that cannot run fails, and does
    not skip, so that CI does not pass without it."""


def directory() -> Path | None:
    """The directory of the base's copies, or ``None`` where there is no base to compare with (a
    local run, a push to ``main``). It raises ``BaseMissingError`` when ``AIBI_WORDING_BASE``
    names a directory that is not there, and when a pull request's run (CI's step sets the
    variable) has none."""
    given = os.environ.get(ENV)
    if not given:
        if os.environ.get(EVENT) == "pull_request":
            raise BaseMissingError(
                f"{ENV} is not set on a pull request: CI's step did not hand it on"
            )
        return None
    found = Path(given)
    if not found.is_dir():
        raise BaseMissingError(f"{ENV} names {given}, which is not a directory")
    return found


def wording_violations(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    """What ``head`` (``wording.json``) must still do, given ``base``'s."""
    found: list[str] = []
    raised = head["cache_wording"] > base["cache_wording"]
    if head["cache_wording"] < base["cache_wording"]:
        found.append("CACHE_WORDING is lower than the base's: it is never lowered")
    if head["report_classification"] < base["report_classification"]:
        found.append("REPORT_CLASSIFICATION is lower than the base's: it is never lowered")
    if (head["templates"], head["count"]) != (base["templates"], base["count"]) and not raised:
        found.append(
            "a template of the server's text changed since the base: raise CACHE_WORDING "
            f"(the base's is {base['cache_wording']})"
        )
    if head["report_classification"] != base["report_classification"] and not raised:
        found.append(
            "REPORT_CLASSIFICATION changed since the base: raise CACHE_WORDING "
            f"(the base's is {base['cache_wording']})"
        )
    if head["history"][: len(base["history"])] != base["history"]:
        found.append("the history is not the base's history and more: it only grows")
    return found


def projection_violations(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    """What ``head`` (``text_projection.json``) must still do, given ``base``'s: the text of a
    result the base holds changed, or the result gone or renamed, only under a raised wording. A
    result the base does not hold has no cached rows to serve in another wording."""
    found: list[str] = []
    if head["cache_wording"] < base["cache_wording"]:
        found.append("CACHE_WORDING is lower than the base's: it is never lowered")
    changed = sorted(
        name
        for name, projected in base["cases"].items()
        if name not in head["cases"] or head["cases"][name] != projected
    )
    if changed and head["cache_wording"] <= base["cache_wording"]:
        found.append(
            f"the text of {', '.join(changed)} changed or is gone since the base: raise "
            f"CACHE_WORDING (the base's is {base['cache_wording']})"
        )
    return found
