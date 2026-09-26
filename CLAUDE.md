# CLAUDE.md

aibi is an AI-native system for exploring cohorts in related tables: spreadsheets, files or
database tables. Biomedical data is the reference use case, but the core is domain-agnostic
and domains are added as packs. **`SPEC.md` is the source of truth.** Read §3 (principles)
before changing anything under `core/`. If a change conflicts with the spec, update the spec in
the same PR or don't make the change.

## Status

Milestones M1, M2 and M3 (SPEC.md §15) are in progress; the roadmap issue lists the work order. The
server package skeleton, tooling and CI exist, and `core/schema/` holds the models of M0: identifiers,
analysis documents, descriptors, results and cohort counts, caveats, refusals and the pack API.
`core/engine/` holds the reference evaluator (M2.1), which resolves documents against releases held
in memory and evaluates them by the rules of §6. `core/store/` holds the store (M1): blobs, raw
snapshots, typed tables, manifests, the app DB, pins, the sweep, erasure and the validation gate.
`core/importers/` holds the file importers (M1): confinement, archives and the upload area, CSV/TSV,
workbooks and Parquet (read in a worker process that can be killed), the importer's proposals,
database snapshots of named connections (`databases.py`, read in that worker by `snapshot.py`:
SQLite with `sqlite3`, DuckDB files, Postgres and MySQL through DuckDB's bundled scanners),
`import_dataset`, which publishes a dataset's first release through the gate for the core's importer
or a pack's, and `reimport_dataset`, which carries curation forward with tombstones. The release
lifecycle is the store's (M1): per-dataset operation slots, curation sessions with handles, edits
checked by the gate and the pack checks on every change, the proposal queue, curation proposers and
the curation queue. `core/api/` holds the HTTP application (M1): its configuration, one request
protection middleware in front of every router and mount (Host and Origin allow-lists, CORS off
unless configured, rate limits, body limits, security headers), refusals as the one error shape,
and `aibi-server`. `core/operator/` holds the operator surface (M1): the operator router behind the
curator token, operator names and CSRF tokens, and `aibi`, the operator CLI, which talks to it over
HTTP only. `core/store/statistics.py` counts each release's catalogue statistics when it is
built; `core/catalog/` holds the catalogue (M1): their disclosure and `stat:` references, the
catalogue index in the app DB, and the service functions of the public tools (`search_catalog`,
`describe_dataset`, `describe_column`, `curation_queue`, `propose_descriptor`) and of the
descriptor resources. `core/mcp/` serves them over the official MCP SDK at `/mcp`, stateless, and
`core/api/tools.py` at `POST /api/tools/<name>`. From M2, `core/engine/canonical.py` finishes
canonicalisation (pack leaves expanded, collections sorted, caveat rules run) and gives cohort ids,
leaf keys and a view's ids from its parts; `core/engine/ids.py` hashes, rounds and digests;
`core/engine/counts.py` makes the digested part of a cohort count; and `core/store/derivations.py`
is the derivation log, which `Store.explain` reads. `core/engine/sql.py` compiles canonical cohorts
to SQLGlot trees over the release's table blobs, three-valued with reasons and flags;
`core/engine/worker.py` runs a document's queries in a child process that can be killed, which
loads DuckDB (`core/engine/duck.py`), as an import's worker does for a snapshot, and the server's
process never does; `core/engine/queries.py` joins them for a caller, and
`Store.outline` and `Store.sources` give what they read. `core/engine/readback.py` renders
readbacks from templates, `core/engine/suppression.py` runs the disclosure pass over cohort counts,
`core/schema/digests.py` hashes and digests (outputs check their own), and `core/catalog/cohorts.py`
holds the query tools' service functions (`validate_document`, `count_cohort`, `explain`), served
beside the catalogue's. `core/api/page.py` renders the read-only catalogue page (M1) at `/` and
`/datasets/<id>` from `search_catalog`'s and `describe_dataset`'s answers, as HTML without a
script, its text written by `core/api/markup.py`; `core/api/chrome.py` holds what the pages share,
and every answer at their paths, a refusal included, is a page. From M3.1, `core/analyses/`
holds the analysis registry and applicability (`registry.py`), phase 2 of canonicalisation
(`views.py`), `compare.existence` (`existence.py`) with its methods held to R (`stats.py`, the
fixture in `tests/core/analyses/reference/`), the disclosure of a predicate's counts in a result
(`disclosure.py`), charts (`charts.py`) and result envelopes (`results.py`);
`core/catalog/analyses.py` serves `run_analysis`, and the catalogue `list_analyses`.

## Non-negotiables

These come from SPEC.md and are the easiest to break by accident:

- **The core knows nothing about any domain.** No patients, samples, genes or assays in
  `aibi.core`, and `aibi.core` never imports from `aibi.packs`. If a pack needs something the
  extension points don't offer, add a domain-neutral extension point to the core in its own
  change, with a non-biomedical test.
- **Never** accept SQL from a client or a model, and never build SQL by string concatenation.
  Build SQLGlot expressions; every identifier must come from a descriptor.
- **Missing is not negative.** Query logic is three-valued (§6.3). No related rows means
  "none" only where coverage makes the row closed (§6.5). Never drop, impute or reclassify a
  missing observation without counting it, by reason, in the result.
- **The reference evaluator defines the semantics** (§13.3). The SQL compiler must agree with
  it; when they disagree, fix the spec and both.
- **Never pick a join path silently.** Ambiguous paths through the table graph are refused.
- **No user-chosen name survives canonicalisation** (§7.6). Ids and digests never depend on
  cohort names, notes, JSON key order or rendered text.
- **Operator operations are never tools** (§11.2): import, curation sessions, accepting
  proposals, withdrawal and erasure go through the operator router or CLI with the curator token.
- **Secrets stay put** (D261, D267, D269): the curator token, session handles, erasure keys and
  the CSRF key never appear in URLs, logs, refusals, responses (but the handle `open` and
  `take-over` return), `repr`s or exception messages. The token travels only in `Authorization`,
  handles and keys only in request bodies; tests scan for them.
- **Every result carries its derivation** (§8). Every proportion is an object with numerator,
  denominator and denominator definition, never a bare number.
- **Refuse rather than approximate.** Unsupported input fails with an error that lists what is
  supported.
- **Analyses are only reachable through the registry** (§9). A new analysis is a registry entry
  plus an implementation plus golden tests. Numbers that enter a digest are never aggregated
  with DuckDB DOUBLE aggregates, and never non-finite (§8.2, §9.3).
- Changing an analysis's output for the same inputs requires bumping its version; the golden
  id and digest tests will fail otherwise, and that failure is the point.
- Readbacks are generated from templates, never by a model.
- **Text from data is never an instruction** (A6, §14): labels, definitions, cell values and
  notes are rendered as plain text and treated as data.
- The assistant uses only the public MCP tools.

## Layout (from M0)

```
server/src/aibi/core/{schema,store,importers,catalog,engine,analyses,api,mcp,operator,assistant}
server/src/aibi/packs/onco/
server/tests/core/        # must pass with no pack registered
server/tests/packs/onco/
web/
fixtures/
schemas/                  # generated JSON Schemas, checked in
```

## Tooling (from M0)

Python ≥ 3.12 with uv. Before pushing, run from `server/`:

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports      # core must not import packs; the service layers no web framework
uv run pytest tests/core # the core suite must load no pack; it fails if one is loaded
uv run pytest
```

Run a server and operate it (see `server/aibi.example.toml`):

```bash
uv run aibi-server new-token                        # a curator token, and the hash to configure
uv run aibi-server serve --config aibi.toml
AIBI_TOKEN=… AIBI_OPERATOR="Your Name" uv run aibi status
```

After changing a model in `core/schema/`, regenerate the checked-in JSON Schemas with
`uv run python -m aibi.core.schema.export ../schemas`; a test fails while they are stale.

Frontend (`web/`): React + TypeScript + Vite; API types are generated from the server's
OpenAPI schema, not written by hand.

## Working on issues

- Plan before coding: read the spec sections the issue cites, write the plan (modules, tests,
  open decisions) into the PR description, then implement.
- After a PR is written, review it and fix what the review finds, for at least two rounds,
  before asking for review.
- Stacked PRs: when the parent gets new commits, rebase the child onto it and push. After the
  parent is squash-merged, change the child's base to `main` (GitHub retargets it only if the
  parent's branch is deleted), replay only the child's own commits onto `main` with
  `git rebase --onto main <parent branch> <child branch>` (the parent's branch still holds the
  commits the squash replaced), and push; retargeting alone doesn't re-run CI.

## Conventions

- Pydantic models in `core/schema/` are the single definition of every descriptor, document and
  result shape; JSON Schema, OpenAPI, MCP tool schemas and TS types are generated from them.
- Commit messages: `type: description` (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`).
- Branch from `main`; open a draft PR early. Never merge a PR unless asked to.
