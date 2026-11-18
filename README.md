# aibi

AI-native cohort exploration over related tables: spreadsheets, files or database tables.
The core is domain-agnostic; domains such as oncology (including cBioPortal studies) are added
as packs.

Researchers and agents define cohorts with a declarative JSON document and run registered
analyses over versioned dataset releases. Every number comes back with how it was derived, over
which entities, from which data release, and what the data could not account for.

Status: design and early implementation. [SPEC.md](SPEC.md) is the source of truth; milestones
are in its §15.

## Development

The server is a Python package in `server/`, managed with [uv](https://docs.astral.sh/uv/).
It needs Python 3.12.6 or later (CPython 3.12.3 to 3.12.5 has a free-list bug in `_asyncio` that the pack's retention scan trips, and `uv` refuses an earlier interpreter).

```bash
cd server
uv python install                         # the version pinned in server/.python-version
uv sync                                   # create .venv with the dev tools
uv run ruff check . && uv run ruff format --check .
uv run pyright                            # strict on aibi.core
uv run lint-imports                       # the core must not import packs (SPEC P8)
uv run pytest tests/core                  # the core suite, which must load no pack
uv run pytest                             # everything
```

CI runs the same checks on every pull request. See [CLAUDE.md](CLAUDE.md) for the conventions
that agents and people follow when changing the code.

## Running the server

The server reads one configuration file at start; `server/aibi.example.toml` is a commented
example. It binds to `127.0.0.1:8000` by default, and needs TLS on any other address.

```bash
cd server
cp aibi.example.toml aibi.toml && chmod 600 aibi.toml   # group and others must not write it
mkdir -p imports                                       # the directories imports may read
uv run aibi-server new-token        # prints a curator token once, and the token_hash to paste
uv run aibi-server check --config aibi.toml            # what the file resolves to
uv run aibi-server serve --config aibi.toml
```

Operators work through `aibi`, which talks to the server over HTTP. It reads the curator token
from `AIBI_TOKEN` (or asks for it on a terminal) and never takes it as an argument; the name
recorded in the audit trail comes from `--operator` or `AIBI_OPERATOR`.

```bash
export AIBI_TOKEN=…  AIBI_OPERATOR="Ada Lovelace"
uv run aibi import library /absolute/path/in/imports/library
uv run aibi queue library
uv run aibi session open library
uv run aibi session confirm library members /fields/primary_key
uv run aibi session publish library
uv run aibi status
```

## A test site

`scripts/testsite.sh` builds the web app, configures a server on loopback, starts it, and imports
the sample datasets (`fixtures/orchard` and `fixtures/hostile_values`), so the catalogue, the
cohort builder (Validate, Count, Run) and the result page can be tried in a browser. It needs
`uv` and the Node version in `web/.nvmrc`.

```bash
scripts/testsite.sh start     # builds, serves and imports; prints the address (default http://127.0.0.1:8000/)
scripts/testsite.sh status
scripts/testsite.sh stop
scripts/testsite.sh reset     # stops, and deletes the site's data (the token is kept)
scripts/testsite.sh import-study demo_onco fixtures/onco/demo_study   # a cBioPortal study, by the oncology pack
```

The site's server installs the oncology pack, and `import-study <name> <dir>` imports a cBioPortal
study directory with it (copied whole into the site; the clinical files become the `patients` and
`samples` tables, a `MUTATION_EXTENDED` profile's MAF the `mutations` table, the case lists the
`case_lists` and `case_list_members` tables, SPEC D444, a `STRUCTURAL_VARIANT` profile's file the
`structural_variants` table, one row per event as the file holds it, SPEC D445, and a discrete
`COPY_NUMBER_ALTERATION` matrix the `cna_calls` table, one row per call other than `0` and `NA`, with
its coverage by the samples' `NA` patterns, `cna_assignment` and `cna_groups`, which needs the
`<study>_cna` case list, SPEC D446, and a `GENE_PANEL_MATRIX` file the `gene_panel_matrix` table, one
row per sample and a column per profile's panel, `NA` unknown, with no coverage yet, so that it
answers whether a sample was given a panel, TRUE or UNKNOWN, never FALSE, SPEC D447), restarting the
server first if it is running.
An import's peak memory grows with its cells (rows times columns, summed over every table it imports),
about 80 to 200 bytes a cell above the server's baseline (about 193 for a case list's members, D444), and
`import_cells` (default 20M) refuses more: a synthetic MAF of 129 columns and 54,000 rows (7.0M cells)
published at a peak of about 1.1 GiB, and one of 160,000 rows (20.6M cells) is refused at the
default. The real `msk_chord_2024` MAF's size is unmeasured here; if its rows times columns are over
about 18.5M (the public database holds about 208,000 of its mutation rows, which at 129 columns
would be 26.8M cells), raise `import_cells` in `[limits]` and give the server the RAM D444 states. A discrete CNA matrix
counts its cells as the core does (3 a call, 1 a `0` or `NA`, 2 a coverage row) though it keeps only
one byte of code a cell and its calls' rows: the real `msk_chord_2024` matrix (702 genes by 25,034
samples) counts 17,753,698 cells, the study without its MAF and SV file 19,253,766, which the default
admits (published in about 35 s at a peak of about 320 MiB, SPEC D446); with its MAF the study is far
over the default, and the matrix adds its cells to the MAF's, so raise `import_cells` for the sum. The gene panel
matrix adds its columns times its rows (the real one 4 by 25,040, 100,160 cells, SPEC D447), counted
before the MAF's. A discrete
CNA matrix's memory at the default limit is a measured range, not a bound: about 3.0-3.5 GiB,
depending on its cells, its number of `NA` groups and the run (SPEC D446: an all-calls matrix of
19.6M cells peaked at 3.3 GiB, ones with a group per sample at 3.0-3.5 GiB); give a server that imports one 6 GiB, and more in
proportion if you raise `import_cells`. The
defaults stay as they are. `fixtures/onco/demo_study` is a
synthetic study; a real one (such as `msk_chord_2024`, whose licence keeps it out of the repository)
is imported from wherever it was downloaded. The pack proposes the study's survival endpoints
(`ep:os` and the like, SPEC D439): run `aibi proposers <dataset>` once for a study imported before
pack 0.3.0, accept them with `aibi session open <dataset>`,
`aibi session accept <dataset> <proposal id>...` (the ids are listed by `aibi queue <dataset>`; read
each proposal's evidence with `aibi --json queue <dataset>` before accepting, since the plain listing
omits it and the evidence says that PFS and DSS are unusable until an event coding is declared, that the
clock and entry are undeclared, and that a proposal may be stale) and
`aibi session publish <dataset>`, and
`survival.km` and `survival.cox` then run through
`run_analysis` (MCP or `POST /api/tools/run_analysis`; a view names its endpoint when two are usable).
Survival does not run in the web app: its builder makes only `summary.distribution`, and survival is
refused under any *k*.

Everything lives in `~/aibi-testsite` (`AIBI_TESTSITE_DIR`; the port is `AIBI_TESTSITE_PORT`). The
curator token is written there once, to `curator-token` (mode 0600), and is never printed. The
server binds 127.0.0.1 only, which needs no TLS: to open it from another machine, tunnel with
`ssh -L 8000:127.0.0.1:8000 <host>`. Do not rebind it without TLS and a proxy (SPEC §14).

The server's read-only catalogue page, at `http://127.0.0.1:8000/`, lists the published datasets
and shows each one's descriptors and table graph; it takes no token and shows what the public
tools show.
