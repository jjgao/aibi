# aibi — Specification

**Status:** Draft v0.8.2 · 2026-09-24
**Scope:** product goals, principles, data model, query semantics, result contract, analysis
registry, domain packs, tool and operator surfaces, security, architecture and milestones. The
text is normative where it says MUST, MUST NOT or SHOULD (RFC 2119); everything else is
rationale.
**How to read it:** each rule is defined in exactly one section and referred to elsewhere.
From milestone M2, the reference evaluator (§13.3) is the executable definition of §6 and
§7.6; a disagreement between it and this text is a spec bug, fixed in both. Exact encodings
that this text leaves open are settled by the M0 schemas and recorded here in the same pull
request. Decisions and their reasons are in Appendix A; the change history is in Appendix B.

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
  (§15). The v1 schema reserves per-unit observation windows (§5.3) and supports delayed entry
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
Every count, proportion, statistic and curve, including cohort counts, catalogue statistics
and the counts in refusals and the curation queue, is returned with a reference to how it was
produced: a derivation id for query results and cohort counts (§7.6) and a release-scoped
reference for catalogue statistics (§8.1). Query results also carry the canonical document, the
registered analysis and its version, and the releases used; every proportion carries its
numerator, its denominator and a structured statement of what the denominator counts.
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
into one new release (§12.3). A release can be withdrawn, which removes its data but keeps its
identity (§12.2). *Enforced by:* the storage layer; a document run against the same releases
returns the same result digest.

**P7 — One documentation surface for data, computations and models.**
Descriptors (data), registry entries (computations) and model cards (the models that propose
descriptors or draft documents) share one envelope (§5.1) and one lookup path. Results record
which model or person the client says drafted the document they answer (§8.1).

**P8 — The core is domain-agnostic; domains are packs.**
The core knows about tables, keys, relationships, columns, observation states, coverage,
endpoints and concepts, and nothing else. It has no notion of patients, samples, genes or
assays; its caveat codes, reasons, readback templates and concepts contain no domain terms.
Domain knowledge (vocabularies, descriptor extensions, importers, validators, query shorthands,
analyses, readback summaries, caveats) is added by packs through the public extension points
of §10.1, and every pack leaf compiles to core clauses.
*Enforced by:* an import-boundary test (`aibi.core` MUST NOT import from `aibi.packs`); the core
test suite loads no pack from `aibi.packs` (it may register test-only packs of its own); at
least one non-biomedical fixture dataset is in the core test suite; and the oncology pack's pull
request (M4) changes nothing outside `server/src/aibi/packs/onco`, its tests and its fixtures.

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
requirements are not met fails loudly, names the problem, and lists what *is* available
(§8.6).

**A4 — No private tools.**
The in-app assistant uses exactly the public MCP tools. Anything it can do, an external agent
can do and a person can inspect. Operations reserved for people (§11.2) are not tools at all:
neither the assistant nor any agent can perform them through the tool surface.

**A5 — Proposals are visible until confirmed.**
Anything a model proposes (a key, a relationship, a table role, a descriptor field, a concept
mapping) is stored with status `proposed` and attributed to its proposer. It is never silently
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
| **Release** | An immutable snapshot of a dataset's data **and** descriptors, identified by the hash of its manifest. Published releases carry a label `<dataset>@<n>`; a curation session's working copy is `<dataset>@draft` (§12.3). |
| **Table** | A set of rows with a declared **grain** (what one row is) and a **role** (§5.3). |
| **Keyed table** | A table with a primary key. Any keyed table can be a unit. |
| **Unit** | The table whose rows a cohort counts. |
| **Relationship** | A declared many-to-one or one-to-one link from a child table's foreign key to a parent table's key. Relationships form the **table graph**. |
| **Path** | A sequence of up and down steps along relationships from one table to another (§6.1). |
| **Coverage** | A declaration, per relationship, of which parent rows (and, optionally, which scope values) a child table is complete for (§5.6). |
| **Closed** | Known to have no further, unrecorded children that matter to a question (§6.5). |
| **Reason** | Why a value is UNKNOWN: `NOT_ASSESSED`, `NOT_COVERED`, `NO_INFORMATION`, `NO_PARENT`, `OUT_OF_SCOPE` or `NO_ROWS` (§6.3). |
| **Flag** | A marker carried by a truth value that depended on partial or proposed coverage: `SCOPE_PARTIAL` or `COVERAGE_PROPOSED` (§6.3). |
| **Endpoint** | A declared time-to-event outcome on a keyed table (§5.8). |
| **Concept** | A dataset-independent meaning (e.g. *age in years*, *person*, *time since entry*) that columns, tables and endpoints can be mapped to (§5.7). |
| **Descriptor** | The structured metadata record for any of the above, or for an analysis or model. |
| **Pack** | A domain extension: concepts, descriptor extensions, importers, validators, leaf kinds, analyses, caveats (§10). |
| **Analysis document** | The JSON object declaring cohorts and views (§7). |
| **Derivation** | The canonical, hashed description of how a result or count was produced (§7.6), kept permanently in the derivation log (§12.2). |
| **Issuance** | One act of producing a result or count for a derivation, recorded with the document as written and the SQL as run (§12.2). |
| **Caveat** | A structured, coded statement about a result's fitness for use (§8.3). |
| **Refusal** | A structured, coded error that names the problem and the alternatives (§8.6). |
| **Operator** | A person using the operator surface with the deployment's curator token (§11.2). |
| **Semantics version** | The version of the core rules that decide cohort membership, counts, caveats and digests, hashed into every cohort id (§7.6). |

---

## 5. Descriptors

### 5.1 Identifiers, envelope and curation status

**Identifiers.**
- Dataset, table and column ids match `[a-z][a-z0-9_]*`, contain no `__`, and are unique within
  their scope (datasets within a deployment, tables within a dataset, columns within a table).
  Importers derive them from source names by **normalisation**: Unicode NFKC; lower case; each
  run of characters outside `[a-z0-9]` replaced by one `_`; leading and trailing `_` removed; a
  result that is empty or starts with a digit prefixed with `t_` (tables) or `c_` (columns),
  followed by the 1-based source position if empty. Ids are then assigned in source order; an
  id already assigned, or the table id `dataset` (the dataset descriptor's, below), is a
  collision, resolved by appending the smallest suffix `_<n>`, n ≥ 2, that gives an id not yet
  assigned. Ids have at most 64 characters: a longer result is cut to 64 characters and any
  trailing `_` removed, and a suffix replaces as many final characters as it needs. On re-import
  (§12.3), the *k*-th occurrence of an original name keeps the id of its *k*-th occurrence in the
  previous release, so repeated and empty names keep their ids too; the other names are assigned
  in source order, treating kept ids as assigned. The original names are kept in `source`.
  Names containing `__` are reserved for the system (§12.2).
- Descriptor ids are stable across releases:

  | Kind | Id |
  |---|---|
  | dataset | `dataset` |
  | table | `<table>` |
  | column | `<table>.<column>` |
  | relationship | `rel:<child table>.<column>[+<column>…]`, child columns in declared order; or `rel:<child table>.<role>` when the relationship has a role (roles are identifiers unique within their child table and distinct from its column ids, and are required when the same child columns reference two parents) |
  | coverage | `cov:` + the relationship id without `rel:`, one per relationship |
  | endpoint | `ep:<identifier>` |
  | concept | `<namespace>:<identifier>(.<identifier>)*`; the namespace is `core` or a pack id |
  | analysis | `<family>.<identifier>`; core families are `summary`, `compare` and `survival`; a pack's analyses use its pack id as the family |
  | model card | `model:<identifier>` |

- Pack ids are identifiers that equal no core analysis family, no core leaf kind, and no word
  that begins a core id form (`rel`, `cov`, `ep`, `model`, `dataset`, `sha256`, `drv`, `leaf`,
  `iss`, `stat`), so that no concept, analysis or code of a pack looks like one of those ids.
- Cohort and parameter names in documents match `[A-Za-z_][A-Za-z0-9_]*` and have at most 64
  characters.
- Field paths are JSON Pointers (RFC 6901) relative to the descriptor's root and name whole
  fields (e.g. `/fields/units`, `/extensions/onco/assay`).
- Hashes are written as lowercase hexadecimal: manifest hashes and digests as `sha256:<hex>`,
  derivation ids as `drv:<hex>` and leaf keys as `leaf:<hex>` (§7.6). Issuance ids are `iss:`
  followed by a ULID and are never hashed.
- Integers outside ±(2^53 − 1) are carried as decimal strings wherever they appear in documents,
  canonical forms and results (§7.6).

**Envelope.** Every descriptor, whatever it describes, has:

```jsonc
{
  "kind": "dataset | table | column | relationship | coverage | endpoint | concept | analysis | model",
  "id": "a descriptor id (above)",
  "version": 3,                      // integer, +1 whenever fields change; analyses and model cards use "X.Y.Z"
  "label": "Human-readable name",
  "definition": "One-paragraph definition in plain text",
  "provenance": { "source": "...", "pipeline": {"name": "...", "version": "..."}, "citation": ["doi:…"] },
  "fields": { /* kind-specific, §5.2–5.9, §9.1 */ },
  "extensions": { "<pack id>": { /* validated against the pack's JSON Schema */ } },
  "curation": { "<JSON Pointer>": CurationStatus }
}
```

**Curation status.** Every field that has a value has an entry in `curation`; a field without a
value is `undeclared`. No other place in a descriptor records a status. The curated fields are
`label`, `definition`, each member of `fields` and each member of a pack's extension object.
Descriptors in a release (dataset, table, column, relationship, coverage, endpoint) carry exactly
one entry per field with a value, never with status `undeclared`, which only reports a field
without one; concepts, analyses and model cards are defined by code or configuration and carry
none. An absent member is undeclared, and `null` appears only where it is a declared value: a
table's `primary_key` (no key), a dataset's `min_cell_count` (off), a mapping's `transform` (none),
an analysis's `cross_dataset` (not across datasets) and a curation entry's `inferred`. Values the
core leaves to JSON (`inferred`, a pack's extension members, an analysis's parameter and return
schemas) may hold `null` inside, but an extension member is never `null` itself. Text is never
empty: a field with empty text has no value, so it is omitted. A descriptor holds only what JSON
text carries unchanged: Unicode text, and finite numbers within ±(2^53 − 1), an integral number
being an integer (§7.1). `label` is required. `version` is `X.Y.Z` for analyses and model cards,
each part at most 16 digits, without pre-release or build parts. Extension members are
identifiers, at most 64 per pack.

```jsonc
CurationStatus = {
  "status": "asserted | proposed | imported | imported_default | undeclared",
  "by": "operator:<self-declared name> | model:<identifier> | agent:<client-declared name> | importer:<name>@<version>",
  "at": "RFC 3339 timestamp with an explicit offset, T and Z in upper case, seconds 00–59",
  "evidence": "optional plain text or reference",
  "inferred": "optional: the importer's own inference for this field (§12.3)"
}
```

- `asserted`: confirmed by an operator. `imported`: taken from the source (a header row, a
  database comment, a declared constraint). `imported_default`: filled in by convention (e.g.
  mapping `NA` to UNKNOWN). `proposed`: suggested by a model, an agent or a tool. `undeclared`:
  nobody has said. So only `operator:` asserts, only `importer:` imports (`imported`,
  `imported_default`), and proposals come from `model:`, `agent:` or `importer:`.
- `by` is always set by the server (§11.1), never taken from a request. `model:` names the in-app
  assistant's registered model card; `agent:` records the name an external client declares.
  Operator and agent names have 1 to 200 characters, without control characters (C0, DEL and C1)
  or line breaks (U+2028, U+2029); an importer's name (`[A-Za-z0-9_.-]`) and version (`[A-Za-z0-9_.+-]`) have 1
  to 200 characters each. Leap seconds are not accepted in `at`.
- The engine treats undeclared semantics conservatively: an undeclared missing code is
  UNKNOWN, and undeclared coverage never makes a row closed (§6.5).
- `UNCONFIRMED_SEMANTICS` (§8.3) is raised for every field that canonicalisation or evaluation
  reads and whose status is `imported_default`, `proposed` or `undeclared`, except a coverage
  descriptor's `parents` field when it is `proposed`: that is reported only through the flag
  `COVERAGE_PROPOSED`, which depends on the data (§6.3, §6.6). The set of fields is determined
  statically, so `validate_document` reports the same `UNCONFIRMED_SEMANTICS` caveats as
  evaluation. A field is read when it has a value that resolution or evaluation uses (a
  column's `datatype`, `permissible_values` and `missing_codes`, a relationship's key columns,
  the unit's `primary_key`, a coverage's `record_filter` and `parent_scope`), and also when its
  absence changes the answer: a numeric column's `units` and a coverage's `parents`, which are
  then `undeclared`.
- `extensions` is how packs add domain fields (e.g. the oncology pack's `reference_genome` on a
  dataset). The core stores and validates them but never interprets them.

### 5.2 Dataset descriptor

Id `dataset`. Fields: `name`, `description`, `domain_tags`, `citation` and `references` (lists of
strings), `source` (`{kind: "files" | "database" | "pack", location, commit?}`; never
credentials, §14), `license`, `data_use` (a list of `OntologyRef`, e.g. GA4GH DUO codes),
`disclosure` (`{min_cell_count: integer ≥ 2 | null, allow_row_ids: boolean}`, §8.4) and `packs`
(every pack whose importer created the dataset or whose extensions appear in its descriptors;
the gate checks this when `packs` is declared, §13.2). Computed statistics (`n_rows` per table,
value distributions, observation-state counts, a table-graph summary) are produced when the
release is built and stored separately from the definitional descriptors (§12.2), never written
by hand.

### 5.3 Table descriptor

| Field | Meaning |
|---|---|
| `grain` | Plain-text statement of what one row is (e.g. *one adverse event report*) |
| `role` | `entity` (rows are things: patients, samples, visits), `link` (a many-to-many linking table: enrolments), `measurement` (observations about a parent: mutation calls, lab results), `event` (time-stamped occurrences: treatments, adverse events) or `coverage` (a coverage, assignment or group table, §5.6). The importer proposes a role; packs declare roles for the tables they create. Coverage proposals follow it (§5.6) |
| `primary_key` | A list of columns, or `null` for no key; a table without a key can be filtered and aggregated but cannot be a unit |
| `maps_to` | Optional table concept the rows are instances of (e.g. `core:person`), without a transform; required for a table referenced across datasets (§7.5) |
| `time_origin` | For tables with time columns: a time-origin concept (§5.7) |
| `observation_window` | Reserved for timeline queries (M7); MUST be absent in v1 |
| `source` | `{kind: "file" | "sheet" | "database" | "pack", name, original_name, parse?}`. For text files (`kind: "file"`) only, `parse` holds every one of `{format: "csv" | "tsv", delimiter, quote, header_row, skip_rows, encoding}`: `delimiter` and `quote` are single characters, a `tsv` file's delimiter is a tab, and `header_row` counts from 0 after the `skip_rows` skipped |

### 5.4 Column descriptor

| Field | Meaning |
|---|---|
| `datatype` | `number`, `integer`, `string`, `boolean`, `category`, `list<category>` (the only list type), `date`, `datetime` (stored and compared in UTC), `time_offset` |
| `units` | UCUM code for numbers and offsets (`a`, `mo`, `d`, `mg/dL`, `[USD]`); required for any number used in a cross-dataset comparison |
| `range` | Optional declared range `{min, max}` in the column's type, with `min` ≤ `max`: numbers (integers for `integer` columns), `YYYY-MM-DD` dates, or RFC 3339 datetimes with an offset, compared in UTC; for number, integer, date, datetime and time-offset columns; used for histogram edges under disclosure (§8.4) |
| `permissible_values` | For categories: `{values: [{value, label, concepts: [OntologyRef]}], ordered: boolean}`. Values are strings, as category constants are (§6.4), distinct, and never also missing codes; `ordered` (false when absent) means the listed order is meaningful (needed for `range` predicates and for `max` and `min`) |
| `missing_codes` | Map from a raw token (matched against the cell's canonical string form, §12.2) to `UNKNOWN`, `NOT_APPLICABLE` or `NOT_ASSESSED`, e.g. `{"": "UNKNOWN", "NA": "UNKNOWN", "N/A": "NOT_APPLICABLE", "Not done": "NOT_ASSESSED"}` |
| `identifier` | `true` for columns whose values identify rows or people (patient numbers, record ids, UUIDs). Primary-key and foreign-key columns are identifiers implicitly. Identifier columns never have value distributions (§8.4) |
| `concepts` | `[OntologyRef]` describing what the column measures |
| `maps_to` | Optional concept mapping (§5.7) |
| `derived` | Optional derivation from other columns of the same table (§5.7) |
| `list_syntax` | Required for `list<category>`, and only for it: `{format: "json" | "python" | "delimited", delimiter?}`, with a delimiter of 1 to 8 characters exactly when the format is `delimited` |
| `completeness` | Declared `complete`, `partial` or `unknown` |
| `source` | `{original_name, metadata?}`: the original column name and any header metadata imported with it |

`OntologyRef = {system, code, label, relation: "exact" | "broader" | "narrower" | "related"}`.
The core accepts any `system` string; packs register the systems they validate (e.g. NCIt,
LOINC, OncoTree, HGNC). How values and observation states are stored is in §12.2.

### 5.5 Relationship descriptor

`{child_table, child_columns, parent_table, parent_columns, cardinality: "many-to-one" |
"one-to-one", role?}`, with the id of §5.1. A null foreign key is allowed; a dangling one
(non-null, with no matching parent) is a structural error (§13.2). Both evaluate as `NO_PARENT`
(§6.1).

### 5.6 Coverage descriptor

A coverage declaration belongs to one relationship and says for which of its parents the child
table is complete, and over what scope:

```jsonc
{
  "relationship": "rel:mutations.sample_id",
  "parents": "all"                                       // absent: undeclared
    | { "table": "<coverage table>",                      // direct form
        "parent_columns": { "<coverage column>": "<parent key column>" },
        "scope_columns":  { "<coverage column>": "<child scope column>" } }   // optional
    | { "assignment": { "table": "<assignment table>",   // grouped form
                        "parent_columns": { "<column>": "<parent key column>" },
                        "group_column": "<column>" },
        "groups":     { "table": "<group table>", "group_column": "<column>",
                        "scope_columns": { "<column>": "<child scope column>" },
                        "covers_all_column": "<boolean column>" } },       // covers_all_column optional
  "record_filter": { "variant_class": ["missense", "nonsense", "frameshift", "splice"] },
  "parent_scope": { /* a core clause over the parent table, e.g. sample_type = tumour */ }
}
```

- `parents: "all"`: every in-scope parent was assessed. A **coverage table** lists the assessed
  parents or, with scope columns, the assessed (parent, scope value) tuples. The **grouped
  form** assigns each assessed parent to a group and lists each group's scope values, or marks a
  group as covering every scope value; this is how gene panels (sample → panel → genes) and
  whole-exome samples are expressed without a row per (sample, gene). Without `parents`,
  coverage is undeclared: nobody has said; so is a relationship with no coverage descriptor.
  In the column maps, no two columns stand for the same column, and `covers_all_column` is given
  only with `scope_columns`.
- Scope columns are matched by equality in v1 (a gene, a visit window). Range-based scope such
  as genomic intervals is out of scope for v1.
- `record_filter` states what kinds of rows the table holds, as a conjunction of allowed-value
  lists (distinct strings) on `category` columns, at most 64 columns. A PRESENT value outside the
  filter, or a NOT_APPLICABLE cell in a filtered column, is a structural error (§13.2); queries
  are evaluated against it as in §6.5, step 1.
- `parent_scope` names which parents the table is about (e.g. tumour samples, not blood
  normals). It is evaluated as in §6.5, step 2. In v1 the engine evaluates parent scopes made of
  `value` leaves on the parent row or rows it looks up, and combinators. A scope that asks a
  question (an `exists` or `covered` leaf, or a `value` leaf below the parent table) is refused as
  `NOT_SUPPORTED`, and one that does not resolve against the release with its cause's code, both
  when the release is checked and when a document asks about the relationship (D207).
- The grouped form lists a parent when an assignment row names it and the group table has that
  row's group; its scope tuples are the group's, and it lists the parent for every tuple when one
  of its groups is marked as covering every scope value.
- Coverage, assignment and group tables have role `coverage`, MUST NOT contain nulls in the
  columns named here, and are not part of the table graph (§6.1). D16's scale targets count data
  tables only.
- **Proposals.** For every relationship whose child table has role `entity` or `link`, the
  importer proposes `parents: "all"`, for files and database snapshots alike. Relationships into
  tables whose role is `measurement` or `event` stay `undeclared`, because that is where partial
  coverage hides; the curation assistant may propose coverage for them with evidence (e.g. *a
  panel column was found*), and an operator decides. Packs declare coverage explicitly. Results
  that rely on proposed coverage carry `COVERAGE_PROPOSED`, and the curation queue ranks these
  confirmations first.

### 5.7 Concepts, mappings and derived columns

A **concept** is a dataset-independent descriptor (`kind: concept`) of one of four sorts, its
`sort` field: value concepts, with `units` or `permissible_values` but not both; table
concepts, naming what rows are; endpoint concepts, naming time-to-event outcomes; and
time-origin concepts, naming what time zero means. Concepts are namespaced by who defines them
and versioned; canonical forms record the versions they use (§7.6).

The core defines only domain-neutral concepts: `core:person` (table); `core:age_years` (value,
units `a`); `core:sex` (value; `female`, `male`); and the time origins `core:origin.birth`,
`core:origin.entry` (the unit's entry into the data or its observation) and
`core:origin.calendar` (absolute dates). Packs add their own, including domain time origins
(the oncology pack: `onco:origin.diagnosis`, `onco:origin.specimen_collection`,
`onco:origin.treatment_start`, `onco:origin.first_sequencing`).

A column, table or endpoint maps to a concept through `maps_to`:

```jsonc
ConceptMapping = {
  "concept": "core:age_years",
  "transform": { "unit_from": "d", "unit_to": "a" } | { "value_map": {"M": "male", "F": "female"} } | null
}
```

- Mappings are **exact**: a column maps to a concept iff it means that concept. A looser
  comparison needs a looser concept, not a weaker mapping.
- Only mappings with curation status `asserted` are used; others are ignored and listed when a
  reference to their concept is refused.
- Transforms are limited to unit conversions and value maps. Anything else is a **derived
  column**, whose `derived` field is one of:
  - `{"op": "date_diff", "from": "<column>", "to": "<column>", "units": "<UCUM>"}`;
  - `{"op": "arith", "operator": "+" | "-" | "*" | "/", "args": [Operand, Operand]}`, where an
    operand is a column id, a number, or another `arith` object;
  - `{"op": "value_map", "input": "<column>", "map": {"<value>": "<value>", …}}`;
  - `{"op": "unit_convert", "input": "<column>", "units": "<UCUM>"}`.

  Derived columns may use other derived columns, without cycles, and are computed when the
  release is built. A derived cell is PRESENT when all inputs are PRESENT and the operation is
  defined; an undefined operation (division by zero, overflow, a value missing from a value map)
  gives UNKNOWN; otherwise the cell takes the first of UNKNOWN, NOT_ASSESSED and NOT_APPLICABLE
  found among its inputs, in that order.

### 5.8 Endpoint descriptor

`table` (a keyed table), `time_column` (a `time_offset` column; its units are the endpoint's
units), `status_column`, `event_coding` (`{event: [values], censored: [values]}`: at least one
event value, the values distinct and none both an event and censored), `time_origin` (a
time-origin concept; defaults to the table's), `entry` (delayed entry: `"at_origin"` or
`{"column": "<time_offset column on the same clock>"}`; undeclared when absent, the default),
and optionally `maps_to` an endpoint concept. Like any field, these may be undeclared; an
endpoint whose table, time, status or event coding is undeclared cannot be used by an analysis
(M3 refuses it).

- An undeclared `entry` raises `UNCONFIRMED_SEMANTICS` on every survival result that uses the
  endpoint, because survival from an origin that precedes entry into the data is biased
  (immortal time) unless entry is declared.
- For analyses, a row whose time, status or entry cell is not PRESENT is excluded with that
  cell's reason (or `NOT_APPLICABLE`); a status outside `event_coding`, a negative time, or an
  entry at or after the time is excluded as `INVALID_VALUE` (§6.6), as R's `Surv(entry, time,
  status)` treats such rows as missing. With `entry: "at_origin"` or undeclared entry, entry is
  just before time 0, so an event at time 0 is at risk at 0, as in R's `Surv(time, status)`. The
  importer lists invalid rows, as counts with references, in the curation queue. The core detects
  nothing by name; packs and the curation assistant propose endpoints.

### 5.9 Model card

A model card (`kind: model`, id `model:<identifier>`) describes a model that proposes
descriptors or drafts documents: `provider`, `model`, `model_version`, `purpose` (a list),
`limitations` (plain text) and `configuration_digest` (the hash of the instructions and settings
in use), all required. Model cards are registered only in server configuration (§11.2).

---

## 6. Query semantics

### 6.1 The table graph and paths

Tables other than coverage tables are the nodes of the table graph; relationships are its
edges, from child to parent.

- A **step** is `{"rel": "<relationship id>", "dir": "up" | "down"}`. An up step (child to
  parent) is a lookup: each child row has at most one parent, and a null or dangling foreign key
  makes whatever is looked up or asked through it UNKNOWN with reason `NO_PARENT`. A down step
  (parent to children) is an existence question (§6.5).
- `via` is an array of steps from the current row (the unit, or the row a `where` is evaluated
  on).
- An **implicit path**, from the current row to a referenced table, is a sequence of at most 16
  steps (as many as a `via` may have, §14) that visits no table twice. If exactly one exists, it
  is used. If none exists, the reference is refused: `NO_PATH` when no relationships join the
  two tables, `LIMIT_EXCEEDED` (`path_steps`) when every path that visits no table twice is
  longer. If several exist, the document is refused, listing them (64 at most, then saying that
  there are more), unless `via` names one. There is no shortest-path or other silent choice.
- An **explicit path** (`via`) may revisit a table, e.g. samples → patient → samples for *the
  other samples of the same patient*. `exclude_self: true` on an `exists` leaves out the row the
  path started from; it is allowed only when the path's first down step enters that row's table,
  and not in a `where` that a path's trailing lookups serve, whose questions canonicalisation
  asks from the row before the lookups (§7.6, step 5; D211).
- **One question per down step.** Canonicalisation (§7.6) writes every existence question as a
  chain of `exists` leaves with one down step each: a multi-step `exists` becomes the `exists`
  for its first down step, whose `where` holds the `exists` for the rest of the path, and a
  reference to a column below the current table (a `value` leaf whose path has a down step)
  becomes the chain that ends with its value predicate. A direct reference, the multi-step
  `exists` over its path and the nested `exists` leaves that spell it out therefore have one
  canonical form, and the same answers.
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
| `NOT_ASSESSED` | It is known that it was not assessed | A `Not done` code; a parent the coverage does not list (reason `NOT_COVERED`, §6.3) |
| `NOT_APPLICABLE` | The question does not apply | `N/A` mapped to NOT_APPLICABLE |
| `UNKNOWN` | There is no information either way | Empty cell, undeclared missing code, undeclared coverage |

A cell takes its state from the column's `missing_codes`; a null or empty cell with no declared
code is UNKNOWN. A list cell has a state as a whole; when it is PRESENT, each of its items has
its own state (§12.2). For ordinary columns, a negative answer (`"No"`) is a PRESENT value.
ABSENT arises only from existence questions.

### 6.3 Truth values, reasons, flags and combinators

Every criterion evaluates, per row it applies to, to TRUE, FALSE or UNKNOWN.

UNKNOWN carries a non-empty set of **reasons**:

| Reason | Meaning |
|---|---|
| `NOT_ASSESSED` | A cell says it was not assessed (a code such as `Not done`) |
| `NOT_COVERED` | Coverage says the row was not assessed: a coverage table does not list it, or nothing below it could be considered (§6.5) |
| `NO_INFORMATION` | No information either way: an empty cell, an undeclared code, undeclared coverage |
| `NO_PARENT` | An up step met a null or dangling foreign key |
| `OUT_OF_SCOPE` | The question does not apply to this row: it is outside a relationship's `parent_scope` |
| `NO_ROWS` | Nothing to evaluate: `every` over no rows, or `match: "all"` over an empty list |

Every truth value also carries a possibly empty set of **flags**, `SCOPE_PARTIAL` and
`COVERAGE_PROPOSED`, set where an answer depended on partial or proposed coverage (§6.5,
step 7).

- `all` is FALSE if any operand is FALSE, otherwise UNKNOWN if any is UNKNOWN, otherwise TRUE.
  `any` is TRUE if any operand is TRUE, otherwise UNKNOWN if any is UNKNOWN, otherwise FALSE.
  `not` swaps TRUE and FALSE and leaves UNKNOWN. (This is Kleene's strong three-valued logic.)
- The reasons of an UNKNOWN result are the union of the reasons of its UNKNOWN operands. The
  flags of a result are the union of the flags of the operands that have the result's value
  (for `all` returning TRUE and `any` returning FALSE, all operands); `not` keeps flags.
- `known(C)` is TRUE iff `C` is not UNKNOWN, otherwise FALSE; `unknown(C)` is TRUE iff `C` is
  UNKNOWN, otherwise FALSE. They keep `C`'s flags, and are the only way to include unknowns
  deliberately.
- A unit is **in** a cohort iff the cohort's predicate is TRUE for it.

### 6.4 Value predicates

A value predicate compares one column's value per row with constants. Its forms are `values`
(membership in a set), `range` (`gt` or `gte`, and/or `lt` or `lte`) and `op` with `value`
(`=`, `!=`, `>`, `>=`, `<`, `<=`), with an optional `negate: true`. Canonicalisation reduces them
to `values` and `range`, each with `negate` (§7.6).

| Cell state | Base result |
|---|---|
| PRESENT | TRUE or FALSE, by the value |
| NOT_APPLICABLE | FALSE |
| NOT_ASSESSED | UNKNOWN (`NOT_ASSESSED`) |
| UNKNOWN | UNKNOWN (`NO_INFORMATION`) |

- `negate` swaps TRUE and FALSE of the base result, per row, and leaves UNKNOWN. So for a male
  patient, `menopause = "pre"` is FALSE and `menopause != "pre"` is TRUE, while `age <= 60` and
  `age > 60` are both FALSE when age is NOT_APPLICABLE. Readbacks show every negation.
- On a multi-valued reference (a column below the current table, or a list column), `negate`
  applies per row or item, inside the quantifier: *some mutation whose gene is not TP53*. A
  clause-level `not` around the leaf negates the quantified answer: *no TP53 mutation*.
- **Constants** have the column's type: numbers for numeric columns (64-bit integers for
  integer columns; numbers beyond ±(2^53 − 1) are written as decimal strings, §5.1, and for
  number and time-offset columns stand for the nearest double, as the column's values do, D203),
  strings for categories and strings, booleans, `YYYY-MM-DD` for dates, RFC 3339 with an explicit
  offset for datetimes (compared in UTC, to the microsecond at most, as datetimes are stored),
  and numbers in the predicate's units for time offsets (see Units). Anything else is refused.
- **Ranges** apply to numbers, dates, datetimes, time offsets and ordered categories (by their
  listed order); they are refused on strings, booleans and unordered categories. A PRESENT value
  outside an ordered category's listed values has no place in their order: a range over it is
  UNKNOWN (`NO_INFORMATION`), while it is simply not a member of any `values`.
- **Units.** A numeric predicate carries `units`: by default the column's units, or the
  concept's units for a concept reference. Constants in other units are converted at evaluation,
  using pinned UCUM conversion factors, and refused when no conversion exists; the canonical
  form keeps the units as written. A constant is converted by one multiplication of doubles, by
  the exact factor rounded to a double, so that the SQL engine computes the same comparison.
  Readbacks always state the units. A numeric column without declared units raises
  `UNCONFIRMED_SEMANTICS`.
- **Categories.** If the column's permissible values are declared, a constant outside them is
  refused, and the refusal lists them.
- **Lists.** A list cell that is not PRESENT takes its base result from the table above,
  whatever `negate` and `match` say: `negate` applies to items, and there are none. A
  PRESENT list is evaluated item by item, each item with its own state: with `match: "any"`
  (default) the result is TRUE if any item is TRUE, otherwise UNKNOWN if any item is UNKNOWN,
  otherwise FALSE (an empty list is FALSE); with `match: "all"` it is FALSE if any item is FALSE,
  otherwise UNKNOWN if any item is UNKNOWN or the list is empty (`NO_ROWS`), otherwise TRUE.

### 6.5 Existence and coverage

An **existence question** is an `exists` leaf in canonical form (§7.6). From a row `r`, it
follows the up steps of its path (lookups) and then one down step, along relationship ρ, into
child table `C`. It has a quantifier (`some` with `min_count` *k* ≥ 1, or `every`) and a
`where`: clauses evaluated on each child row, whose conjunction is `W_C`. The question is
**intermediate** if its `where` contains a **nested question** (an `exists` or `covered` leaf)
at any depth, and then it has a lift rule (`strict`, the default, or `assessed`); otherwise it is
**final**. The coverage used is ρ's (`cov:` of ρ); a relationship without one is `undeclared`.
If a lookup meets a null or dangling foreign key, the answer is UNKNOWN (`NO_PARENT`).

For each `r`:

1. **Record filter.** If ρ's coverage has a `record_filter`, each child is evaluated as `W_C ∧
   filter` under `some` and as `(not filter) ∨ W_C` under `every`, so a child whose filtered
   value is missing is decided only where `W_C` decides it alone. Filtered columns may be
   mentioned in `W_C` only in top-level conjuncts of the form `values` without `negate` whose
   values lie within the allowed values; any other mention is refused. The readback states the
   filter. A column is *mentioned* by a `value` leaf on the child row itself (no `via`),
   anywhere in `W_C` outside nested questions; a leaf reached through a lookup, or inside a
   nested question, is about another row. These rules read `W_C` in canonical form, after
   step 8 of §7.6 removes duplicates: `{"any": [X, X]}` is `X`.
2. **Parent scope.** If ρ's coverage has a `parent_scope` and it is FALSE for `r`, the answer is
   UNKNOWN (`OUT_OF_SCOPE`) and the steps below do not apply. If it is UNKNOWN for `r`, `r` counts
   as in scope, but coverage `all` does not close it (step 4).
3. **Children.** Evaluate each child. For an intermediate question, drop the children whose
   value is UNKNOWN with every reason in the lift rule's drop set: `strict` drops `OUT_OF_SCOPE`;
   `assessed` drops `OUT_OF_SCOPE` and `NOT_COVERED`. A final question drops nothing. Let *K* be
   the remaining children, and *t*, *f*, *u* the numbers of them that are TRUE, FALSE and
   UNKNOWN. A child's **conditions** are the conjunction of the top-level `where` clauses that
   contain no nested question (TRUE when there are none).
4. **Closedness.** `r` is *closed* when it is known that `r` has no unrecorded children that
   matter to the question; when it is not closed, the question records a **closedness reason**.
   - Coverage `all`: closed. If `r`'s parent scope is UNKNOWN, not closed, with the scope
     clause's reasons.
   - A coverage table, direct or grouped, without scope columns: closed iff it lists `r`;
     otherwise `NOT_COVERED`.
   - With scope columns, let *S* be the scope columns that `W_C` mentions. `W_C` may mention a
     scope column only in top-level conjuncts of the form `values` without `negate`; any other
     mention is refused. The coverage lists `r` for **every tuple** only through a group marked
     as covering every scope value; a direct coverage table never does.
     - `some`: closed iff, for every combination of the values `W_C` admits on *S*, the coverage
       lists `r` for at least one scope tuple matching it; otherwise `NOT_COVERED`. If some scope
       columns are not in *S* (in particular if *S* is empty) and the coverage does not list `r`
       for every tuple, a FALSE answer covers only the listed tuples (`SCOPE_PARTIAL`).
     - `every`: `W_C` MUST NOT mention scope columns (refused otherwise). Closed iff the coverage
       lists `r` for at least one scope tuple; unless it lists `r` for every tuple, a TRUE answer
       covers only the listed tuples (`SCOPE_PARTIAL`). Otherwise `NOT_COVERED`.
   - Coverage `undeclared`: not closed, reason `NO_INFORMATION`.
5. **Answer.** Whenever the answer is UNKNOWN and `r` is not closed, the closedness reason is
   added to its reasons.
   - `some` with `min_count` *k*: TRUE if *t* ≥ *k*. Otherwise UNKNOWN if *t* + *u* ≥ *k* (the
     reasons of the UNKNOWN children). Otherwise, for a final question, FALSE if `r` is closed,
     else UNKNOWN; for an intermediate question, FALSE if `r` is closed and some child in *K* has
     conditions that are TRUE, else UNKNOWN, adding `NOT_COVERED` and the reasons of the
     children's UNKNOWN conditions when no child in *K* has conditions that are TRUE.
   - `every`: FALSE if *f* ≥ 1. Otherwise UNKNOWN if *K* is empty (reason `NOT_COVERED` for an
     intermediate question, `NO_ROWS` for a final one), or if *u* ≥ 1 (the reasons of the UNKNOWN
     children). Otherwise TRUE if `r` is closed, else UNKNOWN.
6. **Evidence.** A matching child is evidence even where the coverage does not list `r`: step 5
   makes the answer TRUE regardless (the importer flags such rows, §13.2).
7. **Flags.** A FALSE from `some` and a TRUE from `every` carry the flags of every child,
   remaining or dropped, plus `SCOPE_PARTIAL` when step 4 restricted the answer to listed tuples and
   `COVERAGE_PROPOSED` when ρ's coverage is `proposed`. A TRUE from `some` carries the flags of
   its TRUE children, and a FALSE from `every` those of its FALSE children. An UNKNOWN answer
   carries the flags of its UNKNOWN children and of the dropped children, plus
   `COVERAGE_PROPOSED` when a closedness reason was added and ρ's coverage is `proposed`.

**`covered`** (§7.2) turns closedness into a predicate. For its last down step, in this order:
UNKNOWN (`OUT_OF_SCOPE`) if the parent scope is FALSE; TRUE if `r` is closed for the given scope
values (step 4, read as for `some` with *S* the scope columns given); UNKNOWN (`NO_INFORMATION`)
if the coverage is undeclared; UNKNOWN (the scope clause's reasons) if the parent scope is
UNKNOWN; otherwise FALSE. Its TRUE carries `SCOPE_PARTIAL` when its closedness was restricted to
the listed tuples, and its TRUE and FALSE carry `COVERAGE_PROPOSED` when ρ's coverage is
`proposed`: both rely on ρ's coverage. Each earlier down step, from the last to the first,
applies steps 2–4 to its own relationship and drops children as in step 3; if no child remains,
it gives UNKNOWN (`NOT_COVERED`), with the closedness reason and the flags that steps 5 and 7
give an UNKNOWN. Otherwise, under `strict`, it gives the answer and flags of
`every` over the remaining children (steps 5 and 7). Under `assessed`, it gives TRUE if some
remaining child is TRUE and `r` is closed, FALSE if `r` is closed and every remaining child is
FALSE, and otherwise UNKNOWN (with the children's reasons and the closedness reason); TRUE
carries the flags of the TRUE children and FALSE those of every child, remaining or dropped, both
plus the flags step 7 adds for ρ, and UNKNOWN follows step 7.

Consequences, which the reference evaluator's scenario tests encode:

- `not exists mutations where gene = TP53`, for a sample, is TRUE only if the sample is assessed
  for TP53 and has no TP53 row. With `where: [{any: [gene = TP53, gene = EGFR]}]` the document is
  refused (step 4); `gene in [TP53, EGFR]` is the accepted form.
- A participant whose only adverse event has a missing grade is UNKNOWN both for *some adverse
  event of grade ≥ 3* and for its negation.
- A patient with one assessed wild-type tumour sample and one unassessed tumour sample is
  UNKNOWN for *a TP53 mutation in some sample* under `strict` and FALSE under `assessed`. A blood
  normal outside the mutations relationship's parent scope changes neither answer. A patient
  with no samples, or with only a blood normal, is UNKNOWN (`NOT_COVERED`) under both. The direct
  reference, the two-step `exists` and `exists samples where [exists mutations where gene =
  TP53]` have one canonical form (§6.1).
- *A TP53 mutation in some primary sample, counting only assessed samples* is `exists samples
  where [sample_type = primary, exists mutations where gene = TP53]` with `lift: "assessed"`. A
  patient whose primary samples are all unassessed, or who has no primary sample, is UNKNOWN
  (`NOT_COVERED`) under both lift rules, whatever its other samples; so is a patient whose only
  assessed wild-type sample has a missing type.
- *Some sample with both a TP53 mutation and an EGFR amplification* is one intermediate question
  over samples: a patient with no samples is UNKNOWN (`NOT_COVERED`), and a blood normal changes
  nothing.
- A participant with no enrolments is FALSE for *enrolled in a phase 3 trial* when the
  enrolments relationship is closed: the question is final, because the trial's phase is looked
  up from each enrolment.
- With a record filter, a row whose filtered value is missing makes `every` UNKNOWN, not FALSE,
  when it fails `W_C`, and makes `some` UNKNOWN, not TRUE, when it satisfies `W_C`.
- `every` over a relationship with undeclared coverage is never TRUE, and a parent whose scope is
  UNKNOWN is never closed by coverage `all`.

### 6.6 Accounting

Every cohort result reports, over the unit table:

- `n_true`, `n_false` and `n_unknown`;
- `unknown_by_reason`: the UNKNOWN units per reason, every reason listed (zeros included); a unit
  counts under each of its reasons;
- `unknown_by_leaf`: for each top-level clause of the canonical cohort (each member of its
  top-level `all`), keyed by that clause's hash (`leaf:<sha256>`, §7.6), the units whose cohort
  result is UNKNOWN and for which that clause is UNKNOWN, every clause listed (zeros included;
  counts can overlap). The map from the cohort's leaves as written (a pack leaf and a `cohort`
  leaf count as one each; a referenced cohort's own leaves are in its map) to these keys is kept
  outside the digest (§8.1);
- `lift_differs`: the units whose truth value (TRUE, FALSE or UNKNOWN, not its reasons) would
  change if every `lift` in the canonical cohort were flipped at once; when it is above zero, the
  result carries `LIFT_DIFFERS`.

A cohort raises `SCOPE_PARTIAL` or `COVERAGE_PROPOSED` if the cohort-level truth value of any
unit carries that flag.

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
  "dataset": "trial_xyz",                        // "trial_xyz", "trial_xyz@3", "trial_xyz@sha256:<hex>" or "trial_xyz@draft"
  "unit": "participants",                        // a keyed table of the table graph, or a table concept such as "core:person" (§7.5)
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
  "drafted_by": "model:<id> | agent:<name> | operator:<name>"   // the client's claim; never hashed
}
```

`Clause := Leaf | {"all": [Clause]} | {"any": [Clause]} | {"not": Clause} | {"known": Clause} | {"unknown": Clause}`

- **Releases.** An unpinned dataset means its latest published, non-withdrawn release; `@draft`
  means the open curation session's draft (§12.3). Pins to withdrawn or discarded releases are
  refused, naming the status. A document resolves each dataset to exactly one release; mixing
  releases of one dataset in one document is refused.
- **Params.** A value that is exactly `"$name"` is replaced by that parameter, whatever its
  type; `"$$…"` stands for a literal string starting with `$`; any other string that starts with
  a single `$` is refused, so a mistyped reference is never taken literally; there is no
  interpolation inside longer strings. Substitution happens before validation, so tools accept
  `"$name"` in any position of the document as written, and the substituted document is
  validated against the document schema. Parameter values are taken verbatim: nothing in them is
  substituted or unescaped. `notes`, `note` and `drafted_by` are plain text and never substituted.
  The substituted document, `params` included, may be no larger than a document may be (§14), in
  bytes and in JSON values, nor nest deeper, nor have longer paths to its values, one by one or
  together; a reference that would cross a limit is refused. A document that declares more
  parameters than it may have is refused before any is substituted. An unknown name is a refusal
  naming its path, whose alternatives are the declared names, or the 16 nearest it in sorted
  order when more are declared; declared but unused parameters are reported; the parameters used
  are echoed in results, outside the digest.
- **Parsing.** A document is UTF-8 without a byte order mark, and its strings and keys are
  Unicode text: lone surrogate escapes and noncharacters are refused (as in I-JSON, RFC 7493).
  Duplicate keys in a JSON object, `null` anywhere in a document (an absent member is omitted)
  and non-finite numbers are refused. A number is a value, not a spelling: `2.0` is the integer
  2, as RFC 8785 writes it. Every double beyond ±(2^53 − 1) is an integer, so any number beyond
  that range is refused, however it is written; such integers are written as decimal strings
  (§5.1). Arrays and objects nest at most 64 deep, and the JSON Pointer to any value has at most
  16,384 characters. A document built in code holds only values that JSON text carries
  unchanged; the limits on size, nesting and paths apply to its JSON text, as written and after
  substitution, and are checked when it is loaded. Size limits are in §14.
- **Caps**, applied to the canonical form (§7.6), after duplicates are removed and with the
  clauses of referenced cohorts inlined: depth 8, counted as the number of clause objects on the
  longest chain from a member of a cohort's top-level `all` to a leaf, both included, through
  combinators and `where`; 64 leaves per cohort, counting the leaves inside every `where` and
  each empty `all` or `any` as one; 6 cohorts; 8 views per document. Each down step of a path
  is a question of its own (§6.1): a value three steps below the unit is four clause objects
  deep and four leaves. A cohort past a cap is refused (`LIMIT_EXCEEDED`, `clause_depth` or
  `leaves_per_cohort`) at its `all`. A cohort as written has at most 256 `cohort` leaves, each
  inlining the cohort it names (`cohort_references`, refused at its `all` when the document is
  loaded).
- **Notes** are plain text, never compiled and never interpreted (A6). `drafted_by` is recorded
  as the client's claim.
- **Translation.** Documents in other formats (cbio-lab's first) are translated by pack document
  translators (§10.1) through `validate_document`, which flags every `not` whose meaning changes
  under three-valued logic.

### 7.2 Core leaf kinds

| Kind | Shape | Meaning |
|---|---|---|
| `value` | `{kind: "value", column: "<table>.<column>" \| "<concept>", values? \| range? \| op? + value?, negate?, units?, match?, quantifier?, lift?, via?}` | A value predicate (§6.4) on a column of the current table or any table reachable from it (§6.1) |
| `exists` | `{kind: "exists", table, where?: [Clause], quantifier?, min_count?, lift?, via?, exclude_self?}` | An existence question (§6.5); `where` clauses are ANDed and evaluated per row of `table`. The path MUST have at least one down step, and a path that ends with an up step needs a non-empty `where` (canonicalisation moves those lookups into it, §7.6) |
| `covered` | `{kind: "covered", table, scope?: {<child scope column>: [values]}, lift?, via?}` | Coverage as a predicate (§6.5); `scope` keys MUST be scope columns of the relationship's coverage. The path MUST have at least one down step and end with one |
| `ids` | `{kind: "ids", ids: ["<dataset>:<key>" \| {"dataset": "<id>", "key": [<values in key order>]}, …]}` | An explicit list of unit keys, each typed against the unit's key columns; the text form is for a key of one column. Not allowed inside any `where`; refused on datasets with `allow_row_ids: false` (§8.4) |
| `cohort` | `{kind: "cohort", cohort: "<name>"}` | Another cohort of the same document, with the same unit and dataset(s). Not allowed inside any `where`; cycles are refused. `{"all": [{"kind": "cohort", "cohort": "base"}, {"not": X}]}` is the correct *rest of the base* under three-valued logic, which is why references exist |

A `values` list has at least one member, and a `range` at least one bound and at most one lower
and one upper bound (`gt` or `gte`, `lt` or `lte`). Empty `all` and `any` lists are allowed:
`{"all": []}` is TRUE and `{"any": []}` is FALSE for every row. `datasets` has at least two
entries and names each dataset once; `ids` has at least one member; `scope` has at least one
column and each of its lists at least one value; a quantifier list has at least one entry; a
document has at least one cohort; a view's `cohorts` has at least one entry, none repeated, and
its `reference` is one of them; *k* is at most 2^53 − 1. `drafted_by` names have 1 to 200
characters and no control characters, and `units` 1 to 64 printable ASCII characters.

**Quantifiers.** `quantifier` is `Q` or a list of `Q`, where `Q` is `"some"`, `"every"` or
`{"some": k}`. A single `Q` applies to every down step of the leaf's own resolved path. A list has
exactly one entry per down step, in path order; otherwise the document is refused and the refusal
shows the resolved path. `min_count: k` is shorthand for `{"some": k}` at the leaf's last down
step and is allowed only when `quantifier` is absent or `"some"`. The default is `"some"`. A
leaf's `lift` applies to every intermediate question of its canonical chain (§6.5, §7.6).

### 7.3 Pack leaf kinds

Packs register leaf kinds namespaced by pack id (§10.1), e.g. `{"kind": "onco.genomic", "q":
"EGFR: AMP; PTEN: HOMDEL"}`. The document schema keeps an open branch for them, validated per
deployment against the registered packs' schemas.

- A pack leaf is compiled to core clauses by the pack's leaf compiler (§10.1): a pure,
  deterministic function of the leaf, the release's descriptors and the pack version, with no
  access to data. It returns core clauses in document form containing no pack, `ids` or `cohort`
  leaves, or refuses.
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
  count (§8.6). With `overlap: "allow"`, and only when units are actually shared, the result gives
  each cohort's descriptive values only: no between-cohort estimate, interval, test or q-value is
  computed (reason `overlapping_cohorts`), and the result carries `COHORTS_OVERLAP`. The valid
  contrast is a subset against the rest of its base (§7.2, `cohort`).
- `params` use arrays and objects with fixed keys, never keys chosen by the user. Clauses in
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
  `via` may be given per dataset, as `{"<dataset id>": [steps]}`. Pack leaves are expanded, and
  clause and endpoint parameters resolved, separately in each dataset.
- Endpoints MUST map to the same endpoint concept. Time origins are compared by concept id; if
  they are undeclared or differ, results carry `TIME_ORIGIN_MISMATCH` (block).
- The analysis MUST declare a cross-dataset method in the registry (§9.1), or the view is
  refused. A between-cohort contrast is estimable only if both cohorts have data (and, for
  survival, events) in at least one shared dataset; k-sample tests use the cohorts that share
  datasets, with degrees of freedom equal to the rank of their covariance; cohort terms that
  cannot be identified are dropped from models. Contrasts that cannot be estimated are not
  estimable (reason `confounded_with_dataset`) and the result carries
  `CONFOUNDED_WITH_DATASET`. Pooled values ignore dataset, are labelled as pooled and carry
  `POOLED_ACROSS_DATASETS`.
- `unmapped: "allow"`, on the cohort or view, replaces the refusal: references are then matched
  by name, and every affected result carries `UNMAPPED_COMPARISON`.
- `via` by dataset is allowed only in a cross-dataset cohort, and names only its datasets. The
  rules of this section that need no release (a concept as the unit, concept references in a
  cross-dataset cohort, `via` by dataset) are checked when the document is loaded.
- The same individual appearing in two datasets cannot be detected; units from different
  datasets are always distinct.

### 7.6 Canonical form, ids and digest

Canonicalisation has two phases. `params` are substituted once, before both (§7.1).

**Phase 1: cohorts**, each after the cohorts it references:

1. Resolve every dataset reference to its release's manifest hash (the label is recorded beside
   it and never hashed), and every pack to its exact installed version (refused if missing or
   incompatible).
2. Expand pack leaves (§7.3), separately per dataset for a cross-dataset cohort.
3. Resolve names: columns and tables to descriptor ids; a concept reference to the resolved
   column id plus `concept: {id, version}`, with value maps applied to its constants; paths to
   explicit `via`; `cohort` leaves to the referenced cohort's canonical form, inlined.
4. Normalise leaves: `=` to `values` with one value; `!=` to `values` with `negate` toggled; `op`
   inequalities to `range` (both `gt` and `gte`, or both `lt` and `lte`, are refused); a `not`
   directly around a single-valued `value` leaf folded into its `negate`; each leaf's quantifier
   resolved to one per down step of its own path (§7.2); `ids` members written as `{"dataset":
   <manifest hash>, "key": [<typed values in key order>]}`; constants typed as in §6.4.
5. Split paths (§6.1): every `exists`, and every `value` leaf whose path has a down step, becomes
   a chain of `exists` leaves with one down step each. The `exists` for a down step has as `via`
   the up steps just before it and the step itself, as `table` its child table, the step's
   quantifier, the leaf's `lift`, the leaf's `exclude_self` (first down step only), and as
   `where` the `exists` for the next down step or, for the last down step, the leaf's `where`
   (for a `value` leaf, its predicate). Up steps after the last down step are lookups from that
   step's child row: they are prefixed to the `via` of every leaf in its `where`.
6. Flatten: single-member `all` and `any` unwrapped; `all` inside `all` or `where`, and `any`
   inside `any`, flattened.
7. Write each leaf with exactly the members below; absent members are omitted, never `null`.
8. Sort order-insensitive collections, innermost first, by their canonical serialisation,
   compared as sequences of UTF-16 code units (as RFC 8785 sorts keys), removing duplicates:
   members of `values` and `ids`, the value lists of `covered.scope`, `datasets`, and the clauses
   inside `all`, `any` and every `where`.
9. Drop names, `notes`, `note` and `drafted_by`.

Steps 4 to 8 are repeated until the form no longer changes, so canonicalising a canonical form
changes nothing, and a cohort reached through a `cohort` leaf gets the same form as the same
clauses written inline.

| Leaf | Canonical members |
|---|---|
| `value` | `kind`, `column`, `concept` (for concept references), `values` or `range`, `negate` (only if true), `units` (numeric columns only), `match` (list columns only), `via` (only if not empty; up steps only) |
| `exists` | `kind`, `table`, `via` (ending with its one down step), `quantifier` (`"some"` or `"every"`), `min_count` (with `"some"`), `where` (possibly empty), `lift` (on every intermediate question, §6.5, and on no other: `"strict"` unless `"assessed"`), `exclude_self` (only if true) |
| `covered` | `kind`, `table`, `via`, `scope` (if given), `lift` (exactly when the path has more than one down step: `"strict"` unless `"assessed"`) |
| `ids` | `kind`, `ids` |

A cohort's canonical form is a map from each of its datasets' manifest hashes to that dataset's
canonical clause tree (one entry for a single-dataset cohort). After phase 1 no user-chosen name
remains (§13.4).

**Phase 2: views.** A view's canonical form is `{"analysis": {"id", "version"}, "cohorts":
[cohort ids in view order, or by id by default], "reference": <position>` (only for analyses with
`uses_reference`), `"overlap": <boolean>` (only for analyses with `assumes_independent_groups`),
`"params": <canonical params>}`. Canonical params write every default; their clauses are
canonicalised as in phase 1; in a cross-dataset view, the parts that resolve per dataset are maps
from manifest hash to their resolution.

Canonical forms are serialised with the JSON Canonicalization Scheme (RFC 8785); they contain no
non-finite numbers, and integers outside ±(2^53 − 1) are strings (§5.1). Every hash below is the
lowercase hexadecimal SHA-256 of the RFC 8785 serialisation of the object shown:

- **Cohort id**: `drv:` + hash of `{"cohort": <canonical cohort>, "unit": "<descriptor id>" |
  {"concept": "<id>", "version": <n>}, "semantics_version": <n>, "disclosure":
  {"min_cell_count": <k> | null}, "packs": {"<pack id>": <results version>, …}}`. The disclosure
  setting is the effective one over the cohort's datasets and the deployment floor (§8.4); the
  packs are those whose leaves appear in the cohort's expansion and those in its datasets'
  `packs`.
- **Result id**: `drv:` + hash of `{"view": <canonical view>, "disclosure": {…}, "packs": {…}}`,
  with the effective disclosure setting over all the view's datasets, and the packs of the
  analysis and of its cohorts.
- **Computation id**: the result id computed with the disclosure setting removed at every level
  (in the result id and in each cohort id it contains); it seeds resampling (§9.3).
- **Leaf key**: `leaf:` + hash of the canonical clause.
- **Digest**: `sha256:` + hash of an object whose members are exactly the digested fields: for a
  result, `cohorts`, `population`, `analysed`, `values` and `caveats`; for a cohort count,
  `population`, `size` and `caveats`. Numbers are rounded as in §9.3. Caveats are reduced to
  `{code, severity, affects}`, sorted and de-duplicated, with `DRAFT_RELEASE` excluded because it
  describes a release's status, not the computation. Rendered text (readbacks, labels, messages,
  every field whose name ends in `_text`), charts, echoed params and the document as written are
  outside every digest.

The **semantics version** is bumped whenever a core rule changes that can change a cohort's
membership, a count, a caveat or a digest: §6, §7.6, the caveat rules of §8.3, §8.4 and §9.3.

**Invariant:** the same id MUST produce the same digest. A change that alters a digest for an
unchanged id is a bug unless a version in the id was bumped: the analysis version, a pack's
results version, or the semantics version. Dependency upgrades count: a new version of a
statistics library or of DuckDB that changes any golden digest requires bumping the affected
versions. Golden tests check this (§13.4); §9.3 states the scope of the guarantee.

Ids are recorded in the derivation log (§12.2) when `count_cohort` or `run_analysis` first issues
them; `validate_document` returns ids marked *not yet issued*, and `explain` says so for an id
that was never issued. Ids are designed to be citable, but v1 promises no availability; the ids
of a withdrawn release resolve to *withdrawn*, and erased derivations to *erased* (§12.2).

### 7.7 Readback

The server renders a deterministic, plain-language readback of every canonical cohort and view
from templates, as segments (§8.1): template text, and data tokens for labels, values and units.
A readback states every path step and step condition, quantifier and lift rule, the units of
every numeric constant, every record filter and parent scope, every negation, and what is
excluded. For example: *"Patients in GBM (TCGA PanCan) @3 with a TP53 mutation in some tumour
sample. Patients with no TP53 mutation in any assessed tumour sample, and some tumour sample not
assessed for TP53, are unknown and not counted."* Pack summaries (§7.3) follow the readback,
labelled as such. Readbacks are returned with every result and cohort count and shown next to
every figure.

---

## 8. Result contract

### 8.1 Envelope

```jsonc
{
  "derivation": {
    "id": "drv:…",
    "document": { /* canonical form */ },
    "analysis": { "id": "survival.km", "version": "1.0.0" },
    "releases": [ { "dataset": "trial_xyz", "label": 3, "manifest": "sha256:…", "status": "published | draft" } ],
    "packs": { "onco": { "version": "1.2.0", "results_version": 3 } },
    "semantics_version": 1,
    "disclosure": { "min_cell_count": 5 },       // effective setting (§8.4)
    "engine": "aibi 0.8.0"
  },
  "issuance": { "id": "iss:…", "cache_hit": false, "values_from": "iss:…" },
  "source": { "document": { /* as written */ },
              "leaves": { "<JSON Pointer into the document as written>": ["leaf:…", …] },
              "params": { /* echoed */ } },
  "digest": "sha256:…",
  "cohorts": [ { "position": 0, "id": "drv:…", "reference": true }, … ],   // view order
  "population": [ { "n_true": 0, "n_false": 0, "n_unknown": 0, "unknown_by_reason": { … },
                    "unknown_by_leaf": { "leaf:…": 0 }, "lift_differs": 0,
                    "suppressed": [ "<JSON Pointer>", … ] } ],                           // by position
  "analysed":   [ { "n": 0, "excluded": { "NOT_COVERED": 0, … }, "excluded_units": 0 } ], // by position
  "values": { "positions": [ { … } ], "view": { … } },
  "caveats": [ Caveat ],
  "readback": { "cohorts": [ [Segment, …] ], "view": [Segment, …] },
  "labels": [ { "data": "<cohort name>" }, … ],
  "charts": [ /* Vega-Lite specifications, §8.5 */ ]
}
```

- `values.positions` holds per-cohort values in view order; `values.view` holds values that
  belong to the view as a whole (omnibus tests, q-values, effect sizes).
- `analysed` holds, per position, `{n, excluded, excluded_units}` for the analysis's unit set
  (complete cases in `survival.cox`; units with valid endpoint data in `survival.km`). For views
  over several columns or predicates, it counts the units analysed for at least one of them and
  adds `variables`: one `{n, excluded, excluded_units}` per column or predicate, in parameter
  order (two or more); no variable's `n` exceeds the overall `n`. `excluded` counts a unit under each of its
  reasons and lists every exclusion reason, zeros included; `excluded_units` counts each excluded
  unit once, so `n` + `excluded_units` is the position's `n_true`. A suppressed count in
  `analysed` is `null` with `not_estimable` reason `suppressed`; in `population`, `suppressed`
  lists the null members by JSON Pointers relative to the entry (e.g. `"/n_false"`).
- A result has one to six cohorts. `reference` is true for the reference position of an analysis
  that declares `uses_reference`, and false everywhere else.
- A release's `label` is its number, or `"draft"` exactly when its `status` is `draft`; `releases`
  lists each dataset once, by dataset id. An issuance's `values_from` is its own id exactly when
  it is not a cache hit; an output over a draft is never a cache hit (§12.3).
- **Caveats in outputs** affect the digested parts only: `/cohorts`, `/population`, `/analysed` and
  `/values`, and, for `DRAFT_RELEASE`, `/derivation/releases`. The checks an output makes of its
  caveats read those parts alone, never the documents or parameters echoed beside them. Caveats are
  sorted by code, affected paths, severity and message (each segment by kind, data before text, then
  its string, then truncation), strings compared as UTF-16 code units, and exact duplicates are
  dropped; `affects` and `suppressed` are sorted the same way. An absent optional member of an
  output is omitted, never written as `null`.
- **Segments.** A `Segment` is `{"text": "<server-written text>"}` or `{"data": "<text from data
  or a document>", "truncated": true?}`. Values, numbers included, appear in readbacks and
  messages as data tokens holding strings; a data token is cut at 200 characters and then marked
  `truncated`. Quoting and escaping are applied when rendering, never stored.
- **Data wrapper.** Outside segments, every string in an output that comes from data or from a
  document (labels, values, names, notes) is carried as `{"data": "<text>"}` and marked
  `"x-aibi-data": true` in the JSON Schema. The embedded documents (`derivation.document`,
  `source.document`) are carried verbatim, and their whole schema nodes are marked
  `x-aibi-data` (A6).
- **Cache and issuances.** The result cache stores only the digested content, keyed by result id.
  `source`, readbacks, labels, pack summaries and charts are rendered for each issuance. An
  issuance served from the cache says so and names the issuance whose SQL produced the values.
  `explain` returns the SQL recorded for an issuance (§12.2); it is not part of `run_analysis`
  responses.
- **Cohort counts** (`count_cohort`, §11.1) are `{id, digest, population, size, disclosure,
  readback, caveats, releases, issuance}` per cohort. `size` is the cohort's size as a proportion
  of the unit table (§8.2), with `denominator_definition` `{position: null, predicate: null,
  counts: "unit_table"}` and no `excluded`, since nothing is excluded from the unit table.
  `disclosure` is the effective setting, `{min_cell_count}` (§8.4). Caveats of a cohort count have
  `affects: ["/population"]`, and never concern analyses or several cohorts (`COHORTS_OVERLAP`,
  `CONFOUNDED_WITH_DATASET`, `INVALID_EXCLUDED`).
- **Catalogue statistics** (row counts, value distributions, observation-state counts, and the
  counts in the curation queue) carry a release-scoped reference
  `stat:<manifest hash>/<descriptor id><JSON Pointer>` (the pointer supplies the `/`), with
  `?floor=<n>` (2 ≤ n ≤ 2^53 − 1) appended when a deployment floor (§8.4) applies. The pointer is
  written in its URI fragment form (RFC 6901 §6): percent-encoded from UTF-8 with upper-case hex,
  `?`, `#` and `%` included, so a reference has exactly one spelling and is compared as a string.
  The assistant cites it like a derivation id.

### 8.2 Numbers, proportions and effect sizes

- Results contain only what JSON text carries unchanged: finite numbers within ±(2^53 − 1), and
  Unicode text; a statistic beyond that range is refused or reported in other units, and an
  integer beyond it inside `values` is a decimal string (§5.1). A number that cannot be computed
  is `null`, with its
  reason in the enclosing object's `not_estimable` map, keyed by a JSON Pointer relative to that
  object, e.g. `{"estimate": 29.0, "ci": {"low": 21.4, "high": null}, "not_estimable":
  {"/ci/high": "not_reached"}}`; the map is omitted when nothing is missing. Any number may be
  not estimable. The reasons are an enum:
  `no_units`, `no_events`, `zero_denominator`, `zero_variance`, `not_reached`,
  `beyond_follow_up`, `separation`, `not_converged`, `degenerate_table`, `overlapping_cohorts`,
  `confounded_with_dataset`, `suppressed`.
- Every proportion is an object, never a bare number:

```jsonc
{
  "estimate": 0.4119601328903654,
  "numerator": 124,
  "denominator": 301,
  "denominator_definition": { "position": 0, "predicate": "leaf:…", "counts": "known" },   // counts: "known" | "unit_table" | "rows"
  "denominator_text": [ /* segments; outside the digest */ ],
  "excluded": { "NOT_COVERED": 17, "NO_INFORMATION": 3, "NOT_ASSESSED": 0, … },   // every reason
  "ci": { "method": "wilson", "level": 0.95, "low": 0.358, "high": 0.468 }
}
```

  Survival-function estimates are not proportions and are exempt. The estimate is the numerator
  over the denominator, as a double; rounding happens only before hashing (§9.3). A count is
  `null` only when suppressed, and the estimate and its interval are suppressed exactly when a
  count is. Over a zero denominator the estimate is not estimable: `no_units` where no unit was
  counted, as §9.5 orders the reasons, else `zero_denominator`. `excluded`, where a proportion
  has one, lists every exclusion reason, and a suppressed one is `null`. An interval's `level` is
  between 0 and 1, exclusive, and its `method` is an identifier, as the analysis entry names its
  methods.
- Every effect size names its measure (an enum: `risk_difference`, `risk_ratio`,
  `proportion_difference`, `mean_difference`, `median_difference`, `hazard_ratio`), its position
  and its reference position. Differences are position minus reference; ratios are position over
  reference:

```jsonc
{ "measure": "hazard_ratio", "position": 1, "versus": 0, "estimate": 1.8,
  "ci": { "method": "wald", "level": 0.95, "low": 1.3, "high": 2.5 } }
```

- Whenever a number other than a suppressed one is not estimable, the result carries
  `NOT_ESTIMABLE`. Nothing is extrapolated.

### 8.3 Caveats

`Caveat = {code, severity: "info" | "warn" | "block", message: [Segment, …], affects: [JSON
Pointers relative to the output's root]}`. Codes are a stable, documented enum; core codes are
unprefixed and pack codes are namespaced (`onco.DRIVER_ANNOTATION_PIN`). Every code, core or
pack, declares its severity. The core set:

| Code | Severity | Raised when |
|---|---|---|
| `UNKNOWN_EXCLUDED` | warn | A cohort, denominator or analysis excluded units or rows because they were UNKNOWN |
| `INVALID_EXCLUDED` | warn | An analysis excluded rows or units with invalid values (§5.8) |
| `SCOPE_PARTIAL` | warn | A value depended on closedness restricted to listed scope tuples (flag, §6.3, §6.5) |
| `COVERAGE_PROPOSED` | warn | A value depended on proposed coverage (flag, §6.3, §6.5); the message names the relationship |
| `UNCONFIRMED_SEMANTICS` | warn | A descriptor field that canonicalisation or evaluation read is `imported_default`, `proposed` or `undeclared` (§5.1); the message lists the fields |
| `UNMAPPED_COMPARISON` | warn | A cross-dataset query matched references by name under `unmapped: "allow"` |
| `TIME_ORIGIN_MISMATCH` | **block** | Time-based values were compared across datasets whose time origins are undeclared or differ |
| `CONFOUNDED_WITH_DATASET` | warn | Some between-cohort contrasts could not be estimated because cohort membership coincides with dataset (§7.5) |
| `COHORTS_OVERLAP` | warn | Cohorts in a view share units; no between-cohort value was computed (§7.4) |
| `SMALL_N` | warn | Units analysed or events fell below the analysis's minimums, events per parameter below 10, or expected counts below 5 in a table tested with chi-squared |
| `PH_VIOLATED` | warn | The proportional-hazards test failed; the hazard ratio is an average over time |
| `DRAFT_RELEASE` | warn | The output was computed against a curation session's draft release |
| `SUPPRESSED` | info | Values were suppressed by the disclosure settings (§8.4) |
| `NOT_ESTIMABLE` | info | A number other than a suppressed one could not be estimated (§8.2) |
| `LIFT_DIFFERS` | info | The other lift rule would change some units' results; the message gives the count (subject to §8.4), in the pack's wording where a pack provides one |
| `POOLED_ACROSS_DATASETS` | info | The output reports a value pooled across datasets |

MCP tool descriptions MUST tell clients that `warn` and `block` caveats have to be shown to the
user. A `block` caveat means the output is returned for inspection but MUST NOT be presented as
an answer.

### 8.4 Disclosure settings

- Each dataset descriptor has `disclosure: {min_cell_count, allow_row_ids}`: `min_cell_count`
  (*k*) is `null` (off) by default and at least 2 when set; `allow_row_ids` defaults to true. Being
  part of the descriptor, the settings are part of the release. A deployment may set a floor for
  `min_cell_count`. The effective *k* of an output is the largest of the floor and the settings of
  every dataset the output draws on; it is part of the ids and references of §7.6 and §8.1, and
  results and cohort counts state it.
- With *k* set, a disclosure pass runs on every output after it is computed. A suppressed value
  becomes `null` with `not_estimable` reason `suppressed` (in `population`, its pointer is listed
  in `suppressed`), and the output carries `SUPPRESSED`. The pass applies these rules:
  - **Linked counts.** In each linked set, a count from 1 to *k* − 1 is suppressed, and if exactly
    one count of the set is suppressed, the smallest non-zero other count (the first in the set's
    listed order on a tie) is suppressed too. The linked sets are: `n_true`, `n_false` and
    `n_unknown` (with the size of the unit table shown); `analysed.n` and `excluded_units` (with
    `n_true`), and likewise each entry of `analysed.variables`; a proportion's numerator and its
    complement (denominator minus numerator); the cells of each row and each column of a cohort ×
    category table; a column's observation-state counts (with `n_true`, or with `n_rows` in
    catalogue statistics); a column's histogram bins or category counts (with its PRESENT count);
    and `lift_differs` alone. A count of 0 is shown. The pass runs after categories are pooled and
    histogram bins are merged (below), covers the whole output and repeats until nothing changes: a
    count suppressed in one place is suppressed wherever the same count appears (e.g. `n_true` and
    `size.numerator`); a suppressed total of a linked set counts as a suppressed member of that set;
    and a breakdown is `null` whenever its total is suppressed (`unknown_by_reason` and
    `unknown_by_leaf` with `n_unknown`, `analysed.excluded` with `excluded_units`, observation-state
    counts with `n_true` or `n_rows`). A value that is not estimable keeps its reason: the pass
    suppresses only values that were computed.
  - **Breakdowns.** A per-reason or per-leaf map with any count from 1 to *k* − 1 is replaced by
    `null` as a whole. Categories with any cell from 1 to *k* − 1 are pooled into one
    *suppressed categories* row per cohort; if that row still has such a cell, the table is
    suppressed.
  - **Derived statistics** (proportions, intervals, effect sizes, tests; for survival analyses
    and models, see below) are suppressed with any count they are computed from. Suppressed tests leave the multiple-testing family, and the
    result says how many did.
  - **Distributions.** Histograms take their bin edges from the analysis parameters, or divide
    the column's declared `range` (§5.4) into *B* equal-width bins (*B* set by the analysis
    version; 10 in catalogue statistics); never from the data. Without either they are refused.
    Bins are [eᵢ, eᵢ₊₁), the last closed on the right; values outside the edges fall into open
    *below* and *above* bins. Repeatedly, the bin with the fewest units among those with 1 to
    *k* − 1 units (the leftmost on a tie) is merged, together with any empty bins between them,
    with the nearest non-empty bin on the side where that bin holds fewer units (the left on a
    tie), until no bin has 1 to *k* − 1 units or one non-empty bin remains; empty bins are
    otherwise kept. Minima and maxima are not reported; medians and quartiles are reported as the merged bin
    that contains them. Catalogue statistics follow the same rules, and pool categories with fewer
    than *k* units.
  - **Survival curves** are reported only at grid times given by the analysis parameters (a grid
    is required under *k*, and landmark times must be grid times). The grid intervals are (0,
    g₁], (g₁, g₂], …; repeatedly, the leftmost interval in which some cohort has 1 to *k* − 1
    events is merged with the following interval (the last interval with the preceding one);
    intervals without events are kept. A remaining grid time is reported only if no cohort has 1 to
    *k* − 1 units at risk there. Curve values, pointwise bounds, and landmark estimates with
    their bounds are reported only at reported grid times. Medians and their bounds are reported
    as the reported grid interval that contains them, and suppressed outside the reported range;
    the difference in medians and its bounds are suppressed. The log-rank test is suppressed when a
    cohort it uses has 1 to *k* − 1 units or 1 to *k* − 1 events. Censoring marks, and at-risk counts below
    *k*, are not reported.
  - **Models** (Cox fits) are reported only if no cohort or covariate level in the fit has 1 to
    *k* − 1 units or 1 to *k* − 1 events; otherwise their values are suppressed.
  - Caveat messages that carry counts (`LIFT_DIFFERS`) follow the count rules.
- Identifier columns (§5.4) never have value distributions in catalogue statistics, whatever the
  settings.
- `allow_row_ids: false` refuses the `ids` leaf, the `summary.members` analysis and predicates on
  identifier columns, and requires `min_cell_count`. `summary.members` is also refused for
  cohorts smaller than *k*.
- **Limits.** These settings reduce casual disclosure. They do not prevent inference across
  repeated queries (for example by differencing two counts), and they are not a privacy
  guarantee. Restricted data needs access control, which is outside v1 (§1.2).

### 8.5 Charts

Charts are Vega-Lite specifications generated on the server from result values. Their data is
inline (`data.values`), copied from the result after the disclosure pass; they contain no
transforms that compute numbers, no expressions built from data, and no URLs. Clients render
them with a CSP-safe interpreter and a loader that makes no network requests (§14).

### 8.6 Refusals

A refusal is `{code, path, message: [Segment, …], alternatives: [Segment, …], limit?: {name,
max}, counts?: [cohort counts]}`. `code` is a stable enum of `UPPER_SNAKE_CASE` codes, defined
with the schemas (pack codes are namespaced, `<pack id>.<CODE>`); `path` is a JSON Pointer into
the document as written, or `null` (where a problem lies inside a value a parameter supplied, the
pointer is that of the `"$name"` string, and the message names the parameter; where a key cannot
be written in a pointer because it is not Unicode text, the pointer is that of its object);
`alternatives` lists what *is* available (A3); `limit` names the limit hit (§14); `counts`
carries cohort counts with their ids where a refusal reports numbers (e.g. the overlap of §7.4);
they are issued, and recorded, as `count_cohort`'s are.
Refusals of a descriptor, such as one `propose_descriptor` receives, point into that descriptor
in the same way, and rules that span the descriptors of a release point into the list of them
(`UNKNOWN_DESCRIPTOR` where a descriptor names one the release does not hold).
`validate_document` returns every refusal, sorted by (`path`, `code`), with these bounds:
refusals with the same `path` and `code` are merged; a `null` and a refused parameter
reference are reported wherever they are, except inside a `params` refused as a whole; no
reference is looked up while `params` is refused or is not an object, but a malformed reference
is refused wherever references are substituted, whatever `params` is; the other checks report
nothing inside a value already refused (a `null`, a refused parameter reference, `params`
refused as a whole, or an array or object with more members than it may have) or in a clause
whose `kind` was refused, and nothing that follows only from a `null` (an object lacking that
member, or a cohort's datasets or `unmapped`, or a view's cohorts, being unknown); and after the
first 1,000, one `LIMIT_EXCEEDED` refusal with `path` `null` says how many more were found. The other tools fail
with the first refusal (HTTP 422, or an MCP tool error).

---

## 9. Analysis registry

### 9.1 Entry

Each analysis is a descriptor (`kind: analysis`, §5.1) plus an implementation:

```jsonc
{
  "kind": "analysis", "id": "survival.km", "version": "1.0.0",
  "label": "Kaplan–Meier survival",
  "definition": "Kaplan–Meier estimate per cohort; log-rank test; medians and landmark survival with intervals; difference in medians and unadjusted hazard ratio, with intervals, versus the reference cohort.",
  "fields": {
    "requires": [
      { "role": "endpoint", "kind": "endpoint", "on": "unit" },
      { "role": "cohorts", "min": 1, "max": 6 }
    ],
    "params":  { /* JSON Schema, generated from a Pydantic model */ },
    "returns": { /* JSON Schema of `values` */ },
    "methods": { /* named methods, as in §9.5; "implemented directly" where library defaults differ */ },
    "library": { "name": "lifelines", "version": "…" },
    "assumptions": ["independent censoring", "independent groups"],
    "uses_reference": true,
    "assumes_independent_groups": true,
    "cross_dataset": { "method": "log-rank test and Cox fit stratified by dataset; curves and medians pooled" },
    "randomness": { "seeded": true, "replicates": 2000 },
    "caveats": ["UNKNOWN_EXCLUDED", "INVALID_EXCLUDED", "UNCONFIRMED_SEMANTICS", "SMALL_N", "PH_VIOLATED",
                "NOT_ESTIMABLE", "SUPPRESSED", "COHORTS_OVERLAP", "TIME_ORIGIN_MISMATCH",
                "CONFOUNDED_WITH_DATASET", "POOLED_ACROSS_DATASETS", "UNMAPPED_COMPARISON",
                "SCOPE_PARTIAL", "COVERAGE_PROPOSED", "LIFT_DIFFERS", "DRAFT_RELEASE"],
    "min_group_n": 10,
    "min_events": 5
  },
  "curation": {}
}
```

- Analyses are registered only by the core and by packs, through code review. Users cannot
  upload analyses in v1.
- `requires` entries are `{role, kind?, on?, datatype?, min?, max?, predicate?}`, with `kind` one
  of `endpoint`, `column` or `table`, distinct roles and `min` ≤ `max`; `predicate` cites a pack's
  requirement predicate as `"<pack id>.<name>"` (§10.1).
- `library`, `randomness`, `min_group_n` and `min_events` are optional; every other member of
  `fields` is required, `cross_dataset` included (§7.5).
- `"on": "unit"` means the endpoint must be on the unit table itself, not reached by a lookup:
  with samples as the unit and a patient-level endpoint, a patient with several samples would be
  counted several times.
- `caveats` lists every code the analysis can raise, exhaustively.
- `cross_dataset: null` means the analysis cannot run across datasets (§7.5).

### 9.2 Several values per unit

A column is **multi-valued** for a unit when it is below the unit or list-valued.

- Analyses that declare `assumes_independent_groups` use one value per unit, so a view MUST give
  an `aggregate` for each multi-valued column it uses: `count`, `max`, `min` or `mean` (numeric
  aggregates), or `some` or `every` with a `values` set (*some row has a value in V*, *every row
  has a value in V*). `max` and `min` on categories require `ordered` permissible values.
- `some` and `every` aggregates are existence questions and follow §6.5 in full, including
  evidence and open scope with `SCOPE_PARTIAL`.
- Numeric aggregates are computed over the rows reached at the last down step of the column's
  path through the children kept at every earlier down step (dropped as in §6.5, step 3, under
  the aggregate's `lift`, default `strict`), pooled: `mean` is the mean over those rows, not a
  mean of means. A unit's numeric aggregate is UNKNOWN, with the corresponding reasons, unless the
  unit is in scope and closed at every down step; if an earlier down step keeps no child, it is
  UNKNOWN (`NOT_COVERED`). When the last relationship's coverage has scope columns, the view MUST
  restrict them to a finite set of values, and the parent row must be listed for all of them. A
  numeric aggregate carries `COVERAGE_PROPOSED` when the coverage of any down step it relied on is
  proposed.
- Row states: rows whose value is UNKNOWN or NOT_ASSESSED make `max`, `min` and `mean` UNKNOWN,
  with their reasons; rows whose value is NOT_APPLICABLE are skipped; `count` counts rows
  whatever their values.
- A closed unit with no pooled rows has `count` 0. For `max`, `min` and `mean` the view gives
  `empty`: a value to use (e.g. `0` for *highest grade, none recorded*) or `"exclude"` (the
  default), in which case the unit is excluded with reason `NO_ROWS`.
- Descriptive analyses count units per category, with a denominator per category (the units for
  which *has a row in this category* is known), so a unit with rows in several categories counts
  in each and the output carries `multi_membership: true`; or, if the view asks for `count:
  "rows"`, they count rows, labelled as rows. Numeric multi-valued columns in descriptive analyses
  need an `aggregate` or `count: "rows"`.
- Aggregates are part of the reference evaluator (§13.3).

### 9.3 Determinism

- **Arithmetic.** DuckDB computes only `SUM`, `COUNT`, `MIN`, `MAX` and `AVG` over integer and
  DECIMAL columns for values that enter a digest. Every other aggregate (any aggregate over
  floating-point values, standard deviations, variances, quantiles) is computed in Python: sums
  with correctly rounded summation (`math.fsum`), statistics with exact algorithms over values in
  canonical order.
- **Order.** Before every library call, rows are sorted by (cohort position, dataset manifest
  hash, the RFC 8785 serialisation of the unit key, the row key). Set-like arrays in outputs
  (caveats, affected paths, reasons) are sorted. Category arrays in `values` follow the declared
  permissible order, else canonical value order.
- **Fits.** Model fits use single-threaded linear algebra pinned to a fixed code path, fixed
  starting values, and convergence tolerances set by the analysis version.
- **Randomness.** Resampling uses the number of replicates set by the analysis version, seeded
  from the computation id (§7.6), so that raising a disclosure floor does not change resampled
  values that are still reported.
- **Rounding.** Before hashing, floating-point fields (only) are set to 0 if their magnitude is
  below 1e-12 and then rounded to 10 significant digits; an analysis may declare coarser
  precision for specific values. Integers are never rounded.
- **Scope.** The invariant (§7.6) is guaranteed within a deployment and on CI's reference
  platform. A difference across platforms is a bug to fix with stricter determinism, never
  accepted silently. The thread-count determinism tests use a fixture of at least one million
  rows, because smaller inputs never run in parallel.
- A dependency upgrade that changes a golden digest requires bumping the affected versions
  (§7.6).

### 9.4 Applicability

`applicable_analyses(dataset, unit)` matches each entry's `requires` against the dataset's
descriptors and returns, for each analysis, `available`, `unavailable` (naming the missing
requirement) or `available_with_caveats` (e.g. an endpoint whose event coding is
`imported_default`). The dataset page and `describe_dataset` both show this; before the registry
exists (M1–M2), they show an empty list.

### 9.5 Core analyses (v1)

**Estimability.** Before computing, each analysis checks its inputs and marks values not
estimable (§8.2) instead of computing them. Where several rows apply to one value, the first of
them gives its reason. A test is chosen by the number of cohorts it uses (Welch's *t*,
Mann–Whitney or Fisher's 2×2 test for two):

| Condition | Values not estimable |
|---|---|
| A cohort with no units, or no units for which the variable is known | its estimates and every pairwise contrast with it (`no_units`); omnibus tests (chi-squared, Fisher, Welch's ANOVA, Kruskal–Wallis, log-rank) use the other cohorts and record the positions they used, and are not estimable (`no_units`) if fewer than two remain |
| A cohort with no events (survival) | its difference in medians and its hazard ratio (`no_events`): its units are left out of the Cox fit (the limit of the full fit as its coefficient tends to −∞) but stay in `analysed`, its curve and the log-rank test (unless the next row applies); if it is the reference, every value of the Cox fit (`no_events`) |
| A log-rank test whose variance is 0 (e.g. all units censored, or no event time at which two of its cohorts are at risk) | that test (`zero_variance`) |
| Fewer than two values, or zero variance, in a group (numeric) | its standard deviation, every *t*-test or ANOVA that includes it, and the Welch–Satterthwaite interval of every mean difference that includes it (`zero_variance`) |
| Every analysed value tied (numeric) | the Mann–Whitney and Kruskal–Wallis tests over them (`zero_variance`) |
| A zero denominator | the proportion and every contrast built on it (`zero_denominator`) |
| A numerator of 0 in either proportion of a risk ratio | the ratio and its interval (`no_events`) |
| A contingency table that, after removing cohorts with no known units and dropping all-zero category columns (done before choosing Fisher or chi-squared), has fewer than two columns | its test (`degenerate_table`) |
| A covariate level with no events in a Cox fit | that level's term (`separation`): its units are left out of the fit but stay in `analysed` |
| A Cox term whose estimate is infinite, detected after fitting as R's `coxph` does (at convergence, \|(U·I⁻¹)ⱼ\| > ε and > √ε·\|βⱼ\|, with ε the convergence tolerance) | that term's estimate and interval (`separation`) |
| A fit that does not converge | every value of the fit (`not_converged`) |
| A survival curve that never falls below 0.5 and does not end at exactly 0.5 | its median and the differences that use it (`not_reached`) |
| A survival curve at 0 | its pointwise log-log bounds from that time on (`zero_denominator`), which the median's interval rule skips, as R's `survfit` does; bounds where the curve is 1 equal 1 |
| A landmark time or grid time after the cohort's last follow-up | that cohort's landmark estimate, or curve value, and its bounds there (`beyond_follow_up`) |
| A bootstrap bound whose order statistic is infinite | that bound (`not_reached`) |

**Other rules.** NOT_APPLICABLE cells are excluded from the column comparisons of
`compare.columns` and counted (§6.6); in existence predicates they are FALSE (§6.4) and so known.
Benjamini–Hochberg correction covers the primary tests of a `compare.columns` or
`compare.existence` view only; tests that cannot be computed leave the family and are counted.
Quartiles use R's default definition (type 7). A bootstrap interval at level 1 − α is formed from
the ⌈B·α/2⌉-th and ⌈B·(1 − α/2)⌉-th order statistics of the B replicates, ordered with −∞ below
every finite value and +∞ above; a replicate whose statistic is undefined (e.g. both medians
unreached) counts as −∞ for the lower bound and +∞ for the upper bound.

| Id | Returns |
|---|---|
| `summary.distribution` | Per column, per cohort: for categories, units per category (proportions over units whose value is known; per-category denominators for multi-valued columns, §9.2); for numbers, n, mean, standard deviation, median, quartiles, minimum and maximum, and a histogram (bins as in §8.4, from the parameters, the column's declared range or, without disclosure settings, *B* equal-width bins between the minimum and maximum); observation-state counts. Descriptive only |
| `summary.members` | The unit keys of exactly one cohort, sorted, paginated with `offset` and `limit`; subject to §8.4 |
| `compare.columns` | Categories: chi-squared test of independence, or Fisher's exact test for 2×2 tables; per-category differences in proportions versus the reference, with Newcombe hybrid score intervals. Numbers: primary test Welch's *t* (two cohorts) or Welch's ANOVA (more); secondary test Mann–Whitney (exact when both groups have fewer than 50 values and no ties, otherwise the normal approximation with continuity correction, as R's `wilcox.test`) or Kruskal–Wallis, reported unadjusted; difference in means versus the reference (Welch–Satterthwaite interval) and in medians (bootstrap interval, 2000 replicates) |
| `compare.existence` | Per existence predicate (e.g. *some grade ≥3 adverse event*, *a TP53 mutation*): the proportion per cohort over units for which it is known, with a Wilson score interval without continuity correction; risk difference (Newcombe hybrid score interval) and risk ratio (Katz log interval; see the estimability table for zero numerators) versus the reference; Fisher's exact test for two cohorts, chi-squared for more |
| `survival.km` | Per cohort: Kaplan–Meier curve over left-truncated risk sets (entries from the endpoint's `entry`, §5.8), with log-log (Greenwood) pointwise intervals; median, implemented directly as R's `survival` defines it in `quantile.survfit` (the smallest time at which the curve is below 0.5; where it equals 0.5, to within √ε ≈ 1.49e-8, over an interval, that interval's midpoint; where it ends at exactly 0.5, the midpoint of the time it reached 0.5 and the last follow-up), with a Brookmeyer–Crowley interval on the log-log scale using the same crossing rule; landmark survival at requested times, with intervals. Between cohorts: the log-rank test over left-truncated risk sets {entry < t ≤ time} (the Mantel–Haenszel statistic, summed over datasets when stratified), equal to the score test of R's `coxph(Surv(entry, time, status) ~ cohort + strata(dataset), ties = "exact")`; difference in medians versus the reference, with a bootstrap interval (resampling within cohort, 2000 replicates; a replicate whose median is not reached counts as +∞); unadjusted hazard ratio from a Cox fit (Efron ties), tested for proportional hazards as in `survival.cox`. In a cross-dataset view, the hazard ratio comes from a Cox fit stratified by dataset, like the log-rank test, and curves, medians and the difference in medians are pooled (`POOLED_ACROSS_DATASETS`) |
| `survival.cox` | One joint model, over left-truncated risk sets: cohort membership (versus the reference) and covariates (at most 8 parameters after dummy coding; columns or predicates, one value per unit), Efron ties, Wald intervals; predicates and boolean columns enter as 1 for TRUE and 0 for FALSE; category and string columns are dummy-coded over the levels present among complete cases, against the most common one (ties broken by the smallest canonical value), and the parameter limit counts the dummies; optional stratification (by a column or by dataset); complete cases only, with exclusions counted by reason. Proportional hazards: the Grambsch–Therneau score test implemented directly as in R's `survival` ≥ 3.0 (`cox.zph`, `transform = "km"`), global test; `PH_VIOLATED` when p < 0.05 |

---

## 10. Domain packs

### 10.1 What a pack is

A pack is a Python package with a manifest (`id`, `version` as a PEP 440 version,
`results_version`, required core version) that registers implementations of the extension
points below. Each has a fixed signature, is called at a fixed point, and is consulted only for
the packs listed:

| Extension point | Signature | Called | Packs consulted |
|---|---|---|---|
| **Concepts** | concept descriptors | at registration | all registered |
| **Ontology systems** | a validator per system name, `validate(code) -> bool`; each system is registered by one pack | on descriptor writes | all registered |
| **Descriptor extensions** | a JSON Schema object per descriptor kind | on every descriptor write | the dataset's `packs` |
| **Importer** | `import_source(source: ConfinedPath, options) -> ImportResult` (raw snapshots, parse settings, tables with roles, relationships, coverage, descriptors and proposals); optionally `rebuild(raw, descriptors) -> tables` | operator import and re-import; rebuilds (§12.2) | the pack the operator names |
| **Validator** | `validate_source(source, result) -> [Refusal]`; `validate_descriptors(release) -> [Refusal]` | at import; on every draft change | the dataset's `packs` |
| **Curation proposer** | `propose(release) -> [Proposal]` | after import, and on request | the dataset's `packs` |
| **Leaf kind** | a JSON Schema; `compile(leaf, release, pack_version) -> [Clause]` (§7.3); `summary(leaf) -> [Segment]` | canonicalisation; readbacks | the leaf's pack |
| **Document translator** | `translate(document) -> (aibi document, [{pointer, message}])`, by format `<pack id>.<name>` | `validate_document` with a `format` | the pack named by the format |
| **Analysis** | a registry entry (§9.1) and `run(inputs) -> values`, where the inputs are, per cohort position, the units with the columns, aggregates and endpoint rows the entry requests, materialised by the core; deterministic as in §9.3 | `run_analysis` | the analysis's pack |
| **Requirement predicate** | `predicate(release) -> bool`, cited in `requires` as `"<pack id>.<name>"` | applicability (§9.4) | the predicate's pack |
| **Catalogue facet** | `facet(release) -> {name: [values]}` | catalogue indexing | the dataset's `packs` |
| **Caveat rule** | `rule(release, canonical cohort or view) -> [code]`; static, no access to data | canonicalisation of cohorts and views | the packs involved (§7.6) |
| **Core caveat wording** | a message template per core code | rendering messages | the packs involved; several wordings are shown in pack id order |

- `results_version` is an integer bumped whenever a change to the pack could change outputs:
  leaf expansions, caveat rules or severities, or pack analyses. It is hashed into ids (§7.6), so
  a pack upgrade that changes nothing changes no id.
- A pack importer that reshapes data (the four cBioPortal header rows, a CNA matrix turned into
  a long table) either provides `rebuild` or marks the parse-affecting fields of its tables
  non-editable in drafts (§12.2).
- The extension points are delivered in M1–M3 alongside the core features that call them, each
  tested with a test-only, non-biomedical pack defined in the core test suite (§15).
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
| Mutation, CNA, structural-variant data | Measurement tables below `samples` (CNA as a long table of non-neutral calls with its own coverage; structural variants with one gene per row) |
| Case lists, gene panel matrix, panel definitions | Grouped coverage on each measurement relationship: sample → panel (assignment), panel → genes (groups); whole-exome and whole-genome panels cover all genes |
| Normal samples | Outside the measurement relationships' `parent_scope` |
| `NA`, `[Not Available]`, `[Not Applicable]`, … | `missing_codes` with `imported_default` status |
| OS / PFS / DFS / DSS | Endpoints on `patients`, with the pack's time origins |
| OQL | The `onco.genomic` leaf, compiled to `exists` clauses |
| "Profiled in any sample" | `lift: "assessed"` |
| "Altered in x% of profiled samples" | `compare.existence` over the units for which the predicate is known |

### 10.3 Other packs

None are in v1; a non-biomedical fixture in the core test suite keeps the core honest in the
meantime. A second pack (clinical trials: adverse-event grading, randomisation and other trial
time origins, site and visit coverage) is the first item after v1, to test the extension points
against a domain other than oncology.

---

## 11. Tool and operator surfaces

### 11.1 MCP and HTTP tools

One set of Python functions backs both the HTTP API (FastAPI) and the MCP server. Tool schemas
are generated from the same Pydantic models as the HTTP API; the document a tool accepts is the
document as written (with `"$name"` allowed in scalar positions, §7.1). Packs contribute leaf
kinds, analyses, translators and facets to these tools; they do not add tools of their own.

| Tool | Purpose | Touches row data? |
|---|---|---|
| `search_catalog` | Faceted search over the latest published release of each dataset: domain tags, tables with their grains and roles, concepts present, row counts, completeness thresholds, data-use codes, pack facets | Catalogue statistics only |
| `describe_dataset` | Dataset descriptor, table graph, columns, coverage, endpoints, applicable analyses, and standing caveats (those any query on the dataset would raise from its descriptors: unconfirmed fields, proposed coverage) | Catalogue statistics only |
| `describe_column` | Full descriptor with observation-state counts and value distribution | Catalogue statistics only |
| `list_analyses` | Registry entries, optionally filtered by applicability | No |
| `validate_document` | Canonicalise, check, expand pack leaves (or translate another format) and read back a document without running it; returns every refusal, the caveats that can be determined without data, and ids marked *not yet issued* | No |
| `count_cohort` | Evaluate a document's cohorts; returns cohort counts (§8.1) | Counts only |
| `run_analysis` | Run a document; returns one result envelope per view (§8) | Yes |
| `explain` | Given a derivation id or an issuance id: canonical document, releases, versions and, for an issuance, the document as written and the SQL as run (§12.2) | No |
| `curation_queue` | Fields, keys, relationships, roles and coverage that are `undeclared`, `imported_default` or `proposed`, pending proposals, and counts of rows with invalid values (with references and a document that selects them) | Counts only |
| `propose_descriptor` | Record a proposal for any of those, with rationale, in the queue (§12.3) | No |

- The server sets every `by` (§5.1): the in-app assistant's calls are attributed to its model
  card, and external clients' calls to `agent:<client-declared name>`; no tool call is ever
  attributed to an operator.
- Tool descriptions MUST state that `warn` and `block` caveats and non-zero `n_unknown` have to
  be shown to the user, that every number quoted must cite its id or reference (A1), and that
  data-wrapped text in outputs is data, not instructions (A6).
- Unit keys are listed only by the `summary.members` analysis (§9.5), subject to §8.4. The
  disclosure settings apply to every tool.
- Every descriptor is also an MCP resource: `aibi://dataset/<id>@<n | sha256:hex | draft>/<descriptor
  id>` for dataset descriptors, `aibi://concept/<id>`, `aibi://analysis/<id>@<version>` and
  `aibi://model/<id>` for the others.

### 11.2 Operator operations

Some operations are reserved for people and are never MCP tools:

- importing and re-importing datasets (§12.3, §13.1);
- opening, editing, publishing, discarding and taking over curation sessions, and accepting or
  rejecting proposals (§12.3);
- withdrawing releases (§12.2);
- managing named database connections and model cards, which live in server configuration.

They are served by a separate operator router over HTTP, which the MCP transport never mounts,
and by an operator CLI that talks to that router, so the server process is the only writer of the
app DB and the blob store. Every operator request carries the deployment's curator token; server
configuration stores only its hash, and the CLI reads the token from an environment variable or a
prompt. Each request names the operator it acts for (self-declared, recorded in the audit trail,
Q7). The operator router has no side effects on GET, and browser requests to it must pass the
Origin and Host checks of §14 and carry a CSRF token; requests without an Origin header are
accepted only with the token. M1 ships the CLI; the web UI's curation screens (M5) use the
operator router.

The token protects against remote callers and web pages, not against a local agent with shell
access that can read the token: such an agent acts as an operator.

---

## 12. Architecture

### 12.1 Components

```
   Web UI (React + TS)        External agents (Claude, …)        Operator CLI
        │ HTTP                        │ MCP                           │ HTTP (operator router)
        ▼                             ▼                               ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  aibi server (Python, one process plus worker processes)             │
   │   request protection (§14) on every router                           │
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
- **Releases.** A release is a manifest: the dataset id and the list of its blobs, by role and
  hash, serialised with RFC 8785; the manifest's hash identifies the release. Labels and their statuses live in the app
  DB (§12.3). Releases are never modified.
- **Raw snapshots.** For text files, the raw snapshot is the original bytes; the parse settings
  (§5.3) are descriptor fields. For typed sources (XLSX, XLS, ODS, Parquet, databases), it is the
  source-typed values. Missing codes are matched against each cell's **canonical string form**:
  the text as parsed for text files; for typed values, integers in decimal, other finite numbers
  as RFC 8785 writes them (so −99.0 is `-99`), non-finite numbers as `NaN`, `Infinity` and
  `-Infinity`, booleans as `true`/`false`, dates as RFC 3339 full dates, datetimes in RFC 3339
  with the source's offset or, when the source has none, in ISO 8601 without one, and spreadsheet
  error cells as their error text (e.g. `#N/A`). A non-finite number or an error cell is never
  PRESENT: without a missing code it is UNKNOWN. A datetime without an offset is read as UTC; an
  importer that infers such a column records its `datatype` as `imported_default`.
- **Rebuilds.** Typed values and observation states are a deterministic function of the raw
  snapshot and the descriptors. A draft change to a field that affects parsing (missing codes,
  datatypes, list syntax, derived columns, and parse settings that keep the same set of columns)
  rebuilds the affected tables from the raw snapshot, through the pack's `rebuild` for tables a
  pack importer reshaped (§10.1); other descriptor changes reuse the existing table blobs. A
  change that would alter a table's set of columns (such as a different header row or delimiter
  producing other columns) is a re-import (§12.3) and is refused in drafts.
- **Observation states.** Every column that has missing codes or null cells has a companion
  column `<column>__state` holding each cell's state; the value column holds a value only where
  the state is PRESENT. A list column has a parallel list of item states.
- **Deletion.** A manifest is **live** while a label with status *published* refers to it or it
  is an open session's current draft. After every publish, discard, withdrawal and draft change,
  every blob that no live manifest references is deleted, except the manifests of withdrawn
  releases, which are kept alone so that their ids still resolve (to *withdrawn*). Withdrawal
  also purges the release's cached results. Blobs that an import, re-import, rebuild or draft
  change writes or reuses, and those of the release it starts from, are pinned until it commits or
  fails, and a query or analysis pins the manifests it resolved until it finishes; the sweep skips
  pinned blobs and deletes them once they are released if nothing live references them. An output
  whose manifest stopped being live while it ran is not cached.
- **Erasure.** Honouring an erasure request means re-importing from a source without the
  person's rows (a curation session cannot remove rows), then withdrawing every earlier release
  that holds them, deleting their source files from the upload area, and redacting the person's
  keys and values from the app DB: saved documents, proposals and their evidence, the audit
  trail, cached results and issuances. A derivation whose hashed object contains an erased value
  (a key of the person, or a value of theirs in an identifier column, §5.4) loses its whole
  canonical document and keeps only its id, which resolves to *erased*, because a partial
  redaction next to a hash can be reversed by enumerating keys; its cached results and issuances
  are deleted. Redaction runs once no operation pins a withdrawn manifest. Redaction and the
  pruning below are the only mutations the append-only log permits.
- **Derivation log.** Two tables. *Derivations*, keyed by derivation id, hold the canonical
  document, the releases and the versions hashed into the id; they are kept permanently.
  *Issuances*, keyed by issuance id, record each time `run_analysis` or `count_cohort` produced
  an output: the derivation id, the document as written, the SQL as run (or, for a cache hit, the
  issuance whose SQL produced the values), the engine and pack versions and a timestamp.
  Issuances of `count_cohort` may be pruned after a configured period.
- **App DB (SQLite).** The catalogue index, release labels and statuses, curation sessions and
  their audit trail, the proposal queue, saved documents, the result cache (evictable, digested
  content only, §8.1) and the derivation log.
- **Query engine.** DuckDB reading the release's table blobs, configured as in §14. The compiler
  builds queries as SQLGlot expression trees and never concatenates identifiers or values into
  strings; every identifier comes from a descriptor.

### 12.3 Release lifecycle and curation sessions

- **Labels.** Published releases are numbered per dataset; a label is never removed or reused,
  and the next label is the highest ever issued plus one. A label's status is *published* or
  *withdrawn*. Withdrawal applies to a manifest: withdrawing a release withdraws every label that
  refers to its manifest, and a publish or re-import whose manifest has been withdrawn is refused.
  The **latest published release** is the highest-numbered label that is not withdrawn. The ids computed over a draft state resolve to *discarded* after its session ends,
  unless that state's manifest is live.
- **Import.** Importing a dataset publishes its first release, `@1`, with the importer's
  proposals in its descriptors as `proposed` or `imported_default` fields.
- **Re-import.** Re-importing (re-snapshotting) publishes a new release that carries every
  descriptor forward by id: the dataset descriptor verbatim, and every other field unchanged
  unless the importer's new inference differs from its previous inference (the `inferred` value
  in `curation`, §5.1); only then does the field get the new proposal. Carried values are checked
  by the validation gate (§13.2); a failure refuses the re-import. Every field whose value changed
  is listed in the curation queue. A re-import is refused while a session is open on the dataset,
  and when it would publish a manifest identical to the latest published release's. Removing a
  field or a descriptor whose curation entry has an `inferred` value records a **tombstone** in
  the release (descriptor id, JSON Pointer and `inferred` value), which the engine ignores;
  re-import treats a tombstone as a carried field without a value, so the removal stands unless
  the importer's new inference differs from the tombstone's.
- **Proposals.** `propose_descriptor` adds a proposal to the queue. It changes no release; it
  enters a draft only when an operator accepts it, and becomes `asserted` by that operator, with
  evidence naming the proposal and its proposer.
- **Sessions.** A dataset has at most one open curation session. Opening one creates a draft,
  labelled `@draft`, that starts as a copy of the latest published release, and returns a session
  handle. Every change, publish and discard carries the handle and the draft manifest hash it
  expects; a mismatch is refused as a conflict. Taking over a session issues a new handle and
  invalidates the old one. Changes are applied one at a time; each re-runs the structural checks of
  §13.2 and is refused, with counts, if they fail. A session records the release it was opened
  from, and publishing is refused, as a conflict, if that is no longer the latest published
  release. Imports, re-imports, withdrawals and sessions of one dataset exclude each other: each
  is refused while another is running or open.
- **Queries against drafts.** Only documents that pin `@draft` read the draft. Their outputs carry
  `DRAFT_RELEASE` and are not cached.
- **End of a session.** **Publish** turns the draft into the next label, and is refused when the
  draft's manifest equals the latest published release's; **discard** ends the session without a
  release. Every change, takeover and ending is recorded in the audit trail with the operator's
  self-declared name (Q7).

### 12.4 Stack

| Layer | Choice |
|---|---|
| Language and tooling | Python ≥ 3.12, uv, ruff, pyright (strict on `core/`), pytest, hypothesis, import-linter |
| Schemas | Pydantic v2 as the source of truth; JSON Schema and OpenAPI generated from it |
| API and MCP | FastAPI; the official MCP Python SDK |
| Data | DuckDB (CSV, Parquet, and Postgres, MySQL, SQLite and DuckDB sources), python-calamine (XLSX, XLS, ODS), Parquet, SQLite |
| Statistics | lifelines, scipy, statsmodels, and methods implemented directly where §9.5 says so, in worker processes |
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
        schema/      # Pydantic models: identifiers, descriptors, documents, results, caveats, reasons, refusals, pack API
        store/       # blobs, releases, raw snapshots, rebuilds, deletion, sessions, app DB, derivation log
        importers/   # files and database snapshots
        catalog/
        engine/      # reference evaluator; canonicaliser; SQL compiler
        analyses/    # registry and core analyses
        api/  mcp/  operator/  assistant/
      packs/
        onco/        # concepts, descriptor extensions, cBioPortal importer and validator, onco.genomic, analyses
    tests/
      core/          # loads no pack from aibi.packs; may define test-only packs
      packs/onco/
  web/
  fixtures/          # small public datasets: at least one non-biomedical, one spreadsheet, one cBioPortal study
  schemas/           # JSON Schemas generated from core/schema and checked in; a test fails when they are stale
```

---

## 13. Import, validation and testing

### 13.1 Generic import (core)

- **Files:** CSV/TSV (delimiter, header row, skip rows and encoding detected, then recorded as
  `imported_default` parse settings), XLSX/XLS/ODS (each non-empty sheet a table; empty sheets are
  skipped and noted in the curation queue), Parquet.
- **Databases:** Postgres, MySQL, SQLite and DuckDB sources, through named connections from
  server configuration (§14). All tables are read in one consistent snapshot (one transaction) so
  that keys stay consistent. Declared primary and foreign keys and column comments are imported
  with status `imported`; only base tables are read.
- **Names:** table and column names are normalised as in §5.1; renamed columns are noted in the
  curation queue.
- **Proposals**, for files and databases alike: grain, table roles, primary keys, relationships
  (by value containment, as biai's foreign-key detector did), datatypes, list columns, missing
  codes, identifier columns, and coverage for relationships into `entity` and `link` tables
  (§5.6). Everything proposed lands in the curation queue; nothing proposed is `asserted`.

### 13.2 Validation as a gate

The gate runs at import, at re-import and on every change to a draft (§12.3). Structural errors
stop the import or refuse the change:

- unparseable files;
- duplicate or null primary keys, and violations of one-to-one cardinality;
- foreign keys that reference missing parent rows;
- coverage, assignment and group tables that reference unknown parents or groups, or contain
  nulls in their named columns;
- PRESENT values outside a relationship's `record_filter`, and NOT_APPLICABLE cells in its
  filtered columns;
- colliding identifiers;
- a dataset whose declared `packs` omits a pack whose extensions its descriptors carry.

For proposed rather than declared keys, the proposal is dropped with its evidence instead.
Semantic gaps do not stop the import; they become `undeclared` or `imported_default` fields in
the curation queue: missing units, undeclared coverage, unknown missing codes, child rows outside
their parent's listed coverage (kept, since they are evidence, §6.5), and endpoint rows with
invalid values (§5.8). Pack validators (§10.1) run at the same points; the oncology pack's mirror
the cBioPortal validator.

### 13.3 Reference evaluator

The reference evaluator is a pure-Python, row-by-row implementation of §6, of the aggregates of
§9.2, and of canonicalisation (§7.6), with no SQL. It is written before the SQL compiler (M2),
extended with aggregates in M3, and is the executable definition of the semantics. The SQL
compiler is tested against it: property tests (hypothesis) generate small random datasets, with
random coverage of every form (direct, grouped, scoped, covering all), parent scopes that
evaluate to TRUE, FALSE and UNKNOWN, missing codes of every kind, list items, and null and
dangling keys, together with random documents, and require identical truth values, reasons,
flags and counts.

### 13.4 Tests that encode the principles

- **Three-valued logic:** for any cohort predicate `C`, `n_true(C) + n_false(C) + n_unknown(C)`
  equals the size of the unit table; `not(not C) ≡ C`; `C` and `not C` never share a unit;
  `known(C)` selects the same units as `C ∪ not C`, with the same flags (where `C` is UNKNOWN,
  `known(C)` is FALSE and `any(C, not C)` is UNKNOWN); a direct reference below the unit, the
  multi-step `exists` over its path and the nested form have one canonical form (§6.1).
- **Scenarios of §6.5**, each as a test: unassessed samples, missing grades, blood normals,
  patients with no samples, step conditions under `assessed`, participants with no enrolments,
  `every` over no rows, `every` with scope columns, `min_count`, UNKNOWN parent scopes under
  coverage `all`, scope columns mentioned inside `any` (refused), deeper paths under `assessed`,
  no remaining child whose conditions are TRUE, two nested questions in one `where`, record filters
  under `every`, flags carried through nested questions, `covered` in each of its cases.
- **Canonical form:** no user-chosen name survives phase 1; random renames, reordered top-level
  cohorts, reordered object keys and equivalent syntax (`!=` versus `not` on single-valued
  leaves, `op` versus `range`, single-member combinators, repeated clauses, and the direct,
  multi-step and nested forms of an existence question) give identical ids **and identical
  digests**; canonicalising a canonical form changes nothing.
- **Refusals**, each asserting the refusal's code, path and alternatives: unknown column,
  constant of the wrong type or outside permissible values, unconvertible units, a range on an
  unordered column, out-of-filter existence query, scope columns mentioned outside top-level
  `values`, ambiguous path, a quantifier list of the wrong length, unmapped cross-dataset
  reference (in a cohort and across a view's cohorts), mixed releases of one dataset, pins to
  withdrawn releases, unmet analysis requirement, overlapping cohorts for a test, a missing
  aggregate for a multi-valued column, an open scope for a numeric aggregate, `ids` or identifier
  predicates without row-id access, and each limit of §14.
- **Digests:** golden documents with checked-in ids and digests; CI fails if either changes
  without a version bump. Repeated runs with different thread counts, on a fixture of at least one
  million rows, give identical digests. Swapping a view's reference changes the result id and
  inverts the hazard ratio (M3).
- **Statistics:** the methods of §9.5 agree with reference outputs from R, computed once with
  pinned calls and options (e.g. `survfit(conf.type = "log-log")`, with medians and their
  intervals from `quantile(fit, 0.5)` rather than `print.survfit`, `coxph(ties = "efron")` and
  `ties = "exact"` for the log-rank equivalence, `cox.zph(transform = "km")`, `wilcox.test`,
  `oneway.test`, `fisher.test`, `chisq.test(correct = FALSE)`, `p.adjust(method = "BH")`, and
  named implementations for the Newcombe and Katz intervals) and checked in, to 1e-10 relative for
  closed forms and 1e-6 for iterative fits. Bootstrap intervals are checked for determinism and
  for coverage on simulated data.
- **Reference evaluator versus SQL compiler:** the differential property tests of §13.3.
- **Provenance:** `explain` works for an id after the result cache is cleared; withdrawn and
  discarded releases' ids resolve accordingly; no deletion removes a blob a live manifest or a
  running operation references; after an erasure, no blob or app-DB row contains the erased key,
  and the erased derivations' ids resolve to *erased*.
- **Lifecycle:** re-import carries every descriptor forward, and a removed proposal stays removed;
  a second session is refused while one is open, and imports, re-imports, withdrawals and
  sessions of one dataset exclude each other; a stale session handle is refused; publishing is
  refused when the session's base is no longer the latest release; labels are never reused; a
  withdrawn manifest is never published again; ids of known source names survive re-import.
- **Disclosure:** each rule of §8.4, on results, cohort counts and catalogue statistics.
- **Security:** path confinement, archive entry checks, refusal of views in database files,
  request protection on every router (Host and Origin checks, CORS off, the token on operator
  routes) and rate limits (§14).
- **Domain boundary:** an import-linter contract forbids `aibi.core` → `aibi.packs`; the core suite
  loads no pack from `aibi.packs`, exercises every extension point with a test-only pack, and
  includes a non-biomedical fixture.
- **Assistant evals:** questions the assistant must answer through the tools, citing ids and
  surfacing required caveats, including prompt-injection attempts in column descriptions,
  permissible-value labels, notes and cell values.

---

## 14. Security and privacy

- **Trust model (v1).** Operators, who hold the curator token, are trusted. Everything else is
  not: imported files and databases, shared documents and links, and every request from an MCP
  client, agent or web page.
- **Request protection.** One middleware protects every router (the HTTP API, the MCP transport
  and the operator router): a Host allow-list (by default the loopback names; configured hostnames
  when exposed), an Origin allow-list (by default the server's own origin), CORS disabled unless
  configured, and rate limits per client. This defeats DNS rebinding against a server on
  localhost. The operator router additionally requires the curator token (§11.2). When the server
  is bound to anything but localhost, TLS is required.
- **Untrusted text (A6).**
  - Data-derived text is carried as marked data (§8.1), rendered as plain text, never as markdown
    or HTML, and never placed in tool names, descriptions or schemas.
  - Readbacks and messages are segment lists whose data tokens are escaped when rendered and
    length-capped (§8.1).
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
  - Archives are checked entry by entry (no absolute paths, no `..`, no links out) and are subject
    to size and decompression-ratio limits.
  - From SQLite and DuckDB files only base tables are read, never views or triggers.
  - DuckDB runs with external access disabled except for allowed directories, with extension
    auto-install and auto-load disabled (the extensions it needs are pinned and bundled), and with
    its configuration locked. A Postgres or MySQL snapshot runs instead in its own importer
    worker, whose DuckDB connection attaches that one named connection read-only and runs only the
    snapshot's generated statements (DuckDB refuses such attachments once external access is
    disabled, and enabling it lifts all file confinement); the query engine's configuration never
    changes.
  - Database sources are named connections in server configuration; a request can name a
    connection but never supply a host or credentials. Credentials never appear in descriptors,
    releases, logs or results; provenance records host, database and schema only.
  - Storage paths are built only from hashes and validated identifiers (§5.1).
- **Resource limits.** Request bodies, strings, lists (at most 10,000 members in a `values` or
  `ids` list), notes and parameters have size limits. The defaults for a document are 2 MiB and
  200,000 JSON values, as written and after substitution; nesting 64 deep; 4,096 characters per
  constant, 10,000 per note and 64 per identifier or name (also inside a compound reference,
  which has at most 256 characters, or 1,108 for a relationship or coverage id with 16 key
  columns); 16,384 characters per JSON Pointer to a value, and 67,108,864 (64 Mi) for the
  pointers to all of a document's values together, both also after substitution; 16 steps per path,
  and 100,000 steps of search for an implicit one (`path_search`, which then asks for a `via`);
  16 columns per unit key or scope; 256 clauses per list; 256 `cohort` leaves per cohort (§7.1);
  256 parameters; 64 datasets per cohort; 16 packs; and 1,000 refusals returned. A structured unit
  key costs several JSON values, so long `ids` lists can reach the value limit before the list
  limit. A descriptor has the same limits on its size (in bytes and JSON values), nesting and
  paths, and 200 characters per name, 4,096 per label, key or other string, 10,000 per text, 16
  columns per key or relationship, 10,000 members per list, value map, missing-code map or
  curation map, and 64 per other list or map (extension members per pack, metadata, event codes,
  record filter columns, requirements, methods, caveats). A descriptor's `parent_scope` is a
  clause, so its constants have a document's limits. Imports have size and decompression-ratio
  limits. Every tool call has a wall-clock limit that covers the analysis stage, enforced by
  running queries and analyses in worker processes that can be killed; each worker has a DuckDB
  memory limit. Categorical levels per analysis (at most 150) and resampling replicates are
  capped. Clients of every router are rate-limited, and the number of open proposals is capped.
  Every refusal names the limit it hit (§8.6).
- **Disclosure.** §8.4, including its limits.

---

## 15. Milestones

The order is agent-first: from M1 an external agent (Claude over MCP) is the primary interface,
and the web UI follows once the semantics are settled. A thin read-only page (catalogue and
dataset descriptors) ships with M1 so there is something to show without an agent. Each
milestone delivers the extension points (§10.1) its features call, and the disclosure rules
(§8.4) for the outputs it introduces.

| # | Deliverable | Exit criterion |
|---|---|---|
| **M0** | Repo skeleton and CI; Pydantic schemas for identifiers, descriptors (including analysis entries and model cards; `observation_window` reserved; `requires` closed until M3), documents (as written and substituted), results, cohort counts, segments, caveats, reasons, refusals and the pack API; JSON Schema export; import-boundary check | Schemas published; CI runs lint, type check, boundary check and tests |
| **M1** | Store (blobs, manifests, raw snapshots and rebuilds, state columns, labels, deletion, withdrawal, erasure); generic importers (files and database snapshots) with proposals and the validation gate, hardened as in §14; release lifecycle and curation sessions with handles; operator router and CLI with the curator token; request protection on every router; catalogue with citable statistics and the catalogue and row-id rules of §8.4; `search_catalog`, `describe_dataset`, `describe_column`, `curation_queue`, `propose_descriptor`, MCP resources; extension points: descriptor extensions, importers, validators, proposers, facets; read-only catalogue page | biai's example spreadsheets and a non-biomedical dataset imported, curated through the CLI to confirmed keys and relationships, and published; an MCP client can find and describe them; a release can be withdrawn without deleting blobs a live release uses; a re-import carries curation forward |
| **M2** | Reference evaluator first; then paths, canonicalisation, ids, derivation log, three-valued SQL compilation with coverage, scope and lift, core leaves, readbacks, count suppression, `validate_document`, `count_cohort`, `explain`; extension points: leaf kinds, translators, caveat rules. Until M3, `validate_document` and `count_cohort` check cohorts only (views are syntax-checked and reported as unchecked, and only cohort ids are returned); until M6, concept references are refused | Scenario and property tests pass on the reference evaluator; the SQL compiler matches it in the differential tests; the canonical-form tests pass for cohort ids and cohort-count digests |
| **M3** | Registry and the core analyses of §9.5, with aggregates (added to the reference evaluator), estimability checks, determinism and the remaining disclosure rules; `list_analyses`; `applicable_analyses`; `run_analysis`; charts; extension points: analyses and requirement predicates | Golden, R-reference, determinism and disclosure tests pass, including the reference-swap test |
| **M4** | Oncology pack: cBioPortal importer (with `rebuild`) and validator, grouped coverage from panels, parent scope for normal samples, `onco.genomic`, `onco.alteration_frequency`, concepts and time origins, endpoint proposer, cbio-lab document translator | TCGA GBM PanCan and one panel study imported. From cbio-lab example 1 on `msk_chord_2024`, the altered-percentage comparison and the survival view are reproduced, plus a mutation-only wild-type comparison written with cbio-lab's `profiled` clause; numbers match where semantics agree, and every difference is explained in `docs/cbio-lab-differences.md`. **The pack's pull request changes nothing outside the pack, its tests and its fixtures** |
| **M5** | Web UI: catalogue, dataset page with table graph and applicable analyses, cohort builder with live counts including unknowns, results with provenance panel; curation, import and withdrawal screens on the operator router | The in-scope subset of biai's e2e scenarios, listed in `docs/ui-scenarios.md` (dashboards and map charts excluded), passes |
| **M6** | Assistant (chat that edits the document); AI-proposed descriptors, keys, relationships, roles and coverage; concept mappings, concept references and cross-dataset queries | Assistant evals pass, including the injection cases; a mapped two-dataset survival comparison, with both cohorts present in both datasets and declared time origins, runs with a stratified log-rank |
| **M7+** | Clinical-trials pack; event tables, observation windows and time-window leaves; re-anchored survival; `onco.oncoprint`; driver annotation (onco); JSON-LD / Bioschemas export | Set when M6 lands |

---

## 16. Open questions

| # | Question | Current leaning |
|---|---|---|
| Q1 | Should aibi converge with cbio-lab (one engine, one DSL) rather than sit beside it? | Keep documents compatible and ship the translator; decide after M4, when the oncology pack can be compared with cbio-lab on the same studies |
| Q7 | Who may confirm proposals? | In v1, whoever holds the curator token, under a self-declared name recorded in the audit trail. Named curators (and therefore user accounts) are a precondition for any public deployment |
| Q9 | Who are the first users: this lab only, or outside groups? | Assumed this lab only. Outside users bring user accounts and Q7's named curators forward |

---

## Appendix A. Decisions

Decisions from the walkthrough (D1–D19) and the review rounds (D20–D187), all 2026-09-24. Each
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
| D7 | Table graph (revised by D60, D109, D123) | Many-to-one and one-to-one only (many-to-many through a linking table); up-then-down paths allowed with explicit readbacks; units need a primary key | Every step is a lookup or an existence question |
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
| D27 | Record filters (revised by D136) | Allowed-value lists on categorical columns; queries accepted only if provably inside; readbacks state the filter | Keeps the refusal rule decidable and the meaning of "any row" visible |
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
| D39 | Record-filter wording (revised by D136) | Unconstrained columns are accepted and implicitly restricted to the filter | Removed a contradiction |
| D40 | Row ids and suppression (revised by D59) | `allow_row_ids: false` requires `min_cell_count` | Narrow cohorts re-identify people without it |
| D41 | Draft release ids (revised by D57) | `<dataset>@draft-<content hash>`, valid in derivations, never citable | D30 needed an identifier |
| D42 | Null foreign keys (extended by D80) | Lookups through a null key are UNKNOWN, reason `NO_PARENT` | Closed a gap |
| D43 | Coverage proposals (revises D5, D26; refined by D68) | Proposed `parents: "all"` only for structural child tables; measurement and event tables stay undeclared; `COVERAGE_PROPOSED` otherwise | A plain-CSV panel import must not read unsequenced genes as wild-type |
| D44 | Lifting and empty sets (refines D9; refined by D101, D150) | At an intermediate step, children are dropped by reason, and an empty set of remaining children is UNKNOWN, never FALSE | Otherwise patients with no samples, or with only unsequenced samples under `assessed`, were counted as wild-type |
| D45 | Names in canonical forms (refines D20) | No user-chosen name survives canonicalisation | Names had leaked into ids three times |
| D46 | Cohort order (revises D36; refines D20) | Analyses with a reference group require an explicit `views[].cohorts` array; otherwise the default is every cohort ordered by id | JSON object order is not reliable; JavaScript reorders integer-like keys |
| D47 | Views across datasets | A view whose cohorts come from different datasets follows the cross-dataset rules | Closes a route around P3 |
| D48 | `!=` (revised by D103) | Canonicalised to a negated membership | `!=` and `not =` gave different answers on NOT_APPLICABLE |
| D49 | Units in criteria | Numeric criteria carry `units`, defaulting to the column's (or concept's) and always shown in readbacks | "age > 60" on a column in days meant 60 days |
| D50 | Several values per unit (refined by D89) | Tests require a per-unit aggregate; descriptive views count units per category, or rows when labelled | Rows of one unit are not independent observations |
| D51 | One definition per rule | Existence and coverage are defined once, as an algorithm; the reference evaluator is its executable form | Two sections had drifted apart |
| D52 | Digest scope (refined by D77) | The digest covers cohorts, population, values and caveats | Cohort sizes and caveats could change unnoticed |
| D53 | Determinism (revises D22; revised by D95) | Order-independent sums, deterministic fits, seeded resampling; rounding as a second line | Rounding alone still flips values near a boundary |
| D54 | Derivation log and withdrawal (refined by D74, D84; revised by D113) | A permanent log of every id; releases can be withdrawn, keeping their identity | `explain` must work after cache eviction; consent withdrawal and erasure must be possible |
| D55 | Pack leaves (refined by D83, D107) | Ids hash the core expansion | Same meaning, same id |
| D56 | Citable counts (refines D15; refined by D99) | `count_cohort` returns cohort ids; catalogue statistics carry release-scoped references | A1 requires a citation for every number |
| D57 | Release identity (revises D41; refines D30; refined by D114) | Hashes use manifest hashes; `@n` and `@draft` are labels | Deployments can't mint the same id for different data; a draft published unchanged keeps its ids |
| D58 | Untrusted text (extended by D82) | Principle A6; credentials never stored in descriptors, releases, logs or results | Imported text and shared notes reach the assistant |
| D59 | Disclosure (revises D12, D23, D24, D40; refines D15; refined by D78, D94) | Settings live in the dataset descriptor (hence the release); a deployment floor is hashed; suppression applies to every tool; `summary.members` lists unit keys; described as risk reduction, not protection | Per-result suppression cannot stop differencing across queries |
| D60 | Paths (revises D7; refined by D109, D123, D149) | Any path that visits no table twice, up or down; direct references are shorthand for nested `exists` | Many-to-many links need down-then-up steps |
| D61 | Cross-dataset queries (extends D31; refined by D91, D105) | Table-concept units, concept references throughout, per-dataset pack expansion, registry-declared cross-dataset methods | The previous rules covered only value leaves |
| D62 | Overlap (revises D25; revised by D92) | Refused only for analyses that assume independent groups | Side-by-side descriptive views of a base and a subset are common and harmless |
| D63 | Curation sessions (refines D4, D30; refined by D79) | One open session per dataset, ended by an explicit publish or discard | Without accounts, two people could otherwise publish conflicting drafts |
| D64 | Observation-state storage (refined by D76) | A companion state column for each column with missing codes or nulls | A numeric column cannot hold `NA`, `N/A` and `Not done` distinctly |
| D65 | Unconfirmed semantics | `UNCONFIRMED_SEMANTICS` replaces `DEFAULT_SEMANTICS` and also covers undeclared fields | Undeclared units or time origins affected results silently |
| D66 | Lift caveat (revises D8) | `LIFT_DIFFERS` replaces `CONVENTION_DIFFERS`; packs supply domain wording | Core caveats must not name a domain (P8) |
| D67 | Survival details (refines D13; refined by D93) | The HR inside `survival.km` is tested for proportional hazards; medians not reached are *not estimable* | D13 applies to every hazard ratio; nothing is extrapolated |
| D68 | Table roles (refines D5, D43) | Tables carry a proposed, confirmable `role`; coverage proposals follow it | A heuristic hidden in the importer would misclassify some tables invisibly |
| D69 | Quantifier names (refines D37) | `some` and `every` | `all` was both a combinator and a quantifier |
| D70 | Milestone exits | M4 reproduces only v1-feasible cbio-lab views and explains every difference; M5 lists its in-scope scenarios | The previous criteria could not be met |
| D71 | Reference evaluator | The executable definition of the semantics from M2; the SQL compiler is differential-tested against it | Prose review kept missing interactions between rules |
| D72 | Parent scope (refines D9; refined by D102) | Outside a table's parent scope, the question is UNKNOWN (`OUT_OF_SCOPE`); an UNKNOWN parent scope counts as in scope | Normal samples belong in neither the mutated nor the wild-type group |
| D73 | Operator surface (refines D15) | Import, sessions, acceptance, withdrawal and configuration are operator operations on a separate router and CLI, behind a curator token, same-origin only, no side effects on GET, bound to localhost by default | Without it, "an agent can never confirm its own proposal" was unenforceable, and any web page could withdraw a local release |
| D74 | Blob store (refines D54; revised by D113) | Releases are manifests over content-addressed blobs; discard and withdrawal remove labels and delete only unreferenced blobs; erasure publishes a corrected release and withdraws the earlier ones | Shared files made discard and withdrawal delete data other releases still use |
| D75 | Import hardening | Path confinement for every importer, no URLs or globs, archive checks, base tables only from database files, hardened DuckDB, named connections only, paths built from hashes | Untrusted files could otherwise read server files or reach the network |
| D76 | Raw snapshots (refines D64; refined by D111) | Releases keep raw snapshots; typed values and states are rebuilt from them when parsing fields change; list items have states | Curation could not re-apply a corrected missing code without the raw tokens |
| D77 | Names and prose out of digests (refines D52) | Structured denominator definitions; `unknown_by_leaf` keyed by canonical clause hashes; rendered text outside digests | Two documents with the same id produced different digests |
| D78 | Identifier columns (refines D24, D59) | An `identifier` flag; without row-id access, predicates on and distributions of identifier columns are refused; queue rows appear as counts | A `value` leaf on a key column bypassed the `ids` refusal |
| D79 | Release lifecycle (refines D4, D19, D30, D63; refined by D112, D114, D118) | Imports publish directly and carry descriptors forward; proposals wait in the queue; unpinned means latest published; drafts only when pinned; one change at a time; sessions can be taken over | Implementers would otherwise guess, and a proposal could silently change every query |
| D80 | Structural checks on curation (extends D42) | Every draft change re-runs the structural checks; dangling keys evaluate as `NO_PARENT` | An asserted key or relationship could otherwise break the data model |
| D81 | Identity scheme (refined by D116, D132) | Normalised identifiers, reserved `__` suffix, release-independent descriptor ids, JSON Pointer field paths, explicit coverage column maps, RFC 8785 manifests, resources for every descriptor | Ids are hashed; two implementations must derive the same ones |
| D82 | Structured text (extends D17, D58; refined by D126) | Readbacks and messages are segments with escaped data tokens; a data wrapper in outputs; charts inline-only without computed transforms or URLs; no remote resources in the UI | Data-derived text reached readbacks, errors and charts unmarked |
| D83 | Pack hooks and results versions (refines D55; refined by D117) | Validators, directory importers, leaf-compiler contract, translators, requirement predicates, facets, caveat rules and wording; grouped coverage; a hashed `results_version` per pack | Otherwise M4 could not ship without core changes, and pack caveat changes altered digests under unchanged ids |
| D84 | Derivations and issuances (refines D54; refined by D120) | The log separates derivations (content, permanent) from issuances (as written, SQL as run); `validate_document` records nothing | One id is issued many times with different names, SQL and versions |
| D85 | Resource limits (refined by D156) | Byte, length, import, wall-clock, level, replicate, rate and proposal limits, with analyses in killable workers | Structural caps alone let a single request exhaust the server |
| D86 | Time origins as concepts (extends D6; revised by D115) | `time_origin` is a concept reference compared by id | Prose origins could never be compared, so every cross-dataset survival result would block |
| D87 | Model cards, derived columns, core concepts, analysis entries (extends D6; refined by D119) | Model cards registered in server configuration; derived columns as declared expressions; the `core:` concepts listed; analysis entries in the envelope | The M0 schemas needed them defined |
| D88 | Caveats on closedness (refined by D129) | `SCOPE_PARTIAL` and `COVERAGE_PROPOSED` apply to every answer that depended on closedness | TRUE answers from `every`, `covered` and counts rested on partial or proposed coverage without a caveat |
| D89 | Aggregates (refines D50; refined by D135) | Aggregates follow parent scope and closedness, refuse open scope, handle row states explicitly, and take an `empty` value for closed units without rows; ordered categories for `max` and `min` | Unrecorded rows were treated as absent, and units with no rows silently dropped out |
| D90 | Estimability (refined by D141) | Any number may be not estimable; no non-finite numbers; estimability checks per analysis; `SMALL_N` on units analysed, events and expected counts | Degenerate inputs produced valid-looking numbers, and infinities could not be hashed |
| D91 | Cross-dataset stratification (refines D61; refined by D140) | Stratified results only where a dataset holds at least two cohorts with data; `CONFOUNDED_WITH_DATASET` otherwise; pooled means ignoring dataset | A test stratified by dataset is 0/0 when each cohort comes from one dataset |
| D92 | Overlap (revises D25, D62) | With `overlap: "allow"`, descriptive values only; tests and between-cohort intervals not estimable | Tests on overlapping groups are invalid whatever the caveat says |
| D93 | Survival methods (refines D13, D67; refined by D142, D143) | Median defined as in R, Brookmeyer–Crowley intervals, log-log curves, a specified bootstrap, landmark survival, Efron ties, one joint Cox model, the R ≥ 3.0 Grambsch–Therneau test | Library defaults differ and move medians on floating-point noise |
| D94 | Disclosure rules (revises D12, D23; refines D59; refined by D145, D146, D147) | Per-output rules for counts, derived statistics, histograms, catalogue statistics, survival curves and models; the largest threshold across datasets | "Anything from which it could be recovered" could not be implemented |
| D95 | Determinism mechanics (revises D22, D53; refined by D144) | No DuckDB DOUBLE aggregates in digested values; canonical row order; sorted arrays; pinned numerics; fixed replicates; rounding to 10 significant digits with an absolute floor; a stated scope | DuckDB's floating-point sums differed between runs, and fixed orders disagreed with each other |
| D96 | Delayed entry (refined by D139) | Endpoints declare `entry`; undeclared entry raises `UNCONFIRMED_SEMANTICS` | Survival from diagnosis in data entered at sequencing is biased by immortal time |
| D97 | Exclusion accounting | Exclusion reasons include NOT_APPLICABLE and INVALID_VALUE; every result has an `analysed` block | P2 requires every exclusion counted by reason |
| D98 | Named methods | One primary test per column type enters BH; interval and test methods named, including Mann–Whitney's exact rule; the R reference set extended | Library defaults differ from R and from each other |
| D99 | Versions and counts in ids (refines D11, D56; refined by D124) | Cohort ids include the semantics version, disclosure settings and pack results versions; cohort counts have digests; refusals report counts with ids; the engine computes cohort fractions, per-category differences and landmark survival | Semantic fixes changed cited counts under unchanged ids, and A1 forbade the arithmetic people need |
| D100 | Scope columns (revises D5 in part; refined by D154) | Scope columns may be mentioned only in top-level `values` conjuncts; `every` must not mention them; partial restrictions require matching listed tuples | An `any` of equalities was treated as open scope and made unassessed genes wild-type |
| D101 | Lift and reasons (refines D9, D44; refined by D128) | `NOT_COVERED` separates coverage from cell-level `NOT_ASSESSED`; `assessed` drops only coverage-derived unknowns and never waives closedness; answers keep the closedness reason | `assessed` treated undeclared coverage as closed, and reasons from cells and coverage were confused |
| D102 | Unknown parent scope (refines D72) | Coverage `all` does not close a parent whose scope is UNKNOWN | Otherwise a sample of unknown type became wild-type without evidence |
| D103 | Negation (revises D48; refined by D127) | Negated membership is a leaf-level `negate`, evaluated per row or item; clause-level `not` negates the quantified answer; they are folded together only for single-valued leaves | `!=` on a multi-valued column meant something different from its nested form |
| D104 | `lift_differs` | Computed by flipping every lift in the canonical cohort at once, comparing truth values | Its definition was ambiguous with several lift settings |
| D105 | Cross-dataset canonical form (refines D61; refined by D137) | A map from each dataset's manifest hash to its clause tree; the concept rule applies to the document as written; `via` per dataset; units default to the concept's | Per-dataset expansions had no defined canonical structure |
| D106 | One release per dataset | A document resolves each dataset to one release; mixing releases is refused | Params could otherwise be read from a release the id does not contain |
| D107 | Readbacks from expansions (refines D55) | Readbacks render the canonical expansion; packs add a labelled summary outside the digest | Readbacks must describe what is computed |
| D108 | Two-phase canonicalisation (revises D34; refined by D123, D124) | Cohorts are canonicalised and hashed first, then views | Views' default order and reference positions need cohort ids |
| D109 | Explicit paths (refines D7, D60; refined by D122, D155) | `via` may revisit a table, and `exclude_self` leaves out the starting row | *The other samples of the same patient* could not be written |
| D110 | Request protection | One middleware on every router (HTTP API, MCP transport, operator router): Host and Origin allow-lists, CORS off unless configured, per-client rate limits; TLS whenever the server is not bound to localhost | Only the operator router was protected, so a web page could reach the others on localhost through DNS rebinding |
| D111 | Raw snapshots of text files (refines D76; refined by D169) | The raw snapshot of a text file is its bytes, and the parse settings are descriptor fields; typed sources keep source-typed values; missing codes match a canonical string form; a change that alters a table's columns is a re-import | Parsed cells could not be re-parsed with other settings, and typed values had no string form to match missing codes against |
| D112 | Re-import carry-forward (refines D79; refined by D170) | Every descriptor is carried forward by id; a field takes a new proposal only when the importer's new inference differs from its previous one (`inferred`); carried values pass the gate; changed fields are queued | A re-import otherwise either overwrote curation or kept values the new data contradicts |
| D113 | Deletion (revises D54, D74; refined by D171, D173) | One rule: after every publish, discard, withdrawal and draft change, blobs that no live manifest references are deleted; withdrawn releases keep only their manifest; erasure also redacts the app DB | Separate rules for discard and withdrawal could delete blobs shared with live releases, and erasure left the person's keys in logs and caches |
| D114 | Labels (refines D57, D79; refined by D168) | Labels are never removed or reused, and each has a status; publishes and re-imports that would change nothing are refused; pins to withdrawn or discarded releases are refused, naming the status | A reused label could make an earlier citation point to different data |
| D115 | Core time origins (revises D86) | The core keeps `core:origin.birth`, `core:origin.entry` and `core:origin.calendar`; clinical origins move to the oncology pack | Diagnosis, specimen collection and treatment start are domain terms (P8) |
| D116 | Coverage per relationship (refines D81) | Each coverage descriptor belongs to one relationship (`cov:` plus the relationship id); relationship ids are qualified by child table, and by role where two relationships share child columns; the dataset descriptor's id is `dataset` | Coverage per child table could not describe a table with two parents, and unqualified ids collided |
| D117 | Pack API contract (refines D83) | Every extension point has a signature, a call point and a rule for which packs are consulted; each is delivered in M1–M3 with the feature that calls it and tested with a test-only pack in the core suite | Implementers needed the contract, and the core suite must exercise every hook without loading `aibi.packs` |
| D118 | Session handles (refines D79) | Opening a session returns a handle; every change, publish and discard carries it and the draft manifest hash it expects; a mismatch is a conflict; a takeover issues a new handle | Two operators, or a stale CLI, could overwrite each other's changes |
| D119 | Model cards and attribution (refines D87) | Model card fields defined; `by` always set by the server; external clients recorded as `agent:<name>`; an accepted proposal becomes `asserted` by the accepting operator, with evidence naming the proposer | Attribution taken from requests could be forged, and an external agent is not a registered model |
| D120 | Result cache (refines D84) | The cache holds only digested content, keyed by result id; everything rendered is produced per issuance; a cache hit names the issuance whose SQL produced the values | Cached rendered output would carry the first requester's document as written into other answers |
| D121 | Disclosure schedule | Each milestone delivers the disclosure rules for the outputs it introduces: catalogue statistics and row ids in M1, cohort counts in M2, analysis outputs in M3 | Outputs would otherwise ship before the rules that govern them |
| D122 | Path encoding (refines D109; refined by D149) | `via` is an array of `{rel, dir}` steps; `quantifier` is one quantifier for every down step or a list with one per down step | Multi-step paths had no way to state each step's quantifier |
| D123 | Nested existence (revises D7; refines D60, D108; revised by D149) | Canonicalisation merges an `exists` whose `where` holds exactly one mergeable `exists` into one multi-step question, the outer clauses becoming conditions on that step | The nested and direct forms gave different answers to the same question |
| D124 | Hashed objects (refines D99, D108) | Every id, key and digest hashes an exactly specified object; the semantics version covers §6, §7.6 and the caveat, disclosure and determinism rules | Implementations could hash different objects and still claim conformance |
| D125 | Descriptor members | The fields of every descriptor kind are listed with their types | The M0 schemas need them |
| D126 | Segments and the data wrapper (refines D82) | Readbacks and messages are lists of `text` and `data` segments, data tokens capped at 200 characters; other data-derived strings are wrapped as `{data}` and marked `x-aibi-data` in the schemas, embedded documents as whole nodes | A6 needs a machine-readable boundary between server text and data |
| D127 | Canonical leaves (refines D103; refined by D149) | Each leaf has exactly the listed members; constants are typed; units are kept as written and converted at evaluation; single-member combinators are unwrapped; `!=` and a `not` around a single-valued leaf toggle `negate` | Equivalent documents produced different ids |
| D128 | Closedness reasons (refines D101; refined by D150, D152) | An intermediate step with no remaining children is UNKNOWN (`NOT_COVERED`); the closedness reason joins every UNKNOWN answer of an unclosed row; `covered` is evaluated in a fixed order; a lifted `covered` requires closedness | Rows with no children were answered with the wrong reasons, and `covered` had overlapping cases |
| D129 | Flags (refines D88; refined by D151, D174) | `SCOPE_PARTIAL` and `COVERAGE_PROPOSED` are flags on truth values, raised as caveats when a unit's cohort-level value carries them; `UNCONFIRMED_SEMANTICS` is determined statically and names the fields | `validate_document` could not predict caveats raised during evaluation |
| D130 | Cohort counts (refined by D200) | `{id, digest, population, size, disclosure, readback, caveats, releases, issuance}` per cohort, `size` being a proportion of the unit table | `count_cohort` had no defined output |
| D131 | Refusals | `{code, path, message, alternatives, limit?, counts?}`; `validate_document` returns every refusal, sorted; other tools fail with the first | Refusals must be machine-readable to be acted on (A3) |
| D132 | Identifiers (refines D81; refined by D175) | An identifier grammar and a normalisation of source names, with collisions resolved in source order | Importers would derive different ids from the same source |
| D133 | Milestone order | Until M3, `validate_document` and `count_cohort` check cohorts only; concept references are refused until M6; `observation_window` is reserved | M2 would otherwise depend on parts of M3 and M6 |
| D134 | Result ids | A result id hashes the whole canonical view: the analysis and its version, the cohort ids, the reference position, the overlap setting and the parameters | Views that differed only in their reference got the same id |
| D135 | Aggregate pooling (refines D89) | Numeric aggregates pool the final step's rows reached through the children kept at every intermediate step; `some` and `every` aggregates are existence questions under §6.5 in full | Means of means, and aggregates that bypassed §6.5, contradicted the cohort semantics |
| D136 | Record filters (revises D27, D39; refined by D153) | A PRESENT value outside the filter is a structural error; each child is evaluated as `W ∧ filter`; filtered columns may appear only in top-level `values` within the allowed values | "Provably inside" was not decidable in general, and the implicit restriction changed what unconstrained queries meant |
| D137 | Cross-dataset parameters (refines D105) | View parameters resolve per manifest; a cohort's id hashes only its own expansions and datasets | A cohort's id depended on the other cohorts in its view |
| D138 | List cells | A list cell that is not PRESENT takes its base result from the state table; a PRESENT list is evaluated item by item | Non-PRESENT list cells had no defined result |
| D139 | Log-rank with delayed entry (refines D96; refined by D162) | The log-rank test over left-truncated risk sets, summed over datasets when stratified, equal to the score test of `coxph(..., ties = "exact")` | Delayed entry and strata had no defined test |
| D140 | Partial confounding (refines D91; refined by D166) | Estimability per contrast: a contrast needs data in a shared dataset; k-sample tests use the cohorts that share datasets, with degrees of freedom from the covariance rank; unidentifiable model terms are dropped | Refusing the whole view lost the contrasts that could be estimated |
| D141 | Estimability table (refines D90; refined by D158, D160, D163) | A table of conditions and the values each makes not estimable; `not_estimable` reasons are an enum | Implementations would otherwise choose different fallbacks |
| D142 | Bootstrap bounds (refines D93) | Bounds are the ⌈B·α/2⌉-th and ⌈B·(1 − α/2)⌉-th order statistics, with undefined replicates counted as −∞ or +∞ | Quantile interpolation and dropped replicates differ between libraries |
| D143 | PH test and median (refines D93; refined by D159) | Implemented directly as in R's `survival`: `cox.zph` ≥ 3.0 with `transform = "km"`, and the median's crossing rule | lifelines' defaults differ from R's |
| D144 | Determinism carve-out (refines D95) | For digested values DuckDB computes only integer and DECIMAL aggregates; a total sort key before library calls; resampling seeded from the computation id, which omits disclosure; thread tests on at least one million rows | Parallel floating-point sums varied between runs, and raising a disclosure floor changed resampled values |
| D145 | Distributions under disclosure (refines D94; refined by D164) | Bin edges never come from the data; small bins are merged; minima and maxima are not reported; medians and quartiles are reported as bins | Data-derived edges and extremes disclose individual values |
| D146 | Linked-set suppression (refines D94; refined by D161) | Complementary suppression within each linked set of counts | A single suppressed count can be recovered from its total |
| D147 | Survival curves under disclosure (refines D94; revised by D157) | Curves on a parameter grid with minimum numbers at risk and events; medians, landmarks and bounds as grid intervals | Step curves reveal individual event times |
| D148 | Caveat table | The caveat table matches the rules that raise each code; `INVALID_EXCLUDED` added; registry entries list their caveats exhaustively | Some rules raised codes the table did not list |
| D149 | Canonical chains (revises D123; refines D60, D122, D127; refined by D176, D187) | Canonicalisation splits every multi-step existence question, and every `value` reference below the current table, into a chain of `exists` leaves with one down step each; nothing is merged. `negate` is written only when true | Merging depended on the order of normalisation and on conflicting settings (lift, quantifiers, `exclude_self`), so equivalent documents got different ids and different answers |
| D150 | Intermediate questions (refines D44, D128; refined by D177) | A question is intermediate when its `where` contains a nested question at any depth; an intermediate `some` with no relevant remaining child is UNKNOWN (`NOT_COVERED`) | Children that fail a step's own conditions, and nesting that could not be merged, counted patients with no assessed relevant sample as negative |
| D151 | Flags through nested questions (refines D129; refined by D178) | A FALSE from `some` and a TRUE from `every` carry the flags of every remaining child; UNKNOWN answers carry their children's flags, and `COVERAGE_PROPOSED` when a closedness reason was added | Flags from deeper steps were lost, so cohorts resting on proposed coverage raised no caveat |
| D152 | Lifted `covered` without children (refines D128) | UNKNOWN (`NOT_COVERED`) under both lift rules | The two rules disagreed although nothing had been dropped |
| D153 | Record filters under `every` (refines D136) | `(not filter) ∨ W` under `every`; the filter applies at every question; NOT_APPLICABLE cells in filtered columns are structural errors | Rows outside the filter became counter-examples |
| D154 | Every tuple (refines D100) | Only a group covering every scope value lists a parent for every tuple | "Every tuple" had no defined set of values for direct coverage tables |
| D155 | Trailing lookups and `exclude_self` (refines D109) | Up steps after the last down step are lookups from its child row; `exclude_self` requires the path's first down step to enter the starting row's table | Conditions and `exclude_self` were rooted at different rows under different readings |
| D156 | Caps on the canonical form (refines D85) | Depth 8 and 64 leaves per cohort, counted on the canonical form | Chains lengthen canonical forms, and the old depth count was ambiguous |
| D157 | Survival curves under disclosure (revises D147; refined by D181) | Landmarks on the grid; leftmost-first merging of intervals in which some cohort has 1 to *k* − 1 events; values only at reported grid times; medians as grid intervals; median differences suppressed; the log-rank test gated by units and events | Probabilities were to be reported as time intervals, and the merging was undefined |
| D158 | Cox estimability (refines D141; refined by D179) | Cohorts without events are `no_events` (the whole fit when the reference has none); covariate levels without events are dropped; infinite estimates are detected as R's `coxph` does | "Every unit has the event" is not separation, and monotone likelihoods passed unnoticed |
| D159 | Median at exactly 0.5 (refines D143) | A curve that ends at exactly 0.5 has a median, as in R's `quantile.survfit` | The estimability table contradicted the median rule |
| D160 | Cohorts without known values (refines D141; refined by D182) | Pairwise contrasts with them are not estimable; omnibus tests use the other cohorts and record which | Two rows of the estimability table contradicted each other |
| D161 | Suppression across the output (refines D146; refined by D180) | Suppression reaches every copy of a count and the breakdowns of a suppressed total, and repeats until stable; the complement is the smallest non-zero count | Suppressed counts could be recovered from other fields of the same output |
| D162 | Rows with time at entry (refines D139) | An entry at or after the time is invalid; with entry at the origin, events at time 0 are at risk at 0 | Such rows were in no risk set |
| D163 | Degenerate inputs (refines D141) | Log-rank tests with zero variance, rank tests over tied values, log-log bounds at a curve of 0, risk ratios with a zero numerator and Welch intervals with zero variance are not estimable, with reasons | They produced NaN or infinite values |
| D164 | Histogram bins (refines D145; refined by D183) | Half-open bins, *below* and *above* bins, *B* bins over a declared range, smallest-first merging, empty bins kept | Merge order, bin closure and bin count changed digests |
| D165 | `analysed` per variable | Units analysed for at least one variable, plus per-variable counts | Views over several columns could not report their exclusions per column |
| D166 | Cross-dataset hazard ratio (refines D140) | Stratified by dataset, like the log-rank test; curves and medians pooled | A pooled hazard ratio could contradict the stratified test beside it |
| D167 | Covariate coding | Predicates and boolean columns as 0/1; categories dummy-coded over the levels present | The same data gave reciprocal hazard ratios under different labels |
| D168 | Withdrawal per manifest (refines D114; refined by D185) | Withdrawal applies to manifests; a withdrawn manifest is never published again; withdrawals, imports and sessions exclude each other; publishing requires the session's base to be the latest release | A manifest could be withdrawn and published at once, and a session could silently revert a re-import |
| D169 | Canonical string forms (refines D111; refined by D187) | Numbers as RFC 8785 writes them; non-finite numbers and error cells never PRESENT; datetimes without an offset read as UTC and flagged | Integral floats, NaN and naive datetimes matched missing codes differently across implementations |
| D170 | Tombstones (refines D112) | Removing an importer proposal leaves a tombstone that re-import respects | Re-import brought back proposals an operator had removed |
| D171 | Erasure (refines D113; refined by D187) | Erasure by re-import; source files deleted; affected derivations keep only their ids, which resolve to *erased* | Curation cannot remove rows, and a partial redaction next to a hash can be reversed |
| D172 | Database snapshots under hardened DuckDB | Postgres and MySQL snapshots run in a separate importer worker; the query engine's configuration never changes | Hardened DuckDB cannot attach them, and enabling access lifts all file confinement |
| D173 | Pins during deletion (refines D113; refined by D185) | Running writes and reads pin their blobs; outputs over manifests that stopped being live are not cached | The sweep could delete uncommitted blobs and blobs in use |
| D174 | `COVERAGE_PROPOSED` only as a flag (refines D129) | Proposed coverage raises no `UNCONFIRMED_SEMANTICS`, only the data-dependent flag | §5.1 and §6.6 disagreed, which changed digests |
| D175 | Identifier collisions (refines D132; refined by D186) | `dataset` is reserved as a table id; collisions take the smallest free suffix; roles differ from column ids | Derived ids could collide |
| D176 | Canonical fixpoint (refines D149) | Steps 4 to 8 of phase 1 repeat until the form stops changing | Folding and deduplication ran before and after unwrapping, so equivalent documents, and clauses reached through a `cohort` leaf, got different ids |
| D177 | Conditions decide FALSE (refines D150) | An intermediate `some` is FALSE only if some remaining child's conditions are TRUE; otherwise UNKNOWN (`NOT_COVERED`) | A child whose conditions were unknown could make a patient negative |
| D178 | Flags of dropped children (refines D151) | A FALSE from `some` and a TRUE from `every` also carry the flags of the children dropped under the lift rule | Answers that relied on dropping children under proposed coverage carried no caveat |
| D179 | Event-free units in Cox fits (refines D158) | Left out of the fit, as the limit of the full fit, and kept in `analysed`, curves and the log-rank test | Dropping only the term compared the other cohorts against a reference that included those units |
| D180 | Totals and pooling under suppression (refines D161) | A suppressed total counts as a suppressed member of its set; histogram bins and category counts are linked with the PRESENT count; categories are pooled before the linked-count pass; ties go to the first listed count; values that are not estimable keep their reasons | Counts that summed to a suppressed total revealed it, and the order of pooling changed results |
| D181 | Gates on small counts (refines D157) | Grid times, log-rank tests and models are withheld only for 1 to *k* − 1 units or events, among the cohorts they use | Zero counts suppressed the values the estimability rows keep |
| D182 | Estimability precedence (refines D160) | The first applicable row gives the reason; a test is chosen by the number of cohorts it uses | Overlapping rows gave one value several digested reasons |
| D183 | Merging past empty bins (refines D164) | A small bin merges with the nearest non-empty bin, absorbing the empty bins between them | Empty neighbours conflicted with keeping empty bins |
| D184 | Small exclusion counts under disclosure | Accepted: under *k*, 1 to *k* − 1 excluded units for a variable suppress its `n` and so its derived statistics | With exact counts no weaker rule hides the small count; `min_cell_count` is off by default |
| D185 | Pins and exclusion (refines D168, D173) | Pins cover the blobs an operation writes or reuses and its base release; imports, re-imports, withdrawals and sessions of a dataset exclude each other; erasure's redaction waits for pins | Reused blobs were unpinned, and a withdrawal could land in the middle of a re-import |
| D186 | Ids across re-imports (refines D175) | Source names seen before keep their ids; other names are assigned around them | Suffixes shifted when colliding names were added or reordered, moving curation to other columns |
| D187 | Consistency rules from the fourth review (refines D149, D169, D171) | An `exists` path that ends with an up step needs conditions; the canonical `lift` is written on every intermediate question; erasure covers the person's keys and identifier values; only an importer's inference flags naive datetimes; manifests record their dataset | Each closed a gap in rules added in v0.7 |
| D188 | Numbers are values (M0) | An integral number is an integer however it is written (`2.0` is 2), and one beyond ±(2^53 − 1) is refused | RFC 8785 writes them alike, so accepting both spellings with different meanings would give one canonical form two meanings |
| D189 | Text and parameter values are verbatim (M0) | `notes`, `note` and `drafted_by` are never substituted, and parameter values are neither substituted nor unescaped | Notes are never interpreted (A6); scanning values that were substituted would make substitution recursive |
| D190 | Bounded validation (M0) | Documents are capped in nesting depth, JSON values and the length of the paths to them, one by one and together, as written and after substitution; refusals are merged by (path, code); schema problems are not reported inside refused values or where they only follow from one, while nulls and refused references are reported where they are, except inside a refused `params`; and refusals are capped at 1,000 | A small document could otherwise make validation take minutes and gigabytes: a parameter used many times, or values that are wrong everywhere |
| D191 | Ids across re-imports by occurrence (refines D186) | The *k*-th occurrence of a name keeps its *k*-th previous id; ids have at most 64 characters | Spreadsheets repeat and omit headers, and one id per name moved ids between columns |
| D192 | Cross-dataset rules checked on load (M0) | The unit, concept references and `via` by dataset are checked without a release | They depend only on the document, so waiting for resolution would only delay the refusal |
| D193 | Descriptor refusals and release rules (M0) | Descriptors are loaded like documents, with refusals that point into them; rules that span a release's descriptors (unique ids, one dataset descriptor, roles) are checked on the list | Agents propose descriptors (§11.1), so their errors must be as precise as a document's |
| D194 | Values compared as JSON values (M0) | Event codes and constants compare as JSON values: numbers by value, strings and booleans apart; permissible values and record-filter values are strings | Values reach the core from JSON, where 1 and 1.0 are one number, and category constants are strings (§6.4) |
| D195 | Undeclared by absence (M0) | `parents` and `entry` are undeclared when absent; neither has an `"undeclared"` value | A value would need a curation status, and `undeclared` only reports a field without a value |
| D196 | Typed declared ranges (M0) | A declared range has its column's type; datetimes are compared in UTC | Text order is not time order across offsets, and a range of another type cannot bound the column |
| D197 | Who sets a status (M0) | Only an operator asserts, only an importer imports, and proposals come from models, agents and importers | A cheap check that a status claims no more than its source (A5) |
| D198 | JSON-safe descriptors (M0) | Descriptors built in code hold only values JSON text carries unchanged: Unicode text, finite numbers within ±(2^53 − 1), integral numbers as integers. The limits on size (in bytes and JSON values), nesting and paths apply to a descriptor's JSON text and are checked when it is loaded | Every descriptor round-trips through its JSON text, and manifests hash that text |
| D199 | Whole releases (M0) | A release holds every descriptor its descriptors name, a coverage's `parent_scope` included (with the scope columns of its `covered` leaves), and every `core:` concept they name is a core concept of the right sort; relationships and coverage parent columns lead to the parent's declared key; coverage tables have role `coverage` and are in no relationship; record filters are on `category` columns; an endpoint's table has a key and its time and entry columns are time offsets; derived columns form no cycle; packs with extensions are listed in `packs`. A rule whose inputs are undeclared (a key, a role, `packs`) is not checked, and each rule is checked as far as the release allows | These need no data, so they are checked when a release is assembled, before the gate's data checks (§13.2) |
| D200 | Result invariants on construction (M0) | Result envelopes and cohort counts check their own contract: per-position arrays, `n` + `excluded_units` = `n_true`, caveats' `affects` resolve in the digested parts; `NOT_ESTIMABLE`, `LIFT_DIFFERS` and `DRAFT_RELEASE` are carried exactly when their conditions hold, and `SUPPRESSED`, `UNKNOWN_EXCLUDED` (whenever `n_unknown` is not 0, a suppressed one included), `INVALID_EXCLUDED`, `COHORTS_OVERLAP` and `CONFOUNDED_WITH_DATASET` whenever theirs do (`SUPPRESSED` also covers pooling and merging, which leave no `null`); and these rules of the disclosure pass, which need no data, hold: nothing suppressed without a `min_cell_count`, no shown count or count of a shown breakdown from 1 to *k* − 1, no linked set that shows its one suppressed member through a non-zero other, variables included, and with `n_true` shown `n` and `excluded_units` shown or suppressed together; no cohort count whose size shows a complement from 1 to *k* − 1 beside a non-zero numerator; a cohort count's size (a result envelope states none, so its accounting waits for its size in M3) accounts for its counts as the pass leaves them (each suppressed one counts at least one unit, one suppressed beside two zeros is from 1 to *k* − 1, two suppressed beside a shown count are small or one small and the smallest non-zero other, and with *k* = 2 three suppressed are one each); `analysed.n` is at least each variable's `n` and at most their sum, so no variable excludes fewer units; `lift_differs` is at most the size, and a suppressed one needs a size of at least 1. Cohort counts state their `min_cell_count` so that these can be checked. Until M3 types `values`, proportions there are checked for their `not_estimable` maps only. Outputs are checked when built, nested outputs and `model_copy` with `update` included; `model_construct` bypasses validation, and a dump then refuses anything that is not a JSON value JSON text carries unchanged, held in outputs and in containers of Python's own types, an enumeration member counting as the data of its `str`, `int` or `float` mixin, which must be its value, and no two keys of an object written as the same text | Agents can rely on the contract of any output the server returns |
| D201 | Pack API names (M0) | The importer hook is `import_source` (`import` is a keyword); ontology validators are keyed by system, each system registered by one pack; leaf kinds, translator formats, analyses and predicates are `<pack id>.<name>`; concepts are `<pack id>:…`, each once; extension schemas are for release descriptor kinds only; a pack's analyses cite only declared caveat codes; versions are PEP 440 in normal form; the registry keeps a snapshot of what it was given, and hands out only copies of its concepts, schemas and analysis entries; extension schemas are JSON Schema objects holding only JSON values, read as Python's own types (a boolean schema is refused); ontology system names are Unicode text; every problem of every pack is reported at once; `requires_core` bounds the core version from below; an unknown pack in a lookup is refused alike everywhere | Fixed names let the registry refuse conflicts when packs are loaded, not when they are used |
| D202 | Statistic references (M0) | The pointer of a `stat:` reference is written in URI fragment form, percent-encoded from UTF-8 with upper-case hex, and a floor is at least 2; any other spelling is refused | A reference must survive being pasted into a URL and be compared as a string, and a floor of 1 suppresses nothing |
| D203 | Unit conversion in doubles (M2.1) | A numeric constant in other units than its column's is converted by one multiplication of doubles, by the exact UCUM factor rounded to a double; an integer written as a decimal string is the nearest double for a number or time-offset column, and the canonical form writes that double | The reference evaluator and the SQL engine must agree on every comparison, and SQL computes in doubles |
| D204 | States of lists and ordered categories (M2.1) | A list cell that is not PRESENT takes its base result whatever `negate` and `match` say; a range over an ordered category is UNKNOWN (`NO_INFORMATION`) for a value outside its listed values, which is not a member of any `values`; datetime constants have at most microsecond precision | `negate` applies to items, an unlisted value has no place in the order, and datetimes are stored to the microsecond |
| D205 | Mentions of filtered and scope columns (M2.1) | A column is mentioned by a `value` leaf on the child row itself (no `via`), anywhere in `W_C` outside nested questions; the rules read `W_C` after step 8 of §7.6 removes duplicates, so `{"any": [X, X]}` is the top-level conjunct `X` | A lookup or a nested question is about another row, whose own coverage applies; a document and its canonical form are accepted or refused alike, and evaluation reads the canonical form |
| D206 | Grouped coverage and `covered` flags (M2.1) | The grouped form lists a parent when an assignment row names it and the group table has its group, and for every tuple when one of its groups covers every scope value; `covered` carries `SCOPE_PARTIAL` only on a TRUE whose closedness was restricted to listed tuples | A FALSE from `covered` says the parent was not assessed for what was asked, which no tuple restricts |
| D207 | Parent scopes in v1 (M2.1) | The engine evaluates parent scopes of `value` leaves on the parent row or rows it looks up, and combinators; a parent scope that asks a question (an `exists` or `covered` leaf, or a `value` leaf below the parent table) is refused as `NOT_SUPPORTED` at that leaf when the release is checked, and one that does not resolve against the release is refused with its cause's code; a document asking about the relationship gets the same refusal at its leaf | Every example needs no more; nested questions in scopes need their own closedness rules |
| D208 | `lift_differs` and fields read (M2.1) | `lift_differs` compares truth values, not reasons; the fields read for `UNCONFIRMED_SEMANTICS` are those with a value that resolution or evaluation uses (those of a parent scope's leaves, of record-filtered columns and of the unit's key columns for `ids` included), and a numeric column's `units` and a coverage's `parents` when absent | Flipping a lift can change reasons without changing membership; an absent field that changes the answer is as undeclared as a declared one |
| D209 | Units and unit keys (M2.1) | The unit is a keyed table of the table graph, never a coverage table (`INVALID_UNIT`); unit keys are typed against its key columns, which need a declared datatype (`UNDECLARED_DATATYPE`), and the text form `"<dataset>:<key>"` is for a key of one column (`INVALID_KEY`). In the text form a number key is written as JSON writes a number (so `10`, `10.0` and `1e1` are one key) and an integer key in decimal | A key of several columns has no one text form, and a key's text is read one way only |
| D210 | Implicit path search (M2.1) | An implicit path has at most 16 steps. Its search follows only the relationships on some simple path between the two tables (those of the block an edge joining them would be in) and takes at most 100,000 steps; past that the reference is refused (`LIMIT_EXCEEDED`, `path_search`), even with a path found, and asks for a `via` | Dense table graphs have factorially many simple paths, and a search that stopped cannot tell one path from several |
| D211 | `exclude_self` under trailing lookups (M2.1) | `exclude_self` is refused (`EXCLUDE_SELF_NOT_ALLOWED`) on an `exists` in a `where` that a path's trailing lookups serve, through combinators | Canonicalisation asks such a question from the row before the lookups, so the row it leaves out would be another, and its canonical form would not resolve again |
| D212 | Caps on the canonical form (M2.1) | A cohort's depth and leaves are measured on its canonical form after step 8 of §7.6 removes duplicates and the steps before it apply again, a referenced cohort's clauses inlined, each down step of a path a question, and every node without children a leaf (an empty `all` or `any` included); that form is what resolution returns and evaluation walks. Each leaf as written maps to the top-level clauses its own part of the form became part of: a duplicate's leaves through the nodes they pair with in the clause kept, and a `cohort` leaf to every clause of its expansion. A cohort as written has at most 256 `cohort` leaves (`cohort_references`), checked when the document is loaded | Evaluation's cost grows with the canonical form, not the written one, and a form without leaves would otherwise be bounded by its depth alone; resolution inlines every reference before duplicates are removed, so its cost grows with the references written, which a 2 MiB document could otherwise hold tens of thousands of; and each written leaf resolves its own path, so a path search is done once per release and pair of tables, and copies of an expensive leaf cost one search |

---

## Appendix B. Change log

- **v0.8.2** — Choices the reference evaluator settles (D203–D212): unit conversion and big
  integers in doubles; lists that are not PRESENT, unlisted values of ordered categories and
  datetime precision; what a mention is; grouped listings and `covered` flags; parent scopes in
  v1; `lift_differs` and the fields read; units and unit keys; implicit paths and their search;
  `exclude_self` under trailing lookups; caps on the canonical form and on `cohort` leaves.
- **v0.8.1** — Encodings settled by the M0 document, descriptor and result schemas (D188–D202):
  parsing rules for text, numbers and nesting; verbatim notes and parameter values; bounds on
  substitution and on the refusals returned; id lengths and re-import by occurrence; the static
  cross-dataset checks; descriptor refusals, release rules and limits; values compared as JSON
  values; undeclared coverage and entry by absence; typed declared ranges; who sets a status;
  JSON-safe descriptors; whole releases; result invariants on construction, pack API names and
  statistic references (D200–D202).
- **v0.8** — Fourth review round, verifying v0.7 (D176–D187): canonicalisation to a fixpoint;
  conditions that must be TRUE for an intermediate FALSE; flags of dropped children; event-free
  units left out of Cox fits; totals, pooling order, small-count gates and empty bins under
  disclosure; precedence of estimability rows; pins for reused blobs and mutual exclusion of
  imports, withdrawals and sessions; ids kept across re-imports.
- **v0.7** — Third review round (D149–D175): existence questions canonicalised as chains of
  single-step questions instead of merged; intermediate questions, relevance and flags through
  nested questions in §6.5; record filters under `every`; every tuple defined; survival curves,
  histograms and suppression across the whole output under disclosure; Cox estimability,
  degenerate inputs, the median at exactly 0.5, time-zero rows, per-variable exclusions, the
  cross-dataset hazard ratio and covariate coding in §9; withdrawal per manifest, tombstones,
  erasure by re-import, pins during deletion and canonical string forms in §12; database
  snapshots under hardened DuckDB; identifier collisions.
- **v0.6** — Second review round, of v0.4 and v0.5 (D110–D148): request protection on every
  router; raw snapshots of text files as bytes plus parse settings; re-import carry-forward,
  session handles, one deletion rule and labels that are never reused; core time origins reduced
  to domain-neutral ones; coverage per relationship; the pack API contract; model cards and
  server-set attribution; a result cache of digested content only; path steps, per-step
  quantifiers and merged nested existence; exactly specified hashed objects and result ids over
  whole views; segments, the data wrapper, cohort counts and refusals defined; closedness reasons,
  flags and the order of `covered` in §6.5; record filters as conjunctions; an estimability
  table, bootstrap order statistics, the log-rank test with delayed entry and strata, and the PH
  test and median implemented directly; the DuckDB carve-out for determinism; linked-set
  suppression, and distributions and survival curves under disclosure; the caveat table aligned.
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
