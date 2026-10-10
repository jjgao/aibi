/**
 * What `global-setup.ts` started, as the tests read it: the real `aibi-server` serving each
 * built bundle on a loopback port, and a second origin, another site's pages, on another port;
 * for the operator matrix (D423), a server of each bundle with the default `token_failures`
 * limit (`limits`), a server the matrix kills (`mortal`), and where the curator token is kept (a
 * 0600 file, never in this JSON, never printed).
 */
import { appendFileSync, readFileSync } from "node:fs";

export type Bundle = "prod" | "e2e";

export interface Served {
  readonly origin: string;
  readonly dir: string;
  readonly port: number;
}

export interface Servers {
  /** Each build's server: its origin (`http://127.0.0.1:<port>`) and its bundle directory. */
  readonly bundles: Record<Bundle, Served>;
  /** Each build's server with the default `token_failures` limit (10 a minute, a burst of 10),
   * which one serial test spends: the bucket is per address, and loopback is shared. */
  readonly limits: Record<Bundle, Served>;
  /** The end-to-end build's server that the matrix kills (its process id). */
  readonly mortal: Served & { readonly pid: number };
  /** The other site's port; its pages are at `http://localhost:<port>` (another site than
   * `127.0.0.1`) and at `http://127.0.0.1:<port>` (the same site, another origin). */
  readonly otherPort: number;
  /** The file that holds the curator token (mode 0600, in a directory of its own). */
  readonly tokenFile: string;
  /** The file the tests add every secret they saw to (handles, CSRF tokens), one a line, so that
   * the report's scan (`scan-reporter.ts`) looks for each. */
  readonly secretsFile: string;
}

export const SERVERS_VARIABLE = "AIBI_E2E_SERVERS";

/** The scanning reporter's mark: set, the report's scan removes the token's directory after the
 * run, and the teardown leaves it. */
export const SCANNING_VARIABLE = "AIBI_E2E_SCANNING";

export function servers(): Servers {
  const found = process.env[SERVERS_VARIABLE];
  if (found === undefined) {
    throw new Error("The end-to-end servers are not running: run the tests with Playwright's global setup");
  }
  return JSON.parse(found) as Servers;
}

/** The curator token the servers were configured with (never printed). */
export function curatorToken(): string {
  return readFileSync(servers().tokenFile, "utf8");
}

/** Add secrets the test saw to the report scan's list. */
export function remember(...secrets: readonly string[]): void {
  const given = secrets.filter((secret) => secret !== "");
  if (given.length > 0) {
    appendFileSync(servers().secretsFile, given.map((secret) => `${secret}\n`).join(""), { mode: 0o600 });
  }
}
