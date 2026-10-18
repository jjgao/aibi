/**
 * The census of the result screen's words (D435's §4b, B7.9), as data: every word the census's
 * modules hold (`census-words.ts`) is a key here, and each is one of three things.
 *
 * - **live**: it interprets a server value, and a cell runs over every live result where the value
 *   occurs (`occurs`), asserting the word's claim there (`holds`: the problems, none for a pass); the
 *   value must occur at least once.
 * - **neutral**: it claims nothing of the server: a member's own name, the server's enum member
 *   translated word for word (D430), a description of a list, the page's label for what follows.
 * - **own**: what the page does, or its fail-closed words.
 *
 * Every word is also **placed**: the runtime census (`result-sweep.test.tsx`) holds the words of each
 * result's section, and of the parameters', equal as a multiset to the words the test's own mirror of
 * the page reads from the raw body (`placed.ts`' `wordsOfResult`, `wordsOfParameters`), so that no
 * word stands where its condition does not hold (D435's round 2).
 *
 * Text from data is never a key: the census reads the page's own words alone. `EXEMPT` lists the
 * literals of the census's modules that are no page word (class names, pointers, thrown messages),
 * each with its reason; `RUNTIME_ONLY` the words the run view shows that no census module holds.
 */
import { NO_SETTING } from "../../src/layout/Disclosure";
import { type Json, list, type LiveResult, object, underKCounts } from "./result-support";
import { deferredColumn } from "./sweep";
import * as placed from "./placed";
import { ORCHARD } from "./typed-support";

/** A live row's cell: where its value occurs, and what is wrong with the page there. */
export interface Live {
  readonly held: "live";
  readonly claim: string;
  readonly occurs: (result: LiveResult) => boolean;
  readonly holds: (result: LiveResult, shown: Element) => string[];
}

export type Row = Live | { readonly held: "neutral" | "own"; readonly why: string };

const neutral = (why: string): Row => ({ held: "neutral", why });
const own = (why: string): Row => ({ held: "own", why });
const ENUM = neutral("The server's enum member, translated word for word (D430, reviewed in 2b-1b's R14); held by figure.test.tsx on synthetic answers.");
const MEMBER = neutral("A member's own name, the value beside it as sent.");
const LABEL = neutral("The page's label for what follows it (an id, a list, a number as sent).");

/** The not-null-shown figures of a raw result: each reason at a pointer of a shown arm whose value
 * is null, as `[reason, pointer]`. */
function reasonsShown(raw: Json): [string, string][] {
  const found: [string, string][] = [];
  const k = object(object(raw["derivation"])["disclosure"])["min_cell_count"] !== null;
  const proportion = (value: Json): void => {
    for (const [pointer, reason] of Object.entries(object(value["not_estimable"]))) {
      if (value[pointer.slice(1)] === null) {
        found.push([String(reason), pointer]);
      }
    }
  };
  for (const position of list(object(raw["values"])["positions"]).map(object)) {
    for (const column of list(position["columns"]).map(object)) {
      if (column["kind"] === "numbers" && !deferredColumn(column, k)) {
        for (const [pointer, reason] of Object.entries(object(column["not_estimable"]))) {
          if (column[pointer.slice(1)] === null) {
            found.push([String(reason), pointer]);
          }
        }
      }
      if (column["kind"] === "categories") {
        if (column["categories"] === null) {
          found.push([String(object(column["not_estimable"])["/categories"]), "/categories"]);
        }
        for (const row of list(column["categories"]).map(object)) {
          if (list(row["values"]).length > 0 || row["other_values"] === true) {
            proportion(object(row["proportion"]));
          }
        }
      }
    }
  }
  return found;
}

/** The open bins of a raw result's shown histograms: `[first, last, low null, high null]` per bin. */
function openBins(raw: Json): { readonly first: boolean; readonly last: boolean; readonly low: boolean; readonly high: boolean }[] {
  const k = object(object(raw["derivation"])["disclosure"])["min_cell_count"] !== null;
  return list(object(raw["values"])["positions"])
    .flatMap((position) => list(object(position)["columns"]).map(object))
    .filter((column) => column["kind"] === "numbers" && !deferredColumn(column, k))
    .flatMap((column) => {
      const bins = list(object(column["histogram"])["bins"]).map(object);
      return bins.map((bin, index) => ({ first: index === 0, last: index === bins.length - 1, low: bin["low"] === null, high: bin["high"] === null }));
    });
}

const countOf = (shown: Element, selector: string, text: string): number => [...shown.querySelectorAll(selector)].filter((one) => one.textContent === text).length;

/** A reason's words: the page shows `not estimable: <words>` exactly where the server sent it at a shown null figure. */
function reasonRow(reason: string, words: string): Live {
  return {
    held: "live",
    claim: `The server's ${reason}, translated word for word (D430).`,
    occurs: (result) => reasonsShown(result.raw).some(([one]) => one === reason),
    holds: (result, shown) => {
      const want = reasonsShown(result.raw).filter(([one]) => one === reason).length;
      const got = countOf(shown, ".not-estimable", `not estimable: ${words}`);
      return got === want ? [] : [`${String(got)} shown, ${String(want)} sent`];
    },
  };
}

/** The explain answers of the orchard, raw. */
const EXPLAINED = (): Json[] => ORCHARD.filter((answer) => answer.operation === "explain" && answer.status === 200).map((answer) => JSON.parse(answer.body) as Json);

const issuanceOf = (raw: Json): Json => object(raw["issuance"]);

export const CENSUS: Readonly<Record<string, Row>> = {
  // Caveat.tsx
  note: {
    held: "live",
    claim: "`info` is a note.",
    occurs: (result) => list(result.raw["caveats"]).some((caveat) => object(caveat)["severity"] === "info"),
    holds: (result, shown) => {
      const items = [...shown.querySelectorAll('[aria-label="caveats"] [role="listitem"]')];
      return list(result.raw["caveats"]).flatMap((caveat, index) => (object(caveat)["severity"] === "info" && !(items[index]?.textContent ?? "").includes("(note)") ? [`caveat ${String(index)}`] : []));
    },
  },
  warning: {
    held: "live",
    claim: "`warn` is a warning.",
    occurs: (result) => list(result.raw["caveats"]).some((caveat) => object(caveat)["severity"] === "warn"),
    holds: (result, shown) => {
      const items = [...shown.querySelectorAll('[aria-label="caveats"] [role="listitem"]')];
      return list(result.raw["caveats"]).flatMap((caveat, index) => (object(caveat)["severity"] === "warn" && !(items[index]?.textContent ?? "").includes("(warning)") ? [`caveat ${String(index)}`] : []));
    },
  },
  block: ENUM,
  "For inspection, not an answer: a blocking caveat applies to this output (§8.3).": own("The spec's own rule (§8.3, R22), shown only when a block is present; held by caveat.test.tsx."),
  "affects:": LABEL,
  // Disclosure.tsx
  [NO_SETTING]: {
    held: "live",
    claim: "A null min_cell_count is no setting.",
    occurs: (result) => result.k === null,
    holds: (_result, shown) => (shown.querySelector("p.disclosure")?.textContent === NO_SETTING ? [] : ["not the words of no setting"]),
  },
  "Disclosure: minimum cell count k =": LABEL,
  // B10's cell over every answer under a floor is disclosure.test.tsx's; this one is the results'.
  ": counts of units from 1 to k − 1, and counts that would reveal them, are suppressed (§8.4).": {
    held: "live",
    claim: "Under a setting k, no count shown is from 1 to k − 1 (B10).",
    occurs: (result) => result.k !== null,
    holds: (result) => underKCounts(result.raw).flatMap((count) => (count === null || count === 0 || (result.k !== null && count >= result.k) ? [] : [`a count of ${String(count)} under k = ${String(result.k)}`])),
  },
  // Figure.tsx
  "no units": reasonRow("no_units", "no units"),
  "no events": ENUM,
  "a zero denominator": ENUM,
  "zero variance": ENUM,
  "not reached": ENUM,
  "beyond the follow-up": ENUM,
  separation: ENUM,
  "the fit did not converge": ENUM,
  "a degenerate table": ENUM,
  "the cohorts overlap": ENUM,
  "confounded with the dataset": ENUM,
  suppressed: {
    held: "live",
    claim: "A count the disclosure pass withheld, a member the population lists as suppressed, lift_differs under k, or a figure whose reason is `suppressed`, is said suppressed: exactly those (R14, R20).",
    occurs: (result) => placed.suppressedWords(result.raw) > 0,
    holds: (result, shown) => {
      const want = placed.suppressedWords(result.raw);
      const got = shown.querySelectorAll(".suppressed").length;
      return got === want ? [] : [`${String(got)} suppressed words for ${String(want)}`];
    },
  },
  "not shown: no reason was given": own("Fail-closed words for a null with no reason (R14)."),
  "not estimable:": MEMBER,
  // Population.tsx
  "The cohort's size as a share of the unit table is not shown yet.": own("M5.3b's words (R40b)."),
  none: neutral("Describes a list or a map with no entry."),
  n_true: MEMBER,
  "; n_false": MEMBER,
  "; n_unknown": MEMBER,
  "(largest reason": own("The page's own reading of the map (`largestOf`, R18)."),
  "n_unknown by reason:": MEMBER,
  "n_unknown by leaf:": MEMBER,
  lift_differs: MEMBER,
  Count: LABEL,
  "Releases:": LABEL,
  // Proportion.tsx
  "none given": neutral("A member sent as null, said as such."),
  "This proportion has an interval, not shown on this page yet (R16).": neutral("Says that the proportion's `ci` member is present (placed: exactly where it is), and that the page does not show it yet."),
  "; excluded": MEMBER,
  numerator: MEMBER,
  "; denominator": MEMBER,
  "; estimate": MEMBER,
  "; denominator definition: counts": MEMBER,
  ", position": MEMBER,
  ", predicate": MEMBER,
  // ReleaseRef.tsx
  "(its label and its status disagree: shown as a draft)": own("Fail-closed words (R27)."),
  "@draft": ENUM,
  "· manifest": LABEL,
  // Analysed.tsx
  "Per variable, in the view's column order (labels not shown: the page and the server disagree about them):": own("Fail-closed words (D434's `variables`)."),
  n: MEMBER,
  "; excluded_units": MEMBER,
  "; excluded by reason": MEMBER,
  "Analysed:": LABEL,
  // Charts.tsx
  "This result has no chart.": own("What the page draws: from the list's emptiness alone (R31a)."),
  "This result carries charts; none is drawn on this page (charts come with M5.C). A chart not drawn is not a chart with no data.": own("What the page draws (R31a)."),
  // Figures.tsx
  "This result gives no values for the view as a whole.": neutral("By construction from the generated table (D434's `view: \"none\"`); live (a)'s `view: {}`."),
  "the reference position": neutral("The member's own name (`reference`)."),
  "This position's label is not listed.": own("Fail-closed words for a missing entry."),
  "This position's cohort is not listed.": own("Fail-closed words for a missing entry."),
  "This position's readback is not listed.": own("Fail-closed words for a missing entry."),
  "This position's population is not listed.": own("Fail-closed words for a missing entry."),
  "This position's analysed counts are not listed.": own("Fail-closed words for a missing entry."),
  "This position's values are not listed.": own("Fail-closed words for a missing entry."),
  position: MEMBER,
  ", cohort": MEMBER,
  Analysis: LABEL,
  version: MEMBER,
  // Provenance.tsx
  "computed for this request.": {
    held: "live",
    claim: "A result with cache_hit false was computed for its request: its values come from its own issuance. This cell checks the data; the page's side (the sentence where cache_hit is false) is provenance-panel.test.tsx's R28a and the placed count.",
    occurs: (result) => issuanceOf(result.raw)["cache_hit"] === false,
    holds: (result) => (issuanceOf(result.raw)["values_from"] === issuanceOf(result.raw)["id"] ? [] : ["values_from is another issuance"]),
  },
  "served from the result cache: its values come from issuance": {
    held: "live",
    claim: "A result with cache_hit true serves another issuance's values: values_from names an issuance of the same derivation. This cell checks the data; the page's side is provenance-panel.test.tsx's R28a and the placed count.",
    occurs: (result) => issuanceOf(result.raw)["cache_hit"] === true,
    holds: (result) => {
      const from = issuanceOf(result.raw)["values_from"];
      const id = object(result.raw["derivation"])["id"];
      const issued = ORCHARD.flatMap((answer) => (answer.operation === "run_analysis" && answer.status === 200 ? list(object(JSON.parse(answer.body))["results"]).map(object) : []));
      const source = issued.find((one) => issuanceOf(one)["id"] === from);
      return from !== issuanceOf(result.raw)["id"] && source !== undefined && object(source["derivation"])["id"] === id && issuanceOf(source)["cache_hit"] === false ? [] : ["values_from names no computed issuance of this derivation"];
    },
  },
  "The document as written is not shown on this page yet (R26b).": own("What the page does not show yet."),
  "The parameters as written are not shown on this page yet (R28b).": own("What the page does not show yet."),
  "not shown here yet (R28b).": own("What the page does not show yet."),
  "The derivation id names this result; an agent reads its derivation with explain.": {
    held: "live",
    claim: "explain of a run's derivation id gives that result's derivation (status issued, kind result).",
    occurs: (result) => EXPLAINED().some((answer) => object(answer["derivation"])["id"] === object(result.raw["derivation"])["id"]),
    holds: (result) =>
      EXPLAINED()
        .filter((answer) => object(answer["derivation"])["id"] === object(result.raw["derivation"])["id"])
        .flatMap((answer) => (answer["status"] === "issued" && object(answer["derivation"])["kind"] === "result" ? [] : [`explain ${String(answer["id"])}`])),
  },
  "This page keeps the result while it stays open: a reload clears it (the result is not fetched again). If the form changes, the result stays under the words that say so, until the next run.": own(
    "What the page does (the builder's state is in memory; B7.7's cells).",
  ),
  Derivation: LABEL,
  ": analysis": MEMBER,
  "Packs:": LABEL,
  results_version: MEMBER,
  "Semantics version": MEMBER,
  "; engine": MEMBER,
  Digest: LABEL,
  Issuance: LABEL,
  "Leaves:": LABEL,
  "Parameters used:": LABEL,
  "Parameters not used:": LABEL,
  // Values.tsx
  "This column's numbers are not shown on this page yet (R34b).": own("The page's own condition (a setting, or a bin index); the sweep holds that no member of such an arm is shown."),
  "This column lists no category row for this position.": neutral("Describes the list."),
  "A row that names no category: not shown (the page and the server disagree).": own("Fail-closed words (m14)."),
  "Values of this kind are not shown on this page yet (R29):": own("What the page does not show yet; the kind follows as a data token."),
  "no lower edge": {
    held: "live",
    claim: "A null low is the open bin below the first edge (HistogramBin's schema text).",
    occurs: (result) => openBins(result.raw).some((bin) => bin.low),
    holds: (result, shown) => {
      const bins = openBins(result.raw);
      const problems = bins.filter((bin) => bin.low && !bin.first).map(() => "a null low not on the first bin");
      const want = bins.filter((bin) => bin.low).length;
      return countOf(shown, ".open", "no lower edge") === want ? problems : [...problems, "the words not on each null low"];
    },
  },
  "no upper edge": {
    held: "live",
    claim: "A null high is the open bin above the last edge (HistogramBin's schema text).",
    occurs: (result) => openBins(result.raw).some((bin) => bin.high),
    holds: (result, shown) => {
      const bins = openBins(result.raw);
      const problems = bins.filter((bin) => bin.high && !bin.last).map(() => "a null high not on the last bin");
      const want = bins.filter((bin) => bin.high).length;
      return countOf(shown, ".open", "no upper edge") === want ? problems : [...problems, "the words not on each null high"];
    },
  },
  "This page does not read the values of this analysis and version yet.": own("The page's own words (`unread/analysis`)."),
  "The values are not shown: the page and the server disagree about their shape.": own("The page's own words (`unread/shape`)."),
  mean: MEMBER,
  sd: MEMBER,
  median: MEMBER,
  q1: MEMBER,
  q3: MEMBER,
  min: MEMBER,
  max: MEMBER,
  histogram: MEMBER,
  low: MEMBER,
  ", high": MEMBER,
  ", includes_low": MEMBER,
  ", includes_high": MEMBER,
  true: neutral("A boolean member as sent."),
  false: neutral("A boolean member as sent."),
  ", count": MEMBER,
  ": edges from": neutral("Precedes the server's word, shown as a data token, not explained."),
  other_values: neutral("The member's name (M1.1: no gloss)."),
  categories: MEMBER,
  "This column's label is not listed.": own("Fail-closed words for a missing label (unreachable while the seam checks the counts)."),
  // The attributes a person is shown (aria-label, title): the page's own labels.
  caveats: LABEL,
  "for inspection, not an answer": own("The banner's label (R22)."),
  population: LABEL,
  analysed: LABEL,
  view: LABEL,
  provenance: LABEL,
  parameters: LABEL,
  values: LABEL,
  column: LABEL,
  cut: own("The title of the page's own cut mark (R01)."),
  // format.tsx (Num and its siblings): the page's own words.
  "not shown: not a number this page reads": own("Fail-closed words for a number whose text the page does not read (D430)."),
  and: own("List's \"and N more\" (D312's lists); not rendered by the result screen."),
  more: own("List's \"and N more\" (D312's lists); not rendered by the result screen."),
  "Try again after": own("RetryAfter's words; not rendered by the result screen."),
  "seconds (the server's Retry-After).": own("RetryAfter's words; not rendered by the result screen."),
};

/** The literals of the census's modules that are no page word, each with its reason (reviewed). */
export const EXEMPT: readonly (readonly [module: string, word: string, reason: string])[] = [
  ["src/api/text.tsx", "A vocabulary's member is its own (D426).", "a thrown message, never shown (a route's error element shows fixed words)"],
  ["src/api/text.tsx", "A choice is made by optionsOf alone (D432).", "a thrown message, never shown"],
  ["src/api/text.tsx", "table", "a grammar's name"],
  ["src/api/text.tsx", "column", "a grammar's name"],
  ["src/api/text.tsx", "cut", "a visible form's name"],
  ["src/layout/Caveat.tsx", "caveat info", "a class name (SEVERITY_CLASS)"],
  ["src/layout/Caveat.tsx", "caveat warn", "a class name (SEVERITY_CLASS)"],
  ["src/layout/Caveat.tsx", "caveat block", "a class name (SEVERITY_CLASS)"],
  ["src/layout/Text.tsx", "data token", "a class name"],
  ["src/layout/Text.tsx", "data", "a class name"],
  ["src/layout/key.ts", "A key is an index: a whole number from 0 (D428).", "a thrown message, never shown"],
  ["src/layout/Caveat.tsx", "Severity", "a vocabulary's name, kindOf's argument"],
  ["src/layout/Figure.tsx", "NotEstimableReason", "a vocabulary's name, kindOf's argument"],
  ["src/layout/Population.tsx", "Population.suppressed[]", "a vocabulary's name, kindOf's argument"],
  ["src/layout/ReleaseRef.tsx", "ReleaseRef.status", "a vocabulary's name, kindOf's argument"],
  ["src/layout/ReleaseRef.tsx", "ReleaseRef.label", "a vocabulary's name, kindOf's argument"],
  ["src/layout/Segments.tsx", "whole", "a visible form's name (FORM's value)"],
  ["src/layout/Text.tsx", "whole", "a visible form's name (FORM's value)"],
  ["src/layout/Population.tsx", "/n_true", "a pointer prop (Member's pointer)"],
  ["src/layout/Population.tsx", "/n_false", "a pointer prop (Member's pointer)"],
  ["src/layout/Population.tsx", "/n_unknown", "a pointer prop (Member's pointer)"],
  ["src/layout/Population.tsx", "/unknown_by_reason", "a pointer prop (Unknowns' pointer)"],
  ["src/layout/Population.tsx", "/unknown_by_leaf", "a pointer prop (Unknowns' pointer)"],
  ["src/layout/Population.tsx", "/lift_differs", "a pointer prop (Member's pointer)"],
  ["src/layout/Proportion.tsx", "/excluded", "a pointer prop (Figure's at)"],
  ["src/layout/Proportion.tsx", "/numerator", "a pointer prop (Figure's at)"],
  ["src/layout/Proportion.tsx", "/denominator", "a pointer prop (Figure's at)"],
  ["src/layout/Proportion.tsx", "/estimate", "a pointer prop (Figure's at)"],
  ["src/result/Values.tsx", "/histogram", "a pointer prop (Figure's at)"],
  ["src/result/Values.tsx", "/categories", "a pointer prop (Figure's at)"],
  ["src/result/Values.tsx", "/n", "a pointer of NUMBERED"],
  ["src/result/Values.tsx", "/mean", "a pointer of NUMBERED"],
  ["src/result/Values.tsx", "/sd", "a pointer of NUMBERED"],
  ["src/result/Values.tsx", "/median", "a pointer of NUMBERED"],
  ["src/result/Values.tsx", "/q1", "a pointer of NUMBERED"],
  ["src/result/Values.tsx", "/q3", "a pointer of NUMBERED"],
  ["src/result/Values.tsx", "/min", "a pointer of NUMBERED"],
  ["src/result/Values.tsx", "/max", "a pointer of NUMBERED"],
];

/** The words the run view shows that no census module holds, each with its reason. */
export const RUNTIME_ONLY: Readonly<Record<string, string>> = {
  result: "The run view's section label (Builder.tsx's RunOf): a label.",
};
