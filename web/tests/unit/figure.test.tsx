/**
 * `Count` and `Figure` (D430; D427's R06 and R14): a number that may be `null` is never shown as 0
 * or blank. A null count is suppressed; any other null says why, from the enclosing
 * `not_estimable` at its own pointer, in words of its own for each member of the generated
 * vocabulary (the corpus is the vocabulary), the server's text for a reason the page does not know,
 * and "not shown" where no reason is given (never suppressed by default).
 */
import { describe, expect, it } from "vitest";

import { boxMap, boxText, type ServerNumber, type ServerText } from "../../src/api/box";
import { decode } from "../../src/api/decode";
import { VOCABULARIES } from "../../src/api/generated/shapes";
import { Count, Figure, NOT_SHOWN, REASON_WORDS, SUPPRESSED } from "../../src/layout/Figure";
import { walked } from "./answers";
import { leafSweep } from "./injection";
import { render } from "./render";

function decoded(text: string): ServerNumber {
  const [found] = decode(`[${text}]`) as readonly ServerNumber[];
  if (found === undefined) {
    throw new Error("no number");
  }
  return found;
}

/** A `not_estimable` map of pointers to reasons. */
const reasons = (entries: readonly (readonly [string, string])[]): ReturnType<typeof boxMap<ServerText>> => boxMap(entries.map(([pointer, reason]) => [boxText(pointer), boxText(reason)] as const));

const shownOf = async (element: React.ReactElement): Promise<{ readonly text: string; readonly html: string }> => {
  const shown = await render(<p>{element}</p>);
  const found = { text: shown.container.textContent, html: shown.container.innerHTML };
  shown.unmount();
  return found;
};

describe("Count", () => {
  it("R06: shows a null number as suppressed or as not estimable with its reason, never as 0 or blank", async () => {
    expect.hasAssertions();
    expect((await shownOf(<Count value={null} />)).text).toBe(SUPPRESSED);
    for (const reason of VOCABULARIES.NotEstimableReason) {
      const { text } = await shownOf(<Figure value={null} notEstimable={reasons([["/estimate", reason]])} at="/estimate" />);
      expect(text).not.toBe("");
      expect(text).not.toMatch(/^\s*0\s*$/u);
      expect(text).toBe(reason === "suppressed" ? SUPPRESSED : `not estimable: ${REASON_WORDS[reason]}`);
    }
    expect((await shownOf(<Figure value={null} notEstimable={undefined} at="/estimate" />)).text).toBe(NOT_SHOWN);
    expect((await shownOf(<Count value={decoded("0")} />)).text).toBe("0");
    expect((await shownOf(<Figure value={decoded("0.0")} notEstimable={undefined} at="/estimate" />)).text).toBe("0.0");
  });
});

describe("Figure", () => {
  it("R14: a null count is suppressed (StatCount's included); another null shows the reason at its own pointer, an unknown one as the server's text, and none fails closed as not shown, never suppressed by default", async () => {
    expect.hasAssertions();
    const stat = walked("StatCount", '{"count":null,"not_estimable":{"/count":"suppressed"},"reference":"stat:sha256:' + "0".repeat(64) + '/dataset"}') as { readonly count: ServerNumber | null };
    expect((await shownOf(<Count value={stat.count} />)).text).toBe(SUPPRESSED);
    const map = reasons([
      ["/estimate", "zero_denominator"],
      ["/ci/low", "suppressed"],
      ["/ci/high", "a_reason_from_a_newer_server"],
    ]);
    expect((await shownOf(<Figure value={null} notEstimable={map} at="/estimate" />)).text).toBe("not estimable: a zero denominator");
    expect((await shownOf(<Figure value={null} notEstimable={map} at="/ci/low" />)).text).toBe(SUPPRESSED);
    const unknown = await shownOf(<Figure value={null} notEstimable={map} at="/ci/high" />);
    expect(unknown.text).toBe("not estimable: a_reason_from_a_newer_server");
    expect(unknown.html).toContain('<bdi class="data">a_reason_from_a_newer_server</bdi>');
    expect((await shownOf(<Figure value={null} notEstimable={map} at="/numerator" />)).text).toBe(NOT_SHOWN);
    expect((await shownOf(<Figure value={null} notEstimable={null} at="/estimate" />)).text).toBe(NOT_SHOWN);
    expect((await shownOf(<Figure value={decoded("1e-07")} notEstimable={map} at="/estimate" />)).text).toBe("1e-07");
  });

  it("has words for every reason of the generated vocabulary, and no others: the reason's own words, or a reviewed phrasing of them", () => {
    expect(Object.keys(REASON_WORDS).sort()).toEqual([...VOCABULARIES.NotEstimableReason].sort());
    expect(new Set(Object.values(REASON_WORDS)).size).toBe(VOCABULARIES.NotEstimableReason.length);
    /** The reasons whose words are not their own words spelled out, each reviewed. */
    const PHRASED: Readonly<Record<string, string>> = {
      zero_denominator: "a zero denominator",
      beyond_follow_up: "beyond the follow-up",
      not_converged: "the fit did not converge",
      degenerate_table: "a degenerate table",
      overlapping_cohorts: "the cohorts overlap",
      confounded_with_dataset: "confounded with the dataset",
    };
    for (const reason of VOCABULARIES.NotEstimableReason) {
      expect(REASON_WORDS[reason], reason).toBe(PHRASED[reason] ?? reason.replaceAll("_", " "));
    }
  });

  // Each sweep renders every ServerText position (the reason, the map's key: a hostile key finds no reason, so "not shown") under every hostile row; it grows with
  // the corpus, so it has the shared forms' timeout.
  it("shows a hostile text in every position of the not-estimable map as text alone, by construction", { timeout: 60_000 }, async () => {
    const found = await leafSweep(reasons([["/estimate", "x"]]), (map) => <Figure value={null} notEstimable={map} at="/estimate" />, [], ["not-estimable", "not-shown"]);
    expect(found.problems).toEqual([]);
    expect(found.leaves).toBeGreaterThan(0);
  });

  it("cuts an unknown reason of more than 2,000 code points, with the cut mark, as any text from data (Text, not Ref; m4 of #121 round 1)", async () => {
    const reason = `a_reason_${"x".repeat(2500)}`;
    const shown = await render(<Figure value={null} notEstimable={reasons([["/estimate", reason]])} at="/estimate" />);
    expect(shown.container.querySelector(".cut")).not.toBeNull();
    expect(shown.container.textContent).not.toContain(reason);
    expect(shown.container.textContent).toContain(reason.slice(0, 2000));
    shown.unmount();
  });

  it("reads a reason at its own pointer alone, never a hostile pointer's", async () => {
    const map = boxMap([
      [boxText("/estimate‮"), boxText("no_units")],
      [boxText("<img src=x onerror=alert(1)>"), boxText("suppressed")],
    ]);
    expect((await shownOf(<Figure value={null} notEstimable={map} at="/estimate" />)).text).toBe(NOT_SHOWN);
  });
});
