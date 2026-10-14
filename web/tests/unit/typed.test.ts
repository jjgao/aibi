/**
 * The typed answers (D426): the seam's outcomes, the walk, its class test, its round trip, its
 * misses and the coverage of them, over the golden envelopes (`tests/fixtures/envelopes.json`), the
 * orchard's answers as the server writes them (`tests/fixtures/orchard-answers.json`, both written by
 * `server/tests/core/analyses/test_openapi_results.py`) and the bodies made from the table
 * (`typed-support.ts`), each decoded by the real decoder and walked by the real walk. What the table
 * is held to besides the walk, an independent validator of the document, is `typed-oracle.test.ts`.
 *
 * - **The class test** (m3 of round 3 and of round 4; m7 of the code review's round 1): after the
 *   walk no raw string is left anywhere, as a value or as a key, held to the table position by
 *   position (`audit`): every value is a box, a boolean, `null`, a frozen list, a record whose every
 *   own key is a member of **that record's** own node, or a `BoxedMap` whose every key is a
 *   `ServerText`. It runs over every fixture and over every body made from the table (each
 *   component, each plan, in full and minimal: a property test over the table's positions).
 * - **The round trip** (MA2 of the code review's round 1): unboxing the walked value gives back
 *   exactly the decoded body (`canonWalked` against `canonDecoded`), over every golden envelope,
 *   every orchard answer and every body made from the table, so that a walk that drops, adds or
 *   alters a member, a list item or a map entry fails.
 * - **The misses generated from the table** (MA2): for every position of every body, one miss for
 *   each constraint the table states there (a required member or key dropped, an enum value or a
 *   string const changed, every JSON type the position does not take, an extra key on a closed
 *   record, a map key outside every pattern or its `propertyNames`, an item of a list or of a
 *   `prefixItems` position wrong, a segment that is not exactly one arm); each is `invalid`. The
 *   misses are made from the table they test, so they cannot see a constraint the generator drops
 *   (that is the oracle's); and a miss at a position inside an `allOf` check may be refused by the
 *   base before the check (the oracle's swaps isolate it).
 * - **The coverage** (MA3 of round 2): `constraintsOf` lists, from the table alone, every constraint
 *   position (2,048: each place, and each thing wrong there) and the test fails on any that has no
 *   miss. The bodies are built to reach every one: each arm of an `or`, each kind of segment, a
 *   map with an entry of each shape its keys can take, and what the checks of an `allOf` ask of the
 *   first member's value (the keys, constants and lengths): so `Proportion.excluded`'s eight required
 *   keys, the checks after the first and the `prefixItems` of `cohorts` have a miss. An enum or a
 *   string const among the arms of an `or` that also has a `str` arm is no constraint (`Caveat.code`:
 *   the `str` arm takes the changed word); every other constraint is. The four maps of counts by
 *   reason, whose `required` keys the generator once dropped (MA1), have a named test of their own.
 * - **The outcome grid**: every typed operation against a 200 answer, a refusal at every status
 *   class (`Retry-After` read as whole seconds), a body of another shape, a body that is no JSON,
 *   another status (a 201 and a 203 with the operation's own answer included), a body too large or
 *   not UTF-8, a `fetch` that fails or is aborted and a body cut off; `invalid` echoes nothing and
 *   gives its cause (`shape` or `unreachable`) in fixed words.
 * - **The misses by name**: a nested member renamed, an enum's value changed, a required member
 *   dropped, an extra key on a closed record, a wrong JSON type, a miss at a `patternProperties` and
 *   at a `propertyNames` position, every segment that is not exactly one arm; and the residuals, each
 *   passing by name (`if`/`then`, `pattern`, the inside of an `x-aibi-json` value).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { BoxedMap, entriesOf, isServerNumber, readText, ServerText, textOf } from "../../src/api/box";
import { ClientError, RESPONSE_CAP_BYTES } from "../../src/api/client";
import { decode } from "../../src/api/decode";
import type { components } from "../../src/api/generated/openapi";
import { OPERATIONS, type Shape, SHAPES } from "../../src/api/generated/shapes";
import { kindOf } from "../../src/api/text";
import {
  type Boxed,
  countCohort,
  describeDataset,
  explain,
  type InvalidCause,
  listAnalyses,
  type Outcome,
  runAnalysis,
  searchCatalog,
  SHAPE_MISMATCH,
  type TypedOperation,
  UNREACHABLE,
  validateDocument,
  walkComponent,
} from "../../src/api/typed";

import { bodies, componentOf, constraintsOf, ENVELOPES, ORCHARD, REF, synthetic, UNMADE, variants, without } from "./typed-support";

type Schemas = components["schemas"];

/** What is raw after a walk, by path, held to the table's node at each position: a string value, a
 * key that is no member of the record it stands on (its own node's, m7 of round 1, not any
 * record's), a map key that is no box, an object of another kind. Empty for a walked value. An `or`
 * is read by the first arm the value has nothing raw for (else the arm with the fewest findings). */
function audit(shape: Shape, value: unknown, at: string): string[] {
  switch (shape.t) {
    case "ref": {
      const found = SHAPES[shape.n];
      return found === undefined ? [`${at}: no component ${shape.n}`] : audit(found, value, at);
    }
    case "json":
    case "any":
    case "const":
      return auditJson(value, at);
    case "never":
      return [`${at}: a value where the table allows none`];
    case "str":
    case "enum":
      return ServerText.holds(value) ? [] : [typeof value === "string" ? `${at}: a raw string` : `${at}: no text box`];
    case "num":
      return isServerNumber(value) ? [] : [`${at}: no number box`];
    case "bool":
      return typeof value === "boolean" ? [] : [`${at}: no boolean`];
    case "null":
      return value === null ? [] : [`${at}: no null`];
    case "seg": {
      if (typeof value !== "object" || value === null || Array.isArray(value) || !Object.isFrozen(value)) {
        return [`${at}: no frozen segment`];
      }
      const keys = Reflect.ownKeys(value).map(String).sort().join(",");
      const part = value as Record<string, unknown>;
      if (keys === "kind,text" && part["kind"] === "text") {
        return audit({ t: "str" }, part["text"], `${at}.text`);
      }
      if (keys === "data,kind,truncated" && part["kind"] === "data" && typeof part["truncated"] === "boolean") {
        return audit({ t: "str" }, part["data"], `${at}.data`);
      }
      return [`${at}: a segment of other members (${keys})`];
    }
    case "or": {
      const tried = shape.o.map((arm) => audit(arm, value, at));
      return tried.find((found) => found.length === 0) ?? tried.reduce((fewest, found) => (found.length < fewest.length ? found : fewest), tried[0] ?? [`${at}: an or of no arm`]);
    }
    case "all": {
      const [first] = shape.o;
      return first === undefined ? [`${at}: an all of no shape`] : audit(first, value, at);
    }
    case "arr": {
      if (!Array.isArray(value)) {
        return [`${at}: no list`];
      }
      return [...(Object.isFrozen(value) ? [] : [`${at}: unfrozen`]), ...value.flatMap((item: unknown, index) => audit(shape.pi?.[index] ?? shape.i, item, `${at}[${String(index)}]`))];
    }
    case "rec": {
      if (typeof value !== "object" || value === null || Object.getPrototypeOf(value) !== Object.prototype) {
        return [`${at}: no record`];
      }
      return [
        ...(Object.isFrozen(value) ? [] : [`${at}: unfrozen`]),
        ...Reflect.ownKeys(value).flatMap((key) => {
          const member = typeof key === "string" && Object.hasOwn(shape.p, key) ? shape.p[key] : undefined;
          return member === undefined ? [`${at}: the key ${String(key)} is no member of this record`] : audit(member, Reflect.get(value, key), `${at}.${String(key)}`);
        }),
      ];
    }
    case "map": {
      if (!BoxedMap.holds(value)) {
        return [`${at}: no BoxedMap`];
      }
      return entriesOf(value).flatMap(([key, member], index) => {
        if (!ServerText.holds(key)) {
          return [`${at}{${String(index)}}: a raw key`];
        }
        const text = readText(key);
        return audit(shape.pp?.find(([pattern]) => new RegExp(pattern, "u").test(text))?.[1] ?? shape.v, member, `${at}{${String(index)}}`);
      });
    }
  }
}

/** The audit of an `x-aibi-json` position (and of a const): any JSON, boxed. */
function auditJson(value: unknown, at: string): string[] {
  if (typeof value === "string") {
    return [`${at}: a raw string`];
  }
  if (value === null || typeof value === "boolean" || isServerNumber(value) || ServerText.holds(value)) {
    return [];
  }
  if (Array.isArray(value)) {
    return [...(Object.isFrozen(value) ? [] : [`${at}: unfrozen`]), ...value.flatMap((item: unknown, index) => auditJson(item, `${at}[${String(index)}]`))];
  }
  if (BoxedMap.holds(value)) {
    return entriesOf(value).flatMap(([key, member], index) => [...(ServerText.holds(key) ? [] : [`${at}{${String(index)}}: a raw key`]), ...auditJson(member, `${at}{${String(index)}}`)]);
  }
  return [`${at}: an object that is no BoxedMap`];
}

/** A decoded value as canonical text: keys sorted, a number as its source text (`#1.10`). */
function canonDecoded(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (isServerNumber(value)) {
    return `#${textOf(value)}`;
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonDecoded).join(",")}]`;
  }
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonDecoded(object[key])}`)
      .join(",")}}`;
  }
  throw new Error("not a decoded value");
}

/** A walked value unboxed, as the same canonical text: a text box by its text, a `BoxedMap` by its
 * entries, and a normalised segment back to `{text}` / `{data[, truncated: true]}`. */
function canonWalked(value: unknown): string {
  if (value === null || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (ServerText.holds(value)) {
    return JSON.stringify(readText(value));
  }
  if (isServerNumber(value)) {
    return `#${textOf(value)}`;
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonWalked).join(",")}]`;
  }
  const sorted = (entries: readonly (readonly [string, string])[]): string =>
    `{${[...entries]
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([key, member]) => `${JSON.stringify(key)}:${member}`)
      .join(",")}}`;
  if (BoxedMap.holds(value)) {
    return sorted(entriesOf(value).map(([key, member]) => [readText(key), canonWalked(member)] as const));
  }
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    if (object["kind"] === "text") {
      return sorted([["text", canonWalked(object["text"])]]);
    }
    if (object["kind"] === "data") {
      return sorted([["data", canonWalked(object["data"])], ...(object["truncated"] === true ? [["truncated", "true"] as const] : [])]);
    }
    return sorted(Reflect.ownKeys(value).map((key) => [String(key), canonWalked(object[String(key)])] as const));
  }
  throw new Error("a raw value after the walk");
}

/** A decoded body walked as a component, or a failure naming it. */
function walked(component: string, text: string): unknown {
  const found = walkComponent(component, decode(text));
  if (!found.ok) {
    throw new Error(`${component} did not walk`);
  }
  return found.value;
}

describe("the class test: nothing raw survives the walk, value or key (m3, m7)", () => {
  it("over the 19 golden envelopes", () => {
    expect(ENVELOPES).toHaveLength(19);
    for (const [name, text] of ENVELOPES) {
      expect(audit(REF("ResultEnvelope"), walked("ResultEnvelope", text), "$"), name).toEqual([]);
    }
  });

  it("over the orchard's answers, each as its operation's component or the refusals'", () => {
    expect(ORCHARD.length).toBeGreaterThanOrEqual(13);
    for (const answer of ORCHARD) {
      expect(audit(REF(componentOf(answer)), walked(componentOf(answer), answer.body), "$"), `${answer.operation} ${String(answer.status)}`).toEqual([]);
    }
  });

  it("over every body made from the table: each component, each plan, in full and minimal (a property test over its positions)", () => {
    const failed: string[] = [];
    for (const name of Object.keys(SHAPES)) {
      for (const minimal of [false, true]) {
        if (synthetic(REF(name), 0, minimal) === UNMADE) {
          failed.push(`${name}: no body made${minimal ? " (minimal)" : ""}`);
        }
      }
    }
    const made = bodies().filter(({ source }) => source.startsWith("synthetic"));
    for (const { component, source, value } of made) {
      const found = walkComponent(component, decode(JSON.stringify(value)));
      failed.push(...(found.ok ? audit(REF(component), found.value, `${component} (${source})`) : [`${component} (${source}): did not walk`]));
    }
    expect(failed).toEqual([]);
    expect(made.length).toBeGreaterThanOrEqual(2 * Object.keys(SHAPES).length);
  });

  it("the scan's own controls: a raw string, a raw key, a key of another record, an unfrozen record, a plain object in JSON", () => {
    expect(audit({ t: "str" }, "x", "$")).toEqual(["$: a raw string"]);
    expect(audit(REF("Refusals"), Object.freeze({ refusals: Object.freeze([]), k_raw_never_declared: null }), "$")).toEqual(["$: the key k_raw_never_declared is no member of this record"]);
    // `code` is a member of `Refusal`, a record the table has: but not of `Refusals`, which this is.
    expect(audit(REF("Refusals"), Object.freeze({ refusals: Object.freeze([]), code: null }), "$")).toEqual(["$: the key code is no member of this record"]);
    expect(audit(REF("Refusals"), { refusals: Object.freeze([]) }, "$")).toEqual(["$: unfrozen"]);
    expect(audit({ t: "json" }, Object.freeze({ data: null }), "$")).toEqual(["$: an object that is no BoxedMap"]);
    expect(audit({ t: "arr", i: { t: "seg" } }, Object.freeze([Object.freeze({ data: "x", kind: "data", truncated: false })]), "$")).toEqual(["$[0].data: a raw string"]);
  });
});

// ---------------------------------------------------------------------------------------------

/** The unboxed walk of a body: what the walk gave back, as canonical text, or why not. */
function roundTrip(component: string, text: string): string[] {
  const decoded = decode(text);
  const found = walkComponent(component, decoded);
  if (!found.ok) {
    return [`${component}: did not walk`];
  }
  const was = canonDecoded(decoded);
  const now = canonWalked(found.value);
  if (was === now) {
    return [];
  }
  let at = 0;
  while (was[at] === now[at]) {
    at += 1;
  }
  return [`${component}: the walk differs from the decoded body at ${String(at)}: ${was.slice(Math.max(0, at - 40), at + 60)} against ${now.slice(Math.max(0, at - 40), at + 60)}`];
}

describe("the round trip: unboxing the walked body gives back exactly the decoded body (MA2)", () => {
  it("over the 19 golden envelopes", () => {
    expect(ENVELOPES.flatMap(([, text]) => roundTrip("ResultEnvelope", text))).toEqual([]);
  });

  it("over the orchard's answers", () => {
    expect(ORCHARD.flatMap((answer) => roundTrip(componentOf(answer), answer.body))).toEqual([]);
  });

  it("over every body made from the table (each component, each plan, in full and minimal)", () => {
    const made = bodies().filter(({ source }) => source.startsWith("synthetic"));
    expect(made.flatMap(({ component, value }) => roundTrip(component, JSON.stringify(value)))).toEqual([]);
    expect(made.length).toBeGreaterThanOrEqual(2 * Object.keys(SHAPES).length);
  });

  it("the property's own controls: a body that differs, a number's text, a key's place, a segment", () => {
    expect(canonDecoded(decode('{"a":1}'))).not.toBe(canonDecoded(decode('{"a":2}')));
    expect(canonDecoded(decode('{"a":1.10}'))).not.toBe(canonDecoded(decode('{"a":1.1}')));
    expect(canonDecoded(decode('{"a":1,"b":2}'))).toBe(canonDecoded(decode('{"b":2,"a":1}')));
    expect(roundTrip("Refusals", '{"refusals":[{"code":"X","path":null,"message":[{"text":"a"},{"data":"b","truncated":true},{"data":"c"}],"alternatives":[]}]}')).toEqual([]);
    const dropped = walkComponent("Refusals", decode('{"refusals":[]}'));
    expect(dropped.ok ? canonWalked(dropped.value) : "").not.toBe(canonDecoded(decode('{"refusals":[1]}')));
  });
});

describe("the misses generated from the table: every constraint a position states, one miss each (MA2)", () => {
  it("every miss, over every golden envelope, orchard answer and synthetic body, is invalid", () => {
    const seen = new Set<string>();
    const accepted: string[] = [];
    const run = (component: string, body: unknown): void => {
      for (const [at, what, replacement] of variants(REF(component), body, component, 0)) {
        const key = `${at} :: ${what}`;
        if (seen.has(key)) {
          continue;
        }
        seen.add(key);
        if (walks(JSON.stringify(replacement), component)) {
          accepted.push(key);
        }
      }
    };
    for (const { component, value } of bodies()) {
      run(component, value);
    }
    expect(accepted).toEqual([]);
    // The generator reaches every kind of constraint the table states (a generator that made none
    // would pass the line above).
    expect(seen.size).toBeGreaterThan(1500);
    const kinds = [...seen].map((key) => key.split(" :: ")[1] ?? "");
    for (const kind of [
      /^a (string|number|boolean|null|array|object)$/u,
      /^the required member .* dropped$/u,
      /^the required key .* dropped$/u,
      /^an enum value changed$/u,
      /^a const changed$/u,
      /^an extra key on a closed record$/u,
      /^a key outside every pattern$/u,
      /^a key its propertyNames refuses$/u,
      /^a text segment/u,
      /^a data segment/u,
    ]) {
      expect(kinds.filter((what) => kind.test(what)).length, String(kind)).toBeGreaterThan(0);
    }
    expect([...seen].filter((key) => /\[\d+\] :: /u.test(key)).length, "a prefixItems position").toBeGreaterThan(0);
    expect([...seen].filter((key) => /\[\] :: /u.test(key)).length, "a list item").toBeGreaterThan(0);
    expect([...seen].filter((key) => /\{\} :: /u.test(key)).length, "a map value").toBeGreaterThan(0);
  });

  it("the generator's own controls: a miss it makes that a walk of another table would take", () => {
    const record: Shape = { t: "rec", p: { a: { t: "str" }, b: { t: "num" } }, r: ["a"] };
    const made = variants(record, { a: "x", b: 1 }, "R", 0).map(([, what]) => what);
    expect(made).toEqual(expect.arrayContaining(["a number", "the required member a dropped", "an extra key on a closed record"]));
    expect(variants({ t: "map", v: { t: "never" }, pp: [["^p", { t: "num" }]] }, { p1: 1 }, "M", 0).map(([, what]) => what)).toContain("a key outside every pattern");
    expect(variants({ t: "or", o: [{ t: "rec", p: {}, r: [] }, { t: "rec", p: { a: { t: "str" } }, r: [] }] }, {}, "O", 0).map(([, what]) => what)).not.toContain("an extra key on a closed record");
  });

  /** The four maps of counts by reason, whose `required` keys are the reasons (D426, MA1). */
  const REASON_MAPS: readonly (readonly [string, string])[] = [
    ["Population", "unknown_by_reason"],
    ["Analysed", "excluded"],
    ["AnalysedVariable", "excluded"],
    ["Proportion", "excluded"],
  ];

  it.each(REASON_MAPS)("%s.%s: a reason dropped, or none left, is invalid (D426: required is interpreted on a map)", (component, member) => {
    const reasons = reasonsOf(component, member);
    expect(reasons.length).toBeGreaterThanOrEqual(6);
    // The required members alone, and the member with every reason counted once.
    const base = synthetic(REF(component), 0, true) as Record<string, unknown>;
    const body = { ...base, [member]: Object.fromEntries(reasons.map((reason) => [reason, 1])) };
    expect(walks(JSON.stringify(body), component)).toBe(true);
    expect(walks(JSON.stringify({ ...body, [member]: {} }), component)).toBe(false);
    for (const reason of reasons) {
      expect(walks(JSON.stringify({ ...body, [member]: without(body[member] as Record<string, unknown>, reason) }), component), `${reason} dropped`).toBe(false);
    }
  });

  it("the golden envelope with a reason dropped, or none left, from a count by reason is invalid (the review's input)", () => {
    expect(walks(changed("two_tiers_under_k", () => undefined))).toBe(true);
    expect(walks(changed("two_tiers_under_k", (e) => { at(e, "population", 0)["unknown_by_reason"] = {}; }))).toBe(false);
    expect(walks(changed("two_tiers_under_k", (e) => { delete at(e, "population", 0, "unknown_by_reason")["NO_ROWS"]; }))).toBe(false);
    expect(walks(changed("two_tiers_under_k", (e) => { at(e, "analysed", 0)["excluded"] = {}; }))).toBe(false);
  });
});

/** The reasons a component's member (a map or `null`) requires, from the table. */
const reasonsOf = (component: string, member: string): readonly string[] => {
  const shape = SHAPES[component];
  const record = shape?.t === "all" ? shape.o[0] : shape;
  const found = record?.t === "rec" ? record.p[member] : undefined;
  const map = found?.t === "or" ? found.o.find((arm) => arm.t === "map") : found;
  return map?.t === "map" ? (map.r ?? []) : [];
};

/** The constraints a table states that no miss is made for, from the static list and the misses made. */
const uncoveredOf = (constraints: ReadonlySet<string>, missed: ReadonlySet<string>): string[] => [...constraints].filter((constraint) => !missed.has(constraint)).sort();

/** Every miss's label (`position :: what`) over a set of bodies. */
function missedOver(made: readonly { readonly component: string; readonly value: unknown }[]): Set<string> {
  const missed = new Set<string>();
  for (const { component, value } of made) {
    for (const [at, what] of variants(REF(component), value, component, 0)) {
      missed.add(`${at} :: ${what}`);
    }
  }
  return missed;
}

describe("every constraint the table states has a miss, made from a valid body that reaches it (MA3)", () => {
  /** The constraints of the whole table, once each (`constraintsOf`: a static walk, no body asked). */
  const constraints = (): Set<string> => {
    const all = new Set<string>();
    const visited = new Set<string>();
    for (const name of Object.keys(SHAPES)) {
      constraintsOf(REF(name), name, true, 0, all, visited);
    }
    return all;
  };

  it("no position of the table is left without a miss (the bodies are built to reach every one)", () => {
    const all = constraints();
    expect(all.size).toBeGreaterThan(1900);
    expect(uncoveredOf(all, missedOver(bodies()))).toEqual([]);
  });

  it("the reasons: each of the eight required keys of Proportion.excluded, and its propertyNames, has a miss", () => {
    const missed = missedOver(bodies());
    const keys = reasonsOf("Proportion", "excluded");
    expect(keys).toHaveLength(8);
    for (const key of keys) {
      expect(missed.has(`Proportion.excluded|0 :: the required key ${key} dropped`), key).toBe(true);
    }
    expect(missed.has("Proportion.excluded|0 :: a key its propertyNames refuses")).toBe(true);
  });

  it("the checks after the first are reached: a miss at each of Proportion's, and a body whose list is long enough for the prefix", () => {
    const missed = missedOver(bodies());
    for (const label of ["Proportion&0.not_estimable :: a key its propertyNames refuses", "Proportion&1.not_estimable./denominator :: a const changed", "ResultEnvelope.cohorts&0[5].position :: a string"]) {
      expect(missed.has(label), label).toBe(true);
    }
  });

  it("the assertion's own controls: a position no body reaches is listed, and one a body reaches is not", () => {
    const table: Shape = { t: "rec", p: { a: { t: "str" }, b: { t: "bool" } }, r: ["a"] };
    const all = new Set<string>();
    constraintsOf(table, "R", true, 0, all, new Set());
    expect([...all]).toEqual(expect.arrayContaining(["R :: the required member a dropped", "R.a :: a number", "R.b :: a string"]));
    const only = missedOver([{ component: "Refusals", value: { refusals: [] } }]);
    expect(only.has("Refusals.refusals :: a string")).toBe(true);
    expect(uncoveredOf(new Set(["R.b :: a string", "Refusals.refusals :: a string"]), only)).toEqual(["R.b :: a string"]);
  });
});

// ---------------------------------------------------------------------------------------------

/** A refusal body, well formed, with a marker no outcome may echo. */
const REFUSAL = '{"refusals":[{"code":"UNKNOWN_DATASET","path":"/dataset","message":[{"text":"No such "},{"data":"MARK-echo"}],"alternatives":[{"data":"orchard"}]}]}';

/** Each typed operation, called with a body of its own, and the 200 answer of the orchard for it. */
const CALLS: Readonly<Record<TypedOperation, () => Promise<Outcome<unknown>>>> = {
  search_catalog: () => searchCatalog({}),
  describe_dataset: () => describeDataset({ dataset: "orchard" }),
  list_analyses: () => listAnalyses({ dataset: "orchard" }),
  validate_document: () => validateDocument({ document: {} }),
  count_cohort: () => countCohort({ document: { aibi: "1", dataset: "orchard", unit: "trees", cohorts: { all_trees: { all: [] } } } }),
  run_analysis: () => runAnalysis({ document: { aibi: "1", dataset: "orchard", unit: "trees", cohorts: { all_trees: { all: [] } }, views: [] } }),
  explain: () => explain({ id: `drv:${"0".repeat(64)}` }),
};

const OK_BODY = (operation: TypedOperation): string => {
  const found = ORCHARD.find((answer) => answer.operation === operation && answer.status === 200);
  if (found === undefined) {
    throw new Error(`no orchard answer of ${operation}`);
  }
  return found.body;
};

function answering(body: string | null, status = 200, headers: Record<string, string> = { "Content-Type": "application/json" }): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(body, { status, headers }))),
  );
}

const STATUSES = [400, 401, 403, 404, 405, 408, 409, 411, 413, 415, 418, 422, 429, 451, 500, 502, 503, 599];

describe("the outcome grid: every operation, every status class, every failure", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const operations = Object.keys(OPERATIONS) as TypedOperation[];

  it("covers the seven operations of the table, each with a typed function", () => {
    expect(operations.sort()).toEqual(["count_cohort", "describe_dataset", "explain", "list_analyses", "run_analysis", "search_catalog", "validate_document"]);
  });

  it.each(operations)("%s: a 200 of its component is ok, walked, and nothing raw survives", async (operation) => {
    answering(OK_BODY(operation));
    const outcome = await CALLS[operation]();
    expect(outcome.kind).toBe("ok");
    expect(Object.isFrozen(outcome)).toBe(true);
    expect(audit(REF(OPERATIONS[operation]), outcome.kind === "ok" ? outcome.value : "not ok", "$")).toEqual([]);
  });

  it.each(operations.flatMap((operation) => STATUSES.map((status) => [operation, status] as const)))("%s: a refusal at %i is refused, with its status and its refusals walked", async (operation, status) => {
    answering(REFUSAL, status, { "Content-Type": "application/json", ...(status === 429 || status === 503 ? { "Retry-After": "7" } : {}) });
    const outcome = await CALLS[operation]();
    expect(outcome.kind).toBe("refused");
    if (outcome.kind === "refused") {
      expect(outcome.status).toBe(status);
      expect(outcome.retryAfter).toBe(status === 429 || status === 503 ? 7 : null);
      expect(outcome.refusals).toHaveLength(1);
      expect(audit(REF("Refusals"), Object.freeze({ refusals: outcome.refusals }), "$")).toEqual([]);
      expect(kindOf(outcome.refusals[0]?.code ?? (null as never), "RefusalCode")).toBe("UNKNOWN_DATASET");
    }
  });

  it.each([
    ["an HTTP date", "Wed, 21 Oct 2015 07:28:00 GMT", null],
    ["seconds", "120", 120],
    ["a negative", "-1", null],
    ["a fraction", "1.5", null],
    ["eleven digits", "12345678901", null],
  ])("Retry-After as %s is %s", async (_case, header, expected) => {
    answering(REFUSAL, 503, { "Content-Type": "application/json", "Retry-After": header });
    const outcome = await searchCatalog({});
    expect(outcome.kind === "refused" ? outcome.retryAfter : "not refused").toBe(expected);
  });

  /** A response whose body is `text` and whose status is `status`, with a `Content-Length` header. */
  const answeringWith = (body: BodyInit, headers: Record<string, string>, status = 200): void => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(body, { status, headers: { "Content-Type": "application/json", ...headers } }))),
    );
  };

  /** The answers that are no outcome but `invalid`, each with the marker in its body and its cause:
   * `shape` (the server answered what the page does not read) or `unreachable` (no answer came). */
  const INVALID: readonly (readonly [string, InvalidCause, (operation: TypedOperation) => void])[] = [
    ["a 200 whose body is the refusals", "shape", () => { answering(REFUSAL); }],
    ["a 200 whose body is an empty object", "shape", () => { answering("{}"); }],
    ["a 200 whose body is a list", "shape", () => { answering('["MARK-echo"]'); }],
    ["a 4xx whose body is no refusal list", "shape", () => { answering('{"detail":"MARK-echo"}', 422); }],
    ["a 4xx whose refusal list is empty of members", "shape", () => { answering('{"refusals":[{"code":"X"}]}', 400); }],
    ["a 4xx whose refusal list is empty (a refusal with nothing in it, m6)", "shape", () => { answering('{"refusals":[]}', 400); }],
    ["a 5xx whose refusal list is empty (m6)", "shape", () => { answering('{"refusals":[]}', 503); }],
    ["a 5xx that is no JSON", "shape", () => { answering("<html>MARK-echo</html>", 502, { "Content-Type": "text/html" }); }],
    ["a 200 that is no JSON by its type", "shape", () => { answering('{"MARK-echo":1}', 200, { "Content-Type": "text/plain" }); }],
    ["a 200 whose JSON does not parse", "shape", () => { answering("{MARK-echo", 200); }],
    ["a 204", "shape", () => { answering(null, 204); }],
    ["a 302 with a refusal body", "shape", () => { answering(REFUSAL, 302); }],
    ["a 201 with the operation's own answer (m1)", "shape", (operation) => { answering(OK_BODY(operation), 201); }],
    ["a 203 with the operation's own answer (m1)", "shape", (operation) => { answering(OK_BODY(operation), 203); }],
    ["a 299 with the operation's own answer (m1)", "shape", (operation) => { answering(OK_BODY(operation), 299); }],
    ["a body that says it is over the cap (Content-Length, m1)", "shape", (operation) => { answeringWith(OK_BODY(operation), { "Content-Length": String(RESPONSE_CAP_BYTES + 1) }); }],
    [
      "a streamed body over the cap (m1)",
      "shape",
      () => {
        let sent = 0;
        const stream = new ReadableStream<Uint8Array>({
          pull(controller) {
            const chunk = new Uint8Array(1024 * 1024).fill(0x20);
            sent += chunk.byteLength;
            controller.enqueue(chunk);
            if (sent > RESPONSE_CAP_BYTES + 1024 * 1024) {
              controller.close();
            }
          },
        });
        answeringWith(stream, {});
      },
    ],
    ["a body that is not UTF-8 (m1)", "shape", () => { answeringWith(new Uint8Array([0x7b, 0x22, 0xff, 0xfe, 0x22, 0x3a, 0x31, 0x7d]), {}); }],
    ["a fetch that fails", "unreachable", () => { vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("MARK-echo")))); }],
    ["a fetch that is aborted", "unreachable", () => { vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new DOMException("MARK-echo", "AbortError")))); }],
    [
      "a body cut off",
      "unreachable",
      () => {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('{"hits":['));
            controller.error(new Error("MARK-echo"));
          },
        });
        vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(stream, { status: 200, headers: { "Content-Type": "application/json" } }))));
      },
    ],
  ];

  it.each(operations.flatMap((operation) => INVALID.map(([name, cause, arrange]) => [operation, name, cause, arrange] as const)))(
    "%s: %s is invalid, of its cause, and echoes nothing",
    async (operation, _name, cause, arrange) => {
      arrange(operation);
      const outcome = await CALLS[operation]();
      expect(outcome).toEqual({ kind: "invalid", cause });
      expect(Object.keys(outcome)).toEqual(["kind", "cause"]);
      expect(Object.isFrozen(outcome)).toBe(true);
      expect(JSON.stringify(outcome)).not.toContain("MARK");
    },
  );

  it("the control of the grid's 2xx cells: the operation's own answer at 200 is ok", async () => {
    for (const operation of operations) {
      answering(OK_BODY(operation));
      expect((await CALLS[operation]()).kind, operation).toBe("ok");
    }
  });

  it("the invalid outcome's words are fixed, one for each cause (m2)", () => {
    expect(SHAPE_MISMATCH).toBe("The page and the server disagree about the data's shape; reload.");
    expect(UNREACHABLE).toBe("The page could not reach the server; check the connection and try again.");
    expect(UNREACHABLE).not.toBe(SHAPE_MISMATCH);
  });

  it("a request the client refuses before sending throws, as the caller's fault: a number that is not finite", async () => {
    answering(OK_BODY("search_catalog"));
    await expect(searchCatalog({ limit: Number.NaN })).rejects.toBeInstanceOf(ClientError);
  });
});

// ---------------------------------------------------------------------------------------------

/** An envelope changed by `change` (a copy of its parsed JSON, numbers as JS numbers: what the
 * misses read is their shape). */
/** A JSON object's member by a path of keys and indices, an object (or a failure). */
function at(value: unknown, ...path: readonly (string | number)[]): Record<string, unknown> {
  let found: unknown = value;
  for (const step of path) {
    found = typeof found === "object" && found !== null ? Reflect.get(found, step) : undefined;
  }
  if (typeof found !== "object" || found === null) {
    throw new Error(`nothing at ${path.join("/")}`);
  }
  return found as Record<string, unknown>;
}

function changed(name: string, change: (envelope: Record<string, unknown>) => void): string {
  const text = ENVELOPES.find(([found]) => found === JSON.stringify(name))?.[1];
  if (text === undefined) {
    throw new Error(`no envelope ${name}`);
  }
  const copy = JSON.parse(text) as Record<string, unknown>;
  change(copy);
  return JSON.stringify(copy);
}

const walks = (text: string, component = "ResultEnvelope"): boolean => walkComponent(component, decode(text)).ok;

describe("the misses are invalid", () => {
  it.each([
    ["a nested member renamed (population[0].n_unknown)", (e: Record<string, unknown>) => { const p = at(e, "population", 0); p["unknown"] = p["n_unknown"]; delete p["n_unknown"]; }],
    ["an enum's value changed (a release's status)", (e: Record<string, unknown>) => { at(e, "derivation", "releases", 0)["status"] = "archived"; }],
    ["a required member dropped (a caveat's message)", (e: Record<string, unknown>) => { delete at(e, "caveats", 0)["message"]; }],
    ["an extra key on a closed record (derivation.extra)", (e: Record<string, unknown>) => { at(e, "derivation")["extra"] = 1; }],
    ["a wrong JSON type (population[0].n_true as text)", (e: Record<string, unknown>) => { at(e, "population", 0)["n_true"] = "12"; }],
    ["a miss at a patternProperties position (a not_estimable reason)", (e: Record<string, unknown>) => { at(e, "analysed", 0)["not_estimable"] = { "/n": "hidden" }; }],
    ["a miss at a propertyNames position (unknown_by_reason's key)", (e: Record<string, unknown>) => { at(e, "population", 0)["unknown_by_reason"] = { NOT_A_REASON: 1 }; }],
    ["a list where a record stands", (e: Record<string, unknown>) => { e["derivation"] = []; }],
    ["a record where a list stands", (e: Record<string, unknown>) => { e["caveats"] = {}; }],
    ["a const changed (a data segment's truncated)", (e: Record<string, unknown>) => { e["labels"] = [{ data: "x", truncated: true }]; }],
    ["a key an allOf check refuses (a not_estimable pointer outside its vocabulary)", (e: Record<string, unknown>) => { at(e, "analysed", 0)["not_estimable"] = { "/zzz": "suppressed" }; }],
  ])("%s", (_case, change) => {
    expect(walks(changed("two_tiers_under_k", change))).toBe(false);
  });

  it("the envelope the misses change walks as it is (their control)", () => {
    expect(walks(changed("two_tiers_under_k", () => undefined))).toBe(true);
  });

  /** A refusal list holding one message of the given segments. */
  const message = (...segments: string[]): string => `{"refusals":[{"code":"X","path":null,"message":[${segments.join(",")}],"alternatives":[]}]}`;

  it.each([
    ["both keys", '{"text":"a","data":"b"}'],
    ["an extra key", '{"text":"a","extra":1}'],
    ["a non-string text", '{"text":1}'],
    ["a non-string data", '{"data":null}'],
    ["truncated false", '{"data":"a","truncated":false}'],
    ["truncated alone", '{"truncated":true}'],
    ["__proto__ beside the text", '{"text":"a","__proto__":{}}'],
    ["__proto__ alone", '{"__proto__":{"text":"a"}}'],
    ["no key", "{}"],
    ["a string", '"a"'],
    ["a list", '["a"]'],
  ])("a segment that is not exactly one arm is invalid: %s", (_case, segment) => {
    expect(walks(message(segment), "Refusals")).toBe(false);
  });

  it("a segment is normalised: exactly {text}, {data} or {data, truncated: true}", () => {
    const found = walkComponent("Refusals", decode(message('{"text":"a"}', '{"data":"b"}', '{"data":"c","truncated":true}')));
    expect(found.ok).toBe(true);
    const segments = found.ok ? (found.value as { refusals: { message: Record<string, unknown>[] }[] }).refusals[0]?.message : [];
    expect(segments?.map((segment) => [segment["kind"], Object.keys(segment).sort().join(","), segment["truncated"]])).toEqual([
      ["text", "kind,text", undefined],
      ["data", "data,kind,truncated", false],
      ["data", "data,kind,truncated", true],
    ]);
    expect(segments?.map((segment) => readText((segment["text"] ?? segment["data"]) as ServerText))).toEqual(["a", "b", "c"]);
  });
});

describe("what the walk reads fail-closed, and the residuals it names (D426)", () => {
  it("an unknown severity walks, boxed, and is read as block (W14: not refused; W15: not enumerated)", () => {
    const text = changed("two_tiers_under_k", (e) => { at(e, "caveats", 0)["severity"] = "critical"; });
    const found = walkComponent("ResultEnvelope", decode(text));
    expect(found.ok).toBe(true);
    const severity = found.ok ? (found.value as Boxed<Schemas["ResultEnvelope"]>).caveats[0]?.severity : undefined;
    expect(severity === undefined ? "none" : kindOf(severity, "Severity")).toBe("block");
  });

  it("a pack's code on an open union walks, and is no member of the core vocabulary (kindOf gives null: shown through Text, never dropped)", () => {
    const text = changed("two_tiers_under_k", (e) => { at(e, "caveats", 0)["code"] = "demo.SOMETHING"; });
    const found = walkComponent("ResultEnvelope", decode(text));
    expect(found.ok).toBe(true);
    const code = found.ok ? (found.value as Boxed<Schemas["ResultEnvelope"]>).caveats[0]?.code : undefined;
    expect(code === undefined ? "none" : kindOf(code, "Caveat.code")).toBe(null);
    expect(code !== undefined && readText(code)).toBe("demo.SOMETHING");
  });

  it.each([
    ["if/then: a caveat's code with another severity", (e: Record<string, unknown>) => { at(e, "caveats", 0)["severity"] = "info"; }],
    ["pattern: a digest of another form", (e: Record<string, unknown>) => { e["digest"] = "md5:x"; }],
    ["the inside of an x-aibi-json value: a document's member renamed", (e: Record<string, unknown>) => { const d = at(e, "derivation", "document"); d["views_renamed"] = d["views"]; delete d["views"]; }],
    ["a bound: more than six cohorts' positions (prefixItems' numeric consts)", (e: Record<string, unknown>) => { at(e, "cohorts", 0)["position"] = 5; }],
  ])("a residual passes, by name: %s", (_case, change) => {
    expect(walks(changed("two_tiers_under_k", change))).toBe(true);
  });
});
