/**
 * Every data object `src/api/` exports is frozen, through and through (m14 of round 4, the second
 * instance of m10's class): TypeScript's `readonly` is not held through an alias (`const loose: {
 * read: string } = FEEDS["path:proposal"]; loose.read = "value"` compiles), so a table that code
 * reads its behaviour from is written to at run time, or it is frozen. The test walks the exports of
 * every module of `src/api/` (a new module is walked without a line here), so an object added
 * unfrozen fails it.
 *
 * What it walks: each export that is an object (a plain object, a list, a regular expression, the
 * client), and each object in it; a function and a class are not data (a function's own members are
 * the module's, and `Object.freeze` of a class is each module's own call, e.g. the box's); a module
 * namespace (`routes`) is no object of its own.
 */
import { describe, expect, it } from "vitest";

const MODULES = import.meta.glob<Record<string, unknown>>(["../../src/api/*.ts", "../../src/api/generated/*.ts"], { eager: true });

/** The unfrozen objects reachable from a value, by their path. */
function unfrozen(value: unknown, at: string, seen: Set<unknown>): string[] {
  if (typeof value !== "object" || value === null || seen.has(value)) {
    return [];
  }
  seen.add(value);
  const found = Object.isFrozen(value) ? [] : [at];
  for (const key of Reflect.ownKeys(value)) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor !== undefined && "value" in descriptor) {
      found.push(...unfrozen(descriptor.value, `${at}.${String(key)}`, seen));
    }
  }
  return found;
}

/** Each module's exports that are data: objects, but a module namespace. */
function data(): { readonly module: string; readonly name: string; readonly value: object }[] {
  return Object.entries(MODULES).flatMap(([file, exports]) =>
    Object.entries(exports).flatMap(([name, value]) => (typeof value === "object" && value !== null && !(Symbol.toStringTag in value && value[Symbol.toStringTag] === "Module") ? [{ module: file.replace("../../src/api/", ""), name, value }] : [])),
  );
}

describe("every data object src/api/ exports is frozen, through and through (m14 of round 4)", () => {
  it("walks every module of src/api/ and finds the data it exports (it is not vacuous)", () => {
    expect(Object.keys(MODULES).length).toBeGreaterThanOrEqual(12);
    const names = data().map(({ module, name }) => `${module}:${name}`);
    for (const known of ["provenance.ts:FEEDS", "client.ts:REFUSALS", "client.ts:SEALED_READERS", "client.ts:TOKEN_FORM", "client.ts:OPERATOR_FORM", "client.ts:CSRF_FORM", "curator.ts:ACTIVITY_EVENTS", "operator.ts:operator"]) {
      expect(names, known).toContain(known);
    }
  });

  it("each is frozen, and so is every object inside it", () => {
    const seen = new Set<unknown>();
    expect(data().flatMap(({ module, name, value }) => unfrozen(value, `${module}:${name}`, seen))).toEqual([]);
  });

  it("the walk sees an unfrozen object, one nested in a frozen one, and a list (its own controls)", () => {
    expect(unfrozen({ a: 1 }, "x", new Set())).toEqual(["x"]);
    expect(unfrozen(Object.freeze({ a: { b: 1 } }), "x", new Set())).toEqual(["x.a"]);
    expect(unfrozen(Object.freeze({ a: Object.freeze([{}]) }), "x", new Set())).toEqual(["x.a.0"]);
    expect(unfrozen(/x/u, "x", new Set())).toEqual(["x"]);
    expect(unfrozen(Object.freeze({ a: 1 }), "x", new Set())).toEqual([]);
  });
});
