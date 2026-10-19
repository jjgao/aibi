/**
 * `Population` and `Counted` (D433; D427's R18, R20, R40a and R71; §6.6, §8.4, D297), over the
 * server's own count answers (`orchard-answers.json`: the builder's two-cohort count, and its
 * category view under a floor of 50, every count suppressed) and over populations written from them
 * (a member at a time made `null` or listed in `suppressed`, the reasons' ties).
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ServerNumber } from "../../src/api/box";
import type { Refused } from "../../src/api/format";
import type { Answered } from "../../src/api/typed";
import { REASON_WORDS, SUPPRESSED } from "../../src/layout/Figure";
import { Counted, Population, SIZE_NOT_SHOWN } from "../../src/layout/Population";
import { Refusals } from "../../src/layout/Refusal";
import { walked } from "./answers";
import { leafSweep } from "./injection";
import { render } from "./render";
import { answersOf } from "./screens";

type Counts = Answered<"count_cohort">;

/** The builder's count answers: the two-cohort count (13/27/0, 40/0/0) and the floored one. */
const COUNTED = answersOf("count_cohort").map((answer) => answer.body);
const [, TWO, FLOORED] = COUNTED;

const countsOf = (text: string): Counts => walked("CohortCounts", text) as Counts;

/** The first count of the two-cohort answer, its population replaced by `patch`'s members. */
function withPopulation(patch: Readonly<Record<string, unknown>>, disclosure: { readonly min_cell_count: number | null } | null = null): Counts["counts"][number]["count"] {
  const body = JSON.parse(TWO ?? "{}") as { counts: { count: { population: Record<string, unknown>; disclosure: unknown } }[] };
  const first = body.counts[0];
  if (first === undefined) {
    throw new Error("no count");
  }
  first.count.population = { ...first.count.population, ...patch };
  if (disclosure !== null) {
    first.count.disclosure = disclosure;
  }
  const found = countsOf(JSON.stringify(body)).counts[0];
  if (found === undefined) {
    throw new Error("no count");
  }
  return found.count;
}

/** The text an element shows, its runs of white space one space. */
const textOf = (element: Element): string => element.textContent.replace(/\s+/gu, " ").trim();

/** The text a population shows. */
async function shown(count: Counts["counts"][number]["count"]): Promise<string> {
  const found = await render(<Population value={count.population} disclosure={count.disclosure} />);
  const text = textOf(found.container);
  found.unmount();
  return text;
}

const ZEROS = { NOT_ASSESSED: 0, NOT_COVERED: 0, NO_INFORMATION: 0, NO_PARENT: 0, OUT_OF_SCOPE: 0, NO_ROWS: 0 };

describe("Population", () => {
  it("R40a: counts include unknowns: n_unknown by reason, every reason and its zero, beside the true and false counts", async () => {
    expect.hasAssertions();
    const counts = countsOf(TWO ?? "").counts;
    const texts: string[] = [];
    for (const named of counts) {
      texts.push(await shown(named.count));
    }
    expect(texts[0]).toBe(
      "n_true 13; n_false 27; n_unknown 0n_unknown by reason: NOT_ASSESSED 0, NOT_COVERED 0, NO_INFORMATION 0, NO_PARENT 0, OUT_OF_SCOPE 0, NO_ROWS 0n_unknown by leaf: leaf:5cab1a8184d6ac3628453c6a6b926abf262878f28c441ffc9676f8150bddcd32 0lift_differs 0",
    );
    expect(texts[1]).toContain("n_true 40; n_false 0; n_unknown 0n_unknown by reason: NOT_ASSESSED 0");
    expect(texts[1]).toContain("n_unknown by leaf: none");
  });

  it("R18: a positive n_unknown is shown with its largest reason (ties in the vocabulary's order); a zero, suppressed n_unknown or a null map names none", async () => {
    expect.hasAssertions();
    expect(await shown(withPopulation({ n_unknown: 7, unknown_by_reason: { ...ZEROS, NOT_ASSESSED: 2, NO_ROWS: 5 } }))).toContain("n_unknown 7 (largest reason NO_ROWS)");
    expect(await shown(withPopulation({ n_unknown: 6, unknown_by_reason: { ...ZEROS, NOT_COVERED: 3, NOT_ASSESSED: 3 } }))).toContain("n_unknown 6 (largest reason NOT_ASSESSED)");
    expect(await shown(withPopulation({ n_unknown: 0 }))).not.toContain("largest reason");
    const suppressed = await shown(withPopulation({ n_unknown: null, unknown_by_reason: { ...ZEROS, NO_ROWS: 5 }, suppressed: ["/n_unknown"] }));
    expect(suppressed).toContain(`n_unknown ${SUPPRESSED}`);
    expect(suppressed).not.toContain("largest reason");
    const nullMap = await shown(withPopulation({ n_unknown: 3, unknown_by_reason: null, suppressed: ["/unknown_by_reason"] }));
    expect(nullMap).toContain("n_unknown 3n_unknown by reason: suppressed");
    expect(nullMap).not.toContain("largest reason");
  });

  it("R20: each population count is a value or suppressed by the entry's suppressed pointers; lift_differs suppressed under k, never shown as 0", async () => {
    expect.hasAssertions();
    const floored = countsOf(FLOORED ?? "").counts;
    expect(floored.length).toBe(1);
    const all = await shown(floored[0]?.count ?? withPopulation({}));
    expect(all).toBe(`n_true ${SUPPRESSED}; n_false ${SUPPRESSED}; n_unknown ${SUPPRESSED}n_unknown by reason: ${SUPPRESSED}n_unknown by leaf: ${SUPPRESSED}lift_differs ${SUPPRESSED}`);
    expect(all).not.toMatch(/\d/u);
    // A value the server also listed as suppressed is never shown (fail closed), each member alone.
    const members = { n_true: "/n_true", n_false: "/n_false", n_unknown: "/n_unknown", lift_differs: "/lift_differs" } as const;
    for (const [member, pointer] of Object.entries(members)) {
      const text = await shown(withPopulation({ [member]: 9, suppressed: [pointer] }));
      expect(text, member).toContain(`${member} ${SUPPRESSED}`);
      expect(text, member).not.toContain(" 9");
    }
    for (const [member, pointer] of [
      ["unknown_by_reason", "/unknown_by_reason"],
      ["unknown_by_leaf", "/unknown_by_leaf"],
    ] as const) {
      const text = await shown(withPopulation({ suppressed: [pointer] }));
      expect(text, member).toContain(`n_unknown by ${member === "unknown_by_reason" ? "reason" : "leaf"}: ${SUPPRESSED}`);
    }
    expect(SUPPRESSED).toBe(REASON_WORDS.suppressed);
    // The page's own hold (fail closed): a count under k whose lift_differs came as a number, a 0
    // included and unlisted, is shown as suppressed; with no setting, the same value is shown.
    for (const value of [0, 3]) {
      const underK = await shown(withPopulation({ lift_differs: value, suppressed: [] }, { min_cell_count: 50 }));
      expect(underK, String(value)).toContain(`lift_differs ${SUPPRESSED}`);
      expect(underK, String(value)).toContain("n_true 13");
      expect(await shown(withPopulation({ lift_differs: value, suppressed: [] }, { min_cell_count: null }))).toContain(`lift_differs ${String(value)}`);
      // As every screen shows a count (Counted: the builder's counts, a refusal's): the count's own setting reaches it.
      const counted = await render(<Counted value={withPopulation({ lift_differs: value, suppressed: [] }, { min_cell_count: 50 })} />);
      expect(textOf(counted.container), String(value)).toContain(`lift_differs ${SUPPRESSED}`);
      counted.unmount();
    }
  });

  it("shows a hostile text at every position of a cohort count as text alone, by construction", { timeout: 60_000 }, async () => {
    const count = countsOf(TWO ?? "").counts[0]?.count;
    if (count === undefined) {
      throw new Error("no count");
    }
    const found = await leafSweep(count, (value) => <Counted value={value} />, ["DIV", "P", "SECTION"], ["", "number", "release", "label", "label draft", "disagree", "suppressed", "none", "largest", "affects"]);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(10);
  });
});

describe("Population under k, by brute force", () => {
  it("shows lift_differs suppressed whenever the count's disclosure sets k, whatever the other members and its own value (R20, §8.4: fail closed)", { timeout: 120_000 }, () => {
    const REASONS = { NOT_ASSESSED: 1, NOT_COVERED: 0, NO_INFORMATION: 0, NO_PARENT: 0, OUT_OF_SCOPE: 0, NO_ROWS: 2 };
    const LEAVES = { [`leaf:${"a".repeat(64)}`]: 3 };
    /** Each member: its value, its value listed as suppressed, or null and listed. */
    const STATES = ["value", "listed", "null"] as const;
    const MEMBERS = [
      ["n_true", 13, "/n_true"],
      ["n_false", 27, "/n_false"],
      ["n_unknown", 3, "/n_unknown"],
      ["unknown_by_reason", REASONS, "/unknown_by_reason"],
      ["unknown_by_leaf", LEAVES, "/unknown_by_leaf"],
    ] as const;
    let cases = 0;
    for (const k of [null, 50]) {
      const disclosure = walked("Disclosure", JSON.stringify({ min_cell_count: k })) as { readonly min_cell_count: ServerNumber | null };
      for (let shape = 0; shape < 3 ** MEMBERS.length; shape += 1) {
        for (const lift of [0, 4, null]) {
          for (const liftListed of [false, true]) {
            const population: Record<string, unknown> = {};
            const suppressed: string[] = [];
            MEMBERS.forEach(([name, value, pointer], index) => {
              const state = STATES[Math.floor(shape / 3 ** index) % 3] ?? "value";
              population[name] = state === "null" ? null : value;
              if (state !== "value") {
                suppressed.push(pointer);
              }
            });
            population["lift_differs"] = lift;
            if (liftListed || lift === null) {
              suppressed.push("/lift_differs");
            }
            population["suppressed"] = suppressed;
            const walkedPopulation = walked("Population", JSON.stringify(population)) as Counts["counts"][number]["count"]["population"];
            const markup = renderToStaticMarkup(<Population value={walkedPopulation} disclosure={disclosure} />).replace(/<[^>]+>/gu, "");
            const shown = /lift_differs (\S+)/u.exec(markup)?.[1];
            const expected = k !== null || lift === null || liftListed ? SUPPRESSED : String(lift);
            expect(shown, JSON.stringify({ k, population })).toBe(expected);
            cases += 1;
          }
        }
      }
    }
    expect(cases).toBe(2 * 243 * 3 * 2);
  });
});

describe("Counted", () => {
  it("says its size is not shown in these words, by value", () => {
    expect(SIZE_NOT_SHOWN).toBe("The cohort's size as a share of the unit table is not shown yet.");
  });

  it("R71: a refusal's counts are shown as cohort counts are: each one's markup is Counted's alone", async () => {
    expect.hasAssertions();
    const count = JSON.parse(TWO ?? "{}") as { counts: { count: unknown }[] };
    const body = JSON.stringify({ refusals: [{ code: "COHORTS_OVERLAP", path: "/views/0", message: [{ text: "Cohorts overlap" }], counts: count.counts.map((one) => one.count) }] });
    const refusals = (walked("Refusals", body) as { readonly refusals: Refused["refusals"] }).refusals;
    const shownRefusal = await render(<Refusals refused={{ kind: "refused", status: 422, refusals, retryAfter: null }} />);
    const inRefusal = [...shownRefusal.container.querySelectorAll(".counted")].map((one) => one.outerHTML);
    shownRefusal.unmount();
    const alone: string[] = [];
    for (const named of countsOf(TWO ?? "").counts) {
      const one = await render(<Counted value={named.count} />);
      alone.push(one.container.innerHTML);
      one.unmount();
    }
    expect(inRefusal).toEqual(alone);
    expect(inRefusal.length).toBe(2);
  });

  it("shows a count's id, releases, disclosure, caveats, readback and population, and says its size is not shown", async () => {
    const named = countsOf(TWO ?? "").counts[0];
    if (named === undefined) {
      throw new Error("no count");
    }
    const found = await render(<Counted value={named.count} />);
    const text = textOf(found.container);
    expect(text).toMatch(/^Count drv:[0-9a-f]{64}Releases: orchard@1 · manifest sha256:[0-9a-f]{64}Disclosure: none/u);
    expect(text).toContain("UNCONFIRMED_SEMANTICS (warning)");
    expect(text).toContain("Rows of trees in orchard @1 for which the variety is apple.");
    expect(text).toContain("n_true 13");
    expect(text).toContain(SIZE_NOT_SHOWN);
    expect(text).not.toContain("0.325");
    expect(text).not.toContain("sha256:d68f0c58");
    found.unmount();
  });
});
