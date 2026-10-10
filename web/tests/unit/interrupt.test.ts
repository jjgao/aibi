/**
 * The interrupt guard's listeners (`e2e/interrupt.mjs`, D423): set up once for the run's `exit`,
 * SIGTERM and SIGHUP, and all removed by `release()`, so that watch and UI modes, which set up and
 * tear down again and again, leave none behind. (Signals are never sent here: the guard's effect on
 * a process that is signalled is `selftest.spec.ts`'s, in a process of its own.)
 */
import { describe, expect, it } from "vitest";

import { guardInterrupts, INTERRUPT_ROUNDS, runPendingReleases } from "../../e2e/interrupt.mjs";
import { guardRun } from "../../e2e/run-guard.mjs";

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

describe("the guard held for the scan reporter", () => {
  it("keeps its listeners when the scan reads the token's directory after the teardown, and releases them when the reporter has finished", () => {
    const before = counts();
    const guard = guardInterrupts(held);
    const sigint = process.listenerCount("SIGINT");
    guard.releaseWhenScanned(true);
    expect(counts()).toEqual(before.map((count) => count + 1));
    expect(process.listenerCount("SIGINT")).toBe(sigint + 1);
    runPendingReleases();
    expect(counts()).toEqual(before);
    expect(process.listenerCount("SIGINT")).toBe(sigint);
    runPendingReleases();
    expect(counts()).toEqual(before);
  });

  it("releases at once when no reporter reads it", () => {
    const before = counts();
    guardInterrupts(held).releaseWhenScanned(false);
    expect(counts()).toEqual(before);
    runPendingReleases();
    expect(counts()).toEqual(before);
  });

  it("holds two guards' releases apart, all run by the reporter's one call", () => {
    const before = counts();
    guardInterrupts(held).releaseWhenScanned(true);
    guardInterrupts(held).releaseWhenScanned(true);
    expect(counts()).toEqual(before.map((count) => count + 2));
    runPendingReleases();
    expect(counts()).toEqual(before);
  });

  it("bounds an interrupt's rounds of teardown", () => {
    expect(INTERRUPT_ROUNDS).toBe(5);
  });
});

describe("how a run holds the guard (guardRun)", () => {
  const made = (scanning: boolean, calls: boolean[] = []) => {
    const children: never[] = [];
    return guardRun({
      scratch: "/nonexistent/aibi-run-guard-scratch",
      secrets: "/nonexistent/aibi-run-guard-secrets",
      children,
      teardown: (keep) => {
        calls.push(keep === true);
        return Promise.resolve();
      },
      scanning: () => scanning,
      keepAsked: () => false,
    });
  };

  it("finish stops the servers once, and releases at once without a reporter", async () => {
    const before = counts();
    const calls: boolean[] = [];
    const run = made(false, calls);
    expect(counts()).toEqual(before.map((count) => count + 1));
    await run.finish();
    expect(calls).toEqual([false]);
    expect(counts()).toEqual(before);
  });

  it("finish keeps the guard for the reporter, which releases it", async () => {
    const before = counts();
    const run = made(true);
    await run.finish();
    expect(counts()).toEqual(before.map((count) => count + 1));
    runPendingReleases();
    expect(counts()).toEqual(before);
  });

  it("a failed start stops the servers and keeps the scratch directory (an interrupt aside), and releases", async () => {
    const before = counts();
    const calls: boolean[] = [];
    const run = made(false, calls);
    await run.failed();
    expect(calls).toEqual([true]);
    expect(counts()).toEqual(before);
  });
});
