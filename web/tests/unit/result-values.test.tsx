/**
 * One position's values (D435; D427's R34a; §8.1, §9.1, D434), through the real seam, on the live
 * answers and on bodies changed from them: the statistics and the histogram with no disclosure
 * setting, the deferral words for a number column under one or with a bin index (R34b), the
 * categories, the rows that name none, another kind, and the reads the page did not make.
 */
import { describe, expect, it, vi } from "vitest";

import { VALUE_WORDS } from "../../src/result/Values";
import { changed, type Json, LIVE, list, liveBody, object, shownOf, words } from "./result-support";

// This file's cells render whole results under the suite's load (D435): its own time per cell, as a
// row's test takes no options of its own (D427); the unit project's default stays 5 s.
vi.setConfig({ testTimeout: 20_000 });

/** The columns of a body's first result, shown, in order. */
async function columnsOf(body: Json): Promise<Element[]> {
  return [...(await shownOf(body)).querySelectorAll('section[aria-label="column"]')];
}

/** The raw columns of a body's first result, by position. */
const rawColumns = (body: Json): Json[][] => list(object(object(list(body["results"])[0])["values"])["positions"]).map((position) => list(object(position)["columns"]).map(object));

describe("one position's values (D435)", () => {
  it("R34a: values are read by name, each column by its kind under its label; a number column under the disclosure setting, or with a bin index, is shown as not shown yet; a read the page did not make is said", async () => {
    expect.hasAssertions();
    // (a): no setting. Each statistic under its member's name, as sent; the histogram's edges and counts as sent.
    const built = liveBody(LIVE.a);
    const shown = await columnsOf(built);
    const raw = rawColumns(built).flat();
    expect(shown).toHaveLength(raw.length);
    for (const [at, column] of raw.entries()) {
      const text = words(shown[at] ?? document.createElement("p"));
      if (column["kind"] === "numbers") {
        expect(text.startsWith("trees.height_m n ")).toBe(true);
        for (const name of ["n", "mean", "sd", "median", "q1", "q3", "min", "max"]) {
          expect(text).toContain(`${name} ${JSON.stringify(column[name])}`);
        }
        const histogram = object(column["histogram"]);
        expect(text).toContain(`histogram: edges from ${String(histogram["edges_from"])}`);
        const bins = list(histogram["bins"]).map(object);
        expect(shown[at]?.querySelectorAll('[role="listitem"]').length).toBe(bins.length);
        expect(text).toContain(`low ${VALUE_WORDS.noLow} , high ${JSON.stringify(bins[0]?.["high"])} , includes_low false, includes_high false, count ${JSON.stringify(bins[0]?.["count"])}`);
        expect(text).toContain(`high ${VALUE_WORDS.noHigh} ,`);
      } else {
        expect(text.startsWith("trees.variety ")).toBe(true);
      }
    }
    // (c), an empty cohort: every number null with its reason, no histogram, no category row.
    const empty = words((await columnsOf(liveBody(LIVE.c)))[0] ?? document.createElement("p"));
    expect(empty).toContain("n 0 ; mean not estimable: no units ; sd not estimable: no units");
    expect(empty).toContain("histogram not estimable: no units");
    expect(words((await columnsOf(liveBody(LIVE.c)))[1] ?? document.createElement("p"))).toContain(VALUE_WORDS.empty);
    // (h) and (h'): every number column under the floor shows the deferral words and no number.
    for (const index of [LIVE.h, LIVE.hPrime]) {
      const body = liveBody(index);
      const columns = await columnsOf(body);
      const kinds = rawColumns(body).flat().map((column) => column["kind"]);
      expect(kinds.filter((kind) => kind === "numbers")).toHaveLength(2);
      for (const [at, kind] of kinds.entries()) {
        if (kind === "numbers") {
          expect(words(columns[at] ?? document.createElement("p"))).toBe(`trees.height_m ${VALUE_WORDS.deferred}`);
          expect(columns[at]?.querySelectorAll("bdi.number")).toHaveLength(0);
        }
      }
    }
    // A bin index with no setting is deferred too (the predicate's second half).
    const binned = changed(LIVE.a, (result) => {
      object(list(object(list(object(result["values"])["positions"])[0])["columns"])[0])["median_bin"] = null;
    });
    expect(words((await columnsOf(binned))[0] ?? document.createElement("p"))).toBe(`trees.height_m ${VALUE_WORDS.deferred}`);
    expect(words((await columnsOf(binned))[2] ?? document.createElement("p"))).toContain("trees.height_m n 35");
    // (f), rows of another table counted: another kind, as a data token in fixed words.
    expect(words((await columnsOf(liveBody(LIVE.f)))[0] ?? document.createElement("p"))).toContain(`${VALUE_WORDS.other} category_rows`);
    // The reads the page did not make: another analysis (summary.members) and another shape.
    expect(words(await shownOf(liveBody(7)))).toContain(VALUE_WORDS.analysis);
    const misshapen = changed(LIVE.a, (result) => {
      object(list(object(list(object(result["values"])["positions"])[0])["columns"])[0])["meen"] = 1;
    });
    const words_ = words(await shownOf(misshapen));
    expect(words_).toContain(VALUE_WORDS.shape);
    expect(await columnsOf(misshapen)).toHaveLength(0);
  });

  it("a categories arm: each row's values as sent, other_values as the member's name, a row that names no category in fail-closed words with nothing of its proportion", async () => {
    // (d), floor 5: each position's one row is an other_values row; the empty cohort's names no value.
    const floored = liveBody(LIVE.d);
    const rows = rawColumns(floored).flat().flatMap((column) => list(column["categories"]).map(object));
    expect(rows.map((row) => [row["other_values"], list(row["values"]).length])).toEqual([
      [true, 0],
      [true, 0],
    ]);
    const columns = (await columnsOf(floored)).map(words);
    expect(columns[0]).toContain("trees.variety other_values : numerator 13 ; denominator 13 ; estimate 1");
    expect(columns[1]).toContain("trees.variety other_values : numerator 0 ; denominator 0 ; estimate not estimable: no units");
    // Values listed beside other_values, as sent, with no word on them.
    const listed = changed(LIVE.d, (result) => {
      object(list(object(list(object(list(object(result["values"])["positions"])[0])["columns"])[0])["categories"])[0])["values"] = [{ data: "apple" }, { data: "pear" }];
    });
    expect((await columnsOf(listed)).map(words)[0]).toContain("trees.variety other_values , apple , pear : numerator 13");
    // A value from data is a text, cut at 2,000 code points and marked (R01): the hostile values' (g)
    // 5,000 x's are cut once, and its override shown as an escape.
    const hostile = await columnsOf(liveBody(LIVE.g));
    expect(hostile.flatMap((column) => [...column.querySelectorAll(".category bdi.data .cut")])).toHaveLength(1);
    expect(hostile.flatMap((column) => [...column.querySelectorAll(".category bdi.data .escape")]).length).toBeGreaterThan(0);
    // A row that names no category and is no other_values row: fail-closed words, no proportion.
    const unnamed = changed(LIVE.d, (result) => {
      delete object(list(object(list(object(list(object(result["values"])["positions"])[1])["columns"])[0])["categories"])[0])["other_values"];
    });
    const shown = (await columnsOf(unnamed)).map(words);
    expect(shown[1]).toBe(`trees.variety ${VALUE_WORDS.unnamed}`);
  });
});
