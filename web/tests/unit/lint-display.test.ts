// @vitest-environment node
/**
 * D427's rows of the display lint (R90–R93, owner 2b-1L; D429): each row's corpus linted THROUGH
 * `eslint.config.mjs` (the canary that the rule is wired: a rule the configuration does not register
 * refuses nothing, and each corpus holds cases it must refuse), with its controls, which no
 * `display/*` rule may report. The corpora are `tests/gates/display-lint.ts`'s: hand rows for the
 * classes the reviews found (some asserted on the message of the branch they stand for), and rows
 * GENERATED from the rule's own tables and the language's forms (every write of a name; every write
 * and read of a DOM member × every receiver; a DOM function at every place of a reference; every key
 * of `CALLS` and `ADDRESS`; every element and attribute; R90's walk by its constructors × the raw
 * member's place; every library component type × every place a function reaches a tag), so that a
 * table entry and a branch of the walk have their rows by construction. R90 holds at JSX positions and
 * component returns; its flow class (a render slot reached other than at a JSX position) is OPEN,
 * each probe asserted open (`RAW_OPEN`), until R90f (2b-1L2). The tables the rules read (the class of
 * `ReactNode`, every `exports` subpath, the lists against the DOM library) are
 * `tests/gates/display-lint.test.ts`'s; `tests/gates/display-coverage.test.ts` holds that every block
 * of the rules is executed by the corpora (execution, not assertion). The lints run once, as the file
 * loads.
 */
import { describe, expect, it } from "vitest";

import { ADDRESS, ATTRIBUTES, CALLS, ELEMENTS, LITERAL_ATTRIBUTES, RULES, SITES } from "../../scripts/lint/display.mjs";
import { ANSWERS_CASES, ANSWERS_HEADER, type Case, corpora, DOM_RESIDUALS, displayLinter, type Linted, lintCases, RAW_OPEN, RAW_RESIDUALS, wrong } from "../gates/display-lint";

const linter = displayLinter();

const CORPORA = corpora({ elements: ELEMENTS, attributes: ATTRIBUTES, literal: LITERAL_ATTRIBUTES, calls: [...CALLS.keys()], address: ADDRESS });
const LINTED = new Map<string, { readonly linted: Linted; readonly cases: readonly Case[] }>();
for (const corpus of CORPORA) {
  LINTED.set(corpus.name, { linted: await lintCases(linter, corpus.file, corpus.header, corpus.cases), cases: corpus.cases });
}
const answersInApi = await lintCases(linter, "src/api/probe-display.ts", ANSWERS_HEADER, ANSWERS_CASES.slice(0, 1));

/** What a corpus came to against what its cases state (`wrong`), its parse failures first. */
function held(name: string, only?: readonly Case[]): string[] {
  const found = LINTED.get(name);
  if (found === undefined) {
    return [`${name}: no such corpus`];
  }
  return [...found.linted.fatal, ...wrong(found.linted, only ?? found.cases)];
}

describe("the display lint (D429), through eslint.config.mjs", () => {
  it("R90: no non-literal string, number or bigint at a JSX position or as what a component returns, every member of ReactNode walked; the elements, attributes and specifiers are allow-lists", () => {
    expect.hasAssertions();
    expect(Object.keys(RULES)).toEqual(expect.arrayContaining(["raw-text", "elements", "specifiers"]));
    expect(LINTED.get("raw")?.linted.header).toEqual([]);
    // The own children's every write (MA-1), the residuals open by their probes, D428's readers at the JSX positions they reach.
    expect(["raw", "raw, components", "open", "elements", "specifiers"].flatMap((name) => held(name))).toEqual([]);
    expect(held("raw", RAW_RESIDUALS)).toEqual([]);
    // The flow class, OPEN until R90f (2b-1L2): each probe asserted not refused.
    expect(LINTED.get("raw, open")?.linted.header).toEqual([]);
    expect(RAW_OPEN.every(({ refused }) => refused.length === 0)).toBe(true);
    expect(held("raw, open")).toEqual([]);
  });

  it("R91: no write to the DOM and no function of the DOM library outside the reviewed list, in app code", () => {
    expect.hasAssertions();
    expect(Object.keys(RULES)).toContain("dom");
    expect(LINTED.get("dom")?.linted.header).toEqual([]);
    expect([...held("dom"), ...held("dom, generated")]).toEqual([]);
    // The residuals: a DOM value flowed into a type that does not say so, asserted open.
    expect(held("dom", DOM_RESIDUALS)).toEqual([]);
    expect(SITES.filter(({ rule }) => rule === "dom").map(({ file, code }) => `${file}: ${code}`)).toEqual([
      "src/catalogue/main.tsx: rootElement().textContent",
      "src/operator/main.tsx: rootElement().textContent",
      "src/operator/Shell.tsx: field.value",
    ]);
  });

  it("R92: the address's members are read in the router module alone", () => {
    expect.hasAssertions();
    expect(Object.keys(RULES)).toContain("address");
    expect(held("address")).toEqual([]);
  });

  it("R93: no app module outside src/api/ reads an operator answer's body or codes", () => {
    expect.hasAssertions();
    expect(Object.keys(RULES)).toContain("answers");
    expect([...held("answers"), ...answersInApi.fatal]).toEqual([]);
    // The seam reads them: the rule is off in src/api/.
    expect(answersInApi.cases.get("a member")).toEqual([]);
    expect(SITES.filter(({ rule }) => rule === "answers").map(({ file, code }) => `${file}: ${code}`)).toEqual(["src/harness/Harness.tsx: found.body", "src/harness/Harness.tsx: outcome.codes"]);
  });
});
