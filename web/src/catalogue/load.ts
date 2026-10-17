/**
 * The tab's one queue of public tool calls (D431, D422) and the outcomes React reads through
 * `useSyncExternalStore`: it renders nothing and holds no text.
 *
 * - **A store per load.** `loader(call)` gives a store whose call starts on its first subscribe,
 *   never twice: StrictMode's subscribe, unsubscribe and subscribe again keep the store that
 *   started, so a mount sends one call. A store is queued, first in, first out, behind the calls
 *   already outstanding.
 * - **At most two calls outstanding** (`OUTSTANDING`): a tab never holds more than the server's
 *   `client_tool_calls` (2) places. An overdue call counts until it settles; nothing retries.
 * - **A queued store with no subscriber at its turn is dropped unsent**, its clock stopped and its
 *   start forgotten, so that a later subscribe queues it again.
 * - **The deadline.** Each store's clock starts when it is queued (never when it is sent, so that no
 *   store waits unbounded behind overdue calls) and runs `DEADLINE_MS` (45 s: the server's
 *   `tool_seconds` is a fixed 30 s, a wait for a place inside it). On expiry the store shows the
 *   deadline's own words (`DEADLINE`) and leaves the queue, unsent if it was not yet sent; an answer
 *   that arrives later is discarded. The clock is stopped (`stop`, the one place that clears it)
 *   when the call settles, when the deadline fires and when the store is dropped.
 *
 * The two timer calls are D429's reviewed sites (`scripts/lint/display.mjs`' `SITES`: the rule
 * `dom` reports the callee, so each site matches the identifier, once in this file); this module's
 * own source cell (`tests/unit/load.test.ts`) holds their text, a closure with no argument and no
 * text. No other module may schedule anything (`display/dom`).
 */

/** What a store holds: nothing yet, the call's outcome, the deadline passed, or a call that threw. */
export type Loaded<T> = { readonly kind: "pending" } | { readonly kind: "failed" } | { readonly kind: "deadline" } | { readonly kind: "done"; readonly value: T };

/** A store `useSyncExternalStore` reads. */
export interface Store<T> {
  readonly subscribe: (listener: () => void) => () => void;
  readonly snapshot: () => Loaded<T>;
}

/** How long a store waits for its answer, from when it is queued. */
export const DEADLINE_MS = 45_000;

/** How many calls a tab has outstanding at most (the server's `client_tool_calls`). */
export const OUTSTANDING = 2;

/** The words of a store whose deadline passed: fixed. */
export const DEADLINE = "No answer came within 45 seconds; the server may still be working on this or an earlier request. Reload the page to ask again.";

const PENDING: Loaded<never> = Object.freeze({ kind: "pending" });
const FAILED: Loaded<never> = Object.freeze({ kind: "failed" });
const EXPIRED: Loaded<never> = Object.freeze({ kind: "deadline" });

/** A queued store: send its call, whether it has a subscriber, whether it is over (settled or
 * expired), and forget it unsent. */
interface Job {
  readonly send: () => void;
  readonly live: () => boolean;
  readonly over: () => boolean;
  readonly drop: () => void;
}

const queue: Job[] = [];
let outstanding = 0;

/** Send the queued calls while a place is free: a store that is over is skipped, one with no
 * subscriber dropped. */
function pump(): void {
  while (outstanding < OUTSTANDING) {
    const job = queue.shift();
    if (job === undefined) {
      return;
    }
    if (job.over()) {
      continue;
    }
    if (!job.live()) {
      job.drop();
      continue;
    }
    job.send();
  }
}

/** A store of one call's outcome (the module's docstring). */
export function loader<T>(call: () => Promise<T>): Store<T> {
  let state: Loaded<T> = PENDING;
  let started = false;
  let over = false;
  /** Stops the running clock, if one runs (set as the clock starts). */
  let clear: (() => void) | undefined;
  const listeners = new Set<() => void>();
  const stop = (): void => {
    clear?.();
    clear = undefined;
  };
  const settle = (next: Loaded<T>): void => {
    if (over) {
      return;
    }
    over = true;
    stop();
    state = next;
    for (const listener of [...listeners]) {
      listener();
    }
  };
  const expire = (): void => {
    settle(EXPIRED);
  };
  const done = (next: Loaded<T>): void => {
    outstanding -= 1;
    settle(next);
    pump();
  };
  const job: Job = {
    send: () => {
      outstanding += 1;
      let sent: Promise<T>;
      try {
        sent = call();
      } catch {
        done(FAILED);
        return;
      }
      sent.then(
        (value) => {
          done(Object.freeze({ kind: "done", value }));
        },
        () => {
          done(FAILED);
        },
      );
    },
    live: () => listeners.size > 0,
    over: () => over,
    drop: () => {
      stop();
      started = false;
    },
  };
  return Object.freeze({
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      if (!started && !over) {
        started = true;
        const timer = setTimeout(expire, DEADLINE_MS);
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
  });
}
