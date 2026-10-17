/**
 * `Refusals` and `Refusal` (D430; D427's R42a, R70, R72a and R73; A3, §8.6). The refusals are walked
 * from JSON as an answer's are: the orchard's two (`UNKNOWN_DATASET` at 404, `WRONG_TYPE` at 422),
 * and refusals written as the server writes them (`WITHHELD_UNDER_K`, as `views._disclosure` does,
 * with `count_cohort` and the disclosed analyses offered; `LIMIT_EXCEEDED` with its limit; one with
 * cohort counts).
 */
import { describe, expect, it } from "vitest";

import type { Refused } from "../../src/api/format";
import { CARRIES_COUNTS, DOCUMENT_LEVEL, Refusal, REFUSED, Refusals, WITHHELD } from "../../src/layout/Refusal";
import { walked } from "./answers";
import { leafSweep } from "./injection";
import { render } from "./render";
import { ORCHARD } from "./typed-support";

/** A refusals body's refusals, walked. */
const refusalsOf = (body: string): Refused["refusals"] => (walked("Refusals", body) as { readonly refusals: Refused["refusals"] }).refusals;

const refused = (status: number, refusals: Refused["refusals"], retryAfter: number | null = null): Refused => ({ kind: "refused", status, refusals, retryAfter });

const WITHHELD_BODY = JSON.stringify({
  refusals: [
    {
      code: "WITHHELD_UNDER_K",
      path: "/views/0/analysis",
      message: [
        { text: "Under a disclosure setting (the dataset's min_cell_count, 5) no view of " },
        { data: "summary.members" },
        { text: " is run, whatever its cohorts' sizes: it lists each member's values (§8.4, D353)" },
      ],
      alternatives: [{ data: "count_cohort" }, { data: "compare.columns" }, { data: "compare.existence" }, { data: "summary.distribution" }],
    },
  ],
});

const LIMIT_BODY = JSON.stringify({
  refusals: [{ code: "LIMIT_EXCEEDED", path: "/cohorts", message: [{ text: "A document has at most 6 cohorts" }], limit: { name: "max_cohorts", max: 6 } }],
});

const MIXED_BODY = JSON.stringify({
  refusals: [
    { code: "UNKNOWN_COLUMN", path: "/cohorts/a/where/column", message: [{ text: "No column " }, { data: "harvests.wieght" }, { text: " in the dataset" }], alternatives: [{ data: "harvests.weight" }, { data: "harvests.width" }] },
    { code: "INVALID_JSON", path: null, message: [{ text: "The document is not JSON" }] },
    { code: "onco.PANEL_UNKNOWN", path: "/views/0", message: [{ text: "A pack's refusal" }], alternatives: [{ text: "a panel the pack knows" }] },
  ],
});

/** A refusal that carries cohort counts (a `CohortCount` of the orchard's own, twice): every optional member of a
 * refusal present, and the counts' own texts (a dataset, a cohort's name, its caveats) among its data. */
function countsBody(): string {
  const body = JSON.parse(WITHHELD_BODY) as { refusals: Record<string, unknown>[] };
  const envelope = ORCHARD.find((answer) => answer.operation === "count_cohort" && answer.status === 200);
  const count = (JSON.parse(envelope?.body ?? "{}") as { counts: { count: unknown }[] }).counts[0]?.count;
  const first = body.refusals[0];
  if (first !== undefined) {
    first["code"] = "COHORTS_OVERLAP";
    first["counts"] = [count, count];
  }
  return JSON.stringify(body);
}

const COUNTS_BODY = countsBody();

/** What a refused outcome shows: each refusal's words, and the whole. */
async function shownOf(outcome: Refused): Promise<{ readonly items: string[]; readonly text: string; readonly controls: number; readonly html: string }> {
  const shown = await render(<Refusals refused={outcome} />);
  const found = {
    items: [...shown.container.querySelectorAll(".refusal")].map((item) => item.textContent),
    text: shown.container.textContent,
    controls: shown.container.querySelectorAll("button, a, input, select, textarea, form").length,
    html: shown.container.innerHTML,
  };
  shown.unmount();
  return found;
}

describe("Refusal", () => {
  it("R42a: shows a refusal's code, its message from its segments, its alternatives listed and never applied, and its path as data, or the document level for a null path", async () => {
    expect.hasAssertions();
    const orchard = ORCHARD.filter((answer) => answer.status >= 400);
    expect(orchard.map((answer) => answer.status).sort()).toEqual([404, 422]);
    for (const answer of orchard) {
      const { items, text } = await shownOf(refused(answer.status, refusalsOf(answer.body)));
      expect(text.startsWith(REFUSED)).toBe(true);
      expect(items.length).toBe(refusalsOf(answer.body).length);
    }
    const { items, controls, html } = await shownOf(refused(422, refusalsOf(MIXED_BODY)));
    expect(items).toEqual([
      "UNKNOWN_COLUMN at /cohorts/a/where/column: No column harvests.wieght in the datasetAvailable (not applied): harvests.weight, harvests.width",
      `INVALID_JSON at ${DOCUMENT_LEVEL}: The document is not JSON`,
      "onco.PANEL_UNKNOWN at /views/0: A pack's refusalAvailable (not applied): a panel the pack knows",
    ]);
    expect(controls).toBe(0);
    expect(html).toContain('<bdi class="data">/cohorts/a/where/column</bdi>');
    expect(html).toContain('<bdi class="data token">harvests.wieght</bdi>');
  });

  it("R70: names the limit a refusal hit, its name and its maximum, as the server's numbers", async () => {
    expect.hasAssertions();
    const { text, html } = await shownOf(refused(413, refusalsOf(LIMIT_BODY)));
    expect(text).toContain("Limit: max_cohorts, at most 6.");
    expect(html).toContain('<bdi class="number">6</bdi>');
    const big = refusalsOf(LIMIT_BODY.replace('"max":6', '"max":9007199254740993'));
    expect((await shownOf(refused(413, big))).text).toContain("at most 9007199254740993.");
    expect((await shownOf(refused(413, refusalsOf(LIMIT_BODY.replace('"max":6', '"max":0'))))).text).toContain("at most 0.");
    expect((await shownOf(refused(422, refusalsOf(WITHHELD_BODY)))).text).not.toContain("Limit:");
  });

  it("R72a: shows a refusals body as refusals at any status of 400 or more, with Retry-After stated where it is given", async () => {
    expect.hasAssertions();
    const refusals = refusalsOf(LIMIT_BODY);
    for (const status of [400, 401, 403, 404, 405, 409, 413, 415, 422, 429, 500, 502, 503, 504, 599]) {
      for (const retryAfter of [null, 0, 30]) {
        const { items, text } = await shownOf(refused(status, refusals, retryAfter));
        expect(items.length).toBe(1);
        expect(text.startsWith(REFUSED)).toBe(true);
        expect(text.split("Try again").length - 1).toBe(retryAfter === null ? 0 : 1);
        if (retryAfter === null) {
          expect(text).not.toContain("Try again");
        } else {
          expect(text.endsWith(`Try again after ${String(retryAfter)} seconds (the server's Retry-After).`)).toBe(true);
        }
      }
    }
  });

  it("R73: WITHHELD_UNDER_K names the binding setting and offers count_cohort and the disclosed analyses, shown and never applied, never a value", async () => {
    expect.hasAssertions();
    const { items, controls, html } = await shownOf(refused(422, refusalsOf(WITHHELD_BODY)));
    expect(items).toEqual([
      `${WITHHELD}WITHHELD_UNDER_K at /views/0/analysis: Under a disclosure setting (the dataset's min_cell_count, 5) no view of summary.members is run, whatever its cohorts' sizes: it lists each member's values (§8.4, D353)Offered instead (not run): count_cohort, compare.columns, compare.existence, summary.distribution`,
    ]);
    expect(controls).toBe(0);
    expect(html).not.toContain('class="number"');
    expect(html).toContain('class="refusal withheld"');
  });

  it("says a refusal carries cohort counts, in fixed words, never a number of them, for one count or several", async () => {
    const envelope = ORCHARD.find((answer) => answer.operation === "count_cohort" && answer.status === 200);
    const count = (JSON.parse(envelope?.body ?? "{}") as { counts: { count: unknown }[] }).counts[0]?.count;
    for (const length of [1, 2, 3]) {
      const withCounts = JSON.parse(WITHHELD_BODY) as { refusals: Record<string, unknown>[] };
      const first = withCounts.refusals[0];
      if (first !== undefined) {
        first["code"] = "COHORTS_OVERLAP";
        first["counts"] = Array.from({ length }, () => count);
      }
      const walkedCounts = refusalsOf(JSON.stringify(withCounts));
      expect(walkedCounts[0]?.counts?.length).toBe(length);
      const { text, html } = await shownOf(refused(422, walkedCounts));
      expect(text).toContain(CARRIES_COUNTS);
      expect(html).not.toContain('class="number"');
    }
    const { text } = await shownOf(refused(422, refusalsOf(LIMIT_BODY)));
    expect(text).not.toContain(CARRIES_COUNTS);
  });

  it("shows a path whole, never cut, however long (D428 m16: a pointer is not cut; m4 of #121 round 1)", async () => {
    const path = `/${"p".repeat(2500)}`;
    const walkedLong = refusalsOf(LIMIT_BODY.replace('"/cohorts"', JSON.stringify(path)));
    const { text } = await shownOf(refused(422, walkedLong));
    expect(text).toContain(`LIMIT_EXCEEDED at ${path}: `);
    const shown = await render(<Refusals refused={refused(422, walkedLong)} />);
    expect(shown.container.querySelector(".cut")).toBeNull();
    shown.unmount();
  });

  // Each sweep renders every position (the code, the path, each text and data segment of the message, each alternative's
  // text, the limit's name, every text of the counts) under every hostile row, once per refusal of the corpus; they grow
  // with it, so they have the shared forms' timeout. The corpus has refusals of each shape: a limit, a null path, a
  // pack's code with a text alternative, withheld under k, and one with counts (`CohortCount`'s own texts).
  it.each([
    ["a refusal with a limit and alternatives", JSON.stringify({ refusals: [{ code: "LIMIT_EXCEEDED", path: "/p", message: [{ text: "t" }, { data: "d" }], alternatives: [{ data: "a" }, { text: "b" }], limit: { name: "max_x", max: 3 } }] })],
    ["refusals of other shapes (a null path, a pack's code, a text alternative)", MIXED_BODY],
    ["a refusal withheld under k", WITHHELD_BODY],
    ["a refusal with cohort counts", COUNTS_BODY],
  ])("shows a hostile text in every position of %s as text alone, by construction", { timeout: 60_000 }, async (_name, body) => {
    const OWN = ["DIV", "SECTION", "P"];
    const CLASSES = ["", "number"];
    const found = await leafSweep(refusalsOf(body), (refusals) => <Refusals refused={refused(422, refusals)} />, OWN, CLASSES);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(0);
    for (const one of refusalsOf(body)) {
      const alone = await leafSweep(one, (refusal) => <Refusal value={refusal} />, OWN, CLASSES);
      expect(alone.problems).toEqual([]);
      expect(alone.leaves).toBeGreaterThan(0);
    }
  });
});

/** The markup of each refusal a list of them shows (its list items of class `refusal`). */
async function refusalItems(element: React.ReactElement): Promise<string[]> {
  const shown = await render(element);
  const items = [...shown.container.querySelectorAll(".refusal")].map((item) => item.outerHTML);
  shown.unmount();
  return items;
}

describe("Refusals contain their parts", () => {
  it("M1: the refusals of Refusals are Refusal's own, each as it shows alone, in order, over every corpus of the rows", async () => {
    expect.hasAssertions();
    const corpora = [...ORCHARD.filter((answer) => answer.status >= 400).map((answer) => refusalsOf(answer.body)), MIXED_BODY, LIMIT_BODY, WITHHELD_BODY, COUNTS_BODY].map((body) => (typeof body === "string" ? refusalsOf(body) : body));
    expect(corpora.length).toBe(6);
    for (const refusals of corpora) {
      const whole = await refusalItems(<Refusals refused={refused(422, refusals)} />);
      const parts: (string | undefined)[] = [];
      for (const one of refusals) {
        parts.push((await refusalItems(<Refusal value={one} />))[0]);
      }
      expect(whole.length).toBe(refusals.length);
      expect(whole).toEqual(parts);
      // the items stand in a list: their parent is the list, and holds nothing else
      const shown = await render(<Refusals refused={refused(422, refusals)} />);
      const list = shown.container.querySelector('section > [role="list"]');
      expect(list === null ? [] : [...list.children].map((child) => `${child.getAttribute("role") ?? ""} ${child.className}`.trim().split(" ")[0])).toEqual(refusals.map(() => "listitem"));
      shown.unmount();
    }
  });
});

describe("Refusal alone", () => {
  it("shows one refusal as the list shows it", async () => {
    const [one] = refusalsOf(WITHHELD_BODY);
    if (one === undefined) {
      throw new Error("no refusal");
    }
    const shown = await render(<Refusal value={one} />);
    expect(shown.container.textContent.startsWith(WITHHELD)).toBe(true);
    shown.unmount();
  });
});
