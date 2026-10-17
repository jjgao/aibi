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
export function spansOf(spec: string): string[] {
  const lines = spec.split("\n");
  const row = lines.find((line) => line.startsWith("| D430 |"));
  const start = lines.findIndex((line) => line.startsWith("- **v0.8.76**"));
  if (row === undefined || start < 0) {
    throw new Error("SPEC.md has no D430 row or no v0.8.76 entry");
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
export function stale(spans: readonly string[], sources: ReadonlyMap<string, string>, python: ReadonlyMap<string, string>, files: readonly string[]): { readonly missing: string[]; readonly unused: string[] } {
  const isTest = ([file]: readonly [string, string]): boolean => file.startsWith("web/tests/") || file.startsWith("web/e2e/");
  const tests = new Map([...sources].filter(isTest));
  const app = new Map([...sources].filter((entry) => !isTest(entry)));
  const missing: string[] = [];
  const used = new Set<string>();
  for (const span of new Set(spans)) {
    if (span in NOT_DECLARED) {
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
      if (!declared(name, Object.hasOwn(TEST_NAMES, name) ? tests : app)) {
        missing.push(span);
      }
    }
  }
  return { missing, unused: [...Object.keys(NOT_DECLARED), ...Object.keys(TEST_NAMES)].filter((name) => !used.has(name)) };
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
