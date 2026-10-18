/**
 * The result screen's frame (D435; D427's R10, R33, R37a and R49b): `Result`, through the real seam
 * (`result-support.tsx`), on the server's live answers and on bodies changed from them.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { RunOf } from "../../src/builder/Builder";
import { FOR_INSPECTION } from "../../src/layout/Caveat";
import { RESULT_WORDS, Result } from "../../src/result/Figures";
import { VALUE_WORDS } from "../../src/result/Values";
import { changed, type Json, LIVE, list, liveBody, object, ran, RUNS, shownOf, words } from "./result-support";

// This file's cells render whole results under the suite's load (D435): its own time per cell, as a
// row's test takes no options of its own (D427); the unit project's default stays 5 s.
vi.setConfig({ testTimeout: 20_000 });

/** The words of a list of segments, as the page shows them (each segment a text node of its own). */
const segmentWords = (segments: unknown): string =>
  list(segments)
    .map((segment) => String(object(segment)["text"] ?? object(segment)["data"]))
    .join(" ")
    .replace(/\s+/gu, " ")
    .trim();

const squash = (text: string): string => text.replace(/\s+/gu, "");

describe("the result's frame (D435)", () => {
  it("R10: each position's readback is shown in that position's section, beside its figures, and the view's in the view's section, on every live answer", async () => {
    expect.hasAssertions();
    let positions = 0;
    for (const [, body] of RUNS) {
      const shown = await shownOf(body);
      const results = list((JSON.parse(body) as Json)["results"]).map(object);
      const sections = [...shown.querySelectorAll('section[aria-label="result"]')];
      expect(sections).toHaveLength(results.length);
      for (const [at, result] of results.entries()) {
        const section = sections[at];
        const view = section?.querySelector('section[aria-label="view"]');
        expect(squash(view === null || view === undefined ? "" : words(view))).toContain(squash(segmentWords(object(result["readback"])["view"])));
        const own = [...(section?.querySelectorAll('section[aria-label="position"]') ?? [])];
        const readbacks = list(object(result["readback"])["cohorts"]);
        expect(own).toHaveLength(readbacks.length);
        for (const [index, readback] of readbacks.entries()) {
          const position = own[index];
          expect(squash(position === undefined ? "" : words(position))).toContain(squash(segmentWords(readback)));
          // Its figures are in the same section: its population.
          expect(position?.querySelector('[aria-label="population"]')).not.toBeNull();
          positions += 1;
        }
      }
    }
    expect(positions).toBeGreaterThan(20);
  });

  it("R33: each position's label is shown as data at its head, and the reference position is marked where the server marks it, there alone", async () => {
    expect.hasAssertions();
    const shown = await shownOf(liveBody(LIVE.a));
    const heads = [...shown.querySelectorAll('section[aria-label="position"] > h3')];
    expect(heads.map((head) => [head.querySelector("bdi.data")?.textContent, head.textContent])).toEqual([
      ["first", "first"],
      ["second", "second"],
    ]);
    expect(words(shown)).not.toContain(RESULT_WORDS.reference);
    const marked = await shownOf(
      changed(LIVE.a, (result) => {
        const cohorts = list(result["cohorts"]).map(object);
        if (cohorts[1] !== undefined) {
          cohorts[1]["reference"] = true;
        }
      }),
    );
    const sections = [...marked.querySelectorAll('section[aria-label="position"]')];
    expect(sections.map((section) => words(section).includes(RESULT_WORDS.reference))).toEqual([false, true]);
    // A missing entry of a per-position list is said, never dropped: a label fewer.
    const short = await shownOf(
      changed(LIVE.a, (result) => {
        list(result["labels"]).pop();
      }),
    );
    expect([...short.querySelectorAll('section[aria-label="position"] > h3')].map((head) => head.textContent)).toEqual(["first", RESULT_WORDS.label]);
  });

  it("R37a: every caveat of a result is shown at its head, before the view, with its code, its severity's words and its affects pointers as data, on every live answer", async () => {
    expect.hasAssertions();
    let caveats = 0;
    for (const [, body] of RUNS) {
      const shown = await shownOf(body);
      const results = list((JSON.parse(body) as Json)["results"]).map(object);
      for (const [at, result] of results.entries()) {
        const section = shown.querySelectorAll('section[aria-label="result"]')[at];
        const list_ = section?.querySelector('[aria-label="caveats"]');
        const items = [...(list_?.querySelectorAll('[role="listitem"]') ?? [])];
        const raw = list(result["caveats"]).map(object);
        expect(items).toHaveLength(raw.length);
        for (const [index, caveat] of raw.entries()) {
          const item = items[index];
          expect(item?.querySelector("bdi.data")?.textContent).toBe(caveat["code"]);
          const affects = list(caveat["affects"]).map(String);
          expect([...(item?.querySelectorAll(".affects bdi.data") ?? [])].map((one) => one.textContent)).toEqual(affects);
          caveats += 1;
        }
        if (raw.length > 0) {
          // At the head: the caveats come before the view's section.
          const view = section?.querySelector('section[aria-label="view"]');
          expect(list_ !== null && list_ !== undefined && view !== null && view !== undefined && (list_.compareDocumentPosition(view) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0).toBe(true);
        }
      }
    }
    expect(caveats).toBeGreaterThan(20);
    // A blocking caveat puts the whole result under the banner, for inspection (R22).
    const blocked = await shownOf(
      changed(LIVE.a, (result) => {
        result["caveats"] = [{ code: "BLOCKED_THING", severity: "block", message: [{ text: "blocked" }], affects: [] }];
      }),
    );
    const inspection = blocked.querySelector('section[aria-label="result"] > section.inspection');
    expect(inspection?.querySelector('p[role="alert"]')?.textContent).toBe(FOR_INSPECTION);
    expect(inspection?.querySelector('section[aria-label="provenance"]')).not.toBeNull();
  });

  it("R49b: the overlap and cross-dataset caveats are shown with the result, one cell per code, and a result whose values give none for the view says so", async () => {
    expect.hasAssertions();
    for (const code of ["COHORTS_OVERLAP", "CONFOUNDED_WITH_DATASET", "UNMAPPED_COMPARISON", "POOLED_ACROSS_DATASETS"]) {
      const shown = await shownOf(
        changed(LIVE.a, (result) => {
          result["caveats"] = [{ code, severity: code === "POOLED_ACROSS_DATASETS" ? "info" : "warn", message: [{ text: "zq message " }, { data: "zq token" }], affects: ["/values"] }];
        }),
      );
      const item = shown.querySelector('[aria-label="caveats"] [role="listitem"]');
      expect(item?.querySelector("bdi.data")?.textContent).toBe(code);
      expect(item?.textContent).toContain("zq token");
    }
    for (const [index, body] of RUNS) {
      const shown = await shownOf(body);
      const analyses = list((JSON.parse(body) as Json)["results"]).map((result) => object(object(object(result)["derivation"])["analysis"])["id"]);
      const noView = words(shown).split(RESULT_WORDS.noView).length - 1;
      expect([index, noView]).toEqual([index, analyses.filter((id) => id === "summary.distribution").length]);
      expect(words(shown).includes(VALUE_WORDS.analysis)).toBe(analyses.some((id) => id !== "summary.distribution"));
    }
  });
});

describe("the view's line (round 2's m4: a leaf shown in two places is held at each)", () => {
  it("names each result's own analysis and version, on every live answer", async () => {
    let checked = 0;
    for (const [, body] of RUNS) {
      const shown = await shownOf(body);
      const results = list((JSON.parse(body) as Json)["results"]).map(object);
      const sections = [...shown.querySelectorAll('section[aria-label="result"]')];
      for (const [at, result] of results.entries()) {
        const analysis = object(object(result["derivation"])["analysis"]);
        const line = sections[at]?.querySelector('section[aria-label="view"] > p');
        expect([...(line?.querySelectorAll("bdi.data") ?? [])].map((one) => one.textContent)).toEqual([analysis["id"], analysis["version"]]);
        expect(words(line ?? document.createElement("p"))).toBe(`Analysis ${String(analysis["id"])} version ${String(analysis["version"])}`);
        checked += 1;
      }
    }
    expect(checked).toBeGreaterThan(10);
  });
});

describe("one result's read meets its own envelope (B7.4, m3)", () => {
  it("a two-result answer shows each result as it shows the result alone", async () => {
    const first = object(list(liveBody(LIVE.a)["results"])[0]);
    const second = object(list(liveBody(LIVE.d)["results"])[0]);
    const both = liveBody(LIVE.a);
    both["results"] = [first, second];
    const answer = await ran(JSON.stringify(both));
    const alone = await Promise.all([LIVE.a, LIVE.d].map(async (index) => (await ran(JSON.stringify(liveBody(index)))).results[0]));
    const markupOf = (result: (typeof answer.results)[number] | undefined): string => (result === undefined ? "" : renderToStaticMarkup(<Result result={result} />));
    expect(answer.results.map(markupOf)).toEqual(alone.map(markupOf));
    expect(markupOf(answer.results[0])).not.toEqual(markupOf(answer.results[1]));
    // The run view shows each result as Result shows it alone, in order.
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(<RunOf value={answer} />);
    const parsed = (markup: string): string => {
      const one = document.createElement("div");
      one.innerHTML = markup;
      return one.innerHTML;
    };
    expect([...host.querySelectorAll('section[aria-label="result"]')].map((section) => section.innerHTML)).toEqual(alone.map((result) => parsed(markupOf(result))));
  });
});
