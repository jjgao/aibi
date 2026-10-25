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
It needs Python 3.12 or later.

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
`samples` tables), restarting the server first if it is running. `fixtures/onco/demo_study` is a
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
