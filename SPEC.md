# aibi — Specification

**Status:** Draft v0.1 · 2026-09-24 · open for review
**Scope of this document:** product goals, principles, data model, query semantics, result
contract, analysis registry, MCP surface, architecture and milestones. It is normative where it
says MUST / MUST NOT / SHOULD (RFC 2119); everything else is rationale.

---

## 1. Purpose

aibi is an **AI-native cohort exploration system** for curated clinical and genomic studies.
A researcher, or an agent working for one, asks a question such as *"in GBM, do IDH-wild-type
patients over 60 with EGFR amplification have worse overall survival?"*, and aibi returns an
answer whose every number states how it was derived, over which patients, from which version
of which data, and what it could not account for.

The system is built so that software can use it without a human in the loop to interpret it:
the metadata says what each value means, the catalogue says what each analysis needs, and each
result says how it was computed. That is the sense of *AI-ready* adopted here (NIH Bridge2AI).

### 1.1 Goals (v1)

1. Import cBioPortal-format studies into immutable, versioned releases.
2. Describe every study, attribute, molecular profile and endpoint with structured descriptors,
   including explicit missing-versus-negative semantics.
3. Let people and agents discover studies and attributes by facet **before** querying data.
4. Define cohorts with a declarative JSON document; compile it on the server; never accept SQL.
5. Run a small set of registered analyses (distributions, alteration frequency, clinical
   comparison, Kaplan–Meier survival) whose results carry a machine-readable derivation.
6. Expose all of the above through one MCP server and a web UI that share the same functions.
7. Use a model to *propose* descriptors (curation) and *draft* analysis documents
   (exploration), with a human confirming or editing both.

### 1.2 Non-goals (v1)

- General-purpose BI over arbitrary tables (that was biai; see §2).
- Raw SQL access for users or models.
- Multi-tenant hosting, fine-grained access control, or handling identifiable data. v1 assumes
  de-identified data and a trusted single-team deployment.
- Federation across sites. The result contract (§8) leaves room for it; nothing implements it.
- Timeline and treatment-line queries, Cox regression and JSON-LD export. Designed for here,
  scheduled after v1 (§13).

---

## 2. Prior work and what aibi takes from it

| Source | What aibi takes | What it leaves |
|---|---|---|
| **biai** (`jjgao/biai`) | Domain lessons: multi-table datasets, list-valued columns, parent counting, filter propagation across related tables, the TCGA example data. Its e2e specs and user guide are the behavioural checklist for the UI. | The code, ClickHouse as an app-state store, string-built SQL, per-chart round trips. |
| **cbio-lab DSL v2.2** (the system behind the `oncoprint` MCP server) | The document shape: named cohorts built from `all` / `any` / `not` clauses, plus views. The rules *clients never send SQL*, *refuse rather than approximate*, *profiled-only denominators*, *NOT_PROFILED as a first-class state*, deterministic plain-language readbacks, caveats the agent must surface, `observed` windows on absence queries, left truncation for re-anchored survival, and `params` templates. | Its `not` semantics (study base EXCEPT leaf) are replaced by three-valued logic (§6.3). Documents are pinned to data releases and hashed into derivation ids (§7). |
| **cBioPortal study format and validator** | The import format (meta files, clinical files with header rows, MAF, discrete CNA, gene panel matrix, case lists) and the validator's role as a gate. | — |

Documents SHOULD stay close enough to cbio-lab's shape that a cbio-lab document can be
translated mechanically. Any place where the two differ in meaning MUST be listed in
`docs/cbio-lab-differences.md` once that file exists. Whether aibi should converge with
cbio-lab rather than sit beside it is open question Q1 (§14).

---

## 3. Principles

These principles decide the design. Each one names how it is enforced; a change that weakens
the enforcement is a change to this spec.

### 3.1 Data principles

**P1 — Every reported value carries its derivation.**
Every count, proportion, statistic and curve is returned with the canonical document that
produced it, the registered analysis and its version, the pinned data release(s), and for any
proportion its numerator, its denominator, and a statement of what the denominator counts.
*Enforced by:* the result schema (§8). A value without a derivation cannot be serialised.

**P2 — Missing is not negative.**
The data model distinguishes a value, a confirmed absence, not assessed, not applicable, and
unknown (§6.1). Query logic is three-valued (§6.3). No statistic silently drops, imputes or
reclassifies a missing observation; every exclusion is counted by reason in the result.
*Enforced by:* the observation-state model in the engine; property tests (§12.3).

**P3 — Comparability is asserted, not inferred.**
Two attributes are comparable across studies only if both map to the same harmonised concept
with a declared transform (§5.5). A shared column name counts for nothing. Compatibility
constraints such as the reference genome build, value encoding and time origin are checked.
*Enforced by:* the compiler refuses cross-study references to unharmonised attributes unless
the document opts in explicitly, and then every affected result carries a caveat.

**P4 — Metadata is queryable before data.**
Study, attribute, profile and endpoint descriptors form a catalogue that can be searched by
facet without issuing a data query. *Enforced by:* separate catalogue and query surfaces (§10).

**P5 — Capabilities are declared, not inferred.**
Each analysis is a registry entry declaring the data it requires and the result it returns.
The MCP tool schemas, the UI's analysis menu and the answer to *"which analyses does this
study support?"* are all generated from the registry. *Enforced by:* no analysis can be called
except through the registry (§9).

**P6 — Results point to immutable releases.**
Data is served from content-addressed, read-only releases. A curation change, even to one
descriptor, creates a new release. *Enforced by:* the storage layer (§11.2); a document run
against the same pins returns the same result digest.

**P7 — One documentation surface for data, computations and models.**
Descriptors (data), registry entries (computations) and model cards (the models that propose
descriptors or draft documents) share one envelope (§5.1) and one lookup path.

### 3.2 AI-interaction principles

**A1 — The model writes documents, never SQL and never numbers.**
Every number shown to a user comes from an engine result with a derivation id. The UI renders
numbers from results, not from model prose. The in-app assistant is instructed, and checked in
evals, to cite derivation ids for every figure it states.

**A2 — One document, many editors.**
The UI, the chat assistant, MCP clients and shareable URLs all read and write the same Analysis
document. Its readback is generated deterministically from the canonical document, never by a
model, so a person can confirm that the document asks what they meant.

**A3 — Refuse rather than approximate.**
An unsupported operator, an unknown attribute, or an analysis whose requirements are not met
fails loudly, names the problem, and lists what *is* available.

**A4 — No private tools.**
The in-app assistant uses exactly the public MCP tools. Anything it can do, an external agent
can do and a person can inspect.

**A5 — Proposals are visible until confirmed.**
Anything a model proposes (a descriptor, an ontology code, a harmonisation mapping) is stored
with status `proposed` and the proposing model's card. It is never silently promoted to
`asserted`, and results that depend on it carry a caveat until a person confirms it.

---

## 4. Glossary

| Term | Meaning |
|---|---|
| **Study** | A curated collection of patients, samples and data from one source (e.g. `gbm_tcga_pan_can_atlas_2018`). |
| **Release** | An immutable snapshot of a study's data **and** descriptors, identified as `<study>@<n>` and by a content hash. |
| **Unit** | What is being counted: `patient` (default) or `sample`. |
| **Attribute** | A clinical variable at patient or sample level. |
| **Profile** | A molecular data set in a study: mutations, discrete CNA, structural variants, a continuous matrix, or a generic assay. |
| **Endpoint** | A declared (time, status) pair usable for survival analysis. |
| **Descriptor** | The structured metadata record for a study, attribute, profile, endpoint, analysis or model. |
| **Concept** | A study-independent meaning (e.g. *age at diagnosis in years*) that attributes can be harmonised to. |
| **Analysis document** | The JSON object declaring cohorts and views (§7). |
| **Derivation** | The canonical, hashed description of how a result was produced (§7.4). |
| **Caveat** | A structured, coded statement about a result's fitness for use (§8.3). |

---

## 5. Descriptors

### 5.1 Common envelope

Every descriptor, whatever it describes, has:

```jsonc
{
  "kind": "study | attribute | profile | endpoint | event_domain | analysis | model | concept",
  "id": "string, stable within its scope",
  "version": "string",
  "label": "Human-readable name",
  "definition": "One-paragraph definition in plain text",
  "provenance": { "source": "...", "pipeline": {"name": "...", "version": "..."}, "citation": ["pmid:…", "doi:…"] },
  "fields": { /* kind-specific, §5.2–5.6 */ },
  "curation": { "<field path>": CurationStatus }
}
```

Every semantically meaningful field has a curation status:

```jsonc
CurationStatus = {
  "status": "asserted | proposed | imported | imported_default | undeclared",
  "by": "person:<id> | model:<model-card-id> | importer:<name>@<version>",
  "at": "RFC 3339 timestamp",
  "evidence": "optional free text or reference"
}
```

- `imported`: taken from the source files (e.g. a cBioPortal header row).
- `imported_default`: the importer filled it in by convention (e.g. mapping `[Not Available]`
  to UNKNOWN). Allowed, but it MUST be surfaced as a caveat wherever it affects a result.
- `undeclared`: nobody has said. The engine MUST treat undeclared semantics conservatively
  (e.g. an undeclared missing code is UNKNOWN, never negative).

### 5.2 Study descriptor

`name`, `description`, `cancer_types` (OncoTree codes), `citation` / `references`,
`source` (e.g. a Datahub path and commit), `license`, `data_use` (GA4GH DUO codes),
`reference_genome` (e.g. `GRCh37`), `time_origin` (what day 0 means in this study, e.g.
*date of initial pathologic diagnosis*; `undeclared` is allowed and triggers a caveat on any
cross-study time comparison), and computed fields (`n_patients`, `n_samples`,
`assay_composition`) that are filled in at release build time, never by hand.

### 5.3 Attribute descriptor

| Field | Meaning |
|---|---|
| `level` | `patient` or `sample` |
| `datatype` | `number`, `integer`, `string`, `boolean`, `category`, `list<category>`, `day_offset` |
| `units` | UCUM code for numbers (`a`, `mo`, `d`, `mg/dL`); required for any number used in a cross-study comparison |
| `permissible_values` | For categories: `[{value, label, concepts: [OntologyRef]}]` |
| `missing_codes` | Map from a raw token to an observation state: `{"[Not Available]": "UNKNOWN", "[Not Applicable]": "NOT_APPLICABLE", "[Not Evaluated]": "NOT_ASSESSED", "": "UNKNOWN", "NA": "UNKNOWN"}` |
| `concepts` | `[OntologyRef]` describing what the attribute measures (NCIt, LOINC, …) |
| `harmonized_as` | Optional `HarmonizationRef` (§5.5) |
| `completeness` | `{scope: "all patients | patients with samples | …", declared: "complete | partial | unknown"}` plus computed counts per observation state |
| `source` | `{file, column, header_display_name, header_description, header_datatype, header_priority}` as imported |

`OntologyRef = {system: "NCIt | LOINC | OncoTree | HGNC | SNOMED | UBERON | …", code, label,
relation: "exact | broader | narrower | related"}`.

### 5.4 Profile descriptor

| Field | Meaning |
|---|---|
| `profile_type` | `MUTATION`, `CNA_DISCRETE`, `STRUCTURAL_VARIANT`, `CONTINUOUS`, `GENERIC_ASSAY` |
| `assay` | e.g. `WES`, `WGS`, `panel:IMPACT468` |
| `coverage` | `whole_exome`, `whole_genome`, `panels` (per-sample, from the gene panel matrix) or `undeclared`. `whole_*` without a source is `imported_default` and raises a caveat |
| `reference_genome` | Required for `MUTATION` and `STRUCTURAL_VARIANT` |
| `pipeline` | Caller / normaliser and version |
| `variant_scope` | For mutations: which variant classes and origins the file contains (e.g. *somatic, non-synonymous only*). A query for a class outside the scope is **refused**, since absence of such a record means nothing |
| `value_semantics` | For CNA: encoding (e.g. GISTIC −2…2). For continuous data: scale and, for z-scores, the **reference population** (e.g. *diploid samples* vs *all samples*) |
| `gene_identifiers` | Symbol source and version (HGNC release), with alias resolutions recorded at import |

### 5.5 Concepts and harmonisation

A **concept** is a study-independent descriptor (`kind: concept`) such as
`aibi:age_at_diagnosis` (units `a`), `aibi:sex`, `aibi:oncotree_code`,
`aibi:overall_survival` (an endpoint concept), `aibi:tmb_nonsynonymous`.

```jsonc
HarmonizationRef = {
  "concept": "aibi:age_at_diagnosis",
  "transform": { "unit_from": "d", "unit_to": "a" } | { "value_map": {"Male": "male", "M": "male"} } | null,
  "status": CurationStatus
}
```

An attribute is **harmonised** if it has a `harmonized_as` with status `asserted`. Cross-study
references in documents use the concept id, not the attribute id (§7.3). v1 ships a small
concept set (the attributes needed for the M5 exit criteria); growing it is ongoing curation,
not code.

### 5.6 Endpoint descriptor

`time_attribute`, `status_attribute`, `time_units`, `event_coding` (which status values are
events and which are censored, e.g. `{"1:DECEASED": "event", "0:LIVING": "censored"}`),
`time_origin` (defaults to the study's), and optionally `harmonized_as` (e.g.
`aibi:overall_survival`). cBioPortal's `OS_MONTHS`/`OS_STATUS`, `PFS_*`, `DFS_*` and `DSS_*`
pairs are detected at import with status `imported_default`.

---

## 6. Data model and observation semantics

### 6.1 Observation states

Every (unit, variable) pair evaluated by the engine has exactly one state:

| State | Meaning | Examples |
|---|---|---|
| `PRESENT` | A value, record or alteration exists | `AGE = 61`; a KRAS G12C record in a sample profiled for KRAS |
| `ABSENT` | It was assessed, and there is none | A sample profiled for TP53 with no TP53 record in a mutation profile whose `variant_scope` covers the query |
| `NOT_ASSESSED` | It is known that it was not assessed | A sample not profiled for the gene; a CNA cell reported `NA`; `[Not Evaluated]` |
| `NOT_APPLICABLE` | The question does not apply | `[Not Applicable]` |
| `UNKNOWN` | There is no information either way | Empty cell, `[Not Available]`, `[Unknown]`, an undeclared missing code |

For clinical attributes, a negative answer (`"No"`) is a `PRESENT` value; `ABSENT` exists for
**existence** variables: alterations and, later, events.

### 6.2 Profiling and coverage

`assessed(sample, profile, gene)` is a stored fact, computed at release build time from the
case lists, the gene panel matrix and the panel definitions:

- panel coverage: assessed iff the sample is in the profile's case list **and** the gene is on
  the sample's panel;
- whole-exome or whole-genome coverage: assessed iff the sample is in the profile's case list;
- undeclared coverage: `UNKNOWN` for every gene. The importer MAY default to whole-exome when
  no panel matrix exists, but then the coverage field is `imported_default` and results carry
  `COVERAGE_ASSUMED`.

### 6.3 Three-valued query logic

Leaf predicates evaluate per unit to `TRUE`, `FALSE` or `UNKNOWN` (Kleene logic):

| Observation state | Value predicate (e.g. `AGE > 60`) | Existence predicate (e.g. `TP53: MUT`) |
|---|---|---|
| PRESENT | TRUE or FALSE, by the value | TRUE if it matches, else FALSE |
| ABSENT | — | FALSE |
| NOT_ASSESSED | UNKNOWN | UNKNOWN |
| NOT_APPLICABLE | FALSE | FALSE |
| UNKNOWN | UNKNOWN | UNKNOWN |

- `all` is Kleene AND, `any` is Kleene OR, `not` swaps TRUE and FALSE and leaves UNKNOWN.
- A unit is **in** a cohort iff the cohort predicate is TRUE.
- `known(leaf)` is TRUE iff the leaf is not UNKNOWN; `unknown(leaf)` is its complement. These
  are the only way to deliberately include unknowns.
- **Consequence:** `{"not": {"kind": "genomic", "q": "TP53: MUT"}}` means *profiled for TP53
  and not mutated*. It does not include unprofiled patients. This deliberately differs from
  cbio-lab, where the same clause includes them unless a `profiled` clause is added.

**Sample-to-patient lifting.** When the unit is `patient` and a predicate is sample-level,
the patient's value is the Kleene OR over their samples: TRUE if any sample is TRUE; otherwise
UNKNOWN if any sample is UNKNOWN; otherwise FALSE. A patient with no samples is UNKNOWN. A
patient with one wild-type profiled sample and one unprofiled sample is therefore UNKNOWN for
"TP53 mutated", which is stricter than cBioPortal's convention (see Q2). A document may request
`"lift": "all"` (every sample TRUE) instead.

### 6.4 Accounting

Every cohort result reports `n_true`, `n_false` and `n_unknown` over the study base, and for
each leaf how many units it made UNKNOWN. This makes it visible when a cohort shrank because of
missing data rather than because of the criterion.

---

## 7. Analysis document

### 7.1 Shape

```jsonc
{
  "aibi": "1",                                   // document format version
  "params": { "genes": ["EGFR", "PTEN"] },       // optional; exact "$name" value substitution, as in cbio-lab
  "study": "gbm_tcga_pan_can_atlas_2018",        // default study; may be pinned: "...@3"
  "unit": "patient",                             // patient | sample
  "cohorts": {
    "<name>": {
      "all": [ Clause ],                         // [] = every unit in the study
      "study": "<id>",                           // optional override
      "notes": "plain text, never compiled"
    }
  },
  "views": [ { "analysis": "<registry id>", "cohorts": ["<name>", …], "params": { … }, "note": "plain text" } ],
  "notes": "plain text"
}
```

`Clause := Leaf | {"all": [Clause]} | {"any": [Clause]} | {"not": Clause} | {"known": Clause} | {"unknown": Clause}`.

Nesting is allowed but capped (depth 4, 32 leaves per cohort, 6 cohorts, 8 views per document)
so that readbacks stay readable and compiled queries stay bounded.

### 7.2 Leaf kinds (v1)

| Kind | Shape | Notes |
|---|---|---|
| `clinical` | `{attribute \| concept, values?: [..], range?: {gt,gte,lt,lte}, op?, value?}` | Attribute ids are validated against descriptors; units in `range` MUST match the attribute's `units` or be given explicitly and convertible |
| `genomic` | `{q: "<OQL subset>", profile?}` | `MUT`, `MUT=<change>`, classes `MISSENSE NONSENSE TRUNC INFRAME SPLICE`, `AMP`, `HOMDEL`, `FUSION`. `DRIVER` is refused until an annotation source is pinned (M6). Anything outside the subset, or outside the profile's `variant_scope`, is refused |
| `assessed` | `{profile_type \| profile, gene?}` | TRUE if assessed (for the gene, when given) |
| `ids` | `{patients \| samples: ["<study>:<id>", …]}` | Explicit lists |

Timeline `event` and `derived` leaves follow in M6, with cbio-lab's semantics (exists-pair,
`observed` windows, `priorGuard`), plus an `event_domain` descriptor declaring each domain's
completeness, so that "no treatment record" can resolve to ABSENT only when the domain says it
is complete over the patient's observation window.

### 7.3 Cross-study references

A cohort may name several studies (`"studies": [..]`). Inside such a cohort, `clinical` leaves
MUST use `concept`, not `attribute`, and every study MUST have an asserted harmonisation for
that concept, or the document is refused with the list of studies and attributes that lack
one. `"unharmonized": "allow"` on the cohort turns the refusal into an
`UNHARMONIZED_COMPARISON` caveat on every result that uses it. Tests across studies are
stratified by study (as in cbio-lab); pooled values are reported alongside, labelled as such.

### 7.4 Canonical form and derivation id

Before compilation, the server canonicalises the document:

1. Substitute `params`.
2. Resolve every study reference to a pinned release (`<study>@<n>`) and record its manifest
   hash.
3. Resolve attribute and concept references to descriptor ids and versions.
4. Write every semantically relevant default explicitly (`unit`, `lift`, analysis parameter
   defaults).
5. Sort order-insensitive collections: values in `values`, and clauses inside `all` / `any`,
   by their own canonical serialisation.
6. Drop fields that do not affect results (`notes`, `note`, cohort display order).

The canonical form is serialised with the JSON Canonicalization Scheme (RFC 8785).

- A **cohort derivation id** is `drv:` + SHA-256 of `{canonical cohort, unit, release pins}`.
- A **result derivation id** is `drv:` + SHA-256 of `{analysis id@version, canonical
  parameters, the derivation ids of its cohorts}`.
- A **result digest** is the SHA-256 of the canonical result values.

Invariant: the same result derivation id MUST produce the same result digest. A code change
that alters the digest for an unchanged derivation id is a bug unless the analysis version was
bumped. The golden tests check this (§12.3).

### 7.5 Readback

The server renders a deterministic, plain-language readback of each canonical cohort and view
from templates (e.g. *"Patients in GBM (TCGA PanCan) @3 with ≥1 sample profiled for EGFR
(mutations, CNA), aged over 60 years at diagnosis, and with EGFR amplified in any sample"*).
Readbacks are returned with every result and shown next to every figure.

---

## 8. Result contract

### 8.1 Envelope

```jsonc
{
  "derivation": {
    "id": "drv:…",
    "document": { /* canonical form */ },
    "analysis": { "id": "survival.km", "version": "1.0.0" },
    "releases": [ { "study": "gbm_tcga_pan_can_atlas_2018", "release": 3, "manifest": "sha256:…" } ],
    "engine": "aibi 0.3.1",
    "sql": [ "…" ]                               // compiled queries, for audit
  },
  "digest": "sha256:…",
  "readback": { "cohorts": { "<name>": "…" }, "view": "…" },
  "population": { "<cohort>": { "n_true": 0, "n_false": 0, "n_unknown": 0, "unknown_by_leaf": { … } } },
  "values": { /* analysis-specific, validated against the registry's output schema */ },
  "caveats": [ Caveat ]
}
```

### 8.2 Proportions

Every proportion in `values` is an object, never a bare number:

```jsonc
{
  "estimate": 0.412,
  "numerator": 124,
  "denominator": 301,
  "denominator_definition": "patients in cohort 'IDH-wt >60' with ≥1 sample assessed for EGFR in profile gbm_tcga_pan_can_atlas_2018_mutations",
  "excluded": { "NOT_ASSESSED": 17, "UNKNOWN": 3 },
  "ci": { "method": "wilson", "level": 0.95, "low": 0.357, "high": 0.469 }
}
```

### 8.3 Caveats

`Caveat = {code, severity: "info | warn | block", message, affects: [paths into values]}`.
Codes are a stable, documented enum. The initial set:

| Code | Raised when |
|---|---|
| `UNKNOWN_EXCLUDED` | A cohort or denominator excluded units because they were UNKNOWN or NOT_ASSESSED |
| `COVERAGE_ASSUMED` | Profiling status relies on an `imported_default` coverage |
| `DEFAULT_SEMANTICS` | Any `imported_default` or `proposed` descriptor field affected the result |
| `UNHARMONIZED_COMPARISON` | Cross-study comparison over attributes without an asserted harmonisation |
| `TIME_ORIGIN_UNDECLARED` | Time-based values are compared across studies whose `time_origin` is undeclared or different |
| `SMALL_N` | A group falls below the analysis's declared minimum for a reliable estimate |
| `POOLED_ACROSS_STUDIES` | A pooled value is reported next to a stratified one |

MCP tool descriptions MUST tell clients that `warn` and `block` caveats have to be shown to the
user. A `block` caveat means the result is returned for inspection but MUST NOT be presented as
an answer.

---

## 9. Analysis registry

### 9.1 Entry

Each analysis is a descriptor (`kind: analysis`) plus an implementation:

```jsonc
{
  "id": "survival.km", "version": "1.0.0",
  "label": "Kaplan–Meier survival",
  "definition": "Kaplan–Meier estimate per cohort; log-rank test (k-sample) when there are ≥2 cohorts; median survival with CI.",
  "requires": [
    { "role": "endpoint", "kind": "endpoint" },
    { "role": "cohorts", "min": 1, "max": 6 }
  ],
  "params": { /* JSON Schema, generated from a Pydantic model */ },
  "returns": { /* JSON Schema of `values` */ },
  "method": { "library": "lifelines", "version": "…", "references": ["doi:…"] },
  "assumptions": ["independent censoring", "…"],
  "caveats": ["UNKNOWN_EXCLUDED", "SMALL_N", "TIME_ORIGIN_UNDECLARED"],
  "min_group_n": 10
}
```

### 9.2 Applicability

`applicable_analyses(study)` matches each entry's `requires` against the study's descriptors
and returns, for each analysis, `available`, `unavailable` (with the missing requirement named)
or `available_with_caveats` (e.g. an endpoint whose event coding is `imported_default`). The
study page and `describe_study` both show this.

### 9.3 v1 analyses

| Id | Returns |
|---|---|
| `summary.distribution` | Per attribute: category counts or numeric summary and histogram, per cohort, with observation-state counts |
| `genomic.frequency` | Per gene: altered proportion per cohort over assessed units (§8.2); two-sided Fisher exact test when there are exactly 2 cohorts; Benjamini–Hochberg q across genes |
| `clinical.compare` | Per attribute: chi-squared (categorical) or Welch t / Mann–Whitney (numeric) across cohorts; BH q across attributes |
| `survival.km` | As in §9.1 |

---

## 10. MCP and HTTP surface

One set of Python functions backs both the HTTP API (FastAPI) and the MCP server. Tool schemas
are generated from the same Pydantic models as the HTTP API.

| Tool | Purpose | Touches row data? |
|---|---|---|
| `search_catalog` | Faceted search over studies: cancer type (OncoTree, including descendants), assay composition, profiles, concepts present, sample and patient counts, completeness thresholds, data-use codes | No |
| `describe_study` | Study descriptor, attributes, profiles, endpoints, applicable analyses, standing caveats | No (computed stats only) |
| `describe_attribute` | Full descriptor with observation-state counts and value distribution | Aggregates only |
| `list_analyses` | Registry entries, optionally filtered by applicability to a study | No |
| `validate_document` | Canonicalise, check and read back a document without running it; returns errors, caveats that would be raised, and derivation ids | No |
| `run_analysis` | Run a document; returns results per §8 | Yes |
| `explain` | Given a derivation id: canonical document, readback, releases, SQL, analysis descriptor | No |
| `curation_queue` | Descriptor fields that are `undeclared`, `imported_default` or `proposed` | No |
| `propose_descriptor` | Record a proposed value for a descriptor field, with rationale and model card | No |

Confirming a proposal (`proposed` → `asserted`) is a UI action by a person, not an MCP tool, in
v1. Descriptors are also exposed as MCP resources (`aibi://study/<id>@<n>/attribute/<id>`).

---

## 11. Architecture

### 11.1 Components

```
          Web UI (React + TS)          External agents (Claude, …)
                 │  HTTP                        │  MCP
                 ▼                              ▼
        ┌──────────────────────────────────────────────┐
        │  aibi server (Python, one process)           │
        │                                              │
        │  api/ (FastAPI)          mcp/ (MCP SDK)       │
        │         └──────────┬──────────┘              │
        │              service functions               │
        │   ┌────────────┬───┴──────────┬───────────┐  │
        │   catalog      engine          analyses    assistant
        │   (descriptors (canonicalise,  (registry,  (Claude API,
        │    & search)    3VL compile,    lifelines,  uses the MCP
        │                 SQLGlot→DuckDB) scipy)      tools only)
        │   └──────┬─────┴──────┬───────┘              │
        │    app DB (SQLite)   releases (Parquet + manifest, DuckDB)
        └──────────────────────────────────────────────┘
                 ▲
       importers/cbioportal  (validator gate → release builder)
```

### 11.2 Storage

- **Releases:** `data/releases/<study>/<n>/` holds Parquet files, `descriptors.json`, and
  `manifest.json` listing every file's SHA-256. The manifest's own hash identifies the release.
  Unchanged Parquet files are shared between releases by hash, so a descriptor-only release
  costs almost nothing. Releases are never modified or deleted in place.
- **App DB (SQLite):** the catalogue index (a denormalised copy of descriptors for search),
  saved documents, the curation queue and its audit trail, and the result cache keyed by
  derivation id.
- **Query engine:** DuckDB reading the release's Parquet files. The compiler builds queries as
  SQLGlot expression trees and never concatenates identifiers or values into strings; every
  identifier comes from a descriptor.

### 11.3 Stack

| Layer | Choice |
|---|---|
| Language and tooling | Python ≥ 3.12, uv, ruff, pyright (strict on `schema/` and `engine/`), pytest, hypothesis |
| Schemas | Pydantic v2 as the source of truth; JSON Schema and OpenAPI generated from it |
| API and MCP | FastAPI; the official MCP Python SDK |
| Data | DuckDB, Parquet, SQLite |
| Statistics | lifelines, scipy, statsmodels |
| Assistant | Claude API, calling the same MCP tools |
| Frontend | React + TypeScript + Vite, types generated from OpenAPI; charts rendered from result `values`, never from model text |

### 11.4 Repository layout

```
aibi/
  SPEC.md  CLAUDE.md  README.md
  server/
    pyproject.toml
    src/aibi/
      schema/        # Pydantic models: descriptors, documents, results, caveats
      store/         # release builder and reader, app DB
      importers/cbioportal/
      catalog/
      engine/        # canonicalise → validate → plan → SQL; three-valued logic
      analyses/      # registry and implementations
      api/  mcp/  assistant/
    tests/
  web/
  fixtures/          # small public studies used by tests
```

---

## 12. Import, validation and testing

### 12.1 cBioPortal import (v1 scope)

`meta_study.txt`; clinical patient and sample files (four header rows → descriptor fields with
status `imported`); `data_mutations` (MAF); discrete CNA; structural variants; the gene panel
matrix and panel definitions; case lists. Continuous profiles, generic assays and timeline
files follow in M6.

### 12.2 Validation as a gate

Structural errors (unparseable files, records referring to unknown patient or sample ids,
duplicate ids, a sample on a gene panel that is not defined) stop the import. Semantic gaps (missing units, undeclared coverage,
unknown missing codes) do not stop it; they become `undeclared` or `imported_default` fields
and land in the curation queue.

### 12.3 Tests that encode the principles

- **Three-valued logic (property tests):** for any cohort predicate `C`, `n_true(C) +
  n_false(C) + n_unknown(C)` equals the study base; `not(not C) ≡ C`; `C` and `not C` never
  share a unit; `known(C)` equals `C ∪ not C`.
- **Missing is not negative:** on a fixture with an unprofiled sample, `not TP53: MUT` excludes
  it and the result reports it under `UNKNOWN_EXCLUDED`.
- **Derivation stability (golden tests):** each fixture document has a checked-in derivation
  id and result digest; CI fails if either changes without an analysis version bump.
- **Statistical correctness:** KM curves, log-rank, Fisher and BH values agree with reference
  outputs from R (`survival`, `stats`) computed once and checked in.
- **Refusal:** each refusal rule (unknown attribute, out-of-scope variant class, unharmonised
  cross-study reference, unmet analysis requirement) has a test asserting the error lists the
  available alternatives.
- **Assistant evals:** a small set of questions whose answers the assistant must reach through
  the tools, citing derivation ids, and surfacing required caveats.

---

## 13. Milestones

| # | Deliverable | Exit criterion |
|---|---|---|
| **M0** | Repo skeleton, CI, Pydantic schemas for descriptors, documents, results and caveats; JSON Schema export | Schemas published; CI runs lint, type check and tests |
| **M1** | cBioPortal importer with validator gate; release builder; catalogue; `search_catalog`, `describe_study`, `describe_attribute`, `curation_queue` | One whole-exome study (TCGA GBM PanCan) and one panel study imported; an MCP client can find and describe both |
| **M2** | Engine: canonicalisation, derivation ids, three-valued compile to DuckDB, `clinical` / `genomic` / `assessed` / `ids` leaves, readbacks, `validate_document`, `explain` | Property tests pass; the unprofiled-sample test passes |
| **M3** | Registry and the four v1 analyses; `applicable_analyses`; `run_analysis` | Golden and R-reference tests pass |
| **M4** | Web UI: catalogue, study page with applicable analyses, cohort builder with live counts including unknowns, results with provenance panel | biai's exploration e2e scenarios, ported, pass |
| **M5** | Assistant (chat that edits the document); AI-proposed descriptors with a confirmation UI; initial concept set and cross-study queries | Assistant evals pass; a harmonised two-study survival comparison runs with stratified log-rank |
| **M6** | Timeline events and derived leaves; continuous profiles; Cox; driver annotation; JSON-LD / Bioschemas export | Per-feature, set when M5 lands |

---

## 14. Open questions

| # | Question | Current leaning |
|---|---|---|
| Q1 | Should aibi converge with cbio-lab (one engine, one DSL) rather than sit beside it? | Keep documents translatable now; decide after M3, when both engines exist and can be compared on the same studies |
| Q2 | Strict sample-to-patient lifting (§6.3) makes some patients UNKNOWN that cBioPortal counts as profiled. Keep strict as the default? | Yes, strict by default, with the count of patients this affects reported in every result |
| Q3 | Deployment target: a single-user local app, a lab server, or both? It decides auth and whether the app DB stays SQLite | Lab server with no auth in v1; revisit before handling any restricted data |
| Q4 | Target scale per study (patients, mutation records)? | Up to ~100k patients and ~10M mutation records per study on one machine |
| Q5 | Where should concepts come from: an aibi-owned list, cBioPortal's own harmonisation work, OMOP, or a mix? | Start with an aibi-owned list mapped to NCIt and LOINC; align with cBioPortal's work once it has identifiers |
| Q6 | Import from files only, or also directly from a cBioPortal instance (API or database)? | Files in v1; a connector later |
| Q7 | Should confirming a descriptor be restricted to named curators? | Any user in v1, with every confirmation audited |
