"""Each failed test in pytest's junit reports (``reports/*.xml``, run from ``server/``) as a
check-run annotation, which the checks API serves where the job's log cannot be read (D334).
``OUTCOMES`` names the job's test steps and their outcomes, ``name=outcome`` joined by ``;``:
a step that failed without a failed test ran out of its time limit or was stopped (D340)."""

import glob
import os
import xml.etree.ElementTree as ET


def escaped(value: str, member: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return value.replace(":", "%3A").replace(",", "%2C") if member else value


reports = sorted(glob.glob("reports/*.xml"))
stopped = [
    name
    for name, _, outcome in (
        given.rpartition("=") for given in os.environ.get("OUTCOMES", "").split(";") if given
    )
    if outcome == "failure"
]
after = (
    f" ({' and '.join(stopped)} failed: a step's time limit, D340, or a check of the whole session)"
    if stopped
    else ""
)
found = []
for report in reports:
    for case in ET.parse(report).iter("testcase"):
        for failed in [*case.findall("failure"), *case.findall("error")]:
            name = "::".join(p for p in (case.get("classname"), case.get("name")) if p)
            where = f"server/{case.get('file', '')}"
            line = int(case.get("line") or 0) + 1
            said = f"{failed.get('message') or ''}\n{failed.text or ''}".strip()
            found.append(
                f"::error file={escaped(where, True)},line={line},"
                f"title={escaped(name[:200], True)}::{escaped(said[:4000])}"
            )
# A step keeps 10 error annotations: past that, one says how many the rest are.
if len(found) > 10:
    print(
        f"::error title={len(found)} failed::{len(found)} tests failed; the first 9 "
        "are annotated, and the job's log holds the rest"
    )
    found = found[:9]
if found:
    print(*found, sep="\n")
if not reports:
    print(
        "::error title=No report::pytest wrote no report: a step before the tests "
        f"failed, or pytest stopped before writing one{after}"
    )
elif not found:
    print(
        "::error title=No failed test::No test failed in pytest's reports: a check "
        f"of the whole session failed, or a step outside the tests did{after}"
    )
