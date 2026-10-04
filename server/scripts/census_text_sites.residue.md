# M4.0f-A1: the residue of the census (`server/scripts/census_text_sites.py`)

The census reads every module of `aibi.core` as a syntax tree, finds the **wrappers** (every function
or class whose `str` parameter reaches `text()`, by a fixpoint: `text`/`TextSegment`, `StoreRefused`,
`UrlError`, `refused`, `_refused`, `_refusal`, `refuse`, `refusal`, `fail`, `_limit`, `_invalid`, `_failed`,
`_variable_refused`, `_skipped`, `_because`, `concept`, ...; `--wrappers` lists them), and lists every
argument of one that is not a literal and not a segment, an f-string hole by hole, and every `"text"`
value of a dict written as a segment. Each site is **reviewed** or **residue**:

- it is reviewed when its key (file, enclosing function, wrapper, the argument's source text) has a
  reason in `census_text_sites.reviewed.tsv` (a number, a literal sentence, an enumeration or `Literal`
  member, a constant, an id the server made, a Message of segments already), or when it is a constant,
  an enumeration member, a number or a member of `limits` (`RULES`, by what the hole is, not by what it
  is called);
- the key is the whole of it, so a reverted conversion, a new site, or a variable of a reviewed name
  elsewhere is residue until someone reviews it; a reviewed key that matches no site is a stale
  review, and the script exits 1;
- `tests/core/test_census_text_sites.py` runs the census and holds the table below equal to what it
  generates now, and the reviewed file free of stale keys. To change either, run
  `uv run python scripts/census_text_sites.py` from `server/` and copy the table in.

Run it with `uv run` from `server/`; `--all` lists every site with its line and reason, `--root DIR`
reads another checkout (at `b28474d` it lists the three sites of review round 1, MA-1, and the ones
converted below).

## Residue after this round (the census output, which the test holds current)

<!-- census:begin -->
| file | function | wrapper | hole |
|---|---|---|---|
| core/mcp/server.py | _checked | text | `problem.message` |
| core/schema/loading.py | load_document | text | `problem.message` |
| core/schema/loading.py | _refusal | text | `details['msg']` |
| core/schema/loading.py | _loaded_request | text | `problem.message` |
| core/schema/loading.py | load_descriptor | text | `problem.message` |
| core/store/edits.py | _Draft._validated | text | `error.message` |
Census: 6 sites of 375; 33 wrappers
<!-- census:end -->

All six are library messages that pass as text; A2 converts them.

- `mcp/server.py` `_checked`, `schema/loading.py` `load_document`, `_loaded_request`, `load_descriptor`
  and `store/edits.py` `_Draft._validated`: the message of a `schema.jsonio.JsonError`, which is the
  server's own sentence except where it quotes a decoder (`UnicodeDecodeError`, `json.JSONDecodeError`:
  byte values and positions, no source text).
- `schema/loading.py` `_refusal`: Pydantic's `msg` for a validation error (a validator's `ValueError`
  text is the server's own, and Pydantic's own words are a library's).

## Not text holes, so not in the census, and still A2's

- Several ids in one data token: the composite designators `schema/release.py:217`
  (`f"{table}.{'+'.join(columns)}"` in the roles message) and `importers/databases.py:354` (a
  foreign key's subject).
- `importers/files.py` `_skipped` takes a reason as a `str`: from the archive it is a literal, and a
  sheet's `skipped` is checked in the parent against the two sentences the child writes
  (`sheets.SKIPPED`); `store/sources.py:135` (a numeric offset), the literal-only `str` parameters of
  `gate.fail`, `snapshot._because` and `protection._refused` (reviewed: every argument is a literal).
- Chart title and description (data-marked), the result cache (`analyses/results.py`, a second reader
  of stored server text), the construction-side boundaries (`importers/worker.py` unpickling,
  `operator/client.py`, `engine/resolve.py`, `schema/output.py`), and pack-supplied segments.

## Converted this round (found by the census or the review)

| site | what it quoted | now |
|---|---|---|
| `store/erasure.py:249`, `unheld` (`:600`) | the table id (a source's name) in `INVALID_KEY` | `data(table)`; `unheld` returns a `Message` |
| `store/store.py:467` | the dataset id the release is of | `data(found.dataset)` |
| `store/store.py` `StoreRefused` | `message: str` wrapped as `text` | `*message: Segment | str` (a string part is the server's) |
| `api/errors.py` request validation | the parameter's name and Pydantic's message | `data(where)`, `data(name)`, `data(msg)` |
| `importers/snapshot.py:350/366/850/1750` | a library exception's class name | `data(type(error).__name__)` |
| `importers/worker.py:714` | the child's panic name | `data(panic)` |
| `importers/urls.py` (`UrlError`) and `databases.py:132` | a URL parameter's name | `UrlError` carries a `Message`; `_variable_refused` takes segments |
| `store/writes.py:239` | a pack schema's failure keyword | `data(failure.keyword)` |
