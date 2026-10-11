// @ts-check
/**
 * The files of the package that are tooling, not the app (D419), by name: the one list the lint
 * (`eslint.config.mjs`) and the import-graph test (`tests/gates/api-graph.test.ts`) read. Every
 * other file of the package is the app, and gets the app's rules, so a new directory is under
 * them by default; the build's gate holds besides that no module outside `src/` reaches either
 * entry (`plugins/gate.ts`).
 *
 * **The extensions under `src/` are one table too** (`SRC_EXTENSIONS`), read by the lint, the
 * build's gate (`plugins/gate.ts`) and the import-graph test: a file under `src/` whose extension
 * is not on it (compared exactly, so `.JS`, `.Ts`, `.es6`, `.jsonc` and no extension are off it)
 * is refused, because the bundler reads any extension it does not know as JavaScript and the lint
 * reads only the ones it names (D419).
 *
 * The tooling runs in Node, at build or test time, and never ships in the bundle: the
 * configurations (`eslint.config.mjs`, `vite.config.ts`, `vitest.config.ts`,
 * `playwright.config.ts`) and `scripts/` read files, spawn processes and convert numbers; `plugins/`
 * is Vite's gate, which reads the build's own output; `tests/` and `e2e/` drive the app and so
 * call `fetch`, read response bodies and convert numbers on purpose, to check what the app may not
 * do (D419's oracle and corpus are written there).
 */

/** The directories of tooling. */
export const TOOLING_DIRECTORIES = ["e2e", "plugins", "scripts", "tests"];

/** The files of tooling at the package's root. */
export const TOOLING_FILES = ["eslint.config.mjs", "playwright.config.ts", "vite.config.ts", "vitest.config.ts"];

/** The lint's ignore patterns of the tooling (relative to the package's root). */
export const TOOLING_PATTERNS = [...TOOLING_DIRECTORIES.map((directory) => `${directory}/**`), ...TOOLING_FILES];

/**
 * Whether a path relative to the package's root is tooling.
 * @param {string} relative
 */
export function isTooling(relative) {
  return TOOLING_FILES.includes(relative) || TOOLING_DIRECTORIES.some((directory) => relative.startsWith(`${directory}/`));
}

/** What a file under `src/` may be, by its extension (exact, case-sensitive; the part after the
 * last dot, dot included). `code` is linted with the app's rules and may be in a bundle's graph;
 * `style` is a stylesheet, in the graph and no code; `data` may exist there (the server and the
 * tests read it) and is no module of a bundle. Anything else, no extension included, is refused. */
export const SRC_EXTENSIONS = new Map([
  [".ts", "code"],
  [".tsx", "code"],
  [".css", "style"],
  [".json", "data"],
]);

/** The extensions of script files the lint reads (the app's rules outside `src/` too, so a new
 * directory is under them): `SRC_EXTENSIONS`' code and the other spellings Node and the bundler
 * read as scripts. */
export const SCRIPT_EXTENSIONS = [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"];

/**
 * Whether a file under `src/` (a path relative to the package's root) has an extension of
 * `SRC_EXTENSIONS` of one of the given kinds and no dot-led segment (a dotfile or a dot
 * directory: the lint's project service does not read it, so it is refused by construction, not
 * left to a configuration that happens not to ignore it).
 * @param {string} relative
 * @param {readonly string[]} kinds
 */
export function srcKind(relative, kinds) {
  if (relative.split("/").some((segment) => segment.startsWith("."))) {
    return false;
  }
  const name = relative.slice(relative.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  const kind = dot <= 0 ? undefined : SRC_EXTENSIONS.get(name.slice(dot));
  return kind !== undefined && kinds.includes(kind);
}
