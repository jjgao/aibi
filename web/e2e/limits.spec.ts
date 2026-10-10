/**
 * The real `token_failures` limit (D261, D423) on a server of its own (`servers.ts`'s `limits`:
 * the bucket is per address and loopback is shared, so the main servers raise it, and only this
 * test spends a default one, ten a minute with a burst of ten). Ten refused tokens are refused
 * with a 401 each; the eleventh is a 429 naming `token_failures`, which the client takes as it
 * takes a 401 (locked, nothing kept); the curator token itself is still accepted (a request with
 * it never spends the bucket). Serial: one test a project, each project its own server.
 */
import { expect, test } from "./fixtures";
import { checkCredentials, consoleOf, expectOperator, noSecrets, record, SHELL, status, unlock, WRONG_TOKEN } from "./operator-kit";
import { curatorToken, servers } from "./servers";

test.describe.configure({ mode: "serial" });

test("ten refused tokens, then the limit, which locks as a refused token does; the token still unlocks", async ({ page, context, bundle, watcher }) => {
  const { origin } = servers().limits[bundle];
  const consoled = consoleOf(page);
  const recorded = record(context);
  watcher.expect(`${origin}/favicon.ico`, 404);
  watcher.expect(`${origin}/favicon.ico`, "failed");
  expectOperator(watcher, origin, [["/operator/csrf", 401], ["/operator/csrf", 429]], []);
  await page.goto(`${origin}/curate`);
  for (let attempt = 1; attempt <= 10; attempt += 1) {
    await unlock(page, WRONG_TOKEN);
    await expect(status(page)).toHaveText(SHELL.token);
  }
  await unlock(page, WRONG_TOKEN);
  await expect(status(page)).toHaveText(SHELL.failures);
  await unlock(page, curatorToken());
  await expect(status(page)).toHaveText(SHELL.unlocked);
  const records = (await recorded.all()).filter((request) => request.url.endsWith("/operator/csrf"));
  expect(records.length).toBe(12);
  checkCredentials(records.slice(0, 11), origin, WRONG_TOKEN);
  checkCredentials(records.slice(11), origin, curatorToken());
  await noSecrets(page, consoled, [curatorToken(), WRONG_TOKEN]);
  watcher.clean();
});
