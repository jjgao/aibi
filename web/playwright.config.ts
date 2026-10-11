/**
 * The policy matrix (D411, D412, D418): Chromium against the BUILT bundles served by the real
 * `aibi-server` (`e2e/global-setup.ts`), never Vite's dev server, which injects an inline
 * preamble. Two projects run the same tests: `prod` serves `dist/`, the bundle that ships, and
 * `e2e` serves `dist-e2e/`, the same source with the harness. No traces, screenshots or videos:
 * a trace holds request headers, where M5.1c-1c's token travels.
 *
 * Locally, `AIBI_CHROMIUM` names a Chromium to run instead of the one `playwright install`
 * downloads (CI downloads its own).
 */
import { defineConfig, devices } from "@playwright/test";

import type { Bundle } from "./e2e/servers";

const chromium = process.env["AIBI_CHROMIUM"];

export default defineConfig<{ bundle: Bundle }>({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  forbidOnly: process.env["CI"] !== undefined,
  retries: 0,
  workers: 4,
  timeout: 30_000,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    ...devices["Desktop Chrome"],
    trace: "off",
    screenshot: "off",
    video: "off",
    ...(chromium === undefined ? {} : { launchOptions: { executablePath: chromium } }),
  },
  projects: [
    { name: "prod", use: { bundle: "prod" } },
    { name: "e2e", use: { bundle: "e2e" } },
  ],
});
