/**
 * The charts' words (D435; D427's R31a; §8.5, A3): a result that carries charts says none is drawn
 * here, one with none says it has none; no member of a chart reaches the page.
 */
import { describe, expect, it, vi } from "vitest";

import { CHART_WORDS } from "../../src/result/Charts";
import { changed, LIVE, list, object, RUNS, shownOf, words } from "./result-support";

// This file's cells render whole results under the suite's load (D435): its own time per cell, as a
// row's test takes no options of its own (D427); the unit project's default stays 5 s.
vi.setConfig({ testTimeout: 20_000 });

describe("the charts' words (D435)", () => {
  it("R31a: a result that carries charts says they are not drawn on this page, and one with none says it has none", async () => {
    expect.hasAssertions();
    let some = 0;
    for (const [, body] of RUNS) {
      const results = list(object(JSON.parse(body))["results"]).map(object);
      const shown = words(await shownOf(body));
      const carrying = results.filter((result) => list(result["charts"]).length > 0).length;
      expect(shown.split(CHART_WORDS.some).length - 1).toBe(carrying);
      expect(shown.split(CHART_WORDS.none).length - 1).toBe(results.length - carrying);
      some += carrying;
    }
    expect(some).toBeGreaterThan(5);
    const none = words(
      await shownOf(
        changed(LIVE.a, (result) => {
          result["charts"] = [];
        }),
      ),
    );
    expect(none).toContain(CHART_WORDS.none);
    expect(none).not.toContain(CHART_WORDS.some);
    // No member of a chart is shown: its description is a text the page never shows.
    const description = String(object(list(object(list(object(JSON.parse(RUNS.find(([index]) => index === LIVE.a)?.[1] ?? "{}"))["results"])[0])["charts"])[0])["description"]);
    expect(words(await shownOf(RUNS.find(([index]) => index === LIVE.a)?.[1] ?? "{}"))).not.toContain(description);
  });
});
