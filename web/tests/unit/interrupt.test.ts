/**
 * The interrupt guard's listeners (`e2e/interrupt.mjs`, D423): set up once for the run's `exit`,
 * SIGTERM and SIGHUP, and all removed by `release()`, so that watch and UI modes, which set up and
 * tear down again and again, leave none behind. (Signals are never sent here: the guard's effect on
 * a process that is signalled is `selftest.spec.ts`'s, in a process of its own.)
 */
import { describe, expect, it } from "vitest";

import { guardInterrupts } from "../../e2e/interrupt.mjs";

const EVENTS = ["exit", "SIGTERM", "SIGHUP"] as const;
const counts = (): number[] => EVENTS.map((name) => process.listenerCount(name));

const held = {
  secrets: "/nonexistent/aibi-interrupt-unit-secrets",
  scratch: "/nonexistent/aibi-interrupt-unit-scratch",
  running: () => [],
  teardown: () => Promise.resolve(),
  keepScratch: () => false,
};

describe("the interrupt guard's listeners", () => {
  it("adds one each for exit, SIGTERM and SIGHUP, and release removes them all", () => {
    const before = counts();
    const guard = guardInterrupts(held);
    expect(counts()).toEqual(before.map((count) => count + 1));
    expect(guard.closing()).toBe(false);
    guard.release();
    expect(counts()).toEqual(before);
  });

  it("leaves none behind over many set-ups and releases (a watch mode)", () => {
    const before = counts();
    for (let round = 0; round < 25; round += 1) {
      guardInterrupts(held).release();
    }
    expect(counts()).toEqual(before);
  });

  it("is two guards' own listeners, each released alone", () => {
    const before = counts();
    const first = guardInterrupts(held);
    const second = guardInterrupts(held);
    expect(counts()).toEqual(before.map((count) => count + 2));
    first.release();
    expect(counts()).toEqual(before.map((count) => count + 1));
    second.release();
    expect(counts()).toEqual(before);
  });
});
