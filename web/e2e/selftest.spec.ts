/**
 * The matrix's own checks, tested: the recorder catches a page that breaks the policy, and the
 * expected-refusal matcher explains a refusal only by its URL and its status, both.
 */
import { expect, test, unexplained, Watcher } from "./fixtures";

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
