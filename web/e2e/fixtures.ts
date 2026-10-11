/**
 * The matrix's fixtures: which build a project serves, the origins, and the watcher that makes
 * a policy-refused feature a failure rather than a console line.
 *
 * **The watcher.** A violation is (a) a `securitypolicyviolation` event, recorded by an init
 * script installed in the context before any page script runs and reported through a binding,
 * so that a navigation loses none; (b) an uncaught page error; (c) a console error, or a
 * response of status 400 or more, that does not match a refusal the test said it expects, by
 * URL **and** status (Chromium logs "Failed to load resource: … status of 404" for every refusal
 * a page meets, so an expected refusal is named, never a status alone); (d) a request that
 * failed without a response (and the console's `net::ERR_...` line for it), unless the test
 * expected it to fail, by its URL. Every watcher expects the origin's `/favicon.ico` 404: Chromium
 * asks for an icon the documents never name.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { type BrowserContext, expect, test as base } from "@playwright/test";

import { type Bundle, servers } from "./servers";

export interface Violation {
  readonly directive: string;
  readonly blocked: string;
  readonly source: string;
}

export interface Logged {
  readonly kind: "console" | "response" | "pageerror" | "requestfailed";
  readonly text: string;
  readonly url: string;
  readonly status: number | null;
}

export interface Expected {
  readonly url: string;
  /** The status, or `"failed"` for a request the browser abandoned without one. */
  readonly status: number | "failed";
}

/** The logged events no expected refusal explains: an event matches one only by its URL and
 * its status, both. */
export function unexplained(logged: readonly Logged[], expected: readonly Expected[]): Logged[] {
  return logged.filter(
    (event) =>
      !expected.some((refusal) => {
        if (event.url !== refusal.url) {
          return false;
        }
        if (event.kind === "response") {
          return event.status === refusal.status;
        }
        if (event.kind === "requestfailed") {
          return refusal.status === "failed";
        }
        if (event.kind !== "console") {
          return false;
        }
        // A request expected to fail without a response is logged as `net::ERR_...` (an abort,
        // a reset, a refused connection: the operator matrix's unknown outcomes, D423).
        return refusal.status === "failed" ? /^Failed to load resource: net::ERR_[A-Z_]+$/u.test(event.text) : event.text.includes(`status of ${String(refusal.status)}`);
      }),
  );
}

export class Watcher {
  readonly violations: Violation[] = [];
  readonly logged: Logged[] = [];
  readonly expected: Expected[] = [];

  expect(url: string, status: number | "failed"): void {
    this.expected.push({ url, status });
  }

  unexplained(): Logged[] {
    return unexplained(this.logged, this.expected);
  }

  /** Fail unless nothing was refused by the policy and nothing failed unexpectedly. */
  clean(): void {
    expect(this.violations, "policy violations").toEqual([]);
    expect(this.unexplained(), "unexpected errors and refusals").toEqual([]);
  }
}

const RECORDER = `
addEventListener("securitypolicyviolation", (event) => {
  window.__aibiViolation({
    directive: event.effectiveDirective,
    blocked: event.blockedURI,
    source: event.sourceFile,
  });
}, true);
addEventListener("pageshow", (event) => { window.__aibiPersisted = event.persisted; });
`;

export async function watch(context: BrowserContext): Promise<Watcher> {
  const watcher = new Watcher();
  await context.exposeBinding("__aibiViolation", (_source, violation: Violation) => {
    watcher.violations.push(violation);
  });
  await context.addInitScript({ content: RECORDER });
  const attach = (page: import("@playwright/test").Page): void => {
    page.on("console", (message) => {
      if (message.type() === "error") {
        watcher.logged.push({ kind: "console", text: message.text(), url: message.location().url, status: null });
      }
    });
    page.on("pageerror", (error) => {
      watcher.logged.push({ kind: "pageerror", text: error.message, url: page.url(), status: null });
    });
    page.on("response", (response) => {
      if (response.status() >= 400) {
        watcher.logged.push({ kind: "response", text: "", url: response.url(), status: response.status() });
      }
    });
    page.on("requestfailed", (request) => {
      watcher.logged.push({ kind: "requestfailed", text: request.failure()?.errorText ?? "", url: request.url(), status: null });
    });
  };
  context.pages().forEach(attach);
  context.on("page", attach);
  return watcher;
}

export interface Chunk {
  readonly file: string;
  readonly imports?: readonly string[];
  readonly css?: readonly string[];
  readonly isEntry?: boolean;
}

export type Manifest = Record<string, Chunk>;

export const test = base.extend<
  { origin: string; manifest: Manifest; watcher: Watcher },
  { bundle: Bundle; dir: string }
>({
  bundle: ["prod", { option: true, scope: "worker" }],
  dir: [
    async ({ bundle }, use) => {
      await use(servers().bundles[bundle].dir);
    },
    { scope: "worker" },
  ],
  origin: async ({ bundle }, use) => {
    await use(servers().bundles[bundle].origin);
  },
  manifest: async ({ dir }, use) => {
    await use(JSON.parse(readFileSync(path.join(dir, ".vite", "manifest.json"), "utf8")) as Manifest);
  },
  watcher: async ({ context, origin }, use) => {
    const watcher = await watch(context);
    // Chromium asks every origin it shows for an icon; the documents name none, so the server's
    // answer is the page path's NOT_FOUND, which policy.spec.ts pins.
    watcher.expect(`${origin}/favicon.ico`, 404);
    await use(watcher);
  },
});

export { expect };

/** The other site's origins: `localhost` (another site than `127.0.0.1`) and `127.0.0.1` on
 * another port (the same site, another origin). */
export function other(): { readonly site: string; readonly port: string } {
  const port = String(servers().otherPort);
  return { site: `http://localhost:${port}`, port: `http://127.0.0.1:${port}` };
}

/** The words a page shows, its text nodes joined by one space. */
export const WORDS = {
  home: "aibi The catalogue opens here. Curate",
  notFound: "aibi Nothing is here. The catalogue",
  failure: "aibi This screen failed to load. Reload the page to try again.",
  operator: "aibi operator Enter your name and the curator token to begin. Your name Curator token Unlock The operator screens are not built yet. The catalogue",
  harness: "aibi-e2e-harness-1b6f0c",
} as const;

export async function words(page: import("@playwright/test").Page): Promise<string> {
  return page.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const found: string[] = [];
    for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
      const text = (node.nodeValue ?? "").replace(/\s+/gu, " ").trim();
      if (text !== "") {
        found.push(text);
      }
    }
    return found.join(" ");
  });
}
