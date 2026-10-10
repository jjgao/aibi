/**
 * The matrix's own checks, tested: the recorder catches a page that breaks the policy, the
 * expected-refusal matcher explains a refusal only by its URL and its status, both, the page scan
 * sees a secret wherever a page can keep one (storage, the origin private file system, the Cache
 * API), a failing API call's error holds no credential, and the report scan reads the run's output.
 */
import { expect, test, unexplained, Watcher } from "./fixtures";
import { api, occurrences, pageTexts, redacted } from "./operator-kit";
import { outputFindings } from "./scan-reporter";

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
