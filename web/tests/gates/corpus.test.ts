/**
 * The acceptance corpus of the number box (D419; probe P9 of the plan): every route by which a
 * response number could reach a component as something it can compute with, written as a lazy
 * hand or an accident would write it, must be refused by at least one of: a compile error, a lint
 * error, a run-time throw, or the checker proving the narrowed type `never`. Each route names the
 * refusals it meets, and each is checked: the lint by `ESLint.lintText` on a file of `src/` with
 * the package's configuration, the compiler by one program of all the routes under the app's
 * options (`program.ts`), the throws by running the conversion on a real box. A route that loses
 * every refusal it names fails here, so a rule, an option or a throw cannot be dropped silently.
 */
import path from "node:path";

import { ESLint } from "eslint";
import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import { box, OPAQUE, type ServerNumber } from "../../src/api/box";
import { check, nodeAfter, type Checked, WEB } from "./program";

const eslint = new ESLint({
  cwd: WEB,
  overrideConfig: {
    languageOptions: { parserOptions: { projectService: { allowDefaultProject: ["src/*.tsx", "src/probe.ts", "src/api/probe.ts", "src/harness/probe.ts", "scripts/probe.ts", "shared/probe.ts"], defaultProject: "tsconfig.app.json" } } },
  },
});

/** The rule ids that flag `code` as a file of `src/` (or of `file`), with no fatal message. */
async function lintIds(code: string, file = "src/probe.tsx"): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: path.join(WEB, file) });
  const messages = result?.messages ?? [];
  expect(messages.filter((message) => message.fatal === true).map((message) => message.message)).toEqual([]);
  return messages.map((message) => message.ruleId ?? "inline-configuration");
}

const HEAD = `import type { components, ServerNumber, JsonOut } from "./api";
type S = components["schemas"];
declare const n: ServerNumber;
declare const m: ServerNumber;
declare const maybe: ServerNumber | null;
declare const env: S["ResultEnvelope"];
declare const hits: S["CatalogHits"];
declare const out: JsonOut;
declare const input: HTMLInputElement;
`;

interface Route {
  readonly id: string;
  readonly code: string;
  /** Rule ids the lint reports (each must be among the findings). */
  readonly lint?: readonly string[];
  /** Diagnostic codes the compiler reports (each must be among them). */
  readonly tsc?: readonly number[];
  /** A marker after which the checker must find a node of type `never`. */
  readonly never?: string;
  /** A `.ts` file, not `.tsx` (an angle-bracket assertion is JSX in `.tsx`). */
  readonly ts?: boolean;
  /** The run-time throw: a conversion of a real box that must throw `OPAQUE`. */
  readonly throws?: (n: ServerNumber, m: ServerNumber) => unknown;
}

const SYNTAX = "no-restricted-syntax";
const GLOBALS = "no-restricted-globals";

/** The run-time cells convert a real box as the routes do; the casts are the point. */
const asNumber = (value: ServerNumber): number => value as unknown as number;
const asString = (value: ServerNumber): string => value as unknown as string;

/** The routes: the round 1 and round 2 reviews' (A3, E1, m-b1, m-b5, m-b6) and their kin. */
const ROUTES: readonly Route[] = [
  // round 1's twelve and round 3's
  { id: "BigInt over a box", code: "export const f = () => BigInt(n);", lint: [SYNTAX, GLOBALS], tsc: [2345], throws: (a) => BigInt(asNumber(a)) },
  { id: "Number.parseInt", code: "export const f = () => Number.parseInt(String(n));", lint: [SYNTAX, GLOBALS], throws: (a) => Number.parseInt(String(a)) },
  { id: "Number.parseFloat", code: "export const f = () => Number.parseFloat(`${n}`);", lint: [SYNTAX, GLOBALS, "@typescript-eslint/restrict-template-expressions"], throws: (a) => Number.parseFloat(`x${asString(a)}`) },
  { id: "globalThis.parseInt", code: "export const f = () => globalThis.parseInt(n as unknown as string);", lint: [SYNTAX], throws: (a) => globalThis.parseInt(asString(a)) },
  { id: "BigInt(JSON.stringify(...))", code: "export const f = () => BigInt(JSON.stringify(n));", lint: [SYNTAX, GLOBALS], throws: (a) => BigInt(JSON.stringify(a)) },
  { id: "a - b on boxes", code: "export const f = () => n - m;", tsc: [2362, 2363], throws: (a, b) => asNumber(a) - asNumber(b) },
  { id: "[s].map(Number)", code: "export const f = () => [n].map(Number);", lint: [SYNTAX, GLOBALS], throws: (a) => [a].map(Number) },
  { id: "fetch().json()", code: "export const f = async () => (await fetch('/api/health')).json() as Promise<unknown>;", lint: [SYNTAX, GLOBALS, "@typescript-eslint/consistent-type-assertions"] },
  { id: "new Response(t).json()", code: "export const f = (t: string) => new Response(t).json();", lint: [SYNTAX, GLOBALS] },
  { id: "Object.assign", code: "export const f = () => Object.assign({}, env);", lint: ["no-restricted-properties"] },
  { id: "input.valueAsNumber", code: "export const f = () => input.valueAsNumber;", lint: [SYNTAX] },
  { id: "Reflect.apply(Math.floor, ...)", code: "export const f = () => Reflect.apply(Math.floor, undefined, [n]);", lint: [SYNTAX, GLOBALS], throws: (a): unknown => Reflect.apply(Math.floor, undefined, [a]) as unknown },
  { id: "Number.call", code: "export const f = () => Number.call(undefined, n);", lint: [SYNTAX, GLOBALS], throws: (a) => Number.call(undefined, a) },
  { id: "typeof narrowing", code: "export const f = (v: ServerNumber) => (typeof v === 'number' ? /*never*/v : 0);", never: "/*never*/" },
  {
    id: "an inline eslint-disable",
    code: "// eslint-disable-next-line no-restricted-syntax, no-restricted-globals\nexport const f = () => Number(n);",
    lint: [SYNTAX, GLOBALS, "inline-configuration"],
    throws: (a) => Number(a),
  },
  { id: "a JSON round trip", code: "export const f = (): unknown => JSON.parse(JSON.stringify(env));", lint: [SYNTAX, GLOBALS], throws: (a) => JSON.parse(JSON.stringify({ a })) as unknown },
  // m-b5: aliases, by class
  { id: "document.defaultView?.Number", code: "export const f = () => document.defaultView?.Number('12');", lint: [SYNTAX] },
  { id: "globalThis.self.Number", code: "export const f = () => globalThis.self.Number('3');", lint: [SYNTAX] },
  { id: "window.String", code: "export const f = () => window.String(n);", lint: [SYNTAX], throws: (a) => globalThis.String(a) },
  { id: "globalThis['Number']", code: "export const f = () => globalThis['Number'];", lint: [SYNTAX] },
  { id: "const j = JSON", code: "const j = JSON;\nexport const f = (t: string): unknown => j.parse(t);\nexport const g = () => j.stringify(n);", lint: [SYNTAX, GLOBALS] },
  { id: "const { parseInt } = Number", code: "const { parseInt } = Number;\nexport const f = (t: string) => parseInt(t);", lint: [SYNTAX, GLOBALS] },
  { id: "const { parseInt: p } = globalThis", code: "const { parseInt: p } = globalThis;\nexport const f = (t: string) => p(t);", lint: [SYNTAX] },
  { id: "const { 'JSON': j } = globalThis", code: "const { 'JSON': j } = globalThis;\nexport const f = (t: string): unknown => j.parse(t);", lint: [SYNTAX] },
  { id: "const m = Math", code: "const k = Math;\nexport const f = () => k.floor(1);", lint: [SYNTAX, GLOBALS] },
  { id: "window.JSON.parse", code: "export const f = (t: string): unknown => window.JSON.parse(t);", lint: [SYNTAX] },
  { id: "globalThis.Object.assign", code: "export const f = () => globalThis.Object.assign({}, env);", lint: [SYNTAX] },
  { id: "a template computed key", code: "export const f = () => globalThis[`Number`];", lint: [SYNTAX] },
  // m-b1: a boxed 0 is an object, so truthy
  { id: "a5: maybe ? :", code: "export const f = () => (maybe ? 'shown' : 'none');", lint: ["@typescript-eslint/strict-boolean-expressions"] },
  { id: "a5: maybe && <p/>", code: "export const f = () => <div>{maybe && <p>shown</p>}</div>;", lint: ["@typescript-eslint/strict-boolean-expressions"] },
  { id: "a5: hidden={n}", code: "export const f = () => <p hidden={n}>x</p>;", tsc: [2322] },
  { id: "a5: if (n)", code: "export const f = () => { if (n) { return 1; } return 0; };", lint: ["@typescript-eslint/strict-boolean-expressions"] },
  { id: "a5: !maybe", code: "export const f = () => !maybe;", lint: ["@typescript-eslint/strict-boolean-expressions"] },
  // the round 2 review's survivors of the first lint, and their kin
  { id: "Reflect.get(globalThis, built)", code: "export const f = () => Reflect.get(globalThis, 'Num' + 'ber') as unknown;", lint: [SYNTAX, GLOBALS] },
  { id: "(0).constructor('12')", code: "export const f = (): unknown => (0).constructor('12');", lint: [SYNTAX, "@typescript-eslint/no-unsafe-call"] },
  { id: "n.constructor", code: "export const f = () => n.constructor;", lint: [SYNTAX] },
  { id: "unary plus", code: "export const f = () => +n;", lint: [SYNTAX], throws: (a) => +asString(a) },
  { id: "unary minus", code: "export const f = () => -n;", lint: ["@typescript-eslint/no-unsafe-unary-minus"], throws: (a) => -asNumber(a) },
  { id: "double tilde", code: "export const f = () => ~~n;", lint: ["no-bitwise"], throws: (a) => ~~asNumber(a) },
  { id: "| 0", code: "export const f = () => n | 0;", lint: ["no-bitwise"], tsc: [2362], throws: (a) => asNumber(a) | 0 },
  { id: "a template", code: "export const f = () => `${n}`;", lint: ["@typescript-eslint/restrict-template-expressions"], throws: (a) => `x${asString(a)}` },
  { id: "'' + n", code: "export const f = () => '' + n;", lint: ["@typescript-eslint/restrict-plus-operands"], throws: (a) => "x" + asString(a) },
  { id: "n + 1", code: "export const f = () => n + 1;", tsc: [2365], throws: (a) => asNumber(a) + 1 },
  { id: "n.toString()", code: "export const f = () => n.toString();", throws: (a) => a.toString() },
  { id: "n.valueOf()", code: "export const f = () => n.valueOf();", throws: (a) => a.valueOf() },
  { id: "n.toFixed(2)", code: "export const f = () => n.toFixed(2);", tsc: [2339] },
  { id: "n.toLocaleString()", code: "export const f = () => n.toLocaleString();", throws: (a) => a.toLocaleString() },
  { id: "String(n)", code: "export const f = () => String(n);", lint: [SYNTAX, GLOBALS], throws: (a) => String(a) },
  { id: "Intl.NumberFormat().format(n)", code: "export const f = () => new Intl.NumberFormat().format(n);", tsc: [2769], throws: (a) => new Intl.NumberFormat().format(asNumber(a)) },
  { id: "Math.round(n)", code: "export const f = () => Math.round(n);", lint: [SYNTAX, GLOBALS], tsc: [2345], throws: (a) => Math.round(asNumber(a)) },
  { id: "isNaN(n)", code: "export const f = () => isNaN(n);", lint: [SYNTAX, GLOBALS], tsc: [2345], throws: (a) => isNaN(asNumber(a)) },
  { id: "isFinite(n)", code: "export const f = () => isFinite(n);", lint: [SYNTAX, GLOBALS], tsc: [2345], throws: (a) => isFinite(asNumber(a)) },
  { id: "parseFloat(n)", code: "export const f = () => parseFloat(n);", lint: [SYNTAX, GLOBALS], tsc: [2345], throws: (a) => parseFloat(asString(a)) },
  { id: "new Date(n)", code: "export const f = () => new Date(n);", tsc: [2769], throws: (a) => new Date(asNumber(a)) },
  { id: "Array.from({ length: n })", code: "export const f = () => Array.from({ length: n });", tsc: [2769], throws: (a) => Array.from({ length: asNumber(a) }) },
  { id: "n == 0", code: "export const f = () => n == 0;", tsc: [2367], throws: (a) => asNumber(a) == 0 },
  { id: "total > shown", code: "export const f = () => n > m;", throws: (a, b) => a > b },
  { id: "a sort comparator", code: "export const f = () => [n, m].sort((x, y) => (x < y ? -1 : 1));", throws: (a, b) => [a, b].sort((x, y) => (x < y ? -1 : 1)) },
  { id: "a number from the response type", code: "export const f = (): number => hits.next_offset ?? 0;", tsc: [2322] },
  { id: "a string from the response type", code: "export const f = (): string => env.cohorts[0]?.position ?? '';", tsc: [2322] },
  { id: "a JsonOut leaf as a number", code: "export const f = (): number => (typeof out === 'number' ? /*never*/out : 0);", never: "/*never*/" },
  { id: "a type assertion", code: "export const f = () => n as unknown as number;", lint: ["@typescript-eslint/consistent-type-assertions"] },
  { id: "an angle-bracket assertion", code: "export const f = () => <number>(<unknown>n);", lint: ["@typescript-eslint/consistent-type-assertions"], ts: true },
  // m-b6: builtins patched
  { id: "RegExp.prototype.test patched", code: "RegExp.prototype.test = () => true;", lint: [SYNTAX, "no-extend-native"] },
  { id: "JSON.rawJSON patched", code: "Object.defineProperty(JSON, 'rawJSON', { value: (t: string) => t });", lint: [SYNTAX, GLOBALS, "no-restricted-properties"] },
  { id: "Object.defineProperty on a prototype", code: "Object.defineProperty(RegExp.prototype, 'test', { value: () => true });", lint: ["no-extend-native", "no-restricted-properties"] },
  { id: "WeakMap.prototype.get patched", code: "WeakMap.prototype.get = () => undefined;", lint: [SYNTAX, "no-extend-native"] },
  { id: "a member of the global object assigned", code: "globalThis.fetch = () => Promise.reject(new Error('x'));", lint: [SYNTAX] },
  { id: "delete of a builtin's member", code: "delete (Math as { floor?: unknown }).floor;", lint: [SYNTAX, GLOBALS] },
  { id: "Object.setPrototypeOf", code: "export const f = (o: object) => Object.setPrototypeOf(o, null) as unknown;", lint: ["no-restricted-properties"] },
  { id: "__proto__", code: "export const f = (o: { __proto__: unknown }) => o.__proto__;", lint: ["no-restricted-properties", "no-proto"] },
  { id: "__proto__ assigned", code: "export const f = (o: { __proto__: unknown }) => { o.__proto__ = null; };", lint: [SYNTAX, "no-restricted-properties", "no-proto"] },
  { id: "Object aliased", code: "const O = Object;\nexport const f = () => O.assign({}, env);", lint: [SYNTAX] },
  { id: "assign destructured from an alias", code: "const { assign } = globalThis.Object;\nexport const f = () => assign({}, env);", lint: [SYNTAX] },
  // the box's internals, from outside src/api
  { id: "textOf imported", code: "import { textOf } from './api/box';\nexport const f = () => textOf(n);", lint: ["@typescript-eslint/no-restricted-imports"] },
  { id: "the box imported as a namespace", code: "import * as B from './api/box';\nexport const f = () => B.textOf(n);", lint: ["@typescript-eslint/no-restricted-imports"] },
  { id: "the box re-exported", code: "export { textOf } from './api/box';", lint: ["@typescript-eslint/no-restricted-imports"] },
  { id: "the decoder imported", code: "import { decode } from './api/decode';\nexport const f = (t: string) => decode(t);", lint: ["@typescript-eslint/no-restricted-imports"] },
  { id: "the box imported dynamically", code: "export const f = async () => (await import('./api/box')).textOf(n);", lint: [SYNTAX] },
  { id: "a module named at run time", code: "export const f = async (p: string): Promise<unknown> => import(p);", lint: [SYNTAX] },
  { id: "a type imported past the index", code: "import type { JsonIn } from './api/json';\nexport const f = (v: JsonIn) => v;", lint: ["@typescript-eslint/no-restricted-imports"] },
  // MA1: modules reached without an import statement the source names (the build's gate holds the graph)
  { id: "import.meta.glob of the box, eager", code: "export const f = () => import.meta.glob('./api/box.ts', { eager: true, import: 'textOf' });", lint: [SYNTAX] },
  { id: "import.meta.glob of the box, lazy", code: "export const f = () => import.meta.glob('./api/box.ts');", lint: [SYNTAX] },
  { id: "import.meta.glob with a type argument", code: "export const f = () => import.meta.glob<(n: ServerNumber) => string>('./api/box.ts', { eager: true, import: 'textOf' });", lint: [SYNTAX] },
  { id: "import.meta['glob']", code: "export const f = () => import.meta['glob']('./api/box.ts');", lint: [SYNTAX] },
  { id: "import.meta.glob called optionally", code: "export const f = () => import.meta.glob?.('./api/*.ts');", lint: [SYNTAX] },
  { id: "a JSON module of the API imported", code: "export const f = () => import('/api/health', { with: { type: 'json' } });", lint: [SYNTAX] },
  { id: "a JSON module of the operator imported", code: "export const f = () => import('/operator/csrf', { with: { type: 'json' } });", lint: [SYNTAX] },
  { id: "a JSON module imported statically", code: "import data from './x.json' with { type: 'json' };\nexport const f = () => data;", lint: [SYNTAX], tsc: [2307] },
  // m1: what the seal does not reach (another realm's readers, the Cache API, workers)
  { id: "the Cache API", code: "export const f = async () => (await (await caches.open('x')).match('/api/health'))?.body;", lint: [SYNTAX, GLOBALS] },
  { id: "CacheStorage", code: "export const f = (x: unknown) => x instanceof CacheStorage;", lint: [SYNTAX, GLOBALS] },
  { id: "navigator.serviceWorker", code: "export const f = () => navigator.serviceWorker.register('/assets/sw.js');", lint: [SYNTAX] },
  { id: "an iframe's fetch", code: "export const f = (i: HTMLIFrameElement) => i.contentWindow?.['fetch']('/api/health');", lint: [SYNTAX] },
  // m4: the prototype chain, by alias
  { id: "a prototype aliased and patched", code: "export const f = (g: () => string) => { const p = TextDecoder.prototype; p.decode = g; };", lint: [SYNTAX] },
  { id: "TextDecoder.prototype.decode patched", code: "export const f = (g: () => string) => { TextDecoder.prototype.decode = g; };", lint: [SYNTAX] },
  { id: "getPrototypeOf patched", code: "export const f = (g: () => string) => { Object.getPrototypeOf(new TextDecoder()).decode = g; };", lint: [SYNTAX] },
  { id: "getPrototypeOf destructured", code: "const { getPrototypeOf } = Object;\nexport const f = () => getPrototypeOf(n);", lint: [SYNTAX] },
  { id: "the prototype by a computed name", code: "export const f = () => Object['getPrototypeOf'](n);", lint: [SYNTAX] },
  { id: "a prototype's constructor looked up", code: "export const f = () => Object.getOwnPropertyDescriptor(Object.getPrototypeOf(n), 'constructor');", lint: [SYNTAX] },
  { id: "a prototype set by an object literal", code: "export const f = () => ({ __proto__: null });", lint: [SYNTAX] },
  { id: "setPrototypeOf as a key", code: "export const f = (g: () => void) => ({ setPrototypeOf: g });", lint: [SYNTAX] },
  { id: "a prototype by a computed name", code: "export const f = () => Response['prototype'];", lint: [SYNTAX, GLOBALS] },
  // m8: a type the compiler takes on trust
  { id: "a user-defined type predicate", code: "function isNum(v: unknown): v is number { return v !== null; }\nexport const f = () => (isNum(n) ? n + 1 : 0);", lint: [SYNTAX] },
  { id: "an assertion function", code: "function assertNum(v: unknown): asserts v is number { if (v === null) { throw new Error('x'); } }\nexport const f = () => { assertNum(n); return n + 1; };", lint: [SYNTAX] },
  // m7: the oracle's counter is the harness's
  { id: "decodedCount from the index", code: "import { decodedCount } from './api';\nexport const f = () => decodedCount();", tsc: [2305] },
  { id: "decodedCount from oracle.ts outside the harness", code: "import { decodedCount } from './api/oracle';\nexport const f = () => decodedCount();", lint: ["@typescript-eslint/no-restricted-imports"] },
  // m-H: an import of src/api/ by a path that only starts like the index
  { id: "the box imported through index/..", code: "import { textOf } from './api/index/../box';\nexport const f = () => textOf(n);", lint: ["@typescript-eslint/no-restricted-imports"] },
  { id: "a module named like the index", code: "import { x } from './api/indexer';\nexport const f = () => x;", lint: ["@typescript-eslint/no-restricted-imports"] },
  { id: "the index's sibling by an extension", code: "import { decode } from './api/index.tsx/../decode.ts';\nexport const f = decode;", lint: ["@typescript-eslint/no-restricted-imports"] },
  // MA1-r2: a module the lint does not read is no part of the bundle; an ambient module would let the checker accept one
  { id: "a wildcard ambient module", code: "declare module '*.JS' { export function difference(): Promise<string>; }", lint: [SYNTAX], ts: true },
  { id: "an ambient module of a name", code: "declare module './digits.JS' { export const d: string; }", lint: [SYNTAX], ts: true },
  { id: "an ambient module with a body-less declaration", code: "declare module '*.es6';", lint: [SYNTAX], ts: true },
  // m-A: what the lint does see of a name written out, on an alias of the global object too
  { id: "an alias of window, a name written out", code: "export const f = () => { const w = window; return w.fetch('/api/health'); };", lint: [SYNTAX] },
  { id: "an alias of window, a name written as a string", code: "export const f = () => { const w = window; return w['fetch']('/api/health'); };", lint: [SYNTAX] },
  { id: "a destructuring of the global object", code: "export const f = () => { const { fetch: g } = globalThis; return g('/api/health'); };", lint: [SYNTAX] },
  { id: "Number through an alias of globalThis", code: "export const f = () => { const g = globalThis; return g.Number('1'); };", lint: [SYNTAX] },
  { id: "caches through navigator by name", code: "export const f = () => navigator['serviceWorker'];", lint: [SYNTAX] },
  // m4: a type taken on trust by a comment
  { id: "@ts-expect-error with a description", code: "// @ts-expect-error the box is a number here, trust me\nexport const x: number = n;", lint: ["@typescript-eslint/ban-ts-comment"] },
  { id: "@ts-ignore", code: "// @ts-ignore\nexport const x: number = n;", lint: ["@typescript-eslint/ban-ts-comment"] },
  // m7: require, in any position
  { id: "declare const require, then a call", code: "declare const require: (path: string) => { textOf: (n: ServerNumber) => string };\nexport const f = () => require('./api/box.ts').textOf(n);", lint: [SYNTAX] },
  { id: "require as a value", code: "declare const require: unknown;\nexport const f = () => [require];", lint: [SYNTAX] },
  { id: "a require call with no declaration", code: "export const f = () => require('./api/box.ts');", lint: [SYNTAX] },
  // m9: the case of an import path (a case-insensitive file system resolves it)
  { id: "the box imported with another case", code: "import { textOf } from './API/Box';\nexport const f = () => textOf(n);", lint: ["@typescript-eslint/no-restricted-imports"] },
  { id: "the decoder imported with another case", code: "import { decode } from './Api/DECODE.ts';\nexport const f = decode;", lint: ["@typescript-eslint/no-restricted-imports"] },
  { id: "the box imported dynamically with another case", code: "export const f = async () => (await import('./API/box')).textOf(n);", lint: [SYNTAX] },
  // I/O
  { id: "XMLHttpRequest", code: "export const f = () => new XMLHttpRequest();", lint: [SYNTAX, GLOBALS] },
  { id: "EventSource", code: "export const f = () => new EventSource('/api/x');", lint: [SYNTAX, GLOBALS] },
  { id: "WebSocket", code: "export const f = () => new WebSocket('ws://x');", lint: [SYNTAX, GLOBALS] },
  { id: "window.fetch", code: "export const f = () => window.fetch('/api/health');", lint: [SYNTAX] },
  { id: "a json method destructured", code: "export const f = (r: { json(): unknown }) => { const { json } = r; return json; };", lint: [SYNTAX] },
  { id: "a json method by a computed name", code: "export const f = (r: { json(): unknown }) => r['json']();", lint: [SYNTAX] },
];

let checked: Checked;

const fileOf = (route: Route, index: number): string => `src/probe-${String(index)}.${route.ts === true ? "ts" : "tsx"}`;

beforeAll(() => {
  checked = check(Object.fromEntries(ROUTES.map((route, index) => [fileOf(route, index), `${HEAD}${route.code}\n`])));
}, 120_000);

describe("every launder route is refused", () => {
  it.each(ROUTES.map((route, index) => [route.id, route, index] as const))("%s", async (_id, route, index) => {
    const file = fileOf(route, index);
    const refusals: string[] = [];
    if (route.lint !== undefined) {
      const ids = await lintIds(`${HEAD}${route.code}\n`, route.ts === true ? "src/probe.ts" : "src/probe.tsx");
      expect(ids).toEqual(expect.arrayContaining([...route.lint]));
      refusals.push("lint");
    }
    const codes = checked.codes(file);
    if (route.tsc !== undefined) {
      expect(codes).toEqual(expect.arrayContaining([...route.tsc]));
      refusals.push("tsc");
    }
    if (route.never !== undefined) {
      const node = nodeAfter(checked, file, route.never);
      expect(checked.checker.typeToString(checked.checker.getTypeAtLocation(node))).toBe("never");
      expect(checked.checker.getTypeAtLocation(node).flags & ts.TypeFlags.Never).not.toBe(0);
      refusals.push("never");
    }
    if (route.throws !== undefined) {
      const run = route.throws;
      expect(() => run(box("137"), box("1.10"))).toThrow(OPAQUE);
      refusals.push("throws");
    }
    expect(refusals.length).toBeGreaterThan(0);
  });
});

/** Ordinary code over every declaration of `HEAD`: no finding and no error, so that each one a
 * route meets is the route's. */
const CONTROL = `${HEAD}export const f = () => [env.digest, hits.next_offset === undefined, n === m, Object.keys(env).length, out, input.value];
export const g = () => <p hidden={maybe === null}>shown</p>;`;

describe("the corpus's own controls", () => {
  it("compiles ordinary code over the response types with no error, so a code is the route's", () => {
    const control = check({
      "src/control.tsx": `${CONTROL}\n`,
    });
    expect(control.codes("src/control.tsx")).toEqual([]);
  });

  it("lints ordinary code over the response types with no finding, so a finding is the route's", async () => {
    expect(await lintIds(`${CONTROL}\n`)).toEqual([]);
  });

  it("refuses none of the number rules in the tooling (they are the app's)", async () => {
    const ids = await lintIds("export const f = (t: string) => Number.parseInt(JSON.parse(t) as string) + Math.floor(1);\n", "scripts/probe.ts");
    expect(ids.filter((id) => id === SYNTAX || id === GLOBALS || id === "no-restricted-properties")).toEqual([]);
  });

  it("refuses the number rules in a directory of its own (the app's by default)", async () => {
    const ids = await lintIds("export const f = (t: string) => Number.parseInt(JSON.parse(t) as string) + Math.floor(1);\n", "shared/probe.ts");
    expect(ids).toEqual(expect.arrayContaining([SYNTAX, GLOBALS]));
  });

  it("refuses @ts-nocheck at the top of a file, in the app's layers", async () => {
    for (const file of ["src/probe.ts", "src/api/probe.ts"]) {
      const ids = await lintIds("// @ts-nocheck because this file is trusted\nexport const x = 1;\n", file);
      expect(ids, file).toContain("@typescript-eslint/ban-ts-comment");
    }
  });

  it("lets the harness, and the harness alone, import the oracle's counter", async () => {
    const code = "import { decodedCount } from '../api/oracle';\nexport const f = () => decodedCount();\n";
    expect(await lintIds(code, "src/harness/probe.ts")).not.toContain("@typescript-eslint/no-restricted-imports");
    expect(await lintIds(code.replace("../api/oracle", "../api/box").replace("decodedCount", "textOf"), "src/harness/probe.ts")).toContain(
      "@typescript-eslint/no-restricted-imports",
    );
    expect(await lintIds(code, "src/probe.ts")).toContain("@typescript-eslint/no-restricted-imports");
  });

  it("lets src/api/ convert numbers, but not do I/O outside client.ts", async () => {
    const api = await lintIds("export const f = (t: string): unknown => JSON.parse(String(Number.parseInt(t)));\n", "src/api/probe.ts");
    expect(api.filter((id) => id === SYNTAX || id === GLOBALS)).toEqual([]);
    const bitwise = await lintIds("export const f = (n: number) => n | 0;\n", "src/api/probe.ts");
    expect(bitwise).not.toContain("no-bitwise");
    expect(await lintIds("export const f = (n: number) => n | 0;\n", "src/probe.ts")).toContain("no-bitwise");
    const io = await lintIds("export const f = () => fetch('/api/health');\n", "src/api/probe.ts");
    expect(io).toEqual(expect.arrayContaining([SYNTAX, GLOBALS]));
  });
});
