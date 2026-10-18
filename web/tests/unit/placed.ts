/**
 * Where the result screen's conditional words belong, read from a raw result by the test's own code
 * (never the page's): each function gives how many times a word must stand in that result's section
 * (D435's census, `placed`), and the booleans the page must show, per instance, in order. The
 * multiset holds the page's separators (a run of no letter) beside its words, and each data isolate
 * the section must hold: a leaf in its section, by its class and its form (`isolate`, D435's round 3).
 */
import { type Json, list, object } from "./result-support";
import { deferredColumn, readable } from "./sweep";

/** What the values seam makes of a raw result, by the test's own reading. */
export function readOf(raw: Json): "read" | "analysis" | "shape" {
  const analysis = object(object(raw["derivation"])["analysis"]);
  if (analysis["id"] !== "summary.distribution" || analysis["version"] !== "1.0.0") {
    return "analysis";
  }
  return readable(raw) ? "read" : "shape";
}

/** Whether the result is under a disclosure setting. */
export const underK = (raw: Json): boolean => object(object(raw["derivation"])["disclosure"])["min_cell_count"] !== null;

/** The labels of the view, as sent. */
export const labelsOf = (raw: Json): unknown[] => list(object(object(object(object(raw["derivation"])["document"])["view"])["params"])["columns"]);

/** The positions' columns of a read result (none for a result the page does not read). */
export function columnsOf(raw: Json): Json[] {
  if (readOf(raw) !== "read") {
    return [];
  }
  return list(object(raw["values"])["positions"]).flatMap((position) => list(object(position)["columns"]).map(object));
}

/** The numbers columns the page reads (not deferred). */
export const numbersShown = (raw: Json): Json[] => columnsOf(raw).filter((column) => column["kind"] === "numbers" && !deferredColumn(column, underK(raw)));

/** The numbers columns the page defers. */
export const numbersDeferred = (raw: Json): Json[] => columnsOf(raw).filter((column) => column["kind"] === "numbers" && deferredColumn(column, underK(raw)));

/** The categories columns. */
export const categoriesColumns = (raw: Json): Json[] => columnsOf(raw).filter((column) => column["kind"] === "categories");

/** The other columns. */
export const otherColumns = (raw: Json): Json[] => columnsOf(raw).filter((column) => column["kind"] !== "numbers" && column["kind"] !== "categories");

/** Whether a category row names no value and is no other_values row. */
export const unnamed = (row: Json): boolean => list(row["values"]).length === 0 && row["other_values"] !== true;

/** Every category row of the categories columns. */
export const rowsOf = (raw: Json): Json[] => categoriesColumns(raw).flatMap((column) => list(column["categories"]).map(object));

/** The proportions the page shows (rows that are not unnamed). */
export const proportionsShown = (raw: Json): Json[] => rowsOf(raw).filter((row) => !unnamed(row)).map((row) => object(row["proportion"]));

/** The bins of the histograms the page shows, in order. */
export const binsShown = (raw: Json): Json[] => numbersShown(raw).flatMap((column) => list(object(column["histogram"])["bins"]).map(object));

/** The six per-position lists' lengths, and the longest. */
export function lists(raw: Json): { readonly labels: number; readonly cohorts: number; readonly readbacks: number; readonly population: number; readonly analysed: number; readonly values: number; readonly longest: number } {
  const values = readOf(raw) === "read" ? list(object(raw["values"])["positions"]).length : 0;
  const lengths = {
    labels: list(raw["labels"]).length,
    cohorts: list(raw["cohorts"]).length,
    readbacks: list(object(raw["readback"])["cohorts"]).length,
    population: list(raw["population"]).length,
    analysed: list(raw["analysed"]).length,
    values,
  };
  return { ...lengths, longest: Math.max(...Object.values(lengths)) };
}

/** How many positions miss an entry of one list. */
export function missing(raw: Json, which: "labels" | "cohorts" | "readbacks" | "population" | "analysed" | "values"): number {
  const found = lists(raw);
  if (which === "values" && readOf(raw) !== "read") {
    return 0;
  }
  return found.longest - found[which];
}

/** Whether D434's `variables` reads as labelled for a read result. */
function labelled(raw: Json): boolean {
  const count = labelsOf(raw).length;
  return list(raw["analysed"]).every((entry) => {
    const variables = object(entry)["variables"];
    return variables === undefined ? count === 1 : list(variables).length === count;
  });
}

/** How many analysed entries show the unlabelled words. */
export function unlabelledNotes(raw: Json): number {
  if (readOf(raw) === "read" && labelled(raw)) {
    return 0;
  }
  return list(raw["analysed"]).filter((entry) => list(object(entry)["variables"]).length > 0).length;
}

/** Whether a figure is null with the reason `suppressed` at its pointer. */
const suppressedAt = (holder: Json, member: string): boolean => holder[member] === null && object(holder["not_estimable"])[`/${member}`] === "suppressed";

/** How many `suppressed` words the page shows for a raw result (each suppressed member, R14, R20). */
export function suppressedWords(raw: Json): number {
  let count = 0;
  const k = underK(raw);
  for (const population of list(raw["population"]).map(object)) {
    const listed = list(population["suppressed"]).map(String);
    for (const member of ["n_true", "n_false", "n_unknown"]) {
      count += population[member] === null || listed.includes(`/${member}`) ? 1 : 0;
    }
    for (const member of ["unknown_by_reason", "unknown_by_leaf"]) {
      const map = population[member];
      count += map === null || listed.includes(`/${member}`) ? 1 : Object.values(object(map)).filter((value) => value === null).length;
    }
    count += k || population["lift_differs"] === null || listed.includes("/lift_differs") ? 1 : 0;
  }
  const analysed = (entry: Json): void => {
    count += (entry["n"] === null ? 1 : 0) + (entry["excluded_units"] === null ? 1 : 0);
    count += entry["excluded"] === null ? 1 : Object.values(object(entry["excluded"])).filter((value) => value === null).length;
  };
  for (const entry of list(raw["analysed"]).map(object)) {
    analysed(entry);
    list(entry["variables"]).map(object).forEach(analysed);
  }
  for (const column of numbersShown(raw)) {
    count += ["n", "mean", "sd", "median", "q1", "q3", "min", "max", "histogram"].filter((member) => suppressedAt(column, member)).length;
  }
  for (const column of categoriesColumns(raw)) {
    count += suppressedAt(column, "categories") ? 1 : 0;
  }
  for (const proportion of proportionsShown(raw)) {
    count += ["numerator", "denominator", "estimate", "excluded"].filter((member) => suppressedAt(proportion, member)).length;
    count += proportion["excluded"] !== null && proportion["excluded"] !== undefined ? Object.values(object(proportion["excluded"])).filter((value) => value === null).length : 0;
  }
  return count;
}

/** Whether any caveat blocks (an unknown severity read as block). */
export const blocks = (raw: Json): boolean => list(raw["caveats"]).some((caveat) => !["info", "warn"].includes(String(object(caveat)["severity"])));

/** A multiset of words. */
export type Words = Map<string, number>;

const add = (words: Words, word: string, count = 1): void => {
  if (count > 0) {
    words.set(word, (words.get(word) ?? 0) + count);
  }
};

/** The prefix of an isolate's entry in a section's multiset (no word of the page holds it). */
export const ISOLATE = "\u0001";

/** How a leaf is isolated: `text` (`Text`: cut, class `data`), `ref` (`Ref`: whole, class `data`),
 * `token` (`Ref`'s data token: whole, class `data token`). */
export type Isolated = "text" | "ref" | "token";

/** One data isolate of a leaf, with the server's cut mark where a segment carries one. */
function isolate(words: Words, as: Isolated, value: unknown, truncated = false): void {
  add(words, `${ISOLATE}${as}${ISOLATE}${truncated ? "1" : "0"}${ISOLATE}${String(value)}`);
}

/** An isolate entry read back: its kind, the server's cut mark and its raw text. */
export function isolateOf(entry: string): { readonly as: Isolated; readonly truncated: boolean; readonly text: string } | null {
  if (!entry.startsWith(ISOLATE)) {
    return null;
  }
  const [as = "", truncated = "", ...rest] = entry.slice(ISOLATE.length).split(ISOLATE);
  return { as: as as Isolated, truncated: truncated === "1", text: rest.join(ISOLATE) };
}

/** A list's separators: one before every item but the first. */
const between = (words: Words, separator: string, items: number): void => {
  add(words, separator, items - 1);
};

/** The reasons' words (`Figure.tsx`'s `REASON_WORDS`, by the server's member). */
const REASONS: Readonly<Record<string, string>> = {
  no_units: "no units",
  no_events: "no events",
  zero_denominator: "a zero denominator",
  zero_variance: "zero variance",
  not_reached: "not reached",
  beyond_follow_up: "beyond the follow-up",
  separation: "separation",
  not_converged: "the fit did not converge",
  degenerate_table: "a degenerate table",
  overlapping_cohorts: "the cohorts overlap",
  confounded_with_dataset: "confounded with the dataset",
};

/** Whether a text is cut by `Text` (more than 2,000 code points). */
const cut = (text: unknown): boolean => typeof text === "string" && Array.from(text).length > 2000;

/** The words of a null figure (`Figure`): suppressed, not estimable with its reason, or not shown. */
function figure(words: Words, holder: Json, member: string): void {
  if (holder[member] !== null) {
    return;
  }
  const reasons = object(holder["not_estimable"]);
  const pointer = `/${member}`;
  if (!Object.hasOwn(reasons, pointer)) {
    add(words, "not shown: no reason was given");
    return;
  }
  const reason = String(reasons[pointer]);
  if (reason === "suppressed") {
    add(words, "suppressed");
    return;
  }
  add(words, "not estimable:");
  const known = REASONS[reason];
  if (known !== undefined) {
    add(words, known);
  } else {
    isolate(words, "text", reason);
  }
}

/** The cut marks of a list of segments (a data token the server cut), and each data token. */
function segments(words: Words, value: unknown): void {
  add(words, "cut", list(value).filter((segment) => object(segment)["truncated"] === true).length);
  for (const segment of list(value).map(object)) {
    if (Object.hasOwn(segment, "data")) {
      isolate(words, "token", segment["data"], segment["truncated"] === true);
    }
  }
}

/** The `Reason` vocabulary, in its order (a copy: the largest reason's ties). */
const REASON_ORDER = ["NOT_ASSESSED", "NOT_COVERED", "NO_INFORMATION", "NO_PARENT", "OUT_OF_SCOPE", "NO_ROWS"];

/** The key of a map's largest count, ties by the vocabulary's order, then by the smaller text. */
function largest(map: Json): string {
  const rank = (key: string): number => (REASON_ORDER.includes(key) ? REASON_ORDER.indexOf(key) : REASON_ORDER.length);
  let best: { readonly key: string; readonly value: number } | null = null;
  for (const [key, value] of Object.entries(map)) {
    const count = Number(value);
    if (best === null || count > best.value || (count === best.value && (rank(key) < rank(best.key) || (rank(key) === rank(best.key) && key < best.key)))) {
      best = { key, value: count };
    }
  }
  return best?.key ?? "";
}

/** A map's entries shown in a list: a key isolated, a separator between. */
function keyed(words: Words, map: unknown): void {
  const keys = Object.keys(object(map));
  between(words, ",", keys.length);
  keys.forEach((key) => {
    isolate(words, "ref", key);
  });
}

/** The words of a count (`Count`): suppressed where it is null. */
const count = (words: Words, value: unknown): void => {
  if (value === null) {
    add(words, "suppressed");
  }
};

/** The words of a population (`Population`). */
function population(words: Words, p: Json, k: boolean): void {
  const listed = list(p["suppressed"]).map(String);
  add(words, "population");
  add(words, "n_true");
  add(words, "; n_false");
  add(words, "; n_unknown");
  for (const member of ["n_true", "n_false", "n_unknown"]) {
    add(words, "suppressed", p[member] === null || listed.includes(`/${member}`) ? 1 : 0);
  }
  const unknown = listed.includes("/n_unknown") ? null : p["n_unknown"];
  const reasons = listed.includes("/unknown_by_reason") ? null : p["unknown_by_reason"];
  if (typeof unknown === "number" && unknown > 0 && reasons !== null && typeof reasons === "object") {
    const values = Object.values(reasons as Json);
    if (values.every((value) => typeof value === "number" && Number.isInteger(value) && value >= 0) && values.some((value) => typeof value === "number" && value > 0)) {
      add(words, "(largest reason");
      add(words, ")");
      isolate(words, "ref", largest(reasons as Json));
    }
  }
  for (const [member, label] of [
    ["unknown_by_reason", "n_unknown by reason:"],
    ["unknown_by_leaf", "n_unknown by leaf:"],
  ] as const) {
    add(words, label);
    const map = p[member];
    if (map === null || listed.includes(`/${member}`)) {
      add(words, "suppressed");
    } else if (Object.keys(object(map)).length === 0) {
      add(words, "none");
    } else {
      keyed(words, map);
      Object.values(object(map)).forEach((value) => {
        count(words, value);
      });
    }
  }
  add(words, "lift_differs");
  add(words, "suppressed", k || p["lift_differs"] === null || listed.includes("/lift_differs") ? 1 : 0);
}

/** The three counts of an analysed entry or variable (`Analysed`'s `Three`). */
function three(words: Words, entry: Json): void {
  add(words, "n");
  add(words, "; excluded_units");
  add(words, "; excluded by reason");
  count(words, entry["n"]);
  count(words, entry["excluded_units"]);
  if (entry["excluded"] === null) {
    add(words, "suppressed");
  } else {
    keyed(words, entry["excluded"]);
    Object.values(object(entry["excluded"])).forEach((value) => {
      count(words, value);
    });
  }
}

/** The words of an analysed entry (`Analysed`), its labels or none. */
function analysed(words: Words, entry: Json, labels: readonly unknown[] | null): void {
  add(words, "analysed");
  add(words, "Analysed:");
  three(words, entry);
  const variables = list(entry["variables"]).map(object);
  if (variables.length > 0 && labels === null) {
    add(words, "Per variable, in the view's column order (labels not shown: the page and the server disagree about them):");
  }
  variables.forEach((variable, index) => {
    const label = labels?.[index];
    if (label !== undefined) {
      add(words, "cut", cut(label) ? 1 : 0);
      isolate(words, "text", label);
      add(words, ":");
    }
    three(words, variable);
  });
}

/** The words of a proportion (`Proportion`). */
function proportion(words: Words, value: Json): void {
  for (const word of ["numerator", "; denominator", "; estimate", "; denominator definition: counts", ", position", ", predicate"]) {
    add(words, word);
  }
  for (const member of ["numerator", "denominator", "estimate"]) {
    figure(words, value, member);
  }
  const definition = object(value["denominator_definition"]);
  add(words, "none given", (definition["position"] === null ? 1 : 0) + (definition["predicate"] === null ? 1 : 0));
  isolate(words, "token", definition["counts"]);
  if (definition["predicate"] !== null) {
    isolate(words, "token", definition["predicate"]);
  }
  if (Object.hasOwn(value, "denominator_text")) {
    add(words, "(");
    add(words, ")");
    segments(words, value["denominator_text"]);
  }
  if (Object.hasOwn(value, "excluded")) {
    add(words, "; excluded");
    if (value["excluded"] === null) {
      figure(words, value, "excluded");
    } else {
      keyed(words, value["excluded"]);
      Object.values(object(value["excluded"])).forEach((one) => {
        count(words, one);
      });
    }
  }
  if (Object.hasOwn(value, "ci")) {
    add(words, "This proportion has an interval, not shown on this page yet (R16).");
  }
}

/** The words of one column (`Values`' `ColumnOf`). */
function column(words: Words, raw: Json, value: Json): void {
  if (value["kind"] === "numbers") {
    if (deferredColumn(value, underK(raw))) {
      add(words, "This column's numbers are not shown on this page yet (R34b).");
      return;
    }
    for (const member of ["n", "mean", "sd", "median", "q1", "q3", "min", "max"]) {
      add(words, member);
      figure(words, value, member);
    }
    between(words, ";", 8);
    add(words, "histogram");
    if (value["histogram"] === null) {
      figure(words, value, "histogram");
      return;
    }
    add(words, ": edges from");
    isolate(words, "token", object(value["histogram"])["edges_from"]);
    for (const bin of list(object(value["histogram"])["bins"]).map(object)) {
      for (const word of ["low", ", high", ", includes_low", ", includes_high", ", count"]) {
        add(words, word);
      }
      add(words, String(bin["includes_low"]));
      add(words, String(bin["includes_high"]));
      add(words, "no lower edge", bin["low"] === null ? 1 : 0);
      add(words, "no upper edge", bin["high"] === null ? 1 : 0);
    }
    return;
  }
  if (value["kind"] === "categories") {
    const rows = value["categories"];
    if (rows === null) {
      add(words, "categories");
      figure(words, value, "categories");
      return;
    }
    if (list(rows).length === 0) {
      add(words, "This column lists no category row for this position.");
      return;
    }
    for (const row of list(rows).map(object)) {
      if (unnamed(row)) {
        add(words, "A row that names no category: not shown (the page and the server disagree).");
        continue;
      }
      add(words, "other_values", row["other_values"] === true ? 1 : 0);
      list(row["values"]).forEach((one, index) => {
        add(words, "cut", cut(object(one)["data"]) ? 1 : 0);
        add(words, ",", index > 0 || row["other_values"] === true ? 1 : 0);
        isolate(words, "text", object(one)["data"]);
      });
      add(words, ":");
      proportion(words, object(row["proportion"]));
    }
    return;
  }
  add(words, "Values of this kind are not shown on this page yet (R29):");
  isolate(words, "token", value["kind"]);
}

/** The words of a release (`ReleaseRef`). */
function release(words: Words, value: Json): void {
  const published = value["status"] === "published" && typeof value["label"] === "number";
  const draft = value["status"] === "draft" && value["label"] === "draft";
  if (Object.hasOwn(value, "dataset")) {
    isolate(words, "ref", value["dataset"]);
  }
  add(words, "@", published ? 1 : 0);
  isolate(words, "ref", value["manifest"]);
  add(words, "@draft", published ? 0 : 1);
  add(words, "(its label and its status disagree: shown as a draft)", published || draft ? 0 : 1);
  add(words, "· manifest");
}

/** Every word a result's section shows, from the raw result alone (the page mirrored by the test's own
 * code): each text run that is a census key or a separator, each attribute value a person is shown,
 * and each data isolate (`isolate`): a leaf in its section, as often as the page shows it. */
export function wordsOfResult(raw: Json): Words {
  const words: Words = new Map();
  const caveats = list(raw["caveats"]).map(object);
  if (blocks(raw)) {
    add(words, "for inspection, not an answer");
    add(words, "For inspection, not an answer: a blocking caveat applies to this output (§8.3).");
  }
  add(words, "caveats", caveats.length > 0 ? 1 : 0);
  for (const caveat of caveats) {
    const severity = String(caveat["severity"]);
    add(words, severity === "info" ? "note" : severity === "warn" ? "warning" : "block");
    segments(words, caveat["message"]);
    add(words, "affects:", list(caveat["affects"]).length > 0 ? 1 : 0);
    isolate(words, "ref", caveat["code"]);
    add(words, "(");
    add(words, "):");
    between(words, ",", list(caveat["affects"]).length);
    list(caveat["affects"]).forEach((pointer) => {
      isolate(words, "ref", pointer);
    });
  }
  add(words, "view");
  add(words, "Analysis");
  add(words, "version", 2);
  const analysis = object(object(raw["derivation"])["analysis"]);
  isolate(words, "ref", analysis["id"]);
  isolate(words, "ref", analysis["version"]);
  if (underK(raw)) {
    add(words, "Disclosure: minimum cell count k =");
    add(words, ": counts of units from 1 to k − 1, and counts that would reveal them, are suppressed (§8.4).");
  } else {
    add(words, "Disclosure: none (no minimum cell count applies).");
  }
  const readback = object(raw["readback"]);
  segments(words, readback["view"]);
  const read = readOf(raw);
  add(words, read === "read" ? "This result gives no values for the view as a whole." : read === "analysis" ? "This page does not read the values of this analysis and version yet." : "The values are not shown: the page and the server disagree about their shape.");
  const labelList = labelsOf(raw).map((one) => object(one)["column"]);
  const variableLabels = read === "read" && unlabelledNotes(raw) === 0 ? labelList : null;
  const found = lists(raw);
  const positions = read === "read" ? list(object(raw["values"])["positions"]).map(object) : [];
  for (let index = 0; index < found.longest; index += 1) {
    add(words, "position");
    const label = list(raw["labels"])[index];
    if (label === undefined) {
      add(words, "This position's label is not listed.");
    } else {
      add(words, "cut", cut(object(label)["data"]) ? 1 : 0);
      isolate(words, "text", object(label)["data"]);
    }
    const cohort = list(raw["cohorts"])[index];
    if (cohort === undefined) {
      add(words, "This position's cohort is not listed.");
    } else {
      add(words, "position");
      add(words, ", cohort");
      add(words, "the reference position", object(cohort)["reference"] === true ? 1 : 0);
      add(words, ",", object(cohort)["reference"] === true ? 1 : 0);
      isolate(words, "ref", object(cohort)["id"]);
    }
    const back = list(readback["cohorts"])[index];
    if (back === undefined) {
      add(words, "This position's readback is not listed.");
    } else {
      segments(words, back);
    }
    const p = list(raw["population"])[index];
    if (p === undefined) {
      add(words, "This position's population is not listed.");
    } else {
      population(words, object(p), underK(raw));
    }
    const entry = list(raw["analysed"])[index];
    if (entry === undefined) {
      add(words, "This position's analysed counts are not listed.");
    } else {
      analysed(words, object(entry), variableLabels);
    }
    if (read === "read") {
      const position = positions[index];
      if (position === undefined) {
        add(words, "This position's values are not listed.");
      } else {
        add(words, "values");
        list(position["columns"]).forEach((one, at) => {
          add(words, "column");
          const name = labelList[at];
          if (name === undefined) {
            add(words, "This column's label is not listed.");
          } else {
            add(words, "cut", cut(name) ? 1 : 0);
            isolate(words, "text", name);
          }
          column(words, raw, object(one));
        });
      }
    }
  }
  const derivation = object(raw["derivation"]);
  add(words, "provenance");
  for (const word of ["Derivation", ": analysis", "Releases:", "Packs:", "Semantics version", "; engine", "Digest", "Issuance", "Leaves:"]) {
    add(words, word);
  }
  isolate(words, "ref", derivation["id"]);
  isolate(words, "ref", analysis["id"]);
  isolate(words, "ref", analysis["version"]);
  between(words, ",", list(derivation["releases"]).length);
  list(derivation["releases"]).forEach((one) => {
    release(words, object(one));
  });
  const packs = Object.entries(object(derivation["packs"]));
  add(words, "none", packs.length === 0 ? 1 : 0);
  add(words, "version", packs.length);
  add(words, "results_version", packs.length);
  between(words, ",", packs.length);
  for (const [name, pack] of packs) {
    isolate(words, "ref", name);
    isolate(words, "ref", object(pack)["version"]);
  }
  isolate(words, "ref", derivation["engine"]);
  isolate(words, "ref", raw["digest"]);
  const issuance = object(raw["issuance"]);
  isolate(words, "ref", issuance["id"]);
  add(words, ":");
  add(words, issuance["cache_hit"] === true ? "served from the result cache: its values come from issuance" : "computed for this request.");
  if (issuance["cache_hit"] === true) {
    isolate(words, "ref", issuance["values_from"]);
    add(words, ".");
  }
  const leaves = Object.entries(object(object(raw["source"])["leaves"]));
  add(words, "none", leaves.length === 0 ? 1 : 0);
  between(words, ";", leaves.length);
  for (const [pointer, ids] of leaves) {
    isolate(words, "ref", pointer);
    between(words, ",", list(ids).length);
    list(ids).forEach((id) => {
      isolate(words, "ref", id);
    });
  }
  for (const word of [
    "The document as written is not shown on this page yet (R26b).",
    "The parameters as written are not shown on this page yet (R28b).",
    "The derivation id names this result; an agent reads its derivation with explain.",
    "This page keeps the result while it stays open: a reload clears it (the result is not fetched again). If the form changes, the result stays under the words that say so, until the next run.",
  ]) {
    add(words, word);
  }
  add(words, list(raw["charts"]).length === 0 ? "This result has no chart." : "This result carries charts; none is drawn on this page (charts come with M5.C). A chart not drawn is not a chart with no data.");
  return words;
}

/** Every word the parameters' section shows (`Parameters`), from the raw answer. */
export function wordsOfParameters(raw: Json): Words {
  const words: Words = new Map();
  const params = object(raw["params"]);
  add(words, "parameters");
  add(words, "Parameters used:");
  add(words, Object.keys(object(params["used"])).length === 0 ? "none" : "not shown here yet (R28b).");
  add(words, "Parameters not used:");
  const unused = list(params["unused"]);
  add(words, "none", unused.length === 0 ? 1 : 0);
  between(words, ",", unused.length);
  unused.forEach((one) => {
    add(words, "cut", cut(object(one)["data"]) ? 1 : 0);
    isolate(words, "text", object(one)["data"]);
  });
  return words;
}
