/**
 * The operator client's gates (D423), over the lint and the type checker:
 *
 * - **Storage (m15).** `localStorage`, `sessionStorage`, `indexedDB`, `IDBFactory`, `cookieStore`,
 *   `BroadcastChannel`, `postMessage`, `SharedWorker` and `MessageChannel` are refused in every
 *   position in every layer of `src/` (the client's and `client.ts` included), as are `cookie` as
 *   a member or a destructured key, `history.pushState` in any form, `history.replaceState` with a
 *   state that is not `null` written out, and the global object's `name`: a secret, or an encoded
 *   copy of one (`btoa(token)`), has nowhere to go but the client's closure. The generated types'
 *   `cookie` parameters (a type's key) are not refused, and neither is `forgetAddress`'s
 *   `replaceState(null, "", path)`. The names are written out here, not read from the
 *   configuration, so that a name dropped from it fails a cell.
 * - **Imports.** The page's operator client (`src/api/operator.ts`) is imported by the operator
 *   entry and the harness alone; inside `src/api/`, `send` by `curator.ts` alone and
 *   `createCurator` by `operator.ts` alone.
 * - **The edits of a change carry no number, at compile time.** `set` and `put` (a JSON value)
 *   and `accept` (a proposal's id) are not edits the client's `change` takes: each is a type
 *   error, and the three it takes compile.
 */
import path from "node:path";

import { ESLint } from "eslint";
import { beforeAll, describe, expect, it } from "vitest";

import { type Checked, check, WEB } from "./program";

const eslint = new ESLint({
  cwd: WEB,
  overrideConfig: {
    languageOptions: {
      parserOptions: {
        projectService: {
          allowDefaultProject: ["src/*.tsx", "src/api/probe.ts", "src/operator/probe.tsx", "src/harness/probe.tsx", "src/catalogue/probe.tsx", "scripts/probe.ts", "tests/probe.ts", "e2e/probe.ts", "shared/probe.ts"],
          defaultProject: "tsconfig.app.json",
          maximumDefaultProjectFileMatchCount_THIS_WILL_SLOW_DOWN_LINTING: 16,
        },
      },
    },
  },
});

const POLICY = new Set(["no-restricted-syntax", "no-restricted-globals", "no-restricted-properties", "@typescript-eslint/no-restricted-imports"]);

async function ids(code: string, file: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.flatMap((message) => (message.ruleId !== null && POLICY.has(message.ruleId) ? [message.ruleId] : []));
}

const flagged = async (code: string, file: string): Promise<boolean> => (await ids(code, file)).length > 0;

const STORAGE_NAMES = ["localStorage", "sessionStorage", "indexedDB", "IDBFactory", "cookieStore", "BroadcastChannel", "postMessage", "SharedWorker", "MessageChannel"];

const POSITIONS: readonly [string, string][] = [
  ["a reference", "export const f = (): unknown => @N@;"],
  ["a member", "export const f = (x: Record<string, unknown>): unknown => x.@N@;"],
  ["a member of window", "export const f = (): unknown => window.@N@;"],
  ["an optional member", "export const f = (x?: Record<string, unknown>): unknown => x?.@N@;"],
  ["a computed member", "export const f = (x: Record<string, unknown>): unknown => x['@N@'];"],
  ["a destructured key", "export const f = (x: Record<string, unknown>): unknown => { const { @N@: a } = x; return a; };"],
  ["a destructured string key", "export const f = (x: Record<string, unknown>): unknown => { const { '@N@': a } = x; return a; };"],
  ["an object key", "export const f = (a: unknown) => ({ @N@: a });"],
  ["a binding", "export const f = (@N@: unknown): unknown => @N@;"],
];

const LAYERS = ["src/probe.tsx", "src/api/probe.ts", "src/api/client.ts", "src/operator/probe.tsx", "shared/probe.ts"];

describe("storage, cookies and messages to another context, refused in every layer of src/ (m15)", () => {
  it.each(STORAGE_NAMES.flatMap((name) => POSITIONS.map(([position, code]) => [name, position, code.replaceAll("@N@", name)] as const)))(
    "%s as %s",
    async (_name, _position, code) => {
      for (const file of LAYERS) {
        expect(await flagged(code, file), file).toBe(true);
      }
    },
  );

  it.each([
    ["localStorage.setItem", "export const f = (t: string) => { localStorage.setItem('t', btoa(t)); };"],
    ["window.sessionStorage", "export const f = (t: string) => { window.sessionStorage.setItem('t', t); };"],
    ["indexedDB.open", "export const f = () => indexedDB.open('x');"],
    ["document.cookie written", "export const f = (t: string) => { document.cookie = `t=${t}`; };"],
    ["document.cookie read", "export const f = () => document.cookie;"],
    ["a cookie by a computed name", "export const f = () => document['cookie'];"],
    ["a cookie destructured", "export const f = () => { const { cookie } = document; return cookie; };"],
    ["cookieStore.set", "export const f = (t: string) => cookieStore.set('t', t);"],
    ["a BroadcastChannel", "export const f = (t: string) => { new BroadcastChannel('x').postMessage(t); };"],
    ["postMessage to the opener", "export const f = (t: string) => { window.opener?.postMessage(t, '*'); };"],
    ["postMessage to a parent", "export const f = (t: string) => { parent.postMessage(t, '*'); };"],
    ["a MessageChannel", "export const f = () => new MessageChannel();"],
    ["pushState with no state", "export const f = () => { history.pushState(null, '', '/curate'); };"],
    ["pushState through window", "export const f = (t: string) => { window.history.pushState({ t }, '', '/curate'); };"],
    ["pushState by a computed name", "export const f = () => { history['pushState'](null, '', '/'); };"],
    ["replaceState with a state", "export const f = (t: string) => { history.replaceState({ t }, '', '/curate'); };"],
    ["replaceState with a variable", "export const f = (t: unknown) => { window.history.replaceState(t, '', '/curate'); };"],
    ["replaceState with undefined", "export const f = () => { history.replaceState(undefined, '', '/curate'); };"],
    ["replaceState with no argument", "export const f = (h: History) => { (h.replaceState as (...a: unknown[]) => void)(); };"],
    ["replaceState held", "export const f = () => { const r = history.replaceState; return r; };"],
    ["replaceState by a computed name", "export const f = () => { history['replaceState'](null, '', '/'); };"],
    ["window.name written", "export const f = (t: string) => { window.name = t; };"],
    ["self.name read", "export const f = () => self.name;"],
    ["globalThis.name by a computed name", "export const f = () => globalThis['name'];"],
    ["top.name", "export const f = () => top?.name;"],
  ])("refuses %s, in every layer", async (_case, code) => {
    for (const file of LAYERS) {
      expect(await flagged(code, file), file).toBe(true);
    }
  });

  it.each([
    ["forgetAddress's replaceState", "export const f = (h: Pick<History, 'replaceState'>) => { h.replaceState(null, '', '/curate'); };"],
    ["a type's cookie key, as the generated types write it", "export interface P { readonly cookie?: never; readonly query?: never }\nexport const f = (p: P) => p.query;"],
    ["a name of an ordinary object", "export const f = (x: { name: string }) => x.name;"],
    ["a word that only contains a name", "export const f = (x: { localStorageKey: string; postMessages: number }) => [x.localStorageKey, x.postMessages];"],
  ])("lets %s pass, so that a flag is the rule's", async (_case, code) => {
    for (const file of LAYERS) {
      expect(await ids(code, file), file).toEqual([]);
    }
  });

  it("refuses none of them in the tooling", async () => {
    for (const file of ["scripts/probe.ts", "tests/probe.ts", "e2e/probe.ts"]) {
      expect(await flagged("export const f = () => [localStorage, document.cookie, window.name];\n", file), file).toBe(false);
    }
  });
});

describe("who imports the operator client and its internals", () => {
  it.each([
    ["the operator entry, the page's client", "import { operator } from '../api/operator';\nexport const f = () => operator.view();\n", "src/operator/probe.tsx", false],
    ["the harness, the page's client", "import { operator } from '../api/operator';\nexport const f = () => operator.view();\n", "src/harness/probe.tsx", false],
    ["the catalogue, the page's client", "import { operator } from '../api/operator';\nexport const f = () => operator.view();\n", "src/catalogue/probe.tsx", true],
    ["any other module, the page's client", "import { operator } from './api/operator';\nexport const f = () => operator.view();\n", "src/probe.tsx", true],
    ["the operator entry, the factory", "import { createCurator } from '../api/curator';\nexport const f = createCurator;\n", "src/operator/probe.tsx", true],
    ["the operator entry, the client module", "import { exchange } from '../api/client';\nexport const f = exchange;\n", "src/operator/probe.tsx", true],
    ["the harness, the oracle", "import { decodedCount } from '../api/oracle';\nexport const f = decodedCount;\n", "src/harness/probe.tsx", false],
    ["the operator entry, the oracle", "import { decodedCount } from '../api/oracle';\nexport const f = decodedCount;\n", "src/operator/probe.tsx", true],
    ["a module of src/api/, send", "import { send } from './client';\nexport const f = send;\n", "src/api/probe.ts", true],
    ["curator.ts, send", "import { send } from './client';\nexport const f = send;\n", "src/api/curator.ts", false],
    ["operator.ts, send", "import { send } from './client';\nexport const f = send;\n", "src/api/operator.ts", true],
    ["a module of src/api/, createCurator", "import { createCurator } from './curator';\nexport const f = createCurator;\n", "src/api/probe.ts", true],
    ["operator.ts, createCurator", "import { createCurator } from './curator';\nexport const f = createCurator;\n", "src/api/operator.ts", false],
    ["curator.ts, the box's internals", "import { textOf } from './box';\nexport const f = textOf;\n", "src/api/curator.ts", true],
    ["client.ts, decode", "import { decode } from './decode';\nexport const f = decode;\n", "src/api/client.ts", false],
    ["client.ts, the box's internals", "import { box } from './box';\nexport const f = box;\n", "src/api/client.ts", true],
    ["decode.ts, box", "import { box } from './box';\nexport const f = box;\n", "src/api/decode.ts", false],
    ["decode.ts, send", "import { send } from './client';\nexport const f = send;\n", "src/api/decode.ts", true],
  ])("%s: refused %s", async (_case, code, file, refused) => {
    const found = await ids(code, file);
    expect(found.includes("@typescript-eslint/no-restricted-imports")).toBe(refused);
  });
});

describe("a change's edits carry no number, at compile time", () => {
  const HEAD = "import { operator } from '../api/operator';\n";
  const CASES: readonly (readonly [string, string, boolean])[] = [
    ["remove", "{ op: 'remove', descriptor: 't', pointer: '/label' }", true],
    ["confirm", "{ op: 'confirm', descriptor: 't', evidence: 'e', pointers: ['/a'] }", true],
    ["remove_descriptor", "{ op: 'remove_descriptor', descriptor: 't' }", true],
    ["set, a JSON value", "{ op: 'set', descriptor: 't', pointer: '/label', value: 1 }", false],
    ["set, a string value", "{ op: 'set', descriptor: 't', pointer: '/label', value: 'x' }", false],
    ["put, a whole descriptor", "{ op: 'put', descriptor: { id: 't' } }", false],
    ["accept, a proposal's id", "{ op: 'accept', proposal: 1 }", false],
    ["remove with a number for its pointer", "{ op: 'remove', descriptor: 't', pointer: 1 }", false],
    ["confirm with a member no kind has", "{ op: 'confirm', descriptor: 't', proposal: 1 }", false],
  ];
  let checked: Checked;
  const fileOf = (index: number) => `src/operator/edit-probe-${String(index)}.tsx`;

  beforeAll(() => {
    checked = check(Object.fromEntries(CASES.map(([, edit], index) => [fileOf(index), `${HEAD}export const f = () => operator.change('d', [${edit}]);\n`])));
  }, 120_000);

  it.each(CASES.map(([name, , compiles], index) => [name, compiles, index] as const))("%s compiles: %s", (_name, compiles, index) => {
    const codes = checked.codes(fileOf(index));
    if (compiles) {
      expect(codes).toEqual([]);
    } else {
      expect(codes.length).toBeGreaterThan(0);
      expect(codes.every((code) => [2322, 2353, 2820].includes(code))).toBe(true);
    }
  });
});
