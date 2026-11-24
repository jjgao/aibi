"""The MAF's coverage held to an independent predictor (SPEC D448, M4.2b-2b-ii slice B).

The study's truth is written in ``coverage_predictor.py``, which imports nothing of ``aibi``: each
sample's gene panel matrix cell (one sample per kind the owner's D7 leaves out), the definitions,
each row's symbol, class, status and amino-acid change, the patients. This module writes that truth
as a cBioPortal study, imports it through the real pack with and without its ``gene_panels/``,
and holds, on every case of the predictor's grammar (unit x lift x quantifier lists x ``covered``
with and without a scope x lookup leaves x ``negate`` x ``not``), before and after the coverage:

- the evaluator's answer, its reasons and flags, unit by unit, to the predictor's;
- the SQL compiler's to the evaluator's after the coverage, by both of D449's paths (main's and
  the one-group path, forced as ``tests/core/engine/forcing.py`` forces them), through the
  server's worker (``run_cohorts``), the cases batched into one worker run per batch;
- ``each: category`` of the class at both units (each listed category's answer is the
  predictor's ``class in [c]``, a fresh one's the default), and the SQL materialisation to the
  reference's;
- the predictor module's imports (none of ``aibi``) and the owner-facing table
  (``coverage_consequences.md``) to the predictor (a staleness test, as for ``schemas/``).

*k* is not an axis: a per-unit answer does not depend on it (the analyses' refusals and withheld
forms under *k* are held in ``test_coverage.py``).
"""

import ast
import functools
import importlib.util
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from aibi.core.schema.pack_api import PackRegistry

HERE = Path(__file__).resolve().parent
TABLE = HERE / "coverage_consequences.md"
BATCH = 120
"""The cohorts of one worker run: fewer worker runs than cases, each well inside the limits."""


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def predictor() -> ModuleType:
    return _load("coverage_predictor_oracle", HERE / "coverage_predictor.py")


@functools.cache
def corpus() -> ModuleType:
    return _load("mutation_corpus_coverage", HERE / "mutation_corpus.py")


@functools.cache
def forcing() -> ModuleType:
    return _load("forcing_coverage", HERE.parents[1] / "core" / "engine" / "forcing.py")


def study_files(definitions: bool = True) -> dict[str, bytes]:
    """The predictor's truth as a cBioPortal study: the samples (with ``SAMPLE_TYPE``), the
    patients, the MAF, the ``_sequenced`` list, the matrix and, with ``definitions``, one file a
    definition in ``gene_panels/`` (the first with no final newline)."""
    p = predictor()
    c = corpus()
    samples = (
        "#Patient\tSample\tType\n#p\ts\tt\n#STRING\tSTRING\tSTRING\n#1\t1\t1\n"
        "PATIENT_ID\tSAMPLE_ID\tSAMPLE_TYPE\n"
        + "".join(f"{q}\t{s}\t{p.SAMPLE_TYPE[s]}\n" for q, ss in p.PATIENT.items() for s in ss)
    )
    patients = "#Patient\tSex\n#p\ts\n#STRING\tSTRING\n#1\t1\nPATIENT_ID\tSEX\n" + "".join(
        f"{q}\tF\n" for q in p.PATIENT
    )
    rows = [
        c.row(
            Tumor_Sample_Barcode=sample,
            Hugo_Symbol=symbol,
            Variant_Classification=kind,
            Mutation_Status=status,
            HGVSp_Short=change,
        )
        for sample, found in p.ROWS.items()
        for symbol, kind, status, change in found
    ]
    matrix = "SAMPLE_ID\tmutations\n" + "".join(f"{s}\t{cell}\n" for s, cell in p.MATRIX.items())
    files: dict[str, Any] = {
        "data_clinical_sample_DOT_txt": samples.encode(),
        "data_clinical_patient_DOT_txt": patients.encode(),
        "data_mutations_DOT_txt": c.maf(rows),
        "case_lists__cases_sequenced_DOT_txt": c.case_list("sequenced", list(p.ROWS)),
        "meta_gene_panel_matrix_DOT_txt": (
            f"cancer_study_identifier: {c.SID}\ngenetic_alteration_type: GENE_PANEL_MATRIX\n"
            "datatype: GENE_PANEL_MATRIX\ndata_filename: data_gene_panel_matrix.txt\n"
        ).encode(),
        "data_gene_panel_matrix_DOT_txt": matrix.encode(),
    }
    if definitions:
        for index, (panel, genes) in enumerate(p.DEFINITIONS.items()):
            body = f"stable_id: {panel}\ndescription: a panel\ngene_list: " + "\t".join(genes)
            files[f"gene_panels__data_gene_panel_{panel}_DOT_txt"] = (
                body if index == 0 else body + "\n"
            ).encode()
    return c.study(**files)


def write(world: Any, name: str, files: Mapping[str, Any]) -> Path:
    directory = world.root / "imports" / name
    for path, content in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
    return directory


def published(world: Any, registry: PackRegistry, name: str, definitions: bool) -> Any:
    found = world.publish(name, write(world, name, study_files(definitions)), registry, pack="onco")
    return found.manifest


def rendered(value: Any) -> str:
    """An engine truth value as the predictor renders its answers."""
    p = predictor()
    truth = {"TRUE": p.T, "FALSE": p.F, "UNKNOWN": p.U}[value.value.value]
    reasons = frozenset(str(r.value) for r in value.reasons)
    flags = frozenset(str(f.value) for f in value.flags)
    return p.render(p.V(truth, reasons, flags))


def resolved(release: Any, clause: Mapping[str, Any], unit: str, registry: PackRegistry) -> Any:
    """The case's one cohort, resolved, or its refusal codes, comma-joined."""
    from aibi.core.engine.resolve import resolve
    from aibi.core.schema.loading import load_document

    written = {"aibi": "1", "dataset": "d", "unit": unit, "cohorts": {"c": {"all": [clause]}}}
    loaded = load_document(json.dumps(written))
    assert loaded.document is not None, loaded.refusals
    found = resolve(loaded.document, {"d": release}, loaded.positions, registry=registry)
    if found.refusals:
        return ",".join(sorted({str(r.code) for r in found.refusals}))
    return found.cohorts["c"]


def sql_values(
    cohorts: Sequence[Any], sources: Mapping[str, Any], path: str, monkeypatch: pytest.MonkeyPatch
) -> list[Any]:
    """Each cohort's truth values by the SQL compiler taking ``path``, through the worker, in
    batches of ``BATCH`` cohorts a worker run."""
    from aibi.core.engine import sql
    from aibi.core.engine.queries import run_cohorts
    from aibi.core.engine.worker import Workers
    from aibi.core.schema.limits import QueryLimits

    workers = Workers(QueryLimits())
    found: list[Any] = []
    with monkeypatch.context() as patch:
        patch.setattr(sql, "_Compiler", forcing().forced_compiler(path))
        for start in range(0, len(cohorts), BATCH):
            part = cohorts[start : start + BATCH]
            counted = run_cohorts(part, sources, workers, values=True)
            found.extend(c.values for c in counted)
    return found


def test_the_predictor_imports_nothing_of_aibi() -> None:
    """The predictor imports the standard library alone (no ``aibi``, no test module), so that a
    constant of the pack's (D7's words, the missing spellings) cannot pass both sides."""
    tree = ast.parse((HERE / "coverage_predictor.py").read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import"
            assert node.module is not None
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("__import__", "exec", "eval"), node.func.id
    assert names == {"itertools", "collections", "dataclasses", "typing"}, names


def test_the_owner_table_is_the_predictor_s() -> None:
    """``coverage_consequences.md`` is what ``coverage_predictor.table()`` writes (regenerate it
    with ``uv run python -c`` over that function after a change of the predictor or its truth)."""
    assert TABLE.read_text() == predictor().table()


def test_the_engine_is_the_predictor_s(
    world: Any, registry: PackRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every case of the grammar, before and after the coverage: the evaluator equal to the
    predictor (answer, reasons, flags; refusal codes), and after the coverage the SQL compiler
    equal to the evaluator by both paths, through the worker (before it, the release has no
    coverage of the pack's and the engine's own differential holds the compiler; a compile is
    most of this test's time)."""
    from aibi.core.engine.evaluate import evaluate

    p = predictor()
    started = time.monotonic()
    totals = {"cases": 0, "refused": 0, "answers": 0}
    for definitions in (False, True):
        manifest = published(world, registry, "after" if definitions else "before", definitions)
        release = world.store.load(manifest)
        sources = {manifest: world.store.sources(manifest)}
        cohorts: list[Any] = []
        expected: list[tuple[Any, ...]] = []
        for case in p.cases(definitions):
            totals["cases"] += 1
            found = resolved(release, case.clause, case.unit, registry)
            if isinstance(found, str) or case.refused is not None:
                assert found == case.refused, (case.shape, case.unit, found)
                totals["refused"] += 1
                continue
            values = evaluate(found).values
            table = release.rows(case.unit)
            key = "sample_id" if case.unit == "samples" else "patient_id"
            for row, value in enumerate(values):
                uid = str(table.cell(row, key).value)
                assert rendered(value) == p.render(case.answer(uid)), (
                    case.shape,
                    case.unit,
                    uid,
                )
                totals["answers"] += 1
            cohorts.append(found)
            expected.append(tuple(values))
        for path in ("main", "one_group") if definitions else ():
            got = sql_values(cohorts, sources, path, monkeypatch)
            mismatched = [
                i for i, (a, b) in enumerate(zip(got, expected, strict=True)) if tuple(a) != b
            ]
            assert mismatched == [], (path, len(mismatched))
    minutes = (time.monotonic() - started) / 60
    print(f"coverage oracle: {totals} in {minutes:.2f} minutes")
    assert totals["cases"] == 2 * sum(1 for _ in p.cases(True))


def test_each_category_of_the_class_is_the_predictor_s(
    world: Any, registry: PackRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``each: category`` of ``variant_classification`` at the samples and at the patients (the
    default lift and ``assessed``): each listed category's per-unit answer is the predictor's
    ``class in [c]``, a fresh category's (the default) too; the SQL materialisation equals the
    reference's by both paths after the coverage."""
    from aibi.core.engine import memberships, sql
    from aibi.core.engine.evaluate import evaluate
    from aibi.core.engine.queries import run_views
    from aibi.core.engine.resolve import ViewVariable, resolve
    from aibi.core.engine.worker import Workers
    from aibi.core.schema.analyses import Variable
    from aibi.core.schema.limits import QueryLimits
    from aibi.core.schema.loading import load_document

    p = predictor()
    checked = 0
    for definitions in (False, True):
        manifest = published(
            world, registry, "each_after" if definitions else "each_before", definitions
        )
        release = world.store.load(manifest)
        sources = {manifest: world.store.sources(manifest)}
        for unit, lift in (("samples", None), ("patients", None), ("patients", "assessed")):
            written = {"aibi": "1", "dataset": "d", "unit": unit, "cohorts": {"c": {"all": []}}}
            loaded = load_document(json.dumps(written))
            assert loaded.document is not None
            asked: dict[str, Any] = {
                "column": "mutations.variant_classification",
                "each": "category",
            }
            if lift is not None:
                asked["lift"] = lift
            given = [
                ViewVariable(
                    "0/0", "d", ("views", 0, "params", "columns", 0), Variable.model_validate(asked)
                )
            ]
            found = resolve(
                loaded.document,
                {"d": release},
                loaded.positions,
                registry=registry,
                variables=given,
            )
            assert found.refusals == [], found.refusals
            variable = found.variables["0/0"]
            answers = memberships.evaluate(variable)
            cohort = found.cohorts["c"]
            members = evaluate(cohort).members
            listed, _ = memberships.listing(answers, members)
            table = release.rows(unit)
            key = "sample_id" if unit == "samples" else "patient_id"
            for category in (*listed, "Frame_Shift_Del"):
                where = [("value", "variant_classification", (category,), False)]
                for row in range(len(answers.defaults)):
                    uid = str(table.cell(row, key).value)
                    value = (
                        answers.answer(row, category)
                        if category in listed
                        else answers.defaults[row]
                    )
                    if unit == "samples":
                        expected = p.sample_q(where, "some", 1, uid, definitions)
                    else:
                        expected = p.patient_q(
                            where, "some", "some", lift or "strict", uid, definitions
                        )
                    truth = {"TRUE": p.T, "FALSE": p.F, "UNKNOWN": p.U}[value.value.value]
                    got = p.V(
                        truth,
                        frozenset(str(r.value) for r in value.reasons),
                        frozenset(str(m.flag.value) for m in value.marks),
                    )
                    assert p.render(got) == p.render(expected), (unit, lift, category, uid)
                    checked += 1
            reference = memberships.materialise_over(
                [variable], [memberships.evaluated(variable)], members
            )
            for path in ("main", "one_group") if definitions else ("auto",):
                with monkeypatch.context() as patch:
                    patch.setattr(sql, "_Compiler", forcing().forced_compiler(path))
                    ran = run_views(
                        [], [], [([cohort], [variable])], sources, Workers(QueryLimits())
                    )
                assert ran.materialised[0].materialised == (reference,), (unit, lift, path)
    assert checked > 0


def test_d448_s_patient_unit_sentences_hold_over_the_predictor() -> None:
    """D448's owner-facing patient-unit sentences (errata v1), each checked over the predictor's
    every patient-unit case after the coverage (the predictor being held equal to the engine):
    no question whose outer quantifier is ``some`` (or a count) closes FALSE, nor its negation
    TRUE, under either lift; ``[every, some]`` closes FALSE for some patients; ``covered`` closes
    FALSE under ``strict`` for some and never closes under ``assessed``; ``assessed`` answers
    NOT_COVERED where every sample is dropped; and for survival's "TP53-mutant or not" the second
    arm (``not exists TP53``) holds no patient before or after, the first losing the patients
    whose only TP53 rows have an empty or missing-coded status."""
    p = predictor()
    before = {(c.unit, c.shape): c for c in p.cases(False)}
    every_false, covered_false = set(), set()
    for case in p.cases(True):
        if case.unit != "patients" or case.refused is not None:
            continue
        negated = case.shape.startswith("not ")
        for patient in p.PATIENT:
            answer = p.render(case.answer(patient))
            if "exists[" in case.shape:
                outer = case.shape.split("[", 1)[1].split(",", 1)[0]
                if outer.startswith("some"):
                    closed = "TRUE" if negated else "FALSE"
                    assert not answer.startswith(closed), (case.shape, patient, answer)
                if outer == "every" and not negated and answer.startswith("FALSE"):
                    every_false.add(patient)
            elif "assessed" in case.shape:
                assert answer.startswith("U("), (case.shape, patient, answer)
            elif not negated and answer.startswith("FALSE"):
                covered_false.add(patient)
    assert every_false
    assert covered_false
    shape = "exists[some,some; strict; nested](hugo_symbol=TP53)"
    arms: list[set[str]] = []
    for after in (False, True):
        cases = {c.shape: c for c in p.cases(after) if c.unit == "patients"}
        negation = cases["not " + shape]
        assert [q for q in p.PATIENT if p.render(negation.answer(q)) == "TRUE"] == []
        arms.append({q for q in p.PATIENT if p.render(cases[shape].answer(q)) == "TRUE"})
    assert arms[1] <= arms[0]
    assert arms[0] - arms[1] == {"Q5", "Q8"}
    assert before
