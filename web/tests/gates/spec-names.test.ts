/**
 * The names SPEC.md's D430 text gives (MA-1 of #121 round 2, the class of three rounds' overclaims): every
 * code identifier or file the row, and the v0.8.76 entry, name in backticks exists. A function, a
 * component, a constant, a type or a file that is renamed or removed while the spec still names it fails
 * here (`remainderOf` was the instance). It holds that a name exists, not what the sentence says of it.
 *
 * Which spans are checked: a span with a `/` or a file extension is a file (found by its path's end
 * among the repository's files); a dotted Python path is a module and a name defined in it; a span of
 * one identifier (with or without a call's parentheses) that has an upper-case letter is a code
 * identifier, declared (`const`, `function`, `class`, `interface`, `type`, `enum`) or a key of an object
 * or a schema in the app (every `.ts`, `.tsx`, `.mjs` and `.json` file under `web/` outside `web/tests/`
 * and `web/e2e/`: `web/src` with its generated types, `web/scripts`, `web/plugins`, the vite, vitest and
 * playwright configurations, `tsconfig*`, and the keys of `budget.json` and `licenses.json`), never in a
 * test file (`web/tests/`, `web/e2e/`), but for the few helpers of the tests that `TEST_NAMES`
 * lists, which must be declared in one (m1 of #121 round 3: a pin's key in a test file must not keep a
 * renamed symbol of `src/` passing); a span of the shape of a script (`generate:check`, `test:shuffle`)
 * must be a script of `web/package.json`; a lower-case word (`block`, `listed`, `tables`) is
 * data's, and is not checked. Any other span (a literal, an expression, a signature) is not.
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const WEB = path.join(import.meta.dirname, "../..");
const ROOT = path.join(WEB, "..");
const SKIPPED = new Set(["node_modules", ".git", ".venv", "dist", "dist-e2e", "test-results", "playwright-report", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"]);

/** Every file of the repository, by path from its root. */
function filesUnder(directory: string, found: string[] = []): string[] {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (SKIPPED.has(entry.name)) {
      continue;
    }
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      filesUnder(full, found);
    } else {
      found.push(path.relative(ROOT, full));
    }
  }
  return found;
}

const FILES = filesUnder(ROOT);
const CODE = FILES.filter((file) => /\.(?:ts|tsx|mjs|json)$/u.test(file) && file.startsWith("web/") && !file.includes("package-lock"));
const SOURCES = new Map(CODE.map((file) => [file, readFileSync(path.join(ROOT, file), "utf8")] as const));
const PYTHON = new Map(FILES.filter((file) => file.endsWith(".py") && file.startsWith("server/src/")).map((file) => [file, readFileSync(path.join(ROOT, file), "utf8")] as const));
const SCRIPTS = new Set(Object.keys((JSON.parse(readFileSync(path.join(WEB, "package.json"), "utf8")) as { scripts: Record<string, string> }).scripts));

/** Names the spec gives that are no declaration of this repository, each with why. */
const NOT_DECLARED: Readonly<Record<string, string>> = {
  Map: "a builtin",
  Set: "a builtin",
  JSON: "a builtin",
  Math: "a builtin",
  Number: "a builtin",
  String: "a builtin",
  Intl: "a builtin",
  parseFloat: "a builtin",
  parseInt: "a builtin",
  toFixed: "a builtin's method",
  toPrecision: "a builtin's method",
  toExponential: "a builtin's method",
  toLocaleString: "a builtin's method",
  textContent: "a DOM property",
  WITHHELD_UNDER_K: "a refusal's code, the server's (a token of web/src)",
  "react-dom/client": "a package",
  "format.ts": "the name `format.tsx` had before D430's rename: it must not exist",
};

/** Names the spec gives that are declared in a test file (a helper of the tests), and nowhere else: every
 * other identifier is declared in the app (`web/src`, `web/scripts`, `web/plugins`, the lint's
 * configuration), so that a pin's key in a test file cannot keep a renamed symbol of `src/` passing. */
const TEST_NAMES: Readonly<Record<string, string>> = {
  NUMBER_PINS: "the index gate's pins of the sanctioned predicates",
  leafSweep: "the injection harness's sweep",
  countTexts: "the injection harness's independent count",
};

/** The spans between backticks of the D430 row and the v0.8.76 entry of SPEC.md's change log. (Every
 * span of the entry is also in the row today, so scanning the entry adds no case a mutant of this
 * function that scans the row alone could be told from: it guards the entry against drift.) */
export function spansOf(spec: string, decision = "D430", version = "v0.8.76"): string[] {
  const lines = spec.split("\n");
  const row = lines.find((line) => line.startsWith(`| ${decision} |`));
  const start = lines.findIndex((line) => line.startsWith(`- **${version}**`));
  if (row === undefined || start < 0) {
    throw new Error(`SPEC.md has no ${decision} row or no ${version} entry`);
  }
  let end = start + 1;
  while ((lines[end] ?? "").startsWith("  ")) {
    end += 1;
  }
  return [...`${row}\n${lines.slice(start, end).join("\n")}`.matchAll(/`([^`]+)`/gu)].map((match) => match[1] ?? "");
}

const escaped = (name: string): string => name.replace(/[$()*+.?[\\\]^{|}]/gu, "\\$&");

/** Whether `name` is declared in a source file of `web/`: a declaration or a key. */
function declared(name: string, sources: ReadonlyMap<string, string>): boolean {
  const declaration = new RegExp(`\\b(?:const|let|var|function|class|interface|type|enum)\\s+${escaped(name)}\\b|^\\s*["']?${escaped(name)}["']?\\??:`, "mu");
  return [...sources.values()].some((text) => declaration.test(text));
}

/** The spans of `spans` that name nothing in the repository (a stale name), and the entries of
 * `NOT_DECLARED` that no span uses, by `sources` and `files`. */
export function stale(
  spans: readonly string[],
  sources: ReadonlyMap<string, string>,
  python: ReadonlyMap<string, string>,
  files: readonly string[],
  testNames: Readonly<Record<string, string>> = TEST_NAMES,
  notDeclared: Readonly<Record<string, string>> = NOT_DECLARED,
): { readonly missing: string[]; readonly unused: string[] } {
  const isTest = ([file]: readonly [string, string]): boolean => file.startsWith("web/tests/") || file.startsWith("web/e2e/");
  const tests = new Map([...sources].filter(isTest));
  const app = new Map([...sources].filter((entry) => !isTest(entry)));
  const missing: string[] = [];
  const used = new Set<string>();
  for (const span of new Set(spans)) {
    if (span in notDeclared) {
      used.add(span);
      const tokens = [...sources.entries()].some(([file, text]) => file.startsWith("web/src/") && new RegExp(`\\b${escaped(span)}\\b`, "u").test(text));
      if (span === "WITHHELD_UNDER_K" && !tokens) {
        missing.push(span);
      }
      if (span === "format.ts" && files.some((file) => file === "web/src/api/format.ts")) {
        missing.push(span);
      }
    } else if (/^[a-z][\w-]*:[\w:-]+$/u.test(span)) {
      if (!SCRIPTS.has(span)) {
        missing.push(span);
      }
    } else if (/^[\w.@-]+(?:\/[\w.@-]+)+$/u.test(span) || /^[\w.-]+\.(?:ts|tsx|mjs|json|py)$/u.test(span)) {
      if (!files.some((file) => file === span || file.endsWith(`/${span}`))) {
        missing.push(span);
      }
    } else if (/^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$/u.test(span) && /^(?:aibi|visible)\./u.test(span)) {
      const parts = span.split(".");
      const name = parts.pop() ?? "";
      const module = `${parts.join("/")}.py`;
      const text = [...python].find(([file]) => file.endsWith(`/${module}`))?.[1];
      if (text === undefined || !new RegExp(`^${escaped(name)}\\b\\s*[:=]|^(?:def|class)\\s+${escaped(name)}\\b`, "mu").test(text)) {
        missing.push(span);
      }
    } else if (/^[A-Za-z_]\w*(?:\(.*\))?$/u.test(span) && /[A-Z]/u.test(span.replace(/\(.*$/u, ""))) {
      const name = span.replace(/\(.*$/u, "");
      used.add(name);
      if (!declared(name, Object.hasOwn(testNames, name) ? tests : app)) {
        missing.push(span);
      }
    }
  }
  return { missing, unused: [...Object.keys(notDeclared), ...Object.keys(testNames)].filter((name) => !used.has(name)) };
}

describe("the names D430 gives exist (MA-1 of #121 round 2)", () => {
  const spec = readFileSync(path.join(ROOT, "SPEC.md"), "utf8");
  const spans = spansOf(spec);

  it("names a good many symbols and files, so that the extraction is not empty", () => {
    expect(spans.length).toBeGreaterThan(100);
    for (const span of ["listOf", "numbers.ts", "NUMBER_PINS", "leafSweep", "aibi.core.schema.visible.LISTED"]) {
      expect(spans).toContain(span);
    }
  });

  it("finds every identifier, component, constant and file D430 names in the repository, and no stale entry of the list of exceptions", () => {
    const found = stale(spans, SOURCES, PYTHON, FILES);
    expect(found).toEqual({ missing: [], unused: [] });
  });

  it("sees a name that was renamed or removed (the control: remainderOf, a missing file, a module's missing name)", () => {
    const names = ["remainderOf", "listOf", "src/api/nothing.ts", "numbers.ts", "aibi.core.schema.visible.NOPE", "aibi.core.schema.visible.LISTED", "largestOf(map: BoxedMap<ServerNumber> | null)", "oldOf(a)"];
    const found = stale(names, SOURCES, PYTHON, FILES);
    expect(found.missing).toEqual(["remainderOf", "src/api/nothing.ts", "aibi.core.schema.visible.NOPE", "oldOf(a)"]);
  });

  it("sees a script that is none, and a symbol of src/ kept alive by a key of a test file (the controls)", () => {
    const found = stale(["generate:check", "generate:chek", "test:shuffle", "test:shufle"], SOURCES, PYTHON, FILES);
    expect(found.missing).toEqual(["generate:chek", "test:shufle"]);
    const made = new Map([
      ["web/src/a.ts", "export function Kept(): void {}"],
      ["web/tests/a.test.ts", "const PINS = {\n  Renamed: 1,\n};\nfunction leafSweep(): void {}"],
    ]);
    expect(stale(["Kept", "Renamed", "leafSweep", "NUMBER_PINS"], made, PYTHON, FILES).missing).toEqual(["Renamed", "NUMBER_PINS"]);
    // A helper of the tests (`TEST_NAMES`) is declared in a test file: one declared only in `src/` is missing.
    const onlyInSrc = new Map([...made, ["web/src/b.ts", "const countTexts = 1;"]]);
    expect(stale(["countTexts"], onlyInSrc, PYTHON, FILES).missing).toEqual(["countTexts"]);
    expect(stale(["countTexts"], new Map([...made, ["web/tests/b.test.ts", "const countTexts = 1;"]]), PYTHON, FILES).missing).toEqual([]);
    // `web/e2e/` is a test directory: a key there keeps no name of the app passing, and declares a helper of the tests.
    const inE2e = new Map([...made, ["web/e2e/a.spec.ts", "const KEYS = {\n  Renamed: 1,\n  NUMBER_PINS: 2,\n};"]]);
    expect(stale(["Renamed", "NUMBER_PINS"], inE2e, PYTHON, FILES).missing).toEqual(["Renamed"]);
    expect(stale(["Renamed"], new Map([["web/e2e/a.spec.ts", "const KEYS = {\n  Renamed: 1,\n};"]]), PYTHON, FILES).missing).toEqual(["Renamed"]);
  });

  it("sees an entry of the lists of exceptions that no span uses (the control of `unused`)", () => {
    const all = [...Object.keys(NOT_DECLARED), ...Object.keys(TEST_NAMES)];
    expect(stale([], SOURCES, PYTHON, FILES).unused).toEqual(all);
    expect(stale(["Map", "leafSweep"], SOURCES, PYTHON, FILES).unused).toEqual(all.filter((name) => name !== "Map" && name !== "leafSweep"));
  });
});

/** The helpers of the tests D432 names, each declared in a test file alone. */
const D432_TEST_NAMES: Readonly<Record<string, string>> = {
  DRAFT_TABLE: "the seam's surface gate's checked-in table",
  shapeOf: "the operator client's surface gate's printer",
  TEXT_PINS: "the index gate's pins of the takers of a server text",
  NUMBER_PINS: "the index gate's pins of the sanctioned predicates",
  OUTPUT_PINS: "the index gate's pins of what an export gives",
  signatureText: "the index gate's identity pins",
  classOf: "the property test's classifier of the corpus, held to the server's class (document-support.tsx)",
};

/** The names D432 gives that are no declaration of the web, each with why (the server's are held by
 * name in its Python below). */
const D432_NOT_DECLARED: Readonly<Record<string, string>> = {
  R: "a type parameter",
  WeakMap: "a builtin",
  Proxy: "a builtin",
  TypeError: "a builtin's error",
  NaN: "a builtin",
  ReadonlyArray: "a builtin type",
  toJSON: "a builtin's method",
  Required: "a tuple element's flag, TypeScript's",
  Rest: "a tuple element's flag, TypeScript's",
  LIMIT_EXCEEDED: "a refusal's code, the server's",
  choiceOf: "the name choiceFor had before D432: the web must declare it nowhere",
};

describe("the names D432 gives exist (the class of MA-1 of #121 round 2, for the document seam)", () => {
  const spans = spansOf(readFileSync(path.join(ROOT, "SPEC.md"), "utf8"), "D432", "v0.8.78");

  it("names a good many symbols and files", () => {
    expect(spans.length).toBeGreaterThan(60);
    for (const span of ["optionsOf", "walkedAs", "choiceText", "DRAFT_TABLE", "web/src/api/datatypes.ts"]) {
      expect(spans).toContain(span);
    }
  });

  it("finds every identifier, constant and file D432 names in the repository, and no stale entry of its lists of exceptions", () => {
    expect(stale(spans, SOURCES, PYTHON, FILES, D432_TEST_NAMES, D432_NOT_DECLARED)).toEqual({ missing: [], unused: [] });
  });

  it("the server's name it gives is the server's, and choiceOf is declared nowhere in the web", () => {
    const refusals = [...PYTHON].find(([file]) => file.endsWith("/aibi/core/schema/refusals.py"))?.[1] ?? "";
    expect(refusals).toContain('LIMIT_EXCEEDED = "LIMIT_EXCEEDED"');
    expect([...SOURCES].filter(([file, text]) => !file.startsWith("web/tests/") && /\bchoiceOf\b/u.test(text)).map(([file]) => file)).toEqual([]);
  });
});

/** One row of D432's table of the server's rules (`web/tests/fixtures/seam-rules.json`). */
interface Rule {
  readonly id: string;
  readonly kind: string;
  readonly rule: string;
  readonly cells: readonly string[];
}

const TABLE = path.join(ROOT, "web/tests/fixtures/seam-rules.json");
const KINDS = ["datatype", "relation", "outcome", "bound", "limit"];

/** The cells of `rows` that do not exist: a Python cell is `path::function` (a `def` of the file), a
 * vitest cell `path::title` (the title as a string literal of the file), the path from the repository's
 * root. */
export function missingCells(rows: readonly Rule[], read: (file: string) => string | null): string[] {
  const missing: string[] = [];
  for (const row of rows) {
    for (const cell of row.cells) {
      const [file = "", name = ""] = cell.split("::");
      const text = file === "" || name === "" ? null : read(file);
      const found = text !== null && (file.endsWith(".py") ? new RegExp(`^(?:async )?def ${escaped(name)}\\(`, "mu").test(text) : text.includes(`"${name}"`));
      if (!found) {
        missing.push(`${row.id}: ${cell}`);
      }
    }
  }
  return missing;
}

/** The sentence of the residuals that names the one code D432 states outside the table (the seam's own
 * promise: it sends any number and the server refuses in-band). */
const EXEMPT = "the server refuses in-band with `LIMIT_EXCEEDED` at the position the table names";

/** What a decision's text may not state outside the table: a refusal code, a limit, a status, a function
 * of the server (`words` are the server's, read from its source), but for the one exempt sentence. */
export function outsideTable(text: string, words: readonly string[]): string[] {
  const rest = text.replace(EXEMPT, "");
  return words.filter((word) => new RegExp(`(?<![A-Za-z_0-9])${escaped(word)}(?![A-Za-z_0-9])`, "u").test(rest));
}

/** What the decision's row and the change log's entry state outside the table: the words found in
 * either (the entry is a summary of the row, and drifts from it). */
export function statedOutside(row: string, entry: string, words: readonly string[]): string[] {
  return [...outsideTable(row, words), ...outsideTable(entry, words)];
}

/** The fields of a row of the table; a row holds no other (a free-text field would be a place to state the
 * server's behaviour outside a cell). `rule` states a rule of the server and names the cells that hold it;
 * `why` is the one free text besides, and states a fact of the seam or of the corpus. */
const FIELDS = ["id", "kind", "rule", "cells", "position", "keyword", "value", "reach", "limit", "quantity", "needs", "at", "past", "why"];

/** The problems of the rows' fields: a field the table does not know, and a `why` (or the table's
 * `about`) that names a word of the server. */
export function fieldProblems(rows: readonly Record<string, unknown>[], about: string, words: readonly string[]): string[] {
  const problems = outsideTable(about, words).map((word) => `about: ${word}`);
  for (const row of rows) {
    const id = String(row["id"]);
    problems.push(...Object.keys(row).filter((key) => !FIELDS.includes(key)).map((key) => `${id}: a field ${key}`));
    const why = row["why"];
    problems.push(...outsideTable(typeof why === "string" ? why : "", words).map((word) => `${id}: why names ${word}`));
    if (row["reach"] !== undefined && row["reach"] !== "edge" && (typeof why !== "string" || why === "")) {
      problems.push(`${id}: no why`);
    }
  }
  return problems;
}

/** The comments of a source file (block and line comments, by a crude reading: a `//` inside a string is
 * read as a comment, which can over-report, never under-report). */
export function commentsOf(source: string): string[] {
  return [...source.matchAll(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/gu)].map((match) => match[0]);
}

/** The seam's source files, whose comments state no rule of the server outside the table. */
const SEAM_SOURCES = ["web/src/api/document.ts", "web/src/api/datatypes.ts", "web/src/api/text.tsx", "web/src/api/typed.ts"];

/** The words a seam source file's comments may name, each with why: the client's own contract. */
const SOURCE_EXEMPT: Readonly<Record<string, Readonly<Record<string, string>>>> = {
  "web/src/api/typed.ts": { "200": "the status of an answer the client walks (D426's own contract with its transport, not a rule of a tool)" },
};

/** The server words in the comments of `sources` (by file), but for the exempt ones. */
export function sourceProblems(sources: ReadonlyMap<string, string>, words: readonly string[], exempt: Readonly<Record<string, Readonly<Record<string, string>>>>): string[] {
  const found: string[] = [];
  for (const [file, text] of sources) {
    for (const word of outsideTable(commentsOf(text).join("\n"), words)) {
      if (!(word in (exempt[file] ?? {}))) {
        found.push(`${file}: ${word}`);
      }
    }
  }
  return found;
}

describe("D432's table of the server's rules, and what D432 states outside it (the class of rounds 1 and 2 of #124)", () => {
  const spec = readFileSync(path.join(ROOT, "SPEC.md"), "utf8");
  const rows = (JSON.parse(readFileSync(TABLE, "utf8")) as { rows: Rule[] }).rows;
  const lines = spec.split("\n");
  const row = lines.find((line) => line.startsWith("| D432 |")) ?? "";
  const start = lines.findIndex((line) => line.startsWith("- **v0.8.78**"));
  let end = start + 1;
  while ((lines[end] ?? "").startsWith("  ")) {
    end += 1;
  }
  const entry = lines.slice(start, end).join("\n");
  const python = (name: string): string => [...PYTHON].find(([file]) => file.endsWith(name))?.[1] ?? "";
  const codes = [...python("/aibi/core/schema/refusals.py").matchAll(/^\s+([A-Z][A-Z_]+) = "\1"/gmu)].map((match) => match[1] ?? "");
  const limits = [...python("/aibi/core/schema/limits.py").matchAll(/^(MAX_[A-Z_]+)\b\s*=/gmu)].map((match) => match[1] ?? "");
  const WORDS = [...codes, ...limits, "200", "413", "422", "SUMMARISED", "_CATEGORIES", "typed_constant", "valid: false", "valid: true", "distribution."];
  const read = (file: string): string | null => {
    try {
      return readFileSync(path.join(ROOT, file), "utf8");
    } catch {
      return null;
    }
  };

  it("is a table of rows, each a rule with its cells, of the kinds D432 states, with ids that are unique", () => {
    expect(rows.length).toBeGreaterThan(30);
    expect(new Set(rows.map((one) => one.id)).size).toBe(rows.length);
    for (const one of rows) {
      expect(KINDS, one.id).toContain(one.kind);
      expect(one.rule.length, one.id).toBeGreaterThan(20);
      expect(one.cells.length, one.id).toBeGreaterThan(0);
      expect(new Set(one.cells).size, one.id).toBe(one.cells.length);
    }
    expect(new Set(rows.map((one) => one.kind))).toEqual(new Set(KINDS));
  });

  it("names, in every row, cells that exist", () => {
    expect(missingCells(rows, read)).toEqual([]);
  });

  it("sees a cell that does not exist (the control): a function, a title, a file and a malformed cell", () => {
    const cells = [
      "server/tests/core/catalog/test_builder_documents.py::test_nothing_of_the_kind",
      "web/tests/unit/document.test.tsx::a title nobody wrote",
      "web/tests/unit/nothing.test.ts::x",
      "no separator",
      // A prefix of a function that exists is no cell (`def name(` is read with its parenthesis).
      "server/tests/core/catalog/test_builder_documents.py::test_the_server_s_verdict_on_every",
      // A title found only inside another title is no cell (a title is read as a whole string literal).
      "web/tests/unit/document.test.tsx::a draft that is not one is refused",
      "server/tests/core/catalog/test_builder_documents.py::test_the_server_s_verdict_on_every_entry",
      "web/tests/unit/document.test.tsx::the walk's registry of answers",
    ];
    expect(missingCells([{ id: "x", kind: "outcome", rule: "", cells }], read).map((one) => one.slice(3))).toEqual(cells.slice(0, 6));
  });

  it("names every row D432 and the change log entry cite by its id", () => {
    const ids = new Set(rows.map((one) => one.id));
    const cited = [...`${row}\n${entry}`.matchAll(/`([a-z]+(?:-[a-z]+)+)`/gu)].map((match) => match[1] ?? "");
    expect(cited).toEqual(expect.arrayContaining(["view-lists", "leaf-list", "datatype-vocabulary", "view-ancestors", "any-leaf-any-position"]));
    expect(cited.filter((id) => !ids.has(id))).toEqual([]);
  });

  it("states no refusal code, limit, status or function of the server outside the table, in the row or in the change log's entry", () => {
    expect(WORDS.length).toBeGreaterThan(80);
    for (const word of ["LIMIT_EXCEEDED", "NOT_SUPPORTED", "AGGREGATE_REQUIRED", "MISSING_MEMBER", "NOT_PERMISSIBLE", "INVALID_JSON", "UNKNOWN_PARAMETER", "MAX_LEAVES", "MAX_CLAUSES", "MAX_VARIABLES", "MAX_STRING", "MAX_DOCUMENT_BYTES", "MAX_BODY_BYTES"]) {
      expect(WORDS, word).toContain(word);
    }
    expect(row).toContain(EXEMPT);
    expect(statedOutside(row, entry, WORDS)).toEqual([]);
  });

  it("has no field the table does not know, and no why that names a word of the server (a why is a fact of the seam or of the corpus)", () => {
    const table = JSON.parse(readFileSync(TABLE, "utf8")) as { about: string; rows: Record<string, unknown>[] };
    expect(fieldProblems(table.rows, table.about, WORDS)).toEqual([]);
  });

  it("sees a why, an about and a field that state the server's behaviour (the controls)", () => {
    const rows = [
      { id: "a", reach: "never", why: "the seam reaches it only with over 100,000 columns and the bytes stay under MAX_DOCUMENT_BYTES" },
      { id: "b", reach: "server", why: "refused with LIMIT_EXCEEDED" },
      { id: "c", reach: "never", why: "answered 422" },
      { id: "d", reach: "never", why: "one value is written", notes: "x" },
      { id: "e", reach: "unreached" },
      { id: "f", reach: "edge" },
      { id: "g", reach: "never", why: "not reached by the corpus (the orchard); not stated" },
    ];
    expect(fieldProblems(rows, "an about that says NOT_SUPPORTED", WORDS)).toEqual(["about: NOT_SUPPORTED", "a: why names MAX_DOCUMENT_BYTES", "b: why names LIMIT_EXCEEDED", "c: why names 422", "d: a field notes", "e: no why"]);
    expect(fieldProblems(rows.slice(3, 4), "clean", WORDS)).toEqual(["d: a field notes"]);
  });

  it("holds the comments of the seam's source files to the table too (document.ts, datatypes.ts, text.tsx, typed.ts), but for the exempt words", () => {
    expect(SEAM_SOURCES).toEqual(["web/src/api/document.ts", "web/src/api/datatypes.ts", "web/src/api/text.tsx", "web/src/api/typed.ts"]);
    const sources = new Map(SEAM_SOURCES.map((file) => [file, read(file) ?? ""] as const));
    expect([...sources.values()].every((text) => text.length > 500)).toBe(true);
    expect(sourceProblems(sources, WORDS, SOURCE_EXEMPT)).toEqual([]);
    // Each exemption is needed (a stale one is a hole), and without them the comments are found.
    expect(sourceProblems(sources, WORDS, {})).toEqual(Object.entries(SOURCE_EXEMPT).flatMap(([file, names]) => Object.keys(names).map((word) => `${file}: ${word}`)));
  });

  it("sees a server word in a comment of a source file, a block or a line, and not in its code (the controls)", () => {
    const made = new Map([
      ["web/src/api/a.ts", "/** A column is NOT_SUPPORTED under every k. */\nexport const A = 1;"],
      ["web/src/api/b.ts", "export const B = 1; // refused with 422\n"],
      ["web/src/api/c.ts", "export const NOT_SUPPORTED = 'MAX_LEAVES';\n/** A clean comment. */\n"],
      ["web/src/api/d.ts", "/** the status 200 */\n"],
    ]);
    expect(sourceProblems(made, WORDS, {})).toEqual(["web/src/api/a.ts: NOT_SUPPORTED", "web/src/api/b.ts: 422", "web/src/api/d.ts: 200"]);
    expect(sourceProblems(made, WORDS, { "web/src/api/d.ts": { "200": "x" } })).toEqual(["web/src/api/a.ts: NOT_SUPPORTED", "web/src/api/b.ts: 422"]);
  });

  it("sees a sentence of the server's behaviour outside the table (the controls)", () => {
    for (const text of ["a view column of a string datatype is NOT_SUPPORTED", "more than MAX_LEAVES leaves are refused", "the server answers 422", "a body over the limit is 413", "valid: false", "distribution.SUMMARISED", "the seam sends any number of leaves and the server refuses with LIMIT_EXCEEDED", `the seam sends any number and ${EXEMPT}, and MAX_STRING`]) {
      expect(outsideTable(text, WORDS), text).not.toEqual([]);
    }
    expect(outsideTable(`the seam sends any number and ${EXEMPT}.`, WORDS)).toEqual([]);
    // The row and the entry are each read: a word in either is found.
    expect(statedOutside("a clean row", "an entry that says NOT_SUPPORTED", WORDS)).toEqual(["NOT_SUPPORTED"]);
    expect(statedOutside("a row that says 422", "a clean entry", WORDS)).toEqual(["422"]);
    expect(statedOutside("a clean row", "a clean entry", WORDS)).toEqual([]);
    expect(outsideTable("a column 4200 or 2000 and NOT_SUPPORTEDS and xMAX_LEAVES", WORDS)).toEqual([]);
  });
});
