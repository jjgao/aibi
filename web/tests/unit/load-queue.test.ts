/**
 * The tab's one queue (`src/catalogue/load.ts`, D431) held against an independent model of D431's text
 * (rounds 1 and 2 of #123, m3 and r2-m4): random programs of create (one in ten calls throws
 * synchronously), subscribe, unsubscribe, settle (answer or throw) and the clock's advance, over the
 * real module under fake timers. The model is written here from the text alone (a queue in first-in,
 * first-out order; two calls outstanding at most, an overdue one counting until it settles; a call sent
 * only to a store with a subscriber at its turn, otherwise dropped unsent with its clock stopped and its
 * start forgotten; a clock of 45 s from the store's enqueue, whose expiry leaves the queue and discards
 * a later answer; a synchronous throw is a failed call that gives its place back), and after every step
 * the check holds the module to it by:
 *
 * - M1 the exact list of the calls made, in their order (so FIFO, two places, no call to a store
 *   without a subscriber at its turn, none to an expired store, none twice);
 * - M2 every store's snapshot kind, and a done snapshot's value (the deadline counts from the enqueue,
 *   a late answer is discarded, a synchronous throw is `failed`);
 * - M3 the exact number of timers running: one for each store whose clock is running in the model (so
 *   that a dropped, settled or expired store keeps none);
 * - M4 a settled snapshot never changes (the same object, each time).
 *
 * What it does not see: the listeners being told (`useSyncExternalStore` does, through React, in the
 * tests of the screens and `load.test.ts`), and any text (the module holds none).
 *
 * The check is held by controls: the same programs over a queue written in this file with one flaw
 * each, which the check must find: three places, an overdue call's place leaked (the call's settling
 * after its deadline does not give its place back), the pump stopping at an expired store or at a
 * dropped one instead of going on to the next, and a dropped store keeping its clock. The real module
 * and the copy without a flaw pass every seed of the same programs, so the check reaches what it is
 * held to.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEADLINE_MS, type Loaded, type Store } from "../../src/catalogue/load";

type Loader = <T>(call: () => Promise<T>) => Store<T>;

/** One store of a program, as the check knows it from outside. */
interface Probe {
  readonly store: Store<number>;
  readonly offs: (() => void)[];
  settle: ((answer: boolean) => void) | null;
  readonly throws: boolean;
  final: Loaded<number> | null;
}

/** The model of one store, from D431's text alone. */
interface Model {
  started: boolean;
  over: boolean;
  kind: "pending" | "done" | "failed" | "deadline";
  listeners: number;
  /** The time its clock expires, while the clock runs. */
  expires: number | null;
  readonly throws: boolean;
}

/** A small deterministic generator (a linear congruence), so that a seed is a program. */
function random(seed: number): () => number {
  let state = (seed * 2654435761) >>> 0;
  return () => {
    state = (state * 1103515245 + 12345) >>> 0;
    return state / 2 ** 32;
  };
}

/** Let the promises a settling queued settle. */
async function flush(): Promise<void> {
  for (let turn = 0; turn < 8; turn += 1) {
    await Promise.resolve();
  }
}

/** The member of a list at an index the program made (a program never reaches past its stores). */
function at<T>(list: readonly T[], index: number): T {
  const found = list[index];
  if (found === undefined) {
    throw new Error(`no store ${String(index)}`);
  }
  return found;
}

/** What went wrong in the program of `seed` over `loader`: the first divergence from the model, as
 * `M<n> …`, or none. */
async function violation(loader: Loader, seed: number): Promise<string | null> {
  vi.useFakeTimers();
  const next = random(seed);
  const probes: Probe[] = [];
  const calls: number[] = [];
  const model: Model[] = [];
  const modelCalls: number[] = [];
  const queue: number[] = [];
  let outstanding = 0;
  let now = 0;
  const settled = (id: number, kind: "done" | "failed" | "deadline"): void => {
    const one = at(model, id);
    if (!one.over) {
      one.over = true;
      one.expires = null;
      one.kind = kind;
    }
  };
  const pump = (): void => {
    while (outstanding < 2) {
      const id = queue.shift();
      if (id === undefined) {
        return;
      }
      const one = at(model, id);
      if (one.over) {
        continue;
      }
      if (one.listeners === 0) {
        one.expires = null;
        one.started = false;
        continue;
      }
      modelCalls.push(id);
      if (one.throws) {
        settled(id, "failed");
        continue;
      }
      outstanding += 1;
    }
  };
  try {
    for (let step = 0; step < 80; step += 1) {
      const roll = next();
      if (roll < 0.18 || probes.length === 0) {
        const id = probes.length;
        const throws = next() < 0.1;
        const probe: Probe = {
          store: loader(() => {
            calls.push(id);
            if (throws) {
              throw new Error("the call threw");
            }
            return new Promise<number>((resolve, reject) => {
              probe.settle = (answer) => {
                probe.settle = null;
                if (answer) {
                  resolve(id);
                } else {
                  reject(new Error("the call failed"));
                }
              };
            });
          }),
          offs: [],
          settle: null,
          throws,
          final: null,
        };
        probes.push(probe);
        model.push({ started: false, over: false, kind: "pending", listeners: 0, expires: null, throws });
      } else if (roll < 0.45) {
        const id = Math.floor(next() * probes.length);
        const probe = at(probes, id);
        probe.offs.push(probe.store.subscribe(() => undefined));
        const one = at(model, id);
        one.listeners += 1;
        if (!one.started && !one.over) {
          one.started = true;
          one.expires = now + DEADLINE_MS;
          queue.push(id);
          pump();
        }
      } else if (roll < 0.6) {
        const id = Math.floor(next() * probes.length);
        const off = at(probes, id).offs.pop();
        if (off !== undefined) {
          off();
          at(model, id).listeners -= 1;
        }
      } else if (roll < 0.8) {
        const flying = probes.flatMap((probe, id) => (probe.settle === null ? [] : [{ probe, id }]));
        const pick = flying[Math.floor(next() * flying.length)];
        if (pick !== undefined) {
          const answer = next() < 0.75;
          pick.probe.settle?.(answer);
          await flush();
          outstanding -= 1;
          settled(pick.id, answer ? "done" : "failed");
          pump();
        }
      } else {
        const by = [1, 999, 15_000, 29_999, DEADLINE_MS - 1, DEADLINE_MS][Math.floor(next() * 6)] ?? 1;
        await vi.advanceTimersByTimeAsync(by);
        now += by;
        model.forEach((one, id) => {
          if (one.expires !== null && one.expires <= now) {
            settled(id, "deadline");
          }
        });
      }
      const where = `seed ${String(seed)}, step ${String(step)}`;
      if (JSON.stringify(calls) !== JSON.stringify(modelCalls)) {
        return `M1 ${where}: calls ${JSON.stringify(calls)}, the model's ${JSON.stringify(modelCalls)}`;
      }
      for (const [id, probe] of probes.entries()) {
        const snapshot = probe.store.snapshot();
        const wanted = at(model, id).kind;
        if (snapshot.kind !== wanted || (snapshot.kind === "done" && snapshot.value !== id)) {
          return `M2 ${where}: store ${String(id)} is ${snapshot.kind}, the model's ${wanted}`;
        }
        if (probe.final !== null && snapshot !== probe.final) {
          return `M4 ${where}: store ${String(id)}'s settled snapshot changed`;
        }
        if (probe.final === null && snapshot.kind !== "pending") {
          probe.final = snapshot;
        }
      }
      const clocks = model.filter((one) => one.expires !== null).length;
      if (vi.getTimerCount() !== clocks) {
        return `M3 ${where}: ${String(vi.getTimerCount())} timers run, the model's ${String(clocks)}`;
      }
    }
    return null;
  } finally {
    for (const probe of probes) {
      probe.settle?.(true);
    }
    await flush();
    vi.useRealTimers();
  }
}

/** A queued store of the flawed queue: send, whether it has a subscriber, whether it is over, forget. */
interface Job {
  readonly send: () => void;
  readonly live: () => boolean;
  readonly over: () => boolean;
  readonly drop: () => void;
}

type Flaw = "three places" | "an overdue call's place leaked" | "the pump stops at an expired store" | "the pump stops at a dropped store" | "a dropped store keeps its clock";

/** The queue of `load.ts`, written again with one flaw. */
function flawed(flaw: Flaw | null): Loader {
  const queue: Job[] = [];
  let outstanding = 0;
  const places = flaw === "three places" ? 3 : 2;
  const pump = (): void => {
    while (outstanding < places) {
      const job = queue.shift();
      if (job === undefined) {
        return;
      }
      if (job.over()) {
        if (flaw === "the pump stops at an expired store") {
          return;
        }
        continue;
      }
      if (!job.live()) {
        job.drop();
        if (flaw === "the pump stops at a dropped store") {
          return;
        }
        continue;
      }
      job.send();
    }
  };
  return <T>(call: () => Promise<T>): Store<T> => {
    let state: Loaded<T> = { kind: "pending" };
    let started = false;
    let over = false;
    let clear: (() => void) | undefined;
    const listeners = new Set<() => void>();
    const settle = (settled: Loaded<T>): void => {
      if (over) {
        return;
      }
      over = true;
      clear?.();
      clear = undefined;
      state = settled;
      for (const listener of [...listeners]) {
        listener();
      }
    };
    const done = (settled: Loaded<T>): void => {
      if (!(flaw === "an overdue call's place leaked" && over)) {
        outstanding -= 1;
      }
      settle(settled);
      pump();
    };
    const job: Job = {
      send: () => {
        outstanding += 1;
        let sent: Promise<T>;
        try {
          sent = call();
        } catch {
          done({ kind: "failed" });
          return;
        }
        sent.then(
          (value) => {
            done({ kind: "done", value });
          },
          () => {
            done({ kind: "failed" });
          },
        );
      },
      live: () => listeners.size > 0,
      over: () => over,
      drop: () => {
        if (flaw !== "a dropped store keeps its clock") {
          clear?.();
          clear = undefined;
        }
        started = false;
      },
    };
    return {
      subscribe: (listener) => {
        listeners.add(listener);
        if (!started && !over) {
          started = true;
          const timer = setTimeout(() => {
            settle({ kind: "deadline" });
          }, DEADLINE_MS);
          clear = () => {
            clearTimeout(timer);
          };
          queue.push(job);
          pump();
        }
        return () => {
          listeners.delete(listener);
        };
      },
      snapshot: () => state,
    };
  };
}

const SEEDS = Array.from({ length: 300 }, (_, index) => index + 1);

afterEach(() => {
  vi.useRealTimers();
});

describe("the queue, against the model, over the real module", () => {
  it.each(SEEDS)("agrees with the model after every step: seed %i", async (seed) => {
    vi.resetModules();
    const { loader } = await import("../../src/catalogue/load");
    expect(await violation(loader, seed)).toBeNull();
  });
});

describe("the check finds each flaw (the controls)", () => {
  const FLAWS: readonly (readonly [Flaw, string, readonly string[]])[] = [
    ["three places", "M1", ["M1"]],
    ["an overdue call's place leaked", "M1", ["M1", "M3"]],
    ["the pump stops at an expired store", "M1", ["M1"]],
    ["the pump stops at a dropped store", "M1", ["M1"]],
    ["a dropped store keeps its clock", "M3", ["M3"]],
  ];

  it("passes every seed over the copy without a flaw, so that the controls differ in their flaw alone", async () => {
    for (const seed of SEEDS) {
      expect(await violation(flawed(null), seed)).toBeNull();
    }
  });

  it.each(FLAWS)("%s is found, as %s", async (flaw, _first, checks) => {
    const found: string[] = [];
    for (const seed of SEEDS) {
      const one = await violation(flawed(flaw), seed);
      if (one !== null) {
        found.push(one);
      }
    }
    expect(found.length).toBeGreaterThan(0);
    expect(found.filter((one) => !checks.some((check) => one.startsWith(check)))).toEqual([]);
  });
});
