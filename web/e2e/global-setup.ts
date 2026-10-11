/**
 * Start what the matrix needs, and give back how to stop it: the real `aibi-server` (the
 * server's own virtual environment, `server/.venv`, which `uv sync` makes) serving each built
 * bundle (`dist/` and `dist-e2e/`, built before) on a free loopback port, with a temporary
 * configuration whose rates are raised so that reloads meet no limit; and another site's pages
 * on another port. Each server is started, and stopped, by its own PID. The curator token
 * `aibi-server new-token` makes is never printed: only its hash is kept, in the configuration.
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { closeSync, mkdirSync, mkdtempSync, openSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

import { otherOrigin } from "./other-origin";
import { type Bundle, SERVERS_VARIABLE, type Servers } from "./servers";

const WEB = path.join(import.meta.dirname, "..");
const SERVER = path.join(WEB, "..", "server");
const EXECUTABLE = path.join(SERVER, ".venv", "bin", "aibi-server");
const BUNDLES: Record<Bundle, string> = { prod: path.join(WEB, "dist"), e2e: path.join(WEB, "dist-e2e") };
const RATE = "{ per_minute = 60000, burst = 10000 }";

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

/** The hash of a fresh curator token; the token itself is dropped, never printed. */
function tokenHash(): string {
  const run = spawnSync(EXECUTABLE, ["new-token"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  const found = /^token_hash = "(sha256:[0-9a-f]{64})"$/mu.exec(run.stdout);
  if (run.status !== 0 || found?.[1] === undefined) {
    throw new Error(`aibi-server new-token failed (exit ${String(run.status)}); its output is not shown`);
  }
  return found[1];
}

function configuration(scratch: string, bundle: Bundle, port: number, hash: string): string {
  const data = path.join(scratch, bundle, "data");
  const imports = path.join(scratch, bundle, "imports");
  mkdirSync(data, { recursive: true });
  mkdirSync(imports, { recursive: true });
  const file = path.join(scratch, bundle, "aibi.toml");
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
      `operator = ${RATE}`,
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

export default async function globalSetup(): Promise<() => Promise<void>> {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-e2e-"));
  const children: ChildProcess[] = [];
  const other = otherOrigin();
  const teardown = async (keep = false): Promise<void> => {
    await Promise.all(children.map(stop));
    await new Promise<void>((resolve) => other.close(() => { resolve(); }));
    if (!keep && process.env["AIBI_E2E_KEEP"] === undefined) {
      rmSync(scratch, { recursive: true, force: true });
    }
  };
  try {
    const hash = tokenHash();
    const bundles = {} as Record<Bundle, { origin: string; dir: string; port: number }>;
    for (const bundle of ["prod", "e2e"] as const) {
      const port = await freePort();
      const config = configuration(scratch, bundle, port, hash);
      const log = path.join(scratch, bundle, "serve.log");
      const out = openSync(log, "w");
      const child = spawn(EXECUTABLE, ["serve", "--config", config], { cwd: SERVER, stdio: ["ignore", out, out] });
      closeSync(out);
      children.push(child);
      const origin = `http://127.0.0.1:${String(port)}`;
      await ready(origin, child, log);
      bundles[bundle] = { origin, dir: BUNDLES[bundle], port };
    }
    const otherPort = await freePort();
    await new Promise<void>((resolve) => other.listen(otherPort, "127.0.0.1", () => { resolve(); }));
    const found: Servers = { bundles, otherPort };
    process.env[SERVERS_VARIABLE] = JSON.stringify(found);
  } catch (error) {
    // The error names a serve.log under the scratch directory (the log holds the token's hash,
    // never the token): a failed start keeps it.
    await teardown(true);
    throw error;
  }
  return teardown;
}
