/**
 * The column's loader (`Column`; D443) in each state, through a stubbed `fetch` and the real seam
 * (the route hands it the call and the dataset's id, never the column's): pending, an answer, refused,
 * invalid, the deadline and a call that throws: fixed words, never the error's message, and the
 * address's column (a sentinel) never in the page. The queue is module state: each cell imports a
 * fresh module graph. (Apart from `column.test.tsx`, R67's file, which names no hook: D427.)
 */
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { columnOfAddress, idOfAddress } from "../../src/api/text";
import { type describeColumn, SHAPE_MISMATCH, UNREACHABLE } from "../../src/api/typed";
import { DEADLINE } from "../../src/catalogue/load";
import { BACK, FAILED, NOT_SHOWN, PENDING, REFUSED } from "../../src/dataset/Column";
import { render, type Rendered } from "./render";
import { json, serving } from "./screens";
import { answerAt, ORCHARD } from "./typed-support";

/** The recorded answer of `trees.variety`, the columns section's first. */
const VARIETY = ORCHARD[answerAt("columns", 0)]?.body ?? "";

describe("Column (the loader), in each state: the route hands it the call and the dataset, never the column's id", () => {
  type Screen = typeof import("../../src/dataset/Column");

  let shownNow: Rendered | undefined;

  afterEach(() => {
    shownNow?.unmount();
    shownNow = undefined;
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  /** A fresh module graph (the queue is module state): the screen and the seam's own sender. */
  async function fresh(): Promise<Screen & { readonly describeColumn: typeof describeColumn }> {
    vi.resetModules();
    const screen = await import("../../src/dataset/Column");
    const seam = await import("../../src/api/typed");
    return { ...screen, describeColumn: seam.describeColumn };
  }

  const DATASET = idOfAddress("orchard");
  const SENTINEL = columnOfAddress("trees.zz_sentinel");

  function ids(): { readonly dataset: NonNullable<typeof DATASET>; readonly column: NonNullable<typeof SENTINEL> } {
    if (DATASET === null || SENTINEL === null) {
      throw new Error("both are ids");
    }
    return { dataset: DATASET, column: SENTINEL };
  }

  /** What the page holds, text and attributes, that could echo the address's column. */
  const echoes = (container: Element): boolean => container.innerHTML.includes("zz_sentinel");

  it("pending: fixed words and the link back, one call with the address's ids, the column never shown", async () => {
    const { Column, describeColumn } = await fresh();
    const sent = serving(() => new Promise<Response>(() => undefined));
    const { dataset, column } = ids();
    shownNow = await render(<Column dataset={dataset} describe={() => describeColumn({ dataset, column })} />);
    expect(shownNow.words()).toBe(`${PENDING} ${BACK}`);
    expect(shownNow.container.querySelector("a")?.getAttribute("href")).toBe("#/datasets/orchard");
    expect(sent.map((one) => [one.tool, one.body])).toEqual([["describe_column", '{"dataset":"orchard","column":"trees.zz_sentinel"}']]);
    expect(echoes(shownNow.container)).toBe(false);
  });

  it.each([
    ["an answer (of another column: the heading is the answer's)", () => json(VARIETY), "Column trees.variety ( variety )"],
    ["refused (the server's 422, echoing nothing of the request)", () => json('{"refusals":[{"code":"UNKNOWN_COLUMN","path":"/column","message":[{"text":"No such column; the table has these"}],"alternatives":[{"data":"trees.variety"}]}]}', 422), REFUSED],
    ["a shape the page does not read", () => json('{"column": 3}'), SHAPE_MISMATCH],
    ["no answer", () => Promise.reject(new TypeError("network zz_sentinel <b>secret</b>")), UNREACHABLE],
  ])("%s: its words, the column never echoed", async (_name, answer, words) => {
    const { Column, describeColumn } = await fresh();
    serving(answer);
    const { dataset, column } = ids();
    shownNow = await render(<Column dataset={dataset} describe={() => describeColumn({ dataset, column })} />);
    expect(await shownNow.until((found) => found.includes(words))).toContain(words);
    expect(echoes(shownNow.container)).toBe(false);
    expect(shownNow.container.innerHTML).not.toContain("secret");
    // The notice is the answer's: a refusal or a failure shows none.
    expect(shownNow.container.textContent.includes(NOT_SHOWN)).toBe(words.startsWith("Column "));
  });

  it("a refusal shows the refusal, as the server's data, and no notice", async () => {
    const { Column, describeColumn } = await fresh();
    serving(() => json('{"refusals":[{"code":"UNKNOWN_COLUMN","path":"/column","message":[{"text":"No such column"}],"alternatives":[{"data":"trees.variety"}]}]}', 422));
    const { dataset, column } = ids();
    shownNow = await render(<Column dataset={dataset} describe={() => describeColumn({ dataset, column })} />);
    await shownNow.until((found) => found.includes(REFUSED));
    expect(shownNow.container.querySelectorAll(".refusal")).toHaveLength(1);
    expect(shownNow.words()).toContain("UNKNOWN_COLUMN");
    expect(shownNow.words()).not.toContain(NOT_SHOWN);
  });

  it("deadline: its own words 45 s after it was queued", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { Column, describeColumn } = await fresh();
    serving(() => new Promise<Response>(() => undefined));
    const { dataset, column } = ids();
    shownNow = await render(<Column dataset={dataset} describe={() => describeColumn({ dataset, column })} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });
    expect(shownNow.words()).toBe(`${DEADLINE} ${BACK}`);
    expect(echoes(shownNow.container)).toBe(false);
  });

  it("failed: fixed words, never the error's message", async () => {
    const { Column } = await fresh();
    const { dataset } = ids();
    shownNow = await render(<Column dataset={dataset} describe={() => Promise.reject(new Error("secret zz_sentinel ‮"))} />);
    expect(await shownNow.until((found) => found.includes(FAILED))).toBe(`${FAILED} ${BACK}`);
    expect(echoes(shownNow.container)).toBe(false);
    expect(shownNow.container.innerHTML).not.toContain("secret");
  });
});
