/**
 * The display table's gate (D427): `src/display/rows.ts` tied to the code, and "built and tested"
 * read from a fresh run of the tests, never from a claim of the table.
 *
 * **The gate claims what it can check and no more.** It makes three claims, each a check of the
 * table, of the files on disk or of the AST of one test file, each with fixtures
 * (`display-fixtures.ts`) that assert which rule fails; everything else is a named residual (the end
 * of this comment, and D427), for the review of each row's pull request, for 2b-1L's type-aware
 * lint and for the build's gate. It has no model of module resolution of its own: which modules
 * ship is read from the bundler's module graph by `plugins/gate.ts` (f), and the only specifiers it
 * resolves are the relative ones of a row's own test file (to say that the file imports the row's
 * module, where a miss only refuses).
 *
 * **1. The table is one source, complete and ordered.**
 * - `shape`: the table loads (`scripts/display-table.mjs`, which also holds Appendix C equal to it
 *   and the ids to `scripts/display-ids.json`, in `display.test.ts`) and its ids are unique and
 *   well formed.
 * - `owner-enum`: every row's owner is one of `OWNERS`, every owner has a row, none is listed twice.
 *   `server` is the server's owner: its rows name no module and no test (`server-row`).
 * - `no-module`, `no-screens`: a row not the server's names its module, its test file and a screen.
 * - **`owner-complete`**: a row is *built* when its module file exists; once any row of an owner is
 *   built, all of its rows must be, so a row cannot be "built inside another module" and escape.
 *   Two owners' rows that share a module fall due together, in the first owner's pull request.
 * - **`owner-order`**: `OWNERS` is the landing order; a built row of an owner, the last included,
 *   fails while an owner before it has a row not built (2b-1L's lint lands before M5.2 renders data).
 *
 * **2. A built row has a passing test of its own.**
 * - `empty-module`: a built row's module has a value export statement of its own (a file that is no
 *   code is not blank). `export {}`, a side-effect import, `export type` and `export declare` alone
 *   are refused. **An `export *` and an `export namespace` count as values**, so a types-only module
 *   re-exported by `export *`, or a type-only namespace, passes: a named residual, like a module that
 *   exports a value and does nothing.
 * - **`no-test`**: each built row needs a test that *passed* in the gate's own run, titled
 *   `<id>: …` (the id matched exactly), in the row's `test` file, which imports the row's module
 *   directly, by a value import (not `import type`, not an inline `{ type X }`, not dynamic, not
 *   through a barrel) with a relative specifier; the result's line **and column** are those of a
 *   literal call in that file whose callee is exactly `it` or `test`, bound by a named import of
 *   itself from `"vitest"` that no variable, function, class, import or enum of the file declares
 *   again (a parameter of that name in an unrelated function is not counted, and no function around
 *   the call has a parameter), whose title is a string
 *   literal (or a template with no substitution), with exactly two arguments (no options, which
 *   may say `fails`), the second an inline function with **no parameter** and a block body whose
 *   **first statement** is `expect.hasAssertions()` (`expect` bound from `"vitest"` alike). The
 *   call sits at the file's top level or only inside plain `describe(literal, fn)` calls
 *   (`describe` bound alike, exactly two arguments, `fn` with no parameter): no `.each`, `.skip`,
 *   `.only`, `.todo`, `.fails`, `.runIf`, `.skipIf`, `.concurrent`, no options, no other function
 *   around it. **The row's own test file names none of the hooks (`beforeEach`, `afterEach`,
 *   `beforeAll`, `afterAll`, `onTestFinished`, `onTestFailed`, `aroundEach`, `aroundAll`), `ctx` or
 *   `task`** (as an identifier or a property name, and `task` also as a string literal; no other name
 *   is matched as a string): a name check of that file alone, which is not a claim that a test of
 *   the row's own file cannot be inverted (the residuals below).
 *   Refused though real: `it.each`, `test.for`, an alias (`const t = it`), a computed title, a test
 *   in a `describe.each`. A title with no literal call is refused, so a loop over the rows, or a
 *   table read at run time, gives no row a test.
 * - `orphan-title`: a title that starts `R<digit>` and names no row, in the run's results or as a
 *   literal title in a code file of `tests/` (symlinked directories followed, once each).
 * - **`canary`** and **`run`**: "passed" is read from a fresh child run of vitest's `unit` project
 *   that the gate makes itself (`--project unit`, `--includeTaskLocation` set by the gate, the JSON
 *   written to a new temporary directory, `--no-cache`, `VITEST*` and `NODE_OPTIONS` of the parent
 *   dropped from its environment), never from a results file an earlier run left behind; its
 *   arguments are fixed (`--changed` or `--shard` would run fewer tests, so rows would fail: they
 *   fail safe); the child's stderr and stdout tail is in the failure. It runs the built rows' test
 *   files and the canaries (`tests/unit/d427-canary.test.ts`, whose text the gate pins by its
 *   SHA-256, `CANARY_SHA256`): a `test.fails` whose body is `expect.hasAssertions()` alone must pass
 *   (a setup file that neutered `hasAssertions` would make it fail), and a literal test must pass,
 *   reported at its call's own line and column (a setup file that inverted every test would fail it).
 *
 * **3. The gate's own tests** are fixtures (mock packages, each asserting the rules it fails by),
 * the generated owner-order cells, and the mutants of this file (`MUTANTS`): switches that the
 * tests turn on one at a time, each killed by a named fixture (`KILLS`, one table, which the pull
 * request's table is generated from) or shown equivalent with its reason (`EQUIVALENT`).
 *
 * **What the gate does not see (residuals, for the review of each row's pull request, whose per-row
 * mutation removes the obligation and sees the row's test fail):** a test that asserts something
 * unrelated to the row or an empty-in-effect module (one that exports a value and does nothing), a
 * test that `vi.mock`s the row's module or its dependencies, `expect.soft`, a test file that
 * neuters `expect.hasAssertions` for itself; **a test inverted or rewritten by code the gate does
 * not read as such, in any file, the row's own test file included**: in the row's own file, a hook
 * or the test's context reached by a string key (`(v as any)["beforeEach"]`, `["onTestFailed"]`),
 * `Object.values` of the context, `arguments[0]` or a computed name (`["before" + "Each"]`), which
 * the name check does not see (fixtures s134-s137: each throws and is reported passed); in another
 * file, a hook, a helper or a computed name (a helper under `src/testing/`, `testing/` or `e2e/`;
 * only the row's own file is read at all, and only for names). A runner-side check cannot close
 * this: vitest sends the task to its reporters at collection, before any hook runs, so a runtime
 * `task.fails` or a rewritten result never reaches one. And tampering with the run's configuration
 * beyond what the canaries prove (a setup file that patches the runner only for titles that start
 * `R<digit>`); every claim about modules that ship or are reached (the build's gate, from the
 * bundler's graph; a module named as an exemption or a helper is reviewed, not checked; a built row's
 * module that no entry reaches; a lint that `eslint.config.mjs` does not wire); a test that imports
 * the table, or reads it as text (no literal title follows); an edit of the table that retires an
 * obligation (`scripts/display-ids.json` and `RETIRED` put it in review).
 */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import ts from "typescript";

import { readTable, TableError } from "../../scripts/display-table.mjs";
import type { Row, Table } from "../../scripts/display-table.mjs";

export const RULES = [
  "shape",
  "owner-enum",
  "server-row",
  "no-module",
  "no-screens",
  "owner-complete",
  "owner-order",
  "empty-module",
  "no-test",
  "orphan-title",
  "canary",
  "run",
] as const;

export type Rule = (typeof RULES)[number];

export type Failure = { readonly rule: Rule; readonly message: string };

/** The gate's mutants: each a check weakened or dropped, the ids those of the reviews (gaps are checks that left). */
export const MUTANTS = {
  M1: "a skipped or failed result counts as passed",
  M2: "the test file need not import the row's module",
  M3: "a type-only (or name-less) import of the row's module counts",
  M4: "expect.hasAssertions() need not be the first statement",
  M7: "orphan-title dropped",
  M8: "no-screens dropped",
  M9: "owner-enum dropped",
  M10: "a title matches a row by prefix",
  M11: "owner-complete dropped",
  M12: "the result's line is not checked",
  M13: "the callee need only start with it or test",
  M15: "a results file left in the tree is read instead of a fresh run",
  M16: "the result's column is not checked",
  M17: "it/test need not be bound from vitest",
  M19: "no-module dropped",
  M20: "canary dropped",
  M21: "owner-order dropped",
  M23: "expect need not be bound from vitest",
  M24: "the body may be the last of more than two arguments (options before it, which may say fails)",
  M25: "the test need not be in the row's test file",
  M28: "server-row dropped",
  M29: "shape (duplicate ids) dropped",
  M30: "NM01: hasAssertions may be called on any object",
  M31: "a parameter counts as a redeclaration of it, expect or describe (a correct test file with an unrelated parameter of that name is refused)",
  M32: "NM03: the declared-once half of the vitest binding dropped",
  M38: "NM11: orphan-title drops its literal scan",
  M39: "NM12: orphan-title drops its results scan",
  M40: "NM13: the report's freshness check dropped",
  M41: "NM14: a dynamic import counts as the test's import",
  M43: "NM18: a title matches without its colon",
  M44: "NM20: the canary's location is not checked",
  M45: "NM21: the fails canary dropped",
  M46: "NM25: VITEST* and NODE_OPTIONS are not filtered from the child's environment",
  M50: "NM38: no-module ignores a blank test",
  M51: "NM40: an owner listed twice is accepted",
  M52: "NM41: the id form is not checked",
  M55: "NM44: no-test admits any status but failed",
  M56: "a row's test callback may take a parameter",
  M57: "the describes around a row's test are not checked",
  M58: "hooks, ctx and task in the row's own test file are not checked",
  M59: "an enclosing call need only start with describe (describe.each, describe.skip)",
  M60: "an enclosing describe's arguments are not counted (options before the callback)",
  M72: "the canary file's text is not pinned",
  M75: "N03: onTestFailed is no runner name",
  M76: "N06: the string \"task\" is no runner name",
  M77: "N23: a runner name as a property (v.beforeEach, c.task) is none",
  M78: "N10: a vitest import under another name binds it, or any name of vitest",
  M79: "N11: a describe's title need not be a literal",
  M80: "N12: hasAssertions may be called with arguments",
  M81: "N29: owner-order skips the last owner of the order",
  M82: "empty-module dropped",
  M83: "a module with types alone counts as exporting a value",
  M84: "tests/ is walked without following symlinked directories",
  M85: "N04, N05: aroundEach and aroundAll are no runner names",
  M86: "X15: VITEST* of the parent is not dropped from the child's environment (NODE_OPTIONS still is)",
} as const;

export type Mutant = keyof typeof MUTANTS;

/** One result of the child run, its file relative to the root. */
export type TestResult = {
  readonly file: string;
  readonly title: string;
  readonly status: string;
  readonly line: number | null;
  readonly column: number | null;
};

export type GateOptions = {
  /** A mutant of the gate (its own tests alone pass one). */
  readonly mutant?: Mutant | null;
  /** The child run's results, given (the gate's own tests reuse one run per fixture to judge each mutant). */
  readonly results?: readonly TestResult[] | null;
};

export type Verdict = {
  readonly failures: readonly Failure[];
  /** The child run's results, or null where the gate stopped before the run. */
  readonly results: readonly TestResult[] | null;
  /** The child run's JSON as written, or null. */
  readonly raw: string | null;
};

/** The server's owner: its rows name no module and never fall due. */
export const SERVER = "server";

/** The canaries, run with every child run. */
export const CANARY_FILE = "tests/unit/d427-canary.test.ts";
export const CANARY_FAILS = "D427 canary: a test with no assertion fails under expect.hasAssertions()";
export const CANARY_PASSES = "D427 canary: a literal test passes, reported at its own line and column";
/** The canary file's text, pinned: change the file and this with it. */
export const CANARY_SHA256 = "d11555fa7ed529b20964bdb844c4cc529a9728520597d51749943ea84c915249";

/** The results file mutant M15 reads (the gate itself never does). */
export const STALE_RESULTS = ".d427-results.json";

/** The child run's time limit. */
const RUN_LIMIT_MS = 240_000;

/** The tail of the child's output kept for a failure. */
const TAIL_CHARS = 1500;

const ID = /^R\d+[a-z]?$/;
const TITLE = /^(R\d+[a-z]?):/;
const CODE = /\.(?:ts|tsx|mts|cts|js|jsx|mjs|cjs)$/;
const SKIPPED_DIRECTORIES = new Set(["node_modules", "dist", "dist-e2e", "playwright-report", "test-results"]);
/** What a row's own test file may not name: the hooks, and the test's context and task (which carry `fails` and the result). */
const RUNNER_NAMES = ["beforeEach", "afterEach", "beforeAll", "afterAll", "onTestFinished", "onTestFailed", "aroundEach", "aroundAll", "task", "ctx"];

/** A path relative to the root, with `/`. */
function relative(root: string, file: string): string {
  return path.relative(root, file).split(path.sep).join("/");
}

function isFile(file: string): boolean {
  return existsSync(file) && statSync(file).isFile();
}

/** Every code file under `directory`, following symlinks (a directory once on the way down: a link back to an ancestor is not followed again). */
function walk(directory: string, follow: boolean, above: ReadonlySet<string> = new Set()): string[] {
  if (!existsSync(directory)) {
    return [];
  }
  const here = realpathSync(directory);
  if (above.has(here)) {
    return [];
  }
  const within = new Set([...above, here]);
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name.startsWith(".") || SKIPPED_DIRECTORIES.has(entry.name)) {
      return [];
    }
    const full = path.join(directory, entry.name);
    const link = entry.isSymbolicLink();
    if (link && !follow) {
      return [];
    }
    let info;
    try {
      info = link ? statSync(full) : entry;
    } catch {
      return [];
    }
    return info.isDirectory() ? walk(full, follow, within) : CODE.test(entry.name) ? [full] : [];
  });
}

type Edge = {
  /** The file a relative specifier names, or null (a package, an alias, a miss). */
  readonly file: string | null;
  /** `import type` alone: erased, so no edge at run time. */
  readonly typeOnly: boolean;
  readonly kind: "static" | "dynamic";
  /** The names the edge takes by value: `*` for a namespace or a dynamic import; `default`; none for a side effect. */
  readonly names: readonly string[];
};

type Binding = { readonly name: string; readonly from: string; readonly imported: string; readonly typeOnly: boolean };

type Call = {
  readonly title: string;
  readonly line: number;
  readonly column: number;
  readonly callee: string;
  readonly exact: boolean;
  readonly bound: boolean;
  readonly argumentCount: number;
  /** Whether every function around the call is a plain `describe(literal, fn)` callback with no parameter. */
  readonly plain: boolean;
  /** Whether the second argument is an inline function with no parameter whose first statement is `expect.hasAssertions()`. */
  readonly assertsFirst: boolean;
  /** The same of the last argument (what mutant M24 reads). */
  readonly lastAssertsFirst: boolean;
  readonly expectBound: boolean;
};

type Parsed = {
  readonly edges: readonly Edge[];
  /** The literal `it`/`test`-like calls with a literal title. */
  readonly calls: readonly Call[];
  /** Whether the file names a hook, `ctx` or `task`. */
  readonly runnerNames: boolean;
};

type Off = (id: Mutant) => boolean;

/** The first existing file of a relative specifier: the path itself, `.ts`, `.tsx`, an index of either (Vite's order). */
function resolveRelative(from: string, specifier: string): string | null {
  if (!specifier.startsWith(".")) {
    return null;
  }
  const base = path.resolve(path.dirname(from), specifier);
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")]) {
    if (isFile(candidate)) {
      return candidate;
    }
  }
  return null;
}

function literalText(node: ts.Node | undefined): string | null {
  return node !== undefined && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) ? node.text : null;
}

function scriptKind(file: string): ts.ScriptKind {
  return file.endsWith("x") ? ts.ScriptKind.TSX : /\.[mc]?js$/.test(file) ? ts.ScriptKind.JS : ts.ScriptKind.TS;
}

/** The names a file declares, with how often (an import, a variable, a function...; a parameter only for mutant M31). */
function declarations(source: ts.SourceFile, parameters: boolean): Map<string, number> {
  const counts = new Map<string, number>();
  const count = (name: ts.Node | undefined): void => {
    if (name === undefined) {
      return;
    }
    if (ts.isIdentifier(name)) {
      counts.set(name.text, (counts.get(name.text) ?? 0) + 1);
    } else if (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name)) {
      for (const element of name.elements) {
        if (ts.isBindingElement(element)) {
          count(element.name);
        }
      }
    }
  };
  const visit = (node: ts.Node): void => {
    if (parameters && ts.isParameter(node)) {
      count(node.name);
    }
    if (
      ts.isVariableDeclaration(node) ||
      ts.isFunctionDeclaration(node) ||
      ts.isFunctionExpression(node) ||
      ts.isClassDeclaration(node) ||
      ts.isClassExpression(node) ||
      ts.isImportSpecifier(node) ||
      ts.isImportClause(node) ||
      ts.isNamespaceImport(node) ||
      ts.isImportEqualsDeclaration(node) ||
      ts.isEnumDeclaration(node)
    ) {
      count(node.name);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return counts;
}

/** Whether `name` is bound, once in the file, by a named import of itself from `"vitest"`. */
function boundFromVitest(bindings: readonly Binding[], counts: ReadonlyMap<string, number>, name: string, off: Off): boolean {
  return (
    (!off("M32") ? counts.get(name) === 1 : true) &&
    bindings.some((binding) => binding.name === name && binding.from === "vitest" && (off("M78") || binding.imported === name) && !binding.typeOnly)
  );
}

/** Whether `body` is an inline function (with no parameter) with a block body whose first statement is `expect.hasAssertions()`. */
function assertsFirst(body: ts.Expression | undefined, off: Off): boolean {
  if (body === undefined || !(ts.isArrowFunction(body) || ts.isFunctionExpression(body)) || !ts.isBlock(body.body)) {
    return false;
  }
  if (body.parameters.length > 0 && !off("M56")) {
    return false;
  }
  const first = body.body.statements[0];
  return (
    first !== undefined &&
    ts.isExpressionStatement(first) &&
    ts.isCallExpression(first.expression) &&
    (first.expression.arguments.length === 0 || off("M80")) &&
    ts.isPropertyAccessExpression(first.expression.expression) &&
    (off("M30") || (ts.isIdentifier(first.expression.expression.expression) && first.expression.expression.expression.text === "expect")) &&
    first.expression.expression.name.text === "hasAssertions"
  );
}

/** Whether every function around `node` is the callback of a plain `describe(literal, fn)`, with no parameter. */
function inPlainDescribes(node: ts.Node, source: ts.SourceFile, describeBound: boolean, off: Off): boolean {
  if (off("M57")) {
    return true;
  }
  for (let around: ts.Node = node.parent; !ts.isSourceFile(around); around = around.parent) {
    if (!ts.isFunctionLike(around)) {
      continue;
    }
    const call = around.parent;
    const callback = ts.isArrowFunction(around) || ts.isFunctionExpression(around);
    if (!callback || around.parameters.length > 0 || !ts.isCallExpression(call)) {
      return false;
    }
    const placed = off("M60") ? call.arguments.some((argument) => argument === around) : call.arguments.length === 2 && call.arguments[1] === around;
    const named = off("M59") ? /^describe\b/.test(call.expression.getText(source)) : ts.isIdentifier(call.expression) && call.expression.text === "describe" && describeBound;
    if (!placed || !named || (literalText(call.arguments[0]) === null && !off("M79"))) {
      return false;
    }
  }
  return true;
}

/** Whether `name` is a runner name the file may not hold (each mutant drops one). */
function runnerName(name: string, off: Off): boolean {
  if (name === "onTestFailed" && off("M75")) {
    return false;
  }
  if ((name === "aroundEach" || name === "aroundAll") && off("M85")) {
    return false;
  }
  return RUNNER_NAMES.includes(name);
}

function parseFile(file: string, off: Off): Parsed {
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, scriptKind(file));
  const edges: Edge[] = [];
  const bindings: Binding[] = [];
  const rawCalls: { node: ts.CallExpression; title: string }[] = [];
  let runnerNames = false;
  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) && runnerName(node.text, off) && !(off("M77") && ts.isPropertyAccessExpression(node.parent) && node.parent.name === node)) {
      runnerNames = true;
    }
    if (literalText(node) === "task" && !off("M76")) {
      runnerNames = true;
    }
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const clause = node.importClause;
      const clauseTypeOnly = clause?.phaseModifier === ts.SyntaxKind.TypeKeyword;
      const named = clause?.namedBindings !== undefined && ts.isNamedImports(clause.namedBindings) ? clause.namedBindings.elements : [];
      const names: string[] = [];
      if (clause !== undefined && !clauseTypeOnly) {
        if (clause.name !== undefined) {
          names.push("default");
        }
        if (clause.namedBindings !== undefined && ts.isNamespaceImport(clause.namedBindings)) {
          names.push("*");
        }
        for (const element of named) {
          if (!element.isTypeOnly) {
            names.push((element.propertyName ?? element.name).text);
          }
        }
      }
      // `import { type A } from "x"` is kept by `verbatimModuleSyntax` (it is `import {} from "x"`): no name by value.
      edges.push({ file: resolveRelative(file, node.moduleSpecifier.text), typeOnly: clauseTypeOnly, kind: "static", names });
      for (const element of named) {
        bindings.push({ name: element.name.text, from: node.moduleSpecifier.text, imported: (element.propertyName ?? element.name).text, typeOnly: clauseTypeOnly || element.isTypeOnly });
      }
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const specifier = literalText(node.arguments[0]);
      if (specifier !== null) {
        edges.push({ file: resolveRelative(file, specifier), typeOnly: false, kind: "dynamic", names: ["*"] });
      }
    } else if (ts.isCallExpression(node)) {
      const title = literalText(node.arguments[0]);
      if (title !== null && /^(?:it|test)\b/.test(node.expression.getText(source))) {
        rawCalls.push({ node, title });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  const counts = declarations(source, off("M31"));
  const itBound = boundFromVitest(bindings, counts, "it", off);
  const testBound = boundFromVitest(bindings, counts, "test", off);
  const expectBound = boundFromVitest(bindings, counts, "expect", off);
  const describeBound = boundFromVitest(bindings, counts, "describe", off);
  const calls = rawCalls.map(({ node, title }): Call => {
    const callee = node.expression.getText(source);
    // The identifier the callee starts from (`test` of `test.fails`), whose binding is checked.
    let head: ts.Expression = node.expression;
    while (ts.isPropertyAccessExpression(head) || ts.isCallExpression(head) || ts.isElementAccessExpression(head)) {
      head = head.expression;
    }
    const headName = ts.isIdentifier(head) ? head.text : "";
    // Where vitest reports a test: at the callee's last name (`fails` of `test.fails`), which for
    // `it` and `test` is the call's start.
    const named = ts.isPropertyAccessExpression(node.expression) ? node.expression.name : node.expression;
    const where = source.getLineAndCharacterOfPosition(named.getStart(source));
    return {
      title,
      line: where.line + 1,
      column: where.character + 1,
      callee,
      exact: callee === "it" || callee === "test",
      bound: (headName === "it" && itBound) || (headName === "test" && testBound),
      argumentCount: node.arguments.length,
      plain: inPlainDescribes(node, source, describeBound, off),
      assertsFirst: assertsFirst(node.arguments[1], off),
      lastAssertsFirst: assertsFirst(node.arguments[node.arguments.length - 1], off),
      expectBound,
    };
  });
  return { edges, calls, runnerNames };
}

/** Whether the file at `file` holds nothing a test could take: no value export (code), no text (anything else). */
function isEmptyModule(file: string, off: Off): boolean {
  const text = readFileSync(file, "utf8");
  if (!CODE.test(file)) {
    return text.trim() === "";
  }
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, scriptKind(file));
  return !source.statements.some((statement) => {
    if (ts.isExportAssignment(statement)) {
      return true;
    }
    if (ts.isExportDeclaration(statement)) {
      const clause = statement.exportClause;
      return !statement.isTypeOnly && (clause === undefined || ts.isNamespaceExport(clause) || clause.elements.some((element) => !element.isTypeOnly));
    }
    const modifiers = ts.canHaveModifiers(statement) ? (ts.getModifiers(statement) ?? []) : [];
    if (!modifiers.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword) || modifiers.some((modifier) => modifier.kind === ts.SyntaxKind.DeclareKeyword)) {
      return false;
    }
    return !(ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement)) || off("M83");
  });
}

/** The child run's results, from its JSON (null where it is not vitest's report). */
export function parseResults(root: string, json: string): TestResult[] | null {
  let report: unknown;
  try {
    report = JSON.parse(json);
  } catch {
    return null;
  }
  if (typeof report !== "object" || report === null || !("testResults" in report) || !Array.isArray(report.testResults)) {
    return null;
  }
  const results: TestResult[] = [];
  for (const file of report.testResults as unknown[]) {
    if (typeof file !== "object" || file === null || !("name" in file) || typeof file.name !== "string" || !("assertionResults" in file) || !Array.isArray(file.assertionResults)) {
      return null;
    }
    const name = relative(root, file.name);
    for (const result of file.assertionResults as unknown[]) {
      if (typeof result !== "object" || result === null || !("title" in result) || typeof result.title !== "string" || !("status" in result) || typeof result.status !== "string") {
        return null;
      }
      let line: number | null = null;
      let column: number | null = null;
      if ("location" in result && typeof result.location === "object" && result.location !== null) {
        const location = result.location as { line?: unknown; column?: unknown };
        line = typeof location.line === "number" ? location.line : null;
        column = typeof location.column === "number" ? location.column : null;
      }
      results.push({ file: name, title: result.title, status: result.status, line, column });
    }
  }
  return results;
}

type Run = { readonly results: TestResult[] | null; readonly raw: string | null; readonly problem: string | null };

/** The gate's own child run of vitest's `unit` project over `files`, its results read fresh. */
async function runUnit(root: string, files: readonly string[], off: Off): Promise<Run> {
  const vitest = path.join(root, "node_modules", "vitest", "vitest.mjs");
  if (!isFile(vitest)) {
    return { results: null, raw: null, problem: "vitest is not installed in the package" };
  }
  const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-d427-"));
  const output = path.join(scratch, "results.json");
  try {
    const environment = Object.fromEntries(Object.entries(process.env).filter(([name]) => (off("M46") || name !== "NODE_OPTIONS") && (off("M46") || off("M86") || !name.startsWith("VITEST"))));
    const started = Date.now();
    let tail = "";
    const keep = (chunk: unknown): void => {
      tail = (tail + String(chunk)).slice(-TAIL_CHARS);
    };
    const code = await new Promise<number | null>((resolve) => {
      // Its own process group, so that the limit stops vitest's workers with it.
      const child = spawn(
        process.execPath,
        [vitest, "run", "--root", root, "--project", "unit", "--reporter=json", `--outputFile=${output}`, "--includeTaskLocation", "--no-cache", ...files],
        { cwd: root, env: environment, stdio: ["ignore", "pipe", "pipe"], detached: true },
      );
      child.stdout.on("data", keep);
      child.stderr.on("data", keep);
      const timer = setTimeout(() => {
        try {
          process.kill(-(child.pid ?? 0), "SIGKILL");
        } catch {
          child.kill("SIGKILL");
        }
      }, RUN_LIMIT_MS);
      child.on("close", (exit) => {
        clearTimeout(timer);
        resolve(exit);
      });
      child.on("error", () => {
        clearTimeout(timer);
        resolve(null);
      });
    });
    const said = tail.trim() === "" ? "" : `; its output ended: ${tail.trim()}`;
    if (!isFile(output)) {
      return { results: null, raw: null, problem: `the child run wrote no results (exit ${String(code)})${said}` };
    }
    const raw = readFileSync(output, "utf8");
    const results = parseResults(root, raw);
    if (results === null) {
      return { results: null, raw, problem: `the child run's results are not vitest's JSON report${said}` };
    }
    const report = JSON.parse(raw) as { startTime?: unknown };
    if (!off("M40") && (typeof report.startTime !== "number" || report.startTime < started - 1000)) {
      return { results: null, raw, problem: `the child run's results are older than the run${said}` };
    }
    return { results, raw, problem: null };
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

/** Run the gate over the package at `root`. */
export async function gate(rootGiven: string, options: GateOptions = {}): Promise<Verdict> {
  const root = realpathSync(rootGiven);
  const mutant = options.mutant ?? null;
  const off: Off = (id) => mutant === id;
  const failures: Failure[] = [];
  const fail = (rule: Rule, message: string): void => {
    failures.push({ rule, message });
  };
  const done = (results: readonly TestResult[] | null, raw: string | null): Verdict => {
    const seen = new Set<string>();
    const unique = failures.filter((failure) => {
      const key = `${failure.rule}\u0000${failure.message}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
    unique.sort((a, b) => (a.rule === b.rule ? (a.message < b.message ? -1 : 1) : RULES.indexOf(a.rule) - RULES.indexOf(b.rule)));
    return { failures: unique, results, raw };
  };

  let table: Table;
  try {
    table = readTable(root);
  } catch (error) {
    if (error instanceof TableError) {
      fail("shape", error.message);
      return done(null, null);
    }
    throw error;
  }
  const rows = table.rows;
  const at = (file: string): string => path.resolve(root, file);

  // shape: ids unique and well formed.
  const ids = new Set<string>();
  for (const row of rows) {
    if (!ID.test(row.id) && !off("M52")) {
      fail("shape", `${row.id} is not an id of the form R<number>, with an optional letter`);
    }
    if (ids.has(row.id) && !off("M29")) {
      fail("shape", `${row.id} is listed twice`);
    }
    ids.add(row.id);
  }

  // owner-enum, server-row, no-module, no-screens.
  if (!off("M9")) {
    const listed = new Set<string>();
    for (const owner of table.owners) {
      if (listed.has(owner) && !off("M51")) {
        fail("owner-enum", `owner ${owner} is listed twice`);
      }
      listed.add(owner);
      if (!rows.some((row) => row.owner === owner)) {
        fail("owner-enum", `owner ${owner} has no row`);
      }
    }
    for (const row of rows) {
      if (!table.owners.includes(row.owner)) {
        fail("owner-enum", `${row.id}'s owner ${JSON.stringify(row.owner)} is not one of OWNERS`);
      }
    }
  }
  for (const row of rows) {
    if (row.owner === SERVER) {
      if ((row.module !== "" || row.test !== "") && !off("M28")) {
        fail("server-row", `${row.id} is the server's and names a module or a test`);
      }
      continue;
    }
    if ((row.module === "" || (row.test === "" && !off("M50"))) && !off("M19")) {
      fail("no-module", `${row.id} (owner ${row.owner}) names no module or no test file`);
    }
    if (row.screens.length === 0 && !off("M8")) {
      fail("no-screens", `${row.id} (owner ${row.owner}) binds no screen`);
    }
  }

  const built = (row: Row): boolean => row.owner !== SERVER && row.module !== "" && isFile(at(row.module));
  const owned = (owner: string): Row[] => rows.filter((row) => row.owner === owner && row.owner !== SERVER);

  // owner-complete.
  if (!off("M11")) {
    for (const owner of table.owners) {
      const own = owned(owner);
      if (own.some(built)) {
        for (const row of own.filter((candidate) => !built(candidate))) {
          fail("owner-complete", `owner ${owner} has built rows, and ${row.id}'s module ${row.module} is missing`);
        }
      }
    }
  }

  // owner-order.
  if (!off("M21")) {
    const all = table.owners.filter((owner) => owner !== SERVER);
    const order = off("M81") ? all.slice(0, -1) : all;
    order.forEach((owner, index) => {
      if (!owned(owner).some(built)) {
        return;
      }
      for (const before of order.slice(0, index)) {
        for (const row of owned(before).filter((candidate) => !built(candidate))) {
          fail("owner-order", `owner ${owner} has built rows while ${before}, which lands before it, has ${row.id} not built`);
        }
      }
    });
  }

  const parsed = new Map<string, Parsed>();
  const parsedOf = (file: string): Parsed => {
    let found = parsed.get(file);
    if (found === undefined) {
      found = parseFile(file, off);
      parsed.set(file, found);
    }
    return found;
  };

  // empty-module.
  const builtRows = rows.filter(built);
  if (!off("M82")) {
    for (const row of builtRows) {
      if (isEmptyModule(at(row.module), off)) {
        fail("empty-module", `${row.id}'s module ${row.module} is built and has no value export statement of its own (an empty module, or one with types alone)`);
      }
    }
  }

  // The child run.
  let results: readonly TestResult[] | null;
  let raw: string | null = null;
  const stale = at(STALE_RESULTS);
  if (off("M15") && isFile(stale)) {
    raw = readFileSync(stale, "utf8");
    results = parseResults(root, raw);
  } else if (options.results !== undefined && options.results !== null) {
    results = options.results;
  } else {
    const testFiles = [...new Set([...builtRows.map((row) => row.test).filter((test) => test !== ""), CANARY_FILE])];
    const run = await runUnit(root, testFiles, off);
    if (run.problem !== null) {
      fail("run", run.problem);
    }
    results = run.results;
    raw = run.raw;
  }
  if (results === null) {
    return done(null, raw);
  }

  // canary.
  if (!off("M20")) {
    const canaryFile = at(CANARY_FILE);
    if (!off("M72") && (!isFile(canaryFile) || createHash("sha256").update(readFileSync(canaryFile)).digest("hex") !== CANARY_SHA256)) {
      fail("canary", `${CANARY_FILE} is not the file the gate pins (its text is held by SHA-256): a canary edited or missing proves nothing`);
    }
    const fails = results.find((result) => result.file === CANARY_FILE && result.title === CANARY_FAILS);
    const passes = results.find((result) => result.file === CANARY_FILE && result.title === CANARY_PASSES);
    if (fails?.status !== "passed" && !off("M45")) {
      fail("canary", "the canary test.fails with expect.hasAssertions() alone did not pass: expect.hasAssertions is not what the gate relies on");
    }
    const call = isFile(canaryFile) ? parsedOf(canaryFile).calls.find((candidate) => candidate.title === CANARY_PASSES) : undefined;
    if (passes?.status !== "passed" || (!off("M44") && (passes.line !== call?.line || passes.column !== call.column))) {
      fail("canary", "the literal canary did not pass at its own line and column: the run's results or locations are not what the gate relies on");
    }
  }

  // no-test.
  for (const row of builtRows) {
    const module = at(row.module);
    const passed = results.filter((result) => {
      if (!off("M1") && (off("M55") ? result.status === "failed" : result.status !== "passed")) {
        return false;
      }
      if (off("M10")) {
        return result.title.startsWith(row.id);
      }
      return (off("M43") ? /^(R\d+[a-z]?)/ : TITLE).exec(result.title)?.[1] === row.id;
    });
    const held = passed.some((result) => {
      if (!off("M25") && result.file !== row.test) {
        return false;
      }
      const file = at(result.file);
      if (!isFile(file)) {
        return false;
      }
      const test = parsedOf(file);
      if (
        !off("M2") &&
        !test.edges.some((edge) => edge.file === module && (off("M41") || edge.kind === "static") && (off("M3") || (!edge.typeOnly && edge.names.length > 0)))
      ) {
        return false;
      }
      if (!off("M58") && test.runnerNames) {
        return false;
      }
      return test.calls.some(
        (call) =>
          call.title === result.title &&
          (off("M12") || call.line === result.line) &&
          (off("M16") || call.column === result.column) &&
          (off("M13") || call.exact) &&
          (off("M17") || call.bound) &&
          call.plain &&
          (off("M4") || ((off("M24") ? call.lastAssertsFirst : call.argumentCount === 2 && call.assertsFirst) && (off("M23") || call.expectBound))),
      );
    });
    if (!held) {
      fail("no-test", `${row.id}: ${row.module} is built, and ${row.test} holds no passed literal test titled "${row.id}: …" that imports it`);
    }
  }

  // orphan-title: in the results, and as literal titles in tests/.
  if (!off("M7")) {
    const orphan = (title: string): boolean => {
      if (!/^R\d/.test(title)) {
        return false;
      }
      const id = TITLE.exec(title)?.[1];
      return id === undefined || !ids.has(id);
    };
    for (const result of off("M39") ? [] : results) {
      if (orphan(result.title)) {
        fail("orphan-title", `${result.file}: the title ${JSON.stringify(result.title)} names no row`);
      }
    }
    for (const file of off("M38") ? [] : walk(at("tests"), !off("M84"))) {
      for (const call of parsedOf(file).calls) {
        if (orphan(call.title)) {
          fail("orphan-title", `${relative(root, file)}: the title ${JSON.stringify(call.title)} names no row`);
        }
      }
    }
  }

  return done(results, raw);
}
