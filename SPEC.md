# aibi — Specification

**Status:** Draft v0.5 · 2026-09-24
**Scope:** product goals, principles, data model, query semantics, result contract, analysis
registry, domain packs, tool and operator surfaces, security, architecture and milestones. The
text is normative where it says MUST, MUST NOT or SHOULD (RFC 2119); everything else is
rationale.
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
   differences in means and medians, hazard ratios, landmark survival), each with a confidence
   interval.
6. Ship an oncology pack (cBioPortal-format import, genomic query shorthand, alteration
   frequency) that uses only the public extension points.
7. Expose all of the above through one MCP server and a web UI that share the same functions,
   and keep the operations only people may perform on a separate operator surface (§11.2).
8. Use a model to *propose* descriptors, keys and relationships (curation) and to *draft*
   analysis documents (exploration), with a person confirming or editing both.

### 1.2 Non-goals (v1)

- Dashboards and report building as ends in themselves. aibi answers questions about cohorts;
  it is not a general BI tool (that was biai; see §2).
- Raw SQL access for users or models.
- Live queries against source databases. Database tables are snapshotted into releases (P6).
- Multi-tenant hosting, user accounts, fine-grained access control, and restricted or
  identifiable data. v1 assumes de-identified data on a trusted lab server (also runnable
  locally), with one curator token for the operator surface (§11.2). The disclosure settings of
  §8.4 reduce risk; they are not a privacy guarantee.
- Federation across sites. The result contract (§8) leaves room for it.
- Timeline queries, re-anchored survival and JSON-LD export: designed for, scheduled after v1
  (§15). The v1 schema already carries per-unit observation windows (§5.3) and delayed entry
  for survival (§5.8), so timeline support is an addition, not a redesign.

---

## 2. Prior work and what aibi takes from it

| Source | What aibi takes | What it leaves |
|---|---|---|
| **biai** (`jjgao/biai`) | The generic starting point: any multi-table dataset, relationships between tables, counting by a related (parent) table, filters that propagate along relationships, list-valued columns, spreadsheet and multi-sheet import, key detection. Its e2e specs and user guide are the behavioural checklist for the UI. | The code, ClickHouse as an app-state store, string-built SQL, per-chart round trips, silent path-finding between tables. |
| **cbio-lab DSL v2.2** (the system behind the `oncoprint` MCP server) | The document shape: named cohorts built from `all` / `any` / `not` clauses, plus views. The rules *clients never send SQL*, *refuse rather than approximate*, *denominators count only what was assessed*, *not-assessed is a first-class state*, deterministic plain-language readbacks, caveats the agent must surface, and `params` templates. Delayed entry for survival is in v1 (§5.8); `observed` windows on absence queries and re-anchored survival come with timeline queries after v1. The oncology-specific parts (OQL, profiles, panels) become the oncology pack. | Its `not` semantics (base EXCEPT leaf) are replaced by three-valued logic (§6.3). Documents are pinned to releases and hashed into derivation ids (§7.6). |
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
results and cohort counts (§7.6) and a release-scoped reference for catalogue statistics
(§8.1). Query results also carry the canonical document, the registered analysis and its
version, and the releases used; every proportion carries its numerator, its denominator and a
structured statement of what the denominator counts.
*Enforced by:* the result schema (§8). A value without a reference cannot be serialised.

**P2 — Missing is not negative.**
The data model distinguishes a value, a confirmed absence, not assessed, not applicable and
unknown (§6.2). No related rows means "none" only where coverage makes the row closed (§6.5).
Query logic is three-valued (§6.3). No statistic silently drops, imputes or reclassifies a
missing observation; every exclusion is counted by reason (§6.6).
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
surfaces (§11.1).

**P5 — Capabilities are declared, not inferred.**
Each analysis is a registry entry declaring the data it requires and the result it returns.
The MCP tool schemas, the UI's analysis menu and the answer to *"which analyses does this
dataset support?"* are all generated from the registry. *Enforced by:* no analysis can be
called except through the registry (§9).

**P6 — Results point to immutable releases.**
Data and descriptors are served from content-addressed, read-only releases, and derivations
refer to releases by content hash (§7.6). Curation changes are batched per curation session
into one new release (§12.3). A release can be withdrawn, which removes the data only it holds
but keeps its identity (§12.2). *Enforced by:* the storage layer; a document run against the
same releases returns the same result digest.

**P7 — One documentation surface for data, computations and models.**
Descriptors (data), registry entries (computations) and model cards (the models that propose
descriptors or draft documents) share one envelope (§5.1) and one lookup path. Results record
which model or person drafted the document they answer (§8.1).

**P8 — The core is domain-agnostic; domains are packs.**
The core knows about tables, keys, relationships, columns, observation states, coverage,
endpoints and concepts, and nothing else. It has no notion of patients, samples, genes or
assays, and its caveat codes, reasons and readback templates contain no domain terms. Domain
knowledge (vocabularies, descriptor extensions, importers, validators, query shorthands,
analyses, readback summaries, caveats) is added by packs through public extension points
(§10), and every pack leaf compiles to core clauses.
*Enforced by:* an import-boundary test: `aibi.core` MUST NOT import from `aibi.packs`, and the
core test suite runs with no pack registered. At least one non-biomedical fixture dataset is in
the core test suite. The oncology pack's exit criterion (M4) is that it ships without any
change to the core.

### 3.2 AI-interaction principles

**A1 — The model writes documents, never SQL and never numbers.**
Every number shown to a user comes from an engine result or cohort count with a derivation id,
or from a catalogue statistic with its reference. The UI renders numbers from results, not
from model prose. The in-app assistant may only quote numbers from a result it cites; it does
no arithmetic of its own. Comparisons people naturally ask for ("twice as frequent", "four
months shorter", "a third of the patients") are therefore computed by the engine, with
confidence intervals where they apply (§8.1, §9.5). The evals check both rules.

**A2 — One document, many editors.**
The UI, the chat assistant, MCP clients and shareable URLs all read and write the same
Analysis document. Its readback is generated deterministically from the canonical document,
never by a model, so a person can confirm that the document asks what they meant.

**A3 — Refuse rather than approximate.**
An unsupported operator, an unknown column, an ambiguous join path, or an analysis whose
requirements are not met fails loudly, names the problem, and lists what *is* available.

**A4 — No private tools.**
The in-app assistant uses exactly the public MCP tools. Anything it can do, an external agent
can do and a person can inspect. Operations reserved for people (§11.2) are not tools at all:
neither the assistant nor any agent can perform them.

**A5 — Proposals are visible until confirmed.**
Anything a model proposes (a key, a relationship, a table role, a descriptor field, a concept
mapping) is stored with status `proposed` and the proposing model's card. It is never silently
promoted to `asserted`, and results that depend on it carry a caveat until a person confirms
it.

**A6 — Text from data is never an instruction.**
Labels, definitions, cell values, file names, document notes and anything else that arrives
with data or documents is content, not instructions, for the in-app assistant, external
agents and the UI alike. It is rendered as plain text, carried in outputs as marked data
(§8.1), and never placed in tool names, descriptions or schemas (§14).

---

## 4. Glossary

| Term | Meaning |
|---|---|
| **Dataset** | A collection of related tables from one source, e.g. a clinical trial export, a CRM extract, or a cBioPortal study. |
| **Release** | An immutable snapshot of a dataset's data **and** descriptors, identified by the hash of its manifest and labelled `<dataset>@<n>` once published or `<dataset>@draft` while a curation session edits it (§12.3). |
| **Table** | A set of rows with a declared **grain** (what one row is) and a **role** (§5.3). |
| **Keyed table** | A table with a primary key. Any keyed table can be a unit. |
| **Unit** | The table whose rows a cohort counts. |
| **Relationship** | A declared many-to-one or one-to-one link from a child table's foreign key to a parent table's key. Relationships form the **table graph**. |
| **Path** | A sequence of up and down steps along relationships from one table to another (§6.1). |
| **Coverage** | A declaration of which parent rows (and, optionally, which scope values) a child table is complete for (§5.6). |
| **Closed** | Known to have no further, unrecorded children that matter to a question (§6.5). |
| **Reason** | Why a value is UNKNOWN: `NOT_ASSESSED`, `NOT_COVERED`, `NO_INFORMATION`, `NO_PARENT`, `OUT_OF_SCOPE` or `NO_ROWS` (§6.3). |
| **Endpoint** | A declared time-to-event outcome on a keyed table (§5.8). |
| **Concept** | A dataset-independent meaning (e.g. *age at diagnosis in years*, *person*, *time since diagnosis*) that columns, tables and endpoints can be mapped to (§5.7). |
| **Descriptor** | The structured metadata record for any of the above, or for an analysis or model. |
| **Pack** | A domain extension: concepts, descriptor extensions, importers, validators, leaf kinds, analyses, caveats (§10). |
| **Analysis document** | The JSON object declaring cohorts and views (§7). |
| **Derivation** | The canonical, hashed description of how a result or count was produced (§7.6), kept permanently in the derivation log (§12.2). |
| **Issuance** | One act of producing a result or count for a derivation, recorded with the document as written and the SQL as run (§12.2). |
| **Caveat** | A structured, coded statement about a result's fitness for use (§8.3). |
| **Operator** | A person using the operator surface with the deployment's curator token (§11.2). |
| **Semantics version** | The version of the core evaluation rules of §6, hashed into every cohort id (§7.6). |

---

## 5. Descriptors

### 5.1 Identifiers, envelope and curation status

**Identifiers.**
- Dataset, table and column ids match `[a-z][a-z0-9_]*` and are unique case-insensitively
  within their scope (datasets within a deployment, tables within a dataset, columns within a
  table). Importers normalise source names to this form and keep the originals in `source`
  (§13.1). Names ending in `__` followed by anything are reserved for the system (§12.2).
- Descriptor ids are stable across releases: `<table>`, `<table>.<column>`,
  `rel:<child table>.<column>[+<column>…]` (or `rel:<role>` when the relationship has a role),
  `cov:<child table>`, `ep:<name>`; concepts `core:<name>` or `<pack>:<name>`; analyses by
  registry id; model cards `model:<id>`.
- Field paths are JSON Pointers (RFC 6901) into `fields`.
- Integers outside ±(2^53 − 1) are carried as decimal strings wherever they appear in documents,
  canonical forms and results (§7.6).

**Envelope.** Every descriptor, whatever it describes, has:

```jsonc
{
  "kind": "dataset | table | column | relationship | coverage | endpoint | concept | analysis | model",
  "id": "a descriptor id (above)",
  "version": "string",
  "label": "Human-readable name",
  "definition": "One-paragraph definition in plain text",
  "provenance": { "source": "...", "pipeline": {"name": "...", "version": "..."}, "citation": ["doi:…"] },
  "fields": { /* kind-specific, §5.2–5.8, §9.1 */ },
  "extensions": { "<pack id>": { /* validated against the pack's JSON Schema */ } },
  "curation": { "<JSON Pointer into fields>": CurationStatus }
}
```

`curation` holds the status of every semantically meaningful field, including table roles,
coverage and concept mappings; no other place in a descriptor records a status.

```jsonc
CurationStatus = {
  "status": "asserted | proposed | imported | imported_default | undeclared",
  "by": "operator:<self-declared name> | model:<model card id> | importer:<name>@<version>",
  "at": "RFC 3339 timestamp",
  "evidence": "optional plain text or reference"
}
```

- `asserted`: confirmed by an operator. `imported`: taken from the source (a header row, a
  database comment, a declared constraint). `imported_default`: filled in by convention (e.g.
  mapping `NA` to UNKNOWN). `proposed`: suggested by a model or tool. `undeclared`: nobody has
  said.
- The engine treats undeclared semantics conservatively: an undeclared missing code is
  UNKNOWN, and undeclared coverage never makes a row closed (§6.5).
- Any field that affects a result and is `imported_default`, `proposed` or `undeclared` raises
  `UNCONFIRMED_SEMANTICS` (§8.3), except proposed coverage, which raises `COVERAGE_PROPOSED`
  instead.
- `extensions` is how packs add domain fields (e.g. the oncology pack's `reference_genome` on a
  dataset). The core stores and validates them but never interprets them.

### 5.2 Dataset descriptor

`name`, `description`, `domain_tags`, `citation` / `references`, `source` (file set, database
and schema, or repository and commit; never credentials, §14), `license`, `data_use` (a list of
`OntologyRef`, e.g. GA4GH DUO codes), `disclosure` (`min_cell_count`, `allow_row_ids`; §8.4)
and `packs` (the packs whose importers or extensions the dataset uses). Computed statistics
(`n_rows` per table, value distributions, observation-state counts, a table-graph summary) are
produced when the release is built and stored separately from the definitional descriptors
(§12.2), never written by hand.

### 5.3 Table descriptor

| Field | Meaning |
|---|---|
| `grain` | Plain-text statement of what one row is (e.g. *one adverse event report*) |
| `role` | `entity` (rows are things: patients, samples, visits), `link` (a many-to-many linking table: enrolments), `measurement` (observations about a parent: mutation calls, lab results), `event` (time-stamped occurrences: treatments, adverse events) or `coverage` (a coverage, assignment or group table, §5.6). The importer proposes a role; packs declare roles for the tables they create. Coverage proposals follow it (§5.6) |
| `primary_key` | Column list, or `none`; a table without a key can be filtered and aggregated but cannot be a unit |
| `maps_to` | Optional table concept the rows are instances of (e.g. `core:person`); required for a table referenced across datasets (§7.5) |
| `time_origin` | For tables with time columns: a time-origin concept (e.g. `core:origin.diagnosis`, §5.7), with a curation status |
| `observation_window` | For keyed tables: the columns (or a declared rule) giving, per row, the period over which that row's related records are complete, e.g. enrolment to last contact. Optional in v1 and unused by v1 analyses; timeline queries (M7) rely on it |
| `source` | Sheet, file or database table it came from, its original name, and any header or skip-row handling applied |

### 5.4 Column descriptor

| Field | Meaning |
|---|---|
| `datatype` | `number`, `integer`, `string`, `boolean`, `category`, `list<category>`, `date`, `datetime` (stored and compared in UTC), `time_offset` |
| `units` | UCUM code for numbers and offsets (`a`, `mo`, `d`, `mg/dL`, `[USD]`); required for any number used in a cross-dataset comparison |
| `permissible_values` | For categories: `[{value, label, concepts: [OntologyRef]}]`, and `ordered: true` when the listed order is meaningful (required for `max` and `min`, §9.2) |
| `missing_codes` | Map from a raw token to `UNKNOWN`, `NOT_APPLICABLE` or `NOT_ASSESSED`, e.g. `{"": "UNKNOWN", "NA": "UNKNOWN", "N/A": "NOT_APPLICABLE", "Not done": "NOT_ASSESSED"}` |
| `identifier` | `true` for columns whose values identify rows or people (patient numbers, record ids, UUIDs). Primary-key and foreign-key columns are identifiers implicitly. Governs §8.4 |
| `concepts` | `[OntologyRef]` describing what the column measures |
| `maps_to` | Optional concept mapping (§5.7) |
| `derived` | Optional derivation from other columns of the same table (§5.7) |
| `list_syntax` | Required for `list<…>`: how lists are encoded in the source (JSON array, Python literal, delimiter) |
| `completeness` | Declared `complete`, `partial` or `unknown` |
| `source` | Original column name and any header metadata imported with it |

`OntologyRef = {system, code, label, relation: "exact | broader | narrower | related"}`. The
core accepts any `system` string; packs register the systems they validate (e.g. NCIt, LOINC,
OncoTree, HGNC). How values and observation states are stored is in §12.2.

### 5.5 Relationship descriptor

`{child_table, child_columns, parent_table, parent_columns, cardinality: "many-to-one" |
"one-to-one", role?}`. `role` names the relationship when two tables are linked more than once
(e.g. `orders.billing_customer` and `orders.shipping_customer`). A null foreign key is allowed;
a dangling one (non-null, with no matching parent) is a structural error (§13.2). Both
evaluate as `NO_PARENT` (§6.1).

### 5.6 Coverage descriptor

A coverage declaration says for which parents a child table is complete, and over what scope:

```jsonc
{
  "child_table": "mutations",
  "relationship": "rel:mutations.sample_id",
  "parents": "all" | "undeclared"
    | { "table": "<coverage table>",                      // direct form
        "parent_columns": { "<coverage column>": "<parent key column>" },
        "scope_columns":  { "<coverage column>": "<child scope column>" } }   // optional
    | { "assignment": { "table": "<assignment table>",   // grouped form
                        "parent_columns": { "<column>": "<parent key column>" },
                        "group_column": "<column>" },
        "groups":     { "table": "<group table>", "group_column": "<column>",
                        "scope_columns": { "<column>": "<child scope column>" },
                        "covers_all_column": "<boolean column>" } },       // optional
  "record_filter": { "variant_class": ["missense", "nonsense", "frameshift", "splice"] },
  "parent_scope": { /* a core clause over the parent table, e.g. sample_type = tumour */ }
}
```

- `parents: "all"`: every in-scope parent was assessed. A **coverage table** lists the assessed
  parents or, with scope columns, the assessed (parent, scope value) tuples. The **grouped
  form** assigns each assessed parent to a group and lists each group's scope values, or marks
  a group as covering every scope value; this is how gene panels (sample → panel → genes) and
  whole-exome samples are expressed without a row per (sample, gene). `undeclared`: nobody has
  said.
- Scope columns are matched by equality in v1 (a gene, a visit window). Range-based scope such
  as genomic intervals is out of scope for v1.
- `record_filter` states what kinds of rows the table holds, as a conjunction of allowed-value
  lists on categorical columns. Queries are checked against it (§6.5, step 1).
- `parent_scope` names which parents the table is about (e.g. tumour samples, not blood
  normals). It is evaluated as in §6.5, step 2.
- Coverage, assignment and group tables have role `coverage` and are not part of the table
  graph (§6.1). D16's scale targets count data tables only.
- **Proposals.** The importer proposes `parents: "all"` for child tables whose role is `entity`
  or `link`, for files and database snapshots alike. Tables whose role is `measurement` or
  `event` stay `undeclared`, because that is where partial coverage hides; the curation
  assistant may propose coverage for them with evidence (e.g. *a panel column was found*), and
  an operator decides. Packs declare coverage explicitly. Results that rely on proposed coverage
  carry `COVERAGE_PROPOSED`, and the curation queue ranks these confirmations first.

### 5.7 Concepts, mappings and derived columns

A **concept** is a dataset-independent descriptor (`kind: concept`) of one of four sorts:
value concepts, with units or permissible values; table concepts, naming what rows are;
endpoint concepts, naming time-to-event outcomes; and time-origin concepts, naming what time
zero means. Concepts are namespaced by who defines them and versioned; canonical forms record
the versions they use (§7.6).

The core defines: `core:person` (table); `core:age_years` (value, units `a`); `core:sex`
(value; `female`, `male`); and the time origins `core:origin.birth`, `core:origin.diagnosis`,
`core:origin.enrolment`, `core:origin.randomisation`, `core:origin.specimen_collection`,
`core:origin.treatment_start` and `core:origin.calendar` (absolute dates). Packs add their own
(`onco:overall_survival`, `onco:oncotree_code`, `onco:origin.first_sequencing`).

A column, table or endpoint maps to a concept through `maps_to`:

```jsonc
ConceptMapping = {
  "concept": "core:age_years",
  "transform": { "unit_from": "d", "unit_to": "a" } | { "value_map": {"M": "male", "F": "female"} } | null
}
```

- Mappings are **exact**: a column maps to a concept iff it means that concept. A looser
  comparison needs a looser concept (e.g. `core:age_years_approx`), not a weaker mapping.
- Only mappings with curation status `asserted` are used; others are ignored and listed when a
  reference to their concept is refused.
- Transforms are limited to unit conversions and value maps. Anything else is a **derived
  column**: `derived: {op, inputs, …}` with `op` one of `date_diff` (`from`, `to`, `units`),
  `arith` (`+`, `−`, `×`, `÷` over numeric inputs and constants), `value_map` and
  `unit_convert`. Derived columns are computed when the release is built. A derived cell is
  PRESENT when all inputs are PRESENT; otherwise it takes the first of UNKNOWN, NOT_ASSESSED and
  NOT_APPLICABLE found among its inputs, in that order.

### 5.8 Endpoint descriptor

`table` (a keyed table), `time_column`, `status_column`, `time_units`, `event_coding` (which
status values are events and which are censored), `time_origin` (a time-origin concept;
defaults to the table's), `entry` (delayed entry: `"at_origin"`, `{"column": <time_offset
column on the same clock>}` or undeclared), and optionally `maps_to` an endpoint concept.

- An undeclared `entry` raises `UNCONFIRMED_SEMANTICS` on every survival result that uses the
  endpoint, because survival from an origin that precedes entry into the data is biased
  (immortal time) unless entry is declared.
- For analyses, a row whose time, status or entry cell is missing is excluded with that cell's
  reason; a status outside `event_coding`, a negative time or an entry after the time is
  excluded as `INVALID_VALUE` (§6.6). The importer lists such rows, as counts, in the curation
  queue. The core detects nothing by name; packs and the curation assistant propose endpoints.

---

## 6. Query semantics

### 6.1 The table graph and paths

Tables other than coverage tables are the nodes of the table graph; relationships are its
edges, from child to parent.

- An **up step** (child to parent) is a lookup: each child row has at most one parent. A null or
  dangling foreign key makes the looked-up value UNKNOWN with reason `NO_PARENT`.
- A **down step** (parent to children) is an existence question (§6.5).
- An **implicit path**, from the unit (or from the current row, inside a `where`) to a
  referenced table, is a sequence of up and down steps that visits no table twice. If exactly
  one exists, it is used. If none exists, the reference is refused. If several exist, the
  document is refused, listing them, unless `via` names one. There is no shortest-path or other
  silent choice.
- An **explicit path** (`via`, a list of relationship ids with directions) may revisit a table,
  e.g. samples → patient → samples for *the other samples of the same patient*;
  `exclude_self: true` on an `exists` leaves out the row the path started from.
- A reference to a column below the unit is shorthand for nested existence questions, one per
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
| `NOT_ASSESSED` | It is known that it was not assessed | A `Not done` code; a parent that coverage does not list |
| `NOT_APPLICABLE` | The question does not apply | `N/A` mapped to NOT_APPLICABLE |
| `UNKNOWN` | There is no information either way | Empty cell, undeclared missing code, undeclared coverage |

A cell takes its state from the column's `missing_codes`; a null or empty cell with no declared
code is UNKNOWN. Items of a `list<…>` cell each have their own state (§12.2). For ordinary
columns, a negative answer (`"No"`) is a PRESENT value. ABSENT arises only from existence
questions.

### 6.3 Truth values, reasons and combinators

Every criterion evaluates, per row it applies to, to TRUE, FALSE or UNKNOWN. UNKNOWN carries a
non-empty set of reasons:

| Reason | Meaning |
|---|---|
| `NOT_ASSESSED` | A cell says it was not assessed (a code such as `Not done`) |
| `NOT_COVERED` | Coverage says the row was not assessed: a coverage table does not list it, or nothing below it could be considered (§6.5) |
| `NO_INFORMATION` | No information either way: an empty cell, an undeclared code, undeclared coverage |
| `NO_PARENT` | An up step met a null or dangling foreign key |
| `OUT_OF_SCOPE` | The question does not apply to this row: it is outside a table's `parent_scope` |
| `NO_ROWS` | Nothing to evaluate: `every` over no rows, or `match: "all"` over an empty list |

- `all` is FALSE if any operand is FALSE, otherwise UNKNOWN if any is UNKNOWN, otherwise TRUE.
  `any` is TRUE if any operand is TRUE, otherwise UNKNOWN if any is UNKNOWN, otherwise FALSE.
  `not` swaps TRUE and FALSE and leaves UNKNOWN. When a result is UNKNOWN, its reasons are the
  union of the reasons of its UNKNOWN operands. (This is Kleene's strong three-valued logic.)
- `known(C)` is TRUE iff `C` is not UNKNOWN, otherwise FALSE; `unknown(C)` is TRUE iff `C` is
  UNKNOWN, otherwise FALSE. They are the only way to include unknowns deliberately.
- A unit is **in** a cohort iff the cohort's predicate is TRUE for it.

### 6.4 Value predicates

A value predicate compares one column's value per row with constants. Its forms are `values`
(membership in a set), `range` (`gt`, `gte`, `lt`, `lte`) and `op` with `value` (`=`, `!=`,
`>`, `>=`, `<`, `<=`), with an optional `negate: true`. Canonicalisation reduces them to
`values` and `range`, each with `negate` (§7.6).

| Cell state | Base result |
|---|---|
| PRESENT | TRUE or FALSE, by the value |
| NOT_APPLICABLE | FALSE |
| NOT_ASSESSED | UNKNOWN (`NOT_ASSESSED`) |
| UNKNOWN | UNKNOWN (`NO_INFORMATION`) |

- `negate` swaps TRUE and FALSE of the base result, per row, and leaves UNKNOWN. So for a male
  patient, `menopause = "pre"` is FALSE and `menopause != "pre"` is TRUE, while `age <= 60` and
  `age > 60` are both FALSE when age is NOT_APPLICABLE. Readbacks show every negation.
- On a multi-valued reference (a column below the unit, or a `list<…>` column), `negate`
  applies per row or item, inside the quantifier: *some mutation whose gene is not TP53*. A
  clause-level `not` around the leaf negates the quantified answer: *no TP53 mutation*.
- **Units.** A numeric predicate carries `units`: by default the column's units, or the
  concept's units for a concept reference, written into the canonical form. Constants in other
  units are converted when UCUM allows, and refused otherwise. Readbacks always state the
  units. A numeric column without declared units raises `UNCONFIRMED_SEMANTICS`.
- **Categories.** If the column's permissible values are declared, a constant outside them is
  refused, and the error lists them.
- **Lists.** Each item of a `list<…>` cell is evaluated with its own state. With
  `match: "any"` (default) the result is TRUE if any item is TRUE, otherwise UNKNOWN if any item
  is UNKNOWN, otherwise FALSE; an empty list is FALSE. With `match: "all"` it is FALSE if any
  item is FALSE, otherwise UNKNOWN if any item is UNKNOWN or the list is empty (`NO_ROWS`),
  otherwise TRUE.

### 6.5 Existence and coverage

An **existence question** asks, from a row `r`, about the rows of a table `T` reached by a path
(§6.1). It has an inner predicate `W` over `T`'s rows (the `where` clauses, ANDed; no clauses
means TRUE), a quantifier (`some` with `min_count` *k* ≥ 1, default 1, or `every`) for each down
step, and a lift rule (`strict`, the default, or `assessed`). Up steps are lookups. Each down
step is evaluated as below, from a row `r` into its child table `C`, with `W_C` the per-child
question: `W` itself at the last down step (the **final** step), and the existence question for
the rest of the path at every earlier one (an **intermediate** step).

For each `r`:

1. **Record filter** (final step only). If `C`'s coverage has a `record_filter`, each filtered
   column is either not mentioned in `W`, in which case `W` means *any row the table holds* and
   the readback states the filter, or mentioned only in top-level conjuncts of `W` of the form
   `values` without `negate`, whose values lie within the allowed values. Otherwise the document
   is refused, because an absence of rows outside the filter means nothing.
2. **Parent scope.** If `C`'s coverage has a `parent_scope` and it is FALSE for `r`, the answer is
   UNKNOWN (`OUT_OF_SCOPE`). If it is UNKNOWN for `r`, `r` counts as in scope, but coverage
   `all` does not close it (step 4).
3. **Children.** Evaluate `W_C` for each child. At an intermediate step, drop the children whose
   value is UNKNOWN with every reason in the lift rule's drop set: `strict` drops
   `OUT_OF_SCOPE`; `assessed` drops `OUT_OF_SCOPE` and `NOT_COVERED`. Final steps drop nothing.
   Let *K* be the remaining children, and *t*, *f*, *u* the numbers of them that are TRUE, FALSE
   and UNKNOWN.
4. **Closedness.** `r` is *closed* when it is known that `r` has no unrecorded children that
   matter to the question. When `r` is not closed, the step gives a reason.
   - Coverage `all`: closed. If `r`'s parent scope is UNKNOWN, not closed, with the scope
     clause's reasons.
   - A coverage table, direct or grouped, without scope columns: closed iff it lists `r`;
     otherwise `NOT_COVERED`.
   - With scope columns, let *S* be the scope columns that `W_C` mentions. `W_C` may mention a
     scope column only in top-level conjuncts of the form `values` without `negate`; any other
     mention is refused.
     - `some`: closed iff, for every combination of the values `W_C` admits on *S*, the coverage
       lists `r` for at least one scope tuple matching it; otherwise `NOT_COVERED`. If some scope
       columns are not in *S* (in particular if *S* is empty), a FALSE answer covers only the
       listed tuples (`SCOPE_PARTIAL`).
     - `every`: `W_C` MUST NOT mention scope columns (refused otherwise). Closed iff the coverage
       lists `r` for at least one scope tuple; a TRUE answer covers only the listed tuples
       (`SCOPE_PARTIAL`); otherwise `NOT_COVERED`.
   - Coverage `undeclared`: not closed, reason `NO_INFORMATION`.
5. **Answer.**
   - `some` with `min_count` *k*: TRUE if *t* ≥ *k*. Otherwise, at an intermediate step with *K*
     empty, UNKNOWN (`NOT_COVERED`). Otherwise UNKNOWN if *t* + *u* ≥ *k*, with the reasons of
     the UNKNOWN children and, if `r` is not closed, the closedness reason. Otherwise FALSE if
     `r` is closed, else UNKNOWN with the closedness reason.
   - `every`: FALSE if *f* ≥ 1. Otherwise, with *K* empty, UNKNOWN: `NOT_COVERED` at an
     intermediate step, `NO_ROWS` at the final step. Otherwise UNKNOWN if *u* ≥ 1, with the
     reasons of the UNKNOWN children and, if `r` is not closed, the closedness reason.
     Otherwise TRUE if `r` is closed, else UNKNOWN with the closedness reason.
6. **Evidence.** A matching child is evidence even where the coverage does not list `r`: step 5
   makes the answer TRUE regardless (the importer flags such rows, §13.2).
7. **Caveats.** An answer that depended on closedness (a FALSE from `some`, a TRUE from `every`,
   either value of `covered`, and every aggregate of §9.2) raises `SCOPE_PARTIAL` when step 4
   restricted it to listed tuples, and `COVERAGE_PROPOSED` when the coverage it relied on is
   `proposed`.

**`covered`** (§7.2) turns closedness into a predicate. At the final step it is UNKNOWN
(`OUT_OF_SCOPE`) outside the parent scope; otherwise TRUE if `r` is closed for the given scope
values, FALSE if the coverage is declared and `r` is not closed, and UNKNOWN with the closedness
reason if the coverage is undeclared or the parent scope is UNKNOWN. Across intermediate steps
it is lifted with `every` under `strict` and `some` under `assessed`, using steps 2–5.

Consequences, which the reference evaluator's scenario tests encode:

- `not exists mutations where gene = TP53`, for a sample, is TRUE only if the sample is assessed
  for TP53 and has no TP53 row. With `where: [{any: [gene = TP53, gene = EGFR]}]` the document is
  refused (step 4); `gene in [TP53, EGFR]` is the accepted form.
- A participant whose only adverse event has a missing grade is UNKNOWN both for *some adverse
  event of grade ≥ 3* and for its negation.
- A patient with one assessed wild-type tumour sample and one unassessed tumour sample is
  UNKNOWN for *a TP53 mutation in some sample* under `strict` and FALSE under `assessed`. A blood
  normal outside the mutations table's parent scope changes neither answer. A patient with no
  samples, or with only a blood normal, is UNKNOWN (`NOT_COVERED`) under both.
- A participant with no enrolments is FALSE for *enrolled in a phase 3 trial* when enrolments
  are closed; the step is final, because the trial's phase is a lookup above enrolments.
- `every` over a table with undeclared coverage is never TRUE, and a parent whose scope is
  UNKNOWN is never closed by coverage `all`.

### 6.6 Accounting

Every cohort result reports, over the unit table:

- `n_true`, `n_false` and `n_unknown`;
- `unknown_by_reason`: the UNKNOWN units per reason (a unit counts under each of its reasons);
- `unknown_by_leaf`: for each top-level clause of the canonical cohort (each member of its
  top-level `all`), keyed by that clause's canonical hash (`leaf:<sha256>`), the units whose
  cohort result is UNKNOWN and for which that clause is UNKNOWN (counts can overlap). The map
  from the leaves of the document as written (a pack leaf counts as one) to these keys is kept
  outside the digest (§8.1);
- `lift_differs`: the units whose truth value would change if every `lift` in the canonical
  cohort were flipped at once; when it is above zero, the result carries `LIFT_DIFFERS`.

Analyses exclude units or rows for **exclusion reasons**: the six UNKNOWN reasons, plus
`NOT_APPLICABLE` and `INVALID_VALUE`. Every result states, per cohort position, how many units
were analysed and how many were excluded for each reason (§8.1).

Whenever `n_unknown` is above zero, the UI and the assistant MUST show it next to the cohort
size, with its largest reason (e.g. *120 patients; 40 could not be evaluated, mostly not assessed
for copy number*), subject to §8.4.

---

## 7. Analysis document

### 7.1 Shape

```jsonc
{
  "aibi": "1",                                   // document format version
  "packs": { "onco": ">=1.2,<2" },               // PEP 440 specifiers; exact versions are recorded (§7.6)
  "params": { "min_grade": 3 },                  // optional; exact "$name" substitution, as in cbio-lab
  "dataset": "trial_xyz",                        // "trial_xyz", "trial_xyz@3", "trial_xyz@sha256:…" or "trial_xyz@draft"
  "unit": "participants",                        // a keyed table, or a table concept such as "core:person" (§7.5)
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
  "drafted_by": "model:<card id> | operator:<name>"   // provenance; never part of a hash
}
```

`Clause := Leaf | {"all": [Clause]} | {"any": [Clause]} | {"not": Clause} | {"known": Clause} | {"unknown": Clause}`

- **Releases.** An unpinned dataset means its latest published release; `@draft` means the open
  curation session's draft (§12.3). A document resolves each dataset to exactly one release;
  mixing releases of one dataset in one document is refused.
- **Params.** A value that is exactly `"$name"` is replaced by that parameter, whatever its
  type; `"$$…"` stands for a literal string starting with `$`; there is no interpolation inside
  longer strings. Substitution happens before validation. An unknown name is an error naming its
  path; declared but unused parameters are reported; the parameters used are echoed in results.
- **Parsing.** Duplicate keys in a JSON object are refused. Size limits are in §14.
- **Caps**, applied to the canonical form (§7.6): depth 4, 32 leaves per cohort, 6 cohorts, 8
  views per document. They keep readbacks readable and queries bounded.
- **Notes** are plain text, never compiled and never interpreted (A6).
- **Translation.** Documents in other formats (cbio-lab's first) are translated by pack
  document translators (§10.1) through `validate_document`, which flags every `not` whose
  meaning changes under three-valued logic.

### 7.2 Core leaf kinds

| Kind | Shape | Meaning |
|---|---|---|
| `value` | `{kind: "value", column: "<table>.<column>" \| "<concept>", values? \| range? \| op? + value?, negate?, units?, match?, quantifier?, lift?, via?}` | A value predicate (§6.4) on a column of the current table or any table reachable from it (§6.1). Down steps use `quantifier` (`some` or `every`, default `some`; a list gives one per down step) and `lift` (§6.5) |
| `exists` | `{kind: "exists", table, where?: [Clause], quantifier?, min_count?, lift?, via?, exclude_self?}` | An existence question (§6.5). `where` clauses are ANDed and evaluated per row of `table`; `min_count` applies with `some` only |
| `covered` | `{kind: "covered", table, scope?: {<column>: [values]}, lift?, via?}` | Coverage as a predicate (§6.5) |
| `ids` | `{kind: "ids", ids: ["<dataset>:<key>" \| {"dataset": "<id>", "key": [<values>]}, …]}` | An explicit list of unit keys (composite keys as lists). Allowed only at the top level of a cohort, and refused on datasets with `allow_row_ids: false` (§8.4) |
| `cohort` | `{kind: "cohort", cohort: "<name>"}` | Another cohort of the same document, with the same unit and dataset(s). Allowed only at the top level of a cohort; cycles are refused. `{"all": [{"kind": "cohort", "cohort": "base"}, {"not": X}]}` is the correct *rest of the base* under three-valued logic, which is why references exist |

### 7.3 Pack leaf kinds

Packs register leaf kinds namespaced by pack id (§10.1), e.g. `{"kind": "onco.genomic", "q":
"EGFR: AMP; PTEN: HOMDEL"}`.

- A pack leaf is compiled to core clauses by the pack's leaf compiler: a pure, deterministic
  function of the leaf, the dataset's descriptors and the pack version, with no access to data.
- The canonical form, and therefore every id, contains the expansion, not the pack leaf: two
  ways of writing the same criterion get the same id.
- Readbacks render the expansion, because that is what is computed. The pack may add a summary
  sentence rendered from the leaf as written, labelled as the pack's summary and kept outside the
  digest.
- The document as written, including pack leaves, is kept in the result envelope outside every
  hash; `validate_document` and `explain` return both forms.

### 7.4 Views

```jsonc
{ "analysis": "<registry id>", "cohorts": ["<name>", …], "reference": "<name>",
  "overlap": "allow", "unmapped": "allow", "params": { … }, "note": "plain text" }
```

- `cohorts` is an ordered array. It is required when the analysis declares `uses_reference`
  (effect sizes); otherwise it defaults to every cohort, ordered by cohort id. JSON object key
  order is never used for anything.
- `reference` names the reference group for effect sizes and defaults to the first entry of
  `cohorts`. It applies only to analyses that declare `uses_reference`.
- For analyses that declare `assumes_independent_groups` (§9.1), cohorts that share units are
  refused unless the view says `overlap: "allow"`; the refusal reports the overlap as a cohort
  count with its own id. With `overlap: "allow"`, the result gives each cohort's descriptive
  values only: tests, q-values and between-cohort intervals are not estimable (reason
  `overlapping_cohorts`) and the result carries `COHORTS_OVERLAP`. The valid contrast is a
  subset against the rest of its base (§7.2, `cohort`).
- `params` are arrays and objects with fixed keys, never keys chosen by the user. Clauses in
  `params` (predicates, covariates) are canonicalised like cohort clauses; column, table and
  endpoint references in them are resolved like leaves, against the releases of the view's
  cohorts.
- A view whose cohorts come from more than one dataset is a cross-dataset query (§7.5);
  `unmapped: "allow"` on the view is its opt-in.

### 7.5 Cross-dataset cohorts and views

A cross-dataset query is a cohort with `datasets`, or a view whose cohorts come from different
datasets. In one:

- The unit MUST be a table concept, resolved in each dataset to the one table that maps to it;
  the document is refused if a dataset has no such table or several.
- In the document as written, `value` columns, `exists` and `covered` tables, analysis
  parameters and endpoints MUST be referenced by concept, and every dataset MUST have an
  asserted mapping for each; otherwise the document is refused with a list of what is missing.
  `via` may be given per dataset. Pack leaves are expanded separately in each dataset.
- Endpoints MUST map to the same endpoint concept. Time origins are compared by concept id; if
  they are undeclared or differ, results carry `TIME_ORIGIN_MISMATCH` (block).
- The analysis MUST declare a cross-dataset method in the registry (§9.1), or the view is
  refused. A stratified estimate or test is reported only where at least two cohorts with data
  (with events, for survival) share a dataset; otherwise it is not estimable, and when cohort
  membership coincides with dataset the result carries `CONFOUNDED_WITH_DATASET`. Pooled values
  ignore dataset, are labelled as pooled and carry `POOLED_ACROSS_DATASETS`.
- `unmapped: "allow"`, on the cohort or view, replaces the refusal: references are then matched
  by name, and every affected result carries `UNMAPPED_COMPARISON`.
- The same individual appearing in two datasets cannot be detected; units from different
  datasets are always distinct.

### 7.6 Canonical form, ids and digest

Canonicalisation has two phases. `params` are substituted once, before both (§7.1).

**Phase 1: cohorts**, each after the cohorts it references:

1. Resolve every dataset reference to its release's manifest hash (the label is recorded beside
   it and never hashed), and every pack to its exact installed version (refused if missing or
   incompatible).
2. Expand pack leaves (§7.3), separately per dataset for a cross-dataset cohort.
3. Resolve names: columns, tables and concepts to descriptor ids (concepts with their
   versions); paths to explicit `via`; `cohort` leaves to the referenced cohort's canonical
   form, inlined.
4. Normalise syntax: `=` to `values` with one value; `!=` to `values` with `negate`; `op`
   inequalities to `range`; a `not` directly around a single-valued `value` leaf folded into its
   `negate`; nested `all` inside `all` and `any` inside `any` flattened, including inside
   `where`; quantifiers written as one per down step; every default written explicitly
   (`units`, `quantifier`, `min_count`, `lift`, `match`, `negate`).
5. Sort order-insensitive collections by their canonical serialisation, removing duplicates:
   members of `values` and `ids`, the value lists of `covered.scope`, `datasets`, and the clauses
   inside `all`, `any` and `where`.
6. Drop what does not affect results: names, `notes`, `note` and `drafted_by`.

A cross-dataset cohort's canonical form is a map from each dataset's manifest hash to that
dataset's canonical clause tree. After phase 1 no user-chosen name remains (§13.4).

**Phase 2: views.** Resolve each view's cohort names to cohort ids, in the order given or, by
default, by id; record `reference` as a position (only for analyses with `uses_reference`) and
`overlap` (only for analyses with `assumes_independent_groups`); canonicalise `params` as in
phase 1 and write their defaults.

Canonical forms are serialised with the JSON Canonicalization Scheme (RFC 8785). They contain no
non-finite numbers, and integers outside ±(2^53 − 1) are strings (§5.1).

- A **cohort id** is `drv:` + SHA-256 of {canonical cohort, unit (descriptor id or concept),
  release manifest hashes, semantics version, effective disclosure settings (§8.4), results
  versions of the packs involved}.
- A **result id** is `drv:` + SHA-256 of {analysis id and version, canonical parameters, the
  cohort ids in view order, effective disclosure settings, results versions of the packs
  involved}.
- The **packs involved** are the packs of the analysis, of the document's pack leaves and of
  the datasets' `packs` (§5.2).
- A **digest** is the SHA-256 of the canonical serialisation of a result's `cohorts`,
  `population`, `analysed`, `values` and `caveats`, or of a cohort count's `population` and
  `caveats`, with numbers rounded as in §9.3. In `caveats` only code, severity and affected
  paths count; caveats are sorted by (code, affected paths) and de-duplicated, and
  `DRAFT_RELEASE` is excluded because it describes a release's status, not the computation.
  Rendered text (readbacks, labels, messages, and every field whose name ends in `_text`),
  charts and the document as written are outside every digest.

**Invariant:** the same id MUST produce the same digest. A change that alters a digest for an
unchanged id is a bug unless a version in the id was bumped: the analysis version, a pack's
results version, or the core semantics version. Dependency upgrades count: a new version of a
statistics library or of DuckDB that changes any golden digest requires bumping the affected
versions. Golden tests check this (§13.4); §9.3 states the scope of the guarantee.

Every id issued is recorded in the derivation log (§12.2), which `explain` reads. Ids are
designed to be citable, but v1 promises no availability; the ids of a withdrawn release resolve
to *withdrawn* (§12.2).

### 7.7 Readback

The server renders a deterministic, plain-language readback of every canonical cohort and view
from templates, as a list of segments: template text, and data tokens (labels, values, units),
each quoted, escaped and length-capped (A6, §14). A readback states every path step, quantifier
and lift rule, the units of every numeric constant, every record filter and parent scope, every
negation, and what is excluded. For example: *"Patients in GBM (TCGA PanCan) @3 with a TP53
mutation in some tumour sample. Patients with no TP53 mutation in any assessed tumour sample,
and some tumour sample not assessed for TP53, are unknown and not counted."* Pack summaries
(§7.3) follow the readback, labelled as such. Readbacks are returned with every result and
cohort count and shown next to every figure.

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
    "packs": { "onco": { "version": "1.2.0", "results_version": 3 } },
    "semantics_version": 1,
    "disclosure": { "min_cell_count": 5 },       // effective settings (§8.4)
    "engine": "aibi 0.5.0"
  },
  "issuance": "iss:…",
  "source": { "document": { /* as written */ },
              "leaves": { "<JSON Pointer into the document as written>": ["leaf:…", …] } },
  "digest": "sha256:…",
  "cohorts": [ { "position": 0, "id": "drv:…", "reference": true }, … ],   // view order
  "population": [ { "n_true": 0, "n_false": 0, "n_unknown": 0, "unknown_by_reason": { … },
                    "unknown_by_leaf": { "leaf:…": 0 }, "lift_differs": 0 } ],            // by position
  "analysed":   [ { "n": 0, "excluded": { "NOT_COVERED": 0, … }, "excluded_units": 0 } ], // by position
  "values": { "positions": [ { … } ], "view": { … } },
  "caveats": [ Caveat ],
  "readback": { "cohorts": [ Segments ], "view": Segments },
  "labels": [ { "data": "<cohort name>" }, … ],
  "charts": [ /* Vega-Lite specifications, §8.5 */ ]
}
```

- `values.positions` holds per-cohort values in view order; `values.view` holds values that
  belong to the view as a whole (omnibus tests, q-values, effect sizes).
- `analysed.excluded` counts a unit under each of its reasons; `excluded_units` counts each
  excluded unit once.
- The compiled SQL is not part of `run_analysis` responses; `explain` returns the SQL recorded
  for an issuance (§12.2).
- **Cohort counts** (`count_cohort`, §11.1) return, for each cohort, its id, digest,
  `population` entry, the cohort's size as a proportion of the unit table (§8.2), readback,
  caveats, releases and issuance.
- **Catalogue statistics** (row counts, value distributions, observation-state counts) carry a
  release-scoped reference `stat:<manifest hash>/<descriptor id>/<JSON Pointer>`, with
  `?floor=<n>` appended when a deployment floor (§8.4) applies. The assistant cites it like a
  derivation id.
- **Data wrapper.** Every string in an output that comes from data or from a document — labels,
  values, names, notes — is carried as `{"data": "<text>"}` (in segment lists, as a data token)
  and marked `"x-aibi-data": true` in the JSON Schema, so clients can keep it apart from
  server-written text (A6).

### 8.2 Numbers, proportions and effect sizes

- Results contain no non-finite numbers. A number that cannot be computed is `null`, with its
  reason in the enclosing object's `not_estimable` map, keyed by field path, e.g.
  `{"estimate": 29.0, "ci": {"low": 21.4, "high": null}, "not_estimable": {"ci/high": "not_reached"}}`.
  Any number may be not estimable: estimates, bounds, p- and q-values, medians and proportions.
- Every proportion is an object, never a bare number:

```jsonc
{
  "estimate": 0.412,
  "numerator": 124,
  "denominator": 301,
  "denominator_definition": { "position": 0, "predicate": "leaf:…", "counts": "units for which the predicate is known" },
  "denominator_text": [ /* rendered segments; outside the digest */ ],
  "excluded": { "NOT_COVERED": 17, "NO_INFORMATION": 3 },
  "ci": { "method": "wilson", "level": 0.95, "low": 0.358, "high": 0.468 }
}
```

  Survival-function estimates are not proportions and are exempt.
- Every effect size names its measure, its position and its reference position:

```jsonc
{ "measure": "hazard_ratio", "position": 1, "versus": 0, "estimate": 1.8,
  "ci": { "method": "wald", "level": 0.95, "low": 1.3, "high": 2.5 } }
```

- An effect size or test that cannot be estimated has `null` values with `not_estimable`
  reasons, and the result carries `NOT_ESTIMABLE`. Nothing is extrapolated.

### 8.3 Caveats

`Caveat = {code, severity: "info" | "warn" | "block", message: Segments, affects: [JSON Pointers
into the result]}`. Codes are a stable, documented enum; core codes are unprefixed and pack
codes are namespaced (`onco.DRIVER_ANNOTATION_PIN`). Every code, core or pack, declares its
severity. The core set:

| Code | Severity | Raised when |
|---|---|---|
| `UNKNOWN_EXCLUDED` | warn | A cohort, denominator or analysis excluded units because they were UNKNOWN |
| `SCOPE_PARTIAL` | warn | An answer that depended on closedness covers only the scope values a unit is listed for (§6.5) |
| `UNCONFIRMED_SEMANTICS` | warn | A descriptor field that affected the result is `imported_default`, `proposed` or `undeclared`; the message lists the fields |
| `COVERAGE_PROPOSED` | warn | An answer that depended on closedness relied on proposed coverage; the message names the table |
| `UNMAPPED_COMPARISON` | warn | A cross-dataset query matched references by name under `unmapped: "allow"` |
| `TIME_ORIGIN_MISMATCH` | **block** | Time-based values were compared across datasets whose time origins are undeclared or differ |
| `CONFOUNDED_WITH_DATASET` | warn | Cohort membership coincides with dataset, so a between-cohort contrast is a between-dataset contrast |
| `COHORTS_OVERLAP` | warn | Cohorts in a view share units; tests and between-cohort intervals were not computed |
| `SMALL_N` | warn | Units analysed or events fell below the analysis's minimums, events per parameter below 10, or expected counts below 5 in a contingency table |
| `PH_VIOLATED` | warn | The proportional-hazards test failed; the hazard ratio is an average over time |
| `DRAFT_RELEASE` | warn | The result was computed against a curation session's draft release |
| `SUPPRESSED` | info | Values were suppressed by the disclosure settings (§8.4) |
| `NOT_ESTIMABLE` | info | A number could not be estimated (§8.2) |
| `LIFT_DIFFERS` | info | The other lift rule would change some units' results; the message gives the count, in the pack's wording where a pack provides one |
| `POOLED_ACROSS_DATASETS` | info | A pooled value is reported next to a stratified one |

MCP tool descriptions MUST tell clients that `warn` and `block` caveats have to be shown to the
user. A `block` caveat means the result is returned for inspection but MUST NOT be presented as
an answer.

### 8.4 Disclosure settings

- Each dataset descriptor has `disclosure: {min_cell_count, allow_row_ids}`: `min_cell_count`
  (*k*) is off by default and at least 2 when set; `allow_row_ids` defaults to true. Being part
  of the descriptor, the settings are part of the release. A deployment may set a floor for
  `min_cell_count`; the effective value is the largest of the floor and the settings of every
  dataset an output draws on, and it is part of every cohort id, result id and statistic
  reference (§7.6, §8.1).
- With *k* set, every output follows these rules, and carries `SUPPRESSED` when they apply:
  - **Counts** (cohort counts, category counts, numerators, denominators, units analysed,
    exclusion counts): values from 1 to *k* − 1 are suppressed; 0 is shown. In a set of counts
    with a shown total, if exactly one count is suppressed, the next smallest is suppressed too.
  - **Derived statistics** (proportions, intervals, effect sizes, tests) are suppressed with the
    counts they are computed from. Suppressed tests leave the multiple-testing family, and the
    result says how many did.
  - **Histograms** use bin edges from the analysis parameters or the column's declared range,
    never from the data's minimum and maximum; a bin with 1 to *k* − 1 units is merged with a
    neighbour.
  - **Catalogue statistics** show no minima or maxima, and categories with fewer than *k* units
    are pooled as *suppressed*.
  - **Survival curves** are reported only up to the last time at which each cohort still has at
    least *k* units at risk, with event times grouped into intervals holding at least *k*
    events; medians and landmark estimates are reported only if they fall within the reported
    range.
  - **Models** (Cox fits) are reported only if every cohort and every covariate level has at
    least *k* units and *k* events; otherwise they are not estimable (reason `suppressed`).
- `allow_row_ids: false` refuses the `ids` leaf, the `summary.members` analysis, predicates on
  identifier columns (§5.4) and value distributions of identifier columns, and requires
  `min_cell_count`. `summary.members` is also refused for cohorts smaller than *k*.
- **Limits.** These settings reduce casual disclosure. They do not prevent inference across
  repeated queries (for example by differencing two counts), and they are not a privacy
  guarantee. Restricted data needs access control, which is outside v1 (§1.2).

### 8.5 Charts

Charts are Vega-Lite specifications generated on the server from result values. Their data is
inline (`data.values`), copied from the result; they contain no transforms that compute numbers,
no expressions built from data, and no URLs. Clients render them with a CSP-safe interpreter and
a loader that makes no network requests (§14).

---

## 9. Analysis registry

### 9.1 Entry

Each analysis is a descriptor (`kind: analysis`, §5.1) plus an implementation:

```jsonc
{
  "kind": "analysis", "id": "survival.km", "version": "1.0.0",
  "label": "Kaplan–Meier survival",
  "definition": "Kaplan–Meier estimate per cohort; k-sample log-rank test; medians and landmark survival with CIs; difference in medians and unadjusted hazard ratio, with CIs, versus the reference cohort.",
  "fields": {
    "requires": [
      { "role": "endpoint", "kind": "endpoint", "on": "unit" },
      { "role": "cohorts", "min": 1, "max": 6 }
    ],
    "params":  { /* JSON Schema, generated from a Pydantic model */ },
    "returns": { /* JSON Schema of `values` */ },
    "methods": { /* named methods, as in §9.5 */ },
    "library": { "name": "lifelines", "version": "…" },
    "assumptions": ["independent censoring", "independent groups"],
    "uses_reference": true,
    "assumes_independent_groups": true,
    "cross_dataset": { "method": "log-rank stratified by dataset; pooled estimates ignore dataset" },
    "randomness": "seeded",
    "caveats": ["UNKNOWN_EXCLUDED", "SMALL_N", "PH_VIOLATED", "NOT_ESTIMABLE", "TIME_ORIGIN_MISMATCH", "CONFOUNDED_WITH_DATASET"],
    "min_group_n": 10,
    "min_events": 5
  },
  "curation": {}
}
```

- Analyses are registered only by the core and by packs, through code review. Users cannot
  upload analyses in v1.
- Requirements are stated against core descriptor kinds (an endpoint, a numeric column, a child
  table with declared coverage); a pack analysis may also use the pack's requirement predicates
  (§10.1).
- `"on": "unit"` means the endpoint must be on the unit table itself, not reached by a lookup:
  with samples as the unit and a patient-level endpoint, a patient with several samples would be
  counted several times.
- `cross_dataset: null` means the analysis cannot run across datasets (§7.5).

### 9.2 Several values per unit

A column is **multi-valued** for a unit when it is below the unit or list-valued.

- Analyses that declare `assumes_independent_groups` use one value per unit, so a view MUST give
  an `aggregate` for each multi-valued column it uses: `count`, `max`, `min`, `mean`, `some` or
  `every` (the last two with a `values` set: *some row has a value in V*, *every row has a value
  in V*). `max` and `min` on categories require `ordered` permissible values.
- Aggregates are existence-like questions and follow §6.5: parent scope (step 2), dropping
  (step 3) and closedness (step 4) apply, and a unit that is not in scope and closed gets an
  UNKNOWN aggregate with the corresponding reason. Aggregates never accept an open scope: when
  the child table has scope columns, the view must restrict them to a finite set of values
  (step 4), and the unit must be listed for all of them.
- Row states: rows whose value is UNKNOWN or NOT_ASSESSED make `max`, `min` and `mean` UNKNOWN,
  with their reasons; rows whose value is NOT_APPLICABLE are skipped; `count` counts rows
  whatever their values.
- A closed unit with no rows has `count` 0. For `max`, `min` and `mean` the view gives `empty`:
  a value to use (e.g. `0` for *highest grade, none recorded*) or `"exclude"` (the default), in
  which case the unit is excluded with reason `NO_ROWS`.
- Descriptive analyses count units per category, so a unit with rows in several categories
  counts in each and the output carries `multi_membership: true`; or, if the view asks for
  `count: "rows"`, they count rows, labelled as rows.

### 9.3 Determinism

- **Sums.** Floating-point values that enter a digest are never aggregated with DuckDB's DOUBLE
  aggregates, which are not reproducible across runs. Sums and means are computed in Python
  with correctly rounded summation (`math.fsum`) over values fetched in canonical order, or in
  DuckDB on exact types (integers, DECIMAL).
- **Order.** Before every library call, rows are sorted by (cohort position, unit key, row key).
  Set-like arrays in outputs (caveats, affected paths, reasons) are sorted.
- **Fits.** Model fits use single-threaded linear algebra pinned to a fixed code path, fixed
  starting values, and convergence tolerances set by the analysis version.
- **Randomness.** Resampling (bootstrap, permutation) uses a fixed number of replicates set by
  the analysis version, seeded from the computation id: the result id's inputs without the
  disclosure settings, so that raising a floor does not change unsuppressed values.
- **Rounding.** Before hashing, numbers are rounded to 10 significant digits, and numbers whose
  magnitude is below 1e-12 are hashed as 0; an analysis may declare coarser precision for
  specific values.
- **Scope.** The invariant (§7.6) is guaranteed within a deployment and on CI's reference
  platform. A difference across platforms is a bug to fix with stricter determinism, never
  accepted silently.
- A dependency upgrade that changes a golden digest requires bumping the affected versions
  (§7.6).

### 9.4 Applicability

`applicable_analyses(dataset, unit)` matches each entry's `requires` against the dataset's
descriptors and returns, for each analysis, `available`, `unavailable` (naming the missing
requirement) or `available_with_caveats` (e.g. an endpoint whose event coding is
`imported_default`). The dataset page and `describe_dataset` both show this; before the registry
exists (M1–M2), they show an empty list.

### 9.5 Core analyses (v1)

Every analysis checks estimability before it computes: zero events in a group, a zero
denominator or no known units, non-convergence or separation in a fit, and degenerate tables
give `not_estimable` values (§8.2), never a number. NOT_APPLICABLE cells are excluded from tests
and counted (§6.6). Multiple-testing correction (Benjamini–Hochberg) covers the primary tests of
a view only; tests that cannot be computed leave the family and are counted.

| Id | Returns |
|---|---|
| `summary.distribution` | Per column, per cohort: for categories, units per category (proportions over units whose value is known; `multi_membership` flagged); for numbers, n, mean, standard deviation, median, quartiles, minimum and maximum, and a histogram with bin edges from the parameters or the column's declared range; observation-state counts. Descriptive only |
| `summary.members` | The unit keys of exactly one cohort, sorted, paginated with `offset` and `limit`; subject to §8.4 |
| `compare.columns` | Categories: chi-squared test of independence, or Fisher's exact test for 2×2 tables; per-category differences in proportions versus the reference, with Newcombe hybrid score intervals. Numbers: primary test Welch's t (two cohorts) or Welch's ANOVA (more); secondary test Mann–Whitney (exact when both groups have fewer than 50 values and no ties, otherwise the normal approximation with continuity correction, as R's `wilcox.test`) or Kruskal–Wallis, reported unadjusted; difference in means versus the reference (Welch–Satterthwaite interval) and in medians (seeded percentile bootstrap) |
| `compare.existence` | Per existence predicate (e.g. *some grade ≥3 adverse event*, *a TP53 mutation*): the proportion per cohort over units for which it is known, with a Wilson score interval without continuity correction; risk difference (Newcombe hybrid score interval) and risk ratio (Katz log interval, not estimable when either numerator is 0) versus the reference; Fisher's exact test for two cohorts, chi-squared for more |
| `survival.km` | Per cohort: Kaplan–Meier curve with log-log (Greenwood) pointwise intervals; median, defined as the smallest time at which the curve is below 0.5, or, where it equals 0.5 (to within 1e-9) over an interval, that interval's midpoint (as R's `survival`), with a Brookmeyer–Crowley interval on the log-log scale; landmark survival at requested times, with intervals. Between cohorts: k-sample log-rank test; difference in medians versus the reference, with a percentile bootstrap interval (resampling within cohort, 2000 replicates; a replicate whose median is not reached counts as +∞; a bound is not estimable when more than 2.5% of replicates are unreached on its side); unadjusted hazard ratio from a Cox fit, tested for proportional hazards as in `survival.cox`. Delayed entry comes from the endpoint's `entry` (§5.8) |
| `survival.cox` | One joint model: cohort membership (versus the reference) and covariates (at most 8 parameters after dummy coding; columns or predicates, one value per unit), Efron ties, Wald intervals; categorical covariates dummy-coded against their most common level among complete cases (ties broken by the smallest canonical value); optional stratification (by a column or by dataset); complete cases only, with exclusions counted by reason. Proportional hazards: the Grambsch–Therneau score test as in R's `survival` ≥ 3.0 (`cox.zph`, `transform = "km"`), global test; `PH_VIOLATED` when p < 0.05 |

---

## 10. Domain packs

### 10.1 What a pack is

A pack is a Python package with a manifest (`id`, `version` as a PEP 440 version,
`results_version`, required core version) that registers any of:

| Extension point | Example (oncology pack) |
|---|---|
| **Concepts** | `onco:overall_survival`, `onco:oncotree_code`, `onco:origin.first_sequencing` |
| **Ontology systems** | OncoTree, HGNC, NCIt, LOINC validators |
| **Descriptor extensions** (a JSON Schema per descriptor kind) | `reference_genome` on datasets; `assay`, `variant_scope`, `value_semantics` (CNA encoding, z-score reference population) on tables |
| **Importers** (a file, directory or archive in, confined as in §14; tables with roles, relationships, coverage and descriptors out) | cBioPortal study directories |
| **Validators** (run in the validation gate, §13.2) | The cBioPortal validator's checks |
| **Curation proposers** | Propose an endpoint from `OS_MONTHS` / `OS_STATUS` pairs |
| **Leaf kinds** (a JSON Schema, a compiler as in §7.3, summary templates) | `onco.genomic` (OQL subset: `MUT`, `MUT=<change>`, classes, `AMP`, `HOMDEL`, `FUSION`) |
| **Document translators** (another query format in, an aibi document and flagged differences out, used by `validate_document`) | cbio-lab documents |
| **Analyses** | `onco.alteration_frequency` (a specialisation of `compare.existence` over genes); later `onco.oncoprint` |
| **Requirement predicates** over the pack's extension fields, for registry `requires` | *has a mutation table with a declared reference genome* |
| **Catalogue facets**, computed from descriptors | Cancer type, assay |
| **Caveats**: codes with severities, rules that add them to results from descriptors, and wording for core caveats in the pack's domain | `onco.COVERAGE_ASSUMED_WES`; `LIFT_DIFFERS` explained as cBioPortal's *profiled in any sample* convention |

- `results_version` is an integer bumped whenever a change to the pack could change outputs:
  leaf expansions, caveat rules or severities, or pack analyses. It is hashed into ids (§7.6),
  so a pack upgrade that changes nothing changes no id.
- Packs do not add MCP tools. A visual output such as an oncoprint is an analysis whose values
  are a render specification, so it stays inside the registry and the result contract.
- Packs live in this repository (`aibi/packs/`) until the extension points are stable, then
  move to separate packages so that other groups can publish their own.
- Packs MUST NOT patch the core. If a pack needs something the extension points do not offer,
  the extension point is added to the core in its own change, with a domain-neutral test.

### 10.2 How cBioPortal maps onto the core

This mapping is the working test of P8:

| cBioPortal | aibi core |
|---|---|
| Study | Dataset |
| Patient, sample | Entity tables; `samples.patient_id → patients.patient_id` |
| Clinical attributes (with the four header rows) | Columns, with `label`, `definition`, `datatype` imported |
| Mutation, CNA, structural-variant data | Measurement tables below `samples` (CNA as a long table of non-neutral calls with its own coverage; structural variants with one gene per row) |
| Case lists, gene panel matrix, panel definitions | Grouped coverage: sample → panel (assignment), panel → genes (groups); whole-exome and whole-genome panels cover all genes |
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

## 11. Tool and operator surfaces

### 11.1 MCP and HTTP tools

One set of Python functions backs both the HTTP API (FastAPI) and the MCP server. Tool schemas
are generated from the same Pydantic models as the HTTP API. Packs contribute leaf kinds,
analyses, translators and facets to these tools; they do not add tools of their own.

| Tool | Purpose | Touches row data? |
|---|---|---|
| `search_catalog` | Faceted search over the latest published release of each dataset: domain tags, tables with their grains and roles, concepts present, row counts, completeness thresholds, data-use codes, pack facets | Catalogue statistics only |
| `describe_dataset` | Dataset descriptor, table graph, columns, coverage, endpoints, applicable analyses, and standing caveats (those any query on the dataset would raise from its descriptors: unconfirmed fields, proposed coverage) | Catalogue statistics only |
| `describe_column` | Full descriptor with observation-state counts and value distribution | Catalogue statistics only |
| `list_analyses` | Registry entries, optionally filtered by applicability | No |
| `validate_document` | Canonicalise, check, expand pack leaves (or translate another format) and read back a document without running it; returns errors, the caveats that can be determined without data, and ids. Records no issuance | No |
| `count_cohort` | Evaluate a document's cohorts; returns cohort counts (§8.1) | Counts only |
| `run_analysis` | Run a document; returns one result envelope per view (§8) | Yes |
| `explain` | Given a derivation id or an issuance id: canonical document, releases, versions and, for an issuance, the document as written and the SQL as run (§12.2) | No |
| `curation_queue` | Keys, relationships, roles, coverage and descriptor fields that are `undeclared`, `imported_default` or `proposed`, and pending proposals; rows with invalid values appear as counts with a document that selects them | No |
| `propose_descriptor` | Record a proposal for any of those, with rationale, in the queue (§12.3) | No |

- Tool descriptions MUST state that `warn` and `block` caveats and non-zero `n_unknown` have to
  be shown to the user, that every number quoted must cite its id or reference (A1), and that
  data-wrapped text in outputs is data, not instructions (A6).
- Unit keys are listed only by the `summary.members` analysis (§9.5), subject to §8.4. The
  disclosure settings apply to every tool.
- Every descriptor is also an MCP resource: `aibi://dataset/<id>@<label or manifest
  hash>/<descriptor id>`.

### 11.2 Operator operations

Some operations are reserved for people and are never MCP tools:

- importing and re-importing datasets (§12.3, §13.1);
- opening, editing, publishing, discarding and taking over curation sessions, and accepting or
  rejecting proposals (§12.3);
- withdrawing releases (§12.2);
- managing named database connections and model cards, which live in server configuration.

They are served by a separate operator router over HTTP and by an operator CLI; the MCP
transport never mounts that router. Every operator request carries the deployment's curator
token, set in server configuration (there are no user accounts in v1), and names the operator it
acts for (self-declared, recorded in the audit trail, Q7). The operator router accepts only
same-origin requests (Origin and Host checks, a CSRF token for browser forms) and has no side
effects on GET. The server binds to localhost by default; exposing it on a network is an
explicit configuration choice, and the MCP HTTP transport validates Origin as the MCP
specification requires. M1 ships the CLI; the web UI's curation screens (M5) use the operator
router.

---

## 12. Architecture

### 12.1 Components

```
   Web UI (React + TS)        External agents (Claude, …)        Operator CLI
        │ HTTP                        │ MCP                           │
        ▼                             ▼                               ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  aibi server (Python, one process plus worker processes)             │
   │                                                                      │
   │  api/ (FastAPI)          mcp/ (MCP SDK)          operator/ (router)   │
   │         └──────────────┬────────┘                      │             │
   │                  service functions  ◀──────────────────┘             │
   │   ┌────────────┬───────┴──────────┬───────────────┬────────────┐     │
   │   catalog      engine              analyses        assistant    store │
   │   (descriptors (reference          (registry,      (Claude API, (blobs,│
   │    & search)    evaluator,          lifelines,      MCP tools    releases,│
   │                 canonicaliser,      scipy, in a     only)        sessions,│
   │                 SQLGlot→DuckDB)     worker)                      log)  │
   │                                                                      │
   │   importers (files, databases) · extension points · packs/onco       │
   └──────────────────────────────────────────────────────────────────────┘
```

### 12.2 Storage

- **Blobs.** Every piece of release content is an immutable blob stored at
  `data/blobs/<sha256>`: the raw snapshot of each source table, the typed table data, the
  definitional descriptors, the computed statistics and the manifest itself.
- **Releases.** A release is a manifest: the list of its blobs, by role and hash, serialised with
  RFC 8785; the manifest's hash identifies the release. Labels (`@n`, `@draft`) and release
  status live in the app DB. Releases are never modified.
- **Raw snapshots and rebuilds.** The raw snapshot keeps each cell as the source gave it (the
  original strings for files, the source-typed values for databases). Typed values and
  observation states are a deterministic function of the raw snapshot and the descriptors. A
  draft change to a field that affects parsing (missing codes, datatypes, list syntax,
  delimiter, header rows, encoding, derived columns) rebuilds the affected tables from the raw
  snapshot; other descriptor changes reuse the existing table blobs.
- **Observation states.** Every column that has missing codes or null cells has a companion
  column `<column>__state` holding each cell's state; the value column holds a value only where
  the state is PRESENT. A list column has a parallel list of item states.
- **Withdrawal and deletion.** Discarding a draft and withdrawing a release remove labels, not
  files. A blob is deleted only when no remaining (not withdrawn, not discarded) release
  references it. Withdrawing a release deletes the raw-snapshot, table-data and statistics blobs
  that only it references, keeps its manifest and definitional descriptors, purges its cached
  results, and makes its ids resolve to *withdrawn*. Honouring an erasure request means
  publishing a corrected release without the person's rows and then withdrawing every earlier
  release that references the affected blobs.
- **Derivation log.** Two append-only tables. *Derivations*, keyed by derivation id, hold the
  canonical document, the releases and the versions hashed into the id; they are kept
  permanently. *Issuances*, keyed by issuance id, record each time `run_analysis` or
  `count_cohort` produced an output: the derivation id, the document as written, the SQL as run,
  the engine and pack versions and a timestamp. `validate_document` records nothing. Issuances of
  `count_cohort` may be pruned after a configured period. When a release is withdrawn, unit keys
  in the log (in `ids` leaves, in constants on identifier columns, in parameters and in bound SQL
  parameters) are redacted; that is the log's only permitted mutation.
- **App DB (SQLite).** The catalogue index (a denormalised copy of descriptors for search),
  release labels and statuses, curation sessions and their audit trail, the proposal queue,
  saved documents, the result cache (evictable) and the derivation log.
- **Query engine.** DuckDB reading the release's table blobs, configured as in §14. The compiler
  builds queries as SQLGlot expression trees and never concatenates identifiers or values into
  strings; every identifier comes from a descriptor.

### 12.3 Release lifecycle and curation sessions

- **Import.** Importing a dataset publishes its first release, `@1`, with the importer's
  proposals in its descriptors as `proposed` or `imported_default` fields. Re-importing
  (re-snapshotting) publishes a new release that carries descriptors forward by id: fields whose
  table, column and datatype are unchanged keep their values and statuses; new or changed fields
  get the importer's proposals. Import and re-import are refused while a session is open on the
  dataset.
- **Proposals.** `propose_descriptor` adds a proposal to the queue. It changes no release; it
  enters a draft only when an operator accepts it.
- **Sessions.** A dataset has at most one open curation session. Opening one creates a draft
  release, labelled `@draft`, that starts as a copy of the latest published release. Any
  operator may take over an open session. Changes to the draft are applied one at a time; each
  re-runs the structural checks of §13.2 and is refused, with counts, if they fail; each yields a
  new manifest hash.
- **Queries against drafts.** Only documents that pin `@draft` read the draft. Their results
  carry `DRAFT_RELEASE` and are not cached.
- **End of a session.** **Publish** turns the draft into `@n+1`; **discard** removes it.
  Afterwards, the ids of intermediate drafts resolve to *discarded*. Every change is recorded in
  the audit trail with the operator's self-declared name (Q7).

### 12.4 Stack

| Layer | Choice |
|---|---|
| Language and tooling | Python ≥ 3.12, uv, ruff, pyright (strict on `core/`), pytest, hypothesis, import-linter |
| Schemas | Pydantic v2 as the source of truth; JSON Schema and OpenAPI generated from it |
| API and MCP | FastAPI; the official MCP Python SDK |
| Data | DuckDB (CSV, Parquet, and Postgres, MySQL, SQLite and DuckDB sources), python-calamine (XLSX, XLS, ODS), Parquet, SQLite |
| Statistics | lifelines, scipy, statsmodels, in worker processes |
| Assistant | Claude API, calling the same MCP tools |
| Charts | Vega-Lite specifications generated on the server (§8.5) |
| Frontend | React + TypeScript + Vite, types generated from OpenAPI; renders Vega-Lite with a CSP-safe interpreter, never charts from model text |
| Deployment | One server process with worker processes on a lab server, bound to localhost by default; no user accounts in v1; the same package runs locally |

### 12.5 Repository layout

```
aibi/
  SPEC.md  CLAUDE.md  README.md
  server/
    pyproject.toml
    src/aibi/
      core/
        schema/      # Pydantic models: identifiers, descriptors, documents, results, caveats, reasons, pack API
        store/       # blobs, releases, raw snapshots, rebuilds, withdrawal, sessions, app DB, derivation log
        importers/   # files and database snapshots
        catalog/
        engine/      # reference evaluator; canonicaliser; SQL compiler
        analyses/    # registry and core analyses
        api/  mcp/  operator/  assistant/
      packs/
        onco/        # concepts, descriptor extensions, cBioPortal importer and validator, onco.genomic, analyses
    tests/
      core/          # runs with no pack registered
      packs/onco/
  web/
  fixtures/          # small public datasets: at least one non-biomedical, one spreadsheet, one cBioPortal study
```

---

## 13. Import, validation and testing

### 13.1 Generic import (core)

- **Files:** CSV/TSV (delimiter, header row, skip rows and encoding detected, then recorded as
  `imported_default`), XLSX/XLS/ODS (each non-empty sheet a table; empty sheets are skipped and
  noted in the curation queue), Parquet.
- **Databases:** Postgres, MySQL, SQLite and DuckDB sources, through named connections from
  server configuration (§14). All tables are read in one consistent snapshot (one transaction)
  so that keys stay consistent. Declared primary and foreign keys and column comments are
  imported with status `imported`; only base tables are read.
- **Names:** table and column names are normalised to identifiers (§5.1); names that collide
  after normalisation, or end in the reserved `__…`, are renamed with a note in the curation
  queue.
- **Proposals**, for files and databases alike: grain, table roles, primary keys, relationships
  (by value containment, as biai's foreign-key detector did), datatypes, list columns, missing
  codes, identifier columns, and coverage for `entity` and `link` tables (§5.6). Everything
  proposed lands in the curation queue; nothing proposed is `asserted`.

### 13.2 Validation as a gate

The gate runs at import and on every change to a draft (§12.3). Structural errors stop the
import or refuse the change: unparseable files, duplicate or null primary keys, violations of
one-to-one cardinality, foreign keys that reference missing parent rows, coverage, assignment
and group tables that reference unknown parents or groups, and identifiers that collide. For
proposed rather than declared keys, the proposal is dropped with its evidence instead. Semantic
gaps do not stop the import; they become `undeclared` or `imported_default` fields in the
curation queue: missing units, undeclared coverage, unknown missing codes, child rows outside
their parent's listed coverage (kept, since they are evidence, §6.5), and endpoint rows with
invalid values (§5.8). Packs add validators (§10.1); the oncology pack's mirror the cBioPortal
validator.

### 13.3 Reference evaluator

The reference evaluator is a pure-Python, row-by-row implementation of §6 and of
canonicalisation (§7.6), with no SQL. It is written before the SQL compiler (M2) and is the
executable definition of the semantics. The SQL compiler is tested against it: property tests
(hypothesis) generate small random datasets, with random coverage of every form (direct,
grouped, scoped), parent scopes that evaluate to TRUE, FALSE and UNKNOWN, missing codes of every
kind, list items, and null and dangling keys, together with random documents, and require
identical truth values, reasons and counts.

### 13.4 Tests that encode the principles

- **Three-valued logic:** for any cohort predicate `C`, `n_true(C) + n_false(C) + n_unknown(C)`
  equals the size of the unit table; `not(not C) ≡ C`; `C` and `not C` never share a unit;
  `known(C)` equals `C ∪ not C`; a direct reference below the unit equals its nested `exists`.
- **Scenarios of §6.5**, each as a test: unassessed samples, missing grades, blood normals,
  patients with no samples, participants with no enrolments, `every` over no rows, `every` with
  scope columns, `min_count`, UNKNOWN parent scopes under coverage `all`, scope columns
  mentioned inside `any` (refused), deeper paths under `assessed`.
- **Canonical form:** no user-chosen name survives phase 1; random renames, reordered top-level
  cohorts, reordered object keys and equivalent syntax (`!=` versus `not` on single-valued
  leaves, `op` versus `range`) give identical ids **and identical digests**.
- **Refusals**, each asserting that the error lists the alternatives: unknown column, constant
  outside permissible values, unconvertible units, out-of-filter existence query, scope columns
  mentioned outside top-level `values`, ambiguous path, unmapped cross-dataset reference (in a
  cohort and across a view's cohorts), mixed releases of one dataset, unmet analysis
  requirement, overlapping cohorts for a test, a missing aggregate for a multi-valued column, an
  open scope for an aggregate, `ids` or identifier predicates without row-id access.
- **Digests:** golden documents with checked-in ids and digests; CI fails if either changes
  without a version bump. Repeated runs with different thread counts give identical digests.
  Swapping a view's reference changes the result id and inverts the hazard ratio (M3).
- **Statistics:** KM curves and medians, log-rank, Cox (Efron) and the Grambsch–Therneau test,
  Fisher, chi-squared, Welch, Mann–Whitney, Welch's ANOVA, Kruskal–Wallis, Wilson, Newcombe,
  Katz and BH values agree with reference outputs from R (`survival`, `stats`, `exact2x2` or
  equivalent), computed once and checked in; bootstrap intervals are checked for determinism and
  coverage on simulated data.
- **Reference evaluator versus SQL compiler:** the differential property tests of §13.3.
- **Provenance:** `explain` works for an id after the result cache is cleared; withdrawn and
  discarded releases' ids resolve accordingly; withdrawal deletes no blob a remaining release
  references.
- **Disclosure:** each rule of §8.4, on results, cohort counts and catalogue statistics.
- **Security:** path confinement, archive entry checks, refusal of views in database files,
  operator routes refusing requests without the curator token or from another origin (§14).
- **Domain boundary:** an import-linter contract forbids `aibi.core` → `aibi.packs`; the core
  suite runs with no pack registered and includes a non-biomedical fixture.
- **Assistant evals:** questions the assistant must answer through the tools, citing ids and
  surfacing required caveats, including prompt-injection attempts in column descriptions,
  permissible-value labels, notes and cell values.

---

## 14. Security and privacy

- **Trust model (v1).** Operators, who hold the curator token, are trusted. Everything else is
  not: imported files and databases, shared documents and links, and every request from an MCP
  client or agent.
- **Operator surface.** §11.2.
- **Untrusted text (A6).**
  - Data-derived text is carried as marked data (§8.1), rendered as plain text, never as
    markdown or HTML, and never placed in tool names, descriptions or schemas.
  - Readbacks and messages are segment lists in which data tokens are quoted, escaped and
    length-capped (§7.7).
  - Charts follow §8.5.
  - The UI renders assistant output without remote resources (a content security policy that
    allows images and connections to the server only).
  - Assistant evals include injection attempts (§13.4).
- **Queries.** Documents are validated against their schemas. No SQL is accepted. SQL is built
  from SQLGlot trees with identifiers taken from descriptors and constants passed as bound
  parameters.
- **Imports.**
  - Every file that any importer, core or pack, opens MUST resolve, after following symlinks,
    inside the upload area or a configured import directory. URLs and globs are refused.
  - Archives are checked entry by entry (no absolute paths, no `..`, no links out) and are
    subject to size and decompression-ratio limits.
  - From SQLite and DuckDB files only base tables are read, never views or triggers.
  - DuckDB runs with external access disabled except for allowed directories, with extension
    auto-install and auto-load disabled (the extensions it needs are pinned and bundled), and
    with its configuration locked.
  - Database sources are named connections in server configuration; a request can name a
    connection but never supply a host or credentials. Credentials never appear in descriptors,
    releases, logs or results; provenance records host, database and schema only.
  - Storage paths are built only from hashes and validated identifiers (§5.1).
- **Resource limits.** Request bodies, strings, lists (e.g. at most 10,000 members in a `values`
  or `ids` list), notes and parameters have size limits. Imports have size and
  decompression-ratio limits. Every tool call has a wall-clock limit that covers the analysis
  stage, enforced by running queries and analyses in worker processes that can be killed; each
  worker has a DuckDB memory limit. Categorical levels per analysis (at most 150) and resampling
  replicates are capped. MCP clients are rate-limited, and the number of open proposals is
  capped. Every refusal names the limit it hit.
- **Disclosure.** §8.4, including its limits.

---

## 15. Milestones

The order is agent-first: from M1 an external agent (Claude over MCP) is the primary interface,
and the web UI follows once the semantics are settled. A thin read-only page (catalogue and
dataset descriptors) ships with M1 so there is something to show without an agent.

| # | Deliverable | Exit criterion |
|---|---|---|
| **M0** | Repo skeleton and CI; Pydantic schemas for identifiers, descriptors (including analysis entries and model cards), documents, results, caveats, reasons and the pack API; JSON Schema export; import-boundary check | Schemas published; CI runs lint, type check, boundary check and tests |
| **M1** | Store (blobs, manifests, raw snapshots and rebuilds, state columns, labels, withdrawal); generic importers (files and database snapshots) with proposals and the validation gate, hardened as in §14; release lifecycle and curation sessions; operator CLI with the curator token; catalogue with citable statistics; `search_catalog`, `describe_dataset`, `describe_column`, `curation_queue`, `propose_descriptor`, MCP resources; read-only catalogue page | biai's example spreadsheets and a non-biomedical dataset imported, curated through the CLI to confirmed keys and relationships, and published; an MCP client can find and describe them; a release can be withdrawn without deleting shared blobs |
| **M2** | Reference evaluator first; then paths, canonicalisation, ids, derivation log, three-valued SQL compilation with coverage, scope and lift, core leaves, readbacks, `validate_document`, `count_cohort`, `explain` | Scenario and property tests pass on the reference evaluator; the SQL compiler matches it in the differential tests; the canonical-form tests pass for ids and cohort-count digests |
| **M3** | Registry and the core analyses of §9.5, with aggregation rules, estimability checks, determinism and disclosure rules; `list_analyses`; `applicable_analyses`; `run_analysis`; charts | Golden, R-reference, determinism and disclosure tests pass, including the reference-swap test |
| **M4** | Oncology pack: cBioPortal importer and validator, grouped coverage from panels, parent scope for normal samples, `onco.genomic`, `onco.alteration_frequency`, concepts, endpoint proposer, cbio-lab document translator | TCGA GBM PanCan and one panel study imported. From cbio-lab example 1 on `msk_chord_2024`, the altered-percentage comparison and the survival view are reproduced, plus a mutation-only wild-type comparison written with cbio-lab's `profiled` clause; numbers match where semantics agree, and every difference is explained in `docs/cbio-lab-differences.md`. **No core change in the pack's PR** |
| **M5** | Web UI: catalogue, dataset page with table graph and applicable analyses, cohort builder with live counts including unknowns, results with provenance panel; curation, import and withdrawal screens on the operator router | The in-scope subset of biai's e2e scenarios, listed in `docs/ui-scenarios.md` (dashboards and map charts excluded), passes |
| **M6** | Assistant (chat that edits the document); AI-proposed descriptors, keys, relationships, roles and coverage; concept mappings and cross-dataset queries | Assistant evals pass, including the injection cases; a mapped two-dataset survival comparison, with both cohorts present in both datasets and declared time origins, runs with a stratified log-rank |
| **M7+** | Clinical-trials pack; event tables and time-window leaves using observation windows; re-anchored survival; `onco.oncoprint`; driver annotation (onco); JSON-LD / Bioschemas export | Set when M6 lands |

---

## 16. Open questions

| # | Question | Current leaning |
|---|---|---|
| Q1 | Should aibi converge with cbio-lab (one engine, one DSL) rather than sit beside it? | Keep documents compatible and ship the translator; decide after M4, when the oncology pack can be compared with cbio-lab on the same studies |
| Q7 | Who may confirm proposals? | In v1, whoever holds the curator token, under a self-declared name recorded in the audit trail. Named curators (and therefore user accounts) are a precondition for any public deployment |
| Q9 | Who are the first users: this lab only, or outside groups? | Assumed this lab only. Outside users bring user accounts and Q7's named curators forward |

---

## Appendix A. Decisions

Decisions from the walkthrough (D1–D19) and the review rounds (D20–D109), all 2026-09-24. Each
line records the choice and the reason; reopening one means changing this table. Later decisions
that revise or refine earlier ones say so.

| # | Topic | Decision | Reason |
|---|---|---|---|
| D1 | Model and numbers (A1) | Strict: the model only quotes numbers from cited results; the engine computes the comparisons people ask for, with CIs | A plausible wrong number is the failure nobody catches; a bare "2×" without an interval shouldn't be trusted anyway |
| D2 | Cross-dataset comparisons (A3) | Refused by default; an explicit opt-in marks every affected result | The assistant turns a refusal into a next step (map the columns), and warnings are easy for agents to skip |
| D3 | Scope | Timeline queries after v1, with observation windows in the v1 schema; Cox regression in v1; JSON-LD and federation after v1 | Timelines are most of the missing-data difficulty; Cox is cheap once survival exists, and unadjusted survival comparisons are weak evidence |
| D4 | Curation releases (refined by D63, D79) | Confirmations are batched into one release per curation session | Keeps P6 without a release per click |
| D5 | Coverage defaults (revised by D43, D68, D100) | Importer proposes coverage for obvious cases; open-scope existence answered with `SCOPE_PARTIAL`; scope columns equality-only in v1 | Datasets are useful at once and still honest; matches what cBioPortal users expect |
| D6 | Concepts (extended by D86, D87) | Small `core:` set plus pack vocabularies anchored to NCIt and LOINC; exact mappings; transforms limited to units and value maps; other derivations as declared columns | Keeps comparisons meaningful and every derivation visible |
| D7 | Table graph (revised by D60, D109) | Many-to-one and one-to-one only (many-to-many through a linking table); up-then-down paths allowed with explicit readbacks; units need a primary key | Every step is a lookup or an existence question |
| D8 | Three-valued logic (revised by D66) | NOT_APPLICABLE is FALSE for value predicates; unknown counts always shown when above zero; differences from the cBioPortal convention explained by caveat and readback, not by a second number | Matches plain meaning; makes P2 visible; two numbers invite choosing the convenient one |
| D9 | Lifting (was Q2; refined by D44, D72, D101) | Strict by default; parents outside scope ignored; `lift: "assessed"` opt-in; the affected count always reported | Honest where it matters (an unassessed metastasis), without noise from blood normals or failed samples |
| D10 | Document shape | cbio-lab-compatible, with a translator that flags `not`; caps kept; cohort references added | The most common comparison is X versus not-X within a base, which is easy to get wrong by hand |
| D11 | Derivation ids (refined by D99) | Engine version and cohort names excluded from hashes; ids designed to be citable, with no availability promise in v1 | Ids stay stable for caching and citation; version bumps carry result changes |
| D12 | Results (revised by D59, D94) | `TIME_ORIGIN_MISMATCH` blocks; `SMALL_N` warns; SQL only through `explain`; a `min_cell_count` setting, off by default | Comparing different clocks is meaningless; small groups are imprecise but informative |
| D13 | Analyses (refined by D67, D93) | `survival.km` includes the unadjusted HR; Cox always tests proportional hazards and warns on failure; only core and packs register analyses | "How much worse" always follows "is it worse"; user-uploaded analyses would skip the golden-test discipline |
| D14 | Packs (was Q8) | No pack MCP tools (visual outputs are analyses returning render specifications); packs in this repo until stable; a second pack right after v1 | Keeps A4 auditable and everything inside the registry |
| D15 | MCP (refined by D56, D59, D73) | Confirmation stays with people; `count_cohort` added; row-level ids controlled per dataset by `allow_row_ids` | An agent can't confirm its own proposal; counting first is the most common agent step |
| D16 | Deployment and scale (were Q3, Q4) | Lab server without user accounts, also runnable locally; up to ~100k units and ~10M data rows per dataset | Fits TCGA- and MSK-scale studies on one machine |
| D17 | Charts (refined by D82) | Vega-Lite specifications generated on the server with each result | The UI and agents draw the same figure |
| D18 | Order | Agent-first with an early read-only page; oncology pack before the UI; built-in assistant at M6 | External agents cover the AI-native use from M1; the P8 test happens while the core is cheap to change |
| D19 | Concept ownership, live databases (were Q5, Q6; refined by D79) | As D6; snapshots only, with re-snapshots creating releases | Keeps P6 |
| D20 | Reference group (refined by D45, D46) | Effect sizes use an explicit reference, defaulting to the first view cohort and written into the canonical form; view cohort order is kept | Otherwise reordering cohorts inverts a hazard ratio without changing its id |
| D21 | Cohort references | Resolved to the referenced cohort's canonical form | Names are not hashed, so references must not depend on them |
| D22 | Digest stability (revised by D53, D95) | Values rounded before hashing; returned unrounded | Parallel aggregation and optimisers are not bit-reproducible |
| D23 | Suppression in derivations (revised by D59, D94) | `min_cell_count` is part of the result derivation | A setting that changes outputs must change the id |
| D24 | Row ids (revised by D59, D78) | With `allow_row_ids: false` the `ids` leaf is refused | A count over a chosen id reveals that unit's attributes |
| D25 | Overlapping cohorts (revised by D62, D92) | Refused in a view unless `overlap: "allow"`, then `COHORTS_OVERLAP` | Tests assume independent groups |
| D26 | Structural coverage (revised by D43) | Importer proposes `parents: "all"` for every relationship | Otherwise every negative criterion over a child table is UNKNOWN on a fresh import |
| D27 | Record filters | Allowed-value lists on categorical columns; queries accepted only if provably inside; readbacks state the filter | Keeps the refusal rule decidable and the meaning of "any row" visible |
| D28 | Empty quantifiers | `every` over no rows is UNKNOWN; `min_count` is three-valued | Vacuous truth would put units into cohorts on no evidence |
| D29 | Coverage table without scope | Closed if the parent is listed, otherwise not | Closed a missing case |
| D30 | Draft releases (refined by D57, D63, D79) | Queries during a curation session can use a draft release, carry `DRAFT_RELEASE`, and are neither cached nor citable | Keeps D4 compatible with P6 |
| D31 | Entity concepts (extended by D61) | Tables may map to a table concept; required for cross-dataset units | Cross-dataset units needed a mapping |
| D32 | Caveat severities | Every caveat, core or pack, declares a severity | "Show warn and above" must be unambiguous |
| D33 | Missing child values | Existence is three-valued over rows; a row whose `where` is UNKNOWN makes the answer UNKNOWN, not FALSE | Otherwise a missing grade counts as "no grade ≥3 event" |
| D34 | Canonicalisation order (revised by D108) | Cohort references inlined before sorting | Sorting before inlining lets names into the id |
| D35 | Result keys | Values keyed by position in the view, each position carrying its cohort id | Two identical cohorts would otherwise collide |
| D36 | View cohort lists (revised by D46) | Default is every cohort in document order | The default reference group would otherwise be undefined |
| D37 | `quantifier` versus `lift` (refined by D69) | The quantifier (per step) and the lift rule (unassessed children only) are separate | One parameter was doing two jobs |
| D38 | Dependency upgrades | A library upgrade that changes a golden digest requires a version bump | Keeps the id-digest invariant true across upgrades |
| D39 | Record-filter wording | Unconstrained columns are accepted and implicitly restricted to the filter | Removed a contradiction |
| D40 | Row ids and suppression (revised by D59) | `allow_row_ids: false` requires `min_cell_count` | Narrow cohorts re-identify people without it |
| D41 | Draft release ids (revised by D57) | `<dataset>@draft-<content hash>`, valid in derivations, never citable | D30 needed an identifier |
| D42 | Null foreign keys (extended by D80) | Lookups through a null key are UNKNOWN, reason `NO_PARENT` | Closed a gap |
| D43 | Coverage proposals (revises D26; refined by D68) | Proposed `parents: "all"` only for structural child tables; measurement and event tables stay undeclared; `COVERAGE_PROPOSED` otherwise | A plain-CSV panel import must not read unsequenced genes as wild-type |
| D44 | Lifting and empty sets (refined by D101) | At an intermediate step, children are dropped by reason, and an empty set of remaining children is UNKNOWN, never FALSE | Otherwise patients with no samples, or with only unsequenced samples under `assessed`, were counted as wild-type |
| D45 | Names in canonical forms | No user-chosen name survives canonicalisation | Names had leaked into ids three times |
| D46 | Cohort order (revises D36) | Analyses with a reference group require an explicit `views[].cohorts` array; otherwise the default is every cohort ordered by id | JSON object order is not reliable; JavaScript reorders integer-like keys |
| D47 | Views across datasets | A view whose cohorts come from different datasets follows the cross-dataset rules | Closes a route around P3 |
| D48 | `!=` (revised by D103) | Canonicalised to a negated membership | `!=` and `not =` gave different answers on NOT_APPLICABLE |
| D49 | Units in criteria | Numeric criteria carry `units`, defaulting to the column's (or concept's) and always shown in readbacks | "age > 60" on a column in days meant 60 days |
| D50 | Several values per unit (refined by D89) | Tests require a per-unit aggregate; descriptive views count units per category, or rows when labelled | Rows of one unit are not independent observations |
| D51 | One definition per rule | Existence and coverage are defined once, as an algorithm; the reference evaluator is its executable form | Two sections had drifted apart |
| D52 | Digest scope (refined by D77) | The digest covers cohorts, population, values and caveats | Cohort sizes and caveats could change unnoticed |
| D53 | Determinism (revises D22; revised by D95) | Order-independent sums, deterministic fits, seeded resampling; rounding as a second line | Rounding alone still flips values near a boundary |
| D54 | Derivation log and withdrawal (refined by D74, D84) | A permanent log of every id; releases can be withdrawn, keeping their identity | `explain` must work after cache eviction; consent withdrawal and erasure must be possible |
| D55 | Pack leaves (refined by D83, D107) | Ids hash the core expansion | Same meaning, same id |
| D56 | Citable counts (refined by D99) | `count_cohort` returns cohort ids; catalogue statistics carry release-scoped references | A1 requires a citation for every number |
| D57 | Release identity (revises D41) | Hashes use manifest hashes; `@n` and `@draft` are labels | Deployments can't mint the same id for different data; a draft published unchanged keeps its ids |
| D58 | Untrusted text (extended by D82) | Principle A6; credentials never stored in descriptors, releases, logs or results | Imported text and shared notes reach the assistant |
| D59 | Disclosure (revises D12, D23, D24, D40; refined by D78, D94) | Settings live in the dataset descriptor (hence the release); a deployment floor is hashed; suppression applies to every tool; `summary.members` lists unit keys; described as risk reduction, not protection | Per-result suppression cannot stop differencing across queries |
| D60 | Paths (revises D7; refined by D109) | Any path that visits no table twice, up or down; direct references are shorthand for nested `exists` | Many-to-many links need down-then-up steps |
| D61 | Cross-dataset queries (refined by D91, D105) | Table-concept units, concept references throughout, per-dataset pack expansion, registry-declared cross-dataset methods | The previous rules covered only value leaves |
| D62 | Overlap (revises D25; revised by D92) | Refused only for analyses that assume independent groups | Side-by-side descriptive views of a base and a subset are common and harmless |
| D63 | Curation sessions (refines D4, D30; refined by D79) | One open session per dataset, ended by an explicit publish or discard | Without accounts, two people could otherwise publish conflicting drafts |
| D64 | Observation-state storage (refined by D76) | A companion state column for each column with missing codes or nulls | A numeric column cannot hold `NA`, `N/A` and `Not done` distinctly |
| D65 | Unconfirmed semantics | `UNCONFIRMED_SEMANTICS` replaces `DEFAULT_SEMANTICS` and also covers undeclared fields | Undeclared units or time origins affected results silently |
| D66 | Lift caveat (revises D8) | `LIFT_DIFFERS` replaces `CONVENTION_DIFFERS`; packs supply domain wording | Core caveats must not name a domain (P8) |
| D67 | Survival details (refines D13; refined by D93) | The HR inside `survival.km` is tested for proportional hazards; medians not reached are *not estimable* | D13 applies to every hazard ratio; nothing is extrapolated |
| D68 | Table roles (refines D43) | Tables carry a proposed, confirmable `role`; coverage proposals follow it | A heuristic hidden in the importer would misclassify some tables invisibly |
| D69 | Quantifier names (refines D37) | `some` and `every` | `all` was both a combinator and a quantifier |
| D70 | Milestone exits | M4 reproduces only v1-feasible cbio-lab views and explains every difference; M5 lists its in-scope scenarios | The previous criteria could not be met |
| D71 | Reference evaluator | The executable definition of the semantics from M2; the SQL compiler is differential-tested against it | Prose review kept missing interactions between rules |
| D72 | Parent scope (refines D9; refined by D102) | Outside a table's parent scope, the question is UNKNOWN (`OUT_OF_SCOPE`); an UNKNOWN parent scope counts as in scope | Normal samples belong in neither the mutated nor the wild-type group |
| D73 | Operator surface | Import, sessions, acceptance, withdrawal and configuration are operator operations on a separate router and CLI, behind a curator token, same-origin only, no side effects on GET, bound to localhost by default | Without it, "an agent can never confirm its own proposal" was unenforceable, and any web page could withdraw a local release |
| D74 | Blob store | Releases are manifests over content-addressed blobs; discard and withdrawal remove labels and delete only unreferenced blobs; erasure publishes a corrected release and withdraws the earlier ones | Shared files made discard and withdrawal delete data other releases still use |
| D75 | Import hardening | Path confinement for every importer, no URLs or globs, archive checks, base tables only from database files, hardened DuckDB, named connections only, paths built from hashes | Untrusted files could otherwise read server files or reach the network |
| D76 | Raw snapshots (refines D64) | Releases keep raw snapshots; typed values and states are rebuilt from them when parsing fields change; list items have states | Curation could not re-apply a corrected missing code without the raw tokens |
| D77 | Names and prose out of digests (refines D52) | Structured denominator definitions; `unknown_by_leaf` keyed by canonical clause hashes; rendered text outside digests | Two documents with the same id produced different digests |
| D78 | Identifier columns (refines D24, D59) | An `identifier` flag; without row-id access, predicates on and distributions of identifier columns are refused; queue rows appear as counts | A `value` leaf on a key column bypassed the `ids` refusal |
| D79 | Release lifecycle (refines D4, D19, D30, D63) | Imports publish directly and carry descriptors forward; proposals wait in the queue; unpinned means latest published; drafts only when pinned; one change at a time; sessions can be taken over | Implementers would otherwise guess, and a proposal could silently change every query |
| D80 | Structural checks on curation (extends D42) | Every draft change re-runs the structural checks; dangling keys evaluate as `NO_PARENT` | An asserted key or relationship could otherwise break the data model |
| D81 | Identity scheme | Normalised identifiers, reserved `__` suffix, release-independent descriptor ids, JSON Pointer field paths, explicit coverage column maps, RFC 8785 manifests, resources for every descriptor | Ids are hashed; two implementations must derive the same ones |
| D82 | Structured text (extends D17, D58) | Readbacks and messages are segments with escaped data tokens; a data wrapper in outputs; charts inline-only without computed transforms or URLs; no remote resources in the UI | Data-derived text reached readbacks, errors and charts unmarked |
| D83 | Pack hooks and results versions (refines D55) | Validators, directory importers, leaf-compiler contract, translators, requirement predicates, facets, caveat rules and wording; grouped coverage; a hashed `results_version` per pack | Otherwise M4 could not ship without core changes, and pack caveat changes altered digests under unchanged ids |
| D84 | Derivations and issuances (refines D54) | The log separates derivations (content, permanent) from issuances (as written, SQL as run); `validate_document` records nothing | One id is issued many times with different names, SQL and versions |
| D85 | Resource limits | Byte, length, import, wall-clock, level, replicate, rate and proposal limits, with analyses in killable workers | Structural caps alone let a single request exhaust the server |
| D86 | Time origins as concepts (extends D6) | `time_origin` is a concept reference compared by id | Prose origins could never be compared, so every cross-dataset survival result would block |
| D87 | Model cards, derived columns, core concepts, analysis entries | Model cards registered in server configuration; derived columns as declared expressions; the `core:` concepts listed; analysis entries in the envelope | The M0 schemas needed them defined |
| D88 | Caveats on closedness | `SCOPE_PARTIAL` and `COVERAGE_PROPOSED` apply to every answer that depended on closedness | TRUE answers from `every`, `covered` and counts rested on partial or proposed coverage without a caveat |
| D89 | Aggregates (refines D50) | Aggregates follow parent scope and closedness, refuse open scope, handle row states explicitly, and take an `empty` value for closed units without rows; ordered categories for `max` and `min` | Unrecorded rows were treated as absent, and units with no rows silently dropped out |
| D90 | Estimability | Any number may be not estimable; no non-finite numbers; estimability checks per analysis; `SMALL_N` on units analysed, events and expected counts | Degenerate inputs produced valid-looking numbers, and infinities could not be hashed |
| D91 | Cross-dataset stratification (refines D61) | Stratified results only where a dataset holds at least two cohorts with data; `CONFOUNDED_WITH_DATASET` otherwise; pooled means ignoring dataset | A test stratified by dataset is 0/0 when each cohort comes from one dataset |
| D92 | Overlap (revises D25, D62) | With `overlap: "allow"`, descriptive values only; tests and between-cohort intervals not estimable | Tests on overlapping groups are invalid whatever the caveat says |
| D93 | Survival methods (refines D13, D67) | Median defined as in R, Brookmeyer–Crowley intervals, log-log curves, a specified bootstrap, landmark survival, Efron ties, one joint Cox model, the R ≥ 3.0 Grambsch–Therneau test | Library defaults differ and move medians on floating-point noise |
| D94 | Disclosure rules (refines D59) | Per-output rules for counts, derived statistics, histograms, catalogue statistics, survival curves and models; the largest threshold across datasets | "Anything from which it could be recovered" could not be implemented |
| D95 | Determinism mechanics (revises D53) | No DuckDB DOUBLE aggregates in digested values; canonical row order; sorted arrays; pinned numerics; fixed replicates; rounding to 10 significant digits with an absolute floor; a stated scope | DuckDB's floating-point sums differed between runs, and fixed orders disagreed with each other |
| D96 | Delayed entry | Endpoints declare `entry`; undeclared entry raises `UNCONFIRMED_SEMANTICS` | Survival from diagnosis in data entered at sequencing is biased by immortal time |
| D97 | Exclusion accounting | Exclusion reasons include NOT_APPLICABLE and INVALID_VALUE; every result has an `analysed` block | P2 requires every exclusion counted by reason |
| D98 | Named methods | One primary test per column type enters BH; interval and test methods named, including Mann–Whitney's exact rule; the R reference set extended | Library defaults differ from R and from each other |
| D99 | Versions and counts in ids (refines D11, D56) | Cohort ids include the semantics version, disclosure settings and pack results versions; cohort counts have digests; refusals report counts with ids; the engine computes cohort fractions, per-category differences and landmark survival | Semantic fixes changed cited counts under unchanged ids, and A1 forbade the arithmetic people need |
| D100 | Scope columns (revises D5 in part) | Scope columns may be mentioned only in top-level `values` conjuncts; `every` must not mention them; partial restrictions require matching listed tuples | An `any` of equalities was treated as open scope and made unassessed genes wild-type |
| D101 | Lift and reasons (refines D9, D44) | `NOT_COVERED` separates coverage from cell-level `NOT_ASSESSED`; `assessed` drops only coverage-derived unknowns and never waives closedness; answers keep the closedness reason | `assessed` treated undeclared coverage as closed, and reasons from cells and coverage were confused |
| D102 | Unknown parent scope (refines D72) | Coverage `all` does not close a parent whose scope is UNKNOWN | Otherwise a sample of unknown type became wild-type without evidence |
| D103 | Negation (revises D48) | Negated membership is a leaf-level `negate`, evaluated per row or item; clause-level `not` negates the quantified answer; they are folded together only for single-valued leaves | `!=` on a multi-valued column meant something different from its nested form |
| D104 | `lift_differs` | Computed by flipping every lift in the canonical cohort at once, comparing truth values | Its definition was ambiguous with several lift settings |
| D105 | Cross-dataset canonical form (refines D61) | A map from each dataset's manifest hash to its clause tree; the concept rule applies to the document as written; `via` per dataset; units default to the concept's | Per-dataset expansions had no defined canonical structure |
| D106 | One release per dataset | A document resolves each dataset to one release; mixing releases is refused | Params could otherwise be read from a release the id does not contain |
| D107 | Readbacks from expansions (refines D55) | Readbacks render the canonical expansion; packs add a labelled summary outside the digest | Readbacks must describe what is computed |
| D108 | Two-phase canonicalisation (revises D34) | Cohorts are canonicalised and hashed first, then views | Views' default order and reference positions need cohort ids |
| D109 | Explicit paths (refines D7, D60) | `via` may revisit a table, and `exclude_self` leaves out the starting row | *The other samples of the same patient* could not be written |

---

## Appendix B. Change log

- **v0.5** — First review round of v0.4 (D73–D109): operator surface; blob store, raw snapshots,
  withdrawal and erasure; import hardening and resource limits; identity scheme; structured text;
  pack hooks and results versions; derivations and issuances; time origins as concepts; model
  cards, derived columns and the core concepts; closedness caveats, scope rules, lift reasons,
  unknown parent scopes and leaf-level negation in §6.5; aggregates, estimability, stratification,
  overlap, named statistical methods, disclosure rules and determinism in §8–§9; delayed entry;
  exclusion accounting; semantics and results versions in ids; two-phase canonicalisation.
- **v0.4** — Rewritten for consistency, with each rule stated once; third review (D44–D72).
- **v0.3.2** — Second consistency review (D33–D43).
- **v0.3.1** — First consistency review (D20–D32).
- **v0.3** — Decisions from the walkthrough (D1–D19); Cox regression, engine-computed effect
  sizes, observation windows, cohort references, `count_cohort`.
- **v0.2** — Domain-agnostic core; oncology moved into a pack; principle P8.
- **v0.1** — First draft.
