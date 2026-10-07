/**
 * The build's gate (`plugins/gate.ts`) on real Vite builds of synthetic two-entry projects,
 * written into a temporary directory with stub packages under their own `node_modules/`, and on
 * the real skeleton's build (the positive case: a strict allow-list must not refuse React's own
 * closure and Vite's helpers).
 */
import { readFileSync, realpathSync, writeFileSync } from "node:fs";
import path from "node:path";

import { build, type Plugin, type Rolldown } from "vite";
import { afterAll, describe, expect, it } from "vitest";

import {
  ASSET_EXTENSIONS,
  assetProblems,
  gateProblems,
  type GateChunk,
  noWorker,
  operatorClosure,
  OPERATOR_QUERIES,
  operatorProblem,
  packagesOf,
  VIRTUAL_MODULES,
} from "../../plugins/gate";
import { ASSET_FILE_NAMES, CHUNK_FILE_NAMES, ENTRY_FILE_NAMES, sanitizeFileName } from "../../plugins/names";
import { EXTENSIONS } from "../../scripts/check-bundle.mjs";
import { checkBudget, readPolicy } from "../../scripts/policy-files.mjs";
import { buildConfig } from "../../vite.config";
import { cleanup, gated, project, refused, virtual } from "./synthetic";

const WEB = path.join(import.meta.dirname, "../..");

afterAll(cleanup);

describe("packagesOf", () => {
  it.each([
    ["/w/node_modules/react/index.js", ["react"]],
    ["/w/node_modules/react-dom/node_modules/vega-util/index.js", ["react-dom", "vega-util"]],
    ["/w/node_modules/vega-lite/node_modules/react/chart.js", ["vega-lite", "react"]],
    ["/w/node_modules/@scope/pkg/x.js", ["@scope/pkg"]],
    ["/w/node_modules/react/node_modules/@scope/pkg/x.js", ["react", "@scope/pkg"]],
    ["/w/node_modules/.pnpm/vega@1/node_modules/vega/x.js", [".pnpm", "vega"]],
    ["/w/node_modules/@scope", [""]],
    ["/w/node_modules/react/node_modules/", ["react", ""]],
    ["/w/src/main.ts", []],
    ["/w/my_node_modules/react/x.js", []],
    ["/w/node_modules_x/react/x.js", []],
    ["/w/src/chart/node_modules/react/x.js", ["react"]],
    ["node_modules/react/index.js", ["react"]],
    ["/w/node_modules//react/x.js", [""]],
    ["/w/node_modules/@scope//x.js", [""]],
  ])("%s is in %j", (id, names) => {
    expect(packagesOf(id)).toEqual(names);
  });
});

describe("operatorProblem", () => {
  const root = "/w";
  it.each([
    "/w/node_modules/react/index.js",
    "/w/node_modules/react-dom/client.js",
    "/w/node_modules/scheduler/index.js",
    "/w/node_modules/react-dom/node_modules/scheduler/index.js",
    "/w/node_modules/react/node_modules/react-dom/node_modules/scheduler/x.js",
    "/w/src/operator/main.tsx",
    "/w/src/my_node_modules/react/x.js",
    "/w/src/chart2/x.ts",
    "/w/operator.html",
    ...VIRTUAL_MODULES,
  ])("admits %s", (id) => {
    expect(operatorProblem(root, id)).toBeNull();
  });
  it.each([
    ["/w/node_modules/vega-util/index.js", "vega-util"],
    ["/w/node_modules/react-dom/node_modules/vega-util/index.js", "vega-util"],
    ["/w/node_modules/vega-lite/node_modules/react/chart.js", "vega-lite"],
    ["/w/node_modules/react/node_modules/vega-util/node_modules/scheduler/x.js", "vega-util"],
    ["/w/node_modules/react/node_modules/", '""'],
    ["/w/node_modules/@scope/pkg/node_modules/react/x.js", "@scope/pkg"],
    ["/w/node_modules/react-router/index.js", "react-router"],
    ["/w/node_modules/@scope/react/index.js", "@scope/react"],
    ["/w/src/chart/Chart.tsx", "chart"],
    ["/w/src/chart", "chart"],
    ["/w/src/chart/node_modules/react/x.js", "a node_modules directory that is not the project's own"],
    ["/w/src/foo/node_modules/x/y.js", "a node_modules directory that is not the project's own"],
    ["/w/src/node_modules/react/index.js", "a node_modules directory that is not the project's own"],
    ["/w/src/chart/my_node_modules/react/x.js", 'the chart module "src/chart/my_node_modules/react/x.js"'],
    ["/elsewhere/node_modules/react/x.js", "outside the allow-list"],
    ["/w/../elsewhere/node_modules/react/x.js", "not a normal path"],
    ["/w/src/../src/main.ts", "not a normal path"],
    ["/w/src//main.ts", "not a normal path"],
    ["/w/src/./main.ts", "not a normal path"],
    ["/w/src/chart/../main.ts", "not a normal path"],
    ["/w/src/a.css?used", "a query or fragment"],
    ["/w/src/main.ts?raw", "a query or fragment"],
    ["/w/src/main.ts#x", "a query or fragment"],
    ["/w/node_modules/react/index.js?v=1", "a query or fragment"],
    ["/w/my_node_modules/react/x.js", "outside the allow-list"],
    ["/w/lib/node_modules/react/x.js", "a node_modules directory that is not the project's own"],
    ["/w/node_modules", '""'],
    ["node_modules/react/x.js", "outside the allow-list"],
    ["\0vite/other.js", "virtual"],
    ["\0rolldown/runtime.js?x", "virtual"],
    ["\0commonjs-proxy:/w/node_modules/react/index.js", "virtual"],
    ["/w/index.html", "outside the allow-list"],
    ["/elsewhere/src/x.ts", "outside the allow-list"],
    ["/w/srcx/x.ts", "outside the allow-list"],
    ["relative/x.ts", "outside the allow-list"],
  ])("refuses %s (%s)", (id, why) => {
    expect(operatorProblem(root, id)).toContain(why);
  });
});

/** Suffixes of a module id that a loader ignores and a path normaliser reads. */
const SUFFIXES = ["?raw", "?url&inline", "?/../../x", "?/../..", "?x/../../..", "?x=/../src/a.ts", "#/../..", "#x", "?", "#"];

describe("a suffix is no part of a module's place", () => {
  const root = "/w";
  const BARE = [
    "/w/node_modules/react/index.js",
    "/w/node_modules/cookie-es/dist/index.mjs",
    "/w/node_modules/react-router/index.js",
    "/w/node_modules/react-dom/node_modules/vega-util/index.js",
    "/w/src/operator/main.tsx",
    "/w/src/chart/Chart.tsx",
    "/w/src/harness/Harness.tsx",
    "/w/src/chart/node_modules/react/x.js",
    "/w/operator.html",
    "/w/index.html",
    "/w/root.ts",
    "/w/src/root.ts",
    "/elsewhere/x.ts",
  ];

  it.each(BARE.flatMap((id) => SUFFIXES.map((suffix) => [id, suffix] as const)))(
    "%s%s is not admitted where the bare id is refused, and nothing with a suffix is admitted",
    (id, suffix) => {
      const bare = operatorProblem(root, id);
      const suffixed = operatorProblem(root, `${id}${suffix}`);
      if (bare !== null) {
        expect(suffixed).not.toBeNull();
      }
      expect(suffixed).not.toBeNull();
    },
  );

  it.each([
    "/w/node_modules/cookie-es/dist/index.mjs?/../../../../src/root.ts",
    "/w/node_modules/react/index.js?/../../../../src/root.ts",
    "/w/src/chart/c.ts?/../../root.ts",
  ])("%s is refused as a query", (id) => {
    expect(operatorProblem(root, id)).toContain("a query or fragment");
  });

  it("lists no query, as the real builds make none", () => {
    expect([...OPERATOR_QUERIES]).toEqual([]);
  });

  const chunk = (moduleIds: string[]): GateChunk => ({
    fileName: "assets/c-AAAAAAAA.js",
    isEntry: true,
    facadeModuleId: "/w/operator.html",
    imports: [],
    dynamicImports: [],
    moduleIds: ["/w/operator.html", ...moduleIds],
  });

  it.each(SUFFIXES)("a harness module with %s is still the harness, in any chunk", (suffix) => {
    const ids = [`/w/src/harness/h.ts${suffix}`, `/w/src/harness/h.ts${suffix}`.replace("/w/", "/w/src/../")];
    for (const id of ids) {
      const problems = gateProblems(root, [chunk([id])], { harness: false });
      expect(problems.some((problem) => problem.includes("harness module") || problem.includes("not a normal path"))).toBe(true);
    }
    expect(gateProblems(root, [chunk([`/w/src/harness/h.ts${suffix}`])], { harness: false })[0]).toContain('the harness module "src/harness/h.ts"');
  });

  it("refuses a module whose path is not normal in any chunk, not only the operator's", () => {
    const catalogue: GateChunk = { ...chunk(["/w/src/x/../harness/h.ts"]), fileName: "assets/i-AAAAAAAA.js", isEntry: false, facadeModuleId: null };
    expect(gateProblems(root, [chunk([]), catalogue], { harness: false })).toEqual([
      expect.stringContaining('assets/i-AAAAAAAA.js: the module "/w/src/x/../harness/h.ts", whose file is not a normal path'),
    ]);
  });
});

const REFUSED_ASSET =
  "a file of a type the loader does not serve (a script that is no chunk of the build, for one), which the gate cannot read";

describe("assetProblems", () => {
  it("refuses a script, whatever its extension, and any file the loader does not serve", () => {
    for (const extension of ["js", "mjs", "ts", "cjs", "jsx"]) {
      expect(assetProblems([`assets/w-AAAAAAAA.${extension}`])).toEqual([
        `assets/w-AAAAAAAA.${extension}: ${REFUSED_ASSET}`,
      ]);
    }
    expect(assetProblems(["assets/j.json", "assets/f.woff2", "assets/noext", "assets/i.SVG", "other.html", "assets.css", ".vite/other.json"])).toHaveLength(7);
  });

  it("admits the loader's types but a script, and the bundle's own root files", () => {
    expect(assetProblems(["assets/a-AAAAAAAA.css", "assets/i-AAAAAAAA.svg", "assets/p-AAAAAAAA.png", ".vite/manifest.json", "index.html", "operator.html"])).toEqual([]);
  });

  it("is the server loader's list but for .js, which only a chunk is", () => {
    const server = readFileSync(path.join(WEB, "../server/src/aibi/core/api/bundle.py"), "utf8");
    const table = /CONTENT_TYPES[^=]*=\s*MappingProxyType\(\s*\{([^}]*)\}/u.exec(server)?.[1] ?? "";
    const extensions = [...table.matchAll(/"(\.[a-z0-9]+)":/gu)].map((found) => found[1]);
    expect(extensions.sort()).toEqual([".css", ".js", ".png", ".svg"]);
    expect([...EXTENSIONS].sort()).toEqual(extensions);
    expect([...ASSET_EXTENSIONS].sort()).toEqual(extensions.filter((extension) => extension !== ".js"));
  });
});

describe("gateProblems on chunks", () => {
  const root = "/w";
  const entry = (more: Partial<GateChunk>): GateChunk => ({
    fileName: "assets/operator-AAAAAAAA.js",
    isEntry: true,
    facadeModuleId: "/w/operator.html",
    imports: [],
    dynamicImports: [],
    moduleIds: ["/w/operator.html", "/w/src/operator.ts"],
    ...more,
  });
  const chunk = (fileName: string, moduleIds: string[]): GateChunk => ({
    fileName,
    isEntry: false,
    facadeModuleId: null,
    imports: [],
    dynamicImports: [],
    moduleIds,
  });

  it("walks dynamic imports as well as static ones", () => {
    const lazy = chunk("assets/lazy-AAAAAAAA.js", ["/w/node_modules/vega-util/index.js"]);
    expect(gateProblems(root, [entry({ dynamicImports: [lazy.fileName] }), lazy], { harness: true })).toHaveLength(1);
    expect(gateProblems(root, [entry({ imports: [lazy.fileName] }), lazy], { harness: true })).toHaveLength(1);
  });

  it("walks a chain of imports to its end", () => {
    const deep = chunk("assets/deep-AAAAAAAA.js", ["\0vite/other.js"]);
    const middle = { ...chunk("assets/mid-AAAAAAAA.js", ["/w/src/m.ts"]), dynamicImports: [deep.fileName] };
    const problems = gateProblems(root, [entry({ imports: [middle.fileName] }), middle, deep], { harness: true });
    expect(problems).toEqual([expect.stringContaining("assets/deep-AAAAAAAA.js")]);
  });

  it("judges modules, not chunk names", () => {
    const shared = chunk("assets/react-AAAAAAAA.js", ["/w/node_modules/react/index.js", "/w/node_modules/vega/index.js"]);
    expect(gateProblems(root, [entry({ imports: [shared.fileName] }), shared], { harness: true })).toEqual([
      expect.stringContaining('the package "vega"'),
    ]);
  });

  it("refuses a build without exactly one operator entry, or whose graph names a missing chunk", () => {
    expect(operatorClosure(root, [entry({ facadeModuleId: "operator.html" })])).toContain("0 chunks");
    expect(operatorClosure(root, [entry({ isEntry: false })])).toContain("0 chunks");
    expect(operatorClosure(root, [entry({}), entry({ fileName: "assets/other-AAAAAAAA.js" })])).toContain("2 chunks");
    expect(gateProblems(root, [entry({ imports: ["assets/gone-AAAAAAAA.js"] })], { harness: true })).toEqual([
      expect.stringContaining("which the build lacks"),
    ]);
  });

  it("finds the harness in any chunk unless the build may hold it", () => {
    const catalogue = chunk("assets/index-AAAAAAAA.js", ["/w/src/harness/h.ts"]);
    expect(gateProblems(root, [entry({}), catalogue], { harness: false })).toEqual([
      expect.stringContaining("the harness module"),
    ]);
    expect(gateProblems(root, [entry({}), catalogue], { harness: true })).toEqual([]);
  });
});

describe("the gate on real builds of a synthetic project", () => {
  it("passes a legitimate build: React's stubs, the app's src/, a lazy chunk", async () => {
    const root = project({
      "src/operator.ts":
        "import { react } from 'react'; import { scheduler } from 'scheduler'; import { shared } from './shared.ts';" +
        " document.title = react + scheduler + shared; void import('./lazy.ts');",
      "src/index.ts": "import { shared } from './shared.ts'; document.title = shared;",
      "src/shared.ts": "export const shared = 'shared ' + Math.random();",
      "src/lazy.ts": "export const lazy = 'lazy';",
    });
    const found = await gated(root);
    expect("chunks" in found ? found.chunks.length : found.error).toBeGreaterThan(2);
  });

  it("refuses a chart package the operator imports statically", async () => {
    const root = project({ "src/operator.ts": "import { vega } from 'vega-util'; document.title = vega;" });
    expect(await refused(root)).toContain('the package "vega-util"');
  });

  it("refuses a chart package only a lazy import of the operator reaches", async () => {
    const root = project({
      "src/operator.ts": "void import('./later.ts');",
      "src/later.ts": "import { vega } from 'vega-util'; export const later = vega;",
    });
    expect(await refused(root)).toContain('the package "vega-util"');
  });

  it("refuses a chart package inside a chunk the operator shares with the catalogue", async () => {
    const root = project({
      "src/operator.ts": "import { both } from './both.ts'; document.title = both;",
      "src/index.ts": "import { both } from './both.ts'; document.title = both + 'i';",
      "src/both.ts": "import { vega } from 'vega-util'; export const both = vega;",
    });
    expect(await refused(root)).toContain('the package "vega-util"');
  });

  it("refuses a package nested under an allowed one, and an allowed one nested under a chart package", async () => {
    const nested = project({ "src/operator.ts": "import { dom } from 'react-dom'; document.title = dom;" });
    expect(await refused(nested)).toContain('the package "vega-util"');
    const mirror = project({ "src/operator.ts": "import { lite } from 'vega-lite'; document.title = lite;" });
    expect(await refused(mirror)).toContain('the package "vega-lite"');
  });

  it("refuses a worker the operator starts, whose chart code Vite emits as an asset, not a chunk", async () => {
    const root = project({
      "src/operator.ts": "new Worker(new URL('./chart/w.ts', import.meta.url), { type: 'module' });",
      "src/chart/w.ts": "import { vega } from 'vega-util'; postMessage(vega);",
    });
    expect(await refused(root)).toMatch(/assets\/w-?[A-Za-z0-9_-]*\.js: a file of a type the loader does not serve/u);
  });

  it("refuses the harness in a worker of a production build, and any worker of an end-to-end one", async () => {
    const root = project({
      "src/index.ts": "new Worker(new URL('./harness/h.ts', import.meta.url), { type: 'module' });",
      "src/harness/h.ts": "postMessage('harness ' + Math.random());",
    });
    expect(await refused(root)).toContain("a script that is no chunk");
    expect(await refused(root, true)).toContain("a script that is no chunk");
  });

  it("refuses a scoped package, a router and a chart module", async () => {
    expect(await refused(project({ "src/operator.ts": "import { charts } from '@charts/core'; document.title = charts;" }))).toContain(
      '"@charts/core"',
    );
    expect(await refused(project({ "src/operator.ts": "import { router } from 'react-router'; document.title = router;" }))).toContain(
      '"react-router"',
    );
    const chart = project({
      "src/operator.ts": "import { chart } from './chart/Chart.ts'; document.title = chart;",
      "src/chart/Chart.ts": "export const chart = 'chart ' + Math.random();",
    });
    expect(await refused(chart)).toContain('the chart module "src/chart/Chart.ts"');
  });

  it("refuses a virtual module other than Vite's and Rolldown's own", async () => {
    const root = project({ "src/operator.ts": "import { v } from 'virtual:aibi'; document.title = v;" });
    expect(await refused(root, false, [virtual])).toContain('the virtual module "\\u0000aibi-virtual"');
  });

  it("refuses a module outside src/, in the project or outside it", async () => {
    const top = project({
      "src/operator.ts": "import { top } from '../top.js'; document.title = top;",
      "top.js": "export const top = 'top ' + Math.random();",
    });
    expect(await refused(top)).toContain("top.js\", outside the allow-list");
    const root = project({ "src/operator.ts": "import { outside } from '../../outside.js'; document.title = outside;" });
    writeFileSync(path.join(root, "..", "outside.js"), "export const outside = 'outside ' + Math.random();");
    expect(await refused(root)).toContain("outside.js\", outside the allow-list");
  });

  it("refuses the harness imported statically by the operator, or by the catalogue", async () => {
    const operator = project({
      "src/operator.ts": "import { marker } from './harness/h.ts'; document.title = marker;",
      "src/harness/h.ts": "export const marker = 'harness ' + Math.random();",
    });
    expect(await refused(operator)).toContain('the harness module "src/harness/h.ts"');
    const catalogue = project({
      "src/index.ts": "import { marker } from './harness/h.ts'; document.title = marker;",
      "src/harness/h.ts": "export const marker = 'harness ' + Math.random();",
    });
    expect(await refused(catalogue)).toContain('the harness module "src/harness/h.ts"');
    expect("chunks" in (await gated(catalogue, true))).toBe(true);
  });

  it("refuses a node_modules directory that is not the project's own, under src/chart/ or elsewhere in src/", async () => {
    const chart = project({
      "src/operator.ts": "import { chart } from './chart/node_modules/react/x.ts'; document.title = chart;",
      "src/chart/node_modules/react/x.ts": "export const chart = 'chart ' + Math.random();",
    });
    expect(await refused(chart)).toContain("src/chart/node_modules/react/x.ts\", in a node_modules directory that is not the project's own");
    const foo = project({
      "src/operator.ts": "import { foo } from './foo/node_modules/x/y.ts'; document.title = foo;",
      "src/foo/node_modules/x/y.ts": "export const foo = 'foo ' + Math.random();",
    });
    expect(await refused(foo)).toContain("src/foo/node_modules/x/y.ts\", in a node_modules directory that is not the project's own");
    const mirror = project({
      "src/operator.ts": "import { ok } from './my_node_modules/x.ts'; document.title = ok;",
      "src/my_node_modules/x.ts": "export const ok = 'ok';",
    });
    expect("chunks" in (await gated(mirror))).toBe(true);
  });

  it.each(["?/../../root.js", "?x/../../../root.js", "#/../.."])(
    "refuses an operator import whose query %s would place it elsewhere under a path normaliser",
    async (suffix) => {
      const root = project({
        "src/operator.ts": `import { react } from 'react${suffix}'; document.title = react;`,
        "src/root.ts": "export const root = 'root';",
      });
      expect(await refused(root)).toContain("a query or fragment");
    },
  );

  it.each(["?raw", "?url&inline"])("refuses a module of src/ imported with %s", async (suffix) => {
    const root = project({
      "src/operator.ts": `import text from './text.txt${suffix}'; document.title = String(text);`,
      "src/text.txt": "text",
    });
    expect(await refused(root)).toContain("a query or fragment");
  });

  it("refuses a chart module imported with a query that would place it elsewhere", async () => {
    const root = project({
      "src/operator.ts": "import { chart } from './chart/Chart.ts?/../../root.ts'; document.title = chart;",
      "src/chart/Chart.ts": "export const chart = 'chart ' + Math.random();",
      "src/root.ts": "export const root = 'root';",
    });
    expect(await refused(root)).toContain("a query or fragment");
  });

  it("refuses the harness imported with a query that would place it elsewhere, in a production build", async () => {
    const root = project({
      "src/index.ts": "import { marker } from './harness/h.ts?/../../root.ts'; document.title = marker;",
      "src/harness/h.ts": "export const marker = 'harness ' + Math.random();",
      "src/root.ts": "export const root = 'root';",
    });
    expect(await refused(root)).toContain('the harness module "src/harness/h.ts"');
  });

  it("refuses a raw script a build emits as an asset, which no worker made", async () => {
    const root = project({
      "src/index.ts": "import u from './w.cjs?url'; document.title = u;",
      "src/w.cjs": "postMessage(1);",
    });
    expect(await refused(root)).toMatch(/assets\/w-?[A-Za-z0-9_-]*\.cjs: a file of a type the loader does not serve/u);
  });

  it("names every file after the loader's grammar, whatever the module's name", async () => {
    const root = project({
      "src/operator.ts": "void import('./-lazy @~(é).ts'); void import('./.dot.ts'); import './-we ird@.css';",
      "src/-lazy @~(é).ts": "export const lazy = 'lazy ' + Math.random();",
      "src/.dot.ts": "export const dot = 'dot ' + Math.random();",
      "src/-we ird@.css": "p { color: red; }",
    });
    const output = await build({
      root,
      configFile: false,
      logLevel: "silent",
      publicDir: false,
      build: {
        write: false,
        rolldownOptions: {
          input: { index: path.join(root, "index.html"), operator: path.join(root, "operator.html") },
          output: {
            sanitizeFileName,
            entryFileNames: "assets/[name]-[hash].js",
            chunkFileNames: "assets/[name]-[hash].js",
            assetFileNames: "assets/[name]-[hash][extname]",
          },
        },
      },
    });
    const outputs = (Array.isArray(output) ? output : [output]) as Rolldown.RolldownOutput[];
    const names = outputs.flatMap((found) => found.output.map((item) => item.fileName)).filter((name) => name.startsWith("assets/"));
    expect(names.length).toBeGreaterThanOrEqual(5);
    for (const name of names) {
      expect(name.slice("assets/".length)).toMatch(/^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$/u);
    }
  });
});

/** The six ways to ask Vite for a worker, each from the module that starts it. */
const WORKER_FORMS: [string, string][] = [
  ["?worker", "import W from './w.ts?worker'; new W();"],
  ["?worker&url", "import u from './w.ts?worker&url'; new Worker(u);"],
  ["?worker&inline", "import W from './w.ts?worker&inline'; new W();"],
  ["?sharedworker&inline", "import W from './w.ts?sharedworker&inline'; new W();"],
  ["new Worker(new URL(...))", "new Worker(new URL('./w.ts', import.meta.url), { type: 'module' });"],
  ["new SharedWorker(new URL(...))", "new SharedWorker(new URL('./w.ts', import.meta.url), { type: 'module' });"],
];

describe("the gate refuses any worker, through the skeleton's own configuration", () => {
  /** A build of a synthetic project under `vite.config.ts`'s configuration of a mode. */
  async function built(root: string, mode: "production" | "e2e"): Promise<string | null> {
    const config = buildConfig(mode);
    try {
      await build({
        ...config,
        root,
        configFile: false,
        logLevel: "silent",
        build: { ...config.build, write: false },
      });
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : "a build failed without an Error";
    }
  }

  const ENTRIES = ["src/index.ts", "src/operator.ts"] as const;
  const MODES = ["production", "e2e"] as const;

  it.each(MODES)("passes a build with no worker in %s mode (so that a refusal below is the worker's)", async (mode) => {
    expect(await built(project({ "src/w.ts": "postMessage(1);" }), mode)).toBeNull();
  });

  describe.each(WORKER_FORMS)("%s", (_form, code) => {
    it.each(ENTRIES.flatMap((entry) => MODES.map((mode) => [entry, mode] as const)))(
      "is refused from %s in %s mode",
      async (entry, mode) => {
        const root = project({ [entry]: code, "src/w.ts": "postMessage(1);" });
        const error = await built(root, mode);
        expect(error).toContain("The build's gate refuses it");
        expect(error).toContain("the build has a worker");
      },
    );
  });

  it("holds the worker gate in the configuration of both modes, and it fails a worker's build", () => {
    for (const mode of MODES) {
      const plugins = buildConfig(mode).worker?.plugins?.() ?? [];
      expect(plugins.map((one) => (one as Plugin).name)).toEqual(["aibi:no-worker"]);
    }
    const hook = noWorker().buildStart as (this: { error: (message: string) => never }) => void;
    const context = {
      error: (message: string): never => {
        throw new Error(message);
      },
    };
    expect(() => {
      hook.call(context);
    }).toThrow("the build has a worker");
  });
});

describe("sanitizeFileName", () => {
  it.each([
    ["main", "main"],
    ["-lazy", "_lazy"],
    [".dot", "_dot"],
    ["a b@c~d'e(f)", "a_b_c_d_e_f_"],
    ["été", "_t_"],
    ["a/b", "a_b"],
    ["\0x", "_x"],
    ["_commonjsHelpers", "_commonjsHelpers"],
    ["a..b", "a..b"],
  ])("%j becomes %j", (name, expected) => {
    expect(sanitizeFileName(name)).toBe(expected);
  });
});

describe("the skeleton's configuration", () => {
  function gateOf(mode: string): unknown {
    const plugins = (buildConfig(mode).plugins ?? []).flat() as Plugin[];
    return plugins.find((plugin) => plugin.name === "aibi:operator-gate")?.api;
  }

  it("derives the harness define and the gate's rule from the mode alone", () => {
    const saved = process.env["AIBI_E2E"];
    process.env["AIBI_E2E"] = "true";
    try {
      expect(buildConfig("production").define).toEqual({ __AIBI_E2E__: "false" });
      expect(gateOf("production")).toEqual({ harness: false });
      expect(buildConfig("e2e").define).toEqual({ __AIBI_E2E__: "true" });
      expect(gateOf("e2e")).toEqual({ harness: true });
      expect(buildConfig("production").build?.outDir).toBe("dist");
      expect(buildConfig("e2e").build?.outDir).toBe("dist-e2e");
    } finally {
      if (saved === undefined) {
        delete process.env["AIBI_E2E"];
      } else {
        process.env["AIBI_E2E"] = saved;
      }
    }
  });

  it("refuses any other mode", () => {
    for (const mode of ["development", "test", "prod", ""]) {
      expect(() => buildConfig(mode)).toThrow("production and e2e alone");
    }
  });

  it("states D411's rules", () => {
    const config = buildConfig("production");
    expect(config.base).toBe("/");
    expect(config.publicDir).toBe(false);
    expect(config.build).toMatchObject({
      manifest: true,
      assetsDir: "assets",
      assetsInlineLimit: 0,
      sourcemap: false,
      modulePreload: { polyfill: false },
    });
  });

  it("names every file after the loader's grammar through the configuration's own output options", () => {
    for (const mode of ["production", "e2e"]) {
      const output = buildConfig(mode).build?.rolldownOptions?.output;
      expect(output).toBeDefined();
      const options = output as Rolldown.OutputOptions;
      expect(options.entryFileNames).toBe(ENTRY_FILE_NAMES);
      expect(options.chunkFileNames).toBe(CHUNK_FILE_NAMES);
      expect(options.assetFileNames).toBe(ASSET_FILE_NAMES);
      expect(options.sanitizeFileName).toBe(sanitizeFileName);
    }
  });

  type Built = (Rolldown.OutputAsset | Rolldown.OutputChunk)[];
  const builds = new Map<string, Promise<Built>>();

  /** The output items of a build of the real skeleton, once for each mode, as `npm run build`
   * makes it: Vitest sets `NODE_ENV=test`, which would build React's development code. */
  async function built(mode: "production" | "e2e"): Promise<Built> {
    const found = builds.get(mode) ?? buildReal(mode);
    builds.set(mode, found);
    return found;
  }

  async function buildReal(mode: "production" | "e2e"): Promise<Built> {
    const saved = process.env["NODE_ENV"];
    delete process.env["NODE_ENV"];
    try {
      const output = await build({
        root: WEB,
        mode,
        configFile: path.join(WEB, "vite.config.ts"),
        logLevel: "silent",
        build: { write: false },
      });
      return ((Array.isArray(output) ? output : [output]) as Rolldown.RolldownOutput[]).flatMap((one) => one.output);
    } finally {
      if (saved === undefined) {
        delete process.env["NODE_ENV"];
      } else {
        process.env["NODE_ENV"] = saved;
      }
    }
  }

  async function real(mode: "production" | "e2e"): Promise<GateChunk[]> {
    return (await built(mode)).filter((item) => item.type === "chunk");
  }

  it.each(["production", "e2e"] as const)("lists the queries the real %s build makes, and no other", async (mode) => {
    const suffixes = new Set((await real(mode)).flatMap((chunk) => chunk.moduleIds).flatMap((id) => (id.startsWith("\0") ? [] : [id.slice(id.search(/[?#]|$/u))])).filter((suffix) => suffix !== ""));
    expect([...suffixes].sort()).toEqual([...OPERATOR_QUERIES.keys()].sort());
  });

  it("builds the real skeleton through the gate, with no harness in production", async () => {
    const chunks = await real("production");
    const ids = chunks.flatMap((chunk) => chunk.moduleIds);
    expect(ids.some((id) => id.includes("/src/harness/"))).toBe(false);
    expect(ids.some((id) => id.includes("/src/catalogue/NotFound.tsx"))).toBe(true);
    expect(gateProblems(realpathSync(WEB), chunks, { harness: false })).toEqual([]);
  });

  it("builds the real skeleton's end-to-end build with the harness as a lazy chunk of the operator", async () => {
    const chunks = await real("e2e");
    const harness = chunks.filter((chunk) => chunk.moduleIds.some((id) => id.includes("/src/harness/")));
    expect(harness.map((chunk) => chunk.isEntry)).toEqual([false]);
    const operator = chunks.find((chunk) => chunk.isEntry && chunk.facadeModuleId?.endsWith("/operator.html") === true);
    expect(operator?.dynamicImports).toEqual(harness.map((chunk) => chunk.fileName));
    expect(gateProblems(realpathSync(WEB), chunks, { harness: false })).toEqual([
      expect.stringContaining('the harness module "src/harness/Harness.tsx"'),
    ]);
  });

  it.each([
    ["production", "prod"],
    ["e2e", "e2e"],
  ] as const)("holds the checked-in budget to the %s build: measured <= budget <= 1.15 x measured", async (mode, key) => {
    const files = (await built(mode)).filter((item) => item.fileName.startsWith("assets/"));
    expect(files.length).toBeGreaterThan(2);
    const bytes = files.reduce(
      (sum, item) => sum + Buffer.byteLength(item.type === "chunk" ? item.code : item.source),
      0,
    );
    const budget = readPolicy(path.join(WEB, "budget.json"), checkBudget)[key];
    expect(budget.bytes).toBeGreaterThanOrEqual(bytes);
    expect(budget.bytes).toBeLessThanOrEqual(Math.ceil(1.15 * bytes));
    expect(budget.files).toBeGreaterThanOrEqual(files.length);
    expect(budget.files).toBeLessThanOrEqual(Math.ceil(1.15 * files.length));
  });
});
