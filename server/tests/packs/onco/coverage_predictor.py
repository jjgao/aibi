"""The predictor of the MAF's coverage (SPEC D448, M4.2b-2b-ii slice B): what a question over the
``mutations`` rows answers, at the sample and the patient unit, before and after the coverage the
pack builds from a study's gene panel definitions, written from SPEC §6.5 (steps 1, 4, 5 and 7),
§6.3's three-valued logic and the coverage's rule as D448 states it.

This is not a test: pytest does not collect it, and it imports nothing of ``aibi`` (neither the
engine nor the pack, which ``test_coverage_oracle`` holds by its AST), so that a mutant of the
pack's coverage (an assignment that takes a cell with no definition, a groups table that drops a
gene, a missing-coded status put in the filter) cannot pass both sides. Its inputs are the
fixture's truth, written here as literals: each sample's matrix cell and the definitions (the
owner's D7 cells left out), each row's symbol, class, status and amino-acid change with the
spellings of no value, the patients' samples, and the filter the rows imply.

``test_coverage_oracle`` imports the study ``files`` this truth gives through the real pack and
holds the engine (the evaluator, and the SQL compiler by both of D449's paths) equal to the
predictor on every case of ``cases``; ``table`` writes the owner-facing table of the answers that
change (``coverage_consequences.md``), which a test holds equal to this module.
"""

import itertools
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

# --- The fixture's truth ----------------------------------------------------------------------

M, SIL, SOM = "Missense_Mutation", "Silent", "Somatic"
Row = tuple[str, str, str, str]
"""A MAF row: (Hugo_Symbol, Variant_Classification, Mutation_Status, HGVSp_Short)."""

ROWS: dict[str, list[Row]] = {
    "S1": [("TP53", M, SOM, "p.R175H")],
    "S2": [("KRAS", M, SOM, "p.G12D")],
    "S3": [("TP53", M, "", "p.R175H")],
    "S4": [("EGFR", M, SOM, "p.L858R")],
    "S5": [],
    "S6": [("TP53", SIL, "None", "p.P72P")],
    "S7": [("Unknown", M, SOM, "p.X1Y")],
    "S8": [("TP53", M, SOM, "p.R273H")],
    "S9": [],
    "S10": [("KRAS", SIL, SOM, "p.G12G")],
    "S11": [("TP53", M, "UNKNOWN", "p.R175H")],
    "S12": [("TP53", M, SOM, "p.R248Q"), ("TP53", SIL, SOM, "p.P72P")],
    "S13": [("TP53", "NA", SOM, "p.R175H")],
    "S14": [("TP53", M, SOM, "p.R175H"), ("KRAS", M, "", "p.G12D")],
    "S15": [("KRAS", M, SOM, "p.G12V")],
    "S16": [],
    "S17": [],
    "S18": [("TP53", M, SOM, "p.R175H")],
    "S19": [],
    "S20": [("TP53", SIL, "None", "p.P72P")],
    "S21": [("TP53", M, SOM, "p.R175H")],
    "S22": [],
    "S23": [("KRAS", M, SOM, "p.G13D")],
    "S24": [],
    "S25": [],
    "S26": [("TP53", M, SOM, "p.R248W")],
    "S27": [("TP53", M, "UNCALLED", "p.R175H")],
    "S28": [("KRAS", M, "LOH", "p.G12C")],
    "S29": [("TP53", SIL, "Wildtype", "p.P72P")],
}
NOT_IN_MATRIX = frozenset({"S23"})
"""Samples the gene panel matrix has no row for."""
MATRIX: dict[str, str] = {s: "P1" for s in ROWS if s not in NOT_IN_MATRIX} | {
    "S8": "NA",
    "S9": "WXS",
    "S10": "P2",
    "S17": "P2",
    "S19": "NA",
    "S20": "P2",
    "S21": "WXS",
    "S22": "P9",
    "S24": "p1",
    "S25": "WGS",
    "S26": "WXS/WGS",
}
"""Each sample's ``mutations`` cell: one sample per D7 kind (``NA``, ``WGS``, ``WXS``,
``WXS/WGS``, an undefined id ``P9``, ``p1``, which differs from ``P1`` only in case)."""
DEFINITIONS: dict[str, list[str]] = {"P1": ["TP53", "KRAS"], "P2": ["KRAS"], "P3": ["EGFR", "BRAF"]}
"""The definitions, each in a file of its own; ``P3`` is used by no sample."""
PATIENT: dict[str, list[str]] = {
    "Q1": ["S2", "S9"],
    "Q2": ["S5", "S10"],
    "Q3": ["S1"],
    "Q4": ["S8"],
    "Q5": ["S3"],
    "Q6": ["S4"],
    "Q7": ["S6", "S7"],
    "Q8": ["S11"],
    "Q9": ["S12", "S13"],
    "Q10": ["S14"],
    "Q11": ["S15", "S16"],
    "Q12": ["S17"],
    "Q13": ["S18", "S19"],
    "Q14": [],
    "Q15": ["S20", "S22"],
    "Q16": ["S21"],
    "Q17": ["S23"],
    "Q18": ["S24", "S25"],
    "Q19": ["S26"],
    "Q20": ["S27"],
    "Q21": ["S28", "S29"],
}
SAMPLE_TYPE = {s: ("Metastasis" if int(s[1:]) % 3 == 0 else "Primary") for s in ROWS}
MISSING: dict[str, frozenset[str]] = {
    "hugo_symbol": frozenset({"", "NA", "Unknown"}),
    "variant_classification": frozenset({"", "NA", "UNKNOWN", "Unknown", "unknown", "null"}),
    "mutation_status": frozenset({"", "NA", "UNKNOWN", "Unknown", "unknown", "null"}),
}
"""The spellings of no value this truth's cells use, by column: the MAF's ``NA`` and ``Unknown``
for a symbol, and for a class or a status an empty cell and spellings of D444's family."""
COLUMN = {"hugo_symbol": 0, "variant_classification": 1, "mutation_status": 2, "hgvsp_short": 3}
NO_DEFINITION_WORDS = frozenset({"NA", "WGS", "WXS", "WXS/WGS"})
STATES: dict[str, str] = {
    "S1": "P1, a TP53 Missense Somatic row",
    "S2": "P1, a KRAS row",
    "S3": "P1, a TP53 Missense row with an empty status",
    "S4": "P1, an EGFR row (off the panel)",
    "S5": "P1, no row",
    "S6": "P1, a TP53 Silent row of status None",
    "S7": "P1, a row of symbol Unknown",
    "S8": "NA, a TP53 row",
    "S9": "WXS, no row",
    "S10": "P2 (KRAS only), a KRAS Silent row",
    "S11": "P1, a TP53 Missense row of status UNKNOWN",
    "S12": "P1, a TP53 Missense and a TP53 Silent row",
    "S13": "P1, a TP53 row of class NA",
    "S14": "P1, TP53 Missense Somatic and KRAS with an empty status",
    "S15": "P1, a KRAS row",
    "S16": "P1, no row",
    "S17": "P2, no row",
    "S18": "P1, a TP53 row",
    "S19": "NA, no row",
    "S20": "P2, a TP53 Silent row of status None (off the panel)",
    "S21": "WXS, a TP53 row",
    "S22": "an undefined panel P9, no row",
    "S23": "no gene panel matrix row, a KRAS row",
    "S24": "p1 (P1 in another case), no row",
    "S25": "WGS, no row",
    "S26": "WXS/WGS, a TP53 row",
    "S27": "P1, a TP53 Missense row of status UNCALLED",
    "S28": "P1, a KRAS row of status LOH",
    "S29": "P1, a TP53 Silent row of status Wildtype",
}
"""Each sample's state, as the owner-facing table names it."""


def implied_filter() -> dict[str, list[str]]:
    """The filter the rows imply: each filtered column's present values."""
    found: dict[str, set[str]] = {"variant_classification": set(), "mutation_status": set()}
    for rows in ROWS.values():
        for row in rows:
            for column, values in found.items():
                if row[COLUMN[column]] not in MISSING[column]:
                    values.add(row[COLUMN[column]])
    return {column: sorted(values) for column, values in found.items()}


FILTER = implied_filter()


def assigned(sample: str) -> str | None:
    """A sample's panel: its matrix cell, where that is a defined panel as written."""
    cell = MATRIX.get(sample)
    if cell is None or cell in NO_DEFINITION_WORDS or cell not in DEFINITIONS:
        return None
    return cell


# --- Answers ------------------------------------------------------------------------------------

T, F, U = "TRUE", "FALSE", "UNKNOWN"


@dataclass(frozen=True)
class V:
    """An answer: its truth, its reasons (UNKNOWN) and its flags."""

    t: str
    r: frozenset[str] = frozenset()
    f: frozenset[str] = frozenset()


def of(t: str, reasons: Any = (), flags: Any = ()) -> V:
    return V(t, frozenset(reasons), frozenset(flags))


def render(v: V) -> str:
    """An answer as text: ``TRUE``, ``FALSE`` or ``U(<reasons>)``, then ``+<flag>`` for each."""
    found = v.t if v.t != U else "U(" + ",".join(sorted(v.r)) + ")"
    for flag in sorted(v.f):
        found += "+" + flag
    return found


def conj(*xs: V) -> V:
    if any(x.t == F for x in xs):
        return of(F)
    unknown = [x for x in xs if x.t == U]
    if unknown:
        return of(U, frozenset().union(*(x.r for x in unknown)))
    return of(T)


def disj(*xs: V) -> V:
    if any(x.t == T for x in xs):
        return of(T)
    unknown = [x for x in xs if x.t == U]
    if unknown:
        return of(U, frozenset().union(*(x.r for x in unknown)))
    return of(F)


def neg(x: V) -> V:
    return V({T: F, F: T, U: U}[x.t], x.r, x.f)


Clause = tuple[Any, ...]
"""``("value", column, values, negate)`` on a ``mutations`` column, or ``("sample_type",
values)``, a lookup up to the row's sample."""


def leaf(row: Row, sample: str, clause: Clause) -> V:
    """A ``where`` leaf on a row (§6.3): a cell spelled as no value is UNKNOWN(NO_INFORMATION)."""
    if clause[0] == "value":
        _, column, values, negate = clause
        cell = row[COLUMN[column]]
        if column in MISSING and cell in MISSING[column]:
            return of(U, {"NO_INFORMATION"})
        return of(T if (cell in values) != negate else F)
    return of(T if SAMPLE_TYPE[sample] in clause[1] else F)


def closed_sample(
    sample: str, after: bool, every: bool, admitted: set[str] | None
) -> tuple[bool, bool]:
    """Whether a sample's ``mutations`` are closed for a question (§6.5 step 4), and whether a
    FALSE carries ``SCOPE_PARTIAL`` (the question restricts no gene): never before the coverage;
    after it, only where the sample is assigned and its panel lists every admitted gene, or any
    gene where none is admitted or the quantifier is ``every``."""
    if not after:
        return False, False
    panel = assigned(sample)
    if panel is None:
        return False, False
    genes = set(DEFINITIONS[panel])
    if every or admitted is None:
        return bool(genes), True
    return admitted <= genes, False


def admitted_of(where: list[Clause]) -> set[str] | None:
    """The genes a ``where`` admits: the intersection of its non-negated symbol leaves' values."""
    genes = [set(c[2]) for c in where if c[0] == "value" and c[1] == "hugo_symbol" and not c[3]]
    return set.intersection(*genes) if genes else None


def sample_q(where: list[Clause], quant: str, k: int, sample: str, after: bool) -> V:
    """An existence question at a sample (steps 1, 4, 5, 7): ``some`` (at least ``k`` rows) or
    ``every``; the filter applies after the coverage (each row's filtered cells must be among the
    filter's values: a row whose cell is spelled as no value is UNKNOWN)."""
    rows = ROWS[sample]
    closed, partial = closed_sample(sample, after, quant == "every", admitted_of(where))
    reason = "NOT_COVERED" if after else "NO_INFORMATION"

    def filtered(row: Row) -> V:
        if not after:
            return of(T)
        return conj(*(leaf(row, sample, ("value", c, v, False)) for c, v in FILTER.items()))

    def holds(row: Row) -> V:
        return conj(*(leaf(row, sample, c) for c in where)) if where else of(T)

    def unknown(reasons: set[str]) -> V:
        return of(U, reasons | ({reason} if not closed else set()))

    flags = {"SCOPE_PARTIAL"} if partial else set()
    if quant == "some":
        found = [conj(holds(row), filtered(row)) for row in rows]
        true = [v for v in found if v.t == T]
        maybe = [v for v in found if v.t == U]
        if len(true) >= k:
            return of(T)
        if len(true) + len(maybe) >= k:
            return unknown(set().union(*(v.r for v in maybe)))
        if closed:
            return of(F, flags=flags)
        return unknown(set())
    found = [disj(neg(filtered(row)), holds(row)) for row in rows]
    if any(v.t == F for v in found):
        return of(F)
    if not rows:
        return unknown({"NO_ROWS"})
    maybe = [v for v in found if v.t == U]
    if maybe:
        return unknown(set().union(*(v.r for v in maybe)))
    if closed:
        return of(T, flags=flags)
    return unknown(set())


DROP = {
    "strict": frozenset({"OUT_OF_SCOPE"}),
    "assessed": frozenset({"OUT_OF_SCOPE", "NOT_COVERED"}),
}
"""The reasons a lift drops a child for (§6.5 step 7)."""


def lifted(children: list[V], lift: str) -> tuple[list[V], list[V]]:
    kept = [c for c in children if not (c.t == U and c.r <= DROP[lift])]
    dropped = [c for c in children if c.t == U and c.r <= DROP[lift]]
    return kept, dropped


def flags_of(vs: list[V]) -> frozenset[str]:
    return frozenset().union(*(v.f for v in vs)) if vs else frozenset()


Quantifier = Any
"""``"some"``, ``"every"`` or ``{"some": k}``."""


def patient_q(
    where: list[Clause], outer: Quantifier, inner: Quantifier, lift: str, patient: str, after: bool
) -> V:
    """patient -> samples (no coverage declared: never closed, NO_INFORMATION) -> mutations."""
    k_inner = inner["some"] if isinstance(inner, dict) else 1
    children = [
        sample_q(where, "every" if inner == "every" else "some", k_inner, s, after)
        for s in PATIENT[patient]
    ]
    kept, dropped = lifted(children, lift)
    true = [v for v in kept if v.t == T]
    false = [v for v in kept if v.t == F]
    maybe = [v for v in kept if v.t == U]

    def unknown(reasons: set[str]) -> V:
        return of(U, reasons | {"NO_INFORMATION"}, flags_of(maybe + dropped))

    if outer == "every":
        if false:
            return of(F, flags=flags_of(false))
        if not kept:
            return unknown({"NOT_COVERED"})
        if maybe:
            return unknown(set().union(*(v.r for v in maybe)))
        return unknown(set())
    k_outer = outer["some"] if isinstance(outer, dict) else 1
    if len(true) >= k_outer:
        return of(T, flags=flags_of(true))
    if len(true) + len(maybe) >= k_outer:
        return unknown(set().union(*(v.r for v in maybe)))
    return unknown({"NOT_COVERED"} if not kept else set())


def covered_sample(scope: list[str] | None, sample: str, after: bool) -> V:
    """``covered`` at a sample: TRUE where closed (``SCOPE_PARTIAL`` without a scope), else
    FALSE; UNKNOWN(NO_INFORMATION) before the coverage."""
    if not after:
        return of(U, {"NO_INFORMATION"})
    closed, partial = closed_sample(sample, after, False, set(scope) if scope else None)
    if closed:
        return of(T, flags={"SCOPE_PARTIAL"} if partial else set())
    return of(F)


def covered_patient(scope: list[str] | None, lift: str, patient: str, after: bool) -> V:
    children = [covered_sample(scope, s, after) for s in PATIENT[patient]]
    kept, dropped = lifted(children, lift)
    maybe = [v for v in kept if v.t == U]
    false = [v for v in kept if v.t == F]

    def unknown(reasons: set[str]) -> V:
        return of(U, reasons | {"NO_INFORMATION"}, flags_of(maybe + dropped))

    if not kept:
        return unknown({"NOT_COVERED"})
    if lift == "strict":
        if false:
            return of(F, flags=flags_of(false))
        if maybe:
            return unknown(set().union(*(v.r for v in maybe)))
        return unknown(set())
    return unknown(set().union(*(v.r for v in maybe)) if maybe else set())


def refused(where: list[Clause], every: bool, after: bool, scope: list[str] = ()) -> str | None:  # type: ignore[assignment]
    """The refusal codes a question gets, comma-joined, or ``None``: after the coverage a symbol
    leaf under ``every`` or negated is ``SCOPE_COLUMN_MENTION``, a class or status leaf negated
    or naming a value outside the filter is ``FILTER_COLUMN_MENTION``; a ``covered`` scope on a
    column that is no scope column is ``UNKNOWN_SCOPE_COLUMN`` (before, any scope is)."""
    found: set[str] = set()
    if not after:
        return "UNKNOWN_SCOPE_COLUMN" if scope else None
    symbol = [c for c in where if c[0] == "value" and c[1] == "hugo_symbol"]
    if every and symbol:
        found.add("SCOPE_COLUMN_MENTION")
    for c in where:
        if c[0] == "value" and c[1] in FILTER and (c[3] or not set(c[2]) <= set(FILTER[c[1]])):
            found.add("FILTER_COLUMN_MENTION")
        if c[0] == "value" and c[1] == "hugo_symbol" and c[3]:
            found.add("SCOPE_COLUMN_MENTION")
    if any(column != "hugo_symbol" for column in scope):
        found.add("UNKNOWN_SCOPE_COLUMN")
    return ",".join(sorted(found)) or None


# --- The grammar ---------------------------------------------------------------------------------

SYMBOL: list[Clause | None] = [
    None,
    ("value", "hugo_symbol", ("TP53",), False),
    ("value", "hugo_symbol", ("KRAS",), False),
    ("value", "hugo_symbol", ("TP53", "KRAS"), False),
    ("value", "hugo_symbol", ("TP53", "GENEX"), False),
]
KIND: list[Clause | None] = [
    None,
    ("value", "variant_classification", (M,), False),
    ("value", "variant_classification", ("Frame_Shift_Del",), False),
]
STATUS: list[Clause | None] = [None, ("value", "mutation_status", (SOM,), False)]
EXTRA: list[Clause] = [
    ("sample_type", ("Primary",)),
    ("value", "hgvsp_short", ("p.R175H",), True),
    ("value", "variant_classification", (SIL,), True),
    ("value", "hugo_symbol", ("TP53",), True),
]
QUANTIFIERS: list[tuple[Quantifier, Quantifier]] = [
    ("some", "some"),
    ("every", "some"),
    ("some", "every"),
    ("every", "every"),
    ({"some": 2}, "some"),
    ("some", {"some": 2}),
]
SCOPES: list[Any] = [
    None,
    ["TP53"],
    ["KRAS"],
    ["TP53", "KRAS"],
    ["TP53", "GENEX"],
    ["GENEX"],
    "class",
]


def wheres() -> list[list[Clause]]:
    found: list[list[Clause]] = []
    for symbol, kind, status in itertools.product(SYMBOL, KIND, STATUS):
        found.append([c for c in (symbol, kind, status) if c is not None])
    for extra in EXTRA:
        for symbol in (None, SYMBOL[1]):
            found.append([c for c in (symbol, extra) if c is not None])
    return found


def leaf_document(clause: Clause) -> dict[str, Any]:
    if clause[0] == "value":
        found: dict[str, Any] = {
            "kind": "value",
            "column": "mutations." + clause[1],
            "values": list(clause[2]),
        }
        if clause[3]:
            found["negate"] = True
        return found
    return {"kind": "value", "column": "samples.sample_type", "values": list(clause[1])}


def describe(where: list[Clause]) -> str:
    """A ``where`` as the owner's table writes it."""
    if not where:
        return "any row"
    parts = []
    for c in where:
        if c[0] == "sample_type":
            parts.append("sample_type=" + "|".join(c[1]))
        else:
            parts.append(("not " if c[3] else "") + c[1] + "=" + "|".join(c[2]))
    return " & ".join(parts)


@dataclass(frozen=True)
class Case:
    """A question at a unit, as a document clause, and what it answers: ``refused`` (codes) or
    ``answer(unit id)``; ``shape`` names it in the owner's table."""

    unit: str
    clause: dict[str, Any]
    after: bool
    shape: str
    refused: str | None
    answer: Callable[[str], V]


def cases(after: bool) -> Iterator[Case]:
    """Every case of the grammar, before (``after`` false) or after the coverage: at the sample
    unit every ``where`` x {some, ``min_count`` 2, every} x {plain, ``not``}; at the patient unit
    every ``where`` of at most two leaves x the quantifier lists x lift x {nested, two-step} x
    {plain, ``not``}; ``covered`` with each scope, at both units and both lifts."""
    for where in wheres():
        for quant, k in (("some", 1), ("some", 2), ("every", 1)):
            for negated in (False, True):
                exists: dict[str, Any] = {
                    "kind": "exists",
                    "table": "mutations",
                    "where": [leaf_document(c) for c in where],
                }
                if quant == "every":
                    exists["quantifier"] = "every"
                if k != 1:
                    exists["min_count"] = k
                clause = {"not": exists} if negated else exists
                label = (
                    "not " if negated else ""
                ) + f"exists[{quant}{'' if k == 1 else f' {k}'}]({describe(where)})"

                def answer(
                    uid: str,
                    where: list[Clause] = where,
                    quant: str = quant,
                    k: int = k,
                    negated: bool = negated,
                ) -> V:
                    found = sample_q(where, quant, k, uid, after)
                    return neg(found) if negated else found

                yield Case(
                    "samples", clause, after, label, refused(where, quant == "every", after), answer
                )
    for where in wheres():
        if len(where) > 2:
            continue
        for outer, inner in QUANTIFIERS:
            for lift in ("strict", "assessed"):
                for form in ("nested", "two-step"):
                    for negated in (False, True):
                        leaves = [leaf_document(c) for c in where]
                        if form == "two-step":
                            exists = {
                                "kind": "exists",
                                "table": "mutations",
                                "where": leaves,
                                "quantifier": [outer, inner],
                                "lift": lift,
                            }
                        else:
                            nested: dict[str, Any] = {
                                "kind": "exists",
                                "table": "mutations",
                                "where": leaves,
                            }
                            if inner != "some":
                                nested["quantifier"] = inner
                            exists = {
                                "kind": "exists",
                                "table": "samples",
                                "where": [nested],
                                "lift": lift,
                            }
                            if outer != "some":
                                exists["quantifier"] = outer
                        clause = {"not": exists} if negated else exists
                        label = (
                            "not " if negated else ""
                        ) + f"exists[{_q(outer)},{_q(inner)}; {lift}; {form}]({describe(where)})"

                        def panswer(
                            uid: str,
                            where: list[Clause] = where,
                            outer: Quantifier = outer,
                            inner: Quantifier = inner,
                            lift: str = lift,
                            negated: bool = negated,
                        ) -> V:
                            found = patient_q(where, outer, inner, lift, uid, after)
                            return neg(found) if negated else found

                        yield Case(
                            "patients",
                            clause,
                            after,
                            label,
                            refused(where, inner == "every", after),
                            panswer,
                        )
    for scope in SCOPES:
        for negated in (False, True):
            covered: dict[str, Any] = {"kind": "covered", "table": "mutations"}
            columns: list[str] = []
            if scope == "class":
                covered["scope"] = {"variant_classification": [M]}
                columns = ["variant_classification"]
            elif scope:
                covered["scope"] = {"hugo_symbol": list(scope)}
                columns = ["hugo_symbol"]
            genes = None if scope in (None, "class") else list(scope)
            written = "covered" + (
                "" if scope is None else f"[{'class' if scope == 'class' else '|'.join(scope)}]"
            )
            prefix = "not " if negated else ""

            def sanswer(uid: str, genes: list[str] | None = genes, negated: bool = negated) -> V:
                found = covered_sample(genes, uid, after)
                return neg(found) if negated else found

            yield Case(
                "samples",
                {"not": covered} if negated else covered,
                after,
                prefix + written,
                refused([], False, after, columns),
                sanswer,
            )
            for lift in ("strict", "assessed"):
                lifted_clause = dict(covered, lift=lift)

                def lanswer(
                    uid: str,
                    genes: list[str] | None = genes,
                    lift: str = lift,
                    negated: bool = negated,
                ) -> V:
                    found = covered_patient(genes, lift, uid, after)
                    return neg(found) if negated else found

                yield Case(
                    "patients",
                    {"not": lifted_clause} if negated else lifted_clause,
                    after,
                    prefix + written + f"[{lift}]",
                    refused([], False, after, columns),
                    lanswer,
                )


def _q(quantifier: Quantifier) -> str:
    return f"some {quantifier['some']}" if isinstance(quantifier, dict) else str(quantifier)


def _order(uid: str) -> int:
    return int(uid[1:])


def units(unit: str) -> list[str]:
    return list(ROWS) if unit == "samples" else list(PATIENT)


# --- The owner-facing table ----------------------------------------------------------------------


def table() -> str:
    """The answers the coverage changes, as the owner reads them: for each question (unit, shape),
    the units whose answer changes, ``before -> after``, and the questions newly refused; rows of
    one unit and the same changes are joined. Written from the predictor alone."""
    before = {(c.unit, c.shape): c for c in cases(False)}
    rows: dict[tuple[str, str], list[str]] = {}
    summary: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    for case in cases(True):
        earlier = before[(case.unit, case.shape)]
        if case.refused is not None:
            change = "refused " + case.refused
            if earlier.refused is not None:
                change = f"refused {earlier.refused} -> refused {case.refused}"
            rows.setdefault((case.unit, change), []).append(case.shape)
            continue
        changes: list[str] = []
        if earlier.refused is not None:
            changes.append(f"refused {earlier.refused} -> answered")
        moved: dict[str, list[str]] = {}
        for uid in units(case.unit):
            was = "" if earlier.refused is not None else render(earlier.answer(uid))
            now = render(case.answer(uid))
            if was != now:
                moved.setdefault(f"{was} -> {now}" if was else now, []).append(uid)
                if was:
                    shapes, ids = summary.setdefault((case.unit, f"{was} -> {now}"), (set(), set()))
                    shapes.add(case.shape)
                    ids.add(uid)
        changes += [f"{change}: {' '.join(uids)}" for change, uids in sorted(moved.items())]
        if changes:
            rows.setdefault((case.unit, "; ".join(changes)), []).append(case.shape)
    lines = [
        "# The MAF's coverage: what changes (generated)",
        "",
        "Generated by `coverage_predictor.table()` from the predictor of `coverage_predictor.py` "
        "(SPEC D448), which `test_coverage_oracle` holds equal to the engine on the pack's built "
        "release; a test holds this file equal to the predictor. Each row: the unit, the "
        "questions (joined where their changes are the same) and the units whose answer changes, "
        "before the coverage -> after it. Questions whose answers do not change are not listed.",
        "",
        "## The fixture's units",
        "",
        "| Unit | State |",
        "|---|---|",
    ]
    lines += [f"| {s} | {state} |" for s, state in STATES.items()]
    lines += [
        f"| {q} | samples: {', '.join(samples) if samples else 'none'} |"
        for q, samples in PATIENT.items()
    ]
    lines += [
        "",
        "## Summary: each change of an answer, the questions it occurs in and the units",
        "",
        "| Unit | Before -> after | Questions | Units |",
        "|---|---|---|---|",
    ]
    for (unit, change), (shapes, ids) in sorted(summary.items()):
        lines.append(f"| {unit} | {change} | {len(shapes)} | {' '.join(sorted(ids, key=_order))} |")
    lines += ["", "## The changes", "", "| Unit | Questions | Changes |", "|---|---|---|"]
    for (unit, change), shapes in sorted(rows.items(), key=lambda item: (item[0][0], item[1][0])):
        lines.append(f"| {unit} | {'<br>'.join(shapes)} | {change} |")
    return "\n".join(lines) + "\n"
