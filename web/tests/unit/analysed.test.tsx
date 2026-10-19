/**
 * `Analysed` (D435; D427's R19; §6.6, §8.1): each position's analysed counts on the live answers,
 * through the real seam, and their variables labelled and unlabelled.
 */
import { describe, expect, it, vi } from "vitest";

import { ANALYSED_WORDS } from "../../src/result/Analysed";
import { changed, type Json, LIVE, list, liveBody, object, shownOf, words } from "./result-support";

// This file's cells render whole results under the suite's load (D435): its own time per cell, as a
// row's test takes no options of its own (D427); the unit project's default stays 5 s.
vi.setConfig({ testTimeout: 20_000 });

/** The words the page gives one analysed entry's three counts. */
function threeOf(entry: Json): string {
  const count = (value: unknown): string => (value === null ? "suppressed" : JSON.stringify(value));
  const excluded = entry["excluded"];
  const reasons =
    excluded === null
      ? "suppressed"
      : Object.entries(object(excluded))
          .map(([reason, value]) => `${reason} ${count(value)}`)
          .join(" , ");
  return `n ${count(entry["n"])} ; excluded_units ${count(entry["excluded_units"])} ; excluded by reason ${reasons}`;
}

describe("Analysed (D435)", () => {
  it("R19: per position, the units analysed and those excluded per reason, zeros included, and per variable under its label in the view's order; a null count as suppressed", async () => {
    expect.hasAssertions();
    for (const index of [LIVE.a, LIVE.c, LIVE.d, LIVE.e, LIVE.h]) {
      const body = liveBody(index);
      const result = object(list(body["results"])[0]);
      const labels = list(object(object(object(object(result["derivation"])["document"])["view"])["params"])["columns"]).map((column) => String(object(column)["column"]));
      const sections = [...(await shownOf(body)).querySelectorAll('[aria-label="analysed"]')];
      const analysed = list(result["analysed"]).map(object);
      expect(sections).toHaveLength(analysed.length);
      for (const [at, entry] of analysed.entries()) {
        const shown = sections[at] === undefined ? "" : words(sections[at]);
        expect(shown.startsWith(`Analysed: ${threeOf(entry)}`), shown).toBe(true);
        for (const [j, variable] of list(entry["variables"]).map(object).entries()) {
          expect(shown).toContain(`${labels[j] ?? ""} : ${threeOf(variable)}`);
        }
      }
    }
    // (e), floor 50: every analysed count suppressed.
    const floored = words((await shownOf(liveBody(LIVE.e))).querySelector('[aria-label="analysed"]') ?? document.createElement("p"));
    expect(floored).toContain("n suppressed ; excluded_units suppressed ; excluded by reason suppressed");
  });

  it("variables that do not line up with the labels are shown in order under fixed words, unlabelled, never shifted", async () => {
    const body = changed(LIVE.a, (result) => {
      list(object(list(result["analysed"])[1])["variables"]).pop();
    });
    const sections = [...(await shownOf(body)).querySelectorAll('[aria-label="analysed"]')].map(words);
    for (const shown of sections) {
      expect(shown).toContain(ANALYSED_WORDS.unlabelled);
      expect(shown).not.toContain("trees.height_m :");
    }
  });
});
