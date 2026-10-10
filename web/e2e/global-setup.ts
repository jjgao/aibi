/**
 * Start what the matrix needs, and give back how to stop it: the real `aibi-server` (the
 * server's own virtual environment, `server/.venv`, which `uv sync` makes) serving each built
 * bundle (`dist/` and `dist-e2e/`, built before) on a free loopback port, with a temporary
 * configuration whose rates are raised so that reloads meet no limit (`token_failures` too: the
 * matrix spends it, D423); and another site's pages on another port. For the operator matrix
 * (D423): each main server holds the datasets `DATASETS` (the lending library and small CSV
 * datasets, one for each test that opens a session, since tests run in parallel), imported
 * through the operator router; a server of each bundle keeps the default `token_failures` limit
 * (`limits`), and one more end-to-end server is there to be killed (`mortal`). Each server is
 * started, and stopped, by its own PID.
 *
 * **The curator token** `aibi-server new-token` makes is never printed: its hash goes into each
 * configuration, and the token into a file of mode 0600 in a directory of its own (mode 0700),
 * which the tests read (`servers.ts`'s `curatorToken`) and the report's scan
 * (`scan-reporter.ts`) reads after the run, then removes; without that reporter, the teardown
 * removes it.
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { closeSync, cpSync, mkdirSync, mkdtempSync, openSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

import { guardInterrupts } from "./interrupt.mjs";
import { otherOrigin } from "./other-origin";
import { type Bundle, SCANNING_VARIABLE, SERVERS_VARIABLE, type Servers } from "./servers";

const WEB = path.join(import.meta.dirname, "..");
const SERVER = path.join(WEB, "..", "server");
const EXECUTABLE = path.join(SERVER, ".venv", "bin", "aibi-server");
const BUNDLES: Record<Bundle, string> = { prod: path.join(WEB, "dist"), e2e: path.join(WEB, "dist-e2e") };
const RATE = "{ per_minute = 60000, burst = 10000 }";
const FIXTURES = path.join(WEB, "..", "fixtures");

/** The datasets each main server holds: the lending library (`fixtures/library`, without its
 * formats and README), and a small CSV dataset for each test that opens a session. */
export const DATASETS = ["library", "flow", "tabs", "conflict", "withdrawn"] as const;


async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close(() => {
        if (address === null || typeof address === "string") {
          reject(new Error("no port"));
        } else {
          resolve(address.port);
        }
      });
    });
  });
}

/** A fresh curator token and its hash, read from `aibi-server new-token`'s output, which is
 * never shown. */
function newToken(): { token: string; hash: string } {
  const run = spawnSync(EXECUTABLE, ["new-token"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  const hash = /^token_hash = "(sha256:[0-9a-f]{64})"$/mu.exec(run.stdout)?.[1];
  const token = /(?<![A-Za-z0-9_-])(aibi_[A-Za-z0-9_-]{43})(?![A-Za-z0-9_-])/u.exec(run.stdout)?.[1];
  if (run.status !== 0 || hash === undefined || token === undefined) {
    throw new Error(`aibi-server new-token failed (exit ${String(run.status)}); its output is not shown`);
  }
  return { token, hash };
}

/** The rates a server raises: every class, or every class but the operator's and the token
 * failures' (a `limits` server). */
type Rates = "raised" | "limits";

function configuration(scratch: string, name: string, bundle: Bundle, port: number, hash: string, rates: Rates): string {
  const data = path.join(scratch, name, "data");
  const imports = path.join(scratch, name, "imports");
  mkdirSync(data, { recursive: true });
  mkdirSync(imports, { recursive: true });
  const file = path.join(scratch, name, "aibi.toml");
  writeFileSync(
    file,
    [
      "[server]",
      'bind = "127.0.0.1"',
      `port = ${String(port)}`,
      `web_bundle = ${JSON.stringify(BUNDLES[bundle])}`,
      "[server.rates]",
      `api = ${RATE}`,
      `page = ${RATE}`,
      `assets = ${RATE}`,
      ...(rates === "raised" ? [`operator = ${RATE}`, `token_failures = ${RATE}`] : []),
      "[curator]",
      `token_hash = "${hash}"`,
      "[storage]",
      `data = ${JSON.stringify(data)}`,
      `imports = [${JSON.stringify(imports)}]`,
      "",
    ].join("\n"),
    { mode: 0o600 },
  );
  return file;
}

async function stop(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null || child.pid === undefined) {
    return;
  }
  const exited = new Promise<void>((resolve) => child.once("exit", () => { resolve(); }));
  process.kill(child.pid, "SIGTERM");
  const late = setTimeout(() => {
    if (child.pid !== undefined && child.exitCode === null) {
      process.kill(child.pid, "SIGKILL");
    }
  }, 15_000);
  await exited;
  clearTimeout(late);
}

async function ready(origin: string, child: ChildProcess, log: string): Promise<void> {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`aibi-server exited with ${String(child.exitCode)} before serving; see ${log}`);
    }
    try {
      const answer = await fetch(`${origin}/`);
      if (answer.status === 200) {
        return;
      }
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`aibi-server did not serve ${origin} in time; see ${log}`);
}

/** The datasets of `DATASETS`, imported into a server through the operator router with the
 * token (Node's `fetch` sends fetch metadata, so it is a browser's request: it fetches the CSRF
 * token first). */
async function importDatasets(origin: string, imports: string, token: string): Promise<void> {
  const credentials = { Authorization: `Bearer ${token}`, "Aibi-Operator": "e2e-setup" };
  const csrf = await fetch(`${origin}/operator/csrf`, { headers: credentials });
  const { csrf: key } = (await csrf.json()) as { csrf: string };
  for (const dataset of DATASETS) {
    const source = path.join(imports, dataset);
    if (dataset === "library") {
      cpSync(path.join(FIXTURES, "library"), source, { recursive: true, filter: (from) => !from.endsWith(".md") && path.basename(from) !== "formats" });
    } else {
      mkdirSync(source);
      writeFileSync(path.join(source, "items.csv"), "id,label\n1,a\n2,b\n3,c\n");
    }
    const answer = await fetch(`${origin}/operator/datasets/${dataset}/import`, {
      method: "POST",
      headers: { ...credentials, "Aibi-CSRF": key, "Content-Type": "application/json" },
      body: JSON.stringify({ source: { path: source } }),
    });
    await answer.body?.cancel();
    if (answer.status !== 200) {
      throw new Error(`importing ${dataset} gave ${String(answer.status)}`);
    }
  }
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-e2e-"));
  const secrets = mkdtempSync(path.join(os.tmpdir(), "aibi-e2e-secrets-"));
  const children: ChildProcess[] = [];
  const other = otherOrigin();
  const teardown = async (keep = false): Promise<void> => {
    await Promise.all(children.map(stop));
    await new Promise<void>((resolve) => other.close(() => { resolve(); }));
    if (!keep && process.env["AIBI_E2E_KEEP"] === undefined) {
      rmSync(scratch, { recursive: true, force: true });
    }
    if (process.env[SCANNING_VARIABLE] !== "1") {
      rmSync(secrets, { recursive: true, force: true });
    }
  };
  // The secrets directory holds the curator token (mode 0600): `interrupt.mjs` says what an
  // interrupted run leaves behind, which is nothing.
  let keepScratch = false;
  const guard = guardInterrupts({
    secrets,
    scratch,
    running: () => children.filter((child) => child.exitCode === null && child.signalCode === null),
    teardown: () => teardown(),
    keepScratch: () => keepScratch || process.env["AIBI_E2E_KEEP"] !== undefined,
  });
  try {
    const { token, hash } = newToken();
    const tokenFile = path.join(secrets, "token");
    const secretsFile = path.join(secrets, "seen");
    writeFileSync(tokenFile, token, { mode: 0o600 });
    writeFileSync(secretsFile, "", { mode: 0o600 });
    const start = async (name: string, bundle: Bundle, rates: Rates): Promise<{ served: { origin: string; dir: string; port: number }; pid: number }> => {
      const port = await freePort();
      if (guard.closing()) {
        throw new Error("The end-to-end setup was interrupted");
      }
      const config = configuration(scratch, name, bundle, port, hash, rates);
      const log = path.join(scratch, name, "serve.log");
      const out = openSync(log, "w");
      const child = spawn(EXECUTABLE, ["serve", "--config", config], { cwd: SERVER, stdio: ["ignore", out, out] });
      closeSync(out);
      children.push(child);
      const origin = `http://127.0.0.1:${String(port)}`;
      await ready(origin, child, log);
      return { served: { origin, dir: BUNDLES[bundle], port }, pid: child.pid ?? 0 };
    };
    const bundles = {} as Record<Bundle, { origin: string; dir: string; port: number }>;
    const limits = {} as Record<Bundle, { origin: string; dir: string; port: number }>;
    for (const bundle of ["prod", "e2e"] as const) {
      bundles[bundle] = (await start(bundle, bundle, "raised")).served;
      await importDatasets(bundles[bundle].origin, path.join(scratch, bundle, "imports"), token);
      limits[bundle] = (await start(`${bundle}-limits`, bundle, "limits")).served;
    }
    const mortal = await start("mortal", "e2e", "raised");
    const otherPort = await freePort();
    await new Promise<void>((resolve) => other.listen(otherPort, "127.0.0.1", () => { resolve(); }));
    const found: Servers = { bundles, limits, mortal: { ...mortal.served, pid: mortal.pid }, otherPort, tokenFile, secretsFile };
    process.env[SERVERS_VARIABLE] = JSON.stringify(found);
  } catch (error) {
    // The error names a serve.log under the scratch directory (the log holds the token's hash,
    // never the token): a failed start keeps it.
    keepScratch = !guard.closing();
    await teardown(keepScratch);
    guard.release();
    throw error;
  }
  return async () => {
    await teardown();
    guard.release();
  };
}
