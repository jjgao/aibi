/**
 * The display gate's fixtures (D427): a tiny package that passes the gate (`BASE`), and each
 * fixture an edit of it with the rules the gate must fail it by (`expected`, exact; an empty list
 * is a tree the gate must pass, a residual the gate names as one). They are the scenarios of the
 * plan's verification rounds (s01 on), the first code review's (s54 on) and the second's (s121 on,
 * and the owner-order cells), reduced to the claims the gate still makes: the fixtures about the
 * import graph, the exemptions, the roots and the table's reading by other files left with the
 * claims (D427: the build's gate and review hold them). A mock "module" is a function returning a
 * string: the gate reads the table, the files and the test run, never what a module renders. An
 * edit that does not find what it replaces throws, so no fixture is a no-op.
 *
 * A fixture that edits no test, no vitest configuration and no module a test imports has
 * `sharedRun`: the baseline's run of the tests is its run too, which `D427_FULL=1` turns off.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { CANARY_FILE, type Rule, STALE_RESULTS } from "./display";

export type Tree = Map<string, string>;

export type Fixture = {
  readonly name: string;
  readonly expected: readonly Rule[];
  readonly why: string;
  readonly edit: (tree: Tree) => void;
  /** Symlinks to make, each `[the link, its target]` relative to the tree's root (a directory). */
  readonly links?: readonly (readonly [string, string])[];
  /** The edit touches no test, no vitest configuration and no module a test imports: the baseline's run serves. */
  readonly sharedRun?: boolean;
};

/** A row of the baseline's table: where its module and test are, and what its module shows. */
type BaseRow = { readonly id: string; readonly owner: string; readonly module: string; readonly test: string; readonly screens: string; readonly call: string; readonly shows: string; readonly built: boolean };

const BASE_ROWS: readonly BaseRow[] = [
  { id: "R01", owner: "2b-1a", module: "src/layout/Text.tsx", test: "tests/unit/text.test.ts", screens: `"all"`, call: "Text()", shows: "t", built: true },
  { id: "R02", owner: "2b-1b", module: "src/catalogue/Home.tsx", test: "tests/unit/home.test.ts", screens: `"catalogue"`, call: "Home()", shows: "home", built: true },
  { id: "R03", owner: "2b-1b", module: "src/catalogue/NotFound.tsx", test: "tests/unit/home.test.ts", screens: `"catalogue"`, call: "NotFound()", shows: "nf", built: true },
  { id: "R12", owner: "M5.4-min", module: "src/result/Result.tsx", test: "tests/unit/result.test.ts", screens: `"result"`, call: "Result()", shows: "result", built: false },
  { id: "R45", owner: "M5.2-min", module: "src/dataset/Dataset.tsx", test: "tests/unit/dataset.test.ts", screens: `"dataset"`, call: "Dataset()", shows: "dataset", built: true },
  { id: "R46", owner: "M5.2-min", module: "src/dataset/Datasets.tsx", test: "tests/unit/datasets.test.ts", screens: `"dataset"`, call: "Datasets()", shows: "list", built: true },
  { id: "R60", owner: "M5.5", module: "src/operator/Proposals.tsx", test: "tests/unit/proposals.test.ts", screens: `"operator"`, call: "Proposals()", shows: "proposals", built: false },
  { id: "R61", owner: "M5.3-min", module: "src/result/Builder.tsx", test: "tests/unit/builder.test.ts", screens: `"builder"`, call: "Builder()", shows: "builder", built: false },
  { id: "R90", owner: "2b-1L", module: "scripts/raw-text.mjs", test: "tests/unit/raw-text.test.ts", screens: `"all"`, call: "rule", shows: "raw-text", built: true },
  // A second row for each late owner, so that a partly built owner can be told from an unbuilt one (the owner-complete cells).
  { id: "R62", owner: "M5.3-min", module: "src/result/Chart.tsx", test: "tests/unit/chart.test.ts", screens: `"builder"`, call: "Chart()", shows: "chart", built: false },
  { id: "R13", owner: "M5.4-min", module: "src/result/Table.tsx", test: "tests/unit/table.test.ts", screens: `"result"`, call: "Table()", shows: "table", built: false },
  { id: "R63", owner: "M5.5", module: "src/operator/Queue.tsx", test: "tests/unit/queue.test.ts", screens: `"operator"`, call: "Queue()", shows: "queue", built: false },
];

const row = (id: string, owner: string, module: string, test: string, screens: string): string =>
  `  { id: "${id}", basis: "spec statement", must: "a mock obligation", screens: [${screens}], spec: ["§8"], owner: "${owner}", module: "${module}", test: "${test}" },`;

const ROWS_TS = [
  `export const OWNERS = ["2b-1a", "2b-1L", "2b-1b", "M5.2-min", "M5.3-min", "M5.4-min", "M5.5", "server"];`,
  `export const SCREENS = ["all", "catalogue", "dataset", "builder", "result", "operator"];`,
  `export const BASES = ["spec MUST", "spec statement", "derived"];`,
  `export const ROWS = [`,
  ...BASE_ROWS.map((base) => row(base.id, base.owner, base.module, base.test, base.screens)),
  row("R50", "server", "", "", `"catalogue"`),
  `];`,
  `export const EXEMPT = [{ module: "src/catalogue/About.tsx", reason: "fixed words" }];`,
  `export const HELPERS = [{ module: "src/root.ts", reason: "plumbing" }];`,
  `export const RETIRED = [];`,
  `export const NOT_ROWS = [{ what: "a server duty", spec: ["§8"] }];`,
  ``,
].join("\n");

const CONFIG = `import { defineConfig } from "vitest/config";
export default defineConfig({ test: { projects: [{ test: { name: "unit", environment: "node", include: ["tests/unit/**/*.test.{ts,tsx}"] } }] } });
`;

const DATASET_TEST = `import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
`;

/** A module's text: a function returning what it shows (the lint's module exports a constant). */
const moduleText = (base: BaseRow): string =>
  base.call === "rule" ? `export const rule = "${base.shows}";\n` : `export function ${base.call.replace("()", "")}(): string { return "${base.shows}"; }\n`;

/** The text of a test file for `rows` (those that share it): one titled test of each. */
const testText = (rows: readonly BaseRow[]): string => {
  const names = (base: BaseRow): string => base.call.replace("()", "");
  const imports = rows.map((base) => `import { ${names(base)} } from "../../${base.module.replace(/\.tsx$/, "")}";`);
  const tests = rows.map((base) => `it("${base.id}: shown", () => { expect.hasAssertions(); expect(${base.call}).toBe("${base.shows}"); });`);
  return `import { expect, it } from "vitest";\n${imports.join("\n")}\n${tests.join("\n")}\n`;
};

/** The passing baseline. */
export const BASE: Readonly<Record<string, string>> = {
  "vitest.config.mjs": CONFIG,
  "src/display/rows.ts": ROWS_TS,
  "src/layout/Text.tsx": `export function Text(): string { return "t"; }\n`,
  "src/catalogue/Home.tsx": `export function Home(): string { return "home"; }\n`,
  "src/catalogue/NotFound.tsx": `export function NotFound(): string { return "nf"; }\n`,
  "src/dataset/Dataset.tsx": `export function Dataset(): string { return "dataset"; }\n`,
  "src/dataset/Datasets.tsx": `export function Datasets(): string { return "list"; }\n`,
  "scripts/raw-text.mjs": `export const rule = "raw-text";\n`,
  "tests/unit/text.test.ts": `import { expect, it } from "vitest";
import { Text } from "../../src/layout/Text";
it("R01: data is shown as text", () => { expect.hasAssertions(); expect(Text()).toBe("t"); });
`,
  "tests/unit/home.test.ts": `import { expect, it } from "vitest";
import { Home } from "../../src/catalogue/Home";
import { NotFound } from "../../src/catalogue/NotFound";
it("R02: home", () => { expect.hasAssertions(); expect(Home()).toBe("home"); });
it("R03: not found", () => { expect.hasAssertions(); expect(NotFound()).toBe("nf"); });
`,
  "tests/unit/dataset.test.ts": DATASET_TEST,
  "tests/unit/datasets.test.ts": `import { expect, it } from "vitest";
import { Datasets } from "../../src/dataset/Datasets";
it("R46: list", () => { expect.hasAssertions(); expect(Datasets()).toBe("list"); });
`,
  "tests/unit/raw-text.test.ts": `import { expect, it } from "vitest";
import { rule } from "../../scripts/raw-text.mjs";
it("R90: the lint", () => { expect.hasAssertions(); expect(rule).toBe("raw-text"); });
`,
  [CANARY_FILE]: readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "unit", "d427-canary.test.ts"), "utf8"),
};

/** Replace `from` by `to` in `file`, which must hold it. */
function swap(tree: Tree, file: string, from: string, to: string): void {
  const text = tree.get(file);
  if (text?.includes(from) !== true) {
    throw new Error(`the fixture's edit finds no ${JSON.stringify(from)} in ${file}`);
  }
  tree.set(file, text.replace(from, to));
}

function drop(tree: Tree, file: string): void {
  if (!tree.delete(file)) {
    throw new Error(`the fixture's edit finds no ${file}`);
  }
}

function put(tree: Tree, file: string, text: string): void {
  tree.set(file, text);
}

const dataset = (body: string): ((tree: Tree) => void) => (tree) => {
  put(tree, "tests/unit/dataset.test.ts", body);
};

const DATASET_HEAD = `import { expect, it } from "vitest";\nimport { Dataset } from "../../src/dataset/Dataset";\nvoid Dataset;\n`;
const BROKEN = `it("R45: dataset", () => { expect.hasAssertions(); throw new Error("the module is broken"); });\n`;

export const FIXTURES: readonly Fixture[] = [
  {
    name: "s01_empty_body",
    expected: ["no-test"],
    why: "an empty body asserts nothing",
    edit: dataset(`import { it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", () => {});
`),
  },
  {
    name: "s02_loop_over_rows",
    expected: ["no-test"],
    why: "one generated title per row of ROWS: no row has a literal call (a test that imports the table is itself no claim of the gate)",
    edit: dataset(`import { expect, it } from "vitest";
import { ROWS } from "../../src/display/rows";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
for (const row of ROWS) it(\`\${row.id}: has a row\`, () => { expect.hasAssertions(); expect(row.id).toBeTruthy(); });
`),
  },
  {
    name: "s05_reexport_barrel_row",
    expected: ["no-test"],
    why: "the test imports the module through a barrel",
    edit: (tree) => {
      put(tree, "src/dataset/index.ts", `export { Dataset } from "./Dataset";\n`);
      put(
        tree,
        "tests/unit/dataset.test.ts",
        `import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset";
it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
`,
      );
    },
  },
  {
    name: "s06_title_typo",
    expected: ["no-test", "orphan-title"],
    why: "a title that names no row",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `"R45: dataset"`, `"R45 : dataset"`);
    },
  },
  {
    name: "s07_asserts_nothing_of_the_row",
    expected: [],
    why: "residual: an assertion unrelated to the row (the per-row mutation is a review step)",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", () => { expect.hasAssertions(); expect(1).toBe(1); });
`),
  },
  {
    name: "s08_skipIf",
    expected: ["no-test"],
    why: "a skipped test",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `it("R45`, `it.skipIf(true)("R45`);
    },
  },
  {
    name: "s09_describe_skip",
    expected: ["no-test"],
    why: "a literal test inside describe.skip",
    edit: dataset(`import { describe, expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
describe.skip("d", () => { it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); }); });
`),
  },
  {
    name: "s10_todo",
    expected: ["no-test"],
    why: "a todo",
    edit: dataset(`import { it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it.todo("R45: dataset");
`),
  },
  {
    name: "s11_conditional_registration",
    expected: ["no-test"],
    why: "a test registered under a condition that never holds",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `it("R45`, `if (process.env["NEVER"]) it("R45`);
    },
  },
  {
    name: "s12_excluded_file",
    expected: ["no-test"],
    why: "the row's test file is outside the project's include",
    edit: (tree) => {
      drop(tree, "tests/unit/dataset.test.ts");
      put(tree, "tests/unit/dataset.check.ts", DATASET_TEST);
    },
  },
  {
    name: "s13_type_only_import",
    expected: ["no-test"],
    why: "a type-only import of the module",
    edit: dataset(`import { expect, it } from "vitest";
import type { Dataset } from "../../src/dataset/Dataset";
type D = typeof Dataset;
it("R45: dataset", () => { expect.hasAssertions(); const d: D | null = null; expect(d).toBe(null); });
`),
  },
  {
    name: "s14_test_fails",
    expected: ["no-test"],
    why: "test.fails passes when its body fails",
    edit: dataset(`import { expect, test } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
test.fails("R45: dataset", () => { expect.hasAssertions(); });
`),
  },
  {
    name: "s15_assertions_zero_after",
    expected: ["no-test"],
    why: "expect.assertions(0) after hasAssertions (the test fails)",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", () => { expect.hasAssertions(); expect.assertions(0); });
`),
  },
  {
    name: "s16_dead_literal_plus_loop",
    expected: ["no-test"],
    why: "a dead literal title and a generated one on another line",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
const IDS = ["R45"];
if (IDS.length === 0) it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
for (const id of IDS) it(\`\${id}: dataset\`, () => { expect.hasAssertions(); expect(1).toBe(1); });
`),
  },
  {
    name: "s17_helper_reexports_rows",
    expected: ["no-test"],
    why: "the table reached through a helper: still no literal call per row",
    edit: (tree) => {
      put(tree, "tests/unit/helpers.ts", `export { ROWS } from "../../src/display/rows";\n`);
      put(
        tree,
        "tests/unit/dataset.test.ts",
        `import { expect, it } from "vitest";
import { ROWS } from "./helpers";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
for (const row of ROWS) it(\`\${row.id}: has a row\`, () => { expect.hasAssertions(); expect(row.id).toBeTruthy(); });
`,
      );
    },
  },
  {
    name: "s18_folded",
    expected: ["owner-complete"],
    why: "R46's obligation folded into R45's module: R46's own module never built",
    edit: (tree) => {
      drop(tree, "src/dataset/Datasets.tsx");
      drop(tree, "tests/unit/datasets.test.ts");
    },
  },
  {
    name: "s19_vi_mock_module",
    expected: [],
    why: "residual: vi.mock stubs the row's module",
    edit: dataset(`import { expect, it, vi } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
vi.mock("../../src/dataset/Dataset", () => ({ Dataset: () => "dataset" }));
it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
`),
  },
  {
    name: "s20_screen_row_server_owner",
    expected: ["server-row"],
    why: "a screen's row moved to the server",
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `owner: "M5.2-min", module: "src/dataset/Dataset.tsx"`, `owner: "server", module: "src/dataset/Dataset.tsx"`);
      drop(tree, "tests/unit/dataset.test.ts");
    },
  },
  {
    name: "s21_module_path_unbuilt_by_edit",
    expected: ["owner-complete"],
    why: "the row's module renamed in the table so that it is never built",
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `module: "src/dataset/Dataset.tsx"`, `module: "src/dataset/Dataset2.tsx"`);
      drop(tree, "tests/unit/dataset.test.ts");
    },
  },
  {
    name: "s22_it_each_literal",
    expected: ["no-test"],
    why: "it.each",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it.each([1])("R45: dataset %i", () => { expect.hasAssertions(); expect(1).toBe(1); });
`),
  },
  {
    name: "s23r_stale_results_file",
    expected: ["no-test"],
    why: "a results file a passing run left behind, beside a broken test (mutant M15 reads it)",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `toBe("dataset")`, `toBe("broken")`);
      put(tree, STALE_RESULTS, "{}\n");
    },
  },
  {
    name: "s24_nested_describe_runIf",
    expected: ["no-test"],
    why: "describe.runIf(false)",
    edit: dataset(`import { describe, expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
describe.runIf(false)("d", () => { it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); }); });
`),
  },
  {
    name: "s25_expect_soft",
    expected: [],
    why: "residual: expect.soft",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
it("R45: dataset", () => { expect.hasAssertions(); expect.soft(Dataset()).toBeDefined(); });
`),
  },
  {
    name: "s26_screens_empty",
    expected: ["no-screens"],
    why: "a row that binds no screen",
    sharedRun: true,
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `screens: ["dataset"], spec: ["§8"], owner: "M5.2-min", module: "src/dataset/Dataset.tsx"`, `screens: [], spec: ["§8"], owner: "M5.2-min", module: "src/dataset/Dataset.tsx"`);
    },
  },
  {
    name: "s27_owner_without_row",
    expected: ["owner-enum"],
    why: "an owner with no row",
    sharedRun: true,
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `"M5.5", "server"]`, `"M5.5", "M5.6", "server"]`);
    },
  },
  {
    name: "s28_row_owner_not_enum",
    expected: ["owner-enum"],
    why: "a row whose owner is not an owner",
    sharedRun: true,
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `owner: "M5.3-min"`, `owner: "2b-1b or the screen PR"`);
    },
  },
  {
    name: "s29_title_names_no_row",
    expected: ["orphan-title"],
    why: "a test titled with an id the table lacks",
    edit: (tree) => {
      put(tree, "tests/unit/dataset.test.ts", `${DATASET_TEST}it("R99: orphan", () => { expect.hasAssertions(); expect(1).toBe(1); });\n`);
    },
  },
  {
    name: "s30_prefix_title",
    expected: ["no-test", "orphan-title"],
    why: "R450 is not R45",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `"R45: dataset"`, `"R450: dataset"`);
    },
  },
  {
    name: "s31_shadowed_it_is_test_fails",
    expected: ["no-test"],
    why: "const it = test.fails",
    edit: dataset(`import { expect, test } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
const it = test.fails;
it("R45: dataset", () => { expect.hasAssertions(); });
`),
  },
  {
    name: "s32_it_from_a_shim",
    expected: ["no-test"],
    why: "an it imported from a shim that exports test.fails",
    edit: (tree) => {
      put(tree, "tests/unit/shim.ts", `import { test } from "vitest";\nexport const it = test.fails;\n`);
      put(
        tree,
        "tests/unit/dataset.test.ts",
        `import { expect } from "vitest";
import { it } from "./shim";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", () => { expect.hasAssertions(); });
`,
      );
    },
  },
  {
    name: "s33_dead_literal_same_line",
    expected: ["no-test"],
    why: "a dead literal on the line of a generated title",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
const IDS = ["R45"];
for (const id of IDS) it(\`\${id}: dataset\`, () => { expect(1).toBe(1); }); if (IDS.length === 0) it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
`),
  },
  {
    name: "s34_hasAssertions_in_dead_branch",
    expected: ["no-test"],
    why: "hasAssertions not the first statement",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", () => { if (Dataset() === "never") expect.hasAssertions(); });
`),
  },
  {
    name: "s40_blank_module",
    expected: ["no-module", "owner-complete"],
    why: "a row with no module in a started owner (it is not built, so its owner is not complete either)",
    edit: (tree) => {
      drop(tree, "src/dataset/Datasets.tsx");
      drop(tree, "tests/unit/datasets.test.ts");
      swap(tree, "src/display/rows.ts", `module: "src/dataset/Datasets.tsx"`, `module: ""`);
    },
  },
  {
    name: "s40b_blank_module_unstarted_owner",
    expected: ["no-module"],
    why: "a row with no module in an owner not started would never fall due",
    sharedRun: true,
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `module: "src/result/Builder.tsx"`, `module: ""`);
    },
  },
  {
    name: "s41_shared_module_two_owners",
    expected: ["no-test"],
    why: "another owner's row on a built module falls due with it",
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `owner: "M5.3-min", module: "src/result/Builder.tsx"`, `owner: "M5.3-min", module: "src/dataset/Dataset.tsx"`);
      // The owner's second row follows, or the owner would be partly built (owner-complete, which the partly cells hold).
      swap(tree, "src/display/rows.ts", `owner: "M5.3-min", module: "src/result/Chart.tsx"`, `owner: "M5.3-min", module: "src/dataset/Dataset.tsx"`);
    },
  },
  {
    name: "s42_describe_each_literal_it",
    expected: ["no-test"],
    why: "a literal it inside describe.each of one case is a real test the gate refuses: only plain describes",
    edit: dataset(`import { describe, expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
describe.each([[1]])("d %i", () => {
  it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
});
`),
  },
  {
    name: "s43_describe_each_empty",
    expected: ["no-test"],
    why: "describe.each of nothing runs nothing",
    edit: dataset(`import { describe, expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
describe.each([] as number[][])("d %i", () => {
  it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
});
`),
  },
  {
    name: "s44_it_concurrent",
    expected: ["no-test"],
    why: "it.concurrent (refused though real: the callee is not exactly it)",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `it("R45`, `it.concurrent("R45`);
    },
  },
  {
    name: "s45_test_for",
    expected: ["no-test"],
    why: "test.for",
    edit: dataset(`import { expect, test } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
test.for([1])("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
`),
  },
  {
    name: "s46_template_no_interpolation",
    expected: [],
    why: "a template with no substitution is a literal title",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `it("R45: dataset"`, "it(`R45: dataset`");
    },
  },
  {
    name: "s47_alias_t",
    expected: ["no-test"],
    why: "an alias of it (refused though real)",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
const t = it;
t("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
`),
  },
  {
    name: "s48_it_sequential",
    expected: ["no-test"],
    why: "it.sequential (refused though real)",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `it("R45`, `it.sequential("R45`);
    },
  },
  {
    name: "s49_setup_neuters_hasAssertions",
    expected: ["canary"],
    why: "a setup file that makes expect.hasAssertions a no-op",
    edit: (tree) => {
      put(tree, "tests/unit/setup.ts", `import { expect } from "vitest";\nObject.assign(expect, { hasAssertions: () => undefined });\n`);
      swap(tree, "vitest.config.mjs", `environment: "node",`, `environment: "node", setupFiles: ["tests/unit/setup.ts"],`);
      put(
        tree,
        "tests/unit/dataset.test.ts",
        `import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", () => { expect.hasAssertions(); });
`,
      );
    },
  },
  {
    name: "s50_two_rows_one_module_same_owner",
    expected: [],
    why: "two rows of one owner on one module, each with its titled test",
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `module: "src/dataset/Datasets.tsx"`, `module: "src/dataset/Dataset.tsx"`);
      drop(tree, "src/dataset/Datasets.tsx");
      put(
        tree,
        "tests/unit/datasets.test.ts",
        `import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
it("R46: list", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
`,
      );
    },
  },
  {
    name: "s51_task_location_off",
    expected: ["no-test", "canary"],
    why: "a project that turns task locations off: every row and the canary fail (safe)",
    edit: (tree) => {
      swap(tree, "vitest.config.mjs", `environment: "node",`, `environment: "node", includeTaskLocation: false,`);
    },
  },
  {
    name: "s52_test_imports_rows",
    expected: [],
    why: "residual: a test that imports the table beside its literal title (the gate does not read the imports of a test for the table: the literal title of each row is what no-test requires)",
    edit: dataset(`import { expect, it } from "vitest";
import { ROWS } from "../../src/display/rows";
import { Dataset } from "../../src/dataset/Dataset";
it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); expect(ROWS.length).toBeGreaterThan(0); });
`),
  },
  {
    name: "s53_prefix_ids",
    expected: ["no-test"],
    why: "R4 is not R45",
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", "export const ROWS = [\n", `export const ROWS = [\n${row("R4", "M5.2-min", "src/dataset/Dataset.tsx", "tests/unit/dataset.test.ts", `"dataset"`)}\n`);
    },
  },
  {
    name: "s54_expect_shadowed",
    expected: ["no-test"],
    why: "an expect that is not vitest's",
    edit: dataset(`import { it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
const expect = { hasAssertions: (): void => undefined };
it("R45: dataset", () => { expect.hasAssertions(); void Dataset; });
`),
  },
  {
    name: "s55_options_say_fails",
    expected: ["no-test"],
    why: "options before the body that say fails (vitest 5 refuses options after it)",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", { fails: true }, () => { expect.hasAssertions(); });
`),
  },
  {
    name: "s56_owner_order",
    expected: ["owner-order"],
    why: "later owners built while 2b-1L's lint is not",
    sharedRun: true,
    edit: (tree) => {
      drop(tree, "scripts/raw-text.mjs");
      drop(tree, "tests/unit/raw-text.test.ts");
    },
  },
  {
    name: "s60_duplicate_id",
    expected: ["shape"],
    why: "a row listed twice",
    sharedRun: true,
    edit: (tree) => {
      const line = row("R01", "2b-1a", "src/layout/Text.tsx", "tests/unit/text.test.ts", `"all"`);
      swap(tree, "src/display/rows.ts", line, `${line}\n${line}`);
    },
  },
  {
    name: "s61_test_in_another_file",
    expected: ["no-test"],
    why: "the passed test is in another row's test file, not in its own",
    edit: (tree) => {
      drop(tree, "tests/unit/dataset.test.ts");
      swap(tree, "tests/unit/home.test.ts", `import { Home } from "../../src/catalogue/Home";`, `import { Home } from "../../src/catalogue/Home";\nimport { Dataset } from "../../src/dataset/Dataset";`);
      put(tree, "tests/unit/home.test.ts", `${tree.get("tests/unit/home.test.ts") ?? ""}it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });\n`);
    },
  },
  {
    name: "s62_canary_missing",
    expected: ["canary"],
    why: "the canaries deleted",
    edit: (tree) => {
      drop(tree, CANARY_FILE);
    },
  },
  // The first code review: a row's test that fails and is reported passed, and the rest of its test rules.
  {
    name: "s64_describe_options_fails",
    expected: ["no-test"],
    why: "E: describe(name, { fails: true }, fn) inverts the test inside it",
    edit: dataset(`import { describe, expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
describe("d", { fails: true }, () => {
  it("R45: dataset", () => { expect.hasAssertions(); });
});
`),
  },
  {
    name: "s65_beforeEach_task_fails",
    expected: ["no-test"],
    why: "F: a hook sets the task's fails, and the body throws",
    edit: dataset(`import { beforeEach, expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
beforeEach((ctx) => { ctx.task.fails = true; });
${BROKEN}`),
  },
  {
    name: "s66_afterEach_rewrites_result",
    expected: ["no-test"],
    why: "an afterEach rewrites the failed result to passed",
    edit: dataset(`import { afterEach, expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
afterEach((ctx) => { const result = ctx.task.result; if (result !== undefined) { result.state = "pass"; result.errors = []; } });
${BROKEN}`),
  },
  {
    name: "s67_onTestFinished_rewrites_result",
    expected: ["no-test"],
    why: "onTestFinished, through the context, rewrites the failed result to passed",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", (ctx) => {
  expect.hasAssertions();
  ctx.onTestFinished(() => { const result = ctx.task.result; if (result !== undefined) { result.state = "pass"; result.errors = []; } });
  throw new Error("the module is broken");
});
`),
  },
  {
    name: "s68_ctx_task_fails_in_body",
    expected: ["no-test"],
    why: "the body sets its own task's fails after hasAssertions, then throws",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", (ctx) => { expect.hasAssertions(); ctx.task.fails = true; throw new Error("the module is broken"); });
`),
  },
  {
    name: "s69_describe_each_param_it",
    expected: ["no-test"],
    why: "AC: describe.each([[test.fails]]) hands its it to the callback",
    edit: dataset(`import { describe, expect, it, test } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset; void it;
describe.each([[test.fails]])("d", (it) => {
  it("R45: dataset", () => { expect.hasAssertions(); });
});
`),
  },
  {
    name: "s70_block_scoped_it_fails",
    expected: ["no-test"],
    why: "AD: a block-scoped const it = test.fails",
    edit: dataset(`import { expect, it, test } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset; void it;
{
  const it = test.fails;
  it("R45: dataset", () => { expect.hasAssertions(); });
}
`),
  },
  {
    name: "s71_callback_parameter_unused",
    expected: ["no-test"],
    why: "a row test's callback takes a parameter (the test context), used or not",
    edit: dataset(`${DATASET_HEAD}it("R45: dataset", (c) => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); void c; });
`),
  },
  {
    name: "s72_function_around_the_test",
    expected: ["no-test"],
    why: "a test inside a function that is no plain describe's callback",
    edit: dataset(`${DATASET_HEAD}[1].forEach(() => {
  it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
});
`),
  },
  {
    name: "s73_hasAssertions_on_another_object",
    expected: ["no-test"],
    why: "NM01: a hasAssertions that is not expect's",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset; void expect;
const probe = { hasAssertions: (): void => undefined };
it("R45: dataset", () => { probe.hasAssertions(); });
`),
  },
  {
    name: "s74_dynamic_import_is_the_only_import",
    expected: ["no-test"],
    why: "NM14: the test imports the module only dynamically",
    edit: dataset(`import { expect, it } from "vitest";
it("R45: dataset", async () => { expect.hasAssertions(); const { Dataset } = await import("../../src/dataset/Dataset"); expect(Dataset()).toBe("dataset"); });
`),
  },
  {
    name: "s75_inline_type_import",
    expected: ["no-test"],
    why: "an inline { type X } import names no value (verbatimModuleSyntax keeps it as a side-effect import)",
    edit: dataset(`import { expect, it } from "vitest";
import { type Dataset } from "../../src/dataset/Dataset";
type D = typeof Dataset;
it("R45: dataset", () => { expect.hasAssertions(); const d: D | null = null; expect(d).toBe(null); });
`),
  },
  {
    name: "s76_orphan_in_a_file_no_run_reads",
    expected: ["orphan-title"],
    why: "NM11: a literal title that names no row, in a test file the child run does not run",
    sharedRun: true,
    edit: (tree) => {
      put(tree, "tests/unit/other.test.ts", `import { it } from "vitest";\nit("R99: orphan", () => {});\n`);
    },
  },
  {
    name: "s77_orphan_generated_title",
    expected: ["orphan-title"],
    why: "NM12: a generated title that names no row, seen in the run's results alone",
    edit: (tree) => {
      put(tree, "tests/unit/dataset.test.ts", `${DATASET_TEST}for (const id of ["R99"]) it(\`\${id}: orphan\`, () => { expect.hasAssertions(); expect(1).toBe(1); });\n`);
    },
  },
  {
    name: "s78_canary_text_edited",
    expected: ["canary"],
    why: "the canary file with a comment added: its text is pinned",
    edit: (tree) => {
      put(tree, CANARY_FILE, `${tree.get(CANARY_FILE) ?? ""}// a harmless comment\n`);
    },
  },
  {
    name: "s112_table_read_as_text",
    expected: [],
    why: "residual: the table read as text (it gives no literal title: no-test is the guard)",
    edit: dataset(`import { readFileSync } from "node:fs";
import path from "node:path";
${DATASET_HEAD}const table = readFileSync(path.join("src", "display", "rows.ts"), "utf8");
it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); expect(table).toContain("R45"); });
`),
  },
  {
    name: "s116_row_with_no_test_file",
    expected: ["no-module"],
    why: "NM38: a row with a module and no test file",
    sharedRun: true,
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `module: "src/result/Builder.tsx", test: "tests/unit/builder.test.ts"`, `module: "src/result/Builder.tsx", test: ""`);
    },
  },
  {
    name: "s117_owner_listed_twice",
    expected: ["owner-enum"],
    why: "NM40: an owner listed twice",
    sharedRun: true,
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `"M5.5", "server"]`, `"M5.5", "M5.5", "server"]`);
    },
  },
  {
    name: "s118_id_of_another_form",
    expected: ["shape"],
    why: "NM41: an id that is not R<number> with an optional letter",
    sharedRun: true,
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `id: "R12"`, `id: "R12xy"`);
    },
  },
  {
    name: "s119_sibling_only_skips_the_row_test",
    expected: ["no-test"],
    why: "NM44: a sibling's .only skips the row's own plain test (the form is clean; its status is skipped)",
    edit: (tree) => {
      swap(tree, "vitest.config.mjs", `environment: "node",`, `environment: "node", allowOnly: true,`);
      put(tree, "tests/unit/dataset.test.ts", `${DATASET_HEAD}it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
it.only("another test of the file", () => { expect.hasAssertions(); expect(1).toBe(1); });
`);
    },
  },
  {
    name: "s120_helper_registers_the_hook",
    expected: [],
    why: "RESIDUAL (P01-P03, MA-2): a hook registered by a helper module the row's test imports, which sets fails; only the row's own file is read for names",
    edit: (tree) => {
      put(tree, "tests/unit/helper.ts", `import { beforeEach } from "vitest";\nbeforeEach((ctx) => { ctx.task.fails = true; });\n`);
      put(tree, "tests/unit/dataset.test.ts", `${DATASET_HEAD}import "./helper";\n${BROKEN}`);
    },
  },
  // The second code review: the satisfiers its mutants N03-N23 name, the m-minors, the owner order.
  {
    name: "s121_module_exports_nothing",
    expected: ["empty-module"],
    why: "m5, P14: a built module that is `export {}` and a test of nothing in it",
    edit: (tree) => {
      put(tree, "src/dataset/Datasets.tsx", `export {};\n`);
      put(tree, "tests/unit/datasets.test.ts", `import { expect, it } from "vitest";\nimport * as m from "../../src/dataset/Datasets";\nit("R46: list", () => { expect.hasAssertions(); expect(m).toBeDefined(); });\n`);
    },
  },
  {
    name: "s122_module_with_types_alone",
    expected: ["empty-module"],
    why: "m5: a built module with a type and an interface and no value",
    edit: (tree) => {
      put(tree, "src/dataset/Datasets.tsx", `export type Datasets = string;\nexport interface List { readonly n: number }\n`);
      put(tree, "tests/unit/datasets.test.ts", `import { expect, it } from "vitest";\nimport * as m from "../../src/dataset/Datasets";\nit("R46: list", () => { expect.hasAssertions(); expect(m).toBeDefined(); });\n`);
    },
  },
  {
    name: "s123_onTestFailed_rewrites_result",
    expected: ["no-test"],
    why: "N03: onTestFailed, through a computed key, rewrites the failed result to passed",
    edit: dataset(`import { expect, it, onTestFailed } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", () => { expect.hasAssertions(); onTestFailed((c: any) => { const r = c["ta" + "sk"].result; r.state = "pass"; r.errors = []; }); throw new Error("the module is broken"); });
`),
  },
  {
    name: "s124_task_by_string_in_a_computed_hook",
    expected: ["no-test"],
    why: "N06: a hook named by a string, the task by a string",
    edit: dataset(`import * as v from "vitest";
import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
(v as any)["beforeEach"]((c: any) => { c["task"].fails = true; });
${BROKEN}`),
  },
  {
    name: "s125_hook_by_property_of_a_namespace",
    expected: ["no-test"],
    why: "N23: a hook and the task as properties alone (v.beforeEach, c.task)",
    edit: dataset(`import * as v from "vitest";
import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
v.beforeEach((c: any) => { c.task.fails = true; });
${BROKEN}`),
  },
  {
    name: "s126_aroundEach_sets_fails",
    expected: ["no-test"],
    why: "N04: aroundEach, with its context, sets the task's fails around a body that throws",
    edit: dataset(`import * as v from "vitest";
import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
(v as any).aroundEach(async (run: any, c: any) => { c["ta" + "sk"].fails = true; await run(); });
${BROKEN}`),
  },
  {
    name: "s127_unrelated_parameter_named_expect",
    expected: [],
    why: "M31, the review's probe: a correct test file with a function of an unrelated parameter named expect, which is no redeclaration the test can see",
    edit: dataset(`${DATASET_HEAD}export const helper = (expect: number): number => expect;
it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); expect(helper(1)).toBe(1); });
`),
  },
  {
    name: "s128_it_imported_under_another_name",
    expected: ["no-test"],
    why: "N10: it is vitest's test imported as it (a real test the gate refuses: the name must be the import's own)",
    edit: dataset(`import { expect, test as it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
`),
  },
  {
    name: "s129_describe_with_a_variable_title",
    expected: ["no-test"],
    why: "N11: a describe whose title is no literal (a real test the gate refuses: only describe(literal, fn))",
    edit: dataset(`import { describe, expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
const name = "d";
describe(name, () => {
  it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });
});
`),
  },
  {
    name: "s130_hasAssertions_with_an_argument",
    expected: ["no-test"],
    why: "N12: expect.hasAssertions(1), which is not the call the gate reads (a real test)",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
it("R45: dataset", () => { expect.hasAssertions(1); expect(Dataset()).toBe("dataset"); });
`),
  },
  {
    name: "s131_orphan_behind_a_symlinked_directory",
    expected: ["orphan-title"],
    why: "m7, Q17: an orphan literal title in a directory of tests/ that is a symlink",
    sharedRun: true,
    edit: (tree) => {
      put(tree, "elsewhere/orphan.test.ts", `import { it } from "vitest";\nit("R99: orphan", () => {});\n`);
    },
    links: [["tests/linked", "elsewhere"]],
  },
  {
    name: "s132_symlink_back_to_an_ancestor",
    expected: [],
    why: "m7: a symlink in tests/ to tests/ itself: followed once, no loop",
    sharedRun: true,
    edit: (tree) => {
      put(tree, "tests/unit/touch.txt", "x\n");
    },
    links: [["tests/loop", "tests"]],
  },
  {
    name: "s133_expect_neutered_by_the_file",
    expected: [],
    why: "RESIDUAL (P06, m4): the row's own file makes hasAssertions a no-op for itself, then asserts nothing",
    edit: dataset(`import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
Object.assign(expect, { hasAssertions: () => undefined });
it("R45: dataset", () => { expect.hasAssertions(); });
`),
  },
  // The third code review (the cap): the residuals of the row's own file, the empty module's edges, the cells that kill the survivors.
  {
    name: "s134_computed_hook_in_the_row_file",
    expected: [],
    why: "RESIDUAL (C08, P04): the row's own file registers a hook by a computed name (`[\"before\" + \"Each\"]`) that sets the task's fails by a computed name, and the body throws; the name check sees no runner name",
    edit: dataset(`import * as v from "vitest";
import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
(v as any)["before" + "Each"]((c: any) => { c["ta" + "sk"].fails = true; });
${BROKEN}`),
  },
  {
    name: "s135_arguments_zero_is_the_context",
    expected: [],
    why: "RESIDUAL (C09): a body with no parameter reads the test's context as `arguments[0]` and sets the task's fails by a computed name, then throws",
    edit: dataset(`${DATASET_HEAD}it("R45: dataset", function () { expect.hasAssertions(); (arguments as any)[0]["ta" + "sk"].fails = true; throw new Error("the module is broken"); });
`),
  },
  {
    name: "s136_onTestFailed_by_string_key_object_values",
    expected: [],
    why: "RESIDUAL (C12): onTestFailed by a string key (a string is matched for `task` alone), the task found by Object.values of the context, the failed result rewritten to passed",
    edit: dataset(`import * as v from "vitest";
import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
it("R45: dataset", () => { expect.hasAssertions(); (v as any)["onTestFailed"]((c: any) => { for (const t of Object.values(c) as any[]) { if (t?.result) { t.result.state = "pass"; t.result.errors = []; } } }); throw new Error("the module is broken"); });
`),
  },
  {
    name: "s137_beforeEach_by_string_key_object_values",
    expected: [],
    why: "RESIDUAL (C13): beforeEach by a string key, the task found by Object.values of the context, its fails set, and the body throws",
    edit: dataset(`import * as v from "vitest";
import { expect, it } from "vitest";
import { Dataset } from "../../src/dataset/Dataset";
void Dataset;
(v as any)["beforeEach"]((c: any) => { for (const t of Object.values(c) as any[]) { if (t && typeof t === "object" && "mode" in t) { t.fails = true; } } });
${BROKEN}`),
  },
  {
    name: "s138_module_with_declare_alone",
    expected: ["empty-module"],
    why: "X10 (E08): a built module whose only export is `export declare const` (it emits nothing)",
    edit: (tree) => {
      put(tree, "src/dataset/Datasets.tsx", `export declare const x: number;\n`);
      put(tree, "tests/unit/datasets.test.ts", `import { expect, it } from "vitest";\nimport * as m from "../../src/dataset/Datasets";\nit("R46: list", () => { expect.hasAssertions(); expect(m).toBeDefined(); });\n`);
    },
  },
  {
    name: "s139_export_star_of_types_only",
    expected: [],
    why: "RESIDUAL (E01, m1): `export *` of a module that holds a type alone counts as a value export",
    edit: (tree) => {
      put(tree, "src/dataset/types.ts", `export interface List { readonly n: number }\n`);
      put(tree, "src/dataset/Datasets.tsx", `export * from "./types";\n`);
      put(tree, "tests/unit/datasets.test.ts", `import { expect, it } from "vitest";\nimport * as m from "../../src/dataset/Datasets";\nit("R46: list", () => { expect.hasAssertions(); expect(m).toBeDefined(); });\n`);
    },
  },
  {
    name: "s140_namespace_of_types_only",
    expected: [],
    why: "RESIDUAL (E02, m1): an `export namespace` that holds a type alone counts as a value export",
    edit: (tree) => {
      put(tree, "src/dataset/Datasets.tsx", `export namespace N { export type T = string; }\n`);
      put(tree, "tests/unit/datasets.test.ts", `import { expect, it } from "vitest";\nimport * as m from "../../src/dataset/Datasets";\nit("R46: list", () => { expect.hasAssertions(); expect(m).toBeDefined(); });\n`);
    },
  },
  {
    name: "s141_title_with_the_id_not_first",
    expected: ["no-test"],
    why: "X14: a title that holds the id after other words (\"x R45: ...\") is no test of R45, nor an orphan (it does not start R<digit>)",
    edit: (tree) => {
      swap(tree, "tests/unit/dataset.test.ts", `"R45: dataset"`, `"x R45: dataset"`);
    },
  },
  {
    name: "s142_blank_non_code_module",
    expected: ["empty-module", "no-test"],
    why: "X11: a row whose module is a blank file that is no code (R95's docs/ui-scenarios.md before it is written): blank is empty, and the row's test does not import it",
    sharedRun: true,
    edit: (tree) => {
      swap(tree, "src/display/rows.ts", `module: "src/dataset/Datasets.tsx"`, `module: "docs/list.md"`);
      put(tree, "docs/list.md", "\n");
    },
  },
  {
    name: "s143_the_childs_environment_has_no_parent_vitest_variable",
    expected: [],
    why: "X15 (M86): the row's test asserts the child sees no VITEST_D427_PROBE; display.test.ts runs the gate with that variable set in the parent, where the original passes and the mutant fails",
    edit: dataset(`${DATASET_HEAD}it("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); expect(process.env["VITEST_D427_PROBE"]).toBeUndefined(); });
`),
  },
];

// ---------------------------------------------------------------------------------------------
// One cell per runner name (X03-X06 and the like): the name alone in the row's own file, no `task`
// and no `ctx` beside it, so that dropping one name from the gate's list fails exactly one cell. The
// names are written out here, not read from the gate: a name dropped there must not drop its cell.

const RUNNER_NAME_CELLS: readonly Fixture[] = ["beforeEach", "afterEach", "beforeAll", "afterAll", "onTestFinished", "onTestFailed", "aroundEach", "aroundAll", "task", "ctx"].map(
  (name): Fixture => ({
    name: `runner_name_${name}`,
    expected: ["no-test"],
    why: `the name ${name} alone in the row's own test file (a name check, whatever it is used for)`,
    edit: dataset(`${DATASET_HEAD}const ${name} = 0;\nvoid ${name};\nit("R45: dataset", () => { expect.hasAssertions(); expect(Dataset()).toBe("dataset"); });\n`),
  }),
);

// ---------------------------------------------------------------------------------------------
// Owner order, every owner built alone (m8, N29): the first owner passes, every later owner fails.

const OWNER_ORDER = ["2b-1a", "2b-1L", "2b-1b", "M5.2-min", "M5.3-min", "M5.4-min", "M5.5"] as const;

const OWNER_CELLS: readonly Fixture[] = OWNER_ORDER.map((owner, index): Fixture => ({
  name: `owner_alone_${owner}`,
  expected: index === 0 ? [] : ["owner-order"],
  why: index === 0 ? `owner ${owner}, the first, built alone: nothing lands before it` : `owner ${owner} built alone: an owner before it has a row not built`,
  edit: (tree) => {
    for (const base of BASE_ROWS) {
      if (base.owner !== owner) {
        tree.delete(base.module);
        tree.delete(base.test);
      }
    }
    const mine = BASE_ROWS.filter((base) => base.owner === owner);
    for (const base of mine) {
      if (!tree.has(base.module)) {
        put(tree, base.module, moduleText(base));
      }
    }
    for (const test of new Set(mine.map((base) => base.test))) {
      if (!tree.has(test)) {
        put(tree, test, testText(mine.filter((base) => base.test === test)));
      }
    }
  },
}));

// ---------------------------------------------------------------------------------------------
// Owner complete, every owner with two rows built partly (X28): the owners before it built, its own
// first row built and its second not. Only the owners with two rows or more can be partly built.

const PARTLY_CELLS: readonly Fixture[] = OWNER_ORDER.flatMap((owner, index): Fixture[] => {
  if (BASE_ROWS.filter((base) => base.owner === owner).length < 2) {
    return [];
  }
  const built = BASE_ROWS.filter((base) => OWNER_ORDER.indexOf(base.owner as (typeof OWNER_ORDER)[number]) < index || base === BASE_ROWS.find((candidate) => candidate.owner === owner));
  return [
    {
      name: `owner_partly_${owner}`,
      expected: ["owner-complete"],
      why: `owner ${owner} has its first row built and its second not, the owners before it built: owner-complete alone`,
      edit: (tree) => {
        for (const base of BASE_ROWS) {
          tree.delete(base.module);
          tree.delete(base.test);
        }
        for (const base of built) {
          put(tree, base.module, moduleText(base));
        }
        for (const test of new Set(built.map((base) => base.test))) {
          put(tree, test, testText(built.filter((base) => base.test === test)));
        }
      },
    },
  ];
});

export const ALL_FIXTURES: readonly Fixture[] = [...FIXTURES, ...RUNNER_NAME_CELLS, ...OWNER_CELLS, ...PARTLY_CELLS];

/** The mutants of the gate, each with the fixtures that must kill it (the other half of the table is `EQUIVALENT`). */
export const KILLS: Readonly<Record<string, readonly string[]>> = {
  M1: ["s15_assertions_zero_after", "s23r_stale_results_file", "s119_sibling_only_skips_the_row_test"],
  M2: ["s05_reexport_barrel_row", "s13_type_only_import", "s74_dynamic_import_is_the_only_import"],
  M3: ["s13_type_only_import", "s75_inline_type_import"],
  M4: ["s01_empty_body", "s34_hasAssertions_in_dead_branch", "s54_expect_shadowed", "s55_options_say_fails"],
  M7: ["s06_title_typo", "s29_title_names_no_row"],
  M8: ["s26_screens_empty"],
  M9: ["s27_owner_without_row", "s28_row_owner_not_enum"],
  M10: ["s30_prefix_title", "s53_prefix_ids"],
  M11: ["s18_folded", "s40_blank_module"],
  M12: ["s16_dead_literal_plus_loop"],
  M13: ["s14_test_fails", "s44_it_concurrent"],
  M15: ["s23r_stale_results_file"],
  M16: ["s33_dead_literal_same_line"],
  M17: ["s31_shadowed_it_is_test_fails", "s32_it_from_a_shim", "s70_block_scoped_it_fails"],
  M19: ["s40b_blank_module_unstarted_owner"],
  M20: ["s49_setup_neuters_hasAssertions", "s62_canary_missing"],
  M21: ["s56_owner_order", "owner_alone_2b-1L", "owner_alone_M5.5"],
  M23: ["s54_expect_shadowed"],
  M24: ["s55_options_say_fails"],
  M25: ["s61_test_in_another_file"],
  M28: ["s20_screen_row_server_owner"],
  M29: ["s60_duplicate_id"],
  M30: ["s73_hasAssertions_on_another_object"],
  M31: ["s127_unrelated_parameter_named_expect"],
  M32: ["s70_block_scoped_it_fails"],
  M38: ["s76_orphan_in_a_file_no_run_reads"],
  M39: ["s77_orphan_generated_title"],
  M41: ["s74_dynamic_import_is_the_only_import"],
  M43: ["s06_title_typo"],
  M44: ["s51_task_location_off"],
  M45: ["s49_setup_neuters_hasAssertions"],
  M46: ["base"],
  M50: ["s116_row_with_no_test_file"],
  M51: ["s117_owner_listed_twice"],
  M52: ["s118_id_of_another_form"],
  M55: ["s119_sibling_only_skips_the_row_test"],
  M56: ["s71_callback_parameter_unused"],
  M57: ["s42_describe_each_literal_it", "s64_describe_options_fails", "s72_function_around_the_test"],
  M58: ["s65_beforeEach_task_fails", "s66_afterEach_rewrites_result"],
  M59: ["s42_describe_each_literal_it"],
  M60: ["s64_describe_options_fails"],
  M72: ["s78_canary_text_edited"],
  M75: ["s123_onTestFailed_rewrites_result"],
  M76: ["s124_task_by_string_in_a_computed_hook"],
  M77: ["s125_hook_by_property_of_a_namespace"],
  M78: ["s128_it_imported_under_another_name"],
  M79: ["s129_describe_with_a_variable_title"],
  M80: ["s130_hasAssertions_with_an_argument"],
  M81: ["owner_alone_M5.5"],
  M82: ["s121_module_exports_nothing", "s122_module_with_types_alone"],
  M83: ["s122_module_with_types_alone"],
  M84: ["s131_orphan_behind_a_symlinked_directory"],
  M85: ["s126_aroundEach_sets_fails"],
  M86: ["s143_the_childs_environment_has_no_parent_vitest_variable"],
};

/** Mutants no fixture can kill, each with why, and the fixtures on which it is shown to change no verdict (`sharesRun`: the fixture's recorded run serves, the mutant being about the tree alone). */
export const EQUIVALENT: Readonly<Record<string, { readonly reason: string; readonly fixtures: readonly string[]; readonly sharesRun: boolean }>> = {
  M40: {
    reason: "The child's report is written to a directory this run makes fresh (mkdtemp), so a stale report cannot be there; the check is the belt of that.",
    fixtures: ["base", "s23r_stale_results_file", "s49_setup_neuters_hasAssertions"],
    sharesRun: false,
  },
};
