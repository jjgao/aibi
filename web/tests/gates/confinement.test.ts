/**
 * The build's gate on the client's confinement (D419, `plugins/gate.ts` (d)): the bundler's own
 * module graph of both entries, which every form of import has been resolved into. Real Vite
 * builds of synthetic two-entry projects with a miniature `src/api/` of the real file names, one
 * offending module for each form the review found or could think of, from each entry; the same
 * on the table `API_MODULES`, which a build of the real skeleton in both modes must equal.
 */
import { readFileSync, realpathSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";

import { build, type Plugin, type Rolldown } from "vite";
import { afterAll, describe, expect, it } from "vitest";

import { API_MODULES, confinementProblem, ENTRIES, type GateGraph, type GateModule, graphProblems, operatorGate } from "../../plugins/gate";
import { buildConfig } from "../../vite.config";
import { cleanup, type Files, gated, project, refused, virtual, WEB } from "./synthetic";

afterAll(cleanup);

/** A miniature client with the real files and the real edges (the table's). */
const API: Files = {
  "src/api/box.ts": "export const box = (t: string) => ({ t }); export const isServerNumber = (v: unknown) => typeof v === 'object';",
  "src/api/decode.ts": "import { box } from './box.ts'; export const decode = (t: string) => box(t); export class DecodeError {}",
  "src/api/route.ts": "export const route = (u: string) => ({ u });",
  "src/api/client.ts": "import { decode } from './decode.ts'; import { route } from './route.ts'; export const exchange = (t: string) => decode(t) && route(t);",
  "src/api/generated/routes.ts": "import { route } from '../route.ts'; export const health = () => route('/api/health');",
  "src/api/index.ts":
    "export { isServerNumber } from './box.ts'; export { DecodeError } from './decode.ts'; export { exchange } from './client.ts'; export * as routes from './generated/routes.ts';",
  "src/index.ts": "import { exchange } from './api/index.ts'; document.title = String(exchange('i'));",
  "src/operator.ts": "import { exchange } from './api/index.ts'; document.title = String(exchange('o'));",
};

const ENTRY_FILES = ["src/index.ts", "src/operator.ts"] as const;

/** What an entry file of the miniature client holds. */
const body = (entry: (typeof ENTRY_FILES)[number]): string => API[entry] ?? "";

/** A project of the miniature client where `entry` also imports `src/offender.ts`, `files` more. */
function offending(entry: (typeof ENTRY_FILES)[number], offender: string, files: Files = {}): string {
  return project({ ...API, [entry]: `${body(entry)} import './offender.ts';`, "src/offender.ts": offender, ...files });
}

describe("the miniature client passes the gate (so that a refusal below is the offender's)", () => {
  it("builds, with every edge of the table", async () => {
    const found = await gated(project(API));
    expect(found).toHaveProperty("chunks");
  });
});

/** The forms that reach a module of `src/api/` from a module of `src/` beside it, each with the
 * words of the refusal. */
const FORMS: readonly (readonly [string, string, string])[] = [
  ["a static import", "import { box } from './api/box.ts'; export const a = box('1');", "src/api/box.ts is imported: the importer"],
  ["a dynamic import", "export const a = () => import('./api/box.ts');", "src/api/box.ts is imported dynamically: the importer"],
  [
    "an eager import.meta.glob",
    "export const a = import.meta.glob('./api/box.ts', { eager: true, import: 'box' });",
    "src/api/box.ts is imported: the importer",
  ],
  ["a lazy import.meta.glob", "export const a = import.meta.glob('./api/box.ts');", "src/api/box.ts is imported dynamically: the importer"],
  ["a glob of the whole directory", "export const a = import.meta.glob('./api/*.ts', { eager: true });", "is imported: the importer"],
  ["a re-export", "export { box } from './api/box.ts';", "src/api/box.ts is imported: the importer"],
  ["a namespace re-export", "export * as inner from './api/box.ts';", "src/api/box.ts is imported: the importer"],
  ["the decoder", "import { decode } from './api/decode.ts'; export const a = decode;", "src/api/decode.ts is imported: the importer"],
  ["the decoder, dynamically", "export const a = () => import('./api/decode.ts');", "src/api/decode.ts is imported dynamically: the importer"],
  ["the client", "import { exchange } from './api/client.ts'; export const a = exchange;", "src/api/client.ts is imported: the importer"],
  ["the client, dynamically", "export const a = () => import('./api/client.ts');", "src/api/client.ts is imported dynamically: the importer"],
  ["the oracle's counter", "import './api/oracle.ts'; export const a = 1;", "src/api/oracle.ts is imported: the importer"],
  ["the route maker", "import { route } from './api/route.ts'; export const a = route;", "src/api/route.ts is imported: the importer"],
  ["a generated module", "import { health } from './api/generated/routes.ts'; export const a = health;", "src/api/generated/routes.ts is imported: the importer"],
  ["a module of src/api/ the table lacks", "import './api/extra.ts'; export const a = 1;", 'a module of src/api/ the gate\'s table does not list'],
  ["a ?raw import of the box", "import text from './api/box.ts?raw'; export const a = text;", "src/api/box.ts is imported: the importer"],
  ["a ?url import of the box", "import url from './api/box.ts?url'; export const a = url;", "which carries \"?url\""],
  ["a ?raw import of the box (a query)", "import text from './api/box.ts?raw'; export const a = text;", "which carries \"?raw\""],
];

const EXTRA: Files = { "src/api/extra.ts": "export const extra = 1;", "src/api/oracle.ts": "export const counter = 0;" };

describe.each(ENTRY_FILES)("an offending module of src/ imported by %s", (entry) => {
  it.each(FORMS)("%s is refused", async (_form, code, words) => {
    const error = await refused(offending(entry, code, EXTRA));
    expect(error).toContain(words);
  });
});

describe("a module outside src/", () => {
  it.each(ENTRY_FILES)("is refused from %s, in the project or beside it", async (entry) => {
    const inside = project({
      ...API,
      [entry]: `${body(entry)} import { top } from '../top.js'; document.title = top;`,
      "top.js": "export const top = 'top';",
    });
    expect(await refused(inside)).toContain('the project module "top.js", outside src/');
    const beside = project({
      ...API,
      [entry]: `${body(entry)} import { shared } from '../shared/digits.ts'; document.title = shared;`,
      "shared/digits.ts": "export const shared = 'shared';",
    });
    expect(await refused(beside)).toContain('the project module "shared/digits.ts", outside src/');
  });

  it.each(ENTRY_FILES)("that imports the box is refused from %s twice over: outside src/, and as an importer", async (entry) => {
    const root = project({
      ...API,
      [entry]: `${body(entry)} import { shared } from '../shared/digits.ts'; document.title = String(shared);`,
      "shared/digits.ts": "import { box } from '../src/api/box.ts'; export const shared = box('1');",
    });
    const error = await refused(root);
    expect(error).toContain('in ' + path.basename(entry === "src/index.ts" ? "index.html" : "operator.html") + "'s graph: the project module \"shared/digits.ts\", outside src/");
    expect(error).toContain("src/api/box.ts is imported: the importer");
  });

  it.each(ENTRY_FILES)("that lies outside the project is refused from %s", async (entry) => {
    const root = project({
      ...API,
      [entry]: `${body(entry)} import { outside } from '../../outside.js'; document.title = outside;`,
    });
    const file = path.join(root, "..", "outside.js");
    const { writeFileSync } = await import("node:fs");
    writeFileSync(file, "export const outside = 'outside';");
    expect(await refused(root)).toContain("outside.js");
  });
});

describe("a virtual module", () => {
  it.each(ENTRY_FILES)("that imports the box is refused from %s, as a virtual module and as an importer", async (entry) => {
    const plugin: Plugin = {
      ...virtual,
      load(id) {
        return id === "\0aibi-virtual" ? "import { box } from '/src/api/box.ts'; export const v = box('1');" : null;
      },
    };
    const root = project({ ...API, [entry]: `${body(entry)} import { v } from 'virtual:aibi'; document.title = String(v);` });
    const error = await refused(root, false, [plugin]);
    expect(error).toContain('the virtual module "\\u0000aibi-virtual"');
    expect(error).toContain('src/api/box.ts is imported: the importer "\\u0000aibi-virtual"');
  });
});

/** The project's own tsconfig, which Vite's transform reads for `verbatimModuleSyntax`. */
const TSCONFIG: Files = { "tsconfig.json": readFileSync(path.join(WEB, "tsconfig.base.json"), "utf8") };

describe("a type-only import", () => {
  it("is read under the project's own options, which keep an inline type import's edge", () => {
    expect((JSON.parse(TSCONFIG["tsconfig.json"] ?? "{}") as { compilerOptions: { verbatimModuleSyntax: boolean } }).compilerOptions.verbatimModuleSyntax).toBe(true);
  });

  it.each(ENTRY_FILES)("`import type` from %s leaves no edge, so the gate does not see it (the lint and the type program do)", async (entry) => {
    const root = offending(entry, "import type { box } from './api/box.ts'; export type A = typeof box; export const a = 1;", TSCONFIG);
    expect(await gated(root)).toHaveProperty("chunks");
  });

  it.each(ENTRY_FILES)("`import { type X }` from %s keeps a bare import under verbatimModuleSyntax, which the gate refuses", async (entry) => {
    const root = offending(entry, "import { type box } from './api/box.ts'; export type A = typeof box; export const a = 1;", TSCONFIG);
    expect(await refused(root)).toContain("src/api/box.ts is imported: the importer");
  });

  it("is not in the graph of the real skeleton either: no module of src/api/ holds only types", async () => {
    const graph = await realGraph("production");
    const files = [...graph.keys()].map((id) => path.relative(realpathSync(WEB), id));
    expect(files).not.toContain("src/api/json.ts");
    expect(files).not.toContain("src/api/generated/openapi.ts");
  });
});

/** Modules under `src/` the bundler reads as JavaScript whatever their name, and the lint does not. */
const UNREAD: readonly (readonly [string, string])[] = [
  ["digits.JS", "unread.JS"],
  ["no extension", "unread"],
  [".Ts", "unread.Ts"],
  [".TS", "unread.TS"],
  [".JSX", "unread.JSX"],
  [".MJS", "unread.MJS"],
  [".es6", "unread.es6"],
  [".jsonc", "unread.jsonc"],
  [".coffee", "unread.coffee"],
  [".ls", "unread.ls"],
  [".js", "unread.js"],
  [".mts", "unread.mts"],
  [".json", "unread.json"],
];

describe.each(ENTRY_FILES)("a module under src/ whose extension no lint reads, imported by %s", (entry) => {
  it.each(UNREAD)("%s is refused", async (_what, name) => {
    const code = name.endsWith(".json") ? "{ \"a\": 1 }" : "export const unread = () => fetch('/api/health');";
    const root = project({ ...API, [entry]: `${body(entry)} import './${name}';`, [`src/${name}`]: code });
    expect(await refused(root)).toContain(`the module "src/${name}", whose extension is none the lint reads`);
  });

  it.each(["x.ts", "x.tsx", "x.css"])("%s is admitted", async (name) => {
    const code = name.endsWith(".css") ? "p { color: red; }" : "export const x = 1;";
    const root = project({ ...API, [entry]: `${body(entry)} import './${name}';`, [`src/${name}`]: code });
    expect(await gated(root)).toHaveProperty("chunks");
  });

  it.each([".x.ts", ".d/x.ts"])("a dot-led path, src/%s, is refused", async (name) => {
    const root = project({ ...API, [entry]: `${body(entry)} import './${name}';`, [`src/${name}`]: "export const x = 1; document.title = 'x';" });
    expect(await refused(root)).toContain(`the module "src/${name}", whose path has a dot-led segment`);
  });

  it("a node_modules directory inside src/ is refused", async () => {
    const root = project({
      ...API,
      [entry]: `${body(entry)} import './x/node_modules/p/index.ts';`,
      "src/x/node_modules/p/index.ts": "export const p = 1;",
    });
    expect(await refused(root)).toContain("src/x/node_modules/p/index.ts\", in a node_modules directory that is not the project's own");
  });

  it("a module beside src/ reached only by a dynamic import is refused", async () => {
    const root = project({ ...API, [entry]: `${body(entry)} void import('../shared/lazy.ts');`, "shared/lazy.ts": "export const lazy = 1;" });
    expect(await refused(root)).toContain('the project module "shared/lazy.ts", outside src/');
  });
});

describe("a module under src/ that is not a file at its own path (the plugin passes the real-path check to the graph)", () => {
  it.each(ENTRY_FILES)("made by a plugin for a path with no file, imported by %s, is refused", async (entry) => {
    const root = project({ ...API });
    const ghost = path.join(root, "src/ghost.ts");
    const plugin: Plugin = {
      name: "ghost",
      resolveId(id) {
        return id === "virtual:ghost" ? ghost : null;
      },
      load(id) {
        return id === ghost ? "export const ghost = 1; document.title = 'ghost';" : null;
      },
    };
    writeFileSync(path.join(root, entry), `${body(entry)} import 'virtual:ghost';`);
    expect(await refused(root, false, [plugin])).toContain(`the module ${JSON.stringify(ghost)}, whose path is not its real path`);
  });
});

describe("the plugin's root", () => {
  it("is the real path of the project's root: a plugin configured with a symlink to it places modules as the bundler does", () => {
    const root = project({ ...API });
    const link = `${root}-root-link`;
    symlinkSync(root, link);
    const plugin = operatorGate({ harness: false });
    (plugin.configResolved as unknown as (config: { root: string }) => void)({ root: link });
    const real = realpathSync(root);
    const module = (id: string, imports: string[] = []) => ({ importers: [], dynamicImporters: [], importedIds: imports, dynamicallyImportedIds: [] as string[], id });
    const modules = new Map([
      [`${real}/index.html`, module(`${real}/index.html`, [`${real}/src/index.ts`])],
      [`${real}/operator.html`, module(`${real}/operator.html`, [`${real}/src/operator.ts`])],
      [`${real}/src/index.ts`, module(`${real}/src/index.ts`)],
      [`${real}/src/operator.ts`, module(`${real}/src/operator.ts`)],
    ]);
    const chunk = (name: string) => ({
      type: "chunk" as const,
      fileName: `assets/${name}.js`,
      isEntry: true,
      facadeModuleId: `${real}/${name}.html`,
      imports: [],
      dynamicImports: [],
      moduleIds: [`${real}/${name}.html`, `${real}/src/${name}.ts`],
    });
    const context = {
      getModuleIds: () => modules.keys(),
      getModuleInfo: (id: string) => modules.get(id) ?? null,
      error: (message: string): never => {
        throw new Error(message);
      },
    };
    const hook = plugin.generateBundle as unknown as (this: typeof context, output: unknown, bundle: Record<string, unknown>) => void;
    expect(() => {
      hook.call(context, {}, { index: chunk("index"), operator: chunk("operator") });
    }).not.toThrow();
  });
});

describe("a project reached through a symlink", () => {
  it("is judged by the real path of the root, as the bundler places its modules (a symlinked root passes a clean build, and refuses a bad one)", async () => {
    const root = project({ ...API, "src/offender.ts": "export const x = 1;" });
    const link = `${root}-link`;
    symlinkSync(root, link);
    expect(await gated(link)).toHaveProperty("chunks");
    const bad = project({ ...API, "src/operator.ts": `${API["src/operator.ts"] ?? ""} import '../shared/x.ts';`, "shared/x.ts": "export const x = 1;" });
    const badLink = `${bad}-link`;
    symlinkSync(bad, badLink);
    expect(await refused(badLink)).toContain('the project module "shared/x.ts", outside src/');
  });
});

describe("a third document the build starts from", () => {
  const THIRD: Files = {
    ...API,
    "third.html": '<!doctype html><div id="root"></div><script type="module" src="/src/third.ts"></script>',
  };

  it("is walked like the two: a module beside src/ in its closure is refused", async () => {
    const root = project({ ...THIRD, "src/third.ts": "import { shared } from '../shared/x.ts'; document.title = shared;", "shared/x.ts": "export const shared = 's';" });
    expect(await refused(root, false, [], ["third.html"])).toContain('in third.html\'s graph: the project module "shared/x.ts", outside src/');
  });

  it("is walked for the extensions too, and for the client's table", async () => {
    const root = project({ ...THIRD, "src/third.ts": "import './u.JS'; import { box } from './api/box.ts'; export const b = box;", "src/u.JS": "document.title = 'u';" });
    const error = await refused(root, false, [], ["third.html"]);
    expect(error).toContain('whose extension is none the lint reads');
    expect(error).toContain("src/api/box.ts is imported: the importer");
  });

  it("holding nothing but src/ and the client's door is refused only as a document the loader does not serve, not in its graph", async () => {
    const root = project({ ...THIRD, "src/third.ts": "import { exchange } from './api/index.ts'; document.title = String(exchange('t'));" });
    const error = await refused(root, false, [], ["third.html"]);
    expect(error).toContain("third.html: a file of a type the loader does not serve");
    expect(error).not.toContain("third.html's graph");
  });

  it("is refused when it is no document: an entry that is a script", async () => {
    const root = project({ ...API, "src/third.ts": "export const third = 1;" });
    expect(await refused(root, false, [], ["src/third.ts"])).toContain('the build has an entry that is no document: "src/third.ts"');
  });
});

describe("an import form that lands in the allowed list", () => {
  it.each(ENTRY_FILES)("passes from %s: the index, from outside src/api/, statically, dynamically and by a glob", async (entry) => {
    const root = offending(
      entry,
      "import { exchange } from './api/index.ts'; export const a = [exchange, () => import('./api/index.ts'), import.meta.glob('./api/index.ts')];",
    );
    expect(await gated(root)).toHaveProperty("chunks");
  });
});

/** A graph of the given modules' edges, importers filled from the imports (and dynamic ones). */
function graphOf(root: string, edges: Record<string, { imports?: string[]; dynamicImports?: string[] }>): GateGraph {
  const abs = (name: string): string => (name.startsWith("\0") ? name : `${root}/${name}`);
  const graph = new Map<string, { importers: string[]; dynamicImporters: string[]; imports: string[]; dynamicImports: string[] }>();
  const at = (name: string) => {
    const id = abs(name);
    const found = graph.get(id) ?? { importers: [], dynamicImporters: [], imports: [], dynamicImports: [] };
    graph.set(id, found);
    return found;
  };
  for (const [name, { imports = [], dynamicImports = [] }] of Object.entries(edges)) {
    at(name).imports.push(...imports.map(abs));
    at(name).dynamicImports.push(...dynamicImports.map(abs));
    imports.forEach((target) => at(target).importers.push(abs(name)));
    dynamicImports.forEach((target) => at(target).dynamicImporters.push(abs(name)));
  }
  return graph;
}

describe("graphProblems on a graph (the rule's own cells)", () => {
  const root = "/w";
  const base = {
    "index.html": { imports: ["src/a.ts"] },
    "operator.html": { imports: ["src/b.ts"] },
  };

  it("finds nothing in a graph of src/ alone", () => {
    expect(graphProblems(root, graphOf(root, base))).toEqual([]);
  });

  it("refuses a build without a module for each document", () => {
    expect(graphProblems(root, graphOf(root, { "index.html": {} }))).toEqual(["the build has no module for operator.html"]);
    expect(graphProblems(root, graphOf(root, { "operator.html": {} }))).toEqual(["the build has no module for index.html"]);
  });

  it.each(ENTRIES)("walks %s's graph, statically and dynamically, to a module outside src/, and only its own", (document) => {
    const own = document === "index.html" ? "src/a.ts" : "src/b.ts";
    for (const how of ["imports", "dynamicImports"] as const) {
      const graph = graphOf(root, { ...base, [own]: { [how]: ["lib/x.ts"] } });
      expect(graphProblems(root, graph)).toEqual([`in ${document}'s graph: the project module "lib/x.ts", outside src/`]);
    }
  });

  it.each(["src/x/../y.ts", "src/./y.ts", "src//y.ts"])("refuses %s, whose file is not a normal path, in either entry's graph", (name) => {
    const graph = graphOf(root, { ...base, "src/a.ts": { imports: [name] } });
    expect(graphProblems(root, graph)).toEqual([`in index.html's graph: the module "/w/${name}", whose file is not a normal path`]);
  });

  it("admits a document only if the build starts from it: a stray .html is a project module outside src/", () => {
    const graph = graphOf(root, { ...base, "src/a.ts": { imports: ["other.html"] } });
    expect(graphProblems(root, graph)).toEqual([`in index.html's graph: the project module "other.html", outside src/`]);
    expect(graphProblems(root, graph, ["other.html"])).toEqual([]);
  });

  it.each([
    ["src/a.ts", null],
    ["src/a.tsx", null],
    ["src/a.d.ts", null],
    ["src/a.css", null],
    ["src/x/y.test.ts", null],
    ["src/a.json", "extension"],
    ["src/a.JS", "extension"],
    ["src/a.Ts", "extension"],
    ["src/a.TSX", "extension"],
    ["src/a.mts", "extension"],
    ["src/a.es6", "extension"],
    ["src/a.jsonc", "extension"],
    ["src/a", "extension"],
    ["src/.ts", "dot-led segment"],
    ["src/a.ts.bak", "extension"],
    ["src/a.ts.js", "extension"],
    ["lib/a.ts", "outside src/"],
    ["src2/a.ts", "outside src/"],
  ])("%s is judged by the table of extensions: %s", (name, why) => {
    const problem = confinementProblem(root, `${root}/${name}`);
    if (why === null) {
      expect(problem).toBeNull();
    } else {
      expect(problem).toContain(why);
    }
  });

  it("admits a document the build starts from, and no other .html, in a closure (the check reads the walked documents, not ENTRIES)", () => {
    expect(confinementProblem(root, "/w/other.html", ["other.html"])).toBeNull();
    expect(confinementProblem(root, "/w/other.html")).toContain("outside src/");
    expect(confinementProblem(root, "/w/index.html")).toBeNull();
    expect(confinementProblem(root, "/w/other.html", ["index.html"])).toContain("outside src/");
  });

  it.each(["src/.x.ts", "src/.d/x.ts", "src/a/.b/c.tsx", "src/a/.b.css"])("refuses %s, a path with a dot-led segment", (name) => {
    expect(confinementProblem(root, `/w/${name}`)).toContain("dot-led segment");
    const graph = graphOf(root, { ...base, "src/a.ts": { imports: [name] } });
    expect(graphProblems(root, graph)).toEqual([expect.stringContaining("dot-led segment")]);
  });

  it("requires every module under src/ to be spelled as its real path, a throw included", () => {
    const graph = graphOf(root, { ...base, "src/a.ts": { imports: ["src/b.ts"] } });
    expect(graphProblems(root, graph, [], (file) => file)).toEqual([]);
    const cased = graphProblems(root, graph, [], (file) => file.replace("/src/b.ts", "/src/B.ts"));
    expect(cased).toHaveLength(2);
    expect(cased.every((problem) => problem.includes('"/w/src/b.ts", whose path is not its real path'))).toBe(true);
    const failing = graphProblems(root, graph, [], () => {
      throw new Error("ENOENT");
    });
    expect(failing.length).toBeGreaterThanOrEqual(1);
    expect(failing.every((problem) => problem.includes("not its real path"))).toBe(true);
  });

  it("reads the real path of the modules under src/ alone, not of a package or a document", () => {
    const graph = graphOf(root, { ...base, "src/a.ts": { imports: ["node_modules/p/i.js"] } });
    const elsewhere = (file: string): string => (file.includes("/node_modules/") || file.endsWith(".html") ? `${file}x` : file);
    expect(graphProblems(root, graph, [], elsewhere)).toEqual([]);
  });

  it("holds the importers of an internal to the table, statically and dynamically apart", () => {
    const graph = graphOf(root, { ...base, "src/a.ts": { imports: ["src/api/box.ts"] }, "src/b.ts": { dynamicImports: ["src/api/box.ts"] } });
    expect(graphProblems(root, graph)).toEqual([
      expect.stringContaining('src/api/box.ts is imported: the importer "/w/src/a.ts" is not one of src/api/box.ts\'s (src/api/decode.ts, src/api/index.ts, src/api/curator.ts)'),
      expect.stringContaining('src/api/box.ts is imported dynamically: the importer "/w/src/b.ts"'),
    ]);
  });

  it("admits the table's importers, whether static or dynamic, and no other", () => {
    const ok = graphOf(root, { ...base, "src/a.ts": { imports: ["src/api/index.ts"] }, "src/api/index.ts": { imports: ["src/api/box.ts"], dynamicImports: ["src/api/decode.ts"] } });
    expect(graphProblems(root, ok)).toEqual([]);
  });

  it("refuses an importer with a query, a virtual one and one with a path that is not normal", () => {
    for (const importer of ["src/api/decode.ts?x", "\0virtual", "src/x/../api/decode.ts"]) {
      const graph = graphOf(root, { ...base, "src/a.ts": { imports: [importer] }, [importer]: { imports: ["src/api/box.ts"] } });
      expect(graphProblems(root, graph).some((problem) => problem.startsWith("src/api/box.ts is imported: the importer"))).toBe(true);
    }
  });

  it("holds a module with a query to the file's table, and refuses the query besides", () => {
    const graph = graphOf(root, { ...base, "src/a.ts": { imports: ["src/api/box.ts?raw"] } });
    const problems = graphProblems(root, graph);
    expect(problems).toContainEqual(expect.stringContaining('the importer "/w/src/a.ts"'));
    expect(problems).toContainEqual(expect.stringContaining('which carries "?raw"'));
  });

  it("states the table's one door for the rest of src/, and no other for the index", () => {
    const door = graphOf(root, { ...base, "src/a.ts": { imports: ["src/api/index.ts"] }, "src/b.ts": { dynamicImports: ["src/api/index.ts"] } });
    expect(graphProblems(root, door)).toEqual([]);
    const lib = graphOf(root, { ...base, "src/a.ts": { imports: ["lib/y.ts"] }, "lib/y.ts": { imports: ["src/api/index.ts"] } });
    expect(graphProblems(root, lib)).toContainEqual(expect.stringContaining('src/api/index.ts is imported: the importer "/w/lib/y.ts"'));
    const inner = graphOf(root, { ...base, "src/a.ts": { imports: ["src/api/client.ts"] }, "src/api/client.ts": { imports: ["src/api/index.ts"] } });
    expect(graphProblems(root, inner)).toContainEqual(expect.stringContaining('src/api/index.ts is imported: the importer "/w/src/api/client.ts"'));
  });
});

/** The module graph of a build of the real skeleton in a mode, as the bundler has it. */
async function realGraph(mode: "production" | "e2e"): Promise<GateGraph> {
  const config = buildConfig(mode);
  const captured = new Map<string, GateModule>();
  const spy: Plugin = {
    name: "aibi:spy",
    generateBundle() {
      for (const id of this.getModuleIds()) {
        const info = this.getModuleInfo(id);
        if (info !== null) {
          captured.set(id, {
            importers: info.importers,
            dynamicImporters: info.dynamicImporters,
            imports: info.importedIds,
            dynamicImports: info.dynamicallyImportedIds,
          });
        }
      }
    },
  };
  const saved = process.env["NODE_ENV"];
  delete process.env["NODE_ENV"];
  try {
    await build({
      ...config,
      root: WEB,
      mode,
      configFile: false,
      logLevel: "silent",
      plugins: [...(config.plugins ?? []), spy],
      build: { ...config.build, write: false },
    });
  } finally {
    if (saved !== undefined) {
      process.env["NODE_ENV"] = saved;
    }
  }
  return captured;
}

describe("the table API_MODULES is the real graph's", () => {
  const MODES = ["production", "e2e"] as const;
  const graphs = new Map<string, Promise<GateGraph>>();
  const graphOfMode = (mode: (typeof MODES)[number]): Promise<GateGraph> => {
    const found = graphs.get(mode) ?? realGraph(mode);
    graphs.set(mode, found);
    return found;
  };
  const root = realpathSync(WEB);

  /** The files of `src/api/` of a graph, each with the relative paths of its importers. */
  const apiOf = (graph: GateGraph): Map<string, Set<string>> => {
    const found = new Map<string, Set<string>>();
    for (const [id, module] of graph) {
      const relative = path.relative(root, id);
      if (relative.startsWith("src/api/")) {
        found.set(relative, new Set([...module.importers, ...module.dynamicImporters].map((importer) => path.relative(root, importer))));
      }
    }
    return found;
  };

  it("is passed by both real builds (the gate is in their configuration)", async () => {
    for (const mode of MODES) {
      for (const document of ENTRIES) {
        expect([...(await graphOfMode(mode)).keys()]).toContain(path.join(root, document));
      }
      expect(graphProblems(root, await graphOfMode(mode))).toEqual([]);
    }
  });

  it("lists every module of src/api/ the builds hold, and every importer they show, exactly", async () => {
    const union = new Map<string, Set<string>>();
    for (const mode of MODES) {
      for (const [file, importers] of apiOf(await graphOfMode(mode))) {
        union.set(file, new Set([...(union.get(file) ?? []), ...importers]));
      }
    }
    expect([...union.keys()].sort()).toEqual([...API_MODULES.keys()].sort());
    for (const [file, entry] of API_MODULES) {
      const shown = union.get(file) ?? new Set();
      const listed = new Set(entry.importers.keys());
      if (entry.outside === undefined) {
        expect([...shown].sort(), file).toEqual([...listed].sort());
      } else {
        expect([...shown].filter((name) => !name.startsWith("src/") || name.startsWith("src/api/")), file).toEqual([]);
        expect(listed.size).toBe(0);
      }
      for (const reason of entry.importers.values()) {
        expect(reason).not.toBe("");
      }
    }
  });

  it("holds the oracle's counter in the end-to-end build alone, and imported by the harness alone", async () => {
    const production = apiOf(await graphOfMode("production"));
    expect([...production.keys()]).not.toContain("src/api/oracle.ts");
    expect([...(await graphOfMode("production")).keys()].some((id) => id.includes("/src/harness/"))).toBe(false);
    const e2e = apiOf(await graphOfMode("e2e"));
    expect([...(e2e.get("src/api/oracle.ts") ?? [])]).toEqual(["src/harness/Harness.tsx"]);
  });

  it("states, for each importer the table lists, a reason", () => {
    expect(API_MODULES.size).toBe(9);
  });
});

describe("the real builds' chunks hold the oracle's counter where only the harness is", () => {
  async function chunksOf(mode: "production" | "e2e"): Promise<Rolldown.OutputChunk[]> {
    const saved = process.env["NODE_ENV"];
    delete process.env["NODE_ENV"];
    try {
      const output = await build({ root: WEB, mode, configFile: path.join(WEB, "vite.config.ts"), logLevel: "silent", build: { write: false } });
      return ((Array.isArray(output) ? output : [output]) as Rolldown.RolldownOutput[]).flatMap((one) => one.output).filter((item) => item.type === "chunk");
    } finally {
      if (saved !== undefined) {
        process.env["NODE_ENV"] = saved;
      }
    }
  }

  it("never in a production chunk, and in the end-to-end build in the harness's lazy chunk alone", async () => {
    const holders = (chunks: Rolldown.OutputChunk[]): Rolldown.OutputChunk[] => chunks.filter((chunk) => chunk.moduleIds.some((id) => id.endsWith("/src/api/oracle.ts")));
    expect(holders(await chunksOf("production"))).toEqual([]);
    const e2e = await chunksOf("e2e");
    const [holder, ...rest] = holders(e2e);
    expect(rest).toEqual([]);
    expect(holder?.isEntry).toBe(false);
    expect(holder?.moduleIds.some((id) => id.endsWith("/src/harness/Harness.tsx"))).toBe(true);
    const operator = e2e.find((chunk) => chunk.isEntry && chunk.facadeModuleId?.endsWith("/operator.html") === true);
    expect(operator?.moduleIds.some((id) => id.endsWith("/src/api/oracle.ts"))).toBe(false);
    expect(operator?.code).not.toContain("decodedCount");
  });
  it("never put an operator module in a chunk the catalogue entry loads, in either build (D423)", async () => {
    for (const mode of ["production", "e2e"] as const) {
      const chunks = await chunksOf(mode);
      const byName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
      const entry = chunks.find((chunk) => chunk.isEntry && chunk.facadeModuleId?.endsWith("/index.html") === true);
      expect(entry, mode).toBeDefined();
      const loaded = new Set<string>();
      const pending = entry === undefined ? [] : [entry.fileName];
      for (let name = pending.pop(); name !== undefined; name = pending.pop()) {
        const chunk = byName.get(name);
        if (!loaded.has(name) && chunk !== undefined) {
          loaded.add(name);
          pending.push(...chunk.imports, ...chunk.dynamicImports);
        }
      }
      const held = [...loaded].flatMap((name) => byName.get(name)?.moduleIds ?? []).filter((id) => /\/src\/(?:api\/(?:operator|curator)\.ts$|operator\/)/u.test(id));
      expect(held, mode).toEqual([]);
      const operator = chunks.find((chunk) => chunk.isEntry && chunk.facadeModuleId?.endsWith("/operator.html") === true);
      expect(operator?.moduleIds.some((id) => id.endsWith("/src/api/operator.ts")), mode).toBe(true);
    }
  });
});

describe("the catalogue entry holds no operator module (gate (e), D423)", () => {
  const root = "/w";
  const D423 = (problems: string[]): string[] => problems.filter((problem) => problem.includes("(D423)"));

  it.each([
    ["the page's operator client", "src/api/operator.ts"],
    ["its factory", "src/api/curator.ts"],
    ["the token's shell", "src/operator/Shell.tsx"],
    ["any module under src/operator/", "src/operator/deep/x.ts"],
  ])("refuses %s in index.html's graph, imported statically or dynamically, at any depth", (_case, file) => {
    for (const how of ["imports", "dynamicImports"] as const) {
      const graph = graphOf(root, {
        "index.html": { imports: ["src/a.ts"] },
        "src/a.ts": { [how]: ["src/c.ts"] },
        "src/c.ts": { [how]: [file] },
        "operator.html": { imports: ["src/b.ts"] },
      });
      expect(D423(graphProblems(root, graph)), how).toEqual([`in index.html's graph: the operator module ${JSON.stringify(file)} (D423)`]);
    }
  });

  it("admits them in operator.html's graph, and a module whose path only begins like one in index.html's", () => {
    const graph = graphOf(root, {
      "index.html": { imports: ["src/operatorish.ts", "src/api/operators.ts", "src/operator.ts"] },
      "operator.html": { imports: ["src/operator/Shell.tsx"] },
      "src/operator/Shell.tsx": { imports: ["src/api/operator.ts"] },
      "src/api/operator.ts": { imports: ["src/api/curator.ts"] },
    });
    expect(D423(graphProblems(root, graph))).toEqual([]);
  });

  it("holds in the real builds: index.html's closure has none of them, operator.html's has the client (the control)", async () => {
    for (const mode of ["production", "e2e"] as const) {
      const graph = await realGraph(mode);
      const closure = (document: string): string[] => {
        const seen = new Set<string>();
        const pending = [path.join(realpathSync(WEB), document)];
        for (let id = pending.pop(); id !== undefined; id = pending.pop()) {
          const module = graph.get(id);
          if (!seen.has(id) && module !== undefined) {
            seen.add(id);
            pending.push(...module.imports, ...module.dynamicImports);
          }
        }
        return [...seen].map((id) => path.relative(realpathSync(WEB), id));
      };
      const catalogue = closure("index.html");
      expect(catalogue.filter((id) => id === "src/api/operator.ts" || id === "src/api/curator.ts" || id.startsWith("src/operator/")), mode).toEqual([]);
      expect(catalogue, mode).toContain("src/api/client.ts");
      expect(closure("operator.html"), mode).toEqual(expect.arrayContaining(["src/api/operator.ts", "src/api/curator.ts", "src/operator/Shell.tsx"]));
    }
  });
});
