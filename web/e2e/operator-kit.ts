/**
 * What the operator matrix's specs share (D423): entering the token as a person would, but by
 * `locator.evaluate` (Playwright 1.63 titles a `fill`, `type` or `insertText` step with the value
 * typed, and CI uploads the report); driving the end-to-end harness; recording what the page sent
 * (`request.postData()` is the oracle for a body); and counting secrets where none may be (a count,
 * never the value, so that a failing assertion prints no secret).
 */
import type { APIRequestContext, APIResponse, BrowserContext, Locator, Page, Request } from "@playwright/test";

import { expect, type Watcher } from "./fixtures";
import { remember } from "./servers";

export const SHELL = {
  start: "Enter your name and the curator token to begin.",
  forgotten: "The token is forgotten. Enter it again to go on.",
  idle: "Locked after ten minutes without input. Enter the token again to go on.",
  token: "The server refused the token. Enter it again to go on.",
  failures: "Too many refused tokens from this address. Wait a minute, then enter the token again.",
  page: "Locked when the page was left. Enter the token again to go on.",
  restored: "Locked: the page was restored from the browser's cache. Enter the token again to go on.",
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

/** Run one of the harness's operations over its fields, and read the outcome it wrote. `input`
 * says how its button is pressed: `trusted`, a click as a person makes it (a trusted pointer
 * event, which is activity for the idle lock), or `script`, the page's own `click()` (an event
 * that is no activity), for the tests of what is and is not activity. */
export async function run(
  page: Page,
  operation: string,
  fields: { dataset?: string; manifest?: string; descriptor?: string } = {},
  input: "trusted" | "script" = "trusted",
): Promise<Shown> {
  const output = page.locator('output[aria-label="outcome"]');
  const before = Number(await output.getAttribute("data-runs"));
  for (const [name, value] of Object.entries(fields)) {
    await page.locator(`input[aria-label="${name}"]`).fill(value);
  }
  const button = page.getByRole("button", { name: operation, exact: true });
  if (input === "trusted") {
    await button.click();
  } else {
    await button.evaluate((element) => {
      if (element instanceof HTMLElement) {
        element.click();
      }
    });
  }
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
 * cookies, the databases, the origin private file system (every file's name and content), the Cache
 * API (every cache's name, request and response), its address, its history and its window's name.
 * `stored` counts what is kept at all. */
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
    // The origin private file system: the tree under its root, each name and each file's content.
    let files = 0;
    const walk = async (directory: FileSystemDirectoryHandle, path: string): Promise<void> => {
      // (`entries` is in the DOM's async-iterable typings, which the package's libraries do not name.)
      const listed = (directory as unknown as { entries(): AsyncIterable<[string, FileSystemHandle]> }).entries();
      for await (const [name, handle] of listed) {
        files += 1;
        texts.push(`${path}${name}`);
        if (handle instanceof FileSystemFileHandle) {
          texts.push(await (await handle.getFile()).text());
        } else if (handle instanceof FileSystemDirectoryHandle) {
          await walk(handle, `${path}${name}/`);
        }
      }
    };
    await walk(await navigator.storage.getDirectory(), "/");
    // The Cache API: each cache's name, and each entry's request (URL, headers) and response (headers,
    // body, read through its stream: the client seals the page's `Response.text()`, D419).
    const bodyOf = async (response: Response): Promise<string> => {
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let text = "";
      for (let read = await reader?.read(); read !== undefined && !read.done; read = await reader?.read()) {
        text += decoder.decode(read.value, { stream: true });
      }
      return text + decoder.decode();
    };
    let cached = 0;
    for (const name of await caches.keys()) {
      texts.push(name);
      const cache = await caches.open(name);
      for (const request of await cache.keys()) {
        cached += 1;
        texts.push(request.url, JSON.stringify([...request.headers]));
        const response = await cache.match(request);
        if (response !== undefined) {
          texts.push(JSON.stringify([...response.headers]), await bodyOf(response));
        }
      }
    }
    return { texts, stored: stores.reduce((sum, store) => sum + store.length, 0) + databases.length + files + cached + (document.cookie === "" ? 0 : 1) };
  });
  const cookies = await page.context().cookies();
  return { texts: [...found.texts, JSON.stringify(cookies)], stored: found.stored + cookies.length };
}

/** A text with every credential in it replaced: an `Authorization: Bearer` value, an `Aibi-CSRF`
 * value, any token's or handle's shape, and each of `secrets`. Playwright's `APIRequestContext`
 * errors carry a call log with the request's headers (1.63), `Authorization` among them. */
export function redacted(text: string, secrets: readonly string[] = []): string {
  let found = text
    .replace(/(authorization:\s*bearer\s+)\S+/giu, "$1[redacted]")
    .replace(/(aibi-csrf:\s*)\S+/giu, "$1[redacted]")
    .replace(/(?<![A-Za-z0-9_-])(?:aibi|ses)_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])/gu, "[redacted]");
  for (const secret of secrets) {
    if (secret !== "") {
      found = found.replaceAll(secret, "[redacted]");
    }
  }
  return found;
}

/** The options of a Playwright API request. */
type ApiOptions = NonNullable<Parameters<APIRequestContext["fetch"]>[1]>;

/** A request of the matrix's own through Playwright's API context (the CLI's stand-in, a client
 * that is no browser): a failure is rethrown with its message and its stack redacted (the call
 * log holds the `Authorization` header), and nothing else changed. Every such call goes through
 * here. */
export async function api(request: APIRequestContext, method: "GET" | "POST", url: string, options: ApiOptions = {}): Promise<APIResponse> {
  try {
    return await request.fetch(url, { ...options, method });
  } catch (error) {
    const failure = new Error(redacted(error instanceof Error ? error.message : "the request failed"));
    failure.name = error instanceof Error ? error.name : "Error";
    failure.stack = redacted(error instanceof Error ? (error.stack ?? "") : "");
    throw failure;
  }
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
