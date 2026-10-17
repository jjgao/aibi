/**
 * The text box, the map of boxed keys and the takers (D426).
 *
 * - `ServerText` throws on every conversion, in the language and in React's and the DOM's positions,
 *   and holds its text in an ES private field alone; a forged one is refused.
 * - **The brute force of the key paths** (m4 of round 3): every own function of the ECMAScript
 *   namespaces and constructors that reads or enumerates keys (`Object`, `Reflect`, `Array`, `JSON`,
 *   `Map`, `Set`, `String`, `Iterator`; `Promise`'s are asynchronous, and read no key synchronously), with a walked map as each argument, and
 *   `URLSearchParams`, `Headers`, `FormData`, `Map`, `Set`, `for...in`, a spread, `structuredClone`
 *   and `JSON.stringify` over it: no raw key comes out of any (a call may throw, or give nothing).
 *   The same over `entriesOf`'s list: the keys are boxes, which throw where a string is needed.
 * - The takers: `kindOf` (a member, a miss, `Severity` read fail-closed), `idOf` (the generated
 *   grammar and the `__` rule), `choiceFor` and `choiceText` (D432), `sameText`, `compareText`, `memberOf`.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { createElement, type ReactNode } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BoxedMap, entriesOf, FORGED_TEXT, OPAQUE_TEXT, readText, ServerText } from "../../src/api/box";
import { decode } from "../../src/api/decode";
import { GRAMMARS, VOCABULARIES } from "../../src/api/generated/shapes";
import { Choice, choiceFor, choiceText, compareText, idOf, kindOf, memberOf, ofTable, sameText } from "../../src/api/text";
import { walkComponent } from "../../src/api/typed";

/** The marker of every raw key and text: a hit anywhere is a leak. */
const MARK = "rawkey";

/** A walked map of three data keys holding the marker (a `CatalogHit`'s `facets`). */
function walkedMap(): BoxedMap<unknown> {
  const answers = (JSON.parse(readFileSync(path.join(import.meta.dirname, "../fixtures/orchard-answers.json"), "utf8")) as { answers: { operation: string; body: string }[] }).answers;
  const search = JSON.parse(answers.find((answer) => answer.operation === "search_catalog")?.body ?? "{}") as { hits: Record<string, unknown>[] };
  const [hit] = search.hits;
  if (hit === undefined) {
    throw new Error("no hit");
  }
  hit["facets"] = { [`${MARK}.a`]: [{ data: "x" }], [`${MARK}.b`]: [], [`${MARK}.c`]: [] };
  const hits = JSON.stringify(search);
  const found = walkComponent("CatalogHits", decode(hits));
  if (!found.ok) {
    throw new Error("the hits did not walk");
  }
  const facets = (found.value as { hits: { facets: unknown }[] }).hits[0]?.facets;
  if (!BoxedMap.holds(facets)) {
    throw new Error("no map");
  }
  return facets;
}

/** A walked text box holding `text`. */
function boxed(text: string): ServerText {
  const found = walkComponent({ t: "str" }, text);
  if (!found.ok || !ServerText.holds(found.value)) {
    throw new Error("no box");
  }
  return found.value;
}

/** Every string reachable from a value without converting a box: own keys and values, a map's and
 * a set's members, an iterable's items, a `URLSearchParams`' and a `Headers`' entries. */
function strings(value: unknown, seen = new Set<unknown>()): string[] {
  if (typeof value === "string") {
    return [value];
  }
  if (typeof value !== "object" || value === null || seen.has(value) || ServerText.holds(value)) {
    return [];
  }
  seen.add(value);
  const found: string[] = [];
  if (value instanceof URLSearchParams || value instanceof Headers || value instanceof FormData) {
    for (const [key, member] of value.entries()) {
      found.push(key, ...strings(member, seen));
    }
  }
  if (value instanceof Map) {
    for (const [key, member] of value) {
      found.push(...strings(key, seen), ...strings(member, seen));
    }
  } else if (value instanceof Set || Array.isArray(value)) {
    for (const member of value) {
      found.push(...strings(member, seen));
    }
  }
  for (const key of Reflect.ownKeys(value)) {
    found.push(...(typeof key === "string" ? [key] : []));
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor !== undefined && "value" in descriptor) {
      found.push(...strings(descriptor.value, seen));
    }
  }
  return found;
}

/** Every own function of the namespaces and constructors that read keys. */
const NAMESPACES: Readonly<Record<string, object>> = { Object, Reflect, Array, JSON, Map, Set, String, ...(Reflect.has(globalThis, "Iterator") ? { Iterator: Reflect.get(globalThis, "Iterator") as object } : {}) };

const BUILTINS: readonly (readonly [string, (...given: unknown[]) => unknown])[] = Object.entries(NAMESPACES).flatMap(([name, namespace]) =>
  Reflect.ownKeys(namespace).flatMap((key) => {
    const member: unknown = Reflect.get(namespace, key);
    // `Array.fromAsync` is asynchronous (its rejection is no answer here), and reads no key synchronously.
    return typeof key === "string" && typeof member === "function" && !["prototype", "constructor", "fromAsync"].includes(key)
      ? [[`${name}.${key}`, (...given: unknown[]): unknown => Reflect.apply(member as (...a: unknown[]) => unknown, namespace, given)] as const]
      : [];
  }),
);

/** The forms of syntax and the constructors over a value. */
const FORMS: readonly (readonly [string, (value: unknown) => unknown])[] = [
  ["for...in", (value) => { const keys: string[] = []; for (const key in value as object) keys.push(key); return keys; }],
  ["a spread", (value) => ({ ...(value as object) })],
  ["Object.assign", (value) => Object.assign({}, value)],
  ["structuredClone", (value) => structuredClone(value)],
  ["JSON.stringify", (value) => JSON.stringify(value)],
  ["new URLSearchParams", (value) => new URLSearchParams(value as Record<string, string>)],
  ["new Headers", (value) => new Headers(value as Record<string, string>)],
  ["new Map", (value) => new Map(value as Iterable<[unknown, unknown]>)],
  ["new Set", (value) => new Set(value as Iterable<unknown>)],
  ["Array.from", (value) => Array.from(value as ArrayLike<unknown>)],
  ["a template", (value) => `<${value as string}>`],
];

/** What a call gives, or nothing when it throws (a box's conversion, a value of the wrong kind). */
function attempt(run: () => unknown): string[] {
  try {
    return strings(run());
  } catch {
    return [];
  }
}

describe("the brute force of a map's key paths: no raw key comes out (m4 of round 3, MA1's root fix)", () => {
  it("is not vacuous: the map holds three entries, and the builtins are many", () => {
    expect(entriesOf(walkedMap())).toHaveLength(3);
    expect(BUILTINS.length).toBeGreaterThan(45);
    expect(BUILTINS.map(([name]) => name)).toEqual(expect.arrayContaining(["Object.keys", "Object.entries", "Object.getOwnPropertyNames", "Reflect.ownKeys", "Object.fromEntries", "Object.groupBy"]));
  });

  it.each(BUILTINS.map(([name, call]) => [name, call] as const))("%s, the map as each argument", (_name, call) => {
    const map = walkedMap();
    const leaked = [attempt(() => call(map)), attempt(() => call(map, map)), attempt(() => call({}, map)), attempt(() => call(map, (x: unknown) => x))].flat();
    expect(leaked.filter((found) => found.includes(MARK))).toEqual([]);
  });

  it.each(BUILTINS.map(([name, call]) => [name, call] as const))("%s, entriesOf's list as each argument", (_name, call) => {
    const entries = entriesOf(walkedMap());
    const leaked = [attempt(() => call(entries)), attempt(() => call(entries, (pair: readonly [unknown, unknown]) => pair[0])), attempt(() => call({}, entries))].flat();
    expect(leaked.filter((found) => found.includes(MARK))).toEqual([]);
  });

  it.each(FORMS.map(([name, form]) => [name, form] as const))("%s over the map, and over entriesOf's list", (_name, form) => {
    const map = walkedMap();
    expect([attempt(() => form(map)), attempt(() => form(entriesOf(map)))].flat().filter((found) => found.includes(MARK))).toEqual([]);
  });

  it("the controls: the same paths over a plain object of the keys leak them (the brute force sees a leak)", () => {
    const plain = { [`${MARK}-a`]: 1 };
    expect(attempt(() => Object.keys(plain))).toContain(`${MARK}-a`);
    expect(attempt(() => new URLSearchParams({ [`${MARK}-a`]: "1" }))).toContain(`${MARK}-a`);
    expect(attempt(() => new Headers({ [`${MARK}-a`]: "1" }))).toContain(`${MARK}-a`);
  });

  it("the keys are boxes, read through the takers alone", () => {
    const keys = entriesOf(walkedMap()).map(([key]) => key);
    expect(keys.every((key) => ServerText.holds(key))).toBe(true);
    expect(keys.map(readText).sort()).toEqual([`${MARK}.a`, `${MARK}.b`, `${MARK}.c`]);
    expect(Object.isFrozen(entriesOf(walkedMap()))).toBe(true);
    expect(entriesOf(walkedMap()).every((entry) => Object.isFrozen(entry))).toBe(true);
  });
});

describe("ServerText: opaque, as a ServerNumber is", () => {
  it.each([
    ["String", (t: ServerText) => String(t)],
    ["a template", (t: ServerText) => `<${t as unknown as string}>`],
    ["+", (t: ServerText) => "<" + (t as unknown as string)],
    ["toString", (t: ServerText) => t.toString()],
    ["valueOf", (t: ServerText) => t.valueOf()],
    ["toLocaleString", (t: ServerText) => t.toLocaleString()],
    ["JSON.stringify", (t: ServerText) => JSON.stringify({ t })],
    ["a comparison", (t: ServerText) => (t as unknown as string) < "b"],
    ["localeCompare", (t: ServerText) => "a".localeCompare(t as unknown as string)],
    ["sort", (t: ServerText) => [t, boxed("b")].sort()],
    ["Intl.Collator", (t: ServerText) => new Intl.Collator().compare(t as unknown as string, "b")],
    ["encodeURIComponent", (t: ServerText) => encodeURIComponent(t as unknown as string)],
    ["new URL", (t: ServerText) => new URL(t as unknown as string, "http://x/")],
    ["RegExp.test", (t: ServerText) => /a/u.test(t as unknown as string)],
  ])("%s throws, in fixed words", (_case, convert) => {
    expect(() => convert(boxed(`${MARK}-text`))).toThrow(new TypeError(OPAQUE_TEXT));
  });

  it.each([
    ["a child", (t: ServerText) => createElement("p", null, t as unknown as ReactNode)],
    ["a title", (t: ServerText) => createElement("p", { title: t as unknown as string })],
    ["an href", (t: ServerText) => createElement("a", { href: t as unknown as string })],
    ["a className", (t: ServerText) => createElement("p", { className: t as unknown as string })],
    ["an aria-label", (t: ServerText) => createElement("p", { "aria-label": t as unknown as string })],
    ["a key", (t: ServerText) => createElement("ul", null, [createElement("li", { key: t as unknown as string })])],
  ])("React refuses a box as %s (it throws; nothing raw is rendered)", (_case, element) => {
    let rendered = "";
    expect(() => {
      rendered = renderToString(element(boxed(`${MARK}-react`)));
    }).toThrow();
    expect(rendered).not.toContain(MARK);
  });

  it("holds its text in an ES private field alone, and is frozen", () => {
    const box = boxed(`${MARK}-private`);
    expect(Reflect.ownKeys(box)).toEqual([]);
    expect(structuredClone(box)).toEqual({});
    expect(Object.isFrozen(box)).toBe(true);
    expect(readText(box)).toBe(`${MARK}-private`);
  });

  it("refuses a forged one", () => {
    type Forge = new (...given: unknown[]) => ServerText;
    expect(() => new (ServerText as unknown as Forge)(Symbol("aibi.server-text"), "x")).toThrow(new TypeError(FORGED_TEXT));
    expect(() => new (BoxedMap as unknown as Forge)(Symbol("aibi.server-text"), [])).toThrow(new TypeError(FORGED_TEXT));
    expect(ServerText.holds({})).toBe(false);
    expect(() => readText({} as ServerText)).toThrow(TypeError);
  });

  it("is never === another of the same text: sameText is the equality, compareText the order", () => {
    const [a, b, c] = [boxed("b"), boxed("b"), boxed("a")];
    expect(a === b).toBe(false);
    expect(sameText(a, b)).toBe(true);
    expect(sameText(a, c)).toBe(false);
    expect([a, c, boxed("Z"), boxed("é")].sort(compareText).map(readText)).toEqual(["Z", "a", "b", "é"]);
    expect(compareText(a, b)).toBe(0);
  });
});

describe("the takers", () => {
  it("kindOf gives a vocabulary's own member, null on a miss, and reads Severity fail-closed", () => {
    expect(kindOf(boxed("available"), "ApplicableAnalysis.status")).toBe("available");
    expect(kindOf(boxed("availablE"), "ApplicableAnalysis.status")).toBe(null);
    expect(kindOf(boxed("warn"), "Severity")).toBe("warn");
    expect(kindOf(boxed("critical"), "Severity")).toBe("block");
    expect(kindOf(boxed(""), "Severity")).toBe("block");
    expect(kindOf(boxed("demo.SOMETHING"), "Caveat.code")).toBe(null);
    expect(VOCABULARIES.Severity).toEqual(["info", "warn", "block"]);
  });

  it.each([
    ["orchard", true],
    ["a", true],
    ["a_1", true],
    [`a${"b".repeat(63)}`, true],
    [`a${"b".repeat(64)}`, false],
    ["a__b", false],
    ["A", false],
    ["1a", false],
    ["_a", false],
    ["a\n", false],
    ["a#b", false],
    ["a?b", false],
    ["a/b", false],
    ["a‮", false],
    ["", false],
  ])("idOf(%j) is a DatasetId: %s", (text, valid) => {
    const id = idOf(boxed(text));
    expect(id === null ? false : id === text).toBe(valid);
  });

  it("idOf's grammar is the document's (CatalogHit.dataset: its pattern, its length, its not)", () => {
    expect(GRAMMARS.dataset).toEqual({ p: "^[a-z][a-z0-9_]{0,63}$", m: 64, x: ["__"] });
  });

  it("choiceFor gives an opaque choice of a role for a text of its grammar, and null otherwise (D432)", () => {
    const source = Symbol("answer");
    const choice = choiceFor(boxed("trees.height_m"), "leaf", source);
    expect(choice).not.toBeNull();
    expect(typeof choice).toBe("object");
    expect(Reflect.ownKeys(choice ?? {})).toEqual([]);
    expect(Object.isFrozen(choice)).toBe(true);
    expect(choiceFor(boxed("trees"), "leaf", source)).toBeNull();
    expect(choiceFor(boxed("trees"), "view", source)).toBeNull();
    expect(choiceFor(boxed("trees.height_m"), "unit", source)).toBeNull();
    expect(choiceFor(boxed("trees"), "unit", source)).not.toBeNull();
    expect(choiceFor(boxed("a__b"), "unit", source)).toBeNull();
    expect(choiceFor(boxed("Trees"), "unit", source)).toBeNull();
    expect(() => renderToString(createElement("p", null, choice as unknown as ReactNode))).toThrow();
  });

  it("choiceText reads a choice at its own role alone, with the answer it was minted from; nothing for another role or a forgery (D432)", () => {
    const source = Symbol("answer");
    const leaf = choiceFor(boxed("trees.variety"), "leaf", source);
    if (leaf === null) {
      throw new Error("no choice");
    }
    expect(choiceText(leaf, "leaf")).toEqual({ text: "trees.variety", source });
    expect(choiceText(leaf, "leaf")?.source).toBe(source);
    expect(choiceText(leaf, "view")).toBeNull();
    expect(choiceText(leaf, "unit")).toBeNull();
    for (const forged of [{}, Object.create(leaf) as object, { ...(leaf as object) }, new Proxy(leaf, {}), null, "trees.variety", 1]) {
      expect(choiceText(forged as typeof leaf, "leaf")).toBeNull();
    }
    expect(Choice.holds(leaf)).toBe(true);
    expect(Choice.holds({})).toBe(false);
  });

  it("ofTable compares a column id's table part with a table's id, exactly", () => {
    expect(ofTable(boxed("trees.variety"), boxed("trees"))).toBe(true);
    expect(ofTable(boxed("trees.variety"), boxed("tree"))).toBe(false);
    expect(ofTable(boxed("treesx.variety"), boxed("trees"))).toBe(false);
    expect(ofTable(boxed("harvests.tree_id"), boxed("trees"))).toBe(false);
    expect(ofTable(boxed(".trees"), boxed(""))).toBe(false);
    expect(ofTable(boxed("trees"), boxed("trees"))).toBe(false);
  });

  it("memberOf reads a key's exact text, not a prefix of it: denominator beside denominator_definition and denominator_text (m4 of round 1)", () => {
    const found = walkComponent({ t: "map", v: { t: "str" } }, decode('{"denominator_definition":"definition","denominator_text":"text","denominator":"exact"}'));
    const map = found.ok && BoxedMap.holds(found.value) ? found.value : null;
    if (map === null) {
      throw new Error("no map");
    }
    const text = (name: string): string => {
      const member = memberOf(map, name);
      return member.found && ServerText.holds(member.value) ? readText(member.value) : "none";
    };
    expect(text("denominator")).toBe("exact");
    expect(text("denominator_text")).toBe("text");
    expect(text("denominator_definition")).toBe("definition");
    expect([text("denominator_"), text("denom"), text("")]).toEqual(["none", "none", "none"]);
  });

  it("memberOf gives a member's value by the page's own word, or found: false", () => {
    const map = walkedMap();
    expect(memberOf(map, `${MARK}.b`)).toEqual({ found: true, value: [] });
    expect(memberOf(map, "numerator")).toEqual({ found: false });
    expect(Object.isFrozen(memberOf(map, `${MARK}.c`))).toBe(true);
  });
});
