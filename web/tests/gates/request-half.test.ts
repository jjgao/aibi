/**
 * The request half's gates (D425), over the TypeScript program of the app, the lint and the
 * document:
 *
 * (a) **The operator client's surface** (n2, m7, MA2 of round 2): an allow-list over every place
 *     the client's caller hands it a value (`surface.ts`: every parameter, `this`, rest element,
 *     writable member and setter of every function type reachable from `operator.ts`'s and
 *     `oracle.ts`'s exports and `createCurator`, through members, returns, getters, construct
 *     signatures and overloads), which takes a primitive alone, or a pinned type at its pinned place
 *     by exact shape (`PINS`: the four nodes, `unlock`'s `AbortSignal`, `subscribe`'s listener,
 *     `install`'s targets, `change`'s edits, the maker's clocks and timer); the four flows pinned
 *     whole (one node, `Promise<Outcome>`); no `this` or `arguments` in the implementation; and the
 *     roots are every module of `src/api/` a file outside it imports, but the index. Controls: an
 *     extra `handle?` or `dataset?` parameter, a changed pin, and the review's N1-N14 with N15-N35
 *     of the same class, each refused at its own place whatever the member count; the class is held
 *     by `surface-brute.test.ts`, with the compiler as the oracle, over the input forms and the
 *     output forms (what a caller can write into a value the client gives it). A pin holds at its
 *     place alone, within a pinned member too (`unlock`, `change`, `subscribe`: a second place of
 *     the same shape is refused), and `entries` and `receivers` see a dynamic import, a re-export
 *     and an operator.ts that names `this` or `arguments`.
 * (b) **Generics, instantiated** (m7): every generic function the index exports (`kindOf`, and
 *     a namespace's; `entriesOf` and `memberOf`, unconstrained, are pinned by their exact
 *     signatures, D426) is instantiated at every member of its type parameter's constraint, the
 *     instantiation derived from the checker's constraint (never a hand-written table), and no
 *     instantiation's parameters can hold a `ServerNumber` (`takers.ts`). Control: a generic whose
 *     parameters reach a `ServerNumber` at one operation alone fails at that one, instantiated
 *     (uninstantiated, the check fails closed on its conditional type).
 * (c) **The four positions** (m6, n7): in the generated types they are `RequestId` and no other
 *     position is; a plain number, a box and a `RequestId`-shaped literal at each, through
 *     `routes` and a *declared* `exchange` of the probe's own over `RequestBody<O>` (the index
 *     exports no real one, D426), do not compile; the index exports no `RequestId` and nothing that
 *     makes one. Ways of minting one without a cast that the checker allows exist and are not
 *     closed (an overload with an `unknown` implementation, method-parameter bivariance, array and
 *     mutable-property covariance, a library method's bivariance): (c') pins examples, not a list.
 * (d) **The internals' owners** (n5, m16): the lint lets each internal of the request half be
 *     imported by its owner alone (`curator.ts` importing `stamp` is an error), and no file of
 *     `src/` outside `src/api/` imports `ids.ts`, `origin.ts`, `provenance.ts` or `route.ts`. The
 *     reading of a box's text for a request is `idAt`'s (MA3 of round 3): `textOf` is `decode.ts`'s,
 *     `provenance.ts`'s and (for the page alone, D430) `numbers.ts`'s, whose glyphs are `format.tsx`'s
 *     alone, `requestId` and `originOf` `provenance.ts`'s, and `idAt` `curator.ts`'s;
 *     (g) holds that a member added to the operator client that reads a box's digits (the review's
 *     X1-X3) is a lint error by its import, whatever the member count is. The document seam's
 *     internals (D432: `choiceFor`, `choiceText`, `ofTable`, `walkedAs`, `DATATYPES`, `isCountOf` and the three
 *     functions that send a document) are `document.ts`'s alone, and the index re-exports none.
 * (e) **The launder corpus** (L1-L4, n01-n04, n34 of the plan's reviews): each route is a compile
 *     error or a lint error.
 * (f) **`FEEDS` is `classes.json`'s `from`** (m16): the same four, the same feeds.
 * (g) **One function reads a box's text** (MA3 of round 3): the real `curator.ts` imports none of
 *     `textOf`, `requestId` and `originOf` and calls `idAt` four times; each of the review's X1-X3
 *     (and an alias, a maker, an origin reader) added to it passes the type checker and is a lint
 *     error by its import; a dynamic import in `src/api/` is a lint error; `idAt` takes a position's
 *     name, so a member that hands it a feed of its own (round 4's Y1) does not compile.
 * (h) **`provenance.ts` is held exactly** (m14 of round 4): its exports are `FEEDS`, `idAt`,
 *     `nodesAt` and `recorder` (and their types), its imports are the ones listed, and `textOf`,
 *     `requestId` and `originOf` are each called once, inside `idAt` (a wrapper exported beside it,
 *     round 4's P1, fails). (d) names every spelling a module is imported by (`./box`, `./box.ts`,
 *     `./box.js`: m15) and holds `textOf`'s owners (`decode.ts`, `provenance.ts`, `numbers.ts`) over
 *     every file of `src/api/`, `.tsx` included (m17; D430).
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import * as provenance from "../../src/api/provenance";
import { FEEDS } from "../../src/api/provenance";
import { Linter } from "./linter";
import { appOptions, check, type Checked, WEB } from "./program";
import { BUDGET, entries, PINS, receivers, ROOT_MODULES, surface } from "./surface";
import { takes } from "./takers";

const eslint = new Linter({
  cwd: WEB,
  overrideConfig: {
    languageOptions: { parserOptions: { projectService: { allowDefaultProject: ["src/*.tsx", "src/probe.ts", "src/api/probe.ts", "src/operator/probe.tsx"], defaultProject: "tsconfig.app.json" } } },
  },
});

/** The rule ids that flag `code` as the file `file`, with no fatal message. */
async function lintIds(code: string, file: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.map((message) => message.ruleId ?? "inline-configuration");
}

// ---------------------------------------------------------------------------------------------
// The walk.

/** The exports of a module of the program by its project-relative name. */
function exportsOf(checked: Checked, file: string): ts.Symbol[] {
  const symbol = checked.checker.getSymbolAtLocation(checked.source(file));
  if (symbol === undefined) {
    throw new Error(`no module ${file}`);
  }
  return checked.checker.getExportsOfModule(symbol);
}

/** The four flows, pinned whole: each one parameter, a decoded node, and `Promise<Outcome>` (the
 * surface gate pins the node; this pins the arity, so that no handle or dataset stands beside it). */
export const PINNED: Readonly<Record<string, string>> = {
  takeOver: "(session: JsonOut) => Promise<Outcome>",
  rejectProposal: "(proposal: JsonOut) => Promise<Outcome>",
  acceptProposal: "(proposal: JsonOut) => Promise<Outcome>",
  queueAt: "(label: JsonOut) => Promise<Outcome>",
};

/** What is wrong with the operator client's surface (gate (a)): the allow-list's problems
 * (`surface.ts`), each pinned flow that is not exactly its pin, and each receiver no type declares
 * in the implementation; with how many members the client has and the roots walked. */
function surfaceProblems(checked: Checked): { problems: string[]; members: number; roots: readonly string[]; inputs: number } {
  const { checker } = checked;
  const walked = surface(checked.program, checker);
  const problems = walked.problems.map(({ at, why }) => `${at}: ${why}`);
  const client = exportsOf(checked, "src/api/operator.ts").find((symbol) => symbol.name === "operator");
  if (client === undefined) {
    throw new Error("no operator");
  }
  const type = checker.getTypeOfSymbol(client);
  for (const [name, pin] of Object.entries(PINNED)) {
    const property = type.getProperty(name);
    const member = property === undefined ? undefined : checker.getTypeOfSymbol(property);
    const found = member === undefined ? [] : [...member.getCallSignatures(), ...member.getConstructSignatures()].map((signature) => checker.signatureToString(signature, undefined, ts.TypeFormatFlags.NoTruncation, ts.SignatureKind.Call));
    if (found.length !== 1 || found[0] !== pin.replace(" => ", ": ")) {
      problems.push(`${name}: ${property === undefined ? "missing" : found.join(" | ")}`);
    }
  }
  problems.push(...receivers(checked.program).map((at) => `${at}: a receiver no type declares`));
  return { problems, members: walked.members, roots: walked.roots, inputs: walked.inputs.length };
}

const CURATOR = "src/api/curator.ts";
const CURATOR_TEXT = readFileSync(path.join(WEB, CURATOR), "utf8");
const PROBE = { "src/probe-surface.ts": "export * as operator from './api/operator';\nexport * as oracle from './api/oracle';\n" };
const LAST = "  readonly queueAt: (label: JsonOut) => Promise<Outcome>;\n";
/** `curator.ts`'s import of the box, whatever it names (a mutant adds a name to it). */
const BOX_IMPORT = /^import \{[^}]*\} from "\.\/box";$/mu;

/** `curator.ts` with members added to `Curator` after `queueAt` (and the box's type imported). */
function withMembers(members: readonly string[]): string {
  expect(CURATOR_TEXT).toContain(LAST);
  expect(CURATOR_TEXT).toMatch(BOX_IMPORT);
  return CURATOR_TEXT.replace(BOX_IMPORT, 'import { isServerNumber, type ServerNumber } from "./box";').replace(LAST, `${LAST}${members.map((member) => `  ${member}\n`).join("")}`);
}

let real: Checked;

beforeAll(() => {
  real = check({ ...PROBE, "src/probe-real.ts": "export * from './api';\n" });
}, 120_000);

/** The members a lazy hand might add, each a way a box reaches the client's body: the round-2
 * review's N1-N14, and more of the class (N15-N35). Each is found by the allow-list at its own
 * place, whatever the member count says. */
const MUTANTS: readonly (readonly [string, string, string])[] = [
  ["N1 Readonly<{ value: unknown }>", "readonly n1: (node: Readonly<{ value: unknown }>) => string;", "operator.n1(#0)"],
  ["N2 Record<\"value\", unknown>", 'readonly n2: (node: Record<"value", unknown>) => string;', "operator.n2(#0)"],
  ["N3 NonNullable<T>", "readonly n3: <T>(node: NonNullable<T>) => string;", "operator.n3"],
  ["N4 AbortSignal, its reason", "readonly n4: (signal: AbortSignal) => string;", "operator.n4(#0)"],
  ["N5 Error, its cause", "readonly n5: (error: Error) => string;", "operator.n5(#0)"],
  ["N6 a nested member", "readonly n6: { peek(node: unknown): string };", "operator.n6.peek"],
  ["N7 a member returning a taker", "readonly n7: () => (node: unknown) => string;", "operator.n7()(#0)"],
  ["N8 a this parameter", "readonly n8: (this: unknown) => string;", "operator.n8(this)"],
  ["N9 a conditional type", "readonly n9: <T>(node: T extends number ? never : T) => string;", "operator.n9"],
  ["N10 Partial<{ value: unknown }>", "readonly n10: (node: Partial<{ value: unknown }>) => string;", "operator.n10(#0)"],
  ["N11 PromiseRejectedResult, its reason", "readonly n11: (result: PromiseRejectedResult) => string;", "operator.n11(#0)"],
  ["N12 T & object", "readonly n12: <T>(node: T & object) => string;", "operator.n12"],
  ["N13 a getter giving a taker", "get n13(): (node: unknown) => string;", "operator.n13(#0)"],
  ["N14 an object of strings (Object.values reads a box the caller added)", "readonly n14: (node: { readonly name: string }) => string;", "operator.n14(#0)"],
  ["N15 a writable member", "n15: unknown;", "operator.n15="],
  ["N16 a setter", "get n16(): string;\n  set n16(value: unknown);", "operator.n16="],
  ["N17 a member typed as a pinned flow", 'readonly n17: Curator["takeOver"];', "operator.n17(#0)"],
  ["N18 a callback's return", "readonly n18: (read: () => unknown) => string;", "operator.n18(#0)"],
  ["N19 a rest parameter", "readonly n19: (...nodes: unknown[]) => string;", "operator.n19(#0)[0]"],
  ["N20 a second parameter (R4)", "readonly n20: (label: string, node: unknown) => string;", "operator.n20(#1)"],
  ["N21 a construct signature (S3)", "readonly n21: new (node: unknown) => { readonly text: string };", "operator.n21.new(#0)"],
  ["N22 an overload", "readonly n22: { (label: string): string; (node: unknown): string };", "operator.n22[1](#0)"],
  ["N23 a method, even of strings (a method is writable)", "n23(label: string): string;", "operator.n23="],
  ["N24 AbortSignal at a place no pin names", "readonly n24: (cancel?: AbortSignal) => string;", "operator.n24(#0)"],
  ["N25 a function in a Promise", "readonly n25: () => Promise<(node: unknown) => string>;", "operator.n25()<>(#0)"],
  ["N26 a member giving unknown (an output the walk cannot see through)", "readonly n26: () => unknown;", "operator.n26()"],
  ["N27 a member giving a mutable list (its caller can push into it)", "readonly n27: () => { readonly v: string }[];", "operator.n27()[]="],
  ["N28 a member giving a mutable tuple", "readonly n28: () => [{ readonly v: string }];", "operator.n28()[0]="],
  ["N29 an output a function is assignable to ({ readonly length: number }, round 4's S1b)", "readonly n29: () => { readonly length: number };", "operator.n29()"],
  ["N30 an output a function is assignable to ({ readonly name: string })", "readonly n30: () => { readonly name: string };", "operator.n30()"],
  ["N31 an output a function is assignable to ({ readonly toString: () => string })", "readonly n31: () => { readonly toString: () => string };", "operator.n31()"],
  ["N32 a member a function is assignable to", "readonly n32: { readonly length: number };", "operator.n32"],
  ["N33 a list of them", "readonly n33: () => readonly { readonly length: number }[];", "operator.n33()[]"],
  ["N34 Record<never, string>, which every object is assignable to", "readonly n34: () => Record<never, string>;", "operator.n34()"],
  ["N35 a union with one", "readonly n35: () => { readonly length: number } | string;", "operator.n35()"],
  ["a box", "readonly leak: (n: ServerNumber) => string;", "operator.leak(#0)"],
  ["a node", "readonly peek: (node: JsonOut) => string;", "operator.peek(#0)"],
  ["unknown", "readonly g1: (node: unknown) => string;", "operator.g1(#0)"],
  ["object", "readonly g2: (node: object) => string;", "operator.g2(#0)"],
  ["Readonly<Record<string, unknown>>", "readonly g3: (node: Readonly<Record<string, unknown>>) => string;", "operator.g3(#0)"],
  ["readonly unknown[]", "readonly g4: (node: readonly unknown[]) => string;", "operator.g4(#0)"],
  ["{ readonly held: unknown }", "readonly g5: (node: { readonly held: unknown }) => string;", "operator.g5(#0)"],
  ["a type parameter", "readonly g6: <T>(node: T) => string;", "operator.g6"],
];

describe("(a) the operator client's surface: an allow-list over every place it is handed a value (n2, m7, MA2)", () => {
  it("holds on the real client: every root walked, every member seen, every pin met", () => {
    const { problems, members, roots, inputs } = surfaceProblems(real);
    expect(problems).toEqual([]);
    expect(members).toBe(18);
    // `exercise` (D426): the harness's oracle requests, a root with no parameter; `corpus` (D428): the
    // injection corpus as boxes the walk made, a root with no parameter whose boxes are leaves.
    expect(roots).toEqual(["IDLE_LIMIT_MS", "NAME_LIMIT", "operator", "exercise", "corpus", "decodedCount", "createCurator"]);
    expect(inputs).toBe(21);
    expect(Object.keys(PINS).sort()).toEqual([
      "createCurator(#0)",
      "createCurator(#1)",
      "operator.acceptProposal(#0)",
      "operator.change(#1)",
      "operator.install(#0)",
      "operator.queueAt(#0)",
      "operator.rejectProposal(#0)",
      "operator.subscribe(#0)",
      "operator.takeOver(#0)",
      "operator.unlock(#2)",
    ]);
  });

  it("the roots are the client's entries: every module of src/api/ a file outside it imports is the index (api-graph.test.ts) or a root", () => {
    const parsed = ts.getParsedCommandLineOfConfigFile(path.join(WEB, "tsconfig.app.json"), {}, { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => undefined });
    if (parsed === undefined) {
      throw new Error("tsconfig.app.json is unreadable");
    }
    const program = ts.createProgram(parsed.fileNames, appOptions());
    expect(entries(program, program.getTypeChecker())).toEqual(["src/api/index.ts", ...ROOT_MODULES].sort());
  });

  it.each([
    ["an extra handle parameter on acceptProposal", "  readonly acceptProposal: (proposal: JsonOut) => Promise<Outcome>;", "  readonly acceptProposal: (proposal: JsonOut, handle?: string) => Promise<Outcome>;"],
    ["an extra dataset parameter on takeOver", "  readonly takeOver: (session: JsonOut) => Promise<Outcome>;", "  readonly takeOver: (session: JsonOut, dataset?: string) => Promise<Outcome>;"],
    ["queueAt returning the answer type of exchange", "  readonly queueAt: (label: JsonOut) => Promise<Outcome>;", "  readonly queueAt: (label: JsonOut) => Promise<Outcome | string>;"],
    ["a pinned type that changes its shape (Targets gains a member)", "  readonly document: Listens;\n", "  readonly document: Listens;\n  readonly held?: unknown;\n"],
    ["a pin's place taking another type (unlock's cancel an object)", "  readonly unlock: (token: string, name: string, cancel?: AbortSignal) => Promise<Unlocked>;", "  readonly unlock: (token: string, name: string, cancel?: AbortSignal | { readonly held: unknown }) => Promise<Unlocked>;"],
  ])("fails for %s (the control)", (_case, from, to) => {
    expect(CURATOR_TEXT).toContain(from);
    const checked = check({ ...PROBE, [CURATOR]: CURATOR_TEXT.replace(from, to) });
    expect(surfaceProblems(checked).problems.length).toBeGreaterThan(0);
  });

  it("fails for a pin no input meets (unlock without its cancel): the table of pins is the client's", () => {
    const from = "  readonly unlock: (token: string, name: string, cancel?: AbortSignal) => Promise<Unlocked>;";
    expect(CURATOR_TEXT).toContain(from);
    const checked = check({ ...PROBE, [CURATOR]: CURATOR_TEXT.replace(from, "  readonly unlock: (token: string, name: string) => Promise<Unlocked>;") });
    expect(surfaceProblems(checked).problems).toEqual(["operator.unlock(#2): a pin no input meets"]);
  });

  describe("each member a lazy hand adds that a box reaches is refused at its own place, whatever the member count (MA2's mutants as controls)", () => {
    let mutated: string[];
    beforeAll(() => {
      mutated = surfaceProblems(check({ ...PROBE, [CURATOR]: withMembers(MUTANTS.map(([, member]) => member)) })).problems;
    }, 120_000);

    it.each(MUTANTS)("%s", (_case, _member, place) => {
      expect(mutated.filter((problem) => problem.startsWith(place))).not.toEqual([]);
    });

    it("refuses nothing of the real client beside them", () => {
      const members = MUTANTS.map(([, , place]) => `operator.${place.slice("operator.".length).split(/[.([=]/u)[0] ?? ""}`);
      const own = (problem: string): boolean => members.some((member) => problem.startsWith(member) && !/^[0-9A-Za-z_]/u.test(problem.slice(member.length)));
      expect(mutated.filter((problem) => !own(problem))).toEqual([]);
      expect(members.length).toBe(MUTANTS.length);
    });
  });

  it("refuses a receiver no type declares in the implementation: a this or an arguments in curator.ts (the control)", () => {
    const from = "    async queueAt(label) {";
    expect(CURATOR_TEXT).toContain(from);
    const checked = check({ ...PROBE, [CURATOR]: withMembers(["readonly peek: () => string;"]).replace(from, `    peek() {\n      return isServerNumber(this) || arguments.length > 0 ? "box" : "";\n    },\n${from}`) });
    expect(surfaceProblems(checked).problems.filter((problem) => problem.endsWith("a receiver no type declares")).length).toBe(2);
    expect(receivers(real.program)).toEqual([]);
  });

  it("a walk that exceeds its budget of outputs fails with a problem, never a crash (the budget's control)", () => {
    const walked = surface(real.program, real.checker, PINS, 5);
    expect(walked.problems.map(({ why }) => why)).toContain("the walk exceeded its budget of outputs");
    expect(walked.problems.filter(({ why }) => why.includes("budget"))).toHaveLength(1);
    expect(surface(real.program, real.checker).visits).toBeLessThan(BUDGET / 10);
  });

  it("refuses a receiver in operator.ts too: a this and an arguments there are found (RT5)", () => {
    const original = readFileSync(path.join(WEB, "src/api/operator.ts"), "utf8");
    const checked = check({ ...PROBE, "src/api/operator.ts": `${original}\nexport function peek(): number {\n  return arguments.length;\n}\nexport const own = {\n  m() {\n    return this;\n  },\n};\n` });
    expect(receivers(checked.program).filter((at) => at.startsWith("src/api/operator.ts:"))).toHaveLength(2);
    expect(receivers(checked.program).filter((at) => at.startsWith("src/api/curator.ts:"))).toEqual([]);
  });

  it.each([
    ["an unlock with another AbortSignal after the pinned one (PW2')", "  readonly unlock: (token: string, name: string, cancel?: AbortSignal) => Promise<Unlocked>;", "  readonly unlock: (token: string, name: string, cancel?: AbortSignal, again?: AbortSignal) => Promise<Unlocked>;", "operator.unlock(#3)"],
    ["a change with another list of edits after the pinned one (PW2')", "  readonly change: (dataset: string, edits: readonly Edit[]) => Promise<Outcome>;", "  readonly change: (dataset: string, edits: readonly Edit[], more: readonly Edit[]) => Promise<Outcome>;", "operator.change(#2)"],
    ["a subscribe with another listener after the pinned one (PW2')", "  readonly subscribe: (listener: () => void) => () => void;", "  readonly subscribe: (listener: () => void, again: () => void) => () => void;", "operator.subscribe(#1)"],
  ])("a pin holds at its place alone, within a pinned member too: %s", (_case, from, to, place) => {
    expect(CURATOR_TEXT).toContain(from);
    const problems = surfaceProblems(check({ ...PROBE, [CURATOR]: CURATOR_TEXT.replace(from, to) })).problems;
    expect(problems).toHaveLength(1);
    expect(problems[0]?.startsWith(`${place}: takes `), problems[0]).toBe(true);
  });

  it("an entry is found in every form a file outside src/api/ can reach a module of it: a dynamic import and a re-export (RT3, RT4)", () => {
    const checked = check({
      "src/probe-entry-dynamic.ts": 'export const f = async () => import("./api/curator");\n',
      "src/probe-entry-barrel.ts": 'export * from "./api/box";\n',
      "src/probe-entry-named.ts": 'export { decode } from "./api/decode";\n',
      "src/probe-entry-static.ts": 'import { send } from "./api/client";\nexport const f = send;\n',
    });
    expect(entries(checked.program, checked.checker)).toEqual(["src/api/box.ts", "src/api/client.ts", "src/api/curator.ts", "src/api/decode.ts"]);
    const only = (name: string, code: string) => {
      const one = check({ [`src/probe-entry-${name}.ts`]: code });
      return entries(one.program, one.checker);
    };
    expect(only("dynamic", 'export const f = async () => import("./api/curator");\n')).toEqual(["src/api/curator.ts"]);
    expect(only("barrel", 'export * from "./api/box";\n')).toEqual(["src/api/box.ts"]);
  });
});

// ---------------------------------------------------------------------------------------------

/** The generic functions the index exports, a namespace's included, by their names. */
function genericExports(checked: Checked, file: string): { name: string; signature: ts.Signature }[] {
  const { checker } = checked;
  const found: { name: string; signature: ts.Signature }[] = [];
  const inspect = (symbol: ts.Symbol, name: string): void => {
    const target = (symbol.flags & ts.SymbolFlags.Alias) !== 0 ? checker.getAliasedSymbol(symbol) : symbol;
    if ((target.flags & ts.SymbolFlags.Module) !== 0) {
      checker.getExportsOfModule(target).forEach((member) => {
        inspect(member, `${name}.${member.name}`);
      });
      return;
    }
    if ((target.flags & ts.SymbolFlags.Value) === 0) {
      return;
    }
    for (const signature of checker.getTypeOfSymbol(target).getCallSignatures()) {
      if ((signature.typeParameters?.length ?? 0) > 0) {
        found.push({ name, signature });
      }
    }
  };
  for (const symbol of exportsOf(checked, file)) {
    inspect(symbol, symbol.name);
  }
  return found;
}

/** The text of a probe that instantiates each generic at every member of its constraint, derived
 * from the checker: `{ "op": Parameters<typeof f<"op">> }` for each string literal of the union. */
function instantiations(checked: Checked, generics: readonly { name: string; signature: ts.Signature }[], from: string): string {
  const lines = [`import * as api from "${from}";`];
  generics.forEach(({ name, signature }, index) => {
    const [parameter, ...rest] = signature.typeParameters ?? [];
    const constraint = parameter?.getConstraint();
    if (parameter === undefined || rest.length > 0 || constraint === undefined) {
      throw new Error(`${name}: a generic the gate cannot instantiate (one type parameter, constrained)`);
    }
    const members = constraint.isUnion() ? constraint.types : [constraint];
    const literals = members.map((member) => {
      if (!member.isStringLiteral()) {
        throw new Error(`${name}: a constraint the gate cannot enumerate: ${checked.checker.typeToString(member)}`);
      }
      return JSON.stringify(member.value);
    });
    lines.push(`export type I${String(index)} = { ${literals.map((literal) => `${literal}: Parameters<typeof api.${name}<${literal}>>`).join("; ")} };`);
  });
  return `${lines.join("\n")}\n`;
}

/** The instantiations whose parameters can hold a `ServerNumber`. */
function instantiatedTakers(checked: Checked, file: string, count: number): string[] {
  const { checker } = checked;
  const taker = takes(checked.program, checker);
  const takers: string[] = [];
  for (const symbol of exportsOf(checked, file)) {
    if (!/^I[0-9]+$/u.test(symbol.name)) {
      continue;
    }
    const type = checker.getDeclaredTypeOfSymbol(symbol);
    for (const operation of checker.getPropertiesOfType(type)) {
      if (taker(checker.getTypeOfSymbol(operation))) {
        takers.push(`${symbol.name}.${operation.name}`);
      }
    }
  }
  expect(exportsOf(checked, file).filter((symbol) => /^I[0-9]+$/u.test(symbol.name)).length).toBe(count);
  return takers;
}

/** The index's generics whose type parameter is unconstrained, each pinned by its exact signature
 * (D426, `api-graph.test.ts`' `TEXT_PINS`): they give back the values of the map they are handed,
 * boxes and all, and turn nothing into anything; every other generic is instantiated. */
const PINNED_GENERICS: Readonly<Record<string, string>> = {
  entriesOf: "<V>(map: BoxedMap<V>): readonly (readonly [ServerText, V])[]",
  memberOf: "<V>(map: BoxedMap<V>, name: string): Member<V>",
};

describe("(b) every generic the index exports, instantiated at every operation, takes no ServerNumber (m7)", () => {
  it("holds for the real index (kindOf, at every vocabulary; the index exports no exchange, D426)", () => {
    const all = genericExports(real, "src/api/index.ts");
    const pinned = all.filter(({ name, signature }) => PINNED_GENERICS[name] === real.checker.signatureToString(signature, undefined, ts.TypeFormatFlags.NoTruncation, ts.SignatureKind.Call));
    expect(pinned.map(({ name }) => name).sort()).toEqual(Object.keys(PINNED_GENERICS).sort());
    const generics = all.filter((generic) => !pinned.includes(generic));
    expect(generics.map(({ name }) => name)).toEqual(["kindOf"]);
    const probe = instantiations(real, generics, "./api");
    const checked = check({ "src/probe-generics.ts": probe });
    expect(checked.codes("src/probe-generics.ts")).toEqual([]);
    expect(instantiatedTakers(checked, "src/probe-generics.ts", generics.length)).toEqual([]);
    expect(probe).toContain('"Severity": Parameters<typeof api.kindOf<"Severity">>');
  });

  it("fails for a generic that takes a box at one operation alone, instantiated at that operation alone; uninstantiated, the check fails closed on its conditional type (the control)", () => {
    const leaky = `export * from "./api";
import type { OperationId, ServerNumber } from "./api";
type Leaky<O extends OperationId> = O extends "queue" ? [n: ServerNumber] : [];
export function leak<O extends OperationId>(_operation: O, ..._given: Leaky<O>): void {}
`;
    const index = check({ "src/probe-leaky.ts": leaky });
    const generics = genericExports(index, "src/probe-leaky.ts").filter(({ name }) => name === "leak");
    expect(generics.length).toBe(1);
    const [only] = generics;
    const uninstantiated = only?.signature.getParameters().some((parameter) => takes(index.program, index.checker)(index.checker.getTypeOfSymbol(parameter)));
    expect(uninstantiated).toBe(true);
    const checked = check({ "src/probe-leaky.ts": leaky, "src/probe-generics.ts": instantiations(index, generics, "./probe-leaky") });
    expect(instantiatedTakers(checked, "src/probe-generics.ts", 1)).toEqual(["I0.queue"]);
  });
});

// ---------------------------------------------------------------------------------------------

/** The `operations` interface of a checked program's generated file. */
function operationsOf(checked: Checked): ts.Type {
  const found = exportsOf(checked, "src/api/generated/openapi.ts").find((member) => member.name === "operations");
  if (found === undefined) {
    throw new Error("no operations");
  }
  return checked.checker.getDeclaredTypeOfSymbol(found);
}

/** Where `RequestId` stands on the request side of every operation, by the names on the way. */
function requestIds(checked: Checked): string[] {
  const { checker } = checked;
  const found: string[] = [];
  const visit = (type: ts.Type, at: string, seen: Set<ts.Type>): void => {
    if (type.aliasSymbol?.name === "RequestId") {
      found.push(at);
      return;
    }
    if (type.isUnionOrIntersection()) {
      type.types.forEach((member) => {
        visit(member, at, seen);
      });
      return;
    }
    if ((type.flags & ts.TypeFlags.Object) === 0 || seen.has(type)) {
      return;
    }
    seen.add(type);
    if (checker.isArrayType(type) || checker.isTupleType(type)) {
      checker.getTypeArguments(type as ts.TypeReference).forEach((member) => {
        visit(member, `${at}[]`, seen);
      });
      return;
    }
    for (const symbol of checker.getPropertiesOfType(type)) {
      visit(checker.getTypeOfSymbol(symbol), `${at}.${symbol.name}`, seen);
    }
  };
  for (const operation of checker.getPropertiesOfType(operationsOf(checked))) {
    const type = checker.getTypeOfSymbol(operation);
    for (const side of ["requestBody", "parameters", "responses"]) {
      const member = type.getProperty(side);
      if (member !== undefined) {
        visit(checker.getTypeOfSymbol(member), `${operation.name}.${side}`, new Set());
      }
    }
  }
  return [...new Set(found)].sort();
}

// `exchange` below is a DECLARED function of the probe, over the generated `RequestBody<O>`: no real
// export of `src/api/` is left to type the request positions through (the index exports no
// `exchange`, D426), and the class under test is the request body's type, not any function's body.
const HEAD = `import { routes, type components, type OperationId, type RequestBody, type ServerNumber } from "./api";
declare function exchange<O extends OperationId>(route: { readonly operation: O }, body: RequestBody<O>): void;
declare const n: ServerNumber;
type S = components["schemas"];
declare const queue: S["CurationQueue"];
`;

/** The launder routes and the plain numbers at the four positions: each a compile error. */
const COMPILE: readonly (readonly [string, string, readonly number[]])[] = [
  ["n34: a plain number at path:proposal", `export const f = () => routes.reject({ dataset: "d", proposal: 1 });`, [2322]],
  ["n34: a plain number at query:release", `export const f = () => routes.queue({ dataset: "d" }, { release: 1 });`, [2322]],
  ["n34: a plain number at TakeOver.session", `export const f = () => exchange(routes.take_over({ dataset: "d" }), { session: 1, expected: "x" });`, [2322]],
  ["n34: a plain number at Accept.proposal", `export const f = () => exchange(routes.change({ dataset: "d" }), { handle: "h", expected: "e", edits: [{ op: "accept", proposal: 1 }] });`, [2322]],
  ["a box at path:proposal", `export const f = () => routes.reject({ dataset: "d", proposal: n });`, [2322]],
  ["a box at query:release (L2: a count through a request)", `export const f = () => routes.queue({ dataset: "d" }, { release: queue.truncated });`, [2322]],
  ["a box at TakeOver.session", `export const f = () => exchange(routes.take_over({ dataset: "d" }), { session: n, expected: "x" });`, [2322]],
  ["a proposal's box at Accept.proposal", `export const f = (p: S["QueueProposal"]) => exchange(routes.change({ dataset: "d" }), { handle: "h", expected: "e", edits: [{ op: "accept", proposal: p.id }] });`, [2322]],
  ["a literal shaped like the brand", `export const f = () => routes.reject({ dataset: "d", proposal: Object.freeze({ valueOf: () => 1 }) });`, [2322]],
  ["L1: a route's URL read back", `export const f = () => routes.dataset_state({ dataset: "d" }).url.split("/");`, [2339]],
  ["L3: a count at a tool's offset", `export const f = () => exchange(routes.describe_dataset(), { dataset: "d", columns_offset: queue.truncated });`, [2322]],
  ["L4: a maker of request numbers from the index", `import { userNumber } from "./api";\nexport const f = userNumber;`, [2305, 2724]],
  ["L4: RequestId from the index", `import type { RequestId } from "./api";\nexport type T = RequestId;`, [2305, 2724]],
  ["L4: requestId from the index", `import { requestId } from "./api";\nexport const f = requestId;`, [2305, 2724]],
];

describe("(c) the four positions are RequestId, and nothing but the grammar makes one (m6, n7)", () => {
  it("in the generated types, the four positions alone are RequestId", () => {
    expect(requestIds(real)).toEqual([
      "change.requestBody.content.application/json.edits[].proposal",
      "queue.parameters.query.release",
      "reject.parameters.path.proposal",
      "take_over.requestBody.content.application/json.session",
    ]);
  });

  let compiled: Checked;
  beforeAll(() => {
    compiled = check(Object.fromEntries(COMPILE.map(([, code], index) => [`src/probe-four-${String(index)}.ts`, `${HEAD}${code}\n`])));
  }, 120_000);

  it.each(COMPILE.map(([name, , codes], index) => [name, codes, index] as const))("%s: a compile error", (_name, codes, index) => {
    const found = compiled.codes(`src/probe-four-${String(index)}.ts`);
    expect(found.length).toBeGreaterThan(0);
    expect(found.some((code) => codes.includes(code))).toBe(true);
  });

  it("the control: the same calls compile with what the positions take elsewhere (a string, a JS number at a tool's offset)", () => {
    const checked = check({
      "src/probe-four-control.ts": `${HEAD}export const f = () => [routes.queue({ dataset: "d" }), routes.take_over({ dataset: "d" }), exchange(routes.describe_dataset(), { dataset: "d", columns_offset: 50 }), exchange(routes.take_over({ dataset: "d" }), { expected: "x" })];\n`,
    });
    expect(checked.codes("src/probe-four-control.ts")).toEqual([]);
  });
});

// ---------------------------------------------------------------------------------------------

describe("(c') what the brand does not stop: the lint refuses an any; ways of minting a RequestId without a cast exist, and are not closed", () => {
  // D425 (m1 of round 1, m7 of round 2, MA3 of round 3): the checker allows ways of making a
  // `RequestId` without `requestId` and without a cast, which the lint does not refuse either. These
  // are examples of the class (TypeScript's unsound variance and overload implementations), not a
  // closed list, and no lint rule is added for them. What holds is structural: a `RequestId` so
  // minted reaches only `routes`, whose route no module outside `src/api/` can send (the index
  // exports no `exchange`, D426; it carried no credentials, the server answering 401): the operator client has no place that takes one, its four flows taking nodes and building
  // the URL and the body inside `src/api/`. The same variance does reach a *box* to a primitive place
  // of the operator client (the last example below: round 3's V2, which showed the earlier wording,
  // "never the operator client", false for boxes), and that is harmless for one reason, held
  // elsewhere: no member's body can read a box's text, which `idAt` alone does, from a stamped node
  // (the lint rows of (d), the cells of (g)).
  it.each([
    [
      "an overload whose implementation returns unknown",
      "import { routes } from './api';\ntype Id = Parameters<typeof routes.reject>[0]['proposal'];\nfunction mint(value: number): Id;\nfunction mint(value: unknown): unknown {\n  return value;\n}\nexport const f = (): unknown => routes.reject({ dataset: 'd', proposal: mint(7) });\n",
    ],
    [
      "method-parameter bivariance",
      "import { routes } from './api';\ntype Id = NonNullable<NonNullable<Parameters<typeof routes.queue>[1]>['release']>;\nlet minted: Id | undefined;\nconst sink = {\n  put(value: Id): void {\n    minted = value;\n  },\n};\nconst loose: { put(value: number): void } = sink;\nloose.put(7);\nexport const f = (): unknown => (minted === undefined ? null : routes.queue({ dataset: 'd' }, { release: minted }));\n",
    ],
    [
      "array covariance",
      "import { routes } from './api';\ntype Id = Parameters<typeof routes.reject>[0]['proposal'];\nconst ids: Id[] = [];\nconst sink: number[] = ids;\nsink.push(7);\nexport const f = (): unknown[] => ids.map((proposal) => routes.reject({ dataset: 'd', proposal }));\n",
    ],
    [
      "mutable property covariance",
      "import { routes } from './api';\ntype Id = Parameters<typeof routes.reject>[0]['proposal'];\nconst held: { v?: Id } = {};\nconst loose: { v?: number } = held;\nloose.v = 7;\nexport const f = (): unknown => (held.v === undefined ? null : routes.reject({ dataset: 'd', proposal: held.v }));\n",
    ],
    [
      "a library method's bivariance (Map.set)",
      "import { routes } from './api';\ntype Id = NonNullable<NonNullable<Parameters<typeof routes.queue>[1]>['release']>;\nconst ids = new Map<string, Id>();\nconst loose: Map<string, number> = ids;\nloose.set('a', 2);\nconst release = ids.get('a');\nexport const f = (): unknown => (release === undefined ? null : routes.queue({ dataset: 'd' }, { release }));\n",
    ],
  ])("an example of the class, %s, mints a RequestId without a cast: tsc and the lint pass", async (_case, code) => {
    expect(check({ "src/probe-residual.ts": code }).codes("src/probe-residual.ts")).toEqual([]);
    expect(await lintIds(code, "src/probe.tsx")).toEqual([]);
  });

  it.each([
    [
      "method-parameter bivariance reaches a box to a primitive place of the operator client",
      "import { operator } from '../api/operator';\nimport type { ServerNumber } from '../api';\ndeclare const b: ServerNumber;\nconst loose: { read(label: string | ServerNumber): boolean } = { read: operator.holds };\nexport const f = (): boolean => loose.read(b);\n",
    ],
    [
      "array covariance reaches a box to a primitive place of the operator client",
      "import { operator } from '../api/operator';\nimport type { ServerNumber } from '../api';\ndeclare const b: ServerNumber;\nconst labels: string[] = [];\nconst loose: (string | ServerNumber)[] = labels;\nloose.push(b);\nexport const f = (): boolean => operator.holds(labels[0] ?? '');\n",
    ],
  ])("the same class reaches a box to the operator client's primitive places, and tsc and the lint pass: %s (harmless: no body reads a box's text, (g))", async (_case, code) => {
    expect(check({ "src/operator/probe-residual.ts": code }).codes("src/operator/probe-residual.ts")).toEqual([]);
    expect(await lintIds(code, "src/operator/probe.tsx")).toEqual([]);
  });

  it("an any at a request id is refused by the lint (the brand's residual: any is assignable to it)", async () => {
    const code = "import { routes } from './api';\nexport const f = (o: object) => routes.reject({ dataset: 'd', proposal: Reflect.get(o, 'k') });\n";
    const found = await lintIds(code, "src/probe.tsx");
    expect(found).toContain("@typescript-eslint/no-unsafe-assignment");
  });
});

// ---------------------------------------------------------------------------------------------

describe("(d) each internal of the request half is its owner's alone (n5, m16)", () => {
  it.each([
    ["curator.ts, stamp (it reads origins, never makes one)", "import { stamp } from './origin';\nexport const f = stamp;\n", "src/api/curator.ts", true],
    ["provenance.ts, stamp", "import { stamp } from './origin';\nexport const f = stamp;\n", "src/api/provenance.ts", false],
    ["a module of src/api/, stamp", "import { stamp } from './origin';\nexport const f = stamp;\n", "src/api/probe.ts", true],
    ["client.ts, stamp", "import { stamp } from './origin';\nexport const f = stamp;\n", "src/api/client.ts", true],
    ["curator.ts, originOf (MA3: the one reader of a node's origin is idAt)", "import { originOf } from './origin';\nexport const f = originOf;\n", "src/api/curator.ts", true],
    ["provenance.ts, originOf", "import { originOf } from './origin';\nexport const f = originOf;\n", "src/api/provenance.ts", false],
    ["client.ts, originOf", "import { originOf } from './origin';\nexport const f = originOf;\n", "src/api/client.ts", true],
    ["a module of src/api/, originOf", "import { originOf } from './origin';\nexport const f = originOf;\n", "src/api/probe.ts", true],
    ["client.ts, recorder", "import { recorder } from './provenance';\nexport const f = recorder;\n", "src/api/client.ts", false],
    ["curator.ts, recorder", "import { recorder } from './provenance';\nexport const f = recorder;\n", "src/api/curator.ts", true],
    ["curator.ts, idAt (the four flows' one reader of a box's text)", "import { idAt } from './provenance';\nexport const f = idAt;\n", "src/api/curator.ts", false],
    ["client.ts, idAt", "import { idAt } from './provenance';\nexport const f = idAt;\n", "src/api/client.ts", true],
    ["decode.ts, idAt", "import { idAt } from './provenance';\nexport const f = idAt;\n", "src/api/decode.ts", true],
    ["operator.ts, idAt", "import { idAt } from './provenance';\nexport const f = idAt;\n", "src/api/operator.ts", true],
    ["a module of src/api/, idAt", "import { idAt } from './provenance';\nexport const f = idAt;\n", "src/api/probe.ts", true],
    ["curator.ts, requestId (MA3: the grammar's one maker is idAt's)", "import { requestId } from './ids';\nexport const f = requestId;\n", "src/api/curator.ts", true],
    ["provenance.ts, requestId", "import { requestId } from './ids';\nexport const f = requestId;\n", "src/api/provenance.ts", false],
    ["client.ts, requestId", "import { requestId } from './ids';\nexport const f = requestId;\n", "src/api/client.ts", true],
    ["a module of src/api/, requestId", "import { requestId } from './ids';\nexport const f = requestId;\n", "src/api/probe.ts", true],
    ["curator.ts, textOf (MA3: no member of the operator client reads a box's text)", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/curator.ts", true],
    ["provenance.ts, textOf (idAt)", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/provenance.ts", false],
    ["decode.ts, textOf (as on main)", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/decode.ts", false],
    ["client.ts, textOf", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/client.ts", true],
    ["operator.ts, textOf", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/operator.ts", true],
    ["a module of src/api/, textOf", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/probe.ts", true],
    ["curator.ts, box (it never makes one)", "import { box } from './box';\nexport const f = box;\n", "src/api/curator.ts", true],
    ["curator.ts, isServerNumber (the brand check gives nothing of a number)", "import { isServerNumber } from './box';\nexport const f = isServerNumber;\n", "src/api/curator.ts", false],
    ["client.ts, urlOf", "import { urlOf } from './route';\nexport const f = urlOf;\n", "src/api/client.ts", false],
    ["curator.ts, urlOf", "import { urlOf } from './route';\nexport const f = urlOf;\n", "src/api/curator.ts", true],
    ["curator.ts, datasetOf", "import { datasetOf } from './route';\nexport const f = datasetOf;\n", "src/api/curator.ts", true],
    ["the generated routes, urlOf", "import { urlOf } from '../route';\nexport const f = urlOf;\n", "src/api/generated/routes.ts", true],
    ["curator.ts, textOf renamed (an alias)", "import { textOf as digits } from './box';\nexport const f = digits;\n", "src/api/curator.ts", true],
    ["curator.ts, textOf with a type import beside it", "import { isServerNumber, textOf, type ServerNumber } from './box';\nexport const f = [isServerNumber, textOf, (n: ServerNumber) => n];\n", "src/api/curator.ts", true],
    ["curator.ts, the box as a namespace", "import * as B from './box';\nexport const f = B;\n", "src/api/curator.ts", true],
    ["curator.ts, textOf re-exported", "export { textOf } from './box';\n", "src/api/curator.ts", true],
    ["curator.ts, everything of the box re-exported", "export * from './box';\n", "src/api/curator.ts", true],
    ["curator.ts, the ids' module as a namespace", "import * as I from './ids';\nexport const f = I;\n", "src/api/curator.ts", true],
    ["curator.ts, the origin module as a namespace", "import * as O from './origin';\nexport const f = O;\n", "src/api/curator.ts", true],
    ["curator.ts, the RequestId type alone (a type gives no text)", "import type { RequestId } from './ids';\nexport type T = RequestId;\n", "src/api/curator.ts", false],
    ["index.ts, validateDocument re-exported (the raw document doors leave the index, D432)", "export { validateDocument } from './typed';\n", "src/api/index.ts", true],
    ["index.ts, countCohort and runAnalysis re-exported (D432)", "export { countCohort, runAnalysis } from './typed';\n", "src/api/index.ts", true],
    ["index.ts, choiceFor re-exported (a choice is minted by optionsOf alone, D432)", "export { choiceFor } from './text';\n", "src/api/index.ts", true],
    ["oracle.ts, validateDocument (D432)", "import { validateDocument } from './typed';\nexport const f = validateDocument;\n", "src/api/oracle.ts", true],
    ["document.ts, validateDocument (D432)", "import { validateDocument } from './typed';\nexport const f = validateDocument;\n", "src/api/document.ts", false],
    ["format.tsx, choiceText (D432)", "import { choiceText } from './text';\nexport const f = choiceText;\n", "src/api/format.tsx", true],
  ])("%s: refused %s", async (_case, code, file, refused) => {
    const found = await lintIds(code, file);
    expect(found.includes("@typescript-eslint/no-restricted-imports")).toBe(refused);
  });

  /** Every spelling the resolver maps a specifier to a module by (`moduleResolution: bundler` reads
   * `./box.js` as `./box.ts`): none, `.ts` and `.js` (m15 of round 4: the pattern matched the first
   * two alone, so `./box.js` passed every row, as it did on main), and `.tsx` and `.jsx` (m6 of the
   * typed answers' round 2: the lint's pattern is derived from `SRC_EXTENSIONS`). */
  const SPELLINGS = ["", ".ts", ".tsx", ".js", ".jsx"] as const;

  /** Each internal of the table: its module, a name, an owner (who may import it), and a file that may not. */
  const INTERNALS: readonly (readonly [string, string, string])[] = [
    ["box", "box", "src/api/decode.ts"],
    ["box", "textOf", "src/api/provenance.ts"],
    ["ids", "requestId", "src/api/provenance.ts"],
    ["origin", "stamp", "src/api/provenance.ts"],
    ["origin", "originOf", "src/api/provenance.ts"],
    ["provenance", "recorder", "src/api/client.ts"],
    ["provenance", "idAt", "src/api/curator.ts"],
    ["route", "urlOf", "src/api/client.ts"],
    ["route", "datasetOf", "src/api/client.ts"],
    ["decode", "decode", "src/api/client.ts"],
    ["decode", "revive", "src/api/client.ts"],
    ["client", "send", "src/api/curator.ts"],
    ["curator", "createCurator", "src/api/operator.ts"],
    ["box", "boxText", "src/api/typed.ts"],
    ["box", "boxMap", "src/api/typed.ts"],
    ["box", "readText", "src/api/text.tsx"],
    ["client", "exchange", "src/api/typed.ts"],
    ["numbers", "glyphsOf", "src/api/format.tsx"],
    ["numbers", "countGlyphs", "src/api/format.tsx"],
    ["numbers", "remainingOf", "src/api/format.tsx"],
    ["numbers", "isCountOf", "src/api/document.ts"],
    ["text", "choiceFor", "src/api/document.ts"],
    ["text", "choiceText", "src/api/document.ts"],
    ["text", "ofTable", "src/api/document.ts"],
    ["typed", "walkedAs", "src/api/document.ts"],
    ["typed", "validateDocument", "src/api/document.ts"],
    ["typed", "countCohort", "src/api/document.ts"],
    ["typed", "runAnalysis", "src/api/document.ts"],
    ["datatypes", "DATATYPES", "src/api/document.ts"],
  ];

  const importing = (module: string, name: string, spelling: string, specifier = "."): string => `import { ${name} } from '${specifier}/${module}${spelling}';\nexport const f = ${name};\n`;

  it.each(INTERNALS.flatMap(([module, name, owner]) => SPELLINGS.map((spelling) => [module, name, owner, spelling] as const)))(
    "%s's %s is refused to a module of src/api/ that does not own it, and allowed to %s, whichever the spelling (%j)",
    async (module, name, owner, spelling) => {
      expect(await lintIds(importing(module, name, spelling), "src/api/probe.ts")).toContain("@typescript-eslint/no-restricted-imports");
      expect(await lintIds(importing(module, name, spelling), owner)).not.toContain("@typescript-eslint/no-restricted-imports");
    },
  );

  it.each(SPELLINGS)("a spelling of the specifier does not hide an internal: an alias, a namespace and a re-export, spelled %j", async (spelling) => {
    expect(await lintIds(`import { textOf as digits } from './box${spelling}';\nexport const f = digits;\n`, "src/api/curator.ts")).toContain("@typescript-eslint/no-restricted-imports");
    expect(await lintIds(`import * as B from './box${spelling}';\nexport const f = B;\n`, "src/api/curator.ts")).toContain("@typescript-eslint/no-restricted-imports");
    expect(await lintIds(`export { textOf } from './box${spelling}';\n`, "src/api/curator.ts")).toContain("@typescript-eslint/no-restricted-imports");
    expect(await lintIds(`import { requestId } from '../api/ids${spelling}';\nexport const f = requestId;\n`, "src/api/curator.ts")).toContain("@typescript-eslint/no-restricted-imports");
  });

  /** The modules of `src/api/` (`generated/` included), by project-relative name. */
  const API_FILES = [...readdirSync(path.join(WEB, "src/api")).filter((name) => name.endsWith(".ts") || name.endsWith(".tsx")).map((name) => `src/api/${name}`), "src/api/generated/routes.ts"];

  it("textOf's owners are decode.ts, provenance.ts and numbers.ts alone (D430), over every file of src/api/, whichever the spelling: the same owners the import-graph test states (m17 of round 4)", async () => {
    expect(API_FILES.length).toBeGreaterThanOrEqual(12);
    expect(API_FILES).toContain("src/api/format.tsx");
    const owners = ["src/api/decode.ts", "src/api/provenance.ts", "src/api/numbers.ts"];
    for (const file of API_FILES.filter((one) => one !== "src/api/box.ts")) {
      for (const spelling of SPELLINGS) {
        const specifier = file.startsWith("src/api/generated/") ? ".." : ".";
        const refused = (await lintIds(importing("box", "textOf", spelling, specifier), file)).includes("@typescript-eslint/no-restricted-imports");
        expect(refused, `${file} ${spelling}`).toBe(!owners.includes(file));
      }
    }
  });

  it.each(["ids", "origin", "provenance", "route"])("the operator entry may not import api/%s", async (module) => {
    const found = await lintIds(`import * as m from '../api/${module}';\nexport const f = m;\n`, "src/operator/probe.tsx");
    expect(found).toContain("@typescript-eslint/no-restricted-imports");
  });
});

// ---------------------------------------------------------------------------------------------

/** `curator.ts` with `textOf` (or another reader of a box's text) imported, a member added to
 * `Curator`, and its implementation: the member a lazy hand adds (MA3 of round 3). */
function reading(imports: string, declaration: string, implementation: string): string {
  const at = "    async queueAt(label) {";
  expect(CURATOR_TEXT).toContain(at);
  return withMembers([declaration])
    .replace('import { isServerNumber, type ServerNumber } from "./box";', imports)
    .replace(at, `${implementation}\n${at}`);
}

const FIRST_ID = `      const outcome = await read(() => routes.queue({ dataset }, {}));
      const list = outcome.kind === "answer" ? member(outcome.body, "proposals") : undefined;
      const id = member(isList(list) ? list[0] : undefined, "id");
`;

/** The review's X1-X3 (MA3): each a member whose body reads a box's digits and hands them out, valid
 * to the type checker and to every gate of the surface: the lint alone refuses them, by the import. */
const READERS: readonly (readonly [string, string])[] = [
  [
    "X1 an output of digits, taking a primitive",
    reading(
      'import { isServerNumber, textOf, type ServerNumber } from "./box";',
      "readonly firstProposalId: (dataset: string) => Promise<string>;",
      `    async firstProposalId(dataset) {\n${FIRST_ID}      return isServerNumber(id) ? textOf(id) : "";\n    },`,
    ),
  ],
  [
    "X2 a primitive place narrowed to a box (variance reaches it)",
    reading('import { isServerNumber, textOf, type ServerNumber } from "./box";', "readonly labelText: (label: string) => string;", "    labelText: (label) => (isServerNumber(label) ? textOf(label) : label),"),
  ],
  [
    "X3 digits in an Outcome's body",
    reading(
      'import { isServerNumber, textOf, type ServerNumber } from "./box";',
      "readonly peekQueue: (dataset: string) => Promise<Outcome>;",
      `    async peekQueue(dataset) {\n${FIRST_ID}      return { kind: "answer", status: 200, body: isServerNumber(id) ? textOf(id) : null };\n    },`,
    ),
  ],
  [
    "X1' the same through an alias of textOf",
    reading(
      'import { isServerNumber, textOf as digitsOf, type ServerNumber } from "./box";',
      "readonly firstProposalId: (dataset: string) => Promise<string>;",
      `    async firstProposalId(dataset) {\n${FIRST_ID}      return isServerNumber(id) ? digitsOf(id) : "";\n    },`,
    ),
  ],
  [
    "X4 an id made by the grammar's maker from digits another reader gave",
    reading(
      'import { isServerNumber, type ServerNumber } from "./box";\nimport { requestId } from "./ids";',
      "readonly mint: (label: string) => number | null;",
      "    mint: (label) => requestId(label),",
    ),
  ],
  [
    "X5 a node's origin read in a body",
    reading(
      'import { isServerNumber, type ServerNumber } from "./box";\nimport { originOf } from "./origin";',
      "readonly wherefrom: (label: string) => boolean;",
      "    wherefrom: (label) => originOf(label) !== null,",
    ),
  ],
];

describe("(i) D430: numbers.ts and format.tsx, which read and show a box's text, are held to D419's number rules as the app is", () => {
  /** Each a conversion or a rounding a lazy hand might write where a number is shown. */
  const CONVERSIONS: readonly (readonly [string, string])[] = [
    ["Math.round", "export const f = (n: number): number => Math.round(n);\n"],
    ["Math.floor through an alias", "const M = Math;\nexport const f = (n: number): number => M.floor(n);\n"],
    ["Number", "export const f = (t: string): number => Number(t);\n"],
    ["Number(t).toFixed", "export const f = (t: string): string => Number(t).toFixed(2);\n"],
    ["parseFloat", "export const f = (t: string): number => parseFloat(t);\n"],
    ["parseInt", "export const f = (t: string): number => parseInt(t, 10);\n"],
    ["String", "export const f = (n: number): string => String(n);\n"],
    ["unary plus", "export const f = (t: string): number => +t;\n"],
    ["a bitwise or", "export const f = (n: number): number => n | 0;\n"],
    ["JSON.parse", "export const f = (t: string): unknown => JSON.parse(t);\n"],
    ["BigInt", "export const f = (t: string): bigint => BigInt(t);\n"],
    ["Intl.NumberFormat", 'export const f = (t: number): string => new Intl.NumberFormat("en-US", { maximumSignificantDigits: 6 }).format(t);\n'],
    ["toFixed", "export const f = (n: { toFixed: (digits: number) => string }): string => n.toFixed(2);\n"],
    ["toPrecision", "export const f = (n: { toPrecision: (digits: number) => string }): string => n.toPrecision(6);\n"],
    ["toExponential", "export const f = (n: { toExponential: (digits: number) => string }): string => n.toExponential(3);\n"],
    ["toLocaleString", "export const f = (n: { toLocaleString: () => string }): string => n.toLocaleString();\n"],
  ];
  const NUMBER_RULES = ["no-restricted-globals", "no-restricted-syntax", "no-bitwise"];

  it.each(["src/api/numbers.ts", "src/api/format.tsx"].flatMap((file) => CONVERSIONS.map(([name, code]) => [file, name, code] as const)))("%s: %s is refused", async (file, _name, code) => {
    const found = await lintIds(code, file);
    expect(found.filter((id) => NUMBER_RULES.includes(id)).length).toBeGreaterThan(0);
  });

  it("another module of src/api/ keeps its numbers (the control: the rules are these two files', not the seam's)", async () => {
    for (const [, code] of CONVERSIONS.filter(([name]) => name !== "a bitwise or")) {
      expect((await lintIds(code, "src/api/probe.ts")).filter((id) => NUMBER_RULES.includes(id))).toEqual([]);
    }
  });
});

describe("(g) one function reads a box's text, and no member of the operator client's closure can (MA3 of round 3)", () => {
  it("the real curator.ts passes the lint as src/api/curator.ts: the controls below differ from it by what they import", async () => {
    expect(await lintIds(CURATOR_TEXT, CURATOR)).toEqual([]);
  });

  it("curator.ts names no reader of a box's text, no maker of a request id and no reader of an origin; idAt is its four flows' call", () => {
    const source = ts.createSourceFile(CURATOR, CURATOR_TEXT, ts.ScriptTarget.ES2023, true);
    const names: string[] = [];
    const visit = (node: ts.Node): void => {
      if (ts.isIdentifier(node)) {
        names.push(node.text);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
    for (const name of ["textOf", "requestId", "originOf", "isRequestIdText", "stamp"]) {
      expect(names.filter((one) => one === name), name).toEqual([]);
    }
    expect(names.filter((one) => one === "idAt").length).toBe(5);
  });

  it.each(READERS)("%s: the members pass tsc, and the lint refuses the import (not the member count)", async (_case, text) => {
    expect(check({ ...PROBE, [CURATOR]: text }).codes(CURATOR)).toEqual([]);
    const found = await lintIds(text, CURATOR);
    expect(found).toContain("@typescript-eslint/no-restricted-imports");
  });

  /** A member that hands `idAt` a feed of its own (round 4's Y1: a spread of a `FEEDS` row with another
   * member to read returned a proposal's `value` as an id); `idAt` takes a position's name. */
  const hands = (feed: string): string =>
    reading(
      'import { isServerNumber, type ServerNumber } from "./box";',
      "readonly firstValue: (dataset: string) => Promise<string>;",
      `    async firstValue(dataset) {\n      const outcome = await read(() => routes.queue({ dataset }, {}));\n      const list = outcome.kind === "answer" ? member(outcome.body, "proposals") : undefined;\n      const found = idAt(isList(list) ? (list[0] ?? null) : null, ${feed}, era);\n      return typeof found === "string" ? "" : String(found.id);\n    },`,
    ).replace('import { idAt } from "./provenance";', 'import { FEEDS, idAt } from "./provenance";');

  it.each([
    ["Y1 a spread of a feed with another member to read", '{ ...FEEDS["path:proposal"], read: "value" }'],
    ["Y1b the table's own feed, not its name", 'FEEDS["path:proposal"]'],
    ["Y1c a literal feed", '{ operation: "queue", path: "proposals[]", read: "value" }'],
    ["Y1d a name no feed has", '"value"'],
  ])("%s: a member that hands idAt a feed does not compile (idAt takes a position's name)", (_case, feed) => {
    const found = check({ ...PROBE, [CURATOR]: hands(feed) }).codes(CURATOR);
    expect(found.some((code) => code === 2345 || code === 2353 || code === 2322), found.join(",")).toBe(true);
  });

  it("the control: the same member handing idAt a position's name compiles, and passes the lint", async () => {
    const text = hands('"path:proposal"');
    expect(check({ ...PROBE, [CURATOR]: text }).codes(CURATOR)).toEqual([]);
    expect((await lintIds(text, CURATOR)).filter((id) => id === "@typescript-eslint/no-restricted-imports")).toEqual([]);
  });

  it("a dynamic import in src/api/ is refused: no body reaches textOf past the static import rule", async () => {
    const found = await lintIds("export const f = async () => (await import('./box')).textOf;\n", "src/api/curator.ts");
    expect(found).toContain("no-restricted-syntax");
    expect(await lintIds("export const f = async () => (await import('./box')).isServerNumber;\n", "src/api/probe.ts")).toContain("no-restricted-syntax");
    expect(await lintIds("export const f = async () => (await import('./box')).isServerNumber;\n", "src/api/client.ts")).toContain("no-restricted-syntax");
  });
});

// ---------------------------------------------------------------------------------------------

const PROVENANCE = "src/api/provenance.ts";
const PROVENANCE_TEXT = readFileSync(path.join(WEB, PROVENANCE), "utf8");

/** What `provenance.ts` (a text) exports and imports, and where it calls each reader of a box: the
 * names it exports, the imports it makes (`module:name`), and for each of `textOf`, `requestId` and
 * `originOf` the functions its identifiers stand in, apart from the import (`<call in idAt>` for a
 * call, `<other in f>` for any other use). */
function held(text: string): { exports: string[]; imports: string[]; uses: Record<string, string[]> } {
  const source = ts.createSourceFile(PROVENANCE, text, ts.ScriptTarget.ES2023, true);
  const exported: string[] = [];
  const imports: string[] = [];
  for (const statement of source.statements) {
    if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier)) {
      const from = statement.moduleSpecifier.text;
      const clause = statement.importClause;
      if (clause?.name !== undefined) {
        imports.push(`${from}:default`);
      }
      const bindings = clause?.namedBindings;
      if (bindings !== undefined && ts.isNamespaceImport(bindings)) {
        imports.push(`${from}:*`);
      } else if (bindings !== undefined) {
        for (const element of bindings.elements) {
          imports.push(`${from}:${element.propertyName?.text ?? element.name.text}`);
        }
      }
      if (clause === undefined) {
        imports.push(`${from}:`);
      }
    }
    if (ts.isExportDeclaration(statement)) {
      exported.push(statement.exportClause === undefined ? "*" : ts.isNamedExports(statement.exportClause) ? statement.exportClause.elements.map((element) => element.name.text).join(",") : statement.exportClause.name.text);
    }
    if (ts.isExportAssignment(statement)) {
      exported.push("default");
    }
    if (ts.canHaveModifiers(statement) && ts.getModifiers(statement)?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword) === true) {
      if (ts.isVariableStatement(statement)) {
        for (const declaration of statement.declarationList.declarations) {
          exported.push(declaration.name.getText(source));
        }
      } else if (ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement) || ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement) || ts.isEnumDeclaration(statement)) {
        exported.push(statement.name?.text ?? "default");
      }
    }
  }
  const uses: Record<string, string[]> = { textOf: [], requestId: [], originOf: [] };
  const visit = (node: ts.Node, inside: string): void => {
    const here = ts.isFunctionDeclaration(node) && node.name !== undefined ? node.name.text : inside;
    const listed = ts.isIdentifier(node) ? uses[node.text] : undefined;
    if (listed !== undefined && ts.isIdentifier(node) && !ts.isImportSpecifier(node.parent)) {
      listed.push(`${ts.isCallExpression(node.parent) && node.parent.expression === node ? "<call" : "<other"} in ${here}>`);
    }
    ts.forEachChild(node, (child) => {
      visit(child, here);
    });
  };
  visit(source, "<module>");
  return { exports: exported.sort(), imports: imports.sort(), uses };
}

const HELD = {
  exports: ["FEEDS", "Fed", "Feed", "FeedName", "Refusal", "Tag", "idAt", "nodesAt", "recorder"],
  imports: ["./box:isServerNumber", "./box:textOf", "./ids:RequestId", "./ids:requestId", "./json:JsonOut", "./origin:originOf", "./origin:stamp", "./route:OperationId"],
  uses: { textOf: ["<call in idAt>"], requestId: ["<call in idAt>"], originOf: ["<call in idAt>"] },
};

describe("(h) provenance.ts is held exactly: its exports, its imports and its one call of each reader of a box (m14 of round 4)", () => {
  it("holds on the real module", () => {
    expect(held(PROVENANCE_TEXT)).toEqual(HELD);
  });

  it("its run-time exports are FEEDS, idAt, nodesAt and recorder, and no others", () => {
    expect(Object.keys(provenance).sort()).toEqual(["FEEDS", "idAt", "nodesAt", "recorder"]);
  });

  const BOX_IMPORT_LINE = 'import { isServerNumber, textOf } from "./box";';

  it.each([
    ["P1 a text helper exported beside idAt", PROVENANCE_TEXT.replace("export type Refusal =", "export function textAt(number: Parameters<typeof textOf>[0]): string {\n  return textOf(number);\n}\n\nexport type Refusal ="), "textAt"],
    ["P1b the same, not exported (a local helper idAt does not call)", PROVENANCE_TEXT.replace("export type Refusal =", "function textAt(number: Parameters<typeof textOf>[0]): string {\n  return textOf(number);\n}\n\nexport type Refusal ="), "textAt"],
    ["P1c textOf re-exported", `${PROVENANCE_TEXT}\nexport { textOf };\n`, "textOf"],
    ["P1d textOf re-exported from the box", `${PROVENANCE_TEXT}\nexport { textOf as digits } from "./box";\n`, "digits"],
    ["P1e requestId exported as a constant", `${PROVENANCE_TEXT}\nexport const make = requestId;\n`, "make"],
    ["P1f originOf exported through a wrapper", `${PROVENANCE_TEXT}\nexport const wherefrom = (node: JsonOut) => originOf(node);\n`, "wherefrom"],
    ["P1g a second call of textOf in idAt", PROVENANCE_TEXT.replace("const id = requestId(textOf(found));", "const id = requestId(textOf(found));\n  void textOf(found);"), "textOf"],
    ["P1h a namespace import of the box", PROVENANCE_TEXT.replace(BOX_IMPORT_LINE, `${BOX_IMPORT_LINE}\nimport * as everything from "./box";\nexport const all = everything;`), "all"],
  ])("%s: held() differs from the pins", (_case, text, what) => {
    expect(text).not.toBe(PROVENANCE_TEXT);
    expect(held(text), what).not.toEqual(HELD);
  });
});

// ---------------------------------------------------------------------------------------------

describe("(e) the launder routes into the four flows are compile or lint errors", () => {
  const OPERATOR = "import { operator } from '../api/operator';\nimport type { JsonOut } from '../api';\ndeclare const node: JsonOut;\n";

  it.each([
    ["a dataset beside the node (cross-dataset, m2)", "export const f = () => operator.takeOver(node, 'other');", [2554]],
    ["a handle beside the node (MB1)", "export const f = () => operator.acceptProposal(node, 'ses_x');", [2554]],
    ["a number for the node", "export const f = () => operator.queueAt(2 as unknown as JsonOut);", []],
  ] as const)("%s", async (_case, code, codes) => {
    const checked = check({ "src/operator/probe-launder.tsx": `${OPERATOR}${code}\n` });
    const found = checked.codes("src/operator/probe-launder.tsx");
    if (codes.length > 0) {
      expect(found.some((one) => (codes as readonly number[]).includes(one))).toBe(true);
    } else {
      expect(await lintIds(`${OPERATOR}${code}\n`, "src/operator/probe.tsx")).toContain("@typescript-eslint/consistent-type-assertions");
    }
  });
});

// ---------------------------------------------------------------------------------------------

describe("(f) FEEDS is classes.json's from (m16)", () => {
  const classes = JSON.parse(readFileSync(path.join(WEB, "src/api/classes.json"), "utf8")) as { adapter_fed: { from: Record<string, unknown> } };

  it("the four positions with a feed, and the same feeds", () => {
    const fed = Object.fromEntries(Object.entries(classes.adapter_fed.from).filter(([, feed]) => feed !== null));
    expect(fed).toEqual(FEEDS);
  });
});
