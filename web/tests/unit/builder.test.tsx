/**
 * The builder's rows (D433; D427's R41 and R49a), driven through the rendered controls under
 * `<StrictMode>` over the real seam and the server's own answers: the readback beside each cohort
 * and view of a validation, and the overlap and cross-dataset caveats wherever the builder's answers
 * carry them (each of the four codes at each of the four positions, in four rounds). The other cells of the screens
 * are `builder-flows.test.tsx`'s; this file names no hook (D427's gate), so each cell cleans up
 * after itself.
 */
import { describe, expect, it, vi } from "vitest";

import { Builder } from "../../src/builder/Builder";
import { ANSWERS, type BuilderPage, builderPage, byRequest, fillBuilt } from "./builder-support";
import { json, type Sent } from "./screens";

/** The text of an element, its runs of white space one space. */
const textOf = (element: Element | null | undefined): string => (element?.textContent ?? "").replace(/\s+/gu, " ").trim();

/** Run `body` over a fresh builder page, and close it and the stubs whatever happens. */
async function withPage(answer: (sent: Sent) => Response, body: (page: BuilderPage) => Promise<void>): Promise<void> {
  const page = await builderPage(answer);
  try {
    await body(page);
  } finally {
    page.close();
    vi.unstubAllGlobals();
  }
}

const CODES = ["COHORTS_OVERLAP", "CONFOUNDED_WITH_DATASET", "UNMAPPED_COMPARISON", "POOLED_ACROSS_DATASETS"] as const;
const POSITIONS = ["validation cohort", "validation view", "count", "run result"] as const;

/** The builder's answers with a caveat added at each position, of the code `codeAt` gives it. */
function withCaveats(codeAt: (position: (typeof POSITIONS)[number]) => string): (sent: Sent) => Response {
  const caveat = (position: (typeof POSITIONS)[number]): Record<string, unknown> => {
    const code = codeAt(position);
    return { code, severity: "warn", message: [{ text: "about " }, { data: code.toLowerCase() }], affects: position === "count" ? ["/population"] : [] };
  };
  return (sent) => {
    // The server's answer to this very request (or the cell fails), with the caveat added.
    byRequest(sent);
    if (sent.tool === "validate_document") {
      const body = JSON.parse(ANSWERS.validated.body) as { cohorts: { caveats: unknown[] }[]; views: { caveats: unknown[] }[] };
      body.cohorts[1]?.caveats.push(caveat("validation cohort"));
      body.views[0]?.caveats.push(caveat("validation view"));
      return json(JSON.stringify(body));
    }
    if (sent.tool === "count_cohort") {
      const body = JSON.parse(ANSWERS.counted.body) as { counts: { count: { caveats: unknown[] } }[] };
      body.counts[1]?.count.caveats.push(caveat("count"));
      return json(JSON.stringify(body));
    }
    const body = JSON.parse(ANSWERS.ran.body) as { results: { caveats: unknown[] }[] };
    body.results[0]?.caveats.push(caveat("run result"));
    return json(JSON.stringify(body));
  };
}

describe("Builder", () => {
  it("R41: the readback is shown beside each cohort and view, segment by segment, as the server wrote it", async () => {
    expect.hasAssertions();
    expect(typeof Builder).toBe("function");
    await withPage(byRequest, async (page) => {
      await fillBuilt(page);
      await page.press("Validate");
      const answer = JSON.parse(ANSWERS.validated.body) as { cohorts: { readback: { text?: string; data?: string }[] }[]; views: { readback: { text?: string; data?: string }[] }[] };
      const sections = [...(page.container.querySelector('section.outcome[aria-label="validation"]')?.querySelectorAll("section.checked") ?? [])];
      expect(sections.map((one) => one.getAttribute("aria-label"))).toEqual(["cohort", "cohort", "view"]);
      const readbacks = [...answer.cohorts, ...answer.views].map((one) => one.readback.map((segment) => segment.text ?? segment.data ?? "").join(""));
      expect(sections.map((one) => one.querySelectorAll("p.readback").length)).toEqual([1, 1, 1]);
      expect(sections.map((one) => one.querySelector("p.readback")?.textContent)).toEqual(readbacks);
      expect([...(sections[0]?.querySelectorAll("p.readback bdi.token") ?? [])].map((one) => one.textContent)).toEqual(["trees", "orchard", "1", "variety", "apple"]);
    });
  });

  it("R49a: the overlap and cross-dataset caveats are shown wherever the builder's answers carry them, with code, severity and message", async () => {
    expect.hasAssertions();
    // Four rounds, each position a different code in each (a Latin square): every code at every position.
    for (let round = 0; round < CODES.length; round += 1) {
      const codeAt = (position: (typeof POSITIONS)[number]): string => CODES[(POSITIONS.indexOf(position) + round) % CODES.length] ?? "?";
      await withPage(withCaveats(codeAt), async (page) => {
        await fillBuilt(page);
        await page.press("Validate");
        await page.press("Count");
        await page.press("Run");
        for (const position of POSITIONS) {
          const code = codeAt(position);
          const sections = [...page.container.querySelectorAll("section.checked")].filter((one) => textOf(one).includes(code));
          expect(sections.length, `${position} ${code}`).toBe(1);
          const label = { "validation cohort": "cohort", "validation view": "view", count: "count", "run result": "result" }[position];
          expect(sections[0]?.getAttribute("aria-label"), `${position} ${code}`).toBe(label);
          expect(textOf(sections[0]), `${position} ${code}`).toContain(`${code} (warning): about ${code.toLowerCase()}`);
        }
      });
    }
  });
});
