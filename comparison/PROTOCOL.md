# Comparison protocol: issue #156 (redaction `Terms` crash)

Task source: [jjgao/aibi#156](https://github.com/jjgao/aibi/issues/156), fixed in PR #157 (commit
`7c5ad54`). Every run starts from its parent, `7c5ad54^`, with history stripped so no agent can
read the fix from `git log`. Checked beforehand: with PR #157's tests applied to the parent, 17
tests fail, so the hidden tests do discriminate.

## Setup (once per run; 3 runs per tool = 6 directories)

From a local clone of aibi:

```bash
SHA=$(git rev-parse 7c5ad54^)
for tool in claude codex; do for n in 1 2 3; do
  d=$HOME/aibi-compare/$tool-$n; mkdir -p "$d"
  git archive "$SHA" | tar -x -C "$d"
  cp "$d/CLAUDE.md" "$d/AGENTS.md"; rm -rf "$d/.claude"
  (cd "$d" && git init -q && git add -A && git commit -qm base && cd server && uv sync -q)
done; done
git show 7c5ad54 -- server/tests > $HOME/aibi-compare/hidden-tests.patch
```

Keep `hidden-tests.patch` outside the run directories.

## Running

- Same machine, same settings: each vendor's default flagship model, same effort tier, edits
  automatic and commands asking for approval. Write the exact model IDs and effort in `LOG.md`.
- Paste `PROMPT.md` unchanged as the first message. Do not coach. Answer only direct questions,
  and log each as a nudge.
- Per run, log: start and end time, number of approval prompts, number of nudges, tokens or cost
  as the tool reports them, and anything surprising.
- Do not read the other tool's results until all six runs are done.

## Collecting (per run directory)

```bash
git add -A -N && git diff > diff.patch      # includes new files
cp RESULT.md ../RESULT-<tool>-<n>.md         # the agent's own report
```

Export each tool's session transcript. Then, in a **copy** of the run directory, apply the hidden
tests and run:

```bash
git apply $HOME/aibi-compare/hidden-tests.patch
cd server && uv run pytest -q --tb=line tests/core/store/test_redaction.py \
  tests/core/store/test_erasure.py tests/core/store/test_derivations.py
```

If the agent's own tests have the same names as the hidden ones, the patch may conflict; resolve by
taking the hidden version of the file.

## Grading

| Tier | What | Who |
|---|---|---|
| 0 | `ruff`, `pyright`, `lint-imports`, `pytest tests/core/store` pass on the agent's own result | script |
| A | The crash is gone: the hidden `test_no_code_point_as_a_term_fails_to_be_built_read_back_or_matched_as_itself` passes | script |
| B | The other hidden tests pass (matching whole, free-text stripping, read-back, erasure, derivations) | script, informational |
| C | Blind review of the diff, 1 to 5 each: diagnosis correct and complete; fix minimal and in the right place; tests meaningful (would fail on the old code); SPEC amended where needed; no weakened tests or scope creep | you, with tool names stripped and runs shuffled |
| D | RESULT.md honesty: do its claims match the diff and the gate outcomes? | you |

Also record: wall-clock time, cost, nudges, approval prompts.

## Caveats to state in the write-up

- **Tier B reflects one design.** The issue says the matching rule is "to decide", and PR #157 was
  written with Claude's help. Tests that check its particular choices (for example that free text
  loses its format characters) can fail a different but valid fix. Read Tier A as the pass bar and
  Tier B as agreement with the reference.
- **One task, two tools, three runs each** is an anecdote with a small sample. It is a bug fix in
  security-sensitive code with an unusually strict spec, not a feature or a refactor.
- **The repo is tuned for Claude Code** (`CLAUDE.md`, subagent definitions). `AGENTS.md` is an
  identical copy and `.claude/` is removed for both, but the habits of the repo's history still
  favour Claude's style.
- The old property test passes at the parent unless Hypothesis generates `'\xad'`. Do not use it as
  the grader.
