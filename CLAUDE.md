# CLAUDE.md

aibi is an AI-native system for exploring cohorts in related tables: spreadsheets, files or
database tables. Biomedical data is the reference use case, but the core is domain-agnostic
and domains are added as packs. **`SPEC.md` is the source of truth.** Read §3 (principles)
before changing anything under `core/`. If a change conflicts with the spec, update the spec in
the same PR or don't make the change.

## Status

Milestone M0 (SPEC.md §15) is in progress; the roadmap issue lists the work order. The server
package skeleton, tooling and CI exist, and `core/schema/` holds the models of M0: identifiers,
analysis documents, descriptors, results and cohort counts, caveats, refusals and the pack API.
`core/engine/` holds the reference evaluator (M2.1), which resolves documents against releases
held in memory and evaluates them by the rules of §6. `core/store/` holds the store (M1): blobs,
raw snapshots, typed tables, manifests, the app DB, pins, the sweep, erasure and the validation
gate. `core/importers/` holds the file importers (M1): confinement, archives and the upload
area, CSV/TSV, workbooks and Parquet (read in a worker process that can be killed), the
importer's proposals, and `import_dataset`, which builds an unpublished release through the gate
for the core's importer or a pack's. Publishing imports, curation sessions, database snapshots
and tool calls come next (M1, M2).

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
  proposals and withdrawal go through the operator router or CLI with the curator token.
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
uv run lint-imports      # core must not import packs
uv run pytest tests/core # the core suite must load no pack; it fails if one is loaded
uv run pytest
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
