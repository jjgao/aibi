/**
 * The build's gate (D411, D418): what the loader cannot see in `dist/`, because the manifest lists
 * chunks and not the modules inside them. A Vite plugin that fails the build, in
 * `generateBundle`, over every chunk of the build:
 *
 * (a) **No harness outside the end-to-end build.** Unless the build's mode is `e2e`, any module of
 *     `src/harness/` in any chunk fails it (a harness imported statically lands inside the
 *     operator entry's own chunk, which the loader accepts).
 * (b) **The operator entry's graph is an allow-list.** For the closure of `operator.html` (its
 *     chunk and every chunk it imports, statically or dynamically), every module is placed once,
 *     by the file it loads from (its id up to the first `?` or `#`, which Vite ignores when it
 *     loads the file) relative to the project, and must be one of: a module under the project's own
 *     `node_modules/` of `OPERATOR_PACKAGES` (every package its path names, one for each
 *     `node_modules/` segment, scopes included, must be on the list: `react-dom/node_modules/
 *     vega-util` is refused for `vega-util`, and so is `vega-lite/node_modules/react/chart.js`
 *     for `vega-lite`); the app's own `src/` but `src/chart/`, holding no `node_modules` segment
 *     (no package has a place there, so any such path is refused); `operator.html` itself; or
 *     exactly one of `VIRTUAL_MODULES`. Any other module (a chart package, a chart module, a
 *     router, any other virtual module, a file outside the project) fails it, and so does an id
 *     whose file is not a normal path (`..`, `.`, `//`) and an id with a query or fragment that
 *     `OPERATOR_QUERIES` does not list (a suffix is no part of a place, and one such as
 *     `?/../../x` could move it under a path normaliser). A deny-list would
 *     miss `vega-util`, `d3-*` or a chart module outside `src/chart/`.
 * (c) **No worker.** `noWorker` is the gate's plugin of Vite's `worker.plugins`: Vite builds
 *     every worker (`?worker`, `?worker&url`, `?worker&inline`, `?sharedworker`, `new Worker(new
 *     URL(...))`, `new SharedWorker(new URL(...))`) in a build of its own, which the build's
 *     plugins never see, and an inline worker puts its code into the importing chunk as a string.
 *     The plugin fails every worker build Vite makes, in both modes and from either entry. Any
 *     other file Vite emits must be of a type the loader serves (`ASSET_EXTENSIONS`: the
 *     loader's own list but for `.js`, which a chunk alone is) or be one of the bundle's own
 *     root files (`ROOT_ASSETS`): a script that is no chunk (a raw `.ts`, `.cjs` or `.jsx` that
 *     `new URL(...)` or `?url` made an asset) fails the gate itself, before check-bundle.
 *
 * (d) **The client's confinement, on the module graph of every entry (D419).** The bundler has
 *     resolved every form of import by the time the gate runs (a static or dynamic `import`, a
 *     re-export, `import.meta.glob` eager or lazy, a `?raw` or `?url` query, a module a plugin
 *     made), so the gate asks the graph, not the source text. It walks every document the build
 *     starts from (every entry chunk, not two named ones: `ENTRIES` are the two the build must
 *     hold, and any other is walked too, and refused if it is no `.html`): (1) every module in the
 *     closure of a document (static and dynamic imports) is under `src/`, but for the project's
 *     own `node_modules/`, the documents and `VIRTUAL_MODULES` (a project module beside `src/`,
 *     such as `shared/digits.ts`, is refused, whichever tsconfig includes it), and has an extension
 *     of `SRC_EXTENSIONS` (`scripts/tooling.mjs`, which the lint and the import-graph test read
 *     too: compared exactly, so `.JS`, `.Ts`, `.MJS`, `.es6`, `.jsonc` and no extension are off it,
 *     since the bundler reads any extension it does not know as JavaScript and the lint reads
 *     only the ones it names), has no dot-led segment (a dotfile or dot directory: not read by the
 *     lint's project service), and is spelled as its real path (`realpathSync.native`: a symlink,
 *     or on a case-insensitive file system a case that differs from the file's); (2) every module of `src/api/` is in `API_MODULES`, which states by
 *     file who may import it, statically or dynamically, and why: the box's internals (`box.ts`),
 *     the decoder (`decode.ts`), the client (`client.ts`) and the oracle's counter (`oracle.ts`)
 *     each to the named importers alone, and `index.ts` to the rest of `src/`; a module of
 *     `src/api/` the table lacks, an importer it does not list and an importer that is a virtual
 *     module are refused. `import type` leaves no edge in the graph (the transform removes it
 *     whole), so the gate cannot see it: the lint's import patterns and the import-graph test over
 *     the type checker's program refuse those. `import { type X }` does leave one under the
 *     project's `verbatimModuleSyntax` (a bare `import "..."`), which the gate refuses like any
 *     other. A `require` of a module is no import statement either, and the lint refuses the name;
 *     the graph refuses what it loads like any other module. Beside it the lint refuses
 *     `import.meta.glob` in `src/`.
 * (e) **No operator module in the catalogue entry (D423).** The closure of `index.html` holds no
 *     module of `OPERATOR_ONLY`: the page's operator client (`src/api/operator.ts`, whose module
 *     installs listeners and which holds the curator token), its factory (`src/api/curator.ts`)
 *     and the operator entry's modules (`src/operator/`).
 *
 * (f) **Which modules ship (D427).** Every module in the closure of a document that is under
 *     `src/` outside `src/api/` (the seam, which (d) holds to `API_MODULES`) and is code is named by
 *     the display table (`src/display/rows.ts`, read by `vite.config.ts` through the table's one
 *     reader, `scripts/display-table.mjs`): a row's `module`, an exemption or a helper, or the
 *     build fails with the list of the modules that are not. The bundler's graph is the one
 *     resolver: an `imports` condition (`package.json`), an exact key after a pattern, a package
 *     subpath that climbs back into `src/` (`scheduler/../../src/x.tsx`), an alias, a glob or a
 *     re-export are all resolved before the gate sees them, so no form of import that reaches the
 *     bundle passes unnamed. `gate.test.ts` builds a project through the first three and a plain
 *     import; a glob, a re-export and a plugin's `resolveId` redirect were built by hand on the real
 *     tree (round 3 of the code review) and failed it, and an alias was not built: none of those is
 *     a test. Each build (production and end-to-end) is its own graph; the harness is a helper of
 *     the table, and (a) keeps it out of production. What the check does not say:
 *     that a named module shows no data (the exemptions and helpers are reviewed, not checked), and
 *     that a row's module is reached. Closed here, not by the display gate: a worker, a raw
 *     `new URL(...)` asset, a third document (`ROOT_ASSETS`) and an extension off `SRC_EXTENSIONS`
 *     (`.cts` among them) fail the build by (c) and (d). A symlink is resolved by Vite to its target,
 *     which (f) checks like any module (a link to a named module passes, as the named module).
 *     Not closed: what a plugin's `load` or `transform` of a named module, or a `define`, of
 *     `vite.config.ts` could add (a reviewed edit of that file), and a classic `<script src>` in a
 *     document that names a module under `src/`, which passes (f) and check-bundle but ships
 *     nothing (Vite leaves the tag unbundled and the loader refuses the file).
 *
 * The plugin can only fail the build: a report it wrote into `dist/` would be refused by the
 * loader, which admits nothing at a bundle's root but its own files.
 */
import { realpathSync } from "node:fs";
import path from "node:path";

import type { Plugin } from "vite";

import { EXTENSIONS } from "../scripts/check-bundle.mjs";
import type { Table } from "../scripts/display-table.mjs";
import { SRC_EXTENSIONS, srcKind } from "../scripts/tooling.mjs";

export const OPERATOR_ENTRY = "operator.html";

/** The catalogue entry, whose graph holds no operator module (`OPERATOR_ONLY`). */
export const CATALOGUE_ENTRY = "index.html";

/** The packages the operator entry's graph may hold: React's own closure. */
export const OPERATOR_PACKAGES: ReadonlySet<string> = new Set(["react", "react-dom", "scheduler"]);

/** The virtual modules a Vite 8 build puts in an entry's graph (Rolldown's runtime, Vite's
 * module-preload polyfill and its preload helper), named exactly; any other is refused. */
export const VIRTUAL_MODULES: ReadonlySet<string> = new Set([
  "\0rolldown/runtime.js",
  "\0vite/modulepreload-polyfill.js",
  "\0vite/preload-helper.js",
]);

/** The queries and fragments a module id of the operator entry's graph may carry, each with its
 * reason. Measured from a real build of the skeleton, both modes: no module of any chunk carries
 * one (CSS is emitted as an asset, and its `?used` never reaches a chunk's modules), so the table
 * is empty, and a test holds it equal to what the real builds produce. */
export const OPERATOR_QUERIES: ReadonlyMap<string, string> = new Map();

/** The extensions of a file Vite may emit as an asset: the loader's own list but for `.js`, which
 * a chunk alone is (the list is `check-bundle`'s, which a test holds equal to the server's). */
export const ASSET_EXTENSIONS: ReadonlySet<string> = new Set([...EXTENSIONS].filter((extension) => extension !== ".js"));

/** The files of a bundle's root that Vite emits, by name: the two documents and the manifest. */
export const ROOT_ASSETS: ReadonlySet<string> = new Set(["index.html", "operator.html", ".vite/manifest.json"]);

/** What the gate reads of an output chunk. */
export interface GateChunk {
  readonly fileName: string;
  readonly isEntry: boolean;
  readonly facadeModuleId: string | null;
  readonly imports: readonly string[];
  readonly dynamicImports: readonly string[];
  readonly moduleIds: readonly string[];
}

export interface GateOptions {
  /** Whether the build may hold the harness: true for the end-to-end build alone. */
  readonly harness: boolean;
  /** The modules the display table names (`displayModules`), for (f); `null` or absent: the table's
   * check is not made (a synthetic project's tests that are about another rule; `vite.config.ts`
   * always gives the table's, and a test holds that). */
  readonly display?: ReadonlySet<string> | null;
}

/** The packages a path is in: one for each `node_modules` segment of it, scopes included,
 * outermost first; none for a path with no such segment; `""` for a segment that ends at
 * `node_modules` or names a scope alone, which no package is. Whole segments: a directory
 * `my_node_modules` is none. */
export function packagesOf(id: string): string[] {
  const parts = id.split("/");
  const found: string[] = [];
  parts.forEach((part, index) => {
    if (part !== "node_modules") {
      return;
    }
    const first = parts[index + 1] ?? "";
    if (first.startsWith("@")) {
      const second = parts[index + 2] ?? "";
      found.push(second === "" ? "" : `${first}/${second}`);
    } else {
      found.push(first);
    }
  });
  return found;
}

function inside(relative: string, directory: string): boolean {
  return relative === directory || relative.startsWith(`${directory}/`);
}

/** Where a module id's file is: `suffix` is the query or fragment (from the first `?` or `#`),
 * `file` what is before it, `relative` the file's path relative to the project (`null` for one
 * outside it and for a path that is not absolute), and `normal` whether the file is its own
 * normal form (no `..`, `.` or `//` segment), since a path normaliser would place it elsewhere. */
interface Place {
  readonly suffix: string;
  readonly relative: string | null;
  readonly normal: boolean;
}

function placeOf(root: string, id: string): Place {
  const at = id.search(/[?#]/u);
  const file = at === -1 ? id : id.slice(0, at);
  const suffix = at === -1 ? "" : id.slice(at);
  if (!path.isAbsolute(file)) {
    return { suffix, relative: null, normal: true };
  }
  const relative = path.relative(root, file).split(path.sep).join("/");
  return {
    suffix,
    relative: relative === "" || relative.startsWith("../") || relative === ".." ? null : relative,
    normal: path.posix.normalize(file) === file,
  };
}

/** Why a module may not be in the operator entry's graph, or `null` if it may. A module's place
 * is decided once, from the file it loads from relative to the project: the package rule applies to the
 * project's own `node_modules/` alone, a `node_modules` segment anywhere else is refused, and
 * everything else goes by where it is. */
export function operatorProblem(root: string, id: string): string | null {
  if (id.startsWith("\0")) {
    return VIRTUAL_MODULES.has(id) ? null : `the virtual module ${JSON.stringify(id)}`;
  }
  const { suffix, relative, normal } = placeOf(root, id);
  if (!normal) {
    return `the module ${JSON.stringify(id)}, whose file is not a normal path`;
  }
  if (suffix !== "" && !OPERATOR_QUERIES.has(suffix)) {
    return `the module ${JSON.stringify(id)}, which carries ${JSON.stringify(suffix)}, a query or fragment the skeleton's build never makes`;
  }
  if (relative === null) {
    return `the module ${JSON.stringify(id)}, outside the allow-list`;
  }
  if (relative === OPERATOR_ENTRY) {
    return null;
  }
  if (inside(relative, "node_modules")) {
    const refused = packagesOf(relative).find((name) => !OPERATOR_PACKAGES.has(name));
    return refused === undefined ? null : `the package ${JSON.stringify(refused)}`;
  }
  if (relative.split("/").includes("node_modules")) {
    return `the module ${JSON.stringify(relative)}, in a node_modules directory that is not the project's own`;
  }
  if (inside(relative, "src/chart")) {
    return `the chart module ${JSON.stringify(relative)}`;
  }
  if (inside(relative, "src")) {
    return null;
  }
  return `the module ${JSON.stringify(id)}, outside the allow-list`;
}

/** The two documents the build starts from. */
export const ENTRIES: readonly string[] = ["index.html", "operator.html"];

/** What the gate reads of a module of the bundler's graph: the ids of the modules that import it
 * and of those it imports, each statically or dynamically (an `import.meta.glob`, `?raw` and a
 * re-export included: the bundler has resolved them all). */
export interface GateModule {
  readonly importers: readonly string[];
  readonly dynamicImporters: readonly string[];
  readonly imports: readonly string[];
  readonly dynamicImports: readonly string[];
}

/** The bundler's module graph, by module id. */
export type GateGraph = ReadonlyMap<string, GateModule>;

/** Who may import a module of `src/api/`: `importers` are the exact files (relative to the
 * project) that may, each with its reason; `outside` is the reason for admitting every module of
 * `src/` outside `src/api/`, which the index alone has. Static and dynamic importers are held to
 * the same list. */
export interface ApiModule {
  readonly importers: ReadonlyMap<string, string>;
  readonly outside?: string;
}

/** The modules of `src/api/` the real builds hold, with their importers as the graph shows them
 * (read from a build of the skeleton, both modes, and held equal to it by a test). A module of
 * `src/api/` not listed here fails the build until it is, with its importers and why. */
export const API_MODULES: ReadonlyMap<string, ApiModule> = new Map([
  [
    "src/api/index.ts",
    {
      importers: new Map(),
      outside: "the app's door: every module of src/ outside src/api/ reaches the client through this module alone",
    },
  ],
  [
    "src/api/box.ts",
    {
      importers: new Map([
        ["src/api/decode.ts", "decode boxes each number of a body from its source text: it alone makes a box"],
        ["src/api/index.ts", "re-exports isServerNumber, the brand check, which gives nothing of a number"],
        ["src/api/curator.ts", "the operator's client reads answers and asks isServerNumber whether a value is a box, which gives nothing of a number"],
        ["src/api/provenance.ts", "the recorder asks isServerNumber whether a node is a box, and idAt, the one function that reads a box's text (textOf), reads a request id's (D425)"],
        ["src/api/typed.ts", "the walk of an answer boxes each string (boxText) and each map of data keys (boxMap), and asks isServerNumber whether a value is a number's box (D426)"],
        ["src/api/text.ts", "the takers of a server text read it (readText) and a map's entries (entriesOf), giving a member, an id, a choice, a boolean or an order (D426)"],
      ]),
    },
  ],
  [
    "src/api/decode.ts",
    {
      importers: new Map([
        ["src/api/client.ts", "exchange reads every body through decode: the one decoder"],
        ["src/api/index.ts", "re-exports DecodeError, the decoder's refusal"],
        ["src/api/oracle.ts", "exports the decoder's counter, which the end-to-end harness reads"],
      ]),
    },
  ],
  [
    "src/api/client.ts",
    {
      importers: new Map([
        ["src/api/index.ts", "re-exports start, REFUSALS, the cap and the client's errors"],
        ["src/api/curator.ts", "the operator's client sends through send, the one function that sets the operator's credentials (D423)"],
        ["src/api/typed.ts", "the typed answers send through exchange and walk its raw answer: the seam (D426)"],
        ["src/api/oracle.ts", "the harness's oracle sends the health check and the operator's datasets through exchange (D419)"],
      ]),
    },
  ],
  [
    "src/api/oracle.ts",
    { importers: new Map([["src/harness/Harness.tsx", "the end-to-end harness reads the counter: nothing else may, and no production build holds it"]]) },
  ],
  [
    "src/api/route.ts",
    {
      importers: new Map([
        ["src/api/client.ts", "exchange and send send to a route the generated functions made, whose URL and dataset urlOf and datasetOf alone read (D425)"],
        ["src/api/generated/routes.ts", "each generated function makes its route with route, segment and search"],
      ]),
    },
  ],
  [
    "src/api/generated/routes.ts",
    {
      importers: new Map([
        ["src/api/index.ts", "re-exports the generated functions as routes"],
        ["src/api/curator.ts", "the operator's client makes the routes of its operations"],
        ["src/api/typed.ts", "each typed function makes its operation's route (D426)"],
        ["src/api/oracle.ts", "the harness's oracle makes the health check's route and the operator's datasets'"],
      ]),
    },
  ],
  [
    "src/api/curator.ts",
    { importers: new Map([["src/api/operator.ts", "makes the page's one operator client (D423)"]]) },
  ],
  [
    "src/api/ids.ts",
    { importers: new Map([["src/api/provenance.ts", "idAt makes a request id through requestId, the grammar, its one maker (D425)"]]) },
  ],
  [
    "src/api/origin.ts",
    {
      importers: new Map([
        ["src/api/provenance.ts", "the recorder stamps the feeding nodes of an answer as it is decoded, and idAt reads a node's origin back, refusing a node without one or with another (D425)"],
      ]),
    },
  ],
  [
    "src/api/provenance.ts",
    {
      importers: new Map([
        ["src/api/client.ts", "send's reader records a 2xx answer's feeding nodes through recorder, as decode makes them (D425)"],
        ["src/api/curator.ts", "the four flows read their id through idAt, the one function that reads a box's text, by a position's name (D425)"],
      ]),
    },
  ],
  [
    "src/api/operator.ts",
    {
      importers: new Map([
        ["src/operator/Shell.tsx", "the token's shell unlocks, shows and forgets the page's operator client (D423)"],
        ["src/harness/Harness.tsx", "the end-to-end harness drives the page's operator client for the matrix"],
      ]),
    },
  ],
  [
    "src/api/typed.ts",
    {
      importers: new Map([
        ["src/api/index.ts", "re-exports the typed functions, their outcome and the boxed types: the seam (D426)"],
        ["src/api/oracle.ts", "the harness's oracle reads three public tools through the seam (D426)"],
      ]),
    },
  ],
  [
    "src/api/text.ts",
    { importers: new Map([["src/api/index.ts", "re-exports the takers of a server text, each pinned by its signature (D426)"]]) },
  ],
  [
    "src/api/generated/shapes.ts",
    {
      importers: new Map([
        ["src/api/typed.ts", "the walk reads the table generated from the document (D426)"],
        ["src/api/text.ts", "the takers read the generated vocabularies and grammars (D426)"],
      ]),
    },
  ],
]);

/** The modules the catalogue entry's graph may not hold (D423): the operator's client, whose
 * module installs listeners on the page and holds the curator token, and the operator entry's
 * own modules. */
export const OPERATOR_ONLY: readonly string[] = ["src/api/operator.ts", "src/api/curator.ts", "src/operator"];

/** The file a module id loads from, relative to the project: its id up to the first `?` or `#`,
 * `null` when that is no path inside the project. */
function fileOf(root: string, id: string): string | null {
  return id.startsWith("\0") ? null : placeOf(root, id).relative;
}

/** Why a module may not be in a document's closure, or `null`: every project module is under
 * `src/` and has an extension of `SRC_EXTENSIONS` the lint reads (code or a stylesheet); a
 * document, a package of the project's own `node_modules/` and a virtual module of Vite's are
 * admitted (the operator entry's packages are `operatorProblem`'s). `documents` are the
 * documents the build starts from, relative to the project. */
export function confinementProblem(root: string, id: string, documents: readonly string[] = ENTRIES): string | null {
  if (id.startsWith("\0")) {
    return VIRTUAL_MODULES.has(id) ? null : `the virtual module ${JSON.stringify(id)}`;
  }
  const { suffix, relative, normal } = placeOf(root, id);
  if (!normal) {
    return `the module ${JSON.stringify(id)}, whose file is not a normal path`;
  }
  if (suffix !== "" && !OPERATOR_QUERIES.has(suffix)) {
    return `the module ${JSON.stringify(id)}, which carries ${JSON.stringify(suffix)}, a query or fragment the skeleton's build never makes`;
  }
  if (relative === null) {
    return `the module ${JSON.stringify(id)}, outside the project`;
  }
  if (documents.includes(relative)) {
    return null;
  }
  if (inside(relative, "node_modules")) {
    return null;
  }
  if (relative.split("/").includes("node_modules")) {
    return `the module ${JSON.stringify(relative)}, in a node_modules directory that is not the project's own`;
  }
  if (!inside(relative, "src")) {
    return `the project module ${JSON.stringify(relative)}, outside src/`;
  }
  if (relative.split("/").some((segment) => segment.startsWith("."))) {
    return `the module ${JSON.stringify(relative)}, whose path has a dot-led segment (a file the lint's project service does not read)`;
  }
  return srcKind(relative, ["code", "style"])
    ? null
    : `the module ${JSON.stringify(relative)}, whose extension is none the lint reads (${[...SRC_EXTENSIONS].filter(([, kind]) => kind !== "data").map(([extension]) => extension).join(" ")}: D419)`;
}

/** The ids of the modules an entry reaches, itself included, by static and dynamic imports. */
function reached(graph: GateGraph, entry: string): string[] {
  const seen = new Set<string>();
  const pending = [entry];
  for (let id = pending.pop(); id !== undefined; id = pending.pop()) {
    const module = graph.get(id);
    if (seen.has(id) || module === undefined) {
      continue;
    }
    seen.add(id);
    pending.push(...module.imports, ...module.dynamicImports);
  }
  return [...seen];
}

/** Why `importer` may not import the module of `src/api/` the table calls `file`, or `null`. A
 * virtual importer (`\0...`) is no path inside the project (`relative` is `null`), so it is never
 * one of the table's. */
function importerProblem(root: string, file: string, entry: ApiModule, importer: string): string | null {
  const { suffix, relative, normal } = placeOf(root, importer);
  if (suffix === "" && normal && relative !== null) {
    if (entry.importers.has(relative)) {
      return null;
    }
    if (entry.outside !== undefined && inside(relative, "src") && !inside(relative, "src/api")) {
      return null;
    }
  }
  return `the importer ${JSON.stringify(importer)} is not one of ${file}'s (${[...entry.importers.keys(), ...(entry.outside === undefined ? [] : ["the modules of src/ outside src/api/"])].join(", ")})`;
}

/** The modules the display table names, relative to the project: every row's module, every
 * exemption and every helper. */
export function displayModules(table: Pick<Table, "rows" | "exempt" | "helpers">): ReadonlySet<string> {
  return new Set([...table.rows.map((row) => row.module), ...table.exempt.map((entry) => entry.module), ...table.helpers.map((entry) => entry.module)].filter((module) => module !== ""));
}

/** The modules of the closures of `documents` under `src/` outside `src/api/` (code) that the
 * display table does not name (module docstring, (f)), as one problem listing them, or none. */
export function displayProblems(root: string, graph: GateGraph, documents: readonly string[], named: ReadonlySet<string>): string[] {
  const unnamed = new Set<string>();
  for (const document of documents) {
    const entry = path.join(root, document);
    if (!graph.has(entry)) {
      continue;
    }
    for (const id of reached(graph, entry)) {
      const relative = id.startsWith("\0") ? null : placeOf(root, id).relative;
      if (relative !== null && inside(relative, "src") && !inside(relative, "src/api") && srcKind(relative, ["code"]) && !named.has(relative)) {
        unnamed.add(relative);
      }
    }
  }
  return unnamed.size === 0
    ? []
    : [`modules of the bundle under src/ outside src/api/ that are no row's module and no exemption or helper of the display table (D427: a module that shows data is a row's module, named in src/display/rows.ts by the pull request that adds it; an exemption or a helper is reviewed, not checked): ${[...unnamed].sort().join(", ")}`];
}

/** Every problem of the module graph (module docstring, (d), (f)). `built` are the documents the build
 * starts from beyond `ENTRIES` (relative to the project), each walked as well; `real` gives a
 * file's real path (the plugin passes `realpathSync.native`), which every module under `src/`
 * must be spelled as: a symlink or a different case on a case-insensitive file system places a
 * module by a path the lint and the table did not read. */
export function graphProblems(
  root: string,
  graph: GateGraph,
  built: readonly string[] = [],
  real: (file: string) => string = (file) => file,
  display: ReadonlySet<string> | null = null,
): string[] {
  const problems: string[] = [];
  const documents = [...new Set([...ENTRIES, ...built])];
  for (const document of documents) {
    const entry = path.join(root, document);
    if (!graph.has(entry)) {
      problems.push(`the build has no module for ${document}`);
      continue;
    }
    for (const id of reached(graph, entry)) {
      const relative = id.startsWith("\0") ? null : placeOf(root, id).relative;
      if (document === CATALOGUE_ENTRY && relative !== null && OPERATOR_ONLY.some((place) => inside(relative, place))) {
        problems.push(`in ${document}'s graph: the operator module ${JSON.stringify(relative)} (D423)`);
      }
      const problem = confinementProblem(root, id, documents);
      if (problem !== null) {
        problems.push(`in ${document}'s graph: ${problem}`);
      } else if (!id.startsWith("\0") && inside(placeOf(root, id).relative ?? "", "src")) {
        const file = id.slice(0, id.search(/[?#]|$/u));
        let found: string | null;
        try {
          found = real(file);
        } catch {
          found = null;
        }
        if (found !== file) {
          problems.push(`in ${document}'s graph: the module ${JSON.stringify(id)}, whose path is not its real path (a symlink, or the file's own case)`);
        }
      }
    }
  }
  for (const [id, module] of graph) {
    const file = fileOf(root, id);
    if (file === null || !inside(file, "src/api")) {
      continue;
    }
    const entry = API_MODULES.get(file);
    if (entry === undefined) {
      problems.push(`${JSON.stringify(file)} is a module of src/api/ the gate's table does not list (D419)`);
      continue;
    }
    for (const [importers, how] of [[module.importers, "imported"], [module.dynamicImporters, "imported dynamically"]] as const) {
      for (const importer of importers) {
        const problem = importerProblem(root, file, entry, importer);
        if (problem !== null) {
          problems.push(`${file} is ${how}: ${problem}`);
        }
      }
    }
  }
  if (display !== null) {
    problems.push(...displayProblems(root, graph, documents, display));
  }
  return [...new Set(problems)];
}

/** The chunks of the operator entry's closure, by file name: its chunk and every chunk it
 * imports, statically or dynamically. */
export function operatorClosure(root: string, chunks: readonly GateChunk[]): GateChunk[] | string {
  const entry = path.join(root, OPERATOR_ENTRY);
  const entries = chunks.filter((chunk) => chunk.isEntry && chunk.facadeModuleId === entry);
  if (entries.length !== 1) {
    return `the build has ${String(entries.length)} chunks for ${OPERATOR_ENTRY}, not one`;
  }
  const byName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
  const seen = new Map<string, GateChunk>();
  const pending = entries.map((chunk) => chunk.fileName);
  for (let name = pending.pop(); name !== undefined; name = pending.pop()) {
    if (seen.has(name)) {
      continue;
    }
    const chunk = byName.get(name);
    if (chunk === undefined) {
      return `${OPERATOR_ENTRY}'s graph imports ${JSON.stringify(name)}, which the build lacks`;
    }
    seen.set(name, chunk);
    pending.push(...chunk.imports, ...chunk.dynamicImports);
  }
  return [...seen.values()];
}

/** The emitted assets the loader would not serve, by name (module docstring, (c)): a file under
 * `assets/` must have an extension of `ASSET_EXTENSIONS`, one at the root must be of
 * `ROOT_ASSETS`. */
export function assetProblems(fileNames: readonly string[]): string[] {
  return fileNames
    .filter((name) =>
      name.startsWith("assets/") ? !ASSET_EXTENSIONS.has(path.posix.extname(name)) : !ROOT_ASSETS.has(name),
    )
    .map((name) => `${name}: a file of a type the loader does not serve (a script that is no chunk of the build, for one), which the gate cannot read`);
}

/** Every problem the gate finds in a build's chunks (module docstring). */
export function gateProblems(
  root: string,
  chunks: readonly GateChunk[],
  options: GateOptions,
): string[] {
  const problems: string[] = [];
  if (!options.harness) {
    for (const chunk of chunks) {
      for (const id of chunk.moduleIds) {
        const { relative, normal } = placeOf(root, id);
        if (!normal) {
          problems.push(`${chunk.fileName}: the module ${JSON.stringify(id)}, whose file is not a normal path`);
        } else if (relative !== null && inside(relative, "src/harness")) {
          problems.push(`${chunk.fileName}: the harness module ${JSON.stringify(relative)}`);
        }
      }
    }
  }
  const closure = operatorClosure(root, chunks);
  if (typeof closure === "string") {
    return [...problems, closure];
  }
  for (const chunk of closure) {
    for (const id of chunk.moduleIds) {
      const problem = operatorProblem(root, id);
      if (problem !== null) {
        problems.push(`${chunk.fileName}, in ${OPERATOR_ENTRY}'s graph: ${problem}`);
      }
    }
  }
  return problems;
}

/** The plugin of `worker.plugins` that refuses any worker (module docstring, (c)): Vite runs it
 * at the start of each worker's own build, so whatever form asked for the worker, it fails. */
export function noWorker(): Plugin {
  return {
    name: "aibi:no-worker",
    buildStart() {
      this.error(
        "The build's gate refuses it (D411): the build has a worker (`?worker`, `?sharedworker`, " +
          "`new Worker(new URL(...))`, inline or not), which the gate cannot read and the policy forbids",
      );
    },
  };
}

/** The gate as a Vite plugin, for production builds alone (`apply: "build"`); `api` exposes its
 * options so that a test can see what a configuration gave it. */
export function operatorGate(options: GateOptions): Plugin<GateOptions> {
  let root = "";
  return {
    name: "aibi:operator-gate",
    apply: "build",
    enforce: "post",
    api: options,
    configResolved(config) {
      root = realpathSync(config.root);
    },
    generateBundle(_output, bundle) {
      const chunks: GateChunk[] = [];
      const assets: string[] = [];
      for (const output of Object.values(bundle)) {
        if (output.type === "chunk") {
          chunks.push(output);
        } else {
          assets.push(output.fileName);
        }
      }
      const graph = new Map<string, GateModule>();
      for (const id of this.getModuleIds()) {
        const info = this.getModuleInfo(id);
        if (info !== null) {
          graph.set(id, {
            importers: info.importers,
            dynamicImporters: info.dynamicImporters,
            imports: info.importedIds,
            dynamicImports: info.dynamicallyImportedIds,
          });
        }
      }
      const built: string[] = [];
      const strays: string[] = [];
      for (const chunk of chunks) {
        if (chunk.isEntry && chunk.facadeModuleId !== null) {
          const relative = path.relative(root, chunk.facadeModuleId);
          (relative.endsWith(".html") ? built : strays).push(relative);
        }
      }
      const problems = [
        ...gateProblems(root, chunks, options),
        ...graphProblems(root, graph, built, (file) => realpathSync.native(file), options.display ?? null),
        ...strays.map((name) => `the build has an entry that is no document: ${JSON.stringify(name)}`),
        ...assetProblems(assets),
      ];
      if (problems.length > 0) {
        this.error(`The build's gate refuses it (D411):\n${problems.join("\n")}`);
      }
    },
  };
}
