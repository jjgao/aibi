/**
 * The lint's file set and the build's (D419): a module the build holds that the lint did not read
 * is code outside every rule, so the two are held to one another. ESLint's own computed
 * configuration (`calculateConfigForFile`) says which layer a file gets, for every file of the
 * package and for a table of hypothetical paths (every case variant and exotic extension): the
 * app's layer, the client's, or none (tooling). A file under `src/` is the app's or is refused as
 * a stray, by the one table of extensions (`SRC_EXTENSIONS`, `scripts/tooling.mjs`), which the
 * gate (`plugins/gate.ts`) reads too; a file outside `src/` and the tooling is the app's by
 * default, or is one of the package's named root files.
 */
import { mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

import { confinementProblem } from "../../plugins/gate";
import { isTooling, SCRIPT_EXTENSIONS, SRC_EXTENSIONS, srcKind, TOOLING_DIRECTORIES, TOOLING_FILES, TOOLING_PATTERNS } from "../../scripts/tooling.mjs";
import { WEB } from "./program";

const eslint = new ESLint({ cwd: WEB });

type Layer = "app" | "api" | "client" | "none";

/** The layer ESLint's configuration gives a file, read from the rules that name it: the app's has
 * the number rules, `src/api/`'s the I/O names as globals, `client.ts` neither; none if the file
 * has no rule of the policy (tooling) or is not linted at all. */
async function layerOf(relative: string): Promise<Layer> {
  const config = (await eslint.calculateConfigForFile(path.join(WEB, relative))) as { rules?: Record<string, unknown> } | undefined;
  const rule = config?.rules?.["no-restricted-syntax"];
  if (!Array.isArray(rule)) {
    return "none";
  }
  const messages = rule.slice(1).map((option) => (option as { message?: string }).message ?? "");
  const has = (prefix: string): boolean => messages.some((message) => message.startsWith(prefix));
  if (!has("No import.meta.glob")) {
    return "none";
  }
  if (has("No conversion of a value to a number")) {
    return "app";
  }
  const globals: unknown = config?.rules?.["no-restricted-globals"];
  const names = Array.isArray(globals) ? globals.slice(1).map((option) => (option as { name?: string }).name) : [];
  return names.includes("fetch") ? "api" : "client";
}

/** Hypothetical paths and the layer each must get, or `"stray"`: a file under `src/` the table of
 * extensions refuses (whichever layer ESLint gives it, it is no part of a build). */
const PATHS: readonly (readonly [string, Layer | "stray"])[] = [
  ["src/new/x.ts", "app"],
  ["src/x.tsx", "app"],
  ["src/x.d.ts", "app"],
  ["src/tests/x.ts", "app"],
  ["src/e2e/x.ts", "app"],
  ["src/scripts/x.ts", "app"],
  ["src/plugins/x.ts", "app"],
  ["src/dist/x.ts", "app"],
  ["src/root.ts", "app"],
  ["src/harness/Harness.tsx", "app"],
  ["src/operator/x.ts", "app"],
  ["src/catalogue/x.tsx", "app"],
  ["src/api/x.ts", "api"],
  ["src/api/generated/x.ts", "api"],
  ["src/api/decode.ts", "api"],
  ["src/api/client.ts", "client"],
  ["shared/x.ts", "app"],
  ["shared/x.tsx", "app"],
  ["shared/x.mts", "app"],
  ["shared/x.cts", "app"],
  ["shared/x.js", "app"],
  ["shared/x.jsx", "app"],
  ["shared/x.mjs", "app"],
  ["shared/x.cjs", "app"],
  ["testsX/x.ts", "app"],
  ["scriptsx/y.mjs", "app"],
  ["plugins2/y.ts", "app"],
  ["e2ee/y.ts", "app"],
  ["x.ts", "app"],
  ["src/x.mts", "stray"],
  ["src/x.cts", "stray"],
  ["src/x.js", "stray"],
  ["src/x.jsx", "stray"],
  ["src/x.mjs", "stray"],
  ["src/x.cjs", "stray"],
  ["src/x.JS", "stray"],
  ["src/x.Ts", "stray"],
  ["src/x.TS", "stray"],
  ["src/x.TSX", "stray"],
  ["src/x.JSX", "stray"],
  ["src/x.MJS", "stray"],
  ["src/x.es6", "stray"],
  ["src/x.jsonc", "stray"],
  ["src/x.coffee", "stray"],
  ["src/x.ls", "stray"],
  ["src/x.txt", "stray"],
  ["src/x", "stray"],
  ["src/.ts", "stray"],
  ["src/x.ts.js", "stray"],
  ["src/x/node_modules/p/index.ts", "stray"],
  ["tests/x.ts", "none"],
  ["tests/unit/x.tsx", "none"],
  ["e2e/x.ts", "none"],
  ["plugins/x.ts", "none"],
  ["scripts/x.mjs", "none"],
  ["vite.config.ts", "none"],
  ["vitest.config.ts", "none"],
  ["playwright.config.ts", "none"],
  ["eslint.config.mjs", "none"],
];

describe("a file's layer, by ESLint's own configuration", () => {
  // A dotfile or a dot directory under src/ is linted by the app's layer (ESLint reads dotfiles; the
  // project service may not) and refused by the gate by construction, so that no ignore of dotfiles
  // in the lint's configuration opens a way into a bundle.
  it.each(["src/.x.ts", "src/.d/x.ts", "src/catalogue/.digits.tsx", "src/a/.b/c.ts"])("%s gets the app's layer and is refused by the gate", async (relative) => {
    expect(await layerOf(relative), relative).toBe("app");
    expect(srcKind(relative, ["code", "style"]), relative).toBe(false);
    expect(confinementProblem(WEB, path.join(WEB, relative)), relative).toContain("dot-led segment");
  });

  it.each(PATHS)("%s gets %s", async (relative, expected) => {
    const layer = await layerOf(relative);
    if (expected === "stray") {
      // Linted or not, a file the table refuses never reaches a bundle: the gate refuses it.
      expect(srcKind(relative, ["code", "style"]) && !relative.includes("node_modules"), relative).toBe(false);
      expect(confinementProblem(WEB, path.join(WEB, relative)), relative).not.toBeNull();
    } else {
      expect(layer, relative).toBe(expected);
    }
  });

  it("is the table's: a file the table calls code is in the lint's file set, in every layer it may be", async () => {
    for (const [extension, kind] of SRC_EXTENSIONS) {
      if (kind === "code") {
        expect(SCRIPT_EXTENSIONS, extension).toContain(extension);
        expect(await layerOf(`src/probe${extension}`)).toBe("app");
      } else {
        expect(await layerOf(`src/probe${extension}`), extension).toBe("none");
      }
    }
  });
});

describe("the tooling list", () => {
  it("is tooling for a path in a tooling directory or a root configuration, and for nothing else", () => {
    for (const directory of TOOLING_DIRECTORIES) {
      expect(isTooling(`${directory}/x.ts`)).toBe(true);
      expect(isTooling(`${directory}/a/b/x.ts`)).toBe(true);
      expect(isTooling(`${directory}x/x.ts`)).toBe(false);
      expect(isTooling(`x${directory}/x.ts`)).toBe(false);
      expect(isTooling(directory)).toBe(false);
      expect(isTooling(`src/${directory}/x.ts`)).toBe(false);
      expect(isTooling(`shared/${directory}/x.ts`)).toBe(false);
    }
    for (const file of TOOLING_FILES) {
      expect(isTooling(file)).toBe(true);
      expect(isTooling(`src/${file}`)).toBe(false);
      expect(isTooling(`${file}x`)).toBe(false);
    }
  });

  it("holds nothing under src/", () => {
    expect([...TOOLING_DIRECTORIES, ...TOOLING_FILES].some((name) => name === "src" || name.startsWith("src/"))).toBe(false);
    expect(TOOLING_PATTERNS.some((pattern) => pattern.startsWith("src"))).toBe(false);
    for (const file of ["src/root.ts", "src/api/index.ts", "src/catalogue/main.tsx", "src/tests/x.ts"]) {
      expect(isTooling(file), file).toBe(false);
    }
  });

  it("is the lint's own: the two encodings of the list (the predicate, the patterns) agree with ESLint on every path", async () => {
    for (const [relative, expected] of PATHS) {
      if (expected !== "stray") {
        expect(isTooling(relative), relative).toBe(expected === "none");
      }
    }
    for (const relative of ["tests/gates/scope.test.ts", "e2e/curate.spec.ts", "plugins/gate.ts", "scripts/tooling.mjs", "vite.config.ts"]) {
      expect(isTooling(relative)).toBe(true);
      expect(await layerOf(relative), relative).toBe("none");
    }
  });
});

/** The package's own files that are neither tooling nor under `src/`. */
const ROOT_FILES = new Set([
  ".nvmrc",
  "budget.json",
  "index.html",
  "licenses.json",
  "operator.html",
  "package-lock.json",
  "package.json",
  "tsconfig.app.json",
  "tsconfig.base.json",
  "tsconfig.json",
  "tsconfig.node.json",
]);

/** Every file of the package, relative to its root, but the installed packages and the outputs. */
function filesOfPackage(root: string = WEB): string[] {
  const skipped = new Set(["node_modules", "dist", "dist-e2e", "test-results", "playwright-report"]);
  // Only at the package's root: `src/dist/x` and `src/test-results/x` are files of the package.
  const found: string[] = [];
  const walk = (directory: string): void => {
    for (const entry of readdirSync(path.join(root, directory), { withFileTypes: true })) {
      const relative = directory === "" ? entry.name : `${directory}/${entry.name}`;
      if (entry.isDirectory()) {
        if (directory !== "" || !skipped.has(entry.name)) {
          walk(relative);
        }
      } else {
        found.push(relative);
      }
    }
  };
  walk("");
  return found;
}

describe("the walk of the package's files", () => {
  it("skips the installed packages and the outputs at the package's root alone, and reads every other file", () => {
    const root = mkdtempSync(path.join(os.tmpdir(), "aibi-walk-"));
    try {
      for (const name of ["dist/a.js", "node_modules/p/i.js", "test-results/t", "src/dist/x.JS", "src/test-results/y", "src/node_modules/z.js", "src/.hidden.ts", "e2e/dist/q.ts", ".nvmrc"]) {
        mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
        writeFileSync(path.join(root, name), "");
      }
      expect(filesOfPackage(root).sort()).toEqual([".nvmrc", "e2e/dist/q.ts", "src/.hidden.ts", "src/dist/x.JS", "src/node_modules/z.js", "src/test-results/y"]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});

describe("every file of the package", () => {
  const files = filesOfPackage();

  it("is tooling, under src/ with an extension of the table, or a named root file: no stray", () => {
    const stray = files.filter((name) => {
      if (isTooling(name)) {
        return false;
      }
      if (name.startsWith("src/")) {
        return !srcKind(name, [...new Set(SRC_EXTENSIONS.values())]);
      }
      return !ROOT_FILES.has(name);
    });
    expect(stray).toEqual([]);
    expect(files.length).toBeGreaterThan(60);
    expect(files.filter((name) => name.startsWith("src/")).length).toBeGreaterThan(20);
  });

  it("gets the app's, the client's or no layer, by its place: every non-tooling script file is linted by the policy", async () => {
    const scripts = files.filter((name) => !isTooling(name) && SCRIPT_EXTENSIONS.some((extension) => name.endsWith(extension)));
    expect(scripts.length).toBeGreaterThan(15);
    for (const name of scripts) {
      const expected = name === "src/api/client.ts" ? "client" : name.startsWith("src/api/") ? "api" : "app";
      expect(await layerOf(name), name).toBe(expected);
    }
  });

  it("has no tooling file under src/ and no module of a closure a tooling file", () => {
    expect(files.filter((name) => name.startsWith("src/") && isTooling(name))).toEqual([]);
  });

  it("holds every file under src/, whatever its case, to the table", () => {
    const under = files.filter((name) => name.startsWith("src/"));
    for (const name of under) {
      const extension = path.posix.extname(name);
      expect(SRC_EXTENSIONS.has(extension), name).toBe(true);
    }
  });
});
