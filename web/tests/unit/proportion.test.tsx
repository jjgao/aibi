/**
 * `Proportion` (D435; D427's R12 and R13; §8.2): every proportion of the live answers' category rows,
 * and proportions written from them, walked as the typed answers give them.
 */
import { describe, expect, it } from "vitest";

import { type BoxedProportion, Proportion, PROPORTION_WORDS } from "../../src/layout/Proportion";
import { walked } from "./answers";
import { render } from "./render";
import { type Json, list, object, RUNS } from "./result-support";

/** Every category row's proportion of every live run answer, raw. */
function proportions(): Json[] {
  const found: Json[] = [];
  for (const [, body] of RUNS) {
    for (const result of list((JSON.parse(body) as Json)["results"]).map(object)) {
      for (const position of list(object(result["values"])["positions"]).map(object)) {
        for (const column of list(position["columns"]).map(object)) {
          for (const row of list(column["categories"]).map(object)) {
            found.push(object(row["proportion"]));
          }
        }
      }
    }
  }
  return found;
}

async function shown(raw: Json): Promise<{ readonly text: string; readonly element: HTMLElement }> {
  const rendered = await render(<Proportion value={walked("Proportion", JSON.stringify(raw)) as BoxedProportion} />);
  const element = rendered.container.cloneNode(true) as HTMLElement;
  rendered.unmount();
  return { text: element.textContent, element };
}

const numberText = (value: unknown): string => (typeof value === "number" ? JSON.stringify(value) : "");

describe("Proportion (D435)", () => {
  it("R12: every proportion shows its numerator, its denominator, its estimate and its denominator_definition's three members under their own names, as sent, and its denominator_text; never a bare estimate", async () => {
    expect.hasAssertions();
    const all = proportions();
    expect(all.length).toBeGreaterThan(10);
    for (const raw of all) {
      const { text, element } = await shown(raw);
      const definition = object(raw["denominator_definition"]);
      const numbers = [...element.querySelectorAll("bdi.number")].map((one) => one.textContent);
      for (const member of ["numerator", "denominator", "estimate"]) {
        expect(text).toContain(`${member} `);
        if (raw[member] !== null) {
          expect(numbers).toContain(numberText(raw[member]));
        }
      }
      expect(text).toContain(`denominator definition: counts ${String(definition["counts"])}, position ${definition["position"] === null ? PROPORTION_WORDS.none : numberText(definition["position"])}, predicate ${definition["predicate"] === null ? PROPORTION_WORDS.none : (definition["predicate"] as string)}`);
      // `counts` is a data token, never explained.
      expect(element.querySelector("bdi.data.token")?.textContent).toBe(definition["counts"]);
    }
    // A position of none, a predicate and a denominator text, as sent (synthetic: no live proportion has them).
    const base = all[0] ?? {};
    const { text, element } = await shown({ ...base, denominator_definition: { counts: "unit_table", position: null, predicate: "/cohorts/first" }, denominator_text: [{ text: "the units of " }, { data: "trees" }] });
    expect(text).toContain(`counts unit_table, position ${PROPORTION_WORDS.none}, predicate /cohorts/first (the units of trees)`);
    expect([...element.querySelectorAll("bdi.data.token")].map((one) => one.textContent)).toEqual(["unit_table", "/cohorts/first", "trees"]);
    // A null estimate says why, never 0 or blank (the empty cohort's row of (d)).
    const empty = await shown({ ...base, estimate: null, numerator: 0, denominator: 0, not_estimable: { "/estimate": "no_units" } });
    expect(empty.text).toContain("estimate not estimable: no units");
  });

  it("R13: a proportion's excluded is shown by reason, every reason and its zero, a suppressed one as suppressed; its interval in fixed words", async () => {
    expect.hasAssertions();
    const base = proportions()[0] ?? {};
    const reasons = { NOT_ASSESSED: 0, NOT_COVERED: 0, NO_INFORMATION: 3, NO_PARENT: 0, OUT_OF_SCOPE: 0, NO_ROWS: 0, NOT_APPLICABLE: 0, INVALID_VALUE: 1 };
    const excluded = await shown({ ...base, excluded: reasons });
    expect(excluded.text).toContain("; excluded NOT_ASSESSED 0, NOT_COVERED 0, NO_INFORMATION 3, NO_PARENT 0, OUT_OF_SCOPE 0, NO_ROWS 0, NOT_APPLICABLE 0, INVALID_VALUE 1");
    const suppressed = await shown({ ...base, excluded: null, not_estimable: { "/excluded": "suppressed" } });
    expect(suppressed.text).toContain("; excluded suppressed");
    expect((await shown(base)).text).not.toContain("excluded");
    const interval = await shown({ ...base, ci: { method: "wilson", level: 0.95, low: 0.1, high: 0.9 } });
    expect(interval.text).toContain(PROPORTION_WORDS.interval);
    expect([...interval.element.querySelectorAll("bdi.number")].map((one) => one.textContent)).not.toContain("0.95");
  });
});
