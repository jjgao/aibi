/**
 * The dataset's loader (`Dataset`; D431) in each state, through a stubbed `fetch` and the real seam:
 * loading (fixed words that never name the address's id, which goes into the request's body alone),
 * an answer, refused (the server's 404, echoing nothing of the address), invalid, the deadline and a
 * load that throws, never an error's message. The queue is module state: each cell imports a fresh
 * module.
 */
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { idOfAddress } from "../../src/api/text";
import { SHAPE_MISMATCH, UNREACHABLE } from "../../src/api/typed";
import { DEADLINE } from "../../src/catalogue/load";
import { FAILED, PENDING, REFUSED } from "../../src/dataset/Dataset";
import { render, type Rendered } from "./render";
import { json, orchard, serving } from "./screens";

type Screen = typeof import("../../src/dataset/Dataset");

async function fresh(): Promise<Screen> {
  vi.resetModules();
  return import("../../src/dataset/Dataset");
}

let shown: Rendered | undefined;

afterEach(() => {
  shown?.unmount();
  shown = undefined;
  vi.unstubAllGlobals();
  vi.doUnmock("../../src/api/typed");
  vi.useRealTimers();
});

const ID = idOfAddress("orchard");

function id(): NonNullable<typeof ID> {
  if (ID === null) {
    throw new Error("orchard is an id");
  }
  return ID;
}

describe("Dataset (the loader), in each state", () => {
  it("pending: fixed words, never the address's id", async () => {
    const { Dataset } = await fresh();
    const sent = serving(() => new Promise<Response>(() => undefined));
    shown = await render(<Dataset id={id()} />);
    expect(shown.words()).toBe(PENDING);
    expect(shown.container.innerHTML).not.toContain("orchard");
    expect(sent.map((one) => [one.tool, one.body])).toEqual([["describe_dataset", '{"dataset":"orchard"}']]);
  });

  it("an answer: the description", async () => {
    const { Dataset } = await fresh();
    serving(() => orchard("describe_dataset"));
    shown = await render(<Dataset id={id()} />);
    expect(await shown.until((words) => words.startsWith("Dataset orchard"))).toContain("Applicable analyses");
  });

  it("refused (the 404): fixed words and the refusal, as data, echoing nothing of the address", async () => {
    const { Dataset } = await fresh();
    serving(() => orchard("describe_dataset", 404));
    shown = await render(<Dataset id={id()} />);
    await shown.until((words) => words.includes(REFUSED));
    expect(shown.container.textContent).toBe(`${REFUSED}The request was refused.UNKNOWN_DATASET at /dataset: No release of the dataset was published; these datasets have oneAvailable (not applied): orchard`);
  });

  it.each([
    ["a shape the page does not read", () => json('{"dataset": 3}'), SHAPE_MISMATCH],
    ["no answer", () => Promise.reject(new TypeError("network <b>secret</b>")), UNREACHABLE],
  ])("invalid, %s: fixed words", async (_name, answer, words) => {
    const { Dataset } = await fresh();
    serving(answer);
    shown = await render(<Dataset id={id()} />);
    expect(await shown.until((found) => found.includes(words))).toBe(words);
    expect(shown.container.innerHTML).not.toContain("secret");
  });

  it("deadline: its own words 45 s after it was queued", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { Dataset } = await fresh();
    serving(() => new Promise<Response>(() => undefined));
    shown = await render(<Dataset id={id()} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });
    expect(shown.words()).toBe(DEADLINE);
  });

  it("failed: fixed words, never the error's message", async () => {
    vi.resetModules();
    vi.doMock("../../src/api/typed", async (original) => ({ ...(await original<object>()), describeDataset: () => Promise.reject(new Error("secret \u202e from data")) }));
    const { Dataset } = await import("../../src/dataset/Dataset");
    shown = await render(<Dataset id={id()} />);
    expect(await shown.until((found) => found.includes(FAILED))).toBe(FAILED);
    expect(shown.container.innerHTML).not.toContain("secret");
  });
});
