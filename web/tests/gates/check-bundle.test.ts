/**
 * `scripts/check-bundle.mjs` on synthetic bundles written into a temporary directory, the real
 * loader included (`uv run aibi-server check`, which needs `server/`'s environment).
 */
import { chmodSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, describe, expect, it } from "vitest";

import {
  bundleProblems,
  HARNESS_MARKER,
  hashed,
  htmlProblems,
  loaderProblems,
  main,
} from "../../scripts/check-bundle.mjs";
import { checkBudget, readPolicy } from "../../scripts/policy-files.mjs";

const WEB = path.join(import.meta.dirname, "../..");
const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-check-bundle-test-"));
const BUDGET = { prod: { bytes: 10_000, files: 10 }, e2e: { bytes: 10_000, files: 10 } };

afterAll(() => {
  rmSync(scratch, { recursive: true, force: true });
});

type Manifest = Record<string, Record<string, unknown>>;

const MANIFEST: Manifest = {
  "index.html": {
    file: "assets/index-AbCd_-12.js",
    name: "index",
    src: "index.html",
    isEntry: true,
    imports: ["_shared-ZZZZZZZZ.js"],
    dynamicImports: ["src/Lazy.tsx"],
  },
  "operator.html": {
    file: "assets/operator-aaaaaaaa.js",
    name: "operator",
    src: "operator.html",
    isEntry: true,
    imports: ["_shared-ZZZZZZZZ.js"],
  },
  "_shared-ZZZZZZZZ.js": { file: "assets/shared-ZZZZZZZZ.js", name: "shared" },
  "src/Lazy.tsx": {
    file: "assets/Lazy-12345678.js",
    name: "Lazy",
    src: "src/Lazy.tsx",
    isDynamicEntry: true,
    css: ["assets/Lazy-abcdefgh.css"],
  },
  "src/icon.svg": { file: "assets/icon-PHoxJkZi.svg", src: "src/icon.svg" },
};

const FILES: Record<string, string> = {
  "index-AbCd_-12.js": "import './shared-ZZZZZZZZ.js';",
  "operator-aaaaaaaa.js": "import './shared-ZZZZZZZZ.js';",
  "shared-ZZZZZZZZ.js": "export const shared = 1;",
  "Lazy-12345678.js": "export const lazy = 1;",
  "Lazy-abcdefgh.css": "p{color:red}",
  "icon-PHoxJkZi.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>",
};

const HTML = '<!doctype html><title>x</title><script type="module" crossorigin src="/assets/index-AbCd_-12.js"></script><div id="root"></div>';

let made = 0;

/** A bundle directory, as a build under `umask 022` writes it, with changes. */
function bundle(change: {
  manifest?: Manifest;
  files?: Record<string, string | null>;
  root?: Record<string, string>;
} = {}): string {
  made += 1;
  const dir = path.join(scratch, `b${String(made)}`);
  mkdirSync(path.join(dir, ".vite"), { recursive: true });
  mkdirSync(path.join(dir, "assets"));
  writeFileSync(path.join(dir, ".vite", "manifest.json"), JSON.stringify(change.manifest ?? MANIFEST));
  for (const [name, text] of Object.entries({ ...FILES, ...change.files })) {
    if (text !== null) {
      writeFileSync(path.join(dir, "assets", name), text);
    }
  }
  for (const [name, text] of Object.entries({ "index.html": HTML, "operator.html": HTML, ...change.root })) {
    writeFileSync(path.join(dir, name), text);
  }
  for (const directory of [dir, path.join(dir, ".vite"), path.join(dir, "assets")]) {
    chmodSync(directory, 0o755);
    for (const name of readdirSync(directory, { withFileTypes: true })) {
      if (name.isFile()) {
        chmodSync(path.join(directory, name.name), 0o644);
      }
    }
  }
  return dir;
}

function problems(dir: string, mode: "prod" | "e2e" = "prod", budget = BUDGET): string[] {
  return bundleProblems(dir, { mode, budget });
}

const total = Object.values(FILES).reduce((sum, text) => sum + Buffer.byteLength(text), 0);

describe("check-bundle", () => {
  it("passes a bundle that keeps every rule", () => {
    expect(problems(bundle())).toEqual([]);
  });

  it("knows the harness's marker as the harness states it", () => {
    const source = readFileSync(path.join(WEB, "src/harness/Harness.tsx"), "utf8");
    expect(source).toContain(`export const HARNESS_MARKER = "${HARNESS_MARKER}";`);
  });

  it("refuses the harness's marker in a production build, and its absence in an end-to-end one", () => {
    const marked = bundle({ files: { "Lazy-12345678.js": `export const h = "${HARNESS_MARKER}";` } });
    expect(problems(marked, "prod")).toEqual(["assets/: a script holds the harness, which a production build must not"]);
    expect(problems(marked, "e2e")).toEqual([]);
    expect(problems(bundle(), "e2e")).toEqual(["assets/: no script holds the harness, which the end-to-end build must"]);
    const styled = bundle({ files: { "Lazy-abcdefgh.css": `/* ${HARNESS_MARKER} */` } });
    expect(problems(styled, "e2e")).toHaveLength(1);
  });

  it("refuses a name the build did not hash", () => {
    const manifest = structuredClone(MANIFEST);
    manifest["src/Lazy.tsx"] = { ...manifest["src/Lazy.tsx"], file: "assets/Lazy.js" };
    const found = problems(bundle({ manifest, files: { "Lazy-12345678.js": null, "Lazy.js": "x" } }));
    expect(found).toEqual(["assets/Lazy.js: not <stem>-<hash> for the stem the manifest gives"]);
  });

  it("refuses a hash of another length, or under another stem", () => {
    for (const name of ["Lazy-1234567.js", "Lazy-123456789.js", "Other-12345678.js", "Lazy-1234567!.js"]) {
      const manifest = structuredClone(MANIFEST);
      manifest["src/Lazy.tsx"] = { ...manifest["src/Lazy.tsx"], file: `assets/${name}` };
      const found = problems(bundle({ manifest, files: { "Lazy-12345678.js": null, [name]: "x" } }));
      expect(found.some((problem) => problem.includes("not <stem>-<hash>")), name).toBe(true);
    }
  });

  it("refuses a file the manifest does not name, and a name it gives that the build lacks", () => {
    expect(problems(bundle({ files: { "stray-12345678.js": "x" } }))).toEqual(["assets/stray-12345678.js: not named by the manifest"]);
    expect(problems(bundle({ files: { "shared-ZZZZZZZZ.js": null } }))).toEqual([
      ".vite/manifest.json: names assets/shared-ZZZZZZZZ.js, which the build lacks",
    ]);
  });

  it("refuses names outside the loader's grammar and types it does not serve", () => {
    const manifest = structuredClone(MANIFEST);
    manifest["src/a.ts"] = { file: "assets/-a-12345678.js", name: "-a" };
    manifest["src/b.ts"] = { file: "assets/b-12345678.js.map", name: "b" };
    manifest["src/c.ts"] = { file: "assets/c d-12345678.js", name: "c d" };
    const found = problems(bundle({ manifest, files: { "-a-12345678.js": "x", "b-12345678.js.map": "x", "c d-12345678.js": "x" } }));
    expect(found).toEqual([
      'assets/"-a-12345678.js": not a name the loader admits',
      "assets/b-12345678.js.map: not of a type the loader serves",
      "assets/b-12345678.js.map: not <stem>-<hash> for the stem the manifest gives",
      'assets/"c d-12345678.js": not a name the loader admits',
    ]);
  });

  it("refuses anything else at the root, and a manifest without both entries", () => {
    expect(problems(bundle({ root: { "gate.json": "{}" } }))).toEqual(["gate.json: not a file of a bundle's root"]);
    const manifest = structuredClone(MANIFEST);
    delete manifest["operator.html"];
    expect(problems(bundle({ manifest, files: { "operator-aaaaaaaa.js": null } }))).toEqual([
      ".vite/manifest.json: operator.html is not an entry",
    ]);
  });

  it("refuses inline code in the build's own HTML", () => {
    expect(htmlProblems("index.html", HTML)).toEqual([]);
    expect(htmlProblems("i", "<script>alert(1)</script>")).toEqual(["i: an inline script"]);
    expect(htmlProblems("i", '<script type="module">import "/x.js"</script>')).toEqual(["i: an inline script"]);
    expect(htmlProblems("i", "<style>p{}</style>")).toEqual(["i: an inline stylesheet"]);
    expect(htmlProblems("i", '<p style="color:red">')).toEqual(["i: a style attribute"]);
    expect(htmlProblems("i", '<img src="/a.png" onerror="x()">')).toEqual(["i: an event handler attribute"]);
    expect(htmlProblems("i", '<a href="javascript:x()">')).toEqual(["i: a javascript: URL"]);
    expect(problems(bundle({ root: { "operator.html": "<script>1</script>" } }))).toEqual(["operator.html: an inline script"]);
  });

  it("holds the bundle to its budget: a budget below the build fails", () => {
    const dir = bundle();
    const exact = { prod: { bytes: total, files: 6 }, e2e: { bytes: total, files: 6 } };
    expect(problems(dir, "prod", exact)).toEqual([]);
    expect(problems(dir, "prod", { ...exact, prod: { bytes: total - 1, files: 6 } })).toEqual([
      `assets/: ${String(total)} bytes, over the budget of ${String(total - 1)} (budget.json)`,
    ]);
    expect(problems(dir, "prod", { ...exact, prod: { bytes: total, files: 5 } })).toEqual([
      "assets/: 6 files, over the budget of 5 (budget.json)",
    ]);
    expect(problems(dir, "e2e", { ...exact, prod: { bytes: 1, files: 1 } }).filter((p) => p.includes("budget"))).toEqual([]);
  });

  it("reads the checked-in budget as a budget (gate.test.ts holds it to the build)", () => {
    const budget = readPolicy(path.join(WEB, "budget.json"), checkBudget);
    expect(Object.keys(budget).sort()).toEqual(["e2e", "prod"]);
  });

  it("refuses an entry of assets/ that is not a regular file: a symlink, a directory", () => {
    const linked = bundle();
    symlinkSync("shared-ZZZZZZZZ.js", path.join(linked, "assets", "link-12345678.js"));
    expect(problems(linked)).toEqual(["assets/link-12345678.js: not a regular file"]);
    const directory = bundle();
    mkdirSync(path.join(directory, "assets", "dir-12345678.js"));
    expect(problems(directory)).toEqual(["assets/dir-12345678.js: not a regular file"]);
  });

  it("agrees with the loader on what is hashed", () => {
    expect(hashed("index-AbCd_-12.js", ["index"])).toBe(true);
    expect(hashed("index-AbCd_-12.js", ["inde"])).toBe(false);
    expect(hashed("survival-km-chart.js", ["survival-km-chart", "survival-km"])).toBe(false);
    expect(hashed("survival-km-chart.js", ["survival"])).toBe(true);
    expect(hashed("vendor-ReactDOM.js", ["vendor"])).toBe(true);
    expect(hashed("x-12345678.css", [])).toBe(false);
  });

  it("runs the real loader, which accepts a good bundle and refuses a bad one", () => {
    expect(loaderProblems(bundle())).toEqual([]);
    const shared = bundle();
    chmodSync(path.join(shared, "assets", "Lazy-12345678.js"), 0o666);
    const refused = loaderProblems(shared);
    expect(refused[0]).toBe("the loader refuses the bundle (exit 2):");
    expect(refused.join("\n")).toContain("writable by group or others");
    const manifest = structuredClone(MANIFEST);
    delete manifest["operator.html"];
    expect(loaderProblems(bundle({ manifest, files: { "operator-aaaaaaaa.js": null } }))).toEqual([
      "the loader accepts the bundle without its operator entry",
    ]);
  });

  it("writes nothing into the bundle, and exits 1 on a problem, 2 on a usage error", () => {
    const dir = bundle();
    const listing = (where: string): string[] =>
      readdirSync(where, { recursive: true, withFileTypes: true }).map((entry) => path.join(entry.parentPath, entry.name)).sort();
    const before = listing(dir);
    const budgetFile = path.join(scratch, "budget.json");
    writeFileSync(budgetFile, JSON.stringify(BUDGET));
    const said: string[] = [];
    expect(main(["--mode", "prod", dir, "--budget", budgetFile], (line) => said.push(line))).toBe(0);
    expect(said).toEqual([`check-bundle: ${dir} passes (prod)`]);
    expect(listing(dir)).toEqual(before);
    expect(main(["--mode", "e2e", dir, "--budget", budgetFile, "--no-loader"], () => undefined)).toBe(1);
    const shared = bundle();
    chmodSync(path.join(shared, "assets", "shared-ZZZZZZZZ.js"), 0o664);
    const refused: string[] = [];
    expect(main(["--mode", "prod", shared, "--budget", budgetFile], (line) => refused.push(line))).toBe(1);
    expect(refused[0]).toBe("check-bundle: the loader refuses the bundle (exit 2):");
    expect(main(["--mode", "prod", shared, "--budget", budgetFile, "--no-loader"], () => undefined)).toBe(0);
    expect(main(["--mode", "development", dir], () => undefined)).toBe(2);
    expect(main([dir], () => undefined)).toBe(2);
    expect(main(["--mode", "prod", dir, "--bogus"], () => undefined)).toBe(2);
  });
});
