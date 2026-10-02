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

The server's read-only catalogue page, at `http://127.0.0.1:8000/`, lists the published datasets
and shows each one's descriptors and table graph; it takes no token and shows what the public
tools show.
