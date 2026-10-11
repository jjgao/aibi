/**
 * Synthetic two-entry projects for the gate's tests (`gate.test.ts`, `confinement.test.ts`):
 * files written into a temporary directory with stub packages under their own `node_modules/`,
 * built by Vite with the gate, whose refusal or whose chunks come back.
 */
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { build, type Plugin, type Rolldown } from "vite";
import { expect } from "vitest";

import { type GateChunk, operatorGate } from "../../plugins/gate";
import { sanitizeFileName } from "../../plugins/names";

export const WEB = path.join(import.meta.dirname, "../..");
const scratch = realpathSync(mkdtempSync(path.join(os.tmpdir(), "aibi-gate-")));

/** Remove the scratch directory (each test file calls it once, after all). */
export function cleanup(): void {
  rmSync(scratch, { recursive: true, force: true });
}

export type Files = Record<string, string>;

export const STUBS: Files = {
  "node_modules/react/package.json": '{"name":"react","version":"0.0.0","main":"index.js"}',
  "node_modules/react/index.js": "export const react = 'react';",
  "node_modules/scheduler/package.json": '{"name":"scheduler","version":"0.0.0","main":"index.js"}',
  "node_modules/scheduler/index.js": "export const scheduler = 'scheduler';",
  "node_modules/vega-util/package.json": '{"name":"vega-util","version":"0.0.0","main":"index.js"}',
  "node_modules/vega-util/index.js": "export const vega = 'vega-util ' + Math.random();",
  "node_modules/react-dom/package.json": '{"name":"react-dom","version":"0.0.0","main":"index.js"}',
  "node_modules/react-dom/index.js": "import { vega } from 'vega-util'; export const dom = 'react-dom ' + vega;",
  "node_modules/react-dom/node_modules/vega-util/package.json":
    '{"name":"vega-util","version":"0.0.1","main":"index.js"}',
  "node_modules/react-dom/node_modules/vega-util/index.js": "export const vega = 'nested ' + Math.random();",
  "node_modules/@charts/core/package.json": '{"name":"@charts/core","version":"0.0.0","main":"index.js"}',
  "node_modules/@charts/core/index.js": "export const charts = 'charts ' + Math.random();",
  "node_modules/vega-lite/package.json": '{"name":"vega-lite","version":"0.0.0","main":"node_modules/react/chart.js"}',
  "node_modules/vega-lite/node_modules/react/chart.js": "export const lite = 'vega-lite ' + Math.random();",
  "node_modules/react-router/package.json": '{"name":"react-router","version":"0.0.0","main":"index.js"}',
  "node_modules/react-router/index.js": "export const router = 'router ' + Math.random();",
};

let projects = 0;

/** A two-entry project: `index.html` loads `src/index.ts`, `operator.html` `src/operator.ts`. */
export function project(files: Files): string {
  projects += 1;
  const root = path.join(scratch, `p${String(projects)}`);
  const all: Files = {
    "index.html": '<!doctype html><div id="root"></div><script type="module" src="/src/index.ts"></script>',
    "operator.html": '<!doctype html><div id="root"></div><script type="module" src="/src/operator.ts"></script>',
    "src/index.ts": "document.title = 'index';",
    "src/operator.ts": "document.title = 'operator';",
    ...STUBS,
    ...files,
  };
  for (const [name, text] of Object.entries(all)) {
    mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    writeFileSync(path.join(root, name), text);
  }
  return root;
}

/** A virtual module `\0aibi-virtual` that `import "virtual:aibi"` resolves to. */
export const virtual: Plugin = {
  name: "virtual",
  resolveId(id) {
    return id === "virtual:aibi" ? "\0aibi-virtual" : null;
  },
  load(id) {
    return id === "\0aibi-virtual" ? "export const v = 'virtual ' + Math.random();" : null;
  },
};

/** Build a synthetic project with the gate; the output's chunks, or the build's error. */
export async function gated(
  root: string,
  harness = false,
  plugins: Plugin[] = [],
  /** Documents the build starts from beyond the two (project-relative names). */
  more: readonly string[] = [],
): Promise<{ chunks: GateChunk[] } | { error: string }> {
  try {
    const output = await build({
      root,
      configFile: false,
      logLevel: "silent",
      publicDir: false,
      plugins: [...plugins, operatorGate({ harness })],
      build: {
        write: false,
        assetsInlineLimit: 0,
        modulePreload: { polyfill: false },
        rolldownOptions: {
          input: {
            index: path.join(root, "index.html"),
            operator: path.join(root, "operator.html"),
            ...Object.fromEntries(more.map((name) => [path.basename(name, path.extname(name)), path.join(root, name)])),
          },
          output: { sanitizeFileName },
        },
      },
    });
    const outputs = (Array.isArray(output) ? output : [output]) as Rolldown.RolldownOutput[];
    return { chunks: outputs.flatMap((found) => found.output.filter((item) => item.type === "chunk")) };
  } catch (error) {
    return { error: error instanceof Error ? error.message : "a build failed without an Error" };
  }
}

export async function refused(root: string, harness = false, plugins: Plugin[] = [], more: readonly string[] = []): Promise<string> {
  const found = await gated(root, harness, plugins, more);
  if (!("error" in found)) {
    throw new Error("the build passed the gate");
  }
  expect(found.error).toContain("The build's gate refuses it");
  return found.error;
}

