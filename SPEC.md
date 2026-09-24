# aibi — Specification

**Status:** Draft v0.4 · 2026-09-24
**Scope:** product goals, principles, data model, query semantics, result contract, analysis
registry, domain packs, MCP surface, security, architecture and milestones. The text is
normative where it says MUST, MUST NOT or SHOULD (RFC 2119); everything else is rationale.
**How to read it:** each rule is defined in exactly one section and referred to elsewhere.
From milestone M2, the reference evaluator (§13.3) is the executable definition of §6 and
§7.6; a disagreement between it and this text is a spec bug, fixed in both. Decisions and
their reasons are in Appendix A; the change history is in Appendix B.

---

## 1. Purpose

aibi is an **AI-native system for exploring cohorts in related tables**. A cohort is any set
of entities (patients, samples, customers, sites, devices) selected by criteria over their own
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
- Multi-tenant hosting, user accounts, fine-grained access control, and restricted or
  identifiable data. v1 assumes de-identified data on a trusted lab server (also runnable
  locally). The disclosure settings of §8.4 reduce risk; they are not a privacy guarantee.
- Federation across sites. The result contract (§8) leaves room for it.
- Timeline queries and JSON-LD export: designed for, scheduled after v1 (§15). The v1 schema
  already carries per-unit observation windows (§5.3), so timeline support is an addition,
  not a redesign.

---

## 2. Prior work and what aibi takes from it

| Source | What aibi takes | What it leaves |
|---|---|---|
| **biai** (`jjgao/biai`) | The generic starting point: any multi-table dataset, relationships between tables, counting by a related (parent) table, filters that propagate along relationships, list-valued columns, spreadsheet and multi-sheet import, key detection. Its e2e specs and user guide are the behavioural checklist for the UI. | The code, ClickHouse as an app-state store, string-built SQL, per-chart round trips, silent path-finding between tables. |
| **cbio-lab DSL v2.2** (the system behind the `oncoprint` MCP server) | The document shape: named cohorts built from `all` / `any` / `not` clauses, plus views. The rules *clients never send SQL*, *refuse rather than approximate*, *denominators count only what was assessed*, *not-assessed is a first-class state*, deterministic plain-language readbacks, caveats the agent must surface, `observed` windows on absence queries, left truncation for re-anchored survival, and `params` templates. These are generalised in the core; the oncology-specific parts (OQL, profiles, panels) become the oncology pack. | Its `not` semantics (base EXCEPT leaf) are replaced by three-valued logic (§6.3). Documents are pinned to releases and hashed into derivation ids (§7.6). |
| **cBioPortal study format and validator** | The oncology pack's import format and the validator's role as a gate. | Nothing in the core depends on it. |

Documents SHOULD stay close enough to cbio-lab's shape that a cbio-lab document can be
translated mechanically into core clauses plus oncology-pack leaves. Differences in meaning
MUST be listed in `docs/cbio-lab-differences.md` once that file exists. Whether aibi and
cbio-lab should converge is open question Q1 (§16).

---

## 3. Principles

These principles decide the design. Each one names how it is enforced; a change that weakens
the enforcement is a change to this spec.

### 3.1 Data principles

**P1 — Every reported value carries its derivation.**
Every count, proportion, statistic and curve, including cohort counts and catalogue
statistics, is returned with a reference to how it was produced: a derivation id for query
results (§7.6) and a release-scoped reference for catalogue statistics (§8.1). Query results
also carry the canonical document, the registered analysis and its version, and the releases
used; every proportion carries its numerator, its denominator and a statement of what the
denominator counts.
*Enforced by:* the result schema (§8). A value without a reference cannot be serialised.

**P2 — Missing is not negative.**
The data model distinguishes a value, a confirmed absence, not assessed, not applicable and
unknown (§6.2). No related rows means "none" only where coverage declares the table complete
for that row (§6.5). Query logic is three-valued (§6.3). No statistic silently drops, imputes
or reclassifies a missing observation; every exclusion is counted by reason.
*Enforced by:* the observation-state model, the reference evaluator and property tests (§13).

**P3 — Comparability is asserted, not inferred.**
Values from different datasets are compared only through concepts to which both datasets have
an asserted mapping (§5.7), whether the comparison happens inside one cohort or between the
cohorts of one view (§7.5). A shared column or table name counts for nothing. Units, value
encodings and time origins are checked.
*Enforced by:* the compiler refuses unmapped cross-dataset references unless the document opts
in explicitly, and then every affected result carries a caveat.

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
Data and descriptors are served from content-addressed, read-only releases, and derivations
refer to releases by content hash (§7.6). Curation changes are batched per curation session
into one new release (§12.3). A release can be withdrawn, which removes its data but keeps
its identity (§12.2). *Enforced by:* the storage layer; a document run against the same
releases returns the same result digest.

**P7 — One documentation surface for data, computations and models.**
Descriptors (data), registry entries (computations) and model cards (the models that propose
descriptors or draft documents) share one envelope (§5.1) and one lookup path. Results record
which model or person drafted the document they answer (§8.1).

**P8 — The core is domain-agnostic; domains are packs.**
The core knows about tables, keys, relationships, columns, observation states, coverage,
endpoints and concepts, and nothing else. It has no notion of patients, samples, genes or
assays, and its caveat codes, reasons and readback templates contain no domain terms. Domain
knowledge (vocabularies, descriptor extensions, importers, query shorthands, analyses,
readback templates, caveat codes) is added by packs through public extension points (§10),
and every pack leaf compiles to core clauses.
*Enforced by:* an import-boundary test: `aibi.core` MUST NOT import from `aibi.packs`, and the
core test suite runs with no packs installed. At least one non-biomedical fixture dataset is in
the core test suite. The oncology pack's exit criterion (M4) is that it ships without any
change to the core.

### 3.2 AI-interaction principles

**A1 — The model writes documents, never SQL and never numbers.**
Every number shown to a user comes from an engine result with a derivation id, or from a
catalogue statistic with its reference. The UI renders numbers from results, not from model
prose. The in-app assistant may only quote numbers from a result it cites; it does no
arithmetic of its own. Comparisons people naturally ask for ("twice as frequent", "four months
shorter") are therefore computed by the analyses, with confidence intervals (§9.5). The evals
check both rules.

**A2 — One document, many editors.**
The UI, the chat assistant, MCP clients and shareable URLs all read and write the same
Analysis document. Its readback is generated deterministically from the canonical document,
never by a model, so a person can confirm that the document asks what they meant.

**A3 — Refuse rather than approximate.**
An unsupported operator, an unknown column, an ambiguous join path, or an analysis whose
requirements are not met fails loudly, names the problem, and lists what *is* available.

**A4 — No private tools.**
The in-app assistant uses exactly the public MCP tools. Anything it can do, an external agent
can do and a person can inspect.

**A5 — Proposals are visible until confirmed.**
Anything a model proposes (a key, a relationship, a table role, a descriptor field, a concept
mapping) is stored with status `proposed` and the proposing model's card. It is never silently
promoted to `asserted`, and results that depend on it carry a caveat until a person confirms
it.

**A6 — Text from data is never an instruction.**
Labels, definitions, cell values, file names, document notes and anything else that arrives
with data or documents is content, not instructions, for the in-app assistant, external
agents and the UI alike. It is rendered as plain text and marked as data in tool outputs
(§14).

---

## 4. Glossary

| Term | Meaning |
|---|---|
| **Dataset** | A collection of related tables from one source, e.g. a clinical trial export, a CRM extract, or a cBioPortal study. |
| **Release** | An immutable snapshot of a dataset's data **and** descriptors, identified by the hash of its manifest and labelled `<dataset>@<n>` once published or `<dataset>@draft` while a curation session edits it (§12.3). |
| **Table** | A set of rows with a declared **grain** (what one row is) and a **role** (§5.3). |
| **Entity table** | A table with a primary key. Any entity table can be a unit. |
| **Unit** | The table whose rows a cohort counts. |
| **Relationship** | A declared many-to-one or one-to-one link from a child table's foreign key to a parent table's key. Relationships form the **table graph**. |
| **Path** | A sequence of up and down steps along relationships from the unit to a referenced table (§6.1). |
| **Coverage** | A declaration of which parent rows (and, optionally, which scope values) a child table is complete for (§5.6). |
| **Reason** | Why a value is UNKNOWN: `NOT_ASSESSED`, `NO_INFORMATION`, `NO_PARENT`, `OUT_OF_SCOPE` or `NO_ROWS` (§6.3). |
| **Endpoint** | A declared (time, status) pair on an entity table, usable for time-to-event analysis. |
| **Concept** | A dataset-independent meaning (e.g. *age at diagnosis in years*, *person*) that columns, tables and endpoints can be mapped to. |
| **Descriptor** | The structured metadata record for any of the above, or for an analysis or model. |
| **Pack** | A domain extension: concepts, descriptor extensions, importers, leaf kinds, analyses, templates (§10). |
| **Analysis document** | The JSON object declaring cohorts and views (§7). |
| **Derivation** | The canonical, hashed description of how a result was produced (§7.6), kept permanently in the derivation log (§12.2). |
| **Caveat** | A structured, coded statement about a result's fitness for use (§8.3). |

---

## 5. Descriptors

### 5.1 Envelope and curation status

Every descriptor, whatever it describes, has:

```jsonc
{
  "kind": "dataset | table | column | relationship | coverage | endpoint | concept | analysis | model",
  "id": "string, stable within its scope",
  "version": "string",
  "label": "Human-readable name",
  "definition": "One-paragraph definition in plain text",
  "provenance": { "source": "...", "pipeline": {"name": "...", "version": "..."}, "citation": ["doi:…"] },
  "fields": { /* kind-specific, §5.2–5.8 */ },
  "extensions": { "<pack id>": { /* validated against the pack's JSON Schema */ } },
  "curation": { "<field path>": CurationStatus }
}
```

`curation` holds the status of every semantically meaningful field, including table roles,
coverage and concept mappings; no other place in a descriptor records a status.

```jsonc
CurationStatus = {
  "status": "asserted | proposed | imported | imported_default | undeclared",
  "by": "person:<id> | model:<model-card-id> | importer:<name>@<version>",
  "at": "RFC 3339 timestamp",
  "evidence": "optional plain text or reference"
}
```

- `asserted`: confirmed by a person. `imported`: taken from the source (a header row, a
  database comment, a declared constraint). `imported_default`: filled in by convention (e.g.
  mapping `NA` to UNKNOWN). `proposed`: suggested by a model or tool. `undeclared`: nobody has
  said.
- The engine treats undeclared semantics conservatively: an undeclared missing code is
  UNKNOWN, and undeclared coverage never makes an absence FALSE (§6.5).
- Any field that affects a result and is `imported_default`, `proposed` or `undeclared` raises
  `UNCONFIRMED_SEMANTICS` (§8.3), except proposed coverage, which raises `COVERAGE_PROPOSED`
  instead.
- `extensions` is how packs add domain fields (e.g. the oncology pack's `reference_genome` on a
  dataset). The core stores and validates them but never interprets them.

### 5.2 Dataset descriptor

`name`, `description`, `domain_tags`, `citation` / `references`, `source` (file set, database
and schema, or repository and commit; never credentials, §14), `license`, `data_use` (GA4GH
DUO codes where relevant), `disclosure` (`min_cell_count`, `allow_row_ids`; §8.4), and computed
fields (`n_rows` per table, a table-graph summary) that are filled in when the release is built,
never by hand.

### 5.3 Table descriptor

| Field | Meaning |
|---|---|
| `grain` | Plain-text statement of what one row is (e.g. *one adverse event report*) |
| `role` | `entity` (rows are things: patients, samples, visits), `link` (a many-to-many linking table: enrolments), `measurement` (observations about a parent: mutation calls, lab results), `event` (time-stamped occurrences: treatments, adverse events) or `coverage` (a coverage table, §5.6). The importer proposes a role; packs declare roles for the tables they create. Coverage proposals follow it (§5.6) |
| `primary_key` | Column list, or `none`; a table without a key can be filtered and aggregated but cannot be a unit |
| `maps_to` | Optional table concept the rows are instances of (e.g. `core:person`); required for a table referenced across datasets (§7.5) |
| `time_origin` | For tables with time columns: what time zero means (e.g. *date of initial diagnosis*) |
| `observation_window` | For entity tables: the columns (or a declared rule) giving, per row, the period over which that row's related records are complete, e.g. enrolment to last contact. Optional in v1 and unused by v1 analyses; timeline queries (M7) rely on it |
| `source` | Sheet, file or database table it came from, and any header or skip-row handling applied |

### 5.4 Column descriptor

| Field | Meaning |
|---|---|
| `datatype` | `number`, `integer`, `string`, `boolean`, `category`, `list<category>`, `date`, `datetime`, `time_offset` |
| `units` | UCUM code for numbers and offsets (`a`, `mo`, `d`, `mg/dL`, `[USD]`); required for any number used in a cross-dataset comparison |
| `permissible_values` | For categories: `[{value, label, concepts: [OntologyRef]}]` |
| `missing_codes` | Map from a raw token to an observation state, e.g. `{"": "UNKNOWN", "NA": "UNKNOWN", "N/A": "NOT_APPLICABLE", "Not done": "NOT_ASSESSED"}` |
| `concepts` | `[OntologyRef]` describing what the column measures |
| `maps_to` | Optional concept mapping (§5.7) |
| `list_syntax` | For `list<…>`: how lists are encoded in the source (JSON array, Python literal, delimiter) |
| `completeness` | Declared (`complete`, `partial` or `unknown`) plus computed counts per observation state |
| `source` | Original column name and any header metadata imported with it |

`OntologyRef = {system, code, label, relation: "exact | broader | narrower | related"}`. The
core accepts any `system` string; packs register the systems they validate (e.g. NCIt, LOINC,
OncoTree, HGNC). How observation states are stored is in §12.2.

### 5.5 Relationship descriptor

`{child_table, child_columns, parent_table, parent_columns, cardinality: "many-to-one" |
"one-to-one", role?}`. `role` names the relationship when two tables are linked more than once
(e.g. `orders.billing_customer` and `orders.shipping_customer`). A null foreign key is allowed;
§6.1 says how it evaluates.

### 5.6 Coverage descriptor

A coverage declaration says for which parents a child table is complete, and over what scope:

```jsonc
{
  "child_table": "mutations",
  "relationship": "mutations.sample_id → samples.sample_id",
  "parents": "all | { table: <coverage table>, columns: [...] } | undeclared",
  "scope_columns": ["hugo_symbol"],          // optional: completeness is per (parent, scope value)
  "record_filter": { "variant_class": ["missense", "nonsense", "frameshift", "splice"] },
  "parent_scope": { /* core clause over the parent table, e.g. sample_type = tumour */ }
}
```

- `parents: "all"`: every in-scope parent was assessed. `parents: {table, columns}`: a coverage
  table lists the assessed parents or, with `scope_columns`, the assessed (parent, scope value)
  pairs. This is how a gene panel is expressed (the oncology pack generates it), and equally how
  *these sites reported adverse events for these visit windows* is expressed. `undeclared`:
  nobody has said.
- `scope_columns` are matched by equality in v1 (a gene, a visit window). Range-based scope such
  as genomic intervals is out of scope for v1.
- `record_filter` states what kinds of rows the table holds, as a conjunction of allowed-value
  lists on categorical columns. Queries are checked against it (§6.5, step 1).
- `parent_scope` names which parents the table is about (e.g. tumour samples, not blood
  normals). It is evaluated as in §6.5, step 2.
- Coverage tables have role `coverage` and are not part of the table graph (§6.1).
- **Proposals.** The importer proposes `parents: "all"` for child tables whose role is `entity`
  or `link`. Tables whose role is `measurement` or `event` stay `undeclared`, because that is
  where partial coverage hides; the curation assistant may propose coverage for them with
  evidence (e.g. *a panel column was found*), and a person decides. Packs declare coverage
  explicitly. Results that rely on proposed coverage carry `COVERAGE_PROPOSED`, and the
  curation queue ranks these confirmations first.

### 5.7 Concepts and mapping

A **concept** is a dataset-independent descriptor (`kind: concept`). Value concepts have units
or permissible values (`core:age_years`, `core:sex`); table concepts name what rows are
(`core:person`); endpoint concepts name time-to-event outcomes (`onco:overall_survival`).
Concepts are namespaced by who defines them: `core:` for a handful of universal ones, and
`<pack>:` for the rest. Concepts are versioned; canonical forms record the versions they use
(§7.6).

A column, table or endpoint maps to a concept through `maps_to`:

```jsonc
ConceptMapping = {
  "concept": "core:age_years",
  "transform": { "unit_from": "d", "unit_to": "a" } | { "value_map": {"M": "male", "F": "female"} } | null
}
```

- Mappings are **exact**: a column maps to a concept iff it means that concept. A looser
  comparison needs a looser concept (e.g. `core:age_years_approx`), not a weaker mapping.
- Transforms are limited to unit conversions and value maps. Anything else (e.g. age from a
  birth date and a diagnosis date) is a **derived column**, declared and versioned in the
  release like any other column.
- A mapping counts only when its curation status is `asserted`.

### 5.8 Endpoint descriptor

`table` (an entity table), `time_column`, `status_column`, `time_units`, `event_coding` (which
status values are events and which are censored), `time_origin` (defaults to the table's), and
optionally `maps_to` an endpoint concept. For analyses, a row whose time or status is missing,
whose status is not in `event_coding`, or whose time is negative is UNKNOWN (`NO_INFORMATION`);
the importer lists such rows in the curation queue. The core detects nothing by name; packs and
the curation assistant propose endpoints.

---

## 6. Query semantics

### 6.1 The table graph and paths

Tables other than coverage tables are the nodes of the table graph; relationships are its
edges, from child to parent.

- An **up step** (child to parent) is a lookup: each child row has at most one parent. A null
  foreign key makes the looked-up value UNKNOWN with reason `NO_PARENT`.
- A **down step** (parent to children) is an existence question (§6.5).
- A **path** from the unit to a referenced table is a sequence of up and down steps that visits
  no table twice. If exactly one path exists, it is used. If none exists, the reference is
  refused. If several exist, the document is refused, listing them, unless `via` names one (a
  list of relationships). There is no shortest-path or other silent choice.
- A criterion that returns to a table already on the path (the other samples of the same
  patient) is written as an explicit nested `exists`.
- A reference to a column below the unit is shorthand for nested `exists` questions, one per
  down step, and MUST evaluate identically to them.
- One-to-one relationships are steps like any other; a down step across one has at most one
  child.
- Readbacks state every step (*samples whose patient has a treatment*, *participants enrolled in
  a phase 3 trial*).

### 6.2 Observation states

Every (unit, variable) pair evaluated by the engine has exactly one state:

| State | Meaning | Examples |
|---|---|---|
| `PRESENT` | A value or a matching related row exists | `age = 61`; an adverse-event row of grade 3 |
| `ABSENT` | It was assessed, and there is none | A closed parent (§6.5) with no matching child row |
| `NOT_ASSESSED` | It is known that it was not assessed | A parent the coverage does not list; a `Not done` code |
| `NOT_APPLICABLE` | The question does not apply | `N/A` mapped to NOT_APPLICABLE |
| `UNKNOWN` | There is no information either way | Empty cell, undeclared missing code, undeclared coverage |

A cell takes its state from the column's `missing_codes`; a null or empty cell with no declared
code is UNKNOWN. For ordinary columns, a negative answer (`"No"`) is a PRESENT value. ABSENT
arises only from existence questions.

### 6.3 Truth values, reasons and combinators

Every criterion evaluates, per unit, to TRUE, FALSE or UNKNOWN. UNKNOWN carries a non-empty set
of reasons:

| Reason | Meaning |
|---|---|
| `NOT_ASSESSED` | Known not to have been assessed: outside coverage, or a code such as `Not done` |
| `NO_INFORMATION` | No information either way: an empty cell, an undeclared code, undeclared coverage |
| `NO_PARENT` | An up step met a null foreign key |
| `OUT_OF_SCOPE` | The question does not apply to this row: it is outside a table's `parent_scope` |
| `NO_ROWS` | Nothing to evaluate: an intermediate step with no considered rows, or `every` over no rows |

- `all` is FALSE if any operand is FALSE, otherwise UNKNOWN if any is UNKNOWN, otherwise TRUE.
  `any` is TRUE if any operand is TRUE, otherwise UNKNOWN if any is UNKNOWN, otherwise FALSE.
  `not` swaps TRUE and FALSE and leaves UNKNOWN. When a result is UNKNOWN, its reasons are the
  union of the reasons of its UNKNOWN operands. (This is Kleene's strong three-valued logic.)
- `known(C)` is TRUE iff `C` is not UNKNOWN, otherwise FALSE; `unknown(C)` is TRUE iff `C` is
  UNKNOWN, otherwise FALSE. They are the only way to include unknowns deliberately.
- A unit is **in** a cohort iff the cohort's predicate is TRUE.

### 6.4 Value predicates

A value predicate compares one column's value per row with constants. Its forms are `values`
(membership in a set), `range` (`gt`, `gte`, `lt`, `lte`) and `op` with `value` (`=`, `!=`,
`>`, `>=`, `<`, `<=`); canonicalisation reduces them to `values`, `not` over `values`, and
`range` (§7.6).

| Cell state | Result |
|---|---|
| PRESENT | TRUE or FALSE, by the value |
| NOT_APPLICABLE | FALSE |
| NOT_ASSESSED | UNKNOWN (`NOT_ASSESSED`) |
| UNKNOWN | UNKNOWN (`NO_INFORMATION`) |

- Because NOT_APPLICABLE is FALSE, `not (x in V)` is TRUE for a NOT_APPLICABLE cell, while the
  complementary range is FALSE: for a male patient, *not premenopausal* is TRUE. Readbacks show
  every `not`.
- **Units.** A numeric predicate carries `units`, defaulting to the column's units and written
  into the canonical form. Constants in other units are converted when UCUM allows, and refused
  otherwise. Readbacks always state the units. A numeric column without declared units raises
  `UNCONFIRMED_SEMANTICS`.
- **Categories.** If the column's permissible values are declared, a constant outside them is
  refused, and the error lists them.
- **Lists.** Each item of a `list<…>` cell is evaluated. With `match: "any"` (default) the
  result is TRUE if any item is TRUE, otherwise UNKNOWN if any item is UNKNOWN, otherwise FALSE;
  an empty list is FALSE. With `match: "all"` it is FALSE if any item is FALSE, otherwise
  UNKNOWN if any item is UNKNOWN or the list is empty (`NO_ROWS`), otherwise TRUE.

### 6.5 Existence and coverage

An **existence question** asks, for a row `r` of table `R`, about `r`'s rows in a child table
`T` (its *children*). It has an inner predicate `W` over the children (the `where` clauses,
ANDed; no clauses means TRUE), a quantifier (`some` with `min_count` *k* ≥ 1, default 1, or
`every`) and a lift rule (`strict`, the default, or `assessed`). A step is **intermediate** if
`W` asks about a table reached by a further down step from `T`; otherwise it is **final**.

For each `r`:

1. **Record filter.** If `T`'s coverage has a `record_filter`, each filtered column is either
   not constrained by `W`, in which case `W` means *any row the table holds* and the readback
   states the filter, or constrained only by conjuncts of `W` (not inside `any` or `not`) whose
   admitted values provably lie within the column's allowed values. Otherwise the document is
   refused, because an absence of rows outside the filter means nothing.
2. **Parent scope.** If `T`'s coverage has a `parent_scope` and it is FALSE for `r`, the answer
   is UNKNOWN (`OUT_OF_SCOPE`). If it is UNKNOWN for `r`, `r` counts as in scope.
3. **Children.** Evaluate `W` for each child. At an intermediate step, drop the children whose
   value is UNKNOWN with every reason in the lift rule's drop set: `strict` drops
   `OUT_OF_SCOPE`; `assessed` drops `OUT_OF_SCOPE` and `NOT_ASSESSED`. Final steps drop
   nothing. Let *K* be the remaining children, and *t*, *f*, *u* the numbers of them that are
   TRUE, FALSE and UNKNOWN.
4. **Closedness.** `r` is *closed* when it is known that `r` has no further children that
   could match:
   - coverage `all`: closed;
   - a coverage table without scope columns: closed iff it lists `r`, otherwise not closed with
     reason `NOT_ASSESSED`;
   - a coverage table with scope columns: if conjuncts of `W` restrict every scope column to a
     finite set of values, closed iff the table lists `r` for every combination of them,
     otherwise `NOT_ASSESSED`; if `W` leaves the scope open, closed iff the table lists `r` for
     at least one scope value, and a FALSE answer then covers only the listed values
     (`SCOPE_PARTIAL`);
   - coverage `undeclared`: not closed, reason `NO_INFORMATION`;
   - under `lift: "assessed"`, intermediate steps are closed.
5. **Answer.**
   - `some` with `min_count` *k*: TRUE if *t* ≥ *k*; otherwise UNKNOWN (`NO_ROWS`) if the step
     is intermediate and *K* is empty; otherwise UNKNOWN (the reasons of the UNKNOWN children)
     if *t* + *u* ≥ *k*; otherwise FALSE if `r` is closed; otherwise UNKNOWN (the closedness
     reason).
   - `every`: FALSE if *f* ≥ 1; otherwise UNKNOWN (`NO_ROWS`) if *K* is empty; otherwise
     UNKNOWN (the reasons of the UNKNOWN children) if *u* ≥ 1; otherwise TRUE if `r` is
     closed; otherwise UNKNOWN (the closedness reason).

A matching child is evidence even where the coverage does not list `r`: step 5 makes the
answer TRUE regardless (the importer flags such rows, §13.2).

**`covered`** (§7.2) turns closedness into a predicate. At the final step it is TRUE if `r` is
closed, FALSE if the coverage is declared and `r` is not closed, UNKNOWN (`NO_INFORMATION`) if
the coverage is undeclared, and UNKNOWN (`OUT_OF_SCOPE`) outside the parent scope. Across
intermediate steps it is lifted with `every` under `strict` and `some` under `assessed`, using
steps 3–5.

Consequences, which the reference evaluator's scenario tests encode:

- `not exists mutations where gene = TP53`, for a sample, is TRUE only if the sample is assessed
  for TP53 and has no TP53 row.
- A participant whose only adverse event has a missing grade is UNKNOWN both for *some adverse
  event of grade ≥ 3* and for its negation.
- A patient with one assessed wild-type tumour sample and one unassessed tumour sample is
  UNKNOWN for *a TP53 mutation in some sample* under `strict` and FALSE under `assessed`. A
  blood normal outside the mutations table's parent scope changes neither answer. A patient with
  no samples, or with only a blood normal, is UNKNOWN (`NO_ROWS`) under both.
- A participant with no enrolments is FALSE for *enrolled in a phase 3 trial* when enrolments
  are closed; the step is final, because the trial's phase is a lookup above enrolments.

### 6.6 Accounting

Every cohort result reports, over the unit table:

- `n_true`, `n_false` and `n_unknown`;
- `unknown_by_reason`: the UNKNOWN units per reason (a unit counts under each of its reasons);
- `unknown_by_leaf`: for each leaf of the document as written (a pack leaf counts as one), the
  units whose cohort result is UNKNOWN and for which that leaf is UNKNOWN (counts can overlap);
- `lift_differs`: the units whose result would differ under the other lift rule; when it is
  above zero, the result carries `LIFT_DIFFERS`.

Whenever `n_unknown` is above zero, the UI and the assistant MUST show it next to the cohort
size, with its largest reason (e.g. *120 patients; 40 could not be evaluated, mostly not assessed
for copy number*).

---

## 7. Analysis document

### 7.1 Shape

```jsonc
{
  "aibi": "1",                                   // document format version
  "packs": { "onco": "^1.2" },                   // compatible versions; exact ones are recorded (§7.6)
  "params": { "min_grade": 3 },                  // optional; exact "$name" substitution, as in cbio-lab
  "dataset": "trial_xyz",                        // default dataset; may be pinned: "trial_xyz@3" or "trial_xyz@sha256:…"
  "unit": "participants",                        // an entity table, or an entity concept such as "core:person" (§7.5)
  "cohorts": {
    "<name>": {
      "all": [ Clause ],                         // [] = every row of the unit table
      "dataset": "<id>",                         // optional override, or:
      "datasets": ["<id>", …],                   // a cross-dataset cohort (§7.5)
      "unmapped": "allow",                       // optional (§7.5)
      "notes": "plain text"
    }
  },
  "views": [ View ],                             // §7.4
  "notes": "plain text",
  "drafted_by": "model:<card id> | person:<id>"  // provenance; never part of a hash
}
```

`Clause := Leaf | {"all": [Clause]} | {"any": [Clause]} | {"not": Clause} | {"known": Clause} | {"unknown": Clause}`

- **Caps**, applied to the canonical form (after pack expansion and inlined cohort references,
  with nested `all` inside `all` and `any` inside `any` flattened): depth 4, 32 leaves per
  cohort, 6 cohorts, 8 views per document. They keep readbacks readable and queries bounded.
- **Params.** A value that is exactly `"$name"` is replaced by that parameter, whatever its
  type; there is no interpolation inside longer strings. An unknown name is an error naming its
  path; declared but unused parameters are reported; the parameters used are echoed in results.
- **Notes** are plain text, never compiled and never interpreted (A6).
- **Translation.** A translator (M4) converts cbio-lab documents into aibi documents and flags
  every `not` whose meaning changes under three-valued logic.

### 7.2 Core leaf kinds

| Kind | Shape | Meaning |
|---|---|---|
| `value` | `{kind: "value", column: "<table>.<column>" \| "<concept>", values? \| range? \| op? + value?, units?, match?, quantifier?, lift?, via?}` | A value predicate (§6.4) on a column of the unit's table or any table reachable from it (§6.1). Down steps use `quantifier` (`some` or `every`, default `some`; a list gives one per step) and `lift` (§6.5) |
| `exists` | `{kind: "exists", table, where?: [Clause], quantifier?, min_count?, lift?, via?}` | An existence question (§6.5). `where` clauses are ANDed and evaluated per child row; `min_count` applies with `some` only |
| `covered` | `{kind: "covered", table, scope?: {<column>: [values]}, lift?, via?}` | Coverage as a predicate (§6.5) |
| `ids` | `{kind: "ids", ids: ["<dataset>:<key>", …]}` | An explicit list of unit keys. Refused on datasets with `allow_row_ids: false` (§8.4) |
| `cohort` | `{kind: "cohort", cohort: "<name>"}` | Another cohort of the same document, with the same unit and dataset(s); cycles are refused. `{"all": [{"kind": "cohort", "cohort": "base"}, {"not": X}]}` is the correct *rest of the base* under three-valued logic, which is why references exist |

### 7.3 Pack leaf kinds

Packs register leaf kinds namespaced by pack id (§10.1), e.g. `{"kind": "onco.genomic", "q":
"EGFR: AMP; PTEN: HOMDEL"}`.

- A pack leaf MUST compile to core clauses. The canonical form, and therefore every id,
  contains the expansion, not the pack leaf: two ways of writing the same criterion get the
  same id.
- The document as written, including pack leaves, is kept in the result envelope outside every
  hash. Readbacks use the pack's templates (§7.7). `validate_document` and `explain` return both
  forms.
- The exact pack versions used are recorded in the envelope. A pack change that alters an
  expansion changes the ids; one that does not, changes nothing.

### 7.4 Views

```jsonc
{ "analysis": "<registry id>", "cohorts": ["<name>", …], "reference": "<name>",
  "overlap": "allow", "unmapped": "allow", "params": { … }, "note": "plain text" }
```

- `cohorts` is an ordered array. It is required when the analysis declares `uses_reference`
  (effect sizes); otherwise it defaults to every cohort, ordered by cohort id. JSON object key
  order is never used for anything.
- `reference` names the reference group for effect sizes and defaults to the first entry of
  `cohorts`. The canonical form records it as a position.
- For analyses that declare `assumes_independent_groups` (§9.1), cohorts that share units are
  refused, with the overlap count, unless the view says `overlap: "allow"`; the result then
  carries `COHORTS_OVERLAP`. Other analyses accept overlapping cohorts.
- Column, table and endpoint references in `params` are resolved like leaves: paths (§6.1),
  units (§6.4) and the cross-dataset rules (§7.5).
- A view whose cohorts come from more than one dataset is a cross-dataset query (§7.5);
  `unmapped: "allow"` on the view is its opt-in.

### 7.5 Cross-dataset cohorts and views

A cross-dataset query is a cohort with `datasets`, or a view whose cohorts come from different
datasets. In one:

- The unit MUST be an entity concept, resolved in each dataset to the one table that maps to it;
  the document is refused if a dataset has no such table or several.
- `value` columns, `exists` and `covered` tables, analysis parameters and endpoints MUST be
  referenced by concept, and every dataset MUST have an asserted mapping for each; otherwise the
  document is refused with a list of what is missing.
- Pack leaves are expanded separately in each dataset.
- Endpoints MUST map to the same endpoint concept. If their time origins are undeclared or
  differ, results carry `TIME_ORIGIN_MISMATCH` (block).
- The analysis MUST declare a cross-dataset method in the registry (e.g. a test stratified by
  dataset, with a pooled estimate reported alongside); otherwise the view is refused. Pooled
  values are labelled as pooled and carry `POOLED_ACROSS_DATASETS`.
- `unmapped: "allow"`, on the cohort or view, replaces the refusal: references are then matched
  by name, and every affected result carries `UNMAPPED_COMPARISON`.
- The same individual appearing in two datasets cannot be detected; units from different
  datasets are always distinct.

### 7.6 Canonical form, ids and digest

Before compilation, the server canonicalises the document:

1. Substitute `params`.
2. Resolve every dataset reference to its release's manifest hash (the `@n` or `@draft` label is
   recorded beside it and never hashed), and every pack to its exact installed version (refused
   if missing or incompatible).
3. Expand pack leaves (§7.3).
4. Resolve names: columns, tables and concepts to descriptor ids (concepts with their versions);
   paths to explicit `via`; `cohort` leaves to the referenced cohort's canonical form, inlined;
   each view's `cohorts` to the ordered list of those cohorts' canonical forms; `reference` to a
   position.
5. Normalise syntax: `=` to `values` with one value; `!=` to `not` over `values`; `op`
   inequalities to `range`; nested `all` inside `all` and `any` inside `any` flattened; every
   default written explicitly (`units`, `quantifier`, `min_count`, `lift`, `match`, analysis
   parameter defaults).
6. Sort order-insensitive collections by their canonical serialisation: the members of `values`,
   and the clauses inside `all` and `any`. The order of each view's cohorts is kept.
7. Drop what does not affect results: names, notes, `drafted_by`, and the order of the top-level
   `cohorts` map.

After step 7 the canonical form contains no user-chosen name (§13.4). It is serialised with the
JSON Canonicalization Scheme (RFC 8785).

- A **cohort id** is `drv:` + SHA-256 of {canonical cohort, unit (descriptor id or concept),
  release manifest hashes}.
- A **result id** is `drv:` + SHA-256 of {analysis id and version, canonical parameters
  (including the reference position and `overlap`), the cohort ids in view order, and the
  effective disclosure settings (§8.4)}.
- A **result digest** is the SHA-256 of the canonical serialisation of the result's `cohorts`,
  `population`, `values` and `caveats` (code, severity and affected paths; `DRAFT_RELEASE`
  excluded, because it describes a release's status rather than the computation), with
  floating-point numbers rounded to 12 significant digits after deterministic computation
  (§9.3). Readbacks, labels, charts and caveat messages are outside it.

**Invariant:** the same result id MUST produce the same digest. A change that alters a digest
for an unchanged result id is a bug unless the analysis (or pack) version was bumped, and that
includes dependency upgrades: a new version of a statistics library or of DuckDB that changes
any golden digest requires bumping the affected analyses' versions. Golden tests check this
(§13.4).

Every id issued is written to the derivation log (§12.2), which `explain` reads. Ids are
designed to be citable, but v1 promises no availability; the ids of a withdrawn release resolve
to *withdrawn* (§12.2).

### 7.7 Readback

The server renders a deterministic, plain-language readback of every canonical cohort and view
from templates. A readback states every path step, quantifier and lift rule, the units of every
numeric constant, every record filter and parent scope, every `not`, and what is excluded (e.g.
*"Patients in GBM (TCGA PanCan) with a TP53 mutation in some tumour sample; patients whose tumour
samples were not all assessed for TP53 are not counted"*). Packs supply templates for their
leaves. Readbacks are returned with every result and cohort count and shown next to every
figure.

---

## 8. Result contract

### 8.1 Envelope

```jsonc
{
  "derivation": {
    "id": "drv:…",
    "document": { /* canonical form */ },
    "analysis": { "id": "survival.km", "version": "1.0.0" },
    "releases": [ { "dataset": "trial_xyz", "label": "@3", "manifest": "sha256:…", "status": "published | draft" } ],
    "packs": { "onco": "1.2.0" },                // exact versions; informational
    "disclosure": { "min_cell_count": 5 },        // effective settings (§8.4)
    "engine": "aibi 0.4.0"
  },
  "source": { /* the document as written: names, pack leaves, notes, drafted_by */ },
  "digest": "sha256:…",
  "cohorts": [ { "position": 0, "id": "drv:…", "reference": true }, … ],   // view order
  "population": [ { "n_true": 0, "n_false": 0, "n_unknown": 0,
                    "unknown_by_reason": { … }, "unknown_by_leaf": { … }, "lift_differs": 0 } ],
  "values": { /* analysis-specific, keyed by position */ },
  "caveats": [ Caveat ],
  "readback": { "cohorts": [ "…" ], "view": "…" },   // by position; outside the digest
  "labels": [ "<cohort name>", … ],                  // by position; outside the digest
  "charts": [ /* Vega-Lite specifications generated from values; outside the digest */ ]
}
```

- The compiled SQL is not part of `run_analysis` responses; `explain` returns the SQL recorded
  when the result was computed (§12.2).
- **Cohort counts** (`count_cohort`, §11) return, for each cohort, its cohort id, its
  `population` entry, readback and caveats, and the releases used.
- **Catalogue statistics** (row counts, value distributions, observation-state counts) carry a
  release-scoped reference `stat:<manifest hash>/<descriptor id>/<field>`, which the assistant
  cites like a derivation id.

### 8.2 Proportions and effect sizes

Every proportion in `values` is an object, never a bare number:

```jsonc
{
  "estimate": 0.412,
  "numerator": 124,
  "denominator": 301,
  "denominator_definition": "participants in cohort 'over 60' for whom 'some adverse event of grade ≥ 3' is known",
  "excluded": { "NOT_ASSESSED": 17, "NO_INFORMATION": 3 },
  "ci": { "method": "wilson", "level": 0.95, "low": 0.357, "high": 0.469 }
}
```

Every effect size names its measure, its position and its reference position:

```jsonc
{ "measure": "hazard_ratio", "position": 1, "versus": 0, "estimate": 1.8,
  "ci": { "method": "wald", "level": 0.95, "low": 1.3, "high": 2.5 } }
```

An effect size that cannot be estimated (e.g. a difference in medians where one curve never
reaches 50%) has `"estimate": null` and a `not_estimable` reason, and the result carries
`NOT_ESTIMABLE`. Nothing is extrapolated.

### 8.3 Caveats

`Caveat = {code, severity: "info" | "warn" | "block", message, affects: [paths into values]}`.
Codes are a stable, documented enum; core codes are unprefixed and pack codes are namespaced
(`onco.DRIVER_ANNOTATION_PIN`). Every code, core or pack, declares its severity. The core set:

| Code | Severity | Raised when |
|---|---|---|
| `UNKNOWN_EXCLUDED` | warn | A cohort or denominator excluded units because they were UNKNOWN |
| `SCOPE_PARTIAL` | warn | A FALSE answer covers only the scope values a unit is listed for (§6.5) |
| `UNCONFIRMED_SEMANTICS` | warn | A descriptor field that affected the result is `imported_default`, `proposed` or `undeclared`; the message lists the fields |
| `COVERAGE_PROPOSED` | warn | A FALSE answer relied on proposed coverage; the message names the table |
| `UNMAPPED_COMPARISON` | warn | A cross-dataset query matched references by name under `unmapped: "allow"` |
| `TIME_ORIGIN_MISMATCH` | **block** | Time-based values were compared across datasets whose time origins are undeclared or differ |
| `COHORTS_OVERLAP` | warn | Cohorts in a view share units (allowed only with `overlap: "allow"`); the tests' independence assumption does not hold |
| `SMALL_N` | warn | A group fell below the analysis's declared minimum for a reliable estimate |
| `PH_VIOLATED` | warn | A proportional-hazards test failed; the hazard ratio is an average over time |
| `DRAFT_RELEASE` | warn | The result was computed against a curation session's draft release |
| `SUPPRESSED` | info | Values were suppressed by the disclosure settings (§8.4) |
| `NOT_ESTIMABLE` | info | An effect size could not be estimated (§8.2) |
| `LIFT_DIFFERS` | info | The other lift rule would change some units' results; the message gives the count |
| `POOLED_ACROSS_DATASETS` | info | A pooled value is reported next to a stratified one |

MCP tool descriptions MUST tell clients that `warn` and `block` caveats have to be shown to the
user. A `block` caveat means the result is returned for inspection but MUST NOT be presented as
an answer.

### 8.4 Disclosure settings

- Each dataset descriptor has `disclosure: {min_cell_count, allow_row_ids}`: `min_cell_count`
  defaults to off, `allow_row_ids` to true. Because they are part of the descriptor, they are
  part of the release. A deployment may set a floor for `min_cell_count`; the effective value
  (the larger of the two) is part of every result id (§7.6).
- Suppression applies to every output that contains counts or values that describe few units:
  results, cohort counts and catalogue statistics (value distributions, minima and maxima, rare
  categories). Suppressed values are marked as such, values from which a suppressed value could
  be recovered within the same output are suppressed too, and the output carries `SUPPRESSED`.
- `allow_row_ids: false` refuses the `ids` leaf and the `summary.members` analysis, and requires
  `min_cell_count` to be set.
- **Limits.** These settings reduce casual disclosure. They do not prevent inference across
  repeated queries (for example by differencing two counts), and they are not a privacy
  guarantee. Restricted data needs access control, which is outside v1 (§1.2).

---

## 9. Analysis registry

### 9.1 Entry

Each analysis is a descriptor (`kind: analysis`) plus an implementation:

```jsonc
{
  "id": "survival.km", "version": "1.0.0",
  "label": "Kaplan–Meier survival",
  "definition": "Kaplan–Meier estimate per cohort; k-sample log-rank test when there are ≥2 cohorts; median time with CI; difference in medians and unadjusted hazard ratio, with CIs, versus the reference cohort.",
  "requires": [
    { "role": "endpoint", "kind": "endpoint", "on": "unit" },
    { "role": "cohorts", "min": 1, "max": 6 }
  ],
  "params": { /* JSON Schema, generated from a Pydantic model */ },
  "returns": { /* JSON Schema of `values` */ },
  "method": { "library": "lifelines", "version": "…", "references": ["doi:…"] },
  "assumptions": ["independent censoring", "independent groups"],
  "uses_reference": true,
  "assumes_independent_groups": true,
  "cross_dataset": { "method": "log-rank stratified by dataset; pooled estimates reported alongside" },
  "randomness": "seeded",
  "caveats": ["UNKNOWN_EXCLUDED", "SMALL_N", "PH_VIOLATED", "NOT_ESTIMABLE", "TIME_ORIGIN_MISMATCH"],
  "min_group_n": 10
}
```

- Analyses are registered only by the core and by packs, through code review. Users cannot
  upload analyses in v1.
- Requirements are stated against core descriptor kinds (an endpoint, a numeric column, a child
  table with declared coverage); a pack analysis may also require pack extension fields.
- `"on": "unit"` means the endpoint must be on the unit table itself, not reached by a lookup:
  with samples as the unit and a patient-level endpoint, a patient with several samples would be
  counted several times.
- `cross_dataset: null` means the analysis cannot run across datasets (§7.5).

### 9.2 Several values per unit

A column is **multi-valued** for a unit when it is below the unit or list-valued.

- Analyses that declare `assumes_independent_groups` use one value per unit, so a view MUST give
  an `aggregate` for each multi-valued column it uses: `max`, `min`, `mean`, `count`, `any` or
  `all`. `any` and `all` are existence questions (§6.5). `count` is the number of rows when the
  unit is closed, otherwise UNKNOWN. `max`, `min` and `mean` are UNKNOWN when any contributing
  row is UNKNOWN or there are no rows (`NO_ROWS`).
- Descriptive analyses count units per category, so a unit with rows in several categories
  counts in each and the output says so, or, if the view asks for `count: "rows"`, count rows,
  labelled as rows.

### 9.3 Determinism

- Floating-point sums MUST be independent of order: correctly rounded (for example Shewchuk's
  algorithm, as in Python's `math.fsum`), or computed in a fixed order.
- Model fits use deterministic, single-threaded linear algebra.
- Resampling (bootstrap, permutation) is seeded from the result id.
- Rounding before hashing (§7.6) is a second line of defence, not the mechanism.
- A dependency upgrade that changes a golden digest requires bumping the affected analyses'
  versions (§7.6).

### 9.4 Applicability

`applicable_analyses(dataset, unit)` matches each entry's `requires` against the dataset's
descriptors and returns, for each analysis, `available`, `unavailable` (naming the missing
requirement) or `available_with_caveats` (e.g. an endpoint whose event coding is
`imported_default`). The dataset page and `describe_dataset` both show this.

### 9.5 Core analyses (v1)

| Id | Returns |
|---|---|
| `summary.distribution` | Per column, per cohort: category counts, or a numeric summary and histogram, with observation-state counts |
| `summary.members` | The unit keys of one cohort, sorted, paginated with `offset` and `limit`; refused when `allow_row_ids` is false (§8.4) |
| `compare.columns` | Per column: for categories, chi-squared (Fisher's exact test for 2×2); for numbers, Welch's t and Mann–Whitney for two cohorts, Welch's ANOVA and Kruskal–Wallis for more; differences in means and medians versus the reference, with CIs; Benjamini–Hochberg q across columns |
| `compare.existence` | Per existence predicate (e.g. *some grade ≥3 adverse event*, *a TP53 mutation*): the proportion per cohort over the units for which it is known (§8.2); risk difference and risk ratio versus the reference, with CIs; Fisher's exact test for two cohorts, chi-squared for more; BH q across predicates |
| `survival.km` | As in §9.1. The hazard ratio comes from an unadjusted Cox fit, tested for proportional hazards like any other (`PH_VIOLATED`); the CI of the difference in medians is a seeded bootstrap |
| `survival.cox` | Hazard ratios with CIs for cohort membership (versus the reference) and up to 8 covariates (columns or predicates, one value per unit; categorical covariates dummy-coded against their most common level, ties broken by the smallest canonical value); optional stratification; complete-case, with exclusions counted by reason; a proportional-hazards test on every fit |

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
| **Importers** | cBioPortal study directories → tables with roles, relationships, coverage tables and coverage declarations, descriptors |
| **Curation proposers** | Detect `OS_MONTHS` / `OS_STATUS` pairs and propose an endpoint |
| **Leaf kinds** (compile-to-core, a JSON Schema and readback templates) | `onco.genomic` (OQL subset: `MUT`, `MUT=<change>`, classes, `AMP`, `HOMDEL`, `FUSION`) |
| **Analyses** | `onco.alteration_frequency` (a specialisation of `compare.existence` over genes); later `onco.oncoprint` |
| **Caveat codes** (with severities) | `onco.COVERAGE_ASSUMED_WES` |

- Packs do not add MCP tools. A visual output such as an oncoprint is an analysis whose values
  are a render specification, so it stays inside the registry and the result contract.
- Packs live in this repository (`aibi/packs/`) until the extension points are stable, then move
  to separate packages so that other groups can publish their own.
- Packs MUST NOT patch the core. If a pack needs something the extension points do not offer,
  the extension point is added to the core in its own change, with a domain-neutral test.

### 10.2 How cBioPortal maps onto the core

This mapping is the working test of P8:

| cBioPortal | aibi core |
|---|---|
| Study | Dataset |
| Patient, sample | Entity tables; `samples.patient_id → patients.patient_id` |
| Clinical attributes (with the four header rows) | Columns, with `label`, `definition`, `datatype` imported |
| Mutation, CNA, structural-variant data | Measurement tables below `samples` (CNA as a long table of non-neutral calls with its own coverage) |
| Case lists, gene panel matrix, panel definitions | Coverage tables keyed by (sample, gene), generated by the importer |
| Normal samples | Outside the measurement tables' `parent_scope` |
| `NA`, `[Not Available]`, `[Not Applicable]`, … | `missing_codes` with `imported_default` status |
| OS / PFS / DFS / DSS | Endpoints on `patients` |
| OQL | The `onco.genomic` leaf, compiled to `exists` clauses |
| "Profiled in any sample" | `lift: "assessed"` |
| "Altered in x% of profiled samples" | `compare.existence` over the units for which the predicate is known |

### 10.3 Other packs

None are in v1; a non-biomedical fixture in the core test suite keeps the core honest in the
meantime. A second pack (clinical trials: adverse-event grading, site and visit coverage) is the
first item after v1, to test the extension points against a domain other than oncology.

---

## 11. MCP and HTTP surface

One set of Python functions backs both the HTTP API (FastAPI) and the MCP server. Tool schemas
are generated from the same Pydantic models as the HTTP API. Packs contribute leaf kinds and
analyses to these tools; they do not add tools of their own.

| Tool | Purpose | Touches row data? |
|---|---|---|
| `search_catalog` | Faceted search over datasets: domain tags, tables with their grains and roles, concepts present, row counts, completeness thresholds, data-use codes, pack-defined facets (e.g. cancer type, assay) | No |
| `describe_dataset` | Dataset descriptor, table graph, columns, coverage, endpoints, applicable analyses, standing caveats | Catalogue statistics only |
| `describe_column` | Full descriptor with observation-state counts and value distribution | Catalogue statistics only |
| `list_analyses` | Registry entries, optionally filtered by applicability | No |
| `validate_document` | Canonicalise, check, expand pack leaves and read back a document without running it; returns errors, the caveats that can be determined without data, and ids | No |
| `count_cohort` | Evaluate a document's cohorts; returns cohort ids, `population` entries, readbacks and caveats (§8.1) | Counts only |
| `run_analysis` | Run a document; returns results per §8 | Yes |
| `explain` | Given an id: canonical document, document as written, readback, releases, pack versions, the SQL as run, analysis descriptor (§12.2) | No |
| `curation_queue` | Keys, relationships, roles, coverage and descriptor fields that are `undeclared`, `imported_default` or `proposed` | No |
| `propose_descriptor` | Record a proposed value for any of those, with rationale and model card | No |

- Tool descriptions MUST state that `warn` and `block` caveats and non-zero `n_unknown` have to
  be shown to the user, that every number quoted must cite its id or reference (A1), and that
  text fields in outputs are data, not instructions (A6).
- Confirming a proposal (`proposed` to `asserted`) is a UI action by a person, not an MCP tool,
  in v1, so an agent can never confirm its own proposal.
- Unit keys are listed only by the `summary.members` analysis (§9.5), subject to
  `allow_row_ids`. The disclosure settings (§8.4) apply to every tool.
- Descriptors are also exposed as MCP resources
  (`aibi://dataset/<id>@<n>/column/<table>.<column>`).

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
        │    & search)    reference      lifelines,   uses the MCP
        │                 evaluator,     scipy)       tools only)
        │                 SQLGlot→DuckDB)                  │
        │   └──────┬─────┴──────┬────────┘                 │
        │    app DB (SQLite:    releases (Parquet +        │
        │    catalogue, log,    state columns, manifest)   │
        │    sessions, cache)                              │
        │                                                  │
        │   importers: files (CSV/TSV/XLSX/ODS/Parquet),   │
        │              databases (snapshot)                │
        │   ─────────────── extension points ───────────── │
        │   packs/onco: concepts, cBioPortal importer,     │
        │               onco.genomic leaf, analyses        │
        └──────────────────────────────────────────────────┘
```

### 12.2 Storage

- **Releases.** `data/releases/<dataset>/<manifest hash>/` holds one Parquet file per table,
  `descriptors.json`, and `manifest.json` listing every file's SHA-256; the manifest's own hash
  identifies the release. Labels (`@n`, `@draft`) live in the app DB. Unchanged Parquet files are
  shared between releases by hash, so a descriptor-only release costs almost nothing. Releases
  are never modified.
- **Observation states.** Every column that has missing codes or null cells has a companion
  column `<column>__state` holding each cell's observation state; the value column holds a value
  only where the state is PRESENT.
- **Withdrawal.** Withdrawing a release deletes its data files and computed statistics and keeps
  its manifest, its definitional descriptors and its derivation-log entries, with unit keys in
  logged `ids` leaves redacted. Its ids then resolve to *withdrawn*. Withdrawal is how consent
  withdrawal and erasure requests are honoured.
- **App DB (SQLite).** The catalogue index (a denormalised copy of descriptors for search),
  release labels, curation sessions and their audit trail, saved documents, the result cache
  (evictable), and the **derivation log**: an append-only, permanent record of every id issued,
  with its canonical document, the document as written, the SQL as run, the engine version and a
  timestamp.
- **Query engine.** DuckDB reading the release's Parquet files. The compiler builds queries as
  SQLGlot expression trees and never concatenates identifiers or values into strings; every
  identifier comes from a descriptor.

### 12.3 Curation sessions

- A dataset has at most one open curation session at a time (no user accounts in v1). Opening a
  session creates a draft release, labelled `@draft`, that starts as a copy of the latest
  published release's descriptors.
- Confirmations and edits change the draft; each change yields a new manifest hash. Queries
  against the draft carry `DRAFT_RELEASE` and are not cached.
- The session ends explicitly: **publish** turns the draft into `@n+1`; **discard** deletes it.
  Every change is recorded in the audit trail with a self-declared name (Q7).

### 12.4 Stack

| Layer | Choice |
|---|---|
| Language and tooling | Python ≥ 3.12, uv, ruff, pyright (strict on `core/`), pytest, hypothesis, import-linter |
| Schemas | Pydantic v2 as the source of truth; JSON Schema and OpenAPI generated from it |
| API and MCP | FastAPI; the official MCP Python SDK |
| Data | DuckDB (including its readers for CSV, Excel, Parquet, Postgres, MySQL and SQLite), Parquet, SQLite |
| Statistics | lifelines, scipy, statsmodels |
| Assistant | Claude API, calling the same MCP tools |
| Charts | Vega-Lite specifications generated on the server from result values and returned with the result, so the UI and agents draw the same figure |
| Frontend | React + TypeScript + Vite, types generated from OpenAPI; renders the Vega-Lite specifications, never charts from model text |
| Deployment | One process on a lab server, no user accounts in v1; the same package runs locally |

### 12.5 Repository layout

```
aibi/
  SPEC.md  CLAUDE.md  README.md
  server/
    pyproject.toml
    src/aibi/
      core/
        schema/      # Pydantic models: descriptors, documents, results, caveats, reasons, pack API
        store/       # release builder and reader, state columns, withdrawal, app DB, derivation log
        importers/   # files and database snapshots
        catalog/
        engine/      # reference evaluator; canonicalise → validate → plan → SQL
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
  into a release. Declared primary and foreign keys and column comments are imported with status
  `imported`. Credentials are handled as in §14.
- **Structure proposals:** for files, the importer proposes grain, table roles, primary keys,
  relationships (by value containment, as biai's foreign-key detector did), datatypes, list
  columns, missing codes and coverage for `entity` and `link` tables (§5.6). Everything proposed
  lands in the curation queue; nothing proposed is `asserted`.

### 13.2 Validation as a gate

Structural errors stop the import: unparseable files, duplicate primary keys, foreign keys that
reference missing parent rows, and coverage tables that reference unknown parents. For
proposed rather than declared keys, the proposal is dropped with its evidence instead. Semantic
gaps do not stop the import; they become `undeclared` or `imported_default` fields in the
curation queue: missing units, undeclared coverage, unknown missing codes, child rows outside
their parent's listed coverage (kept, since they are evidence, §6.5), and endpoint rows with
invalid values (§5.8). Packs add their own validators; the oncology pack's mirrors the
cBioPortal validator.

### 13.3 Reference evaluator

The reference evaluator is a pure-Python, row-by-row implementation of §6 and of
canonicalisation (§7.6), with no SQL. It is written before the SQL compiler (M2) and is the
executable definition of the semantics. The SQL compiler is tested against it: property tests
(hypothesis) generate small random datasets, with random coverage, scopes, missing codes and
null keys, and random documents, and require identical truth values, reasons and counts.

### 13.4 Tests that encode the principles

- **Three-valued logic:** for any cohort predicate `C`, `n_true(C) + n_false(C) + n_unknown(C)`
  equals the size of the unit table; `not(not C) ≡ C`; `C` and `not C` never share a unit;
  `known(C)` equals `C ∪ not C`; a direct reference below the unit equals its nested `exists`.
- **Scenarios of §6.5**, each as a test: unassessed samples, missing grades, blood normals,
  patients with no samples, participants with no enrolments, `every` over no rows, `min_count`.
- **Canonical form:** no user-chosen name survives canonicalisation; random renames, reordered
  top-level cohorts, reordered object keys and equivalent syntax (`!=` versus `not`, `op` versus
  `range`) give identical ids; changing a view's reference changes the result id and inverts the
  hazard ratio.
- **Refusals**, each asserting that the error lists the alternatives: unknown column, constant
  outside permissible values, unconvertible units, out-of-filter existence query, ambiguous path,
  unmapped cross-dataset reference (in a cohort and across a view's cohorts), unmet analysis
  requirement, overlapping cohorts for a test, a missing aggregate for a multi-valued column,
  `ids` without row-id access.
- **Digests:** golden documents with checked-in result ids and digests; CI fails if either
  changes without a version bump. Repeated runs with different thread counts give identical
  digests.
- **Statistics:** KM curves, log-rank, Cox, Fisher, chi-squared and BH values agree with
  reference outputs from R (`survival`, `stats`), computed once and checked in.
- **Reference evaluator versus SQL compiler:** the differential property tests of §13.3.
- **Provenance:** `explain` works for an id after the result cache is cleared; a withdrawn
  release's ids resolve to *withdrawn*.
- **Disclosure:** suppression applies to results, cohort counts and catalogue statistics.
- **Domain boundary:** an import-linter contract forbids `aibi.core` → `aibi.packs`; the core
  suite runs with no packs installed and includes a non-biomedical fixture.
- **Assistant evals:** questions the assistant must answer through the tools, citing ids and
  surfacing required caveats, including prompt-injection attempts in column descriptions, notes
  and cell values.

---

## 14. Security and privacy

- **Trust model (v1).** People using a deployment are trusted. Data, documents and agent
  requests are not: imported files and databases, shared documents and links, and anything an
  agent sends.
- **Untrusted text (A6).** Labels, definitions, cell values, file names and document notes are
  rendered as plain text, never as markdown or HTML. Tool outputs mark them as data, and the
  assistant's instructions say they are never instructions. Assistant evals include injection
  attempts (§13.4).
- **Queries.** Documents are validated against their schemas. No SQL is accepted. SQL is built
  from SQLGlot trees with identifiers taken from descriptors and constants passed as bound
  parameters.
- **Imports.** Uploaded files go to a server-managed area; server-side paths are accepted only
  inside configured import directories. Database credentials live in server configuration and
  never appear in descriptors, releases, logs or results; provenance records host, database and
  schema only.
- **Resource limits.** Document caps (§7.1), and per-query time and memory limits in DuckDB.
  Refusals name the limit.
- **Disclosure.** §8.4, including its limits.

---

## 15. Milestones

The order is agent-first: from M1 an external agent (Claude over MCP) is the primary interface,
and the web UI follows once the semantics are settled. A thin read-only page (catalogue and
dataset descriptors) ships with M1 so there is something to show without an agent.

| # | Deliverable | Exit criterion |
|---|---|---|
| **M0** | Repo skeleton and CI; Pydantic schemas for descriptors, documents, results, caveats, reasons and the pack API; JSON Schema export; import-boundary check | Schemas published; CI runs lint, type check, boundary check and tests |
| **M1** | Release store (manifest hashes, state columns, labels, withdrawal); generic importers (files and database snapshots) with proposals for keys, relationships, roles and coverage, and the validation gate; curation sessions; catalogue with citable statistics; `search_catalog`, `describe_dataset`, `describe_column`, `curation_queue`, `propose_descriptor`; read-only catalogue page | biai's example spreadsheets and a non-biomedical dataset imported with confirmed keys and relationships; an MCP client can find and describe them; a release can be withdrawn |
| **M2** | Reference evaluator first; then paths, canonicalisation, ids, derivation log, three-valued SQL compilation with coverage, scope and lift, core leaves, readbacks, `validate_document`, `count_cohort`, `explain` | Scenario and property tests pass on the reference evaluator; the SQL compiler matches it in the differential tests; the canonical-form tests pass |
| **M3** | Registry and the core analyses of §9.5, with aggregation rules and determinism; `applicable_analyses`; `run_analysis`; Vega-Lite output | Golden, R-reference and determinism tests pass |
| **M4** | Oncology pack: cBioPortal importer and validator, coverage tables from panels, parent scope for normal samples, `onco.genomic`, `onco.alteration_frequency`, concepts, endpoint proposer; cbio-lab translator | TCGA GBM PanCan and one panel study imported. From cbio-lab example 1 on `msk_chord_2024`, the altered-percentage comparison and the survival view are reproduced, plus a mutation-only wild-type comparison written with cbio-lab's `profiled` clause; numbers match where semantics agree, and every difference is explained in `docs/cbio-lab-differences.md`. **No core change in the pack's PR** |
| **M5** | Web UI: catalogue, dataset page with table graph and applicable analyses, cohort builder with live counts including unknowns, results with provenance panel, curation queue | The in-scope subset of biai's e2e scenarios, listed in `docs/ui-scenarios.md` (dashboards and map charts excluded), passes |
| **M6** | Assistant (chat that edits the document); AI-proposed descriptors, keys, relationships, roles and coverage with confirmation; concept mappings and cross-dataset queries | Assistant evals pass, including the injection cases; a mapped two-dataset survival comparison runs with a stratified log-rank |
| **M7+** | Clinical-trials pack; event tables and time-window leaves using observation windows; `onco.oncoprint`; driver annotation (onco); JSON-LD / Bioschemas export | Set when M6 lands |

---

## 16. Open questions

| # | Question | Current leaning |
|---|---|---|
| Q1 | Should aibi converge with cbio-lab (one engine, one DSL) rather than sit beside it? | Keep documents compatible and ship the translator; decide after M4, when the oncology pack can be compared with cbio-lab on the same studies |
| Q7 | Who may confirm proposals? | Any user in v1, recorded in the audit trail under a self-declared name. A curator role (and therefore user accounts) is a precondition for any public or shared deployment |
| Q9 | Who are the first users: this lab only, or outside groups? | Assumed this lab only. Outside users bring user accounts and Q7's curator role forward |

---

## Appendix A. Decisions

Decisions from the walkthrough (D1–D19) and the consistency reviews (D20–D72), all
2026-09-24. Each line records the choice and the reason; reopening one means changing this
table. Later decisions that revise or refine earlier ones say so.

| # | Topic | Decision | Reason |
|---|---|---|---|
| D1 | Model and numbers (A1) | Strict: the model only quotes numbers from cited results; analyses compute the comparisons people ask for, with CIs | A plausible wrong number is the failure nobody catches; a bare "2×" without an interval shouldn't be trusted anyway |
| D2 | Cross-dataset comparisons (A3) | Refused by default; an explicit opt-in marks every affected result | The assistant turns a refusal into a next step (map the columns), and warnings are easy for agents to skip |
| D3 | Scope | Timeline queries after v1, with observation windows in the v1 schema; Cox regression in v1; JSON-LD and federation after v1 | Timelines are most of the missing-data difficulty; Cox is cheap once survival exists, and unadjusted survival comparisons are weak evidence |
| D4 | Curation releases (refined by D63) | Confirmations are batched into one release per curation session | Keeps P6 without a release per click |
| D5 | Coverage defaults (revised by D43, D68) | Importer proposes coverage for obvious cases; open-scope existence answered with `SCOPE_PARTIAL`; scope columns equality-only in v1 | Datasets are useful at once and still honest; matches what cBioPortal users expect |
| D6 | Concepts | Small `core:` set plus pack vocabularies anchored to NCIt and LOINC; exact mappings; transforms limited to units and value maps; other derivations as declared columns | Keeps comparisons meaningful and every derivation visible |
| D7 | Table graph (revised by D60) | Many-to-one and one-to-one only (many-to-many through a linking table); up-then-down paths allowed with explicit readbacks; units need a primary key | Every step is a lookup or an existence question |
| D8 | Three-valued logic (revised by D66) | NOT_APPLICABLE is FALSE for value predicates; unknown counts always shown when above zero; differences from the cBioPortal convention explained by caveat, not by a second number | Matches plain meaning; makes P2 visible; two numbers invite choosing the convenient one |
| D9 | Lifting (was Q2; refined by D44, D72) | Strict by default; parents outside scope ignored; `lift: "assessed"` opt-in; the affected count always reported | Honest where it matters (an unassessed metastasis), without noise from blood normals or failed samples |
| D10 | Document shape | cbio-lab-compatible, with a translator that flags `not`; caps kept; cohort references added | The most common comparison is X versus not-X within a base, which is easy to get wrong by hand |
| D11 | Derivation ids | Engine version and cohort names excluded from hashes; ids designed to be citable, with no availability promise in v1 | Ids stay stable for caching and citation; version bumps carry result changes |
| D12 | Results (revised by D59) | `TIME_ORIGIN_MISMATCH` blocks; `SMALL_N` warns; SQL only through `explain`; a `min_cell_count` setting, off by default | Comparing different clocks is meaningless; small groups are imprecise but informative |
| D13 | Analyses (refined by D67) | `survival.km` includes the unadjusted HR; Cox always tests proportional hazards and warns on failure; only core and packs register analyses | "How much worse" always follows "is it worse"; user-uploaded analyses would skip the golden-test discipline |
| D14 | Packs (was Q8) | No pack MCP tools (visual outputs are analyses returning render specifications); packs in this repo until stable; a second pack right after v1 | Keeps A4 auditable and everything inside the registry |
| D15 | MCP (refined by D56, D59) | Confirmation stays UI-only; `count_cohort` added; row-level ids controlled per dataset by `allow_row_ids` | An agent can't confirm its own proposal; counting first is the most common agent step |
| D16 | Deployment and scale (were Q3, Q4) | Lab server without user accounts, also runnable locally; up to ~100k units and ~10M child rows per dataset | Fits TCGA- and MSK-scale studies on one machine |
| D17 | Charts | Vega-Lite specifications generated on the server with each result | The UI and agents draw the same figure |
| D18 | Order | Agent-first with an early read-only page; oncology pack before the UI; built-in assistant at M6 | External agents cover the AI-native use from M1; the P8 test happens while the core is cheap to change |
| D19 | Concept ownership, live databases (were Q5, Q6) | As D6; snapshots only, with scheduled re-snapshots creating releases | Keeps P6 |
| D20 | Reference group (refined by D45, D46) | Effect sizes use an explicit reference, defaulting to the first view cohort and written into the canonical form; view cohort order is kept | Otherwise reordering cohorts inverts a hazard ratio without changing its id |
| D21 | Cohort references | Resolved to the referenced cohort's canonical form | Names are not hashed, so references must not depend on them |
| D22 | Digest stability (revised by D53) | Values rounded to 12 significant digits before hashing; returned unrounded | Parallel aggregation and optimisers are not bit-reproducible |
| D23 | Suppression in derivations (revised by D59) | `min_cell_count` is part of the result derivation | A setting that changes outputs must change the id |
| D24 | Row ids (revised by D59) | With `allow_row_ids: false` the `ids` leaf is refused | A count over a chosen id reveals that unit's attributes |
| D25 | Overlapping cohorts (revised by D62) | Refused in a view unless `overlap: "allow"`, then `COHORTS_OVERLAP` | Tests assume independent groups |
| D26 | Structural coverage (revised by D43) | Importer proposes `parents: "all"` for every relationship | Otherwise every negative criterion over a child table is UNKNOWN on a fresh import |
| D27 | Record filters | Allowed-value lists on categorical columns; queries accepted only if provably inside; readbacks state the filter | Keeps the refusal rule decidable and the meaning of "any row" visible |
| D28 | Empty quantifiers | `every` over no rows is UNKNOWN; `min_count` is three-valued | Vacuous truth would put units into cohorts on no evidence |
| D29 | Coverage table without scope | FALSE if the parent is listed, otherwise UNKNOWN | Closed a missing case |
| D30 | Draft releases (refined by D57, D63) | Queries during a curation session use a draft release, carry `DRAFT_RELEASE`, and are neither cached nor citable | Keeps D4 compatible with P6 |
| D31 | Entity concepts (extended by D61) | Tables may map to an entity concept; required for cross-dataset units | Cross-dataset units needed a mapping |
| D32 | Caveat severities | Every caveat, core or pack, declares a severity | "Show warn and above" must be unambiguous |
| D33 | Missing child values | Existence is three-valued over rows; a row whose `where` is UNKNOWN makes the answer UNKNOWN, not FALSE | Otherwise a missing grade counts as "no grade ≥3 event" |
| D34 | Canonicalisation order | Defaults and view lists written first, cohort references inlined next, sorting last | Sorting before inlining lets names into the id |
| D35 | Result keys | Values keyed by position in the view, each position carrying its cohort id | Two identical cohorts would otherwise collide |
| D36 | View cohort lists (revised by D46) | Default is every cohort in document order | The default reference group would otherwise be undefined |
| D37 | `quantifier` versus `lift` (refined by D69) | The quantifier (per step) and the lift rule (unassessed children only) are separate | One parameter was doing two jobs |
| D38 | Dependency upgrades | A library upgrade that changes a golden digest requires an analysis version bump | Keeps the id-digest invariant true across upgrades |
| D39 | Record-filter wording | Unconstrained columns are accepted and implicitly restricted to the filter | Removed a contradiction |
| D40 | Row ids and suppression (revised by D59) | `allow_row_ids: false` requires `min_cell_count` | Narrow cohorts re-identify people without it |
| D41 | Draft release ids (revised by D57) | `<dataset>@draft-<content hash>`, valid in derivations, never citable | D30 needed an identifier |
| D42 | Null foreign keys | Lookups through a null key are UNKNOWN, reason `NO_PARENT` | Closed a gap |
| D43 | Coverage proposals (revises D26; refined by D68) | Proposed `parents: "all"` only for structural child tables; measurement and event tables stay undeclared; `COVERAGE_PROPOSED` otherwise | A plain-CSV panel import must not read unsequenced genes as wild-type |
| D44 | Lifting and empty sets | At an intermediate step, children are dropped by reason (`strict`: out of scope; `assessed`: also not assessed), and an empty set of remaining children is UNKNOWN (`NO_ROWS`), never FALSE | Otherwise patients with no samples, or with only unsequenced samples under `assessed`, were counted as wild-type |
| D45 | Names in canonical forms | No user-chosen name survives canonicalisation: view cohorts become an ordered list of canonical cohorts, the reference a position | Names had leaked into ids three times |
| D46 | Cohort order (revises D36) | Analyses with a reference group require an explicit `views[].cohorts` array; otherwise the default is every cohort ordered by id | JSON object order is not reliable; JavaScript reorders integer-like keys |
| D47 | Views across datasets | A view whose cohorts come from different datasets follows the cross-dataset rules | Closes a route around P3 |
| D48 | `!=` | Canonicalised to `not` over `values` | `!=` and `not =` gave different answers on NOT_APPLICABLE |
| D49 | Units in criteria | Numeric criteria carry `units`, defaulting to the column's and always shown in readbacks | "age > 60" on a column in days meant 60 days |
| D50 | Several values per unit | Tests require a per-unit aggregate; descriptive views count units per category, or rows when labelled | Rows of one unit are not independent observations |
| D51 | One definition per rule | Existence and coverage are defined once, as an algorithm; the reference evaluator is its executable form | Two sections had drifted apart |
| D52 | Digest scope | The digest covers cohorts, population, values and caveats | Cohort sizes and caveats could change unnoticed |
| D53 | Determinism (revises D22) | Order-independent floating-point sums, deterministic fits, seeded resampling; rounding as a second line | Rounding alone still flips values near a boundary |
| D54 | Derivation log and withdrawal | A permanent log of every id; releases can be withdrawn, keeping their identity | `explain` must work after cache eviction; consent withdrawal and erasure must be possible |
| D55 | Pack leaves | Ids hash the core expansion; pack versions are recorded, not hashed; readbacks use pack templates | Same meaning, same id; a pack upgrade that changes nothing changes no id |
| D56 | Citable counts | `count_cohort` returns cohort ids; catalogue statistics carry release-scoped references | A1 requires a citation for every number |
| D57 | Release identity (revises D41) | Hashes use manifest hashes; `@n` and `@draft` are labels | Deployments can't mint the same id for different data; a draft published unchanged keeps its ids |
| D58 | Untrusted text | Principle A6; credentials never stored in descriptors, releases, logs or results | Imported text and shared notes reach the assistant |
| D59 | Disclosure (revises D12, D23, D24, D40) | Settings live in the dataset descriptor (hence the release); a deployment floor is hashed; suppression applies to every tool; `summary.members` lists unit keys; described as risk reduction, not protection | Per-result suppression cannot stop differencing across queries |
| D60 | Paths (revises D7) | Any path that visits no table twice, up or down; revisits by explicit nesting; direct references are shorthand for nested `exists` | Many-to-many links need down-then-up steps |
| D61 | Cross-dataset queries | Entity-concept units, concept references throughout, per-dataset pack expansion, registry-declared cross-dataset methods | The previous rules covered only value leaves |
| D62 | Overlap (revises D25) | Refused only for analyses that assume independent groups | Side-by-side descriptive views of a base and a subset are common and harmless |
| D63 | Curation sessions (refines D4, D30) | One open session per dataset, ended by an explicit publish or discard | Without accounts, two people could otherwise publish conflicting drafts |
| D64 | Observation-state storage | A companion state column for each column with missing codes or nulls | A numeric column cannot hold `NA`, `N/A` and `Not done` distinctly |
| D65 | Unconfirmed semantics | `UNCONFIRMED_SEMANTICS` replaces `DEFAULT_SEMANTICS` and also covers undeclared fields | Undeclared units or time origins affected results silently |
| D66 | Lift caveat (revises D8) | `LIFT_DIFFERS` replaces `CONVENTION_DIFFERS`; packs supply domain wording | Core caveats must not name a domain (P8) |
| D67 | Survival details (refines D13) | The HR inside `survival.km` is tested for proportional hazards; medians not reached are *not estimable* | D13 applies to every hazard ratio; nothing is extrapolated |
| D68 | Table roles (refines D43) | Tables carry a proposed, confirmable `role`; coverage proposals follow it | A heuristic hidden in the importer would misclassify some tables invisibly |
| D69 | Quantifier names (refines D37) | `some` and `every` | `all` was both a combinator and a quantifier |
| D70 | Milestone exits | M4 reproduces only v1-feasible cbio-lab views and explains every difference; M5 lists its in-scope scenarios | The previous criteria could not be met |
| D71 | Reference evaluator | The executable definition of the semantics from M2; the SQL compiler is differential-tested against it | Prose review kept missing interactions between rules |
| D72 | Parent scope (refines D9) | Outside a table's parent scope, the question is UNKNOWN (`OUT_OF_SCOPE`); an UNKNOWN parent scope counts as in scope | Normal samples belong in neither the mutated nor the wild-type group |

---

## Appendix B. Change log

- **v0.4** — Rewritten for consistency, with each rule stated once. Applies the third review
  (D44–D72): lifting over empty sets, names removed from canonical forms, explicit view cohort
  order, cross-dataset views, `!=`, units, per-unit aggregation, digest scope, determinism,
  derivation log and withdrawal, pack-leaf hashing, citable counts, content-hash release
  identity, untrusted text (A6, §14), disclosure limits, paths, overlap, curation sessions,
  state columns, table roles, feasible milestone exits and the reference evaluator.
- **v0.3.2** — Second consistency review (D33–D43).
- **v0.3.1** — First consistency review (D20–D32).
- **v0.3** — Decisions from the walkthrough (D1–D19); Cox regression, engine-computed effect
  sizes, observation windows, cohort references, `count_cohort`.
- **v0.2** — Domain-agnostic core; oncology moved into a pack; principle P8.
- **v0.1** — First draft.
