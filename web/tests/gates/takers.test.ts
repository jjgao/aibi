/**
 * The two checks of a parameter type, against a table (round 1 of #115's review, m1; round 2, MA2):
 * each row is a parameter type, whether the index's check (`takers.ts`'s `takes`, a deny-check
 * kept as defence in depth) must call it a taker, and whether the operator client's allow-list
 * (`surface.ts`'s `primitive`, which accepts a primitive alone) must refuse it. Both are checked in
 * both directions: a type the table refuses that a check accepts is a hole, one it accepts that a
 * check refuses is a gate that cries wolf. Every type `takes` refuses the allow-list refuses too.
 * The class is held by the brute force over a grammar (`surface-brute.test.ts`); this table is its
 * named controls.
 */
import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

import { check, type Checked } from "./program";
import { primitive } from "./surface";
import { takes, type Takes } from "./takers";

/** A row: a name, the parameter's declaration (`p: <type>` with an optional type parameter list),
 * whether `takes` must call it a taker, and whether the allow-list must refuse it. A library type
 * is walked like the program's, so `Promise<string>` is a taker (its `then` hands a callback a
 * `reason: any`), as is `void` (a function typed `() => void` may give anything). */
const ROWS: readonly (readonly [string, string, boolean, boolean])[] = [
  // Refused by both: anything a box or a node can be handed to.
  ["unknown", "(p: unknown)", true, true],
  ["any", "(p: any)", true, true],
  ["object", "(p: object)", true, true],
  ["{}", "(p: {})", true, true],
  ["Object", "(p: Object)", true, true],
  ["Record<string, unknown>", "(p: Record<string, unknown>)", true, true],
  ["Readonly<Record<string, unknown>>", "(p: Readonly<Record<string, unknown>>)", true, true],
  ["unknown[]", "(p: unknown[])", true, true],
  ["readonly unknown[]", "(p: readonly unknown[])", true, true],
  ["ServerNumber", "(p: ServerNumber)", true, true],
  ["a list of boxes", "(p: readonly ServerNumber[])", true, true],
  ["JsonOut", "(p: JsonOut)", true, true],
  ["a list of nodes", "(p: readonly JsonOut[])", true, true],
  ["a union with a taker: string | object", "(p: string | object)", true, true],
  ["a union with a taker: string | readonly unknown[]", "(p: string | readonly unknown[])", true, true],
  ["a union with a box: ServerNumber | string", "(p: ServerNumber | string)", true, true],
  ["a union with undefined: unknown[] | undefined", "(p?: unknown[])", true, true],
  ["an intersection of takers", "(p: readonly string[] & readonly unknown[])", true, true],
  ["a generic T, unconstrained", "<T>(p: T)", true, true],
  ["a generic T extends object", "<T extends object>(p: T)", true, true],
  ["a generic T extends ServerNumber", "<T extends ServerNumber>(p: T)", true, true],
  ["a type alias of unknown", "(p: Anything)", true, true],
  ["a tuple with a taker: [string, unknown]", "(p: [string, unknown])", true, true],
  ["a tuple of a box: [ServerNumber]", "(p: [ServerNumber])", true, true],
  ["a record of unknown members: { readonly a: unknown }", "(p: { readonly a: unknown })", true, true],
  ["an optional unknown member: { a?: unknown }", "(p: { a?: unknown })", true, true],
  ["a class instance with an unknown member", "(p: Holder)", true, true],
  ["a callback that takes a box", "(p: (x: ServerNumber) => void)", true, true],
  ["a callback that takes unknown", "(p: (x: unknown) => void)", true, true],
  ["a callback that takes a taker, nested", "(p: { readonly on: (x: readonly unknown[]) => void })", true, true],
  ["a promise of unknown", "(p: Promise<unknown>)", true, true],
  ["a map of unknown", "(p: Map<string, unknown>)", true, true],
  ["a record of unknown, as an index", "(p: { readonly [key: string]: unknown })", true, true],
  ["a record whose members hold an unknown", "(p: { readonly [key: string]: { readonly held: unknown } })", true, true],
  // The round-2 review's survivors (MA2), and kinds a deny-check cut short.
  ["Readonly<{ v: unknown }> (N1)", "(p: Readonly<{ v: unknown }>)", true, true],
  ["Record<\"v\", unknown> (N2)", '(p: Record<"v", unknown>)', true, true],
  ["NonNullable<T> (N3)", "<T>(p: NonNullable<T>)", true, true],
  ["Error, its cause (N5)", "(p: Error)", true, true],
  ["a conditional over T (N9)", "<T>(p: T extends number ? never : T)", true, true],
  ["Partial<{ v: unknown }> (N10)", "(p: Partial<{ v: unknown }>)", true, true],
  ["PromiseRejectedResult (N11)", "(p: PromiseRejectedResult)", true, true],
  ["T & object (N12)", "<T>(p: T & object)", true, true],
  ["a mapped type over T", "<T>(p: { readonly [K in keyof T]: T[K] })", true, true],
  ["T[keyof T]", "<T>(p: T[keyof T])", true, true],
  ["an external library's type: React's ReactNode", "(p: ReactNode)", true, true],
  ["the box's own shape with an optional member (assignability decides: W4)", "(p: { readonly a?: string; toJSON(): never })", true, true],
  ["a constructor that takes unknown", "(p: new (x: unknown) => { readonly a: string })", true, true],
  ["a function whose this is unknown", "(p: (this: unknown) => string)", true, true],
  // Accepted by takes; the allow-list accepts the primitives alone.
  ["string", "(p: string)", false, false],
  ["number", "(p: number)", false, false],
  ["boolean", "(p: boolean)", false, false],
  ["bigint", "(p: bigint)", false, false],
  ["symbol", "(p: symbol)", false, true],
  ["null", "(p: null)", false, false],
  ["undefined", "(p: undefined)", false, false],
  ["void", "(p: void)", true, true],
  ["never", "(p: never)", false, true],
  ["a literal union", '(p: "a" | "b")', false, false],
  ["string | number", "(p: string | number)", false, false],
  ["string[]", "(p: string[])", false, true],
  ["readonly string[]", "(p: readonly string[])", false, true],
  ["a record of strings", "(p: { readonly [key: string]: string })", false, true],
  ["a record of objects of strings", "(p: { readonly [key: string]: { readonly held: string } })", false, true],
  ["an object of strings", "(p: { readonly a: string; readonly b: number })", false, true],
  ["an optional string member", "(p: { readonly a?: string })", false, true],
  ["a list of objects of strings", "(p: readonly { readonly op: string; readonly name: string }[])", false, true],
  ["an intersection of objects of strings", "(p: { readonly a: string } & { readonly b: number })", false, true],
  ["a type alias of string", "(p: Name)", false, false],
  ["a branded string", '(p: string & { readonly __brand: "id" })', false, true],
  ["a tuple of strings and numbers", "(p: [string, number])", false, true],
  ["Function", "(p: Function)", true, true],
  ["a callback that takes a string (its void return may be anything)", "(p: (x: string) => void)", true, true],
  ["a generic T extends string", "<T extends string>(p: T)", false, true],
  ["a generic T extends a literal union", '<T extends "a" | "b">(p: T)', false, true],
  ["Date (a class instance)", "(p: Date)", true, true],
  ["AbortSignal (a class instance of the library, with `reason: any`)", "(p: AbortSignal)", true, true],
  ["AddEventListenerOptions (weak)", "(p?: AddEventListenerOptions)", true, true],
  ["Event", "(p: Event)", true, true],
  ["a map of strings", "(p: Map<string, string>)", true, true],
  ["a promise of a string", "(p: Promise<string>)", true, true],
  ["a class instance of strings", "(p: Plain)", false, true],
];

const SOURCE = `import type { ReactNode } from "react";
import type { ServerNumber } from "./api/box";
import type { JsonOut } from "./api/json";
type Anything = unknown;
type Name = string;
declare class Holder { readonly held: unknown; }
declare class Plain { readonly name: string; }
${ROWS.map(([, declaration], index) => `export declare function row${String(index)}${declaration}: void;`).join("\n")}
export type { ServerNumber, JsonOut, ReactNode };
`;

let checked: Checked;
let taker: Takes;

/** The type of the first parameter of a row's function. */
function parameterOf(index: number): ts.Type {
  const { checker } = checked;
  const symbol = checker.getExportsOfModule(checker.getSymbolAtLocation(checked.source("src/probe-takers.ts")) as ts.Symbol).find((member) => member.name === `row${String(index)}`);
  const [signature] = symbol === undefined ? [] : checker.getTypeOfSymbol(symbol).getCallSignatures();
  const [parameter] = signature?.getParameters() ?? [];
  if (parameter === undefined) {
    throw new Error(`row${String(index)} has no parameter`);
  }
  return checker.getTypeOfSymbol(parameter);
}

beforeAll(() => {
  checked = check({ "src/probe-takers.ts": SOURCE });
  taker = takes(checked.program, checked.checker);
}, 120_000);

describe("the two checks of a parameter type, against a table", () => {
  it("compiles the table", () => {
    expect(checked.codes("src/probe-takers.ts")).toEqual([]);
  });

  it.each(ROWS.map(([name, declaration, taker_, refused], index) => [name, declaration, taker_, refused, index] as const))("%s: %s", (_name, _declaration, isTaker, refused, index) => {
    expect(taker(parameterOf(index))).toBe(isTaker);
    expect(!primitive(parameterOf(index))).toBe(refused);
  });

  it("has rows in both directions, many of each, and the allow-list refuses every taker", () => {
    expect(ROWS.filter(([, , isTaker]) => isTaker).length).toBeGreaterThanOrEqual(25);
    expect(ROWS.filter(([, , isTaker]) => !isTaker).length).toBeGreaterThanOrEqual(15);
    expect(ROWS.filter(([, , , refused]) => !refused).length).toBeGreaterThanOrEqual(9);
    expect(ROWS.filter(([, , isTaker, refused]) => isTaker && !refused)).toEqual([]);
  });
});
