/**
 * The document seam's surface gate (`draft-gate.ts`, D432) on the real program, its generated
 * controls and the control of the controls (MA1 of the seam's plan round 2; minors 1, 5 and 6 of
 * round 3), and `shapeOf`'s fix for the operator client's pins (`surface.ts`).
 *
 * **The controls are generated**, as a brute force over copies of `document.ts` in one program
 * (each copy is a module of `src/api/`, so its imports are the real ones, and the gate is handed its
 * path): every place of `Draft` (`column`, `value`, `dataset`, `unit`, a view column, the first and
 * the rest, and a cohort) holding each type of `TYPES` (the primitives, `unknown`, `any`, `never`,
 * `object`, `{}`, every other brand, a `Choice` of another role, of `Role` or of a union of roles, a
 * same-named fake `Typed` and `DatasetId` of another module, a template literal, a list, a mapped
 * type, a function, a union with an extra arm of an allowed brand or `undefined`, an intersection);
 * every object with a member added (required and optional, a string, a brand, a method, a function
 * member, an index signature, a unique-symbol key, a merged declaration) and each member removed,
 * made optional or writable; every list made mutable; every tuple's flags changed; and the roots'
 * rule (one call signature, one parameter, no type parameter): an overload, a second parameter
 * (required, optional, rest), a default, a generic, a `this`, an exported constant, class or
 * `documentOf`, and `optionsOf` taking another type. Each must fail **at its own path** (a diff of
 * the table or a new problem there, and none elsewhere), whatever the member count.
 *
 * **The control of the controls:** the same type at a place passes; an identity edit passes
 * (members reordered and a local alias, an interface written as a type alias, `ReadonlyArray<T>`,
 * the union's members in the other order, the file reformatted, the parameter's type through a
 * local alias); and the real program built after another program has run in the same worker
 * (which shifts the process-global symbol counter that defeated round 2's printed pins) gives the
 * same table.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import { type DraftWalk, brandDeclarations, draftGate } from "./draft-gate";
import { expectedTable, ROOTS } from "./draft-table";
import { appOptions, check, WEB } from "./program";
import { PINS, shapeOf } from "./surface";

const DOCUMENT = "src/api/document.ts";
const ORIGINAL = readFileSync(path.join(WEB, DOCUMENT), "utf8");
const BLOCK_START = ORIGINAL.indexOf("export interface LeafDraft");
const BLOCK_END = ORIGINAL.indexOf("/** An option of a picker");
const BLOCK = ORIGINAL.slice(BLOCK_START, BLOCK_END);

/** What every copy imports and declares beside the real module's text: the fake brands and a unique
 * symbol of its own. */
const PRELUDE = 'import type { Typed as FakeTyped, DatasetId as FakeDatasetId } from "./zz-fake";\ndeclare const zzBrand: unique symbol;\n';

/** Same-named brands of another module (S2 of round 2: they printed as the real ones). */
const FAKE = "declare const fake: unique symbol;\nexport type Typed = string & { readonly [fake]: true };\nexport type DatasetId = string & { readonly [fake]: true };\n";

/** A variant: its name, the text of its `document.ts`, and where it must fail (`null`: it must pass). */
type Variant = readonly [name: string, text: string, at: string | null];

/** The module's text with the `Draft` declarations replaced. */
const withBlock = (block: string, extra = ""): string => PRELUDE + ORIGINAL.slice(0, BLOCK_START) + block + ORIGINAL.slice(BLOCK_END) + extra;

/** The `Draft` declarations with one text replaced (it must be there). */
function sub(from: string, to: string): string {
  if (!BLOCK.includes(from)) {
    throw new Error(`the Draft declarations hold no ${from}`);
  }
  return BLOCK.replace(from, to);
}

/** The module's text with one text of it replaced (it must be there). */
function subModule(from: string, to: string): string {
  if (!ORIGINAL.includes(from)) {
    throw new Error(`document.ts holds no ${from}`);
  }
  return PRELUDE + ORIGINAL.replace(from, to);
}

/** The places of `Draft`: the text that declares each, the template that puts a type `X` there, the
 * type there now, and its path (union members' numbers dropped, tuple positions as `[i]`). */
const PLACES: readonly (readonly [from: string, to: string, now: string, at: string])[] = [
  ['readonly column: Choice<"leaf">;', "readonly column: X;", 'Choice<"leaf">', "(#0).cohorts[i].leaves[].column"],
  ["readonly value: Typed;", "readonly value: X;", "Typed", "(#0).cohorts[i].leaves[].value"],
  ["readonly dataset: DatasetId;", "readonly dataset: X;", "DatasetId", "(#0).dataset"],
  ['readonly unit: Choice<"unit">;', "readonly unit: X;", 'Choice<"unit">', "(#0).unit"],
  ['readonly [Choice<"view">, ...Choice<"view">[]]', 'readonly [X, ...Choice<"view">[]]', 'Choice<"view">', "(#0).view.columns[i]"],
  ['readonly [Choice<"view">, ...Choice<"view">[]]', 'readonly [Choice<"view">, ...(X)[]]', 'Choice<"view">', "(#0).view.columns[i]"],
  ["readonly [CohortDraft] | readonly [CohortDraft, CohortDraft]", "readonly [CohortDraft] | readonly [CohortDraft, X]", "CohortDraft", "(#0).cohorts[i]"],
];

/** The types each place is given (each must fail, but the place's own). */
const TYPES: readonly string[] = [
  "string",
  "number",
  "boolean",
  "bigint",
  "symbol",
  "unknown",
  "any",
  "never",
  "object",
  "{}",
  "null",
  "undefined",
  "Typed",
  "DatasetId",
  'Choice<"unit">',
  'Choice<"leaf">',
  'Choice<"view">',
  "Choice<Role>",
  'Choice<"leaf" | "view">',
  "FakeTyped",
  "FakeDatasetId",
  "`${string}`",
  "`$${string}`",
  "Lowercase<string>",
  "Typed[]",
  "readonly Typed[]",
  "Readonly<{ v: Typed }>",
  "() => Typed",
  "Typed | DatasetId",
  'Choice<"unit"> | Choice<"leaf">',
  'Choice<"leaf"> | Typed',
  'Choice<"view"> | Choice<"leaf">',
  "Typed | undefined",
  'Choice<"leaf"> | undefined',
  "DatasetId | null",
  "Typed & { readonly notes: string }",
  'Choice<"leaf"> & { readonly notes: string }',
  "Typed & {}",
  'Choice<"unit"> & Choice<"leaf">',
  "{ readonly leaves: readonly LeafDraft[]; readonly notes: string }",
];

/** The objects of `Draft`: a member of each (where a member is added), its path, and its members. */
const OBJECTS: readonly (readonly [anchor: string, at: string, members: readonly string[]])[] = [
  ["readonly value: Typed;", "(#0).cohorts[i].leaves[]", ['readonly column: Choice<"leaf">;', "readonly value: Typed;"]],
  ["readonly leaves: readonly LeafDraft[];", "(#0).cohorts[i]", ["readonly leaves: readonly LeafDraft[];"]],
  ["readonly dataset: DatasetId;", "(#0)", ["readonly dataset: DatasetId;", 'readonly unit: Choice<"unit">;', "readonly cohorts: readonly [CohortDraft] | readonly [CohortDraft, CohortDraft];"]],
  ["readonly view: { readonly columns:", "(#0).view", []],
];

/** The members a lazy hand adds to an object. */
const ADDED: readonly string[] = [
  "readonly notes: string;",
  "readonly notes?: string;",
  "readonly notes: Typed;",
  'readonly extra: Choice<"leaf">;',
  "readonly toJSON: () => string;",
  "toJSON(): string;",
  "notes: Typed;",
  "readonly [zzBrand]: Typed;",
  "readonly [key: string]: unknown;",
];

function variants(): Variant[] {
  const found: Variant[] = [];
  for (const [place, [from, to, now, at]] of PLACES.entries()) {
    for (const type of TYPES) {
      found.push([`place ${String(place)} ${at} := ${type}`, withBlock(sub(from, to.replace("X", type))), type === now ? null : at]);
    }
  }
  for (const [anchor, at, members] of OBJECTS) {
    for (const added of ADDED) {
      const into = anchor === "readonly view: { readonly columns:" ? sub(anchor, `readonly view: { ${added} readonly columns:`) : sub(anchor, `${anchor}\n  ${added}`);
      found.push([`${at} + ${added}`, withBlock(into), at]);
    }
    for (const member of members) {
      found.push([`${at} - ${member}`, withBlock(sub(member, "")), at]);
      found.push([`${at} ${member} optional`, withBlock(sub(member, member.replace(":", "?:"))), at]);
      found.push([`${at} ${member} writable`, withBlock(sub(member, member.replace("readonly ", ""))), at]);
    }
  }
  found.push(["(#0).view.columns writable", withBlock(sub("readonly view: { readonly columns:", "readonly view: { columns:")), "(#0).view"]);
  found.push(["(#0).view writable", withBlock(sub("readonly view: {", "view: {")), "(#0)"]);
  found.push(["(#0).view optional", withBlock(sub("readonly view: {", "readonly view?: {")), "(#0)"]);
  found.push(["(#0).view mapped", withBlock(sub('readonly view: { readonly columns: readonly [Choice<"view">, ...Choice<"view">[]] };', 'readonly view: Readonly<{ columns: readonly [Choice<"view">, ...Choice<"view">[]] }>;')), "(#0).view"]);
  found.push(["LeafDraft merged with a member", withBlock(BLOCK + "export interface LeafDraft {\n  readonly notes: string;\n}\n"), "(#0).cohorts[i].leaves[]"]);
  // Lists and tuples.
  found.push(["leaves mutable", withBlock(sub("readonly leaves: readonly LeafDraft[];", "readonly leaves: LeafDraft[];")), "(#0).cohorts[i].leaves"]);
  found.push(["leaves Array<>", withBlock(sub("readonly leaves: readonly LeafDraft[];", "readonly leaves: Array<LeafDraft>;")), "(#0).cohorts[i].leaves"]);
  found.push(["leaves a tuple", withBlock(sub("readonly leaves: readonly LeafDraft[];", "readonly leaves: readonly [LeafDraft];")), "(#0).cohorts[i].leaves"]);
  found.push(["cohorts' pair mutable", withBlock(sub("readonly [CohortDraft, CohortDraft]", "[CohortDraft, CohortDraft]")), "(#0).cohorts"]);
  found.push(["cohorts' one mutable", withBlock(sub("readonly [CohortDraft] |", "[CohortDraft] |")), "(#0).cohorts"]);
  found.push(["columns mutable", withBlock(sub('readonly columns: readonly [Choice<"view">', 'readonly columns: [Choice<"view">')), "(#0).view.columns"]);
  for (const cohorts of [
    "readonly [CohortDraft] | readonly [CohortDraft, ...CohortDraft[]]",
    "readonly [CohortDraft] | readonly [CohortDraft, CohortDraft] | readonly [CohortDraft, CohortDraft, CohortDraft]",
    "readonly [CohortDraft, CohortDraft?]",
    "readonly CohortDraft[]",
    "readonly [CohortDraft?] | readonly [CohortDraft, CohortDraft]",
    "readonly [...CohortDraft[], CohortDraft]",
    "readonly [CohortDraft]",
    "readonly [CohortDraft, CohortDraft]",
    "readonly [] | readonly [CohortDraft] | readonly [CohortDraft, CohortDraft]",
  ]) {
    found.push([`cohorts := ${cohorts}`, withBlock(sub("readonly [CohortDraft] | readonly [CohortDraft, CohortDraft]", cohorts)), "(#0).cohorts"]);
  }
  for (const columns of [
    'readonly [Choice<"view">, Choice<"view">]',
    'readonly [...Choice<"view">[], Choice<"view">]',
    'readonly [Choice<"view">?]',
    'readonly Choice<"view">[]',
    'readonly [Choice<"view">, ...Choice<"view">[], Choice<"view">]',
    'readonly [Choice<"view">]',
    'readonly [Choice<"view">, Choice<"view">?, ...Choice<"view">[]]',
  ]) {
    found.push([`columns := ${columns}`, withBlock(sub('readonly [Choice<"view">, ...Choice<"view">[]]', columns)), "(#0).view.columns"]);
  }
  // The roots' rule.
  const validate = 'export function validateDraft(draft: Draft): Promise<Outcome<Answered<"validate_document">>> {';
  const options = 'export function optionsOf(answer: Answered<"describe_dataset">): Options {';
  found.push(["an exported constant", withBlock(BLOCK, "export const LIMIT = 4;\n"), "LIMIT"]);
  found.push(["an exported class", withBlock(BLOCK, "export class Thing {\n  constructor(readonly x: string) {}\n}\n"), "Thing"]);
  found.push(["documentOf exported", withBlock(BLOCK, "export { documentOf };\n"), "documentOf"]);
  found.push(["a member on a root", withBlock(BLOCK, "validateDraft.notes = 'x';\n"), "validateDraft"]);
  found.push(["a fourth sender", withBlock(BLOCK, 'export function sendDraft(draft: Draft): Promise<Outcome<Answered<"validate_document">>> {\n  return validateDraft(draft);\n}\n'), "sendDraft"]);
  found.push([
    "an overload with a string",
    subModule(validate, `export function validateDraft(draft: string): Promise<Outcome<Answered<"validate_document">>>;\nexport function validateDraft(draft: Draft): Promise<Outcome<Answered<"validate_document">>>;\n${validate.replace("draft: Draft", "draft: Draft | string")}`),
    "validateDraft",
  ]);
  found.push(["a second parameter", subModule(validate, validate.replace("draft: Draft)", "draft: Draft, notes: string)")), "validateDraft"]);
  found.push(["an optional second parameter", subModule(validate, validate.replace("draft: Draft)", "draft: Draft, notes?: string)")), "validateDraft"]);
  found.push(["a rest parameter", subModule(validate, validate.replace("draft: Draft)", "draft: Draft, ...notes: string[])")), "validateDraft"]);
  found.push(["a defaulted parameter", subModule(validate, validate.replace("draft: Draft)", "draft: Draft = (null as unknown as Draft))")), "validateDraft(#0)"]);
  found.push(["a generic", subModule(validate, validate.replace("validateDraft(draft: Draft)", "validateDraft<T extends Draft>(draft: T)")), "validateDraft"]);
  found.push(["a this", subModule(validate, validate.replace("(draft: Draft)", "(this: void, draft: Draft)")), "validateDraft"]);
  found.push(["optionsOf of another operation", subModule(options, options.replace('Answered<"describe_dataset">', 'Answered<"search_catalog">')), "optionsOf(#0)"]);
  found.push(["optionsOf of a describe answer or null", subModule(options, options.replace('Answered<"describe_dataset">', 'Answered<"describe_dataset"> | null')), "optionsOf(#0)"]);
  found.push(["optionsOf of a describe answer and more", subModule(options, options.replace('Answered<"describe_dataset">', 'Answered<"describe_dataset"> & { readonly notes: string }')), "optionsOf(#0)"]);
  found.push(["optionsOf of a second parameter", subModule(options, options.replace('answer: Answered<"describe_dataset">', 'answer: Answered<"describe_dataset">, notes?: string')), "optionsOf"]);
  found.push(["optionsOf generic", subModule(options, options.replace('optionsOf(answer: Answered<"describe_dataset">)', 'optionsOf<A extends Answered<"describe_dataset">>(answer: A)')), "optionsOf"]);
  // The control of the controls: identity edits.
  found.push(["CONTROL unchanged", withBlock(BLOCK), null]);
  found.push([
    "CONTROL members reordered, a local alias",
    withBlock(sub('export interface LeafDraft {\n  readonly column: Choice<"leaf">;\n  readonly value: Typed;\n}', 'type Value = Typed;\nexport interface LeafDraft {\n  readonly value: Value;\n  readonly column: Choice<"leaf">;\n}')),
    null,
  ]);
  found.push(["CONTROL an interface as a type alias", withBlock(sub("export interface CohortDraft {\n  readonly leaves: readonly LeafDraft[];\n}", "export type CohortDraft = {\n  readonly leaves: readonly LeafDraft[];\n};")), null]);
  found.push(["CONTROL ReadonlyArray<T>", withBlock(sub("readonly leaves: readonly LeafDraft[];", "readonly leaves: ReadonlyArray<LeafDraft>;")), null]);
  found.push(["CONTROL the union in the other order", withBlock(sub("readonly [CohortDraft] | readonly [CohortDraft, CohortDraft]", "readonly [CohortDraft, CohortDraft] | readonly [CohortDraft]")), null]);
  found.push(["CONTROL reformatted", withBlock(BLOCK.replaceAll(";\n", ";\n\n").replaceAll("\n  ", "\n        ").replaceAll(": ", " :  ")), null]);
  found.push(["CONTROL optionsOf through a local alias", subModule(options, `type Described = Answered<"describe_dataset">;\n${options.replace('answer: Answered<"describe_dataset">', "answer: Described")}`), null]);
  return found;
}

/** A row's path (before ` -> ` or `: `), with the union members' numbers dropped and tuple positions
 * as `[i]`, and, for a place of `Draft` (`(#0)...`), a draft root's name dropped (its three roots
 * share the table). */
function placeOf(row: string, ofDraft: boolean): string {
  const cut = [row.indexOf(" -> "), row.indexOf(": ")].filter((at) => at >= 0);
  const raw = cut.length === 0 ? row : row.slice(0, Math.min(...cut));
  return (ofDraft ? raw.replace(/^(validateDraft|countDraft|runDraft)(?=\(#0\))/u, "") : raw).replace(/\|\d+/gu, "").replace(/\[\d+\]/gu, "[i]");
}

/** Where a walk differs from the base walk: each differing row's and new problem's place. */
function differences(base: DraftWalk, found: DraftWalk, ofDraft: boolean): string[] {
  const rows = [...found.table.filter((row) => !base.table.includes(row)), ...base.table.filter((row) => !found.table.includes(row)), ...found.problems.filter((problem) => !base.problems.includes(problem))];
  const roots = found.roots.filter((root) => !base.roots.includes(root));
  return [...new Set([...rows.map((row) => placeOf(row, ofDraft)), ...roots])].sort();
}

/** The judgement of a variant's walk against the base's: `null` if it fails at its path `at` and
 * nowhere else (or passes, for an identity edit, `at` `null`), and what is wrong otherwise. */
function judged(base: DraftWalk, walked: DraftWalk, at: string | null): string | null {
  const found = differences(base, walked, at === null || at.startsWith("(#0)"));
  if (at === null) {
    return found.length > 0 ? `an identity edit differs at ${found.join(", ")}` : null;
  }
  if (found.length === 0) {
    return `passes (it must fail at ${at})`;
  }
  return !found.some((place) => place === at) || !found.every((place) => within(place, at)) ? `fails at ${found.join(", ")}, not at ${at} alone` : null;
}

/** Whether a place is at a path or below it. */
const within = (place: string, at: string): boolean => place === at || place.startsWith(`${at}.`) || place.startsWith(`${at}[`) || place.startsWith(`${at}(`);

let real: DraftWalk;
let program: ts.Program;
let checker: ts.TypeChecker;

beforeAll(() => {
  const parsed = ts.getParsedCommandLineOfConfigFile(path.join(WEB, "tsconfig.app.json"), {}, { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => undefined });
  if (parsed === undefined) {
    throw new Error("tsconfig.app.json is unreadable");
  }
  program = ts.createProgram(parsed.fileNames, appOptions());
  checker = program.getTypeChecker();
  real = draftGate(program, checker);
}, 120_000);

describe("the document seam's surface gate, by identity (D432)", () => {
  it("holds on the real program: the roots are document.ts' value exports, no problem, and the table is the checked-in one", () => {
    expect(real.roots).toEqual(ROOTS);
    expect(real.problems).toEqual([]);
    expect(real.table).toEqual(expectedTable());
  });

  it("resolves each brand by its declaring file and name", () => {
    expect(brandDeclarations(program, checker)).toEqual(["src/layout/Field.tsx:Typed", "src/api/text.tsx:DatasetId", "src/api/text.tsx:Choice"]);
  });

  it("a Choice's role is no phantom: a Choice of one role is not assignable to another's (a type cell, minor 6)", () => {
    const probe = [
      "import type { Choice, Role } from './api';",
      'declare const unit: Choice<"unit">;',
      'declare const leaf: Choice<"leaf">;',
      'export const a: Choice<"leaf"> = unit;',
      'export const b: Choice<"view"> = leaf;',
      'export const c: Choice<"unit"> = leaf;',
      "export const d: Choice<Role> = leaf;",
      'export const e: Choice<"leaf"> = leaf;',
      "",
    ].join("\n");
    const checked = check({ "src/probe-role.ts": probe });
    const lines = checked.diagnostics("src/probe-role.ts").map((diagnostic) => [diagnostic.code, ts.getLineAndCharacterOfPosition(checked.source("src/probe-role.ts"), diagnostic.start ?? 0).line]);
    expect(lines).toEqual([
      [2322, 3],
      [2322, 4],
      [2322, 5],
    ]);
  });

  describe("its generated controls: every variant fails at its own path, and every identity edit passes", () => {
    const all = variants();
    let walks: Map<string, DraftWalk>;
    let base: DraftWalk;

    beforeAll(() => {
      const files: Record<string, string> = { "src/api/zz-fake.ts": FAKE };
      all.forEach(([, text], index) => {
        files[`src/api/zz-draft-${String(index)}.ts`] = text;
      });
      const checked = check(files);
      walks = new Map(all.map(([name], index) => [name, draftGate(checked.program, checked.checker, `src/api/zz-draft-${String(index)}.ts`)]));
      base = draftGate(checked.program, checked.checker, `src/api/zz-draft-${String(all.findIndex(([name]) => name === "CONTROL unchanged"))}.ts`);
    }, 300_000);

    it("the base copy walks as the real module does", () => {
      expect(base.problems).toEqual(real.problems);
      expect(base.table).toEqual(real.table);
      expect(base.roots).toEqual(real.roots);
    });

    it("generates the variants named (a count, so that a place or a type dropped is seen)", () => {
      expect(all.length).toBeGreaterThanOrEqual(PLACES.length * TYPES.length + 60);
      expect(new Set(all.map(([name]) => name)).size).toBe(all.length);
    });

    it("each fails at its own path and nowhere else, or passes if it is an identity edit", () => {
      const wrong: string[] = [];
      for (const [name, , at] of all) {
        const walked = walks.get(name);
        if (walked === undefined) {
          throw new Error(`no walk of ${name}`);
        }
        const verdict = judged(base, walked, at);
        if (verdict !== null) {
          wrong.push(`${name}: ${verdict}`);
        }
      }
      expect(wrong).toEqual([]);
    });

    it("the judgement sees a variant that fails elsewhere than its path, or not at all (its own control)", () => {
      const dataset = walks.get(`place 2 (#0).dataset := string`);
      const unchanged = walks.get("CONTROL unchanged");
      if (dataset === undefined || unchanged === undefined) {
        throw new Error("no walks");
      }
      expect(judged(base, dataset, "(#0).dataset")).toBeNull();
      expect(judged(base, dataset, "(#0).unit")).toMatch(/not at \(#0\)\.unit alone/u);
      expect(judged(base, dataset, "(#0)")).toMatch(/not at \(#0\) alone/u);
      expect(judged(base, unchanged, "(#0).unit")).toMatch(/passes/u);
      expect(judged(base, dataset, null)).toMatch(/an identity edit differs/u);
    });

    it("names each rule's own problem: an unpinned root, a member on a root, a generic (the rules' cells)", () => {
      const problems = (name: string): readonly string[] => walks.get(name)?.problems ?? [];
      expect(problems("documentOf exported")).toContain("documentOf: a root no pin names");
      expect(problems("a member on a root")).toContain("validateDraft: 1 call signatures, 0 construct signatures, 1 members (a root is a function of one call signature)");
      expect(problems("an exported class").some((one) => one.startsWith("Thing: 0 call signatures, 1 construct signatures"))).toBe(true);
      expect(problems("a generic")).toContain("validateDraft: a generic signature");
      expect(problems("a this")).toContain("validateDraft: a this parameter");
    });
  });

  it("the control of the controls: the real program built again after another program ran in the same worker gives the same walk (the symbol counter moved)", () => {
    const noise = Array.from({ length: 300 }, (_unused, index) => `declare const s${String(index)}: unique symbol;\nexport type T${String(index)} = { readonly [s${String(index)}]: readonly [string, number?, ...boolean[]] };`).join("\n");
    check({ "src/probe-noise.ts": `${noise}\n` });
    const again = check({ "src/probe-again.ts": "export * from './api';\n" });
    const walked = draftGate(again.program, again.checker);
    expect(walked).toEqual(real);
  });
});

/** The calls of a name in a source, each with the function it stands in (by its name). */
function callsOf(source: ts.SourceFile, name: string): string[] {
  const found: string[] = [];
  const visit = (node: ts.Node, inside: string): void => {
    const here = ts.isFunctionDeclaration(node) && node.name !== undefined ? node.name.text : inside;
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === name) {
      found.push(here);
    }
    ts.forEachChild(node, (child) => {
      visit(child, here);
    });
  };
  visit(source, "<module>");
  return found;
}

/** What of a source copies, iterates or passes on an object whole: a spread, a `for...in`, and
 * `Object`'s and `Reflect`'s readers of members, `JSON` and `structuredClone`. */
function copiers(source: ts.SourceFile): string[] {
  const found: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isSpreadElement(node) || ts.isSpreadAssignment(node)) {
      found.push(`a spread at ${String(source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1)}`);
    }
    if (ts.isForInStatement(node)) {
      found.push("a for...in");
    }
    if (ts.isPropertyAccessExpression(node) && ts.isIdentifier(node.expression) && ["Object", "Reflect", "JSON"].includes(node.expression.text) && !["freeze", "isFrozen"].includes(node.name.text)) {
      found.push(`${node.expression.text}.${node.name.text}`);
    }
    if (ts.isIdentifier(node) && node.text === "structuredClone") {
      found.push("structuredClone");
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

describe("document.ts' source: one reader of a choice, one minting function, no copy of a draft (A3, m8 of round 2)", () => {
  const source = (): ts.SourceFile => {
    const found = program.getSourceFile(path.join(WEB, DOCUMENT));
    if (found === undefined) {
      throw new Error("no document.ts");
    }
    return found;
  };

  it("calls choiceText once, in id(); choiceFor in optionsOf alone; walkedAs once, in optionsOf", () => {
    expect(callsOf(source(), "choiceText")).toEqual(["id"]);
    expect(new Set(callsOf(source(), "choiceFor"))).toEqual(new Set(["optionsOf"]));
    expect(callsOf(source(), "walkedAs")).toEqual(["optionsOf"]);
    expect(callsOf(source(), "documentOf").sort()).toEqual(["countDraft", "runDraft", "validateDraft"]);
  });

  it("spreads, iterates the keys of and passes on no object whole: it reads each member by its name", () => {
    expect(copiers(source())).toEqual([]);
  });

  it("the cells see what they hold (their controls)", () => {
    const probe = ts.createSourceFile("x.ts", "function id() { choiceText(a); }\nfunction b(d) { return { ...d, x: [...d.y] }; }\nfor (const k in d) {}\nObject.keys(d); JSON.stringify(d); structuredClone(d); Object.freeze(d);\n", ts.ScriptTarget.ES2023, true);
    expect(callsOf(probe, "choiceText")).toEqual(["id"]);
    expect(copiers(probe)).toEqual(["a spread at 2", "a spread at 2", "a for...in", "Object.keys", "JSON.stringify", "structuredClone"]);
  });
});

describe("shapeOf's fix: a unique-symbol key by its declaration, a tuple with its flags, a named type with its file (S2 of round 2; minor 5 of round 3)", () => {
  const PROBE = [
    "export type { ServerText } from './api';",
    "declare const brand: unique symbol;",
    "export interface Held { readonly [brand]: true; readonly t: readonly [string, number?, ...boolean[]]; readonly u: [string, ...number[], boolean] }",
    "export declare function take(held: Held): void;",
    "",
  ].join("\n");

  const shapeIn = (files: Record<string, string>, file: string): string => {
    const checked = check(files);
    const module = checked.checker.getSymbolAtLocation(checked.source(file));
    const take = module === undefined ? undefined : checked.checker.getExportsOfModule(module).find((one) => one.getName() === "take");
    const [signature] = take === undefined ? [] : checked.checker.getTypeOfSymbol(take).getCallSignatures();
    const [parameter] = signature?.getParameters() ?? [];
    if (parameter === undefined) {
      throw new Error("no take");
    }
    return shapeOf(checked.program, checked.checker, checked.checker.getTypeOfSymbol(parameter));
  };

  it("prints a unique-symbol key and a flagged tuple the same in two programs built in two orders", () => {
    const first = shapeIn({ "src/probe-shape.ts": PROBE }, "src/probe-shape.ts");
    const noise = Array.from({ length: 200 }, (_unused, index) => `declare const n${String(index)}: unique symbol;\nexport type N${String(index)} = { readonly [n${String(index)}]: 1 };`).join("\n");
    check({ "src/probe-noise.ts": `${noise}\n` });
    const second = shapeIn({ "src/probe-noise2.ts": `${noise}\n`, "src/probe-shape.ts": PROBE }, "src/probe-shape.ts");
    expect(first).toBe(second);
    expect(first).toBe("{ readonly [src/probe-shape.ts:brand]: true; readonly t: readonly [string, (number | undefined)?, ...(false | true)[]]; readonly u: [string, ...number[], false | true] }");
    expect(first).not.toContain("__@");
  });

  it("tells apart what round 2's printing did not: a brand of another module, and a tuple's flags", () => {
    const base = shapeIn({ "src/probe-shape.ts": PROBE }, "src/probe-shape.ts");
    const other = shapeIn(
      { "src/probe-brand.ts": "export declare const brand: unique symbol;\n", "src/probe-shape.ts": PROBE.replace("declare const brand: unique symbol;", "import { brand } from './probe-brand';") },
      "src/probe-shape.ts",
    );
    expect(other).toBe(base.replace("src/probe-shape.ts:brand", "src/probe-brand.ts:brand"));
    for (const [from, to] of [
      ["number?", "number"],
      ["...boolean[]", "boolean"],
      ["readonly [string,", "[string,"],
      ["[string, ...number[], boolean]", "[string, number, boolean]"],
    ] as const) {
      expect(shapeIn({ "src/probe-shape.ts": PROBE.replace(from, to) }, "src/probe-shape.ts"), `${from} -> ${to}`).not.toBe(base);
    }
  });

  it("the operator client's pins, regenerated, are 8b1433e's but for JsonOut, named by its file where it recurs (shown equivalent)", () => {
    const BEFORE = "ReadonlyArray<@JsonOut> | ServerNumber | false | null | string | true | { readonly [string]: @JsonOut }";
    const changed = Object.entries(PINS).filter(([, pin]) => pin.shape.includes("@"));
    expect(changed.map(([at]) => at).sort()).toEqual(["operator.acceptProposal(#0)", "operator.queueAt(#0)", "operator.rejectProposal(#0)", "operator.takeOver(#0)"]);
    for (const [, pin] of changed) {
      expect(pin.shape.replaceAll("@src/api/json.ts:JsonOut", "@JsonOut")).toBe(BEFORE);
    }
    expect(Object.values(PINS).some((pin) => pin.shape.includes("__@"))).toBe(false);
  });
});
