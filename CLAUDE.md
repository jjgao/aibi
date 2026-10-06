# CLAUDE.md

aibi is an AI-native system for exploring cohorts in related tables: spreadsheets, files or
database tables. Biomedical data is the reference use case, but the core is domain-agnostic
and domains are added as packs. **`SPEC.md` is the source of truth.** Read §3 (principles)
before changing anything under `core/`. If a change conflicts with the spec, update the spec in
the same PR or don't make the change.

## Status

Milestones M1, M2 and M3 (SPEC.md §15) are in progress; the roadmap issue lists the work order and
`git log` what has landed. The code is the record of what exists: find a module by its package below,
and read its docstrings and tests rather than a history of the milestones. The reference
evaluator defines the semantics and the SQL compiler agrees with it (§13.3), and every analysis with
a golden test is held to R where a reference fixture exists (`tests/core/analyses/reference/`).

- `core/schema/`: the models of M0 (identifiers, analysis documents, descriptors, results, cohort
  counts, caveats, refusals, the pack API, whose `PackRegistry` keeps core-made copies of what a pack
  gives and hands out `Hook`s, one guard (`guards.py`, `copiers.py`) for every call of a pack's code)
  and `digests.py`, which hashes and digests.
- `core/store/`: blobs, raw snapshots, typed tables, manifests, the app DB, pins, the sweep, erasure,
  the validation gate, the release lifecycle (operation slots, curation sessions with handles, the
  proposal and curation queues), the checks of every descriptor write against the installed packs
  (`writes.py`: extensions, ontology codes, concepts), catalogue statistics (`statistics.py`), the
  derivation log (`derivations.py`, read by `Store.explain`) and the result cache (`cache.py`,
  `Store.results`), and the coverage and scope links erasure follows (`links.py`).
- `core/importers/`: confinement, archives and the upload area, CSV/TSV, workbooks, Parquet and
  database snapshots (read in a worker process that can be killed), `import_dataset` and
  `reimport_dataset`, `checks.py`, which takes a pack importer's result apart before any validator,
  and `reshaped.py`, which records a table a pack's importer unpivoted and has the core rebuild it.
- `core/api/`: the HTTP application: configuration, one request-protection middleware in front of
  every router and mount, refusals as the one error shape, `aibi-server`, `POST /api/tools/<name>`,
  the read-only catalogue pages (`page.py`, `markup.py`, `chrome.py`; HTML without a script),
  `packs.py`, which installs the packs the configuration's `[packs] modules` names, and `bundle.py`,
  which serves the built web bundle; protection records one classification of each request
  (`core/classify.py`, D414) and everything after it reads that record; `openapi.py` (with
  `openapi_normalise.py`, `openapi_vocabulary.py`) generates the OpenAPI document (D416).
- `core/operator/`: the operator router behind the curator token, and `aibi`, the operator CLI,
  which talks to it over HTTP only.
- `core/catalog/`: the catalogue and the service functions of the public tools (`search_catalog`,
  `describe_dataset`, `describe_column`, `curation_queue`, `propose_descriptor`, `validate_document`,
  `count_cohort`, `explain`, `list_analyses` (typed entries), `run_analysis`); `core/mcp/` serves them at
  `/mcp`.
- `core/engine/`: canonicalisation, ids and counts, the reference evaluator, the SQL compiler
  (`sql.py`), the worker that runs a document's queries in a child process (the server's process
  never loads DuckDB), readbacks, and the disclosure pass over cohort counts (`suppression.py`).
- `core/analyses/`: the registry and applicability, phase 2 of canonicalisation (`views.py`),
  result envelopes, charts, disclosure of predicate counts, and the analyses: `compare.existence`,
  `summary.distribution`, `summary.members`, `compare.columns`, `survival.km`, `survival.cox` and a
  pack's analyses (`packs.py`). Their statistical methods (`stats.py`, `timetoevent.py`, `coxfit.py`,
  `coxph.py`, `cone.py`, `cox.py`) are held to R, with `ieee.py` doing their arithmetic as C does.
- `packs/onco/`: the oncology pack (D407), installed by `[packs] modules`: its concepts, the `OncoTree`,
  `HGNC`, `NCBIGene` and `SO` validators, its dataset, table and column extensions and its facet. It
  imports only the pack API (`aibi.core.schema.pack_api`).
- `tests/core/determinism/`: thread-count determinism tests (§9.3, D372) over an orchard of a
  million trees; they carry the `million` marker, which `addopts` deselects, and CI runs them in a
  job of their own.

Disclosure under *k* (§8.4) is per analysis and per form: `registry.CoreAnalysis` states the class and
`withheld_forms` the forms withheld under any *k*. Row counts (`count: "rows"`) are withheld under
any *k* (D379: `registry.withheld_form`, `views._withheld_form`). Category memberships (`each:
"category"`) are disclosed under *k* only on a list or one open down step from the unit to another
table (D383, D384: `disclosure.membership_shown`, `resolve.open_path`, `resolve.open_step`,
`resolve.GATE_CHECKS`), and are otherwise withheld (`distribution.withheld_under_k`). Read those
before touching either.

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
- **Every analysis has a disclosure class** (§8.4, D353): `disclosed`, citing the §8.4 rule that
  protects what it shows, or `refused` under any *k*. A core analysis states it in
  `registry.CoreAnalysis`; a pack's is always `refused`. An analysis leaves `refused` only with a
  §8.4 rule and a brute force in the same PR.
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
uv run pytest tests/core/determinism -m million  # a million rows: about 5 minutes and 4 GB
```

`addopts` deselects the `million` tests, so without `-m million` that directory selects nothing
(pytest exits 5).

Keep command output out of the context. Run each gate above quietly and read only the end, keeping
pytest's exit status (a pipe to `tail` would hide it):
`set -o pipefail; uv run pytest -q --tb=short tests/core 2>&1 | tail -n 30`, and
`uv run ruff check --quiet .`. The full `uv run pytest` takes long enough to run in the
background, to a file in the session's scratchpad directory (`… > <scratchpad>/pytest.log 2>&1`),
whose tail you then read;
`tail` writes nothing until the command ends, so a pipe into it loses everything if the command
is killed. When a run fails, rerun just the failing test (`uv run pytest path::test -x --tb=short`,
with `-m million` for a determinism test, which `addopts` otherwise deselects even by node id)
rather than the suite, and never `cat` a log or a large file: grep it or read the lines you need.
Run a noisy job (the `million` tests, a survey) in a subagent, or in the background to a file.

Run a server and operate it (see `server/aibi.example.toml`):

```bash
uv run aibi-server new-token                        # a curator token, and the hash to configure
uv run aibi-server serve --config aibi.toml
AIBI_TOKEN=… AIBI_OPERATOR="Your Name" uv run aibi status
```

After changing a model in `core/schema/` or a route, regenerate the checked-in JSON Schemas and
the OpenAPI document; a test fails while either is stale:

```bash
uv run python -m aibi.core.schema.export ../schemas   # schemas/*.schema.json
uv run python -m aibi.core.api.openapi ../schemas     # schemas/openapi.json (generated, never served)
```

Frontend (`web/`): React + TypeScript + Vite; API types are generated from the server's
OpenAPI document (`schemas/openapi.json`), not written by hand, but for the one exception SPEC
§12.4 records: three JSON-value types and `ServerNumber`, which the generated types reach through
the document's `x-aibi-json` and `x-aibi-server-number` marks (written in M5.1c-1, D416).

## Working on issues

- Plan before coding: read the spec sections the issue cites, write the plan (modules, tests,
  open decisions) into the PR description, then implement.
- After a PR is written, review it and fix what the review finds, for at least two rounds,
  before asking for review.
- Subagents are defined in `.claude/agents/`, each with its model and tools: `cold-reviewer`
  (Opus) for every cold review of a plan or code, carrying issue #2's rules (a cold plan review
  before code; probes and a mutation pass in every round for security, disclosure, erasure or
  untrusted input; the round cap); `fixer` (Sonnet) for applying settled decisions, restacks and
  gates; `surveyor` (Sonnet) for surveys. Don't override their `model` per call. Their tools
  include no GitHub write tool, but they keep Bash and Write, so "never push, post or merge"
  (and, for the reviewer and the surveyor, "edit nothing tracked") holds by instruction. The
  main thread therefore records `git ls-remote origin refs/heads/<branch>` before delegating,
  checks it is unchanged and reviews every result before anything is pushed.
- Session hygiene: one issue per session. In a delegation prompt, name the files and the SPEC
  sections the subagent should read, so they are read once; don't paste a file or a log into the
  conversation twice. For the operator who starts the session (the agent can't do these): pick the
  model and effort before the first turn, since changing either mid-session breaks the prompt
  cache; check `/context` in a fresh session and turn off connectors the work doesn't use; before
  a long break `/compact` while the cache is warm; between issues `/rename`, then `/clear`.
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
