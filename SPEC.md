# aibi — Specification

**Status:** Draft v0.3.1 · 2026-09-24 · open for review
**Scope of this document:** product goals, principles, data model, query semantics, result
contract, analysis registry, domain packs, MCP surface, architecture and milestones. It is
normative where it says MUST / MUST NOT / SHOULD (RFC 2119); everything else is rationale.

**Changes in v0.3.1:** fixes from a consistency review (D20–D32): explicit reference cohorts,
resolved cohort references, rounding before hashing, suppression settings in derivations, `ids`
refused without row-id access, overlapping cohorts refused by default, coverage proposed for
every relationship, decidable record filters, empty-quantifier and `min_count` semantics, a
missing coverage case, draft releases during curation, entity concepts on tables, and a severity
for every caveat.

**Changes in v0.3:** records the decisions from the first full review (§16); adds Cox
regression, engine-computed comparisons, observation windows, cohort references,
`count_cohort`, per-session curation releases and out-of-scope parents; resolves most open
questions.

**Changes in v0.2:** the core is now domain-agnostic. Patients, samples, genes and molecular
profiles moved out of the core into an *oncology pack* (§10). The core works over any set of
related tables from spreadsheets, delimited files or databases. A new principle, P8, requires
that a system like cBioPortal can be built on top of aibi without changing its core.

---

## 1. Purpose

aibi is an **AI-native system for exploring cohorts in related tables**. A cohort is any set of
entities (patients, samples, customers, sites, devices) selected by criteria over their own
attributes and over the rows related to them. A researcher, or an agent working for one, asks
a question such as *"do patients over 60 with at least one grade ≥3 adverse event have shorter
time to discontinuation?"*, and aibi returns an answer whose every number states how it was
derived, over which entities, from which version of which data, and what it could not account
for.

The system is built so that software can use it without a human to interpret it: the metadata
says what each value means, the catalogue says what each analysis needs, and each result says
how it was computed. That is the sense of *AI-ready* adopted here (NIH Bridge2AI).

Biomedical data, and cBioPortal studies in particular, are the reference use case and the
hardest test of the design. They are not built into it: domain knowledge enters through
**domain packs** (§10). The long-term test of the design is that cBioPortal's study
exploration could be rebuilt as an aibi pack plus a UI.

### 1.1 Goals (v1)

1. Import any set of related tables (CSV/TSV, XLSX/ODS sheets, Parquet, or database tables)
   into immutable, versioned releases.
2. Describe every dataset, table, column, relationship and endpoint with structured
   descriptors, including explicit missing-versus-negative semantics and declared coverage.
3. Let people and agents discover datasets and columns by facet **before** querying data.
4. Define cohorts with a declarative JSON document over the table graph; compile it on the
   server; never accept SQL.
5. Run a small set of registered, domain-neutral analyses (distributions, group comparison,
   existence frequency, Kaplan–Meier, Cox regression) whose results carry a machine-readable
   derivation, including the effect sizes people ask for (risk differences and ratios,
   differences in medians, hazard ratios), each with a confidence interval.
6. Ship an oncology pack (cBioPortal-format import, genomic query shorthand, alteration
   frequency) that uses only the public extension points.
7. Expose all of the above through one MCP server and a web UI that share the same functions.
8. Use a model to *propose* descriptors, keys and relationships (curation) and to *draft*
   analysis documents (exploration), with a person confirming or editing both.

### 1.2 Non-goals (v1)

- Dashboards and report building as ends in themselves. aibi answers questions about cohorts;
  it is not a general BI tool (that was biai; see §2).
- Raw SQL access for users or models.
- Live queries against source databases. Database tables are snapshotted into releases (P6).
- Multi-tenant hosting, user accounts, fine-grained access control, or handling identifiable
  data. v1 assumes de-identified data and a trusted lab-server deployment (also runnable
  locally).
- Federation across sites. The result contract (§8) leaves room for it.
- Timeline queries and JSON-LD export: designed for, scheduled after v1 (§14). The v1 schema
  already carries per-unit observation windows (§5.3) so that timeline support is an addition,
  not a redesign.

---

## 2. Prior work and what aibi takes from it

| Source | What aibi takes | What it leaves |
|---|---|---|
| **biai** (`jjgao/biai`) | The generic starting point: any multi-table dataset, relationships between tables, counting by a related (parent) table, filters that propagate along relationships, list-valued columns, spreadsheet and multi-sheet import, key detection. Its e2e specs and user guide are the behavioural checklist for the UI. | The code, ClickHouse as an app-state store, string-built SQL, per-chart round trips, silent path-finding between tables. |
| **cbio-lab DSL v2.2** (the system behind the `oncoprint` MCP server) | The document shape: named cohorts built from `all` / `any` / `not` clauses, plus views. The rules *clients never send SQL*, *refuse rather than approximate*, *denominators count only what was assessed*, *not-assessed is a first-class state*, deterministic plain-language readbacks, caveats the agent must surface, `observed` windows on absence queries, left truncation for re-anchored survival, and `params` templates. These are generalised in the core; the oncology-specific parts (OQL, profiles, panels) become the oncology pack. | Its `not` semantics (base EXCEPT leaf) are replaced by three-valued logic (§6.3). Documents are pinned to releases and hashed into derivation ids (§7). |
| **cBioPortal study format and validator** | The oncology pack's import format and the validator's role as a gate. | Nothing in the core depends on it. |

Documents SHOULD stay close enough to cbio-lab's shape that a cbio-lab document can be
translated mechanically into core clauses plus oncology-pack leaves. Differences in meaning
MUST be listed in `docs/cbio-lab-differences.md` once that file exists. Whether aibi and
cbio-lab should converge is open question Q1 (§15).

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
unknown (§6.2). An absence of related rows means "none" only where the dataset declares that
the table is complete for that entity (§6.4). Query logic is three-valued (§6.3). No statistic
silently drops, imputes or reclassifies a missing observation; every exclusion is counted by
reason in the result.
*Enforced by:* the observation-state model in the engine; property tests (§13.3).

**P3 — Comparability is asserted, not inferred.**
Two columns are comparable across datasets only if both map to the same concept with a
declared transform (§5.6). A shared column name counts for nothing. Units, value encodings and
time origins are checked.
*Enforced by:* the compiler refuses cross-dataset references to unmapped columns unless the
document opts in explicitly, and then every affected result carries a caveat.

**P4 — Metadata is queryable before data.**
Dataset, table, column, relationship and endpoint descriptors form a catalogue that can be
searched by facet without issuing a data query. *Enforced by:* separate catalogue and query
surfaces (§11).

**P5 — Capabilities are declared, not inferred.**
Each analysis is a registry entry declaring the data it requires and the result it returns.
The MCP tool schemas, the UI's analysis menu and the answer to *"which analyses does this
dataset support?"* are all generated from the registry. *Enforced by:* no analysis can be
called except through the registry (§9).

**P6 — Results point to immutable releases.**
Data is served from content-addressed, read-only releases. Curation changes create a new
release; the confirmations made in one curation session are batched into a single release when
the session ends. During a session, queries may run against the session's **draft release**;
such results carry `DRAFT_RELEASE`, are not cached and are not citable. *Enforced by:* the storage layer (§12.2); a document run
against the same pins returns the same result digest.

**P7 — One documentation surface for data, computations and models.**
Descriptors (data), registry entries (computations) and model cards (the models that propose
descriptors or draft documents) share one envelope (§5.1) and one lookup path.

**P8 — The core is domain-agnostic; domains are packs.**
The core knows about tables, keys, relationships, columns, observation states, coverage,
endpoints and concepts, and nothing else. It has no notion of patients, samples, genes or
assays. Domain knowledge (vocabularies, descriptor extensions, importers, query shorthands,
analyses, readback templates, caveat codes) is added by packs through public extension points
(§10), and every pack leaf compiles to core clauses.
*Enforced by:* an import-boundary test: `aibi.core` MUST NOT import from `aibi.packs`, and the
core test suite runs with no packs installed. At least one non-biomedical fixture dataset is in
the core test suite. The oncology pack's exit criterion (M4) is that it ships without any change
to the core.

### 3.2 AI-interaction principles

**A1 — The model writes documents, never SQL and never numbers.**
Every number shown to a user comes from an engine result with a derivation id. The UI renders
numbers from results, not from model prose. The in-app assistant may only quote numbers from a
result it cites; it does no arithmetic of its own. Comparisons people naturally ask for
("twice as frequent", "four months shorter") are therefore computed by the analyses, with
confidence intervals (§9.3). The evals check both rules.

**A2 — One document, many editors.**
The UI, the chat assistant, MCP clients and shareable URLs all read and write the same Analysis
document. Its readback is generated deterministically from the canonical document, never by a
model, so a person can confirm that the document asks what they meant.

**A3 — Refuse rather than approximate.**
An unsupported operator, an unknown column, an ambiguous join path, or an analysis whose
requirements are not met fails loudly, names the problem, and lists what *is* available.

**A4 — No private tools.**
The in-app assistant uses exactly the public MCP tools. Anything it can do, an external agent
can do and a person can inspect.

**A5 — Proposals are visible until confirmed.**
Anything a model proposes (a key, a relationship, a descriptor field, a concept mapping) is
stored with status `proposed` and the proposing model's card. It is never silently promoted to
`asserted`, and results that depend on it carry a caveat until a person confirms it.

---

## 4. Glossary

| Term | Meaning |
|---|---|
| **Dataset** | A collection of related tables from one source, e.g. a clinical trial export, a CRM extract, or a cBioPortal study. |
| **Release** | An immutable snapshot of a dataset's data **and** descriptors, identified as `<dataset>@<n>` and by a content hash. |
| **Table** | A set of rows with a declared **grain**: what one row is (a patient, a sample, a mutation call, an order). |
| **Entity table** | A table with a primary key. Any entity table can be the **unit** a cohort counts. |
| **Relationship** | A declared many-to-one (or one-to-one) link from a child table's foreign key to a parent table's key. Relationships form the **table graph**. |
| **Column** | A variable in a table. |
| **Coverage** | A declaration of which parent rows (and, optionally, which scope values) a child table is complete for (§6.4). |
| **Endpoint** | A declared (time, status) pair on an entity table, usable for time-to-event analysis. |
| **Concept** | A dataset-independent meaning (e.g. *age at diagnosis in years*) that columns can be mapped to. |
| **Descriptor** | The structured metadata record for any of the above, or for an analysis or model. |
| **Pack** | A domain extension: concepts, descriptor extensions, importers, leaf kinds, analyses, templates (§10). |
| **Analysis document** | The JSON object declaring cohorts and views (§7). |
| **Derivation** | The canonical, hashed description of how a result was produced (§7.5). |
| **Caveat** | A structured, coded statement about a result's fitness for use (§8.3). |

---

## 5. Descriptors

### 5.1 Common envelope

Every descriptor, whatever it describes, has:

```jsonc
{
  "kind": "dataset | table | column | relationship | coverage | endpoint | concept | analysis | model",
  "id": "string, stable within its scope",
  "version": "string",
  "label": "Human-readable name",
  "definition": "One-paragraph definition in plain text",
  "provenance": { "source": "...", "pipeline": {"name": "...", "version": "..."}, "citation": ["doi:…"] },
  "fields": { /* kind-specific, §5.2–5.7 */ },
  "extensions": { "<pack id>": { /* validated against the pack's JSON Schema */ } },
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

- `imported`: taken from the source (a header row, a database comment, a declared constraint).
- `imported_default`: filled in by convention (e.g. mapping `NA` to UNKNOWN). Allowed, but it
  MUST be surfaced as a caveat wherever it affects a result.
- `undeclared`: nobody has said. The engine MUST treat undeclared semantics conservatively: an
  undeclared missing code is UNKNOWN, and an undeclared coverage makes absence UNKNOWN, never
  negative.

`extensions` is how packs add domain fields (e.g. the oncology pack's `reference_genome` on a
dataset). The core stores and validates them but never interprets them.

### 5.2 Dataset descriptor

`name`, `description`, `domain_tags`, `citation` / `references`, `source` (file set, database
and schema, or repository and commit), `license`, `data_use` (GA4GH DUO codes where relevant),
`time_origin` (what time zero means for each entity table with time columns; `undeclared`
triggers a caveat on cross-dataset time comparison), and computed fields (`n_rows` per table,
`table_graph` summary) that are filled in at release build time, never by hand.

### 5.3 Table descriptor

| Field | Meaning |
|---|---|
| `grain` | Plain-text statement of what one row is (e.g. *one adverse event report*) |
| `primary_key` | Column list, or `none`; a table without a key can be filtered and aggregated but cannot be a unit |
| `source` | Sheet, file or database table it came from, and any header or skip-row handling applied |
| `maps_to` | Optional entity concept the table's rows are instances of (e.g. `core:person`), required for a table to be the unit of a cross-dataset cohort (§7.4) |
| `observation_window` | For entity tables: the columns (or a declared rule) giving, per row, the period over which that unit's related records are complete, e.g. enrolment to last contact. Optional in v1 and unused by v1 analyses; timeline queries (M7) rely on it |

### 5.4 Column descriptor

| Field | Meaning |
|---|---|
| `datatype` | `number`, `integer`, `string`, `boolean`, `category`, `list<category>`, `date`, `datetime`, `time_offset` |
| `units` | UCUM code for numbers and offsets (`a`, `mo`, `d`, `mg/dL`, `USD`); required for any number used in a cross-dataset comparison |
| `permissible_values` | For categories: `[{value, label, concepts: [OntologyRef]}]` |
| `missing_codes` | Map from a raw token to an observation state, e.g. `{"": "UNKNOWN", "NA": "UNKNOWN", "N/A": "NOT_APPLICABLE", "Not done": "NOT_ASSESSED"}` |
| `concepts` | `[OntologyRef]` describing what the column measures |
| `maps_to` | Optional `ConceptMapping` (§5.6) |
| `list_syntax` | For `list<…>`: how lists are encoded in the source (JSON array, Python literal, delimiter) |
| `completeness` | Declared (`complete | partial | unknown`) plus computed counts per observation state |
| `source` | Original column name and any header metadata imported with it |

`OntologyRef = {system, code, label, relation: "exact | broader | narrower | related"}`. The
core accepts any `system` string; packs register the systems they validate (e.g. NCIt, LOINC,
OncoTree, HGNC).

### 5.5 Relationship and coverage descriptors

A **relationship** is `{child_table, child_columns, parent_table, parent_columns, cardinality:
"many-to-one | one-to-one", role?}`. `role` names the relationship when two tables are linked
more than once (e.g. `orders.billing_customer` and `orders.shipping_customer`).

A **coverage** declaration says for which parents a child table is complete, and over what
scope:

```jsonc
{
  "child_table": "mutations",
  "relationship": "mutations.sample_id → samples.sample_id",
  "parents": "all | { table: <coverage table>, columns: [...] } | undeclared",
  "scope_columns": ["hugo_symbol"],          // optional: completeness is per (parent, scope value)
  "record_filter": { /* core clause over the child: which kinds of rows the table holds */ }
}
```

- `parents: "all"`: every parent row was assessed; no child rows means none exist.
- `parents: {table, columns}`: a coverage table lists the assessed parents, and, when
  `scope_columns` is set, the assessed (parent, scope value) pairs. This is how a gene panel is
  expressed (the oncology pack generates it), but equally how "these sites reported adverse
  events for these visit windows" is expressed.
- `record_filter`: what the table holds, e.g. *only non-synonymous calls* or *only serious
  adverse events*. A query for rows outside the filter is **refused**, since absence of such a
  row means nothing. To keep that check decidable, a record filter is a conjunction of value
  lists on categorical columns (`{column: [allowed values]}`); a query's filter is accepted only
  if, for each such column, it can be shown to select a subset of the allowed values, and is
  otherwise refused. A query that does not constrain the column at all means "any row the table
  holds", and its readback MUST state the record filter (*any non-synonymous mutation*).
- `parent_scope`: optional core clause over the parent table naming which parents are **in
  scope** for this table at all, e.g. *tumour samples only*. Parents outside it (a blood
  normal, a sample that failed QC) are ignored when lifting (§6.3) instead of making their own
  parent UNKNOWN.
- Scope columns are matched by equality in v1 (a gene, a visit window). Range-based scope such
  as genomic intervals is out of scope for v1.
- Coverage applies to **every** downward step, including structural ones such as patient →
  samples. So that ordinary datasets are usable at once, the importer proposes `parents: "all"`
  for every relationship, with status `proposed`; packs and people replace the proposal where it
  is wrong (e.g. gene panels). Until confirmed, results that rely on it carry
  `DEFAULT_SEMANTICS`, and the curation queue ranks these confirmations first.

### 5.6 Concepts and mapping

A **concept** is a dataset-independent descriptor (`kind: concept`) with units or permissible
values. Concepts are namespaced by who defines them: `core:` for a handful of universal ones
(`core:age_years`, `core:sex`), and `<pack>:` for the rest (`onco:overall_survival`,
`onco:oncotree_code`).

```jsonc
ConceptMapping = {
  "concept": "core:age_years",
  "transform": { "unit_from": "d", "unit_to": "a" } | { "value_map": {"M": "male", "F": "female"} } | null,
  "status": CurationStatus
}
```

Mappings are **exact** only: a column maps to a concept iff it means that concept. A looser
comparison needs a looser concept (e.g. `core:age_years_approx`), not a weaker mapping.
Transforms are limited to unit conversions and value maps; anything else (e.g. age from a birth
date and a diagnosis date) is a **derived column**, declared and versioned in the release like
any other column.

A column is **mapped** if it has a `maps_to` with status `asserted`. Cross-dataset references in
documents use the concept id, not the column id (§7.4).

### 5.7 Endpoint descriptor

`table` (an entity table), `time_column`, `status_column`, `time_units`, `event_coding` (which
status values are events and which are censored), `time_origin` (defaults to the dataset's for
that table), and optionally `maps_to` a concept (e.g. `onco:overall_survival`). The core
detects nothing by name; packs and the curation assistant propose endpoints.

---

## 6. Data model and query semantics

### 6.1 The table graph

A dataset's tables and relationships form a directed graph (child → parent). The engine
supports two moves along it:

- **Up** (child to parent, many-to-one): a parent's column is a single value for each child
  row. A cohort of samples can be filtered on its patient's age.
- **Down** (parent to children, one-to-many): a parent is related to zero or more child rows,
  so a child-level criterion becomes an **existence** question with a quantifier (§6.3).

Relationships are many-to-one or one-to-one, declared on the child. A many-to-many relationship
(participants ↔ trials) is modelled through its linking table (enrolments) as an ordinary table
in the graph, so every step is either a lookup or an existence question. Paths may go up and
then down (samples → patient → treatments: *samples whose patient has a treatment row*);
readbacks MUST say so explicitly.

Paths MUST be unambiguous. If more than one relationship path connects the unit to a referenced
table, the compiler refuses and lists the paths; the document names one with `via`. There is
no silent shortest-path choice (biai's BFS was a source of wrong joins).

### 6.2 Observation states

Every (unit, variable) pair evaluated by the engine has exactly one state:

| State | Meaning | Examples |
|---|---|---|
| `PRESENT` | A value or a matching related row exists | `age = 61`; an adverse-event row of grade 3 |
| `ABSENT` | It was assessed, and there is none | A covered parent with no matching child row |
| `NOT_ASSESSED` | It is known that it was not assessed | A parent outside the table's coverage; a `Not done` code |
| `NOT_APPLICABLE` | The question does not apply | `N/A` mapped to NOT_APPLICABLE |
| `UNKNOWN` | There is no information either way | Empty cell, undeclared missing code, undeclared coverage |

For ordinary columns, a negative answer (`"No"`) is a `PRESENT` value. `ABSENT` exists for
**existence** questions over related rows.

### 6.3 Three-valued logic

Leaf predicates evaluate per unit to `TRUE`, `FALSE` or `UNKNOWN` (Kleene logic):

| Observation state | Value predicate (`age > 60`) | Existence predicate (`exists adverse_events where grade ≥ 3`) |
|---|---|---|
| PRESENT | TRUE or FALSE, by the value | TRUE if a matching row exists |
| ABSENT | — | FALSE |
| NOT_ASSESSED | UNKNOWN | UNKNOWN |
| NOT_APPLICABLE | FALSE | — |
| UNKNOWN | UNKNOWN | UNKNOWN |

- `all` is Kleene AND, `any` is Kleene OR, `not` swaps TRUE and FALSE and leaves UNKNOWN.
- A unit is **in** a cohort iff the cohort predicate is TRUE.
- `known(clause)` is TRUE iff the clause is not UNKNOWN; `unknown(clause)` is its complement.
  These are the only way to deliberately include unknowns.
- `exists` with `quantifier: "all"` is TRUE if the unit has at least one covered child and every
  one matches; FALSE if any covered child fails to match; UNKNOWN otherwise. An empty set of
  children is UNKNOWN, never vacuously TRUE, and the readback says so.
- `min_count: k` is TRUE with at least *k* matching rows; FALSE if the unit is covered and has
  fewer than *k*; UNKNOWN otherwise.
- **Existence** is evaluated per unit as: TRUE if at least one related row matches; otherwise
  FALSE if the unit is covered (§6.4) for the whole scope the predicate asks about; otherwise
  UNKNOWN. So `not exists mutations where gene = TP53` means *assessed for TP53 and no TP53
  row*, and it excludes units that were never assessed.
- **Lifting through intermediate tables.** When a criterion sits two or more steps down
  (patient → sample → mutation), each step down is its own existence quantifier: `any`
  (default: Kleene OR over the children) or `all` (Kleene AND over the children). Children
  outside the child table's `parent_scope` (§5.5) are ignored. A parent with no in-scope
  children at that step is UNKNOWN unless the step's own coverage says the parent has none.
  Consequence: a patient with one assessed wild-type tumour sample and one unassessed tumour
  sample is UNKNOWN for "has a TP53 mutation in any sample", while an unassessed blood normal
  changes nothing.
- `"lift": "assessed"` on a leaf opts into the cBioPortal convention instead: only children
  assessed for the question are considered, so the same patient is FALSE. Whichever rule is
  used, the result reports how many units the choice affected.
- Where aibi's answer differs from what the cBioPortal convention would give, the result says
  so in a `CONVENTION_DIFFERS` caveat. It does not report the alternative number.
- A value predicate that reaches a list-valued column matches if any list item matches;
  `{"match": "all"}` requires every item to.

### 6.4 Coverage evaluation

For a unit `u` and an existence predicate over child table `T` with filter `F`:

1. If `F` asks for rows outside `T`'s `record_filter`, the document is refused.
2. If a matching row exists, TRUE.
3. If `T`'s coverage is `undeclared`, UNKNOWN.
4. If coverage is `all` and has no scope columns, FALSE.
5. If coverage is `{table, columns}` without scope columns: FALSE if `u` is listed in the
   coverage table, otherwise UNKNOWN.
6. With scope columns: if `F` fixes the scope column(s) to specific values, FALSE iff `u` is
   covered for every one of them; otherwise UNKNOWN. If `F` leaves the scope open, the answer
   is evaluated over the scope values `u` is covered for, and the result carries
   `SCOPE_PARTIAL` naming the restriction (e.g. *no mutation among the genes assessed*).

### 6.5 Accounting

Every cohort result reports `n_true`, `n_false` and `n_unknown` over the unit's base table, and
for each leaf how many units it made UNKNOWN. This shows when a cohort shrank because of
missing data rather than because of the criterion. Whenever `n_unknown` is above zero, the UI
and the assistant MUST show it next to the cohort size, with its main reason (e.g. *120
patients; 40 could not be evaluated: not assessed for copy number*).

---

## 7. Analysis document

### 7.1 Shape

```jsonc
{
  "aibi": "1",                                   // document format version
  "packs": { "onco": "1.2" },                    // packs whose leaves or analyses are used, pinned
  "params": { "min_grade": 3 },                  // optional; exact "$name" value substitution, as in cbio-lab
  "dataset": "trial_xyz",                        // default dataset; may be pinned: "trial_xyz@3"
  "unit": "participants",                        // any entity table in the dataset
  "cohorts": {
    "<name>": {
      "all": [ Clause ],                         // [] = every row of the unit table
      "dataset": "<id>",                         // optional override, or:
      "datasets": ["<id>", …],                   // cross-dataset cohort (§7.4)
      "unmapped": "allow",                       // optional, §7.4
      "notes": "plain text, never compiled"
    }
  },
  "views": [ { "analysis": "<registry id>", "cohorts": ["<name>", …], "reference": "<name>",
               "overlap": "allow", "params": { … }, "note": "plain text" } ],
  "notes": "plain text"
}
```

`Clause := Leaf | {"all": [Clause]} | {"any": [Clause]} | {"not": Clause} | {"known": Clause} | {"unknown": Clause}`.

Leaves that cross a downward step accept `"lift": "any" | "all" | "assessed"` (§6.3).

`views[].reference` names the reference group for effect sizes (hazard ratios, differences,
risk ratios); it defaults to the first cohort in `views[].cohorts`, and canonicalisation writes
it explicitly. Cohorts in one view MUST NOT share units unless the view says
`"overlap": "allow"`, in which case every test result carries `COHORTS_OVERLAP`; otherwise the
document is refused with the overlap count, because the tests assume independent groups.

Caps: depth 4, 32 leaves per cohort, 6 cohorts, 8 views per document, so that readbacks stay
readable and compiled queries stay bounded.

The shape stays compatible with cbio-lab documents. A translator (M4) converts cbio-lab
documents into aibi documents and flags every `not` whose meaning changes under three-valued
logic.

### 7.2 Core leaf kinds

| Kind | Shape | Meaning |
|---|---|---|
| `value` | `{column: "<table>.<column>" \| concept, values?, range?: {gt,gte,lt,lte}, op?, value?, via?, match?}` | A value predicate on the unit's own table or any table reachable from it. Reaching a table below the unit implies `exists` with `any`. Units in `range` MUST match the column's `units` or be convertible |
| `exists` | `{table, where?: [Clause], quantifier?: "any" \| "all", min_count?, via?}` | An existence predicate over a child table, with coverage semantics (§6.4). `min_count` asks for at least *k* matching rows |
| `covered` | `{table, scope?: {<column>: value}}` | TRUE if the unit is inside the table's coverage (for the scope, if given) |
| `ids` | `{ids: ["<dataset>:<key>", …]}` | Explicit lists of unit keys. Refused on datasets with `allow_row_ids: false` (§11), since a count over a chosen id reveals that unit's attributes |
| `cohort` | `{cohort: "<name>"}` | Another cohort in the same document, by name; cycles are refused. `{"all": [{"cohort": "base"}, {"not": X}]}` is the correct "rest of the base" under three-valued logic, which is why references exist: comparing X with not-X within a base is the most common pattern and easy to get wrong by hand |

`where` clauses inside `exists` are evaluated per child row with the same logic, so criteria
nest along the graph.

### 7.3 Pack leaf kinds

Packs register additional leaf kinds (§10.2), namespaced by pack id: e.g. `onco.genomic`
(`{"kind": "onco.genomic", "q": "EGFR: AMP; PTEN: HOMDEL"}`). A pack leaf MUST compile to core
clauses, and `validate_document` returns that expansion so the readback and the derivation are
in core terms. Canonicalisation (§7.5) stores the pack leaf **and** the pack version, so a pack
change that alters an expansion changes the derivation.

### 7.4 Cross-dataset references

A cohort may name several datasets (`"datasets": [..]`). Inside such a cohort, `value` leaves
MUST reference a `concept`, the unit MUST be given as a concept-mapped entity table in each
dataset, and every dataset MUST have an asserted mapping for each concept used, or the document
is refused with a list of what is missing. `"unmapped": "allow"` on the cohort turns the
refusal into an `UNMAPPED_COMPARISON` caveat on every result that uses it. Tests across
datasets are stratified by dataset; pooled values are reported alongside, labelled as such.

### 7.5 Canonical form and derivation id

Before compilation, the server canonicalises the document:

1. Substitute `params`.
2. Resolve every dataset reference to a pinned release (`<dataset>@<n>`) and record its
   manifest hash; pin every pack to its exact version.
3. Resolve column, table and concept references to descriptor ids and versions; resolve join
   paths and write them explicitly as `via`.
4. Write every semantically relevant default explicitly (`quantifier`, `match`, analysis
   parameter defaults).
5. Sort order-insensitive collections: values in `values`, and clauses inside `all` / `any`,
   by their own canonical serialisation.
6. Replace each `cohort` reference with the referenced cohort's canonical form, so that
   references survive the removal of names.
7. Write each view's `reference` explicitly and keep the order of `views[].cohorts`; drop fields
   that do not affect results (`notes`, `note`, cohort names, the order of the top-level
   `cohorts` map).

The canonical form is serialised with the JSON Canonicalization Scheme (RFC 8785).

- A **cohort derivation id** is `drv:` + SHA-256 of `{canonical cohort, unit, release pins,
  pack pins}`.
- A **result derivation id** is `drv:` + SHA-256 of `{analysis id@version, canonical
  parameters (including `reference` and `overlap`), the derivation ids of its cohorts in view
  order, and the deployment settings that change outputs (`min_cell_count`)}`.
- A **result digest** is the SHA-256 of the canonical result values after rounding: floating
  point values are rounded to 12 significant digits before serialisation, so that parallel
  aggregation and optimiser noise cannot change a digest. Values are *returned* unrounded.

Cohort names are not part of any hash (step 7). Values in results are keyed by cohort derivation id, and
names are attached as labels outside the digest, so renaming a cohort changes neither id nor
digest.

Derivation ids are designed to be citable (e.g. through a future public resolver), but v1 makes
no promise that any release stays available.

Invariant: the same result derivation id MUST produce the same result digest. A code change
that alters the digest for an unchanged derivation id is a bug unless the analysis (or pack)
version was bumped. Golden tests check this (§13.3).

### 7.6 Readback

The server renders a deterministic, plain-language readback of each canonical cohort and view
from templates (e.g. *"Participants in Trial XYZ @3 aged over 60 years at enrolment, with at
least one adverse event of grade 3 or higher among sites that reported adverse events"*). Packs
supply templates for their leaves, so an `onco.genomic` leaf reads as *"with EGFR amplified in
any sample assessed for copy number"*, not as its core expansion. Readbacks are returned with
every result and shown next to every figure.

---

## 8. Result contract

### 8.1 Envelope

```jsonc
{
  "derivation": {
    "id": "drv:…",
    "document": { /* canonical form */ },
    "analysis": { "id": "survival.km", "version": "1.0.0" },
    "releases": [ { "dataset": "trial_xyz", "release": 3, "manifest": "sha256:…" } ],
    "packs": { "onco": "1.2.0" },
    "engine": "aibi 0.3.1"
  },
  "digest": "sha256:…",
  "readback": { "cohorts": { "<cohort derivation id>": "…" }, "view": "…" },
  "population": { "<cohort derivation id>": { "n_true": 0, "n_false": 0, "n_unknown": 0, "unknown_by_leaf": { … } } },
  "labels": { "<cohort derivation id>": "<cohort name>" },    // outside the digest
  "values": { /* analysis-specific, keyed by cohort derivation id */ },
  "charts": [ /* Vega-Lite specifications generated from values; outside the digest */ ],
  "caveats": [ Caveat ]
}
```

The compiled SQL is not part of `run_analysis` responses; `explain` returns it for any
derivation id.

A deployment setting `min_cell_count` (default: off) suppresses any count below the threshold,
together with anything from which it could be recovered, and marks it as suppressed. It exists
for shared or restricted deployments and for future federation. Because it changes outputs, it
is part of the result derivation (§7.5).

### 8.2 Proportions

Every proportion in `values` is an object, never a bare number:

```jsonc
{
  "estimate": 0.412,
  "numerator": 124,
  "denominator": 301,
  "denominator_definition": "participants in cohort 'over 60' covered by adverse_events reporting",
  "excluded": { "NOT_ASSESSED": 17, "UNKNOWN": 3 },
  "ci": { "method": "wilson", "level": 0.95, "low": 0.357, "high": 0.469 }
}
```

### 8.3 Caveats

`Caveat = {code, severity: "info | warn | block", message, affects: [paths into values]}`.
Codes are a stable, documented enum; core codes are unprefixed and pack codes are namespaced
(`onco.DRIVER_ANNOTATION_PIN`). The initial core set:

| Code | Severity | Raised when |
|---|---|---|
| `UNKNOWN_EXCLUDED` | warn | A cohort or denominator excluded units because they were UNKNOWN or NOT_ASSESSED |
| `SCOPE_PARTIAL` | warn | An existence answer was evaluated only over the scope values a unit is covered for (§6.4) |
| `DEFAULT_SEMANTICS` | warn | Any `imported_default` or `proposed` descriptor field affected the result |
| `UNMAPPED_COMPARISON` | warn | A cross-dataset comparison used columns without an asserted concept mapping |
| `TIME_ORIGIN_MISMATCH` | **block** | Time-based values were compared across datasets whose `time_origin` is undeclared or different |
| `COHORTS_OVERLAP` | warn | Cohorts in a view share units (allowed only with `overlap: "allow"`); test assumptions do not hold |
| `DRAFT_RELEASE` | warn | The result was computed against a curation session's draft release |
| `SMALL_N` | warn | A group fell below the analysis's declared minimum for a reliable estimate |
| `PH_VIOLATED` | warn | A Cox model's proportional-hazards test failed; the hazard ratio is an average over time |
| `CONVENTION_DIFFERS` | info | The answer differs from the cBioPortal convention (e.g. strict lifting, §6.3) |
| `POOLED_ACROSS_DATASETS` | info | A pooled value is reported next to a stratified one |

Pack caveat codes MUST declare a severity the same way.

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
  "definition": "Kaplan–Meier estimate per cohort; k-sample log-rank test when there are ≥2 cohorts; median time with CI; difference in medians and unadjusted Cox hazard ratio, with CIs, versus the first cohort.",
  "requires": [
    { "role": "endpoint", "kind": "endpoint", "on": "unit" },
    { "role": "cohorts", "min": 1, "max": 6 }
  ],
  "params": { /* JSON Schema, generated from a Pydantic model */ },
  "returns": { /* JSON Schema of `values` */ },
  "method": { "library": "lifelines", "version": "…", "references": ["doi:…"] },
  "assumptions": ["independent censoring"],
  "caveats": ["UNKNOWN_EXCLUDED", "SMALL_N", "TIME_ORIGIN_MISMATCH"],
  "min_group_n": 10
}
```

Analyses are registered only by the core and by packs, through code review. Users cannot
upload analyses in v1.

An endpoint requirement `"on": "unit"` means the endpoint must be on the unit table itself, not
reached by a lookup: with samples as the unit and a patient-level endpoint, a patient with
several samples would be counted several times and the independence assumption would fail.

Requirements are stated against **core** descriptor kinds (an endpoint, a numeric column, a
child table with declared coverage). A pack analysis may additionally require pack extension
fields.

### 9.2 Applicability

`applicable_analyses(dataset, unit)` matches each entry's `requires` against the dataset's
descriptors and returns, for each analysis, `available`, `unavailable` (with the missing
requirement named) or `available_with_caveats` (e.g. an endpoint whose event coding is
`imported_default`). The dataset page and `describe_dataset` both show this.

### 9.3 Core analyses (v1)

| Id | Returns |
|---|---|
| `summary.distribution` | Per column: category counts or numeric summary and histogram, per cohort, with observation-state counts |
| `compare.columns` | Per column: chi-squared (categorical) or Welch t / Mann–Whitney (numeric) across cohorts; Benjamini–Hochberg q across columns |
| `compare.existence` | Per existence predicate (e.g. "has a grade ≥3 AE", "has a TP53 mutation"): proportion per cohort over covered units (§8.2); risk difference and risk ratio with CIs; Fisher exact test for 2 cohorts, chi-squared otherwise; BH q across predicates |
| `survival.km` | As in §9.1 |
| `survival.cox` | Hazard ratios with CIs for cohort membership plus up to 8 covariates (columns or existence predicates; categorical covariates dummy-coded against their most common level); optional stratification; a proportional-hazards test on every fit, raising `PH_VIOLATED` when it fails |

---

## 10. Domain packs

### 10.1 What a pack is

A pack is a Python package with a manifest (`id`, `version`, required core version) that
registers any of:

| Extension point | Example (oncology pack) |
|---|---|
| **Concepts** | `onco:overall_survival`, `onco:oncotree_code`, `onco:tmb_nonsynonymous` |
| **Ontology systems** | OncoTree, HGNC, NCIt, LOINC validators |
| **Descriptor extensions** (JSON Schema per descriptor kind) | `reference_genome` on datasets; `assay`, `variant_scope`, `value_semantics` (CNA encoding, z-score reference population) on tables |
| **Importers** | cBioPortal study directories → tables, relationships, coverage tables, descriptors |
| **Curation proposers** | Detect `OS_MONTHS` / `OS_STATUS` pairs and propose an endpoint |
| **Leaf kinds** (with compile-to-core, a JSON Schema, and readback templates) | `onco.genomic` (OQL subset: `MUT`, `MUT=<change>`, classes, `AMP`, `HOMDEL`, `FUSION`) |
| **Analyses** | `onco.alteration_frequency` (a specialisation of `compare.existence` over genes), later oncoprint |
| **Caveat codes** | `onco.COVERAGE_ASSUMED_WES` |

Packs do not add MCP tools. A visual output such as an oncoprint is an analysis
(`onco.oncoprint`, after v1) whose values are a render specification, so it stays inside the
registry and the result contract.

Packs live in this repository (`aibi/packs/`) until the extension points are stable, then move
to separate packages so that other groups can publish their own.

Packs MUST NOT patch or monkey-patch the core. If a pack needs something the extension points
do not offer, the extension point is added to the core in its own change, with a
domain-neutral test.

### 10.2 How cBioPortal maps onto the core

This mapping is the working test of P8:

| cBioPortal | aibi core |
|---|---|
| Study | Dataset |
| Patient, sample | Entity tables; `samples.patient_id → patients.patient_id` |
| Clinical attributes (with the four header rows) | Columns, with `label`, `definition`, `datatype` imported |
| Mutation, CNA, structural-variant data | Child tables of `samples` (CNA as a long table of non-neutral calls plus its own coverage) |
| Case lists + gene panel matrix + panel definitions | Coverage tables keyed by (sample, gene), generated by the importer |
| `NA`, `[Not Available]`, `[Not Applicable]`, … | `missing_codes` with `imported_default` status |
| OS / PFS / DFS / DSS | Endpoints on `patients` |
| OQL | The `onco.genomic` leaf, compiled to `exists` clauses |
| "Altered in x% of profiled samples" | `compare.existence` over covered units |

### 10.3 Other packs

None are in v1; a non-biomedical fixture in the core test suite keeps the core honest in the
meantime. A second pack (clinical trials: adverse-event grading, site and visit coverage) is the
first item after v1, to test the extension points against a domain other than oncology.

---

## 11. MCP and HTTP surface

One set of Python functions backs both the HTTP API (FastAPI) and the MCP server. Tool schemas
are generated from the same Pydantic models as the HTTP API. Packs contribute leaf kinds and
analyses to these tools; they do not add tools of their own in v1.

| Tool | Purpose | Touches row data? |
|---|---|---|
| `search_catalog` | Faceted search over datasets: domain tags, tables and their grains, concepts present, row counts, completeness thresholds, data-use codes, pack-defined facets (e.g. cancer type, assay) | No |
| `describe_dataset` | Dataset descriptor, table graph, columns, coverage, endpoints, applicable analyses, standing caveats | No (computed stats only) |
| `describe_column` | Full descriptor with observation-state counts and value distribution | Aggregates only |
| `list_analyses` | Registry entries, optionally filtered by applicability | No |
| `validate_document` | Canonicalise, check, expand pack leaves and read back a document without running it; returns errors, caveats that would be raised, and derivation ids | No |
| `count_cohort` | Evaluate the cohorts of a document and return only `n_true` / `n_false` / `n_unknown`, unknowns by leaf, and the readback | Counts only |
| `run_analysis` | Run a document; returns results per §8 | Yes |
| `explain` | Given a derivation id: canonical document, readback, releases, pack pins, SQL, analysis descriptor | No |
| `curation_queue` | Keys, relationships, coverage and descriptor fields that are `undeclared`, `imported_default` or `proposed` | No |
| `propose_descriptor` | Record a proposed value for any of those, with rationale and model card | No |

Confirming a proposal (`proposed` → `asserted`) is a UI action by a person, not an MCP tool, in
v1, so an agent can never confirm its own proposal.

Listing row-level keys of a cohort (for follow-up) is controlled per dataset by
`allow_row_ids`, on by default in local and lab deployments; turning it off leaves only
aggregates available through every tool, and also refuses the `ids` leaf (§7.2). Descriptors are also exposed as MCP resources (`aibi://dataset/<id>@<n>/column/<table>.<column>`).

---

## 12. Architecture

### 12.1 Components

```
          Web UI (React + TS)          External agents (Claude, …)
                 │  HTTP                        │  MCP
                 ▼                              ▼
        ┌──────────────────────────────────────────────────┐
        │  aibi server (Python, one process)               │
        │                                                  │
        │  api/ (FastAPI)            mcp/ (MCP SDK)         │
        │          └──────────┬──────────┘                 │
        │               service functions                  │
        │   ┌────────────┬────┴─────────┬──────────────┐   │
        │   catalog      engine          analyses     assistant
        │   (descriptors (canonicalise,  (registry,   (Claude API,
        │    & search)    3VL compile,    lifelines,   uses the MCP
        │                 SQLGlot→DuckDB) scipy)       tools only)
        │   └──────┬─────┴──────┬────────┘             │
        │    app DB (SQLite)   releases (Parquet + manifest, DuckDB)
        │                                                  │
        │   importers: files (CSV/TSV/XLSX/ODS/Parquet),   │
        │              databases (snapshot)                │
        │   ─────────────── extension points ───────────── │
        │   packs/onco: concepts, cBioPortal importer,     │
        │               onco.genomic leaf, analyses        │
        └──────────────────────────────────────────────────┘
```

### 12.2 Storage

- **Releases:** `data/releases/<dataset>/<n>/` holds one Parquet file per table,
  `descriptors.json`, and `manifest.json` listing every file's SHA-256. The manifest's own hash
  identifies the release. Unchanged Parquet files are shared between releases by hash, so a
  descriptor-only release costs almost nothing. Releases are never modified or deleted in place.
- **App DB (SQLite):** the catalogue index (a denormalised copy of descriptors for search),
  saved documents, the curation queue and its audit trail, and the result cache keyed by
  derivation id.
- **Query engine:** DuckDB reading the release's Parquet files. The compiler builds queries as
  SQLGlot expression trees and never concatenates identifiers or values into strings; every
  identifier comes from a descriptor.

### 12.3 Stack

| Layer | Choice |
|---|---|
| Language and tooling | Python ≥ 3.12, uv, ruff, pyright (strict on `core/`), pytest, hypothesis |
| Schemas | Pydantic v2 as the source of truth; JSON Schema and OpenAPI generated from it |
| API and MCP | FastAPI; the official MCP Python SDK |
| Data | DuckDB (including its readers for CSV, Excel, Parquet, Postgres, MySQL and SQLite), Parquet, SQLite |
| Statistics | lifelines, scipy, statsmodels |
| Assistant | Claude API, calling the same MCP tools |
| Charts | Vega-Lite specifications generated on the server from result values and returned with the result, so the UI and agents draw the same figure |
| Frontend | React + TypeScript + Vite, types generated from OpenAPI; renders the Vega-Lite specifications, never charts from model text |
| Deployment | One process on a lab server, no user accounts in v1; the same package runs locally |

### 12.4 Repository layout

```
aibi/
  SPEC.md  CLAUDE.md  README.md
  server/
    pyproject.toml
    src/aibi/
      core/
        schema/      # Pydantic models: descriptors, documents, results, caveats, pack API
        store/       # release builder and reader, app DB
        importers/   # files and database snapshots
        catalog/
        engine/      # canonicalise → validate → plan → SQL; three-valued logic; coverage
        analyses/    # registry and core analyses
        api/  mcp/  assistant/
      packs/
        onco/        # concepts, descriptor extensions, cBioPortal importer, onco.genomic, analyses
    tests/
      core/          # runs with no packs installed
      packs/onco/
  web/
  fixtures/          # small public datasets: at least one non-biomedical, one spreadsheet, one cBioPortal study
```

---

## 13. Import, validation and testing

### 13.1 Generic import (core)

- **Files:** CSV/TSV (delimiter, header row, skip rows and encoding detected, then recorded as
  `imported_default`), XLSX/ODS (each sheet a table), Parquet.
- **Databases:** Postgres, MySQL, SQLite and DuckDB tables, read through DuckDB and snapshotted
  into a release. Declared primary and foreign keys, and column comments, are imported with
  status `imported`.
- **Structure proposal:** for files, the importer proposes grain, primary keys, relationships
  (by value containment, as biai's foreign-key detector did), datatypes, list columns and
  missing codes. Everything proposed lands in the curation queue; nothing proposed is
  `asserted`.

### 13.2 Validation as a gate

Structural errors (unparseable files, duplicate primary keys, foreign keys that reference
missing parent rows, a coverage table that references unknown parents) stop the import, or,
for proposed rather than declared keys, drop the proposal with the evidence. Semantic gaps
(missing units, undeclared coverage, unknown missing codes) do not stop it; they become
`undeclared` or `imported_default` fields in the curation queue. Packs add their own
validators (the oncology pack's mirrors the cBioPortal validator).

### 13.3 Tests that encode the principles

- **Three-valued logic (property tests):** for any cohort predicate `C`, `n_true(C) +
  n_false(C) + n_unknown(C)` equals the unit base; `not(not C) ≡ C`; `C` and `not C` never
  share a unit; `known(C)` equals `C ∪ not C`.
- **Missing is not negative:** on a fixture with an uncovered parent, `not exists …` excludes
  it and the result reports it under `UNKNOWN_EXCLUDED`; with coverage `undeclared`, every
  absence is UNKNOWN.
- **Ambiguous paths:** a fixture with two relationship paths is refused without `via`.
- **Canonical form:** reordering top-level cohorts or renaming a referenced cohort leaves every
  id unchanged; swapping a view's `reference` changes the result id and inverts the hazard
  ratio; a digest is stable across repeated runs with parallel execution.
- **Overlap and row ids:** overlapping cohorts in a view are refused without `overlap: "allow"`;
  an `ids` leaf is refused when `allow_row_ids` is off.
- **Quantifiers:** `quantifier: "all"` over zero children is UNKNOWN; `min_count` follows §6.3.
- **Lifting:** a patient with one assessed wild-type tumour sample and one unassessed tumour
  sample is UNKNOWN by default and FALSE with `lift: "assessed"`; an unassessed sample outside
  `parent_scope` changes neither answer.
- **Derivation stability (golden tests):** each fixture document has a checked-in derivation
  id and result digest; CI fails if either changes without an analysis or pack version bump.
- **Statistical correctness:** KM curves, log-rank, Fisher and BH values agree with reference
  outputs from R (`survival`, `stats`) computed once and checked in.
- **Refusal:** each refusal rule (unknown column, out-of-filter existence query, unmapped
  cross-dataset reference, ambiguous path, unmet analysis requirement) has a test asserting
  that the error lists the available alternatives.
- **Domain boundary:** an import-linter contract forbids `aibi.core` → `aibi.packs`; the core
  suite runs with no packs installed and includes a non-biomedical fixture.
- **Assistant evals:** a small set of questions whose answers the assistant must reach through
  the tools, citing derivation ids and surfacing required caveats.

---

## 14. Milestones

The order is agent-first: from M1 an external agent (Claude over MCP) is the primary interface,
and the web UI follows once the semantics are settled. A thin read-only page (catalogue and
dataset descriptors) ships with M1 so there is something to show without an agent.

| # | Deliverable | Exit criterion |
|---|---|---|
| **M0** | Repo skeleton, CI, Pydantic schemas for descriptors, documents, results, caveats and the pack API; JSON Schema export; import-boundary check | Schemas published; CI runs lint, type check, boundary check and tests |
| **M1** | Generic importers (files and database snapshots) with key, relationship and coverage proposals and a validation gate; release builder; catalogue; `search_catalog`, `describe_dataset`, `describe_column`, `curation_queue`; per-session curation releases; read-only catalogue page | biai's example spreadsheets and a non-biomedical dataset imported with confirmed keys and relationships; an MCP client can find and describe them |
| **M2** | Engine: table-graph resolution, canonicalisation, derivation ids, three-valued compile with coverage, `parent_scope` and strict lifting, core leaves including `cohort` references, readbacks, `validate_document`, `count_cohort`, `explain` | Property, coverage, lifting and ambiguous-path tests pass |
| **M3** | Registry and the five core analyses (including Cox and the effect sizes of §9.3); `applicable_analyses`; `run_analysis`; Vega-Lite output | Golden and R-reference tests pass |
| **M4** | Oncology pack: cBioPortal importer and validator, coverage tables from panels, `parent_scope` for normal samples, `onco.genomic`, `onco.alteration_frequency`, concepts, endpoint proposer; cbio-lab translator | TCGA GBM PanCan and one panel study imported; cbio-lab examples 1 and 4 reproduced; **no core change in the pack's PR** |
| **M5** | Web UI: catalogue, dataset page with table graph and applicable analyses, cohort builder with live counts including unknowns, results with provenance panel, curation queue | biai's exploration e2e scenarios, ported, pass |
| **M6** | Assistant (chat that edits the document); AI-proposed descriptors, keys, relationships and coverage with confirmation; concept mappings and cross-dataset queries | Assistant evals pass; a mapped two-dataset survival comparison runs with a stratified log-rank |
| **M7+** | Clinical-trials pack; event tables and time-window leaves using observation windows; `onco.oncoprint`; driver annotation (onco); JSON-LD / Bioschemas export | Set when M6 lands |

---

## 15. Open questions

| # | Question | Current leaning |
|---|---|---|
| Q1 | Should aibi converge with cbio-lab (one engine, one DSL) rather than sit beside it? | Keep documents compatible and ship the translator; decide after M4, when the oncology pack can be compared with cbio-lab on the same studies |
| Q7 | Who may confirm proposals? | Any user in v1, recorded in an audit log under a self-declared name. A curator role (and therefore user accounts) is a precondition for any public or shared deployment |
| Q9 | Who are the first users: this lab only, or outside groups? | Assumed this lab only. Outside users bring user accounts and Q7's curator role forward |

---

## 16. Decisions

Decisions from the first full review (D1–D19) and the consistency review (D20–D32), 2026-09-24.
Each line records the choice and the reason;
reopening one means changing this table.

| # | Topic | Decision | Reason |
|---|---|---|---|
| D1 | A1, model and numbers | Strict: the model only quotes numbers from cited results; analyses compute the comparisons people ask for, with CIs | A plausible wrong number is the failure nobody catches; a bare "2×" without an interval shouldn't be trusted anyway |
| D2 | A3, cross-dataset comparisons | Refused by default; explicit opt-in marks every affected result | The assistant turns a refusal into a next step (map the columns), and warnings are easy for agents to skip |
| D3 | Scope | Timeline queries after v1, with observation windows in the v1 schema; Cox regression in v1; JSON-LD and federation after v1 | Timelines are most of the missing-data difficulty; Cox is cheap once survival exists, and unadjusted survival comparisons are weak evidence |
| D4 | Curation releases | Confirmations are batched into one release per curation session | Keeps P6 without a release per click |
| D5 | Coverage defaults | Importer proposes coverage for obvious cases (`proposed`, with caveat); open-scope existence answered with `SCOPE_PARTIAL`; scope columns equality-only in v1 | Datasets are useful immediately and still honest; matches what cBioPortal users expect |
| D6 | Concepts | Small `core:` set plus pack vocabularies anchored to NCIt and LOINC; mappings exact only; transforms limited to units and value maps, other derivations as declared columns | Keeps comparisons meaningful and every derivation visible |
| D7 | Table graph | Many-to-one and one-to-one only (many-to-many through a linking table); up-then-down paths allowed with explicit readbacks; units need a primary key | Every step is a lookup or an existence question |
| D8 | Three-valued logic | NOT_APPLICABLE is FALSE; unknown counts always shown when above zero; differences from cBioPortal explained by caveat, not a second number | Matches plain meaning; makes P2 visible; two numbers invite choosing the convenient one |
| D9 | Lifting (was Q2) | Strict by default; children outside `parent_scope` ignored; `lift: "assessed"` opt-in; affected count always reported | Honest where it matters (an unassessed metastasis), no noise from blood normals or failed samples |
| D10 | Document shape | cbio-lab-compatible with a translator that flags `not`; caps kept; `cohort` references added | Most common comparison is X vs not-X within a base, which is easy to get wrong by hand |
| D11 | Derivation ids | Engine version and cohort names excluded from hashes; ids designed to be citable, no availability promise in v1 | Ids stay stable for caching and citation; version bumps carry result changes |
| D12 | Results | `TIME_ORIGIN_MISMATCH` blocks; `SMALL_N` warns; SQL only via `explain`; `min_cell_count` setting, off by default | A comparison of different clocks is meaningless; small groups are imprecise but informative |
| D13 | Analyses | `survival.km` includes the unadjusted HR; Cox always tests proportional hazards and warns on failure; only core and packs register analyses | "How much worse" always follows "is it worse"; user-uploaded analyses would skip the golden-test discipline |
| D14 | Packs (was Q8) | No pack MCP tools (visual outputs are analyses returning render specifications); packs in this repo until stable; second pack right after v1 | Keeps A4 auditable and everything inside the registry |
| D15 | MCP | Confirmation stays UI-only; `count_cohort` added; row-level ids controlled per dataset by `allow_row_ids` | An agent can't confirm its own proposal; counting first is the most common agent step |
| D16 | Deployment and scale (were Q3, Q4) | Lab server without user accounts, also runnable locally; up to ~100k units and ~10M child rows per dataset | Fits TCGA- and MSK-scale studies on one machine |
| D17 | Charts | Vega-Lite specifications generated on the server with each result | The UI and agents draw the same figure |
| D18 | Order | Agent-first with an early read-only page; oncology pack before the UI; built-in assistant at M6 | External agents cover the AI-native use from M1; the P8 test happens while the core is cheap to change |
| D19 | Concept ownership, live databases (were Q5, Q6) | As D6; snapshots only, with scheduled re-snapshots creating releases | Keeps P6 |
| D20 | Reference group | Effect sizes use an explicit `views[].reference`, defaulting to the first cohort and written into the canonical form; view cohort order is kept | Otherwise reordering cohorts inverts a hazard ratio without changing its id |
| D21 | Cohort references | Resolved to the referenced cohort's canonical form during canonicalisation | Names are not hashed, so references must not depend on them |
| D22 | Digest stability | Values rounded to 12 significant digits before hashing; returned unrounded | Parallel aggregation and optimisers are not bit-reproducible |
| D23 | Suppression | `min_cell_count` is part of the result derivation | A setting that changes outputs must change the id |
| D24 | Row ids | With `allow_row_ids: false` the `ids` leaf is refused | A count over a chosen id reveals that unit's attributes |
| D25 | Overlapping cohorts | Refused in a view unless `overlap: "allow"`, then `COHORTS_OVERLAP` (warn) | Tests assume independent groups |
| D26 | Structural coverage | Importer proposes `parents: "all"` for every relationship; queue ranks these first | Otherwise every negative criterion over a child table is UNKNOWN on a fresh import |
| D27 | Record filters | Conjunctions of value lists on categorical columns; queries accepted only if provably inside; readbacks state the filter | Keeps the refusal rule decidable and the meaning of "any row" visible |
| D28 | Empty quantifiers | `quantifier: "all"` over zero children is UNKNOWN; `min_count` is three-valued | Vacuous truth is surprising and would put units into cohorts on no evidence |
| D29 | Coverage table without scope | FALSE if the parent is listed, else UNKNOWN | Closes a missing case in §6.4 |
| D30 | Draft releases | Queries during a curation session use a draft release, carry `DRAFT_RELEASE`, and are neither cached nor citable | Keeps D4 compatible with P6 |
| D31 | Entity concepts | Tables may `maps_to` an entity concept; required for cross-dataset units | §7.4 needed it and the table descriptor lacked it |
| D32 | Caveat severities | Every caveat, core or pack, declares a severity | "Show warn and above" must be unambiguous |
