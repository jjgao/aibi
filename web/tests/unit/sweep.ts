/**
 * The result screen's sweeps on raw bodies, through the real seam (D435, B7.2–B7.3): a body is JSON;
 * its leaves are found by walking it beside its walked answer (a string the walk boxed as a
 * `ServerText`, a number it boxed as a `ServerNumber`, and each key of a map the walk made a
 * `BoxedMap`), each at its normalised path (`deferred.ts`' header). A leaf is changed **in the raw
 * body**, which is encoded, decoded and walked again at a stubbed `fetch`, and the answer rendered as
 * the run view renders it, so `valuesOf` reads each result as the page does.
 *
 * `BULK` subtrees (members the page never reads: charts, documents and parameters as written) are
 * swept in one rendering per body, each of their leaves changed to a marker of its own; every other
 * leaf is swept alone.
 */
import { BoxedMap, entriesOf, isServerNumber, readText, ServerText } from "../../src/api/box";
import { countTexts } from "./injection";
import { list, markupOf, object, type Ran, ran } from "./result-support";

export type Path = readonly (string | number)[];

/** A leaf of a raw body. */
export interface Leaf {
  readonly at: Path;
  readonly kind: "text" | "number" | "key" | "null";
  readonly path: string;
}

/** What the test's own code reads of a raw result (never the page's). */
interface Context {
  readonly k: boolean;
  readonly read: boolean;
}

/** Whether a raw result is read by the values seam, by the test's own reading: its analysis and
 * version, labels that are all text, and every position as many columns as labels. */
export function readable(result: Record<string, unknown>): boolean {
  const derivation = object(result["derivation"]);
  const analysis = object(derivation["analysis"]);
  if (analysis["id"] !== "summary.distribution" || analysis["version"] !== "1.0.0") {
    return false;
  }
  const labels = list(object(object(object(derivation["document"])["view"])["params"])["columns"]).map((column) => object(column)["column"]);
  if (labels.length === 0 || !labels.every((label) => typeof label === "string")) {
    return false;
  }
  return list(object(result["values"])["positions"]).every((position) => list(object(position)["columns"]).length === labels.length);
}

/** A numbers column the page defers (B2's condition, restated): under a setting, or with a bin index. */
export function deferredColumn(column: Record<string, unknown>, underK: boolean): boolean {
  return underK || ["q1_bin", "median_bin", "q3_bin"].some((member) => Object.hasOwn(column, member));
}

/** The arm segment of a column of a position. */
function armOf(column: unknown, context: Context): string {
  const kind = object(column)["kind"];
  if (kind === "numbers") {
    return deferredColumn(object(column), context.k) ? "[numbers deferred]" : "[numbers]";
  }
  return kind === "categories" ? "[categories]" : "[other]";
}

/** The segment of a list's item. */
function itemSegment(norm: string, item: unknown, context: Context): string {
  if (norm === "results/[]/values/positions/[]/columns") {
    return armOf(item, context);
  }
  if (norm.endsWith("/[categories]/categories")) {
    const row = object(item);
    return list(row["values"]).length === 0 && row["other_values"] !== true ? "[unnamed]" : "[]";
  }
  return "[]";
}

/** The segment of a record's member. */
function memberSegment(norm: string, name: string, raw: Record<string, unknown>, context: Context): string {
  if (norm === "results/[]") {
    if (name === "values") {
      return context.read ? "values" : "values(unread)";
    }
    if (name === "issuance") {
      return object(raw["issuance"])["cache_hit"] === true ? "issuance(hit)" : "issuance(computed)";
    }
    if (name === "population") {
      return context.k ? "population(k)" : "population";
    }
  }
  return name;
}

/** Every leaf of a raw body, beside its walked answer. */
export function leavesOf(raw: unknown, walked: unknown): Leaf[] {
  const out: Leaf[] = [];
  const visit = (value: unknown, boxed: unknown, at: Path, norm: string, context: Context): void => {
    if (value === null) {
      out.push({ at, kind: "null", path: `${norm}@null` });
      return;
    }
    if (typeof value === "string") {
      if (ServerText.holds(boxed)) {
        // A label of a result whose values the page does not read is marked so.
        const label = norm === "results/[]/derivation/document/view/params/columns/[]/column" && !context.read;
        out.push({ at, kind: "text", path: `${norm}${label ? "(unread)" : ""}@text` });
      }
      return;
    }
    if (typeof value === "number") {
      if (isServerNumber(boxed)) {
        out.push({ at, kind: "number", path: `${norm}@number` });
      }
      return;
    }
    if (Array.isArray(value)) {
      const items = Array.isArray(boxed) ? (boxed as readonly unknown[]) : [];
      value.forEach((item: unknown, index) => {
        let inner = context;
        if (norm === "results") {
          const result = object(item);
          inner = { k: object(object(result["derivation"])["disclosure"])["min_cell_count"] !== null, read: readable(result) };
        }
        const segment = norm === "results" ? "[]" : itemSegment(norm, item, inner);
        visit(item, items[index], [...at, index], `${norm}/${segment}`, inner);
      });
      return;
    }
    if (typeof value !== "object") {
      return;
    }
    const record = value as Record<string, unknown>;
    if (BoxedMap.holds(boxed)) {
      const entries = entriesOf(boxed);
      Object.keys(record).forEach((key, index) => {
        const entry = entries[index];
        if (entry === undefined || readText(entry[0]) !== key) {
          throw new Error(`a map's keys out of order at ${norm}`);
        }
        out.push({ at: [...at, key], kind: "key", path: `${norm}/{key}@text` });
        visit(record[key], entry[1], [...at, key], `${norm}/${/^[a-z][a-z_]*$/u.test(key) ? key : "{}"}`, context);
      });
      return;
    }
    const boxedRecord = typeof boxed === "object" && boxed !== null ? (boxed as Record<string, unknown>) : {};
    for (const key of Object.keys(record)) {
      visit(record[key], boxedRecord[key], [...at, key], norm === "" ? key : `${norm}/${memberSegment(norm, key, record, context)}`, context);
    }
  };
  visit(raw, walked, [], "", { k: false, read: false });
  return out;
}

/** A raw body with the leaf at `at` replaced (a key renamed in place, keeping the members' order). */
export function replaced(raw: unknown, leaf: Leaf, by: string | number): unknown {
  const copy: unknown = structuredClone(raw);
  const parentAt = leaf.at.slice(0, -1);
  const last = leaf.at.at(-1);
  let parent: unknown = copy;
  for (const step of parentAt) {
    parent = (parent as Record<string | number, unknown>)[step];
  }
  if (leaf.kind === "key" && typeof last === "string") {
    // The map rebuilt with the key renamed in its place, set where the map was.
    const renamed = Object.fromEntries(Object.entries(parent as Record<string, unknown>).map(([key, member]) => [key === last ? String(by) : key, member]));
    const holderAt = parentAt.slice(0, -1);
    const step = parentAt.at(-1);
    if (step === undefined) {
      return renamed;
    }
    let holder: unknown = copy;
    for (const one of holderAt) {
      holder = (holder as Record<string | number, unknown>)[one];
    }
    (holder as Record<string | number, unknown>)[step] = renamed;
    return copy;
  }
  if (last !== undefined) {
    (parent as Record<string | number, unknown>)[last] = by;
  }
  return copy;
}

/** The subtrees swept in one rendering (members the page never reads), and the keys along the label
 * path, which the seam reads by name and so are swept with the rest. */
const BULK = /^(results\/\[\]\/charts\/|results\/\[\]\/source\/(document|params)\/|params\/used\/|results\/\[\]\/derivation\/document\/)/u;
const ALONE = /^results\/\[\]\/derivation\/document\/(view\/(params\/(columns\/\[\]\/)?)?)?(\{key\}|column|column\(unread\))@text$/u;

/** The words a result's values read of another shape shows (`Values.tsx`' `VALUE_WORDS.shape`). */
export const SHAPE_WORDS = "The values are not shown: the page and the server disagree about their shape.";

/** What a sweep of one body found: every path, the paths of which a leaf is shown, those of which a
 * leaf is not, the paths whose change the walk refuses (`refused`) or the values model's walk refuses
 * (`unread`: the result's values read as of another shape), and the leaf count and the walked
 * answer's text count. */
export interface Found {
  readonly all: Set<string>;
  readonly shown: Set<string>;
  readonly missed: Set<string>;
  readonly refused: Set<string>;
  readonly unread: Set<string>;
  readonly leaves: number;
  readonly texts: number;
  readonly textLeaves: number;
}

/** The marker of the `index`th leaf of a change: a text, or a number of its own source text. */
const markerOf = (leaf: Leaf, index: number): string | number => (leaf.kind === "number" ? 98765432100 + index : `zqmark${String(index)}qz`);

/** A body with `leaves` changed to their markers (last first: a key is renamed after every leaf
 * below it, whose path names the key as it was). */
function changedAll(raw: unknown, leaves: readonly Leaf[]): unknown {
  let changed: unknown = raw;
  for (let index = leaves.length - 1; index >= 0; index -= 1) {
    const leaf = leaves[index];
    if (leaf !== undefined) {
      changed = replaced(changed, leaf, markerOf(leaf, index));
    }
  }
  return changed;
}

/** Sweeps one raw body. Every instance of one normalised path is changed at once, each to a marker of
 * its own, and the body rendered: each instance is shown when its marker is (so a list's second item
 * is judged apart from its first); the `BULK` subtrees are changed together, in one rendering. `only`
 * limits the paths swept (a spliced body's values). */
export async function sweep(raw: unknown, only: (path: string) => boolean = () => true): Promise<Found> {
  const walked: Ran = await ran(JSON.stringify(raw));
  const original = await markupOf(JSON.stringify(raw));
  const leaves = leavesOf(raw, walked).filter((leaf) => leaf.kind !== "null");
  const found: Found = {
    all: new Set(),
    shown: new Set(),
    missed: new Set(),
    refused: new Set(),
    unread: new Set(),
    leaves: leaves.length,
    texts: countTexts(walked),
    textLeaves: leaves.filter((leaf) => leaf.kind !== "number").length,
  };
  const groups = new Map<string, Leaf[]>();
  for (const leaf of leaves.filter((one) => only(one.path))) {
    const key = BULK.test(leaf.path) && !ALONE.test(leaf.path) ? "bulk" : leaf.path;
    groups.set(key, [...(groups.get(key) ?? []), leaf]);
  }
  for (const group of groups.values()) {
    let markup: string;
    try {
      markup = await markupOf(JSON.stringify(changedAll(raw, group)));
    } catch {
      group.forEach((leaf) => found.refused.add(leaf.path));
      continue;
    }
    if (markup.includes(SHAPE_WORDS) && !original.includes(SHAPE_WORDS)) {
      group.forEach((leaf) => found.unread.add(leaf.path));
      continue;
    }
    group.forEach((leaf, index) => {
      found.all.add(leaf.path);
      (markup.includes(String(markerOf(leaf, index))) ? found.shown : found.missed).add(leaf.path);
    });
  }
  return found;
}
/** A body with every list doubled (a copy of its last item appended), so that each member is swept
 * for a second instance. */
export function doubled(value: unknown): unknown {
  if (Array.isArray(value)) {
    const items = value.map(doubled);
    const last = items.at(-1);
    return last === undefined ? items : [...items, structuredClone(last)];
  }
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([key, member]) => [key, doubled(member)]));
  }
  return value;
}

const JSON_TREES = /^(results\/\[\]\/(values|values\(unread\)|charts|derivation\/document|source\/document|source\/params)\/|params\/used\/)/u;

/** A body with each data-keyed map's last entry copied under a fresh key (`<key>_2`), one map at a
 * time, where the walk still accepts the body and the values still read as they did (a map whose
 * keys a vocabulary or a pattern checks is left as it is: its entries are held by named cells), so
 * that a map's second entry is swept as a list's second item is (m3 of round 1). */
export async function withMapsDoubled(raw: unknown): Promise<unknown> {
  let body: unknown = raw;
  const original = await markupOf(JSON.stringify(raw));
  const walked: Ran = await ran(JSON.stringify(raw));
  const maps = new Map<string, Path>();
  for (const leaf of leavesOf(raw, walked)) {
    // The envelope's data-keyed maps: not the JSON trees the walk reads as maps of member names (the
    // values, the charts, the documents and parameters), whose keys are names, never data.
    if (leaf.kind === "key" && !JSON_TREES.test(leaf.path)) {
      const at = leaf.at.slice(0, -1);
      maps.set(JSON.stringify(at), at);
    }
  }
  for (const at of maps.values()) {
    const candidate: unknown = structuredClone(body);
    let map: unknown = candidate;
    for (const step of at) {
      map = (map as Record<string | number, unknown>)[step];
    }
    const record = map as Record<string, unknown>;
    const last = Object.keys(record).at(-1);
    if (last === undefined) {
      continue;
    }
    let fresh = `${last}_2`;
    while (Object.hasOwn(record, fresh)) {
      fresh = `${fresh}_2`;
    }
    record[fresh] = structuredClone(record[last]);
    try {
      const markup = await markupOf(JSON.stringify(candidate));
      if (markup.includes(SHAPE_WORDS) && !original.includes(SHAPE_WORDS)) {
        continue;
      }
    } catch {
      continue;
    }
    body = candidate;
  }
  return body;
}

/** A body with every list doubled and then every data-keyed map that allows it. */
export async function doubledAll(raw: unknown): Promise<unknown> {
  return withMapsDoubled(doubled(raw));
}

