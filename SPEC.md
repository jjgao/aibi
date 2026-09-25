# aibi — Specification

**Status:** Draft v0.8.9 · 2026-09-25
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
  assistant's registered model card; `agent:` records the name an external client declares; a
  pack's curation proposer proposes as `importer:<pack id>@<pack version>` (D249).
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
in use), all required. Model cards are registered only in server configuration (§11.2, D253).

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
- Before it is compiled, the leaf as written, `kind` included, is checked against its kind's schema
  as the pack had it when it was registered; the checks of a document's pack leaves share one budget
  of steps (`pack_leaf_steps`, §14). The compiler and the summary each get a copy of the leaf of
  their own, and the compiler a view of the release's descriptors without its label. A compiler's
  refusals point below the leaf; what a compiler or a summary gives that is not an expansion or a
  list of segments, or any other exception it raises, is refused (`PACK_FAILED`, with a message of
  the core's). The expansion is resolved where the leaf is written, and every refusal inside it is
  placed at the pack leaf. A pack leaf counts as one leaf as written: the clauses its expansion
  became part of are its entry in the leaf map (§6.6, §8.1). Each distinct leaf is compiled once per
  release (D285).
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
membership, a count, a caveat or a digest: §6, §7.6, the caveat rules of §8.3, §8.4 and §9.3. It
is 1.

Step 8 sorts by the RFC 8785 serialisation of each member, compared as UTF-16 code units, and keeps
one of each serialisation. Resolution applies steps 4 to 6 and removes duplicates as it builds the
tree, so sorting after it removes no clause and gives the fixpoint (D281). A cohort's clause tree is
its top-level `all`, with a single member unwrapped. In step 1 every pack the document's `packs`
names must be installed at a version its specifier admits (pre-releases included), and every pack a
cohort's dataset lists must be installed, since its results version is part of the cohort's id
(`PACK_UNAVAILABLE`, D286). The canonical form is a document but for its manifest hashes: read with
each manifest hash as a pin of its dataset, a unit key's as its dataset's id, and each string that
starts with `$` written `$$…` (§7.1), it canonicalises to itself. After phase 1 the caveat rules of
the packs involved run on the canonical cohort, and the codes they raise join its caveats (D287).
Until the registry exists (M3), phase 2 is only the shape of the canonical view and its ids: views
are syntax-checked and reported unchecked, with no id, and a view that names no cohorts orders them
by their ids without the disclosure setting, so that raising a floor never reorders them (D284).

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
  The assistant cites it like a derivation id. The pointer addresses the descriptor's statistics
  as disclosed (`/n_rows` of a table; `/states/<STATE>`, `/categories/<value>`, `/pooled`,
  `/bins/<i>`, `/min` and `/max` of a column; `/report/<i>` of the dataset for the *i*-th note of
  the import report that the curation queue counts), and the floor is appended whenever the
  deployment sets one (D272).

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
  `min_cell_count` (in its configuration, D253). The effective *k* of an output is the largest of the floor and the settings of
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
    than *k* units; a column's distribution is suppressed whole while its PRESENT count is, the
    pooled row of a list column, whose rows count under several values, has no count, and no
    curation evidence, which may quote counts, is served (D271). Descriptor text (labels,
    definitions, descriptions and pack extensions) is declared content and served as written, so
    no importer writes counts or other values computed from the rows into it (§10.1, D271).
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
  DECIMAL columns, and `BIT_OR` and `BOOL_OR` over integers and booleans, for values that enter a
  digest (D294). Every other aggregate (any aggregate over
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
  below 1e-12 and then rounded to 10 significant digits (the double's exact value, correctly
  rounded, ties to even, read back as the nearest double; D283), and a value that rounds past the
  largest double is refused, as a non-finite one is; an analysis may declare coarser precision
  for specific values. Integers are never rounded.
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
| **Curation proposer** | `propose(release) -> [Proposal]` | after every publish (import, re-import, session), and on request (D249) | the dataset's `packs` |
| **Leaf kind** | a JSON Schema of the leaf, `kind` included, checked like an extension schema when the pack is registered; `compile(leaf, release, pack_version) -> [Clause]` on a copy of the leaf and a view of the release without its label, raising a refusal whose paths point below the leaf (§7.3); `summary(leaf) -> [Segment]`, at most 64 | canonicalisation; readbacks | the leaf's pack |
| **Document translator** | `translate(document) -> (aibi document, [{pointer, message}])`, by format `<pack id>.<name>` | `validate_document` with a `format` | the pack named by the format |
| **Analysis** | a registry entry (§9.1) and `run(inputs) -> values`, where the inputs are, per cohort position, the units with the columns, aggregates and endpoint rows the entry requests, materialised by the core; deterministic as in §9.3 | `run_analysis` | the analysis's pack |
| **Requirement predicate** | `predicate(release) -> bool`, cited in `requires` as `"<pack id>.<name>"` | applicability (§9.4) | the predicate's pack |
| **Catalogue facet** | `facet(release) -> {name: [values]}` | catalogue indexing | the dataset's `packs` |
| **Caveat rule** | `rule(release, canonical cohort or view) -> [code]`, codes the pack declares; static, no access to data, on a copy of the form and a view of the release without its label (D287) | canonicalisation of cohorts and views | the packs involved (§7.6) |
| **Core caveat wording** | a message template per core code | rendering messages | the packs involved; several wordings are shown in pack id order |

- `results_version` is an integer bumped whenever a change to the pack could change outputs:
  leaf expansions, caveat rules or severities, or pack analyses. It is hashed into ids (§7.6), so
  a pack upgrade that changes nothing changes no id.
- A pack importer that reshapes data (the four cBioPortal header rows, a CNA matrix turned into
  a long table) either provides `rebuild` or marks the parse-affecting fields of its tables
  non-editable in drafts (§12.2).
- An importer MUST NOT write counts or other values computed from the rows into descriptor text
  (labels, definitions, descriptions and extension members): that text carries what its source
  declares (headers, data dictionaries, metadata files), and it is served as written under every
  disclosure setting (§8.4). What an importer counts goes into the import report's notes, whose
  counts the disclosure pass governs, or into curation evidence, which is not served under *k*
  (D271, D277). The core's importer writes labels from names alone, and its tests, like each
  pack's, import two sources that differ only in their rows and find the same descriptor text.
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
- `propose_descriptor` takes the client's name as its `agent` argument, which the server records
  as `agent:<name>`; the transport keeps no session in which a client could declare it once
  (D277, D278). A client's proposals are admitted at a rate of their own; agents together hold
  at most 5,000 of a dataset's open proposals, half of the 10,000 a dataset may have, and the
  agents of one client at most 500 of those; an operator rejects every open proposal of a proposer
  or of every agent at once (D277). The standing caveats of
  `describe_dataset` are those a query can raise from the descriptors alone, whichever fields it
  reads (D275).
- The MCP transport is stateless streamable HTTP at `/mcp`, behind request protection (§14); the
  HTTP API gives the same tools at `POST /api/tools/<name>` (D278, D280). M1 ships
  `search_catalog`, `describe_dataset`, `describe_column`, `curation_queue` and
  `propose_descriptor`; the other tools come with the milestones that compute what they return.
- Every descriptor is also an MCP resource: `aibi://dataset/<id>@<n | sha256:hex | draft>/<descriptor
  id>` for dataset descriptors, `aibi://concept/<id>`, `aibi://analysis/<id>@<version>` and
  `aibi://model/<id>` for the others.

### 11.2 Operator operations

Some operations are reserved for people and are never MCP tools:

- importing and re-importing datasets, and uploading their files (§12.3, §13.1, D266);
- opening, editing, publishing, discarding and taking over curation sessions, accepting or
  rejecting proposals (one at a time, or every open one of a proposer or of a kind of proposer,
  D277), and running the curation proposers on request (§12.3, D249);
- withdrawing releases (§12.2), and erasing a person's rows (§12.2, Erasure; D269);
- managing named database connections and model cards, which live in server configuration, read
  once at start (D253).

They are served by a separate operator router over HTTP, at `/operator`, which the MCP transport
never mounts (D264), and by an operator CLI, `aibi`, that talks to that router over HTTP only
(D268), so the server process is the only writer of the app DB and the blob store. Every operator
request carries the deployment's curator token as `Authorization: Bearer <token>`, and nowhere
else (D261); server configuration stores only its hash, and the CLI reads the token from an
environment variable or a prompt. Each request names the operator it acts for in an
`Aibi-Operator` header (self-declared, recorded in the audit trail, Q7, D262). The operator router
has no side effects on GET, and browser requests to it (those with an `Origin` or a `Sec-Fetch-*`
header) must pass the Origin and Host checks of §14 and carry a CSRF token; requests without an
Origin header, and without fetch metadata, are accepted with the token alone (D263). Session
handles travel in request and response bodies only (D267). M1 ships the CLI; the web UI's curation
screens (M5) use the operator router.

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
  definitional descriptors, the computed statistics, the import report (notes for the curation
  queue, without cell values, D231) and the manifest itself.
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
  trail, cached results and issuances. A derivation whose hashed object holds an erased value
  (a key of the person, or a value of theirs in an identifier column, §5.4) as data (a constant
  of its clauses, or a parameter, matched as its place says, below: by value in any spelling,
  a datetime by its instant, where it may name the person; by its text alone on a column that
  names none of their rows; a number only where it names their rows, D290) loses
  its whole canonical document and keeps only its id, which resolves to *erased*, because a partial
  redaction next to a hash can be reversed by enumerating keys; its cached results and issuances
  are deleted. Redaction runs once no operation pins a withdrawn manifest. Redaction and the
  pruning below are the only mutations the append-only log permits.

  **Erasure matching (D290).** A value is read under every datatype it may be of (text, a
  number, a date, an instant in UTC), by the store's cell reader and the engine's constant
  reader alike; a *token* is a term's text as a whole token, or, in free text and wherever
  every reading counts, a date and time or a number in the spellings D290 lists whose value
  is a term's.

  | Where the value is | Place | Matched by | Terms |
  |---|---|---|---|
  | On a key or identifier column of a table that holds the person's rows, or a foreign key into one, a `covered` scope column included (a column of its `table`); on a column the core cannot name (a parameter, a value concept, a scope of a table given as a parameter or a concept); a part of a unit key of such a table, or of a unit not known to be a table; a pack leaf as written; anywhere, with `redact_only` | naming | every reading, whole or as a token | the terms of the tables whose rows it names (a key or identifier column's own table, the table a foreign key points into, a unit key's table; every table's where the core cannot name the column or the unit, in a pack leaf and with `redact_only`), JSON numbers and booleans too; no other term |
  | Free text (`notes`, evidence), structure, a parameter no clause refers to, a result's strings | unknown | every reading, whole or as a token | the person's, and those below them but the numbers by their columns' datatypes; no JSON number or boolean |
  | On a known column that names none of the person's rows; a part of a unit key of a table that holds none of them; a tree over another dataset's release, or a cohort as written over another dataset | other | its text alone: the whole string, or a whole token of it that is no part of a longer number, date, time or range | as unknown |
  | An object key anywhere, a name a document gives and every reference to it; a JSON value of the audit trail or the proposal queue (D223) | — | every reading, whole (free text as unknown) | every term, JSON numbers too |

  A parameter referred to from several places is matched as in each of them. A view's
  constants are in the places of the cohorts it names, or, naming none, of the document's.
- **Derivation log.** Two tables. *Derivations*, keyed by derivation id, hold the canonical
  document, the releases and the versions hashed into the id; they are kept permanently.
  *Issuances*, keyed by issuance id, record each time `run_analysis` or `count_cohort` produced
  an output: the derivation id, the document as written, the SQL as run (or, for a cache hit, the
  issuance whose SQL produced the values), the engine and pack versions and a timestamp.
  Issuances of `count_cohort` may be pruned after a configured period, except one that a kept
  issuance names as the source of its values. A derivation id is recorded only with the object
  it is the hash of, over the releases its object is over, each under its manifest's dataset,
  and none of them withdrawn when it is issued. The ids of a derivation resolve, in this order, to *erased* when erasure took
  its object, to *withdrawn* when one of its releases is withdrawn, to *discarded* when one is
  a draft state that is no longer live, and to *unknown release* when the store has no record of
  one (D289, D290).
- **Statistics.** Every build writes the release's catalogue statistics, counted before any
  disclosure setting applies, which the catalogue applies when it serves them (D270, D271).
- **App DB (SQLite).** The catalogue index (one entry per dataset, for its latest published
  release, rebuilt when that release, the floor or the packs change; D273), release labels and
  statuses, curation sessions and
  their audit trail, the proposal queue, saved documents, the result cache (evictable, digested
  content only, §8.1) and the derivation log.
- **Query engine.** DuckDB reading the release's table blobs in place, each verified first as a
  loaded release's are (D221), configured as in §14, in a worker process of the query's (D293).
  The compiler builds queries as SQLGlot expression trees and never concatenates identifiers or
  values into strings; every identifier comes from a descriptor, and appears only where a blob
  is read, renamed there to one of the compiler's own names; every value of a document or
  descriptor is a bound parameter (D292). A node of a canonical cohort is compiled, once per
  table it is evaluated on, to a relation holding each row's truth value, reasons and flags
  (D291). The SQL as run and its parameters are recorded for
  an issuance, blob paths as the blobs' digests.

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
  descriptor forward by id, the dataset descriptor included (its `packs` stays the release's):
  every field unchanged unless the importer's new inference differs from its previous inference
  (the `inferred` value in `curation`, §5.1); only then does the field get the new proposal.
  Carried values are checked by the validation gate (§13.2); a failure refuses the re-import,
  except that a failing field whose status is `proposed`, being still a guess, carried or new, is
  dropped as at import (D241). Every field
  whose value changed is listed in the curation queue. A re-import is refused while a session is
  open on the dataset, and when it would publish a manifest identical to the latest published
  release's (D241). Removing a field or a descriptor whose curation entry has an `inferred` value
  records a **tombstone** in the release (descriptor id, JSON Pointer and `inferred` value),
  which the engine ignores; re-import treats a tombstone as a carried field without a value, so
  the removal stands while the importer's new inference is the tombstone's, and an inference that
  differs or is absent consumes it (D240).
- **Proposals.** `propose_descriptor` adds a proposal to the queue. It changes no release; it
  enters a draft only when an operator accepts it, and becomes `asserted` by that operator, with
  the evidence `Proposal <id>`; the proposer's name and rationale stay in the proposals table
  (D248).
- **Sessions.** A dataset has at most one open curation session. Opening one creates a draft,
  labelled `@draft`, that starts as a copy of the latest published release, and returns a session
  handle. Every change, publish and discard carries the handle and the draft manifest hash it
  expects; a mismatch is refused as a conflict. Taking over a session issues a new handle and
  invalidates the old one. Changes are applied one at a time; each re-runs the structural checks of
  §13.2 and is refused, with counts, if they fail. A session records the release it was opened
  from, and publishing is refused, as a conflict, if that is no longer the latest published
  release. Imports, re-imports, withdrawals and sessions of one dataset exclude each other: each
  is refused while another is running or open. Each dataset has one operation slot, held by the
  server for the whole of an import, re-import, withdrawal, erasure or session operation, and a
  second operation is refused at once, never queued (D236). Every state a draft takes is
  recorded, so that its ids resolve to *discarded* later (D251).
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
| API and MCP | FastAPI, served by uvicorn; the official MCP Python SDK (1.x from 1.30, whose HTTP stack is httpx, D278); the operator CLI uses httpx |
| Data | DuckDB (CSV, Parquet, and Postgres, MySQL, SQLite and DuckDB sources; 1.5, and the query engine in worker processes, D293), SQLGlot (30.x, building the query engine's SQL, D292), python-calamine (XLSX, XLS, ODS; `.xls` is refused in v1, D225), Parquet, SQLite |
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
        api/         # the application: request protection, configuration, refusals over HTTP, aibi-server
        mcp/
        operator/    # the operator router, the curator token and operator names, the aibi CLI
        assistant/
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
  skipped and noted in the curation queue), Parquet. In v1 `.xls` is refused
  (`UNSUPPORTED_FORMAT`): the worker that reads workbooks would contain it too, and reading it
  is left for later (D225).
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

For proposed rather than declared keys, the proposal is dropped with its evidence instead, at
import and at re-import, where a carried proposal is still a guess (D241); on a draft change
every failure refuses.
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
flags and counts. Every document the engine's other tests evaluate is compiled too, and must
agree (D295).

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
  and the operator router), every mount and every path, unknown ones included (D255). In this
  order, each refusing at once: a Host allow-list (the loopback names `localhost`, `127.0.0.1` and
  `[::1]`, the bound address, and configured hostnames when exposed; D256); an Origin allow-list
  (by default the server's own origins, and configured public origins), a request from another
  site (`Sec-Fetch-Site`) needing an allowed Origin (D257); CORS disabled unless configured, and
  never on the operator router (D258); rate limits per client, counting only requests that passed
  the Host and Origin checks, a preflight included (D259); on the operator router the curator
  token, verified first, a failing request counted against the client's token-failure limit and
  never against its operator rate, and a request with the token never refused for failures, then
  the client's operator rate, the operator's name, for a browser a CSRF token, and the content
  type (§11.2, D259, D261–D263); and the body limits below (D260). This
  defeats DNS rebinding against a server on localhost. Every response carries security headers
  (no sniffing, no referrer, same-origin resources, a content security policy), and the operator
  router's are never cached. When the server is bound to anything but the loopback interface
  (`localhost`, 127.0.0.0/8 or `::1`), TLS is required; the server trusts no `X-Forwarded-*`
  header, so a client is its connection's address, an IPv6 one by its /64 unless it is IPv4-mapped,
  NAT64 or link-local (D254, D259). Below the middleware, a connection that has no request under way
  some seconds after it opened or after its last response is closed, a client that takes too little
  of the answers waiting for it is cut off, idle connections make room for new ones, and each client
  may hold only a share of the connections, so that connections that never finish a request, or
  never read their answers, lock no one out; a server reachable from other hosts should still sit
  behind a proxy with header timeouts and caps per address, and then lets each client hold all but
  one of the connections (D254).
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
  - Workbooks and Parquet files are parsed only in a worker process of the import, never in the
    server's, with limits on its memory, its time, the text it returns and how many run at
    once; it is killed when it runs over, and the import is refused naming the limit (D225).
  - From SQLite and DuckDB files only base tables are read, never views or triggers.
  - DuckDB runs with external access disabled except for allowed directories or files (for
    queries, the document's table blobs alone, which it only reads: each statement is one
    `SELECT`), with extension auto-install and auto-load disabled (the extensions it needs are
    pinned and bundled), without spilling to disk, in UTC, and with its configuration locked
    (D293). A Postgres or MySQL snapshot runs instead in its own importer
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
  256 parameters; 64 datasets per cohort; 16 packs; 256 pack leaves per cohort as written
  (`pack_leaves`), with their expansions holding at most as many JSON values together as a
  document may (`expansion_values`) and their checks against their kinds' schemas sharing one
  budget of steps (`pack_leaf_steps`: 10,000 and 8 per JSON value of the document's pack leaves,
  at most the budget of a descriptor, D247, D285); and 1,000 refusals returned. A structured unit
  key costs several JSON values, so long `ids` lists can reach the value limit before the list
  limit. A descriptor has the same limits on its size (in bytes and JSON values), nesting and
  paths, and 200 characters per name, 4,096 per label, key or other string, 10,000 per text, 16
  columns per key or relationship, 10,000 members per list, value map, missing-code map or
  curation map, and 64 per other list or map (extension members per pack, metadata, event codes,
  record filter columns, requirements, methods, caveats). A descriptor's `parent_scope` is a
  clause, so its constants have a document's limits. Imports have size and decompression-ratio
  limits, and limits on the memory, time and returned text of the worker that reads workbooks and
  Parquet files (D225). Every tool call has a wall-clock limit that covers the analysis stage,
  enforced by running queries and analyses in worker processes that can be killed; each worker has
  a DuckDB memory limit. A document's queries run in one fresh worker process holding at most
  `query_memory` bytes, half of them DuckDB's, for at most `query_seconds` from when it has its
  place, and never past the deadline of the tool call that runs it, among at most
  `query_workers` at once (a query waits for its place as long), answering with at most 64 MiB
  of rows (`query_answer_bytes`, D293). The catalogue's tools (M1) read no rows, so a release
  built before its catalogue statistics were kept is refused rather than counted (D270); they run in worker threads
  of their own, a few at once and a share of them per client, each holding its place until its
  thread returns and answered at one limit that counts from its body's arrival, the reading of the
  body included (`tool_calls`, `client_tool_calls`, `tool_seconds`), and a tool call's body has an
  upload's deadlines with a shorter idle time (`tool_body_idle_seconds`, D266, D278). Categorical levels per analysis (at most 150) and resampling replicates are
  capped. A request body has at most 8 MiB by default (`request_bytes`), and an upload at most
  `import_bytes`, streamed to disk; a larger declared length is refused before the body is read,
  and a body is counted as it arrives (D260). At most `imports.concurrent` uploads, imports,
  re-imports and erasures run at once in a server, and an upload that sends nothing for
  `upload_idle_seconds`, or has not ended by a deadline its length sets at the slowest rate
  accepted, is refused (D266). Clients of every router are rate-limited, and the
  number of open proposals is capped. Every refusal names the limit it hit (§8.6).
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
| D119 | Model cards and attribution (refines D87; amended by D248) | Model card fields defined; `by` always set by the server; external clients recorded as `agent:<name>`; an accepted proposal becomes `asserted` by the accepting operator, with evidence naming the proposer | Attribution taken from requests could be forged, and an external agent is not a registered model |
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
| D213 | Release manifests (M1; amended by D231) | A manifest (format 1) holds the dataset, the descriptors blob, the sources (name, `text` or `rows`, hash) sorted by name, and the tables (id, hash, source, the source columns' ids and names in source order) sorted by id, with optional statistics, tombstones and import report blobs; it is stored in RFC 8785 form as a blob named by its hash, and read back only in that form | One manifest, one hash: a release's id is a function of its content |
| D214 | Raw snapshots (M1) | A text source is its bytes; a typed source is JSON Lines, a header `{"columns": […], "format": "aibi.rows/1"}` then one RFC 8785 array per row, with tagged objects for integers beyond ±(2^53 − 1), non-finite numbers, dates, datetimes (with or without an offset) and error cells; a string or column name that UTF-8 cannot carry (a lone surrogate) is refused (`UNPARSEABLE_SOURCE`) | Typed values keep their types and every value round-trips exactly |
| D215 | Parsing text (M1) | Strict decoding in the named encoding; `skip_rows` counts lines (CRLF, LF or CR) and `header_row` non-blank records after them; blank records are skipped, so a file of one column has no row for an empty line; a quote inside an unquoted field is text; with `utf-8` a byte order mark is part of the first header name (`utf-8-sig` reads past it); a record with more or fewer fields than the header is unparseable (`UNPARSEABLE_SOURCE`, naming its line); the delimiter is neither the quote nor a line break; a header name is Unicode text (`UNPARSEABLE_SOURCE`) of at most 4,096 characters (`LIMIT_EXCEEDED`), as a descriptor's strings are | A file is read one way, and a ragged row is an error, not a shifted one |
| D216 | Typing cells (M1) | A declared missing code (on the canonical string) first; then null or empty is UNKNOWN, and a non-finite number or error cell is never PRESENT; a value of the column's type is taken as it is, any other read from its canonical string: numbers in ASCII decimal notation, integers whose decimal value is exactly integral (a token with an exponent beyond a decimal's is none), within 64 bits (within ±(2^53 − 1) when written with a fraction or exponent, or given as a double), booleans `true/t/yes/y/1` and `false/f/no/n/0` in any case, dates `YYYY-MM-DD`, RFC 3339 datetimes with `T`, `t` or a space, fractions truncated to microseconds, no offset read as UTC; strings and categories exactly; a token that does not parse is UNKNOWN and reported | Typing is a function of the snapshot and the descriptors, and what does not parse is visible to curation |
| D217 | List cells (M1) | The cell's canonical string is matched against the missing codes, then parsed by `list_syntax` (a JSON array or a Python list or tuple of scalars, a length cap on both, or delimited text split exactly); each item takes its state as a cell does, `""` and null items being UNKNOWN; a list that does not parse, nests, or holds a string with a lone surrogate escape leaves the cell UNKNOWN | Items have states of their own (§6.4) |
| D218 | Table blobs (M1) | Parquet 2.6, zstd level 3, dictionaries and statistics on, data pages v1, row groups of 2^20 rows; source columns in source order, then derived columns by id; a `<column>__state` column exactly when the column declares a missing code or holds a cell that is not PRESENT, and `<column>__item_state` for list items under the same rule; physical types int64, float64, string, bool, date32, timestamp in µs UTC and lists of strings; rows in source order | The same inputs give the same bytes under one pyarrow version, so a release's hash is reproducible |
| D219 | Derived columns (M1) | Computed from PRESENT inputs only, else UNKNOWN before NOT_ASSESSED before NOT_APPLICABLE; `arith` into an `integer` column is exact, each operand being the number its canonical string writes (the double 0.1 is 1/10), and a result that is not an integer within 64 bits, or a division by zero, is UNKNOWN; into any other column it keeps integers for `+`, `−` and `×`, divides truly and gives a double, a non-finite result or division by zero being UNKNOWN; a derived column of undeclared datatype is a number (a string if it maps values), and other derivations read it as one; `date_diff` reads a date as its midnight UTC and uses exact factors; `value_map` matches the input's canonical string and types the result as a source token; `unit_convert` multiplies by the exact factor rounded to a double (D203); what is wrong whatever the data (types, units, a source column declared derived) refuses the build | Derived values follow the same states as source values, and SQL computes the same doubles |
| D220 | Rebuilding releases (M1) | A draft change rebuilds a table only when its parse settings, or a column's `datatype`, `missing_codes`, `list_syntax`, `derived` or `units`, or its set of derived columns changes, and reuses every other table blob; a change that gives a table other source columns (a header read differently, a table or a source column added or removed) is a re-import (`COLUMNS_CHANGED`) | Curation that does not touch parsing costs nothing, and rows change only by re-import |
| D221 | Pins and the sweep (M1) | Pins are held by the server process, the store's only writer: an operation pins a blob before writing it, a query pins the releases it reads, and the sweep holds the store's lock from reading the pins to its last deletion; the process holds an exclusive `flock` on the store's lock file while it has the store open, so no second process opens it; the sweep runs after publishing and withdrawing, after releasing a pin when that frees a blob no live release references, and when the store opens, which releases what a crashed process pinned; it keeps every live release's blobs and each withdrawn manifest alone; publishing checks that the manifest is not withdrawn first, and holds the lock from checking its blobs to the label; loading a release verifies its table blobs' hashes, and writing a blob that exists but is damaged writes it again | A sweep never deletes what an operation is about to reference, and what a failed one wrote does not stay |
| D222 | The app DB (M1) | SQLite in WAL mode with foreign keys, full synchronisation and `secure_delete`, migrated by `user_version`; its triggers keep labels unremoved and unchanged, manifests unremoved and withdrawn at most once, and the audit trail append-only, changed only by redaction; at most one open session per dataset; the next label is the highest ever issued plus one; a WAL checkpoint that a reader keeps from finishing within 0.1 s is retried when the store opens and, at most once a second, when a pin is released; it also holds what an erasure has still to do: redactions waiting on pins, and upload areas still to delete | The rules hold whatever code writes to the database, and a long reader of the WAL does not slow every query |
| D223 | Erasure (M1) | Refused (`ERASURE_BLOCKED`) while a curation session is open on the dataset, whose draft may hold the person, and until a re-import gives a latest release that does not hold the person; the key is typed by its columns' datatypes, as cells are, and one that does not parse, or that no published release holds, is refused (`INVALID_KEY`) unless the operator asks for `redact_only`; the person's rows in a release are their row, found by the key in every release, and the rows below it: those whose foreign key holds the key of a row of theirs that the same release holds, through every relationship any published release declares (the same tables and columns counting once), followed in every release whose child table has its columns (the gate refuses a dangling declared foreign key, D230, but drops a proposed one, so a re-import can keep such a row without its relationship), except those into the person's own table or into a table whose role is `entity` (other people), and so on down; a key a row of theirs has in another release counts only for an orphan, a row whose foreign key names no row of this release (so a loan whose member a re-import dropped is still theirs, and a loan number a re-import gave to someone else is not), keys being compared across releases by their values' canonical strings, since a re-import may type a column otherwise; a release holds the person when it holds one of those rows, or a row whose foreign key names one of them in the same way through any relationship; every earlier release holding the person is withdrawn, under the store's lock from the checks on, in one transaction with the erasure's audit entry and the redaction it records, so that a failure after it (deleting the upload area, say) is finished by the redaction running when a pin is released or the store opens, and by erasing again with the same key while the redaction waits, an upload area still to delete being reported until then and deleted afterwards by erasing with `redact_only`; `redact_only` trusts the operator's key: it is refused (`INVALID_KEY`) unless the dataset has a withdrawn release, and only then does a refusal suggest it; with it, a key no published release holds (one only releases withdrawn before held) is typed by the key columns of the latest published release that has its table (`INVALID_KEY` if it does not parse), or, if none has the table, taken as the given values' canonical strings, is the only term, and is redacted, with the audit entry, which records the mode, and the upload area's deletion, as above; the terms are the canonical strings, over every release, of the keys of the person's rows and of their values in identifier columns (each PRESENT item of a list), but not of columns that are foreign keys to rows not the person's (such as a link table's `book_id`); in the dataset's rows in the app DB, a JSON string or number (or object key) that is a term by value, sharing a reading with it as D290 reads values (as text, a number, a date or an instant, in every spelling the store's cells and the engine's constants accept, so `"2.0"` is the loan `2` and `"2020-01-02T00:00:00Z"` the date `2020-01-02`), is redacted, two keys redacted alike being kept apart; inside free text (evidence, a string holding more than a term) a term is redacted as a whole token, next to no letter, digit or mark (Unicode category M) of any script, its text matching exactly, case included, and so is a date and time, or a number, in the spellings D290 lists, whose value is a term's: every term of the person's own row (their key and identifier values), numeric or not, and a term of the rows below them only if it is not a number by its column's datatype, so a surrogate key matches only whole JSON values (the derivation log's own rules, by where a constant is, are D290's); JSON Pointers are never redacted, nor the labels and manifest hashes in the store's own publish and withdraw entries, nor the erasure's own audit entry, which keeps its counts; redaction waits in the app DB while a pin holds a withdrawn manifest or a blob of it that no live release references, and is done once the WAL checkpoint after it finishes | Nothing that names the person is left, whatever fails part way, and what else is erased is bounded: the people linked to them are not, nor numbers that only look like a key below them, nor a row of another release that reuses one of their keys; the person's own key and identifier values are erased as whole tokens wherever they appear, which takes equal values with them when they are small numbers, so an operator avoids that by choosing natural keys |
| D224 | Detecting parse settings (M1) | Encoding: a UTF-8 byte-order mark gives `utf-8-sig`, a UTF-16 one `utf-16`, else strict `utf-8`, else `cp1252` if the bytes decode, else `latin-1`; the quote is `"`; comma, semicolon, bar and tab (tab first for `.tsv`) are tried on the first 1 MiB (at most 1,000 records), and the winner has the most records with its modal field count (the smaller on a tie) when that count is above 1, the first candidate on a tie, a comma when none is; `tsv` exactly for a tab; `skip_rows` counts the leading lines that are blank, are comments (`#` followed by a space, another `#` or nothing, so `#id,name` is a header) or have another field count, up to the first with the modal count; the whole file's cells are counted as it is read, under `import_cells`; `header_row` is 0; `/fields/source` is `imported_default` with evidence; a file the detected settings cannot read is `UNPARSEABLE_SOURCE`, never retried with other settings | A guess is checked on the whole file, and a ragged file is an error, not a file of one column |
| D225 | Sources and typed values (M1) | A source is a file, a flat directory or a `.zip` of files at any depth; hidden entries, subdirectories, symlinks (never followed), entries that are not regular files and other kinds are skipped and noted, each by its own name before anything is resolved, a nested `.zip` refused inside an archive and skipped and noted inside a directory. `.xls` is refused (`UNSUPPORTED_FORMAT`, converting to `.xlsx`, `.ods` or `.csv`), a deviation from §13.1 made in v1, when sheets were to be bounded before python-calamine read them; the worker below would contain an XLS workbook too, and reading one is left for later. python-calamine and pyarrow allocate more than a file's bytes (a sheet's whole range from A1 to its last cell, a shared string or an ODS cell repeated into every cell, a Parquet dictionary decoded into every row) and can loop on a malformed file, so an import's workbooks and Parquet files are read in one worker process per import: a fresh interpreter, never a fork of the server, started with only `PATH`, `PYTHONPATH` and a UTF-8 locale in its environment, without the working directory on its path, in a session of its own, and with its standard error drained and discarded but for its last 4 KiB; at most `reader_workers` (at least 1) run at once in a server process, over every import, and an import waits for its own at most `reader_seconds` plus 5 s for a worker stopped at its deadline to be reaped, so that one queued behind an import that runs out of time gets it (else `LIMIT_EXCEEDED` naming `reader_workers`); before it reads, it limits its address space to `reader_memory` (`RLIMIT_AS`, soft and hard) and its CPU time to `reader_seconds` + 5 s (`RLIMIT_CPU`), and on Linux asks to be killed when the server dies (`PR_SET_PDEATHSIG`); the server kills its process group once the import's reads have taken `reader_seconds` and when the import ends; its returned strings hold at most `decoded_bytes` bytes of UTF-8 together, counted before it answers, and a string in a cell holds at most 131,072 characters, the field limit of the text reader (a longer one is `UNPARSEABLE_SOURCE`, as a text file's field is, naming the sheet or file, the column by its header and the row of the table, which counts neither the header nor blank rows); the server reads each answer's frame itself under the same deadline, refuses one longer than `reader_memory` bytes, and unpickles it allowing only the classes a read returns (`Sheet`, `TypedSource`, `ParquetSource`) and `datetime`'s `date`, `datetime`, `timedelta` and `timezone`, from a fixed table that imports nothing. The worker bounds what a hostile file makes a reader consume; it is not a privilege boundary (it runs as the server's user, with its files and network). A worker that runs out of memory is `LIMIT_EXCEEDED` naming `reader_memory`: a `MemoryError`, dying of `SIGKILL` (the OOM killer), and a panic (pyo3's `PanicException`) or `SIGABRT` when its peak memory (`VmPeak` read before it is killed, else its peak resident size) came within 90 % of `reader_memory` or Rust wrote that an allocation failed (a line that is exactly `memory allocation of N bytes failed`, in the last 4 KiB of its standard error); one out of time, or out of CPU time, is `LIMIT_EXCEEDED` naming `reader_seconds`; any other panic or abort, and any other end, is `UNPARSEABLE_SOURCE`; an answer that breaks the framing or names another class is a fault (`ReaderError`), and the worker is killed. A `MemoryError` in the server is `LIMIT_EXCEEDED` too, naming `import_bytes` while the importer reads and while a read's request to the worker (a file's bytes) is pickled and sent, `decoded_bytes` while an answer is received or unpickled, and, while the store builds, `decoded_bytes` when any table is a typed source and `import_bytes` otherwise. The server's memory for an import is bounded by the limits, not by the upload's size; measured with pyarrow 25 on CPython 3.12, building the release without loading it: about 4.5 bytes per decoded byte (1.2 GB for 2²⁸ bytes of ASCII strings), and up to about 8.5 (2.2 GB) when every string holds a character outside the Basic Multilingual Plane, which CPython stores in 4 bytes a character; about 270 bytes per cell (5.3 GB for 20,000,000 cells of integers and short strings, or of floats); and about 7 to 9 times a text file's bytes (2.1 GB for 240 MB, 4.2 GB for 480 MB), so an import at the default limits may need several GB. That budget is per import and multiplies with the imports that run at once: `reader_workers` bounds only the worker processes, in each server process, and a server-wide bound on concurrent imports is a server setting (the operator surface, #11). In the worker, a workbook holding `xl/workbook.bin` (XLSB, whatever its extension) is `UNSUPPORTED_FORMAT`, and a sheet's extent (its last cell as python-calamine reads it, from A1) is counted under `import_cells`, for each sheet and for the sheets together, before its cells become values. Names that become labels or original names (headers, Parquet column, sheet, file, member and dataset names) over 4,096 characters are `LIMIT_EXCEEDED`. A sheet's header is its first non-empty row, blank rows are dropped, empty columns at either edge trimmed, a sheet with no cell (or no worksheet) skipped and noted, one with only a header a table of no rows; spreadsheet times become ISO text and durations ISO 8601 durations, and error cells, which python-calamine reads as empty strings, are empty (UNKNOWN). Parquet integers, floats, booleans, strings (dictionaries too), dates and timestamps (offset kept, truncated to µs) are typed values, decimals their exact text, times ISO text, lists of strings JSON array text, nulls null; other types are refused with the list, and cells are counted from the metadata before reading; a file pyarrow or the conversion cannot read (a corrupt header, a duplicate column name, a timestamp beyond year 9999, an unknown zone, invalid UTF-8) is `UNPARSEABLE_SOURCE`. pyarrow is called only in `store/parquet.py`, python-calamine only in `importers/sheets.py`, and on imported bytes both only in the worker (`importers/worker.py`); the store's `parquet.read` and `write` run pyarrow in the server on the store's own files | Every value reaches the store as a source value (§12.2) that round-trips, and an error cell is never PRESENT; a lexical bound on a sheet's extent kept losing to the parser it mirrored, and a process that can be killed bounds what no scan can, so no file exhausts the server's memory or holds its import lock forever |
| D226 | Table names (M1) | A table's original name is its file's stem (an upload's original name), a sheet's name when the source is that one workbook, or `<stem> <sheet>` beside other files; `source.name` is the table id and `source.original_name` the file's or sheet's name; ids are normalised as §5.1 says (D191), the table id `dataset` avoided, and a suffix after a cut to 64 characters follows the cut base without its trailing `_`, so no id has `__`; every rename is noted | Names are stable and legible, and §5.1 did not say what a suffix meets |
| D227 | Statuses the importer sets (M1) | `imported`: labels from original names, a column's `source`, a sheet's or Parquet file's table `source`, a datatype Parquet declares (datetimes with an offset), the dataset's `name`, `source` and `packs`; `imported_default`: a text file's `source` (the detected parse settings take its status), conventional missing codes, a datetime datatype read without offsets, labels made from ids; `proposed`: everything else inferred. Nothing is `asserted`; `by` is `importer:aibi.files@<version>`, `at` the import's, and every entry has the field's value as `inferred`; evidence gives rules, counts and ids, never a cell value. Only tokens of a fixed list that occur are declared as missing codes (`NA`, `N/A`, `n/a`, `#N/A`, `NULL`, `null`, `None`, `NaN`, `-`, `.`, `?`, `unknown`, `Unknown`, all UNKNOWN) | Re-import compares inferences (§12.3), and a curation entry outlives what erasure reaches, so it holds no values (A6) |
| D228 | Column inference (M1) | The first of integer, number, date, datetime and boolean that parses every non-missing cell wins; only when none does, the first that parses all but one in twenty of at least 20, the cells left UNKNOWN and reported, but never for a column that is a key or a foreign key as a string (booleans only with a word such as `yes`, so 0 and 1 are integers); else string; no PRESENT cell, no datatype. A string column is `list<category>` if every PRESENT cell is a JSON array (`json`) or a Python list (`python`) of scalars, or `;` or else `\|` is in at least 2 cells and splitting gives fewer distinct items than distinct cells (`delimited`); a `category` if it is no key or foreign key and has at most 50 distinct values, at most half its PRESENT cells; an identifier if it is none of those and its PRESENT values, at least 20, are distinct, and from 2 to 19 one note per table names such columns | A stray token should not turn a column of numbers into text, and tiny tables should not flood the curation queue with identifiers |
| D229 | Keys, relationships and roles (M1) | The key is the first string or integer column PRESENT and distinct in every row, else the first pair of foreign keys that is, together; else none, noted. A relationship is proposed for a string or integer column whose PRESENT values are all keys of one other table's single-column key, integers only when the column's id is the key's, `<parent>_<key>` or `<singular parent>_<key>` (a trailing `s` dropped, or `es` after s, x, z, ch or sh, so `book_id` for `books.id`); a table's own key is never a foreign key, of strings or integers (`books.id` in `members.id`); these, containment in two parents' keys, self-references and integers failing the name guard are noted instead, so a one-to-one extension table keyed by its parent's key (`member_details.member_id` for `members.member_id`) gets only the note, and its relationship is the operator's to declare; it is one-to-one when the child's values are distinct. Roles: `link` for a key of two foreign keys, `entity` for a parent or a keyed table with no parent, `event` for a table with a date or datetime, else `measurement`, never `coverage`; grain `One row per <key>`; coverage `parents: "all"` for relationships whose child table is an entity or a link (§5.6's rule, where the issue said parent) | Proposals are few, deterministic and written down, and a join path is never picked silently |
| D230 | The validation gate (M1) | On the typed tables, before any release is read: key cells not PRESENT (`KEY_NULL`) or repeated (`KEY_NOT_UNIQUE`, booleans apart from numbers); a relationship's parent columns unique among PRESENT tuples whether or not they are the declared key; dangling foreign keys (`DANGLING_REFERENCE`), a tuple with a cell not PRESENT being null; one-to-one children sharing a parent (`CARDINALITY_VIOLATED`); coverage, assignment and group tables with a named cell not PRESENT (`COVERAGE_NULL`) or naming a parent or group that does not exist (`COVERAGE_UNKNOWN`); record filters (`OUTSIDE_RECORD_FILTER`, `NOT_APPLICABLE_IN_FILTER`); a layout repeating a column id (`DUPLICATE_ENTRY`), then `check_release` again. Refusals point at the field and give a count and the first five rows from 1, never values. At import a failing `proposed` key (with a `proposed` grain, which names it, and a `proposed` role `link` or `entity`, which D229 gives only to a table with a key), coverage `parents` or `record_filter`, or relationship whose tables and columns are all `proposed` (with its coverage) is dropped instead, to a fixpoint; on a draft change everything refuses. Children outside their listed coverage and invalid endpoint rows are gaps, reported only | The evaluator's release needs unique parents, and a proposal that fails is a wrong guess, not a broken dataset |
| D231 | The import report (M1; amends D213) | An optional `report` blob in the manifest, `{"format": "aibi.import-report/1", "notes": [...]}` in RFC 8785 form, notes sorted by kind, subject and content, without timestamps: skipped sources, renames, what was not proposed and why, drops with their evidence, gaps, and unparsed cells per column as counts and the first five rows; it holds no cell values. It lives and dies with its release, a change carries its base's, and the curation queue (#10) reads it | Identical inputs give identical manifests, and a report that names no value widens no disclosure |
| D232 | Confinement (M1) | A path is absolute, holds no NUL, is neither a URL nor a glob (`*`, `?` or `[` in a path that does not exist as written), exists, is a regular file or a directory, and lies inside a root after `realpath`, the roots resolved too; its device and inode are recorded, and it is read through one descriptor opened with `O_NOFOLLOW`, checked against them by `fstat` and read into memory under `import_bytes`; directories are flat, opened once with `O_DIRECTORY` and `O_NOFOLLOW`, checked by `fstat` against the device and inode confined, and their entries listed through that descriptor by their own names and classified by `lstat` there, regular files confined one by one, each being the file listed, symlinks never followed, and nothing but a regular file read; a hard link is accepted, being the file itself, which an operator put inside a root; every importer, core or pack, reads through the `SourceReader` of its options (`read`, `files`, `location`) | Nothing is opened twice, so no file can be swapped between the check and the use (§14) |
| D233 | Archives and limits (M1) | Defaults: 1 GiB per file (`import_bytes`) and per member (`member_bytes`), 10,000 members, 4 GiB uncompressed in all, a ratio of 100 for a member over 1 MiB and for the total against the archive's size, 1,000 tables, 4,096 columns per table and 20,000,000 cells per import; for the worker that reads workbooks and Parquet files (D225), 4 GiB of address space (`reader_memory`), 300 seconds per import (`reader_seconds`), 2 such workers at once in a server process (`reader_workers`, at least 1) and 2²⁸ bytes of returned text per import, in UTF-8 (`decoded_bytes`: the server holds several times what it receives, and a small typed file can decode to it, D225, which states the server's memory per import in terms of these limits). It multiplies with concurrent imports, since `reader_workers` bounds only the workers of one server process; a server-wide bound on concurrent imports is a server setting (#11). Entries with absolute names, drive letters, backslashes or `..`, symbolic links, encryption or a repeated name are refused (`ARCHIVE_REFUSED`), and so is a nested `.zip` in a dataset; sizes are counted while decompressing, and a member that does not decompress to what its header says (more or fewer bytes, or a bad checksum), or an archive the zip module raises on in any way, is refused; workbooks in zip containers are checked the same way before python-calamine reads them. A limit hit is `LIMIT_EXCEEDED` with its name | A header can lie; the bytes decompressed cannot |
| D234 | The upload area (M1) | `<data root>/uploads/<dataset id>/<sha256 hex>.<extension>`, the extension from the list the file importer reads; the original name travels in the import's options and names the tables; writes are atomic, through `<data root>/uploads.tmp`, which is inside no confinement root, and capped at `import_bytes`; the area is a confinement root, and deleting a dataset's directory is the erasure hook | Storage paths are built only from hashes and identifiers (§14) |
| D235 | Pack imports (M1) | The core's `FileImporter` and a pack's importer share `import_source`, `ImportOptions` and one pipeline; `validate_source` runs for the importing pack and the dataset's `packs` before the build, and `validate_descriptors` after it, on a view of the built release labelled with the label it would be published as, because a `ReleaseView` has a label and an unpublished import has none; a pack's refusals keep its namespaced codes and stop the import; nothing is published | Core and pack imports get the same confinement, gate and report |
| D236 | Operation slots (M1) | Each dataset has one slot, held in the server process for the whole of an import, a re-import, a withdrawal, an erasure or a session operation (open, change, publish, discard, take over); a second operation of the dataset is refused at once (`DATASET_BUSY`), never queued, and while a session is open an import, a re-import or a withdrawal is refused `DATASET_BUSY` too, naming the session, while an erasure keeps refusing it as `ERASURE_BLOCKED` (D223); other datasets are unaffected; the slot is taken under the store's lock only long enough to check and mark it, released however the operation ends, and not persisted; the order is always the slot, then the store's lock; the store's own `publish` takes none | The flock of D221 makes this process the only writer, a crash ends every running operation, and open sessions persist in the app DB: §12.3's "refused while another is running or open" |
| D237 | Import publishes (M1; amends D235) | `import_dataset` publishes the release it builds as the dataset's next label, `@1` for a new dataset or the highest ever issued plus one when every earlier release was withdrawn (nothing is carried from them, their descriptors being swept), audited as `import`; it is refused (`DATASET_EXISTS`) while the dataset has a published release; `build_import` builds without publishing | Labels are never reused (§12.3), and a re-import needs a live base to carry from |
| D238 | Previous names (M1) | On re-import `ImportOptions.previous` holds the latest release's names: each table's `/label` inference with its id, ordered by that name and, within one name, in the order its ids were assigned: the id without a collision suffix first (the name's normalised id), then by numeric suffix `_<n>` compared as a number (`a`, `a_2`, `a_10`; `a…a` cut to 64 before `a…a_2` cut to 62), a table whose label entry has no inference left out; and each manifest table entry's (source name, id) pairs in source order; every importer, core or pack, passes them to `normalise_names` | §5.1's k-th-occurrence rule needs the previous order: manifests keep column order but not table order, and suffixes follow assignment order |
| D239 | Carry-forward (M1) | By descriptor id: tables and source columns exist exactly when the new import has them; a base descriptor the new import lacks is removed if the importer had inferred any of its fields, else carried (a derived column, which is curation, included), and a carried one naming something gone is removed too (`gone`), down the chain (a derived column whose input is gone); the dataset descriptor is carried field by field like every other, its `packs` value and entry the base's, since which packs a dataset uses is the operator's; a new id is added (`added`) unless a descriptor tombstone holds the same inferences, when it stays removed, its tombstone kept; a tombstone of other inferences is consumed (D240), and a new id naming a descriptor whose removal stands is dropped (`dropped`), with or without such a tombstone, whatever its fields' statuses; an added one's field tombstones apply to it as to a descriptor in both, unless it does not hold without those fields, when they are consumed and the fields return (`returned`). Per field of a descriptor in both, inferences are compared as RFC 8785 bytes, the previous one being the base entry's `inferred` (present when the member is set, `null` included) or a field tombstone's, the new one the importer's or absent: equal, the base's value and entry are carried verbatim (a tombstoned field stays absent); different, the field takes the new proposal and entry (`proposed`, `returned` over a tombstone) or is removed (`removed`), an absent inference over a tombstone consuming it, the field staying absent with nothing reported (D240); a valued field without a previous inference is an operator's and is carried, the new inference not written into it, so a field an operator wrote before the importer inferred it leaves no tombstone when removed, and the importer's proposal of it returns as `proposed` at the next re-import (an accepted limitation); an importer's entry without `inferred` takes its value as one. `provenance`, which has no curation entry (§5.1), is the new import's when it has one and the base's otherwise, so that an operator's `put` of it survives an importer that sets none (the core's). A carried field that fails its model or `check_release` takes the new proposal when it is `proposed` or `imported_default`; a new proposal that fails (a field that took the importer's `proposed` value, or a descriptor new to the release whose failing field is `proposed`) is dropped (`dropped`, D241): the field alone when its descriptor holds without it, a descriptor new to the release included; else, in a descriptor the base has, the field goes back to the base's value and entry; else, or when that fails too and is a guess (`proposed`), the whole descriptor, unless it is the source's or a descriptor of the base's holding an asserted field; a descriptor that names a dropped one is dropped with it (`dropped`) unless it holds an asserted field, which it never loses that way; anything else refuses the re-import at `/descriptors/<id>/…` (a failing field of a table or source column new to the release that it cannot hold without, or whose status is not `proposed`, say), as does a derived column whose id a new source column takes (at `/descriptors/<id>/fields/derived`; removing the derived column first is the remedy). Operator-edited parse settings the importer detects otherwise give `COLUMNS_CHANGED` | §12.3 compares inferences, not values, so curation survives an unchanged inference, a field no one inferred is a person's, and an unchanged re-import changes no byte |
| D240 | Tombstones (M1) | The blob is `{"format": "aibi.tombstones/1", "tombstones": [{descriptor, pointer, inferred, version}]}` in RFC 8785 form, sorted by (descriptor, pointer), absent when there are none; removing a field whose entry has an inference records it; removing a descriptor records one tombstone at the pointer `""` whose `inferred` maps each inferred field's pointer (its field tombstones' included, which it replaces) to its inference, and a re-import brings back the whole new descriptor when that map differs, at the tombstone's version plus one; re-adding a field or descriptor lifts its tombstone, its inference kept in the new entry, and re-creating a descriptor by `put` (or by accepting a whole-descriptor proposal) turns each inference of its descriptor tombstone whose pointer the new descriptor lacks into a field tombstone, as a `put` over the descriptor would; a field without an inference (an operator's) leaves no tombstone (D239); changes carry the draft's tombstones; at re-import a tombstone is kept only while the import infers the same (a descriptor's, while the importer proposes the descriptor with the same inferences): a differing inference consumes it, the field or descriptor returning (`returned`), and so does an absent one (the importer no longer infers the field or proposes the descriptor, its table gone included), nothing returning and nothing reported, so that every tombstone a release keeps holds the latest import's inference, which an edit that lifts it records (D245); one export lacking an inference (orphan rows blocking a relationship, say) thus lets the next import that infers it again bring back what an operator removed, an accepted limit, since a tombstone kept through the absence would put a stale inference into a later operator's entry or descriptor tombstone, and re-importing the same files would then undo that curation; a field tombstone of a descriptor new to the release (one the gate dropped, say) applies to it as to a carried one (D239); a returning descriptor that names one whose removal stands is dropped, its tombstone consumed (D239), and one a check drops after it returned does not get its tombstone back, since both tombstones hold other inferences than the import's and each would put a stale inference into a later `put`; the field tombstones of a descriptor that stays out of the release stand while the import infers the same, and those an added descriptor consumed only because it does not hold without their fields come back when a check drops it; gate drops are never tombstones, and the engine never reads them | A removal stands until the evidence changes (§12.3), and the gate decides afresh on each import's data |
| D241 | The re-import gate and refusals (M1) | Before the gate, a new proposal of the importer's that fails against the carried curation is dropped, or goes back to the base's value, as D239 says, refusing the re-import only where D239 does (a descriptor of the base's that holds an asserted field and cannot hold without the failing value, a table or source column new to the release that cannot hold without its failing proposal, or a descriptor with an asserted field naming a dropped one); the gate runs in import mode on the carried descriptors: a failing field whose status is `proposed`, carried or new, is dropped (D230), and one of any other status refuses the re-import; a re-import is refused `NO_CHANGE` when its manifest equals the latest's but for `report` and `statistics`, and `RELEASE_WITHDRAWN` when it equals a withdrawn one | A carried proposal is still a guess, which settles §12.3's "a failure refuses" against §13.2's "proposals are dropped"; changes carry the report, so identical data could otherwise differ only in notes |
| D242 | Re-import notes (M1) | Each change of D239 is an `ImportNote(kind="reimported")` in the report, its subject `<id>` or `<id><pointer>` and its message a template by what happened, without values; the notes describe the last import or re-import of the release's lineage, since a change carries them | "Every field whose value changed is listed in the curation queue", which reads the report (D231) |
| D243 | Descriptor versions (M1) | The server sets `version` and a client never does: against the release a write starts from (a draft's base, the latest release at re-import), a descriptor keeps its version when nothing but `version` differs, curation included, and takes it plus one otherwise; a new descriptor has 1, and one returning from a tombstone the tombstone's version plus one | §5.1's "+1 whenever fields change", counted per release rather than per edit, so that a draft edited back gives the base's bytes again |
| D244 | Sessions and handles (M1) | A handle is `ses_` and 43 base64url characters from `secrets`, returned once; only its SHA-256 is stored, compared in constant time, and a handle is never in the app DB, the audit trail, a refusal or a `repr`; the draft starts as the base's manifest; change, publish and discard carry the handle and the expected draft hash, and a wrong or replaced handle or another hash is `CONFLICT`, no open session `NO_SESSION`; take over needs no handle and replaces the hash; publish is `CONFLICT` when the base is no longer the latest and `NO_CHANGE` when the draft equals it, and refused as a change is when the pack checks on every descriptor or the packs' validators refuse the draft under the registry it runs with (D247); publish and discard each run in one transaction with ending the session and the audit entry, then sweep | §12.3, with a handle the database cannot leak |
| D245 | Edits (M1) | A change is 1 to 256 edits, applied in order to copies, the first that cannot apply refusing it at `/edits/<n>`, then one draft and one gate run; pointers name whole curated fields; `set` writes a value with an entry `{asserted, operator:<name>, now, evidence}` keeping the replaced entry's `inferred`; `confirm` asserts current values (every field without `pointers`); `remove` deletes a field, never `/label`; `put` writes a whole descriptor without `version` or `curation` (refused if given), fields equal to the draft's keeping their entries, the others asserted, those left out removed (over a removed descriptor, their inferences becoming field tombstones, D240); `remove_descriptor` removes a relationship, coverage, endpoint or derived column, and is refused for the dataset (`INVALID_VALUE`), a table or a source column (`COLUMNS_CHANGED`); `accept` applies an open proposal (D248); a change that would rebuild a table whose `source.kind` is `pack` is `NOT_SUPPORTED` until packs rebuild (M4) | Only operators assert (§5.1), and `inferred` is what re-import compares, so an edit must not erase it |
| D246 | Checks on a change (M1) | In order, the first failing stage refusing the change with all its refusals: the descriptors' models; `check_release`; the pack checks (D247); the build (rebuilds, `COLUMNS_CHANGED`); the gate in change mode over every table, a reused table's gate columns read from its blob alone; the packs' `validate_descriptors` on a view labelled `"draft"`; paths point at `/draft/<escaped id><field pointer>` rather than positions, a pack validator's paths being `/<escaped id><field pointer>` into the view's descriptors, prefixed `/draft` on a change and `/descriptors` on an import or re-import, as the pack checks' refusals are at import too | §12.3 and §13.2 ("each re-runs the structural checks … refused, with counts"); positions in a sorted list mean nothing to a client |
| D247 | Descriptor-write checks (M1) | On import, re-import, every change and every proposal: every pack the dataset lists is registered (`INVALID_VALUE`); each extension object validates against its pack's JSON Schema 2020-12 for the kind, `$ref` local only and formats not asserted, refused `INVALID_EXTENSION` at the failing member by keyword, never by value, and refused whole when the pack has no schema for the kind; codes of an ontology system a registered pack validates are checked (`INVALID_VALUE`), other systems pass; registration refuses a schema that is not valid 2020-12, declares another dialect (in any subschema) or has a non-local reference, a `$ref` or `$dynamicRef` that does not resolve within the schema (a pointer token into an array that is not an index included), a cycle of references and in-place applicators that never descends into the value (`{"$ref": "#"}`), and `pattern` or `patternProperties`, since v1 has no regular expression engine that runs in linear time on values from files and agents; what each `$ref` and `$dynamicRef` resolves to is checked as a subschema is (the refused keywords, the dialect, its own references), wherever it is in the schema, under a keyword the dialect does not know included, and must be a valid schema; `uniqueItems` is evaluated in linear time, by the RFC 8785 bytes of each item, which is JSON Schema's equality (`1` equals `1.0`, booleans are not numbers, objects compare by content), since jsonschema compares every pair of objects and one value of `MAX_LIST` objects would cost minutes, and which finds equal items that jsonschema's own check misses by sorting first (`[[1], [true], [1]]`, Python ordering `true` as `1`); evaluating one extension object takes at most 10,000 steps plus 8 per JSON value of the object (`STEPS_BASE`, `STEPS_PER_VALUE`), and never more than 120,000 for a proposal (`STEPS_MAX`), whose value has at most 64 KiB, or 1,610,000 for an operator's or an importer's write (`WRITE_STEPS_MAX`, the budget of 200,000 values, the most a descriptor holds, so that every descriptor within §14's limits gets its whole budget), a step being a keyword evaluation, an error, or every 4 items, members or values a keyword goes through itself (`items`, `contains`, `additionalProperties` and `propertyNames` over `true`, the values `uniqueItems` writes, the items and members `unevaluatedItems` and `unevaluatedProperties` look at), since recursion under `anyOf`, `oneOf`, `not` or `allOf` otherwise evaluates the same values again at every level, in time exponential in the value's depth (a value of 330 bytes, 16 deep, took 25 s and 1.6 GiB); the budget does not grow with the schema, since one growing with its keywords let 20 unused properties and a value padded to 60 KB buy the same recursion 74 s and 3.4 GB; measured, an ordinary schema (an array of objects of string members, with `type`, `additionalProperties`, `required`, and `type` and `maxLength` for each member) takes 2.5 to 3 steps per value, so `MAX_LIST` items of 3 members take about 100,000 steps and of 8 members about 220,000, past a proposal's ceiling but within a write's, while the slowest steps (recursion through references) run at about 150,000 a second, so `STEPS_MAX` is about 0.8 s and `WRITE_STEPS_MAX` about 11 s; values are validated as copies whose `repr` is fixed and `anyOf` and `oneOf` stop a subschema at its first error and keep none, since jsonschema's messages repeat the value and its `anyOf` keeps every error of every subschema, so memory grew with the value times the errors (it now stays near a MiB, and a `MemoryError` is not caught, being no longer reachable by a value); `unevaluatedItems` and `unevaluatedProperties` collect what other keywords evaluated in a set, since jsonschema tests membership against a list (32,000 items took 5.4 s), and give jsonschema's results; a spent budget is refused `LIMIT_EXCEEDED` (`extension_steps`, its `max` the object's budget) at the extension object, naming the remedies (a smaller object, a schema that takes fewer steps, and for a proposal an operator's session), as §14 refuses a limit, and a value the schema cannot evaluate otherwise (a recursion too deep, a reference that does not resolve) `INVALID_EXTENSION` there, neither ever raised; a change or a proposal evaluates the extensions and ontology codes of only the descriptors it adds or changes, and checks every extension against the dataset's `packs`, which needs no schema, so that one large extension value does not slow every later write; an import, a re-import and a session's publish check every descriptor, the publish also running the validators of the dataset's packs, against the registry they are given, since a pack removed or upgraded (its schema or an ontology validator) after a descriptor was written would otherwise let a change to another descriptor publish a release the running registry refuses; schemas whose cost grows with their own depth rather than with the value (nested `anyOf` under `unevaluatedProperties`, a `contains` over hundreds of branches), and recursion through an in-place applicator under `unevaluatedProperties` (`{"allOf": [{"properties": {"child": {"$ref": …}}}], "unevaluatedProperties": false}`), whose work doubles with each level of the value, each level being evaluated once to validate it and once to find what it evaluated (an ordinary value 10 deep spends its budget; jsonschema itself takes 6.9 s at 16 deep), are an accepted limit, since a pack's author writes them and registration is not an agent's; `jsonschema` is a runtime dependency, called by `aibi.core.schema.jsonschemas` alone | §10.1's "on every descriptor write" |
| D248 | Proposals (M1; amends D119) | `propose_descriptor` records {descriptor, pointer or `""` for a whole descriptor, a value or `remove`, evidence of at most 10,000 characters of Unicode text} against the latest published release, `by` from the server in a §5.1 form of `model:`, `agent:` or `importer:` (`operator:` refused); a value has at most 64 KiB (`MAX_PROPOSAL_BYTES`) in RFC 8785 form, checked first (`LIMIT_EXCEEDED`, `proposal_bytes`), so that the cap on open proposals bounds the queue's size too, which leaves larger values (a long `permissible_values` list, a whole descriptor holding one) to an operator's session or an importer; it is checked by applying it as `proposed` to that release's descriptors (models, `check_release`, D247; no data read) and refused with those refusals at `/descriptors/<id>/…`; an open proposal of the same descriptor, pointer, value, proposer and evidence returns its id, and one made against an earlier release is checked again and then counts as made against the latest (no longer `stale`); more than 10,000 open per dataset is `LIMIT_EXCEEDED` (`open_proposals`); the duplicate and the cap are checked before the release is; accepting one is a draft edit that asserts it by the operator with the evidence `Proposal <id>`, never the proposer's name or rationale (D119 named the proposer), which stay in the proposals table that erasure redacts; the draft holds an accepted proposal while it holds what the proposal proposes (the value at its pointer, the descriptor as `put` writes it, or the removal), so a later edit away from it leaves it unaccepted, to be accepted again or rejected; it becomes `accepted` when the session publishes a draft that holds it, and stays `open` otherwise and when the session is discarded; a change reads only the proposals its `accept` edits name; rejecting needs no session, is audited, and is `CONFLICT` for a proposal the open draft accepted and holds, the remedy being to discard the session or edit the draft away from it; one naming a descriptor that is gone is `UNKNOWN_DESCRIPTOR` on accept | §12.3 and §14 ("open proposals capped"); descriptors are never redacted, so the proposer's text stays in the proposals table, which erasure redacts |
| D249 | Curation proposers (M1; amends §5.1) | `Proposal` is a dataclass {descriptor, pointer, value, remove, evidence}; the proposers of the registered packs the dataset lists run on a view of its latest release after every publish (import, re-import, session) and on request (`run_proposers`), their proposals entering the queue by `importer:<pack id>@<pack version>`, deduplicated as in D248, and a proposal the same proposer made before and an operator rejected is not made again; an invalid proposal, one that raises anything, or a proposer that raises, is skipped and reported to the caller (with the label an import, a re-import or a session's publish returns), never raised, since proposers run after a publish has committed | §10.1's "after import, and on request"; not on each draft change, where proposals against a draft would be orphaned by a discard and flood the queue edit by edit |
| D250 | The curation queue (M1) | For the latest published release or a given published label: every curated field whose status is `imported_default` or `proposed`, with id, kind, pointer, status, `by`, `at`, evidence and value; what nobody declared from a fixed list (a table's `role`, `primary_key` and `grain`, a column's `datatype`, the `units` of `number` and `time_offset` columns, a coverage's `parents`, and a relationship without coverage as `cov:…` at `""`); the open proposals, `stale` when made against another release than the latest and `accepted_in_draft` when the open draft accepted them and holds them (D248); the report's notes, as counts and row references; in that order, at most 10,000 items and 8 MiB of them in JSON (`MAX_QUEUE_BYTES`), the first item that does not fit and every one after it counted in `truncated`, and the open proposals read a page at a time so that a full queue reads no more of them, since 10,000 proposals of 64 KiB with their evidence would make about a gigabyte a call (what is left out is listed once earlier items are decided); the release, the open session and its draft are read and pinned under the store's lock, so that a change, discard or withdrawal committing meanwhile cannot sweep what the queue reads; the document that selects invalid rows comes with M2 | §11.1's list, without row data |
| D251 | Drafts and their resolution (M1) | Every state a draft takes is recorded (`drafts`); `resolve(dataset, "sha256:…")` gives `draft` for the open session's current draft and `discarded` for a recorded state no label names and no open session holds; `resolve(dataset)` and a label never give a draft | §12.3's "ids … over a draft state resolve to *discarded*", whose derivation log comes with M2, and "only documents that pin `@draft` read the draft" |
| D252 | The audit trail (M1) | The actions are `import`, `reimport`, `open`, `change`, `take_over`, `publish`, `discard`, `withdraw`, `reject_proposal` and `erase`; the details of all but `erase` (whose own are D223's) hold labels, the manifest, base, previous and draft hashes, the (descriptor id, pointer) pairs a change touched and proposal ids, never values or handles, and redaction leaves them as they are; `actor` is the operator's `operator:<self-declared name>` and `session` is set for session actions | Q7 and §12.3, with nothing for erasure to redact beyond what redaction already reaches |
| D253 | Server configuration (M1) | One TOML file, named by `--config` or `AIBI_CONFIG`, read at start and never reloaded; `ServerConfig` validates it strictly, an unknown key refused and every problem reported at once with its TOML path: `[server]` (bind, port, hostnames, public_origins, cors_origins, tls_certificate and tls_key, max_body_bytes, max_connections, max_connections_per_client, request_head_seconds, send_seconds, `[server.rates]`), `[curator] token_hash`, `[storage]` data (the store and the upload area, D234) and imports (the import directories), `[imports]` (every limit of D233, `concurrent`, at most 20, `upload_idle_seconds` and `upload_min_bytes_per_second`), `[disclosure] min_cell_count_floor` (2 or more), `[databases.<identifier>]` (a `kind`, and a `path` inside an import directory for SQLite and DuckDB or `url_env`, the name of the environment variable that holds the URL, for Postgres and MySQL; nothing else, so no credential) and `[[models]]` (model cards, validated as descriptors, each id once); relative paths are the file's directory's; import directories exist, are directories, and neither hold nor lie inside the data directory, by real paths; the path is resolved once, a component at a time, and refused if a symbolic link on the way lies in a directory its group or others can write (unless that directory has the sticky bit and the link is the server's user's or root's) or if the file's own directory is so writable without the sticky bit; the resolved file is opened without following a link (`O_NOFOLLOW`), and the descriptor, checked by `fstat`, must be a regular file that neither its group nor others can write, owned by the server's user or root, and is the one read (directories further up are the deployment's to protect); the import limits are the server's, never a request's | §11.2 puts connections, model cards and the token's hash in configuration, and one file checked whole refuses a bad deployment before it serves; an import directory over the store would put its blobs and the uploads being written under a confinement root (D234); a file others can write, or own, or can replace by a rename in its directory or by swapping a link on the way to it, lets them make themselves operators by replacing the hash, and checks made on one resolution of the path and a read through another leave a window to swap it in; one set of limits keeps `reader_workers` one bound (D225) |
| D254 | Binding and TLS (M1) | The default bind is `127.0.0.1:8000`; a bind is an IP literal or `localhost`, the loopback interface being `localhost`, 127.0.0.0/8 and `::1`; any other bind needs `tls_certificate` and `tls_key`, a key file others can read being refused, and a wildcard bind needs a hostname too, or the server does not start; uvicorn runs with `proxy_headers` off (the client is the connection's address and the scheme the connection's), without a `Server` header or a WebSocket protocol, with `limit_concurrency` of `max_connections` (64, at least 2), past which uvicorn itself answers 503 (as it does while its requests under way, its tasks, reach that number, a pipelining connection holding two for a moment, which closing an idle connection cannot free), its h11 protocol guarded (`api.connections`): a connection with no request under way `request_head_seconds` (10) after it opened or after its last response is closed, a new connection when `max_connections` are open closes the one idle longest, and a client (its address, keyed as D259 keys it) has at most `max_connections_per_client` open, fewer than `max_connections` (a quarter by default), a new one past that closing the client's longest idle one or, if all of them have a request under way, itself, and a connection whose answers wait past the transport's high-water mark, or that those rules closed with answers still unsent, is aborted and its socket reset unless its client takes at least 1 KiB of them a second on average over each `send_seconds` (30), what it took being what its end acknowledged (`tcpi_bytes_acked` of Linux's `TCP_INFO`) or, where the socket does not say, how far the transport's buffer fell, which the kernel refills only after it has sent about a third of its send buffer, so that there a client reading less than about a third of that buffer each `send_seconds` can be cut off; uvicorn is bound to the minor version whose protocol the guard reads (0.53), and a test checks the internals it reads; a graceful shutdown of 30 s; its logs without query strings and with each path segment that holds a token's or a handle's shape, as written or percent-decoded up to three times, written `<secret>`, and every other word of a line, and an exception's and a stack's text, blanked the same way, whatever the record's shape, by a filter on uvicorn's and the server's loggers and on every handler (D261, D267), under umask 077; `aibi-server serve` refuses to start while another process has the store open (D221); a reverse proxy that ends TLS in front of a loopback-bound server is named by `hostnames` and `public_origins`; a server reachable from other hosts should still sit behind a proxy that has timeouts for request headers and bodies and caps connections per address; behind a proxy every connection has the proxy's address, so `max_connections_per_client` caps the whole server, and past it the server closes each new connection at once, which the proxy's users see as errors rather than uvicorn's 503: set it to `max_connections - 1` there, and cap clients at the proxy; on a TLS bind the guard starts once asyncio has finished the handshake, so a connection still in it has only asyncio's 60 s handshake timeout and no place in `max_connections` or a client's cap, and enough of them can use up file descriptors, which the proxy also prevents | §14's "TLS whenever not bound to localhost"; uvicorn trusts `X-Forwarded-*` from 127.0.0.1 by default, which on a loopback bind would let any local process pick its rate-limit key; uvicorn counts idle connections against `limit_concurrency` and times none before its first response, so connections that send nothing or half a request would otherwise lock every client out, the operator included, before request protection sees a request, and the deadline, the room idle connections make and the cap per client keep that from any one client; uvicorn waits without a deadline for a client to take its answers before it writes more, so a client that pipelines requests the Host check refuses, which no rate charges, and never reads, would keep its connections busy, and out of the other rules' reach, for good; a minimum rate rather than a fixed time lets a large answer go over a slow link, and counting what the client's end acknowledged keeps a client that pipelines and reads steadily but slowly, whose kernel buffer drains long before the transport's does, from being cut off; on a loopback bind every local process shares one address, so the cap does not tell them apart, and one that keeps opening connections can still crowd others out, which v1 accepts of local processes, while in front of an exposed server a proxy's own limits are the stronger defence; a URL a client gets wrong may hold a secret, and an exception's message may quote a request; the data files hold datasets, and only the server's user needs them |
| D255 | One protection middleware (M1) | One pure ASGI middleware, the application's outermost, sees every request to every router, mount and path, unknown ones included, and refuses at the first check that fails: the Host (D256); the Origin and fetch metadata (D257); CORS preflights, each first charged to the client's `api` rate (D258, D259); off the operator router, the client's rate (D259); at `/operator` and below, the token (D261), verified first, a request that fails it refused as `TOKEN_REQUIRED` or, once the client's token-failure limit is spent, `LIMIT_EXCEEDED` (D259), then the client's operator rate (D259), the operator's name (D262), and the CSRF token and the content type (D263); and the declared body size (D260); it then counts the body as it arrives, drops any attribution already in the request's scope before it adds its own, answers an exception the application leaves unanswered with `INTERNAL_ERROR` (logged with its path blanked as the access log's is, D254, and never quoted) and a client that left with nothing, and gives every response `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin` and, unless it set its own, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, operator responses `Cache-Control: no-store`, and with TLS `Strict-Transport-Security`; WebSocket connections get the Host, Origin and rate checks and never reach the operator router; lifespan passes; the application refuses a mount at `/` or at or below `/api` or `/operator`, and the operator router's own dependency refuses a request without an attribution, so it fails closed wherever it is mounted | §14's "one middleware protects every router"; refusing forged origins before the rate check stops a web page draining the bucket that local clients share, and refusing them before authentication keeps a rebinding page away from the token check; verifying the token before any limit that others' requests can spend means no other client, on the loopback interface or behind a proxy where every client shares one address, can lock the operator out with requests, and the connection rules of D254 keep connections that never finish a request, or never read their answers, from doing so; `no-store` keeps handles out of caches |
| D256 | The Host allow-list (M1) | Exactly one `Host`, whose host, in lower case and without its port, is a loopback name (`localhost`, `127.0.0.1`, `[::1]`), the bound address when the bind is a specific one, or a configured hostname (a DNS name or IP literal, with no port, wildcard, trailing dot or IPv6 zone); anything else, or a missing or malformed Host, is `HOST_NOT_ALLOWED` (400); the port is not compared | A DNS-rebinding page reaches the server under its own name, which it cannot make one of these (§14); the port says nothing of who sent the request |
| D257 | The Origin allow-list and fetch metadata (M1) | The allowed origins are the server's own (each allowed host with the server's scheme and port, a default port left out, such as `http://127.0.0.1:8000`) and the configured `public_origins`, each on an allowed host, and on the API and the mounts the `cors_origins` too; another `Origin`, `Origin: null`, several `Origin` headers, and `Sec-Fetch-Site: same-site` or `cross-site` without an allowed `Origin` are `ORIGIN_NOT_ALLOWED` (403); a request without either header passes this check | Browsers send `Origin` on cross-origin and unsafe requests, and fetch metadata catches the no-cors requests (`<img>`, `<script>`) that carry none; clients that are not browsers send neither, and on the operator router the token, not the origin, admits them (§11.2) |
| D258 | CORS (M1) | Off unless `cors_origins` is set: then no response carries an `Access-Control-*` header (one a mount sets is removed) and every preflight is refused; when set, a preflight from one of those origins to the API or a mount, for `GET` or `POST` and no header but `Content-Type`, gets `Access-Control-Allow-Origin` with that origin, the methods and the header, and no credentials, those origins' requests get `Access-Control-Allow-Origin` too, and responses there carry `Vary: Origin`; the operator router never answers a preflight or sends a CORS header | §14, and D73's "same-origin only"; without a preflight, a cross-origin page can send neither `Authorization` nor `Aibi-CSRF` |
| D259 | Rate limits (M1) | Token buckets per client and class, the client being the connection's address, an IPv6 one by its /64 (an IPv4-mapped one, or one of the NAT64 prefix `64:ff9b::/96`, by its IPv4 address; a link-local one, `fe80::/10`, by the whole address and its zone; one of the local-use NAT64 prefix `64:ff9b:1::/48` (RFC 8215) by the whole address, which in each of RFC 6052's layouts stands for one IPv4 address): `operator` (`/operator` and below; 600 a minute, bursts of 50), `api` (every other path, and every preflight; 3,000 a minute, bursts of 200; #12 may give `/mcp` its own) and `token_failures` (10 a minute, bursts of 10); at `/operator` and below the token is verified first: a request that fails takes from `token_failures` alone and, once it is empty, is refused as `token_failures` rather than `TOKEN_REQUIRED`, and a request with the token takes from `operator` alone and is never refused for failures, its client's or any other's; at most 10,000 clients are tracked per class, the least recently seen dropped first; over its bucket a request is `LIMIT_EXCEEDED` (429) naming `operator_requests`, `api_requests` or `token_failures`, with the rate a minute as `max` and `Retry-After` in whole seconds; only requests that passed the Host and Origin checks count | §14's "clients of every router are rate-limited"; the token has 256 bits, so the failure bucket bounds the work failing requests cause rather than guessing, and a SHA-256 before it costs little; were failures checked before the token, or unauthenticated requests charged to `operator`, any local process, or on a proxy's shared address anyone, could lock the operator out with ten requests a minute; the cap bounds memory when many addresses are used; one host is routinely given a whole IPv6 /64, which would otherwise let it pass every limit by changing its low bits; but every host on a link shares fe80::/64, and a translator puts every IPv4 client in 64:ff9b::/96 or 64:ff9b:1::/48, so one of them could take the others' share, while a host that picks many link-local addresses can pass the limits only on its own link, which v1 accepts; behind a proxy all clients share one bucket, which v1 accepts |
| D260 | Request sizes and bodies (M1) | A body is at most `max_body_bytes` (8 MiB, named `request_bytes`), and an upload at most `import_bytes` (D233), streamed to disk and never held in memory; a larger `Content-Length` is refused before the body is read, and a body is counted as it arrives, `LIMIT_EXCEEDED` (413) naming `request_bytes` with the maximum that applies; a JSON body is read by `load_request`: `parse_json` with the rules and limits of documents (§7.1, D190: UTF-8, no duplicate keys, non-finite numbers or lone surrogates, nesting 64, 200,000 values, the pointer caps), then the request's model, strict and closed, every union chosen by a function, refusals pointing into the body as written, a union listing its own members as alternatives, and none quoting the body; each request model bounds its lists and strings; the request line and headers are bounded by h11 (16 KiB) | §14's body limits; FastAPI's JSON handling keeps the last duplicate key and bounds neither nesting nor values; 8 MiB and a document's 200,000 values hold a change that puts one descriptor at its limits (2 MiB and 200,000 values), or several smaller ones, while bounding what one body costs to parse |
| D261 | The curator token (M1) | `aibi_` and 43 base64url characters (256 bits from `secrets`); `aibi-server new-token` makes one and prints it, once, with its hash, `aibi-server hash-token` hashes one read from standard input, and configuration holds only `sha256:<hex>`; every request at `/operator` and below, unknown paths included, carries `Authorization: Bearer <token>`; a missing, malformed, repeated or wrong token, a token standing alone in the URL's path or query or in a cookie (no base64url character touching either end, D265), or the configured token anywhere in them, inside a longer word too (each `aibi_` window of 48 characters hashed and compared in constant time), as written or percent-decoded up to three times, or Basic credentials, is `TOKEN_REQUIRED` (401) with `WWW-Authenticate: Bearer realm="aibi-operator"`, with the same message in every case; a value not in the token's form is refused before it is hashed, whatever the configuration holds, and the SHA-256 is compared in constant time; the server sets no cookie and never asks for Basic credentials; the token is in no log (a path is logged blanked, D254), refusal (D265), response or `repr` | §11.2; a fast hash is enough for a 256-bit secret, where a slow one would make every request costly and the failure bucket a CPU sink; refusing other forms keeps a password from ever being a token; a cookie, or cached Basic credentials, would make the token something a browser sends by itself (an ambient credential), which is what CSRF exploits |
| D262 | Operator names (M1) | Every operator request names its operator in one `Aibi-Operator` header: the UTF-8 name percent-encoded, everything but RFC 3986's unreserved characters encoded, matching `^(?:[A-Za-z0-9._~-]\|%[0-9A-Fa-f]{2})+$`, decoded strictly and in that one encoding (upper-case hexadecimal, no unreserved character encoded), a name of §5.1 (1 to 200 characters, without C0, DEL, C1 or line breaks) without bidi formatting characters (U+061C, U+200E, U+200F, U+202A–U+202E and U+2066–U+2069) and holding no token's or handle's shape standing alone (`SECRET_ALONE_RE`, D265), as written or percent-decoded up to three times; the server attributes the request to `operator:<name>`, the `by` of the service functions and the audit trail's actor; a missing, repeated, undecodable or invalid name is `OPERATOR_REQUIRED` (400), its message quoting nothing; the CLI takes the name from `--operator` or `AIBI_OPERATOR`, with no default, and refuses an invalid one as a usage error that does not repeat it; the other text a request stores, an import's `name` and `original_name` and a change's values, evidence and whole descriptors put, member names included, is refused the same shapes (D265) | Q7's self-declared name, which the audit trail records; HTTP libraries read header bytes as Latin-1, so a Unicode name needs one encoding, and one spelling per name keeps the trail from recording `%41da` as `Ada`; bidi formatting can make one name read as another's; the name becomes the `by` of published curation, so a swapped variable (`AIBI_OPERATOR=$AIBI_TOKEN`) would otherwise put the token into a content-addressed blob that every reader of the release sees, as a pasted token would through a label, a value or evidence, and a name that decodes to a token is one a client or a log may decode; a default, the login name, would record names nobody chose |
| D263 | CSRF tokens (M1) | A browser request is one with an `Origin` or a `Sec-Fetch-*` header; every browser request to the operator router but `GET /operator/csrf` carries `Aibi-CSRF`, whose value is the token that route returns: base64url of HMAC-SHA256, keyed by 32 random bytes the process draws at start, over the configured token hash, compared in constant time, else `CSRF_REQUIRED` (403); every operator request whose method is not `GET`, `HEAD` or `OPTIONS` has `Content-Type: application/json` (`application/octet-stream` for an upload), else `UNSUPPORTED_MEDIA_TYPE` (415); a request with neither header is not a browser's and is accepted with the curator token alone, which is how §11.2's "requests without an Origin header are accepted only with the token" is read | No operator credential is ambient (D261), but a browser can still be made to attach one (an extension or a proxy that injects `Authorization`), and only a page of this server's origin can read the CSRF token (CORS is off there, D258); the token changes at each start and with the curator token; content types that are not "simple" force a preflight on any cross-origin form |
| D264 | The operator router (M1) | The application mounts it at `/operator`, and the MCP transport never does; reads are `GET` (`/csrf`, `/datasets`, `/datasets/{d}`, `…/queue` and `…/descriptors/{id}` of a release or the draft) and change no release, label, session, proposal or audit entry (the store's housekeeping when a pin is released may still run, as for any read, and `HEAD` is 405); changes are `POST` (uploads, import and re-import, withdraw, erase, proposers, reject, and the session's open, change, publish, discard and take-over; accepting a proposal is an `accept` edit, D248), each one synchronous request whose service call runs in a worker thread that a dropped connection does not cancel; bodies are JSON objects (`{}` when empty), read by `load_request` in a worker thread, never on the event loop or by the framework; responses are `schema.operator`'s outputs; a dataset with no label is `UNKNOWN_DATASET` (404) on every route that names one but an upload and an import, which make it, checked once the body is read and a `concurrent_imports` place taken, so a body refused or a place refused is answered as for any dataset; OpenAPI is generated but not served, and neither are documentation pages; trailing slashes are not redirected | §11.2; imports take minutes (D225), and an operator does not expect a half-done operation after a dropped connection; the core's loader refuses duplicate keys and quotes no input; one code for a dataset that does not exist, whichever route names it, rather than each service's own (`UNKNOWN_RELEASE`, `NO_SESSION`); parsing and validating a body of 8 MiB takes a fraction of a second, which on the event loop would hold up every other request, Host checks included; the documentation pages load scripts from a CDN |
| D265 | Refusals over HTTP (M1) | Every error response on every router is `{"refusals": [Refusal, …]}`, at least one, and all of a change's failing stage (D246) or of an import; the status is the first refusal's: 400 `HOST_NOT_ALLOWED` and `OPERATOR_REQUIRED`; 401 `TOKEN_REQUIRED`; 403 `ORIGIN_NOT_ALLOWED` and `CSRF_REQUIRED`; 404 `NOT_FOUND`, `UNKNOWN_RELEASE`, `UNKNOWN_PROPOSAL` and `UNKNOWN_DATASET`; 405 `METHOD_NOT_ALLOWED`, listing the methods allowed; 409 `DATASET_BUSY`, `CONFLICT`, `NO_SESSION`, `NO_CHANGE`, `DATASET_EXISTS`, `RELEASE_WITHDRAWN` and `ERASURE_BLOCKED`; 411 `LENGTH_REQUIRED`; `LIMIT_EXCEEDED` 408 for `upload_idle_seconds` and `upload_seconds`, 413 for `request_bytes`, 429 for a rate and 503 for `concurrent_imports`, both with `Retry-After`; 415 `UNSUPPORTED_MEDIA_TYPE`; 500 `INTERNAL_ERROR`, with no detail (the server logs it); and 422 for every other code, a pack's and every other limit included; Starlette's own errors and FastAPI's errors of path and query parameters are refusals too, a parameter named but not quoted, and only what uvicorn answers before the application runs (a malformed request, a connection past `max_connections`) is not; no response writes a handle, a token or an erasure key, and every refusal the server answers, the loader's, each service's and request protection's alike, writes `<secret>` for anything of a token's or a handle's shape in its path, message and alternatives (`SECRET_RE`, `(?:aibi\|ses)_[A-Za-z0-9_-]{43}`, inside longer words too, and for each word or pointer segment that holds one once percent-decoded up to three times; `blank_secrets`, applied where every handler answers); text a request gives that the server keeps (D262) and that holds one of those shapes standing alone, no base64url character touching either end (`SECRET_ALONE_RE`, the same pattern between `(?<![A-Za-z0-9_-])` and `(?![A-Za-z0-9_-])`), as written or percent-decoded, or that holds the configured curator token anywhere, as D261 finds it, is refused as `INVALID_VALUE` (422) at its pointer, a member name of that shape written `<secret>`, before any service runs; pointers and ids, which name what exists, are left to the services, whose refusals are blanked | §8.6's refusal is the one error shape on every router; FastAPI's default errors quote their inputs, and services quote the pointer or member they refuse, so a refusal would carry a handle into whatever logs it; blanking also matches the shape inside ordinary long names (`ses_` or `aibi_` and 43 more of `[A-Za-z0-9_-]`, as in `courses_completed_before_enrollment_in_the_program_2024`), whose refusal then no longer locates them: accepted, because the refusal's code still says what failed and blanking by shape needs no knowledge of which handles exist; refusing input for such names would refuse columns and tables the server itself names, and the descriptors it serves, while a pasted or swapped secret stands alone, so input is refused only for a shape standing alone, and a name that is exactly such a shape between delimiters, which is rare, is the one name refused; refusing stored text keeps a pasted secret out of releases every reader sees, where blanking could only hide it in answers; the server knows the token's hash, so the real token is caught wherever it sits while no name is refused |
| D266 | Imports and uploads over HTTP (M1) | `POST …/uploads?extension=<ext>` streams an `application/octet-stream` body into the upload area (D234) and returns `{upload: "<sha256 hex>.<ext>", bytes}`; an import or re-import names its source as `{"path": …}`, an absolute server path confined to the upload area and the import directories by a fresh `Confinement` (D232), or as `{"upload": …}`, an upload of the same dataset, and may add `original_name` (which names the dataset and the tables, D226), `name` and `pack`; its limits are the server's, and its `at` the server's clock; at most `imports.concurrent` (2, at most 20, half of the 40 worker threads the server leaves anyio's limiter) uploads, imports, re-imports and erasures run at once in the server, and one more is refused at once, `LIMIT_EXCEEDED` (503) naming `concurrent_imports`, never queued; an upload declares its length in one `Content-Length`, which `Transfer-Encoding` does not frame, or is refused, `LENGTH_REQUIRED` (411); an upload whose body sends nothing for `imports.upload_idle_seconds` (60) is refused, `LIMIT_EXCEEDED` (408) naming `upload_idle_seconds`, and so is one that has not ended by its deadline, `upload_idle_seconds` plus its declared length at `imports.upload_min_bytes_per_second` (32 KiB/s) after it began, `LIMIT_EXCEEDED` (408) naming `upload_seconds` with that deadline in whole seconds; either way its partial file is removed and its place and thread freed, and an upload that keeps sending within its deadline is never refused for its gaps; an operator request's JSON body has the same two deadlines, its length taken as `max_body_bytes` when it declares none | §14 (paths built from hashes; confinement); D225 and D233 leave the server-wide bound on concurrent imports to the operator surface, because what an import holds in memory multiplies with the imports that run at once and `reader_workers` bounds only the worker processes; each of these holds a worker thread while it runs, which every read and operation shares, so slow uploads could otherwise take them all, and a stalled or half-open connection would hold its place until a restart, a trickle of a byte a minute for as long as it kept sending; a deadline that grows with the length refuses a trickle without refusing a large upload over a slow link, and an upload that declares no length would get the longest (about 9 h by default) although the CLI always declares one, while h11 lets `Transfer-Encoding` frame a body whatever its `Content-Length` says; a JSON body that stalls would keep its connection busy, out of reach of D254's rules; and the cap on `concurrent` keeps the reads their threads; refusing at once matches the slots (D236); the upload area's total size is not capped in v1 (only an operator can upload, and erasure and each dataset's deletion clear it) |
| D267 | Session handles over HTTP and in the CLI (M1) | Handles travel only in bodies: `open` and `take-over` return one; `change` carries `{handle, expected, edits}`, and `publish` and `discard` `{handle, expected}`; a handle is checked for its form (`ses_` and 43 base64url characters), then by the store (`CONFLICT`); it is never in a URL, a header, a log, a refusal or a `repr`, and `GET …/datasets/{d}` shows the session's id, base, draft and opener, never its handle; the CLI keeps each (server, dataset)'s handle and last draft in `$XDG_STATE_HOME/aibi/sessions.json`, mode 0600 in a 0700 directory, written atomically, sends that last draft as `expected`, never one fetched afresh, replaces the entry on open, on take-over and after each change, forgets it on publish and discard, lets a handle read from standard input (`--handle -`, from a prompt without echo on a terminal) or from `AIBI_HANDLE`, and `--expected`, override it, never takes a handle as an argument, refuses one not in its form as a usage error, and prints a handle only with `--show-handle` | D244 keeps handles out of the database, and a URL or a header can reach access logs, and an argument the shell's history and `ps` (D268); an `expected` fetched just before a change would defeat conflict detection (§12.3) |
| D268 | The operator CLI (M1) | `aibi` talks only to the operator router, over HTTP, and imports no store, importer, engine or server module (an import-linter contract, and a check in a fresh interpreter); its commands are `status`, `queue`, `show`, `upload`, `import` and `reimport` (by a path on the server, or `--upload` of a local file), `withdraw`, `erase`, `proposers`, `reject`, and `session` `open`, `change`, `set`, `confirm`, `remove`, `put`, `remove-descriptor`, `accept`, `publish`, `discard` and `take-over`; the token comes from `AIBI_TOKEN` or, on a terminal, a prompt without echo, never from a flag or a file; an argument holding a token's or a handle's shape standing alone (`SECRET_ALONE_RE`, D265), as written or percent-decoded up to three times, is a usage error before any request, and so is a dataset that is not an identifier, since datasets go into URLs; a usage error repeats no value argparse would quote (unrecognized arguments, invalid choices and values, ambiguous options), and every message the CLI writes has token and handle shapes blanked; `erase` reads the key as a JSON array of its values from standard input or, on a terminal, a prompt without echo, never from an argument; a file or standard input a command reads that is not UTF-8 is a usage error; the server from `--server` or `AIBI_SERVER` (`http://127.0.0.1:8000` by default), a token going over plain HTTP only to a loopback host; it takes no proxy from the environment, follows no redirect and verifies TLS (`--ca-bundle`); changes wait without a read timeout, and reads time out after 120 s; requests ask for no content coding (`Accept-Encoding: identity`), and an answer that cannot be read, whatever `httpx` raises, is unreachable; every string from the server is printed with control, bidi-formatting and separator characters escaped, a refusal as its code, path, message, alternatives and limit, and `--json` prints each answer's JSON, indented, those characters written as JSON escapes, so it is the value the server sent; exit codes are 0 done, 1 refused, 2 usage (a missing token, name or handle), 3 unreachable and 130 interrupted (the server may still finish); `aibi-server` has `serve`, `check` (the resolved bind, allow-lists, directories and limits, without the hash), `new-token` and `hash-token` | §11.2; a flag lands in the shell's history and `ps`, where an erased person's key would outlast the erasure, and an argument goes into a URL, a body or a refusal the CLI prints; a proxy from the environment would carry the token to a third party; text from data could otherwise drive the terminal (A6) |
| D269 | Erasure and proposers on the operator router (M1; amends §11.2) | Erasure is an operator operation: `POST …/erase {table, key, redact_only}` calls `erase` (D223) as the operator, with the upload area's `delete_dataset` as its hook, and returns the labels withdrawn and the counts; the key (1 to 16 JSON scalars) is read from the body alone, and is never logged, quoted or shown by a `repr`, and the CLI never takes it from an argument (D268); running the curation proposers on request (`POST …/proposers`, D249) is an operator operation too | The server holds the store's lock (D221), so nothing else can reach `erase` while it runs; erasure withdraws releases, which §11.2 reserves for operators; D249's "on request" had no caller |
| D270 | Catalogue statistics (M1) | Every build (import, re-import, draft change) writes the manifest's `statistics` blob, `{"format": "aibi.statistics/1", "tables": {…}}` in RFC 8785 form: per table `n_rows`, and per column the counts of its four cell states and one distribution: categories for `category`, `boolean` (`"false"`, `"true"`) and `list<category>` columns (a row counted once under each PRESENT item it holds, `multi_membership`), the declared permissible values first in their listed order with their zero counts, then the others in canonical order, at most 150 (§14's cap on levels; else none, `categories`); histograms for `number`, `integer`, `time_offset`, `date` and `datetime` columns, 10 equal-width bins over the declared `range` or, without one, between the smallest and the largest PRESENT value, bins [eᵢ, eᵢ₊₁) with the last closed and open *below* and *above* bins, dates placed by their day and datetimes by their microsecond in UTC, whose edges are whole units (floor division) with at most as many bins as the span has units, edges and extremes written in the column's type; and none for identifier columns (declared, and every primary-key, foreign-key and coverage parent column, `identifier`), `string` columns (`text`), undeclared datatypes (`undeclared`), values or ranges beyond ±(2^53 − 1) (`out_of_range`) and histograms with neither a range nor a value (`empty`); the counts are made before any disclosure setting applies; a draft change's reused table keeps its statistics while its blob, its columns' stored datatypes, ranges and permissible values and its identifier columns are unchanged, and is otherwise counted again from its blob; a release built before statistics were kept has none, and the catalogue never counts them itself: `search_catalog` leaves such a dataset out (its index entry deleted, a warning logged by the dataset's id), and `describe_dataset`, `describe_column` and, under *k*, `curation_queue` refuse it, `NOT_SUPPORTED` at `/release` listing the published labels, whose remedy is a new release (a session's change, or a re-import of changed files), whose build counts them | §5.2 computes statistics when the release is built; §14's catalogue tools read no rows, and counting a release on first use would read them on a tool call and keep a blob no manifest names, while no release outside development was built before v0.8.7; the floor is configuration, not release content, so counts are disclosed when served (D271); counting reused tables again on every edit would make curation cost a scan of the data; keys are identifiers, whose values would otherwise sit in every release's statistics |
| D271 | Disclosure of catalogue statistics (M1; refines §8.4) | The effective *k* is the largest of the floor and the dataset's `min_cell_count`; a column's state counts and its table's `n_rows` are one linked set, in which a single suppressed state count takes the smallest non-zero other state with it, and `n_rows` is suppressed only when it is from 1 to *k* − 1 itself, and then with every state count of the table, as a breakdown whose total is (a column's states sum to `n_rows`, so it is never the smallest other); a distribution is suppressed whole (`suppressed`) while its PRESENT count is; categories with a count from 1 to *k* − 1 are pooled into one row (`pooled`), which suppresses the distribution when it holds 1 to *k* − 1 itself, and a list column's pooled row has no count; a histogram whose edges came from the data is not reported under *k* (`no_declared_range`); bins merge as §8.4 says, and a bin left with 1 to *k* − 1 values suppresses the histogram; minima and maxima are not reported; categories whose listed values an output cannot carry (not Unicode text, longer than 10,000 characters, or a reference pointer longer than 16,384) are not reported, with or without *k* (`unrepresentable`); an output carries `SUPPRESSED` wherever something was suppressed, pooled or merged; and under *k* no descriptor a tool or a resource serves carries the `evidence` of its curation entries, the operator router serving them whole; declared permissible values are listed with their zero counts, so under *k* the declared values missing from a disclosed list are those pooled, each counting 1 to *k* − 1, which with a closed vocabulary bounds each (exactly, when the pooled count is *m*(*k* − 1) for *m* values), as §8.4's null cells do; descriptor text (labels, definitions, descriptions, extension members) is declared content, served as written, and no importer, the core's or a pack's, writes counts or other values computed from the rows into it (§10.1): its counts go into the import report, which D277 discloses, or into evidence, and an importer's tests import two sources that differ only in their rows and find the same text | A distribution shown beside a suppressed PRESENT count gives it back as the sum of its categories or bins, which §8.4's linked set alone would leave shown; a list column's rows count under several values, so the sum of its pooled values is no count of rows, and a row with several rare values could make it reach *k* alone; evidence is free text that the importer writes from counts ("195 of 198 cells parse", "whose 10 cells are all distinct") and an operator or a pack may write so too, which no pass can read for counts, while making the importer's count-free would take the curator's reasons away and bind no one else; a bound of 1 to *k* − 1 is what any suppressed count discloses, and leaving declared zeros out would hide which declared values do not occur, which curation needs; no pass can read counts in free text either, so the rule binds the code that writes it, as §10.1 binds packs, rather than dropping every pack's declared text under *k* |
| D272 | Statistic references (M1; refines D202) | A reference's pointer addresses the descriptor's statistics as disclosed: `/n_rows` of a table; `/states/<STATE>`, `/categories/<value>` (the value as a pointer token), `/pooled`, `/bins/<i>` (by position among the disclosed bins), `/min` and `/max` of a column; and `/report/<i>` of the dataset for the count of the *i*-th note of the import report, as the curation queue lists it; `?floor=<n>` is appended whenever the deployment sets a floor, whichever of the floor and the dataset's setting is larger | A reference names its number by its own text: the dataset's setting is in the manifest, and the floor is not; the queue's counts are the report's, which lives in its release |
| D273 | The catalogue index (M1) | The app DB's `catalog` table (migration 3) holds one entry per dataset, for its latest published release, with the basis it was built on (the manifest, the floor and the registered packs' versions); reading the catalogue builds again the entries whose basis changed and deletes those of datasets with no published release left, writing an entry while its release is pinned, under the store's lock, and only if that release is still the dataset's latest published one, so that a release withdrawn while its entry was built, by an erasure too, is not indexed; an entry holds descriptor text, concepts, facet values and disclosed counts, never cell values, and an erasure deletes the dataset's entry; pack facets are called on a view of the release for each registered pack the dataset lists and named `<pack id>.<name>`, and a facet that raises, or returns anything but at most 64 identifiers naming lists of at most 64 strings of Unicode text of at most 4,096 characters, is left out and logged by its pack alone, never raised | §12.2 keeps the index in the app DB; building it when it is read needs no hook in every publish, withdrawal and erasure, and follows a new floor or pack at once; a pack's facet must not take the catalogue down, nor put an unbounded value in it |
| D274 | Searching the catalogue (M1) | `search_catalog`'s filters must all hold: `text`, whose white-space-separated words each occur, case folded, in the dataset's id, label, name, definition, description or tags, or a table's or column's id, label or definition, a grain or an endpoint's id or label; `domain_tags` (case folded); `data_use` by system and code; `concepts` that a table, column or endpoint maps to by an asserted mapping (§5.7); `roles`, each held by some table; `min_rows`, held by some table other than a coverage table; `completeness`, some column (of the concept and datatype if given) whose PRESENT count is at least `min_present` of its table's rows, compared as rationals with `min_present` read as the decimal its shortest spelling writes (7 of 100 meets 0.07); and `facets`, each listing values the pack's facet holds; counts are read as disclosed, so a suppressed one meets no threshold; hits are in dataset order, `limit` at most 50 (20 by default) from `offset`, with `total` and `next_offset`, each listing at most 100 tables (`tables_left_out`) | §11.1's facets, over what an agent may see, so that no filter tells a suppressed count apart from another; one order makes pages stable |
| D275 | Describing a dataset (M1) | `describe_dataset` gives, for the latest published release or the label, manifest hash or `draft` it pins: the dataset descriptor, each table's descriptor with its disclosed rows and a page of its columns (id, label, datatype, identifier; `columns_offset` and `columns_limit`, at most 10,000 and 2,000 by default, over every table's columns in table and column order, with `columns_total` and `columns_next`), the relationships, coverage and endpoints, the table graph (tables other than coverage tables, and the relationships), `applicable_analyses` (empty until M3, §9.4) and the standing caveats: one `UNCONFIRMED_SEMANTICS` naming the first 16, and counting, of every field §5.1 says queries read (a column's datatype, permissible values and missing codes, a numeric column's units, a relationship's key columns, a primary key, a coverage's record filter, parent scope and parents, a relationship without coverage included) whose status is `imported_default`, `proposed` or `undeclared`, and a `COVERAGE_PROPOSED` for each coverage whose `parents` is proposed; a draft carries `DRAFT_RELEASE`, and is disclosed under the largest of the floor, its own setting and the latest published release's; `SUPPRESSED` names the rows of each table whose rows are `null`; a withdrawn release is `RELEASE_WITHDRAWN`, a discarded state or an unknown pin `UNKNOWN_RELEASE`, both at `/release` listing the published labels (the tools' refusals of a release, the queue's included), and a dataset with no label `UNKNOWN_DATASET`, listing the datasets that have a published release | §11.1's "those any query on the dataset would raise from its descriptors", read as those a query can raise from the descriptors alone, since which fields a query reads is its own; a release may hold 1,000 tables of 4,096 columns, which do not fit one answer; a session could otherwise remove the published setting and show the unpublished draft's raw counts to any agent (A3 for the labels) |
| D276 | Describing a column (M1) | `describe_column` gives the column's descriptor (under *k* without its evidence), whether it is an identifier (declared or implied, §5.4), its table's rows, its four state counts and its distribution as disclosed (D271; `unrepresentable` for values an output cannot carry, rather than a failure), and the caveats of its own fields; an unknown table or column is refused (`UNKNOWN_TABLE`, `UNKNOWN_COLUMN`) at `/column`, listing the tables or the table's columns | §11.1; A3 |
| D277 | The queue and proposals as tools (M1) | `curation_queue` gives D250's queue of a published label with the effective disclosure setting and its caveats, each note's count with its reference (D272); under *k* its notes are disclosed with the release's statistics as one linked pass: an `unparsed` note's count is shown only while it is its column's `UNKNOWN` count as disclosed, and every other note's (a `gap`'s or a `dropped`'s, or a pack's of any kind), which counts rows the statistics do not, is suppressed, a suppressed count being `null`, its reason in `not_estimable`, its rows dropped; a note without a count lists no rows either; each note's message is its kind's fixed text, and no field or proposal carries its evidence, all of which is left out before the queue's byte budget (`queue_bytes`, D250) is spent, so that the public queue holds as many items as fit what it shows; the operator router's queue stays the curator's working view, with counts, messages and evidence as written and no references; `propose_descriptor` takes the dataset, the proposal (D248) and `agent`, a §5.1 name without bidi formatting or a token's or handle's shape standing alone (D262), records it by `agent:<agent>`, and returns the proposal's id, release, label and `by`; a proposal's value and evidence holding a token's or handle's shape standing alone, or the curator token anywhere, are refused (`INVALID_VALUE`, D265); it is not idempotent (a proposal decided meanwhile is made again); a client's calls of it are admitted at a rate of their own (`[server.rates] proposals`, 30 a minute in bursts of 10, `proposal_requests`, 429), agents together hold at most 5,000 of a dataset's open proposals (`MAX_AGENT_PROPOSALS`, half of `MAX_OPEN_PROPOSALS`, `agent_proposals`), and the agents of one client, keyed by its address as D259 keys it and recorded with each agent's proposal (the proposals table's `client`, migration 3), at most 500 of them (`MAX_CLIENT_PROPOSALS`, `client_proposals`); an operator takes the agents' share back by rejecting every open proposal of one proposer, or of every proposer of a kind, at once (`POST /operator/datasets/{d}/proposals/reject` with `proposer` or `kind`, `aibi reject-all`), keeping those the open draft accepted and holds, audited as `reject_proposals` with the proposer (`<kind>:*` for a kind) and the counts, which adds to D252's actions, D264's routes and D268's commands | §11.1 attributes external clients by the name they declare, and a stateless transport keeps no session in which to declare it; P1 and §8.4 govern the public queue, while operators curate the data itself (§14's trust model); what a proposal stores is shown to every reader of the queue; an `unparsed` note counts cells its column's `UNKNOWN` counts, so a count shown beside a suppressed or a larger `UNKNOWN` gives back what the statistics hid, and a note's words quote counts and a dropped proposal's evidence; a client without a token could otherwise fill `MAX_OPEN_PROPOSALS` and refuse everyone else's, and an agent's name, which it chooses, bounds nothing, so the bound is by client address and by agents as a whole: one address fills 500 in about 17 minutes at its rate, and ten are needed to use up the agents' share, which one operator request then clears; stale agent proposals are not expired, since a proposal's age says nothing of its worth and expiry would drop an honest agent's unseen; a pack's note may quote any count, and text the public never sees should not take the room of items it could see |
| D278 | The MCP transport (M1) | The official MCP Python SDK, 1.30 or later within 1.x (the first with `max_request_body_size` and `streamable_http_client`; 2.x depends on httpx2, which would turn Starlette's `TestClient` into another client than the CLI's httpx, D268), its low-level server over stateless streamable HTTP answering in JSON, at `POST /mcp` alone, behind request protection (D255: its own DNS-rebinding check off, its body limit `request_bytes`, the `api` rate of D259); a body is received within D266's deadlines, with `[server] tool_body_idle_seconds` (10 s by default) as its idle time, since the tools take no token, and the upload rate (`tool_body_idle_seconds`, `upload_seconds`, 408), then read by the document loader's rules (§7.1, D260; -32700), on the event loop for a body of at most 4 KiB (an `initialize`, a `ping`, a notification or a list, about a millisecond) and otherwise on the tools' threads as a call of the client's, and as one JSON-RPC message the server takes: an object with `jsonrpc` `"2.0"`, an integer or string `id` if any, and a string `method` (-32600) that it answers (`initialize`, `ping`, `tools/list`, `tools/call`, `resources/list`, `resources/templates/list`, `resources/read`, and the notifications `initialized`, `cancelled`, `progress` and `roots/list_changed`; -32601), with `params` an object shaped as that method takes them (-32602), no string or member name anywhere holding a token's or handle's shape standing alone or the curator token (-32600, or -32602 within `params`); each is answered 400 with its refusal, blanked, as the error's data and the message's `id` when that is valid and holds no secret, `null` otherwise; the tools are exactly `search_catalog`, `describe_dataset`, `describe_column`, `curation_queue` and `propose_descriptor`, with schemas generated from their models and descriptions of the server's own text that carry §11.1's rules, which the server's instructions repeat; a call's arguments are written back as JSON and read by `load_request`, its output is the result's structured content and the same JSON as text, and its refusals a tool error (`isError`) holding `{"refusals": […]}`; no handler raises, anything unexpected being `INTERNAL_ERROR`, logged by its type alone; the root logger and the SDK's loggers carry D254's filter, and the root logger writes to the server's filtered handler; a call runs in a worker thread among at most 8 of their own (`tool_calls`), of which one client, keyed as D259 keys it, holds at most 2 (`client_tool_calls`), holding its place until its function returns in the thread, within one 30 s from its body's arrival that the reading of its message, its wait and the call share: one that gets no place in time is refused naming `client_tool_calls` or `tool_calls`, one that runs past it naming `tool_seconds` (all 503), its thread left to finish with its place, and one answered before its thread began gives its place back and never runs; an import-linter contract keeps the catalogue and the transport from importing the operator surface, the importers, sessions, erasure and the HTTP application | §11.1 and A4: the SDK is what clients speak; a session per client would be state an agent could multiply without bound; the SDK parses bodies with Python's `json` (duplicate keys kept last, nesting unbounded) and quotes exceptions and validation errors in its answers; the SDK's Pydantic validation quotes a message's values in its answers and in a warning on the root logger, which a secret given by mistake would reach; a body read without a deadline would bring back the idle lockout of D254, and a public body needs no upload's minute; tool calls must not take the threads the operator router and imports share (D266); a thread cannot be killed, so a place given back when its call is answered would let slow calls pile up threads without bound, and catalogue reads are bounded by their inputs, while M2's queries run in killable workers (§14); one client's calls, well within the `api` rate, could otherwise take every place, and every other client's `ping` and `initialize` would wait behind them, while a limit for the reading and another for the call would let a call take twice its 30 s |
| D279 | Descriptors as resources (M1) | `aibi://dataset/<id>@<n \| sha256:<hex> \| draft>/<descriptor id>`, `aibi://concept/<id>`, `aibi://analysis/<id>@<version>` and `aibi://model/<id>` read as the descriptor's RFC 8785 JSON (`application/json`), a dataset's without curation evidence under *k* (D271), a draft's under D275's setting; the list names each dataset's descriptor in its latest release, the core's and the packs' concepts and the configuration's model cards, at most 1,000, and the templates name the rest; a URI of more than 2,048 characters or of another form, or naming nothing, is `NOT_FOUND`, and a withdrawn or unknown release D275's refusal, listing the published labels, a JSON-RPC error (-32002, or -32603 for `INTERNAL_ERROR`) with its refusals as data; no analysis resolves until M3 | §11.1; a list of every descriptor of every release would be unbounded, and templates say how to name any |
| D280 | The tools over HTTP (M1) | Each tool is `POST /api/tools/<name>`, taking `Content-Type: application/json` alone (`UNSUPPORTED_MEDIA_TYPE`, 415), its body received within D266's deadlines, and running the MCP server's calls; refusals take D265's statuses: `tool_seconds`, `tool_calls` and `client_tool_calls` 503, `proposal_requests` 429 and `tool_body_idle_seconds` 408 | §11.1's "one set of Python functions backs both"; a route per tool gives the UI (M5) one generated type per tool; arguments are JSON objects, which a query string does not carry |
| D281 | Sorting and the fixpoint (M2) | Step 8 sorts every order-insensitive collection, innermost first, by its members' RFC 8785 serialisation compared as UTF-16 code units, and keeps one member of each serialisation. Resolution applies steps 4 to 6 and removes duplicates as step 8 does while it builds the tree (D212), so sorting after it removes no clause and its form is the fixpoint. A cohort's clause tree is its top-level `all`, a single member unwrapped. The canonical form is a document but for its manifest hashes: with each read as a pin of its dataset, a unit key's as its dataset id, and each string that starts with `$` written `$$…`, as a document writes a literal one (§7.1), it canonicalises to itself; `canonical.as_document` is that reading | One order that needs no knowledge of types sorts constants, keys and clauses alike, and RFC 8785 already orders keys this way; reading the form back is how "canonicalising a canonical form changes nothing" is tested |
| D282 | Cohort ids (M2) | A cohort id hashes exactly `{cohort, unit, semantics_version, disclosure, packs}`: the unit table's id, semantics version 1, the effective `min_cell_count` (the largest of the floor and the dataset's setting, `null` when neither is set) and each pack involved with its results version (`{}` when none); the computation id hashes the same object without `disclosure`. Each top-level clause's leaf key hashes its canonical form; each leaf as written maps to the sorted keys of the clauses it became part of, and one that became none (a reference to an empty cohort) is left out. Every hash is the lowercase hex SHA-256 of the object's RFC 8785 UTF-8 bytes | §7.6 lists the members; the rest pins what a second implementation must write to get the same bytes |
| D283 | Digests and rounding (M2) | A digest hashes exactly the digested members as the output carries them after the disclosure pass: members whose names end in `_text` are left out at every depth, caveats become `{code, severity, affects}`, `DRAFT_RELEASE` left out, sorted by serialisation and de-duplicated, and every floating-point number (a double, integral or not) is 0 below 1e-12 in magnitude and otherwise its exact value correctly rounded to 10 significant digits, ties to even, read back as the nearest double. Integers are never rounded, and a non-finite number is refused, as is one that rounds past the largest double (`1.7976931348623157e308` rounds to `1.797693135e308`, which reads back as infinity) | Correctly rounded decimal conversion is the one rounding every language offers identically, and outputs hold no member whose name data chooses, so leaving out `_text` members removes rendered text only; clamping an overflow to the largest double would give two values one digest, and no count or estimate comes near it |
| D284 | Views until M3 (M2) | Phase 2 needs the registry: until M3 views are syntax-checked by the loader, reported unchecked and given no id. The core gives the canonical view and its result and computation ids from their parts (analysis id and version, cohort ids in view order, `reference` and `overlap` where the analysis declares them, canonical params, packs, effective disclosure); the computation id puts each cohort's computation id in the view. A view that names no cohorts orders them by their computation ids | §15 defers views to M3; ordering by ids that include the disclosure setting would let a raised floor reorder a view's cohorts, changing its computation id and so the resampling it seeds (§9.3) |
| D285 | Pack leaves (M2) | A pack leaf's kind must be installed (`UNKNOWN_KIND`, listing the installed kinds). The leaf as written, `kind` included, is checked against its kind's schema as the pack had it when it was registered, where the schema is checked as an extension schema is (D247); a failure is `INVALID_VALUE` where it is, naming the keyword. The checks of a document's pack leaves share one budget of steps, 10,000 and 8 per JSON value of its pack leaves, at most `WRITE_STEPS_MAX` (`pack_leaf_steps`). Each distinct leaf is compiled once per release, with a view of the release's descriptors of its own (each descriptor a deep copy made the first time the compiler reads it, in a mapping that cannot be changed and that holds the release's own descriptors only in closures, so that no attribute of it reaches them; it is no sandbox, since a pack's code runs in the server's process and introspection reaches anything), so that no leaf's expansion, in any cohort, depends on what another leaf's compiler did to its view; a compiler's refusals point below the leaf; an expansion must be a list of core clause models valid as a document's, with no pack, `ids` or `cohort` leaf at any depth (`PACK_FAILED` otherwise), and a document's expansions hold at most `MAX_VALUES` JSON values together (`expansion_values`). The expansion is resolved where the leaf is, every refusal inside it placed at the leaf with its message saying so, and every node of it takes the leaf as its origin. A summary is a list of at most 64 segments (`PACK_FAILED` otherwise), kept beside the form. The compiler and the summary each get a leaf of their own, built from the leaf's JSON, so that what they do to it reaches neither the document nor another reader, and the compiler a view of the release (dataset, manifest, packs and descriptors) whose label it cannot read. An exception a compiler or a summary raises, other than a compiler's refusal and a `MemoryError`, is `PACK_FAILED` at the leaf with a message of the core's, never the exception's (a `RecursionError` included, the pack's code or its expansion being too deep); the log names the pack and the exception's type only. A `MemoryError` is raised as it is: the process ran out of memory, which is not the pack's failure. A cohort as written has at most 256 pack leaves (`pack_leaves`), checked when it is loaded. Compilers, summaries and caveat rules run in the server's process with no time budget of their own until the query tools run canonicalisation under a tool call's limit in §14's workers (M2) | A pack is trusted code but its leaves are an agent's input: the schema and the budgets bound what one document can make the core do, and placing refusals at the leaf keeps every path pointing into the document as written; one pack's bug refuses the leaf, not the whole document, and its message, which may quote the leaf, reaches no one; a label would let one manifest's expansion differ between its draft and its release; a pack that changed the release's own descriptors would change every later reader's, giving one id two digests, and one that changed a view shared with other leaves would make a cohort's id depend on its siblings; copying only what a pack reads keeps a view free on a release of thousands of descriptors, which copying them all (some 150 ms for 3,000) per cohort was not |
| D286 | Pack versions (M2) | Every pack the document's `packs` names must be installed at a version its specifier admits, pre-releases included (`PACK_UNAVAILABLE` at `/packs/<id>`, naming the installed version or listing the installed packs); a pack leaf's pack need not be named there. Every pack a cohort's dataset lists must be installed (`PACK_UNAVAILABLE` at the cohort's dataset reference). A pack's version is recorded beside the id; only its results version is hashed | The results version of every dataset pack is part of the id, so a missing pack leaves no id to give; an operator who installs a pre-release means it to be used |
| D287 | Caveat rules (M2) | After phase 1, the caveat rule of each pack involved (whose leaves the expansion holds, or that the dataset lists) runs, in pack id order, on a deep copy of the canonical cohort, its own, with a view of the release of its own, without its label, whose descriptors are copied as it reads them (as a compiler's, D285), none being made for a pack without a rule; it returns a list of codes its pack declares, each raised once with its declared severity, affecting `/population` in a cohort count. Anything else, or an exception it raises but a `MemoryError`, refuses the cohort (`PACK_FAILED` at its `all`, with a message of the core's) | The rule is static and its inputs are in the id, so the id still determines the caveats: the label is not in the id, and a rule that could read it would give one id two digests, drafted and published; a code a pack did not declare has no severity to show |
| D288 | A cohort count's digested part (M2) | Before the disclosure pass, a cohort count's population is the evaluation's, `unknown_by_leaf` keyed by leaf key; its size is `n_true` over the unit table's rows (`no_units` when it has none); and its caveats are `UNKNOWN_EXCLUDED` when `n_unknown` is above 0, `SCOPE_PARTIAL` and `COVERAGE_PROPOSED` when some unit's cohort-level truth value carries the flag (naming the relationships), `UNCONFIRMED_SEMANTICS` for the fields read (listing them), `LIFT_DIFFERS` when `lift_differs` is above 0, `DRAFT_RELEASE` over a draft, and the caveat rules' codes, each once. The disclosure pass and the readback then apply to them | These are what §8.1 and §8.3 require of a cohort count, and they must exist before golden count digests can be checked in |
| D289 | The derivation log (M2) | The app DB's `derivations` table holds, by id, the kind (`cohort` or `result`), the object the id hashes in RFC 8785 form and the releases; `derivation_releases` lists a derivation's datasets; `issuances` holds, by `iss:` and a ULID (48 bits of milliseconds and 80 random bits), the derivation, the tool, the document as written, the parameters, the SQL as run (none for a cache hit, which names an issuance of the same derivation whose SQL ran), the engine and pack versions and the time. A derivation is recorded only with the object its id is the hash of, and of the kind its object is (a cohort's names a `cohort`, a result's a `view`), once, with at least one release, each dataset once and each under the dataset the store records its manifest for (erasure finds a derivation by its releases' datasets), and a cohort's exactly the manifests its object's `cohort` is over; a list holding anything but releases is refused whole. A derivation any of whose releases is withdrawn is neither recorded nor issued (`WithdrawnReleaseError`), as the issuing transaction sees the releases, so an output computed before an erasure and issued after it records nothing of the person. Triggers hold these rules against every write, `INSERT OR REPLACE` included: a derivation is inserted once and with its object, is never removed, and changes only once, when erasure takes its object; its `derivation_releases` are the releases it lists, each dataset once, of its manifest's dataset, none withdrawn, and never change or go; an issuance is inserted once, only for a derivation whose object is kept and none of whose releases is withdrawn, and a cache hit only naming an issuance of its derivation whose SQL ran; it changes only by redaction (its document, parameters and SQL, each to JSON that holds `[erased]`; the table's check keeps the SQL from going to or from none), and is removed only once its derivation is erased, or, for `count_cohort`'s, by pruning, never one a kept issuance names. Taking an object, changing an issuance and removing one each need a permit in `log_permits`: redaction writes `redaction`'s, and pruning `pruning`'s with the time it prunes before, first within its transaction and removes it last, so no permit is ever committed (redaction refuses to run outside a transaction, where its permit would be); a permit is one of each kind and never changes. The triggers guard the log against the store's own code and its mistakes, not against a writer with SQL access to the app DB, who could write a permit or drop a trigger; that limit is accepted, the app DB being the server's own file. `count_cohort`'s issuances are pruned by time, but those a kept issuance names; `prune` takes an RFC 3339 time with its offset (`T` or `t`, `Z`, `z` or `±HH:MM`, a four-digit year, a fraction to the microsecond), refusing anything else (ISO 8601's basic and week forms, an offset of hours alone, an instant beyond year 9999 in UTC) with a `ValueError`, and compares the instant, written as the store's clock writes times, the year always in four digits. Issuance ids made by one process only ever increase, by the ULID specification's monotonic generation, and a derivation's issuances are listed by time, then id. An id resolves, in this order, to *erased*, *withdrawn* (a release withdrawn), *discarded* (a release a draft state no longer live), *unknown release* (a release the store has no record of) or *issued*; an unknown derivation id is *not issued*, an unknown issuance id *unknown* | §12.2 names the tables and what they hold; recording only verified objects makes `explain` exact, and the order of the statuses puts first what the id can no longer show; the releases are what erasure finds a derivation by, so they must be its object's, and an issuance after a withdrawal would bring back what an erasure took; permits make a stray `UPDATE` or `DELETE` fail, which checking the shape of a write alone does not; times compared as text in two spellings order wrongly |
| D290 | Erasure of the derivation log (M2) | Erasure takes the object of every derivation over one of the dataset's releases whose object holds a term as data, and deletes its issuances. A cohort's data are the constants of its canonical clause trees: `values`, range bounds, `scope` values and unit keys' parts; a result's are its canonical parameters: a clause among them (a predicate or a covariate, canonicalised as in phase 1, §7.6) by a cohort's rules alone, its unit not known, never again as strings, and any other string as a string constant. Versions, the disclosure setting, descriptor ids, paths, counts such as `min_count` and a result's numbers outside clauses (an analysis's settings) are never matched. Values are read under every datatype they may be of (as text; as a number, written as RFC 8785 writes it; as a date; as an instant, in UTC), by the store's cell reader and the engine's constant reader alike (§6.4), so that every spelling either accepts has its value: a datetime is its instant in every spelling (the canonical form's `Z`, a cell's `+00:00`, a client's `+01:00`, `t` and `z`, a fraction padded with zeros beyond the microsecond), and `2.0` or `2e0` is the number 2. How a constant is matched goes by where it is, its *place*, as the table in §12.2 (Erasure matching) sets out and this decision states: (1) *naming*: on a column whose values name the person's rows (the key and identifier columns of the tables that hold them, but for foreign keys to rows of other tables, and the foreign keys into those tables; a `covered` clause's scope names a column of its `table` by its name alone, so it is that table's column), on a column the core cannot name (one given as a parameter, or a value concept, from M6, whose column each dataset maps, and a scope of a `covered` table given as a parameter or a concept), a part of a unit key of a table that holds their rows (or of a unit not known to be a table, as a concept unit from M6), and a pack leaf as written, only in the tree over the dataset's release (or in a cohort or a view as written that may be over the dataset): a constant is a term when they share a reading, JSON numbers and booleans too, and a string also when it holds a term as a token, but only a term of the tables whose rows the place names: each term is kept with the tables whose rows of the person's gave it, and a key or identifier column names its own table's rows, a foreign key the rows of the table it points into, a unit key its unit table's, and a column or unit the core cannot name, a pack leaf as written and anything with `redact_only` every table's; a term of another table, text or number, is not matched there at all (the member numbered 3 is not the person's loan 3, nor the loan numbered 17 the member 17); (2) *unknown*: free text (`notes`, `note`, `drafted_by`), structure, a parameter no clause refers to, a result's strings: by every reading and as tokens, but never a JSON number or boolean, nor a term below the person that is a number by its column's datatype (`"2"` names no loan there, `"104233"` is a member whatever it reads as); (3) *other*: on a known column that names none of the person's rows, a part of a unit key of a table that holds none of them, and anything in a tree over another dataset's release or in a cohort as written over another dataset (its `dataset`, every one of its `datasets`, or else the document's, names another; one given as a parameter, `datasets` included, may be over the dataset), or in a view over such cohorts only (those its `cohorts` and `reference` name, or, naming none, every cohort of the document; one it names by a parameter, or that no cohort has, may be over the dataset): a string by its text alone, the whole of it or a whole token of it that is no part of a longer number, date, time or range (a term starting or ending with a digit has no sign before it, no `.`, `,`, `:`, `/` or `-` joining it to another digit on either side, and no time of day, ` HH:MM`, after it), so `07`, `7.0`, `+7`, `6-7` and `7-8` do not hold the text key `7` there, nor `2024-01-01T00:00:00Z` or `2024-01-01 10:00` the date `2024-01-01`, while `2024-01-01` does and so does `for m-17 only`; never a JSON number or boolean, nor a number below the person. A parameter referred to from several places is matched as in each of them, a term any of them holds being erased; an object key anywhere is matched whole by every reading against every term, as the audit trail's values are (D223), two keys redacted alike being kept apart as `[erased] (2)`, `[erased] (3)` and so on; a name a document gives (a cohort's or a parameter's, which starts with a letter or `_`, so only a text term can be one) is such a key, and every reference to it (a view's `cohorts` and `reference`, a `cohort` leaf, `"$name"`) is redacted as the name is, so each still names what it named: §7.6 keeps names out of ids, but a name, like a note, may quote the person. A token, in free text and in a string matched by every reading, is: the term as written, next to no letter, digit or mark of any script; a date and time (`T`, `t` or a space between them; to the minute or the second; a fraction after `.` or `,` of any length, truncated to the microsecond as a cell's is; `Z`, `z`, `UTC`, `GMT`, `±HH:MM`, `±HHMM`, `±HH` or none, after a space or not) whose instant, read at its offset or, the offset aside, as UTC, is a term's; and a number (a sign, digits, a fraction, an exponent: `0017`, `+17`, `17.0`, `1.7e1`) that no `.`, sign or `-` joins to more text, nor a `,`, `:` or `/` to another digit, whose value is one of the person's terms' (so `017` in `member 017: Grace` is one, and neither `17` in `10:17` nor in `3/17`); digits grouped by separators (`1,017`) are read as no number, neither the whole nor a group, and a number below the person by its datatype is never a token. An erasure with `redact_only`, which knows no release, puts every constant on a naming place. Every other issuance that names the dataset, through its derivation or in its document as written (its `dataset`, a cohort's `dataset` or `datasets`) or its parameters (those given with the issuance or the document's own `params`; one, or one in a list), has its document, parameters and SQL redacted by the same rules: each constant in its place, everything else (`notes`, structure, the SQL's text and strings) as *unknown*; a number only as a naming constant, a parameter as the places that refer to it (`"$name"`) are, a clause among them as a clause, a parameter that stands for an `ids` list or a member of it as inline unit keys are (`"<dataset>:<key>"` split at the first `:`), and a pack leaf as written, whose columns the core cannot know, losing every constant that is a term as a naming place's, JSON numbers and booleans too (in a cohort over another dataset, as an other place's); never `aibi`, a count, a quantifier, a bound on another column or an analysis's setting; the SQL's numbers, which are its derivation's constants (which held no term) or structure, are kept. The log is read a batch at a time: the dataset's derivations in id order, and the issuances, those of its derivations and those whose text holds its id chosen in SQL. Terms are found in text by its tokens (runs of letters, digits and marks, and each other character) looked up in sets, so that no pattern of them is compiled and their number costs a lookup, not a step of every match. Erasure is one pass over what the log holds when it runs: an issuance canonicalised before it and recorded after it is checked only as every issuance is (a derivation over a withdrawn release is refused, D289), so the query tools must record an issuance only if no erasure of a dataset it names was recorded since they canonicalised it; checking later issuances against the terms would mean keeping the person's data | §12.2: a partial redaction next to a hash can be reversed by enumerating keys, so the whole object goes, and it cannot be restored, so it must go only when it holds the person: a loan numbered 1 below them names no one as `semantics_version` 1, `min_count` 2 or an age of 3 does, while a string is never a count, an age or a version, so a key that reads as a number stays a key; an issuance of a kept derivation is still a document, whose structure names no one, and redaction is as irreversible there; issuances of other derivations may still quote the person in notes or parameters; a value is identifying whatever its spelling, and each spelling matched as text left one behind (a text key that read as a number, an instant the canonical form writes with `Z` and a cell with `+00:00`, a key split inline but not through a parameter), so values are compared, not their text; but a value's readings erase too much where it names no one, since a date or a number is a period's or a range's bound as often as a person's value, and an erased object cannot be restored, so on a column known to name none of the person's rows only a quote of a term's text counts, and every reading only where the value may name them (review round 4 found both directions); and a value on a naming place is a key of the rows of its tables, so a term of another table there names another row, while every value that names a row of the person's there is a term of that row's table, which gave it (review round 6) |
| D291 | The SQL compiler's relations (M2) | Each node of a canonical cohort is compiled once per table it is evaluated on (a node and its table key a relation, by the node's canonical serialisation) into a relation with one row per row of that table: its number in the table's blob (DuckDB's `file_row_number`, the order the reference evaluator numbers rows in), its truth value coded 0 (FALSE), 1 (UNKNOWN) and 2 (TRUE), so that `all` is the least, `any` the greatest and `not` 2 − *v*, its reasons as bits in the order of §6.3's table, and its flags as bits, one per flag and relationship the cohort's coverage can raise (`COVERAGE_PROPOSED` for a proposed coverage, `SCOPE_PARTIAL` for one with scope columns), sorted, 63 to a signed 64-bit word. A combinator stacks its operands and groups them by row. A lookup is a relation from each row to the first row its keys reach, or none (`NO_PARENT`). An existence question counts its children per parent row by value and by whether they are kept, and ORs their reasons, flags and conditions; under `exclude_self` it counts each parent row's children once, by value and bit by bit for what it ORs, and takes away the part of the row it starts from when that row is one of them, so the work is linear in the rows and never a join of each row with its siblings; a coverage's listing is aggregated per parent row (whether it lists the row, some whole scope tuple, every tuple through a group, and the distinct tuples of the scope columns `W_C` admits, within the values it admits); §6.5's steps are then comparisons of those aggregates. `lift_differs` compiles the cohort with every lift flipped | One relation per node and table keeps the SQL linear in the canonical form's size, and reuses what the flipped form shares with it; bits carry reasons and flags through every aggregate exactly; stacking operands avoids joins of up to 256 relations, which DuckDB plans slowly; joining each row with its siblings is quadratic in a parent's children, and 8,000 of them ran out of memory |
| D292 | Identifiers, constants and exact comparisons (M2) | Table and column names come from descriptors and appear only in the select lists that read table blobs, each renamed there to a name of the compiler's own (`c0`, …); every other name is the compiler's (`b0` and `q0` for relations; `rid`, `v`, `r`, `m0` and the like for their columns), so no descriptor name can collide with one. Every value from a document or a descriptor (constants, bounds, permissible values, a record filter's allowed values, scope values, unit keys, `min_count`, blob paths) is a bound named parameter; literals are the compiler's codes (truth values, bits, the names of cell states). Membership in a list of constants is a semi-join on the bound list (`IN (SELECT unnest(…))`), which DuckDB hashes, not a search of the list for each row. Constants are bound in their column's stored type, as the evaluator compares them: datetimes as microseconds since the epoch against `epoch_us` of the column; a double bound on an integer column (after a conversion of units) as the integer bound the comparison implies, or TRUE or FALSE when none does; a double member of an integer column only when whole; an integer member of a double column only when the double holds it exactly. Keys of two columns stored in the same type compare by equality; an integer and a double by value, exactly; any other pair never matches. A table whose blob stores a column named `file_row_number` cannot be queried (refused as a fault, `CompileError`). An issuance records the SQL as run and its parameters, blob paths as the blobs' digests | §12.2 and §14: no SQL from clients, identifiers from descriptors; renaming at the blob keeps descriptor names out of every other clause; the evaluator compares Python values exactly (`key_part`), and DuckDB's implicit casts do not; DuckDB names a Parquet file's row numbers `file_row_number` and shadows them with a column of that name; paths name where the server keeps its data, digests what was read; a search of a list per row made a million rows against 10,000 values take 15 s, the semi-join 1 s |
| D293 | Query sessions and workers (M2) | A document's queries run in one fresh worker process (not a fork; `-P`, its standard streams on `/dev/null`, an environment of `PATH`, `PYTHONPATH` and a UTF-8 locale), which loads DuckDB and nothing of aibi's; the server's process never loads DuckDB. The child asks the kernel's OOM killer to pick it first (`oom_score_adj` 1000), limits its CPU time to `query_seconds` × `query_threads` + 5 s (`SIGXCPU`, and `SIGKILL` 2 s later) and its address space to 4 × `query_memory` + 1 GiB (a backstop far above what it uses), writes no core file (it would hold the rows read), asks to die with the server, and runs the queries in an in-memory database whose settings are locked before any query: extensions neither installed nor loaded on demand, the document's table blobs the only files it may open (DuckDB's `allowed_paths`, which would let it write them too, so each statement must be one `SELECT` as DuckDB's parser reads it, and none writes a file), external access otherwise disabled, no temporary directory (no spilling), time zone UTC, `query_threads` threads (1 by default), and a memory limit of half of `query_memory` (2 GiB by default, at least 512 MiB). The blobs are verified before the queries read them, as a loaded release's are (D221), once per file while it stays the same file (device, inode, size, modification time and change time, which a write sets even when the modification time is set back): a damaged blob is `CorruptBlobError`, a missing one `MissingBlobError`. The server watches the child's resident memory in `/proc` every 50 ms while it waits and kills it once it holds more than `query_memory`, which a child can overshoot by what it touches between two looks; without `/proc` there is no watch and no worker runs (the workers refuse to start, and a run whose child's memory cannot be read is stopped as a fault). At most `query_workers` (2) run at once; a query waits for its place at most `query_seconds` (25), and has as long from then, its start included; a caller's own deadline, a tool call's, ends the wait and the run sooner, so no run outlives its call, and its child is killed; that end is the caller's to answer by its own limit (`CallerDeadline`), as no limit of the worker's was hit, and a deadline that has passed before a child starts starts none. The answer is one frame of at most 64 MiB (`query_answer_bytes`) holding the rows' integers packed column by column in the narrowest type that holds each column, read and parsed under the deadline into a buffer of its size and read in place; each query states how many columns its rows have (at least one: the counts query 3 + one per reason + one per top-level clause + 1 + its flag words, the values query 3 + its flag words), and a result of another number of columns, or of more or fewer results than queries, is a fault, so what the server makes of a frame is bounded by what it asked, not by what the child claims; a cohort's truth values are made from the packed columns as each is read, after the columns are checked, so rows of an answer make no object each. Rows larger than 64 MiB are refused naming `query_answer_bytes`, and a frame of another shape or size, or rows the queries cannot give (a count below 0, a code or reasons no truth value has, rows out of order, a flag bit that stands for no flag), is a fault (`QueryError`). A child that closes its socket is waited for, until the deadline, and judged by how it ended. Past the deadline, or on `SIGXCPU`, the process group is killed and the query refused (`LIMIT_EXCEEDED`, `query_seconds`); a process the child started in a session of its own would leave the group and not be killed, and none is started, as the child's session loads no extension and runs one `SELECT` per statement (the worker is not a privilege boundary, so the server does not make itself a subreaper for such a process); DuckDB's out-of-memory error, a `MemoryError`, the watch, `SIGKILL` or `SIGABRT` is `query_memory`; no place in time is `query_workers`; anything else is a fault, raised naming the error's class alone, never DuckDB's message. The limits are `[queries]` in the server's configuration | §14 requires killable workers with a wall-clock and a DuckDB memory limit; a run bounded by its tool call's deadline cannot keep a place or a child after the call has given up; DuckDB's allocator reserves several times the memory it uses as address space, so a limit on address space near `query_memory` makes allocations fail outside DuckDB's accounting and can crash the child rather than refuse the query, while DuckDB's own limit refuses cleanly, the watch bounds what it does not count (lists it aggregates, the answer), and the backstop and the OOM killer's choice keep a runaway child from taking the server with it; the kernel closes a dying child's socket before its exit status can be read, so a status read at once misjudged a child killed for memory as a fault; soft and hard CPU limits must differ for `SIGXCPU` to be sent; packed integers, read against the column counts the server expects and turned into truth values only as each is read, keep the server from making an object per row, or per column a child claims, of an answer it has not bounded, and an unpickler is not needed; a caller's deadline is not a limit of the worker's, so a refusal naming `query_seconds` or `query_workers` for it would name a limit that was not hit; DuckDB's messages can hold paths, SQL and values, which are erasable data (D290); a blob that is damaged must not be counted as if it were whole |
| D294 | Integer aggregates only (M2; refines §9.3) | The SQL computes counts with `COUNT` and flags, reasons and conditions with `BIT_OR` and `BOOL_OR` over integers and booleans, and `MIN` and `MAX` only over truth codes and row numbers; no aggregate over a double enters a digest; a cohort's per-unit rows are returned by row number | §9.3 allowed `SUM`, `COUNT`, `MIN`, `MAX` and `AVG`; bitwise and logical ORs of integers are exact and order-free, so they keep the determinism §9.3 asks for |
| D295 | Differential tests (M2) | The compiler is tested against the reference evaluator on random city releases (every coverage form, proposed coverage, parent scopes TRUE, FALSE and UNKNOWN, missing codes, list items, null and dangling keys, numbers in other units, dates, datetimes and booleans) and random documents of one to three top-level clauses, requiring identical truth values, reasons, flags with their relationships, and accounting; again with one flag to a word; and requiring identical count digests. It is tested the same way on random schemas: two to four tables in a random tree, keys stored as strings, integers or doubles (an integer key referenced from a double column and the other way round), value columns of every stored type holding their extremes, random missing codes, null and dangling keys, empty tables, every coverage form with proposed statuses, parent scopes and record filters, a second relationship between a table and its parent, and documents on any of the tables (lookups, nested questions with `min_count`, `every` and both lifts, `covered` with scopes, siblings under `exclude_self`, up one relationship and down the other, ids and every combinator), and again on schemas whose every child table has two relationships to its parent naming its parent's rows, with a sibling question up one and down the other. Every document the engine tests evaluate outside a property test is compiled and must agree as well. The tests run the queries in DuckDB sessions of one long-lived helper process, never in the test's process (as DuckDB never runs in the server's); the worker has tests of its own | §13.3's acceptance; the evaluator's scenario tests encode §6's consequences, so running each through SQL checks the compiler against every one of them; one fixed schema leaves combinations of key types, coverage forms and table shapes untried |

---

## Appendix B. Change log

- **v0.8.9** — Choices the three-valued SQL compiler settles (D291–D295): one relation per node and
  table holding each row's truth value, reasons and flags as integers and bits, combinators as
  stacked aggregates, existence questions and coverage listings aggregated per parent row;
  descriptor names only where blobs are read, every value bound, and comparisons exact as the
  evaluator makes them; DuckDB sessions locked and confined to the blobs, in a fresh worker
  process per document with limits on its time, memory and number (`[queries]`), and the server
  never loading DuckDB; integer aggregates only (refines §9.3 with `BIT_OR` and `BOOL_OR`); and the
  differential tests. §9.3 names the new aggregates, §12.2 how the query engine reads blobs and
  what an issuance records, §13.3 that every evaluated document is compiled too, and §14 the
  session's settings and the three new limits (`query_seconds`, `query_memory`,
  `query_workers`). Review round 1 of #35 amended D291, D292, D293 and D295, §12.2 and §14 within
  v0.8.9, adding no numbers: `exclude_self` counts each parent's children once and takes away a
  row's own part, and membership is a semi-join on the bound list; a child that closes its socket
  is waited for and judged by how it ended, its CPU limit sends `SIGXCPU` before `SIGKILL`, it
  asks the OOM killer to pick it first and has an address-space backstop, and no worker runs
  without `/proc` to watch it; its session may open the document's blobs alone and runs one
  `SELECT` per statement; a run ends by its caller's deadline; answers are packed integers of at
  most 64 MiB (a fourth limit, `query_answer_bytes`); faults name the error's class, never
  DuckDB's message; the SQL path verifies blobs as loading does; and the differential tests also
  run on random schemas. Review round 2 of #35 signed it off, amending D293 and D295 within
  v0.8.9: each query states its rows' number of columns, which its answer must hold, the answer is
  parsed under the deadline, and truth values are made as they are read; rows a query cannot give
  are a fault; a caller's deadline ends a run as `CallerDeadline`, and one already past starts no
  child; the blob cache knows a file by its change time too; a process that leaves the child's
  process group is not followed; and the random schemas have second relationships for sibling
  paths.
- **v0.8.8** — Choices the canonical form, ids, digests and the derivation log settle (D281–D290):
  sorting by serialisation as UTF-16 code units, with resolution's removal of duplicates making
  one sort the fixpoint, and the canonical form read back as a document; the objects cohort ids,
  computation ids and leaf keys hash, and the semantics version, 1; digests and rounding; views
  reported unchecked until M3, and a view's ids from their parts, ordered without the disclosure
  setting; pack leaves (their schemas, the shared budget of steps, compiling once per release,
  expansions checked and bounded, refusals placed at the leaf, summaries); pack versions; caveat
  rules; the digested part of a cohort count; the derivation log's tables, what an id resolves
  to, pruning; and erasure of the log. §7.3 says how a pack leaf is checked and expanded, §7.6
  gives the semantics version, the sort, step 1's packs, reading the form back, caveat rules and
  views until M3, §9.3 the rounding, §10.1 the leaf kind's and caveat rule's contracts, §12.2
  pruning and what ids resolve to, and §14 the three new limits (`pack_leaves`,
  `expansion_values`, `pack_leaf_steps`). Review round 1 of #34 amended D281, D283, D285, D287,
  D289 and D290, §7.3, §7.6, §9.3, §10.1 and §12.2 within v0.8.8, adding no numbers: erasure
  matches only a derivation's data, and numbers only where they name the person's rows; a pack's
  exceptions are `PACK_FAILED`, its code gets copies and no release label; the read-back escapes
  `$`; the log's triggers hold against every write; a new *unknown release* status; monotonic
  issuance ids; an overflowing rounding is refused; slow packs are unbounded until the query
  tools. Review round 2 amended D285, D287, D289 and D290 and §12.2 within v0.8.8, adding no
  numbers: erasure decides a constant by its JSON type and its term's datatype, so a text key
  that reads as a number is matched on every column; the issuances it redacts keep what names
  no one; a result's clause parameters are matched as clauses, and concept units' keys as a
  holding table's; a cohort's releases are its object's, and a derivation over a withdrawn
  release takes no issuance; the log's triggers ask for permits and refuse issuances of erased
  derivations; pruning compares instants; packs see copies of descriptors, and a `MemoryError`
  is not a pack's failure. Review round 3 amended D285, D287, D289 and D290 and §12.2 within
  v0.8.8, adding no numbers: erasure matches terms and constants by value under every datatype
  they may be of, so a datetime is erased in every spelling of its instant and a number in every
  spelling of its value, and unit keys given through a parameter as inline ones are; a column
  given as a parameter or a value concept names rows; a derivation's release is listed under
  its manifest's dataset; `prune` takes RFC 3339 only and writes four-digit years; each pack
  leaf's compile and each caveat rule gets a view of its own that copies a descriptor when it
  is read, and a pack without a rule none; redaction runs only within a transaction and reads
  the log a batch at a time. Review round 4 amended D223, D285 and D290 and §12.2 within
  v0.8.8, adding no numbers: erasure matches a value as its place says (a table in §12.2): by
  every reading, read by the engine's constant reader too, where it may name the person, and
  by its text alone, never as a number, on a column known to name none of their rows; free text
  reads more spellings of instants and the person's numbers; each item of a list in an
  identifier column is a term; the M1 redactors' matching by value is D223's; a pack's view
  holds the release's descriptors only in closures, and a redaction leaves no compiled pattern
  of its terms cached. Review round 5 amended D290 and §12.2 within v0.8.8, adding no numbers:
  a coverage scope's column is its table's, and a scope of a table given as a parameter or a
  concept names rows; a cohort as written over another dataset is matched as *other*; a
  result's clause parameters are matched as clauses alone; a `:` or `/` joins a number only to
  a digit, grouped digits are no number, and by text alone a date followed by a time, or a term
  in a range, is no token; a pack leaf's booleans are matched as a naming place's; a name and
  every reference to it are redacted alike; derivations are read a batch at a time. Review round
  6 amended D290 and §12.2 within v0.8.8, adding no numbers: a naming place holds only the terms
  of the tables whose rows it names, text terms too; a view's constants are over the cohorts it
  names; a document's own parameters, and a cohort's `datasets` given as a parameter, may name
  the dataset; a parameter referred to from several places is matched as in each; terms are
  found in text by tokens looked up in sets; and erasure is one pass over the log as it stands.
- **v0.8.7** — Choices the catalogue, its statistics and the tools over MCP settle (D270–D280):
  catalogue statistics counted at every build; their disclosure (refines §8.4: a distribution is
  suppressed with its PRESENT count, and a list column's pooled row has no count); statistic
  references' pointers and floors (refines D202); the catalogue index in the app DB; searching
  the catalogue; describing a dataset, with its standing caveats, and a column; the curation queue
  and proposals as tools, a proposal naming its agent; the MCP transport, stateless and behind
  request protection, with an upload's deadlines on bodies, the document loader's rules and a wall-clock
  limit on calls; descriptors as resources; and the tools over HTTP. §8.1 gives the pointers of
  `stat:` references, §8.4 the two refinements, §11.1 the `agent` argument, the standing caveats
  and the transports, §12.2 the statistics and the index, §12.4 the SDK's version, and §14 the
  tools' limits. Review round 1 of #33 amended D271 and D273–D280 within v0.8.7, adding no
  numbers: no curation evidence is served under *k*, the queue's notes are disclosed with the
  statistics and in fixed words, and `n_rows` is suppressed only when it is small (D271, D277); a
  draft is disclosed under the published setting too, and release refusals name `/release` and the
  labels (D275, D279); values an output cannot carry are `unrepresentable` (D271, D276); entries
  are written only while their release is latest (D273); completeness compares rationals (D274);
  proposals have a rate per client and agents a share of the open ones (D277); the transport checks
  the JSON-RPC envelope and refuses secrets before the SDK, filters the root and the SDK's loggers,
  holds a call's place until its thread returns (`tool_calls`), gives public bodies a shorter idle
  time (`tool_body_idle_seconds`) and needs the SDK 1.30 (D278, D280); §8.4, §11.1 and §14 say so.
  Review round 2 amended D270, D271, D277, D278 and D280 within v0.8.7, adding no numbers: a
  release without catalogue statistics is refused, not counted (D270); importers write no counts
  into descriptor text, and declared zeros bound pooled values (D271, §8.4, §10.1); agents' share
  is 5,000, a client's 500, and an operator rejects a proposer's open proposals at once, while the
  public queue drops hidden text before its byte budget (D277, §11.1, §11.2); one client holds at
  most 2 of the tools' places, small messages are read off them, and one 30 s covers a message's
  reading and its call (D278, D280, §14).
- **v0.8.6** — Choices the operator router and request protection settle (D253–D269): server
  configuration; binding and TLS; one protection middleware; the Host allow-list; the Origin
  allow-list and fetch metadata; CORS; rate limits; request sizes and bodies; the curator token;
  operator names; CSRF tokens; the operator router; refusals over HTTP; imports and uploads over
  HTTP; session handles over HTTP and in the CLI; the operator CLI; erasure and proposers on the
  operator router (amends §11.2). §11.2 says where the token and the operator's name travel, which
  requests are a browser's, and adds uploads, erasure and the proposers to the operator's
  operations; §14 gives the order of request protection, the loopback interface, forwarded headers
  and the body limits; §12.4 and §12.5 name uvicorn and httpx, and the `api/` and `operator/`
  packages. The first review's fixes amend D254, D255, D259 and D261–D269 and §14 within this
  version, adding no numbers: the token is verified before any failure limit, and requests
  without it take nothing from the operator's rate; preflights count against the `api` rate;
  tokens are found in URLs once percent-decoded; logged paths, and refusals of a request, blank
  token and handle shapes; operator names have one encoding and no bidi formatting; uploads take
  a `concurrent_imports` place; the CLI takes handles from standard input or `AIBI_HANDLE`, and
  erasure keys from standard input or a prompt, never from arguments. The second review's fixes
  amend D253, D254, D262 and D264–D268 and §14 within this version, again adding no numbers: operator
  names of a token's or a handle's shape are refused; every refusal the server answers blanks
  those shapes; the CLI refuses arguments of those shapes and datasets that are not identifiers
  before any request, and quotes no value in a usage error; a stalled upload is refused after
  `upload_idle_seconds`, freeing its place; a dataset with no label is `UNKNOWN_DATASET` on every
  route that names one; the configuration file's owner and directory are checked; the access
  log blanks those shapes in a record of any shape. The third review's fixes amend D253–D255,
  D259, D260, D262, D264–D266 and D268 and §14 within this version, again adding no numbers: each
  connection has a deadline for its request head, idle connections make room, and each client
  holds at most a share of the connections; an upload has a deadline its length sets; the
  configuration's path is resolved once and the file opened without following a link and
  checked on its descriptor; every log, exceptions included, blanks secret shapes; text a
  request stores, and operator names and CLI arguments once percent-decoded, are refused those
  shapes; bodies are parsed off the event loop; `imports.concurrent` is at most 20; the CLI
  reports any HTTP error as unreachable; IPv6 clients are rate-limited by their /64; D260's
  reason is corrected. The fourth review's fixes amend D253–D255, D259, D261, D262, D265, D266 and
  D268 and §14 within this version, again adding no numbers: a client that takes too little of
  the answers waiting for it is cut off (`send_seconds`), and uvicorn is bound to its tested minor
  version; input is refused only for a token's or a handle's shape standing alone, so ordinary
  long names that hold one inside a word are stored and named, while logs and refusals still
  blank the shape anywhere; uploads declare their length (`LENGTH_REQUIRED`), and JSON bodies
  have an upload's deadlines; D254 says what the per-client cap does behind a proxy and what a
  TLS handshake escapes; link-local and NAT64 clients are keyed by their own address. The fifth
  review's fixes amend D254, D259, D261 and D265 and §14 within this version, again adding no
  numbers: what a client took of the answers waiting for it is what its end acknowledged, so a
  client that pipelines and reads steadily but slowly keeps its connection; D254 says when
  uvicorn answers 503 for its tasks; refusals blank a secret's shape once percent-decoded too;
  the configured token is refused at `/operator` and in stored text wherever it sits; clients of
  the local-use NAT64 prefix are keyed by their whole address.
- **v0.8.5** — Choices the release lifecycle, curation sessions and the proposal queue settle
  (D236–D252): operation slots; imports that publish (amends D235); previous names on re-import;
  carry-forward; tombstones; the re-import gate; re-import notes; descriptor versions; sessions
  and handles; edits; the checks on a change; descriptor-write checks; proposals; curation
  proposers (amends §5.1); the curation queue; draft states and *discarded*; the audit trail.
  §5.1 gives a pack's proposer its `by`, §10.1 says when proposers run, §12.3 adds the operation
  slot, the re-import gate and the recorded draft states, and §13.2 says where proposals are
  dropped. Amended before release: D239–D241 (an absent inference consumes a tombstone; a new
  descriptor's failing proposal goes alone; no tombstone of other inferences is kept for a
  descriptor that stays out); D247 (a step budget for extension schemas that does not grow with
  the schema, capped lower for proposals than for operators' and importers' writes and refused as
  `LIMIT_EXCEEDED`, `extension_steps`; errors that keep no copy of the value; linear
  `uniqueItems`, `unevaluatedItems` and `unevaluatedProperties`; checks of only the descriptors a
  change or a proposal touches, and of every descriptor when a session publishes, as D244 now
  says); D248 (what the proposal cap leaves to operators; an accepted proposal is one the draft
  still holds; it amends D119's evidence); D250 (a byte budget, and the release and the draft
  pinned under the store's lock); and §12.3's sentence on an accepted proposal's evidence.
- **v0.8.4** — Choices the file importers settle (D224–D235): detecting parse settings; sources
  and typed values; table names; the statuses an importer sets; column inference; keys,
  relationships and roles; the validation gate; the import report; confinement; archives and
  limits; the upload area; pack imports. Erasure follows the relationships of every published
  release (amends D223), since the gate drops a proposed relationship a re-import breaks. §12.4 and
  §13.1 note that `.xls` is refused in v1 (D225). Workbooks and Parquet files are read in a
  worker process with memory, CPU, time, decoded-text and concurrency limits, whose answers the
  server reads under the deadline, and a typed cell holds a text field's 131,072 characters at
  most (D225, D233, §14).
- **v0.8.3** — Choices the store settles (D213–D223): release manifests; raw snapshots; parsing
  text; typing cells and list cells; table blobs; derived columns; rebuilds; pins and the sweep;
  the app DB; erasure.
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
