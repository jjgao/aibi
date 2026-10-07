/**
 * The number and I/O names of the lint (D419), each in each position, as the sink names are
 * tested (`lint.test.ts`): outside `src/api/` every number name and every I/O name is refused as a
 * call, a value, a member (`x.N`), a computed member (`x['N']`), a destructured key (`{ N }`,
 * `{ 'N': a }`) and an alias; in `src/api/` but `client.ts` the I/O names are, and the number
 * names are not; in `client.ts` neither is; in the tooling (`scripts/tooling.mjs`) nothing is, and
 * in any other directory (`shared/`) everything is, as in `src/`. The names are written out
 * here, not read from the configuration, so that a name dropped from it fails a cell.
 */
import path from "node:path";

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

import { WEB } from "./program";

const eslint = new ESLint({
  cwd: WEB,
  overrideConfig: {
    languageOptions: {
      parserOptions: {
        projectService: { allowDefaultProject: ["src/*.tsx", "src/api/probe.ts", "scripts/probe.ts", "tests/probe.ts", "e2e/probe.ts", "plugins/probe.ts", "shared/probe.ts"], defaultProject: "tsconfig.app.json" },
      },
    },
  },
});

const RULES = new Set(["no-restricted-syntax", "no-restricted-globals", "no-restricted-properties"]);

/** Whether the policy's rules flag `code` as `file`. */
async function flagged(code: string, file: string): Promise<boolean> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.some((message) => message.ruleId !== null && RULES.has(message.ruleId));
}

const NUMBER_NAMES = ["BigInt", "Number", "String", "parseInt", "parseFloat", "isNaN", "isFinite", "Math", "JSON", "valueAsNumber"];
const IO_NAMES = ["fetch", "XMLHttpRequest", "Response", "Request", "EventSource", "WebSocket", "caches", "CacheStorage", "serviceWorker", "sendBeacon"];

/** Each position a name can be written in, as code over `@N@`. */
const POSITIONS: readonly [string, string][] = [
  ["a reference", "export const f = (): unknown => @N@;"],
  ["a member", "export const f = (x: Record<string, unknown>): unknown => x.@N@;"],
  ["a member of globalThis", "export const f = (): unknown => (globalThis as Record<string, unknown>).@N@;"],
  ["an optional member", "export const f = (x?: Record<string, unknown>): unknown => x?.@N@;"],
  ["a computed member", "export const f = (x: Record<string, unknown>): unknown => x['@N@'];"],
  ["a destructured key", "export const f = (x: Record<string, unknown>): unknown => { const { @N@: a } = x; return a; };"],
  ["a destructured string key", "export const f = (x: Record<string, unknown>): unknown => { const { '@N@': a } = x; return a; };"],
  ["an object key", "export const f = (a: unknown) => ({ @N@: a });"],
  ["a binding", "export const f = (@N@: unknown): unknown => @N@;"],
];

const cells = (names: readonly string[]) =>
  names.flatMap((name) => POSITIONS.map(([position, code]) => [name, position, code.replaceAll("@N@", name)] as const));

describe("outside src/api/, every number and I/O name in every position", () => {
  it.each(cells([...NUMBER_NAMES, ...IO_NAMES]))("%s as %s", async (_name, _position, code) => {
    expect(await flagged(code, "src/probe.tsx")).toBe(true);
  });
});

describe("in src/api/ but client.ts, the I/O names and not the number names", () => {
  it.each(cells(IO_NAMES))("%s as %s is refused", async (_name, _position, code) => {
    expect(await flagged(code, "src/api/probe.ts")).toBe(true);
  });

  it.each(NUMBER_NAMES)("%s is the client's own", async (name) => {
    expect(await flagged(`export const f = (): unknown => ${name};\n`, "src/api/probe.ts")).toBe(false);
  });
});

describe("client.ts names I/O, and no tooling is refused, but every other file is", () => {
  it.each(IO_NAMES)("%s in client.ts", async (name) => {
    expect(await flagged(`export const f = (): unknown => ${name};\n`, "src/api/client.ts")).toBe(false);
  });

  it.each(["scripts/probe.ts", "tests/probe.ts", "e2e/probe.ts", "plugins/probe.ts"])("no name in the tooling, %s", async (file) => {
    for (const name of [...NUMBER_NAMES, ...IO_NAMES]) {
      expect(await flagged(`export const f = (): unknown => ${name};\n`, file)).toBe(false);
    }
  });

  it.each([...NUMBER_NAMES, ...IO_NAMES])("%s in a directory of its own, shared/ (the app's by default)", async (name) => {
    expect(await flagged(`export const f = (): unknown => ${name};\n`, "shared/probe.ts")).toBe(true);
  });

  it.each([
    ["box from box.ts", "import { box } from './box';\nexport const f = () => box('1');\n", "src/api/probe.ts", true],
    ["textOf from box.ts", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/probe.ts", true],
    ["the box as a namespace", "import * as B from './box';\nexport const f = B;\n", "src/api/probe.ts", true],
    ["decode from decode.ts", "import { decode } from './decode';\nexport const f = decode;\n", "src/api/probe.ts", true],
    ["revive from decode.ts", "import { revive } from './decode';\nexport const f = revive;\n", "src/api/probe.ts", true],
    ["box in decode.ts", "import { box } from './box';\nexport const f = box;\n", "src/api/decode.ts", false],
    ["decode in client.ts", "import { decode } from './decode';\nexport const f = decode;\n", "src/api/client.ts", false],
    ["textOf in client.ts", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/client.ts", true],
    ["the box's type anywhere in src/api/", "import type { ServerNumber } from './box';\nexport const f = (n: ServerNumber) => n;\n", "src/api/probe.ts", false],
  ])("inside src/api/, %s", async (_case, code, file, refused) => {
    const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
    const ids = (result?.messages ?? []).map((message) => message.ruleId);
    expect(ids.includes("@typescript-eslint/no-restricted-imports")).toBe(refused);
  });

  it.each(["src/probe.tsx", "src/api/probe.ts", "src/api/client.ts", "src/api/decode.ts", "shared/probe.ts"])(
    "refuses import.meta.glob in %s, whichever layer it is",
    async (file) => {
      expect(await flagged("export const f = () => import.meta.glob('./box.ts', { eager: true });\n", file)).toBe(true);
      expect(await flagged("export const f = () => import.meta.glob('./box.ts');\n", file)).toBe(true);
    },
  );

  it.each(["src/probe.tsx", "src/api/probe.ts", "src/api/client.ts", "src/api/decode.ts", "shared/probe.ts"])(
    "refuses an ambient module in %s, whichever layer it is",
    async (file) => {
      expect(await flagged("declare module '*.JS' { export const x: string; }\n", file)).toBe(true);
      expect(await flagged("declare module './x.es6' { export const x: string; }\n", file)).toBe(true);
    },
  );

  it.each(["src/probe.tsx", "src/api/probe.ts", "src/api/client.ts", "src/api/decode.ts", "shared/probe.ts"])(
    "refuses require in %s, whichever layer it is",
    async (file) => {
      expect(await flagged("declare const require: (p: string) => unknown;\nexport const f = () => require('./box.ts');\n", file)).toBe(true);
      expect(await flagged("export const f = () => require('./box.ts');\n", file)).toBe(true);
    },
  );

  it("refuses .json even in client.ts", async () => {
    expect(await flagged("export const f = (r: { json(): unknown }) => r.json();\n", "src/api/client.ts")).toBe(true);
  });
});
