/**
 * The operator matrix (D412, D423): the built bundles served by the real server, Chromium, the
 * operator client and its shell. The token is entered by `locator.evaluate` (never `fill`, whose
 * step title holds the value) and is in no storage, address, history, console or markup; every
 * request with a credential goes to this origin's `/operator/`; a handle stands only in the
 * bodies of change, publish and discard; the CSRF token is fetched again once, on CSRF_REQUIRED
 * alone; a 401 locks; the idle lock holds against Playwright's clock (a jump of the system time
 * with no timer run, a suspend); an unknown outcome (an aborted request, a killed server) is
 * never sent again. The flows that need the end-to-end build's harness skip the production build.
 *
 * Two tabs: what is testable without take-over (which waits for the request half, D423) is that a
 * second tab cannot open a session on a dataset another holds (DATASET_BUSY), holds no handle and
 * sends no change, and that the first tab goes on; that the second tab takes the session over is
 * not. A handle made stale by a take-over elsewhere (the CLI's, here Playwright's request
 * context, a client that is not a browser) is dropped on the CONFLICT it meets.
 */
import net from "node:net";

import { expect, test } from "./fixtures";
import { api, checkCredentials, consoleOf, expectOperator, IDLE_LIMIT_MS, noSecrets, record, run, SHELL, status, unlock, WRONG_TOKEN } from "./operator-kit";
import { curatorToken, remember, servers } from "./servers";

const UNLOCKED = SHELL.unlocked;

test.describe("the token's shell", () => {
  test("is locked on load, unlocks with the token, which goes nowhere but /operator/, and forgets it", async ({ page, context, origin, watcher }) => {
    const token = curatorToken();
    const consoled = consoleOf(page);
    const recorded = record(context);
    expectOperator(watcher, origin, [], []);
    await page.goto(`${origin}/curate`);
    await expect(status(page)).toHaveText(SHELL.start);
    const field = page.locator('input[name="token"]');
    expect(await field.evaluate((element) => [element.getAttribute("type"), element.getAttribute("autocomplete"), element.hasAttribute("value")])).toEqual(["password", "off", false]);
    await unlock(page, token);
    await expect(status(page)).toHaveText(UNLOCKED);
    await expect(field).toHaveCount(0);
    await noSecrets(page, consoled, [token]);
    await page.getByRole("button", { name: "Forget the token" }).click();
    await expect(status(page)).toHaveText(SHELL.forgotten);
    expect(await page.locator('input[name="token"]').evaluate((element) => (element instanceof HTMLInputElement ? element.value : "?"))).toBe("");
    const records = await recorded.all();
    checkCredentials(records, origin, token);
    expect(records.filter((request) => request.url.includes("/operator/")).map((request) => `${request.method} ${new URL(request.url).pathname}`)).toEqual(["GET /operator/csrf"]);
    await noSecrets(page, consoled, [token]);
    watcher.clean();
  });

  test("keeps a refused token nowhere: the 401 leaves it locked, the field emptied", async ({ page, context, origin, watcher }) => {
    const consoled = consoleOf(page);
    const recorded = record(context);
    expectOperator(watcher, origin, [["/operator/csrf", 401]], []);
    await page.goto(`${origin}/curate`);
    await unlock(page, WRONG_TOKEN);
    await expect(status(page)).toHaveText(SHELL.token);
    expect(await page.locator('input[name="token"]').evaluate((element) => (element instanceof HTMLInputElement ? [element.value, element.hasAttribute("value")] : []))).toEqual(["", false]);
    checkCredentials(await recorded.all(), origin, WRONG_TOKEN);
    await noSecrets(page, consoled, [WRONG_TOKEN, curatorToken()]);
    watcher.clean();
  });

  test("locks after ten minutes without input, on the wall clock alone (a suspend), on a trusted input, and by its timer", async ({ page, origin, watcher }) => {
    expectOperator(watcher, origin, [], []);
    await page.clock.install();
    await page.goto(`${origin}/curate`);
    const start = await page.evaluate(() => Date.now());
    await unlock(page, curatorToken());
    await expect(status(page)).toHaveText(UNLOCKED);
    // A suspend: the system time jumps, no timer runs; the next input locks rather than counting.
    await page.clock.setSystemTime(start + IDLE_LIMIT_MS + 1000);
    await page.mouse.click(2, 2);
    await expect(status(page)).toHaveText(SHELL.idle);
    // The wall clock set back locks too.
    await unlock(page, curatorToken());
    await expect(status(page)).toHaveText(UNLOCKED);
    await page.clock.setSystemTime(start);
    await page.mouse.click(2, 2);
    await expect(status(page)).toHaveText(SHELL.idle);
    // The timer: time that runs locks with no input at all.
    await unlock(page, curatorToken());
    await expect(status(page)).toHaveText(UNLOCKED);
    await page.clock.runFor(IDLE_LIMIT_MS + 20_000);
    await expect(status(page)).toHaveText(SHELL.idle);
    watcher.clean();
  });
});

test.describe("the operator client, driven by the harness", () => {
  test.beforeEach(({ bundle }) => {
    test.skip(bundle !== "e2e", "the harness is in the end-to-end build alone");
  });

  test("reads, opens, changes and publishes a session; the handle only in the bodies; every answer decoded", async ({ page, context, origin, watcher }) => {
    const token = curatorToken();
    const consoled = consoleOf(page);
    const recorded = record(context);
    const received: string[] = [];
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.origin === origin && /^\/(?:api|operator)\//u.test(url.pathname)) {
        received.push(url.pathname);
      }
    });
    const base = "/operator/datasets/flow";
    expectOperator(watcher, origin, [], ["/operator/datasets", base, `${base}/queue`, `${base}/session/open`, `${base}/session/change`, `${base}/session/publish`]);
    await page.goto(`${origin}/curate`);
    await unlock(page, token);
    await expect(status(page)).toHaveText(UNLOCKED);
    expect(await run(page, "datasets")).toMatchObject({ kind: "answer", status: "200" });
    expect(await run(page, "dataset", { dataset: "flow" })).toMatchObject({ kind: "answer", status: "200", holds: "no" });
    expect(await run(page, "queue")).toMatchObject({ kind: "answer", status: "200" });
    expect(await run(page, "open")).toMatchObject({ kind: "answer", status: "200", holds: "yes" });
    expect(await run(page, "confirm", { descriptor: "items" })).toMatchObject({ kind: "answer", status: "200", holds: "yes" });
    const published = await run(page, "publish");
    expect(published).toMatchObject({ kind: "answer", status: "200", holds: "no" });
    expect(published.decoded).toBe(String(received.length));
    const records = await recorded.all();
    checkCredentials(records, origin, token);
    const operations = records.filter((request) => new URL(request.url).pathname.startsWith("/operator/")).map((request) => `${request.method} ${new URL(request.url).pathname}`);
    expect(operations).toEqual(["GET /operator/csrf", "GET /operator/datasets", `GET ${base}`, `GET ${base}/queue`, `POST ${base}/session/open`, `POST ${base}/session/change`, `POST ${base}/session/publish`]);
    const handles = new Set(records.flatMap((request) => [...(request.body ?? "").matchAll(/ses_[A-Za-z0-9_-]{43}/gu)].map((found) => found[0])));
    expect(handles.size).toBe(1);
    const change = records.find((request) => request.url.endsWith("/session/change"));
    const parsed = JSON.parse(change?.body ?? "{}") as Record<string, unknown>;
    expect(Object.keys(parsed)).toEqual(["handle", "expected", "edits"]);
    expect(parsed["edits"]).toEqual([{ op: "confirm", descriptor: "items" }]);
    await noSecrets(page, consoled, [token, ...handles, ...records.flatMap((request) => request.headers["aibi-csrf"] ?? [])]);
    watcher.clean();
  });

  test("withdraws a release by its manifest, never a label", async ({ page, context, origin, watcher, request }) => {
    const token = curatorToken();
    const headers = { Authorization: `Bearer ${token}`, "Aibi-Operator": "e2e" };
    const state = (await (await api(request, "GET", `${origin}/operator/datasets/withdrawn`, { headers })).json()) as { labels: { manifest: string; status: string }[] };
    const manifest = state.labels[0]?.manifest ?? "";
    const recorded = record(context);
    expectOperator(watcher, origin, [], ["/operator/datasets/withdrawn/withdraw"]);
    await page.goto(`${origin}/curate`);
    await unlock(page, token);
    await expect(status(page)).toHaveText(UNLOCKED);
    expect(await run(page, "withdraw", { dataset: "withdrawn", manifest })).toMatchObject({ kind: "answer", status: "200" });
    const after = (await (await api(request, "GET", `${origin}/operator/datasets/withdrawn`, { headers })).json()) as { labels: { status: string }[] };
    expect(after.labels.map((label) => label.status)).toEqual(["withdrawn"]);
    const records = await recorded.all();
    checkCredentials(records, origin, token);
    expect(records.filter((found) => found.url.endsWith("/withdraw")).map((found) => found.body)).toEqual([JSON.stringify({ release: manifest })]);
    expect(await run(page, "withdraw", { manifest: "1" })).toMatchObject({ kind: "invalid", what: "manifest" });
    watcher.clean();
  });

  test("two tabs, without take-over: the second cannot open the session the first holds, and the first goes on", async ({ page, context, origin, watcher }) => {
    const token = curatorToken();
    const recorded = record(context);
    const base = "/operator/datasets/tabs";
    expectOperator(watcher, origin, [[`${base}/session/open`, 409]], [`${base}/session/change`, `${base}/session/discard`]);
    await page.goto(`${origin}/curate`);
    await unlock(page, token);
    await expect(status(page)).toHaveText(UNLOCKED);
    expect(await run(page, "open", { dataset: "tabs" })).toMatchObject({ kind: "answer", holds: "yes" });
    const second = await context.newPage();
    await second.goto(`${origin}/curate`);
    await unlock(second, token);
    await expect(status(second)).toHaveText(UNLOCKED);
    expect(await run(second, "open", { dataset: "tabs" })).toMatchObject({ kind: "refused", status: "409", codes: "DATASET_BUSY", holds: "no" });
    const before = (await recorded.all()).length;
    expect(await run(second, "confirm", { descriptor: "items" })).toMatchObject({ kind: "invalid", what: "session" });
    expect((await recorded.all()).length).toBe(before);
    expect(await run(page, "confirm", { descriptor: "items" })).toMatchObject({ kind: "answer", status: "200", holds: "yes" });
    expect(await run(page, "discard")).toMatchObject({ kind: "answer", holds: "no" });
    checkCredentials(await recorded.all(), origin, token);
    watcher.clean();
  });

  test("drops a handle that a take-over elsewhere made stale, on the CONFLICT it meets", async ({ page, context, origin, watcher, request }) => {
    const token = curatorToken();
    const recorded = record(context);
    const base = "/operator/datasets/conflict";
    expectOperator(watcher, origin, [[`${base}/session/change`, 409]], [`${base}/session/open`]);
    await page.goto(`${origin}/curate`);
    await unlock(page, token);
    await expect(status(page)).toHaveText(UNLOCKED);
    expect(await run(page, "open", { dataset: "conflict" })).toMatchObject({ kind: "answer", holds: "yes" });
    const cli = { Authorization: `Bearer ${token}`, "Aibi-Operator": "cli" };
    const taken = await api(request, "POST", `${origin}${base}/session/take-over`, { headers: cli, data: {} });
    expect(taken.status()).toBe(200);
    const { handle } = (await taken.json()) as { handle: string };
    remember(handle);
    expect(await run(page, "confirm", { descriptor: "items" })).toMatchObject({ kind: "refused", status: "409", codes: "CONFLICT", holds: "no" });
    const before = (await recorded.all()).length;
    expect(await run(page, "confirm")).toMatchObject({ kind: "invalid", what: "session" });
    expect((await recorded.all()).length).toBe(before);
    checkCredentials(await recorded.all(), origin, token);
    const draft = ((await (await api(request, "GET", `${origin}${base}`, { headers: cli })).json()) as { session: { draft: string } }).session.draft;
    const discarded = await api(request, "POST", `${origin}${base}/session/discard`, { headers: cli, data: { handle, expected: draft } });
    expect(discarded.status()).toBe(200);
    watcher.clean();
  });

  test("fetches the CSRF token again and sends once more on CSRF_REQUIRED alone, the same body", async ({ page, context, origin, watcher }) => {
    const token = curatorToken();
    const recorded = record(context);
    const withdraw = "/operator/datasets/library/withdraw";
    const manifest = `sha256:${"0".repeat(64)}`;
    const refusal = (code: string) => ({ status: 403, contentType: "application/json", body: JSON.stringify({ refusals: [{ code, path: null, message: [{ text: "CSRF_REQUIRED" }], alternatives: [] }] }) });
    expectOperator(watcher, origin, [[withdraw, 403], [withdraw, 404]], []);
    await page.goto(`${origin}/curate`);
    await unlock(page, token);
    await expect(status(page)).toHaveText(UNLOCKED);
    const cases: readonly (readonly [string, (call: number) => string | null, number, number, string])[] = [
      ["CSRF_REQUIRED once, then the server", (call) => (call === 1 ? "CSRF_REQUIRED" : null), 2, 1, "UNKNOWN_RELEASE"],
      ["CSRF_REQUIRED every time", () => "CSRF_REQUIRED", 2, 1, "CSRF_REQUIRED"],
      ["ORIGIN_NOT_ALLOWED", () => "ORIGIN_NOT_ALLOWED", 1, 0, "ORIGIN_NOT_ALLOWED"],
    ];
    for (const [, answer, posts, refetches, code] of cases) {
      let call = 0;
      await page.route(`${origin}${withdraw}`, async (route) => {
        call += 1;
        const given = answer(call);
        await (given === null ? route.continue() : route.fulfill(refusal(given)));
      });
      const before = (await recorded.all()).length;
      const outcome = await run(page, "withdraw", { dataset: "library", manifest });
      expect(outcome).toMatchObject({ kind: "refused", codes: code });
      const sent = (await recorded.all()).slice(before);
      const bodies = sent.filter((request) => request.url.endsWith(withdraw)).map((request) => request.body);
      expect(bodies).toEqual(Array.from({ length: posts }, () => JSON.stringify({ release: manifest })));
      expect(sent.filter((request) => request.url.endsWith("/operator/csrf")).length).toBe(refetches);
      await page.unroute(`${origin}${withdraw}`);
    }
    checkCredentials(await recorded.all(), origin, token);
    watcher.clean();
  });

  test("a 401 locks an unlocked client: nothing more is sent", async ({ page, context, origin, watcher }) => {
    const recorded = record(context);
    expectOperator(watcher, origin, [["/operator/datasets", 401]], []);
    await page.goto(`${origin}/curate`);
    await unlock(page, curatorToken());
    await expect(status(page)).toHaveText(UNLOCKED);
    await page.route(`${origin}/operator/datasets`, (route) =>
      route.fulfill({ status: 401, contentType: "application/json", headers: { "WWW-Authenticate": 'Bearer realm="aibi-operator"' }, body: JSON.stringify({ refusals: [{ code: "TOKEN_REQUIRED", path: null, message: [], alternatives: [] }] }) }),
    );
    expect(await run(page, "datasets")).toMatchObject({ kind: "locked", reason: "token" });
    await expect(status(page)).toHaveText(SHELL.token);
    const before = (await recorded.all()).length;
    expect(await run(page, "dataset", { dataset: "library" })).toMatchObject({ kind: "locked" });
    expect((await recorded.all()).length).toBe(before);
    watcher.clean();
  });

  test("the idle lock before a request: a trusted input is activity; a synthetic input and a request are not", async ({ page, context, origin, watcher }) => {
    const recorded = record(context);
    expectOperator(watcher, origin, [], ["/operator/datasets"]);
    await page.clock.install();
    await page.goto(`${origin}/curate`);
    const start = await page.evaluate(() => Date.now());
    await unlock(page, curatorToken());
    await expect(status(page)).toHaveText(UNLOCKED);
    const minutes = (count: number) => start + count * 60_000;
    // Every request below is pressed by the page's own `click()` (an event that is no activity), so
    // that the only activity is what the test makes. Nine minutes, a trusted click, nine more: the
    // request goes, nine minutes after the input (and eighteen after the unlock).
    await page.clock.setSystemTime(minutes(9));
    await page.mouse.click(2, 2);
    await page.clock.setSystemTime(minutes(18));
    expect(await run(page, "datasets", {}, "script")).toMatchObject({ kind: "answer" });
    // Half a minute on, a synthetic key press (no activity: the client is still inside the limit, so
    // that a synthetic input that counted would extend it), and one minute later a request: 10.5
    // minutes after the trusted input, 1.5 after the earlier request, 1 after the synthetic one. A
    // request that counted as activity, or a synthetic input, would keep the client unlocked; it is
    // locked.
    await page.clock.setSystemTime(minutes(18.5));
    await page.evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" })));
    await page.clock.setSystemTime(minutes(19.5));
    const before = (await recorded.all()).length;
    expect(await run(page, "datasets", {}, "script")).toMatchObject({ kind: "locked", reason: "idle" });
    expect((await recorded.all()).length).toBe(before);
    await expect(status(page)).toHaveText(SHELL.idle);
    watcher.clean();
  });

  test("an aborted request is an unknown outcome, never sent again", async ({ page, context, origin, watcher }) => {
    const recorded = record(context);
    const open = "/operator/datasets/library/session/open";
    expectOperator(watcher, origin, [], [open, "/operator/datasets"]);
    await page.goto(`${origin}/curate`);
    await unlock(page, curatorToken());
    await expect(status(page)).toHaveText(UNLOCKED);
    await page.route(`${origin}${open}`, (route) => route.abort());
    await page.route(`${origin}/operator/datasets`, (route) => route.abort("connectionreset"));
    expect(await run(page, "open", { dataset: "library" })).toMatchObject({ kind: "unknown", reason: "network", holds: "no" });
    expect(await run(page, "datasets")).toMatchObject({ kind: "unknown", reason: "network" });
    const sent = await recorded.all();
    expect(sent.filter((request) => request.url.endsWith(open)).length).toBe(1);
    expect(sent.filter((request) => request.url.endsWith("/operator/datasets")).length).toBe(1);
    await expect(status(page)).toHaveText(UNLOCKED);
    watcher.clean();
  });

  test("a request that reached the server, held, and a lock that follows: the abort is real, the outcome unknown, the request sent once", async ({ page, context, origin, watcher }) => {
    const recorded = record(context);
    const held = "/operator/datasets";
    expectOperator(watcher, origin, [], [held]);
    const failures: string[] = [];
    page.on("requestfailed", (request) => failures.push(`${request.url()} ${request.failure()?.errorText ?? ""}`));
    await page.goto(`${origin}/curate`);
    await unlock(page, curatorToken());
    await expect(status(page)).toHaveText(UNLOCKED);
    let reached = (): void => undefined;
    const arrived = new Promise<void>((resolve) => {
      reached = resolve;
    });
    let release = (): void => undefined;
    const released = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`${origin}${held}`, async (route) => {
      const answer = await route.fetch();
      reached();
      await released;
      try {
        await route.fulfill({ response: answer });
      } catch {
        // the page aborted the request meanwhile: that is the case under test
      }
    });
    const output = page.locator('output[aria-label="outcome"]');
    const runs = Number(await output.getAttribute("data-runs"));
    await page.getByRole("button", { name: "datasets", exact: true }).click();
    // The server has the request and has answered it; the page has not seen the answer.
    await arrived;
    await page.getByRole("button", { name: "Forget the token" }).click();
    await expect(output).toHaveAttribute("data-runs", String(runs + 1));
    expect({ kind: await output.getAttribute("data-kind"), reason: await output.getAttribute("data-reason") }).toEqual({ kind: "unknown", reason: "aborted" });
    await expect(status(page)).toHaveText(SHELL.forgotten);
    release();
    await expect.poll(() => failures.filter((failure) => failure.startsWith(`${origin}${held} `))).toEqual([`${origin}${held} net::ERR_ABORTED`]);
    expect((await recorded.all()).filter((request) => request.url.endsWith(held)).length).toBe(1);
    watcher.clean();
  });

  test("a server killed before a request is sent: a refused connection is an unknown outcome (network), the request sent once", async ({ page, context, watcher }) => {
    const { origin, port, pid } = servers().mortal;
    const recorded = record(context);
    watcher.expect(`${origin}/favicon.ico`, 404);
    watcher.expect(`${origin}/favicon.ico`, "failed");
    expectOperator(watcher, origin, [], ["/operator/datasets"]);
    await page.goto(`${origin}/curate`);
    await unlock(page, curatorToken());
    await expect(status(page)).toHaveText(UNLOCKED);
    await page.route(`${origin}/operator/datasets`, async (route) => {
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        // already gone
      }
      for (let tries = 0; tries < 200; tries += 1) {
        const open = await new Promise<boolean>((resolve) => {
          const socket = net.connect(port, "127.0.0.1");
          socket.once("connect", () => {
            socket.destroy();
            resolve(true);
          });
          socket.once("error", () => {
            resolve(false);
          });
        });
        if (!open) {
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
      await route.continue();
    });
    expect(await run(page, "datasets")).toMatchObject({ kind: "unknown", reason: "network" });
    expect((await recorded.all()).filter((request) => request.url.endsWith("/operator/datasets")).length).toBe(1);
    watcher.clean();
  });
});
