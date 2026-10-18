/**
 * What the result screen shows of a whole answer (D435, B7.2–B7.4), on raw bodies through the real
 * seam (`sweep.ts`): every leaf of every body changed in place, the answer decoded, walked and read
 * again, and rendered as the run view renders it.
 *
 * - **Shown, exactly.** The paths whose leaf the page does not show equal the paths `DEFERRED`
 *   matches (`deferred.ts`), per instance (every list doubled), over the live answers, the schema's
 *   `DistributionValues` and `DistributionPosition` bodies spliced into the built answer with the
 *   disclosure setting unset and set (every arm), and the schema's synthetic `AnalysisResults`. The
 *   leaf count is held to the walked answer's `countTexts`. The paths whose change the walk, or the
 *   values model's walk, refuses are pinned: a vocabulary the schema checks, a reviewed change.
 * - **The runtime census.** Every body above and the short, empty and long ones, rendered once: each
 *   section's runs, separators, shown attributes and data isolates (by class and shown text) equal as
 *   a multiset the test's own mirror of the raw body (`placed.ts`).
 * - **The numbers oracle.** Over the same bodies, every number of a body is rewritten with a source
 *   text of its own (`1e-07`, `-0.0`, `1.10`, `9007199254740993`, `5e-321` among them); the multiset
 *   of the numbers the page shows equals the body's numbers outside `DEFERRED`, and the page makes
 *   none; the null words equal the nulls the page shows.
 * - **Alignment (T7).** Each position's texts stay in its own section; a hostile text at every shown
 *   text position is shown as text alone.
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ENGINE, visibleForm } from "../../src/api/visible";
import { CUT, UNSAFE } from "../../src/api/visible-ranges";
import { CENSUS, RUNTIME_ONLY } from "./census";
import { DEFERRED, deferredAt, REFUSED, UNREAD } from "./deferred";
import { HOSTILE, problemsOf } from "./injection";
import * as placed from "./placed";
import { changed, type Json, LIVE, list, liveBody, markupOf, object, ran, RUNS, stringOf } from "./result-support";
import { doubledAll, type Found, type Leaf, leavesOf, replaced, sweep } from "./sweep";
import { bodies, PLANS, REF, synthetic, UNMADE } from "./typed-support";

/** A live answer whose first result's values (and labels and analysed variables, to match) are
 * replaced by a values body of the schema's, under a setting or none (D434's `spliced`). */
function spliced(values: unknown, minCellCount: number | null): Json {
  return changed(LIVE.a, (result) => {
    result["values"] = values;
    const columns = list(object(list(object(values)["positions"])[0])["columns"]).length;
    const count = columns === 0 ? 1 : columns;
    const derivation = object(result["derivation"]);
    object(derivation["disclosure"])["min_cell_count"] = minCellCount;
    object(object(object(derivation["document"])["view"])["params"])["columns"] = Array.from({ length: count }, (_unused, index) => ({ column: `t.c${String(index)}` }));
    for (const entry of list(result["analysed"])) {
      const variables = list(object(entry)["variables"]);
      object(entry)["variables"] = Array.from({ length: count }, () => variables[0]);
    }
  });
}

/** The schema's values bodies (every arm, `PLANS`' and the forced ones). */
const MADE = (): unknown[] =>
  bodies().flatMap((body) => (body.component === "DistributionValues" ? [body.value] : body.component === "DistributionPosition" ? [{ positions: [body.value], view: {} }] : []));

/** The schema's synthetic answers, every plan, whole and minimal. */
const SYNTHETIC = (): unknown[] =>
  PLANS.flatMap((plan) =>
    [false, true].flatMap((minimal) => {
      const body = synthetic(REF("AnalysisResults"), 0, minimal, plan);
      return body === UNMADE ? [] : [body];
    }),
  );

/** The floor-5 answer (d) with its empty cohort's row stripped of other_values. */
const UNNAMED = (): Json =>
  changed(LIVE.d, (result) => {
    delete object(list(object(list(object(list(object(result["values"])["positions"])[1])["columns"])[0])["categories"])[0])["other_values"];
  });

const inValues = (at: string): boolean => at.startsWith("results/[]/values");

const WEB = path.join(import.meta.dirname, "../..");

/** The problems of the sweeps against `DEFERRED`: a deferred path shown, a path not deferred not
 * shown (any instance), an entry that matches no path. */
function problemsOf_(found: readonly Found[]): string[] {
  const problems = new Set<string>();
  const used = new Set<RegExp>();
  for (const one of found) {
    for (const path of [...one.all].sort()) {
      const entry = deferredAt(path);
      if (entry !== undefined) {
        used.add(entry.path);
        if (one.shown.has(path)) {
          problems.add(`shown, but deferred (${entry.row}): ${path}`);
        }
      } else if (one.missed.has(path)) {
        problems.add(`not shown, and not deferred${one.shown.has(path) ? " (some instances)" : ""}: ${path}`);
      }
    }
  }
  for (const entry of DEFERRED) {
    if (!used.has(entry.path)) {
      problems.add(`an entry that matches no path: ${entry.path.source}`);
    }
  }
  return [...problems].sort();
}

/** A body the sweeps read: its raw JSON, and the paths swept (a spliced body's values alone). */
interface Swept {
  readonly raw: unknown;
  readonly only: (path: string) => boolean;
}

const EVERY = (): boolean => true;

/** Every body the sweeps read, each as it is and with its lists and data-keyed maps doubled, made once. */
let made: Promise<Swept[]> | undefined;
function sweptBodies(): Promise<Swept[]> {
  made ??= (async () => {
    const out: Swept[] = [];
    const both = async (raw: unknown, only: (path: string) => boolean = EVERY): Promise<void> => {
      out.push({ raw, only }, { raw: await doubledAll(raw), only });
    };
    for (const [, body] of RUNS) {
      await both(JSON.parse(body) as unknown);
    }
    for (const values of MADE()) {
      for (const k of [null, 5]) {
        await both(spliced(values, k), inValues);
      }
    }
    for (const body of SYNTHETIC()) {
      await both(body);
    }
    // A row that names no value and is no other_values row (m14): no body made from the table has
    // one, so it is made from the floor-5 answer (d) by dropping the member.
    await both(UNNAMED());
    // A result whose labels and columns disagree in count: the values read as another shape.
    await both(
      changed(LIVE.a, (result) => {
        list(object(object(object(object(result["derivation"])["document"])["view"])["params"])["columns"]).pop();
      }),
    );
    return out;
  })();
  return made;
}

/** Every sweep, made once. */
let swept: Promise<Found[]> | undefined;
function sweeps(): Promise<Found[]> {
  swept ??= (async () => {
    const found: Found[] = [];
    for (const { raw, only } of await sweptBodies()) {
      found.push(await sweep(raw, only));
    }
    return found;
  })();
  return swept;
}

describe("what the result screen shows of a whole answer (B7.2)", () => {
  it("the paths not shown are exactly DEFERRED's, per instance, over the live answers, every values arm with and without a setting, and the synthetic answers", { timeout: 900_000 }, async () => {
    const found = await sweeps();
    expect(found.length).toBeGreaterThan(40);
    expect(problemsOf_(found).join("\n")).toBe("");
    for (const one of found) {
      expect(one.textLeaves).toBe(one.texts);
    }
    // What the sweep reached: the deferred arm, the unnamed row, an interval, a cache hit.
    const all = new Set(found.flatMap((one) => [...one.all]));
    for (const path of [
      "results/[]/values/positions/[]/columns/[numbers deferred]/n@number",
      "results/[]/values/positions/[]/columns/[numbers]/histogram/bins/[]/low@number",
      "results/[]/issuance(hit)/values_from@text",
      "results/[]/derivation/document/view/params/columns/[]/column@text",
    ]) {
      expect(all.has(path), path).toBe(true);
    }
  });

  it("the paths whose change the walk refuses, and those the values model's walk refuses (the result reads as another shape), are exactly these, each with the cell that holds it (a reviewed change)", { timeout: 900_000 }, async () => {
    const found = await sweeps();
    const refused = [...new Set(found.flatMap((one) => [...one.refused]))].sort();
    const unread = [...new Set(found.flatMap((one) => [...one.unread]))].sort();
    expect({ refused, unread }).toEqual({ refused: Object.keys(REFUSED).sort(), unread: Object.keys(UNREAD).sort() });
    // Each names the cell that holds what the page shows of it: a file and a title it holds.
    const missing = Object.entries({ ...REFUSED, ...UNREAD }).flatMap(([path, cell]) => {
      const [file = "", title = ""] = cell.split("::");
      const text = existsSync(`${WEB}/${file}`) ? readFileSync(`${WEB}/${file}`, "utf8") : "";
      return text.includes(title) && title !== "" ? [] : [`${path}: ${cell}`];
    });
    expect(missing).toEqual([]);
  });

  it("controls: a deferred entry dropped, and one that matches a shown path, each fail", { timeout: 120_000 }, async () => {
    const found = [await sweep(liveBody(LIVE.a))];
    const shown = [...(found[0]?.shown ?? [])][0] ?? "";
    expect(problemsOf_(found).filter((one) => !one.startsWith("an entry that matches no path"))).toEqual([]);
    const one: Found = { ...(found[0] as Found), shown: new Set([...(found[0]?.shown ?? []), "results/[]/charts/[]/x@text"]), all: new Set([...(found[0]?.all ?? []), "results/[]/charts/[]/x@text"]) };
    expect(problemsOf_([one])).toContain("shown, but deferred (R30, R31b): results/[]/charts/[]/x@text");
    const missed: Found = { ...(found[0] as Found), missed: new Set([...(found[0]?.missed ?? []), shown]) };
    expect(problemsOf_([missed])).toContain(`not shown, and not deferred (some instances): ${shown}`);
  });
});

/** A body whose segments' server words (`{text}` segments: readbacks, messages, denominator texts) are
 * one sentinel, so that the runtime census tells the page's words from the server's. */
const SEGMENT = "zqsegmentqz";
function segmented(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(segmented);
  }
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    if (typeof record["text"] === "string" && Object.keys(record).every((key) => key === "text" || key === "truncated")) {
      return { ...record, text: SEGMENT };
    }
    return Object.fromEntries(Object.entries(record).map(([key, member]) => [key, segmented(member)]));
  }
  return value;
}

const normal = (text: string): string => text.replace(/\s+/gu, " ").trim();

/** The attributes that hold no word (pinned): every other attribute's value is a word, a census key. */
const NON_WORD = ["class", "role", "id", "type", "name", "href", "rel", "autocomplete", "spellcheck", "disabled", "style"];

/** The runs outside an isolate that hold no word (pinned): the page's separators, each counted in the
 * sections' multisets as the mirror places it. Any other run is a census key: a digit or a sign
 * outside a number isolate is neither. */
const SEPARATORS = new Set([",", ";", ":", "(", ")", ").", ".", "@", "):", "·"]);

/** The page's own text runs of an element: every text node outside a data or number isolate (letters
 * or not), the segments' server words aside. */
function runsOf(element: Element): string[] {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const found: string[] = [];
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    if (node.parentElement?.closest("bdi.data, bdi.number") === null) {
      found.push(normal(node.nodeValue ?? ""));
    }
  }
  return found.filter((text) => text !== "" && text !== SEGMENT);
}

/** The word-holding attribute values of an element and its descendants (the element's own aside
 * when `own` is false). */
function attributesOf(element: Element, own: boolean): string[] {
  const elements = [...(own ? [element] : []), ...element.querySelectorAll("*")];
  return elements.flatMap((one) => [...one.attributes].filter((attribute) => !NON_WORD.includes(attribute.name)).map((attribute) => normal(attribute.value)));
}

/** A data isolate as the multisets hold it: its class and its text as shown. */
const isolateWord = (className: string, text: string): string => `isolate ${className}: ${text}`;

/** The data isolates of an element, each by its class and its text. */
const isolatesOf = (element: Element): string[] => [...element.querySelectorAll("bdi.data")].map((one) => isolateWord(one.className, one.textContent));

/** The words of an element: its runs (separators counted), its word-holding attribute values and its
 * data isolates. */
function wordsIn(element: Element, own: boolean): placed.Words {
  const words: placed.Words = new Map();
  for (const word of [...runsOf(element), ...attributesOf(element, own), ...isolatesOf(element)]) {
    words.set(word, (words.get(word) ?? 0) + 1);
  }
  return words;
}

/** The mirror's multiset with each isolate entry in the form the page shows it: `Text`'s cut form
 * with its mark where cut, `Ref`'s whole form with the server's mark where a segment carries one, and
 * the class of each (`data`, or `data token`). */
function shownForms(words: placed.Words): placed.Words {
  const out: placed.Words = new Map();
  for (const [word, count] of words) {
    const entry = placed.isolateOf(word);
    let key = word;
    if (entry !== null) {
      const shown = visibleForm(entry.text, UNSAFE, ENGINE, entry.as === "text" ? CUT : null);
      const joined = shown.runs.map((run) => run.text).join("");
      key = isolateWord(entry.as === "token" ? "data token" : "data", shown.truncated || entry.truncated ? `${joined}\u2026` : joined);
    }
    out.set(key, (out.get(key) ?? 0) + count);
  }
  return out;
}

/** The differences of two multisets of words. */
function differences(where: string, got: placed.Words, want: placed.Words): string[] {
  return [...new Set([...got.keys(), ...want.keys()])]
    .sort()
    .flatMap((word) => ((got.get(word) ?? 0) === (want.get(word) ?? 0) ? [] : [`${where}: "${word}" stands ${String(got.get(word) ?? 0)} times, the raw body says ${String(want.get(word) ?? 0)}`]));
}

/** What the runtime census finds wrong with one rendered body (its raw JSON beside it). */
function runtimeProblems(host: Element, raw: Json): string[] {
  const problems: string[] = [];
  const keys = new Set([...Object.keys(CENSUS), ...Object.keys(RUNTIME_ONLY)]);
  for (const text of runsOf(host)) {
    if (!keys.has(text) && !SEPARATORS.has(text)) {
      problems.push(`a run no census key or separator holds: ${text}`);
    }
  }
  for (const value of attributesOf(host, false)) {
    if (!keys.has(value)) {
      problems.push(`an attribute no census key holds: ${value}`);
    }
  }
  const results = list(raw["results"]).map(object);
  const sections = [...host.querySelectorAll('section[aria-label="result"]')];
  if (sections.length !== results.length) {
    problems.push(`${String(sections.length)} result sections for ${String(results.length)} results`);
  }
  // The answer's words: the result sections' labels, the parameters' and every result's, and no other.
  const parameters = host.querySelector('section[aria-label="parameters"]');
  const want: placed.Words = new Map([["result", results.length]]);
  const merge = (words: placed.Words): void => {
    for (const [word, count] of words) {
      want.set(word, (want.get(word) ?? 0) + count);
    }
  };
  const ofParameters = shownForms(placed.wordsOfParameters(raw));
  merge(ofParameters);
  const none: placed.Words = new Map();
  problems.push(...differences("the parameters", parameters === null ? none : wordsIn(parameters, true), ofParameters));
  results.forEach((result, at) => {
    const expected = shownForms(placed.wordsOfResult(result));
    merge(expected);
    for (const word of expected.keys()) {
      if (!keys.has(word) && !SEPARATORS.has(word) && !word.startsWith("isolate ")) {
        problems.push(`the mirror's word is no census key: ${word}`);
      }
    }
    const section = sections[at];
    if (section === undefined) {
      return;
    }
    problems.push(...differences(`result ${String(at)}`, wordsIn(section, false), expected));
    // Booleans, per instance: each bin's includes_* as sent; the reference mark on its own position;
    // the other_values mark on its own row.
    const bins = [...section.querySelectorAll(".bin")].map((bin) => /includes_low (true|false), includes_high (true|false)/u.exec(normal(bin.textContent))?.slice(1).join(" ") ?? "unread");
    const rawBins = placed.binsShown(result).map((bin) => `${String(bin["includes_low"])} ${String(bin["includes_high"])}`);
    if (JSON.stringify(bins) !== JSON.stringify(rawBins)) {
      problems.push(`result ${String(at)}: bins' includes_* ${JSON.stringify(bins)}, sent ${JSON.stringify(rawBins)}`);
    }
    const marked = [...section.querySelectorAll('section[aria-label="position"]')].map((position) => runsOf(position).includes("the reference position"));
    const references = list(result["cohorts"]).map((cohort) => object(cohort)["reference"] === true);
    if (JSON.stringify(marked.slice(0, references.length)) !== JSON.stringify(references) || marked.slice(references.length).some(Boolean)) {
      problems.push(`result ${String(at)}: the reference marks ${JSON.stringify(marked)}, sent ${JSON.stringify(references)}`);
    }
    const rows = [...section.querySelectorAll(".category")].map((row) => row.querySelector(".member") !== null);
    const rawRows = placed.rowsOf(result).filter((row) => !placed.unnamed(row)).map((row) => row["other_values"] === true);
    if (JSON.stringify(rows) !== JSON.stringify(rawRows)) {
      problems.push(`result ${String(at)}: the other_values marks ${JSON.stringify(rows)}, sent ${JSON.stringify(rawRows)}`);
    }
  });
  problems.push(...differences("the answer", wordsIn(host, false), want));
  return problems;
}

/** The built answer with one per-position list one entry short each (rendered, not swept: their
 * leaves are the built answer's): the missing-entry words. */
const SHORT = (): Json[] =>
  [
    (result: Json) => list(result["labels"]),
    (result: Json) => list(result["cohorts"]),
    (result: Json) => list(object(result["readback"])["cohorts"]),
    (result: Json) => list(result["population"]),
    (result: Json) => list(result["analysed"]),
    (result: Json) => list(object(result["values"])["positions"]),
  ].map((of) =>
    changed(LIVE.a, (result) => {
      of(result).pop();
    }),
  );

/** The built answer with an empty bins list, which the schema refuses (a histogram has a bin) and
 * the walk does not (D426: no bound read): what the page says of it is held all the same (round 2's
 * x1). An empty analysed excluded map (q14, n10) the walk itself refuses (its eight reasons are
 * required), so no answer the page reads holds one: those two mutants are equivalent. */
const EMPTY = (): Json[] => [
  changed(LIVE.a, (result) => {
    object(object(list(object(list(object(result["values"])["positions"])[0])["columns"])[0])["histogram"])["bins"] = [];
  }),
];

/** The built answer with its first column's label, its first position's label and a category value
 * each longer than `Text`'s cut, so that a cut text and a whole one are told apart (round 3's x19). */
const LONG = (): Json[] => [
  changed(LIVE.a, (result) => {
    const long = (mark: string): string => `${mark}${"x".repeat(2100)}`;
    object(list(object(object(object(object(result["derivation"])["document"])["view"])["params"])["columns"])[0])["column"] = long("column ");
    object(list(result["labels"])[0])["data"] = long("label ");
  }),
];

/** Every body the sweeps read, rendered once with its segments' words a sentinel (`renderToString`,
 * whose markers keep each of React's text nodes apart, as the DOM has them). */
let rendered: Promise<{ readonly raw: Json; readonly host: Element }[]> | undefined;
function renderedBodies(): NonNullable<typeof rendered> {
  rendered ??= (async () => {
    const out: { readonly raw: Json; readonly host: Element }[] = [];
    for (const raw of [...(await sweptBodies()).map((one) => one.raw), ...SHORT(), ...EMPTY(), ...LONG()]) {
      const body = segmented(raw) as Json;
      const host = document.createElement("div");
      host.innerHTML = await stringOf(JSON.stringify(body));
      out.push({ raw: body, host });
    }
    return out;
  })();
  return rendered;
}

/** The census keys no body shows, each a word of another screen or of a branch the bodies do not
 * reach (pinned: a reviewed change). */
const UNMET = [
  "Count",
  "The cohort's size as a share of the unit table is not shown yet.",
  "This column's label is not listed.",
  "Try again after",
  "a degenerate table",
  "a zero denominator",
  "and",
  "beyond the follow-up",
  "confounded with the dataset",
  "more",
  "no events",
  "not reached",
  "not shown: not a number this page reads",
  "seconds (the server's Retry-After).",
  "separation",
  "the cohorts overlap",
  "the fit did not converge",
  "zero variance",
];

describe("the runtime census (B7.9, D435's rounds 1 to 3: every run, separator, attribute and isolate)", () => {
  it("over every body the sweeps read, every run outside an isolate is a census key or a pinned separator, every attribute but the pinned non-word ones a key, each section's words, separators and data isolates (a leaf in its section, by class and form) equal as a multiset to the raw body's mirror, and each boolean the raw value, per instance", { timeout: 900_000 }, async () => {
    const all = await renderedBodies();
    expect(all.length).toBeGreaterThan(40);
    const problems = [...new Set(all.flatMap(({ raw, host }) => runtimeProblems(host, raw)))];
    expect(problems.join("\n")).toBe("");
    // What the bodies reached: every census key shown somewhere (but the words of other screens,
    // `format.tsx`'s and `ReleaseRef`'s disagreement), both values of each boolean.
    const results = all.flatMap(({ raw }) => list(raw["results"]).map(object));
    const met = new Set(all.flatMap(({ host }) => [...wordsIn(host, false).keys()]));
    expect(Object.keys(CENSUS).filter((key) => !met.has(key)).sort()).toEqual(UNMET);
    const flags = new Set(results.flatMap((result) => placed.binsShown(result).flatMap((bin) => [`low ${String(bin["includes_low"])}`, `high ${String(bin["includes_high"])}`])));
    expect([...flags].sort()).toEqual(["high false", "high true", "low false", "low true"]);
  });

  it("controls: a word no census key holds, a shown attribute of any name, a page-made number, a data isolate of no leaf in its section, a misplaced or missing word and a wrong boolean are each found", async () => {
    const raw = segmented(liveBody(LIVE.a)) as Json;
    const host = document.createElement("div");
    host.innerHTML = await stringOf(JSON.stringify(raw));
    expect(runtimeProblems(host, raw)).toEqual([]);
    const changed = (change: (element: Element) => void): string[] => {
      const copy = host.cloneNode(true) as Element;
      change(copy);
      return runtimeProblems(copy, raw);
    };
    expect(changed((copy) => copy.querySelector("p.note")?.append("These values were merged."))).toContain("a run no census key or separator holds: These values were merged.");
    // n1b, n6, n9, n12: an aria attribute of any name.
    for (const name of ["aria-description", "aria-roledescription", "aria-valuetext", "title"]) {
      expect(changed((copy) => copy.querySelector('[aria-label="values"]')?.setAttribute(name, "merged"))).toContain("an attribute no census key holds: merged");
    }
    // n14, n16: a page-made number outside a number isolate.
    expect(changed((copy) => copy.querySelector(".proportion")?.append(" ×100"))).toContain("a run no census key or separator holds: ×100");
    expect(changed((copy) => copy.querySelector(".proportion")?.append("(0)"))).toContain("a run no census key or separator holds: (0)");
    // n5: a data isolate that holds a sentence of the page's.
    expect(
      changed((copy) => {
        const isolate = document.createElement("bdi");
        isolate.className = "data";
        isolate.textContent = "These values were merged by the disclosure setting.";
        copy.querySelector(".proportion")?.append(isolate);
      }),
    ).toContain('result 0: "isolate data: These values were merged by the disclosure setting." stands 1 times, the raw body says 0');
    // q1, q2: "none" beside a leaf.
    expect(
      changed((copy) => {
        const leaves = [...copy.querySelectorAll('[aria-label="provenance"] p')].find((one) => one.textContent.startsWith("Leaves:"));
        leaves?.append("none");
      }),
    ).toContain('result 0: "none" stands 3 times, the raw body says 2');
    expect(
      changed((copy) => {
        const span = document.createElement("span");
        span.className = "member";
        span.textContent = "other_values";
        copy.querySelector(".category")?.prepend(span);
      }),
    ).toContain('result 0: "other_values" stands 1 times, the raw body says 0');
    expect(
      changed((copy) => {
        copy.querySelector(".excluded")?.remove();
        copy.querySelector('[aria-label="caveats"] .affects')?.remove();
      }).some((one) => one.startsWith('result 0: "affects:" stands')),
    ).toBe(true);
    expect(
      changed((copy) => {
        const bin = copy.querySelectorAll(".bin")[1];
        if (bin !== undefined) {
          bin.innerHTML = bin.innerHTML.replace("includes_high<!-- --> <!-- -->false", "includes_high<!-- --> <!-- -->true");
        }
      }).some((one) => one.startsWith("result 0: bins' includes_*")),
    ).toBe(true);
  });
});

/** Source texts for the numbers oracle: the corpus's hard ones, then texts of their own. */
const HARD = ["1e-07", "-0.0", "1.10", "9007199254740993", "5e-321"];
const textAt = (index: number): string => HARD[index] ?? `${String(index)}.${String(index)}5`;

/** A body with every number leaf rewritten with a source text of its own, and the texts by leaf. */
function renumbered(raw: unknown, leaves: readonly Leaf[]): { readonly json: string; readonly texts: readonly (readonly [Leaf, string])[] } {
  const numbers = leaves.filter((leaf) => leaf.kind === "number");
  let changedBody: unknown = raw;
  numbers.forEach((leaf, index) => {
    changedBody = replaced(changedBody, leaf, `zqnum${String(index)}qz`);
  });
  const json = JSON.stringify(changedBody).replace(/"zqnum(\d+)qz"/gu, (_match, index: string) => textAt(Number(index)));
  return { json, texts: numbers.map((leaf, index) => [leaf, textAt(index)] as const) };
}

/** A multiset, sorted. */
const sorted = (values: readonly string[]): string[] => [...values].sort();

describe("the numbers oracle (B7.3)", () => {
  it("the page shows exactly the body's numbers outside DEFERRED, each by its source text, and makes none; its null words are the nulls it shows (every body the runtime census renders: the sweeps' bodies, doubled, and the short, empty and long ones)", { timeout: 900_000 }, async () => {
    let checked = 0;
    for (const { raw } of await renderedBodies()) {
      const walked = await ran(JSON.stringify(raw));
      const leaves = leavesOf(raw, walked);
      const { json, texts } = renumbered(raw, leaves);
      const host = document.createElement("div");
      host.innerHTML = await markupOf(json);
      const shown = [...host.querySelectorAll("bdi.number")].map((one) => one.textContent);
      const expected = texts.filter(([leaf]) => deferredAt(leaf.path) === undefined).map(([, text]) => text);
      expect(sorted(shown)).toEqual(sorted(expected));
      // The null words: every null of a shown member is said, and nothing else is said as null but
      // what the page shows as suppressed whatever came (lift_differs under k).
      // A null setting is said by the no-setting words (R25), not as a null figure.
      const nulls = leaves.filter(
        (leaf) => leaf.kind === "null" && leaf.path !== "results/[]/derivation/disclosure/min_cell_count@null" && deferredAt(leaf.path.replace(/@null$/u, "@number")) === undefined && !leaf.path.startsWith("results/[]/values(unread)"),
      );
      const words = host.querySelectorAll(".suppressed, .not-shown, .not-estimable, .open, .absent").length;
      // lift_differs under k is said suppressed whatever came, a null or a number (R20).
      const liftUnderK = leaves.filter((leaf) => /^results\/\[\]\/population\(k\)\/\[\]\/lift_differs@(number|null)$/u.test(leaf.path)).length;
      expect(words, JSON.stringify(nulls.map((leaf) => leaf.path))).toBe(nulls.length + liftUnderK);
      checked += 1;
    }
    expect(checked).toBeGreaterThan(80);
  });
});

describe("alignment and hostile texts (B7.4)", () => {
  it("each position's texts stay in its own section (labels, readback tokens and category values marked by position)", async () => {
    for (const index of [LIVE.a, LIVE.d]) {
      const raw = liveBody(index);
      const result = object(list(raw["results"])[0]);
      list(result["labels"]).forEach((label, at) => {
        object(label)["data"] = `zqpos${String(at)}qz`;
      });
      list(object(result["readback"])["cohorts"]).forEach((segments, at) => {
        list(segments).forEach((segment) => {
          if (typeof object(segment)["data"] === "string") {
            object(segment)["data"] = `zqpos${String(at)}qz`;
          }
        });
      });
      list(object(result["values"])["positions"]).forEach((position, at) => {
        for (const column of list(object(position)["columns"])) {
          for (const row of list(object(column)["categories"])) {
            object(row)["values"] = [{ data: `zqpos${String(at)}qz` }];
          }
        }
      });
      const host = document.createElement("div");
      host.innerHTML = await markupOf(JSON.stringify(raw));
      const sections = [...host.querySelectorAll('section[aria-label="position"]')];
      expect(sections).toHaveLength(2);
      sections.forEach((section, at) => {
        const marks = new Set((section.textContent.match(/zqpos\d+qz/gu) ?? []).map(String));
        expect([...marks]).toEqual([`zqpos${String(at)}qz`]);
      });
    }
  });

  const OWN = ["DIV", "P", "SECTION", "H3"];
  const CLASSES = ["", "number", "release", "label", "label draft", "disagree", "suppressed", "none", "largest", "affects", "open", "absent", "member", "proportion", "excluded", "denominator-text", "note", "reference", "not-shown", "not-estimable"];

  it("a hostile text at every text position the page shows is shown as text alone (the hostile fixture's answer and the built answer)", { timeout: 300_000 }, async () => {
    let positions = 0;
    for (const index of [LIVE.g, LIVE.a]) {
      const raw = liveBody(index);
      const found = await sweep(raw);
      const walked = await ran(JSON.stringify(raw));
      const groups = new Map<string, Leaf[]>();
      for (const leaf of leavesOf(raw, walked)) {
        if (leaf.kind !== "number" && leaf.kind !== "null" && found.shown.has(leaf.path) && !found.missed.has(leaf.path)) {
          groups.set(leaf.path, [...(groups.get(leaf.path) ?? []), leaf]);
        }
      }
      for (const group of groups.values()) {
        for (const row of HOSTILE) {
          let body: unknown = raw;
          for (const leaf of [...group].reverse()) {
            body = replaced(body, leaf, row.text);
          }
          const host = document.createElement("div");
          host.innerHTML = await markupOf(JSON.stringify(body));
          expect(problemsOf(host, row, OWN, CLASSES), `${group[0]?.path ?? ""} ${row.name}`).toEqual([]);
        }
        positions += 1;
      }
    }
    expect(positions).toBeGreaterThan(30);
  });
});
