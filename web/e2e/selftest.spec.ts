/**
 * The matrix's own checks, tested: the recorder catches a page that breaks the policy, the
 * expected-refusal matcher explains a refusal only by its URL and its status, both, the page scan
 * sees a secret wherever a page can keep one (storage, the origin private file system, the Cache
 * API), a failing API call's error holds no credential, and the report scan reads the run's output.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import type { FullResult, TestCase, TestError, TestResult, TestStep } from "@playwright/test/reporter";

import { expect, test, unexplained, Watcher } from "./fixtures";
import { api, occurrences, pageTexts, redacted } from "./operator-kit";
import ScanReporter, { errorTexts, outputFindings } from "./scan-reporter";

test("the recorder catches what the policy refuses: a Trusted Types sink, inline style and script", async ({ page, origin, watcher }) => {
  await page.goto(`${origin}/`);
  await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
  const thrown = await page.evaluate(() => {
    const errors: string[] = [];
    try {
      document.body.insertAdjacentHTML("beforeend", "<b>markup</b>");
    } catch (error) {
      errors.push(error instanceof Error ? error.name : "thrown");
    }
    const style = document.createElement("style");
    style.append("p { color: red; }");
    document.head.append(style);
    const script = document.createElement("script");
    try {
      script.append("void 0");
      document.head.append(script);
    } catch (error) {
      errors.push(error instanceof Error ? error.name : "thrown");
    }
    return errors;
  });
  expect(thrown).toContain("TypeError");
  await expect.poll(() => watcher.violations.map((violation) => violation.directive).sort()).toEqual(
    expect.arrayContaining(["require-trusted-types-for", "style-src-elem"]),
  );
  expect(() => {
    watcher.clean();
  }).toThrow();
});

test("the recorder is installed before the page's own scripts, and survives a navigation", async ({ page, origin, watcher }) => {
  await page.goto(`${origin}/curate`);
  await page.goto(`${origin}/`);
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.append("p { color: red; }");
    document.head.append(style);
  });
  await expect.poll(() => watcher.violations.length).toBe(1);
  expect(await page.evaluate(() => typeof Reflect.get(window, "__aibiViolation"))).toBe("function");
});

test("the recorder sees a violation during the document's own parse, before any load event", async ({ page, origin, watcher }) => {
  await page.route(`${origin}/`, async (route) => {
    const answer = await route.fetch();
    const body = (await answer.text()).replace("</head>", "<style>p { color: red; }</style></head>");
    await route.fulfill({ response: answer, body });
  });
  await page.goto(`${origin}/`);
  await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
  await expect.poll(() => watcher.violations.map((violation) => violation.directive)).toEqual(["style-src-elem"]);
});

test("an unexpected refusal fails; an expected one is matched by URL and status", async ({ page, origin, watcher }) => {
  await page.goto(`${origin}/`);
  await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
  const missing = `${origin}/assets/missing-AAAAAAAA.png`;
  await Promise.all([
    page.waitForResponse(missing),
    page.evaluate((source) => {
      const image = document.createElement("img");
      image.src = source;
      document.body.append(image);
    }, missing),
  ]);
  await expect.poll(() => watcher.logged.filter((event) => event.url === missing).map((event) => event.kind).sort()).toEqual([
    "console",
    "response",
  ]);
  expect(watcher.violations).toEqual([]);
  expect(unexplained(watcher.logged, [])).toHaveLength(2);
  expect(unexplained(watcher.logged, [{ url: missing, status: 404 }])).toEqual([]);
  expect(unexplained(watcher.logged, [{ url: missing, status: 403 }])).toHaveLength(2);
  expect(unexplained(watcher.logged, [{ url: `${origin}/assets/other-AAAAAAAA.png`, status: 404 }])).toHaveLength(2);
  expect(() => {
    watcher.clean();
  }).toThrow();
  watcher.expect(missing, 404);
  watcher.clean();
});

test("an uncaught page error fails the watcher, though it is a console error too", async ({ page, origin, watcher }) => {
  await page.goto(`${origin}/`);
  await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
  await page.evaluate(() => {
    setTimeout(() => {
      throw new Error("the selftest's uncaught error");
    }, 0);
  });
  await expect.poll(() => watcher.logged.filter((event) => event.kind === "pageerror").map((event) => event.text)).toEqual([
    "the selftest's uncaught error",
  ]);
  expect(watcher.violations).toEqual([]);
  const errors = watcher.logged.filter((event) => event.kind === "pageerror");
  expect(unexplained(errors, [])).toHaveLength(1);
  expect(unexplained(errors, [{ url: `${origin}/`, status: 404 }])).toHaveLength(1);
  expect(() => {
    watcher.clean();
  }).toThrow();
});

test("a request that failed without a response fails the watcher, unless it was expected as failed", async ({ page, origin, watcher }) => {
  await page.goto(`${origin}/`);
  await expect(page.getByRole("link", { name: "Curate" })).toBeVisible();
  const aborted = `${origin}/assets/aborted-AAAAAAAA.png`;
  await page.route(aborted, (route) => route.abort());
  await page.evaluate(async (address) => {
    await fetch(address).catch(() => undefined);
  }, aborted);
  await expect.poll(() => watcher.logged.filter((event) => event.kind === "requestfailed").map((event) => event.url)).toEqual([aborted]);
  expect(watcher.logged.filter((event) => event.kind === "response")).toEqual([]);
  const failed = watcher.logged.filter((event) => event.kind === "requestfailed");
  expect(unexplained(failed, [])).toHaveLength(1);
  expect(unexplained(failed, [{ url: aborted, status: 404 }])).toHaveLength(1);
  expect(unexplained(failed, [{ url: `${origin}/assets/other-AAAAAAAA.png`, status: "failed" }])).toHaveLength(1);
  expect(unexplained(failed, [{ url: aborted, status: "failed" }])).toEqual([]);
  expect(() => {
    watcher.clean();
  }).toThrow();
});

test("a console line of a request that failed is explained by its URL expected as failed, and by nothing else", () => {
  const url = "http://127.0.0.1:1/operator/datasets";
  const line = { kind: "console", text: "Failed to load resource: net::ERR_CONNECTION_REFUSED", url, status: null } as const;
  expect(unexplained([line], [{ url, status: "failed" }])).toEqual([]);
  expect(unexplained([line], [{ url, status: 404 }])).toHaveLength(1);
  expect(unexplained([line], [{ url: `${url}/x`, status: "failed" }])).toHaveLength(1);
  expect(unexplained([{ ...line, text: "Failed to load resource: the server responded with a status of 401 ()" }], [{ url, status: "failed" }])).toHaveLength(1);
  expect(unexplained([{ ...line, text: "Uncaught net::ERR_FAILED" }], [{ url, status: "failed" }])).toHaveLength(1);
});

test("a violation alone fails the watcher, even with no console line", () => {
  const watcher = new Watcher();
  watcher.clean();
  watcher.violations.push({ directive: "require-trusted-types-for", blocked: "trusted-types-sink", source: "" });
  expect(() => {
    watcher.clean();
  }).toThrow("policy violations");
});

/** A seed the page scan is asked to find: no token's shape (the report's scan would fail the run
 * on one), and held by no real server. */
const SEED = "selftest-seed-9f3a1c-for-the-page-scan";

test("the page scan sees a secret kept in storage, the origin private file system or the Cache API", async ({ page, origin }) => {
  await page.goto(`${origin}/curate`);
  const clean = await pageTexts(page);
  expect(occurrences(clean.texts, [SEED])).toBe(0);
  expect(clean.stored).toBe(0);
  const places = ["localStorage", "opfs file name", "opfs file content", "opfs nested file content", "cache name", "cache request", "cache response"] as const;
  for (const place of places) {
    await page.evaluate(
      async ([where, seed]) => {
        if (where === "localStorage") {
          window.localStorage.setItem("k", seed);
        } else if (where.startsWith("opfs")) {
          let directory = await navigator.storage.getDirectory();
          if (where === "opfs nested file content") {
            directory = await directory.getDirectoryHandle("nested", { create: true });
          }
          const name = where === "opfs file name" ? seed : "seed.txt";
          const file = await directory.getFileHandle(name, { create: true });
          const writable = await file.createWritable();
          await writable.write(where === "opfs file name" ? "x" : seed);
          await writable.close();
        } else {
          const cache = await caches.open(where === "cache name" ? seed : "seed");
          await cache.put(where === "cache request" ? `/${seed}` : "/seeded", new Response(where === "cache response" ? seed : "x"));
        }
      },
      [place, SEED] as const,
    );
    const seen = await pageTexts(page);
    expect(occurrences(seen.texts, [SEED]), `the scan sees a seed kept in ${place}`).toBeGreaterThan(0);
    expect(seen.stored, `the scan counts what is kept in ${place}`).toBeGreaterThan(0);
    await page.evaluate(async () => {
      window.localStorage.clear();
      const root = await navigator.storage.getDirectory();
      for await (const [name] of (root as unknown as { entries(): AsyncIterable<[string, unknown]> }).entries()) {
        await root.removeEntry(name, { recursive: true });
      }
      for (const name of await caches.keys()) {
        await caches.delete(name);
      }
    });
    expect(occurrences((await pageTexts(page)).texts, [SEED])).toBe(0);
  }
});

test("a failing API call does not print its credential, in its message or its stack", async ({ request }) => {
  const bearer = "selftest-bearer-7d41c2-not-a-token";
  const closed = "http://127.0.0.1:1/operator/datasets";
  const headers = { Authorization: `Bearer ${bearer}`, "Aibi-CSRF": "selftest-csrf-0b5e" };
  // What Playwright prints, unwrapped: the call log holds the header (the reason for the wrapper).
  const raw = await request.get(closed, { headers, timeout: 5000 }).then(
    () => null,
    (error: unknown) => (error instanceof Error ? error : null),
  );
  expect(raw !== null && `${raw.message}\n${raw.stack ?? ""}`.includes(bearer)).toBe(true);
  const wrapped = await api(request, "GET", closed, { headers, timeout: 5000 }).then(
    () => null,
    (error: unknown) => (error instanceof Error ? error : null),
  );
  expect(wrapped).not.toBeNull();
  const printed = `${wrapped?.name ?? ""}\n${wrapped?.message ?? ""}\n${wrapped?.stack ?? ""}`;
  expect(printed.includes(bearer)).toBe(false);
  expect(printed.includes("selftest-csrf-0b5e")).toBe(false);
  expect(printed).toContain("[redacted]");
  expect(printed).toContain("ECONNREFUSED");
});

test("redaction removes a bearer value, a CSRF value, the shapes and the named secrets, in any case", () => {
  const token = `aibi_${"a".repeat(43)}`;
  const handle = `ses_${"b".repeat(43)}`;
  const text = `  - authorization: BEARER ${token}\n  - Aibi-CSRF: abc123\n  body ${handle} and named-secret-1234`;
  const clean = redacted(text, ["named-secret-1234"]);
  expect([token, handle, "abc123", "named-secret-1234"].map((secret) => clean.includes(secret))).toEqual([false, false, false, false]);
  expect(redacted("nothing to hide: GET /operator/datasets")).toBe("nothing to hide: GET /operator/datasets");
});

test("the report scan reads the run's output: a secret, a shape, or a secret split across two chunks", () => {
  const secret = "selftest-secret-5c0d8e-of-the-run";
  const shaped = `aibi_${"c".repeat(43)}`;
  expect(outputFindings("out", ["a clean line\n", "another\n"], [secret])).toEqual([]);
  expect(outputFindings("out", [`before ${secret} after`], [secret])).toEqual(["out: 1 secret(s), 0 shape(s)"]);
  expect(outputFindings("out", [secret.slice(0, 10), secret.slice(10)], [secret])).toEqual(["out: 1 secret(s), 0 shape(s)"]);
  expect(outputFindings("out", [`Authorization: Bearer ${shaped}`], [secret])).toEqual(["out: 0 secret(s), 1 shape(s)"]);
  expect(outputFindings("out", [secret, "\n", shaped], [secret])).toEqual(["out: 1 secret(s), 1 shape(s)"]);
});

/** A reporter driven by hand: it looks for `SECRET` in an empty directory, and writes nowhere. */
function reporter(): { readonly scan: ScanReporter; readonly done: () => void } {
  const root = mkdtempSync(path.join(os.tmpdir(), "aibi-scan-selftest-"));
  const scan = new ScanReporter({ secrets: [SECRET], root, write: () => undefined });
  return { scan, done: () => { rmSync(root, { recursive: true, force: true }); } };
}

const SECRET = "selftest-secret-e17b4c-in-the-run-output";
const PASSED: FullResult = { status: "passed", startTime: new Date(), duration: 1 };
const TEST = {} as TestCase;
const RESULT = (errors: TestError[]): TestResult => ({ errors }) as unknown as TestResult;

test("the report scan's reporter fails the run on a secret in each place the run printed one (its wiring)", async () => {
  const cycle: TestError = { message: "a cycle" };
  cycle.cause = cycle;
  const deep: TestError = { message: "outer", cause: { message: "middle", cause: { message: "inner", cause: { stack: `the stack of the root cause: ${SECRET}` } } } };
  const places: readonly (readonly [string, (given: ScanReporter) => void])[] = [
    ["stdout", (given) => { given.onStdOut(`worker said ${SECRET}\n`); }],
    ["stdout, a buffer", (given) => { given.onStdOut(Buffer.from(`worker said ${SECRET}\n`)); }],
    ["stderr", (given) => { given.onStdErr(`worker said ${SECRET}\n`); }],
    ["a secret split across two chunks", (given) => { given.onStdOut(SECRET.slice(0, 12)); given.onStdOut(SECRET.slice(12)); }],
    ["a test's error message", (given) => { given.onTestEnd(TEST, RESULT([{ message: `failed with ${SECRET}` }])); }],
    ["a test's error stack", (given) => { given.onTestEnd(TEST, RESULT([{ stack: `at ${SECRET}` }])); }],
    ["a test's error snippet", (given) => { given.onTestEnd(TEST, RESULT([{ snippet: SECRET }])); }],
    ["a test's thrown value", (given) => { given.onTestEnd(TEST, RESULT([{ value: SECRET }])); }],
    ["a test's error cause", (given) => { given.onTestEnd(TEST, RESULT([{ message: "wrapped", cause: { message: SECRET } }])); }],
    ["a test's error cause's cause", (given) => { given.onTestEnd(TEST, RESULT([deep])); }],
    ["a test's error, the second of two", (given) => { given.onTestEnd(TEST, RESULT([{ message: "clean" }, { message: SECRET }])); }],
    ["a step's error", (given) => { given.onStepEnd(TEST, RESULT([]), { error: { message: SECRET } } as TestStep); }],
    ["a step's error cause", (given) => { given.onStepEnd(TEST, RESULT([]), { error: { message: "wrapped", cause: { message: SECRET } } } as TestStep); }],
    ["the runner's error", (given) => { given.onError({ message: SECRET }); }],
    ["the runner's error cause", (given) => { given.onError({ message: "wrapped", cause: { stack: SECRET } }); }],
  ];
  for (const [place, print] of places) {
    const { scan, done } = reporter();
    print(scan);
    expect(await scan.onEnd(PASSED), `a secret in ${place} fails the run`).toEqual({ status: "failed" });
    done();
  }
  const clean = reporter();
  clean.scan.onStdOut("nothing\n");
  clean.scan.onStdErr("nothing\n");
  clean.scan.onTestEnd(TEST, RESULT([{ message: "an error", cause: { message: "its cause" } }, cycle]));
  expect(await clean.scan.onEnd(PASSED)).toBeUndefined();
  expect(await clean.scan.onEnd({ ...PASSED, status: "failed" })).toEqual({ status: "failed" });
  clean.done();
});

test("an error's causes are read to a bounded depth, a cycle once", () => {
  const cycle: TestError = { message: "a" };
  cycle.cause = { message: "b", cause: cycle };
  expect(errorTexts(cycle).filter((text) => text === "a" || text === "b")).toEqual(["a", "b"]);
  let chain: TestError = { message: "bottom" };
  for (let depth = 0; depth < 100; depth += 1) {
    chain = { message: `level ${String(depth)}`, cause: chain };
  }
  const texts = errorTexts(chain);
  expect(texts.length).toBeLessThan(200);
  expect(texts).toContain("level 99");
});

interface Held {
  readonly scratch: string;
  readonly secrets: string;
  readonly server: number;
}

/** What a signalled process did: how it ended, what it left, how many times it stopped its servers,
 * and whether the secrets directory was already gone `early` milliseconds after the first signal. */
interface Interrupted {
  readonly code: number | null;
  readonly signal: NodeJS.Signals | null;
  readonly left: string[];
  readonly teardowns: number;
  readonly secretsGoneEarly: boolean | null;
}

/** Run `interrupt-child.mjs`, signal it as `signals` say (each after its delay, in milliseconds), and
 * give how it ended and what it left: its directories, and its server (alive, as a process id). */
async function interrupted(mode: "wait" | "exit", signals: readonly (readonly [NodeJS.Signals, number])[], early = 0): Promise<Interrupted> {
  const child = spawn(process.execPath, [path.join(import.meta.dirname, "interrupt-child.mjs"), mode], { stdio: ["ignore", "pipe", "inherit"] });
  let output = "";
  let announce: (held: Held) => void = () => undefined;
  const held = new Promise<Held>((resolve) => {
    announce = resolve;
  });
  child.stdout.on("data", (chunk: Buffer) => {
    output += chunk.toString();
    const first = output.indexOf("\n");
    if (first !== -1) {
      announce(JSON.parse(output.slice(0, first)) as Held);
    }
  });
  const ended = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>((resolve) => {
    child.once("exit", (code, signal) => {
      resolve({ code, signal });
    });
  });
  const given = await held;
  let secretsGoneEarly: boolean | null = null;
  for (const [index, [signal, after]] of signals.entries()) {
    await new Promise((resolve) => setTimeout(resolve, after));
    child.kill(signal);
    if (index === 0 && early > 0) {
      await new Promise((resolve) => setTimeout(resolve, early));
      secretsGoneEarly = !existsSync(given.secrets) && alive(given.server);
    }
  }
  const how = await ended;
  // A killed process is reaped a moment after its parent is gone.
  for (let waited = 0; waited < 40 && alive(given.server); waited += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const left = [existsSync(given.scratch) ? "the scratch directory" : "", existsSync(given.secrets) ? "the secrets directory" : "", alive(given.server) ? "the server" : ""].filter((name) => name !== "");
  // Whatever it left is removed here, so that a failure leaves nothing either.
  rmSync(given.scratch, { recursive: true, force: true });
  rmSync(given.secrets, { recursive: true, force: true });
  if (alive(given.server)) {
    process.kill(given.server, "SIGKILL");
  }
  return { ...how, left, teardowns: output.split("\n").filter((line) => line === "teardown").length, secretsGoneEarly };
}

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

test.describe("an interrupted run leaves nothing behind (m12b, m6)", () => {
  test.beforeEach(({ bundle }) => {
    test.skip(bundle !== "prod", "a process of its own, the same for both builds");
  });

  test("a SIGTERM stops the server, removes both directories and exits 143", async () => {
    expect(await interrupted("wait", [["SIGTERM", 400]])).toMatchObject({ code: 143, signal: null, left: [], teardowns: 2 });
  });

  test("a SIGHUP does the same and exits 129", async () => {
    expect(await interrupted("wait", [["SIGHUP", 400]])).toMatchObject({ code: 129, signal: null, left: [], teardowns: 2 });
  });

  test("the secrets directory goes first, while the server is still being stopped", async () => {
    expect(await interrupted("wait", [["SIGTERM", 400]], 300)).toMatchObject({ secretsGoneEarly: true, left: [] });
  });

  test("a second SIGTERM, a second SIGHUP, while the first signal is being handled (a server slow to stop), cut nothing and start no second cleanup", async () => {
    expect(await interrupted("wait", [["SIGTERM", 400], ["SIGTERM", 300], ["SIGHUP", 300], ["SIGHUP", 100]])).toMatchObject({ code: 143, signal: null, left: [], teardowns: 2 });
  });

  test("a SIGHUP twice, the first being handled", async () => {
    expect(await interrupted("wait", [["SIGHUP", 400], ["SIGHUP", 300]])).toMatchObject({ code: 129, signal: null, left: [], teardowns: 2 });
  });

  test("a forced exit removes both directories and kills the server", async () => {
    expect(await interrupted("exit", [])).toMatchObject({ code: 3, signal: null, left: [], teardowns: 0 });
  });
});
