/**
 * What the typed answers' tests share (D426): the fixtures (the golden envelopes and the orchard's
 * answers), the bodies made from the table, built to reach every position of it (`synthetic`), the
 * misses made from the table (`variants`) and the static list of the constraints the table states
 * (`constraintsOf`) that the coverage assertion holds the misses to. `typed.test.ts` holds the walk
 * to the table; `typed-oracle.test.ts` holds the table to an independent validator of the document.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { decode } from "../../src/api/decode";
import { OPERATIONS, type Shape, SHAPES } from "../../src/api/generated/shapes";
import { type TypedOperation, walkComponent } from "../../src/api/typed";

export const FIXTURES = path.join(import.meta.dirname, "../fixtures");

/** The golden envelopes, each as its own text (one a line: numbers keep their source text). */
export const ENVELOPES: readonly (readonly [string, string])[] = readFileSync(path.join(FIXTURES, "envelopes.json"), "utf8")
  .split("\n")
  .flatMap((line) => {
    const found = /^("(?:[^"\\]|\\.)*"): (\{.*\}),?$/u.exec(line);
    return found?.[1] === undefined || found[2] === undefined ? [] : [[found[1], found[2]] as const];
  });

export interface Written {
  readonly operation: TypedOperation;
  readonly status: number;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string;
  /** The request a builder answer answers, as the page sends it (D433: the builder's seven alone). */
  readonly request?: string;
}

export const ORCHARD: readonly Written[] = (JSON.parse(readFileSync(path.join(FIXTURES, "orchard-answers.json"), "utf8")) as { answers: Written[] }).answers;

/** The component of an orchard answer: its operation's 200, or the refusals'. */
export const componentOf = (answer: Written): string => (answer.status === 200 ? OPERATIONS[answer.operation] : "Refusals");

export const REF = (name: string): Shape => ({ t: "ref", n: name });

/** Whether a JSON value walks by a shape. */
export function walkOf(shape: Shape, value: unknown): boolean {
  return walkComponent(shape, decode(JSON.stringify(value))).ok;
}

export const UNMADE: unique symbol = Symbol("unmade");

/** Every enum member of the table: the keys a map tries first (a vocabulary-keyed map, and a
 * check's `propertyNames`, take one of them). */
export const MEMBERS: readonly string[] = [
  ...new Set(
    JSON.stringify(SHAPES)
      .match(/"t":"enum","v":\[[^\]]*\]/gu)
      ?.flatMap((found) => (JSON.parse(`{${found}}`) as { v: string[] }).v) ?? [],
  ),
].sort((a, b) => Number(!a.startsWith("/")) - Number(!b.startsWith("/")));

/** The keys a map of data tries, in turn: one a pattern-less map takes, a pointer, a dotted pair
 * (`CatalogHit.facets`), then the table's members. */
const KEYS: readonly string[] = ["k-raw", "/k-raw", "a.b", ...MEMBERS];

/**
 * What the checks of an `allOf` ask of the value its first member gives, member by member (the keys
 * of a map their `propertyNames` allow, the constants they fix, the length their `prefixItems` need),
 * so that a body made for the first member reaches the positions of the checks too.
 */
export interface Hint {
  readonly keys?: readonly string[];
  readonly length?: number;
  readonly consts?: Readonly<Record<string, unknown>>;
  readonly members?: Readonly<Record<string, Hint>>;
}

const present = (hint: Hint): boolean => Object.keys(hint).length > 0;

/** Hints merged: keys joined, the longest length, constants and members by name. */
export function mergeHints(hints: readonly (Hint | undefined)[]): Hint {
  const keys = new Set<string>();
  let length = 0;
  const consts: Record<string, unknown> = {};
  const named = new Map<string, Hint[]>();
  for (const hint of hints) {
    for (const key of hint?.keys ?? []) {
      keys.add(key);
    }
    length = Math.max(length, hint?.length ?? 0);
    Object.assign(consts, hint?.consts);
    for (const [name, member] of Object.entries(hint?.members ?? {})) {
      named.set(name, [...(named.get(name) ?? []), member]);
    }
  }
  const members = Object.fromEntries([...named].map(([name, many]) => [name, mergeHints(many)]));
  return {
    ...(keys.size > 0 ? { keys: [...keys] } : {}),
    ...(length > 0 ? { length } : {}),
    ...(Object.keys(consts).length > 0 ? { consts } : {}),
    ...(Object.keys(members).length > 0 ? { members } : {}),
  };
}

/** What a check asks of a value, from the table alone. */
export function hintOf(shape: Shape, depth = 0): Hint {
  if (depth > 8) {
    return {};
  }
  switch (shape.t) {
    case "ref": {
      const found = SHAPES[shape.n];
      return found === undefined ? {} : hintOf(found, depth + 1);
    }
    case "rec": {
      const members: Record<string, Hint> = {};
      const consts: Record<string, unknown> = {};
      for (const [name, member] of Object.entries(shape.p)) {
        if (member.t === "const") {
          consts[name] = member.v;
        }
        const sub = hintOf(member, depth + 1);
        if (present(sub)) {
          members[name] = sub;
        }
      }
      return { ...(Object.keys(consts).length > 0 ? { consts } : {}), ...(Object.keys(members).length > 0 ? { members } : {}) };
    }
    case "map": {
      let names = shape.k;
      while (names?.t === "ref") {
        names = SHAPES[names.n];
      }
      const entry = shape.pp?.[0]?.[1] ?? shape.v;
      const inner = hintOf(entry, depth + 1);
      return { ...(names?.t === "enum" ? { keys: names.v } : {}), ...(inner.consts === undefined ? {} : { consts: inner.consts }) };
    }
    case "arr":
      return shape.pi === undefined ? {} : { length: shape.pi.length };
    case "all":
      return mergeHints(shape.o.map((member) => hintOf(member, depth + 1)));
    default:
      return {};
  }
}

/** How a body is made where it has a choice: `prefer` is the arm of an `or` taken first (past the
 * default order, a `null` last) and, by its parity, the kind of segment (text, then data). */
export interface Plan {
  readonly prefer: number;
  /** An alternative whose arm is forced (by its index in the table's `o`): the arms past `PLANS`' reach (D434). */
  readonly force?: { readonly or: Shape; readonly arm: number };
}

/** The order `synthetic` takes an alternative's arms in, which a plan's `prefer` indexes: the table's,
 * a `null` last. */
export const armOrder = (shape: Extract<Shape, { readonly o: readonly Shape[] }>): readonly Shape[] => [...shape.o].sort((a, b) => Number(a.t === "null") - Number(b.t === "null"));

/** The plans the tests make a body of every component under: each arm of an `or`, each segment kind. */
export const PLANS: readonly Plan[] = [{ prefer: 0 }, { prefer: 1 }, { prefer: 2 }];

const PLAN: Plan = { prefer: 0 };

/** The shape a map's entry is walked by: the first pattern its key matches, else the values'. */
export const entryShape = (shape: Extract<Shape, { readonly t: "map" }>, key: string): Shape => shape.pp?.find(([text]) => new RegExp(text, "u").test(key))?.[1] ?? shape.v;

/** A JSON value of a shape (numbers as JS numbers, decoded into boxes later) that the walk takes:
 * each part made is walked by its own shape, an optional member that does not walk is left out,
 * a list whose item does not is empty, and `minimal` leaves every optional member out (an `allOf`'s
 * checks may refuse one). Map keys are marked (`k-raw`) unless a vocabulary or pattern needs one of
 * the table's members. Built to reach: an `or`'s arm and a segment's kind follow the `plan`, a
 * map has an entry of each distinct shape its keys can take, and the hints of an `allOf`'s checks
 * give the keys, constants and lengths they ask of the first member. */
export function synthetic(shape: Shape, depth: number, minimal = false, plan: Plan = PLAN, hint?: Hint): unknown {
  if (depth > 24) {
    return UNMADE;
  }
  const made = make(shape, depth, minimal, plan, hint);
  return made !== UNMADE && walkOf(shape, made) ? made : UNMADE;
}

function make(shape: Shape, depth: number, minimal: boolean, plan: Plan, hint: Hint | undefined): unknown {
  switch (shape.t) {
    case "ref": {
      const found = SHAPES[shape.n];
      return found === undefined ? UNMADE : synthetic(found, depth + 1, minimal, plan, hint);
    }
    case "json":
    case "any":
      return { "k-raw-json": ["s-raw-json", 1, true, null, { "k-raw-inner": "s-raw-inner" }] };
    case "never":
      return UNMADE;
    case "str":
      return "s-raw";
    case "num":
      return 1;
    case "bool":
      return true;
    case "null":
      return null;
    case "enum":
      return shape.v[0] ?? UNMADE;
    case "const":
      return shape.v;
    case "seg":
      return plan.prefer % 2 === 0 ? { text: "s-raw-text" } : { data: "s-raw-data", truncated: true };
    case "or": {
      const sorted = armOrder(shape);
      const first = plan.force?.or === shape ? shape.o[plan.force.arm] : plan.prefer > 0 ? sorted[plan.prefer] : undefined;
      const order = first === undefined ? sorted : [first, ...sorted.filter((arm) => arm !== first)];
      for (const arm of order) {
        const made = synthetic(arm, depth + 1, minimal, plan, hint);
        if (made !== UNMADE) {
          return made;
        }
      }
      return UNMADE;
    }
    case "all": {
      const [first, ...checks] = shape.o;
      if (first === undefined) {
        return UNMADE;
      }
      const made = make(first, depth + 1, minimal, plan, mergeHints([hint, ...checks.map((check) => hintOf(check))]));
      if (made === UNMADE || walkOf(shape, made)) {
        return made;
      }
      if (Array.isArray(made)) {
        return [];
      }
      // A check's constants put in (`CohortCount.size`'s denominator definition).
      const patched: Record<string, unknown> = { ...(made as Record<string, unknown>) };
      for (const check of checks) {
        for (const [name, member] of check.t === "rec" ? Object.entries(check.p) : []) {
          if (member.t === "const") {
            patched[name] = member.v;
          }
        }
      }
      return walkOf(shape, patched) ? patched : make(first, depth + 1, true, plan, undefined);
    }
    case "arr": {
      const length = shape.i.t === "never" ? (shape.pi?.length ?? 0) : Math.max(shape.pi?.length ?? 1, hint?.length ?? 0);
      const items = Array.from({ length }, (_, index) => synthetic(shape.pi?.[index] ?? shape.i, depth + 1, minimal, plan));
      return items.includes(UNMADE) ? (shape.pi === undefined ? [] : UNMADE) : items;
    }
    case "rec": {
      const out: Record<string, unknown> = {};
      for (const [name, member] of Object.entries(shape.p)) {
        if (minimal && !shape.r.includes(name)) {
          continue;
        }
        const constant = hint?.consts?.[name];
        const made = constant !== undefined && walkOf(member, constant) ? constant : synthetic(member, depth + 1, minimal, plan, hint?.members?.[name]);
        if (made === UNMADE) {
          if (shape.r.includes(name)) {
            return UNMADE;
          }
          continue;
        }
        out[name] = made;
      }
      return out;
    }
    case "map": {
      if (shape.r !== undefined) {
        // A map with required keys (the counts by reason): every one, each by its own shape.
        const out: Record<string, unknown> = {};
        for (const key of shape.r) {
          const made = synthetic(entryShape(shape, key), depth + 1, minimal, plan);
          if (made === UNMADE) {
            return UNMADE;
          }
          out[key] = made;
        }
        return out;
      }
      // An entry of each distinct shape the keys can take, and every key a check asks for.
      const out: Record<string, unknown> = {};
      const taken = new Set<Shape>();
      for (const key of [...(hint?.keys ?? []), ...KEYS]) {
        const entry = entryShape(shape, key);
        if (Object.hasOwn(out, key) || (taken.has(entry) && hint?.keys?.includes(key) !== true)) {
          continue;
        }
        const constant = hint?.consts?.[key];
        const made = constant !== undefined && walkOf(entry, constant) ? constant : synthetic(entry, depth + 1, minimal, plan);
        if (made !== UNMADE && walkOf(shape, { ...out, [key]: made })) {
          out[key] = made;
          taken.add(entry);
        }
      }
      return out;
    }
  }
}

/** The JSON types, as `typeof`-like words. */
export const JSON_TYPES = ["string", "number", "boolean", "null", "array", "object"] as const;
export type JsonType = (typeof JSON_TYPES)[number];

/** One value of each JSON type. */
export const SAMPLE: Readonly<Record<JsonType, unknown>> = { string: "x", number: 1, boolean: true, null: null, array: [], object: {} };

export function jsonTypeOf(value: unknown): JsonType {
  return value === null ? "null" : Array.isArray(value) ? "array" : typeof value === "string" ? "string" : typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : "object";
}

/** The JSON types a shape takes, by the table alone (a walk is not asked). */
export function typesOf(shape: Shape, depth = 0): ReadonlySet<JsonType> {
  if (depth > 40) {
    return new Set(JSON_TYPES);
  }
  switch (shape.t) {
    case "ref": {
      const found = SHAPES[shape.n];
      return found === undefined ? new Set() : typesOf(found, depth + 1);
    }
    case "json":
    case "any":
      return new Set(JSON_TYPES);
    case "never":
      return new Set();
    case "str":
    case "enum":
      return new Set<JsonType>(["string"]);
    case "num":
      return new Set<JsonType>(["number"]);
    case "bool":
      return new Set<JsonType>(["boolean"]);
    case "null":
      return new Set<JsonType>(["null"]);
    case "const":
      return new Set([jsonTypeOf(shape.v)]);
    case "seg":
    case "rec":
    case "map":
      return new Set<JsonType>(["object"]);
    case "arr":
      return new Set<JsonType>(["array"]);
    case "or":
      return new Set(shape.o.flatMap((arm) => [...typesOf(arm, depth + 1)]));
    case "all": {
      const [first] = shape.o;
      return first === undefined ? new Set() : typesOf(first, depth + 1);
    }
  }
}

/** An object without one of its keys. */
export function without(object: Readonly<Record<string, unknown>>, key: string): Record<string, unknown> {
  return Object.fromEntries(Object.entries(object).filter(([name]) => name !== key));
}

/** One miss of a position: where in the table, what is wrong, and the value to put there. */
export type Miss = readonly [at: string, what: string, replacement: unknown];

/** The misses of a value that the shape takes, generated from the table: every constraint the
 * node states, once (`types`: the JSON types the position does not take, by the caller's node when
 * it is a part of it). Where another arm or check could take the changed value, no miss is made. */
export function variants(shape: Shape, value: unknown, at: string, depth: number, types = true): Miss[] {
  if (depth > 40) {
    return [];
  }
  const taken = typesOf(shape);
  const own: Miss[] = types ? JSON_TYPES.filter((type) => !taken.has(type)).map((type) => [at, `a ${type}`, SAMPLE[type]] as const) : [];
  const object = typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  switch (shape.t) {
    case "ref": {
      const found = SHAPES[shape.n];
      return found === undefined ? [] : variants(found, value, shape.n, depth + 1, types);
    }
    case "json":
    case "any":
    case "never":
    case "str":
    case "num":
    case "bool":
    case "null":
      return own;
    case "enum":
      return [...own, [at, "an enum value changed", "~not-a-member"]];
    case "const":
      return [...own, ...(typeof shape.v === "string" ? [[at, "a const changed", `${shape.v}~`] as const] : typeof shape.v === "boolean" ? [[at, "a const flipped", !shape.v] as const] : [])];
    case "seg": {
      if (object === null) {
        return own;
      }
      const misses: Miss[] =
        "text" in object
          ? [[at, "a text segment of a non-string", { text: 1 }], [at, "a text segment with an extra key", { text: "x", extra: 1 }], [at, "a segment with no key", {}]]
          : [[at, "a data segment of a non-string", { data: 1 }], [at, "a data segment, truncated false", { data: "x", truncated: false }], [at, "a data segment with a text beside", { data: "x", text: "x" }], [at, "a segment with no key", {}]];
      return [...own, ...misses];
    }
    case "or": {
      const out = [...own];
      shape.o.forEach((arm, index) => {
        if (!walkOf(arm, value)) {
          return;
        }
        const others = new Set(shape.o.filter((_, other) => other !== index).flatMap((other) => [...typesOf(other)]));
        for (const miss of variants(arm, value, `${at}|${String(index)}`, depth + 1, false)) {
          if (!others.has(jsonTypeOf(miss[2]))) {
            out.push(miss);
          }
        }
      });
      return out;
    }
    case "all": {
      const [first, ...checks] = shape.o;
      return [...own, ...(first === undefined ? [] : variants(first, value, at, depth + 1, false)), ...checks.flatMap((check, index) => variants(check, value, `${at}&${String(index)}`, depth + 1, false))];
    }
    case "arr": {
      if (!Array.isArray(value)) {
        return own;
      }
      const items: unknown[] = value;
      const prefix = shape.pi ?? [];
      const out = [...own];
      const at_ = (index: number, place: string, item: Shape): void => {
        for (const [where, what, replacement] of variants(item, items[index], place, depth + 1)) {
          out.push([where, what, items.map((kept, i) => (i === index ? replacement : kept))]);
        }
      };
      prefix.forEach((item, index) => {
        if (index < items.length) {
          at_(index, `${at}[${String(index)}]`, item);
        }
      });
      if (items.length > prefix.length) {
        at_(prefix.length, `${at}[]`, shape.i);
      }
      if (shape.pi !== undefined && shape.i.t === "never") {
        out.push([at, "an item past the prefix", [...items, 1]]);
      }
      return out;
    }
    case "rec": {
      if (object === null) {
        return own;
      }
      const out = [...own];
      for (const name of shape.r) {
        if (Object.hasOwn(object, name)) {
          out.push([at, `the required member ${name} dropped`, without(object, name)]);
        }
      }
      if (shape.open !== true) {
        out.push([at, "an extra key on a closed record", { ...object, "~extra": 1 }]);
      }
      for (const [name, member] of Object.entries(shape.p)) {
        if (Object.hasOwn(object, name)) {
          for (const [where, what, replacement] of variants(member, object[name], `${at}.${name}`, depth + 1)) {
            out.push([where, what, { ...object, [name]: replacement }]);
          }
        }
      }
      return out;
    }
    case "map": {
      if (object === null) {
        return own;
      }
      const out = [...own];
      const entries = Object.entries(object);
      const sample = entries[0]?.[1] ?? 1;
      for (const key of shape.r ?? []) {
        if (Object.hasOwn(object, key)) {
          out.push([at, `the required key ${key} dropped`, without(object, key)]);
        }
      }
      if (shape.pp !== undefined && shape.v.t === "never") {
        const outside = ["~outside", "/~outside", "~"].find((key) => !(shape.pp ?? []).some(([text]) => new RegExp(text, "u").test(key)));
        if (outside !== undefined) {
          out.push([at, "a key outside every pattern", { ...object, [outside]: sample }]);
        }
      }
      let names = shape.k;
      while (names?.t === "ref") {
        names = SHAPES[names.n];
      }
      if (names?.t === "enum") {
        out.push([at, "a key its propertyNames refuses", { ...object, "~not-a-name": sample }]);
      }
      // The first entry of each distinct shape the keys take (a pattern's, or the values').
      const seen = new Set<Shape>();
      for (const [key, member] of entries) {
        const entry = entryShape(shape, key);
        if (seen.has(entry)) {
          continue;
        }
        seen.add(entry);
        for (const [where, what, replacement] of variants(entry, member, `${at}{}`, depth + 1)) {
          out.push([where, what, { ...object, [key]: replacement }]);
        }
      }
      return out;
    }
  }
}

/**
 * Every constraint the table states, by the labels `variants` gives its misses (a static walk of the
 * table, no body asked): the position and what is wrong there. An enum or a string const among the
 * arms of an `or` that also has a `str` arm is no constraint (the `str` arm takes the changed word:
 * `Caveat.code`).
 */
export function constraintsOf(shape: Shape, at: string, types: boolean, depth: number, out: Set<string>, visited: Set<string>): void {
  if (depth > 40) {
    return;
  }
  if (types && shape.t !== "ref") {
    const taken = typesOf(shape);
    for (const type of JSON_TYPES) {
      if (!taken.has(type)) {
        out.add(`${at} :: a ${type}`);
      }
    }
  }
  switch (shape.t) {
    case "ref": {
      const key = `${shape.n}/${String(types)}`;
      if (visited.has(key)) {
        return;
      }
      visited.add(key);
      const found = SHAPES[shape.n];
      if (found !== undefined) {
        constraintsOf(found, shape.n, types, depth + 1, out, visited);
      }
      return;
    }
    case "enum":
      out.add(`${at} :: an enum value changed`);
      return;
    case "const":
      if (typeof shape.v === "string") {
        out.add(`${at} :: a const changed`);
      }
      if (typeof shape.v === "boolean") {
        out.add(`${at} :: a const flipped`);
      }
      return;
    case "seg":
      for (const what of ["a text segment of a non-string", "a text segment with an extra key", "a data segment of a non-string", "a data segment, truncated false", "a data segment with a text beside", "a segment with no key"]) {
        out.add(`${at} :: ${what}`);
      }
      return;
    case "or": {
      const shadowing = shape.o.some((arm) => arm.t === "str");
      shape.o.forEach((arm, index) => {
        if (shadowing && (arm.t === "enum" || (arm.t === "const" && typeof arm.v === "string"))) {
          return;
        }
        constraintsOf(arm, `${at}|${String(index)}`, false, depth + 1, out, visited);
      });
      return;
    }
    case "all":
      shape.o.forEach((member, index) => {
        constraintsOf(member, index === 0 ? at : `${at}&${String(index - 1)}`, false, depth + 1, out, visited);
      });
      return;
    case "arr":
      (shape.pi ?? []).forEach((item, index) => {
        constraintsOf(item, `${at}[${String(index)}]`, true, depth + 1, out, visited);
      });
      if (shape.i.t !== "never") {
        constraintsOf(shape.i, `${at}[]`, true, depth + 1, out, visited);
      } else if (shape.pi !== undefined) {
        out.add(`${at} :: an item past the prefix`);
      }
      return;
    case "rec":
      for (const name of shape.r) {
        out.add(`${at} :: the required member ${name} dropped`);
      }
      if (shape.open !== true) {
        out.add(`${at} :: an extra key on a closed record`);
      }
      for (const [name, member] of Object.entries(shape.p)) {
        constraintsOf(member, `${at}.${name}`, true, depth + 1, out, visited);
      }
      return;
    case "map": {
      for (const key of shape.r ?? []) {
        out.add(`${at} :: the required key ${key} dropped`);
      }
      if (shape.pp !== undefined && shape.v.t === "never") {
        out.add(`${at} :: a key outside every pattern`);
      }
      let names = shape.k;
      while (names?.t === "ref") {
        names = SHAPES[names.n];
      }
      if (names?.t === "enum") {
        out.add(`${at} :: a key its propertyNames refuses`);
      }
      for (const [, value] of shape.pp ?? []) {
        constraintsOf(value, `${at}{}`, true, depth + 1, out, visited);
      }
      if (shape.v.t !== "never") {
        constraintsOf(shape.v, `${at}{}`, true, depth + 1, out, visited);
      }
      return;
    }
    default:
  }
}

/** A body the tests walk: the component it is of, where it came from, the JSON value. */
export interface Body {
  readonly component: string;
  readonly source: string;
  readonly value: unknown;
}

let made: readonly Body[] | undefined;

/** The alternatives a shape holds itself (through its records, lists, maps and checks; not through a reference). */
export function ownAlternatives(shape: Shape, out: Extract<Shape, { readonly o: readonly Shape[] }>[] = []): Extract<Shape, { readonly o: readonly Shape[] }>[] {
  switch (shape.t) {
    case "or":
    case "all":
      if (shape.t === "or") {
        out.push(shape);
      }
      shape.o.forEach((arm) => ownAlternatives(arm, out));
      return out;
    case "arr":
      ownAlternatives(shape.i, out);
      (shape.pi ?? []).forEach((arm) => ownAlternatives(arm, out));
      return out;
    case "rec":
      Object.values(shape.p).forEach((member) => ownAlternatives(member, out));
      return out;
    case "map":
      ownAlternatives(shape.v, out);
      (shape.pp ?? []).forEach(([, member]) => ownAlternatives(member, out));
      return out;
    default:
      return out;
  }
}

/** Every valid body the tests start from, each once: for every component of the table, under every
 * plan, in full and minimal (`synthetic`: built to reach every position), then every arm of an
 * alternative past the plans' reach, forced (D434), the orchard's answers and the golden envelopes. */
export function bodies(): readonly Body[] {
  if (made !== undefined) {
    return made;
  }
  const out: Body[] = [];
  const known = new Set<string>();
  const add = (component: string, source: string, value: unknown): void => {
    const key = `${component}:${JSON.stringify(value)}`;
    if (!known.has(key)) {
      known.add(key);
      out.push({ component, source, value });
    }
  };
  for (const name of Object.keys(SHAPES)) {
    for (const plan of PLANS) {
      for (const minimal of [false, true]) {
        const value = synthetic(REF(name), 0, minimal, plan);
        if (value !== UNMADE) {
          add(name, `synthetic ${String(plan.prefer)}${minimal ? " minimal" : ""}`, value);
        }
      }
    }
  }
  // Every arm of an alternative that `PLANS` does not reach (past their `prefer`s, in the order
  // `synthetic` takes the arms), forced, in the component whose own shape holds it (D434:
  // `DistributionPosition.columns`' five arms).
  for (const [name, shape] of Object.entries(SHAPES)) {
    for (const or of ownAlternatives(shape)) {
      for (const taken of armOrder(or).slice(PLANS.length)) {
        const arm = or.o.indexOf(taken);
        const value = synthetic(REF(name), 0, false, { prefer: 0, force: { or, arm } });
        if (value !== UNMADE) {
          add(name, `synthetic arm ${String(arm)}`, value);
        }
      }
    }
  }
  for (const answer of ORCHARD) {
    add(componentOf(answer), `orchard ${answer.operation} ${String(answer.status)}`, JSON.parse(answer.body));
  }
  for (const [name, text] of ENVELOPES) {
    add("ResultEnvelope", `envelope ${name}`, JSON.parse(text));
  }
  made = out;
  return out;
}
