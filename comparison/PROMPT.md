# Task: fix a crash in redaction's `Terms` (identical prompt for every run)

You are one of two coding agents given exactly this task, so that your work can be compared
side by side. Work the way you normally would. Don't try to look good or guess what an evaluator
wants. Honest reporting matters more than a tidy result: say what you did not verify and where you
were unsure.

## Repository and rules

The repository in the current directory is `aibi`. Read `CLAUDE.md` (also provided as `AGENTS.md`;
same content) and the parts of `SPEC.md` it points to before you change anything under `core/`.
Follow its code rules, non-negotiables and gates.

For this exercise, skip its *process* rules: do not push, open pull requests, call GitHub or any
web service, use subagents, or run cold reviews. Package installs are fine. Do not try to look up
this issue or its fix online. Do not commit; leave your changes in the working tree.

## The issue

**Core: redaction's `Terms` raises `IndexError` for a term with no token (e.g. a string constant of `'\xad'`)**

`tests/core/store/test_redaction.py::test_every_string_the_engine_accepts_as_a_constant_shares_a_reading_with_its_value`
can fail for the example `spelling=('string', '\xad')` (a soft hyphen), with:

```
src/aibi/core/store/redaction.py:579: in __init__
    starts.setdefault(tokens[0].group(), set()).add(len(tokens))
IndexError: list index out of range
```

`tokens = list(_runs().finditer(term))` is empty for a term made only of characters that are not
token characters, and `tokens[0]` is taken unconditionally. The test is a Hypothesis property test,
so it fails only when Hypothesis happens to generate such an example; any run where its example
database holds it fails every time.

Why it matters beyond the test: `Terms` is the redaction/erasure matcher (D261-D269). A string
constant, name or category value that consists only of such characters makes building a person's
terms raise instead of redacting or refusing.

Behaviour to decide: a term with no token is matched only as text (the class doc says `exact` holds
"the terms matched as text and as tokens"), so it should be skipped in `starts`, not crash. A brute
force over every Unicode code point and short combinations should show no other crasher in `Terms`
and `Terms.whole`.

## What to do

1. **Diagnose first.** Find the cause, state it, and check for other crashers in the same code
   before you edit.
2. **Plan briefly** (at most 15 lines): the fix, the open decisions and which way you decide each.
   Then implement. Ask a human only if you are truly blocked; otherwise state your assumption and
   continue.
3. **Fix** it in `server/src/aibi/core/store/redaction.py`. Decide how a term with no token is
   matched, and what happens to free text that contains it. If this changes a rule `SPEC.md` states,
   amend `SPEC.md` in the same change, as the repo's rules require.
4. **Test it.** Add a regression test for the example above, and a brute-force test over every
   Unicode code point and short combinations for `Terms` and `Terms.whole`. Also cover the erasure
   path (a person whose key or identifier value is such a term) if you find it reachable.
5. **Run the gates** from `server/`, quietly, as `CLAUDE.md` describes: `ruff check`,
   `ruff format --check`, `pyright`, `lint-imports`, and `uv run pytest tests/core/store`. Then
   `uv run pytest tests/core` if time allows. Skip the `million` and `measurement` tests.
   Never skip, disable or weaken a test to get green. If something fails, say so.
6. **Report.** Write `RESULT.md` at the repository root (leave it untracked) with these sections:
   Diagnosis; Matching rule you chose and why; Files changed; Tests added; Gates run (exact command
   and outcome of each); Not verified / risks; Rough time spent per phase; Any point where you
   wanted a human decision. Then end with a summary of at most 10 lines.
