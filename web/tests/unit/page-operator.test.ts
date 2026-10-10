/**
 * The page's own operator client (`src/api/operator.ts`, D423), loaded as the page loads it, on
 * jsdom's real window and document: the wiring of its listeners. The idle lock's inputs and
 * `pagehide` and `pageshow` fire at the window, `visibilitychange` at the document: listening on the
 * other object loses the lock when the page is left or restored from the back/forward cache.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Curator } from "../../src/api/curator";

const TOKEN = `aibi_${"T0k-_n".repeat(7)}Q`;
const CSRF = `${"CsRf-_".repeat(7)}Z`;

beforeEach(() => {
  vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ csrf: CSRF }), { status: 200, headers: { "Content-Type": "application/json" } })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The page's operator, freshly loaded and unlocked. */
async function unlocked(): Promise<Curator> {
  vi.resetModules();
  const { operator } = await import("../../src/api/operator");
  expect(await operator.unlock(TOKEN, "Ada")).toEqual({ kind: "unlocked" });
  expect(operator.view()).toEqual({ locked: false, reason: null });
  return operator;
}

describe("the page's operator client listens where the page fires", () => {
  it("locks on a pagehide at the window", async () => {
    const operator = await unlocked();
    window.dispatchEvent(new PageTransitionEvent("pagehide"));
    expect(operator.view()).toEqual({ locked: true, reason: "page" });
  });

  it("locks on a pageshow from the back/forward cache at the window, and not on one that is not", async () => {
    const operator = await unlocked();
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false }));
    expect(operator.view().locked).toBe(false);
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    expect(operator.view()).toEqual({ locked: true, reason: "restored" });
  });

  it("locks on a visibilitychange at the document once the idle limit is past", async () => {
    const operator = await unlocked();
    const now = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(now + 11 * 60 * 1000);
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
    expect(operator.view()).toEqual({ locked: true, reason: "idle" });
  });

  it("does not lock on a visibilitychange inside the limit", async () => {
    const operator = await unlocked();
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
    expect(operator.view().locked).toBe(false);
  });

  it("hears a pagehide at the document no more than a browser would (it fires at the window alone)", async () => {
    const operator = await unlocked();
    document.dispatchEvent(new PageTransitionEvent("pagehide"));
    expect(operator.view().locked).toBe(false);
  });
});
