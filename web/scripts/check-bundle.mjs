// @ts-check
/**
 * The checks of a built bundle after the build (D411, D418), for what neither the build's gate
 * (`plugins/gate.ts`, which sees modules) nor the loader enforces, then the loader itself:
 *
 * - the root holds `.vite/manifest.json`, `assets/` and the build's own `index.html` and
 *   `operator.html` alone, and the manifest has both entries;
 * - every file of `assets/` is one the manifest names, with a name of the loader's grammar
 *   (`^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$`, no `..`), of an extension it serves (`.js`, `.css`,
 *   `.svg`, `.png`), and hashed as the loader decides it (`<stem>-<8 of [A-Za-z0-9_-]>`, the stem
 *   the manifest gives): the loader serves an unhashed name with `no-cache`, so a name the build
 *   failed to hash would pass it and lose the year's cache;
 * - the files' count and sizes are under the loader's caps, and their bytes under the checked-in
 *   budget (`budget.json`: the measured build and 15%);
 * - the build's own HTML holds no inline script or style (the server never serves it, but a build
 *   that writes one has code the policy would refuse);
 * - the harness's marker is absent from every script of a production build (`--mode prod`) and
 *   present in one of an end-to-end build (`--mode e2e`);
 * - **the real loader accepts it**: `uv run aibi-server check` with a temporary configuration
 *   whose `web_bundle` is the directory, and it reports the operator entry.
 *
 * It writes nothing into the bundle: the loader refuses any other file at a bundle's root.
 * Build under `umask 022`: the loader refuses a file or directory group or others can write.
 *
 * The budget is read strictly (`policy-files.mjs`: its exact shape, no repeated key); a budget
 * that is not one is a usage error. The loader runs `uv run --frozen`: it never re-locks or
 * syncs `server/`'s environment, which CI has made.
 *
 * Usage: `node scripts/check-bundle.mjs --mode prod|e2e <dir> [--budget <file>] [--no-loader]`.
 */
import { spawnSync } from "node:child_process";
import { chmodSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { checkBudget, PolicyError, readPolicy } from "./policy-files.mjs";

export const HARNESS_MARKER = "aibi-e2e-harness-1b6f0c";
export const ASSET_NAME = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$/u;
export const HASH = /^[A-Za-z0-9_-]{8}$/u;
export const EXTENSIONS = new Set([".js", ".css", ".svg", ".png"]);
export const MAX_FILES = 2000;
export const MAX_FILE_BYTES = 16 * 1024 * 1024;
export const MAX_BUNDLE_BYTES = 64 * 1024 * 1024;
const ROOT_FILES = new Set([".vite", "assets", "index.html", "operator.html"]);
const MODES = new Set(["prod", "e2e"]);
const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

/**
 * A JSON file's value, unknown until its reader says what it is.
 * @param {string} file
 * @returns {unknown}
 */
function readJson(file) {
  /** @type {unknown} */
  const value = JSON.parse(readFileSync(file, "utf8"));
  return value;
}

/**
 * @typedef {{ file: string, name?: string, isEntry?: boolean, imports?: string[],
 *   dynamicImports?: string[], css?: string[], assets?: string[] }} Chunk
 * @typedef {Record<string, Chunk>} Manifest
 * @typedef {import("./policy-files.mjs").Budget} Budget
 */

/**
 * The stems the build would give a manifest's output (the loader's `_stems`): a chunk's own
 * name for its script and stylesheets, the key's file name for a file without one.
 * @param {string} key
 * @param {Chunk} chunk
 * @param {"file" | "css" | "assets"} member
 * @returns {string[]}
 */
export function stems(key, chunk, member) {
  if (member === "assets") {
    return [];
  }
  if (chunk.name !== undefined) {
    return [chunk.name];
  }
  const base = key.slice(key.lastIndexOf("/") + 1);
  const dot = base.lastIndexOf(".");
  const stem = dot > 0 ? base.slice(0, dot) : base;
  return member === "file" && !chunk.file.endsWith(".js") ? [stem] : [];
}

/**
 * Whether a file name is `<stem>-<hash>` before its extension for one of `found` (the loader's
 * `hashed`).
 * @param {string} name
 * @param {Iterable<string>} found
 */
export function hashed(name, found) {
  const dot = name.lastIndexOf(".");
  const base = dot > 0 ? name.slice(0, dot) : name;
  for (const stem of found) {
    if (base.startsWith(`${stem}-`) && HASH.test(base.slice(stem.length + 1))) {
      return true;
    }
  }
  return false;
}

/**
 * The problems of the build's own HTML: an inline script or style.
 * @param {string} name
 * @param {string} html
 * @returns {string[]}
 */
export function htmlProblems(name, html) {
  const problems = [];
  for (const tag of html.matchAll(/<script\b[^>]*>/giu)) {
    if (!/\ssrc\s*=/iu.test(tag[0])) {
      problems.push(`${name}: an inline script`);
    }
  }
  if (/<style\b/iu.test(html)) {
    problems.push(`${name}: an inline stylesheet`);
  }
  if (/<[^>]*\sstyle\s*=/iu.test(html)) {
    problems.push(`${name}: a style attribute`);
  }
  if (/<[^>]*\son[a-z]+\s*=/iu.test(html)) {
    problems.push(`${name}: an event handler attribute`);
  }
  if (/javascript:/iu.test(html)) {
    problems.push(`${name}: a javascript: URL`);
  }
  return problems;
}

/**
 * Every problem of the bundle in `dir` but the loader's own (module docstring).
 * @param {string} dir
 * @param {{ mode: "prod" | "e2e", budget: Budget }} options
 * @returns {string[]}
 */
export function bundleProblems(dir, options) {
  const problems = [];
  const root = readdirSync(dir).sort();
  for (const name of root) {
    if (!ROOT_FILES.has(name)) {
      problems.push(`${name}: not a file of a bundle's root`);
    }
  }
  /** @type {Manifest} */
  let manifest;
  try {
    manifest = /** @type {Manifest} */ (readJson(path.join(dir, ".vite", "manifest.json")));
  } catch {
    return [...problems, ".vite/manifest.json: missing or not JSON"];
  }
  for (const entry of ["index.html", "operator.html"]) {
    if (manifest[entry]?.isEntry !== true) {
      problems.push(`.vite/manifest.json: ${entry} is not an entry`);
    }
  }
  /** @type {Map<string, Set<string>>} */
  const outputs = new Map();
  for (const [key, chunk] of Object.entries(manifest)) {
    for (const [member, values] of /** @type {const} */ ([
      ["file", [chunk.file]],
      ["css", chunk.css ?? []],
      ["assets", chunk.assets ?? []],
    ])) {
      for (const value of values) {
        const name = value.startsWith("assets/") ? value.slice("assets/".length) : null;
        if (name === null) {
          problems.push(`.vite/manifest.json: ${JSON.stringify(key)} names ${JSON.stringify(value)}, outside assets/`);
          continue;
        }
        const found = outputs.get(name) ?? new Set();
        for (const stem of stems(key, chunk, member)) {
          found.add(stem);
        }
        outputs.set(name, found);
      }
    }
  }
  const assets = path.join(dir, "assets");
  const names = readdirSync(assets).sort();
  if (names.length > MAX_FILES) {
    problems.push(`assets/: ${String(names.length)} files, more than ${String(MAX_FILES)}`);
  }
  let total = 0;
  let marked = false;
  for (const name of names) {
    const status = lstatSync(path.join(assets, name));
    if (!status.isFile()) {
      problems.push(`assets/${name}: not a regular file`);
      continue;
    }
    if (!ASSET_NAME.test(name) || name.includes("..")) {
      problems.push(`assets/${JSON.stringify(name)}: not a name the loader admits`);
    }
    const extension = path.extname(name);
    if (!EXTENSIONS.has(extension)) {
      problems.push(`assets/${name}: not of a type the loader serves`);
    }
    const found = outputs.get(name);
    if (found === undefined) {
      problems.push(`assets/${name}: not named by the manifest`);
    } else if (!hashed(name, found)) {
      problems.push(`assets/${name}: not <stem>-<hash> for the stem the manifest gives`);
    }
    if (status.size > MAX_FILE_BYTES) {
      problems.push(`assets/${name}: more than ${String(MAX_FILE_BYTES)} bytes`);
    }
    total += status.size;
    if (extension === ".js" && readFileSync(path.join(assets, name), "utf8").includes(HARNESS_MARKER)) {
      marked = true;
    }
  }
  for (const name of outputs.keys()) {
    if (!names.includes(name)) {
      problems.push(`.vite/manifest.json: names assets/${name}, which the build lacks`);
    }
  }
  if (total > MAX_BUNDLE_BYTES) {
    problems.push(`assets/: ${String(total)} bytes, more than ${String(MAX_BUNDLE_BYTES)}`);
  }
  const allowance = options.budget[options.mode];
  if (total > allowance.bytes) {
    problems.push(`assets/: ${String(total)} bytes, over the budget of ${String(allowance.bytes)} (budget.json)`);
  }
  if (names.length > allowance.files) {
    problems.push(`assets/: ${String(names.length)} files, over the budget of ${String(allowance.files)} (budget.json)`);
  }
  for (const name of ["index.html", "operator.html"]) {
    if (root.includes(name)) {
      problems.push(...htmlProblems(name, readFileSync(path.join(dir, name), "utf8")));
    }
  }
  if (options.mode === "prod" && marked) {
    problems.push("assets/: a script holds the harness, which a production build must not");
  }
  if (options.mode === "e2e" && !marked) {
    problems.push("assets/: no script holds the harness, which the end-to-end build must");
  }
  return problems;
}

/**
 * The loader's verdict on the bundle in `dir`: `aibi-server check` with a temporary
 * configuration, run from `server/` (`uv run`); its problems, none when it accepts the bundle
 * and reports the operator entry. The configuration and its directories are removed after.
 * @param {string} dir
 * @returns {string[]}
 */
export function loaderProblems(dir) {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-check-bundle-"));
  try {
    mkdirSync(path.join(scratch, "data"));
    mkdirSync(path.join(scratch, "imports"));
    const config = path.join(scratch, "aibi.toml");
    writeFileSync(
      config,
      [
        "[server]",
        'bind = "127.0.0.1"',
        `web_bundle = ${JSON.stringify(path.resolve(dir))}`,
        "[curator]",
        `token_hash = "sha256:${"0".repeat(64)}"`,
        "[storage]",
        `data = ${JSON.stringify(path.join(scratch, "data"))}`,
        `imports = [${JSON.stringify(path.join(scratch, "imports"))}]`,
        "",
      ].join("\n"),
      { mode: 0o600 },
    );
    chmodSync(config, 0o600);
    const run = spawnSync("uv", ["run", "--frozen", "--quiet", "aibi-server", "check", "--config", config], {
      cwd: path.join(WEB, "..", "server"),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    if (run.error !== undefined) {
      return [`the loader could not be run (${run.error.message})`];
    }
    if (run.status !== 0) {
      return [`the loader refuses the bundle (exit ${String(run.status)}):`, ...run.stderr.trim().split("\n")];
    }
    if (!run.stdout.includes("operator entry at /curate: yes")) {
      return ["the loader accepts the bundle without its operator entry"];
    }
    return [];
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

/**
 * The command line (module docstring); the exit status: 0 when the bundle passes, 1 when it
 * does not, 2 for a usage error or a budget that is not one.
 * @param {readonly string[]} argv
 * @param {(line: string) => void} say
 * @returns {number}
 */
export function main(argv, say) {
  /** @type {string | undefined} */
  let mode;
  /** @type {string | undefined} */
  let dir;
  let budgetFile = path.join(WEB, "budget.json");
  let loader = true;
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--mode") {
      mode = argv[(index += 1)];
    } else if (arg === "--budget") {
      budgetFile = argv[(index += 1)] ?? "";
    } else if (arg === "--no-loader") {
      loader = false;
    } else if (dir === undefined && arg !== undefined && !arg.startsWith("--")) {
      dir = arg;
    } else {
      say(`check-bundle: unknown argument ${JSON.stringify(arg)}`);
      return 2;
    }
  }
  if (mode === undefined || !MODES.has(mode) || dir === undefined) {
    say("usage: check-bundle.mjs --mode prod|e2e <dir> [--budget <file>] [--no-loader]");
    return 2;
  }
  /** @type {Budget} */
  let budget;
  try {
    budget = readPolicy(budgetFile, checkBudget);
  } catch (error) {
    if (error instanceof PolicyError) {
      say(`check-bundle: ${error.message}`);
      return 2;
    }
    throw error;
  }
  const problems = bundleProblems(dir, { mode: /** @type {"prod" | "e2e"} */ (mode), budget });
  if (problems.length === 0 && loader) {
    problems.push(...loaderProblems(dir));
  }
  for (const problem of problems) {
    say(`check-bundle: ${problem}`);
  }
  if (problems.length === 0) {
    say(`check-bundle: ${dir} passes (${mode})`);
  }
  return problems.length === 0 ? 0 : 1;
}

if (process.argv[1] !== undefined && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2), (line) => {
    process.stdout.write(`${line}\n`);
  });
}
