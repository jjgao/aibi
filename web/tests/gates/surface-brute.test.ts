/**
 * The surface gate (`surface.ts`, the operator client's allow-list) and the index's check
 * (`takers.ts`) held to the compiler over every parameter type of a grammar, up to a bound (round
 * 2 of #115's review: MA2, m9). The oracle is the type checker, never a table of what is
 * dangerous: each type is put into a member of a copy of the client (`curator.ts`'s `Curator`), in
 * each of the forms a member can take a value in, and a file calls each member with a box, the
 * decoder's list and record, a node, a void-typed value (a function typed `() => void` may give
 * anything), a carrier (an object of the type with one more member holding a box: object types are
 * not exact) and the grammar's own witnesses (the box at a declared slot of the type: `{ v: b }`
 * for `{ readonly v: unknown }`). A call that compiles is a way a box reaches the member's body,
 * and each one is then run against a client whose members read what they are given: the box's
 * digits come back.
 *
 * **What is held,** for every member: a box reaches it only if the gate refuses it (no hole); a
 * primitive in a form that takes a value is accepted by the gate and no call reaches it (the
 * allow-list's primitives are sound, by the compiler); anything else is refused (the allow-list);
 * and for every type, `takes` (the index's check) is a taker wherever a box reaches a declared slot
 * (no hole), and not one for a shape of primitives the program declares (it does not cry wolf).
 *
 * **The grammar and its bound** (`BASES`, `WRAPPERS`, `GENERICS`, `FORMS`): the bases (primitives,
 * literals, enums, a template literal, `unknown`, `any`, `object`, `{}`, `Object`, the box, a node,
 * `void`, `never`, `symbol`, a branded string, and library types with an `any` or `unknown` slot);
 * the wrappers (arrays, tuples, read-only and optional members, a second member, index signatures,
 * `Readonly`, `Partial`, `Required`, `Pick`, `Omit`, `Record`, `NonNullable`, a union, an
 * intersection, `Promise`, `ReadonlyMap`, a function returning it, a callback taking it, a getter, a
 * class, an interface, a mapped type and a conditional one); generics (a bare type parameter,
 * constrained, defaulted, under `NonNullable`, `& {}`, `& object`, `Partial`, `Readonly`, a
 * conditional, a mapped type, indexed access); and the forms (a first and a second parameter,
 * `this`, a nested member, a returned function, a getter, a construct signature, an overload, an
 * optional and a rest parameter, a function in a `Promise`, a writable member, a setter, an index
 * signature, a callable object, a method, and a callback's return). Bounded: every base, every
 * wrapper of every base, and every wrapper of every wrapper of the four core bases (`unknown`,
 * `string`, the box, a node) in the first form; every form over every base, every generic, and
 * every wrapper of the core bases. The whole domain at that bound is checked, with no sampling.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";

import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import { isServerNumber, textOf } from "../../src/api/box";
import { decode } from "../../src/api/decode";
import { check, type Checked, WEB } from "./program";
import { BUDGET, primitive, surface } from "./surface";
import { takes } from "./takers";

/** A type of the grammar: its text, whether the allow-list holds it a primitive, its own witnesses
 * (a value with the box at a declared slot), and for a generic its type parameters and the type
 * arguments it is called with. */
interface Term {
  readonly name: string;
  readonly text: string;
  readonly primitive: boolean;
  readonly slots: readonly string[];
  /** Built over a primitive base with the program's own shapes alone (no library class, no
   * function returning `void`, which may give anything): `takes` must not call it a taker. */
  readonly inert: boolean;
  readonly generic?: { readonly parameters: string; readonly arguments: readonly string[] };
}

const base = (name: string, text: string, isPrimitive: boolean, slots: readonly string[] = [], inert = isPrimitive): Term => ({ name, text, primitive: isPrimitive, slots, inert });

/** The bases. */
const BASES: readonly Term[] = [
  base("string", "string", true),
  base("number", "number", true),
  base("boolean", "boolean", true),
  base("bigint", "bigint", true),
  base("undefined", "undefined", true),
  base("null", "null", true),
  base("a string literal", '"a"', true),
  base("a number literal", "1", true),
  base("true", "true", true),
  base("a literal union", '"a" | "b"', true),
  base("a string enum", "Colour", true),
  base("a number enum", "Level", true),
  base("a template literal", "`x${number}`", true),
  base("string | undefined", "string | undefined", true),
  base("unknown", "unknown", false),
  base("any", "any", false),
  base("object", "object", false),
  base("{}", "{}", false),
  base("Object", "Object", false),
  base("the box", "ServerNumber", false),
  base("a node", "JsonOut", false),
  base("void", "void", false),
  base("never", "never", false),
  base("symbol", "symbol", false),
  base("a branded string", 'string & { readonly brand: "id" }', false),
  base("Function", "Function", false),
  base("Error", "Error", false, ['new Error("x", { cause: b })']),
  base("ErrorOptions", "ErrorOptions", false, ["{ cause: b }"]),
  base("AbortSignal", "AbortSignal", false, ["AbortSignal.abort(b)"]),
  base("PromiseRejectedResult", "PromiseRejectedResult", false, ['{ status: "rejected" as const, reason: b }']),
  base("PropertyDescriptor", "PropertyDescriptor", false, ["{ value: b }"]),
  base("CustomEvent", "CustomEvent", false, ['new CustomEvent("x", { detail: b })']),
  base("MessageEvent", "MessageEvent", false, ['new MessageEvent("x", { data: b })']),
  base("IteratorResult<unknown>", "IteratorResult<unknown>", false, ["{ value: b }"]),
  base("WeakRef<object>", "WeakRef<object>", false, ["new WeakRef(b)"]),
  base("ReadonlySet<unknown>", "ReadonlySet<unknown>", false, ["new Set([b])"]),
  base("Iterable<unknown>", "Iterable<unknown>", false, ["[b]"]),
  base("ArrayLike<unknown>", "ArrayLike<unknown>", false, ["[b]"]),
  base("Event", "Event", false),
  base("Date", "Date", false),
];

/** The four core bases the wrappers are composed over twice. */
const CORE = ["unknown", "string", "the box", "a node"];

/** What a wrapper's inner type is given, besides its own witnesses: the box and a node, which the
 * compiler then admits or not. */
const inner = (term: Term): string[] => [...new Set([...term.slots, "b", "node"])];

/** A wrapper: its name, and the term it makes of a term. */
type Wrapper = readonly [string, (term: Term) => Term];

const wrap = (name: string, text: (inner: string) => string, slot: ((witness: string) => string) | null, options: { primitive?: boolean; library?: boolean } = {}): Wrapper => [
  name,
  (term) => ({
    name: `${name} of ${term.name}`,
    text: text(term.text),
    primitive: options.primitive === true && term.primitive,
    slots: slot === null ? [] : inner(term).map(slot),
    inert: term.inert && options.library !== true,
  }),
];

const WRAPPERS: readonly Wrapper[] = [
  wrap("a list", (x) => `readonly (${x})[]`, (w) => `[${w}]`),
  wrap("a mutable list", (x) => `(${x})[]`, (w) => `[${w}]`),
  wrap("a tuple", (x) => `readonly [string, ${x}]`, (w) => `["s", ${w}] as const`),
  wrap("a member", (x) => `{ readonly v: ${x} }`, (w) => `{ v: ${w} }`),
  wrap("an optional member", (x) => `{ readonly v?: ${x} }`, (w) => `{ v: ${w} }`),
  wrap("a second member", (x) => `{ readonly w: string; readonly v: ${x} }`, (w) => `{ w: "s", v: ${w} }`),
  wrap("an index", (x) => `{ readonly [key: string]: ${x} }`, (w) => `{ k: ${w} }`),
  wrap("a number index", (x) => `{ readonly [key: number]: ${x} }`, (w) => `[${w}]`),
  wrap("Readonly", (x) => `Readonly<{ v: ${x} }>`, (w) => `{ v: ${w} }`),
  wrap("Partial", (x) => `Partial<{ v: ${x} }>`, (w) => `{ v: ${w} }`),
  wrap("Required", (x) => `Required<{ v?: ${x} }>`, (w) => `{ v: ${w} }`),
  wrap("Pick", (x) => `Pick<{ v: ${x}; w: string }, "v">`, (w) => `{ v: ${w} }`),
  wrap("Omit", (x) => `Omit<{ w: string; v: ${x} }, "w">`, (w) => `{ v: ${w} }`),
  wrap("Record of a key", (x) => `Record<"v", ${x}>`, (w) => `{ v: ${w} }`),
  wrap("Record of strings", (x) => `Record<string, ${x}>`, (w) => `{ k: ${w} }`),
  wrap("NonNullable", (x) => `NonNullable<${x}>`, (w) => w),
  wrap("a union with a string", (x) => `${x} | string`, (w) => w, { primitive: true }),
  wrap("an intersection with {}", (x) => `(${x}) & {}`, (w) => w),
  wrap("a Promise", (x) => `Promise<${x}>`, (w) => `Promise.resolve(${w})`, { library: true }),
  wrap("a ReadonlyMap", (x) => `ReadonlyMap<string, ${x}>`, (w) => `new Map([["k", ${w}]])`, { library: true }),
  wrap("a function giving it", (x) => `() => ${x}`, (w) => `() => (${w})`),
  // A callback the caller gives has no declared slot the member can read a box from.
  wrap("a callback taking it", (x) => `(x: ${x}) => void`, null, { library: true }),
  wrap("a getter", (x) => `{ get v(): ${x} }`, (w) => `{ get v() { return ${w}; } }`),
  wrap("a class", (x) => `Holder<${x}>`, (w) => `new Holder(${w})`),
  wrap("an interface", (x) => `Wrap<${x}>`, (w) => `{ v: ${w} }`),
  wrap("a mapped type", (x) => `{ readonly [K in "v"]: ${x} }`, (w) => `{ v: ${w} }`),
  wrap("a conditional type", (x) => `[${x}] extends [never] ? never : ${x}`, (w) => w),
];

/** The generics: a type parameter `T0` under each construction, with the type arguments a call
 * gives it (and a call that infers it). */
const generic = (name: string, parameters: string, text: string, slots: readonly string[] = []): Term => ({
  name,
  text,
  primitive: false,
  slots,
  inert: false,
  generic: { parameters, arguments: ["ServerNumber", "unknown", "JsonOut", "{ readonly v: ServerNumber }", '"a"'] },
});

const GENERICS: readonly Term[] = [
  generic("T", "<T0>", "T0"),
  generic("T extends object", "<T0 extends object>", "T0"),
  generic("T extends the box", "<T0 extends ServerNumber>", "T0"),
  generic("T extends a node", "<T0 extends JsonOut>", "T0"),
  generic("T extends string", "<T0 extends string>", "T0"),
  generic("T extends a literal union", '<T0 extends "a" | "b">', "T0"),
  generic("T = unknown", "<T0 = unknown>", "T0"),
  generic("NonNullable<T>", "<T0>", "NonNullable<T0>"),
  generic("T & {}", "<T0>", "T0 & {}"),
  generic("T & object", "<T0>", "T0 & object"),
  generic("Partial<T>", "<T0>", "Partial<T0>"),
  generic("Readonly<T>", "<T0>", "Readonly<T0>"),
  generic("a conditional over T", "<T0>", "T0 extends number ? never : T0"),
  generic("an identity conditional", "<T0>", "T0 extends unknown ? T0 : never"),
  generic("a mapped type over T", "<T0>", "{ readonly [K in keyof T0]: T0[K] }"),
  generic("T[keyof T]", "<T0>", "T0[keyof T0]", ["{ v: b }"]),
  generic("T[number]", "<T0 extends readonly unknown[]>", "T0[number]"),
  generic("Record<T, unknown>", "<T0 extends string>", "Record<T0, unknown>", ["{ v: b }"]),
  generic("a list of T", "<T0>", "readonly T0[]", ["[b]"]),
  generic("a member of T", "<T0>", "{ readonly v: T0 }", ["{ v: b }"]),
  generic("a function giving T", "<T0>", "() => T0", ["() => b"]),
  generic("a Promise of T", "<T0>", "Promise<T0>", ["Promise.resolve(b)"]),
];

/** A form a member takes a value in: its declaration in `Curator` (`gen` the type parameters), a
 * call with a value (`args` the type arguments), whether the gate accepts a primitive there, and
 * what its body is at run time. */
interface Form {
  readonly name: string;
  readonly declare: (member: string, type: string, gen: string) => string;
  readonly call: (member: string, value: string, args: string) => string;
  /** The type a value is handed at, as the compiler's own type operators give it from the member's
   * type (`Parameters`, `ThisParameterType`, an instantiation expression), with the declarations it
   * stands on; `null` where no operator gives it (a setter's), whose calls are compiled instead. */
  readonly place: ((member: string, args: string) => { readonly helper?: string; readonly type: string }) | null;
  /** How the value is written at the call (a callback's return is written in a function). */
  readonly given?: (value: string) => string;
  readonly accepts: boolean;
  readonly generic: boolean;
  readonly body: (report: (value: unknown) => unknown) => { readonly value?: unknown; readonly get?: () => unknown; readonly set?: (value: unknown) => void };
}

const reading = (report: (value: unknown) => unknown) => (...given: unknown[]): unknown => report(given);

const FORMS: readonly Form[] = [
  { name: "a first parameter", declare: (m, t, g) => `readonly ${m}: ${g}(node: ${t}) => string;`, call: (m, v, a) => `operator.${m}${a}(${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ value: reading(r) }) },
  { name: "a second parameter", declare: (m, t, g) => `readonly ${m}: ${g}(label: string, node: ${t}) => string;`, call: (m, v, a) => `operator.${m}${a}("x", ${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[1]` }), accepts: true, generic: true, body: (r) => ({ value: reading(r) }) },
  {
    name: "this",
    declare: (m, t) => `readonly ${m}: (this: ${t}) => string;`,
    call: (m, v) => `operator.${m}.call(${v})`,
    place: (m) => ({ type: `ThisParameterType<Curator["${m}"]>` }), accepts: true,
    generic: false,
    body: (r) => ({
      value: function (this: unknown): unknown {
        return r(this);
      },
    }),
  },
  { name: "a nested member", declare: (m, t, g) => `readonly ${m}: { readonly peek: ${g}(node: ${t}) => string };`, call: (m, v, a) => `operator.${m}.peek${a}(${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}.peek${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ value: { peek: reading(r) } }) },
  { name: "a returned function", declare: (m, t, g) => `readonly ${m}: () => ${g}(node: ${t}) => string;`, call: (m, v, a) => `operator.${m}()${a}(${v})`, place: (m, a) => ({ helper: `declare const r_${m}: ReturnType<Curator["${m}"]>;`, type: `Parameters<typeof r_${m}${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ value: () => reading(r) }) },
  { name: "a getter", declare: (m, t, g) => `get ${m}(): ${g}(node: ${t}) => string;`, call: (m, v, a) => `operator.${m}${a}(${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ get: () => reading(r) }) },
  {
    name: "a construct signature",
    declare: (m, t, g) => `readonly ${m}: new ${g}(node: ${t}) => { readonly read: string };`,
    call: (m, v, a) => `new operator.${m}${a}(${v})`,
    place: (m, a) => ({ type: `ConstructorParameters<typeof operator.${m}${a}>[0]` }), accepts: true,
    generic: true,
    body: (r) => ({
      value: function (...given: unknown[]): object {
        return { read: r(given) };
      },
    }),
  },
  { name: "an overload", declare: (m, t, g) => `readonly ${m}: { (label: string): string; ${g}(node: ${t}): string };`, call: (m, v, a) => `operator.${m}${a}(${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ value: reading(r) }) },
  { name: "an optional parameter", declare: (m, t, g) => `readonly ${m}: ${g}(node?: ${t}) => string;`, call: (m, v, a) => `operator.${m}${a}(${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ value: reading(r) }) },
  { name: "a rest parameter", declare: (m, t, g) => `readonly ${m}: ${g}(...nodes: (${t})[]) => string;`, call: (m, v, a) => `operator.${m}${a}(${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[number]` }), accepts: true, generic: true, body: (r) => ({ value: reading(r) }) },
  { name: "a function in a Promise", declare: (m, t, g) => `readonly ${m}: () => Promise<${g}(node: ${t}) => string>;`, call: (m, v, a) => `operator.${m}().then((f) => f${a}(${v}))`, place: (m, a) => ({ helper: `declare const p_${m}: Awaited<ReturnType<Curator["${m}"]>>;`, type: `Parameters<typeof p_${m}${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ value: () => Promise.resolve(reading(r)) }) },
  { name: "a writable member", declare: (m, t) => `${m}: ${t};`, call: (m, v) => `(operator.${m} = ${v})`, place: (m) => ({ type: `Curator["${m}"]` }), accepts: true, generic: false, body: (r) => ({ set: (value) => r(value) }) },
  { name: "a setter", declare: (m, t) => `get ${m}(): string;\n  set ${m}(value: ${t});`, call: (m, v) => `(operator.${m} = ${v})`, place: null, accepts: true, generic: false, body: (r) => ({ set: (value) => r(value) }) },
  { name: "an index signature", declare: (m, t, g) => `readonly ${m}: { readonly [key: string]: ${g}(node: ${t}) => string };`, call: (m, v, a) => `operator.${m}["x"]?.${a}(${v})`, place: (m, a) => ({ helper: `declare const i_${m}: NonNullable<Curator["${m}"][string]>;`, type: `Parameters<typeof i_${m}${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ value: { x: reading(r) } }) },
  { name: "a callable object", declare: (m, t, g) => `readonly ${m}: { readonly tag: string; ${g}(node: ${t}): string };`, call: (m, v, a) => `operator.${m}${a}(${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[0]` }), accepts: true, generic: true, body: (r) => ({ value: Object.assign(reading(r), { tag: "t" }) }) },
  { name: "a method", declare: (m, t, g) => `${m}${g}(node: ${t}): string;`, call: (m, v, a) => `operator.${m}${a}(${v})`, place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[0]` }), accepts: false, generic: true, body: (r) => ({ value: reading(r) }) },
  {
    name: "a callback's return",
    declare: (m, t, g) => `readonly ${m}: ${g}(get: () => ${t}) => string;`,
    call: (m, v, a) => `operator.${m}${a}(() => (${v}))`,
    place: (m, a) => ({ type: `Parameters<typeof operator.${m}${a}>[0]` }), given: (v) => `() => (${v})`, accepts: false,
    generic: true,
    body: (r) => ({ value: (get: () => unknown) => r(get()) }),
  },
];

/** A member of the copy of the client: its name, form and term. */
interface Member {
  readonly name: string;
  readonly form: Form;
  readonly term: Term;
}

/** A value a member is handed: its text in TypeScript and at run time, and whether it holds the box
 * at a declared slot (`takes`'s oracle) or not (a carrier, a void-typed value). */
interface Given {
  readonly value: string;
  readonly exact: boolean;
  readonly js: string;
}

/** A question to the oracle: whether a value reaches a member, at an instance of its type. */
interface Question {
  readonly member: Member;
  readonly args: string;
  readonly given: Given;
  /** The file its place and carrier are declared in. */
  readonly file: "place" | "generic";
  /** The place type's alias in the oracle file (`null` for a setter's). */
  readonly place: string | null;
  /** The witness's name in the oracle file. */
  readonly witness: string;
  /** The carrier's name, which must not be `never`, or `null`. */
  readonly carrier: string | null;
  /** The lines of the oracle file its place and its carrier are declared at. */
  readonly declared: readonly number[];
  /** The line of its call in the oracle file, if it is compiled. */
  line: number | null;
}

const depth1 = BASES.flatMap((term) => WRAPPERS.map(([, make]) => make(term)));
const coreDepth1 = depth1.filter((_term, index) => CORE.includes(BASES[Math.floor(index / WRAPPERS.length)]?.name ?? ""));
const depth2 = coreDepth1.flatMap((term) => WRAPPERS.map(([, make]) => make(term)));

const [FIRST] = FORMS;
if (FIRST === undefined) {
  throw new Error("no forms");
}

/** The members: every term up to depth 2 in the first form; every form over the bases, the
 * generics and the wrappers of the core bases. */
const MEMBERS: readonly Member[] = [
  ...[...BASES, ...depth1, ...depth2, ...GENERICS].map((term) => ({ form: FIRST, term })),
  ...FORMS.slice(1).flatMap((form) => [...BASES, ...coreDepth1, ...GENERICS].filter((term) => term.generic === undefined || form.generic).map((term) => ({ form, term }))),
].map((member, index) => ({ ...member, name: `zz${String(index)}` }));

/** The type with its type parameter replaced by a type argument. */
const instantiate = (term: Term, argument: string): string => term.text.replace(/\bT0\b/gu, `(${argument})`);

/** "Declared but never read" (`program.ts`'s): no failure of a call. */
const UNUSED = new Set([6133, 6192, 6196, 6198, 6199, 6205]);

const CURATOR = "src/api/curator.ts";
const MARK = "  readonly queueAt: (label: JsonOut) => Promise<Outcome>;\n";

/** The copy of the client with the given members added to `Curator`, and the declarations the
 * grammar names. */
function copyWith(members: string): string {
  const text = readFileSync(path.join(WEB, CURATOR), "utf8");
  if (!text.includes(MARK)) {
    throw new Error("curator.ts's Curator has no queueAt");
  }
  const box = /^import \{[^}]*\} from "\.\/box";$/mu;
  if (!box.test(text)) {
    throw new Error("curator.ts imports the box otherwise");
  }
  return `${text.replace(box, 'import { isServerNumber, type ServerNumber } from "./box";').replace(MARK, `${MARK}${members}\n`)}
export enum Colour { A = "a", B = "b" }
export enum Level { Low, High }
export declare class Holder<V> { constructor(v: V); readonly v: V; }
export interface Wrap<V> { readonly v: V }
export interface Empty {}
export interface Sized { readonly length: number }
export declare class Named { readonly name: string }
`;
}

/** The copy of the client with every member of the grammar's input forms added. */
function client(): string {
  return copyWith(MEMBERS.map(({ name, form, term }) => `  ${form.declare(name, term.text, term.generic?.parameters ?? "")}`).join("\n"));
}

/** Whether a member's calls are compiled whatever the oracle says (the cross-check of the place
 * operators against real calls): a base in any form, a generic in the first, every setter. */
const crossChecked = ({ form, term }: Member): boolean => BASES.includes(term) || (GENERICS.includes(term) && form === FIRST) || form.place === null;

/** The files of the oracle: the witnesses, the places of the plain members, those of the generic
 * ones (an instance its constraint refuses fails there, and is dropped), and the compiled calls. */
const FILES = {
  witness: "src/operator/probe-witness.ts",
  place: "src/operator/probe-place.ts",
  generic: "src/operator/probe-generic.ts",
  calls: "src/operator/probe-calls.ts",
} as const;

type File = keyof typeof FILES;

const PREAMBLE = [
  'import { operator } from "../api/operator";',
  'import type { components, ServerNumber } from "../api";',
  'type JsonOut = components["schemas"]["OutputJson"];',
  'import { Colour, Holder, Level, type Curator, type Wrap } from "../api/curator";',
  "export type Unused = [typeof operator, typeof Colour, typeof Level, typeof Holder, Wrap<0>, Curator, JsonOut, ServerNumber];",
];

const GIVEN = ['import { b, list, rec, node, vd } from "./probe-witness";', "export type Given = [typeof b, typeof list, typeof rec, typeof node, typeof vd];"];

/** The oracle's files and its questions. The calls are written later (`calls`). */
function oracle(): { files: Record<Exclude<File, "calls">, string[]>; questions: Question[] } {
  const files = {
    witness: [
      ...PREAMBLE,
      "export declare const b: ServerNumber;",
      "export declare const list: readonly ServerNumber[];",
      "export declare const rec: { readonly [key: string]: ServerNumber };",
      "export declare const node: JsonOut;",
      "const launder: () => void = () => b;",
      "export const vd = launder();",
      "export type Primitive = string | number | boolean | bigint | null | undefined;",
    ],
    place: [...PREAMBLE, ...GIVEN],
    generic: [...PREAMBLE, ...GIVEN],
  };
  const witnesses = new Map<string, string>();
  const witness = (file: File, expression: string): string => {
    const key = `${file}:${expression}`;
    const known = witnesses.get(key);
    if (known !== undefined) {
      return known;
    }
    const name = `w${String(witnesses.size)}`;
    witnesses.set(key, name);
    if (file !== "calls") {
      files[file].push(`export const ${name} = ${expression};`);
    }
    return name;
  };
  const questions: Question[] = [];
  let places = 0;
  for (const member of MEMBERS) {
    const { form, term } = member;
    const file = term.generic === undefined ? "place" : "generic";
    const lines = files[file];
    const instances: readonly (readonly [string, string])[] = term.generic === undefined ? [["", term.text]] : term.generic.arguments.map((argument) => [`<${argument}>`, instantiate(term, argument)] as const);
    const helped = new Set<string>();
    for (const [args, type] of instances) {
      const placed = form.place?.(member.name, args) ?? null;
      const declared: number[] = [];
      let place: string | null = null;
      if (placed !== null) {
        if (placed.helper !== undefined && !helped.has(placed.helper)) {
          helped.add(placed.helper);
          lines.push(placed.helper);
        }
        place = `Q${String(places)}`;
        lines.push(`export type ${place} = ${placed.type};`);
        declared.push(lines.length - 1);
      }
      const carrier = `c${String(places)}`;
      places += 1;
      lines.push(`export declare const ${carrier}: (${type}) & object & { readonly extra: ServerNumber };`);
      declared.push(lines.length - 1);
      const givens: Given[] = [
        { value: "b", exact: true, js: "b" },
        { value: "list", exact: false, js: "list" },
        { value: "rec", exact: false, js: "rec" },
        { value: "node", exact: false, js: "node" },
        { value: "vd", exact: false, js: "b" },
        { value: carrier, exact: false, js: "({ extra: b })" },
        ...term.slots.map((slot) => ({ value: slot, exact: true, js: slot.replace(/ as const/gu, "") })),
      ];
      for (const given of givens) {
        const wrapped = form.given?.(given.value) ?? given.value;
        // A carrier's witness stands beside the carrier; every other in the witnesses' file.
        const at: File = given.value === carrier ? file : "witness";
        const name = witness(at, wrapped);
        if (at === file) {
          // The carrier's witness fails with it.
          declared.push(lines.length - 1);
        }
        questions.push({ member, args, given, file, place, witness: name, carrier: given.value === carrier ? carrier : null, declared, line: null });
      }
    }
  }
  return { files, questions };
}

let checked: Checked;
let questions: readonly Question[];
/** The questions whose value reaches the member. */
let reached: Set<Question>;
/** The members the gate refuses. */
let refused: Set<string>;
/** The diagnostics of the copy of the client. */
let clientCodes: string[];
/** The compiled calls whose verdict differs from the place operators'. */
let disagreements: string[];
/** The questions of an instance its type parameter's constraint refuses. */
let dropped: readonly Question[];
/** The terms the compiler holds primitive: their place in the first form is assignable to
 * `string | number | boolean | bigint | null | undefined` (no object is). */
let primitives: Set<Term>;
/** The terms whose place is `never`. */
let empty: Set<Term>;
/** The terms whose place is, or has among its union's members, an intersection. */
let branded: Set<Term>;
/** How many outputs the walk of the input forms' copy looked at. */
let visits: number;

/** The lines of a file of a program with a diagnostic (but an unused declaration's). */
function failing(program: Checked, file: string): Set<number> {
  const source = program.source(file);
  return new Set(program.diagnostics(file).flatMap((diagnostic) => (diagnostic.start === undefined || UNUSED.has(diagnostic.code) ? [] : [source.getLineAndCharacterOfPosition(diagnostic.start).line])));
}

beforeAll(() => {
  const made = oracle();
  questions = made.questions;
  const copy = client();
  const text = (lines: readonly string[]): string => `${lines.join("\n")}\n`;
  // First the place operators: the assignability of each witness's type to the place's.
  const first = check({ [CURATOR]: copy, [FILES.witness]: text(made.files.witness), [FILES.place]: text(made.files.place), [FILES.generic]: text(made.files.generic), "src/probe-surface.ts": "export * as oracle from './api/oracle';\n" });
  const symbols = new Map<string, ts.Symbol>(
    (["witness", "place", "generic"] as const).flatMap((file) => first.checker.getExportsOfModule(first.checker.getSymbolAtLocation(first.source(FILES[file])) as ts.Symbol).map((symbol) => [`${file}:${symbol.getName()}`, symbol] as const)),
  );
  const typeOf = (file: File, name: string, declared: boolean): ts.Type => {
    const symbol = symbols.get(`${file}:${name}`);
    if (symbol === undefined) {
      throw new Error(`the oracle's ${file} has no ${name}`);
    }
    return declared ? first.checker.getDeclaredTypeOfSymbol(symbol) : first.checker.getTypeOfSymbol(symbol);
  };
  expect([...failing(first, FILES.witness)].map((line) => made.files.witness[line])).toEqual([]);
  // An instance its constraint refuses is no call: its place or carrier fails, and its questions
  // are dropped; nothing else of the generics' file fails.
  const broken = failing(first, FILES.generic);
  const isBroken = (question: Question): boolean => question.file === "generic" && question.declared.some((line) => broken.has(line));
  dropped = made.questions.filter(isBroken);
  const declaredLines = new Set(made.questions.filter((question) => question.file === "generic").flatMap((question) => question.declared));
  expect([...broken].filter((line) => !declaredLines.has(line)).map((line) => made.files.generic[line])).toEqual([]);
  const inhabited = (question: Question): boolean => question.carrier === null || (typeOf(question.file, question.carrier, false).flags & ts.TypeFlags.Never) === 0;
  const answered = new Map<Question, boolean>();
  for (const question of made.questions) {
    if (question.place === null || isBroken(question)) {
      continue;
    }
    const at: File = question.carrier === null ? "witness" : question.file;
    answered.set(question, inhabited(question) && first.checker.isTypeAssignableTo(typeOf(at, question.witness, false), typeOf(question.file, question.place, true)));
  }
  const primitiveType = typeOf("witness", "Primitive", true);
  primitives = new Set();
  empty = new Set();
  branded = new Set();
  for (const question of made.questions) {
    if (question.member.form === FIRST && question.member.term.generic === undefined && question.place !== null) {
      const place = typeOf("place", question.place, true);
      if ((place.flags & ts.TypeFlags.Any) === 0 && first.checker.isTypeAssignableTo(place, primitiveType)) {
        primitives.add(question.member.term);
      }
      if ((place.flags & ts.TypeFlags.Never) !== 0) {
        empty.add(question.member.term);
      }
      if ((place.isUnion() ? place.types : [place]).some((member) => member.isIntersection())) {
        branded.add(question.member.term);
      }
    }
  }
  // Then the calls of the cross-checked members, compiled.
  const calls = [...PREAMBLE, ...GIVEN, 'import * as places from "./probe-place";', 'import * as generics from "./probe-generic";', "export type Places = [typeof places, typeof generics];"];
  for (const question of made.questions) {
    if (crossChecked(question.member) && !isBroken(question)) {
      const value = question.carrier === null ? question.given.value : `${question.file === "generic" ? "generics" : "places"}.${question.carrier}`;
      calls.push(`export const o${String(calls.length)} = () => ${question.member.form.call(question.member.name, value, question.args)};`);
      question.line = calls.length - 1;
    }
  }
  checked = check({ [CURATOR]: copy, [FILES.witness]: text(made.files.witness), [FILES.place]: text(made.files.place), [FILES.generic]: text(made.files.generic), [FILES.calls]: text(calls), "src/probe-surface.ts": "export * as oracle from './api/oracle';\n" });
  const wrong = failing(checked, FILES.calls);
  expect([...wrong].filter((line) => line < PREAMBLE.length + GIVEN.length + 3).map((line) => calls[line])).toEqual([]);
  reached = new Set();
  disagreements = [];
  for (const question of made.questions) {
    const operators = answered.get(question);
    const compiled = question.line === null ? undefined : !wrong.has(question.line) && inhabited(question);
    if (operators !== undefined && compiled !== undefined && operators !== compiled) {
      disagreements.push(`${question.member.form.name}: ${question.member.term.name}${question.args} (${question.given.value}): operators ${String(operators)}, call ${String(compiled)}`);
    }
    if (operators ?? compiled ?? false) {
      reached.add(question);
    }
  }
  refused = new Set();
  const walked = surface(checked.program, checked.checker);
  visits = walked.visits;
  for (const { at } of walked.problems) {
    const found = /^operator\.(zz[0-9]+)(?![0-9A-Za-z_])/u.exec(at);
    if (found?.[1] !== undefined) {
      refused.add(found[1]);
    }
  }
  const curator = checked.source(CURATOR);
  clientCodes = checked.diagnostics(CURATOR).map((diagnostic) => `${String(diagnostic.code)}@${String(diagnostic.start === undefined ? -1 : curator.getLineAndCharacterOfPosition(diagnostic.start).line + 1)}`);
}, 600_000);

describe("the surface gate and the index's check, against the compiler over a grammar of parameter types", () => {
  it("covers the whole domain at its bound", () => {
    expect([BASES.length, WRAPPERS.length, GENERICS.length, FORMS.length]).toEqual([40, 27, 22, 17]);
    expect(depth1.length).toBe(BASES.length * WRAPPERS.length);
    expect(depth2.length).toBe(CORE.length * WRAPPERS.length * WRAPPERS.length);
    const generic = FORMS.filter((form) => form.generic).length - 1;
    expect(MEMBERS.length).toBe(BASES.length + depth1.length + depth2.length + GENERICS.length + (FORMS.length - 1) * (BASES.length + coreDepth1.length) + generic * GENERICS.length);
    for (const form of FORMS) {
      expect([...reached].some((question) => question.member.form === form), form.name).toBe(true);
    }
    // Dropped: only the instances of a generic its constraint refuses.
    expect(dropped.length).toBeGreaterThan(0);
    expect(dropped.filter((question) => question.member.term.generic === undefined)).toEqual([]);
  });

  it("compiles every member: the copy of the client fails only where createCurator does not make the new members", () => {
    expect(clientCodes.filter((code) => !/^274[01]@/u.test(code))).toEqual([]);
    expect(clientCodes.length).toBe(1);
  });

  it("the place operators agree with real calls: wherever a call is compiled, it compiles exactly when the operators say the value reaches", () => {
    expect(disagreements).toEqual([]);
    expect(questions.filter((question) => question.line !== null).length).toBeGreaterThan(5000);
  });

  it("refuses every member a box reaches (the compiler is the oracle)", () => {
    const holes = [...new Set([...reached].filter((question) => !refused.has(question.member.name)).map(({ member, args, given }) => `${member.form.name}: ${member.term.name}${args} (${given.value})`))];
    expect(holes).toEqual([]);
  });

  it("accepts a primitive of the grammar in every form that takes a value, and no box reaches it", () => {
    const plain = MEMBERS.filter(({ form, term }) => form.accepts && term.primitive);
    const wrong = plain.flatMap(({ name, form, term }) => {
      const reaching = [...reached].filter((question) => question.member.name === name);
      return refused.has(name) || reaching.length > 0 ? [`${form.name}: ${term.name}${reaching.length > 0 ? ` reached by ${reaching.map((question) => question.given.value).join(", ")}` : ""}`] : [];
    });
    expect(wrong).toEqual([]);
    expect(plain.length).toBeGreaterThan(200);
  });

  it("accepts nothing else (the allow-list): what it accepts is a primitive by the compiler, in a form that takes a value; what it refuses that the compiler holds primitive holds no object", () => {
    const accepted = MEMBERS.filter(({ name }) => !refused.has(name));
    expect(accepted.filter(({ form, term }) => !form.accepts || !primitives.has(term)).map(({ form, term }) => `${form.name}: ${term.name}`)).toEqual([]);
    expect(accepted.length).toBeGreaterThan(300);
    // Refused although the compiler holds it primitive: `never`, or an intersection of a primitive
    // with an object type, which the allow-list does not name and no object inhabits.
    const overRefused = MEMBERS.filter(({ name, form, term }) => form.accepts && primitives.has(term) && !empty.has(term) && refused.has(name));
    expect(overRefused.filter(({ term }) => !branded.has(term)).map(({ form, term }) => `${form.name}: ${term.name}`)).toEqual([]);
    expect(overRefused.length).toBeGreaterThan(0);
  });

  it("takes() is a taker wherever a box reaches a declared slot, and not for a shape of primitives the program declares", () => {
    const taker = takes(checked.program, checked.checker);
    const operator = checked.checker.getExportsOfModule(checked.checker.getSymbolAtLocation(checked.source("src/api/operator.ts")) as ts.Symbol).find((symbol) => symbol.getName() === "operator");
    if (operator === undefined) {
      throw new Error("no operator");
    }
    const type = checked.checker.getTypeOfSymbol(operator);
    const parameterOf = (member: Member): ts.Type => {
      const property = type.getProperty(member.name);
      const [signature] = property === undefined ? [] : checked.checker.getTypeOfSymbol(property).getCallSignatures();
      const [parameter] = signature?.getParameters() ?? [];
      if (parameter === undefined) {
        throw new Error(`${member.name} has no parameter`);
      }
      return checked.checker.getTypeOfSymbol(parameter);
    };
    const first = MEMBERS.filter(({ form }) => form === FIRST);
    const exact = new Set([...reached].filter((question) => question.given.exact).map((question) => question.member));
    expect(first.filter((member) => exact.has(member) && !taker(parameterOf(member))).map(({ term }) => term.name)).toEqual([]);
    const inert = first.filter(({ term }) => term.inert && term.generic === undefined);
    expect(inert.filter((member) => taker(parameterOf(member))).map(({ term }) => term.name)).toEqual([]);
    expect(first.filter(({ term }) => term.primitive).every((member) => primitive(parameterOf(member)))).toBe(true);
    expect(inert.length).toBeGreaterThan(300);
    expect(exact.size).toBeGreaterThan(2000);
  });

  it("at run time, every value that reaches a member is handed to its body: the box's digits come back", async () => {
    const root = decode('{"n": 4217, "list": [4217], "rec": {"k": 4217}}');
    const at = (value: unknown, name: string): unknown => (typeof value === "object" && value !== null && !Array.isArray(value) ? Object.getOwnPropertyDescriptor(value, name)?.value : undefined);
    const b = at(root, "n");
    expect(isServerNumber(b)).toBe(true);
    /** What a member's body would do: look for a box in what it is given. */
    const find = async (value: unknown, depth: number, seen: Set<unknown>): Promise<string | undefined> => {
      if (isServerNumber(value)) {
        return textOf(value);
      }
      if (depth > 6 || value === null || (typeof value !== "object" && typeof value !== "function") || seen.has(value)) {
        return undefined;
      }
      seen.add(value);
      const inside: unknown[] = [];
      if (value instanceof Promise) {
        inside.push(await (value as Promise<unknown>));
      }
      if (value instanceof Map || value instanceof Set) {
        inside.push(...(value as ReadonlyMap<unknown, unknown> | ReadonlySet<unknown>).values());
      }
      if (value instanceof WeakRef) {
        inside.push((value as WeakRef<object>).deref());
      }
      for (const key of Reflect.ownKeys(value)) {
        inside.push(Reflect.get(value, key));
      }
      for (const key of ["reason", "detail", "data", "cause", "value"]) {
        if (key in value) {
          inside.push(Reflect.get(value, key));
        }
      }
      if (typeof value === "function" && value.length === 0) {
        try {
          inside.push((value as () => unknown)());
        } catch {
          // A function that needs its arguments.
        }
      }
      for (const one of inside) {
        const found = await find(one, depth + 1, seen);
        if (found !== undefined) {
          return found;
        }
      }
      return undefined;
    };
    const read = new Map<string, string>();
    const pending: Promise<void>[] = [];
    const fake: Record<string, unknown> = {};
    for (const { name, form } of MEMBERS) {
      const report = (value: unknown): string => {
        pending.push(
          find(value, 0, new Set()).then((found) => {
            if (found !== undefined) {
              read.set(name, found);
            }
          }),
        );
        return "";
      };
      const body = form.body(report);
      Object.defineProperty(fake, name, { enumerable: true, configurable: true, ...(body.get !== undefined || body.set !== undefined ? { get: body.get ?? (() => ""), set: body.set ?? (() => undefined) } : { value: body.value, writable: true }) });
    }
    const all = [...reached];
    const run = vm.runInThisContext(`(function (operator, b, list, rec, node, Holder) { return [${all.map(({ member, given }) => `() => ${member.form.call(member.name, given.js, "")}`).join(",\n")}]; })`) as (...given: unknown[]) => (() => unknown)[];
    class Holder {
      readonly v: unknown;
      constructor(v: unknown) {
        this.v = v;
      }
    }
    const thunks = run(fake, b, at(root, "list"), at(root, "rec"), root, Holder);
    const missed: string[] = [];
    for (const [index, thunk] of thunks.entries()) {
      const question = all[index];
      if (question === undefined) {
        continue;
      }
      read.delete(question.member.name);
      pending.length = 0;
      await Promise.resolve(thunk());
      while (pending.length > 0) {
        await Promise.all(pending.splice(0));
      }
      if (read.get(question.member.name) !== "4217") {
        missed.push(`${question.member.form.name}: ${question.member.term.name} (${question.given.value})`);
      }
    }
    expect(missed).toEqual([]);
    expect(all.length).toBeGreaterThan(10_000);
  });
});

// ---------------------------------------------------------------------------------------------
// The output forms (m11 of round 3): what the client gives, and what its caller can write into it.

/** A form a member gives a value in: its declaration in `Curator` and the expression of the value
 * the caller is given. */
interface OutputForm {
  readonly name: string;
  readonly declare: (member: string, type: string) => string;
  readonly read: (member: string) => string;
}

const OUTPUT_FORMS: readonly OutputForm[] = [
  { name: "an output", declare: (m, t) => `readonly ${m}: () => ${t};`, read: (m) => `operator.${m}()` },
  { name: "a list output", declare: (m, t) => `readonly ${m}: () => (${t})[];`, read: (m) => `operator.${m}()` },
  { name: "a tuple output", declare: (m, t) => `readonly ${m}: () => [${t}];`, read: (m) => `operator.${m}()` },
  { name: "a member", declare: (m, t) => `readonly ${m}: ${t};`, read: (m) => `operator.${m}` },
];

/** What a caller can do to a value `r` the client gave it, to put the box `b` where the client may
 * read it: a member written, an element written or pushed, a function called (what `unknown`,
 * `any`, `object` and `Function` can be narrowed to). Each is compiled; one that compiles is a way
 * a box reaches the client from an output. */
const WRITES: readonly (readonly [string, string])[] = [
  ["v", "r.v = b;"],
  ["value", "r.value = b;"],
  ["cause", "r.cause = b;"],
  ["reason", "r.reason = b;"],
  ["a key", 'r["k"] = b;'],
  ["an index", "r[0] = b;"],
  ["push", "r.push(b);"],
  ["a call", 'if (typeof r === "function") { r(b); }'],
];

/** The object types a function is assignable to, and their neighbours (round 4's S1b: the walk
 * refused `{}` alone, and `{ readonly length: number }`, which a caller narrows to a function by
 * `typeof r === "function"` and calls with a box, passed it): objects with one member that a
 * function has (`length`, `name`, `toString`, `valueOf`, `call`, `apply`, `bind`), alone or beside
 * another, optional, read-only, through a utility type, a class, an interface, an index, in a union;
 * objects with one member of each primitive and function kind, which a function lacks (the compiler
 * narrows them to `never`: the gate may walk them); weak types; and objects with an `any` or an
 * `unknown` member. The compiler is the oracle, as for every term: a call that compiles must be
 * refused. */
const FUNCTION_SHAPES: readonly Term[] = [
  base("{ readonly length: number }", "{ readonly length: number }", false),
  base("{ readonly name: string }", "{ readonly name: string }", false),
  base("{ readonly toString: () => string }", "{ readonly toString: () => string }", false),
  base("{ toString(): string }", "{ toString(): string }", false),
  base("{ readonly valueOf: () => Object }", "{ readonly valueOf: () => Object }", false),
  base("{ readonly call: Function['call'] }", "{ readonly call: Function['call'] }", false),
  base("{ readonly apply: Function['apply'] }", "{ readonly apply: Function['apply'] }", false),
  base("{ readonly bind: Function['bind'] }", "{ readonly bind: Function['bind'] }", false),
  base("{ readonly length: number; readonly name: string }", "{ readonly length: number; readonly name: string }", false),
  base("{ readonly length?: number }", "{ readonly length?: number }", false),
  base("{ readonly name?: string }", "{ readonly name?: string }", false),
  base("{ length: number }", "{ length: number }", false),
  base("Readonly<{ length: number }>", "Readonly<{ length: number }>", false),
  base("Pick<Function, 'length'>", "Pick<Function, 'length'>", false),
  base("Record<never, string>", "Record<never, string>", false),
  base("Record<'length', number>", "Record<'length', number>", false),
  base("{ readonly [key: string]: string }", "{ readonly [key: string]: string }", false),
  base("{ readonly [key: number]: string }", "{ readonly [key: number]: string }", false),
  base("an empty interface", "Empty", false),
  base("an interface with length", "Sized", false),
  base("a class with a name", "Named", false),
  base("{ readonly length: number } | string", "{ readonly length: number } | string", false),
  base("{ readonly length: number } | null", "{ readonly length: number } | null", false),
  base("{ readonly length: number } & { readonly x: string }", "{ readonly length: number } & { readonly x: string }", false),
  base("{ readonly flag: boolean }", "{ readonly flag: boolean }", false),
  base("{ readonly big: bigint }", "{ readonly big: bigint }", false),
  base("{ readonly s: symbol }", "{ readonly s: symbol }", false),
  base("{ readonly n: null }", "{ readonly n: null }", false),
  base("{ readonly u: undefined }", "{ readonly u: undefined }", false),
  base("{ readonly f: () => void }", "{ readonly f: () => void }", false),
  base("{ readonly f: () => string }", "{ readonly f: () => string }", false),
  base("{ readonly x?: string }", "{ readonly x?: string }", false),
  base("{ readonly length?: number; readonly x?: string }", "{ readonly length?: number; readonly x?: string }", false),
  base("{ readonly v: any }", "{ readonly v: any }", false),
  base("{ readonly length: any }", "{ readonly length: any }", false),
  base("{ readonly length: unknown }", "{ readonly length: unknown }", false),
  base("{ readonly v: unknown }", "{ readonly v: unknown }", false),
];

/** The terms the output forms are given: every base and every wrapper of the core bases, and the
 * function-shaped objects. */
const OUTPUTS: readonly Term[] = [...BASES, ...coreDepth1, ...FUNCTION_SHAPES];

/** The members of the output copy: every output form over every term. */
const OUTPUT_MEMBERS = OUTPUT_FORMS.flatMap((form) => OUTPUTS.map((term) => ({ form, term }))).map((member, index) => ({ ...member, name: `oo${String(index)}` }));

describe("the surface gate against the compiler over the output forms: a member giving T, a list or a tuple of T, or a member of T", () => {
  let reachedBy: Map<string, string[]>;
  let refusedOut: Set<string>;
  let outputVisits: number;

  beforeAll(() => {
    const members = OUTPUT_MEMBERS.map(({ name, form, term }) => `  ${form.declare(name, term.text)}`).join("\n");
    const calls = ['import { operator } from "../api/operator";', 'import type { ServerNumber } from "../api";', "declare const b: ServerNumber;"];
    const lines: { name: string; write: string }[] = [];
    for (const { name, form } of OUTPUT_MEMBERS) {
      for (const [write, code] of WRITES) {
        calls.push(`export const p${String(calls.length)} = () => { const r = ${form.read(name)}; ${code} };`);
        lines.push({ name, write });
      }
    }
    const file = "src/operator/probe-outputs.ts";
    const made = check({ [CURATOR]: copyWith(members), [file]: `${calls.join("\n")}\n`, "src/probe-surface.ts": "export * as oracle from './api/oracle';\n" });
    outputVisits = surface(made.program, made.checker).visits;
    const wrong = failing(made, file);
    reachedBy = new Map();
    lines.forEach(({ name, write }, index) => {
      if (!wrong.has(index + 3)) {
        reachedBy.set(name, [...(reachedBy.get(name) ?? []), write]);
      }
    });
    refusedOut = new Set();
    for (const { at } of surface(made.program, made.checker).problems) {
      const found = /^operator\.(oo[0-9]+)(?![0-9A-Za-z_])/u.exec(at);
      if (found?.[1] !== undefined) {
        refusedOut.add(found[1]);
      }
    }
  }, 600_000);

  it("the walk stays far inside its budget (a walk that goes into library types fails cleanly, in a bounded time, instead of running out of memory: A25)", () => {
    expect(visits).toBeLessThan(BUDGET / 10);
    expect(outputVisits).toBeLessThan(BUDGET / 10);
  });

  it("covers the whole domain: every base and every wrapper of the core bases, in every output form", () => {
    expect(OUTPUT_FORMS.length).toBe(4);
    expect(OUTPUTS.length).toBe(BASES.length + CORE.length * WRAPPERS.length + FUNCTION_SHAPES.length);
    expect(OUTPUT_MEMBERS.length).toBe(OUTPUT_FORMS.length * OUTPUTS.length);
    expect(WRITES.length).toBe(8);
  });

  it("the oracle finds every kind of write, in every form (it is not vacuous)", () => {
    for (const [write] of WRITES) {
      expect(
        OUTPUT_MEMBERS.some(({ name }) => reachedBy.get(name)?.includes(write) === true),
        write,
      ).toBe(true);
    }
    for (const form of OUTPUT_FORMS) {
      expect(OUTPUT_MEMBERS.some((member) => member.form === form && reachedBy.has(member.name)), form.name).toBe(true);
    }
    expect(reachedBy.size).toBeGreaterThan(100);
  });

  it("refuses every output a box can be written into: an unknown or any output, a mutable list's elements, a mutable tuple's, a member the caller can write (the compiler is the oracle)", () => {
    const holes = OUTPUT_MEMBERS.filter(({ name }) => reachedBy.has(name) && !refusedOut.has(name)).map(({ form, term, name }) => `${form.name}: ${term.name} (${(reachedBy.get(name) ?? []).join(", ")})`);
    expect(holes).toEqual([]);
  });

  it("the oracle finds the call a function-shaped object lets a caller make (it is not vacuous): with a member a function has, in the forms that give the value itself; none of the objects with a member it lacks", () => {
    const called = (form: OutputForm, term: Term): boolean => OUTPUT_MEMBERS.some((member) => member.form === form && member.term === term && reachedBy.get(member.name)?.includes("a call") === true);
    // In a list or a tuple the value is the list, which `typeof r === "function"` narrows to nothing.
    const direct = OUTPUT_FORMS.filter(({ name }) => name === "an output" || name === "a member");
    const callable = FUNCTION_SHAPES.filter((term) => direct.every((form) => called(form, term))).map((term) => term.name);
    expect(callable).toEqual(expect.arrayContaining(["{ readonly length: number }", "{ readonly name: string }", "{ readonly toString: () => string }", "Record<never, string>", "an empty interface"]));
    expect(callable.length).toBeGreaterThan(15);
    for (const lacking of ["{ readonly flag: boolean }", "{ readonly f: () => void }", "{ readonly x?: string }"]) {
      expect(FUNCTION_SHAPES.some((term) => term.name === lacking && OUTPUT_FORMS.some((form) => called(form, term))), lacking).toBe(false);
    }
  });

  it("accepts a primitive, a list, a tuple and a member of one: nothing is written into them that holds a box", () => {
    const plain = OUTPUT_MEMBERS.filter(({ term }) => term.primitive);
    expect(plain.filter(({ name }) => refusedOut.has(name) || reachedBy.has(name)).map(({ form, term }) => `${form.name}: ${term.name}`)).toEqual([]);
    expect(plain.length).toBeGreaterThan(50);
  });
});
