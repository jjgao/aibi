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
