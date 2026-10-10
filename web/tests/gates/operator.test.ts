/**
 * The operator client's gates (D423), over the lint and the type checker:
 *
 * - **Storage (m15).** `localStorage`, `sessionStorage`, `indexedDB`, `IDBFactory`, `cookieStore`,
 *   `BroadcastChannel`, `postMessage`, `SharedWorker`, `MessageChannel`, `caches` and
 *   `CacheStorage` are refused in every position in every layer of `src/` (the client's and
 *   `client.ts` included), as are `cookie` as a member or a destructured key: a secret, or an
 *   encoded copy of one (`btoa(token)`), has nowhere to go but the client's closure. The generated
 *   types' `cookie` parameters (a type's key) are not refused. The names are written out here, not
 *   read from the configuration, so that a name dropped from it fails a cell.
 * - **The channels, by position (M1).** `navigator.storage`, `navigator.clipboard`,
 *   `history.pushState` and `replaceState`, a write to `location` or one of its members,
 *   `location.replace`, `assign`, `hash` and `href`, a write to `document.title`, `window.open` and
 *   the window's `name` are each refused in each position (a call, an assignment, a destructuring,
 *   an alias, an argument, an optional chain, a member of the window, a computed name): the table
 *   `CHANNELS` fails if a cell is accepted. The page's objects are used by direct member access
 *   alone (`ALIASES`: no alias, no destructuring, no argument, no computed member), so that the
 *   table sees every use. `history.replaceState` stands in `src/operator/url.ts` alone, in its one
 *   call (`replaceState(null, "", CURATE_PATH)`, D412).
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

const STORAGE_NAMES = ["localStorage", "sessionStorage", "indexedDB", "IDBFactory", "cookieStore", "BroadcastChannel", "postMessage", "SharedWorker", "MessageChannel", "caches", "CacheStorage"];

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

const POSITION_NAMES = ["call", "assignment", "destructure", "alias", "argument", "optional chain", "member of the window", "computed name"] as const;
type PositionName = (typeof POSITION_NAMES)[number];

/** A channel's cells: the code of each position, or `n/a: why` where the position is no use of the
 * channel (a syntax error, or a use that is no channel). */
type Cells = Readonly<Record<PositionName, string>>;

/** The body of a snippet: `t` is the secret, `use` takes whatever it is given. */
const snippet = (body: string): string => `export const f = (t: string, use: (x: unknown) => void) => { ${body} };`;

/** A channel that is any member of an object: `R.M` called, assigned, destructured, aliased, passed,
 * chained and computed, and as a member of the window. */
function member(root: string, name: string, call: string): Cells {
  return {
    call: snippet(call),
    assignment: snippet(`${root}.${name} = t;`),
    destructure: snippet(`const { ${name}: a } = ${root}; use(a);`),
    alias: snippet(`const a = ${root}; use(a.${name});`),
    argument: snippet(`use(${root});`),
    "optional chain": snippet(`use(${root}?.${name});`),
    "member of the window": snippet(`use(window.${root}.${name});`),
    "computed name": snippet(`use(${root}['${name}']);`),
  };
}

/** The channels that are a write alone (`document.title = t`, `location = t`): reading one is no
 * channel, and an optional chain cannot be assigned to. */
function written(root: string, name: string, own: string): Cells {
  return {
    call: `n/a: ${root}.${name} is no function`,
    assignment: snippet(own),
    destructure: snippet(`const { ${name}: a } = ${root}; use(a);`),
    alias: snippet(`const a = ${root}; a.${name} = t;`),
    argument: snippet(`use(${root});`),
    "optional chain": `n/a: an optional chain cannot be assigned to`,
    "member of the window": snippet(`window.${root}.${name} = t;`),
    "computed name": snippet(`${root}['${name}'] = t;`),
  };
}

/** Each channel out of the page's memory (D423), in each position. A cell is refused in every
 * layer of `src/`; the table fails if one is accepted. */
const CHANNELS: Readonly<Record<string, Cells>> = {
  "navigator.storage (the origin private file system)": member("navigator", "storage", "navigator.storage.getDirectory();"),
  "navigator.clipboard": member("navigator", "clipboard", "void navigator.clipboard.writeText(t);"),
  "history.pushState": member("history", "pushState", "history.pushState(null, '', '/curate#' + t);"),
  "history.replaceState": member("history", "replaceState", "history.replaceState(null, '', '/curate#' + t);"),
  "location.replace": member("location", "replace", "location.replace('/curate#' + t);"),
  "location.assign": member("location", "assign", "location.assign('/curate?' + t);"),
  "location.hash": member("location", "hash", "location.hash = t;"),
  "location.href": member("location", "href", "location.href = '/curate#' + t;"),
  "window.open": member("window", "open", "window.open('/curate?' + t);"),
  "window.name": member("window", "name", "window.name = t;"),
  "location written": written("window", "location", "location = t;"),
  "a member of location written": written("location", "search", "location.search = t;"),
  "document.title written": written("document", "title", "document.title = t;"),
};

const CHANNEL_LAYERS = ["src/probe.tsx", "src/api/probe.ts", "src/api/client.ts", "src/operator/probe.tsx"];

describe("the channels out of the page's memory, by position (M1)", () => {
  it("has a cell for every channel in every position, each code or a stated n/a", () => {
    for (const [name, cells] of Object.entries(CHANNELS)) {
      expect(Object.keys(cells).sort(), name).toEqual([...POSITION_NAMES].sort());
      for (const cell of Object.values(cells)) {
        expect(cell.startsWith("export const f = ") || cell.startsWith("n/a: "), name).toBe(true);
      }
    }
    expect(Object.keys(CHANNELS)).toHaveLength(13);
  });

  it.each(
    Object.entries(CHANNELS).flatMap(([name, cells]) =>
      Object.entries(cells)
        .filter(([, code]) => code.startsWith("export"))
        .map(([position, code]) => [name, position, code] as const),
    ),
  )("%s, as a %s, is refused in every layer", async (_name, _position, code) => {
    for (const file of CHANNEL_LAYERS) {
      expect(await flagged(code, file), file).toBe(true);
    }
  });

  it.each([
    ["a bare window.name assignment through an alias of the global object", "export const f = (t: string) => { const w = window; w.name = t; };"],
    ["self.location written", "export const f = (t: string) => { self.location = t; };"],
    ["a string naming pushState, on any object", "export const f = (o: Record<string, unknown>) => o['pushState'];"],
    ["a string naming replaceState, on any object", "export const f = (o: Record<string, unknown>) => o['replaceState'];"],
    ["top.location written (no builtin rule sees it)", "export const f = (t: string) => { top.location = t; };"],
    ["parent.location written", "export const f = (t: string) => { parent.location = t; };"],
    ["opener.location written", "export const f = (t: string) => { opener.location = t; };"],
    ["document.location written", "export const f = (t: string) => { document.location = t; };"],
    ["top.name written", "export const f = (t: string) => { parent.name = t; top.name = t; };"],
    ["parent.open", "export const f = (t: string) => { parent.open(t); };"],
    ["top.location.hash written", "export const f = (t: string) => { top.location.hash = t; };"],
    ["window.location.hash written", "export const f = (t: string) => { window.location.hash = t; };"],
    ["document.defaultView.name written", "export const f = (t: string) => { document.defaultView.name = t; };"],
    ["document.defaultView.open", "export const f = (t: string) => { document.defaultView.open(t); };"],
    ["globalThis.navigator.storage", "export const f = () => globalThis.navigator.storage.getDirectory();"],
    ["window.document.title written", "export const f = (t: string) => { window.document.title = t; };"],
    ["a bare open", "export const f = (t: string) => { open('/curate#' + t); };"],
    ["a title in a compound assignment", "export const f = (t: string) => { document.title += t; };"],
    ["location.href read", "export const f = () => location.href;"],
    ["location.hash read", "export const f = () => window.location.hash;"],
    ["window.caches", "export const f = () => window.caches.open('x');"],
    ["a Cache storage", "export const f = (c: CacheStorage) => c;"],
  ])("refuses %s, in every layer", async (_case, code) => {
    for (const file of CHANNEL_LAYERS) {
      expect(await flagged(code, file), file).toBe(true);
    }
  });

  it.each([
    ["a read of document.title", "export const f = () => document.title;"],
    ["a read of history.length", "export const f = () => history.length;"],
    ["a read of location.origin", "export const f = () => window.location.origin;"],
    ["the navigator's language", "export const f = () => navigator.language;"],
    ["a listener on the window", "export const f = (g: () => void) => { window.addEventListener('pagehide', g); };"],
    ["an element of the document", "export const f = () => document.getElementById('root');"],
    ["a member named like a page object", "export const f = (x: { window: unknown; document: unknown; history: number }) => [x.window, x.document, x.history];"],
    ["an object key named like a page object", "export const f = (a: unknown) => ({ window: a, document: a, location: a, navigator: a });"],
    ["a type key named like a page object", "export interface T { readonly window: string; readonly document: string }\nexport const f = (t: T) => t.window + t.document;"],
    ["an ordinary open, name, title and storage", "export const f = (x: { open(): void; name: string; title: string; storage: string }) => { x.open(); x.name = x.title + x.storage; };"],
    ["a method named like a page object", "export class C {\n  window(): void {}\n  history = 1;\n}"],
  ])("lets %s pass, so that a flag is the rule's", async (_case, code) => {
    for (const file of CHANNEL_LAYERS) {
      expect(await ids(code, file), file).toEqual([]);
    }
  });
});

const ALIASED = ["window", "globalThis", "self", "top", "parent", "frames", "opener", "location", "history", "navigator", "document"];

/** Each position a page object can stand in, but as the object of a member (`G` is the object). */
const ALIASES: readonly (readonly [string, string])[] = [
  ["a variable's initialiser", "const a = @G@; use(a);"],
  ["a destructuring's source", "const { name: a } = @G@; use(a);"],
  ["an assignment's right side", "let a: unknown; a = @G@; use(a);"],
  ["an argument", "use(@G@);"],
  ["a returned value", "return @G@;"],
  ["an array element", "use([@G@]);"],
  ["an object's value", "use({ a: @G@ });"],
  ["a shorthand member", "const @G@ = 1; use({ @G@ });"],
  ["a spread", "use({ ...@G@ });"],
  ["a conditional", "use(t === '' ? @G@ : null);"],
  ["a logical operand", "use(@G@ ?? null);"],
  ["an awaited value", "await use(@G@);"],
  ["a default value", "((a = @G@) => use(a))();"],
  ["a template's substitution", "use(`${@G@}`);"],
  ["a computed member", "use(@G@[t]);"],
  ["a computed key", "use(t[@G@]);"],
  ["a computed member of a member", "use(window.@G@[t]);"],
  ["a binding", "const @G@ = 1; use(@G@);"],
];

describe("the page's objects are used by direct member access alone (M1)", () => {
  it.each(ALIASED.flatMap((name) => ALIASES.map(([position, body]) => [name, position, body.replaceAll("@G@", name)] as const)))(
    "%s, as %s, is refused in every layer",
    async (_name, _position, body) => {
      const code = `export const f = async (t: string, use: (x: unknown) => unknown): Promise<unknown> => { ${body} return undefined; };`;
      for (const file of CHANNEL_LAYERS) {
        expect(await flagged(code, file), file).toBe(true);
      }
    },
  );

  it("refuses none of the channels and aliases in the tooling", async () => {
    const code = "export const f = (t: string) => { const w = window; w.name = t; location.hash = t; document.title = t; void navigator.storage; };\n";
    for (const file of ["scripts/probe.ts", "tests/probe.ts", "e2e/probe.ts"]) {
      expect(await flagged(code, file), file).toBe(false);
    }
  });
});

describe("history.replaceState stands in src/operator/url.ts alone, in its one call (D412, M1)", () => {
  const URL_FILE = "src/operator/url.ts";
  const ONE = "export function forgetAddress(): void { history.replaceState(null, '', CURATE_PATH); }";

  it("accepts the one call there", async () => {
    expect(await ids(`const CURATE_PATH = '/curate';\n${ONE}`, URL_FILE)).toEqual([]);
  });

  it.each(["src/probe.tsx", "src/api/probe.ts", "src/operator/probe.tsx", "src/harness/probe.tsx", "src/api/client.ts"])("refuses the same call in %s", async (file) => {
    expect(await flagged(`const CURATE_PATH = '/curate';\n${ONE}`, file)).toBe(true);
  });

  it.each([
    ["a state", "history.replaceState({ t: 1 }, '', CURATE_PATH);"],
    ["undefined for its state", "history.replaceState(undefined, '', CURATE_PATH);"],
    ["another path", "history.replaceState(null, '', '/curate#x');"],
    ["a built path", "history.replaceState(null, '', CURATE_PATH + '#x');"],
    ["a title", "history.replaceState(null, 'x', CURATE_PATH);"],
    ["a fourth argument", "history.replaceState(null, '', CURATE_PATH, 1);"],
    ["a fourth argument through a cast", "(history.replaceState as (...a: unknown[]) => void)(null, '', CURATE_PATH, 1);"],
    ["no argument", "history.replaceState();"],
    ["no argument through a cast", "(history.replaceState as (...a: unknown[]) => void)();"],
    ["window.history", "window.history.replaceState(null, '', CURATE_PATH);"],
    ["a held function", "const r = history.replaceState; void r;"],
    ["a computed name", "history['replaceState'](null, '', CURATE_PATH);"],
    ["pushState", "history.pushState(null, '', CURATE_PATH);"],
    ["pushState by a string", "history['pushState'](null, '', CURATE_PATH);"],
    ["a string naming replaceState, on any object", "const o: Record<string, unknown> = {}; void o['replaceState'];"],
    ["location.replace", "location.replace(CURATE_PATH);"],
    ["location.hash", "location.hash = '';"],
  ])("refuses %s there", async (_case, body) => {
    expect(await flagged(`const CURATE_PATH = '/curate';\nexport function f(): void { ${body} }`, URL_FILE)).toBe(true);
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
