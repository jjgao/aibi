/**
 * What the builder's validation and count views show of a whole answer (D433's not-shown list, B6.2;
 * T10), by the coverage sweep (`coverage.tsx`): every text and number leaf of each walked answer is
 * replaced in turn by a sentinel, and the set of paths whose sentinel the view shows is held to all
 * the answer's paths minus the view's exception list, exactly. Each exception carries its reason;
 * an exception that names no path of the answers it applies to fails, as does a member the view
 * starts or stops showing. The answers are the server's own (the builder's eight), a validation
 * with a `translation` written from one of them, and the schema's synthetic answers of each view's
 * component (every `PLANS` arm, whole and minimal: the members, `null`s and optional members the
 * server's answers do not hold). The harness has controls of its own. The run view is the result
 * screen (D435), whose sweep goes through the real seam on raw bodies (`result-sweep.test.tsx`).
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Answered } from "../../src/api/typed";
import { CountsOf, ValidationOf, WORDS } from "../../src/builder/Builder";
import { walked } from "./answers";
import { ANSWERS } from "./builder-support";
import { coverage, coverageProblems, type Exception, type Swept, under } from "./coverage";
import { PLANS, REF, synthetic, UNMADE } from "./typed-support";

type Validated = Answered<"validate_document">;
type Counts = Answered<"count_cohort">;

const validationOf = (body: string): Validated => walked("DocumentValidation", body) as Validated;
const countsOf = (body: string): Counts => walked("CohortCounts", body) as Counts;

/** The schema's synthetic bodies of a component, every plan, whole and minimal. */
function synthetics(component: string): string[] {
  const bodies: string[] = [];
  for (const plan of PLANS) {
    for (const minimal of [false, true]) {
      const body = synthetic(REF(component), 0, minimal, plan);
      if (body === UNMADE) {
        throw new Error(`no synthetic ${component}`);
      }
      bodies.push(JSON.stringify(body));
    }
  }
  return bodies;
}

/** The valid validation with a `translation` (an answer this form cannot cause until M5.3-rest). */
function translated(): string {
  const body = JSON.parse(ANSWERS.validated.body) as Record<string, unknown>;
  body["translation"] = { format: "zz.format", document: { unit: "trees", n: 7 }, notes: [{ pointer: "/cohorts", translated: true, message: [{ text: "a note " }, { data: "x" }] }] };
  return JSON.stringify(body);
}

/** The string view's invalid validation, its refusal carrying the server's two counts (one under a
 * floor, one not): a refusal's counts (R71) with their suppressed pointers and their caveats. */
function withCounts(): string {
  const body = JSON.parse(ANSWERS.stringView.body) as { refusals: Record<string, unknown>[] };
  const counts = [ANSWERS.countedUnderK.body, ANSWERS.counted.body].map((one) => (JSON.parse(one) as { counts: { count: unknown }[] }).counts[0]?.count);
  const first = body.refusals[0];
  if (first !== undefined) {
    first["counts"] = counts;
  }
  return JSON.stringify(body);
}

/** Whether every count of a list is under an effective disclosure setting (and there is one). */
const allUnderK = (counts: readonly { readonly disclosure: { readonly min_cell_count: unknown } }[]): boolean => counts.length > 0 && counts.every((one) => one.disclosure.min_cell_count !== null);

/** What a cohort count shows not (`Counted`, the count view's and a refusal's alike), under `at`. */
const countNotShown = (at: string): string[] => [
  // The size (R40b, with R12: M5.3-rest), the digest and the issuance (R26: M5.4-min).
  `${at}/size`,
  `${at}/digest`,
  `${at}/issuance`,
  // The suppressed pointers: read, not shown (each member says suppressed).
  `${at}/population/suppressed`,
  // Words, never the text: a caveat's severity, a release's status, and a release's text label (`@draft`).
  `${at}/caveats/[]/severity`,
  `${at}/releases/[]/status`,
  `${at}/releases/[]/label@text`,
];

/** Not shown by the validation view, each with its reason (D433). */
const VALIDATION_NOT_SHOWN: readonly Exception<Validated>[] = [
  // The canonical form, the computation, what the server resolved and the packs' versions: the server's and explain's.
  "cohorts/[]/form",
  "cohorts/[]/computation_id",
  "cohorts/[]/leaves",
  "cohorts/[]/unit",
  "cohorts/[]/packs",
  "views/[]/form",
  "views/[]/computation_id",
  "views/[]/position",
  // The parameters the server resolved from the document: the form shows those it sent (the view's columns) and the view's readback states them; the resolved record is explain's.
  "params",
  // Shown as words, never as the text: a caveat's severity, a release's status and its text label (`@draft`).
  "cohorts/[]/caveats/[]/severity",
  "views/[]/caveats/[]/severity",
  "cohorts/[]/release/status",
  "cohorts/[]/release/label@text",
  // A translation: fixed words that one is present, its document and notes not shown (R39, M5.3-rest).
  "translation",
  // A refusal's counts, as every count is shown (R71).
  ...countNotShown("refusals/[]/counts/[]"),
  // lift_differs under any k is suppressed (§8.4, R20), whatever value came.
  { path: "refusals/[]/counts/[]/population/lift_differs", when: (answer) => allUnderK(answer.refusals.flatMap((one) => one.counts ?? [])) },
];

/** Not shown by the count view, each with its reason (D433). */
const COUNT_NOT_SHOWN: readonly Exception<Counts>[] = [
  // The same document's views, which the validation shows (a count follows a valid one).
  "views",
  // The parameters the server resolved from the document: the form shows those it sent (the view's columns) and the view's readback states them; the resolved record is explain's.
  "params",
  // The leaves the server resolved (R48: M5.3-rest).
  "counts/[]/leaves",
  ...countNotShown("counts/[]/count"),
  { path: "counts/[]/count/population/lift_differs", when: (answer) => allUnderK(answer.counts.map((one) => one.count)) },
];

/** A body, and the same body with every list doubled (a copy of its last item appended) when that
 * still walks: so that each member is swept for a second instance (a view that shows a list's first
 * item, or the first cohort's member, alone is found). */
function doubled(component: string, body: string): string[] {
  const grow = (value: unknown): unknown => {
    if (Array.isArray(value)) {
      const items = value.map(grow);
      const last = items.at(-1);
      return last === undefined ? items : [...items, structuredClone(last)];
    }
    if (typeof value === "object" && value !== null) {
      return Object.fromEntries(Object.entries(value).map(([key, member]) => [key, grow(member)]));
    }
    return value;
  };
  const made = JSON.stringify(grow(JSON.parse(body)));
  try {
    walked(component, made);
    return [body, made];
  } catch {
    return [body];
  }
}

/** A sweep of each answer of a view, made once. */
function once<T>(make: () => Swept<T>[]): () => Swept<T>[] {
  let made: Swept<T>[] | undefined;
  return () => (made ??= make());
}

const VALIDATIONS = once(() =>
  [ANSWERS.validated.body, ANSWERS.stringView.body, ANSWERS.numericUnderK.body, translated(), withCounts(), ...synthetics("DocumentValidation")].flatMap((one) => doubled("DocumentValidation", one)).map((body) => {
    const answer = validationOf(body);
    return { answer, found: coverage(answer, (value) => <ValidationOf value={value} />) };
  }),
);
const COUNTS = once(() =>
  [ANSWERS.counted.body, ANSWERS.countedUnderK.body, ...synthetics("CohortCounts")].flatMap((one) => doubled("CohortCounts", one)).map((body) => {
    const answer = countsOf(body);
    return { answer, found: coverage(answer, (value) => <CountsOf value={value} />) };
  }),
);
describe("what the outcome views show of a whole answer (B6.2)", () => {
  it("the validation view shows every member but its exception list, exactly (the server's answers, a translation, a refusal's counts, every synthetic arm)", { timeout: 300_000 }, () => {
    const swept = VALIDATIONS();
    // Eleven bodies, and the doubled of each that still walks.
    expect(swept.length).toBeGreaterThanOrEqual(19);
    expect(coverageProblems(swept, VALIDATION_NOT_SHOWN)).toEqual([]);
    expect(swept.filter(({ answer }) => answer.refusals.length >= 2).length).toBeGreaterThan(0);
    expect(swept.some(({ found }) => found.shown.has("refusals/[]/code@text"))).toBe(true);
    expect(swept.some(({ found }) => found.all.has("translation/format@text"))).toBe(true);
  });

  it("says that an answer holds a translation, in fixed words, and only then", () => {
    expect(renderToStaticMarkup(<ValidationOf value={validationOf(translated())} />)).toContain(WORDS.translation);
    expect(renderToStaticMarkup(<ValidationOf value={validationOf(ANSWERS.validated.body)} />)).not.toContain(WORDS.translation);
  });

  it("the count view shows every member but its exception list, exactly (no setting, under a floor, every synthetic arm)", { timeout: 300_000 }, () => {
    const swept = COUNTS();
    expect(swept.length).toBeGreaterThanOrEqual(14);
    expect(coverageProblems(swept, COUNT_NOT_SHOWN)).toEqual([]);
  });
});

describe("the coverage harness, controlled", () => {
  const found = { all: new Set(["views/a@text", "viewsX/a@text", "kept@number"]), shown: new Set(["viewsX/a@text", "kept@number"]), missed: new Set(["views/a@text"]), leaves: 3 };

  it("matches a prefix only at a segment boundary (h3)", () => {
    expect(under("viewsX/a@text", ["views"])).toBe(false);
    expect(under("views/a@text", ["views"])).toBe(true);
    expect(under("views@text", ["views"])).toBe(true);
    expect(coverageProblems([{ answer: null, found }], ["views"])).toEqual([]);
    expect(coverageProblems([{ answer: null, found }], ["view"])).toEqual(["not shown, and not on the exception list: views/a@text", "an exception that names no path of the answers it applies to: view"]);
  });

  it("reports a path shown for one instance and not for another (a list's second item dropped)", () => {
    const partly = { all: new Set(["refusals/[]/code@text"]), shown: new Set(["refusals/[]/code@text"]), missed: new Set(["refusals/[]/code@text"]), leaves: 2 };
    expect(coverageProblems([{ answer: null, found: partly }], [])).toEqual(["shown for some instances and not for others, and not on the exception list: refusals/[]/code@text"]);
    expect(coverageProblems([{ answer: null, found: partly }], ["refusals"])).toEqual(["shown, but on the exception list: refusals/[]/code@text"]);
  });

  it("reports a stale exception, and one that applies to no answer (h4)", () => {
    expect(coverageProblems([{ answer: null, found }], ["views", "gone"])).toEqual(["an exception that names no path of the answers it applies to: gone"]);
    expect(coverageProblems([{ answer: null, found }], ["views", { path: "kept", when: () => false }])).toEqual(["an exception that names no path of the answers it applies to: kept"]);
    expect(coverageProblems([{ answer: null, found }], ["views", "kept"])).toEqual(["shown, but on the exception list: kept@number"]);
  });
});
