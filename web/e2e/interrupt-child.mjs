// @ts-check
/**
 * A process that holds what a run holds (two directories and a slow server that ignores SIGTERM for
 * a while) behind `guardInterrupts`, for `selftest.spec.ts` to signal. It prints its directories and
 * its server's process id as one JSON line, then waits (or, given `exit`, exits at once, forced).
 */
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { guardInterrupts } from "./interrupt.mjs";

const scratch = mkdtempSync(path.join(os.tmpdir(), "aibi-interrupt-scratch-"));
const secrets = mkdtempSync(path.join(os.tmpdir(), "aibi-interrupt-secrets-"));
writeFileSync(path.join(secrets, "token"), "not-a-token", { mode: 0o600 });
const server = spawn(process.execPath, ["-e", "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);"], { stdio: "ignore" });
const alive = () => [server].filter((child) => child.exitCode === null && child.signalCode === null);

guardInterrupts({
  secrets,
  scratch,
  running: alive,
  keepScratch: () => false,
  async teardown() {
    process.stdout.write("teardown\n");
    for (const child of alive()) {
      const exited = new Promise((resolve) => child.once("exit", resolve));
      child.kill("SIGTERM");
      const late = setTimeout(() => child.kill("SIGKILL"), 1500);
      await exited;
      clearTimeout(late);
    }
  },
});

process.stdout.write(`${JSON.stringify({ scratch, secrets, server: server.pid })}\n`);
if (process.argv[2] === "exit") {
  setTimeout(() => {
    process.exit(3);
  }, 300);
} else {
  setInterval(() => undefined, 1000);
}
