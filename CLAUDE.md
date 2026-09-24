# CLAUDE.md

aibi is an AI-native system for exploring cohorts in related tables: spreadsheets, files or
database tables. Biomedical data is the reference use case, but the core is domain-agnostic
and domains are added as packs. **`SPEC.md` is the source of truth.** Read §3 (principles)
before changing anything under `core/`. If a change conflicts with the spec, update the spec in
the same PR or don't make the change.

## Status

Pre-code: the repository holds the spec only. Milestone M0 (SPEC.md §14) creates the skeleton below.

## Non-negotiables

These come from SPEC.md §3 and are the easiest to break by accident:

- **The core knows nothing about any domain.** No patients, samples, genes or assays in
  `aibi.core`, and `aibi.core` never imports from `aibi.packs`. If a pack needs something the
  extension points don't offer, add a domain-neutral extension point to the core in its own
  change, with a non-biomedical test.
- **Never** accept SQL from a client or a model, and never build SQL by string concatenation.
  Build SQLGlot expressions; every identifier must come from a descriptor.
- **Missing is not negative.** Keep query logic three-valued (§6.3). No related rows means
  "none" only where coverage is declared (§6.4). Never drop, impute or reclassify a missing
  observation without counting it in the result.
- **Never pick a join path silently.** Ambiguous paths through the table graph are refused.
- **Every result carries its derivation** (§8). Every proportion is an object with numerator,
  denominator and denominator definition, never a bare number.
- **Refuse rather than approximate.** Unsupported input fails with an error that lists what is
  supported.
- **Analyses are only reachable through the registry** (§9). A new analysis is a registry entry
  plus an implementation plus golden tests.
- Changing an analysis's output for the same inputs requires bumping its version; the golden
  derivation and digest tests will fail otherwise, and that failure is the point.
- Readbacks are generated from templates, never by a model.
- The assistant uses only the public MCP tools.

## Layout (from M0)

```
server/src/aibi/core/{schema,store,importers,catalog,engine,analyses,api,mcp,assistant}
server/src/aibi/packs/onco/
server/tests/core/        # must pass with no packs installed
server/tests/packs/onco/
web/
fixtures/
```

## Tooling (from M0)

Python ≥ 3.12 with uv. Before pushing, run from `server/`:

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports      # core must not import packs
uv run pytest
```

Frontend (`web/`): React + TypeScript + Vite; API types are generated from the server's
OpenAPI schema, not written by hand.

## Conventions

- Pydantic models in `core/schema/` are the single definition of every descriptor, document and
  result shape; JSON Schema, OpenAPI, MCP tool schemas and TS types are generated from them.
- Commit messages: `type: description` (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`).
- Branch from `main`; open a draft PR early. Never merge a PR unless asked to.
