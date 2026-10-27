/**
 * A synthetic column descriptor built from the generated schema (`schemas/descriptor.schema.json`'s
 * `ColumnDescriptor`, which the server's model writes, D443): every member of it and of `ColumnFields`
 * present, so that a member added to the model joins the domain of the tests that use this file by
 * itself. Each string leaf (and each free key of a map) is a mark of its own, each number leaf a
 * number of its own, so that a screen that reads one of them shows a value that names it; a value the
 * schema fixes (an enum's member, a constant) is kept as the schema gives it, with its pointer, and a
 * boolean or a null is kept as is (a boolean cannot be told by its text). The values need not satisfy
 * the schema's patterns and ranges: the answer carries the descriptor as open JSON, which the page
 * reads without checking it, and a mark is what a reader can find. A schema that offers alternatives
 * (`oneOf`, `anyOf`, an `enum`) has a **site** at each place that offers them (its pointer with the
 * free keys and the list indices as `*`, and the keyword): a build takes one option at each site,
 * each site chosen of its own, never by a number shared with the others (`arm % n` at every site
 * correlates the nested ones, and 13 of the 27 sites were then never fully reached). `variants()` builds
 * until every option of every site is reached by some build (`gaps()` is what is left, and is empty), a
 * site found only under another's option being reached by forcing that option: a build of every option
 * combination is not made (an option at one site is not held with every option at another). A
 * recursive reference is cut where the schema allows a way out.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Schema = Record<string, unknown>;

const DOCUMENT = JSON.parse(readFileSync(path.join(import.meta.dirname, "../../../schemas/descriptor.schema.json"), "utf8")) as { $defs: Record<string, Schema> };

/** What a build found: the descriptor, and its leaves by their JSON Pointer. */
export interface Synthetic {
  readonly descriptor: { [key: string]: Json };
  /** The marks of the string leaves and keys, by pointer (a key's pointer ends in `#key`). */
  readonly texts: ReadonlyMap<string, string>;
  /** The numbers, by pointer. */
  readonly numbers: ReadonlyMap<string, number>;
  /** The strings the schema fixes (an enum's member, a constant), by pointer. */
  readonly fixed: ReadonlyMap<string, string>;
  /** Each site this build passed, by its key: how many options it offers, the one this build took, and the
   * options taken at the sites it lies under (those that led to it). */
  readonly sites: ReadonlyMap<string, { readonly n: number; readonly arm: number; readonly via: ReadonlyMap<string, number> }>;
}

/** Which option a build takes at a site (its key, and how many options it offers). */
type Choose = (site: string, n: number) => number;

/** The `$defs` entry a `$ref` names. */
function resolved(reference: string): { readonly name: string; readonly schema: Schema } {
  const name = reference.replace("#/$defs/", "");
  const schema = DOCUMENT.$defs[name];
  if (schema === undefined) {
    throw new Error(`the schema has no ${reference}`);
  }
  return { name, schema };
}

const array = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const object = (value: unknown): Schema => (typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Schema) : {});

/** The key of a site: where it is (a free key or a list index as `*`) and which keyword offers the options. */
const siteOf = (at: string, keyword: string): string => `${at.replace(/\/(?:zq\d+z|\d+)(?=\/|$)/gu, "/*")} ${keyword}`;

/** Builds a descriptor, `choose` giving the option at each site (once for a site in one build). */
function build(choose: Choose): Synthetic {
  const texts = new Map<string, string>();
  const numbers = new Map<string, number>();
  const fixed = new Map<string, string>();
  const sites = new Map<string, { n: number; arm: number; via: ReadonlyMap<string, number> }>();
  let counter = 0;

  const pick = (site: string, n: number, via: ReadonlyMap<string, number>): number => {
    const known = sites.get(site);
    if (known !== undefined) {
      return known.arm;
    }
    const arm = choose(site, n);
    sites.set(site, { n, arm, via });
    return arm;
  };

  const mark = (): string => {
    counter += 1;
    return `zq${String(counter)}z`;
  };

  const make = (schema: Schema, at: string, stack: readonly string[], via: ReadonlyMap<string, number> = new Map()): Json => {
    if (typeof schema["$ref"] === "string") {
      const { name, schema: target } = resolved(schema["$ref"]);
      return make(target, at, [...stack, name], via);
    }
    if ("const" in schema) {
      fixed.set(at, String(schema["const"]));
      return schema["const"] as Json;
    }
    if (Array.isArray(schema["enum"])) {
      const one = schema["enum"][pick(siteOf(at, "enum"), schema["enum"].length, via)] as string;
      fixed.set(at, one);
      return one;
    }
    if ("properties" in schema || "patternProperties" in schema || (schema["type"] === "object" && "additionalProperties" in schema)) {
      const members: { [key: string]: Json } = {};
      for (const [name, one] of Object.entries(object(schema["properties"]))) {
        members[name] = make(object(one), `${at}/${name}`, stack, via);
      }
      const free = "properties" in schema ? undefined : (Object.values(object(schema["patternProperties"]))[0] ?? schema["additionalProperties"]);
      if (typeof free === "object" && free !== null) {
        const key = mark();
        texts.set(`${at}/${key}#key`, key);
        members[key] = make(object(free), `${at}/${key}`, stack, via);
      }
      return members;
    }
    if (schema["type"] === "object") {
      return {};
    }
    for (const keyword of ["oneOf", "anyOf"] as const) {
      if (Array.isArray(schema[keyword])) {
        const options = array(schema[keyword]).map(object).filter((option) => option["type"] !== "null");
        // A recursive reference is cut: where a name is already twice in the stack, an option without a reference.
        const cut = stack.some((name, index) => stack.indexOf(name) !== index);
        const open = cut ? options.filter((option) => !JSON.stringify(option).includes("$ref")) : options;
        const usable = open.length === 0 ? options : open;
        const site = siteOf(at, cut ? `${keyword} (cut)` : keyword);
        const arm = pick(site, usable.length, via);
        return make(object(usable[arm]), at, stack, new Map([...via, [site, arm]]));
      }
    }
    if (Array.isArray(schema["allOf"])) {
      const structured = array(schema["allOf"]).map(object).find((one) => "type" in one || "properties" in one || "patternProperties" in one || "$ref" in one);
      if (structured === undefined) {
        throw new Error(`${at}: an allOf with no member that builds`);
      }
      return make(structured, at, stack, via);
    }
    const type: unknown = Array.isArray(schema["type"]) ? (schema["type"] as unknown[]).find((one) => one !== "null") : schema["type"];
    if (type === "array") {
      const count = Math.max(typeof schema["minItems"] === "number" ? schema["minItems"] : 1, 1);
      return Array.from({ length: count }, (_, index) => make(object(schema["items"]), `${at}/${String(index)}`, stack, via));
    }
    if (type === "string") {
      const text = mark();
      texts.set(at, text);
      return text;
    }
    if (type === "integer" || type === "number") {
      counter += 1;
      const value = 7_710_000 + counter;
      numbers.set(at, value);
      return value;
    }
    if (type === "boolean") {
      return true;
    }
    if (type === "null") {
      return null;
    }
    throw new Error(`${at}: a schema this builder does not read: ${JSON.stringify(schema).slice(0, 200)}`);
  };

  const descriptor = make(resolved("#/$defs/ColumnDescriptor").schema, "", ["ColumnDescriptor"]);
  if (typeof descriptor !== "object" || descriptor === null || Array.isArray(descriptor)) {
    throw new Error("a descriptor is an object");
  }
  return { descriptor, texts, numbers, fixed, sites };
}

/** The members a model lists (`properties`), by `$defs` name. */
export function membersOf(name: string): string[] {
  return Object.keys(object(resolved(`#/$defs/${name}`).schema["properties"]));
}

/** The options of each site that no build of `list` took. */
function missing(list: readonly Synthetic[]): { readonly site: string; readonly arm: number; readonly n: number }[] {
  const offered = new Map<string, number>();
  const taken = new Map<string, Set<number>>();
  for (const one of list) {
    for (const [site, { n, arm }] of one.sites) {
      offered.set(site, Math.max(offered.get(site) ?? 0, n));
      taken.set(site, (taken.get(site) ?? new Set<number>()).add(arm));
    }
  }
  return [...offered].flatMap(([site, n]) =>
    Array.from({ length: n }, (_, arm) => arm)
      .filter((arm) => taken.get(site)?.has(arm) !== true)
      .map((arm) => ({ site, arm, n })),
  );
}

/** The options of each site that no build of `list` took, as `site: option k of n`. */
export const gaps = (list: readonly Synthetic[]): string[] => missing(list).map(({ site, arm, n }) => `${site}: option ${String(arm)} of ${String(n)}`);

/** The number of sites `list` passed. */
export const siteCount = (list: readonly Synthetic[]): number => new Set(list.flatMap((one) => [...one.sites.keys()])).size;

/** The builds that took `arm % n` at every site, for each `arm` up to the most options the first build's
 * sites offered: the generator this file had, kept as the control that `gaps()` finds what it left out. */
export function correlated(): readonly Synthetic[] {
  const most = Math.max(...[...build(() => 0).sites.values()].map(({ n }) => n));
  return Array.from({ length: most }, (_, arm) => build((_site, n) => arm % n));
}

let memo: readonly Synthetic[] | undefined;

/** Every option of every site in at least one build. The first builds each take, at each site, its
 * lowest option not yet taken; a site that stays short (it lies under an option the later builds no
 * longer take) is reached by a build that takes the options that led to it, and the one it lacks. */
export function variants(): readonly Synthetic[] {
  if (memo !== undefined) {
    return memo;
  }
  const taken = new Map<string, Set<number>>();
  /** For each site, the options at the sites above it that first led to it. */
  const ways = new Map<string, ReadonlyMap<string, number>>();
  const list: Synthetic[] = [];
  /** A build, `forced` giving the options it must take; how many options it took that none had. */
  const run = (forced: ReadonlyMap<string, number>): number => {
    const one = build((site, n) => forced.get(site) ?? Array.from({ length: n }, (_, arm) => arm).find((arm) => taken.get(site)?.has(arm) !== true) ?? 0);
    let fresh = 0;
    for (const [site, { arm, via }] of one.sites) {
      const arms = taken.get(site) ?? new Set<number>();
      fresh += arms.has(arm) ? 0 : 1;
      taken.set(site, arms.add(arm));
      if (!ways.has(site)) {
        ways.set(site, via);
      }
    }
    list.push(one);
    return fresh;
  };
  while (run(new Map()) > 0) {
    // each build takes, at every site it passes, an option not yet taken
  }
  for (let left = missing(list); left.length > 0; left = missing(list)) {
    // One build takes every option it can of those still short: the ones whose paths do not conflict.
    const forced = new Map<string, number>();
    for (const { site, arm } of left) {
      const wanted = new Map([...(ways.get(site) ?? new Map<string, number>()), [site, arm]]);
      if ([...wanted].every(([key, value]) => (forced.get(key) ?? value) === value)) {
        for (const [key, value] of wanted) {
          forced.set(key, value);
        }
      }
    }
    if (run(forced) === 0) {
      throw new Error(`no build reaches ${gaps(list).join("; ")}`);
    }
  }
  memo = list;
  return list;
}
