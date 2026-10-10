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
 *   call (`replaceState(null, "", "/curate")`, the path a literal, D412).
 * - **The page's objects by type (M1, round 2).** A generated table (`TYPED`: each type of the
 *   page's, each way to reach one, each position, each channel) must be refused by the type-aware
 *   rule `aibi/page-objects` itself (not merely by a name rule), and control rows must stay
 *   accepted. A self-check holds the table's types equal to the rule's.
 * - **No mutable module binding in `src/operator/` (m4).**
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

import { PAGE_TYPES } from "../../scripts/page-objects.mjs";
import { type Checked, check, WEB } from "./program";

const PARSER = {
  languageOptions: {
    parserOptions: {
      projectService: {
        allowDefaultProject: ["src/*.tsx", "src/api/probe.ts", "src/operator/probe.tsx", "src/harness/probe.tsx", "src/catalogue/probe.tsx", "scripts/probe.ts", "tests/probe.ts", "e2e/probe.ts", "shared/probe.ts"],
        defaultProject: "tsconfig.app.json",
        maximumDefaultProjectFileMatchCount_THIS_WILL_SLOW_DOWN_LINTING: 16,
      },
    },
  },
};

const eslint = new ESLint({ cwd: WEB, overrideConfig: PARSER });

/** A linter that has `src/operator/url.ts` under `rules` besides the configuration's (a layer of the
 * lint switched off, to see the other alone). */
const without = (rules: Record<string, "off">, files: string[] = ["src/operator/url.ts"]) => new ESLint({ cwd: WEB, overrideConfig: [PARSER, { files, rules }] });

/** The name layer alone: the type-aware rule is off in every file of `src/`, so that the layer of
 * names, which the type rule makes redundant on most cells, is held by its own tables. */
const NAMES = without({ "aibi/page-objects": "off" }, ["src/**/*.{ts,tsx}", "shared/*.ts"]);

const POLICY = new Set(["no-restricted-syntax", "no-restricted-globals", "no-restricted-properties", "@typescript-eslint/no-restricted-imports", "aibi/page-objects"]);

async function ids(code: string, file: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.flatMap((message) => (message.ruleId !== null && POLICY.has(message.ruleId) ? [message.ruleId] : []));
}

const flagged = async (code: string, file: string): Promise<boolean> => (await ids(code, file)).length > 0;

/** `ids`, with the type-aware rule off (the layer of names alone). */
async function idsNames(code: string, file: string): Promise<string[]> {
  const [result] = await NAMES.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.flatMap((message) => (message.ruleId !== null && POLICY.has(message.ruleId) ? [message.ruleId] : []));
}

const flaggedNames = async (code: string, file: string): Promise<boolean> => (await idsNames(code, file)).length > 0;

const STORAGE_NAMES = ["localStorage", "sessionStorage", "indexedDB", "IDBFactory", "cookieStore", "BroadcastChannel", "postMessage", "SharedWorker", "MessageChannel", "Worker", "caches", "CacheStorage"];

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
        expect(await flaggedNames(code, file), file).toBe(true);
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
      expect(await flaggedNames(code, file), file).toBe(true);
    }
  });

  it.each([
    ["a type's cookie key, as the generated types write it", "export interface P { readonly cookie?: never; readonly query?: never }\nexport const f = (p: P) => p.query;"],
    ["a name of an ordinary object", "export const f = (x: { name: string }) => x.name;"],
    ["a word that only contains a name", "export const f = (x: { localStorageKey: string; postMessages: number }) => [x.localStorageKey, x.postMessages];"],
  ])("lets %s pass, so that a flag is the rule's", async (_case, code) => {
    for (const file of LAYERS) {
      expect(await idsNames(code, file), file).toEqual([]);
    }
  });

  it("refuses none of them in the tooling", async () => {
    for (const file of ["scripts/probe.ts", "tests/probe.ts", "e2e/probe.ts"]) {
      expect(await flaggedNames("export const f = () => [localStorage, document.cookie, window.name];\n", file), file).toBe(false);
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
  "navigator.locks": member("navigator", "locks", "void navigator.locks.request('session:' + t, () => undefined);"),
  "navigator.registerProtocolHandler": member("navigator", "registerProtocolHandler", "navigator.registerProtocolHandler('web+x', '/c?%s' + t);"),
  "navigator.mediaSession": member("navigator", "mediaSession", "use(navigator.mediaSession.metadata);"),
  "document.open": member("document", "open", "document.open('/curate?' + t, '', '');"),
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
    expect(Object.keys(CHANNELS)).toHaveLength(17);
  });

  it.each(
    Object.entries(CHANNELS).flatMap(([name, cells]) =>
      Object.entries(cells)
        .filter(([, code]) => code.startsWith("export"))
        .map(([position, code]) => [name, position, code] as const),
    ),
  )("%s, as a %s, is refused in every layer", async (_name, _position, code) => {
    for (const file of CHANNEL_LAYERS) {
      expect(await flaggedNames(code, file), file).toBe(true);
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
      expect(await flaggedNames(code, file), file).toBe(true);
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
      expect(await idsNames(code, file), file).toEqual([]);
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
        expect(await flaggedNames(code, file), file).toBe(true);
      }
    },
  );

  it.each(["Window", "Location", "Navigator", "Document", "HTMLDocument", "History", "Storage", "StorageManager", "Clipboard", "CacheStorage", "Cache", "ShadowRoot"])(
    "refuses a test against the constructor %s, by name",
    async (name) => {
      for (const file of CHANNEL_LAYERS) {
        expect(await flaggedNames(`export const f = (el: unknown) => el instanceof ${name};`, file), file).toBe(true);
      }
    },
  );

  it("refuses none of the channels and aliases in the tooling", async () => {
    const code = "export const f = (t: string) => { const w = window; w.name = t; location.hash = t; document.title = t; void navigator.storage; };\n";
    for (const file of ["scripts/probe.ts", "tests/probe.ts", "e2e/probe.ts"]) {
      expect(await flaggedNames(code, file), file).toBe(false);
    }
  });
});

// ---------------------------------------------------------------------------------------------
// The page's objects by type (M1, round 2): a generated table.

/** A type of the page's, the ways to reach a value of it, and the uses of it that are channels. */
interface Typed {
  /** The type's name, as a parameter's annotation. */
  readonly type: string;
  /** Types that extend it (a parameter of one is a page object too). */
  readonly subtypes?: readonly string[];
  /** Expressions that give a value of the type: by name, through the window, through `defaultView`,
   * `ownerDocument`, an event's `view`, a promise's value. */
  readonly reachers: readonly (readonly [string, string])[];
  /** The channels of the type: each as a written form (`x.name = t`, or none) and a read form, with
   * the member's operator (`.` or `?.`) given; a read form of `null` is a channel written alone
   * (`document.title`, `location.search`: reading one is no channel). */
  readonly channels: readonly { readonly name: string; readonly write: string | null; readonly read: ((x: string, q: string) => string) | null }[];
}

const TYPED: readonly Typed[] = [
  {
    type: "Window",
    reachers: [
      ["the name", "window"],
      ["globalThis", "globalThis"],
      ["self", "self"],
      ["window.window", "window.window"],
      ["window.self", "window.self"],
      ["window.top", "window.top!"],
      ["window.parent", "window.parent"],
      ["window.frames", "window.frames"],
      ["document.defaultView", "document.defaultView!"],
      ["el.ownerDocument.defaultView", "el.ownerDocument.defaultView!"],
      ["event.view", "event.view!"],
    ],
    channels: [
      { name: "name", write: "@X@.name = t;", read: (x, q) => `use(${x}${q}name);` },
      { name: "open", write: null, read: (x, q) => `${x}${q}open(t);` },
    ],
  },
  {
    type: "Location",
    reachers: [
      ["the name", "location"],
      ["window.location", "window.location"],
      ["self.location", "self.location"],
      ["document.location", "document.location!"],
      ["el.ownerDocument.location", "el.ownerDocument.location"],
      ["event.view.location", "event.view!.location"],
      ["document.defaultView.location", "document.defaultView!.location"],
    ],
    channels: [
      { name: "hash", write: "@X@.hash = t;", read: (x, q) => `use(${x}${q}hash);` },
      { name: "href", write: "@X@.href = '/curate#' + t;", read: (x, q) => `use(${x}${q}href);` },
      { name: "replace", write: null, read: (x, q) => `${x}${q}replace('/curate#' + t);` },
      { name: "assign", write: null, read: (x, q) => `${x}${q}assign('/curate?' + t);` },
      { name: "search", write: "@X@.search = t;", read: null },
    ],
  },
  {
    type: "Navigator",
    reachers: [
      ["the name", "navigator"],
      ["window.navigator", "window.navigator"],
      ["self.navigator", "self.navigator"],
      ["event.view.navigator", "event.view!.navigator"],
      ["el.ownerDocument.defaultView.navigator", "el.ownerDocument.defaultView!.navigator"],
    ],
    channels: [
      { name: "storage", write: null, read: (x, q) => `void ${x}${q}storage;` },
      { name: "clipboard", write: null, read: (x, q) => `void ${x}${q}clipboard;` },
      { name: "locks", write: null, read: (x, q) => `void ${x}${q}locks;` },
      { name: "registerProtocolHandler", write: null, read: (x, q) => `${x}${q}registerProtocolHandler('web+x', '/c?%s' + t);` },
      { name: "mediaSession", write: null, read: (x, q) => `void ${x}${q}mediaSession;` },
    ],
  },
  {
    type: "Document",
    subtypes: ["XMLDocument", "HTMLDocument"],
    reachers: [
      ["the name", "document"],
      ["window.document", "window.document"],
      ["self.document", "self.document"],
      ["el.ownerDocument", "el.ownerDocument"],
      ["event.view.document", "event.view!.document"],
      ["document.defaultView.document", "document.defaultView!.document"],
    ],
    channels: [
      { name: "title", write: "@X@.title = t;", read: null },
      { name: "cookie", write: "@X@.cookie = t;", read: (x, q) => `use(${x}${q}cookie);` },
      { name: "open", write: null, read: (x, q) => `use(${x}${q}open('/c?' + t, '', ''));` },
      { name: "write", write: null, read: (x, q) => `${x}${q}write(t);` },
      { name: "writeln", write: null, read: (x, q) => `${x}${q}writeln(t);` },
    ],
  },
  {
    type: "History",
    reachers: [
      ["the name", "history"],
      ["window.history", "window.history"],
      ["self.history", "self.history"],
      ["event.view.history", "event.view!.history"],
    ],
    channels: [
      { name: "pushState", write: null, read: (x, q) => `${x}${q}pushState(null, '', '/curate#' + t);` },
      { name: "replaceState", write: null, read: (x, q) => `${x}${q}replaceState(null, '', '/curate#' + t);` },
    ],
  },
  {
    type: "StorageManager",
    reachers: [
      ["navigator.storage", "navigator.storage"],
      ["window.navigator.storage", "window.navigator.storage"],
      ["event.view.navigator.storage", "event.view!.navigator.storage"],
    ],
    channels: [
      { name: "getDirectory", write: null, read: (x, q) => `void ${x}${q}getDirectory();` },
      { name: "persist", write: null, read: (x, q) => `void ${x}${q}persist();` },
    ],
  },
  {
    type: "Clipboard",
    reachers: [
      ["navigator.clipboard", "navigator.clipboard"],
      ["window.navigator.clipboard", "window.navigator.clipboard"],
    ],
    channels: [
      { name: "writeText", write: null, read: (x, q) => `void ${x}${q}writeText(t);` },
      { name: "readText", write: null, read: (x, q) => `void ${x}${q}readText();` },
    ],
  },
  {
    type: "Storage",
    reachers: [
      ["window.localStorage", "window.localStorage"],
      ["self.sessionStorage", "self.sessionStorage"],
    ],
    channels: [
      { name: "setItem", write: null, read: (x, q) => `${x}${q}setItem('k', t);` },
      { name: "getItem", write: null, read: (x, q) => `use(${x}${q}getItem('k'));` },
    ],
  },
  {
    type: "CacheStorage",
    reachers: [
      ["window.caches", "window.caches"],
      ["self.caches", "self.caches"],
    ],
    channels: [
      { name: "open", write: null, read: (x, q) => `void ${x}${q}open('c');` },
      { name: "keys", write: null, read: (x, q) => `void ${x}${q}keys();` },
    ],
  },
  {
    type: "Cache",
    reachers: [["an awaited cache", "await window.caches.open('c')"]],
    channels: [
      { name: "put", write: null, read: (x, q) => `void ${x}${q}put('/x', new Response(t));` },
      { name: "keys", write: null, read: (x, q) => `void ${x}${q}keys();` },
    ],
  },
  {
    type: "ShadowRoot",
    reachers: [
      ["el.shadowRoot", "el.shadowRoot!"],
      ["el.attachShadow", "el.attachShadow({ mode: 'open' })"],
    ],
    channels: [
      { name: "textContent", write: "@X@.textContent = t;", read: null },
    ],
  },
];

/** What a function that holds a typed value is called with: a string, a sink, an element, an event. */
const typedBody = (body: string): string => `export const f = async (t: string, use: (x: unknown) => void, el: HTMLElement, event: UIEvent) => { ${body} };`;

/** Every use of a typed value that the rule must refuse, for a value `x` given by `reacher`. */
function typedRows(entry: Typed, reacher: string): [string, string][] {
  const rows: [string, string][] = [];
  for (const channel of entry.channels) {
    if (channel.read !== null) {
      rows.push([`direct ${channel.name}, read`, channel.read(reacher, ".")]);
      rows.push([`direct ${channel.name}, optional chain`, channel.read(reacher, "?.")]);
      rows.push([`alias, ${channel.name}`, `const a = ${reacher}; ${channel.read("a", ".")}`]);
      rows.push([`a computed member, ${channel.name}`, `use((${reacher})['${channel.name}']);`]);
    }
    if (channel.write !== null) {
      rows.push([`direct ${channel.name}, written`, channel.write.replaceAll("@X@", reacher)]);
      rows.push([`alias, ${channel.name}, written`, `const a = ${reacher}; ${channel.write.replaceAll("@X@", "a")}`]);
    }
  }
  const name = entry.channels[0]?.name ?? "x";
  rows.push(["a computed member, by a variable", `use((${reacher})[t]);`]);
  rows.push(["destructured", `const { length: a } = ${reacher}; use(a);`]);
  rows.push(["destructured, renamed", `const { ${name}: a } = ${reacher}; use(a);`]);
  rows.push(["a computed key", `use(el[${reacher}]);`]);
  rows.push(["a computed key in an object", `use({ [${reacher}]: 1 });`]);
  if (/^[A-Za-z]+$/u.test(reacher)) {
    rows.push(["a shorthand member", `use({ ${reacher} });`]);
  }
  rows.push(["a spread", `use({ ...${reacher} });`]);
  rows.push(["an argument", `use(${reacher});`]);
  rows.push(["a returned value", `const g = () => ${reacher}; use(g);`]);
  rows.push(["a returned value, a function", `function g() { return ${reacher}; } use(g);`]);
  rows.push(["an array element", `use([${reacher}]);`]);
  rows.push(["an object's value", `use({ a: ${reacher} });`]);
  rows.push(["a conditional", `use(t === '' ? ${reacher} : null);`]);
  rows.push(["a logical operand", `use(${reacher} ?? null);`]);
  rows.push(["a default value", `((a = ${reacher}) => use(a))();`]);
  rows.push(["a typeof", `use(typeof ${reacher});`]);
  rows.push(["getOwnPropertyDescriptor", `use(Object.getOwnPropertyDescriptor(${reacher}, 'k'));`]);
  rows.push(["getOwnPropertyDescriptor, then its setter", `Object.getOwnPropertyDescriptor(${reacher}, '${name}')?.set?.call(${reacher}, t);`]);
  return rows;
}

/** The rows that do not depend on how a value was reached: a parameter of the type, a test against its constructor. */
function typedOwnRows(entry: Typed): [string, string][] {
  const used = (x: string, q: string): string => entry.channels.map((channel) => channel.read?.(x, q) ?? `void ${x}${q}x;`).join(" ");
  return [
    ["a parameter", `const g = (a: ${entry.type}) => { ${used("a", ".")} }; use(g);`],
    ["a parameter, nullable", `const g = (a: ${entry.type} | null) => { ${used("a", "?.")} }; use(g);`],
    ["a parameter, a union", `const g = (a: ${entry.type} | string) => { use(a); }; use(g);`],
    ["a parameter, an intersection", `const g = (a: ${entry.type} & { readonly x?: number }) => { use(a); }; use(g);`],
    ["a parameter, a generic constraint", `const g = <T extends ${entry.type}>(a: T) => { use(a); }; use(g);`],
    ["a parameter, a list", `const g = (a: readonly ${entry.type}[]) => { use(a[0]); }; use(g);`],
    ["a parameter, a bare binding of the name", `const g = (a: ${entry.type}) => a; use(g);`],
    ...(entry.subtypes ?? []).map((sub): [string, string] => [`a parameter, a subtype (${sub})`, `const g = (a: ${sub}) => { ${used("a", ".")} }; use(g);`]),
    ...(entry.subtypes ?? []).map((sub): [string, string] => [`a parameter, a subtype (${sub}), bare`, `const g = (a: ${sub}) => a; use(g);`]),
    ["a constructed value", `use(new ${entry.type}());`],
    ["a this typed as the type, no parameter", `const o: { m(): void } & ThisType<${entry.type}> = { m() { use(this); } }; use(o);`],
    ["a test against its constructor", `use(el instanceof ${entry.type});`],
    ["a narrowing by its constructor", `const w = event.currentTarget; if (w instanceof ${entry.type}) { use(w); }`],
  ];
}

const TYPED_LAYERS = ["src/probe.tsx", "src/api/probe.ts", "src/api/client.ts"] as const;

/** The code of every cell: type, reacher, row. */
const TYPED_CELLS: readonly (readonly [string, string, string, string])[] = TYPED.flatMap((entry) => [
  ...entry.reachers.flatMap(([label, reacher]) => typedRows(entry, reacher).map(([row, body]) => [entry.type, label, row, typedBody(body)] as const)),
  ...typedOwnRows(entry).map(([row, body]) => [entry.type, "(no reacher)", row, typedBody(body)] as const),
]);

/** Constructors that need `Window`'s own global, in a position that names no instance (`el instanceof ShadowRoot` is fine in lib.dom). */
describe("the page's objects by type: a generated table (M1, round 2)", () => {
  it("is generated from the rule's own list of types (the self-check)", () => {
    const configured = [...PAGE_TYPES.keys()];
    expect(configured.length).toBeGreaterThan(0);
    const covered = new Set(TYPED.map((entry) => entry.type));
    // `globalThis` is the type of the global object itself, reached by `globalThis` in the Window rows.
    expect(configured.filter((name) => name !== "globalThis" && !covered.has(name))).toEqual([]);
    expect([...covered].filter((name) => !configured.includes(name))).toEqual([]);
    expect(TYPED_CELLS.length).toBeGreaterThan(1000);
  });

  it("has every cell refused by the type rule itself, in the layers", async () => {
    // Collected, not one test a cell: a failing cell is named with its code.
    const accepted: string[] = [];
    for (const [type, reacher, row, code] of TYPED_CELLS) {
      const found = await ids(code, "src/probe.tsx");
      if (!found.includes("aibi/page-objects")) {
        accepted.push(`${type} | ${reacher} | ${row} | ${code}`);
      }
    }
    expect(accepted).toEqual([]);
  });

  it.each(TYPED_LAYERS.slice(1))("has the first reacher's every row refused by the type rule in %s", async (file) => {
    const accepted: string[] = [];
    for (const entry of TYPED) {
      const [, reacher] = entry.reachers[0] ?? ["", ""];
      for (const [row, body] of [...typedRows(entry, reacher), ...typedOwnRows(entry)]) {
        const code = typedBody(body);
        if (!(await ids(code, file)).includes("aibi/page-objects")) {
          accepted.push(`${entry.type} | ${row} | ${code}`);
        }
      }
    }
    expect(accepted).toEqual([]);
  });

  it.each([
    ["a listener on the window", "window.addEventListener('pagehide', () => undefined);"],
    ["a listener on the document", "document.addEventListener('visibilitychange', () => undefined, { passive: true });"],
    ["the document's root", "use(document.getElementById('root'));"],
    ["the document's body", "document.body.append(el);"],
    ["the title, read", "use(document.title);"],
    ["the history's length", "use(history.length);"],
    ["the navigator's agent", "use(navigator.userAgent);"],
    ["the location's origin", "use(window.location.origin);"],
    ["the location's pathname", "use(location.pathname);"],
    ["the window's width", "use(window.innerWidth);"],
    ["a timer", "const n = setInterval(() => undefined, 1000); clearInterval(n);"],
    ["the clock", "use(performance.now() + Date.now());"],
    ["an element's text", "el.textContent = t;"],
    ["an element's parent node", "use(el.parentNode);"],
    ["an event's target", "use(event.target);"],
    ["an event's current target", "use(event.currentTarget);"],
    ["a member named like a reacher, not of a page object's type", "const x = { window: 1, view: 2, parent: 3, top: 4, self: 5, defaultView: 6, ownerDocument: 7 }; use(x.window + x.view + x.parent + x.top + x.self + x.defaultView + x.ownerDocument);"],
    ["a member named like a channel, not of a page object's type", "const x = { name: 'a', open: () => undefined, hash: 'b', href: 'c', title: 'd', storage: 'e' }; x.name = t; x.open(); x.title = t; use(x.hash + x.href + x.storage);"],
    ["a generic that is no page object's", "const g = <T extends object>(a: T) => a; use(g({}));"],
    ["a function that takes a listener", "const g = (target: { addEventListener(type: string, listener: () => void): void }) => { target.addEventListener('x', () => undefined); }; use(g);"],
    ["the wrappers of operator.ts", "use({ window: { addEventListener: (type: string, listener: EventListenerOrEventListenerObject, options?: AddEventListenerOptions) => { window.addEventListener(type, listener, options); } }, document: { addEventListener: (type: string, listener: EventListenerOrEventListenerObject, options?: AddEventListenerOptions) => { document.addEventListener(type, listener, options); } } });"],
    ["the targets of curator.ts", "const targets = { window: { addEventListener(_type: string, _listener: (event: Event) => void) { return; } } }; targets.window.addEventListener('pagehide', () => undefined);"],
  ])("lets %s pass, so that a flag is the rule's", async (_case, body) => {
    for (const file of TYPED_LAYERS) {
      expect(await ids(typedBody(body), file), file).toEqual([]);
    }
  });

  it.each([
    ["window.window", "window.window.addEventListener('x', g);"],
    ["window.self", "window.self.addEventListener('x', g);"],
    ["window.top", "window.top!.addEventListener('x', g);"],
    ["window.parent", "window.parent.addEventListener('x', g);"],
    ["window.frames", "window.frames.addEventListener('x', g);"],
    ["document.defaultView", "document.defaultView!.addEventListener('x', g);"],
    ["el.ownerDocument", "use(el.ownerDocument.getElementById('x'));"],
    ["el.ownerDocument, optional", "use(el.ownerDocument?.getElementById('x'));"],
    ["event.view", "event.view!.addEventListener('x', g);"],
    ["event.view, optional", "event.view?.addEventListener('x', g);"],
    ["a node's ownerDocument body", "use(el.ownerDocument.body);"],
  ])("refuses a member that reaches a page object, whatever it is then used for (%s)", async (_case, body) => {
    const code = typedBody(`const g = () => undefined; ${body}`);
    for (const file of TYPED_LAYERS) {
      expect(await ids(code, file), file).toContain("aibi/page-objects");
    }
  });

  it.each([
    ["an assignment to the title", "document.title = t;"],
    ["an assignment to a member of the location", "location.search = t;"],
    ["a compound assignment", "document.title += t;"],
    ["an increment of a member of a page object", "history.length++;"],
    ["a decrement of a member", "--history.length;"],
    ["a delete of a member of the history", "delete history.state;"],
    ["a delete of a member of the document", "delete document.body;"],
    ["a delete of a member of the window", "delete window.onpagehide;"],
    ["a write to a member of the window", "window.onpagehide = null;"],
    ["a write to the document's body", "document.body = el as HTMLBodyElement;"],
    ["a write to a member of the navigator", "(navigator as unknown as { userAgent: string }).userAgent = t;"],
  ])("refuses a write to a member of a page object (%s)", async (_case, body) => {
    const found = await ids(typedBody(body), "src/probe.tsx");
    expect(found).toContain("aibi/page-objects");
  });

  it.each([
    ["an optional chain on a page object", "window?.addEventListener('x', () => undefined);"],
    ["an optional chain through a member", "use(window?.document?.getElementById('x'));"],
    ["a parenthesised optional chain, a non-channel member", "use((window?.document).getElementById('x'));"],
    ["an interface that names page objects' types", "interface P { readonly w: Window; readonly n: Navigator; readonly d: Document } use(null as unknown as P);"],
    ["an interface that extends a page object's type, no value", "interface MyWindow extends Window { readonly extra: number } use(null as unknown as MyWindow | null);"],
    ["a type alias of a page object's type, no value", "type Alias = Window; use(null as unknown as Alias | null);"],
    ["a generic that names a page object as a constraint, no value", "const g = <T extends Window>() => 1; use(g);"],
    ["a class field typed as a page object, no value", "class C { w!: Window; } use(C);"],
    ["the document of the window, a non-channel member", "use(window.document.getElementById('x'));"],
    ["the history of the window, a non-channel member", "use(window.history.length);"],
    ["a user type named like a page object's: Location", "interface Location { readonly hash: string } const g = (a: Location) => use(a.hash); use(g);"],
    ["a user type named like a page object's: Cache", "interface Cache { put(x: string): void } const g = (a: Cache) => { a.put(t); }; use(g);"],
    ["a user type named like a page object's: Storage", "interface Storage { setItem(k: string): void } const g = (a: Storage) => { a.setItem(t); }; use(g);"],
    ["a user type named like a page object's: Document", "interface Document { readonly title: string } const g = (a: Document) => use(a); use(g);"],
    ["a user type named like a page object's: History", "interface History { go(): void } const g = (a: History) => { a.go(); }; use(g);"],
    ["a user type named like a page object's: Clipboard", "interface Clipboard { writeText(x: string): void } const g = (a: Clipboard) => { a.writeText(t); }; use(g);"],
    ["a user type named like a page object's: Navigator", "interface Navigator { readonly storage: string } const g = (a: Navigator) => use(a.storage); use(g);"],
    ["a user class named Window", "class Window { name = ''; open(): void { return; } } const w = new Window(); w.name = t; w.open(); use(w);"],
  ])("lets %s pass, so that a flag is the rule's", async (_case, body) => {
    for (const file of TYPED_LAYERS) {
      expect(await ids(typedBody(body), file), file).toEqual([]);
    }
  });

  it("lets a type position that names a page object pass the type rule (the name rules, a layer of their own, refuse `typeof window`)", async () => {
    expect(await ids(typedBody("type W = typeof window; use(null as unknown as W);"), "src/probe.tsx")).not.toContain("aibi/page-objects");
    expect(await ids(typedBody("type N = typeof navigator; type L = Location; use(null as unknown as [N, L]);"), "src/probe.tsx")).not.toContain("aibi/page-objects");
  });

  it("lets a non-null assertion on a page object pass the type rule (the name rules, a layer of their own, do not)", async () => {
    expect(await ids(typedBody("window!.addEventListener('x', () => undefined);"), "src/probe.tsx")).not.toContain("aibi/page-objects");
  });

  it("is a rule that needs type information: it is not silent without", async () => {
    // The rule throws without parser services; every cell above ran with them.
    const messages = (await eslint.lintText(typedBody("use(window.navigator);"), { filePath: path.join(WEB, "src/probe.tsx") }))[0]?.messages ?? [];
    expect(messages.some((message) => message.ruleId === "aibi/page-objects")).toBe(true);
  });
});

const URL_ONE = 'export function forgetAddress(): void {\n  history.replaceState(null, "", "/curate");\n}\n';
const URL_CALL = 'history.replaceState(null, "", "/curate");';
const urlWithin = (body: string): string => `export function forgetAddress(): void {\n  ${body}\n}\n`;

/** Each variant of the file that must be refused. */
const URL_REFUSED: readonly (readonly [string, string])[] = [
    ["another path", urlWithin('history.replaceState(null, "", "/curate#x");')],
    ["another path, a query", urlWithin('history.replaceState(null, "", "/curate?x");')],
    ["a built path", urlWithin('history.replaceState(null, "", "/cur" + "ate");')],
    ["a template path", urlWithin("history.replaceState(null, \"\", `/curate`);")],
    ["a binding for the path, in the file", `const CURATE_PATH = "/curate";\n${urlWithin("history.replaceState(null, '', CURATE_PATH);")}`],
    ["a binding for the path, a parameter", 'export function forgetAddress(path: string): void {\n  history.replaceState(null, "", path);\n}\n'],
    ["a binding for the path, a parameter with a default", 'export function forgetAddress(path = "/curate"): void {\n  history.replaceState(null, "", path);\n}\n'],
    ["a binding for the path, a let reassigned", urlWithin('let path = "/curate"; path = "/curate#x"; history.replaceState(null, "", path);')],
    ["a binding for the path, destructured", `const { path } = { path: "/curate" };\n${urlWithin("history.replaceState(null, '', path);")}`],
    ["a binding for the path, imported", `import { path } from "./path";\n${urlWithin("history.replaceState(null, '', path);")}`],
    ["a binding for the path, exported", `export const CURATE_PATH = "/curate";\n${URL_ONE}`],
    ["a state", urlWithin('history.replaceState({ t: 1 }, "", "/curate");')],
    ["undefined for its state", urlWithin('history.replaceState(undefined, "", "/curate");')],
    ["a title", urlWithin('history.replaceState(null, "x", "/curate");')],
    ["a fourth argument", urlWithin('history.replaceState(null, "", "/curate", 1);')],
    ["no argument", urlWithin("history.replaceState();")],
    ["two calls", urlWithin(`${URL_CALL}\n  ${URL_CALL}`)],
    ["two functions with a call each", `${URL_ONE}export function second(): void {\n  ${URL_CALL}\n}\n`],
    ["a second statement", urlWithin(`${URL_CALL}\n  void 0;`)],
    ["the call at the top of the module", `${URL_CALL}\n${URL_ONE}`],
    ["an arrow function", `export const forgetAddress = (): void => {\n  ${URL_CALL}\n};\n`],
    ["another name", URL_ONE.replace("forgetAddress", "go")],
    ["not exported", URL_ONE.replace("export ", "")],
    ["a default export", URL_ONE.replace("export ", "export default ")],
    ["an async function", URL_ONE.replace("export function", "export async function")],
    ["a generator", URL_ONE.replace("export function", "export function*")],
    ["a nested function", urlWithin(`(() => ${URL_CALL.slice(0, -1)})();`)],
    ["a shadowed history, a local", urlWithin(`const history = window.history;\n  ${URL_CALL}`)],
    ["a shadowed history, a parameter", 'export function forgetAddress(history: History): void {\n  history.replaceState(null, "", "/curate");\n}\n'],
    ["a shadowed history, a let", `let history = window.history;\n${URL_ONE}`],
    ["a shadowed history, an import", `import { history } from "./h";\n${URL_ONE}`],
    ["a shadowed history, destructured", `const { history } = window;\n${URL_ONE}`],
    ["window.history", urlWithin('window.history.replaceState(null, "", "/curate");')],
    ["a computed name", urlWithin('history["replaceState"](null, "", "/curate");')],
    ["a held function", urlWithin("void history.replaceState;")],
    ["pushState", urlWithin('history.pushState(null, "", "/curate");')],
    ["pushState by a string", urlWithin('history["pushState"](null, "", "/curate");')],
    ["a string naming replaceState, on any object", urlWithin('void ({} as Record<string, unknown>)["replaceState"];')],
    ["location.replace", urlWithin('location.replace("/curate");')],
    ["location.hash", urlWithin('location.hash = "";')],
    ["an import", `import { x } from "./x";\n${URL_ONE}`],
    ["a let", `let kept = "";\n${URL_ONE}`],
    ["an export of history", `export { history };\n${URL_ONE}`],
    ["two functions of the same name", `${URL_ONE}${URL_ONE}`],
    ["a parameter that is never used", URL_ONE.replace("forgetAddress()", "forgetAddress(unused: number)")],
    ["no call, a return", 'export function forgetAddress(): void {\n  return;\n}\n'],
    ["no call, a read", 'export function forgetAddress(): void {\n  void history.length;\n}\n'],
];

describe("history.replaceState stands in src/operator/url.ts alone, in its one call, the path a literal (D412, M1)", () => {
  const URL_FILE = "src/operator/url.ts";
  const ONE = URL_ONE;

  it("accepts the one function and its one call there", async () => {
    expect(await ids(ONE, URL_FILE)).toEqual([]);
  });

  it.each(["src/probe.tsx", "src/api/probe.ts", "src/operator/probe.tsx", "src/harness/probe.tsx", "src/api/client.ts", "src/operator/Shell.tsx"])("refuses the same function in %s", async (file) => {
    expect(await flagged(ONE, file)).toBe(true);
  });

  it.each(URL_REFUSED)("refuses %s there", async (_case, text) => {
    expect(await flagged(text, URL_FILE)).toBe(true);
  });
});

describe("each layer of the lint holds src/operator/url.ts on its own (M1, round 2)", () => {
  /** What only the file's shape (the syntax layer) refuses: the type rule has no claim on these. */
  const SHAPE_ONLY = new Set([
    "a binding for the path, exported",
    "a second statement",
    "an async function",
    "a generator",
    "a shadowed history, an import",
    "a computed name",
    "a string naming replaceState, on any object",
    "a parameter that is never used",
    "no call, a return",
    "no call, a read",
    "an import",
    "a let",
    "an export of history",
  ]);
  /** What only the type rule refuses: the syntax layer reads the call, not where it stands. */
  const TYPE_ONLY = new Set(["a nested function"]);
  const file = path.join(WEB, "src/operator/url.ts");

  it("refuses each variant by the syntax layer alone, but a call that stands in a nested function", async () => {
    const syntaxOnly = without({ "aibi/page-objects": "off" });
    const got: Record<string, boolean> = {};
    for (const [name, text] of URL_REFUSED) {
      got[name] = ((await syntaxOnly.lintText(text, { filePath: file }))[0]?.messages ?? []).some((message) => message.ruleId === "no-restricted-syntax");
    }
    expect(Object.fromEntries(URL_REFUSED.map(([name]) => [name, !TYPE_ONLY.has(name)]))).toEqual(got);
  });

  it("allows nothing in another file: the type rule alone, with no option, refuses the function, whatever it is called", async () => {
    const typeOnly = without({ "no-restricted-syntax": "off" }, ["src/probe.tsx", "src/operator/probe.tsx", "src/api/probe.ts"]);
    for (const file of ["src/probe.tsx", "src/operator/probe.tsx", "src/api/probe.ts"]) {
      for (const text of [URL_ONE, 'export default function () {\n  history.replaceState(null, "", "/curate");\n}\n', 'export function other(): void {\n  history.replaceState(null, "", "/curate");\n}\n']) {
        const messages = (await typeOnly.lintText(text, { filePath: path.join(WEB, file) }))[0]?.messages ?? [];
        expect(messages.some((message) => message.ruleId === "aibi/page-objects"), `${file}: ${text}`).toBe(true);
      }
    }
  });

  it("refuses each variant by the type rule alone, but the shapes of the file, and accepts the one function", async () => {
    const typeOnly = without({ "no-restricted-syntax": "off" });
    const got: Record<string, boolean> = {};
    for (const [name, text] of URL_REFUSED) {
      got[name] = ((await typeOnly.lintText(text, { filePath: file }))[0]?.messages ?? []).some((message) => message.ruleId === "aibi/page-objects");
    }
    expect(Object.fromEntries(URL_REFUSED.map(([name]) => [name, !SHAPE_ONLY.has(name)]))).toEqual(got);
    expect(((await typeOnly.lintText(URL_ONE, { filePath: file }))[0]?.messages ?? []).filter((message) => message.ruleId === "aibi/page-objects")).toEqual([]);
    expect(((await without({ "aibi/page-objects": "off" }).lintText(URL_ONE, { filePath: file }))[0]?.messages ?? []).filter((message) => message.ruleId === "no-restricted-syntax")).toEqual([]);
  });
});

describe("no mutable module binding in src/operator/ (m4)", () => {
  it.each(["src/operator/probe.tsx", "src/operator/Shell.tsx", "src/operator/main.tsx"])("refuses a module-level let or var in %s", async (file) => {
    for (const code of [
      "let kept = '';\nexport const f = (t: string) => { kept = t; };",
      "var kept = '';\nexport const f = (t: string) => { kept = t; };",
      "export let kept = '';\nexport const f = (t: string) => { kept = t; };",
      "export var kept = '';\nexport const f = (t: string) => { kept = t; };",
      "let a = 1, b = 2;\nexport const f = () => a + b;",
      "let late: string | undefined;\nexport const f = (t: string) => { late = t; };",
    ]) {
      expect(await flagged(code, file), code).toBe(true);
    }
  });

  it.each(["src/operator/probe.tsx", "src/operator/Shell.tsx"])("lets %s hold a const and a let inside a function", async (file) => {
    for (const code of [
      "const kept = 'x';\nexport const f = () => kept;",
      "export const f = (t: string) => { let kept = t; kept += 'x'; return kept; };",
      "export const f = (t: string[]) => { for (let i = 0; i < t.length; i += 1) { void i; } };",
      "export function g(t: string) { var kept = t; return kept; }",
    ]) {
      expect(await ids(code, file), code).toEqual([]);
    }
  });

  it.each(["src/probe.tsx", "src/api/probe.ts", "src/harness/probe.tsx", "src/catalogue/probe.tsx"])(
    "does not widen to %s (src/api/ has module-level lets of its own: decode.ts, box.ts)",
    async (file) => {
      expect(await ids("let kept = '';\nexport const f = (t: string) => { kept = t; };", file)).toEqual([]);
    },
  );
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
