/**
 * What `global-setup.ts` started, as the tests read it: the real `aibi-server` serving each
 * built bundle on a loopback port, and a second origin, another site's pages, on another port.
 */
export type Bundle = "prod" | "e2e";

export interface Servers {
  /** Each build's server: its origin (`http://127.0.0.1:<port>`) and its bundle directory. */
  readonly bundles: Record<Bundle, { readonly origin: string; readonly dir: string; readonly port: number }>;
  /** The other site's port; its pages are at `http://localhost:<port>` (another site than
   * `127.0.0.1`) and at `http://127.0.0.1:<port>` (the same site, another origin). */
  readonly otherPort: number;
}

export const SERVERS_VARIABLE = "AIBI_E2E_SERVERS";

export function servers(): Servers {
  const found = process.env[SERVERS_VARIABLE];
  if (found === undefined) {
    throw new Error("The end-to-end servers are not running: run the tests with Playwright's global setup");
  }
  return JSON.parse(found) as Servers;
}
