/**
 * The tab's one queue (D431, `src/catalogue/load.ts`): one call a store, at most two outstanding,
 * first in first out, a store with no subscriber at its turn dropped unsent and queued again when
 * subscribed again, the 45-second clock from when a store is queued, a late answer discarded, the
 * clock stopped whenever a store settles, expires or is dropped (`vi.getTimerCount()`: M-d's
 * killer), and the module's two timer calls as the source cell holds them (D429's `SITES` match
 * the identifier; this cell holds the call's text). The queue is module state: every cell imports
 * a fresh module (`vi.resetModules()`), so that no cell's unsettled calls starve another's.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { createElement, type ReactElement, StrictMode, useState, useSyncExternalStore } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "./render";

type Module = typeof import("../../src/catalogue/load");

async function fresh(): Promise<Module> {
  vi.resetModules();
  return import("../../src/catalogue/load");
}

/** A call the test settles: how many times it was called, and its resolve and reject. */
interface Held<T> {
  readonly call: () => Promise<T>;
  calls: number;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function held<T>(): Held<T> {
  const made: Held<T> = {
    calls: 0,
    resolve: () => undefined,
    reject: () => undefined,
    call: () => {
      made.calls += 1;
      return new Promise<T>((resolve, reject) => {
        made.resolve = resolve;
        made.reject = reject;
      });
    },
  };
  return made;
}

/** Let the settled promises' callbacks run. */
async function flush(): Promise<void> {
  for (let step = 0; step < 5; step += 1) {
    await Promise.resolve();
  }
}

describe("the tab's one queue", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends a store's call on its first subscribe and never twice (subscribe, unsubscribe, subscribe)", async () => {
    const { loader } = await fresh();
    const one = held<string>();
    const store = loader(one.call);
    expect(one.calls).toBe(0);
    const off = store.subscribe(() => undefined);
    off();
    store.subscribe(() => undefined);
    expect(one.calls).toBe(1);
    one.resolve("a");
    await flush();
    expect(store.snapshot()).toEqual({ kind: "done", value: "a" });
    expect(one.calls).toBe(1);
  });

  it("holds at most two calls outstanding: a third waits, first in first out, until one settles", async () => {
    const { loader, OUTSTANDING } = await fresh();
    expect(OUTSTANDING).toBe(2);
    const calls = [held<number>(), held<number>(), held<number>(), held<number>()];
    const stores = calls.map((one) => loader(one.call));
    for (const store of stores) {
      store.subscribe(() => undefined);
    }
    expect(calls.map((one) => one.calls)).toEqual([1, 1, 0, 0]);
    calls[1]?.resolve(1);
    await flush();
    expect(calls.map((one) => one.calls)).toEqual([1, 1, 1, 0]);
    calls[0]?.reject(new Error("x"));
    await flush();
    expect(calls.map((one) => one.calls)).toEqual([1, 1, 1, 1]);
    expect(stores[0]?.snapshot()).toEqual({ kind: "failed" });
    expect(stores[1]?.snapshot()).toEqual({ kind: "done", value: 1 });
  });

  it("drops a queued store with no subscriber at its turn, unsent, and queues it again when subscribed again", async () => {
    const { loader } = await fresh();
    const [a, b, c] = [held<string>(), held<string>(), held<string>()];
    loader(a.call).subscribe(() => undefined);
    loader(b.call).subscribe(() => undefined);
    const queued = loader(c.call);
    const off = queued.subscribe(() => undefined);
    off();
    expect(vi.getTimerCount()).toBe(3);
    a.resolve("a");
    await flush();
    expect(c.calls).toBe(0);
    expect(queued.snapshot()).toEqual({ kind: "pending" });
    // a settled and c dropped: only b's clock runs (n3).
    expect(vi.getTimerCount()).toBe(1);
    queued.subscribe(() => undefined);
    expect(c.calls).toBe(1);
    expect(vi.getTimerCount()).toBe(2);
    c.resolve("c");
    b.resolve("b");
    await flush();
    expect(queued.snapshot()).toEqual({ kind: "done", value: "c" });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("starts a store's clock when it is queued: behind two unsettled calls it shows the deadline 45 s after queueing, never sent", async () => {
    const { loader, DEADLINE_MS } = await fresh();
    expect(DEADLINE_MS).toBe(45_000);
    const [a, b, c] = [held<string>(), held<string>(), held<string>()];
    loader(a.call).subscribe(() => undefined);
    loader(b.call).subscribe(() => undefined);
    await vi.advanceTimersByTimeAsync(10_000);
    const late = loader(c.call);
    late.subscribe(() => undefined);
    await vi.advanceTimersByTimeAsync(44_999);
    expect(late.snapshot()).toEqual({ kind: "pending" });
    await vi.advanceTimersByTimeAsync(1);
    expect(late.snapshot()).toEqual({ kind: "deadline" });
    expect(vi.getTimerCount()).toBe(0);
    a.resolve("a");
    b.resolve("b");
    await flush();
    expect(c.calls).toBe(0);
  });

  it("shows the deadline for an unsettled call at 45 s, discards its late answer, and counts it until it settles, then moves on", async () => {
    const { loader } = await fresh();
    const [a, b, c] = [held<string>(), held<string>(), held<string>()];
    const first = loader(a.call);
    first.subscribe(() => undefined);
    loader(b.call).subscribe(() => undefined);
    await vi.advanceTimersByTimeAsync(45_000);
    expect(first.snapshot()).toEqual({ kind: "deadline" });
    expect(vi.getTimerCount()).toBe(0);
    const third = loader(c.call);
    third.subscribe(() => undefined);
    expect(c.calls).toBe(0);
    a.resolve("late");
    await flush();
    expect(first.snapshot()).toEqual({ kind: "deadline" });
    expect(c.calls).toBe(1);
    c.resolve("c");
    await flush();
    expect(third.snapshot()).toEqual({ kind: "done", value: "c" });
  });

  it("keeps an answer that settles at 10 s when the clock passes 45 s, its clock stopped on settling (M-d)", async () => {
    const { loader } = await fresh();
    const one = held<string>();
    const store = loader(one.call);
    store.subscribe(() => undefined);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(10_000);
    one.resolve("kept");
    await flush();
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(store.snapshot()).toEqual({ kind: "done", value: "kept" });
  });

  it("notifies a listener after the state is set, once", async () => {
    const { loader } = await fresh();
    const one = held<string>();
    const store = loader(one.call);
    const seen: unknown[] = [];
    store.subscribe(() => seen.push(store.snapshot()));
    one.resolve("v");
    await flush();
    expect(seen).toEqual([{ kind: "done", value: "v" }]);
  });

  it("shows a call that throws as failed and frees its place", async () => {
    const { loader } = await fresh();
    const thrown = loader((): Promise<string> => {
      throw new Error("secret");
    });
    thrown.subscribe(() => undefined);
    expect(thrown.snapshot()).toEqual({ kind: "failed" });
    const [a, b] = [held<string>(), held<string>()];
    loader(a.call).subscribe(() => undefined);
    loader(b.call).subscribe(() => undefined);
    expect([a.calls, b.calls]).toEqual([1, 1]);
    expect(vi.getTimerCount()).toBe(2);
  });
});

describe("a store under React", () => {
  it("is sent once a mount under StrictMode", async () => {
    const { loader } = await fresh();
    const one = held<string>();
    function Probe(): ReactElement {
      const [store] = useState(() => loader(one.call));
      const loaded = useSyncExternalStore(store.subscribe, store.snapshot);
      return createElement("p", null, loaded.kind);
    }
    const shown = await render(createElement(StrictMode, null, createElement(Probe)));
    expect(shown.words()).toBe("pending");
    expect(one.calls).toBe(1);
    one.resolve("x");
    expect(await shown.until((words) => words === "done")).toBe("done");
    expect(one.calls).toBe(1);
    shown.unmount();
  });
});

describe("the module's two timer calls (D429's SITES match the identifier; this cell holds the call)", () => {
  const source = readFileSync(path.join(import.meta.dirname, "../../src/catalogue/load.ts"), "utf8");
  const code = source.replace(/\/\*[\s\S]*?\*\//gu, "").replace(/\/\/.*$/gmu, "");

  it("holds one setTimeout, setTimeout(expire, DEADLINE_MS), and one clearTimeout, clearTimeout(timer)", () => {
    expect([...code.matchAll(/\bsetTimeout\b[^;]*/gu)].map((found) => found[0])).toEqual(["setTimeout(expire, DEADLINE_MS)"]);
    expect([...code.matchAll(/\bclearTimeout\b[^;]*/gu)].map((found) => found[0])).toEqual(["clearTimeout(timer)"]);
    expect(code).not.toMatch(/\b(?:setInterval|queueMicrotask|requestAnimationFrame|requestIdleCallback)\b/u);
  });

  it("refuses the control: a timer with an extra argument, or a second one", () => {
    const held_ = (text: string): string[] => [...text.matchAll(/\bsetTimeout\b[^;]*/gu)].map((found) => found[0]);
    expect(held_("timer = setTimeout(expire, DEADLINE_MS, words);")).not.toEqual(["setTimeout(expire, DEADLINE_MS)"]);
    expect(held_("setTimeout(expire, DEADLINE_MS); setTimeout(expire, DEADLINE_MS);")).not.toEqual(["setTimeout(expire, DEADLINE_MS)"]);
  });
});
