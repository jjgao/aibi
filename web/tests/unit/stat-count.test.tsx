/**
 * `StatCount` (D430, D431; D427's R51, per R14): a row count with its `stat:` reference, always,
 * never cut; a `null` count with a reason at `/count` in its `not_estimable` says the reason (a
 * known one in its words, an unknown one as its text), and one without says suppressed: never 0
 * and never blank. Every text of it is swept (`leafSweep`).
 */
import { describe, expect, it } from "vitest";

import { boxMap, boxText } from "../../src/api/box";
import { type Stat, StatCount } from "../../src/layout/StatCount";
import { walked } from "./answers";
import { leafSweep } from "./injection";
import { render } from "./render";

const REFERENCE = `stat:sha256:${"ab".repeat(32)}/trees/n_rows`;

const stat = (body: Record<string, unknown>): Stat => walked("StatCount", JSON.stringify({ reference: REFERENCE, ...body })) as Stat;

async function shown(value: Stat): Promise<{ readonly words: string; readonly html: string }> {
  const found = await render(<StatCount value={value} />);
  const result = { words: found.container.textContent, html: found.container.innerHTML };
  found.unmount();
  return result;
}

describe("StatCount", () => {
  it("R51: every row count carries its stat: reference; a suppressed one is shown as suppressed, and a not estimable one with its reasons", async () => {
    expect.hasAssertions();
    expect((await shown(stat({ count: 40 }))).words).toBe(`40 ${REFERENCE}`);
    expect((await shown(stat({ count: 0 }))).words).toBe(`0 ${REFERENCE}`);
    expect((await shown(stat({ count: 1.1e3 }))).words).toBe(`1100 ${REFERENCE}`);
    // A null with no reason: a count is null only when suppressed (R14).
    expect((await shown(stat({ count: null }))).words).toBe(`suppressed ${REFERENCE}`);
    // A null with a reason at /count: the reason.
    expect((await shown(stat({ count: null, not_estimable: { "/count": "suppressed" } }))).words).toBe(`suppressed ${REFERENCE}`);
    expect((await shown(stat({ count: null, not_estimable: { "/count": "no_units" } }))).words).toBe(`not estimable: no units ${REFERENCE}`);
    // A reason at another pointer is no reason of the count's (the walk refuses the key; a box stands in): suppressed.
    const other: Stat = { count: null, reference: boxText(REFERENCE), not_estimable: boxMap([[boxText("/other"), boxText("no_units")]]) };
    expect((await shown(other)).words).toBe(`suppressed ${REFERENCE}`);
    // A reason the page does not know: its text, as data (the walk refuses it; a box stands in).
    const unknown: Stat = { count: null, reference: boxText(REFERENCE), not_estimable: boxMap([[boxText("/count"), boxText("a_new_reason")]]) };
    expect((await shown(unknown)).words).toBe(`not estimable: a_new_reason ${REFERENCE}`);
  });

  it("R51: never shows a null count as 0 or blank, and shows the number by its source text", async () => {
    expect.hasAssertions();
    for (const body of [{ count: null }, { count: null, not_estimable: { "/count": "zero_variance" } }]) {
      const { words } = await shown(stat(body));
      expect(words.startsWith(REFERENCE)).toBe(false);
      expect(words).not.toMatch(/^0 /u);
    }
    const text = walked("StatCount", `{"count": 4.00e1, "reference": "${REFERENCE}"}`) as Stat;
    expect((await shown(text)).words).toBe(`4.00e1 ${REFERENCE}`);
  });

  it("R51: shows a reference of 5,000 code points whole, never cut", async () => {
    expect.hasAssertions();
    const long = `stat:${"x".repeat(4995)}`;
    const found = await render(<StatCount value={walked("StatCount", JSON.stringify({ count: 1, reference: long })) as Stat} />);
    expect(found.container.textContent).toContain(long);
    expect(found.container.querySelector(".cut")).toBeNull();
    found.unmount();
  });

  it("shows a hostile text in every position as text alone, by construction (both null arms and a count)", { timeout: 60_000 }, async () => {
    const unknown: Stat = { count: null, reference: boxText(REFERENCE), not_estimable: boxMap([[boxText("/count"), boxText("weird")]]) };
    for (const value_ of [stat({ count: 3 }), stat({ count: null }), stat({ count: null, not_estimable: { "/count": "no_units" } }), unknown]) {
      const found = await leafSweep(value_, (value) => <StatCount value={value} />, [], ["stat", "suppressed", "not-estimable", "not-shown"]);
      expect(found.problems).toEqual([]);
      expect(found.leaves).toBeGreaterThan(0);
    }
  });
});
