/**
 * The display lint's blocks against its corpora (D429; MA-3 of round 1 of #119, MA-2 of round 2):
 * every block of `scripts/lint/display.mjs` that V8's precise coverage sees is EXECUTED by the rows'
 * corpora (and the scenarios below: the real app, the entries, the router module, the harness, the
 * seam, a stale site), or it is in `UNREACHED`, each with the reason no corpus can reach it. This gate
 * counts execution, not assertion: a block a corpus runs passes here whatever that corpus asserts (the
 * rows' tests assert, and the mutation pass measures them: its survivors are seven equivalent and one stricter
 * than the rule, each with its reason in D429). So a block added to the rule that no
 * corpus executes fails here, and so does a corpus that no longer executes its block. The coverage is
 * read from the module ESLint itself loads (`eslint.config.mjs`'s import), started before it loads.
 */
import { readFileSync } from "node:fs";
import { Session } from "node:inspector/promises";
import path from "node:path";

import { createRequire } from "node:module";

import { expect, it } from "vitest";

import { ADDRESS, ATTRIBUTES, CALLS, ELEMENTS, ENTRY_SPECIFIERS, LITERAL_ATTRIBUTES, ROUTER_SPECIFIERS } from "../../scripts/lint/display.mjs";
import { ANSWERS_CASES, ANSWERS_HEADER, type Case, corpora, displayLinter, lintCases } from "./display-lint";
import { WEB } from "./program";
import { MODULE } from "./router-scan";

/** The blocks no corpus reaches, by their first line, each with its reason. */
const UNREACHED: Readonly<Record<string, string>> = {
  "return Array.isArray(value) ? value : NONE; ⟶ : NONE": "A list field of an ESTree node (arguments, properties, elements, params, ...) is an array in every tree the parser makes.",
  "return constraint === type ? undefined : constraint; ⟶ ? undefined": "The checker gives no deferred type itself as its base constraint; the guard keeps the walk from reading a type as its own constraint if a release did.",
};

const RULE_FILE = path.join(WEB, "scripts/lint/display.mjs");

interface Range {
  readonly startOffset: number;
  readonly endOffset: number;
  readonly count: number;
}

interface ScriptCoverage {
  readonly url: string;
  readonly functions: readonly { readonly functionName: string; readonly ranges: readonly Range[] }[];
}

const session = new Session();
session.connect();
await session.post("Profiler.enable");
await session.post("Profiler.startPreciseCoverage", { callCount: true, detailed: true });

const linter = displayLinter();
for (const corpus of corpora({ elements: ELEMENTS, attributes: ATTRIBUTES, literal: LITERAL_ATTRIBUTES, calls: [...CALLS.keys()], address: ADDRESS })) {
  await lintCases(linter, corpus.file, corpus.header, corpus.cases);
}
await lintCases(linter, "src/api/probe-display.ts", ANSWERS_HEADER, ANSWERS_CASES);
/** Each allowed name of a table, imported in a file that has the table. */
const allowed = (table: Readonly<Record<string, { readonly values: readonly string[] }>>): Case[] =>
  Object.entries(table).flatMap(([specifier, { values }]) => values.map((name, index) => ({ id: `${specifier}: ${name}`, code: `import { ${name} as a${String(index)}${specifier.replace(/\W/gu, "_")} } from "${specifier}";`, refused: [] })));
await lintCases(linter, "src/catalogue/main.tsx", "", allowed(ENTRY_SPECIFIERS));
await lintCases(linter, MODULE, "", allowed(ROUTER_SPECIFIERS));
await lintCases(linter, "src/harness/probe-display.tsx", "declare const raw: string;", [{ id: "a data attribute", code: "export const d = <output data-codes={raw} />;", refused: [] }]);
await linter.lintFiles(["src"]);
const shell = readFileSync(path.join(WEB, "src/operator/Shell.tsx"), "utf8");
await linter.lintText(shell.replace('field.value = "";', ""), { filePath: path.join(WEB, "src/operator/Shell.tsx") });
await linter.lintText(shell, { filePath: path.join(WEB, "src/operator/Shell.tsx") });

// The rule's module as ESLint loaded it (Node's own instance, `require` of an ES module), for the
// branch no lint reaches: a lint with no type information.
const native = createRequire(import.meta.url)(RULE_FILE) as typeof import("../../scripts/lint/display.mjs");
const bare = { sourceCode: { parserServices: {} }, cwd: WEB, filename: path.join(WEB, "src/probe.ts"), options: [] };
const unservedRules = Object.entries(native.RULES).filter(([, rule]) => {
  try {
    rule.create(bare as unknown as Parameters<typeof rule.create>[0]);
    return true;
  } catch {
    return false;
  }
});

const taken = await session.post("Profiler.takePreciseCoverage");
await session.post("Profiler.stopPreciseCoverage");
session.disconnect();

/** The blocks of the rule's module that ran no time, by their first line (trimmed). */
function unreached(): string[] {
  const scripts: readonly ScriptCoverage[] = taken.result;
  const source = readFileSync(RULE_FILE, "utf8");
  // The module as Node loads it (vitest's own transform of it, for the tests' imports, is another
  // script of the same name, whose text is not the file's): the one whose ranges end at the file's end.
  const candidates = scripts.filter(({ url }) => url.endsWith("/scripts/lint/display.mjs"));
  const script = candidates.find(({ functions }) => functions.some(({ ranges }) => ranges.some(({ endOffset }) => endOffset === source.length)));
  if (script === undefined) {
    throw new Error(`the rule's module was not loaded under coverage (${String(candidates.length)} scripts of its name)`);
  }
  const found = new Set<string>();
  for (const { ranges } of script.functions) {
    for (const range of ranges) {
      if (range.count === 0) {
        const block = (source.slice(range.startOffset, range.endOffset).trim().split("\n")[0] ?? "").trim().slice(0, 60);
        const start = source.lastIndexOf("\n", range.startOffset) + 1;
        const end = source.indexOf("\n", range.startOffset);
        found.add(`${source.slice(start, end === -1 ? source.length : end).trim().slice(0, 120)} ⟶ ${block}`);
      }
    }
  }
  return [...found].sort();
}

it("runs the branch no lint reaches through the module itself: each rule that reads types refuses a lint with no types", () => {
  expect(unservedRules.map(([name]) => name)).toEqual(["answers"]);
});

it("executes every block of the display lint's rules under the corpora, but the listed with their reasons (execution, not assertion)", () => {
  expect(unreached()).toEqual(Object.keys(UNREACHED).sort());
  expect(Object.values(UNREACHED).every((reason) => reason.length > 10)).toBe(true);
}, 600_000);
