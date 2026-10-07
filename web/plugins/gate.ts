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
 * The plugin can only fail the build: a report it wrote into `dist/` would be refused by the
 * loader, which admits nothing at a bundle's root but its own files.
 */
import { realpathSync } from "node:fs";
import path from "node:path";

import type { Plugin } from "vite";

import { EXTENSIONS } from "../scripts/check-bundle.mjs";

export const OPERATOR_ENTRY = "operator.html";

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
      const problems = [...gateProblems(root, chunks, options), ...assetProblems(assets)];
      if (problems.length > 0) {
        this.error(`The build's gate refuses it (D411):\n${problems.join("\n")}`);
      }
    },
  };
}
