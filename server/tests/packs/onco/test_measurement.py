"""Measures what importing a study's clinical files costs (M4.2a-1b, D438); **by hand**, never in
the suite or in CI (the ``measurement`` marker, which ``addopts`` deselects), from ``server/``:

    uv run pytest -m measurement tests/packs/onco/test_measurement.py -s -p no:cacheprovider

Each import runs in a fresh interpreter of its own, so that its peak resident memory is its own
(``ru_maxrss``), into a new store, through ``import_dataset`` and the pack's registry, twice:

- ``core``: the core alone, with the pack's reader replaced by one that reads the header and counts
  the lines (``HEADER_ONLY``), so that the same snapshots, layouts and descriptors reach the core's
  checks and build;
- ``pack``: through ``PACK.importer`` as it is.

The pack's share is their difference. The studies, generated here (synthetic, in a temporary
directory):

- ``short``: just under 20,000,000 short cells, the default ``import_cells`` (a sample file of
  987,654 rows of 20 columns, and a patient file of one column of a quarter as many rows);
- ``long-<n>``: a sample file of about *n* MiB of long cells, each 4,000 characters holding one
  U+1F600 and the same cell throughout, for *n* in 100, 256 and 512 (1 GiB, the default
  ``import_bytes``, needs about 15 GiB: the numbers above are extrapolated to it, and that is
  stated as an extrapolation). **That shape repeats one cell, so it hides what distinct cells
  cost** (a cache, a set of values); the next two do not;
- ``distinct-<n>``: the same, every cell distinct (a number first), for *n* in 100 and 256;
- ``tabs``: a patient file of 191 MiB whose one data line is 10^8 tabs, which the pack refuses for
  its width (the core alone, given the same file, parses the line);
- ``retain``: two studies of distinct long cells imported one after the other, and the first
  again, in one process (``retention``), recording what Python still holds (``tracemalloc``) when
  each import has returned: it must not grow with the imports, and holds no cell's value.

It records seconds and MiB, and asserts nothing of them; ``AIBI_MEASUREMENT_OUT``, a file, receives
the lines as well as the output. The unmarked smoke test runs the machinery on a tiny study.
"""

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

RUN = r"""
import json, resource, shutil, sys, time
from pathlib import Path
import aibi
from aibi.core.importers.confine import Confinement
from aibi.core.importers.errors import ImportRefused
from aibi.core.importers.run import import_dataset
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import ImportOptions, PackRegistry
from aibi.core.store.store import Store
from aibi.packs.onco import PACK, clinical, importer

study, work, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
spent = {"pack": 0.0}
read = clinical.read

def header_only(data, name, role, limits, *, cells_before=0, patient=None):
    end = -1
    for _ in range(5):  # the header's five lines, found without a copy of what follows them
        end = data.find(b"\n", end + 1)
    found = read(data[: end + 1], name, role, limits, cells_before=cells_before, patient=patient)
    rows = data.count(b"\n") - 5
    return clinical.Clinical(found.name, found.role, data, found.columns, rows, found.keys)

if mode == "core":
    importer.read = header_only
inner = PACK.importer.import_source
def timed(source, options):
    started = time.perf_counter()
    try:
        return inner(source, options)
    finally:
        spent["pack"] = time.perf_counter() - started
PACK.importer.import_source = timed
store = Store(work / "data")
registry = PackRegistry([PACK], core_version=aibi.__version__)
confinement = Confinement.of(study.parent)
options = ImportOptions(dataset="m", reader=confinement, limits=ImportLimits(), at=store.now())
started = time.perf_counter()
source = confinement.confine(study)
try:
    published = import_dataset(
        store, source, options, "operator:Ada", registry=registry, pack="onco"
    )
    label = published.label
except ImportRefused as refused:
    label = "refused " + str(refused.refusals[0].code)
wall = time.perf_counter() - started
peak = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
found = {"mode": mode, "label": label, "wall": round(wall, 2)}
found.update({"importer": round(spent["pack"], 2), "peak_mib": peak})
print(json.dumps(found))
"""

RETAIN = r"""
import gc, json, sys, tracemalloc
from pathlib import Path
import aibi
from aibi.core.importers.confine import Confinement
from aibi.core.importers.run import import_dataset
from aibi.core.schema.limits import ImportLimits
from aibi.core.schema.pack_api import ImportOptions, PackRegistry
from aibi.core.store.store import Store
from aibi.packs.onco import PACK

work = Path(sys.argv[1])
studies = [Path(path) for path in sys.argv[2:]]
registry = PackRegistry([PACK], core_version=aibi.__version__)
store = Store(work / "data")
tracemalloc.start()
held = []
for index, study in enumerate([*studies, studies[0]]):
    confinement = Confinement.of(study.parent)
    options = ImportOptions(
        dataset=f"m{index}", reader=confinement, limits=ImportLimits(), at=store.now()
    )
    published = import_dataset(
        store, confinement.confine(study), options, "operator:Ada", registry=registry, pack="onco"
    )
    del published, confinement, options
    gc.collect()
    held.append(round(tracemalloc.get_traced_memory()[0] / 2**20, 1))
print(json.dumps({"held_mib_after_each_import": held}))
"""


def measure(study: Path, mode: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as work:
        ran = subprocess.run(
            [sys.executable, "-c", RUN, str(study), work, mode],
            capture_output=True,
            text=True,
            timeout=3_600,
            check=False,
        )
    assert ran.returncode == 0, ran.stderr[-2000:]
    found: dict[str, object] = json.loads(ran.stdout.strip().splitlines()[-1])
    return found


def record(line: str) -> None:
    print(line, flush=True)
    out = os.environ.get("AIBI_MEASUREMENT_OUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


META = {
    "meta_study.txt": (
        b"type_of_cancer: mixed\ncancer_study_identifier: m\nname: m\ndescription: m\n"
    ),
    "meta_clinical_sample.txt": (
        b"cancer_study_identifier: m\ngenetic_alteration_type: CLINICAL\n"
        b"datatype: SAMPLE_ATTRIBUTES\ndata_filename: data_clinical_sample.txt\n"
    ),
    "meta_clinical_patient.txt": (
        b"cancer_study_identifier: m\ngenetic_alteration_type: CLINICAL\n"
        b"datatype: PATIENT_ATTRIBUTES\ndata_filename: data_clinical_patient.txt\n"
    ),
}


def header(names: list[str], types: list[str]) -> bytes:
    n = len(names)
    rows = ["\t".join(["L"] * n), "\t".join(["D"] * n), "\t".join(types), "\t".join(["1"] * n)]
    return ("".join(f"#{row}\n" for row in rows) + "\t".join(names) + "\n").encode()


def short_study(directory: Path, rows: int, columns: int) -> Path:
    """A sample file of ``rows`` rows of ``columns`` short cells (the two keys among them), whose
    patients are one patient file's single column."""
    directory.mkdir(parents=True)
    for name, content in META.items():
        (directory / name).write_bytes(content)
    patients = max(1, rows // 4)
    with open(directory / "data_clinical_patient.txt", "wb") as out:
        out.write(header(["PATIENT_ID"], ["STRING"]))
        out.writelines(f"P{i}\n".encode() for i in range(patients))
    names = ["PATIENT_ID", "SAMPLE_ID", *(f"C{i}" for i in range(columns - 2))]
    types = ["STRING", "STRING", *(["NUMBER", "STRING"] * columns)[: columns - 2]]
    with open(directory / "data_clinical_sample.txt", "wb") as out:
        out.write(header(names, types))
        cells = "\t".join("1" if t == "NUMBER" else "a" for t in types[2:])
        out.writelines(f"P{i % patients}\tS{i}\t{cells}\n".encode() for i in range(rows))
    return directory


def long_study(directory: Path, mebibytes: int, *, distinct: bool = False) -> Path:
    """A sample file of about ``mebibytes`` MiB of cells of 4,000 characters, one U+1F600 each:
    the same cell in every row, or with ``distinct`` each cell's own (a number first)."""
    directory.mkdir(parents=True)
    for name, content in META.items():
        (directory / name).write_bytes(content)
    (directory / "data_clinical_patient.txt").write_bytes(
        header(["PATIENT_ID"], ["STRING"]) + b"P0\n"
    )
    cell = ("x" * 3_999 + "\U0001f600").encode()
    names = ["PATIENT_ID", "SAMPLE_ID", "NOTE_A", "NOTE_B"]
    rows = mebibytes * 2**20 // (2 * len(cell) + 16)
    with open(directory / "data_clinical_sample.txt", "wb") as out:
        out.write(header(names, ["STRING"] * 4))
        if distinct:
            out.writelines(
                b"P0\tS%d\t%08d" % (i, 2 * i) + cell + b"\t%08d" % (2 * i + 1) + cell + b"\n"
                for i in range(rows)
            )
        else:
            out.writelines(b"P0\tS%d\t" % i + cell + b"\t" + cell + b"\n" for i in range(rows))
    return directory


def tabs_study(directory: Path, tabs: int = 10**8) -> Path:
    """A patient file whose one data line is ``tabs`` tabs after a cell (191 MiB at 10^8 of
    ``a`` and a tab: a line the pack refuses for its width before it splits it)."""
    directory.mkdir(parents=True)
    for name, content in META.items():
        (directory / name).write_bytes(content)
    with open(directory / "data_clinical_patient.txt", "wb") as out:
        out.write(header(["PATIENT_ID"], ["STRING"]))
        out.write(b"a\t" * tabs + b"a\n")
    (directory / "data_clinical_sample.txt").write_bytes(
        header(["PATIENT_ID", "SAMPLE_ID"], ["STRING", "STRING"]) + b"P0\tS0\n"
    )
    return directory


def both(study: Path, name: str) -> None:
    size = sum(f.stat().st_size for f in study.iterdir()) / 2**20
    for mode in ("core", "pack"):
        found = measure(study, mode)
        record(
            f"{name}: {size:.0f} MiB, {mode}: wall {found['wall']} s, "
            f"importer {found['importer']} s, peak {found['peak_mib']} MiB"
        )


def retention(studies: list[Path]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as work:
        ran = subprocess.run(
            [sys.executable, "-c", RETAIN, work, *map(str, studies)],
            capture_output=True,
            text=True,
            timeout=3_600,
            check=False,
        )
    assert ran.returncode == 0, ran.stderr[-2000:]
    found: dict[str, object] = json.loads(ran.stdout.strip().splitlines()[-1])
    return found


def test_the_measurement_runs_on_a_tiny_study(tmp_path: Path) -> None:
    """The machinery, in three fresh interpreters at once: the core alone on a short study, the
    pack on a line of tabs it refuses, and two imports of a distinct-cells study with what Python
    holds."""
    study = short_study(tmp_path / "imports" / "tiny", rows=10, columns=4)
    tabs = tabs_study(tmp_path / "imports" / "tabs", tabs=1_000)
    distinct = long_study(tmp_path / "imports" / "distinct", 1, distinct=True)
    with ThreadPoolExecutor(3) as pool:
        core = pool.submit(measure, study, "core")
        refused = pool.submit(measure, tabs, "pack")
        held = pool.submit(retention, [distinct, distinct])
        assert core.result()["label"] == 1
        assert str(refused.result()["label"]).startswith("refused onco.")
        kept = held.result()["held_mib_after_each_import"]
    assert isinstance(kept, list)
    assert len(kept) == 3


@pytest.mark.measurement
def test_short_cells_at_the_cell_limit(tmp_path: Path) -> None:
    both(short_study(tmp_path / "imports" / "short", rows=987_654, columns=20), "short 20M cells")


@pytest.mark.measurement
@pytest.mark.parametrize("mebibytes", [100, 256, 512])
def test_long_cells(tmp_path: Path, mebibytes: int) -> None:
    both(long_study(tmp_path / "imports" / f"long{mebibytes}", mebibytes), f"long-{mebibytes}")


@pytest.mark.measurement
@pytest.mark.parametrize("mebibytes", [100, 256])
def test_distinct_long_cells(tmp_path: Path, mebibytes: int) -> None:
    both(
        long_study(tmp_path / "imports" / f"distinct{mebibytes}", mebibytes, distinct=True),
        f"distinct-{mebibytes}",
    )


@pytest.mark.measurement
def test_a_line_of_a_hundred_million_tabs(tmp_path: Path) -> None:
    both(tabs_study(tmp_path / "imports" / "tabs"), "tabs 10^8")


@pytest.mark.measurement
def test_what_is_held_once_an_import_has_returned(tmp_path: Path) -> None:
    first = long_study(tmp_path / "imports" / "first", 95, distinct=True)
    second = long_study(tmp_path / "imports" / "second", 40, distinct=True)
    record(f"retain: {retention([first, second])}")
