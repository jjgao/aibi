"""The clinical-role grid (M4.2a-1a, D409): every spelling of a clinical meta's ``data_filename``
the plan names, built as study directories, each with the outcome the plan expects.

This is not a test: pytest does not collect it (its name does not start with ``test_``), and it
imports nothing of the pack, so ``test_study.py`` loads it by its path inside a fixture. Run as a
script it builds the grid into a directory, writes ``manifest.json`` there (each directory's
role, spelling, target, second axis and expected outcome) and prints its size, so that the same
directories go into the reviewer's differential against cBioPortal::

    cd server
    uv run python tests/packs/onco/clinical_grid.py OUT

**Rows.** A target ``x`` is spelled each way of ``SPELLINGS`` and written as one role's
``data_filename``: the sample meta's or the patient meta's, while the other clinical meta names
its own data file or the same target, as ``x`` or ``./x`` (the second axis); or the study meta's,
which M4.2a ignores, while both clinical metas name their own files as ``x`` or ``./x``. The
targets are the sample file, a patient file whose name is not ASCII (``data_clinical_patiént.txt``,
NFC, so that its NFD spelling differs), ``meta_study.txt`` and a plain listed file. The
``EXTRA`` rows follow (Meta(S) is computed after the exclusions; two metas naming fold-equal
distinct files; a data file named by one component of 255 bytes). The generator asserts the
defining property of each boundary spelling (``LENGTHS``) and the normal form of each accepted
one, so that no row's name is a claim the run did not check.

**The expected outcome** is written from the plan's rule, not computed by the pack's code: a value
is accepted exactly when its normal form is the exact name of a regular file at the study's root
outside Meta(S) (``x``, ``./x``, ``././x``, ``.//x``, padding within 255 bytes), and the two
clinical metas resolve to different entries; otherwise ``DATA_FILE``, naming the first clinical
meta in name order that fails (the patient meta's name sorts first), with the reason P0 or the
names rule gives, and for two metas naming one file, the patient meta.
"""

import json
import os
import posixpath
import shutil
import sys
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path

STUDY = (
    b"type_of_cancer: brca\n"
    b"cancer_study_identifier: demo_study\n"
    b"name: Demo study\n"
    b"description: A synthetic study.\n"
    b"citation: Demo et al., 2026\n"
    b"pmid: 12345\n"
    b"add_global_case_list: true\n"
)
SAMPLE_META = (
    b"cancer_study_identifier: demo_study\n"
    b"genetic_alteration_type: CLINICAL\n"
    b"datatype: SAMPLE_ATTRIBUTES\n"
    b"data_filename: data_clinical_sample.txt\n"
)
PATIENT_META = (
    b"cancer_study_identifier: demo_study\n"
    b"genetic_alteration_type: CLINICAL\n"
    b"datatype: PATIENT_ATTRIBUTES\n"
    b"data_filename: data_clinical_patient.txt\n"
)
SAMPLE_DATA = (
    b"#Patient\tSample\tSample type\tTMB\n"
    b"#The patient's id.\tThe sample's id.\tThe sample's type.\tMutations per megabase.\n"
    b"#STRING\tSTRING\tSTRING\tNUMBER\n"
    b"#1\t1\t1\t1\n"
    b"PATIENT_ID\tSAMPLE_ID\tSAMPLE_TYPE\tTMB\n"
    b"P1\tS1\tPrimary\t4.2\n"
    b"P2\tS2\tPrimary\t[Not Applicable]\n"
)
PATIENT_DATA = (
    b"#Patient\tSex\tVital status\tSurvival (months)\n"
    b"#The patient's id.\tThe patient's sex.\tAlive or not.\tMonths from diagnosis.\n"
    b"#STRING\tSTRING\tSTRING\tNUMBER\n"
    b"#1\t1\t1\t1\n"
    b"PATIENT_ID\tSEX\tOS_STATUS\tOS_MONTHS\n"
    b"P1\tFemale\t1:DECEASED\t12.5\n"
    b"P2\tMale\t0:LIVING\t[Not Available]\n"
)
BASE: Mapping[str, bytes] = {
    "meta_study.txt": STUDY,
    "meta_clinical_sample.txt": SAMPLE_META,
    "meta_clinical_patient.txt": PATIENT_META,
    "data_clinical_sample.txt": SAMPLE_DATA,
    "data_clinical_patient.txt": PATIENT_DATA,
}
"""A small study that imports: the study meta, both clinical metas and their data files."""

MAF = (
    b"Hugo_Symbol\tEntrez_Gene_Id\tTumor_Sample_Barcode\tVariant_Classification\n"
    b"TP53\t7157\tS1\tMissense_Mutation\n"
)
PATIENT_FILE = unicodedata.normalize("NFC", "data_clinical_patiént.txt")
TARGETS: Mapping[str, str] = {
    "S": "data_clinical_sample.txt",
    "P": PATIENT_FILE,
    "M": "meta_study.txt",
    "U": "data_mut.txt",
}
OWN: Mapping[str, str] = {"sample": "data_clinical_sample.txt", "patient": PATIENT_FILE}
METAS: Mapping[str, str] = {
    "sample": "meta_clinical_sample.txt",
    "patient": "meta_clinical_patient.txt",
}

P0 = "is not a file name M4.2a accepts"
NONE = "names no regular file in the study's directory"
SYMLINK = "is a symlink"
META = "names a meta file"
SAME = "names the other clinical meta file's data file"

PLAN_ROWS = 764
EXTRA_ROWS = 4

LENGTHS: Mapping[str, int] = {
    "pad255": 255,
    "pad256": 256,
    "single256": 256,
    "pad4224": 4_224,
}
"""The UTF-8 byte length each spelling is named for; ``spellings`` asserts it, so that a row
named for a boundary is at that boundary: 255 is the longest within the name cap, 256 the
shortest over it, and 4,224 a whole value over ``PATH_MAX`` (4,096) by more than a name."""
NAME_MAX = 255
"""The longest component of a name a Linux filesystem takes, in bytes."""


def _padded(target: str, size: int) -> str:
    """``target`` behind ``./`` steps, ``size`` UTF-8 bytes in all: ``./`` is two bytes, so an
    odd fill takes one ``.//`` step (three bytes) first."""
    fill = size - len(target.encode())
    head = ".//" if fill % 2 else ""
    assert fill >= len(head)
    return head + "./" * ((fill - len(head)) // 2) + target


def spellings(directory: Path, target: str) -> dict[str, tuple[str, str | None]]:
    """Each spelling of ``target``, by name, with what the names rule makes of it: ``None`` where
    it is accepted (its normal form is ``target``), else the reason it is refused."""
    found: dict[str, tuple[str, str | None]] = {
        "x": (target, None),
        "dot1": ("./" + target, None),
        "dot2": ("././" + target, None),
        "dsl": (".//" + target, None),
        "pad255": (_padded(target, 255), None),
        "d_up": ("d/../" + target, P0),
        "nod_up": ("nod/../" + target, P0),
        "slash": (target + "/", P0),
        "slashdot": (target + "/.", P0),
        "dot_only": (".", P0),
        "abs": (str(directory.resolve() / target), P0),
        "dabs": ("/" + str(directory.resolve() / target), P0),
        "nul": (target + "\0", P0),
        "pad256": (_padded(target, 256), P0),
        "single256": ("a" * 252 + ".txt", P0),
        "pad4224": (_padded(target, 4_224), P0),
        "upper": (target.upper(), NONE),
        "nfd": (unicodedata.normalize("NFD", target), NONE),
        "link": ("link/" + target, NONE),
        "sub": ("sub/" + target, NONE),
        "alias": ("alias.txt", SYMLINK),
        "empty": ("", P0),
    }
    if found["nfd"][0] == target:
        del found["nfd"]
    for name, size in LENGTHS.items():
        assert len(found[name][0].encode()) == size, name
    for name, (value, reason) in found.items():
        if reason is None:
            assert posixpath.normpath(value) == target, name
    return found


def _base(directory: Path, target: str) -> None:
    directory.mkdir(parents=True)
    for name, content in BASE.items():
        (directory / name).write_bytes(content)
    (directory / "data_clinical_patient.txt").rename(directory / PATIENT_FILE)
    _set(directory, "patient", PATIENT_FILE)
    (directory / "data_mut.txt").write_bytes(MAF)
    (directory / "d").mkdir()
    os.symlink(".", directory / "link")
    os.symlink(target, directory / "alias.txt")
    os.link(directory / target, directory / "hard.txt")
    (directory / "sub").mkdir()
    shutil.copy(directory / target, directory / "sub" / target)


def _set(directory: Path, role: str, value: str) -> None:
    """The ``role``'s clinical meta, with ``value`` as its ``data_filename``."""
    meta = directory / METAS[role]
    lines = [line for line in meta.read_bytes().split(b"\n") if line]
    kept = [line for line in lines if not line.startswith(b"data_filename:")]
    meta.write_bytes(b"\n".join([*kept, b"data_filename: " + value.encode()]) + b"\n")


def _outcome(
    values: Mapping[str, tuple[str, str | None]],
) -> dict[str, object]:
    """The expected outcome, by the plan's rule, of the two clinical metas' values: each role's
    target and the reason it is refused (``None`` when its normal form names the target)."""
    chosen: dict[str, str] = {}
    for role in ("patient", "sample"):  # the order of the metas' names
        target, reason = values[role]
        if reason is None and target == "meta_study.txt":
            reason = META
        if reason is not None:
            return {"refused": "onco.DATA_FILE", "meta": METAS[role], "reason": reason}
        chosen[role] = target
    if chosen["patient"] == chosen["sample"]:
        return {"refused": "onco.DATA_FILE", "meta": METAS["patient"], "reason": SAME}
    return {"sample": chosen["sample"], "patient": chosen["patient"]}


def build(root: Path) -> dict[str, dict[str, object]]:
    """The grid, built under ``root``: each directory's name to its row."""
    rows: dict[str, dict[str, object]] = {}
    count = 0
    for key, target in TARGETS.items():
        for spelling in spellings(root / "g", target):
            for role in ("sample", "patient", "study_df"):
                if role == "study_df":
                    axes: dict[str, str] = {"own_x": "", "own_dot": "./"}
                else:
                    other = OWN["patient" if role == "sample" else "sample"]
                    axes = {"own_x": other, "own_dot": "./" + other}
                    if target != other:
                        axes.update({"tgt_x": target, "tgt_dot": "./" + target})
                for axis, second in axes.items():
                    count += 1
                    name = f"g{count:05d}"
                    directory = root / name
                    _base(directory, target)
                    value, reason = spellings(directory, target)[spelling]
                    if role == "study_df":
                        study = directory / "meta_study.txt"
                        study.write_bytes(STUDY + b"data_filename: " + value.encode() + b"\n")
                        for each in ("sample", "patient"):
                            _set(directory, each, second + OWN[each])
                        values = {each: (OWN[each], None) for each in ("sample", "patient")}
                    else:
                        other_role = "patient" if role == "sample" else "sample"
                        _set(directory, role, value)
                        _set(directory, other_role, second)
                        named = second.removeprefix("./")
                        values = {role: (target, reason), other_role: (named, None)}
                    rows[name] = {
                        "plan": True,
                        "role": role,
                        "spelling": spelling,
                        "target": key,
                        "axis": axis,
                        "expected": _outcome(values),
                    }
    for tag, change, expected in EXTRA:
        count += 1
        name = f"e{count:05d}"
        directory = root / name
        _base(directory, TARGETS["S"])
        change(directory)
        rows[name] = {
            "plan": False,
            "role": "sample",
            "spelling": tag,
            "target": "S",
            "axis": "-",
            "expected": expected,
        }
    return rows


def _fold_pair(directory: Path) -> None:
    (directory / "DATA_CLINICAL_SAMPLE.txt").write_bytes(PATIENT_DATA)
    _set(directory, "patient", "DATA_CLINICAL_SAMPLE.txt")


def _named_excluded(name: str) -> Callable[[Path], None]:
    def change(directory: Path) -> None:
        (directory / name).write_bytes(SAMPLE_DATA)
        _set(directory, "sample", name)

    return change


def _single_255(directory: Path) -> None:
    """A data file whose whole name is one 255-byte component, the longest within the cap, named
    by the sample meta."""
    name = "b" * 251 + ".txt"
    assert len(name.encode()) == NAME_MAX
    _named_excluded(name)(directory)


EXTRA: tuple[tuple[str, Callable[[Path], None], dict[str, object]], ...] = (
    (
        "fold_pair_two_metas",
        _fold_pair,
        {"sample": "data_clinical_sample.txt", "patient": "DATA_CLINICAL_SAMPLE.txt"},
    ),
    (
        "hidden_meta_named",
        _named_excluded(".meta_hidden.txt"),
        {"sample": ".meta_hidden.txt", "patient": PATIENT_FILE},
    ),
    ("single_255", _single_255, {"sample": "b" * 251 + ".txt", "patient": PATIENT_FILE}),
    (
        "oncokb_backup_meta_named",
        _named_excluded("ONCOKB_IMPORT_BACKUP_meta_x.txt"),
        {"sample": "ONCOKB_IMPORT_BACKUP_meta_x.txt", "patient": PATIENT_FILE},
    ),
)
"""The rows beyond the spellings: two metas naming fold-equal distinct files import on Linux, an
excluded meta-named entry is no member of Meta(S), so a clinical meta may name it, and a listed
file whose name is one component of 255 bytes is accepted (its twin of 256 is ``single256``)."""


def main(arguments: list[str]) -> int:
    root = Path(arguments[0])
    root.mkdir(parents=True)
    rows = build(root)
    (root / "manifest.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    plan = sum(1 for row in rows.values() if row["plan"])
    print(f"{plan} plan rows, {len(rows) - plan} extra rows")
    return 0 if (plan, len(rows) - plan) == (PLAN_ROWS, EXTRA_ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
