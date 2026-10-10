// @ts-check
/**
 * A process that holds what a run holds (two directories and a slow server that ignores SIGTERM for
 * a while) behind the guard, for `selftest.spec.ts` to signal. It prints its directories and its
 * server's process id as one JSON line, then waits. The mode (argument 2) says what it is:
 *
 * - `wait`: `guardInterrupts` alone, a teardown that stops the server (slowly).
 * - `exit`: the same, and a forced exit at once; `exit-keep`: the same, the scratch directory kept.
 * - `stuck`: a teardown that does nothing (the servers stay alive): the interrupt must not spin.
 * - `scanned`: the guard held for the scan reporter, as after a run's teardown (`releaseWhenScanned`).
 * - `run`, `run-scanned`: the guard as `global-setup.ts` holds it (`guardRun`), the servers stopped by
 *   `finish()` first for `run-scanned` (the window between the global teardown and the scan).
 * - `run-exit-keep`: `guardRun` with the scratch directory kept on request (`AIBI_E2E_KEEP`), then a forced exit.
 * - `finish`, `finish-scanned`: `finish()` at once, then the listeners the process has left, printed.
 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { guardInterrupts, runPendingReleases } from "./interrupt.mjs";
import { guardRun } from "./run-guard.mjs";

const mode = process.argv[2] ?? "wait";
const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-interrupt-scratch-"));
const secrets = mkdtempSync(path.join(os.tmpdir(), "aibi-interrupt-secrets-"));
writeFileSync(path.join(secrets, "token"), "not-a-token", { mode: 0o600 });
const server = spawn(process.execPath, ["-e", "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);"], { stdio: "ignore" });
const children = [server];
const alive = () => children.filter((child) => child.exitCode === null && child.signalCode === null);

/** Stop the servers, slowly (SIGTERM, then SIGKILL after a while). */
async function stopServers() {
  process.stdout.write("teardown\n");
  for (const child of alive()) {
    const exited = new Promise((resolve) => child.once("exit", resolve));
    child.kill("SIGTERM");
    const late = setTimeout(() => child.kill("SIGKILL"), 1500);
    await exited;
    clearTimeout(late);
  }
}

const scanning = mode.endsWith("scanned");
/** A run's own teardown, as `global-setup.ts`'s: the servers, the scratch directory unless kept, the
 * token's directory unless the scan reporter reads it.
 * @param {boolean} [keep] */
async function runTeardown(keep = false) {
  await stopServers();
  if (!keep) {
    rmSync(scratch, { recursive: true, force: true });
  }
  if (!scanning) {
    rmSync(secrets, { recursive: true, force: true });
  }
}

const listeners = () => ["exit", "SIGTERM", "SIGHUP"].map((name) => process.listenerCount(name));
const before = listeners();

if (mode.startsWith("run") || mode.startsWith("finish")) {
  const run = guardRun({ scratch, secrets, children, teardown: runTeardown, scanning: () => scanning, keepAsked: () => mode === "run-exit-keep" });
  process.stdout.write(`${JSON.stringify({ scratch, secrets, server: server.pid })}\n`);
  if (mode.startsWith("finish")) {
    await run.finish();
    const held = listeners().map((count, index) => count - (before[index] ?? 0));
    runPendingReleases();
    const after = listeners().map((count, index) => count - (before[index] ?? 0));
    process.stdout.write(`${JSON.stringify({ held, after })}\n`);
    process.exit(0);
  } else if (mode === "run-scanned") {
    await run.finish();
    process.stdout.write("finished\n");
  } else if (mode === "run-exit-keep") {
    setTimeout(() => {
      process.exit(3);
    }, 300);
  }
  setInterval(() => undefined, 1000);
} else {
  const guard = guardInterrupts({
    secrets,
    scratch,
    running: alive,
    keepScratch: () => mode === "exit-keep",
    teardown: mode === "stuck" ? () => Promise.resolve() : stopServers,
  });
  if (mode === "scanned") {
    guard.releaseWhenScanned(true);
  }
  process.stdout.write(`${JSON.stringify({ scratch, secrets, server: server.pid })}\n`);
  if (mode.startsWith("exit")) {
    setTimeout(() => {
      process.exit(3);
    }, 300);
  } else {
    setInterval(() => undefined, 1000);
  }
}
