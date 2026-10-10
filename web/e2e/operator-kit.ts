/**
 * What the operator matrix's specs share (D423): entering the token as a person would, but by
 * `locator.evaluate` (Playwright 1.63 titles a `fill`, `type` or `insertText` step with the value
 * typed, and CI uploads the report); driving the end-to-end harness; recording what the page sent
 * (`request.postData()` is the oracle for a body); and counting secrets where none may be (a count,
 * never the value, so that a failing assertion prints no secret).
 */
import type { BrowserContext, Locator, Page, Request } from "@playwright/test";

import { expect, type Watcher } from "./fixtures";
import { remember } from "./servers";

export const SHELL = {
  start: "Enter your name and the curator token to begin.",
  forgotten: "The token is forgotten. Enter it again to go on.",
  idle: "Locked after ten minutes without input. Enter the token again to go on.",
  token: "The server refused the token. Enter it again to go on.",
  failures: "Too many refused tokens from this address. Wait a minute, then enter the token again.",
  unlocked: "Unlocked. The token is held by this page alone, until you forget it, leave the page or stop for ten minutes.",
} as const;

export const IDLE_LIMIT_MS = 10 * 60 * 1000;

/** A token in the curator token's form that is not the configured one. */
export const WRONG_TOKEN = `aibi_${"Wr0ng-".repeat(7)}X`;

export function status(page: Page): Locator {
  return page.locator('section[aria-label="token"] [role="status"]');
}

/** Enter a name and a token and press Unlock: the name by `fill` (no secret), the token by
 * `evaluate`, its argument never rendered in a step's title; the field's `value` attribute is
 * checked while it holds the token. */
export async function unlock(page: Page, token: string, name = "e2e operator"): Promise<void> {
  await page.locator('input[name="operator"]').fill(name);
  const field = page.locator('input[name="token"]');
  await field.evaluate((element, value) => {
    if (element instanceof HTMLInputElement) {
      element.value = value;
    }
  }, token);
  expect(await field.evaluate((element) => element.hasAttribute("value")), "the token field's value attribute while it holds the token").toBe(false);
  await page.getByRole("button", { name: "Unlock" }).click();
}

export interface Shown {
  readonly kind: string;
  readonly status: string;
  readonly codes: string;
  readonly reason: string;
  readonly what: string;
  readonly holds: string;
  readonly decoded: string;
}

/** Run one of the harness's operations over its fields, and read the outcome it wrote. */
export async function run(page: Page, operation: string, fields: { dataset?: string; manifest?: string; descriptor?: string } = {}): Promise<Shown> {
  const output = page.locator('output[aria-label="outcome"]');
  const before = Number(await output.getAttribute("data-runs"));
  for (const [name, value] of Object.entries(fields)) {
    await page.locator(`input[aria-label="${name}"]`).fill(value);
  }
  await page.getByRole("button", { name: operation, exact: true }).click();
  await expect(output).toHaveAttribute("data-runs", String(before + 1));
  const read = async (name: string): Promise<string> => (await output.getAttribute(`data-${name}`)) ?? "";
  return {
    kind: await read("kind"),
    status: await read("status"),
    codes: await read("codes"),
    reason: await read("reason"),
    what: await read("what"),
    holds: await read("holds"),
    decoded: await read("decoded"),
  };
}

export interface Recorded {
  readonly url: string;
  readonly method: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string | null;
}

/** Record every request the context's pages send, with all its headers and its body. */
export function record(context: BrowserContext): { all: () => Promise<Recorded[]> } {
  const pending: Promise<Recorded>[] = [];
  const take = (request: Request): void => {
    pending.push(request.allHeaders().then((headers) => ({ url: request.url(), method: request.method(), headers, body: request.postData() })));
  };
  context.on("request", take);
  return { all: () => Promise.all(pending) };
}

const CREDENTIALS = ["authorization", "aibi-operator", "aibi-csrf"];

/** Every request that carries a credential goes to this origin's `/operator/`, the token as
 * `Authorization: Bearer` exactly; the CSRF tokens and handles seen are remembered for the
 * report's scan, and each handle stands only in the body of a change, a publish or a discard.
 * The failures are counts. */
export function checkCredentials(records: readonly Recorded[], origin: string, token: string): void {
  let elsewhere = 0;
  let wrongBearer = 0;
  let handlesOutOfPlace = 0;
  const handles = new Set<string>();
  for (const request of records) {
    const url = new URL(request.url);
    const credentialed = CREDENTIALS.some((name) => name in request.headers);
    if (credentialed && (url.origin !== origin || !url.pathname.startsWith("/operator/"))) {
      elsewhere += 1;
    }
    const authorization = request.headers["authorization"];
    if (authorization !== undefined && authorization !== `Bearer ${token}`) {
      wrongBearer += 1;
    }
    const csrf = request.headers["aibi-csrf"];
    if (csrf !== undefined) {
      remember(csrf);
    }
    for (const found of (request.body ?? "").matchAll(/ses_[A-Za-z0-9_-]{43}/gu)) {
      handles.add(found[0]);
    }
  }
  remember(...handles);
  for (const request of records) {
    const pathname = new URL(request.url).pathname;
    const inBody = /\/session\/(?:change|publish|discard)$/u.test(pathname);
    for (const handle of handles) {
      const outside = [request.url, ...Object.values(request.headers)].filter((text) => text.includes(handle)).length;
      const body = (request.body ?? "").includes(handle) ? 1 : 0;
      handlesOutOfPlace += outside + (inBody ? 0 : body);
    }
    if (/ses_[A-Za-z0-9_-]{43}/u.test(request.url)) {
      handlesOutOfPlace += 1;
    }
  }
  expect(elsewhere, "requests with a credential sent anywhere but this origin's /operator/").toBe(0);
  expect(wrongBearer, "Authorization headers that are not the token as Bearer").toBe(0);
  expect(handlesOutOfPlace, "handles outside the bodies of change, publish and discard").toBe(0);
}

/** How many times the secrets stand in the texts. */
export function occurrences(texts: readonly string[], secrets: readonly string[]): number {
  return texts.reduce((sum, text) => sum + secrets.reduce((inner, secret) => inner + (secret === "" ? 0 : text.split(secret).length - 1), 0), 0);
}

/** The places a page could keep or show a secret: its markup, every field's value, storage,
 * cookies, the databases, its address, its history and its window's name. */
export async function pageTexts(page: Page): Promise<{ texts: string[]; stored: number }> {
  const found = await page.evaluate(async () => {
    const texts = [document.documentElement.outerHTML, window.location.href, JSON.stringify(window.history.state), window.name, document.cookie, document.title];
    for (const input of document.querySelectorAll("input")) {
      texts.push(input.value);
    }
    const stores = [window.localStorage, window.sessionStorage];
    for (const store of stores) {
      for (let index = 0; index < store.length; index += 1) {
        const key = store.key(index) ?? "";
        texts.push(key, store.getItem(key) ?? "");
      }
    }
    const databases = await window.indexedDB.databases();
    return { texts, stored: stores.reduce((sum, store) => sum + store.length, 0) + databases.length + (document.cookie === "" ? 0 : 1) };
  });
  const cookies = await page.context().cookies();
  return { texts: [...found.texts, JSON.stringify(cookies)], stored: found.stored + cookies.length };
}

/** No secret in the page, its storage, its console, and nothing stored at all; the address is
 * the fixed path and the history holds no state. */
export async function noSecrets(page: Page, consoled: readonly string[], secrets: readonly string[]): Promise<void> {
  const { texts, stored } = await pageTexts(page);
  expect(occurrences(texts, secrets), "secrets in the page").toBe(0);
  expect(occurrences(consoled, secrets), "secrets in the console").toBe(0);
  expect(stored, "things stored by the page").toBe(0);
  expect(new URL(page.url()).pathname, "the address").toBe("/curate");
  expect(new URL(page.url()).search + new URL(page.url()).hash, "the address's query and fragment").toBe("");
  expect(await page.evaluate(() => window.history.state === null), "the history entry's state is null").toBe(true);
}

/** The console's texts of a page, as they come. */
export function consoleOf(page: Page): string[] {
  const texts: string[] = [];
  page.on("console", (message) => texts.push(message.text()));
  return texts;
}

/** Tell the watcher what the operator matrix expects: each refusal by URL and status, and a
 * `requestfailed` for each operator URL (Chromium 141 reports a share of `/operator/` answers it
 * delivered in full, 200s among them, as `net::ERR_ABORTED` to Playwright, D423; the page's
 * `fetch` resolved and read each body, which the outcome shows). */
export function expectOperator(watcher: Watcher, origin: string, refusals: readonly (readonly [string, number])[], paths: readonly string[]): void {
  for (const [route, code] of refusals) {
    watcher.expect(`${origin}${route}`, code);
  }
  for (const route of new Set([...paths, ...refusals.map(([found]) => found), "/operator/csrf"])) {
    watcher.expect(`${origin}${route}`, "failed");
  }
}
